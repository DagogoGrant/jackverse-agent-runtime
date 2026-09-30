"""Security & Governance View: Contextual authorization, rule precedence, and containment boundaries."""

from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Container, Horizontal, Vertical, VerticalScroll
from textual.widgets import DataTable, Label, Static

from harness.config import AppConfig
from harness.tui.store import RuntimeStateStore
from harness.tui.theme import BORDER, PANEL_BG, TEXT_ACCENT, TEXT_MUTED


class SecurityScreen(Container):
    """Operator screen for verifying Feature A contextual permissions and hard workspace containment."""

    DEFAULT_CSS = f"""
    SecurityScreen {{
        layout: vertical;
        padding: 1;
        height: 1fr;
    }}
    .security-top {{
        height: 40%;
        layout: horizontal;
        border-bottom: solid {BORDER};
        padding-bottom: 1;
        margin-bottom: 1;
    }}
    .boundary-card {{
        width: 35%;
        background: {PANEL_BG};
        border: solid {BORDER};
        padding: 1;
    }}
    .rules-card {{
        width: 65%;
        background: {PANEL_BG};
        border: solid {BORDER};
        padding: 1;
    }}
    .security-bottom {{
        height: 60%;
        layout: horizontal;
    }}
    .audit-pane {{
        width: 60%;
        height: 1fr;
        border-right: solid {BORDER};
        padding-right: 1;
    }}
    .audit-detail-pane {{
        width: 40%;
        height: 1fr;
        padding-left: 1;
    }}
    .pane-title {{
        text-style: bold;
        color: {TEXT_ACCENT};
        margin-bottom: 1;
        border-bottom: solid {BORDER};
        padding-bottom: 1;
    }}
    """

    def __init__(self, store: RuntimeStateStore, config: AppConfig, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.store = store
        self.config = config
        self._selected_audit_idx: int | None = None

    def compose(self) -> ComposeResult:
        with Horizontal(classes="security-top"):
            # Left Top: Architectural Layers & Containment
            with Vertical(classes="boundary-card"):
                yield Label("SECURITY LAYERS & CONTAINMENT", classes="pane-title")
                yield Static(
                    "[bold yellow]1. HARD WORKSPACE BARRIER[/bold yellow]\n"
                    "Enforced unconditionally by [cyan]Workspace.resolve()[/cyan].\n"
                    "Prevents directory traversal (e.g. [dim]../../etc/passwd[/dim]).\n"
                    "Boundary escape is [bold #FF7B72]IMPOSSIBLE[/bold #FF7B72] regardless of policy rules.\n\n"
                    "[bold #3FB950]2. CONTEXTUAL POLICY ENGINE[/bold #3FB950]\n"
                    "Default Stance: [bold #FF7B72]DENY[/bold #FF7B72]\n"
                    "Deterministic evaluation by highest-priority matching rule.\n"
                    "Considers tool, resource pattern, risk level, and argument match.",
                )

            # Right Top: Active Policy Rules Table
            with Vertical(classes="rules-card"):
                yield Label(f"ACTIVE POLICY RULES ({len(self.config.permissions.rules)} rules)", classes="pane-title")
                rules_table = DataTable(id="rules-table")
                rules_table.add_columns("PRIORITY", "RULE NAME", "TOOL PATTERN", "RESOURCE PATTERN", "RISK", "DECISION")
                yield rules_table

        with Horizontal(classes="security-bottom"):
            # Left Bottom: Recent Decision Audit Log
            with Vertical(classes="audit-pane"):
                yield Label("GOVERNANCE AUDIT LOG", classes="pane-title")
                audit_table = DataTable(id="audit-table", cursor_type="row")
                audit_table.add_columns("TIME", "TOOL", "RISK", "DECISION", "CONFIRMATION", "RULE")
                yield audit_table

            # Right Bottom: Decision Inspector
            with Vertical(classes="audit-detail-pane"):
                yield Label("AUTHORIZATION INSPECTOR", classes="pane-title")
                yield VerticalScroll(
                    Static("Select a decision from the audit log to inspect evaluation evidence.", id="security-detail-text"),
                    classes="detail-card",
                )

    def on_mount(self) -> None:
        self.refresh_rules()
        self.refresh_audit_log()

    def refresh_rules(self) -> None:
        table = self.query_one("#rules-table", DataTable)
        table.clear()

        # Sort rules descending by priority
        sorted_rules = sorted(
            self.config.permissions.rules,
            key=lambda r: getattr(r, "priority", 0),
            reverse=True,
        )
        for r in sorted_rules:
            prio_str = str(getattr(r, "priority", 0))
            name_str = r.name
            tool_pat = r.tool_pattern or "*"
            res_pat = getattr(r, "resource_pattern", None) or "*"
            risk_val = getattr(r, "risk_level", None) or "*"
            if r.decision == "allow":
                dec_str = "[bold #3FB950]✓ ALLOW[/bold #3FB950]"
            elif "confirm" in r.decision:
                dec_str = "[bold #F2CC60]? REQUIRE_CONFIRMATION[/bold #F2CC60]"
            else:
                dec_str = "[bold #FF7B72]✗ DENY[/bold #FF7B72]"

            table.add_row(prio_str, name_str, tool_pat, res_pat, str(risk_val), dec_str)

    def refresh_audit_log(self) -> None:
        table = self.query_one("#audit-table", DataTable)
        table.clear()

        records = list(self.store.governance_log)
        for idx, rec in enumerate(reversed(records)):
            rel_str = f"[#B0B8C4]+{rec.relative_seconds:05.2f}s[/#B0B8C4]"
            if rec.decision.value == "allow":
                dec_str = "[bold #3FB950]✓ ALLOW[/bold #3FB950]"
            elif "confirm" in rec.decision.value:
                dec_str = "[bold #F2CC60]? REQUIRE_CONFIRMATION[/bold #F2CC60]"
            else:
                dec_str = "[bold #FF7B72]✗ DENY[/bold #FF7B72]"
            confirm_str = rec.confirmation_outcome or "-"
            risk_str = rec.risk_level.value.upper()
            rule_str = rec.matched_rule or "default_stance"

            table.add_row(rel_str, rec.tool_name, risk_str, dec_str, confirm_str, rule_str, key=str(idx))

        if records and self._selected_audit_idx is None:
            self.inspect_record(records[-1])

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        idx_str = str(event.row_key.value)
        try:
            records = list(reversed(list(self.store.governance_log)))
            idx = int(idx_str)
            if 0 <= idx < len(records):
                self.inspect_record(records[idx])
        except Exception:
            pass

    def inspect_record(self, rec) -> None:
        text = (
            f"[bold cyan]AUTHORIZATION EVALUATION EVIDENCE[/bold cyan]\n"
            f"[dim]────────────────────────────────────────[/dim]\n"
            f"[bold]Timestamp:[/bold]          +{rec.relative_seconds:05.2f}s\n"
            f"[bold]Tool Invocation:[/bold]    {rec.tool_name}\n"
            f"[bold]Resource Target:[/bold]    {rec.resource_descriptor}\n"
            f"[bold]Risk Classification:[/bold] {rec.risk_level.value.upper()}\n"
            f"[bold]Matched Rule:[/bold]       {rec.matched_rule or 'No rule matched (Default Stance Applied)'}\n"
            f"[bold]Evaluated Decision:[/bold] {rec.decision.value.upper()}\n"
        )
        if rec.confirmation_outcome:
            text += f"[bold]Confirmation Resolution:[/bold] {rec.confirmation_outcome}\n"
        if rec.is_boundary_violation:
            text += "\n[bold red]CRITICAL: Workspace Boundary Violation Attempt Blocked at Containment Barrier![/bold red]\n"

        text += (
            f"\n[bold yellow]DETERMINISTIC EVALUATION REASONING[/bold yellow]\n"
            f"1. Request was classified as risk level '{rec.risk_level.value.upper()}'.\n"
            f"2. PolicyEngine evaluated configured rules in descending priority order.\n"
            f"3. Winner was rule '{rec.matched_rule or 'default'}' yielding decision '{rec.decision.value.upper()}'.\n\n"
            f"[dim]Note: This audit is derived from deterministic policy evaluation and\n"
            f"is completely independent of model internal reasoning.[/dim]\n"
        )
        self.query_one("#security-detail-text", Static).update(text)
