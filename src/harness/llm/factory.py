from collections.abc import Callable
from typing import Any

from harness.config import ConfigurationError, LLMConfig
from harness.llm.base import LLMProvider
from harness.llm.openai_compatible import OpenAICompatibleProvider
from harness.runtime.events import LifecycleEventBus

ProviderBuilder = Callable[[LLMConfig, LifecycleEventBus | None], LLMProvider]

PROVIDER_REGISTRY: dict[str, ProviderBuilder] = {}


def register_provider(name: str, builder: ProviderBuilder) -> None:
    """Register a provider builder for a named provider identifier."""
    PROVIDER_REGISTRY[name.strip().lower()] = builder


def _build_openai_compatible(
    config: LLMConfig,
    event_bus: LifecycleEventBus | None = None,
) -> LLMProvider:
    if not config.base_url or not config.base_url.strip():
        raise ConfigurationError("LLM_BASE_URL is required for openai_compatible provider.")
    if not config.model or not config.model.strip():
        raise ConfigurationError("LLM_MODEL is required for openai_compatible provider.")

    return OpenAICompatibleProvider(
        base_url=config.base_url,
        model=config.model,
        api_key=config.api_key,
        temperature=config.temperature,
        timeout=config.timeout,
        max_retries=config.max_retries,
        retry_backoff=config.retry_backoff,
        extra_headers=config.extra_headers,
        event_bus=event_bus,
    )


# Register built-in OpenAI-compatible provider aliases
for alias in (
    "openai_compatible",
    "openai",
    "openrouter",
    "ollama",
    "vllm",
    "lmstudio",
    "together",
    "groq",
):
    register_provider(alias, _build_openai_compatible)


def create_llm_provider(
    config: LLMConfig,
    event_bus: LifecycleEventBus | None = None,
) -> LLMProvider:
    """Factory function: instantiate the configured LLMProvider.

    Dispatches to registered provider builders (e.g. OpenAICompatibleProvider).
    """
    provider_name = (config.provider or "openai_compatible").strip().lower()
    builder = PROVIDER_REGISTRY.get(provider_name)
    if builder is None:
        supported = sorted(PROVIDER_REGISTRY.keys())
        raise ConfigurationError(
            f"Unsupported LLM provider '{config.provider}'. Supported providers: {supported}"
        )
    return builder(config, event_bus)
