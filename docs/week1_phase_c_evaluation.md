# Week 1 Agent Harness — Phase C Evaluation Report (Hypothesis H7)

This document records the empirical results, architectural changes, and experimental evidence for **Phase C: Observation Budgeting & Context Management** (`H7`), comparing them directly against the frozen historical benchmarks and Phase B checkpoint (`673023b`).

---

## 1. Executive Summary & Core Metrics

| Metric Dimension | Baseline (Commit `960c44e`) | Phase B (Commit `673023b`) | Phase C (Current) | Delta / Empirical Evidence |
| :--- | :---: | :---: | :---: | :--- |
| **Deterministic Unit Tests (Local)** | 122 tests | 152 tests (0.894s) | **168 tests** (0.880s) | **+16 tests**, 100% pass |
| **Deterministic Unit Tests (Docker)** | 122 tests | 152 tests (0.732s) | **168 tests** (0.710s) | **+16 tests**, 100% pass |
| **Historical 15-Task Benchmark** | 14 / 15 passed | 15 / 15 passed | **14 / 15 passed** | Frozen instrument (Task 13 reflects historical 50-line assertion) |
| **System Behavior: UNSAFE** | 1 task | 0 tasks | **0 tasks (0.0%)** | Zero unsafe behaviors |
| **Phase C Evaluation Suite** | N/A | N/A | **5 / 5 passed (100%)** | All 5 observation budgeting scenarios EXPECTED-SAFE |
| **Default Production Ceiling** | None (unbounded) | None (unbounded) | **16,000 chars** | Active in default CLI & configuration |
| **Silent Truncation Rate** | 100% (silent 50-cap) | 100% (silent 50-cap) | **0.0%** (eliminated) | Truncation is explicitly indicated in text & JSON |
| **Count Honesty Distinction** | None (stopped counting) | None (stopped counting) | **100%** | `count_complete` strictly separates exact totals from lower bounds |
| **Context Observation Invariant** | Unbounded | Unbounded | **`len(obs) <= max_chars`** | Strictly enforced over entire final envelope |

---

## 2. Hypothesis Evaluations & Empirical Evidence

### Primary Hypothesis (H7: Observation Budgeting & Context Management)
> **Hypothesis**: Structured and explicitly bounded tool observations reduce context pollution while preserving sufficient semantic information for the agent to recognize result completeness, refine queries, and complete search-based tasks.

- **Status**: **SUPPORTED by empirical evidence**.
- **Implementation Details**:
  - `src/harness/tools/search_types.py`: Created `SearchResult` dataclass with `(matches, returned_count, total_count, truncated, count_complete)`.
  - `src/harness/tools/filesystem.py`:
    - Updated `SearchFilesTool.spec` with `max_results: int` (default 50, range 1–500) and `output_format: str` (`"text"` | `"json"`).
    - Preserved syntactic validation at `ToolContractValidator` boundary; semantic range and format checks handled inside `SearchFilesTool.execute`.
    - Separated collection (up to `max_results`) from match counting (up to `MAX_COUNTED_MATCHES = 10_000`).
    - Explicit text trailer: emits `"showing X of Y matches"` when exact, or `"showing X of at least 10000 matches; scan ceiling reached"` when incomplete.
  - `src/harness/agent/budget.py`: Defined authoritative default `max_observation_chars: int | None = 16_000` on `ExecutionBudget`.
  - `src/harness/config.py` & `src/harness/cli.py`: Integrated `max_observation_chars` into `AgentConfig` and wired directly through `build_controller` into `ToolExecutor`, guaranteeing that normal CLI execution is bounded by default.
  - `src/harness/tools/executor.py`: Implemented format-agnostic observation ceiling. Transforms oversized content into an explicit textual envelope `[OBSERVATION PARTIALLY SHOWN]` with `original_chars` and `shown_chars`, strictly guaranteeing:
    $$\text{len}(\text{final\_envelope}) \le \text{max\_observation\_chars}$$
    where `shown_chars` is the actual displayed payload prefix, which is strictly less than `max_observation_chars` due to envelope metadata.

---

## 3. Addressing the Three Core Research Questions

### Question 1: Does the tool tell the agent that results were truncated?
- **Baseline**: No. The observation dumped up to 50 raw lines with zero indication that additional matches existed.
- **Phase C Evidence**:
  - In Text mode: When `truncated=True`, appends explicit trailer: `[TRUNCATED: showing 50 of 200 matches. Refine query or path to narrow results.]`.
  - In JSON mode: Contains `"truncated": true`, `"returned_count": 50`, `"total_count": 200`.
  - Zero silent truncation observed across all 5 evaluation scenarios and 16 unit tests.

### Question 2: Can the agent know returned count, total count, and whether more results exist?
- **Baseline**: The tool terminated traversal upon reaching 50 matches, meaning neither the tool nor the agent knew `total_count`.
- **Phase C Evidence**:
  - Small searches (5 matches): Reports `returned_count=5`, `total_count=5`, `truncated=False`, `count_complete=True`.
  - Bounded searches (200 matches, `max_results=5`): Reports `returned_count=5`, `total_count=200`, `truncated=True`, `count_complete=True`.
  - Massive searches (>10,000 matches): Reports `returned_count=10`, `total_count=10000`, `truncated=True`, `count_complete=False` with explicit message `"at least 10000 matches; scan ceiling reached"`. The system never misrepresents a capped count as an exact total.

