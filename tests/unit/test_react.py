from collections.abc import Mapping, Sequence
from typing import Any
from unittest.mock import MagicMock
import unittest

from harness.agent.react import ReActController
from harness.llm.client import LLMResponse, ToolCall
from harness.tools.base import Tool, ToolResult, ToolSpec
from harness.tools.executor import ToolExecutor
from harness.tools.registry import ToolRegistry


class DummyTool:
    """Mock tool for testing ReAct execution."""

    def __init__(self, name: str = "dummy_tool", return_error: bool = False) -> None:
        self._name = name
        self._return_error = return_error
        self.invocations: list[dict[str, object]] = []

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name=self._name,
            description=f"Mock tool {self._name}",
            input_schema={
                "type": "object",
                "properties": {"arg": {"type": "string"}},
                "required": [],
                "additionalProperties": False,
            },
        )

    def execute(self, arguments: Mapping[str, object]) -> ToolResult:
        self.invocations.append(dict(arguments))
        if self._return_error:
            return ToolResult(content=f"Error in {self._name}", is_error=True)
        return ToolResult(content=f"Success from {self._name} with arg={arguments.get('arg')}", is_error=False)


class MockLLMClient:
    """Deterministic fake LLM client for testing ReAct loop."""

    def __init__(self, responses: list[LLMResponse]) -> None:
        self.responses = list(responses)
        self.recorded_calls: list[dict[str, Any]] = []

    def chat(
        self,
        messages: Sequence[dict[str, Any]],
        tools: Sequence[ToolSpec] | None = None,
    ) -> LLMResponse:
        self.recorded_calls.append({
            "messages": [dict(m) for m in messages],
            "tools": list(tools) if tools is not None else None,
        })
        if not self.responses:
            raise RuntimeError("MockLLMClient ran out of queued responses.")
        return self.responses.pop(0)


