"""Unit tests for Phase W3.8 Sub-Agent Delegation and Multi-Principal Architecture."""

from __future__ import annotations

import json
from typing import Any, Mapping
import unittest
from unittest.mock import MagicMock

from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from prometheus_client import CollectorRegistry

from harness.agent.budget import ExecutionBudget, RunResult, TerminationReason
from harness.agent.delegation import (
    AgentFactory,
    AgentSpec,
    DelegateTaskTool,
    DelegationRequest,
    DelegationResult,
    MemoryAccessLevel,
    SubAgentManager,
    build_tool_catalog,
    get_standard_specialist_specs,
)
from harness.agent.ledger import (
    BudgetExhaustedError,
    DelegationLimitExceededError,
    HierarchicalBudgetLedger,
)
from harness.agent.react import ReActController
from harness.observability.logging import StructuredLogObserver
from harness.observability.metrics import PrometheusObserver
from harness.observability.tracing import OpenTelemetryObserver
from harness.permissions.base import (
    AuthorizationResult,
    PermissionDecision,
    PermissionRequest,
    canonical_tool_identity,
)
from harness.permissions.confirmation import DeterministicConfirmationHandler
from harness.permissions.manager import PermissionManager
from harness.permissions.policy import PolicyEngine, PolicyRule
from harness.runtime.context import (
    ExecutionContext,
    call_id_scope,
    execution_context_scope,
    get_current_call_id,
    get_current_context,
)
from harness.runtime.events import (
    DelegationFinishedEvent,
    DelegationStartedEvent,
    EventObserver,
    FailureCategory,
    LifecycleEvent,
    LifecycleEventBus,
    RunStartedEvent,
    ToolCallStartedEvent,
)
from harness.tools.base import ErrorCode, Tool, ToolResult, ToolSource, ToolSpec
from harness.tools.executor import ToolExecutor
from harness.tools.registry import ToolRegistry


class DummyTool:
    """Simple test tool implementing Tool protocol."""

    def __init__(
        self,
        name: str,
        source: ToolSource = ToolSource.BUILTIN,
        server_name: str | None = None,
        is_mutating: bool = False,
    ) -> None:
        self._spec = ToolSpec(
            name=name,
            description=f"Test tool {name}",
            input_schema={"type": "object", "properties": {"query": {"type": "string"}}},
            source=source,
            server_name=server_name,
            is_mutating=is_mutating,
        )
        self.invocations: list[Mapping[str, object]] = []

    @property
    def spec(self) -> ToolSpec:
        return self._spec

    def execute(self, arguments: Mapping[str, object]) -> ToolResult:
        self.invocations.append(arguments)
        return ToolResult(content=f"Executed {self.spec.name} with {arguments}")


class RecordingObserver(EventObserver):
    def __init__(self) -> None:
        self.events: list[LifecycleEvent] = []

    def on_event(self, event: LifecycleEvent) -> None:
        self.events.append(event)


class TestHierarchicalBudgetLedger(unittest.TestCase):
    """Unit tests verifying HierarchicalBudgetLedger capacity bounds and anti-multiplication invariants."""

    def setUp(self) -> None:
        self.root_budget = ExecutionBudget(
            max_steps=10,
            max_tool_calls=15,
            max_runtime_seconds=60.0,
            max_observation_chars=16000,
        )
        self.ledger = HierarchicalBudgetLedger(
            root_budget=self.root_budget,
            max_delegation_depth=2,
            max_delegations=3,
        )

    def test_allocate_child_slice_bounded_by_remaining_capacity(self) -> None:
        requested = ExecutionBudget(max_steps=5, max_tool_calls=5, max_runtime_seconds=30.0)
        child_slice = self.ledger.allocate_child_slice(requested, current_depth=0)

        self.assertEqual(child_slice.max_steps, 5)
        self.assertEqual(child_slice.max_tool_calls, 5)
        self.assertLessEqual(child_slice.max_runtime_seconds, 60.0)
        self.assertEqual(self.ledger.delegations_count, 1)

    def test_allocate_child_slice_cannot_exceed_remaining_capacity(self) -> None:
        # Parent consumed 8 of 10 steps
        self.ledger.record_parent_consumption(steps=8, tool_calls=12)
        self.assertEqual(self.ledger.remaining_steps, 2)
        self.assertEqual(self.ledger.remaining_tool_calls, 3)

        requested = ExecutionBudget(max_steps=5, max_tool_calls=5, max_runtime_seconds=30.0)
        child_slice = self.ledger.allocate_child_slice(requested, current_depth=0)

        # Child slice must be clamped to remaining (2 steps, 3 tool calls)
        self.assertEqual(child_slice.max_steps, 2)
        self.assertEqual(child_slice.max_tool_calls, 3)

    def test_max_delegation_depth_rejected(self) -> None:
        requested = ExecutionBudget(max_steps=3, max_tool_calls=3)
        # Depth 2 is at max_delegation_depth (2)
        with self.assertRaises(DelegationLimitExceededError):
            self.ledger.allocate_child_slice(requested, current_depth=2)

    def test_max_delegations_fan_out_rejected(self) -> None:
        requested = ExecutionBudget(max_steps=2, max_tool_calls=2)
        self.ledger.allocate_child_slice(requested, current_depth=0)
        self.ledger.allocate_child_slice(requested, current_depth=0)
        self.ledger.allocate_child_slice(requested, current_depth=0)

        # 4th delegation exceeds max_delegations=3
        with self.assertRaises(DelegationLimitExceededError):
            self.ledger.allocate_child_slice(requested, current_depth=0)

    def test_exhausted_steps_rejected(self) -> None:
        self.ledger.record_parent_consumption(steps=10)
        requested = ExecutionBudget(max_steps=2, max_tool_calls=2)
        with self.assertRaises(BudgetExhaustedError):
            self.ledger.allocate_child_slice(requested, current_depth=0)

    def test_reconcile_child_consumption_deducts_accurately(self) -> None:
        self.ledger.record_parent_consumption(steps=2, tool_calls=1)
        requested = ExecutionBudget(max_steps=5, max_tool_calls=5)
        _ = self.ledger.allocate_child_slice(requested, current_depth=0)

        # Child used 3 steps and 2 tool calls
        self.ledger.reconcile_child_consumption(actual_steps=3, actual_tool_calls=2)

        self.assertEqual(self.ledger.remaining_steps, 5)  # 10 - 2 - 3 = 5
        self.assertEqual(self.ledger.remaining_tool_calls, 12)  # 15 - 1 - 2 = 12
        self.assertEqual(self.ledger.total_steps_consumed, 5)
        self.assertEqual(self.ledger.total_tool_calls_consumed, 3)


