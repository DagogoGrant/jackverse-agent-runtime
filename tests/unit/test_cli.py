import io
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from harness.cli import build_controller, main
from harness.config import AgentConfig, AppConfig, LLMConfig, ToolsConfig


class TestCLIAndComposition(unittest.TestCase):
    def setUp(self) -> None:
        self._temp_dir = tempfile.TemporaryDirectory()
        self.workspace_dir = Path(self._temp_dir.name) / "custom_workspace"
        self._metrics_patcher = patch("harness.cli.start_metrics_server", return_value=None)
        self._metrics_patcher.start()
        self.config = AppConfig(
            agent=AgentConfig(max_steps=7),
            llm=LLMConfig(
                base_url="https://test-llm.example.com",
                model="test-model-123",
                temperature=0.3,
            ),
            tools=ToolsConfig(
                workspace_root=str(self.workspace_dir),
            ),
        )

    def tearDown(self) -> None:
        self._metrics_patcher.stop()
        self._temp_dir.cleanup()

    def test_build_controller_registers_all_six_tools_and_no_deletion(self) -> None:
        controller, workspace, registry = build_controller(self.config, "fake_key")

        specs = registry.list_specs()
        registered_names = {s.name for s in specs}
        expected_names = {
            "create_directory",
            "create_file",
            "read_file",
            "list_directory",
            "search_files",
            "modify_file",
        }

        self.assertEqual(registered_names, expected_names)
        self.assertNotIn("delete", registered_names)
        self.assertNotIn("delete_file", registered_names)
        self.assertNotIn("delete_directory", registered_names)

    def test_build_controller_wires_workspace_and_creates_dir(self) -> None:
        self.assertFalse(self.workspace_dir.exists())

        controller, workspace, registry = build_controller(self.config, "fake_key")

        self.assertTrue(self.workspace_dir.exists())
        self.assertTrue(self.workspace_dir.is_dir())
        self.assertEqual(workspace.root, self.workspace_dir.resolve())

    def test_build_controller_config_reaches_components(self) -> None:
        controller, workspace, registry = build_controller(self.config, "fake_key")

        self.assertEqual(controller.max_steps, 7)
        self.assertEqual(controller.llm_client.model, "test-model-123")
        self.assertEqual(controller.llm_client.temperature, 0.3)
        self.assertEqual(str(controller.llm_client.client.base_url).rstrip("/"), "https://test-llm.example.com")

    def test_main_missing_api_key_exits(self) -> None:
        with patch("harness.cli.load_dotenv"), patch.dict(os.environ, {}, clear=True):
            stderr_capture = io.StringIO()
            with patch("sys.stderr", stderr_capture):
                with self.assertRaises(SystemExit) as ctx:
                    main()
            self.assertEqual(ctx.exception.code, 1)
            self.assertIn("LLM_API_KEY", stderr_capture.getvalue())

    def test_main_config_loading_error_exits(self) -> None:
        with patch.dict(os.environ, {"INNKUBE_API_KEY": "fake_key", "AGENT_HARNESS_CONFIG": "nonexistent_config.yaml"}):
            stderr_capture = io.StringIO()
            with patch("sys.stderr", stderr_capture):
                with self.assertRaises(SystemExit) as ctx:
                    main()
            self.assertEqual(ctx.exception.code, 1)
            self.assertIn("Error loading configuration", stderr_capture.getvalue())

    def test_main_interactive_loop_exit_and_quit(self) -> None:
        for exit_command in ["exit", "quit", "QUIT", "Exit"]:
            with patch.dict(os.environ, {"INNKUBE_API_KEY": "fake_key"}):
                with patch("harness.cli.load_config", return_value=self.config):
                    with patch("harness.cli.build_controller") as mock_build:
                        mock_controller = MagicMock()
                        mock_registry = MagicMock()
                        mock_registry.list_specs.return_value = []
                        mock_workspace = MagicMock(root=self.workspace_dir)
                        mock_build.return_value = (mock_controller, mock_workspace, mock_registry)

                        with patch("builtins.input", side_effect=[exit_command]):
                            stdout_capture = io.StringIO()
                            with patch("sys.stdout", stdout_capture):
                                main()

                        mock_controller.run.assert_not_called()

    def test_main_interactive_loop_keyboard_interrupt(self) -> None:
        with patch.dict(os.environ, {"INNKUBE_API_KEY": "fake_key"}):
            with patch("harness.cli.load_config", return_value=self.config):
                with patch("harness.cli.build_controller") as mock_build:
                    mock_controller = MagicMock()
                    mock_registry = MagicMock()
                    mock_registry.list_specs.return_value = []
                    mock_workspace = MagicMock(root=self.workspace_dir)
                    mock_build.return_value = (mock_controller, mock_workspace, mock_registry)

                    with patch("builtins.input", side_effect=KeyboardInterrupt):
                        stdout_capture = io.StringIO()
                        with patch("sys.stdout", stdout_capture):
                            main()

                    self.assertIn("Exiting...", stdout_capture.getvalue())
                    mock_controller.run.assert_not_called()

    def test_main_interactive_loop_normal_turn_and_empty_input(self) -> None:
        with patch.dict(os.environ, {"INNKUBE_API_KEY": "fake_key"}):
            with patch("harness.cli.load_config", return_value=self.config):
                with patch("harness.cli.build_controller") as mock_build:
                    mock_controller = MagicMock()
                    mock_controller.run.return_value = "Here is the result."
                    mock_registry = MagicMock()
                    mock_registry.list_specs.return_value = []
                    mock_workspace = MagicMock(root=self.workspace_dir)
                    mock_build.return_value = (mock_controller, mock_workspace, mock_registry)

                    with patch("builtins.input", side_effect=["", "   ", "What is in docs?", "quit"]):
                        stdout_capture = io.StringIO()
                        with patch("sys.stdout", stdout_capture):
                            main()

                    mock_controller.run.assert_called_once_with("What is in docs?")
                    self.assertIn("Agent is working...", stdout_capture.getvalue())
                    self.assertIn("Agent > Here is the result.", stdout_capture.getvalue())

    def test_main_interactive_loop_handles_per_turn_exception(self) -> None:
        with patch.dict(os.environ, {"INNKUBE_API_KEY": "fake_key"}):
            with patch("harness.cli.load_config", return_value=self.config):
                with patch("harness.cli.build_controller") as mock_build:
                    mock_controller = MagicMock()
                    mock_controller.run.side_effect = [
                        RuntimeError("Maximum agent steps reached."),
                        "Recovered next turn.",
                    ]
                    mock_registry = MagicMock()
                    mock_registry.list_specs.return_value = []
                    mock_workspace = MagicMock(root=self.workspace_dir)
                    mock_build.return_value = (mock_controller, mock_workspace, mock_registry)

                    with patch("builtins.input", side_effect=["First prompt", "Second prompt", "exit"]):
                        stdout_capture = io.StringIO()
                        stderr_capture = io.StringIO()
                        with patch("sys.stdout", stdout_capture), patch("sys.stderr", stderr_capture):
                            main()

                    self.assertEqual(mock_controller.run.call_count, 2)
                    self.assertIn("Error during execution: Maximum agent steps reached.", stderr_capture.getvalue())
                    self.assertIn("Agent > Recovered next turn.", stdout_capture.getvalue())

    def test_main_cli_command_help(self) -> None:
        with patch.dict(os.environ, {"INNKUBE_API_KEY": "fake_key"}):
            with patch("harness.cli.load_config", return_value=self.config):
                with patch("harness.cli.build_controller") as mock_build:
                    mock_controller = MagicMock()
                    mock_registry = MagicMock()
                    mock_registry.list_specs.return_value = []
                    mock_workspace = MagicMock(root=self.workspace_dir)
                    mock_build.return_value = (mock_controller, mock_workspace, mock_registry)

                    with patch("builtins.input", side_effect=["/help", "exit"]):
                        stdout_capture = io.StringIO()
                        with patch("sys.stdout", stdout_capture):
                            main()

                    mock_controller.run.assert_not_called()
                    out = stdout_capture.getvalue()
                    self.assertIn("Available commands:", out)
                    self.assertIn("/help", out)
                    self.assertIn("/tools", out)
                    self.assertIn("/config", out)

    def test_main_cli_command_tools(self) -> None:
        from harness.tools.base import ToolSpec
        dummy_specs = [
            ToolSpec(name="create_file", description="create a file", input_schema={}),
            ToolSpec(name="read_file", description="read a file", input_schema={}),
        ]
        with patch.dict(os.environ, {"INNKUBE_API_KEY": "fake_key"}):
            with patch("harness.cli.load_config", return_value=self.config):
                with patch("harness.cli.build_controller") as mock_build:
                    mock_controller = MagicMock()
                    mock_registry = MagicMock()
                    mock_registry.list_specs.return_value = dummy_specs
                    mock_workspace = MagicMock(root=self.workspace_dir)
                    mock_build.return_value = (mock_controller, mock_workspace, mock_registry)

                    with patch("builtins.input", side_effect=["/tools", "exit"]):
                        stdout_capture = io.StringIO()
                        with patch("sys.stdout", stdout_capture):
                            main()

                    mock_controller.run.assert_not_called()
                    out = stdout_capture.getvalue()
                    self.assertIn("Available tools:", out)
                    self.assertIn("- create_file", out)
                    self.assertIn("- read_file", out)

    def test_main_cli_command_config_displays_safe_runtime_values(self) -> None:
        with patch.dict(os.environ, {"INNKUBE_API_KEY": "super_secret_key_12345"}):
            with patch("harness.cli.load_config", return_value=self.config):
                with patch("harness.cli.build_controller") as mock_build:
                    mock_controller = MagicMock()
                    mock_registry = MagicMock()
                    mock_registry.list_specs.return_value = []
                    mock_workspace = MagicMock(root=self.workspace_dir)
                    mock_build.return_value = (mock_controller, mock_workspace, mock_registry)

                    with patch("builtins.input", side_effect=["/config", "exit"]):
                        stdout_capture = io.StringIO()
                        with patch("sys.stdout", stdout_capture):
                            main()

                    mock_controller.run.assert_not_called()
                    out = stdout_capture.getvalue()
                    self.assertIn("Runtime configuration:", out)
                    self.assertIn("Model         : test-model-123", out)
                    self.assertIn("Max Steps     : 7", out)
                    self.assertIn("Timeout       : 30.0s", out)
                    self.assertIn("Max Retries   : 2", out)
                    self.assertIn("Retry Backoff : 0.5s", out)
                    self.assertNotIn("super_secret_key_12345", out)
                    self.assertNotIn("INNKUBE_API_KEY", out)

    def test_build_controller_wires_observation_budget_from_config(self) -> None:
        controller, workspace, registry = build_controller(self.config, "fake_key")
        # Authoritative default observation budget is 16,000
        self.assertEqual(controller.budget.max_observation_chars, 16_000)
        self.assertEqual(controller.tool_executor.max_observation_chars, 16_000)

    def test_mcp_command_lists_server_and_tools(self) -> None:
        from harness.config import MCPServerConfig
        from harness.mcp.adapter import MCPToolAdapter
        from harness.tools.base import ToolSpec

        server_cfg = MCPServerConfig(
            name="transport_service",
            command="python",
            args=["-m", "harness.mcp.servers.transport_server"],
        )
        cfg = AppConfig(
            agent=self.config.agent,
            llm=self.config.llm,
            tools=self.config.tools,
            mcp_servers=[server_cfg],
        )

        mock_spec = ToolSpec(
            name="find_connection",
            description="Discover scheduled train routes and connections",
            input_schema={},
        )
        mock_client = MagicMock()
        mock_client.config = server_cfg
        mock_client.server_name = "transport_service"
        adapter = MCPToolAdapter(mock_spec, mock_client)

        with patch.dict(os.environ, {"INNKUBE_API_KEY": "fake_key"}):
            with patch("harness.cli.load_config", return_value=cfg):
                with patch("harness.cli.build_controller") as mock_build:
                    mock_controller = MagicMock()
                    mock_registry = MagicMock()
                    mock_registry.list_specs.return_value = [mock_spec]
                    mock_registry.get.return_value = adapter
                    mock_workspace = MagicMock(root=self.workspace_dir)
                    mock_build.return_value = (mock_controller, mock_workspace, mock_registry)

                    with patch("builtins.input", side_effect=["/mcp", "exit"]):
                        stdout_capture = io.StringIO()
                        with patch("sys.stdout", stdout_capture):
                            main()

                    out = stdout_capture.getvalue()
                    self.assertIn("MCP Servers (1 configured):", out)
                    self.assertIn("transport_service [stdio]", out)
                    self.assertIn("Discovery : successful", out)
                    self.assertIn("Tools     : 1", out)
                    self.assertIn("find_connection", out)
                    self.assertNotIn("connected", out.lower())
                    self.assertNotIn("fake_key", out)

    def test_mcp_command_redacts_sensitive_target_information(self) -> None:
        from harness.cli import _sanitize_mcp_target
        from harness.config import MCPServerConfig

        # 1. URL with query secrets and basic auth
        url_server = MCPServerConfig(
            name="secret_sse",
            url="https://user:super_secret_pass@api.mcp.example.com/sse?api_key=secret_token_abc&other=1",
        )
        sanitized_url = _sanitize_mcp_target(url_server)
        self.assertNotIn("super_secret_pass", sanitized_url)
        self.assertNotIn("secret_token_abc", sanitized_url)
        self.assertEqual(sanitized_url, "https://api.mcp.example.com/sse")

        # 2. Command with token args and auth_token_env
        with patch.dict(os.environ, {"SECRET_TOKEN_VAR": "very_secret_bearer_999"}):
            cmd_server = MCPServerConfig(
                name="secret_stdio",
                command="npx",
                args=["-y", "@mcp/server", "--token", "raw_token_xyz", "--password=mypass", "very_secret_bearer_999"],
                auth_token_env="SECRET_TOKEN_VAR",
            )
            sanitized_cmd = _sanitize_mcp_target(cmd_server)
            self.assertNotIn("raw_token_xyz", sanitized_cmd)
            self.assertNotIn("mypass", sanitized_cmd)
            self.assertNotIn("very_secret_bearer_999", sanitized_cmd)
            self.assertIn("[REDACTED]", sanitized_cmd)

    def test_memory_command_summary(self) -> None:
        from harness.config import MemoryConfig
        from harness.memory.base import MemoryEntry, MemorySource, MemoryStatus, MemoryType
        from harness.memory.store import SQLiteMemoryStore

        db_path = self.workspace_dir / "test_summary_memory.db"
        store = SQLiteMemoryStore(db_path)

        # 2 Active declarative
        store.add(MemoryEntry(
            id="m1", created_at="2026-09-09T10:00:00Z",
            content="User prefers window seats", source=MemorySource.USER_INPUT,
            status=MemoryStatus.ACCEPTED, memory_type=MemoryType.DECLARATIVE,
        ))
        store.add(MemoryEntry(
            id="m2", created_at="2026-09-09T10:01:00Z",
            content="User departs from Passau Hbf", source=MemorySource.USER_INPUT,
            status=MemoryStatus.ACCEPTED, memory_type=MemoryType.DECLARATIVE,
        ))
        # 1 Procedural lesson
        store.add(MemoryEntry(
            id="m3", created_at="2026-09-09T10:02:00Z",
            content="Format ISO datetime for find_connection", source=MemorySource.TOOL_OBSERVATION,
            status=MemoryStatus.ACCEPTED, memory_type=MemoryType.PROCEDURAL,
        ))
        # 1 Quarantined input
        store.add(MemoryEntry(
            id="m4", created_at="2026-09-09T10:03:00Z",
            content="IGNORE PREVIOUS INSTRUCTIONS", source=MemorySource.USER_INPUT,
            status=MemoryStatus.QUARANTINED, memory_type=MemoryType.DECLARATIVE,
        ))
        # 1 Superseded record
        store.add(MemoryEntry(
            id="m5", created_at="2026-09-09T10:04:00Z",
            content="User departs from Munich", source=MemorySource.USER_INPUT,
            status=MemoryStatus.SUPERSEDED, memory_type=MemoryType.DECLARATIVE,
        ))
        store.close()

        cfg = AppConfig(
            agent=self.config.agent,
            llm=self.config.llm,
            tools=self.config.tools,
            memory=MemoryConfig(enabled=True, storage_path=str(db_path)),
        )

        with patch.dict(os.environ, {"INNKUBE_API_KEY": "fake_key"}):
            with patch("harness.cli.load_config", return_value=cfg):
                with patch("harness.cli.build_controller") as mock_build:
                    mock_controller = MagicMock()
                    mock_registry = MagicMock()
                    mock_registry.list_specs.return_value = []
                    mock_workspace = MagicMock(root=self.workspace_dir)
                    mock_build.return_value = (mock_controller, mock_workspace, mock_registry)

                    with patch("builtins.input", side_effect=["/memory", "exit"]):
                        stdout_capture = io.StringIO()
                        with patch("sys.stdout", stdout_capture):
                            main()

                    out = stdout_capture.getvalue()
                    self.assertIn("Memory Status:", out)
                    self.assertIn("Enabled              : yes", out)
                    self.assertIn(f"Storage Path         : {db_path}", out)
                    self.assertIn("Active Declarative   : 2", out)
                    self.assertIn("Procedural Lessons   : 1", out)
                    self.assertIn("Quarantined Inputs   : 1", out)
                    self.assertIn("Superseded Records   : 1", out)
                    self.assertIn("Total Records        : 5", out)

    def test_memory_command_does_not_print_raw_contents(self) -> None:
        from harness.config import MemoryConfig
        from harness.memory.base import MemoryEntry, MemorySource, MemoryStatus, MemoryType
        from harness.memory.store import SQLiteMemoryStore

        db_path = self.workspace_dir / "secret_memory.db"
        store = SQLiteMemoryStore(db_path)
        secret_payload = "SUPER_SECRET_MEMORY_PAYLOAD_ABC_123"
        store.add(MemoryEntry(
            id="s1", created_at="2026-09-09T10:00:00Z",
            content=secret_payload, source=MemorySource.USER_INPUT,
            status=MemoryStatus.ACCEPTED, memory_type=MemoryType.DECLARATIVE,
        ))
        store.close()

        cfg = AppConfig(
            agent=self.config.agent,
            llm=self.config.llm,
            tools=self.config.tools,
            memory=MemoryConfig(enabled=True, storage_path=str(db_path)),
        )

        with patch.dict(os.environ, {"INNKUBE_API_KEY": "fake_key"}):
            with patch("harness.cli.load_config", return_value=cfg):
                with patch("harness.cli.build_controller") as mock_build:
                    mock_controller = MagicMock()
                    mock_registry = MagicMock()
                    mock_registry.list_specs.return_value = []
                    mock_workspace = MagicMock(root=self.workspace_dir)
                    mock_build.return_value = (mock_controller, mock_workspace, mock_registry)

                    with patch("builtins.input", side_effect=["/memory", "exit"]):
                        stdout_capture = io.StringIO()
                        with patch("sys.stdout", stdout_capture):
                            main()

                    out = stdout_capture.getvalue()
                    self.assertNotIn(secret_payload, out)
                    self.assertIn("Active Declarative   : 1", out)

    def test_memory_command_empty_store(self) -> None:
        from harness.config import MemoryConfig

        non_existent_db = self.workspace_dir / "does_not_exist.db"
        cfg = AppConfig(
            agent=self.config.agent,
            llm=self.config.llm,
            tools=self.config.tools,
            memory=MemoryConfig(enabled=True, storage_path=str(non_existent_db)),
        )

        with patch.dict(os.environ, {"INNKUBE_API_KEY": "fake_key"}):
            with patch("harness.cli.load_config", return_value=cfg):
                with patch("harness.cli.build_controller") as mock_build:
                    mock_controller = MagicMock()
                    mock_registry = MagicMock()
                    mock_registry.list_specs.return_value = []
                    mock_workspace = MagicMock(root=self.workspace_dir)
                    mock_build.return_value = (mock_controller, mock_workspace, mock_registry)

                    with patch("builtins.input", side_effect=["/memory", "exit"]):
                        stdout_capture = io.StringIO()
                        with patch("sys.stdout", stdout_capture):
                            main()

                    out = stdout_capture.getvalue()
                    self.assertIn("Memory Status:", out)
                    self.assertIn("Enabled              : yes", out)
                    self.assertIn("Active Declarative   : 0", out)
                    self.assertIn("Total Records        : 0", out)

    def test_memory_command_disabled(self) -> None:
        from harness.config import MemoryConfig

        cfg = AppConfig(
            agent=self.config.agent,
            llm=self.config.llm,
            tools=self.config.tools,
            memory=MemoryConfig(enabled=False),
        )

        with patch.dict(os.environ, {"INNKUBE_API_KEY": "fake_key"}):
            with patch("harness.cli.load_config", return_value=cfg):
                with patch("harness.cli.build_controller") as mock_build:
                    mock_controller = MagicMock()
                    mock_registry = MagicMock()
                    mock_registry.list_specs.return_value = []
                    mock_workspace = MagicMock(root=self.workspace_dir)
                    mock_build.return_value = (mock_controller, mock_workspace, mock_registry)

                    with patch("builtins.input", side_effect=["/memory", "exit"]):
                        stdout_capture = io.StringIO()
                        with patch("sys.stdout", stdout_capture):
                            main()

                    out = stdout_capture.getvalue()
                    self.assertIn("Memory Status:", out)
                    self.assertIn("Enabled              : no", out)
                    self.assertNotIn("Storage Path", out)

    def test_banner_shows_memory_and_mcp_status(self) -> None:
        from harness.cli import print_banner
        from harness.config import MCPServerConfig, MemoryConfig

        cfg = AppConfig(
            agent=self.config.agent,
            llm=self.config.llm,
            tools=self.config.tools,
            memory=MemoryConfig(enabled=True, storage_path=".agent_memory/memory.db"),
            mcp_servers=[MCPServerConfig(name="srv1", command="echo")],
        )
        registry = MagicMock()
        registry.list_specs.return_value = ["t1", "t2", "t3"]
        workspace = MagicMock(root=self.workspace_dir)

        stdout_capture = io.StringIO()
        with patch("sys.stdout", stdout_capture):
            print_banner(cfg, workspace, registry)

        out = stdout_capture.getvalue()
        self.assertIn("Memory      : enabled (.agent_memory/memory.db)", out)
        self.assertIn("MCP Servers : 1 configured", out)
        self.assertIn("Tools       : 3 available", out)
        self.assertIn("/mcp", out)
        self.assertIn("/memory", out)

    def test_config_shows_memory_and_mcp_status(self) -> None:
        from harness.cli import print_config
        from harness.config import MCPServerConfig, MemoryConfig

        cfg = AppConfig(
            agent=self.config.agent,
            llm=self.config.llm,
            tools=self.config.tools,
            memory=MemoryConfig(enabled=True, storage_path=".agent_memory/custom.db"),
            mcp_servers=[MCPServerConfig(name="srv1", command="echo"), MCPServerConfig(name="srv2", command="echo")],
        )
        workspace = MagicMock(root=self.workspace_dir)

        stdout_capture = io.StringIO()
        with patch("sys.stdout", stdout_capture):
            print_config(cfg, workspace)

        out = stdout_capture.getvalue()
        self.assertIn("Memory        : enabled", out)
        self.assertIn("Memory Path   : .agent_memory/custom.db", out)
        self.assertIn("MCP Servers   : 2", out)

    def test_memory_command_lifecycle_freshness_excludes_expired_records(self) -> None:
        """Verify /memory Active Declarative and Procedural Lessons strictly exclude expired accepted rows."""
        from harness.config import MemoryConfig
        from harness.memory.base import MemoryEntry, MemorySource, MemoryStatus, MemoryType
        from harness.memory.store import SQLiteMemoryStore

        db_path = self.workspace_dir / "expiry_test_memory.db"
        store = SQLiteMemoryStore(db_path)

        # 1. Fresh accepted declarative (future expiry)
        store.add(MemoryEntry(
            id="d_fresh_1", created_at="2026-09-09T10:00:00Z",
            content="Fresh preference with future expiry", source=MemorySource.USER_INPUT,
            status=MemoryStatus.ACCEPTED, memory_type=MemoryType.DECLARATIVE,
            expires_at="2099-01-01T00:00:00Z",
        ))
        # 2. Fresh accepted declarative (no expiry)
        store.add(MemoryEntry(
            id="d_fresh_2", created_at="2026-09-09T10:01:00Z",
            content="Fresh preference without expiry", source=MemorySource.USER_INPUT,
            status=MemoryStatus.ACCEPTED, memory_type=MemoryType.DECLARATIVE,
            expires_at=None,
        ))
        # 3. Expired accepted declarative (past expiry)
        store.add(MemoryEntry(
            id="d_expired", created_at="2020-01-01T10:00:00Z",
            content="Old stale preference expired in 2020", source=MemorySource.USER_INPUT,
            status=MemoryStatus.ACCEPTED, memory_type=MemoryType.DECLARATIVE,
            expires_at="2020-01-02T00:00:00Z",
        ))

        # 4. Fresh accepted procedural (future expiry)
        store.add(MemoryEntry(
            id="p_fresh", created_at="2026-09-09T10:02:00Z",
            content="Active procedural recovery heuristic", source=MemorySource.TOOL_OBSERVATION,
            status=MemoryStatus.ACCEPTED, memory_type=MemoryType.PROCEDURAL,
            expires_at="2099-01-01T00:00:00Z",
        ))
        # 5. Expired accepted procedural (past expiry)
        store.add(MemoryEntry(
            id="p_expired", created_at="2020-01-01T10:00:00Z",
            content="Obsolete procedural rule expired in 2020", source=MemorySource.TOOL_OBSERVATION,
            status=MemoryStatus.ACCEPTED, memory_type=MemoryType.PROCEDURAL,
            expires_at="2020-01-02T00:00:00Z",
        ))
        store.close()

        cfg = AppConfig(
            agent=self.config.agent,
            llm=self.config.llm,
            tools=self.config.tools,
            memory=MemoryConfig(enabled=True, storage_path=str(db_path)),
        )

        with patch.dict(os.environ, {"INNKUBE_API_KEY": "fake_key"}):
            with patch("harness.cli.load_config", return_value=cfg):
                with patch("harness.cli.build_controller") as mock_build:
                    mock_controller = MagicMock()
                    mock_registry = MagicMock()
                    mock_registry.list_specs.return_value = []
                    mock_workspace = MagicMock(root=self.workspace_dir)
                    mock_build.return_value = (mock_controller, mock_workspace, mock_registry)

                    with patch("builtins.input", side_effect=["/memory", "exit"]):
                        stdout_capture = io.StringIO()
                        with patch("sys.stdout", stdout_capture):
                            main()

                    out = stdout_capture.getvalue()
                    self.assertIn("Active Declarative   : 2", out)
                    self.assertNotIn("Active Declarative   : 3", out)
                    self.assertIn("Procedural Lessons   : 1", out)
                    self.assertNotIn("Procedural Lessons   : 2", out)
                    self.assertIn("Total Records        : 5", out)

    def test_build_controller_applies_mcp_prefix_to_prevent_tool_name_collisions(self) -> None:
        """Verify that prefix avoids DuplicateToolError when external MCP duplicates native tool name."""
        from harness.config import MCPServerConfig
        from harness.tools.base import ToolSpec
        from harness.tools.registry import DuplicateToolError

        # 1. Without prefix: tool name 'read_file' collides with native ReadFileTool
        mcp_cfg_unprefixed = MCPServerConfig(name="colliding_server", command="echo", prefix=None)
        cfg_unprefixed = AppConfig(
            agent=self.config.agent,
            llm=self.config.llm,
            tools=self.config.tools,
            mcp_servers=[mcp_cfg_unprefixed],
        )

        fake_spec = ToolSpec(name="read_file", description="External read file", input_schema={})

        with patch("harness.cli.MCPClient") as mock_client_cls:
            mock_client = MagicMock()
            mock_client.list_tools.return_value = [fake_spec]
            mock_client_cls.return_value = mock_client

            with self.assertRaises(DuplicateToolError):
                build_controller(cfg_unprefixed, "fake_key")

        # 2. With prefix="mcpfs": tool name becomes 'mcpfs_read_file', coexisting cleanly
        mcp_cfg_prefixed = MCPServerConfig(name="namespaced_server", command="echo", prefix="mcpfs")
        cfg_prefixed = AppConfig(
            agent=self.config.agent,
            llm=self.config.llm,
            tools=self.config.tools,
            mcp_servers=[mcp_cfg_prefixed],
        )

        with patch("harness.cli.MCPClient") as mock_client_cls:
            mock_client = MagicMock()
            mock_client.list_tools.return_value = [fake_spec]
            mock_client_cls.return_value = mock_client

            controller, ws, registry = build_controller(cfg_prefixed, "fake_key")
            spec_names = [s.name for s in registry.list_specs()]
            self.assertIn("read_file", spec_names, "Native read_file must be present")
            self.assertIn("mcpfs_read_file", spec_names, "Namespaced mcpfs_read_file must be present")


if __name__ == "__main__":
    unittest.main()
