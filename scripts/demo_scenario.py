"""Automated, deterministic runner for Week 2 Demo Scenario:
Cross-Session Bavarian Trip Planner with Dual MCP Servers (E2E-W2-01).

Demonstrates:
- Session A: Persistent preference ingestion into SQLite long-term memory.
- Process Termination: Database connection closed, memory isolated on disk.
- Session B: Fresh controller startup, retrieval into [RECALLED MEMORY],
  dual MCP stdio server execution:
    1. Custom domain MCP server (transport_service / find_connection):
       Retrieves synthetic Bavarian timetable options grounded on recalled stations.
    2. Official external MCP server (@modelcontextprotocol/server-filesystem):
       Collision-safe namespaced tools (mcpfs_write_file -> mcpfs_read_text_file)
       restricted to dedicated sandboxed workspace/mcp_external_demo directory.
  True data-flow propagation:
    MCP transport observation dynamically parsed and formatted into trip_plan.md.
  Filesystem side-effects:
    Persisted and read back via official external MCP filesystem server.
"""

from dataclasses import replace
import json
import os
from pathlib import Path
import shutil
import sys
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from harness.agent.budget import ExecutionBudget
from harness.agent.react import ReActController
from harness.cli import print_mcp
from harness.config import AppConfig, MCPServerConfig, load_config
from harness.llm.client import LLMResponse, ToolCall
from harness.mcp.adapter import MCPToolAdapter
from harness.mcp.client import MCPClient
from harness.memory import (
    MemoryFirewall,
    MemoryManager,
    MemoryRetriever,
    SQLiteMemoryStore,
)
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


def resolve_mcpfs_command() -> tuple[str, list[str]]:
    """Resolve the executable command and initial args for the official MCP filesystem server."""
    bin_path = shutil.which("mcp-server-filesystem")
    if bin_path:
        return bin_path, []
    standard_paths = [
        Path("/usr/local/bin/mcp-server-filesystem"),
        Path("/usr/bin/mcp-server-filesystem"),
    ]
    for p in standard_paths:
        if p.exists() and os.access(p, os.X_OK):
            return str(p), []
    # Fallback to npx if binary is not globally linked
    npx_path = shutil.which("npx")
    if npx_path:
        return npx_path, ["-y", "@modelcontextprotocol/server-filesystem@2026.8.31"]
    return "mcp-server-filesystem", []


