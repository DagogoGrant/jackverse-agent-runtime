"""Integrated End-to-End Acceptance Test for Week 3 Governance & Observability.

Covers simultaneously in a single deterministic automated scenario:
1. Sub-agent delegation (orchestrator -> workspace_analyst specialist)
2. Permission enforcement (closed-default policy, ALLOW, REQUIRE_CONFIRMATION, single-use token consumption)
3. Concrete tool execution (read_file by specialist, create_file by orchestrator with workspace side effects)
4. Comprehensive observability (Prometheus metric counters, OpenTelemetry span hierarchy, structured JSON logs)
"""

from __future__ import annotations

import json
from pathlib import Path
import tempfile
from typing import Any, Mapping
import unittest
from unittest.mock import MagicMock

from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from prometheus_client import CollectorRegistry

from harness.agent.budget import ExecutionBudget, TerminationReason
from harness.agent.delegation import (
    AgentFactory,
    AgentSpec,
    DelegateTaskTool,
    HierarchicalBudgetLedger,
    MemoryAccessLevel,
    SubAgentManager,
    build_tool_catalog,
    get_standard_specialist_specs,
)
from harness.agent.react import ReActController
from harness.observability.logging import StructuredLogObserver
from harness.observability.metrics import PrometheusObserver
from harness.observability.tracing import OpenTelemetryObserver
from harness.permissions import (
    ConfirmationGrant,
    ConfirmationRecord,
    DeterministicConfirmationHandler,
    PermissionDecision,
    PermissionManager,
    PermissionRequest,
    PolicyEngine,
    PolicyRule,
    RiskLevel,
)
from harness.runtime.context import ExecutionContext
from harness.runtime.events import (
    ConfirmationResolvedEvent,
    DelegationFinishedEvent,
    DelegationStartedEvent,
    LifecycleEventBus,
    PermissionEvaluatedEvent,
    RunFinishedEvent,
    RunStartedEvent,
    ToolCallFinishedEvent,
    ToolCallStartedEvent,
)
from harness.tools.base import ToolSource
from harness.tools.executor import ToolExecutor
from harness.tools.filesystem import CreateFileTool, ListDirectoryTool, ReadFileTool, SearchFilesTool
from harness.tools.registry import ToolRegistry
from harness.tools.workspace import Workspace


