"""Unit tests for ToolContractValidator and ToolExecutor boundary (Hypothesis H2)."""

import unittest

from harness.tools.base import ErrorCode, ToolResult, ToolSpec
from harness.tools.executor import ToolExecutor
from harness.tools.validation import ToolContractValidator


class SampleContractTool:
    def __init__(self, additional_properties: bool = False) -> None:
        self._spec = ToolSpec(
            name="sample_tool",
            description="Tool with defined contract schema.",
            input_schema={
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "count": {"type": "integer"},
                    "flag": {"type": "boolean"},
                },
                "required": ["path"],
                "additionalProperties": additional_properties,
            },
        )

    @property
    def spec(self) -> ToolSpec:
        return self._spec

    def execute(self, arguments: dict[str, object]) -> ToolResult:
        return ToolResult(content=f"Executed with path={arguments['path']}", is_error=False)


class ExplodingTool:
    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="exploding_tool",
            description="Tool that raises an unexpected internal exception.",
            input_schema={"type": "object", "properties": {}, "additionalProperties": False},
        )

    def execute(self, arguments: dict[str, object]) -> ToolResult:
        raise ZeroDivisionError("Simulated internal catastrophic divide by zero")


class TestToolValidation(unittest.TestCase):
    def setUp(self) -> None:
        self.validator = ToolContractValidator()
        self.executor = ToolExecutor(validator=self.validator)

    def test_missing_required_argument_rejected(self) -> None:
        tool = SampleContractTool()
        result = self.executor.execute(tool, {"count": 10})
        self.assertTrue(result.is_error)
        self.assertEqual(result.error_code, ErrorCode.INVALID_ARGUMENT)
        self.assertIn("Missing required argument 'path'", result.content)

    def test_wrong_primitive_type_rejected(self) -> None:
        tool = SampleContractTool()
        # path must be string, pass integer
        result = self.executor.execute(tool, {"path": 12345})
        self.assertTrue(result.is_error)
        self.assertEqual(result.error_code, ErrorCode.INVALID_ARGUMENT)
        self.assertIn("Argument 'path' must be a string", result.content)

        # count must be integer, pass string
        result_count = self.executor.execute(tool, {"path": "file.txt", "count": "not_an_int"})
        self.assertTrue(result_count.is_error)
        self.assertEqual(result_count.error_code, ErrorCode.INVALID_ARGUMENT)
        self.assertIn("Argument 'count' must be an integer", result_count.content)

        # count cannot be boolean
        result_bool = self.executor.execute(tool, {"path": "file.txt", "count": True})
        self.assertTrue(result_bool.is_error)
        self.assertEqual(result_bool.error_code, ErrorCode.INVALID_ARGUMENT)
        self.assertIn("Argument 'count' must be an integer", result_bool.content)

    def test_unexpected_argument_rejected_when_additional_properties_false(self) -> None:
        tool = SampleContractTool(additional_properties=False)
        result = self.executor.execute(tool, {"path": "valid.txt", "unexpected_key": "rogue"})
        self.assertTrue(result.is_error)
        self.assertEqual(result.error_code, ErrorCode.INVALID_ARGUMENT)
        self.assertIn("Unexpected argument 'unexpected_key'", result.content)

    def test_unexpected_argument_permitted_when_additional_properties_true(self) -> None:
        tool = SampleContractTool(additional_properties=True)
        result = self.executor.execute(tool, {"path": "valid.txt", "extra_param": "allowed"})
        self.assertFalse(result.is_error)
        self.assertIn("Executed with path=valid.txt", result.content)

    def test_valid_payload_executes_successfully(self) -> None:
        tool = SampleContractTool()
        result = self.executor.execute(tool, {"path": "doc.md", "count": 3, "flag": False})
        self.assertFalse(result.is_error)
        self.assertIsNone(result.error_code)
        self.assertEqual(result.content, "Executed with path=doc.md")

    def test_executor_traps_unhandled_exception_safely(self) -> None:
        tool = ExplodingTool()
        result = self.executor.execute(tool, {})
        self.assertTrue(result.is_error)
        self.assertEqual(result.error_code, ErrorCode.INTERNAL_ERROR)
        # Verify internal raw exception traceback is NOT leaked to content
        self.assertEqual(result.content, "An internal tool execution error occurred.")
        self.assertNotIn("ZeroDivisionError", result.content)


if __name__ == "__main__":
    unittest.main()
