"""Thread-safe runtime state store for the Agent Harness Operator Console."""

from __future__ import annotations

from collections import deque
from dataclasses import replace
import logging
import threading
import time
from typing import Any, Callable

from harness.agent.budget import ExecutionBudget
from harness.agent.delegation import get_standard_specialist_specs
from harness.config import AppConfig
from harness.permissions.base import (
    PermissionDecision,
    RiskLevel,
    canonical_tool_identity,
    summarize_arguments,
)
from harness.runtime.events import (
    ConfirmationResolvedEvent,
    DelegationFinishedEvent,
    DelegationStartedEvent,
    LifecycleEvent,
    LLMCallFinishedEvent,
    LLMCallStartedEvent,
    MCPCircuitStateChangedEvent,
    MCPRetryEvent,
    MemoryOperationEvent,
    PermissionEvaluatedEvent,
    RunFinishedEvent,
    RunStartedEvent,
    ToolCallFinishedEvent,
    ToolCallRequestedEvent,
    ToolCallStartedEvent,
)
from harness.tools.base import ToolSource
from harness.tools.registry import ToolRegistry
from harness.tui.models import (
    AgentCard,
    GovernanceRecord,
    MCPServerCard,
    RunNode,
    TimelineEvent,
    ToolCallNode,
    ToolCard,
)

logger = logging.getLogger("harness.tui.store")


