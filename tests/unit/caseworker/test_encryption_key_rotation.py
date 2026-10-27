"""Unit, boundary, and fault-injection tests for crash-recoverable OAuth encryption key rotation."""

from __future__ import annotations

import os
from pathlib import Path
import secrets
import shutil
import sqlite3
import tempfile
import pytest

from caseworker.adapters.crypto.token_cipher import TokenCipher
from scripts.rotate_oauth_encryption_key import (
    CONTROLLED_ACTION_ID,
    CONTROLLED_APPROVAL_ID,
    CONTROLLED_CASE_ID,
    CONTROLLED_PLAN_ID,
    CONTROLLED_PLAN_FINGERPRINT,
    CONTROLLED_USER_ID,
    STATE_NOT_STARTED,
    STATE_STAGED,
    STATE_DATABASE_UPDATED,
    STATE_ENVIRONMENT_UPDATED,
    STATE_COMPLETED,
    STATE_RECOVERY_REQUIRED,
    acquire_exclusive_maintenance_lock,
    assert_maintenance_isolation,
    create_verified_online_backup,
    execute_migration,
    get_migration_state,
    load_secret_from_env_file,
    recover_or_resume,
    release_exclusive_maintenance_lock,
    restore_database_from_backup,
    update_secret_in_env_file,
    validate_db_path,
    _create_secure_file_exclusive,
)


def _setup_test_db(
    db_path: Path,
    cipher: TokenCipher,
    access_token: str = "ya29.initial_test_token",
    refresh_token: str = "1//initial_refresh_token",
) -> None:
    """Create schema and populate test database with required controlled entities."""
    conn = sqlite3.connect(str(db_path))
    cur = conn.cursor()

    cur.execute("""
        CREATE TABLE cases (
            case_id TEXT PRIMARY KEY,
            case_type TEXT NOT NULL,
            status TEXT NOT NULL,
            title TEXT,
            description TEXT,
            metadata TEXT,
            user_id TEXT NOT NULL DEFAULT 'operator-1',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
    """)

    cur.execute("""
        CREATE TABLE external_execution_plans (
            plan_id TEXT PRIMARY KEY,
            case_id TEXT NOT NULL,
            action_id TEXT NOT NULL,
            destination_type TEXT NOT NULL,
            status TEXT NOT NULL,
            plan_hash TEXT NOT NULL,
            execution_plan_fingerprint TEXT NOT NULL,
            parameters TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
    """)

    cur.execute("""
        CREATE TABLE actions (
            action_id TEXT PRIMARY KEY,
            case_id TEXT NOT NULL,
            action_type TEXT NOT NULL,
            status TEXT NOT NULL,
            version INTEGER NOT NULL DEFAULT 1,
            description TEXT,
            parameters TEXT,
            fingerprint TEXT NOT NULL DEFAULT 'e16f0e85ba41ddd43dd5401c7c0c65fd0b2f0f4172b1f22a77024dafb0f0ec86',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
    """)

    cur.execute("""
        CREATE TABLE approvals (
            approval_id TEXT PRIMARY KEY,
            case_id TEXT NOT NULL,
            action_id TEXT NOT NULL,
            status TEXT NOT NULL,
            decision TEXT,
            action_fingerprint TEXT NOT NULL DEFAULT 'e16f0e85ba41ddd43dd5401c7c0c65fd0b2f0f4172b1f22a77024dafb0f0ec86',
            decided_at TEXT,
            decided_by TEXT,
            comments TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
    """)

    cur.execute("""
        CREATE TABLE user_oauth_credentials (
            user_id TEXT NOT NULL,
            provider TEXT NOT NULL,
            authenticated_email TEXT NOT NULL,
            access_token_enc TEXT NOT NULL,
            refresh_token_enc TEXT NOT NULL,
            token_uri TEXT NOT NULL,
            client_id TEXT NOT NULL,
            client_secret_enc TEXT NOT NULL,
            scopes TEXT NOT NULL,
            expiry TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            PRIMARY KEY (user_id, provider)
        );
    """)

    cur.execute("""
        CREATE TABLE execution_attempts (
            attempt_id TEXT PRIMARY KEY,
            action_id TEXT NOT NULL,
            plan_id TEXT NOT NULL,
            attempt_number INTEGER NOT NULL,
            status TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
    """)

    cur.execute("""
        CREATE TABLE execution_receipts (
            receipt_id TEXT PRIMARY KEY,
            action_id TEXT NOT NULL,
            plan_id TEXT NOT NULL,
            status TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
    """)

    now_iso = "2026-10-27T12:00:00Z"
    cur.execute(
        "INSERT INTO cases (case_id, case_type, status, user_id, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
        (CONTROLLED_CASE_ID, "opportunity_application", "action_required", CONTROLLED_USER_ID, now_iso, now_iso),
    )

    cur.execute(
        "INSERT INTO external_execution_plans (plan_id, case_id, action_id, destination_type, status, plan_hash, execution_plan_fingerprint, parameters, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (CONTROLLED_PLAN_ID, CONTROLLED_CASE_ID, CONTROLLED_ACTION_ID, "gmail", "ready_for_authorization", "hash-plan-test", CONTROLLED_PLAN_FINGERPRINT, "{}", now_iso, now_iso),
    )

    cur.execute(
        "INSERT INTO actions (action_id, case_id, action_type, status, version, fingerprint, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (CONTROLLED_ACTION_ID, CONTROLLED_CASE_ID, "submit_form", "approved", 3, "e16f0e85ba41ddd43dd5401c7c0c65fd0b2f0f4172b1f22a77024dafb0f0ec86", now_iso, now_iso),
    )

    cur.execute(
        "INSERT INTO approvals (approval_id, case_id, action_id, status, decision, action_fingerprint, decided_at, decided_by, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (CONTROLLED_APPROVAL_ID, CONTROLLED_CASE_ID, CONTROLLED_ACTION_ID, "approved", "APPROVED", "e16f0e85ba41ddd43dd5401c7c0c65fd0b2f0f4172b1f22a77024dafb0f0ec86", now_iso, "operator-1", now_iso, now_iso),
    )

    at_enc = cipher.encrypt(access_token)
    rt_enc = cipher.encrypt(refresh_token)
    sec_enc = cipher.encrypt("mock-client-secret")
    cur.execute(
        "INSERT INTO user_oauth_credentials (user_id, provider, authenticated_email, access_token_enc, refresh_token_enc, token_uri, client_id, client_secret_enc, scopes, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (CONTROLLED_USER_ID, "gmail", "dagogograntjack@gmail.com", at_enc, rt_enc, "https://oauth2.googleapis.com/token", "client-id-123", sec_enc, "https://www.googleapis.com/auth/gmail.send", now_iso, now_iso),
    )

    conn.commit()
    conn.close()


