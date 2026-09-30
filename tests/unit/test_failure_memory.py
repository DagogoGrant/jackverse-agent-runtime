"""Unit and scenario tests for Phase 3D: Failure Memory / Procedural Recovery.

Evaluates:
1. Deterministic attribution of schema and contract violations.
2. Verified recovery detection (same tool, delta verified, non-transient, step_succ > step_fail).
3. Negative controls (transient 503, timeout, unrelated tool, unrecovered, coincidental success).
4. Privacy redaction (no raw arguments, secrets, or observation payloads).
5. Declarative retrieval isolation and character bounds.
6. Cross-session ReAct loop with identical mock LLM policy proving failure avoidance.
"""

from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
import json
import sqlite3
import tempfile
from typing import Any
import unittest

from harness.agent.budget import ExecutionBudget, TerminationReason
from harness.agent.react import ReActController
from harness.llm.client import LLMResponse, ToolCall
from harness.memory.admission import BaselineAdmissionPolicy
from harness.memory.base import (
    AdmissionAction,
    MemoryEntry,
    MemorySource,
    MemoryStatus,
    MemoryType,
)
from harness.memory.firewall import MemoryFirewall
from harness.memory.manager import MemoryManager
from harness.memory.recovery import (
    ProceduralLesson,
    RecoveryDetector,
    ToolAttempt,
)
from harness.memory.retrieval import MemoryRetriever
from harness.memory.store import SQLiteMemoryStore
from harness.tools.base import ErrorCode, Tool, ToolResult, ToolSpec
from harness.tools.executor import ToolExecutor
from harness.tools.registry import ToolRegistry


class DummyMockTool(Tool):
    """Configurable mock tool for testing failure memory and procedural recovery."""

    def __init__(
        self,
        spec: ToolSpec,
        handler: Any = None,
    ) -> None:
        self._spec = spec
        self._handler = handler
        self.invocations: list[dict[str, Any]] = []

    @property
    def spec(self) -> ToolSpec:
        return self._spec

    def execute(self, arguments: Mapping[str, Any]) -> ToolResult:
        self.invocations.append(dict(arguments))
        if self._handler:
            return self._handler(arguments)
        return ToolResult(content="Success", is_error=False)


class PolicyMockLLMClient:
    """Deterministic Mock LLM that uses an identical decision policy for Baseline and Treatment.

    Policy:
    - Checks system context for '[PAST TOOL EXPERIENCE]'.
    - If experience provides a rule for the tool/param, uses the compliant argument format on step 1.
    - If no experience is present, uses naive/default argument format on step 1.
    - If a tool error is observed in context, in-turn retries with the compliant format.
    - Once tool succeeds, outputs final answer.
    """

    def __init__(self, tool_to_call: str, compliant_args: dict[str, Any], naive_args: dict[str, Any], experience_keyword: str) -> None:
        self.tool_to_call = tool_to_call
        self.compliant_args = compliant_args
        self.naive_args = naive_args
        self.experience_keyword = experience_keyword
        self.call_count = 0

    def chat(
        self,
        messages: Sequence[dict[str, Any]],
        tools: Sequence[ToolSpec] | None = None,
    ) -> LLMResponse:
        self.call_count += 1

        # Check if the latest tool observation is in messages
        last_tool_msg = None
        for m in reversed(messages):
            if m.get("role") == "tool":
                last_tool_msg = m
                break

        if last_tool_msg is not None:
            content = str(last_tool_msg.get("content", ""))
            is_err = any(w in content.lower() for w in ("error", "invalid", "missing", "must be", "unexpected", "unknown"))
            if not is_err:
                return LLMResponse(content="Operation completed successfully.", tool_calls=[])
            else:
                # In-turn recovery: tool failed, retry with compliant args
                return LLMResponse(
                    content=None,
                    tool_calls=[
                        ToolCall(id=f"call_{self.call_count}", name=self.tool_to_call, arguments=dict(self.compliant_args))
                    ],
                )

        # First step: inspect system prompt for past tool experience
        has_experience = False
        for m in messages:
            if m.get("role") == "system" and "[PAST TOOL EXPERIENCE]" in m.get("content", ""):
                if self.experience_keyword in m.get("content", ""):
                    has_experience = True
                    break

        args = dict(self.compliant_args) if has_experience else dict(self.naive_args)
        return LLMResponse(
            content=None,
            tool_calls=[
                ToolCall(id=f"call_{self.call_count}", name=self.tool_to_call, arguments=args)
            ],
        )