class DeterministicDemoLLM:
    """Deterministic LLM test double simulating agent reasoning for E2E-W2-01."""

    def __init__(self, demo_file_path: Path) -> None:
        self.demo_file_path = demo_file_path
        self.turns = 0

    def chat(self, messages: list[dict[str, Any]], tools: list[Any] | None = None) -> LLMResponse:
        self.turns += 1
        system_text = messages[0]["content"]
        last_msg = messages[-1]

        # Session A: Ingestion of travel preference
        if "preferred departure station is Passau Hbf" in last_msg.get("content", ""):
            return LLMResponse(
                content="Noted: Your preferred departure station is Passau Hbf and your destination is Deggendorf.",
                tool_calls=[],
            )

        # Session B: Step 1 - Query custom domain MCP server using retrieved preferences
        if "Find a train connection between my preferred stations" in last_msg.get("content", ""):
            assert "[RECALLED MEMORY]" in system_text, "Memory must be present in system prompt."
            assert "Passau Hbf" in system_text, "Passau Hbf must be present in retrieved memory."
            assert "Deggendorf" in system_text, "Deggendorf must be present in retrieved memory."

            return LLMResponse(
                content=None,
                tool_calls=[
                    ToolCall(
                        id="call_find",
                        name="find_connection",
                        arguments={
                            "origin": "Passau Hbf",
                            "destination": "Deggendorf",
                            "departure_time": "2026-09-10T10:00:00",
                        },
                    )
                ],
            )

        # Session B: Step 2 - Dynamically parse MCP observation and write via official MCP filesystem server
        if last_msg.get("role") == "tool" and last_msg.get("tool_call_id") == "call_find":
            mcp_raw_obs = last_msg.get("content", "")
            data = json.loads(mcp_raw_obs)
            connections = data.get("connections", [])
            assert connections, "Expected connections in find_connection MCP observation."

            best_conn = connections[0]
            conn_id = best_conn.get("connection_id")
            data_source = data.get("data_source")
            dep_time = best_conn.get("departure_time")
            arr_time = best_conn.get("arrival_time")
            duration = best_conn.get("duration_minutes")
            transfers = best_conn.get("transfers")

            legs_summary = " -> ".join(
                f"{leg['line']} from {leg['from_station']} (Pl. {leg['platform']}) to {leg['to_station']}"
                for leg in best_conn.get("legs", [])
            )

            # REAL DATA FLOW: Content derived strictly from real MCP observation
            plan_content = (
                f"# Trip Plan: Passau Hbf -> Deggendorf\n\n"
                f"- **Connection ID**: {conn_id}\n"
                f"- **Data Source**: {data_source}\n"
                f"- **Departure**: {dep_time}\n"
                f"- **Arrival**: {arr_time}\n"
                f"- **Duration**: {duration} minutes\n"
                f"- **Transfers**: {transfers}\n"
                f"- **Route**: {legs_summary}\n"
            )

            return LLMResponse(
                content=None,
                tool_calls=[
                    ToolCall(
                        id="call_mcpfs_write",
                        name="mcpfs_write_file",
                        arguments={
                            "path": str(self.demo_file_path.resolve()),
                            "content": plan_content,
                        },
                    )
                ],
            )

        # Session B: Step 3 - Read back file using official MCP filesystem server
        if last_msg.get("role") == "tool" and last_msg.get("tool_call_id") == "call_mcpfs_write":
            return LLMResponse(
                content=None,
                tool_calls=[
                    ToolCall(
                        id="call_mcpfs_read",
                        name="mcpfs_read_text_file",
                        arguments={"path": str(self.demo_file_path.resolve())},
                    )
                ],
            )

        # Session B: Step 4 - Final synthesis and confirmation
        if last_msg.get("role") == "tool" and last_msg.get("tool_call_id") == "call_mcpfs_read":
            file_content = last_msg.get("content", "")
            return LLMResponse(
                content=(
                    f"Trip successfully planned from Passau Hbf to Deggendorf.\n"
                    f"Route options were retrieved via custom transport MCP service, "
                    f"and the itinerary was persisted and verified via the official external MCP filesystem server.\n\n"
                    f"Verified File Contents:\n{file_content}"
                ),
                tool_calls=[],
            )

        return LLMResponse(content="Unexpected step.", tool_calls=[])


def build_scenario_controller(
    db_path: Path,
    workspace: Workspace,
    mcpfs_dir: Path,
    trip_plan_file: Path,
) -> tuple[ReActController, ToolRegistry, SQLiteMemoryStore, AppConfig]:
    registry = ToolRegistry()
    executor = ToolExecutor()

    # 1. Register built-in Week 1 filesystem tools (proves coexistence with zero collisions)
    registry.register(CreateDirectoryTool(workspace))
    registry.register(CreateFileTool(workspace))
    registry.register(ReadFileTool(workspace))
    registry.register(ListDirectoryTool(workspace))
    registry.register(SearchFilesTool(workspace))
    registry.register(ModifyFileTool(workspace))

    # 2. Configure OUR OWN custom domain MCP server (transport_service) over stdio
    base_config = load_config(REPO_ROOT / "config/config.yaml")
    transport_server_cfg = replace(base_config.mcp_servers[0], command=sys.executable)
    transport_client = MCPClient(transport_server_cfg)
    for spec in transport_client.list_tools():
        registry.register(MCPToolAdapter(spec, transport_client))

    # 3. Configure EXISTING official ecosystem MCP server (@modelcontextprotocol/server-filesystem)
    # Sandboxed exclusively to mcpfs_dir
    cmd, base_args = resolve_mcpfs_command()
    mcpfs_server_cfg = MCPServerConfig(
        name="filesystem_server",
        command=cmd,
        args=base_args + [str(mcpfs_dir.resolve())],
        timeout_seconds=30.0,
    )
    mcpfs_client = MCPClient(mcpfs_server_cfg)
    for spec in mcpfs_client.list_tools():
        # Clean collision-safe namespacing: write_file -> mcpfs_write_file, etc.
        registry.register(MCPToolAdapter(spec, mcpfs_client, prefix="mcpfs"))

    # Compose demo AppConfig representing both active MCP servers
    demo_config = replace(
        base_config,
        mcp_servers=[transport_server_cfg, mcpfs_server_cfg],
    )

    # Initialize persistent memory
    store = SQLiteMemoryStore(db_path)
    firewall = MemoryFirewall(store)
    retriever = MemoryRetriever(store)
    mgr = MemoryManager(store, firewall, retriever)

    llm = DeterministicDemoLLM(trip_plan_file)
    budget = ExecutionBudget(max_steps=10, max_tool_calls=10)

    controller = ReActController(
        llm_client=llm,
        tool_registry=registry,
        tool_executor=executor,
        budget=budget,
        memory_manager=mgr,
    )
    return controller, registry, store, demo_config


