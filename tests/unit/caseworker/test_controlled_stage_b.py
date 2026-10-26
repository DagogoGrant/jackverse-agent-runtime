"""Unit tests for JackVerse Phase 5.3.3.2 Stage B: Governed Gmail Transmission Preparation."""

from __future__ import annotations

import os
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
from caseworker.domain.errors import DomainValidationError, SSRFValidationError
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
    if os.path.exists(db_path):
        os.remove(db_path)
    if os.path.exists(temp_dir):
        os.rmdir(temp_dir)


class DummyMock:
    def __init__(self, **kwargs):
        for k, v in kwargs.items():
            setattr(self, k, v)


def test_self_test_policy_valid():
    """Verify ServerEnforcedSelfTestPolicy passes when all invariants match."""
    case = DummyMock(case_id=CONTROLLED_TEST_CASE_ID, user_id=CONTROLLED_TEST_OPERATOR_ID)
    opp = DummyMock(opportunity_id=CONTROLLED_TEST_OPP_ID)
    pkg = DummyMock(
        package_id=CONTROLLED_TEST_PACKAGE_ID,
        package_fingerprint=CONTROLLED_TEST_PACKAGE_FP,
        status=PreparationPackageStatus.APPROVED,
    )
    ev = DummyMock(
        evidence_source=SELF_TEST_EVIDENCE_SOURCE,
        source_uri=f"{SELF_TEST_SOURCE_URI_PREFIX}{CONTROLLED_TEST_OPERATOR_ID}",
        employer_domain=SELF_TEST_EMPLOYER_DOMAIN,
        verified_recipient_email=CONTROLLED_TEST_EMAIL,
        verified_by=f"operator:{CONTROLLED_TEST_OPERATOR_ID}",
        instructions_text=f"Self-test transmission to {CONTROLLED_TEST_EMAIL}",
    )

    # Should succeed without exception
    ServerEnforcedSelfTestPolicy.validate(
        case=case,
        opportunity=opp,
        package=pkg,
        evidence=ev,
        clean_dest_email=CONTROLLED_TEST_EMAIL,
    )


def test_self_test_policy_fails_on_execution_flags(monkeypatch):
    """Verify ServerEnforcedSelfTestPolicy fails closed if execution flags are enabled."""
    case = DummyMock(case_id=CONTROLLED_TEST_CASE_ID, user_id=CONTROLLED_TEST_OPERATOR_ID)
    opp = DummyMock(opportunity_id=CONTROLLED_TEST_OPP_ID)
    pkg = DummyMock(
        package_id=CONTROLLED_TEST_PACKAGE_ID,
        package_fingerprint=CONTROLLED_TEST_PACKAGE_FP,
        status=PreparationPackageStatus.APPROVED,
    )
    ev = DummyMock(
        evidence_source=SELF_TEST_EVIDENCE_SOURCE,
        source_uri=f"{SELF_TEST_SOURCE_URI_PREFIX}{CONTROLLED_TEST_OPERATOR_ID}",
        employer_domain=SELF_TEST_EMPLOYER_DOMAIN,
        verified_recipient_email=CONTROLLED_TEST_EMAIL,
        verified_by=f"operator:{CONTROLLED_TEST_OPERATOR_ID}",
        instructions_text=f"Self-test transmission to {CONTROLLED_TEST_EMAIL}",
    )

    monkeypatch.setenv("CASEWORKER_REAL_EXECUTION_ENABLED", "true")
    with pytest.raises(RuntimeError, match="SAFETY VIOLATION"):
        ServerEnforcedSelfTestPolicy.validate(case, opp, pkg, ev, CONTROLLED_TEST_EMAIL)


