from collections.abc import Mapping
import unittest

from harness.tools.base import Tool, ToolResult, ToolSpec
from harness.tools.executor import ToolExecutor
from harness.tools.registry import DuplicateToolError, ToolRegistry, UnknownToolError


class EchoTool:
    """Test tool that echoes back provided text."""

    def __init__(self, name: str = "echo", description: str = "Echoes input text") -> None:
        self._spec = ToolSpec(
            name=name,
            description=description,
            input_schema={
                "type": "object",
                "properties": {
                    "text": {"type": "string", "description": "Text to echo"}
                },
                "required": ["text"],
            },
        )

    @property
    def spec(self) -> ToolSpec:
        return self._spec

    def execute(self, arguments: Mapping[str, object]) -> ToolResult:
        text = str(arguments.get("text", ""))
        return ToolResult(content=text, is_error=False)


class ErrorEchoTool:
    """Test tool returning a failed ToolResult."""

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="error_tool",
            description="Returns error",
            input_schema={},
        )

    def execute(self, arguments: Mapping[str, object]) -> ToolResult:
        return ToolResult(content="An intentional failure", is_error=True)


class TestToolRegistryAndExecutor(unittest.TestCase):
    def setUp(self) -> None:
        self.registry = ToolRegistry()
        self.executor = ToolExecutor()

    def test_successful_registration_and_lookup(self) -> None:
        tool = EchoTool()
        self.registry.register(tool)

        retrieved = self.registry.get("echo")
        self.assertIs(retrieved, tool)
        self.assertEqual(retrieved.spec.name, "echo")

    def test_duplicate_registration_raises_error(self) -> None:
        tool1 = EchoTool(name="duplicate_name")
        tool2 = EchoTool(name="duplicate_name")

        self.registry.register(tool1)
        with self.assertRaises(DuplicateToolError) as ctx:
            self.registry.register(tool2)
        self.assertIn("duplicate_name", str(ctx.exception))

    def test_unknown_lookup_raises_error(self) -> None:
        with self.assertRaises(UnknownToolError) as ctx:
            self.registry.get("non_existent_tool")
        self.assertIn("non_existent_tool", str(ctx.exception))

    def test_list_specs_and_order_preservation(self) -> None:
        tool_a = EchoTool(name="tool_a", description="First tool")
        tool_b = EchoTool(name="tool_b", description="Second tool")
        tool_c = EchoTool(name="tool_c", description="Third tool")

        self.registry.register(tool_a)
        self.registry.register(tool_b)
        self.registry.register(tool_c)

        specs = self.registry.list_specs()
        self.assertEqual(len(specs), 3)
        self.assertEqual(specs[0].name, "tool_a")
        self.assertEqual(specs[1].name, "tool_b")
        self.assertEqual(specs[2].name, "tool_c")
        self.assertEqual(specs[0].description, "First tool")

    def test_executor_invokes_tool_and_returns_tool_result(self) -> None:
        tool = EchoTool()
        result = self.executor.execute(tool, {"text": "hello world"})

        self.assertIsInstance(result, ToolResult)
        self.assertEqual(result.content, "hello world")
        self.assertFalse(result.is_error)

    def test_executor_returns_error_tool_result(self) -> None:
        tool = ErrorEchoTool()
        result = self.executor.execute(tool, {})

        self.assertIsInstance(result, ToolResult)
        self.assertEqual(result.content, "An intentional failure")
        self.assertTrue(result.is_error)


if __name__ == "__main__":
    unittest.main()
