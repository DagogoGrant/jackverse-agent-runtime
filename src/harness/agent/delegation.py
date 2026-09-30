"""Sub-agent delegation and multi-principal architecture for runtime governance."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from enum import Enum
import logging
import time
from typing import Any
import uuid

from harness.agent.budget import ExecutionBudget
from harness.agent.ledger import (
    BudgetExhaustedError,
    DelegationLimitExceededError,
    HierarchicalBudgetLedger,
)
from harness.agent.react import ReActController, RunResult
from harness.permissions.base import canonical_tool_identity
from harness.permissions.manager import PermissionManager
from harness.runtime.context import (
    ExecutionContext,
    call_id_scope,
    get_current_call_id,
    get_current_context,
)
from harness.runtime.events import (
    DelegationFinishedEvent,
    DelegationStartedEvent,
    FailureCategory,
    LifecycleEventBus,
)
from harness.tools.base import ErrorCode, Tool, ToolResult, ToolSource, ToolSpec
from harness.tools.executor import ToolExecutor
from harness.tools.registry import ToolRegistry

logger = logging.getLogger("harness.agent.delegation")


class MemoryAccessLevel(str, Enum):
    """Memory access tier for an agent profile."""

    NONE = "none"
    READ_ONLY = "read_only"
    READ_WRITE = "read_write"


@dataclass(frozen=True)
class AgentSpec:
    """Immutable specification for an agent role/profile."""

    role: str
    system_prompt: str
    allowed_tool_ids: tuple[str, ...]  # Canonical tool identities
    budget_ceiling: ExecutionBudget = field(
        default_factory=lambda: ExecutionBudget(max_steps=5, max_tool_calls=5, max_runtime_seconds=300.0)
    )
    memory_access: MemoryAccessLevel = MemoryAccessLevel.NONE
    model_id: str | None = None


@dataclass(frozen=True)
class DelegationRequest:
    """Domain request to delegate a subtask."""

    specialist_role: str
    task: str


@dataclass(frozen=True)
class DelegationResult:
    """Structured outcome of a sub-agent delegation."""

    success: bool
    output: str | None = None
    error: str | None = None
    steps: int = 0
    tool_calls: int = 0
    duration_seconds: float = 0.0
    failure_category: FailureCategory | None = None


def build_tool_catalog(tools: Sequence[Tool]) -> dict[str, Tool]:
    """Index tools by their canonical tool identity string."""
    catalog: dict[str, Tool] = {}
    for tool in tools:
        canon_id = canonical_tool_identity(
            tool.spec.source,
            tool.spec.server_name,
            tool.spec.name,
        )
        catalog[canon_id] = tool
        # Also index by simple name if unambiguous, as convenient fallback
        if tool.spec.name not in catalog:
            catalog[tool.spec.name] = tool
    return catalog


class AgentFactory:
    """Constructs isolated ReActController instances for authorized agent roles."""

    def __init__(
        self,
        specs: Mapping[str, AgentSpec],
        tool_catalog: Mapping[str, Tool],
        llm_client: Any,
        permission_manager: PermissionManager,
        event_bus: LifecycleEventBus | None = None,
    ) -> None:
        self.specs = dict(specs)
        self.tool_catalog = dict(tool_catalog)
        self.llm_client = llm_client
        self.permission_manager = permission_manager
        self.event_bus = event_bus

    def create_agent(
        self,
        role: str,
        child_ctx: ExecutionContext,
        budget: ExecutionBudget,
    ) -> ReActController:
        """Instantiate a fresh controller and isolated registry/executor for the requested role."""
        if role not in self.specs:
            raise ValueError(f"Unknown agent role '{role}'. Available roles: {list(self.specs.keys())}")

        spec = self.specs[role]

        # Build isolated tool registry from canonical catalog
        registry = ToolRegistry()
        for canon_id in spec.allowed_tool_ids:
            tool = self.tool_catalog.get(canon_id)
            if tool is not None:
                registry.register(tool)
            else:
                logger.warning(
                    f"Tool with canonical identity '{canon_id}' not found in catalog for role '{role}'"
                )

        # Child gets fresh ToolExecutor sharing permission_manager and event_bus
        executor = ToolExecutor(
            max_observation_chars=budget.max_observation_chars,
            event_bus=self.event_bus,
            permission_manager=self.permission_manager,
        )

        # Conservative MemoryAccessLevel.NONE for children per Architecture Review G
        memory_manager = None
        enable_procedural_memory = False

        return ReActController(
            llm_client=self.llm_client,
            tool_registry=registry,
            tool_executor=executor,
            budget=budget,
            system_prompt=spec.system_prompt,
            memory_manager=memory_manager,
            enable_procedural_memory=enable_procedural_memory,
            event_bus=self.event_bus,
            agent_id=child_ctx.agent_id,
            agent_role=child_ctx.agent_role,
            budget_ledger=None,  # Consumption managed and reconciled by SubAgentManager
        )


class SubAgentManager:
    """Manages lifecycle, budget allocation, context propagation, and failure isolation for sub-agent delegation."""

    def __init__(
        self,
        agent_factory: AgentFactory,
        budget_ledger: HierarchicalBudgetLedger | None = None,
        event_bus: LifecycleEventBus | None = None,
    ) -> None:
        self.agent_factory = agent_factory
        self.budget_ledger = budget_ledger
        self.event_bus = event_bus

    def delegate(
        self,
        parent_ctx: ExecutionContext,
        request: DelegationRequest,
    ) -> DelegationResult:
        """Execute delegation turn under hierarchical governance."""
        start_time = time.perf_counter()
        role = request.specialist_role
        parent_call_id = get_current_call_id() or ""
        delegation_id = uuid.uuid4().hex

        # 0. Unknown specialist role validation
        if role not in self.agent_factory.specs:
            duration = time.perf_counter() - start_time
            error_msg = f"Specialist '{role}' not found. Available specialists: {list(self.agent_factory.specs.keys())}"
            if self.event_bus:
                self.event_bus.publish(
                    DelegationFinishedEvent(
                        timestamp=time.time(),
                        trace_id=parent_ctx.trace_id,
                        run_id=parent_ctx.run_id,
                        root_run_id=parent_ctx.root_run_id,
                        parent_run_id=parent_ctx.parent_run_id,
                        agent_id=parent_ctx.agent_id,
                        agent_role=parent_ctx.agent_role,
                        delegation_id=delegation_id,
                        parent_call_id=parent_call_id,
                        child_run_id="",
                        parent_agent_id=parent_ctx.agent_id,
                        parent_agent_role=parent_ctx.agent_role,
                        child_agent_id="",
                        child_agent_role=role,
                        delegation_depth=parent_ctx.delegation_depth + 1,
                        duration_seconds=round(duration, 6),
                        status="rejected",
                        steps=0,
                        tool_calls=0,
                        failure_category=FailureCategory.VALIDATION,
                    )
                )
            return DelegationResult(
                success=False,
                error=error_msg,
                failure_category=FailureCategory.VALIDATION,
            )

        spec = self.agent_factory.specs[role]
        ledger = parent_ctx.budget_ledger or self.budget_ledger
        if ledger is None:
            ledger = HierarchicalBudgetLedger(root_budget=parent_ctx.budget_limits)

        # 1. Allocate sliced budget from root ledger
        try:
            child_budget = ledger.allocate_child_slice(
                requested_ceiling=spec.budget_ceiling,
                current_depth=parent_ctx.delegation_depth,
            )
        except (DelegationLimitExceededError, BudgetExhaustedError) as exc:
            duration = time.perf_counter() - start_time
            error_msg = f"Delegation rejected: {exc}"
            if self.event_bus:
                self.event_bus.publish(
                    DelegationFinishedEvent(
                        timestamp=time.time(),
                        trace_id=parent_ctx.trace_id,
                        run_id=parent_ctx.run_id,
                        root_run_id=parent_ctx.root_run_id,
                        parent_run_id=parent_ctx.parent_run_id,
                        agent_id=parent_ctx.agent_id,
                        agent_role=parent_ctx.agent_role,
                        delegation_id=delegation_id,
                        parent_call_id=parent_call_id,
                        child_run_id="",
                        parent_agent_id=parent_ctx.agent_id,
                        parent_agent_role=parent_ctx.agent_role,
                        child_agent_id="",
                        child_agent_role=role,
                        delegation_depth=parent_ctx.delegation_depth + 1,
                        duration_seconds=round(duration, 6),
                        status="rejected",
                        steps=0,
                        tool_calls=0,
                        failure_category=FailureCategory.BUDGET,
                    )
                )
            return DelegationResult(
                success=False,
                error=error_msg,
                failure_category=FailureCategory.BUDGET,
            )

        # 2. Fork immutable child execution context
        child_agent_id = f"{role}-{uuid.uuid4().hex[:8]}"
        child_ctx = parent_ctx.fork_child(
            agent_id=child_agent_id,
            agent_role=role,
            child_budget=child_budget,
            budget_ledger=ledger,
        )
        delegation_id = uuid.uuid4().hex

        # 3. Publish DelegationStartedEvent (Strict privacy: zero task text, zero hash)
        if self.event_bus:
            self.event_bus.publish(
                DelegationStartedEvent(
                    timestamp=time.time(),
                    trace_id=parent_ctx.trace_id,
                    run_id=parent_ctx.run_id,
                    root_run_id=parent_ctx.root_run_id,
                    parent_run_id=parent_ctx.parent_run_id,
                    agent_id=parent_ctx.agent_id,
                    agent_role=parent_ctx.agent_role,
                    delegation_id=delegation_id,
                    parent_call_id=parent_call_id,
                    child_run_id=child_ctx.run_id,
                    parent_agent_id=parent_ctx.agent_id,
                    parent_agent_role=parent_ctx.agent_role,
                    child_agent_id=child_ctx.agent_id,
                    child_agent_role=role,
                    delegation_depth=child_ctx.delegation_depth,
                    task_length=len(request.task),
                )
            )

        # 4. Instantiate child controller
        child_controller = self.agent_factory.create_agent(
            role=role,
            child_ctx=child_ctx,
            budget=child_budget,
        )

        start_time = time.perf_counter()
        run_res: RunResult | None = None
        error_msg: str | None = None
        success = False
        failure_cat: FailureCategory | None = None

        try:
            # ContextVar isolation: reset call_id so child does NOT inherit parent's call ID
            with call_id_scope(None):
                run_res = child_controller.run_turn(request.task, context=child_ctx)

            success = run_res.is_success
            if not success:
                error_msg = run_res.error or f"Child agent execution failed: {run_res.termination_reason.value}"
                failure_cat = FailureCategory.TOOL if "tool" in (error_msg or "").lower() else FailureCategory.INTERNAL
        except Exception as exc:
            logger.exception(f"Unexpected exception during sub-agent delegation: {exc}")
            error_msg = f"Child execution exception: {type(exc).__name__}"
            failure_cat = FailureCategory.INTERNAL
            success = False
        finally:
            duration = time.perf_counter() - start_time
            actual_steps = run_res.steps if run_res is not None else 0
            actual_tool_calls = run_res.tool_calls if run_res is not None else 0

            # Reconcile consumed budget back into the shared ledger
            ledger.reconcile_child_consumption(
                actual_steps=actual_steps,
                actual_tool_calls=actual_tool_calls,
            )

            # Publish DelegationFinishedEvent (Strict privacy: zero task text, zero hash)
            if self.event_bus:
                self.event_bus.publish(
                    DelegationFinishedEvent(
                        timestamp=time.time(),
                        trace_id=parent_ctx.trace_id,
                        run_id=parent_ctx.run_id,
                        root_run_id=parent_ctx.root_run_id,
                        parent_run_id=parent_ctx.parent_run_id,
                        agent_id=parent_ctx.agent_id,
                        agent_role=parent_ctx.agent_role,
                        delegation_id=delegation_id,
                        parent_call_id=parent_call_id,
                        child_run_id=child_ctx.run_id,
                        parent_agent_id=parent_ctx.agent_id,
                        parent_agent_role=parent_ctx.agent_role,
                        child_agent_id=child_ctx.agent_id,
                        child_agent_role=role,
                        delegation_depth=child_ctx.delegation_depth,
                        duration_seconds=round(duration, 6),
                        status="success" if success else "error",
                        steps=actual_steps,
                        tool_calls=actual_tool_calls,
                        failure_category=failure_cat,
                    )
                )

        return DelegationResult(
            success=success,
            output=run_res.final_text if run_res is not None else None,
            error=error_msg,
            steps=actual_steps,
            tool_calls=actual_tool_calls,
            duration_seconds=round(duration, 4),
            failure_category=failure_cat,
        )


class DelegateTaskTool:
    """Tool enabling an authorized agent to delegate specific subtasks to specialists."""

    def __init__(self, manager: SubAgentManager) -> None:
        self.manager = manager
        available_specialists = list(manager.agent_factory.specs.keys())
        schema_specialists: dict[str, Any] = {"type": "string", "description": "The role of the specialist to delegate to."}
        if available_specialists:
            schema_specialists["enum"] = available_specialists

        self._spec = ToolSpec(
            name="delegate_task",
            description=(
                "Delegate a specific subtask to an authorized specialist agent. "
                f"Available specialists: {', '.join(available_specialists) if available_specialists else 'none'}."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "specialist": schema_specialists,
                    "task": {
                        "type": "string",
                        "description": "Clear, specific instructions describing the subtask to execute.",
                    },
                },
                "required": ["specialist", "task"],
                "additionalProperties": False,
            },
            is_mutating=False,
            source=ToolSource.BUILTIN,
            server_name="delegation",
        )

    @property
    def spec(self) -> ToolSpec:
        return self._spec

    def execute(self, arguments: Mapping[str, object]) -> ToolResult:
        specialist = str(arguments.get("specialist", "")).strip()
        task = str(arguments.get("task", "")).strip()

        ctx = get_current_context()
        if ctx is None:
            return ToolResult(
                content="Execution error: No active ExecutionContext for delegation.",
                is_error=True,
                error_code=ErrorCode.INTERNAL_ERROR,
            )

        res = self.manager.delegate(
            parent_ctx=ctx,
            request=DelegationRequest(specialist_role=specialist, task=task),
        )

        if res.success:
            return ToolResult(
                content=res.output or "Specialist completed task successfully with no text output.",
                is_error=False,
            )
        else:
            return ToolResult(
                content=f"Delegation failed: {res.error}",
                is_error=True,
                error_code=ErrorCode.INTERNAL_ERROR,
            )


def get_standard_specialist_specs() -> dict[str, AgentSpec]:
    """Define default specialist profiles for W3.8 multi-principal architecture."""
    return {
        "transport_specialist": AgentSpec(
            role="transport_specialist",
            system_prompt=(
                "You are a specialized public transport assistant. "
                "Your role is to look up train and route connections using find_connection. "
                "Answer user queries concisely with accurate departure/arrival details."
            ),
            allowed_tool_ids=(
                "mcp:transport_service:find_connection",
                "mcp:transport_service:get_station_info",
            ),
            budget_ceiling=ExecutionBudget(max_steps=5, max_tool_calls=5, max_runtime_seconds=300.0),
            memory_access=MemoryAccessLevel.NONE,
        ),
        "workspace_analyst": AgentSpec(
            role="workspace_analyst",
            system_prompt=(
                "You are a read-only workspace analyst. "
                "Your role is to analyze files, directories, and code structure. "
                "You do not have permission to modify files."
            ),
            allowed_tool_ids=(
                "builtin:filesystem:read_file",
                "builtin:filesystem:list_directory",
                "builtin:filesystem:search_files",
            ),
            budget_ceiling=ExecutionBudget(max_steps=5, max_tool_calls=5, max_runtime_seconds=300.0),
            memory_access=MemoryAccessLevel.NONE,
        ),
    }