def test_self_test_policy_fails_on_unauthorized_scope():
    """Verify ServerEnforcedSelfTestPolicy fails on mismatch of case, opp, operator, or package."""
    opp = DummyMock(opportunity_id=CONTROLLED_TEST_OPP_ID)
    pkg = DummyMock(
        package_id=CONTROLLED_TEST_PACKAGE_ID,
        package_fingerprint=CONTROLLED_TEST_PACKAGE_FP,
        status=PreparationPackageStatus.APPROVED,
    )
    ev = DummyMock(
        evidence_source=SELF_TEST_EVIDENCE_SOURCE,
        source_uri=f"{SELF_TEST_SOURCE_URI_PREFIX}{CONTROLLED_TEST_OPERATOR_ID}",
        employer_domain=SELF_TEST_EMPLOYER_DOMAIN,
        verified_recipient_email=CONTROLLED_TEST_EMAIL,
        verified_by=f"operator:{CONTROLLED_TEST_OPERATOR_ID}",
        instructions_text=f"Self-test transmission to {CONTROLLED_TEST_EMAIL}",
    )

    # 1. Wrong Case ID
    case_wrong_id = DummyMock(case_id="case-other-employer", user_id=CONTROLLED_TEST_OPERATOR_ID)
    with pytest.raises(DomainValidationError, match="locked to Case"):
        ServerEnforcedSelfTestPolicy.validate(case_wrong_id, opp, pkg, ev, CONTROLLED_TEST_EMAIL)

    # 2. Wrong Operator ID
    case_wrong_user = DummyMock(case_id=CONTROLLED_TEST_CASE_ID, user_id="operator-impostor")
    with pytest.raises(DomainValidationError, match="locked to Operator"):
        ServerEnforcedSelfTestPolicy.validate(case_wrong_user, opp, pkg, ev, CONTROLLED_TEST_EMAIL)

    # 3. Wrong Opportunity ID
    case_ok = DummyMock(case_id=CONTROLLED_TEST_CASE_ID, user_id=CONTROLLED_TEST_OPERATOR_ID)
    opp_wrong = DummyMock(opportunity_id="opp-real-employer")
    with pytest.raises(DomainValidationError, match="locked to Opportunity"):
        ServerEnforcedSelfTestPolicy.validate(case_ok, opp_wrong, pkg, ev, CONTROLLED_TEST_EMAIL)

    # 4. Wrong Package ID
    pkg_wrong_id = DummyMock(
        package_id="pkg_other",
        package_fingerprint=CONTROLLED_TEST_PACKAGE_FP,
        status=PreparationPackageStatus.APPROVED,
    )
    with pytest.raises(DomainValidationError, match="locked to Package"):
        ServerEnforcedSelfTestPolicy.validate(case_ok, opp, pkg_wrong_id, ev, CONTROLLED_TEST_EMAIL)

    # 5. Fingerprint mismatch
    pkg_wrong_fp = DummyMock(
        package_id=CONTROLLED_TEST_PACKAGE_ID,
        package_fingerprint="0000000000000000000000000000000000000000000000000000000000000000",
        status=PreparationPackageStatus.APPROVED,
    )
    with pytest.raises(DomainValidationError, match="fingerprint mismatch"):
        ServerEnforcedSelfTestPolicy.validate(case_ok, opp, pkg_wrong_fp, ev, CONTROLLED_TEST_EMAIL)

    # 6. Unapproved Package status
    pkg_frozen = DummyMock(
        package_id=CONTROLLED_TEST_PACKAGE_ID,
        package_fingerprint=CONTROLLED_TEST_PACKAGE_FP,
        status=PreparationPackageStatus.FROZEN,
    )
    with pytest.raises(DomainValidationError, match="must be APPROVED"):
        ServerEnforcedSelfTestPolicy.validate(case_ok, opp, pkg_frozen, ev, CONTROLLED_TEST_EMAIL)


def test_self_test_policy_fails_on_recipient_or_provenance_mismatch():
    """Verify ServerEnforcedSelfTestPolicy strictly locks destination and rejects external employer reuse."""
    case = DummyMock(case_id=CONTROLLED_TEST_CASE_ID, user_id=CONTROLLED_TEST_OPERATOR_ID)
    opp = DummyMock(opportunity_id=CONTROLLED_TEST_OPP_ID)
    pkg = DummyMock(
        package_id=CONTROLLED_TEST_PACKAGE_ID,
        package_fingerprint=CONTROLLED_TEST_PACKAGE_FP,
        status=PreparationPackageStatus.APPROVED,
    )

    # 1. Recipient mismatch
    ev_ok = DummyMock(
        evidence_source=SELF_TEST_EVIDENCE_SOURCE,
        source_uri=f"{SELF_TEST_SOURCE_URI_PREFIX}{CONTROLLED_TEST_OPERATOR_ID}",
        employer_domain=SELF_TEST_EMPLOYER_DOMAIN,
        verified_recipient_email=CONTROLLED_TEST_EMAIL,
        verified_by=f"operator:{CONTROLLED_TEST_OPERATOR_ID}",
        instructions_text=f"Self-test transmission to {CONTROLLED_TEST_EMAIL}",
    )
    with pytest.raises(DomainValidationError, match="does not match authorized operator mailbox"):
        ServerEnforcedSelfTestPolicy.validate(case, opp, pkg, ev_ok, "external-target@victim.org")

    # 2. Non-self-test evidence source
    ev_bad_source = DummyMock(
        evidence_source="job_posting_scrape",
        source_uri=f"{SELF_TEST_SOURCE_URI_PREFIX}{CONTROLLED_TEST_OPERATOR_ID}",
        employer_domain=SELF_TEST_EMPLOYER_DOMAIN,
        verified_recipient_email=CONTROLLED_TEST_EMAIL,
        verified_by=f"operator:{CONTROLLED_TEST_OPERATOR_ID}",
        instructions_text=f"Self-test transmission to {CONTROLLED_TEST_EMAIL}",
    )
    with pytest.raises(DomainValidationError, match="must be 'operator_self_test_v1'"):
        ServerEnforcedSelfTestPolicy.validate(case, opp, pkg, ev_bad_source, CONTROLLED_TEST_EMAIL)

    # 3. External employer domain attribution attempt
    ev_bad_emp = DummyMock(
        evidence_source=SELF_TEST_EVIDENCE_SOURCE,
        source_uri=f"{SELF_TEST_SOURCE_URI_PREFIX}{CONTROLLED_TEST_OPERATOR_ID}",
        employer_domain="external-corp.com",
        verified_recipient_email=CONTROLLED_TEST_EMAIL,
        verified_by=f"operator:{CONTROLLED_TEST_OPERATOR_ID}",
        instructions_text=f"Self-test transmission to {CONTROLLED_TEST_EMAIL}",
    )
    with pytest.raises(DomainValidationError, match="cannot be attributed to an external employer"):
        ServerEnforcedSelfTestPolicy.validate(case, opp, pkg, ev_bad_emp, CONTROLLED_TEST_EMAIL)


