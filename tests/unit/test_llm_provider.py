from dataclasses import replace
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch

from openai import (
    APIConnectionError,
    APITimeoutError,
    AuthenticationError,
    InternalServerError,
)

from harness.config import AppConfig, ConfigurationError, LLMConfig, load_config
from harness.llm.base import (
    LLMAuthenticationError,
    LLMConnectionError,
    LLMError,
    LLMProvider,
    LLMResponse,
    ProviderCapabilityError,
    ToolCall,
)
from harness.llm.doctor import DiagnosticReport, run_llm_diagnostics
from harness.llm.factory import create_llm_provider, register_provider
from harness.llm.openai_compatible import OpenAICompatibleProvider
from harness.tools.base import ToolSpec


class DummyCustomProvider:
    """Mock provider for factory registry testing."""

    def __init__(self, config: object = None, event_bus: object = None, **kwargs: object) -> None:
        self.config = config
        self.event_bus = event_bus
        self.kwargs = kwargs

    def chat(self, messages: list[dict[str, object]], tools: list[ToolSpec] | None = None) -> LLMResponse:
        return LLMResponse(content="dummy response")


class TestLLMProvider(unittest.TestCase):
    """Exhaustive tests covering all 18 provider abstraction and configuration requirements."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.yaml_path = Path(self.temp_dir.name) / "test_config.yaml"
        self.yaml_path.write_text(
            """
agent:
  max_steps: 10
llm:
  provider: openai_compatible
  base_url: https://yaml-base.example.com/v1
  model: yaml-model-v1
  api_key: yaml_api_key_123
  temperature: 0.1
  timeout: 45.0
  max_retries: 3
  extra_headers:
    X-Source: yaml
tools:
  workspace_root: ./workspace
