from collections.abc import Mapping
from dataclasses import dataclass
from enum import Enum
from typing import Protocol


class ErrorCode(str, Enum):
    """Machine-readable taxonomy of tool failure conditions."""

    INVALID_ARGUMENT = "INVALID_ARGUMENT"      # Schema violation, missing/extra key, bad type
    NOT_FOUND = "NOT_FOUND"                    # Target file or directory missing
    AMBIGUOUS = "AMBIGUOUS"                    # Multiple occurrences during targeted replace
    CONFLICT = "CONFLICT"                      # File modified externally or stale state
    ALREADY_EXISTS = "ALREADY_EXISTS"          # File already exists and overwrite forbidden
    BOUNDARY_VIOLATION = "BOUNDARY_VIOLATION"  # Path escapes workspace root
    TRANSIENT_ERROR = "TRANSIENT_ERROR"        # Temporary OS/filesystem I/O failure
    INTERNAL_ERROR = "INTERNAL_ERROR"          # Unexpected failure or postcondition discrepancy
    PERMISSION_DENIED = "PERMISSION_DENIED"    # Tool action rejected by authorization policy or user confirmation


class ToolSource(str, Enum):
    """Origin of a tool capability."""

    BUILTIN = "builtin"
    MCP = "mcp"


@dataclass(frozen=True)
class ToolSpec:
    """Tool metadata exposed for discovery and LLM tool selection."""

    name: str
    description: str
    input_schema: dict[str, object]
    is_mutating: bool = False
    source: ToolSource = ToolSource.BUILTIN
    server_name: str | None = None


@dataclass(frozen=True)
class ToolResult:
    """Standard result/observation contract returned by tool executions."""

    content: str
    is_error: bool = False
    error_code: ErrorCode | None = None

    def __post_init__(self) -> None:
        if self.is_error and self.error_code is None:
            # Migration compromise: default unclassified legacy errors to INTERNAL_ERROR
            # so existing third-party or unmigrated tests continue to function.
            object.__setattr__(self, "error_code", ErrorCode.INTERNAL_ERROR)
        elif not self.is_error and self.error_code is not None:
            raise ValueError("error_code must be None when is_error is False.")


class Tool(Protocol):
    """Structural protocol defining a tool capability."""

    @property
    def spec(self) -> ToolSpec:
        ...

    def execute(
        self,
        arguments: Mapping[str, object],
    ) -> ToolResult:
        ...
