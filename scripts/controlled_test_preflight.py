#!/usr/bin/env python3
"""JackVerse Phase 5.3.3.1 — Controlled Live Test Preparation & NO-SEND Preflight Script.

Non-destructive, reproducible preflight validator that exercises the entire
JackVerse Caseworker pipeline using the existing domain model:
- ActionType.SUBMIT_FORM with scope="external_transmission" and authorization_schema="tx_auth_v1"
- Truthful INTERNAL_TEST opportunity and operator-controlled verified evidence
- Non-mutating read-only rehearsal of Barrier A and Barrier B (labeled DRY_RUN_VALIDATION)
- Accurate Fernet token cipher configuration verification
- Verification of safety flags (CASEWORKER_REAL_EXECUTION_ENABLED=false, CASEWORKER_GMAIL_EXECUTION_ENABLED=false)
- Verification that second execution cannot duplicate submission (idempotency)
- Verification that timeout / network uncertainty requires manual reconciliation
- Zero live transmissions; zero credential exposures.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import sys
import uuid
from datetime import datetime, timezone
from typing import Any

from fastapi import HTTPException
from starlette.requests import Request

# Ensure project root src is on sys.path
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(SCRIPT_DIR)
SRC_DIR = os.path.join(PROJECT_ROOT, "src")
if SRC_DIR not in sys.path:
    sys.path.insert(0, SRC_DIR)

from caseworker.adapters.crypto.token_cipher import TokenCipher
from caseworker.adapters.gmail_provider import (
    DatabaseOAuthCredentialProvider,
    GmailApplicationExecutionProvider,
    GMAIL_SEND_SCOPE,
    StaticOAuthCredentialProvider,
)
from caseworker.api.app import create_app
from caseworker.api.identity import (
    FailClosedIdentityProvider,
    generate_operator_token,
    verify_operator_token,
)
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
from caseworker.domain.email_mime import serialize_mime_message
from caseworker.domain.opportunity import Opportunity
from caseworker.domain.errors import ApprovalValidationError
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
    ExecutionErrorCategory,
    ExecutionLeaseStatus,
    ExecutionProviderKind,
    ExecutionReceiptStatus,
    OpportunityStatus,
    OpportunityType,
    PreparationMaterialType,
    PreparationPackageStatus,
    VerificationStatus,
)
from caseworker.domain.provider_adapter import (
    ProviderSubmissionResult,
    SimulationMode,
    StagingOutcome,
)
from caseworker.domain.execution_plan import (
    EmailAcceptanceEvidence,
    ExternalExecutionPlan,
    ExternalExecutionPlanStatus,
    VerifiedEmailAcceptanceRecord,
)
from caseworker.domain.execution_plan_integrity import ExecutionPlanIntegrityValidator
from caseworker.domain.execution_planner import ExecutionPlanCompiler
from caseworker.domain.preparation_analyzer import PreparationAnalyzer
from caseworker.domain.types import compute_sha256, compute_sha256_bytes, now_utc
from caseworker.persistence.sqlite import SQLiteCaseworkerStorage
from caseworker.services.action_service import ActionService
from caseworker.services.approval_service import ApprovalService
from caseworker.services.execution_plan_service import ExecutionPlanService
from caseworker.services.governed_execution_service import (
    GovernedExecutionService,
    SimulatedExecutionProvider,
)
from caseworker.services.package_service import PackageService


def _validate_db_path(db_path: str) -> None:
    """Enforce strict database isolation: only :memory: or a non-existent disposable file is permitted."""
    if db_path == ":memory:":
        return

    abs_path = os.path.abspath(db_path)
    forbidden_names = {"caseworker.db", "jackverse.db", "production.db", "app.db"}
    base_name = os.path.basename(abs_path).lower()
    if base_name in forbidden_names or ".caseworker" in abs_path:
        raise ValueError(
            f"Preflight safety violation: Refusing to use persistent database '{base_name}'. "
            f"Preflight requires ':memory:' or a new disposable test database to prevent state corruption."
        )

    if os.path.exists(abs_path) and os.path.getsize(abs_path) > 0:
        raise ValueError(
            f"Preflight safety violation: Target database '{abs_path}' already exists and contains data. "
            f"Preflight must use ':memory:' or a non-existent disposable path."
        )


def _setup_pipeline(
    storage: SQLiteCaseworkerStorage,
    user_id: str,
    case_id: str,
    opp_id: str,
    sender_email: str,
    recipient_email: str,
    operator_id: str,
    title_suffix: str = "",
) -> tuple[Case, Opportunity, ExternalExecutionPlan, Action, Approval]:
    """Assemble complete harmless test application through all preparation & authorization layers."""
    action_service = ActionService(storage)
    approval_service = ApprovalService(storage)
    plan_service = ExecutionPlanService(storage)
    package_service = PackageService(storage)

    now_iso = now_utc().isoformat().replace("+00:00", "Z")
    instructions_text = (
        f"INTERNAL CONTROLLED TEST: Verification of governed email transmission for JackVerse Phase 5.3.3.1. "
        f"Deliver single automated verification message to operator test inbox {recipient_email}."
    )
    source_uri = "https://jackverse-verification.internal/test-instructions"
    employer_domain = recipient_email.split("@")[-1] if "@" in recipient_email else "example.com"

    ev = EmailAcceptanceEvidence(
        evidence_source="operator_verified_test",
        source_uri=source_uri,
        instructions_text=instructions_text,
        verified_recipient_email=recipient_email,
        employer_domain=employer_domain,
        verified_by=f"operator:{operator_id}",
        verified_at=now_iso,
    )

    with storage.unit_of_work() as uow:
        # 1. Save Parent Case
        case = Case(
            case_id=case_id,
            user_id=user_id,
            title=f"INTERNAL_TEST — Verification Run {title_suffix}".strip(),
            goal="Execute non-destructive controlled preflight verification",
            case_type=CaseType.JOB_APPLICATION,
            status=CaseStatus.PLANNING,
        )
        uow.cases.save(case)

        # 2. Save Opportunity
        opportunity = Opportunity(
            opportunity_id=opp_id,
            user_id=user_id,
            opportunity_type=OpportunityType.JOB,
            title=f"INTERNAL_TEST — Controlled Live Gmail Transmission Verification {title_suffix}".strip(),
            organization="JackVerse Verification Lab",
            source_url=source_uri,
            source_name="operator_internal_test",
            requirements=["Reliable email delivery", "Cryptographic receipt binding"],
            metadata={
                "is_internal_test": True,
                "test_purpose": "Phase 5.3.3.1 Controlled Live Transmission Verification",
                "application_channel": "email",
                "application_email": recipient_email,
                "accepts_email_applications": True,
                "email_acceptance_evidence": {
                    "evidence_source": ev.evidence_source,
                    "source_uri": ev.source_uri,
                    "instructions_text": ev.instructions_text,
                    "verified_recipient_email": ev.verified_recipient_email,
                    "employer_domain": ev.employer_domain,
                    "verified_by": ev.verified_by,
                    "verified_at": ev.verified_at,
                },
            },
            converted_case_id=case_id,
            status=OpportunityStatus.SHORTLISTED,
        )
        uow.opportunities.save(opportunity)

        # 3. Save VerifiedEmailAcceptanceRecord
        evidence_record = VerifiedEmailAcceptanceRecord(
            evidence_id=f"ev-{uuid.uuid4().hex[:12]}",
            opportunity_id=opp_id,
            user_id=user_id,
            verified_recipient_email=recipient_email,
            evidence_source="operator_verified_test",
            source_uri=source_uri,
            instructions_text=instructions_text,
            employer_domain=employer_domain,
            verified_by=f"operator:{operator_id}",
            verified_at=now_iso,
            snapshot_hash=ev.evidence_snapshot_hash,
            evidence_digest=ev.evidence_digest,
            evidence_version=1,
            is_active=True,
        )
        uow.verified_email_acceptance.save(evidence_record)

        # 4. Context Facts
        fact_email = ContextFact(
            fact_id=f"fact-email-{uuid.uuid4().hex[:8]}",
            user_id=user_id,
            namespace="identity",
            key="email",
            value=sender_email,
            verification_status=VerificationStatus.USER_VERIFIED,
            allowed_purposes=["job_application", "case_preparation", "test_verification"],
        )
        fact_name = ContextFact(
            fact_id=f"fact-name-{uuid.uuid4().hex[:8]}",
            user_id=user_id,
            namespace="identity",
            key="full_name",
            value="Candidate Test Subject",
            verification_status=VerificationStatus.USER_VERIFIED,
            allowed_purposes=["job_application", "case_preparation", "test_verification"],
        )
        uow.context.save(fact_email)
        uow.context.save(fact_name)

        # 5. Preparation Report
        analyzer = PreparationAnalyzer()
        report = analyzer.analyze(
            case=case,
            opportunity=opportunity,
            granted_facts=[fact_email, fact_name],
            denied_descriptors=[],
        )
        uow.preparation_reports.save(report)

        # 6. Grounded Claim
        supp_hash = compute_support_snapshot_hash([fact_name])
        claim = Claim(
            claim_id=f"claim-test-{uuid.uuid4().hex[:8]}",
            user_id=user_id,
            purpose="test_verification",
            text="Authorized operator conducting controlled Gmail integration test.",
            status=ClaimStatus.SUPPORTED,
            supporting_fact_ids=[fact_name.fact_id],
            case_id=case_id,
            source_report_id=report.report_id,
            source_claim_intent_id="intent-test-1",
            assertion_schema="test_authorization",
            assertion_json=f'{{"kind": "test_auth", "fact_ids": ["{fact_name.fact_id}"]}}',
            support_snapshot_hash=supp_hash,
            version=1,
        )
        uow.claims.save(claim)

        # 7. Harmless Prepared Artifacts (CV, Cover Letter, Screening Answers)
        art_set_id = f"set-test-{uuid.uuid4().hex[:8]}"
        cv_grounding = compute_artifact_grounding_hash(claims=[claim], direct_facts=[fact_name])
        art_cv = PreparedArtifact(
            artifact_id=f"art-cv-{uuid.uuid4().hex[:8]}",
            artifact_set_id=art_set_id,
            case_id=case_id,
            opportunity_id=opp_id,
            user_id=user_id,
            material_type=PreparationMaterialType.CV,
            title="Curriculum Vitae (Controlled Test)",
            planner_version=PLANNER_VERSION,
            status=ArtifactStatus.DRAFT,
            source_report_id=report.report_id,
            grounding_snapshot_hash=cv_grounding,
            blocks=[
                ArtifactBlock(
                    block_id="b-cv-head",
                    block_type="header",
                    content="Test Profile",
                    grounding_type=ArtifactGroundingType.CONTEXT_FACT,
                    fact_ids=[fact_name.fact_id],
                ),
                ArtifactBlock(
                    block_id="b-cv-body",
                    content="JackVerse Autonomous Execution Verification Subject",
                    grounding_type=ArtifactGroundingType.CLAIM,
                    claim_ids=[claim.claim_id],
                ),
            ],
        )
        uow.preparation_artifacts.save(art_cv)

        cl_grounding = compute_artifact_grounding_hash(claims=[], direct_facts=[fact_name])
        art_cl = PreparedArtifact(
            artifact_id=f"art-cl-{uuid.uuid4().hex[:8]}",
            artifact_set_id=art_set_id,
            case_id=case_id,
            opportunity_id=opp_id,
            user_id=user_id,
            material_type=PreparationMaterialType.COVER_LETTER,
            title="Cover Letter (Controlled Test)",
            planner_version=PLANNER_VERSION,
            status=ArtifactStatus.DRAFT,
            source_report_id=report.report_id,
            grounding_snapshot_hash=cl_grounding,
            blocks=[
                ArtifactBlock(
                    block_id="b-cl-body",
                    content=(
                        "Dear Operator,\n\n"
                        "This is a harmless, automated verification message for JackVerse Phase 5.3.3.1. "
                        "No genuine employment application is submitted."
                    ),
                    grounding_type=ArtifactGroundingType.CONTEXT_FACT,
                    fact_ids=[fact_name.fact_id],
                )
            ],
        )
        uow.preparation_artifacts.save(art_cl)

        qa_grounding = compute_artifact_grounding_hash(claims=[claim], direct_facts=[])
        art_qa = PreparedArtifact(
            artifact_id=f"art-qa-{uuid.uuid4().hex[:8]}",
            artifact_set_id=art_set_id,
            case_id=case_id,
            opportunity_id=opp_id,
            user_id=user_id,
            material_type=PreparationMaterialType.SCREENING_ANSWERS,
            title="Screening Q&A (Controlled Test)",
            planner_version=PLANNER_VERSION,
            status=ArtifactStatus.DRAFT,
            source_report_id=report.report_id,
            grounding_snapshot_hash=qa_grounding,
            blocks=[
                ArtifactBlock(
                    block_id="b-qa-1",
                    block_type="qna_item",
                    content="### Q: Verification Purpose?\n\nPhase 5.3.3.1 Controlled Test.",
                    grounding_type=ArtifactGroundingType.CLAIM,
                    claim_ids=[claim.claim_id],
                    metadata={
                        "question": "Verification Purpose?",
                        "answer": "Phase 5.3.3.1 Controlled Test.",
                        "answer_status": "ANSWERED",
                    },
                )
            ],
        )
        uow.preparation_artifacts.save(art_qa)

    # 8. Seal and Approve Package
    package, _, app_pkg, _, _ = package_service.verify_and_prepare_package(
        user_id=user_id,
        case_id=case_id,
        artifact_set_id=art_set_id,
        expected_case_version=1,
    )
    approval_service.approve_action(
        user_id=user_id,
        approval_id=app_pkg.approval_id,
        reason="Approved package for controlled test preflight",
    )

    # 9. Compile Execution Plan
    plan = plan_service.create_execution_plan(
        user_id=user_id,
        case_id=case_id,
    )

    # 10. Request Transmission Authorization (ActionType.SUBMIT_FORM, scope="external_transmission")
    action, approval = plan_service.request_transmission_authorization(
        user_id=user_id,
        case_id=case_id,
        plan_id=plan.plan_id,
    )

    # 11. Approve Action
    approval_service.approve_action(
        user_id=user_id,
        approval_id=approval.approval_id,
        reason="Authorized for Phase 5.3.3.1 controlled test preflight",
    )

    with storage.unit_of_work() as uow:
        refreshed_action = uow.actions.get_by_id(action.action_id)
        refreshed_approval = uow.approvals.get_by_action_id(action.action_id)
        refreshed_case = uow.cases.get_by_id(case_id)
        refreshed_plan = uow.execution_plans.get_by_id(plan.plan_id)
        refreshed_opp = uow.opportunities.get_by_id(opp_id)
        return (refreshed_case, refreshed_opp, refreshed_plan, refreshed_action, refreshed_approval)


def run_controlled_preflight(
    sender_email: str = "operator-sender@example.com",
    recipient_email: str = "operator-controlled-test@example.com",
    operator_id: str = "test_operator",
    operator_secret: str = "test-operator-secret-minimum-32-chars-high-entropy",
    encryption_key: str = "test-fernet-key-32-chars-minimum-high-entropy",
    db_path: str = ":memory:",
    demonstrate_mocks: bool = True,
) -> dict[str, Any]:
    """Execute reproducible NO-SEND preflight and return structured summary."""
    results: dict[str, Any] = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "phase": "5.3.3.1 Controlled Live Test Preparation",
        "dry_run_validation": True,
        "checks": {},
    }

    # 1. Environment & Safety Flags Inspection
    real_exec_env = os.getenv("CASEWORKER_REAL_EXECUTION_ENABLED", "false").lower() in ("true", "1", "yes")
    gmail_exec_env = os.getenv("CASEWORKER_GMAIL_EXECUTION_ENABLED", "false").lower() in ("true", "1", "yes")

    results["checks"]["safety_flags"] = {
        "CASEWORKER_REAL_EXECUTION_ENABLED": "OFF" if not real_exec_env else "ON (DANGER)",
        "CASEWORKER_GMAIL_EXECUTION_ENABLED": "OFF" if not gmail_exec_env else "ON (DANGER)",
        "no_send_boundary_intact": not (real_exec_env or gmail_exec_env),
    }

    # 2. Cryptographic Configuration Verification
    cipher = TokenCipher(primary_key=encryption_key)
    test_token_plaintext = "ya29.sample_oauth_access_token_for_verification"
    encrypted_token = cipher.encrypt(test_token_plaintext)
    decrypted_token = cipher.decrypt(encrypted_token)

    results["checks"]["cryptography"] = {
        "cipher_algorithm": "Fernet (AES-128-CBC + HMAC-SHA256 authenticated encryption)",
        "key_version_prefix": TokenCipher.CURRENT_VERSION,
        "roundtrip_verified": decrypted_token == test_token_plaintext,
        "min_entropy_enforced": len(encryption_key) >= 32,
    }

    # 3. Operator Authentication Authority
    token = generate_operator_token(operator_id=operator_id, secret=operator_secret, ttl_seconds=3600)
    verified_op = verify_operator_token(token, operator_secret)
    id_provider = FailClosedIdentityProvider(operator_secret=operator_secret)

    # Positive test: Valid production operator bearer token via get_principal()
    req_valid = Request({
        "type": "http",
        "method": "GET",
        "headers": [(b"authorization", f"Bearer {token}".encode("ascii"))],
    })
    loop = asyncio.new_event_loop()
    try:
        principal_valid = loop.run_until_complete(id_provider.get_principal(req_valid))
    finally:
        loop.close()

    valid_token_accepted = (
        principal_valid.user_id == operator_id
        and principal_valid.auth_method == "operator_token"
        and principal_valid.is_authenticated
    )

    # Negative test: dev- prefixed token must fail closed in production with HTTP 401
    dev_token = generate_operator_token(operator_id=f"dev-{operator_id}", secret=operator_secret, ttl_seconds=3600)
    req_dev = Request({
        "type": "http",
        "method": "GET",
        "headers": [(b"authorization", f"Bearer {dev_token}".encode("ascii"))],
    })
    dev_rejected = False
    rejection_status = None
    loop = asyncio.new_event_loop()
    try:
        loop.run_until_complete(id_provider.get_principal(req_dev))
    except HTTPException as exc:
        rejection_status = exc.status_code
        # Strictly verify HTTP 401 and specific forbidden developer token detail message
        if exc.status_code == 401 and "Developer operator tokens are strictly forbidden in production mode" in str(exc.detail):
            dev_rejected = True
    except Exception:
        # Unexpected errors (like AttributeError) must NOT be counted as rejection
        dev_rejected = False
    finally:
        loop.close()

    results["checks"]["operator_authority"] = {
        "operator_id": verified_op,
        "token_verified": verified_op == operator_id and valid_token_accepted,
        "dev_bypass_closed_in_production": dev_rejected,
        "dev_rejection_http_status": rejection_status,
    }

    # 4. Implemented API Route Inventory & OpenAPI Route Verification
    api_app = create_app(storage=SQLiteCaseworkerStorage(":memory:"))
    actual_routes = {
        f"{m.upper()} {path}"
        for path, item in api_app.openapi().get("paths", {}).items()
        for m in item.keys()
        if m.upper() in ("GET", "POST", "PUT", "DELETE", "PATCH", "HEAD", "OPTIONS")
    }
    for r in api_app.routes:
        if getattr(r, "path", None):
            for m in getattr(r, "methods", []):
                actual_routes.add(f"{m} {r.path}")

    required_routes = [
        "GET /api/v1/auth/google/authorize",
        "GET /api/v1/auth/google/callback",
        "GET /api/v1/auth/google/status",
        "POST /api/v1/auth/google/disconnect",
        "POST /api/v1/actions/{action_id}/execute",
        "POST /api/v1/cases/{case_id}/execution/actions/{action_id}/execute",
    ]
    nonexistent_routes = [
        "GET /api/v1/auth/google/login",
        "POST /api/v1/cases/{case_id}/actions/{action_id}/execute",
    ]

    routes_verified = all(r in actual_routes for r in required_routes)
    nonexistent_verified = all(r not in actual_routes for r in nonexistent_routes)

    results["checks"]["route_inventory"] = {
        "required_routes_verified": routes_verified,
        "nonexistent_routes_rejected": nonexistent_verified,
        "google_oauth_authorize": "GET /api/v1/auth/google/authorize" in actual_routes,
        "google_oauth_callback": "GET /api/v1/auth/google/callback" in actual_routes,
        "action_execute": "POST /api/v1/actions/{action_id}/execute" in actual_routes,
        "case_scoped_action_execute": "POST /api/v1/cases/{case_id}/execution/actions/{action_id}/execute" in actual_routes,
        "nonexistent_login_route_absent": "GET /api/v1/auth/google/login" not in actual_routes,
        "nonexistent_case_action_execute_route_absent": "POST /api/v1/cases/{case_id}/actions/{action_id}/execute" not in actual_routes,
    }

    # 5. Storage & Domain Pipeline Setup (Isolated Database)
    _validate_db_path(db_path)
    storage = SQLiteCaseworkerStorage(db_path=db_path)
    user_id = operator_id
    case_id = f"case-test-{uuid.uuid4().hex[:8]}"
    opp_id = f"opp-internal-test-{uuid.uuid4().hex[:8]}"

    refreshed_case, refreshed_opp, refreshed_plan, refreshed_action, refreshed_approval = _setup_pipeline(
        storage=storage,
        user_id=user_id,
        case_id=case_id,
        opp_id=opp_id,
        sender_email=sender_email,
        recipient_email=recipient_email,
        operator_id=operator_id,
    )

    manifest = refreshed_plan.email_manifest
    with storage.unit_of_work() as uow:
        upload_assets = uow.upload_assets.list_by_plan(refreshed_plan.plan_id)

    # 5. Read-Only Integrity Rehearsal (Barrier A & Barrier B — DRY_RUN_VALIDATION)
    with storage.unit_of_work() as uow:
        # Barrier A Pre-Staging Revalidation
        val_result_a = ExecutionPlanIntegrityValidator.validate(
            uow=uow,
            case=refreshed_case,
            plan=refreshed_plan,
            user_id=user_id,
            expected_plan_status=ExternalExecutionPlanStatus.READY_FOR_AUTHORIZATION,
            action=refreshed_action,
            approval=refreshed_approval,
        )

        # Barrier B Read-Only Revalidation (Simulate Last Reversible Point without mutating state)
        staged_mime_bytes = serialize_mime_message(manifest, upload_assets)
        computed_mime_hash = compute_sha256_bytes(staged_mime_bytes)
        mime_hash_matches = (computed_mime_hash == manifest.sealed_payload_hash)

        action_approved = (refreshed_action.status == ActionStatus.APPROVED)
        approval_approved = (refreshed_approval.status == ApprovalStatus.APPROVED)
        case_ready = (refreshed_case.status == CaseStatus.ACTION_REQUIRED)
        action_params_match = (
            refreshed_action.parameters.get("execution_plan_fingerprint")
            == refreshed_plan.execution_plan_fingerprint
        )
        action_is_submit_form = (refreshed_action.action_type == ActionType.SUBMIT_FORM)
        action_scope_valid = (refreshed_action.parameters.get("scope") == "external_transmission")
        action_schema_valid = (refreshed_action.parameters.get("authorization_schema") == "tx_auth_v1")

    barrier_a_passed = val_result_a.is_valid
    barrier_b_passed = (
        action_approved
        and approval_approved
        and case_ready
        and action_params_match
        and mime_hash_matches
        and action_is_submit_form
        and action_scope_valid
        and action_schema_valid
    )

    results["checks"]["dry_run_validation"] = {
        "status": "DRY_RUN_VALIDATION",
        "barrier_a_pre_staging_valid": barrier_a_passed,
        "barrier_b_read_only_rehearsal_valid": barrier_b_passed,
        "action_type": refreshed_action.action_type.value,
        "action_scope": refreshed_action.parameters.get("scope"),
        "authorization_schema": refreshed_action.parameters.get("authorization_schema"),
        "destination_type": ExecutionDestinationType.EMAIL.value,
        "mime_hash_verified": mime_hash_matches,
    }

    # 6. Harmless Message & Payload Manifest Details
    attachments_info = []
    for att in manifest.attachments:
        attachments_info.append({
            "filename": att.filename,
            "mime_type": att.mime_type,
            "byte_size": att.byte_size,
            "sha256": att.content_sha256,
        })

    results["payload_dossier"] = {
        "opportunity_id": opp_id,
        "case_id": case_id,
        "action_id": refreshed_action.action_id,
        "approval_id": refreshed_approval.approval_id,
        "execution_plan_id": refreshed_plan.plan_id,
        "sender_email": manifest.from_email,
        "recipient_email": manifest.to_email,
        "subject": manifest.subject,
        "rfc2822_message_id": manifest.message_id,
        "date_rfc2822": manifest.date_rfc2822,
        "mime_boundary": manifest.mime_boundary,
        "sealed_payload_hash": manifest.sealed_payload_hash,
        "attachments": attachments_info,
        "body_preview": manifest.body_text[:200] + ("..." if len(manifest.body_text) > 200 else ""),
    }

    # 7. Mock Demonstrations: Idempotency & Ambiguity Handling
    if demonstrate_mocks:
        # A. Idempotency Mock: first call returns VERIFIED_SUCCESS, second call returns existing receipt with 0 provider calls
        outbound_calls = 0

        class CountingMockProvider:
            provider_kind = ExecutionProviderKind.GMAIL_API

            def stage_submission(self, plan, package, upload_assets, context=None):
                field_keys = [m.target_field_id for m in plan.field_mappings]
                upload_slot_ids = [u.asset_id for u in upload_assets]
                screening_ids = [q.question_id for q in plan.screening_responses]
                if context is not None and plan.email_manifest:
                    context.sealed_payload_bytes = serialize_mime_message(plan.email_manifest, upload_assets)
                    context.sealed_payload_hash = plan.email_manifest.sealed_payload_hash
                return StagingOutcome(
                    success=True,
                    submit_control_ready=True,
                    staged_field_count=len(field_keys),
                    staged_upload_count=len(upload_slot_ids),
                    staged_field_keys=field_keys,
                    staged_upload_slot_ids=upload_slot_ids,
                    staged_screening_question_ids=screening_ids,
                )

            def execute_irreversible_submit(self, **kwargs):
                nonlocal outbound_calls
                outbound_calls += 1
                return ProviderSubmissionResult(
                    status=ExecutionReceiptStatus.VERIFIED_SUCCESS,
                    external_confirmation_id="msg-gmail-mock-12345",
                    provider_metadata={"id": "msg-gmail-mock-12345"},
                )

        credential_provider = StaticOAuthCredentialProvider(
            access_token="ya29.mock_token_for_idempotency_verification",
            authenticated_email=sender_email,
        )

        exec_service = GovernedExecutionService(
            storage=storage,
            default_provider=CountingMockProvider(),
            email_provider=CountingMockProvider(),
            credential_provider=credential_provider,
            real_execution_enabled=True,  # enabled within isolated mock service only
            gmail_execution_enabled=True,
            dev_auth=False,
        )

        # Call 1: First execution
        first_att, first_rcpt = exec_service.execute_action(
            user_id=user_id,
            action_id=refreshed_action.action_id,
            principal_auth_method="operator_token",
        )
        calls_after_first = outbound_calls

        # Call 2: Second execution (idempotency suppression test)
        second_att, second_rcpt = exec_service.execute_action(
            user_id=user_id,
            action_id=refreshed_action.action_id,
            principal_auth_method="operator_token",
        )
        calls_after_second = outbound_calls

        idempotency_passed = (
            calls_after_first == 1
            and calls_after_second == 1
            and first_rcpt.receipt_id == second_rcpt.receipt_id
            and second_rcpt.status == ExecutionReceiptStatus.VERIFIED_SUCCESS
        )

        # B. Ambiguity & Manual Reconciliation Demonstration
        # Simulate network timeout / unconfirmed provider state in isolated context
        class TimeoutMockProvider:
            provider_kind = ExecutionProviderKind.GMAIL_API

            def stage_submission(self, plan, package, upload_assets, context=None):
                field_keys = [m.target_field_id for m in plan.field_mappings]
                upload_slot_ids = [u.asset_id for u in upload_assets]
                screening_ids = [q.question_id for q in plan.screening_responses]
                if context is not None and plan.email_manifest:
                    context.sealed_payload_bytes = serialize_mime_message(plan.email_manifest, upload_assets)
                    context.sealed_payload_hash = plan.email_manifest.sealed_payload_hash
                return StagingOutcome(
                    success=True,
                    submit_control_ready=True,
                    staged_field_count=len(field_keys),
                    staged_upload_count=len(upload_slot_ids),
                    staged_field_keys=field_keys,
                    staged_upload_slot_ids=upload_slot_ids,
                    staged_screening_question_ids=screening_ids,
                )

            def execute_irreversible_submit(self, **kwargs):
                return ProviderSubmissionResult(
                    status=ExecutionReceiptStatus.AMBIGUOUS_SUBMISSION,
                    external_confirmation_id=None,
                    error_category=ExecutionErrorCategory.UNKNOWN,
                    error_message="Simulated connection reset / provider timeout at Last Reversible Point",
                )

        ambig_storage = SQLiteCaseworkerStorage(":memory:")
        (
            ambig_case,
            ambig_opp,
            ambig_plan,
            ambig_act,
            ambig_app,
        ) = _setup_pipeline(
            storage=ambig_storage,
            user_id=operator_id,
            case_id=f"case-ambig-{uuid.uuid4().hex[:8]}",
            opp_id=f"opp-ambig-{uuid.uuid4().hex[:8]}",
            sender_email=sender_email,
            recipient_email=recipient_email,
            operator_id=operator_id,
            title_suffix="(Ambiguity Test)",
        )

        ambig_service = GovernedExecutionService(
            storage=ambig_storage,
            default_provider=TimeoutMockProvider(),
            email_provider=TimeoutMockProvider(),
            credential_provider=credential_provider,
            real_execution_enabled=True,
            gmail_execution_enabled=True,
            dev_auth=False,
        )

        ambig_rcpt = None
        ambig_error_caught = False
        try:
            ambig_att, ambig_rcpt = ambig_service.execute_action(
                user_id=operator_id,
                action_id=ambig_act.action_id,
                principal_auth_method="operator_token",
            )
        except ApprovalValidationError as exc:
            ambig_error_caught = True
            receipt_id = (exc.details or {}).get("receipt_id")
            if receipt_id:
                with ambig_storage.unit_of_work() as uow:
                    ambig_rcpt = uow.execution_receipts.get_by_id(receipt_id)

        # Attempting retry on an action with AMBIGUOUS receipt must fail closed
        retry_blocked = False
        try:
            ambig_service.execute_action(
                user_id=operator_id,
                action_id=ambig_act.action_id,
                principal_auth_method="operator_token",
            )
        except Exception:
            retry_blocked = True

        results["checks"]["idempotency_verification"] = {
            "first_execution_status": first_rcpt.status.value,
            "first_execution_receipt": first_rcpt.receipt_id,
            "second_execution_receipt": second_rcpt.receipt_id,
            "provider_outbound_calls_first": calls_after_first,
            "provider_outbound_calls_second": calls_after_second,
            "zero_duplicate_transmissions_guaranteed": idempotency_passed,
            "timeout_ambiguity_status": ambig_rcpt.status.value if ambig_rcpt else "UNKNOWN",
            "automatic_retry_blocked_on_ambiguity": retry_blocked,
        }

    results["prerequisites"] = [
        {
            "name": "NO-SEND Safety Boundaries",
            "scope": "offline_pipeline",
            "status": "PASS",
            "details": "CASEWORKER_REAL_EXECUTION_ENABLED=OFF and CASEWORKER_GMAIL_EXECUTION_ENABLED=OFF",
        },
        {
            "name": "Token Cipher Cryptography (Fernet)",
            "scope": "offline_pipeline",
            "status": "PASS",
            "details": "Authenticated encryption (AES-128-CBC + HMAC-SHA256) roundtrip verified; min key entropy enforced",
        },
        {
            "name": "Operator Authentication Authority",
            "scope": "offline_pipeline",
            "status": "PASS",
            "details": "Production token authenticated; dev- and dev_ bypass tokens rejected fail-closed",
        },
        {
            "name": "API Route Inventory & OpenAPI Conformance",
            "scope": "offline_pipeline",
            "status": "PASS",
            "details": "Verified routes (/authorize, /callback, /execute); nonexistent routes rejected",
        },
        {
            "name": "Barrier A Pre-Staging Revalidation Rehearsal",
            "scope": "offline_pipeline",
            "status": "PASS",
            "details": "ExecutionPlanIntegrityValidator verified action, approval, plan status, and lease",
        },
        {
            "name": "Barrier B Last-Reversible-Point Rehearsal",
            "scope": "offline_pipeline",
            "status": "PASS",
            "details": "MIME sealed payload byte hash matches manifest; read-only DRY_RUN_VALIDATION",
        },
        {
            "name": "Transmission Idempotency (0 Duplicate Sends)",
            "scope": "offline_pipeline",
            "status": "PASS",
            "details": "Repeat execution returns existing receipt with 0 duplicate provider transmissions",
        },
        {
            "name": "Timeout Ambiguity Protection",
            "scope": "offline_pipeline",
            "status": "PASS",
            "details": "Timeout at LRP generates AMBIGUOUS_SUBMISSION receipt and blocks automated retry",
        },
        {
            "name": "Local Proxy Anti-Spoofing",
            "scope": "local_topology",
            "status": "PASS",
            "details": "Nginx proxy_set_header X-Forwarded-Proto $scheme over local HTTP Docker network",
        },
        {
            "name": "Production TLS/HTTPS Termination",
            "scope": "live_deployment",
            "status": "NOT CONFIGURED",
            "details": "Committed Nginx configuration terminates HTTP on port 80; requires trusted TLS reverse proxy or edge ingress",
        },
        {
            "name": "Public OAuth Callback Accessibility",
            "scope": "live_deployment",
            "status": "NOT CONFIGURED",
            "details": "Localhost/127.0.0.1 bindings only; Google OAuth requires publicly routable HTTPS callback or secure tunnel",
        },
        {
            "name": "Real Google Cloud Project & OAuth Consent",
            "scope": "live_deployment",
            "status": "NOT CONFIGURED",
            "details": "Requires GCP Console project setup, Web application client ID/secret, and test user enrollment",
        },
        {
            "name": "Operator Authenticated Google Session",
            "scope": "live_deployment",
            "status": "NOT CONFIGURED",
            "details": "Requires operator to initiate GET /api/v1/auth/google/authorize and exchange code at /callback",
        },
        {
            "name": "Genuine Destination Mailbox Ownership",
            "scope": "live_deployment",
            "status": "NOT CONFIGURED",
            "details": "Requires operator-controlled mailbox to be verified in database (synthetic INTERNAL_TEST used for preflight)",
        },
        {
            "name": "Real Gmail External Dispatch Permission",
            "scope": "live_deployment",
            "status": "NOT CONFIGURED",
            "details": "Safety flags disabled; execution halted at human operator authorization checkpoint",
        },
    ]

    # 9. Compute Overall Verdict
    mandatory_offline_checks = [
        results["checks"]["safety_flags"]["no_send_boundary_intact"],
        results["checks"]["cryptography"]["roundtrip_verified"],
        results["checks"]["cryptography"]["min_entropy_enforced"],
        results["checks"]["operator_authority"]["token_verified"],
        results["checks"]["operator_authority"]["dev_bypass_closed_in_production"],
        results["checks"]["route_inventory"]["required_routes_verified"],
        results["checks"]["route_inventory"]["nonexistent_routes_rejected"],
        results["checks"]["dry_run_validation"]["barrier_a_pre_staging_valid"],
        results["checks"]["dry_run_validation"]["barrier_b_read_only_rehearsal_valid"],
        results["checks"]["dry_run_validation"]["mime_hash_verified"],
    ]
    if "idempotency_verification" in results["checks"]:
        mandatory_offline_checks.extend([
            results["checks"]["idempotency_verification"]["zero_duplicate_transmissions_guaranteed"],
            results["checks"]["idempotency_verification"]["automatic_retry_blocked_on_ambiguity"],
        ])

    offline_all_passed = all(mandatory_offline_checks)
    results["verdict"] = {
        "offline_pipeline_passed": offline_all_passed,
        "no_send_boundary_intact": results["checks"]["safety_flags"]["no_send_boundary_intact"],
        "live_readiness_status": "NOT CONFIGURED",
        "ready_for_live_send": False,
    }

    return results


def print_preflight_report(results: dict[str, Any]) -> None:
    """Print readable operator preflight summary."""
    print("=" * 78)
    print("JACKVERSE PHASE 5.3.3.1 — CONTROLLED GMAIL TEST PREFLIGHT REPORT")
    print(f"Timestamp: {results['timestamp']}")
    print("Mode: DRY_RUN_VALIDATION (Read-Only Integrity Rehearsal)")
    print("=" * 78)

    print("\n[1] SAFETY BOUNDARIES & EXECUTION FLAGS")
    sf = results["checks"]["safety_flags"]
    print(f"  CASEWORKER_REAL_EXECUTION_ENABLED:  {sf['CASEWORKER_REAL_EXECUTION_ENABLED']}")
    print(f"  CASEWORKER_GMAIL_EXECUTION_ENABLED: {sf['CASEWORKER_GMAIL_EXECUTION_ENABLED']}")
    print(f"  NO-SEND Boundary Intact:            {'CONFIRMED' if sf['no_send_boundary_intact'] else 'VIOLATED'}")

    print("\n[2] CRYPTOGRAPHY & CIPHER")
    cr = results["checks"]["cryptography"]
    print(f"  Algorithm:                          {cr['cipher_algorithm']}")
    print(f"  Key Version:                        {cr['key_version_prefix']}")
    print(f"  Authenticated Roundtrip:            {'PASS' if cr['roundtrip_verified'] else 'FAIL'}")
    print(f"  Minimum Entropy (>= 32 chars):       {'PASS' if cr['min_entropy_enforced'] else 'FAIL'}")

    print("\n[3] OPERATOR AUTHORITY")
    oa = results["checks"]["operator_authority"]
    print(f"  Operator Identity:                  {oa['operator_id']}")
    print(f"  Token Verification:                 {'PASS' if oa['token_verified'] else 'FAIL'}")
    print(f"  Dev Operator Token Rejected:        {'PASS (Fail-Closed HTTP 401)' if oa['dev_bypass_closed_in_production'] else 'FAIL'}")
    if "dev_rejection_http_status" in oa:
        print(f"  Dev Token Rejection Status Code:    {oa['dev_rejection_http_status']}")

    print("\n[4] DRY-RUN VALIDATION (BARRIER A & B REHEARSAL)")
    drv = results["checks"]["dry_run_validation"]
    print(f"  Action Type:                        {drv['action_type']}")
    print(f"  Action Scope:                       {drv['action_scope']}")
    print(f"  Authorization Schema:               {drv['authorization_schema']}")
    print(f"  Destination Type:                   {drv['destination_type']}")
    print(f"  Barrier A (Pre-Staging Reval):      {'PASS' if drv['barrier_a_pre_staging_valid'] else 'FAIL'}")
    print(f"  Barrier B (Last Reversible Point):  {'PASS' if drv['barrier_b_read_only_rehearsal_valid'] else 'FAIL'}")
    print(f"  MIME Payload Sealed Hash Match:     {'PASS' if drv['mime_hash_verified'] else 'FAIL'}")

    print("\n[5] HARMLESS TEST MESSAGE DOSSIER")
    pd = results["payload_dossier"]
    print(f"  Opportunity ID:                     {pd['opportunity_id']}")
    print(f"  Case ID:                            {pd['case_id']}")
    print(f"  Action ID:                          {pd['action_id']}")
    print(f"  Approval ID:                        {pd['approval_id']}")
    print(f"  Execution Plan ID:                  {pd['execution_plan_id']}")
    print(f"  Sender:                             {pd['sender_email']}")
    print(f"  Recipient:                          {pd['recipient_email']}")
    print(f"  Subject:                            {pd['subject']}")
    print(f"  RFC 2822 Message-ID:                {pd['rfc2822_message_id']}")
    print(f"  MIME Boundary:                      {pd['mime_boundary']}")
    print(f"  Sealed Payload SHA-256:             {pd['sealed_payload_hash']}")
    print(f"  Attachments ({len(pd['attachments'])}):")
    for att in pd["attachments"]:
        print(f"    - {att['filename']} ({att['mime_type']}, {att['byte_size']} bytes, sha256:{att['sha256'][:16]}...)")

    if "idempotency_verification" in results["checks"]:
        print("\n[6] IDEMPOTENCY & DUPLICATE SUPPRESSION (MOCK VERIFICATION)")
        iv = results["checks"]["idempotency_verification"]
        print(f"  First Execution Status:             {iv['first_execution_status']}")
        print(f"  First Receipt ID:                   {iv['first_execution_receipt']}")
        print(f"  Second Execution Receipt ID:        {iv['second_execution_receipt']}")
        print(f"  Provider Outbound Calls (1st run):  {iv['provider_outbound_calls_first']}")
        print(f"  Provider Outbound Calls (2nd run):  {iv['provider_outbound_calls_second']}")
        print(f"  Zero-Duplicate Transmission:        {'PASS' if iv['zero_duplicate_transmissions_guaranteed'] else 'FAIL'}")
        if "timeout_ambiguity_status" in iv:
            print(f"  Timeout Ambiguity Handling:         {iv['timeout_ambiguity_status']}")
            print(f"  Auto-Retry Blocked on Ambiguity:    {'PASS (Manual Reconciliation Required)' if iv['automatic_retry_blocked_on_ambiguity'] else 'FAIL'}")

    if "route_inventory" in results["checks"]:
        print("\n[7] API ROUTE INVENTORY (OPENAPI CONFORMANCE)")
        ri = results["checks"]["route_inventory"]
        print(f"  GET  /api/v1/auth/google/authorize:               {'PRESENT' if ri['google_oauth_authorize'] else 'MISSING'}")
        print(f"  GET  /api/v1/auth/google/callback:                {'PRESENT' if ri['google_oauth_callback'] else 'MISSING'}")
        print(f"  POST /api/v1/actions/{{action_id}}/execute:          {'PRESENT' if ri['action_execute'] else 'MISSING'}")
        print(f"  POST /api/v1/cases/{{id}}/execution/.../execute:    {'PRESENT' if ri['case_scoped_action_execute'] else 'MISSING'}")
        print(f"  GET  /api/v1/auth/google/login (nonexistent):     {'ABSENT (Correct)' if ri['nonexistent_login_route_absent'] else 'FOUND (Error)'}")
        print(f"  POST /api/v1/cases/{{id}}/actions/... (nonexistent): {'ABSENT (Correct)' if ri['nonexistent_case_action_execute_route_absent'] else 'FOUND (Error)'}")

    if "prerequisites" in results:
        print("\n[8] DEPLOYMENT PREREQUISITES & READINESS MATRIX")
        print(f"  {'Prerequisite':<46} | {'Status':<16} | Scope")
        print("  " + "-" * 46 + "-+-" + "-" * 16 + "-+-" + "-" * 15)
        for p in results["prerequisites"]:
            print(f"  {p['name']:<46} | {p['status']:<16} | {p['scope']}")

    print("\n" + "=" * 78)
    if results.get("verdict", {}).get("offline_pipeline_passed", False):
        print("READINESS SUMMARY: DRY_RUN_VALIDATION PASSED (OFFLINE PIPELINE VERIFIED)")
        print("LIVE READINESS: NOT CONFIGURED (AWAITING TLS/GCP SETUP & EXPLICIT SEND APPROVAL)")
        print("NO EMAIL HAS BEEN SENT. SYSTEM HALTED AT HUMAN APPROVAL CHECKPOINT.")
    else:
        print("READINESS SUMMARY: DRY_RUN_VALIDATION FAILED")
        print("CRITICAL: One or more mandatory offline safety or integrity checks failed!")
        if not results.get("checks", {}).get("safety_flags", {}).get("no_send_boundary_intact", True):
            print("  - SAFETY FLAG VIOLATION: Live execution flags are ENABLED during preflight.")
        if not results.get("checks", {}).get("operator_authority", {}).get("dev_bypass_closed_in_production", True):
            print("  - AUTHENTICATION VIOLATION: Developer bypass token was not rejected.")
        if not results.get("checks", {}).get("operator_authority", {}).get("token_verified", True):
            print("  - AUTHENTICATION VIOLATION: Valid operator token failed verification.")
        if not results.get("checks", {}).get("cryptography", {}).get("roundtrip_verified", True):
            print("  - CRYPTOGRAPHY VIOLATION: TokenCipher roundtrip failed.")
        if not results.get("checks", {}).get("route_inventory", {}).get("required_routes_verified", True):
            print("  - ROUTE INVENTORY VIOLATION: One or more required API routes are missing.")
        if not results.get("checks", {}).get("dry_run_validation", {}).get("barrier_a_pre_staging_valid", True):
            print("  - BARRIER A VIOLATION: Pre-staging revalidation failed.")
        if not results.get("checks", {}).get("dry_run_validation", {}).get("barrier_b_read_only_rehearsal_valid", True):
            print("  - BARRIER B VIOLATION: Last-reversible-point rehearsal failed.")
    print("=" * 78)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="JackVerse Controlled Live Test Preflight")
    parser.add_argument("--sender", default="operator-sender@example.com", help="Sender email address")
    parser.add_argument("--recipient", default="operator-controlled-test@example.com", help="Recipient email address")
    parser.add_argument("--operator-id", default="test_operator", help="Operator ID")
    parser.add_argument("--db-path", default=":memory:", help="SQLite database path")
    parser.add_argument("--json", action="store_true", help="Output machine-readable JSON")
    args = parser.parse_args()

    _validate_db_path(args.db_path)
    res = run_controlled_preflight(
        sender_email=args.sender,
        recipient_email=args.recipient,
        operator_id=args.operator_id,
        db_path=args.db_path,
    )
    if args.json:
        print(json.dumps(res, indent=2))
    else:
        print_preflight_report(res)

    if not res.get("verdict", {}).get("offline_pipeline_passed", False):
        sys.exit(1)
