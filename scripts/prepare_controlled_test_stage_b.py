#!/usr/bin/env python3
"""JackVerse Phase 5.3.3.2 — Governed Gmail Transmission Acceptance Preparation: Stage B.

Compiles an authoritative ExternalExecutionPlan and requests human transmission authorization
for the approved Stage A package under server-enforced internal self-test policy constraints:
- Confined strictly to Case case-controlled-test-5332, Opportunity opp-controlled-test-5332,
  Operator operator-1, Package pkg_d17af0209e42 (fingerprint 1ebfd0bbaf5c371b089d6a412fb8fc0115b9c37bab834fb7845d9f92846dea53),
  and verified recipient dagogograntjack@gmail.com.
- SSRF validator remains unchanged; self-test provenance uses dedicated ServerEnforcedSelfTestPolicy.
- Dedicated TargetInteractionSchema requires applicant_email (bound to verified OAuth email fact)
  and omits synthetic full_name to avoid Vault access policy denial.
- Compiles ExternalExecutionPlan in status READY_FOR_AUTHORIZATION.
- Requests transmission authorization via ExecutionPlanService.request_transmission_authorization(),
  creating Action in AWAITING_APPROVAL and Approval in PENDING.
- STOPS before transmission. Real Gmail sends remain 0.
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from datetime import datetime, timezone
from typing import Any

# Ensure project root src is on sys.path
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(SCRIPT_DIR)
SRC_DIR = os.path.join(PROJECT_ROOT, "src")
if SRC_DIR not in sys.path:
    sys.path.insert(0, SRC_DIR)

from caseworker.adapters.crypto.token_cipher import TokenCipher
from caseworker.domain.action import Action
from caseworker.domain.approval import Approval
from caseworker.domain.case import Case
from caseworker.domain.enums import (
    ActionStatus,
    ActionType,
    ApprovalStatus,
    CaseStatus,
    CaseType,
    ExecutionDestinationType,
    ExternalExecutionPlanStatus,
    PreparationPackageStatus,
    TargetFieldType,
    TargetSchemaProvenance,
    VerificationStatus,
)
from caseworker.domain.errors import (
    ApprovalValidationError,
    DomainValidationError,
    EntityNotFoundError,
)
from caseworker.domain.execution_plan import (
    EmailAcceptanceEvidence,
    ExternalExecutionPlan,
    TargetFieldBinding,
    TargetInteractionSchema,
    TargetSchemaField,
    TargetSchemaUploadSlot,
    TargetSubmitControlSpec,
    VerifiedEmailAcceptanceRecord,
)
from caseworker.domain.package_integrity import PreparationPackageIntegrityValidator
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
from caseworker.domain.types import compute_sha256, now_utc
from caseworker.persistence.sqlite import SQLiteCaseworkerStorage
from caseworker.services.execution_plan_service import ExecutionPlanService


def verify_runtime_safety(db_path: str, operator_id: str = CONTROLLED_TEST_OPERATOR_ID) -> dict[str, Any]:
    """Verify fail-closed execution flags and authentic Google OAuth connection."""
    # 1. Execution flags
    real_exec = os.getenv("CASEWORKER_REAL_EXECUTION_ENABLED", "false").lower() in ("true", "1", "yes")
    gmail_exec = os.getenv("CASEWORKER_GMAIL_EXECUTION_ENABLED", "false").lower() in ("true", "1", "yes")
    if real_exec or gmail_exec:
        raise RuntimeError(
            f"SAFETY VIOLATION: Live execution flags must be disabled during Stage B preparation! "
            f"CASEWORKER_REAL_EXECUTION_ENABLED={real_exec}, CASEWORKER_GMAIL_EXECUTION_ENABLED={gmail_exec}"
        )

    # 2. Check credentials in DB
    if not os.path.exists(db_path):
        raise FileNotFoundError(f"Database file does not exist at {db_path}")

    conn = sqlite3.connect(db_path, timeout=5.0)
    cur = conn.cursor()
    table_name = "user_oauth_credentials"
    tables = [r[0] for r in cur.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
    if "user_oauth_credentials" in tables:
        table_name = "user_oauth_credentials"
    elif "oauth_credentials" in tables:
        table_name = "oauth_credentials"
    else:
        conn.close()
        raise RuntimeError(f"No OAuth credentials table found in {db_path}")

    row = cur.execute(
        f"SELECT authenticated_email, scopes, token_expiry, access_token_enc, refresh_token_enc "
        f"FROM {table_name} WHERE user_id = ? AND provider = 'gmail'",
        (operator_id,),
    ).fetchone()
    conn.close()

    if not row:
        raise RuntimeError(f"No Google OAuth connection found for user '{operator_id}' in {db_path}")

    auth_email, scopes_raw, token_expiry, access_tok_enc, refresh_tok_enc = row
    if not auth_email:
        raise RuntimeError("Connected Google account record does not have a verified email address.")

    scopes = [s.strip() for s in scopes_raw.split() if s.strip()]
    required_scopes = [
        "openid",
        "https://www.googleapis.com/auth/userinfo.email",
        "https://www.googleapis.com/auth/gmail.send",
    ]
    missing_scopes = [s for s in required_scopes if not any(s in sc for sc in scopes)]
    if missing_scopes:
        raise RuntimeError(f"Google connection lacks required scopes: {missing_scopes}")

    return {
        "operator_id": operator_id,
        "authenticated_email": auth_email,
        "scopes": scopes,
        "token_expiry": token_expiry,
        "real_execution_enabled": real_exec,
        "gmail_execution_enabled": gmail_exec,
    }


def prepare_stage_b(
    db_path: str,
    operator_id: str = CONTROLLED_TEST_OPERATOR_ID,
    recipient_email: str | None = None,
) -> dict[str, Any]:
    """Execute Stage B: Compile execution plan and request transmission authorization."""
    # 1. Safety & Credential verification
    safety = verify_runtime_safety(db_path, operator_id)
    connected_email = safety["authenticated_email"].strip().lower()

    if recipient_email is None:
        recipient_email = connected_email
    else:
        recipient_email = recipient_email.strip().lower()

    if recipient_email != CONTROLLED_TEST_EMAIL:
        raise DomainValidationError(
            f"Recipient '{recipient_email}' does not match controlled test recipient '{CONTROLLED_TEST_EMAIL}'."
        )

    if connected_email != CONTROLLED_TEST_EMAIL:
        raise DomainValidationError(
            f"Connected OAuth email '{connected_email}' does not match controlled test recipient '{CONTROLLED_TEST_EMAIL}'."
        )

    storage = SQLiteCaseworkerStorage(db_path)
    plan_service = ExecutionPlanService(storage)

    with storage.unit_of_work() as uow:
        # 2. Case & Opportunity verification
        case = uow.cases.get_by_id(CONTROLLED_TEST_CASE_ID)
        if case is None:
            raise EntityNotFoundError("Case", CONTROLLED_TEST_CASE_ID)
        if case.user_id != operator_id:
            raise DomainValidationError(f"Case owner '{case.user_id}' does not match '{operator_id}'.")
        if case.case_type != CaseType.JOB_APPLICATION:
            raise DomainValidationError(f"Case type '{case.case_type}' is not JOB_APPLICATION.")

        opp = uow.opportunities.get_by_id(CONTROLLED_TEST_OPP_ID)
        if opp is None:
            raise EntityNotFoundError("Opportunity", CONTROLLED_TEST_OPP_ID)

        # 3. Target Approved Preparation Package verification
        pkg = uow.preparation_packages.get_by_id(CONTROLLED_TEST_PACKAGE_ID)
        if pkg is None:
            raise EntityNotFoundError("PreparationPackage", CONTROLLED_TEST_PACKAGE_ID)
        if pkg.user_id != operator_id or pkg.case_id != case.case_id:
            raise DomainValidationError("Preparation package does not belong to specified case/user.")
        if pkg.package_fingerprint != CONTROLLED_TEST_PACKAGE_FP:
            raise DomainValidationError("Preparation package fingerprint does not match approved Stage A fingerprint.")
        if pkg.status != PreparationPackageStatus.APPROVED:
            raise DomainValidationError(f"Preparation package is '{pkg.status.value}', expected APPROVED.")

        # Revalidate package integrity
        pkg_integrity = PreparationPackageIntegrityValidator.validate(
            uow=uow,
            case=case,
            package=pkg,
            user_id=operator_id,
            expected_package_status=PreparationPackageStatus.APPROVED,
        )
        if not pkg_integrity.is_valid:
            raise DomainValidationError(f"Approved package failed integrity revalidation: {pkg_integrity.reason}")

        # 4. Verified Email Acceptance Record (Self-Test Policy)
        existing_va = uow.verified_email_acceptance.get_active_by_opportunity(opp.opportunity_id)
        if existing_va is None:
            instructions = f"Controlled internal test transmission to {CONTROLLED_TEST_EMAIL}"
            snap_hash = compute_sha256(instructions.strip())
            ev_source_uri = f"{SELF_TEST_SOURCE_URI_PREFIX}{operator_id}"
            verified_at = now_utc().isoformat()
            verified_by = f"operator:{operator_id}"

            temp_evidence = EmailAcceptanceEvidence(
                evidence_source=SELF_TEST_EVIDENCE_SOURCE,
                source_uri=ev_source_uri,
                instructions_text=instructions,
                verified_recipient_email=CONTROLLED_TEST_EMAIL,
                employer_domain=SELF_TEST_EMPLOYER_DOMAIN,
                evidence_snapshot_hash=snap_hash,
                verified_by=verified_by,
                verified_at=verified_at,
            )

            va_record = VerifiedEmailAcceptanceRecord(
                evidence_id=f"ev_self_test_{opp.opportunity_id}",
                opportunity_id=opp.opportunity_id,
                user_id=operator_id,
                evidence_source=SELF_TEST_EVIDENCE_SOURCE,
                source_uri=ev_source_uri,
                instructions_text=instructions,
                verified_recipient_email=CONTROLLED_TEST_EMAIL,
                employer_domain=SELF_TEST_EMPLOYER_DOMAIN,
                snapshot_hash=snap_hash,
                evidence_digest=temp_evidence.evidence_digest,
                verified_by=verified_by,
                verified_at=verified_at,
                evidence_version=1,
                is_active=True,
            )
            uow.verified_email_acceptance.save(va_record)
            uow.commit()
            verified_acceptance = va_record
        else:
            # Revalidate existing record against server policy invariants
            ev = existing_va.to_evidence()
            ServerEnforcedSelfTestPolicy.validate(
                case=case,
                opportunity=opp,
                package=pkg,
                evidence=ev,
                clean_dest_email=CONTROLLED_TEST_EMAIL,
            )
            verified_acceptance = existing_va

    # 5. Define Dedicated TargetInteractionSchema
    target_schema = TargetInteractionSchema(
        schema_id=f"schema_controlled_test_{CONTROLLED_TEST_CASE_ID[-4:]}",
        destination_type=ExecutionDestinationType.EMAIL.value,
        target_schema_provenance=TargetSchemaProvenance.USER_PROVIDED.value,
        fields=[
            TargetSchemaField(
                target_field_id="applicant_email",
                label="Applicant Email",
                field_type=TargetFieldType.EMAIL.value,
                required=True,
            ),
        ],
        upload_slots=[
            TargetSchemaUploadSlot(
                slot_id="cv_slot",
                role="cv",
                label="Curriculum Vitae / Resume",
                required=True,
                accepted_mime_types=["application/pdf", "text/plain"],
            ),
        ],
        final_submit_control=TargetSubmitControlSpec(
            control_type="email_send",
            semantic_target="Send Governed Application Email",
            method="GMAIL_API_SEND",
            selector="gmail:users.messages.send",
            control_provenance=TargetSchemaProvenance.USER_PROVIDED.value,
            requires_double_confirmation=True,
        ),
    )

    field_bindings = [
        TargetFieldBinding(
            target_field_id="applicant_email",
            source_fact_id="fact-email-operator-1",
        )
    ]

    channel_metadata = {
        "email_subject": "[JACKVERSE INTERNAL TEST] Governed Gmail Transmission Verification"
    }

    # 6. Check existing or compile ExternalExecutionPlan
    plan: ExternalExecutionPlan | None = None
    action: Action | None = None
    approval: Approval | None = None

    if case.status == CaseStatus.AWAITING_APPROVAL:
        latest_plan = plan_service.get_latest_execution_plan(operator_id, CONTROLLED_TEST_CASE_ID)
        if latest_plan and latest_plan.package_id == CONTROLLED_TEST_PACKAGE_ID:
            existing_auth = plan_service.find_existing_transmission_authorization(
                user_id=operator_id,
                case_id=CONTROLLED_TEST_CASE_ID,
                plan_id=latest_plan.plan_id,
            )
            if existing_auth is not None:
                plan = latest_plan
                action, approval = existing_auth

    if plan is None:
        plan = plan_service.create_execution_plan(
            user_id=operator_id,
            case_id=CONTROLLED_TEST_CASE_ID,
            package_id=CONTROLLED_TEST_PACKAGE_ID,
            target_schema=target_schema,
            field_bindings=field_bindings,
            channel_metadata=channel_metadata,
        )

    if plan.status != ExternalExecutionPlanStatus.READY_FOR_AUTHORIZATION:
        unresolved = [f.message for f in plan.findings if not f.passed]
        raise DomainValidationError(
            f"Execution plan '{plan.plan_id}' is not READY_FOR_AUTHORIZATION (status: {plan.status.value}). "
            f"Unresolved findings: {unresolved}"
        )

    # Invariant assertions on compiled plan
    if plan.email_manifest is None:
        raise DomainValidationError("Compiled execution plan is missing email manifest.")

    manifest = plan.email_manifest
    if manifest.to_email.strip().lower() != CONTROLLED_TEST_EMAIL:
        raise DomainValidationError(f"Plan to_email mismatch: {manifest.to_email}")
    if manifest.from_email.strip().lower() != CONTROLLED_TEST_EMAIL:
        raise DomainValidationError(f"Plan from_email mismatch: {manifest.from_email}")
    if manifest.subject != "[JACKVERSE INTERNAL TEST] Governed Gmail Transmission Verification":
        raise DomainValidationError(f"Plan subject mismatch: {manifest.subject}")
    if not manifest.sealed_payload_hash:
        raise DomainValidationError("Plan email manifest has missing sealed_payload_hash.")
    if len(manifest.attachments) != 1:
        raise DomainValidationError(f"Plan has {len(manifest.attachments)} attachments, expected 1.")

    att = manifest.attachments[0]
    if att.filename != "cv_operator-1.txt":
        raise DomainValidationError(f"Attachment filename mismatch: {att.filename}")
    if att.byte_size != 131:
        raise DomainValidationError(f"Attachment byte size mismatch: {att.byte_size} (expected 131)")

    # 7. Request Human Transmission Authorization (if not already requested)
    if action is None or approval is None:
        action, approval = plan_service.request_transmission_authorization(
            user_id=operator_id,
            case_id=CONTROLLED_TEST_CASE_ID,
            plan_id=plan.plan_id,
        )

    # Invariant assertions on action and approval
    if action.status != ActionStatus.AWAITING_APPROVAL:
        raise DomainValidationError(f"Action status is '{action.status.value}', expected AWAITING_APPROVAL.")
    if approval.status != ApprovalStatus.PENDING:
        raise DomainValidationError(f"Approval status is '{approval.status.value}', expected PENDING.")
    if action.parameters.get("execution_plan_fingerprint") != plan.execution_plan_fingerprint:
        raise DomainValidationError("Action execution_plan_fingerprint does not match plan fingerprint.")

    # 8. Verify Zero Outbound Dispatches & Zero Receipts in Database
    with storage.unit_of_work() as uow:
        fresh_case = uow.cases.get_by_id(CONTROLLED_TEST_CASE_ID)
        case_status_val = fresh_case.status.value if fresh_case else "unknown"
        cur = uow.conn.cursor()
        cur.execute("SELECT count(*) FROM execution_receipts WHERE case_id = ?", (CONTROLLED_TEST_CASE_ID,))
        receipt_count = cur.fetchone()[0]

    if receipt_count > 0:
        raise RuntimeError(f"SAFETY VIOLATION: Execution receipts found ({receipt_count})! Transmission must not execute.")

    dossier = {
        "timestamp": now_utc().isoformat(),
        "stage": "STAGE_B_TRANSMISSION_PREPARATION",
        "status": "READY_FOR_HUMAN_TRANSMISSION_REVIEW",
        "operator_id": operator_id,
        "sender_email": connected_email,
        "recipient_email": recipient_email,
        "safety_checks": {
            "real_execution_enabled": safety["real_execution_enabled"],
            "gmail_execution_enabled": safety["gmail_execution_enabled"],
            "oauth_scopes_verified": safety["scopes"],
            "no_send_boundary_intact": True,
        },
        "self_test_policy": {
            "evidence_source": SELF_TEST_EVIDENCE_SOURCE,
            "source_uri": f"{SELF_TEST_SOURCE_URI_PREFIX}{operator_id}",
            "employer_domain": SELF_TEST_EMPLOYER_DOMAIN,
            "verified_by": f"operator:{operator_id}",
            "verified_recipient_email": CONTROLLED_TEST_EMAIL,
            "target_schema_id": target_schema.schema_id,
            "full_name_omitted": True,
            "applicant_email_bound": True,
        },
        "execution_plan": {
            "plan_id": plan.plan_id,
            "status": plan.status.value,
            "execution_plan_fingerprint": plan.execution_plan_fingerprint,
            "target_schema_hash": plan.target_schema_hash,
            "execution_input_hash": plan.execution_input_hash,
            "destination_type": plan.destination.destination_type,
            "canonical_url": plan.destination.canonical_url,
            "hostname": plan.destination.normalized_hostname,
            "field_mappings": [m.to_dict() for m in plan.field_mappings],
            "upload_assets": [u.to_dict() for u in plan.upload_mappings],
            "findings_count": len(plan.findings),
            "unresolved_errors": [f.message for f in plan.findings if not f.passed],
        },
        "email_manifest": {
            "to_email": manifest.to_email,
            "from_email": manifest.from_email,
            "subject": manifest.subject,
            "message_id": manifest.message_id,
            "date_rfc2822": manifest.date_rfc2822,
            "mime_boundary": manifest.mime_boundary,
            "sealed_payload_hash": manifest.sealed_payload_hash,
            "body_sha256": compute_sha256(manifest.body_text),
            "body_preview": manifest.body_text[:120] + "...",
            "attachments": [
                {
                    "filename": a.filename,
                    "mime_type": a.mime_type,
                    "byte_size": a.byte_size,
                    "content_sha256": a.content_sha256,
                    "asset_id": a.asset_id,
                }
                for a in manifest.attachments
            ],
        },
        "transmission_authorization": {
            "action_id": action.action_id,
            "action_type": action.action_type.value,
            "action_status": action.status.value,
            "action_fingerprint": action.compute_fingerprint(),
            "approval_id": approval.approval_id,
            "approval_status": approval.status.value,
            "case_id": CONTROLLED_TEST_CASE_ID,
            "case_status": case_status_val,
        },
        "real_gmail_transmissions": 0,
        "execution_attempts": 0,
        "execution_receipts": 0,
        "human_send_authorization": "NOT_GRANTED",
    }
    return dossier


def main() -> int:
    parser = argparse.ArgumentParser(description="JackVerse Phase 5.3.3.2 Stage B Preparation")
    parser.add_argument(
        "--db-path",
        default=os.getenv("CASEWORKER_DB_PATH", "/app/.caseworker/caseworker.db"),
        help="Path to Caseworker SQLite database",
    )
    parser.add_argument("--operator-id", default=CONTROLLED_TEST_OPERATOR_ID, help="Operator ID")
    parser.add_argument("--recipient-email", default=None, help="Recipient email (must match operator's email)")
    parser.add_argument("--json", action="store_true", help="Output JSON only")
    args = parser.parse_args()

    try:
        dossier = prepare_stage_b(
            db_path=args.db_path,
            operator_id=args.operator_id,
            recipient_email=args.recipient_email,
        )
    except Exception as exc:
        print(f"STAGE B PREPARATION FAILED: {exc}", file=sys.stderr)
        import traceback
        traceback.print_exc()
        return 1

    if args.json:
        print(json.dumps(dossier, indent=2))
        return 0

    print("=" * 80)
    print("JACKVERSE PHASE 5.3.3.2 — STAGE B TRANSMISSION PREPARATION REPORT")
    print(f"Timestamp: {dossier['timestamp']}")
    print(f"Status:    {dossier['status']}")
    print("=" * 80)
    print()
    print("[1] RUNTIME & SAFETY BOUNDARIES")
    print(f"  Operator ID:                     {dossier['operator_id']}")
    print(f"  Sender Mailbox (OAuth):          {dossier['sender_email']}")
    print(f"  Recipient Mailbox:               {dossier['recipient_email']}")
    print(f"  OAuth Scopes Verified:           {', '.join(dossier['safety_checks']['oauth_scopes_verified'])}")
    print(f"  CASEWORKER_REAL_EXECUTION:       OFF (Disabled)")
    print(f"  CASEWORKER_GMAIL_EXECUTION:      OFF (Disabled)")
    print(f"  NO-SEND Boundary Intact:         CONFIRMED")
    print()
    print("[2] SERVER-ENFORCED SELF-TEST POLICY")
    print(f"  Evidence Source:                 {dossier['self_test_policy']['evidence_source']}")
    print(f"  Source URI:                      {dossier['self_test_policy']['source_uri']}")
    print(f"  Employer Domain:                 {dossier['self_test_policy']['employer_domain']}")
    print(f"  Verified Recipient:              {dossier['self_test_policy']['verified_recipient_email']}")
    print(f"  Target Schema ID:                {dossier['self_test_policy']['target_schema_id']}")
    print(f"  Full Name Omitted:               CONFIRMED (Zero Vault Denial on Synthetic Name)")
    print(f"  Applicant Email Bound:           CONFIRMED (Strictly bound to OAuth email fact)")
    print()
    print("[3] COMPILED EXTERNAL EXECUTION PLAN")
    print(f"  Plan ID:                         {dossier['execution_plan']['plan_id']}")
    print(f"  Plan Status:                     {dossier['execution_plan']['status']}")
    print(f"  Plan Fingerprint:                {dossier['execution_plan']['execution_plan_fingerprint']}")
    print(f"  Target Schema Hash:              {dossier['execution_plan']['target_schema_hash']}")
    print(f"  Execution Input Hash:            {dossier['execution_plan']['execution_input_hash']}")
    print(f"  Destination Type:                {dossier['execution_plan']['destination_type']}")
    print(f"  Destination Canonical URL:       {dossier['execution_plan']['canonical_url']}")
    print(f"  Unresolved Findings / Errors:    {len(dossier['execution_plan']['unresolved_errors'])}")
    print()
    print("[4] IMMUTABLE EMAIL PAYLOAD MANIFEST")
    print(f"  From:                            {dossier['email_manifest']['from_email']}")
    print(f"  To:                              {dossier['email_manifest']['to_email']}")
    print(f"  Subject:                         {dossier['email_manifest']['subject']}")
    print(f"  Message-ID:                      {dossier['email_manifest']['message_id']}")
    print(f"  Date (RFC 2822):                 {dossier['email_manifest']['date_rfc2822']}")
    print(f"  MIME Boundary:                   {dossier['email_manifest']['mime_boundary']}")
    print(f"  Sealed Payload SHA-256:          {dossier['email_manifest']['sealed_payload_hash']}")
    print(f"  Body Hash (SHA-256):             {dossier['email_manifest']['body_sha256']}")
    print(f"  Attachments (1):")
    for att in dossier['email_manifest']['attachments']:
        print(f"    - {att['filename']} ({att['byte_size']} bytes, mime: {att['mime_type']}, sha256: {att['content_sha256'][:16]}...)")
    print()
    print("[5] TRANSMISSION AUTHORIZATION REQUEST")
    print(f"  Action ID:                       {dossier['transmission_authorization']['action_id']}")
    print(f"  Action Type:                     {dossier['transmission_authorization']['action_type']}")
    print(f"  Action Status:                   {dossier['transmission_authorization']['action_status']} (AWAITING OPERATOR DECISION)")
    print(f"  Action Fingerprint:              {dossier['transmission_authorization']['action_fingerprint']}")
    print(f"  Approval ID:                     {dossier['transmission_authorization']['approval_id']}")
    print(f"  Approval Status:                 {dossier['transmission_authorization']['approval_status']} (PENDING)")
    print(f"  Case ID:                         {dossier['transmission_authorization']['case_id']}")
    print(f"  Case Status:                     {dossier['transmission_authorization']['case_status']}")
    print()
    print("=" * 80)
    print("MANDATORY STOP: STAGE B COMPLETE — HALTED FOR HUMAN TRANSMISSION AUTHORIZATION")
    print("REAL GMAIL TRANSMISSIONS: 0")
    print("EXECUTION ATTEMPTS: 0")
    print("EXECUTION RECEIPTS: 0")
    print("HUMAN SEND AUTHORIZATION: NOT GRANTED")
    print("READY FOR HUMAN TRANSMISSION REVIEW")
    print("=" * 80)
    print()
    print("NEXT ACTION FOR OPERATOR:")
    print(f"  Review and approve transmission authorization in the JackVerse UI at:")
    print(f"  https://localhost:3443/cases/{dossier['transmission_authorization']['case_id']}")
    print(f"  or via authenticated API request:")
    print(f"  POST /api/v1/approvals/{dossier['transmission_authorization']['approval_id']}/approve")
    print("=" * 80)
    return 0


if __name__ == "__main__":
    sys.exit(main())
