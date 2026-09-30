"""Resilient MCP execution layer providing bounded retry and per-server circuit breaker."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import Enum
import logging
import threading
import time
from typing import Any, Callable

from harness.config import MCPResilienceConfig
from harness.runtime.events import (
    LifecycleEventBus,
    MCPCircuitStateChangedEvent,
    MCPRetryEvent,
)
from harness.tools.base import ErrorCode, ToolResult

logger = logging.getLogger("harness.mcp.resilience")


class CircuitState(str, Enum):
    """Categorical circuit breaker states."""

    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


@dataclass(frozen=True)
class CircuitSnapshot:
    """Immutable point-in-time snapshot of an MCP server's circuit breaker state."""

    server_name: str
    state: CircuitState
    consecutive_failures: int
    failure_threshold: int
    cooldown_seconds: float
    last_failure_timestamp: float
    probe_in_flight: bool


class MCPCircuitOpenError(RuntimeError):
    """Raised when an action is rejected because the target MCP server circuit is open or probing."""


class CircuitBreaker:
    """Thread-safe, per-server Finite State Machine circuit breaker.

    Transitions:
        CLOSED -> OPEN: upon reaching consecutive failure threshold of logical operations.
        OPEN -> HALF_OPEN: upon arrival of request after cooldown duration has elapsed.
        HALF_OPEN -> CLOSED: upon successful completion of the single probe request.
        HALF_OPEN -> OPEN: upon failure of the single probe request.
    """

    def __init__(
        self,
        server_name: str,
        config: MCPResilienceConfig | None = None,
        clock_fn: Callable[[], float] = time.monotonic,
        event_bus: LifecycleEventBus | None = None,
    ) -> None:
        self.server_name = server_name
        self.config = config or MCPResilienceConfig()
        self.clock_fn = clock_fn
        self.event_bus = event_bus

        self._lock = threading.Lock()
        self._state = CircuitState.CLOSED
        self._consecutive_failures = 0
        self._last_failure_timestamp = 0.0
        self._probe_in_flight = False

    @property
    def state(self) -> CircuitState:
        with self._lock:
            return self._state

    @property
    def consecutive_failures(self) -> int:
        with self._lock:
            return self._consecutive_failures

    @property
    def last_failure_timestamp(self) -> float:
        with self._lock:
            return self._last_failure_timestamp

    @property
    def probe_in_flight(self) -> bool:
        with self._lock:
            return self._probe_in_flight

    def snapshot(self) -> CircuitSnapshot:
        """Return an immutable read-only snapshot of the circuit state under lock."""
        with self._lock:
            return CircuitSnapshot(
                server_name=self.server_name,
                state=self._state,
                consecutive_failures=self._consecutive_failures,
                failure_threshold=self.config.circuit_failure_threshold,
                cooldown_seconds=self.config.circuit_cooldown_seconds,
                last_failure_timestamp=self._last_failure_timestamp,
                probe_in_flight=self._probe_in_flight,
            )

    def before_call(self) -> None:
        """Evaluate circuit state before issuing a tool invocation.

        Raises:
            MCPCircuitOpenError: If the circuit is OPEN or if a probe is already executing in HALF_OPEN.
        """
        with self._lock:
            if self._state == CircuitState.CLOSED:
                return

            if self._state == CircuitState.OPEN:
                now = self.clock_fn()
                elapsed = now - self._last_failure_timestamp
                if elapsed >= self.config.circuit_cooldown_seconds:
                    # Atomic transition: OPEN -> HALF_OPEN with probe reserved under lock
                    self._state = CircuitState.HALF_OPEN
                    self._probe_in_flight = True
                    self._emit_state_change("open", "half_open")
                    return
                else:
                    remaining = self.config.circuit_cooldown_seconds - elapsed
                    raise MCPCircuitOpenError(
                        f"Circuit breaker for server '{self.server_name}' is OPEN. "
                        f"Cooldown active ({remaining:.1f}s remaining)."
                    )

            if self._state == CircuitState.HALF_OPEN:
                if self._probe_in_flight:
                    raise MCPCircuitOpenError(
                        f"Circuit breaker for server '{self.server_name}' is HALF_OPEN "
                        f"and probe request is already in flight."
                    )
                self._probe_in_flight = True
                return

    def record_success(self) -> None:
        """Record successful logical operation, resetting failure counter and closing circuit."""
        with self._lock:
            prev = self._state
            self._consecutive_failures = 0
            self._probe_in_flight = False
            if prev != CircuitState.CLOSED:
                self._state = CircuitState.CLOSED
                self._emit_state_change(prev.value, "closed")

    def record_failure(self, is_transient: bool = True) -> None:
        """Record logical operation failure against circuit threshold."""
        with self._lock:
            if not is_transient:
                # Non-transient client/argument errors do not degrade server health
                self._probe_in_flight = False
                return

            prev = self._state
            self._consecutive_failures += 1
            self._last_failure_timestamp = self.clock_fn()
            self._probe_in_flight = False

            if prev == CircuitState.HALF_OPEN:
                # Probe failed; immediately reopen circuit
                self._state = CircuitState.OPEN
                self._emit_state_change("half_open", "open")
            elif prev == CircuitState.CLOSED:
                if self._consecutive_failures >= self.config.circuit_failure_threshold:
                    self._state = CircuitState.OPEN
                    self._emit_state_change("closed", "open")

    def _emit_state_change(self, from_state: str, to_state: str) -> None:
        if not self.event_bus:
            return
        try:
            from harness.runtime.context import get_current_context
            ctx = get_current_context()
        except Exception:
            ctx = None

        event = MCPCircuitStateChangedEvent(
            timestamp=time.time(),
            trace_id=ctx.trace_id if ctx else "",
            run_id=ctx.run_id if ctx else "",
            root_run_id=ctx.root_run_id if ctx else "",
            parent_run_id=ctx.parent_run_id if ctx else None,
            agent_id=ctx.agent_id if ctx else "",
            agent_role=ctx.agent_role if ctx else "",
            server_name=self.server_name,
            from_state=from_state,
            to_state=to_state,
            consecutive_failures=self._consecutive_failures,
        )
        self.event_bus.publish(event)


