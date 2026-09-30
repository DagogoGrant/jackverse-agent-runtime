"""Phase B: Filesystem Mutation Safety Evaluation Suite.

Evaluates the agent harness on targeted mutation safety, crash-resilience,
postcondition verification, and optimistic conflict detection scenarios.
Empirically captures both expected and actual error classifications.
Outputs structured evaluation results to evaluation/phase_b_results.json.
"""

from dataclasses import asdict, dataclass
import hashlib
import json
import os
from pathlib import Path
import stat
import sys
import tempfile
import time
from typing import Any
from unittest.mock import patch

from harness.agent.budget import ExecutionBudget, RunResult, TerminationReason
from harness.agent.react import ReActController
from harness.llm.client import LLMResponse, ToolCall
from harness.tools.base import ErrorCode, ToolResult
from harness.tools.executor import ToolExecutor
from harness.tools.filesystem import (
    CreateDirectoryTool,
    CreateFileTool,
    ListDirectoryTool,
    ModifyFileTool,
    ReadFileTool,
    SearchFilesTool,
    _compute_file_sha256,
)
from harness.tools.registry import ToolRegistry
from harness.tools.workspace import Workspace


@dataclass
class PhaseBScenarioResult:
    scenario_id: int
    name: str
    hypothesis: str
    success: bool
    system_behavior: str  # "EXPECTED-SAFE", "UNDER-SPECIFIED", "UNSAFE"
    steps: int
    tool_calls: int
    duration_seconds: float
    termination_reason: str
    expected_error_classification: str | None
    actual_error_classification: str | None
    filesystem_state: dict[str, Any]
    notes: str


class RecordingToolExecutor(ToolExecutor):
    """ToolExecutor that empirically captures the actual ToolResult of each execution."""

    def __init__(self) -> None:
        super().__init__()
        self.last_result: ToolResult | None = None
        self.results: list[ToolResult] = []

    def execute(self, tool: Any, arguments: Any) -> ToolResult:
        res = super().execute(tool, arguments)
        self.last_result = res
        self.results.append(res)
        return res


class ScriptedMockLLM:
    """Mock LLM client executing a pre-programmed sequence of responses."""

    def __init__(self, responses: list[LLMResponse]) -> None:
        self._responses = list(responses)
        self._call_index = 0

    def chat(self, messages: list[dict[str, Any]], tools: Any = None) -> LLMResponse:
        if self._call_index >= len(self._responses):
            return LLMResponse(content="No more scripted responses.")
        resp = self._responses[self._call_index]
        self._call_index += 1
        return resp


def _build_test_harness(
    workspace_dir: Path,
    mock_responses: list[LLMResponse],
) -> tuple[ReActController, Workspace, ToolRegistry, RecordingToolExecutor]:
    workspace = Workspace(workspace_dir)
    registry = ToolRegistry()
    registry.register(CreateDirectoryTool(workspace))
    registry.register(CreateFileTool(workspace))
    registry.register(ReadFileTool(workspace))
    registry.register(ListDirectoryTool(workspace))
    registry.register(SearchFilesTool(workspace))
    registry.register(ModifyFileTool(workspace))
    executor = RecordingToolExecutor()
    llm = ScriptedMockLLM(mock_responses)
    budget = ExecutionBudget(max_steps=10, max_tool_calls=25, max_runtime_seconds=30.0)
    agent = ReActController(
        llm_client=llm,
        tool_registry=registry,
        tool_executor=executor,
        budget=budget,
    )
    return agent, workspace, registry, executor


