"""Evidence-based runtime health probe utilities for the Operator Console.

Requirement: Never hardcode status labels or claim 'SCRAPING' / 'CONNECTED' without
an actual bounded check. Use accurate states: CONFIGURED, REGISTERED, OFFLINE, UNREACHABLE.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any
import urllib.error
import urllib.request


def probe_http_endpoint(
    url: str,
    timeout_seconds: float = 0.5,
    acceptable_status_codes: tuple[int, ...] = (200,),
) -> tuple[str, bool]:
    """Perform a bounded HTTP GET probe without external library dependencies."""
    req = urllib.request.Request(
        url,
        headers={"User-Agent": "AgentHarness-TUIHealthProbe/1.0", "Accept": "*/*"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout_seconds) as resp:
            if resp.status in acceptable_status_codes:
                return f"● READY ({resp.status})", True
            return f"○ UNEXPECTED_STATUS ({resp.status})", False
    except urllib.error.HTTPError as err:
        if err.code in acceptable_status_codes:
            return f"● RESPONDING ({err.code})", True
        return f"○ HTTP_{err.code}", False
    except (urllib.error.URLError, TimeoutError, OSError):
        return "○ UNREACHABLE", False
    except Exception as e:
        return f"○ ERROR ({type(e).__name__})", False


def check_prometheus_scrape_health(
    host: str = "127.0.0.1",
    port: int = 9090,
    timeout: float = 0.5,
) -> tuple[str, bool]:
    """Check if Prometheus server is running and whether it is actively scraping harness:9101."""
    hosts_to_try = [host]
    if host in ("127.0.0.1", "localhost"):
        hosts_to_try.append("prometheus")

    for h in hosts_to_try:
        targets_url = f"http://{h}:{port}/api/v1/targets"
        req = urllib.request.Request(targets_url, headers={"Accept": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                if resp.status == 200:
                    import json
                    payload = json.loads(resp.read().decode("utf-8"))
                    active = payload.get("data", {}).get("activeTargets", [])
                    harness_target = next((t for t in active if "9101" in t.get("scrapeUrl", "")), None)
                    if harness_target:
                        health = harness_target.get("health", "unknown")
                        if health == "up":
                            return "● SCRAPING (:9101 UP)", True
                        return f"● CONNECTED (Target {health.upper()})", True
                    return "● UP (:9090 Active)", True
        except Exception:
            pass

        health_url = f"http://{h}:{port}/-/healthy"
        status, ok = probe_http_endpoint(health_url, timeout_seconds=timeout, acceptable_status_codes=(200,))
        if ok:
            return "● UP (:9090 Active)", True

    return "○ UNREACHABLE", False


def check_metrics_endpoint_health(
    host: str = "127.0.0.1",
    port: int = 9101,
    timeout: float = 0.5,
) -> tuple[str, bool]:
    """Check if the harness Prometheus metrics HTTP exposition server is listening."""
    url = f"http://{host}:{port}/metrics"
    status, ok = probe_http_endpoint(url, timeout_seconds=timeout, acceptable_status_codes=(200,))
    if ok:
        return "● ACTIVE (:9101)", True
    return "○ OFFLINE", False


def check_tempo_health(
    host: str = "127.0.0.1",
    port: int = 3200,
    timeout: float = 0.5,
) -> tuple[str, bool]:
    """Check if Grafana Tempo trace collector is ready."""
    hosts_to_try = [host]
    if host in ("127.0.0.1", "localhost"):
        hosts_to_try.append("tempo")

    for h in hosts_to_try:
        url = f"http://{h}:{port}/ready"
        status, ok = probe_http_endpoint(url, timeout_seconds=timeout, acceptable_status_codes=(200,))
        if ok:
            return "● READY (:3200)", True

    return "○ UNREACHABLE", False


def check_mcp_transport_health(
    endpoint_url: str = "http://127.0.0.1:8000/mcp",
    timeout: float = 0.5,
) -> tuple[str, bool]:
    """Check if the transport-mcp server is responding over HTTP.

    Accepts 200, 400, 405, 406 as evidence that the MCP HTTP server is active and responding.
    """
    urls_to_try = [endpoint_url]
    if "127.0.0.1" in endpoint_url or "localhost" in endpoint_url:
        urls_to_try.append("http://transport-mcp:8000/mcp")

    for u in urls_to_try:
        status, ok = probe_http_endpoint(
            u,
            timeout_seconds=timeout,
            acceptable_status_codes=(200, 400, 405, 406),
        )
        if ok:
            return "● CONNECTED (:8000)", True

    return "○ OFFLINE", False


def check_workspace_health(workspace_path: str) -> tuple[str, bool]:
    """Verify local workspace existence and write containment permissions."""
    try:
        p = Path(workspace_path).resolve()
        if not p.exists():
            return "○ MISSING_DIRECTORY", False
        if not p.is_dir():
            return "○ NOT_A_DIRECTORY", False
        if not os.access(p, os.W_OK):
            return "○ READ_ONLY (NO_WRITE_PERMISSION)", False
        return "● READY (Writable)", True
    except Exception as e:
        return f"○ ERROR ({type(e).__name__})", False
