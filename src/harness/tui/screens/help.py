"""Help & Documentation Screen: Keyboard shortcuts, operational cheatsheet, and telemetry links."""

from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Container, Horizontal, Vertical, VerticalScroll
from textual.widgets import Label, Static

from harness.tui.observability import get_grafana_base_url, get_grafana_dashboard_url
from harness.tui.theme import BORDER, PANEL_BG, TEXT_ACCENT, TEXT_MUTED


class HelpScreen(VerticalScroll):
    """Operator screen explaining navigation, keybindings, and external telemetry handoffs."""

    DEFAULT_CSS = f"""
    HelpScreen {{
        padding: 1;
    }}
    .help-card {{
        background: {PANEL_BG};
        border: solid {BORDER};
        padding: 1;
        margin-bottom: 1;
    }}
    .card-title {{
        text-style: bold;
        color: {TEXT_ACCENT};
        margin-bottom: 1;
        border-bottom: solid {BORDER};
        padding-bottom: 1;
    }}
    """

    def compose(self) -> ComposeResult:
        yield Label("OPERATOR CONSOLE CHEATSHEET & OBSERVABILITY HANDOFF", classes="panel-title")

        with Vertical(classes="help-card"):
            yield Label("1. NAVIGATION & SHORTCUTS", classes="card-title")
            yield Static(
                "[bold cyan]Tabs / Screens:[/bold cyan]\n"
                "  [bold]1[/bold]  Overview               (System readiness & dynamic capabilities)\n"
                "  [bold]2[/bold]  Runs & Execution Tree  (Topology tree, flight recorder, deep inspector)\n"
                "  [bold]3[/bold]  Agents & Specialists   (Discover least-privilege profiles & budgets)\n"
                "  [bold]4[/bold]  Tools Catalog          (Mutation classes, context-dependent policies, schemas)\n"
                "  [bold]5[/bold]  MCP External Servers   (Circuit breaker FSM, idempotency & retries)\n"
                "  [bold]6[/bold]  Security & Governance  (Workspace containment vs contextual rules)\n"
                "  [bold]7[/bold]  Events History         (Bounded searchable lifecycle event stream)\n"
                "  [bold]8[/bold]  System Scenarios       (1-click guided runtime workflows with checkpoints)\n"
                "  [bold]9[/bold]  Help & Documentation   (This operational guide)\n\n"
                "[bold cyan]Operator Actions:[/bold cyan]\n"
                "  [bold]j / k[/bold] or [bold]↑ / ↓[/bold]  Navigate tree nodes, tables, and event records\n"
                "  [bold]Enter[/bold]            Inspect selected node or item\n"
                "  [bold]Ctrl+P[/bold]           Open Command Palette\n"
                "  [bold]G[/bold]                Display Grafana dashboard link & verification hints\n"
                "  [bold]T[/bold]                Display Tempo trace explorer link for active/selected run\n"
                "  [bold]E[/bold]                Reconstruct architectural explanation from state\n"
                "  [bold]Q[/bold]                Quit Operator Console safely\n",
            )

        with Vertical(classes="help-card"):
            yield Label("2. OBSERVABILITY ECOSYSTEM HANDOFF", classes="card-title")
            grafana_url = get_grafana_dashboard_url()
            tempo_url = f"{get_grafana_base_url()}/explore"
            yield Static(
                "[bold green]Grafana Runtime Dashboard:[/bold green]\n"
                f"  URL: [cyan]{grafana_url}[/cyan]\n"
                "  Credentials: admin / admin\n"
                "  Key Panels to inspect during scenario runs:\n"
                "    • Multi-Agent Execution: Delegation Volume & Duration\n"
                "    • Security & Governance: Evaluations & Human Confirmation Outcomes\n"
                "    • MCP & External: Circuit Breaker State & Retries\n"
                "    • LLM Telemetry: Invocations, Retries & Token Consumption\n\n"
                "[bold green]Grafana Tempo (Distributed Trace Explorer):[/bold green]\n"
                f"  URL: [cyan]{tempo_url}[/cyan]\n"
                "  Datasource: [bold]tempo[/bold] | Query type: [bold]Trace ID[/bold]\n"
                "  Copy the active run's [cyan]Trace ID[/cyan] (press [bold]T[/bold] in TUI) to view the full span tree.\n\n"
                "[bold green]Prometheus Metrics Endpoint:[/bold green]\n"
                "  Prometheus UI: [cyan]http://localhost:9090[/cyan]\n"
                "  Raw Metrics:   [cyan]http://localhost:9101/metrics[/cyan]\n",
            )
