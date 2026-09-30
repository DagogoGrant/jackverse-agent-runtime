"""Reference reproducible MCP server over stdio for testing and baseline integration."""

from mcp.server.mcpserver import MCPServer

server = MCPServer("reference-mcp-server")


@server.tool()
def echo(message: str) -> str:
    """Echo the provided message."""
    return f"echo: {message}"


@server.tool()
def add(a: int, b: int) -> int:
    """Add two integers."""
    return a + b


@server.tool()
def fail_tool(message: str) -> str:
    """Deliberately fail with an exception to test error handling."""
    raise ValueError(f"deliberate failure: {message}")


@server.tool()
def large_payload(count: int = 20000) -> str:
    """Generate a large payload to test observation bounding."""
    return "X" * count


def main() -> None:
    server.run(transport="stdio")


if __name__ == "__main__":
    main()
