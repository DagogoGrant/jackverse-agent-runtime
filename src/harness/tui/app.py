"""Main Textual Application for the JackVerse Agent Runtime Operator Console."""

from __future__ import annotations

import logging
import threading
from typing import Any

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Container, Horizontal, Vertical
from textual.widgets import (
    Button,
    Footer,
    Header,
    Label,
    RichLog,
    Static,
    TabbedContent,
    TabPane,
)

from harness.agent.react import ReActController
from harness.config import AppConfig
from harness.permissions.base import PermissionRequest
from harness.runtime.events import LifecycleEventBus
from harness.tools.registry import ToolRegistry
from harness.tools.workspace import Workspace
from harness.tui.confirmation import TUIConfirmationHandler
from harness.tui.modals import (
    ConfirmationModal,
    CustomPromptModal,
    ExplanationModal,
    PostRunSummaryModal,
    extract_post_run_summary,
)
from harness.tui.scenarios import (
    SCENARIOS,
    build_full_journey_prompt,
    prepare_evaluator_fixtures,
    prepare_full_journey_memory,
    prepare_full_journey_workspace,
    run_deterministic_resilience_tour,
)
from harness.tui.screens.agents import AgentsScreen
from harness.tui.screens.events import EventsScreen
from harness.tui.screens.help import HelpScreen
from harness.tui.screens.home import HomeScreen
from harness.tui.screens.mcp import MCPScreen
from harness.tui.screens.overview import OverviewScreen
from harness.tui.observability import (
    build_tempo_trace_url,
    get_grafana_dashboard_url,
    open_host_browser,
)
from harness.tui.screens.runs import RunsScreen
from harness.tui.screens.security import SecurityScreen
from harness.tui.screens.tools import ToolsScreen
from harness.tui.store import RuntimeStateStore
from harness.tui.theme import APP_CSS, BORDER, PANEL_BG, SUCCESS, TEXT_ACCENT, TEXT_MUTED, WARNING

logger = logging.getLogger("harness.tui.app")


