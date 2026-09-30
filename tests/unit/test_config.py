import os
import tempfile
import unittest
from pathlib import Path

from harness.config import (
    AgentConfig,
    AppConfig,
    LLMConfig,
    ToolsConfig,
    load_config,
)


class TestConfigLoading(unittest.TestCase):
    def setUp(self) -> None:
        self._orig_env = dict(os.environ)
        for key in list(os.environ.keys()):
            if key.startswith("LLM_") or key.startswith("AGENT_HARNESS_"):
                del os.environ[key]

    def tearDown(self) -> None:
        os.environ.clear()
        os.environ.update(self._orig_env)

    def _create_temp_yaml(self, content: str) -> Path:
        """Helper to create a temporary YAML file and register cleanup."""
        temp = tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False)
        temp.write(content)
        temp.close()
        self.addCleanup(lambda: os.path.exists(temp.name) and os.unlink(temp.name))
        return Path(temp.name)

    def test_valid_yaml_loads_correctly(self) -> None:
        content = """
        agent:
          max_steps: 10
        llm:
          base_url: "https://llms.innkube.fim.uni-passau.de"
          model: "qwen-agentworld-35b-a3b"
          temperature: 0.7
        tools:
          workspace_root: "./workspace"
        """
        path = self._create_temp_yaml(content)
        config = load_config(path)

        self.assertIsInstance(config, AppConfig)
        self.assertIsInstance(config.agent, AgentConfig)
        self.assertIsInstance(config.llm, LLMConfig)
        self.assertIsInstance(config.tools, ToolsConfig)
        self.assertEqual(config.agent.max_steps, 10)
        self.assertEqual(config.llm.base_url, "https://llms.innkube.fim.uni-passau.de")
        self.assertEqual(config.llm.model, "qwen-agentworld-35b-a3b")
        self.assertEqual(config.llm.temperature, 0.7)
        self.assertEqual(config.tools.workspace_root, "./workspace")

    # --- Agent Section Tests ---

    def test_missing_agent_section_fails_clearly(self) -> None:
        content = """
        llm:
          base_url: "https://llms.innkube.fim.uni-passau.de"
          model: "qwen-agentworld-35b-a3b"
          temperature: 0.0
        tools:
          workspace_root: "./workspace"
        """
        path = self._create_temp_yaml(content)
        with self.assertRaises(ValueError) as ctx:
            load_config(path)
        self.assertIn("agent", str(ctx.exception).lower())

    def test_missing_max_steps_fails_clearly(self) -> None:
        content = """
        agent: {}
        llm:
          base_url: "https://llms.innkube.fim.uni-passau.de"
          model: "qwen-agentworld-35b-a3b"
          temperature: 0.0
        tools:
          workspace_root: "./workspace"
        """
        path = self._create_temp_yaml(content)
        with self.assertRaises(ValueError) as ctx:
            load_config(path)
        self.assertIn("max_steps", str(ctx.exception).lower())

    def test_string_max_steps_rejected(self) -> None:
        content = """
        agent:
          max_steps: "10"
        llm:
          base_url: "https://llms.innkube.fim.uni-passau.de"
          model: "qwen-agentworld-35b-a3b"
          temperature: 0.0
        tools:
          workspace_root: "./workspace"
        """
        path = self._create_temp_yaml(content)
        with self.assertRaises(ValueError) as ctx:
            load_config(path)
        self.assertIn("max_steps", str(ctx.exception).lower())

    def test_boolean_max_steps_rejected(self) -> None:
        content = """
        agent:
          max_steps: true
        llm:
          base_url: "https://llms.innkube.fim.uni-passau.de"
          model: "qwen-agentworld-35b-a3b"
          temperature: 0.0
        tools:
          workspace_root: "./workspace"
        """
        path = self._create_temp_yaml(content)
        with self.assertRaises(ValueError) as ctx:
            load_config(path)
        self.assertIn("max_steps", str(ctx.exception).lower())

    def test_zero_max_steps_rejected(self) -> None:
        content = """
        agent:
          max_steps: 0
        llm:
          base_url: "https://llms.innkube.fim.uni-passau.de"
          model: "qwen-agentworld-35b-a3b"
          temperature: 0.0
        tools:
          workspace_root: "./workspace"
        """
        path = self._create_temp_yaml(content)
        with self.assertRaises(ValueError) as ctx:
            load_config(path)
        self.assertIn("max_steps", str(ctx.exception).lower())

    def test_negative_max_steps_rejected(self) -> None:
        content = """
        agent:
          max_steps: -5
        llm:
          base_url: "https://llms.innkube.fim.uni-passau.de"
          model: "qwen-agentworld-35b-a3b"
          temperature: 0.0
        tools:
          workspace_root: "./workspace"
        """
        path = self._create_temp_yaml(content)
        with self.assertRaises(ValueError) as ctx:
            load_config(path)
        self.assertIn("max_steps", str(ctx.exception).lower())

    # --- LLM Section Tests ---

    def test_missing_llm_section_fails_clearly(self) -> None:
        content = """
        agent:
          max_steps: 10
        tools:
          workspace_root: "./workspace"
        """
        path = self._create_temp_yaml(content)
        with self.assertRaises(ValueError) as ctx:
            load_config(path)
        self.assertIn("llm", str(ctx.exception).lower())

    def test_missing_llm_model_fails_clearly(self) -> None:
        content = """
        agent:
          max_steps: 10
        llm:
          base_url: "https://llms.innkube.fim.uni-passau.de"
          temperature: 0.0
        tools:
          workspace_root: "./workspace"
        """
        path = self._create_temp_yaml(content)
        with self.assertRaises(ValueError) as ctx:
            load_config(path)
        self.assertIn("model", str(ctx.exception).lower())

    def test_missing_llm_base_url_fails_clearly(self) -> None:
        content = """
        agent:
          max_steps: 10
        llm:
          model: "qwen-agentworld-35b-a3b"
          temperature: 0.0
        tools:
          workspace_root: "./workspace"
        """
        path = self._create_temp_yaml(content)
        with self.assertRaises(ValueError) as ctx:
            load_config(path)
        self.assertIn("base_url", str(ctx.exception).lower())

    def test_missing_llm_temperature_fails_clearly(self) -> None:
        content = """
        agent:
          max_steps: 10
        llm:
          base_url: "https://llms.innkube.fim.uni-passau.de"
          model: "qwen-agentworld-35b-a3b"
        tools:
          workspace_root: "./workspace"
        """
        path = self._create_temp_yaml(content)
        with self.assertRaises(ValueError) as ctx:
            load_config(path)
        self.assertIn("temperature", str(ctx.exception).lower())

    def test_custom_timeout_and_retries_load_correctly(self) -> None:
        content = """
        agent:
          max_steps: 10
        llm:
          base_url: "https://llms.innkube.fim.uni-passau.de"
          model: "qwen-agentworld-35b-a3b"
          temperature: 0.0
          timeout: 45.0
          max_retries: 3
          retry_backoff: 1.0
        tools:
          workspace_root: "./workspace"
        """
        path = self._create_temp_yaml(content)
        config = load_config(path)
        self.assertEqual(config.llm.timeout, 45.0)
        self.assertEqual(config.llm.max_retries, 3)
        self.assertEqual(config.llm.retry_backoff, 1.0)

    def test_invalid_timeout_rejected(self) -> None:
        for bad_val in [0, -10.0, "abc"]:
            content = f"""
            agent:
              max_steps: 10
            llm:
              base_url: "https://llms.innkube.fim.uni-passau.de"
              model: "qwen-agentworld-35b-a3b"
              temperature: 0.0
              timeout: {bad_val}
            tools:
              workspace_root: "./workspace"
            """
            path = self._create_temp_yaml(content)
            with self.assertRaises(ValueError):
                load_config(path)

    def test_invalid_max_retries_rejected(self) -> None:
        for bad_val in [-1, True, "two"]:
            content = f"""
            agent:
              max_steps: 10
            llm:
              base_url: "https://llms.innkube.fim.uni-passau.de"
              model: "qwen-agentworld-35b-a3b"
              temperature: 0.0
              max_retries: {bad_val}
            tools:
              workspace_root: "./workspace"
            """
            path = self._create_temp_yaml(content)
            with self.assertRaises(ValueError):
                load_config(path)

    # --- Tools Section Tests ---

    def test_missing_tools_section_fails_clearly(self) -> None:
        content = """
        agent:
          max_steps: 10
        llm:
          base_url: "https://llms.innkube.fim.uni-passau.de"
          model: "qwen-agentworld-35b-a3b"
          temperature: 0.0
        """
        path = self._create_temp_yaml(content)
        with self.assertRaises(ValueError) as ctx:
            load_config(path)
        self.assertIn("tools", str(ctx.exception).lower())

    def test_missing_tools_workspace_root_fails_clearly(self) -> None:
        content = """
        agent:
          max_steps: 10
        llm:
          base_url: "https://llms.innkube.fim.uni-passau.de"
          model: "qwen-agentworld-35b-a3b"
          temperature: 0.0
        tools: {}
        """
        path = self._create_temp_yaml(content)
        with self.assertRaises(ValueError) as ctx:
            load_config(path)
        self.assertIn("workspace_root", str(ctx.exception).lower())

    def test_empty_tools_workspace_root_fails_clearly(self) -> None:
        content = """
        agent:
          max_steps: 10
        llm:
          base_url: "https://llms.innkube.fim.uni-passau.de"
          model: "qwen-agentworld-35b-a3b"
          temperature: 0.0
        tools:
          workspace_root: "   "
        """
        path = self._create_temp_yaml(content)
        with self.assertRaises(ValueError) as ctx:
            load_config(path)
        self.assertIn("workspace_root", str(ctx.exception).lower())

    def test_null_tools_workspace_root_fails_clearly(self) -> None:
        content = """
        agent:
          max_steps: 10
        llm:
          base_url: "https://llms.innkube.fim.uni-passau.de"
          model: "qwen-agentworld-35b-a3b"
          temperature: 0.0
        tools:
          workspace_root: null
        """
        path = self._create_temp_yaml(content)
        with self.assertRaises(ValueError) as ctx:
            load_config(path)
        self.assertIn("workspace_root", str(ctx.exception).lower())

    def test_non_string_tools_workspace_root_fails_clearly(self) -> None:
        content = """
        agent:
          max_steps: 10
        llm:
          base_url: "https://llms.innkube.fim.uni-passau.de"
          model: "qwen-agentworld-35b-a3b"
          temperature: 0.0
        tools:
          workspace_root: 123
        """
        path = self._create_temp_yaml(content)
        with self.assertRaises(ValueError) as ctx:
            load_config(path)
        self.assertIn("workspace_root", str(ctx.exception).lower())

    # --- YAML Parsing Tests ---

    def test_malformed_yaml_fails_clearly(self) -> None:
        content = """
        agent:
          max_steps: 10
        llm:
          base_url: "https://example.com"
          model: [unclosed list
        """
        path = self._create_temp_yaml(content)
        with self.assertRaises(ValueError) as ctx:
            load_config(path)
        self.assertIn("malformed", str(ctx.exception).lower())

    def test_missing_file_raises_file_not_found(self) -> None:
        with self.assertRaises(FileNotFoundError):
            load_config(Path("non_existent_config_file.yaml"))

    # --- Memory Runtime Configuration Regression Test ---

    def test_default_config_enables_memory(self) -> None:
        """Verify the canonical config file explicitly declares memory state as enabled."""
        config_path = Path("config/config.yaml")
        self.assertTrue(config_path.exists(), "config/config.yaml must exist")
        config = load_config(config_path)

        self.assertTrue(config.memory.enabled, "Canonical runtime config must explicitly enable memory")
        self.assertEqual(config.memory.storage_path, ".agent_memory/memory.db")
        self.assertEqual(config.memory.max_entry_chars, 4000)
        self.assertEqual(config.memory.max_retrieved, 3)
        self.assertEqual(config.memory.max_context_chars, 2000)

    # --- Dual MCP Server Configuration Regression Tests ---

    def test_default_config_declares_dual_mcp_servers(self) -> None:
        """Verify the canonical config file declares both transport and filesystem MCP servers."""
        config_path = Path("config/config.yaml")
        config = load_config(config_path)

        self.assertEqual(len(config.mcp_servers), 2, "Config must declare exactly 2 MCP servers")
        srv_transport = config.mcp_servers[0]
        self.assertEqual(srv_transport.name, "transport_service")
        self.assertIsNone(srv_transport.prefix)

        srv_fs = config.mcp_servers[1]
        self.assertEqual(srv_fs.name, "filesystem_server")
        self.assertEqual(srv_fs.command, "mcp-server-filesystem")
        self.assertEqual(srv_fs.args, ["./workspace/mcp_external_demo"])
        self.assertEqual(srv_fs.prefix, "mcpfs")

    def test_mcp_server_prefix_parsed_successfully(self) -> None:
        """Verify prefix is parsed as stripped string or defaults to None."""
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
          - name: "srv1"
            command: "python"
            args: ["-m", "srv1"]
            prefix: "  mcpfs  "
          - name: "srv2"
            command: "python"
            args: ["-m", "srv2"]
        """
        path = self._create_temp_yaml(content)
        config = load_config(path)
        self.assertEqual(config.mcp_servers[0].prefix, "mcpfs")
        self.assertIsNone(config.mcp_servers[1].prefix)

    def test_mcp_server_prefix_validation(self) -> None:
        """Verify empty strings, whitespace, and non-strings for prefix are rejected."""
        base_yaml = """
        agent:
          max_steps: 5
        llm:
          base_url: "http://mock"
          model: "test"
          temperature: 0.0
        tools:
          workspace_root: "./workspace"
        mcp_servers:
          - name: "srv1"
            command: "python"
            args: ["-m", "srv1"]
            prefix: {bad_val}
        """
        for bad_val in ('""', '"   "', '123', 'true', '["invalid"]'):
            path = self._create_temp_yaml(base_yaml.format(bad_val=bad_val))
            with self.assertRaises(ValueError, msg=f"Should reject bad prefix {bad_val}") as ctx:
                load_config(path)
            self.assertIn("prefix", str(ctx.exception).lower())

    # --- Docker Compose Hybrid Deployment Configuration Tests ---

    def test_docker_compose_config_declares_hybrid_mcp_servers(self) -> None:
        """Verify the Docker Compose config declares Streamable HTTP for transport and stdio for filesystem."""
        config_path = Path("config/config.docker.yaml")
        config = load_config(config_path)

        self.assertEqual(len(config.mcp_servers), 2)
        srv_transport = config.mcp_servers[0]
        self.assertEqual(srv_transport.name, "transport_service")
        self.assertEqual(srv_transport.transport, "streamable_http")
        self.assertEqual(srv_transport.url, "http://transport-mcp:8000/mcp")
        self.assertEqual(srv_transport.trusted_insecure_hosts, ["transport-mcp"])

        srv_fs = config.mcp_servers[1]
        self.assertEqual(srv_fs.name, "filesystem_server")
        self.assertEqual(srv_fs.transport, "stdio")
        self.assertEqual(srv_fs.command, "mcp-server-filesystem")
        self.assertEqual(srv_fs.prefix, "mcpfs")

    def test_mcp_server_trusted_insecure_hosts_parsed_and_enforced(self) -> None:
        """Verify trusted_insecure_hosts permits internal HTTP while rejecting untrusted hosts."""
        trusted_yaml = """
        agent:
          max_steps: 5
        llm:
          base_url: "http://mock"
          model: "test"
          temperature: 0.0
        tools:
          workspace_root: "./workspace"
        mcp_servers:
          - name: "internal_srv"
            transport: "streamable_http"
            url: "http://custom-internal-host:9000/mcp"
            trusted_insecure_hosts:
              - "custom-internal-host"
        """
        path = self._create_temp_yaml(trusted_yaml)
        config = load_config(path)
        self.assertEqual(config.mcp_servers[0].trusted_insecure_hosts, ["custom-internal-host"])

        untrusted_yaml = """
        agent:
          max_steps: 5
        llm:
          base_url: "http://mock"
          model: "test"
          temperature: 0.0
        tools:
          workspace_root: "./workspace"
        mcp_servers:
          - name: "untrusted_srv"
            transport: "streamable_http"
            url: "http://custom-internal-host:9000/mcp"
        """
        path_untrusted = self._create_temp_yaml(untrusted_yaml)
        with self.assertRaises(ValueError) as ctx:
            load_config(path_untrusted)
        self.assertIn("must use https", str(ctx.exception).lower())

    def test_mcp_server_trusted_insecure_hosts_validation(self) -> None:
        """Verify invalid values for trusted_insecure_hosts raise ValueError."""
        base_yaml = """
        agent:
          max_steps: 5
        llm:
          base_url: "http://mock"
          model: "test"
          temperature: 0.0
        tools:
          workspace_root: "./workspace"
        mcp_servers:
          - name: "srv"
            transport: "streamable_http"
            url: "http://localhost:8000/mcp"
            trusted_insecure_hosts: {bad_val}
        """
        for bad_val in ('"not-a-list"', '123', '[""]', '["   "]', '[123]'):
            path = self._create_temp_yaml(base_yaml.format(bad_val=bad_val))
            with self.assertRaises(ValueError, msg=f"Should reject {bad_val}"):
                load_config(path)

    def test_mcp_server_resilience_config_defaults(self) -> None:
        """Verify MCP server config without resilience block gets standard defaults."""
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
          - name: "srv"
            transport: "stdio"
            command: "echo"
        """
        path = self._create_temp_yaml(content)
        cfg = load_config(path)
        server = cfg.mcp_servers[0]
        self.assertEqual(server.resilience.max_retries, 2)
        self.assertEqual(server.resilience.initial_backoff_seconds, 0.5)
        self.assertEqual(server.resilience.max_backoff_seconds, 2.0)
        self.assertEqual(server.resilience.backoff_multiplier, 2.0)
        self.assertEqual(server.resilience.circuit_failure_threshold, 3)
        self.assertEqual(server.resilience.circuit_cooldown_seconds, 30.0)
        self.assertEqual(server.resilience.idempotent_tools, frozenset())

    def test_mcp_server_resilience_config_custom(self) -> None:
        """Verify MCP server config with custom resilience block parses correctly."""
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
          - name: "srv"
            transport: "stdio"
            command: "echo"
            resilience:
              max_retries: 4
              initial_backoff_seconds: 1.0
              max_backoff_seconds: 8.0
              backoff_multiplier: 2.5
              circuit_failure_threshold: 5
              circuit_cooldown_seconds: 45.0
              idempotent_tools:
                - "query_data"
                - "get_info"
        """
        path = self._create_temp_yaml(content)
        cfg = load_config(path)
        server = cfg.mcp_servers[0]
        self.assertEqual(server.resilience.max_retries, 4)
        self.assertEqual(server.resilience.initial_backoff_seconds, 1.0)
        self.assertEqual(server.resilience.max_backoff_seconds, 8.0)
        self.assertEqual(server.resilience.backoff_multiplier, 2.5)
        self.assertEqual(server.resilience.circuit_failure_threshold, 5)
        self.assertEqual(server.resilience.circuit_cooldown_seconds, 45.0)
        self.assertEqual(server.resilience.idempotent_tools, frozenset({"query_data", "get_info"}))

    def test_mcp_server_resilience_config_validation(self) -> None:
        """Verify invalid resilience values raise ValueError with descriptive message."""
        base_yaml = """
        agent:
          max_steps: 5
        llm:
          base_url: "http://mock"
          model: "test"
          temperature: 0.0
        tools:
          workspace_root: "./workspace"
        mcp_servers:
          - name: "srv"
            transport: "stdio"
            command: "echo"
            resilience:
              {bad_field}
        """
        bad_cases = [
            "max_retries: -1",
            "initial_backoff_seconds: -0.5",
            "max_backoff_seconds: 0.2\n              initial_backoff_seconds: 0.5",
            "backoff_multiplier: 0.5",
            "circuit_failure_threshold: 0",
            "circuit_cooldown_seconds: -10.0",
            "idempotent_tools: 'not-a-list'",
            "idempotent_tools: [123]",
        ]
        for bad_field in bad_cases:
            path = self._create_temp_yaml(base_yaml.format(bad_field=bad_field))
            with self.assertRaises(ValueError, msg=f"Should reject {bad_field}"):
                load_config(path)


if __name__ == "__main__":
    unittest.main()
