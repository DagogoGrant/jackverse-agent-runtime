"""Unit and integration tests for the custom Phase 2 MCP transport tool (find_connection)."""

from datetime import datetime
import json
import sys
import tempfile
from typing import Any
import unittest

from harness.agent.budget import ExecutionBudget
from harness.agent.react import ReActController
from harness.cli import build_controller
from harness.config import (
    AgentConfig,
    AppConfig,
    LLMConfig,
    MCPServerConfig,
    ToolsConfig,
)
from harness.llm.client import LLMResponse, ToolCall
from harness.mcp.adapter import MCPToolAdapter
from harness.mcp.client import MCPClient
from harness.mcp.servers.transport_backend import (
    DATA_SOURCE_NAME,
    SUPPORTED_STATIONS,
    find_connections,
    normalize_station,
)
from harness.tools.base import ErrorCode, ToolResult, ToolSpec
from harness.tools.executor import ToolExecutor


class TestTransportBackendAndContract(unittest.TestCase):
    """Direct tests for the deterministic synthetic Bavarian timetable backend."""

    def test_direct_journey(self) -> None:
        """1. Test direct connection search (e.g. Passau Hbf to München Hbf on RE 3)."""
        res = find_connections(
            origin="Passau Hbf",
            destination="München Hbf",
            departure_time="2026-09-08T06:00:00",
            max_results=1,
        )
        self.assertEqual(res["data_source"], DATA_SOURCE_NAME)
        self.assertEqual(res["total_found"], 1)
        self.assertEqual(len(res["connections"]), 1)

        conn = res["connections"][0]
        self.assertEqual(conn["origin"], "Passau Hbf")
        self.assertEqual(conn["destination"], "München Hbf")
        self.assertEqual(conn["transfers"], 0)
        self.assertEqual(len(conn["legs"]), 1)

        leg = conn["legs"][0]
        self.assertEqual(leg["line"], "RE 3")
        self.assertEqual(leg["from_station"], "Passau Hbf")
        self.assertEqual(leg["to_station"], "München Hbf")
        self.assertEqual(leg["platform"], "5")
        self.assertTrue(conn["duration_minutes"] > 0)

    def test_one_transfer_journey(self) -> None:
        """2. Test connection with at most one transfer (Passau Hbf to Deggendorf via Plattling)."""
        res = find_connections(
            origin="Passau Hbf",
            destination="Deggendorf",
            departure_time="2026-09-08T07:00:00",
            max_results=1,
        )
        self.assertEqual(res["total_found"], 1)
        conn = res["connections"][0]
        self.assertEqual(conn["transfers"], 1)
        self.assertEqual(len(conn["legs"]), 2)

        leg1, leg2 = conn["legs"][0], conn["legs"][1]
        self.assertEqual(leg1["from_station"], "Passau Hbf")
        self.assertEqual(leg1["to_station"], "Plattling")
        self.assertEqual(leg2["from_station"], "Plattling")
        self.assertEqual(leg2["to_station"], "Deggendorf")
        self.assertEqual(leg2["line"], "RB 35")

        # Verify transfer buffer: between 5 and 60 minutes
        arr1 = datetime.fromisoformat(leg1["arrival_time"])
        dep2 = datetime.fromisoformat(leg2["departure_time"])
        transfer_buffer = int((dep2 - arr1).total_seconds() // 60)
        self.assertGreaterEqual(transfer_buffer, 5)
        self.assertLessEqual(transfer_buffer, 60)

    def test_earliest_departure_filtering(self) -> None:
        """3. Test that results only include departures at or after departure_time."""
        query_time = "2026-09-08T14:30:00"
        res = find_connections(
            origin="Passau Hbf",
            destination="München Hbf",
            departure_time=query_time,
            max_results=3,
        )
        self.assertGreater(res["total_found"], 0)
        target_dt = datetime.fromisoformat(query_time)
        for conn in res["connections"]:
            dep_dt = datetime.fromisoformat(conn["departure_time"])
            self.assertGreaterEqual(dep_dt, target_dt)

    def test_result_ordering(self) -> None:
        """4. Test that connections are sorted by earliest departure, then duration."""
        res = find_connections(
            origin="Passau Hbf",
            destination="München Hbf",
            departure_time="2026-09-08T08:00:00",
            max_results=4,
        )
        self.assertGreaterEqual(res["total_found"], 2)
        conns = res["connections"]
        for i in range(len(conns) - 1):
            dt1 = datetime.fromisoformat(conns[i]["departure_time"])
            dt2 = datetime.fromisoformat(conns[i + 1]["departure_time"])
            self.assertLessEqual(dt1, dt2)
            if dt1 == dt2:
                self.assertLessEqual(conns[i]["duration_minutes"], conns[i + 1]["duration_minutes"])

    def test_max_results_bounding(self) -> None:
        """5. Test that max_results is strictly respected."""
        res1 = find_connections("Passau Hbf", "München Hbf", "2026-09-08T08:00:00", max_results=1)
        self.assertEqual(len(res1["connections"]), 1)
        self.assertEqual(res1["total_found"], 1)

        res3 = find_connections("Passau Hbf", "München Hbf", "2026-09-08T08:00:00", max_results=3)
        self.assertEqual(len(res3["connections"]), 3)
        self.assertEqual(res3["total_found"], 3)

    def test_unknown_station_rejected(self) -> None:
        """6. Test that unknown station name raises ValueError with supported list."""
        with self.assertRaises(ValueError) as ctx:
            find_connections("Atlantis", "München Hbf", "2026-09-08T08:00:00")
        self.assertIn("Unknown station 'Atlantis'", str(ctx.exception))
        self.assertIn("Passau Hbf", str(ctx.exception))

    def test_same_origin_destination_rejected(self) -> None:
        """7. Test that identical origin and destination are rejected."""
        with self.assertRaises(ValueError) as ctx:
            find_connections("Passau Hbf", "passau hbf", "2026-09-08T08:00:00")
        self.assertIn("cannot be identical", str(ctx.exception))

    def test_malformed_datetime_rejected(self) -> None:
        """8. Test that non-ISO datetime strings are rejected."""
        with self.assertRaises(ValueError) as ctx:
            find_connections("Passau Hbf", "München Hbf", "tomorrow at 9am")
        self.assertIn("Invalid departure_time format", str(ctx.exception))

    def test_max_results_below_bound_rejected(self) -> None:
        """9. Test that max_results < 1 is rejected."""
        with self.assertRaises(ValueError) as ctx:
            find_connections("Passau Hbf", "München Hbf", "2026-09-08T08:00:00", max_results=0)
        self.assertIn("between 1 and 5", str(ctx.exception))

    def test_max_results_above_bound_rejected(self) -> None:
        """10. Test that max_results > 5 is rejected."""
        with self.assertRaises(ValueError) as ctx:
            find_connections("Passau Hbf", "München Hbf", "2026-09-08T08:00:00", max_results=6)
        self.assertIn("between 1 and 5", str(ctx.exception))

    def test_successful_empty_result(self) -> None:
        """11. Test that valid query with zero matching connections returns success with empty list."""
        # Querying after daily service ends (e.g. 23:55)
        res = find_connections("Passau Hbf", "München Hbf", "2026-09-08T23:55:00", max_results=3)
        self.assertEqual(res["data_source"], DATA_SOURCE_NAME)
        self.assertEqual(res["total_found"], 0)
        self.assertEqual(res["connections"], [])

    def test_deterministic_repeated_execution(self) -> None:
        """15. Test that repeated identical queries yield byte-for-byte identical output."""
        res1 = find_connections("Passau Hbf", "Deggendorf", "2026-09-08T09:00:00", max_results=2)
        res2 = find_connections("Passau Hbf", "Deggendorf", "2026-09-08T09:00:00", max_results=2)
        self.assertEqual(json.dumps(res1, sort_keys=True), json.dumps(res2, sort_keys=True))

    def test_provenance_metadata_present(self) -> None:
        """Verify provenance metadata (data_source) is always present."""
        res = find_connections("Passau Hbf", "Vilshofen", "2026-09-08T10:00:00", max_results=1)
        self.assertEqual(res.get("data_source"), "synthetic_bavarian_timetable_v1")


class TestTransportMCPIntegration(unittest.TestCase):
    """Integration tests running find_connection across MCPClient, ToolExecutor, and ReAct loop."""

    def setUp(self) -> None:
        self.server_config = MCPServerConfig(
            name="transport_service",
            command=sys.executable,
            args=["-m", "harness.mcp.servers.transport_server"],
        )

    def test_execution_through_tool_executor(self) -> None:
        """12. Test executing find_connection through ToolExecutor boundary."""
        client = MCPClient(self.server_config)
        specs = {s.name: s for s in client.list_tools()}
        self.assertIn("find_connection", specs)

        adapter = MCPToolAdapter(specs["find_connection"], client)
        executor = ToolExecutor()

        result = executor.execute(adapter, {
            "origin": "Passau Hbf",
            "destination": "München Hbf",
            "departure_time": "2026-09-08T08:00:00",
            "max_results": 2,
        })

        self.assertIsInstance(result, ToolResult)
        self.assertFalse(result.is_error)
        self.assertIsNone(result.error_code)

        parsed = json.loads(result.content)
        self.assertEqual(parsed["data_source"], DATA_SOURCE_NAME)
        self.assertEqual(parsed["total_found"], 2)

    def test_successful_empty_result_through_mcp_client(self) -> None:
        """11b. Verify valid query with zero connections returns is_error=False through MCP."""
        client = MCPClient(self.server_config)
        res = client.call_tool("find_connection", {
            "origin": "Passau Hbf",
            "destination": "München Hbf",
            "departure_time": "2026-09-08T23:55:00",
            "max_results": 3,
        })
        self.assertFalse(res.is_error)
        self.assertIsNone(res.error_code)
        parsed = json.loads(res.content)
        self.assertEqual(parsed["total_found"], 0)
        self.assertEqual(parsed["connections"], [])

    def test_layer0_rejection_before_mcp_call(self) -> None:
        """13. Test Layer 0 schema validation traps missing or invalid argument types before MCP invocation."""
        client = MCPClient(self.server_config)
        specs = {s.name: s for s in client.list_tools()}
        adapter = MCPToolAdapter(specs["find_connection"], client)
        executor = ToolExecutor()

        # Missing required parameter: 'destination'
        res_missing = executor.execute(adapter, {
            "origin": "Passau Hbf",
            "departure_time": "2026-09-08T08:00:00",
        })
        self.assertTrue(res_missing.is_error)
        self.assertEqual(res_missing.error_code, ErrorCode.INVALID_ARGUMENT)
        self.assertIn("Missing required argument 'destination'", res_missing.content)

        # Invalid primitive type: max_results is string instead of integer
        res_bad_type = executor.execute(adapter, {
            "origin": "Passau Hbf",
            "destination": "München Hbf",
            "departure_time": "2026-09-08T08:00:00",
            "max_results": "three",
        })
        self.assertTrue(res_bad_type.is_error)
        self.assertEqual(res_bad_type.error_code, ErrorCode.INVALID_ARGUMENT)
        self.assertIn("Argument 'max_results' must be an integer", res_bad_type.content)

    def test_semantic_validation_rejection_through_mcp(self) -> None:
        """Verify semantic validation errors (unknown station, bad ISO date) map to INVALID_ARGUMENT."""
        client = MCPClient(self.server_config)

        # Unknown station
        res_unknown = client.call_tool("find_connection", {
            "origin": "Atlantis",
            "destination": "München Hbf",
            "departure_time": "2026-09-08T08:00:00",
        })
        self.assertTrue(res_unknown.is_error)
        self.assertEqual(res_unknown.error_code, ErrorCode.INVALID_ARGUMENT)
        self.assertIn("Unknown station", res_unknown.content)

        # Identical origin and destination
        res_same = client.call_tool("find_connection", {
            "origin": "Passau Hbf",
            "destination": "Passau Hbf",
            "departure_time": "2026-09-08T08:00:00",
        })
        self.assertTrue(res_same.is_error)
        self.assertEqual(res_same.error_code, ErrorCode.INVALID_ARGUMENT)
        self.assertIn("cannot be identical", res_same.content)

        # Malformed ISO timestamp
        res_bad_time = client.call_tool("find_connection", {
            "origin": "Passau Hbf",
            "destination": "München Hbf",
            "departure_time": "tomorrow morning",
        })
        self.assertTrue(res_bad_time.is_error)
        self.assertEqual(res_bad_time.error_code, ErrorCode.INVALID_ARGUMENT)
        self.assertIn("Invalid departure_time format", res_bad_time.content)

        # max_results out of bounds
        res_bound = client.call_tool("find_connection", {
            "origin": "Passau Hbf",
            "destination": "München Hbf",
            "departure_time": "2026-09-08T08:00:00",
            "max_results": 9,
        })
        self.assertTrue(res_bound.is_error)
        self.assertEqual(res_bound.error_code, ErrorCode.INVALID_ARGUMENT)
        self.assertIn("between 1 and 5", res_bound.content)

    def test_react_e2e_selecting_find_connection(self) -> None:
        """14. Integration/E2E test: ReAct loop discovers, selects find_connection, executes via MCP, and receives observation."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            config = AppConfig(
                agent=AgentConfig(max_steps=5),
                llm=LLMConfig(
                    base_url="https://mock.llm.example",
                    model="mock-model",
                    temperature=0.0,
                ),
                tools=ToolsConfig(workspace_root=tmp_dir),
                mcp_servers=[self.server_config],
            )

            controller, workspace, registry = build_controller(config, "mock_key")

            # Verify find_connection was dynamically registered
            spec_names = {s.name for s in registry.list_specs()}
            self.assertIn("find_connection", spec_names)

            class MockTravelLLM:
                def __init__(self) -> None:
                    self.turn = 0

                def chat(self, messages: Any, tools: Any = None) -> LLMResponse:
                    self.turn += 1
                    if self.turn == 1:
                        return LLMResponse(
                            content="I will search for train connections from Passau to Munich.",
                            tool_calls=[
                                ToolCall(
                                    id="call_transport_1",
                                    name="find_connection",
                                    arguments={
                                        "origin": "Passau Hbf",
                                        "destination": "München Hbf",
                                        "departure_time": "2026-09-08T09:00:00",
                                        "max_results": 1,
                                    },
                                )
                            ],
                        )
                    # Turn 2: LLM consumes the structured observation to ground its final response
                    tool_msgs = [m for m in messages if m.get("role") == "tool"]
                    conn_id = "UNKNOWN"
                    dep_time = "UNKNOWN"
                    arr_time = "UNKNOWN"
                    line = "UNKNOWN"
                    if tool_msgs:
                        try:
                            payload = json.loads(tool_msgs[-1]["content"])
                            if payload.get("connections"):
                                c = payload["connections"][0]
                                conn_id = c.get("connection_id", "")
                                dep_time = c.get("departure_time", "")
                                arr_time = c.get("arrival_time", "")
                                line = c.get("legs", [{}])[0].get("line", "")
                        except Exception:
                            pass
                    return LLMResponse(
                        content=f"Final Answer: Found connection {conn_id} departing Passau Hbf at {dep_time} on line {line}, arriving München Hbf at {arr_time}.",
                        tool_calls=[],
                    )

            controller.llm_client = MockTravelLLM()
            run_result = controller.run_turn("Find me a train from Passau to Munich at 9am.")

            self.assertTrue(run_result.is_success)
            self.assertEqual(run_result.steps, 2)
            self.assertEqual(run_result.tool_calls, 1)
            self.assertIn("CONN-RE3-0925", run_result.final_text)
            self.assertIn("2026-09-08T11:30:00", run_result.final_text)

            # Verify observation was inserted into context
            tool_messages = [m for m in controller.context if m.get("role") == "tool"]
            self.assertEqual(len(tool_messages), 1)
            self.assertIn("synthetic_bavarian_timetable_v1", tool_messages[0]["content"])
            self.assertIn("CONN-RE3-0925", tool_messages[0]["content"])