class TestRecoveryDetectorUnit(unittest.TestCase):
    """Unit tests for RecoveryDetector: positive recovery scenarios and negative controls."""

    def setUp(self) -> None:
        self.detector = RecoveryDetector()

    # --- 4 Positive Recovery Scenarios ---

    def test_positive_01_iso_datetime_recovery(self) -> None:
        """Scenario 1: ISO 8601 datetime format error recovered by ISO format."""
        failed = ToolAttempt(
            tool_name="find_connection",
            arguments={"origin": "Passau Hbf", "destination": "München Hbf", "departure_time": "tomorrow 2pm"},
            result=ToolResult(
                content="Invalid departure_time format 'tomorrow 2pm'. Expected ISO 8601 string (e.g. 'YYYY-MM-DDTHH:MM:SS').",
                is_error=True,
                error_code=ErrorCode.INVALID_ARGUMENT,
            ),
            step=1,
        )
        self.detector.record_attempt(failed)

        recovered = ToolAttempt(
            tool_name="find_connection",
            arguments={"origin": "Passau Hbf", "destination": "München Hbf", "departure_time": "2026-09-08T14:00:00Z"},
            result=ToolResult(content="Found connection ICE 28.", is_error=False),
            step=2,
        )
        lesson = self.detector.detect_recovery(recovered)

        self.assertIsNotNone(lesson)
        self.assertEqual(lesson.tool_name, "find_connection")
        self.assertEqual(lesson.failing_parameter, "departure_time")
        self.assertEqual(lesson.constraint, "iso_8601_datetime")
        self.assertIn("requires ISO-8601 datetime format", lesson.lesson)
        self.assertEqual(lesson.provenance["failed_step"], 1)
        self.assertEqual(lesson.provenance["successful_step"], 2)

    def test_positive_02_missing_required_param_recovery(self) -> None:
        """Scenario 2: Missing required argument 'query' recovered by supplying 'query'."""
        failed = ToolAttempt(
            tool_name="search_stations",
            arguments={"max_results": 3},
            result=ToolResult(
                content="Schema validation error: Missing required argument 'query'.",
                is_error=True,
                error_code=ErrorCode.INVALID_ARGUMENT,
            ),
            step=1,
        )
        self.detector.record_attempt(failed)

        recovered = ToolAttempt(
            tool_name="search_stations",
            arguments={"query": "Köln", "max_results": 3},
            result=ToolResult(content="Found Köln Hbf.", is_error=False),
            step=2,
        )
        lesson = self.detector.detect_recovery(recovered)

        self.assertIsNotNone(lesson)
        self.assertEqual(lesson.tool_name, "search_stations")
        self.assertEqual(lesson.failing_parameter, "query")
        self.assertEqual(lesson.constraint, "required")
        self.assertIn("requires parameter 'query'", lesson.lesson)

    def test_positive_03_bounded_integer_recovery(self) -> None:
        """Scenario 3: Parameter 'max_results' out of bounds recovered by bounded value."""
        failed = ToolAttempt(
            tool_name="search_stations",
            arguments={"query": "Passau", "max_results": 10},
            result=ToolResult(
                content="Parameter 'max_results' must be an integer between 1 and 5.",
                is_error=True,
                error_code=ErrorCode.BOUNDARY_VIOLATION,
            ),
            step=1,
        )
        self.detector.record_attempt(failed)

        recovered = ToolAttempt(
            tool_name="search_stations",
            arguments={"query": "Passau", "max_results": 5},
            result=ToolResult(content="Found Passau Hbf.", is_error=False),
            step=2,
        )
        lesson = self.detector.detect_recovery(recovered)

        self.assertIsNotNone(lesson)
        self.assertEqual(lesson.tool_name, "search_stations")
        self.assertEqual(lesson.failing_parameter, "max_results")
        self.assertEqual(lesson.constraint, "bounds_1_to_5")
        self.assertIn("must be an integer between 1 and 5", lesson.lesson)

    def test_positive_04_type_mismatch_recovery(self) -> None:
        """Scenario 4: Argument type mismatch recovered by supplying correct type."""
        failed = ToolAttempt(
            tool_name="get_weather",
            arguments={"station": "Passau", "days": "3"},
            result=ToolResult(
                content="Schema validation error: Argument 'days' must be an integer, got str.",
                is_error=True,
                error_code=ErrorCode.INVALID_ARGUMENT,
            ),
            step=1,
        )
        self.detector.record_attempt(failed)

        recovered = ToolAttempt(
            tool_name="get_weather",
            arguments={"station": "Passau", "days": 3},
            result=ToolResult(content="Weather: sunny 18C.", is_error=False),
            step=2,
        )
        lesson = self.detector.detect_recovery(recovered)

        self.assertIsNotNone(lesson)
        self.assertEqual(lesson.tool_name, "get_weather")
        self.assertEqual(lesson.failing_parameter, "days")
        self.assertEqual(lesson.constraint, "type_mismatch")
        self.assertIn("must match expected schema type", lesson.lesson)

    # --- 5 Negative Controls ---

    def test_negative_01_transient_503_error(self) -> None:
        """Negative Control 1: HTTP 503 / Service Unavailable must NOT create procedural rule."""
        failed = ToolAttempt(
            tool_name="find_connection",
            arguments={"origin": "Passau", "destination": "Köln"},
            result=ToolResult(
                content="Service unavailable (503): Backend connection gateway overloaded.",
                is_error=True,
                error_code=ErrorCode.TRANSIENT_ERROR,
            ),
            step=1,
        )
        self.detector.record_attempt(failed)

        recovered = ToolAttempt(
            tool_name="find_connection",
            arguments={"origin": "Passau", "destination": "Köln"},
            result=ToolResult(content="Found connection ICE 91.", is_error=False),
            step=2,
        )
        lesson = self.detector.detect_recovery(recovered)
        self.assertIsNone(lesson, "Transient 503 error must not synthesize a procedural lesson.")

    def test_negative_02_timeout_error(self) -> None:
        """Negative Control 2: Connection timeout must NOT create procedural rule."""
        failed = ToolAttempt(
            tool_name="find_connection",
            arguments={"origin": "Passau", "destination": "Köln"},
            result=ToolResult(
                content="Network request timed out after 10000ms.",
                is_error=True,
                error_code=ErrorCode.TRANSIENT_ERROR,
            ),
            step=1,
        )
        self.detector.record_attempt(failed)

        recovered = ToolAttempt(
            tool_name="find_connection",
            arguments={"origin": "Passau", "destination": "Köln"},
            result=ToolResult(content="Found connection ICE 91.", is_error=False),
            step=2,
        )
        lesson = self.detector.detect_recovery(recovered)
        self.assertIsNone(lesson, "Timeout error must not synthesize a procedural lesson.")

    def test_negative_03_unrelated_tool_success(self) -> None:
        """Negative Control 3: Success on Tool B must NOT recover failure on Tool A."""
        failed = ToolAttempt(
            tool_name="find_connection",
            arguments={"origin": "Passau"},
            result=ToolResult(
                content="Schema validation error: Missing required argument 'destination'.",
                is_error=True,
                error_code=ErrorCode.INVALID_ARGUMENT,
            ),
            step=1,
        )
        self.detector.record_attempt(failed)

        # Tool B succeeds
        succ_other_tool = ToolAttempt(
            tool_name="get_weather",
            arguments={"station": "Passau"},
            result=ToolResult(content="Sunny 18C.", is_error=False),
            step=2,
        )
        lesson = self.detector.detect_recovery(succ_other_tool)
        self.assertIsNone(lesson, "Success of unrelated tool must not synthesize a procedural lesson.")

    def test_negative_04_unrecovered_failure(self) -> None:
        """Negative Control 4: Unrecovered failure without a subsequent success must not persist a lesson."""
        failed = ToolAttempt(
            tool_name="find_connection",
            arguments={"departure_time": "invalid_date"},
            result=ToolResult(
                content="Invalid departure_time format 'invalid_date'. Expected ISO 8601 string (e.g. 'YYYY-MM-DDTHH:MM:SS').",
                is_error=True,
                error_code=ErrorCode.INVALID_ARGUMENT,
            ),
            step=1,
        )
        self.detector.record_attempt(failed)

        # Turn finishes without any successful call
        self.assertEqual(len(self.detector._failed_attempts), 1)
        # Attempting recovery detection on an error attempt returns None
        subsequent_failure = ToolAttempt(
            tool_name="find_connection",
            arguments={"departure_time": "still_invalid"},
            result=ToolResult(content="Invalid departure_time format", is_error=True),
            step=2,
        )
        self.assertIsNone(self.detector.detect_recovery(subsequent_failure))

    def test_negative_05_coincidental_success_unchanged_param(self) -> None:
        """Negative Control 5: Coincidental success where failing argument was NOT changed must NOT synthesize lesson."""
        # Tool failed on max_results out of bounds
        failed = ToolAttempt(
            tool_name="search_stations",
            arguments={"query": "Passau", "max_results": 10},
            result=ToolResult(
                content="Parameter 'max_results' must be an integer between 1 and 5.",
                is_error=True,
                error_code=ErrorCode.BOUNDARY_VIOLATION,
            ),
            step=1,
        )
        self.detector.record_attempt(failed)

        # Second call unexpectedly succeeds but still has max_results=10 (e.g. coincidental or cached)
        coincidental_succ = ToolAttempt(
            tool_name="search_stations",
            arguments={"query": "Köln", "max_results": 10},  # max_results NOT changed!
            result=ToolResult(content="Found Köln.", is_error=False),
            step=2,
        )
        lesson = self.detector.detect_recovery(coincidental_succ)
        self.assertIsNone(lesson, "Coincidental success without changing failing param must not synthesize lesson.")


