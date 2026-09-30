"""Integration tests for Step 5: Configuration parsing, build_controller wiring, index readiness, and CLI observability."""

from __future__ import annotations

import io
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from harness.config import (
    AgentConfig,
    AppConfig,
    LLMConfig,
    MemoryConfig,
    MemoryEmbeddingConfig,
    MemoryHybridConfig,
    MemorySparseConfig,
    ToolsConfig,
    load_config,
)
from harness.cli import build_controller, print_config, print_memory
from harness.memory import (
    BaselineAdmissionPolicy,
    MemoryFirewall,
    MemoryManager,
    MemoryRetriever,
    SQLiteMemoryStore,
)
from harness.memory.base import (
    AdmissionAction,
    AdmissionDecision,
    MemoryEntry,
    MemorySource,
    MemoryStatus,
    MemoryType,
)
from harness.memory.embeddings import (
    DeterministicFakeEmbeddingProvider,
    MemoryEmbeddingIndexer,
)
from harness.memory.strategies import (
    BM25FTS5Strategy,
    DenseSemanticStrategy,
    HybridRRFStrategy,
    LexicalOverlapStrategy,
)
from harness.tools.registry import ToolRegistry
from harness.tools.workspace import Workspace


class TestMemoryConfiguration(unittest.TestCase):
    """Verify strongly typed configuration loading, default freezing, and validation."""

    def test_production_yaml_defaults(self) -> None:
        """Loading config/config.yaml yields the calibrated production hybrid defaults."""
        config_path = Path("config/config.yaml")
        self.assertTrue(config_path.exists())
        cfg = load_config(config_path)

        self.assertTrue(cfg.memory.enabled)
        self.assertEqual(cfg.memory.retrieval_strategy, "hybrid_rrf")
        self.assertIsNone(cfg.memory.candidate_limit)

        # Embedding block
        self.assertEqual(cfg.memory.embedding.model, "Snowflake/snowflake-arctic-embed-s")
        self.assertEqual(cfg.memory.embedding.revision, "e596f507467533e48a2e17c007f0e1dacc837b33")
        self.assertEqual(cfg.memory.embedding.dimension, 384)
        self.assertEqual(cfg.memory.embedding.query_prompt_name, "query")
        self.assertEqual(cfg.memory.embedding.threshold, 0.55)
        self.assertEqual(cfg.memory.embedding.missing_embedding, "skip")
        self.assertEqual(cfg.memory.embedding.index_readiness, "incremental")

        # Sparse block
        self.assertEqual(cfg.memory.sparse.min_coverage, 0.35)
        self.assertEqual(cfg.memory.sparse.bm25_cutoff, -2.5)
        self.assertEqual(cfg.memory.sparse.stopword_policy, "v1-standard-english-34")

        # Hybrid block
        self.assertEqual(cfg.memory.hybrid.candidate_pool, 10)
        self.assertEqual(cfg.memory.hybrid.rrf_k, 10)
        self.assertEqual(cfg.memory.hybrid.sparse_weight, 1.0)
        self.assertEqual(cfg.memory.hybrid.dense_weight, 1.0)
        self.assertTrue(cfg.memory.hybrid.relevance_gate)

    def test_backward_compatible_flat_properties(self) -> None:
        """MemoryConfig properties map faithfully to nested sub-configurations."""
        cfg = MemoryConfig()
        self.assertEqual(cfg.dense_model, "Snowflake/snowflake-arctic-embed-s")
        self.assertEqual(cfg.dense_revision, "e596f507467533e48a2e17c007f0e1dacc837b33")
        self.assertEqual(cfg.dense_threshold, 0.55)
        self.assertEqual(cfg.index_readiness, "incremental")
        self.assertEqual(cfg.sparse_min_coverage, 0.35)
        self.assertEqual(cfg.sparse_bm25_cutoff, -2.5)
        self.assertEqual(cfg.sparse_stopword_policy_version, "v1-standard-english-34")
        self.assertEqual(cfg.rrf_k, 10)
        self.assertEqual(cfg.candidate_pool, 10)
        self.assertTrue(cfg.enable_relevance_gate)

    def test_invalid_retrieval_strategy_raises_value_error(self) -> None:
        """Unsupported retrieval strategies raise clean ValueError."""
        with tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False) as f:
            f.write(
                "agent: {max_steps: 10}\n"
                "llm: {base_url: 'http://test', model: 'test', temperature: 0.0}\n"
                "tools: {workspace_root: './workspace'}\n"
                "memory: {enabled: true, retrieval_strategy: 'unsupported_strategy'}\n"
            )
            tmp_name = f.name
        try:
            with self.assertRaises(ValueError) as ctx:
                load_config(tmp_name)
            self.assertIn("Invalid retrieval_strategy", str(ctx.exception))
        finally:
            Path(tmp_name).unlink(missing_ok=True)

    def test_invalid_index_readiness_raises_value_error(self) -> None:
        """Unsupported index readiness values raise clean ValueError."""
        with tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False) as f:
            f.write(
                "agent: {max_steps: 10}\n"
                "llm: {base_url: 'http://test', model: 'test', temperature: 0.0}\n"
                "tools: {workspace_root: './workspace'}\n"
                "memory: {enabled: true, embedding: {index_readiness: 'unsupported'}}\n"
            )
            tmp_name = f.name
        try:
            with self.assertRaises(ValueError) as ctx:
                load_config(tmp_name)
            self.assertIn("index_readiness", str(ctx.exception))
        finally:
            Path(tmp_name).unlink(missing_ok=True)

    def test_env_var_overrides(self) -> None:
        """Environment variables override YAML settings."""
        with tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False) as f:
            f.write(
                "agent: {max_steps: 10}\n"
                "llm: {base_url: 'http://test', model: 'test', temperature: 0.0}\n"
                "tools: {workspace_root: './workspace'}\n"
                "memory: {enabled: true}\n"
            )
            tmp_name = f.name

        try:
            with patch.dict(os.environ, {
                "MEMORY_RETRIEVAL_STRATEGY": "bm25",
                "MEMORY_INDEX_READINESS": "sparse_only",
                "MEMORY_DENSE_MODEL": "BAAI/bge-small-en-v1.5",
            }):
                cfg = load_config(tmp_name)
                self.assertEqual(cfg.memory.retrieval_strategy, "bm25")
                self.assertEqual(cfg.memory.embedding.index_readiness, "sparse_only")
                self.assertEqual(cfg.memory.embedding.model, "BAAI/bge-small-en-v1.5")
        finally:
            Path(tmp_name).unlink(missing_ok=True)


