"""Unit tests for resilient MCP execution with bounded retry and circuit breaker (Feature B)."""

from __future__ import annotations

import time
from typing import Any
import unittest
from unittest.mock import MagicMock

from harness.config import MCPServerConfig
from harness.mcp.client import MCPClient
from harness.mcp.resilience import (
    CircuitBreaker,
    CircuitState,
    MCPCircuitOpenError,
    MCPResilienceConfig,
    ResilientMCPInvoker,
)
from harness.observability.metrics import PrometheusObserver
from harness.runtime.events import (
    LifecycleEventBus,
    MCPCircuitStateChangedEvent,
    MCPRetryEvent,
)
from harness.tools.base import ErrorCode, ToolResult


class FakeClock:
    """Controllable monotonic clock for deterministic time testing."""

    def __init__(self, initial_time: float = 100.0) -> None:
        self._current_time = initial_time

    def now(self) -> float:
        return self._current_time

    def advance(self, seconds: float) -> None:
        self._current_time += seconds


class CollectingObserver:
    """Collects lifecycle events for test assertions."""

    def __init__(self, target_list: list[Any]) -> None:
        self.target_list = target_list

    def on_event(self, event: Any) -> None:
        self.target_list.append(event)


class TestMCPResilience(unittest.TestCase):
    """Verify circuit breaker FSM, idempotency-aware retries, and metric emissions."""

    def setUp(self) -> None:
        self.clock = FakeClock(100.0)
        self.sleep_calls: list[float] = []
        self.fake_sleep = lambda s: self.sleep_calls.append(s)
        self.event_bus = LifecycleEventBus()
        self.published_events: list[Any] = []
        self.event_bus.subscribe(CollectingObserver(self.published_events))

        self.resilience_config = MCPResilienceConfig(
            max_retries=2,
            initial_backoff_seconds=0.5,
            max_backoff_seconds=2.0,
            backoff_multiplier=2.0,
            circuit_failure_threshold=3,
            circuit_cooldown_seconds=30.0,
            idempotent_tools=frozenset({"query_metrics", "status_check"}),
        )

        self.circuit_breaker = CircuitBreaker(
            server_name="test_server",
            config=self.resilience_config,
            clock_fn=self.clock.now,
            event_bus=self.event_bus,
        )

        self.invoker = ResilientMCPInvoker(
            circuit_breaker=self.circuit_breaker,
            config=self.resilience_config,
            clock_fn=self.clock.now,
            sleep_fn=self.fake_sleep,
            event_bus=self.event_bus,
        )

    # -------------------------------------------------------------------------
    # 1. Circuit Breaker Finite State Machine Tests
    # -------------------------------------------------------------------------

    def test_circuit_breaker_initial_state_closed(self) -> None:
        """Initial state must be CLOSED with 0 consecutive failures and no probe."""
        self.assertEqual(self.circuit_breaker.state, CircuitState.CLOSED)
        self.assertEqual(self.circuit_breaker.consecutive_failures, 0)
        self.assertFalse(self.circuit_breaker.probe_in_flight)
        # before_call should succeed without error
        self.circuit_breaker.before_call()

    def test_circuit_breaker_transient_failures_trip_to_open(self) -> None:
        """Consecutive transient failures up to threshold trip circuit from CLOSED to OPEN."""
        # Threshold is 3
        self.circuit_breaker.record_failure(is_transient=True)
        self.assertEqual(self.circuit_breaker.state, CircuitState.CLOSED)
        self.assertEqual(self.circuit_breaker.consecutive_failures, 1)

        self.circuit_breaker.record_failure(is_transient=True)
        self.assertEqual(self.circuit_breaker.state, CircuitState.CLOSED)
        self.assertEqual(self.circuit_breaker.consecutive_failures, 2)

        # 3rd failure trips the circuit
        self.circuit_breaker.record_failure(is_transient=True)
        self.assertEqual(self.circuit_breaker.state, CircuitState.OPEN)
        self.assertEqual(self.circuit_breaker.consecutive_failures, 3)
        self.assertEqual(self.circuit_breaker.last_failure_timestamp, 100.0)

        # Event emitted
        state_events = [e for e in self.published_events if isinstance(e, MCPCircuitStateChangedEvent)]
        self.assertEqual(len(state_events), 1)
        self.assertEqual(state_events[0].from_state, "closed")
        self.assertEqual(state_events[0].to_state, "open")
        self.assertEqual(state_events[0].server_name, "test_server")

        # In OPEN state, before_call raises MCPCircuitOpenError
        with self.assertRaises(MCPCircuitOpenError) as ctx:
            self.circuit_breaker.before_call()
        self.assertIn("OPEN", str(ctx.exception))
        self.assertIn("Cooldown active", str(ctx.exception))

    def test_circuit_breaker_non_transient_failure_does_not_trip(self) -> None:
        """Non-transient errors (e.g. invalid arguments) must NOT degrade circuit health."""
        for _ in range(5):
            self.circuit_breaker.record_failure(is_transient=False)

        self.assertEqual(self.circuit_breaker.state, CircuitState.CLOSED)
        self.assertEqual(self.circuit_breaker.consecutive_failures, 0)

    def test_circuit_breaker_cooldown_and_half_open_transition(self) -> None:
        """After cooldown elapses, next call transitions OPEN -> HALF_OPEN with probe reserved."""
        # Trip to OPEN at t=100
        for _ in range(3):
            self.circuit_breaker.record_failure(is_transient=True)
        self.assertEqual(self.circuit_breaker.state, CircuitState.OPEN)

        # At t=115 (cooldown is 30s), still OPEN
        self.clock.advance(15.0)
        with self.assertRaises(MCPCircuitOpenError):
            self.circuit_breaker.before_call()
        self.assertEqual(self.circuit_breaker.state, CircuitState.OPEN)

        # At t=130 (exactly 30s elapsed), before_call transitions to HALF_OPEN
        self.clock.advance(15.0)
        self.circuit_breaker.before_call()

        self.assertEqual(self.circuit_breaker.state, CircuitState.HALF_OPEN)
        self.assertTrue(self.circuit_breaker.probe_in_flight)

        state_events = [e for e in self.published_events if isinstance(e, MCPCircuitStateChangedEvent)]
        self.assertEqual(len(state_events), 2)
        self.assertEqual(state_events[1].from_state, "open")
        self.assertEqual(state_events[1].to_state, "half_open")

    def test_circuit_breaker_half_open_single_probe_isolation(self) -> None:
        """In HALF_OPEN, only one probe is admitted; concurrent calls are rejected immediately."""
        # Trip and advance past cooldown
        for _ in range(3):
            self.circuit_breaker.record_failure(is_transient=True)
        self.clock.advance(31.0)

        # First probe admitted
        self.circuit_breaker.before_call()
        self.assertEqual(self.circuit_breaker.state, CircuitState.HALF_OPEN)
        self.assertTrue(self.circuit_breaker.probe_in_flight)

        # Second probe attempt while first is in flight is rejected
        with self.assertRaises(MCPCircuitOpenError) as ctx:
            self.circuit_breaker.before_call()
        self.assertIn("probe request is already in flight", str(ctx.exception))

    def test_circuit_breaker_half_open_success_resets_to_closed(self) -> None:
        """Successful probe in HALF_OPEN resets circuit to CLOSED and clears failure counter."""
        for _ in range(3):
            self.circuit_breaker.record_failure(is_transient=True)
        self.clock.advance(31.0)
        self.circuit_breaker.before_call()

        # Probe succeeds
        self.circuit_breaker.record_success()
        self.assertEqual(self.circuit_breaker.state, CircuitState.CLOSED)
        self.assertEqual(self.circuit_breaker.consecutive_failures, 0)
        self.assertFalse(self.circuit_breaker.probe_in_flight)

        state_events = [e for e in self.published_events if isinstance(e, MCPCircuitStateChangedEvent)]
        self.assertEqual(state_events[-1].from_state, "half_open")
        self.assertEqual(state_events[-1].to_state, "closed")

        # Subsequent calls work normally
        self.circuit_breaker.before_call()

    def test_circuit_breaker_half_open_failure_reopens_circuit(self) -> None:
        """Failed probe in HALF_OPEN immediately re-opens circuit with fresh cooldown."""
        for _ in range(3):
            self.circuit_breaker.record_failure(is_transient=True)
        self.clock.advance(31.0)
        self.circuit_breaker.before_call()

        # Probe fails at t=131
        self.circuit_breaker.record_failure(is_transient=True)
        self.assertEqual(self.circuit_breaker.state, CircuitState.OPEN)
        self.assertFalse(self.circuit_breaker.probe_in_flight)
        self.assertEqual(self.circuit_breaker.last_failure_timestamp, 131.0)

        state_events = [e for e in self.published_events if isinstance(e, MCPCircuitStateChangedEvent)]
        self.assertEqual(state_events[-1].from_state, "half_open")
        self.assertEqual(state_events[-1].to_state, "open")

        # Immediate call rejected with full cooldown
        with self.assertRaises(MCPCircuitOpenError):
            self.circuit_breaker.before_call()

    # -------------------------------------------------------------------------
    # 2. Resilient MCP Invoker and Retry Invariants
    # -------------------------------------------------------------------------

    def test_invoker_success_first_attempt(self) -> None:
        """Normal tool invocation succeeds immediately without retry or delay."""
        call_count = 0

        def call_fn() -> ToolResult:
            nonlocal call_count
            call_count += 1
            return ToolResult(content="OK", is_error=False)

        res = self.invoker.invoke(
            tool_name="echo",
            arguments={"msg": "hello"},
            call_fn=call_fn,
            is_mutating=False,
        )

        self.assertFalse(res.is_error)
        self.assertEqual(res.content, "OK")
        self.assertEqual(call_count, 1)
        self.assertEqual(len(self.sleep_calls), 0)
        self.assertEqual(self.circuit_breaker.consecutive_failures, 0)

    def test_invoker_idempotent_transient_retry_and_success(self) -> None:
        """Transient failure on idempotent tool is retried with backoff and succeeds."""
        call_count = 0

        def call_fn() -> ToolResult:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return ToolResult(
                    content="Connection reset by peer",
                    is_error=True,
                    error_code=ErrorCode.TRANSIENT_ERROR,
                )
            return ToolResult(content="Success on retry", is_error=False)

        res = self.invoker.invoke(
            tool_name="read_data",
            arguments={},
            call_fn=call_fn,
            is_mutating=False,
        )

        self.assertFalse(res.is_error)
        self.assertEqual(res.content, "Success on retry")
        self.assertEqual(call_count, 2)
        self.assertEqual(self.sleep_calls, [0.5])  # initial_backoff_seconds

        # Verify retry event published
        retry_events = [e for e in self.published_events if isinstance(e, MCPRetryEvent)]
        self.assertEqual(len(retry_events), 1)
        self.assertEqual(retry_events[0].attempt, 1)
        self.assertEqual(retry_events[0].max_retries, 2)
        self.assertEqual(retry_events[0].delay_seconds, 0.5)
        self.assertEqual(retry_events[0].server_name, "test_server")
        self.assertEqual(retry_events[0].tool_name, "read_data")

        # Circuit breaker recorded success
        self.assertEqual(self.circuit_breaker.consecutive_failures, 0)

    def test_invoker_idempotent_retry_exhaustion_records_one_logical_failure(self) -> None:
        """Exhausting retries results in exactly 1 logical failure on the circuit breaker."""
        call_count = 0

        def call_fn() -> ToolResult:
            nonlocal call_count
            call_count += 1
            return ToolResult(
                content="Timeout contacting upstream",
                is_error=True,
                error_code=ErrorCode.TRANSIENT_ERROR,
            )

        res = self.invoker.invoke(
            tool_name="read_data",
            arguments={},
            call_fn=call_fn,
            is_mutating=False,
        )

        self.assertTrue(res.is_error)
        # Attempt 0, attempt 1, attempt 2 (max_retries=2) -> 3 calls
        self.assertEqual(call_count, 3)
        self.assertEqual(self.sleep_calls, [0.5, 1.0])  # exponential backoff (0.5 * 2^0, 0.5 * 2^1)

        # Invariant: exactly 1 logical failure recorded against circuit breaker, not 3
        self.assertEqual(self.circuit_breaker.consecutive_failures, 1)

    def test_invoker_non_idempotent_inflight_timeout_never_retried(self) -> None:
        """Mutating/non-idempotent tool timeout in-flight MUST NOT be retried (ambiguous execution)."""
        call_count = 0

        def call_fn() -> ToolResult:
            nonlocal call_count
            call_count += 1
            return ToolResult(
                content="Stream timed out waiting for server response",
                is_error=True,
                error_code=ErrorCode.TRANSIENT_ERROR,
            )

        res = self.invoker.invoke(
            tool_name="transfer_funds",
            arguments={"amount": 100},
            call_fn=call_fn,
            is_mutating=True,
            is_idempotent=False,
        )

        self.assertTrue(res.is_error)
        # Invariant: called exactly ONCE; no retry attempted
        self.assertEqual(call_count, 1)
        self.assertEqual(len(self.sleep_calls), 0)
        # Logical failure still counted against circuit breaker
        self.assertEqual(self.circuit_breaker.consecutive_failures, 1)

        # No retry events emitted
        retry_events = [e for e in self.published_events if isinstance(e, MCPRetryEvent)]
        self.assertEqual(len(retry_events), 0)

    def test_invoker_non_idempotent_pre_execution_failure_safely_retried(self) -> None:
        """Mutating tool CAN be retried if error is proven pre-execution (connection not established)."""
        call_count = 0

        def call_fn() -> ToolResult:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                res = ToolResult(
                    content="ConnectError: [Errno 111] Connection refused",
                    is_error=True,
                    error_code=ErrorCode.TRANSIENT_ERROR,
                )
                object.__setattr__(res, "_is_pre_execution", True)
                return res
            return ToolResult(content="Fund transfer executed", is_error=False)

        res = self.invoker.invoke(
            tool_name="transfer_funds",
            arguments={"amount": 100},
            call_fn=call_fn,
            is_mutating=True,
            is_idempotent=False,
        )

        self.assertFalse(res.is_error)
        self.assertEqual(res.content, "Fund transfer executed")
        self.assertEqual(call_count, 2)
        self.assertEqual(len(self.sleep_calls), 1)

    def test_invoker_non_transient_error_no_retry_no_circuit_increment(self) -> None:
        """Client/validation errors (e.g. INVALID_ARGUMENT) are not retried and do not affect circuit."""
        call_count = 0

        def call_fn() -> ToolResult:
            nonlocal call_count
            call_count += 1
            return ToolResult(
                content="Missing required parameter 'query'",
                is_error=True,
                error_code=ErrorCode.INVALID_ARGUMENT,
            )

        res = self.invoker.invoke(
            tool_name="read_data",
            arguments={},
            call_fn=call_fn,
            is_mutating=False,
        )

        self.assertTrue(res.is_error)
        self.assertEqual(call_count, 1)
        self.assertEqual(len(self.sleep_calls), 0)
        self.assertEqual(self.circuit_breaker.consecutive_failures, 0)

    def test_invoker_fast_failure_when_circuit_open(self) -> None:
        """When circuit is OPEN, calls fail fast immediately with TRANSIENT_ERROR without calling backend."""
        # Trip the circuit
        for _ in range(3):
            self.circuit_breaker.record_failure(is_transient=True)
        self.assertEqual(self.circuit_breaker.state, CircuitState.OPEN)

        called = False

        def call_fn() -> ToolResult:
            nonlocal called
            called = True
            return ToolResult(content="Should not be called", is_error=False)

        res = self.invoker.invoke(
            tool_name="read_data",
            arguments={},
            call_fn=call_fn,
            is_mutating=False,
        )

        self.assertTrue(res.is_error)
        self.assertEqual(res.error_code, ErrorCode.TRANSIENT_ERROR)
        self.assertIn("MCP circuit open", res.content)
        self.assertFalse(called)

    # -------------------------------------------------------------------------
    # 3. Server Isolation and Metrics Verification
    # -------------------------------------------------------------------------

    def test_per_server_circuit_isolation(self) -> None:
        """Failure of server A must not trip the circuit of server B."""
        breaker_b = CircuitBreaker(
            server_name="server_b",
            config=self.resilience_config,
            clock_fn=self.clock.now,
        )

        # Trip server A
        for _ in range(3):
            self.circuit_breaker.record_failure(is_transient=True)

        self.assertEqual(self.circuit_breaker.state, CircuitState.OPEN)
        self.assertEqual(breaker_b.state, CircuitState.CLOSED)

        # Server B before_call succeeds
        breaker_b.before_call()

    def test_metrics_collector_routes_mcp_resilience_events(self) -> None:
        """PrometheusObserver must update categorical gauge and counters upon receiving resilience events."""
        collector = PrometheusObserver()

        # Emit retry event
        retry_event = MCPRetryEvent(
            timestamp=time.time(),
            trace_id="t1",
            run_id="r1",
            root_run_id="r1",
            parent_run_id=None,
            agent_id="a1",
            agent_role="orchestrator",
            server_name="sqlite_mcp",
            tool_name="query",
            attempt=1,
            max_retries=2,
            delay_seconds=0.5,
            error_message="Connection reset",
        )
        collector.on_event(retry_event)

        # Verify retry counter
        retry_val = collector.mcp_retries_total.labels(
            server_name="sqlite_mcp",
            tool_name="query",
        )._value.get()
        self.assertEqual(retry_val, 1.0)

        # Emit transition: closed -> open
        state_event = MCPCircuitStateChangedEvent(
            timestamp=time.time(),
            trace_id="t1",
            run_id="r1",
            root_run_id="r1",
            parent_run_id=None,
            agent_id="a1",
            agent_role="orchestrator",
            server_name="sqlite_mcp",
            from_state="closed",
            to_state="open",
            consecutive_failures=3,
        )
        collector.on_event(state_event)

        # Verify one-hot gauge: open=1.0, closed=0.0, half_open=0.0
        open_val = collector.mcp_circuit_state.labels(server_name="sqlite_mcp", state="open")._value.get()
        closed_val = collector.mcp_circuit_state.labels(server_name="sqlite_mcp", state="closed")._value.get()
        half_open_val = collector.mcp_circuit_state.labels(server_name="sqlite_mcp", state="half_open")._value.get()

        self.assertEqual(open_val, 1.0)
        self.assertEqual(closed_val, 0.0)
        self.assertEqual(half_open_val, 0.0)

        # Verify transition counter
        trans_val = collector.mcp_circuit_transitions_total.labels(
            server_name="sqlite_mcp",
            from_state="closed",
            to_state="open",
        )._value.get()
        self.assertEqual(trans_val, 1.0)

    def test_mcp_client_resilience_integration(self) -> None:
        """MCPClient exposes circuit_breaker and invoker, routing tool calls through resilience layer."""
        cfg = MCPServerConfig(
            name="mock_server",
            command="python",
            args=["-m", "nonexistent"],
        )
        transport = MagicMock()
        client = MCPClient(
            config=cfg,
            transport=transport,
            event_bus=self.event_bus,
            resilience_config=self.resilience_config,
        )

        self.assertEqual(client.circuit_breaker.server_name, "mock_server")
        self.assertEqual(client.circuit_breaker.state, CircuitState.CLOSED)

        # Mock low-level call tool
        raw_mock = MagicMock(return_value=ToolResult(content="OK", is_error=False))
        client._raw_call_tool = raw_mock  # type: ignore[assignment]

        res = client.call_tool("test_tool", {"arg": 1}, is_mutating=False)
        self.assertFalse(res.is_error)
        self.assertEqual(res.content, "OK")
        raw_mock.assert_called_once_with("test_tool", {"arg": 1})

    def test_real_adapter_client_resilience_propagation_and_idempotency_defaults(self) -> None:
        """Verify real adapter/client invocation path conservatively protects unknown tools from retry."""
        from harness.mcp.adapter import MCPToolAdapter
        from harness.tools.base import ToolSpec

        cfg = MCPServerConfig(
            name="mock_server",
            command="python",
            args=["-m", "nonexistent"],
            resilience=self.resilience_config,
        )
        client = MCPClient(config=cfg, transport=MagicMock(), event_bus=self.event_bus)

        # 1. Read-only / discovered query tool -> classified as non-mutating (is_mutating=False)
        read_spec = ToolSpec(
            name="read_report",
            description="Read status report",
            input_schema={"type": "object"},
            is_mutating=client._classify_tool_mutating("read_report"),
        )
        self.assertFalse(read_spec.is_mutating)
        read_adapter = MCPToolAdapter(read_spec, client)

        read_attempts = 0

        def raw_call_read(name: str, args: Any) -> ToolResult:
            nonlocal read_attempts
            read_attempts += 1
            if read_attempts == 1:
                return ToolResult(
                    content="Temporary read timeout",
                    is_error=True,
                    error_code=ErrorCode.TRANSIENT_ERROR,
                )
            return ToolResult(content="Report data", is_error=False)

        client._raw_call_tool = raw_call_read  # type: ignore[assignment]
        read_res = read_adapter.execute({})
        self.assertFalse(read_res.is_error)
        self.assertEqual(read_res.content, "Report data")
        self.assertEqual(read_attempts, 2)  # Safely retried!

        # 2. Unknown / mutating tool -> classified conservatively as mutating (is_mutating=True)
        unknown_spec = ToolSpec(
            name="custom_transfer_funds",
            description="Transfer funds remotely",
            input_schema={"type": "object"},
            is_mutating=client._classify_tool_mutating("custom_transfer_funds"),
        )
        self.assertTrue(unknown_spec.is_mutating)
        unknown_adapter = MCPToolAdapter(unknown_spec, client)

        unknown_attempts = 0

        def raw_call_unknown(name: str, args: Any) -> ToolResult:
            nonlocal unknown_attempts
            unknown_attempts += 1
            return ToolResult(
                content="Timeout after request bytes sent",
                is_error=True,
                error_code=ErrorCode.TRANSIENT_ERROR,
            )

        client._raw_call_tool = raw_call_unknown  # type: ignore[assignment]
        unknown_res = unknown_adapter.execute({"amount": 1000})

        # Conservative Invariant: Unknown idempotency MUST NOT be retried after ambiguous in-flight failure!
        self.assertTrue(unknown_res.is_error)
        self.assertEqual(unknown_attempts, 1)  # NOT retried!
        self.assertEqual(client.circuit_breaker.consecutive_failures, 1)

        # 3. Mutating tool with pre-execution failure -> safely retried because bytes never reached wire
        pre_exec_attempts = 0

        def raw_call_pre_exec(name: str, args: Any) -> ToolResult:
            nonlocal pre_exec_attempts
            pre_exec_attempts += 1
            if pre_exec_attempts == 1:
                err_res = ToolResult(
                    content="ConnectError: connection refused before handshake",
                    is_error=True,
                    error_code=ErrorCode.TRANSIENT_ERROR,
                )
                object.__setattr__(err_res, "_is_pre_execution", True)
                return err_res
            return ToolResult(content="Transfer succeeded", is_error=False)

        client._raw_call_tool = raw_call_pre_exec  # type: ignore[assignment]
        pre_exec_res = unknown_adapter.execute({"amount": 1000})
        self.assertFalse(pre_exec_res.is_error)
        self.assertEqual(pre_exec_res.content, "Transfer succeeded")
        self.assertEqual(pre_exec_attempts, 2)  # Safely retried!

    def test_mcp_tool_mutation_classification_and_ambiguous_add_rejection(self) -> None:
        """Verify get_status/find_connection classify as read-only while add_* defaults to mutating."""
        from harness.mcp.adapter import MCPToolAdapter
        from harness.tools.base import ToolSpec

        # Server config with only 'find_connection' explicitly in idempotent_tools
        cfg = MCPServerConfig(
            name="test_classifier",
            command="python",
            args=["-m", "nonexistent"],
            resilience=MCPResilienceConfig(
                max_retries=2,
                idempotent_tools=frozenset({"find_connection"}),
            ),
        )
        client = MCPClient(config=cfg, transport=MagicMock(), event_bus=self.event_bus)

        # 1. Read-only verbs and explicit idempotent tools are non-mutating
        self.assertFalse(client._classify_tool_mutating("get_status"))
        self.assertFalse(client._classify_tool_mutating("find_connection"))
        self.assertFalse(client._classify_tool_mutating("read_file"))
        self.assertFalse(client._classify_tool_mutating("list_items"))
        self.assertFalse(client._classify_tool_mutating("echo"))
        self.assertFalse(client._classify_tool_mutating("ping"))

        # 2. 'add' and 'add_*' are ambiguous/mutating by default
        self.assertTrue(client._classify_tool_mutating("add"))
        self.assertTrue(client._classify_tool_mutating("add_user"))
        self.assertTrue(client._classify_tool_mutating("add_record"))
        self.assertTrue(client._classify_tool_mutating("add_payment"))
        self.assertTrue(client._classify_tool_mutating("delete_item"))
        self.assertTrue(client._classify_tool_mutating("unknown_operation"))

        # 3. Explicit idempotent_tools override
        cfg_override = MCPServerConfig(
            name="test_override",
            command="python",
            args=["-m", "nonexistent"],
            resilience=MCPResilienceConfig(
                max_retries=2,
                idempotent_tools=frozenset({"add_record"}),
            ),
        )
        client_override = MCPClient(config=cfg_override, transport=MagicMock(), event_bus=self.event_bus)
        self.assertFalse(client_override._classify_tool_mutating("add_record"))

        # 4. Ambiguous mutating tool ('add_user') experiencing in-flight timeout is NOT retried
        add_spec = ToolSpec(
            name="add_user",
            description="Create user profile",
            input_schema={"type": "object"},
            is_mutating=client._classify_tool_mutating("add_user"),
        )
        self.assertTrue(add_spec.is_mutating)
        add_adapter = MCPToolAdapter(add_spec, client)

        call_count = 0

        def raw_call_add(name: str, args: Any) -> ToolResult:
            nonlocal call_count
            call_count += 1
            return ToolResult(
                content="Timeout waiting for database commit response",
                is_error=True,
                error_code=ErrorCode.TRANSIENT_ERROR,
            )

        client._raw_call_tool = raw_call_add  # type: ignore[assignment]
        res = add_adapter.execute({"username": "alice"})

        # Conservative Invariant: Must NOT be retried
        self.assertTrue(res.is_error)
        self.assertEqual(call_count, 1)
        self.assertEqual(client.circuit_breaker.consecutive_failures, 1)


if __name__ == "__main__":
    unittest.main()
