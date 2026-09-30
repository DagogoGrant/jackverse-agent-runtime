"""Base types, data structures, and protocol interfaces for the permissions and action authorization subsystem."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import Enum
import hashlib
import json
from typing import Any, Protocol

from harness.tools.base import ToolSource

# Standard filesystem tool names for canonical naming
FILESYSTEM_TOOLS = frozenset(
    {
        "read_file",
        "modify_file",
        "create_file",
        "create_directory",
        "list_directory",
        "search_files",
    }
)

# Tool-specific safe argument keys allowed in user-facing confirmation summaries
TOOL_SPECIFIC_SUMMARY_ALLOWLIST: Mapping[str, frozenset[str]] = {
    "create_file": frozenset({"path"}),
    "modify_file": frozenset({"path"}),
    "create_directory": frozenset({"path"}),
    "read_file": frozenset({"path"}),
    "list_directory": frozenset({"path"}),
    "find_connection": frozenset({"origin", "destination", "departure_time"}),
    "get_station_info": frozenset({"station", "station_id"}),
    "delegate_task": frozenset({"specialist"}),
}

ALLOWLISTED_ARGUMENT_KEYS = frozenset(
    {
        "path",
        "origin",
        "destination",
        "departure_time",
        "station",
        "station_id",
    }
)


class PermissionDecision(str, Enum):
    """Action authorization decision outcomes."""

    ALLOW = "allow"
    DENY = "deny"
    REQUIRE_CONFIRMATION = "require_confirmation"


class RiskLevel(str, Enum):
    """Categorization of tool action risk."""

    READ_ONLY = "read_only"
    MUTATING = "mutating"
    NETWORK = "network"
    ADMIN = "admin"
    SENSITIVE = "sensitive"
    CRITICAL = "critical"


def canonical_tool_identity(
    tool_source: ToolSource,
    server_name: str | None,
    tool_name: str,
) -> str:
    """Generate the canonical tool identity string: <source>:<server_or_subsystem>:<tool_name>."""
    if tool_source == ToolSource.BUILTIN:
        subsystem = server_name or ("filesystem" if tool_name in FILESYSTEM_TOOLS else "builtin")
        return f"builtin:{subsystem}:{tool_name}"
    else:
        server = server_name or "mcp"
        return f"mcp:{server}:{tool_name}"


def compute_arguments_fingerprint(arguments: Mapping[str, Any]) -> str:
    """Compute a deterministic SHA-256 digest over the canonically sorted JSON representation of arguments."""
    json_bytes = json.dumps(
        arguments,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(json_bytes).hexdigest()


def summarize_arguments(
    tool_name: str,
    arguments: Mapping[str, Any],
) -> dict[str, str]:
    """Generate a privacy-safe, bounded summary of arguments without raw secrets or bulk payloads."""
    allowed_keys = TOOL_SPECIFIC_SUMMARY_ALLOWLIST.get(tool_name, frozenset())
    summary: dict[str, str] = {}
    for key in sorted(allowed_keys):
        if key in arguments:
            val = arguments[key]
            str_val = str(val)
            if len(str_val) > 120:
                summary[key] = str_val[:117] + "..."
            else:
                summary[key] = str_val
    return summary


@dataclass(frozen=True)
class PermissionRequest:
    """W3.8-ready authorization request capturing principal, tool identity, fingerprint, and target resource."""

    run_id: str
    agent_id: str
    agent_role: str
    delegation_depth: int
    call_id: str
    canonical_tool_identity: str
    tool_name: str
    tool_source: ToolSource
    server_name: str | None
    arguments: Mapping[str, Any]
    arguments_fingerprint: str
    arguments_summary: dict[str, str]
    resource_descriptor: str | None = None
    risk_level: RiskLevel | None = None


@dataclass
class ConfirmationRecord:
    """Server-side, single-use, digest-bound confirmation record providing anti-TOCTOU and anti-replay guarantees."""

    confirmation_id: str
    run_id: str
    call_id: str
    agent_id: str
    agent_role: str
    canonical_tool_identity: str
    arguments_fingerprint: str
    created_at: float
    consumed: bool = False


# Review E alias for ConfirmationRecord
ConfirmationGrant = ConfirmationRecord


@dataclass(frozen=True)
class AuthorizationResult:
    """Final outcome returned by PermissionManager to ToolExecutor."""

    decision: PermissionDecision
    matched_rule: str | None = None
    risk_level: RiskLevel = RiskLevel.MUTATING
    reason: str = ""
    confirmation_record: ConfirmationRecord | None = None


class ConfirmationHandler(Protocol):
    """Abstract interface for interactive or headless confirmation resolution."""

    def request_confirmation(self, request: PermissionRequest) -> bool:
        """Present request to the user or testing oracle and return approval status."""
        ...
