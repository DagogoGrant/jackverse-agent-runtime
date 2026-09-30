"""Deterministic Memory Firewall for Phase 3B Memory Integrity."""

from __future__ import annotations

from datetime import datetime, timezone
import logging
import re
from typing import Any

from harness.memory.base import (
    AdmissionAction,
    AdmissionDecision,
    MemorySource,
    MemoryStore,
    normalize_content,
)

logger = logging.getLogger("harness.memory.firewall")

# Imperative directive override patterns (applied strictly to untrusted TOOL_OBSERVATION)
DIRECTIVE_INSTRUCTION_PATTERNS = [
    # Imperative instruction override (e.g., "Ignore previous instructions", "Please disregard all prior rules")
    re.compile(
        r"(?i)(?:^|[.!?;\n\r]|:\s*)\s*(?:please\s+)?(?:ignore|disregard|forget)\s+(?:all\s+|any\s+)?(?:previous|prior|above)\s+(?:instructions|prompts|rules|commands|directives)\b"
    ),
    # Persona hijack (e.g., "You are now EVIL_BOT", "From now on act as", "New role: assistant")
    re.compile(
        r"(?i)(?:^|[.!?;\n\r]|:\s*)\s*(?:you\s+are\s+now|from\s+now\s+on\s+act\s+as|new\s+role:\s*)\s+([a-z0-9_-]+)"
    ),
    # Explicit system override marker
    re.compile(
        r"(?i)\b(?:system\s+override|admin\s+override|developer\s+mode\s+enabled)\b"
    ),
]

# Hidden infrastructure error signatures formatted as plain text
HIDDEN_ERROR_PATTERNS = [
    re.compile(r"(?i)\b(?:503\s+service\s+unavailable|502\s+bad\s+gateway|504\s+gateway\s+timeout)\b"),
    re.compile(r"(?i)\b(?:connection\s+refused|connection\s+timed\s+out)\b"),
    re.compile(r"(?i)\btraceback\s+\(most\s+recent\s+call\s+last\)"),
    re.compile(r"(?i)\b(?:fatal\s+error|internal\s+server\s+error)\b"),
]

# Transient live transport indicators (live timetables, platforms, delays, journey result blocks)
TRANSIENT_PATTERNS = [
    re.compile(r"(?i)\b(?:journey\s+\d+|departing\s+at\s+\d{1,2}:\d{2}|platform\s+\d+|delayed\s+by\s+\d+\s*min)\b"),
    re.compile(r"(?i)\b(?:transfers:\s*\d+|duration:\s*\d+\s*min|departure:\s*\d{4}-\d{2}-\d{2})\b"),
]


