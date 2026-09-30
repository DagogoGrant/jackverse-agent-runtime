"""Unit tests for Phase 3 EmbeddingProvider, lazy SentenceTransformer provider, fake provider, and indexer."""

import math
import struct
import unittest
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

from harness.memory.base import MemoryEntry, MemorySource, MemoryStatus, MemoryType
from harness.memory.embeddings import (
    DeterministicFakeEmbeddingProvider,
    EmbeddingProvider,
    MemoryEmbeddingIndexer,
    SentenceTransformerEmbeddingProvider,
    deserialize_embedding,
    memory_content_hash,
    memory_embedding_text,
    normalize_vector,
    serialize_embedding,
)
from harness.memory.store import SQLiteMemoryStore


class TestMemoryEmbeddings(unittest.TestCase):
    """Test suite for embedding providers, serialization contract, and embedding indexer."""

    def setUp(self) -> None:
        self.store = SQLiteMemoryStore(":memory:")

    def tearDown(self) -> None:
        self.store.close()

    def _make_entry(
        self,
        entry_id: str,
        content: str,
        memory_key: str | None = None,
        memory_value: str | None = None,
    ) -> MemoryEntry:
        return MemoryEntry(
            id=entry_id,
            created_at=datetime.now(timezone.utc).isoformat(),
            content=content,
            source=MemorySource.USER_INPUT,
            status=MemoryStatus.ACCEPTED,
            memory_type=MemoryType.DECLARATIVE,
            memory_key=memory_key,
            memory_value=memory_value,
        )

    # -------------------------------------------------------------------------
    # 1. Serialization & Contract Tests
    # -------------------------------------------------------------------------

    def test_serialize_and_deserialize_roundtrip(self) -> None:
        """Float vector should serialize and deserialize faithfully within float32 precision."""
        vec = [0.12345, -0.6789, 0.99999]
        blob = serialize_embedding(vec, expected_dimension=3)
        self.assertEqual(len(blob), 12)  # 3 * 4 bytes

        recovered = deserialize_embedding(blob, expected_dimension=3)
        self.assertEqual(len(recovered), 3)
        for original, rec in zip(vec, recovered):
            self.assertAlmostEqual(original, rec, places=5)

    def test_serialize_dimension_mismatch_raises(self) -> None:
        """serialize_embedding must raise ValueError if vector length does not match expected dimension."""
        with self.assertRaises(ValueError):
            serialize_embedding([0.1, 0.2], expected_dimension=3)

    def test_serialize_empty_vector_raises(self) -> None:
        """serialize_embedding must reject empty vectors."""
        with self.assertRaises(ValueError):
            serialize_embedding([])

    def test_serialize_non_finite_value_raises(self) -> None:
        """serialize_embedding must reject NaN and Inf."""
        with self.assertRaises(ValueError):
            serialize_embedding([0.1, float("nan"), 0.3])
        with self.assertRaises(ValueError):
            serialize_embedding([0.1, float("inf"), 0.3])

    def test_deserialize_dimension_mismatch_raises(self) -> None:
        """deserialize_embedding must reject BLOBs whose byte count != expected_dim * 4."""
        valid_blob = struct.pack("3f", 0.1, 0.2, 0.3)
        with self.assertRaises(ValueError) as ctx:
            deserialize_embedding(valid_blob, expected_dimension=4)  # 16 expected, got 12
        self.assertIn("Corrupt embedding BLOB", str(ctx.exception))

    def test_deserialize_corrupt_non_finite_raises(self) -> None:
        """deserialize_embedding must reject BLOB containing NaN."""
        nan_blob = struct.pack("2f", 0.1, float("nan"))
        with self.assertRaises(ValueError) as ctx:
            deserialize_embedding(nan_blob, expected_dimension=2)
        self.assertIn("non-finite value", str(ctx.exception))

    def test_normalize_vector_unit_length(self) -> None:
        """normalize_vector must produce vector with L2 norm ≈ 1.0."""
        vec = [3.0, 4.0, 0.0]
        normed = normalize_vector(vec)
        self.assertAlmostEqual(normed[0], 0.6, places=6)
        self.assertAlmostEqual(normed[1], 0.8, places=6)
        self.assertAlmostEqual(normed[2], 0.0, places=6)
        self.assertAlmostEqual(sum(x * x for x in normed), 1.0, places=6)

    def test_normalize_zero_vector_handled(self) -> None:
        """normalize_vector on zero vector raises ValueError."""
        with self.assertRaises(ValueError) as ctx:
            normalize_vector([0.0, 0.0, 0.0])
        self.assertIn("Zero-norm vector cannot be normalized", str(ctx.exception))

    # -------------------------------------------------------------------------
    # 2. Canonical Document Representation Tests
    # -------------------------------------------------------------------------

    def test_canonical_document_text_and_hash(self) -> None:
        """Canonical representation formats structured memory slots deterministically."""
        e1 = self._make_entry("m1", "My favorite programming language is Python.")
        self.assertEqual(memory_embedding_text(e1), "My favorite programming language is Python.")
        h1 = memory_content_hash(e1)
        self.assertIsInstance(h1, str)
        self.assertEqual(len(h1), 64)

        # Adding structured key/value changes representation and hash
        e2 = self._make_entry(
            "m1",
            "My favorite programming language is Python.",
            memory_key="preferred_language",
            memory_value="Python",
        )
        self.assertEqual(
            memory_embedding_text(e2),
            "key: preferred_language\nvalue: Python\ncontent: My favorite programming language is Python.",
        )
        h2 = memory_content_hash(e2)
        self.assertNotEqual(h1, h2)

    # -------------------------------------------------------------------------
    # 3. Provider Lazy Loading & Contract Tests
    # -------------------------------------------------------------------------

    def test_sentence_transformer_provider_construction_is_lazy(self) -> None:
        """Constructing SentenceTransformerEmbeddingProvider must NOT initialize the neural model."""
        provider = SentenceTransformerEmbeddingProvider(
            model_name="BAAI/bge-small-en-v1.5",
            dimension=384,
            revision="50bc194f",
        )
        self.assertFalse(provider.is_loaded())
        self.assertIsNone(provider._model)

    def test_sentence_transformer_provider_loads_once_on_first_call(self) -> None:
        """First embed call initializes model once; subsequent calls reuse the initialized instance."""
        provider = SentenceTransformerEmbeddingProvider(
            model_name="mock-model",
            dimension=4,
        )

        mock_st = MagicMock()
        mock_st.get_sentence_embedding_dimension.return_value = 4
        mock_st.encode.return_value = [[0.5, 0.5, 0.5, 0.5]]

        mock_module = MagicMock()
        mock_module.SentenceTransformer = MagicMock(return_value=mock_st)

        with patch.dict("sys.modules", {"sentence_transformers": mock_module}):
            self.assertFalse(provider.is_loaded())

            # 1. First embed call
            res1 = provider.embed_documents(["hello"])
            self.assertTrue(provider.is_loaded())
            self.assertEqual(mock_module.SentenceTransformer.call_count, 1)
            self.assertEqual(res1, [[0.5, 0.5, 0.5, 0.5]])

            # 2. Subsequent embed call reuses model
            res2 = provider.embed_documents(["world"])
            self.assertEqual(mock_module.SentenceTransformer.call_count, 1)  # NOT called again
            self.assertEqual(res2, [[0.5, 0.5, 0.5, 0.5]])

    def test_sentence_transformer_asymmetric_query_handling(self) -> None:
        """embed_query applies configured query_prefix or query_prompt_name, while embed_documents does not."""
        mock_st = MagicMock()
        mock_st.get_sentence_embedding_dimension.return_value = 4
        mock_st.encode.return_value = [[0.1, 0.2, 0.3, 0.4]]

        provider = SentenceTransformerEmbeddingProvider(
            model_name="mock-model",
            dimension=4,
            query_prefix="Represent this query: ",
            query_prompt_name="query_prompt",
        )
        provider._model = mock_st  # Inject mock directly

        # Document encoding: no prefix or prompt_name passed
        provider.embed_documents(["doc text"])
        mock_st.encode.assert_called_with(
            ["doc text"],
            normalize_embeddings=True,
            convert_to_numpy=True,
        )

        # Query encoding: prefix prepended, prompt_name forwarded
        provider.embed_query("search query")
        mock_st.encode.assert_called_with(
            "Represent this query: search query",
            normalize_embeddings=True,
            convert_to_numpy=True,
            prompt_name="query_prompt",
        )

    def test_model_fingerprint_determinism_and_sensitivity(self) -> None:
        """model_fingerprint must be deterministic and sensitive to revision and query prompt configuration."""
        p1 = SentenceTransformerEmbeddingProvider(
            model_name="BAAI/bge-small-en-v1.5",
            dimension=384,
            revision="revA",
            query_prefix="instruction A",
        )
        p2 = SentenceTransformerEmbeddingProvider(
            model_name="BAAI/bge-small-en-v1.5",
            dimension=384,
            revision="revA",
            query_prefix="instruction A",
        )
        self.assertEqual(p1.model_fingerprint, p2.model_fingerprint)

        # Revision change changes fingerprint
        p_rev = SentenceTransformerEmbeddingProvider(
            model_name="BAAI/bge-small-en-v1.5",
            dimension=384,
            revision="revB",
            query_prefix="instruction A",
        )
        self.assertNotEqual(p1.model_fingerprint, p_rev.model_fingerprint)

        # Query prompt change changes fingerprint
        p_prompt = SentenceTransformerEmbeddingProvider(
            model_name="BAAI/bge-small-en-v1.5",
            dimension=384,
            revision="revA",
            query_prefix="instruction DIFFERENT",
        )
        self.assertNotEqual(p1.model_fingerprint, p_prompt.model_fingerprint)

        # Normalization flag change changes fingerprint
        p_norm = SentenceTransformerEmbeddingProvider(
            model_name="BAAI/bge-small-en-v1.5",
            dimension=384,
            revision="revA",
            query_prefix="instruction A",
            normalize_embeddings=False,
        )
        self.assertNotEqual(p1.model_fingerprint, p_norm.model_fingerprint)

    # -------------------------------------------------------------------------
    # 4. Deterministic Fake Provider Tests
    # -------------------------------------------------------------------------

    def test_fake_provider_explicit_vectors_and_fallback(self) -> None:
        """DeterministicFakeEmbeddingProvider returns configured vectors when present, else hash vectors."""
        explicit = {
            "target query": [1.0, 0.0, 0.0],
            "target relevant": [0.9, 0.1, 0.0],
        }
        fake = DeterministicFakeEmbeddingProvider(vectors=explicit, dimension=3)

        embs = fake.embed_documents(["target relevant", "arbitrary unknown text"])
        self.assertEqual(len(embs), 2)
        # Target relevant should match normalized explicit vector
        norm_expected = normalize_vector([0.9, 0.1, 0.0])
        for a, b in zip(embs[0], norm_expected):
            self.assertAlmostEqual(a, b, places=5)

        # Unknown text produces valid, deterministic unit vector of dim=3
        self.assertEqual(len(embs[1]), 3)
        self.assertAlmostEqual(sum(x * x for x in embs[1]), 1.0, places=5)

        # Calling again on unknown text returns the exact same vector
        embs_again = fake.embed_documents(["arbitrary unknown text"])
        self.assertEqual(embs[1], embs_again[0])

    def test_fake_provider_asymmetric_query_prefix(self) -> None:
        """DeterministicFakeEmbeddingProvider prepends query_prefix only on embed_query."""
        fake = DeterministicFakeEmbeddingProvider(
            dimension=3,
            query_prefix="QUERY: ",
        )
        fake.embed_documents(["doc text"])
        self.assertEqual(fake.last_encoded_documents, ["doc text"])

        fake.embed_query("user query")
        self.assertEqual(fake.last_encoded_query, "QUERY: user query")

    # -------------------------------------------------------------------------
    # 5. Indexer Tests (Cache hit, refresh on change, batching, multi-model)
    # -------------------------------------------------------------------------

    def test_indexer_creates_embedding_and_reuses_cache(self) -> None:
        """Indexer creates embedding on first call, reuses cache on second call without provider re-invocation."""
        fake = DeterministicFakeEmbeddingProvider(dimension=3)
        indexer = MemoryEmbeddingIndexer(self.store, fake)

        entry = self._make_entry("m1", "Python programming language notes")
        self.store.add(entry)

        # 1. First call: new embedding created
        created = indexer.ensure_embedding(entry)
        self.assertTrue(created)
        self.assertEqual(fake.document_call_count, 1)
        self.assertEqual(self.store.count_embeddings(), 1)

        # Check stored embedding
        record = self.store.get_embedding("m1", fake.model_fingerprint)
        self.assertIsNotNone(record)
        self.assertEqual(record["content_hash"], memory_content_hash(entry))
        self.assertEqual(record["dimension"], 3)

        # 2. Second call: cache hit, provider NOT called
        created_again = indexer.ensure_embedding(entry)
        self.assertFalse(created_again)
        self.assertEqual(fake.document_call_count, 1)  # Call count unchanged

    def test_indexer_refreshes_embedding_when_content_changes(self) -> None:
        """When memory content changes, the indexer detects hash mismatch and generates a fresh vector."""
        fake = DeterministicFakeEmbeddingProvider(dimension=3)
        indexer = MemoryEmbeddingIndexer(self.store, fake)

        entry = self._make_entry("m1", "Initial draft")
        self.store.add(entry)
        self.assertTrue(indexer.ensure_embedding(entry))
        self.assertEqual(fake.document_call_count, 1)

        # Content changes
        updated_entry = self._make_entry("m1", "Substantially revised and modified text")
        self.store.add(updated_entry)

        # Indexer detects content_hash change and re-embeds
        refreshed = indexer.ensure_embedding(updated_entry)
        self.assertTrue(refreshed)
        self.assertEqual(fake.document_call_count, 2)
        self.assertEqual(self.store.count_embeddings(), 1)

        # New content hash stored
        record = self.store.get_embedding("m1", fake.model_fingerprint)
        self.assertIsNotNone(record)
        self.assertEqual(record["content_hash"], memory_content_hash(updated_entry))

    def test_indexer_backfill_missing_respects_batch_size(self) -> None:
        """backfill_missing batches embedding requests according to configured batch_size."""
        fake = DeterministicFakeEmbeddingProvider(dimension=3)
        indexer = MemoryEmbeddingIndexer(self.store, fake)

        entries = [self._make_entry(f"mem_{i:02d}", f"Content for item {i}") for i in range(10)]
        for e in entries:
            self.store.add(e)

        # Backfill 10 entries with batch_size=4 (batches of 4, 4, 2)
        total_created = indexer.backfill_missing(entries, batch_size=4)
        self.assertEqual(total_created, 10)
        self.assertEqual(self.store.count_embeddings(), 10)
        self.assertEqual(fake.document_call_count, 10)

        # Running backfill again returns 0 (all cached)
        second_run = indexer.backfill_missing(entries, batch_size=4)
        self.assertEqual(second_run, 0)
        self.assertEqual(fake.document_call_count, 10)  # No additional calls

    def test_multiple_model_fingerprints_coexist_in_indexer(self) -> None:
        """Two providers with different fingerprints independently index the same memory."""
        provider_a = DeterministicFakeEmbeddingProvider(dimension=3, model_name="modelA")
        provider_b = DeterministicFakeEmbeddingProvider(dimension=4, model_name="modelB")

        indexer_a = MemoryEmbeddingIndexer(self.store, provider_a)
        indexer_b = MemoryEmbeddingIndexer(self.store, provider_b)

        entry = self._make_entry("m1", "Common memory entry")
        self.store.add(entry)

        self.assertTrue(indexer_a.ensure_embedding(entry))
        self.assertTrue(indexer_b.ensure_embedding(entry))

        self.assertEqual(self.store.count_embeddings(), 2)

        rec_a = self.store.get_embedding("m1", provider_a.model_fingerprint)
        rec_b = self.store.get_embedding("m1", provider_b.model_fingerprint)
        self.assertIsNotNone(rec_a)
        self.assertIsNotNone(rec_b)
        self.assertEqual(rec_a["dimension"], 3)
        self.assertEqual(rec_b["dimension"], 4)

    def test_malformed_blob_in_store_rejected_with_value_error(self) -> None:
        """A corrupt BLOB with wrong byte length raises ValueError on retrieval from store."""
        fake = DeterministicFakeEmbeddingProvider(dimension=3)
        entry = self._make_entry("m_bad", "Corrupt vector test")
        self.store.add(entry)

        # Save corrupted blob directly with length != dimension * 4
        corrupt_blob = b"\x00" * 7  # 7 bytes instead of 3 * 4 = 12
        with self.store._conn:
            self.store._conn.execute(
                """
                INSERT INTO memory_embeddings (
                    memory_id, model_fingerprint, model_name, model_revision,
                    dimension, dtype, content_hash, embedding, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?);
                """,
                (
                    "m_bad",
                    fake.model_fingerprint,
                    fake.model_name,
                    fake.model_revision,
                    3,
                    "float32",
                    "dummy_hash",
                    corrupt_blob,
                    "2026-09-12T00:00:00Z",
                ),
            )

        with self.assertRaises(ValueError) as ctx1:
            self.store.get_embedding("m_bad", fake.model_fingerprint)
        self.assertIn("Corrupt embedding BLOB", str(ctx1.exception))

        with self.assertRaises(ValueError) as ctx2:
            self.store.get_embeddings_for_model(fake.model_fingerprint)
        self.assertIn("Corrupt embedding BLOB", str(ctx2.exception))

    def test_physical_memory_delete_cascades_embeddings(self) -> None:
        """Physical deletion of memory cascades to delete its embeddings in SQLite."""
        fake = DeterministicFakeEmbeddingProvider(dimension=3)
        indexer = MemoryEmbeddingIndexer(self.store, fake)

        entry = self._make_entry("m_to_delete", "Temporary memory")
        self.store.add(entry)
        self.assertTrue(indexer.ensure_embedding(entry))
        self.assertEqual(self.store.count_embeddings(), 1)

        # Physically delete memory
        deleted = self.store.delete("m_to_delete")
        self.assertTrue(deleted)
        self.assertEqual(self.store.count_embeddings(), 0)
        self.assertIsNone(self.store.get_embedding("m_to_delete", fake.model_fingerprint))


if __name__ == "__main__":
    unittest.main()
