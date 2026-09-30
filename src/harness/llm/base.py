from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from harness.tools.base import ToolSpec


class LLMError(RuntimeError):
    """Base error for LLM provider operations."""


class ProviderCapabilityError(LLMError):
    """Raised when a configured model or provider lacks required capabilities (e.g. structured tool calls)."""


class LLMAuthenticationError(LLMError):
    """Raised when LLM API authentication fails."""


class LLMConnectionError(LLMError):
    """Raised when connection to the LLM API endpoint fails."""


@dataclass(frozen=True)
class ToolCall:
    """Neutral representation of a structured tool invocation requested by the LLM."""

    id: str
    name: str
    arguments: dict[str, object]


@dataclass(frozen=True)
class LLMResponse:
    """Neutral container for LLM output, containing text content, tool calls, and optional token usage."""

    content: str | None
    tool_calls: list[ToolCall] = field(default_factory=list)
    usage: dict[str, int] | None = None


@runtime_checkable
class LLMProvider(Protocol):
    """Contract for LLM inference providers."""

    @property
    def model(self) -> str:
        """The configured model identifier."""
        ...

    def chat(
        self,
        messages: Sequence[dict[str, Any]],
        tools: Sequence[ToolSpec] | None = None,
    ) -> LLMResponse:
        """Send chat messages to the provider and return a structured LLMResponse."""
        ...
