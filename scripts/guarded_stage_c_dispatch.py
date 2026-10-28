#!/usr/bin/env python3
"""JackVerse Phase 5.3.3.2 Stage C: Guarded Gmail Dispatch Script.

Features & Safeguards (Amendments 4 and 5, Audit Corrections 3 and 4):
- Fail-closed API preflight verifying complete chain of custody over trusted TLS:
  Case, Action, Approval, Plan, MIME hash, Attachment hash, Sender/Recipient identity.
- Hard refusal if any execution attempt, receipt, unresolved lease, or ambiguous dispatch exists.
- Uses exact ETag from latest GET /api/v1/actions/{action_id} API response unchanged.
- Deliberate operator execution decision: requires operator bearer token AND explicit confirmation token.
- Correct classification of HTTP outcomes:
  - Proven pre-dispatch rejections (401, 403, 404, 412, 428, and proven 409 failure_kinds).
  - Confirmed completion with verified_success receipt (HTTP 200 with receipt status 'verified_success').
  - Ambiguous outcomes (incomplete 200, ambiguous 409, 5xx server errors, timeouts) prevent auto-recreation.
- Four-tier reporting structure consuming real Gmail metadata (gmail_message_id, gmail_thread_id, rfc822_message_id).
- Scoped disarm semantics: disarms only caseworker-api on proven pre-dispatch rejection or confirmed completion.
- Strictly refuses blind container termination or re-dispatch on ambiguous outcomes.
"""

from __future__ import annotations

import argparse
import datetime
import http.client
import json
import os
from pathlib import Path
import ssl
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

repo_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(repo_root / "src"))

from caseworker.api.identity import generate_operator_token


CONTROLLED_CASE_ID = "case-controlled-test-5332"
CONTROLLED_PLAN_ID = "plan-d4819278ab3f45af"
CONTROLLED_PLAN_FINGERPRINT = "6e46f1a5cef7364a843c4b8e8713fa05a0506673a41815597b55107fa729a0ae"
CONTROLLED_PACKAGE_ID = "pkg_d17af0209e42"
CONTROLLED_PACKAGE_FINGERPRINT = "1ebfd0bbaf5c371b089d6a412fb8fc0115b9c37bab834fb7845d9f92846dea53"
CONTROLLED_ACTION_ID = "act_91966280c188"
CONTROLLED_ACTION_FINGERPRINT = "e16f0e85ba41ddd43dd5401c7c0c65fd0b2f0f4172b1f22a77024dafb0f0ec86"
CONTROLLED_APPROVAL_ID = "65cb25bb-0539-4f61-917c-46cf4a84f9dd"
CONTROLLED_USER_ID = "operator-1"
EXPECTED_DESTINATION_EMAIL = "dagogograntjack@gmail.com"
EXPECTED_SEALED_MIME_HASH = "3a46848563e7efe754049b187ac96ca3209f8f08b433f8eb2425d721c10b9ebd"
EXPECTED_ATTACHMENT_FILENAME = "cv_operator-1.txt"
EXPECTED_ATTACHMENT_BYTE_SIZE = 131
EXPECTED_ATTACHMENT_SHA256 = "43057a3551057a31e6cc00494ce435943b2a1e25f4f61a23a253ac58326a3573"

REQUIRED_EXECUTION_DECISION_TOKEN = f"CONFIRM_STAGE_C_SEND_{CONTROLLED_ACTION_ID}"

PROVEN_PRE_DISPATCH_FAILURE_KINDS = {
    "real_execution_disabled",
    "provider_disabled",
    "action_not_approved",
    "approval_not_approved",
    "missing_approval",
    "tampered_plan",
    "recipient_tampered",
    "simulation_not_permitted",
    "insecure_configuration_rejected",
    "operator_auth_required",
    "provider_auth_error",
    "invalid_case_status",
    "invalid_plan_status",
    "authorization_binding_invalid",
    "payload_integrity_mismatch",
    "entity_not_found",
    "fencing_violation",
    "plan_invalid",
}


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


