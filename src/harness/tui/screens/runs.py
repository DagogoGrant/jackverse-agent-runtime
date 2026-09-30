"""Runs & Execution Tree Screen: Split-view with hierarchical tree, live flight recorder, authoritative result, and deep inspector."""

from __future__ import annotations

import re
from typing import Any

from rich import box
from rich.table import Table
from textual.app import ComposeResult
from textual.containers import Container, Horizontal, Vertical, VerticalScroll
from textual.widgets import Button, Label, Markdown, RichLog, Static, Tree
from textual.widgets.tree import TreeNode

from harness.tui.models import RunNode, TimelineEvent, ToolCallNode
from harness.tui.observability import build_tempo_trace_url, get_grafana_dashboard_url
from harness.tui.store import RuntimeStateStore
from harness.tui.theme import BORDER, PANEL_BG, SUCCESS, TEXT_ACCENT, TEXT_MUTED, WARNING


class RunsScreen(Container):
    """Operator screen providing real-time hierarchical execution tree, timeline, and inspector."""

    DEFAULT_CSS = f"""
    RunsScreen {{
        layout: horizontal;
        height: 1fr;
    }}
    .runs-left {{
        width: 30%;
        min-width: 24;
        height: 1fr;
        border-right: solid {BORDER};
        padding: 0 1;
    }}
    .runs-center {{
        width: 44%;
        min-width: 32;
        height: 1fr;
        border-right: solid {BORDER};
        padding: 0 1;
    }}
    .runs-right {{
        width: 26%;
        min-width: 22;
        height: 1fr;
        padding: 0 1;
    }}
    .inspector-actions {{
        margin-top: 1;
        border-top: solid {BORDER};
        padding-top: 1;
    }}
    .obs-title {{
        text-style: bold;
        color: {TEXT_ACCENT};
        margin-bottom: 1;
    }}
    .pane-title {{
        text-style: bold;
        color: {TEXT_ACCENT};
        height: 1;
        margin-bottom: 1;
        border-bottom: solid {BORDER};
    }}
    .pane-header {{
        height: 1;
        margin-bottom: 1;
        layout: horizontal;
    }}
    .pane-header .pane-title {{
        width: 1fr;
        margin-bottom: 0;
        border-bottom: none;
    }}
    .scroll-badge {{
        color: #e5a50a;
        text-style: bold;
        width: auto;
    }}
    .status-badge {{
        width: auto;
        text-style: bold;
    }}
    .timeline-pane {{
        height: 1fr;
        min-height: 8;
        border-bottom: solid {BORDER};
        margin-bottom: 1;
    }}
    .result-pane {{
        height: 1fr;
        min-height: 8;
    }}
    .result-scroll {{
        height: 1fr;
        background: {PANEL_BG};
        border: solid {BORDER};
        padding: 0 1;
    }}
    .error-card {{
        margin: 1 0;
        padding: 0 1;
        border: solid #d9534f;
        background: #2b1111;
        color: #ff9999;
    }}
    .evidence-table {{
        margin: 1 0;
    }}
    .sub-title {{
        text-style: bold;
        color: {TEXT_ACCENT};
        margin: 1 0 0 0;
    }}
    .result-markdown {{
        margin: 0;
        padding: 0;
    }}
    .inspector-content {{
        background: {PANEL_BG};
        height: 1fr;
        padding: 1;
        border: solid {BORDER};
    }}
    """

    def __init__(self, store: RuntimeStateStore, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.store = store
        self._last_timeline_len = 0
        self._last_runs_count = 0
        self._unread_events = 0
        self._last_rendered_key: tuple[Any, ...] | None = None
        self._last_overview_key: tuple[Any, ...] | None = None

    def compose(self) -> ComposeResult:
        # Left: Hierarchical Execution Tree
        with Vertical(classes="runs-left"):
            yield Label("EXECUTION TOPOLOGY", classes="pane-title")
            tree: Tree[dict] = Tree("Runs", id="runs-tree")
            tree.show_root = False
            yield tree

        # Center: Flight Recorder (top) & Authoritative Result (bottom)
        with Vertical(classes="runs-center"):
            # Flight Recorder
            with Vertical(classes="timeline-pane", id="timeline-pane"):
                with Horizontal(classes="pane-header"):
                    yield Label("LIVE FLIGHT RECORDER", classes="pane-title")
                    yield Label("", id="timeline-scroll-badge", classes="scroll-badge")
                yield RichLog(id="timeline-log", wrap=True, highlight=True, markup=True)

            # Result Pane
            with Vertical(classes="result-pane", id="result-pane"):
                with Horizontal(classes="pane-header"):
                    yield Label("RUN RESULT", classes="pane-title")
                    yield Label("", id="result-status-badge", classes="status-badge")
                with VerticalScroll(id="result-scroll", classes="result-scroll"):
                    yield Static("", id="result-error-card", classes="error-card")
                    yield Static("", id="result-evidence-table", classes="evidence-table")
                    yield Label("AGENT RESPONSE", classes="sub-title", id="result-response-title")
                    yield Markdown("", id="result-markdown", classes="result-markdown")

        # Right: Inspector Panel
        with Vertical(classes="runs-right"):
            yield Label("INSPECTOR", classes="pane-title")
            with VerticalScroll(classes="inspector-content"):
                yield Static("Select a run node or tool call in the tree to inspect runtime details.", id="inspector-text")
                with Vertical(id="inspector-actions", classes="inspector-actions"):
                    yield Label("OBSERVABILITY & ACTIONS", classes="obs-title")
                    yield Button("↗ Grafana Dashboard (G)", id="btn-inspector-grafana", classes="obs-btn")
                    yield Button("↗ Open This Trace (T)", id="btn-inspector-trace", classes="obs-btn")
                    yield Button("⛯ Explain Architecture (E)", id="btn-inspector-explain", classes="obs-btn")

    def on_mount(self) -> None:
        try:
            self.query_one("#result-error-card", Static).display = False
            self.query_one("#result-response-title", Label).display = False
            self.query_one("#result-markdown", Markdown).display = False
        except Exception:
            pass
        self.refresh_tree()
        self.refresh_timeline()
        self.display_run_overview()
        self.refresh_result()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn-inspector-grafana":
            self.app.action_show_grafana()
        elif event.button.id == "btn-inspector-trace":
            self.app.action_show_tempo()
        elif event.button.id == "btn-inspector-explain":
            self.app.action_explain_run()

    def refresh_timeline(self) -> None:
        """Append newly observed timeline events to the flight recorder log with smart autoscroll."""
        log = self.query_one("#timeline-log", RichLog)
        badge = self.query_one("#timeline-scroll-badge", Label)
        timeline_list = list(self.store.timeline)

        is_at_bottom = log.scroll_y >= max(0, log.max_scroll_y - 2)

        if len(timeline_list) > self._last_timeline_len:
            new_events = timeline_list[self._last_timeline_len:]
            if is_at_bottom:
                self._unread_events = 0
                badge.update("")
                for ev in new_events:
                    line = self._format_timeline_event(ev)
                    log.write(line, scroll_end=True)
            else:
                self._unread_events += len(new_events)
                badge.update(f"↓ {self._unread_events} new events")
                for ev in new_events:
                    line = self._format_timeline_event(ev)
                    log.write(line, scroll_end=False)
            self._last_timeline_len = len(timeline_list)
        elif is_at_bottom and self._unread_events > 0:
            self._unread_events = 0
            badge.update("")

    def _format_timeline_event(self, ev: TimelineEvent) -> str:
        rel = f"+{ev.relative_seconds:05.2f}s"
        cat = f"[bold #56D4C8]{ev.category}[/bold #56D4C8]" if "MCP" in ev.category else (
            f"[bold #D2A8FF]{ev.category}[/bold #D2A8FF]" if "DELEGATE" in ev.category or "SUBAGENT" in ev.category else (
                f"[bold #F2CC60]{ev.category}[/bold #F2CC60]" if "POLICY" in ev.category or "CONFIRM" in ev.category else (
                    f"[bold #56D364]{ev.category}[/bold #56D364]" if "MEMORY" in ev.category else f"[bold #38BDF8]{ev.category}[/bold #38BDF8]"
                )
            )
        )
        icon = f"[bold #56D364]{ev.icon}[/bold #56D364]" if ev.icon == "✓" else (
            f"[bold #F2CC60]{ev.icon}[/bold #F2CC60]" if ev.icon in ("!", "?") else (
                f"[bold #FF7B72]{ev.icon}[/bold #FF7B72]" if ev.icon == "✗" else f"[bold #F0F6FC]{ev.icon}[/bold #F0F6FC]"
            )
        )
        return f"[#B0B8C4]{rel}[/#B0B8C4] {icon} {cat} [bold #F0F6FC]{ev.title}[/bold #F0F6FC] [#B0B8C4]{ev.details}[/#B0B8C4]"

    def refresh_tree(self) -> None:
        """Reconstruct the tree of active and completed agent runs and tool nodes."""
        tree = self.query_one("#runs-tree", Tree)
        tree.clear()

        for r_id in self.store.root_run_ids:
            run_node = self.store.runs.get(r_id)
            if not run_node:
                continue
            self._add_run_to_tree(tree.root, run_node)

        # If no node is explicitly selected, keep inspector on RUN OVERVIEW
        if not self.store.selected_run_id:
            self.display_run_overview()

    def _add_run_to_tree(self, parent_tree_node: TreeNode, run_node: RunNode) -> None:
        icon = "●" if run_node.status == "RUNNING" else ("✓" if run_node.status == "SUCCESS" else "✗")
        style = "bold #56D364" if run_node.status == "SUCCESS" else ("bold #F2CC60" if run_node.status == "RUNNING" else "bold #FF7B72")
        label = f"[{style}]{icon} {run_node.agent_role}[/{style}] [#B0B8C4]({run_node.duration_seconds:.2f}s)[/#B0B8C4]"

        r_tree_node = parent_tree_node.add(label, data={"type": "run", "run_id": run_node.run_id})
        r_tree_node.expand()

        # Add Tool Calls under this run
        for c_id, t_node in run_node.tool_calls_map.items():
            t_icon = "✓" if t_node.status == "SUCCESS" else ("✗" if t_node.is_error else "◆")
            t_style = "#56D364" if t_node.status == "SUCCESS" else ("#FF7B72" if t_node.is_error else "#38BDF8")
            t_label = f"[{t_style}]{t_icon} {t_node.tool_name}[/{t_style}] [#B0B8C4]{t_node.duration_seconds:.3f}s[/#B0B8C4]"
            r_tree_node.add_leaf(t_label, data={"type": "tool", "run_id": run_node.run_id, "call_id": c_id})

        # Add Delegated Children
        for child_id in run_node.children_run_ids:
            child_run = self.store.runs.get(child_id)
            if child_run:
                self._add_run_to_tree(r_tree_node, child_run)

    def on_tree_node_selected(self, event: Tree.NodeSelected) -> None:
        data = event.node.data
        if not data:
            return

        # Reset unread scroll state on navigation
        self._unread_events = 0
        try:
            self.query_one("#timeline-scroll-badge", Label).update("")
        except Exception:
            pass

        if data.get("type") == "run":
            run_id = data.get("run_id", "")
            self.inspect_run(run_id)
            self.refresh_result(run_id)
        elif data.get("type") == "tool":
            run_id = data.get("run_id", "")
            call_id = data.get("call_id", "")
            self.inspect_tool_call(run_id, call_id)
            self.refresh_result(run_id)

    def display_run_overview(self, run: RunNode | None = None) -> None:
        """Display RUN OVERVIEW in inspector when no specific node is selected."""
        if run is None:
            run = self.store.get_selected_or_latest_run()
        inspector = self.query_one("#inspector-text", Static)
        if not run:
            inspector.update(
                "[bold #38BDF8]RUN OVERVIEW[/bold #38BDF8]\n"
                "[#30363D]────────────────────────────────────────[/#30363D]\n"
                "[#B0B8C4]No active or completed agent run.[/#B0B8C4]\n\n"
                "Select a run scenario from the Home tab [bold]1[/bold]\n"
                "or submit a Custom Prompt [bold]C[/bold] to begin.\n\n"
                "[#30363D]────────────────────────────────────────[/#30363D]\n"
                "[bold #56D364]Navigation Hotkeys:[/bold #56D364]\n"
                "[bold]C[/bold]  Custom Prompt\n"
                "[bold]1-9[/bold] Switch Tabs\n"
                "[bold]?[/bold]  Help"
            )
            try:
                btn_trace = self.query_one("#btn-inspector-trace", Button)
                btn_trace.label = "↗ Open This Trace (T)"
                btn_trace.disabled = True
            except Exception:
                pass
            return

        status_style = "bold #56D364" if run.status == "SUCCESS" else ("bold #F2CC60" if run.status == "RUNNING" else "bold #FF7B72")
        status_icon = "✓ " if run.status == "SUCCESS" else ("● " if run.status == "RUNNING" else "✗ ")
        text = (
            f"[bold #38BDF8]RUN OVERVIEW[/bold #38BDF8]\n"
            f"[#30363D]────────────────────────────────────────[/#30363D]\n"
            f"[bold #F0F6FC]Status:[/bold #F0F6FC]       [{status_style}]{status_icon}{run.status}[/{status_style}]\n"
            f"[bold #F0F6FC]Role:[/bold #F0F6FC]         {run.agent_role}\n"
            f"[bold #F0F6FC]Type:[/bold #F0F6FC]         {'Root Orchestrator' if run.is_root else 'Specialist Sub-Agent'}\n"
            f"[bold #F0F6FC]Duration:[/bold #F0F6FC]     {run.duration_seconds:.2f}s\n"
            f"[bold #F0F6FC]Steps:[/bold #F0F6FC]        {run.steps}\n"
            f"[bold #F0F6FC]Tool Calls:[/bold #F0F6FC]   {run.tool_calls}\n"
            f"[bold #F0F6FC]LLM Calls:[/bold #F0F6FC]    {run.llm_calls_count}\n"
            f"[bold #F0F6FC]Tokens:[/bold #F0F6FC]       {run.total_tokens} [#B0B8C4](p:{run.prompt_tokens} / c:{run.completion_tokens})[/#B0B8C4]\n"
            f"[bold #F0F6FC]Trace ID:[/bold #F0F6FC]     {run.trace_id or 'N/A'}\n"
            f"[bold #F0F6FC]Run ID:[/bold #F0F6FC]       {run.run_id[:16]}...\n"
        )
        if run.task_description:
            desc = run.task_description.strip().replace("\n", " ")
            if len(desc) > 60:
                desc = desc[:57] + "..."
            text += f"[bold #F0F6FC]Task:[/bold #F0F6FC]         [#B0B8C4]{desc}[/#B0B8C4]\n"
        if run.error_message:
            text += f"\n[bold #FF7B72]Error Details:[/bold #FF7B72]\n{run.error_message}\n"

        inspector.update(text)

        try:
            btn_trace = self.query_one("#btn-inspector-trace", Button)
            if run.trace_id:
                btn_trace.label = f"↗ Open Trace ({run.trace_id[:8]}...) (T)"
                btn_trace.disabled = False
            else:
                btn_trace.label = "↗ Open This Trace (T)"
                btn_trace.disabled = True
        except Exception:
            pass

    def inspect_run(self, run_id: str) -> None:
        run = self.store.runs.get(run_id)
        if not run:
            return
        self.store.selected_run_id = run_id

        status_style = "bold #56D364" if run.status == "SUCCESS" else ("bold #F2CC60" if run.status == "RUNNING" else "bold #FF7B72")
        status_icon = "✓ " if run.status == "SUCCESS" else ("● " if run.status == "RUNNING" else "✗ ")
        text = (
            f"[bold #38BDF8]AGENT RUN INSPECTOR[/bold #38BDF8]\n"
            f"[#30363D]────────────────────────────────────────[/#30363D]\n"
            f"[bold #F0F6FC]Role:[/bold #F0F6FC]         {run.agent_role}\n"
            f"[bold #F0F6FC]Type:[/bold #F0F6FC]         {'Root Orchestrator' if run.is_root else 'Specialist Sub-Agent'}\n"
            f"[bold #F0F6FC]Run ID:[/bold #F0F6FC]       {run.run_id}\n"
            f"[bold #F0F6FC]Trace ID:[/bold #F0F6FC]     {run.trace_id or 'N/A'}\n"
            f"[bold #F0F6FC]Status:[/bold #F0F6FC]       [{status_style}]{status_icon}{run.status}[/{status_style}]\n"
            f"[bold #F0F6FC]Duration:[/bold #F0F6FC]     {run.duration_seconds:.2f}s\n"
            f"[bold #F0F6FC]Steps:[/bold #F0F6FC]        {run.steps}\n"
            f"[bold #F0F6FC]Tool Calls:[/bold #F0F6FC]   {run.tool_calls}\n"
            f"[bold #F0F6FC]LLM Calls:[/bold #F0F6FC]    {run.llm_calls_count}\n"
            f"[bold #F0F6FC]Total Tokens:[/bold #F0F6FC] {run.total_tokens} [#B0B8C4](p:{run.prompt_tokens}, c:{run.completion_tokens})[/#B0B8C4]\n"
            f"[bold #F0F6FC]Children:[/bold #F0F6FC]      {len(run.children_run_ids)}\n"
        )
        if run.error_message:
            text += f"\n[bold #FF7B72]Error Details:[/bold #FF7B72]\n{run.error_message}\n"

        self.query_one("#inspector-text", Static).update(text)

        try:
            btn_trace = self.query_one("#btn-inspector-trace", Button)
            if run.trace_id:
                btn_trace.label = f"↗ Open Trace ({run.trace_id[:8]}...) (T)"
                btn_trace.disabled = False
            else:
                btn_trace.label = "↗ Open This Trace (T)"
                btn_trace.disabled = True
        except Exception:
            pass

    def inspect_tool_call(self, run_id: str, call_id: str) -> None:
        run = self.store.runs.get(run_id)
        if not run or call_id not in run.tool_calls_map:
            return
        tool = run.tool_calls_map[call_id]

        is_mcp = (tool.tool_source.value == "mcp" if hasattr(tool.tool_source, "value") else tool.tool_source == "mcp")
        header_tag = "MCP TOOL INVOCATION" if is_mcp else "BUILT-IN TOOL EXECUTION"

        status_style = "bold #56D364" if tool.status == "SUCCESS" else ("bold #FF7B72" if tool.is_error else "bold #38BDF8")
        status_icon = "✓ " if tool.status == "SUCCESS" else ("✗ " if tool.is_error else "◆ ")

        text = (
            f"[bold #38BDF8]{header_tag}[/bold #38BDF8]\n"
            f"[#30363D]────────────────────────────────────────[/#30363D]\n"
            f"[bold #F0F6FC]Tool Name:[/bold #F0F6FC]       {tool.tool_name}\n"
            f"[bold #F0F6FC]Source:[/bold #F0F6FC]          {tool.tool_source.value if hasattr(tool.tool_source, 'value') else tool.tool_source}\n"
            f"[bold #F0F6FC]Canonical ID:[/bold #F0F6FC]    {tool.canonical_identity}\n"
            f"[bold #F0F6FC]Server Name:[/bold #F0F6FC]     {tool.server_name or 'N/A'}\n"
            f"[bold #F0F6FC]Mutating:[/bold #F0F6FC]        {'YES (Workspace Mutation)' if tool.is_mutating else 'NO (Read-Only)'}\n"
            f"[bold #F0F6FC]Status:[/bold #F0F6FC]          [{status_style}]{status_icon}{tool.status}[/{status_style}]\n"
            f"[bold #F0F6FC]Duration:[/bold #F0F6FC]        {tool.duration_seconds:.3f}s\n"
            f"[bold #F0F6FC]Bytes Returned:[/bold #F0F6FC]  {tool.observation_length}\n"
        )

        if tool.risk_level is not None:
            text += (
                f"\n[bold #F2CC60]AUTHORIZATION AUDIT[/bold #F2CC60]\n"
                f"[bold #F0F6FC]Risk Level:[/bold #F0F6FC]      {tool.risk_level.value.upper()}\n"
                f"[bold #F0F6FC]Matched Rule:[/bold #F0F6FC]    {tool.matched_rule or 'default'}\n"
                f"[bold #F0F6FC]Decision:[/bold #F0F6FC]        {tool.permission_decision.value.upper() if tool.permission_decision else 'ALLOW'}\n"
            )
            if tool.confirmation_outcome:
                text += f"[bold #F0F6FC]Confirmation:[/bold #F0F6FC]    {tool.confirmation_outcome}\n"
            if tool.arguments_fingerprint:
                text += f"[bold #F0F6FC]Fingerprint:[/bold #F0F6FC]     {tool.arguments_fingerprint[:16]}...\n"

        if tool.arguments_summary:
            text += f"\n[bold #F0F6FC]Safe Arguments Summary:[/bold #F0F6FC]\n"
            for k, v in tool.arguments_summary.items():
                text += f"  {k}: {v}\n"

        if tool.error_message:
            text += f"\n[bold #FF7B72]Error Message:[/bold #FF7B72]\n{tool.error_message}\n"

        self.query_one("#inspector-text", Static).update(text)

        try:
            btn_trace = self.query_one("#btn-inspector-trace", Button)
            if run.trace_id:
                btn_trace.label = f"↗ Open Trace ({run.trace_id[:8]}...) (T)"
                btn_trace.disabled = False
            else:
                btn_trace.label = "↗ Open This Trace (T)"
                btn_trace.disabled = True
        except Exception:
            pass

    def refresh_result(self, run_id: str | None = None) -> None:
        """Render the authoritative runtime evidence and final response for the selected/latest run."""
        run = self.store.runs.get(run_id) if run_id else self.store.get_selected_or_latest_run()
        status_badge = self.query_one("#result-status-badge", Label)
        error_card = self.query_one("#result-error-card", Static)
        evidence_table = self.query_one("#result-evidence-table", Static)
        response_title = self.query_one("#result-response-title", Label)
        markdown_widget = self.query_one("#result-markdown", Markdown)

        if not run:
            status_badge.update("")
            error_card.update("")
            error_card.display = False
            evidence_table.update("[#B0B8C4]No agent runs recorded yet.[/#B0B8C4]")
            response_title.display = False
            markdown_widget.display = False
            self._last_rendered_key = None
            return

        run_key = (run.run_id, run.status, len(run.tool_calls_map), run.final_response, run.error_message)
        if run_key == self._last_rendered_key:
            return
        self._last_rendered_key = run_key

        # 1. Update status badge
        if run.status == "SUCCESS":
            status_badge.update("[bold #56D364]✓ SUCCESS[/bold #56D364]")
        elif run.status == "RUNNING":
            status_badge.update("[bold #F2CC60]● RUNNING[/bold #F2CC60]")
        elif run.status in ("FAILED", "ERROR"):
            status_badge.update("[bold #FF7B72]✗ FAILED[/bold #FF7B72]")
        else:
            status_badge.update(f"[bold #F0F6FC]{run.status}[/bold #F0F6FC]")

        # 2. Render Error Card if error or early termination
        has_error = bool(run.error_message) or run.status in ("FAILED", "ERROR")
        if has_error:
            card_content = self._format_error_card(run)
            error_card.update(card_content)
            error_card.display = True
        else:
            error_card.update("")
            error_card.display = False

        # 3. Build Authoritative Runtime Evidence Table
        table = self._build_evidence_table(run)
        evidence_table.update(table)

        # 4. Render Semantic Agent Response Markdown
        if run.final_response:
            response_title.display = True
            markdown_widget.display = True
            try:
                markdown_widget.update(run.final_response)
            except Exception:
                markdown_widget.update(f"```text\n{run.final_response}\n```")
        elif run.status == "RUNNING":
            response_title.display = True
            markdown_widget.display = True
            try:
                markdown_widget.update("*Agent is currently executing tools...*")
            except Exception:
                markdown_widget.update("Agent is currently executing...")
        else:
            response_title.display = False
            markdown_widget.display = False

    def _format_error_card(self, run: RunNode) -> str:
        """Calm, structured Error Card distinguishing configured execution budget from run wall time."""
        err_msg = run.error_message or "Execution failed without detailed error message."
        lower_err = err_msg.lower()

        if "time" in lower_err and ("budget" in lower_err or "limit" in lower_err):
            m = re.search(r"(\d+(?:\.\d+)?)\s*s", err_msg)
            budget_str = f"{m.group(1)} s" if m else "60.0 s"
            return (
                f"[bold #FF7B72]✗ EXECUTION STOPPED[/bold #FF7B72]\n\n"
                f"[bold #F0F6FC]Reason:[/bold #F0F6FC]            Time budget exceeded\n"
                f"[bold #F0F6FC]Configured budget:[/bold #F0F6FC] {budget_str}\n"
                f"[bold #F0F6FC]Run wall time:[/bold #F0F6FC]     {run.duration_seconds:.1f} s\n\n"
                f"[#B0B8C4]The configured execution budget terminated further agent execution.[/#B0B8C4]"
            )
        elif "step" in lower_err and ("budget" in lower_err or "limit" in lower_err or "reached" in lower_err):
            return (
                f"[bold #FF7B72]✗ EXECUTION STOPPED[/bold #FF7B72]\n\n"
                f"[bold #F0F6FC]Reason:[/bold #F0F6FC]            Step limit exceeded\n"
                f"[bold #F0F6FC]Steps taken:[/bold #F0F6FC]       {run.steps}\n"
                f"[bold #F0F6FC]Run wall time:[/bold #F0F6FC]     {run.duration_seconds:.1f} s\n\n"
                f"[#B0B8C4]The configured step budget terminated further agent execution.[/#B0B8C4]"
            )
        elif "boundary" in lower_err or "workspace" in lower_err:
            return (
                f"[bold #FF7B72]✗ EXECUTION STOPPED[/bold #FF7B72]\n\n"
                f"[bold #F0F6FC]Reason:[/bold #F0F6FC]            Workspace boundary violation\n"
                f"[bold #F0F6FC]Detail:[/bold #F0F6FC]            {err_msg}\n"
                f"[bold #F0F6FC]Run wall time:[/bold #F0F6FC]     {run.duration_seconds:.1f} s\n\n"
                f"[#B0B8C4]Execution halted due to unauthorized path access outside the workspace.[/#B0B8C4]"
            )
        else:
            return (
                f"[bold #FF7B72]✗ EXECUTION STOPPED[/bold #FF7B72]\n\n"
                f"[bold #F0F6FC]Reason:[/bold #F0F6FC]            Execution Error\n"
                f"[bold #F0F6FC]Detail:[/bold #F0F6FC]            {err_msg}\n"
                f"[bold #F0F6FC]Run wall time:[/bold #F0F6FC]     {run.duration_seconds:.1f} s"
            )

    def _build_evidence_table(self, run: RunNode) -> Table:
        """Construct authoritative runtime evidence table from state store facts using safe summaries."""
        table = Table(
            title="AUTHORITATIVE RUNTIME EVIDENCE",
            title_style="bold #38BDF8",
            box=box.ROUNDED,
            expand=True,
            header_style="bold #F0F6FC",
            border_style="#30363D",
        )
        table.add_column("Tool", style="bold #F0F6FC", ratio=2)
        table.add_column("Resource / Target", style="#38BDF8", ratio=3)
        table.add_column("Risk", ratio=1)
        table.add_column("Decision", ratio=2)
        table.add_column("Outcome", ratio=3)

        if not run.tool_calls_map:
            table.add_row(
                "[#B0B8C4]None[/#B0B8C4]",
                "[#B0B8C4]No tool invocations recorded for this run[/#B0B8C4]",
                "[#B0B8C4]-[/#B0B8C4]",
                "[#B0B8C4]-[/#B0B8C4]",
                "[#B0B8C4]N/A[/#B0B8C4]",
            )
            return table

        for _, tool in run.tool_calls_map.items():
            # Derive safe target without leaking raw secret arguments
            target = "-"
            if tool.arguments_summary:
                for k in ("path", "station", "symbol", "query", "url", "command"):
                    if k in tool.arguments_summary and tool.arguments_summary[k]:
                        val = str(tool.arguments_summary[k])
                        target = val[:35] + "..." if len(val) > 35 else val
                        break
            if target == "-" and tool.resource_descriptor:
                target = tool.resource_descriptor[:35] + "..." if len(tool.resource_descriptor) > 35 else tool.resource_descriptor
            if target == "-" and tool.error_message:
                m = re.search(r"path\s*['\"]([^'\"]+)['\"]", tool.error_message, re.IGNORECASE)
                if not m:
                    m = re.search(r"boundary:\s*([^\s]+)", tool.error_message, re.IGNORECASE)
                if m:
                    val = m.group(1)
                    target = val[:35] + "..." if len(val) > 35 else val
            if target == "-" and tool.server_name:
                target = tool.server_name

            # Risk column with explicit symbols
            if tool.risk_level:
                risk_str = tool.risk_level.value.upper()
                if risk_str in ("HIGH", "CRITICAL"):
                    risk_cell = f"[bold #FF7B72]▲ {risk_str}[/bold #FF7B72]"
                elif risk_str in ("MEDIUM", "MUTATING"):
                    risk_cell = f"[bold #F2CC60]! {risk_str}[/bold #F2CC60]"
                elif risk_str in ("LOW", "READ_ONLY"):
                    risk_cell = f"[#38BDF8]READ_ONLY[/#38BDF8]"
                else:
                    risk_cell = f"[#B0B8C4]{risk_str}[/#B0B8C4]"
            else:
                risk_cell = "[#B0B8C4]NORMAL[/#B0B8C4]"

            # Decision column: policy decision + optional human confirmation outcome
            dec_str = tool.permission_decision.value.upper() if tool.permission_decision else "ALLOW"
            if tool.confirmation_outcome:
                if tool.confirmation_outcome == "APPROVED":
                    dec_cell = "[bold #56D364]✓ ALLOW (APPROVED)[/bold #56D364]"
                else:
                    dec_cell = f"[bold #FF7B72]✗ DENY ({tool.confirmation_outcome})[/bold #FF7B72]"
            elif dec_str == "ALLOW":
                dec_cell = "[bold #56D364]✓ ALLOW[/bold #56D364]"
            elif dec_str == "DENY":
                dec_cell = "[bold #FF7B72]✗ DENY[/bold #FF7B72]"
            elif dec_str == "REQUIRE_CONFIRMATION":
                dec_cell = "[bold #F2CC60]? REQUIRE_CONFIRMATION[/bold #F2CC60]"
            else:
                dec_cell = f"[bold #F0F6FC]{dec_str}[/bold #F0F6FC]"

            # Outcome column: execution outcome cleanly separated from policy decision
            err_lower = (tool.error_message or "").lower()
            is_workspace_violation = (
                ("workspace" in err_lower and any(w in err_lower for w in ("boundary", "escape", "outside", "denied")))
                or "outside the workspace" in err_lower
            )

            status_upper = tool.status.upper() if tool.status else ""
            if status_upper == "SUCCESS" and not tool.is_error:
                outcome_cell = "[bold #56D364]✓ SUCCESS[/bold #56D364]"
            elif is_workspace_violation:
                outcome_cell = "[bold #FF7B72]✗ BLOCKED — Workspace Boundary[/bold #FF7B72]"
            elif tool.confirmation_outcome == "REJECTED":
                outcome_cell = "[bold #F2CC60]! BLOCKED — Operator Rejected[/bold #F2CC60]"
            elif "unregistered" in err_lower or "not found" in err_lower or "unknown tool" in err_lower:
                outcome_cell = "[bold #FF7B72]✗ BLOCKED — Unregistered Tool[/bold #FF7B72]"
            elif tool.is_error or status_upper in ("ERROR", "FAILED"):
                outcome_cell = "[bold #FF7B72]✗ ERROR[/bold #FF7B72]"
            else:
                outcome_cell = f"[#F2CC60]{tool.status}[/#F2CC60]"

            table.add_row(
                tool.tool_name,
                target,
                risk_cell,
                dec_cell,
                outcome_cell,
            )

        return table

    def refresh_display(self) -> None:
        """Full refresh of timeline, tree, inspector overview, and result pane."""
        self.refresh_timeline()
        self.refresh_tree()
        if self.store.selected_run_id:
            self.inspect_run(self.store.selected_run_id)
            self.refresh_result(self.store.selected_run_id)
        else:
            self.display_run_overview()
            self.refresh_result()

    def explain_selected_run(self) -> str:
        """Reconstruct human-readable architectural explanation from state (never private LLM reasoning)."""
        return self.store.explain_run(self.store.selected_run_id)
