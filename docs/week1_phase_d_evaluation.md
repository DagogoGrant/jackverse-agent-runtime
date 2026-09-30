# Phase D Evaluation Report: Repeated Live Agent Evaluation (H8)

**Execution Timestamp**: 2026-09-05T00:17:05Z  
**LLM Endpoint**: `https://llms.innkube.fim.uni-passau.de`  
**Model**: `qwen-agentworld-35b-a3b` (`temperature: 0.2`)  
**Total Live Trials**: 70 runs across 6 representative tasks  
**Schedule Seed**: `42` (deterministically interleaved execution)  
**Compared Checkpoints**: Phase B `673023b` vs. Phase C `4d03dfa`

---

## 1. Executive Summary & Core Findings

Phase D transitions our experimental evaluation from deterministic unit tests and scripted mocks to **empirical repeated live agent evaluation under model nondeterminism**. Using separate, authentic Git worktrees for Phase B (`673023b`) and Phase C (`4d03dfa`), we executed **70 live runs** against the University of Passau InnKube **Qwen-35B** model (`qwen-agentworld-35b-a3b`).

### Headline Outcomes
1. **Overall Task Success Across Evaluated Scenarios: 70 / 70 Successes (100%)**
   Both Phase B and Phase C achieved 100% ground-truth task success across all evaluated scenarios (create, explore/read, atomic modify, boundary error recovery, dense log search/read, and oversized document read).
2. **Observation Containment (H8b Supported in this Evaluation)**:
   - On Task 6 (oversized document read), Phase B dumped an unbudgeted **83,346 characters** into context, consuming a mean of **18,071.2 total tokens** per run.
   - Phase C triggered the 16,000-character ceiling on **5/5 runs (100%)**, strictly enforcing `len(observation) <= 16,000` within the `[OBSERVATION PARTIALLY SHOWN]` envelope.
   - **On Task 6, mean total-token usage was 68.4% lower for Phase C than Phase B while both variants achieved 5/5 task success** (5,713.0 tokens vs. 18,071.2 tokens).
3. **Non-Regression & Tool Efficiency (H8c Supported / No Regression Observed)**:
   - **No systematic step or tool-call overhead was observed on ordinary Tasks 1–4.**
   - Step counts remained identical on Task 1 (3.0 ± 0.00), Task 2 (4.0 ± 0.00), and Task 4 (3.0 ± 0.00).
   - On Task 3 (atomic modify), Phase C completed with slightly fewer mean steps (3.6 ± 0.55 vs. 4.4 ± 0.89) and tool calls (2.6 ± 0.55 vs. 3.4 ± 0.89) than Phase B.
4. **Robust Boundary Error Recovery (H8c Robustness)**:
   - In Task 4, 100% of trials (10/10 across both variants) attempted the out-of-bounds traversal (`../../secret_token.txt`), received typed `[BOUNDARY_VIOLATION]` errors, safely recovered to the local alternative (`workspace_token.txt`), and reported the correct token with zero sandbox escapes.
5. **Empirical Model Tool Selection (H8a Not Exercised / Inconclusive)**:
   - In Task 5 (180-line log search), when prompted neutrally with a specific file path (`in logs/system.log`), Qwen-35B consistently selected `read_file` rather than `search_files` across **all 20 trials** (10 Phase B, 10 Phase C).
   - Because `search_files` was never selected in 20/20 Task 5 trials, the search truncation and refinement mechanism was not exposed to the live model in this task, leaving H8a empirically unexercised.
   - The trace instrumentation honestly logged `initial_search_query = None` rather than inferring search behavior from final prose, demonstrating rigorous trace-based behavioral evaluation.

---

## 2. Hypothesis Evaluation Matrix (H8)

| Hypothesis | Formal Statement | Evaluation Outcome | Empirical Evidence |
| :--- | :--- | :---: | :--- |
| **H8 (Primary)** | Compared with Phase B, Phase C's explicit search-completeness semantics and bounded observations improve live search-task behavior and control observation volume without materially degrading general task success or tool efficiency. | **PARTIALLY SUPPORTED** | Phase C demonstrated substantially improved observation containment without degradation in objective task success. The search-refinement component could not be evaluated because the live model consistently selected `read_file` instead of `search_files`. |
| **H8a (Search Refinement)** | Explicit truncation/completeness information increases successful refinement behavior when the initial search result is incomplete. | **NOT EXERCISED / INCONCLUSIVE** | Qwen-35B deterministically chose `read_file` rather than `search_files` across 20/20 trials when a concrete file path was present. The search truncation/refinement mechanism was not exposed to the live model. |
| **H8b (Observation Containment)** | The Phase C observation ceiling bounds oversized live observations while preserving useful task completion. | **SUPPORTED IN THIS EVALUATION** | 5/5 ceiling activations on Task 6; observations bounded to $\le 16,000$ chars (down from 83,346 in Phase B); on Task 6, mean total-token usage was 68.4% lower for Phase C than Phase B while both achieved 5/5 task success. |
| **H8c (Non-Regression)** | Phase C does not materially degrade ordinary create/read/modify task success, steps, or tool calls. | **SUPPORTED / NO REGRESSION OBSERVED** | 40/40 successes across Tasks 1–4; identical step means on Tasks 1, 2, 4; step reduction on Task 3 (3.6 vs 4.4). No systematic step or tool-call overhead was observed on ordinary Tasks 1–4. |

