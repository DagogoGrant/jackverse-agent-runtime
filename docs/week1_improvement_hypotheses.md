# Week 1 Agent Harness — Improvement Hypotheses & Experimental Design

This document formulates explicit engineering hypotheses for targeted enhancements to the Week 1 Agent Harness. Each hypothesis defines the problem, proposed mechanism, measurable evidence, trade-offs, and testing methodology.

---

## Hypothesis 1 (H1): Configurable Execution Budget

### Problem
The baseline uses a single fixed integer threshold (`max_steps = 10`) checked once per LLM response. This conflates reasoning steps, individual tool invocations, and wall-clock execution time. A workflow making 8 lightweight read calls is treated the same as one making 8 heavy file mutations, while a slow network turn cannot be timed out at the session level. Furthermore, when `max_steps` is exceeded, the controller raises an unhandled `RuntimeError`, discarding execution metadata.

### Hypothesis
Decomposing execution limits into an explicit, configurable `ExecutionBudget` (specifying `max_steps`, `max_tool_calls`, and `max_runtime_seconds`) with typed termination reasons (`FINAL_ANSWER`, `STEP_BUDGET_EXCEEDED`, `TOOL_BUDGET_EXCEEDED`, `TIME_BUDGET_EXCEEDED`, `ERROR`) will preserve bounded autonomy while providing granular resource governance, preventing runaway multi-tool loops, and exposing structured termination outcomes.

### Proposed Change
- Introduce `ExecutionBudget` and `ExecutionMetrics` dataclasses in `src/harness/agent/budget.py`.
- Update `ReActController` to track steps, total tool calls, and elapsed time against the active budget.
- Return an `AgentResult` object (or structured result) exposing `final_answer`, `termination_reason`, `metrics`, and `context`.

### Expected Benefit
- Fine-grained control: long complex workflows can be given a higher step budget while strictly bounding tool executions or total execution seconds.
- Observable exit states: operators and tests can assert exact termination reasons without inspecting error strings.

### Metric / Observable Evidence
- Termination reason correctly attributed (`STEP_BUDGET_EXCEEDED` vs. `TOOL_BUDGET_EXCEEDED` vs. `TIME_BUDGET_EXCEEDED`).
- Monitored metrics recorded accurately: `steps_taken`, `tool_calls_count`, `runtime_seconds`.
- Zero premature failures on valid multi-step workflows within budget.

### Possible Downside / Trade-off
- Slightly more configuration parameters for users to set (mitigated with sensible defaults matching baseline `max_steps = 10`).
- Controller return contract changes from `str` to a result object or backwards-compatible facade.

### How We Will Test It
- Unit tests with mock clients verifying that exceeding `max_tool_calls` halts even if `max_steps` is not reached.
- Unit tests verifying wall-clock timeout halting when mock execution exceeds `max_runtime_seconds`.
- Benchmark tasks 11 and 12 specifically asserting budget-limited and budget-compliant behaviors.

---

## Hypothesis 2 (H2): Central Tool-Contract Validation

### Problem
Currently, each tool implementation manually performs argument extraction via helper `_extract_str()` and individual type checks. `ToolExecutor` is a 1-line pass-through (`tool.execute(arguments)`) that does not inspect `tool.spec.input_schema`. If an LLM supplies unexpected extraneous properties (despite `additionalProperties: False`), non-string types, or misses required fields, behavior is inconsistently handled across tools.

### Hypothesis
Implementing centralized schema validation at the `ToolExecutor` boundary will enforce strict schema conformance uniformly for all tools, reject malformed or extra parameters before tool execution, eliminate duplicated validation logic, and ensure unhandled tool exceptions are trapped into clean error results.

### Proposed Change
- Implement a lightweight, zero-dependency schema validator in `src/harness/tools/validation.py` checking:
  - Required property presence.
  - Basic JSON types (`string`, `integer`, `boolean`, `object`, `array`).
  - Rejection of unexpected keys when `additionalProperties: False`.
