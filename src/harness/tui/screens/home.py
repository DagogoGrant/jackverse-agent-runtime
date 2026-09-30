"""Home Screen: Minimal landing screen and scenario launcher."""

from __future__ import annotations

import os
from textual.app import ComposeResult
from textual.containers import Container, Horizontal, Vertical, VerticalScroll
from textual.widgets import Button, DataTable, Label, Static

from harness.config import AppConfig
from harness.tui.health import (
    check_mcp_transport_health,
    check_metrics_endpoint_health,
    check_prometheus_scrape_health,
    check_tempo_health,
    check_workspace_health,
)
from harness.tui.scenarios import SCENARIOS, EvaluatorScenario
from harness.tui.store import RuntimeStateStore
from harness.tui.theme import BORDER, PANEL_BG, SUCCESS, TEXT_ACCENT, TEXT_MUTED, WARNING


# 4 Primary scenarios shown on Home
PRIMARY_SCENARIO_IDS = ("week1_fs", "week2_mcp", "week3_governance", "full_journey")


class HomeScreen(Container):
    """Landing screen providing instant system readiness and scenario launching."""

    DEFAULT_CSS = f"""
    HomeScreen {{
        layout: vertical;
        padding: 1 2;
        height: 1fr;
    }}
    .home-header {{
        height: auto;
        margin-bottom: 1;
        border-bottom: solid {BORDER};
        padding-bottom: 1;
    }}
    .home-title {{
        text-style: bold;
        color: {TEXT_ACCENT};
    }}
    .home-status-bar {{
        height: 1;
        margin-top: 1;
    }}
    .status-item {{
        margin-right: 3;
        color: #FFFFFF;
        text-style: bold;
    }}
    .home-main-split {{
        layout: horizontal;
        height: 1fr;
        margin-top: 1;
    }}
    .home-scenarios-pane {{
        width: 46%;
        height: 1fr;
        border-right: solid {BORDER};
        padding-right: 1;
    }}
    .home-detail-pane {{
        width: 54%;
        height: 1fr;
        padding-left: 2;
    }}
    .pane-header {{
        text-style: bold;
        color: {TEXT_ACCENT};
        margin-bottom: 1;
    }}
    .detail-card {{
        background: {PANEL_BG};
        border: solid {BORDER};
        padding: 1;
        height: 1fr;
    }}
    .home-action-bar {{
        height: 3;
        margin-top: 1;
        border-top: solid {BORDER};
        padding-top: 1;
        align: left middle;
    }}
    .home-action-hint {{
        color: {TEXT_MUTED};
        margin-right: 2;
    }}
    """

    def __init__(
        self,
        store: RuntimeStateStore,
        config: AppConfig,
        *args,
        **kwargs,
    ) -> None:
        super().__init__(*args, **kwargs)
        self.store = store
        self.config = config
        self._selected_scenario_id: str = "week1_fs"

    def compose(self) -> ComposeResult:
        with Vertical(classes="home-header"):
            yield Label(
                "JACKVERSE · GOVERNED AGENT RUNTIME",
                classes="home-title",
            )
            with Horizontal(classes="home-status-bar"):
                yield Label("SYSTEM STATUS:", classes="status-item")
                yield Label("LLM ○ CHECKING", id="status-llm", classes="status-item")
                yield Label("MCP ○ CHECKING", id="status-mcp", classes="status-item")
                yield Label("OBSERVABILITY ○ CHECKING", id="status-obs", classes="status-item")
                yield Label("WORKSPACE ○ CHECKING", id="status-ws", classes="status-item")

        with Horizontal(classes="home-main-split"):
            with Vertical(classes="home-scenarios-pane"):
                yield Label("INTERACTIVE SYSTEM SCENARIOS (Select with ↑/↓, Enter to run)", classes="pane-header")
                table = DataTable(id="home-scenarios-table", cursor_type="row")
                table.add_columns("SCENARIO", "SCOPE")
                yield table

            with Vertical(classes="home-detail-pane"):
                yield Label("SCENARIO PREVIEW", classes="pane-header")
                with VerticalScroll(classes="detail-card"):
                    yield Static(id="home-scenario-detail-text")

        with Horizontal(classes="home-action-bar"):
            yield Button("▶ Run Selected Scenario (Enter)", id="btn-home-run", variant="primary")
            yield Button("✎ Custom Prompt (C)", id="btn-home-custom-prompt")
            yield Button("⛯ Advanced Inspection (A)", id="btn-home-advanced")

    def on_mount(self) -> None:
        table = self.query_one("#home-scenarios-table", DataTable)
        table.clear()
        primary = [s for s in SCENARIOS if s.id in PRIMARY_SCENARIO_IDS]
        for s in primary:
            table.add_row(s.title, f"[cyan]{s.week_label}[/cyan]", key=s.id)

        self.inspect_scenario(self._selected_scenario_id)
        self.refresh_system_status()
        table.focus()

    def refresh_system_status(self) -> None:
        """Derive evidence-sensitive statuses without optimistic hardcoding."""
        # 1. LLM Status
        api_key = self.config.llm.api_key or os.environ.get("LLM_API_KEY", "") or os.environ.get("INNKUBE_API_KEY", "")
        has_runs = len(self.store.runs) > 0
        has_errors = any(r.status == "FAILED" for r in self.store.runs.values())
        is_local = any(
            h in (self.config.llm.base_url or "").lower()
            for h in ("localhost", "127.0.0.1", "::1", "host.docker.internal", "ollama")
        )
        if has_runs and not has_errors:
            llm_text = "[bold green]LLM ● READY[/bold green]"
        elif self.config.llm.model and (api_key or is_local):
            llm_text = "[bold cyan]LLM ○ CONFIGURED[/bold cyan]"
        elif not api_key and not is_local:
            llm_text = "[bold red]LLM ● NO_KEY[/bold red]"
        else:
            llm_text = "[bold red]LLM ● ERROR[/bold red]"
        self.query_one("#status-llm", Label).update(llm_text)

        # 2. MCP Status
        mcp_status, mcp_ok = check_mcp_transport_health()
        if mcp_ok:
            mcp_label = "[bold green]MCP ● CONNECTED[/bold green]"
        else:
            mcp_label = "[bold yellow]MCP ○ OFFLINE[/bold yellow]"
        self.query_one("#status-mcp", Label).update(mcp_label)

        # 3. Observability Status (Prometheus + Tempo combined)
        _, prom_ok = check_prometheus_scrape_health()
        _, tempo_ok = check_tempo_health()
        if prom_ok and tempo_ok:
            obs_label = "[bold green]OBSERVABILITY ● READY[/bold green]"
        elif prom_ok or tempo_ok:
            obs_label = "[bold yellow]OBSERVABILITY ● DEGRADED[/bold yellow]"
        else:
            obs_label = "[bold red]OBSERVABILITY ○ OFFLINE[/bold red]"
        self.query_one("#status-obs", Label).update(obs_label)

        # 4. Workspace Status
        ws_status, ws_ok = check_workspace_health(self.config.tools.workspace_root)
        if ws_ok:
            ws_label = "[bold green]WORKSPACE ● READY[/bold green]"
        else:
            ws_label = f"[bold red]WORKSPACE ○ ERROR[/bold red]"
        self.query_one("#status-ws", Label).update(ws_label)

    def on_data_table_row_highlighted(self, event: DataTable.RowHighlighted) -> None:
        if event.row_key and event.row_key.value:
            self.inspect_scenario(str(event.row_key.value))

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        if event.row_key and event.row_key.value:
            s_id = str(event.row_key.value)
            self._selected_scenario_id = s_id
            self.app.launch_scenario(s_id)

    def inspect_scenario(self, scenario_id: str) -> None:
        self._selected_scenario_id = scenario_id
        sc = next((s for s in SCENARIOS if s.id == scenario_id), None)
        if not sc:
            return

        highlights_str = "\n".join(f"  • {h}" for h in sc.expected_highlights)

        text = (
            f"[bold cyan]{sc.title.upper()}[/bold cyan]\n"
            f"[bold]Target Scope:[/bold]  {sc.week_label}\n"
            f"\n[bold yellow]PURPOSE & BEHAVIOR[/bold yellow]\n"
            f"{sc.description}\n"
        )
        if sc.security_fixture:
            text += (
                f"\n[bold yellow]SECURITY FIXTURE[/bold yellow]\n"
                f"[#B0B8C4]{sc.security_fixture}[/#B0B8C4]\n"
            )
        text += (
            f"\n[bold yellow]SUGGESTED PROMPT[/bold yellow]\n"
            f"[#B0B8C4]\"{sc.suggested_prompt}\"[/#B0B8C4]\n"
            f"\n[bold yellow]EXPECTED VERIFICATION HIGHLIGHTS[/bold yellow]\n"
            f"{highlights_str}\n"
            f"\n[bold green]OBSERVABILITY CHECKPOINT[/bold green]\n"
            f"[#B0B8C4]{sc.observability_checkpoint}[/#B0B8C4]\n"
        )
        self.query_one("#home-scenario-detail-text", Static).update(text)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn-home-run":
            self.app.launch_scenario(self._selected_scenario_id)
        elif event.button.id == "btn-home-custom-prompt":
            self.app.action_custom_prompt()
        elif event.button.id == "btn-home-advanced":
            self.app.action_tab_runs()
