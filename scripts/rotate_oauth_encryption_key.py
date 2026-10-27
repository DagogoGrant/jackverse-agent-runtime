#!/usr/bin/env python3
"""Crash-recoverable OAuth credential encryption-key rotation utility for JackVerse.

Enforces strict maintenance isolation, durable migration-state tracking,
exclusive secret staging, matching backup-set verification, dual-token checks,
idempotent re-run protection, and guarded backup restoration.
"""

from __future__ import annotations

import argparse
import datetime
import fcntl
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
import uuid

# Add src to PYTHONPATH
repo_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(repo_root / "src"))

from caseworker.adapters.crypto.token_cipher import TokenCipher, TokenDecryptionError


CONTROLLED_CASE_ID = "case-controlled-test-5332"
CONTROLLED_PLAN_ID = "plan-d4819278ab3f45af"
CONTROLLED_PLAN_FINGERPRINT = "6e46f1a5cef7364a843c4b8e8713fa05a0506673a41815597b55107fa729a0ae"
CONTROLLED_ACTION_ID = "act_91966280c188"
CONTROLLED_APPROVAL_ID = "65cb25bb-0539-4f61-917c-46cf4a84f9dd"
CONTROLLED_USER_ID = "operator-1"
EXPECTED_DESTINATION_EMAIL = "dagogograntjack@gmail.com"

# Migration State Enum values
STATE_NOT_STARTED = "NOT_STARTED"
STATE_STAGED = "STAGED"
STATE_DATABASE_UPDATED = "DATABASE_UPDATED"
STATE_ENVIRONMENT_UPDATED = "ENVIRONMENT_UPDATED"
STATE_COMPLETED = "COMPLETED"
STATE_RECOVERY_REQUIRED = "RECOVERY_REQUIRED"


def _key_fingerprint(key: str) -> str:
    """Return an opaque 16-character SHA-256 fingerprint of a secret key without disclosing it."""
    if not key:
        return "none"
    return hashlib.sha256(key.encode("utf-8")).hexdigest()[:16]


def _file_sha256(path: Path) -> str:
    """Compute SHA-256 digest of a file."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(65536):
            h.update(chunk)
    return h.hexdigest()


def _create_secure_file_exclusive(path: Path) -> int:
    """Open a file with O_CREAT | O_EXCL | O_WRONLY and 0600 permissions, rejecting symlinks."""
    if path.is_symlink():
        raise RuntimeError(f"Unsafe staging path: {path} is a symlink!")
    flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    return os.open(str(path), flags, 0o600)


def _fsync_parent_dir(path: Path) -> None:
    """Durable directory sync for atomic filesystem operations."""
    try:
        dir_fd = os.open(str(path.parent), os.O_RDONLY)
        try:
            os.fsync(dir_fd)
        finally:
            os.close(dir_fd)
    except Exception:
        pass


def validate_db_path(db_path: Path) -> None:
    """Assert database file exists and is non-empty before any operations."""
    if not db_path.is_file():
        raise FileNotFoundError(f"Database file missing or not a regular file: {db_path}")
    if db_path.stat().st_size == 0:
        raise ValueError(f"Database file is 0 bytes (uninitialized SQLite file): {db_path}")


def acquire_exclusive_maintenance_lock(db_path: Path) -> int:
    """Acquire an exclusive advisory maintenance lock on caseworker_maintenance.lock."""
    lock_file = db_path.parent / "caseworker_maintenance.lock"
    lock_fd = os.open(str(lock_file), os.O_CREAT | os.O_RDWR, 0o600)
    try:
        fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except (BlockingIOError, OSError) as e:
        os.close(lock_fd)
        raise RuntimeError(
            f"MAINTENANCE ISOLATION FAILED: Another migration or maintenance process holds the exclusive lock on {lock_file}"
        ) from e
    return lock_fd


def release_exclusive_maintenance_lock(lock_fd: int) -> None:
    """Release exclusive maintenance lock."""
    try:
        fcntl.flock(lock_fd, fcntl.LOCK_UN)
        os.close(lock_fd)
    except Exception:
        pass


def assert_maintenance_isolation(db_path: Path, timeout_seconds: float = 2.0) -> None:
    """Verify that no active writers or open transactions are touching the database (PRAGMA wal_checkpoint TRUNCATE)."""
    validate_db_path(db_path)
    conn = sqlite3.connect(f"file:{db_path.resolve()}?mode=rw", uri=True, timeout=timeout_seconds)
    try:
        cur = conn.cursor()
        cur.execute("PRAGMA busy_timeout = 2000;")
        cur.execute("PRAGMA locking_mode = EXCLUSIVE;")
        cur.execute("PRAGMA wal_checkpoint(TRUNCATE);")
        res = cur.fetchone()
        # res is (busy_flag, log_size, checkpointed_size)
        if res and res[0] != 0:
            raise RuntimeError(
                f"MAINTENANCE ISOLATION FAILED: Active database writer or uncheckpointed WAL frames detected: {res}. "
                "Ensure caseworker-api container is stopped before maintenance."
            )
    finally:
        conn.close()


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


def update_secret_in_env_file(
    env_file_path: Path,
    key_name: str,
    new_value: str,
    fallback_key_name: str = "",
    fallback_value: str = "",
) -> None:
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
    fd = os.open(str(temp_path), os.O_CREAT | os.O_WRONLY | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.writelines(new_lines)
        f.flush()
        os.fsync(f.fileno())

    os.replace(temp_path, env_file_path)
    _fsync_parent_dir(env_file_path)


def init_migration_state_table(conn: sqlite3.Connection) -> None:
    """Initialize credential_migration_state table in SQLite database."""
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS credential_migration_state (
            migration_id TEXT PRIMARY KEY,
            state TEXT NOT NULL,
            old_key_fingerprint TEXT NOT NULL,
            new_key_fingerprint TEXT,
            backup_db_path TEXT,
            backup_env_path TEXT,
            recovery_manifest_path TEXT,
            started_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
    """)
    conn.commit()