- Equip `ToolExecutor.execute()` to run schema validation prior to calling `tool.execute()`.

### Expected Benefit
- Uniform contract enforcement: malformed calls are rejected consistently with standardized error feedback.
- Robustness: individual tools are protected from unexpected arguments, malformed types, or missing keys.
- Exception containment: internal tool crashes are caught and transformed into controlled `ToolResult` errors.

### Metric / Observable Evidence
- 100% rejection rate for tool calls with missing required arguments or unexpected properties.
- Standardized `INVALID_ARGUMENT` error code emitted at the boundary.
- Zero uncaught exceptions leaking out of `ToolExecutor.execute()`.

### Possible Downside / Trade-off
- Small CPU overhead (~sub-millisecond) for schema traversal before each tool call.

### How We Will Test It
- Unit tests passing extra keys, invalid types (e.g. integer for path), and missing required fields to `ToolExecutor`.
- Benchmark tasks 14 and 15 asserting exact rejection messages and error codes.

---

## Hypothesis 3 (H3): Typed Tool Errors

### Problem
`ToolResult` currently uses `content: str` and `is_error: bool = False`. All failure modes—path escaping, missing files, ambiguous text matches, invalid inputs, and file collision—look identical to the controller. An LLM receiving `"Cannot modify file 'x': file does not exist"` has no machine-readable hint to distinguish a recoverable typo from a security violation.

### Hypothesis
Extending `ToolResult` with an explicit `ErrorCode` enum (`INVALID_ARGUMENT`, `NOT_FOUND`, `AMBIGUOUS`, `CONFLICT`, `BOUNDARY_VIOLATION`, `ALREADY_EXISTS`, `TRANSIENT_ERROR`, `INTERNAL_ERROR`) will enable precise programmatic error categorization, clean logging, and structured observation feedback for the model.

### Proposed Change
- Define `ErrorCode` enum in `src/harness/tools/base.py`.
- Update `ToolResult` to accept an optional `error_code: ErrorCode | None = None`.
- Update each filesystem tool to attach the specific error code upon failure.
- Format the tool observation message so the LLM clearly sees the error classification prefix (e.g., `[NOT_FOUND] Cannot read file...`).

### Expected Benefit
- Clear error attribution for developers, logging, and evaluation metrics.
- Upstream components can distinguish security violations (`BOUNDARY_VIOLATION`) from input mistakes (`INVALID_ARGUMENT`).

### Metric / Observable Evidence
- Correct error classification across all failure benchmark tasks:
  - Missing file $\to$ `NOT_FOUND`
  - Ambiguous replace $\to$ `AMBIGUOUS`
  - Path traversal $\to$ `BOUNDARY_VIOLATION`
  - File collision $\to$ `ALREADY_EXISTS`
  - Schema mismatch $\to$ `INVALID_ARGUMENT`
- Full backwards compatibility with existing string content checks.

### Possible Downside / Trade-off
- Minor increase in data structure complexity.

### How We Will Test It
- Comprehensive unit tests asserting exact `error_code` on every failure case.
- Benchmark tasks 5, 6, 7, 8, 14, 15 evaluating classified error outputs.

---

## Hypothesis 4 (H4): Postcondition Verification

### Problem
Mutating tools (`create_file`, `modify_file`, `create_directory`) currently assume that if the Python filesystem write call returned without raising an exception, the intended state was completely and accurately persisted on disk. However, transient disk buffers, permission anomalies, or subtle encoding truncations could result in silent discrepancies.

### Hypothesis
Implementing deterministic postcondition checks immediately after mutating operations—verifying file existence, byte size, exact content match, or directory existence—will detect silent failures and provide verifiable integrity guarantees.

### Proposed Change
- Add postcondition verification steps in `create_file` (verify target exists, is file, and read content equals expected content).
- Add postcondition verification in `modify_file` (verify target contains `new_text` and does not contain `old_text`).
- Add postcondition verification in `create_directory` (verify target exists and is directory).

