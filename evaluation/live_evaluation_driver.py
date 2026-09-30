"""External driver script for Phase D live evaluation.

Orchestrates live repeated evaluation across frozen git worktrees:
- Phase B: .worktrees/phase-b (673023b)
- Phase C: .worktrees/phase-c (4d03dfa)

Enforces deterministic trial scheduling, temp workspace isolation,
incremental progress saving, and statistical summarization.
"""

import argparse
import json
import os
from pathlib import Path
import random
import statistics
import subprocess
import sys
import tempfile
import time
from typing import Any


def build_schedule(seed: int = 42) -> list[dict[str, Any]]:
    """Construct the complete 70-run matrix and deterministically shuffle it."""
    runs: list[dict[str, Any]] = []

    # Tasks 1 to 4: 5 trials per variant
    for task_id in (1, 2, 3, 4):
        for trial in range(1, 6):
            runs.append({"task_id": task_id, "variant": "phase_b", "trial": trial})
            runs.append({"task_id": task_id, "variant": "phase_c", "trial": trial})

    # Task 5: 10 trials per variant (main behavioral hypothesis H8a)
    for trial in range(1, 11):
        runs.append({"task_id": 5, "variant": "phase_b", "trial": trial})
        runs.append({"task_id": 5, "variant": "phase_c", "trial": trial})

    # Task 6: 5 trials per variant (observation containment hypothesis H8b)
    for trial in range(1, 6):
        runs.append({"task_id": 6, "variant": "phase_b", "trial": trial})
        runs.append({"task_id": 6, "variant": "phase_c", "trial": trial})

    # Total runs = (4 * 5 * 2) + (10 * 2) + (5 * 2) = 40 + 20 + 10 = 70 runs
    rng = random.Random(seed)
    rng.shuffle(runs)

    for idx, item in enumerate(runs, start=1):
        item["schedule_index"] = idx

    return runs


def execute_single_run(
    variant: str,
    task_id: int,
    trial: int,
    schedule_idx: int,
    repo_root: Path,
) -> dict[str, Any]:
    """Execute one trial in a clean temporary directory using the target worktree."""
    worktree_src = repo_root / ".worktrees" / variant.replace("_", "-") / "src"
    if not worktree_src.is_dir():
        raise RuntimeError(f"Worktree source directory not found: {worktree_src}")

    python_bin = repo_root / "venv-repro312" / "bin" / "python"
    if not python_bin.is_file():
        python_bin = Path(sys.executable)

    worker_script = repo_root / "evaluation" / "live_worker.py"

    with tempfile.TemporaryDirectory(prefix=f"live_task{task_id}_{variant}_t{trial}_") as temp_dir:
        temp_workspace = Path(temp_dir)
        temp_out = temp_workspace / "output.json"

        env = dict(os.environ)
        env["PYTHONPATH"] = f"{worktree_src}:{repo_root}"

        cmd = [
            str(python_bin),
            str(worker_script),
            "--variant", variant,
            "--task-id", str(task_id),
            "--trial", str(trial),
            "--schedule-index", str(schedule_idx),
            "--workspace-dir", str(temp_workspace),
            "--output-json", str(temp_out),
        ]

        # Execute with retries on transient network/LLM failure
        max_attempts = 2
        for attempt in range(1, max_attempts + 1):
            try:
                proc = subprocess.run(
                    cmd,
                    cwd=str(repo_root),
                    env=env,
                    capture_output=True,
                    text=True,
                    timeout=180.0,
                )
                if proc.returncode == 0 and temp_out.is_file():
                    data = json.loads(temp_out.read_text(encoding="utf-8"))
                    return data

                # If failed, check error
                err_msg = proc.stderr.strip() or proc.stdout.strip()
                print(f"  [WARN] Attempt {attempt}/{max_attempts} failed for task={task_id} {variant} t={trial}: {err_msg[:200]}")
                if attempt < max_attempts:
                    time.sleep(2.0 * attempt)
            except subprocess.TimeoutExpired:
                print(f"  [WARN] Attempt {attempt}/{max_attempts} timed out for task={task_id} {variant} t={trial}")
                if attempt < max_attempts:
                    time.sleep(2.0 * attempt)

        # If all attempts failed, record error result
        return {
            "task_id": task_id,
            "task_name": f"task_{task_id}",
            "variant": variant,
            "trial_index": trial,
            "schedule_index": schedule_idx,
            "success": False,
            "verifier_message": "Worker subprocess failed to complete execution.",
            "termination_reason": "ERROR",
            "steps": 0,
            "tool_calls": 0,
            "tool_errors": 0,
            "duration_seconds": 0.0,
            "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
            "task_5_trace": None,
            "observation_ceiling_applied": False,
            "max_observation_chars_observed": 0,
            "final_text": None,
            "error": err_msg if "err_msg" in locals() else "Subprocess execution failure",
            "tool_trace": [],
        }