def main() -> None:
    demo_db = REPO_ROOT / ".agent_memory" / "demo_week2.db"
    demo_workspace = REPO_ROOT / "workspace"
    mcp_external_demo = demo_workspace / "mcp_external_demo"

    demo_db.parent.mkdir(parents=True, exist_ok=True)
    demo_workspace.mkdir(parents=True, exist_ok=True)
    mcp_external_demo.mkdir(parents=True, exist_ok=True)

    # Clean previous run state
    for p in (demo_db, Path(f"{demo_db}-wal"), Path(f"{demo_db}-shm")):
        p.unlink(missing_ok=True)
    trip_plan = mcp_external_demo / "trip_plan.md"
    trip_plan.unlink(missing_ok=True)

    print("=" * 75)
    print("WEEK 2 CANONICAL DEMO SCENARIO: Cross-Session Trip Planner (E2E-W2-01)")
    print("Dual MCP: transport_service (custom) + @modelcontextprotocol/server-filesystem (official)")
    print("=" * 75)

    # --- SESSION A ---
    print("\n[1/2] Executing Session A: Ingest Travel Preferences into Long-Term Memory")
    ws_a = Workspace(demo_workspace)
    controller_a, registry_a, store_a, demo_cfg_a = build_scenario_controller(
        demo_db, ws_a, mcp_external_demo, trip_plan
    )

    print("\n--- Configured MCP Servers Observability (/mcp) ---")
    print_mcp(demo_cfg_a, registry_a)

    print(f"  • Tools Registered   : {len(registry_a.list_specs())} tools")
    print(f"  • Pre-Session Memory : {store_a.count()} records")

    prompt_a = (
        "Please remember that my preferred departure station is Passau Hbf and my "
        "preferred destination is Deggendorf."
    )
    print(f"  • User Prompt        : {prompt_a}")
    resp_a = controller_a.run(prompt_a)
    print(f"  • Agent Response     : {resp_a}")

    store_a.close()
    if controller_a.memory_manager:
        controller_a.memory_manager.close()
    print("  • Session A Status   : Terminated cleanly. SQLite connection closed.")

    # Verify persistence
    check_store = SQLiteMemoryStore(demo_db)
    post_count = check_store.count()
    print(f"  • Post-Session Memory: {post_count} record(s) persisted in {demo_db.name}")
    check_store.close()
    assert post_count >= 1, "Session A must persist at least one memory record."

    # --- SESSION B ---
    print("\n[2/2] Executing Session B: Fresh Controller Recall & Multi-Step Dual-MCP Flow")
    ws_b = Workspace(demo_workspace)
    controller_b, registry_b, store_b, demo_cfg_b = build_scenario_controller(
        demo_db, ws_b, mcp_external_demo, trip_plan
    )
    print("  • Fresh Controller   : Initialized independently.")

    prompt_b = (
        "Find a train connection between my preferred stations tomorrow at 10:00. "
        "Save the best connection as trip_plan.md using the MCP filesystem server, "
        "then read the file back using the MCP filesystem server and confirm the contents."
    )
    print(f"  • User Prompt        : {prompt_b}")
    resp_b = controller_b.run(prompt_b)
    print(f"\n  • Final Response     :\n{resp_b}")

    # Inspect executed tool calls
    tool_calls = []
    for msg in controller_b.context:
        if msg.get("role") == "assistant" and msg.get("tool_calls"):
            for tc in msg["tool_calls"]:
                tool_calls.append((tc["function"]["name"], tc["function"]["arguments"]))

    print(f"\nExecuted Tool Calls ({len(tool_calls)} total):")
    for idx, (name, args) in enumerate(tool_calls, 1):
        print(f"  {idx}. {name}: {args}")

    # Assertions
    print("\nRunning Verification Assertions:")
    assert len(tool_calls) == 3, f"Expected 3 tool calls, got {len(tool_calls)}"
    print("  ✓ Step count: exactly 3 tool calls executed")

    # 1. Custom Domain Transport MCP Server call
    assert tool_calls[0][0] == "find_connection", f"Step 1 must be 'find_connection', got {tool_calls[0][0]}"
    find_args = json.loads(tool_calls[0][1]) if isinstance(tool_calls[0][1], str) else tool_calls[0][1]
    assert find_args.get("origin") == "Passau Hbf"
    assert find_args.get("destination") == "Deggendorf"
    print("  ✓ Step 1: transport_service 'find_connection' called with grounded origin & destination")

    # 2. Write file via official external MCP filesystem server
    assert tool_calls[1][0] == "mcpfs_write_file", f"Step 2 must be 'mcpfs_write_file', got {tool_calls[1][0]}"
    write_args = json.loads(tool_calls[1][1]) if isinstance(tool_calls[1][1], str) else tool_calls[1][1]
    created_content = write_args.get("content", "")

    # DATA-FLOW PROOF: Assert exact data from MCP observation is present in created content
    assert "CONN-RE3-RB35-1025" in created_content, "Must contain connection_id from find_connection"
    assert "synthetic_bavarian_timetable_v1" in created_content, "Must contain data_source from find_connection"
    assert "2026-09-10T10:25:00" in created_content, "Must contain departure_time from find_connection"
    assert "2026-09-10T11:22:00" in created_content, "Must contain arrival_time from find_connection"
    assert "RE 3" in created_content and "RB 35" in created_content, "Must contain route train lines"
    assert "Plattling" in created_content, "Must contain transfer station from find_connection"
    print("  ✓ Step 2: mcpfs_write_file content dynamically incorporates actual MCP observation data")

    # 3. Read back file via official external MCP filesystem server
    assert tool_calls[2][0] == "mcpfs_read_text_file", f"Step 3 must be 'mcpfs_read_text_file', got {tool_calls[2][0]}"
    print("  ✓ Step 3: mcpfs_read_text_file verifies persistence via official filesystem MCP server")

    # Filesystem Verification
    print(f"\nFilesystem Verification:")
    print(f"  • trip_plan.md exists: {trip_plan.exists()}")
    assert trip_plan.exists(), "trip_plan.md must exist in workspace/mcp_external_demo!"
    saved_file_content = trip_plan.read_text(encoding="utf-8")
    assert saved_file_content == created_content, "Persisted file content must match created content exactly."
    assert "CONN-RE3-RB35-1025" in saved_file_content
    print(f"  • File size          : {trip_plan.stat().st_size} bytes")
    print(f"  • Content verified   : Exact byte match with MCP-derived plan")

    # Final response verification
    assert "Passau Hbf" in resp_b and "Deggendorf" in resp_b
    assert "CONN-RE3-RB35-1025" in resp_b
    print("  ✓ Agent final response synthesizes verified itinerary")

    store_b.close()
    if controller_b.memory_manager:
        controller_b.memory_manager.close()

    print("\n" + "=" * 75)
    print("VERIFICATION RESULT: ALL ASSERTIONS PASSED (100% REPRODUCIBLE)")
    print("=" * 75)


if __name__ == "__main__":
    main()
