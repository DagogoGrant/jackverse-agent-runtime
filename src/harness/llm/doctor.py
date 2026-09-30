from dataclasses import dataclass, field
import re
from typing import Any
import urllib.parse

from harness.config import ConfigurationError, LLMConfig
from harness.llm.base import (
    LLMAuthenticationError,
    LLMConnectionError,
    LLMError,
    LLMProvider,
    ProviderCapabilityError,
)
from harness.llm.factory import create_llm_provider
from harness.tools.base import ToolSpec


def _scrub_secrets(text: str, api_key: str | None = None) -> str:
    """Ensure no credentials, authorization headers, or secret keys appear in output."""
    if api_key and api_key.strip():
        text = text.replace(api_key.strip(), "[REDACTED]")
    text = re.sub(r"Bearer\s+[A-Za-z0-9_\-\.]+", "Bearer [REDACTED]", text)
    text = re.sub(r"sk-[A-Za-z0-9_\-]{10,}", "[REDACTED]", text)
    return text


@dataclass(frozen=True)
class DiagnosticCheck:
    name: str
    passed: bool
    details: str


@dataclass(frozen=True)
class DiagnosticReport:
    provider: str
    base_url: str
    model: str
    authenticated: bool
    checks: list[DiagnosticCheck]
    live: bool = False

    @property
    def all_passed(self) -> bool:
        return all(c.passed for c in self.checks)

    def render(self) -> str:
        mode_title = " (LIVE)" if self.live else ""
        lines: list[str] = [
            "==================================================================",
            f"            JackVerse LLM Provider Diagnostics{mode_title}            ",
            "==================================================================",
            f"LLM Provider   : {self.provider}",
            f"LLM Base URL   : {self.base_url}",
            f"LLM Model      : {self.model}",
            f"Authentication : {'configured (redacted)' if self.authenticated else 'anonymous / not required'}",
            f"Check Mode     : {'Live Provider Verification' if self.live else 'Configuration & Environment Syntax'}",
            "------------------------------------------------------------------",
        ]
        for c in self.checks:
            mark = "✓" if c.passed else "✗"
            status = "PASS" if c.passed else "FAIL"
            lines.append(f"  {mark} [{status}] {c.name}: {c.details}")
        lines.append("==================================================================")
        return "\n".join(lines)


