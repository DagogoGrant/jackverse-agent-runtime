"""Focused unit tests for Phase P1: Transport Provider Abstraction."""

from datetime import datetime
import json
from typing import Any
import unittest

from mcp.server.mcpserver.exceptions import ToolError

from harness.mcp.servers.transport_backend import (
    DATA_SOURCE_NAME as LEGACY_DATA_SOURCE,
    SUPPORTED_STATIONS as LEGACY_STATIONS,
    find_connections as legacy_find_connections,
    normalize_station as legacy_normalize_station,
)
from harness.mcp.servers.transport_models import (
    JourneyLeg,
    Station,
    TransportDomainError,
    TransportErrorCode,
    TransportJourney,
)
from harness.mcp.servers.transport_provider import (
    SyntheticTransportProvider,
    TransportProvider,
)
from harness.mcp.servers.transport_server import (
    find_connection,
    get_transport_service,
    set_transport_service,
)
from harness.mcp.servers.transport_service import TransportService


class MockCustomTransportProvider(TransportProvider):
    """Custom mock provider for testing dependency injection and data_source flow."""

    def __init__(self, provider_id: str = "mock_provider_v1") -> None:
        self._id = provider_id

    @property
    def provider_name(self) -> str:
        return self._id

    def search_stations(self, query: str) -> list[Station]:
        return [Station(id="st-alpha", name="Station Alpha")]

    def validate_stations(self, origin: str, destination: str) -> tuple[str, str]:
        if origin == "UNKNOWN":
            raise TransportDomainError(
                TransportErrorCode.INVALID_STATION,
                "Unknown origin station in mock provider.",
            )
        return origin.strip(), destination.strip()

    def query_connections(
        self,
        origin: str,
        destination: str,
        departure_time: datetime,
        max_results: int,
    ) -> list[TransportJourney]:
        leg = JourneyLeg(
            line="MOCK-LINE-1",
            from_station=origin,
            to_station=destination,
            departure_time="2026-09-08T10:00:00",
            arrival_time="2026-09-08T11:00:00",
            platform="99",
        )
        return [
            TransportJourney(
                connection_id="MOCK-CONN-01",
                origin=origin,
                destination=destination,
                departure_time="2026-09-08T10:00:00",
                arrival_time="2026-09-08T11:00:00",
                duration_minutes=60,
                transfers=0,
                legs=[leg],
            )
        ]


