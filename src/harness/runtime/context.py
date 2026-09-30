"""Execution context and ambient propagation primitives for runtime governance."""

from __future__ import annotations

from contextlib import contextmanager
import contextvars
from dataclasses import dataclass, field
import time
from typing import Generator
import uuid

_CURRENT_CONTEXT: contextvars.ContextVar[ExecutionContext | None] = contextvars.ContextVar(
    "current_execution_context", default=None
)


def get_current_context() -> ExecutionContext | None:
    """Retrieve the active ExecutionContext from the current contextvar frame.

    Note on concurrency & threading:
        contextvars automatically propagate across asyncio tasks within the same task tree.
        However, newly spawned OS threads (e.g. via threading.Thread) do NOT inherit ambient
        contextvars by default unless explicitly propagated using contextvars.copy_context().run(...).
    """
    return _CURRENT_CONTEXT.get()


@contextmanager
def execution_context_scope(ctx: ExecutionContext) -> Generator[ExecutionContext, None, None]:
    """Context manager setting active ExecutionContext and restoring previous token on exit."""
    token = _CURRENT_CONTEXT.set(ctx)
    try:
        yield ctx
    finally:
        _CURRENT_CONTEXT.reset(token)


_CURRENT_CALL_ID: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "current_call_id", default=None
)


def get_current_call_id() -> str | None:
    """Retrieve the active tool call ID from the current contextvar frame."""
    return _CURRENT_CALL_ID.get()


@contextmanager
def call_id_scope(call_id: str | None) -> Generator[str | None, None, None]:
    """Context manager setting active tool call ID and restoring previous token on exit."""
    token = _CURRENT_CALL_ID.set(call_id)
    try:
        yield call_id
    finally:
        _CURRENT_CALL_ID.reset(token)


from harness.agent.budget import ExecutionBudget



@dataclass(frozen=True)
class ExecutionContext:
    """Frozen execution-identity snapshot defining correlation, identity, and budget limits.

    Terminology & Structure:
        - trace_id: 128-bit trace-shaped correlation ID (32 hex characters) shared across
          root and all descendant sub-agents. Maps directly to future OpenTelemetry Trace IDs.
        - run_id: Application-level Agent Run ID (32 hex characters) identifying this discrete
          controller turn. (Distinct from OpenTelemetry Span IDs, which will be managed in W3.3).
        - root_run_id: Agent Run ID of the root orchestrator.
        - parent_run_id: Agent Run ID of the direct parent agent (None for root).
        - delegation_depth: Nesting level (0 for root orchestrator, 1+ for sub-agents).
        - agent_id: Identifier of the agent instance (e.g. 'orchestrator-main').
        - agent_role: Logical role (e.g. 'orchestrator', 'transport_specialist').
        - budget_limits: Static immutable resource ceiling assigned to this agent run.
          Transitive Immutability Guarantee:
              ExecutionContext is deeply, transitively immutable. All top-level fields
              are immutable value types (str, int, float, None), and budget_limits is
              an instance of ExecutionBudget, which is itself a frozen dataclass containing
              strictly immutable primitive bounds. Attempting to mutate ctx.budget_limits.max_steps
              raises dataclasses.FrozenInstanceError.
          (Note: Dynamic root-wide consumption tracking across descendants will be layered
          in W3.9 via a shared BudgetLedger without altering single-agent limits).
    """

    trace_id: str
    run_id: str
    root_run_id: str
    parent_run_id: str | None
    delegation_depth: int
    agent_id: str
    agent_role: str
    budget_limits: ExecutionBudget
    created_at: float = field(default_factory=time.time)
    budget_ledger: Any | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.trace_id, str) or not self.trace_id.strip():
            raise ValueError("trace_id must be a non-empty string.")
        if not isinstance(self.run_id, str) or not self.run_id.strip():
            raise ValueError("run_id must be a non-empty string.")
        if not isinstance(self.root_run_id, str) or not self.root_run_id.strip():
            raise ValueError("root_run_id must be a non-empty string.")
        if self.parent_run_id is not None and (not isinstance(self.parent_run_id, str) or not self.parent_run_id.strip()):
            raise ValueError("parent_run_id must be None or a non-empty string.")
        if self.delegation_depth < 0:
            raise ValueError("delegation_depth must be non-negative (>= 0).")
        if not isinstance(self.agent_id, str) or not self.agent_id.strip():
            raise ValueError("agent_id must be a non-empty string.")
        if not isinstance(self.agent_role, str) or not self.agent_role.strip():
            raise ValueError("agent_role must be a non-empty string.")
        if not isinstance(self.budget_limits, ExecutionBudget):
            raise ValueError("budget_limits must be an ExecutionBudget instance.")

    @classmethod
    def create_root(
        cls,
        budget: ExecutionBudget,
        agent_id: str = "orchestrator-main",
        agent_role: str = "orchestrator",
        trace_id: str | None = None,
        run_id: str | None = None,
        budget_ledger: Any | None = None,
    ) -> ExecutionContext:
        """Construct a new root orchestrator execution context with full 128-bit UUID hex IDs."""
        t_id = uuid.uuid4().hex if trace_id is None else trace_id
        r_id = uuid.uuid4().hex if run_id is None else run_id
        return cls(
            trace_id=t_id,
            run_id=r_id,
            root_run_id=r_id,
            parent_run_id=None,
            delegation_depth=0,
            agent_id=agent_id,
            agent_role=agent_role,
            budget_limits=budget,
            budget_ledger=budget_ledger,
        )

    def fork_child(
        self,
        agent_id: str,
        agent_role: str,
        child_budget: ExecutionBudget | None = None,
        budget_ledger: Any | None = None,
    ) -> ExecutionContext:
        """Derive an immutable child execution context for sub-agent delegation."""
        return ExecutionContext(
            trace_id=self.trace_id,
            run_id=uuid.uuid4().hex,
            root_run_id=self.root_run_id,
            parent_run_id=self.run_id,
            delegation_depth=self.delegation_depth + 1,
            agent_id=agent_id,
            agent_role=agent_role,
            budget_limits=child_budget or self.budget_limits,
            budget_ledger=budget_ledger if budget_ledger is not None else self.budget_ledger,
        )


