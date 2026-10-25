"""End-to-end integration tests for governed email application execution (Phase 5.3.3.1)."""

from __future__ import annotations

import unittest
import httpx

from caseworker.adapters.gmail_provider import (
    GmailApplicationExecutionProvider,
    StaticOAuthCredentialProvider,
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
from caseworker.domain.enums import (
    ActionStatus,
    ActionType,
    ApprovalStatus,
    ArtifactGroundingType,
    ArtifactPublicationRole,
    ArtifactStatus,
    CaseStatus,
    CaseType,
    ClaimStatus,
    ExecutionAttemptStatus,
    ExecutionDestinationType,
    ExecutionErrorCategory,
    ExecutionProviderKind,
    ExecutionReceiptStatus,
    ExternalExecutionPlanStatus,
    OpportunityStatus,
    OpportunityType,
    PackageVerificationStatus,
    PreparationMaterialType,
    PreparationPackageStatus,
    RiskLevel,
    VerificationStatus,
)
from caseworker.domain.errors import ApprovalValidationError
from caseworker.domain.execution_planner import ExecutionPlanCompiler
from caseworker.domain.opportunity import Opportunity
from caseworker.domain.package import (
    ArtifactPackageSnapshot,
    PreparationPackage,
    SafeClaimSnapshot,
)
from caseworker.domain.preparation_analyzer import PreparationAnalyzer
from caseworker.persistence.sqlite import SQLiteCaseworkerStorage
from caseworker.services.action_service import ActionService
from caseworker.services.approval_service import ApprovalService
from caseworker.services.execution_plan_service import ExecutionPlanService
from caseworker.services.governed_execution_service import GovernedExecutionService
from caseworker.services.package_service import PackageService


class TestGovernedEmailExecution(unittest.TestCase):
    """Integration test suite verifying affirmative employer acceptance, MIME sealing, and two-barrier execution."""

    def setUp(self) -> None:
        self.storage = SQLiteCaseworkerStorage(db_path=":memory:")
        self.action_service = ActionService(self.storage)
        self.approval_service = ApprovalService(self.storage)
        self.plan_service = ExecutionPlanService(self.storage)
        self.package_service = PackageService(self.storage)

        self.user_id = "user_grant_email"
        self.case_id = "case_email_001"
        self.opp_id = "opp_email_001"
        self.applicant_email = "grant@example.com"
        self.applicant_name = "Grant Dagogo-Jack"

        with self.storage.unit_of_work() as uow:
            # 1. Create Case
            self.case = Case(
                case_id=self.case_id,
                user_id=self.user_id,
                title="SRE Application at CloudScale",
                goal="Submit verified job application by email",
                case_type=CaseType.JOB_APPLICATION,
                status=CaseStatus.PLANNING,
            )
            uow.cases.save(self.case)

            # 2. Create Opportunity with affirmative acceptance
            self.opportunity = Opportunity(
                opportunity_id=self.opp_id,
                user_id=self.user_id,
                opportunity_type=OpportunityType.JOB,
                title="Lead Infrastructure Engineer",
                organization="CloudScale Corp",
                source_url="https://cloudscale.io/careers/lead-infra",
                requirements=["Distributed systems", "Python"],
                metadata={
                    "application_channel": "email",
                    "application_email": "jobs@cloudscale.io",
                    "accepts_email_applications": True,
                    "email_acceptance_evidence": {
                        "evidence_source": "official_job_posting",
                        "source_uri": "https://cloudscale.io/careers/lead-infra",
                        "instructions_text": "Please email your resume and cover letter to jobs@cloudscale.io",
                        "verified_recipient_email": "jobs@cloudscale.io",
                        "employer_domain": "cloudscale.io",
                        "verified_by": "operator:grant",
                        "verified_at": "2026-10-25T10:00:00Z",
                    },
                },
                converted_case_id=self.case_id,
                status=OpportunityStatus.SHORTLISTED,
            )
            uow.opportunities.save(self.opportunity)

            from caseworker.domain.execution_plan import EmailAcceptanceEvidence, VerifiedEmailAcceptanceRecord
            ev = EmailAcceptanceEvidence(
                evidence_source="official_job_posting",
                source_uri="https://cloudscale.io/careers/lead-infra",
                instructions_text="Please email your resume and cover letter to jobs@cloudscale.io",
                verified_recipient_email="jobs@cloudscale.io",
                employer_domain="cloudscale.io",
                verified_by="operator:grant",
                verified_at="2026-10-25T10:00:00Z",
            )
            self.email_acceptance = VerifiedEmailAcceptanceRecord(
                evidence_id="ev-email-1",
                opportunity_id=self.opp_id,
                user_id=self.user_id,
                verified_recipient_email="jobs@cloudscale.io",
                evidence_source="official_job_posting",
                source_uri="https://cloudscale.io/careers/lead-infra",
                instructions_text="Please email your resume and cover letter to jobs@cloudscale.io",
                employer_domain="cloudscale.io",
                verified_by="operator:grant",
                verified_at="2026-10-25T10:00:00Z",
                snapshot_hash=ev.evidence_snapshot_hash,
                evidence_digest=ev.evidence_digest,
                evidence_version=1,
                is_active=True,
            )
            uow.verified_email_acceptance.save(self.email_acceptance)

            # 3. Verified Facts (Applicant Email & Full Name)
            self.fact_email = ContextFact(
                fact_id="fact-email-1",
                user_id=self.user_id,
                namespace="identity",
                key="email",
                value=self.applicant_email,
                verification_status=VerificationStatus.USER_VERIFIED,
                allowed_purposes=["job_application", "case_preparation"],
            )
            uow.context.save(self.fact_email)

            self.fact_name = ContextFact(
                fact_id="fact-name-1",
                user_id=self.user_id,
                namespace="identity",
                key="full_name",
                value=self.applicant_name,
                verification_status=VerificationStatus.USER_VERIFIED,
                allowed_purposes=["job_application", "case_preparation"],
            )
            uow.context.save(self.fact_name)

            # 4. Preparation Report & Claims
            analyzer = PreparationAnalyzer()
            self.report = analyzer.analyze(
                case=self.case,
                opportunity=self.opportunity,
                granted_facts=[self.fact_email, self.fact_name],
                denied_descriptors=[],
            )
            uow.preparation_reports.save(self.report)

            supp_hash = compute_support_snapshot_hash([self.fact_name])
            self.claim = Claim(
                claim_id="claim-email-1",
                user_id=self.user_id,
                purpose="job_application",
                text="10+ years distributed systems architecture",
                status=ClaimStatus.SUPPORTED,
                supporting_fact_ids=[self.fact_name.fact_id],
                case_id=self.case_id,
                source_report_id=self.report.report_id,
                source_claim_intent_id="intent-1",
                assertion_schema="skill_possession",
                assertion_json=f'{{"kind": "skill_possession", "fact_ids": ["{self.fact_name.fact_id}"], "fields": {{"skill": "Python"}}}}',
                support_snapshot_hash=supp_hash,
                version=1,
            )
            uow.claims.save(self.claim)

            # 5. Approved Artifacts (CV, Cover Letter, Screening Answers)
            cv_grounding = compute_artifact_grounding_hash(claims=[self.claim], direct_facts=[self.fact_name])
            self.art_cv = PreparedArtifact(
                artifact_id="art-email-cv",
                artifact_set_id="set-email-1",
                case_id=self.case_id,
                opportunity_id=self.opp_id,
                user_id=self.user_id,
                material_type=PreparationMaterialType.CV,
                title="Curriculum Vitae",
                planner_version=PLANNER_VERSION,
                status=ArtifactStatus.DRAFT,
                source_report_id=self.report.report_id,
                grounding_snapshot_hash=cv_grounding,
                blocks=[
                    ArtifactBlock(
                        block_id="b-cv-header",
                        block_type="header",
                        content="Profile",
                        grounding_type=ArtifactGroundingType.CONTEXT_FACT,
                        fact_ids=[self.fact_name.fact_id],
                    ),
                    ArtifactBlock(
                        block_id="b-cv-1",
                        content="Senior Infrastructure Engineer - Python",
                        grounding_type=ArtifactGroundingType.CLAIM,
                        claim_ids=[self.claim.claim_id],
                    ),
                ],
            )
            uow.preparation_artifacts.save(self.art_cv)

            cl_grounding = compute_artifact_grounding_hash(claims=[], direct_facts=[self.fact_name])
            self.art_cl = PreparedArtifact(
                artifact_id="art-email-cl",
                artifact_set_id="set-email-1",
                case_id=self.case_id,
                opportunity_id=self.opp_id,
                user_id=self.user_id,
                material_type=PreparationMaterialType.COVER_LETTER,
                title="Cover Letter",
                planner_version=PLANNER_VERSION,
                status=ArtifactStatus.DRAFT,
                source_report_id=self.report.report_id,
                grounding_snapshot_hash=cl_grounding,
                blocks=[
                    ArtifactBlock(
                        block_id="b-cl-1",
                        content="Dear Hiring Team,\n\nI am excited to apply for the Lead Infrastructure Engineer role.",
                        grounding_type=ArtifactGroundingType.CONTEXT_FACT,
                        fact_ids=[self.fact_name.fact_id],
                    )
                ],
            )
            uow.preparation_artifacts.save(self.art_cl)

            qa_grounding = compute_artifact_grounding_hash(claims=[self.claim], direct_facts=[])
            self.art_qa = PreparedArtifact(
                artifact_id="art-email-qa",
                artifact_set_id="set-email-1",
                case_id=self.case_id,
                opportunity_id=self.opp_id,
                user_id=self.user_id,
                material_type=PreparationMaterialType.SCREENING_ANSWERS,
                title="Screening Q&A",
                planner_version=PLANNER_VERSION,
                status=ArtifactStatus.DRAFT,
                source_report_id=self.report.report_id,
                grounding_snapshot_hash=qa_grounding,
                blocks=[
                    ArtifactBlock(
                        block_id="b-qa-1",
                        block_type="qna_item",
                        content="### Q: Notice period?\n\n1 month.",
                        grounding_type=ArtifactGroundingType.CLAIM,
                        claim_ids=[self.claim.claim_id],
                        metadata={
                            "question": "Notice period?",
                            "answer": "1 month.",
                            "answer_status": "ANSWERED",
                        },
                    )
                ],
            )
            uow.preparation_artifacts.save(self.art_qa)

        # 6. Seal & Approve Package locally
        package, _, app, _, _ = self.package_service.verify_and_prepare_package(
            user_id=self.user_id,
            case_id=self.case_id,
            artifact_set_id="set-email-1",
            expected_case_version=1,
        )
        self.approval_service.approve_action(
            user_id=self.user_id,
            approval_id=app.approval_id,
            reason="Confirmed local package review",
        )
        self.package = package

        with self.storage.unit_of_work() as uow:
            self.case = uow.cases.get_by_id(self.case_id)

    def test_affirmative_employer_acceptance_required(self) -> None:
        """When an opportunity does not declare affirmative email acceptance, plan compilation fails closed."""
        with self.storage.unit_of_work() as uow:
            unaccepted_opp = Opportunity(
                opportunity_id="opp-unaccepted",
                user_id=self.user_id,
                opportunity_type=OpportunityType.JOB,
                title="Frontend Dev",
                organization="Some Corp",
                source_url="mailto:jobs@somecorp.com",
                metadata={"application_email": "jobs@somecorp.com"},  # Email address without affirmative acceptance
            )
            uow.opportunities.save(unaccepted_opp)

            plan = ExecutionPlanCompiler.compile_plan(
                case=self.case,
                opportunity=unaccepted_opp,
                package=self.package,
                artifacts=[self.art_cv, self.art_cl, self.art_qa],
                context_facts=[self.fact_email, self.fact_name],
            )
            self.assertEqual(plan.status, ExternalExecutionPlanStatus.DRAFT)
            error_codes = [f.code for f in plan.findings if f.severity == "error"]
            self.assertIn("EMAIL_APPLICATION_NOT_ACCEPTED", error_codes)

    def test_affirmative_acceptance_verified_seals_mime_payload(self) -> None:
        """With affirmative acceptance, compile_plan generates sealed EmailPayloadManifest with hash."""
        with self.storage.unit_of_work() as uow:
            rec = uow.verified_email_acceptance.get_active_by_opportunity(self.opp_id)
            plan = ExecutionPlanCompiler.compile_plan(
                case=self.case,
                opportunity=self.opportunity,
                package=self.package,
                artifacts=[self.art_cv, self.art_cl, self.art_qa],
                context_facts=[self.fact_email, self.fact_name],
                verified_email_acceptance=rec,
            )
            self.assertEqual(plan.status, ExternalExecutionPlanStatus.READY_FOR_AUTHORIZATION)
            self.assertIsNotNone(plan.email_manifest)
            manifest = plan.email_manifest
            self.assertEqual(manifest.to_email, "jobs@cloudscale.io")
            self.assertEqual(manifest.from_email, self.applicant_email)
            self.assertTrue(manifest.message_id.startswith("<plan-"))
            self.assertIn("@example.com>", manifest.message_id)
            self.assertTrue(manifest.sealed_payload_hash)
            self.assertTrue(len(manifest.attachments) >= 1)

    def test_end_to_end_governed_email_execution_success(self) -> None:
        """Complete two-barrier governed pipeline dispatches sealed MIME payload via Gmail API."""
        # 1. Compile Plan
        plan = self.plan_service.create_execution_plan(
            user_id=self.user_id,
            case_id=self.case_id,
        )
        self.assertEqual(plan.status, ExternalExecutionPlanStatus.READY_FOR_AUTHORIZATION)

        # 2. Request Transmission Authorization
        action, approval = self.plan_service.request_transmission_authorization(
            user_id=self.user_id,
            case_id=self.case_id,
            plan_id=plan.plan_id,
        )

        # 3. Approve Action
        self.approval_service.approve_action(
            user_id=self.user_id,
            approval_id=approval.approval_id,
            reason="User authorized transmission",
        )

        # 4. Mocked Gmail API transport
        captured_requests: list[dict] = []

        def mock_gmail_api(request: httpx.Request) -> httpx.Response:
            self.assertIn("users/me/messages/send", str(request.url))
            self.assertEqual(request.headers.get("authorization"), "Bearer secret-oauth-token-xyz")
            captured_requests.append(request.read())
            return httpx.Response(
                200,
                json={"id": "msg-gmail-cloudscale-1234", "threadId": "th-cloudscale-5678"},
            )

        client = httpx.Client(transport=httpx.MockTransport(mock_gmail_api))
        credential_provider = StaticOAuthCredentialProvider(
            access_token="secret-oauth-token-xyz",
            authenticated_email=self.applicant_email,
        )
        provider = GmailApplicationExecutionProvider(
            credential_provider=credential_provider,
            http_client=client,
            real_execution_enabled=True,
            gmail_execution_enabled=True,
        )

        exec_service = GovernedExecutionService(
            storage=self.storage,
            email_provider=provider,
            real_execution_enabled=True,
            gmail_execution_enabled=True,
            credential_provider=credential_provider,
        )

        # 5. Execute Action through GovernedExecutionService
        attempt, receipt = exec_service.execute_action(
            user_id=self.user_id,
            action_id=action.action_id,
            principal_auth_method="operator_token",
        )

        self.assertIsNotNone(receipt)
        self.assertEqual(receipt.status, ExecutionReceiptStatus.VERIFIED_SUCCESS)
        self.assertEqual(receipt.destination_type, ExecutionDestinationType.EMAIL.value)
        self.assertEqual(receipt.safe_provider_metadata.get("provider_status"), "GMAIL_API_ACCEPTED")
        self.assertEqual(receipt.safe_provider_metadata.get("gmail_message_id"), "msg-gmail-cloudscale-1234")
        self.assertEqual(receipt.safe_provider_metadata.get("recipient_email"), "jobs@cloudscale.io")
        self.assertEqual(receipt.safe_provider_metadata.get("sender_email"), self.applicant_email)
        self.assertFalse(receipt.safe_provider_metadata.get("is_simulated"))

        # Verify state transitions
        with self.storage.unit_of_work() as uow:
            persisted_act = uow.actions.get_by_id(action.action_id)
            persisted_plan = uow.execution_plans.get_by_id(plan.plan_id)
            persisted_case = uow.cases.get_by_id(self.case_id)

            self.assertEqual(persisted_act.status, ActionStatus.SUCCEEDED)
            self.assertEqual(persisted_plan.status, ExternalExecutionPlanStatus.EXECUTED)
            self.assertEqual(persisted_case.status, CaseStatus.WAITING_EXTERNAL)

        # 6. Verify Idempotent Replay
        replay_attempt, replay_receipt = exec_service.execute_action(
            user_id=self.user_id,
            action_id=action.action_id,
            principal_auth_method="operator_token",
        )
        self.assertEqual(replay_receipt.receipt_id, receipt.receipt_id)
        self.assertEqual(len(captured_requests), 1)  # Zero additional outbound network calls

    def test_barrier_b_detects_sealed_payload_tampering(self) -> None:
        """If staged payload bytes are tampered with between staging and Barrier B, submit is aborted."""
        # 1. Compile Plan
        plan = self.plan_service.create_execution_plan(
            user_id=self.user_id,
            case_id=self.case_id,
        )

        # 2. Request Transmission Authorization
        action, approval = self.plan_service.request_transmission_authorization(
            user_id=self.user_id,
            case_id=self.case_id,
            plan_id=plan.plan_id,
        )

        # 3. Approve Action
        self.approval_service.approve_action(
            user_id=self.user_id,
            approval_id=approval.approval_id,
            reason="User authorized transmission",
        )

        # Tampering provider
        credential_provider = StaticOAuthCredentialProvider("tok", self.applicant_email)
        base_provider = GmailApplicationExecutionProvider(
            credential_provider=credential_provider,
            real_execution_enabled=True,
            gmail_execution_enabled=True,
        )

        import dataclasses

        class TamperingProvider(GmailApplicationExecutionProvider):
            def stage_submission(self, plan, package, upload_assets, context=None):
                outcome = super().stage_submission(plan, package, upload_assets, context=context)
                if context is not None:
                    context.sealed_payload_bytes = b"MALICIOUS_TAMPERED_BYTES"
                    context.sealed_payload_hash = "malicious_tampered_hash"
                return dataclasses.replace(
                    outcome,
                    sealed_payload_bytes=b"MALICIOUS_TAMPERED_BYTES",
                    sealed_payload_hash="malicious_tampered_hash",
                )

        tamper_provider = TamperingProvider(
            credential_provider=credential_provider,
            real_execution_enabled=True,
            gmail_execution_enabled=True,
        )

        exec_service = GovernedExecutionService(
            storage=self.storage,
            email_provider=tamper_provider,
            real_execution_enabled=True,
            gmail_execution_enabled=True,
            credential_provider=credential_provider,
        )

        with self.assertRaises(ApprovalValidationError) as ctx:
            exec_service.execute_action(
                user_id=self.user_id,
                action_id=action.action_id,
                provider=tamper_provider,
                principal_auth_method="operator_token",
            )
        self.assertEqual(ctx.exception.details.get("failure_kind"), "payload_integrity_mismatch")

        with self.storage.unit_of_work() as uow:
            persisted_act = uow.actions.get_by_id(action.action_id)
            attempt = uow.execution_attempts.get_latest_for_action(action.action_id)

            self.assertEqual(persisted_act.status, ActionStatus.APPROVED)
            self.assertEqual(attempt.status, ExecutionAttemptStatus.FAILED)
            self.assertEqual(attempt.error_category, ExecutionErrorCategory.PAYLOAD_INTEGRITY_MISMATCH)
            self.assertFalse(attempt.irreversible_action_taken)


if __name__ == "__main__":
    unittest.main()
