"""Color constants and semantic theme palette for the Agent Harness Operator Console.

Inspiration: GitHub Dark + Grafana Dark + k9s.
Color communicates state, not decoration.
"""

from __future__ import annotations

# Background & Container Panels
BG_DARK = "#0D1117"        # Terminal / application background
PANEL_BG = "#161B22"       # Card / container panel background
PANEL_HEADER = "#21262D"   # Header bar inside panels
BORDER = "#30363D"         # Subtle dividing borders
BORDER_FOCUS = "#58A6FF"   # Focused widget border

# Typography
TEXT_PRIMARY = "#F0F6FC"   # Near-white primary text
TEXT_MUTED = "#B0B8C4"     # Readable light gray secondary / timestamp text
TEXT_ACCENT = "#38BDF8"    # Bright cyan / light blue accent
TEXT_WHITE = "#FFFFFF"

# Semantic Status (Never rely on color alone; pair with symbols)
SUCCESS = "#3FB950"        # ✓ Bright Green: successful execution, ALLOW, CLOSED circuit
WARNING = "#F2CC60"        # ! Bright Amber: require confirmation, HALF_OPEN circuit, retrying
FAILURE = "#FF7B72"        # ✗ Bright Salmon/Red: error, DENY, OPEN circuit, failed run
INFO = "#38BDF8"           # ◆ Bright Cyan: in-flight run, LLM active, neutral info
DELEGATION = "#D2A8FF"     # ⮑ Bright Lavender: multi-agent delegation / specialist
MCP = "#56D4C8"            # ⛯ Bright Turquoise/Cyan: external MCP server / tool invocation

# Common Textual CSS
APP_CSS = f"""
Screen {{
    background: {BG_DARK};
    color: {TEXT_PRIMARY};
}}

Header {{
    background: {PANEL_HEADER};
    color: {TEXT_PRIMARY};
    dock: top;
    height: 1;
}}

Footer {{
    background: {PANEL_HEADER};
    color: {TEXT_MUTED};
    dock: bottom;
    height: 1;
}}

TabbedContent {{
    height: 1fr;
}}

ContentTab {{
    background: {PANEL_BG};
    color: {TEXT_MUTED};
    padding: 0 1;
}}

ContentTab.-active {{
    background: {PANEL_HEADER};
    color: {TEXT_ACCENT};
    text-style: bold;
}}

.panel-card {{
    background: {PANEL_BG};
    border: solid {BORDER};
    padding: 1;
    margin: 0 1 1 0;
}}

.panel-title {{
    text-style: bold;
    color: {TEXT_ACCENT};
    margin-bottom: 1;
}}

.status-badge {{
    padding: 0 1;
}}

#bottom-status-bar {{
    height: 1;
    background: {PANEL_BG};
    border-top: solid {BORDER};
    padding: 0 1;
    margin: 0;
}}


DataTable {{
    background: {PANEL_BG};
    color: {TEXT_PRIMARY};
    border: solid {BORDER};
}}

DataTable > .datatable--cursor {{
    background: #213547;
    color: #FFFFFF;
    text-style: bold;
    border-left: tall #38BDF8;
}}

Tree {{
    background: {PANEL_BG};
    color: {TEXT_PRIMARY};
    border: solid {BORDER};
    padding: 0 1;
}}

Tree:focus {{
    border: solid {BORDER_FOCUS};
}}

RichLog {{
    background: {PANEL_BG};
    color: {TEXT_PRIMARY};
    border: solid {BORDER};
    padding: 0 1;
}}

Input {{
    background: {PANEL_BG};
    color: {TEXT_PRIMARY};
    border: solid {BORDER};
}}

Input:focus {{
    border: solid {BORDER_FOCUS};
}}

Button {{
    background: {PANEL_HEADER};
    color: {TEXT_PRIMARY};
    border: solid {BORDER};
}}

Button:focus {{
    background: {TEXT_ACCENT};
    color: #0D1117;
    text-style: bold;
}}

Button.-primary {{
    background: #238636;
    color: #FFFFFF;
    border: none;
}}

Button.-primary:focus {{
    background: #2EA043;
    color: #FFFFFF;
}}

Button.-warning {{
    background: #9E6A03;
    color: #FFFFFF;
    border: none;
}}

Button.-danger {{
    background: #DA3633;
    color: #FFFFFF;
    border: none;
}}

.obs-btn {{
    width: 100%;
    margin-top: 1;
    background: #21262D;
    color: {TEXT_ACCENT};
    border: solid {BORDER};
}}

.obs-btn:focus {{
    background: {TEXT_ACCENT};
    color: #0D1117;
    text-style: bold;
    border: solid {TEXT_ACCENT};
}}
"""
