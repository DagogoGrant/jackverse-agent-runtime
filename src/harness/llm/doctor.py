from dataclasses import dataclass
from typing import Any
import urllib.parse
import urllib.request
import urllib.error

from harness.config import ConfigurationError, LLMConfig
from harness.llm.base import LLMProvider
from harness.llm.factory import create_llm_provider


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

    @property
    def all_passed(self) -> bool:
        return all(c.passed for c in self.checks)

    def render(self) -> str:
        lines: list[str] = [
            "==================================================================",
            "                JackVerse LLM Provider Diagnostics                ",
            "==================================================================",
            f"LLM Provider   : {self.provider}",
            f"LLM Base URL   : {self.base_url}",
            f"LLM Model      : {self.model}",
            f"Authentication : {'configured (redacted)' if self.authenticated else 'anonymous / not required'}",
            "------------------------------------------------------------------",
        ]
        for c in self.checks:
            mark = "✓" if c.passed else "✗"
            status = "PASS" if c.passed else "FAIL"
            lines.append(f"  {mark} [{status}] {c.name}: {c.details}")
        lines.append("==================================================================")
        return "\n".join(lines)


def run_llm_diagnostics(config: LLMConfig) -> DiagnosticReport:
    """Run non-destructive diagnostic checks on the configured LLM provider."""
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
    try:
        provider_instance = create_llm_provider(config)
        checks.append(
            DiagnosticCheck("Provider Factory", True, f"Resolved to {type(provider_instance).__name__}")
        )
    except Exception as e:
        checks.append(
            DiagnosticCheck("Provider Factory", False, f"Resolution error: {e}")
        )

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

    return DiagnosticReport(
        provider=config.provider,
        base_url=config.base_url or "",
        model=config.model or "",
        authenticated=auth_configured,
        checks=checks,
    )