"""
        )

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    # 1. Environment configuration overrides YAML
    def test_environment_configuration_overrides_yaml(self) -> None:
        env = {
            "LLM_PROVIDER": "openai_compatible",
            "LLM_BASE_URL": "https://env-base.example.com/v1",
            "LLM_MODEL": "env-model-v2",
            "LLM_API_KEY": "env_api_key_456",
            "LLM_TEMPERATURE": "0.7",
            "LLM_TIMEOUT_SECONDS": "60.0",
            "LLM_MAX_RETRIES": "5",
            "LLM_EXTRA_HEADERS": "{\"X-Source\": \"env\", \"X-Custom\": \"true\"}",
        }
        with patch.dict(os.environ, env, clear=True):
            cfg = load_config(self.yaml_path)

        self.assertEqual(cfg.llm.provider, "openai_compatible")
        self.assertEqual(cfg.llm.base_url, "https://env-base.example.com/v1")
        self.assertEqual(cfg.llm.model, "env-model-v2")
        self.assertEqual(cfg.llm.api_key, "env_api_key_456")
        self.assertEqual(cfg.llm.temperature, 0.7)
        self.assertEqual(cfg.llm.timeout, 60.0)
        self.assertEqual(cfg.llm.max_retries, 5)
        self.assertEqual(cfg.llm.extra_headers, {"X-Source": "env", "X-Custom": "true"})

    # 2. Custom base URL
    def test_custom_base_url(self) -> None:
        provider = OpenAICompatibleProvider(
            base_url="https://api.together.xyz/v1",
            model="mistralai/mixtral-8x7b",
            api_key="together_key",
        )
        self.assertEqual(provider.base_url, "https://api.together.xyz/v1")
        self.assertEqual(str(provider.client.base_url).rstrip("/"), "https://api.together.xyz/v1")

    # 3. Custom model
    def test_custom_model(self) -> None:
        provider = OpenAICompatibleProvider(
            base_url="https://api.openai.com/v1",
            model="llama-3.3-70b-instruct",
            api_key="test_key",
        )
        self.assertEqual(provider.model, "llama-3.3-70b-instruct")

    # 4. Custom API key
    def test_custom_api_key(self) -> None:
        provider = OpenAICompatibleProvider(
            base_url="https://api.openai.com/v1",
            model="gpt-4o",
            api_key="sk-live-secret-test-key",
        )
        self.assertEqual(provider.api_key, "sk-live-secret-test-key")
        self.assertEqual(provider.client.api_key, "sk-live-secret-test-key")

    # 5. Missing API key for local endpoint
    def test_missing_api_key_for_local_endpoint(self) -> None:
        provider = OpenAICompatibleProvider(
            base_url="http://localhost:11434/v1",
            model="llama3",
            api_key=None,
        )
        self.assertIsNone(provider.api_key)
        # Client initialized with "none" placeholder so OpenAI client does not throw MissingKey error
        self.assertEqual(provider.client.api_key, "none")

    # 6. Malformed base URL
    def test_malformed_base_url_raises_configuration_error(self) -> None:
        with patch.dict(os.environ, {"LLM_BASE_URL": "not-a-valid-url"}, clear=True):
            with self.assertRaises(ConfigurationError) as ctx:
                load_config(self.yaml_path)
            self.assertIn("llm.base_url", str(ctx.exception))
            self.assertIn("http://", str(ctx.exception))

    # 7. Malformed LLM_EXTRA_HEADERS
    def test_malformed_llm_extra_headers_raises_configuration_error(self) -> None:
        with patch.dict(os.environ, {"LLM_EXTRA_HEADERS": "{bad json"}, clear=True):
            with self.assertRaises(ConfigurationError) as ctx:
                load_config(self.yaml_path)
            self.assertIn("LLM_EXTRA_HEADERS", str(ctx.exception))

    # 8. Custom headers parsed correctly
    def test_custom_headers_parsed_correctly(self) -> None:
        headers = {"HTTP-Referer": "https://jackverse.dev", "X-Title": "JackVerse"}
        provider = OpenAICompatibleProvider(
            base_url="https://openrouter.ai/api/v1",
            model="anthropic/claude-3.5-sonnet",
            api_key="sk-or-123",
            extra_headers=headers,
        )
        self.assertEqual(provider.extra_headers, headers)
        self.assertEqual(provider.client.default_headers.get("HTTP-Referer"), "https://jackverse.dev")
        self.assertEqual(provider.client.default_headers.get("X-Title"), "JackVerse")

    # 9. Secrets are not logged or exposed
    def test_secrets_are_not_logged_or_exposed(self) -> None:
        raw_secret = "sk-live-super-secret-1234567890"
        provider = OpenAICompatibleProvider(
            base_url="https://api.openai.com/v1",
            model="gpt-4o",
            api_key=raw_secret,
        )
        rep = repr(provider)
        self.assertNotIn(raw_secret, rep)
        self.assertIn("authenticated=yes", rep)

        # Also verify doctor diagnostic report masks secrets
        cfg = LLMConfig(
            provider="openai_compatible",
            base_url="https://api.openai.com/v1",
            model="gpt-4o",
            temperature=0.0,
            api_key=raw_secret,
        )
        report = run_llm_diagnostics(cfg)
        rendered = report.render()
        self.assertNotIn(raw_secret, rendered)
        self.assertIn("Authentication : configured (redacted)", rendered)
        self.assertIn("API key present (redacted)", rendered)

    # 10. Provider factory returns correct provider
    def test_provider_factory_returns_correct_provider(self) -> None:
        cfg = LLMConfig(
            provider="openai_compatible",
            base_url="https://api.openai.com/v1",
            model="gpt-4.1-mini",
            temperature=0.0,
            api_key="fake-key",
        )
        provider = create_llm_provider(cfg)
        self.assertIsInstance(provider, OpenAICompatibleProvider)
        self.assertIsInstance(provider, LLMProvider)

        # Test custom registry extension
        register_provider("mock_custom", DummyCustomProvider)
        custom_cfg = replace(cfg, provider="mock_custom")
        custom_prov = create_llm_provider(custom_cfg)
        self.assertIsInstance(custom_prov, DummyCustomProvider)

        # Test unknown provider raises ConfigurationError
        bad_cfg = replace(cfg, provider="unsupported_provider_xyz")
        with self.assertRaises(ConfigurationError) as ctx:
            create_llm_provider(bad_cfg)
        self.assertIn("Unsupported LLM provider", str(ctx.exception))

    # 11. Configured model is passed to requests
    def test_configured_model_is_passed_to_requests(self) -> None:
        provider = OpenAICompatibleProvider(
            base_url="https://api.openai.com/v1",
            model="custom-specialized-model",
            api_key="fake-key",
        )
        mock_choice = SimpleNamespace(
            message=SimpleNamespace(content="Hello world", tool_calls=None),
            finish_reason="stop",
        )
        mock_create = MagicMock(return_value=SimpleNamespace(choices=[mock_choice]))
        provider.client.chat.completions.create = mock_create

        res = provider.chat([{"role": "user", "content": "Hi"}])
        self.assertEqual(res.content, "Hello world")
        mock_create.assert_called_once()
        _, kwargs = mock_create.call_args
        self.assertEqual(kwargs.get("model"), "custom-specialized-model")

    # 12. Configured base URL is actually used
    def test_configured_base_url_is_actually_used(self) -> None:
        target_url = "https://custom-gateway.corp.internal/v1"
        provider = OpenAICompatibleProvider(
            base_url=target_url,
            model="gpt-4o",
            api_key="corp-key",
        )
        self.assertEqual(str(provider.client.base_url).rstrip("/"), target_url)

    # 13. Structured tool calls continue working
    def test_structured_tool_calls_continue_working(self) -> None:
        provider = OpenAICompatibleProvider(
            base_url="https://api.openai.com/v1",
            model="gpt-4o",
            api_key="fake-key",
        )
        mock_tool_call = SimpleNamespace(
            id="call_abc_123",
            function=SimpleNamespace(
                name="read_file",
                arguments=json.dumps({"path": "src/main.py"}),
            ),
        )
        mock_choice = SimpleNamespace(
            message=SimpleNamespace(
                content="Inspecting workspace...",
                tool_calls=[mock_tool_call],
            ),
            finish_reason="tool_calls",
        )
        mock_create = MagicMock(return_value=SimpleNamespace(choices=[mock_choice]))
        provider.client.chat.completions.create = mock_create

        spec = ToolSpec(
            name="read_file",
            description="Read a file",
            input_schema={"type": "object", "properties": {"path": {"type": "string"}}},
        )
        res = provider.chat([{"role": "user", "content": "Read file"}], tools=[spec])

        self.assertEqual(res.content, "Inspecting workspace...")
        self.assertIsNotNone(res.tool_calls)
        self.assertEqual(len(res.tool_calls or []), 1)
        tc = (res.tool_calls or [])[0]
        self.assertEqual(tc.id, "call_abc_123")
        self.assertEqual(tc.name, "read_file")
        self.assertEqual(tc.arguments, {"path": "src/main.py"})

    # 14. Authentication failure is normalized
    def test_authentication_failure_is_normalized(self) -> None:
        provider = OpenAICompatibleProvider(
            base_url="https://api.openai.com/v1",
            model="gpt-4o",
            api_key="invalid_key",
        )
        fake_response = MagicMock(status_code=401, headers={})
        mock_create = MagicMock(
            side_effect=AuthenticationError(
                message="Incorrect API key provided",
                response=fake_response,
                body={"error": {"message": "Incorrect API key provided"}},
            )
        )
        provider.client.chat.completions.create = mock_create

        with self.assertRaises(LLMAuthenticationError) as ctx:
            provider.chat([{"role": "user", "content": "Hi"}])
        self.assertIsInstance(ctx.exception, LLMError)
        self.assertIn("Authentication failed", str(ctx.exception))

    # 15. Connection failure is normalized
    def test_connection_failure_is_normalized(self) -> None:
        sleeps: list[float] = []
        provider = OpenAICompatibleProvider(
            base_url="https://api.openai.com/v1",
            model="gpt-4o",
            api_key="fake-key",
            max_retries=1,
            retry_backoff=0.1,
            sleep_fn=sleeps.append,
        )
        fake_request = MagicMock()
        mock_create = MagicMock(side_effect=APIConnectionError(request=fake_request))
        provider.client.chat.completions.create = mock_create

        with self.assertRaises(LLMConnectionError) as ctx:
            provider.chat([{"role": "user", "content": "Hi"}])
        self.assertIsInstance(ctx.exception, LLMError)
        self.assertIn("connection request failed", str(ctx.exception).lower())
        self.assertEqual(mock_create.call_count, 2)

    # 16. Timeout behavior remains bounded
    def test_timeout_behavior_remains_bounded(self) -> None:
        sleeps: list[float] = []
        provider = OpenAICompatibleProvider(
            base_url="https://api.openai.com/v1",
            model="gpt-4o",
            api_key="fake-key",
            timeout=10.0,
            max_retries=2,
            retry_backoff=0.2,
            sleep_fn=sleeps.append,
        )
        fake_request = MagicMock()
        mock_create = MagicMock(side_effect=APITimeoutError(request=fake_request))
        provider.client.chat.completions.create = mock_create

        with self.assertRaises(LLMConnectionError) as ctx:
            provider.chat([{"role": "user", "content": "Hi"}])
        self.assertIsInstance(ctx.exception, LLMError)
        self.assertIn("Request timed out", str(ctx.exception))
        # 1 initial + 2 retries = 3 attempts total
        self.assertEqual(mock_create.call_count, 3)
        self.assertEqual(sleeps, [0.2, 0.4])

    # 17. Capability errors for malformed tool call arguments
    def test_capability_error_on_malformed_tool_call_arguments(self) -> None:
        provider = OpenAICompatibleProvider(
            base_url="https://api.openai.com/v1",
            model="gpt-4o",
            api_key="fake-key",
        )
        mock_tool_call = SimpleNamespace(
            id="call_bad_json",
            function=SimpleNamespace(
                name="read_file",
                arguments="NOT_A_VALID_JSON{",
            ),
        )
        mock_choice = SimpleNamespace(
            message=SimpleNamespace(
                content="Executing...",
                tool_calls=[mock_tool_call],
            ),
            finish_reason="tool_calls",
        )
        mock_create = MagicMock(return_value=SimpleNamespace(choices=[mock_choice]))
        provider.client.chat.completions.create = mock_create

        with self.assertRaises(ProviderCapabilityError) as ctx:
            provider.chat([{"role": "user", "content": "Read file"}])
        self.assertIsInstance(ctx.exception, LLMError)
        self.assertIn("failed to parse arguments JSON", str(ctx.exception))

    # 18. Doctor diagnostics checks
    def test_doctor_diagnostics_report(self) -> None:
        # Scenario A: Local Ollama endpoint without API key (valid)
        ollama_cfg = LLMConfig(
            provider="openai_compatible",
            base_url="http://localhost:11434/v1",
            model="llama3",
            temperature=0.0,
            api_key=None,
        )
        rep = run_llm_diagnostics(ollama_cfg)
        self.assertTrue(rep.checks[0].passed)  # Provider registered
        self.assertTrue(rep.checks[1].passed)  # URL syntax valid
        self.assertTrue(rep.checks[2].passed)  # Auth check (local endpoint doesn't require key)
        self.assertTrue(rep.checks[3].passed)  # Model specified

        # Scenario B: Remote endpoint missing API key
        remote_no_key_cfg = LLMConfig(
            provider="openai_compatible",
            base_url="https://api.openai.com/v1",
            model="gpt-4o",
            temperature=0.0,
            api_key=None,
        )
        rep2 = run_llm_diagnostics(remote_no_key_cfg)
        self.assertFalse(rep2.all_passed)
        # Authentication check is check 3 (index 3: Configuration, Base URL Format, Provider Factory, Authentication)
        auth_check = next(c for c in rep2.checks if c.name == "Authentication")
        self.assertFalse(auth_check.passed)
        self.assertIn("not configured for remote endpoint", auth_check.details)

        # Scenario C: Diagnostic report rendering
        rendered = rep.render()
        self.assertIn("JackVerse LLM Provider Diagnostics", rendered)
        self.assertIn("http://localhost:11434/v1", rendered)


class TestDoctorAndLegacyCleanup(unittest.TestCase):
    """Targeted tests verifying legacy InnKube removal, clean domain errors, and live doctor capabilities."""

    # 1. InnKube fallback completely removed
    def test_innkube_fallback_completely_removed(self) -> None:
        from harness.cli import main
        import io

        # Scenario A: config.py does not read INNKUBE_API_KEY
        with tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False) as f:
            f.write("""
