"""Worker script executed inside an isolated worktree environment for Phase D live trials."""

import argparse
import json
import os
from pathlib import Path
import sys
import time
from typing import Any

from dotenv import load_dotenv

from evaluation.live_tasks import TASKS


class TracingLLMClient:
    """Wraps LLMClient to record cumulative token usage across all steps."""

    def __init__(self, inner_client: Any) -> None:
        self._inner = inner_client
        self.prompt_tokens = 0
        self.completion_tokens = 0
        self.total_tokens = 0

    def chat(self, messages: Any, tools: Any = None) -> Any:
        resp = self._inner.chat(messages, tools=tools)
        if resp.usage:
            self.prompt_tokens += resp.usage.get("prompt_tokens", 0)
            self.completion_tokens += resp.usage.get("completion_tokens", 0)
            self.total_tokens += resp.usage.get("total_tokens", 0)
        return resp


def run_trial(
    variant: str,
    task_id: int,
    trial_idx: int,
    schedule_idx: int,
    workspace_dir: Path,
) -> dict[str, Any]:
    task = TASKS[task_id]

    # Clean workspace and populate task fixture
    task.setup_fixture(workspace_dir)

    # Load environment & config
    load_dotenv()
    api_key = os.environ.get("INNKUBE_API_KEY")
    if not api_key:
        raise RuntimeError("INNKUBE_API_KEY environment variable not found in .env or environment.")

    # Import classes from active worktree (PYTHONPATH)
    from harness.agent.budget import ExecutionBudget
    from harness.agent.react import ReActController
    from harness.llm.client import LLMClient
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

    workspace = Workspace(workspace_dir)
    registry = ToolRegistry()
    registry.register(CreateDirectoryTool(workspace))
    registry.register(CreateFileTool(workspace))
    registry.register(ReadFileTool(workspace))
    registry.register(ListDirectoryTool(workspace))
    registry.register(SearchFilesTool(workspace))
    registry.register(ModifyFileTool(workspace))

    # ToolExecutor instance from worktree
    executor = ToolExecutor()
    tool_trace: list[dict[str, Any]] = []

    # Monkey-patch execute to capture trace without hiding ToolExecutor class attributes (like max_observation_chars)
    orig_execute = executor.execute

    def wrapped_execute(tool: Any, arguments: dict[str, Any]) -> Any:
        res = orig_execute(tool, arguments)
        err_code_val = getattr(res, "error_code", None)
        if hasattr(err_code_val, "value"):
            err_code_str = err_code_val.value
        elif err_code_val is not None:
            err_code_str = str(err_code_val)
        else:
            err_code_str = None

        tool_trace.append({
            "tool": tool.spec.name,
            "arguments": dict(arguments),
            "content": res.content,
            "is_error": res.is_error,
            "error_code": err_code_str,
            "content_len": len(res.content),
        })
        return res

    executor.execute = wrapped_execute

    raw_llm = LLMClient(
        api_key=api_key,
        base_url="https://llms.innkube.fim.uni-passau.de",
        model="qwen-agentworld-35b-a3b",
        temperature=0.2,
        timeout=45.0,
        max_retries=2,
    )
    tracing_llm = TracingLLMClient(raw_llm)

    if variant == "phase_b":
        budget = ExecutionBudget(max_steps=10, max_tool_calls=25, max_runtime_seconds=90.0)
    else:
        budget = ExecutionBudget(
            max_steps=10,
            max_tool_calls=25,
            max_runtime_seconds=90.0,
            max_observation_chars=16_000,
        )

    controller = ReActController(
        llm_client=tracing_llm,
        tool_registry=registry,
        tool_executor=executor,
        budget=budget,
    )

    start_time = time.perf_counter()
    run_result = controller.run_turn(task.prompt)
    duration = round(time.perf_counter() - start_time, 4)

    # Postcondition verification
    is_success, verifier_msg, verifier_meta = task.verify_outcome(
        workspace_dir,
        run_result.final_text,
        tool_trace,
        variant,
    )

    # Count tool errors
    tool_errors = sum(1 for c in tool_trace if c.get("is_error"))

    # Observation ceiling stats
    ceiling_applied = any(
        "[OBSERVATION PARTIALLY SHOWN]" in c.get("content", "")
        for c in tool_trace
    )
    max_obs_len = max([c.get("content_len", 0) for c in tool_trace], default=0)

    # Task 5 trace metrics
    task_5_trace = verifier_meta if task_id == 5 else None

    # Termination reason string
    term_reason = run_result.termination_reason.value if hasattr(run_result.termination_reason, "value") else str(run_result.termination_reason)

    return {
        "task_id": task_id,
        "task_name": task.name,
        "variant": variant,
        "trial_index": trial_idx,
        "schedule_index": schedule_idx,
        "success": is_success,
        "verifier_message": verifier_msg,
        "termination_reason": term_reason,
        "steps": run_result.steps,
        "tool_calls": run_result.tool_calls,
        "tool_errors": tool_errors,
        "duration_seconds": duration,
        "usage": {
            "prompt_tokens": tracing_llm.prompt_tokens,
            "completion_tokens": tracing_llm.completion_tokens,
            "total_tokens": tracing_llm.total_tokens,
        },
        "task_5_trace": task_5_trace,
        "task_meta": verifier_meta if task_id != 5 else None,
        "observation_ceiling_applied": ceiling_applied,
        "max_observation_chars_observed": max_obs_len,
        "final_text": run_result.final_text,
        "error": run_result.error,
        "tool_trace": tool_trace,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Live evaluation trial worker.")
    parser.add_argument("--variant", required=True, choices=["phase_b", "phase_c"])
    parser.add_argument("--task-id", required=True, type=int, choices=[1, 2, 3, 4, 5, 6])
    parser.add_argument("--trial", required=True, type=int)
    parser.add_argument("--schedule-index", default=0, type=int)
    parser.add_argument("--workspace-dir", required=True, type=Path)
    parser.add_argument("--output-json", required=True, type=Path)

    args = parser.parse_args()

    result = run_trial(
        variant=args.variant,
        task_id=args.task_id,
        trial_idx=args.trial,
        schedule_idx=args.schedule_index,
        workspace_dir=args.workspace_dir,
    )

    args.output_json.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(f"TRIAL_DONE: task={args.task_id} variant={args.variant} trial={args.trial} success={result['success']} steps={result['steps']} tools={result['tool_calls']}")


if __name__ == "__main__":
    main()
