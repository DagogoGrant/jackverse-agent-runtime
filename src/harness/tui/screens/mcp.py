"""MCP Servers View: Real-time circuit breaker state, retry counters, and resilience metrics."""

from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Container, Horizontal, Vertical, VerticalScroll
from textual.widgets import DataTable, Label, Static

from harness.tui.store import RuntimeStateStore
from harness.tui.theme import BORDER, PANEL_BG, TEXT_ACCENT, TEXT_MUTED


class MCPScreen(Container):
    """Operator screen for inspecting MCP external servers, resilience FSM, and tool discovery."""

    DEFAULT_CSS = f"""
    MCPScreen {{
        layout: horizontal;
        padding: 1;
        height: 1fr;
    }}
    .mcp-table-pane {{
        width: 50%;
        height: 1fr;
        border-right: solid {BORDER};
        padding-right: 1;
    }}
    .mcp-detail-pane {{
        width: 50%;
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
    .mcp-detail-card {{
        background: {PANEL_BG};
        border: solid {BORDER};
        padding: 1;
        height: 1fr;
    }}
    """

    def __init__(self, store: RuntimeStateStore, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.store = store
        self._selected_server: str | None = None

    def compose(self) -> ComposeResult:
        with Vertical(classes="mcp-table-pane"):
            yield Label("CONFIGURED MCP SERVERS & RESILIENCE FSM", classes="pane-title")
            table = DataTable(id="mcp-table", cursor_type="row")
            table.add_columns("SERVER", "TRANSPORT", "TOOLS", "CIRCUIT", "FAILURES", "RETRIES")
            yield table

        with Vertical(classes="mcp-detail-pane"):
            yield Label("CIRCUIT BREAKER & RESILIENCE INSPECTOR", classes="pane-title")
            yield VerticalScroll(
                Static("Select an MCP server from the table to inspect circuit breaker status and discovered tools.", id="mcp-detail-text"),
                classes="mcp-detail-card",
            )

    def on_mount(self) -> None:
        self.refresh_mcp()

    def refresh_mcp(self) -> None:
        table = self.query_one("#mcp-table", DataTable)
        table.clear()

        for s_name, card in self.store.mcp_servers.items():
            state_color = "#3FB950" if card.circuit_state == "CLOSED" else ("#F2CC60" if card.circuit_state == "HALF_OPEN" else "#FF7B72")
            state_icon = "●" if card.circuit_state == "CLOSED" else ("▲" if card.circuit_state == "HALF_OPEN" else "✗")
            circuit_str = f"[bold {state_color}]{state_icon} {card.circuit_state}[/bold {state_color}]"
            fails_str = f"{card.consecutive_failures}/{card.failure_threshold}"
            retries_str = str(card.retries_count)

            table.add_row(
                s_name,
                card.transport_type,
                str(card.tools_count),
                circuit_str,
                fails_str,
                retries_str,
                key=s_name,
            )

        if self.store.mcp_servers and not self._selected_server:
            first_s = next(iter(self.store.mcp_servers))
            self.inspect_server(first_s)

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        s_name = str(event.row_key.value)
        self.inspect_server(s_name)

    def inspect_server(self, server_name: str) -> None:
        card = self.store.mcp_servers.get(server_name)
        if not card:
            return
        self._selected_server = server_name

        state_color = "#3FB950" if card.circuit_state == "CLOSED" else ("#F2CC60" if card.circuit_state == "HALF_OPEN" else "#FF7B72")
        state_icon = "●" if card.circuit_state == "CLOSED" else ("▲" if card.circuit_state == "HALF_OPEN" else "✗")
        tools_formatted = "\n".join(f"    • [cyan]{t}[/cyan]" for t in card.tools_list) or "    • [#B0B8C4]None discovered[/#B0B8C4]"

        # Recent events for this server
        recent_events = [
            ev for ev in self.store.timeline if server_name in ev.title or server_name in ev.details
        ]
        ev_lines = []
        for ev in recent_events[-5:]:
            ev_lines.append(f"  • [#B0B8C4]+{ev.relative_seconds:05.2f}s[/#B0B8C4] {ev.icon} {ev.title} {ev.details}")
        ev_str = "\n".join(ev_lines) or "  • [#B0B8C4]No recent resilience events for this server[/#B0B8C4]"

        text = (
            f"[bold cyan]MCP SERVER: {card.server_name}[/bold cyan]\n"
            f"[bold]Transport Protocol:[/bold]  {card.transport_type}\n"
            f"[bold]Target Endpoint:[/bold]     {card.endpoint or 'localhost'}\n"
            f"[bold]Discovered Tools:[/bold]    {card.tools_count}\n"
            f"\n[bold yellow]CIRCUIT BREAKER STATE MACHINE (Feature B)[/bold yellow]\n"
            f"  Current FSM State:     [bold {state_color}]{state_icon} {card.circuit_state}[/bold {state_color}]\n"
            f"  Consecutive Failures:  {card.consecutive_failures} (Threshold: {card.failure_threshold})\n"
            f"  Cooldown Duration:     {card.cooldown_seconds}s (Monotonic clock)\n"
            f"  Probe Semantics:       Single-probe allowed in HALF_OPEN\n"
            f"  Accounting Level:      Logical operations (post-retry exhaustion)\n"
            f"\n[bold yellow]IDEMPOTENCY & RETRY CONFIGURATION[/bold yellow]\n"
            f"  Total Retries Emitted: {card.retries_count}\n"
            f"  Backoff Strategy:      Deterministic bounded exponential backoff (no random jitter)\n"
            f"  Mutation Protection:   Non-idempotent/mutating operations are never retried in flight\n"
            f"\n[bold yellow]DISCOVERED CAPABILITIES[/bold yellow]\n"
            f"{tools_formatted}\n"
            f"\n[bold yellow]RECENT RESILIENCE EVENTS[/bold yellow]\n"
            f"{ev_str}\n"
        )
        self.query_one("#mcp-detail-text", Static).update(text)