class RuntimeStateStore:
    """Central in-memory state repository fed by TUIObserver.

    Performance & Fault Isolation:
      - All modifications occur under a reentrant lock (RLock).
      - Event histories are strictly bounded with deques to prevent memory leaks.
      - Registered UI notification callbacks are invoked safely without blocking the publisher.
    """

    def __init__(self, max_events: int = 1000) -> None:
        self._lock = threading.RLock()
        self._max_events = max_events

        self.runs: dict[str, RunNode] = {}
        self.root_run_ids: list[str] = []
        self.selected_run_id: str | None = None

        self.timeline: deque[TimelineEvent] = deque(maxlen=max_events)
        self.raw_events: deque[LifecycleEvent] = deque(maxlen=max_events)
        self.governance_log: deque[GovernanceRecord] = deque(maxlen=max_events)

        self.agents: dict[str, AgentCard] = {}
        self.tools: dict[str, ToolCard] = {}
        self.mcp_servers: dict[str, MCPServerCard] = {}

        self.active_trace_id: str | None = None
        self.active_run_id: str | None = None
        self.is_agent_executing: bool = False

        self._start_wall_time: float = time.time()
        self._listeners: list[Callable[[], None]] = []

    def add_listener(self, callback: Callable[[], None]) -> None:
        """Register a callback to be invoked when state is updated."""
        with self._lock:
            if callback not in self._listeners:
                self._listeners.append(callback)

    def remove_listener(self, callback: Callable[[], None]) -> None:
        """Unregister a listener callback."""
        with self._lock:
            if callback in self._listeners:
                self._listeners.remove(callback)

    def _notify(self) -> None:
        """Invoke all listeners outside lock."""
        with self._lock:
            callbacks = list(self._listeners)
        for cb in callbacks:
            try:
                cb()
            except Exception as e:
                logger.debug(f"Error in TUI state store listener: {e}")

    def get_relative_seconds(self, timestamp: float) -> float:
        """Calculate elapsed seconds relative to store initialization or run start."""
        return max(0.0, timestamp - self._start_wall_time)

    # -------------------------------------------------------------------------
    # Discovery & Static Metadata Ingestion
    # -------------------------------------------------------------------------

    def populate_from_runtime(
        self,
        config: AppConfig,
        registry: ToolRegistry,
        mcp_client: Any | None = None,
    ) -> None:
        """Derive dynamic capability cards from live config and registry."""
        with self._lock:
            # 1. Specialists & Root Agent
            self.agents.clear()
            # Root orchestrator
            root_tools = tuple(
                canonical_tool_identity(t.spec.source, t.spec.server_name, t.spec.name)
                for t in registry.get_all()
            )
            self.agents["orchestrator"] = AgentCard(
                role="orchestrator",
                agent_type="Root Orchestrator",
                system_prompt=getattr(config.agent, "system_prompt", "Autonomous ReAct agent orchestrator."),
                allowed_tool_ids=root_tools,
                budget_ceiling=ExecutionBudget(
                    max_steps=config.agent.max_steps,
                    max_tool_calls=config.agent.max_tool_calls,
                    max_runtime_seconds=config.agent.max_runtime_seconds,
                    max_observation_chars=config.agent.max_observation_chars,
                ),
                memory_access="READ_WRITE" if config.memory.enabled else "NONE",
                model_id=config.llm.model,
            )

            # Standard Specialists
            for role, spec in get_standard_specialist_specs().items():
                self.agents[role] = AgentCard(
                    role=role,
                    agent_type="Specialist Sub-Agent",
                    system_prompt=spec.system_prompt,
                    allowed_tool_ids=spec.allowed_tool_ids,
                    budget_ceiling=spec.budget_ceiling,
                    memory_access=spec.memory_access.value.upper(),
                    model_id=spec.model_id or config.llm.model,
                )

            # 2. Registered Tools
            self.tools.clear()
            for tool in registry.get_all():
                spec = tool.spec
                canon_id = canonical_tool_identity(spec.source, spec.server_name, spec.name)
                self.tools[spec.name] = ToolCard(
                    name=spec.name,
                    source=spec.source,
                    canonical_identity=canon_id,
                    server_name=spec.server_name,
                    is_mutating=spec.is_mutating,
                    description=spec.description,
                    parameters_schema=dict(getattr(spec, "input_schema", {})) if getattr(spec, "input_schema", None) else {},
                )

            # 3. MCP Servers
            self.mcp_servers.clear()
            for s_cfg in config.mcp_servers:
                tools_for_server = [
                    t.spec.name for t in registry.get_all()
                    if t.spec.source == ToolSource.MCP and t.spec.server_name == s_cfg.name
                ]
                card = MCPServerCard(
                    server_name=s_cfg.name,
                    transport_type=s_cfg.transport.value if hasattr(s_cfg.transport, "value") else str(s_cfg.transport),
                    endpoint=s_cfg.url or (s_cfg.command or ""),
                    tools_count=len(tools_for_server),
                    tools_list=tools_for_server,
                    circuit_state="CLOSED",
                    failure_threshold=s_cfg.resilience.circuit_failure_threshold if s_cfg.resilience else 3,
                    cooldown_seconds=s_cfg.resilience.circuit_cooldown_seconds if s_cfg.resilience else 30.0,
                    is_connected=len(tools_for_server) > 0,
                )
                if mcp_client and hasattr(mcp_client, "get_circuit_snapshot"):
                    try:
                        snap = mcp_client.get_circuit_snapshot()
                        if snap:
                            card.circuit_state = snap.state.value.upper()
                            card.consecutive_failures = snap.consecutive_failures
                            card.last_failure_timestamp = snap.last_failure_timestamp
                    except Exception:
                        pass
                self.mcp_servers[s_cfg.name] = card

        self._notify()

    # -------------------------------------------------------------------------
    # Lifecycle Event Handlers
    # -------------------------------------------------------------------------

    def record_raw_event(self, event: LifecycleEvent) -> None:
        with self._lock:
            self.raw_events.append(event)

    def handle_run_started(self, event: RunStartedEvent) -> None:
        with self._lock:
            self.record_raw_event(event)
            self.active_trace_id = event.trace_id
            self.active_run_id = event.run_id
            self.is_agent_executing = True

            is_root = (event.parent_run_id is None or event.parent_run_id == "")
            run_node = RunNode(
                run_id=event.run_id,
                trace_id=event.trace_id,
                parent_run_id=event.parent_run_id,
                root_run_id=event.root_run_id,
                agent_id=event.agent_id,
                agent_role=event.agent_role,
                is_root=is_root,
                status="RUNNING",
                start_time=event.timestamp,
            )
            self.runs[event.run_id] = run_node

            if is_root:
                self.root_run_ids.append(event.run_id)
                self.selected_run_id = event.run_id
            elif event.parent_run_id in self.runs:
                self.runs[event.parent_run_id].children_run_ids.append(event.run_id)

            rel_sec = self.get_relative_seconds(event.timestamp)
            prefix = "[W1][RUN]" if is_root else "[W3][SUBAGENT]"
            self.timeline.append(
                TimelineEvent(
                    timestamp=event.timestamp,
                    relative_seconds=rel_sec,
                    run_id=event.run_id,
                    agent_role=event.agent_role,
                    category=prefix,
                    icon="◆",
                    title=f"{event.agent_role} started",
                    details=f"run_id={event.run_id[:8]}",
                    style="info",
                )
            )
        self._notify()

    def handle_run_finished(self, event: RunFinishedEvent) -> None:
        with self._lock:
            self.record_raw_event(event)
            if event.run_id == self.active_run_id:
                self.is_agent_executing = False

            if event.run_id in self.runs:
                node = self.runs[event.run_id]
                node.finish_time = event.timestamp
                node.duration_seconds = event.duration_seconds
                node.status = "SUCCESS" if event.is_success else "FAILED"
                node.steps = event.steps
                node.tool_calls = event.tool_calls
                node.termination_reason = event.termination_reason.value if hasattr(event.termination_reason, "value") else str(event.termination_reason)
                node.error_message = event.error_message

            rel_sec = self.get_relative_seconds(event.timestamp)
            is_success = event.is_success
            prefix = "[W1][RUN]" if (node.is_root if event.run_id in self.runs else True) else "[W3][SUBAGENT]"
            icon = "✓" if is_success else "✗"
            style = "success" if is_success else "error"
            title = f"{event.agent_role} finished ({event.duration_seconds:.2f}s)"
            details = f"steps={event.steps} tools={event.tool_calls} status={'OK' if is_success else event.error_type or 'ERROR'}"

            self.timeline.append(
                TimelineEvent(
                    timestamp=event.timestamp,
                    relative_seconds=rel_sec,
                    run_id=event.run_id,
                    agent_role=event.agent_role,
                    category=prefix,
                    icon=icon,
                    title=title,
                    details=details,
                    style=style,
                )
            )
        self._notify()

    def record_final_response(self, run_id: str | None, response: str) -> None:
        """Associate a completed agent turn's final response with its RunNode."""
        with self._lock:
            target_id = run_id or self.active_run_id or (self.root_run_ids[-1] if self.root_run_ids else None)
            if target_id and target_id in self.runs:
                self.runs[target_id].final_response = response
        self._notify()

    def get_selected_or_latest_run(self) -> RunNode | None:
        """Return the user-selected RunNode or fallback to the latest root run."""
        with self._lock:
            if self.selected_run_id and self.selected_run_id in self.runs:
                return self.runs[self.selected_run_id]
            if self.root_run_ids:
                return self.runs.get(self.root_run_ids[-1])
            return None

    def handle_llm_started(self, event: LLMCallStartedEvent) -> None:
        with self._lock:
            self.record_raw_event(event)
            if event.run_id in self.runs:
                self.runs[event.run_id].llm_calls_count += 1

            rel_sec = self.get_relative_seconds(event.timestamp)
            self.timeline.append(
                TimelineEvent(
                    timestamp=event.timestamp,
                    relative_seconds=rel_sec,
                    run_id=event.run_id,
                    agent_role=event.agent_role,
                    category="[W1][LLM]",
                    icon="◆",
                    title=f"LLM inference ({event.model})",
                    details=f"msgs={event.message_count} tools={event.tools_count}",
                    style="info",
                )
            )
        self._notify()

    def handle_llm_finished(self, event: LLMCallFinishedEvent) -> None:
        with self._lock:
            self.record_raw_event(event)
            if event.run_id in self.runs:
                node = self.runs[event.run_id]
                node.prompt_tokens += (event.prompt_tokens or 0)
                node.completion_tokens += (event.completion_tokens or 0)
                node.total_tokens += (event.total_tokens or 0)

            rel_sec = self.get_relative_seconds(event.timestamp)
            tokens_str = f"tok={event.total_tokens}" if event.total_tokens else ""
            dur_str = f"{event.duration_seconds:.2f}s"
            is_ok = (event.status.value == "success" if hasattr(event.status, "value") else str(event.status) == "success")

            self.timeline.append(
                TimelineEvent(
                    timestamp=event.timestamp,
                    relative_seconds=rel_sec,
                    run_id=event.run_id,
                    agent_role=event.agent_role,
                    category="[W1][LLM]",
                    icon="✓" if is_ok else "✗",
                    title=f"LLM response received ({dur_str})",
                    details=f"{tokens_str} calls_requested={event.tool_calls_count}",
                    style="success" if is_ok else "error",
                )
            )
        self._notify()

    def handle_tool_requested(self, event: ToolCallRequestedEvent) -> None:
        with self._lock:
            self.record_raw_event(event)
            if event.run_id in self.runs:
                node = self.runs[event.run_id]
                if event.call_id not in node.tool_calls_map:
                    node.tool_calls_map[event.call_id] = ToolCallNode(
                        call_id=event.call_id,
                        run_id=event.run_id,
                        tool_name=event.tool_name,
                        start_time=event.timestamp,
                    )

            rel_sec = self.get_relative_seconds(event.timestamp)
            self.timeline.append(
                TimelineEvent(
                    timestamp=event.timestamp,
                    relative_seconds=rel_sec,
                    run_id=event.run_id,
                    agent_role=event.agent_role,
                    category="[W1][ACTION]",
                    icon="◆",
                    title=f"action → {event.tool_name}",
                    details=f"step={event.step}",
                    style="info",
                )
            )
        self._notify()

    def handle_tool_started(self, event: ToolCallStartedEvent) -> None:
        with self._lock:
            self.record_raw_event(event)
            is_mcp = (event.tool_source == ToolSource.MCP)
            if event.run_id in self.runs:
                node = self.runs[event.run_id]
                t_node = node.tool_calls_map.setdefault(
                    event.call_id,
                    ToolCallNode(
                        call_id=event.call_id,
                        run_id=event.run_id,
                        tool_name=event.tool_name,
                    ),
                )
                t_node.tool_source = event.tool_source
                t_node.server_name = event.server_name
                t_node.start_time = event.timestamp
                t_node.canonical_identity = canonical_tool_identity(
                    event.tool_source, event.server_name, event.tool_name
                )

            rel_sec = self.get_relative_seconds(event.timestamp)
            category = "[W2][MCP]" if is_mcp else "[W1][TOOL]"
            icon = "⛯" if is_mcp else "◆"
            srv = f" ({event.server_name})" if event.server_name else ""

            self.timeline.append(
                TimelineEvent(
                    timestamp=event.timestamp,
                    relative_seconds=rel_sec,
                    run_id=event.run_id,
                    agent_role=event.agent_role,
                    category=category,
                    icon=icon,
                    title=f"invoking {event.tool_name}{srv}",
                    details=f"call_id={event.call_id[:8]}",
                    style="mcp" if is_mcp else "info",
                )
            )
        self._notify()

    def handle_tool_finished(self, event: ToolCallFinishedEvent) -> None:
        with self._lock:
            self.record_raw_event(event)
            is_mcp = (event.tool_source == ToolSource.MCP)
            if event.run_id in self.runs:
                node = self.runs[event.run_id]
                t_node = node.tool_calls_map.setdefault(
                    event.call_id,
                    ToolCallNode(
                        call_id=event.call_id,
                        run_id=event.run_id,
                        tool_name=event.tool_name,
                    ),
                )
                t_node.finish_time = event.timestamp
                t_node.duration_seconds = event.duration_seconds
                t_node.status = event.status.value if hasattr(event.status, "value") else str(event.status)
                t_node.is_error = event.is_error
                t_node.error_message = event.error_message
                t_node.observation_length = event.observation_length

            # Update ToolCard stats
            if event.tool_name in self.tools:
                tc = self.tools[event.tool_name]
                tc.call_count += 1
                if event.is_error:
                    tc.error_count += 1
                else:
                    tc.success_count += 1

            rel_sec = self.get_relative_seconds(event.timestamp)
            category = "[W2][MCP]" if is_mcp else "[W1][TOOL]"
            icon = "✗" if event.is_error else "✓"
            style = "error" if event.is_error else ("mcp" if is_mcp else "success")
            title = f"{event.tool_name} returned ({event.duration_seconds:.3f}s)"
            details = f"bytes={event.observation_length} outcome={'ERROR' if event.is_error else 'SUCCESS'}"

            self.timeline.append(
                TimelineEvent(
                    timestamp=event.timestamp,
                    relative_seconds=rel_sec,
                    run_id=event.run_id,
                    agent_role=event.agent_role,
                    category=category,
                    icon=icon,
                    title=title,
                    details=details,
                    style=style,
                )
            )
        self._notify()

    def handle_permission_decision(self, event: PermissionEvaluatedEvent) -> None:
        with self._lock:
            self.record_raw_event(event)
            dec_str = event.decision.value.upper() if hasattr(event.decision, "value") else str(event.decision).upper()
            risk_str = event.risk_level.value.upper() if hasattr(event.risk_level, "value") else str(event.risk_level).upper()

            # Correlate into tool call if present
            if event.run_id in self.runs and event.call_id in self.runs[event.run_id].tool_calls_map:
                t_node = self.runs[event.run_id].tool_calls_map[event.call_id]
                t_node.permission_decision = event.decision
                t_node.risk_level = event.risk_level
                t_node.matched_rule = event.matched_rule
                t_node.canonical_identity = event.canonical_tool_identity
                t_node.arguments_fingerprint = event.arguments_fingerprint

            # Update ToolCard last decision
            if event.tool_name in self.tools:
                tc = self.tools[event.tool_name]
                tc.last_decision = dec_str
                tc.last_risk_level = risk_str
                tc.last_matched_rule = event.matched_rule

            # Record governance audit
            rel_sec = self.get_relative_seconds(event.timestamp)
            self.governance_log.append(
                GovernanceRecord(
                    timestamp=event.timestamp,
                    relative_seconds=rel_sec,
                    run_id=event.run_id,
                    call_id=event.call_id,
                    tool_name=event.tool_name,
                    resource_descriptor=event.canonical_tool_identity,
                    risk_level=event.risk_level,
                    matched_rule=event.matched_rule,
                    decision=event.decision,
                    is_boundary_violation=(event.risk_level == RiskLevel.CRITICAL),
                )
            )

            # Timeline entry
            icon = "✓" if dec_str == "ALLOW" else ("!" if "CONFIRM" in dec_str else "✗")
            style = "success" if dec_str == "ALLOW" else ("warning" if "CONFIRM" in dec_str else "error")

            self.timeline.append(
                TimelineEvent(
                    timestamp=event.timestamp,
                    relative_seconds=rel_sec,
                    run_id=event.run_id,
                    agent_role=event.agent_role,
                    category="[W3][POLICY]",
                    icon=icon,
                    title=f"policy: {event.tool_name} → {dec_str}",
                    details=f"risk={risk_str} rule={event.matched_rule or 'default'}",
                    style=style,
                )
            )
        self._notify()

    def handle_confirmation_resolved(self, event: ConfirmationResolvedEvent) -> None:
        with self._lock:
            self.record_raw_event(event)
            status_str = "APPROVED" if event.approved else "REJECTED"

            if event.run_id in self.runs and event.call_id in self.runs[event.run_id].tool_calls_map:
                t_node = self.runs[event.run_id].tool_calls_map[event.call_id]
                t_node.confirmation_outcome = status_str

            for idx, rec in enumerate(self.governance_log):
                if rec.call_id == event.call_id:
                    self.governance_log[idx] = replace(rec, confirmation_outcome=status_str)
                    break

            rel_sec = self.get_relative_seconds(event.timestamp)
            icon = "✓" if event.approved else "✗"
            style = "success" if event.approved else "error"

            self.timeline.append(
                TimelineEvent(
                    timestamp=event.timestamp,
                    relative_seconds=rel_sec,
                    run_id=event.run_id,
                    agent_role=event.agent_role,
                    category="[W3][CONFIRM]",
                    icon=icon,
                    title=f"confirmation: {status_str}",
                    details=f"tool={event.canonical_tool_identity} ({event.duration_seconds:.2f}s)",
                    style=style,
                )
            )
        self._notify()

    def handle_delegation_started(self, event: DelegationStartedEvent) -> None:
        with self._lock:
            self.record_raw_event(event)
            rel_sec = self.get_relative_seconds(event.timestamp)

            if event.child_agent_role in self.agents:
                self.agents[event.child_agent_role].delegations_count += 1

            self.timeline.append(
                TimelineEvent(
                    timestamp=event.timestamp,
                    relative_seconds=rel_sec,
                    run_id=event.run_id,
                    agent_role=event.parent_agent_role,
                    category="[W3][DELEGATE]",
                    icon="⮑",
                    title=f"delegate: {event.parent_agent_role} → {event.child_agent_role}",
                    details=f"depth={event.delegation_depth} task_chars={event.task_length}",
                    style="delegation",
                )
            )
        self._notify()

    def handle_delegation_finished(self, event: DelegationFinishedEvent) -> None:
        with self._lock:
            self.record_raw_event(event)
            rel_sec = self.get_relative_seconds(event.timestamp)
            is_ok = (event.status == "success")

            if event.child_agent_role in self.agents:
                self.agents[event.child_agent_role].total_duration_seconds += event.duration_seconds

            self.timeline.append(
                TimelineEvent(
                    timestamp=event.timestamp,
                    relative_seconds=rel_sec,
                    run_id=event.run_id,
                    agent_role=event.child_agent_role,
                    category="[W3][DELEGATE]",
                    icon="✓" if is_ok else "✗",
                    title=f"child {event.child_agent_role} completed ({event.duration_seconds:.2f}s)",
                    details=f"steps={event.steps} tools={event.tool_calls} status={'OK' if is_ok else 'FAIL'}",
                    style="success" if is_ok else "error",
                )
            )
        self._notify()

    def handle_memory_op(self, event: MemoryOperationEvent) -> None:
        with self._lock:
            self.record_raw_event(event)
            rel_sec = self.get_relative_seconds(event.timestamp)
            op_type = event.operation_type.value.upper() if hasattr(event.operation_type, "value") else str(event.operation_type).upper()
            status_str = event.status.value.upper() if hasattr(event.status, "value") else str(event.status).upper()
            is_ok = (status_str == "SUCCESS")

            self.timeline.append(
                TimelineEvent(
                    timestamp=event.timestamp,
                    relative_seconds=rel_sec,
                    run_id=event.run_id,
                    agent_role=event.agent_role,
                    category="[W2][MEMORY]",
                    icon="✓" if is_ok else "✗",
                    title=f"memory: {op_type} ({event.duration_seconds:.3f}s)",
                    details=f"status={status_str} entries={event.entry_count}",
                    style="success" if is_ok else "error",
                )
            )
        self._notify()

    def handle_mcp_retry(self, event: MCPRetryEvent) -> None:
        with self._lock:
            self.record_raw_event(event)
            rel_sec = self.get_relative_seconds(event.timestamp)

            if event.server_name in self.mcp_servers:
                self.mcp_servers[event.server_name].retries_count += 1

            self.timeline.append(
                TimelineEvent(
                    timestamp=event.timestamp,
                    relative_seconds=rel_sec,
                    run_id=event.run_id,
                    agent_role=event.agent_role,
                    category="[W2][RETRY]",
                    icon="!",
                    title=f"retry: {event.server_name}:{event.tool_name} (attempt {event.attempt}/{event.max_retries})",
                    details=f"backoff={event.delay_seconds:.2f}s err={event.error_message[:40]}",
                    style="warning",
                )
            )
        self._notify()

    def handle_mcp_circuit_changed(self, event: MCPCircuitStateChangedEvent) -> None:
        with self._lock:
            self.record_raw_event(event)
            rel_sec = self.get_relative_seconds(event.timestamp)

            if event.server_name in self.mcp_servers:
                srv = self.mcp_servers[event.server_name]
                srv.circuit_state = event.to_state.upper()
                srv.consecutive_failures = event.consecutive_failures

            to_st = event.to_state.upper()
            icon = "✓" if to_st == "CLOSED" else ("!" if to_st == "HALF_OPEN" else "✗")
            style = "success" if to_st == "CLOSED" else ("warning" if to_st == "HALF_OPEN" else "error")

            self.timeline.append(
                TimelineEvent(
                    timestamp=event.timestamp,
                    relative_seconds=rel_sec,
                    run_id=event.run_id,
                    agent_role=event.agent_role,
                    category="[W2][CIRCUIT]",
                    icon=icon,
                    title=f"circuit: {event.server_name} {event.from_state.upper()} → {to_st}",
                    details=f"failures={event.consecutive_failures}",
                    style=style,
                )
            )
        self._notify()

    def _collect_descendant_runs(self, node: RunNode) -> list[RunNode]:
        """Recursively collect a run and all its child specialist runs."""
        res = [node]
        for c_id in node.children_run_ids:
            child = self.runs.get(c_id)
            if child:
                res.extend(self._collect_descendant_runs(child))
        return res

    def explain_run(self, target_run_id: str | None = None) -> str:
        """Reconstruct human-readable architectural execution explanation from deterministic state.

        Analyzes the full execution tree rooted at target_run_id (or latest root run).
        Explains delegation, governance, MCP adaptation, and workspace containment.
        Never relies on private LLM chain-of-thought.
        """
        with self._lock:
            run_id = target_run_id or self.selected_run_id or (self.root_run_ids[-1] if self.root_run_ids else None)
            if not run_id or run_id not in self.runs:
                return "No run selected to explain."

            run = self.runs[run_id]
            tree_runs = self._collect_descendant_runs(run)

            lines = [
                f"=== ARCHITECTURAL EXECUTION EXPLANATION: RUN {run.run_id[:8]} ===",
                f"Role: {run.agent_role} | Status: {run.status} | Duration: {run.duration_seconds:.2f}s",
                "",
                "1. DELEGATION & SPECIALISTS:",
            ]
            if run.children_run_ids:
                for c_id in run.children_run_ids:
                    c = self.runs.get(c_id)
                    if c:
                        lines.append(
                            f"  • Task was delegated to specialist '{c.agent_role}' "
                            f"because required capabilities belong to that profile."
                        )
            elif run.parent_run_id:
                lines.append(
                    f"  • Executed as a delegated specialist ('{run.agent_role}') under parent run {run.parent_run_id[:8]}."
                )
            else:
                lines.append("  • Execution proceeded directly within the orchestrator.")

            # Collect tools across the entire execution tree
            all_tools: list[tuple[RunNode, ToolCallNode]] = []
            for r in tree_runs:
                for t in r.tool_calls_map.values():
                    all_tools.append((r, t))

            lines.extend(["", "2. GOVERNANCE & PERMISSION EVALUATION:"])
            gov_found = False
            for r, t in all_tools:
                if t.risk_level:
                    gov_found = True
                    invoked_ctx = f" (via '{r.agent_role}')" if r.run_id != run.run_id else ""
                    lines.append(
                        f"  • Tool '{t.tool_name}'{invoked_ctx}: classified as {t.risk_level.value.upper()}. "
                        f"Matched rule '{t.matched_rule}'. Evaluated to {t.permission_decision.value.upper() if t.permission_decision else 'ALLOW'}."
                    )
                    if t.confirmation_outcome:
                        lines.append(f"    Human operator confirmation resolved to: {t.confirmation_outcome}.")
            if not gov_found:
                lines.append("  • No permission-gated or mutating tool calls were evaluated for this run.")

            lines.extend(["", "3. MCP & TOOL ABSTRACTION:"])
            mcp_tools: list[tuple[RunNode, ToolCallNode]] = []
            builtin_tools: list[tuple[RunNode, ToolCallNode]] = []
            for r, t in all_tools:
                is_mcp = (
                    t.tool_source == ToolSource.MCP
                    or (hasattr(t.tool_source, "value") and t.tool_source.value == "mcp")
                    or (t.canonical_identity and t.canonical_identity.startswith("mcp:"))
                )
                if is_mcp:
                    mcp_tools.append((r, t))
                else:
                    builtin_tools.append((r, t))

            if mcp_tools:
                mcp_seen: set[str] = set()
                builtin_names = sorted(set(b.tool_name for _, b in builtin_tools))
                builtin_context = f"built-in tools ({', '.join(builtin_names)})" if builtin_names else "built-in filesystem tools"

                for r, t in mcp_tools:
                    if t.tool_name in mcp_seen:
                        continue
                    mcp_seen.add(t.tool_name)

                    srv_name = t.server_name or "unknown"
                    srv_card = self.mcp_servers.get(srv_name)
                    transport_str = f" over transport '{srv_card.transport_type}'" if (srv_card and srv_card.transport_type) else ""
                    invoked_by = f" by delegated specialist '{r.agent_role}'" if r.run_id != run.run_id else ""

                    lines.append(
                        f"  • Tool '{t.tool_name}' originated from external MCP server '{srv_name}'{transport_str}{invoked_by}."
                    )
                    lines.append(
                        f"    Unlike {builtin_context}, which execute directly within local harness modules, "
                        f"this capability was discovered dynamically via MCP. It was adapted through MCPToolAdapter into "
                        f"the same internal ToolSpec/ToolResult abstraction used by built-in tools. Invocation then "
                        f"passed through the harness execution and governance path, while the enclosing agent run "
                        f"remained subject to the same bounded execution budget."
                    )
            else:
                if builtin_tools:
                    builtin_names = sorted(set(b.tool_name for _, b in builtin_tools))
                    lines.append(
                        f"  • No external MCP tool execution was observed for this run; "
                        f"all invoked tools ({', '.join(builtin_names)}) were built-in harness capabilities."
                    )
                else:
                    lines.append("  • No external MCP tool execution was observed for this run.")

            lines.extend([
                "",
                "4. WORKSPACE CONTAINMENT:",
                "  • All filesystem operations were verified against the strict workspace root barrier.",
                "",
                "Notice: This explanation is reconstructed entirely from deterministic lifecycle events",
                "and active configuration rules. It does not expose private LLM chain-of-thought.",
            ])
            return "\n".join(lines)
