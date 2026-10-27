"""Unit tests for JackVerse Phase 5.3.3.2 Stage B: Governed Gmail Transmission Preparation (Amended)."""

from __future__ import annotations

import hashlib
import os
import shutil
import sqlite3
import tempfile
import pytest

from caseworker.adapters.crypto.token_cipher import TokenCipher
from caseworker.domain.action import Action
from caseworker.domain.approval import Approval
from caseworker.domain.artifact import (
    ArtifactBlock,
    PreparedArtifact,
    compute_artifact_grounding_hash,
)
from caseworker.domain.artifact_planner import PLANNER_VERSION
from caseworker.domain.case import Case
from caseworker.domain.claim import Claim, compute_support_snapshot_hash
from caseworker.domain.context import ContextFact
from caseworker.domain.enums import (
    ActionStatus,
    ActionType,
    ApprovalStatus,
    ArtifactGroundingType,
    ArtifactStatus,
    CaseStatus,
    CaseType,
    ClaimStatus,
    ExecutionDestinationType,
    ExternalExecutionPlanStatus,
    OpportunityStatus,
    OpportunityType,
    PreparationMaterialType,
    PreparationPackageStatus,
    SourceType,
    TargetFieldType,
    TargetSchemaProvenance,
    VerificationStatus,
)
from caseworker.domain.errors import (
    ApprovalValidationError,
    DomainValidationError,
    SSRFValidationError,
)
from caseworker.domain.execution_plan import (
    EmailAcceptanceEvidence,
    ExecutionDestination,
    TargetFieldBinding,
    TargetInteractionSchema,
    TargetSchemaField,
    TargetSchemaUploadSlot,
    TargetSubmitControlSpec,
    VerifiedEmailAcceptanceRecord,
)
from caseworker.domain.execution_plan_integrity import (
    ExecutionPlanIntegrityFailureKind,
    ExecutionPlanIntegrityValidator,
)
from caseworker.domain.execution_planner import ExecutionPlanCompiler
from caseworker.domain.opportunity import Opportunity
from caseworker.domain.package import PreparationPackage
from caseworker.domain.self_test_policy import (
    CONTROLLED_TEST_CASE_ID,
    CONTROLLED_TEST_EMAIL,
    CONTROLLED_TEST_OPERATOR_ID,
    CONTROLLED_TEST_OPP_ID,
    CONTROLLED_TEST_PACKAGE_FP,
    CONTROLLED_TEST_PACKAGE_ID,
    SELF_TEST_EMPLOYER_DOMAIN,
    SELF_TEST_EVIDENCE_SOURCE,
    SELF_TEST_SOURCE_URI_PREFIX,
    SELF_TEST_SYSTEM_VERIFIER,
    ServerEnforcedSelfTestPolicy,
)
from caseworker.domain.ssrf_validator import validate_evidence_source_uri
from caseworker.domain.types import compute_sha256, compute_sha256_bytes, now_utc
from caseworker.persistence.sqlite import SQLiteCaseworkerStorage
from caseworker.services.execution_plan_service import ExecutionPlanService
from scripts.prepare_controlled_test_stage_a import (
    EXACT_PROPOSED_BODY,
    EXACT_PROPOSED_SUBJECT,
    prepare_stage_a,
)
from scripts.prepare_controlled_test_stage_b import (
    prepare_stage_b,
    verify_runtime_safety,
)


