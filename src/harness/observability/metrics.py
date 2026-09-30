"""Prometheus metrics observer for agent harness lifecycle events."""

from __future__ import annotations

import logging
import threading
from typing import Any

from prometheus_client import CollectorRegistry, Counter, Gauge, Histogram

from harness.runtime.events import (
    ConfirmationResolvedEvent,
    DelegationFinishedEvent,
    LifecycleEvent,
    LLMCallFinishedEvent,
    MCPCircuitStateChangedEvent,
    MCPRetryEvent,
    MemoryOperationEvent,
    PermissionEvaluatedEvent,
    RunFinishedEvent,
    RunStartedEvent,
    ToolCallFinishedEvent,
)

logger = logging.getLogger("harness.observability.metrics")

# -----------------------------------------------------------------------------
# Histogram Bucket Boundaries Calibrated to Empirical Agent Runtime Data
# -----------------------------------------------------------------------------

# Agent turn duration: fast single-step turns (1-3s), multi-step turns (10-30s), budget cutoff (60-120s)
AGENT_RUN_LATENCY_BUCKETS = (
    0.5, 1.0, 2.5, 5.0, 10.0, 15.0, 20.0, 30.0, 45.0, 60.0, 90.0, 120.0
)

# LLM inference latency: resolves short completions (<1s), typical turns (1.5-5s), long/retry turns (5-20s+)
LLM_CALL_LATENCY_BUCKETS = (
    0.1, 0.25, 0.5, 1.0, 2.0, 3.5, 5.0, 7.5, 10.0, 15.0, 20.0, 30.0
)

# Tool latency: local filesystem (<5ms), MCP local/HTTP (20ms-200ms), network timeouts (5-10s)
TOOL_CALL_LATENCY_BUCKETS = (
    0.001, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0
)

# Memory operation latency:
# - SQLite BM25 retrieval / admission: 1-10ms
# - Warm dense CPU neural indexing: 150-800ms
# - Cold lazy Arctic model initialization + indexing: ~7-15s (empirically measured at 9.373s)
MEMORY_OPERATION_LATENCY_BUCKETS = (
    0.001, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.0, 5.0, 7.5, 10.0, 15.0, 30.0
)


