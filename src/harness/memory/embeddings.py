"""Embedding provider protocol, lazy SentenceTransformer implementation, fake provider, and indexer."""

from __future__ import annotations

import hashlib
import math
import struct
from typing import Any, Protocol, runtime_checkable

from harness.memory.base import MemoryEntry
from harness.memory.store import SQLiteMemoryStore


def serialize_embedding(vector: list[float], expected_dimension: int | None = None) -> bytes:
    """Serialize a list of float32 values to bytes with dimension and finiteness validation."""
    if expected_dimension is not None and len(vector) != expected_dimension:
        raise ValueError(
            f"Vector length {len(vector)} does not match expected dimension {expected_dimension}"
        )
    if not vector:
        raise ValueError("Cannot serialize an empty vector")
    for i, val in enumerate(vector):
        if not math.isfinite(val):
            raise ValueError(f"Vector contains non-finite value at index {i}: {val}")
    return struct.pack(f"{len(vector)}f", *vector)


def deserialize_embedding(blob: bytes, expected_dimension: int) -> list[float]:
    """Deserialize a binary float32 BLOB to list of floats, strictly enforcing length and finiteness."""
    if expected_dimension <= 0:
        raise ValueError(f"Dimension must be a positive integer: {expected_dimension}")
    expected_bytes = expected_dimension * 4
    if len(blob) != expected_bytes:
        raise ValueError(
            f"Corrupt embedding BLOB: expected {expected_bytes} bytes for dimension "
            f"{expected_dimension}, got {len(blob)} bytes"
        )
    vector = list(struct.unpack(f"{expected_dimension}f", blob))
    for i, val in enumerate(vector):
        if not math.isfinite(val):
            raise ValueError(f"Deserialized embedding contains non-finite value at index {i}: {val}")
    return vector


def normalize_vector(vector: list[float], eps: float = 1e-12) -> list[float]:
    """L2-normalize a float vector to unit length (norm ≈ 1.0). Rejects zero-norm vectors."""
    norm = math.sqrt(sum(x * x for x in vector))
    if not math.isfinite(norm):
        raise ValueError(f"Vector norm is not finite: {norm}")
    if norm < eps:
        raise ValueError(f"Zero-norm vector cannot be normalized (norm {norm} < {eps})")
    return [x / norm for x in vector]


def memory_embedding_text(entry: MemoryEntry) -> str:
    """Canonical string representation of a memory entry for dense embedding generation."""
    if entry.memory_key and entry.memory_value:
        return f"key: {entry.memory_key}\nvalue: {entry.memory_value}\ncontent: {entry.content}"
    return entry.content


def memory_content_hash(entry: MemoryEntry) -> str:
    """Compute SHA-256 hash of the canonical representation of a memory entry."""
    canonical = memory_embedding_text(entry)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


@runtime_checkable
class EmbeddingProvider(Protocol):
    """Protocol for dense embedding providers supporting asymmetric retrieval."""

    @property
    def model_name(self) -> str:
        """Name or repository identifier of the embedding model."""
        ...

    @property
    def model_revision(self) -> str:
        """Git commit SHA or version tag of the model."""
        ...

    @property
    def dimension(self) -> int:
        """Dimensionality of output embedding vectors."""
        ...

    @property
    def model_fingerprint(self) -> str:
        """Deterministic fingerprint uniquely identifying model, revision, dimension, and query prompt config."""
        ...

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        """Encode a batch of document texts into normalized dense vectors."""
        ...

    def embed_query(self, query: str) -> list[float]:
        """Encode a single search query into a normalized dense vector using model-specific query prompting."""
        ...


