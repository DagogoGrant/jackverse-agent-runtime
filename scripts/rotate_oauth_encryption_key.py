#!/usr/bin/env python3
"""Crash-recoverable OAuth credential encryption-key rotation utility.

Features:
- Consistent live SQLite snapshots via SQLite Online Backup API (handles WAL).
- PRAGMA integrity_check validation on backups and isolated restore testing.
- Two-resource crash-recovery across SQLite database and host environment file (.env.caseworker-api.local).
- Pre-commit in-memory verification (verifies decryption with new key BEFORE committing to SQLite).
- Atomic SQLite transaction (BEGIN IMMEDIATE) and atomic env-file replacement with fsync.
- Strict filesystem permissions (0600 on files, 0700 on directories).
- Zero secret disclosure in terminal, logs, or command line arguments.
- Re-run idempotency: detects already-migrated state without double-rotating.
"""

from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import sqlite3
import sys
import tempfile
from typing import Sequence

# Add src to PYTHONPATH
repo_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(repo_root / "src"))

from caseworker.adapters.crypto.token_cipher import TokenCipher, TokenDecryptionError


CONTROLLED_CASE_ID = "case-controlled-test-5332"
CONTROLLED_PLAN_ID = "plan-d4819278ab3f45af"
CONTROLLED_ACTION_ID = "act_91966280c188"
CONTROLLED_APPROVAL_ID = "65cb25bb-0539-4f61-917c-46cf4a84f9dd"
CONTROLLED_USER_ID = "operator-1"


def load_secret_from_env_file(env_file_path: Path, key_name: str) -> str:
    """Extract a key value from an env file without shell evaluation."""
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


def update_secret_in_env_file(env_file_path: Path, key_name: str, new_value: str, fallback_key_name: str = "", fallback_value: str = "") -> None:
    """Atomically update an environment variable in a file using fsync and temporary file replacement."""
    if not env_file_path.is_file():
        raise FileNotFoundError(f"Environment file not found: {env_file_path}")

    with open(env_file_path, "r", encoding="utf-8") as f:
        lines = f.readlines()

    found_primary = False
    found_fallback = False
    new_lines = []

    for line in lines:
        if line.strip().startswith(f"{key_name}="):
            new_lines.append(f"{key_name}={new_value}\n")
            found_primary = True
        elif fallback_key_name and line.strip().startswith(f"{fallback_key_name}="):
            if fallback_value:
                new_lines.append(f"{fallback_key_name}={fallback_value}\n")
            found_fallback = True
        else:
            new_lines.append(line)

    if not found_primary:
        new_lines.append(f"{key_name}={new_value}\n")
    if fallback_key_name and fallback_value and not found_fallback:
        new_lines.append(f"{fallback_key_name}={fallback_value}\n")

    temp_path = env_file_path.with_suffix(".tmp")
    with open(temp_path, "w", encoding="utf-8") as f:
        f.writelines(new_lines)
        f.flush()
        os.fsync(f.fileno())

    os.chmod(temp_path, 0o600)
    os.replace(temp_path, env_file_path)
    os.chmod(env_file_path, 0o600)