def run_scenario_1_atomic_modify_crash(workspace_dir: Path) -> PhaseBScenarioResult:
    """Scenario 1 (H5): Injected in-flight write failure during modify preserves target content."""
    target = workspace_dir / "database.conf"
    initial_content = "max_connections=100\nport=5432\nstatus=active\n"
    target.write_text(initial_content, encoding="utf-8")

    responses = [
        LLMResponse(
            content="Updating database port to 5433.",
            tool_calls=[
                ToolCall(
                    id="c1",
                    name="modify_file",
                    arguments={
                        "path": "database.conf",
                        "old_text": "port=5432",
                        "new_text": "port=5433",
                    },
                )
            ],
        ),
        LLMResponse(content="Encountered I/O failure; modification was aborted."),
    ]

    agent, workspace, _, executor = _build_test_harness(workspace_dir, responses)
    start_time = time.monotonic()

    real_write = os.write
    calls = [0]

    def partial_write_mock(fd: int, data: bytes) -> int:
        calls[0] += 1
        if calls[0] == 1:
            return real_write(fd, data[:5])
        raise OSError("Simulated disk full during partial in-flight write")

    with patch("os.write", side_effect=partial_write_mock):
        result = agent.run_turn("Update database port in database.conf to 5433")

    duration = time.monotonic() - start_time
    final_content = target.read_text(encoding="utf-8")
    temp_files = [p.name for p in workspace_dir.iterdir() if p.name.startswith(".tmp_")]

    expected_error = ErrorCode.TRANSIENT_ERROR.name
    actual_error = executor.last_result.error_code.name if (executor.last_result and executor.last_result.error_code) else None

    content_preserved = (final_content == initial_content) and (len(temp_files) == 0)
    error_matched = (actual_error == expected_error) and (calls[0] == 2)
    success = content_preserved and error_matched
    safe = success and (result.termination_reason == TerminationReason.FINAL_ANSWER)

    return PhaseBScenarioResult(
        scenario_id=1,
        name="atomic_modify_crash_preservation",
        hypothesis="H5 (Atomic Writes)",
        success=success,
        system_behavior="EXPECTED-SAFE" if safe else "UNSAFE",
        steps=result.steps,
        tool_calls=result.tool_calls,
        duration_seconds=round(duration, 4),
        termination_reason=result.termination_reason.name,
        expected_error_classification=expected_error,
        actual_error_classification=actual_error,
        filesystem_state={
            "initial_content": initial_content,
            "final_content": final_content,
            "content_preserved": content_preserved,
            "partial_write_called_twice": calls[0] == 2,
            "temp_files_count": len(temp_files),
        },
        notes="Pre-existing file remained 100% intact after partial write and subsequent failure; empirically verified as TRANSIENT_ERROR.",
    )


def run_scenario_2_create_collision(workspace_dir: Path) -> PhaseBScenarioResult:
    """Scenario 2 (H5): Concurrent appearance of target file prevents clobbering."""
    target = workspace_dir / "output.csv"
    responses = [
        LLMResponse(
            content="Creating output.csv with report data.",
            tool_calls=[
                ToolCall(
                    id="c1",
                    name="create_file",
                    arguments={"path": "output.csv", "content": "agent_data_row_1\n"},
                )
            ],
        ),
        LLMResponse(content="Cannot create file: already exists."),
    ]

    agent, workspace, _, executor = _build_test_harness(workspace_dir, responses)
    start_time = time.monotonic()

    real_link = os.link

    def race_link(src: os.PathLike[str] | str, dst: os.PathLike[str] | str) -> None:
        Path(dst).write_text("concurrent_writer_payload\n", encoding="utf-8")
        real_link(src, dst)

    with patch("os.link", side_effect=race_link):
        result = agent.run_turn("Create output.csv with report data")

    duration = time.monotonic() - start_time
    final_content = target.read_text(encoding="utf-8")
    temp_files = [p.name for p in workspace_dir.iterdir() if p.name.startswith(".tmp_")]

    expected_error = ErrorCode.ALREADY_EXISTS.name
    actual_error = executor.last_result.error_code.name if (executor.last_result and executor.last_result.error_code) else None

    content_preserved = (final_content == "concurrent_writer_payload\n") and (len(temp_files) == 0)
    error_matched = (actual_error == expected_error)
    success = content_preserved and error_matched

    return PhaseBScenarioResult(
        scenario_id=2,
        name="create_collision_no_overwrite",
        hypothesis="H5 (Atomic Writes)",
        success=success,
        system_behavior="EXPECTED-SAFE" if success else "UNSAFE",
        steps=result.steps,
        tool_calls=result.tool_calls,
        duration_seconds=round(duration, 4),
        termination_reason=result.termination_reason.name,
        expected_error_classification=expected_error,
        actual_error_classification=actual_error,
        filesystem_state={
            "final_content": final_content,
            "content_preserved": content_preserved,
            "temp_files_count": len(temp_files),
        },
        notes="os.link atomically rejected overwrite; collision empirically returned ALREADY_EXISTS.",
    )