def test_global_ssrf_validator_remains_unmodified():
    """Verify that global ssrf_validator still rejects non-HTTP(S) schemes, URNs, and local endpoints."""
    # URNs must be rejected by standard SSRF validator
    with pytest.raises(SSRFValidationError, match="scheme"):
        validate_evidence_source_uri("urn:jackverse:controlled-test:operator-1")

    # Private IP and localhost must be rejected
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

    # Self-test policy assertions
    assert dossier["self_test_policy"]["evidence_source"] == SELF_TEST_EVIDENCE_SOURCE
    assert dossier["self_test_policy"]["source_uri"] == f"{SELF_TEST_SOURCE_URI_PREFIX}{CONTROLLED_TEST_OPERATOR_ID}"
    assert dossier["self_test_policy"]["employer_domain"] == SELF_TEST_EMPLOYER_DOMAIN
    assert dossier["self_test_policy"]["full_name_omitted"] is True
    assert dossier["self_test_policy"]["applicant_email_bound"] is True

    # Execution Plan assertions
    plan_info = dossier["execution_plan"]
    assert plan_info["status"] == ExternalExecutionPlanStatus.READY_FOR_AUTHORIZATION.value
    assert len(plan_info["unresolved_errors"]) == 0
    assert plan_info["destination_type"] == ExecutionDestinationType.EMAIL.value
    assert plan_info["canonical_url"] == f"mailto:{CONTROLLED_TEST_EMAIL}"

    # Email Manifest assertions
    manifest = dossier["email_manifest"]
    assert manifest["to_email"] == CONTROLLED_TEST_EMAIL
    assert manifest["from_email"] == CONTROLLED_TEST_EMAIL
    assert manifest["subject"] == EXACT_PROPOSED_SUBJECT
    assert len(manifest["sealed_payload_hash"]) == 64
    assert len(manifest["attachments"]) == 1
    assert manifest["attachments"][0]["filename"] == "cv_operator-1.txt"
    assert manifest["attachments"][0]["byte_size"] == 131

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

    # Direct database verification
    storage = SQLiteCaseworkerStorage(db_path=stage_b_test_db)
    with storage.unit_of_work() as uow:
        db_plan = uow.execution_plans.get_by_id(plan_info["plan_id"])
        assert db_plan is not None
        assert db_plan.status == ExternalExecutionPlanStatus.READY_FOR_AUTHORIZATION

        db_action = uow.actions.get_by_id(tx_auth["action_id"])
        assert db_action is not None
        assert db_action.status == ActionStatus.AWAITING_APPROVAL
        assert db_action.parameters.get("scope") == "external_transmission"
        assert db_action.parameters.get("authorization_schema") == "tx_auth_v1"
        assert db_action.parameters.get("plan_id") == db_plan.plan_id
        assert db_action.parameters.get("execution_plan_fingerprint") == db_plan.execution_plan_fingerprint

        db_approval = uow.approvals.get_by_id(tx_auth["approval_id"])
        assert db_approval is not None
        assert db_approval.status == ApprovalStatus.PENDING
        assert db_approval.action_fingerprint == db_action.compute_fingerprint()

    # Idempotent replay test: running prepare_stage_b again returns exact same plan and auth
    replay_dossier = prepare_stage_b(
        db_path=stage_b_test_db,
        operator_id=CONTROLLED_TEST_OPERATOR_ID,
        recipient_email=CONTROLLED_TEST_EMAIL,
    )
    assert replay_dossier["execution_plan"]["plan_id"] == plan_info["plan_id"]
    assert replay_dossier["transmission_authorization"]["action_id"] == tx_auth["action_id"]
    assert replay_dossier["transmission_authorization"]["approval_id"] == tx_auth["approval_id"]