@pytest.fixture
def test_env(tmp_path: Path):
    """Set up temporary directory with database, env file, and initial encryption key."""
    db_path = tmp_path / "caseworker.db"
    env_file = tmp_path / ".env.caseworker-api.local"
    backup_dir = tmp_path / "backups"

    initial_key = secrets.token_urlsafe(48)
    env_file.write_text(f"CASEWORKER_ENCRYPTION_KEY={initial_key}\nCASEWORKER_ENCRYPTION_FALLBACK_KEYS=\n", encoding="utf-8")
    os.chmod(env_file, 0o600)

    cipher = TokenCipher(primary_key=initial_key)
    _setup_test_db(db_path, cipher)

    return {
        "tmp_path": tmp_path,
        "db_path": db_path,
        "env_file": env_file,
        "backup_dir": backup_dir,
        "initial_key": initial_key,
        "cipher": cipher,
    }


def test_validate_db_path_rejects_missing_and_zero_byte(tmp_path: Path):
    """validate_db_path strictly rejects non-existent and 0-byte database paths."""
    missing = tmp_path / "non_existent.db"
    with pytest.raises(FileNotFoundError):
        validate_db_path(missing)

    zero_byte = tmp_path / "empty.db"
    zero_byte.touch()
    with pytest.raises(ValueError, match="0 bytes"):
        validate_db_path(zero_byte)


def test_create_secure_file_exclusive(tmp_path: Path):
    """_create_secure_file_exclusive enforces 0600 permissions, O_EXCL, and rejects symlinks."""
    target = tmp_path / "secret.tmp"
    fd = _create_secure_file_exclusive(target)
    os.close(fd)
    assert (target.stat().st_mode & 0o777) == 0o600

    # Calling again raises FileExistsError due to O_EXCL
    with pytest.raises(FileExistsError):
        _create_secure_file_exclusive(target)

    # Reject symlinks
    symlink_target = tmp_path / "symlink_secret.tmp"
    symlink_target.symlink_to(target)
    with pytest.raises(RuntimeError, match="symlink"):
        _create_secure_file_exclusive(symlink_target)