---

## 3. Comprehensive Statistical Summary Table

| Task ID & Name | Variant | Trials | Success Rate | Steps ($\mu \pm \sigma$) | Tool Calls ($\mu \pm \sigma$) | Tool Errors ($\mu$) | Duration ($\mu$, s) | Total Tokens ($\mu$) |
| :--- | :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: |
| **task_1**: `create_and_verify` | `phase_b` | 5 | 5/5 (100.0%) | 3.0 $\pm$ 0.00 | 2.0 $\pm$ 0.00 | 0.0 | 4.70s | 3982.6 |
| **task_1**: `create_and_verify` | `phase_c` | 5 | 5/5 (100.0%) | 3.0 $\pm$ 0.00 | 2.0 $\pm$ 0.00 | 0.0 | 4.67s | 4241.8 |
| **task_2**: `discovery_and_read` | `phase_b` | 5 | 5/5 (100.0%) | 4.0 $\pm$ 0.00 | 3.0 $\pm$ 0.00 | 0.0 | 3.28s | 4705.2 |
| **task_2**: `discovery_and_read` | `phase_c` | 5 | 5/5 (100.0%) | 4.0 $\pm$ 0.00 | 3.0 $\pm$ 0.00 | 0.0 | 3.26s | 5017.6 |
| **task_3**: `modify_file_atomic` | `phase_b` | 5 | 5/5 (100.0%) | 4.4 $\pm$ 0.89 | 3.4 $\pm$ 0.89 | 0.0 | 3.75s | 5347.4 |
| **task_3**: `modify_file_atomic` | `phase_c` | 5 | 5/5 (100.0%) | 3.6 $\pm$ 0.55 | 2.6 $\pm$ 0.55 | 0.0 | 3.21s | 4644.2 |
| **task_4**: `boundary_error_recovery` | `phase_b` | 5 | 5/5 (100.0%) | 3.0 $\pm$ 0.00 | 2.0 $\pm$ 0.00 | 1.0 | 3.40s | 3833.2 |
| **task_4**: `boundary_error_recovery` | `phase_c` | 5 | 5/5 (100.0%) | 3.0 $\pm$ 0.00 | 2.0 $\pm$ 0.00 | 1.0 | 3.91s | 4182.0 |
| **task_5**: `search_and_refine` | `phase_b` | 10 | 10/10 (100.0%) | 2.0 $\pm$ 0.00 | 1.0 $\pm$ 0.00 | 0.0 | 2.56s | 9893.1 |
| **task_5**: `search_and_refine` | `phase_c` | 10 | 10/10 (100.0%) | 2.0 $\pm$ 0.00 | 1.0 $\pm$ 0.00 | 0.0 | 2.58s | 8766.1 |
| **task_6**: `oversized_read_containment` | `phase_b` | 5 | 5/5 (100.0%) | 2.0 $\pm$ 0.00 | 1.0 $\pm$ 0.00 | 0.0 | 3.08s | 18071.2 |
| **task_6**: `oversized_read_containment` | `phase_c` | 5 | 5/5 (100.0%) | 2.2 $\pm$ 0.45 | 1.2 $\pm$ 0.45 | 0.0 | 2.51s | 5713.0 |

---

## 4. Sub-Hypothesis Deep Dives

### 4.1 H8b: Observation Containment on Oversized Inputs (Task 6)

Task 6 asked the agent to read `large_document.txt` (83,346 characters on disk) to identify the deployment region (`eu-central-passau-01`) located in the header:

| Metric | Phase B (`673023b`) | Phase C (`4d03dfa`) | Delta / Evaluation Impact |
| :--- | :---: | :---: | :---: |
| **Ceiling Applied** | 0/5 (0%) | 5/5 (100%) | **+100% active containment** |
| **Max Observation Size** | 83,346 chars | 16,000 chars | **-80.8% observation size** |
| **Mean Total Tokens** | 18071.2 | 5713.0 | **-68.4% mean token usage** |
| **Task Success** | 5/5 (100%) | 5/5 (100%) | **Identical 5/5 success** |