class TestSubAgentManager(unittest.TestCase):
    """Unit tests for SubAgentManager governance, ContextVar isolation, and telemetry privacy."""

    def setUp(self) -> None:
        self.bus = LifecycleEventBus()
        self.observer = RecordingObserver()
        self.bus.subscribe(self.observer)

        self.root_budget = ExecutionBudget(max_steps=10, max_tool_calls=10, max_runtime_seconds=60.0)
        self.ledger = HierarchicalBudgetLedger(root_budget=self.root_budget, max_delegation_depth=2, max_delegations=5)

        # Create tools
        self.tool_find = DummyTool("find_connection", source=ToolSource.MCP, server_name="transport_service")
        self.tool_station = DummyTool("get_station_info", source=ToolSource.MCP, server_name="transport_service")
        self.tool_read = DummyTool("read_file", source=ToolSource.BUILTIN)
        self.catalog = build_tool_catalog([self.tool_find, self.tool_station, self.tool_read])

        # Allow policies
        policy = PolicyEngine(
            rules=[
                PolicyRule(name="allow_transport", decision=PermissionDecision.ALLOW, tool_pattern="mcp:transport_service:*"),
                PolicyRule(name="allow_read", decision=PermissionDecision.ALLOW, tool_pattern="read_file"),
                PolicyRule(name="allow_delegate", decision=PermissionDecision.ALLOW, tool_pattern="delegate_task"),
            ],
            default_stance="deny",
        )
        self.permission_manager = PermissionManager(policy_engine=policy, confirmation_handler=DeterministicConfirmationHandler(always_allow=True))

        # Mock LLM client
        self.mock_llm = MagicMock()
        mock_resp = MagicMock()
        mock_resp.tool_calls = []
        mock_resp.content = "Specialist completed analysis."
        self.mock_llm.chat.return_value = mock_resp

        self.specs = get_standard_specialist_specs()
        self.factory = AgentFactory(
            specs=self.specs,
            tool_catalog=self.catalog,
            llm_client=self.mock_llm,
            permission_manager=self.permission_manager,
            event_bus=self.bus,
        )
        self.manager = SubAgentManager(
            agent_factory=self.factory,
            budget_ledger=self.ledger,
            event_bus=self.bus,
        )

    def test_call_id_contextvar_isolated_during_child_execution(self) -> None:
        parent_ctx = ExecutionContext.create_root(budget=self.root_budget, agent_role="orchestrator")
        observed_child_call_id: list[str | None] = []

        def capture_call_id(*args: Any, **kwargs: Any) -> Any:
            observed_child_call_id.append(get_current_call_id())
            resp = MagicMock()
            resp.tool_calls = []
            resp.content = "Done"
            return resp

        self.mock_llm.chat.side_effect = capture_call_id

        # Parent runs inside active call_id_scope
        with call_id_scope("parent-call-uuid-999"):
            self.assertEqual(get_current_call_id(), "parent-call-uuid-999")
            res = self.manager.delegate(
                parent_ctx=parent_ctx,
                request=DelegationRequest(specialist_role="transport_specialist", task="Lookup route"),
            )
            # After delegation, parent's call_id must be restored
            self.assertEqual(get_current_call_id(), "parent-call-uuid-999")

        self.assertTrue(res.success)
        # Child saw call_id as None (not parent's call ID)
        self.assertEqual(observed_child_call_id, [None])

    def test_strict_current_call_id_isolation_lifecycle(self) -> None:
        """Prove strict ContextVar isolation across full parent/child execution tree:
        - inside parent DelegateTaskTool -> parent call ID visible
        - inside child controller before any child tool call -> get_current_call_id() is None
        - inside child's own ToolExecutor -> child's call ID visible
        - after child returns -> parent's delegate_task call ID restored
        """
        parent_ctx = ExecutionContext.create_root(budget=self.root_budget, agent_role="orchestrator")
        lifecycle_observations: list[tuple[str, str | None]] = []
        child_call_id_val = "child-tool-call-uuid-888"

        def child_tool_exec(arguments: Mapping[str, object]) -> ToolResult:
            lifecycle_observations.append(("inside_child_tool_executor", get_current_call_id()))
            return ToolResult(content="Child tool executed successfully")

        orig_find_execute = self.tool_find.execute
        self.tool_find.execute = child_tool_exec

        try:
            # Child LLM:
            # Step 1: verify get_current_call_id() is None before child tool execution, returns tool call
            def child_llm_step1(*args: Any, **kwargs: Any) -> Any:
                lifecycle_observations.append(("inside_child_controller_pre_tool", get_current_call_id()))
                call_child = MagicMock()
                call_child.id = child_call_id_val
                call_child.name = "find_connection"
                call_child.arguments = {"origin": "Passau", "destination": "München"}
                resp = MagicMock()
                resp.tool_calls = [call_child]
                resp.content = None
                return resp

            # Step 2: final answer
            def child_llm_step2(*args: Any, **kwargs: Any) -> Any:
                resp = MagicMock()
                resp.tool_calls = []
                resp.content = "Child specialist completed work."
                return resp

            step_count = 0

            def chat_dispatcher(*args: Any, **kwargs: Any) -> Any:
                nonlocal step_count
                step_count += 1
                if step_count == 1:
                    return child_llm_step1(*args, **kwargs)
                return child_llm_step2(*args, **kwargs)

            self.mock_llm.chat.side_effect = chat_dispatcher

            delegate_tool = DelegateTaskTool(self.manager)
            orig_delegate_exec = delegate_tool.execute

            def wrapped_delegate_exec(arguments: Mapping[str, object]) -> ToolResult:
                lifecycle_observations.append(("inside_parent_delegate_task_tool_pre", get_current_call_id()))
                res = orig_delegate_exec(arguments)
                lifecycle_observations.append(("inside_parent_delegate_task_tool_post", get_current_call_id()))
                return res

            delegate_tool.execute = wrapped_delegate_exec

            parent_executor = ToolExecutor(
                max_observation_chars=1000,
                event_bus=self.bus,
                permission_manager=self.permission_manager,
            )

            parent_delegate_call_id = "parent-delegate-call-uuid-777"
            with execution_context_scope(parent_ctx):
                parent_result = parent_executor.execute(
                    delegate_tool,
                    {"specialist": "transport_specialist", "task": "Lookup train"},
                    call_id=parent_delegate_call_id,
                )

            self.assertFalse(parent_result.is_error)
            self.assertEqual(
                lifecycle_observations,
                [
                    ("inside_parent_delegate_task_tool_pre", parent_delegate_call_id),
                    ("inside_child_controller_pre_tool", None),
                    ("inside_child_tool_executor", child_call_id_val),
                    ("inside_parent_delegate_task_tool_post", parent_delegate_call_id),
                ],
            )
        finally:
            self.tool_find.execute = orig_find_execute

    def test_active_parent_budget_accounting_boundary(self) -> None:
        """Prove active-parent budget accounting:
        Root max_tool_calls=2, max_steps=2.
        Parent's first tool call is delegate_task.
        Child spec asks for >= 2 tool calls and >= 2 steps (ceiling of 5).
        Child must receive at most 1 tool call and at most 1 step.
        """
        root_budget = ExecutionBudget(max_steps=2, max_tool_calls=2, max_runtime_seconds=60.0)
        ledger = HierarchicalBudgetLedger(root_budget=root_budget, max_delegation_depth=2, max_delegations=5)

        child_allocated_budget: list[ExecutionBudget] = []
        orig_create_agent = self.factory.create_agent

        def probe_create_agent(role: str, child_ctx: ExecutionContext, budget: ExecutionBudget) -> ReActController:
            child_allocated_budget.append(budget)
            return orig_create_agent(role, child_ctx, budget)

        self.factory.create_agent = probe_create_agent
        try:
            manager = SubAgentManager(
                agent_factory=self.factory,
                budget_ledger=ledger,
                event_bus=self.bus,
            )
            delegate_tool = DelegateTaskTool(manager)
            parent_registry = ToolRegistry()
            parent_registry.register(delegate_tool)
            parent_executor = ToolExecutor(
                max_observation_chars=1000,
                event_bus=self.bus,
                permission_manager=self.permission_manager,
            )
            parent_controller = ReActController(
                llm_client=self.mock_llm,
                tool_registry=parent_registry,
                tool_executor=parent_executor,
                budget=root_budget,
                event_bus=self.bus,
                agent_role="orchestrator",
                budget_ledger=ledger,
            )

            # Parent LLM turn 1: invokes delegate_task
            call_del = MagicMock()
            call_del.id = "parent-del-call"
            call_del.name = "delegate_task"
            call_del.arguments = {"specialist": "transport_specialist", "task": "Find connection"}
            resp_p1 = MagicMock()
            resp_p1.tool_calls = [call_del]
            resp_p1.content = None

            # Child LLM turn 1: final answer (consumes 1 step, 0 tool calls)
            resp_c = MagicMock()
            resp_c.tool_calls = []
            resp_c.content = "Child route answer"

            # Parent LLM turn 2: final answer (consumes 1 step)
            resp_p2 = MagicMock()
            resp_p2.tool_calls = []
            resp_p2.content = "Orchestrator finished"

            self.mock_llm.chat.side_effect = [resp_p1, resp_c, resp_p2]

            res = parent_controller.run_turn("Find train connection")
            self.assertEqual(res.termination_reason, TerminationReason.FINAL_ANSWER)

            # Child slice must have been capped at 1 tool call and 1 step because parent
            # already committed step 1 and active delegate_task tool call 1
            self.assertEqual(len(child_allocated_budget), 1)
            child_slice = child_allocated_budget[0]
            self.assertLessEqual(child_slice.max_tool_calls, 1, "Child must receive at most 1 tool call!")
            self.assertLessEqual(child_slice.max_steps, 1, "Child must receive at most 1 step!")

            # Ledger reconciliation:
            # Parent consumed 2 steps and 1 tool call. Child consumed 1 step and 0 tool calls.
            # Total tool calls consumed: 1 (parent delegate_task)
            self.assertEqual(ledger.total_tool_calls_consumed, 1)
            self.assertEqual(ledger.remaining_tool_calls, 1)
        finally:
            self.factory.create_agent = orig_create_agent

    def test_active_parent_budget_accounting_failed_child_reconciliation(self) -> None:
        """Prove failed children reconcile in finally without returning already-consumed parent capacity."""
        root_budget = ExecutionBudget(max_steps=3, max_tool_calls=3, max_runtime_seconds=60.0)
        ledger = HierarchicalBudgetLedger(root_budget=root_budget, max_delegation_depth=2, max_delegations=5)

        manager = SubAgentManager(
            agent_factory=self.factory,
            budget_ledger=ledger,
            event_bus=self.bus,
        )
        parent_ctx = ExecutionContext.create_root(budget=root_budget, agent_role="orchestrator")

        # Parent consumed 1 step and 1 tool call (active delegate_task call)
        ledger.record_parent_consumption(steps=1, tool_calls=1)

        # Child LLM fails with an exception
        self.mock_llm.chat.side_effect = RuntimeError("Child network error")

        with execution_context_scope(parent_ctx):
            res = manager.delegate(
                parent_ctx=parent_ctx,
                request=DelegationRequest(specialist_role="transport_specialist", task="Lookup train"),
            )

        self.assertFalse(res.success)
        # Verify parent's consumed capacity was not returned/refunded
        self.assertEqual(ledger.total_steps_consumed, 1)
        self.assertEqual(ledger.total_tool_calls_consumed, 1)
        self.assertEqual(ledger.remaining_steps, 2)
        self.assertEqual(ledger.remaining_tool_calls, 2)

    def test_child_context_propagation_and_depth(self) -> None:
        parent_ctx = ExecutionContext.create_root(budget=self.root_budget, agent_role="orchestrator")
        res = self.manager.delegate(
            parent_ctx=parent_ctx,
            request=DelegationRequest(specialist_role="transport_specialist", task="Check connection"),
        )
        self.assertTrue(res.success)

        # Check emitted events
        started = [e for e in self.observer.events if isinstance(e, DelegationStartedEvent)]
        finished = [e for e in self.observer.events if isinstance(e, DelegationFinishedEvent)]
        self.assertEqual(len(started), 1)
        self.assertEqual(len(finished), 1)

        start_ev = started[0]
        self.assertEqual(start_ev.trace_id, parent_ctx.trace_id)
        self.assertEqual(start_ev.run_id, parent_ctx.run_id)
        self.assertEqual(start_ev.parent_agent_id, parent_ctx.agent_id)
        self.assertNotEqual(start_ev.child_run_id, parent_ctx.run_id)
        self.assertEqual(start_ev.delegation_depth, 1)
        self.assertEqual(start_ev.child_agent_role, "transport_specialist")

    def test_strict_telemetry_privacy_zero_task_text_or_hash(self) -> None:
        parent_ctx = ExecutionContext.create_root(budget=self.root_budget, agent_role="orchestrator")
        secret_task = "Find train from Zurich to Geneva with secret code TOPSECRET123"

        res = self.manager.delegate(
            parent_ctx=parent_ctx,
            request=DelegationRequest(specialist_role="transport_specialist", task=secret_task),
        )
        self.assertTrue(res.success)

        for ev in self.observer.events:
            if isinstance(ev, (DelegationStartedEvent, DelegationFinishedEvent)):
                # Task text and substring MUST NOT appear
                self.assertNotIn("TOPSECRET123", str(ev.__dict__))
                self.assertNotIn("Zurich", str(ev.__dict__))
                # No hash field
                self.assertFalse(hasattr(ev, "task_hash"))
                self.assertFalse(hasattr(ev, "arguments_fingerprint"))
                # Length must match on DelegationStartedEvent
                if isinstance(ev, DelegationStartedEvent):
                    self.assertEqual(ev.task_length, len(secret_task))

    def test_specialist_least_privilege_registry_isolation(self) -> None:
        # transport_specialist must ONLY have transport tools, NOT read_file
        child_ctx = ExecutionContext.create_root(budget=self.root_budget, agent_role="transport_specialist")
        transport_agent = self.factory.create_agent("transport_specialist", child_ctx, self.root_budget)

        specs = [s.name for s in transport_agent.tool_registry.list_specs()]
        self.assertIn("find_connection", specs)
        self.assertIn("get_station_info", specs)
        self.assertNotIn("read_file", specs)
        self.assertNotIn("delegate_task", specs)

        # workspace_analyst must ONLY have read-only filesystem tools, NOT transport MCP tools
        analyst_ctx = ExecutionContext.create_root(budget=self.root_budget, agent_role="workspace_analyst")
        analyst_agent = self.factory.create_agent("workspace_analyst", analyst_ctx, self.root_budget)

        analyst_specs = [s.name for s in analyst_agent.tool_registry.list_specs()]
        self.assertIn("read_file", analyst_specs)
        self.assertNotIn("find_connection", analyst_specs)
        self.assertNotIn("modify_file", analyst_specs)

    def test_llm_conversational_context_isolation(self) -> None:
        """Prove LLM short-term conversational context isolation:
        - Parent conversation history contains sensitive sentinels and prior tool observations.
        - Child receives ONLY its own specialist system prompt and delegated task.
        - Child messages do NOT contain parent turns, prior observations, or sentinels.
        - Child context list is a distinct object from parent context.
        - Child completion does not mutate or delete existing parent history.
        - Parent receives only the delegation ToolResult through normal observation flow.
        """
        sentinel_history = "PARENT_SECRET_HISTORY_SENTINEL_XYZ_987"
        sentinel_tool_obs = "OBSERVATION_SECRET_PARENT_DATA_456"

        recorded_chat_calls: list[list[dict[str, Any]]] = []
        created_child_controllers: list[ReActController] = []

        orig_create_agent = self.factory.create_agent

        def probe_create_agent(role: str, child_ctx: ExecutionContext, budget: ExecutionBudget) -> ReActController:
            child = orig_create_agent(role, child_ctx, budget)
            created_child_controllers.append(child)
            return child

        self.factory.create_agent = probe_create_agent

        delegate_tool = DelegateTaskTool(self.manager)
        parent_registry = ToolRegistry()
        parent_registry.register(delegate_tool)

        parent_executor = ToolExecutor(
            max_observation_chars=2000,
            event_bus=self.bus,
            permission_manager=self.permission_manager,
        )

        parent_controller = ReActController(
            llm_client=self.mock_llm,
            tool_registry=parent_registry,
            tool_executor=parent_executor,
            budget=self.root_budget,
            event_bus=self.bus,
            agent_role="orchestrator",
            budget_ledger=self.ledger,
        )

        # Pre-populate parent history with sentinel turns and prior observations
        parent_controller.context.append({"role": "user", "content": f"Prior sensitive instruction: {sentinel_history}"})
        parent_controller.context.append({"role": "assistant", "content": "Acknowledged previous instruction."})
        parent_controller.context.append({"role": "tool", "tool_call_id": "call_old_1", "name": "dummy_read", "content": sentinel_tool_obs})
        prior_parent_context_len = len(parent_controller.context)

        # Mock LLM sequence:
        # 1. Parent step 1: delegates to workspace_analyst
        call_del = MagicMock()
        call_del.id = "del-call-context-iso"
        call_del.name = "delegate_task"
        call_del.arguments = {"specialist": "workspace_analyst", "task": "Inspect workspace directory structure"}
        resp_p1 = MagicMock()
        resp_p1.tool_calls = [call_del]
        resp_p1.content = None

        # 2. Child step 1: final answer
        resp_child = MagicMock()
        resp_child.tool_calls = []
        resp_child.content = "Workspace contains 4 safe files."

        # 3. Parent step 2: final answer
        resp_p2 = MagicMock()
        resp_p2.tool_calls = []
        resp_p2.content = "Orchestrator received analysis: Workspace contains 4 safe files."

        def chat_spy(messages: Any, tools: Any = None) -> Any:
            recorded_chat_calls.append([dict(m) for m in messages])
            if len(recorded_chat_calls) == 1:
                return resp_p1
            elif len(recorded_chat_calls) == 2:
                return resp_child
            return resp_p2

        self.mock_llm.chat.side_effect = chat_spy

        try:
            res = parent_controller.run_turn("Start orchestrator workflow")

            self.assertEqual(res.termination_reason, TerminationReason.FINAL_ANSWER)
            self.assertEqual(len(recorded_chat_calls), 3)

            # --- Check 1: Parent Call 1 contains prior history & sentinels ---
            parent_messages_call1 = recorded_chat_calls[0]
            parent_text_1 = str(parent_messages_call1)
            self.assertIn(sentinel_history, parent_text_1)
            self.assertIn(sentinel_tool_obs, parent_text_1)
            self.assertIn("Start orchestrator workflow", parent_text_1)

            # --- Check 2: Child Call contains ONLY child system prompt & delegated task ---
            child_messages = recorded_chat_calls[1]
            self.assertEqual(len(child_messages), 2, "Child LLM must receive exactly system prompt and user task")
            self.assertEqual(child_messages[0]["role"], "system")
            self.assertEqual(child_messages[0]["content"], self.specs["workspace_analyst"].system_prompt)
            self.assertEqual(child_messages[1]["role"], "user")
            self.assertEqual(child_messages[1]["content"], "Inspect workspace directory structure")

            # --- Check 3: ZERO parent turns, prior observations, or sentinels leak to child ---
            child_text = str(child_messages)
            self.assertNotIn(sentinel_history, child_text)
            self.assertNotIn(sentinel_tool_obs, child_text)
            self.assertNotIn("Prior sensitive instruction", child_text)
            self.assertNotIn("Start orchestrator workflow", child_text)
            self.assertNotIn("dummy_read", child_text)

            # --- Check 4: Context list identity separation ---
            self.assertEqual(len(created_child_controllers), 1)
            child_ctrl = created_child_controllers[0]
            self.assertIsNot(child_ctrl.context, parent_controller.context)

            # --- Check 5: Parent history preservation and mutation immunity ---
            # Prior items in parent context remain unchanged at their original indices
            self.assertEqual(parent_controller.context[1]["content"], f"Prior sensitive instruction: {sentinel_history}")
            self.assertEqual(parent_controller.context[3]["content"], sentinel_tool_obs)
            # Parent context was not cleared or replaced
            self.assertGreaterEqual(len(parent_controller.context), prior_parent_context_len + 2)

            # --- Check 6: Parent receives child result via standard ToolResult observation ---
            tool_obs_entries = [m for m in parent_controller.context if m.get("role") == "tool" and m.get("tool_call_id") == "del-call-context-iso"]
            self.assertEqual(len(tool_obs_entries), 1)
            self.assertEqual(tool_obs_entries[0]["content"], "Workspace contains 4 safe files.")
        finally:
            self.factory.create_agent = orig_create_agent


