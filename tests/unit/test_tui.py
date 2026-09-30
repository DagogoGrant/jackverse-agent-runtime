"""Deterministic unit tests for the TUI Observer, RuntimeStateStore, and Operator Console components."""

from __future__ import annotations

import threading
import time
import unittest

from harness.agent.budget import ExecutionBudget, TerminationReason
from harness.config import (
    AgentConfig,
    AppConfig,
    LLMConfig,
    MCPServerConfig,
    MemoryConfig,
    ObservabilityConfig,
    PermissionsConfig,
    PolicyRuleConfig,
    ToolsConfig,
    load_config,
)
from harness.permissions.base import (
    PermissionDecision,
    PermissionRequest,
    RiskLevel,
    canonical_tool_identity,
    compute_arguments_fingerprint,
    summarize_arguments,
)
from harness.runtime.events import (
    ConfirmationResolvedEvent,
    DelegationFinishedEvent,
    DelegationStartedEvent,
    LifecycleEventBus,
    LLMCallFinishedEvent,
    LLMCallStartedEvent,
    LLMCallStatus,
    MCPCircuitStateChangedEvent,
    MCPRetryEvent,
    MemoryOperationEvent,
    MemoryOperationStatus,
    MemoryOperationType,
    PermissionEvaluatedEvent,
    RunFinishedEvent,
    RunStartedEvent,
    ToolCallFinishedEvent,
    ToolCallRequestedEvent,
    ToolCallStartedEvent,
    ToolCallStatus,
)
from harness.tools.base import Tool, ToolResult, ToolSource, ToolSpec
from harness.tools.registry import ToolRegistry
from harness.tui.confirmation import TUIConfirmationHandler
from harness.tui.health import check_workspace_health, probe_http_endpoint
from harness.tui.models import MCPServerCard, RunNode, TimelineEvent, ToolCallNode
from harness.tui.observer import TUIObserver
from harness.tui.scenarios import SCENARIOS, run_deterministic_resilience_tour
from harness.tui.store import RuntimeStateStore


class DummyTool(Tool):
    def __init__(self, name: str = "test_tool", is_mutating: bool = False, source: ToolSource = ToolSource.BUILTIN) -> None:
        self._spec = ToolSpec(
            name=name,
            description="A test tool",
            input_schema={"type": "object", "properties": {"arg": {"type": "string"}}},
            is_mutating=is_mutating,
            source=source,
            server_name="transport_service" if source == ToolSource.MCP else None,
        )

    @property
    def spec(self) -> ToolSpec:
        return self._spec

    def execute(self, arguments):
        return ToolResult(content="ok", is_error=False)


