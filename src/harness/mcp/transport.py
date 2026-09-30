"""Transport abstraction layer for Model Context Protocol (MCP) clients.

Supports:
  - StdioTransport: local subprocess execution over stdin/stdout.
  - StreamableHttpTransport: remote and local Streamable HTTP endpoints (mcp==2.1.1).
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import AbstractAsyncContextManager, asynccontextmanager
import logging
import os
import sys
from typing import Any, Protocol, runtime_checkable
import urllib.parse

import httpx2
from mcp.client.stdio import StdioServerParameters, stdio_client
from mcp.client.streamable_http import StreamableHTTPError, streamable_http_client

from harness.config import MCPServerConfig

logger = logging.getLogger("harness.mcp.transport")

LOCAL_HOSTNAMES = frozenset({"localhost", "127.0.0.1", "::1"})


@runtime_checkable
class MCPTransport(Protocol):
    """Protocol defining the interface for MCP stream-based transports."""

    def connect(self) -> AbstractAsyncContextManager[tuple[Any, Any]]:
        """Return an async context manager yielding (read_stream, write_stream)."""
        ...


class StdioTransport:
    """stdio-based subprocess transport for local MCP servers."""

    def __init__(
        self,
        command: str,
        args: list[str] | None = None,
        env: dict[str, str] | None = None,
    ) -> None:
        if not command or not command.strip():
            raise ValueError("StdioTransport requires a non-empty command.")
        cmd = command.strip()
        if cmd in ("python", "python3") and sys.executable:
            cmd = sys.executable

        self.command = cmd
        self.args = list(args or [])
        self.env = dict(env or {})
        self._params = StdioServerParameters(
            command=self.command,
            args=self.args,
            env=self.env or None,
        )

    @property
    def params(self) -> StdioServerParameters:
        """Return underlying StdioServerParameters."""
        return self._params

    @asynccontextmanager
    async def connect(self) -> AsyncIterator[tuple[Any, Any]]:
        """Connect to the stdio subprocess, yielding (read_stream, write_stream)."""
        async with stdio_client(self._params) as streams:
            yield streams

    def __repr__(self) -> str:
        return f"StdioTransport(command='{self.command}', args={self.args})"


class StreamableHttpTransport:
    """Streamable HTTP transport connecting to remote or local MCP HTTP endpoints."""

    def __init__(
        self,
        url: str,
        auth_token_env: str | None = None,
        timeout_seconds: float = 30.0,
        http_client: httpx2.AsyncClient | None = None,
        _allow_insecure_host_for_test: bool = False,
        trusted_insecure_hosts: list[str] | None = None,
    ) -> None:
        if not url or not url.strip():
            raise ValueError("StreamableHttpTransport requires a non-empty url.")
        self.url = url.strip()
        self.auth_token_env = auth_token_env.strip() if auth_token_env else None
        self.trusted_insecure_hosts = list(trusted_insecure_hosts or [])

        try:
            self.timeout_seconds = float(timeout_seconds)
            if self.timeout_seconds <= 0.0:
                raise ValueError()
        except (ValueError, TypeError) as e:
            raise ValueError(f"timeout_seconds must be a positive number (> 0), got '{timeout_seconds}'.") from e

        # Validate URL scheme and host security
        parsed = urllib.parse.urlsplit(self.url)
        if parsed.scheme not in ("http", "https"):
            raise ValueError(f"Invalid URL scheme '{parsed.scheme}'. Must be 'http' or 'https'.")

        hostname = (parsed.hostname or "").lower()
        allowed_hosts = LOCAL_HOSTNAMES | frozenset(h.lower() for h in self.trusted_insecure_hosts)
        is_local = (hostname in allowed_hosts) or _allow_insecure_host_for_test
        if parsed.scheme == "http" and not is_local:
            raise ValueError(
                f"Remote Streamable HTTP endpoints must use HTTPS. Insecure HTTP is only permitted for localhost/127.0.0.1 or configured trusted_insecure_hosts, got '{self.url}'."
            )


        # Optional preconfigured client (used for in-process ASGI tests)
        self._custom_client = http_client

    @asynccontextmanager
    async def connect(self) -> AsyncIterator[tuple[Any, Any]]:
        """Connect to the Streamable HTTP server, yielding (read_stream, write_stream)."""
        if self._custom_client is not None:
            async with streamable_http_client(self.url, http_client=self._custom_client) as streams:
                yield streams
            return

        headers: dict[str, str] = {
            "Accept": "application/json, text/event-stream",
        }
        if self.auth_token_env:
            token = os.environ.get(self.auth_token_env)
            if token:
                headers["Authorization"] = f"Bearer {token}"

        timeout = httpx2.Timeout(
            self.timeout_seconds,
            connect=min(self.timeout_seconds, 5.0),
            read=self.timeout_seconds,
        )

        # Security constraints:
        # - verify=True (TLS verification strictly enforced)
        # - follow_redirects=False (no silent cross-origin redirect token leakage)
        async with httpx2.AsyncClient(
            headers=headers,
            timeout=timeout,
            follow_redirects=False,
            verify=True,
        ) as client:
            async with streamable_http_client(self.url, http_client=client) as streams:
                yield streams

    def __repr__(self) -> str:
        # Guarantee no secrets appear in repr: only environment variable name is shown
        return (
            f"StreamableHttpTransport(url='{self.url}', "
            f"auth_token_env={repr(self.auth_token_env)}, "
            f"timeout_seconds={self.timeout_seconds})"
        )


def create_transport(config: MCPServerConfig) -> MCPTransport:
    """Create appropriate MCPTransport based on server configuration."""
    transport_type = getattr(config, "transport", "stdio")
    if transport_type == "stdio":
        if not config.command:
            raise ValueError(f"MCP server '{config.name}' configured with stdio transport requires a non-empty 'command'.")
        return StdioTransport(
            command=config.command,
            args=config.args,
            env=config.env,
        )
    elif transport_type == "streamable_http":
        if not config.url:
            raise ValueError(f"MCP server '{config.name}' configured with streamable_http transport requires a non-empty 'url'.")
        return StreamableHttpTransport(
            url=config.url,
            auth_token_env=config.auth_token_env,
            timeout_seconds=config.timeout_seconds,
            trusted_insecure_hosts=getattr(config, "trusted_insecure_hosts", None),
        )

    else:
        raise ValueError(f"Unsupported MCP transport '{transport_type}' for server '{config.name}'.")


__all__ = [
    "MCPTransport",
    "StdioTransport",
    "StreamableHttpTransport",
    "StreamableHTTPError",
    "create_transport",
]