class TestTransportProviderAbstraction(unittest.TestCase):
    """Verify provider abstraction, injection, structured errors, and backward compatibility."""

    def setUp(self) -> None:
        self.original_service = get_transport_service()

    def tearDown(self) -> None:
        set_transport_service(self.original_service)

    def test_1_synthetic_provider_satisfies_interface(self) -> None:
        """1. Verify SyntheticTransportProvider implements TransportProvider ABC."""
        provider = SyntheticTransportProvider()
        self.assertIsInstance(provider, TransportProvider)
        self.assertEqual(provider.provider_name, "synthetic_bavarian_timetable_v1")

        # Test search_stations
        stations = provider.search_stations("passau")
        self.assertEqual(len(stations), 1)
        self.assertEqual(stations[0].id, "passau-hbf")
        self.assertEqual(stations[0].name, "Passau Hbf")

        # Test validate_stations
        o, d = provider.validate_stations("passau hbf", "MÜNCHEN HBF")
        self.assertEqual(o, "Passau Hbf")
        self.assertEqual(d, "München Hbf")

        # Test query_connections returns TransportJourney instances
        journeys = provider.query_connections(
            origin="Passau Hbf",
            destination="München Hbf",
            departure_time=datetime(2026, 9, 8, 8, 30),
            max_results=1,
        )
        self.assertEqual(len(journeys), 1)
        self.assertIsInstance(journeys[0], TransportJourney)
        self.assertEqual(journeys[0].connection_id, "CONN-RE3-0925")

    def test_2_transport_service_with_injected_provider(self) -> None:
        """2. Verify TransportService works seamlessly with an injected custom provider."""
        mock_provider = MockCustomTransportProvider("injected_carrier_v2")
        service = TransportService(provider=mock_provider)

        self.assertIs(service.provider, mock_provider)
        response = service.find_connections(
            origin="Station Alpha",
            destination="Station Beta",
            departure_time="2026-09-08T09:45:00",
            max_results=2,
        )

        self.assertEqual(response["data_source"], "injected_carrier_v2")
        self.assertEqual(response["total_found"], 1)
        self.assertEqual(response["connections"][0]["connection_id"], "MOCK-CONN-01")
        self.assertEqual(response["connections"][0]["legs"][0]["line"], "MOCK-LINE-1")

    def test_3_provider_data_source_flows_into_mcp_output(self) -> None:
        """3. Verify provider data_source dynamically propagates into the structured MCP payload."""
        # A. Synthetic provider default
        synthetic_service = TransportService(SyntheticTransportProvider())
        out_synthetic = synthetic_service.find_connections(
            origin="Passau Hbf",
            destination="Vilshofen",
            departure_time="2026-09-08T08:00:00",
        )
        self.assertEqual(out_synthetic["data_source"], "synthetic_bavarian_timetable_v1")

        # B. Custom injected provider into MCP server
        custom_provider = MockCustomTransportProvider("oebb_partner_feed_v3")
        set_transport_service(TransportService(custom_provider))

        raw_json = find_connection(
            origin="Station 1",
            destination="Station 2",
            departure_time="2026-09-08T10:00:00",
        )
        parsed = json.loads(raw_json)
        self.assertEqual(parsed["data_source"], "oebb_partner_feed_v3")

    def test_4_domain_errors_are_structured_not_inferred_from_text(self) -> None:
        """4. Verify errors raise TransportDomainError with machine-readable TransportErrorCode enums."""
        service = TransportService()

        # A. Invalid Station code
        with self.assertRaises(TransportDomainError) as ctx_station:
            service.find_connections("Atlantis", "Passau Hbf", "2026-09-08T08:00:00")
        self.assertEqual(ctx_station.exception.code, TransportErrorCode.INVALID_STATION)
        self.assertIsInstance(ctx_station.exception.code, TransportErrorCode)

        # B. Identical stations code
        with self.assertRaises(TransportDomainError) as ctx_same:
            service.find_connections("Passau Hbf", "Passau Hbf", "2026-09-08T08:00:00")
        self.assertEqual(ctx_same.exception.code, TransportErrorCode.INVALID_STATION)

        # C. Invalid timestamp code
        with self.assertRaises(TransportDomainError) as ctx_time:
            service.find_connections("Passau Hbf", "Vilshofen", "next monday at 8am")
        self.assertEqual(ctx_time.exception.code, TransportErrorCode.INVALID_TIME)

        # D. Invalid max_results code
        with self.assertRaises(TransportDomainError) as ctx_bound:
            service.find_connections("Passau Hbf", "Vilshofen", "2026-09-08T08:00:00", max_results=10)
        self.assertEqual(ctx_bound.exception.code, TransportErrorCode.INVALID_ARGUMENT)

        # E. Injected provider can raise other structured codes without code changes
        class DownstreamFailureProvider(MockCustomTransportProvider):
            def query_connections(self, *args: Any, **kwargs: Any) -> list[TransportJourney]:
                raise TransportDomainError(
                    TransportErrorCode.PROVIDER_UNAVAILABLE,
                    "Live Deutsche Bahn HAFAS API connection timed out.",
                    details={"http_status": 504},
                )

        failing_service = TransportService(DownstreamFailureProvider())
        with self.assertRaises(TransportDomainError) as ctx_down:
            failing_service.find_connections("Station A", "Station B", "2026-09-08T08:00:00")
        self.assertEqual(ctx_down.exception.code, TransportErrorCode.PROVIDER_UNAVAILABLE)
        self.assertEqual(ctx_down.exception.details.get("http_status"), 504)

    def test_5_backward_compatibility_across_legacy_and_mcp_interfaces(self) -> None:
        """5. Verify legacy facade and MCP tool contract remain completely backward compatible."""
        # A. Legacy exports from transport_backend
        self.assertEqual(LEGACY_DATA_SOURCE, "synthetic_bavarian_timetable_v1")
        self.assertIn("Passau Hbf", LEGACY_STATIONS)
        self.assertEqual(legacy_normalize_station("passau hbf"), "Passau Hbf")

        with self.assertRaises(ValueError):
            legacy_normalize_station("Nonexistent Station")

        # B. Legacy find_connections continues returning identical schema
        legacy_res = legacy_find_connections("Passau Hbf", "Plattling", "2026-09-08T08:00:00", max_results=1)
        self.assertEqual(legacy_res["data_source"], "synthetic_bavarian_timetable_v1")
        self.assertEqual(legacy_res["total_found"], 1)

        # C. MCP find_connection tool raises ToolError containing structured code and original text
        with self.assertRaises(ToolError) as ctx_tool_err:
            find_connection("Nonexistent Station", "Passau Hbf", "2026-09-08T08:00:00")
        self.assertIn("[INVALID_STATION]", str(ctx_tool_err.exception))
        self.assertIn("Unknown station", str(ctx_tool_err.exception))