class AgentHarnessApp(App[None]):
    """Live AI Runtime Operator Console for JackVerse Agent Runtime."""

    CSS = APP_CSS
    TITLE = "JACKVERSE · AGENT RUNTIME"
    SUB_TITLE = "Governed Multi-Agent Systems · Runtime & Observability Control Plane"

    BINDINGS = [
        Binding("1", "tab_home", "Home", show=True),
        Binding("2", "tab_runs", "Runs", show=True),
        Binding("3", "tab_agents", "Agents", show=True),
        Binding("4", "tab_tools", "Tools", show=True),
        Binding("5", "tab_mcp", "MCP", show=True),
        Binding("6", "tab_security", "Security", show=True),
        Binding("7", "tab_events", "Events", show=True),
        Binding("8", "tab_overview", "Overview", show=True),
        Binding("9", "tab_help", "Help", show=True),
        Binding("c", "custom_prompt", "Custom Prompt", show=True),
        Binding("a", "tab_runs", "Advanced / Runs", show=False),
        Binding("e", "explain_run", "Explain", show=True),
        Binding("t", "show_tempo", "Tempo", show=True),
        Binding("g", "show_grafana", "Grafana", show=True),
        Binding("q", "quit", "Quit", show=True),
    ]

    def __init__(
        self,
        controller: ReActController,
        config: AppConfig,
        workspace: Workspace,
        registry: ToolRegistry,
        event_bus: LifecycleEventBus,
        store: RuntimeStateStore,
        confirmation_handler: TUIConfirmationHandler | None = None,
        memory_manager: Any | None = None,
        *args,
        **kwargs,
    ) -> None:
        super().__init__(*args, **kwargs)
        self.controller = controller
        self.config = config
        self.workspace = workspace
        self.registry = registry
        self.event_bus = event_bus
        self.store = store
        self.confirmation_handler = confirmation_handler
        self.memory_manager = memory_manager

        self._exec_lock = threading.Lock()
        self._is_executing = False
        self._active_scenario_id: str | None = None
        self._active_scenario_prep_id: str | None = None

        # Attach UI confirmation callback if handler provided
        if self.confirmation_handler:
            self.confirmation_handler.set_prompt_callback(self._prompt_operator_confirmation)

        # Populate discovery cards from runtime
        self.store.populate_from_runtime(config, registry)

        # Prepare harmless evaluator fixtures (e.g. sample .env) in workspace
        prepare_evaluator_fixtures(self.workspace)

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)

        with TabbedContent(id="main-tabs", initial="tab-home"):
            with TabPane("1 Home", id="tab-home"):
                yield HomeScreen(self.store, self.config, id="screen-home")
            with TabPane("2 Runs & Tree", id="tab-runs"):
                yield RunsScreen(self.store, id="screen-runs")
            with TabPane("3 Agents", id="tab-agents"):
                yield AgentsScreen(self.store, id="screen-agents")
            with TabPane("4 Tools", id="tab-tools"):
                yield ToolsScreen(self.store, id="screen-tools")
            with TabPane("5 MCP", id="tab-mcp"):
                yield MCPScreen(self.store, id="screen-mcp")
            with TabPane("6 Security", id="tab-security"):
                yield SecurityScreen(self.store, self.config, id="screen-security")
            with TabPane("7 Events", id="tab-events"):
                yield EventsScreen(self.store, id="screen-events")
            with TabPane("8 Overview", id="tab-overview"):
                yield OverviewScreen(self.store, self.config, id="screen-overview")
            with TabPane("9 Help", id="tab-help"):
                yield HelpScreen(id="screen-help")

        # Bottom Operator Status Bar (No persistent input clutter)
        with Horizontal(id="bottom-status-bar"):
            yield Label("RUNTIME: ● READY", id="status-badge-runtime")
            yield Label("  |  ACTIVE RUN: None", id="status-badge-run")
            yield Label("  |  TRACE ID: None", id="status-badge-trace")
            yield Label("  |  TOOL CALLS: 0", id="status-badge-tools")
            yield Label("  |  [#B0B8C4]1-9 Navigate • Enter Run • C Prompt • Q Quit[/#B0B8C4]", id="status-badge-hint")

        yield Footer()

    def on_mount(self) -> None:
        self.store.add_listener(self._on_store_updated)

    def _on_store_updated(self) -> None:
        """Invoked when the background store updates; marshaled to Textual thread."""
        self.call_from_thread(self._refresh_ui_from_store)

    def _refresh_ui_from_store(self) -> None:
        # Update status bar badges
        run_id_str = self.store.active_run_id[:8] if self.store.active_run_id else "None"
        trace_id_str = self.store.active_trace_id[:8] if self.store.active_trace_id else "None"
        st_color = "yellow" if self.store.is_agent_executing else "green"
        st_text = "● RUNNING" if self.store.is_agent_executing else "● READY"

        try:
            self.query_one("#status-badge-runtime", Label).update(f"RUNTIME: [{st_color}]{st_text}[/{st_color}]")
            self.query_one("#status-badge-run", Label).update(f"  |  ACTIVE RUN: {run_id_str}")
            self.query_one("#status-badge-trace", Label).update(f"  |  TRACE ID: {trace_id_str}")
            self.query_one("#status-badge-tools", Label).update(f"  |  TOOL CALLS: {len(self.store.timeline)}")

            # Refresh HomeScreen status badges if active
            try:
                home_screen = self.query_one("#screen-home", HomeScreen)
                home_screen.refresh_system_status()
            except Exception:
                pass

            # Refresh RunsScreen tree, timeline, and result pane
            runs_screen = self.query_one("#screen-runs", RunsScreen)
            runs_screen.refresh_timeline()
            runs_screen.refresh_tree()
            runs_screen.refresh_result()

            # Refresh other screens if active
            tools_screen = self.query_one("#screen-tools", ToolsScreen)
            tools_screen.refresh_tools()
            sec_screen = self.query_one("#screen-security", SecurityScreen)
            sec_screen.refresh_audit_log()
            mcp_screen = self.query_one("#screen-mcp", MCPScreen)
            mcp_screen.refresh_mcp()
            ev_screen = self.query_one("#screen-events", EventsScreen)
            ev_screen.refresh_events()
        except Exception as e:
            logger.debug(f"Error updating UI widgets: {e}")

    # -------------------------------------------------------------------------
    # Tab Navigation Actions
    # -------------------------------------------------------------------------

    def action_tab_home(self) -> None:
        self.query_one("#main-tabs", TabbedContent).active = "tab-home"

    def action_tab_runs(self) -> None:
        self.query_one("#main-tabs", TabbedContent).active = "tab-runs"

    def action_tab_agents(self) -> None:
        self.query_one("#main-tabs", TabbedContent).active = "tab-agents"

    def action_tab_tools(self) -> None:
        self.query_one("#main-tabs", TabbedContent).active = "tab-tools"

    def action_tab_mcp(self) -> None:
        self.query_one("#main-tabs", TabbedContent).active = "tab-mcp"

    def action_tab_security(self) -> None:
        self.query_one("#main-tabs", TabbedContent).active = "tab-security"

    def action_tab_events(self) -> None:
        self.query_one("#main-tabs", TabbedContent).active = "tab-events"

    def action_tab_overview(self) -> None:
        self.query_one("#main-tabs", TabbedContent).active = "tab-overview"

    def action_tab_scenarios(self) -> None:
        self.action_tab_home()

    def action_tab_help(self) -> None:
        self.query_one("#main-tabs", TabbedContent).active = "tab-help"

    def action_custom_prompt(self) -> None:
        """Open custom prompt dialog to enter agent instructions."""
        def on_prompt_submitted(result: Any | None) -> None:
            if not result:
                return
            prompt = ""
            fresh_task = True
            if hasattr(result, "prompt") and hasattr(result, "fresh_task"):
                prompt = result.prompt
                fresh_task = result.fresh_task
            elif isinstance(result, tuple) and len(result) == 2:
                prompt, fresh_task = result
            elif isinstance(result, str):
                prompt, fresh_task = result, True

            if prompt:
                self.execute_agent_task(prompt, fresh_task=fresh_task)
        self.push_screen(CustomPromptModal(), callback=on_prompt_submitted)

    # -------------------------------------------------------------------------
    # Telemetry Handoff Actions
    # -------------------------------------------------------------------------

    def action_show_grafana(self) -> None:
        url = get_grafana_dashboard_url()
        opened = open_host_browser(url)
        if opened:
            self.notify(
                f"Opened in browser:\n{url}",
                title="Grafana Dashboard",
                severity="information",
                timeout=6.0,
            )
        else:
            self.notify(
                f"Grafana URL (copy or Cmd+Click in terminal):\n{url}\n(Default login: admin / admin)",
                title="Grafana Dashboard",
                severity="information",
                timeout=10.0,
            )

    def action_show_tempo(self) -> None:
        run = self.store.get_selected_or_latest_run()
        trace_id = run.trace_id if run and run.trace_id else self.store.active_trace_id
        if not trace_id:
            self.notify(
                "No active or selected trace ID to inspect.\nRun a scenario first or select a completed run node.",
                title="Tempo Trace",
                severity="warning",
                timeout=6.0,
            )
            return

        url = build_tempo_trace_url(trace_id)
        opened = open_host_browser(url)
        if opened:
            self.notify(
                f"Opened trace {trace_id[:8]}... in browser:\n{url}",
                title="Tempo Trace Explorer",
                severity="information",
                timeout=6.0,
            )
        else:
            self.notify(
                f"Trace URL (copy or Cmd+Click in terminal):\n{url}",
                title=f"Tempo Trace: {trace_id[:8]}...",
                severity="information",
                timeout=10.0,
            )

    def action_explain_run(self) -> None:
        runs_screen = self.query_one("#screen-runs", RunsScreen)
        explanation = runs_screen.explain_selected_run()
        self.push_screen(ExplanationModal(explanation))

    def action_quit(self) -> None:
        """Quit the operator console cleanly, unblocking any waiting confirmation workers."""
        if self.confirmation_handler:
            self.confirmation_handler.cancel_pending()
        self.exit()

    # -------------------------------------------------------------------------
    # Operator Confirmation Modal Callback
    # -------------------------------------------------------------------------

    def _prompt_operator_confirmation(
        self,
        request: PermissionRequest,
        resolve_fn: Any,
    ) -> None:
        """Marshaled from background worker thread to Textual main event loop."""
        if request.run_id in self.store.runs and request.call_id in self.store.runs[request.run_id].tool_calls_map:
            t_node = self.store.runs[request.run_id].tool_calls_map[request.call_id]
            if request.arguments_summary:
                t_node.arguments_summary = dict(request.arguments_summary)
            if request.resource_descriptor:
                t_node.resource_descriptor = request.resource_descriptor

        def show_modal() -> None:
            modal = ConfirmationModal(request)
            self.push_screen(modal, callback=resolve_fn)
        self.call_from_thread(show_modal)

    # -------------------------------------------------------------------------
    # Task Execution (Threaded Off UI)
    # -------------------------------------------------------------------------

    def launch_scenario(self, scenario_id: str) -> None:
        """Launch a curated scenario with pre-configuration and UI switch."""
        sc = next((s for s in SCENARIOS if s.id == scenario_id), None)
        if not sc:
            return

        self._active_scenario_id = scenario_id

        if sc.id == "resilience_tour":
            # Run deterministic resilience tour
            self.query_one("#main-tabs", TabbedContent).active = "tab-runs"
            threading.Thread(
                target=self._run_resilience_worker,
                daemon=True,
            ).start()
            return

        if sc.id in ("full_journey", "week2_mcp"):
            # Pre-admit user preference into memory for genuine memory lifecycle
            self._active_scenario_prep_id = prepare_full_journey_memory(self.memory_manager)

        if sc.id == "full_journey":
            prepare_full_journey_workspace(self.workspace.root)
            prompt = build_full_journey_prompt()
        else:
            prompt = sc.suggested_prompt

        self.execute_agent_task(prompt, fresh_task=True)

    def _run_resilience_worker(self) -> None:
        """Execute isolated deterministic resilience demonstration."""
        with self._exec_lock:
            self._is_executing = True
            runs_screen = self.query_one("#screen-runs", RunsScreen)
            log = runs_screen.query_one("#timeline-log")

            def out(msg: str) -> None:
                self.call_from_thread(lambda: log.write(f"[bold cyan][RESILIENCE TOUR][/bold cyan] {msg}"))

            try:
                run_deterministic_resilience_tour(self.event_bus, out)
            finally:
                self._is_executing = False

    def execute_agent_task(self, user_prompt: str, *, fresh_task: bool = True) -> None:
        """Dispatch agent run to a background thread to keep Textual responsive."""
        if not self._exec_lock.acquire(blocking=False):
            self.notify("Agent is currently executing a run. Please wait for it to complete.", severity="warning")
            return

        self._is_executing = True
        if fresh_task and hasattr(self.controller, "reset_conversation"):
            self.controller.reset_conversation()

        # Switch to Runs screen to observe live flight recorder
        self.query_one("#main-tabs", TabbedContent).active = "tab-runs"

        mode_label = "NEW EVALUATION TASK" if fresh_task else "CONTINUED CONVERSATION"
        runs_screen = self.query_one("#screen-runs", RunsScreen)
        log = runs_screen.query_one("#timeline-log")
        log.write(f"\n[bold magenta]─── Starting Run [{mode_label}] ───[/bold magenta]")

        def worker() -> None:
            try:
                logger.info(f"Starting agent task [{mode_label}]: {user_prompt[:60]}...")
                response = self.controller.run(user_prompt)
                self.call_from_thread(self._on_task_success, response)
            except Exception as exc:
                logger.error(f"Agent execution error: {exc}", exc_info=True)
                self.call_from_thread(self._on_task_error, str(exc))
            finally:
                self._is_executing = False
                self._exec_lock.release()

        thread = threading.Thread(target=worker, daemon=True)
        thread.start()

    def _on_task_success(self, response: str) -> None:
        self.notify("Agent execution turn completed successfully.", title="Run Complete", severity="information")
        # Record final response on the RunNode in RuntimeStateStore
        self.store.record_final_response(self.store.active_run_id, response)

        # Refresh RunsScreen display
        runs_screen = self.query_one("#screen-runs", RunsScreen)
        log = runs_screen.query_one("#timeline-log", RichLog)
        log.write("\n[bold green]✓ AGENT TURN COMPLETED[/bold green]\n")
        runs_screen.refresh_display()

        # Auto-open PostRunSummaryModal for curated scenarios
        if self._active_scenario_id:
            summary = extract_post_run_summary(
                self.store,
                prep_run_id=self._active_scenario_prep_id,
            )
            if summary:
                def on_summary_dismiss(action: str | None) -> None:
                    if action == "explain":
                        self.action_explain_run()
                    elif action == "trace":
                        self.action_show_tempo()
                    elif action == "security":
                        self.action_tab_security()
                self.push_screen(PostRunSummaryModal(summary), callback=on_summary_dismiss)
            self._active_scenario_id = None
            self._active_scenario_prep_id = None

    def _on_task_error(self, error_msg: str) -> None:
        self._active_scenario_id = None
        self._active_scenario_prep_id = None
        self.notify(f"Execution error: {error_msg}", title="Run Error", severity="error")
        # Record error on the RunNode in RuntimeStateStore
        self.store.record_final_response(self.store.active_run_id, f"Execution Error: {error_msg}")

        # Refresh RunsScreen display
        runs_screen = self.query_one("#screen-runs", RunsScreen)
        log = runs_screen.query_one("#timeline-log", RichLog)
        log.write(f"\n[bold red]✗ AGENT TURN FAILED: {error_msg}[/bold red]\n")
        runs_screen.refresh_display()