class TestOpenTelemetryTraceHierarchy(unittest.TestCase):
    """Unit tests verifying OpenTelemetry span hierarchy and parenting under delegation."""

    def setUp(self) -> None:
        self.exporter = InMemorySpanExporter()
        self.provider = TracerProvider(resource=Resource.create({"service.name": "agent-harness-test"}))
        self.provider.add_span_processor(SimpleSpanProcessor(self.exporter))
        self.observer = OpenTelemetryObserver(
            service_name="agent-harness-test",
            tracer_provider=self.provider,
        )

        self.bus = LifecycleEventBus()
        self.bus.subscribe(self.observer)

        self.root_budget = ExecutionBudget(max_steps=5, max_tool_calls=5, max_runtime_seconds=30.0)
        self.ledger = HierarchicalBudgetLedger(root_budget=self.root_budget)

        self.tool_find = DummyTool("find_connection", source=ToolSource.MCP, server_name="transport_service")
        self.catalog = build_tool_catalog([self.tool_find])

        policy = PolicyEngine(
            rules=[PolicyRule(name="allow_all", decision=PermissionDecision.ALLOW, tool_pattern="*")],
            default_stance="allow",
        )
        self.pm = PermissionManager(policy_engine=policy, confirmation_handler=DeterministicConfirmationHandler(always_allow=True))

        self.mock_llm = MagicMock()
        mock_resp = MagicMock()
        mock_resp.tool_calls = []
        mock_resp.content = "Child done"
        self.mock_llm.chat.return_value = mock_resp

        self.specs = get_standard_specialist_specs()
        self.factory = AgentFactory(
            specs=self.specs,
            tool_catalog=self.catalog,
            llm_client=self.mock_llm,
            permission_manager=self.pm,
            event_bus=self.bus,
        )
        self.manager = SubAgentManager(
            agent_factory=self.factory,
            budget_ledger=self.ledger,
            event_bus=self.bus,
        )

    def tearDown(self) -> None:
        self.observer.shutdown()

    def test_child_agent_span_parented_to_delegate_task_tool_span(self) -> None:
        delegate_tool = DelegateTaskTool(self.manager)
        parent_registry = ToolRegistry()
        parent_registry.register(delegate_tool)

        parent_executor = ToolExecutor(
            max_observation_chars=16000,
            event_bus=self.bus,
            permission_manager=self.pm,
        )

        parent_controller = ReActController(
            llm_client=self.mock_llm,
            tool_registry=parent_registry,
            tool_executor=parent_executor,
            budget=self.root_budget,
            event_bus=self.bus,
            agent_role="orchestrator",
            budget_ledger=self.ledger,
        )

        # First LLM call returns tool call to delegate_task
        call_delegate = MagicMock()
        call_delegate.id = "call-delegate-1"
        call_delegate.name = "delegate_task"
        call_delegate.arguments = {"specialist": "transport_specialist", "task": "Lookup train"}

        resp_turn1 = MagicMock()
        resp_turn1.tool_calls = [call_delegate]
        resp_turn1.content = None

        resp_turn2 = MagicMock()
        resp_turn2.tool_calls = []
        resp_turn2.content = "Orchestrator received child result and finished."

        resp_child = MagicMock()
        resp_child.tool_calls = []
        resp_child.content = "Train arrives at 14:00."

        # Sequence of LLM calls:
        # 1. Orchestrator turn 1 -> call delegate_task
        # 2. Child specialist turn 1 -> final text
        # 3. Orchestrator turn 2 -> final text
        self.mock_llm.chat.side_effect = [resp_turn1, resp_child, resp_turn2]

        res = parent_controller.run_turn("Find me a train")
        self.assertEqual(res.termination_reason, TerminationReason.FINAL_ANSWER)

        spans = self.exporter.get_finished_spans()
        self.assertGreater(len(spans), 0)

        # Verify trace structure
        # All spans must share the EXACT SAME trace ID
        trace_ids = {span.context.trace_id for span in spans}
        self.assertEqual(len(trace_ids), 1, f"Expected 1 trace ID across all spans, got: {trace_ids}")

        agent_run_spans = [s for s in spans if s.name == "agent.run"]
        self.assertEqual(len(agent_run_spans), 2)  # Parent run span + Child run span

        # Identify parent vs child run span
        parent_run_span = [s for s in agent_run_spans if s.attributes.get("agent.role") == "orchestrator"][0]
        child_run_span = [s for s in agent_run_spans if s.attributes.get("agent.role") == "transport_specialist"][0]

        delegate_tool_span = [s for s in spans if s.name == "tool.execute" and s.attributes.get("tool.name") == "delegate_task"][0]

        # Invariant 7: Child's agent.run span parent is the delegate_task tool span!
        self.assertEqual(child_run_span.parent.span_id, delegate_tool_span.context.span_id)
        # Parent's delegate_task tool span parent is the parent's agent.run span!
        self.assertEqual(delegate_tool_span.parent.span_id, parent_run_span.context.span_id)

        # Gate 4: Verify correlation mapping is cleanly emptied after successful delegation
        self.assertEqual(len(self.observer._delegation_parent_spans), 0)

    def test_delegation_opentelemetry_correlation_cleaned_on_child_failure(self) -> None:
        """Verify _delegation_parent_spans is cleanly emptied even if child execution fails."""
        parent_run_id = "parent-run-fail"
        child_run_id = "child-run-fail"
        parent_call_id = "parent-call-fail"

        # Parent run and tool start
        self.bus.publish(RunStartedEvent(
            timestamp=1.0, trace_id="trace-fail", run_id=parent_run_id,
            root_run_id=parent_run_id, parent_run_id=None, agent_id="orch-1",
            agent_role="orchestrator", max_steps=5, max_tool_calls=5, max_runtime_seconds=60.0,
        ))
        self.bus.publish(ToolCallStartedEvent(
            timestamp=1.1, trace_id="trace-fail", run_id=parent_run_id,
            root_run_id=parent_run_id, parent_run_id=None, agent_id="orch-1",
            agent_role="orchestrator", tool_name="delegate_task", call_id=parent_call_id,
        ))
        self.bus.publish(DelegationStartedEvent(
            timestamp=1.2, trace_id="trace-fail", run_id=parent_run_id,
            root_run_id=parent_run_id, parent_run_id=None, agent_id="orch-1",
            agent_role="orchestrator", delegation_id="del-fail", parent_call_id=parent_call_id,
            child_run_id=child_run_id, parent_agent_id="orch-1", parent_agent_role="orchestrator",
            child_agent_id="trans-fail", child_agent_role="transport_specialist",
            delegation_depth=1, task_length=15,
        ))

        # Correlation mapping holds parent span
        self.assertEqual(len(self.observer._delegation_parent_spans), 1)

        # Child fails before starting run or during run -> DelegationFinishedEvent fires with error status
        self.bus.publish(DelegationFinishedEvent(
            timestamp=1.3, trace_id="trace-fail", run_id=parent_run_id,
            root_run_id=parent_run_id, parent_run_id=None, agent_id="orch-1",
            agent_role="orchestrator", delegation_id="del-fail", parent_call_id=parent_call_id,
            child_run_id=child_run_id, parent_agent_id="orch-1", parent_agent_role="orchestrator",
            child_agent_id="trans-fail", child_agent_role="transport_specialist",
            delegation_depth=1, duration_seconds=0.05, status="error", steps=0, tool_calls=0,
            failure_category=FailureCategory.INTERNAL,
        ))

        # Correlation map MUST be empty after failure
        self.assertEqual(len(self.observer._delegation_parent_spans), 0)


