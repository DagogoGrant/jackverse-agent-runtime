"""HTTP exposition server for Prometheus metrics using official prometheus_client."""

from __future__ import annotations

import logging
import threading
from typing import Any

from prometheus_client import CollectorRegistry, start_http_server

logger = logging.getLogger("harness.observability.server")


class MetricsServer:
    """Wrapper around official prometheus_client HTTP exposition server with clean lifecycle."""

    def __init__(self, httpd: Any, thread: threading.Thread, host: str, port: int) -> None:
        self._httpd = httpd
        self._thread = thread
        self._host = host
        # Record the actual bound port (crucial when port=0 is used in tests)
        self._bound_port = int(httpd.server_address[1]) if hasattr(httpd, "server_address") else port

    @property
    def host(self) -> str:
        """The host address bound by the metrics server."""
        return self._host

    @property
    def port(self) -> int:
        """The actual TCP port bound by the metrics server."""
        return self._bound_port

    def stop(self, timeout: float = 2.0) -> None:
        """Gracefully shut down HTTP exposition server and join the background daemon thread."""
        try:
            self._httpd.shutdown()
            self._httpd.server_close()
            self._thread.join(timeout=timeout)
            logger.info(f"Prometheus metrics server on {self._host}:{self._bound_port} stopped successfully.")
        except Exception as exc:
            logger.warning(f"Error while shutting down Prometheus metrics server: {exc}")


def start_metrics_server(
    registry: CollectorRegistry,
    host: str = "0.0.0.0",
    port: int = 9101,
    required: bool = True,
) -> MetricsServer | None:
    """Start the official Prometheus HTTP exposition server on a background daemon thread.

    Args:
        registry: The CollectorRegistry containing metrics to expose.
        host: Interface address to bind.
        port: TCP port to bind (default 9101).
        required: If True, port binding failures will raise RuntimeError (fail-fast startup).
                  If False, a warning is logged and None is returned.

    Returns:
        A MetricsServer instance with clean lifecycle control, or None if optional and failed to bind.
    """
    try:
        httpd, thread = start_http_server(
            port=port,
            addr=host,
            registry=registry,
        )
        actual_port = httpd.server_address[1] if hasattr(httpd, "server_address") else port
        logger.info(f"Prometheus metrics HTTP server listening on http://{host}:{actual_port}/metrics")
        return MetricsServer(httpd=httpd, thread=thread, host=host, port=port)
    except OSError as exc:
        if required:
            raise RuntimeError(
                f"Failed to bind required Prometheus metrics server to {host}:{port}: {exc}. "
                "Exposition server is marked required by configuration; terminating startup."
            ) from exc
        logger.warning(
            f"Failed to bind optional Prometheus metrics server to {host}:{port}: {exc}. "
            "Continuing agent harness without Prometheus exposition."
        )
        return None