@pytest.fixture
def stage_b_test_db(monkeypatch):
    """Fixture providing an isolated SQLite database with an approved Stage A package."""
    temp_dir = tempfile.mkdtemp()
    db_path = os.path.join(temp_dir, "test_stage_b.db")

    # Enforce safe execution flags
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
            CONTROLLED_TEST_OPERATOR_ID,
            "gmail",
            "google-sub-operator-1",
            access_token_enc,
            refresh_token_enc,
            "2099-01-01T00:00:00Z",
            CONTROLLED_TEST_EMAIL,
            "https://www.googleapis.com/auth/gmail.send https://www.googleapis.com/auth/userinfo.email openid",
            "2026-10-26T12:00:00Z",
            "2026-10-26T12:00:00Z",
        ),
    )
    conn.commit()
    conn.close()

    # Run Stage A preparation
    dossier_a = prepare_stage_a(
        db_path=db_path,
        operator_id=CONTROLLED_TEST_OPERATOR_ID,
        recipient_email=CONTROLLED_TEST_EMAIL,
    )
    pkg_id = dossier_a["preparation_package"]["package_id"]
    pkg_fp = dossier_a["preparation_package"]["package_fingerprint"]
    approval_id = dossier_a["approval"]["approval_id"]

    # Approve package legitimately via ApprovalService
    from caseworker.services.approval_service import ApprovalService
    app_svc = ApprovalService(storage)
    app_svc.approve_action(
        user_id=CONTROLLED_TEST_OPERATOR_ID,
        approval_id=approval_id,
        reason="Human review approval",
    )

    # Monkeypatch server-enforced package constants to match this test database
    monkeypatch.setattr("caseworker.domain.self_test_policy.CONTROLLED_TEST_PACKAGE_ID", pkg_id)
    monkeypatch.setattr("caseworker.domain.self_test_policy.CONTROLLED_TEST_PACKAGE_FP", pkg_fp)
    monkeypatch.setattr("scripts.prepare_controlled_test_stage_b.CONTROLLED_TEST_PACKAGE_ID", pkg_id)
    monkeypatch.setattr("scripts.prepare_controlled_test_stage_b.CONTROLLED_TEST_PACKAGE_FP", pkg_fp)

    yield db_path

    # Teardown
    if os.path.exists(temp_dir):
        shutil.rmtree(temp_dir, ignore_errors=True)


class DummyMock:
    def __init__(self, **kwargs):
        for k, v in kwargs.items():
            setattr(self, k, v)


def _make_valid_test_entities():
    case = DummyMock(case_id=CONTROLLED_TEST_CASE_ID, user_id=CONTROLLED_TEST_OPERATOR_ID)
    opp = DummyMock(
        opportunity_id=CONTROLLED_TEST_OPP_ID,
        converted_case_id=CONTROLLED_TEST_CASE_ID,
        user_id=CONTROLLED_TEST_OPERATOR_ID,
    )
    pkg = DummyMock(
        package_id=CONTROLLED_TEST_PACKAGE_ID,
        case_id=CONTROLLED_TEST_CASE_ID,
        user_id=CONTROLLED_TEST_OPERATOR_ID,
        package_fingerprint=CONTROLLED_TEST_PACKAGE_FP,
        status=PreparationPackageStatus.APPROVED,
    )
    cred = DummyMock(
        user_id=CONTROLLED_TEST_OPERATOR_ID,
        provider="gmail",
        authenticated_email=CONTROLLED_TEST_EMAIL,
        scopes=["https://www.googleapis.com/auth/gmail.send", "openid"],
    )
    instructions = f"Controlled internal test to {CONTROLLED_TEST_EMAIL}"
    ev = DummyMock(
        evidence_source=SELF_TEST_EVIDENCE_SOURCE,
        source_uri=f"{SELF_TEST_SOURCE_URI_PREFIX}{CONTROLLED_TEST_OPERATOR_ID}",
        employer_domain=SELF_TEST_EMPLOYER_DOMAIN,
        verified_recipient_email=CONTROLLED_TEST_EMAIL,
        verified_by=SELF_TEST_SYSTEM_VERIFIER,
        instructions_text=instructions,
        snapshot_hash=compute_sha256(instructions.strip()),
    )
    return case, opp, pkg, cred, ev


