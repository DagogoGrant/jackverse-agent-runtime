"""Modal dialogs for Operator Console (Security Confirmation, Architectural Explanation, Custom Prompt, and Post-Run Summary)."""

from __future__ import annotations

from dataclasses import dataclass
import os
from typing import Any

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Container, Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Label, RadioButton, RadioSet, Static, TextArea

from harness.permissions.base import PermissionRequest
from harness.runtime.events import MemoryOperationEvent
from harness.tools.base import ToolSource
from harness.tui.theme import BORDER, PANEL_BG, SUCCESS, TEXT_ACCENT, TEXT_MUTED, WARNING


class ConfirmationModal(ModalScreen[bool]):
    """Interactive operator confirmation dialog for mutating or sensitive tool calls."""

    DEFAULT_CSS = f"""
    ConfirmationModal {{
        align: center middle;
    }}
    .confirm-dialog {{
        width: 68;
        height: auto;
        background: {PANEL_BG};
        border: thick {WARNING};
        padding: 1 2;
    }}
    .confirm-title {{
        text-style: bold;
        color: {WARNING};
        margin-bottom: 1;
        border-bottom: solid {BORDER};
        padding-bottom: 1;
    }}
    .confirm-prop {{
        height: 1;
        margin-bottom: 0;
    }}
    .confirm-label {{
        width: 20;
        color: {TEXT_MUTED};
    }}
    .confirm-val {{
        color: #FFFFFF;
        text-style: bold;
    }}
    .confirm-buttons {{
        margin-top: 1;
        height: 3;
        align: right middle;
    }}
    """

    def __init__(self, request: PermissionRequest, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.request = request

    def compose(self) -> ComposeResult:
        with Vertical(classes="confirm-dialog"):
            yield Label("SECURITY CONFIRMATION REQUIRED", classes="confirm-title")

            with Horizontal(classes="confirm-prop"):
                yield Label("Tool Identity:", classes="confirm-label")
                yield Label(f"{self.request.canonical_tool_identity}", classes="confirm-val")

            with Horizontal(classes="confirm-prop"):
                yield Label("Risk Level:", classes="confirm-label")
                risk_str = self.request.risk_level.value.upper() if getattr(self.request, "risk_level", None) else "MUTATING"
                yield Label(f"{risk_str}", classes="confirm-val")

            if self.request.resource_descriptor:
                with Horizontal(classes="confirm-prop"):
                    yield Label("Resource Target:", classes="confirm-label")
                    yield Label(f"{self.request.resource_descriptor}", classes="confirm-val")

            if self.request.arguments_fingerprint:
                with Horizontal(classes="confirm-prop"):
                    yield Label("Arg Fingerprint:", classes="confirm-label")
                    yield Label(f"{self.request.arguments_fingerprint[:16]}...", classes="confirm-val")

            if self.request.arguments_summary:
                args_fmt = ", ".join(f"{k}={v}" for k, v in self.request.arguments_summary.items())
                with Horizontal(classes="confirm-prop"):
                    yield Label("Arguments:", classes="confirm-label")
                    yield Label(f"{args_fmt}", classes="confirm-val")

            yield Static(
                "\n[#B0B8C4]Single-use authorization: Approving binds token to this exact argument fingerprint.\n"
                "Anti-replay and TOCTOU protections are strictly enforced.[/#B0B8C4]"
            )

            with Horizontal(classes="confirm-buttons"):
                yield Button("✓ Approve Action (Y)", id="btn-approve", variant="primary")
                yield Button("✗ Reject Action (N)", id="btn-reject", variant="error")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn-approve":
            self.dismiss(True)
        elif event.button.id == "btn-reject":
            self.dismiss(False)

    def on_key(self, event) -> None:
        if event.key in ("y", "Y", "enter"):
            self.dismiss(True)
        elif event.key in ("n", "N", "escape"):
            self.dismiss(False)


class ExplanationModal(ModalScreen[None]):
    """Modal dialog displaying the human-readable architectural explanation."""

    DEFAULT_CSS = f"""
    ExplanationModal {{
        align: center middle;
    }}
    .explanation-dialog {{
        width: 80;
        height: 80%;
        background: {PANEL_BG};
        border: thick {TEXT_ACCENT};
        padding: 1 2;
    }}
    .explanation-title {{
        text-style: bold;
        color: {TEXT_ACCENT};
        margin-bottom: 1;
        border-bottom: solid {BORDER};
        padding-bottom: 1;
    }}
    """

    def __init__(self, text: str, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.text = text

    def compose(self) -> ComposeResult:
        with Vertical(classes="explanation-dialog"):
            yield Label("ARCHITECTURAL EXECUTION EXPLANATION", classes="explanation-title")
            with VerticalScroll():
                yield Static(self.text)
            with Horizontal(classes="confirm-buttons"):
                yield Button("Close (Esc)", id="btn-close", variant="primary")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self.dismiss(None)

    def on_key(self, event) -> None:
        if event.key in ("escape", "enter", "q"):
            self.dismiss(None)


def extract_post_run_summary(
    store: Any,
    target_run_id: str | None = None,
    prep_run_id: str | None = None,
) -> dict[str, str]:
    """Extract truthful, evidence-derived post-run summary without private LLM thoughts."""
    with store._lock:
        run_id = target_run_id or store.selected_run_id or (store.root_run_ids[-1] if store.root_run_ids else None)
        if not run_id or run_id not in store.runs:
            return {}

        run = store.runs[run_id]
        tree_runs = store._collect_descendant_runs(run)
        tree_run_ids = {r.run_id for r in tree_runs}

        # 1. Memory HIT/MISS/not evaluated & Memory Firewall quarantine
        memory_status = "not evaluated"
        for ev in store.raw_events:
            if isinstance(ev, MemoryOperationEvent) and ev.run_id in tree_run_ids:
                if ev.entry_count > 0:
                    memory_status = f"HIT ({ev.entry_count} {'entry' if ev.entry_count == 1 else 'entries'} retrieved)"
                    break
                else:
                    memory_status = "MISS (0 entries found)"

        # Check for quarantined untrusted memory directive within tree or correlated prep session
        has_quarantine = any(
            isinstance(ev, MemoryOperationEvent)
            and (ev.run_id in tree_run_ids or (prep_run_id is not None and ev.run_id == prep_run_id))
            and (
                getattr(ev.operation_type, "value", ev.operation_type) == "quarantine"
                or getattr(ev.status, "value", ev.status) == "quarantined"
            )
            for ev in store.raw_events
        )
        if has_quarantine:
            if memory_status != "not evaluated":
                memory_status = f"{memory_status} · Memory Firewall: untrusted directive quarantined"
            else:
                memory_status = "Memory Firewall: untrusted directive quarantined"

        # 2. Specialist Delegation
        delegation_status = "orchestrator direct"
        if run.children_run_ids:
            child_roles = []
            for cid in run.children_run_ids:
                cnode = store.runs.get(cid)
                if cnode:
                    child_roles.append(cnode.agent_role)
            if child_roles:
                delegation_status = f"delegated to {', '.join(child_roles)}"

        # 3. MCP Tool Invocation
        all_tools: list[tuple[Any, Any]] = []
        for r in tree_runs:
            for t in r.tool_calls_map.values():
                all_tools.append((r, t))

        mcp_tools = []
        for r, t in all_tools:
            is_mcp = (
                t.tool_source == ToolSource.MCP
                or (hasattr(t.tool_source, "value") and t.tool_source.value == "mcp")
                or (t.canonical_identity and t.canonical_identity.startswith("mcp:"))
            )
            if is_mcp:
                mcp_tools.append(t)

        if mcp_tools:
            mcp_names = [f"{t.tool_name} ({t.server_name or 'mcp'})" for t in mcp_tools]
            seen_mcp = set()
            uniq_mcp = []
            for n in mcp_names:
                if n not in seen_mcp:
                    seen_mcp.add(n)
                    uniq_mcp.append(n)
            mcp_status = ", ".join(uniq_mcp)
        else:
            mcp_status = "not invoked"

        # 4. Contextual Permission Outcome
        confirmed_tools = []
        denied_tools = []
        for r, t in all_tools:
            if t.confirmation_outcome == "APPROVED":
                confirmed_tools.append(f"{t.tool_name} → APPROVED")
            elif t.confirmation_outcome == "REJECTED":
                denied_tools.append(f"{t.tool_name} → REJECTED")

        if confirmed_tools:
            gov_status = ", ".join(confirmed_tools)
        elif denied_tools:
            gov_status = ", ".join(denied_tools)
        elif any(t.risk_level for _, t in all_tools):
            gov_status = "allowed by policy"
        else:
            gov_status = "allowed by policy"

        # 5. Workspace File Creation / Containment
        created_files = []
        escape_blocked = False
        for r, t in all_tools:
            if t.is_error and t.error_message and (
                "escape" in t.error_message.lower()
                or "traversal" in t.error_message.lower()
                or "outside workspace" in t.error_message.lower()
            ):
                escape_blocked = True
            if t.tool_name in ("create_file", "write_file") and not t.is_error:
                target = (
                    t.arguments_summary.get("path")
                    or t.arguments_summary.get("file_path")
                    or t.resource_descriptor
                )
                if target:
                    basename = os.path.basename(target.strip("'\""))
                    created_files.append(basename if basename else target)

        if escape_blocked:
            ws_status = "boundary escape blocked"
        elif created_files:
            uniq_files = list(dict.fromkeys(created_files))
            ws_status = f"{', '.join(uniq_files)} created inside workspace"
        else:
            ws_status = "path resolved within configured root"

        # 6. Trace Context Recorded
        trace_status = f"recorded ({run.trace_id[:8]})" if run.trace_id else "not recorded"

        return {
            "run_id": run.run_id,
            "agent_role": run.agent_role,
            "status": run.status,
            "duration": f"{run.duration_seconds:.2f}s",
            "memory": memory_status,
            "delegation": delegation_status,
            "mcp": mcp_status,
            "governance": gov_status,
            "workspace": ws_status,
            "trace": trace_status,
        }


class PostRunSummaryModal(ModalScreen[str | None]):
    """Concise 6-row evidence summary modal shown after an evaluator scenario run."""

    DEFAULT_CSS = f"""
    PostRunSummaryModal {{
        align: center middle;
    }}
    .summary-dialog {{
        width: 82;
        height: auto;
        background: {PANEL_BG};
        border: thick {SUCCESS};
        padding: 1 2;
    }}
    .summary-title {{
        text-style: bold;
        color: {SUCCESS};
        margin-bottom: 0;
    }}
    .summary-subtitle {{
        color: {TEXT_MUTED};
        margin-bottom: 1;
        border-bottom: solid {BORDER};
        padding-bottom: 1;
    }}
    .summary-row {{
        height: 1;
        margin-bottom: 0;
    }}
    .summary-label {{
        width: 26;
        color: {TEXT_MUTED};
    }}
    .summary-val {{
        color: #FFFFFF;
        text-style: bold;
    }}
    .summary-buttons {{
        margin-top: 1;
        height: 3;
        align: right middle;
    }}
    """

    def __init__(self, summary: dict[str, str], *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.summary = summary

    def compose(self) -> ComposeResult:
        run_short = self.summary.get("run_id", "")[:8]
        status = self.summary.get("status", "SUCCESS")
        duration = self.summary.get("duration", "0.00s")
        agent_role = self.summary.get("agent_role", "orchestrator")

        with Vertical(classes="summary-dialog"):
            yield Label(f"EXECUTION EVIDENCE: RUN {run_short}", classes="summary-title")
            yield Label(f"Role: {agent_role}  |  Status: {status}  |  Duration: {duration}", classes="summary-subtitle")

            with Horizontal(classes="summary-row"):
                yield Label("Memory Lifecycle:", classes="summary-label")
                yield Label(self.summary.get("memory", "not evaluated"), classes="summary-val")

            with Horizontal(classes="summary-row"):
                yield Label("Specialist Delegation:", classes="summary-label")
                yield Label(self.summary.get("delegation", "orchestrator direct"), classes="summary-val")

            with Horizontal(classes="summary-row"):
                yield Label("MCP Tool Invocation:", classes="summary-label")
                yield Label(self.summary.get("mcp", "not invoked"), classes="summary-val")

            with Horizontal(classes="summary-row"):
                yield Label("Contextual Governance:", classes="summary-label")
                yield Label(self.summary.get("governance", "allowed by policy"), classes="summary-val")

            with Horizontal(classes="summary-row"):
                yield Label("Workspace Containment:", classes="summary-label")
                yield Label(self.summary.get("workspace", "path resolved within configured root"), classes="summary-val")

            with Horizontal(classes="summary-row"):
                yield Label("Distributed Tracing:", classes="summary-label")
                yield Label(self.summary.get("trace", "not recorded"), classes="summary-val")

            with Horizontal(classes="summary-buttons"):
                yield Button("Explain (E)", id="btn-explain", variant="primary")
                yield Button("Trace (T)", id="btn-trace")
                yield Button("Security (S)", id="btn-security")
                yield Button("Close (Esc)", id="btn-close")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn-explain":
            self.dismiss("explain")
        elif event.button.id == "btn-trace":
            self.dismiss("trace")
        elif event.button.id == "btn-security":
            self.dismiss("security")
        elif event.button.id == "btn-close":
            self.dismiss(None)

    def on_key(self, event) -> None:
        if event.key in ("e", "E"):
            self.dismiss("explain")
        elif event.key in ("t", "T"):
            self.dismiss("trace")
        elif event.key in ("s", "S"):
            self.dismiss("security")
        elif event.key in ("escape", "enter", "q"):
            self.dismiss(None)


@dataclass(frozen=True)
class CustomPromptSubmission:
    """Structured submission from CustomPromptModal distinguishing fresh tasks from continued turns."""

    prompt: str
    fresh_task: bool = True

    def __iter__(self):
        return iter((self.prompt, self.fresh_task))


class MultilinePromptArea(TextArea):
    """Multiline prompt editor with value compatibility property for Input-like access."""

    @property
    def value(self) -> str:
        return self.text

    @value.setter
    def value(self, val: str) -> None:
        self.text = val


class CustomPromptModal(ModalScreen[CustomPromptSubmission | str | None]):
    """Modal dialog allowing custom agent instruction entry with explicit session isolation choice."""

    BINDINGS = [
        Binding("ctrl+enter,ctrl+j", "submit", "Submit", show=True),
        Binding("escape", "cancel", "Cancel", show=True),
    ]

    DEFAULT_CSS = f"""
    CustomPromptModal {{
        align: center middle;
    }}
    .custom-prompt-dialog {{
        width: 78;
        height: auto;
        background: {PANEL_BG};
        border: thick {TEXT_ACCENT};
        padding: 1 2;
    }}
    .custom-prompt-title {{
        text-style: bold;
        color: {TEXT_ACCENT};
        margin-bottom: 1;
        border-bottom: solid {BORDER};
        padding-bottom: 1;
    }}
    .custom-prompt-input {{
        margin: 1 0;
        height: 7;
        border: solid {BORDER};
    }}
    .custom-prompt-hint {{
        color: {TEXT_MUTED};
        margin-bottom: 1;
    }}
    .custom-prompt-mode-label {{
        color: {TEXT_MUTED};
        text-style: bold;
        margin-top: 0;
    }}
    #custom-prompt-mode {{
        background: transparent;
        border: none;
        margin-top: 0;
        margin-bottom: 1;
        height: auto;
    }}
    .custom-prompt-buttons {{
        margin-top: 1;
        height: 3;
        align: right middle;
    }}
    """

    def compose(self) -> ComposeResult:
        with Vertical(classes="custom-prompt-dialog"):
            yield Label("CUSTOM AGENT PROMPT", classes="custom-prompt-title")
            yield Static("Enter custom prompt/instruction for the agent. Tools, memory, and governance rules remain active.")
            yield MultilinePromptArea(
                id="custom-prompt-input",
                classes="custom-prompt-input",
                show_line_numbers=False,
                soft_wrap=True,
            )
            yield Static(
                "[bold #58a6ff]Ctrl+Enter[/] Submit   [bold #B0B8C4]Esc[/] Cancel   [#B0B8C4]Enter Newline[/#B0B8C4]",
                classes="custom-prompt-hint",
            )
            yield Label("Session Mode:", classes="custom-prompt-mode-label")
            with RadioSet(id="custom-prompt-mode"):
                yield RadioButton("New evaluation task (fresh short-term context)", value=True, id="mode-new")
                yield RadioButton("Continue current conversation (preserve history)", value=False, id="mode-continue")
            with Horizontal(classes="custom-prompt-buttons"):
                yield Button("Submit (Ctrl+Enter)", id="btn-submit", variant="primary")
                yield Button("Cancel (Esc)", id="btn-cancel")

    def on_mount(self) -> None:
        inp = self.query_one("#custom-prompt-input", TextArea)
        inp.focus()

    def _get_submission(self) -> CustomPromptSubmission | None:
        inp = self.query_one("#custom-prompt-input", TextArea)
        raw_text = inp.text.replace("\r\n", "\n")
        val = raw_text.strip()
        if not val:
            return None
        is_fresh = True
        try:
            rs = self.query_one("#custom-prompt-mode", RadioSet)
            if rs.pressed_button and rs.pressed_button.id == "mode-continue":
                is_fresh = False
        except Exception:
            is_fresh = True
        return CustomPromptSubmission(prompt=val, fresh_task=is_fresh)

    def action_submit(self) -> None:
        self.dismiss(self._get_submission())

    def action_cancel(self) -> None:
        self.dismiss(None)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn-submit":
            self.action_submit()
        elif event.button.id == "btn-cancel":
            self.action_cancel()

    def on_input_submitted(self, event: Any) -> None:
        self.action_submit()

    def on_key(self, event) -> None:
        if event.key in ("ctrl+enter", "ctrl+j"):
            event.prevent_default()
            event.stop()
            self.action_submit()
        elif event.key == "escape":
            event.prevent_default()
            event.stop()
            self.action_cancel()

