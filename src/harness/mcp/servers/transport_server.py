"""Transport MCP Server exposing the find_connection journey planning tool.

Scope & Provenance Note:
    This server exposes tools operating on the TransportService abstraction.
    By default, it uses the deterministic synthetic Bavarian rail timetable provider
    for reproducible agent evaluation and multi-agent coordination.
"""

import json

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

from harness.mcp.servers.transport_models import TransportDomainError
from harness.mcp.servers.transport_service import (
    TransportService,
    create_transport_service,
)

server = MCPServer("transport-server")
_transport_service: TransportService = create_transport_service()


def get_transport_service() -> TransportService:
    """Get the active TransportService instance."""
    return _transport_service


def set_transport_service(service: TransportService) -> None:
    """Configure or inject the active TransportService instance."""
    global _transport_service
    _transport_service = service


@server.tool()
def find_connection(
    origin: str,
    destination: str,
    departure_time: str,
    max_results: int = 3,
) -> str:
    """Find train connections between stations in the synthetic Bavarian regional network.

    Parameters:
        origin: Departure station name (e.g. 'Passau Hbf', 'München Hbf', 'Plattling').
        destination: Arrival station name (e.g. 'München Hbf', 'Deggendorf', 'Nürnberg Hbf').
        departure_time: Earliest departure time in ISO 8601 format (e.g. '2026-09-08T08:30:00').
        max_results: Maximum number of connections to return (1 to 5, default 3).
    """
    try:
        result = _transport_service.find_connections(
            origin=origin,
            destination=destination,
            departure_time=departure_time,
            max_results=max_results,
        )
    except TransportDomainError as e:
        raise ToolError(f"Invalid argument: [{e.code.value}] {e.message}") from e
    except Exception as e:
        raise ToolError(f"Internal error: {e}") from e

    return json.dumps(result, indent=2)


import argparse
import sys

from mcp.server.transport_security import TransportSecuritySettings


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Transport MCP Server")
    parser.add_argument(
        "--transport",
        choices=["stdio", "streamable-http"],
        default="stdio",
        help="Transport protocol (default: stdio)",
    )
    parser.add_argument(
        "--host",
        default="0.0.0.0",
        help="Host address to bind for streamable-http (default: 0.0.0.0)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=8000,
        help="Port to bind for streamable-http (default: 8000)",
    )
    parser.add_argument(
        "--path",
        default="/mcp",
        help="Streamable HTTP endpoint path (default: /mcp)",
    )
    args = parser.parse_args(argv)

    if args.transport == "stdio":
        server.run(transport="stdio")
    elif args.transport == "streamable-http":
        security = TransportSecuritySettings(
            enable_dns_rebinding_protection=True,
            allowed_hosts=[
                "transport-mcp:*",
                "transport-mcp",
                "localhost:*",
                "localhost",
                "127.0.0.1:*",
                "127.0.0.1",
                "[::1]:*",
                "[::1]",
                f"{args.host}:*",
                args.host,
            ],
            allowed_origins=[
                "http://transport-mcp:*",
                "http://transport-mcp",
                "http://localhost:*",
                "http://localhost",
                "http://127.0.0.1:*",
                "http://127.0.0.1",
            ],
        )
        server.run(
            transport="streamable-http",
            host=args.host,
            port=args.port,
            streamable_http_path=args.path,
            transport_security=security,
        )


if __name__ == "__main__":
    main()