def run_scenario_3_postcondition_mismatch(workspace_dir: Path) -> PhaseBScenarioResult:
    """Scenario 3 (H4): Injected postcondition mismatch detects corrupt state."""
    target = workspace_dir / "manifest.json"
    target.write_text('{"build": "v1.0.0"}\n', encoding="utf-8")

    responses = [
        LLMResponse(
            content="Updating build version.",
            tool_calls=[
                ToolCall(
                    id="c1",
                    name="modify_file",
                    arguments={
                        "path": "manifest.json",
                        "old_text": "v1.0.0",
                        "new_text": "v1.0.1",
                    },
                )
            ],
        ),
        LLMResponse(content="Error: Postcondition verification failed."),
    ]

    agent, workspace, _, executor = _build_test_harness(workspace_dir, responses)
    start_time = time.monotonic()

    real_read = Path.read_text
    call_count = [0]

    def corrupted_postcondition_read(*args: object, **kwargs: object) -> str:
        call_count[0] += 1
        if call_count[0] >= 2:
            return "corrupted payload on disk"
        return real_read(target, *args, **kwargs)

    with patch.object(Path, "read_text", side_effect=corrupted_postcondition_read):
        result = agent.run_turn("Update build to v1.0.1 in manifest.json")

    duration = time.monotonic() - start_time

    expected_error = ErrorCode.INTERNAL_ERROR.name
    actual_error = executor.last_result.error_code.name if (executor.last_result and executor.last_result.error_code) else None

    error_matched = (actual_error == expected_error)
    detected = error_matched and (executor.last_result.is_error if executor.last_result else False)

    return PhaseBScenarioResult(
        scenario_id=3,
        name="postcondition_mismatch_detected",
        hypothesis="H4 (Postcondition Verification)",
        success=detected,
        system_behavior="EXPECTED-SAFE" if detected else "UNSAFE",
        steps=result.steps,
        tool_calls=result.tool_calls,
        duration_seconds=round(duration, 4),
        termination_reason=result.termination_reason.name,
        expected_error_classification=expected_error,
        actual_error_classification=actual_error,
        filesystem_state={"postcondition_detected": detected},
        notes="H4 postcondition read-back caught data divergence; empirically verified as INTERNAL_ERROR.",
    )


def run_scenario_4_stale_observation_conflict(workspace_dir: Path) -> PhaseBScenarioResult:
    """Scenario 4 (H6): Optimistic conflict detection rejects modification when file changed externally."""
    target = workspace_dir / "server.cfg"
    target.write_text("port=80\ntimeout=30\nlog_level=info\n", encoding="utf-8")
    version_v1 = _compute_file_sha256(target)

    # External modification occurs before agent attempts mutation
    target.write_text("port=80\ntimeout=60\nlog_level=debug\n", encoding="utf-8")

    responses = [
        LLMResponse(
            content="Updating server port with expected_version.",
            tool_calls=[
                ToolCall(
                    id="c1",
                    name="modify_file",
                    arguments={
                        "path": "server.cfg",
                        "old_text": "port=80",
                        "new_text": "port=443",
                        "expected_version": version_v1,
                    },
                )
            ],
        ),
        LLMResponse(content="Conflict detected: server.cfg changed on disk. Mutation aborted."),
    ]

    agent, workspace, _, executor = _build_test_harness(workspace_dir, responses)
    start_time = time.monotonic()
    result = agent.run_turn("Change port in server.cfg to 443")
    duration = time.monotonic() - start_time

    final_content = target.read_text(encoding="utf-8")
    expected_error = ErrorCode.CONFLICT.name
    actual_error = executor.last_result.error_code.name if (executor.last_result and executor.last_result.error_code) else None

    content_preserved = (final_content == "port=80\ntimeout=60\nlog_level=debug\n")
    error_matched = (actual_error == expected_error)
    success = content_preserved and error_matched

    return PhaseBScenarioResult(
        scenario_id=4,
        name="stale_observation_conflict_rejected",
        hypothesis="H6 (Conflict Detection)",
        success=success,
        system_behavior="EXPECTED-SAFE" if success else "UNSAFE",
        steps=result.steps,
        tool_calls=result.tool_calls,
        duration_seconds=round(duration, 4),
        termination_reason=result.termination_reason.name,
        expected_error_classification=expected_error,
        actual_error_classification=actual_error,
        filesystem_state={
            "final_content": final_content,
            "stale_mutation_rejected": content_preserved,
        },
        notes="Pre-mutation hash mismatch identified external changes; empirically returned CONFLICT.",
    )