class TestProceduralPrivacyAndRedaction(unittest.TestCase):
    """Verifies privacy protection: secrets and raw arguments are never stored in procedural lessons."""

    def test_privacy_redaction_secrets_not_persisted(self) -> None:
        detector = RecoveryDetector()
        failed = ToolAttempt(
            tool_name="find_connection",
            arguments={
                "origin": "Passau Hbf",
                "destination": "München Hbf",
                "departure_time": "yesterday",
                "api_key": "sk-secret-token-abcdef12345",
                "auth_header": "Bearer confidential_user_credential",
            },
            result=ToolResult(
                content="Invalid departure_time format 'yesterday'. Expected ISO 8601 string (e.g. 'YYYY-MM-DDTHH:MM:SS').",
                is_error=True,
                error_code=ErrorCode.INVALID_ARGUMENT,
            ),
            step=1,
        )
        detector.record_attempt(failed)

        recovered = ToolAttempt(
            tool_name="find_connection",
            arguments={
                "origin": "Passau Hbf",
                "destination": "München Hbf",
                "departure_time": "2026-09-08T14:00:00Z",
                "api_key": "sk-secret-token-abcdef12345",
                "auth_header": "Bearer confidential_user_credential",
            },
            result=ToolResult(content="Connection found.", is_error=False),
            step=2,
        )
        lesson = detector.detect_recovery(recovered)
        self.assertIsNotNone(lesson)

        # Verify lesson string does not contain secrets
        self.assertNotIn("sk-secret", lesson.lesson)
        self.assertNotIn("confidential", lesson.lesson)
        self.assertNotIn("yesterday", lesson.lesson)

        # Verify provenance does not store raw arguments dictionary
        self.assertNotIn("arguments", lesson.provenance)
        self.assertNotIn("sk-secret", str(lesson.provenance))