class TestObservabilityMetricsAndLogging(unittest.TestCase):
    """Unit tests for Prometheus delegation metrics and structured logging."""

    def test_prometheus_delegation_metrics(self) -> None:
        reg = CollectorRegistry()
        obs = PrometheusObserver(registry=reg)

        ev = DelegationFinishedEvent(
            timestamp=1.0,
            trace_id="t1",
            run_id="r1",
            root_run_id="r1",
            parent_run_id=None,
            agent_id="a1",
            agent_role="orchestrator",
            delegation_id="d1",
            parent_call_id="c1",
            child_run_id="cr1",
            parent_agent_id="a1",
            parent_agent_role="orchestrator",
            child_agent_id="ca1",
            child_agent_role="transport_specialist",
            delegation_depth=1,
            duration_seconds=1.23,
            status="success",
            steps=2,
            tool_calls=1,
        )

        obs.on_event(ev)

        val = reg.get_sample_value(
            "harness_delegations_total",
            {"parent_role": "orchestrator", "child_role": "transport_specialist", "status": "success"},
        )
        self.assertEqual(val, 1.0)

        # Verify histogram recorded sample
        count = reg.get_sample_value(
            "harness_delegation_duration_seconds_count",
            {"child_role": "transport_specialist"},
        )
        self.assertEqual(count, 1.0)

    def test_structured_logging_delegation(self) -> None:
        logged_lines: list[str] = []
        log_obs = StructuredLogObserver(destination=logged_lines.append)

        ev_start = DelegationStartedEvent(
            timestamp=1.0,
            trace_id="t1",
            run_id="r1",
            root_run_id="r1",
            parent_run_id=None,
            agent_id="a1",
            agent_role="orchestrator",
            delegation_id="d1",
            parent_call_id="c1",
            child_run_id="cr1",
            parent_agent_id="a1",
            parent_agent_role="orchestrator",
            child_agent_id="ca1",
            child_agent_role="transport_specialist",
            delegation_depth=1,
            task_length=42,
        )
        log_obs.on_event(ev_start)

        ev_finish = DelegationFinishedEvent(
            timestamp=2.0,
            trace_id="t1",
            run_id="r1",
            root_run_id="r1",
            parent_run_id=None,
            agent_id="a1",
            agent_role="orchestrator",
            delegation_id="d1",
            parent_call_id="c1",
            child_run_id="cr1",
            parent_agent_id="a1",
            parent_agent_role="orchestrator",
            child_agent_id="ca1",
            child_agent_role="transport_specialist",
            delegation_depth=1,
            duration_seconds=1.0,
            status="success",
            steps=2,
            tool_calls=1,
        )
        log_obs.on_event(ev_finish)

        self.assertEqual(len(logged_lines), 2)
        start_rec = json.loads(logged_lines[0])
        self.assertEqual(start_rec["event"], "delegation.started")
        self.assertEqual(start_rec["child_agent_role"], "transport_specialist")
        self.assertEqual(start_rec["task_length"], 42)

        finish_rec = json.loads(logged_lines[1])
        self.assertEqual(finish_rec["event"], "delegation.finished")
        self.assertEqual(finish_rec["status"], "success")
        self.assertEqual(finish_rec["steps"], 2)