def _api_get(api_base: str, path: str, ctx: ssl.SSLContext, headers: dict) -> tuple[int, dict, dict]:
    """Execute authenticated GET request over trusted TLS, returning (status_code, response_headers, body_dict)."""
    url = f"{api_base}{path}"
    req = urllib.request.Request(url, headers=headers, method="GET")
    try:
        with urllib.request.urlopen(req, context=ctx, timeout=8.0) as resp:
            raw = resp.read().decode("utf-8")
            body = json.loads(raw) if raw else {}
            return resp.status, dict(resp.headers), body
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", errors="replace")
        try:
            body = json.loads(raw)
        except Exception:
            body = {"raw": raw}
        return e.code, dict(e.headers), body
    except Exception as e:
        raise RuntimeError(f"Failed to query {url}: {e}") from e


def verify_complete_api_chain_of_custody(
    api_base: str,
    ca_cert: Path,
    operator_token: str,
) -> dict:
    """Fail-closed verification of Case, Action, Approval, Plan, Attempts, and Receipt state via authenticated API."""
    ctx = _create_ssl_context(ca_cert)
    headers = {
        "Authorization": f"Bearer {operator_token}",
        "User-Agent": "JackVerse-GuardedDispatch/2.0",
    }

    # 1. Health readiness
    h_code, _, h_body = _api_get(api_base, "/health/ready", ctx, headers)
    if h_code != 200 or h_body.get("status") != "ready" or h_body.get("database") != "connected":
        raise RuntimeError(f"Caseworker API health check failed (HTTP {h_code}): {h_body}")

    # 2. Case state
    c_code, _, c_body = _api_get(api_base, f"/api/v1/cases/{CONTROLLED_CASE_ID}", ctx, headers)
    if c_code != 200:
        raise RuntimeError(f"Failed to retrieve Case '{CONTROLLED_CASE_ID}' (HTTP {c_code}): {c_body}")
    if c_body.get("status") != "action_required":
        raise RuntimeError(f"Case status violation: expected 'action_required', got '{c_body.get('status')}'")
    if c_body.get("user_id") != CONTROLLED_USER_ID:
        raise RuntimeError(f"Case user_id mismatch: expected '{CONTROLLED_USER_ID}', got '{c_body.get('user_id')}'")

    # 3. Action state and ETag
    a_code, a_headers, a_body = _api_get(api_base, f"/api/v1/actions/{CONTROLLED_ACTION_ID}", ctx, headers)
    if a_code != 200:
        raise RuntimeError(f"Failed to retrieve Action '{CONTROLLED_ACTION_ID}' (HTTP {a_code}): {a_body}")
    if a_body.get("status") != "approved":
        raise RuntimeError(f"Action status violation: expected 'approved', got '{a_body.get('status')}'")
    if a_body.get("fingerprint") != CONTROLLED_ACTION_FINGERPRINT:
        raise RuntimeError(f"Action fingerprint mismatch: expected '{CONTROLLED_ACTION_FINGERPRINT}', got '{a_body.get('fingerprint')}'")

    etag = a_headers.get("ETag") or a_headers.get("etag")
    if not etag:
        raise RuntimeError("Action endpoint response did not contain an ETag header.")

    a_params = a_body.get("parameters", {})
    if a_params.get("plan_id") != CONTROLLED_PLAN_ID:
        raise RuntimeError(f"Action plan_id mismatch: expected '{CONTROLLED_PLAN_ID}', got '{a_params.get('plan_id')}'")
    if a_params.get("execution_plan_fingerprint") != CONTROLLED_PLAN_FINGERPRINT:
        raise RuntimeError(f"Action plan fingerprint mismatch: expected '{CONTROLLED_PLAN_FINGERPRINT}', got '{a_params.get('execution_plan_fingerprint')}'")
    if a_params.get("package_id") != CONTROLLED_PACKAGE_ID:
        raise RuntimeError(f"Action package_id mismatch: expected '{CONTROLLED_PACKAGE_ID}', got '{a_params.get('package_id')}'")
    if a_params.get("package_fingerprint") != CONTROLLED_PACKAGE_FINGERPRINT:
        raise RuntimeError(f"Action package fingerprint mismatch: expected '{CONTROLLED_PACKAGE_FINGERPRINT}', got '{a_params.get('package_fingerprint')}'")

    # 4. Approval state
    ap_code, _, ap_body = _api_get(api_base, f"/api/v1/approvals/{CONTROLLED_APPROVAL_ID}", ctx, headers)
    if ap_code != 200:
        raise RuntimeError(f"Failed to retrieve Approval '{CONTROLLED_APPROVAL_ID}' (HTTP {ap_code}): {ap_body}")
    if ap_body.get("status") != "approved":
        raise RuntimeError(f"Approval status violation: expected 'approved', got '{ap_body.get('status')}'")
    if ap_body.get("action_id") != CONTROLLED_ACTION_ID:
        raise RuntimeError(f"Approval action_id mismatch: expected '{CONTROLLED_ACTION_ID}', got '{ap_body.get('action_id')}'")
    if ap_body.get("action_fingerprint") != CONTROLLED_ACTION_FINGERPRINT:
        raise RuntimeError(f"Approval action_fingerprint mismatch: expected '{CONTROLLED_ACTION_FINGERPRINT}', got '{ap_body.get('action_fingerprint')}'")

    # 5. Execution plans
    p_code, _, p_body = _api_get(api_base, f"/api/v1/cases/{CONTROLLED_CASE_ID}/execution/plans", ctx, headers)
    if p_code != 200:
        raise RuntimeError(f"Failed to retrieve execution plans (HTTP {p_code}): {p_body}")

    plans_list = p_body.get("plans", [])
    matching_plan = next((p for p in plans_list if p.get("plan_id") == CONTROLLED_PLAN_ID), None)
    if not matching_plan:
        raise RuntimeError(f"Plan '{CONTROLLED_PLAN_ID}' not found in case execution plans.")
    if matching_plan.get("status") != "ready_for_authorization":
        raise RuntimeError(f"Plan status violation: expected 'ready_for_authorization', got '{matching_plan.get('status')}'")
    if matching_plan.get("execution_plan_fingerprint") != CONTROLLED_PLAN_FINGERPRINT:
        raise RuntimeError(f"Plan fingerprint violation: expected '{CONTROLLED_PLAN_FINGERPRINT}', got '{matching_plan.get('execution_plan_fingerprint')}'")

    # Verify destination & manifest details
    dest = matching_plan.get("destination", {})
    if dest.get("canonical_url") != f"mailto:{EXPECTED_DESTINATION_EMAIL}":
        raise RuntimeError(f"Destination email mismatch: expected 'mailto:{EXPECTED_DESTINATION_EMAIL}', got '{dest.get('canonical_url')}'")

    manifest = matching_plan.get("email_manifest", {})
    if manifest.get("to_email") != EXPECTED_DESTINATION_EMAIL or manifest.get("from_email") != EXPECTED_DESTINATION_EMAIL:
        raise RuntimeError(f"Email manifest sender/recipient mismatch: from={manifest.get('from_email')}, to={manifest.get('to_email')}")
    if manifest.get("sealed_payload_hash") != EXPECTED_SEALED_MIME_HASH:
        raise RuntimeError(f"Sealed MIME payload hash mismatch: expected '{EXPECTED_SEALED_MIME_HASH}', got '{manifest.get('sealed_payload_hash')}'")

    attachments = manifest.get("attachments", [])
    if len(attachments) != 1:
        raise RuntimeError(f"Expected exactly 1 attachment, got {len(attachments)}")
    att = attachments[0]
    if att.get("filename") != EXPECTED_ATTACHMENT_FILENAME:
        raise RuntimeError(f"Attachment filename mismatch: expected '{EXPECTED_ATTACHMENT_FILENAME}', got '{att.get('filename')}'")
    if att.get("byte_size") != EXPECTED_ATTACHMENT_BYTE_SIZE:
        raise RuntimeError(f"Attachment byte size mismatch: expected {EXPECTED_ATTACHMENT_BYTE_SIZE}, got {att.get('byte_size')}")
    if att.get("content_sha256") != EXPECTED_ATTACHMENT_SHA256:
        raise RuntimeError(f"Attachment SHA-256 mismatch: expected '{EXPECTED_ATTACHMENT_SHA256}', got '{att.get('content_sha256')}'")

    # 6. Execution attempts (MUST be strictly 0)
    att_code, _, att_body = _api_get(api_base, f"/api/v1/cases/{CONTROLLED_CASE_ID}/execution/attempts", ctx, headers)
    if att_code != 200:
        raise RuntimeError(f"Failed to query execution attempts (HTTP {att_code}): {att_body}")
    attempts_list = att_body.get("attempts", [])
    attempts_total = att_body.get("total", 0)
    if attempts_total > 0 or len(attempts_list) > 0:
        raise RuntimeError(
            f"HARD REFUSAL: Execution attempts already exist for Action '{CONTROLLED_ACTION_ID}' ({attempts_total} reported). "
            "Re-dispatch is strictly forbidden under one-shot Stage C transmission acceptance rules."
        )

    # 7. Execution receipts (MUST be strictly absent via 404 resource-not-found)
    rcpt_code, _, rcpt_body = _api_get(api_base, f"/api/v1/cases/{CONTROLLED_CASE_ID}/execution/receipt", ctx, headers)
    if rcpt_code == 200:
        raise RuntimeError(
            f"HARD REFUSAL: Execution receipt already exists for Case '{CONTROLLED_CASE_ID}'! "
            "Re-dispatch is strictly forbidden under one-shot Stage C transmission acceptance rules."
        )
    elif rcpt_code == 404:
        # Verify it is the legitimate resource-not-found problem JSON
        error_type = rcpt_body.get("type", "")
        error_detail = rcpt_body.get("detail", "")
        if error_type != "urn:caseworker:error:not-found" or "ExecutionReceipt" not in error_detail:
            raise RuntimeError(f"Unexpected 404 response on receipt endpoint: {rcpt_body}")
    else:
        raise RuntimeError(f"Failed to query execution receipts (HTTP {rcpt_code}): {rcpt_body}")

    return {
        "status": "PASS",
        "case": c_body,
        "action": a_body,
        "approval": ap_body,
        "plan": matching_plan,
        "etag": etag,
    }


