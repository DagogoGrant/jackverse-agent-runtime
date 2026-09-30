"""Harness Agent Package."""

from typing import Any

from harness.agent.budget import ExecutionBudget

__all__ = [
    "AgentFactory",
    "AgentSpec",
    "BudgetExhaustedError",
    "DelegateTaskTool",
    "DelegationLimitExceededError",
    "DelegationRequest",
    "DelegationResult",
    "ExecutionBudget",
    "HierarchicalBudgetLedger",
    "MemoryAccessLevel",
    "ReActController",
    "RunResult",
    "SubAgentManager",
    "build_tool_catalog",
    "get_standard_specialist_specs",
]


def __getattr__(name: str) -> Any:
    if name in (
        "AgentFactory",
        "AgentSpec",
        "DelegateTaskTool",
        "DelegationRequest",
        "DelegationResult",
        "MemoryAccessLevel",
        "SubAgentManager",
        "build_tool_catalog",
        "get_standard_specialist_specs",
    ):
        from harness.agent import delegation

        return getattr(delegation, name)
    if name in ("BudgetExhaustedError", "DelegationLimitExceededError", "HierarchicalBudgetLedger"):
        from harness.agent import ledger

        return getattr(ledger, name)
    if name in ("ReActController", "RunResult"):
        from harness.agent import react

        return getattr(react, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

