"""Unit tests for MCP transport abstraction and Streamable HTTP support (Phase P3B)."""

import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import httpx2
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

from harness.config import MCPServerConfig, load_config
from harness.mcp.adapter import MCPToolAdapter
from harness.mcp.client import MCPClient
from harness.mcp.transport import (
    MCPTransport,
    StdioTransport,
    StreamableHttpTransport,
    create_transport,
)
from harness.tools.base import ErrorCode, ToolResult, ToolSpec
from harness.tools.executor import ToolExecutor
from harness.tools.registry import ToolRegistry


class TestMCPTransportConfigAndValidation(unittest.TestCase):
    """Test configuration validation and factory creation for MCP transports."""

    def test_stdio_transport_defaults_and_validation(self) -> None:
        """Verify StdioTransport initializes parameters correctly."""
        transport = StdioTransport("python", ["-m", "echo"], {"KEY": "VAL"})
        self.assertEqual(transport.args, ["-m", "echo"])
        self.assertEqual(transport.env, {"KEY": "VAL"})
        self.assertIn("StdioTransport", repr(transport))

        with self.assertRaises(ValueError):
            StdioTransport("")

    def test_streamable_http_transport_validation(self) -> None:
        """Verify StreamableHttpTransport enforces security and URL policies."""
        # Localhost http is allowed (localhost, 127.0.0.1, ::1)
        t_local = StreamableHttpTransport("http://localhost:8000/mcp")
        self.assertEqual(t_local.url, "http://localhost:8000/mcp")

        t_ip = StreamableHttpTransport("http://127.0.0.1:8000/mcp")
        self.assertEqual(t_ip.url, "http://127.0.0.1:8000/mcp")

        t_ipv6 = StreamableHttpTransport("http://[::1]:8000/mcp")
        self.assertEqual(t_ipv6.url, "http://[::1]:8000/mcp")

        # testserver is rejected by default (not a production localhost hostname)
        with self.assertRaises(ValueError) as cm_ts:
            StreamableHttpTransport("http://testserver/mcp")
        self.assertIn("must use HTTPS", str(cm_ts.exception))

        # Dedicated test seam allows testserver when explicitly requested
        t_testserver = StreamableHttpTransport("http://testserver/mcp", _allow_insecure_host_for_test=True)
        self.assertEqual(t_testserver.url, "http://testserver/mcp")

        # Remote HTTPS is allowed
        t_remote_https = StreamableHttpTransport("https://mcp.example.com/mcp")
        self.assertEqual(t_remote_https.url, "https://mcp.example.com/mcp")

        # Insecure remote HTTP must be rejected
        with self.assertRaises(ValueError) as cm:
            StreamableHttpTransport("http://remote.example.com/mcp")
        self.assertIn("must use HTTPS", str(cm.exception))

        # Invalid scheme rejected
        with self.assertRaises(ValueError):
            StreamableHttpTransport("ftp://localhost/mcp")

        # Invalid timeout rejected
        with self.assertRaises(ValueError):
            StreamableHttpTransport("http://localhost:8000/mcp", timeout_seconds=-5.0)

    def test_streamable_http_repr_sanitization(self) -> None:
        """Verify __repr__ masks secrets and only shows environment variable name."""
        transport = StreamableHttpTransport(
            url="https://secure.example.com/mcp",
            auth_token_env="MY_SECRET_TOKEN_VAR",
            timeout_seconds=12.0,
        )
        rep = repr(transport)
        self.assertIn("auth_token_env='MY_SECRET_TOKEN_VAR'", rep)
        self.assertNotIn("secret_value", rep)

    def test_create_transport_factory(self) -> None:
        """Verify create_transport dispatches based on MCPServerConfig."""
        cfg_stdio = MCPServerConfig(
            name="local",
            transport="stdio",
            command="python",
            args=["-m", "test"],
        )
        t_stdio = create_transport(cfg_stdio)
        self.assertIsInstance(t_stdio, StdioTransport)

        cfg_http = MCPServerConfig(
            name="remote",
            transport="streamable_http",
            url="http://localhost:8000/mcp",
        )
        t_http = create_transport(cfg_http)
        self.assertIsInstance(t_http, StreamableHttpTransport)

        cfg_bad = MCPServerConfig(name="unknown", transport="websocket")
        with self.assertRaises(ValueError):
            create_transport(cfg_bad)

    def _create_temp_yaml(self, content: str) -> Path:
        temp = tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False)
        temp.write(content)
        temp.close()
        self.addCleanup(lambda: os.path.exists(temp.name) and os.unlink(temp.name))
        return Path(temp.name)

    def test_yaml_config_backwards_compatibility(self) -> None:
        """Verify configs omitting transport default to stdio and parse correctly."""
        content = """
        agent:
          max_steps: 5
        llm:
          base_url: "http://mock"
          model: "test"
          temperature: 0.0
        tools:
          workspace_root: "./workspace"
        mcp_servers:
          - name: "legacy_server"
            command: "python"
            args: ["-m", "legacy"]
        """
        path = self._create_temp_yaml(content)
        app_cfg = load_config(path)
        self.assertEqual(len(app_cfg.mcp_servers), 1)
        srv = app_cfg.mcp_servers[0]
        self.assertEqual(srv.name, "legacy_server")
        self.assertEqual(srv.transport, "stdio")
        self.assertEqual(srv.command, "python")

    def test_yaml_config_streamable_http(self) -> None:
        """Verify YAML configuration for streamable_http validates and loads."""
        content = """
        agent:
          max_steps: 5
        llm:
          base_url: "http://mock"
          model: "test"
          temperature: 0.0
        tools:
          workspace_root: "./workspace"
        mcp_servers:
          - name: "remote_server"
            transport: "streamable_http"
            url: "https://remote.mcp.example.com/mcp"
            auth_token_env: "REMOTE_TOKEN"
            timeout_seconds: 15.5
        """
        path = self._create_temp_yaml(content)
        app_cfg = load_config(path)
        srv = app_cfg.mcp_servers[0]
        self.assertEqual(srv.name, "remote_server")
        self.assertEqual(srv.transport, "streamable_http")
        self.assertEqual(srv.url, "https://remote.mcp.example.com/mcp")
        self.assertEqual(srv.auth_token_env, "REMOTE_TOKEN")
        self.assertEqual(srv.timeout_seconds, 15.5)

    def test_yaml_config_remote_http_rejected(self) -> None:
        """Verify YAML parser rejects non-local http URLs including testserver."""
        content = """
        agent:
          max_steps: 5
        llm:
          base_url: "http://mock"
          model: "test"
          temperature: 0.0
        tools:
          workspace_root: "./workspace"
        mcp_servers:
          - name: "insecure_server"
            transport: "streamable_http"
            url: "http://insecure.remote.example.com/mcp"
        """
        path = self._create_temp_yaml(content)
        with self.assertRaises(ValueError) as cm:
            load_config(path)
        self.assertIn("must use HTTPS", str(cm.exception))

        # testserver must also be rejected by production YAML config parser
        content_ts = """
        agent:
          max_steps: 5
        llm:
          base_url: "http://mock"
          model: "test"
          temperature: 0.0
        tools:
          workspace_root: "./workspace"
        mcp_servers:
          - name: "testserver_server"
            transport: "streamable_http"
            url: "http://testserver/mcp"
        """
        path_ts = self._create_temp_yaml(content_ts)
        with self.assertRaises(ValueError) as cm_ts:
            load_config(path_ts)
        self.assertIn("must use HTTPS", str(cm_ts.exception))


