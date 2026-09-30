"""Observability URL construction and host-facing navigation for Grafana and Tempo."""

from __future__ import annotations

import json
import logging
import os
import urllib.parse
from typing import Any

logger = logging.getLogger("harness.tui.observability")

# Actual configuration values derived from:
# - docker-compose.yml (127.0.0.1:3000:3000)
# - observability/grafana/dashboards/agent-harness-runtime.json (uid: "agent-harness-runtime", title: "Agent Harness Operations")
# - observability/grafana/provisioning/datasources/datasource.yml (name: "Tempo", uid: "tempo")
DEFAULT_GRAFANA_HOST_PORT = 3000
GRAFANA_DASHBOARD_UID = "agent-harness-runtime"
GRAFANA_DASHBOARD_SLUG = "agent-harness-operations"
TEMPO_DATASOURCE_UID = "tempo"
TEMPO_DATASOURCE_NAME = "Tempo"


def get_grafana_base_url() -> str:
    """Return the host-facing Grafana base URL, allowing environment override."""
    env_url = os.environ.get("GRAFANA_HOST_URL", "").strip()
    if env_url:
        return env_url.rstrip("/")
    return f"http://localhost:{DEFAULT_GRAFANA_HOST_PORT}"


def get_grafana_dashboard_url() -> str:
    """Return the full host-facing URL to the provisioned operations dashboard."""
    base = get_grafana_base_url()
    return f"{base}/d/{GRAFANA_DASHBOARD_UID}/{GRAFANA_DASHBOARD_SLUG}"


def build_tempo_trace_url(trace_id: str) -> str:
    """Construct a version-compatible Grafana Explore URL for the given trace ID.

    Uses the provisioned Tempo datasource UID and queryType='traceId' with full URL encoding.
    """
    clean_trace_id = trace_id.strip()
    base = get_grafana_base_url()

    payload: dict[str, Any] = {
        "datasource": TEMPO_DATASOURCE_UID,
        "queries": [
            {
                "refId": "A",
                "datasource": {
                    "type": "tempo",
                    "uid": TEMPO_DATASOURCE_UID,
                },
                "queryType": "traceId",
                "query": clean_trace_id,
            }
        ],
    }
    encoded_query = urllib.parse.quote(json.dumps(payload))
    return f"{base}/explore?left={encoded_query}"


def open_host_browser(url: str) -> bool:
    """Attempt to open the given URL in the system default browser.

    Best-effort execution: returns True if webbrowser reported launch, False if unsupported/failed.
    Never raises exceptions.
    """
    try:
        import webbrowser
        return bool(webbrowser.open(url))
    except Exception as exc:
        logger.debug(f"webbrowser.open failed for {url}: {exc}")
        return False
