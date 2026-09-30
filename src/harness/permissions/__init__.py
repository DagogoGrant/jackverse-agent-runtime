"""Permissions and action authorization subsystem."""

from harness.permissions.base import (
    ALLOWLISTED_ARGUMENT_KEYS,
    AuthorizationResult,
    ConfirmationGrant,
    ConfirmationHandler,
    ConfirmationRecord,
    FILESYSTEM_TOOLS,
    PermissionDecision,
    PermissionRequest,
    RiskLevel,
    TOOL_SPECIFIC_SUMMARY_ALLOWLIST,
    canonical_tool_identity,
    compute_arguments_fingerprint,
    summarize_arguments,
)
from harness.permissions.confirmation import (
    ConsoleConfirmationHandler,
    DeterministicConfirmationHandler,
)
from harness.permissions.manager import PermissionManager
from harness.permissions.policy import PolicyEngine, PolicyRule

__all__ = [
    "ALLOWLISTED_ARGUMENT_KEYS",
    "AuthorizationResult",
    "ConfirmationGrant",
    "ConfirmationHandler",
    "ConfirmationRecord",
    "ConsoleConfirmationHandler",
    "DeterministicConfirmationHandler",
    "FILESYSTEM_TOOLS",
    "PermissionDecision",
    "PermissionManager",
    "PermissionRequest",
    "PolicyEngine",
    "PolicyRule",
    "RiskLevel",
    "TOOL_SPECIFIC_SUMMARY_ALLOWLIST",
    "canonical_tool_identity",
    "compute_arguments_fingerprint",
    "summarize_arguments",
]
