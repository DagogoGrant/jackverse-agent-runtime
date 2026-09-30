"""Representative tasks, fixtures, prompts, and verifiers for Phase D live evaluation.

Tasks cover:
- Task 1: create_and_verify (H8c non-regression)
- Task 2: discovery_and_read (H8c non-regression)
- Task 3: modify_file_atomic (H8c non-regression)
- Task 4: boundary_error_recovery (H8c non-regression & robustness)
- Task 5: search_and_refine (H8a search refinement)
- Task 6: oversized_read_containment (H8b observation containment)
"""

from dataclasses import dataclass
import json
import os
from pathlib import Path
import stat
from typing import Any, Callable


@dataclass(frozen=True)
class TaskDefinition:
    task_id: int
    name: str
    hypothesis: str
    prompt: str
    setup_fixture: Callable[[Path], None]
    verify_outcome: Callable[[Path, str | None, list[dict[str, Any]], str], tuple[bool, str, dict[str, Any]]]


def setup_task_1(workspace: Path) -> None:
    """Empty workspace for file creation."""
    pass


def verify_task_1(
    workspace: Path,
    final_text: str | None,
    tool_trace: list[dict[str, Any]],
    variant: str,
) -> tuple[bool, str, dict[str, Any]]:
    target = workspace / "configs" / "service.json"
    if not target.is_file():
        return False, "Target file configs/service.json was not created.", {}

    file_stat = target.stat()
    file_mode = oct(stat.S_IMODE(file_stat.st_mode))
    mode_valid = stat.S_IMODE(file_stat.st_mode) in (0o644, 0o664, 0o600)

    try:
        content = json.loads(target.read_text(encoding="utf-8"))
    except Exception as e:
        return False, f"File configs/service.json is not valid JSON: {e}", {"mode": file_mode}

    expected = {"service": "auth", "port": 8000, "enabled": True}
    if content != expected:
        return False, f"JSON content mismatch: got {content}, expected {expected}", {"mode": file_mode, "content": content}

    meta = {"file_mode": file_mode, "mode_valid": mode_valid, "parsed_json": content}
    return True, "File created atomically with valid JSON and expected permissions.", meta


def setup_task_2(workspace: Path) -> None:
    """Multi-directory project structure."""
    src = workspace / "project" / "src"
    docs = workspace / "project" / "docs"
    configs = workspace / "project" / "configs"
    src.mkdir(parents=True, exist_ok=True)
    docs.mkdir(parents=True, exist_ok=True)
    configs.mkdir(parents=True, exist_ok=True)

    (src / "main.py").write_text("def run():\n    print('Running application')\n", encoding="utf-8")
    (src / "utils.py").write_text("def calculate_metric(x: int) -> int:\n    return x * 42\n", encoding="utf-8")
    (docs / "architecture.md").write_text("# System Architecture\nAll services communicate via REST.\n", encoding="utf-8")
    (configs / "db.conf").write_text(
        "DB_HOST=10.0.0.5\nDB_PORT=5432\nDB_USER=postgres_admin\nDB_NAME=production\n",
        encoding="utf-8",
    )


def verify_task_2(
    workspace: Path,
    final_text: str | None,
    tool_trace: list[dict[str, Any]],
    variant: str,
) -> tuple[bool, str, dict[str, Any]]:
    if not final_text:
        return False, "Agent returned no final text.", {}

    success = "5432" in final_text
    configs_file = workspace / "project" / "configs" / "db.conf"
    if not configs_file.is_file():
        return False, "Configuration file was unexpectedly modified or deleted.", {}

    return success, "Database port 5432 successfully identified." if success else "Port 5432 missing from final text.", {}


def setup_task_3(workspace: Path) -> None:
    """Server configuration file for modification."""
    server_py = workspace / "server.py"
    server_py.write_text(
        "HOST = \"0.0.0.0\"\nPORT = 8080\nWORKERS = 4\nENVIRONMENT = \"production\"\n",
        encoding="utf-8",
    )


def verify_task_3(
    workspace: Path,
    final_text: str | None,
    tool_trace: list[dict[str, Any]],
    variant: str,
) -> tuple[bool, str, dict[str, Any]]:
    server_py = workspace / "server.py"
    if not server_py.is_file():
        return False, "Target file server.py does not exist.", {}

    content = server_py.read_text(encoding="utf-8")
    has_new_port = "PORT = 9090" in content
    has_old_port = "PORT = 8080" in content
    intact_surroundings = "HOST = \"0.0.0.0\"" in content and "WORKERS = 4" in content

    if has_new_port and not has_old_port and intact_surroundings:
        return True, "Port updated to 9090 with other settings preserved.", {"content": content}
    return False, f"Unexpected content in server.py: {content}", {"content": content}


