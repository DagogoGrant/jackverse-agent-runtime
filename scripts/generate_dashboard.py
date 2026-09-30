#!/usr/bin/env python3
"""Canonical generator for the version-controlled production Grafana dashboard JSON.

Source of Truth Policy:
    This script is the canonical definition of the Agent Harness Runtime Grafana dashboard.
    Running this script regenerates 'observability/grafana/dashboards/agent-harness-runtime.json'.
    The generated JSON is committed into Git and provisioned into Grafana with:
        editable: false
        allowUiUpdates: false
    Guaranteeing that repository code remains the authoritative single source of truth.
"""

from __future__ import annotations

import json
from pathlib import Path


def build_dashboard() -> dict:
    dashboard = {
        "annotations": {"list": []},
        "editable": False,
        "fiscalYearStartMonth": 0,
        "graphTooltip": 1,
        "id": None,
        "links": [],
        "liveNow": False,
        "panels": [
            # =================================================================
            # SECTION 1: SYSTEM HEALTH
            # =================================================================
            {
                "collapsed": False,
                "gridPos": {"h": 1, "w": 24, "x": 0, "y": 0},
                "id": 100,
                "title": "SYSTEM HEALTH",
                "type": "row",
            },
            # Top Executive Stat Cards
            {
                "id": 1,
                "title": "Harness Scrape Health",
                "description": "Liveness probe of the agent harness Prometheus metrics endpoint (:9101).",
                "type": "stat",
                "gridPos": {"h": 4, "w": 3, "x": 0, "y": 1},
                "datasource": {"type": "prometheus", "uid": "prometheus"},
                "targets": [
                    {
                        "expr": 'up{job="agent-harness"}',
                        "instant": True,
                        "refId": "A",
                    }
                ],
                "fieldConfig": {
                    "defaults": {
                        "noValue": "DOWN",
                        "mappings": [
                            {
                                "type": "value",
                                "options": {
                                    "1": {"text": "UP", "color": "green"},
                                    "0": {"text": "DOWN", "color": "red"},
                                },
                            }
                        ],
                        "thresholds": {
                            "mode": "absolute",
                            "steps": [
                                {"color": "red", "value": None},
                                {"color": "green", "value": 1},
                            ],
                        },
                    }
                },
                "options": {
                    "reduceOptions": {"calcs": ["lastNotNull"], "values": False},
                    "textMode": "auto",
                },
            },
            {
                "id": 2,
                "title": "Runs in Range",
                "description": "Total completed agent reasoning turns across all roles in the selected time range.",
                "type": "stat",
                "gridPos": {"h": 4, "w": 4, "x": 3, "y": 1},
                "datasource": {"type": "prometheus", "uid": "prometheus"},
                "targets": [
                    {
                        "expr": 'sum(increase(harness_agent_runs_total{agent_role=~"$agent_role"}[$__range])) or vector(0)',
                        "instant": True,
                        "refId": "A",
                    }
                ],
                "fieldConfig": {
                    "defaults": {
                        "unit": "short",
                        "noValue": "0",
                        "color": {"mode": "fixed", "fixedColor": "blue"},
                        "thresholds": {"mode": "absolute", "steps": [{"color": "blue", "value": None}]},
                    }
                },
                "options": {
                    "reduceOptions": {"calcs": ["lastNotNull"], "values": False},
                    "textMode": "auto",
                },
            },
            {
                "id": 3,
                "title": "Active Turns",
                "description": "Current number of concurrently executing agent turns in the runtime.",
                "type": "stat",
                "gridPos": {"h": 4, "w": 3, "x": 7, "y": 1},
                "datasource": {"type": "prometheus", "uid": "prometheus"},
                "targets": [
                    {
                        "expr": 'sum(harness_agent_runs_active{agent_role=~"$agent_role"}) or vector(0)',
                        "instant": True,
                        "refId": "A",
                    }
                ],
                "fieldConfig": {
                    "defaults": {
                        "unit": "short",
                        "noValue": "0",
                        "thresholds": {
                            "mode": "absolute",
                            "steps": [
                                {"color": "green", "value": None},
                                {"color": "orange", "value": 3},
                                {"color": "red", "value": 6},
                            ],
                        },
                    }
                },
                "options": {
                    "reduceOptions": {"calcs": ["lastNotNull"], "values": False},
                    "textMode": "auto",
                },
            },
            {
                "id": 4,
                "title": "Success Rate",
                "description": "Percentage of agent turns that completed successfully within the selected time range. Communicates 'No activity' when no runs occurred.",
                "type": "stat",
                "gridPos": {"h": 4, "w": 4, "x": 10, "y": 1},
                "datasource": {"type": "prometheus", "uid": "prometheus"},
                "targets": [
                    {
                        "expr": '((sum(increase(harness_agent_runs_total{status="success", agent_role=~"$agent_role"}[$__range])) or vector(0)) / clamp_min(sum(increase(harness_agent_runs_total{agent_role=~"$agent_role"}[$__range])), 1e-6) * 100) and (sum(increase(harness_agent_runs_total{agent_role=~"$agent_role"}[$__range])) > 0)',
                        "instant": True,
                        "refId": "A",
                    }
                ],
                "fieldConfig": {
                    "defaults": {
                        "min": 0,
                        "max": 100,
                        "unit": "percent",
                        "noValue": "No activity",
                        "thresholds": {
                            "mode": "absolute",
                            "steps": [
                                {"color": "red", "value": None},
                                {"color": "orange", "value": 80},
                                {"color": "green", "value": 95},
                            ],
                        },
                    }
                },
                "options": {
                    "reduceOptions": {"calcs": ["lastNotNull"], "values": False},
                    "textMode": "auto",
                },
            },
            {
                "id": 5,
                "title": "Failure Rate",
                "description": "Percentage of agent turns terminating in error within the selected time range. Returns 0.0% when runs exist without errors.",
                "type": "stat",
                "gridPos": {"h": 4, "w": 5, "x": 14, "y": 1},
                "datasource": {"type": "prometheus", "uid": "prometheus"},
                "targets": [
                    {
                        "expr": '((sum(increase(harness_agent_runs_total{status="error", agent_role=~"$agent_role"}[$__range])) or vector(0)) / clamp_min(sum(increase(harness_agent_runs_total{agent_role=~"$agent_role"}[$__range])), 1e-6) * 100) and (sum(increase(harness_agent_runs_total{agent_role=~"$agent_role"}[$__range])) > 0)',
                        "instant": True,
                        "refId": "A",
                    }
                ],
                "fieldConfig": {
                    "defaults": {
                        "min": 0,
                        "max": 100,
                        "unit": "percent",
                        "noValue": "No activity",
                        "thresholds": {
                            "mode": "absolute",
                            "steps": [
                                {"color": "green", "value": None},
                                {"color": "orange", "value": 5},
                                {"color": "red", "value": 15},
                            ],
                        },
                    }
                },
                "options": {
                    "reduceOptions": {"calcs": ["lastNotNull"], "values": False},
                    "textMode": "auto",
                },
            },
            {
                "id": 6,
                "title": "p95 Turn Latency",
                "description": "95th percentile end-to-end agent turn latency across the selected range.",
                "type": "stat",
                "gridPos": {"h": 4, "w": 5, "x": 19, "y": 1},
                "datasource": {"type": "prometheus", "uid": "prometheus"},
                "targets": [
                    {
                        "expr": 'histogram_quantile(0.95, sum by (le) (rate(harness_agent_run_duration_seconds_bucket{agent_role=~"$agent_role"}[$__range])))',
                        "instant": True,
                        "refId": "A",
                    }
                ],
                "fieldConfig": {
                    "defaults": {
                        "unit": "s",
                        "noValue": "—",
                        "thresholds": {
                            "mode": "absolute",
                            "steps": [
                                {"color": "green", "value": None},
                                {"color": "orange", "value": 10},
                                {"color": "red", "value": 25},
                            ],
                        },
                    }
                },
                "options": {
                    "reduceOptions": {"calcs": ["lastNotNull"], "values": False},
                    "textMode": "auto",
                },
            },
            # Secondary Compact Operational Stats
            {
                "id": 7,
                "title": "Total Tokens (Range)",
                "description": "Combined prompt and completion tokens billed across models in the selected range.",
                "type": "stat",
                "gridPos": {"h": 3, "w": 6, "x": 0, "y": 5},
                "datasource": {"type": "prometheus", "uid": "prometheus"},
                "targets": [
                    {
                        "expr": '(sum(increase(harness_llm_input_tokens_total{model=~"$model"}[$__range])) or vector(0)) + (sum(increase(harness_llm_output_tokens_total{model=~"$model"}[$__range])) or vector(0))',
                        "instant": True,
                        "refId": "A",
                    }
                ],
                "fieldConfig": {
                    "defaults": {
                        "unit": "short",
                        "noValue": "0",
                        "color": {"mode": "fixed", "fixedColor": "text"},
                    }
                },
                "options": {"reduceOptions": {"calcs": ["lastNotNull"], "values": False}},
            },
            {
                "id": 8,
                "title": "Tool Invocations (Range)",
                "description": "Total tool execution dispatches through ToolExecutor in the selected range.",
                "type": "stat",
                "gridPos": {"h": 3, "w": 6, "x": 6, "y": 5},
                "datasource": {"type": "prometheus", "uid": "prometheus"},
                "targets": [
                    {
                        "expr": 'sum(increase(harness_tool_calls_total{tool_source=~"$tool_source", tool_name=~"$tool_name"}[$__range])) or vector(0)',
                        "instant": True,
                        "refId": "A",
                    }
                ],
                "fieldConfig": {
                    "defaults": {
                        "unit": "short",
                        "noValue": "0",
                        "color": {"mode": "fixed", "fixedColor": "text"},
                    }
                },
                "options": {"reduceOptions": {"calcs": ["lastNotNull"], "values": False}},
            },
            {
                "id": 9,
                "title": "Delegations (Range)",
                "description": "Total sub-agent delegations orchestrated in the selected range.",
                "type": "stat",
                "gridPos": {"h": 3, "w": 6, "x": 12, "y": 5},
                "datasource": {"type": "prometheus", "uid": "prometheus"},
                "targets": [
                    {
                        "expr": 'sum(increase(harness_delegations_total[$__range])) or vector(0)',
                        "instant": True,
                        "refId": "A",
                    }
                ],
                "fieldConfig": {
                    "defaults": {
                        "unit": "short",
                        "noValue": "0",
                        "color": {"mode": "fixed", "fixedColor": "text"},
                    }
                },
                "options": {"reduceOptions": {"calcs": ["lastNotNull"], "values": False}},
            },
            {
                "id": 10,
                "title": "Confirmed Actions (Range)",
                "description": "Total human confirmations approved for sensitive actions in the selected range.",
                "type": "stat",
                "gridPos": {"h": 3, "w": 6, "x": 18, "y": 5},
                "datasource": {"type": "prometheus", "uid": "prometheus"},
                "targets": [
                    {
                        "expr": 'sum(increase(harness_permission_confirmations_total{status="approved"}[$__range])) or vector(0)',
                        "instant": True,
                        "refId": "A",
                    }
                ],
                "fieldConfig": {
                    "defaults": {
                        "unit": "short",
                        "noValue": "0",
                        "color": {"mode": "fixed", "fixedColor": "text"},
                    }
                },
                "options": {"reduceOptions": {"calcs": ["lastNotNull"], "values": False}},
            },
            # =================================================================
            # SECTION 2: RUNTIME PERFORMANCE
            # =================================================================
            {
                "collapsed": False,
                "gridPos": {"h": 1, "w": 24, "x": 0, "y": 8},
                "id": 200,
                "title": "RUNTIME PERFORMANCE",
                "type": "row",
            },
            {
                "id": 11,
                "title": "Agent Run Latency (p50 / p95)",
                "description": "50th and 95th percentile duration of agent turns over time.",
                "type": "timeseries",
                "gridPos": {"h": 7, "w": 12, "x": 0, "y": 9},
                "datasource": {"type": "prometheus", "uid": "prometheus"},
                "targets": [
                    {
                        "expr": 'histogram_quantile(0.50, sum by (le) (rate(harness_agent_run_duration_seconds_bucket{agent_role=~"$agent_role"}[$__rate_interval])))',
                        "legendFormat": "p50 Turn Latency",
                        "refId": "A",
                    },
                    {
                        "expr": 'histogram_quantile(0.95, sum by (le) (rate(harness_agent_run_duration_seconds_bucket{agent_role=~"$agent_role"}[$__rate_interval])))',
                        "legendFormat": "p95 Turn Latency",
                        "refId": "B",
                    },
                ],
                "fieldConfig": {
                    "defaults": {
                        "unit": "s",
                        "custom": {
                            "drawStyle": "line",
                            "lineInterpolation": "smooth",
                            "fillOpacity": 8,
                        },
                    }
                },
            },
            {
                "id": 12,
                "title": "LLM Inference Latency (p50 / p95 by Model)",
                "description": "Monotonic provider inference call duration partitioned by model tier.",
                "type": "timeseries",
                "gridPos": {"h": 7, "w": 12, "x": 12, "y": 9},
                "datasource": {"type": "prometheus", "uid": "prometheus"},
                "targets": [
                    {
                        "expr": 'histogram_quantile(0.50, sum by (le, model) (rate(harness_llm_call_duration_seconds_bucket{model=~"$model"}[$__rate_interval])))',
                        "legendFormat": "p50 - {{model}}",
                        "refId": "A",
                    },
                    {
                        "expr": 'histogram_quantile(0.95, sum by (le, model) (rate(harness_llm_call_duration_seconds_bucket{model=~"$model"}[$__rate_interval])))',
                        "legendFormat": "p95 - {{model}}",
                        "refId": "B",
                    },
                ],
                "fieldConfig": {
                    "defaults": {
                        "unit": "s",
                        "custom": {
                            "drawStyle": "line",
                            "lineInterpolation": "smooth",
                            "fillOpacity": 8,
                        },
                    }
                },
            },
            {
                "id": 13,
                "title": "Run Volume & Outcomes (Over Time)",
                "description": "Time series of completed agent turns categorized by success and error status.",
                "type": "timeseries",
                "gridPos": {"h": 7, "w": 12, "x": 0, "y": 16},
                "datasource": {"type": "prometheus", "uid": "prometheus"},
                "targets": [
                    {
                        "expr": 'sum by (status) (rate(harness_agent_runs_total{agent_role=~"$agent_role"}[$__rate_interval]))',
                        "legendFormat": "{{status}}",
                        "refId": "A",
                    }
                ],
                "fieldConfig": {
                    "defaults": {
                        "unit": "ops",
                        "custom": {
                            "drawStyle": "bars",
                            "stacking": {"mode": "normal"},
                            "fillOpacity": 60,
                        },
                    }
                },
            },
            {
                "id": 14,
                "title": "Component Latency Overview (p95 Comparison)",
                "description": "Comparative 95th percentile latency across Tools, Memory Operations, and Sub-Agent Delegations.",
                "type": "timeseries",
                "gridPos": {"h": 7, "w": 12, "x": 12, "y": 16},
                "datasource": {"type": "prometheus", "uid": "prometheus"},
                "targets": [
                    {
                        "expr": 'histogram_quantile(0.95, sum by (le) (rate(harness_tool_call_duration_seconds_bucket{tool_source=~"$tool_source"}[$__rate_interval])))',
                        "legendFormat": "Tool Execution (p95)",
                        "refId": "A",
                    },
                    {
                        "expr": 'histogram_quantile(0.95, sum by (le) (rate(harness_memory_operation_duration_seconds_bucket[$__rate_interval])))',
                        "legendFormat": "Memory Operation (p95)",
                        "refId": "B",
                    },
                    {
                        "expr": 'histogram_quantile(0.95, sum by (le) (rate(harness_delegation_duration_seconds_bucket[$__rate_interval])))',
                        "legendFormat": "Sub-Agent Delegation (p95)",
                        "refId": "C",
                    },
                ],
                "fieldConfig": {
                    "defaults": {
                        "unit": "s",
                        "custom": {
                            "drawStyle": "line",
                            "lineInterpolation": "smooth",
                            "fillOpacity": 5,
                        },
                    }
                },
            },
            # =================================================================
            # SECTION 3: LLM & TOKEN ECONOMICS
            # =================================================================
            {
                "collapsed": True,
                "gridPos": {"h": 1, "w": 24, "x": 0, "y": 23},
                "id": 300,
                "title": "LLM & TOKEN ECONOMICS",
                "type": "row",
            },
            {
                "id": 15,
                "title": "Token Consumption Rate",
                "description": "Provider-reported prompt and completion token burn rate per model.",
                "type": "timeseries",
                "gridPos": {"h": 7, "w": 12, "x": 0, "y": 24},
                "datasource": {"type": "prometheus", "uid": "prometheus"},
                "targets": [
                    {
                        "expr": 'sum by (model) (rate(harness_llm_input_tokens_total{model=~"$model"}[$__rate_interval]))',
                        "legendFormat": "Input Tokens (Prompt) - {{model}}",
                        "refId": "A",
                    },
                    {
                        "expr": 'sum by (model) (rate(harness_llm_output_tokens_total{model=~"$model"}[$__rate_interval]))',
                        "legendFormat": "Output Tokens (Completion) - {{model}}",
                        "refId": "B",
                    },
                ],
                "fieldConfig": {
                    "defaults": {
                        "unit": "tokens/s",
                        "custom": {"drawStyle": "line", "fillOpacity": 12},
                    }
                },
            },
            {
                "id": 16,
                "title": "LLM Calls & Retries by Model",
                "description": "Rate of logical inference calls partitioned by status, alongside automated exponential-backoff retries.",
                "type": "timeseries",
                "gridPos": {"h": 7, "w": 12, "x": 12, "y": 24},
                "datasource": {"type": "prometheus", "uid": "prometheus"},
                "targets": [
                    {
                        "expr": 'sum by (status, failure_category) (rate(harness_llm_calls_total{model=~"$model"}[$__rate_interval]))',
                        "legendFormat": "Calls: {{status}} ({{failure_category}})",
                        "refId": "A",
                    },
                    {
                        "expr": 'sum by (model) (rate(harness_llm_retries_total{model=~"$model"}[$__rate_interval]))',
                        "legendFormat": "Retries: {{model}}",
                        "refId": "B",
                    },
                ],
                "fieldConfig": {
                    "defaults": {
                        "unit": "ops",
                        "custom": {
                            "drawStyle": "bars",
                            "fillOpacity": 40,
                        },
                    }
                },
            },
            # =================================================================
            # SECTION 4: TOOLS & MEMORY
            # =================================================================
            {
                "collapsed": True,
                "gridPos": {"h": 1, "w": 24, "x": 0, "y": 31},
                "id": 400,
                "title": "TOOLS & MEMORY",
                "type": "row",
            },
            {
                "id": 17,
                "title": "Tool Invocations by Source",
                "description": "Invocation throughput comparing built-in filesystem tools vs. external Model Context Protocol (MCP) tools.",
                "type": "timeseries",
                "gridPos": {"h": 7, "w": 12, "x": 0, "y": 32},
                "datasource": {"type": "prometheus", "uid": "prometheus"},
                "targets": [
                    {
                        "expr": 'sum by (tool_source) (rate(harness_tool_calls_total{tool_source=~"$tool_source", tool_name=~"$tool_name"}[$__rate_interval]))',
                        "legendFormat": "{{tool_source}}",
                        "refId": "A",
                    }
                ],
                "fieldConfig": {
                    "defaults": {
                        "unit": "ops",
                        "custom": {"drawStyle": "line", "fillOpacity": 15},
                    }
                },
            },
            {
                "id": 18,
                "title": "Top Tools & Reliability",
                "description": "Ranking of the most frequently invoked tools and their execution outcome in the selected range.",
                "type": "timeseries",
                "gridPos": {"h": 7, "w": 12, "x": 12, "y": 32},
                "datasource": {"type": "prometheus", "uid": "prometheus"},
                "targets": [
                    {
                        "expr": 'topk(6, sum by (tool_name, status) (increase(harness_tool_calls_total{tool_source=~"$tool_source", tool_name=~"$tool_name"}[$__range])))',
                        "legendFormat": "{{tool_name}} - {{status}}",
                        "refId": "A",
                    }
                ],
                "fieldConfig": {
                    "defaults": {
                        "unit": "short",
                        "custom": {
                            "drawStyle": "bars",
                            "stacking": {"mode": "normal"},
                            "fillOpacity": 60,
                        },
                    }
                },
            },
            {
                "id": 19,
                "title": "Memory Operations by Type",
                "description": "Throughput of long-term memory operations: admission, quarantine, retrieval, and supersession.",
                "type": "timeseries",
                "gridPos": {"h": 7, "w": 12, "x": 0, "y": 39},
                "datasource": {"type": "prometheus", "uid": "prometheus"},
                "targets": [
                    {
                        "expr": "sum by (operation_type, status) (rate(harness_memory_operations_total[$__rate_interval]))",
                        "legendFormat": "{{operation_type}} - {{status}}",
                        "refId": "A",
                    }
                ],
                "fieldConfig": {
                    "defaults": {
                        "unit": "ops",
                        "custom": {"drawStyle": "bars", "fillOpacity": 40},
                    }
                },
            },
            {
                "id": 20,
                "title": "Memory Operation Latency (p50 / p95)",
                "description": "Quantiles for SQLite relational storage and vector embedding retrieval latencies.",
                "type": "timeseries",
                "gridPos": {"h": 7, "w": 12, "x": 12, "y": 39},
                "datasource": {"type": "prometheus", "uid": "prometheus"},
                "targets": [
                    {
                        "expr": "histogram_quantile(0.50, sum by (le, operation_type) (rate(harness_memory_operation_duration_seconds_bucket[$__rate_interval])))",
                        "legendFormat": "p50 - {{operation_type}}",
                        "refId": "A",
                    },
                    {
                        "expr": "histogram_quantile(0.95, sum by (le, operation_type) (rate(harness_memory_operation_duration_seconds_bucket[$__rate_interval])))",
                        "legendFormat": "p95 - {{operation_type}}",
                        "refId": "B",
                    },
                ],
                "fieldConfig": {
                    "defaults": {
                        "unit": "s",
                        "custom": {
                            "drawStyle": "line",
                            "lineInterpolation": "smooth",
                            "fillOpacity": 8,
                        },
                    }
                },
            },
            # =================================================================
            # SECTION 5: SECURITY & GOVERNANCE
            # =================================================================
            {
                "collapsed": False,
                "gridPos": {"h": 1, "w": 24, "x": 0, "y": 46},
                "id": 500,
                "title": "SECURITY & GOVERNANCE",
                "type": "row",
            },
            # First-Class Decision Counters
            {
                "id": 21,
                "title": "ALLOW Decisions",
                "description": "Total pre-execution tool authorisations permitted immediately by role-based policy rules in range.",
                "type": "stat",
                "gridPos": {"h": 3, "w": 8, "x": 0, "y": 47},
                "datasource": {"type": "prometheus", "uid": "prometheus"},
                "targets": [
                    {
                        "expr": 'sum(increase(harness_permission_decisions_total{decision="allow"}[$__range])) or vector(0)',
                        "instant": True,
                        "refId": "A",
                    }
                ],
                "fieldConfig": {
                    "defaults": {
                        "unit": "short",
                        "noValue": "0",
                        "thresholds": {"mode": "absolute", "steps": [{"color": "green", "value": None}]},
                    }
                },
                "options": {"reduceOptions": {"calcs": ["lastNotNull"], "values": False}},
            },
            {
                "id": 22,
                "title": "REQUIRE_CONFIRMATION",
                "description": "Total mutating actions halted at the security boundary for interactive human approval in range.",
                "type": "stat",
                "gridPos": {"h": 3, "w": 8, "x": 8, "y": 47},
                "datasource": {"type": "prometheus", "uid": "prometheus"},
                "targets": [
                    {
                        "expr": 'sum(increase(harness_permission_decisions_total{decision="require_confirmation"}[$__range])) or vector(0)',
                        "instant": True,
                        "refId": "A",
                    }
                ],
                "fieldConfig": {
                    "defaults": {
                        "unit": "short",
                        "noValue": "0",
                        "thresholds": {"mode": "absolute", "steps": [{"color": "orange", "value": None}]},
                    }
                },
                "options": {"reduceOptions": {"calcs": ["lastNotNull"], "values": False}},
            },
            {
                "id": 23,
                "title": "DENY Decisions",
                "description": "Total unauthorized or out-of-boundary tool calls blocked fail-closed by policy in range. Note: Denied action != failed policy system (reflects successful proactive policy enforcement).",
                "type": "stat",
                "gridPos": {"h": 3, "w": 8, "x": 16, "y": 47},
                "datasource": {"type": "prometheus", "uid": "prometheus"},
                "targets": [
                    {
                        "expr": 'sum(increase(harness_permission_decisions_total{decision="deny"}[$__range])) or vector(0)',
                        "instant": True,
                        "refId": "A",
                    }
                ],
                "fieldConfig": {
                    "defaults": {
                        "unit": "short",
                        "noValue": "0",
                        "thresholds": {"mode": "absolute", "steps": [{"color": "red", "value": None}]},
                    }
                },
                "options": {"reduceOptions": {"calcs": ["lastNotNull"], "values": False}},
            },
            {
                "id": 24,
                "title": "Permission Decisions Timeline",
                "description": "Rate of authorization decisions evaluated by the PermissionManager across principals.",
                "type": "timeseries",
                "gridPos": {"h": 7, "w": 12, "x": 0, "y": 50},
                "datasource": {"type": "prometheus", "uid": "prometheus"},
                "targets": [
                    {
                        "expr": "sum by (decision) (rate(harness_permission_decisions_total[$__rate_interval]))",
                        "legendFormat": "{{decision}}",
                        "refId": "A",
                    }
                ],
                "fieldConfig": {
                    "defaults": {
                        "unit": "ops",
                        "custom": {
                            "drawStyle": "line",
                            "lineInterpolation": "smooth",
                            "fillOpacity": 12,
                        },
                    }
                },
            },
            {
                "id": 25,
                "title": "Human Confirmation Outcomes",
                "description": "Resolution of interactive confirmation requests (approved vs. rejected).",
                "type": "timeseries",
                "gridPos": {"h": 7, "w": 12, "x": 12, "y": 50},
                "datasource": {"type": "prometheus", "uid": "prometheus"},
                "targets": [
                    {
                        "expr": "sum by (status) (rate(harness_permission_confirmations_total[$__rate_interval]))",
                        "legendFormat": "{{status}}",
                        "refId": "A",
                    }
                ],
                "fieldConfig": {
                    "defaults": {
                        "unit": "ops",
                        "custom": {
                            "drawStyle": "bars",
                            "fillOpacity": 40,
                        },
                    }
                },
            },
            # =================================================================
            # SECTION 6: MULTI-AGENT EXECUTION
            # =================================================================
            {
                "collapsed": False,
                "gridPos": {"h": 1, "w": 24, "x": 0, "y": 57},
                "id": 600,
                "title": "MULTI-AGENT EXECUTION",
                "type": "row",
            },
            {
                "id": 26,
                "title": "Sub-Agent Delegation Volume by Specialist",
                "description": "Delegated task volume dispatched by the root orchestrator to specialized child agents.",
                "type": "timeseries",
                "gridPos": {"h": 7, "w": 12, "x": 0, "y": 58},
                "datasource": {"type": "prometheus", "uid": "prometheus"},
                "targets": [
                    {
                        "expr": 'sum by (child_role, status) (rate(harness_delegations_total[$__rate_interval]))',
                        "legendFormat": "{{child_role}} ({{status}})",
                        "refId": "A",
                    }
                ],
                "fieldConfig": {
                    "defaults": {
                        "unit": "reqps",
                        "custom": {
                            "drawStyle": "bars",
                            "stacking": {"mode": "normal"},
                            "fillOpacity": 70,
                        },
                    }
                },
            },
            {
                "id": 27,
                "title": "Sub-Agent Delegation Latency (p50 / p95)",
                "description": "Latency quantiles of sub-agent turn execution from delegation start to completion.",
                "type": "timeseries",
                "gridPos": {"h": 7, "w": 12, "x": 12, "y": 58},
                "datasource": {"type": "prometheus", "uid": "prometheus"},
                "targets": [
                    {
                        "expr": 'histogram_quantile(0.50, sum by (le, child_role) (rate(harness_delegation_duration_seconds_bucket[$__rate_interval])))',
                        "legendFormat": "p50 - {{child_role}}",
                        "refId": "A",
                    },
                    {
                        "expr": 'histogram_quantile(0.95, sum by (le, child_role) (rate(harness_delegation_duration_seconds_bucket[$__rate_interval])))',
                        "legendFormat": "p95 - {{child_role}}",
                        "refId": "B",
                    },
                ],
                "fieldConfig": {
                    "defaults": {
                        "unit": "s",
                        "custom": {
                            "drawStyle": "line",
                            "lineInterpolation": "smooth",
                            "fillOpacity": 8,
                        },
                    }
                },
            },
            {
                "id": 28,
                "title": "Multi-Principal Routing Topology & Governance Invariants",
                "description": "Architectural reference for principal isolation, tool registry partitioning, and budget ledger.",
                "type": "text",
                "gridPos": {"h": 6, "w": 24, "x": 0, "y": 65},
                "options": {
                    "mode": "markdown",
                    "content": (
                        "### Governed Multi-Principal Architecture\n\n"
                        "```text\n"
                        "                     Root Orchestrator (orchestrator)\n"
                        "                     │  • ToolRegistry: [delegate_task, create_file (CONFIRM REQUIRED)]\n"
                        "                     │  • HierarchicalBudgetLedger (Anti-Multiplication Authority)\n"
                        "                     │\n"
                        "          ┌──────────┴─────────────────────────┐\n"
                        "          ▼ delegate_task                      ▼ delegate_task\n"
                        "   workspace_analyst                    transport_specialist\n"
                        "   • ToolRegistry: [read_file,          • ToolRegistry: [find_connection (MCP)]\n"
                        "                    list_dir, search]   • Conversational Context: Isolated\n"
                        "   • Conversational Context: Isolated   • Budget Slice: max_steps <= remaining_parent\n"
                        "   • Budget Slice: Sliced & Reconciled  • Enforcement: Shared PermissionManager\n"
                        "```\n\n"
                        "**Security Invariants**: Sub-agents receive fresh, role-specific `ToolExecutor` instances with least-privilege tool subsets. Conversational history does not leak from parent turns, and budget cannot multiply across descendants."
                    ),
                },
            },
            # =================================================================
            # SECTION 7: INVESTIGATION & DIAGNOSTICS
            # =================================================================
            {
                "collapsed": True,
                "gridPos": {"h": 1, "w": 24, "x": 0, "y": 71},
                "id": 700,
                "title": "INVESTIGATION & DIAGNOSTICS",
                "type": "row",
            },
            {
                "id": 29,
                "title": "Root-Cause Investigation & Distributed Trace Inspection (Tempo)",
                "description": "Standard operating procedure for diagnosing agent execution anomalies.",
                "type": "text",
                "gridPos": {"h": 7, "w": 24, "x": 0, "y": 72},
                "options": {
                    "mode": "markdown",
                    "content": (
                        "### Incident Investigation & Root-Cause Workflow\n\n"
                        "When an operational anomaly or failure is detected on this dashboard, follow this deterministic four-step diagnostic procedure:\n\n"
                        "1. **Isolate Principal & Failure Mode**: Check **Failure Rate (%)** and **Run Volume & Outcomes**. Use the `$agent_role` and `$model` variables above to determine whether errors are isolated to a sub-agent specialist (`workspace_analyst`, `transport_specialist`) or the root `orchestrator`.\n"
                        "2. **Verify Policy & Permissions**: Check **Security & Governance**. If actions are failing with `PERMISSION_DENIED`, inspect **DENY Decisions** and **Human Confirmation Outcomes** to distinguish between security policy rejections and operational crashes.\n"
                        "3. **Inspect Distributed Spans in Grafana Tempo**:\n"
                        "   - Navigate to Grafana **Explore** (`/explore`) and select datasource **Tempo** (`uid: tempo`).\n"
                        "   - In the Search tab, filter by service `agent-harness` and tags: `agent.role = <role>` or `status = error`.\n"
                        "   - Inspect the complete hierarchical span tree:\n"
                        "     `agent.run [orchestrator]` $\\to$ `tool.execute [delegate_task]` $\\to$ `agent.run [specialist]` $\\to$ `tool.execute [tool_name]`.\n"
                        "   - Trace parentage maintains the single distributed `trace_id` across process boundaries with W3C `traceparent` context.\n"
                        "4. **Correlate with Structured Logs**: Search logs for matching `trace_id`, `run_id`, and `call_id` to inspect error categories without exposing sensitive user data."
                    ),
                },
            },
        ],
        "refresh": "5s",
        "schemaVersion": 39,
        "style": "dark",
        "tags": ["agent-harness", "operations", "production", "governance"],
        "templating": {
            "list": [
                {
                    "current": {"selected": True, "text": "All", "value": "$__all"},
                    "datasource": {"type": "prometheus", "uid": "prometheus"},
                    "definition": "label_values(harness_agent_runs_total, agent_role)",
                    "hide": 0,
                    "includeAll": True,
                    "label": "Agent Role",
                    "multi": True,
                    "name": "agent_role",
                    "options": [],
                    "query": {
                        "query": "label_values(harness_agent_runs_total, agent_role)",
                        "refId": "StandardVariableQuery",
                    },
                    "refresh": 1,
                    "type": "query",
                },
                {
                    "current": {"selected": True, "text": "All", "value": "$__all"},
                    "datasource": {"type": "prometheus", "uid": "prometheus"},
                    "definition": "label_values(harness_llm_calls_total, model)",
                    "hide": 0,
                    "includeAll": True,
                    "label": "Model",
                    "multi": True,
                    "name": "model",
                    "options": [],
                    "query": {
                        "query": "label_values(harness_llm_calls_total, model)",
                        "refId": "StandardVariableQuery",
                    },
                    "refresh": 1,
                    "type": "query",
                },
                {
                    "current": {"selected": True, "text": "All", "value": "$__all"},
                    "datasource": {"type": "prometheus", "uid": "prometheus"},
                    "definition": "label_values(harness_tool_calls_total, tool_source)",
                    "hide": 0,
                    "includeAll": True,
                    "label": "Tool Source",
                    "multi": True,
                    "name": "tool_source",
                    "options": [],
                    "query": {
                        "query": "label_values(harness_tool_calls_total, tool_source)",
                        "refId": "StandardVariableQuery",
                    },
                    "refresh": 1,
                    "type": "query",
                },
                {
                    "current": {"selected": True, "text": "All", "value": "$__all"},
                    "datasource": {"type": "prometheus", "uid": "prometheus"},
                    "definition": "label_values(harness_tool_calls_total, tool_name)",
                    "hide": 0,
                    "includeAll": True,
                    "label": "Tool",
                    "multi": True,
                    "name": "tool_name",
                    "options": [],
                    "query": {
                        "query": "label_values(harness_tool_calls_total, tool_name)",
                        "refId": "StandardVariableQuery",
                    },
                    "refresh": 1,
                    "type": "query",
                },
            ]
        },
        "time": {"from": "now-15m", "to": "now"},
        "timepicker": {
            "refresh_intervals": ["1s", "2s", "5s", "10s", "30s", "1m", "5m", "15m"]
        },
        "timezone": "browser",
        "title": "Agent Harness Operations",
        "uid": "agent-harness-runtime",
        "version": 2,
    }
    return dashboard


if __name__ == "__main__":
    out_path = Path("observability/grafana/dashboards/agent-harness-runtime.json")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(build_dashboard(), f, indent=2)
    print(f"Successfully generated {out_path}")