class TestTUIStateAndObserver(unittest.TestCase):
    """Tests for RuntimeStateStore and TUIObserver decoupled event ingestion."""

    def setUp(self) -> None:
        self.store = RuntimeStateStore(max_events=100)
        self.observer = TUIObserver(self.store)
        self.bus = LifecycleEventBus()
        self.bus.subscribe(self.observer)

    def test_run_lifecycle_and_hierarchy(self) -> None:
        """Verify root and child runs are correctly linked in hierarchy."""
        root_run_id = "root_123"
        child_run_id = "child_456"
        trace_id = "trace_abc"

        # 1. Start root run
        self.bus.publish(
            RunStartedEvent(
                timestamp=100.0,
                trace_id=trace_id,
                run_id=root_run_id,
                root_run_id=root_run_id,
                parent_run_id=None,
                agent_id="agent_root",
                agent_role="orchestrator",
                user_input_length=50,
            )
        )

        self.assertIn(root_run_id, self.store.runs)
        self.assertEqual(self.store.runs[root_run_id].agent_role, "orchestrator")
        self.assertTrue(self.store.runs[root_run_id].is_root)
        self.assertEqual(self.store.active_run_id, root_run_id)
        self.assertEqual(self.store.active_trace_id, trace_id)
        self.assertTrue(self.store.is_agent_executing)

        # 2. Start child run
        self.bus.publish(
            RunStartedEvent(
                timestamp=101.0,
                trace_id=trace_id,
                run_id=child_run_id,
                root_run_id=root_run_id,
                parent_run_id=root_run_id,
                agent_id="agent_child",
                agent_role="transport_specialist",
                user_input_length=20,
            )
        )

        self.assertIn(child_run_id, self.store.runs)
        self.assertFalse(self.store.runs[child_run_id].is_root)
        # Verify parent linked to child
        self.assertIn(child_run_id, self.store.runs[root_run_id].children_run_ids)

        # 3. Finish child run
        self.bus.publish(
            RunFinishedEvent(
                timestamp=103.0,
                trace_id=trace_id,
                run_id=child_run_id,
                root_run_id=root_run_id,
                parent_run_id=root_run_id,
                agent_id="agent_child",
                agent_role="transport_specialist",
                termination_reason=TerminationReason.FINAL_ANSWER,
                steps=2,
                tool_calls=1,
                duration_seconds=2.0,
                is_success=True,
            )
        )
        self.assertEqual(self.store.runs[child_run_id].status, "SUCCESS")
        self.assertEqual(self.store.runs[child_run_id].duration_seconds, 2.0)

        # 4. Finish root run
        self.bus.publish(
            RunFinishedEvent(
                timestamp=105.0,
                trace_id=trace_id,
                run_id=root_run_id,
                root_run_id=root_run_id,
                parent_run_id=None,
                agent_id="agent_root",
                agent_role="orchestrator",
                termination_reason=TerminationReason.FINAL_ANSWER,
                steps=4,
                tool_calls=2,
                duration_seconds=5.0,
                is_success=True,
            )
        )
        self.assertEqual(self.store.runs[root_run_id].status, "SUCCESS")
        self.assertFalse(self.store.is_agent_executing)

    def test_tool_call_lifecycle_and_correlation(self) -> None:
        """Verify tool calls correlate permission, execution, and timing under the run node."""
        run_id = "run_t1"
        trace_id = "trace_t1"
        call_id = "call_xyz"

        self.bus.publish(
            RunStartedEvent(
                timestamp=10.0,
                trace_id=trace_id,
                run_id=run_id,
                root_run_id=run_id,
                parent_run_id=None,
                agent_id="orch",
                agent_role="orchestrator",
            )
        )

        # Tool requested
        self.bus.publish(
            ToolCallRequestedEvent(
                timestamp=10.5,
                trace_id=trace_id,
                run_id=run_id,
                root_run_id=run_id,
                parent_run_id=None,
                agent_id="orch",
                agent_role="orchestrator",
                call_id=call_id,
                tool_name="create_file",
                step=1,
            )
        )

        # Permission evaluated
        self.bus.publish(
            PermissionEvaluatedEvent(
                timestamp=10.6,
                trace_id=trace_id,
                run_id=run_id,
                root_run_id=run_id,
                parent_run_id=None,
                agent_id="orch",
                agent_role="orchestrator",
                call_id=call_id,
                canonical_tool_identity="builtin:filesystem:create_file",
                tool_name="create_file",
                tool_source=ToolSource.BUILTIN,
                decision=PermissionDecision.REQUIRE_CONFIRMATION,
                matched_rule="confirm_filesystem_modifications",
                risk_level=RiskLevel.MUTATING,
                arguments_fingerprint="fp123456",
            )
        )

        # Confirmation resolved
        self.bus.publish(
            ConfirmationResolvedEvent(
                timestamp=11.0,
                trace_id=trace_id,
                run_id=run_id,
                root_run_id=run_id,
                parent_run_id=None,
                agent_id="orch",
                agent_role="orchestrator",
                call_id=call_id,
                confirmation_id="conf_001",
                canonical_tool_identity="builtin:filesystem:create_file",
                approved=True,
                duration_seconds=0.4,
            )
        )

        # Tool execution started
        self.bus.publish(
            ToolCallStartedEvent(
                timestamp=11.1,
                trace_id=trace_id,
                run_id=run_id,
                root_run_id=run_id,
                parent_run_id=None,
                agent_id="orch",
                agent_role="orchestrator",
                call_id=call_id,
                tool_name="create_file",
                tool_source=ToolSource.BUILTIN,
            )
        )

        # Tool execution finished
        self.bus.publish(
            ToolCallFinishedEvent(
                timestamp=11.2,
                trace_id=trace_id,
                run_id=run_id,
                root_run_id=run_id,
                parent_run_id=None,
                agent_id="orch",
                agent_role="orchestrator",
                call_id=call_id,
                tool_name="create_file",
                tool_source=ToolSource.BUILTIN,
                duration_seconds=0.1,
                status=ToolCallStatus.SUCCESS,
                is_error=False,
                observation_length=42,
            )
        )

        run_node = self.store.runs[run_id]
        self.assertIn(call_id, run_node.tool_calls_map)
        t_call = run_node.tool_calls_map[call_id]
        self.assertEqual(t_call.tool_name, "create_file")
        self.assertEqual(t_call.permission_decision, PermissionDecision.REQUIRE_CONFIRMATION)
        self.assertEqual(t_call.risk_level, RiskLevel.MUTATING)
        self.assertEqual(t_call.confirmation_outcome, "APPROVED")
        self.assertEqual(t_call.status, "success")
        self.assertFalse(t_call.is_error)
        self.assertEqual(t_call.duration_seconds, 0.1)

    def test_mcp_resilience_and_circuit_events(self) -> None:
        """Verify MCP retry and circuit transition events update store models."""
        self.bus.publish(
            MCPRetryEvent(
                timestamp=20.0,
                trace_id="t",
                run_id="r",
                root_run_id="r",
                parent_run_id=None,
                agent_id="a",
                agent_role="transport_specialist",
                server_name="transport_service",
                tool_name="find_connection",
                attempt=1,
                max_retries=2,
                delay_seconds=0.5,
                error_message="HTTP 503",
            )
        )

        self.bus.publish(
            MCPCircuitStateChangedEvent(
                timestamp=21.0,
                trace_id="t",
                run_id="r",
                root_run_id="r",
                parent_run_id=None,
                agent_id="a",
                agent_role="transport_specialist",
                server_name="transport_service",
                from_state="closed",
                to_state="open",
                consecutive_failures=3,
            )
        )

        # Timeline has recorded the events
        categories = [ev.category for ev in self.store.timeline]
        self.assertIn("[W2][RETRY]", categories)
        self.assertIn("[W2][CIRCUIT]", categories)

    def test_bounded_history(self) -> None:
        """Verify bounded deque prevents memory growth under event flooding."""
        small_store = RuntimeStateStore(max_events=10)
        obs = TUIObserver(small_store)
        bus = LifecycleEventBus()
        bus.subscribe(obs)

        for i in range(50):
            bus.publish(
                MemoryOperationEvent(
                    timestamp=float(i),
                    trace_id=f"tr_{i}",
                    run_id=f"run_{i}",
                    root_run_id=f"run_{i}",
                    parent_run_id=None,
                    agent_id="a",
                    agent_role="orch",
                    operation_id=f"op_{i}",
                    operation_type=MemoryOperationType.RETRIEVE,
                    status=MemoryOperationStatus.SUCCESS,
                    duration_seconds=0.01,
                )
            )

        self.assertEqual(len(small_store.raw_events), 10)
        self.assertEqual(len(small_store.timeline), 10)

    def test_observer_fault_isolation(self) -> None:
        """Verify exception in TUI observer never propagates to the EventBus."""
        class ExplodingStore(RuntimeStateStore):
            def handle_run_started(self, event):
                raise RuntimeError("Catastrophic UI Store Failure!")

        exploding_store = ExplodingStore()
        fault_obs = TUIObserver(exploding_store)
        bus = LifecycleEventBus()
        bus.subscribe(fault_obs)

        # Must not raise
        try:
            bus.publish(
                RunStartedEvent(
                    timestamp=1.0,
                    trace_id="t",
                    run_id="r",
                    root_run_id="r",
                    parent_run_id=None,
                    agent_id="a",
                    agent_role="orch",
                )
            )
        except Exception as exc:
            self.fail(f"EventBus allowed observer exception to propagate: {exc}")

    def test_dynamic_capability_population(self) -> None:
        """Verify populate_from_runtime dynamically registers tools, specialists, and MCP servers."""
        reg = ToolRegistry()
        reg.register(DummyTool("read_file", is_mutating=False, source=ToolSource.BUILTIN))
        reg.register(DummyTool("find_connection", is_mutating=False, source=ToolSource.MCP))

        cfg = load_config("config/config.docker.yaml")
        self.store.populate_from_runtime(cfg, reg)

        # Check agents
        self.assertIn("orchestrator", self.store.agents)
        self.assertIn("workspace_analyst", self.store.agents)
        self.assertIn("transport_specialist", self.store.agents)

        # Check tools
        self.assertIn("read_file", self.store.tools)
        self.assertIn("find_connection", self.store.tools)
        self.assertEqual(self.store.tools["find_connection"].source, ToolSource.MCP)
        self.assertFalse(self.store.tools["find_connection"].is_mutating)

        # Check MCP servers
        self.assertIn("transport_service", self.store.mcp_servers)
        self.assertEqual(self.store.mcp_servers["transport_service"].tools_count, 1)

    def test_explain_run_with_delegated_mcp_evidence(self) -> None:
        """Verify explain_run includes delegated specialist MCP tool calls, server, and transport."""
        root_run_id = "root_mcp_test"
        child_run_id = "child_mcp_test"
        trace_id = "trace_mcp_test"

        # Register MCP server card in store
        self.store.mcp_servers["transport_service"] = MCPServerCard(
            server_name="transport_service",
            transport_type="streamable_http",
            endpoint="http://localhost:8001/mcp",
            tools_count=1,
            tools_list=["find_connection"],
        )

        # 1. Start root orchestrator run
        self.bus.publish(
            RunStartedEvent(
                timestamp=100.0,
                trace_id=trace_id,
                run_id=root_run_id,
                root_run_id=root_run_id,
                parent_run_id=None,
                agent_id="orch-1",
                agent_role="orchestrator",
            )
        )

        # 2. Root invokes built-in tool read_file
        self.bus.publish(
            ToolCallStartedEvent(
                timestamp=101.0,
                trace_id=trace_id,
                run_id=root_run_id,
                root_run_id=root_run_id,
                parent_run_id=None,
                agent_id="orch-1",
                agent_role="orchestrator",
                call_id="call_builtin_1",
                tool_name="read_file",
                tool_source=ToolSource.BUILTIN,
                server_name=None,
            )
        )
        self.bus.publish(
            ToolCallFinishedEvent(
                timestamp=101.2,
                trace_id=trace_id,
                run_id=root_run_id,
                root_run_id=root_run_id,
                parent_run_id=None,
                agent_id="orch-1",
                agent_role="orchestrator",
                call_id="call_builtin_1",
                tool_name="read_file",
                tool_source=ToolSource.BUILTIN,
                server_name=None,
                duration_seconds=0.2,
                status=ToolCallStatus.SUCCESS,
                observation_length=42,
                is_error=False,
            )
        )

        # 3. Start child transport_specialist run
        self.bus.publish(
            RunStartedEvent(
                timestamp=102.0,
                trace_id=trace_id,
                run_id=child_run_id,
                root_run_id=root_run_id,
                parent_run_id=root_run_id,
                agent_id="child-1",
                agent_role="transport_specialist",
            )
        )

        # 4. Child invokes MCP tool find_connection
        self.bus.publish(
            ToolCallStartedEvent(
                timestamp=103.0,
                trace_id=trace_id,
                run_id=child_run_id,
                root_run_id=root_run_id,
                parent_run_id=root_run_id,
                agent_id="child-1",
                agent_role="transport_specialist",
                call_id="call_mcp_1",
                tool_name="find_connection",
                tool_source=ToolSource.MCP,
                server_name="transport_service",
            )
        )
        self.bus.publish(
            ToolCallFinishedEvent(
                timestamp=103.5,
                trace_id=trace_id,
                run_id=child_run_id,
                root_run_id=root_run_id,
                parent_run_id=root_run_id,
                agent_id="child-1",
                agent_role="transport_specialist",
                call_id="call_mcp_1",
                tool_name="find_connection",
                tool_source=ToolSource.MCP,
                server_name="transport_service",
                duration_seconds=0.5,
                status=ToolCallStatus.SUCCESS,
                observation_length=120,
                is_error=False,
            )
        )

        # Finish runs
        self.bus.publish(
            RunFinishedEvent(
                timestamp=104.0,
                trace_id=trace_id,
                run_id=child_run_id,
                root_run_id=root_run_id,
                parent_run_id=root_run_id,
                agent_id="child-1",
                agent_role="transport_specialist",
                termination_reason=TerminationReason.FINAL_ANSWER,
                steps=1,
                tool_calls=1,
                duration_seconds=2.0,
                is_success=True,
            )
        )
        self.bus.publish(
            RunFinishedEvent(
                timestamp=105.0,
                trace_id=trace_id,
                run_id=root_run_id,
                root_run_id=root_run_id,
                parent_run_id=None,
                agent_id="orch-1",
                agent_role="orchestrator",
                termination_reason=TerminationReason.FINAL_ANSWER,
                steps=2,
                tool_calls=2,
                duration_seconds=5.0,
                is_success=True,
            )
        )

        explanation = self.store.explain_run(root_run_id)

        # Assert section 1 notes delegation
        self.assertIn("1. DELEGATION & SPECIALISTS:", explanation)
        self.assertIn("transport_specialist", explanation)

        # Assert section 3 is populated with evidence
        self.assertIn("3. MCP & TOOL ABSTRACTION:", explanation)
        self.assertIn("find_connection", explanation)
        self.assertIn("transport_service", explanation)
        self.assertIn("streamable_http", explanation)
        self.assertIn("MCPToolAdapter", explanation)
        self.assertIn("ToolSpec/ToolResult", explanation)
        self.assertIn("governance path", explanation)
        self.assertIn("bounded execution budget", explanation)
        self.assertIn("read_file", explanation)

        # Assert notice
        self.assertIn("Notice: This explanation is reconstructed entirely from deterministic lifecycle events", explanation)
        self.assertIn("does not expose private LLM chain-of-thought", explanation)

    def test_explain_run_non_mcp_explicitly_states_no_mcp(self) -> None:
        """Verify explain_run for a run without MCP calls explicitly states no external MCP tool execution occurred."""
        run_id = "root_non_mcp"
        trace_id = "trace_non_mcp"

        self.bus.publish(
            RunStartedEvent(
                timestamp=200.0,
                trace_id=trace_id,
                run_id=run_id,
                root_run_id=run_id,
                parent_run_id=None,
                agent_id="orch-1",
                agent_role="orchestrator",
            )
        )
        self.bus.publish(
            ToolCallStartedEvent(
                timestamp=201.0,
                trace_id=trace_id,
                run_id=run_id,
                root_run_id=run_id,
                parent_run_id=None,
                agent_id="orch-1",
                agent_role="orchestrator",
                call_id="call_builtin_2",
                tool_name="list_directory",
                tool_source=ToolSource.BUILTIN,
                server_name=None,
            )
        )
        self.bus.publish(
            ToolCallFinishedEvent(
                timestamp=201.1,
                trace_id=trace_id,
                run_id=run_id,
                root_run_id=run_id,
                parent_run_id=None,
                agent_id="orch-1",
                agent_role="orchestrator",
                call_id="call_builtin_2",
                tool_name="list_directory",
                tool_source=ToolSource.BUILTIN,
                server_name=None,
                duration_seconds=0.1,
                status=ToolCallStatus.SUCCESS,
                observation_length=15,
                is_error=False,
            )
        )
        self.bus.publish(
            RunFinishedEvent(
                timestamp=202.0,
                trace_id=trace_id,
                run_id=run_id,
                root_run_id=run_id,
                parent_run_id=None,
                agent_id="orch-1",
                agent_role="orchestrator",
                termination_reason=TerminationReason.FINAL_ANSWER,
                steps=1,
                tool_calls=1,
                duration_seconds=2.0,
                is_success=True,
            )
        )

        explanation = self.store.explain_run(run_id)

        self.assertIn("3. MCP & TOOL ABSTRACTION:", explanation)
        self.assertIn("No external MCP tool execution was observed for this run", explanation)
        self.assertIn("list_directory", explanation)
        self.assertIn("built-in harness capabilities", explanation)


