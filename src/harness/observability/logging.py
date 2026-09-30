"""Structured JSON logging observer for agent harness lifecycle events."""

from __future__ import annotations

from datetime import datetime, timezone
import json
import logging
from pathlib import Path
import sys
import threading
from typing import Any, Callable, TextIO

from harness.runtime.events import (
    ConfirmationResolvedEvent,
    DelegationFinishedEvent,
    DelegationStartedEvent,
    EventObserver,
    LifecycleEvent,
    LLMCallFinishedEvent,
    LLMCallStartedEvent,
    MemoryOperationEvent,
    PermissionEvaluatedEvent,
    RunFinishedEvent,
    RunStartedEvent,
    ToolCallFinishedEvent,
    ToolCallStartedEvent,
)

logger = logging.getLogger("harness.observability.logging")


class StructuredLogObserver(EventObserver):
    """Event subscriber that emits strict, allowlisted machine-parseable JSON lines.

    Privacy & Invariant Guarantees:
        1. Emits only typed lifecycle event metadata.
        2. Strictly prohibits raw user prompts, model responses, tool arguments,
           tool observations, memory records, and arbitrary exception stack traces.
        3. Errors are categorized using bounded taxonomy (`failure_category`).
    """

    def __init__(
        self,
        destination: TextIO | Path | str | Callable[[str], None] | None = None,
    ) -> None:
        self._lock = threading.Lock()
        self._custom_writer: Callable[[str], None] | None = None
        self._file_handle: TextIO | None = None
        self._stream: TextIO | None = None

        if callable(destination):
            self._custom_writer = destination
        elif isinstance(destination, (str, Path)):
            p = Path(destination)
            p.parent.mkdir(parents=True, exist_ok=True)
            self._file_handle = open(p, "a", encoding="utf-8")
        elif destination is not None:
            self._stream = destination
        else:
            self._stream = sys.stdout

    def on_event(self, event: LifecycleEvent) -> None:
        """Format event into strict allowlisted JSON and emit atomically."""
        record = self._format_event(event)
        if record is None:
            return

        line = json.dumps(record, separators=(",", ":"))
        with self._lock:
            try:
                if self._custom_writer is not None:
                    self._custom_writer(line)
                elif self._file_handle is not None:
                    self._file_handle.write(line + "\n")
                    self._file_handle.flush()
                elif self._stream is not None:
                    self._stream.write(line + "\n")
                    self._stream.flush()
            except Exception as exc:
                logger.debug(f"StructuredLogObserver failed to write log: {type(exc).__name__}")

    def close(self) -> None:
        """Close opened file handle if observer owns one."""
        with self._lock:
            if self._file_handle is not None:
                try:
                    self._file_handle.close()
                except Exception:
                    pass
                self._file_handle = None

    def _format_event(self, event: LifecycleEvent) -> dict[str, Any] | None:
        """Format lifecycle event with strict allowlist."""
        iso_ts = datetime.fromtimestamp(event.timestamp, timezone.utc).isoformat()
        base: dict[str, Any] = {
            "timestamp": iso_ts,
            "harness_trace_id": event.trace_id,
            "run_id": event.run_id,
            "agent_role": event.agent_role,
        }

        if isinstance(event, RunStartedEvent):
            base.update(
                {
                    "component": "agent",
                    "event": "run.started",
                    "root_run_id": event.root_run_id,
                    "parent_run_id": event.parent_run_id,
                    "max_steps": event.max_steps,
                    "max_tool_calls": event.max_tool_calls,
                    "max_runtime_seconds": event.max_runtime_seconds,
                }
            )
            return base

        if isinstance(event, RunFinishedEvent):
            base.update(
                {
                    "component": "agent",
                    "event": "run.finished",
                    "status": "success" if event.is_success else "error",
                    "termination_reason": event.termination_reason.value,
                    "steps": event.steps,
                    "tool_calls": event.tool_calls,
                    "duration_seconds": round(event.duration_seconds, 6),
                    "failure_category": event.failure_category.value if event.failure_category else None,
                }
            )
            return base

        if isinstance(event, LLMCallStartedEvent):
            base.update(
                {
                    "component": "llm",
                    "event": "llm.started",
                    "call_id": event.llm_call_id,
                    "model": event.model,
                    "tools_count": event.tools_count,
                }
            )
            return base

        if isinstance(event, LLMCallFinishedEvent):
            base.update(
                {
                    "component": "llm",
                    "event": "llm.finished",
                    "call_id": event.llm_call_id,
                    "model": event.model,
                    "status": event.status.value,
                    "duration_seconds": round(event.duration_seconds, 6),
                    "attempts": event.attempts,
                    "prompt_tokens": event.prompt_tokens,
                    "completion_tokens": event.completion_tokens,
                    "total_tokens": event.total_tokens,
                    "failure_category": event.failure_category.value if event.failure_category else None,
                }
            )
            return base

        if isinstance(event, ToolCallStartedEvent):
            base.update(
                {
                    "component": "tool",
                    "event": "tool.started",
                    "call_id": event.call_id,
                    "tool_name": event.tool_name,
                    "tool_source": event.tool_source.value,
                    "server_name": event.server_name,
                }
            )
            return base

        if isinstance(event, ToolCallFinishedEvent):
            base.update(
                {
                    "component": "tool",
                    "event": "tool.finished",
                    "call_id": event.call_id,
                    "tool_name": event.tool_name,
                    "tool_source": event.tool_source.value,
                    "server_name": event.server_name,
                    "status": event.status.value,
                    "duration_seconds": round(event.duration_seconds, 6),
                    "observation_length": event.observation_length,
                    "failure_category": event.failure_category.value if event.failure_category else None,
                }
            )
            return base

        if isinstance(event, MemoryOperationEvent):
            base.update(
                {
                    "component": "memory",
                    "event": f"memory.{event.operation_type.value}",
                    "operation_id": event.operation_id,
                    "operation": event.operation_type.value,
                    "status": event.status.value,
                    "duration_seconds": round(event.duration_seconds, 6),
                    "entry_count": event.entry_count,
                    "source": event.source,
                    "failure_category": event.failure_category.value if event.failure_category else None,
                }
            )
            return base

        if isinstance(event, PermissionEvaluatedEvent):
            base.update(
                {
                    "component": "permission",
                    "event": "permission.evaluated",
                    "call_id": event.call_id,
                    "canonical_tool_identity": event.canonical_tool_identity,
                    "tool_name": event.tool_name,
                    "tool_source": event.tool_source.value,
                    "decision": event.decision.value,
                    "risk_level": event.risk_level.value,
                    "matched_rule": event.matched_rule,
                }
            )
            return base

        if isinstance(event, ConfirmationResolvedEvent):
            base.update(
                {
                    "component": "permission",
                    "event": "permission.confirmation_resolved",
                    "call_id": event.call_id,
                    "confirmation_id": event.confirmation_id,
                    "canonical_tool_identity": event.canonical_tool_identity,
                    "approved": event.approved,
                    "duration_seconds": round(event.duration_seconds, 6),
                }
            )
            return base

        if isinstance(event, DelegationStartedEvent):
            base.update(
                {
                    "component": "delegation",
                    "event": "delegation.started",
                    "delegation_id": event.delegation_id,
                    "parent_call_id": event.parent_call_id,
                    "child_run_id": event.child_run_id,
                    "parent_agent_id": event.parent_agent_id or event.agent_id,
                    "parent_agent_role": event.parent_agent_role or event.agent_role,
                    "child_agent_id": event.child_agent_id,
                    "child_agent_role": event.child_agent_role,
                    "delegation_depth": event.delegation_depth,
                    "task_length": event.task_length,
                }
            )
            return base

        if isinstance(event, DelegationFinishedEvent):
            base.update(
                {
                    "component": "delegation",
                    "event": "delegation.finished",
                    "delegation_id": event.delegation_id,
                    "parent_call_id": event.parent_call_id,
                    "child_run_id": event.child_run_id,
                    "parent_agent_id": event.parent_agent_id or event.agent_id,
                    "parent_agent_role": event.parent_agent_role or event.agent_role,
                    "child_agent_id": event.child_agent_id,
                    "child_agent_role": event.child_agent_role,
                    "delegation_depth": event.delegation_depth,
                    "status": event.status,
                    "duration_seconds": round(event.duration_seconds, 6),
                    "steps": event.steps,
                    "tool_calls": event.tool_calls,
                    "failure_category": event.failure_category.value if event.failure_category else None,
                }
            )
            return base

        return None
