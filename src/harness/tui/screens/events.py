"""Events View: Searchable and filterable raw lifecycle event history."""

from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Container, Horizontal, Vertical
from textual.widgets import Button, DataTable, Input, Label

from harness.tui.store import RuntimeStateStore
from harness.tui.theme import BORDER, PANEL_BG, TEXT_ACCENT, TEXT_MUTED


class EventsScreen(Container):
    """Operator screen for searching and filtering the bounded in-memory lifecycle event stream."""

    DEFAULT_CSS = f"""
    EventsScreen {{
        layout: vertical;
        padding: 1;
        height: 1fr;
    }}
    .events-filter-bar {{
        height: 3;
        layout: horizontal;
        margin-bottom: 1;
    }}
    .filter-input {{
        width: 1fr;
    }}
    .events-table-pane {{
        height: 1fr;
        background: {PANEL_BG};
        border: solid {BORDER};
    }}
    .pane-title {{
        text-style: bold;
        color: {TEXT_ACCENT};
        margin-bottom: 1;
    }}
    """

    def __init__(self, store: RuntimeStateStore, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.store = store
        self._current_filter: str = ""

    def compose(self) -> ComposeResult:
        yield Label("LIFECYCLE EVENT STREAM (BOUNDED BUFFER)", classes="pane-title")

        with Horizontal(classes="events-filter-bar"):
            yield Input(placeholder="Filter events by tool, agent, run_id, or event type... (Press Enter)", id="input-event-filter", classes="filter-input")
            yield Button("Clear Filter", id="btn-clear-filter")

        with Vertical(classes="events-table-pane"):
            table = DataTable(id="events-table", cursor_type="row")
            table.add_columns("TIME", "EVENT TYPE", "AGENT", "RUN ID", "SUMMARY DETAILS")
            yield table

    def on_mount(self) -> None:
        self.refresh_events()

    def refresh_events(self) -> None:
        table = self.query_one("#events-table", DataTable)
        table.clear()

        flt = self._current_filter.lower()
        events_list = list(self.store.raw_events)

        for ev in reversed(events_list):
            ev_type = ev.event_type.value if hasattr(ev.event_type, "value") else str(ev.event_type)
            agent_role = ev.agent_role
            run_id = ev.run_id[:8]
            rel_time = f"+{self.store.get_relative_seconds(ev.timestamp):05.2f}s"

            # Derive concise summary based on concrete event fields
            details = ""
            if hasattr(ev, "tool_name"):
                details += f"tool={ev.tool_name} "
            if hasattr(ev, "status"):
                st = ev.status.value if hasattr(ev.status, "value") else str(ev.status)
                details += f"status={st} "
            if hasattr(ev, "decision"):
                dec = ev.decision.value if hasattr(ev.decision, "value") else str(ev.decision)
                details += f"decision={dec} "
            if hasattr(ev, "duration_seconds"):
                details += f"dur={ev.duration_seconds:.3f}s "
            if hasattr(ev, "error_message") and ev.error_message:
                details += f"err={ev.error_message[:40]} "

            searchable_line = f"{ev_type} {agent_role} {ev.run_id} {details}".lower()
            if flt and flt not in searchable_line:
                continue

            table.add_row(rel_time, ev_type, agent_role, run_id, details)

    def on_input_submitted(self, event: Input.Submitted) -> None:
        self._current_filter = event.value.strip()
        self.refresh_events()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn-clear-filter":
            self.query_one("#input-event-filter", Input).value = ""
            self._current_filter = ""
            self.refresh_events()
