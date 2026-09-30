"""Thread-safe confirmation handler bridging PermissionManager with the Textual Operator UI."""

from __future__ import annotations

from collections.abc import Callable
import logging
import threading
from typing import Any

from harness.permissions.base import ConfirmationHandler, PermissionRequest

logger = logging.getLogger("harness.tui.confirmation")


class TUIConfirmationHandler(ConfirmationHandler):
    """Bridges PermissionManager's confirmation requirement with Textual's operator modal dialog.

    Security & Architecture Invariants:
      1. Zero Bypass: The operator can only approve or deny the request. The approved decision
         flows directly back through PermissionManager.authorize(), which binds the confirmation
         token with the arguments fingerprint and enforces single-use anti-replay protection.
      2. Worker Isolation: The background agent worker thread is blocked while awaiting the operator
         decision, while the main Textual event loop remains completely responsive.
      3. Safe Presentation: Only allowlisted summary arguments and canonical identities are displayed.
    """

    def __init__(
        self,
        prompt_callback: Callable[[PermissionRequest, Callable[[bool], None]], None] | None = None,
        timeout_seconds: float = 120.0,
        default_decision: bool = False,
    ) -> None:
        self._prompt_callback = prompt_callback
        self._timeout_seconds = timeout_seconds
        self._default_decision = default_decision
        self._lock = threading.Lock()
        self._pending_events: set[threading.Event] = set()

    def set_prompt_callback(
        self,
        callback: Callable[[PermissionRequest, Callable[[bool], None]], None],
    ) -> None:
        """Attach or update the UI modal callback."""
        self._prompt_callback = callback

    def cancel_pending(self) -> None:
        """Cancel all pending confirmation dialogs immediately, rejecting pending actions and unblocking workers."""
        with self._lock:
            for ev in list(self._pending_events):
                ev.set()

    def request_confirmation(self, request: PermissionRequest) -> bool:
        """Display modal confirmation prompt in TUI and block worker until resolved or timed out."""
        if self._prompt_callback is None:
            logger.warning(
                f"No TUI prompt callback registered for confirmation of {request.canonical_tool_identity}. Defaulting to {self._default_decision}."
            )
            return self._default_decision

        decision_box: list[bool] = [self._default_decision]
        resolved_event = threading.Event()

        def resolve(approved: bool) -> None:
            decision_box[0] = approved
            resolved_event.set()

        with self._lock:
            self._pending_events.add(resolved_event)

        try:
            # Marshal modal prompt to Textual UI thread
            self._prompt_callback(request, resolve)

            # Block background worker thread until operator clicks Approve/Reject
            signaled = resolved_event.wait(timeout=self._timeout_seconds)
            if not signaled:
                logger.warning(
                    f"Operator confirmation timed out after {self._timeout_seconds}s for {request.canonical_tool_identity}."
                )
                return False

            return decision_box[0]
        except Exception as exc:
            logger.error(f"Error handling TUI confirmation: {exc}", exc_info=False)
            return False
        finally:
            with self._lock:
                self._pending_events.discard(resolved_event)
