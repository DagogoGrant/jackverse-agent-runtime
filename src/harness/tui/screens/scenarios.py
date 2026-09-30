"""Interactive Scenarios View: 1-click execution of curated journeys with observability checkpoints."""

from __future__ import annotations

import threading
from textual.app import ComposeResult
from textual.containers import Container, Horizontal, Vertical, VerticalScroll
from textual.widgets import Button, DataTable, Label, RichLog, Static

from harness.tui.scenarios import (
    SCENARIOS,
    EvaluatorScenario,
    run_deterministic_resilience_tour,
)
from harness.tui.store import RuntimeStateStore
from harness.tui.theme import BORDER, PANEL_BG, TEXT_ACCENT, TEXT_MUTED


class ScenariosScreen(Container):
    """Operator screen for launching guided scenarios and inspecting observability guidance."""

    DEFAULT_CSS = f"""
    ScenariosScreen {{
        layout: horizontal;
        padding: 1;
        height: 1fr;
    }}
    .scenarios-list-pane {{
        width: 45%;
        height: 1fr;
        border-right: solid {BORDER};
        padding-right: 1;
    }}
    .scenarios-detail-pane {{
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
    .scenario-detail-card {{
        background: {PANEL_BG};
        border: solid {BORDER};
        padding: 1;
        height: 1fr;
    }}
    .launch-bar {{
        margin-top: 1;
        height: 3;
    }}
    """

    def __init__(self, store: RuntimeStateStore, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.store = store
        self._selected_scenario_id: str = "full_journey"

    def compose(self) -> ComposeResult:
        with Vertical(classes="scenarios-list-pane"):
            yield Label("INTERACTIVE SYSTEM SCENARIOS", classes="pane-title")
            table = DataTable(id="scenarios-table", cursor_type="row")
            table.add_columns("SCENARIO", "SCOPE")
            yield table

        with Vertical(classes="scenarios-detail-pane"):
            yield Label("SCENARIO RUNBOOK & OBSERVABILITY CHECKPOINTS", classes="pane-title")
            with VerticalScroll(classes="scenario-detail-card"):
                yield Static(id="scenario-detail-text")
                with Horizontal(classes="launch-bar"):
                    yield Button("▶ Execute This Scenario", id="btn-run-scenario", variant="primary")

    def on_mount(self) -> None:
        table = self.query_one("#scenarios-table", DataTable)
        table.clear()
        for s in SCENARIOS:
            table.add_row(s.title, f"[cyan]{s.week_label}[/cyan]", key=s.id)
        self.inspect_scenario(self._selected_scenario_id)

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        s_id = str(event.row_key.value)
        self.inspect_scenario(s_id)

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
            f"\n[bold yellow]SUGGESTED PROMPT / ACTION[/bold yellow]\n"
            f"[dim]\"{sc.suggested_prompt}\"[/dim]\n"
            f"\n[bold yellow]EXPECTED VERIFICATION HIGHLIGHTS[/bold yellow]\n"
            f"{highlights_str}\n"
            f"\n[bold green]EXTERNAL OBSERVABILITY CHECKPOINT[/bold green]\n"
            f"[bold]{sc.observability_checkpoint}[/bold]\n"
        )
        self.query_one("#scenario-detail-text", Static).update(text)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn-run-scenario":
            self.app.launch_scenario(self._selected_scenario_id)
