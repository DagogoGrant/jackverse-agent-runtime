"""MCP Client implementation for connecting to external MCP servers over stdio.

Lifecycle Architecture Note:
    This client implements a deliberately simple baseline lifecycle using
    per-operation asyncio.run() calls.

    Protocol & SDK Evolution Note:
      - The current MCP specification (2026-07-28+) and the Python SDK v2
        support newer stateless communication and first-class Client abstractions
        that remove the mandatory session/handshake model.
      - This baseline intentionally retains the empirically verified
        ClientSession + stdio compatibility path as the smallest, most robust
        bridge for our synchronous agent harness.
      - We do not claim this lifecycle is the optimal modern MCP lifecycle.
      - A systematic lifecycle/API comparison (e.g. persistent sessions or
        newer client abstractions) may be evaluated in later phases if latency
        or statefulness requirements justify the added architectural complexity.

    Tradeoffs and Rationale:
      - It strictly avoids background threads, worker loops, and daemon
        lifecycle complexity.
      - Each MCP operation (discovery, tool invocation) creates a fresh stdio
        process, establishes an MCP session handshake, performs the request,
        and cleanly shuts down the subprocess upon exit.
      - Therefore, it incurs additional process startup and handshake latency
        overhead per invocation (~1.4s per operation).
      - We deliberately accept this startup overhead for the minimum reproducible
        baseline to prioritize deterministic containment, zero thread leaks, and
        verifiable correctness before persistent connections are empirically justified.
"""

import asyncio
from collections.abc import Mapping
import logging
import os
import sys
from typing import Any

import httpx2
from mcp.client import Client
from mcp.client.streamable_http import StreamableHTTPError

from harness.config import MCPServerConfig
from harness.mcp.resilience import (
    CircuitBreaker,
    CircuitSnapshot,
    MCPResilienceConfig,
    ResilientMCPInvoker,
)
from harness.mcp.transport import MCPTransport, create_transport
from harness.runtime.events import LifecycleEventBus
from harness.tools.base import ErrorCode, ToolResult, ToolSpec

logger = logging.getLogger("harness.mcp.client")