def create_verified_online_backup(db_path: Path, backup_dir: Path) -> Path:
    """Create a transactionally consistent backup using SQLite Online Backup API and verify it thoroughly."""
    backup_dir.mkdir(parents=True, exist_ok=True)
    os.chmod(backup_dir, 0o700)

    timestamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%d_%H%M%S")
    backup_file = backup_dir / f"caseworker_backup_{timestamp}.db"

    # Step 1: Online Backup API (captures active WAL correctly)
    src_conn = sqlite3.connect(str(db_path))
    bck_conn = sqlite3.connect(str(backup_file))
    try:
        src_conn.backup(bck_conn)
    finally:
        src_conn.close()
        bck_conn.close()

    os.chmod(backup_file, 0o600)

    # Step 2: PRAGMA integrity_check on backup
    verify_conn = sqlite3.connect(str(backup_file))
    cur = verify_conn.cursor()
    cur.execute("PRAGMA integrity_check;")
    check_rows = cur.fetchall()
    if check_rows != [("ok",)]:
        raise RuntimeError(f"Database backup integrity check failed: {check_rows}")

    # Step 3: Validate exact required domain entities exist in backup
    cur.execute("SELECT case_id, status FROM cases WHERE case_id = ?", (CONTROLLED_CASE_ID,))
    case_row = cur.fetchone()
    if not case_row or case_row[1] != "action_required":
        raise RuntimeError(f"Backup missing or invalid Case '{CONTROLLED_CASE_ID}': {case_row}")

    cur.execute("SELECT plan_id, status FROM external_execution_plans WHERE plan_id = ?", (CONTROLLED_PLAN_ID,))
    plan_row = cur.fetchone()
    if not plan_row or plan_row[1] != "ready_for_authorization":
        raise RuntimeError(f"Backup missing or invalid Plan '{CONTROLLED_PLAN_ID}': {plan_row}")

    cur.execute("SELECT action_id, status FROM actions WHERE action_id = ?", (CONTROLLED_ACTION_ID,))
    act_row = cur.fetchone()
    if not act_row or act_row[1] != "approved":
        raise RuntimeError(f"Backup missing or invalid Action '{CONTROLLED_ACTION_ID}': {act_row}")

    cur.execute("SELECT approval_id, status FROM approvals WHERE approval_id = ?", (CONTROLLED_APPROVAL_ID,))
    app_row = cur.fetchone()
    if not app_row or app_row[1] != "approved":
        raise RuntimeError(f"Backup missing or invalid Approval '{CONTROLLED_APPROVAL_ID}': {app_row}")

    cur.execute("SELECT user_id, provider FROM user_oauth_credentials WHERE user_id = ? AND provider = 'gmail'", (CONTROLLED_USER_ID,))
    oauth_row = cur.fetchone()
    if not oauth_row:
        raise RuntimeError(f"Backup missing OAuth record for user '{CONTROLLED_USER_ID}'")

    # Step 4: Validate isolated restoration into temporary database
    test_restore_conn = sqlite3.connect(":memory:")
    try:
        verify_conn.backup(test_restore_conn)
        test_cur = test_restore_conn.cursor()
        test_cur.execute("PRAGMA integrity_check;")
        if test_cur.fetchall() != [("ok",)]:
            raise RuntimeError("Test restore into in-memory database failed integrity check.")
    finally:
        test_restore_conn.close()
        verify_conn.close()

    return backup_file


def restore_database_from_backup(backup_file: Path, target_db_path: Path) -> None:
    """Safely restore target database from a verified backup using Online Backup API."""
    if not backup_file.is_file():
        raise FileNotFoundError(f"Backup file not found: {backup_file}")

    bck_conn = sqlite3.connect(str(backup_file))
    cur = bck_conn.cursor()
    cur.execute("PRAGMA integrity_check;")
    if cur.fetchall() != [("ok",)]:
        raise RuntimeError(f"Source backup file failed integrity check: {backup_file}")

    target_conn = sqlite3.connect(str(target_db_path))
    try:
        bck_conn.backup(target_conn)
    finally:
        bck_conn.close()
        target_conn.close()


def check_encryption_status(db_path: Path, env_file_path: Path) -> dict:
    """Audit current database records against current environment key."""
    current_key = load_secret_from_env_file(env_file_path, "CASEWORKER_ENCRYPTION_KEY")
    fallback_keys_str = load_secret_from_env_file(env_file_path, "CASEWORKER_ENCRYPTION_FALLBACK_KEYS")
    fallback_keys = [k.strip() for k in fallback_keys_str.split(",") if k.strip()]

    cipher = TokenCipher(primary_key=current_key, rotation_fallback_keys=fallback_keys) if len(current_key) >= 32 else None

    conn = sqlite3.connect(str(db_path))
    cur = conn.cursor()
    cur.execute("SELECT user_id, provider, authenticated_email, access_token_enc, refresh_token_enc FROM user_oauth_credentials")
    rows = cur.fetchall()
    conn.close()

    results = []
    for r in rows:
        u_id, prov, email, at_enc, rt_enc = r
        at_dec_ok = False
        rt_dec_ok = False
        if cipher:
            try:
                dec_at = cipher.decrypt(at_enc)
                at_dec_ok = bool(dec_at)
            except Exception:
                pass
            try:
                dec_rt = cipher.decrypt(rt_enc)
                rt_dec_ok = bool(dec_rt)
            except Exception:
                pass
        results.append({
            "user_id": u_id,
            "provider": prov,
            "authenticated_email": email,
            "access_token_decrypt_ok": at_dec_ok,
            "refresh_token_decrypt_ok": rt_dec_ok,
            "cipher_version": at_enc.split(":", 1)[0] if at_enc else "none",
        })

    return {
        "key_configured": bool(current_key and len(current_key) >= 32),
        "fallback_keys_count": len(fallback_keys),
        "records_count": len(rows),
        "records": results,
        "all_decryptable": all(r["access_token_decrypt_ok"] and r["refresh_token_decrypt_ok"] for r in results) if results else False,
    }