def test_stage_b_denies_unverified_full_name_if_included_as_required(stage_b_test_db):
    """Test that if full_name is required without verified status, Vault policy denies compilation."""
    storage = SQLiteCaseworkerStorage(db_path=stage_b_test_db)
    with storage.unit_of_work() as uow:
        case = uow.cases.get_by_id(CONTROLLED_TEST_CASE_ID)
        assert case.status == CaseStatus.ACTION_REQUIRED

        opp = uow.opportunities.get_by_id(CONTROLLED_TEST_OPP_ID)
        pkg = uow.preparation_packages.get_latest_for_case(CONTROLLED_TEST_CASE_ID)
        assert pkg is not None
        artifacts = uow.preparation_artifacts.list_by_case(CONTROLLED_TEST_CASE_ID)
        claims = uow.claims.list_by_case(CONTROLLED_TEST_CASE_ID)
        facts = uow.context.list_active(user_id=CONTROLLED_TEST_OPERATOR_ID)
        va = uow.verified_email_acceptance.get_active_by_opportunity(opp.opportunity_id)

        # Construct schema requiring full_name (which is UNVERIFIED in the DB)
        bad_schema = TargetInteractionSchema(
            schema_id="schema_bad_full_name_test",
            destination_type=ExecutionDestinationType.EMAIL.value,
            fields=[
                TargetSchemaField(target_field_id="applicant_email", label="Email", field_type=TargetFieldType.EMAIL.value, required=True),
                TargetSchemaField(target_field_id="full_name", label="Full Name", field_type=TargetFieldType.TEXT.value, required=True),
            ],
            upload_slots=[
                TargetSchemaUploadSlot(slot_id="cv_slot", role="cv", label="CV", required=True),
            ],
            final_submit_control=TargetSubmitControlSpec(control_type="email_send"),
        )
        bad_bindings = [
            TargetFieldBinding(target_field_id="applicant_email", source_fact_id="fact-email-operator-1"),
            TargetFieldBinding(target_field_id="full_name", source_fact_id="fact-name-operator-1"),
        ]

        compiled = ExecutionPlanCompiler.compile_plan(
            case=case,
            opportunity=opp,
            package=pkg,
            artifacts=artifacts,
            claims=claims,
            facts=facts,
            target_schema=bad_schema,
            field_bindings=bad_bindings,
            verified_email_acceptance=va,
        )

        # Plan must fail closed with DRAFT status due to unresolved required field
        assert compiled.status == ExternalExecutionPlanStatus.DRAFT
        errors = [f for f in compiled.findings if not f.passed]
        assert any(e.code == "UNRESOLVED_REQUIRED_FIELD" for e in errors)
        assert any("fact-name-operator-1" in e.message for e in errors)


def test_execution_plan_integrity_revalidation_enforces_self_test_policy(stage_b_test_db):
    """Test that ExecutionPlanIntegrityValidator revalidates ServerEnforcedSelfTestPolicy."""
    prepare_stage_b(
        db_path=stage_b_test_db,
        operator_id=CONTROLLED_TEST_OPERATOR_ID,
        recipient_email=CONTROLLED_TEST_EMAIL,
    )
    storage = SQLiteCaseworkerStorage(db_path=stage_b_test_db)
    with storage.unit_of_work() as uow:
        case = uow.cases.get_by_id(CONTROLLED_TEST_CASE_ID)
        plan = uow.execution_plans.get_latest_for_case(CONTROLLED_TEST_CASE_ID)
        assert plan is not None

        # Revalidation with matching case & plan should pass
        res = ExecutionPlanIntegrityValidator.validate(
            uow=uow,
            case=case,
            plan=plan,
            user_id=CONTROLLED_TEST_OPERATOR_ID,
            expected_plan_status=ExternalExecutionPlanStatus.READY_FOR_AUTHORIZATION,
        )
        assert res.is_valid is True

        # Tampering: mismatching user_id fails closed
        res_bad_user = ExecutionPlanIntegrityValidator.validate(
            uow=uow,
            case=case,
            plan=plan,
            user_id="impostor-user",
            expected_plan_status=ExternalExecutionPlanStatus.READY_FOR_AUTHORIZATION,
        )
        assert res_bad_user.is_valid is False
        assert res_bad_user.failure_kind == ExecutionPlanIntegrityFailureKind.PLAN_INVALID
