"""Deterministic unit tests for memory index readiness modes and failure resilience.

Validates:
1. incremental: startup does not load the neural encoder; admitting an active declarative
   memory loads the model and indexes the embedding into SQLite.
2. eager: startup with missing eligible embeddings backfills the vector cache before
   the controller is returned.
3. sparse_only: startup and query paths never construct or instantiate SentenceTransformer;
   BM25 retrieval remains fully operational.
4. failure resilience: neural embedding failure on incremental admission does not rollback
   the accepted memory or its FTS5 index; BM25 remains capable of retrieving it.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from harness.cli import build_controller
from harness.config import (
    AgentConfig,
    AppConfig,
    LLMConfig,
    MemoryConfig,
    MemoryEmbeddingConfig,
    MemoryHybridConfig,
    MemorySparseConfig,
    ToolsConfig,
)
from harness.memory import (
    BaselineAdmissionPolicy,
    MemoryManager,
    MemoryRetriever,
    SQLiteMemoryStore,
)
from harness.memory.base import (
    MemoryEntry,
    MemorySource,
    MemoryStatus,
    MemoryType,
)
from harness.memory.embeddings import (
    DeterministicFakeEmbeddingProvider,
    MemoryEmbeddingIndexer,
    SentenceTransformerEmbeddingProvider,
)
from harness.memory.strategies import BM25FTS5Strategy, HybridRRFStrategy


class TestMemoryReadinessModes(unittest.TestCase):
    """Test suite for incremental, eager, sparse_only readiness modes and failure resilience."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "test_readiness.db"

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def _make_app_config(self, mem_cfg: MemoryConfig) -> AppConfig:
        return AppConfig(
            agent=AgentConfig(max_steps=5),
            llm=LLMConfig(
                base_url="https://llms.innkube.fim.uni-passau.de",
                model="qwen-agentworld-35b-a3b",
                temperature=0.0,
            ),
            tools=ToolsConfig(workspace_root=self.temp_dir.name),
            memory=mem_cfg,
        )

    # -------------------------------------------------------------------------
    # 1. Incremental Readiness Semantics
    # -------------------------------------------------------------------------
    def test_incremental_startup_does_not_load_encoder_until_admitted(self) -> None:
        """incremental: startup does not load encoder; admitting declarative memory loads encoder and caches vector."""
        store = SQLiteMemoryStore(self.db_path)
        fake_provider = DeterministicFakeEmbeddingProvider(dimension=384)

        # Wrap fake_provider to observe document embedding calls
        spy_embed = MagicMock(side_effect=fake_provider.embed_documents)
        fake_provider.embed_documents = spy_embed

        with patch("harness.cli.SentenceTransformerEmbeddingProvider", return_value=fake_provider):
            mem_cfg = MemoryConfig(
                enabled=True,
                storage_path=str(self.db_path),
                retrieval_strategy="hybrid_rrf",
                embedding=MemoryEmbeddingConfig(index_readiness="incremental"),
            )
            app_cfg = self._make_app_config(mem_cfg)
            controller, ws, reg = build_controller(app_cfg, api_key="test-key")

            # At startup: encoder has not been invoked
            spy_embed.assert_not_called()
            self.assertEqual(store.count_embeddings(), 0)

            # Admit active declarative memory -> triggers incremental embedding
            manager = controller.memory_manager
            decision = manager.admit_and_store(
                content="User preference: Always write tests with pytest.",
                source=MemorySource.USER_INPUT,
            )
            self.assertTrue(decision.admitted)
            spy_embed.assert_called_once()
            self.assertEqual(store.count_embeddings(), 1)

            entry = store.list_all()[0]
            emb = store.get_embedding(entry.id, fake_provider.model_fingerprint)
            self.assertIsNotNone(emb)
            self.assertEqual(emb["dimension"], 384)

            store.close()
            controller.memory_manager.store.close()

    def test_sentence_transformer_provider_is_lazy_at_startup(self) -> None:
        """SentenceTransformerEmbeddingProvider remains uninitialized (_model is None) until first encode call."""
        provider = SentenceTransformerEmbeddingProvider(
            model_name="Snowflake/snowflake-arctic-embed-s",
            dimension=384,
            revision="e596f507467533e48a2e17c007f0e1dacc837b33",
        )
        self.assertIsNone(provider._model)

    # -------------------------------------------------------------------------
    # 2. Eager Readiness Semantics
    # -------------------------------------------------------------------------
    def test_eager_startup_backfills_missing_embeddings_before_controller_returns(self) -> None:
        """eager: startup with un-embedded eligible memories completes backfill before controller is returned."""
        # Pre-populate database with an un-embedded declarative memory
        init_store = SQLiteMemoryStore(self.db_path)
        entry = MemoryEntry(
            id="mem_unembedded_init",
            created_at="2026-09-12T00:00:00+00:00",
            content="Pre-existing active declarative knowledge.",
            source=MemorySource.USER_INPUT,
            status=MemoryStatus.ACCEPTED,
            memory_type=MemoryType.DECLARATIVE,
            normalized_content="pre existing active declarative knowledge",
        )
        init_store.add(entry)
        self.assertEqual(init_store.count_embeddings(), 0)
        init_store.close()

        fake_provider = DeterministicFakeEmbeddingProvider(dimension=384)
        with patch("harness.cli.SentenceTransformerEmbeddingProvider", return_value=fake_provider):
            mem_cfg = MemoryConfig(
                enabled=True,
                storage_path=str(self.db_path),
                retrieval_strategy="hybrid_rrf",
                embedding=MemoryEmbeddingConfig(index_readiness="eager"),
            )
            app_cfg = self._make_app_config(mem_cfg)

            # On build_controller call, eager backfill must execute synchronously
            controller, ws, reg = build_controller(app_cfg, api_key="test-key")
            store = controller.memory_manager.store

            # Backfill is already complete before controller returned
            self.assertEqual(store.count_embeddings(), 1)
            cached = store.get_embedding("mem_unembedded_init", fake_provider.model_fingerprint)
            self.assertIsNotNone(cached)
            self.assertEqual(cached["dimension"], 384)

            controller.memory_manager.store.close()

    # -------------------------------------------------------------------------
    # 3. Sparse-Only Readiness Semantics
    # -------------------------------------------------------------------------
    def test_sparse_only_never_constructs_provider_and_retrieves_via_bm25(self) -> None:
        """sparse_only: never constructs SentenceTransformerEmbeddingProvider; BM25 retrieval remains functional."""
        with patch("harness.cli.SentenceTransformerEmbeddingProvider") as mock_provider_cls:
            mem_cfg = MemoryConfig(
                enabled=True,
                storage_path=str(self.db_path),
                retrieval_strategy="hybrid_rrf",
                embedding=MemoryEmbeddingConfig(index_readiness="sparse_only"),
            )
            app_cfg = self._make_app_config(mem_cfg)

            controller, ws, reg = build_controller(app_cfg, api_key="test-key")

            # Provider must NEVER be constructed
            mock_provider_cls.assert_not_called()
            manager = controller.memory_manager
            self.assertIsNone(manager.indexer)
            # Strategy falls back cleanly to pure BM25
            self.assertIsInstance(manager.retriever.strategy, BM25FTS5Strategy)

            # Store a declarative memory
            decision = manager.admit_and_store(
                content="Special access code is ALPHA-OMEGA-42.",
                source=MemorySource.USER_INPUT,
            )
            self.assertTrue(decision.admitted)
            self.assertEqual(manager.store.count_embeddings(), 0)

            # Retrieve via BM25 query
            retrieved = manager.retrieve("ALPHA-OMEGA")
            self.assertEqual(len(retrieved), 1)
            self.assertIn("ALPHA-OMEGA-42", retrieved[0].content)

            controller.memory_manager.store.close()

    # -------------------------------------------------------------------------
    # 4. Incremental Embedding Failure Resilience
    # -------------------------------------------------------------------------
    def test_incremental_embedding_failure_preserves_memory_in_sqlite_and_fts(self) -> None:
        """Incremental embedding failure leaves memory ACCEPTED in SQLite, indexed in FTS, and retrievable via BM25."""
        store = SQLiteMemoryStore(self.db_path)
        faulty_indexer = MagicMock()
        # Simulate neural runtime / OOM / disk error
        faulty_indexer.ensure_embeddings.side_effect = RuntimeError("Neural encoder out of memory")

        admission = BaselineAdmissionPolicy(store)
        retriever = MemoryRetriever(store, strategy=BM25FTS5Strategy(store))
        manager = MemoryManager(
            store=store,
            admission_policy=admission,
            retriever=retriever,
            indexer=faulty_indexer,
        )

        # Admitting memory must NOT raise exception
        decision = manager.admit_and_store(
            content="Critical business guideline: Always verify deployment gates before tag.",
            source=MemorySource.USER_INPUT,
        )
        self.assertTrue(decision.admitted)

        # 1. Memory remains ACCEPTED in SQLite authoritative store
        entries = store.list_all()
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0].status, MemoryStatus.ACCEPTED)

        # 2. FTS5 index contains the memory
        self.assertEqual(store.count_fts(), 1)
        fts_hits = store.search_fts("deployment gates")
        self.assertEqual(len(fts_hits), 1)
        self.assertEqual(fts_hits[0][0], entries[0].id)

        # 3. Dense embeddings table has NO cached vector (0 embeddings)
        self.assertEqual(store.count_embeddings(), 0)

        # 4. BM25 strategy successfully retrieves the memory
        retrieved = manager.retrieve("verify deployment gates")
        self.assertEqual(len(retrieved), 1)
        self.assertEqual(retrieved[0].id, entries[0].id)

        store.close()


if __name__ == "__main__":
    unittest.main()
