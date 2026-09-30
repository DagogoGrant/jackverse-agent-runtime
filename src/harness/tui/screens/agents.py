"""Agents View: Discoverable specialist profiles, tool allowances, budgets, and delegation stats."""

from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Container, Horizontal, Vertical, VerticalScroll
from textual.widgets import DataTable, Label, Static

from harness.tui.store import RuntimeStateStore
from harness.tui.theme import BORDER, PANEL_BG, TEXT_ACCENT, TEXT_MUTED


class AgentsScreen(Container):
    """Operator screen for discovering available agent roles and least-privilege configurations."""

    DEFAULT_CSS = f"""
    AgentsScreen {{
        layout: horizontal;
        padding: 1;
        height: 1fr;
    }}
    .agents-table-pane {{
        width: 45%;
        height: 1fr;
        border-right: solid {BORDER};
        padding-right: 1;
    }}
    .agents-detail-pane {{
        width: 55%;
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
    .detail-card {{
        background: {PANEL_BG};
        border: solid {BORDER};
        padding: 1;
        height: 1fr;
    }}
    """

    def __init__(self, store: RuntimeStateStore, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.store = store
        self._selected_role: str | None = None

    def compose(self) -> ComposeResult:
        with Vertical(classes="agents-table-pane"):
            yield Label("CONFIGURED AGENTS & SPECIALISTS", classes="pane-title")
            table = DataTable(id="agents-table", cursor_type="row")
            table.add_columns("ROLE", "TYPE", "TOOLS", "MEMORY", "DELEGATIONS")
            yield table

        with Vertical(classes="agents-detail-pane"):
            yield Label("AGENT SPECIFICATION INSPECTOR", classes="pane-title")
            yield VerticalScroll(
                Static("Select an agent profile from the table to inspect details.", id="agent-detail-text"),
                classes="detail-card",
            )

    def on_mount(self) -> None:
        self.refresh_agents()

    def refresh_agents(self) -> None:
        table = self.query_one("#agents-table", DataTable)
        table.clear()

        for role, card in self.store.agents.items():
            type_tag = "[bold cyan]Root[/bold cyan]" if "Root" in card.agent_type else "[magenta]Specialist[/magenta]"
            tools_cnt = str(len(card.allowed_tool_ids))
            mem_tag = f"[green]{card.memory_access}[/green]" if card.memory_access != "NONE" else "[dim]NONE[/dim]"
            del_cnt = str(card.delegations_count)
            table.add_row(role, type_tag, tools_cnt, mem_tag, del_cnt, key=role)

        if self.store.agents and not self._selected_role:
            first_role = next(iter(self.store.agents))
            self.inspect_agent(first_role)

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        role = str(event.row_key.value)
        self.inspect_agent(role)

    def inspect_agent(self, role: str) -> None:
        card = self.store.agents.get(role)
        if not card:
            return
        self._selected_role = role

        tools_formatted = "\n".join(f"    • [cyan]{t}[/cyan]" for t in card.allowed_tool_ids) or "    • [dim]None[/dim]"
        b = card.budget_ceiling

        text = (
            f"[bold cyan]ROLE: {card.role}[/bold cyan]\n"
            f"[bold]Profile Type:[/bold]      {card.agent_type}\n"
            f"[bold]Inference Model:[/bold]   {card.model_id or 'Inherited default'}\n"
            f"[bold]Memory Access:[/bold]      {card.memory_access}\n"
            f"[bold]Total Delegations:[/bold]  {card.delegations_count}\n"
            f"[bold]Active Duration:[/bold]    {card.total_duration_seconds:.2f}s\n"
            f"\n[bold yellow]EXECUTION BUDGET CEILING[/bold yellow]\n"
            f"  Max Turn Steps:       {b.max_steps}\n"
            f"  Max Tool Calls:       {b.max_tool_calls}\n"
            f"  Max Runtime Seconds:  {b.max_runtime_seconds}s\n"
            f"  Max Obs Characters:   {b.max_observation_chars}\n"
            f"\n[bold yellow]LEAST-PRIVILEGE CAPABILITIES ({len(card.allowed_tool_ids)} tools)[/bold yellow]\n"
            f"{tools_formatted}\n"
            f"\n[bold yellow]SYSTEM PROMPT[/bold yellow]\n"
            f"[#B0B8C4]{card.system_prompt}[/#B0B8C4]\n"
        )
        self.query_one("#agent-detail-text", Static).update(text)