def compute_summary(records: list[dict[str, Any]], seed: int) -> dict[str, Any]:
    """Compute descriptive statistical summaries for Phase B vs Phase C."""
    summary: dict[str, Any] = {
        "metadata": {
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "total_trials": len(records),
            "schedule_seed": seed,
            "model": "qwen-agentworld-35b-a3b",
            "endpoint": "https://llms.innkube.fim.uni-passau.de",
            "temperature": 0.2,
            "phase_b_commit": "673023b",
            "phase_c_commit": "4d03dfa",
        },
        "tasks": {},
        "task_5_behavioral_summary": {},
        "task_6_containment_summary": {},
    }

    # Group by (task_id, variant)
    by_task_variant: dict[tuple[int, str], list[dict[str, Any]]] = {}
    for r in records:
        key = (r["task_id"], r["variant"])
        by_task_variant.setdefault(key, []).append(r)

    all_task_ids = sorted(list({r["task_id"] for r in records}))
    for t_id in all_task_ids:
        summary["tasks"][f"task_{t_id}"] = {}
        for var in ("phase_b", "phase_c"):
            runs = by_task_variant.get((t_id, var), [])
            if not runs:
                continue
            total = len(runs)
            successes = sum(1 for r in runs if r["success"])
            steps_list = [r["steps"] for r in runs]
            tools_list = [r["tool_calls"] for r in runs]
            errors_list = [r["tool_errors"] for r in runs]
            durations = [r["duration_seconds"] for r in runs]
            total_tokens = [r.get("usage", {}).get("total_tokens", 0) for r in runs]

            summary["tasks"][f"task_{t_id}"][var] = {
                "task_name": runs[0]["task_name"],
                "total_trials": total,
                "successes": successes,
                "success_rate_raw": f"{successes}/{total}",
                "success_percentage": round((successes / total) * 100, 1),
                "steps_mean": round(statistics.mean(steps_list), 2) if steps_list else 0,
                "steps_stdev": round(statistics.stdev(steps_list), 2) if len(steps_list) > 1 else 0,
                "tool_calls_mean": round(statistics.mean(tools_list), 2) if tools_list else 0,
                "tool_calls_stdev": round(statistics.stdev(tools_list), 2) if len(tools_list) > 1 else 0,
                "tool_errors_mean": round(statistics.mean(errors_list), 2) if errors_list else 0,
                "duration_mean_seconds": round(statistics.mean(durations), 2) if durations else 0,
                "total_tokens_mean": round(statistics.mean(total_tokens), 1) if total_tokens else 0,
            }

    # Task 5 granular refinement summary
    for var in ("phase_b", "phase_c"):
        runs_5 = by_task_variant.get((5, var), [])
        if runs_5:
            n = len(runs_5)
            truncated_cnt = sum(1 for r in runs_5 if (r.get("task_5_trace") or {}).get("initial_search_truncated"))
            notice_cnt = sum(1 for r in runs_5 if (r.get("task_5_trace") or {}).get("truncation_notice_available"))
            followup_cnt = sum(1 for r in runs_5 if (r.get("task_5_trace") or {}).get("followup_search_or_read"))
            refine_cnt = sum(1 for r in runs_5 if (r.get("task_5_trace") or {}).get("refinement_triggered"))
            refine_after_trunc = sum(1 for r in runs_5 if (r.get("task_5_trace") or {}).get("refinement_after_truncation"))
            target_found_cnt = sum(1 for r in runs_5 if (r.get("task_5_trace") or {}).get("target_found"))
            success_cnt = sum(1 for r in runs_5 if r["success"])

            summary["task_5_behavioral_summary"][var] = {
                "trials": n,
                "initial_search_truncated": f"{truncated_cnt}/{n}",
                "truncation_notice_available": f"{notice_cnt}/{n}",
                "followup_search_or_read": f"{followup_cnt}/{n}",
                "refinement_triggered": f"{refine_cnt}/{n}",
                "refinement_after_truncation": f"{refine_after_trunc}",
                "target_found": f"{target_found_cnt}/{n}",
                "task_success": f"{success_cnt}/{n}",
            }

    # Task 6 containment summary
    for var in ("phase_b", "phase_c"):
        runs_6 = by_task_variant.get((6, var), [])
        if runs_6:
            n = len(runs_6)
            ceiling_cnt = sum(1 for r in runs_6 if r.get("observation_ceiling_applied"))
            max_obs = max([r.get("max_observation_chars_observed", 0) for r in runs_6], default=0)
            success_cnt = sum(1 for r in runs_6 if r["success"])
            summary["task_6_containment_summary"][var] = {
                "trials": n,
                "ceiling_applied_count": f"{ceiling_cnt}/{n}",
                "max_observation_chars_observed": max_obs,
                "task_success": f"{success_cnt}/{n}",
            }

    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description="Phase D live evaluation runner.")
    parser.add_argument("--smoke-test", action="store_true", help="Run smoke test (3-4 representative runs).")
    parser.add_argument("--full", action="store_true", help="Run full 70-trial matrix.")
    parser.add_argument("--task-id", type=int, choices=[1, 2, 3, 4, 5, 6], help="Run single task ID.")
    parser.add_argument("--variant", choices=["phase_b", "phase_c"], help="Run single variant.")
    parser.add_argument("--trial", type=int, default=1, help="Trial index for single run.")
    parser.add_argument("--seed", type=int, default=42, help="Schedule random seed (default 42).")
    parser.add_argument("--output", type=Path, default=Path("evaluation/phase_d_live_results.json"))

    args = parser.parse_args()
    repo_root = Path.cwd().resolve()

    if args.smoke_test:
        smoke_runs = [
            {"task_id": 1, "variant": "phase_c", "trial": 1, "schedule_index": 1},
            {"task_id": 5, "variant": "phase_b", "trial": 1, "schedule_index": 2},
            {"task_id": 5, "variant": "phase_c", "trial": 1, "schedule_index": 3},
            {"task_id": 6, "variant": "phase_c", "trial": 1, "schedule_index": 4},
        ]
        results = []
        print("=== STARTING PHASE D SMOKE TEST (4 RUNS) ===")
        for run_info in smoke_runs:
            print(f"\n--> Running smoke trial: Task {run_info['task_id']} {run_info['variant']} trial {run_info['trial']}...")
            res = execute_single_run(
                variant=run_info["variant"],
                task_id=run_info["task_id"],
                trial=run_info["trial"],
                schedule_idx=run_info["schedule_index"],
                repo_root=repo_root,
            )
            results.append(res)
            print(f"    Result: success={res['success']} steps={res['steps']} tools={res['tool_calls']} dur={res['duration_seconds']}s")
            if run_info["task_id"] == 5:
                print(f"    Task 5 trace: {res.get('task_5_trace')}")
            if run_info["task_id"] == 6:
                print(f"    Task 6 ceiling applied: {res.get('observation_ceiling_applied')} max_obs={res.get('max_observation_chars_observed')}")

        smoke_out = repo_root / "evaluation" / "phase_d_smoke_results.json"
        summary = compute_summary(results, seed=args.seed)
        output_payload = {"summary": summary, "runs": results}
        smoke_out.write_text(json.dumps(output_payload, indent=2), encoding="utf-8")
        print(f"\nSmoke test finished. Saved to {smoke_out}")
        return

    if args.task_id and args.variant:
        print(f"Running single trial: Task {args.task_id} {args.variant} trial {args.trial}...")
        res = execute_single_run(
            variant=args.variant,
            task_id=args.task_id,
            trial=args.trial,
            schedule_idx=1,
            repo_root=repo_root,
        )
        print(json.dumps(res, indent=2))
        return

    if args.full:
        schedule = build_schedule(seed=args.seed)
        total_runs = len(schedule)
        print(f"=== STARTING PHASE D FULL MATRIX ({total_runs} RUNS, SEED={args.seed}) ===")

        results = []
        out_file = args.output
        out_file.parent.mkdir(parents=True, exist_ok=True)

        for item in schedule:
            idx = item["schedule_index"]
            t_id = item["task_id"]
            var = item["variant"]
            tr = item["trial"]
            print(f"[{idx:02d}/{total_runs}] Task {t_id} | {var:7s} | trial {tr:02d} ... ", end="", flush=True)

            res = execute_single_run(
                variant=var,
                task_id=t_id,
                trial=tr,
                schedule_idx=idx,
                repo_root=repo_root,
            )
            results.append(res)
            status_str = "PASS" if res["success"] else "FAIL"
            print(f"{status_str} (steps={res['steps']}, tools={res['tool_calls']}, dur={res['duration_seconds']:.1f}s)")

            # Save incremental progress
            summary = compute_summary(results, seed=args.seed)
            out_file.write_text(json.dumps({"summary": summary, "runs": results}, indent=2), encoding="utf-8")

        print(f"\nFull matrix evaluation complete! {len(results)} runs recorded in {out_file}")
        return

    parser.print_help()


if __name__ == "__main__":
    main()