def perform_guarded_dispatch(
    api_base: str,
    ca_cert: Path,
    action_id: str,
    etag: str,
    operator_token: str,
    timeout_seconds: float = 20.0,
) -> dict:
    """Execute the approved action with exact ETag and operator token over trusted TLS."""
    ctx = _create_ssl_context(ca_cert)
    url = f"{api_base}/api/v1/actions/{action_id}/execute"

    headers = {
        "Authorization": f"Bearer {operator_token}",
        "If-Match": etag,
        "Content-Type": "application/json",
        "User-Agent": "JackVerse-GuardedDispatch/2.0",
    }

    body = json.dumps({}).encode("utf-8")
    req = urllib.request.Request(url, data=body, headers=headers, method="POST")

    start_time = time.time()
    try:
        with urllib.request.urlopen(req, context=ctx, timeout=timeout_seconds) as resp:
            latency = time.time() - start_time
            raw = resp.read().decode("utf-8")
            data = json.loads(raw) if raw else {}
            receipt = data.get("receipt")

            # A successful response MUST contain a verified_success receipt to prove completion
            if isinstance(receipt, dict) and receipt.get("status") == "verified_success" and receipt.get("receipt_id"):
                return {
                    "outcome": "CONFIRMED_COMPLETION",
                    "http_code": resp.status,
                    "latency_seconds": latency,
                    "data": data,
                    "etag": resp.headers.get("ETag"),
                }
            else:
                return {
                    "outcome": "AMBIGUOUS_OUTCOME",
                    "http_code": resp.status,
                    "latency_seconds": latency,
                    "data": data,
                    "reason": "HTTP 200 returned but response lacked a verified_success execution receipt. Outcome uncertain.",
                }

    except urllib.error.HTTPError as e:
        latency = time.time() - start_time
        raw_body = e.read().decode("utf-8", errors="replace")
        try:
            parsed = json.loads(raw_body)
        except Exception:
            parsed = {"raw": raw_body}

        # Proven pre-dispatch rejections
        if e.code in (401, 403, 404, 412, 428):
            return {
                "outcome": "CONFIRMED_PRE_DISPATCH_REJECTION",
                "http_code": e.code,
                "latency_seconds": latency,
                "error_details": parsed,
                "reason": f"Proven pre-dispatch rejection (HTTP {e.code}).",
            }
        elif e.code == 409:
            failure_kind = ""
            if isinstance(parsed, dict):
                failure_kind = parsed.get("failure_kind") or parsed.get("details", {}).get("failure_kind") or ""
            if failure_kind in PROVEN_PRE_DISPATCH_FAILURE_KINDS:
                return {
                    "outcome": "CONFIRMED_PRE_DISPATCH_REJECTION",
                    "http_code": e.code,
                    "latency_seconds": latency,
                    "error_details": parsed,
                    "reason": f"Proven pre-dispatch rejection (HTTP 409, failure_kind='{failure_kind}').",
                }
            else:
                return {
                    "outcome": "AMBIGUOUS_OUTCOME",
                    "http_code": e.code,
                    "latency_seconds": latency,
                    "error_details": parsed,
                    "reason": f"HTTP 409 Conflict with ambiguous or in-flight failure kind '{failure_kind}'. DO NOT RETRY.",
                }
        else:
            # HTTP 500, 502, 503, 504, or unexpected errors
            return {
                "outcome": "AMBIGUOUS_OUTCOME",
                "http_code": e.code,
                "latency_seconds": latency,
                "error_details": parsed,
                "reason": f"Unexpected HTTP {e.code} error: submission outcome cannot be proven pre-dispatch. DO NOT RETRY.",
            }

    except urllib.error.URLError as e:
        latency = time.time() - start_time
        err_msg = str(e.reason).lower()
        if isinstance(e.reason, TimeoutError) or "timed out" in err_msg or "connection reset" in err_msg:
            return {
                "outcome": "AMBIGUOUS_TIMEOUT",
                "latency_seconds": latency,
                "error": str(e.reason),
                "message": (
                    "Transmission request timed out in-flight or connection dropped. "
                    "DO NOT RETRY AUTOMATICALLY AND DO NOT FORCE-RECREATE CONTAINER. "
                    "Check recipient mailbox and execution receipts before any operational change."
                ),
            }
        return {
            "outcome": "AMBIGUOUS_OUTCOME",
            "latency_seconds": latency,
            "error": str(e.reason),
            "reason": f"Transport error during execution request: {e.reason}",
        }
    except Exception as e:
        return {
            "outcome": "AMBIGUOUS_OUTCOME",
            "error": str(e),
            "reason": f"Unexpected client execution error: {e}",
        }


