"""Typed lifecycle domain events and decoupled EventBus for agent runtime observability."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
import logging
import threading
import time
from typing import ClassVar, Protocol

from harness.agent.budget import TerminationReason
from harness.permissions.base import PermissionDecision, RiskLevel
from harness.tools.base import ToolSource

logger = logging.getLogger("harness.runtime.events")


# -----------------------------------------------------------------------------
# Bounded Taxonomy & Enums
# -----------------------------------------------------------------------------

class LifecycleEventType(str, Enum):
    """Canonical lifecycle milestones emitted across runtime subsystems."""

    RUN_STARTED = "run_started"
    RUN_FINISHED = "run_finished"
    LLM_CALL_STARTED = "llm_call_started"
    LLM_CALL_FINISHED = "llm_call_finished"
    TOOL_CALL_REQUESTED = "tool_call_requested"
    TOOL_CALL_STARTED = "tool_call_started"
    TOOL_CALL_FINISHED = "tool_call_finished"
    MEMORY_OPERATION = "memory_operation"
    PERMISSION_DECISION = "permission_decision"
    CONFIRMATION_RESOLVED = "confirmation_resolved"
    DELEGATION_STARTED = "delegation_started"        # Reserved for W3.8
    DELEGATION_FINISHED = "delegation_finished"      # Reserved for W3.8
    MCP_RETRY = "mcp_retry"
    MCP_CIRCUIT_STATE_CHANGED = "mcp_circuit_state_changed"


class LLMCallStatus(str, Enum):
    """Terminal outcome of a logical LLM call."""

    SUCCESS = "success"
    ERROR = "error"


class ToolCallStatus(str, Enum):
    """Terminal outcome of a tool execution across ToolExecutor."""

    SUCCESS = "success"
    VALIDATION_ERROR = "validation_error"
    PERMISSION_DENIED = "permission_denied"
    EXECUTION_ERROR = "execution_error"
    CEILING_APPLIED = "ceiling_applied"


class MemoryOperationType(str, Enum):
    """Sub-operation performed on the persistent memory store."""

    RETRIEVE = "retrieve"
    ADMIT = "admit"
    REJECT = "reject"
    QUARANTINE = "quarantine"
    SUPERSEDE = "supersede"
    INDEX = "index"


class MemoryOperationStatus(str, Enum):
    """Outcome status of a memory operation."""

    SUCCESS = "success"
    REJECTED = "rejected"
    QUARANTINED = "quarantined"
    ERROR = "error"


class FailureCategory(str, Enum):
    """Bounded taxonomy of runtime failures for low-cardinality Prometheus labeling.

    Cardinaility Guard:
        Arbitrary Python exception classes (e.g. APITimeoutError, SQLiteCorruptError)
        must never become metric labels. Instead, they are mapped to one of these
        bounded categories for Prometheus, while the raw error_type is retained for traces/logs.
    """

    TIMEOUT = "timeout"
    VALIDATION = "validation"
    PROVIDER = "provider"
    TOOL = "tool"
    PERMISSION = "permission"
    BUDGET = "budget"
    INTERNAL = "internal"


# -----------------------------------------------------------------------------
# Typed Domain Event Envelope & Concrete Classes
# -----------------------------------------------------------------------------

@dataclass(frozen=True)
class LifecycleEvent:
    """Base envelope common to all typed lifecycle domain events."""

    timestamp: float                   # UTC wall clock seconds (time.time())
    trace_id: str                      # 128-bit trace correlation ID (32 hex chars)
    run_id: str                        # Application-level Agent Run ID (32 hex chars)
    root_run_id: str                   # Agent Run ID of top-level orchestrator (32 hex chars)
    parent_run_id: str | None          # Direct parent Agent Run ID (or None if root)
    agent_id: str                      # Identifier of emitting agent instance
    agent_role: str                    # Logical role of emitting agent
    event_type: ClassVar[LifecycleEventType]


@dataclass(frozen=True)
class RunStartedEvent(LifecycleEvent):
    """Emitted when an agent controller begins an execution turn."""

    event_type: ClassVar[LifecycleEventType] = LifecycleEventType.RUN_STARTED
    user_input_length: int = 0
    max_steps: int = 10
    max_tool_calls: int = 25
    max_runtime_seconds: float = 60.0


@dataclass(frozen=True)
class RunFinishedEvent(LifecycleEvent):
    """Emitted when an agent controller finishes an execution turn (terminal guarantee)."""

    event_type: ClassVar[LifecycleEventType] = LifecycleEventType.RUN_FINISHED
    termination_reason: TerminationReason = TerminationReason.FINAL_ANSWER
    steps: int = 0
    tool_calls: int = 0
    duration_seconds: float = 0.0      # Monotonic duration (time.perf_counter() delta)
    is_success: bool = True
    failure_category: FailureCategory | None = None
    error_type: str | None = None
    error_code: str | None = None
    error_message: str | None = None   # Sanitized, bounded string (max 200 chars)


@dataclass(frozen=True)
class LLMCallStartedEvent(LifecycleEvent):
    """Emitted at the common LLMClient boundary before invoking the model."""

    event_type: ClassVar[LifecycleEventType] = LifecycleEventType.LLM_CALL_STARTED
    llm_call_id: str = ""              # 32 hex chars unique call identifier
    model: str = ""
    temperature: float = 0.0
    message_count: int = 0
    tools_count: int = 0


@dataclass(frozen=True)
class LLMCallFinishedEvent(LifecycleEvent):
    """Emitted at the common LLMClient boundary upon inference completion or failure."""

    event_type: ClassVar[LifecycleEventType] = LifecycleEventType.LLM_CALL_FINISHED
    llm_call_id: str = ""
    model: str = ""
    duration_seconds: float = 0.0      # Monotonic duration
    status: LLMCallStatus = LLMCallStatus.SUCCESS
    attempts: int = 1                  # Number of retry attempts made
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    total_tokens: int | None = None
    tool_calls_count: int = 0
    failure_category: FailureCategory | None = None
    error_type: str | None = None
    error_code: str | None = None
    error_message: str | None = None


@dataclass(frozen=True)
class ToolCallRequestedEvent(LifecycleEvent):
    """Emitted by controller when the LLM outputs a structured tool invocation request."""

    event_type: ClassVar[LifecycleEventType] = LifecycleEventType.TOOL_CALL_REQUESTED
    call_id: str = ""                  # Provider tool call ID
    tool_name: str = ""
    step: int = 0


@dataclass(frozen=True)
class ToolCallStartedEvent(LifecycleEvent):
    """Emitted at the common ToolExecutor boundary before tool validation and execution."""

    event_type: ClassVar[LifecycleEventType] = LifecycleEventType.TOOL_CALL_STARTED
    call_id: str = ""                  # Correlated tool call ID
    tool_name: str = ""
    tool_source: ToolSource = ToolSource.BUILTIN
    server_name: str | None = None


@dataclass(frozen=True)
class ToolCallFinishedEvent(LifecycleEvent):
    """Emitted at the common ToolExecutor boundary upon tool completion or error."""

    event_type: ClassVar[LifecycleEventType] = LifecycleEventType.TOOL_CALL_FINISHED
    call_id: str = ""
    tool_name: str = ""
    tool_source: ToolSource = ToolSource.BUILTIN
    duration_seconds: float = 0.0      # Monotonic duration
    status: ToolCallStatus = ToolCallStatus.SUCCESS
    is_error: bool = False
    observation_length: int = 0
    server_name: str | None = None
    failure_category: FailureCategory | None = None
    error_type: str | None = None
    error_code: str | None = None
    error_message: str | None = None


@dataclass(frozen=True)
class MemoryOperationEvent(LifecycleEvent):
    """Emitted at the MemoryManager boundary for retrieve and admit operations."""

    event_type: ClassVar[LifecycleEventType] = LifecycleEventType.MEMORY_OPERATION
    operation_id: str = ""             # 32 hex chars unique operation identifier
    operation_type: MemoryOperationType = MemoryOperationType.RETRIEVE
    status: MemoryOperationStatus = MemoryOperationStatus.SUCCESS
    duration_seconds: float = 0.0      # Monotonic duration
    source: str | None = None
    entry_count: int = 1
    failure_category: FailureCategory | None = None
    error_type: str | None = None
    error_code: str | None = None
    error_message: str | None = None


@dataclass(frozen=True)
class PermissionEvaluatedEvent(LifecycleEvent):
    """Emitted when an action authorization request is evaluated against policy rules."""

    event_type: ClassVar[LifecycleEventType] = LifecycleEventType.PERMISSION_DECISION
    call_id: str = ""
    canonical_tool_identity: str = ""
    tool_name: str = ""
    tool_source: ToolSource = ToolSource.BUILTIN
    decision: PermissionDecision = PermissionDecision.DENY
    matched_rule: str | None = None
    risk_level: RiskLevel = RiskLevel.MUTATING
    arguments_fingerprint: str = ""


@dataclass(frozen=True)
class ConfirmationResolvedEvent(LifecycleEvent):
    """Emitted when a human or automated confirmation prompt is resolved."""

    event_type: ClassVar[LifecycleEventType] = LifecycleEventType.CONFIRMATION_RESOLVED
    call_id: str = ""
    confirmation_id: str = ""
    canonical_tool_identity: str = ""
    approved: bool = False
    duration_seconds: float = 0.0


@dataclass(frozen=True)
class DelegationStartedEvent(LifecycleEvent):
    """Emitted when task delegation to a specialized sub-agent begins."""

    event_type: ClassVar[LifecycleEventType] = LifecycleEventType.DELEGATION_STARTED
    delegation_id: str = ""
    parent_call_id: str = ""
    child_run_id: str = ""
    parent_agent_id: str = ""
    parent_agent_role: str = ""
    child_agent_id: str = ""
    child_agent_role: str = ""
    delegation_depth: int = 0
    task_length: int = 0


@dataclass(frozen=True)
class DelegationFinishedEvent(LifecycleEvent):
    """Emitted when task delegation to a specialized sub-agent completes."""

    event_type: ClassVar[LifecycleEventType] = LifecycleEventType.DELEGATION_FINISHED
    delegation_id: str = ""
    parent_call_id: str = ""
    child_run_id: str = ""
    parent_agent_id: str = ""
    parent_agent_role: str = ""
    child_agent_id: str = ""
    child_agent_role: str = ""
    delegation_depth: int = 0
    duration_seconds: float = 0.0
    status: str = "success"  # "success" or "error"
    steps: int = 0
    tool_calls: int = 0
    failure_category: FailureCategory | None = None


@dataclass(frozen=True)
class MCPRetryEvent(LifecycleEvent):
    """Emitted when an MCP tool invocation attempt fails transiently and is retried."""

    event_type: ClassVar[LifecycleEventType] = LifecycleEventType.MCP_RETRY
    server_name: str = ""
    tool_name: str = ""
    attempt: int = 1
    max_retries: int = 2
    delay_seconds: float = 0.0
    error_message: str = ""


@dataclass(frozen=True)
class MCPCircuitStateChangedEvent(LifecycleEvent):
    """Emitted when an MCP server circuit breaker changes state."""

    event_type: ClassVar[LifecycleEventType] = LifecycleEventType.MCP_CIRCUIT_STATE_CHANGED
    server_name: str = ""
    from_state: str = "closed"
    to_state: str = "open"
    consecutive_failures: int = 0


# -----------------------------------------------------------------------------
# EventBus & Observer Protocol
# -----------------------------------------------------------------------------

class EventObserver(Protocol):
    """Contract for observers subscribing to lifecycle events.

    Performance & Concurrency Invariant:
        on_event() implementations MUST be strictly synchronous, in-memory, and non-blocking.
        Observers MUST NOT perform remote HTTP, database, or blocking filesystem I/O directly
        in on_event(). Remote exporters must enqueue data into background batch queues.
    """

    def on_event(self, event: LifecycleEvent) -> None:
        ...


class LifecycleEventBus:
    """Thread-safe, decoupled event publisher with strict subscriber error isolation.

    Invariant 3 (Telemetry Independence):
        Observability must never control execution. If an observer raises an exception,
        the bus catches and logs it. It never propagates back to interrupt the agent run.
    """

    def __init__(self) -> None:
        self._observers: list[EventObserver] = []
        self._lock = threading.Lock()

    def subscribe(self, observer: EventObserver) -> None:
        """Register an observer under thread-safe lock."""
        with self._lock:
            if observer not in self._observers:
                self._observers.append(observer)

    def unsubscribe(self, observer: EventObserver) -> None:
        """Unregister an observer under thread-safe lock."""
        with self._lock:
            if observer in self._observers:
                self._observers.remove(observer)

    def publish(self, event: LifecycleEvent) -> None:
        """Dispatch event to a snapshot of observers outside lock with isolated failure containment."""
        with self._lock:
            snapshot = list(self._observers)

        for obs in snapshot:
            try:
                obs.on_event(event)
            except Exception as exc:
                logger.warning(
                    f"Lifecycle observer '{type(obs).__name__}' failed on event '{event.event_type.value}': {type(exc).__name__}",
                    exc_info=False,
                )
