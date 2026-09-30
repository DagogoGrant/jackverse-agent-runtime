# Week 1 Agent Harness — Baseline Engineering Evaluation

This document establishes the empirical and architectural baseline of the **Week 1 Agent Harness** prior to incremental engineering enhancements.

---

## 1. System Environment & Baseline Metadata

| Dimension | Baseline Specification |
| :--- | :--- |
| **Baseline Git Commit** | `960c44e0b5dfea47a8d9c0adf46177c9de058c40` (`main`) |
| **Frozen Milestone Tag** | `demo-week1` at `235fcaf2c11f02a4c2b599c05b679553738cb863` |
| **Python Version (Local)** | `Python 3.12.11` (`venv-repro312`) |
| **Docker Base Image** | `python:3.12-slim-bookworm` (`agent-harness:latest`) |
| **Model Endpoint** | `https://llms.innkube.fim.uni-passau.de` |
| **Default Model** | `qwen-agentworld-35b-a3b` |
| **Model Parameters** | `temperature: 0.0`, `timeout: 30.0s`, `max_retries: 2`, `retry_backoff: 0.5s` |
| **Deterministic Test Count** | **122 tests total** (120 unit tests + 2 E2E acceptance tests) |
| **Local Test Runtime** | `0.857s` (122 / 122 passed, 0 failures, 0 errors) |
| **Docker Test Runtime** | `0.752s` (122 / 122 passed, 0 failures, 0 errors) |

---

## 2. Functional Scope Categorization

To maintain clear boundary control and prevent scope creep, we explicitly distinguish the functional layers:

### A. Mandatory Week 1 Core Capabilities
1. **Bounded ReAct Cycle**: Reason $\to$ Act $\to$ Observe loop with step threshold termination.
2. **Workspace Containment**: Strict confinement of all filesystem operations to a designated root directory (`./workspace`).
3. **Six Atomic Filesystem Tools**:
   - `create_directory`: Directory creation with parents (`mkdir -p`).
   - `create_file`: Non-overwriting file creation.
   - `read_file`: UTF-8 text file reading.
   - `list_directory`: Immediate directory entry enumeration with `DIR`/`FILE` categorization.
   - `search_files`: Regular text file searching matching queries across files and directories. *(Note: Required Week 1 core capability).*
   - `modify_file`: Exact, unique target text chunk substitution.
4. **LLM Integration**: OpenAI SDK wire protocol with structured function calling against InnKube.
5. **Interactive CLI**: Terminal REPL with startup summary and local commands (`/help`, `/tools`, `/config`, `exit`).

### B. Robustness Improvements Already Present in Baseline
1. **Transparent Transport Retries**: Bounded exponential backoff in `LLMClient` for 5xx/timeouts with SDK-level retries disabled (`max_retries=0`).
2. **Markdown-Fence JSON Sanitization**: Cleans ````json ... ```` fences from model tool arguments before JSON decoding.
3. **Path Canonicalization & Symlink Containment**: `Workspace.resolve()` uses `.resolve(strict=False)` and `.is_relative_to()`, with symlink directory pruning in `search_files`.
4. **Token Usage Extraction**: Extracts `prompt_tokens`, `completion_tokens`, `total_tokens` when reported by the provider.
5. **Direct File Path Search**: `SearchFilesTool` accepts both directory paths and direct file paths.
6. **Local Command Isolation**: `/help`, `/tools`, and `/config` short-circuit locally without LLM calls, and `/config` conceals the API key.

### C. Architectural Limitations Targeted for Improvement
1. **Single Fixed Step Limit**: Only `max_steps = 10` exists. Time budget, tool-call count budget, and execution metrics are unmeasured.
2. **No Central Schema Enforcement**: Tool argument validation is duplicated across individual tools; `ToolExecutor` is a 1-line pass-through.
3. **Untyped String Errors**: `ToolResult` contains a boolean `is_error` and arbitrary string `content`. Recoverable vs. fatal failures cannot be distinguished programmatically.
4. **Non-Atomic Mutations**: `create_file` uses `open(..., "x")` and `modify_file` uses direct in-place writes; an interrupt leaves partial or corrupted files.
5. **Lack of Postcondition Verification**: Mutating tools assume filesystem success if the OS call succeeds without independently verifying disk state.
6. **No Conflict Detection**: No content hashing or version checking exists to detect stale concurrent modifications.
7. **Example-Only Boundary Tests**: Workspace containment is tested only on hand-written cases without property-based invariant generation.
8. **Unbounded Search Observation Context**: Search outputs return raw matched lines (capped at 50 results) without character/token budget controls or structured truncation metadata.
9. **No Dry-Run Execution Mode**: Operators cannot test or preview agent actions without actually modifying the disk.
10. **Lack of Systematic Model Benchmarking**: Evaluation relies on manual scripts rather than a multi-trial benchmark with empirical metrics.

