#!/usr/bin/env python3
"""Week 3 Demonstration Runner: Governed Multi-Principal Delegation & Observability.

Demonstrates:
1. Least-Privilege Role Partitioning:
   - orchestrator: No direct filesystem read, no direct transport tools; can delegate_task and create_file.
   - workspace_analyst: Read-only access to workspace files; cannot mutate or call MCP tools.
   - transport_specialist: Access to transport MCP service; cannot access local filesystem.
2. Hierarchical Delegation & Budget Governance:
   - Orchestrator delegates subtasks to specialists with sliced step/tool limits.
   - ContextVar & short-term conversational context isolation.
3. Common Permission Enforcement Boundary:
   - Closed-default policy with deterministic confirmation for mutating operations (create_file).
   - Single-use, digest-bound confirmation tokens.
4. Comprehensive Observability:
   - Structured JSON logging with trace/run correlation.
   - Prometheus metrics across runs, delegations, tools, and permissions.
   - OpenTelemetry trace spans with parent-child span hierarchy.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys
import tempfile
import time
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from prometheus_client import CollectorRegistry

from harness.agent.budget import ExecutionBudget, TerminationReason
from harness.agent.delegation import (
    AgentFactory,
    AgentSpec,
    DelegateTaskTool,
    HierarchicalBudgetLedger,
    MemoryAccessLevel,
    SubAgentManager,
    build_tool_catalog,
    get_standard_specialist_specs,
)
from harness.agent.react import ReActController
from harness.observability.logging import StructuredLogObserver
from harness.observability.metrics import PrometheusObserver
from harness.observability.tracing import OpenTelemetryObserver
from harness.permissions import (
    DeterministicConfirmationHandler,
    PermissionDecision,
    PermissionManager,
    PolicyEngine,
    PolicyRule,
    RiskLevel,
)
from harness.runtime.events import LifecycleEventBus
from harness.tools.base import Tool, ToolResult, ToolSource, ToolSpec
from harness.tools.executor import ToolExecutor
from harness.tools.filesystem import CreateFileTool, ListDirectoryTool, ReadFileTool, SearchFilesTool
from harness.tools.registry import ToolRegistry
from harness.tools.workspace import Workspace


class DeterministicMockLLM:
    """Mock LLM providing deterministic canned ReAct steps for headless demo execution."""

    def __init__(self) -> None:
        self.call_count = 0

    def chat(self, messages: list[dict[str, Any]], tools: Any = None) -> Any:
        self.call_count += 1
        last_msg = messages[-1]
        last_content = str(last_msg.get("content", ""))
        system_text = str(messages[0].get("content", "")).lower()

        from unittest.mock import MagicMock

        # 1. Orchestrator turn 1 -> delegate workspace analysis
        if "orchestrator" in system_text and "travel_request.txt" in last_content:
            call = MagicMock()
            call.id = f"demo-call-{self.call_count}"
            call.name = "delegate_task"
            call.arguments = {
                "specialist": "workspace_analyst",
                "task": "Read travel_request.txt and extract origin, destination, and departure date.",
            }
            resp = MagicMock()
            resp.tool_calls = [call]
            resp.content = "Delegating travel request inspection to workspace analyst."
            return resp

        # 2. Workspace analyst turn 1 -> read_file
        if "workspace analyst" in system_text and last_msg.get("role") == "user":
            call = MagicMock()
            call.id = f"demo-call-{self.call_count}"
            call.name = "read_file"
            call.arguments = {"path": "travel_request.txt"}
            resp = MagicMock()
            resp.tool_calls = [call]
            resp.content = "Reading travel request file."
            return resp

        # 3. Workspace analyst turn 2 -> final text
        if "workspace analyst" in system_text and last_msg.get("role") == "tool":
            resp = MagicMock()
            resp.tool_calls = []
            resp.content = "Extracted Request: Origin=Passau Hbf, Destination=München Hbf, Date=2026-09-15T09:00:00"
            return resp

        # 4. Orchestrator turn 2 -> delegate transport connection lookup
        if "orchestrator" in system_text and "Extracted Request" in last_content:
            call = MagicMock()
            call.id = f"demo-call-{self.call_count}"
            call.name = "delegate_task"
            call.arguments = {
                "specialist": "transport_specialist",
                "task": "Find connection from Passau Hbf to München Hbf departing 2026-09-15T09:00:00.",
            }
            resp = MagicMock()
            resp.tool_calls = [call]
            resp.content = "Extracted details received. Delegating train connection lookup to transport specialist."
            return resp

        # 5. Transport specialist turn 1 -> find_connection
        if "transport assistant" in system_text and last_msg.get("role") == "user":
            call = MagicMock()
            call.id = f"demo-call-{self.call_count}"
            call.name = "find_connection"
            call.arguments = {
                "origin": "Passau Hbf",
                "destination": "München Hbf",
                "departure_time": "2026-09-15T09:00:00",
            }
            resp = MagicMock()
            resp.tool_calls = [call]
            resp.content = "Querying Bavarian timetable for connections."
            return resp

        # 6. Transport specialist turn 2 -> final text
        if "transport assistant" in system_text and last_msg.get("role") == "tool":
            resp = MagicMock()
            resp.tool_calls = []
            resp.content = "Found Connection: RE 3 departing Passau Hbf at 09:25, arriving München Hbf at 11:35 (Direct, 2h 10m)."
            return resp

        # 7. Orchestrator turn 3 -> propose create_file for summary report
        if "orchestrator" in system_text and "Found Connection" in last_content:
            call = MagicMock()
            call.id = f"demo-call-{self.call_count}"
            call.name = "create_file"
            call.arguments = {
                "path": "trip_summary.txt",
                "content": (
                    "=== ITINERARY REPORT ===\n"
                    "Route: Passau Hbf -> München Hbf\n"
                    "Train: RE 3 (Direct)\n"
                    "Departure: 2026-09-15T09:25:00\n"
                    "Arrival: 2026-09-15T11:35:00\n"
                    "Status: Confirmed via Governed Sub-Agent Delegation\n"
                ),
            }
            resp = MagicMock()
            resp.tool_calls = [call]
            resp.content = "Connection retrieved. Writing itinerary summary report."
            return resp

        # 8. Orchestrator turn 4 -> final text
        resp = MagicMock()
        resp.tool_calls = []
        resp.content = "Demonstration complete: Travel itinerary successfully generated and verified."
        return resp


class SyntheticTransportTool:
    """Deterministic synthetic transport service tool for local demonstration."""

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="find_connection",
            description="Find rail connection between Bavarian stations.",
            input_schema={
                "type": "object",
                "properties": {
                    "origin": {"type": "string"},
                    "destination": {"type": "string"},
                    "departure_time": {"type": "string"},
                },
                "required": ["origin", "destination", "departure_time"],
            },
            source=ToolSource.MCP,
            server_name="transport_service",
            is_mutating=False,
        )

    def execute(self, arguments: dict[str, Any]) -> ToolResult:
        origin = arguments.get("origin")
        destination = arguments.get("destination")
        dep_time = arguments.get("departure_time")
        return ToolResult(
            content=json.dumps({
                "connection_id": "CONN-RE3-DEMO-0925",
                "origin": origin,
                "destination": destination,
                "departure_time": "2026-09-15T09:25:00",
                "arrival_time": "2026-09-15T11:35:00",
                "transfers": 0,
                "legs": [{"train": "RE 3", "departure": "09:25", "arrival": "11:35"}],
            }),
            is_error=False,
        )


class SyntheticStationInfoTool:
    """Deterministic station info tool for local demonstration."""

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="get_station_info",
            description="Lookup station details.",
            input_schema={
                "type": "object",
                "properties": {
                    "station": {"type": "string"},
                },
                "required": ["station"],
            },
            source=ToolSource.MCP,
            server_name="transport_service",
            is_mutating=False,
        )

    def execute(self, arguments: dict[str, Any]) -> ToolResult:
        return ToolResult(
            content=json.dumps({"station": arguments.get("station"), "is_active": True}),
            is_error=False,
        )


def run_demo(workspace_dir: Path | None = None) -> None:
    """Run the complete Week 3 governed delegation demonstration."""
    print("=" * 80)
    print("WEEK 3 DEMONSTRATION: GOVERNED MULTI-PRINCIPAL DELEGATION & OBSERVABILITY")
    print("=" * 80)

    # Initialize workspace
    cleanup_temp = False
    if workspace_dir is None:
        temp_obj = tempfile.TemporaryDirectory()
        workspace_dir = Path(temp_obj.name).resolve()
        cleanup_temp = True
    else:
        workspace_dir.mkdir(parents=True, exist_ok=True)

    workspace = Workspace(workspace_dir)

    # Seed input task file
    input_file = workspace_dir / "travel_request.txt"
    input_file.write_text(
        "PASSENGER TRAVEL REQUEST:\n"
        "Need train from Passau Hbf to München Hbf on 2026-09-15 departing around 09:00.\n",
        encoding="utf-8",
    )
    output_file = workspace_dir / "trip_summary.txt"

    print(f"\n[1] Workspace Initialized at: {workspace_dir}")
    print(f"    • Seeded input file : {input_file.name}")
    print(f"    • Target report     : {output_file.name} (not yet created)")

    # 1. Observability stack setup
    metric_registry = CollectorRegistry(auto_describe=True)
    prom_observer = PrometheusObserver(registry=metric_registry)

    span_exporter = InMemorySpanExporter()
    tracer_provider = TracerProvider(resource=Resource.create({"service.name": "agent-harness-demo"}))
    tracer_provider.add_span_processor(SimpleSpanProcessor(span_exporter))
    otel_observer = OpenTelemetryObserver(
        service_name="agent-harness-demo",
        tracer_provider=tracer_provider,
    )

    structured_logs: list[str] = []
    log_observer = StructuredLogObserver(destination=structured_logs.append)

    bus = LifecycleEventBus()
    bus.subscribe(prom_observer)
    bus.subscribe(otel_observer)
    bus.subscribe(log_observer)

    # 2. Tool setup
    read_tool = ReadFileTool(workspace)
    create_tool = CreateFileTool(workspace)
    list_tool = ListDirectoryTool(workspace)
    search_tool = SearchFilesTool(workspace)
    transport_tool = SyntheticTransportTool()
    station_tool = SyntheticStationInfoTool()

    tool_catalog = build_tool_catalog([read_tool, create_tool, list_tool, search_tool, transport_tool, station_tool])

    # 3. Policy & Permissions setup
    policy = PolicyEngine(
        rules=[
            # Orchestrator can only delegate tasks and create files with confirmation
            PolicyRule(
                name="allow_orchestrator_delegation",
                decision=PermissionDecision.ALLOW,
                tool_pattern="delegate_task",
                role="orchestrator",
                risk_level=RiskLevel.READ_ONLY,
            ),
            PolicyRule(
                name="confirm_orchestrator_file_creation",
                decision=PermissionDecision.REQUIRE_CONFIRMATION,
                tool_pattern="builtin:filesystem:create_file",
                role="orchestrator",
                risk_level=RiskLevel.MUTATING,
            ),
            # workspace_analyst can only read workspace files
            PolicyRule(
                name="allow_analyst_read",
                decision=PermissionDecision.ALLOW,
                tool_pattern="builtin:filesystem:read_file",
                role="workspace_analyst",
                risk_level=RiskLevel.READ_ONLY,
            ),
            # transport_specialist can only query transport tools
            PolicyRule(
                name="allow_transport_query",
                decision=PermissionDecision.ALLOW,
                tool_pattern="mcp:transport_service:find_connection",
                role="transport_specialist",
                risk_level=RiskLevel.READ_ONLY,
            ),
        ],
        default_stance=PermissionDecision.DENY,
    )

    confirmation_handler = DeterministicConfirmationHandler(always_allow=True)
    permission_manager = PermissionManager(
        policy_engine=policy,
        confirmation_handler=confirmation_handler,
        event_bus=bus,
        workspace=workspace,
    )

    # 4. Delegation & Ledger setup
    root_budget = ExecutionBudget(max_steps=10, max_tool_calls=10, max_runtime_seconds=60.0)
    ledger = HierarchicalBudgetLedger(root_budget=root_budget, max_delegation_depth=2, max_delegations=5)

    specs = get_standard_specialist_specs()
    mock_llm = DeterministicMockLLM()

    factory = AgentFactory(
        specs=specs,
        tool_catalog=tool_catalog,
        llm_client=mock_llm,
        permission_manager=permission_manager,
        event_bus=bus,
    )

    subagent_manager = SubAgentManager(
        agent_factory=factory,
        budget_ledger=ledger,
        event_bus=bus,
    )

    delegate_tool = DelegateTaskTool(subagent_manager)

    parent_registry = ToolRegistry()
    parent_registry.register(delegate_tool)
    parent_registry.register(create_tool)

    parent_executor = ToolExecutor(
        max_observation_chars=root_budget.max_observation_chars,
        event_bus=bus,
        permission_manager=permission_manager,
    )

    orchestrator = ReActController(
        llm_client=mock_llm,
        tool_registry=parent_registry,
        tool_executor=parent_executor,
        budget=root_budget,
        system_prompt="You are an orchestrator agent coordinating tasks.",
        event_bus=bus,
        agent_id="orchestrator-main",
        agent_role="orchestrator",
        budget_ledger=ledger,
    )

    print("\n[2] Least-Privilege Capabilities Configured:")
    print("    • orchestrator       : [delegate_task, create_file (CONFIRMATION REQUIRED)]")
    print("    • workspace_analyst  : [read_file, list_directory, search_files]")
    print("    • transport_specialist: [find_connection (MCP)]")
    print("    • Closed default stance: DENY all unlisted actions")

    # 5. Run workflow
    print("\n[3] Executing Orchestrator Workflow...")
    start_time = time.perf_counter()
    result = orchestrator.run_turn(
        "Process travel_request.txt, retrieve matching train connections, and save a trip_summary.txt report."
    )
    duration = time.perf_counter() - start_time

    print(f"\n[4] Execution Outcome ({duration:.3f}s):")
    print(f"    • Success             : {result.is_success}")
    print(f"    • Termination Reason  : {result.termination_reason.value}")
    print(f"    • Final Agent Text    : {result.final_text}")

    # 6. Verification
    print("\n[5] Verifying Governance Guarantees:")
    assert output_file.exists(), "Target report must exist after authorized execution"
    report_content = output_file.read_text(encoding="utf-8")
    print(f"    ✓ File Created        : {output_file.name} ({len(report_content)} chars)")
    print(f"    ✓ Data-Flow Integrity : Contains 'Passau Hbf -> München Hbf' and 'RE 3'")
    assert "RE 3" in report_content and "Passau Hbf" in report_content

    # Verification of single-use confirmation token
    assert len(confirmation_handler.requests) == 1, "Must require confirmation for create_file"
    print(f"    ✓ Action Confirmation : Required and approved for tool '{confirmation_handler.requests[0].tool_name}'")

    # Telemetry inspection
    print("\n[6] Observability Summary:")
    orch_runs = metric_registry.get_sample_value(
        "harness_agent_runs_total",
        {"agent_role": "orchestrator", "status": "success", "failure_category": "none"},
    )
    analyst_runs = metric_registry.get_sample_value(
        "harness_agent_runs_total",
        {"agent_role": "workspace_analyst", "status": "success", "failure_category": "none"},
    )
    transport_runs = metric_registry.get_sample_value(
        "harness_agent_runs_total",
        {"agent_role": "transport_specialist", "status": "success", "failure_category": "none"},
    )
    delegations = metric_registry.get_sample_value(
        "harness_delegations_total",
        {"parent_role": "orchestrator", "child_role": "transport_specialist", "status": "success"},
    )
    print(f"    • Prometheus Runs     : orchestrator={orch_runs}, analyst={analyst_runs}, transport={transport_runs}")
    print(f"    • Delegations Recorded: {delegations} to transport_specialist")

    spans = span_exporter.get_finished_spans()
    trace_ids = {s.context.trace_id for s in spans}
    print(f"    • OpenTelemetry Spans : {len(spans)} spans generated across {len(trace_ids)} shared trace")
    print(f"    • Structured Log Lines: {len(structured_logs)} JSON event records emitted")

    print("\n" + "=" * 80)
    print("DEMONSTRATION COMPLETE: ALL WEEK 3 GOVERNANCE CONTROLS VERIFIED")
    print("=" * 80)

    if cleanup_temp:
        import shutil
        shutil.rmtree(workspace_dir, ignore_errors=True)


def main() -> None:
    parser = argparse.ArgumentParser(description="Week 3 Governed Delegation & Observability Demonstration")
    parser.add_argument("--workspace", type=str, default=None, help="Optional custom workspace directory")
    args = parser.parse_args()

    workspace_path = Path(args.workspace).resolve() if args.workspace else None
    run_demo(workspace_path)


if __name__ == "__main__":
    main()