class TestTUIConfirmationHandler(unittest.TestCase):
    """Tests for the TUI confirmation handler protocol compliance."""

    def test_confirmation_approval(self) -> None:
        """Verify prompt callback approval returns True through handler."""
        handler = TUIConfirmationHandler(timeout_seconds=2.0)

        def mock_ui_prompt(req: PermissionRequest, resolve_fn):
            # Simulate operator pressing [Y]
            threading.Thread(target=lambda: resolve_fn(True)).start()

        handler.set_prompt_callback(mock_ui_prompt)

        req = PermissionRequest(
            run_id="r",
            agent_id="a",
            agent_role="orch",
            delegation_depth=0,
            call_id="c",
            canonical_tool_identity="builtin:filesystem:create_file",
            tool_name="create_file",
            tool_source=ToolSource.BUILTIN,
            server_name=None,
            arguments={"path": "trip.md"},
            arguments_fingerprint="abc",
            arguments_summary={"path": "trip.md"},
            resource_descriptor="/app/workspace/trip.md",
            risk_level=RiskLevel.MUTATING,
        )

        decision = handler.request_confirmation(req)
        self.assertTrue(decision)

    def test_confirmation_rejection(self) -> None:
        """Verify prompt callback rejection returns False through handler."""
        handler = TUIConfirmationHandler(timeout_seconds=2.0)

        def mock_ui_prompt(req: PermissionRequest, resolve_fn):
            # Simulate operator pressing [N]
            threading.Thread(target=lambda: resolve_fn(False)).start()

        handler.set_prompt_callback(mock_ui_prompt)

        req = PermissionRequest(
            run_id="r",
            agent_id="a",
            agent_role="orch",
            delegation_depth=0,
            call_id="c",
            canonical_tool_identity="builtin:filesystem:create_file",
            tool_name="create_file",
            tool_source=ToolSource.BUILTIN,
            server_name=None,
            arguments={"path": "trip.md"},
            arguments_fingerprint="abc",
            arguments_summary={"path": "trip.md"},
            resource_descriptor="/app/workspace/trip.md",
            risk_level=RiskLevel.MUTATING,
        )

        decision = handler.request_confirmation(req)
        self.assertFalse(decision)

    def test_confirmation_timeout_defaults_false(self) -> None:
        """Verify timeout safely returns False."""
        handler = TUIConfirmationHandler(timeout_seconds=0.1)

        def stalling_prompt(req, resolve_fn):
            pass  # Does not resolve

        handler.set_prompt_callback(stalling_prompt)

        req = PermissionRequest(
            run_id="r",
            agent_id="a",
            agent_role="orch",
            delegation_depth=0,
            call_id="c",
            canonical_tool_identity="builtin:filesystem:create_file",
            tool_name="create_file",
            tool_source=ToolSource.BUILTIN,
            server_name=None,
            arguments={},
            arguments_fingerprint="abc",
            arguments_summary={},
            resource_descriptor="trip.md",
        )

        decision = handler.request_confirmation(req)
        self.assertFalse(decision)


class TestTUIHealthAndScenarios(unittest.TestCase):
    """Tests for health probes and evaluator scenarios."""

    def test_curated_scenarios_exist(self) -> None:
        """Verify all 5 curated scenarios are registered with checkpoints."""
        ids = [s.id for s in SCENARIOS]
        self.assertIn("full_journey", ids)
        self.assertIn("workspace_analyst", ids)
        self.assertIn("transport_specialist", ids)
        self.assertIn("security_tour", ids)
        self.assertIn("resilience_tour", ids)

    def test_deterministic_resilience_tour(self) -> None:
        """Verify the deterministic resilience tour runs cleanly with mocked clock."""
        outputs = []
        bus = LifecycleEventBus()
        run_deterministic_resilience_tour(bus, lambda msg: outputs.append(msg))
        out_text = "\n".join(outputs)
        self.assertIn("Starting Deterministic MCP Resilience", out_text)
        self.assertIn("RECOVERED", out_text)
        self.assertIn("Current Circuit State: OPEN", out_text)
        self.assertIn("Probe executed against service", out_text)
        self.assertIn("Final Circuit State: CLOSED", out_text)

    def test_workspace_health_probe(self) -> None:
        """Verify workspace health check returns READY for existing directory."""
        status, ok = check_workspace_health(".")
        self.assertTrue(ok)
        self.assertIn("READY", status)

    def test_dynamic_date_construction(self) -> None:
        """Verify transport date is derived dynamically using local date semantics without fixed clock."""
        from datetime import datetime
        from harness.tui.scenarios import build_full_journey_prompt, get_tomorrow_transport_date

        fixed_now = datetime(2026, 9, 19, 14, 0, 0)
        date_str, dep_iso = get_tomorrow_transport_date(now_fn=lambda: fixed_now)
        self.assertEqual(date_str, "2026-09-20")
        self.assertEqual(dep_iso, "2026-09-20T08:30:00")

        prompt = build_full_journey_prompt(now_fn=lambda: fixed_now)
        self.assertIn("2026-09-20", prompt)
        self.assertIn("at or after 09:00", prompt)
        self.assertIn("Passau Hbf to München Hbf", prompt)
        self.assertIn("travel_plan.md", prompt)
        self.assertIn("transport_specialist", prompt)

    def test_shutdown_cancels_pending_confirmation(self) -> None:
        """Verify cancel_pending immediately unblocks waiting worker with False."""
        handler = TUIConfirmationHandler(timeout_seconds=60.0, default_decision=False)
        received_request: list[PermissionRequest] = []

        def mock_prompt(req: PermissionRequest, resolve_fn: Any) -> None:
            received_request.append(req)

        handler.set_prompt_callback(mock_prompt)

        req = PermissionRequest(
            run_id="run-1",
            agent_id="orch",
            agent_role="orch",
            delegation_depth=0,
            call_id="call-1",
            canonical_tool_identity="builtin:filesystem:create_file",
            tool_name="create_file",
            tool_source=ToolSource.BUILTIN,
            server_name=None,
            arguments={"path": "test.txt", "content": "hello"},
            arguments_fingerprint="abc",
            arguments_summary={"path": "test.txt"},
        )

        outcome_box = [None]

        def worker() -> None:
            outcome_box[0] = handler.request_confirmation(req)

        t = threading.Thread(target=worker)
        t.start()

        time.sleep(0.05)
        self.assertEqual(len(received_request), 1)

        t0 = time.time()
        handler.cancel_pending()
        t.join(timeout=2.0)

        self.assertFalse(t.is_alive(), "Worker thread must exit cleanly upon cancel_pending")
        self.assertFalse(outcome_box[0], "Cancelled confirmation must safely evaluate to False")
        self.assertLess(time.time() - t0, 1.0, "Worker must unblock immediately without waiting for timeout")


