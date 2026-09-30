"""Domain presentation models for the Agent Harness Operator Console."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from harness.agent.budget import ExecutionBudget
from harness.permissions.base import PermissionDecision, RiskLevel
from harness.tools.base import ToolSource


@dataclass
class ToolCallNode:
    """Represents an individual tool call within an agent run."""

    call_id: str
    run_id: str
    tool_name: str
    canonical_identity: str = ""
    tool_source: ToolSource = ToolSource.BUILTIN
    server_name: str | None = None
    is_mutating: bool = False
    arguments_summary: dict[str, str] = field(default_factory=dict)
    arguments_fingerprint: str = ""
    resource_descriptor: str = ""
    risk_level: RiskLevel | None = None
    matched_rule: str | None = None
    permission_decision: PermissionDecision | None = None
    confirmation_outcome: str | None = None  # "APPROVED", "REJECTED", None
    start_time: float = 0.0
    finish_time: float | None = None
    duration_seconds: float = 0.0
    status: str = "IN_FLIGHT"  # IN_FLIGHT, SUCCESS, ERROR, PERMISSION_DENIED
    is_error: bool = False
    error_message: str | None = None
    observation_length: int = 0
    observation_preview: str = ""


@dataclass
class RunNode:
    """Represents an agent execution run (root orchestrator or specialist child)."""

    run_id: str
    trace_id: str
    parent_run_id: str | None
    root_run_id: str
    agent_id: str
    agent_role: str
    is_root: bool
    status: str = "RUNNING"  # RUNNING, SUCCESS, FAILED
    start_time: float = 0.0
    finish_time: float | None = None
    duration_seconds: float = 0.0
    steps: int = 0
    tool_calls: int = 0
    termination_reason: str = ""
    error_message: str | None = None
    children_run_ids: list[str] = field(default_factory=list)
    tool_calls_map: dict[str, ToolCallNode] = field(default_factory=dict)
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    llm_calls_count: int = 0
    task_description: str = ""
    final_response: str | None = None


@dataclass(frozen=True)
class TimelineEvent:
    """Single flight-recorder entry for the live execution stream."""

    timestamp: float
    relative_seconds: float
    run_id: str
    agent_role: str
    category: str       # [W1][RUN], [W1][TOOL], [W2][MCP], [W2][MEMORY], [W3][DELEGATE], [W3][POLICY], [W3][CONFIRM]
    icon: str           # ◆, ✓, !, ✗, ?, ⮑, ⛯
    title: str
    details: str = ""
    style: str = "text" # CSS or Rich style tag


@dataclass
class AgentCard:
    """Discovered agent profile metadata."""

    role: str
    agent_type: str  # "Root Orchestrator" or "Specialist Sub-Agent"
    system_prompt: str
    allowed_tool_ids: tuple[str, ...]
    budget_ceiling: ExecutionBudget
    memory_access: str
    model_id: str | None = None
    delegations_count: int = 0
    total_duration_seconds: float = 0.0


@dataclass
class ToolCard:
    """Discovered tool metadata with dynamic invocation tracking."""

    name: str
    source: ToolSource
    canonical_identity: str
    server_name: str | None = None
    is_mutating: bool = False
    description: str = ""
    parameters_schema: dict[str, Any] = field(default_factory=dict)
    call_count: int = 0
    success_count: int = 0
    error_count: int = 0
    last_risk_level: str | None = None
    last_decision: str | None = None
    last_matched_rule: str | None = None


@dataclass
class MCPServerCard:
    """Discovered MCP server status and resilience state."""

    server_name: str
    transport_type: str
    endpoint: str = ""
    tools_count: int = 0
    tools_list: list[str] = field(default_factory=list)
    circuit_state: str = "CLOSED"  # CLOSED, HALF_OPEN, OPEN
    consecutive_failures: int = 0
    failure_threshold: int = 3
    cooldown_seconds: float = 30.0
    last_failure_timestamp: float = 0.0
    retries_count: int = 0
    is_connected: bool = False


@dataclass(frozen=True)
class GovernanceRecord:
    """Contextual authorization decision record."""

    timestamp: float
    relative_seconds: float
    run_id: str
    call_id: str
    tool_name: str
    resource_descriptor: str
    risk_level: RiskLevel
    matched_rule: str | None
    decision: PermissionDecision
    confirmation_outcome: str | None = None  # APPROVED, REJECTED, None
    is_boundary_violation: bool = False

    @property
    def canonical_tool_identity(self) -> str:
        return self.resource_descriptor