class TestLongLivedControllerBudgetLifecycle(unittest.TestCase):
    """Deterministic regression tests proving long-lived controller budget refresh across turns."""

    def setUp(self) -> None:
        self.bus = LifecycleEventBus()
        policy = PolicyEngine(
            rules=[
                PolicyRule(name="allow_transport", decision=PermissionDecision.ALLOW, tool_pattern="mcp:transport_service:*"),
                PolicyRule(name="allow_delegate", decision=PermissionDecision.ALLOW, tool_pattern="delegate_task"),
            ],
            default_stance="deny",
        )
        self.permission_manager = PermissionManager(
            policy_engine=policy,
            confirmation_handler=DeterministicConfirmationHandler(always_allow=True),
        )
        self.mock_llm = MagicMock()
        self.tool_find = DummyTool("find_connection", source=ToolSource.MCP, server_name="transport_service")
        self.catalog = build_tool_catalog([self.tool_find])
        self.specs = get_standard_specialist_specs()
        self.factory = AgentFactory(
            specs=self.specs,
            tool_catalog=self.catalog,
            llm_client=self.mock_llm,
            permission_manager=self.permission_manager,
            event_bus=self.bus,
        )

    def test_long_lived_controller_refreshes_root_budget_across_turns(self) -> None:
        """Prove that a long-lived controller/runtime:
        1. Executes Turn 1 (consuming root steps/tool calls).
        2. Simulates passage of time beyond Turn 1's root deadline without sleeping.
        3. Starts Turn 2 with fresh root budget.
        4. Turn 2 successfully delegates to child without 'deadline expired' error.
        5. Child still receives only a slice bounded by Turn 2's root capacity.
        """
        current_simulated_time = 1000.0

        def fake_clock() -> float:
            return current_simulated_time

        root_budget = ExecutionBudget(max_steps=5, max_tool_calls=5, max_runtime_seconds=60.0)

        manager = SubAgentManager(
            agent_factory=self.factory,
            budget_ledger=None,  # Dynamic per-turn ledger
            event_bus=self.bus,
        )
        delegate_tool = DelegateTaskTool(manager)
        registry = ToolRegistry()
        registry.register(delegate_tool)
        executor = ToolExecutor(
            max_observation_chars=1000,
            event_bus=self.bus,
            permission_manager=self.permission_manager,
        )

        controller = ReActController(
            llm_client=self.mock_llm,
            tool_registry=registry,
            tool_executor=executor,
            budget=root_budget,
            event_bus=self.bus,
            agent_role="orchestrator",
            budget_ledger=None,  # Fresh per-turn ledger
            max_delegation_depth=2,
            max_delegations=5,
            clock=fake_clock,
        )

        # TURN 1: Orchestrator answers directly without delegation
        resp_turn1 = MagicMock()
        resp_turn1.tool_calls = []
        resp_turn1.content = "Turn 1 answer"
        self.mock_llm.chat.return_value = resp_turn1

        res1 = controller.run_turn("Hello Turn 1")
        self.assertEqual(res1.termination_reason, TerminationReason.FINAL_ANSWER)
        self.assertEqual(res1.final_text, "Turn 1 answer")
        self.assertEqual(res1.steps, 1)

        # SIMULATE PASSAGE OF TIME: Advance clock by 10,000 seconds (well past 60s root deadline)
        current_simulated_time += 10000.0

        # TURN 2: Orchestrator delegates to specialist
        child_allocated_budget: list[ExecutionBudget] = []
        orig_create_agent = self.factory.create_agent

        def probe_create_agent(role: str, child_ctx: ExecutionContext, budget: ExecutionBudget) -> ReActController:
            child_allocated_budget.append(budget)
            return orig_create_agent(role, child_ctx, budget)

        self.factory.create_agent = probe_create_agent
        try:
            # Turn 2 LLM calls:
            # Call 1: Parent invokes delegate_task
            call_del = MagicMock()
            call_del.id = "parent-del-call-t2"
            call_del.name = "delegate_task"
            call_del.arguments = {"specialist": "transport_specialist", "task": "Find connection"}
            resp_p1 = MagicMock()
            resp_p1.tool_calls = [call_del]
            resp_p1.content = None

            # Call 2: Child final answer
            resp_c = MagicMock()
            resp_c.tool_calls = []
            resp_c.content = "Child connection found"

            # Call 3: Parent final answer
            resp_p2 = MagicMock()
            resp_p2.tool_calls = []
            resp_p2.content = "Turn 2 completed with delegation"

            self.mock_llm.chat.side_effect = [resp_p1, resp_c, resp_p2]

            res2 = controller.run_turn("Hello Turn 2 (delegate)")
            self.assertEqual(res2.termination_reason, TerminationReason.FINAL_ANSWER)
            self.assertEqual(res2.final_text, "Turn 2 completed with delegation")

            # Verify child was created and received fresh slice from Turn 2's root budget
            self.assertEqual(len(child_allocated_budget), 1)
            child_slice = child_allocated_budget[0]
            # Turn 2 parent used 1 step and 1 tool call prior to delegation -> remaining: 4 steps, 4 tool calls
            self.assertLessEqual(child_slice.max_steps, 4)
            self.assertLessEqual(child_slice.max_tool_calls, 4)
            self.assertGreaterEqual(child_slice.max_runtime_seconds, 50.0)
        finally:
            self.factory.create_agent = orig_create_agent


