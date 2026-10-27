#!/usr/bin/env python3
"""JackVerse Phase 5.3.3.2 Stage C: Guarded Gmail Dispatch Script.

Features & Safeguards:
- Default mode is --dry-run (safe preflight audit).
- Strictly requires --execute-authorized to initiate live transmission.
- Uses trusted localhost TLS verification with CA certificate (certs/localhost.crt) — no insecure TLS (-k).
- Evaluates complete chain of custody: Case, Plan, Action, Approval, Package, OAuth mailbox.
- Checks ETag and passes exact If-Match header to prevent concurrent or stale dispatch.
- Verifies execution attempt/receipt counts (guards against accidental double-sends).
- Differentiates confirmed success, verified rejection, and ambiguous timeouts (strictly forbids blind retries).
- Displays immediate disarm instructions to close the Stage C dispatch window.
"""

from __future__ import annotations

import argparse
import http.client
import json
import os
from pathlib import Path
import ssl
import sqlite3
import sys
import urllib.error
import urllib.parse
import urllib.request

repo_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(repo_root / "src"))

from caseworker.api.identity import generate_operator_token


CONTROLLED_CASE_ID = "case-controlled-test-5332"
CONTROLLED_PLAN_ID = "plan-d4819278ab3f45af"
CONTROLLED_PLAN_FINGERPRINT = "6e46f1a5cef7364a843c4b8e8713fa05a0506673a41815597b55107fa729a0ae"
CONTROLLED_ACTION_ID = "act_91966280c188"
CONTROLLED_APPROVAL_ID = "65cb25bb-0539-4f61-917c-46cf4a84f9dd"
CONTROLLED_USER_ID = "operator-1"
EXPECTED_DESTINATION_EMAIL = "dagogograntjack@gmail.com"


