"""Unit tests for JackVerse Phase 5.3.3.2 Stage A preparation tooling."""

from __future__ import annotations

import os
import sqlite3
import tempfile
import pytest

from caseworker.adapters.crypto.token_cipher import TokenCipher
from caseworker.domain.enums import (
    ActionStatus,
    ActionType,
    ApprovalStatus,
    CaseStatus,
    ClaimStatus,
    PreparationPackageStatus,
    SourceType,
    VerificationStatus,
)
from caseworker.domain.package import PreparationPackage
from caseworker.persistence.sqlite import SQLiteCaseworkerStorage
from scripts.prepare_controlled_test_stage_a import (
    EXACT_PROPOSED_BODY,
    EXACT_PROPOSED_SUBJECT,
    STABLE_TEST_ID,
    prepare_stage_a,
    verify_runtime_safety,
)


@pytest.fixture
def test_db_with_oauth(monkeypatch):
    """Fixture providing an isolated SQLite database with valid OAuth credentials for operator-1."""
    temp_dir = tempfile.mkdtemp()
    db_path = os.path.join(temp_dir, "test_stage_a.db")

    # Enforce safe environment flags
    monkeypatch.setenv("CASEWORKER_REAL_EXECUTION_ENABLED", "false")
    monkeypatch.setenv("CASEWORKER_GMAIL_EXECUTION_ENABLED", "false")
    enc_key = "test-fernet-key-minimum-32-characters-entropy-pass"
    monkeypatch.setenv("CASEWORKER_ENCRYPTION_KEY", enc_key)

    # Initialize schema
    storage = SQLiteCaseworkerStorage(db_path=db_path)

    # Insert credentials into user_oauth_credentials
    cipher = TokenCipher(primary_key=enc_key)
    access_token_enc = cipher.encrypt("ya29.sample_mock_access_token_for_operator")
    refresh_token_enc = cipher.encrypt("1//sample_mock_refresh_token_for_operator")

    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO user_oauth_credentials (
            user_id, provider, google_sub, access_token_enc, refresh_token_enc,
            token_expiry, authenticated_email, scopes, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "operator-1",
            "gmail",
            "google-sub-operator-1",
            access_token_enc,
            refresh_token_enc,
            "2099-01-01T00:00:00Z",
            "dagogograntjack@gmail.com",
            "https://www.googleapis.com/auth/gmail.send https://www.googleapis.com/auth/userinfo.email openid",
            "2026-10-26T12:00:00Z",
            "2026-10-26T12:00:00Z",
        ),
    )
    conn.commit()
    conn.close()

    yield db_path

    # Teardown
    if os.path.exists(db_path):
        os.remove(db_path)
    if os.path.exists(temp_dir):
        os.rmdir(temp_dir)