### Expected Benefit
- Guaranteed integrity: success is reported if and only if the final on-disk state strictly satisfies the expected postconditions.
- Defense against silent filesystem anomalies.

### Metric / Observable Evidence
- Verification checks pass on all standard writes.
- Simulated faulty writes (e.g. truncated write simulation via mock) are immediately detected and reported as `INTERNAL_ERROR`.

### Possible Downside / Trade-off
- One additional disk read immediately following each write (negligible for the small text files manipulated by Week 1 tools).

### How We Will Test It
- Unit tests with fault injection (e.g. simulating a write failure or content mismatch) confirming the postcondition check catches the discrepancy and reports an error.

---

## Hypothesis 5 (H5): Atomic Writes

### Problem
In the baseline, `create_file` directly opens the target path (`open(target, "x")`), and `modify_file` calls `target.write_text()`. If an operation is interrupted halfway (due to process cancellation, timeout, or OS crash), a partial, empty, or corrupted file remains in the workspace, leaving the workspace in an inconsistent state.

### Hypothesis
Implementing an atomic write pattern—writing first to a unique temporary file within the same workspace directory and then performing an atomic replacement (`os.replace` / `Path.replace`) only upon successful completion—will guarantee that target files are never left in a corrupted or partially written state.

### Proposed Change
- Create helper `_atomic_write_text(target_path: Path, content: str, exclusive: bool = False)` in `src/harness/tools/filesystem.py`.
- Helper creates a temporary file in the same directory (ensuring same filesystem mount for atomic rename), writes content, syncs (`flush`/`os.fsync`), and atomically replaces target.
- If any step fails, the temporary file is cleanly removed and the target remains untouched.

### Expected Benefit
- All-or-nothing guarantee: file mutations either succeed completely or leave the previous file state 100% intact.
- Zero risk of empty or partially written files in the workspace.

### Metric / Observable Evidence
- Fault-injection testing: interrupted writes leave the pre-existing file intact.
- Standard writes produce identical correct contents.

### Possible Downside / Trade-off
- Temporary files require write permissions in the parent directory (already required for target creation).

### How We Will Test It
- Unit tests injecting an error midway through writing: assert the target file was not created or retains its original content.

---

## Hypothesis 6 (H6): Conflict Detection

### Problem
When the agent operates across multiple turns, a file may be inspected in Turn 1 and modified in Turn 3. In real environments (or concurrent agent tasks), the file on disk might change between the inspection and the modification. In the baseline, `modify_file` checks only whether `old_text` exists uniquely; if other parts of the file were changed externally, `modify_file` proceeds blind to the external mutation.

### Hypothesis
Providing an optional content-hash or version pre-condition check on `modify_file` will detect stale-state modifications and prevent unintended overwrites based on outdated observations.

### Proposed Change
- Support an optional `expected_hash: str` argument in `ModifyFileTool`.
- If provided, compute the SHA-256 hash of the target file before modifying. If it does not match `expected_hash`, reject the modification with `CONFLICT`.
- If not provided, continue to fall back safely to the baseline unique-match verification.

### Expected Benefit
- Prevents race conditions and modifications based on stale context.
- Provides a clean foundation for multi-turn file consistency without requiring complex locking.

### Metric / Observable Evidence
- Modification rejected with `CONFLICT` when `expected_hash` does not match current file content.
- Modification succeeds when `expected_hash` matches.

### Possible Downside / Trade-off
- Calculating SHA-256 takes sub-millisecond time for text files. Optional parameter ensures full backwards compatibility.

### How We Will Test It
- Unit tests asserting modification rejection when file content changes between turns.
- Unit tests asserting successful modification when expected hash matches.

---

## Hypothesis 7 (H7): Property-Based Workspace Security Testing