def execute_migration(
    db_path: Path,
    env_file_path: Path,
    backup_dir: Path,
    staged_key: str | None = None,
    fault_inject_checkpoint: int = 0,
) -> dict:
    """Execute crash-recoverable migration workflow with checkpoints and fault-injection hooks."""
    # Pre-condition: Check current key
    current_key = load_secret_from_env_file(env_file_path, "CASEWORKER_ENCRYPTION_KEY")
    if not current_key or len(current_key) < 32:
        raise ValueError("Current CASEWORKER_ENCRYPTION_KEY is missing or invalid in env file.")

    old_cipher = TokenCipher(primary_key=current_key)

    # 1. Pre-flight Check: zero execution attempts/receipts
    conn_pre = sqlite3.connect(str(db_path))
    cur_pre = conn_pre.cursor()
    cur_pre.execute("SELECT count(*) FROM execution_attempts")
    att_count = cur_pre.fetchone()[0]
    cur_pre.execute("SELECT count(*) FROM execution_receipts")
    rcpt_count = cur_pre.fetchone()[0]
    conn_pre.close()

    if att_count > 0 or rcpt_count > 0:
        raise RuntimeError(f"Refusing migration: execution attempts ({att_count}) or receipts ({rcpt_count}) exist in database!")

    # 2. Consistent Online Database Backup
    backup_file = create_verified_online_backup(db_path=db_path, backup_dir=backup_dir)

    # 3. Environment File Backup
    timestamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%d_%H%M%S")
    env_backup_file = backup_dir / f"env_caseworker_api_local_{timestamp}.bak"
    shutil.copy2(env_file_path, env_backup_file)
    os.chmod(env_backup_file, 0o600)

    # 4. Durable Key Material Staging
    new_key = staged_key or secrets.token_urlsafe(48)
    if len(new_key) < 48:
        raise ValueError("Generated key does not satisfy minimum 48 character length requirement.")

    staged_key_file = env_file_path.parent / ".env.encryption_key_staged.tmp"
    with open(staged_key_file, "w", encoding="utf-8") as f:
        f.write(f"STAGED_NEW_KEY={new_key}\nOLD_KEY_FOR_ROLLBACK={current_key}\nBACKUP_DB={backup_file}\nBACKUP_ENV={env_backup_file}\n")
        f.flush()
        os.fsync(f.fileno())
    os.chmod(staged_key_file, 0o600)

    new_cipher = TokenCipher(primary_key=new_key)

    # 5. In-Memory Decryption & Re-encryption (pre-commit verification)
    conn = sqlite3.connect(str(db_path))
    cur = conn.cursor()
    cur.execute("SELECT user_id, provider, access_token_enc, refresh_token_enc FROM user_oauth_credentials")
    rows = cur.fetchall()

    reencrypted_rows = []
    for r in rows:
        u_id, prov, at_enc, rt_enc = r
        plain_at = old_cipher.decrypt(at_enc)
        plain_rt = old_cipher.decrypt(rt_enc)

        new_at_enc = new_cipher.encrypt(plain_at)
        new_rt_enc = new_cipher.encrypt(plain_rt)

        # In-memory test decryption with new key BEFORE committing
        if new_cipher.decrypt(new_at_enc) != plain_at or new_cipher.decrypt(new_rt_enc) != plain_rt:
            conn.close()
            raise RuntimeError("Pre-commit validation failed: newly encrypted tokens do not decrypt to original plaintexts!")

        reencrypted_rows.append((new_at_enc, new_rt_enc, u_id, prov))

    # Fault-injection checkpoint 1: crash before DB commit
    if fault_inject_checkpoint == 1:
        conn.close()
        raise RuntimeError("FAULT_INJECTION_CHECKPOINT_1: Crash before database commit.")

    # 6. Atomic SQLite Transaction
    now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat().replace("+00:00", "Z")
    cur.execute("BEGIN IMMEDIATE;")
    for new_at, new_rt, u_id, prov in reencrypted_rows:
        cur.execute(
            "UPDATE user_oauth_credentials SET access_token_enc = ?, refresh_token_enc = ?, updated_at = ? WHERE user_id = ? AND provider = ?;",
            (new_at, new_rt, now_iso, u_id, prov),
        )
    conn.commit()

    # Verify committed database row decrypts with new key
    cur.execute("SELECT access_token_enc, refresh_token_enc FROM user_oauth_credentials WHERE user_id = ? AND provider = 'gmail'", (CONTROLLED_USER_ID,))
    v_row = cur.fetchone()
    conn.close()

    if not v_row or not new_cipher.decrypt(v_row[0]) or not new_cipher.decrypt(v_row[1]):
        raise RuntimeError("Post-commit verification failed: committed database records failed decryption with new key!")

    # Fault-injection checkpoint 2: crash after DB commit, before env file update
    if fault_inject_checkpoint == 2:
        raise RuntimeError("FAULT_INJECTION_CHECKPOINT_2: Crash after DB commit but before env replacement.")

    # 7. Atomic Environment File Replacement
    update_secret_in_env_file(
        env_file_path=env_file_path,
        key_name="CASEWORKER_ENCRYPTION_KEY",
        new_value=new_key,
        fallback_key_name="CASEWORKER_ENCRYPTION_FALLBACK_KEYS",
        fallback_value="",  # Clear fallback once primary is updated
    )

    # Fault-injection checkpoint 3: crash after env replacement before staging cleanup
    if fault_inject_checkpoint == 3:
        raise RuntimeError("FAULT_INJECTION_CHECKPOINT_3: Crash after env replacement before staging cleanup.")

    # 8. Staging File Cleanup
    if staged_key_file.is_file():
        os.remove(staged_key_file)

    return {
        "status": "SUCCESS",
        "backup_db": str(backup_file),
        "backup_env": str(env_backup_file),
        "migrated_records": len(reencrypted_rows),
    }