class SentenceTransformerEmbeddingProvider:
    """Generic, lazy-loading SentenceTransformers provider with explicit asymmetric query prompting."""

    def __init__(
        self,
        model_name: str,
        dimension: int | None = None,
        revision: str | None = None,
        query_prefix: str | None = None,
        query_prompt_name: str | None = None,
        normalize_embeddings: bool = True,
        local_files_only: bool = False,
        device: str | None = "cpu",
    ) -> None:
        self._model_name = model_name
        self._dimension = dimension
        self._revision = revision or ""
        self._query_prefix = query_prefix
        self._query_prompt_name = query_prompt_name
        self._normalize_embeddings = normalize_embeddings
        self._local_files_only = local_files_only
        self._device = device
        self._model: Any = None

    @property
    def model_name(self) -> str:
        return self._model_name

    @property
    def model_revision(self) -> str:
        return self._revision

    @property
    def dimension(self) -> int:
        if self._dimension is None:
            # Trigger lazy load to resolve dimension if not provided upfront
            self._ensure_model()
        assert self._dimension is not None
        return self._dimension

    @property
    def model_fingerprint(self) -> str:
        query_cfg = []
        if self._query_prefix:
            qp_hash = hashlib.sha256(self._query_prefix.encode("utf-8")).hexdigest()[:8]
            query_cfg.append(f"qpfx={qp_hash}")
        if self._query_prompt_name:
            query_cfg.append(f"qpname={self._query_prompt_name}")
        q_tag = ",".join(query_cfg) if query_cfg else "none"

        rev_tag = self._revision or "default"
        l2_tag = "l2=1" if self._normalize_embeddings else "l2=0"
        dim_str = str(self._dimension) if self._dimension is not None else "lazy"
        return f"{self._model_name}@{rev_tag}:dim={dim_str}:{q_tag}:{l2_tag}"

    def is_loaded(self) -> bool:
        """Return True if underlying neural model is currently initialized in memory."""
        return self._model is not None

    def _ensure_model(self) -> Any:
        if self._model is None:
            from sentence_transformers import SentenceTransformer

            kwargs: dict[str, Any] = {}
            if self._revision:
                kwargs["revision"] = self._revision
            if self._local_files_only:
                kwargs["local_files_only"] = True
            if self._device is not None:
                kwargs["device"] = self._device
            elif os.environ.get("TORCH_DEVICE"):
                kwargs["device"] = os.environ["TORCH_DEVICE"]
            model = SentenceTransformer(self._model_name, **kwargs)
            actual_dim = model.get_sentence_embedding_dimension()
            if self._dimension is not None and self._dimension != actual_dim:
                raise ValueError(
                    f"Configured dimension {self._dimension} does not match model dimension {actual_dim}"
                )
            self._dimension = actual_dim
            self._model = model
        return self._model

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        model = self._ensure_model()
        # Documents are encoded without query prompt or query prefix
        raw_embs = model.encode(
            texts,
            normalize_embeddings=self._normalize_embeddings,
            convert_to_numpy=True,
        )
        results: list[list[float]] = []
        for row in raw_embs:
            vec = [float(x) for x in row]
            if len(vec) != self.dimension:
                raise ValueError(
                    f"Model returned vector of dimension {len(vec)}, expected {self.dimension}"
                )
            for x in vec:
                if not math.isfinite(x):
                    raise ValueError(f"Model returned non-finite value: {x}")
            results.append(vec)
        return results

    def embed_query(self, query: str) -> list[float]:
        model = self._ensure_model()
        kwargs: dict[str, Any] = {
            "normalize_embeddings": self._normalize_embeddings,
            "convert_to_numpy": True,
        }
        if self._query_prompt_name:
            kwargs["prompt_name"] = self._query_prompt_name

        query_text = f"{self._query_prefix}{query}" if self._query_prefix else query
        raw = model.encode(query_text, **kwargs)
        if hasattr(raw, "flatten"):
            items = raw.flatten().tolist()
        elif isinstance(raw, list):
            if raw and isinstance(raw[0], (list, tuple)):
                items = [x for sub in raw for x in sub]
            else:
                items = raw
        else:
            items = list(raw)

        vec = [float(x) for x in items]
        if len(vec) != self.dimension:
            raise ValueError(
                f"Model returned vector of dimension {len(vec)}, expected {self.dimension}"
            )
        for x in vec:
            if not math.isfinite(x):
                raise ValueError(f"Model returned non-finite value: {x}")
        return vec