def scoped_disarm_caseworker_api() -> dict:
    """Execute scoped recreation of caseworker-api to restore disabled execution flags."""
    cmd = ["docker", "compose", "up", "-d", "--force-recreate", "caseworker-api"]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=30.0)
        if proc.returncode != 0:
            return {"status": "error", "error": proc.stderr}

        check_cmd = [
            "docker",
            "compose",
            "exec",
            "caseworker-api",
            "python3",
            "-c",
            "import os; print(os.environ.get('CASEWORKER_REAL_EXECUTION_ENABLED'), os.environ.get('CASEWORKER_GMAIL_EXECUTION_ENABLED'))",
        ]
        chk = subprocess.run(check_cmd, capture_output=True, text=True, timeout=10.0)
        out = chk.stdout.strip()
        flags_disarmed = ("false false" in out)
        return {"status": "ok", "flags_disarmed": flags_disarmed, "output": out}
    except Exception as e:
        return {"status": "error", "error": str(e)}


def print_four_tier_report(
    dispatch_result: dict,
    plan_info: dict,
    dest_email: str,
) -> None:
    """Format outcome into four distinct governance and operational tiers."""
    outcome = dispatch_result["outcome"]
    data = dispatch_result.get("data", {})
    receipt = data.get("receipt", {}) if isinstance(data, dict) else {}
    provider_meta = receipt.get("safe_provider_metadata", {}) if isinstance(receipt, dict) else {}

    print("\n================================================================================")
    print("JACKVERSE STAGE C TRANSMISSION REPORT — FOUR-TIER AUDIT")
    print("================================================================================")

    # Tier 1: HTTP Request Accepted by JackVerse
    print("\n[TIER 1: HTTP REQUEST ACCEPTED BY JACKVERSE]")
    print(f"Outcome Status:       {outcome}")
    print(f"HTTP Status Code:     {dispatch_result.get('http_code')}")
    print(f"Response ETag:        {dispatch_result.get('etag')}")
    print(f"Roundtrip Latency:    {dispatch_result.get('latency_seconds', 0.0):.3f}s")
    if outcome != "CONFIRMED_COMPLETION":
        print(f"Details / Reason:     {dispatch_result.get('reason') or dispatch_result.get('error') or json.dumps(dispatch_result.get('error_details'), indent=2)}")
        return

    # Tier 2: Gmail Provider Acceptance
    # Consume actual Gmail provider schema: gmail_message_id, gmail_thread_id, rfc822_message_id
    gmail_msg_id = provider_meta.get("gmail_message_id") or receipt.get("external_reference_id")
    gmail_thread_id = provider_meta.get("gmail_thread_id")
    rfc822_msg_id = provider_meta.get("rfc822_message_id")
    provider_status = provider_meta.get("provider_status")

    print("\n[TIER 2: GMAIL PROVIDER ACCEPTANCE]")
    if gmail_msg_id:
        print(f"Gmail Message ID:     {gmail_msg_id}")
        print(f"Gmail Thread ID:      {gmail_thread_id or 'None'}")
        print(f"RFC 822 Message-ID:   {rfc822_msg_id or 'None'}")
        print(f"Provider Status:      {provider_status or 'GMAIL_API_ACCEPTED'}")
        print("Provider Acceptance:  CONFIRMED (Accepted by Google Gmail API)")
    else:
        print("Gmail Message ID:     None (no provider message ID returned)")
        print("Gmail Thread ID:      None")
        print("RFC 822 Message-ID:   None")
        print("Provider Acceptance:  NOT DETECTED")

    # Tier 3: Verified Execution Receipt
    print("\n[TIER 3: VERIFIED EXECUTION RECEIPT]")
    print(f"Receipt ID:           {receipt.get('receipt_id')}")
    print(f"Receipt Status:       {receipt.get('status')}")
    print(f"Receipt Hash:         {receipt.get('receipt_hash')}")
    print(f"External Reference:   {receipt.get('external_reference_id') or gmail_msg_id or 'None'}")
    print(f"Case Transition:      {data.get('case_status')}")

    # Tier 4: Mailbox Delivery Observed by Operator
    manifest = plan_info.get("email_manifest", {})
    print("\n[TIER 4: MAILBOX DELIVERY OBSERVED BY OPERATOR]")
    print(f"Target Mailbox:       {dest_email}")
    print(f"Subject:              {manifest.get('subject')}")
    print("Verification Instructions:")
    print("  1. Log into Google Gmail account: dagogograntjack@gmail.com")
    print("  2. Verify receipt of message with subject: [JACKVERSE INTERNAL TEST] Governed Gmail Transmission Verification")
    print("  3. Verify test identifier in body: JV-TEST-20261026-B819A")
    print(f"  4. Verify attachment '{EXPECTED_ATTACHMENT_FILENAME}' (131 bytes, SHA-256: {EXPECTED_ATTACHMENT_SHA256})")
    print("================================================================================")


