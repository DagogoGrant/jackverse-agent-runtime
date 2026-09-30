"""Confirmation handlers for interactive terminal execution and deterministic automated testing."""

from __future__ import annotations

from dataclasses import dataclass, field
import sys
from typing import TextIO

from harness.permissions.base import ConfirmationHandler, PermissionRequest


class ConsoleConfirmationHandler(ConfirmationHandler):
    """Interactive CLI confirmation handler prompting via terminal standard streams."""

    def __init__(
        self,
        stdin: TextIO | None = None,
        stdout: TextIO | None = None,
    ) -> None:
        self._stdin = stdin or sys.stdin
        self._stdout = stdout or sys.stdout

    def request_confirmation(self, request: PermissionRequest) -> bool:
        """Prompt user on terminal for confirmation with privacy-safe allowlisted details."""
        try:
            banner = (
                f"\n{'=' * 68}\n"
                f"[SECURITY CONFIRMATION REQUIRED]\n"
                f"Tool:       {request.canonical_tool_identity}\n"
                f"Risk Level: {request.risk_level.value.upper() if getattr(request, 'risk_level', None) is not None else 'MUTATING'}\n"
            )
            if request.resource_descriptor:
                banner += f"Target:     {request.resource_descriptor}\n"
            if request.arguments_summary:
                args_fmt = ", ".join(f"{k}={v}" for k, v in request.arguments_summary.items())
                banner += f"Arguments:  {args_fmt}\n"
            banner += (
                f"Call ID:    {request.call_id}\n"
                f"{'=' * 68}\n"
                f"Authorize action? [y/N]: "
            )
            self._stdout.write(banner)
            self._stdout.flush()

            line = self._stdin.readline()
            if not line:
                return False
            answer = line.strip().lower()
            return answer in ("y", "yes")
        except (EOFError, KeyboardInterrupt, Exception):
            return False


@dataclass
class DeterministicConfirmationHandler(ConfirmationHandler):
    """Headless, non-blocking confirmation handler for automated testing and CI."""

    always_allow: bool = True
    responses: dict[str, bool] = field(default_factory=dict)
    requests: list[PermissionRequest] = field(default_factory=list)

    def request_confirmation(self, request: PermissionRequest) -> bool:
        """Deterministically resolve confirmation from canned responses or default stance."""
        self.requests.append(request)

        if request.call_id in self.responses:
            return self.responses[request.call_id]
        if request.tool_name in self.responses:
            return self.responses[request.tool_name]
        if request.canonical_tool_identity in self.responses:
            return self.responses[request.canonical_tool_identity]

        return self.always_allow
