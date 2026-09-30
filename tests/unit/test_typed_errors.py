"""Unit tests for typed ErrorCode taxonomy and failure classification (Hypothesis H3)."""

from pathlib import Path
import tempfile
import unittest

from harness.agent.budget import ExecutionBudget
from harness.agent.react import ReActController
from harness.llm.client import LLMResponse, ToolCall
from harness.tools.base import ErrorCode, ToolResult, ToolSpec
from harness.tools.executor import ToolExecutor
from harness.tools.filesystem import (
    CreateDirectoryTool,
    CreateFileTool,
    ModifyFileTool,
    ReadFileTool,
)
from harness.tools.registry import ToolRegistry
from harness.tools.workspace import Workspace


class TestTypedErrors(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.workspace_dir = Path(self.temp_dir.name).resolve()
        self.workspace = Workspace(self.workspace_dir)
        self.executor = ToolExecutor()

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_tool_result_post_init_invariants(self) -> None:
        # Success result with error_code raises ValueError
        with self.assertRaises(ValueError):
            ToolResult(content="ok", is_error=False, error_code=ErrorCode.INVALID_ARGUMENT)

        # Legacy error without explicit code defaults to INTERNAL_ERROR (migration compromise)
        legacy_err = ToolResult(content="unclassified error", is_error=True)
        self.assertEqual(legacy_err.error_code, ErrorCode.INTERNAL_ERROR)

        # Explicitly categorized error retains its code
        explicit_err = ToolResult(content="missing file", is_error=True, error_code=ErrorCode.NOT_FOUND)
        self.assertEqual(explicit_err.error_code, ErrorCode.NOT_FOUND)

    def test_read_file_not_found_error_code(self) -> None:
        tool = ReadFileTool(self.workspace)
        result = self.executor.execute(tool, {"path": "nonexistent.txt"})
        self.assertTrue(result.is_error)
        self.assertEqual(result.error_code, ErrorCode.NOT_FOUND)

    def test_create_file_already_exists_error_code(self) -> None:
        (self.workspace_dir / "exists.txt").write_text("initial", encoding="utf-8")
        tool = CreateFileTool(self.workspace)
        result = self.executor.execute(tool, {"path": "exists.txt", "content": "overwrite"})
        self.assertTrue(result.is_error)
        self.assertEqual(result.error_code, ErrorCode.ALREADY_EXISTS)

    def test_modify_file_not_found_and_ambiguous_error_codes(self) -> None:
        (self.workspace_dir / "target.txt").write_text("repeat repeat\n", encoding="utf-8")
        tool = ModifyFileTool(self.workspace)

        # Not found
        res_nf = self.executor.execute(tool, {"path": "target.txt", "old_text": "missing", "new_text": "x"})
        self.assertTrue(res_nf.is_error)
        self.assertEqual(res_nf.error_code, ErrorCode.NOT_FOUND)

        # Ambiguous
        res_amb = self.executor.execute(tool, {"path": "target.txt", "old_text": "repeat", "new_text": "x"})
        self.assertTrue(res_amb.is_error)
        self.assertEqual(res_amb.error_code, ErrorCode.AMBIGUOUS)

    def test_boundary_violation_error_code(self) -> None:
        tool = ReadFileTool(self.workspace)
        result = self.executor.execute(tool, {"path": "../../etc/passwd"})
        self.assertTrue(result.is_error)
        self.assertEqual(result.error_code, ErrorCode.BOUNDARY_VIOLATION)

    def test_tool_errors_are_observed_not_retried_by_harness(self) -> None:
        """Tool errors should be returned to LLM as an observation, not trigger transport retries."""
        responses = [
            # Model asks for non-existent file
            LLMResponse(content=None, tool_calls=[ToolCall(id="c1", name="read_file", arguments={"path": "ghost.txt"})]),
            # Model observes the error and explains to user
            LLMResponse(content="The file ghost.txt was not found on disk."),
        ]

        class TrackingLLMClient:
            def __init__(self, responses):
                self._responses = responses
                self.chat_calls = 0

            def chat(self, messages, tools=None):
                resp = self._responses[self.chat_calls]
                self.chat_calls += 1
                return resp

        llm = TrackingLLMClient(responses)
        registry = ToolRegistry()
        registry.register(ReadFileTool(self.workspace))
        controller = ReActController(
            llm_client=llm,
            tool_registry=registry,
            tool_executor=self.executor,
            budget=ExecutionBudget(max_steps=5),
        )

        result = controller.run_turn("Read ghost.txt")
        self.assertTrue(result.is_success)
        self.assertEqual(result.final_text, "The file ghost.txt was not found on disk.")
        # Exactly 2 LLM turns: no transport retry attempts
        self.assertEqual(llm.chat_calls, 2)
        # Context contains tool observation
        tool_obs = [m for m in controller.context if m.get("role") == "tool"]
        self.assertEqual(len(tool_obs), 1)
        self.assertIn("file does not exist", tool_obs[0]["content"])


if __name__ == "__main__":
    unittest.main()