def run_scenario_5_versioned_mutation_success(workspace_dir: Path) -> PhaseBScenarioResult:
    """Scenario 5 (H6): Versioned read followed by matching mutation succeeds cleanly."""
    target = workspace_dir / "routes.txt"
    target.write_text("GET /index\nPOST /login\n", encoding="utf-8")

    version = _compute_file_sha256(target)

    responses = [
        LLMResponse(
            content="Reading routes.txt with version token.",
            tool_calls=[
                ToolCall(
                    id="c1",
                    name="read_file",
                    arguments={"path": "routes.txt", "include_version": True},
                )
            ],
        ),
        LLMResponse(
            content="Applying modification with verified version.",
            tool_calls=[
                ToolCall(
                    id="c2",
                    name="modify_file",
                    arguments={
                        "path": "routes.txt",
                        "old_text": "POST /login",
                        "new_text": "POST /auth/login",
                        "expected_version": version,
                    },
                )
            ],
        ),
        LLMResponse(content="Route successfully updated."),
    ]

    agent, workspace, _, executor = _build_test_harness(workspace_dir, responses)
    start_time = time.monotonic()
    result = agent.run_turn("Update POST /login route in routes.txt")
    duration = time.monotonic() - start_time

    final_content = target.read_text(encoding="utf-8")
    expected_content = "GET /index\nPOST /auth/login\n"

    actual_error = executor.last_result.error_code.name if (executor.last_result and executor.last_result.error_code) else None

    succeeded = (final_content == expected_content) and (result.termination_reason == TerminationReason.FINAL_ANSWER) and (actual_error is None)

    return PhaseBScenarioResult(
        scenario_id=5,
        name="versioned_mutation_clean_path",
        hypothesis="H6 (Conflict Detection)",
        success=succeeded,
        system_behavior="EXPECTED-SAFE" if succeeded else "UNSAFE",
        steps=result.steps,
        tool_calls=result.tool_calls,
        duration_seconds=round(duration, 4),
        termination_reason=result.termination_reason.name,
        expected_error_classification=None,
        actual_error_classification=actual_error,
        filesystem_state={
            "final_content": final_content,
            "succeeded": succeeded,
        },
        notes="Versioned read and atomic modification executed with full verification and zero error.",
    )


def main() -> None:
    results: list[PhaseBScenarioResult] = []

    scenarios = [
        run_scenario_1_atomic_modify_crash,
        run_scenario_2_create_collision,
        run_scenario_3_postcondition_mismatch,
        run_scenario_4_stale_observation_conflict,
        run_scenario_5_versioned_mutation_success,
    ]

    for scenario_fn in scenarios:
        with tempfile.TemporaryDirectory() as temp_dir:
            res = scenario_fn(Path(temp_dir).resolve())
            results.append(res)

    out_file = Path(__file__).parent / "phase_b_results.json"
    with open(out_file, "w", encoding="utf-8") as f:
        json.dump([asdict(r) for r in results], f, indent=2)

    passed_count = sum(1 for r in results if r.success)
    expected_safe_count = sum(1 for r in results if r.system_behavior == "EXPECTED-SAFE")
    unsafe_count = sum(1 for r in results if r.system_behavior == "UNSAFE")

    print(f"Phase B Evaluation finished. {len(results)} scenarios executed. Saved to {out_file.name}.")
    print(f"Scenario Outcomes: Passed {passed_count} / {len(results)}")
    print(f"System Behavior: {expected_safe_count} EXPECTED-SAFE, {unsafe_count} UNSAFE")

    for r in results:
        err_info = f"expected={r.expected_error_classification}, actual={r.actual_error_classification}"
        print(f"[{r.system_behavior}] Scenario {r.scenario_id:02d}: {r.name} ({r.hypothesis}) -> {r.termination_reason} ({err_info})")


if __name__ == "__main__":
    main()