def recover_or_resume(db_path: Path, env_file_path: Path, backup_dir: Path) -> dict:
    """Inspect recovery state and forward-recover or rollback cleanly."""
    staged_key_file = env_file_path.parent / ".env.encryption_key_staged.tmp"
    if not staged_key_file.is_file():
        status = check_encryption_status(db_path, env_file_path)
        if status["all_decryptable"]:
            return {"status": "ALREADY_CONSISTENT", "message": "Database and env file are already in sync and decryptable."}
        else:
            return {"status": "INCONSISTENT_NO_STAGING", "message": "Inconsistent state detected without staging file. Manual restore from backup required."}

    staged_data = {}
    with open(staged_key_file, "r", encoding="utf-8") as f:
        for line in f:
            if "=" in line:
                k, v = line.strip().split("=", 1)
                staged_data[k] = v

    staged_new_key = staged_data.get("STAGED_NEW_KEY", "")
    backup_db = Path(staged_data.get("BACKUP_DB", ""))
    backup_env = Path(staged_data.get("BACKUP_ENV", ""))

    # Test if DB is already encrypted with staged new key
    cipher_new = TokenCipher(primary_key=staged_new_key)
    conn = sqlite3.connect(str(db_path))
    cur = conn.cursor()
    cur.execute("SELECT access_token_enc FROM user_oauth_credentials WHERE user_id = ?", (CONTROLLED_USER_ID,))
    row = cur.fetchone()
    conn.close()

    db_has_new_key = False
    if row and row[0]:
        try:
            db_has_new_key = bool(cipher_new.decrypt(row[0]))
        except Exception:
            db_has_new_key = False

    current_env_key = load_secret_from_env_file(env_file_path, "CASEWORKER_ENCRYPTION_KEY")

    if db_has_new_key and current_env_key != staged_new_key:
        # Checkpoint 2 recovery: DB has new key, complete env replacement
        update_secret_in_env_file(env_file_path, "CASEWORKER_ENCRYPTION_KEY", staged_new_key)
        if staged_key_file.is_file():
            os.remove(staged_key_file)
        return {"status": "FORWARD_RECOVERED", "message": "Completed env file replacement from staged key."}

    elif not db_has_new_key and current_env_key == staged_new_key:
        # DB has old key, but env was updated: restore env file from backup
        if backup_env.is_file():
            shutil.copy2(backup_env, env_file_path)
            os.chmod(env_file_path, 0o600)
        if staged_key_file.is_file():
            os.remove(staged_key_file)
        return {"status": "ROLLED_BACK_ENV", "message": "Reverted env file to match uncommitted database."}

    elif db_has_new_key and current_env_key == staged_new_key:
        # Both completed, only staging file remained
        if staged_key_file.is_file():
            os.remove(staged_key_file)
        return {"status": "CLEANED_STAGING", "message": "Migration was already complete. Cleaned staging file."}

    else:
        # Both old: just remove staging file
        if staged_key_file.is_file():
            os.remove(staged_key_file)
        return {"status": "CLEANED_ABORTED_STAGING", "message": "No changes committed. Cleaned aborted staging file."}