def test_self_test_policy_valid_with_system_attestation_and_oauth_cred():
    """Verify ServerEnforcedSelfTestPolicy passes when all invariants match."""
    case, opp, pkg, cred, ev = _make_valid_test_entities()

    # Should succeed without exception
    ServerEnforcedSelfTestPolicy.validate(
        case=case,
        opportunity=opp,
        package=pkg,
        evidence=ev,
        clean_dest_email=CONTROLLED_TEST_EMAIL,
        oauth_credential=cred,
    )


def test_self_test_policy_rejects_fabricated_operator_attestation():
    """Amendment 2: Policy strictly rejects verified_by starting with operator: on self-test evidence."""
    case, opp, pkg, cred, ev = _make_valid_test_entities()
    ev.verified_by = f"operator:{CONTROLLED_TEST_OPERATOR_ID}"

    with pytest.raises(DomainValidationError, match="verifier authority mismatch: expected exact"):
        ServerEnforcedSelfTestPolicy.validate(
            case=case,
            opportunity=opp,
            package=pkg,
            evidence=ev,
            clean_dest_email=CONTROLLED_TEST_EMAIL,
            oauth_credential=cred,
        )


def test_self_test_policy_rejects_arbitrary_or_truncated_system_verifier():
    """Amendment 2: Policy rejects startswith variants; exact match required."""
    case, opp, pkg, cred, ev = _make_valid_test_entities()
    ev.verified_by = "system:oauth_mailbox_attestation"  # missing :operator-1

    with pytest.raises(DomainValidationError, match="verifier authority mismatch: expected exact"):
        ServerEnforcedSelfTestPolicy.validate(
            case=case,
            opportunity=opp,
            package=pkg,
            evidence=ev,
            clean_dest_email=CONTROLLED_TEST_EMAIL,
            oauth_credential=cred,
        )


def test_self_test_policy_independent_of_execution_flags(monkeypatch):
    """Requirement 3: Identity and destination validation pass regardless of execution flags."""
    case, opp, pkg, cred, ev = _make_valid_test_entities()

    # When flags are enabled, ServerEnforcedSelfTestPolicy.validate() must NOT fail!
    monkeypatch.setenv("CASEWORKER_REAL_EXECUTION_ENABLED", "true")
    monkeypatch.setenv("CASEWORKER_GMAIL_EXECUTION_ENABLED", "true")

    # Policy validation succeeds
    ServerEnforcedSelfTestPolicy.validate(
        case=case,
        opportunity=opp,
        package=pkg,
        evidence=ev,
        clean_dest_email=CONTROLLED_TEST_EMAIL,
        oauth_credential=cred,
    )


def test_prepare_stage_b_fails_if_execution_flags_enabled(monkeypatch, stage_b_test_db):
    """Requirement 3: Stage B preparation script fails closed if execution flags are enabled."""
    monkeypatch.setenv("CASEWORKER_REAL_EXECUTION_ENABLED", "true")
    with pytest.raises(RuntimeError, match="SAFETY VIOLATION"):
        prepare_stage_b(
            db_path=stage_b_test_db,
            operator_id=CONTROLLED_TEST_OPERATOR_ID,
            recipient_email=CONTROLLED_TEST_EMAIL,
        )


def test_self_test_policy_enforces_case_opportunity_converted_case_id():
    """Amendment 1: Verify policy checks opportunity.converted_case_id and owner."""
    case, opp, pkg, cred, ev = _make_valid_test_entities()

    # Mismatched converted_case_id
    opp_mismatch = DummyMock(
        opportunity_id=CONTROLLED_TEST_OPP_ID,
        converted_case_id="case-different-1234",
        user_id=CONTROLLED_TEST_OPERATOR_ID,
    )
    with pytest.raises(DomainValidationError, match="converted_case_id 'case-different-1234' does not match Case"):
        ServerEnforcedSelfTestPolicy.validate(case, opp_mismatch, pkg, ev, CONTROLLED_TEST_EMAIL, oauth_credential=cred)

    # Mismatched opportunity owner
    opp_bad_user = DummyMock(
        opportunity_id=CONTROLLED_TEST_OPP_ID,
        converted_case_id=CONTROLLED_TEST_CASE_ID,
        user_id="impostor-user",
    )
    with pytest.raises(DomainValidationError, match="Opportunity owner 'impostor-user' does not match Case owner"):
        ServerEnforcedSelfTestPolicy.validate(case, opp_bad_user, pkg, ev, CONTROLLED_TEST_EMAIL, oauth_credential=cred)