def assert_governance_domain_invariants(conn: sqlite3.Connection) -> None:
    """Verify that required Stage A/B governance records exist and zero attempts/receipts exist."""
    cur = conn.cursor()

    # Case check
    cur.execute("SELECT case_id, status FROM cases WHERE case_id = ?", (CONTROLLED_CASE_ID,))
    case_row = cur.fetchone()
    if not case_row or case_row[1] != "action_required":
        raise RuntimeError(f"Governance invariant violation: Case '{CONTROLLED_CASE_ID}' is missing or not 'action_required': {case_row}")

    # Plan check
    cur.execute(
        "SELECT plan_id, status, execution_plan_fingerprint FROM external_execution_plans WHERE plan_id = ?",
        (CONTROLLED_PLAN_ID,),
    )
    plan_row = cur.fetchone()
    if not plan_row or plan_row[1] != "ready_for_authorization" or plan_row[2] != CONTROLLED_PLAN_FINGERPRINT:
        raise RuntimeError(f"Governance invariant violation: Plan '{CONTROLLED_PLAN_ID}' invalid: {plan_row}")

    # Action check
    cur.execute("SELECT action_id, status FROM actions WHERE action_id = ?", (CONTROLLED_ACTION_ID,))
    act_row = cur.fetchone()
    if not act_row or act_row[1] != "approved":
        raise RuntimeError(f"Governance invariant violation: Action '{CONTROLLED_ACTION_ID}' is not 'approved': {act_row}")

    # Approval check
    cur.execute("SELECT approval_id, status FROM approvals WHERE approval_id = ?", (CONTROLLED_APPROVAL_ID,))
    app_row = cur.fetchone()
    if not app_row or app_row[1] != "approved":
        raise RuntimeError(f"Governance invariant violation: Approval '{CONTROLLED_APPROVAL_ID}' is not 'approved': {app_row}")

    # Attempts count
    cur.execute("SELECT count(*) FROM execution_attempts WHERE action_id = ?", (CONTROLLED_ACTION_ID,))
    att_count = cur.fetchone()[0]
    if att_count > 0:
        raise RuntimeError(f"Governance invariant violation: {att_count} execution attempt(s) already recorded for Action '{CONTROLLED_ACTION_ID}'!")

    # Receipts count
    cur.execute("SELECT count(*) FROM execution_receipts WHERE action_id = ?", (CONTROLLED_ACTION_ID,))
    rcpt_count = cur.fetchone()[0]
    if rcpt_count > 0:
        raise RuntimeError(f"Governance invariant violation: {rcpt_count} execution receipt(s) already recorded for Action '{CONTROLLED_ACTION_ID}'!")

    # OAuth credentials check
    cur.execute(
        "SELECT user_id, provider, authenticated_email FROM user_oauth_credentials WHERE user_id = ? AND provider = 'gmail'",
        (CONTROLLED_USER_ID,),
    )
    oauth_row = cur.fetchone()
    if not oauth_row or oauth_row[2] != EXPECTED_DESTINATION_EMAIL:
        raise RuntimeError(f"Governance invariant violation: Missing or invalid OAuth credentials for user '{CONTROLLED_USER_ID}': {oauth_row}")