def test_stage_a_preparation_halts_at_pending_approval(test_db_with_oauth):
    """Test that Stage A completes package creation and freezes it with PENDING approval."""
    dossier = prepare_stage_a(
        db_path=test_db_with_oauth,
        operator_id="operator-1",
        recipient_email="dagogograntjack@gmail.com",
    )

    assert dossier["stage"] == "STAGE_A_CONTENT_PREPARATION"
    assert dossier["status"] == "AWAITING_HUMAN_CONTENT_APPROVAL"
    assert dossier["operator_id"] == "operator-1"
    assert dossier["sender_email"] == "dagogograntjack@gmail.com"
    assert dossier["recipient_email"] == "dagogograntjack@gmail.com"

    # Package assertions
    assert dossier["preparation_package"]["status"] == PreparationPackageStatus.FROZEN.value
    assert dossier["preparation_package"]["package_id"].startswith("pkg_")

    # Approval boundary assertions
    assert dossier["approval"]["action_type"] == ActionType.APPROVE_PREPARATION_PACKAGE.value
    assert dossier["approval"]["status"] == ApprovalStatus.PENDING.value
    assert dossier["approval"]["requires_human_decision"] is True

    # Case assertions
    assert dossier["case"]["status"] == CaseStatus.AWAITING_APPROVAL.value

    # Proposed message content assertions
    assert dossier["proposed_message"]["subject"] == EXACT_PROPOSED_SUBJECT
    assert dossier["proposed_message"]["test_identifier"] == STABLE_TEST_ID
    assert STABLE_TEST_ID in dossier["proposed_message"]["body"]
    assert dossier["proposed_message"]["body"] == EXACT_PROPOSED_BODY

    # Synthetic attachment assertions
    assert len(dossier["proposed_attachments"]) == 1
    att = dossier["proposed_attachments"][0]
    assert att["filename"] == "cv_operator_1.txt"
    assert "Synthetic payload" in att["content_preview"]
    assert len(att["content_sha256"]) == 64

    # Direct database verification
    storage = SQLiteCaseworkerStorage(db_path=test_db_with_oauth)
    with storage.unit_of_work() as uow:
        pkg = uow.preparation_packages.get_by_id(dossier["preparation_package"]["package_id"])
        assert pkg is not None
        assert pkg.status == PreparationPackageStatus.FROZEN

        app = uow.approvals.get_by_id(dossier["approval"]["approval_id"])
        assert app is not None
        assert app.status == ApprovalStatus.PENDING

        act = uow.actions.get_by_id(dossier["approval"]["action_id"])
        assert act is not None
        assert act.status == ActionStatus.AWAITING_APPROVAL

        # Confirm zero live transmissions or execution attempts
        attempts = uow.execution_attempts.list_by_case(dossier["case"]["case_id"])
        assert len(attempts) == 0


def test_stage_a_evidence_provenance(test_db_with_oauth):
    """Test that evidence provenance correctly marks OAuth email SOURCE_VERIFIED and name UNVERIFIED."""
    dossier = prepare_stage_a(
        db_path=test_db_with_oauth,
        operator_id="operator-1",
    )

    prov = dossier["evidence_provenance"]
    assert prov["registered_source_id"] == "src-oauth-operator-1"
    assert prov["email_fact"]["verification_status"] == VerificationStatus.SOURCE_VERIFIED.value
    assert prov["email_fact"]["source_type"] == SourceType.PROFILE_IMPORT.value
    assert prov["email_fact"]["value"] == "dagogograntjack@gmail.com"

    assert prov["name_fact"]["verification_status"] == VerificationStatus.UNVERIFIED.value
    assert prov["name_fact"]["source_type"] == SourceType.AGENT_INFERENCE.value

    assert prov["claim"]["status"] == ClaimStatus.SUPPORTED.value
    assert prov["claim"]["supporting_fact_ids"] == ["fact-email-operator-1"]

    storage = SQLiteCaseworkerStorage(db_path=test_db_with_oauth)
    with storage.unit_of_work() as uow:
        f_email = uow.context.get_by_id("fact-email-operator-1")
        assert f_email is not None
        assert f_email.verification_status == VerificationStatus.SOURCE_VERIFIED

        f_name = uow.context.get_by_id("fact-name-operator-1")
        assert f_name is not None
        assert f_name.verification_status == VerificationStatus.UNVERIFIED

        claim = uow.claims.get_by_id("claim-test-operator-1")
        assert claim is not None
        assert claim.status == ClaimStatus.SUPPORTED
        assert claim.supporting_fact_ids == ["fact-email-operator-1"]