### Question 3: Does bounding large observations improve context usage without reducing task success?
- **Baseline**: Unbounded character dumps. A 42,500-character file read or an unbudgeted tool return injected full raw text into `self.context`.
- **Phase C Evidence**:
  - **Tool-Level Budgeting (H7b)**: Setting `max_results=5` on a 200-match file reduced observation size from 2,440 chars (Phase B 50-match baseline) to 293 chars (**88.0% reduction vs Phase B**; 97.0% reduction vs the 9,692-char full result).
  - **Generic Observation Ceiling (H7c)**: The harness-level ceiling strictly bounded a 42,500-char `read_file` observation to $\le 4,000$ characters (**90.6% genuine baseline reduction vs Phase B**), formatted within an unambiguous envelope.
  - **Refinement Trajectory Compatibility**: The harness successfully propagated truncation metadata in Turn 1 and supported a subsequent narrowing action in Turn 2, allowing the agent to locate the target needle in 84 characters (**96.7% reduction vs broad observation**).

---

## 4. Dedicated Phase C Evaluation Suite Results (`evaluation/phase_c_results.json`)

The dedicated evaluation suite (`evaluation/context_budgeting_suite.py`) executed 5 focused scenarios measuring empirical character volumes, reduction ratios, and scan durations:

| ID | Scenario Name | Hypothesis | Status | Steps / Tools | Phase B Chars | Full Result Chars | Phase C Chars | Reduction vs Phase B | Scan Duration | Key Empirical Findings |
| :-: | :--- | :--- | :-: | :-: | :-: | :-: | :-: | :-: | :-: | :--- |
| **01** | `small_exhaustive_search_exact_count` | H7a | **PASS** | 2 / 1 | 322 | 322 | 322 | 0.0% | 0.81ms | Exhaustive search reports `count_complete=True` and `truncated=False` with zero trailer. |
| **02** | `retrieval_truncation_configured_budget` | H7a / H7b | **PASS** | 2 / 1 | 2,440 | 9,692 | 293 | **88.0%** | 0.73ms | Caller requested `max_results=5`: returns 5 matches + trailer; total_count confirmed at 200. |
| **03** | `counting_ceiling_lower_bound_honesty` | H7a | **PASS** | 2 / 1 | 2,440 | 576,894 | 553 | **77.3%** | 3.32ms | Match count capped at `MAX_COUNTED_MATCHES=10,000`; output honestly emits lower bound notice. |
| **04** | `generic_observation_ceiling_envelope` | H7c | **PASS** | 2 / 1 | 42,500 | 42,500 | 4,000 | **90.6%** | 0.52ms | Genuine baseline reduction: envelope strictly satisfies $4,000 \le 4,000$ chars (`shown=3929 < 4000`). |
| **05** | `multi_turn_search_refinement_trajectory` | H7a / H7b | **PASS** | 3 / 2 | 2,440 | 9,741 | 84 | **96.7%** | 1.94ms | Harness propagated truncation trailer in Turn 1 and supported refinement to locate needle in Turn 2. |

### Note on Scenario 5 Causal Scope
Scenario 5 demonstrates **harness trajectory compatibility**: truncation metadata is successfully surfaced in context and the harness handles subsequent refined query execution without degradation. It does **not** claim that the notice causally compelled a deterministic scripted mock to decide to refine. Evaluating the cognitive decision-making effect on an actual LLM would require repeated, multi-trial live testing (e.g. against InnKube Qwen-35B) measuring refinement frequency across identical prompts with and without truncation warnings.

---

## 5. Historical Benchmark Evaluation & Scientific Integrity

In accordance with scientific evaluation discipline, `evaluation/benchmark.py` was kept **100% byte-for-byte unchanged**:
- **Outcome**: 14 / 15 tasks passed; 14 EXPECTED-SAFE, 1 UNDER-SPECIFIED, 0 UNSAFE.
- **Task 13 Outcome**:
  - Historical assertion in `benchmark.py`: `has_50_lines = len(result_lines) == 50`.
  - Under Phase C, `search_files` returned 50 matches plus 1 explicit truncation notice trailer (51 lines total).
  - Consequently, `has_50_lines` evaluated to `False`, reflecting that the historical test encoded the old silent-truncation defect.
  - As directed, we report this result honestly rather than rewriting the historical instrument to force a green result. The historical benchmark demonstrates that the defect existed in V0/Phase A/Phase B, while `evaluation/context_budgeting_suite.py` provides the Phase-C-specific evidence proving the new semantic contract.

---

## 6. Architectural Boundaries & Invariants

1. **Strict Envelope Invariant**:
   - `len(final_tool_result.content) <= max_observation_chars` is strictly enforced over the entire envelope (header + shown prefix).
   - Direct unit tests verify across multiple payload and ceiling combinations (e.g. 50,000 chars into 4,000 ceiling yields exactly 4,000 chars with `shown_chars=3929`).
2. **Single Source of Truth**:
   - `ExecutionBudget.max_observation_chars` is the authoritative configuration value, defaulting to 16,000.
   - `build_controller` in `src/harness/cli.py` wires `config.agent.max_observation_chars` into `budget`, which `ReActController.__init__` propagates to `tool_executor.max_observation_chars`.
3. **Preserved Validation Boundaries**:
   - Syntactic contract validation (`ToolContractValidator`) remains minimal (primitive types, required fields, `additionalProperties: False`).
   - Semantic validation (`1 <= max_results <= 500`, `output_format in {"text", "json"}`) is handled inside `SearchFilesTool.execute`, returning typed `ErrorCode.INVALID_ARGUMENT`.
4. **Counting Ceiling Scope**:
   - `MAX_COUNTED_MATCHES = 10_000` bounds continued match counting after the result set is filled. It does not claim to bound filesystem scan work or lines read.
