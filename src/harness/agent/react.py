from collections.abc import Sequence
import json
import logging
import time
from typing import Any
import uuid

from harness.agent.budget import ExecutionBudget, RunResult, TerminationReason
from harness.llm.client import LLMResponse
from harness.memory.base import MemorySource
from harness.memory.recovery import RecoveryDetector, ToolAttempt
from harness.runtime.context import (
    ExecutionContext,
    execution_context_scope,
    get_current_context,
)
from harness.runtime.events import (
    FailureCategory,
    LifecycleEventBus,
    RunFinishedEvent,
    RunStartedEvent,
    ToolCallRequestedEvent,
)
from harness.tools.base import ErrorCode, ToolResult
from harness.tools.executor import ToolExecutor
from harness.tools.registry import ToolRegistry, UnknownToolError

logger = logging.getLogger("harness.react")


class ReActController:
    """Orchestrates the bounded Reason-Act-Observe loop using structured tool calling."""

    def __init__(
        self,
        llm_client: Any,
        tool_registry: ToolRegistry,
        tool_executor: ToolExecutor,
        budget: ExecutionBudget | None = None,
        max_steps: int | None = None,
        system_prompt: str = "You are a helpful assistant running inside an agent harness.",
        memory_manager: Any | None = None,
        enable_procedural_memory: bool = True,
        event_bus: LifecycleEventBus | None = None,
        agent_id: str = "orchestrator-main",
        agent_role: str = "orchestrator",
        budget_ledger: Any | None = None,
        max_delegation_depth: int = 2,
        max_delegations: int = 5,
        clock: Any | None = None,
    ) -> None:
        self.memory_manager = memory_manager
        self.enable_procedural_memory = enable_procedural_memory
        self.recovery_detector = RecoveryDetector()
        self._initial_budget_ledger = budget_ledger
        self._initial_ledger_used = False
        self.budget_ledger = budget_ledger
        self.max_delegation_depth = max_delegation_depth
        self.max_delegations = max_delegations
        self._clock = clock if clock is not None else time.time
        if budget is not None:
            self.budget = budget
        elif max_steps is not None:
            self.budget = ExecutionBudget(max_steps=max_steps)
        else:
            self.budget = ExecutionBudget()

        self.llm_client = llm_client
        self.tool_registry = tool_registry
        self.tool_executor = tool_executor
        self.event_bus = event_bus
        self.agent_id = agent_id
        self.agent_role = agent_role

        # Wire authoritative observation ceiling from ExecutionBudget into ToolExecutor (single source of truth)
        if hasattr(self.tool_executor, "max_observation_chars"):
            if self.budget.max_observation_chars is not None:
                self.tool_executor.max_observation_chars = self.budget.max_observation_chars

        self.system_prompt = system_prompt
        self.context: list[dict[str, Any]] = [
            {"role": "system", "content": system_prompt}
        ]

    def reset_conversation(self) -> None:
        """Reset short-term conversational context for a new evaluation task.

        Preserves:
            - Persistent long-term memory (SQLite store & neural embeddings)
            - Registered tools and external MCP connections
            - Governance rules and authorization engine
            - Configured execution budget
            - Lifecycle event bus and observability telemetry
        """
        self.context = [{"role": "system", "content": self.system_prompt}]
        self.recovery_detector.clear()
        logger.info(f"Conversation context reset to initial system prompt for agent '{self.agent_role}'")

    @property
    def max_steps(self) -> int:
        """Backwards-compatible accessor for max_steps from the active budget."""
        return self.budget.max_steps

    def _finalize_turn_memory(self, run_id: str, user_input: str, observations: list[Any]) -> None:
        """Admit user input and successful tool observations to persistent memory."""
        if self.memory_manager is not None:
            self.memory_manager.admit_and_store(
                content=user_input,
                source=MemorySource.USER_INPUT,
                metadata={"run_id": run_id},
            )
            for item in observations:
                if isinstance(item, tuple) and len(item) == 2:
                    tool_name, obs = item
                else:
                    tool_name, obs = None, item
                self.memory_manager.admit_and_store(
                    content=obs,
                    source=MemorySource.TOOL_OBSERVATION,
                    metadata={
                        "run_id": run_id,
                        "tool_name": tool_name,
                        "is_transient": tool_name == "find_connection",
                    },
                )

    def _build_request_context(
        self,
        base_context: Sequence[dict[str, Any]],
        user_message: dict[str, Any],
        recalled_formatted: str | None = None,
        procedural_formatted: str | None = None,
    ) -> list[dict[str, Any]]:
        """Construct a turn-scoped, outbound message context satisfying provider role invariants.

        Invariant: Exactly one system message exists in the outbound request, strictly at index 0.
        Retrieved declarative memory and procedural lessons are incorporated into the leading
        system message content for this turn only, without mutating canonical conversation history.
        """
        # Shallow copy of message dictionaries to ensure turn isolation
        request_context: list[dict[str, Any]] = [dict(msg) for msg in base_context]

        # Enforce canonical context integrity: exactly one system message must exist at index 0
        system_indices = [
            i for i, msg in enumerate(request_context)
            if msg.get("role") == "system"
        ]
        if system_indices != [0]:
            raise ValueError(
                "Canonical conversation context must contain exactly one system message at index 0."
            )

        turn_extensions: list[str] = []
        if procedural_formatted:
            turn_extensions.append(procedural_formatted)
        if recalled_formatted:
            turn_extensions.append(recalled_formatted)

        if turn_extensions:
            extension_text = "\n\n".join(turn_extensions)
            leading_system = dict(request_context[0])
            base_content = leading_system.get("content", "")
            leading_system["content"] = f"{base_content}\n\n{extension_text}".strip()
            request_context[0] = leading_system

        request_context.append(dict(user_message))
        return request_context

    def run_turn(
        self,
        user_input: str,
        *,
        context: ExecutionContext | None = None,
        reset_conversation: bool = False,
    ) -> RunResult:
        """Process one user request through the bounded ReAct loop returning a structured RunResult."""
        if reset_conversation:
            self.reset_conversation()
        ambient = get_current_context()
        if context is None and ambient is not None:
            raise RuntimeError("Nested controller invocation detected without explicit ExecutionContext child delegation")

        if context is None:
            from harness.agent.ledger import HierarchicalBudgetLedger

            if self._initial_budget_ledger is not None and not self._initial_ledger_used:
                active_ledger = self._initial_budget_ledger
                self._initial_ledger_used = True
            else:
                active_ledger = HierarchicalBudgetLedger(
                    root_budget=self.budget,
                    max_delegation_depth=self.max_delegation_depth,
                    max_delegations=self.max_delegations,
                    clock=self._clock,
                )
            self.budget_ledger = active_ledger
            active_ctx = ExecutionContext.create_root(
                budget=self.budget,
                agent_id=self.agent_id,
                agent_role=self.agent_role,
                budget_ledger=active_ledger,
            )
        elif context.delegation_depth == 0 and context.budget_ledger is None:
            from dataclasses import replace
            from harness.agent.ledger import HierarchicalBudgetLedger

            active_ledger = HierarchicalBudgetLedger(
                root_budget=context.budget_limits or self.budget,
                max_delegation_depth=self.max_delegation_depth,
                max_delegations=self.max_delegations,
                clock=self._clock,
            )
            active_ctx = replace(context, budget_ledger=active_ledger)
            self.budget_ledger = active_ledger
        elif context.delegation_depth >= 1:
            if context.budget_ledger is None:
                raise RuntimeError(
                    f"Malformed child ExecutionContext (depth={context.delegation_depth}, role='{context.agent_role}') "
                    "arrived without inherited budget authority (budget_ledger is None). Child execution must inherit its parent root ledger."
                )
            active_ctx = context
            active_ledger = context.budget_ledger
            self.budget_ledger = active_ledger
        else:
            active_ctx = context
            active_ledger = context.budget_ledger
            self.budget_ledger = active_ledger

        with execution_context_scope(active_ctx):
            run_id = active_ctx.run_id
            start_time = time.perf_counter()
            logger.info(f"run_started run_id={run_id}")

            if self.event_bus:
                self.event_bus.publish(
                    RunStartedEvent(
                        timestamp=time.time(),
                        trace_id=active_ctx.trace_id,
                        run_id=active_ctx.run_id,
                        root_run_id=active_ctx.root_run_id,
                        parent_run_id=active_ctx.parent_run_id,
                        agent_id=active_ctx.agent_id,
                        agent_role=active_ctx.agent_role,
                        user_input_length=len(user_input),
                        max_steps=self.budget.max_steps,
                        max_tool_calls=self.budget.max_tool_calls,
                        max_runtime_seconds=self.budget.max_runtime_seconds,
                    )
                )

            termination_reason = TerminationReason.ERROR
            steps_taken = 0
            total_tool_calls = 0
            is_success = False
            failure_category: FailureCategory | None = None
            error_type: str | None = None
            error_code: str | None = None
            error_message: str | None = None

            try:
                self.recovery_detector.clear()

                # Retrieve relevant declarative memories and procedural guidance
                recalled_formatted: str | None = None
                procedural_formatted: str | None = None
                tools = self.tool_registry.list_specs()

                if self.memory_manager is not None:
                    recalled = self.memory_manager.retrieve(user_input)
                    recalled_formatted = self.memory_manager.format_context(recalled)

                    if self.enable_procedural_memory and hasattr(self.memory_manager, "retrieve_procedural"):
                        tool_names = [t.name for t in tools]
                        lessons = self.memory_manager.retrieve_procedural(tool_names)
                        procedural_formatted = self.memory_manager.format_procedural_context(lessons)

                user_msg = {"role": "user", "content": user_input}

                # Temporary LLM context: leading system prompt (with turn-scoped memory augmentation)
                # + canonical prior conversation history + current user message.
                llm_context = self._build_request_context(
                    base_context=self.context,
                    user_message=user_msg,
                    recalled_formatted=recalled_formatted,
                    procedural_formatted=procedural_formatted,
                )

                # Base persistent short-term context stores only normal conversation state
                self.context.append(user_msg)

                seen_observations: set[str] = set()
                turn_observations: list[str] = []

                while True:
                    elapsed = time.perf_counter() - start_time

                    # 1. Cooperative time budget check between operations
                    if elapsed >= self.budget.max_runtime_seconds:
                        logger.warning(f"time_budget_exceeded run_id={run_id} elapsed={elapsed:.2f}s")
                        self._finalize_turn_memory(run_id, user_input, turn_observations)
                        termination_reason = TerminationReason.TIME_BUDGET_EXCEEDED
                        failure_category = FailureCategory.BUDGET
                        error_type = "TimeBudgetExceededError"
                        error_message = f"Execution time budget ({self.budget.max_runtime_seconds}s) exceeded."
                        return RunResult(
                            final_text=None,
                            termination_reason=termination_reason,
                            steps=steps_taken,
                            tool_calls=total_tool_calls,
                            runtime_seconds=round(elapsed, 4),
                            error=error_message,
                        )

                    # 2. Step budget check before querying model
                    if steps_taken >= self.budget.max_steps:
                        logger.warning(f"step_limit_exceeded run_id={run_id} max_steps={self.budget.max_steps}")
                        self._finalize_turn_memory(run_id, user_input, turn_observations)
                        termination_reason = TerminationReason.STEP_BUDGET_EXCEEDED
                        failure_category = FailureCategory.BUDGET
                        error_type = "StepBudgetExceededError"
                        error_message = f"Maximum agent steps ({self.budget.max_steps}) reached before final response."
                        return RunResult(
                            final_text=None,
                            termination_reason=termination_reason,
                            steps=steps_taken,
                            tool_calls=total_tool_calls,
                            runtime_seconds=round(elapsed, 4),
                            error=error_message,
                        )

                    logger.debug(f"step_started run_id={run_id} step={steps_taken + 1}")
                    response: LLMResponse = self.llm_client.chat(llm_context, tools=tools)
                    steps_taken += 1
                    if active_ledger is not None:
                        active_ledger.record_parent_consumption(steps=1)

                    if not response.tool_calls:
                        elapsed = time.perf_counter() - start_time
                        if response.content is None:
                            self._finalize_turn_memory(run_id, user_input, turn_observations)
                            termination_reason = TerminationReason.ERROR
                            failure_category = FailureCategory.PROVIDER
                            error_type = "EmptyLLMResponseError"
                            error_message = "Received empty response content from LLM."
                            return RunResult(
                                final_text=None,
                                termination_reason=termination_reason,
                                steps=steps_taken,
                                tool_calls=total_tool_calls,
                                runtime_seconds=round(elapsed, 4),
                                error=error_message,
                            )
                        self.context.append({"role": "assistant", "content": response.content})
                        logger.info(f"run_completed run_id={run_id} steps={steps_taken} tool_calls={total_tool_calls}")
                        self._finalize_turn_memory(run_id, user_input, turn_observations)
                        termination_reason = TerminationReason.FINAL_ANSWER
                        is_success = True
                        return RunResult(
                            final_text=response.content,
                            termination_reason=termination_reason,
                            steps=steps_taken,
                            tool_calls=total_tool_calls,
                            runtime_seconds=round(elapsed, 4),
                        )

                    # Tool calls requested: emit ToolCallRequestedEvent for each call
                    if self.event_bus:
                        for call in response.tool_calls:
                            self.event_bus.publish(
                                ToolCallRequestedEvent(
                                    timestamp=time.time(),
                                    trace_id=active_ctx.trace_id,
                                    run_id=active_ctx.run_id,
                                    root_run_id=active_ctx.root_run_id,
                                    parent_run_id=active_ctx.parent_run_id,
                                    agent_id=active_ctx.agent_id,
                                    agent_role=active_ctx.agent_role,
                                    call_id=call.id,
                                    tool_name=call.name,
                                    step=steps_taken,
                                )
                            )

                    assistant_msg: dict[str, Any] = {
                        "role": "assistant",
                        "content": response.content,
                        "tool_calls": [
                            {
                                "id": call.id,
                                "type": "function",
                                "function": {
                                    "name": call.name,
                                    "arguments": json.dumps(call.arguments)
                                    if isinstance(call.arguments, dict)
                                    else str(call.arguments),
                                },
                            }
                            for call in response.tool_calls
                        ],
                    }
                    llm_context.append(assistant_msg)
                    self.context.append(assistant_msg)

                    # Execute tool calls with budget checks before execution
                    for call in response.tool_calls:
                        # 3. Tool call budget check: verify budget before incrementing and executing
                        if total_tool_calls >= self.budget.max_tool_calls:
                            elapsed = time.perf_counter() - start_time
                            logger.warning(
                                f"tool_budget_exceeded run_id={run_id} max_tool_calls={self.budget.max_tool_calls}"
                            )
                            self._finalize_turn_memory(run_id, user_input, turn_observations)
                            termination_reason = TerminationReason.TOOL_BUDGET_EXCEEDED
                            failure_category = FailureCategory.BUDGET
                            error_type = "ToolBudgetExceededError"
                            error_message = f"Maximum tool calls budget ({self.budget.max_tool_calls}) reached."
                            return RunResult(
                                final_text=None,
                                termination_reason=termination_reason,
                                steps=steps_taken,
                                tool_calls=total_tool_calls,
                                runtime_seconds=round(elapsed, 4),
                                error=error_message,
                            )

                        total_tool_calls += 1
                        if active_ledger is not None:
                            active_ledger.record_parent_consumption(tool_calls=1)
                        logger.info(f"tool_invoked run_id={run_id} tool={call.name} call_num={total_tool_calls}")
                        try:
                            tool = self.tool_registry.get(call.name)
                            result = self.tool_executor.execute(tool, call.arguments, call_id=call.id)
                        except UnknownToolError as e:
                            result = ToolResult(
                                content=f"Error: {e}",
                                is_error=True,
                                error_code=ErrorCode.INVALID_ARGUMENT,
                            )

                        outcome = "error" if result.is_error else "success"
                        logger.info(f"tool_completed run_id={run_id} tool={call.name} outcome={outcome}")

                        if self.enable_procedural_memory:
                            attempt = ToolAttempt(
                                tool_name=call.name,
                                arguments=call.arguments if isinstance(call.arguments, dict) else {},
                                result=result,
                                step=steps_taken,
                            )
                            if result.is_error:
                                self.recovery_detector.record_attempt(attempt)
                            else:
                                lesson = self.recovery_detector.detect_recovery(attempt)
                                if lesson is not None and self.memory_manager is not None and hasattr(self.memory_manager, "admit_procedural_lesson"):
                                    self.memory_manager.admit_procedural_lesson(lesson)

                        if not result.is_error and result.content and result.content not in seen_observations:
                            seen_observations.add(result.content)
                            turn_observations.append((call.name, result.content))

                        tool_msg: dict[str, Any] = {
                            "role": "tool",
                            "tool_call_id": call.id,
                            "content": result.content,
                        }
                        llm_context.append(tool_msg)
                        self.context.append(tool_msg)
            except Exception as exc:
                if not is_success:
                    termination_reason = TerminationReason.ERROR
                    error_type = type(exc).__name__
                    error_message = str(exc)[:200]
                    failure_category = FailureCategory.INTERNAL
                raise
            finally:
                if self.event_bus:
                    duration = time.perf_counter() - start_time
                    self.event_bus.publish(
                        RunFinishedEvent(
                            timestamp=time.time(),
                            trace_id=active_ctx.trace_id,
                            run_id=active_ctx.run_id,
                            root_run_id=active_ctx.root_run_id,
                            parent_run_id=active_ctx.parent_run_id,
                            agent_id=active_ctx.agent_id,
                            agent_role=active_ctx.agent_role,
                            termination_reason=termination_reason,
                            steps=steps_taken,
                            tool_calls=total_tool_calls,
                            duration_seconds=duration,
                            is_success=is_success,
                            failure_category=failure_category,
                            error_type=error_type,
                            error_code=error_code,
                            error_message=error_message,
                        )
                    )

    def run(
        self,
        user_input: str,
        *,
        context: ExecutionContext | None = None,
        reset_conversation: bool = False,
    ) -> str:
        """Execute turn and return final answer string, raising RuntimeError on non-final outcomes."""
        result = self.run_turn(user_input, context=context, reset_conversation=reset_conversation)
        if result.is_success and result.final_text is not None:
            return result.final_text
        raise RuntimeError(result.error or f"Run ended with termination reason: {result.termination_reason.value}")

    def step(
        self,
        user_input: str,
        *,
        context: ExecutionContext | None = None,
    ) -> str:
        """Alias for run() to maintain API consistency."""
        return self.run(user_input, context=context)
