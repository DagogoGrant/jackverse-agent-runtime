"""Tools View: Unified tool catalog with context-aware policy indicators and schema inspection."""

from __future__ import annotations

import json
from textual.app import ComposeResult
from textual.containers import Container, Horizontal, Vertical, VerticalScroll
from textual.widgets import DataTable, Label, Static

from harness.tools.base import ToolSource
from harness.tui.store import RuntimeStateStore
from harness.tui.theme import BORDER, PANEL_BG, TEXT_ACCENT, TEXT_MUTED


class ToolsScreen(Container):
    """Operator screen displaying registered tools, mutation classes, dynamic policy, and schema details."""

    DEFAULT_CSS = f"""
    ToolsScreen {{
        layout: horizontal;
        padding: 1;
        height: 1fr;
    }}
    .tools-table-pane {{
        width: 52%;
        height: 1fr;
        border-right: solid {BORDER};
        padding-right: 1;
    }}
    .tools-detail-pane {{
        width: 48%;
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
    .tool-detail-card {{
        background: {PANEL_BG};
        border: solid {BORDER};
        padding: 1;
        height: 1fr;
    }}
    """

    def __init__(self, store: RuntimeStateStore, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.store = store
        self._selected_tool: str | None = None

    def compose(self) -> ComposeResult:
        with Vertical(classes="tools-table-pane"):
            yield Label("UNIFIED TOOL CATALOG (BUILT-IN & MCP)", classes="pane-title")
            table = DataTable(id="tools-table", cursor_type="row")
            table.add_columns("TOOL NAME", "SOURCE", "MUTATION", "AUTHORIZATION", "CALLS (OK/ERR)")
            yield table

        with Vertical(classes="tools-detail-pane"):
            yield Label("TOOL SPECIFICATION & SCHEMA INSPECTOR", classes="pane-title")
            yield VerticalScroll(
                Static("Select a tool to inspect canonical identity, parameters schema, and policy behavior.", id="tool-detail-text"),
                classes="tool-detail-card",
            )

    def on_mount(self) -> None:
        self.refresh_tools()

    def refresh_tools(self) -> None:
        table = self.query_one("#tools-table", DataTable)
        table.clear()

        for name, card in self.store.tools.items():
            src_str = "[cyan]MCP[/cyan]" if card.source == ToolSource.MCP else "[dim]Built-in[/dim]"
            mut_str = "[bold yellow]MUTATING[/bold yellow]" if card.is_mutating else "[dim]READ-ONLY[/dim]"
            # Contextual policy indicator per architectural correction #2
            policy_str = "[green]Context-aware[/green]"
            calls_str = f"{card.call_count} ({card.success_count}/{card.error_count})"

            table.add_row(name, src_str, mut_str, policy_str, calls_str, key=name)

        if self.store.tools and not self._selected_tool:
            first_tool = next(iter(self.store.tools))
            self.inspect_tool(first_tool)

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        name = str(event.row_key.value)
        self.inspect_tool(name)

    def inspect_tool(self, tool_name: str) -> None:
        card = self.store.tools.get(tool_name)
        if not card:
            return
        self._selected_tool = tool_name

        schema_json = json.dumps(card.parameters_schema, indent=2) if card.parameters_schema else "{}"

        # Check recent governance decisions for this tool
        recent_decisions = [
            r for r in self.store.governance_log if r.tool_name == tool_name
        ]
        audit_lines = []
        for r in recent_decisions[-4:]:
            dec_color = "green" if r.decision.value == "allow" else ("yellow" if "confirm" in r.decision.value else "red")
            audit_lines.append(
                f"  • Resource: {r.resource_descriptor[:30]} → [{dec_color}]{r.decision.value.upper()}[/{dec_color}] "
                f"(Risk: {r.risk_level.value.upper()}, Rule: {r.matched_rule or 'default'})"
            )
        audit_str = "\n".join(audit_lines) or "  • [dim]No recent invocations in current session[/dim]"

        text = (
            f"[bold cyan]TOOL: {card.name}[/bold cyan]\n"
            f"[bold]Canonical Identity:[/bold] {card.canonical_identity}\n"
            f"[bold]Source Provenance:[/bold]  {card.source.value if hasattr(card.source, 'value') else card.source}\n"
            f"[bold]Owning Server:[/bold]      {card.server_name or 'Harness Internal Subsystem'}\n"
            f"[bold]Mutation Class:[/bold]     {'MUTATING (Can modify workspace state)' if card.is_mutating else 'READ-ONLY (Idempotent query)'}\n"
            f"[bold]Retry Eligibility:[/bold]  {'NON-RETRYABLE in flight' if card.is_mutating else 'RETRYABLE on transient failures'}\n"
            f"[bold]Session Invocations:[/bold]{card.call_count} total ({card.success_count} success, {card.error_count} error)\n"
            f"\n[bold yellow]CONTEXTUAL AUTHORIZATION BEHAVIOR[/bold yellow]\n"
            f"Policy stance: Evaluated dynamically per-resource and per-argument.\n"
            f"Recent decisions for this tool:\n"
            f"{audit_str}\n"
            f"\n[bold yellow]DESCRIPTION[/bold yellow]\n"
            f"[#B0B8C4]{card.description or 'No description provided.'}[/#B0B8C4]\n"
            f"\n[bold yellow]PARAMETERS SCHEMA (JSON)[/bold yellow]\n"
            f"[#B0B8C4]{schema_json}[/#B0B8C4]\n"
        )
        self.query_one("#tool-detail-text", Static).update(text)
