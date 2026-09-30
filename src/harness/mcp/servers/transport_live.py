"""Live transport provider querying external transport.rest / db-rest REST endpoints."""

from datetime import datetime
import logging
import os
from typing import Any
import httpx

from harness.mcp.servers.transport_models import (
    JourneyLeg,
    Station,
    TransportDomainError,
    TransportErrorCode,
    TransportJourney,
)
from harness.mcp.servers.transport_provider import TransportProvider

logger = logging.getLogger("harness.transport.live")


class LiveTransportProvider(TransportProvider):
    """Live transit provider implementing TransportProvider against transport.rest.

    Deployment Note:
        The default public endpoint (https://v6.db.transport.rest) is intended
        for local development and integration testing. For production deployments,
        TRANSPORT_API_URL should be configured to point to a controlled, self-hosted
        container (e.g. derhuerst/db-rest) or contracted enterprise transit endpoint.
    """

    DATA_SOURCE_NAME = "transport_rest_live"

    def __init__(
        self,
        base_url: str = "https://v6.db.transport.rest",
        api_key: str | None = None,
        timeout_seconds: float = 8.0,
        connect_timeout_seconds: float = 3.0,
        max_retries: int = 1,
        client: httpx.Client | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._timeout_seconds = timeout_seconds
        self._connect_timeout = connect_timeout_seconds
        self._max_retries = max_retries

        headers: dict[str, str] = {
            "Accept": "application/json",
            "User-Agent": "agent-harness-live-transport/1.0",
        }
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"

        timeout = httpx.Timeout(
            self._timeout_seconds,
            connect=self._connect_timeout,
            read=self._timeout_seconds,
        )
        limits = httpx.Limits(max_keepalive_connections=5, max_connections=10)

        self._client = client or httpx.Client(
            base_url=self._base_url,
            headers=headers,
            timeout=timeout,
            limits=limits,
        )
        # Cache canonical station name -> provider station ID
        self._id_cache: dict[str, str] = {}

    @property
    def provider_name(self) -> str:
        return self.DATA_SOURCE_NAME

    def close(self) -> None:
        """Release underlying HTTP client resources."""
        self._client.close()

    def __enter__(self) -> "LiveTransportProvider":
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        self.close()

    def _execute_http(self, method: str, url: str, params: dict[str, Any] | None = None) -> httpx.Response:
        """Execute HTTP request with bounded retry on transient connection failures."""
        last_exc: Exception | None = None
        for attempt in range(self._max_retries + 1):
            try:
                return self._client.request(method, url, params=params)
            except (httpx.ConnectError, httpx.ConnectTimeout) as e:
                last_exc = e
                logger.warning(f"Live transport connection attempt {attempt + 1} failed: {e}")
                if attempt < self._max_retries:
                    continue
            except httpx.TimeoutException as e:
                logger.warning(f"Live transport request timed out: {e}")
                raise TransportDomainError(
                    TransportErrorCode.PROVIDER_UNAVAILABLE,
                    f"Live transport provider timed out after {self._timeout_seconds}s.",
                ) from e
            except httpx.RequestError as e:
                logger.warning(f"Live transport network error: {e}")
                raise TransportDomainError(
                    TransportErrorCode.PROVIDER_UNAVAILABLE,
                    f"Live transport network communication failed: {e}",
                ) from e

        raise TransportDomainError(
            TransportErrorCode.PROVIDER_UNAVAILABLE,
            f"Live transport provider unreachable after {self._max_retries + 1} attempts: {last_exc}",
        ) from last_exc

    def search_stations(self, query: str) -> list[Station]:
        cleaned = query.strip()
        if not cleaned:
            return []

        resp = self._execute_http(
            "GET",
            "/locations",
            params={
                "query": cleaned,
                "results": 5,
                "stops": "true",
                "addresses": "false",
                "poi": "false",
            },
        )
        if resp.status_code == 429:
            raise TransportDomainError(
                TransportErrorCode.RATE_LIMITED,
                "Live transport provider rate limit exceeded (HTTP 429).",
            )
        if resp.status_code in (500, 502, 503, 504):
            raise TransportDomainError(
                TransportErrorCode.PROVIDER_UNAVAILABLE,
                f"Live transport gateway error (HTTP {resp.status_code}).",
            )
        if resp.status_code != 200:
            raise TransportDomainError(
                TransportErrorCode.PROVIDER_UNAVAILABLE,
                f"Live transport station lookup failed with HTTP {resp.status_code}.",
            )

        try:
            payload = resp.json()
        except Exception as e:
            raise TransportDomainError(
                TransportErrorCode.INTERNAL_ERROR,
                f"Failed to parse live transport provider JSON: {e}",
            ) from e

        if not isinstance(payload, list):
            raise TransportDomainError(
                TransportErrorCode.INTERNAL_ERROR,
                "Malformed provider JSON: expected array of locations.",
            )

        stations: list[Station] = []
        for loc in payload:
            if not isinstance(loc, dict):
                continue
            loc_type = loc.get("type")
            if loc_type in ("stop", "station") and "id" in loc and "name" in loc:
                stations.append(Station(id=str(loc["id"]), name=str(loc["name"])))
        return stations

    def _resolve_single_station(self, name: str) -> Station:
        cleaned = name.strip()
        if not cleaned:
            raise TransportDomainError(
                TransportErrorCode.INVALID_STATION,
                "Station name cannot be empty.",
            )

        stations = self.search_stations(cleaned)
        if not stations:
            raise TransportDomainError(
                TransportErrorCode.INVALID_STATION,
                f"Unknown station '{name}'. No matching rail stations found.",
            )

        # Deterministic resolution: exact case-insensitive match preferred
        cleaned_lower = cleaned.lower()
        for s in stations:
            if s.name.lower() == cleaned_lower:
                return s

        # Otherwise pick top relevance match returned by provider
        return stations[0]

    def validate_stations(self, origin: str, destination: str) -> tuple[str, str]:
        st_orig = self._resolve_single_station(origin)
        st_dest = self._resolve_single_station(destination)

        if st_orig.id == st_dest.id or st_orig.name.lower() == st_dest.name.lower():
            raise TransportDomainError(
                TransportErrorCode.INVALID_STATION,
                f"Origin and destination stations cannot be identical ('{st_orig.name}').",
            )

        self._id_cache[st_orig.name] = st_orig.id
        self._id_cache[st_dest.name] = st_dest.id
        return st_orig.name, st_dest.name

    def query_connections(
        self,
        origin: str,
        destination: str,
        departure_time: datetime,
        max_results: int,
    ) -> list[TransportJourney]:
        orig_id = self._id_cache.get(origin) or self._resolve_single_station(origin).id
        dest_id = self._id_cache.get(destination) or self._resolve_single_station(destination).id

        params: dict[str, Any] = {
            "from": orig_id,
            "to": dest_id,
            "departure": departure_time.isoformat(),
            "results": max_results,
            "transfers": 3,
            "stopovers": "false",
        }

        resp = self._execute_http("GET", "/journeys", params=params)

        if resp.status_code == 400:
            raise TransportDomainError(
                TransportErrorCode.INVALID_ARGUMENT,
                f"Live transport provider rejected query: {resp.text}",
            )
        if resp.status_code == 404:
            # Valid query with zero matching departures
            return []
        if resp.status_code == 429:
            raise TransportDomainError(
                TransportErrorCode.RATE_LIMITED,
                "Live transport provider rate limit exceeded (HTTP 429).",
            )
        if resp.status_code in (500, 502, 503, 504):
            raise TransportDomainError(
                TransportErrorCode.PROVIDER_UNAVAILABLE,
                f"Live transport gateway error (HTTP {resp.status_code}).",
            )
        if resp.status_code != 200:
            raise TransportDomainError(
                TransportErrorCode.PROVIDER_UNAVAILABLE,
                f"Live transport query failed with HTTP {resp.status_code}.",
            )

        try:
            payload = resp.json()
        except Exception as e:
            raise TransportDomainError(
                TransportErrorCode.INTERNAL_ERROR,
                f"Failed to parse live transport provider JSON: {e}",
            ) from e

        if not isinstance(payload, dict) or "journeys" not in payload:
            raise TransportDomainError(
                TransportErrorCode.INTERNAL_ERROR,
                "Malformed provider response: missing 'journeys' list.",
            )

        raw_journeys = payload.get("journeys", [])
        if not isinstance(raw_journeys, list):
            raise TransportDomainError(
                TransportErrorCode.INTERNAL_ERROR,
                "Malformed provider response: 'journeys' is not a list.",
            )

        journeys: list[TransportJourney] = []
        for idx, j in enumerate(raw_journeys):
            if not isinstance(j, dict):
                continue
            legs_data = j.get("legs", [])
            if not isinstance(legs_data, list) or not legs_data:
                continue

            journey_legs: list[JourneyLeg] = []
            for leg in legs_data:
                if not isinstance(leg, dict):
                    continue
                line_info = leg.get("line") or {}
                line_name = line_info.get("name") or line_info.get("id") or "Train"
                from_st = leg.get("origin", {}).get("name") or origin
                to_st = leg.get("destination", {}).get("name") or destination
                dep_time = leg.get("departure") or leg.get("plannedDeparture") or ""
                arr_time = leg.get("arrival") or leg.get("plannedArrival") or ""
                plat = str(leg.get("departurePlatform") or leg.get("plannedDeparturePlatform") or "")

                journey_legs.append(
                    JourneyLeg(
                        line=line_name,
                        from_station=from_st,
                        to_station=to_st,
                        departure_time=dep_time,
                        arrival_time=arr_time,
                        platform=plat,
                    )
                )

            if not journey_legs:
                continue

            start_dep = journey_legs[0].departure_time
            end_arr = journey_legs[-1].arrival_time

            duration = 0
            try:
                t1 = datetime.fromisoformat(start_dep)
                t2 = datetime.fromisoformat(end_arr)
                duration = max(0, int((t2 - t1).total_seconds() // 60))
            except Exception:
                duration = 0

            primary_line = journey_legs[0].line.replace(" ", "")
            time_tag = start_dep[11:16].replace(":", "") if len(start_dep) >= 16 else f"{idx:02d}"
            conn_id = f"LIVE-{primary_line}-{time_tag}"

            journeys.append(
                TransportJourney(
                    connection_id=conn_id,
                    origin=origin,
                    destination=destination,
                    departure_time=start_dep,
                    arrival_time=end_arr,
                    duration_minutes=duration,
                    transfers=max(0, len(journey_legs) - 1),
                    legs=journey_legs,
                )
            )

        return journeys[:max_results]