class TestReActController(unittest.TestCase):
    def setUp(self) -> None:
        self.registry = ToolRegistry()
        self.executor = ToolExecutor()

    def test_direct_final_answer_with_zero_tools(self) -> None:
        llm = MockLLMClient([
            LLMResponse(content="Passau is in Bavaria.", tool_calls=[])
        ])
        controller = ReActController(
            llm_client=llm,
            tool_registry=self.registry,
            tool_executor=self.executor,
            max_steps=5,
        )

        result = controller.run("Where is Passau?")

        self.assertEqual(result, "Passau is in Bavaria.")
        # Context: system + user + assistant = 3
        self.assertEqual(len(controller.context), 3)
        self.assertEqual(controller.context[0]["role"], "system")
        self.assertEqual(controller.context[1], {"role": "user", "content": "Where is Passau?"})
        self.assertEqual(controller.context[2], {"role": "assistant", "content": "Passau is in Bavaria."})
        # Verify 1 LLM call made
        self.assertEqual(len(llm.recorded_calls), 1)

    def test_one_tool_call_followed_by_final_answer(self) -> None:
        tool = DummyTool(name="get_weather")
        self.registry.register(tool)

        llm = MockLLMClient([
            # Turn 1: model requests tool
            LLMResponse(
                content=None,
                tool_calls=[ToolCall(id="call_123", name="get_weather", arguments={"arg": "Passau"})],
            ),
            # Turn 2: model sees observation and provides final text
            LLMResponse(
                content="The weather in Passau is sunny.",
                tool_calls=[],
            ),
        ])

        controller = ReActController(
            llm_client=llm,
            tool_registry=self.registry,
            tool_executor=self.executor,
            max_steps=5,
        )

        result = controller.run("What is the weather in Passau?")

        self.assertEqual(result, "The weather in Passau is sunny.")
        # Tool was executed once
        self.assertEqual(len(tool.invocations), 1)
        self.assertEqual(tool.invocations[0], {"arg": "Passau"})

        # Verify context fidelity:
        # [0] system
        # [1] user
        # [2] assistant with tool_calls
        # [3] tool observation with matching tool_call_id (without extraneous 'name' field)
        # [4] assistant final answer
        self.assertEqual(len(controller.context), 5)
        self.assertEqual(controller.context[1]["role"], "user")
        self.assertEqual(controller.context[2]["role"], "assistant")
        self.assertEqual(controller.context[2]["tool_calls"][0]["id"], "call_123")
        self.assertEqual(controller.context[3]["role"], "tool")
        self.assertEqual(controller.context[3]["tool_call_id"], "call_123")
        self.assertNotIn("name", controller.context[3])
        self.assertIn("Success from get_weather", controller.context[3]["content"])
        self.assertEqual(controller.context[4]["role"], "assistant")
        self.assertEqual(controller.context[4]["content"], "The weather in Passau is sunny.")

        # Verify second LLM call received updated context with tool observation
        self.assertEqual(len(llm.recorded_calls), 2)
        second_call_messages = llm.recorded_calls[1]["messages"]
        self.assertEqual(len(second_call_messages), 4)
        self.assertEqual(second_call_messages[3]["role"], "tool")
        self.assertEqual(second_call_messages[3]["tool_call_id"], "call_123")

    def test_multiple_tool_calls_and_order_preservation(self) -> None:
        tool_a = DummyTool(name="tool_a")
        tool_b = DummyTool(name="tool_b")
        self.registry.register(tool_a)
        self.registry.register(tool_b)

        llm = MockLLMClient([
            LLMResponse(
                content=None,
                tool_calls=[
                    ToolCall(id="call_a", name="tool_a", arguments={"arg": "first"}),
                    ToolCall(id="call_b", name="tool_b", arguments={"arg": "second"}),
                ],
            ),
            LLMResponse(
                content="Both operations finished.",
                tool_calls=[],
            ),
        ])

        controller = ReActController(
            llm_client=llm,
            tool_registry=self.registry,
            tool_executor=self.executor,
            max_steps=5,
        )

        result = controller.run("Run both")

        self.assertEqual(result, "Both operations finished.")
        # Returned order preserved
        self.assertEqual(tool_a.invocations, [{"arg": "first"}])
        self.assertEqual(tool_b.invocations, [{"arg": "second"}])

        # Observations added in order:
        # system (0), user (1), assistant_tool_calls (2), tool_a (3), tool_b (4), assistant_final (5)
        self.assertEqual(len(controller.context), 6)
        self.assertEqual(controller.context[3]["role"], "tool")
        self.assertEqual(controller.context[3]["tool_call_id"], "call_a")
        self.assertEqual(controller.context[4]["role"], "tool")
        self.assertEqual(controller.context[4]["tool_call_id"], "call_b")

    def test_tool_result_error_becomes_observation(self) -> None:
        tool = DummyTool(name="failing_tool", return_error=True)
        self.registry.register(tool)

        llm = MockLLMClient([
            LLMResponse(
                content=None,
                tool_calls=[ToolCall(id="call_err", name="failing_tool", arguments={})],
            ),
            LLMResponse(
                content="I noticed the error and handled it.",
                tool_calls=[],
            ),
        ])

        controller = ReActController(
            llm_client=llm,
            tool_registry=self.registry,
            tool_executor=self.executor,
            max_steps=5,
        )

        result = controller.run("Trigger tool")

        self.assertEqual(result, "I noticed the error and handled it.")
        self.assertEqual(controller.context[3]["role"], "tool")
        self.assertEqual(controller.context[3]["tool_call_id"], "call_err")
        self.assertEqual(controller.context[3]["content"], "Error in failing_tool")

    def test_unknown_tool_becomes_error_observation_without_crashing(self) -> None:
        llm = MockLLMClient([
            LLMResponse(
                content=None,
                tool_calls=[ToolCall(id="call_unk", name="unregistered_tool", arguments={})],
            ),
            LLMResponse(
                content="Recovered from unknown tool.",
                tool_calls=[],
            ),
        ])

        controller = ReActController(
            llm_client=llm,
            tool_registry=self.registry,
            tool_executor=self.executor,
            max_steps=5,
        )

        result = controller.run("Try unknown tool")

        self.assertEqual(result, "Recovered from unknown tool.")
        self.assertEqual(controller.context[3]["role"], "tool")
        self.assertEqual(controller.context[3]["tool_call_id"], "call_unk")
        self.assertIn("not registered", controller.context[3]["content"])

    def test_execution_delegates_to_tool_executor(self) -> None:
        tool = DummyTool(name="spied_tool")
        self.registry.register(tool)

        mock_executor = MagicMock(spec=ToolExecutor)
        mock_executor.execute.return_value = ToolResult(content="Executor result", is_error=False)

        llm = MockLLMClient([
            LLMResponse(
                content=None,
                tool_calls=[ToolCall(id="call_exec", name="spied_tool", arguments={"arg": "test"})],
            ),
            LLMResponse(content="Done", tool_calls=[]),
        ])

        controller = ReActController(
            llm_client=llm,
            tool_registry=self.registry,
            tool_executor=mock_executor,
            max_steps=5,
        )

        controller.run("Execute tool")

        mock_executor.execute.assert_called_once_with(tool, {"arg": "test"}, call_id="call_exec")
        self.assertEqual(controller.context[3]["content"], "Executor result")

    def test_max_steps_stops_infinite_tool_loop(self) -> None:
        tool = DummyTool(name="loop_tool")
        self.registry.register(tool)

        responses = [
            LLMResponse(
                content=None,
                tool_calls=[ToolCall(id=f"call_{i}", name="loop_tool", arguments={})],
            )
            for i in range(10)
        ]
        llm = MockLLMClient(responses)

        controller = ReActController(
            llm_client=llm,
            tool_registry=self.registry,
            tool_executor=self.executor,
            max_steps=3,
        )

        with self.assertRaises(RuntimeError) as ctx:
            controller.run("Loop forever")

        self.assertIn("maximum agent steps (3) reached", str(ctx.exception).lower())
        self.assertEqual(len(llm.recorded_calls), 3)

    def test_content_and_tool_calls_treated_as_tool_turn_not_final_response(self) -> None:
        tool = DummyTool(name="my_tool")
        self.registry.register(tool)

        llm = MockLLMClient([
            LLMResponse(
                content="Thinking: I need to query my_tool first.",
                tool_calls=[ToolCall(id="call_combo", name="my_tool", arguments={"arg": "val"})],
            ),
            LLMResponse(
                content="Final conclusion.",
                tool_calls=[],
            ),
        ])

        controller = ReActController(
            llm_client=llm,
            tool_registry=self.registry,
            tool_executor=self.executor,
            max_steps=5,
        )

        result = controller.run("Do work")

        self.assertEqual(result, "Final conclusion.")
        self.assertEqual(controller.context[2]["content"], "Thinking: I need to query my_tool first.")
        self.assertEqual(len(controller.context[2]["tool_calls"]), 1)

    def test_tools_exposed_from_registry_list_specs_on_each_turn(self) -> None:
        tool = DummyTool(name="visible_tool")
        self.registry.register(tool)

        llm = MockLLMClient([
            LLMResponse(
                content=None,
                tool_calls=[ToolCall(id="call_turn1", name="visible_tool", arguments={})],
            ),
            LLMResponse(content="Finished after turn 2", tool_calls=[]),
        ])

        controller = ReActController(
            llm_client=llm,
            tool_registry=self.registry,
            tool_executor=self.executor,
            max_steps=5,
        )

        controller.run("Hello")

        # Verify LLM received tool specs on BOTH reasoning turns
        self.assertEqual(len(llm.recorded_calls), 2)
        for i, call in enumerate(llm.recorded_calls, start=1):
            specs = call["tools"]
            self.assertIsNotNone(specs, f"Turn {i} should supply tools")
            self.assertEqual(len(specs), 1)
            self.assertEqual(specs[0].name, "visible_tool")

    def test_invalid_max_steps_rejected_in_constructor(self) -> None:
        for bad_steps in [0, -1, True, False, "5"]:
            with self.assertRaises(ValueError):
                ReActController(
                    llm_client=None,
                    tool_registry=self.registry,
                    tool_executor=self.executor,
                    max_steps=bad_steps,  # type: ignore[arg-type]
                )

    def test_lifecycle_logging_emits_structured_events_without_sensitive_payloads(self) -> None:
        tool = DummyTool(name="get_weather")
        self.registry.register(tool)

        llm = MockLLMClient([
            LLMResponse(
                content=None,
                tool_calls=[ToolCall(id="call_1", name="get_weather", arguments={"arg": "SECRET_CITY"})],
            ),
            LLMResponse(content="SECRET_ANSWER", tool_calls=[]),
        ])

        controller = ReActController(
            llm_client=llm,
            tool_registry=self.registry,
            tool_executor=self.executor,
            max_steps=5,
        )

        with self.assertLogs("harness.react", level="DEBUG") as cm:
            controller.run("SECRET_USER_PROMPT")

        logs = "\n".join(cm.output)

        # Verify structured lifecycle event tokens are present
        self.assertIn("run_started run_id=", logs)
        self.assertIn("step_started run_id=", logs)
        self.assertIn("tool_invoked run_id=", logs)
        self.assertIn("tool=get_weather", logs)
        self.assertIn("tool_completed run_id=", logs)
        self.assertIn("outcome=success", logs)
        self.assertIn("run_completed run_id=", logs)
        self.assertIn("steps=2", logs)

        # Verify privacy: sensitive user prompt and raw arguments are not logged in operational logs
        self.assertNotIn("SECRET_USER_PROMPT", logs)
        self.assertNotIn("SECRET_CITY", logs)
        self.assertNotIn("SECRET_ANSWER", logs)

    def test_step_limit_exceeded_logs_warning(self) -> None:
        tool = DummyTool(name="loop_tool")
        self.registry.register(tool)

        llm = MockLLMClient([
            LLMResponse(
                content=None,
                tool_calls=[ToolCall(id=f"call_{i}", name="loop_tool", arguments={})],
            )
            for i in range(5)
        ])

        controller = ReActController(
            llm_client=llm,
            tool_registry=self.registry,
            tool_executor=self.executor,
            max_steps=2,
        )

        with self.assertLogs("harness.react", level="WARNING") as cm:
            with self.assertRaises(RuntimeError):
                controller.run("Loop")

        logs = "\n".join(cm.output)
        self.assertIn("step_limit_exceeded run_id=", logs)
        self.assertIn("max_steps=2", logs)


