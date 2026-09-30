"""Deterministic synthetic Bavarian rail timetable backend (Backward Compatibility Facade).

Scope & Provenance Note:
    This module preserves backward compatibility with Phase 2 callers while
    delegating to the decoupled TransportProvider and TransportService abstractions.
    The synthetic schedules, lines, and platforms remain deterministic fixtures for testing.
"""

from datetime import datetime
from typing import Any

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
from harness.mcp.servers.transport_service import TransportService

DATA_SOURCE_NAME = SyntheticTransportProvider.DATA_SOURCE_NAME
SUPPORTED_STATIONS = SyntheticTransportProvider.SUPPORTED_STATIONS
_LINE_DEFINITIONS = SyntheticTransportProvider._LINE_DEFINITIONS
_STATION_LOOKUP: dict[str, str] = {s.lower(): s for s in SUPPORTED_STATIONS}

# Alias for backward compatibility
Connection = TransportJourney


def normalize_station(name: str) -> str:
    """Normalize and validate a station name against the bounded station set.

    Raises:
        ValueError: If station is unrecognized.
    """
    cleaned = name.strip().lower()
    if cleaned not in _STATION_LOOKUP:
        valid_list = ", ".join(f"'{s}'" for s in SUPPORTED_STATIONS)
        raise ValueError(f"Unknown station '{name}'. Supported stations are: {valid_list}")
    return _STATION_LOOKUP[cleaned]


def parse_departure_time(iso_str: str) -> datetime:
    """Parse an ISO 8601 departure timestamp.

    Raises:
        ValueError: If the string cannot be parsed as a valid ISO 8601 datetime.
    """
    cleaned = iso_str.strip()
    try:
        dt = datetime.fromisoformat(cleaned)
        return dt.replace(tzinfo=None)
    except Exception as e:
        raise ValueError(
            f"Invalid departure_time format '{iso_str}'. Expected ISO 8601 string (e.g. '2026-09-08T08:30:00')."
        ) from e


def _generate_line_runs(target_date: Any) -> list[dict[str, Any]]:
    """Legacy helper delegating to SyntheticTransportProvider._generate_line_runs."""
    provider = SyntheticTransportProvider()
    return provider._generate_line_runs(target_date)


def find_connections(
    origin: str,
    destination: str,
    departure_time: str,
    max_results: int = 3,
) -> dict[str, Any]:
    """Legacy entrypoint delegating to TransportService.

    Raises:
        ValueError: On validation failure or domain errors (for backward compatibility).
    """
    service = TransportService()
    try:
        return service.find_connections(
            origin=origin,
            destination=destination,
            departure_time=departure_time,
            max_results=max_results,
        )
    except TransportDomainError as e:
        raise ValueError(e.message) from e
