"""Unit tests for ExecutionContext and ambient propagation primitives."""

import asyncio
from contextvars import copy_context
from dataclasses import FrozenInstanceError
import threading
import unittest
from unittest.mock import MagicMock
import uuid

from harness.agent.budget import ExecutionBudget
from harness.agent.react import ReActController
from harness.llm.client import LLMResponse
from harness.runtime.context import (
    ExecutionContext,
    execution_context_scope,
    get_current_context,
)
from harness.tools.registry import ToolRegistry


class TestExecutionContext(unittest.TestCase):
    """Test suite for ExecutionContext immutability, hierarchy, and scoping."""

    def setUp(self) -> None:
        self.budget = ExecutionBudget(max_steps=5, max_tool_calls=10, max_runtime_seconds=30.0)

    def test_create_root_context(self) -> None:
        ctx = ExecutionContext.create_root(budget=self.budget)
        self.assertEqual(len(ctx.trace_id), 32)
        int(ctx.trace_id, 16)  # Validates hex format
        self.assertEqual(len(ctx.run_id), 32)
        int(ctx.run_id, 16)
        self.assertEqual(ctx.root_run_id, ctx.run_id)
        self.assertIsNone(ctx.parent_run_id)
        self.assertEqual(ctx.delegation_depth, 0)
        self.assertEqual(ctx.agent_id, "orchestrator-main")
        self.assertEqual(ctx.agent_role, "orchestrator")
        self.assertEqual(ctx.budget_limits, self.budget)

    def test_create_root_custom_ids(self) -> None:
        custom_trace = uuid.uuid4().hex
        custom_run = uuid.uuid4().hex
        ctx = ExecutionContext.create_root(
            budget=self.budget,
            agent_id="custom-agent",
            agent_role="custom-role",
            trace_id=custom_trace,
            run_id=custom_run,
        )
        self.assertEqual(ctx.trace_id, custom_trace)
        self.assertEqual(ctx.run_id, custom_run)
        self.assertEqual(ctx.root_run_id, custom_run)
        self.assertIsNone(ctx.parent_run_id)
        self.assertEqual(ctx.agent_id, "custom-agent")
        self.assertEqual(ctx.agent_role, "custom-role")

    def test_validation_errors(self) -> None:
        with self.assertRaises(ValueError):
            ExecutionContext.create_root(budget=self.budget, trace_id="")
        with self.assertRaises(ValueError):
            ExecutionContext.create_root(budget=self.budget, run_id="   ")
        with self.assertRaises(ValueError):
            ExecutionContext.create_root(budget=self.budget, agent_id="")
        with self.assertRaises(ValueError):
            ExecutionContext.create_root(budget=self.budget, agent_role=" ")
        with self.assertRaises(ValueError):
            ExecutionContext(
                trace_id="a" * 32,
                run_id="b" * 32,
                root_run_id="b" * 32,
                parent_run_id=None,
                delegation_depth=-1,
                agent_id="test",
                agent_role="test",
                budget_limits=self.budget,
            )
        with self.assertRaises(ValueError):
            ExecutionContext(
                trace_id="a" * 32,
                run_id="b" * 32,
                root_run_id="b" * 32,
                parent_run_id=None,
                delegation_depth=0,
                agent_id="test",
                agent_role="test",
                budget_limits="invalid_budget",  # type: ignore
            )

    def test_fork_child_context(self) -> None:
        root = ExecutionContext.create_root(budget=self.budget)
        child_budget = ExecutionBudget(max_steps=2, max_tool_calls=3)
        child = root.fork_child(agent_id="sub-1", agent_role="researcher", child_budget=child_budget)

        self.assertEqual(child.trace_id, root.trace_id)
        self.assertEqual(child.root_run_id, root.run_id)
        self.assertEqual(child.parent_run_id, root.run_id)
        self.assertNotEqual(child.run_id, root.run_id)
        self.assertEqual(len(child.run_id), 32)
        int(child.run_id, 16)
        self.assertEqual(child.delegation_depth, 1)
        self.assertEqual(child.agent_id, "sub-1")
        self.assertEqual(child.agent_role, "researcher")
        self.assertEqual(child.budget_limits, child_budget)

        # Fork grandchild
        grandchild = child.fork_child(agent_id="sub-sub-1", agent_role="worker")
        self.assertEqual(grandchild.trace_id, root.trace_id)
        self.assertEqual(grandchild.root_run_id, root.run_id)
        self.assertEqual(grandchild.parent_run_id, child.run_id)
        self.assertEqual(grandchild.delegation_depth, 2)
        self.assertEqual(grandchild.budget_limits, child_budget)

    def test_immutability(self) -> None:
        ctx = ExecutionContext.create_root(budget=self.budget)
        with self.assertRaises((FrozenInstanceError, AttributeError)):
            ctx.trace_id = "mutated"  # type: ignore
        with self.assertRaises((FrozenInstanceError, AttributeError)):
            ctx.delegation_depth = 5  # type: ignore
        with self.assertRaises((FrozenInstanceError, AttributeError)):
            ctx.budget_limits.max_steps = 999  # type: ignore

    def test_ambient_scope_lifecycle(self) -> None:
        self.assertIsNone(get_current_context())
        ctx = ExecutionContext.create_root(budget=self.budget)

        with execution_context_scope(ctx) as active:
            self.assertEqual(active, ctx)
            self.assertEqual(get_current_context(), ctx)

        self.assertIsNone(get_current_context())

    def test_ambient_scope_restoration_on_exception(self) -> None:
        self.assertIsNone(get_current_context())
        ctx = ExecutionContext.create_root(budget=self.budget)

        with self.assertRaises(RuntimeError):
            with execution_context_scope(ctx):
                self.assertEqual(get_current_context(), ctx)
                raise RuntimeError("Simulated crash inside scope")

        self.assertIsNone(get_current_context())

    def test_nested_execution_context_scope(self) -> None:
        root = ExecutionContext.create_root(budget=self.budget)
        child = root.fork_child(agent_id="sub-1", agent_role="sub")

        with execution_context_scope(root):
            self.assertEqual(get_current_context(), root)
            with execution_context_scope(child):
                self.assertEqual(get_current_context(), child)
            self.assertEqual(get_current_context(), root)

        self.assertIsNone(get_current_context())

    def test_nested_controller_guard(self) -> None:
        from harness.agent.ledger import HierarchicalBudgetLedger

        ledger = HierarchicalBudgetLedger(root_budget=self.budget)
        root = ExecutionContext.create_root(budget=self.budget, budget_ledger=ledger)
        child = root.fork_child(agent_id="sub-1", agent_role="sub")

        mock_llm = MagicMock()
        mock_llm.chat.return_value = LLMResponse(content="Child finished", tool_calls=[])
        mock_executor = MagicMock()
        registry = ToolRegistry()

        nested_controller = ReActController(
            llm_client=mock_llm,
            tool_registry=registry,
            tool_executor=mock_executor,
            agent_id="sub-1",
            agent_role="sub",
        )

        with execution_context_scope(root):
            # Guard trigger: ambient context exists but no explicit delegated context provided
            with self.assertRaises(RuntimeError) as cm:
                nested_controller.run_turn("Nested request without explicit context")
            self.assertIn("Nested controller invocation detected", str(cm.exception))

            # Proper delegation: explicit context passed -> succeeds
            result = nested_controller.run_turn("Proper delegation", context=child)
            self.assertTrue(result.is_success)
            self.assertEqual(result.final_text, "Child finished")

    def test_asyncio_task_propagation(self) -> None:
        root = ExecutionContext.create_root(budget=self.budget)

        async def inner_coro() -> ExecutionContext | None:
            return get_current_context()

        async def main() -> tuple[ExecutionContext | None, ExecutionContext | None]:
            with execution_context_scope(root):
                task1 = asyncio.create_task(inner_coro())
                task2 = asyncio.create_task(inner_coro())
                return await asyncio.gather(task1, task2)

        results = asyncio.run(main())
        self.assertEqual(results[0], root)
        self.assertEqual(results[1], root)

    def test_thread_boundary_isolation_and_explicit_copy(self) -> None:
        root = ExecutionContext.create_root(budget=self.budget)

        raw_thread_context: list[ExecutionContext | None] = []
        copied_thread_context: list[ExecutionContext | None] = []

        def worker(target_list: list[ExecutionContext | None]) -> None:
            target_list.append(get_current_context())

        with execution_context_scope(root):
            # 1. Raw thread does NOT inherit ambient contextvar
            t1 = threading.Thread(target=worker, args=(raw_thread_context,))
            t1.start()
            t1.join()
            self.assertIsNone(raw_thread_context[0])

            # 2. Explicit context copy DOES propagate to thread
            ctx_snapshot = copy_context()
            t2 = threading.Thread(target=lambda: ctx_snapshot.run(worker, copied_thread_context))
            t2.start()
            t2.join()
            self.assertEqual(copied_thread_context[0], root)


if __name__ == "__main__":
    unittest.main()
