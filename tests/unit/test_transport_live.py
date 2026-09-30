"""Unit tests for LiveTransportProvider (Phase P2: Live Transport Hardening)."""

from datetime import datetime
import json
import os
from typing import Any
import unittest
import httpx

from harness.config import (
    AppConfig,
    MCPServerConfig,
    ToolsConfig,
    AgentConfig,
    LLMConfig,
    TransportConfig,
    TransportLiveConfig,
)
from harness.mcp.servers.transport_live import LiveTransportProvider
from harness.mcp.servers.transport_models import (
    Station,
    TransportDomainError,
    TransportErrorCode,
    TransportJourney,
)
from harness.mcp.servers.transport_provider import (
    SyntheticTransportProvider,
    TransportProvider,
)
from harness.mcp.servers.transport_service import (
    TransportService,
    create_transport_service,
)

# Standard mock location payloads (FPTF v2)
MOCK_LOCATIONS_PASSAU = [
    {
        "type": "stop",
        "id": "8000298",
        "name": "Passau Hbf",
        "location": {"type": "location", "latitude": 48.5727, "longitude": 13.4542},
        "products": {"nationalExpress": True, "national": True, "regional": True},
    },
    {
        "type": "stop",
        "id": "8006123",
        "name": "Passau-Voglau",
        "location": {"type": "location", "latitude": 48.568, "longitude": 13.431},
    },
]

MOCK_LOCATIONS_MUNICH = [
    {
        "type": "stop",
        "id": "8000261",
        "name": "München Hbf",
        "location": {"type": "location", "latitude": 48.1402, "longitude": 11.5583},
        "products": {"nationalExpress": True, "national": True, "regional": True},
    }
]

MOCK_LOCATIONS_AMBIGUOUS = [
    {
        "type": "stop",
        "id": "8000260",
        "name": "München Ost",
    },
    {
        "type": "stop",
        "id": "8000261",
        "name": "München Hbf",
    },
    {
        "type": "stop",
        "id": "8000262",
        "name": "München-Pasing",
    },
]

# Standard mock direct journey payload (FPTF v2)
MOCK_JOURNEY_DIRECT = {
    "journeys": [
        {
            "type": "journey",
            "legs": [
                {
                    "origin": {"type": "stop", "id": "8000298", "name": "Passau Hbf"},
                    "destination": {"type": "stop", "id": "8000261", "name": "München Hbf"},
                    "departure": "2026-09-08T09:25:00+02:00",
                    "plannedDeparture": "2026-09-08T09:25:00+02:00",
                    "departurePlatform": "5",
                    "arrival": "2026-09-08T11:30:00+02:00",
                    "plannedArrival": "2026-09-08T11:30:00+02:00",
                    "arrivalPlatform": "24",
                    "line": {"type": "line", "id": "re-3", "name": "RE 3", "product": "regional"},
                }
            ],
        }
    ]
}

# Standard mock 2-leg transfer journey payload
MOCK_JOURNEY_MULTI_LEG = {
    "journeys": [
        {
            "type": "journey",
            "legs": [
                {
                    "origin": {"type": "stop", "id": "8000298", "name": "Passau Hbf"},
                    "destination": {"type": "stop", "id": "8000299", "name": "Plattling"},
                    "departure": "2026-09-08T10:10:00+02:00",
                    "plannedDeparture": "2026-09-08T10:10:00+02:00",
                    "departurePlatform": "3",
                    "arrival": "2026-09-08T10:40:00+02:00",
                    "plannedArrival": "2026-09-08T10:40:00+02:00",
                    "arrivalPlatform": "3",
                    "line": {"type": "line", "id": "ice-28", "name": "ICE 28"},
                },
                {
                    "origin": {"type": "stop", "id": "8000299", "name": "Plattling"},
                    "destination": {"type": "stop", "id": "8000300", "name": "Deggendorf"},
                    "departure": "2026-09-08T11:10:00+02:00",
                    "plannedDeparture": "2026-09-08T11:10:00+02:00",
                    "departurePlatform": "4",
                    "arrival": "2026-09-08T11:22:00+02:00",
                    "plannedArrival": "2026-09-08T11:22:00+02:00",
                    "arrivalPlatform": "1",
                    "line": {"type": "line", "id": "rb-35", "name": "RB 35"},
                },
            ],
        }
    ]
}