def get_migration_state(db_path: Path, env_file_path: Path, staging_file: Path) -> dict:
    """Inspect durable migration state distinguishing NOT_STARTED, STAGED, DATABASE_UPDATED, ENVIRONMENT_UPDATED, COMPLETED, and RECOVERY_REQUIRED."""
    validate_db_path(db_path)
    conn = sqlite3.connect(f"file:{db_path.resolve()}?mode=rw", uri=True)
    try:
        init_migration_state_table(conn)
        cur = conn.cursor()
        cur.execute("SELECT migration_id, state, old_key_fingerprint, new_key_fingerprint, backup_db_path, backup_env_path, recovery_manifest_path FROM credential_migration_state ORDER BY rowid DESC LIMIT 1;")
        db_state_row = cur.fetchone()

        cur.execute("SELECT user_id, provider, access_token_enc, refresh_token_enc FROM user_oauth_credentials")
        cred_rows = cur.fetchall()
    finally:
        conn.close()

    current_env_key = load_secret_from_env_file(env_file_path, "CASEWORKER_ENCRYPTION_KEY")
    current_key_fp = _key_fingerprint(current_env_key)

    # Helper to test full decryption
    def can_decrypt_all(key: str) -> bool:
        if not key or len(key) < 32 or not cred_rows:
            return False
        try:
            cipher = TokenCipher(primary_key=key)
            for r in cred_rows:
                dec_at = cipher.decrypt(r[2])
                dec_rt = cipher.decrypt(r[3])
                if not dec_at or not dec_rt:
                    return False
            return True
        except Exception:
            return False

    # Check staging file existence and properties
    if staging_file.is_file():
        if staging_file.is_symlink():
            return {"state": STATE_RECOVERY_REQUIRED, "reason": "Staging file is an unsafe symlink."}
        # Check permissions
        if staging_file.stat().st_mode & 0o077 != 0:
            return {"state": STATE_RECOVERY_REQUIRED, "reason": "Staging file permissions are broader than 0600."}

        staged_data = {}
        with open(staging_file, "r", encoding="utf-8") as f:
            for line in f:
                if "=" in line:
                    k, v = line.strip().split("=", 1)
                    staged_data[k] = v

        staged_new_key = staged_data.get("STAGED_NEW_KEY", "")
        old_key = staged_data.get("OLD_KEY_FOR_ROLLBACK", "")
        migration_id = staged_data.get("MIGRATION_ID", "")

        db_decrypts_old = can_decrypt_all(old_key)
        db_decrypts_new = can_decrypt_all(staged_new_key)
        env_has_old = (current_env_key == old_key)
        env_has_new = (current_env_key == staged_new_key)

        if db_decrypts_old and env_has_old:
            return {
                "state": STATE_STAGED,
                "migration_id": migration_id,
                "staged_data": staged_data,
                "reason": "Staging file exists; database and environment are both on old key (uncommitted).",
            }
        elif db_decrypts_new and env_has_old:
            return {
                "state": STATE_DATABASE_UPDATED,
                "migration_id": migration_id,
                "staged_data": staged_data,
                "reason": "Database is updated with new key; environment file still on old key.",
            }
        elif db_decrypts_new and env_has_new:
            return {
                "state": STATE_ENVIRONMENT_UPDATED,
                "migration_id": migration_id,
                "staged_data": staged_data,
                "reason": "Database and environment are both updated with new key; staging file not yet removed.",
            }
        else:
            return {
                "state": STATE_RECOVERY_REQUIRED,
                "migration_id": migration_id,
                "reason": f"Inconsistent keys in database/environment (db_old={db_decrypts_old}, db_new={db_decrypts_new}, env_old={env_has_old}, env_new={env_has_new}).",
            }

    # No staging file exists
    if not db_state_row:
        # Table exists but has no records
        if can_decrypt_all(current_env_key):
            return {
                "state": STATE_NOT_STARTED,
                "reason": "Initial operational state: no migrations recorded, database decrypts with configured key.",
            }
        else:
            return {
                "state": STATE_RECOVERY_REQUIRED,
                "reason": "No migration records found, but database credentials fail decryption with current env key.",
            }

    m_id, m_state, m_old_fp, m_new_fp, b_db, b_env, b_man = db_state_row

    # Reset or aborted states evaluate to NOT_STARTED if database decrypts with current configured key
    if m_state in (STATE_NOT_STARTED, "ABORTED"):
        if can_decrypt_all(current_env_key):
            return {
                "state": STATE_NOT_STARTED,
                "migration_id": m_id,
                "reason": "Migration state reset to NOT_STARTED; database decrypts with configured key.",
            }
        else:
            return {
                "state": STATE_RECOVERY_REQUIRED,
                "migration_id": m_id,
                "reason": f"Durable record indicates '{m_state}', but database credentials fail decryption with configured key.",
            }

    if m_state == STATE_COMPLETED:
        if current_key_fp == m_new_fp and can_decrypt_all(current_env_key):
            return {
                "state": STATE_COMPLETED,
                "migration_id": m_id,
                "new_key_fingerprint": m_new_fp,
                "reason": "Migration completed successfully and verified for current key.",
            }
        else:
            return {
                "state": STATE_RECOVERY_REQUIRED,
                "migration_id": m_id,
                "reason": "Database record indicates COMPLETED, but current environment key or decryption verification failed.",
            }

    # Incomplete database state record without staging file
    return {
        "state": STATE_RECOVERY_REQUIRED,
        "migration_id": m_id,
        "reason": f"Durable record indicates interrupted state '{m_state}' without matching staging file.",
    }