---

## 3. Detailed Baseline Component Analysis

### 3.1 ReAct Controller & Step Bounding (`src/harness/agent/react.py`)
- **Current Behavior**:
  ```python
  while True:
      if steps_taken >= self.max_steps:
          logger.warning(f"step_limit_exceeded run_id={run_id} max_steps={self.max_steps}")
          raise RuntimeError(f"Maximum agent steps ({self.max_steps}) reached before final response.")
  ```
- **Limitations**:
  - `steps_taken` increments once per LLM turn, regardless of how many tool calls were in that turn (1 tool call vs. 5 parallel tool calls both consume 1 step).
  - No wall-clock timeout: if network requests or tools are slow, a run could take minutes without budget exhaustion.
  - Termination produces an unhandled `RuntimeError` rather than a structured status object indicating why termination occurred (`STEP_BUDGET_EXCEEDED`, `TIME_BUDGET_EXCEEDED`, `FINAL_ANSWER`).

### 3.2 Tool Validation & Execution Boundary (`src/harness/tools/executor.py`)
- **Current Behavior**:
  ```python
  class ToolExecutor:
      def execute(self, tool: Tool, arguments: Mapping[str, object]) -> ToolResult:
          return tool.execute(arguments)
  ```
- **Limitations**:
  - `ToolExecutor` performs zero validation. It does not validate `arguments` against `tool.spec.input_schema`.
  - Tools manually validate required fields via `_extract_str(arguments, ...)`.
  - Unexpected extra properties are not rejected at the boundary even though `additionalProperties: False` is declared in schemas.
  - Any unexpected exception raised within a tool is not caught by `ToolExecutor`.

### 3.3 Error Representation (`src/harness/tools/base.py`)
- **Current Behavior**:
  ```python
  @dataclass(frozen=True)
  class ToolResult:
      content: str
      is_error: bool = False
  ```
- **Limitations**:
  - `is_error=True` does not categorize the failure. A missing file (`NOT_FOUND`), a boundary traversal attempt (`BOUNDARY_VIOLATION`), a malformed argument (`INVALID_ARGUMENT`), and an ambiguous replacement (`AMBIGUOUS`) all produce the same shape.
  - The ReAct controller and upstream callers cannot make programmatic recovery decisions without fragile substring regex matching on `content`.

### 3.4 Filesystem Mutation Safety (`src/harness/tools/filesystem.py`)
- **Current Behavior**:
  - `CreateFileTool`: `with open(target, "x", encoding="utf-8") as f: f.write(content)`
  - `ModifyFileTool`: `target.write_text(updated_content, encoding="utf-8")`
- **Limitations**:
  - Neither operation is atomic. If the process is terminated, disk runs out of space, or write fails halfway, a corrupted or partially written file remains.
  - Neither operation performs a postcondition read-back check to verify that the file actually exists and contains the expected byte sequence.
  - Neither operation checks whether the file was modified since the agent last inspected it (no conflict detection).

### 3.5 Search Result Representation & Observation Sizing (`src/harness/tools/filesystem.py`)
- **Current Behavior**:
  - Results are collected in a string list and joined with `\n`.
  - Capped at `MAX_SEARCH_RESULTS = 50`.
- **Limitations**:
  - When capped, the output silently stops at 50 results. The agent is not told how many total matches existed, nor is a structured `truncated: true` flag returned.
  - 50 lines of long text can inject thousands of characters into the prompt context, crowding out instructions and intermediate reasoning.

### 3.6 Automated Testing & Evaluation Strategy
- **Current State**:
  - **Deterministic Unit Tests**: 120 tests covering CLI, config, executor, filesystem tools, LLM client parsing/retry, ReAct step bounding, registry, and workspace boundary.
  - **Deterministic E2E Tests**: 2 tests (`test_numbers_in_words.py`, `test_find_and_inspect.py`) using `MockLLMClient` with scripted response queues.
  - **Live Evaluation**: Manual experiment scripts (`scripts/test_react_live.py`, `scripts/test_tool_calling.py`). No multi-trial metric tracking, no automated calculation of step distributions, invalid tool call rates, or failure classifications across repeated runs.

---

## 4. Summary of Baseline State

The baseline Week 1 harness is functionally complete, thoroughly verified (122 tests passing locally and in Docker), and adheres strictly to safety invariants (no deletion, workspace containment). The identified architectural limitations form the basis for our targeted, hypothesis-driven improvements.
