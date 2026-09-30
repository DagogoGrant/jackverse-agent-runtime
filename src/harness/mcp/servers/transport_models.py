"""Domain models and structured error taxonomy for the transport service."""

from dataclasses import dataclass
from enum import Enum
from typing import Any


class TransportErrorCode(str, Enum):
    """Machine-readable domain error codes for transport operations."""

    INVALID_ARGUMENT = "INVALID_ARGUMENT"      # Malformed primitive arguments or limits
    INVALID_STATION = "INVALID_STATION"        # Unknown station or identical origin/destination
    INVALID_TIME = "INVALID_TIME"              # Malformed ISO 8601 timestamp
    NO_SERVICE = "NO_SERVICE"                  # Station or line not currently operating
    PROVIDER_UNAVAILABLE = "PROVIDER_UNAVAILABLE"  # Upstream transport API unreachable
    RATE_LIMITED = "RATE_LIMITED"              # Provider rate limit exceeded
    INTERNAL_ERROR = "INTERNAL_ERROR"          # Unexpected provider failure


class TransportDomainError(Exception):
    """Structured domain error raised by transport providers and services."""

    def __init__(
        self,
        code: TransportErrorCode,
        message: str,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details or {}

    def __str__(self) -> str:
        return f"[{self.code.value}] {self.message}"


@dataclass(frozen=True)
class Station:
    """Canonical representation of a transit station."""

    id: str
    name: str
    platform: str | None = None


@dataclass(frozen=True)
class JourneyLeg:
    """Individual segment of a multi-leg or direct transit journey."""

    line: str
    from_station: str
    to_station: str
    departure_time: str
    arrival_time: str
    platform: str


@dataclass(frozen=True)
class TransportJourney:
    """Complete scheduled transit connection from origin to destination."""

    connection_id: str
    origin: str
    destination: str
    departure_time: str
    arrival_time: str
    duration_minutes: int
    transfers: int
    legs: list[JourneyLeg]


# Aliased for backward compatibility with existing code
Connection = TransportJourney