def test_migration_state_lifecycle(test_env):
    """Verify state transition from NOT_STARTED to COMPLETED, and refusal of silent re-rotation."""
    db_path = test_env["db_path"]
    env_file = test_env["env_file"]
    backup_dir = test_env["backup_dir"]
    staging_file = env_file.parent / ".env.encryption_key_staged.tmp"

    # Step 1: Initial state is NOT_STARTED
    state = get_migration_state(db_path, env_file, staging_file)
    assert state["state"] == STATE_NOT_STARTED

    # Step 2: Execute migration
    res = execute_migration(db_path=db_path, env_file_path=env_file, backup_dir=backup_dir)
    assert res["status"] == "SUCCESS"
    assert res["migrated_records"] == 1

    # Step 3: State is now COMPLETED
    state_after = get_migration_state(db_path, env_file, staging_file)
    assert state_after["state"] == STATE_COMPLETED

    # Step 4: Re-running execute_migration returns MIGRATION_ALREADY_COMPLETED and refuses rotation
    re_run = execute_migration(db_path=db_path, env_file_path=env_file, backup_dir=backup_dir)
    assert re_run["status"] == "MIGRATION_ALREADY_COMPLETED"


def test_exclusive_maintenance_lock(test_env):
    """Verify exclusive maintenance lock prevents concurrent processes from mutating."""
    db_path = test_env["db_path"]

    fd1 = acquire_exclusive_maintenance_lock(db_path)
    try:
        # Second lock attempt must fail
        with pytest.raises(RuntimeError, match="MAINTENANCE ISOLATION FAILED"):
            acquire_exclusive_maintenance_lock(db_path)
    finally:
        release_exclusive_maintenance_lock(fd1)

    # After release, locking succeeds again
    fd2 = acquire_exclusive_maintenance_lock(db_path)
    release_exclusive_maintenance_lock(fd2)


def test_dual_token_verification(test_env):
    """Verify that both access_token_enc and refresh_token_enc are validated."""
    db_path = test_env["db_path"]
    env_file = test_env["env_file"]
    backup_dir = test_env["backup_dir"]

    # Corrupt refresh token in database
    conn = sqlite3.connect(str(db_path))
    cur = conn.cursor()
    cur.execute("UPDATE user_oauth_credentials SET refresh_token_enc = 'corrupt-cipher-token'")
    conn.commit()
    conn.close()

    # Migration must fail pre-flight validation
    with pytest.raises(Exception):
        execute_migration(db_path=db_path, env_file_path=env_file, backup_dir=backup_dir)


def test_fault_injection_checkpoint_1_crash_before_db_commit(test_env):
    """Crash before DB commit leaves DB and env file on old key; resume cleans up safely."""
    db_path = test_env["db_path"]
    env_file = test_env["env_file"]
    backup_dir = test_env["backup_dir"]
    initial_key = test_env["initial_key"]

    with pytest.raises(RuntimeError, match="FAULT_INJECTION_CHECKPOINT_1"):
        execute_migration(db_path=db_path, env_file_path=env_file, backup_dir=backup_dir, fault_inject_checkpoint=1)

    # Database still decrypts with original key
    cipher_old = TokenCipher(primary_key=initial_key)
    conn = sqlite3.connect(str(db_path))
    cur = conn.cursor()
    cur.execute("SELECT access_token_enc, refresh_token_enc FROM user_oauth_credentials WHERE user_id = ?", (CONTROLLED_USER_ID,))
    at_enc, rt_enc = cur.fetchone()
    conn.close()
    assert cipher_old.decrypt(at_enc) == "ya29.initial_test_token"
    assert cipher_old.decrypt(rt_enc) == "1//initial_refresh_token"

    # Resume cleans up aborted staging
    recovery_res = recover_or_resume(db_path, env_file, backup_dir)
    assert recovery_res["status"] == "ABORTED_STAGING_CLEANED"
    staged_key_file = env_file.parent / ".env.encryption_key_staged.tmp"
    assert not staged_key_file.exists()