import urllib.parse


def build_mock_transport(responses: dict[str, Any]) -> httpx.MockTransport:
    """Helper creating an httpx.MockTransport returning predetermined responses."""

    def handler(request: httpx.Request) -> httpx.Response:
        url_path = request.url.path
        raw_query = request.url.query.decode("utf-8")
        unquoted_query = urllib.parse.unquote_plus(raw_query)
        target = f"{url_path}?{unquoted_query}"

        for pattern, config in responses.items():
            if pattern in target or pattern in f"{url_path}?{raw_query}" or pattern == url_path:
                if isinstance(config, Exception):
                    raise config
                status = config.get("status", 200)
                content = config.get("content")
                json_data = config.get("json")
                return httpx.Response(status_code=status, json=json_data, content=content)

        return httpx.Response(404, json={"error": f"Not found in mock transport: {target}"})

    return httpx.MockTransport(handler)


class TestLiveTransportProvider(unittest.TestCase):
    """Offline test suite for LiveTransportProvider using mock HTTP transport."""

    def test_provider_satisfies_interface(self) -> None:
        """1. Verify LiveTransportProvider satisfies TransportProvider interface."""
        client = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200)))
        provider = LiveTransportProvider(client=client)
        self.assertIsInstance(provider, TransportProvider)
        self.assertEqual(provider.provider_name, "transport_rest_live")

    def test_station_resolution_exact_and_ambiguous(self) -> None:
        """2. Test station resolution handles exact matching and ambiguous ranking deterministically."""
        responses = {
            "/locations?query=Passau": {"json": MOCK_LOCATIONS_PASSAU},
            "/locations?query=München": {"json": MOCK_LOCATIONS_AMBIGUOUS},
            "/locations?query=UnknownCity": {"json": []},
        }
        client = httpx.Client(
            base_url="https://mock.transport.rest",
            transport=build_mock_transport(responses),
        )
        provider = LiveTransportProvider(client=client)

        # Exact match preferred: "München Hbf" in ambiguous list
        orig, dest = provider.validate_stations("Passau Hbf", "München Hbf")
        self.assertEqual(orig, "Passau Hbf")
        self.assertEqual(dest, "München Hbf")
        self.assertEqual(provider._id_cache["Passau Hbf"], "8000298")
        self.assertEqual(provider._id_cache["München Hbf"], "8000261")

        # Unknown station raises INVALID_STATION
        with self.assertRaises(TransportDomainError) as ctx:
            provider.validate_stations("Passau Hbf", "UnknownCity")
        self.assertEqual(ctx.exception.code, TransportErrorCode.INVALID_STATION)

        # Identical stations rejected
        with self.assertRaises(TransportDomainError) as ctx_same:
            provider.validate_stations("Passau Hbf", "Passau Hbf")
        self.assertEqual(ctx_same.exception.code, TransportErrorCode.INVALID_STATION)

    def test_successful_direct_journey_normalization(self) -> None:
        """3. Test successful direct journey response parsing into TransportJourney."""
        responses = {
            "/locations?query=Passau": {"json": MOCK_LOCATIONS_PASSAU},
            "/locations?query=München": {"json": MOCK_LOCATIONS_MUNICH},
            "/journeys": {"json": MOCK_JOURNEY_DIRECT},
        }
        client = httpx.Client(
            base_url="https://mock.transport.rest",
            transport=build_mock_transport(responses),
        )
        provider = LiveTransportProvider(client=client)

        journeys = provider.query_connections(
            origin="Passau Hbf",
            destination="München Hbf",
            departure_time=datetime(2026, 9, 8, 8, 30),
            max_results=2,
        )

        self.assertEqual(len(journeys), 1)
        j = journeys[0]
        self.assertIsInstance(j, TransportJourney)
        self.assertEqual(j.origin, "Passau Hbf")
        self.assertEqual(j.destination, "München Hbf")
        self.assertEqual(j.departure_time, "2026-09-08T09:25:00+02:00")
        self.assertEqual(j.arrival_time, "2026-09-08T11:30:00+02:00")
        self.assertEqual(j.duration_minutes, 125)
        self.assertEqual(j.transfers, 0)
        self.assertEqual(len(j.legs), 1)
        self.assertEqual(j.legs[0].line, "RE 3")
        self.assertEqual(j.legs[0].platform, "5")

    def test_successful_multi_leg_journey_normalization(self) -> None:
        """4. Test multi-leg journey with 1 transfer properly normalizes duration and legs."""
        responses = {
            "/locations?query=Passau": {"json": MOCK_LOCATIONS_PASSAU},
            "/locations?query=Deggendorf": {
                "json": [{"type": "stop", "id": "8000300", "name": "Deggendorf"}]
            },
            "/journeys": {"json": MOCK_JOURNEY_MULTI_LEG},
        }
        client = httpx.Client(
            base_url="https://mock.transport.rest",
            transport=build_mock_transport(responses),
        )
        provider = LiveTransportProvider(client=client)

        journeys = provider.query_connections(
            origin="Passau Hbf",
            destination="Deggendorf",
            departure_time=datetime(2026, 9, 8, 10, 0),
            max_results=1,
        )

        self.assertEqual(len(journeys), 1)
        j = journeys[0]
        self.assertEqual(j.transfers, 1)
        self.assertEqual(len(j.legs), 2)
        self.assertEqual(j.legs[0].line, "ICE 28")
        self.assertEqual(j.legs[0].platform, "3")
        self.assertEqual(j.legs[1].line, "RB 35")
        self.assertEqual(j.legs[1].platform, "4")
        self.assertEqual(j.duration_minutes, 72)

    def test_no_results_returns_empty_list_success(self) -> None:
        """5. Verify HTTP 404 or empty journeys array returns empty list with no error."""
        responses = {
            "/locations?query=Passau": {"json": MOCK_LOCATIONS_PASSAU},
            "/locations?query=München": {"json": MOCK_LOCATIONS_MUNICH},
            "/journeys": {"json": {"journeys": []}},
        }
        client = httpx.Client(
            base_url="https://mock.transport.rest",
            transport=build_mock_transport(responses),
        )
        service = TransportService(provider=LiveTransportProvider(client=client))

        res = service.find_connections(
            origin="Passau Hbf",
            destination="München Hbf",
            departure_time="2026-09-08T23:59:00",
        )

        self.assertEqual(res["data_source"], "transport_rest_live")
        self.assertEqual(res["total_found"], 0)
        self.assertEqual(res["connections"], [])

    def test_timeout_maps_to_provider_unavailable(self) -> None:
        """6. Verify timeout exception maps to PROVIDER_UNAVAILABLE."""
        responses = {
            "/locations?query=Passau": httpx.TimeoutException("Read timed out"),
        }
        client = httpx.Client(
            base_url="https://mock.transport.rest",
            transport=build_mock_transport(responses),
        )
        provider = LiveTransportProvider(client=client)

        with self.assertRaises(TransportDomainError) as ctx:
            provider.search_stations("Passau")
        self.assertEqual(ctx.exception.code, TransportErrorCode.PROVIDER_UNAVAILABLE)

    def test_429_maps_to_rate_limited(self) -> None:
        """7. Verify HTTP 429 maps to RATE_LIMITED."""
        responses = {
            "/locations?query=Passau": {"status": 429, "content": b"Rate limit exceeded"},
        }
        client = httpx.Client(
            base_url="https://mock.transport.rest",
            transport=build_mock_transport(responses),
        )
        provider = LiveTransportProvider(client=client)

        with self.assertRaises(TransportDomainError) as ctx:
            provider.search_stations("Passau")
        self.assertEqual(ctx.exception.code, TransportErrorCode.RATE_LIMITED)

    def test_malformed_json_maps_to_internal_error(self) -> None:
        """8. Verify malformed provider JSON raises INTERNAL_ERROR."""
        responses = {
            "/locations?query=Passau": {"status": 200, "content": b"<!DOCTYPE html><html>Gateway error</html>"},
        }
        client = httpx.Client(
            base_url="https://mock.transport.rest",
            transport=build_mock_transport(responses),
        )
        provider = LiveTransportProvider(client=client)

        with self.assertRaises(TransportDomainError) as ctx:
            provider.search_stations("Passau")
        self.assertEqual(ctx.exception.code, TransportErrorCode.INTERNAL_ERROR)

    def test_no_silent_fallback_by_default(self) -> None:
        """9. Test default behavior raises PROVIDER_UNAVAILABLE rather than silently returning synthetic data."""
        responses = {
            "/locations?query=Passau": {"status": 503, "content": b"Gateway error"},
        }
        client = httpx.Client(
            base_url="https://mock.transport.rest",
            transport=build_mock_transport(responses),
        )
        live_provider = LiveTransportProvider(client=client)
        synthetic_provider = SyntheticTransportProvider()

        service = TransportService(
            provider=live_provider,
            fallback_provider=synthetic_provider,
            fallback_enabled=False,  # default
        )

        with self.assertRaises(TransportDomainError) as ctx:
            service.find_connections("Passau Hbf", "München Hbf", "2026-09-08T08:30:00")
        self.assertEqual(ctx.exception.code, TransportErrorCode.PROVIDER_UNAVAILABLE)

    def test_explicit_fallback_tags_provenance(self) -> None:
        """10. Test explicit fallback when enabled gracefully falls back and tags data_source."""
        responses = {
            "/locations?query=Passau": {"status": 503, "content": b"Gateway error"},
        }
        client = httpx.Client(
            base_url="https://mock.transport.rest",
            transport=build_mock_transport(responses),
        )
        live_provider = LiveTransportProvider(client=client)
        synthetic_provider = SyntheticTransportProvider()

        service = TransportService(
            provider=live_provider,
            fallback_provider=synthetic_provider,
            fallback_enabled=True,  # explicitly enabled
        )

        res = service.find_connections("Passau Hbf", "München Hbf", "2026-09-08T08:30:00")
        self.assertIn("(fallback)", res["data_source"])
        self.assertGreater(res["total_found"], 0)

    def test_provider_selection_from_config(self) -> None:
        """11. Test create_transport_service factory instantiates live or synthetic based on config."""
        cfg_synthetic = AppConfig(
            agent=AgentConfig(max_steps=5),
            llm=LLMConfig(base_url="http://mock", model="m", temperature=0.0),
            tools=ToolsConfig(workspace_root="./workspace"),
            transport=TransportConfig(provider="synthetic"),
        )
        service_syn = create_transport_service(cfg_synthetic)
        self.assertIsInstance(service_syn.provider, SyntheticTransportProvider)
        self.assertEqual(service_syn.provider.provider_name, "synthetic_bavarian_timetable_v1")

        cfg_live = AppConfig(
            agent=AgentConfig(max_steps=5),
            llm=LLMConfig(base_url="http://mock", model="m", temperature=0.0),
            tools=ToolsConfig(workspace_root="./workspace"),
            transport=TransportConfig(
                provider="live",
                live=TransportLiveConfig(base_url="https://mock.live.rest"),
            ),
        )
        service_live = create_transport_service(cfg_live)
        self.assertIsInstance(service_live.provider, LiveTransportProvider)
        self.assertEqual(service_live.provider.provider_name, "transport_rest_live")

    @unittest.skipUnless(
        os.environ.get("RUN_LIVE_TRANSPORT_TESTS") == "1",
        "Live network tests disabled by default; enable with RUN_LIVE_TRANSPORT_TESTS=1",
    )
    def test_live_network_smoke_test(self) -> None:
        """12. Optional live network test (skipped unless RUN_LIVE_TRANSPORT_TESTS=1)."""
        provider = LiveTransportProvider(base_url="https://v6.db.transport.rest")
        stations = provider.search_stations("Passau Hbf")
        self.assertTrue(len(stations) > 0)
        self.assertEqual(stations[0].name, "Passau Hbf")