class PrometheusObserver:
    """Subscriber to LifecycleEventBus that exposes metrics to a Prometheus CollectorRegistry.

    Cardinality & Governance Policy:
        Labels are strictly constrained to bounded vocabularies (agent_role, model, tool_name,
        tool_source, operation_type, status, failure_category). High-cardinality values such as
        trace_id, run_id, call_id, user prompts, and raw error tracebacks are strictly prohibited.
    """

    def __init__(
        self,
        registry: CollectorRegistry | None = None,
        default_model: str = "qwen-agentworld-35b-a3b",
    ) -> None:
        self.registry = registry if registry is not None else CollectorRegistry(auto_describe=True)
        self._default_model = default_model

        # Concurrency-safe and idempotent active-run tracking: run_id -> agent_role
        self._active_runs: dict[str, str] = {}
        self._lock = threading.Lock()

        # 1. Agent run metrics
        self.agent_runs_total = Counter(
            "harness_agent_runs_total",
            "Total completed agent runs across all roles and terminal statuses.",
            ["agent_role", "status", "failure_category"],
            registry=self.registry,
        )
        self.agent_runs_active = Gauge(
            "harness_agent_runs_active",
            "Current number of concurrently running agent turns.",
            ["agent_role"],
            registry=self.registry,
        )
        self.agent_run_duration_seconds = Histogram(
            "harness_agent_run_duration_seconds",
            "Total end-to-end agent turn latency in seconds.",
            ["agent_role"],
            buckets=AGENT_RUN_LATENCY_BUCKETS,
            registry=self.registry,
        )

        # 2. LLM metrics
        self.llm_calls_total = Counter(
            "harness_llm_calls_total",
            "Total logical LLM inference calls.",
            ["model", "status", "failure_category"],
            registry=self.registry,
        )
        self.llm_call_duration_seconds = Histogram(
            "harness_llm_call_duration_seconds",
            "Monotonic latency of LLM chat completions in seconds.",
            ["model"],
            buckets=LLM_CALL_LATENCY_BUCKETS,
            registry=self.registry,
        )
        self.llm_input_tokens_total = Counter(
            "harness_llm_input_tokens_total",
            "Total prompt tokens reported by LLM provider.",
            ["model"],
            registry=self.registry,
        )
        self.llm_output_tokens_total = Counter(
            "harness_llm_output_tokens_total",
            "Total completion tokens reported by LLM provider.",
            ["model"],
            registry=self.registry,
        )
        self.llm_retries_total = Counter(
            "harness_llm_retries_total",
            "Total LLM inference retry attempts.",
            ["model"],
            registry=self.registry,
        )

        # 3. Tool metrics
        self.tool_calls_total = Counter(
            "harness_tool_calls_total",
            "Total tool execution attempts.",
            ["tool_name", "tool_source", "status", "failure_category"],
            registry=self.registry,
        )
        self.tool_call_duration_seconds = Histogram(
            "harness_tool_call_duration_seconds",
            "Tool execution duration across ToolExecutor boundary in seconds.",
            ["tool_name", "tool_source"],
            buckets=TOOL_CALL_LATENCY_BUCKETS,
            registry=self.registry,
        )

        # 4. Memory metrics
        self.memory_operations_total = Counter(
            "harness_memory_operations_total",
            "Total memory operations across MemoryManager boundary.",
            ["operation_type", "status", "failure_category"],
            registry=self.registry,
        )
        self.memory_operation_duration_seconds = Histogram(
            "harness_memory_operation_duration_seconds",
            "Memory operation latency in seconds.",
            ["operation_type"],
            buckets=MEMORY_OPERATION_LATENCY_BUCKETS,
            registry=self.registry,
        )

        # 5. Permission & Authorization metrics
        self.permission_decisions_total = Counter(
            "harness_permission_decisions_total",
            "Total permission decisions evaluated.",
            ["decision", "tool_source", "risk_level"],
            registry=self.registry,
        )
        self.permission_confirmations_total = Counter(
            "harness_permission_confirmations_total",
            "Total human confirmation resolution outcomes.",
            ["status"],
            registry=self.registry,
        )

        # 6. Delegation metrics
        self.delegations_total = Counter(
            "harness_delegations_total",
            "Total sub-agent delegation attempts and outcomes.",
            ["parent_role", "child_role", "status"],
            registry=self.registry,
        )
        self.delegation_duration_seconds = Histogram(
            "harness_delegation_duration_seconds",
            "Duration of sub-agent delegations in seconds.",
            ["child_role"],
            buckets=AGENT_RUN_LATENCY_BUCKETS,
            registry=self.registry,
        )

        # 7. MCP Resilience metrics
        self.mcp_retries_total = Counter(
            "harness_mcp_retries_total",
            "Total MCP tool invocation retry attempts across transient failures.",
            ["server_name", "tool_name"],
            registry=self.registry,
        )
        self.mcp_circuit_state = Gauge(
            "harness_mcp_circuit_state",
            "One-hot categorical gauge indicating active circuit breaker state per server.",
            ["server_name", "state"],
            registry=self.registry,
        )
        self.mcp_circuit_transitions_total = Counter(
            "harness_mcp_circuit_transitions_total",
            "Total state transitions observed across per-server circuit breakers.",
            ["server_name", "from_state", "to_state"],
            registry=self.registry,
        )

    def on_event(self, event: LifecycleEvent) -> None:
        """Route domain events to corresponding Prometheus metrics deterministically."""
        if isinstance(event, RunStartedEvent):
            self._handle_run_started(event)
        elif isinstance(event, RunFinishedEvent):
            self._handle_run_finished(event)
        elif isinstance(event, LLMCallFinishedEvent):
            self._handle_llm_call_finished(event)
        elif isinstance(event, ToolCallFinishedEvent):
            self._handle_tool_call_finished(event)
        elif isinstance(event, MemoryOperationEvent):
            self._handle_memory_operation(event)
        elif isinstance(event, PermissionEvaluatedEvent):
            self._handle_permission_evaluated(event)
        elif isinstance(event, ConfirmationResolvedEvent):
            self._handle_confirmation_resolved(event)
        elif isinstance(event, DelegationFinishedEvent):
            self._handle_delegation_finished(event)
        elif isinstance(event, MCPRetryEvent):
            self._handle_mcp_retry(event)
        elif isinstance(event, MCPCircuitStateChangedEvent):
            self._handle_mcp_circuit_state_changed(event)

    def _handle_run_started(self, event: RunStartedEvent) -> None:
        role = event.agent_role or "unknown"
        with self._lock:
            if event.run_id not in self._active_runs:
                self._active_runs[event.run_id] = role
                self.agent_runs_active.labels(agent_role=role).inc()
            else:
                logger.debug(f"Duplicate RunStartedEvent for run_id={event.run_id}; ignoring duplicate increment")

    def _handle_run_finished(self, event: RunFinishedEvent) -> None:
        role = event.agent_role or "unknown"
        status_str = "success" if event.is_success else "error"
        failure_cat = event.failure_category.value if event.failure_category else "none"

        # Concurrency-safe and idempotent decrement
        with self._lock:
            stored_role = self._active_runs.pop(event.run_id, None)
            if stored_role is not None:
                self.agent_runs_active.labels(agent_role=stored_role).dec()
            else:
                logger.debug(f"RunFinishedEvent for untracked run_id={event.run_id}; ignoring gauge decrement")

        self.agent_runs_total.labels(
            agent_role=role,
            status=status_str,
            failure_category=failure_cat,
        ).inc()

        self.agent_run_duration_seconds.labels(
            agent_role=role,
        ).observe(max(event.duration_seconds, 0.0))

    def _handle_llm_call_finished(self, event: LLMCallFinishedEvent) -> None:
        model = event.model or self._default_model
        status_str = event.status.value
        failure_cat = event.failure_category.value if event.failure_category else "none"

        self.llm_calls_total.labels(
            model=model,
            status=status_str,
            failure_category=failure_cat,
        ).inc()

        self.llm_call_duration_seconds.labels(
            model=model,
        ).observe(max(event.duration_seconds, 0.0))

        # Authoritative token accounting: provider-reported only, zero fabrication
        if event.prompt_tokens is not None:
            self.llm_input_tokens_total.labels(model=model).inc(event.prompt_tokens)
        if event.completion_tokens is not None:
            self.llm_output_tokens_total.labels(model=model).inc(event.completion_tokens)

        # Retry tracking: attempts - 1
        retries = max(event.attempts - 1, 0)
        if retries > 0:
            self.llm_retries_total.labels(model=model).inc(retries)

    def _handle_tool_call_finished(self, event: ToolCallFinishedEvent) -> None:
        tool_name = event.tool_name or "unknown"
        tool_source = event.tool_source.value if hasattr(event.tool_source, "value") else str(event.tool_source)
        status_str = event.status.value
        failure_cat = event.failure_category.value if event.failure_category else "none"

        self.tool_calls_total.labels(
            tool_name=tool_name,
            tool_source=tool_source,
            status=status_str,
            failure_category=failure_cat,
        ).inc()

        self.tool_call_duration_seconds.labels(
            tool_name=tool_name,
            tool_source=tool_source,
        ).observe(max(event.duration_seconds, 0.0))

    def _handle_memory_operation(self, event: MemoryOperationEvent) -> None:
        op_type = event.operation_type.value
        status_str = event.status.value
        failure_cat = event.failure_category.value if event.failure_category else "none"

        self.memory_operations_total.labels(
            operation_type=op_type,
            status=status_str,
            failure_category=failure_cat,
        ).inc()

        self.memory_operation_duration_seconds.labels(
            operation_type=op_type,
        ).observe(max(event.duration_seconds, 0.0))

    def _handle_permission_evaluated(self, event: PermissionEvaluatedEvent) -> None:
        self.permission_decisions_total.labels(
            decision=event.decision.value,
            tool_source=event.tool_source.value,
            risk_level=event.risk_level.value,
        ).inc()

    def _handle_confirmation_resolved(self, event: ConfirmationResolvedEvent) -> None:
        status_str = "approved" if event.approved else "rejected"
        self.permission_confirmations_total.labels(
            status=status_str,
        ).inc()

    def _handle_delegation_finished(self, event: DelegationFinishedEvent) -> None:
        parent_role = event.parent_agent_role or event.agent_role or "unknown"
        child_role = event.child_agent_role or "unknown"
        status_str = event.status or "unknown"
        self.delegations_total.labels(
            parent_role=parent_role,
            child_role=child_role,
            status=status_str,
        ).inc()
        self.delegation_duration_seconds.labels(
            child_role=child_role,
        ).observe(max(event.duration_seconds, 0.0))

    def _handle_mcp_retry(self, event: MCPRetryEvent) -> None:
        server = event.server_name or "unknown"
        tool = event.tool_name or "unknown"
        self.mcp_retries_total.labels(
            server_name=server,
            tool_name=tool,
        ).inc()

    def _handle_mcp_circuit_state_changed(self, event: MCPCircuitStateChangedEvent) -> None:
        server = event.server_name or "unknown"
        to_state = event.to_state.lower()
        from_state = event.from_state.lower()

        # Update one-hot state gauge: 1 for active state, 0 for others
        for st in ("closed", "open", "half_open"):
            val = 1.0 if st == to_state else 0.0
            self.mcp_circuit_state.labels(server_name=server, state=st).set(val)

        # Increment categorical transition counter
        self.mcp_circuit_transitions_total.labels(
            server_name=server,
            from_state=from_state,
            to_state=to_state,
        ).inc()
