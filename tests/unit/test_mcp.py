"""Unit and integration tests for Model Context Protocol (MCP) integration in Week 2."""

import os
from pathlib import Path
import sys
import tempfile
from typing import Any
import unittest
from unittest.mock import MagicMock

from harness.agent.budget import ExecutionBudget
from harness.agent.react import ReActController
from harness.cli import build_controller
from harness.config import (
    AgentConfig,
    AppConfig,
    LLMConfig,
    MCPServerConfig,
    ToolsConfig,
)
from harness.llm.client import LLMResponse
from harness.mcp.adapter import MCPToolAdapter
from harness.mcp.client import MCPClient
from harness.tools.base import ErrorCode, ToolResult, ToolSpec
from harness.tools.executor import ToolExecutor
from harness.tools.registry import ToolRegistry


class TestMCPIntegration(unittest.TestCase):
    def setUp(self) -> None:
        self.server_config = MCPServerConfig(
            name="test_echo_server",
            command=sys.executable,
            args=["-m", "harness.mcp.servers.echo_server"],
        )

    def test_mcp_tool_discovery(self) -> None:
        """Verify dynamic runtime discovery of tools from an external stdio MCP server."""
        client = MCPClient(self.server_config)
        specs = client.list_tools()

        discovered_names = {s.name for s in specs}
        self.assertIn("echo", discovered_names)
        self.assertIn("add", discovered_names)
        self.assertIn("fail_tool", discovered_names)
        self.assertIn("large_payload", discovered_names)

    def test_mcp_tool_spec_conversion(self) -> None:
        """Verify discovered MCP tools are converted into valid ToolSpec instances."""
        client = MCPClient(self.server_config)
        specs = {s.name: s for s in client.list_tools()}

        echo_spec = specs["echo"]
        self.assertIsInstance(echo_spec, ToolSpec)
        self.assertEqual(echo_spec.name, "echo")
        self.assertTrue(len(echo_spec.description) > 0)
        self.assertFalse(echo_spec.is_mutating)
        self.assertEqual(echo_spec.input_schema.get("type"), "object")
        self.assertIn("message", echo_spec.input_schema.get("properties", {}))
        self.assertIn("message", echo_spec.input_schema.get("required", []))

        add_spec = specs["add"]
        self.assertIsInstance(add_spec, ToolSpec)
        self.assertEqual(add_spec.name, "add")
        self.assertIn("a", add_spec.input_schema.get("properties", {}))
        self.assertIn("b", add_spec.input_schema.get("properties", {}))
        self.assertEqual(set(add_spec.input_schema.get("required", [])), {"a", "b"})

    def test_mcp_invocation_success(self) -> None:
        """Verify successful tool invocation over stdio transport returning ToolResult."""
        client = MCPClient(self.server_config)

        res_echo = client.call_tool("echo", {"message": "hello world"})
        self.assertIsInstance(res_echo, ToolResult)
        self.assertFalse(res_echo.is_error)
        self.assertIsNone(res_echo.error_code)
        self.assertEqual(res_echo.content, "echo: hello world")

        res_add = client.call_tool("add", {"a": 40, "b": 2})
        self.assertIsInstance(res_add, ToolResult)
        self.assertFalse(res_add.is_error)
        self.assertIsNone(res_add.error_code)
        self.assertEqual(res_add.content, "42")

    def test_mcp_error_conversion_and_containment(self) -> None:
        """Verify server-side tool errors are contained and mapped to INTERNAL_ERROR."""
        client = MCPClient(self.server_config)

        res = client.call_tool("fail_tool", {"message": "deliberate-crash"})
        self.assertIsInstance(res, ToolResult)
        self.assertTrue(res.is_error)
        self.assertEqual(res.error_code, ErrorCode.INTERNAL_ERROR)
        self.assertIn("fail_tool", res.content)

    def test_mcp_transport_error(self) -> None:
        """Verify transport/OS failures map to TRANSIENT_ERROR."""
        bad_config = MCPServerConfig(
            name="broken_server",
            command="/nonexistent/path/to/mcp_server_binary",
            args=[],
        )
        client = MCPClient(bad_config)
        res = client.call_tool("any_tool", {})
        self.assertIsInstance(res, ToolResult)
        self.assertTrue(res.is_error)
        self.assertEqual(res.error_code, ErrorCode.TRANSIENT_ERROR)
        self.assertIn("MCP transport error", res.content)

    def test_mcp_through_tool_executor_success(self) -> None:
        """Verify MCP tool wrapped in MCPToolAdapter executes cleanly through ToolExecutor."""
        client = MCPClient(self.server_config)
        specs = {s.name: s for s in client.list_tools()}
        adapter = MCPToolAdapter(specs["echo"], client)

        executor = ToolExecutor()
        result = executor.execute(adapter, {"message": "validated through executor"})

        self.assertIsInstance(result, ToolResult)
        self.assertFalse(result.is_error)
        self.assertEqual(result.content, "echo: validated through executor")

    def test_mcp_through_tool_executor_layer0_schema_rejection(self) -> None:
        """Verify Layer 0 schema validation traps bad arguments before MCP tool invocation."""
        client = MCPClient(self.server_config)
        specs = {s.name: s for s in client.list_tools()}
        adapter = MCPToolAdapter(specs["echo"], client)

        executor = ToolExecutor()

        # Missing required field
        res_missing = executor.execute(adapter, {})
        self.assertTrue(res_missing.is_error)
        self.assertEqual(res_missing.error_code, ErrorCode.INVALID_ARGUMENT)
        self.assertIn("Missing required argument 'message'", res_missing.content)

        # Wrong type (int instead of string)
        res_wrong_type = executor.execute(adapter, {"message": 9999})
        self.assertTrue(res_wrong_type.is_error)
        self.assertEqual(res_wrong_type.error_code, ErrorCode.INVALID_ARGUMENT)
        self.assertIn("Argument 'message' must be a string", res_wrong_type.content)

    def test_mcp_through_tool_executor_layer2_observation_ceiling(self) -> None:
        """Verify Layer 2 observation ceiling strictly bounds oversized MCP tool output."""
        client = MCPClient(self.server_config)
        specs = {s.name: s for s in client.list_tools()}
        adapter = MCPToolAdapter(specs["large_payload"], client)

        # Set ceiling to 150 characters
        executor = ToolExecutor(max_observation_chars=150)
        result = executor.execute(adapter, {"count": 10000})

        self.assertFalse(result.is_error)
        self.assertLessEqual(len(result.content), 150)
        self.assertIn("[OBSERVATION PARTIALLY SHOWN]", result.content)
        self.assertIn("original_chars: 10000", result.content)

    def test_mcp_client_lifecycle_and_close(self) -> None:
        """Verify client lifecycle, context manager entry/exit, and rejection after close."""
        with MCPClient(self.server_config) as client:
            self.assertFalse(client.is_closed)

        self.assertTrue(client.is_closed)
        with self.assertRaises(RuntimeError):
            client.list_tools()

        res = client.call_tool("echo", {"message": "closed"})
        self.assertTrue(res.is_error)
        self.assertEqual(res.error_code, ErrorCode.INTERNAL_ERROR)

    def test_mcp_cli_build_controller_and_registry_integration(self) -> None:
        """Verify build_controller dynamically registers configured MCP tools alongside Week 1 tools."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            config = AppConfig(
                agent=AgentConfig(max_steps=5),
                llm=LLMConfig(
                    base_url="https://mock.llm.example",
                    model="mock-model",
                    temperature=0.0,
                ),
                tools=ToolsConfig(workspace_root=tmp_dir),
                mcp_servers=[self.server_config],
            )

            controller, workspace, registry = build_controller(config, "mock_key")
            spec_names = {s.name for s in registry.list_specs()}

            # All six Week 1 filesystem tools must be present
            self.assertIn("create_directory", spec_names)
            self.assertIn("create_file", spec_names)
            self.assertIn("read_file", spec_names)
            self.assertIn("list_directory", spec_names)
            self.assertIn("search_files", spec_names)
            self.assertIn("modify_file", spec_names)

            # Dynamically discovered MCP tools must also be present
            self.assertIn("echo", spec_names)
            self.assertIn("add", spec_names)
            self.assertIn("fail_tool", spec_names)
            self.assertIn("large_payload", spec_names)

    def test_mcp_react_e2e_selection_and_execution(self) -> None:
        """Integration/E2E test: ReAct loop discovers, selects, executes MCP tool and feeds observation."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            config = AppConfig(
                agent=AgentConfig(max_steps=5),
                llm=LLMConfig(
                    base_url="https://mock.llm.example",
                    model="mock-model",
                    temperature=0.0,
                ),
                tools=ToolsConfig(workspace_root=tmp_dir),
                mcp_servers=[self.server_config],
            )

            controller, workspace, registry = build_controller(config, "mock_key")

            from harness.llm.client import ToolCall

            class MockLLM:
                def __init__(self) -> None:
                    self.turns = [
                        LLMResponse(
                            content="Calling echo via MCP",
                            tool_calls=[
                                ToolCall(id="call_1", name="echo", arguments={"message": "MCP-ReAct-Success"})
                            ],
                        ),
                        LLMResponse(
                            content="Echo received: echo: MCP-ReAct-Success",
                            tool_calls=[],
                        ),
                    ]

                def chat(self, messages: Any, tools: Any = None) -> LLMResponse:
                    return self.turns.pop(0)

            controller.llm_client = MockLLM()

            run_result = controller.run_turn("Please echo MCP-ReAct-Success")

            self.assertTrue(run_result.is_success)
            self.assertEqual(run_result.steps, 2)
            self.assertEqual(run_result.tool_calls, 1)
            self.assertIn("echo: MCP-ReAct-Success", run_result.final_text)

            # Verify the observation was placed into context
            obs_messages = [
                m for m in controller.context
                if m.get("role") == "tool"
            ]
            self.assertEqual(len(obs_messages), 1)
            self.assertIn("echo: MCP-ReAct-Success", obs_messages[0]["content"])

    def test_mcp_tool_adapter_namespacing_and_collision_safety(self) -> None:
        """Verify external MCP tools can be namespaced to prevent registry collisions while invoking remote names."""
        client = MCPClient(self.server_config)
        specs = {s.name: s for s in client.list_tools()}

        # 1. Test explicit name override
        adapter_override = MCPToolAdapter(
            specs["echo"],
            client,
            name_override="mcpfs_echo",
        )
        self.assertEqual(adapter_override.spec.name, "mcpfs_echo")
        self.assertEqual(adapter_override.remote_name, "echo")

        # Invocation maps back to remote name 'echo'
        res_override = adapter_override.execute({"message": "aliased"})
        self.assertEqual(res_override.content, "echo: aliased")

        # 2. Test prefix namespacing
        adapter_prefixed = MCPToolAdapter(
            specs["add"],
            client,
            prefix="custom",
        )
        self.assertEqual(adapter_prefixed.spec.name, "custom_add")
        self.assertEqual(adapter_prefixed.remote_name, "add")

        res_prefixed = adapter_prefixed.execute({"a": 10, "b": 20})
        self.assertEqual(res_prefixed.content, "30")

        # 3. Test collision safety in ToolRegistry with mock conflicting tool
        from harness.tools.filesystem import ReadFileTool
        from harness.tools.workspace import Workspace

        with tempfile.TemporaryDirectory() as tmp_dir:
            ws = Workspace(Path(tmp_dir))
            builtin_read = ReadFileTool(ws)

            # Suppose remote MCP server also provides a tool named 'read_file'
            remote_read_spec = ToolSpec(
                name="read_file",
                description="Remote MCP read file",
                input_schema={"type": "object", "properties": {"path": {"type": "string"}}},
            )
            # Registering without namespacing would fail with DuplicateToolError
            registry = ToolRegistry()
            registry.register(builtin_read)

            # With namespacing:
            namespaced_mcp_read = MCPToolAdapter(
                remote_read_spec,
                client,
                name_override="mcpfs_read_file",
            )
            # Successfully registers without collision!
            registry.register(namespaced_mcp_read)

            self.assertIn("read_file", [s.name for s in registry.list_specs()])
            self.assertIn("mcpfs_read_file", [s.name for s in registry.list_specs()])
            self.assertIs(registry.get("read_file"), builtin_read)
            self.assertIs(registry.get("mcpfs_read_file"), namespaced_mcp_read)