def create_verified_online_backup(
    db_path: Path,
    backup_dir: Path,
    env_file_path: Path,
    current_key: str,
    migration_id: str,
) -> tuple[Path, Path, Path]:
    """Create a transactionally consistent, matching recovery set (DB backup, env backup, manifest) with PRAGMA integrity_check."""
    backup_dir.mkdir(parents=True, exist_ok=True)
    os.chmod(backup_dir, 0o700)

    timestamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%d_%H%M%S")
    backup_db_file = backup_dir / f"caseworker_backup_{timestamp}_{migration_id[:8]}.db"
    backup_env_file = backup_dir / f"env_caseworker_api_local_{timestamp}_{migration_id[:8]}.bak"
    manifest_file = backup_dir / f"recovery_manifest_{timestamp}_{migration_id[:8]}.json"

    # 1. SQLite Online Backup API (handles WAL)
    src_conn = sqlite3.connect(f"file:{db_path.resolve()}?mode=ro", uri=True)
    bck_conn = sqlite3.connect(str(backup_db_file))
    try:
        src_conn.backup(bck_conn)
    finally:
        src_conn.close()
        bck_conn.close()

    os.chmod(backup_db_file, 0o600)

    # 2. PRAGMA integrity_check on backup
    verify_conn = sqlite3.connect(str(backup_db_file))
    try:
        cur = verify_conn.cursor()
        cur.execute("PRAGMA integrity_check;")
        check_rows = cur.fetchall()
        if check_rows != [("ok",)]:
            raise RuntimeError(f"Backup SQLite integrity check failed: {check_rows}")

        # Assert domain invariants in backup
        assert_governance_domain_invariants(verify_conn)

        # Test in-memory restoration
        mem_conn = sqlite3.connect(":memory:")
        try:
            verify_conn.backup(mem_conn)
            m_cur = mem_conn.cursor()
            m_cur.execute("PRAGMA integrity_check;")
            if m_cur.fetchall() != [("ok",)]:
                raise RuntimeError("Test restore of backup into in-memory database failed integrity check.")
        finally:
            mem_conn.close()
    finally:
        verify_conn.close()

    # 3. Environment File Backup
    fd = os.open(str(backup_env_file), os.O_CREAT | os.O_WRONLY | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as out_f, open(env_file_path, "r", encoding="utf-8") as in_f:
        out_f.write(in_f.read())
        out_f.flush()
        os.fsync(out_f.fileno())

    # 4. Recovery Manifest File (matching pair metadata, zero secrets)
    db_sha = _file_sha256(backup_db_file)
    env_sha = _file_sha256(backup_env_file)
    manifest_data = {
        "migration_id": migration_id,
        "created_at": datetime.datetime.now(datetime.timezone.utc).isoformat().replace("+00:00", "Z"),
        "backup_db": {
            "path": str(backup_db_file.resolve()),
            "sha256": db_sha,
            "byte_size": backup_db_file.stat().st_size,
        },
        "backup_env": {
            "path": str(backup_env_file.resolve()),
            "sha256": env_sha,
            "byte_size": backup_env_file.stat().st_size,
        },
        "key_fingerprint": _key_fingerprint(current_key),
        "case_id": CONTROLLED_CASE_ID,
        "plan_id": CONTROLLED_PLAN_ID,
        "plan_fingerprint": CONTROLLED_PLAN_FINGERPRINT,
        "action_id": CONTROLLED_ACTION_ID,
        "approval_id": CONTROLLED_APPROVAL_ID,
    }

    m_fd = os.open(str(manifest_file), os.O_CREAT | os.O_WRONLY | os.O_TRUNC, 0o600)
    with os.fdopen(m_fd, "w", encoding="utf-8") as f:
        json.dump(manifest_data, f, indent=2)
        f.flush()
        os.fsync(f.fileno())

    _fsync_parent_dir(manifest_file)

    return backup_db_file, backup_env_file, manifest_file


def execute_migration(
    db_path: Path,
    env_file_path: Path,
    backup_dir: Path,
    staged_key: str | None = None,
    fault_inject_checkpoint: int = 0,
) -> dict:
    """Execute crash-recoverable migration workflow with strict isolation and state machine."""
    validate_db_path(db_path)
    if not env_file_path.is_file():
        raise FileNotFoundError(f"Environment file not found: {env_file_path}")

    staging_file = env_file_path.parent / ".env.encryption_key_staged.tmp"

    # Step 1: Exclusive maintenance lock
    lock_fd = acquire_exclusive_maintenance_lock(db_path)
    try:
        # Step 2: Quiescence / maintenance isolation assertion
        assert_maintenance_isolation(db_path)

        # Step 3: Migration state inspection
        state_info = get_migration_state(db_path, env_file_path, staging_file)
        current_state = state_info["state"]

        if current_state == STATE_COMPLETED:
            return {
                "status": "MIGRATION_ALREADY_COMPLETED",
                "message": state_info["reason"],
                "new_key_fingerprint": state_info.get("new_key_fingerprint"),
            }
        elif current_state != STATE_NOT_STARTED:
            raise RuntimeError(
                f"CANNOT MIGRATE from state '{current_state}': {state_info.get('reason')}. "
                "Run with --resume to recover or inspect recovery state."
            )

        current_key = load_secret_from_env_file(env_file_path, "CASEWORKER_ENCRYPTION_KEY")
        if not current_key or len(current_key) < 32:
            raise ValueError("Current CASEWORKER_ENCRYPTION_KEY is missing or invalid in env file.")

        old_cipher = TokenCipher(primary_key=current_key)

        # Step 4: Governance Domain Invariants Assertion
        conn = sqlite3.connect(f"file:{db_path.resolve()}?mode=rw", uri=True)
        try:
            assert_governance_domain_invariants(conn)
        finally:
            conn.close()

        migration_id = str(uuid.uuid4())
        now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat().replace("+00:00", "Z")

        # Step 5: Create Verified Matching Backup Set
        b_db, b_env, b_man = create_verified_online_backup(
            db_path=db_path,
            backup_dir=backup_dir,
            env_file_path=env_file_path,
            current_key=current_key,
            migration_id=migration_id,
        )

        # Step 6: Generate New Key & Staging Material
        new_key = staged_key or secrets.token_urlsafe(48)
        if len(new_key) < 48:
            raise ValueError("Generated key does not satisfy minimum 48 character length requirement.")

        new_cipher = TokenCipher(primary_key=new_key)

        # Create staging file exclusively (mode 0600)
        stg_fd = _create_secure_file_exclusive(staging_file)
        with os.fdopen(stg_fd, "w", encoding="utf-8") as f:
            f.write(
                f"MIGRATION_ID={migration_id}\n"
                f"STAGED_STATE={STATE_STAGED}\n"
                f"STAGED_NEW_KEY={new_key}\n"
                f"OLD_KEY_FOR_ROLLBACK={current_key}\n"
                f"BACKUP_DB={b_db}\n"
                f"BACKUP_ENV={b_env}\n"
                f"RECOVERY_MANIFEST={b_man}\n"
            )
            f.flush()
            os.fsync(f.fileno())
        _fsync_parent_dir(staging_file)

        # Record STAGED state in DB
        conn = sqlite3.connect(f"file:{db_path.resolve()}?mode=rw", uri=True)
        try:
            init_migration_state_table(conn)
            cur = conn.cursor()
            cur.execute(
                "INSERT INTO credential_migration_state (migration_id, state, old_key_fingerprint, new_key_fingerprint, backup_db_path, backup_env_path, recovery_manifest_path, started_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?);",
                (
                    migration_id,
                    STATE_STAGED,
                    _key_fingerprint(current_key),
                    _key_fingerprint(new_key),
                    str(b_db),
                    str(b_env),
                    str(b_man),
                    now_iso,
                    now_iso,
                ),
            )
            conn.commit()

            # Step 7: In-memory dual-token validation before commit
            cur.execute("SELECT user_id, provider, access_token_enc, refresh_token_enc FROM user_oauth_credentials")
            rows = cur.fetchall()

            reencrypted_rows = []
            for r in rows:
                u_id, prov, at_enc, rt_enc = r
                plain_at = old_cipher.decrypt(at_enc)
                plain_rt = old_cipher.decrypt(rt_enc)
                if not plain_at or not plain_rt:
                    raise RuntimeError("Decryption returned empty plaintext token.")

                new_at_enc = new_cipher.encrypt(plain_at)
                new_rt_enc = new_cipher.encrypt(plain_rt)

                # In-memory test decryption with new key BEFORE committing
                if new_cipher.decrypt(new_at_enc) != plain_at or new_cipher.decrypt(new_rt_enc) != plain_rt:
                    raise RuntimeError("Pre-commit validation failed: newly encrypted tokens do not decrypt to original plaintexts!")

                reencrypted_rows.append((new_at_enc, new_rt_enc, u_id, prov))

            # Fault-injection checkpoint 1: crash before DB commit
            if fault_inject_checkpoint == 1:
                raise RuntimeError("FAULT_INJECTION_CHECKPOINT_1: Crash before database commit.")

            # Step 8: Atomic SQLite Transaction
            now_iso2 = datetime.datetime.now(datetime.timezone.utc).isoformat().replace("+00:00", "Z")
            cur.execute("BEGIN IMMEDIATE;")
            for new_at, new_rt, u_id, prov in reencrypted_rows:
                cur.execute(
                    "UPDATE user_oauth_credentials SET access_token_enc = ?, refresh_token_enc = ?, updated_at = ? WHERE user_id = ? AND provider = ?;",
                    (new_at, new_rt, now_iso2, u_id, prov),
                )
            cur.execute(
                "UPDATE credential_migration_state SET state = ?, updated_at = ? WHERE migration_id = ?;",
                (STATE_DATABASE_UPDATED, now_iso2, migration_id),
            )
            conn.commit()

            # Verify committed DB rows decrypt with new key
            for _, _, u_id, prov in reencrypted_rows:
                cur.execute(
                    "SELECT access_token_enc, refresh_token_enc FROM user_oauth_credentials WHERE user_id = ? AND provider = ?",
                    (u_id, prov),
                )
                vr = cur.fetchone()
                if not vr or not new_cipher.decrypt(vr[0]) or not new_cipher.decrypt(vr[1]):
                    raise RuntimeError("Post-commit DB verification failed!")
        finally:
            conn.close()

        # Fault-injection checkpoint 2: crash after DB commit, before env file update
        if fault_inject_checkpoint == 2:
            raise RuntimeError("FAULT_INJECTION_CHECKPOINT_2: Crash after DB commit but before env replacement.")

        # Step 9: Atomic Environment File Replacement
        update_secret_in_env_file(
            env_file_path=env_file_path,
            key_name="CASEWORKER_ENCRYPTION_KEY",
            new_value=new_key,
            fallback_key_name="CASEWORKER_ENCRYPTION_FALLBACK_KEYS",
            fallback_value="",
        )

        # Fault-injection checkpoint 3: crash after env replacement before staging cleanup
        if fault_inject_checkpoint == 3:
            raise RuntimeError("FAULT_INJECTION_CHECKPOINT_3: Crash after env replacement before staging cleanup.")

        # Step 10: Post-Migration End-to-End Verification
        verify_env_key = load_secret_from_env_file(env_file_path, "CASEWORKER_ENCRYPTION_KEY")
        if verify_env_key != new_key:
            raise RuntimeError("Verification failed: env file key does not match new key!")

        # Verify all rows decrypt with verified env key
        verified_cipher = TokenCipher(primary_key=verify_env_key)
        conn = sqlite3.connect(f"file:{db_path.resolve()}?mode=rw", uri=True)
        try:
            cur = conn.cursor()
            cur.execute("SELECT access_token_enc, refresh_token_enc FROM user_oauth_credentials")
            for r in cur.fetchall():
                if not verified_cipher.decrypt(r[0]) or not verified_cipher.decrypt(r[1]):
                    raise RuntimeError("Verification failed: tokens in DB failed decryption with reloaded env key!")

            now_iso3 = datetime.datetime.now(datetime.timezone.utc).isoformat().replace("+00:00", "Z")
            cur.execute(
                "UPDATE credential_migration_state SET state = ?, updated_at = ? WHERE migration_id = ?;",
                (STATE_COMPLETED, now_iso3, migration_id),
            )
            conn.commit()
        finally:
            conn.close()

        # Step 11: Staging File Cleanup
        if staging_file.is_file():
            os.remove(staging_file)
            _fsync_parent_dir(staging_file)

        return {
            "status": "SUCCESS",
            "migration_id": migration_id,
            "backup_db": str(b_db),
            "backup_env": str(b_env),
            "manifest": str(b_man),
            "migrated_records": len(reencrypted_rows),
            "new_key_fingerprint": _key_fingerprint(new_key),
        }
    finally:
        release_exclusive_maintenance_lock(lock_fd)


def recover_or_resume(db_path: Path, env_file_path: Path, backup_dir: Path) -> dict:
    """Inspect migration state and safely forward-recover or rollback with dual-token validation."""
    validate_db_path(db_path)
    staging_file = env_file_path.parent / ".env.encryption_key_staged.tmp"

    lock_fd = acquire_exclusive_maintenance_lock(db_path)
    try:
        assert_maintenance_isolation(db_path)

        state_info = get_migration_state(db_path, env_file_path, staging_file)
        current_state = state_info["state"]

        if current_state == STATE_COMPLETED:
            return {"status": "ALREADY_COMPLETED", "message": "Database and environment are already COMPLETED."}

        elif current_state == STATE_NOT_STARTED:
            return {"status": "NOT_STARTED", "message": "No migration is in progress. Ready for --migrate."}

        elif current_state == STATE_STAGED:
            # Crash happened before DB commit: DB and env are both on old key
            staged_data = state_info.get("staged_data", {})
            migration_id = state_info.get("migration_id") or staged_data.get("MIGRATION_ID", "")
            old_key = staged_data.get("OLD_KEY_FOR_ROLLBACK", "") or load_secret_from_env_file(env_file_path, "CASEWORKER_ENCRYPTION_KEY")

            # Verify both credentials still decrypt using the old key
            old_cipher = TokenCipher(primary_key=old_key)
            conn = sqlite3.connect(f"file:{db_path.resolve()}?mode=rw", uri=True)
            try:
                cur = conn.cursor()
                cur.execute("SELECT access_token_enc, refresh_token_enc FROM user_oauth_credentials")
                rows = cur.fetchall()
                for r in rows:
                    if not old_cipher.decrypt(r[0]) or not old_cipher.decrypt(r[1]):
                        raise RuntimeError("Tokens in database do not decrypt with old key during STAGED recovery!")

                # Reset durable migration state row in database to NOT_STARTED (retaining audit history)
                now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat().replace("+00:00", "Z")
                if migration_id:
                    cur.execute(
                        "UPDATE credential_migration_state SET state = ?, updated_at = ? WHERE migration_id = ?;",
                        (STATE_NOT_STARTED, now_iso, migration_id),
                    )
                else:
                    cur.execute(
                        "UPDATE credential_migration_state SET state = ?, updated_at = ? WHERE state = ?;",
                        (STATE_NOT_STARTED, now_iso, STATE_STAGED),
                    )
                conn.commit()
            finally:
                conn.close()

            if staging_file.is_file():
                os.remove(staging_file)
                _fsync_parent_dir(staging_file)

            return {
                "status": "ABORTED_STAGING_CLEANED",
                "message": "Database was uncommitted. Verified old key decryption, reset state to NOT_STARTED, and cleaned staging file.",
            }

        elif current_state == STATE_DATABASE_UPDATED:
            # DB has new key, env has old key: forward-recover env file
            staged_data = state_info.get("staged_data", {})
            staged_new_key = staged_data.get("STAGED_NEW_KEY", "")
            migration_id = staged_data.get("MIGRATION_ID", "")
            if not staged_new_key or len(staged_new_key) < 32:
                return {
                    "status": "RECOVERY_REQUIRED",
                    "message": "Corrupt staged key in staging file. Manual recovery required.",
                }

            update_secret_in_env_file(env_file_path, "CASEWORKER_ENCRYPTION_KEY", staged_new_key)

            # Post-update verification
            verified_cipher = TokenCipher(primary_key=staged_new_key)
            conn = sqlite3.connect(f"file:{db_path.resolve()}?mode=rw", uri=True)
            try:
                cur = conn.cursor()
                cur.execute("SELECT access_token_enc, refresh_token_enc FROM user_oauth_credentials")
                for r in cur.fetchall():
                    if not verified_cipher.decrypt(r[0]) or not verified_cipher.decrypt(r[1]):
                        raise RuntimeError("Tokens failed decryption with staged key during forward recovery!")

                now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat().replace("+00:00", "Z")
                cur.execute(
                    "UPDATE credential_migration_state SET state = ?, updated_at = ? WHERE migration_id = ?;",
                    (STATE_COMPLETED, now_iso, migration_id),
                )
                conn.commit()
            finally:
                conn.close()

            if staging_file.is_file():
                os.remove(staging_file)
                _fsync_parent_dir(staging_file)

            return {
                "status": "FORWARD_RECOVERED",
                "message": "Environment file updated from staged key, verified, and completed.",
                "new_key_fingerprint": _key_fingerprint(staged_new_key),
            }

        elif current_state == STATE_ENVIRONMENT_UPDATED:
            # Both updated, just finalize
            staged_data = state_info.get("staged_data", {})
            staged_new_key = staged_data.get("STAGED_NEW_KEY", "")
            migration_id = staged_data.get("MIGRATION_ID", "")

            conn = sqlite3.connect(f"file:{db_path.resolve()}?mode=rw", uri=True)
            try:
                cur = conn.cursor()
                now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat().replace("+00:00", "Z")
                cur.execute(
                    "UPDATE credential_migration_state SET state = ?, updated_at = ? WHERE migration_id = ?;",
                    (STATE_COMPLETED, now_iso, migration_id),
                )
                conn.commit()
            finally:
                conn.close()

            if staging_file.is_file():
                os.remove(staging_file)
                _fsync_parent_dir(staging_file)

            return {
                "status": "CLEANED_STAGING",
                "message": "Migration verified and marked COMPLETED. Staging file cleaned.",
            }

        else:
            return {
                "status": "RECOVERY_REQUIRED",
                "message": f"Ambiguous state cannot be resolved automatically: {state_info.get('reason')}. Manual inspection required.",
            }
    finally:
        release_exclusive_maintenance_lock(lock_fd)


def restore_database_from_backup(
    backup_file: Path,
    target_db_path: Path,
    env_file_path: Path,
    confirm_backup_name: str,
) -> None:
    """Safely restore target database and environment from a verified matching backup set."""
    if not backup_file.is_file():
        raise FileNotFoundError(f"Database backup file not found: {backup_file}")

    if confirm_backup_name != backup_file.name:
        raise ValueError(
            f"Explicit confirmation mismatch: requested '{backup_file.name}', but --confirm-restore-backup='{confirm_backup_name}'"
        )

    # 1. Require matching manifest and environment backup to exist
    manifest_file = backup_file.parent / backup_file.name.replace("caseworker_backup_", "recovery_manifest_").replace(".db", ".json")
    env_backup_file = backup_file.parent / backup_file.name.replace("caseworker_backup_", "env_caseworker_api_local_").replace(".db", ".bak")

    if not manifest_file.is_file():
        raise FileNotFoundError(f"Matching recovery manifest file missing: {manifest_file}")
    if not env_backup_file.is_file():
        raise FileNotFoundError(f"Matching environment backup file missing: {env_backup_file}")

    # 2. Parse and verify recovery manifest
    try:
        with open(manifest_file, "r", encoding="utf-8") as f:
            manifest_data = json.load(f)
    except Exception as e:
        raise ValueError(f"Corrupt recovery manifest JSON in {manifest_file}: {e}") from e

    if manifest_data.get("case_id") != CONTROLLED_CASE_ID:
        raise ValueError(f"Manifest case_id mismatch: expected '{CONTROLLED_CASE_ID}', got '{manifest_data.get('case_id')}'")
    if manifest_data.get("plan_id") != CONTROLLED_PLAN_ID:
        raise ValueError(f"Manifest plan_id mismatch: expected '{CONTROLLED_PLAN_ID}', got '{manifest_data.get('plan_id')}'")
    if manifest_data.get("plan_fingerprint") != CONTROLLED_PLAN_FINGERPRINT:
        raise ValueError(f"Manifest plan_fingerprint mismatch: expected '{CONTROLLED_PLAN_FINGERPRINT}', got '{manifest_data.get('plan_fingerprint')}'")
    if manifest_data.get("action_id") != CONTROLLED_ACTION_ID:
        raise ValueError(f"Manifest action_id mismatch: expected '{CONTROLLED_ACTION_ID}', got '{manifest_data.get('action_id')}'")
    if manifest_data.get("approval_id") != CONTROLLED_APPROVAL_ID:
        raise ValueError(f"Manifest approval_id mismatch: expected '{CONTROLLED_APPROVAL_ID}', got '{manifest_data.get('approval_id')}'")

    # Verify both backup hashes and sizes against the manifest
    db_sha = _file_sha256(backup_file)
    expected_db_sha = manifest_data.get("backup_db", {}).get("sha256")
    expected_db_size = manifest_data.get("backup_db", {}).get("byte_size")
    if db_sha != expected_db_sha or backup_file.stat().st_size != expected_db_size:
        raise ValueError(
            f"Database backup hash or size mismatch! Expected sha={expected_db_sha}, size={expected_db_size}; "
            f"actual sha={db_sha}, size={backup_file.stat().st_size}"
        )

    env_sha = _file_sha256(env_backup_file)
    expected_env_sha = manifest_data.get("backup_env", {}).get("sha256")
    expected_env_size = manifest_data.get("backup_env", {}).get("byte_size")
    if env_sha != expected_env_sha or env_backup_file.stat().st_size != expected_env_size:
        raise ValueError(
            f"Environment backup hash or size mismatch! Expected sha={expected_env_sha}, size={expected_env_size}; "
            f"actual sha={env_sha}, size={env_backup_file.stat().st_size}"
        )

    # 3. Verify SQLite integrity and domain records of backup_file
    bck_conn = sqlite3.connect(f"file:{backup_file.resolve()}?mode=ro", uri=True)
    try:
        cur = bck_conn.cursor()
        cur.execute("PRAGMA integrity_check;")
        if cur.fetchall() != [("ok",)]:
            raise RuntimeError(f"Backup file failed SQLite integrity check: {backup_file}")

        assert_governance_domain_invariants(bck_conn)

        # 4. Verify that the backup environment key decrypts all backed-up OAuth credentials
        bck_env_key = load_secret_from_env_file(env_backup_file, "CASEWORKER_ENCRYPTION_KEY")
        if not bck_env_key or len(bck_env_key) < 32:
            raise ValueError("Backup environment file contains missing or invalid CASEWORKER_ENCRYPTION_KEY.")
        if _key_fingerprint(bck_env_key) != manifest_data.get("key_fingerprint"):
            raise ValueError("Backup environment key fingerprint does not match manifest key fingerprint.")

        bck_cipher = TokenCipher(primary_key=bck_env_key)
        cur.execute("SELECT access_token_enc, refresh_token_enc FROM user_oauth_credentials")
        bck_rows = cur.fetchall()
        if not bck_rows:
            raise RuntimeError("Backup database contains no OAuth credential records.")
        for r in bck_rows:
            try:
                at_dec = bck_cipher.decrypt(r[0])
                rt_dec = bck_cipher.decrypt(r[1])
                if not at_dec or not rt_dec:
                    raise RuntimeError("Backup environment key cannot decrypt backed-up OAuth credentials!")
            except Exception as e:
                raise RuntimeError(f"Backup environment key cannot decrypt backed-up OAuth credentials: {e}") from e
    finally:
        bck_conn.close()

    # 5. Maintenance isolation on target DB
    lock_fd = acquire_exclusive_maintenance_lock(target_db_path)
    try:
        assert_maintenance_isolation(target_db_path)

        # 6. Guard against erasing newer records in target DB
        tgt_conn = sqlite3.connect(f"file:{target_db_path.resolve()}?mode=rw", uri=True)
        try:
            t_cur = tgt_conn.cursor()
            t_cur.execute("SELECT count(*) FROM execution_attempts;")
            tgt_attempts = t_cur.fetchone()[0]
            t_cur.execute("SELECT count(*) FROM execution_receipts;")
            tgt_receipts = t_cur.fetchone()[0]
            if tgt_attempts > 0 or tgt_receipts > 0:
                raise RuntimeError(
                    f"RESTORE REFUSED: Target database contains {tgt_attempts} attempt(s) and {tgt_receipts} receipt(s). "
                    "Restoring an older backup would silently erase execution records!"
                )
            t_cur.execute("SELECT action_id, status FROM actions WHERE action_id = ?;", (CONTROLLED_ACTION_ID,))
            t_act = t_cur.fetchone()
            if t_act and t_act[1] in ("executing", "succeeded"):
                raise RuntimeError(f"RESTORE REFUSED: Target action status is '{t_act[1]}'. Restoring would erase execution state!")
        finally:
            tgt_conn.close()

        # 7. Perform restoration only after all validations pass
        src_conn = sqlite3.connect(f"file:{backup_file.resolve()}?mode=ro", uri=True)
        dst_conn = sqlite3.connect(f"file:{target_db_path.resolve()}?mode=rw", uri=True)
        try:
            src_conn.backup(dst_conn)
        finally:
            src_conn.close()
            dst_conn.close()

        # Atomically restore environment file
        temp_env = env_file_path.with_suffix(".restore_tmp")
        fd = os.open(str(temp_env), os.O_CREAT | os.O_WRONLY | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as out_f, open(env_backup_file, "r", encoding="utf-8") as in_f:
            out_f.write(in_f.read())
            out_f.flush()
            os.fsync(out_f.fileno())
        os.replace(temp_env, env_file_path)
        _fsync_parent_dir(env_file_path)

        # Clean lingering staging file
        staging_file = env_file_path.parent / ".env.encryption_key_staged.tmp"
        if staging_file.is_file():
            os.remove(staging_file)
            _fsync_parent_dir(staging_file)

    finally:
        release_exclusive_maintenance_lock(lock_fd)


def main() -> int:
    parser = argparse.ArgumentParser(description="Crash-recoverable OAuth encryption key rotation utility for JackVerse")
    parser.add_argument("--db-path", default="workspace/caseworker.db", help="Path to SQLite database")
    parser.add_argument("--env-file", default=".env.caseworker-api.local", help="Path to local environment file")
    parser.add_argument("--backup-dir", default="workspace/backups", help="Directory to store verified backups")
    parser.add_argument("--check", action="store_true", help="Check current encryption and migration state without mutating")
    parser.add_argument("--migrate", action="store_true", help="Execute the crash-recoverable migration workflow")
    parser.add_argument("--resume", action="store_true", help="Check for interrupted staging state and recover")
    parser.add_argument("--restore-backup", type=str, default="", help="Restore database from specified verified backup file")
    parser.add_argument("--confirm-restore-backup", type=str, default="", help="Explicit confirmation matching backup basename")

    args = parser.parse_args()

    db_path = Path(args.db_path)
    env_file = Path(args.env_file)
    backup_dir = Path(args.backup_dir)
    staging_file = env_file.parent / ".env.encryption_key_staged.tmp"

    if args.check:
        print("=== OAUTH ENCRYPTION AND MIGRATION AUDIT ===")
        print(f"Database path:         {db_path}")
        print(f"Environment file:      {env_file}")
        try:
            validate_db_path(db_path)
            state_info = get_migration_state(db_path, env_file, staging_file)
            print(f"Migration state:       {state_info['state']}")
            print(f"State details:         {state_info.get('reason', '')}")
            if state_info.get("new_key_fingerprint"):
                print(f"Key fingerprint:       {state_info['new_key_fingerprint']}")

            current_key = load_secret_from_env_file(env_file, "CASEWORKER_ENCRYPTION_KEY")
            cipher = TokenCipher(primary_key=current_key) if len(current_key) >= 32 else None
            conn = sqlite3.connect(f"file:{db_path.resolve()}?mode=ro", uri=True)
            try:
                cur = conn.cursor()
                cur.execute("SELECT user_id, provider, authenticated_email, access_token_enc, refresh_token_enc FROM user_oauth_credentials")
                rows = cur.fetchall()
            finally:
                conn.close()

            print(f"Records count:         {len(rows)}")
            all_ok = True
            for r in rows:
                u, p, em, at, rt = r
                at_ok = bool(cipher and cipher.decrypt(at))
                rt_ok = bool(cipher and cipher.decrypt(rt))
                print(f"  - User: {u}, Provider: {p}, Email: {em}, Access Decrypt: {at_ok}, Refresh Decrypt: {rt_ok}")
                if not (at_ok and rt_ok):
                    all_ok = False
            return 0 if (all_ok and state_info["state"] in (STATE_NOT_STARTED, STATE_COMPLETED)) else 1
        except Exception as e:
            print(f"[FAIL] Audit error: {e}")
            return 1

    elif args.restore_backup:
        bck_path = Path(args.restore_backup)
        print(f"=== RESTORING DATABASE FROM BACKUP: {bck_path} ===")
        try:
            restore_database_from_backup(
                backup_file=bck_path,
                target_db_path=db_path,
                env_file_path=env_file,
                confirm_backup_name=args.confirm_restore_backup,
            )
            print(f"Successfully restored {db_path} from verified backup.")
            return 0
        except Exception as e:
            print(f"[FAIL] Restore failed: {e}")
            return 1

    elif args.resume:
        print("=== RESUMING / RECOVERING MIGRATION STATE ===")
        try:
            res = recover_or_resume(db_path, env_file, backup_dir)
            print(f"Recovery status: {res['status']}")
            print(f"Details:         {res['message']}")
            return 0 if res["status"] in ("ALREADY_COMPLETED", "FORWARD_RECOVERED", "CLEANED_STAGING", "ABORTED_STAGING_CLEANED", "NOT_STARTED") else 1
        except Exception as e:
            print(f"[FAIL] Recovery error: {e}")
            return 1

    elif args.migrate:
        print("=== EXECUTING OAUTH ENCRYPTION KEY MIGRATION ===")
        try:
            res = execute_migration(db_path, env_file, backup_dir)
            print(f"Migration status:      {res['status']}")
            if res["status"] == "SUCCESS":
                print(f"Migration ID:          {res['migration_id']}")
                print(f"Verified DB backup:    {res['backup_db']}")
                print(f"Verified env backup:   {res['backup_env']}")
                print(f"Recovery manifest:     {res['manifest']}")
                print(f"Migrated records:      {res['migrated_records']}")
                print(f"New key fingerprint:   {res['new_key_fingerprint']}")
                return 0
            elif res["status"] == "MIGRATION_ALREADY_COMPLETED":
                print(f"Notice:                {res['message']}")
                print(f"Key fingerprint:       {res.get('new_key_fingerprint')}")
                return 0
            else:
                return 1
        except Exception as e:
            print(f"[FAIL] Migration failed: {e}")
            return 1

    else:
        parser.print_help()
        return 0


if __name__ == "__main__":
    sys.exit(main())