# =====================================================================
# Memory Context Composition & Provider Contract Regression Tests
# =====================================================================

class StrictRoleValidatingMockLLM:
    """Mock LLM client enforcing strict OpenAI / vLLM chat completion provider contract."""

    def __init__(self, responses: list[LLMResponse]) -> None:
        self.responses = list(responses)
        self.recorded_calls: list[dict[str, Any]] = []

    def chat(
        self,
        messages: Sequence[dict[str, Any]],
        tools: Sequence[ToolSpec] | None = None,
    ) -> LLMResponse:
        self.recorded_calls.append({
            "messages": [dict(m) for m in messages],
            "tools": list(tools) if tools is not None else None,
        })
        # Provider contract: system role may strictly and only appear at index 0
        if any(m.get("role") == "system" for m in messages[1:]):
            raise AssertionError(
                "Provider contract violation: 'system' role is only allowed at index 0. "
                f"Found system messages at indices: {[i for i, m in enumerate(messages) if m.get('role') == 'system']}"
            )
        if not self.responses:
            return LLMResponse(content="Default response", tool_calls=[])
        return self.responses.pop(0)


class FakeMemoryEntry:
    def __init__(self, content: str) -> None:
        self.content = content


class FakeProceduralLesson:
    def __init__(self, guidance: str) -> None:
        self.guidance = guidance


