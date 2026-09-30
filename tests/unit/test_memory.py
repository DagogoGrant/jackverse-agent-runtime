"""Unit and integration tests for Phase 3A: Minimal Persistent Long-Term Memory (LTM)."""

import os
from pathlib import Path
import tempfile
from typing import Any
import unittest
from unittest.mock import MagicMock

from harness.agent.budget import ExecutionBudget
from harness.agent.react import ReActController
from harness.cli import build_controller
from harness.config import (
    AgentConfig,
    AppConfig,
    LLMConfig,
    MemoryConfig,
    ToolsConfig,
    load_config,
)
from harness.llm.client import LLMResponse, ToolCall
from harness.memory.admission import BaselineAdmissionPolicy
from harness.memory.base import (
    AdmissionDecision,
    MemoryEntry,
    MemorySource,
    MemoryStore,
)
from harness.memory.manager import MemoryManager
from harness.memory.retrieval import MemoryRetriever
from harness.memory.store import SQLiteMemoryStore
from harness.tools.base import Tool, ToolResult, ToolSpec
from harness.tools.executor import ToolExecutor
from harness.tools.registry import ToolRegistry


class DummyStationTool(Tool):
    """Simple test tool providing station observations."""

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="get_station_platform",
            description="Returns platform for a station",
            input_schema={"type": "object", "properties": {"station": {"type": "string"}}},
        )

    def execute(self, arguments: dict[str, Any]) -> ToolResult:
        station = arguments.get("station", "")
        return ToolResult(content=f"Platform 3 at {station}", is_error=False)


