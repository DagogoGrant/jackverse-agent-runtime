"""Deterministic memory retrieval for Phase 3 Persistent Long-Term Memory."""

from __future__ import annotations

from datetime import datetime, timezone
import re

from harness.memory.base import MemoryEntry, MemoryStatus, MemoryStore, MemoryType
from harness.memory.strategies import LexicalOverlapStrategy, RetrievalStrategy, ScoredMemory


class MemoryRetriever:
    """Retrieves relevant memories using a pluggable retrieval strategy and lifecycle gating."""

    def __init__(
        self,
        store: MemoryStore,
        max_retrieved: int = 3,
        max_context_chars: int = 2000,
        strategy: RetrievalStrategy | None = None,
        candidate_limit: int | None = 500,
    ) -> None:
        self.store = store
        self.max_retrieved = max_retrieved
        self.max_context_chars = max_context_chars
        self.strategy: RetrievalStrategy = (
            strategy if strategy is not None else LexicalOverlapStrategy()
        )
        self.candidate_limit = candidate_limit

    @staticmethod
    def _tokenize(text: str) -> set[str]:
        """Extract alphanumeric word tokens in lowercase (preserved for backward compatibility)."""
        return set(re.findall(r"\w+", text.lower()))

    def retrieve(
        self,
        query: str,
        limit: int | None = None,
        now: datetime | str | None = None,
    ) -> list[MemoryEntry]:
        """Retrieve top relevant memories matching the query.

        Defense-in-depth architecture:
          1. Store fetch: list_all(limit=500)
          2. Lifecycle filter (pre-strategy): DECLARATIVE + ACCEPTED + NOT EXPIRED + NOT SUPERSEDED
          3. Strategy ranking: strategy.rank(query, eligible, limit)
          4. Authorization check (post-strategy): guarantees no unauthorized entries enter context
          5. Context budgeting: enforces max_retrieved and max_context_chars
        """
        if not query or not query.strip():
            return []

        # Parse reference query timestamp for staleness filtering
        if now is None:
            now_dt = datetime.now(timezone.utc)
        elif isinstance(now, str):
            now_dt = datetime.fromisoformat(now.replace("Z", "+00:00"))
        elif isinstance(now, datetime):
            now_dt = now
        else:
            now_dt = datetime.now(timezone.utc)

        if now_dt.tzinfo is None:
            now_dt = now_dt.replace(tzinfo=timezone.utc)

        all_candidates = self.store.list_all(limit=self.candidate_limit)
        eligible: list[MemoryEntry] = []
        for e in all_candidates:
            # 0. Memory type check: declarative retrieval excludes procedural lessons
            if getattr(e, "memory_type", MemoryType.DECLARATIVE) != MemoryType.DECLARATIVE:
                continue

            # 1. Active status check: must be ACCEPTED (excludes quarantined, pending, superseded)
            if getattr(e, "status", None) not in (None, MemoryStatus.ACCEPTED):
                continue

            # 1b. Supersession check
            if getattr(e, "superseded_by", None) is not None:
                continue

            # 2. Staleness / expiry check:
            # Fresh iff query_now < expires_at. If query_now >= expires_at -> stale / excluded.
            if getattr(e, "expires_at", None):
                try:
                    exp_dt = datetime.fromisoformat(str(e.expires_at).replace("Z", "+00:00"))
                    if exp_dt.tzinfo is None:
                        exp_dt = exp_dt.replace(tzinfo=timezone.utc)
                    if now_dt >= exp_dt:
                        # Expired/stale
                        continue
                except (ValueError, TypeError):
                    # Malformed expiry excluded for safety
                    continue

            eligible.append(e)

        if not eligible:
            return []

        k = limit if limit is not None else self.max_retrieved
        if k <= 0:
            return []

        # 3. Strategy ranking proposes relevance
        scored_memories: list[ScoredMemory] = self.strategy.rank(
            query=query,
            candidates=eligible,
            limit=k,
        )

        # 4. Post-ranking authorization check: Defense-in-depth
        # Strategy proposes relevance; lifecycle governs eligibility and canonical content.
        eligible_by_id = {entry.id: entry for entry in eligible}
        authorized_ranked: list[MemoryEntry] = []
        for sm in scored_memories:
            canonical = eligible_by_id.get(sm.entry.id)
            if canonical is not None:
                # Bind strictly to the canonical stored instance, discarding any strategy-forged content
                authorized_ranked.append(canonical)

        # 5. Context budgeting
        selected: list[MemoryEntry] = []
        total_chars = 0

        for entry in authorized_ranked:
            if len(selected) >= k:
                break
            entry_len = len(entry.content)
            # Never truncate mid-content; skip entry if it would exceed remaining character budget
            if total_chars + entry_len > self.max_context_chars:
                continue

            selected.append(entry)
            total_chars += entry_len

        return selected
