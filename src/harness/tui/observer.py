"""Decoupled TUI event observer bridging the LifecycleEventBus to the RuntimeStateStore."""

from __future__ import annotations

import logging

from harness.runtime.events import (
    ConfirmationResolvedEvent,
    DelegationFinishedEvent,
    DelegationStartedEvent,
    EventObserver,
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
from harness.tui.store import RuntimeStateStore

logger = logging.getLogger("harness.tui.observer")


class TUIObserver(EventObserver):
    """Subscribes to LifecycleEventBus and updates the in-memory RuntimeStateStore.

    Invariant:
      - Telemetry independence: Errors in this observer are contained and never propagate
        to interrupt or crash the agent runtime.
      - Fully synchronous, in-memory, non-blocking execution.
    """

    def __init__(self, store: RuntimeStateStore) -> None:
        self.store = store

    def on_event(self, event: LifecycleEvent) -> None:
        """Route incoming lifecycle event to the appropriate store handler."""
        try:
            if isinstance(event, RunStartedEvent):
                self.store.handle_run_started(event)
            elif isinstance(event, RunFinishedEvent):
                self.store.handle_run_finished(event)
            elif isinstance(event, LLMCallStartedEvent):
                self.store.handle_llm_started(event)
            elif isinstance(event, LLMCallFinishedEvent):
                self.store.handle_llm_finished(event)
            elif isinstance(event, ToolCallRequestedEvent):
                self.store.handle_tool_requested(event)
            elif isinstance(event, ToolCallStartedEvent):
                self.store.handle_tool_started(event)
            elif isinstance(event, ToolCallFinishedEvent):
                self.store.handle_tool_finished(event)
            elif isinstance(event, MemoryOperationEvent):
                self.store.handle_memory_op(event)
            elif isinstance(event, PermissionEvaluatedEvent):
                self.store.handle_permission_decision(event)
            elif isinstance(event, ConfirmationResolvedEvent):
                self.store.handle_confirmation_resolved(event)
            elif isinstance(event, DelegationStartedEvent):
                self.store.handle_delegation_started(event)
            elif isinstance(event, DelegationFinishedEvent):
                self.store.handle_delegation_finished(event)
            elif isinstance(event, MCPRetryEvent):
                self.store.handle_mcp_retry(event)
            elif isinstance(event, MCPCircuitStateChangedEvent):
                self.store.handle_mcp_circuit_changed(event)
            else:
                self.store.record_raw_event(event)
        except Exception as exc:
            logger.debug(f"TUIObserver safely handled unexpected error processing event: {exc}")