class TestMemoryPhase3A(unittest.TestCase):
    """Test suite covering all 15 required Phase 3A memory tests."""

    def test_01_sqlite_write_and_read(self) -> None:
        """1. SQLite write/read."""
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "test1.db"
            store = SQLiteMemoryStore(db_path)
            try:
                entry = MemoryEntry(
                    id="mem_1",
                    created_at="2026-09-08T08:00:00+00:00",
                    content="Departure from Passau Hbf at 08:30",
                    source=MemorySource.USER_INPUT,
                    metadata={"test_key": "val1"},
                )
                store.add(entry)
                self.assertEqual(store.count(), 1)

                read_entry = store.get("mem_1")
                self.assertIsNotNone(read_entry)
                self.assertEqual(read_entry.id, "mem_1")
                self.assertEqual(read_entry.created_at, "2026-09-08T08:00:00+00:00")
                self.assertEqual(read_entry.content, "Departure from Passau Hbf at 08:30")
                self.assertEqual(read_entry.source, MemorySource.USER_INPUT)
                self.assertEqual(read_entry.metadata.get("test_key"), "val1")
            finally:
                store.close()

    def test_02_persistence_after_closing_and_reopening(self) -> None:
        """2. Persistence after closing/reopening."""
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "test2.db"
            store_1 = SQLiteMemoryStore(db_path)
            entry = MemoryEntry(
                id="mem_persisted",
                created_at="2026-09-08T09:00:00+00:00",
                content="Persistent memory content across DB handles",
                source=MemorySource.USER_INPUT,
            )
            store_1.add(entry)
            self.assertEqual(store_1.count(), 1)
            store_1.close()

            # Open a second, independent connection to the same SQLite DB file
            store_2 = SQLiteMemoryStore(db_path)
            try:
                self.assertEqual(store_2.count(), 1)
                retrieved = store_2.get("mem_persisted")
                self.assertIsNotNone(retrieved)
                self.assertEqual(retrieved.content, "Persistent memory content across DB handles")
            finally:
                store_2.close()

    def test_03_exact_duplicate_rejection(self) -> None:
        """3. Exact duplicate rejection."""
        store = SQLiteMemoryStore(":memory:")
        try:
            policy = BaselineAdmissionPolicy(store, max_entry_chars=1000)
            existing = MemoryEntry(
                id="dup_1",
                created_at="2026-09-08T10:00:00",
                content="Identical content line",
                source=MemorySource.USER_INPUT,
            )
            store.add(existing)

            # User input duplicate
            dec_user = policy.evaluate("Identical content line", MemorySource.USER_INPUT)
            self.assertFalse(dec_user.admitted)
            self.assertIn("duplicate", dec_user.reason.lower())

            # Tool observation duplicate
            dec_tool = policy.evaluate(
                "Identical content line",
                MemorySource.TOOL_OBSERVATION,
                metadata={"is_error": False},
            )
            self.assertFalse(dec_tool.admitted)
            self.assertIn("duplicate", dec_tool.reason.lower())
        finally:
            store.close()

    def test_04_blank_rejection(self) -> None:
        """4. Blank rejection."""
        store = SQLiteMemoryStore(":memory:")
        try:
            policy = BaselineAdmissionPolicy(store)
            d1 = policy.evaluate("", MemorySource.USER_INPUT)
            self.assertFalse(d1.admitted)
            self.assertIn("empty", d1.reason.lower())

            d2 = policy.evaluate("   \n\t  ", MemorySource.TOOL_OBSERVATION)
            self.assertFalse(d2.admitted)
            self.assertIn("empty", d2.reason.lower())
        finally:
            store.close()

    def test_05_oversized_rejection(self) -> None:
        """5. Oversized rejection."""
        store = SQLiteMemoryStore(":memory:")
        try:
            policy = BaselineAdmissionPolicy(store, max_entry_chars=50)
            oversized_content = "X" * 51
            decision = policy.evaluate(oversized_content, MemorySource.USER_INPUT)
            self.assertFalse(decision.admitted)
            self.assertIn("exceeds maximum limit", decision.reason)
        finally:
            store.close()

    def test_06_successful_tool_observation_admitted(self) -> None:
        """6. Successful tool observation admitted."""
        store = SQLiteMemoryStore(":memory:")
        try:
            policy = BaselineAdmissionPolicy(store)
            decision = policy.evaluate(
                content="Platform 3 Passau Hbf",
                source=MemorySource.TOOL_OBSERVATION,
                metadata={"is_error": False, "tool_name": "get_platform"},
            )
            self.assertTrue(decision.admitted)
            self.assertIn("Valid tool observation", decision.reason)
        finally:
            store.close()

    def test_07_failed_tool_observation_rejected(self) -> None:
        """7. Failed tool observation rejected."""
        store = SQLiteMemoryStore(":memory:")
        try:
            policy = BaselineAdmissionPolicy(store)
            decision = policy.evaluate(
                content="Error executing tool: Station not found",
                source=MemorySource.TOOL_OBSERVATION,
                metadata={"is_error": True, "tool_name": "get_platform"},
            )
            self.assertFalse(decision.admitted)
            self.assertIn("error", decision.reason.lower())
        finally:
            store.close()

    def test_08_lexical_relevance_ranking(self) -> None:
        """8. Lexical relevance ranking."""
        store = SQLiteMemoryStore(":memory:")
        try:
            retriever = MemoryRetriever(store, max_retrieved=5, max_context_chars=1000)
            # 2 matching tokens: "passau", "train"
            store.add(MemoryEntry("1", "2026-09-08T01:00:00", "passau regional train", MemorySource.USER_INPUT))
            # 3 matching tokens: "passau", "train", "express"
            store.add(MemoryEntry("2", "2026-09-08T02:00:00", "passau express train connection", MemorySource.USER_INPUT))
            # 1 matching token: "train"
            store.add(MemoryEntry("3", "2026-09-08T03:00:00", "munich train schedule", MemorySource.USER_INPUT))

            results = retriever.retrieve("passau train express")
            result_ids = [r.id for r in results]
            self.assertEqual(result_ids[0], "2")  # 3 tokens match
            self.assertEqual(result_ids[1], "1")  # 2 tokens match
            self.assertEqual(result_ids[2], "3")  # 1 token match
        finally:
            store.close()

    def test_09_zero_overlap_query_returns_no_memories(self) -> None:
        """9. Zero-overlap query returns no memories."""
        store = SQLiteMemoryStore(":memory:")
        try:
            retriever = MemoryRetriever(store, max_retrieved=3)
            store.add(MemoryEntry("1", "2026-09-08T01:00:00", "Passau Bavaria Germany", MemorySource.USER_INPUT))
            store.add(MemoryEntry("2", "2026-09-08T02:00:00", "Regensburg Danube river", MemorySource.USER_INPUT))

            results = retriever.retrieve("Tokyo subway timetable")
            self.assertEqual(len(results), 0)
        finally:
            store.close()

    def test_10_top_k_limit(self) -> None:
        """10. Top-k limit."""
        store = SQLiteMemoryStore(":memory:")
        try:
            retriever = MemoryRetriever(store, max_retrieved=2)
            for i in range(5):
                store.add(MemoryEntry(str(i), f"2026-09-08T0{i}:00:00", f"station match item {i}", MemorySource.USER_INPUT))

            results = retriever.retrieve("station match")
            self.assertEqual(len(results), 2)
        finally:
            store.close()

    def test_11_character_budget_uses_complete_entries_only(self) -> None:
        """11. Character budget uses complete entries only."""
        store = SQLiteMemoryStore(":memory:")
        try:
            # Budget: 40 chars total
            retriever = MemoryRetriever(store, max_retrieved=5, max_context_chars=40)
            # Entry 1 has 30 characters (newer timestamp -> ranked first)
            e1 = MemoryEntry("1", "2026-09-08T02:00:00", "Short memory entry number one.", MemorySource.USER_INPUT)
            # Entry 2 has 25 characters (30 + 25 = 55 > 40)
            e2 = MemoryEntry("2", "2026-09-08T01:00:00", "Second memory entry line.", MemorySource.USER_INPUT)
            store.add(e1)
            store.add(e2)

            results = retriever.retrieve("memory entry")
            # Only Entry 1 should be returned. Entry 2 should be skipped entirely, never truncated.
            self.assertEqual(len(results), 1)
            self.assertEqual(results[0].id, "1")
            self.assertEqual(results[0].content, "Short memory entry number one.")

            # If even the first entry exceeds budget, it must be skipped, never truncated mid-content
            tight_retriever = MemoryRetriever(store, max_retrieved=5, max_context_chars=10)
            tight_results = tight_retriever.retrieve("memory entry")
            self.assertEqual(len(tight_results), 0)
        finally:
            store.close()

    def test_12_memory_disabled_leaves_controller_behavior_unchanged(self) -> None:
        """12. Memory disabled leaves controller behavior unchanged."""
        mock_llm = MagicMock()
        mock_llm.chat.return_value = LLMResponse(content="Baseline response without memory.")
        registry = ToolRegistry()
        executor = ToolExecutor()

        controller = ReActController(
            llm_client=mock_llm,
            tool_registry=registry,
            tool_executor=executor,
            memory_manager=None,
        )

        res = controller.run_turn("Hello world")
        self.assertTrue(res.is_success)
        self.assertEqual(res.final_text, "Baseline response without memory.")
        # Exact structure: [system, user, assistant]
        self.assertEqual(len(controller.context), 3)
        self.assertEqual(controller.context[0]["role"], "system")
        self.assertEqual(controller.context[1]["role"], "user")
        self.assertEqual(controller.context[2]["role"], "assistant")

    def test_13_recalled_memory_message_is_turn_scoped_and_does_not_accumulate(self) -> None:
        """13. Recalled-memory message is turn-scoped and does not accumulate."""
        store = SQLiteMemoryStore(":memory:")
        admission = BaselineAdmissionPolicy(store)
        retriever = MemoryRetriever(store)
        manager = MemoryManager(store, admission, retriever)

        try:
            # Seed long-term memory
            store.add(
                MemoryEntry(
                    id="seed_1",
                    created_at="2026-09-08T00:00:00",
                    content="User preference: Always travel by train.",
                    source=MemorySource.USER_INPUT,
                )
            )

            mock_llm = MagicMock()
            calls_received: list[list[dict[str, Any]]] = []

            def mock_chat(messages: list[dict[str, Any]], tools: Any = None) -> LLMResponse:
                # Deep copy messages seen by LLM
                calls_received.append([dict(m) for m in messages])
                return LLMResponse(content=f"Response for turn {len(calls_received)}")

            mock_llm.chat.side_effect = mock_chat
            registry = ToolRegistry()
            executor = ToolExecutor()

            controller = ReActController(
                llm_client=mock_llm,
                tool_registry=registry,
                tool_executor=executor,
                memory_manager=manager,
            )

            # Turn 1: query matches memory
            res1 = controller.run_turn("Can you book a train ticket?")
            self.assertTrue(res1.is_success)

            # Check that LLM received recalled memory in its turn context
            turn1_llm_messages = calls_received[0]
            has_recalled_in_llm_1 = any("[RECALLED MEMORY]" in m.get("content", "") for m in turn1_llm_messages)
            self.assertTrue(has_recalled_in_llm_1)

            # But controller.context must NOT contain recalled memory message!
            has_recalled_in_context_1 = any("[RECALLED MEMORY]" in m.get("content", "") for m in controller.context)
            self.assertFalse(has_recalled_in_context_1)
            self.assertEqual(len(controller.context), 3)  # system, user1, assistant1

            # Turn 2: another query matching memory
            res2 = controller.run_turn("What train options are there?")
            self.assertTrue(res2.is_success)

            # Turn 2 LLM saw recalled memory for turn 2
            turn2_llm_messages = calls_received[1]
            has_recalled_in_llm_2 = any("[RECALLED MEMORY]" in m.get("content", "") for m in turn2_llm_messages)
            self.assertTrue(has_recalled_in_llm_2)

            # But controller.context still must NOT accumulate any [RECALLED MEMORY] messages!
            has_recalled_in_context_2 = any("[RECALLED MEMORY]" in m.get("content", "") for m in controller.context)
            self.assertFalse(has_recalled_in_context_2)
            self.assertEqual(len(controller.context), 5)  # system, user1, assistant1, user2, assistant2

            # Permanent system prompt remains untouched
            self.assertEqual(controller.context[0]["role"], "system")
            self.assertNotIn("[RECALLED MEMORY]", controller.context[0]["content"])
        finally:
            manager.close()

    def test_14_successful_tool_observations_not_stored_twice(self) -> None:
        """14. Successful tool observations are not stored twice."""
        store = SQLiteMemoryStore(":memory:")
        admission = BaselineAdmissionPolicy(store)
        retriever = MemoryRetriever(store)
        manager = MemoryManager(store, admission, retriever)

        try:
            registry = ToolRegistry()
            registry.register(DummyStationTool())
            executor = ToolExecutor()

            mock_llm = MagicMock()
            step_count = 0

            def mock_chat(messages: list[dict[str, Any]], tools: Any = None) -> LLMResponse:
                nonlocal step_count
                step_count += 1
                if step_count == 1:
                    # Request tool call
                    return LLMResponse(
                        content="Checking station...",
                        tool_calls=[ToolCall(id="call_1", name="get_station_platform", arguments={"station": "Passau Hbf"})],
                    )
                return LLMResponse(content="Platform 3 found.")

            mock_llm.chat.side_effect = mock_chat

            controller = ReActController(
                llm_client=mock_llm,
                tool_registry=registry,
                tool_executor=executor,
                memory_manager=manager,
            )

            res = controller.run_turn("Which platform at Passau?")
            self.assertTrue(res.is_success)

            # Observation "Platform 3 at Passau Hbf" should be stored once
            platform_entries = [e for e in store.list_all() if "Platform 3 at Passau Hbf" in e.content]
            self.assertEqual(len(platform_entries), 1)

            # Run a second turn where the tool executes again and returns identical output
            step_count = 0
            res2 = controller.run_turn("Check the platform again.")
            self.assertTrue(res2.is_success)

            # Verify it is still stored only once (duplicate rejected by admission policy)
            platform_entries_after = [e for e in store.list_all() if "Platform 3 at Passau Hbf" in e.content]
            self.assertEqual(len(platform_entries_after), 1)
        finally:
            manager.close()

    def test_15_cross_session_memory_persistence_e2e(self) -> None:
        """15. Cross-session end-to-end memory test.

        Session A: "My preferred departure station is Passau Hbf."
        destroy controller/store.

        Session B, using the same SQLite DB:
        "What is my preferred departure station?"

        Verify:
        - persisted memory is read from SQLite
        - memory is injected into the LLM context
        - deterministic mock LLM answers "Passau Hbf"
        """
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "cross_session.db"

            # === SESSION A ===
            store_a = SQLiteMemoryStore(db_path)
            admission_a = BaselineAdmissionPolicy(store_a)
            retriever_a = MemoryRetriever(store_a)
            manager_a = MemoryManager(store_a, admission_a, retriever_a)

            mock_llm_a = MagicMock()
            mock_llm_a.chat.return_value = LLMResponse(content="Understood, I will remember Passau Hbf.")
            registry_a = ToolRegistry()
            executor_a = ToolExecutor()

            controller_a = ReActController(
                llm_client=mock_llm_a,
                tool_registry=registry_a,
                tool_executor=executor_a,
                memory_manager=manager_a,
            )

            user_input_a = "My preferred departure station is Passau Hbf."
            result_a = controller_a.run_turn(user_input_a)
            self.assertTrue(result_a.is_success)

            # Verify memory was stored in SQLite
            self.assertEqual(store_a.count(), 1)
            stored = store_a.list_all()[0]
            self.assertIn("Passau Hbf", stored.content)

            # Destroy Session A completely
            manager_a.close()
            del controller_a
            del manager_a
            del store_a

            # === SESSION B ===
            # Recreate new store and controller pointing to the same SQLite DB
            store_b = SQLiteMemoryStore(db_path)
            admission_b = BaselineAdmissionPolicy(store_b)
            retriever_b = MemoryRetriever(store_b)
            manager_b = MemoryManager(store_b, admission_b, retriever_b)

            mock_llm_b = MagicMock()

            def mock_chat_b(messages: list[dict[str, Any]], tools: Any = None) -> LLMResponse:
                context_texts = [m["content"] for m in messages if m.get("content")]
                has_recalled_memory = any("[RECALLED MEMORY]" in t and "Passau Hbf" in t for t in context_texts)
                if not has_recalled_memory:
                    return LLMResponse(content="I do not have any memory of your preferred station.")
                return LLMResponse(content="Passau Hbf")

            mock_llm_b.chat.side_effect = mock_chat_b
            registry_b = ToolRegistry()
            executor_b = ToolExecutor()

            controller_b = ReActController(
                llm_client=mock_llm_b,
                tool_registry=registry_b,
                tool_executor=executor_b,
                memory_manager=manager_b,
            )

            user_input_b = "What is my preferred departure station?"
            result_b = controller_b.run_turn(user_input_b)

            self.assertTrue(result_b.is_success)
            self.assertEqual(result_b.final_text, "Passau Hbf")

            # Verify controller_b context is clean and does not accumulate recalled memory
            self.assertEqual(controller_b.context[0]["role"], "system")
            self.assertEqual(controller_b.context[1]["role"], "user")
            self.assertEqual(controller_b.context[1]["content"], user_input_b)
            self.assertEqual(controller_b.context[2]["role"], "assistant")
            self.assertEqual(controller_b.context[2]["content"], "Passau Hbf")
            self.assertFalse(any("[RECALLED MEMORY]" in m["content"] for m in controller_b.context))

            manager_b.close()