class TestRejectedDelegationObservability(unittest.TestCase):
    """Deterministic tests proving terminal observability events and Prometheus metrics for rejected delegations."""

    def setUp(self) -> None:
        self.bus = LifecycleEventBus()
        self.collector = CollectorRegistry()
        self.prom_obs = PrometheusObserver(registry=self.collector)
        self.bus.subscribe(self.prom_obs)

        policy = PolicyEngine(
            rules=[
                PolicyRule(name="allow_transport", decision=PermissionDecision.ALLOW, tool_pattern="mcp:transport_service:*"),
                PolicyRule(name="allow_delegate", decision=PermissionDecision.ALLOW, tool_pattern="delegate_task"),
            ],
            default_stance="deny",
        )
        self.permission_manager = PermissionManager(
            policy_engine=policy,
            confirmation_handler=DeterministicConfirmationHandler(always_allow=True),
        )
        self.mock_llm = MagicMock()
        self.tool_find = DummyTool("find_connection", source=ToolSource.MCP, server_name="transport_service")
        self.catalog = build_tool_catalog([self.tool_find])
        self.specs = get_standard_specialist_specs()
        self.factory = AgentFactory(
            specs=self.specs,
            tool_catalog=self.catalog,
            llm_client=self.mock_llm,
            permission_manager=self.permission_manager,
            event_bus=self.bus,
        )

    def test_rejected_delegation_unknown_role_publishes_terminal_event_and_metric(self) -> None:
        """Prove rejection before child creation (unknown specialist) emits exactly one terminal event with status='rejected'."""
        published_events: list[LifecycleEvent] = []

        class CaptureObserver:
            def on_event(self, event: LifecycleEvent) -> None:
                published_events.append(event)

        self.bus.subscribe(CaptureObserver())

        manager = SubAgentManager(
            agent_factory=self.factory,
            event_bus=self.bus,
        )
        parent_ctx = ExecutionContext.create_root(
            budget=ExecutionBudget(max_steps=5),
            agent_role="orchestrator",
        )

        res = manager.delegate(
            parent_ctx=parent_ctx,
            request=DelegationRequest(specialist_role="non_existent_specialist", task="Do something"),
        )
        self.assertFalse(res.success)
        self.assertEqual(res.failure_category, FailureCategory.VALIDATION)
        self.assertIn("not found", res.error or "")

        # Exactly 1 event published, of type DelegationFinishedEvent with status="rejected"
        self.assertEqual(len(published_events), 1)
        finish_ev = published_events[0]
        self.assertIsInstance(finish_ev, DelegationFinishedEvent)
        self.assertEqual(finish_ev.status, "rejected")
        self.assertEqual(finish_ev.child_agent_role, "non_existent_specialist")
        self.assertEqual(finish_ev.failure_category, FailureCategory.VALIDATION)

        # Prometheus metric recorded exactly 1 rejected delegation
        metric_val = self.collector.get_sample_value(
            "harness_delegations_total",
            labels={"parent_role": "orchestrator", "child_role": "non_existent_specialist", "status": "rejected"},
        )
        self.assertEqual(metric_val, 1.0)

    def test_rejected_delegation_budget_exhaustion_publishes_terminal_event(self) -> None:
        """Prove rejection due to exhausted root budget emits DelegationFinishedEvent(status='rejected')."""
        published_events: list[LifecycleEvent] = []

        class CaptureObserver:
            def on_event(self, event: LifecycleEvent) -> None:
                published_events.append(event)

        self.bus.subscribe(CaptureObserver())

        # Create ledger with 1 tool call and record consumption to exhaust it
        budget = ExecutionBudget(max_steps=5, max_tool_calls=1)
        ledger = HierarchicalBudgetLedger(root_budget=budget)
        ledger.record_parent_consumption(tool_calls=1)
        manager = SubAgentManager(
            agent_factory=self.factory,
            budget_ledger=ledger,
            event_bus=self.bus,
        )
        parent_ctx = ExecutionContext.create_root(
            budget=budget,
            agent_role="orchestrator",
            budget_ledger=ledger,
        )

        res = manager.delegate(
            parent_ctx=parent_ctx,
            request=DelegationRequest(specialist_role="transport_specialist", task="Find route"),
        )
        self.assertFalse(res.success)
        self.assertEqual(res.failure_category, FailureCategory.BUDGET)

        self.assertEqual(len(published_events), 1)
        finish_ev = published_events[0]
        self.assertIsInstance(finish_ev, DelegationFinishedEvent)
        self.assertEqual(finish_ev.status, "rejected")
        self.assertEqual(finish_ev.child_agent_role, "transport_specialist")
        self.assertEqual(finish_ev.failure_category, FailureCategory.BUDGET)

        metric_val = self.collector.get_sample_value(
            "harness_delegations_total",
            labels={"parent_role": "orchestrator", "child_role": "transport_specialist", "status": "rejected"},
        )
        self.assertEqual(metric_val, 1.0)

    def test_rejected_delegation_depth_limit_publishes_terminal_event(self) -> None:
        """Prove rejection due to maximum delegation depth emits DelegationFinishedEvent(status='rejected')."""
        published_events: list[LifecycleEvent] = []

        class CaptureObserver:
            def on_event(self, event: LifecycleEvent) -> None:
                published_events.append(event)

        self.bus.subscribe(CaptureObserver())

        ledger = HierarchicalBudgetLedger(root_budget=ExecutionBudget(max_steps=5), max_delegation_depth=1)
        manager = SubAgentManager(
            agent_factory=self.factory,
            budget_ledger=ledger,
            event_bus=self.bus,
        )
        # Context already at depth 1 (child) attempting to delegate further
        parent_ctx = ExecutionContext(
            trace_id="t1",
            run_id="r1",
            root_run_id="r0",
            parent_run_id="r0",
            delegation_depth=1,
            agent_id="child-1",
            agent_role="workspace_analyst",
            budget_limits=ExecutionBudget(max_steps=5),
            budget_ledger=ledger,
        )

        res = manager.delegate(
            parent_ctx=parent_ctx,
            request=DelegationRequest(specialist_role="transport_specialist", task="Find route"),
        )
        self.assertFalse(res.success)
        self.assertEqual(res.failure_category, FailureCategory.BUDGET)

        self.assertEqual(len(published_events), 1)
        finish_ev = published_events[0]
        self.assertIsInstance(finish_ev, DelegationFinishedEvent)
        self.assertEqual(finish_ev.status, "rejected")
        self.assertEqual(finish_ev.failure_category, FailureCategory.BUDGET)

    def test_rejected_delegation_fanout_limit_publishes_terminal_event(self) -> None:
        """Prove rejection due to fan-out limit emits DelegationFinishedEvent(status='rejected')."""
        published_events: list[LifecycleEvent] = []

        class CaptureObserver:
            def on_event(self, event: LifecycleEvent) -> None:
                published_events.append(event)

        self.bus.subscribe(CaptureObserver())

        ledger = HierarchicalBudgetLedger(root_budget=ExecutionBudget(max_steps=5), max_delegations=0)
        manager = SubAgentManager(
            agent_factory=self.factory,
            budget_ledger=ledger,
            event_bus=self.bus,
        )
        parent_ctx = ExecutionContext.create_root(
            budget=ExecutionBudget(max_steps=5),
            agent_role="orchestrator",
            budget_ledger=ledger,
        )

        res = manager.delegate(
            parent_ctx=parent_ctx,
            request=DelegationRequest(specialist_role="transport_specialist", task="Find route"),
        )
        self.assertFalse(res.success)
        self.assertEqual(res.failure_category, FailureCategory.BUDGET)

        self.assertEqual(len(published_events), 1)
        finish_ev = published_events[0]
        self.assertIsInstance(finish_ev, DelegationFinishedEvent)
        self.assertEqual(finish_ev.status, "rejected")