class TestControllerMemoryWiring(unittest.TestCase):
    """Verify build_controller() correctly constructs strategies, indexers, and readiness policies."""

    def setUp(self) -> None:
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp_dir.name) / "test_memory.db"
        self.workspace_root = Path(self.tmp_dir.name) / "workspace"
        self.workspace_root.mkdir(parents=True, exist_ok=True)

    def tearDown(self) -> None:
        self.tmp_dir.cleanup()

    def _base_app_config(self, memory_cfg: MemoryConfig) -> AppConfig:
        return AppConfig(
            agent=AgentConfig(max_steps=5),
            llm=LLMConfig(base_url="https://mock", model="test-model", temperature=0.0),
            tools=ToolsConfig(workspace_root=str(self.workspace_root)),
            memory=memory_cfg,
        )

    @patch("harness.cli.SentenceTransformerEmbeddingProvider")
    def test_build_controller_wires_hybrid_rrf(self, mock_provider_cls: MagicMock) -> None:
        """Hybrid strategy wires BM25, DenseSemanticStrategy, and HybridRRFStrategy."""
        mock_provider = DeterministicFakeEmbeddingProvider(dimension=384)
        mock_provider_cls.return_value = mock_provider

        mem_cfg = MemoryConfig(
            enabled=True,
            storage_path=str(self.db_path),
            retrieval_strategy="hybrid_rrf",
            candidate_limit=None,
            embedding=MemoryEmbeddingConfig(index_readiness="incremental"),
        )
        app_cfg = self._base_app_config(mem_cfg)

        controller, ws, reg = build_controller(app_cfg, api_key="dummy-key")
        self.assertIsNotNone(controller.memory_manager)
        retriever = controller.memory_manager.retriever
        self.assertIsInstance(retriever.strategy, HybridRRFStrategy)
        self.assertEqual(retriever.strategy.rrf_k, 10)
        self.assertTrue(retriever.strategy.enable_relevance_gate)
        self.assertIsNone(retriever.candidate_limit)
        self.assertIsNotNone(controller.memory_manager.indexer)
        controller.memory_manager.store.close()

    @patch("harness.cli.SentenceTransformerEmbeddingProvider")
    def test_build_controller_sparse_only_never_instantiates_provider(
        self, mock_provider_cls: MagicMock
    ) -> None:
        """Under sparse_only, SentenceTransformerEmbeddingProvider is NEVER constructed."""
        mem_cfg = MemoryConfig(
            enabled=True,
            storage_path=str(self.db_path),
            retrieval_strategy="hybrid_rrf",
            embedding=MemoryEmbeddingConfig(index_readiness="sparse_only"),
        )
        app_cfg = self._base_app_config(mem_cfg)

        controller, ws, reg = build_controller(app_cfg, api_key="dummy-key")
        mock_provider_cls.assert_not_called()
        self.assertIsNotNone(controller.memory_manager)
        # Strategy falls back to sparse BM25 with zero neural loading
        self.assertIsInstance(controller.memory_manager.retriever.strategy, BM25FTS5Strategy)
        self.assertIsNone(controller.memory_manager.indexer)
        controller.memory_manager.store.close()

    @patch("harness.cli.SentenceTransformerEmbeddingProvider")
    def test_build_controller_eager_backfills_store(self, mock_provider_cls: MagicMock) -> None:
        """Under eager readiness, build_controller() immediately indexes existing eligible memories."""
        # Pre-populate database with an eligible memory
        store = SQLiteMemoryStore(self.db_path)
        entry = MemoryEntry(
            id="mem_pre_existing",
            created_at="2026-09-01T00:00:00+00:00",
            content="Pre-existing infrastructure fact",
            source=MemorySource.USER_INPUT,
            status=MemoryStatus.ACCEPTED,
            normalized_content="pre existing infrastructure fact",
        )
        store.add(entry)
        self.assertEqual(store.count_embeddings(), 0)
        store.close()

        fake_provider = DeterministicFakeEmbeddingProvider(dimension=384)
        mock_provider_cls.return_value = fake_provider

        mem_cfg = MemoryConfig(
            enabled=True,
            storage_path=str(self.db_path),
            retrieval_strategy="hybrid_rrf",
            embedding=MemoryEmbeddingConfig(index_readiness="eager"),
        )
        app_cfg = self._base_app_config(mem_cfg)

        controller, ws, reg = build_controller(app_cfg, api_key="dummy-key")
        # Eager readiness backfills the database on boot
        self.assertEqual(controller.memory_manager.store.count_embeddings(), 1)
        rec = controller.memory_manager.store.get_embedding("mem_pre_existing", fake_provider.model_fingerprint)
        self.assertIsNotNone(rec)
        controller.memory_manager.store.close()


