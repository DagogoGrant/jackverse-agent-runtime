#!/usr/bin/env python3
"""Verify caseworker-api container environment configuration and OAuth readiness safely.

Guarantees:
- Never prints secret values.
- Evaluates safety configuration independently from OAuth credential readiness.
- Asserts strict safety flags (CASEWORKER_DEV_AUTH=false, REAL_EXECUTION=false, GMAIL_EXECUTION=false).
- Evaluates complete OAuth prerequisites (client ID, client secret, encryption key, operator secret, redirect URI).
- Verifies operator connection against database credential storage without claiming untested browser flows.
"""

from __future__ import annotations

import os
import sqlite3
import sys
from pathlib import Path

repo_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(repo_root / "src"))

from caseworker.api.config import APIConfig


def _check_db_credentials(db_path: str) -> tuple[bool, str, list[str]]:
    """Inspect SQLite database for verified Google OAuth credentials without throwing."""
    if not os.path.isfile(db_path):
        return False, "", []
    try:
        conn = sqlite3.connect(db_path, timeout=3.0)
        cursor = conn.cursor()
        cursor.execute(
            "SELECT authenticated_email, scopes FROM oauth_credentials WHERE provider = 'gmail' ORDER BY updated_at DESC LIMIT 1"
        )
        row = cursor.fetchone()
        conn.close()
        if row and row[0]:
            email = str(row[0])
            raw_scopes = str(row[1]) if row[1] else ""
            scopes = [s.strip() for s in raw_scopes.split(",") if s.strip()] if "," in raw_scopes else raw_scopes.split()
            return True, email, scopes
    except Exception:
        pass
    return False, "", []


