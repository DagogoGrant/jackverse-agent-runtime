from collections.abc import Callable, Mapping, Sequence
import time
from typing import Any

from harness.llm.base import (
    LLMAuthenticationError,
    LLMConnectionError,
    LLMError,
    LLMProvider,
    LLMResponse,
    ProviderCapabilityError,
    ToolCall,
)
from harness.llm.openai_compatible import (
    OpenAICompatibleProvider,
    TRANSIENT_LLM_ERRORS,
    _clean_json_string,
    tool_spec_to_openai,
)
from harness.runtime.events import LifecycleEventBus
from harness.tools.base import ToolSpec


class LLMClient(OpenAICompatibleProvider):
    """Backward-compatible client implementing LLMProvider via OpenAICompatibleProvider."""

    def __init__(
        self,
        api_key: str | None = None,
        base_url: str = "https://api.openai.com/v1",
        model: str = "gpt-4.1-mini",
        temperature: float = 0.0,
        timeout: float = 30.0,
        max_retries: int = 2,
        retry_backoff: float = 0.5,
        extra_headers: Mapping[str, str] | None = None,
        sleep_fn: Callable[[float], None] = time.sleep,
        event_bus: LifecycleEventBus | None = None,
    ) -> None:
        super().__init__(
            base_url=base_url,
            model=model,
            api_key=api_key,
            temperature=temperature,
            timeout=timeout,
            max_retries=max_retries,
            retry_backoff=retry_backoff,
            extra_headers=extra_headers,
            sleep_fn=sleep_fn,
            event_bus=event_bus,
        )