class TestMemoryManagerIncrementalLifecycle(unittest.TestCase):
    """Verify incremental indexing on write and decoupling of embedding failures."""

    def setUp(self) -> None:
        self.store = SQLiteMemoryStore(":memory:")
        self.provider = DeterministicFakeEmbeddingProvider(dimension=384)
        self.indexer = MemoryEmbeddingIndexer(self.store, self.provider)
        self.admission = BaselineAdmissionPolicy(self.store)
        self.retriever = MemoryRetriever(self.store, strategy=BM25FTS5Strategy(self.store))

    def tearDown(self) -> None:
        self.store.close()

    def test_incremental_indexes_accepted_declarative_memory(self) -> None:
        """Admitting an ACCEPTED declarative memory indexes it into SQLite embeddings automatically."""
        manager = MemoryManager(
            store=self.store,
            admission_policy=self.admission,
            retriever=self.retriever,
            indexer=self.indexer,
        )
        decision = manager.admit_and_store(
            "Production server runs PostgreSQL 16 on port 5432.",
            source=MemorySource.USER_INPUT,
        )
        self.assertTrue(decision.admitted)
        self.assertEqual(self.store.count(), 1)
        self.assertEqual(self.store.count_fts(), 1)
        self.assertEqual(self.store.count_embeddings(), 1)

    def test_incremental_does_not_index_procedural_or_quarantined(self) -> None:
        """Quarantined inputs are not embedded in vector store."""
        class QuarantinePolicy:
            def evaluate(self, content, source, metadata):
                return AdmissionDecision(action=AdmissionAction.QUARANTINE, admitted=True, reason="Untrusted")

        manager = MemoryManager(
            store=self.store,
            admission_policy=QuarantinePolicy(),
            retriever=self.retriever,
            indexer=self.indexer,
        )
        decision = manager.admit_and_store(
            "Suspicious shell command suggestion",
            source=MemorySource.TOOL_OBSERVATION,
        )
        self.assertTrue(decision.admitted)
        self.assertEqual(self.store.count(status=MemoryStatus.QUARANTINED), 1)
        # FTS and embeddings must NOT index quarantined memories
        self.assertEqual(self.store.count_fts(), 0)
        self.assertEqual(self.store.count_embeddings(), 0)

    def test_embedding_failure_does_not_rollback_memory_commit(self) -> None:
        """If embedding generation fails, memory and FTS index remain committed and BM25 can retrieve it."""
        faulty_indexer = MagicMock()
        faulty_indexer.ensure_embeddings.side_effect = RuntimeError("Neural network OOM error")

        manager = MemoryManager(
            store=self.store,
            admission_policy=self.admission,
            retriever=self.retriever,
            indexer=faulty_indexer,
        )

        # Must NOT raise exception despite neural failure
        decision = manager.admit_and_store(
            "Critical fallback secret passcode is DELTA-99.",
            source=MemorySource.USER_INPUT,
        )
        self.assertTrue(decision.admitted)

        # Memory is safely persisted in SQLite
        self.assertEqual(self.store.count(), 1)
        self.assertEqual(self.store.count_fts(), 1)
        self.assertEqual(self.store.count_embeddings(), 0)  # No vector created

        # BM25 is fully functional and retrieves the entry
        res = manager.retrieve("passcode")
        self.assertEqual(len(res), 1)
        self.assertIn("DELTA-99", res[0].content)