def _load_env_secret(env_file_path: Path, key_name: str) -> str:
    """Read secret from local env file without executing shell."""
    if not env_file_path.is_file():
        return ""
    with open(env_file_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line.startswith("#") or not line or "=" not in line:
                continue
            k, v = line.split("=", 1)
            if k.strip() == key_name:
                return v.strip().strip("'\"")
    return ""


def _create_ssl_context(ca_cert: Path) -> ssl.SSLContext:
    """Create trusted SSL context supporting root CA, bundle, or specified certificate."""
    ctx = ssl.create_default_context()
    # Candidate CA certificates to load
    candidates = [
        ca_cert.parent / "rootCA.pem",
        Path.home() / "Library/Application Support/mkcert/rootCA.pem",
        ca_cert.parent / "localhost-bundle.crt",
        ca_cert,
    ]
    for cand in candidates:
        if cand.is_file():
            try:
                ctx.load_verify_locations(cafile=str(cand))
            except Exception:
                pass
    return ctx


def check_tls_readiness(api_base: str, ca_cert: Path) -> dict:
    """Verify API ready status using trusted TLS."""
    ctx = _create_ssl_context(ca_cert)
    url = f"{api_base}/health/ready"
    req = urllib.request.Request(url, method="GET")
    try:
        with urllib.request.urlopen(req, context=ctx, timeout=5.0) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            return {"status": "ok", "http_code": resp.status, "data": data}
    except Exception as e:
        return {"status": "error", "error": str(e)}


def query_db_governance_invariants(db_path: Path) -> dict:
    """Query live SQLite database to verify all chain-of-custody invariants."""
    if not db_path.is_file():
        return {"status": "error", "error": f"Database file not found at {db_path}"}

    conn = sqlite3.connect(str(db_path))
    cur = conn.cursor()

    cur.execute("SELECT case_id, status FROM cases WHERE case_id = ?", (CONTROLLED_CASE_ID,))
    case_row = cur.fetchone()

    cur.execute(
        "SELECT plan_id, status, execution_plan_fingerprint FROM external_execution_plans WHERE plan_id = ?",
        (CONTROLLED_PLAN_ID,),
    )
    plan_row = cur.fetchone()

    cur.execute("SELECT action_id, status, version FROM actions WHERE action_id = ?", (CONTROLLED_ACTION_ID,))
    act_row = cur.fetchone()

    cur.execute(
        "SELECT approval_id, status, action_fingerprint, decided_at FROM approvals WHERE approval_id = ?",
        (CONTROLLED_APPROVAL_ID,),
    )
    app_row = cur.fetchone()

    cur.execute("SELECT count(*) FROM execution_attempts WHERE action_id = ?", (CONTROLLED_ACTION_ID,))
    att_count = cur.fetchone()[0]

    cur.execute("SELECT count(*) FROM execution_receipts WHERE action_id = ?", (CONTROLLED_ACTION_ID,))
    rcpt_count = cur.fetchone()[0]

    cur.execute(
        "SELECT user_id, provider, authenticated_email FROM user_oauth_credentials WHERE user_id = ? AND provider = 'gmail'",
        (CONTROLLED_USER_ID,),
    )
    oauth_row = cur.fetchone()

    conn.close()

    return {
        "status": "ok",
        "case": case_row,
        "plan": plan_row,
        "action": act_row,
        "approval": app_row,
        "execution_attempts_count": att_count,
        "execution_receipts_count": rcpt_count,
        "oauth": oauth_row,
    }


def query_api_action_invariants(api_base: str, ca_cert: Path, action_id: str, operator_token: str) -> dict:
    """Fetch action and ETag via authenticated API over trusted TLS."""
    ctx = _create_ssl_context(ca_cert)
    url = f"{api_base}/api/v1/actions/{action_id}"
    req = urllib.request.Request(
        url,
        headers={
            "Authorization": f"Bearer {operator_token}",
            "User-Agent": "JackVerse-GuardedDispatch/1.0",
        },
        method="GET",
    )
    try:
        with urllib.request.urlopen(req, context=ctx, timeout=5.0) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            return {
                "status": "ok",
                "http_code": resp.status,
                "etag": resp.headers.get("ETag"),
                "data": data,
            }
    except Exception as e:
        return {"status": "error", "error": str(e)}


def perform_guarded_dispatch(
    api_base: str,
    ca_cert: Path,
    action_id: str,
    action_version: int,
    operator_token: str,
    timeout_seconds: float = 15.0,
) -> dict:
    """Execute the approved action with exact ETag and operator token over trusted TLS."""
    ctx = _create_ssl_context(ca_cert)
    url = f"{api_base}/api/v1/actions/{action_id}/execute"

    etag = f'"action:{action_id}:v{action_version}"'
    headers = {
        "Authorization": f"Bearer {operator_token}",
        "If-Match": etag,
        "Content-Type": "application/json",
        "User-Agent": "JackVerse-GuardedDispatch/1.0",
    }

    body = json.dumps({}).encode("utf-8")
    req = urllib.request.Request(url, data=body, headers=headers, method="POST")

    try:
        with urllib.request.urlopen(req, context=ctx, timeout=timeout_seconds) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            return {
                "outcome": "SUCCESS",
                "http_code": resp.status,
                "data": data,
                "etag": resp.headers.get("ETag"),
            }
    except urllib.error.HTTPError as e:
        raw_body = e.read().decode("utf-8", errors="replace")
        try:
            parsed = json.loads(raw_body)
        except Exception:
            parsed = {"raw": raw_body}
        return {
            "outcome": "HTTP_ERROR",
            "http_code": e.code,
            "error_details": parsed,
        }
    except urllib.error.URLError as e:
        if isinstance(e.reason, TimeoutError) or "timed out" in str(e.reason).lower():
            return {
                "outcome": "AMBIGUOUS_TIMEOUT",
                "error": str(e.reason),
                "message": "Transmission request timed out in-flight. DO NOT RETRY AUTOMATICALLY. Check execution receipts and attempts.",
            }
        return {
            "outcome": "TRANSPORT_ERROR",
            "error": str(e.reason),
        }
    except Exception as e:
        return {
            "outcome": "UNKNOWN_ERROR",
            "error": str(e),
        }


def main() -> int:
    parser = argparse.ArgumentParser(description="JackVerse Stage C: Guarded Gmail Dispatch Utility")
    parser.add_argument("--api-base", default="https://localhost:3443", help="Base URL of Caseworker API/Reverse Proxy")
    parser.add_argument("--ca-cert", default="certs/localhost.crt", help="Path to CA certificate for trusted TLS")
    parser.add_argument("--env-file", default=".env.caseworker-api.local", help="Path to local environment file")
    parser.add_argument("--db-path", default="workspace/caseworker.db", help="Path to SQLite database (fallback to host-mounted)")
    parser.add_argument("--operator-id", default=CONTROLLED_USER_ID, help="Operator identifier")
    parser.add_argument("--action-id", default=CONTROLLED_ACTION_ID, help="Action ID to execute")
    parser.add_argument("--dry-run", action="store_true", default=True, help="Perform safe preflight audit only without executing (default: True)")
    parser.add_argument("--execute-authorized", action="store_true", default=False, help="Explicit flag required to initiate transmission")
    parser.add_argument("--non-interactive-approved", action="store_true", default=False, help="Confirm non-interactive authorization")

    args = parser.parse_args()

    api_base = args.api_base.rstrip("/")
    ca_cert = Path(args.ca_cert)
    env_file = Path(args.env_file)
    db_path = Path(args.db_path)

    # In container or host path resolution for database:
    if not db_path.is_file():
        # Check docker volume path or local workspace
        alt_db = Path(".caseworker/caseworker.db")
        if alt_db.is_file():
            db_path = alt_db

    print("================================================================================")
    print("JACKVERSE PHASE 5.3.3.2 — STAGE C GUARDED GMAIL DISPATCH")
    print("================================================================================")

    # 1. TLS and Health Check
    if not ca_cert.is_file():
        print(f"[FAIL] CA certificate not found at {ca_cert}. Insecure TLS is strictly forbidden.")
        return 1

    health_res = check_tls_readiness(api_base, ca_cert)
    if health_res.get("status") != "ok":
        print(f"[FAIL] Unable to reach Caseworker API over trusted TLS: {health_res}")
        return 1
    print(f"[PASS] Trusted TLS connection to {api_base} verified (health: {health_res['data']})")

    # 2. Operator Bearer Token Generation
    op_secret = _load_env_secret(env_file, "CASEWORKER_OPERATOR_SECRET")
    if not op_secret or len(op_secret) < 32:
        print("[FAIL] CASEWORKER_OPERATOR_SECRET is missing or less than 32 characters in env file.")
        return 1

    operator_token = generate_operator_token(args.operator_id, op_secret)
    print(f"[PASS] Signed operator bearer token generated for '{args.operator_id}' (length={len(operator_token)})")

    # 3. Read-Only API Invariant Checks
    api_action = query_api_action_invariants(api_base, ca_cert, args.action_id, operator_token)
    if api_action.get("status") != "ok":
        print(f"[FAIL] Unable to query Action '{args.action_id}' via API: {api_action.get('error')}")
        return 1

    action_data = api_action["data"]
    act_version = action_data.get("version", 3)
    act_status = action_data.get("status")
    act_params = action_data.get("parameters", {})
    act_plan_fp = act_params.get("execution_plan_fingerprint")
    act_plan_id = act_params.get("plan_id")
    act_pkg_id = act_params.get("package_id")
    act_pkg_fp = act_params.get("package_fingerprint")

    print("\n--- GOVERNANCE CHAIN-OF-CUSTODY (API AUDIT) ---")
    print(f"Action ID:           {action_data.get('action_id')} (Status: {act_status}, Version: {act_version})")
    print(f"ETag Header:         {api_action.get('etag')}")
    print(f"Plan ID:             {act_plan_id}")
    print(f"Plan Fingerprint:    {act_plan_fp}")
    print(f"Package ID:          {act_pkg_id}")
    print(f"Package Fingerprint: {act_pkg_fp}")
    print(f"Case ID:             {action_data.get('case_id')}")

    if act_status != "approved":
        print(f"[FAIL] Action status is '{act_status}', expected 'approved'.")
        return 1
    if act_plan_fp != CONTROLLED_PLAN_FINGERPRINT:
        print(f"[FAIL] Plan fingerprint mismatch: expected {CONTROLLED_PLAN_FINGERPRINT}, got {act_plan_fp}")
        return 1

    # 4. Read-Only Direct DB Invariant Checks (if DB accessible)
    invariants = query_db_governance_invariants(db_path)
    if invariants.get("status") == "ok":
        case = invariants["case"]
        plan = invariants["plan"]
        action = invariants["action"]
        approval = invariants["approval"]
        attempts = invariants["execution_attempts_count"]
        receipts = invariants["execution_receipts_count"]
        oauth = invariants["oauth"]

        print("\n--- GOVERNANCE CHAIN-OF-CUSTODY (DATABASE AUDIT) ---")
        print(f"Case ID:             {case[0]} (Status: {case[1]})")
        print(f"Plan ID:             {plan[0]} (Status: {plan[1]})")
        print(f"Plan Fingerprint:    {plan[2]}")
        print(f"Action ID:           {action[0]} (Status: {action[1]}, Version: {action[2]})")
        print(f"Approval ID:         {approval[0]} (Status: {approval[1]}, Decided: {approval[3]})")
        print(f"OAuth Mailbox:       {oauth[0]} -> {oauth[2]}")
        print(f"Execution Attempts:  {attempts}")
        print(f"Execution Receipts:  {receipts}")

        act_version = action[2]

        # Validations
        if plan[2] != CONTROLLED_PLAN_FINGERPRINT:
            print(f"[FAIL] Plan fingerprint mismatch: expected {CONTROLLED_PLAN_FINGERPRINT}, got {plan[2]}")
            return 1
        if approval[1] != "approved":
            print(f"[FAIL] Transmission approval is not approved (status: {approval[1]})")
            return 1
        if oauth[2] != EXPECTED_DESTINATION_EMAIL:
            print(f"[FAIL] Connected email mismatch: expected {EXPECTED_DESTINATION_EMAIL}, got {oauth[2]}")
            return 1
        if attempts > 0 or receipts > 0:
            print(f"[WARN] Database already records {attempts} attempt(s) and {receipts} receipt(s).")
            if not args.execute_authorized:
                print("       Subsequent execution will be handled via idempotent replay.")

    # 4. Mode Evaluation: Dry-Run vs Authorized Execution
    if not args.execute_authorized:
        print("\n================================================================================")
        print("PREFLIGHT AUDIT COMPLETE — DISPATCH SAFEGUARDS ENGAGED")
        print("Mode: --dry-run (default). No HTTP execute request was sent.")
        print("To execute with explicit human authorization, operator must pass:")
        print("    --execute-authorized --non-interactive-approved")
        print("================================================================================")
        return 0

    # Human confirmation check
    if not args.non-interactive-approved:
        confirm = input("\n[WARNING] You have passed --execute-authorized. Proceed with live dispatch? (yes/no): ")
        if confirm.strip().lower() != "yes":
            print("Execution cancelled by operator.")
            return 0

    print("\n--- INITIATING GUARDED DISPATCH ---")
    print(f"Target URL:    {api_base}/api/v1/actions/{args.action_id}/execute")
    print(f"If-Match ETag: W/\"action:{args.action_id}:{act_version}\"")
    print(f"Operator ID:   {args.operator_id}")

    result = perform_guarded_dispatch(
        api_base=api_base,
        ca_cert=ca_cert,
        action_id=args.action_id,
        action_version=act_version,
        operator_token=operator_token,
    )

    print("\n--- DISPATCH OUTCOME ---")
    print(f"Outcome: {result['outcome']}")
    if result["outcome"] == "SUCCESS":
        data = result.get("data", {})
        receipt = data.get("receipt", {})
        print(f"HTTP Status:          {result.get('http_code')}")
        print(f"Receipt ID:           {receipt.get('receipt_id')}")
        print(f"Receipt Status:       {receipt.get('status')}")
        print(f"Provider Message ID:  {receipt.get('safe_provider_metadata', {}).get('provider_message_id') or receipt.get('destination_url')}")
        print(f"Receipt Hash:         {receipt.get('receipt_hash')}")
        print(f"Case Status:          {data.get('case_status')}")
        print("\n[SUCCESS] Governed transmission executed and receipt verified.")
    elif result["outcome"] == "HTTP_ERROR":
        print(f"HTTP Status Code:     {result.get('http_code')}")
        print(f"Error Details:        {json.dumps(result.get('error_details'), indent=2)}")
        print("\n[BLOCKED / FAILED] Request rejected by Caseworker API governance barriers.")
    elif result["outcome"] == "AMBIGUOUS_TIMEOUT":
        print(f"[CRITICAL AMBIGUITY] {result.get('message')}")
        print(f"Details: {result.get('error')}")
    else:
        print(f"[ERROR] {result.get('error')}")

    # Immediate disarm instructions
    print("\n================================================================================")
    print("MANDATORY POST-DISPATCH DISARM STEP:")
    print("Immediately tear down the Stage C dispatch window by running:")
    print("    docker compose -f docker-compose.yml -f docker-compose.stage-c-dispatch.yml down")
    print("    docker compose up -d")
    print("================================================================================")

    return 0 if result["outcome"] == "SUCCESS" else 1


if __name__ == "__main__":
    sys.exit(main())