from mcp.server.transport_security import TransportSecuritySettings


class _LifespanASGIWrapper:
    """Wraps in-process Starlette ASGI app to execute StreamableHTTP session manager lifespan."""

    def __init__(self, app, session_manager):
        self.app = app
        self.session_manager = session_manager

    async def __call__(self, scope, receive, send):
        if self.session_manager._task_group is None:
            self.session_manager._has_started = False
            async with self.session_manager.run():
                await self.app(scope, receive, send)
        else:
            await self.app(scope, receive, send)


class TestStreamableHttpIntegration(unittest.TestCase):
    """Integration test suite executing discovery and calls over Streamable HTTP."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.server = MCPServer("test-http-server")

        @cls.server.tool()
        def http_echo(message: str) -> str:
            """Echoes back a string message."""
            return f"http_echo: {message}"

        @cls.server.tool()
        def http_add(a: int, b: int) -> int:
            """Adds two integers."""
            return a + b

        @cls.server.tool()
        def http_fail(should_fail: bool) -> str:
            """Fails with an error if requested."""
            if should_fail:
                raise ToolError("Invalid argument: [BAD_VALUE] HTTP test failure")
            return "ok"

        @cls.server.tool()
        def http_large() -> str:
            """Returns a large string payload to test observation limits."""
            return "X" * 1000

        sec = TransportSecuritySettings(enable_dns_rebinding_protection=False)
        raw_app = cls.server.streamable_http_app(transport_security=sec, stateless_http=True)
        sm = cls.server._lowlevel_server._session_manager
        cls.asgi_app = _LifespanASGIWrapper(raw_app, sm)

    def _create_asgi_client(
        self,
        auth_token_env: str | None = None,
        expected_token: str | None = None,
    ) -> MCPClient:
        """Create an MCPClient hooked to the in-process ASGI app on loopback 127.0.0.1."""
        headers: dict[str, str] = {}
        if auth_token_env:
            token = os.environ.get(auth_token_env)
            if token:
                headers["Authorization"] = f"Bearer {token}"

        asgi_client = httpx2.AsyncClient(
            transport=httpx2.ASGITransport(app=self.asgi_app),
            base_url="http://127.0.0.1/mcp",
            headers=headers,
        )

        transport = StreamableHttpTransport(
            url="http://127.0.0.1/mcp",
            auth_token_env=auth_token_env,
            http_client=asgi_client,
        )
        cfg = MCPServerConfig(
            name="test_http_server",
            transport="streamable_http",
            url="http://127.0.0.1/mcp",
            auth_token_env=auth_token_env,
        )
        return MCPClient(config=cfg, transport=transport)

    def test_http_tool_discovery(self) -> None:
        """Verify dynamic discovery of tools over Streamable HTTP."""
        client = self._create_asgi_client()
        specs = client.list_tools()
        names = {s.name for s in specs}
        self.assertIn("http_echo", names)
        self.assertIn("http_add", names)
        self.assertIn("http_fail", names)
        self.assertIn("http_large", names)

        echo_spec = next(s for s in specs if s.name == "http_echo")
        self.assertIsInstance(echo_spec, ToolSpec)
        self.assertEqual(echo_spec.name, "http_echo")
        self.assertEqual(echo_spec.input_schema.get("type"), "object")
        self.assertIn("message", echo_spec.input_schema.get("properties", {}))

    def test_http_tool_invocation(self) -> None:
        """Verify successful tool invocation over Streamable HTTP returning ToolResult."""
        client = self._create_asgi_client()
        result = client.call_tool("http_echo", {"message": "hello streamable http"})
        self.assertIsInstance(result, ToolResult)
        self.assertFalse(result.is_error)
        self.assertIsNone(result.error_code)
        self.assertIn("http_echo: hello streamable http", result.content)

        res_add = client.call_tool("http_add", {"a": 25, "b": 17})
        self.assertFalse(res_add.is_error)
        self.assertIn("42", res_add.content)

    def test_http_tool_error_classification(self) -> None:
        """Verify tool error reporting and semantic error code classification."""
        client = self._create_asgi_client()
        result = client.call_tool("http_fail", {"should_fail": True})
        self.assertTrue(result.is_error)
        self.assertEqual(result.error_code, ErrorCode.INVALID_ARGUMENT)
        self.assertIn("HTTP test failure", result.content)

    def test_unreachable_http_endpoint(self) -> None:
        """Verify connection errors to unreachable endpoints are captured as TRANSIENT_ERROR."""
        cfg = MCPServerConfig(
            name="unreachable",
            transport="streamable_http",
            url="http://127.0.0.1:59999/mcp",
            timeout_seconds=2.0,
        )
        client = MCPClient(config=cfg)
        result = client.call_tool("any_tool", {})
        self.assertTrue(result.is_error)
        self.assertEqual(result.error_code, ErrorCode.TRANSIENT_ERROR)
        self.assertIn("transport error", result.content)

    def test_auth_secret_never_leaks_in_errors(self) -> None:
        """Verify auth token is scrubbed from error messages and logs."""
        secret_token = "secret-super-sensitive-token-12345"
        with patch.dict(os.environ, {"MCP_SECRET_VAR": secret_token}):
            cfg = MCPServerConfig(
                name="secret_server",
                transport="streamable_http",
                url="http://127.0.0.1:59999/mcp",
                auth_token_env="MCP_SECRET_VAR",
                timeout_seconds=1.0,
            )
            client = MCPClient(config=cfg)
            result = client.call_tool("any_tool", {})
            self.assertTrue(result.is_error)
            self.assertNotIn(secret_token, result.content)
            self.assertNotIn(secret_token, repr(client.transport))

    def test_tool_executor_validation_and_budget_controls(self) -> None:
        """Verify ToolExecutor schema validation and observation truncation apply to HTTP tools."""
        client = self._create_asgi_client()
        registry = ToolRegistry()
        executor = ToolExecutor(max_observation_chars=100)

        for spec in client.list_tools():
            registry.register(MCPToolAdapter(spec, client))

        # 1. Schema validation applies through ToolExecutor
        tool_echo = registry.get("http_echo")
        self.assertIsNotNone(tool_echo)
        # Pass invalid argument types
        res_invalid = executor.execute(tool_echo, {"message": 12345})  # expected str
        self.assertTrue(res_invalid.is_error)
        self.assertEqual(res_invalid.error_code, ErrorCode.INVALID_ARGUMENT)

        # 2. Observation truncation ceiling applies through ToolExecutor
        tool_large = registry.get("http_large")
        self.assertIsNotNone(tool_large)
        res_large = executor.execute(tool_large, {})
        self.assertFalse(res_large.is_error)
        self.assertLessEqual(len(res_large.content), 100)
        self.assertIn("[OBSERVATION PARTIALLY SHOWN]", res_large.content)

    # --- Docker Compose & Host Validation Tests ---

    def test_streamable_http_transport_trusted_insecure_hosts(self) -> None:
        """Verify StreamableHttpTransport allows trusted_insecure_hosts while rejecting untrusted."""
        # Allowed when explicitly in trusted_insecure_hosts
        t_trusted = StreamableHttpTransport(
            url="http://transport-mcp:8000/mcp",
            trusted_insecure_hosts=["transport-mcp"],
        )
        self.assertEqual(t_trusted.url, "http://transport-mcp:8000/mcp")

        # Rejected when not in trusted_insecure_hosts
        with self.assertRaises(ValueError) as ctx:
            StreamableHttpTransport(
                url="http://transport-mcp:8000/mcp",
                trusted_insecure_hosts=[],
            )
        self.assertIn("must use HTTPS", str(ctx.exception))

    def test_mcp_transport_security_host_validation_semantics(self) -> None:
        """Empirically prove that TransportSecurityMiddleware accepts transport-mcp and rejects attacker hosts."""
        from mcp.server.transport_security import TransportSecuritySettings, TransportSecurityMiddleware

        sec = TransportSecuritySettings(
            enable_dns_rebinding_protection=True,
            allowed_hosts=[
                "transport-mcp:*",
                "transport-mcp",
                "localhost:*",
                "localhost",
                "127.0.0.1:*",
                "127.0.0.1",
            ],
        )
        mw = TransportSecurityMiddleware(sec)

        # Expected trusted hosts (accepted)
        self.assertTrue(mw._validate_host("transport-mcp:8000"), "Host 'transport-mcp:8000' must be accepted")
        self.assertTrue(mw._validate_host("transport-mcp"), "Host 'transport-mcp' must be accepted")
        self.assertTrue(mw._validate_host("localhost:8000"), "Host 'localhost:8000' must be accepted")
        self.assertTrue(mw._validate_host("127.0.0.1:8000"), "Host '127.0.0.1:8000' must be accepted")

        # Untrusted / attacker hosts (strictly rejected)
        self.assertFalse(mw._validate_host("attacker.example:8000"), "Host 'attacker.example:8000' must be rejected")
        self.assertFalse(mw._validate_host("attacker.example"), "Host 'attacker.example' must be rejected")
        self.assertFalse(mw._validate_host("evil.com"), "Host 'evil.com' must be rejected")
        self.assertFalse(mw._validate_host(None), "Missing Host header must be rejected")

    def test_transport_server_main_stdio_backward_compatibility(self) -> None:
        """Verify transport_server main() defaults to stdio transport preserving backward compatibility."""
        from harness.mcp.servers.transport_server import main as server_main, server as transport_mcpserver

        with patch.object(transport_mcpserver, "run") as mock_run:
            server_main([])
            mock_run.assert_called_once_with(transport="stdio")

        with patch.object(transport_mcpserver, "run") as mock_run:
            server_main(["--transport", "stdio"])
            mock_run.assert_called_once_with(transport="stdio")


if __name__ == "__main__":
    unittest.main()