def test_self_test_policy_rejects_wrong_oauth_credential():
    """Amendment 2: Policy rejects missing or mismatched OAuth credentials."""
    case, opp, pkg, cred, ev = _make_valid_test_entities()

    # 1. Missing credential
    with pytest.raises(DomainValidationError, match="requires authenticated Google OAuth credential"):
        ServerEnforcedSelfTestPolicy.validate(case, opp, pkg, ev, CONTROLLED_TEST_EMAIL, oauth_credential=None)

    # 2. Mismatched user_id
    cred_bad_user = DummyMock(
        user_id="other-user",
        provider="gmail",
        authenticated_email=CONTROLLED_TEST_EMAIL,
        scopes=["https://www.googleapis.com/auth/gmail.send"],
    )
    with pytest.raises(DomainValidationError, match="Connected OAuth credential owner 'other-user' does not match"):
        ServerEnforcedSelfTestPolicy.validate(case, opp, pkg, ev, CONTROLLED_TEST_EMAIL, oauth_credential=cred_bad_user)

    # 3. Wrong provider
    cred_bad_provider = DummyMock(
        user_id=CONTROLLED_TEST_OPERATOR_ID,
        provider="github",
        authenticated_email=CONTROLLED_TEST_EMAIL,
        scopes=["https://www.googleapis.com/auth/gmail.send"],
    )
    with pytest.raises(DomainValidationError, match="provider must be 'gmail'"):
        ServerEnforcedSelfTestPolicy.validate(case, opp, pkg, ev, CONTROLLED_TEST_EMAIL, oauth_credential=cred_bad_provider)

    # 4. Mismatched email
    cred_bad_email = DummyMock(
        user_id=CONTROLLED_TEST_OPERATOR_ID,
        provider="gmail",
        authenticated_email="impostor@victim.com",
        scopes=["https://www.googleapis.com/auth/gmail.send"],
    )
    with pytest.raises(DomainValidationError, match="does not match authorized operator email"):
        ServerEnforcedSelfTestPolicy.validate(case, opp, pkg, ev, CONTROLLED_TEST_EMAIL, oauth_credential=cred_bad_email)

    # 5. Missing required send scope
    cred_no_scope = DummyMock(
        user_id=CONTROLLED_TEST_OPERATOR_ID,
        provider="gmail",
        authenticated_email=CONTROLLED_TEST_EMAIL,
        scopes=["https://www.googleapis.com/auth/userinfo.email"],
    )
    with pytest.raises(DomainValidationError, match="lacks required 'https://www.googleapis.com/auth/gmail.send' scope"):
        ServerEnforcedSelfTestPolicy.validate(case, opp, pkg, ev, CONTROLLED_TEST_EMAIL, oauth_credential=cred_no_scope)


def test_global_ssrf_validator_remains_unmodified():
    """Verify that global ssrf_validator still rejects non-HTTP(S) schemes, URNs, and local endpoints."""
    with pytest.raises(SSRFValidationError, match="scheme"):
        validate_evidence_source_uri("urn:jackverse:controlled-test:operator-1")

    with pytest.raises(SSRFValidationError, match="prohibited"):
        validate_evidence_source_uri("http://127.0.0.1/test")

    with pytest.raises(SSRFValidationError, match="prohibited"):
        validate_evidence_source_uri("http://localhost:8080/test")