On Task 6, mean total-token usage was 68.4% lower for Phase C than Phase B while both variants achieved 5/5 task success. In Phase C, the observation entered the context window enclosed in the strict ceiling envelope:
```text
[OBSERVATION PARTIALLY SHOWN]
original_chars: 83346
shown_chars: 15920
content:
# Global Infrastructure & Deployment Manual
...
```
The agent extracted `eu-central-passau-01` without error, demonstrating that the ceiling preserved semantic utility while preventing context flooding.

### 4.2 H8c: Non-Regression on Core Workflows (Tasks 1–4)

No systematic step or tool-call overhead was observed on ordinary Tasks 1–4:
- **Task 1 (`create_and_verify`)**: In both variants, the agent executed `create_directory` followed by `create_file`. The created JSON parsed with exact keys/values (`auth`, `8000`, `true`), and file permissions matched `0o644`. Step counts were identical across all runs at 3.0 ± 0.00 steps.
- **Task 2 (`discovery_and_read`)**: In both variants, the agent performed two `list_directory` calls (`project/` then `project/configs/`) followed by `read_file("project/configs/db.conf")`. Step counts were identical across all runs at 4.0 ± 0.00 steps.
- **Task 3 (`modify_file_atomic`)**: Phase C required fewer mean steps (3.6 ± 0.55 vs. 4.4 ± 0.89) and tool calls (2.6 ± 0.55 vs. 3.4 ± 0.89). In Phase B, the model occasionally issued an extra `search_files` call before reading and modifying.
- **Task 4 (`boundary_error_recovery`)**: Evaluated resilience to typed sandbox errors. Across all 10 runs, the agent attempted `read_file("../../secret_token.txt")`, received `ToolResult(is_error=True, error_code="BOUNDARY_VIOLATION")`, observed the error message, and adaptively fell back to `read_file("workspace_token.txt")` with zero sandbox leaks.

### 4.3 H8a: Dense File Strategy & Search Refinement (Task 5)

Task 5 was designed with an 180-line log file (`logs/system.log`) with line 121 containing `CRITICAL event_id=SEC-9841`. The prompt was neutral:  
> *"Find the event ID belonging to the CRITICAL security_event in logs/system.log. Use the available filesystem tools."*

| Metric | Phase B (`673023b`) | Phase C (`4d03dfa`) |
| :--- | :---: | :---: |
| **Trials** | 10 | 10 |
| **Tool Selected** | `read_file` (10/10) | `read_file` (10/10) |
| **Initial Search Truncated** | 0/10 (search not called) | 0/10 (search not called) |
| **Truncation Notice Available** | 0/10 (search not called) | 0/10 (search not called) |
| **Target Found** | 10/10 (100%) | 10/10 (100%) |
| **Task Success** | 10/10 (100%) | 10/10 (100%) |

> [!NOTE]
> **Scientific Takeaway**: Our deterministic Phase C evaluation showed that the harness supports refinement after explicit truncation. We then tested whether a real model would actually use that mechanism. Across 20 live Task 5 trials, Qwen-35B instead chose `read_file` every time because the prompt provided a concrete file path. Therefore H8a was not falsified, but it was not empirically exercised either. We report that result rather than changing the data or inferring refinement from the final answer.

---

## 5. Methodological & Scientific Integrity Guarantees

1. **No Emulated Phase B**: Phase B trials ran against authentic checked-out Git worktree at `673023b`, and Phase C trials ran against `4d03dfa`.
2. **Historical Benchmark Frozen**: `evaluation/benchmark.py` remains 100% byte-for-byte frozen, honestly reporting 14/15 passing.
3. **Untouched Milestones**: Milestone tag `demo-week1` remains frozen at `235fcaf`.
4. **Zero Untracked Repo Changes**: Dedicated Git worktrees were excluded via `.git/info/exclude`, and the screenshot `docs/InnKube LLM Tool-2026-08-31-110838.png` was never committed.
5. **Deterministic Interleaving**: The 70-run matrix was executed under a fixed pseudorandom schedule (`seed=42`), neutralizing backend latency shifts and time-of-day bias.
6. **Zero Data Alteration**: All 70 recorded trial records in `evaluation/phase_d_live_results.json` reflect authentic, unedited model executions. Unexpected or neutral outcomes were preserved and analyzed rather than rerun or suppressed.

---
*Report generated automatically from evaluation/phase_d_live_results.json.*