class ResilientMCPInvoker:
    """Executes MCP tool calls with idempotency-aware retry and circuit breaker governance."""

    def __init__(
        self,
        circuit_breaker: CircuitBreaker,
        config: MCPResilienceConfig | None = None,
        clock_fn: Callable[[], float] = time.monotonic,
        sleep_fn: Callable[[float], None] = time.sleep,
        event_bus: LifecycleEventBus | None = None,
    ) -> None:
        self.circuit_breaker = circuit_breaker
        self.config = config or circuit_breaker.config
        self.clock_fn = clock_fn
        self.sleep_fn = sleep_fn
        self.event_bus = event_bus or circuit_breaker.event_bus

    def invoke(
        self,
        tool_name: str,
        arguments: Mapping[str, Any],
        call_fn: Callable[[], ToolResult],
        is_mutating: bool = False,
        is_idempotent: bool | None = None,
    ) -> ToolResult:
        """Invoke MCP tool within circuit breaker guard and idempotency-aware retry loop.

        Invariant:
            Mutating / non-idempotent tool calls that fail in-flight (ambiguous remote execution)
            are NEVER retried. Only operations known to be safe/idempotent or failures proven to
            have occurred before request transmission (pre-execution) are retried.
        """
        try:
            self.circuit_breaker.before_call()
        except MCPCircuitOpenError as e:
            return ToolResult(
                content=f"MCP circuit open: {e}",
                is_error=True,
                error_code=ErrorCode.TRANSIENT_ERROR,
            )

        idempotent = is_idempotent if is_idempotent is not None else (
            not is_mutating or tool_name in self.config.idempotent_tools
        )

        last_result: ToolResult | None = None
        for attempt in range(self.config.max_retries + 1):
            res = call_fn()
            if not res.is_error:
                self.circuit_breaker.record_success()
                return res

            last_result = res
            is_transient = (res.error_code == ErrorCode.TRANSIENT_ERROR)
            is_pre_execution = getattr(res, "_is_pre_execution", False)

            # Strict idempotency check: only retry if transient AND (safe/idempotent OR pre-execution)
            can_retry = is_transient and (idempotent or is_pre_execution)

            if can_retry and attempt < self.config.max_retries:
                delay = min(
                    self.config.initial_backoff_seconds * (self.config.backoff_multiplier ** attempt),
                    self.config.max_backoff_seconds,
                )
                if self.event_bus:
                    try:
                        from harness.runtime.context import get_current_context
                        ctx = get_current_context()
                    except Exception:
                        ctx = None

                    sanitized_msg = res.content[:200]
                    self.event_bus.publish(
                        MCPRetryEvent(
                            timestamp=time.time(),
                            trace_id=ctx.trace_id if ctx else "",
                            run_id=ctx.run_id if ctx else "",
                            root_run_id=ctx.root_run_id if ctx else "",
                            parent_run_id=ctx.parent_run_id if ctx else None,
                            agent_id=ctx.agent_id if ctx else "",
                            agent_role=ctx.agent_role if ctx else "",
                            server_name=self.circuit_breaker.server_name,
                            tool_name=tool_name,
                            attempt=attempt + 1,
                            max_retries=self.config.max_retries,
                            delay_seconds=delay,
                            error_message=sanitized_msg,
                        )
                    )
                self.sleep_fn(delay)
                continue
            else:
                # Retries exhausted or non-retryable error:
                # Record logical operation failure against the circuit breaker
                self.circuit_breaker.record_failure(is_transient=is_transient)
                return res

        # Fallback (safety guarantee)
        assert last_result is not None
        return last_result