class TestTUIPilot(unittest.IsolatedAsyncioTestCase):
    """Headless integration tests for the Textual Operator Console application."""

    async def test_app_mounting_and_navigation(self) -> None:
        """Verify the full Textual app mounts all 9 screens and switches tabs cleanly."""
        from unittest.mock import MagicMock
        from harness.tui.app import AgentHarnessApp

        config = load_config("config/config.yaml")
        store = RuntimeStateStore()
        bus = LifecycleEventBus()
        controller = MagicMock()
        workspace = MagicMock()
        workspace.root_path = "/app/workspace"
        registry = MagicMock()
        registry.list_tools.return_value = []

        app = AgentHarnessApp(
            controller=controller,
            config=config,
            workspace=workspace,
            registry=registry,
            event_bus=bus,
            store=store,
        )

        async with app.run_test() as pilot:
            # Verify initial home tab
            self.assertEqual(app.query_one("#main-tabs").active, "tab-home")

            # Verify switching to all screens via numeric keybindings
            await pilot.press("2")
            self.assertEqual(app.query_one("#main-tabs").active, "tab-runs")
            await pilot.press("3")
            self.assertEqual(app.query_one("#main-tabs").active, "tab-agents")
            await pilot.press("4")
            self.assertEqual(app.query_one("#main-tabs").active, "tab-tools")
            await pilot.press("5")
            self.assertEqual(app.query_one("#main-tabs").active, "tab-mcp")
            await pilot.press("6")
            self.assertEqual(app.query_one("#main-tabs").active, "tab-security")
            await pilot.press("7")
            self.assertEqual(app.query_one("#main-tabs").active, "tab-events")
            await pilot.press("8")
            self.assertEqual(app.query_one("#main-tabs").active, "tab-overview")
            await pilot.press("9")
            self.assertEqual(app.query_one("#main-tabs").active, "tab-help")
            await pilot.press("1")
            self.assertEqual(app.query_one("#main-tabs").active, "tab-home")

    async def test_cross_thread_tui_and_governance_flow(self) -> None:
        """Verify real cross-thread event delivery, confirmation approval, and tree representation."""
        import tempfile
        from unittest.mock import MagicMock
        from harness.tui.app import AgentHarnessApp
        from harness.tui.modals import ConfirmationModal
        from harness.tools.workspace import Workspace
        from harness.tools.registry import ToolRegistry
        from harness.tools.filesystem import CreateFileTool
        from harness.tools.executor import ToolExecutor
        from harness.runtime.context import ExecutionContext, execution_context_scope
        from harness.permissions import (
            PermissionManager,
            PolicyEngine,
            PolicyRule,
            PermissionDecision,
            RiskLevel,
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            ws = Workspace(tmpdir)
            target_file = ws.root / "approved_output.txt"
            self.assertFalse(target_file.exists())

            bus = LifecycleEventBus()
            store = RuntimeStateStore()
            observer = TUIObserver(store)
            bus.subscribe(observer)

            conf_handler = TUIConfirmationHandler(timeout_seconds=5.0)
            rule = PolicyRule(
                name="confirm_create",
                decision=PermissionDecision.REQUIRE_CONFIRMATION,
                priority=100,
                tool_pattern="create_file",
                risk_level=RiskLevel.MUTATING,
            )
            policy = PolicyEngine(rules=[rule], default_stance=PermissionDecision.DENY)
            perm_manager = PermissionManager(
                policy_engine=policy,
                confirmation_handler=conf_handler,
                event_bus=bus,
                workspace=ws,
            )
            executor = ToolExecutor(
                event_bus=bus,
                permission_manager=perm_manager,
            )
            registry = ToolRegistry()
            create_tool = CreateFileTool(ws)
            registry.register(create_tool)

            config = load_config("config/config.yaml")
            controller = MagicMock()

            app = AgentHarnessApp(
                controller=controller,
                config=config,
                workspace=ws,
                registry=registry,
                event_bus=bus,
                store=store,
                confirmation_handler=conf_handler,
            )

            async with app.run_test() as pilot:
                root_run_id = "root_run_test_001"
                child_run_id = "child_run_test_002"

                def background_agent_work() -> None:
                    bus.publish(RunStartedEvent(
                        timestamp=time.time(),
                        trace_id="4bf92f3577b34da6a3ce929d0e0e4736",
                        run_id=root_run_id,
                        root_run_id=root_run_id,
                        parent_run_id=None,
                        agent_id="orch-1",
                        agent_role="orchestrator",
                    ))

                    bus.publish(DelegationStartedEvent(
                        timestamp=time.time(),
                        trace_id="4bf92f3577b34da6a3ce929d0e0e4736",
                        run_id=root_run_id,
                        root_run_id=root_run_id,
                        parent_run_id=None,
                        agent_id="orch-1",
                        agent_role="orchestrator",
                        delegation_id="del-1",
                        parent_call_id="call-1",
                        child_run_id=child_run_id,
                        parent_agent_id="orch-1",
                        parent_agent_role="orchestrator",
                        child_agent_id="spec-1",
                        child_agent_role="workspace_analyst",
                        delegation_depth=1,
                        task_length=17,
                    ))

                    bus.publish(RunStartedEvent(
                        timestamp=time.time(),
                        trace_id="4bf92f3577b34da6a3ce929d0e0e4736",
                        run_id=child_run_id,
                        root_run_id=root_run_id,
                        parent_run_id=root_run_id,
                        agent_id="spec-1",
                        agent_role="workspace_analyst",
                    ))

                    bus.publish(RunFinishedEvent(
                        timestamp=time.time(),
                        trace_id="4bf92f3577b34da6a3ce929d0e0e4736",
                        run_id=child_run_id,
                        root_run_id=root_run_id,
                        parent_run_id=root_run_id,
                        agent_id="spec-1",
                        agent_role="workspace_analyst",
                        is_success=True,
                        steps=1,
                        tool_calls=0,
                        duration_seconds=0.1,
                    ))

                    bus.publish(DelegationFinishedEvent(
                        timestamp=time.time(),
                        trace_id="4bf92f3577b34da6a3ce929d0e0e4736",
                        run_id=root_run_id,
                        root_run_id=root_run_id,
                        parent_run_id=None,
                        agent_id="orch-1",
                        agent_role="orchestrator",
                        delegation_id="del-1",
                        parent_call_id="call-1",
                        child_run_id=child_run_id,
                        parent_agent_id="orch-1",
                        parent_agent_role="orchestrator",
                        child_agent_id="spec-1",
                        child_agent_role="workspace_analyst",
                        delegation_depth=1,
                        status="success",
                        duration_seconds=0.1,
                        steps=1,
                        tool_calls=0,
                    ))

                    from harness.agent.budget import ExecutionBudget
                    ctx = ExecutionContext.create_root(
                        budget=ExecutionBudget(max_steps=10),
                        agent_id="orch-1",
                        agent_role="orchestrator",
                        run_id=root_run_id,
                        trace_id="4bf92f3577b34da6a3ce929d0e0e4736",
                    )
                    with execution_context_scope(ctx):
                        res = executor.execute(create_tool, {"path": "approved_output.txt", "content": "Verified Content"})
                    assert not res.is_error, f"Execution failed: {res.content}"

                    bus.publish(RunFinishedEvent(
                        timestamp=time.time(),
                        trace_id="4bf92f3577b34da6a3ce929d0e0e4736",
                        run_id=root_run_id,
                        root_run_id=root_run_id,
                        parent_run_id=None,
                        agent_id="orch-1",
                        agent_role="orchestrator",
                        is_success=True,
                        steps=2,
                        tool_calls=1,
                        duration_seconds=0.5,
                    ))

                worker_thread = threading.Thread(target=background_agent_work)
                worker_thread.start()

                # Wait for ConfirmationModal to appear on screen and approve via 'y'
                modal = None
                for _ in range(50):
                    await pilot.pause(0.05)
                    if isinstance(app.screen, ConfirmationModal):
                        modal = app.screen
                        break
                self.assertIsNotNone(modal, "ConfirmationModal was not displayed on screen")
                self.assertEqual(modal.request.canonical_tool_identity, "builtin:filesystem:create_file")
                self.assertEqual(modal.request.risk_level, RiskLevel.MUTATING)

                await pilot.press("y")
                await pilot.pause(0.1)

                worker_thread.join(timeout=3.0)
                self.assertFalse(worker_thread.is_alive())

                self.assertTrue(target_file.exists())
                self.assertEqual(target_file.read_text(), "Verified Content")

                self.assertIn(root_run_id, store.runs)
                self.assertIn(child_run_id, store.runs)
                self.assertEqual(store.runs[root_run_id].children_run_ids, [child_run_id])
                self.assertEqual(store.runs[child_run_id].parent_run_id, root_run_id)

                gov_records = list(store.governance_log)
                self.assertGreaterEqual(len(gov_records), 1)
                rec = gov_records[0]
                self.assertEqual(rec.canonical_tool_identity, "builtin:filesystem:create_file")
                self.assertEqual(rec.decision, PermissionDecision.REQUIRE_CONFIRMATION)
                self.assertEqual(rec.confirmation_outcome, "APPROVED")

    async def test_cross_thread_tui_governance_rejection(self) -> None:
        """Verify simulated operator rejection prevents execution and sets error outcome."""
        import tempfile
        from unittest.mock import MagicMock
        from harness.tui.app import AgentHarnessApp
        from harness.tui.modals import ConfirmationModal
        from harness.tools.workspace import Workspace
        from harness.tools.registry import ToolRegistry
        from harness.tools.filesystem import CreateFileTool
        from harness.tools.executor import ToolExecutor
        from harness.runtime.context import ExecutionContext, execution_context_scope
        from harness.permissions import (
            PermissionManager,
            PolicyEngine,
            PolicyRule,
            PermissionDecision,
            RiskLevel,
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            ws = Workspace(tmpdir)
            target_file = ws.root / "rejected_output.txt"

            bus = LifecycleEventBus()
            store = RuntimeStateStore()
            observer = TUIObserver(store)
            bus.subscribe(observer)

            conf_handler = TUIConfirmationHandler(timeout_seconds=5.0)
            rule = PolicyRule(
                name="confirm_create",
                decision=PermissionDecision.REQUIRE_CONFIRMATION,
                priority=100,
                tool_pattern="create_file",
                risk_level=RiskLevel.MUTATING,
            )
            policy = PolicyEngine(rules=[rule], default_stance=PermissionDecision.DENY)
            perm_manager = PermissionManager(
                policy_engine=policy,
                confirmation_handler=conf_handler,
                event_bus=bus,
                workspace=ws,
            )
            executor = ToolExecutor(
                event_bus=bus,
                permission_manager=perm_manager,
            )
            create_tool = CreateFileTool(ws)

            config = load_config("config/config.yaml")
            controller = MagicMock()
            registry = ToolRegistry()

            app = AgentHarnessApp(
                controller=controller,
                config=config,
                workspace=ws,
                registry=registry,
                event_bus=bus,
                store=store,
                confirmation_handler=conf_handler,
            )

            async with app.run_test() as pilot:
                run_id = "run_reject_001"
                def background_work() -> None:
                    bus.publish(RunStartedEvent(
                        timestamp=time.time(),
                        trace_id="4bf92f3577b34da6a3ce929d0e0e4736",
                        run_id=run_id,
                        root_run_id=run_id,
                        parent_run_id=None,
                        agent_id="orch-1",
                        agent_role="orchestrator",
                    ))
                    from harness.agent.budget import ExecutionBudget
                    ctx = ExecutionContext.create_root(
                        budget=ExecutionBudget(max_steps=10),
                        agent_id="orch-1",
                        agent_role="orchestrator",
                        run_id=run_id,
                        trace_id="4bf92f3577b34da6a3ce929d0e0e4736",
                    )
                    with execution_context_scope(ctx):
                        res = executor.execute(create_tool, {"path": "rejected_output.txt", "content": "Should Not Exist"})
                    assert res.is_error, "Execution should have failed due to rejection"

                worker_thread = threading.Thread(target=background_work)
                worker_thread.start()

                # Wait for ConfirmationModal to appear on screen and reject via 'n'
                modal = None
                for _ in range(50):
                    await pilot.pause(0.05)
                    if isinstance(app.screen, ConfirmationModal):
                        modal = app.screen
                        break
                self.assertIsNotNone(modal, "ConfirmationModal was not displayed on screen")

                await pilot.press("n")
                await pilot.pause(0.1)

                worker_thread.join(timeout=3.0)
                self.assertFalse(worker_thread.is_alive())

                self.assertFalse(target_file.exists())

                gov_records = list(store.governance_log)
                self.assertGreaterEqual(len(gov_records), 1)
                rec = gov_records[-1]
                self.assertEqual(rec.canonical_tool_identity, "builtin:filesystem:create_file")
                self.assertEqual(rec.decision, PermissionDecision.REQUIRE_CONFIRMATION)
                self.assertEqual(rec.confirmation_outcome, "REJECTED")

    async def test_startup_focus_contract_and_clean_home(self) -> None:
        """Verify startup has NO persistent Input widget focused, scenarios table is populated, and 1-9 works immediately."""
        from unittest.mock import MagicMock
        from harness.config import load_config
        from harness.tui.app import AgentHarnessApp
        from harness.tui.screens.home import HomeScreen
        from textual.widgets import DataTable, Input

        config = load_config("config/config.yaml")
        store = RuntimeStateStore()
        bus = LifecycleEventBus()
        controller = MagicMock()
        workspace = MagicMock()
        workspace.root_path = "/app/workspace"
        registry = MagicMock()
        registry.list_tools.return_value = []

        app = AgentHarnessApp(
            controller=controller,
            config=config,
            workspace=workspace,
            registry=registry,
            event_bus=bus,
            store=store,
        )

        async with app.run_test() as pilot:
            # 1. Startup tab is Home
            self.assertEqual(app.query_one("#main-tabs").active, "tab-home")

            # 2. Verify no Input is focused on startup
            focused = app.focused
            self.assertFalse(
                isinstance(focused, Input),
                f"Startup focused widget must NOT be an Input, but was {type(focused)}",
            )

            # 3. Verify HomeScreen scenarios table has the 4 primary scenarios
            home_screen = app.query_one("#screen-home", HomeScreen)
            table = home_screen.query_one("#home-scenarios-table", DataTable)
            self.assertEqual(table.row_count, 4)

            # 4. Verify numeric keypresses navigate immediately without typing
            await pilot.press("2")
            self.assertEqual(app.query_one("#main-tabs").active, "tab-runs")
            await pilot.press("1")
            self.assertEqual(app.query_one("#main-tabs").active, "tab-home")

    async def test_custom_prompt_modal_lifecycle(self) -> None:
        """Verify C opens modal, numeric characters type as text inside modal, and Esc dismisses cleanly."""
        from unittest.mock import MagicMock
        from harness.config import load_config
        from harness.tui.app import AgentHarnessApp
        from harness.tui.modals import CustomPromptModal
        from textual.widgets import TextArea

        config = load_config("config/config.yaml")
        store = RuntimeStateStore()
        bus = LifecycleEventBus()
        controller = MagicMock()
        workspace = MagicMock()
        workspace.root_path = "/app/workspace"
        registry = MagicMock()
        registry.list_tools.return_value = []

        app = AgentHarnessApp(
            controller=controller,
            config=config,
            workspace=workspace,
            registry=registry,
            event_bus=bus,
            store=store,
        )

        async with app.run_test() as pilot:
            self.assertEqual(app.query_one("#main-tabs").active, "tab-home")

            # Press 'c' to open CustomPromptModal
            await pilot.press("c")
            await pilot.pause(0.1)

            self.assertTrue(isinstance(app.screen, CustomPromptModal))
            prompt_input = app.screen.query_one("#custom-prompt-input", TextArea)
            self.assertTrue(prompt_input.has_focus)

            # Type digits and words into modal input
            await pilot.press("1", "2", "3", " ", "t", "e", "s", "t")
            self.assertEqual(prompt_input.value, "123 test")

            # Verify that main tabs did NOT navigate to tab-runs / tab-agents while typing digits
            self.assertEqual(app.query_one("#main-tabs").active, "tab-home")

            # Press escape to dismiss modal
            await pilot.press("escape")
            await pilot.pause(0.1)
            self.assertFalse(isinstance(app.screen, CustomPromptModal))

            # Verify 1-9 navigation is restored
            await pilot.press("2")
            self.assertEqual(app.query_one("#main-tabs").active, "tab-runs")

    def test_post_run_summary_extraction_truthfulness(self) -> None:
        """Verify post-run summary extracts memory HIT/MISS, delegation, MCP, governance, and truthful workspace status."""
        from harness.tui.modals import extract_post_run_summary
        from harness.tui.models import RunNode, ToolCallNode
        from harness.runtime.events import MemoryOperationEvent, MemoryOperationType, MemoryOperationStatus
        from harness.permissions.base import RiskLevel

        store = RuntimeStateStore()

        # Empty store returns empty dict
        self.assertEqual(extract_post_run_summary(store), {})

        # Build synthetic root run with child and tool calls
        root_run = RunNode(
            run_id="root-100",
            trace_id="trace-abc-12345678",
            parent_run_id=None,
            root_run_id="root-100",
            agent_id="agent-orch",
            agent_role="orchestrator",
            is_root=True,
            status="SUCCESS",
            duration_seconds=1.85,
            children_run_ids=["child-200"],
        )
        child_run = RunNode(
            run_id="child-200",
            trace_id="trace-abc-12345678",
            parent_run_id="root-100",
            root_run_id="root-100",
            agent_id="agent-trans",
            agent_role="transport_specialist",
            is_root=False,
            status="SUCCESS",
            duration_seconds=0.75,
        )

        # MCP tool inside child run
        mcp_tool = ToolCallNode(
            call_id="call-1",
            run_id="child-200",
            tool_name="find_connection",
            canonical_identity="mcp:transport_service:find_connection",
            tool_source=ToolSource.MCP,
            server_name="transport_service",
            status="SUCCESS",
        )
        child_run.tool_calls_map["call-1"] = mcp_tool

        # Builtin file creation inside root run (approved)
        fs_tool = ToolCallNode(
            call_id="call-2",
            run_id="root-100",
            tool_name="create_file",
            canonical_identity="builtin:filesystem:create_file",
            tool_source=ToolSource.BUILTIN,
            arguments_summary={"path": "travel_plan.md"},
            risk_level=RiskLevel.MUTATING,
            confirmation_outcome="APPROVED",
            status="SUCCESS",
        )
        root_run.tool_calls_map["call-2"] = fs_tool

        store.runs["root-100"] = root_run
        store.runs["child-200"] = child_run
        store.root_run_ids.append("root-100")

        # Record memory event with hit
        mem_event = MemoryOperationEvent(
            timestamp=time.time(),
            trace_id="trace-abc-12345678",
            run_id="root-100",
            root_run_id="root-100",
            parent_run_id=None,
            agent_id="agent-orch",
            agent_role="orchestrator",
            operation_id="op-123",
            operation_type=MemoryOperationType.RETRIEVE,
            entry_count=1,
            duration_seconds=0.01,
            status=MemoryOperationStatus.SUCCESS,
        )
        store.raw_events.append(mem_event)

        summary = extract_post_run_summary(store, "root-100")
        self.assertEqual(summary["memory"], "HIT (1 entry retrieved)")
        self.assertEqual(summary["delegation"], "delegated to transport_specialist")
        self.assertEqual(summary["mcp"], "find_connection (transport_service)")
        self.assertEqual(summary["governance"], "create_file → APPROVED")
        self.assertEqual(summary["workspace"], "travel_plan.md created inside workspace")
        self.assertEqual(summary["trace"], "recorded (trace-ab)")

        # Verify workspace containment truthfulness: NEVER claims boundary escape blocked without escape event
        clean_tool = ToolCallNode(
            call_id="call-3",
            run_id="root-100",
            tool_name="read_file",
            canonical_identity="builtin:filesystem:read_file",
            tool_source=ToolSource.BUILTIN,
            status="SUCCESS",
        )
        root_run.tool_calls_map = {"call-3": clean_tool}
        summary_clean = extract_post_run_summary(store, "root-100")
        self.assertEqual(summary_clean["workspace"], "path resolved within configured root")

        # Now test that boundary escape blocked is ONLY reported when an escape error genuinely occurs
        escape_tool = ToolCallNode(
            call_id="call-4",
            run_id="root-100",
            tool_name="read_file",
            canonical_identity="builtin:filesystem:read_file",
            tool_source=ToolSource.BUILTIN,
            is_error=True,
            error_message="Path traversal escape outside workspace root",
            status="ERROR",
        )
        root_run.tool_calls_map = {"call-4": escape_tool}
        summary_escape = extract_post_run_summary(store, "root-100")
        self.assertEqual(summary_escape["workspace"], "boundary escape blocked")

    async def test_post_run_summary_modal_actions(self) -> None:
        """Verify PostRunSummaryModal keyboard and button dismiss values."""
        from unittest.mock import MagicMock
        from harness.tui.modals import PostRunSummaryModal

        summary = {
            "run_id": "run-xyz-987",
            "agent_role": "orchestrator",
            "status": "SUCCESS",
            "duration": "1.23s",
            "memory": "HIT (1 entry retrieved)",
            "delegation": "delegated to transport_specialist",
            "mcp": "find_connection (transport_service)",
            "governance": "create_file → APPROVED",
            "workspace": "travel_plan.md created inside workspace",
            "trace": "recorded (run-xyz-)",
        }

        modal = PostRunSummaryModal(summary)
        dismiss_val = None

        def record_dismiss(val):
            nonlocal dismiss_val
            dismiss_val = val

        # Test key dismissals
        modal.dismiss = record_dismiss
        modal.on_key(MagicMock(key="e"))
        self.assertEqual(dismiss_val, "explain")

        modal.on_key(MagicMock(key="t"))
        self.assertEqual(dismiss_val, "trace")

        modal.on_key(MagicMock(key="s"))
        self.assertEqual(dismiss_val, "security")

        modal.on_key(MagicMock(key="escape"))
        self.assertIsNone(dismiss_val)


class TestTUIPresentationPolish(unittest.IsolatedAsyncioTestCase):
    """Tests for presentation polish: Markdown rendering, safe runtime evidence, error cards, and isolation."""

    def _create_run(self, run_id: str, **kwargs: Any) -> RunNode:
        params: dict[str, Any] = {
            "trace_id": f"trace-{run_id}",
            "parent_run_id": None,
            "root_run_id": run_id,
            "agent_id": f"agent-{run_id}",
            "agent_role": "orchestrator",
            "is_root": True,
        }
        params.update(kwargs)
        return RunNode(run_id=run_id, **params)

    def test_structured_error_card_budget_separation(self) -> None:
        """Verify error card cleanly distinguishes configured budget from run wall time."""
        from harness.tui.screens.runs import RunsScreen

        store = RuntimeStateStore()
        screen = RunsScreen(store)

        run = self._create_run(
            run_id="run-err-1",
            agent_role="orchestrator",
            status="ERROR",
            duration_seconds=104.8,
            steps=5,
            error_message="Execution time budget (60.0s) exceeded.",
        )
        card = screen._format_error_card(run)
        self.assertIn("EXECUTION STOPPED", card)
        self.assertIn("Time budget exceeded", card)
        self.assertIn("Configured budget:", card)
        self.assertIn("60.0 s", card)
        self.assertIn("Run wall time:", card)
        self.assertIn("104.8 s", card)
        self.assertIn("The configured execution budget terminated further agent execution.", card)

        # Step limit error
        run_steps = self._create_run(
            run_id="run-err-2",
            agent_role="orchestrator",
            status="ERROR",
            duration_seconds=42.1,
            steps=10,
            error_message="Maximum agent steps (10) reached before final response.",
        )
        card_steps = screen._format_error_card(run_steps)
        self.assertIn("Step limit exceeded", card_steps)
        self.assertIn("Steps taken:", card_steps)
        self.assertIn("10", card_steps)
        self.assertIn("Run wall time:", card_steps)
        self.assertIn("42.1 s", card_steps)

        # Workspace boundary violation
        run_bound = self._create_run(
            run_id="run-err-3",
            agent_role="orchestrator",
            status="ERROR",
            duration_seconds=1.2,
            steps=2,
            error_message="Path traversal escape outside workspace boundary: /etc/passwd",
        )
        card_bound = screen._format_error_card(run_bound)
        self.assertIn("Workspace boundary violation", card_bound)
        self.assertIn("unauthorized path access", card_bound)

    def test_authoritative_evidence_table_safe_summary(self) -> None:
        """Verify evidence table uses safe redacted summaries and outranks model prose."""
        from harness.tui.screens.runs import RunsScreen

        store = RuntimeStateStore()
        screen = RunsScreen(store)

        tool_ok = ToolCallNode(
            call_id="call-safe-1",
            run_id="run-sec-1",
            tool_name="read_file",
            canonical_identity="builtin:filesystem:read_file",
            tool_source=ToolSource.BUILTIN,
            status="SUCCESS",
            risk_level=RiskLevel.SENSITIVE,
            permission_decision=PermissionDecision.ALLOW,
            confirmation_outcome="APPROVED",
            arguments_summary={"path": ".env.evaluator_sample", "secret_token": "REDACTED"},
        )
        tool_err = ToolCallNode(
            call_id="call-safe-2",
            run_id="run-sec-1",
            tool_name="read_file",
            canonical_identity="builtin:filesystem:read_file",
            tool_source=ToolSource.BUILTIN,
            status="ERROR",
            is_error=True,
            risk_level=RiskLevel.CRITICAL,
            permission_decision=PermissionDecision.DENY,
            arguments_summary={"path": "../../etc/shadow"},
        )

        run = self._create_run(
            run_id="run-sec-1",
            agent_role="orchestrator",
            status="SUCCESS",
            tool_calls_map={"call-safe-1": tool_ok, "call-safe-2": tool_err},
            final_response="All files read successfully without restrictions.",  # Contradictory prose
        )

        table = screen._build_evidence_table(run)
        self.assertEqual(table.title, "AUTHORITATIVE RUNTIME EVIDENCE")
        self.assertEqual(len(table.rows), 2)

        from rich.console import Console
        from io import StringIO
        c = Console(file=StringIO(), width=100)
        c.print(table)
        output = c.file.getvalue()

        # Tool 1: read_file .env.evaluator_sample, APPROVED, SUCCESS
        self.assertIn("read_file", output)
        self.assertIn(".env.evaluator_sample", output)
        self.assertNotIn("secret_token", output)
        self.assertIn("SUCCESS", output)

        # Tool 2: ../../etc/shadow, DENY, ERROR
        self.assertIn("../../etc/shadow", output)
        self.assertIn("DENY", output)
        self.assertIn("ERROR", output)

        # The table reflects actual store facts, NOT the contradictory prose
        self.assertNotIn("without restrictions", output)

    def test_evidence_table_does_not_conflate_policy_and_workspace_boundary(self) -> None:
        """Verify evidence table separates policy ALLOW from execution BLOCKED — Workspace Boundary."""
        from harness.tui.screens.runs import RunsScreen

        store = RuntimeStateStore()
        screen = RunsScreen(store)

        tool_traversal = ToolCallNode(
            call_id="call-trav-1",
            run_id="run-trav-1",
            tool_name="read_file",
            canonical_identity="builtin:filesystem:read_file",
            tool_source=ToolSource.BUILTIN,
            status="ERROR",
            is_error=True,
            risk_level=RiskLevel.READ_ONLY,
            permission_decision=PermissionDecision.ALLOW,
            confirmation_outcome=None,
            arguments_summary={"path": "../outside_workspace_test.txt"},
            error_message="Access denied: path '../outside_workspace_test.txt' resolves to '/outside_workspace_test.txt', which is outside the workspace boundary '/app/workspace'.",
        )

        run = self._create_run(
            run_id="run-trav-1",
            agent_role="orchestrator",
            status="SUCCESS",
            tool_calls_map={"call-trav-1": tool_traversal},
        )

        table = screen._build_evidence_table(run)
        from rich.console import Console
        from io import StringIO
        c = Console(file=StringIO(), width=160)
        c.print(table)
        output = c.file.getvalue()
        normalized_output = " ".join(output.split())

        # Decision column shows ALLOW (policy decision)
        self.assertIn("ALLOW", normalized_output)
        # Outcome column shows BLOCKED — Workspace Boundary (filesystem sandbox enforcement)
        self.assertIn("BLOCKED — Workspace Boundary", normalized_output)
        self.assertNotIn("DENY", normalized_output)

    async def test_empty_inspector_displays_run_overview(self) -> None:
        """Verify empty inspector displays RUN OVERVIEW instead of blank space."""
        from unittest.mock import MagicMock
        from harness.tui.app import AgentHarnessApp
        from harness.tui.screens.runs import RunsScreen
        from textual.widgets import Static

        config = load_config("config/config.yaml")
        store = RuntimeStateStore()
        bus = LifecycleEventBus()
        controller = MagicMock()
        workspace = MagicMock()
        workspace.root_path = "/app/workspace"
        registry = MagicMock()
        registry.list_tools.return_value = []

        run = self._create_run(
            run_id="run-overview-1",
            agent_role="orchestrator",
            status="SUCCESS",
            duration_seconds=3.45,
            steps=4,
            tool_calls=2,
            llm_calls_count=3,
            total_tokens=1500,
            prompt_tokens=1000,
            completion_tokens=500,
            trace_id="trace-test-123",
            task_description="Plan a trip to Zurich",
        )
        store.runs["run-overview-1"] = run
        store.root_run_ids.append("run-overview-1")
        store.selected_run_id = None

        app = AgentHarnessApp(
            controller=controller,
            config=config,
            workspace=workspace,
            registry=registry,
            event_bus=bus,
            store=store,
        )

        async with app.run_test() as pilot:
            await pilot.press("2")
            runs_screen = app.query_one("#screen-runs", RunsScreen)
            inspector_text = str(runs_screen.query_one("#inspector-text", Static).render())

            self.assertIn("RUN OVERVIEW", inspector_text)
            self.assertIn("orchestrator", inspector_text)
            self.assertIn("SUCCESS", inspector_text)
            self.assertIn("3.45s", inspector_text)
            self.assertIn("trace-test-123", inspector_text)

    async def test_multi_run_response_and_evidence_isolation(self) -> None:
        """Verify selecting Run A displays only A, selecting Run B displays only B."""
        from unittest.mock import MagicMock
        from harness.tui.app import AgentHarnessApp
        from harness.tui.screens.runs import RunsScreen
        from textual.widgets import Markdown

        config = load_config("config/config.yaml")
        store = RuntimeStateStore()
        bus = LifecycleEventBus()
        controller = MagicMock()
        workspace = MagicMock()
        workspace.root_path = "/app/workspace"
        registry = MagicMock()
        registry.list_tools.return_value = []

        # Set up Run A
        run_a = self._create_run(
            run_id="run-A",
            agent_role="orchestrator",
            status="SUCCESS",
            duration_seconds=2.0,
            final_response="## Final Result A\nTask A finished with **success**.",
            tool_calls_map={
                "call-a": ToolCallNode(
                    call_id="call-a",
                    run_id="run-A",
                    tool_name="tool_alpha",
                    canonical_identity="builtin:tool_alpha",
                    tool_source=ToolSource.BUILTIN,
                    status="SUCCESS",
                    arguments_summary={"path": "alpha.txt"},
                )
            },
        )
        store.runs["run-A"] = run_a
        store.root_run_ids.append("run-A")

        # Set up Run B
        run_b = self._create_run(
            run_id="run-B",
            agent_role="orchestrator",
            status="SUCCESS",
            duration_seconds=3.0,
            final_response="## Final Result B\nTask B finished with `beta_code`.",
            tool_calls_map={
                "call-b": ToolCallNode(
                    call_id="call-b",
                    run_id="run-B",
                    tool_name="tool_beta",
                    canonical_identity="builtin:tool_beta",
                    tool_source=ToolSource.BUILTIN,
                    status="SUCCESS",
                    arguments_summary={"path": "beta.txt"},
                )
            },
        )
        store.runs["run-B"] = run_b
        store.root_run_ids.append("run-B")

        app = AgentHarnessApp(
            controller=controller,
            config=config,
            workspace=workspace,
            registry=registry,
            event_bus=bus,
            store=store,
        )

        async with app.run_test() as pilot:
            await pilot.press("2")
            runs_screen = app.query_one("#screen-runs", RunsScreen)

            # View Run A
            runs_screen.refresh_result("run-A")
            md_widget = runs_screen.query_one("#result-markdown", Markdown)
            self.assertEqual(md_widget.display, True)
            self.assertEqual(run_a.final_response, "## Final Result A\nTask A finished with **success**.")

            # View Run B
            runs_screen.refresh_result("run-B")
            self.assertEqual(md_widget.display, True)
            self.assertEqual(run_b.final_response, "## Final Result B\nTask B finished with `beta_code`.")

            # Switch back to Run A: isolated and restored
            runs_screen.refresh_result("run-A")
            self.assertEqual(md_widget.display, True)
            self.assertEqual(run_a.final_response, "## Final Result A\nTask A finished with **success**.")

    async def test_smart_autoscroll_and_unread_badge(self) -> None:
        """Verify unread event count appears when scrolled up and clears at bottom."""
        from unittest.mock import patch
        from harness.tui.screens.runs import RunsScreen
        from textual.widgets import Label, RichLog

        store = RuntimeStateStore()
        screen = RunsScreen(store)

        from textual.app import App, ComposeResult
        class ScrollTestApp(App):
            def compose(self) -> ComposeResult:
                yield screen

        test_app = ScrollTestApp()
        async with test_app.run_test() as pilot:
            log = screen.query_one("#timeline-log", RichLog)
            badge = screen.query_one("#timeline-scroll-badge", Label)

            # Add initial lines
            for i in range(10):
                store.timeline.append(
                    TimelineEvent(
                        timestamp=float(i),
                        relative_seconds=float(i),
                        run_id="run-1",
                        agent_role="orchestrator",
                        category="[STEP]",
                        title=f"Step {i}",
                        details="info",
                        icon="✓",
                    )
                )
            screen.refresh_timeline()
            self.assertEqual(str(badge.render()), "")

            # Simulate user scrolled up: scroll_y=0, max_scroll_y=15
            with patch.object(type(log), "scroll_y", 0), patch.object(type(log), "max_scroll_y", 15):
                # Emit 3 new events
                for i in range(10, 13):
                    store.timeline.append(
                        TimelineEvent(
                            timestamp=float(i),
                            relative_seconds=float(i),
                            run_id="run-1",
                            agent_role="orchestrator",
                            category="[STEP]",
                            title=f"Step {i}",
                            details="info",
                            icon="✓",
                        )
                    )
                screen.refresh_timeline()
                # Badge reflects 3 new events
                self.assertEqual(str(badge.render()), "↓ 3 new events")

            # Simulate user scrolled back to bottom: scroll_y=15, max_scroll_y=15
            with patch.object(type(log), "scroll_y", 15), patch.object(type(log), "max_scroll_y", 15):
                screen.refresh_timeline()
                # Badge cleared
                self.assertEqual(str(badge.render()), "")


class TestTUIObservabilityAndHighContrast(unittest.TestCase):
    """Tests for TUI accessibility, high-contrast theme styling, and Grafana / Tempo navigation."""

    def test_theme_semantic_high_contrast(self) -> None:
        """Verify semantic theme colors are bright, accessible, and do not use green for cursor focus."""
        from harness.tui import theme

        self.assertEqual(theme.TEXT_PRIMARY, "#F0F6FC")
        self.assertEqual(theme.TEXT_MUTED, "#B0B8C4")
        self.assertEqual(theme.TEXT_ACCENT, "#38BDF8")
        self.assertEqual(theme.SUCCESS, "#3FB950")
        self.assertEqual(theme.WARNING, "#F2CC60")
        self.assertEqual(theme.FAILURE, "#FF7B72")

        # DataTable cursor must use neutral slate/cyan, NOT green (reserved for success)
        self.assertIn("DataTable > .datatable--cursor", theme.APP_CSS)
        self.assertIn("background: #213547", theme.APP_CSS)
        cursor_block = theme.APP_CSS.split("DataTable > .datatable--cursor")[1].split("}")[0]
        self.assertNotIn("background: #238636", cursor_block)
        self.assertNotIn("background: #2EA043", cursor_block)
        self.assertNotIn("background: #3FB950", cursor_block)

        # Observable button styles
        self.assertIn(".obs-btn", theme.APP_CSS)

    def test_observability_urls(self) -> None:
        """Verify Grafana dashboard and Tempo trace URLs match real provisioned configurations."""
        import json
        import os
        import urllib.parse
        from unittest.mock import patch
        from harness.tui.observability import (
            build_tempo_trace_url,
            get_grafana_base_url,
            get_grafana_dashboard_url,
        )

        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(get_grafana_base_url(), "http://localhost:3000")
            dashboard_url = get_grafana_dashboard_url()
            self.assertEqual(
                dashboard_url,
                "http://localhost:3000/d/agent-harness-runtime/agent-harness-operations",
            )

        with patch.dict(os.environ, {"GRAFANA_HOST_URL": "http://grafana.internal:8080/"}):
            self.assertEqual(get_grafana_base_url(), "http://grafana.internal:8080")
            self.assertEqual(
                get_grafana_dashboard_url(),
                "http://grafana.internal:8080/d/agent-harness-runtime/agent-harness-operations",
            )

        # Test Tempo trace URL structure
        trace_url = build_tempo_trace_url("4bf92f3577b34da6a3ce929d0e0e4736")
        self.assertTrue(trace_url.startswith("http://localhost:3000/explore?left="))
        parsed = urllib.parse.urlparse(trace_url)
        params = urllib.parse.parse_qs(parsed.query)
        self.assertIn("left", params)
        payload = json.loads(params["left"][0])
        self.assertEqual(payload["datasource"], "tempo")
        self.assertEqual(len(payload["queries"]), 1)
        self.assertEqual(payload["queries"][0]["queryType"], "traceId")
        self.assertEqual(payload["queries"][0]["query"], "4bf92f3577b34da6a3ce929d0e0e4736")
        self.assertEqual(payload["queries"][0]["datasource"]["uid"], "tempo")

    def test_open_host_browser_best_effort(self) -> None:
        """Verify open_host_browser returns boolean truthfully and catches all exceptions."""
        from unittest.mock import patch
        from harness.tui.observability import open_host_browser

        with patch("webbrowser.open", return_value=True):
            self.assertTrue(open_host_browser("http://localhost:3000"))

        with patch("webbrowser.open", return_value=False):
            self.assertFalse(open_host_browser("http://localhost:3000"))

        with patch("webbrowser.open", side_effect=Exception("Display not available")):
            self.assertFalse(open_host_browser("http://localhost:3000"))

    def test_tempo_action_selected_run_trace_id(self) -> None:
        """Verify action_show_tempo resolves selected run's trace_id and invokes browser."""
        from unittest.mock import MagicMock, patch
        from harness.tui.app import AgentHarnessApp
        from harness.tui.models import RunNode

        app = AgentHarnessApp.__new__(AgentHarnessApp)
        app.store = RuntimeStateStore()
        app.notify = MagicMock()

        # Add a run with trace_id
        run_node = RunNode(
            run_id="run-test-tempo",
            trace_id="trace-xyz-987",
            parent_run_id=None,
            root_run_id="run-test-tempo",
            agent_id="orch-1",
            agent_role="orchestrator",
            is_root=True,
            status="SUCCESS",
        )
        app.store.runs["run-test-tempo"] = run_node
        app.store.root_run_ids.append("run-test-tempo")
        app.store.selected_run_id = "run-test-tempo"

        with patch("harness.tui.app.open_host_browser", return_value=True) as mock_open:
            app.action_show_tempo()
            mock_open.assert_called_once()
            called_url = mock_open.call_args[0][0]
            self.assertIn("trace-xyz-987", called_url)
            app.notify.assert_called_once()
            self.assertIn("Opened trace trace-xy...", app.notify.call_args[0][0])

    def test_tempo_action_missing_trace_id_handling(self) -> None:
        """Verify action_show_tempo warns gracefully when no trace ID is available."""
        from unittest.mock import MagicMock, patch
        from harness.tui.app import AgentHarnessApp

        app = AgentHarnessApp.__new__(AgentHarnessApp)
        app.store = RuntimeStateStore()
        app.notify = MagicMock()

        with patch("harness.tui.app.open_host_browser") as mock_open:
            app.action_show_tempo()
            mock_open.assert_not_called()
            app.notify.assert_called_once()
            self.assertEqual(app.notify.call_args[1].get("severity"), "warning")
            self.assertIn("No active or selected trace ID", app.notify.call_args[0][0])

    def test_evidence_table_symbols_and_high_contrast(self) -> None:
        """Verify timeline and evidence table render explicit symbols and accessible color tags."""
        from harness.tui.models import ToolCallNode, RunNode
        from harness.tui.screens.runs import RunsScreen

        screen = RunsScreen(RuntimeStateStore())

        # Test event formatting
        ev_step = TimelineEvent(
            timestamp=1.0,
            relative_seconds=0.5,
            run_id="r1",
            agent_role="orchestrator",
            category="[STEP]",
            icon="◆",
            title="Reasoning step",
            details="evaluating prompt",
        )
        rendered = screen._format_timeline_event(ev_step)
        self.assertIn("#B0B8C4", rendered)
        self.assertNotIn("[dim]", rendered)

        # Test tool call node in run evidence table
        t_node = ToolCallNode(
            call_id="call-1",
            run_id="r1",
            tool_name="write_file",
            canonical_identity="builtin:filesystem:write_file",
            permission_decision=PermissionDecision.REQUIRE_CONFIRMATION,
            risk_level=RiskLevel.MUTATING,
            confirmation_outcome="APPROVED",
            status="SUCCESS",
        )
        run_node = RunNode(
            run_id="r1",
            trace_id="t1",
            parent_run_id=None,
            root_run_id="r1",
            agent_id="orch-1",
            agent_role="orchestrator",
            is_root=True,
            tool_calls_map={"call-1": t_node},
        )
        table = screen._build_evidence_table(run_node)
        self.assertEqual(len(table.rows), 1)


class TestFullLabJourneyShowcase(unittest.TestCase):
    """Authoritative test suite for the End-to-End Agent Runtime Journey showcase."""

    def test_full_journey_scenario_specification(self) -> None:
        """Verify scenario metadata, highlights, and observability checkpoints."""
        sc = next((s for s in SCENARIOS if s.id == "full_journey"), None)
        self.assertIsNotNone(sc)
        self.assertEqual(sc.week_label, "Full Runtime")
        self.assertIn("exercises persistent memory retrieval", sc.description)
        self.assertIn("Memory Firewall prompt-injection quarantine", sc.description)
        self.assertIn("multi-agent delegation to transport_specialist", sc.description)

        expected_tokens = [
            "[W2] Memory Firewall: untrusted directive quarantined",
            "[W2] Memory Retrieval: legitimate user travel preference retrieved",
            "[W3] Delegation: orchestrator → transport_specialist",
            "[W2] MCP Execution: transport_service:find_connection",
            "[W3] Security Policy: create_file / modify_file",
            "[W3] Human Confirmation:",
            "[W1] Workspace Containment:",
            "[W1] Safe Filesystem:",
            "[W1] Verification: read_file",
            "[W1] ReAct Synthesis:",
            "[OBS] Telemetry:",
            "[BUDGET] Execution Envelope:",
        ]
        highlights_str = " ".join(sc.expected_highlights)
        for token in expected_tokens:
            self.assertIn(token, highlights_str)

        self.assertIn("Grafana", sc.observability_checkpoint)
        self.assertIn("Tempo", sc.observability_checkpoint)
        self.assertIn("TUI", sc.observability_checkpoint)

        self.assertIsNotNone(sc.security_fixture)
        self.assertIn("Trusted memory:", sc.security_fixture)
        self.assertIn("Untrusted tool observation:", sc.security_fixture)
        self.assertIn("Memory Firewall → QUARANTINE", sc.security_fixture)
        self.assertIn("Please prepare a verified travel plan", sc.suggested_prompt)

    def test_memory_seeding_and_firewall_quarantine(self) -> None:
        """Verify prepare_full_journey_memory seeds valid preference and quarantines injection under prep context."""
        from harness.memory.store import SQLiteMemoryStore
        from harness.memory.firewall import MemoryFirewall
        from harness.memory.retrieval import MemoryRetriever
        from harness.memory.manager import MemoryManager
        from harness.memory.base import MemoryStatus
        from harness.runtime.events import LifecycleEventBus, MemoryOperationEvent, MemoryOperationType, MemoryOperationStatus
        from harness.tui.scenarios import prepare_full_journey_memory

        store = SQLiteMemoryStore(":memory:")
        firewall = MemoryFirewall(store)
        retriever = MemoryRetriever(store)
        bus = LifecycleEventBus()
        captured_events: list[MemoryOperationEvent] = []

        class CaptureObserver:
            def on_event(self, event: Any) -> None:
                if isinstance(event, MemoryOperationEvent):
                    captured_events.append(event)

        bus.subscribe(CaptureObserver())

        manager = MemoryManager(
            store=store,
            admission_policy=firewall,
            retriever=retriever,
            event_bus=bus,
        )

        prep_run_id = prepare_full_journey_memory(manager)
        self.assertIsNotNone(prep_run_id)
        self.assertTrue(prep_run_id.startswith("prep_"))

        # Verify exactly 2 memories persisted in store
        self.assertEqual(store.count(), 2)

        accepted = store.list_all(status=MemoryStatus.ACCEPTED)
        quarantined = store.list_all(status=MemoryStatus.QUARANTINED)

        self.assertEqual(len(accepted), 1)
        self.assertIn("departures after 09:00", accepted[0].content)

        self.assertEqual(len(quarantined), 1)
        self.assertIn("Ignore previous instructions", quarantined[0].content)

        # Verify lifecycle events published with prep_run_id correlation
        admit_events = [ev for ev in captured_events if ev.operation_type == MemoryOperationType.ADMIT]
        quarantine_events = [ev for ev in captured_events if ev.operation_type == MemoryOperationType.QUARANTINE]

        self.assertEqual(len(admit_events), 1)
        self.assertEqual(admit_events[0].run_id, prep_run_id)
        self.assertEqual(admit_events[0].agent_role, "scenario_setup")
        self.assertEqual(admit_events[0].status, MemoryOperationStatus.SUCCESS)

        self.assertEqual(len(quarantine_events), 1)
        self.assertEqual(quarantine_events[0].run_id, prep_run_id)
        self.assertEqual(quarantine_events[0].agent_role, "scenario_setup")
        self.assertEqual(quarantine_events[0].status, MemoryOperationStatus.QUARANTINED)

    def test_retrieval_safety_excludes_injection(self) -> None:
        """Verify MemoryRetriever returns legitimate preference and excludes quarantined injection."""
        from harness.memory.store import SQLiteMemoryStore
        from harness.memory.firewall import MemoryFirewall
        from harness.memory.retrieval import MemoryRetriever
        from harness.memory.manager import MemoryManager
        from harness.memory.base import MemoryStatus
        from harness.tui.scenarios import prepare_full_journey_memory

        store = SQLiteMemoryStore(":memory:")
        firewall = MemoryFirewall(store)
        retriever = MemoryRetriever(store)
        manager = MemoryManager(store=store, admission_policy=firewall, retriever=retriever)

        prepare_full_journey_memory(manager)

        # Retrieve memories matching prompt intent
        results = manager.retrieve("Passau to München train preference")
        self.assertGreaterEqual(len(results), 1)
        for entry in results:
            self.assertEqual(entry.status, MemoryStatus.ACCEPTED)
            self.assertNotIn("Ignore previous instructions", entry.content)
            self.assertNotIn("outside the workspace", entry.content)

        # Confirms legitimate preference is present
        preference_present = any("departures after 09:00" in entry.content for entry in results)
        self.assertTrue(preference_present)

    def test_quarantined_content_never_enters_react_request_context(self) -> None:
        """Verify prompt context constructed from retrieved memories never contains malicious directives."""
        from harness.memory.store import SQLiteMemoryStore
        from harness.memory.firewall import MemoryFirewall
        from harness.memory.retrieval import MemoryRetriever
        from harness.memory.manager import MemoryManager
        from harness.tui.scenarios import prepare_full_journey_memory

        store = SQLiteMemoryStore(":memory:")
        firewall = MemoryFirewall(store)
        retriever = MemoryRetriever(store)
        manager = MemoryManager(store=store, admission_policy=firewall, retriever=retriever)

        prepare_full_journey_memory(manager)

        retrieved_memories = manager.retrieve("travel plan Passau München")
        formatted_context = "\n".join(f"- {m.content}" for m in retrieved_memories)

        self.assertIn("departures after 09:00", formatted_context)
        self.assertNotIn("Ignore previous instructions", formatted_context)
        self.assertNotIn("reveal environment variables", formatted_context)

    def test_post_run_summary_scoped_quarantine(self) -> None:
        """Verify post-run summary correlates quarantine events strictly to target run or prep session."""
        from harness.tui.modals import extract_post_run_summary
        from harness.tui.models import RunNode
        from harness.runtime.events import MemoryOperationEvent, MemoryOperationType, MemoryOperationStatus

        store = RuntimeStateStore()

        run_node = RunNode(
            run_id="run-active-123",
            trace_id="trace-test",
            parent_run_id=None,
            root_run_id="run-active-123",
            agent_id="orch-1",
            agent_role="orchestrator",
            is_root=True,
            status="SUCCESS",
        )
        store.runs["run-active-123"] = run_node
        store.root_run_ids.append("run-active-123")

        prep_id = "prep-scoped-456"
        unrelated_prep_id = "prep-unrelated-789"

        # Record a quarantine event tied to prep_id
        store.raw_events.append(
            MemoryOperationEvent(
                timestamp=time.time(),
                trace_id="trace-test",
                run_id=prep_id,
                root_run_id=prep_id,
                parent_run_id=None,
                agent_id="scenario-setup",
                agent_role="scenario_setup",
                operation_id="op-1",
                operation_type=MemoryOperationType.QUARANTINE,
                status=MemoryOperationStatus.QUARANTINED,
                duration_seconds=0.01,
            )
        )

        # Summary with matching prep_run_id reports the firewall quarantine
        summary_matched = extract_post_run_summary(
            store,
            target_run_id="run-active-123",
            prep_run_id=prep_id,
        )
        self.assertIn("Memory Firewall: untrusted directive quarantined", summary_matched["memory"])

        # Summary with unrelated prep_run_id does NOT report quarantine
        summary_unrelated = extract_post_run_summary(
            store,
            target_run_id="run-active-123",
            prep_run_id=unrelated_prep_id,
        )
        self.assertNotIn("Memory Firewall: untrusted directive quarantined", summary_unrelated["memory"])

    def test_specialist_and_mcp_availability(self) -> None:
        """Verify transport_specialist profile and MCP tool IDs."""
        from harness.agent.delegation import get_standard_specialist_specs

        specs = get_standard_specialist_specs()
        self.assertIn("transport_specialist", specs)
        transport_spec = specs["transport_specialist"]
        self.assertEqual(transport_spec.role, "transport_specialist")
        self.assertIn("mcp:transport_service:find_connection", transport_spec.allowed_tool_ids)

    def test_governance_rules_for_showcase(self) -> None:
        """Verify permission policy evaluation for travel_plan.md and delegation."""
        from dataclasses import replace
        from harness.permissions.manager import PermissionManager
        from harness.permissions.base import PermissionDecision, RiskLevel, PermissionRequest
        from harness.permissions.risk import RiskClassifier

        pm = PermissionManager.create_default()

        # Filesystem write/create requires operator confirmation
        req_create = PermissionRequest(
            run_id="r1",
            agent_id="orch",
            agent_role="orchestrator",
            delegation_depth=0,
            call_id="c1",
            canonical_tool_identity="builtin:filesystem:create_file",
            tool_name="create_file",
            tool_source=ToolSource.BUILTIN,
            server_name=None,
            arguments={"path": "travel_plan.md", "content": "# Travel Plan"},
            arguments_fingerprint="fp1",
            arguments_summary={"path": "travel_plan.md"},
        )
        dec_create, _, risk_create = pm.policy_engine.evaluate(
            replace(req_create, risk_level=RiskClassifier.classify(req_create))
        )
        self.assertEqual(dec_create, PermissionDecision.REQUIRE_CONFIRMATION)
        self.assertEqual(risk_create, RiskLevel.MUTATING)

        # Filesystem modify requires operator confirmation
        req_modify = PermissionRequest(
            run_id="r1",
            agent_id="orch",
            agent_role="orchestrator",
            delegation_depth=0,
            call_id="c2",
            canonical_tool_identity="builtin:filesystem:modify_file",
            tool_name="modify_file",
            tool_source=ToolSource.BUILTIN,
            server_name=None,
            arguments={"path": "travel_plan.md", "content": "# Updated Plan"},
            arguments_fingerprint="fp2",
            arguments_summary={"path": "travel_plan.md"},
        )
        dec_modify, _, risk_modify = pm.policy_engine.evaluate(
            replace(req_modify, risk_level=RiskClassifier.classify(req_modify))
        )
        self.assertEqual(dec_modify, PermissionDecision.REQUIRE_CONFIRMATION)
        self.assertEqual(risk_modify, RiskLevel.MUTATING)

        # Filesystem read is allowed without human confirmation
        req_read = PermissionRequest(
            run_id="r1",
            agent_id="orch",
            agent_role="orchestrator",
            delegation_depth=0,
            call_id="c3",
            canonical_tool_identity="builtin:filesystem:read_file",
            tool_name="read_file",
            tool_source=ToolSource.BUILTIN,
            server_name=None,
            arguments={"path": "travel_plan.md"},
            arguments_fingerprint="fp3",
            arguments_summary={"path": "travel_plan.md"},
        )
        dec_read, _, risk_read = pm.policy_engine.evaluate(
            replace(req_read, risk_level=RiskClassifier.classify(req_read))
        )
        self.assertEqual(dec_read, PermissionDecision.ALLOW)
        self.assertEqual(risk_read, RiskLevel.READ_ONLY)

        # MCP find_connection is allowed
        req_mcp = PermissionRequest(
            run_id="r2",
            agent_id="spec",
            agent_role="transport_specialist",
            delegation_depth=1,
            call_id="c4",
            canonical_tool_identity="mcp:transport_service:find_connection",
            tool_name="find_connection",
            tool_source=ToolSource.MCP,
            server_name="transport_service",
            arguments={"origin": "Passau Hbf", "destination": "München Hbf"},
            arguments_fingerprint="fp4",
            arguments_summary={"origin": "Passau Hbf", "destination": "München Hbf"},
        )
        dec_mcp, _, _ = pm.policy_engine.evaluate(
            replace(req_mcp, risk_level=RiskClassifier.classify(req_mcp))
        )
        self.assertEqual(dec_mcp, PermissionDecision.ALLOW)

    def test_workspace_containment_for_showcase(self) -> None:
        """Verify Workspace.resolve safely bounds travel_plan.md and blocks directory traversal."""
        from pathlib import Path
        import tempfile
        from harness.tools.workspace import Workspace, WorkspaceBoundaryError

        with tempfile.TemporaryDirectory() as tmp_dir:
            ws = Workspace(root=tmp_dir)

            # Valid workspace file resolves inside root
            resolved_plan = ws.resolve("travel_plan.md")
            self.assertEqual(resolved_plan, Path(tmp_dir).resolve() / "travel_plan.md")
            self.assertTrue(str(resolved_plan).startswith(str(Path(tmp_dir).resolve())))

            # Directory traversal outside root is rejected
            with self.assertRaises(WorkspaceBoundaryError):
                ws.resolve("../../etc/passwd")

    def test_workspace_fixture_initialization_and_repeat_safety(self) -> None:
        """Verify prepare_full_journey_workspace initializes stale draft and safely resets across repeated runs."""
        from pathlib import Path
        from harness.tui.scenarios import (
            STALE_TRAVEL_PLAN_CONTENT,
            prepare_full_journey_workspace,
        )
        import tempfile

        with tempfile.TemporaryDirectory() as tmp_dir:
            ws_root = Path(tmp_dir)

            # Create an unrelated user file in the workspace
            unrelated_file = ws_root / "project_notes.txt"
            unrelated_file.write_text("Important user notes", encoding="utf-8")

            # 1. First scenario preparation
            target = prepare_full_journey_workspace(ws_root)
            self.assertIsNotNone(target)
            self.assertTrue(target.is_file())
            self.assertEqual(target.name, "travel_plan.md")
            self.assertEqual(target.read_text(encoding="utf-8"), STALE_TRAVEL_PLAN_CONTENT)

            # Unrelated file must be untouched
            self.assertEqual(unrelated_file.read_text(encoding="utf-8"), "Important user notes")

            # Simulate agent mutating travel_plan.md during run
            target.write_text("# Verified Itinerary: RE 3 Passau -> Munich\n", encoding="utf-8")
            self.assertNotEqual(target.read_text(encoding="utf-8"), STALE_TRAVEL_PLAN_CONTENT)

            # 2. Repeated scenario preparation resets only travel_plan.md
            target_reset = prepare_full_journey_workspace(ws_root)
            self.assertEqual(target_reset.read_text(encoding="utf-8"), STALE_TRAVEL_PLAN_CONTENT)
            self.assertEqual(unrelated_file.read_text(encoding="utf-8"), "Important user notes")

    def test_permission_governance_on_stale_fixture(self) -> None:
        """Verify scenario preparation does not bypass PermissionManager; modify_file requires confirmation."""
        from pathlib import Path
        from harness.permissions.manager import PermissionManager
        from harness.permissions.base import PermissionDecision, PermissionRequest, RiskLevel
        from harness.permissions.risk import RiskClassifier
        from harness.tools.base import ToolSource
        from harness.tui.scenarios import prepare_full_journey_workspace
        from dataclasses import replace
        import tempfile

        with tempfile.TemporaryDirectory() as tmp_dir:
            ws_root = Path(tmp_dir)
            target = prepare_full_journey_workspace(ws_root)
            self.assertTrue(target.is_file())

            pm = PermissionManager.create_default()

            # modify_file against existing travel_plan.md MUST require confirmation
            req = PermissionRequest(
                run_id="run_test",
                agent_id="orch",
                agent_role="orchestrator",
                delegation_depth=0,
                call_id="call_mod",
                canonical_tool_identity="builtin:filesystem:modify_file",
                tool_name="modify_file",
                tool_source=ToolSource.BUILTIN,
                server_name=None,
                arguments={"path": "travel_plan.md", "old_str": "Outdated", "new_str": "Verified"},
                arguments_fingerprint="fp_mod",
                arguments_summary={"path": "travel_plan.md"},
            )
            decision, _, risk_level = pm.policy_engine.evaluate(
                replace(req, risk_level=RiskClassifier.classify(req))
            )
            self.assertEqual(decision, PermissionDecision.REQUIRE_CONFIRMATION)
            self.assertEqual(risk_level, RiskLevel.MUTATING)


if __name__ == "__main__":
    unittest.main()

