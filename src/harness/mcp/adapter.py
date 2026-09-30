"""Adapter integrating external MCP tools into the harness Tool protocol."""

from collections.abc import Mapping
from dataclasses import replace

from harness.mcp.client import MCPClient
from harness.tools.base import ToolResult, ToolSource, ToolSpec


class MCPToolAdapter:
    """Adapts a dynamically discovered MCP tool into the harness Tool protocol.

    This ensures external MCP tools are registered directly into ToolRegistry and
    invoked strictly through ToolExecutor, thereby inheriting all runtime controls:
    schema validation, execution budgets, error containment, and observation ceilings.
    """

    def __init__(
        self,
        spec: ToolSpec,
        client: MCPClient,
        name_override: str | None = None,
        prefix: str | None = None,
    ) -> None:
        self._remote_name = spec.name
        self._client = client

        effective_name = (
            name_override
            if name_override is not None
            else (f"{prefix}_{spec.name}" if prefix else spec.name)
        )
        self._spec = replace(
            spec,
            name=effective_name,
            source=ToolSource.MCP,
            server_name=client.server_name,
        )

    @property
    def remote_name(self) -> str:
        """Original remote tool name as exposed by the MCP server."""
        return self._remote_name

    @property
    def spec(self) -> ToolSpec:
        """Expose the tool specification for registry and LLM visibility."""
        return self._spec

    @property
    def client(self) -> MCPClient:
        """Underlying MCP client."""
        return self._client

    def execute(self, arguments: Mapping[str, object]) -> ToolResult:
        """Execute the MCP tool via the underlying client using the remote name."""
        return self._client.call_tool(
            self._remote_name,
            arguments,
            is_mutating=self._spec.is_mutating,
        )
