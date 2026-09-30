"""Retrieval strategies for Phase 3 Hybrid Long-Term Memory Retrieval."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import math
import re
from typing import Any, Protocol, runtime_checkable

from harness.memory.base import MemoryEntry
from harness.memory.embeddings import EmbeddingProvider, memory_content_hash
from harness.memory.store import SQLiteMemoryStore


class MissingEmbeddingError(Exception):
    """Raised when dense retrieval encounters candidate memories lacking cached embeddings."""


class StaleEmbeddingError(MissingEmbeddingError):
    """Raised when dense retrieval encounters candidate memories whose cached embeddings are stale."""


@dataclass(frozen=True)
class ScoredMemory:
    """Universal contract for scored memory candidates.

    Attributes:
        entry: The retrieved MemoryEntry.
        score: Strategy-specific raw score (lexical overlap, BM25, cosine sim, or RRF).
        rank: 1-based integer rank (1 = best, universally across all strategies).
        strategy: Identifying name of the producing strategy.
    """

    entry: MemoryEntry
    score: float
    rank: int
    strategy: str


@runtime_checkable
class RetrievalStrategy(Protocol):
    """Protocol defining retrieval strategy ranking contract."""

    @property
    def name(self) -> str:
        """Identifying name of the retrieval strategy."""
        ...

    def rank(
        self,
        query: str,
        candidates: list[MemoryEntry],
        limit: int,
    ) -> list[ScoredMemory]:
        """Rank eligible candidate memories for a query.

        Returns a list of ScoredMemory objects ordered best-first, with rank=1, 2, 3...
        """
        ...


class LexicalOverlapStrategy:
    """Deterministic token-overlap retrieval strategy (System A baseline).

    Ranking contract:
      1. Overlap score (count of shared lowercase word tokens) descending
      2. Timestamp (created_at) descending
    """

    @property
    def name(self) -> str:
        return "lexical"

    @staticmethod
    def _tokenize(text: str) -> set[str]:
        """Extract alphanumeric word tokens in lowercase."""
        return set(re.findall(r"\w+", text.lower()))

    def rank(
        self,
        query: str,
        candidates: list[MemoryEntry],
        limit: int,
    ) -> list[ScoredMemory]:
        if not query or not query.strip() or not candidates or limit <= 0:
            return []

        query_tokens = self._tokenize(query)
        if not query_tokens:
            return []

        scored: list[tuple[int, str, MemoryEntry]] = []
        for entry in candidates:
            entry_tokens = self._tokenize(entry.content)
            overlap = len(query_tokens & entry_tokens)
            if overlap > 0:
                scored.append((overlap, entry.created_at or "", entry))

        # Sort: overlap score descending, created_at descending
        scored.sort(key=lambda item: (item[0], item[1]), reverse=True)

        results: list[ScoredMemory] = []
        for i, (overlap, _, entry) in enumerate(scored[:limit]):
            results.append(
                ScoredMemory(
                    entry=entry,
                    score=float(overlap),
                    rank=i + 1,
                    strategy="lexical",
                )
            )
        return results


class BM25FTS5Strategy:
    """Sparse retrieval strategy backed by SQLite contentless FTS5 (System B).

    Uses pre-LIMIT candidate restriction to guarantee that stale or expired
    indexed records cannot crowd out valid lifecycle-authorized candidates.
    """

    def __init__(self, store: SQLiteMemoryStore, mode: str = "OR") -> None:
        self.store = store
        self.mode = mode

    @property
    def name(self) -> str:
        return "bm25"

    def rank(
        self,
        query: str,
        candidates: list[MemoryEntry],
        limit: int,
    ) -> list[ScoredMemory]:
        if not query or not query.strip() or not candidates or limit <= 0:
            return []

        candidates_by_id = {c.id: c for c in candidates}
        candidate_ids = set(candidates_by_id.keys())

        # Pre-LIMIT candidate restriction in SQLite FTS5
        hits = self.store.search_fts(
            query=query,
            limit=limit,
            mode=self.mode,
            candidate_ids=candidate_ids,
        )

        results: list[ScoredMemory] = []
        for i, (mem_id, bm25_score) in enumerate(hits):
            entry = candidates_by_id.get(mem_id)
            if entry is not None:
                results.append(
                    ScoredMemory(
                        entry=entry,
                        score=float(bm25_score),
                        rank=i + 1,
                        strategy="bm25",
                    )
                )
        return results


class DenseSemanticStrategy:
    """Dense semantic retrieval strategy backed by cached vector embeddings (System C).

    Invariants:
      1. Never calls embed_documents during query retrieval (pure query-time retrieval).
      2. Surfaces missing cached embeddings via controlled error or skip policy.
      3. Threshold filtering applies per-candidate (not all-or-nothing).
      4. Results ordered cosine similarity descending, created_at descending.
    """

    def __init__(
        self,
        store: SQLiteMemoryStore,
        provider: EmbeddingProvider,
        threshold: float = 0.0,
        missing_embedding: str = "error",  # "error" | "skip"
    ) -> None:
        self.store = store
        self.provider = provider
        self.threshold = threshold
        if missing_embedding not in ("error", "skip"):
            raise ValueError(
                f"Invalid missing_embedding policy: {missing_embedding!r}. Must be 'error' or 'skip'."
            )
        self.missing_embedding = missing_embedding

    @property
    def name(self) -> str:
        return "dense"

    @staticmethod
    def _cosine_similarity(v1: list[float], v2: list[float]) -> float:
        """Compute cosine similarity between two float vectors."""
        dot = sum(a * b for a, b in zip(v1, v2))
        norm1 = math.sqrt(sum(a * a for a in v1))
        norm2 = math.sqrt(sum(b * b for b in v2))
        if norm1 <= 0.0 or norm2 <= 0.0:
            return 0.0
        return dot / (norm1 * norm2)

    def rank(
        self,
        query: str,
        candidates: list[MemoryEntry],
        limit: int,
    ) -> list[ScoredMemory]:
        if not query or not query.strip() or not candidates or limit <= 0:
            return []

        candidates_by_id = {c.id: c for c in candidates}
        cached_embeddings = self.store.get_embeddings_for_model(
            self.provider.model_fingerprint,
            list(candidates_by_id.keys()),
        )

        missing_ids: list[str] = []
        stale_ids: list[str] = []
        valid_cached: dict[str, list[float]] = {}

        for cid, entry in candidates_by_id.items():
            if cid not in cached_embeddings:
                missing_ids.append(cid)
                continue
            stored_hash, doc_vec = cached_embeddings[cid]
            current_hash = memory_content_hash(entry)
            if stored_hash != current_hash:
                stale_ids.append(cid)
                continue
            valid_cached[cid] = doc_vec

        if missing_ids or stale_ids:
            if self.missing_embedding == "error":
                errors: list[str] = []
                if missing_ids:
                    errors.append(f"{len(missing_ids)} missing embedding(s): {missing_ids[:5]}")
                if stale_ids:
                    errors.append(f"{len(stale_ids)} stale embedding(s): {stale_ids[:5]}")
                err_msg = "; ".join(errors)
                if stale_ids and not missing_ids:
                    raise StaleEmbeddingError(
                        f"Stale cached embeddings for {len(stale_ids)} candidate(s) under model '{self.provider.model_fingerprint}': {err_msg}"
                    )
                raise MissingEmbeddingError(
                    f"Missing cached embeddings for {len(missing_ids)} candidate(s) under model '{self.provider.model_fingerprint}': {err_msg}"
                )
            # if "skip": proceed with valid_cached only

        if not valid_cached:
            return []

        # Embed query once
        query_vec = self.provider.embed_query(query)

        scored: list[tuple[float, str, MemoryEntry]] = []
        for mem_id, doc_vec in valid_cached.items():
            entry = candidates_by_id.get(mem_id)
            if entry is None:
                continue
            sim = self._cosine_similarity(query_vec, doc_vec)
            # Per-candidate threshold filtering
            if sim >= self.threshold:
                scored.append((sim, entry.created_at or "", entry))

        # Sort: cosine similarity descending, created_at descending
        scored.sort(key=lambda item: (item[0], item[1]), reverse=True)

        results: list[ScoredMemory] = []
        for i, (sim, _, entry) in enumerate(scored[:limit]):
            results.append(
                ScoredMemory(
                    entry=entry,
                    score=float(sim),
                    rank=i + 1,
                    strategy="dense",
                )
            )
        return results


SPARSE_STOPWORD_POLICY_VERSION: str = "v1-standard-english-34"

_QUERY_STOPWORDS: frozenset[str] = frozenset({
    "a", "an", "and", "are", "as", "at", "be", "by", "do", "does", "for",
    "from", "how", "i", "in", "is", "it", "my", "of", "on", "or", "our",
    "should", "that", "the", "this", "to", "was", "we", "what", "when",
    "where", "which", "who", "will", "with",
})


class HybridRRFStrategy:
    """Reciprocal Rank Fusion hybrid retrieval strategy (System D).

    Fuses rank positions from sparse and dense constituent strategies:
      RRF(d) = sum_{m in {sparse, dense}} w_m / (k + r_m(d))

    Invariants:
      1. Operates strictly on integer ranks (r_m(d) >= 1), NOT raw scores.
      2. Retrieves up to candidate_pool entries from each constituent strategy before fusion.
      3. Hybrid Relevance Gate:
           - Requires verified relevance evidence from at least one constituent branch.
           - Dense evidence: at least one candidate exceeds semantic cosine threshold tau.
           - Sparse evidence: top candidate satisfies query token coverage and BM25 cutoff.
           - When both pass -> fused RRF ranking.
           - When dense only passes -> returns dense hits, preventing weak sparse distractors.
           - When sparse only passes -> returns sparse hits (exact identifiers / lexical rescue).
           - When neither passes -> returns [] (clean out-of-domain abstention).
      4. Deterministic tie-breaking:
           - RRF score descending
           - best constituent rank ascending (lower integer is better)
           - created_at descending (newer is better)
           - memory ID ascending (lexicographical)
    """

    def __init__(
        self,
        sparse: RetrievalStrategy,
        dense: RetrievalStrategy,
        candidate_pool: int = 20,
        rrf_k: int = 60,
        sparse_weight: float = 1.0,
        dense_weight: float = 1.0,
        sparse_min_coverage: float = 0.35,
        sparse_bm25_cutoff: float = -2.5,
        sparse_stopword_policy_version: str = SPARSE_STOPWORD_POLICY_VERSION,
        enable_relevance_gate: bool = True,
    ) -> None:
        if candidate_pool <= 0:
            raise ValueError(f"candidate_pool must be positive, got {candidate_pool}")
        if rrf_k <= 0:
            raise ValueError(f"rrf_k must be positive, got {rrf_k}")
        self.sparse = sparse
        self.dense = dense
        self.candidate_pool = candidate_pool
        self.rrf_k = rrf_k
        self.sparse_weight = sparse_weight
        self.dense_weight = dense_weight
        self.sparse_min_coverage = sparse_min_coverage
        self.sparse_bm25_cutoff = sparse_bm25_cutoff
        self.enable_relevance_gate = enable_relevance_gate
        self.sparse_stopword_policy_version = sparse_stopword_policy_version

    @property
    def name(self) -> str:
        return "hybrid_rrf"

    def _has_sparse_evidence(
        self,
        query: str,
        sparse_hits: list[ScoredMemory],
    ) -> bool:
        """Evaluate whether top sparse candidate exhibits sufficient lexical evidence.

        Prevents accidental 1-token matches on out-of-domain queries from authorizing retrieval.
        Requires:
          1. At least one sparse candidate returned.
          2. Top candidate BM25 score <= sparse_bm25_cutoff (lower is stronger in SQLite FTS5).
          3. Significant query token coverage >= sparse_min_coverage (or exact single-token match).
        """
        if not sparse_hits:
            return False

        top_hit = sparse_hits[0]
        # SQLite FTS5 BM25 returns negative scores where more negative = stronger match
        if top_hit.score > self.sparse_bm25_cutoff:
            return False

        q_tokens = set(re.findall(r"\w+", query.lower()))
        sig_q = q_tokens - _QUERY_STOPWORDS
        if not sig_q:
            sig_q = q_tokens

        doc_text = f"{top_hit.entry.content} {top_hit.entry.memory_key or ''} {top_hit.entry.memory_value or ''}".lower()
        doc_tokens = set(re.findall(r"\w+", doc_text))
        matched = sig_q & doc_tokens

        if len(sig_q) == 1:
            return len(matched) == 1
        coverage = len(matched) / len(sig_q)
        return coverage >= self.sparse_min_coverage

    def _fuse_rrf(
        self,
        sparse_hits: list[ScoredMemory],
        dense_hits: list[ScoredMemory],
        limit: int,
    ) -> list[ScoredMemory]:
        """Execute reciprocal rank fusion across sparse and dense candidate hits."""
        fused_map: dict[str, dict[str, Any]] = {}

        for hit in sparse_hits:
            mem_id = hit.entry.id
            reciprocal_score = self.sparse_weight / (self.rrf_k + hit.rank)
            if mem_id not in fused_map:
                fused_map[mem_id] = {
                    "entry": hit.entry,
                    "rrf_score": reciprocal_score,
                    "best_rank": hit.rank,
                }
            else:
                fused_map[mem_id]["rrf_score"] += reciprocal_score
                fused_map[mem_id]["best_rank"] = min(fused_map[mem_id]["best_rank"], hit.rank)

        for hit in dense_hits:
            mem_id = hit.entry.id
            reciprocal_score = self.dense_weight / (self.rrf_k + hit.rank)
            if mem_id not in fused_map:
                fused_map[mem_id] = {
                    "entry": hit.entry,
                    "rrf_score": reciprocal_score,
                    "best_rank": hit.rank,
                }
            else:
                fused_map[mem_id]["rrf_score"] += reciprocal_score
                fused_map[mem_id]["best_rank"] = min(fused_map[mem_id]["best_rank"], hit.rank)

        def _sort_key(item: dict[str, Any]) -> tuple[float, int, float, str]:
            entry: MemoryEntry = item["entry"]
            ts = 0.0
            if entry.created_at:
                try:
                    dt = datetime.fromisoformat(str(entry.created_at).replace("Z", "+00:00"))
                    ts = dt.timestamp()
                except (ValueError, TypeError):
                    ts = 0.0
            return (
                -item["rrf_score"],
                item["best_rank"],
                -ts,
                entry.id,
            )

        fused_items = sorted(fused_map.values(), key=_sort_key)

        results: list[ScoredMemory] = []
        for i, item in enumerate(fused_items[:limit]):
            results.append(
                ScoredMemory(
                    entry=item["entry"],
                    score=float(item["rrf_score"]),
                    rank=i + 1,
                    strategy="hybrid_rrf",
                )
            )
        return results

    def rank(
        self,
        query: str,
        candidates: list[MemoryEntry],
        limit: int,
    ) -> list[ScoredMemory]:
        if not query or not query.strip() or not candidates or limit <= 0:
            return []

        # Retrieve constituent candidate pools
        sparse_hits = self.sparse.rank(query, candidates, limit=self.candidate_pool)
        dense_hits = self.dense.rank(query, candidates, limit=self.candidate_pool)

        if not sparse_hits and not dense_hits:
            return []

        if not self.enable_relevance_gate:
            return self._fuse_rrf(sparse_hits, dense_hits, limit)

        dense_evidence = len(dense_hits) > 0
        sparse_evidence = self._has_sparse_evidence(query, sparse_hits)

        if dense_evidence and sparse_evidence:
            # Both branches possess calibrated evidence: fuse using RRF
            return self._fuse_rrf(sparse_hits, dense_hits, limit)
        elif dense_evidence:
            # Dense possesses semantic evidence; sparse has no verified evidence.
            # Return dense results directly, preventing unevidenced sparse distractors from corrupting ranking.
            return [
                ScoredMemory(entry=h.entry, score=h.score, rank=i + 1, strategy="hybrid_rrf")
                for i, h in enumerate(dense_hits[:limit])
            ]
        elif sparse_evidence:
            # Sparse possesses lexical evidence (exact identifiers, long-memory keywords); dense abstained.
            # Return sparse results directly to preserve lexical rescue.
            return [
                ScoredMemory(entry=h.entry, score=h.score, rank=i + 1, strategy="hybrid_rrf")
                for i, h in enumerate(sparse_hits[:limit])
            ]
        else:
            # Neither branch possesses verified evidence: cleanly abstain.
            return []
