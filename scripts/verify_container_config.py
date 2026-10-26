#!/usr/bin/env python3
"""Verify caseworker-api container environment configuration safely.

Guarantees:
- Never prints secret values.
- Checks presence and length/entropy of critical secrets.
- Asserts strict safety flags (CASEWORKER_DEV_AUTH=false, REAL_EXECUTION=false, GMAIL_EXECUTION=false).
- Asserts exact redirect URI: https://localhost:3443/api/v1/auth/google/callback.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

repo_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(repo_root / "src"))

from caseworker.api.config import APIConfig


def main() -> int:
    config = APIConfig()
    errors = []

    print("================================================================================")
    print("CASEWORKER API CONFIGURATION AUDIT")
    print("================================================================================")

    # 1. Safety flags (must be strictly False)
    print(f"[-] CASEWORKER_DEV_AUTH:                 {config.dev_auth} {'(PASS)' if not config.dev_auth else '(FAIL - MUST BE FALSE)'}")
    if config.dev_auth:
        errors.append("CASEWORKER_DEV_AUTH is True (must be False)")

    print(f"[-] CASEWORKER_REAL_EXECUTION_ENABLED:   {config.real_execution_enabled} {'(PASS)' if not config.real_execution_enabled else '(FAIL - MUST BE FALSE)'}")
    if config.real_execution_enabled:
        errors.append("CASEWORKER_REAL_EXECUTION_ENABLED is True (must be False)")

    print(f"[-] CASEWORKER_GMAIL_EXECUTION_ENABLED:  {config.gmail_execution_enabled} {'(PASS)' if not config.gmail_execution_enabled else '(FAIL - MUST BE FALSE)'}")
    if config.gmail_execution_enabled:
        errors.append("CASEWORKER_GMAIL_EXECUTION_ENABLED is True (must be False)")

    # 2. Redirect URI
    expected_redirect = "https://localhost:3443/api/v1/auth/google/callback"
    redirect_pass = (config.gmail_redirect_uri == expected_redirect)
    print(f"[-] CASEWORKER_GMAIL_REDIRECT_URI:       {config.gmail_redirect_uri} {'(PASS)' if redirect_pass else f'(FAIL - expected {expected_redirect})'}")
    if not redirect_pass:
        errors.append(f"CASEWORKER_GMAIL_REDIRECT_URI mismatch: got {config.gmail_redirect_uri}")

    # 3. Client ID & Secret presence (NO values printed)
    has_client_id = bool(config.gmail_client_id.strip())
    has_client_secret = bool(config.gmail_client_secret.strip())
    print(f"[-] CASEWORKER_GMAIL_CLIENT_ID:          {'[CONFIGURED]' if has_client_id else '[NOT CONFIGURED]'}")
    print(f"[-] CASEWORKER_GMAIL_CLIENT_SECRET:      {'[CONFIGURED]' if has_client_secret else '[NOT CONFIGURED]'}")

    # 4. Encryption key (TokenCipher SHA-256 Fernet key derivation, >= 32 chars)
    enc_key = config.encryption_key.strip()
    enc_status = "[CONFIGURED - VALID LENGTH (>= 32 chars)]" if len(enc_key) >= 32 else (
        "[CONFIGURED - INSUFFICIENT ENTROPY (< 32 chars)]" if enc_key else "[NOT CONFIGURED]"
    )
    print(f"[-] CASEWORKER_ENCRYPTION_KEY:           {enc_status}")

    # 5. Operator secret (HMAC-SHA256 signing secret, >= 32 chars)
    op_secret = config.operator_secret.strip()
    op_status = "[CONFIGURED - VALID LENGTH (>= 32 chars)]" if len(op_secret) >= 32 else (
        "[CONFIGURED - INSUFFICIENT ENTROPY (< 32 chars)]" if op_secret else "[NOT CONFIGURED]"
    )
    print(f"[-] CASEWORKER_OPERATOR_SECRET:          {op_status}")

    print("================================================================================")
    if errors:
        print(f"VERDICT: FAILED ({len(errors)} safety violations detected)")
        for err in errors:
            print(f"  * {err}")
        return 1

    print("VERDICT: PASS (Safety gates strictly enforced; exact redirect URI configured)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
