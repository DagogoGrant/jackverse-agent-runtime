"""Unit tests for JackVerse Phase 5.3.3.1 Controlled Test Preflight."""

import json
import subprocess
import sys
from pathlib import Path

from scripts.controlled_test_preflight import run_controlled_preflight


def test_preflight_dry_run_validation_passes() -> None:
    """Verify that run_controlled_preflight executes all offline checks successfully."""
    res = run_controlled_preflight(
        sender_email="operator-sender@example.com",
        recipient_email="operator-controlled-test@example.com",
        operator_id="test_operator",
        db_path=":memory:",
        demonstrate_mocks=True,
    )

    assert res["dry_run_validation"] is True
    assert res["checks"]["safety_flags"]["no_send_boundary_intact"] is True
    assert res["checks"]["safety_flags"]["CASEWORKER_REAL_EXECUTION_ENABLED"] == "OFF"
    assert res["checks"]["safety_flags"]["CASEWORKER_GMAIL_EXECUTION_ENABLED"] == "OFF"

    # Cryptography
    assert res["checks"]["cryptography"]["roundtrip_verified"] is True
    assert res["checks"]["cryptography"]["min_entropy_enforced"] is True

    # Operator authority & dev token rejection
    assert res["checks"]["operator_authority"]["token_verified"] is True
    assert res["checks"]["operator_authority"]["dev_bypass_closed_in_production"] is True

    # Route inventory verification
    ri = res["checks"]["route_inventory"]
    assert ri["required_routes_verified"] is True
    assert ri["nonexistent_routes_rejected"] is True
    assert ri["google_oauth_authorize"] is True
    assert ri["google_oauth_callback"] is True
    assert ri["action_execute"] is True
    assert ri["case_scoped_action_execute"] is True
    assert ri["nonexistent_login_route_absent"] is True
    assert ri["nonexistent_case_action_execute_route_absent"] is True

    # Dry-run Barrier A & B rehearsal
    drv = res["checks"]["dry_run_validation"]
    assert drv["barrier_a_pre_staging_valid"] is True
    assert drv["barrier_b_read_only_rehearsal_valid"] is True
    assert drv["mime_hash_verified"] is True
    assert drv["action_type"] == "submit_form"
    assert drv["action_scope"] == "external_transmission"
    assert drv["authorization_schema"] == "tx_auth_v1"

    # Mock idempotency and ambiguity
    iv = res["checks"]["idempotency_verification"]
    assert iv["zero_duplicate_transmissions_guaranteed"] is True
    assert iv["provider_outbound_calls_first"] == 1
    assert iv["provider_outbound_calls_second"] == 1
    assert iv["automatic_retry_blocked_on_ambiguity"] is True
    assert iv["timeout_ambiguity_status"] == "ambiguous_submission"

    # Prerequisites status verification
    prereqs = {p["name"]: p["status"] for p in res["prerequisites"]}
    assert prereqs["NO-SEND Safety Boundaries"] == "PASS"
    assert prereqs["Token Cipher Cryptography (Fernet)"] == "PASS"
    assert prereqs["Operator Authentication Authority"] == "PASS"
    assert prereqs["API Route Inventory & OpenAPI Conformance"] == "PASS"
    assert prereqs["Barrier A Pre-Staging Revalidation Rehearsal"] == "PASS"
    assert prereqs["Barrier B Last-Reversible-Point Rehearsal"] == "PASS"
    assert prereqs["Transmission Idempotency (0 Duplicate Sends)"] == "PASS"
    assert prereqs["Timeout Ambiguity Protection"] == "PASS"
    assert prereqs["Local Proxy Anti-Spoofing"] == "PASS"
    assert prereqs["Production TLS/HTTPS Termination"] == "NOT CONFIGURED"
    assert prereqs["Public OAuth Callback Accessibility"] == "NOT CONFIGURED"
    assert prereqs["Real Google Cloud Project & OAuth Consent"] == "NOT CONFIGURED"
    assert prereqs["Operator Authenticated Google Session"] == "NOT CONFIGURED"
    assert prereqs["Genuine Destination Mailbox Ownership"] == "NOT CONFIGURED"
    assert prereqs["Real Gmail External Dispatch Permission"] == "NOT CONFIGURED"


def test_preflight_cli_runs_successfully() -> None:
    """Verify scripts/controlled_test_preflight.py executes cleanly as CLI with --json output."""
    project_root = Path(__file__).resolve().parents[3]
    script_path = project_root / "scripts" / "controlled_test_preflight.py"

    proc = subprocess.run(
        [sys.executable, str(script_path), "--json"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, f"CLI exited with error:\nSTDOUT:\n{proc.stdout}\nSTDERR:\n{proc.stderr}"

    data = json.loads(proc.stdout)
    assert data["dry_run_validation"] is True
    assert data["checks"]["safety_flags"]["no_send_boundary_intact"] is True
    assert data["checks"]["route_inventory"]["required_routes_verified"] is True
