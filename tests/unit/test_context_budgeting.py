"""Unit tests for Phase C: Observation Budgeting & Context Management (Hypothesis H7)."""

import json
from pathlib import Path
import tempfile
import unittest

from harness.agent.budget import ExecutionBudget
from harness.agent.react import ReActController
from harness.tools.base import ErrorCode, Tool, ToolResult, ToolSpec
from harness.tools.executor import ToolExecutor
from harness.tools.filesystem import (
    MAX_COUNTED_MATCHES,
    MAX_SEARCH_RESULTS,
    ReadFileTool,
    SearchFilesTool,
)
from harness.tools.registry import ToolRegistry
from harness.tools.search_types import SearchResult
from harness.tools.validation import ToolContractValidator
from harness.tools.workspace import Workspace


class DummyLargeTool:
    """Mock tool producing arbitrary length string output for envelope testing."""

    def __init__(self, size: int) -> None:
        self._size = size
        self._spec = ToolSpec(
            name="dummy_large",
            description="Returns large payload.",
            input_schema={"type": "object", "properties": {}, "additionalProperties": False},
        )

    @property
    def spec(self) -> ToolSpec:
        return self._spec

    def execute(self, arguments: dict[str, object]) -> ToolResult:
        return ToolResult(content="X" * self._size, is_error=False)