class DeterministicFakeEmbeddingProvider:
    """Deterministic test double for embedding providers, supporting explicit vectors and hash fallback."""

    def __init__(
        self,
        vectors: dict[str, list[float]] | None = None,
        dimension: int = 384,
        model_name: str = "fake-embedder",
        model_revision: str = "v1",
        query_prefix: str | None = None,
        query_prompt_name: str | None = None,
    ) -> None:
        self._vectors = dict(vectors) if vectors else {}
        self._dimension = dimension
        self._model_name = model_name
        self._model_revision = model_revision
        self._query_prefix = query_prefix
        self._query_prompt_name = query_prompt_name

        self.document_call_count = 0
        self.query_call_count = 0
        self.last_encoded_documents: list[str] = []
        self.last_encoded_query: str | None = None

    @property
    def model_name(self) -> str:
        return self._model_name

    @property
    def model_revision(self) -> str:
        return self._model_revision

    @property
    def dimension(self) -> int:
        return self._dimension

    @property
    def model_fingerprint(self) -> str:
        query_cfg = []
        if self._query_prefix:
            qp_hash = hashlib.sha256(self._query_prefix.encode("utf-8")).hexdigest()[:8]
            query_cfg.append(f"qpfx={qp_hash}")
        if self._query_prompt_name:
            query_cfg.append(f"qpname={self._query_prompt_name}")
        q_tag = ",".join(query_cfg) if query_cfg else "none"
        return f"{self._model_name}@{self._model_revision}:dim={self._dimension}:{q_tag}:l2=1"

    def _generate_vector(self, text: str) -> list[float]:
        # 1. Exact match in pre-configured vectors
        if text in self._vectors:
            raw = self._vectors[text]
            if len(raw) != self._dimension:
                raise ValueError(
                    f"Configured vector length {len(raw)} does not match provider dimension {self._dimension}"
                )
            return normalize_vector(raw)

        # 2. Deterministic unit vector derived from SHA-256 hash
        digest = hashlib.sha256(text.encode("utf-8")).digest()
        raw_values: list[float] = []
        for i in range(self._dimension):
            # Sample bytes circularly
            b0 = digest[(i * 4) % len(digest)]
            b1 = digest[(i * 4 + 1) % len(digest)]
            val = ((b0 << 8) | b1) / 32768.0 - 1.0  # Float in [-1.0, 1.0]
            if val == 0.0:
                val = 0.01  # Prevent zero vector
            raw_values.append(val)
        return normalize_vector(raw_values)

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        self.document_call_count += len(texts)
        self.last_encoded_documents = list(texts)
        # Documents NEVER receive query_prefix or query_prompt_name
        return [self._generate_vector(t) for t in texts]

    def embed_query(self, query: str) -> list[float]:
        self.query_call_count += 1
        query_text = f"{self._query_prefix}{query}" if self._query_prefix else query
        self.last_encoded_query = query_text
        return self._generate_vector(query_text)


class MemoryEmbeddingIndexer:
    """Coordinates embedding generation, content-hash caching, and batched backfilling."""

    def __init__(
        self,
        store: SQLiteMemoryStore,
        provider: EmbeddingProvider,
    ) -> None:
        self.store = store
        self.provider = provider

    def ensure_embedding(self, entry: MemoryEntry) -> bool:
        """Ensure an entry has a cached embedding matching the provider's fingerprint and content hash.

        Returns True if a new embedding was generated and stored, False if existing cache was reused.
        """
        current_hash = memory_content_hash(entry)
        cached = self.store.get_embedding(entry.id, self.provider.model_fingerprint)

        if cached is not None and cached["content_hash"] == current_hash:
            return False

        # Generate new embedding
        doc_text = memory_embedding_text(entry)
        vectors = self.provider.embed_documents([doc_text])
        vector = vectors[0]

        self.store.save_embedding(
            memory_id=entry.id,
            model_fingerprint=self.provider.model_fingerprint,
            model_name=self.provider.model_name,
            model_revision=self.provider.model_revision,
            dimension=self.provider.dimension,
            content_hash=current_hash,
            embedding=vector,
        )
        return True

    def ensure_embeddings(self, entries: list[MemoryEntry], batch_size: int = 64) -> int:
        """Ensure embeddings exist for a list of entries. Returns number of newly computed embeddings."""
        return self.backfill_missing(entries, batch_size=batch_size)

    def backfill_missing(
        self,
        entries: list[MemoryEntry],
        batch_size: int = 64,
    ) -> int:
        """Identify missing or stale embeddings, batch-encode, and persist to SQLite.

        Returns total count of newly generated embeddings.
        """
        if not entries:
            return 0

        # 1. Fetch existing cached embeddings for these entries in a single query
        entry_ids = [e.id for e in entries]
        cached_map = self.store.get_embeddings_for_model(self.provider.model_fingerprint, entry_ids)

        # 2. Filter for entries lacking a cached embedding or whose content_hash changed
        missing_or_stale: list[tuple[MemoryEntry, str]] = []
        for e in entries:
            current_hash = memory_content_hash(e)
            if e.id not in cached_map or cached_map[e.id][0] != current_hash:
                missing_or_stale.append((e, current_hash))

        if not missing_or_stale:
            return 0

        # 3. Batch process missing entries
        total_created = 0
        for i in range(0, len(missing_or_stale), batch_size):
            batch = missing_or_stale[i : i + batch_size]
            batch_texts = [memory_embedding_text(e) for e, _ in batch]
            batch_vectors = self.provider.embed_documents(batch_texts)

            records = []
            for (e, c_hash), vec in zip(batch, batch_vectors):
                records.append(
                    {
                        "memory_id": e.id,
                        "model_fingerprint": self.provider.model_fingerprint,
                        "model_name": self.provider.model_name,
                        "model_revision": self.provider.model_revision,
                        "dimension": self.provider.dimension,
                        "dtype": "float32",
                        "content_hash": c_hash,
                        "embedding": vec,
                    }
                )

            self.store.save_embeddings_batch(records)
            total_created += len(records)

        return total_created
