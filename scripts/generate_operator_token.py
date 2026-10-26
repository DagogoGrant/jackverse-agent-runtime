#!/usr/bin/env python3
"""Generate a signed, time-limited operator bearer token for browser session bootstrap.

Usage:
    python3 scripts/generate_operator_token.py [--operator-id ID] [--ttl-seconds SECONDS]

Security guarantees:
- Reads CASEWORKER_OPERATOR_SECRET from .env.caseworker-api.local or environment.
- Fails closed if secret is missing or under 32 characters.
- Rejects 'dev-' prefixes (developer tokens are strictly forbidden in production mode).
- Outputs the signed token directly without printing secret values.
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import sys

# Add src to PYTHONPATH so caseworker modules are importable
repo_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(repo_root / "src"))

from caseworker.api.identity import generate_operator_token


def _load_secret_from_env_file(env_file_path: Path) -> str:
    """Extract CASEWORKER_OPERATOR_SECRET from local env file if present."""
    if not env_file_path.is_file():
        return ""
    try:
        with open(env_file_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line.startswith("#") or not line or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                if k.strip() == "CASEWORKER_OPERATOR_SECRET":
                    return v.strip().strip("'\"")
    except Exception:
        pass
    return ""


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generate a signed operator bearer token for browser session bootstrap."
    )
    parser.add_argument(
        "--operator-id",
        type=str,
        default="operator-1",
        help="Unique identifier for the operator (must not start with 'dev-' or 'dev_'). Default: 'operator-1'",
    )
    parser.add_argument(
        "--ttl-seconds",
        type=int,
        default=86400,
        help="Token validity duration in seconds (default: 86400 = 24 hours).",
    )
    parser.add_argument(
        "--env-file",
        type=str,
        default=".env.caseworker-api.local",
        help="Path to local caseworker-api env file (default: .env.caseworker-api.local).",
    )
    args = parser.parse_args()

    operator_id = args.operator_id.strip()
    if operator_id.startswith("dev-") or operator_id.startswith("dev_"):
        sys.stderr.write(
            f"ERROR: Operator ID '{operator_id}' starts with forbidden dev prefix. "
            "Developer operator IDs are strictly rejected in production mode.\n"
        )
        sys.exit(1)

    secret = os.getenv("CASEWORKER_OPERATOR_SECRET", "")
    if not secret:
        env_path = repo_root / args.env_file
        secret = _load_secret_from_env_file(env_path)

    if not secret or len(secret) < 32:
        sys.stderr.write(
            "ERROR: CASEWORKER_OPERATOR_SECRET is missing or less than 32 characters.\n"
            "Please configure CASEWORKER_OPERATOR_SECRET in your .env.caseworker-api.local or environment.\n"
        )
        sys.exit(1)

    try:
        token = generate_operator_token(
            operator_id=operator_id,
            secret=secret,
            ttl_seconds=args.ttl_seconds,
        )
    except Exception as exc:
        sys.stderr.write(f"ERROR: Failed to generate token: {exc}\n")
        sys.exit(1)

    print("================================================================================")
    print("JACKVERSE OPERATOR TOKEN GENERATED")
    print("================================================================================")
    print(f"Operator ID:  {operator_id}")
    print(f"TTL:          {args.ttl_seconds} seconds")
    print(f"Token:        {token}")
    print("--------------------------------------------------------------------------------")
    print("To establish your browser session:")
    print("1. Open https://localhost:3443 in your browser.")
    print("2. Click 'OPERATOR SESSION' in the top right navigation bar.")
    print("3. Paste the token above into the password field and click 'Authenticate Session'.")
    print("4. Navigate to any case with an email plan and click 'Connect Google Account'.")
    print("================================================================================")


if __name__ == "__main__":
    main()