class TestContextBudgeting(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.workspace_dir = Path(self.tmp_dir.name).resolve()
        self.workspace = Workspace(self.workspace_dir)
        self.executor = ToolExecutor()

    def tearDown(self) -> None:
        self.tmp_dir.cleanup()

    # -------------------------------------------------------------
    # 1. SearchResult Dataclass & Text Formatting
    # -------------------------------------------------------------

    def test_search_result_exact_formatting(self) -> None:
        sr = SearchResult(
            matches=["a.txt:1: hello", "b.txt:2: hello"],
            returned_count=2,
            total_count=2,
            truncated=False,
            count_complete=True,
        )
        self.assertEqual(sr.format_text(), "a.txt:1: hello\nb.txt:2: hello")
        self.assertFalse(sr.truncated)
        self.assertTrue(sr.count_complete)

    def test_search_result_truncated_complete_count_formatting(self) -> None:
        sr = SearchResult(
            matches=["file.txt:1: match", "file.txt:2: match"],
            returned_count=2,
            total_count=50,
            truncated=True,
            count_complete=True,
        )
        text = sr.format_text()
        self.assertIn("file.txt:1: match\nfile.txt:2: match", text)
        self.assertIn("[TRUNCATED: showing 2 of 50 matches. Refine query or path to narrow results.]", text)

    def test_search_result_truncated_scan_ceiling_formatting(self) -> None:
        sr = SearchResult(
            matches=["file.txt:1: match"],
            returned_count=1,
            total_count=10_000,
            truncated=True,
            count_complete=False,
        )
        text = sr.format_text()
        self.assertIn("[TRUNCATED: showing 1 of at least 10000 matches; scan ceiling reached. Refine query or path to narrow results.]", text)

    def test_search_result_empty_formatting(self) -> None:
        sr = SearchResult(
            matches=[],
            returned_count=0,
            total_count=0,
            truncated=False,
            count_complete=True,
        )
        self.assertEqual(sr.format_text(), "No matches found.")

    def test_search_result_json_serialization(self) -> None:
        sr = SearchResult(
            matches=["file.txt:1: item"],
            returned_count=1,
            total_count=1,
            truncated=False,
            count_complete=True,
        )
        parsed = json.loads(sr.to_json())
        self.assertEqual(parsed["matches"], ["file.txt:1: item"])
        self.assertEqual(parsed["returned_count"], 1)
        self.assertEqual(parsed["total_count"], 1)
        self.assertFalse(parsed["truncated"])
        self.assertTrue(parsed["count_complete"])

    # -------------------------------------------------------------
    # 2. SearchFilesTool Execution & Counting Scenarios
    # -------------------------------------------------------------

    def test_search_files_exact_small_exhaustive(self) -> None:
        lines = [f"Line {i}: target item" for i in range(5)]
        (self.workspace_dir / "small.txt").write_text("\n".join(lines), encoding="utf-8")
        tool = SearchFilesTool(self.workspace)

        result = self.executor.execute(tool, {"query": "target item", "path": "small.txt"})
        self.assertFalse(result.is_error)
        lines_out = result.content.splitlines()
        self.assertEqual(len(lines_out), 5)
        self.assertNotIn("[TRUNCATED", result.content)

    def test_search_files_truncated_with_complete_count(self) -> None:
        lines = [f"Record {i:03d}: findme keyword" for i in range(200)]
        (self.workspace_dir / "large.txt").write_text("\n".join(lines), encoding="utf-8")
        tool = SearchFilesTool(self.workspace)

        result = self.executor.execute(tool, {"query": "findme", "path": "large.txt"})
        self.assertFalse(result.is_error)
        lines_out = result.content.splitlines()
        # Default max_results = 50, so 50 lines + 1 trailer line
        self.assertEqual(len(lines_out), 51)
        self.assertIn("[TRUNCATED: showing 50 of 200 matches. Refine query or path to narrow results.]", lines_out[-1])

    def test_search_files_json_mode(self) -> None:
        lines = [f"Entry {i}: data token" for i in range(30)]
        (self.workspace_dir / "data.txt").write_text("\n".join(lines), encoding="utf-8")
        tool = SearchFilesTool(self.workspace)

        result = self.executor.execute(tool, {"query": "data token", "output_format": "json", "max_results": 10})
        self.assertFalse(result.is_error)
        parsed = json.loads(result.content)
        self.assertEqual(len(parsed["matches"]), 10)
        self.assertEqual(parsed["returned_count"], 10)
        self.assertEqual(parsed["total_count"], 30)
        self.assertTrue(parsed["truncated"])
        self.assertTrue(parsed["count_complete"])

    def test_search_files_match_counting_ceiling(self) -> None:
        # Create 12,000 matching lines in a file
        lines = ["keyword match" for _ in range(12_000)]
        (self.workspace_dir / "ceiling.txt").write_text("\n".join(lines), encoding="utf-8")
        tool = SearchFilesTool(self.workspace)

        result = self.executor.execute(tool, {"query": "keyword match", "path": "ceiling.txt", "max_results": 20})
        self.assertFalse(result.is_error)
        lines_out = result.content.splitlines()
        self.assertEqual(len(lines_out), 21)
        self.assertIn(f"[TRUNCATED: showing 20 of at least {MAX_COUNTED_MATCHES} matches; scan ceiling reached.", lines_out[-1])

    # -------------------------------------------------------------
    # 3. Semantic & Syntactic Contract Validation
    # -------------------------------------------------------------

    def test_search_files_semantic_validation_max_results(self) -> None:
        tool = SearchFilesTool(self.workspace)
        # Out of bounds: 0
        res_zero = self.executor.execute(tool, {"query": "x", "max_results": 0})
        self.assertTrue(res_zero.is_error)
        self.assertEqual(res_zero.error_code, ErrorCode.INVALID_ARGUMENT)
        self.assertIn("must be an integer between 1 and 500", res_zero.content)

        # Out of bounds: 501
        res_over = self.executor.execute(tool, {"query": "x", "max_results": 501})
        self.assertTrue(res_over.is_error)
        self.assertEqual(res_over.error_code, ErrorCode.INVALID_ARGUMENT)

        # Non-integer (syntactic check by ToolContractValidator)
        res_str = self.executor.execute(tool, {"query": "x", "max_results": "50"})
        self.assertTrue(res_str.is_error)
        self.assertEqual(res_str.error_code, ErrorCode.INVALID_ARGUMENT)
        self.assertIn("must be an integer", res_str.content)

    def test_search_files_semantic_validation_output_format(self) -> None:
        tool = SearchFilesTool(self.workspace)
        # Unsupported format
        res_bad = self.executor.execute(tool, {"query": "x", "output_format": "yaml"})
        self.assertTrue(res_bad.is_error)
        self.assertEqual(res_bad.error_code, ErrorCode.INVALID_ARGUMENT)
        self.assertIn("must be 'text' or 'json'", res_bad.content)

        # Non-string format (syntactic check)
        res_int = self.executor.execute(tool, {"query": "x", "output_format": 123})
        self.assertTrue(res_int.is_error)
        self.assertEqual(res_int.error_code, ErrorCode.INVALID_ARGUMENT)

    # -------------------------------------------------------------
    # 4. Harness Observation Ceiling & Strict Invariant Testing
    # -------------------------------------------------------------

    def test_observation_ceiling_strict_invariant(self) -> None:
        """Verify len(final_envelope) <= max_observation_chars across multiple configurations."""
        test_cases = [
            (50_000, 4_000),
            (100_000, 16_000),
            (1_000, 200),
            (500, 100),
            (250, 60),  # Very tight envelope limit
        ]
        for payload_size, ceiling in test_cases:
            tool = DummyLargeTool(size=payload_size)
            executor = ToolExecutor(max_observation_chars=ceiling)
            result = executor.execute(tool, {})

            self.assertFalse(result.is_error)
            # Strict Phase C invariant:
            self.assertLessEqual(
                len(result.content),
                ceiling,
                f"Failed for payload {payload_size} and ceiling {ceiling}: got len {len(result.content)}",
            )
            # Check envelope markers
            if ceiling >= 100:
                self.assertIn("[OBSERVATION PARTIALLY SHOWN]", result.content)
                self.assertIn(f"original_chars: {payload_size}", result.content)
                self.assertIn("content:\n", result.content)

    def test_observation_ceiling_unaffected_when_within_budget(self) -> None:
        tool = DummyLargeTool(size=500)
        executor = ToolExecutor(max_observation_chars=1000)
        result = executor.execute(tool, {})

        self.assertFalse(result.is_error)
        self.assertEqual(len(result.content), 500)
        self.assertNotIn("[OBSERVATION PARTIALLY SHOWN]", result.content)

    def test_observation_ceiling_applied_to_read_file(self) -> None:
        (self.workspace_dir / "big.txt").write_text("A" * 20_000, encoding="utf-8")
        tool = ReadFileTool(self.workspace)
        executor = ToolExecutor(max_observation_chars=2000)

        result = executor.execute(tool, {"path": "big.txt"})
        self.assertFalse(result.is_error)
        self.assertLessEqual(len(result.content), 2000)
        self.assertIn("[OBSERVATION PARTIALLY SHOWN]", result.content)
        self.assertIn("original_chars: 20000", result.content)

    # -------------------------------------------------------------
    # 5. Single Source of Truth Wiring
    # -------------------------------------------------------------

    def test_budget_wiring_to_executor_in_react_controller(self) -> None:
        budget = ExecutionBudget(max_steps=5, max_observation_chars=3500)
        executor = ToolExecutor()
        self.assertIsNone(executor.max_observation_chars)

        registry = ToolRegistry()
        controller = ReActController(
            llm_client=None,  # type: ignore
            tool_registry=registry,
            tool_executor=executor,
            budget=budget,
        )
        # Executor receives the authoritative ceiling from ExecutionBudget
        self.assertEqual(executor.max_observation_chars, 3500)


if __name__ == "__main__":
    unittest.main()
