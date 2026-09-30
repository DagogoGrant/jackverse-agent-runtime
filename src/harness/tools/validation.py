"""Minimal schema validator for ToolSpecs.

Engineering Scope Note:
    This validator deliberately implements only the minimal JSON-schema subset
    required by our tool contracts:
      - Object type verification
      - Required property presence
      - Primitive type checking ('string', 'integer', 'number', 'boolean')
      - Rejection of unexpected keys when additionalProperties is False

    It is intentionally NOT a general-purpose JSON Schema implementation
    (no $ref, oneOf, anyOf, regex patterns, or deep nested object resolution).
    This avoids pulling in heavyweight external dependencies while strictly
    enforcing our declared tool contracts.
"""

from collections.abc import Mapping
from typing import Any


class ToolContractValidator:
    """Validates tool invocation arguments against a ToolSpec's input_schema."""

    def validate(
        self,
        schema: Mapping[str, Any],
        arguments: Mapping[str, Any],
    ) -> tuple[bool, str | None]:
        """Validate arguments against schema.

        Returns:
            (is_valid, error_message): (True, None) if valid, or (False, message) if invalid.
        """
        if not isinstance(arguments, Mapping):
            return False, f"Expected arguments to be a dictionary/mapping, got {type(arguments).__name__}."

        schema_type = schema.get("type")
        if schema_type and schema_type != "object":
            return False, f"Unsupported root schema type '{schema_type}'; only 'object' is supported."

        properties = schema.get("properties", {})
        if not isinstance(properties, Mapping):
            properties = {}

        # 1. Verify required properties
        required_fields = schema.get("required", [])
        if isinstance(required_fields, (list, tuple)):
            for req_key in required_fields:
                if req_key not in arguments:
                    return False, f"Missing required argument '{req_key}'."

        # 2. Check additionalProperties rejection
        if schema.get("additionalProperties") is False:
            for arg_key in arguments:
                if arg_key not in properties:
                    return False, f"Unexpected argument '{arg_key}'. Tool does not allow additional properties."

        # 3. Check property primitive types
        for arg_key, arg_val in arguments.items():
            if arg_key in properties:
                prop_spec = properties[arg_key]
                if isinstance(prop_spec, Mapping):
                    expected_type = prop_spec.get("type")
                    err = self._check_primitive_type(arg_key, arg_val, expected_type)
                    if err is not None:
                        return False, err

        return True, None

    @staticmethod
    def _check_primitive_type(key: str, val: Any, expected_type: str | None) -> str | None:
        """Validate value against expected primitive type name."""
        if expected_type is None:
            return None

        if expected_type == "string":
            if not isinstance(val, str):
                return f"Argument '{key}' must be a string, got {type(val).__name__}."
        elif expected_type == "integer":
            if isinstance(val, bool) or not isinstance(val, int):
                return f"Argument '{key}' must be an integer, got {type(val).__name__}."
        elif expected_type == "number":
            if isinstance(val, bool) or not isinstance(val, (int, float)):
                return f"Argument '{key}' must be a number, got {type(val).__name__}."
        elif expected_type == "boolean":
            if not isinstance(val, bool):
                return f"Argument '{key}' must be a boolean, got {type(val).__name__}."
        elif expected_type == "array":
            if not isinstance(val, list):
                return f"Argument '{key}' must be an array/list, got {type(val).__name__}."
        elif expected_type == "object":
            if not isinstance(val, Mapping):
                return f"Argument '{key}' must be an object/dict, got {type(val).__name__}."
        return None
