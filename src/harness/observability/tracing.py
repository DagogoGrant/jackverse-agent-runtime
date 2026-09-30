"""OpenTelemetry distributed tracing observer for agent harness lifecycle events."""

from __future__ import annotations

import logging
import threading
from typing import Any

from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, SpanProcessor
from opentelemetry.trace import Span, SpanKind, StatusCode, Tracer

from harness.runtime.events import (
    ConfirmationResolvedEvent,
    DelegationFinishedEvent,
    DelegationStartedEvent,
    EventObserver,
    LifecycleEvent,
    LLMCallFinishedEvent,
    LLMCallStartedEvent,
    LLMCallStatus,
    MemoryOperationEvent,
    MemoryOperationStatus,
    PermissionEvaluatedEvent,
    RunFinishedEvent,
    RunStartedEvent,
    ToolCallFinishedEvent,
    ToolCallStartedEvent,
    ToolCallStatus,
)
from harness.tools.base import ToolSource

logger = logging.getLogger("harness.observability.tracing")


class OpenTelemetryObserver(EventObserver):
    """Subscriber to LifecycleEventBus that translates domain events into OpenTelemetry traces.

    Architecture & Invariants:
        1. Context Hierarchy: All child operations (LLM, tool, memory) are explicitly
           parented to the active root `agent.run` span using `trace.set_span_in_context()`.
        2. Identifier Semantics: OpenTelemetry generates standard 128-bit trace and 64-bit
           span IDs. Application correlation IDs are stored strictly as span attributes
           (`harness.trace_id`, `agent.run_id`, `agent.root_run_id`, `agent.parent_run_id`).
        3. Span Boundaries: LLM calls and external MCP tools execute as `SpanKind.CLIENT`.
           Agent turns, built-in tools, and memory operations execute as `SpanKind.INTERNAL`.
        4. GenAI Semantic Conventions: Adheres to OTel GenAI attributes:
           `gen_ai.operation.name="chat"`, `gen_ai.request.model`, `gen_ai.usage.input_tokens`,
           `gen_ai.usage.output_tokens`. Raw prompts and completions are NEVER recorded.
        5. Privacy & Redaction: Tool arguments, observations, memory records, and arbitrary
           error tracebacks are strictly excluded. Errors use bounded `failure.category`.
        6. Non-blocking & Decoupled: Emits spans to an asynchronous `BatchSpanProcessor`.
           Telemetry export failure never blocks or crashes agent turn execution.
    """

    def __init__(
        self,
        endpoint: str = "http://127.0.0.1:4318/v1/traces",
        service_name: str = "agent-harness",
        environment: str = "development",
        export_timeout_seconds: float = 5.0,
        tracer_provider: TracerProvider | None = None,
        span_processor: SpanProcessor | None = None,
    ) -> None:
        self.endpoint = endpoint
        self.service_name = service_name
        self.environment = environment
        self.export_timeout_seconds = export_timeout_seconds

        if tracer_provider is not None:
            self._tracer_provider = tracer_provider
        else:
            resource = Resource.create(
                {
                    "service.name": self.service_name,
                    "deployment.environment": self.environment,
                }
            )
            self._tracer_provider = TracerProvider(resource=resource)

            if span_processor is not None:
                self._tracer_provider.add_span_processor(span_processor)
            else:
                exporter = OTLPSpanExporter(
                    endpoint=self.endpoint,
                    timeout=self.export_timeout_seconds,
                )
                processor = BatchSpanProcessor(
                    exporter,
                    max_queue_size=2048,
                    schedule_delay_millis=500,
                    max_export_batch_size=512,
                    export_timeout_millis=int(self.export_timeout_seconds * 1000),
                )
                self._tracer_provider.add_span_processor(processor)

        self._tracer: Tracer = self._tracer_provider.get_tracer(
            "harness.observability.tracing",
            "0.1.0",
        )

        self._lock = threading.Lock()
        self._active_runs: dict[str, Span] = {}
        self._active_llm_calls: dict[tuple[str, str], Span] = {}
        self._active_tool_calls: dict[tuple[str, str], Span] = {}
        self._delegation_parent_spans: dict[str, Span] = {}

    @property
    def tracer_provider(self) -> TracerProvider:
        """Expose tracer provider for inspection or test assertions."""
        return self._tracer_provider

    def on_event(self, event: LifecycleEvent) -> None:
        """Route lifecycle event to typed span handlers."""
        if isinstance(event, RunStartedEvent):
            self._on_run_started(event)
        elif isinstance(event, RunFinishedEvent):
            self._on_run_finished(event)
        elif isinstance(event, LLMCallStartedEvent):
            self._on_llm_started(event)
        elif isinstance(event, LLMCallFinishedEvent):
            self._on_llm_finished(event)
        elif isinstance(event, ToolCallStartedEvent):
            self._on_tool_started(event)
        elif isinstance(event, ToolCallFinishedEvent):
            self._on_tool_finished(event)
        elif isinstance(event, MemoryOperationEvent):
            self._on_memory_operation(event)
        elif isinstance(event, PermissionEvaluatedEvent):
            self._on_permission_evaluated(event)
        elif isinstance(event, ConfirmationResolvedEvent):
            self._on_confirmation_resolved(event)
        elif isinstance(event, DelegationStartedEvent):
            self._on_delegation_started(event)
        elif isinstance(event, DelegationFinishedEvent):
            self._on_delegation_finished(event)

    def shutdown(self) -> None:
        """Shut down the tracer provider and flush pending spans.

        Semantics & Lifecycle Contract:
            - Network export operations are bounded by the exporter timeout
              (`export_timeout_seconds` configured at initialization and passed to OTLPSpanExporter).
            - Final SDK shutdown follows OpenTelemetry Python SDK's standard blocking
              shutdown semantics (`TracerProvider.shutdown()`), cleanly joining batch
              processor worker threads without leaking background resources.
        """
        try:
            self._tracer_provider.shutdown()
        except Exception as exc:
            logger.warning(
                f"Error during OpenTelemetryObserver shutdown: {type(exc).__name__}"
            )

    # -------------------------------------------------------------------------
    # Event Handlers
    # -------------------------------------------------------------------------

    def _on_delegation_started(self, event: DelegationStartedEvent) -> None:
        """Correlate child run ID to parent's active delegation tool span."""
        with self._lock:
            parent_tool_span = self._active_tool_calls.get((event.run_id, event.parent_call_id))
            if parent_tool_span is not None:
                self._delegation_parent_spans[event.child_run_id] = parent_tool_span

    def _on_delegation_finished(self, event: DelegationFinishedEvent) -> None:
        """Clean up delegation parent correlation mapping."""
        with self._lock:
            self._delegation_parent_spans.pop(event.child_run_id, None)

    def _on_run_started(self, event: RunStartedEvent) -> None:
        """Create span for the agent turn, properly parented under delegation tool span if sub-agent."""
        with self._lock:
            parent_span = self._delegation_parent_spans.pop(event.run_id, None)
            if parent_span is None and event.parent_run_id:
                parent_span = self._active_runs.get(event.parent_run_id)

        parent_ctx = trace.set_span_in_context(parent_span) if parent_span is not None else None

        span = self._tracer.start_span(
            "agent.run",
            context=parent_ctx,
            kind=SpanKind.INTERNAL,
        )
        span.set_attribute("harness.trace_id", event.trace_id)
        span.set_attribute("agent.run_id", event.run_id)
        span.set_attribute("agent.root_run_id", event.root_run_id)
        if event.parent_run_id:
            span.set_attribute("agent.parent_run_id", event.parent_run_id)
        span.set_attribute("agent.role", event.agent_role)
        span.set_attribute("agent.status", "running")
        span.set_attribute("agent.max_steps", event.max_steps)
        span.set_attribute("agent.max_tool_calls", event.max_tool_calls)
        span.set_attribute("agent.max_runtime_seconds", event.max_runtime_seconds)

        with self._lock:
            self._active_runs[event.run_id] = span

    def _on_run_finished(self, event: RunFinishedEvent) -> None:
        """Finalize root span with outcome status and bounded failure taxonomy."""
        with self._lock:
            span = self._active_runs.pop(event.run_id, None)
            # Clean up any dangling operations for this run
            orphaned_llm = [k for k in self._active_llm_calls if k[0] == event.run_id]
            for k in orphaned_llm:
                s = self._active_llm_calls.pop(k, None)
                if s:
                    s.set_status(StatusCode.ERROR, description="orphaned")
                    s.end()
            orphaned_tool = [k for k in self._active_tool_calls if k[0] == event.run_id]
            for k in orphaned_tool:
                s = self._active_tool_calls.pop(k, None)
                if s:
                    s.set_status(StatusCode.ERROR, description="orphaned")
                    s.end()

        if span is None:
            return

        span.set_attribute("agent.steps", event.steps)
        span.set_attribute("agent.tool_calls", event.tool_calls)
        span.set_attribute("agent.termination_reason", event.termination_reason.value)
        span.set_attribute("agent.duration_seconds", event.duration_seconds)
        status_str = "success" if event.is_success else "error"
        span.set_attribute("agent.status", status_str)

        if event.is_success:
            span.set_status(StatusCode.OK)
        else:
            cat = event.failure_category.value if event.failure_category else "error"
            span.set_attribute("failure.category", cat)
            span.set_status(StatusCode.ERROR, description=cat)

        span.end()

    def _on_llm_started(self, event: LLMCallStartedEvent) -> None:
        """Start CLIENT span for model inference under explicit parent context."""
        with self._lock:
            root_span = self._active_runs.get(event.run_id)

        parent_ctx = trace.set_span_in_context(root_span) if root_span is not None else None

        span = self._tracer.start_span(
            "llm.chat",
            context=parent_ctx,
            kind=SpanKind.CLIENT,
        )
        span.set_attribute("gen_ai.operation.name", "chat")
        span.set_attribute("gen_ai.request.model", event.model)
        span.set_attribute("harness.trace_id", event.trace_id)
        span.set_attribute("agent.run_id", event.run_id)
        span.set_attribute("agent.role", event.agent_role)
        span.set_attribute("llm.call_id", event.llm_call_id)
        span.set_attribute("llm.temperature", event.temperature)
        span.set_attribute("llm.message_count", event.message_count)
        span.set_attribute("llm.tools_count", event.tools_count)

        with self._lock:
            self._active_llm_calls[(event.run_id, event.llm_call_id)] = span

    def _on_llm_finished(self, event: LLMCallFinishedEvent) -> None:
        """Finalize LLM inference span with token usage and bounded error status."""
        with self._lock:
            span = self._active_llm_calls.pop((event.run_id, event.llm_call_id), None)

        if span is None:
            return

        if event.prompt_tokens is not None:
            span.set_attribute("gen_ai.usage.input_tokens", event.prompt_tokens)
        if event.completion_tokens is not None:
            span.set_attribute("gen_ai.usage.output_tokens", event.completion_tokens)
        if event.total_tokens is not None:
            span.set_attribute("llm.usage.total_tokens", event.total_tokens)

        span.set_attribute("llm.attempts", event.attempts)
        span.set_attribute("llm.duration_seconds", event.duration_seconds)
        span.set_attribute("llm.status", event.status.value)

        if event.status == LLMCallStatus.SUCCESS:
            span.set_status(StatusCode.OK)
        else:
            cat = event.failure_category.value if event.failure_category else "error"
            span.set_attribute("failure.category", cat)
            span.set_status(StatusCode.ERROR, description=cat)

        span.end()

    def _on_tool_started(self, event: ToolCallStartedEvent) -> None:
        """Start tool execution span under explicit parent context."""
        with self._lock:
            root_span = self._active_runs.get(event.run_id)

        parent_ctx = trace.set_span_in_context(root_span) if root_span is not None else None
        span_kind = SpanKind.CLIENT if event.tool_source == ToolSource.MCP else SpanKind.INTERNAL

        span = self._tracer.start_span(
            "tool.execute",
            context=parent_ctx,
            kind=span_kind,
        )
        span.set_attribute("tool.name", event.tool_name)
        span.set_attribute("tool.source", event.tool_source.value)
        span.set_attribute("tool.call_id", event.call_id)
        span.set_attribute("harness.trace_id", event.trace_id)
        span.set_attribute("agent.run_id", event.run_id)
        span.set_attribute("agent.role", event.agent_role)
        if event.server_name:
            span.set_attribute("tool.server_name", event.server_name)

        with self._lock:
            self._active_tool_calls[(event.run_id, event.call_id)] = span

    def _on_tool_finished(self, event: ToolCallFinishedEvent) -> None:
        """Finalize tool execution span with outcome metrics."""
        with self._lock:
            span = self._active_tool_calls.pop((event.run_id, event.call_id), None)

        if span is None:
            return

        span.set_attribute("tool.status", event.status.value)
        span.set_attribute("tool.duration_seconds", event.duration_seconds)
        span.set_attribute("tool.observation_length", event.observation_length)

        if event.status == ToolCallStatus.SUCCESS:
            span.set_status(StatusCode.OK)
        else:
            cat = event.failure_category.value if event.failure_category else "error"
            span.set_attribute("failure.category", cat)
            span.set_status(StatusCode.ERROR, description=cat)

        span.end()

    def _on_memory_operation(self, event: MemoryOperationEvent) -> None:
        """Record atomic memory span with exact monotonic epoch nanosecond boundaries."""
        with self._lock:
            root_span = self._active_runs.get(event.run_id)

        parent_ctx = trace.set_span_in_context(root_span) if root_span is not None else None

        end_ns = int(event.timestamp * 1_000_000_000)
        duration = max(event.duration_seconds, 0.0)
        duration_ns = int(duration * 1_000_000_000)
        start_ns = max(0, end_ns - duration_ns)

        span_name = f"memory.{event.operation_type.value}"
        span = self._tracer.start_span(
            span_name,
            context=parent_ctx,
            kind=SpanKind.INTERNAL,
            start_time=start_ns,
        )
        span.set_attribute("memory.operation", event.operation_type.value)
        span.set_attribute("memory.operation_id", event.operation_id)
        span.set_attribute("memory.status", event.status.value)
        span.set_attribute("memory.entry_count", event.entry_count)
        span.set_attribute("memory.duration_seconds", event.duration_seconds)
        span.set_attribute("harness.trace_id", event.trace_id)
        span.set_attribute("agent.run_id", event.run_id)
        span.set_attribute("agent.role", event.agent_role)
        if event.source:
            span.set_attribute("memory.source", event.source)

        if event.status == MemoryOperationStatus.SUCCESS:
            span.set_status(StatusCode.OK)
        else:
            cat = event.failure_category.value if event.failure_category else "error"
            span.set_attribute("failure.category", cat)
            span.set_status(StatusCode.ERROR, description=cat)

        span.end(end_time=end_ns)

    def _on_permission_evaluated(self, event: PermissionEvaluatedEvent) -> None:
        """Attach policy decision attributes to the active tool execution span."""
        with self._lock:
            span = self._active_tool_calls.get((event.run_id, event.call_id))

        if span is not None:
            span.set_attribute("permission.decision", event.decision.value)
            span.set_attribute("permission.risk_level", event.risk_level.value)
            span.set_attribute("permission.rule", event.matched_rule or "none")
            span.set_attribute("permission.canonical_id", event.canonical_tool_identity)

    def _on_confirmation_resolved(self, event: ConfirmationResolvedEvent) -> None:
        """Attach confirmation resolution telemetry to the active tool execution span."""
        with self._lock:
            span = self._active_tool_calls.get((event.run_id, event.call_id))

        if span is not None:
            span.set_attribute("permission.confirmation_id", event.confirmation_id)
            span.set_attribute("permission.confirmation_approved", event.approved)
            span.set_attribute("permission.confirmation_duration_seconds", event.duration_seconds)