def main() -> int:
    config = APIConfig()
    safety_errors = []
    oauth_missing = []

    print("================================================================================")
    print("JACKVERSE CASEWORKER API CONFIGURATION & OAUTH AUDIT")
    print("================================================================================")

    # -------------------------------------------------------------------------
    # 1. Safety Configuration (Fail-Closed Execution Gates)
    # -------------------------------------------------------------------------
    print("\n[1] SAFETY CONFIGURATION (FAIL-CLOSED BOUNDARIES)")
    dev_auth_ok = not config.dev_auth
    print(f"  [-] CASEWORKER_DEV_AUTH:                 {config.dev_auth} {'(PASS)' if dev_auth_ok else '(FAIL - MUST BE FALSE)'}")
    if not dev_auth_ok:
        safety_errors.append("CASEWORKER_DEV_AUTH is True (must be False)")

    real_exec_ok = not config.real_execution_enabled
    print(f"  [-] CASEWORKER_REAL_EXECUTION_ENABLED:   {config.real_execution_enabled} {'(PASS)' if real_exec_ok else '(FAIL - MUST BE FALSE)'}")
    if not real_exec_ok:
        safety_errors.append("CASEWORKER_REAL_EXECUTION_ENABLED is True (must be False)")

    gmail_exec_ok = not config.gmail_execution_enabled
    print(f"  [-] CASEWORKER_GMAIL_EXECUTION_ENABLED:  {config.gmail_execution_enabled} {'(PASS)' if gmail_exec_ok else '(FAIL - MUST BE FALSE)'}")
    if not gmail_exec_ok:
        safety_errors.append("CASEWORKER_GMAIL_EXECUTION_ENABLED is True (must be False)")

    safety_verdict = "PASS" if not safety_errors else "FAIL"

    # -------------------------------------------------------------------------
    # 2. Google OAuth Prerequisites
    # -------------------------------------------------------------------------
    print("\n[2] GOOGLE OAUTH PREREQUISITES (CONTAINER SETTINGS)")
    expected_redirect = "https://localhost:3443/api/v1/auth/google/callback"
    redirect_pass = (config.gmail_redirect_uri == expected_redirect)
    print(f"  [-] CASEWORKER_GMAIL_REDIRECT_URI:       {config.gmail_redirect_uri} {'(PASS)' if redirect_pass else f'(FAIL - expected {expected_redirect})'}")
    if not redirect_pass:
        oauth_missing.append(f"CASEWORKER_GMAIL_REDIRECT_URI mismatch (got: {config.gmail_redirect_uri})")

    client_id = config.gmail_client_id.strip()
    has_client_id = bool(client_id) and "your_google_client_id" not in client_id
    print(f"  [-] CASEWORKER_GMAIL_CLIENT_ID:          {'[CONFIGURED]' if has_client_id else '[NOT CONFIGURED]'}")
    if not has_client_id:
        oauth_missing.append("CASEWORKER_GMAIL_CLIENT_ID missing or placeholder")

    client_secret = config.gmail_client_secret.strip()
    has_client_secret = bool(client_secret) and "your_google_client_secret" not in client_secret
    print(f"  [-] CASEWORKER_GMAIL_CLIENT_SECRET:      {'[CONFIGURED]' if has_client_secret else '[NOT CONFIGURED]'}")
    if not has_client_secret:
        oauth_missing.append("CASEWORKER_GMAIL_CLIENT_SECRET missing or placeholder")

    enc_key = config.encryption_key.strip()
    has_enc_key = len(enc_key) >= 32 and "replace_with" not in enc_key
    enc_status = "[CONFIGURED - VALID LENGTH (>= 32 chars)]" if has_enc_key else (
        "[CONFIGURED - INSUFFICIENT ENTROPY (< 32 chars)]" if enc_key else "[NOT CONFIGURED]"
    )
    print(f"  [-] CASEWORKER_ENCRYPTION_KEY:           {enc_status}")
    if not has_enc_key:
        oauth_missing.append("CASEWORKER_ENCRYPTION_KEY missing or insufficient entropy (< 32 chars)")

    op_secret = config.operator_secret.strip()
    has_op_secret = len(op_secret) >= 32 and "replace_with" not in op_secret
    op_status = "[CONFIGURED - VALID LENGTH (>= 32 chars)]" if has_op_secret else (
        "[CONFIGURED - INSUFFICIENT ENTROPY (< 32 chars)]" if op_secret else "[NOT CONFIGURED]"
    )
    print(f"  [-] CASEWORKER_OPERATOR_SECRET:          {op_status}")
    if not has_op_secret:
        oauth_missing.append("CASEWORKER_OPERATOR_SECRET missing or insufficient entropy (< 32 chars)")

    oauth_verdict = "PASS" if not oauth_missing else "NOT CONFIGURED"

    # -------------------------------------------------------------------------
    # 3. Operator Browser Connection Status
    # -------------------------------------------------------------------------
    print("\n[3] OPERATOR BROWSER CONNECTION STATUS")
    has_creds, email, scopes = _check_db_credentials(config.db_path)
    has_send = any("gmail.send" in s for s in scopes)
    if has_creds and email and has_send:
        connection_status = f"CONNECTED (Email: {email}, Scopes: {scopes})"
        connection_verdict = "PASS"
    elif has_creds and email:
        connection_status = f"CONNECTED WITHOUT SEND (Email: {email}, Missing gmail.send scope)"
        connection_verdict = "INCOMPLETE (MISSING gmail.send)"
    else:
        connection_status = "Awaiting real browser operator consent and callback at https://localhost:3443"
        connection_verdict = "NOT VERIFIED"

    print(f"  [-] Stored OAuth Credential:             {connection_status}")
    print(f"  [-] Connection Verdict:                  {connection_verdict}")

    # -------------------------------------------------------------------------
    # Summary Table
    # -------------------------------------------------------------------------
    print("\n================================================================================")
    print("AUDIT SUMMARY REPORT")
    print("================================================================================")
    print(f"Safety configuration:             {safety_verdict}")
    print(f"Google OAuth prerequisites:       {oauth_verdict}")
    print(f"Operator connection:              {connection_verdict}")
    print("================================================================================")

    if safety_errors:
        print("CRITICAL: Safety configuration FAILED:")
        for err in safety_errors:
            print(f"  * {err}")
        return 1

    if oauth_missing:
        print("INFO: Google OAuth prerequisites are NOT CONFIGURED:")
        for item in oauth_missing:
            print(f"  * {item}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
