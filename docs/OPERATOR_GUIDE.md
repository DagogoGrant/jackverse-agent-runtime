# JackVerse Agent Runtime: Operator Runbook

Welcome to the **JackVerse Agent Runtime Operator & Verification Guide**.

This runbook allows an operator, developer, or systems architect to sit down at the system with **zero manual configuration** and independently explore, execute, and verify bounded agent execution, memory firewalls, MCP resilience, contextual governance, and distributed telemetry within **10–15 minutes**.

---

## 1. Quick Start: Single Entrypoint

Launch the entire ecosystem with one command:

```bash
make showcase
```

This single command:
1. Builds and starts all Docker containers (`harness`, `transport-mcp`, `prometheus`, `tempo`, `grafana`).
2. Performs bounded health probes until the harness metrics endpoint (`:9101`) and transport service are ready.
3. Prints the live observability URLs.
4. Attaches the **Live AI Runtime Operator Console (TUI)** to your terminal.

> **Observability URLs:**
> - **Grafana Operations Dashboard:** [http://localhost:3000/d/agent-harness-runtime/agent-harness-operations](http://localhost:3000/d/agent-harness-runtime/agent-harness-operations) (Login: `admin` / `admin`)
> - **Tempo Distributed Trace Explorer:** [http://localhost:3000/explore](http://localhost:3000/explore) (Datasource: `tempo`)
> - **Prometheus Metrics Engine:** [http://localhost:9090](http://localhost:9090)
> - **Raw Harness Metrics Exposition:** [http://localhost:9101/metrics](http://localhost:9101/metrics)
> - **Transport MCP Endpoint:** [http://localhost:8000/mcp](http://localhost:8000/mcp)

---

## 2. Pre-Flight Diagnostics: `jackverse doctor`

Before launching agents or long-running scenarios, operators can independently verify LLM provider configuration and live upstream capabilities.

### Offline Configuration Mode
Performs local syntax, endpoint parsing, provider factory resolution, and credential checks without initiating network requests:
```bash
python -m harness doctor
```

### Live Verification Mode (`--live`)
Executes non-destructive end-to-end probes against the live endpoint:
1. **Network Reachability:** Verifies TCP/TLS handshake with the remote host.
2. **Authentication Acceptance:** Confirms credentials are accepted upstream.
3. **Model Response:** Asserts model availability and responsiveness.
4. **Structured Tool Calling Compatibility:** Dispatches a synthetic, non-executed tool spec (`health_check`) to ensure the model natively supports JSON tool calling required by ReAct loops.

```bash
python -m harness doctor --live
```

> **Secret Redaction:** All diagnostic checks and error messages automatically redact API keys, tokens, and authorization headers (`[REDACTED]`).

---

## 3. Operator Console Architecture & Layout

The terminal console is a **live operations control plane** (inspired by `k9s`, `lazygit`, and Grafana Dark). 

### Architectural Boundary: Observation, Not Intrusion
```
[ ReActController / ToolExecutor / Memory / Permissions / SubAgentManager / MCP ]
                                      │
                                      ▼
                             LifecycleEventBus
                                      │
        ┌───────────────────┬─────────┴─────────┬───────────────────┐
        ▼                   ▼                   ▼                   ▼
PrometheusObserver   OTelObserver     StructuredLogObserver    TUIObserver
   (:9101)          (Tempo:3200)         (app.log)                  │
                                                                    ▼
                                                            RuntimeStateStore
                                                        (thread-safe, bounded)
                                                                    │
                                                                    ▼
                                                           Textual Operator App
```

- **Strict Telemetry Decoupling:** The TUI subscribes to `LifecycleEventBus`. There are **zero** `print()` calls scattered in core engine classes.
- **Fault Containment:** Any observer error is isolated and can never crash an agent turn.
- **Thread Safety:** Agent execution runs in a background worker thread; Textual widgets update strictly on the UI event loop.

### Screen Navigation & Keybindings

| Key | View / Action | Purpose |
|---|---|---|
| `1` | **Home** | Minimal operator launchpad, evidence-based readiness, curated scenarios |
| `2` | **Runs & Tree** | Hierarchical execution topology tree, live flight recorder, deep inspector |
| `3` | **Agents** | Discover least-privilege specialist profiles, tool allowances, and budgets |
| `4` | **Tools** | Unified tool catalog (built-in + MCP), mutation classes, contextual policies, schemas |
| `5` | **MCP** | External MCP server status, live circuit breaker FSM state, retry metrics |
| `6` | **Security** | Hard workspace containment vs contextual rules, rule precedence, audit log |
| `7` | **Events** | Searchable & filterable in-memory raw lifecycle event stream |
| `8` | **Overview** | Comprehensive system telemetry, memory statistics, circuit breaker FSM |
| `9` | **Help** | In-console cheatsheet, telemetry links, and verification hints |
| `Enter` | **Run / Inspect** | Run selected scenario (on Home) or open detailed inspector (on Runs) |
| `C` | **Custom Prompt** | Open modal dialog to input custom agent instructions |
| `A` | **Advanced / Runs** | Jump directly to Runs & Tree screen |
| `G` | **Grafana Link** | Display direct link to the Grafana operations dashboard |
| `T` | **Tempo Link** | Display Tempo trace explorer link with the active `trace_id` |
| `E` | **Explain Run** | Reconstruct architectural state explanation from deterministic events |
| `Q` | **Quit** | Cleanly exit the operator console |

---

## 4. Recommended 15-Minute Operational Walkthrough

Follow these sequential steps to independently exercise every dimension of the harness:

### Step 1: System Readiness & Discovery (Screens `1`, `8`, `3`, `4`, `5`)
1. Launch the console (`make showcase`) and land directly on Screen `1` (**Home**):
   - Notice the **evidence-sensitive status badges** (`LLM`, `MCP`, `OBSERVABILITY`, `WORKSPACE`).
   - The status is derived truthfully from live probes; badges never display static green without evidence.
2. Press `8` (**Overview**):
   - Notice that capability counts are **derived dynamically from runtime configuration**, not hardcoded.
   - Observe live health metrics for Prometheus, Tempo, Metrics, and Transport MCP.
3. Press `3` (**Agents**):
   - Discover the 3 configured agent profiles:
     - `orchestrator`: Root agent with full tool catalog and memory access.
     - `workspace_analyst`: Read-only specialist sub-agent (allowed: `read_file`, `list_directory`, `search_files`).
     - `transport_specialist`: External capability specialist (allowed: `find_connection`, `get_station_info`).
4. Press `4` (**Tools**):
   - Review the unified catalog combining 6 built-in filesystem tools, delegation, and discovered MCP tools.
   - Note that policy is marked **Context-aware**, reflecting that authorization is evaluated dynamically based on resource patterns and risk classification.
5. Press `5` (**MCP**):
   - Inspect `transport_service` running over streamable HTTP.
   - Verify that the Circuit Breaker FSM starts in `CLOSED` with 0 failures.

---

### Step 2: Execute Curated Scenario — End-to-End Agent Runtime Journey (Screen `1`)
1. Press `1` (**Home**).
2. Select **End-to-End Agent Runtime Journey** and press **`Enter`** (or click `▶ Run Selected Scenario`).
3. The console automatically switches to Screen `2` (**Runs & Tree**) to stream the live flight recorder:
   - `[W2][MEMORY]`: A user preference (`"prefer departures after 09:00"`) is admitted into persistent memory and subsequently retrieved by the ReAct loop.
   - `[W3][DELEGATE]`: Root orchestrator delegates transport query to `transport_specialist`.
   - `[W2][MCP]`: Specialist invokes `transport_service:find_connection` over HTTP and returns connections after 09:00.
   - `[W3][POLICY]`: Orchestrator attempts `create_file("travel_plan.md")`. The contextual policy engine flags this as `MUTATING`, triggering `REQUIRE_CONFIRMATION`.
   - **Interactive Modal Dialog:** A security confirmation modal pops up in the terminal showing the tool, risk level (`MUTATING`), target resource (`travel_plan.md`), and argument fingerprint.
   - **Press `y` (or click `Approve`)** to authorize the action.
   - `[W3][CONFIRM]`: Confirmation resolved as `APPROVED` with single-use token binding.
   - `[W1][TOOL]`: `create_file` executes within the workspace containment boundary.
   - `[W1][TOOL]`: `read_file` verifies the written travel plan.
   - `[W1][RUN]`: Orchestrator synthesizes the final answer.
4. **Post-Run Summary Modal:**
   - As soon as the run completes, the Post-Run Summary Modal automatically appears, displaying 6 concise, truthful evidence rows:
     - **Memory Lifecycle:** `HIT (1 entry retrieved)`
     - **Specialist Delegation:** `delegated to transport_specialist`
     - **MCP Tool Invocation:** `find_connection (transport_service)`
     - **Contextual Governance:** `create_file → APPROVED`
     - **Workspace Containment:** `travel_plan.md created inside workspace`
     - **Distributed Tracing:** `recorded (<trace_id>)`
   - Press **`E`** from the modal to open the detailed architectural explanation, **`T`** to view trace info, **`S`** to jump to the security audit ledger, or **`Esc`** to close.

---

### Step 3: Verify in Grafana & Tempo
1. Open the **Grafana Dashboard** in your browser:
   [http://localhost:3000/d/agent-harness-runtime/agent-harness-operations](http://localhost:3000/d/agent-harness-runtime/agent-harness-operations)
   - **System Health:** Scrape Health shows `UP`, Runs incremented, Success rate updated.
   - **LLM Telemetry:** Successful inferences, tokens consumed, latency percentiles.
   - **Multi-Agent Execution:** Delegation volume shows `transport_specialist` execution; delegation duration recorded.
   - **Security & Governance:** `REQUIRE_CONFIRMATION` evaluated; Human Confirmation Outcomes shows `approved`.
   - **Memory Subsystem:** Shows `admit` and `retrieve` operations.
2. In the TUI, press `T` to see the exact active **Trace ID** (e.g. `4f82...`).
3. Open **Tempo Trace Explorer** in Grafana:
   [http://localhost:3000/explore](http://localhost:3000/explore) (Datasource: `tempo`)
   - Paste the Trace ID into the query field.
   - View the complete distributed trace waterfall: Root Orchestrator span $\to$ SubAgent Delegation span $\to$ Transport MCP HTTP span $\to$ Tool Executor span.

---

### Step 4: Verify Feature A — Contextual Permissions & Containment (Screen `6`)
1. Press `6` (**Security & Governance**).
2. Examine the two distinct security layers:
   - **Layer 1: Hard Workspace Barrier (`Workspace.resolve()`):**
     Directory escape (e.g. `../../etc/passwd`) is **unconditionally blocked** at the filesystem barrier. No policy rule can authorize an escape.
   - **Layer 2: Contextual Policy Engine:**
     Rules match against resource patterns (`*.env*`), risk levels (`SENSITIVE`, `MUTATING`, `READ_ONLY`), and argument patterns.
3. In the TUI, press `1` (**Home**), press `C` to open the custom prompt dialog, and test path traversal rejection or file creation.
4. Inspect the decision audit log on Screen `6`.

---

### Step 5: Verify Feature B — MCP Resilience & Circuit Breakers (Screen `5`)
1. Select the resilience scenario or inspect `transport_service`:
   - Runs an isolated, deterministic demonstration against an invoker fixture.
   - **Test 1 (Transient Retries):** Injects transient 503 errors on an idempotent read $\to$ invoker executes bounded retries with deterministic backoff $\to$ emits `MCPRetryEvent` $\to$ succeeds on recovery.
   - **Test 2 (Circuit Tripping):** Injects sustained logical failures $\to$ failure counter increments $\to$ trips circuit from `CLOSED` to `OPEN` $\to$ emits `MCPCircuitStateChangedEvent`.
   - **Test 3 (Fast Fail & Half-Open Recovery):** Subsequent calls fail fast without network load $\to$ monotonic clock advances beyond cooldown $\to$ circuit transitions to `HALF_OPEN` $\to$ single probe request succeeds $\to$ resets circuit to `CLOSED`.
2. Press `5` (**MCP**) to inspect the live circuit state and retry metrics.

---

### Step 6: Verify Human-Readable Explanation (Hotkey `E`)
1. On Screen `2` (**Runs & Tree**), press **`E`**.
2. An architectural explanation dialog appears reconstructing:
   - Why delegation occurred (capabilities assigned to specialist profile).
   - How permissions were evaluated (risk classification and rule priority).
   - How MCP tools were adapted into the unified `ToolSpec` abstraction.
   - How workspace containment remained intact.
   - *Explicit notice:* Derived purely from deterministic events and config; never exposes hidden model chain-of-thought.

---

## 5. Copy-Paste Interactive Scenarios (For Custom Testing)

Operators can press **`C`** on any screen in the TUI to open the Custom Agent Prompt modal. 
The modal allows selecting the session mode:
- **`New evaluation task`** (default): Resets short-term working context for isolated runtime probes while keeping persistent memory (SQLite/embeddings), tools, and governance intact.
- **`Continue current conversation`**: Retains prior conversation turns for multi-turn conversational testing.

You can copy-paste any of these self-contained scenarios:

### Scenario 1: ReAct Loop & Workspace Containment
```text
Write a concise project status report to 'status_report.md' detailing the current directory contents, then read 'status_report.md' to verify its contents.
```
- **Expected Observables:**
  - `list_directory` executes $\to$ `create_file("status_report.md")` triggers `MUTATING` confirmation $\to$ operator approves (`y`) $\to$ `read_file` verifies content.
  - Summary Modal: Workspace containment confirms `status_report.md created inside workspace`.

### Scenario 2: Persistent Memory & MCP Tool Integration (2-Step Test)
**Step 1 (Admit User Preference to Persistent Memory):**
```text
Remember that my preferred departure station is Passau Hbf and I strictly travel by regional train.
```
- **Expected Observable:** Memory manager admits and stores preference into SQLite store (`[W2][MEMORY] entry_count=1`).

**Step 2 (Retrieve Preference & Query MCP Capability):**
```text
What was my preferred departure station that I asked you to remember, and what are the morning connections from there to Munich tomorrow?
```
- **Expected Observables:**
  - Memory manager retrieves the stored preference (`[W2][MEMORY] HIT`).
  - Specialist/Agent queries external `transport_service:find_connection` via streamable HTTP MCP.
  - Final response applies the retrieved preference to the MCP query results.

### Scenario 3: Governed Multi-Agent Delegation
```text
Look up morning train connections from Berlin to Hamburg using our transport specialist sub-agent, and write the itinerary to 'travel_itinerary.md'.
```
- **Expected Observables:**
  - Root orchestrator delegates sub-task to `transport_specialist`.
  - Specialist executes `find_connection` over MCP.
  - Orchestrator attempts `create_file("travel_itinerary.md")`, triggering contextual policy check and interactive security confirmation.
  - Single-use authorization token is bound to argument fingerprint upon operator approval (`y`).

### Scenario 4: End-to-End Agent Runtime Journey
*(Also selectable with 1 click on Home screen)*
```text
Check my stored travel preferences from memory. Delegate to the transport specialist to find tomorrow's connections matching those preferences, and write the finalized travel plan to 'travel_plan.md'. Finally, verify the written file.
```
- **Expected Observables:**
  - Memory: `HIT (1 entry retrieved)` (pre-admitted preference `"departures after 09:00"`).
  - Delegation: `delegated to transport_specialist`.
  - MCP: `find_connection (transport_service)`.
  - Governance: `create_file → APPROVED`.
  - Containment: `travel_plan.md created inside workspace`.
  - Post-Run Summary Modal appears automatically with all 6 evidence dimensions.

---

## 6. Running the Automated Test Suite

To run the complete automated regression suite inside the Docker environment:

```bash
make test
```

Expected baseline:
- **618 tests collected**
- **617 passed** (0 failures, 0 errors)
- **1 expected skip** (`test_live_network_smoke_test`), because the live external network transport test is disabled by default unless `RUN_LIVE_TRANSPORT_TESTS=1`.

To run the fast unit test suite:
```bash
make test-unit
```

---

## 7. Teardown

To shut down all containers cleanly:

```bash
make down
```

