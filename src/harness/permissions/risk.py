"""Pure deterministic risk classification engine for action authorization requests."""

from __future__ import annotations

import fnmatch
import os
from typing import Any, Mapping

from harness.permissions.base import PermissionRequest, RiskLevel
from harness.tools.base import ToolSource

# Filename / path patterns considered inherently sensitive
SENSITIVE_PATTERNS: frozenset[str] = frozenset(
    {
        ".env*",
        "*.env*",
        "*.pem",
        "*.key",
        "*.pkcs*",
        "*.pfx",
        "*.p12",
        "*id_rsa*",
        "*id_ed25519*",
        "*id_ecdsa*",
        "*id_dsa*",
        "*secret*",
        "*credential*",
        "*token*",
        ".git*",
        "*.git*",
    }
)

# Critical system-level resource patterns
CRITICAL_PATTERNS: frozenset[str] = frozenset(
    {
        "*/etc/shadow*",
        "*/etc/passwd*",
        "*/etc/sudoers*",
        "/etc/shadow*",
        "/etc/passwd*",
        "/etc/sudoers*",
    }
)

# Standard builtin filesystem mutating tools
MUTATING_TOOLS: frozenset[str] = frozenset(
    {
        "create_file",
        "modify_file",
        "create_directory",
    }
)

# Standard builtin read-only tools
READ_ONLY_TOOLS: frozenset[str] = frozenset(
    {
        "read_file",
        "list_directory",
        "search_files",
        "delegate_task",
    }
)


class RiskClassifier:
    """Deterministic classifier evaluating authorization requests against risk levels."""

    @staticmethod
    def is_critical_resource(resource: str | None) -> bool:
        """Check if a path or descriptor references a critical system resource."""
        if not resource:
            return False
        normalized = resource.strip().lower()
        basename = os.path.basename(normalized)
        for pattern in CRITICAL_PATTERNS:
            pat_lower = pattern.lower()
            if (
                fnmatch.fnmatch(normalized, pat_lower)
                or fnmatch.fnmatch(basename, pat_lower)
                or fnmatch.fnmatch(normalized, f"*/{pat_lower.lstrip('/')}")
            ):
                return True
        return False

    @staticmethod
    def is_sensitive_resource(resource: str | None) -> bool:
        """Check if a path or descriptor references a sensitive credential or secret file."""
        if not resource:
            return False
        normalized = resource.strip().lower()
        basename = os.path.basename(normalized)
        for pattern in SENSITIVE_PATTERNS:
            pat_lower = pattern.lower()
            if (
                fnmatch.fnmatch(normalized, pat_lower)
                or fnmatch.fnmatch(basename, pat_lower)
                or fnmatch.fnmatch(normalized, f"*/{pat_lower.lstrip('/')}")
            ):
                return True
        return False

    @classmethod
    def _extract_path_candidate(cls, arguments: Mapping[str, Any]) -> str | None:
        for key in ("path", "target_file", "directory"):
            val = arguments.get(key)
            if isinstance(val, str) and val.strip():
                return val.strip()
        return None

    @classmethod
    def classify(cls, request: PermissionRequest) -> RiskLevel:
        """Deterministically determine the RiskLevel for a permission request."""
        # 1. If risk level was explicitly assigned, honor it
        if request.risk_level is not None:
            return request.risk_level

        # Check candidate resources (resource_descriptor or path arguments)
        path_candidate = cls._extract_path_candidate(request.arguments)
        candidates = [c for c in (request.resource_descriptor, path_candidate) if c]

        # 2. Check critical resources
        for cand in candidates:
            if cls.is_critical_resource(cand):
                return RiskLevel.CRITICAL

        # 3. Check sensitive resources
        for cand in candidates:
            if cls.is_sensitive_resource(cand):
                return RiskLevel.SENSITIVE

        # 4. Check tool source (MCP tools communicate over network)
        if request.tool_source == ToolSource.MCP:
            return RiskLevel.NETWORK

        # 5. Check builtin tool action types
        if request.tool_name in MUTATING_TOOLS:
            return RiskLevel.MUTATING

        if request.tool_name in READ_ONLY_TOOLS:
            return RiskLevel.READ_ONLY

        # Fallback default
        return RiskLevel.MUTATING