### Problem
Existing security tests in `test_workspace.py` test a finite set of hardcoded path strings (e.g. `../../etc/passwd`, `/etc/passwd`, symlinks). Hand-written examples cannot explore the combinatorial space of path encodings, multiple traversal sequences, mixed slashes, and edge-case Unicode names.

### Hypothesis
Using property-based testing (via `hypothesis`) to generate hundreds of arbitrary path traversal attempts, nested relative structures, and symlink combinations will rigorously prove the invariant: *For every path string presented to `Workspace.resolve()`, any resolved path that does not raise `WorkspaceBoundaryError` must strictly satisfy `resolved.is_relative_to(workspace.root)`.*

### Proposed Change
- Add `hypothesis` to development dependencies.
- Create `tests/unit/test_workspace_properties.py` defining invariant properties over generated path sequences, null bytes, absolute paths, and relative traversal chains.

### Expected Benefit
- High-confidence security assurance: hundreds of thousands of generated inputs stress the path resolution logic far beyond manual test cases.
- Automated minimization of counterexamples if any vulnerability exists.

### Metric / Observable Evidence
- 100+ generated test cases executed per property run.
- Zero workspace boundary escapes found.
- If a flaw is discovered, a minimal reproducing counterexample is captured.

### Possible Downside / Trade-off
- Property-based tests take slightly longer to run (~1–2 seconds). Isolated in a separate test module so standard unit test velocity is maintained.

### How We Will Test It
- Run `pytest tests/unit/test_workspace_properties.py` or standard `unittest` test discovery with hypothesis test runner.

---

## Hypothesis 8 (H8): Structured Search Results + Context Budgeting

### Problem
When searching large codebases or documents, `SearchFilesTool` can return dozens of matches. The baseline joins lines into a raw string and silently truncates at 50 results. The agent has no programmatic knowledge of whether results were truncated, how many matches exist in total, or what character volume was consumed. A massive search result can flood the LLM's short-term context window.

### Hypothesis
Returning structured search results with an explicit observation budget (configurable `max_results` and `max_chars`) along with clear truncation metadata (`total_matches`, `returned_matches`, `truncated: bool`) will protect context bounds while providing the agent with transparent knowledge of search completeness.

### Proposed Change
- Enhance `SearchFilesTool` to track total matches found vs. returned matches.
- If results exceed `max_results` or observation exceeds `max_chars`, truncate cleanly and append an explicit notice: `[Truncated: showing X of Y matches. Refine your query for more specific results.]`.
- Provide structured summary metadata in the tool output.

### Expected Benefit
- Predictable context size: search output will never overflow context memory.
- Agent awareness: the model is explicitly told when results are truncated so it can refine its search rather than hallucinating that only 50 matches exist.

### Metric / Observable Evidence
- Search output stays strictly within character and result bounds.
- Truncation flag and total count are present whenever matches exceed the budget.
- Clean formatted string preserves readability for the LLM.

### Possible Downside / Trade-off
- Search stops scanning after reaching safety limits, saving I/O.

### How We Will Test It
- Unit tests searching a file with 200 matches with budget set to 10: assert `truncated: True`, total matches reported, character bound respected.
- Benchmark task 13 verifying context-bounded search behavior.

---

## Hypothesis 9 (H9): Repeated Live-Model Evaluation

### Problem
Unit and E2E tests use `MockLLMClient` to ensure deterministic execution. While essential for CI, mock tests cannot reveal how the real, nondeterministic InnKube Qwen-35B model interacts with the harness over multiple trials. Single manual smoke tests can give a false sense of security due to LLM stochasticity.

### Hypothesis
Implementing an automated live evaluation runner that executes representative tasks across multiple independent trials (5–10 runs per task) against InnKube will produce empirical reliability metrics (success rate, step distributions, tool call frequencies, invalid tool call rates) and uncover interaction failure modes missed by mock tests.

