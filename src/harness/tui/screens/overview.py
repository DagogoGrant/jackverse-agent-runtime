"""Overview Screen: Instant status, capability counts, governance stance, and telemetry links."""

from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Container, Grid, Horizontal, Vertical, VerticalScroll
from textual.widgets import Button, Label, Static

from harness.config import AppConfig
from harness.tools.base import ToolSource
from harness.tui.health import (
    check_mcp_transport_health,
    check_metrics_endpoint_health,
    check_prometheus_scrape_health,
    check_tempo_health,
    check_workspace_health,
)
from harness.tui.store import RuntimeStateStore
from harness.tui.theme import BORDER, PANEL_BG, SUCCESS, TEXT_ACCENT, TEXT_MUTED, WARNING


class OverviewScreen(VerticalScroll):
    """Overview dashboard providing at-a-glance capability discovery and system health."""

    DEFAULT_CSS = f"""
    OverviewScreen {{
        padding: 1;
    }}
    .overview-grid {{
        layout: grid;
        grid-size: 2 2;
        grid-gutter: 1;
        height: auto;
    }}
    .overview-card {{
        background: {PANEL_BG};
        border: solid {BORDER};
        padding: 1;
        height: auto;
    }}
    .card-title {{
        text-style: bold;
        color: {TEXT_ACCENT};
        margin-bottom: 1;
        border-bottom: solid {BORDER};
        padding-bottom: 1;
    }}
    .prop-row {{
        height: 1;
        margin-bottom: 0;
    }}
    .prop-label {{
        width: 24;
        color: {TEXT_MUTED};
    }}
    .prop-val {{
        color: #FFFFFF;
        text-style: bold;
    }}
    .action-bar {{
        margin-top: 1;
        height: 3;
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

    def compose(self) -> ComposeResult:
        yield Label("AGENT HARNESS OPERATOR CONSOLE · SYSTEM OVERVIEW", classes="panel-title")

        with Container(classes="overview-grid"):
            # Card 1: Runtime
            with Vertical(classes="overview-card", id="card-runtime"):
                yield Label("1. RUNTIME SUBSYSTEM", classes="card-title")
                with Horizontal(classes="prop-row"):
                    yield Label("Harness Core:", classes="prop-label")
                    yield Label("● READY", id="val-core", classes="prop-val")
                with Horizontal(classes="prop-row"):
                    yield Label("LLM Model:", classes="prop-label")
                    yield Label(f"{self.config.llm.model}", classes="prop-val")
                with Horizontal(classes="prop-row"):
                    yield Label("LLM Timeout/Retries:", classes="prop-label")
                    yield Label(f"{self.config.llm.timeout}s / max {self.config.llm.max_retries}", classes="prop-val")
                with Horizontal(classes="prop-row"):
                    yield Label("Workspace Containment:", classes="prop-label")
                    yield Label("● READY", id="val-ws", classes="prop-val")
                with Horizontal(classes="prop-row"):
                    yield Label("Persistent Memory:", classes="prop-label")
                    yield Label(
                        f"{'● ACTIVE' if self.config.memory.enabled else '○ DISABLED'} ({self.config.memory.retrieval_strategy})",
                        classes="prop-val",
                    )

            # Card 2: Capabilities (Dynamically Derived)
            with Vertical(classes="overview-card", id="card-capabilities"):
                yield Label("2. DYNAMIC CAPABILITIES", classes="card-title")
                with Horizontal(classes="prop-row"):
                    yield Label("Built-in Tools:", classes="prop-label")
                    yield Label("Loading...", id="val-builtin-tools", classes="prop-val")
                with Horizontal(classes="prop-row"):
                    yield Label("MCP Servers:", classes="prop-label")
                    yield Label("Loading...", id="val-mcp-servers", classes="prop-val")
                with Horizontal(classes="prop-row"):
                    yield Label("MCP Tools Discovered:", classes="prop-label")
                    yield Label("Loading...", id="val-mcp-tools", classes="prop-val")
                with Horizontal(classes="prop-row"):
                    yield Label("Specialist Agents:", classes="prop-label")
                    yield Label("Loading...", id="val-specialists", classes="prop-val")
                with Horizontal(classes="prop-row"):
                    yield Label("Transport MCP Status:", classes="prop-label")
                    yield Label("Loading...", id="val-mcp-health", classes="prop-val")

            # Card 3: Governance & Security
            with Vertical(classes="overview-card", id="card-governance"):
                yield Label("3. GOVERNANCE & SECURITY", classes="card-title")
                with Horizontal(classes="prop-row"):
                    yield Label("Default Stance:", classes="prop-label")
                    yield Label(f"{self.config.permissions.default_stance.upper()}", classes="prop-val")
                with Horizontal(classes="prop-row"):
                    yield Label("Policy Rules:", classes="prop-label")
                    yield Label(f"{len(self.config.permissions.rules)} rules active", classes="prop-val")
                with Horizontal(classes="prop-row"):
                    yield Label("Contextual Risk Rules:", classes="prop-label")
                    yield Label("● ACTIVE (Resource & Path matching)", classes="prop-val")
                with Horizontal(classes="prop-row"):
                    yield Label("Confirmation Protocol:", classes="prop-label")
                    yield Label("● SINGLE-USE ANTI-REPLAY BINDING", classes="prop-val")
                with Horizontal(classes="prop-row"):
                    yield Label("Workspace Boundary:", classes="prop-label")
                    yield Label("● HARD BARRIER (Workspace.resolve)", classes="prop-val")

            # Card 4: Observability (Evidence-Based Checks)
            with Vertical(classes="overview-card", id="card-telemetry"):
                yield Label("4. OBSERVABILITY & TELEMETRY", classes="card-title")
                with Horizontal(classes="prop-row"):
                    yield Label("Harness Metrics (:9101):", classes="prop-label")
                    yield Label("Loading...", id="val-metrics", classes="prop-val")
                with Horizontal(classes="prop-row"):
                    yield Label("Prometheus Scraper:", classes="prop-label")
                    yield Label("Loading...", id="val-prometheus", classes="prop-val")
                with Horizontal(classes="prop-row"):
                    yield Label("Tempo Trace Collector:", classes="prop-label")
                    yield Label("Loading...", id="val-tempo", classes="prop-val")
                with Horizontal(classes="prop-row"):
                    yield Label("Structured Logging:", classes="prop-label")
                    yield Label(
                        "● ACTIVE" if self.config.observability.logging.structured_enabled else "○ DISABLED",
                        classes="prop-val",
                    )
                with Horizontal(classes="prop-row"):
                    yield Label("TUI Observer:", classes="prop-label")
                    yield Label("● SUBSCRIBED (LifecycleEventBus)", classes="prop-val")

        with Horizontal(classes="action-bar"):
            yield Button("↻ Probe System Health", id="btn-refresh-health", variant="primary")
            yield Button("Grafana Dashboard ↗", id="btn-open-grafana")
            yield Button("Tempo Traces ↗", id="btn-open-tempo")

    def on_mount(self) -> None:
        self.refresh_overview()

    def refresh_overview(self) -> None:
        """Derive all counts and query health endpoints safely."""
        # Dynamic Counts from Store
        builtin_tools = [t for t in self.store.tools.values() if t.source == ToolSource.BUILTIN]
        mcp_tools = [t for t in self.store.tools.values() if t.source == ToolSource.MCP]
        specialists = [a for a in self.store.agents.values() if a.agent_type != "Root Orchestrator"]

        self.query_one("#val-builtin-tools", Label).update(f"{len(builtin_tools)} registered")
        self.query_one("#val-mcp-servers", Label).update(f"{len(self.config.mcp_servers)} configured")
        self.query_one("#val-mcp-tools", Label).update(f"{len(mcp_tools)} tools discovered")
        self.query_one("#val-specialists", Label).update(f"{len(specialists)} roles ({', '.join(a.role for a in specialists)})")

        # Workspace Health
        ws_status, _ = check_workspace_health(self.config.tools.workspace_root)
        self.query_one("#val-ws", Label).update(ws_status)

        # Evidence-based telemetry health probes
        p_status, _ = check_prometheus_scrape_health()
        self.query_one("#val-prometheus", Label).update(p_status)

        m_status, _ = check_metrics_endpoint_health(port=self.config.observability.metrics.port)
        self.query_one("#val-metrics", Label).update(m_status)

        t_status, _ = check_tempo_health()
        self.query_one("#val-tempo", Label).update(t_status)

        mcp_status, _ = check_mcp_transport_health()
        self.query_one("#val-mcp-health", Label).update(mcp_status)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn-refresh-health":
            self.refresh_overview()
            self.app.notify("System health probed successfully.", severity="information")
        elif event.button.id == "btn-open-grafana":
            self.app.action_show_grafana()
        elif event.button.id == "btn-open-tempo":
            self.app.action_show_tempo()