def run_llm_diagnostics(
    config: LLMConfig,
    live: bool = False,
    provider: LLMProvider | None = None,
) -> DiagnosticReport:
    """Run non-destructive diagnostic checks on the configured LLM provider.

    Args:
        config: The LLM configuration to validate.
        live: If True, executes minimal non-destructive live network probes against the provider.
        provider: Optional pre-constructed LLMProvider (useful for dependency injection and tests).
    """
    checks: list[DiagnosticCheck] = []
    auth_configured = bool(config.api_key and config.api_key.strip())

    # 1. Configuration presence
    if not config.model or not config.model.strip():
        checks.append(DiagnosticCheck("Configuration", False, "LLM_MODEL is missing or empty"))
    elif not config.base_url or not config.base_url.strip():
        checks.append(DiagnosticCheck("Configuration", False, "LLM_BASE_URL is missing or empty"))
    else:
        checks.append(
            DiagnosticCheck("Configuration", True, f"Configured for model '{config.model}'")
        )

    # 2. Base URL format
    parsed = urllib.parse.urlsplit(config.base_url or "")
    if parsed.scheme in ("http", "https") and parsed.netloc:
        checks.append(
            DiagnosticCheck("Base URL Format", True, f"Valid {parsed.scheme.upper()} endpoint: {parsed.netloc}")
        )
    else:
        checks.append(
            DiagnosticCheck("Base URL Format", False, f"Malformed URL: {config.base_url}")
        )

    # 3. Provider factory resolution
    provider_instance = provider
    if provider_instance is None:
        try:
            provider_instance = create_llm_provider(config)
            checks.append(
                DiagnosticCheck("Provider Factory", True, f"Resolved to {type(provider_instance).__name__}")
            )
        except Exception as e:
            cleaned_err = _scrub_secrets(str(e), config.api_key)
            checks.append(
                DiagnosticCheck("Provider Factory", False, f"Resolution error: {cleaned_err}")
            )
    else:
        checks.append(
            DiagnosticCheck("Provider Factory", True, f"Resolved to {type(provider_instance).__name__}")
        )

    # 4. Authentication configuration
    is_local = any(
        h in (config.base_url or "").lower()
        for h in ("localhost", "127.0.0.1", "::1", "host.docker.internal", "ollama")
    )
    if auth_configured:
        checks.append(DiagnosticCheck("Authentication", True, "API key present (redacted)"))
    elif is_local:
        checks.append(DiagnosticCheck("Authentication", True, "No API key configured (local / anonymous mode)"))
    else:
        checks.append(DiagnosticCheck("Authentication", False, "API key is not configured for remote endpoint"))

    # If configuration checks failed or live mode was not requested, return static report
    if not live or not all(c.passed for c in checks) or provider_instance is None:
        return DiagnosticReport(
            provider=config.provider,
            base_url=config.base_url or "",
            model=config.model or "",
            authenticated=auth_configured,
            checks=checks,
            live=live,
        )

    # ------------------------------------------------------------------
    # LIVE VERIFICATION SUITE
    # ------------------------------------------------------------------

    # 5. Live Reachability, Authentication, and Model Response Probe
    live_chat_ok = False
    try:
        ping_response = provider_instance.chat([{"role": "user", "content": "ping"}], tools=None)
        if ping_response.content is not None or ping_response.tool_calls:
            checks.append(DiagnosticCheck("Endpoint Reachable", True, f"Successfully contacted {parsed.netloc}"))
            checks.append(DiagnosticCheck("Authentication Accepted", True, "Provider accepted credentials"))
            checks.append(DiagnosticCheck("Model Response", True, f"Model '{config.model}' responded successfully"))
            live_chat_ok = True
        else:
            checks.append(DiagnosticCheck("Endpoint Reachable", True, f"Connected to {parsed.netloc}"))
            checks.append(DiagnosticCheck("Model Response", False, "Model returned empty response payload"))
    except LLMAuthenticationError as e:
        cleaned_err = _scrub_secrets(str(e), config.api_key)
        checks.append(DiagnosticCheck("Endpoint Reachable", True, f"Connected to {parsed.netloc}"))
        checks.append(DiagnosticCheck("Authentication Accepted", False, f"Authentication rejected: {cleaned_err}"))
    except LLMConnectionError as e:
        cleaned_err = _scrub_secrets(str(e), config.api_key)
        checks.append(DiagnosticCheck("Endpoint Reachable", False, f"Connection failed: {cleaned_err}"))
    except Exception as e:
        cleaned_err = _scrub_secrets(str(e), config.api_key)
        err_lower = cleaned_err.lower()
        if "not found" in err_lower or "404" in err_lower or "does not exist" in err_lower:
            checks.append(DiagnosticCheck("Endpoint Reachable", True, f"Connected to {parsed.netloc}"))
            checks.append(DiagnosticCheck("Model Response", False, f"Model '{config.model}' not found on provider: {cleaned_err}"))
        elif "auth" in err_lower or "401" in err_lower or "key" in err_lower:
            checks.append(DiagnosticCheck("Endpoint Reachable", True, f"Connected to {parsed.netloc}"))
            checks.append(DiagnosticCheck("Authentication Accepted", False, f"Authentication failure: {cleaned_err}"))
        else:
            checks.append(DiagnosticCheck("Model Response", False, f"Provider error: {cleaned_err}"))

    # 6. Structured Tool Calling Capability Check (Harmless synthetic tool, NOT executed)
    if live_chat_ok:
        health_tool = ToolSpec(
            name="health_check",
            description="Diagnostic probe verifying structured tool-call capabilities.",
            input_schema={
                "type": "object",
                "properties": {
                    "message": {"type": "string", "description": "Verification ping message."}
                },
                "required": ["message"],
                "additionalProperties": False,
            },
        )
        try:
            tool_probe_response = provider_instance.chat(
                [
                    {
                        "role": "user",
                        "content": "Run an automated diagnostic check. Call the 'health_check' tool with message='ping'. Do not reply with text.",
                    }
                ],
                tools=[health_tool],
            )
            # The doctor must NOT execute that tool. It only verifies structured tool calls.
            found_tool_call = any(
                tc.name == "health_check" for tc in tool_probe_response.tool_calls
            )
            if found_tool_call:
                checks.append(
                    DiagnosticCheck("Structured Tool Calling", True, "Model returned valid structured tool call for 'health_check'")
                )
            else:
                checks.append(
                    DiagnosticCheck(
                        "Structured Tool Calling",
                        False,
                        "Model responded with plain text instead of returning a structured tool call",
                    )
                )
        except ProviderCapabilityError as e:
            cleaned_err = _scrub_secrets(str(e), config.api_key)
            checks.append(
                DiagnosticCheck("Structured Tool Calling", False, f"Provider lacks structured tool capability: {cleaned_err}")
            )
        except Exception as e:
            cleaned_err = _scrub_secrets(str(e), config.api_key)
            checks.append(
                DiagnosticCheck("Structured Tool Calling", False, f"Tool call probe failed: {cleaned_err}")
            )

    return DiagnosticReport(
        provider=config.provider,
        base_url=config.base_url or "",
        model=config.model or "",
        authenticated=auth_configured,
        checks=checks,
        live=live,
    )
