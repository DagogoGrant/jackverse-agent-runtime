from harness.tools.base import Tool, ToolSpec


class DuplicateToolError(Exception):
    """Raised when registering a tool with a name that already exists."""


class UnknownToolError(Exception):
    """Raised when requesting a tool that has not been registered."""


class ToolRegistry:
    """Registry maintaining available tools and exposing their specifications."""

    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}

    def register(self, tool: Tool) -> None:
        """Register a new tool. Raises DuplicateToolError if name already exists."""
        name = tool.spec.name
        if name in self._tools:
            raise DuplicateToolError(f"Tool with name '{name}' is already registered.")
        self._tools[name] = tool

    def get(self, name: str) -> Tool:
        """Retrieve a tool by name. Raises UnknownToolError if not found."""
        if name not in self._tools:
            raise UnknownToolError(f"Tool '{name}' is not registered.")
        return self._tools[name]

    def list_specs(self) -> list[ToolSpec]:
        """Return the specifications of all registered tools in registration order."""
        return [tool.spec for tool in self._tools.values()]

    def list_tools(self) -> list[Tool]:
        """Return all registered tools in registration order."""
        return list(self._tools.values())

    def get_all(self) -> list[Tool]:
        """Return all registered tools in registration order."""
        return list(self._tools.values())