class TestActiveDelegationGaugeInvariant(unittest.TestCase):
    """Verify that active agent/delegation gauges strictly satisfy active >= 0 under all rejection paths."""

    def setUp(self) -> None:
        self.registry = CollectorRegistry()
        self.observer = PrometheusObserver(registry=self.registry)
        self.bus = LifecycleEventBus()
        self.bus.subscribe(self.observer)

        self.tool_find = DummyTool("find_connection", source=ToolSource.MCP, server_name="transport_service")
        self.tool_station = DummyTool("get_station_info", source=ToolSource.MCP, server_name="transport_service")
        self.tool_read = DummyTool("read_file", source=ToolSource.BUILTIN)
        self.tool_list = DummyTool("list_directory", source=ToolSource.BUILTIN)
        self.tool_search = DummyTool("search_files", source=ToolSource.BUILTIN)
        self.catalog = build_tool_catalog([self.tool_find, self.tool_station, self.tool_read, self.tool_list, self.tool_search])

        self.specs = get_standard_specialist_specs()
        self.pm = PermissionManager(
            policy_engine=PolicyEngine([PolicyRule(name="allow_all", decision=PermissionDecision.ALLOW, tool_pattern="*")]),
            confirmation_handler=DeterministicConfirmationHandler(always_allow=True),
        )
        self.mock_llm = MagicMock()
        self.factory = AgentFactory(
            specs=self.specs,
            tool_catalog=self.catalog,
            llm_client=self.mock_llm,
            permission_manager=self.pm,
            event_bus=self.bus,
        )
        self.budget = ExecutionBudget(max_steps=10, max_runtime_seconds=30.0)
        self.ledger = HierarchicalBudgetLedger(root_budget=self.budget)
        self.manager = SubAgentManager(
            agent_factory=self.factory,
            budget_ledger=self.ledger,
            event_bus=self.bus,
        )
        self.parent_ctx = ExecutionContext.create_root(
            budget=self.budget,
            agent_role="orchestrator",
            budget_ledger=self.ledger,
        )

    def test_rejected_delegation_preserves_zero_active_gauge(self) -> None:
        # 1. Unknown role
        val_before = self.registry.get_sample_value("harness_agent_runs_active", {"agent_role": "unknown_specialist"}) or 0.0
        self.assertEqual(val_before, 0.0)

        res = self.manager.delegate(
            parent_ctx=self.parent_ctx,
            request=DelegationRequest(specialist_role="unknown_specialist", task="Do something"),
        )
        self.assertFalse(res.success)
        val_after = self.registry.get_sample_value("harness_agent_runs_active", {"agent_role": "unknown_specialist"}) or 0.0
        self.assertEqual(val_after, 0.0)

        # 2. Budget exhausted
        exhausted_ledger = HierarchicalBudgetLedger(root_budget=ExecutionBudget(max_steps=1))
        exhausted_ledger.record_parent_consumption(steps=1)
        mgr2 = SubAgentManager(agent_factory=self.factory, budget_ledger=exhausted_ledger, event_bus=self.bus)
        ctx2 = ExecutionContext.create_root(budget=ExecutionBudget(max_steps=1), agent_role="orchestrator", budget_ledger=exhausted_ledger)
        res2 = mgr2.delegate(
            parent_ctx=ctx2,
            request=DelegationRequest(specialist_role="workspace_analyst", task="Analyze"),
        )
        self.assertFalse(res2.success)
        val_analyst = self.registry.get_sample_value("harness_agent_runs_active", {"agent_role": "workspace_analyst"}) or 0.0
        self.assertEqual(val_analyst, 0.0)

    def test_multiple_rejections_never_produce_negative_gauge(self) -> None:
        """Ensure that repeatedly rejecting delegations does not decrement gauge to -1, -2, etc."""
        for i in range(10):
            self.manager.delegate(
                parent_ctx=self.parent_ctx,
                request=DelegationRequest(specialist_role=f"nonexistent_{i}", task="Task"),
            )

        for role in ["orchestrator", "workspace_analyst", "transport_specialist"]:
            sample = self.registry.get_sample_value("harness_agent_runs_active", {"agent_role": role})
            if sample is not None:
                self.assertGreaterEqual(sample, 0.0, f"Active gauge for {role} became negative: {sample}")

    def test_successful_delegation_active_gauge_lifecycle(self) -> None:
        """Verify gauge is 0 initially, 1 during child execution, and 0 after completion."""
        gauge_values_during_exec: list[float] = []

        def mock_chat_impl(*args: Any, **kwargs: Any) -> Any:
            current_gauge = self.registry.get_sample_value("harness_agent_runs_active", {"agent_role": "workspace_analyst"}) or 0.0
            gauge_values_during_exec.append(current_gauge)
            resp = MagicMock()
            resp.tool_calls = []
            resp.content = "Analysis completed."
            return resp

        self.mock_llm.chat.side_effect = mock_chat_impl

        val_init = self.registry.get_sample_value("harness_agent_runs_active", {"agent_role": "workspace_analyst"}) or 0.0
        self.assertEqual(val_init, 0.0)

        res = self.manager.delegate(
            parent_ctx=self.parent_ctx,
            request=DelegationRequest(specialist_role="workspace_analyst", task="Analyze workspace"),
        )
        self.assertTrue(res.success)
        self.assertEqual(gauge_values_during_exec, [1.0])

        val_final = self.registry.get_sample_value("harness_agent_runs_active", {"agent_role": "workspace_analyst"}) or 0.0
        self.assertEqual(val_final, 0.0)


