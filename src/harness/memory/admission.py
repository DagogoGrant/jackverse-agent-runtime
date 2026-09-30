"""Baseline deterministic admission policy for Phase 3A Persistent Long-Term Memory."""

from __future__ import annotations

from typing import Any

from harness.memory.base import AdmissionDecision, MemorySource, MemoryStore


class BaselineAdmissionPolicy:
    """Deterministic, rule-based admission policy for persistent long-term memory."""

    def __init__(
        self,
        store: MemoryStore,
        max_entry_chars: int = 4000,
    ) -> None:
        self.store = store
        self.max_entry_chars = max_entry_chars

    def evaluate(
        self,
        content: str,
        source: MemorySource,
        metadata: dict[str, Any] | None = None,
    ) -> AdmissionDecision:
        """Evaluate whether a candidate interaction should be admitted to persistent storage."""
        meta = metadata or {}

        # 1. Blank / whitespace rejection
        if not content or not content.strip():
            return AdmissionDecision(
                admitted=False,
                reason="Empty or whitespace-only content rejected.",
            )

        stripped = content.strip()

        # 2. Maximum entry size bound
        if len(stripped) > self.max_entry_chars:
            return AdmissionDecision(
                admitted=False,
                reason=f"Content length ({len(stripped)}) exceeds maximum limit ({self.max_entry_chars}).",
            )

        # 3. Source-specific admission criteria
        if source == MemorySource.USER_INPUT:
            # Check for exact duplicate in store
            if self.store.find_exact_content(stripped) is not None:
                return AdmissionDecision(
                    admitted=False,
                    reason="Exact duplicate content already exists in memory store.",
                )
            return AdmissionDecision(
                admitted=True,
                reason="Valid user input admitted.",
            )

        elif source == MemorySource.TOOL_OBSERVATION:
            # In Phase 3A: Reject all tool errors from long-term memory
            if meta.get("is_error") is True:
                return AdmissionDecision(
                    admitted=False,
                    reason="Tool execution error rejected from long-term memory.",
                )

            # Check for exact duplicate in store
            if self.store.find_exact_content(stripped) is not None:
                return AdmissionDecision(
                    admitted=False,
                    reason="Exact duplicate content already exists in memory store.",
                )

            return AdmissionDecision(
                admitted=True,
                reason="Valid tool observation admitted.",
            )

        return AdmissionDecision(
            admitted=False,
            reason=f"Unsupported memory source: {source}",
        )