class TestMemoryConfigValidation(unittest.TestCase):
    """Test YAML loading and validation for MemoryConfig."""

    def test_memory_config_defaults(self) -> None:
        """Verify default configuration has memory disabled."""
        cfg = MemoryConfig()
        self.assertFalse(cfg.enabled)
        self.assertEqual(cfg.storage_path, ".agent_memory/memory.db")
        self.assertEqual(cfg.max_retrieved, 3)
        self.assertEqual(cfg.max_context_chars, 2000)
        self.assertEqual(cfg.max_entry_chars, 4000)

    def test_yaml_config_memory_parsing(self) -> None:
        """Verify memory configuration loads from YAML."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            f.write(
                """
                agent:
                  max_steps: 5
                llm:
                  base_url: "http://mock"
                  model: "test"
                  temperature: 0.0
                tools:
                  workspace_root: "./workspace"
                memory:
                  enabled: true
                  storage_path: "./custom_memory/db.sqlite"
                  max_retrieved: 4
                  max_context_chars: 1500
                  max_entry_chars: 3000
                """
            )
            temp_path = f.name

        try:
            app_cfg = load_config(temp_path)
            self.assertTrue(app_cfg.memory.enabled)
            self.assertEqual(app_cfg.memory.storage_path, "./custom_memory/db.sqlite")
            self.assertEqual(app_cfg.memory.max_retrieved, 4)
            self.assertEqual(app_cfg.memory.max_context_chars, 1500)
            self.assertEqual(app_cfg.memory.max_entry_chars, 3000)
        finally:
            os.remove(temp_path)

    def test_yaml_config_invalid_memory_rejected(self) -> None:
        """Verify negative bounds are rejected."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            f.write(
                """
                agent:
                  max_steps: 5
                llm:
                  base_url: "http://mock"
                  model: "test"
                  temperature: 0.0
                tools:
                  workspace_root: "./workspace"
                memory:
                  enabled: true
                  max_retrieved: -1
                """
            )
            temp_path = f.name

        try:
            with self.assertRaises(ValueError) as cm:
                load_config(temp_path)
            self.assertIn("memory.max_retrieved", str(cm.exception))
        finally:
            os.remove(temp_path)

    def test_memory_disabled_does_not_create_storage_artifacts(self) -> None:
        """Verify that when memory is disabled, no storage directory or database file is created."""
        with tempfile.TemporaryDirectory() as temp_dir:
            test_storage_dir = Path(temp_dir) / "should_not_exist"
            test_storage_file = test_storage_dir / "memory.db"

            cfg = AppConfig(
                agent=AgentConfig(max_steps=5),
                llm=LLMConfig(base_url="http://mock", model="test", temperature=0.0),
                tools=ToolsConfig(workspace_root=temp_dir),
                memory=MemoryConfig(enabled=False, storage_path=str(test_storage_file)),
            )

            controller, _, _ = build_controller(cfg, api_key="dummy-key")
            # Controller runs without memory manager
            self.assertIsNone(controller.memory_manager)

            # Neither directory nor database file exists on disk
            self.assertFalse(test_storage_dir.exists())
            self.assertFalse(test_storage_file.exists())


if __name__ == "__main__":
    unittest.main()