class TestExplicitRootContextBudgetOwnership(unittest.TestCase):
    """Verify budget ledger ownership semantics for context=None, explicit root context, and child context."""

    def setUp(self) -> None:
        self.budget = ExecutionBudget(max_steps=10, max_runtime_seconds=60.0)
        self.mock_llm = MagicMock()
        resp = MagicMock()
        resp.tool_calls = []
        resp.content = "Done."
        self.mock_llm.chat.return_value = resp

    def test_case_a_context_none_creates_fresh_root_ledger(self) -> None:
        controller = ReActController(
            llm_client=self.mock_llm,
            tool_registry=MagicMock(),
            tool_executor=MagicMock(),
            budget=self.budget,
        )
        res = controller.run_turn("hello")
        self.assertIsNotNone(controller.budget_ledger)
        self.assertEqual(controller.budget_ledger.remaining_steps, self.budget.max_steps - 1)
        self.assertEqual(controller.budget_ledger.total_steps_consumed, 1)

    def test_case_b_explicit_root_context_without_ledger_gets_fresh_root_ledger(self) -> None:
        controller = ReActController(
            llm_client=self.mock_llm,
            tool_registry=MagicMock(),
            tool_executor=MagicMock(),
            budget=self.budget,
        )
        explicit_root_ctx = ExecutionContext.create_root(
            budget=self.budget,
            agent_role="orchestrator",
            budget_ledger=None,  # No ledger provided
        )
        self.assertEqual(explicit_root_ctx.delegation_depth, 0)
        self.assertIsNone(explicit_root_ctx.budget_ledger)

        res = controller.run_turn("hello", context=explicit_root_ctx)
        self.assertIsNotNone(controller.budget_ledger)
        self.assertEqual(controller.budget_ledger.remaining_steps, self.budget.max_steps - 1)
        self.assertEqual(controller.budget_ledger.total_steps_consumed, 1)

    def test_case_c_child_context_reuses_inherited_ledger_without_creating_fresh_capacity(self) -> None:
        root_ledger = HierarchicalBudgetLedger(root_budget=self.budget)
        controller = ReActController(
            llm_client=self.mock_llm,
            tool_registry=MagicMock(),
            tool_executor=MagicMock(),
            budget=self.budget,
        )
        child_ctx = ExecutionContext(
            trace_id="t1",
            run_id="r1",
            root_run_id="r0",
            parent_run_id="r0",
            delegation_depth=1,  # child
            agent_id="child-1",
            agent_role="workspace_analyst",
            budget_limits=ExecutionBudget(max_steps=4),
            budget_ledger=root_ledger,
        )
        res = controller.run_turn("child task", context=child_ctx)
        # Controller must reuse the exact root_ledger, NOT instantiate a fresh one
        self.assertIs(controller.budget_ledger, root_ledger)
        self.assertTrue(res.is_success)

    def test_case_d_malformed_child_context_without_ledger_fails_closed(self) -> None:
        controller = ReActController(
            llm_client=self.mock_llm,
            tool_registry=MagicMock(),
            tool_executor=MagicMock(),
            budget=self.budget,
        )
        malformed_child_ctx = ExecutionContext(
            trace_id="t1",
            run_id="r1",
            root_run_id="r0",
            parent_run_id="r0",
            delegation_depth=1,  # child
            agent_id="child-1",
            agent_role="workspace_analyst",
            budget_limits=ExecutionBudget(max_steps=4),
            budget_ledger=None,  # Malformed: missing budget_ledger
        )
        with self.assertRaises(RuntimeError) as cm:
            controller.run_turn("child task", context=malformed_child_ctx)
        self.assertIn("arrived without inherited budget authority", str(cm.exception))

    def test_case_e_malformed_child_cannot_borrow_stale_controller_ledger(self) -> None:
        controller = ReActController(
            llm_client=self.mock_llm,
            tool_registry=MagicMock(),
            tool_executor=MagicMock(),
            budget=self.budget,
        )
        # 1. Root run creates a root ledger on controller
        controller.run_turn("root prompt")
        stale_ledger = controller.budget_ledger
        self.assertIsNotNone(stale_ledger)
        remaining_before = stale_ledger.remaining_steps

        # 2. Malformed child context arrives
        malformed_child_ctx = ExecutionContext(
            trace_id="t1",
            run_id="r2",
            root_run_id="r0",
            parent_run_id="r0",
            delegation_depth=2,
            agent_id="child-malformed",
            agent_role="workspace_analyst",
            budget_limits=ExecutionBudget(max_steps=4),
            budget_ledger=None,
        )
        with self.assertRaises(RuntimeError):
            controller.run_turn("child prompt", context=malformed_child_ctx)

        # 3. Verify stale ledger was NOT borrowed and was NOT consumed
        self.assertEqual(stale_ledger.remaining_steps, remaining_before)
        self.assertIs(controller.budget_ledger, stale_ledger)


class TestRejectionObservabilityInvariants(unittest.TestCase):
    """Verify simultaneous invariants upon pre-instantiation delegation rejection."""

    def setUp(self) -> None:
        self.registry = CollectorRegistry()
        self.prom_observer = PrometheusObserver(registry=self.registry)
        self.bus = LifecycleEventBus()
        self.bus.subscribe(self.prom_observer)

        self.exporter = InMemorySpanExporter()
        self.provider = TracerProvider(resource=Resource.create({"service.name": "test"}))
        self.provider.add_span_processor(SimpleSpanProcessor(self.exporter))
        self.otel_observer = OpenTelemetryObserver(service_name="test", tracer_provider=self.provider)
        self.bus.subscribe(self.otel_observer)

        self.tool_find = DummyTool("find_connection", source=ToolSource.MCP, server_name="transport_service")
        self.tool_station = DummyTool("get_station_info", source=ToolSource.MCP, server_name="transport_service")
        self.tool_read = DummyTool("read_file", source=ToolSource.BUILTIN)
        self.tool_list = DummyTool("list_directory", source=ToolSource.BUILTIN)
        self.tool_search = DummyTool("search_files", source=ToolSource.BUILTIN)
        self.catalog = build_tool_catalog([self.tool_find, self.tool_station, self.tool_read, self.tool_list, self.tool_search])

        self.specs = get_standard_specialist_specs()
        self.pm = PermissionManager(
            policy_engine=PolicyEngine([PolicyRule(name="allow_all", decision=PermissionDecision.ALLOW, tool_pattern="*")]),
            confirmation_handler=DeterministicConfirmationHandler(always_allow=True),
        )
        self.mock_llm = MagicMock()
        self.factory = AgentFactory(
            specs=self.specs,
            tool_catalog=self.catalog,
            llm_client=self.mock_llm,
            permission_manager=self.pm,
            event_bus=self.bus,
        )
        self.budget = ExecutionBudget(max_steps=10)
        self.ledger = HierarchicalBudgetLedger(root_budget=self.budget)
        self.manager = SubAgentManager(
            agent_factory=self.factory,
            budget_ledger=self.ledger,
            event_bus=self.bus,
        )
        self.parent_ctx = ExecutionContext.create_root(
            budget=self.budget,
            agent_role="orchestrator",
            budget_ledger=self.ledger,
        )

    def tearDown(self) -> None:
        self.otel_observer.shutdown()

    def test_simultaneous_rejection_invariants(self) -> None:
        """Verify all 5 invariants simultaneously upon rejected delegation."""
        published_events: list[LifecycleEvent] = []

        class CaptureObserver:
            def on_event(self, event: LifecycleEvent) -> None:
                published_events.append(event)

        self.bus.subscribe(CaptureObserver())

        # Snapshot state prior to rejection
        initial_available_steps = self.ledger.remaining_steps

        # Trigger pre-instantiation rejection via unknown role
        res = self.manager.delegate(
            parent_ctx=self.parent_ctx,
            request=DelegationRequest(specialist_role="unknown_role", task="Do work"),
        )
        self.assertFalse(res.success)

        # 1. harness_delegations_total{status="rejected"} increments exactly once
        rejected_count = self.registry.get_sample_value(
            "harness_delegations_total",
            {"parent_role": "orchestrator", "child_role": "unknown_role", "status": "rejected"},
        ) or 0.0
        self.assertEqual(rejected_count, 1.0)

        # 2. Active delegation gauge remains unchanged at 0
        active_gauge = self.registry.get_sample_value(
            "harness_agent_runs_active",
            {"agent_role": "unknown_role"},
        ) or 0.0
        self.assertEqual(active_gauge, 0.0)

        # 3. No child agent.run event / span is created
        agent_run_spans = [s for s in self.exporter.get_finished_spans() if s.name == "agent.run"]
        self.assertEqual(len(agent_run_spans), 0)

        # 4. No budget slice leaks (available steps on parent ledger remain intact)
        self.assertEqual(self.ledger.remaining_steps, initial_available_steps)

        # 5. Exactly one terminal event occurs (no duplicate terminal event)
        terminal_events = [e for e in published_events if isinstance(e, DelegationFinishedEvent)]
        self.assertEqual(len(terminal_events), 1)
        self.assertEqual(terminal_events[0].status, "rejected")


if __name__ == "__main__":
    unittest.main()