agent:
  max_steps: 5
llm:
  provider: openai_compatible
  base_url: https://api.openai.com/v1
  model: gpt-4.1-mini
  temperature: 0.0
tools:
  workspace_root: ./workspace
""")
            cfg_path = Path(f.name)

        try:
            with patch.dict(os.environ, {"INNKUBE_API_KEY": "innkube_val_123"}, clear=True):
                cfg = load_config(cfg_path)
                # Must be None, not "innkube_val_123"
                self.assertIsNone(cfg.llm.api_key)

            # Scenario B: cli.py main exits with missing LLM_API_KEY even if INNKUBE_API_KEY is in env
            with patch.dict(os.environ, {"INNKUBE_API_KEY": "innkube_val_123", "AGENT_HARNESS_CONFIG": str(cfg_path)}, clear=True):
                stderr_capture = io.StringIO()
                with patch("sys.stderr", stderr_capture), patch("harness.cli.load_dotenv"):
                    with self.assertRaises(SystemExit) as ctx:
                        main()
                self.assertEqual(ctx.exception.code, 1)
                self.assertIn("LLM_API_KEY environment variable is not set", stderr_capture.getvalue())
                self.assertNotIn("INNKUBE_API_KEY", stderr_capture.getvalue())
        finally:
            if os.path.exists(cfg_path):
                os.unlink(cfg_path)

    # 2. Domain exceptions contain no unittest/mock dependency
    def test_domain_exceptions_contain_no_unittest_or_mock_dependency(self) -> None:
        base_path = Path(__file__).resolve().parent.parent.parent / "src" / "harness" / "llm" / "base.py"
        base_code = base_path.read_text(encoding="utf-8")

        self.assertNotIn("unittest", base_code)
        self.assertNotIn("mock", base_code)
        self.assertNotIn("MagicMock", base_code)

        # Verify hierarchy
        auth_err = LLMAuthenticationError("unauthorized")
        conn_err = LLMConnectionError("unreachable")
        cap_err = ProviderCapabilityError("no tool support")

        for err in (auth_err, conn_err, cap_err):
            self.assertIsInstance(err, LLMError)
            self.assertIsInstance(err, RuntimeError)

    # 3. Doctor configuration-only mode
    def test_doctor_configuration_only_mode(self) -> None:
        cfg = LLMConfig(
            provider="openai_compatible",
            base_url="https://api.openai.com/v1",
            model="gpt-4.1-mini",
            temperature=0.0,
            api_key="sk-test-key",
        )
        mock_provider = MagicMock()
        report = run_llm_diagnostics(cfg, live=False, provider=mock_provider)

        self.assertFalse(report.live)
        self.assertTrue(report.all_passed)
        # In non-live mode, mock_provider.chat is never called
        mock_provider.chat.assert_not_called()
        self.assertEqual(len(report.checks), 4)

    # 4. Doctor live successful response
    def test_doctor_live_successful_response(self) -> None:
        cfg = LLMConfig(
            provider="openai_compatible",
            base_url="https://api.openai.com/v1",
            model="gpt-4.1-mini",
            temperature=0.0,
            api_key="sk-test-key",
        )
        # Mock responses: 1st for ping (content), 2nd for health_check (tool_calls)
        tool_call = ToolCall(id="call_1", name="health_check", arguments={"message": "ping"})
        mock_provider = MagicMock()
        mock_provider.chat.side_effect = [
            LLMResponse(content="pong"),
            LLMResponse(content=None, tool_calls=[tool_call]),
        ]

        report = run_llm_diagnostics(cfg, live=True, provider=mock_provider)

        self.assertTrue(report.live)
        self.assertTrue(report.all_passed)
        names = [c.name for c in report.checks]
        self.assertIn("Endpoint Reachable", names)
        self.assertIn("Authentication Accepted", names)
        self.assertIn("Model Response", names)
        self.assertIn("Structured Tool Calling", names)
        self.assertEqual(mock_provider.chat.call_count, 2)

    # 5. Doctor live authentication failure
    def test_doctor_live_authentication_failure(self) -> None:
        cfg = LLMConfig(
            provider="openai_compatible",
            base_url="https://api.openai.com/v1",
            model="gpt-4.1-mini",
            temperature=0.0,
            api_key="sk-invalid-key",
        )
        mock_provider = MagicMock()
        mock_provider.chat.side_effect = LLMAuthenticationError("401 Unauthorized: Invalid API key")

        report = run_llm_diagnostics(cfg, live=True, provider=mock_provider)

        self.assertFalse(report.all_passed)
        auth_check = next(c for c in report.checks if c.name == "Authentication Accepted")
        self.assertFalse(auth_check.passed)
        self.assertIn("rejected", auth_check.details.lower())

    # 6. Doctor live connection failure
    def test_doctor_live_connection_failure(self) -> None:
        cfg = LLMConfig(
            provider="openai_compatible",
            base_url="https://api.openai.com/v1",
            model="gpt-4.1-mini",
            temperature=0.0,
            api_key="sk-test-key",
        )
        mock_provider = MagicMock()
        mock_provider.chat.side_effect = LLMConnectionError("Connection timed out to api.openai.com")

        report = run_llm_diagnostics(cfg, live=True, provider=mock_provider)

        self.assertFalse(report.all_passed)
        conn_check = next(c for c in report.checks if c.name == "Endpoint Reachable")
        self.assertFalse(conn_check.passed)
        self.assertIn("failed", conn_check.details.lower())

    # 7. Doctor live nonexistent model
    def test_doctor_live_nonexistent_model(self) -> None:
        cfg = LLMConfig(
            provider="openai_compatible",
            base_url="https://api.openai.com/v1",
            model="nonexistent-model-xyz",
            temperature=0.0,
            api_key="sk-test-key",
        )
        mock_provider = MagicMock()
        mock_provider.chat.side_effect = LLMError("Model 'nonexistent-model-xyz' not found (404)")

        report = run_llm_diagnostics(cfg, live=True, provider=mock_provider)

        self.assertFalse(report.all_passed)
        model_check = next(c for c in report.checks if c.name == "Model Response")
        self.assertFalse(model_check.passed)
        self.assertIn("not found", model_check.details.lower())

    # 8. Doctor live provider without structured tool support
    def test_doctor_live_provider_without_structured_tool_support(self) -> None:
        cfg = LLMConfig(
            provider="openai_compatible",
            base_url="https://api.openai.com/v1",
            model="gpt-4.1-mini",
            temperature=0.0,
            api_key="sk-test-key",
        )
        mock_provider = MagicMock()
        # Ping succeeds, but tool probe returns plain conversational text without tool_calls
        mock_provider.chat.side_effect = [
            LLMResponse(content="pong"),
            LLMResponse(content="I do not support tool calls, here is plain text instead.", tool_calls=[]),
        ]

        report = run_llm_diagnostics(cfg, live=True, provider=mock_provider)

        self.assertFalse(report.all_passed)
        tool_check = next(c for c in report.checks if c.name == "Structured Tool Calling")
        self.assertFalse(tool_check.passed)
        self.assertIn("plain text instead of returning a structured tool call", tool_check.details)

    # 9. Doctor never exposes API keys
    def test_doctor_never_exposes_api_keys(self) -> None:
        secret = "sk-live-supersecrettoken9876543210"
        cfg = LLMConfig(
            provider="openai_compatible",
            base_url="https://api.openai.com/v1",
            model="gpt-4.1-mini",
            temperature=0.0,
            api_key=secret,
        )
        mock_provider = MagicMock()
        mock_provider.chat.side_effect = LLMAuthenticationError(
            f"Authentication failed: Bearer {secret} was rejected"
        )

        report = run_llm_diagnostics(cfg, live=True, provider=mock_provider)
        rendered = report.render()

        self.assertNotIn(secret, rendered)
        self.assertIn("[REDACTED]", rendered)
