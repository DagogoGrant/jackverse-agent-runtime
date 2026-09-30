# Week 1 Agent Harness — Phase A Evaluation Report (H1, H2, H3)

This document records the empirical results, architectural changes, and evidence evaluations for **Phase A: Execution & Control** (`H1`, `H2`, `H3`), comparing them directly against the frozen baseline.

---

## 1. Executive Summary & Core Metrics

| Metric Dimension | Baseline (Commit `960c44e`) | Phase A (Current) | Delta / Evidence |
| :--- | :---: | :---: | :--- |
| **Deterministic Test Suite (Local)** | 122 tests (0.857s) | **139 tests** (0.765s) | **+17 tests**, 100% pass |
| **Deterministic Test Suite (Docker)** | 122 tests (0.752s) | **139 tests** (0.726s) | **+17 tests**, 100% pass |
| **Benchmark Task Pass Rate** | 14 / 15 (93.3%) | **15 / 15 (100.0%)** | Task 15 fixed |
| **System Behavior: UNSAFE** | 1 task (Task 15) | **0 tasks (0.0%)** | Defect eliminated |
| **System Behavior: EXPECTED-SAFE** | 8 tasks | **14 tasks (93.3%)** | +6 tasks hardened |
| **System Behavior: UNDER-SPECIFIED** | 6 tasks | **1 task (Task 13)** | Task 13 queued for Phase D (H8) |
| **Unexpected Property Rejection** | 0% (silently accepted) | **100%** (`INVALID_ARGUMENT`) | Enforced by `ToolContractValidator` |
| **Step Budget Termination Behavior** | Unhandled `RuntimeError` crash | Structured `RunResult` | Clean `STEP_BUDGET_EXCEEDED` |
| **Typed Error Categorization** | 0% (untyped strings) | **100%** (8 `ErrorCode` classes) | Machine-readable failure taxonomy |

---

## 2. Hypothesis Evaluations & Empirical Evidence

### Hypothesis 1 (H1: Configurable Execution Budget)
- **Claim**: Decomposing limits into `ExecutionBudget` (`max_steps`, `max_tool_calls`, `max_runtime_seconds`) with explicit termination reasons will preserve bounded execution without unhandled crashes.
- **Implementation**:
  - Created `src/harness/agent/budget.py` with `ExecutionBudget`, `TerminationReason`, and `RunResult`.
  - Updated `ReActController` to check step budget, tool-call budget (checked before incrementing/executing), and cooperative runtime seconds.
  - Implemented `run_turn() -> RunResult` while preserving backwards-compatible `run() -> str`.
- **Evidence**:
  - `tests/unit/test_budget.py` (5 tests):
    - Tool-call budget halts at exactly `max_tool_calls=2` without over-counting.
    - Cooperative time budget halts cleanly on duration overrun.
    - Step budget produces `STEP_BUDGET_EXCEEDED` in `RunResult`.
  - Benchmark Task 12: Baseline crashed with unhandled `RuntimeError`; Phase A returned `RunResult(termination_reason=STEP_BUDGET_EXCEEDED, steps=10, tool_calls=10)` with zero unhandled exceptions.
- **Conclusion**: **PARTIALLY SUPPORTED at this stage**. The architectural and correctness claims are demonstrated, including clean termination, independent resource accounting, and accurate budget enforcement. The claim that the new budget model improves success on legitimate longer workflows still requires agent-level evaluation on complex tasks.

---

### Hypothesis 2 (H2: Central Tool Contract Validation)
- **Claim**: Central syntactic validation at the `ToolExecutor` boundary will enforce schemas uniformly and reject unexpected parameters before tool execution.
- **Implementation**:
  - Implemented `ToolContractValidator` in `src/harness/tools/validation.py` checking required fields, primitive types, and `additionalProperties: False`.
  - Updated `ToolExecutor` to validate arguments before calling `tool.execute()` and trap unhandled exceptions safely into generic `INTERNAL_ERROR` observations without leaking raw Python traces.
- **Evidence**:
  - `tests/unit/test_validation.py` (6 tests):
    - Missing required argument rejected with `INVALID_ARGUMENT`.
    - Integer path or boolean count rejected with `INVALID_ARGUMENT`.
    - Extra argument rejected when `additionalProperties: False`.
    - Catastrophic internal exception caught and converted to generic message.
  - Benchmark Task 15: Baseline failed by silently running with `"unexpected_param": "rogue_value"`. Phase A cleanly rejected the call at the boundary with `Schema validation error: Unexpected argument 'unexpected_param'`.
- **Conclusion**: **SUPPORTED**.

---

### Hypothesis 3 (H3: Typed Tool Errors)
- **Claim**: Introducing an explicit `ErrorCode` enum will enable machine-readable failure attribution while preserving human-readable messages.
- **Implementation**:
  - Defined `ErrorCode` in `src/harness/tools/base.py`: `INVALID_ARGUMENT`, `NOT_FOUND`, `AMBIGUOUS`, `CONFLICT`, `ALREADY_EXISTS`, `BOUNDARY_VIOLATION`, `TRANSIENT_ERROR`, `INTERNAL_ERROR`.
  - Updated all 6 filesystem tools to return appropriate `error_code` on every failure branch.
  - Added migration compatibility: unclassified legacy errors default to `INTERNAL_ERROR`.