class TestCliObservability(unittest.TestCase):
    """Verify safe, concise CLI observability (/config and /memory)."""

    def test_print_config_shows_concise_memory_summary(self) -> None:
        """print_config() shows hybrid strategy, model, readiness, and candidate scope."""
        cfg = load_config(Path("config/config.yaml"))
        ws = Workspace(Path("./workspace"))

        buf = io.StringIO()
        with patch("sys.stdout", buf):
            print_config(cfg, ws)
        output = buf.getvalue()

        self.assertIn("Memory Retrieval : hybrid", output)
        self.assertIn("Semantic Encoder : snowflake-arctic-embed-s", output)
        self.assertIn("Index Policy     : incremental", output)
        self.assertIn("Candidate Scope  : full", output)

    def test_print_memory_shows_cache_and_lifecycle_counts(self) -> None:
        """print_memory() shows dense cache ratio vs active declarative count."""
        cfg = load_config(Path("config/config.yaml"))
        buf = io.StringIO()
        with patch("sys.stdout", buf):
            print_memory(cfg)
        output = buf.getvalue()

        self.assertIn("Memory Retrieval     : hybrid", output)
        self.assertIn("Semantic Encoder     : snowflake-arctic-embed-s", output)
        self.assertIn("Index Policy         : incremental", output)
        self.assertIn("Dense Cache          :", output)
        self.assertIn("Candidate Scope      : full", output)


if __name__ == "__main__":
    unittest.main()
