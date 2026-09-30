from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
import json
import time
from typing import Any

from openai import (
    APIConnectionError,
    APITimeoutError,
    InternalServerError,
    OpenAI,
    RateLimitError,
)
import uuid

from harness.runtime.context import get_current_context
from harness.runtime.events import (
    FailureCategory,
    LifecycleEventBus,
    LLMCallFinishedEvent,
    LLMCallStartedEvent,
    LLMCallStatus,
)
from harness.tools.base import ToolSpec

TRANSIENT_LLM_ERRORS = (
    APITimeoutError,
    APIConnectionError,
    RateLimitError,
    InternalServerError,
)


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


def tool_spec_to_openai(spec: ToolSpec) -> dict[str, object]:
    """Convert an internal ToolSpec to the OpenAI function tool wire format."""
    return {
        "type": "function",
        "function": {
            "name": spec.name,
            "description": spec.description,
            "parameters": spec.input_schema,
        },
    }


def _clean_json_string(raw_args: str) -> str:
    """Strip markdown formatting fences from JSON strings if present."""
    cleaned = raw_args.strip()
    if cleaned.startswith("```"):
        lines = cleaned.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        cleaned = "\n".join(lines).strip()
    return cleaned


class LLMClient:
    """Client for interacting with the InnKube LLM service via the OpenAI SDK."""

    def __init__(
        self,
        api_key: str,
        base_url: str,
        model: str,
        temperature: float = 0.0,
        timeout: float = 30.0,
        max_retries: int = 2,
        retry_backoff: float = 0.5,
        sleep_fn: Callable[[float], None] = time.sleep,
        event_bus: LifecycleEventBus | None = None,
    ) -> None:
        self.model = model
        self.temperature = temperature
        self.timeout = timeout
        self.max_retries = max_retries
        self.retry_backoff = retry_backoff
        self.sleep_fn = sleep_fn
        self.event_bus = event_bus

        # Initialize OpenAI with max_retries=0 so the harness owns the transparent retry loop
        self.client = OpenAI(
            api_key=api_key,
            base_url=base_url,
            timeout=timeout,
            max_retries=0,
        )

    def chat(
        self,
        messages: Sequence[dict[str, Any]],
        tools: Sequence[ToolSpec] | None = None,
    ) -> LLMResponse:
        """Send chat messages to the LLM with bounded retries and return a neutral LLMResponse."""
        ctx = get_current_context()
        llm_call_id = uuid.uuid4().hex
        if self.event_bus and ctx:
            self.event_bus.publish(
                LLMCallStartedEvent(
                    timestamp=time.time(),
                    trace_id=ctx.trace_id,
                    run_id=ctx.run_id,
                    root_run_id=ctx.root_run_id,
                    parent_run_id=ctx.parent_run_id,
                    agent_id=ctx.agent_id,
                    agent_role=ctx.agent_role,
                    llm_call_id=llm_call_id,
                    model=self.model,
                    temperature=self.temperature,
                    message_count=len(messages),
                    tools_count=len(tools) if tools else 0,
                )
            )

        start_time = time.perf_counter()
        status = LLMCallStatus.ERROR
        attempts = 0
        prompt_tokens: int | None = None
        completion_tokens: int | None = None
        total_tokens: int | None = None
        tool_calls_count = 0
        failure_category: FailureCategory | None = None
        error_type: str | None = None
        error_code: str | None = None
        error_message: str | None = None

        try:
            kwargs: dict[str, Any] = {
                "model": self.model,
                "messages": list(messages),
                "temperature": self.temperature,
                "timeout": self.timeout,
            }

            if tools:
                kwargs["tools"] = [tool_spec_to_openai(t) for t in tools]

            max_attempts = self.max_retries + 1

            while True:
                attempts += 1
                try:
                    response = self.client.chat.completions.create(**kwargs)
                    break
                except TRANSIENT_LLM_ERRORS as e:
                    if attempts >= max_attempts:
                        raise RuntimeError(
                            f"LLM request failed after {attempts} attempts (max {self.max_retries} retries): {e}"
                        ) from e

                    delay = self.retry_backoff * (2 ** (attempts - 1))
                    if self.sleep_fn is not None:
                        self.sleep_fn(delay)

            choice = response.choices[0]
            message = choice.message

            parsed_tool_calls: list[ToolCall] = []
            if message.tool_calls:
                for raw_call in message.tool_calls:
                    call_id = getattr(raw_call, "id", None)
                    if not isinstance(call_id, str) or not call_id.strip():
                        raise ValueError("Malformed tool call from LLM: missing or empty tool call 'id'.")
                    call_id = call_id.strip()

                    func = getattr(raw_call, "function", None)
                    if func is None:
                        raise ValueError(f"Malformed tool call (ID: {call_id}): missing 'function' definition.")

                    func_name = getattr(func, "name", None)
                    if not isinstance(func_name, str) or not func_name.strip():
                        raise ValueError(f"Malformed tool call (ID: {call_id}): missing or empty function 'name'.")
                    func_name = func_name.strip()

                    raw_args = getattr(func, "arguments", None)
                    if not isinstance(raw_args, str) or not raw_args.strip():
                        raise ValueError(
                            f"Malformed tool call '{func_name}' (ID: {call_id}): 'arguments' field is missing or empty."
                        )

                    cleaned_args = _clean_json_string(raw_args)
                    try:
                        parsed_args = json.loads(cleaned_args)
                    except (json.JSONDecodeError, TypeError) as e:
                        raise ValueError(
                            f"Failed to parse arguments JSON for tool call '{func_name}' (ID: {call_id}): {e}"
                        ) from e

                    if not isinstance(parsed_args, dict):
                        raise ValueError(
                            f"Arguments for tool call '{func_name}' (ID: {call_id}) must decode to a JSON object, got {type(parsed_args).__name__}."
                        )

                    parsed_tool_calls.append(
                        ToolCall(
                            id=call_id,
                            name=func_name,
                            arguments=parsed_args,
                        )
                    )

            if message.content is None and not parsed_tool_calls:
                raise RuntimeError("Received empty response (no content and no tool calls) from LLM.")

            usage_dict: dict[str, int] | None = None
            if getattr(response, "usage", None) is not None:
                prompt_tokens = getattr(response.usage, "prompt_tokens", 0)
                completion_tokens = getattr(response.usage, "completion_tokens", 0)
                total_tokens = getattr(response.usage, "total_tokens", 0)
                usage_dict = {
                    "prompt_tokens": prompt_tokens,
                    "completion_tokens": completion_tokens,
                    "total_tokens": total_tokens,
                }

            status = LLMCallStatus.SUCCESS
            tool_calls_count = len(parsed_tool_calls)

            return LLMResponse(
                content=message.content,
                tool_calls=parsed_tool_calls,
                usage=usage_dict,
            )
        except Exception as exc:
            status = LLMCallStatus.ERROR
            root_exc = getattr(exc, "__cause__", None) or exc
            error_type = type(root_exc).__name__
            error_message = str(exc)[:200]
            if isinstance(root_exc, APITimeoutError) or isinstance(exc, APITimeoutError):
                failure_category = FailureCategory.TIMEOUT
            elif isinstance(root_exc, (ValueError, json.JSONDecodeError)) or isinstance(exc, (ValueError, json.JSONDecodeError)):
                failure_category = FailureCategory.VALIDATION
            elif isinstance(root_exc, (APIConnectionError, RateLimitError, InternalServerError, RuntimeError)) or isinstance(exc, (APIConnectionError, RateLimitError, InternalServerError, RuntimeError)):
                failure_category = FailureCategory.PROVIDER
            else:
                failure_category = FailureCategory.INTERNAL
            raw_code = getattr(root_exc, "code", None) or getattr(root_exc, "status_code", None) or getattr(exc, "code", None) or getattr(exc, "status_code", None)
            error_code = str(raw_code) if raw_code is not None else None
            raise
        finally:
            if self.event_bus and ctx:
                duration = time.perf_counter() - start_time
                self.event_bus.publish(
                    LLMCallFinishedEvent(
                        timestamp=time.time(),
                        trace_id=ctx.trace_id,
                        run_id=ctx.run_id,
                        root_run_id=ctx.root_run_id,
                        parent_run_id=ctx.parent_run_id,
                        agent_id=ctx.agent_id,
                        agent_role=ctx.agent_role,
                        llm_call_id=llm_call_id,
                        model=self.model,
                        duration_seconds=duration,
                        status=status,
                        attempts=attempts,
                        prompt_tokens=prompt_tokens,
                        completion_tokens=completion_tokens,
                        total_tokens=total_tokens,
                        tool_calls_count=tool_calls_count,
                        failure_category=failure_category,
                        error_type=error_type,
                        error_code=error_code,
                        error_message=error_message,
                    )
                )
