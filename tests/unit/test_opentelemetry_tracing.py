"""Unit tests for OpenTelemetry distributed tracing observer."""

from __future__ import annotations

import time
import unittest

from opentelemetry import trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import SpanKind, StatusCode

from harness.agent.budget import TerminationReason
from harness.observability.tracing import OpenTelemetryObserver
from harness.runtime.events import (
    EventObserver,
    FailureCategory,
    LifecycleEventBus,
    LLMCallFinishedEvent,
    LLMCallStartedEvent,
    LLMCallStatus,
    MemoryOperationEvent,
    MemoryOperationStatus,
    MemoryOperationType,
    RunFinishedEvent,
    RunStartedEvent,
    ToolCallFinishedEvent,
    ToolCallStartedEvent,
    ToolCallStatus,
)
from harness.tools.base import ToolSource


class TestOpenTelemetryTracing(unittest.TestCase):
    """Test suite for OpenTelemetry distributed tracing observer."""

    def setUp(self) -> None:
        self.exporter = InMemorySpanExporter()
        self.provider = TracerProvider(resource=Resource.create({"service.name": "agent-harness-test"}))
        self.provider.add_span_processor(SimpleSpanProcessor(self.exporter))
        self.observer = OpenTelemetryObserver(
            service_name="agent-harness-test",
            tracer_provider=self.provider,
        )

    def tearDown(self) -> None:
        self.observer.shutdown()

    def test_span_hierarchy_and_explicit_parentage(self) -> None:
        run_id = "run_abc_123"
        trace_id = "trace_xyz_789"
        root_run_id = run_id
        now = time.time()

        # 1. Run Started
        self.observer.on_event(
            RunStartedEvent(
                timestamp=now,
                trace_id=trace_id,
                run_id=run_id,
                root_run_id=root_run_id,
                parent_run_id=None,
                agent_id="agent_1",
                agent_role="orchestrator",
            )
        )

        # 2. Memory Retrieve
        self.observer.on_event(
            MemoryOperationEvent(
                timestamp=now + 0.05,
                trace_id=trace_id,
                run_id=run_id,
                root_run_id=root_run_id,
                parent_run_id=None,
                agent_id="agent_1",
                agent_role="orchestrator",
                operation_id="mem_op_1",
                operation_type=MemoryOperationType.RETRIEVE,
                status=MemoryOperationStatus.SUCCESS,
                duration_seconds=0.015,
                entry_count=2,
            )
        )

        # 3. LLM Call 1
        self.observer.on_event(
            LLMCallStartedEvent(
                timestamp=now + 0.1,
                trace_id=trace_id,
                run_id=run_id,
                root_run_id=root_run_id,
                parent_run_id=None,
                agent_id="agent_1",
                agent_role="orchestrator",
                llm_call_id="call_1",
                model="qwen-agentworld-35b-a3b",
                temperature=0.0,
                message_count=2,
                tools_count=5,
            )
        )
        self.observer.on_event(
            LLMCallFinishedEvent(
                timestamp=now + 1.5,
                trace_id=trace_id,
                run_id=run_id,
                root_run_id=root_run_id,
                parent_run_id=None,
                agent_id="agent_1",
                agent_role="orchestrator",
                llm_call_id="call_1",
                model="qwen-agentworld-35b-a3b",
                duration_seconds=1.4,
                status=LLMCallStatus.SUCCESS,
                prompt_tokens=500,
                completion_tokens=50,
                total_tokens=550,
            )
        )

        # 4. Built-in Tool Call
        self.observer.on_event(
            ToolCallStartedEvent(
                timestamp=now + 1.6,
                trace_id=trace_id,
                run_id=run_id,
                root_run_id=root_run_id,
                parent_run_id=None,
                agent_id="agent_1",
                agent_role="orchestrator",
                call_id="tool_1",
                tool_name="read_file",
                tool_source=ToolSource.BUILTIN,
            )
        )
        self.observer.on_event(
            ToolCallFinishedEvent(
                timestamp=now + 1.62,
                trace_id=trace_id,
                run_id=run_id,
                root_run_id=root_run_id,
                parent_run_id=None,
                agent_id="agent_1",
                agent_role="orchestrator",
                call_id="tool_1",
                tool_name="read_file",
                tool_source=ToolSource.BUILTIN,
                duration_seconds=0.02,
                status=ToolCallStatus.SUCCESS,
                observation_length=120,
            )
        )

        # 5. External MCP Tool Call
        self.observer.on_event(
            ToolCallStartedEvent(
                timestamp=now + 1.7,
                trace_id=trace_id,
                run_id=run_id,
                root_run_id=root_run_id,
                parent_run_id=None,
                agent_id="agent_1",
                agent_role="orchestrator",
                call_id="tool_2",
                tool_name="find_connection",
                tool_source=ToolSource.MCP,
                server_name="transport_service",
            )
        )
        self.observer.on_event(
            ToolCallFinishedEvent(
                timestamp=now + 1.78,
                trace_id=trace_id,
                run_id=run_id,
                root_run_id=root_run_id,
                parent_run_id=None,
                agent_id="agent_1",
                agent_role="orchestrator",
                call_id="tool_2",
                tool_name="find_connection",
                tool_source=ToolSource.MCP,
                server_name="transport_service",
                duration_seconds=0.08,
                status=ToolCallStatus.SUCCESS,
                observation_length=450,
            )
        )

        # 6. Run Finished
        self.observer.on_event(
            RunFinishedEvent(
                timestamp=now + 2.0,
                trace_id=trace_id,
                run_id=run_id,
                root_run_id=root_run_id,
                parent_run_id=None,
                agent_id="agent_1",
                agent_role="orchestrator",
                termination_reason=TerminationReason.FINAL_ANSWER,
                steps=1,
                tool_calls=2,
                duration_seconds=2.0,
                is_success=True,
            )
        )

        spans = self.exporter.get_finished_spans()
        # Expect 5 spans: memory.retrieve, llm.chat, tool.execute (builtin), tool.execute (mcp), agent.run
        self.assertEqual(len(spans), 5)

        root_span = [s for s in spans if s.name == "agent.run"][0]
        self.assertEqual(root_span.kind, SpanKind.INTERNAL)
        self.assertIsNone(root_span.parent)
        self.assertEqual(root_span.status.status_code, StatusCode.OK)
        self.assertEqual(root_span.attributes["agent.run_id"], run_id)
        self.assertEqual(root_span.attributes["harness.trace_id"], trace_id)
        self.assertEqual(root_span.attributes["agent.role"], "orchestrator")

        root_trace_id = root_span.context.trace_id
        root_span_id = root_span.context.span_id

        child_spans = [s for s in spans if s.name != "agent.run"]
        self.assertEqual(len(child_spans), 4)
        for child in child_spans:
            self.assertEqual(child.context.trace_id, root_trace_id, f"{child.name} does not share root trace_id")
            self.assertIsNotNone(child.parent, f"{child.name} has no parent")
            self.assertEqual(child.parent.span_id, root_span_id, f"{child.name} parent is not root.span_id")

        llm_span = [s for s in spans if s.name == "llm.chat"][0]
        self.assertEqual(llm_span.kind, SpanKind.CLIENT)

        builtin_tool_span = [s for s in spans if s.name == "tool.execute" and s.attributes["tool.source"] == "builtin"][0]
        self.assertEqual(builtin_tool_span.kind, SpanKind.INTERNAL)

        mcp_tool_span = [s for s in spans if s.name == "tool.execute" and s.attributes["tool.source"] == "mcp"][0]
        self.assertEqual(mcp_tool_span.kind, SpanKind.CLIENT)
        self.assertEqual(mcp_tool_span.attributes["tool.server_name"], "transport_service")

        mem_span = [s for s in spans if s.name == "memory.retrieve"][0]
        self.assertEqual(mem_span.kind, SpanKind.INTERNAL)

    def test_gen_ai_semantic_conventions(self) -> None:
        run_id = "run_genai"
        trace_id = "trace_genai"
        now = time.time()

        self.observer.on_event(
            RunStartedEvent(
                timestamp=now,
                trace_id=trace_id,
                run_id=run_id,
                root_run_id=run_id,
                parent_run_id=None,
                agent_id="agent_1",
                agent_role="orchestrator",
            )
        )
        self.observer.on_event(
            LLMCallStartedEvent(
                timestamp=now + 0.1,
                trace_id=trace_id,
                run_id=run_id,
                root_run_id=run_id,
                parent_run_id=None,
                agent_id="agent_1",
                agent_role="orchestrator",
                llm_call_id="call_genai",
                model="qwen-agentworld-35b-a3b",
                temperature=0.0,
            )
        )
        self.observer.on_event(
            LLMCallFinishedEvent(
                timestamp=now + 1.2,
                trace_id=trace_id,
                run_id=run_id,
                root_run_id=run_id,
                parent_run_id=None,
                agent_id="agent_1",
                agent_role="orchestrator",
                llm_call_id="call_genai",
                model="qwen-agentworld-35b-a3b",
                duration_seconds=1.1,
                status=LLMCallStatus.SUCCESS,
                prompt_tokens=11159,
                completion_tokens=426,
                total_tokens=11585,
                attempts=2,
            )
        )
        self.observer.on_event(
            RunFinishedEvent(
                timestamp=now + 1.3,
                trace_id=trace_id,
                run_id=run_id,
                root_run_id=run_id,
                parent_run_id=None,
                agent_id="agent_1",
                agent_role="orchestrator",
                termination_reason=TerminationReason.FINAL_ANSWER,
                steps=1,
                tool_calls=0,
                duration_seconds=1.3,
                is_success=True,
            )
        )

        spans = self.exporter.get_finished_spans()
        llm_span = [s for s in spans if s.name == "llm.chat"][0]

        attrs = llm_span.attributes
        self.assertEqual(attrs["gen_ai.operation.name"], "chat")
        self.assertEqual(attrs["gen_ai.request.model"], "qwen-agentworld-35b-a3b")
        self.assertEqual(attrs["gen_ai.usage.input_tokens"], 11159)
        self.assertEqual(attrs["gen_ai.usage.output_tokens"], 426)
        self.assertEqual(attrs["llm.usage.total_tokens"], 11585)
        self.assertEqual(attrs["llm.attempts"], 2)
        self.assertEqual(llm_span.status.status_code, StatusCode.OK)

    def test_privacy_and_error_bounded_status(self) -> None:
        run_id = "run_err"
        trace_id = "trace_err"
        now = time.time()

        secret_str = "sk-secret-token-12345-do-not-leak"
        raw_error = f"API connection to https://api.endpoint.internal?token={secret_str} timed out after 30s"

        self.observer.on_event(
            RunStartedEvent(
                timestamp=now,
                trace_id=trace_id,
                run_id=run_id,
                root_run_id=run_id,
                parent_run_id=None,
                agent_id="agent_1",
                agent_role="orchestrator",
            )
        )
        self.observer.on_event(
            LLMCallStartedEvent(
                timestamp=now + 0.1,
                trace_id=trace_id,
                run_id=run_id,
                root_run_id=run_id,
                parent_run_id=None,
                agent_id="agent_1",
                agent_role="orchestrator",
                llm_call_id="call_err",
                model="qwen-agentworld-35b-a3b",
            )
        )
        self.observer.on_event(
            LLMCallFinishedEvent(
                timestamp=now + 1.0,
                trace_id=trace_id,
                run_id=run_id,
                root_run_id=run_id,
                parent_run_id=None,
                agent_id="agent_1",
                agent_role="orchestrator",
                llm_call_id="call_err",
                model="qwen-agentworld-35b-a3b",
                duration_seconds=0.9,
                status=LLMCallStatus.ERROR,
                failure_category=FailureCategory.TIMEOUT,
                error_type="TimeoutError",
                error_code="TIMEOUT",
                error_message=raw_error,
            )
        )
        self.observer.on_event(
            RunFinishedEvent(
                timestamp=now + 1.2,
                trace_id=trace_id,
                run_id=run_id,
                root_run_id=run_id,
                parent_run_id=None,
                agent_id="agent_1",
                agent_role="orchestrator",
                termination_reason=TerminationReason.ERROR,
                steps=1,
                tool_calls=0,
                duration_seconds=1.2,
                is_success=False,
                failure_category=FailureCategory.TIMEOUT,
                error_type="TimeoutError",
                error_code="TIMEOUT",
                error_message=raw_error,
            )
        )

        spans = self.exporter.get_finished_spans()
        for s in spans:
            self.assertNotIn(secret_str, str(s.attributes))
            self.assertNotIn(secret_str, str(s.status.description))
            self.assertNotIn("https://", str(s.attributes))
            self.assertNotIn("https://", str(s.status.description))
            self.assertNotIn("error.type", s.attributes)
            self.assertNotIn("error.code", s.attributes)

            if s.name == "llm.chat":
                self.assertEqual(s.status.status_code, StatusCode.ERROR)
                self.assertEqual(s.status.description, "timeout")
                self.assertEqual(s.attributes["failure.category"], "timeout")
            elif s.name == "agent.run":
                self.assertEqual(s.status.status_code, StatusCode.ERROR)
                self.assertEqual(s.status.description, "timeout")
                self.assertEqual(s.attributes["failure.category"], "timeout")

    def test_memory_nanosecond_derivation(self) -> None:
        run_id = "run_mem"
        trace_id = "trace_mem"
        now = 1789296000.5
        duration = 0.25

        self.observer.on_event(
            RunStartedEvent(
                timestamp=now,
                trace_id=trace_id,
                run_id=run_id,
                root_run_id=run_id,
                parent_run_id=None,
                agent_id="agent_1",
                agent_role="orchestrator",
            )
        )
        self.observer.on_event(
            MemoryOperationEvent(
                timestamp=now + duration,
                trace_id=trace_id,
                run_id=run_id,
                root_run_id=run_id,
                parent_run_id=None,
                agent_id="agent_1",
                agent_role="orchestrator",
                operation_id="mem_1",
                operation_type=MemoryOperationType.INDEX,
                status=MemoryOperationStatus.SUCCESS,
                duration_seconds=duration,
                entry_count=1,
            )
        )
        self.observer.on_event(
            RunFinishedEvent(
                timestamp=now + duration + 0.1,
                trace_id=trace_id,
                run_id=run_id,
                root_run_id=run_id,
                parent_run_id=None,
                agent_id="agent_1",
                agent_role="orchestrator",
                termination_reason=TerminationReason.FINAL_ANSWER,
                is_success=True,
            )
        )

        spans = self.exporter.get_finished_spans()
        mem_span = [s for s in spans if s.name == "memory.index"][0]

        expected_end_ns = int((now + duration) * 1_000_000_000)
        expected_duration_ns = int(duration * 1_000_000_000)
        expected_start_ns = expected_end_ns - expected_duration_ns

        self.assertEqual(mem_span.end_time, expected_end_ns)
        self.assertEqual(mem_span.start_time, expected_start_ns)
        self.assertEqual(mem_span.end_time - mem_span.start_time, expected_duration_ns)

    def test_concurrency_isolation_between_runs(self) -> None:
        now = time.time()
        run_a = "run_aaa"
        run_b = "run_bbb"

        self.observer.on_event(RunStartedEvent(timestamp=now, trace_id="trace_a", run_id=run_a, root_run_id=run_a, parent_run_id=None, agent_id="agent_a", agent_role="orchestrator"))
        self.observer.on_event(RunStartedEvent(timestamp=now, trace_id="trace_b", run_id=run_b, root_run_id=run_b, parent_run_id=None, agent_id="agent_b", agent_role="orchestrator"))

        self.observer.on_event(LLMCallStartedEvent(timestamp=now + 0.1, trace_id="trace_a", run_id=run_a, root_run_id=run_a, parent_run_id=None, agent_id="agent_a", agent_role="orchestrator", llm_call_id="call_a1", model="model_a"))
        self.observer.on_event(LLMCallStartedEvent(timestamp=now + 0.2, trace_id="trace_b", run_id=run_b, root_run_id=run_b, parent_run_id=None, agent_id="agent_b", agent_role="orchestrator", llm_call_id="call_b1", model="model_b"))

        self.observer.on_event(LLMCallFinishedEvent(timestamp=now + 0.5, trace_id="trace_a", run_id=run_a, root_run_id=run_a, parent_run_id=None, agent_id="agent_a", agent_role="orchestrator", llm_call_id="call_a1", model="model_a", duration_seconds=0.4))
        self.observer.on_event(LLMCallFinishedEvent(timestamp=now + 0.6, trace_id="trace_b", run_id=run_b, root_run_id=run_b, parent_run_id=None, agent_id="agent_b", agent_role="orchestrator", llm_call_id="call_b1", model="model_b", duration_seconds=0.4))

        self.observer.on_event(RunFinishedEvent(timestamp=now + 1.0, trace_id="trace_a", run_id=run_a, root_run_id=run_a, parent_run_id=None, agent_id="agent_a", agent_role="orchestrator"))
        self.observer.on_event(RunFinishedEvent(timestamp=now + 1.1, trace_id="trace_b", run_id=run_b, root_run_id=run_b, parent_run_id=None, agent_id="agent_b", agent_role="orchestrator"))

        spans = self.exporter.get_finished_spans()
        spans_a = [s for s in spans if s.attributes.get("agent.run_id") == run_a]
        spans_b = [s for s in spans if s.attributes.get("agent.run_id") == run_b]

        self.assertEqual(len(spans_a), 2)
        self.assertEqual(len(spans_b), 2)

        root_a = [s for s in spans_a if s.name == "agent.run"][0]
        llm_a = [s for s in spans_a if s.name == "llm.chat"][0]
        self.assertEqual(llm_a.parent.span_id, root_a.context.span_id)
        self.assertEqual(llm_a.context.trace_id, root_a.context.trace_id)

        root_b = [s for s in spans_b if s.name == "agent.run"][0]
        llm_b = [s for s in spans_b if s.name == "llm.chat"][0]
        self.assertEqual(llm_b.parent.span_id, root_b.context.span_id)
        self.assertEqual(llm_b.context.trace_id, root_b.context.trace_id)

        self.assertNotEqual(root_a.context.trace_id, root_b.context.trace_id)

    def test_tempo_unreachability_resilience(self) -> None:
        """Verify export failure isolation and clean SDK shutdown when endpoint is unreachable."""
        dead_observer = OpenTelemetryObserver(
            endpoint="http://127.0.0.1:59999/v1/traces",
            export_timeout_seconds=0.5,
        )
        bus = LifecycleEventBus()
        bus.subscribe(dead_observer)

        now = time.time()
        bus.publish(
            RunStartedEvent(
                timestamp=now,
                trace_id="trace_dead",
                run_id="run_dead",
                root_run_id="run_dead",
                parent_run_id=None,
                agent_id="agent_1",
                agent_role="orchestrator",
            )
        )
        bus.publish(
            RunFinishedEvent(
                timestamp=now + 0.1,
                trace_id="trace_dead",
                run_id="run_dead",
                root_run_id="run_dead",
                parent_run_id=None,
                agent_id="agent_1",
                agent_role="orchestrator",
                termination_reason=TerminationReason.FINAL_ANSWER,
                is_success=True,
            )
        )

        t0 = time.perf_counter()
        dead_observer.shutdown()
        elapsed = time.perf_counter() - t0
        self.assertLess(elapsed, 5.0, f"Shutdown took unexpectedly long: {elapsed:.2f}s")

    def test_subscriber_failure_isolation_and_secret_redaction_in_logs(self) -> None:
        """Fault-injection test: bus subscriber raising exception with secrets never leaks to logs."""
        secret_token = "secret-token-do-not-leak-999"
        raw_error = f"Authentication failure at https://api.endpoint?key={secret_token}"

        class FaultyObserver(EventObserver):
            def on_event(self, event: LifecycleEvent) -> None:
                raise ValueError(f"Observer exploded with sensitive info: {secret_token}")

        bus = LifecycleEventBus()
        bus.subscribe(FaultyObserver())
        bus.subscribe(self.observer)

        now = time.time()
        with self.assertLogs("harness.runtime.events", level="WARNING") as cm:
            bus.publish(
                RunStartedEvent(
                    timestamp=now,
                    trace_id="tr_secret",
                    run_id="run_secret",
                    root_run_id="run_secret",
                    parent_run_id=None,
                    agent_id="agent_1",
                    agent_role="orchestrator",
                )
            )
            bus.publish(
                RunFinishedEvent(
                    timestamp=now + 0.1,
                    trace_id="tr_secret",
                    run_id="run_secret",
                    root_run_id="run_secret",
                    parent_run_id=None,
                    agent_id="agent_1",
                    agent_role="orchestrator",
                    termination_reason=TerminationReason.ERROR,
                    is_success=False,
                    failure_category=FailureCategory.INTERNAL,
                    error_type="InternalAuthError",
                    error_code="AUTH_FAIL",
                    error_message=raw_error,
                )
            )

        log_output = "\n".join(cm.output)
        # Verify the exception type is logged safely without message or secret
        self.assertIn("Lifecycle observer 'FaultyObserver' failed on event 'run_started': ValueError", log_output)
        self.assertIn("Lifecycle observer 'FaultyObserver' failed on event 'run_finished': ValueError", log_output)
        self.assertNotIn(secret_token, log_output)
        self.assertNotIn("https://", log_output)

        # Verify spans created by OpenTelemetryObserver still succeeded and redacted secrets
        spans = self.exporter.get_finished_spans()
        self.assertEqual(len(spans), 1)
        root_span = spans[0]
        self.assertEqual(root_span.attributes["failure.category"], "internal")
        self.assertNotIn("error.type", root_span.attributes)
        self.assertNotIn("error.code", root_span.attributes)
        self.assertNotIn(secret_token, str(root_span.attributes))


if __name__ == "__main__":
    unittest.main()