class MCPClient:
    """Client for communicating with an external Model Context Protocol (MCP) server."""

    def __init__(
        self,
        config: MCPServerConfig,
        transport: MCPTransport | None = None,
        event_bus: LifecycleEventBus | None = None,
        resilience_config: MCPResilienceConfig | None = None,
    ) -> None:
        self._config = config
        self._transport = transport or create_transport(config)
        self._closed = False
        self._event_bus = event_bus
        self._resilience_config = (
            resilience_config
            or getattr(config, "resilience", None)
            or MCPResilienceConfig()
        )
        self._circuit_breaker = CircuitBreaker(
            server_name=config.name,
            config=self._resilience_config,
            event_bus=self._event_bus,
        )
        self._invoker = ResilientMCPInvoker(
            circuit_breaker=self._circuit_breaker,
            config=self._resilience_config,
            event_bus=self._event_bus,
        )

    @property
    def server_name(self) -> str:
        return self._config.name

    @property
    def config(self) -> MCPServerConfig:
        return self._config

    @property
    def is_closed(self) -> bool:
        return self._closed

    @property
    def transport(self) -> MCPTransport:
        return self._transport

    @property
    def circuit_breaker(self) -> CircuitBreaker:
        return self._circuit_breaker

    @property
    def invoker(self) -> ResilientMCPInvoker:
        return self._invoker

    def get_circuit_snapshot(self) -> CircuitSnapshot:
        """Return an immutable read-only snapshot of the client's circuit breaker state."""
        return self._circuit_breaker.snapshot()

    def _sanitize_message(self, message: str) -> str:
        """Ensure auth secrets never appear in error messages or logs."""
        clean = message
        if self._config.auth_token_env:
            secret = os.environ.get(self._config.auth_token_env)
            if secret and secret in clean:
                clean = clean.replace(secret, "[REDACTED]")
        return clean

    def _classify_tool_mutating(self, name: str, description: str = "") -> bool:
        """Classify whether a discovered MCP tool is mutating.

        Conservative Principle:
            Unknown tools are treated as mutating (not safely retryable after ambiguous
            in-flight failures) unless explicitly configured as idempotent or matching
            unambiguous read-only semantics.
        """
        # 1. Explicitly declared idempotent tools in configuration are non-mutating
        if name in self._resilience_config.idempotent_tools:
            return False

        # 2. Known read-only / query verbs
        lower_name = name.lower()
        read_only_prefixes = (
            "read_",
            "get_",
            "list_",
            "search_",
            "find_",
            "fetch_",
            "query_",
            "describe_",
            "check_",
            "inspect_",
            "view_",
        )
        read_only_exact = {"echo", "ping", "status", "version", "info"}
        if lower_name in read_only_exact or any(lower_name.startswith(p) for p in read_only_prefixes):
            return False

        # 3. Everything else (mutating verbs or unknown tools) is conservatively treated as mutating
        return True

    def list_tools(self) -> list[ToolSpec]:
        """Discover tools exposed by the MCP server and convert to ToolSpecs.

        Raises:
            RuntimeError: If connection or discovery fails.
        """
        if self._closed:
            raise RuntimeError(f"Cannot list tools on closed MCPClient for server '{self._config.name}'.")

        async def _async_list() -> list[ToolSpec]:
            async with Client(self._transport.connect(), mode="auto") as client:
                response = await client.list_tools()
                specs: list[ToolSpec] = []
                for tool in response.tools:
                    schema = (
                        tool.input_schema
                        if isinstance(tool.input_schema, dict)
                        else {"type": "object", "properties": {}}
                    )
                    is_mutating = self._classify_tool_mutating(tool.name, tool.description or "")
                    specs.append(
                        ToolSpec(
                            name=tool.name,
                            description=tool.description or "",
                            input_schema=schema,
                            is_mutating=is_mutating,
                        )
                    )
                return specs

        try:
            return asyncio.run(_async_list())
        except Exception as e:
            primary_e = self._extract_primary_exception(e)
            clean_err = self._sanitize_message(str(primary_e))
            logger.exception(f"Tool discovery failed for MCP server '{self._config.name}': {clean_err}")
            raise RuntimeError(f"Failed to discover tools from MCP server '{self._config.name}': {clean_err}") from e

    @staticmethod
    def _extract_primary_exception(exc: BaseException) -> BaseException:
        """Extract the root cause exception if wrapped inside an ExceptionGroup."""
        candidate_types = (
            OSError,
            ProcessLookupError,
            TimeoutError,
            ConnectionError,
            FileNotFoundError,
            httpx2.HTTPError,
            StreamableHTTPError,
        )
        if isinstance(exc, BaseExceptionGroup):
            for sub in exc.exceptions:
                unwrapped = MCPClient._extract_primary_exception(sub)
                if isinstance(unwrapped, candidate_types):
                    return unwrapped
            if exc.exceptions:
                return MCPClient._extract_primary_exception(exc.exceptions[0])
        return exc

    def call_tool(
        self,
        name: str,
        arguments: Mapping[str, object],
        is_mutating: bool | None = None,
        is_idempotent: bool | None = None,
    ) -> ToolResult:
        """Invoke a tool on the MCP server and convert result to ToolResult.

        Executes via ResilientMCPInvoker to provide idempotency-aware retry and circuit breaker protection.
        """
        effective_mutating = (
            is_mutating
            if is_mutating is not None
            else self._classify_tool_mutating(name)
        )
        return self._invoker.invoke(
            tool_name=name,
            arguments=arguments,
            call_fn=lambda: self._raw_call_tool(name, arguments),
            is_mutating=effective_mutating,
            is_idempotent=is_idempotent,
        )

    def _raw_call_tool(self, name: str, arguments: Mapping[str, object]) -> ToolResult:
        """Low-level single tool invocation against the MCP transport."""
        if self._closed:
            return ToolResult(
                content=f"Cannot invoke tool '{name}' on closed MCPClient for server '{self._config.name}'.",
                is_error=True,
                error_code=ErrorCode.INTERNAL_ERROR,
            )

        dict_args: dict[str, Any] = dict(arguments)

        async def _async_call() -> Any:
            async with Client(self._transport.connect(), mode="auto") as client:
                return await client.call_tool(name, dict_args)

        try:
            mcp_result = asyncio.run(_async_call())
        except Exception as raw_e:
            e = self._extract_primary_exception(raw_e)
            clean_err = self._sanitize_message(str(e))

            if isinstance(
                e,
                (
                    OSError,
                    ProcessLookupError,
                    TimeoutError,
                    ConnectionError,
                    FileNotFoundError,
                    httpx2.HTTPError,
                    StreamableHTTPError,
                ),
            ):
                logger.warning(f"MCP transport/communication error with server '{self._config.name}': {clean_err}")

                if isinstance(e, httpx2.HTTPStatusError) and e.response.status_code in (401, 403):
                    err_code = ErrorCode.INTERNAL_ERROR
                else:
                    err_code = ErrorCode.TRANSIENT_ERROR

                res = ToolResult(
                    content=f"MCP transport error communicating with server '{self._config.name}': {clean_err}",
                    is_error=True,
                    error_code=err_code,
                )
                is_pre_exec = isinstance(e, (ConnectionError, ProcessLookupError, FileNotFoundError))
                if isinstance(e, httpx2.ConnectError):
                    is_pre_exec = True
                if is_pre_exec:
                    object.__setattr__(res, "_is_pre_execution", True)
                return res

            logger.exception(f"Unexpected error during MCP tool execution '{name}' on '{self._config.name}': {clean_err}")
            return ToolResult(
                content=f"MCP client unexpected failure: {clean_err}",
                is_error=True,
                error_code=ErrorCode.INTERNAL_ERROR,
            )

        # Extract content text from MCP result
        text_chunks: list[str] = []
        raw_content = getattr(mcp_result, "content", None)
        if raw_content and isinstance(raw_content, list):
            for block in raw_content:
                if hasattr(block, "text") and block.text:
                    text_chunks.append(str(block.text))
                elif isinstance(block, dict) and "text" in block:
                    text_chunks.append(str(block["text"]))
                else:
                    text_chunks.append(str(block))

        content_str = "\n".join(text_chunks) if text_chunks else ""

        is_error = getattr(mcp_result, "is_error", False)
        if is_error:
            err_content = content_str or f"MCP tool '{name}' reported an error on server '{self._config.name}'."

            # Semantic Error Classification Baseline Note:
            # MCPClient currently infers ErrorCode.INVALID_ARGUMENT from anticipated
            # error-message substrings. This is a deliberately limited baseline mechanism:
            #   - It depends on stable, predictable error text from external servers.
            #   - It is not a fully structured, cross-server error taxonomy.
            #   - Systematic error schema negotiation and cross-server classification
            #     are intentionally deferred to the Phase 4 MCP Reliability investigation.
            err_lower = err_content.lower()
            if any(k in err_lower for k in ("invalid argument", "unknown station", "cannot be identical", "between 1 and 5", "schema")):
                err_code = ErrorCode.INVALID_ARGUMENT
            else:
                err_code = ErrorCode.INTERNAL_ERROR

            return ToolResult(
                content=err_content,
                is_error=True,
                error_code=err_code,
            )

        return ToolResult(
            content=content_str,
            is_error=False,
            error_code=None,
        )

    def close(self) -> None:
        """Mark the client as closed."""
        self._closed = True

    def __enter__(self) -> "MCPClient":
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        self.close()