- **Evidence**:
  - `tests/unit/test_typed_errors.py` (6 tests):
    - `read_file` on missing file returns `NOT_FOUND`.
    - `create_file` collision returns `ALREADY_EXISTS`.
    - `modify_file` non-unique match returns `AMBIGUOUS`.
    - Path traversal returns `BOUNDARY_VIOLATION`.
    - Tool errors are observed by model in next turn without triggering transport retries.
  - Benchmark Tasks 5, 6, 7, 8: All transitioned from untyped string errors (`UNDER-SPECIFIED`) to structured, categorized errors (`EXPECTED-SAFE`).
- **Conclusion**: **SUPPORTED within tested scope**. 100% of the failure cases represented in the current benchmark and tested Week 1 error paths received machine-readable categories. Unexpected or unhandled exceptions fallback safely to `INTERNAL_ERROR`.

---

## 3. Detailed Benchmark Comparison (Baseline vs. Phase A)

| Task ID & Name | Baseline Status & Behavior | Phase A Status & Behavior | Improvement Note |
| :--- | :--- | :--- | :--- |
| **01. create_directory_and_create_file** | PASS (EXPECTED-SAFE) | PASS (EXPECTED-SAFE) | Preserved core capability. |
| **02. list_directory_and_read_file** | PASS (EXPECTED-SAFE) | PASS (EXPECTED-SAFE) | Preserved core capability. |
| **03. search_file_and_create_output** | PASS (EXPECTED-SAFE) | PASS (EXPECTED-SAFE) | Preserved E2E multi-tool sequence. |
| **04. modify_file_unique_match** | PASS (EXPECTED-SAFE) | PASS (EXPECTED-SAFE) | Preserved single-match substitution. |
| **05. modify_file_zero_matches** | PASS (UNDER-SPECIFIED) | **PASS (EXPECTED-SAFE)** | Error classified as `NOT_FOUND`. |
| **06. modify_file_ambiguous_matches** | PASS (UNDER-SPECIFIED) | **PASS (EXPECTED-SAFE)** | Error classified as `AMBIGUOUS`. |
| **07. invalid_path_traversal_blocked** | PASS (UNDER-SPECIFIED) | **PASS (EXPECTED-SAFE)** | Error classified as `BOUNDARY_VIOLATION`. |
| **08. symlink_escape_blocked** | PASS (UNDER-SPECIFIED) | **PASS (EXPECTED-SAFE)** | Error classified as `BOUNDARY_VIOLATION`. |
| **09. direct_file_search** | PASS (EXPECTED-SAFE) | PASS (EXPECTED-SAFE) | Direct-file search preserved. |
| **10. recursive_directory_search** | PASS (EXPECTED-SAFE) | PASS (EXPECTED-SAFE) | Recursive search preserved. |
| **11. multi_tool_chained_workflow** | PASS (EXPECTED-SAFE) | PASS (EXPECTED-SAFE) | 4-step pipeline executed. |
| **12. step_limit_budget_exhaustion** | PASS (UNDER-SPECIFIED) | **PASS (EXPECTED-SAFE)** | Replaced unhandled `RuntimeError` with structured `STEP_BUDGET_EXCEEDED`. |
| **13. large_search_context_stress** | PASS (UNDER-SPECIFIED) | PASS (UNDER-SPECIFIED) | Unchanged in Phase A; targeted for Phase D (H8 context budgeting). |
| **14. malformed_argument_type_rejected** | PASS (UNDER-SPECIFIED) | **PASS (EXPECTED-SAFE)** | Enforced at boundary with `INVALID_ARGUMENT`. |
| **15. unexpected_arguments_schema_check** | **FAIL (UNSAFE)** | **PASS (EXPECTED-SAFE)** | **Fixed**: Boundary rejected rogue parameters via `additionalProperties: False`. |

---

## 4. Architectural Boundaries & Trade-offs Introduced

1. **Cooperative vs. Preemptive Runtime**:
   `max_runtime_seconds` is evaluated cooperatively between turns. An in-flight LLM call that takes 30s will not be interrupted mid-stream by the agent loop (it remains bounded by `LLMClient.timeout`).
2. **Validator Scope**:
   `ToolContractValidator` deliberately covers only the JSON-schema subset declared by Week 1 ToolSpecs. It does not implement complex keywords like `$ref`, `oneOf`, or regex patterns, keeping zero external dependencies.
3. **Exception Shielding**:
   Internal tool crashes are caught at the `ToolExecutor` boundary, logged with tracebacks, and returned as generic observations to prevent leaking implementation details to LLMs.
4. **Tool Result Migration Default**:
   `ToolResult(content=..., is_error=True)` defaults `error_code` to `INTERNAL_ERROR` if omitted, allowing legacy code to function while new tools supply explicit codes.