class TestGovernanceIntegratedE2E(unittest.TestCase):
    """Deterministic, headless E2E validating complete harness governance and observability."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.workspace_root = Path(self.temp_dir.name).resolve()
        self.workspace = Workspace(self.workspace_root)

        # Pre-seed an initial input file in workspace for the child specialist to read
        self.input_file = self.workspace_root / "task_input.txt"
        self.input_file.write_text("Source Data: Passau-Munich Rail Corridor Review 2026", encoding="utf-8")

        # The target file that should be created only after confirmation
        self.output_file = self.workspace_root / "final_report.txt"

        # 1. Observability setup with isolated registries
        self.metric_registry = CollectorRegistry(auto_describe=True)
        self.prom_observer = PrometheusObserver(registry=self.metric_registry)

        self.span_exporter = InMemorySpanExporter()
        self.tracer_provider = TracerProvider(resource=Resource.create({"service.name": "agent-harness-e2e"}))
        self.tracer_provider.add_span_processor(SimpleSpanProcessor(self.span_exporter))
        self.otel_observer = OpenTelemetryObserver(
            service_name="agent-harness-e2e",
            tracer_provider=self.tracer_provider,
        )

        self.structured_logs: list[str] = []
        self.log_observer = StructuredLogObserver(destination=self.structured_logs.append)

        self.bus = LifecycleEventBus()
        self.bus.subscribe(self.prom_observer)
        self.bus.subscribe(self.otel_observer)
        self.bus.subscribe(self.log_observer)

        # 2. Filesystem tools
        self.read_tool = ReadFileTool(self.workspace)
        self.create_tool = CreateFileTool(self.workspace)
        self.list_tool = ListDirectoryTool(self.workspace)
        self.search_tool = SearchFilesTool(self.workspace)
        self.tool_catalog = build_tool_catalog([self.read_tool, self.create_tool, self.list_tool, self.search_tool])

        # 3. Permissions setup: closed-default stance with explicit role rules
        self.policy = PolicyEngine(
            rules=[
                # Orchestrator is allowed to delegate tasks
                PolicyRule(
                    name="allow_orchestrator_delegation",
                    decision=PermissionDecision.ALLOW,
                    tool_pattern="delegate_task",
                    role="orchestrator",
                    risk_level=RiskLevel.READ_ONLY,
                ),
                # workspace_analyst is allowed to read workspace files
                PolicyRule(
                    name="allow_analyst_read",
                    decision=PermissionDecision.ALLOW,
                    tool_pattern="builtin:filesystem:read_file",
                    role="workspace_analyst",
                    risk_level=RiskLevel.READ_ONLY,
                ),
                # Orchestrator requires human confirmation to write files
                PolicyRule(
                    name="require_confirmation_file_write",
                    decision=PermissionDecision.REQUIRE_CONFIRMATION,
                    tool_pattern="builtin:filesystem:create_file",
                    role="orchestrator",
                    risk_level=RiskLevel.MUTATING,
                ),
            ],
            default_stance=PermissionDecision.DENY,
        )

        self.confirmation_handler = DeterministicConfirmationHandler(always_allow=True)
        self.permission_manager = PermissionManager(
            policy_engine=self.policy,
            confirmation_handler=self.confirmation_handler,
            event_bus=self.bus,
            workspace=self.workspace,
        )

        # 4. Budget & Delegation setup
        self.root_budget = ExecutionBudget(
            max_steps=6,
            max_tool_calls=6,
            max_runtime_seconds=60.0,
            max_observation_chars=8000,
        )
        self.ledger = HierarchicalBudgetLedger(root_budget=self.root_budget, max_delegation_depth=2, max_delegations=3)

        self.mock_llm = MagicMock()
        self.specialist_specs = get_standard_specialist_specs()

        self.factory = AgentFactory(
            specs=self.specialist_specs,
            tool_catalog=self.tool_catalog,
            llm_client=self.mock_llm,
            permission_manager=self.permission_manager,
            event_bus=self.bus,
        )

        self.subagent_manager = SubAgentManager(
            agent_factory=self.factory,
            budget_ledger=self.ledger,
            event_bus=self.bus,
        )

        self.delegate_tool = DelegateTaskTool(self.subagent_manager)

        # 5. Parent Orchestrator controller setup
        self.parent_registry = ToolRegistry()
        self.parent_registry.register(self.delegate_tool)
        self.parent_registry.register(self.create_tool)

        self.parent_executor = ToolExecutor(
            max_observation_chars=self.root_budget.max_observation_chars,
            event_bus=self.bus,
            permission_manager=self.permission_manager,
        )

        self.orchestrator = ReActController(
            llm_client=self.mock_llm,
            tool_registry=self.parent_registry,
            tool_executor=self.parent_executor,
            budget=self.root_budget,
            event_bus=self.bus,
            agent_id="orchestrator-root",
            agent_role="orchestrator",
            budget_ledger=self.ledger,
        )

    def tearDown(self) -> None:
        self.otel_observer.shutdown()
        self.temp_dir.cleanup()

    def test_integrated_delegation_permissions_execution_and_observability(self) -> None:
        """Run the complete integrated Week 3 governance scenario."""

        # Verify initial filesystem state: output report MUST NOT exist yet
        self.assertFalse(self.output_file.exists(), "Target file must not exist before execution")

        # Track instantiated children and captured confirmations
        created_specialists: list[ReActController] = []
        orig_create_agent = self.factory.create_agent

        def probe_create_agent(role: str, child_ctx: ExecutionContext, budget: ExecutionBudget) -> ReActController:
            child = orig_create_agent(role, child_ctx, budget)
            created_specialists.append(child)
            return child

        self.factory.create_agent = probe_create_agent

        # Program deterministic LLM sequence:
        # Step 1 (Parent - orchestrator): delegates task to workspace_analyst
        call_delegate = MagicMock()
        call_delegate.id = "call-del-101"
        call_delegate.name = "delegate_task"
        call_delegate.arguments = {
            "specialist": "workspace_analyst",
            "task": "Read task_input.txt and summarize the corridor name",
        }
        resp_p1 = MagicMock()
        resp_p1.tool_calls = [call_delegate]
        resp_p1.content = "Delegating workspace inspection to specialist."

        # Step 2 (Child - workspace_analyst turn 1): child invokes permitted read_file
        call_read = MagicMock()
        call_read.id = "call-read-201"
        call_read.name = "read_file"
        call_read.arguments = {"path": "task_input.txt"}
        resp_c1 = MagicMock()
        resp_c1.tool_calls = [call_read]
        resp_c1.content = "Reading workspace task input."

        # Step 3 (Child - workspace_analyst turn 2): child synthesizes final answer
        resp_c2 = MagicMock()
        resp_c2.tool_calls = []
        resp_c2.content = "Passau-Munich Rail Corridor verified active."

        # Step 4 (Parent - orchestrator turn 2): parent invokes confirmation-gated create_file
        call_create = MagicMock()
        call_create.id = "call-create-301"
        call_create.name = "create_file"
        call_create.arguments = {
            "path": "final_report.txt",
            "content": "GOVERNANCE REPORT: Passau-Munich Rail Corridor verified active.",
        }
        resp_p2 = MagicMock()
        resp_p2.tool_calls = [call_create]
        resp_p2.content = "Child completed analysis. Generating final report file."

        # Step 5 (Parent - orchestrator turn 3): parent returns final answer
        resp_p3 = MagicMock()
        resp_p3.tool_calls = []
        resp_p3.content = "Workflow completed: Final governance report successfully written."

        call_sequence = [resp_p1, resp_c1, resp_c2, resp_p2, resp_p3]
        chat_calls_recorded: list[list[dict[str, Any]]] = []

        def mock_chat(messages: Any, tools: Any = None) -> Any:
            chat_calls_recorded.append([dict(m) for m in messages])
            if not call_sequence:
                raise RuntimeError("Exhausted programmed mock LLM responses")
            return call_sequence.pop(0)

        self.mock_llm.chat.side_effect = mock_chat

        try:
            # -----------------------------------------------------------------
            # EXECUTE END-TO-END SCENARIO
            # -----------------------------------------------------------------
            result = self.orchestrator.run_turn("Review rail corridor and save report")

            # -----------------------------------------------------------------
            # 1. ASSERTIONS: OUTCOME & TOOL EXECUTION SIDE-EFFECTS
            # -----------------------------------------------------------------
            self.assertTrue(result.is_success, f"Orchestrator run must succeed. Error: {result.error}")
            self.assertEqual(result.termination_reason, TerminationReason.FINAL_ANSWER)
            self.assertIn("Final governance report successfully written", result.final_text or "")

            # Verify side effect: file was physically created with exact expected text
            self.assertTrue(self.output_file.exists(), "Target file must exist after authorized tool execution")
            self.assertEqual(
                self.output_file.read_text(encoding="utf-8"),
                "GOVERNANCE REPORT: Passau-Munich Rail Corridor verified active.",
            )

            # -----------------------------------------------------------------
            # 2. ASSERTIONS: SUB-AGENT DELEGATION GOVERNANCE
            # -----------------------------------------------------------------
            # Child instance was created with expected specialist role
            self.assertEqual(len(created_specialists), 1, "Exactly one child specialist must be created")
            child_controller = created_specialists[0]
            self.assertEqual(child_controller.agent_role, "workspace_analyst")
            self.assertTrue(child_controller.agent_id.startswith("workspace_analyst-"))

            # Sliced budget was allocated and reconciled
            self.assertGreater(self.ledger.total_steps_consumed, 0)
            self.assertGreater(self.ledger.total_tool_calls_consumed, 0)
            self.assertEqual(self.ledger.delegations_count, 1)

            # -----------------------------------------------------------------
            # 3. ASSERTIONS: PERMISSION ENFORCEMENT & CONFIRMATION
            # -----------------------------------------------------------------
            # Confirmation handler was consulted exactly once (for create_file)
            self.assertEqual(len(self.confirmation_handler.requests), 1)
            confirmed_req = self.confirmation_handler.requests[0]
            self.assertEqual(confirmed_req.tool_name, "create_file")
            self.assertEqual(confirmed_req.agent_role, "orchestrator")
            self.assertEqual(confirmed_req.resource_descriptor, str(self.output_file))

            # Confirmation record was single-use: token must now be consumed and invalid for reuse
            # Find the confirmation record
            conf_records = list(self.permission_manager._confirmations.values())
            self.assertEqual(len(conf_records), 1)
            record = conf_records[0]
            self.assertTrue(record.consumed, "Confirmation token must be consumed after tool execution")
            self.assertFalse(
                self.permission_manager.verify_confirmation_token(record.confirmation_id, confirmed_req),
                "Confirmation token cannot be verified/reused once consumed",
            )

            # Direct attempt to execute unauthorized mutating tool without permission fails closed
            unauthorized_req = PermissionRequest(
                run_id="unauth-run",
                agent_id="hacker-agent",
                agent_role="untrusted_role",
                delegation_depth=0,
                call_id="call-unauth",
                canonical_tool_identity="builtin:filesystem:create_file",
                tool_name="create_file",
                tool_source=ToolSource.BUILTIN,
                server_name="filesystem",
                arguments={"path": "unauthorized.txt", "content": "bad"},
                arguments_fingerprint="dummy_fp",
                arguments_summary={"path": "unauthorized.txt"},
            )
            unauth_auth = self.permission_manager.authorize(unauthorized_req)
            self.assertEqual(unauth_auth.decision, PermissionDecision.DENY)

            # -----------------------------------------------------------------
            # 4. ASSERTIONS: OBSERVABILITY - PROMETHEUS METRICS
            # -----------------------------------------------------------------
            # Both orchestrator and child specialist run completions recorded
            orch_runs = self.metric_registry.get_sample_value(
                "harness_agent_runs_total",
                {"agent_role": "orchestrator", "status": "success", "failure_category": "none"},
            )
            self.assertEqual(orch_runs, 1.0, "Orchestrator run completion must be 1")

            child_runs = self.metric_registry.get_sample_value(
                "harness_agent_runs_total",
                {"agent_role": "workspace_analyst", "status": "success", "failure_category": "none"},
            )
            self.assertEqual(child_runs, 1.0, "Child specialist run completion must be 1")

            # Delegation counter incremented
            delegations = self.metric_registry.get_sample_value(
                "harness_delegations_total",
                {"parent_role": "orchestrator", "child_role": "workspace_analyst", "status": "success"},
            )
            self.assertEqual(delegations, 1.0, "Delegation counter must be 1")

            # Concrete tool executions counted
            del_tool_calls = self.metric_registry.get_sample_value(
                "harness_tool_calls_total",
                {"tool_name": "delegate_task", "tool_source": "builtin", "status": "success", "failure_category": "none"},
            )
            self.assertEqual(del_tool_calls, 1.0)

            read_tool_calls = self.metric_registry.get_sample_value(
                "harness_tool_calls_total",
                {"tool_name": "read_file", "tool_source": "builtin", "status": "success", "failure_category": "none"},
            )
            self.assertEqual(read_tool_calls, 1.0)

            create_tool_calls = self.metric_registry.get_sample_value(
                "harness_tool_calls_total",
                {"tool_name": "create_file", "tool_source": "builtin", "status": "success", "failure_category": "none"},
            )
            self.assertEqual(create_tool_calls, 1.0)

            # Permission decisions and confirmations counted
            # 1. ALLOW decisions: 2 evaluated (delegate_task by orchestrator, read_file by workspace_analyst)
            allow_decisions = self.metric_registry.get_sample_value(
                "harness_permission_decisions_total",
                {"decision": "allow", "tool_source": "builtin", "risk_level": "read_only"},
            )
            self.assertEqual(allow_decisions, 2.0, "Expected exactly 2 ALLOW decisions (delegate_task and read_file)")

            # 2. REQUIRE_CONFIRMATION decisions: 1 evaluated (create_file by orchestrator)
            confirm_decisions = self.metric_registry.get_sample_value(
                "harness_permission_decisions_total",
                {"decision": "require_confirmation", "tool_source": "builtin", "risk_level": "mutating"},
            )
            self.assertEqual(confirm_decisions, 1.0, "Expected exactly 1 REQUIRE_CONFIRMATION decision (create_file)")

            confirm_count = self.metric_registry.get_sample_value(
                "harness_permission_confirmations_total",
                {"status": "approved"},
            )
            self.assertEqual(confirm_count, 1.0)

            # -----------------------------------------------------------------
            # 5. ASSERTIONS: OBSERVABILITY - OPENTELEMETRY TRACE HIERARCHY
            # -----------------------------------------------------------------
            spans = self.span_exporter.get_finished_spans()
            self.assertGreater(len(spans), 4, "Must have spans for runs and tool executions")

            # All spans share the EXACT SAME distributed trace_id
            trace_ids = {span.context.trace_id for span in spans}
            self.assertEqual(len(trace_ids), 1, f"Expected 1 trace_id across all spans, got: {trace_ids}")

            # Identify spans
            orch_run_spans = [s for s in spans if s.name == "agent.run" and s.attributes.get("agent.role") == "orchestrator"]
            child_run_spans = [s for s in spans if s.name == "agent.run" and s.attributes.get("agent.role") == "workspace_analyst"]
            delegate_tool_spans = [s for s in spans if s.name == "tool.execute" and s.attributes.get("tool.name") == "delegate_task"]
            read_tool_spans = [s for s in spans if s.name == "tool.execute" and s.attributes.get("tool.name") == "read_file"]
            create_tool_spans = [s for s in spans if s.name == "tool.execute" and s.attributes.get("tool.name") == "create_file"]

            self.assertEqual(len(orch_run_spans), 1)
            self.assertEqual(len(child_run_spans), 1)
            self.assertEqual(len(delegate_tool_spans), 1)
            self.assertEqual(len(read_tool_spans), 1)
            self.assertEqual(len(create_tool_spans), 1)

            orch_span = orch_run_spans[0]
            child_span = child_run_spans[0]
            del_span = delegate_tool_spans[0]
            read_span = read_tool_spans[0]
            create_span = create_tool_spans[0]

            # Trace Hierarchy Assertions:
            # 1. delegate_task tool span is child of orchestrator run span
            self.assertEqual(del_span.parent.span_id, orch_span.context.span_id)
            # 2. child specialist run span is child of delegate_task tool span
            self.assertEqual(child_span.parent.span_id, del_span.context.span_id)
            # 3. child read_file tool span is child of child specialist run span
            self.assertEqual(read_span.parent.span_id, child_span.context.span_id)
            # 4. parent create_file tool span is child of orchestrator run span
            self.assertEqual(create_span.parent.span_id, orch_span.context.span_id)

            # -----------------------------------------------------------------
            # 6. ASSERTIONS: OBSERVABILITY - STRUCTURED LOG CORRELATION
            # -----------------------------------------------------------------
            self.assertGreater(len(self.structured_logs), 0)
            parsed_logs = [json.loads(line) for line in self.structured_logs]

            # Verify structured event names exist
            event_types = {entry.get("event") for entry in parsed_logs}
            self.assertIn("run.started", event_types)
            self.assertIn("run.finished", event_types)
            self.assertIn("tool.started", event_types)
            self.assertIn("tool.finished", event_types)
            self.assertIn("delegation.started", event_types)
            self.assertIn("delegation.finished", event_types)
            self.assertIn("permission.evaluated", event_types)
            self.assertIn("permission.confirmation_resolved", event_types)

            # Verify privacy safety: prompt and secret text must not leak into log metadata
            for entry in parsed_logs:
                log_dump = json.dumps(entry)
                self.assertNotIn("Passau-Munich Rail Corridor Review", log_dump)
                # Sensitive argument values are excluded/sanitized
                self.assertNotIn("Audit Report", log_dump)

        finally:
            self.factory.create_agent = orig_create_agent


if __name__ == "__main__":
    unittest.main()