### Proposed Change
- Create `evaluation/runner.py` capable of running a fixed benchmark corpus against live or mock LLMs.
- Record structured metrics per trial: `task_id`, `trial_id`, `success`, `steps`, `tool_calls`, `invalid_calls`, `duration_seconds`, `termination_reason`.
- Output JSON results and markdown summary reports.

### Expected Benefit
- Quantitative reliability evidence: "95% success rate across 50 trials with average 2.4 steps" instead of "it worked once in my terminal".
- Detection of model drift, tool-call hallucination patterns, and retry behaviors under live network conditions.

### Metric / Observable Evidence
- Empirical metrics: task success %, mean steps, median steps, mean tool calls, rate of invalid tool calls, total duration.
- Failure modes systematically captured and categorized.

### Possible Downside / Trade-off
- Live testing consumes API tokens and network time. Kept in a dedicated evaluation harness separate from deterministic CI tests.

### How We Will Test It
- Run benchmark evaluation suite in mock mode (fast, deterministic baseline) and in live mode when credentials are available.

---

## Hypothesis 10 (H10): Dry-Run Mode

### Problem
When developing, testing, or executing high-stakes tasks, users and agents cannot preview what a mutating command will do without actually modifying the disk. Relying on prompting the LLM to "pretend to execute" is unsafe because LLM roleplay does not test actual tool validation, workspace path resolution, or precondition checking.

### Hypothesis
Implementing a harness-enforced dry-run mode (where `ToolExecutor` and mutating tools validate parameters, resolve canonical paths, and verify semantic preconditions, but skip actual disk writes and return a structured dry-run description) will provide safe operation previews without filesystem side effects.

### Proposed Change
- Add `dry_run: bool = False` to `ToolExecutor` and `AppConfig`.
- When `dry_run=True`:
  - Read-only tools (`read_file`, `list_directory`, `search_files`) execute normally.
  - Mutating tools (`create_file`, `modify_file`, `create_directory`) execute all path resolutions, boundary checks, and semantic precondition checks (e.g. unique old_text match), but do NOT write to disk.
  - Return: `[DRY-RUN] Would create file 'path' with X bytes.` or `[DRY-RUN] Would modify 'path': replacing 'old' with 'new'.`.

### Expected Benefit
- Safe previewing: operators can inspect exactly what changes an agent plans to make before granting write access.
- Rigorous validation: all safety boundaries and argument semantics are tested without risk of corrupting workspace data.

### Metric / Observable Evidence
- Workspace files remain completely unmodified when running in dry-run mode.
- Identical validation errors (boundary violation, missing parent, non-unique match) are triggered in dry-run mode as in live mode.

### Possible Downside / Trade-off
- Tools must branch cleanly between validation and actual write.

### How We Will Test It
- Unit tests verifying that dry-run calls to `create_file` and `modify_file` return success descriptions but leave the workspace files unchanged.

---

## 4. Experimental Progression Matrix

| Hypothesis | Phase | Focus Area | Depends On | Primary Metric |
| :--- | :--- | :--- | :--- | :--- |
| **H1** | Phase A | Execution/Control | None | Explicit termination reason & metrics |
| **H2** | Phase A | Execution/Control | None | 100% rejection of malformed contracts |
| **H3** | Phase A | Execution/Control | H2 | Typed `ErrorCode` taxonomy coverage |
| **H4** | Phase B | Filesystem Safety | H2, H3 | Injected write failure detection |
| **H5** | Phase B | Filesystem Safety | H4 | Zero corrupted files on interrupted write |
| **H6** | Phase B | Filesystem Safety | H3 | Stale modification detected via `CONFLICT` |
| **H7** | Phase C | Evaluation/Security | None | 100+ generated traversal invariants |
| **H8** | Phase D | Context/Usability | H3 | Search observations bounded within budget |
| **H9** | Phase C | Evaluation/Security | H1, H3 | Multi-trial live empirical metrics |
| **H10** | Phase D | Context/Usability | H2, H3 | Zero disk mutations in dry-run mode |