class FakeMemoryManager:
    """Lightweight test double for MemoryManager to verify ReActController composition."""

    def __init__(
        self,
        declarative_memories: list[Any] | None = None,
        procedural_lessons: list[Any] | None = None,
    ) -> None:
        self.declarative_memories = declarative_memories or []
        self.procedural_lessons = procedural_lessons or []
        self.stored_admissions: list[dict[str, Any]] = []

    def retrieve(self, query: str) -> list[Any]:
        return list(self.declarative_memories)

    def format_context(self, memories: list[Any]) -> str:
        if not memories:
            return ""
        lines = ["[RECALLED MEMORY]"]
        for m in memories:
            content = getattr(m, "content", str(m))
            lines.append(f"- {content}")
        return "\n".join(lines)

    def retrieve_procedural(self, tool_names: list[str]) -> list[Any]:
        return list(self.procedural_lessons)

    def format_procedural_context(self, lessons: list[Any]) -> str:
        if not lessons:
            return ""
        lines = ["[PAST TOOL EXPERIENCE]"]
        for l in lessons:
            guidance = getattr(l, "guidance", str(l))
            lines.append(f"- Lesson: {guidance}")
        return "\n".join(lines)

    def admit_and_store(self, content: str, source: Any, metadata: dict[str, Any] | None = None) -> Any:
        self.stored_admissions.append({"content": content, "source": source, "metadata": metadata})

    def admit_procedural_lesson(self, lesson: Any) -> None:
        self.procedural_lessons.append(lesson)


