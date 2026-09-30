from types import SimpleNamespace
from unittest.mock import MagicMock
import unittest

from harness.llm.client import (
    LLMClient,
    LLMResponse,
    ToolCall,
    tool_spec_to_openai,
)
from harness.tools.base import ToolSpec


class TestLLMClientAndToolTransport(unittest.TestCase):
    def test_tool_spec_to_openai_schema_conversion(self) -> None:
        spec = ToolSpec(
            name="get_weather",
            description="Get weather for a city.",
            input_schema={
                "type": "object",
                "properties": {
                    "city": {"type": "string", "description": "City name."}
                },
                "required": ["city"],
                "additionalProperties": False,
            },
        )

        openai_tool = tool_spec_to_openai(spec)

        expected = {
            "type": "function",
            "function": {
                "name": "get_weather",
                "description": "Get weather for a city.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "city": {"type": "string", "description": "City name."}
                    },
                    "required": ["city"],
                    "additionalProperties": False,
                },
            },
        }
        self.assertEqual(openai_tool, expected)

    def test_plain_text_response_returns_llm_response(self) -> None:
        client = LLMClient(
            api_key="fake_key",
            base_url="https://example.com",
            model="fake-model",
        )

        mock_choice = SimpleNamespace(
            message=SimpleNamespace(
                content="Hello! How can I help you?",
                tool_calls=None,
            ),
            finish_reason="stop",
        )
        mock_response = SimpleNamespace(choices=[mock_choice])
        client.client.chat.completions.create = MagicMock(return_value=mock_response)  # type: ignore[method-assign]

        res = client.chat([{"role": "user", "content": "Hi"}])

        self.assertIsInstance(res, LLMResponse)
        self.assertEqual(res.content, "Hello! How can I help you?")
        self.assertEqual(res.tool_calls, [])

    def test_single_tool_call_parses_correctly(self) -> None:
        client = LLMClient(
            api_key="fake_key",
            base_url="https://example.com",
            model="fake-model",
        )

        mock_tool_call = SimpleNamespace(
            id="call_weather_1",
            type="function",
            function=SimpleNamespace(
                name="get_weather",
                arguments='{"city": "Passau"}',
            ),
        )
        mock_choice = SimpleNamespace(
            message=SimpleNamespace(
                content=None,
                tool_calls=[mock_tool_call],
            ),
            finish_reason="tool_calls",
        )
        mock_response = SimpleNamespace(choices=[mock_choice])
        client.client.chat.completions.create = MagicMock(return_value=mock_response)  # type: ignore[method-assign]

        res = client.chat([{"role": "user", "content": "Weather in Passau?"}])

        self.assertIsInstance(res, LLMResponse)
        self.assertIsNone(res.content)
        self.assertEqual(len(res.tool_calls), 1)

        call = res.tool_calls[0]
        self.assertEqual(call.id, "call_weather_1")
        self.assertEqual(call.name, "get_weather")
        self.assertEqual(call.arguments, {"city": "Passau"})

    def test_explicit_empty_json_dict_arguments_accepted(self) -> None:
        client = LLMClient(
            api_key="fake_key",
            base_url="https://example.com",
            model="fake-model",
        )

        mock_tool_call = SimpleNamespace(
            id="call_list_1",
            type="function",
            function=SimpleNamespace(
                name="list_directory",
                arguments="{}",
            ),
        )
        mock_choice = SimpleNamespace(
            message=SimpleNamespace(content=None, tool_calls=[mock_tool_call]),
            finish_reason="tool_calls",
        )
        client.client.chat.completions.create = MagicMock(  # type: ignore[method-assign]
            return_value=SimpleNamespace(choices=[mock_choice])
        )

        res = client.chat([{"role": "user", "content": "List"}])

        self.assertEqual(len(res.tool_calls), 1)
        self.assertEqual(res.tool_calls[0].arguments, {})

    def test_multiple_tool_calls_parse_correctly(self) -> None:
        client = LLMClient(
            api_key="fake_key",
            base_url="https://example.com",
            model="fake-model",
        )

        mock_call1 = SimpleNamespace(
            id="call_1",
            type="function",
            function=SimpleNamespace(
                name="read_file",
                arguments='{"path": "notes.txt"}',
            ),
        )
        mock_call2 = SimpleNamespace(
            id="call_2",
            type="function",
            function=SimpleNamespace(
                name="list_directory",
                arguments='{"path": "docs"}',
            ),
        )
        mock_choice = SimpleNamespace(
            message=SimpleNamespace(
                content="I will check these files.",
                tool_calls=[mock_call1, mock_call2],
            ),
            finish_reason="tool_calls",
        )
        mock_response = SimpleNamespace(choices=[mock_choice])
        client.client.chat.completions.create = MagicMock(return_value=mock_response)  # type: ignore[method-assign]

        res = client.chat([{"role": "user", "content": "Inspect notes and docs"}])

        self.assertEqual(res.content, "I will check these files.")
        self.assertEqual(len(res.tool_calls), 2)
        self.assertEqual(res.tool_calls[0].name, "read_file")
        self.assertEqual(res.tool_calls[0].arguments, {"path": "notes.txt"})
        self.assertEqual(res.tool_calls[1].name, "list_directory")
        self.assertEqual(res.tool_calls[1].arguments, {"path": "docs"})

    def test_tools_parameter_forwarded_to_openai_client(self) -> None:
        client = LLMClient(
            api_key="fake_key",
            base_url="https://example.com",
            model="fake-model",
        )

        mock_choice = SimpleNamespace(
            message=SimpleNamespace(content="Done", tool_calls=None)
        )
        mock_create = MagicMock(return_value=SimpleNamespace(choices=[mock_choice]))
        client.client.chat.completions.create = mock_create  # type: ignore[method-assign]

        spec = ToolSpec(
            name="search_files",
            description="Search text",
            input_schema={"type": "object", "properties": {}},
        )

        client.chat([{"role": "user", "content": "Search"}], tools=[spec])

        mock_create.assert_called_once()
        _, kwargs = mock_create.call_args
        self.assertIn("tools", kwargs)
        self.assertEqual(len(kwargs["tools"]), 1)
        self.assertEqual(kwargs["tools"][0]["function"]["name"], "search_files")

    def test_missing_or_empty_call_id_raises_error(self) -> None:
        client = LLMClient(
            api_key="fake_key",
            base_url="https://example.com",
            model="fake-model",
        )

        for bad_id in [None, "", "   "]:
            mock_tool_call = SimpleNamespace(
                id=bad_id,
                type="function",
                function=SimpleNamespace(name="some_tool", arguments='{"a": 1}'),
            )
            mock_choice = SimpleNamespace(
                message=SimpleNamespace(content=None, tool_calls=[mock_tool_call])
            )
            client.client.chat.completions.create = MagicMock(  # type: ignore[method-assign]
                return_value=SimpleNamespace(choices=[mock_choice])
            )

            with self.assertRaises(ValueError) as ctx:
                client.chat([{"role": "user", "content": "Run"}])
            self.assertIn("id", str(ctx.exception).lower())

    def test_missing_function_definition_raises_error(self) -> None:
        client = LLMClient(
            api_key="fake_key",
            base_url="https://example.com",
            model="fake-model",
        )

        mock_tool_call = SimpleNamespace(
            id="call_1",
            type="function",
            function=None,
        )
        mock_choice = SimpleNamespace(
            message=SimpleNamespace(content=None, tool_calls=[mock_tool_call])
        )
        client.client.chat.completions.create = MagicMock(  # type: ignore[method-assign]
            return_value=SimpleNamespace(choices=[mock_choice])
        )

        with self.assertRaises(ValueError) as ctx:
            client.chat([{"role": "user", "content": "Run"}])
        self.assertIn("function", str(ctx.exception).lower())

    def test_missing_or_empty_function_name_raises_error(self) -> None:
        client = LLMClient(
            api_key="fake_key",
            base_url="https://example.com",
            model="fake-model",
        )

        for bad_name in [None, "", "   "]:
            mock_tool_call = SimpleNamespace(
                id="call_1",
                type="function",
                function=SimpleNamespace(name=bad_name, arguments='{"a": 1}'),
            )
            mock_choice = SimpleNamespace(
                message=SimpleNamespace(content=None, tool_calls=[mock_tool_call])
            )
            client.client.chat.completions.create = MagicMock(  # type: ignore[method-assign]
                return_value=SimpleNamespace(choices=[mock_choice])
            )

            with self.assertRaises(ValueError) as ctx:
                client.chat([{"role": "user", "content": "Run"}])
            self.assertIn("name", str(ctx.exception).lower())

    def test_missing_or_empty_arguments_raises_error(self) -> None:
        client = LLMClient(
            api_key="fake_key",
            base_url="https://example.com",
            model="fake-model",
        )

        for bad_args in [None, "", "   "]:
            mock_tool_call = SimpleNamespace(
                id="call_1",
                type="function",
                function=SimpleNamespace(name="some_tool", arguments=bad_args),
            )
            mock_choice = SimpleNamespace(
                message=SimpleNamespace(content=None, tool_calls=[mock_tool_call])
            )
            client.client.chat.completions.create = MagicMock(  # type: ignore[method-assign]
                return_value=SimpleNamespace(choices=[mock_choice])
            )

            with self.assertRaises(ValueError) as ctx:
                client.chat([{"role": "user", "content": "Run"}])
            self.assertIn("arguments", str(ctx.exception).lower())

    def test_malformed_json_arguments_raises_error(self) -> None:
        client = LLMClient(
            api_key="fake_key",
            base_url="https://example.com",
            model="fake-model",
        )

        mock_tool_call = SimpleNamespace(
            id="call_bad_json",
            type="function",
            function=SimpleNamespace(
                name="some_tool",
                arguments="{unclosed json",
            ),
        )
        mock_choice = SimpleNamespace(
            message=SimpleNamespace(content=None, tool_calls=[mock_tool_call])
        )
        client.client.chat.completions.create = MagicMock(  # type: ignore[method-assign]
            return_value=SimpleNamespace(choices=[mock_choice])
        )

        with self.assertRaises(ValueError) as ctx:
            client.chat([{"role": "user", "content": "Run"}])
        self.assertIn("JSON", str(ctx.exception))

    def test_non_object_json_arguments_raises_error(self) -> None:
        client = LLMClient(
            api_key="fake_key",
            base_url="https://example.com",
            model="fake-model",
        )

        mock_tool_call = SimpleNamespace(
            id="call_array",
            type="function",
            function=SimpleNamespace(
                name="some_tool",
                arguments='["not", "an", "object"]',
            ),
        )
        mock_choice = SimpleNamespace(
            message=SimpleNamespace(content=None, tool_calls=[mock_tool_call])
        )
        client.client.chat.completions.create = MagicMock(  # type: ignore[method-assign]
            return_value=SimpleNamespace(choices=[mock_choice])
        )

        with self.assertRaises(ValueError) as ctx:
            client.chat([{"role": "user", "content": "Run"}])
        self.assertIn("JSON object", str(ctx.exception))

    def test_empty_response_without_content_or_tools_raises_runtime_error(self) -> None:
        client = LLMClient(
            api_key="fake_key",
            base_url="https://example.com",
            model="fake-model",
        )

        mock_choice = SimpleNamespace(
            message=SimpleNamespace(content=None, tool_calls=None)
        )
        client.client.chat.completions.create = MagicMock(  # type: ignore[method-assign]
            return_value=SimpleNamespace(choices=[mock_choice])
        )

        with self.assertRaises(RuntimeError) as ctx:
            client.chat([{"role": "user", "content": "Run"}])
        self.assertIn("empty response", str(ctx.exception).lower())

    def test_timeout_and_max_retries_zero_passed_to_openai_constructor(self) -> None:
        client = LLMClient(
            api_key="fake_key",
            base_url="https://example.com",
            model="fake-model",
            timeout=42.5,
            max_retries=2,
        )
        self.assertEqual(client.timeout, 42.5)
        self.assertEqual(client.max_retries, 2)
        self.assertEqual(client.client.timeout, 42.5)
        self.assertEqual(client.client.max_retries, 0)

    def test_timeout_forwarded_in_chat_completion_kwargs(self) -> None:
        client = LLMClient(
            api_key="fake_key",
            base_url="https://example.com",
            model="fake-model",
            timeout=15.0,
        )

        mock_choice = SimpleNamespace(
            message=SimpleNamespace(content="OK", tool_calls=None)
        )
        mock_create = MagicMock(return_value=SimpleNamespace(choices=[mock_choice]))
        client.client.chat.completions.create = mock_create  # type: ignore[method-assign]

        client.chat([{"role": "user", "content": "Hi"}])

        mock_create.assert_called_once()
        _, kwargs = mock_create.call_args
        self.assertEqual(kwargs.get("timeout"), 15.0)

    def test_transient_error_retries_and_succeeds(self) -> None:
        sleep_calls: list[float] = []

        client = LLMClient(
            api_key="fake_key",
            base_url="https://example.com",
            model="fake-model",
            max_retries=2,
            retry_backoff=0.5,
            sleep_fn=sleep_calls.append,
        )

        mock_choice = SimpleNamespace(
            message=SimpleNamespace(content="Recovered response", tool_calls=None)
        )
        mock_response = SimpleNamespace(choices=[mock_choice])

        fake_request = MagicMock()
        from openai import APITimeoutError, APIConnectionError

        # Fail attempt 1 with timeout, fail attempt 2 with connection error, succeed on attempt 3
        mock_create = MagicMock(
            side_effect=[
                APITimeoutError(request=fake_request),
                APIConnectionError(request=fake_request),
                mock_response,
            ]
        )
        client.client.chat.completions.create = mock_create  # type: ignore[method-assign]

        res = client.chat([{"role": "user", "content": "Query"}])

        self.assertEqual(res.content, "Recovered response")
        self.assertEqual(mock_create.call_count, 3)
        # Verify exponential backoff delays recorded: 0.5s (2^0 * 0.5) on attempt 1, 1.0s (2^1 * 0.5) on attempt 2
        self.assertEqual(sleep_calls, [0.5, 1.0])

    def test_transient_error_exhausts_retries_and_raises_runtime_error(self) -> None:
        sleep_calls: list[float] = []

        client = LLMClient(
            api_key="fake_key",
            base_url="https://example.com",
            model="fake-model",
            max_retries=2,
            retry_backoff=0.25,
            sleep_fn=sleep_calls.append,
        )

        from openai import APITimeoutError
        fake_request = MagicMock()

        # Fail all 3 attempts (1 initial + 2 retries)
        mock_create = MagicMock(side_effect=APITimeoutError(request=fake_request))
        client.client.chat.completions.create = mock_create  # type: ignore[method-assign]

        with self.assertRaises(RuntimeError) as ctx:
            client.chat([{"role": "user", "content": "Query"}])

        self.assertIn("failed after 3 attempts", str(ctx.exception))
        self.assertEqual(mock_create.call_count, 3)
        self.assertEqual(sleep_calls, [0.25, 0.5])

    def test_non_retryable_error_fails_immediately_without_retry_or_sleep(self) -> None:
        sleep_calls: list[float] = []

        client = LLMClient(
            api_key="fake_key",
            base_url="https://example.com",
            model="fake-model",
            max_retries=2,
            sleep_fn=sleep_calls.append,
        )

        from openai import AuthenticationError
        fake_response = MagicMock(status_code=401)
        fake_response.headers = {}
        mock_create = MagicMock(
            side_effect=AuthenticationError(message="Invalid API Key", response=fake_response, body=None)
        )
        client.client.chat.completions.create = mock_create  # type: ignore[method-assign]

        with self.assertRaises(AuthenticationError):
            client.chat([{"role": "user", "content": "Query"}])

        # Exactly 1 attempt made, 0 sleep calls
        self.assertEqual(mock_create.call_count, 1)
        self.assertEqual(len(sleep_calls), 0)

    def test_markdown_json_fences_are_cleaned_successfully(self) -> None:
        client = LLMClient(
            api_key="fake_key",
            base_url="https://example.com",
            model="fake-model",
        )

        mock_tool_call = SimpleNamespace(
            id="call_fenced",
            type="function",
            function=SimpleNamespace(
                name="read_file",
                arguments='```json\n{"path": "data.txt"}\n```',
            ),
        )
        mock_choice = SimpleNamespace(
            message=SimpleNamespace(content=None, tool_calls=[mock_tool_call])
        )
        client.client.chat.completions.create = MagicMock(  # type: ignore[method-assign]
            return_value=SimpleNamespace(choices=[mock_choice], usage=None)
        )

        res = client.chat([{"role": "user", "content": "Read"}])
        self.assertEqual(len(res.tool_calls), 1)
        self.assertEqual(res.tool_calls[0].arguments, {"path": "data.txt"})

    def test_usage_tokens_parsed_when_present(self) -> None:
        client = LLMClient(
            api_key="fake_key",
            base_url="https://example.com",
            model="fake-model",
        )

        mock_choice = SimpleNamespace(
            message=SimpleNamespace(content="Hello", tool_calls=None)
        )
        mock_usage = SimpleNamespace(prompt_tokens=15, completion_tokens=8, total_tokens=23)
        client.client.chat.completions.create = MagicMock(  # type: ignore[method-assign]
            return_value=SimpleNamespace(choices=[mock_choice], usage=mock_usage)
        )

        res = client.chat([{"role": "user", "content": "Hi"}])
        self.assertIsNotNone(res.usage)
        self.assertEqual(res.usage, {"prompt_tokens": 15, "completion_tokens": 8, "total_tokens": 23})


if __name__ == "__main__":
    unittest.main()
