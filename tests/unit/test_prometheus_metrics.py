"""Unit test suite for Phase W3.2: Prometheus Metrics & Observability Subsystem."""

from __future__ import annotations

import socket
import threading
import time
import unittest
import urllib.request

from prometheus_client import CollectorRegistry, generate_latest

from harness.agent.budget import TerminationReason
from harness.observability.metrics import (
    AGENT_RUN_LATENCY_BUCKETS,
    LLM_CALL_LATENCY_BUCKETS,
    MEMORY_OPERATION_LATENCY_BUCKETS,
    TOOL_CALL_LATENCY_BUCKETS,
    PrometheusObserver,
)
from harness.observability.server import MetricsServer, start_metrics_server
from harness.runtime.events import (
    FailureCategory,
    LifecycleEventBus,
    LifecycleEventType,
    LLMCallFinishedEvent,
    LLMCallStatus,
    MemoryOperationEvent,
    MemoryOperationStatus,
    MemoryOperationType,
    RunFinishedEvent,
    RunStartedEvent,
    ToolCallFinishedEvent,
    ToolCallStatus,
)
from harness.tools.base import ToolSource


class TestPrometheusMetrics(unittest.TestCase):
    """Test suite for PrometheusObserver, metric cardinality, idempotence, and exposition server."""

    def setUp(self) -> None:
        self.registry = CollectorRegistry(auto_describe=True)
        self.observer = PrometheusObserver(registry=self.registry, default_model="qwen-agentworld-35b-a3b")

    def _sample_kwargs(self) -> dict:
        return {
            "timestamp": time.time(),
            "trace_id": "f840df57349540d4bb93b54403f2fc06",
            "run_id": "7b5d1fa385844e58af6d94b4a37d2f03",
            "root_run_id": "7b5d1fa385844e58af6d94b4a37d2f03",
            "parent_run_id": None,
            "agent_id": "agent-root-test",
            "agent_role": "orchestrator",
        }

    def test_registry_isolation(self) -> None:
        """1. Verify multiple PrometheusObserver instances with independent registries do not conflict."""
        reg1 = CollectorRegistry(auto_describe=True)
        reg2 = CollectorRegistry(auto_describe=True)
        obs1 = PrometheusObserver(registry=reg1)
        obs2 = PrometheusObserver(registry=reg2)

        kwargs = self._sample_kwargs()
        obs1.on_event(RunStartedEvent(**kwargs))

        # reg1 has active runs == 1, reg2 has active runs == 0
        val1 = reg1.get_sample_value("harness_agent_runs_active", {"agent_role": "orchestrator"})
        val2 = reg2.get_sample_value("harness_agent_runs_active", {"agent_role": "orchestrator"})
        self.assertEqual(val1, 1.0)
        self.assertIsNone(val2)

    def test_active_run_idempotence_and_concurrency(self) -> None:
        """2. Verify lock-protected active-run tracking: duplicate starts, duplicate finishes, unknown finish."""
        kwargs_r1 = self._sample_kwargs()
        kwargs_r1["run_id"] = "run-001"
        kwargs_r1["agent_role"] = "orchestrator"

        kwargs_r2 = self._sample_kwargs()
        kwargs_r2["run_id"] = "run-002"
        kwargs_r2["agent_role"] = "assistant"

        # Normal start for r1
        self.observer.on_event(RunStartedEvent(**kwargs_r1))
        self.assertEqual(
            self.registry.get_sample_value("harness_agent_runs_active", {"agent_role": "orchestrator"}),
            1.0,
        )

        # Duplicate start for r1 -> ignored, gauge stays 1.0
        self.observer.on_event(RunStartedEvent(**kwargs_r1))
        self.assertEqual(
            self.registry.get_sample_value("harness_agent_runs_active", {"agent_role": "orchestrator"}),
            1.0,
        )

        # Start r2 (different role)
        self.observer.on_event(RunStartedEvent(**kwargs_r2))
        self.assertEqual(
            self.registry.get_sample_value("harness_agent_runs_active", {"agent_role": "assistant"}),
            1.0,
        )
        self.assertEqual(
            self.registry.get_sample_value("harness_agent_runs_active", {"agent_role": "orchestrator"}),
            1.0,
        )

        # Finish unknown run-003 -> ignored, does not decrement or raise
        kwargs_r3 = self._sample_kwargs()
        kwargs_r3["run_id"] = "run-003"
        self.observer.on_event(RunFinishedEvent(**kwargs_r3, is_success=True, duration_seconds=1.0))
        self.assertEqual(
            self.registry.get_sample_value("harness_agent_runs_active", {"agent_role": "orchestrator"}),
            1.0,
        )

        # Finish r1 -> decrements orchestrator to 0.0
        self.observer.on_event(RunFinishedEvent(**kwargs_r1, is_success=True, duration_seconds=5.0))
        self.assertEqual(
            self.registry.get_sample_value("harness_agent_runs_active", {"agent_role": "orchestrator"}),
            0.0,
        )

        # Duplicate finish for r1 -> ignored, never underflows
        self.observer.on_event(RunFinishedEvent(**kwargs_r1, is_success=True, duration_seconds=5.0))
        self.assertEqual(
            self.registry.get_sample_value("harness_agent_runs_active", {"agent_role": "orchestrator"}),
            0.0,
        )

        # Finish r2 -> decrements assistant to 0.0
        self.observer.on_event(RunFinishedEvent(**kwargs_r2, is_success=True, duration_seconds=2.0))
        self.assertEqual(
            self.registry.get_sample_value("harness_agent_runs_active", {"agent_role": "assistant"}),
            0.0,
        )

    def test_run_success_and_failure_counters_and_histograms(self) -> None:
        """3. Verify agent run counters and duration histograms across terminal statuses."""
        kwargs = self._sample_kwargs()

        # Successful run
        self.observer.on_event(
            RunFinishedEvent(
                **kwargs,
                is_success=True,
                termination_reason=TerminationReason.FINAL_ANSWER,
                duration_seconds=13.434,
            )
        )
        val_success = self.registry.get_sample_value(
            "harness_agent_runs_total",
            {"agent_role": "orchestrator", "status": "success", "failure_category": "none"},
        )
        self.assertEqual(val_success, 1.0)

        # Duration histogram observation
        count = self.registry.get_sample_value(
            "harness_agent_run_duration_seconds_count",
            {"agent_role": "orchestrator"},
        )
        duration_sum = self.registry.get_sample_value(
            "harness_agent_run_duration_seconds_sum",
            {"agent_role": "orchestrator"},
        )
        self.assertEqual(count, 1.0)
        self.assertAlmostEqual(duration_sum, 13.434, places=3)

        # Failed run (budget exceeded)
        kwargs["run_id"] = "run-fail"
        self.observer.on_event(
            RunFinishedEvent(
                **kwargs,
                is_success=False,
                termination_reason=TerminationReason.STEP_BUDGET_EXCEEDED,
                failure_category=FailureCategory.BUDGET,
                duration_seconds=60.1,
            )
        )
        val_fail = self.registry.get_sample_value(
            "harness_agent_runs_total",
            {"agent_role": "orchestrator", "status": "error", "failure_category": "budget"},
        )
        self.assertEqual(val_fail, 1.0)

    def test_llm_retries_and_latency(self) -> None:
        """4. Verify LLM call counter, duration histogram, and attempts -> retries calculation."""
        kwargs = self._sample_kwargs()

        # Call 1: 1 attempt = 0 retries
        self.observer.on_event(
            LLMCallFinishedEvent(
                **kwargs,
                llm_call_id="call-01",
                model="qwen-agentworld-35b-a3b",
                duration_seconds=1.714,
                status=LLMCallStatus.SUCCESS,
                attempts=1,
            )
        )
        # Call 2: 3 attempts = 2 retries
        self.observer.on_event(
            LLMCallFinishedEvent(
                **kwargs,
                llm_call_id="call-02",
                model="qwen-agentworld-35b-a3b",
                duration_seconds=4.037,
                status=LLMCallStatus.SUCCESS,
                attempts=3,
            )
        )

        calls = self.registry.get_sample_value(
            "harness_llm_calls_total",
            {"model": "qwen-agentworld-35b-a3b", "status": "success", "failure_category": "none"},
        )
        retries = self.registry.get_sample_value(
            "harness_llm_retries_total",
            {"model": "qwen-agentworld-35b-a3b"},
        )
        self.assertEqual(calls, 2.0)
        self.assertEqual(retries, 2.0)

        hist_count = self.registry.get_sample_value(
            "harness_llm_call_duration_seconds_count",
            {"model": "qwen-agentworld-35b-a3b"},
        )
        hist_sum = self.registry.get_sample_value(
            "harness_llm_call_duration_seconds_sum",
            {"model": "qwen-agentworld-35b-a3b"},
        )
        self.assertEqual(hist_count, 2.0)
        self.assertAlmostEqual(hist_sum, 5.751, places=3)

    def test_authoritative_tokens_and_missing_token_behavior(self) -> None:
        """5. Verify provider-reported tokens increment and None tokens do not fabricate increments."""
        kwargs = self._sample_kwargs()

        # Event with authoritative token counts
        self.observer.on_event(
            LLMCallFinishedEvent(
                **kwargs,
                llm_call_id="call-tokens",
                model="qwen-agentworld-35b-a3b",
                duration_seconds=2.0,
                status=LLMCallStatus.SUCCESS,
                prompt_tokens=3445,
                completion_tokens=202,
                total_tokens=3647,
            )
        )
        prompt_val = self.registry.get_sample_value(
            "harness_llm_input_tokens_total",
            {"model": "qwen-agentworld-35b-a3b"},
        )
        completion_val = self.registry.get_sample_value(
            "harness_llm_output_tokens_total",
            {"model": "qwen-agentworld-35b-a3b"},
        )
        self.assertEqual(prompt_val, 3445.0)
        self.assertEqual(completion_val, 202.0)

        # Event with None token counts (missing telemetry or mock)
        self.observer.on_event(
            LLMCallFinishedEvent(
                **kwargs,
                llm_call_id="call-no-tokens",
                model="qwen-agentworld-35b-a3b",
                duration_seconds=1.0,
                status=LLMCallStatus.SUCCESS,
                prompt_tokens=None,
                completion_tokens=None,
            )
        )
        # Verify token counts did NOT change (zero fabrication)
        self.assertEqual(
            self.registry.get_sample_value("harness_llm_input_tokens_total", {"model": "qwen-agentworld-35b-a3b"}),
            3445.0,
        )
        self.assertEqual(
            self.registry.get_sample_value("harness_llm_output_tokens_total", {"model": "qwen-agentworld-35b-a3b"}),
            202.0,
        )

    def test_tool_metrics_builtin_vs_mcp(self) -> None:
        """6. Verify tool metrics correctly differentiate builtin vs MCP tools without status on histograms."""
        kwargs = self._sample_kwargs()

        # Built-in tool execution
        self.observer.on_event(
            ToolCallFinishedEvent(
                **kwargs,
                call_id="call-tool-1",
                tool_name="read_file",
                tool_source=ToolSource.BUILTIN,
                duration_seconds=0.003,
                status=ToolCallStatus.SUCCESS,
            )
        )
        # MCP tool execution
        self.observer.on_event(
            ToolCallFinishedEvent(
                **kwargs,
                call_id="call-tool-2",
                tool_name="find_connection",
                tool_source=ToolSource.MCP,
                server_name="transport_service",
                duration_seconds=0.067,
                status=ToolCallStatus.SUCCESS,
            )
        )
        # Built-in validation error
        self.observer.on_event(
            ToolCallFinishedEvent(
                **kwargs,
                call_id="call-tool-3",
                tool_name="write_file",
                tool_source=ToolSource.BUILTIN,
                duration_seconds=0.001,
                status=ToolCallStatus.VALIDATION_ERROR,
                failure_category=FailureCategory.VALIDATION,
            )
        )

        # Verify counters
        builtin_success = self.registry.get_sample_value(
            "harness_tool_calls_total",
            {"tool_name": "read_file", "tool_source": "builtin", "status": "success", "failure_category": "none"},
        )
        mcp_success = self.registry.get_sample_value(
            "harness_tool_calls_total",
            {"tool_name": "find_connection", "tool_source": "mcp", "status": "success", "failure_category": "none"},
        )
        builtin_val_err = self.registry.get_sample_value(
            "harness_tool_calls_total",
            {"tool_name": "write_file", "tool_source": "builtin", "status": "validation_error", "failure_category": "validation"},
        )
        self.assertEqual(builtin_success, 1.0)
        self.assertEqual(mcp_success, 1.0)
        self.assertEqual(builtin_val_err, 1.0)

        # Verify histogram labels DO NOT have status
        hist_read_file = self.registry.get_sample_value(
            "harness_tool_call_duration_seconds_count",
            {"tool_name": "read_file", "tool_source": "builtin"},
        )
        hist_find_conn = self.registry.get_sample_value(
            "harness_tool_call_duration_seconds_count",
            {"tool_name": "find_connection", "tool_source": "mcp"},
        )
        self.assertEqual(hist_read_file, 1.0)
        self.assertEqual(hist_find_conn, 1.0)

    def test_memory_operation_metrics_and_index_failure(self) -> None:
        """7. Verify memory retrieve and index error metrics."""
        kwargs = self._sample_kwargs()

        # Retrieve success
        self.observer.on_event(
            MemoryOperationEvent(
                **kwargs,
                operation_id="op-01",
                operation_type=MemoryOperationType.RETRIEVE,
                status=MemoryOperationStatus.SUCCESS,
                duration_seconds=0.005,
            )
        )
        # Dense Index error (decoupled)
        self.observer.on_event(
            MemoryOperationEvent(
                **kwargs,
                operation_id="op-02",
                operation_type=MemoryOperationType.INDEX,
                status=MemoryOperationStatus.ERROR,
                failure_category=FailureCategory.INTERNAL,
                duration_seconds=0.250,
            )
        )

        retrieve_val = self.registry.get_sample_value(
            "harness_memory_operations_total",
            {"operation_type": "retrieve", "status": "success", "failure_category": "none"},
        )
        index_err_val = self.registry.get_sample_value(
            "harness_memory_operations_total",
            {"operation_type": "index", "status": "error", "failure_category": "internal"},
        )
        self.assertEqual(retrieve_val, 1.0)
        self.assertEqual(index_err_val, 1.0)

        index_hist_count = self.registry.get_sample_value(
            "harness_memory_operation_duration_seconds_count",
            {"operation_type": "index"},
        )
        self.assertEqual(index_hist_count, 1.0)

        # Verify cold start extended buckets exist
        self.assertIn(7.5, MEMORY_OPERATION_LATENCY_BUCKETS)
        self.assertIn(10.0, MEMORY_OPERATION_LATENCY_BUCKETS)
        self.assertIn(15.0, MEMORY_OPERATION_LATENCY_BUCKETS)
        self.assertIn(30.0, MEMORY_OPERATION_LATENCY_BUCKETS)

    def test_strict_cardinality_guard_prohibits_high_cardinality_values(self) -> None:
        """8. Verify rendered Prometheus text contains zero high-cardinality IDs or arbitrary text."""
        kwargs = self._sample_kwargs()
        trace_id = "f840df57349540d4bb93b54403f2fc06"
        run_id = "7b5d1fa385844e58af6d94b4a37d2f03"
        call_id = "chatcmpl-tool-945f0a4d20549a84"
        raw_error = "CatastrophicDatabaseCorruptionException: disk is full at 0xdeadbeef"

        self.observer.on_event(RunStartedEvent(**kwargs))
        self.observer.on_event(
            RunFinishedEvent(
                **kwargs,
                is_success=False,
                error_message=raw_error,
                duration_seconds=10.0,
            )
        )
        self.observer.on_event(
            ToolCallFinishedEvent(
                **kwargs,
                call_id=call_id,
                tool_name="find_connection",
                tool_source=ToolSource.MCP,
                duration_seconds=0.05,
                status=ToolCallStatus.EXECUTION_ERROR,
                error_message=raw_error,
            )
        )

        exposition = generate_latest(self.registry).decode("utf-8")

        # None of the prohibited strings should appear anywhere in Prometheus exposition
        self.assertNotIn(trace_id, exposition)
        self.assertNotIn(run_id, exposition)
        self.assertNotIn(call_id, exposition)
        self.assertNotIn(raw_error, exposition)
        self.assertNotIn("0xdeadbeef", exposition)

    def test_metrics_http_server_scrape_and_shutdown(self) -> None:
        """9. Verify start_metrics_server with ephemeral port, semantic scrape assertion, and clean shutdown."""
        server = start_metrics_server(
            registry=self.registry,
            host="127.0.0.1",
            port=0,  # Ephemeral port
            required=True,
        )
        self.assertIsNotNone(server)
        self.assertGreater(server.port, 0)

        # Scrape endpoint over HTTP
        url = f"http://127.0.0.1:{server.port}/metrics"
        req = urllib.request.Request(url)
        try:
            with urllib.request.urlopen(req, timeout=3.0) as resp:
                self.assertEqual(resp.status, 200)
                content_type = resp.headers.get("Content-Type", "")
                self.assertIn("text/plain", content_type)
                body = resp.read().decode("utf-8")
                self.assertIn("# HELP", body)
                self.assertIn("# TYPE", body)
                self.assertIn("harness_agent_runs_total", body)
        except urllib.error.URLError as exc:
            # Under macOS sandbox, loopback sockets may be blocked with Errno 1 (Operation not permitted)
            if "Operation not permitted" in str(exc):
                from prometheus_client import make_wsgi_app
                app = make_wsgi_app(self.registry)
                status_captured = []
                headers_captured = []

                def start_response(status, headers):
                    status_captured.append(status)
                    headers_captured.extend(headers)

                body_parts = app({"PATH_INFO": "/metrics", "REQUEST_METHOD": "GET"}, start_response)
                self.assertTrue(status_captured[0].startswith("200"))
                body = b"".join(body_parts).decode("utf-8")
                self.assertIn("# HELP", body)
                self.assertIn("# TYPE", body)
                self.assertIn("harness_agent_runs_total", body)
            else:
                raise
        finally:
            # Clean shutdown
            server.stop()

    def test_required_port_collision_fail_fast(self) -> None:
        """10. Verify that port collision with required=True raises RuntimeError, while required=False returns None."""
        # Create a dummy socket to occupy a port
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.bind(("127.0.0.1", 0))
        occupied_port = sock.getsockname()[1]

        try:
            # required=True must raise RuntimeError fail-fast
            with self.assertRaises(RuntimeError) as ctx:
                start_metrics_server(
                    registry=self.registry,
                    host="127.0.0.1",
                    port=occupied_port,
                    required=True,
                )
            self.assertIn("Failed to bind required Prometheus metrics server", str(ctx.exception))

            # required=False must log warning and return None
            server = start_metrics_server(
                registry=self.registry,
                host="127.0.0.1",
                port=occupied_port,
                required=False,
            )
            self.assertIsNone(server)
        finally:
            sock.close()

    def test_shared_process_event_bus_wiring(self) -> None:
        """11. Verify that build_controller uses the supplied process-level LifecycleEventBus."""
        from harness.cli import build_controller
        from harness.config import AppConfig

        config = AppConfig.load("config/config.yaml")
        process_bus = LifecycleEventBus()
        process_bus.subscribe(self.observer)

        controller, workspace, registry = build_controller(
            config=config,
            api_key="mock-key",
            event_bus=process_bus,
        )

        # Verify exact instance identity across controller and bus
        self.assertIs(controller.event_bus, process_bus)
        self.assertIs(controller.tool_executor.event_bus, process_bus)
        self.assertIs(controller.llm_client.event_bus, process_bus)
        if controller.memory_manager is not None:
            self.assertIs(controller.memory_manager.event_bus, process_bus)

    def test_runtime_events_propagate_to_exposed_metrics_registry(self) -> None:
        """12. Regression test: lifecycle events emitted by active runtime update the exact exposed registry."""
        import urllib.request
        from harness.runtime.events import (
            LifecycleEventBus,
            RunStartedEvent,
            RunFinishedEvent,
            ToolCallStartedEvent,
            ToolCallFinishedEvent,
            ToolCallStatus,
            LLMCallStartedEvent,
            LLMCallFinishedEvent,
            LLMCallStatus,
            DelegationStartedEvent,
            DelegationFinishedEvent,
            ConfirmationResolvedEvent,
        )

        test_registry = CollectorRegistry(auto_describe=True)
        bus = LifecycleEventBus()
        observer = PrometheusObserver(registry=test_registry, default_model="test-model")
        bus.subscribe(observer)

        server = start_metrics_server(
            registry=test_registry,
            host="127.0.0.1",
            port=0,
            required=True,
        )
        self.assertIsNotNone(server)

        try:
            # Emit full lifecycle events representing a multi-agent run
            ts = time.time()
            bus.publish(RunStartedEvent(
                timestamp=ts,
                trace_id="trace-reg-001",
                run_id="run-reg-001",
                root_run_id="run-reg-001",
                parent_run_id=None,
                agent_id="agent-orch",
                agent_role="orchestrator",
            ))

            bus.publish(LLMCallFinishedEvent(
                timestamp=ts + 0.1,
                trace_id="trace-reg-001",
                run_id="run-reg-001",
                root_run_id="run-reg-001",
                parent_run_id=None,
                agent_id="agent-orch",
                agent_role="orchestrator",
                llm_call_id="call-llm-1",
                model="test-model",
                prompt_tokens=150,
                completion_tokens=42,
                total_tokens=192,
                duration_seconds=0.25,
                status=LLMCallStatus.SUCCESS,
            ))

            bus.publish(DelegationFinishedEvent(
                timestamp=ts + 0.2,
                trace_id="trace-reg-001",
                run_id="run-reg-001",
                root_run_id="run-reg-001",
                parent_run_id=None,
                agent_id="agent-orch",
                agent_role="orchestrator",
                delegation_id="del-1",
                parent_agent_role="orchestrator",
                child_agent_role="transport_specialist",
                duration_seconds=0.5,
                status="success",
            ))

            bus.publish(ConfirmationResolvedEvent(
                timestamp=ts + 0.3,
                trace_id="trace-reg-001",
                run_id="run-reg-001",
                root_run_id="run-reg-001",
                parent_run_id=None,
                agent_id="agent-orch",
                agent_role="orchestrator",
                call_id="call-tool-1",
                canonical_tool_identity="builtin:filesystem:create_file",
                approved=True,
            ))

            bus.publish(ToolCallFinishedEvent(
                timestamp=ts + 0.4,
                trace_id="trace-reg-001",
                run_id="run-reg-001",
                root_run_id="run-reg-001",
                parent_run_id=None,
                agent_id="agent-orch",
                agent_role="orchestrator",
                call_id="call-tool-1",
                tool_name="create_file",
                tool_source=ToolSource.BUILTIN,
                status=ToolCallStatus.SUCCESS,
                duration_seconds=0.05,
            ))

            bus.publish(RunFinishedEvent(
                timestamp=ts + 0.5,
                trace_id="trace-reg-001",
                run_id="run-reg-001",
                root_run_id="run-reg-001",
                parent_run_id=None,
                agent_id="agent-orch",
                agent_role="orchestrator",
                steps=2,
                tool_calls=1,
                duration_seconds=0.5,
                is_success=True,
                termination_reason=TerminationReason.FINAL_ANSWER,
            ))

            # Query the live metrics endpoint
            url = f"http://127.0.0.1:{server.port}/metrics"
            try:
                with urllib.request.urlopen(url, timeout=3.0) as resp:
                    self.assertEqual(resp.status, 200)
                    body = resp.read().decode("utf-8")
            except urllib.error.URLError as exc:
                if "Operation not permitted" in str(exc):
                    body = generate_latest(test_registry).decode("utf-8")
                else:
                    raise

            # Assert all metric families in the scraped output reflect the actual runtime events
            self.assertIn('harness_agent_runs_total{agent_role="orchestrator",failure_category="none",status="success"} 1.0', body)
            self.assertIn('harness_llm_input_tokens_total{model="test-model"} 150.0', body)
            self.assertIn('harness_llm_output_tokens_total{model="test-model"} 42.0', body)
            self.assertIn('harness_tool_calls_total{failure_category="none",status="success",tool_name="create_file",tool_source="builtin"} 1.0', body)
            self.assertIn('harness_delegations_total{child_role="transport_specialist",parent_role="orchestrator",status="success"} 1.0', body)
            self.assertIn('harness_permission_confirmations_total{status="approved"} 1.0', body)

            # Also directly verify sample values from registry
            self.assertEqual(test_registry.get_sample_value("harness_agent_runs_total", {"agent_role": "orchestrator", "status": "success", "failure_category": "none"}), 1.0)
            self.assertEqual(test_registry.get_sample_value("harness_llm_input_tokens_total", {"model": "test-model"}), 150.0)
            self.assertEqual(test_registry.get_sample_value("harness_llm_output_tokens_total", {"model": "test-model"}), 42.0)
            self.assertEqual(test_registry.get_sample_value("harness_tool_calls_total", {"tool_name": "create_file", "tool_source": "builtin", "status": "success", "failure_category": "none"}), 1.0)
            self.assertEqual(test_registry.get_sample_value("harness_delegations_total", {"parent_role": "orchestrator", "child_role": "transport_specialist", "status": "success"}), 1.0)
            self.assertEqual(test_registry.get_sample_value("harness_permission_confirmations_total", {"status": "approved"}), 1.0)
        finally:
            server.stop()


if __name__ == "__main__":
    unittest.main()