def main() -> int:
    parser = argparse.ArgumentParser(description="JackVerse Stage C: Guarded Gmail Dispatch Utility")
    parser.add_argument("--api-base", default="https://localhost:3443", help="Base URL of Caseworker API / Reverse Proxy")
    parser.add_argument("--ca-cert", default="certs/localhost.crt", help="Path to CA certificate for trusted TLS")
    parser.add_argument("--env-file", default=".env.caseworker-api.local", help="Path to local environment file")
    parser.add_argument("--operator-id", default=CONTROLLED_USER_ID, help="Operator identifier")
    parser.add_argument("--action-id", default=CONTROLLED_ACTION_ID, help="Action ID to execute")
    parser.add_argument("--dry-run", action="store_true", default=True, help="Perform safe preflight audit only without executing (default: True)")
    parser.add_argument("--execute-authorized", action="store_true", default=False, help="Explicit flag required to initiate transmission")
    parser.add_argument("--operator-decision-token", type=str, default="", help="Explicit human operator decision confirmation token")
    parser.add_argument("--auto-disarm", action="store_true", default=True, help="Automatically perform scoped disarm on proven pre-dispatch rejection or confirmed completion")

    args = parser.parse_args()

    api_base = args.api_base.rstrip("/")
    ca_cert = Path(args.ca_cert)
    env_file = Path(args.env_file)

    print("================================================================================")
    print("JACKVERSE PHASE 5.3.3.2 — STAGE C GUARDED GMAIL DISPATCH")
    print("================================================================================")

    # 1. TLS and Certificate Validation
    if not ca_cert.is_file():
        print(f"[FAIL] CA certificate not found at {ca_cert}. Insecure TLS is strictly forbidden.")
        return 1

    # 2. Operator Bearer Token Generation
    op_secret = _load_env_secret(env_file, "CASEWORKER_OPERATOR_SECRET")
    if not op_secret or len(op_secret) < 32:
        print("[FAIL] CASEWORKER_OPERATOR_SECRET is missing or less than 32 characters in env file.")
        return 1

    operator_token = generate_operator_token(args.operator_id, op_secret)
    print(f"[PASS] Signed operator bearer token generated for '{args.operator_id}'")

    # 3. Fail-Closed Complete Chain-of-Custody API Audit
    try:
        audit_res = verify_complete_api_chain_of_custody(api_base, ca_cert, operator_token)
        print(f"[PASS] Complete governance chain-of-custody verified over trusted TLS ({api_base})")
    except Exception as e:
        print(f"\n[FAIL-CLOSED PREFLIGHT AUDIT REJECTED] {e}")
        return 1

    action_data = audit_res["action"]
    plan_data = audit_res["plan"]
    exact_etag = audit_res["etag"]

    print("\n--- GOVERNANCE CHAIN-OF-CUSTODY (API AUDIT) ---")
    print(f"Action ID:           {action_data.get('action_id')} (Status: {action_data.get('status')}, Version: {action_data.get('version')})")
    print(f"Action ETag:         {exact_etag}")
    print(f"Plan ID:             {plan_data.get('plan_id')} (Status: {plan_data.get('status')})")
    print(f"Plan Fingerprint:    {plan_data.get('execution_plan_fingerprint')}")
    print(f"Package ID:          {action_data.get('parameters', {}).get('package_id')}")
    print(f"Package Fingerprint: {action_data.get('parameters', {}).get('package_fingerprint')}")
    print(f"Case ID:             {action_data.get('case_id')}")
    print(f"Sender / Recipient:  {EXPECTED_DESTINATION_EMAIL} -> {EXPECTED_DESTINATION_EMAIL}")
    print(f"Sealed MIME Hash:    {EXPECTED_SEALED_MIME_HASH}")
    print(f"Attachment Hash:     {EXPECTED_ATTACHMENT_SHA256} ({EXPECTED_ATTACHMENT_BYTE_SIZE} bytes)")
    print("Execution Attempts:  0 (verified)")
    print("Execution Receipts:  0 (verified)")

    # 4. Mode Evaluation: Dry-Run vs Authorized Execution
    if not args.execute_authorized:
        print("\n================================================================================")
        print("PREFLIGHT AUDIT COMPLETE — DISPATCH SAFEGUARDS ENGAGED")
        print("Mode: --dry-run (default). No HTTP execute request was sent.")
        print("All fail-closed invariants successfully verified.")
        print("To execute with explicit human authorization, operator must pass:")
        print(f"    --execute-authorized --operator-decision-token='{REQUIRED_EXECUTION_DECISION_TOKEN}'")
        print("================================================================================")
        return 0

    # 5. Deliberate Operator Execution Confirmation
    if args.operator_decision_token != REQUIRED_EXECUTION_DECISION_TOKEN:
        print(f"\n[CONFIRMATION REQUIRED] To execute one-time live dispatch, operator confirmation is required.")
        user_input = input(f"Type exact confirmation token '{REQUIRED_EXECUTION_DECISION_TOKEN}' to proceed: ")
        if user_input.strip() != REQUIRED_EXECUTION_DECISION_TOKEN:
            print("[ABORTED] Operator confirmation token mismatch. Execution aborted.")
            return 1

    print("\n--- INITIATING GUARDED DISPATCH ---")
    print(f"Target URL:    {api_base}/api/v1/actions/{args.action_id}/execute")
    print(f"If-Match ETag: {exact_etag}")
    print(f"Operator ID:   {args.operator_id}")

    result = perform_guarded_dispatch(
        api_base=api_base,
        ca_cert=ca_cert,
        action_id=args.action_id,
        etag=exact_etag,
        operator_token=operator_token,
    )

    # 6. Four-Tier Reporting
    print_four_tier_report(
        dispatch_result=result,
        plan_info=plan_data,
        dest_email=EXPECTED_DESTINATION_EMAIL,
    )

    # 7. Scoped Disarm Execution (Amendments 4 and 5)
    outcome = result["outcome"]
    if outcome == "CONFIRMED_COMPLETION":
        if args.auto_disarm:
            print("\n--- EXECUTING SCOPED DISARM (RESTORE BASE DEPLOYMENT) ---")
            disarm_res = scoped_disarm_caseworker_api()
            print(f"Disarm Status:        {disarm_res.get('status')}")
            print(f"Flags Disarmed:       {disarm_res.get('flags_disarmed')}")
            if not disarm_res.get("flags_disarmed", False):
                print("\n================================================================================")
                print("[CRITICAL: SEND COMPLETED — DISARM FAILED — MANUAL INTERVENTION REQUIRED — DO NOT RETRY]")
                print("The transmission succeeded and the verified execution receipt is recorded above.")
                print("However, automatic container recreation or flag disarm verification failed.")
                print("To manually disarm sending permissions, immediately run:")
                print("    docker compose up -d --force-recreate caseworker-api")
                print("DO NOT RETRY TRANSMISSION. An execution receipt has already been committed.")
                print("================================================================================")
                return 3
        else:
            print("\n================================================================================")
            print("MANDATORY POST-DISPATCH DISARM STEP:")
            print("Immediately restore base deployment to disarm execution flags:")
            print("    docker compose up -d --force-recreate caseworker-api")
            print("================================================================================")
        return 0

    elif outcome == "CONFIRMED_PRE_DISPATCH_REJECTION":
        if args.auto_disarm:
            print("\n--- EXECUTING SCOPED DISARM (RESTORE BASE DEPLOYMENT) ---")
            disarm_res = scoped_disarm_caseworker_api()
            print(f"Disarm Status:        {disarm_res.get('status')}")
            print(f"Flags Disarmed:       {disarm_res.get('flags_disarmed')}")
            if not disarm_res.get("flags_disarmed", False):
                print("[WARNING] Automatic disarm verification failed. Verify flags manually:")
                print("    docker compose up -d --force-recreate caseworker-api")
        return 1

    elif outcome in ("AMBIGUOUS_TIMEOUT", "AMBIGUOUS_OUTCOME"):
        print("\n================================================================================")
        print("[CRITICAL: AMBIGUOUS OUTCOME — DISARM DELAYED FOR OPERATOR RECONCILIATION]")
        print("The transmission outcome is ambiguous or unverified.")
        print(f"Details: {result.get('reason') or result.get('error') or result.get('error_details')}")
        print("Automatic container recreation was REFUSED to avoid killing an in-flight worker.")
        print("Immediate reconciliation steps:")
        print(f"  1. Check recipient mailbox: {EXPECTED_DESTINATION_EMAIL}")
        print("  2. Check container logs:    docker compose logs caseworker-api")
        print("  3. Run preflight audit:     python3 scripts/guarded_stage_c_dispatch.py --dry-run")
        print("  4. After verifying state, close dispatch window via:")
        print("     docker compose up -d --force-recreate caseworker-api")
        print("================================================================================")
        return 2

    else:
        print(f"\n[UNEXPECTED ERROR] {result.get('error')}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
