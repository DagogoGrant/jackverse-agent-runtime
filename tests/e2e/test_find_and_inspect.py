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


class TestFindAndInspectE2E(unittest.TestCase):
    """E2E Acceptance Test: Multi-step directory traversal and file inspection.

    Validates that ReActController can execute a multi-tool sequence:
    1. Inspect directory hierarchy (list_directory)
    2. Read target file content (read_file)
    3. Synthesize and report findings in final answer
    """

    def setUp(self) -> None:
        self._temp_dir = tempfile.TemporaryDirectory()
        self.workspace_dir = Path(self._temp_dir.name).resolve()

        # Deterministic fixture structure:
        # - ABC/data/notes.txt (exactly 5 lines)
        # - ABC/empty/ (empty directory to test empty-dir handling)
        # - readme.md (unrelated file containing Kartoffelsalat to verify non-interference)
        data_dir = self.workspace_dir / "ABC" / "data"
        data_dir.mkdir(parents=True)
        empty_dir = self.workspace_dir / "ABC" / "empty"
        empty_dir.mkdir(parents=True)

        notes_file = data_dir / "notes.txt"
        notes_content = (
            "Task Overview: Week 1 Deliverables\n"
            "Item 1: Implement workspace containment boundary\n"
            "Item 2: Implement bounded ReAct controller\n"
            "Item 3: Implement filesystem capabilities\n"
            "Item 4: Validate with deterministic acceptance tests\n"
        )
        notes_file.write_text(notes_content, encoding="utf-8")

        readme_file = self.workspace_dir / "readme.md"
        readme_file.write_text("Kartoffelsalat\n", encoding="utf-8")

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

    def test_find_and_inspect_end_to_end_flow(self) -> None:
        """Execute and verify the find-and-inspect multi-tool ReAct flow."""
        llm = MockLLMClient([
            # Turn 1: Agent inspects the ABC directory via list_directory
            LLMResponse(
                content=None,
                tool_calls=[
                    ToolCall(
                        id="call_list_abc",
                        name="list_directory",
                        arguments={"path": "ABC"},
                    )
                ],
            ),
            # Turn 2: Agent reads ABC/data/notes.txt
            LLMResponse(
                content=None,
                tool_calls=[
                    ToolCall(
                        id="call_read_notes",
                        name="read_file",
                        arguments={"path": "ABC/data/notes.txt"},
                    )
                ],
            ),
            # Turn 3: Agent reports line count
            LLMResponse(
                content="The file ABC/data/notes.txt contains 5 lines.",
                tool_calls=[],
            ),
        ])

        controller = ReActController(
            llm_client=llm,
            tool_registry=self.registry,
            tool_executor=self.executor,
            max_steps=10,
        )

        user_prompt = "Inspect the ABC directory and report how many lines ABC/data/notes.txt contains."
        final_answer = controller.run(user_prompt)

        # 1. Verify answer correctly reports 5 lines
        self.assertIn("5 lines", final_answer)

        # 2. Verify tool interaction trace
        tool_turns = [msg for msg in controller.context if msg.get("role") == "tool"]
        self.assertEqual(len(tool_turns), 2)
        self.assertIn("DIR data", tool_turns[0]["content"])
        self.assertIn("DIR empty", tool_turns[0]["content"])
        self.assertIn("Task Overview", tool_turns[1]["content"])

        # 3. Verify untouched files remain intact
        readme_file = self.workspace_dir / "readme.md"
        self.assertEqual(readme_file.read_text(encoding="utf-8"), "Kartoffelsalat\n")


if __name__ == "__main__":
    unittest.main()
