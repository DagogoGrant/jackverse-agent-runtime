"""Unit and fault-injection tests for crash-recoverable OAuth encryption key rotation."""

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
    CONTROLLED_USER_ID,
    check_encryption_status,
    create_verified_online_backup,
    execute_migration,
    load_secret_from_env_file,
    recover_or_resume,
    restore_database_from_backup,
    update_secret_in_env_file,
)


def _setup_test_db(db_path: Path, cipher: TokenCipher, access_token: str = "ya29.initial_test_token", refresh_token: str = "1//initial_refresh_token") -> None:
    """Create schema and populate test database with required controlled entities."""
    conn = sqlite3.connect(str(db_path))
    cur = conn.cursor()

    # Create tables matching caseworker SQLite schema
    cur.execute("""
        CREATE TABLE cases (
            case_id TEXT PRIMARY KEY,
            case_type TEXT NOT NULL,
            status TEXT NOT NULL,
            title TEXT,
            description TEXT,
            metadata TEXT,
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
            fingerprint TEXT NOT NULL,
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

    # Populate controlled entities
    now_iso = "2026-10-27T12:00:00Z"
    cur.execute(
        "INSERT INTO cases (case_id, case_type, status, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
        (CONTROLLED_CASE_ID, "opportunity_application", "action_required", now_iso, now_iso),
    )

    plan_fingerprint = "6e46f1a5cef7364a843c4b8e8713fa05a0506673a41815597b55107fa729a0ae"
    cur.execute(
        "INSERT INTO external_execution_plans (plan_id, case_id, action_id, destination_type, status, plan_hash, fingerprint, parameters, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (CONTROLLED_PLAN_ID, CONTROLLED_CASE_ID, CONTROLLED_ACTION_ID, "gmail", "ready_for_authorization", "hash-plan-test", plan_fingerprint, "{}", now_iso, now_iso),
    )

    cur.execute(
        "INSERT INTO actions (action_id, case_id, action_type, status, version, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (CONTROLLED_ACTION_ID, CONTROLLED_CASE_ID, "submit_form", "approved", 3, now_iso, now_iso),
    )

    cur.execute(
        "INSERT INTO approvals (approval_id, case_id, action_id, status, decision, decided_at, decided_by, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (CONTROLLED_APPROVAL_ID, CONTROLLED_CASE_ID, CONTROLLED_ACTION_ID, "approved", "APPROVED", now_iso, "operator-1", now_iso, now_iso),
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


def test_create_verified_online_backup_success(test_env):
    """Verify that create_verified_online_backup creates a verified copy with required domain entities."""
    db_path = test_env["db_path"]
    backup_dir = test_env["backup_dir"]

    backup_file = create_verified_online_backup(db_path=db_path, backup_dir=backup_dir)
    assert backup_file.is_file()
    assert (backup_file.stat().st_mode & 0o777) == 0o600

    conn = sqlite3.connect(str(backup_file))
    cur = conn.cursor()
    cur.execute("PRAGMA integrity_check;")
    assert cur.fetchall() == [("ok",)]

    cur.execute("SELECT case_id, status FROM cases WHERE case_id = ?", (CONTROLLED_CASE_ID,))
    assert cur.fetchone() == (CONTROLLED_CASE_ID, "action_required")

    cur.execute("SELECT plan_id, status FROM external_execution_plans WHERE plan_id = ?", (CONTROLLED_PLAN_ID,))
    assert cur.fetchone() == (CONTROLLED_PLAN_ID, "ready_for_authorization")

    cur.execute("SELECT action_id, status FROM actions WHERE action_id = ?", (CONTROLLED_ACTION_ID,))
    assert cur.fetchone() == (CONTROLLED_ACTION_ID, "approved")

    cur.execute("SELECT approval_id, status FROM approvals WHERE approval_id = ?", (CONTROLLED_APPROVAL_ID,))
    assert cur.fetchone() == (CONTROLLED_APPROVAL_ID, "approved")
    conn.close()


def test_migration_successful_without_data_loss(test_env):
    """Test full successful migration lifecycle without data loss."""
    db_path = test_env["db_path"]
    env_file = test_env["env_file"]
    backup_dir = test_env["backup_dir"]
    initial_key = test_env["initial_key"]

    new_key = secrets.token_urlsafe(48)
    res = execute_migration(
        db_path=db_path,
        env_file_path=env_file,
        backup_dir=backup_dir,
        staged_key=new_key,
    )

    assert res["status"] == "SUCCESS"
    assert res["migrated_records"] == 1
    assert Path(res["backup_db"]).is_file()
    assert Path(res["backup_env"]).is_file()

    # Check updated env file
    updated_key = load_secret_from_env_file(env_file, "CASEWORKER_ENCRYPTION_KEY")
    assert updated_key == new_key
    assert updated_key != initial_key

    # Check database tokens decrypt with new key
    cipher_new = TokenCipher(primary_key=new_key)
    conn = sqlite3.connect(str(db_path))
    cur = conn.cursor()
    cur.execute("SELECT access_token_enc, refresh_token_enc FROM user_oauth_credentials WHERE user_id = ?", (CONTROLLED_USER_ID,))
    at_enc, rt_enc = cur.fetchone()
    conn.close()

    assert cipher_new.decrypt(at_enc) == "ya29.initial_test_token"
    assert cipher_new.decrypt(rt_enc) == "1//initial_refresh_token"

    # Verify staging file is cleaned up
    staged_key_file = env_file.parent / ".env.encryption_key_staged.tmp"
    assert not staged_key_file.exists()


def test_plan_fingerprint_invariance_before_and_after_rotation(test_env):
    """Confirm external execution plan fingerprint and hash remain invariant across rotation."""
    db_path = test_env["db_path"]
    env_file = test_env["env_file"]
    backup_dir = test_env["backup_dir"]

    conn_before = sqlite3.connect(str(db_path))
    cur_before = conn_before.cursor()
    cur_before.execute("SELECT plan_hash, fingerprint FROM external_execution_plans WHERE plan_id = ?", (CONTROLLED_PLAN_ID,))
    hash_before, fp_before = cur_before.fetchone()
    conn_before.close()

    execute_migration(db_path=db_path, env_file_path=env_file, backup_dir=backup_dir)

    conn_after = sqlite3.connect(str(db_path))
    cur_after = conn_after.cursor()
    cur_after.execute("SELECT plan_hash, fingerprint FROM external_execution_plans WHERE plan_id = ?", (CONTROLLED_PLAN_ID,))
    hash_after, fp_after = cur_after.fetchone()
    conn_after.close()

    assert hash_before == hash_after
    assert fp_before == fp_after


def test_fault_injection_checkpoint_1_crash_before_db_commit(test_env):
    """Crash before DB commit leaves DB and env file completely unchanged."""
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
    cur.execute("SELECT access_token_enc FROM user_oauth_credentials WHERE user_id = ?", (CONTROLLED_USER_ID,))
    at_enc = cur.fetchone()[0]
    conn.close()
    assert cipher_old.decrypt(at_enc) == "ya29.initial_test_token"

    # Env file still has original key
    assert load_secret_from_env_file(env_file, "CASEWORKER_ENCRYPTION_KEY") == initial_key

    # Resume cleans up the aborted staging file
    recovery_res = recover_or_resume(db_path, env_file, backup_dir)
    assert recovery_res["status"] == "CLEANED_ABORTED_STAGING"
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
    cur.execute("SELECT access_token_enc FROM user_oauth_credentials WHERE user_id = ?", (CONTROLLED_USER_ID,))
    at_enc = cur.fetchone()[0]
    conn.close()
    assert cipher_new.decrypt(at_enc) == "ya29.initial_test_token"

    # Forward recovery completes env replacement
    rec = recover_or_resume(db_path, env_file, backup_dir)
    assert rec["status"] == "FORWARD_RECOVERED"
    assert load_secret_from_env_file(env_file, "CASEWORKER_ENCRYPTION_KEY") == new_key

    # Staging cleaned
    staged_key_file = env_file.parent / ".env.encryption_key_staged.tmp"
    assert not staged_key_file.exists()

    # Status check confirms all decryptable
    status = check_encryption_status(db_path, env_file)
    assert status["all_decryptable"] is True


def test_fault_injection_checkpoint_3_crash_after_env_replacement(test_env):
    """Crash after env replacement before staging cleanup leaves staging file; resume cleans it."""
    db_path = test_env["db_path"]
    env_file = test_env["env_file"]
    backup_dir = test_env["backup_dir"]

    new_key = secrets.token_urlsafe(48)
    with pytest.raises(RuntimeError, match="FAULT_INJECTION_CHECKPOINT_3"):
        execute_migration(db_path=db_path, env_file_path=env_file, backup_dir=backup_dir, staged_key=new_key, fault_inject_checkpoint=3)

    # Both DB and env have new key
    assert load_secret_from_env_file(env_file, "CASEWORKER_ENCRYPTION_KEY") == new_key
    staged_key_file = env_file.parent / ".env.encryption_key_staged.tmp"
    assert staged_key_file.is_file()

    # Resume cleans staging
    rec = recover_or_resume(db_path, env_file, backup_dir)
    assert rec["status"] == "CLEANED_STAGING"
    assert not staged_key_file.exists()


def test_migration_refused_if_execution_attempts_exist(test_env):
    """Migration is strictly refused if any execution attempts exist in the database."""
    db_path = test_env["db_path"]
    env_file = test_env["env_file"]
    backup_dir = test_env["backup_dir"]

    conn = sqlite3.connect(str(db_path))
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO execution_attempts (attempt_id, action_id, plan_id, attempt_number, status, created_at) VALUES (?, ?, ?, ?, ?, ?)",
        ("att-1", CONTROLLED_ACTION_ID, CONTROLLED_PLAN_ID, 1, "failed", "2026-10-27T12:00:00Z"),
    )
    conn.commit()
    conn.close()

    with pytest.raises(RuntimeError, match="Refusing migration: execution attempts"):
        execute_migration(db_path=db_path, env_file_path=env_file, backup_dir=backup_dir)


def test_migration_refused_if_execution_receipts_exist(test_env):
    """Migration is strictly refused if any execution receipts exist in the database."""
    db_path = test_env["db_path"]
    env_file = test_env["env_file"]
    backup_dir = test_env["backup_dir"]

    conn = sqlite3.connect(str(db_path))
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO execution_receipts (receipt_id, action_id, plan_id, status, created_at) VALUES (?, ?, ?, ?, ?)",
        ("rcpt-1", CONTROLLED_ACTION_ID, CONTROLLED_PLAN_ID, "delivered", "2026-10-27T12:00:00Z"),
    )
    conn.commit()
    conn.close()

    with pytest.raises(RuntimeError, match="Refusing migration: execution attempts"):
        execute_migration(db_path=db_path, env_file_path=env_file, backup_dir=backup_dir)


def test_restore_database_from_backup(test_env):
    """Verify clean restoration of database from backup using restore_database_from_backup."""
    db_path = test_env["db_path"]
    backup_dir = test_env["backup_dir"]

    backup_file = create_verified_online_backup(db_path=db_path, backup_dir=backup_dir)

    # Mutate db_path
    conn = sqlite3.connect(str(db_path))
    cur = conn.cursor()
    cur.execute("DELETE FROM cases;")
    conn.commit()
    conn.close()

    # Restore from backup
    restore_database_from_backup(backup_file, db_path)

    # Verify restored state
    conn = sqlite3.connect(str(db_path))
    cur = conn.cursor()
    cur.execute("SELECT count(*) FROM cases WHERE case_id = ?", (CONTROLLED_CASE_ID,))
    assert cur.fetchone()[0] == 1
    conn.close()
