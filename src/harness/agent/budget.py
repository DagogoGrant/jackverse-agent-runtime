"""Execution budget and structured result contracts for ReAct agent runs."""

from dataclasses import dataclass
from enum import Enum


class TerminationReason(str, Enum):
    """Explicit, machine-readable reason why an agent run completed or halted."""

    FINAL_ANSWER = "FINAL_ANSWER"
    STEP_BUDGET_EXCEEDED = "STEP_BUDGET_EXCEEDED"
    TOOL_BUDGET_EXCEEDED = "TOOL_BUDGET_EXCEEDED"
    TIME_BUDGET_EXCEEDED = "TIME_BUDGET_EXCEEDED"
    ERROR = "ERROR"


@dataclass(frozen=True)
class ExecutionBudget:
    """Configurable resource bounds for an agent run.

    Note on concurrency & deadlines:
        max_runtime_seconds is a cooperative harness-level budget evaluated
        between turn operations. It is not a preemptive thread-interrupting
        deadline for in-flight LLM requests or I/O. Transport-level timeouts
        remain governed separately by LLMClient.timeout.
    """

    max_steps: int = 10
    max_tool_calls: int = 25
    max_runtime_seconds: float = 60.0
    max_observation_chars: int | None = 16_000

    def __post_init__(self) -> None:
        if isinstance(self.max_steps, bool) or not isinstance(self.max_steps, int) or self.max_steps <= 0:
            raise ValueError("max_steps must be a positive integer (> 0).")
        if isinstance(self.max_tool_calls, bool) or not isinstance(self.max_tool_calls, int) or self.max_tool_calls <= 0:
            raise ValueError("max_tool_calls must be a positive integer (> 0).")
        if isinstance(self.max_runtime_seconds, bool) or not isinstance(self.max_runtime_seconds, (int, float)) or self.max_runtime_seconds <= 0.0:
            raise ValueError("max_runtime_seconds must be a positive number (> 0.0).")
        if self.max_observation_chars is not None:
            if isinstance(self.max_observation_chars, bool) or not isinstance(self.max_observation_chars, int) or self.max_observation_chars <= 0:
                raise ValueError("max_observation_chars must be a positive integer (> 0).")


@dataclass(frozen=True)
class RunResult:
    """Structured, machine-readable outcome of an agent execution turn."""

    final_text: str | None
    termination_reason: TerminationReason
    steps: int
    tool_calls: int
    runtime_seconds: float
    error: str | None = None

    @property
    def is_success(self) -> bool:
        """Indicates whether the agent reached a successful final answer."""
        return self.termination_reason == TerminationReason.FINAL_ANSWER