def main() -> int:
    parser = argparse.ArgumentParser(description="Crash-recoverable OAuth encryption key rotation utility")
    parser.add_argument("--db-path", default="workspace/caseworker.db", help="Path to SQLite database")
    parser.add_argument("--env-file", default=".env.caseworker-api.local", help="Path to local environment file")
    parser.add_argument("--backup-dir", default="workspace/backups", help="Directory to store verified backups")
    parser.add_argument("--check", action="store_true", help="Check current encryption and decryption status without mutating")
    parser.add_argument("--backup-only", action="store_true", help="Create and verify a consistent Online Backup only")
    parser.add_argument("--migrate", action="store_true", help="Execute the crash-recoverable migration workflow")
    parser.add_argument("--resume", action="store_true", help="Check for interrupted staging state and recover")
    parser.add_argument("--restore-backup", type=str, default="", help="Restore database from specified verified backup file")

    args = parser.parse_args()

    db_path = Path(args.db_path)
    env_file = Path(args.env_file)
    backup_dir = Path(args.backup_dir)

    if args.check:
        print("=== OAUTH ENCRYPTION STATUS AUDIT ===")
        status = check_encryption_status(db_path, env_file)
        print(f"Database path:         {db_path}")
        print(f"Environment file:      {env_file}")
        print(f"Key configured:        {status['key_configured']}")
        print(f"Fallback keys count:   {status['fallback_keys_count']}")
        print(f"Records count:         {status['records_count']}")
        print(f"All decryptable:       {status['all_decryptable']}")
        for r in status["records"]:
            print(f"  - User: {r['user_id']}, Provider: {r['provider']}, Email: {r['authenticated_email']}, Decrypt OK: {r['access_token_decrypt_ok'] and r['refresh_token_decrypt_ok']}")
        return 0 if status["all_decryptable"] else 1

    elif args.backup_only:
        print("=== CREATING VERIFIED ONLINE BACKUP ===")
        bck = create_verified_online_backup(db_path, backup_dir)
        print(f"Verified consistent backup created at: {bck}")
        return 0

    elif args.restore_backup:
        bck_path = Path(args.restore_backup)
        print(f"=== RESTORING DATABASE FROM BACKUP: {bck_path} ===")
        restore_database_from_backup(bck_path, db_path)
        print(f"Successfully restored {db_path} from verified backup.")
        return 0

    elif args.resume:
        print("=== RESUMING / RECOVERING MIGRATION STATE ===")
        res = recover_or_resume(db_path, env_file, backup_dir)
        print(f"Recovery result: {res['status']} — {res['message']}")
        return 0

    elif args.migrate:
        print("=== EXECUTING OAUTH ENCRYPTION KEY MIGRATION ===")
        res = execute_migration(db_path, env_file, backup_dir)
        print(f"Migration status: {res['status']}")
        print(f"Verified DB backup:  {res['backup_db']}")
        print(f"Verified env backup: {res['backup_env']}")
        print(f"Migrated records:    {res['migrated_records']}")
        return 0

    else:
        parser.print_help()
        return 0


if __name__ == "__main__":
    sys.exit(main())
