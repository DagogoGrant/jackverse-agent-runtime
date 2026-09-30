"""Unit tests for StructuredLogObserver."""

from __future__ import annotations

import json
import time
import unittest

from harness.agent.budget import TerminationReason
from harness.observability.logging import StructuredLogObserver
from harness.runtime.events import (
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


class TestStructuredLogging(unittest.TestCase):
    """Test suite for StructuredLogObserver."""

    def test_structured_log_allowlist_and_privacy(self) -> None:
        emitted_lines: list[str] = []
        observer = StructuredLogObserver(destination=emitted_lines.append)
        now = time.time()
        run_id = "run_json_1"
        trace_id = "trace_json_1"

        observer.on_event(
            RunStartedEvent(
                timestamp=now,
                trace_id=trace_id,
                run_id=run_id,
                root_run_id=run_id,
                parent_run_id=None,
                agent_id="agent_1",
                agent_role="orchestrator",
                max_steps=10,
                max_tool_calls=25,
                max_runtime_seconds=60.0,
            )
        )
        observer.on_event(
            MemoryOperationEvent(
                timestamp=now + 0.05,
                trace_id=trace_id,
                run_id=run_id,
                root_run_id=run_id,
                parent_run_id=None,
                agent_id="agent_1",
                agent_role="orchestrator",
                operation_id="mem_1",
                operation_type=MemoryOperationType.RETRIEVE,
                status=MemoryOperationStatus.SUCCESS,
                duration_seconds=0.012,
                entry_count=3,
            )
        )
        observer.on_event(
            LLMCallStartedEvent(
                timestamp=now + 0.1,
                trace_id=trace_id,
                run_id=run_id,
                root_run_id=run_id,
                parent_run_id=None,
                agent_id="agent_1",
                agent_role="orchestrator",
                llm_call_id="call_1",
                model="qwen-agentworld-35b-a3b",
                tools_count=4,
            )
        )
        observer.on_event(
            LLMCallFinishedEvent(
                timestamp=now + 1.2,
                trace_id=trace_id,
                run_id=run_id,
                root_run_id=run_id,
                parent_run_id=None,
                agent_id="agent_1",
                agent_role="orchestrator",
                llm_call_id="call_1",
                model="qwen-agentworld-35b-a3b",
                duration_seconds=1.1,
                status=LLMCallStatus.SUCCESS,
                prompt_tokens=1000,
                completion_tokens=150,
                total_tokens=1150,
                attempts=1,
            )
        )
        observer.on_event(
            ToolCallStartedEvent(
                timestamp=now + 1.3,
                trace_id=trace_id,
                run_id=run_id,
                root_run_id=run_id,
                parent_run_id=None,
                agent_id="agent_1",
                agent_role="orchestrator",
                call_id="tool_1",
                tool_name="find_connection",
                tool_source=ToolSource.MCP,
                server_name="transport_service",
            )
        )
        observer.on_event(
            ToolCallFinishedEvent(
                timestamp=now + 1.4,
                trace_id=trace_id,
                run_id=run_id,
                root_run_id=run_id,
                parent_run_id=None,
                agent_id="agent_1",
                agent_role="orchestrator",
                call_id="tool_1",
                tool_name="find_connection",
                tool_source=ToolSource.MCP,
                server_name="transport_service",
                status=ToolCallStatus.SUCCESS,
                duration_seconds=0.1,
                observation_length=300,
            )
        )
        observer.on_event(
            RunFinishedEvent(
                timestamp=now + 1.5,
                trace_id=trace_id,
                run_id=run_id,
                root_run_id=run_id,
                parent_run_id=None,
                agent_id="agent_1",
                agent_role="orchestrator",
                termination_reason=TerminationReason.FINAL_ANSWER,
                steps=1,
                tool_calls=1,
                duration_seconds=1.5,
                is_success=True,
            )
        )

        self.assertEqual(len(emitted_lines), 7)
        parsed_records = [json.loads(line) for line in emitted_lines]

        for rec in parsed_records:
            self.assertIn("timestamp", rec)
            self.assertEqual(rec["harness_trace_id"], trace_id)
            self.assertNotIn("trace_id", rec)
            self.assertEqual(rec["run_id"], run_id)
            self.assertEqual(rec["agent_role"], "orchestrator")
            self.assertIn("component", rec)
            self.assertIn("event", rec)

            self.assertNotIn("prompt", rec)
            self.assertNotIn("content", rec)
            self.assertNotIn("arguments", rec)
            self.assertNotIn("observation", rec)
            self.assertNotIn("error_message", rec)
            self.assertNotIn("error_type", rec)
            self.assertNotIn("error_code", rec)

        components = [r["component"] for r in parsed_records]
        self.assertEqual(components, ["agent", "memory", "llm", "llm", "tool", "tool", "agent"])

    def test_structured_log_failure_bounded_taxonomy(self) -> None:
        emitted_lines: list[str] = []
        observer = StructuredLogObserver(destination=emitted_lines.append)
        now = time.time()
        run_id = "run_json_fail"
        trace_id = "trace_json_fail"

        raw_secret_error = "Failed to connect to database at postgresql://admin:secretPass@internal:5432/db"

        observer.on_event(
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
        observer.on_event(
            RunFinishedEvent(
                timestamp=now + 1.0,
                trace_id=trace_id,
                run_id=run_id,
                root_run_id=run_id,
                parent_run_id=None,
                agent_id="agent_1",
                agent_role="orchestrator",
                termination_reason=TerminationReason.ERROR,
                is_success=False,
                failure_category=FailureCategory.INTERNAL,
                error_type="DatabaseError",
                error_code="DB_CONN_FAIL",
                error_message=raw_secret_error,
            )
        )

        self.assertEqual(len(emitted_lines), 2)
        rec = json.loads(emitted_lines[1])
        self.assertEqual(rec["status"], "error")
        self.assertEqual(rec["failure_category"], "internal")
        self.assertEqual(rec["harness_trace_id"], trace_id)
        self.assertNotIn("trace_id", rec)
        self.assertNotIn("error_type", rec)
        self.assertNotIn("error_code", rec)
        self.assertNotIn("secretPass", json.dumps(rec))
        self.assertNotIn("postgresql://", json.dumps(rec))

    def test_structured_log_write_failure_privacy(self) -> None:
        secret = "super-secret-credential-9988"

        def failing_writer(line: str) -> None:
            raise RuntimeError(f"Disk error containing secret: {secret}")

        observer = StructuredLogObserver(destination=failing_writer)
        now = time.time()
        with self.assertLogs("harness.observability.logging", level="DEBUG") as cm:
            observer.on_event(
                RunStartedEvent(
                    timestamp=now,
                    trace_id="tr1",
                    run_id="run1",
                    root_run_id="run1",
                    parent_run_id=None,
                    agent_id="agent_1",
                    agent_role="orchestrator",
                )
            )
        output = "\n".join(cm.output)
        self.assertIn("StructuredLogObserver failed to write log: RuntimeError", output)
        self.assertNotIn(secret, output)


if __name__ == "__main__":
    unittest.main()
