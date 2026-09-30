"""Transport service orchestrating validation, provider queries, and response structuring."""

from dataclasses import asdict
from datetime import datetime
import logging
import os
from typing import Any

from harness.mcp.servers.transport_models import (
    TransportDomainError,
    TransportErrorCode,
)
from harness.mcp.servers.transport_provider import (
    SyntheticTransportProvider,
    TransportProvider,
)

logger = logging.getLogger("harness.transport.service")


class TransportService:
    """Domain service mediating transport requests between callers/MCP and underlying providers."""

    def __init__(
        self,
        provider: TransportProvider | None = None,
        fallback_provider: TransportProvider | None = None,
        fallback_enabled: bool = False,
    ) -> None:
        self._provider = provider or SyntheticTransportProvider()
        self._fallback_provider = fallback_provider
        self._fallback_enabled = fallback_enabled

    @property
    def provider(self) -> TransportProvider:
        """The active underlying transport provider."""
        return self._provider

    @property
    def fallback_enabled(self) -> bool:
        """Whether fallback to secondary provider is active."""
        return self._fallback_enabled

    def find_connections(
        self,
        origin: str,
        destination: str,
        departure_time: str,
        max_results: int = 3,
    ) -> dict[str, Any]:
        """Validate input parameters, query the provider, and build a standard structured response.

        Returns:
            Standard dictionary conforming to the find_connection output schema:
            - data_source
            - query
            - total_found
            - connections

        Raises:
            TransportDomainError: On validation failures, unknown stations, or provider errors.
        """
        # 1. Parameter validation
        if not isinstance(origin, str) or not origin.strip():
            raise TransportDomainError(
                TransportErrorCode.INVALID_ARGUMENT,
                "Parameter 'origin' must be a non-empty string.",
            )
        if not isinstance(destination, str) or not destination.strip():
            raise TransportDomainError(
                TransportErrorCode.INVALID_ARGUMENT,
                "Parameter 'destination' must be a non-empty string.",
            )
        if not isinstance(departure_time, str) or not departure_time.strip():
            raise TransportDomainError(
                TransportErrorCode.INVALID_ARGUMENT,
                "Parameter 'departure_time' must be a non-empty string.",
            )
        if not isinstance(max_results, int) or isinstance(max_results, bool):
            raise TransportDomainError(
                TransportErrorCode.INVALID_ARGUMENT,
                "Parameter 'max_results' must be an integer between 1 and 5.",
            )
        if max_results < 1 or max_results > 5:
            raise TransportDomainError(
                TransportErrorCode.INVALID_ARGUMENT,
                "Parameter 'max_results' must be between 1 and 5.",
            )

        # 2. ISO 8601 parsing
        cleaned_time = departure_time.strip()
        try:
            parsed_dt = datetime.fromisoformat(cleaned_time)
            parsed_dt = parsed_dt.replace(tzinfo=None)
        except Exception as e:
            raise TransportDomainError(
                TransportErrorCode.INVALID_TIME,
                f"Invalid departure_time format '{departure_time}'. Expected ISO 8601 string (e.g. '2026-09-08T08:30:00').",
            ) from e

        # 3. Execution against primary provider with optional explicit fallback
        try:
            norm_origin, norm_dest = self._provider.validate_stations(origin, destination)
            journeys = self._provider.query_connections(
                origin=norm_origin,
                destination=norm_dest,
                departure_time=parsed_dt,
                max_results=max_results,
            )
            data_source = self._provider.provider_name
        except TransportDomainError as e:
            if (
                self._fallback_enabled
                and self._fallback_provider is not None
                and e.code in (TransportErrorCode.PROVIDER_UNAVAILABLE, TransportErrorCode.RATE_LIMITED)
            ):
                logger.warning(
                    f"Primary provider '{self._provider.provider_name}' failed with {e.code.value}: {e.message}. "
                    f"Falling back to '{self._fallback_provider.provider_name}'."
                )
                norm_origin, norm_dest = self._fallback_provider.validate_stations(origin, destination)
                journeys = self._fallback_provider.query_connections(
                    origin=norm_origin,
                    destination=norm_dest,
                    departure_time=parsed_dt,
                    max_results=max_results,
                )
                data_source = f"{self._fallback_provider.provider_name} (fallback)"
            else:
                raise e

        # 4. Schema composition
        return {
            "data_source": data_source,
            "query": {
                "origin": norm_origin,
                "destination": norm_dest,
                "earliest_departure": parsed_dt.isoformat(),
                "max_results": max_results,
            },
            "total_found": len(journeys),
            "connections": [
                {
                    "connection_id": j.connection_id,
                    "origin": j.origin,
                    "destination": j.destination,
                    "departure_time": j.departure_time,
                    "arrival_time": j.arrival_time,
                    "duration_minutes": j.duration_minutes,
                    "transfers": j.transfers,
                    "legs": [asdict(l) for l in j.legs],
                }
                for j in journeys
            ],
        }


def create_transport_service(config: Any = None) -> TransportService:
    """Factory creating a TransportService configured from configuration or environment."""
    raw_provider = os.environ.get("TRANSPORT_PROVIDER")
    raw_base_url = os.environ.get("TRANSPORT_API_URL")
    raw_api_key = os.environ.get("TRANSPORT_API_KEY")
    raw_timeout = os.environ.get("TRANSPORT_TIMEOUT_SECONDS")
    raw_fallback = os.environ.get("TRANSPORT_FALLBACK_TO_SYNTHETIC")

    provider_name = "synthetic"
    base_url = "https://v6.db.transport.rest"
    api_key: str | None = None
    timeout_sec = 8.0
    fallback_to_synthetic = False

    if config is not None and hasattr(config, "transport"):
        t_cfg = config.transport
        provider_name = t_cfg.provider
        base_url = t_cfg.live.base_url
        if t_cfg.live.api_key_env:
            api_key = os.environ.get(t_cfg.live.api_key_env)
        timeout_sec = t_cfg.live.timeout_seconds
        fallback_to_synthetic = t_cfg.live.fallback_to_synthetic

    # Environment variables override file configuration
    if raw_provider:
        provider_name = raw_provider.strip().lower()
    if raw_base_url:
        base_url = raw_base_url.strip()
    if raw_api_key:
        api_key = raw_api_key.strip()
    if raw_timeout:
        try:
            timeout_sec = float(raw_timeout)
        except ValueError:
            pass
    if raw_fallback is not None:
        fallback_to_synthetic = raw_fallback.strip().lower() in ("1", "true", "yes")

    if provider_name == "live":
        from harness.mcp.servers.transport_live import LiveTransportProvider
        live_prov = LiveTransportProvider(
            base_url=base_url,
            api_key=api_key,
            timeout_seconds=timeout_sec,
        )
        fallback_prov = SyntheticTransportProvider() if fallback_to_synthetic else None
        return TransportService(
            provider=live_prov,
            fallback_provider=fallback_prov,
            fallback_enabled=fallback_to_synthetic,
        )

    return TransportService(provider=SyntheticTransportProvider())
