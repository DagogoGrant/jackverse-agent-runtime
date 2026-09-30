"""Thread-safe hierarchical budget ledger enforcing root capacity bounds across parent and sub-agents."""

from __future__ import annotations

from collections.abc import Callable
import threading
import time

from harness.agent.budget import ExecutionBudget


class BudgetExhaustedError(RuntimeError):
    """Raised when an action or delegation would exceed available root budget."""


class DelegationLimitExceededError(RuntimeError):
    """Raised when delegation depth or total delegation fan-out limit is exceeded."""


class HierarchicalBudgetLedger:
    """Thread-safe hierarchical budget ledger.

    Invariant:
        Delegation does not create more authority or more budget.
        It redistributes bounded authority and bounded capacity to another principal.
        A child receives a slice of remaining root capacity, never an independent fresh budget.
    """

    def __init__(
        self,
        root_budget: ExecutionBudget,
        max_delegation_depth: int = 2,
        max_delegations: int = 5,
        start_time: float | None = None,
        clock: Callable[[], float] | None = None,
    ) -> None:
        self._lock = threading.Lock()
        self._root_budget = root_budget
        self._max_delegation_depth = max_delegation_depth
        self._max_delegations = max_delegations
        self._clock = clock if clock is not None else time.time

        self._start_time = start_time if start_time is not None else self._clock()
        self._root_deadline = self._start_time + root_budget.max_runtime_seconds

        self._remaining_steps = root_budget.max_steps
        self._remaining_tool_calls = root_budget.max_tool_calls
        self._delegations_count = 0
        self._total_steps_consumed = 0
        self._total_tool_calls_consumed = 0

    @property
    def max_delegation_depth(self) -> int:
        return self._max_delegation_depth

    @property
    def max_delegations(self) -> int:
        return self._max_delegations

    @property
    def remaining_steps(self) -> int:
        with self._lock:
            return self._remaining_steps

    @property
    def remaining_tool_calls(self) -> int:
        with self._lock:
            return self._remaining_tool_calls

    @property
    def delegations_count(self) -> int:
        with self._lock:
            return self._delegations_count

    @property
    def total_steps_consumed(self) -> int:
        with self._lock:
            return self._total_steps_consumed

    @property
    def total_tool_calls_consumed(self) -> int:
        with self._lock:
            return self._total_tool_calls_consumed

    def record_parent_consumption(self, steps: int = 0, tool_calls: int = 0) -> None:
        """Record consumption by the parent orchestrator before or between delegations."""
        with self._lock:
            if steps > 0:
                self._remaining_steps = max(0, self._remaining_steps - steps)
                self._total_steps_consumed += steps
            if tool_calls > 0:
                self._remaining_tool_calls = max(0, self._remaining_tool_calls - tool_calls)
                self._total_tool_calls_consumed += tool_calls

    def allocate_child_slice(
        self,
        requested_ceiling: ExecutionBudget,
        current_depth: int,
    ) -> ExecutionBudget:
        """Allocate a child budget slice from remaining root capacity.

        Raises:
            DelegationLimitExceededError if current_depth >= max_delegation_depth or delegations_count >= max_delegations.
            BudgetExhaustedError if remaining steps, tool calls, or wall-clock deadline are exhausted.
        """
        with self._lock:
            # 1. Depth check
            if current_depth >= self._max_delegation_depth:
                raise DelegationLimitExceededError(
                    f"Delegation rejected: maximum delegation depth ({self._max_delegation_depth}) reached at depth {current_depth}."
                )

            # 2. Fan-out / total delegations check
            if self._delegations_count >= self._max_delegations:
                raise DelegationLimitExceededError(
                    f"Delegation rejected: maximum total delegations ({self._max_delegations}) reached."
                )

            # 3. Deadline check
            now = self._clock()
            time_left = self._root_deadline - now
            if time_left <= 0.0:
                raise BudgetExhaustedError("Delegation rejected: root execution deadline expired.")

            # 4. Step and tool call capacity check
            if self._remaining_steps <= 0:
                raise BudgetExhaustedError("Delegation rejected: zero remaining steps in root budget.")
            if self._remaining_tool_calls <= 0:
                raise BudgetExhaustedError("Delegation rejected: zero remaining tool calls in root budget.")

            # 5. Slicing: child receives min(requested, remaining)
            slice_steps = min(requested_ceiling.max_steps, self._remaining_steps)
            slice_tool_calls = min(requested_ceiling.max_tool_calls, self._remaining_tool_calls)
            slice_runtime = min(requested_ceiling.max_runtime_seconds, time_left)
            slice_obs_chars = requested_ceiling.max_observation_chars

            self._delegations_count += 1

            return ExecutionBudget(
                max_steps=max(1, slice_steps),
                max_tool_calls=max(1, slice_tool_calls),
                max_runtime_seconds=max(0.1, slice_runtime),
                max_observation_chars=slice_obs_chars,
            )

    def reconcile_child_consumption(
        self,
        actual_steps: int,
        actual_tool_calls: int,
    ) -> None:
        """Reconcile actual child consumption back into the shared root ledger.

        Called in a finally block so that even upon child exception/cancellation,
        all consumed capacity is deducted accurately without double-accounting.
        """
        with self._lock:
            self._remaining_steps = max(0, self._remaining_steps - actual_steps)
            self._remaining_tool_calls = max(0, self._remaining_tool_calls - actual_tool_calls)
            self._total_steps_consumed += actual_steps
            self._total_tool_calls_consumed += actual_tool_calls
