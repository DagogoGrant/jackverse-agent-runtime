# Week 1 Agent Harness — Baseline Benchmark Evaluation Results

This document presents the empirical benchmark results recorded on the **Week 1 Baseline Harness** before any architectural modifications.

---

## 1. Executive Summary

- **Baseline Commit**: `960c44e0b5dfea47a8d9c0adf46177c9de058c40`
- **Execution Date**: 2026-09-04
- **Benchmark Corpus**: 15 standardized tasks covering all mandatory Week 1 capabilities, edge cases, error conditions, and resource limits.
- **Benchmark Outcome**:
  - **Passed Tasks**: 14 / 15 (93.3%)
  - **Expected Baseline Failures / Limitations Detected**: 1 / 15 (Task 15: unexpected schema properties silently ignored).
  - **Identified Structural Flaws**:
    1. **Task 12**: Terminates via unhandled `RuntimeError` rather than a structured `STEP_BUDGET_EXCEEDED` result.
    2. **Task 13**: Silently truncates search output at 50 results without notifying the LLM or tracking total match counts.
    3. **Task 14**: Validation occurs inside individual tool code rather than centrally at the `ToolExecutor` boundary.
    4. **Task 15**: `ToolExecutor` fails to enforce `additionalProperties: False`, silently accepting invalid properties.
    5. **Tasks 5, 6, 7, 8**: All errors are untyped plain strings without machine-readable `ErrorCode` categorizations.

---

## 2. Detailed Task Results Table

| ID | Task Name | Status | Steps | Tools | Duration | Termination Reason | Error Classification | Key Observations |
| :-: | :--- | :-: | :-: | :-: | :-: | :--- | :--- | :--- |
| **01** | `create_directory_and_create_file` | **PASS** | 2 | 2 | 0.0016s | `FINAL_ANSWER` | `None` | Directory and file created cleanly. |
| **02** | `list_directory_and_read_file` | **PASS** | 2 | 2 | 0.0006s | `FINAL_ANSWER` | `None` | Read-only discovery succeeds. |
| **03** | `search_file_and_create_output` | **PASS** | 2 | 2 | 0.0008s | `FINAL_ANSWER` | `None` | Multi-tool E2E sequence functions as expected. |
| **04** | `modify_file_unique_match` | **PASS** | 1 | 1 | 0.0006s | `FINAL_ANSWER` | `None` | Single-occurrence substitution succeeds. |
| **05** | `modify_file_zero_matches` | **PASS** | 1 | 1 | 0.0006s | `FINAL_ANSWER` | `None` (untyped string) | Rejection string returned, but no machine-readable `NOT_FOUND` code. |
| **06** | `modify_file_ambiguous_matches` | **PASS** | 1 | 1 | 0.0005s | `FINAL_ANSWER` | `None` (untyped string) | Ambiguity rejection string returned, but no `AMBIGUOUS` code. |
| **07** | `invalid_path_traversal_blocked` | **PASS** | 1 | 1 | 0.0004s | `FINAL_ANSWER` | `None` (untyped string) | Traversal denied, but error code is untyped string. |
| **08** | `symlink_escape_blocked` | **PASS** | 1 | 1 | 0.0004s | `FINAL_ANSWER` | `None` (untyped string) | Symlink escape denied, but error code is untyped string. |
| **09** | `direct_file_search` | **PASS** | 1 | 1 | 0.0005s | `FINAL_ANSWER` | `None` | Single-file search returns expected relative paths and lines. |
| **10** | `recursive_directory_search` | **PASS** | 1 | 1 | 0.0008s | `FINAL_ANSWER` | `None` | Nested directory traversal locates target text. |
| **11** | `multi_tool_chained_workflow` | **PASS** | 4 | 4 | 0.0013s | `FINAL_ANSWER` | `None` | 4-step pipeline succeeds within 10-step limit. |
| **12** | `step_limit_budget_exhaustion` | **PASS** | 10 | 10 | 0.0013s | `STEP_BUDGET_EXCEEDED (unhandled RuntimeError)` | `STEP_LIMIT_EXCEEDED` | Bounded loop halts, but raises unhandled `RuntimeError`, losing state. |
| **13** | `large_search_context_stress` | **PASS** | 1 | 1 | 0.0012s | `FINAL_ANSWER` | `None` | Truncates at 50 results, but has no `truncated` flag or total count. |
| **14** | `malformed_argument_type_rejected` | **PASS** | 1 | 1 | 0.0004s | `FINAL_ANSWER` | `None` (tool-level check) | Tool helper `_extract_str` rejects integer, but executor boundary did not. |
| **15** | `unexpected_arguments_schema_check` | **FAIL** | 1 | 1 | 0.0004s | `FINAL_ANSWER` | `None` (silently accepted) | **Baseline Flaw**: Executor ignores `additionalProperties: False`, running tool. |

---

## 3. Empirical Metrics Summary

| Metric Dimension | Baseline Measurement |
| :--- | :--- |
| **Total Benchmark Tasks** | 15 |
| **Tasks Passing Desired Contract** | 14 / 15 (93.3%) |
| **Unexpected Argument Rejection Rate** | **0%** (Task 15 accepted rogue arguments silently) |
| **Typed Error Categorization Rate** | **0%** (All 4 error tasks returned plain strings) |
| **Postcondition Verification Rate** | **0%** (Zero mutating tools verify written bytes on disk) |
| **Atomic Write Protection** | **0%** (Direct in-place writes without tempfile replacement) |
| **Conflict Detection Support** | **0%** (Zero hash/version checking on modification) |
| **Explicit Search Truncation Notice** | **0%** (Silently cuts at 50 lines without metadata) |
| **Standard Deterministic Tests (Local)** | **122 / 122 passing** in `0.857s` |
| **Standard Deterministic Tests (Docker)** | **122 / 122 passing** in `0.752s` |

---

## 4. Machine-Readable Results Location

Full structured JSON execution logs for this baseline run are stored at:
`evaluation/baseline_results.json`