class TestMemoryContextCompositionRegression(unittest.TestCase):
    """Regression tests for Week 2 memory context composition and provider contract invariants."""

    def setUp(self) -> None:
        self.registry = ToolRegistry()
        self.executor = ToolExecutor()

    def test_recalled_memory_keeps_single_leading_system_message(self) -> None:
        """Verify recalled declarative memory is composed into leading system content without extra system messages."""
        mem = FakeMemoryManager(declarative_memories=[FakeMemoryEntry("User prefers window seats on trains.")])
        llm = MockLLMClient([
            LLMResponse(content="Noted preference.", tool_calls=[]),
            LLMResponse(content="You should choose a window seat.", tool_calls=[]),
        ])
        controller = ReActController(
            llm_client=llm,
            tool_registry=self.registry,
            tool_executor=self.executor,
            memory_manager=mem,
        )

        # Turn 1
        controller.run("Remember my seat preference.")
        # Turn 2
        controller.run("Which seat should I pick?")

        # Check Turn 2 outbound messages
        recorded = llm.recorded_calls[1]["messages"]
        system_indices = [i for i, m in enumerate(recorded) if m.get("role") == "system"]
        self.assertEqual(system_indices, [0], "Outbound request must contain exactly one system message at index 0")
        self.assertIn("[RECALLED MEMORY]", recorded[0]["content"])
        self.assertIn("window seats", recorded[0]["content"])

    def test_procedural_and_declarative_memory_share_leading_system_message(self) -> None:
        """Verify procedural guidance and declarative memory both fold into messages[0] with 0 extra system messages."""
        mem = FakeMemoryManager(
            declarative_memories=[FakeMemoryEntry("Preferred departure Passau Hbf.")],
            procedural_lessons=[FakeProceduralLesson("When searching stations, use query argument.")],
        )
        llm = MockLLMClient([LLMResponse(content="Departure from Passau.", tool_calls=[])])
        controller = ReActController(
            llm_client=llm,
            tool_registry=self.registry,
            tool_executor=self.executor,
            memory_manager=mem,
            enable_procedural_memory=True,
        )

        controller.run("Find train from my home station.")

        recorded = llm.recorded_calls[0]["messages"]
        system_indices = [i for i, m in enumerate(recorded) if m.get("role") == "system"]
        self.assertEqual(system_indices, [0])
        self.assertIn("[PAST TOOL EXPERIENCE]", recorded[0]["content"])
        self.assertIn("query argument", recorded[0]["content"])
        self.assertIn("[RECALLED MEMORY]", recorded[0]["content"])
        self.assertIn("Passau Hbf", recorded[0]["content"])

    def test_memory_augmentation_is_turn_scoped(self) -> None:
        """Verify controller.context is never permanently mutated by turn memory retrieval across turns."""
        base_system_prompt = "You are a helpful assistant running inside an agent harness."
        mem = FakeMemoryManager(declarative_memories=[FakeMemoryEntry("Fact Alpha")])
        llm = MockLLMClient([
            LLMResponse(content="Ack 1", tool_calls=[]),
            LLMResponse(content="Ack 2", tool_calls=[]),
        ])
        controller = ReActController(
            llm_client=llm,
            tool_registry=self.registry,
            tool_executor=self.executor,
            system_prompt=base_system_prompt,
            memory_manager=mem,
        )

        # Before any turn
        self.assertEqual(controller.context[0]["content"], base_system_prompt)

        # Turn 1
        controller.run("Turn 1 input")
        self.assertEqual(controller.context[0]["content"], base_system_prompt, "Canonical context must NOT be mutated")

        # Turn 2 with different memory
        mem.declarative_memories = [FakeMemoryEntry("Fact Beta")]
        controller.run("Turn 2 input")
        self.assertEqual(controller.context[0]["content"], base_system_prompt, "Canonical context must remain pristine")

        # In Turn 2 outbound messages, Fact Alpha from Turn 1 must NOT have accumulated into the system prompt
        turn2_recorded = llm.recorded_calls[1]["messages"]
        self.assertNotIn("Fact Alpha", turn2_recorded[0]["content"])
        self.assertIn("Fact Beta", turn2_recorded[0]["content"])

    def test_multiturn_history_order_is_preserved(self) -> None:
        """Verify history [system, user, assistant, tool, assistant] outputs [system, user, assistant, tool, assistant, user]."""
        mem = FakeMemoryManager(declarative_memories=[FakeMemoryEntry("Saved preference")])
        llm = MockLLMClient([LLMResponse(content="Final turn answer", tool_calls=[])])
        controller = ReActController(
            llm_client=llm,
            tool_registry=self.registry,
            tool_executor=self.executor,
            memory_manager=mem,
        )

        # Pre-seed multi-turn history including tool call
        controller.context = [
            {"role": "system", "content": "Base instructions"},
            {"role": "user", "content": "Previous prompt"},
            {"role": "assistant", "content": None, "tool_calls": [{"id": "tc1", "name": "dummy"}]},
            {"role": "tool", "tool_call_id": "tc1", "content": "Tool output"},
            {"role": "assistant", "content": "Previous answer"},
        ]

        controller.run("Next prompt")

        recorded = llm.recorded_calls[0]["messages"]
        roles = [m.get("role") for m in recorded]
        expected_roles = ["system", "user", "assistant", "tool", "assistant", "user"]
        self.assertEqual(roles, expected_roles)
        self.assertIn("[RECALLED MEMORY]", recorded[0]["content"])
        self.assertEqual(recorded[-1]["content"], "Next prompt")

    def test_canonical_context_invariant_violation_raises(self) -> None:
        """Verify _build_request_context strictly enforces system_indices == [0] on canonical base_context."""
        controller = ReActController(
            llm_client=MockLLMClient([]),
            tool_registry=self.registry,
            tool_executor=self.executor,
        )

        # Case 1: System message outside index 0
        malformed_context_late_system = [
            {"role": "system", "content": "Prompt 1"},
            {"role": "user", "content": "User 1"},
            {"role": "system", "content": "Illegal system message"},
        ]
        with self.assertRaises(ValueError) as ctx1:
            controller._build_request_context(
                base_context=malformed_context_late_system,
                user_message={"role": "user", "content": "User 2"},
                recalled_formatted="[RECALLED MEMORY]\n- fact",
            )
        self.assertIn("exactly one system message at index 0", str(ctx1.exception))

        # Case 2: No system message in canonical context
        malformed_context_no_system = [
            {"role": "user", "content": "User 1"},
            {"role": "assistant", "content": "Assistant 1"},
        ]
        with self.assertRaises(ValueError) as ctx2:
            controller._build_request_context(
                base_context=malformed_context_no_system,
                user_message={"role": "user", "content": "User 2"},
                recalled_formatted="[RECALLED MEMORY]\n- fact",
            )
        self.assertIn("exactly one system message at index 0", str(ctx2.exception))

        # Case 3: Empty canonical context
        with self.assertRaises(ValueError) as ctx3:
            controller._build_request_context(
                base_context=[],
                user_message={"role": "user", "content": "User 1"},
                recalled_formatted="[RECALLED MEMORY]\n- fact",
            )
        self.assertIn("exactly one system message at index 0", str(ctx3.exception))

        # Case 4: Multiple system messages starting at index 0
        malformed_context_multiple_systems = [
            {"role": "system", "content": "System 1"},
            {"role": "system", "content": "System 2"},
            {"role": "user", "content": "User 1"},
        ]
        with self.assertRaises(ValueError) as ctx4:
            controller._build_request_context(
                base_context=malformed_context_multiple_systems,
                user_message={"role": "user", "content": "User 2"},
            )
        self.assertIn("exactly one system message at index 0", str(ctx4.exception))

    def test_no_memory_path_disabled_is_behaviorally_unchanged(self) -> None:
        """Verify when memory_manager is None (memory disabled), outbound messages match ordinary non-memory behavior."""
        llm = MockLLMClient([LLMResponse(content="Response without memory", tool_calls=[])])
        controller = ReActController(
            llm_client=llm,
            tool_registry=self.registry,
            tool_executor=self.executor,
            memory_manager=None,
        )
        controller.run("Hello world")

        recorded = llm.recorded_calls[0]["messages"]
        roles = [m.get("role") for m in recorded]
        self.assertEqual(roles, ["system", "user"])
        self.assertNotIn("[RECALLED MEMORY]", recorded[0]["content"])
        self.assertNotIn("[PAST TOOL EXPERIENCE]", recorded[0]["content"])

    def test_no_memory_path_zero_retrieval_is_behaviorally_unchanged(self) -> None:
        """Verify when memory is enabled but retrieval returns 0 entries, outbound messages match ordinary non-memory behavior."""
        empty_mem = FakeMemoryManager(declarative_memories=[], procedural_lessons=[])
        llm = MockLLMClient([LLMResponse(content="Response with zero memory hits", tool_calls=[])])
        controller = ReActController(
            llm_client=llm,
            tool_registry=self.registry,
            tool_executor=self.executor,
            memory_manager=empty_mem,
            enable_procedural_memory=True,
        )
        controller.run("Hello world")

        recorded = llm.recorded_calls[0]["messages"]
        roles = [m.get("role") for m in recorded]
        self.assertEqual(roles, ["system", "user"])
        self.assertNotIn("[RECALLED MEMORY]", recorded[0]["content"])
        self.assertNotIn("[PAST TOOL EXPERIENCE]", recorded[0]["content"])

    def test_strict_provider_contract_integration_prevents_400(self) -> None:
        """Integration test: StrictRoleValidatingMockLLM simulates strict OpenAI / vLLM provider contract.

        Recreates the exact production regression: On Turn 2+, prior conversation history
        ([system, user_1, assistant_1]) exists AND retrieved memory is active.
        Old implementation appended system messages mid-conversation, triggering 400 BadRequest / AssertionError.
        New implementation preserves [system (augmented), user_1, assistant_1, user_2] with system strictly at index 0.
        """
        mem = FakeMemoryManager(declarative_memories=[FakeMemoryEntry("Prefers ICE over RE trains.")])
        strict_llm = StrictRoleValidatingMockLLM([
            LLMResponse(content="Turn 1 answer.", tool_calls=[]),
            LLMResponse(content="Turn 2 answer.", tool_calls=[]),
            LLMResponse(content="Turn 3 answer.", tool_calls=[]),
        ])
        controller = ReActController(
            llm_client=strict_llm,
            tool_registry=self.registry,
            tool_executor=self.executor,
            memory_manager=mem,
        )

        # Multi-turn execution
        r1 = controller.run("Turn 1: Note I like ICE.")
        self.assertEqual(r1, "Turn 1 answer.")

        r2 = controller.run("Turn 2: What train do I like?")
        self.assertEqual(r2, "Turn 2 answer.")

        r3 = controller.run("Turn 3: Plan my journey.")
        self.assertEqual(r3, "Turn 3 answer.")

        # Verify all 3 turns were received by LLM and satisfied provider invariant
        self.assertEqual(len(strict_llm.recorded_calls), 3)
        for turn_idx, call in enumerate(strict_llm.recorded_calls):
            msgs = call["messages"]
            sys_indices = [i for i, m in enumerate(msgs) if m.get("role") == "system"]
            self.assertEqual(sys_indices, [0], f"Turn {turn_idx + 1} violated single leading system invariant")

        # Explicitly verify the exact Turn 2 regression shape:
        # Prior history (user_1, assistant_1) present, retrieved memory active, exactly 1 system message at index 0
        turn2_msgs = strict_llm.recorded_calls[1]["messages"]
        turn2_roles = [m.get("role") for m in turn2_msgs]
        self.assertEqual(turn2_roles, ["system", "user", "assistant", "user"])
        self.assertIn("[RECALLED MEMORY]", turn2_msgs[0]["content"])
        self.assertIn("Prefers ICE over RE trains", turn2_msgs[0]["content"])
        self.assertEqual(turn2_msgs[1]["content"], "Turn 1: Note I like ICE.")
        self.assertEqual(turn2_msgs[2]["content"], "Turn 1 answer.")
        self.assertEqual(turn2_msgs[3]["content"], "Turn 2: What train do I like?")


if __name__ == "__main__":
    unittest.main()