class TestProceduralStorageAndRetrieval(unittest.TestCase):
    """Tests SQLite storage, supersession, and retrieval isolation for procedural memory."""

    def setUp(self) -> None:
        self.store = SQLiteMemoryStore(":memory:")
        self.firewall = MemoryFirewall(self.store, allow_time_bounded_transients=True)
        self.retriever = MemoryRetriever(self.store, max_retrieved=5, max_context_chars=1200)
        self.manager = MemoryManager(self.store, self.firewall, self.retriever)

    def tearDown(self) -> None:
        self.store.close()

    def test_declarative_retrieval_isolation(self) -> None:
        """Declarative retriever must NOT return procedural recovery memories."""
        # 1. Admit a declarative memory
        self.manager.admit_and_store(
            content="User preferred station is Passau Hbf.",
            source=MemorySource.USER_INPUT,
            metadata={"memory_key": "user.pref.station"},
        )

        # 2. Admit a procedural lesson
        lesson = ProceduralLesson(
            tool_name="find_connection",
            failing_parameter="departure_time",
            constraint="iso_8601_datetime",
            lesson="find_connection.departure_time requires ISO-8601 datetime format.",
            provenance={"tool_name": "find_connection", "failing_parameter": "departure_time"},
        )
        self.manager.admit_procedural_lesson(lesson)

        # 3. Query declarative retriever with query matching procedural lesson
        recalled = self.manager.retrieve("find_connection departure_time")
        for m in recalled:
            self.assertEqual(m.memory_type, MemoryType.DECLARATIVE)
            self.assertNotEqual(m.content, lesson.lesson)

        # 4. Procedural retrieval returns the procedural lesson
        proc_recalled = self.manager.retrieve_procedural(["find_connection"])
        self.assertEqual(len(proc_recalled), 1)
        self.assertEqual(proc_recalled[0].memory_type, MemoryType.PROCEDURAL)
        self.assertEqual(proc_recalled[0].content, lesson.lesson)

    def test_procedural_supersession(self) -> None:
        """When an updated procedural lesson for the same parameter arrives, the old one is superseded."""
        lesson_v1 = ProceduralLesson(
            tool_name="search_stations",
            failing_parameter="max_results",
            constraint="bounds_1_to_5",
            lesson="search_stations.max_results must be an integer between 1 and 5.",
        )
        self.manager.admit_procedural_lesson(lesson_v1)

        active = self.store.find_active_by_key("procedural:search_stations:max_results")
        self.assertIsNotNone(active)
        self.assertEqual(active.content, lesson_v1.lesson)

        # Newer lesson arrives for the same parameter slot
        lesson_v2 = ProceduralLesson(
            tool_name="search_stations",
            failing_parameter="max_results",
            constraint="bounds_1_to_10",
            lesson="search_stations.max_results must be an integer between 1 and 10.",
        )
        self.manager.admit_procedural_lesson(lesson_v2)

        # Check store state: old superseded, new active
        active_now = self.store.find_active_by_key("procedural:search_stations:max_results")
        self.assertIsNotNone(active_now)
        self.assertEqual(active_now.content, lesson_v2.lesson)

        # Exactly 1 active procedural entry for this key
        all_for_tool = self.store.list_procedural(["search_stations"])
        self.assertEqual(len(all_for_tool), 1)
        self.assertEqual(all_for_tool[0].content, lesson_v2.lesson)

    def test_procedural_retrieval_budget_bounds(self) -> None:
        """Procedural retrieval bounds output by limit_per_tool and max_chars."""
        for i in range(5):
            lesson = ProceduralLesson(
                tool_name="find_connection",
                failing_parameter=f"param_{i}",
                constraint="test_constraint",
                lesson=f"find_connection.param_{i} rule with padding text {i * 10}.",
            )
            self.manager.admit_procedural_lesson(lesson)

        # limit_per_tool=2
        retrieved = self.manager.retrieve_procedural(["find_connection"], limit_per_tool=2, max_chars=400)
        self.assertLessEqual(len(retrieved), 2)