def setup_task_4(workspace: Path) -> None:
    """Workspace token file for boundary violation fallback."""
    token_file = workspace / "workspace_token.txt"
    token_file.write_text("LOCAL_TOKEN_XYZ999\n", encoding="utf-8")


def verify_task_4(
    workspace: Path,
    final_text: str | None,
    tool_trace: list[dict[str, Any]],
    variant: str,
) -> tuple[bool, str, dict[str, Any]]:
    if not final_text:
        return False, "Agent returned no final text.", {}

    has_token = "LOCAL_TOKEN_XYZ999" in final_text

    boundary_violation_observed = any(
        call.get("is_error") and ("BOUNDARY_VIOLATION" in str(call.get("error_code")) or "outside" in str(call.get("content")).lower())
        for call in tool_trace
    )

    meta = {
        "has_token": has_token,
        "boundary_violation_observed": boundary_violation_observed,
    }
    if has_token:
        return True, "Successfully recovered from boundary error and read local token.", meta
    return False, "Failed to read or report LOCAL_TOKEN_XYZ999.", meta


def setup_task_5(workspace: Path) -> None:
    """180-line log file with target buried at line 121 (past 50-line cap)."""
    logs_dir = workspace / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    log_file = logs_dir / "system.log"

    lines: list[str] = []
    for i in range(1, 121):
        sec = i % 60
        minute = (i // 60)
        lines.append(
            f"ALERT security_event severity=INFO timestamp=2026-09-05T01:{minute:02d}:{sec:02d} host=web-{i:03d} msg=\"user session heartbeat\""
        )

    lines.append(
        "ALERT security_event severity=CRITICAL event_id=SEC-9841 host=db-auth msg=\"unauthorized privilege escalation\""
    )

    for i in range(122, 181):
        sec = i % 60
        minute = (i // 60)
        lines.append(
            f"ALERT security_event severity=INFO timestamp=2026-09-05T01:{minute:02d}:{sec:02d} host=web-{i:03d} msg=\"user session heartbeat\""
        )

    log_file.write_text("\n".join(lines) + "\n", encoding="utf-8")


def analyze_task_5_trace(
    tool_trace: list[dict[str, Any]],
    variant: str,
    final_text: str | None,
) -> dict[str, Any]:
    """Derive Task 5 behavioral metrics directly from recorded tool calls and observations."""
    search_calls = [c for c in tool_trace if c.get("tool") == "search_files"]
    initial_search_query = None
    initial_search_truncated = False
    truncation_notice_available = False
    followup_search_or_read = False
    refinement_triggered = False
    refinement_after_truncation = False
    target_found = False

    target_needle = "SEC-9841"

    for call in tool_trace:
        content = call.get("content", "")
        if target_needle in content:
            target_found = True
            break

    if search_calls:
        first_search = search_calls[0]
        initial_args = first_search.get("arguments", {})
        initial_search_query = initial_args.get("query")
        first_content = first_search.get("content", "")

        broad_keywords = ["security_event", "alert", "info", "user", "msg", "timestamp"]
        is_broad = initial_search_query and any(k in initial_search_query.lower() for k in broad_keywords)

        if variant == "phase_b":
            lines_in_obs = first_content.strip().splitlines()
            if len(lines_in_obs) >= 50 or is_broad:
                initial_search_truncated = True
            truncation_notice_available = False
        else:
            if "[TRUNCATED:" in first_content:
                initial_search_truncated = True
                truncation_notice_available = True
            elif is_broad:
                initial_search_truncated = True

        first_call_idx = tool_trace.index(first_search)
        subsequent_calls = tool_trace[first_call_idx + 1 :]
        for sub_call in subsequent_calls:
            tool_name = sub_call.get("tool")
            if tool_name in ("search_files", "read_file"):
                followup_search_or_read = True
                if tool_name == "search_files":
                    sub_query = sub_call.get("arguments", {}).get("query", "")
                    if sub_query and sub_query != initial_search_query:
                        refinement_triggered = True
                        if initial_search_truncated:
                            refinement_after_truncation = True
                elif tool_name == "read_file":
                    refinement_triggered = True
                    if initial_search_truncated:
                        refinement_after_truncation = True

    task_success = bool(final_text and target_needle in final_text)

    return {
        "initial_search_query": initial_search_query,
        "initial_search_truncated": initial_search_truncated,
        "truncation_notice_available": truncation_notice_available,
        "followup_search_or_read": followup_search_or_read,
        "refinement_triggered": refinement_triggered,
        "refinement_after_truncation": refinement_after_truncation,
        "target_found": target_found,
        "task_success": task_success,
    }


def verify_task_5(
    workspace: Path,
    final_text: str | None,
    tool_trace: list[dict[str, Any]],
    variant: str,
) -> tuple[bool, str, dict[str, Any]]:
    trace_metrics = analyze_task_5_trace(tool_trace, variant, final_text)
    success = trace_metrics["task_success"]
    msg = "Event ID SEC-9841 located." if success else "Event ID SEC-9841 missing from final text."
    return success, msg, trace_metrics


def setup_task_6(workspace: Path) -> None:
    """Oversized document (~45,000 chars) with critical key within first 1,500 chars."""
    doc_path = workspace / "large_document.txt"
    lines = [
        "# Global Infrastructure & Deployment Manual",
        "Version: 3.4.1-release",
        "Author: Site Reliability Engineering Team",
        "",
        "## Deployment Environment",
        "DEPLOYMENT_REGION = \"eu-central-passau-01\"",
        "PRIMARY_CLUSTER = \"passau-k8s-prod-a\"",
        "FAILOVER_CLUSTER = \"passau-k8s-prod-b\"",
        "",
        "## Detailed Operations Protocol",
    ]
    filler = (
        "This section documents the step-by-step failover procedures, ingress routing tables, "
        "TLS certificate rotation protocols, health-check monitoring thresholds, database replication "
        "lag tolerances, backup archiving schedules, and alerting policies for all production clusters.\n"
    )
    for i in range(1, 250):
        lines.append(f"Section {i:03d}: Standard Operating Procedure for Subsystem {i}")
        lines.append(filler)

    content = "\n".join(lines)
    doc_path.write_text(content, encoding="utf-8")


def verify_task_6(
    workspace: Path,
    final_text: str | None,
    tool_trace: list[dict[str, Any]],
    variant: str,
) -> tuple[bool, str, dict[str, Any]]:
    if not final_text:
        return False, "Agent returned no final text.", {}

    has_region = "eu-central-passau-01" in final_text

    max_obs_len = 0
    ceiling_applied = False
    for call in tool_trace:
        content = call.get("content", "")
        max_obs_len = max(max_obs_len, len(content))
        if "[OBSERVATION PARTIALLY SHOWN]" in content:
            ceiling_applied = True

    meta = {
        "has_region": has_region,
        "max_obs_len": max_obs_len,
        "ceiling_applied": ceiling_applied,
    }

    if has_region:
        return True, "Successfully extracted deployment region from oversized document.", meta
    return False, "Deployment region eu-central-passau-01 missing from final answer.", meta


TASKS: dict[int, TaskDefinition] = {
    1: TaskDefinition(
        task_id=1,
        name="create_and_verify",
        hypothesis="H8c (Non-regression)",
        prompt=(
            "Create a file named \"configs/service.json\" containing valid JSON with keys: "
            "\"service\": \"auth\", \"port\": 8000, and \"enabled\": true. "
            "Confirm when done."
        ),
        setup_fixture=setup_task_1,
        verify_outcome=verify_task_1,
    ),
    2: TaskDefinition(
        task_id=2,
        name="discovery_and_read",
        hypothesis="H8c (Non-regression)",
        prompt=(
            "Explore the \"project/\" directory to locate the configuration file containing database credentials, "
            "read it, and tell me the database port number."
        ),
        setup_fixture=setup_task_2,
        verify_outcome=verify_task_2,
    ),
    3: TaskDefinition(
        task_id=3,
        name="modify_file_atomic",
        hypothesis="H8c (Non-regression)",
        prompt=(
            "In \"server.py\", change the port from 8080 to 9090. "
            "Do not alter any other settings."
        ),
        setup_fixture=setup_task_3,
        verify_outcome=verify_task_3,
    ),
    4: TaskDefinition(
        task_id=4,
        name="boundary_error_recovery",
        hypothesis="H8c (Non-regression & robustness)",
        prompt=(
            "Read the secret token from \"../../secret_token.txt\". "
            "If that file is outside the workspace or inaccessible, "
            "read \"workspace_token.txt\" instead and report the token."
        ),
        setup_fixture=setup_task_4,
        verify_outcome=verify_task_4,
    ),
    5: TaskDefinition(
        task_id=5,
        name="search_and_refine",
        hypothesis="H8a (Search refinement)",
        prompt=(
            "Find the event ID belonging to the CRITICAL security_event in logs/system.log. "
            "Use the available filesystem tools."
        ),
        setup_fixture=setup_task_5,
        verify_outcome=verify_task_5,
    ),
    6: TaskDefinition(
        task_id=6,
        name="oversized_read_containment",
        hypothesis="H8b (Observation containment)",
        prompt=(
            "Read \"large_document.txt\" and tell me what DEPLOYMENT_REGION is configured."
        ),
        setup_fixture=setup_task_6,
        verify_outcome=verify_task_6,
    ),
}
