"""Unit tests for ExecutionBudget, TerminationReason, and RunResult (Hypothesis H1)."""

from pathlib import Path
import tempfile
import time
import unittest

from harness.agent.budget import ExecutionBudget, RunResult, TerminationReason
from harness.agent.react import ReActController
from harness.llm.client import LLMResponse, ToolCall
from harness.tools.base import ToolResult, ToolSpec
from harness.tools.executor import ToolExecutor
from harness.tools.registry import ToolRegistry


class DummyTool:
    def __init__(self, name: str = "dummy") -> None:
        self._spec = ToolSpec(
            name=name,
            description="Dummy tool for testing budget and limits.",
            input_schema={"type": "object", "properties": {}, "additionalProperties": False},
        )

    @property
    def spec(self) -> ToolSpec:
        return self._spec

    def execute(self, arguments: dict[str, object]) -> ToolResult:
        return ToolResult(content="dummy output", is_error=False)


class MockLLMClient:
    def __init__(self, responses: list[LLMResponse]) -> None:
        self._responses = list(responses)
        self._idx = 0

    def chat(self, messages: list[dict], tools: list = None) -> LLMResponse:
        if self._idx >= len(self._responses):
            return LLMResponse(content="Done.")
        resp = self._responses[self._idx]
        self._idx += 1
        return resp


class TestExecutionBudget(unittest.TestCase):
    def test_budget_field_validation(self) -> None:
        budget = ExecutionBudget(max_steps=5, max_tool_calls=12, max_runtime_seconds=30.0)
        self.assertEqual(budget.max_steps, 5)
        self.assertEqual(budget.max_tool_calls, 12)
        self.assertEqual(budget.max_runtime_seconds, 30.0)

        # Invalid step types/values
        with self.assertRaises(ValueError):
            ExecutionBudget(max_steps=0)
        with self.assertRaises(ValueError):
            ExecutionBudget(max_steps=True)  # type: ignore

        # Invalid tool call types/values
        with self.assertRaises(ValueError):
            ExecutionBudget(max_tool_calls=-1)
        with self.assertRaises(ValueError):
            ExecutionBudget(max_tool_calls=False)  # type: ignore

        # Invalid runtime seconds
        with self.assertRaises(ValueError):
            ExecutionBudget(max_runtime_seconds=0.0)
        with self.assertRaises(ValueError):
            ExecutionBudget(max_runtime_seconds=True)  # type: ignore

    def test_step_budget_termination_reason(self) -> None:
        responses = [
            LLMResponse(content=None, tool_calls=[ToolCall(id="1", name="dummy", arguments={})]),
            LLMResponse(content=None, tool_calls=[ToolCall(id="2", name="dummy", arguments={})]),
        ]
        registry = ToolRegistry()
        registry.register(DummyTool())
        controller = ReActController(
            llm_client=MockLLMClient(responses),
            tool_registry=registry,
            tool_executor=ToolExecutor(),
            budget=ExecutionBudget(max_steps=1),
        )

        result: RunResult = controller.run_turn("Do something")
        self.assertFalse(result.is_success)
        self.assertEqual(result.termination_reason, TerminationReason.STEP_BUDGET_EXCEEDED)
        self.assertEqual(result.steps, 1)
        self.assertIn("Maximum agent steps", result.error or "")

        # Calling backward-compatible run() raises RuntimeError
        with self.assertRaises(RuntimeError) as ctx:
            controller.run("Do something")
        self.assertIn("Maximum agent steps", str(ctx.exception))

    def test_tool_budget_accounting_checks_before_execution(self) -> None:
        # Turn with 3 tool calls requested, but budget is max_tool_calls=2
        responses = [
            LLMResponse(
                content=None,
                tool_calls=[
                    ToolCall(id="1", name="dummy", arguments={}),
                    ToolCall(id="2", name="dummy", arguments={}),
                    ToolCall(id="3", name="dummy", arguments={}),
                ],
            ),
        ]
        registry = ToolRegistry()
        registry.register(DummyTool())
        controller = ReActController(
            llm_client=MockLLMClient(responses),
            tool_registry=registry,
            tool_executor=ToolExecutor(),
            budget=ExecutionBudget(max_steps=10, max_tool_calls=2),
        )

        result: RunResult = controller.run_turn("Run tools")
        self.assertFalse(result.is_success)
        self.assertEqual(result.termination_reason, TerminationReason.TOOL_BUDGET_EXCEEDED)
        # Verify tool_calls represents actually executed calls (exactly 2, not 3)
        self.assertEqual(result.tool_calls, 2)
        self.assertIn("Maximum tool calls budget (2) reached", result.error or "")

    def test_time_budget_cooperative_termination(self) -> None:
        class SleepingLLMClient:
            def chat(self, messages: list[dict], tools: list = None) -> LLMResponse:
                time.sleep(0.05)
                return LLMResponse(content=None, tool_calls=[ToolCall(id="1", name="dummy", arguments={})])

        registry = ToolRegistry()
        registry.register(DummyTool())
        controller = ReActController(
            llm_client=SleepingLLMClient(),
            tool_registry=registry,
            tool_executor=ToolExecutor(),
            budget=ExecutionBudget(max_steps=10, max_tool_calls=10, max_runtime_seconds=0.03),
        )

        result: RunResult = controller.run_turn("Run slow")
        self.assertFalse(result.is_success)
        self.assertEqual(result.termination_reason, TerminationReason.TIME_BUDGET_EXCEEDED)
        self.assertIn("Execution time budget", result.error or "")

    def test_final_answer_produces_complete_run_result(self) -> None:
        responses = [
            LLMResponse(content=None, tool_calls=[ToolCall(id="1", name="dummy", arguments={})]),
            LLMResponse(content="Final successful answer."),
        ]
        registry = ToolRegistry()
        registry.register(DummyTool())
        controller = ReActController(
            llm_client=MockLLMClient(responses),
            tool_registry=registry,
            tool_executor=ToolExecutor(),
            budget=ExecutionBudget(max_steps=5, max_tool_calls=5),
        )

        result: RunResult = controller.run_turn("Hello")
        self.assertTrue(result.is_success)
        self.assertEqual(result.termination_reason, TerminationReason.FINAL_ANSWER)
        self.assertEqual(result.final_text, "Final successful answer.")
        self.assertEqual(result.steps, 2)
        self.assertEqual(result.tool_calls, 1)
        self.assertIsNone(result.error)
        self.assertGreaterEqual(result.runtime_seconds, 0.0)


if __name__ == "__main__":
    unittest.main()