def test_stage_a_package_supersession(test_db_with_oauth):
    """Test that existing v1 package is superseded and its approval cancelled when v2 is prepared."""
    from caseworker.domain.action import Action
    from caseworker.domain.approval import Approval
    from caseworker.domain.types import now_utc

    storage = SQLiteCaseworkerStorage(db_path=test_db_with_oauth)
    # First initialize case and report
    init_dossier = prepare_stage_a(
        db_path=test_db_with_oauth,
        operator_id="operator-1",
    )

    # Simulate an existing v1 package and approval under set-controlled-test-5332
    v1_set_id = "set-controlled-test-5332"
    with storage.unit_of_work() as uow:
        rep = uow.preparation_reports.get_latest_for_case("case-controlled-test-5332")
        v1_pkg = PreparationPackage(
            package_id="pkg_v1_test_supersede",
            case_id="case-controlled-test-5332",
            artifact_set_id=v1_set_id,
            source_report_id=rep.report_id,
            user_id="operator-1",
            artifact_ids=["art-1"],
            artifact_versions={"art-1": 1},
            artifact_snapshots=[],
            claim_snapshots=[],
            rendered_payload_hashes={"art-1": "hash"},
            status=PreparationPackageStatus.FROZEN,
            created_at=now_utc(),
            updated_at=now_utc(),
            version=1,
        )
        uow.preparation_packages.save(v1_pkg)

        act = Action(
            action_id="act-v1-test",
            case_id="case-controlled-test-5332",
            action_type=ActionType.APPROVE_PREPARATION_PACKAGE,
            description="Mock test action",
            idempotency_key=f"pkg_act:{v1_pkg.package_fingerprint}",
            status=ActionStatus.AWAITING_APPROVAL,
            created_at=now_utc(),
            version=1,
        )
        uow.actions.save(act)

        app = Approval(
            approval_id="app-v1-test",
            action_id=act.action_id,
            case_id="case-controlled-test-5332",
            user_id="operator-1",
            action_fingerprint=act.compute_fingerprint(),
            status=ApprovalStatus.PENDING,
            requested_at=now_utc(),
            version=1,
        )
        uow.approvals.save(app)

    # Prepare Stage A again (which replaces v1 with v2)
    dossier = prepare_stage_a(
        db_path=test_db_with_oauth,
        operator_id="operator-1",
    )

    # Verify supersession history
    assert len(dossier["superseded_history"]) >= 1
    sup = next(s for s in dossier["superseded_history"] if s["package_id"] == "pkg_v1_test_supersede")
    assert sup["new_status"] == "superseded"
    assert sup["cancelled_approval_id"] == "app-v1-test"

    # Verify db records
    with storage.unit_of_work() as uow:
        reloaded_v1 = uow.preparation_packages.get_by_id("pkg_v1_test_supersede")
        assert reloaded_v1.status == PreparationPackageStatus.SUPERSEDED

        reloaded_app = uow.approvals.get_by_id("app-v1-test")
        assert reloaded_app.status == ApprovalStatus.CANCELLED

        reloaded_act = uow.actions.get_by_id("act-v1-test")
        assert reloaded_act.status == ActionStatus.CANCELLED


def test_stage_a_rejects_unapproved_recipient(test_db_with_oauth):
    """Test that Stage A preparation fails closed if recipient does not match authenticated operator."""
    with pytest.raises(RuntimeError, match="Controlled test must remain self-addressed"):
        prepare_stage_a(
            db_path=test_db_with_oauth,
            operator_id="operator-1",
            recipient_email="intruder@example.com",
        )


def test_stage_a_idempotent_execution(test_db_with_oauth):
    """Test that running Stage A multiple times returns the same package bundle without duplicating records."""
    dossier1 = prepare_stage_a(
        db_path=test_db_with_oauth,
        operator_id="operator-1",
    )
    dossier2 = prepare_stage_a(
        db_path=test_db_with_oauth,
        operator_id="operator-1",
    )

    assert dossier1["preparation_package"]["package_id"] == dossier2["preparation_package"]["package_id"]
    assert dossier1["approval"]["approval_id"] == dossier2["approval"]["approval_id"]
    assert dossier1["case"]["case_id"] == dossier2["case"]["case_id"]


def test_stage_a_fails_closed_when_execution_flags_enabled(test_db_with_oauth, monkeypatch):
    """Test that Stage A preparation fails closed if real execution flags are turned on."""
    monkeypatch.setenv("CASEWORKER_REAL_EXECUTION_ENABLED", "true")
    with pytest.raises(RuntimeError, match="SAFETY VIOLATION"):
        prepare_stage_a(db_path=test_db_with_oauth, operator_id="operator-1")

    monkeypatch.setenv("CASEWORKER_REAL_EXECUTION_ENABLED", "false")
    monkeypatch.setenv("CASEWORKER_GMAIL_EXECUTION_ENABLED", "true")
    with pytest.raises(RuntimeError, match="SAFETY VIOLATION"):
        prepare_stage_a(db_path=test_db_with_oauth, operator_id="operator-1")