def test_stage_b_end_to_end_preparation_and_idempotency(stage_b_test_db):
    """Test full Stage B preparation: compilation, authorization request, and idempotent replay."""
    dossier = prepare_stage_b(
        db_path=stage_b_test_db,
        operator_id=CONTROLLED_TEST_OPERATOR_ID,
        recipient_email=CONTROLLED_TEST_EMAIL,
    )

    assert dossier["stage"] == "STAGE_B_TRANSMISSION_PREPARATION"
    assert dossier["status"] == "READY_FOR_HUMAN_TRANSMISSION_REVIEW"
    assert dossier["operator_id"] == CONTROLLED_TEST_OPERATOR_ID
    assert dossier["sender_email"] == CONTROLLED_TEST_EMAIL
    assert dossier["recipient_email"] == CONTROLLED_TEST_EMAIL

    # Self-test policy assertions (Amendment 2: exact system verifier)
    assert dossier["self_test_policy"]["evidence_source"] == SELF_TEST_EVIDENCE_SOURCE
    assert dossier["self_test_policy"]["source_uri"] == f"{SELF_TEST_SOURCE_URI_PREFIX}{CONTROLLED_TEST_OPERATOR_ID}"
    assert dossier["self_test_policy"]["employer_domain"] == SELF_TEST_EMPLOYER_DOMAIN
    assert dossier["self_test_policy"]["verified_by"] == SELF_TEST_SYSTEM_VERIFIER
    assert dossier["self_test_policy"]["full_name_omitted"] is True
    assert dossier["self_test_policy"]["applicant_email_bound"] is True

    # Execution Plan assertions
    plan_info = dossier["execution_plan"]
    assert plan_info["status"] == ExternalExecutionPlanStatus.READY_FOR_AUTHORIZATION.value
    assert len(plan_info["unresolved_errors"]) == 0
    assert plan_info["destination_type"] == ExecutionDestinationType.EMAIL.value
    assert plan_info["canonical_url"] == f"mailto:{CONTROLLED_TEST_EMAIL}"
    assert plan_info["canonical_integrity_valid"] is True

    # Attachment Fidelity assertions (Requirement 4)
    att_fid = dossier["attachment_fidelity"]
    assert att_fid["approved_artifact_id"] == "art-cv-case-controlled-test-5332-v2"
    assert att_fid["transmitted_filename"] == "cv_operator-1.txt"
    assert att_fid["byte_size"] == 131
    assert att_fid["content_sha256"] == "43057a3551057a31e6cc00494ce435943b2a1e25f4f61a23a253ac58326a3573"
    assert att_fid["bytes_match_approved_artifact"] is True

    # Email Manifest assertions (Requirement 6: complete body)
    manifest = dossier["email_manifest"]
    assert manifest["to_email"] == CONTROLLED_TEST_EMAIL
    assert manifest["from_email"] == CONTROLLED_TEST_EMAIL
    assert manifest["subject"] == EXACT_PROPOSED_SUBJECT
    assert len(manifest["sealed_payload_hash"]) == 64
    assert manifest["complete_body"] == EXACT_PROPOSED_BODY

    # Transmission Authorization assertions
    tx_auth = dossier["transmission_authorization"]
    assert tx_auth["action_type"] == ActionType.SUBMIT_FORM.value
    assert tx_auth["action_status"] == ActionStatus.AWAITING_APPROVAL.value
    assert tx_auth["approval_status"] == ApprovalStatus.PENDING.value
    assert tx_auth["case_status"] == CaseStatus.AWAITING_APPROVAL.value

    # Safety assertions
    assert dossier["real_gmail_transmissions"] == 0
    assert dossier["execution_attempts"] == 0
    assert dossier["execution_receipts"] == 0
    assert dossier["human_send_authorization"] == "NOT_GRANTED"

    # Idempotent replay test: running prepare_stage_b again returns exact same plan and auth
    replay_dossier = prepare_stage_b(
        db_path=stage_b_test_db,
        operator_id=CONTROLLED_TEST_OPERATOR_ID,
        recipient_email=CONTROLLED_TEST_EMAIL,
    )
    assert replay_dossier["execution_plan"]["plan_id"] == plan_info["plan_id"]
    assert replay_dossier["transmission_authorization"]["action_id"] == tx_auth["action_id"]
    assert replay_dossier["transmission_authorization"]["approval_id"] == tx_auth["approval_id"]