def test_fault_injection_checkpoint_2_crash_after_db_commit_before_env_update(test_env):
    """Crash after DB commit but before env file update is forward-recovered via resume."""
    db_path = test_env["db_path"]
    env_file = test_env["env_file"]
    backup_dir = test_env["backup_dir"]
    initial_key = test_env["initial_key"]

    new_key = secrets.token_urlsafe(48)
    with pytest.raises(RuntimeError, match="FAULT_INJECTION_CHECKPOINT_2"):
        execute_migration(db_path=db_path, env_file_path=env_file, backup_dir=backup_dir, staged_key=new_key, fault_inject_checkpoint=2)

    # State: DB committed with new_key, but env file still has initial_key
    assert load_secret_from_env_file(env_file, "CASEWORKER_ENCRYPTION_KEY") == initial_key
    cipher_new = TokenCipher(primary_key=new_key)
    conn = sqlite3.connect(str(db_path))
    cur = conn.cursor()
    cur.execute("SELECT access_token_enc, refresh_token_enc FROM user_oauth_credentials WHERE user_id = ?", (CONTROLLED_USER_ID,))
    at_enc, rt_enc = cur.fetchone()
    conn.close()
    assert cipher_new.decrypt(at_enc) == "ya29.initial_test_token"
    assert cipher_new.decrypt(rt_enc) == "1//initial_refresh_token"

    # Forward recovery completes env replacement and marks COMPLETED
    rec = recover_or_resume(db_path, env_file, backup_dir)
    assert rec["status"] == "FORWARD_RECOVERED"
    assert load_secret_from_env_file(env_file, "CASEWORKER_ENCRYPTION_KEY") == new_key

    staged_key_file = env_file.parent / ".env.encryption_key_staged.tmp"
    assert not staged_key_file.exists()

    state = get_migration_state(db_path, env_file, staged_key_file)
    assert state["state"] == STATE_COMPLETED


def test_fault_injection_checkpoint_3_crash_after_env_replacement(test_env):
    """Crash after env replacement before staging cleanup leaves staging file; resume cleans it and marks COMPLETED."""
    db_path = test_env["db_path"]
    env_file = test_env["env_file"]
    backup_dir = test_env["backup_dir"]

    new_key = secrets.token_urlsafe(48)
    with pytest.raises(RuntimeError, match="FAULT_INJECTION_CHECKPOINT_3"):
        execute_migration(db_path=db_path, env_file_path=env_file, backup_dir=backup_dir, staged_key=new_key, fault_inject_checkpoint=3)

    assert load_secret_from_env_file(env_file, "CASEWORKER_ENCRYPTION_KEY") == new_key
    staged_key_file = env_file.parent / ".env.encryption_key_staged.tmp"
    assert staged_key_file.is_file()

    rec = recover_or_resume(db_path, env_file, backup_dir)
    assert rec["status"] == "CLEANED_STAGING"
    assert not staged_key_file.exists()

    state = get_migration_state(db_path, env_file, staged_key_file)
    assert state["state"] == STATE_COMPLETED


def test_restore_database_from_backup_guardrails(test_env):
    """Verify that restore_database_from_backup requires explicit confirmation and protects newer execution records."""
    db_path = test_env["db_path"]
    env_file = test_env["env_file"]
    backup_dir = test_env["backup_dir"]
    initial_key = test_env["initial_key"]

    b_db, b_env, b_man = create_verified_online_backup(
        db_path=db_path,
        backup_dir=backup_dir,
        env_file_path=env_file,
        current_key=initial_key,
        migration_id="test-mig-123",
    )

    # 1. Confirmation mismatch must fail
    with pytest.raises(ValueError, match="Explicit confirmation mismatch"):
        restore_database_from_backup(
            backup_file=b_db,
            target_db_path=db_path,
            env_file_path=env_file,
            confirm_backup_name="wrong_name.db",
        )

    # 2. Add an execution attempt to target DB: restore must be REFUSED to prevent erasing evidence
    conn = sqlite3.connect(str(db_path))
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO execution_attempts (attempt_id, action_id, plan_id, attempt_number, status, created_at) VALUES (?, ?, ?, ?, ?, ?)",
        ("att-restore-test", CONTROLLED_ACTION_ID, CONTROLLED_PLAN_ID, 1, "succeeded", "2026-10-27T12:00:00Z"),
    )
    conn.commit()
    conn.close()

    with pytest.raises(RuntimeError, match="RESTORE REFUSED: Target database contains .* attempt"):
        restore_database_from_backup(
            backup_file=b_db,
            target_db_path=db_path,
            env_file_path=env_file,
            confirm_backup_name=b_db.name,
        )

    # Clean the attempt and verify legitimate restore succeeds
    conn = sqlite3.connect(str(db_path))
    cur = conn.cursor()
    cur.execute("DELETE FROM execution_attempts;")
    cur.execute("DELETE FROM cases;")
    conn.commit()
    conn.close()

    restore_database_from_backup(
        backup_file=b_db,
        target_db_path=db_path,
        env_file_path=env_file,
        confirm_backup_name=b_db.name,
    )

    conn = sqlite3.connect(str(db_path))
    cur = conn.cursor()
    cur.execute("SELECT count(*) FROM cases WHERE case_id = ?", (CONTROLLED_CASE_ID,))
    assert cur.fetchone()[0] == 1
    conn.close()
