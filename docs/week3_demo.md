# Week 3 Demonstration Guide: Governed Multi-Principal Delegation & Observability

This guide provides an evaluator with an end-to-end, reproducible demonstration of the **Week 3 multi-agent governance, action authorization, and observability architecture**.

---

## 1. Scenario Overview

### User Task
A travel passenger provides an unstructured travel request in `travel_request.txt`:
> *"Need train from Passau Hbf to München Hbf on 2026-09-15 departing around 09:00."*

The root orchestrator is tasked with:
1. Inspecting the workspace file to extract origin, destination, and departure time.
2. Querying the Bavarian public transport timetable service for matching trains.
3. Generating a formatted, verified itinerary report and persisting it to `trip_summary.txt`.

---

## 2. Why Specialization and Delegation Are Beneficial

In an ungoverned system, an agent has direct, unconstrained access to all tools (filesystem mutation, network MCP endpoints, etc.), leading to severe security risks:
- **Broad Blast Radius**: A prompt injection from an external tool output or untrusted file could trick the agent into overwriting or reading arbitrary workspace files.
- **Context Pollution**: Dumping full timetable responses or raw file contents into the root agent's conversation history consumes token budget and induces hallucinations.

### Least-Privilege Role Partitioning
The Week 3 architecture strictly isolates capabilities across three bounded principals:

| Agent Role | Permitted Tools | Prohibited Capabilities | Justification |
| :--- | :--- | :--- | :--- |
| **`orchestrator`** | `delegate_task`, `create_file` (with confirmation) | No direct `read_file`, no direct `find_connection` | Acts purely as a governance coordinator; cannot directly inspect files or query transport APIs. |
| **`workspace_analyst`** | `read_file`, `list_directory`, `search_files` | No mutating tools, no MCP transport tools | Read-only specialist. Cannot alter filesystem state or access external networks. |
| **`transport_specialist`** | `find_connection` (MCP), `get_station_info` (MCP) | No filesystem access | Domain network specialist. Cannot inspect, read, or modify local workspace files. |

By separating roles:
1. The **orchestrator cannot access external timetable data directly**; it is forced to delegate to `transport_specialist`.
2. The **specialists cannot mutate the filesystem**; only the orchestrator can propose `create_file`.
3. Mutating the filesystem requires **explicit human/policy confirmation** via `PermissionManager`.

---

## 3. Architecture & Execution Flow

```text
USER / CLI
    ↓ "Process travel_request.txt and write trip_summary.txt"
ORCHESTRATOR ReActController (agent_role="orchestrator")
    │
    ├── 1. Dispatches delegate_task(specialist="workspace_analyst", task="...")
    │       ↓
    │   SubAgentManager / AgentFactory
    │       ↓
    │   workspace_analyst (agent_role="workspace_analyst")
    │       ↓ executes read_file("travel_request.txt") [ALLOW]
    │       ↓ returns extracted route: Passau Hbf -> München Hbf
    │
    ├── 2. Dispatches delegate_task(specialist="transport_specialist", task="...")
    │       ↓
    │   SubAgentManager / AgentFactory
    │       ↓
    │   transport_specialist (agent_role="transport_specialist")
    │       ↓ executes find_connection(origin="Passau Hbf", destination="München Hbf") [ALLOW]
    │       ↓ returns timetable result: RE 3 departing 09:25
    │
    └── 3. Proposes create_file(path="trip_summary.txt", content="...")
            ↓
        PermissionManager (PolicyEngine evaluates create_file)
            ↓
        [REQUIRE_CONFIRMATION] (RiskLevel=MUTATING)
            ↓
        DeterministicConfirmationHandler (approves, issues single-use bound token)
            ↓
        ToolExecutor (verifies token binding, consumes token, writes file)
            ↓
        trip_summary.txt persisted to workspace
```

---

## 4. What Permissions Prevent

1. **Closed-Default Denial**: Any tool invocation not explicitly authorized by a `PolicyRule` is denied immediately without entering `tool.execute()`.
2. **Specialist Containment**: If `workspace_analyst` attempts to call `find_connection` or `create_file`, the call is denied by policy.
3. **Anti-TOCTOU & Anti-Replay Tokens**: When `create_file` is confirmed, the confirmation token is bound to `(run_id, call_id, agent_role, canonical_tool_identity, arguments_fingerprint)`. The token is strictly single-use and consumed upon execution.

---

## 5. Observability Inspection

During execution, the harness emits rich, privacy-safe telemetry:

1. **Prometheus Metrics** (`http://localhost:9101/metrics`):
   - `harness_agent_runs_total{agent_role="orchestrator", status="success"}`
   - `harness_agent_runs_total{agent_role="workspace_analyst", status="success"}`
   - `harness_agent_runs_total{agent_role="transport_specialist", status="success"}`
   - `harness_delegations_total{parent_role="orchestrator", child_role="workspace_analyst", status="success"}`
   - `harness_delegations_total{parent_role="orchestrator", child_role="transport_specialist", status="success"}`
   - `harness_permission_decisions_total{decision="require_confirmation", risk_level="mutating"}`
   - `harness_permission_confirmations_total{status="approved"}`
2. **OpenTelemetry Distributed Traces** (Grafana Tempo at `http://localhost:3000`):
   - A single unified `trace_id` spans the entire multi-agent transaction.
   - Trace parenting reflects the exact delegation tree:
     ```text
     agent.run [orchestrator]
       ├── tool.execute [delegate_task]
       │     └── agent.run [workspace_analyst]
       │           └── tool.execute [read_file]
       ├── tool.execute [delegate_task]
       │     └── agent.run [transport_specialist]
       │           └── tool.execute [find_connection]
       └── tool.execute [create_file]
     ```
3. **Structured JSON Logs**:
   - Every lifecycle event emits a structured JSON line containing `trace_id`, `run_id`, `root_run_id`, `parent_run_id`, `agent_role`, and sanitized argument descriptors.

---

## 6. How to Run the Demonstration

### Option A: Direct Local Execution (Deterministic / Headless)
Requires no external network, no live LLM tokens, and finishes in $< 1$ second:

```bash
python scripts/demo_week3.py
```

### Option B: Docker Container Execution
Execute inside the verified Python 3.12 Docker container:

```bash
docker exec -it agent-harness-harness-1 python scripts/demo_week3.py
```

### Option C: Run the Automated E2E Test Suite
The identical scenario is verified by our automated end-to-end regression test:

```bash
docker exec -it agent-harness-harness-1 python -m unittest tests.e2e.test_week3_integrated
```