def test_stage_b_atomic_supersession_replaces_old_plan_and_authorization(stage_b_test_db):
    """Amendment 3: If an old plan with stale evidence exists, it is superseded and replaced."""
    # First, run Stage B to create initial plan
    dossier_1 = prepare_stage_b(
        db_path=stage_b_test_db,
        operator_id=CONTROLLED_TEST_OPERATOR_ID,
        recipient_email=CONTROLLED_TEST_EMAIL,
    )
    plan_1_id = dossier_1["execution_plan"]["plan_id"]
    action_1_id = dossier_1["transmission_authorization"]["action_id"]
    approval_1_id = dossier_1["transmission_authorization"]["approval_id"]

    # Now simulate an invalidation by inserting an outdated/fabricated evidence record
    storage = SQLiteCaseworkerStorage(db_path=stage_b_test_db)
    with storage.unit_of_work() as uow:
        # Supersede active evidence with a mock old record that will force supersession
        uow.verified_email_acceptance.supersede_active(CONTROLLED_TEST_OPP_ID, now_utc().isoformat())
        old_rec = VerifiedEmailAcceptanceRecord(
            evidence_id="ev_old_fabricated",
            opportunity_id=CONTROLLED_TEST_OPP_ID,
            user_id=CONTROLLED_TEST_OPERATOR_ID,
            evidence_source=SELF_TEST_EVIDENCE_SOURCE,
            source_uri=f"{SELF_TEST_SOURCE_URI_PREFIX}{CONTROLLED_TEST_OPERATOR_ID}",
            instructions_text="Old fabricated text",
            verified_recipient_email=CONTROLLED_TEST_EMAIL,
            employer_domain=SELF_TEST_EMPLOYER_DOMAIN,
            snapshot_hash=compute_sha256("Old fabricated text"),
            evidence_digest="old_digest_123",
            verified_by=f"operator:{CONTROLLED_TEST_OPERATOR_ID}",  # Old rejected format
            verified_at=now_utc().isoformat(),
            evidence_version=10,
            is_active=True,
        )
        uow.verified_email_acceptance.save(old_rec)
        uow.commit()

    # Run Stage B again: it must detect the old verifier, supersede it, supersede plan 1, cancel action 1, and create replacement!
    dossier_2 = prepare_stage_b(
        db_path=stage_b_test_db,
        operator_id=CONTROLLED_TEST_OPERATOR_ID,
        recipient_email=CONTROLLED_TEST_EMAIL,
    )

    plan_2_id = dossier_2["execution_plan"]["plan_id"]
    action_2_id = dossier_2["transmission_authorization"]["action_id"]
    approval_2_id = dossier_2["transmission_authorization"]["approval_id"]

    assert plan_2_id != plan_1_id
    assert action_2_id != action_1_id
    assert approval_2_id != approval_1_id

    # Verify database state for old vs new
    with storage.unit_of_work() as uow:
        old_plan = uow.execution_plans.get_by_id(plan_1_id)
        assert old_plan.status == ExternalExecutionPlanStatus.SUPERSEDED

        old_act = uow.actions.get_by_id(action_1_id)
        assert old_act.status == ActionStatus.CANCELLED

        old_app = uow.approvals.get_by_id(approval_1_id)
        assert old_app.status == ApprovalStatus.CANCELLED

        new_plan = uow.execution_plans.get_by_id(plan_2_id)
        assert new_plan.status == ExternalExecutionPlanStatus.READY_FOR_AUTHORIZATION

        new_act = uow.actions.get_by_id(action_2_id)
        assert new_act.status == ActionStatus.AWAITING_APPROVAL

        new_app = uow.approvals.get_by_id(approval_2_id)
        assert new_app.status == ApprovalStatus.PENDING