class MemoryFirewall:
    """Deterministic admission firewall evaluating memory integrity, provenance, and safety."""

    def __init__(
        self,
        store: MemoryStore,
        max_entry_chars: int = 4000,
        allow_time_bounded_transients: bool = False,
    ) -> None:
        self.store = store
        self.max_entry_chars = max_entry_chars
        self.allow_time_bounded_transients = allow_time_bounded_transients

    def evaluate(
        self,
        content: str,
        source: MemorySource,
        metadata: dict[str, Any] | None = None,
    ) -> AdmissionDecision:
        """Evaluate a memory candidate through the 4-control deterministic firewall."""
        meta = dict(metadata or {})

        # Control 1: Basic sanitation & bounds
        if not content or not content.strip():
            return AdmissionDecision(
                action=AdmissionAction.REJECT,
                reason="Empty or whitespace-only content rejected.",
            )

        stripped = content.strip()
        if len(stripped) > self.max_entry_chars:
            return AdmissionDecision(
                action=AdmissionAction.REJECT,
                reason=f"Content length ({len(stripped)}) exceeds maximum limit ({self.max_entry_chars}).",
            )

        # Provenance validation: verify known source
        if source not in (MemorySource.USER_INPUT, MemorySource.TOOL_OBSERVATION):
            return AdmissionDecision(
                action=AdmissionAction.REJECT,
                reason=f"Unsupported memory source: {source}",
            )

        # Attach verifiable provenance tier
        trust_tier = (
            "TIER_USER_EXPLICIT"
            if source == MemorySource.USER_INPUT
            else "TIER_TOOL_EXTERNAL"
        )
        meta["trust_tier"] = trust_tier
        meta["observed_at"] = meta.get("observed_at") or datetime.now(timezone.utc).isoformat()

        # Control 2 & 3: TOOL_OBSERVATION checks (errors, transient schedules, instruction patterns)
        if source == MemorySource.TOOL_OBSERVATION:
            # 2a. Explicit tool execution error
            if meta.get("is_error") is True:
                return AdmissionDecision(
                    action=AdmissionAction.REJECT,
                    reason="Tool execution error rejected from long-term memory.",
                    metadata=meta,
                )

            # 2b. Hidden infrastructure error pattern in output text
            for pat in HIDDEN_ERROR_PATTERNS:
                if pat.search(stripped):
                    return AdmissionDecision(
                        action=AdmissionAction.REJECT,
                        reason="Hidden infrastructure error pattern detected in tool observation.",
                        metadata=meta,
                    )

            is_transient_meta = (
                meta.get("is_transient") is True
                or meta.get("tool_name") == "find_connection"
            )
            is_transient_pattern = any(pat.search(stripped) for pat in TRANSIENT_PATTERNS)
            is_transient = is_transient_meta or is_transient_pattern

            if is_transient:
                if not self.allow_time_bounded_transients:
                    reason = (
                        "Transient time-sensitive transport observation rejected from persistent memory."
                        if is_transient_meta
                        else "Time-sensitive transport schedule detected and rejected from persistent memory."
                    )
                    return AdmissionDecision(
                        action=AdmissionAction.REJECT,
                        reason=reason,
                        metadata=meta,
                    )

                # Variant C: Explicit lifecycle treatment policy
                # transient observation + no expires_at -> REJECT
                # transient observation + malformed expires_at -> REJECT
                # transient observation + expires_at <= observed_at -> REJECT
                # transient observation + valid explicit future expires_at -> eligible for ACCEPT
                expires_at_val = meta.get("expires_at")
                if not expires_at_val:
                    return AdmissionDecision(
                        action=AdmissionAction.REJECT,
                        reason="Transient observation without explicit expiration rejected.",
                        metadata=meta,
                    )

                try:
                    exp_dt = datetime.fromisoformat(str(expires_at_val).replace("Z", "+00:00"))
                    obs_dt_val = meta.get("observed_at")
                    if obs_dt_val:
                        obs_dt = datetime.fromisoformat(str(obs_dt_val).replace("Z", "+00:00"))
                    else:
                        obs_dt = datetime.now(timezone.utc)
                    if exp_dt.tzinfo is None:
                        exp_dt = exp_dt.replace(tzinfo=timezone.utc)
                    if obs_dt.tzinfo is None:
                        obs_dt = obs_dt.replace(tzinfo=timezone.utc)
                except (ValueError, TypeError):
                    return AdmissionDecision(
                        action=AdmissionAction.REJECT,
                        reason="Malformed expires_at timestamp rejected.",
                        metadata=meta,
                    )

                if exp_dt <= obs_dt:
                    return AdmissionDecision(
                        action=AdmissionAction.REJECT,
                        reason="Expired or non-future expires_at timestamp rejected.",
                        metadata=meta,
                    )

                meta["is_time_bounded"] = True

            # Control 3: Obvious instruction-pattern quarantine (applied strictly to untrusted external tool data)
            for pat in DIRECTIVE_INSTRUCTION_PATTERNS:
                if pat.search(stripped):
                    logger.warning(f"Obvious instruction pattern quarantined in tool observation: {stripped[:80]}")
                    meta["quarantine_reason"] = "OBVIOUS_INSTRUCTION_PATTERN"
                    return AdmissionDecision(
                        action=AdmissionAction.QUARANTINE,
                        reason="Obvious instruction directive pattern detected in external tool observation.",
                        metadata=meta,
                    )

        # Control 4: Normalized duplicate control (applies to both USER_INPUT and TOOL_OBSERVATION)
        # Check exact duplicate first
        if self.store.find_exact_content(stripped) is not None:
            return AdmissionDecision(
                action=AdmissionAction.REJECT,
                reason="Exact duplicate content already exists in memory store.",
                metadata=meta,
            )

        # Check normalized duplicate
        norm = normalize_content(stripped)
        if hasattr(self.store, "find_normalized_content"):
            if self.store.find_normalized_content(norm) is not None:
                return AdmissionDecision(
                    action=AdmissionAction.REJECT,
                    reason="Normalized duplicate content already exists in memory store.",
                    metadata=meta,
                )

        # All controls passed: ACCEPT
        return AdmissionDecision(
            action=AdmissionAction.ACCEPT,
            reason="Candidate memory verified and accepted by firewall.",
            metadata=meta,
        )
