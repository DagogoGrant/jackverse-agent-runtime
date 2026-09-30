"""Unit tests for LifecycleEventBus and typed domain lifecycle events."""

from datetime import datetime, timezone
import tempfile
import unittest
from unittest.mock import MagicMock
import uuid

from openai import APITimeoutError

from harness.agent.budget import ExecutionBudget, TerminationReason
from harness.agent.react import ReActController
from harness.llm.client import LLMClient, LLMResponse, ToolCall
from harness.memory.base import (
    AdmissionAction,
    AdmissionDecision,
    MemoryEntry,
    MemorySource,
    MemoryStatus,
    MemoryStore,
)
from harness.memory.manager import MemoryManager
from harness.runtime.context import (
    ExecutionContext,
    execution_context_scope,
    get_current_context,
)
from harness.runtime.events import (
    EventObserver,
    FailureCategory,
    LifecycleEvent,
    LifecycleEventBus,
    LifecycleEventType,
    LLMCallFinishedEvent,
    LLMCallStartedEvent,
    LLMCallStatus,
    MemoryOperationEvent,
    MemoryOperationStatus,
    MemoryOperationType,
    RunFinishedEvent,
    RunStartedEvent,
    ToolCallFinishedEvent,
    ToolCallRequestedEvent,
    ToolCallStartedEvent,
    ToolCallStatus,
)
from harness.tools.base import ErrorCode, Tool, ToolResult, ToolSource, ToolSpec
from harness.tools.executor import ToolExecutor
from harness.tools.registry import ToolRegistry


class RecordingObserver:
    """In-memory event recorder for assertions."""

    def __init__(self) -> None:
        self.events: list[LifecycleEvent] = []

    def on_event(self, event: LifecycleEvent) -> None:
        self.events.append(event)


class CrashingObserver:
    """Observer that deliberately raises to test subscriber isolation."""

    def on_event(self, event: LifecycleEvent) -> None:
        raise RuntimeError("Catastrophic observer crash")


class DummyEchoTool(Tool):
    def __init__(self, name: str = "echo_tool", source: ToolSource = ToolSource.BUILTIN, server_name: str | None = None) -> None:
        self._name = name
        self._source = source
        self._server_name = server_name

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name=self._name,
            description="Echoes input",
            input_schema={
                "type": "object",
                "properties": {"text": {"type": "string"}},
                "required": ["text"],
            },
            source=self._source,
            server_name=self._server_name,
        )

    def execute(self, arguments: dict[str, object]) -> ToolResult:
        return ToolResult(content=f"Echo: {arguments.get('text')}")


