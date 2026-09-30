"""JackVerse LLM provider abstraction, factory, diagnostics, and implementations."""

from harness.llm.base import (
    LLMAuthenticationError,
    LLMConnectionError,
    LLMError,
    LLMProvider,
    LLMResponse,
    ProviderCapabilityError,
    ToolCall,
)
from harness.llm.client import LLMClient
from harness.llm.doctor import DiagnosticCheck, DiagnosticReport, run_llm_diagnostics
from harness.llm.factory import (
    PROVIDER_REGISTRY,
    create_llm_provider,
    register_provider,
)
from harness.llm.openai_compatible import (
    OpenAICompatibleProvider,
    tool_spec_to_openai,
)

__all__ = [
    "DiagnosticCheck",
    "DiagnosticReport",
    "LLMAuthenticationError",
    "LLMClient",
    "LLMConnectionError",
    "LLMError",
    "LLMProvider",
    "LLMResponse",
    "OpenAICompatibleProvider",
    "PROVIDER_REGISTRY",
    "ProviderCapabilityError",
    "ToolCall",
    "create_llm_provider",
    "register_provider",
    "run_llm_diagnostics",
    "tool_spec_to_openai",
]