class TestCrossSessionProceduralReAct(unittest.TestCase):
    """End-to-end evaluation comparing Baseline vs Treatment in a 2-session ReAct loop.

    Uses an IDENTICAL mock LLM policy across Baseline and Treatment.
    Session A: Initial run where the tool call is attempted.
    Session B: Subsequent run in a fresh controller instance.
    """

    def setUp(self) -> None:
        self.db_file = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.db_path = self.db_file.name
        self.db_file.close()

    def tearDown(self) -> None:
        import os
        if os.path.exists(self.db_path):
            os.remove(self.db_path)

    def _create_tool(self) -> Tool:
        spec = ToolSpec(
            name="find_connection",
            description="Find train connection between stations.",
            input_schema={
                "type": "object",
                "properties": {
                    "origin": {"type": "string"},
                    "destination": {"type": "string"},
                    "departure_time": {"type": "string"},
                },
                "required": ["origin", "destination", "departure_time"],
                "additionalProperties": False,
            },
        )

        def handler(args: Mapping[str, Any]) -> ToolResult:
            dep_time = str(args.get("departure_time", ""))
            # Must look like ISO 8601 YYYY-MM-DDTHH:MM:SS
            if "T" not in dep_time or len(dep_time) < 16:
                return ToolResult(
                    content=f"Invalid departure_time format '{dep_time}'. Expected ISO 8601 string (e.g. 'YYYY-MM-DDTHH:MM:SS').",
                    is_error=True,
                    error_code=ErrorCode.INVALID_ARGUMENT,
                )
            return ToolResult(
                content=f"Found connection ICE 28 from {args.get('origin')} to {args.get('destination')} departing {dep_time}.",
                is_error=False,
            )

        return DummyMockTool(spec, handler)

    def test_cross_session_comparison(self) -> None:
        """Compare Baseline (procedural disabled) vs Treatment (procedural enabled).

        Identical LLM Policy:
        - Checks for '[PAST TOOL EXPERIENCE]' mentioning 'departure_time' and 'ISO-8601'.
        - If present: formats departure_time as '2026-09-08T14:00:00Z' on step 1.
        - If absent: formats departure_time as 'tomorrow at 2pm' on step 1.
        - If step 1 fails with format error: retries with '2026-09-08T14:00:00Z'.
        """
        compliant_args = {"origin": "Passau Hbf", "destination": "München Hbf", "departure_time": "2026-09-08T14:00:00Z"}
        naive_args = {"origin": "Passau Hbf", "destination": "München Hbf", "departure_time": "tomorrow at 2pm"}
        exp_keyword = "ISO-8601 datetime format"

        # ==========================================
        # 1. BASELINE VARIANT (Procedural memory disabled)
        # ==========================================
        baseline_store = SQLiteMemoryStore(self.db_path + ".baseline")
        baseline_mgr = MemoryManager(baseline_store, BaselineAdmissionPolicy(baseline_store), MemoryRetriever(baseline_store))

        # --- Session A ---
        llm_base_a = PolicyMockLLMClient("find_connection", compliant_args, naive_args, exp_keyword)
        reg_base_a = ToolRegistry()
        reg_base_a.register(self._create_tool())
        ctrl_base_a = ReActController(
            llm_client=llm_base_a,
            tool_registry=reg_base_a,
            tool_executor=ToolExecutor(),
            memory_manager=baseline_mgr,
            enable_procedural_memory=False,  # Baseline
        )
        res_base_a = ctrl_base_a.run_turn("Find train to Munich")
        self.assertEqual(res_base_a.termination_reason, TerminationReason.FINAL_ANSWER)
        self.assertEqual(res_base_a.tool_calls, 2)  # Step 1 failed, Step 2 recovered in-turn

        # --- Session B (Fresh controller instance, new conversation) ---
        llm_base_b = PolicyMockLLMClient("find_connection", compliant_args, naive_args, exp_keyword)
        reg_base_b = ToolRegistry()
        reg_base_b.register(self._create_tool())
        ctrl_base_b = ReActController(
            llm_client=llm_base_b,
            tool_registry=reg_base_b,
            tool_executor=ToolExecutor(),
            memory_manager=baseline_mgr,
            enable_procedural_memory=False,  # Baseline
        )
        res_base_b = ctrl_base_b.run_turn("Find train to Munich tomorrow")
        self.assertEqual(res_base_b.termination_reason, TerminationReason.FINAL_ANSWER)
        # Baseline repeats the failure in Session B because it has NO procedural memory across sessions!
        self.assertEqual(res_base_b.tool_calls, 2)
        baseline_store.close()

        # ==========================================
        # 2. TREATMENT VARIANT (Procedural memory enabled)
        # ==========================================
        treat_store = SQLiteMemoryStore(self.db_path + ".treatment")
        treat_mgr = MemoryManager(treat_store, MemoryFirewall(treat_store, allow_time_bounded_transients=True), MemoryRetriever(treat_store))

        # --- Session A ---
        llm_treat_a = PolicyMockLLMClient("find_connection", compliant_args, naive_args, exp_keyword)
        reg_treat_a = ToolRegistry()
        reg_treat_a.register(self._create_tool())
        ctrl_treat_a = ReActController(
            llm_client=llm_treat_a,
            tool_registry=reg_treat_a,
            tool_executor=ToolExecutor(),
            memory_manager=treat_mgr,
            enable_procedural_memory=True,  # Treatment
        )
        res_treat_a = ctrl_treat_a.run_turn("Find train to Munich")
        self.assertEqual(res_treat_a.termination_reason, TerminationReason.FINAL_ANSWER)
        self.assertEqual(res_treat_a.tool_calls, 2)  # Step 1 failed, Step 2 recovered in-turn
        # Verify procedural lesson was persisted
        lessons = treat_store.list_procedural(["find_connection"])
        self.assertEqual(len(lessons), 1)
        self.assertIn("requires ISO-8601 datetime format", lessons[0].content)

        # --- Session B (Fresh controller instance, new conversation) ---
        llm_treat_b = PolicyMockLLMClient("find_connection", compliant_args, naive_args, exp_keyword)
        reg_treat_b = ToolRegistry()
        reg_treat_b.register(self._create_tool())
        ctrl_treat_b = ReActController(
            llm_client=llm_treat_b,
            tool_registry=reg_treat_b,
            tool_executor=ToolExecutor(),
            memory_manager=treat_mgr,
            enable_procedural_memory=True,  # Treatment
        )
        res_treat_b = ctrl_treat_b.run_turn("Find train to Munich tomorrow")
        self.assertEqual(res_treat_b.termination_reason, TerminationReason.FINAL_ANSWER)
        # Treatment avoids the failure in Session B: succeeds on attempt 1!
        self.assertEqual(res_treat_b.tool_calls, 1)

        treat_store.close()

    def test_benchmark_quantitative_four_scenarios(self) -> None:
        """Run quantitative benchmark across 4 procedural scenarios for Baseline and Treatment."""
        scenarios = [
            {
                "name": "iso_datetime",
                "tool_name": "find_connection",
                "spec": ToolSpec(
                    name="find_connection",
                    description="Find connection",
                    input_schema={
                        "type": "object",
                        "properties": {"departure_time": {"type": "string"}},
                        "required": ["departure_time"],
                    },
                ),
                "handler": lambda a: ToolResult(
                    content="Invalid departure_time format. Expected ISO 8601 string (e.g. 'YYYY-MM-DDTHH:MM:SS').",
                    is_error=True,
                    error_code=ErrorCode.INVALID_ARGUMENT,
                ) if "T" not in str(a.get("departure_time")) else ToolResult(content="Success", is_error=False),
                "naive_args": {"departure_time": "tomorrow 2pm"},
                "compliant_args": {"departure_time": "2026-09-08T14:00:00Z"},
                "keyword": "ISO-8601 datetime format",
            },
            {
                "name": "required_parameter",
                "tool_name": "search_stations",
                "spec": ToolSpec(
                    name="search_stations",
                    description="Search stations",
                    input_schema={
                        "type": "object",
                        "properties": {"query": {"type": "string"}},
                        "required": ["query"],
                    },
                ),
                "handler": lambda a: ToolResult(
                    content="Schema validation error: Missing required argument 'query'.",
                    is_error=True,
                    error_code=ErrorCode.INVALID_ARGUMENT,
                ) if "query" not in a else ToolResult(content="Success", is_error=False),
                "naive_args": {},
                "compliant_args": {"query": "Passau Hbf"},
                "keyword": "requires parameter 'query'",
            },
            {
                "name": "bounded_integer",
                "tool_name": "search_stations",
                "spec": ToolSpec(
                    name="search_stations",
                    description="Search stations",
                    input_schema={
                        "type": "object",
                        "properties": {"max_results": {"type": "integer"}},
                        "required": [],
                    },
                ),
                "handler": lambda a: ToolResult(
                    content="Parameter 'max_results' must be an integer between 1 and 5.",
                    is_error=True,
                    error_code=ErrorCode.BOUNDARY_VIOLATION,
                ) if a.get("max_results", 1) > 5 else ToolResult(content="Success", is_error=False),
                "naive_args": {"max_results": 10},
                "compliant_args": {"max_results": 5},
                "keyword": "must be an integer between 1 and 5",
            },
            {
                "name": "type_mismatch",
                "tool_name": "get_weather",
                "spec": ToolSpec(
                    name="get_weather",
                    description="Get weather",
                    input_schema={
                        "type": "object",
                        "properties": {"days": {"type": "integer"}},
                        "required": ["days"],
                    },
                ),
                "handler": lambda a: ToolResult(
                    content="Schema validation error: Argument 'days' must be an integer, got str.",
                    is_error=True,
                    error_code=ErrorCode.INVALID_ARGUMENT,
                ) if not isinstance(a.get("days"), int) else ToolResult(content="Success", is_error=False),
                "naive_args": {"days": "3"},
                "compliant_args": {"days": 3},
                "keyword": "must match expected schema type",
            },
        ]

        # Evaluate Baseline
        baseline_store = SQLiteMemoryStore(self.db_path + ".bench_base")
        baseline_mgr = MemoryManager(baseline_store, BaselineAdmissionPolicy(baseline_store), MemoryRetriever(baseline_store))

        base_s1_fails = 0
        base_s2_fails = 0
        base_s2_first_try = 0

        for sc in scenarios:
            # Session A
            tool = DummyMockTool(sc["spec"], sc["handler"])
            reg_a = ToolRegistry()
            reg_a.register(tool)
            llm_a = PolicyMockLLMClient(sc["tool_name"], sc["compliant_args"], sc["naive_args"], sc["keyword"])
            ctrl_a = ReActController(llm_a, reg_a, ToolExecutor(), memory_manager=baseline_mgr, enable_procedural_memory=False)
            res_a = ctrl_a.run_turn(f"Run {sc['name']}")
            if res_a.tool_calls > 1:
                base_s1_fails += 1

            # Session B
            tool_b = DummyMockTool(sc["spec"], sc["handler"])
            reg_b = ToolRegistry()
            reg_b.register(tool_b)
            llm_b = PolicyMockLLMClient(sc["tool_name"], sc["compliant_args"], sc["naive_args"], sc["keyword"])
            ctrl_b = ReActController(llm_b, reg_b, ToolExecutor(), memory_manager=baseline_mgr, enable_procedural_memory=False)
            res_b = ctrl_b.run_turn(f"Run {sc['name']} session 2")
            if res_b.tool_calls > 1:
                base_s2_fails += 1
            else:
                base_s2_first_try += 1

        baseline_store.close()

        # Evaluate Treatment
        treat_store = SQLiteMemoryStore(self.db_path + ".bench_treat")
        treat_mgr = MemoryManager(treat_store, MemoryFirewall(treat_store, allow_time_bounded_transients=True), MemoryRetriever(treat_store))

        treat_s1_fails = 0
        treat_s2_fails = 0
        treat_s2_first_try = 0

        for sc in scenarios:
            # Session A
            tool = DummyMockTool(sc["spec"], sc["handler"])
            reg_a = ToolRegistry()
            reg_a.register(tool)
            llm_a = PolicyMockLLMClient(sc["tool_name"], sc["compliant_args"], sc["naive_args"], sc["keyword"])
            ctrl_a = ReActController(llm_a, reg_a, ToolExecutor(), memory_manager=treat_mgr, enable_procedural_memory=True)
            res_a = ctrl_a.run_turn(f"Run {sc['name']}")
            if res_a.tool_calls > 1:
                treat_s1_fails += 1

            # Session B
            tool_b = DummyMockTool(sc["spec"], sc["handler"])
            reg_b = ToolRegistry()
            reg_b.register(tool_b)
            llm_b = PolicyMockLLMClient(sc["tool_name"], sc["compliant_args"], sc["naive_args"], sc["keyword"])
            ctrl_b = ReActController(llm_b, reg_b, ToolExecutor(), memory_manager=treat_mgr, enable_procedural_memory=True)
            res_b = ctrl_b.run_turn(f"Run {sc['name']} session 2")
            if res_b.tool_calls > 1:
                treat_s2_fails += 1
            else:
                treat_s2_first_try += 1

        treat_store.close()

        # Empirical Assertions
        total = len(scenarios)
        self.assertEqual(base_s1_fails, total)  # Baseline: 4/4 failed in Session 1
        self.assertEqual(base_s2_fails, total)  # Baseline: 4/4 repeated failure in Session 2
        self.assertEqual(base_s2_first_try, 0)  # Baseline: 0/4 succeeded on first attempt in Session 2

        self.assertEqual(treat_s1_fails, total)  # Treatment: 4/4 failed in Session 1 (identical starting knowledge)
        self.assertEqual(treat_s2_fails, 0)     # Treatment: 0/4 repeated failure in Session 2 (100% prevented!)
        self.assertEqual(treat_s2_first_try, total)  # Treatment: 4/4 succeeded on first attempt in Session 2