class TestLifecycleEvents(unittest.TestCase):
    """Test suite for event bus error isolation, sequencing, and domain telemetry."""

    def setUp(self) -> None:
        self.bus = LifecycleEventBus()
        self.observer = RecordingObserver()
        self.bus.subscribe(self.observer)
        self.budget = ExecutionBudget(max_steps=5, max_tool_calls=10, max_runtime_seconds=30.0)

    def test_event_bus_error_isolation(self) -> None:
        crasher = CrashingObserver()
        self.bus.subscribe(crasher)

        ctx = ExecutionContext.create_root(budget=self.budget)
        event = RunStartedEvent(
            timestamp=100.0,
            trace_id=ctx.trace_id,
            run_id=ctx.run_id,
            root_run_id=ctx.root_run_id,
            parent_run_id=None,
            agent_id=ctx.agent_id,
            agent_role=ctx.agent_role,
        )

        # Bus should trap crasher exception without propagating to caller
        self.bus.publish(event)

        # Healthy observer still receives event
        self.assertEqual(len(self.observer.events), 1)
        self.assertEqual(self.observer.events[0], event)

        # Test unsubscription
        self.bus.unsubscribe(crasher)
        self.bus.publish(event)
        self.assertEqual(len(self.observer.events), 2)

    def test_react_loop_full_lifecycle_sequencing(self) -> None:
        tool = DummyEchoTool()
        registry = ToolRegistry()
        registry.register(tool)
        executor = ToolExecutor(event_bus=self.bus)

        # Mock LLM client: Step 1 emits tool call, Step 2 returns final answer
        mock_llm = MagicMock()
        mock_llm.chat.side_effect = [
            LLMResponse(
                content=None,
                tool_calls=[ToolCall(id="call_provider_42", name="echo_tool", arguments={"text": "hello"})],
            ),
            LLMResponse(content="Final greeting completed.", tool_calls=[]),
        ]

        # Mock memory manager with event bus
        mock_memory = MagicMock()
        mock_memory.retrieve.return_value = []
        mock_memory.format_context.return_value = ""
        mock_memory.retrieve_procedural.return_value = []
        mock_memory.format_procedural_context.return_value = ""

        controller = ReActController(
            llm_client=mock_llm,
            tool_registry=registry,
            tool_executor=executor,
            budget=self.budget,
            memory_manager=mock_memory,
            event_bus=self.bus,
        )

        result = controller.run("Say hello")
        self.assertEqual(result, "Final greeting completed.")

        types = [e.event_type for e in self.observer.events]

        # Invariant: RunStarted must be first, RunFinished must be last
        self.assertEqual(types[0], LifecycleEventType.RUN_STARTED)
        self.assertEqual(types[-1], LifecycleEventType.RUN_FINISHED)

        run_start = self.observer.events[0]
        run_finish = self.observer.events[-1]
        assert isinstance(run_start, RunStartedEvent)
        assert isinstance(run_finish, RunFinishedEvent)

        self.assertTrue(run_finish.is_success)
        self.assertEqual(run_finish.termination_reason, TerminationReason.FINAL_ANSWER)
        self.assertEqual(run_finish.steps, 2)
        self.assertEqual(run_finish.tool_calls, 1)
        self.assertGreater(run_finish.duration_seconds, 0.0)

        # Correlation invariant: trace_id and run_id must match across all events
        for e in self.observer.events:
            self.assertEqual(e.trace_id, run_start.trace_id)
            self.assertEqual(e.run_id, run_start.run_id)

        # Tool sequence invariant: REQUESTED -> STARTED -> FINISHED
        tool_req = next(e for e in self.observer.events if isinstance(e, ToolCallRequestedEvent))
        tool_start = next(e for e in self.observer.events if isinstance(e, ToolCallStartedEvent))
        tool_finish = next(e for e in self.observer.events if isinstance(e, ToolCallFinishedEvent))

        self.assertEqual(tool_req.call_id, "call_provider_42")
        self.assertEqual(tool_start.call_id, "call_provider_42")
        self.assertEqual(tool_finish.call_id, "call_provider_42")
        self.assertEqual(tool_start.tool_source, ToolSource.BUILTIN)
        self.assertEqual(tool_finish.status, ToolCallStatus.SUCCESS)
        self.assertFalse(tool_finish.is_error)
        self.assertGreater(tool_finish.duration_seconds, 0.0)

    def test_validation_failure_event_containment(self) -> None:
        tool = DummyEchoTool()
        executor = ToolExecutor(event_bus=self.bus)
        ctx = ExecutionContext.create_root(budget=self.budget)

        with execution_context_scope(ctx):
            # Pass invalid arguments (missing required "text", wrong property "wrong_key")
            result = executor.execute(tool, {"wrong_key": 123}, call_id="call_val_err")

        self.assertTrue(result.is_error)
        self.assertEqual(result.error_code, ErrorCode.INVALID_ARGUMENT)

        types = [e.event_type for e in self.observer.events]
        self.assertIn(LifecycleEventType.TOOL_CALL_STARTED, types)
        self.assertIn(LifecycleEventType.TOOL_CALL_FINISHED, types)

        finish_event = next(e for e in self.observer.events if isinstance(e, ToolCallFinishedEvent))
        self.assertEqual(finish_event.call_id, "call_val_err")
        self.assertEqual(finish_event.status, ToolCallStatus.VALIDATION_ERROR)
        self.assertTrue(finish_event.is_error)
        self.assertEqual(finish_event.failure_category, FailureCategory.VALIDATION)
        self.assertEqual(finish_event.error_type, "ContractValidationError")
        self.assertEqual(finish_event.error_code, ErrorCode.INVALID_ARGUMENT.value)

    def test_llm_boundary_instrumentation_and_error(self) -> None:
        llm = LLMClient(
            api_key="fake-key",
            base_url="http://fake-url",
            model="test-model",
            max_retries=0,
            event_bus=self.bus,
        )
        ctx = ExecutionContext.create_root(budget=self.budget)

        # Mock OpenAI client chat completions to simulate APITimeoutError
        mock_response = MagicMock()
        mock_response.request = MagicMock()
        fake_timeout = APITimeoutError(request=mock_response.request)
        llm.client.chat.completions.create = MagicMock(side_effect=fake_timeout)

        with execution_context_scope(ctx):
            with self.assertRaises(RuntimeError) as cm:
                llm.chat([{"role": "user", "content": "hello"}])
            self.assertIn("Request timed out", str(cm.exception))

        types = [e.event_type for e in self.observer.events]
        self.assertEqual(types, [LifecycleEventType.LLM_CALL_STARTED, LifecycleEventType.LLM_CALL_FINISHED])

        start_event = self.observer.events[0]
        finish_event = self.observer.events[1]
        assert isinstance(start_event, LLMCallStartedEvent)
        assert isinstance(finish_event, LLMCallFinishedEvent)

        self.assertEqual(finish_event.llm_call_id, start_event.llm_call_id)
        self.assertEqual(finish_event.status, LLMCallStatus.ERROR)
        self.assertEqual(finish_event.failure_category, FailureCategory.TIMEOUT)
        self.assertEqual(finish_event.error_type, "APITimeoutError")
        self.assertGreater(finish_event.duration_seconds, 0.0)

    def test_budget_termination_event(self) -> None:
        tool = DummyEchoTool()
        registry = ToolRegistry()
        registry.register(tool)
        executor = ToolExecutor(event_bus=self.bus)

        # Model requests tool infinitely
        mock_llm = MagicMock()
        mock_llm.chat.return_value = LLMResponse(
            content=None,
            tool_calls=[ToolCall(id="call_inf", name="echo_tool", arguments={"text": "inf"})],
        )

        controller = ReActController(
            llm_client=mock_llm,
            tool_registry=registry,
            tool_executor=executor,
            budget=ExecutionBudget(max_steps=2, max_tool_calls=5),
            event_bus=self.bus,
        )

        run_res = controller.run_turn("Infinite loop test")
        self.assertFalse(run_res.is_success)
        self.assertEqual(run_res.termination_reason, TerminationReason.STEP_BUDGET_EXCEEDED)

        run_finish = next(e for e in self.observer.events if isinstance(e, RunFinishedEvent))
        self.assertFalse(run_finish.is_success)
        self.assertEqual(run_finish.termination_reason, TerminationReason.STEP_BUDGET_EXCEEDED)
        self.assertEqual(run_finish.failure_category, FailureCategory.BUDGET)
        self.assertEqual(run_finish.error_type, "StepBudgetExceededError")

    def test_tool_origin_metadata_builtin_vs_mcp(self) -> None:
        builtin_tool = DummyEchoTool("builtin_tool", source=ToolSource.BUILTIN, server_name=None)
        mcp_tool = DummyEchoTool("mcp_tool", source=ToolSource.MCP, server_name="transport-mcp")

        executor = ToolExecutor(event_bus=self.bus)
        ctx = ExecutionContext.create_root(budget=self.budget)

        with execution_context_scope(ctx):
            executor.execute(builtin_tool, {"text": "builtin"})
            executor.execute(mcp_tool, {"text": "mcp"})

        tool_finishes = [e for e in self.observer.events if isinstance(e, ToolCallFinishedEvent)]
        self.assertEqual(len(tool_finishes), 2)

        # Builtin tool check
        self.assertEqual(tool_finishes[0].tool_name, "builtin_tool")
        self.assertEqual(tool_finishes[0].tool_source, ToolSource.BUILTIN)
        self.assertIsNone(tool_finishes[0].server_name)

        # MCP tool check
        self.assertEqual(tool_finishes[1].tool_name, "mcp_tool")
        self.assertEqual(tool_finishes[1].tool_source, ToolSource.MCP)
        self.assertEqual(tool_finishes[1].server_name, "transport-mcp")

    def test_memory_lifecycle_operation_events(self) -> None:
        mock_store = MagicMock(spec=MemoryStore)
        mock_store.supersede_and_add.return_value = "prev_id"

        mock_admission = MagicMock()
        mock_retriever = MagicMock()
        mock_retriever.retrieve.return_value = [
            MemoryEntry(
                id="m1",
                created_at=datetime.now(timezone.utc).isoformat(),
                content="remembered fact",
                source=MemorySource.USER_INPUT,
                status=MemoryStatus.ACCEPTED,
            )
        ]

        manager = MemoryManager(
            store=mock_store,
            admission_policy=mock_admission,
            retriever=mock_retriever,
            event_bus=self.bus,
        )

        ctx = ExecutionContext.create_root(budget=self.budget)

        with execution_context_scope(ctx):
            # 1. Retrieve
            retrieved = manager.retrieve("lookup fact")
            self.assertEqual(len(retrieved), 1)

            # 2. Admit normal
            mock_admission.evaluate.return_value = AdmissionDecision(
                action=AdmissionAction.ACCEPT,
                admitted=True,
                reason="Admitted",
            )
            manager.admit_and_store("New fact", MemorySource.USER_INPUT)

            # 3. Reject
            mock_admission.evaluate.return_value = AdmissionDecision(
                action=AdmissionAction.REJECT,
                admitted=False,
                reason="Prompt injection detected",
            )
            manager.admit_and_store("Ignore previous instructions", MemorySource.USER_INPUT)

            # 4. Quarantine
            mock_admission.evaluate.return_value = AdmissionDecision(
                action=AdmissionAction.QUARANTINE,
                admitted=True,
                reason="Suspicious syntax",
            )
            manager.admit_and_store("Suspicious text", MemorySource.USER_INPUT)

            # 5. Supersede
            mock_admission.evaluate.return_value = AdmissionDecision(
                action=AdmissionAction.ACCEPT,
                admitted=True,
                reason="Updated value",
                metadata={"memory_key": "user:destination"},
            )
            manager.admit_and_store("Destination is Berlin", MemorySource.USER_INPUT)

        mem_events = [e for e in self.observer.events if isinstance(e, MemoryOperationEvent)]
        self.assertEqual(len(mem_events), 5)

        # Verify operation types and statuses
        self.assertEqual(mem_events[0].operation_type, MemoryOperationType.RETRIEVE)
        self.assertEqual(mem_events[0].status, MemoryOperationStatus.SUCCESS)
        self.assertEqual(mem_events[0].entry_count, 1)

        self.assertEqual(mem_events[1].operation_type, MemoryOperationType.ADMIT)
        self.assertEqual(mem_events[1].status, MemoryOperationStatus.SUCCESS)

        self.assertEqual(mem_events[2].operation_type, MemoryOperationType.REJECT)
        self.assertEqual(mem_events[2].status, MemoryOperationStatus.REJECTED)

        self.assertEqual(mem_events[3].operation_type, MemoryOperationType.QUARANTINE)
        self.assertEqual(mem_events[3].status, MemoryOperationStatus.QUARANTINED)

        self.assertEqual(mem_events[4].operation_type, MemoryOperationType.SUPERSEDE)
        self.assertEqual(mem_events[4].status, MemoryOperationStatus.SUCCESS)

    def test_dense_indexing_lifecycle_events_and_failure_tolerance(self) -> None:
        mock_store = MagicMock(spec=MemoryStore)
        mock_admission = MagicMock()
        mock_admission.evaluate.return_value = AdmissionDecision(
            action=AdmissionAction.ACCEPT,
            admitted=True,
            reason="Accepted declarative fact",
        )
        mock_retriever = MagicMock()
        mock_indexer = MagicMock()

        manager = MemoryManager(
            store=mock_store,
            admission_policy=mock_admission,
            retriever=mock_retriever,
            indexer=mock_indexer,
            event_bus=self.bus,
        )

        ctx = ExecutionContext.create_root(budget=self.budget)

        with execution_context_scope(ctx):
            # Case 1: Indexing succeeds
            mock_indexer.ensure_embeddings.return_value = None
            manager.admit_and_store("Fact with embeddings", MemorySource.USER_INPUT)

            # Case 2: Indexing fails (simulated model crash / OOM)
            mock_indexer.ensure_embeddings.side_effect = RuntimeError("Dense embedding model unavailable")
            decision = manager.admit_and_store("Fact with failing embedding", MemorySource.USER_INPUT)

            # Week 2 invariant: memory remains admitted and persisted despite index failure
            self.assertTrue(decision.admitted)

        mem_events = [e for e in self.observer.events if isinstance(e, MemoryOperationEvent)]
        self.assertEqual(len(mem_events), 4)

        # Pair 1: ADMIT (SUCCESS) -> INDEX (SUCCESS)
        self.assertEqual(mem_events[0].operation_type, MemoryOperationType.ADMIT)
        self.assertEqual(mem_events[0].status, MemoryOperationStatus.SUCCESS)
        self.assertEqual(mem_events[1].operation_type, MemoryOperationType.INDEX)
        self.assertEqual(mem_events[1].status, MemoryOperationStatus.SUCCESS)

        # Pair 2: ADMIT (SUCCESS) -> INDEX (ERROR with decoupled failure telemetry)
        self.assertEqual(mem_events[2].operation_type, MemoryOperationType.ADMIT)
        self.assertEqual(mem_events[2].status, MemoryOperationStatus.SUCCESS)
        self.assertEqual(mem_events[3].operation_type, MemoryOperationType.INDEX)
        self.assertEqual(mem_events[3].status, MemoryOperationStatus.ERROR)
        self.assertEqual(mem_events[3].failure_category, FailureCategory.INTERNAL)
        self.assertEqual(mem_events[3].error_type, "RuntimeError")
        self.assertIn("Dense embedding model unavailable", mem_events[3].error_message or "")

    def test_no_active_context_compatibility(self) -> None:
        # Verify ambient context is None
        self.assertIsNone(get_current_context())

        # 1. ToolExecutor without active context
        tool = DummyEchoTool()
        executor = ToolExecutor(event_bus=self.bus)
        res = executor.execute(tool, {"text": "direct call"})
        self.assertEqual(res.content, "Echo: direct call")
        self.assertFalse(res.is_error)

        # 2. LLMClient without active context
        mock_response = MagicMock()
        mock_choice = MagicMock()
        mock_choice.message.content = "Direct LLM reply"
        mock_choice.message.tool_calls = None
        mock_response.choices = [mock_choice]
        mock_response.usage = None

        llm = LLMClient(api_key="k", base_url="http://fake", model="m", event_bus=self.bus)
        llm.client.chat.completions.create = MagicMock(return_value=mock_response)
        llm_res = llm.chat([{"role": "user", "content": "hi"}])
        self.assertEqual(llm_res.content, "Direct LLM reply")

        # 3. MemoryManager without active context
        mock_store = MagicMock(spec=MemoryStore)
        mock_admission = MagicMock()
        mock_admission.evaluate.return_value = AdmissionDecision(
            action=AdmissionAction.ACCEPT, admitted=True, reason="ok"
        )
        mock_retriever = MagicMock()
        mock_retriever.retrieve.return_value = []
        manager = MemoryManager(
            store=mock_store,
            admission_policy=mock_admission,
            retriever=mock_retriever,
            event_bus=self.bus,
        )
        ret = manager.retrieve("query")
        self.assertEqual(ret, [])
        adm = manager.admit_and_store("content", MemorySource.USER_INPUT)
        self.assertTrue(adm.admitted)

        # Invariant: zero events published when no active context is present
        self.assertEqual(len(self.observer.events), 0)

    def test_concrete_events_own_their_type(self) -> None:
        ctx = ExecutionContext.create_root(budget=self.budget)
        common_kwargs = {
            "timestamp": 12345.0,
            "trace_id": ctx.trace_id,
            "run_id": ctx.run_id,
            "root_run_id": ctx.root_run_id,
            "parent_run_id": None,
            "agent_id": ctx.agent_id,
            "agent_role": ctx.agent_role,
        }

        # 1. Concrete event instance returns its canonical event_type
        start_event = RunStartedEvent(**common_kwargs)
        self.assertEqual(start_event.event_type, LifecycleEventType.RUN_STARTED)

        finish_event = RunFinishedEvent(**common_kwargs)
        self.assertEqual(finish_event.event_type, LifecycleEventType.RUN_FINISHED)

        llm_start = LLMCallStartedEvent(**common_kwargs)
        self.assertEqual(llm_start.event_type, LifecycleEventType.LLM_CALL_STARTED)

        tool_finish = ToolCallFinishedEvent(**common_kwargs)
        self.assertEqual(tool_finish.event_type, LifecycleEventType.TOOL_CALL_FINISHED)

        mem_event = MemoryOperationEvent(**common_kwargs)
        self.assertEqual(mem_event.event_type, LifecycleEventType.MEMORY_OPERATION)

        # 2. Attempting to pass event_type as argument raises TypeError (ClassVar invariant)
        with self.assertRaises(TypeError):
            RunStartedEvent(
                event_type=LifecycleEventType.TOOL_CALL_FINISHED,  # type: ignore
                **common_kwargs,
            )

        with self.assertRaises(TypeError):
            RunFinishedEvent(
                event_type=LifecycleEventType.RUN_STARTED,  # type: ignore
                **common_kwargs,
            )


if __name__ == "__main__":
    unittest.main()
