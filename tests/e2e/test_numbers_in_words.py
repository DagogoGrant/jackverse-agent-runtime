from collections.abc import Sequence
from pathlib import Path
import tempfile
from typing import Any
import unittest

from harness.agent.react import ReActController
from harness.llm.client import LLMResponse, ToolCall
from harness.tools.base import ToolSpec
from harness.tools.executor import ToolExecutor
from harness.tools.filesystem import (
    CreateDirectoryTool,
    CreateFileTool,
    ListDirectoryTool,
    ModifyFileTool,
    ReadFileTool,
    SearchFilesTool,
)
from harness.tools.registry import ToolRegistry
from harness.tools.workspace import Workspace


class MockLLMClient:
    """Deterministic mock LLM client for E2E testing."""

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


class TestNumbersInWordsE2E(unittest.TestCase):
    """E2E Acceptance Test: Multi-step directory creation and file generation.

    Validates that ReActController can execute a multi-tool sequence:
    1. Create enclosing output directory (create_directory)
    2. Write generated content to file (create_file)
    3. Return final completion acknowledgement
    """

    def setUp(self) -> None:
        self._temp_dir = tempfile.TemporaryDirectory()
        self.workspace_dir = Path(self._temp_dir.name).resolve()
        self.workspace = Workspace(self.workspace_dir)

        self.registry = ToolRegistry()
        self.registry.register(CreateDirectoryTool(self.workspace))
        self.registry.register(CreateFileTool(self.workspace))
        self.registry.register(ReadFileTool(self.workspace))
        self.registry.register(ListDirectoryTool(self.workspace))
        self.registry.register(SearchFilesTool(self.workspace))
        self.registry.register(ModifyFileTool(self.workspace))

        self.executor = ToolExecutor()

    def tearDown(self) -> None:
        self._temp_dir.cleanup()

    def test_numbers_in_words_end_to_end_flow(self) -> None:
        """Execute and verify the numbers-in-words multi-tool ReAct flow."""
        numbers_content = (
            "one\ntwo\nthree\nfour\nfive\n"
            "six\nseven\neight\nnine\nten\n"
            "eleven\ntwelve\nthirteen\nfourteen\nfifteen\n"
            "sixteen\nseventeen\neighteen\nnineteen\ntwenty\n"
        )

        llm = MockLLMClient([
            # Turn 1: Agent creates output directory via create_directory
            LLMResponse(
                content=None,
                tool_calls=[
                    ToolCall(
                        id="call_create_dir",
                        name="create_directory",
                        arguments={"path": "output"},
                    )
                ],
            ),
            # Turn 2: Agent creates output/numbers.md with numbers 1-20 in words
            LLMResponse(
                content=None,
                tool_calls=[
                    ToolCall(
                        id="call_create_file",
                        name="create_file",
                        arguments={
                            "path": "output/numbers.md",
                            "content": numbers_content,
                        },
                    )
                ],
            ),
            # Turn 3: Agent acknowledges completion
            LLMResponse(
                content="I have created output/numbers.md containing the numbers 1 to 20 written as words.",
                tool_calls=[],
            ),
        ])

        controller = ReActController(
            llm_client=llm,
            tool_registry=self.registry,
            tool_executor=self.executor,
            max_steps=10,
        )

        user_prompt = "Create a file output/numbers.md containing the numbers 1 to 20 written as words."
        final_answer = controller.run(user_prompt)

        # 1. Verify directory creation
        output_dir = self.workspace_dir / "output"
        self.assertTrue(output_dir.is_dir(), "Directory 'output' should exist in workspace")

        # 2. Verify file creation and content
        numbers_file = output_dir / "numbers.md"
        self.assertTrue(numbers_file.is_file(), "File 'output/numbers.md' should exist in workspace")
        file_content = numbers_file.read_text(encoding="utf-8")
        self.assertIn("one", file_content)
        self.assertIn("twenty", file_content)
        for word in ["two", "three", "four", "five", "six", "seven", "eight", "nine", "ten"]:
            self.assertIn(word, file_content)

        # 3. Verify final answer acknowledges completion
        self.assertIn("output/numbers.md", final_answer)
        self.assertIn("1 to 20", final_answer)


if __name__ == "__main__":
    unittest.main()
