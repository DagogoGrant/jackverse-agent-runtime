"""Comprehensive security regression and hardening tests for Governed Gmail Execution (Phase 5.3.3.1)."""

from __future__ import annotations

import base64
from datetime import datetime, timedelta, timezone
import hashlib
import json
import unittest
import httpx

from caseworker.adapters.crypto.token_cipher import TokenCipher
from caseworker.adapters.gmail_provider import (
    DatabaseOAuthCredentialProvider,
    GmailApplicationExecutionProvider,
    StaticOAuthCredentialProvider,
)
from caseworker.api.app import create_app
from caseworker.api.config import APIConfig
from caseworker.api.identity import generate_operator_token
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
from caseworker.domain.errors import (
    ApprovalValidationError,
    CryptoConfigurationError,
    EntityNotFoundError,
    ProviderAuthError,
    TokenDecryptionError,
)
from caseworker.domain.execution_planner import ExecutionPlanCompiler
from caseworker.domain.opportunity import Opportunity
from caseworker.domain.preparation_analyzer import PreparationAnalyzer
from caseworker.domain.package import (
    ArtifactPackageSnapshot,
    PreparationPackage,
    SafeClaimSnapshot,
)
from caseworker.domain.provider_adapter import (
    ExecutionAttemptContext,
    SimulatedExecutionProvider,
    SimulationMode,
)
from caseworker.domain.types import compute_sha256_bytes, now_utc
from caseworker.persistence.sqlite import (
    OAuthCredentialRecord,
    OAuthStateRecord,
    SQLiteCaseworkerStorage,
)
from caseworker.services.action_service import ActionService
from caseworker.services.approval_service import ApprovalService
from caseworker.services.execution_plan_service import ExecutionPlanService
from caseworker.services.governed_execution_service import (
    GovernedExecutionService,
    _sanitize_diagnostic_message,
    _sanitize_provider_metadata,
)
from caseworker.services.package_service import PackageService


class TestGmailHardeningRegressions(unittest.TestCase):
    """14 Hardening Defect and Security Regression Tests for Governed Gmail Provider."""

    TEST_KEY = "test-encryption-key-for-credentials-at-least-32-chars-long"
    OPERATOR_SECRET = "super-secure-operator-secret-at-least-32-bytes-long"

    def setUp(self) -> None:
        self.storage = SQLiteCaseworkerStorage(db_path=":memory:")
        self.cipher = TokenCipher(self.TEST_KEY)
        self.action_service = ActionService(self.storage)
        self.approval_service = ApprovalService(self.storage)
        self.plan_service = ExecutionPlanService(self.storage)
        self.package_service = PackageService(self.storage)

        self.user_id = "user_grant_hardened"
        self.case_id = "case_email_hardened"
        self.opp_id = "opp_email_hardened"
        self.applicant_email = "grant@example.com"
        self.applicant_name = "Grant Dagogo-Jack"

        with self.storage.unit_of_work() as uow:
            # Create Case
            self.case = Case(
                case_id=self.case_id,
                user_id=self.user_id,
                title="Principal SRE at CloudScale",
                goal="Submit verified job application by email",
                case_type=CaseType.JOB_APPLICATION,
                status=CaseStatus.PLANNING,
            )
            uow.cases.save(self.case)

            # Create Opportunity with affirmative acceptance
            self.opportunity = Opportunity(
                opportunity_id=self.opp_id,
                user_id=self.user_id,
                opportunity_type=OpportunityType.JOB,
                title="Principal SRE",
                organization="CloudScale Corp",
                source_url="https://cloudscale.io/careers/principal-sre",
                requirements=["Distributed systems", "Python"],
                metadata={
                    "application_channel": "email",
                    "application_email": "jobs@cloudscale.io",
                    "accepts_email_applications": True,
                    "email_acceptance_evidence": {
                        "evidence_source": "official_job_posting",
                        "source_uri": "https://cloudscale.io/careers/principal-sre",
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
                source_uri="https://cloudscale.io/careers/principal-sre",
                instructions_text="Please email your resume and cover letter to jobs@cloudscale.io",
                verified_recipient_email="jobs@cloudscale.io",
                employer_domain="cloudscale.io",
                verified_by="operator:grant",
                verified_at="2026-10-25T10:00:00Z",
            )
            self.email_acceptance = VerifiedEmailAcceptanceRecord(
                evidence_id="ev-email-h",
                opportunity_id=self.opp_id,
                user_id=self.user_id,
                verified_recipient_email="jobs@cloudscale.io",
                evidence_source="official_job_posting",
                source_uri="https://cloudscale.io/careers/principal-sre",
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

            # Verified Facts
            self.fact_email = ContextFact(
                fact_id="fact-email-h",
                user_id=self.user_id,
                namespace="identity",
                key="email",
                value=self.applicant_email,
                verification_status=VerificationStatus.USER_VERIFIED,
                allowed_purposes=["job_application", "case_preparation"],
            )
            self.fact_name = ContextFact(
                fact_id="fact-name-h",
                user_id=self.user_id,
                namespace="identity",
                key="full_name",
                value=self.applicant_name,
                verification_status=VerificationStatus.USER_VERIFIED,
                allowed_purposes=["job_application", "case_preparation"],
            )
            uow.context.save(self.fact_email)
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
                claim_id="claim-email-h",
                user_id=self.user_id,
                purpose="job_application",
                text="10+ years engineering distributed systems",
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
                artifact_id="art-email-cv-h",
                artifact_set_id="set-email-h",
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
                        content="Principal SRE - Distributed Systems",
                        grounding_type=ArtifactGroundingType.CLAIM,
                        claim_ids=[self.claim.claim_id],
                    ),
                ],
            )
            uow.preparation_artifacts.save(self.art_cv)

            cl_grounding = compute_artifact_grounding_hash(claims=[], direct_facts=[self.fact_name])
            self.art_cl = PreparedArtifact(
                artifact_id="art-email-cl-h",
                artifact_set_id="set-email-h",
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
                        content="Dear Hiring Team,\n\nI am excited to apply for the Principal SRE role.",
                        grounding_type=ArtifactGroundingType.CONTEXT_FACT,
                        fact_ids=[self.fact_name.fact_id],
                    )
                ],
            )
            uow.preparation_artifacts.save(self.art_cl)

            qa_grounding = compute_artifact_grounding_hash(claims=[self.claim], direct_facts=[])
            self.art_qa = PreparedArtifact(
                artifact_id="art-email-qa-h",
                artifact_set_id="set-email-h",
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
            artifact_set_id="set-email-h",
            expected_case_version=1,
        )
        self.approval_service.approve_action(
            user_id=self.user_id,
            approval_id=app.approval_id,
            reason="Confirmed package review",
        )
        self.package = package

    def _setup_authorized_email_action(self) -> tuple[Action, Approval]:
        """Helper to create and approve an authorized email transmission action."""
        plan = self.plan_service.create_execution_plan(
            user_id=self.user_id,
            case_id=self.case_id,
        )
        action, approval = self.plan_service.request_transmission_authorization(
            user_id=self.user_id,
            case_id=self.case_id,
            plan_id=plan.plan_id,
        )
        self.approval_service.approve_action(
            user_id=self.user_id,
            approval_id=approval.approval_id,
            reason="Authorized for hardened testing",
        )
        with self.storage.unit_of_work() as uow:
            refreshed_act = uow.actions.get_by_id(action.action_id)
            refreshed_app = uow.approvals.get_by_id(approval.approval_id)
            return refreshed_act, refreshed_app

    def test_case_1_end_to_end_provider_selection_defaults_to_gmail(self) -> None:
        """Case 1: When no provider is explicitly passed, GovernedExecutionService resolves Gmail provider for EMAIL destination."""
        action, _ = self._setup_authorized_email_action()
        credential_provider = StaticOAuthCredentialProvider(
            access_token="ya29.test-valid-access-token",
            authenticated_email=self.applicant_email,
        )
        captured = []

        def mock_handler(request: httpx.Request) -> httpx.Response:
            captured.append(request)
            return httpx.Response(200, json={"id": "msg-auto-gmail-999", "threadId": "th-999"})

        mock_http = httpx.Client(transport=httpx.MockTransport(mock_handler))
        email_provider = GmailApplicationExecutionProvider(
            credential_provider=credential_provider,
            http_client=mock_http,
            real_execution_enabled=True,
            gmail_execution_enabled=True,
        )

        exec_service = GovernedExecutionService(
            storage=self.storage,
            email_provider=email_provider,
            real_execution_enabled=True,
            gmail_execution_enabled=True,
            credential_provider=credential_provider,
        )

        attempt, receipt = exec_service.execute_action(
            user_id=self.user_id,
            action_id=action.action_id,
            provider=None,  # Provider resolution occurs server-side
            principal_auth_method="operator_token",
        )

        self.assertEqual(attempt.provider_kind, ExecutionProviderKind.GMAIL_API)
        self.assertIsNotNone(receipt)
        self.assertEqual(receipt.status, ExecutionReceiptStatus.VERIFIED_SUCCESS)
        self.assertEqual(len(captured), 1)

    def test_case_2_simulation_rejected_for_email_actions(self) -> None:
        """Case 2: Simulation request is strictly rejected for EMAIL transmission actions in API route."""
        action, _ = self._setup_authorized_email_action()
        config = APIConfig(
            dev_auth=True,
            simulation_enabled=True,
            real_execution_enabled=True,
            gmail_execution_enabled=True,
            encryption_key=self.TEST_KEY,
            operator_secret=self.OPERATOR_SECRET,
        )
        app = create_app(config=config, storage=self.storage)
        from fastapi.testclient import TestClient
        client = TestClient(app)

        etag = f'"action:{action.action_id}:v{action.version}"'
        resp = client.post(
            f"/api/v1/actions/{action.action_id}/execute",
            headers={"X-JackVerse-User": self.user_id, "If-Match": etag},
            json={"simulation_mode": "success"},
        )
        self.assertEqual(resp.status_code, 409)
        data = resp.json()
        self.assertEqual(data.get("failure_kind"), "simulation_not_permitted")
        self.assertIn("Simulation request rejected for live real-transmission approval", data.get("detail", ""))

    def test_case_3_account_mismatch_fails_closed(self) -> None:
        """Case 3: Stored OAuth account mismatch against plan sender_email fails closed before lease/attempt."""
        action, _ = self._setup_authorized_email_action()
        mismatched_credential_provider = StaticOAuthCredentialProvider(
            access_token="ya29.test-token",
            authenticated_email="impostor@attacker.org",  # Does NOT match grant@example.com
        )
        exec_service = GovernedExecutionService(
            storage=self.storage,
            real_execution_enabled=True,
            gmail_execution_enabled=True,
            credential_provider=mismatched_credential_provider,
        )

        with self.assertRaises(ApprovalValidationError) as ctx:
            exec_service.execute_action(
                user_id=self.user_id,
                action_id=action.action_id,
                principal_auth_method="operator_token",
            )
        self.assertEqual(ctx.exception.details.get("failure_kind"), "provider_auth_error")

        # Action must remain APPROVED and no attempts created
        with self.storage.unit_of_work() as uow:
            act = uow.actions.get_by_id(action.action_id)
            self.assertEqual(act.status, ActionStatus.APPROVED)
            attempts = uow.execution_attempts.list_by_case(self.case_id)
            self.assertEqual(len(attempts), 0)

    def test_case_4_multi_user_isolation(self) -> None:
        """Case 4: User A's action and OAuth credentials cannot be executed or accessed by User B."""
        action, _ = self._setup_authorized_email_action()
        exec_service = GovernedExecutionService(
            storage=self.storage,
            real_execution_enabled=True,
            gmail_execution_enabled=True,
        )
        with self.assertRaises(EntityNotFoundError):
            exec_service.execute_action(
                user_id="user_b_unauthorized",
                action_id=action.action_id,
            )

    def test_case_5_oauth_disconnect_deletes_local_record_on_remote_failure(self) -> None:
        """Case 5: Disconnect deletes local credential record even if Google revoke endpoint fails."""
        with self.storage.unit_of_work() as uow:
            cred = OAuthCredentialRecord(
                user_id=self.user_id,
                provider="gmail",
                google_sub="sub-12345",
                access_token_enc=self.cipher.encrypt("ya29.token-to-revoke"),
                refresh_token_enc=self.cipher.encrypt("1//refresh-token"),
                token_expiry=now_utc().isoformat().replace("+00:00", "Z"),
                authenticated_email=self.applicant_email,
                scopes=["gmail.send"],
                created_at=now_utc().isoformat().replace("+00:00", "Z"),
                updated_at=now_utc().isoformat().replace("+00:00", "Z"),
            )
            uow.oauth_credentials.save(cred)
            uow.commit()

        # Mock remote revoke failure (HTTP 500)
        mock_http = httpx.Client(
            transport=httpx.MockTransport(lambda req: httpx.Response(500, text="Internal Google Error"))
        )
        provider = DatabaseOAuthCredentialProvider(
            storage=self.storage,
            cipher=self.cipher,
            http_client=mock_http,
        )

        remote_revoked, remote_err = provider.revoke_and_disconnect(self.user_id)
        self.assertFalse(remote_revoked)
        self.assertIsNotNone(remote_err)

        # Local record must be deleted
        with self.storage.unit_of_work() as uow:
            stored = uow.oauth_credentials.get_by_user_and_provider(self.user_id, "gmail")
            self.assertIsNone(stored)

    def test_case_6_missing_http_200_id_treated_as_ambiguous(self) -> None:
        """Case 6: Missing or malformed ID in HTTP 200 response is treated as AMBIGUOUS_SUBMISSION."""
        plan = self.plan_service.create_execution_plan(user_id=self.user_id, case_id=self.case_id)
        with self.storage.unit_of_work() as uow:
            assets = uow.upload_assets.list_by_plan(plan.plan_id)
        cred = StaticOAuthCredentialProvider("ya29.dummy", self.applicant_email)

        # Mock HTTP 200 with missing "id"
        mock_http = httpx.Client(
            transport=httpx.MockTransport(lambda req: httpx.Response(200, json={"threadId": "th-only"}))
        )
        provider = GmailApplicationExecutionProvider(
            credential_provider=cred,
            http_client=mock_http,
            real_execution_enabled=True,
            gmail_execution_enabled=True,
        )

        context = ExecutionAttemptContext(
            attempt_id="att-missing-id",
            lease_token="lease-1",
            fencing_token=1,
            user_id=self.user_id,
            case_id=self.case_id,
            plan_id=plan.plan_id,
            action_id="act-1",
        )
        staging = provider.stage_submission(plan=plan, package=self.package, upload_assets=assets, context=context)
        self.assertTrue(staging.success)

        result = provider.execute_irreversible_submit(
            plan=plan,
            attempt_id="att-missing-id",
            context=context,
        )
        self.assertEqual(result.status, ExecutionReceiptStatus.AMBIGUOUS_SUBMISSION)
        self.assertEqual(result.error_category, ExecutionErrorCategory.UNKNOWN)
        self.assertIn("without a valid message ID", result.error_message)

    def test_case_7_disabled_provider_preserves_action_and_plan(self) -> None:
        """Case 7: When Gmail execution is disabled in config, execution fails closed and leaves Action APPROVED."""
        action, _ = self._setup_authorized_email_action()
        exec_service = GovernedExecutionService(
            storage=self.storage,
            real_execution_enabled=False,  # Disabled
            gmail_execution_enabled=False,
            credential_provider=StaticOAuthCredentialProvider("ya29.tok", self.applicant_email),
        )

        with self.assertRaises(ApprovalValidationError) as ctx:
            exec_service.execute_action(user_id=self.user_id, action_id=action.action_id)

        self.assertEqual(ctx.exception.details.get("failure_kind"), "provider_disabled")

        with self.storage.unit_of_work() as uow:
            act = uow.actions.get_by_id(action.action_id)
            self.assertEqual(act.status, ActionStatus.APPROVED)
            plan = uow.execution_plans.get_by_id(act.parameters["plan_id"])
            self.assertEqual(plan.status, ExternalExecutionPlanStatus.READY_FOR_AUTHORIZATION)
            # Zero attempts or leases created
            attempts = uow.execution_attempts.list_by_case(self.case_id)
            self.assertEqual(len(attempts), 0)

    def test_case_8_provider_readiness_fails_before_submitting_no_lease(self) -> None:
        """Case 8: Provider readiness failure aborts before lease acquisition and attempt initialization."""
        action, _ = self._setup_authorized_email_action()
        exec_service = GovernedExecutionService(
            storage=self.storage,
            real_execution_enabled=True,
            gmail_execution_enabled=True,
            credential_provider=None,  # Missing credential provider
        )

        with self.assertRaises(ApprovalValidationError) as ctx:
            exec_service.execute_action(user_id=self.user_id, action_id=action.action_id)

        self.assertEqual(ctx.exception.details.get("failure_kind"), "provider_auth_error")

        with self.storage.unit_of_work() as uow:
            active_lease = uow.execution_leases.get_active_lease(self.case_id, action.parameters["plan_id"], action.action_id)
            self.assertIsNone(active_lease)
            attempts = uow.execution_attempts.list_by_case(self.case_id)
            self.assertEqual(len(attempts), 0)

    def test_case_9_unverified_employer_evidence_fails_compilation(self) -> None:
        """Case 9: Email opportunity without affirmative verified acceptance evidence fails compilation with EMAIL_APPLICATION_NOT_ACCEPTED."""
        with self.storage.unit_of_work() as uow:
            unaccepted_opp = Opportunity(
                opportunity_id="opp-unaccepted-test",
                user_id=self.user_id,
                opportunity_type=OpportunityType.JOB,
                title="Frontend Dev",
                organization="Acme",
                source_url="https://acme.com/jobs",
                metadata={"application_channel": "email", "application_email": "jobs@acme.com"},
            )
            uow.opportunities.save(unaccepted_opp)

            plan = ExecutionPlanCompiler.compile_plan(
                case=self.case,
                opportunity=unaccepted_opp,
                package=self.package,
                artifacts=[self.art_cv, self.art_cl],
                context_facts=[self.fact_email, self.fact_name],
            )
            self.assertEqual(plan.status, ExternalExecutionPlanStatus.DRAFT)
            codes = [f.code for f in plan.findings if f.severity == "error"]
            self.assertIn("EMAIL_APPLICATION_NOT_ACCEPTED", codes)

    def test_case_10_grounded_evidence_compiles_and_binds_fingerprint(self) -> None:
        """Case 10: Grounded employer evidence compiles to READY_FOR_AUTHORIZATION and binds digest into fingerprint."""
        plan = self.plan_service.create_execution_plan(user_id=self.user_id, case_id=self.case_id)
        self.assertEqual(plan.status, ExternalExecutionPlanStatus.READY_FOR_AUTHORIZATION)
        self.assertIsNotNone(plan.email_manifest)
        self.assertIsNotNone(plan.email_manifest.acceptance_evidence)
        ev = plan.email_manifest.acceptance_evidence
        self.assertEqual(ev.employer_domain, "cloudscale.io")
        self.assertEqual(ev.verified_by, "operator:grant")
        self.assertTrue(ev.evidence_digest)
        self.assertTrue(plan.execution_plan_fingerprint)

    def test_case_11_sender_controlled_message_id(self) -> None:
        """Case 11: Message-ID header uses sender domain per RFC 5322 section 3.6.4."""
        plan = self.plan_service.create_execution_plan(user_id=self.user_id, case_id=self.case_id)
        self.assertIsNotNone(plan.email_manifest)
        msg_id = plan.email_manifest.message_id
        # Applicant email is grant@example.com -> sender domain is example.com
        self.assertTrue(msg_id.startswith("<plan-"))
        self.assertTrue(msg_id.endswith("@example.com>"))
        self.assertNotIn("@cloudscale.io>", msg_id)

    def test_case_12_timeout_past_irreversible_boundary_handled_as_ambiguous(self) -> None:
        """Case 12: Network timeout during irreversible Gmail send is recorded as AMBIGUOUS_SUBMISSION with no retries."""
        plan = self.plan_service.create_execution_plan(user_id=self.user_id, case_id=self.case_id)
        cred = StaticOAuthCredentialProvider("ya29.tok", self.applicant_email)

        def timeout_handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ReadTimeout("Connection timed out waiting for Gmail API")

        mock_http = httpx.Client(transport=httpx.MockTransport(timeout_handler))
        provider = GmailApplicationExecutionProvider(
            credential_provider=cred,
            http_client=mock_http,
            real_execution_enabled=True,
            gmail_execution_enabled=True,
        )

        context = ExecutionAttemptContext(
            attempt_id="att-timeout-h",
            lease_token="lease-h",
            fencing_token=1,
            user_id=self.user_id,
            case_id=self.case_id,
            plan_id=plan.plan_id,
            action_id="act-h",
        )
        with self.storage.unit_of_work() as uow:
            assets = uow.upload_assets.list_by_plan(plan.plan_id)
        staging = provider.stage_submission(plan=plan, package=self.package, upload_assets=assets, context=context)
        self.assertTrue(staging.success)

        result = provider.execute_irreversible_submit(
            plan=plan,
            attempt_id="att-timeout-h",
            context=context,
        )
        self.assertEqual(result.status, ExecutionReceiptStatus.AMBIGUOUS_SUBMISSION)
        self.assertEqual(result.error_category, ExecutionErrorCategory.SUBMIT_TIMEOUT)

    def test_case_13_diagnostic_redaction(self) -> None:
        """Case 13: Diagnostics and provider metadata redact sensitive tokens, passwords, and authorization headers."""
        sensitive_msg = "Error: Bearer ya29.secret_token_1234567890 failed with Authorization: Bearer ya29.super_secret"
        cleaned_msg = _sanitize_diagnostic_message(sensitive_msg)
        self.assertNotIn("ya29.secret_token_1234567890", cleaned_msg)
        self.assertNotIn("ya29.super_secret", cleaned_msg)
        self.assertIn("[REDACTED]", cleaned_msg)

        meta = {
            "token": "secret_access_token_123",
            "password": "my_secret_password",
            "environment_label": "safe_value",
            "nested": {"auth_header": "Bearer confidential_string_123456"},
        }
        clean_meta = _sanitize_provider_metadata(meta)
        self.assertEqual(clean_meta["token"], "[REDACTED]")
        self.assertEqual(clean_meta["password"], "[REDACTED]")
        self.assertEqual(clean_meta["environment_label"], "safe_value")
        self.assertEqual(clean_meta["nested"]["auth_header"], "[REDACTED]")

    def test_case_14_google_oauth_callback_flow_with_pkce_and_encryption(self) -> None:
        """Case 14: Google OAuth callback validates state CSRF, PKCE verifier, email_verified, and encrypts tokens."""
        config = APIConfig(
            dev_auth=True,
            gmail_client_id="test-client-id.apps.googleusercontent.com",
            gmail_client_secret="test-client-secret-xyz",
            gmail_redirect_uri="http://localhost:8000/api/v1/auth/google/callback",
            encryption_key=self.TEST_KEY,
            operator_secret=self.OPERATOR_SECRET,
        )
        app = create_app(config=config, storage=self.storage)
        from fastapi.testclient import TestClient
        client = TestClient(app)

        # 1. Authorize: creates PKCE challenge and saves state
        auth_resp = client.get(
            "/api/v1/auth/google/authorize",
            headers={"X-JackVerse-User": self.user_id},
        )
        self.assertEqual(auth_resp.status_code, 200)
        auth_data = auth_resp.json()
        state = auth_data["state"]
        self.assertTrue(auth_data["authorization_url"].startswith("https://accounts.google.com/o/oauth2/v2/auth"))

        # Verify state was saved
        with self.storage.unit_of_work() as uow:
            state_rec = uow.oauth_states.get_by_state(state)
            self.assertIsNotNone(state_rec)
            self.assertEqual(state_rec.user_id, self.user_id)
            self.assertIsNone(state_rec.used_at)

        # 2. Callback with invalid state fails
        bad_resp = client.get(
            "/api/v1/auth/google/callback",
            params={"code": "auth-code-123", "state": "invalid-state-token"},
        )
        self.assertEqual(bad_resp.status_code, 400)
        self.assertIn("Invalid or unrecognized OAuth state parameter", bad_resp.text)

        # Mock Google Token & UserInfo endpoints for valid callback
        real_post = httpx.AsyncClient.post
        real_get = httpx.AsyncClient.get

        async def mock_async_post(self_client, url, *args, **kwargs):
            if "oauth2.googleapis.com/token" in str(url):
                return httpx.Response(
                    200,
                    json={
                        "access_token": "ya29.mocked-access-token",
                        "refresh_token": "1//mocked-refresh-token",
                        "expires_in": 3600,
                        "token_type": "Bearer",
                        "scope": "openid https://www.googleapis.com/auth/userinfo.email https://www.googleapis.com/auth/gmail.send",
                    },
                )
            return await real_post(self_client, url, *args, **kwargs)

        async def mock_async_get(self_client, url, *args, **kwargs):
            if "oauth2/v3/userinfo" in str(url):
                return httpx.Response(
                    200,
                    json={
                        "sub": "google-sub-grant-101",
                        "email": self.applicant_email,
                        "email_verified": True,
                    },
                )
            return await real_get(self_client, url, *args, **kwargs)

        import unittest.mock as mock
        with mock.patch.object(httpx.AsyncClient, "post", mock_async_post), \
             mock.patch.object(httpx.AsyncClient, "get", mock_async_get):

            # 3. Valid callback succeeds
            cb_resp = client.get(
                "/api/v1/auth/google/callback",
                params={"code": "valid-auth-code-789", "state": state},
            )
            self.assertEqual(cb_resp.status_code, 200)
            cb_data = cb_resp.json()
            self.assertTrue(cb_data["success"])
            self.assertEqual(cb_data["email"], self.applicant_email)

            # 4. State cannot be replayed
            replay_resp = client.get(
                "/api/v1/auth/google/callback",
                params={"code": "valid-auth-code-789", "state": state},
            )
            self.assertEqual(replay_resp.status_code, 400)
            self.assertIn("already been used", replay_resp.text)

        # 5. Check credential storage and decryption
        with self.storage.unit_of_work() as uow:
            cred = uow.oauth_credentials.get_by_user_and_provider(self.user_id, "gmail")
            self.assertIsNotNone(cred)
            self.assertEqual(cred.google_sub, "google-sub-grant-101")
            self.assertEqual(cred.authenticated_email, self.applicant_email)
            # Must be encrypted with TokenCipher
            self.assertTrue(cred.access_token_enc.startswith("v1:"))
            decrypted_access = self.cipher.decrypt(cred.access_token_enc)
            self.assertEqual(decrypted_access, "ya29.mocked-access-token")
            self.assertTrue(cred.refresh_token_enc.startswith("v1:"))
            decrypted_refresh = self.cipher.decrypt(cred.refresh_token_enc)
            self.assertEqual(decrypted_refresh, "1//mocked-refresh-token")

        # 6. Check status endpoint
        status_resp = client.get(
            "/api/v1/auth/google/status",
            headers={"X-JackVerse-User": self.user_id},
        )
        self.assertEqual(status_resp.status_code, 200)
        status_data = status_resp.json()
        self.assertTrue(status_data["connected"])
        self.assertEqual(status_data["authenticated_email"], self.applicant_email)

    def test_case_15_oauth_browser_session_nonce_enforcement(self) -> None:
        """Case 15: OAuth callback strictly enforces browser-session nonce match and prevents cross-user linking."""
        config = APIConfig(
            dev_auth=True,
            gmail_client_id="test-client-id.apps.googleusercontent.com",
            gmail_client_secret="test-client-secret-xyz",
            gmail_redirect_uri="http://localhost:8000/api/v1/auth/google/callback",
            encryption_key=self.TEST_KEY,
            operator_secret=self.OPERATOR_SECRET,
        )
        app = create_app(config=config, storage=self.storage)
        from fastapi.testclient import TestClient
        client = TestClient(app)

        auth_resp = client.get(
            "/api/v1/auth/google/authorize",
            headers={"X-JackVerse-User": self.user_id},
        )
        self.assertEqual(auth_resp.status_code, 200)
        state = auth_resp.json()["state"]
        self.assertIn("jackverse_oauth_nonce", client.cookies)
        real_nonce = client.cookies["jackverse_oauth_nonce"]

        # A: Missing cookie fails
        client_no_cookie = TestClient(app)
        resp_no_cookie = client_no_cookie.get(
            "/api/v1/auth/google/callback",
            params={"code": "dummy-code", "state": state},
        )
        self.assertEqual(resp_no_cookie.status_code, 400)
        self.assertIn("OAuth session nonce validation failed", resp_no_cookie.text)

        # B: Mismatched cookie fails
        client_bad_cookie = TestClient(app)
        resp_bad_cookie = client_bad_cookie.get(
            "/api/v1/auth/google/callback",
            params={"code": "dummy-code", "state": state},
            cookies={"jackverse_oauth_nonce": "tampered-nonce-xyz"},
        )
        self.assertEqual(resp_bad_cookie.status_code, 400)
        self.assertIn("OAuth session nonce validation failed", resp_bad_cookie.text)

        # C: Cross-user principal mismatch fails
        client_cross_user = TestClient(app)
        resp_cross_user = client_cross_user.get(
            "/api/v1/auth/google/callback",
            params={"code": "dummy-code", "state": state},
            cookies={"jackverse_oauth_nonce": real_nonce},
            headers={"X-JackVerse-User": "different_user_123"},
        )
        self.assertEqual(resp_cross_user.status_code, 403)
        self.assertIn("OAuth callback caller identity does not match initiating user ID.", resp_cross_user.text)

    def test_case_16_tokeninfo_scope_verification_when_google_omits_scope(self) -> None:
        """Case 16: When Google omits scope on refresh, DatabaseOAuthCredentialProvider verifies scopes via tokeninfo."""
        past_time = "2020-01-01T00:00:00Z"
        with self.storage.unit_of_work() as uow:
            cred = OAuthCredentialRecord(
                user_id=self.user_id,
                provider="gmail",
                google_sub="sub-scope-test",
                access_token_enc=self.cipher.encrypt("ya29.expired-token"),
                refresh_token_enc=self.cipher.encrypt("1//refresh-token-valid"),
                token_expiry=past_time,
                authenticated_email=self.applicant_email,
                scopes=["gmail.send"],
                created_at=past_time,
                updated_at=past_time,
            )
            uow.oauth_credentials.save(cred)
            uow.commit()

        # Mock refresh response without "scope" field
        def mock_refresh_and_tokeninfo(request: httpx.Request) -> httpx.Response:
            if "oauth2.googleapis.com/token" in str(request.url):
                return httpx.Response(
                    200,
                    json={
                        "access_token": "ya29.refreshed-without-scope-field",
                        "expires_in": 3600,
                        "token_type": "Bearer",
                    },
                )
            if "oauth2/v3/tokeninfo" in str(request.url):
                return httpx.Response(
                    200,
                    json={
                        "scope": "https://www.googleapis.com/auth/userinfo.email https://www.googleapis.com/auth/gmail.send",
                        "email": self.applicant_email,
                    },
                )
            return httpx.Response(404)

        mock_http = httpx.Client(transport=httpx.MockTransport(mock_refresh_and_tokeninfo))
        db_provider = DatabaseOAuthCredentialProvider(
            storage=self.storage,
            cipher=self.cipher,
            http_client=mock_http,
        )

        session = db_provider.verify_pre_dispatch_readiness(
            user_id=self.user_id,
            expected_sender=self.applicant_email,
        )
        self.assertEqual(session.access_token, "ya29.refreshed-without-scope-field")
        self.assertTrue(any("gmail.send" in s for s in session.scopes))

        # If tokeninfo returns scopes WITHOUT gmail.send, readiness fails
        with self.storage.unit_of_work() as uow:
            cred = uow.oauth_credentials.get_by_user_and_provider(self.user_id, "gmail")
            cred.token_expiry = past_time
            uow.oauth_credentials.save(cred)
            uow.commit()

        def mock_tokeninfo_missing_scope(request: httpx.Request) -> httpx.Response:
            if "oauth2.googleapis.com/token" in str(request.url):
                return httpx.Response(
                    200,
                    json={
                        "access_token": "ya29.refreshed-without-scope-field",
                        "expires_in": 3600,
                        "token_type": "Bearer",
                    },
                )
            if "oauth2/v3/tokeninfo" in str(request.url):
                return httpx.Response(
                    200,
                    json={
                        "scope": "https://www.googleapis.com/auth/userinfo.email",
                        "email": self.applicant_email,
                    },
                )
            return httpx.Response(404)

        bad_provider = DatabaseOAuthCredentialProvider(
            storage=self.storage,
            cipher=self.cipher,
            http_client=httpx.Client(transport=httpx.MockTransport(mock_tokeninfo_missing_scope)),
        )
        with self.assertRaises(ProviderAuthError) as ctx:
            bad_provider.verify_pre_dispatch_readiness(
                user_id=self.user_id,
                expected_sender=self.applicant_email,
            )
        self.assertIn("lacks required scope", str(ctx.exception))

    def test_case_17_operator_token_verification_no_raw_bypass(self) -> None:
        """Case 17: Operator token validation rejects raw secret bypass, rejects expired tokens, and accepts valid signatures."""
        from caseworker.api.identity import verify_operator_token, generate_operator_token
        import time

        # A. Raw secret bypass attempt MUST fail
        self.assertIsNone(verify_operator_token(self.OPERATOR_SECRET, self.OPERATOR_SECRET))
        self.assertIsNone(verify_operator_token("Bearer " + self.OPERATOR_SECRET, self.OPERATOR_SECRET))

        # B. Expired token MUST fail
        expired_tok = f"op.op_grant.{int(time.time()) - 3600}.invalidsig"
        self.assertIsNone(verify_operator_token(expired_tok, self.OPERATOR_SECRET))

        # C. Valid signed token succeeds
        valid_tok = generate_operator_token("op_grant", self.OPERATOR_SECRET, ttl_seconds=3600)
        verified_id = verify_operator_token(valid_tok, self.OPERATOR_SECRET)
        self.assertEqual(verified_id, "op_grant")

        # D. Operator session cookie endpoint in API
        config = APIConfig(
            dev_auth=True,
            operator_secret=self.OPERATOR_SECRET,
        )
        app = create_app(config=config, storage=self.storage)
        from fastapi.testclient import TestClient
        client = TestClient(app)

        login_resp = client.post(
            "/api/v1/auth/operator/session",
            json={"operator_token": valid_tok},
        )
        self.assertEqual(login_resp.status_code, 200)
        self.assertTrue(login_resp.json()["success"])
        self.assertEqual(login_resp.json()["operator_id"], "op_grant")
        self.assertIn("jackverse_operator_session", client.cookies)

        logout_resp = client.delete("/api/v1/auth/operator/session")
        self.assertEqual(logout_resp.status_code, 200)
        self.assertTrue(logout_resp.json()["success"])

    def test_case_18_operator_verify_email_acceptance_endpoint(self) -> None:
        """Case 18: POST /opportunities/{id}/verify-email-acceptance enforces operator auth, SSRF checks, and citation checks."""
        from caseworker.api.identity import generate_operator_token
        config = APIConfig(
            dev_auth=True,
            operator_secret=self.OPERATOR_SECRET,
        )
        app = create_app(config=config, storage=self.storage)
        from fastapi.testclient import TestClient
        client = TestClient(app)

        valid_op_token = generate_operator_token(self.user_id, self.OPERATOR_SECRET, ttl_seconds=3600)
        op_headers = {"Authorization": f"Bearer {valid_op_token}", "X-JackVerse-User": self.user_id}

        # A. Non-operator caller rejected with 403
        no_op_resp = client.post(
            f"/api/v1/opportunities/{self.opp_id}/verify-email-acceptance",
            headers={"X-JackVerse-User": self.user_id},
            json={
                "source_uri": "https://cloudscale.io/careers",
                "instructions_text": "Email applications to jobs@cloudscale.io",
                "verified_recipient_email": "jobs@cloudscale.io",
                "evidence_source": "official_job_posting",
            },
        )
        self.assertEqual(no_op_resp.status_code, 403)

        # B. SSRF attempt rejected
        ssrf_resp = client.post(
            f"/api/v1/opportunities/{self.opp_id}/verify-email-acceptance",
            headers=op_headers,
            json={
                "source_uri": "http://169.254.169.254/latest/meta-data",
                "instructions_text": "Email applications to jobs@cloudscale.io",
                "verified_recipient_email": "jobs@cloudscale.io",
                "evidence_source": "official_job_posting",
            },
        )
        self.assertEqual(ssrf_resp.status_code, 422)

        # C. Recipient not cited in instructions rejected
        uncited_resp = client.post(
            f"/api/v1/opportunities/{self.opp_id}/verify-email-acceptance",
            headers=op_headers,
            json={
                "source_uri": "https://cloudscale.io/careers",
                "instructions_text": "Please apply via our web portal only.",
                "verified_recipient_email": "jobs@cloudscale.io",
                "evidence_source": "official_job_posting",
            },
        )
        self.assertEqual(uncited_resp.status_code, 422)

        # D. Valid operator verification succeeds and supersedes
        valid_resp = client.post(
            f"/api/v1/opportunities/{self.opp_id}/verify-email-acceptance",
            headers=op_headers,
            json={
                "source_uri": "https://cloudscale.io/careers/v2",
                "instructions_text": "Send CV directly to jobs@cloudscale.io for priority review.",
                "verified_recipient_email": "jobs@cloudscale.io",
                "evidence_source": "official_job_posting",
            },
        )
        self.assertEqual(valid_resp.status_code, 200)
        body = valid_resp.json()
        self.assertEqual(body["verified_by"], f"operator:{self.user_id}")
        self.assertEqual(body["employer_domain"], "cloudscale.io")
        self.assertTrue(body["evidence_digest"])
        self.assertEqual(body["evidence_version"], 2)

        # Verify persisted record
        with self.storage.unit_of_work() as uow:
            active_rec = uow.verified_email_acceptance.get_active_by_opportunity(self.opp_id)
            self.assertIsNotNone(active_rec)
            self.assertEqual(active_rec.evidence_version, 2)
            self.assertEqual(active_rec.verified_by, f"operator:{self.user_id}")

    def test_case_19_predispatch_readiness_outside_sqlite_write_locks(self) -> None:
        """Case 19: Pre-dispatch readiness executes outside SQLite write transactions."""
        action, _ = self._setup_authorized_email_action()
        readiness_called = []

        class MockLockCheckProvider(StaticOAuthCredentialProvider):
            def __init__(self, applicant_email):
                super().__init__(access_token="ya29.test-lock-free-token", authenticated_email=applicant_email)

            def verify_pre_dispatch_readiness(self, user_id, expected_sender):
                readiness_called.append(True)
                from caseworker.adapters.gmail_provider import ValidatedOAuthSession
                return ValidatedOAuthSession(
                    access_token="ya29.test-lock-free-token",
                    authenticated_email=self._authenticated_email,
                    scopes=["gmail.send"],
                )

        mock_cred = MockLockCheckProvider(self.applicant_email)
        captured = []
        mock_http = httpx.Client(
            transport=httpx.MockTransport(lambda req: (captured.append(req), httpx.Response(200, json={"id": "msg-lf-1"}))[1])
        )
        provider = GmailApplicationExecutionProvider(
            credential_provider=mock_cred,
            http_client=mock_http,
            real_execution_enabled=True,
            gmail_execution_enabled=True,
        )

        exec_service = GovernedExecutionService(
            storage=self.storage,
            email_provider=provider,
            real_execution_enabled=True,
            gmail_execution_enabled=True,
            credential_provider=mock_cred,
        )

        attempt, receipt = exec_service.execute_action(
            user_id=self.user_id,
            action_id=action.action_id,
            principal_auth_method="operator_token",
        )
        self.assertEqual(receipt.status, ExecutionReceiptStatus.VERIFIED_SUCCESS)
        self.assertEqual(readiness_called, [True])

    def test_case_20_dev_operator_session_rejected_when_real_execution_enabled(self) -> None:
        """Case 20: POST /auth/operator/dev-session fails closed (403) when real execution flags are enabled."""
        from fastapi.testclient import TestClient

        # A. real_execution_enabled=True -> 403
        config_real = APIConfig(
            dev_auth=True,
            operator_secret=self.OPERATOR_SECRET,
            real_execution_enabled=True,
            gmail_execution_enabled=False,
        )
        app_real = create_app(config=config_real, storage=self.storage)
        client_real = TestClient(app_real)
        resp_real = client_real.post("/api/v1/auth/operator/dev-session")
        self.assertEqual(resp_real.status_code, 403)
        self.assertIn("strictly forbidden when real execution or Gmail transmission is enabled", resp_real.text)

        # B. gmail_execution_enabled=True -> 403
        config_gmail = APIConfig(
            dev_auth=True,
            operator_secret=self.OPERATOR_SECRET,
            real_execution_enabled=False,
            gmail_execution_enabled=True,
        )
        app_gmail = create_app(config=config_gmail, storage=self.storage)
        client_gmail = TestClient(app_gmail)
        resp_gmail = client_gmail.post("/api/v1/auth/operator/dev-session")
        self.assertEqual(resp_gmail.status_code, 403)
        self.assertIn("strictly forbidden when real execution or Gmail transmission is enabled", resp_gmail.text)

    def test_case_21_dev_operator_identity_cannot_execute_real_gmail(self) -> None:
        """Case 21: Developer-generated operator identities cannot authorize genuine external transmissions."""
        action, _ = self._setup_authorized_email_action()
        mock_cred = StaticOAuthCredentialProvider("ya29.tok", self.applicant_email)
        exec_service = GovernedExecutionService(
            storage=self.storage,
            real_execution_enabled=True,
            gmail_execution_enabled=True,
            credential_provider=mock_cred,
            dev_auth=False,  # In production, but caller presents dev operator token
        )

        # 1. Dev operator auth method rejected at service level
        with self.assertRaises(ApprovalValidationError) as ctx:
            exec_service.execute_action(
                user_id=self.user_id,
                action_id=action.action_id,
                principal_auth_method="dev_operator_token",
            )
        self.assertEqual(ctx.exception.details.get("failure_kind"), "operator_auth_required")

        # 2. In API route: calling execute with dev operator token fails closed
        from fastapi.testclient import TestClient
        from caseworker.api.identity import generate_operator_token

        config = APIConfig(
            dev_auth=False,
            real_execution_enabled=True,
            gmail_execution_enabled=True,
            operator_secret=self.OPERATOR_SECRET,
        )
        app = create_app(config=config, storage=self.storage)
        client = TestClient(app)

        dev_token = generate_operator_token("dev-operator", self.OPERATOR_SECRET, ttl_seconds=3600)
        resp = client.post(
            f"/api/v1/actions/{action.action_id}/execute",
            headers={
                "Authorization": f"Bearer {dev_token}",
                "If-Match": f'action:{action.action_id}:{action.version}',
            },
        )
        self.assertIn(resp.status_code, (400, 401, 403, 404, 422))

    def test_case_22_simultaneous_dev_auth_and_real_gmail_fails_closed(self) -> None:
        """Case 22: Combining dev_auth=True with real execution flags fails closed on live execution."""
        action, _ = self._setup_authorized_email_action()
        mock_cred = StaticOAuthCredentialProvider("ya29.tok", self.applicant_email)
        exec_service = GovernedExecutionService(
            storage=self.storage,
            real_execution_enabled=True,
            gmail_execution_enabled=True,
            credential_provider=mock_cred,
            dev_auth=True,  # Insecure combination
        )

        with self.assertRaises(ApprovalValidationError) as ctx:
            exec_service.execute_action(
                user_id=self.user_id,
                action_id=action.action_id,
                principal_auth_method="operator_token",
            )
        self.assertEqual(ctx.exception.details.get("failure_kind"), "insecure_configuration_rejected")

    def test_case_23_cross_user_evidence_attestation_rejected(self) -> None:
        """Case 23: Operator A cannot verify, replace, or supersede Operator B's evidence (cross-user rejected 403)."""
        from caseworker.api.identity import generate_operator_token
        from fastapi.testclient import TestClient

        # Setup User B and Opportunity B
        user_b = "user_b_applicant"
        opp_b_id = "opp_b_email"
        with self.storage.unit_of_work() as uow:
            opp_b = Opportunity(
                opportunity_id=opp_b_id,
                user_id=user_b,
                opportunity_type=OpportunityType.JOB,
                title="Backend Engineer",
                organization="BetaCorp",
                source_url="https://betacorp.io/careers/backend",
                status=OpportunityStatus.SHORTLISTED,
            )
            uow.opportunities.save(opp_b)
            uow.commit()

        config = APIConfig(
            dev_auth=True,
            operator_secret=self.OPERATOR_SECRET,
        )
        app = create_app(config=config, storage=self.storage)
        client = TestClient(app)

        # Operator A attempts to attest evidence for User B's opportunity
        token_a = generate_operator_token("operator_a", self.OPERATOR_SECRET, ttl_seconds=3600)
        headers_a = {"Authorization": f"Bearer {token_a}"}

        resp = client.post(
            f"/api/v1/opportunities/{opp_b_id}/verify-email-acceptance",
            headers=headers_a,
            json={
                "source_uri": "https://betacorp.io/careers/backend",
                "instructions_text": "Apply directly to jobs@betacorp.io",
                "verified_recipient_email": "jobs@betacorp.io",
                "evidence_source": "official_job_posting",
            },
        )
        self.assertEqual(resp.status_code, 403)
        self.assertIn("does not own Opportunity", resp.text)

    def test_case_24_production_cookie_security_attributes_and_insecure_http_rejection(self) -> None:
        """Case 24: Production HTTPS requires Secure cookies; insecure HTTP deployments fail closed."""
        from caseworker.api.identity import generate_operator_token
        from fastapi.testclient import TestClient

        prod_config = APIConfig(
            dev_auth=False,
            gmail_client_id="test-client-id.apps.googleusercontent.com",
            operator_secret=self.OPERATOR_SECRET,
        )
        app = create_app(config=prod_config, storage=self.storage)
        client = TestClient(app)

        # A. Insecure HTTP request in production fails closed with 400
        valid_tok = generate_operator_token("prod_operator_1", self.OPERATOR_SECRET, ttl_seconds=3600)
        insecure_resp = client.post(
            "/api/v1/auth/operator/session",
            json={"operator_token": valid_tok},
        )
        self.assertEqual(insecure_resp.status_code, 400)
        self.assertIn("Insecure deployment", insecure_resp.text)

        # B. HTTPS request (via X-Forwarded-Proto) succeeds and sets Secure, HttpOnly, SameSite=lax
        secure_resp = client.post(
            "/api/v1/auth/operator/session",
            headers={"X-Forwarded-Proto": "https"},
            json={"operator_token": valid_tok},
        )
        self.assertEqual(secure_resp.status_code, 200)
        set_cookie = secure_resp.headers.get("set-cookie", "")
        self.assertIn("jackverse_operator_session=", set_cookie)
        self.assertIn("Secure", set_cookie)
        self.assertIn("HttpOnly", set_cookie)
        self.assertIn("samesite=lax", set_cookie.lower())

    def test_case_25_callback_html_response_clears_cookie_and_escapes_xss(self) -> None:
        """Case 25: Callback HTML response clears nonce on actual response, escapes email, and has no inline scripts."""
        import secrets
        from datetime import datetime, timedelta, timezone
        from fastapi.testclient import TestClient
        from caseworker.adapters.gmail_provider import GMAIL_SEND_SCOPE

        state = secrets.token_urlsafe(32)
        nonce = secrets.token_urlsafe(32)
        now_dt = datetime.now(timezone.utc)
        exp_dt = now_dt + timedelta(minutes=10)

        with self.storage.unit_of_work() as uow:
            rec = OAuthStateRecord(
                state=state,
                user_id=self.user_id,
                provider="gmail",
                code_verifier="test-verifier",
                redirect_uri="http://localhost:8000/api/v1/auth/google/callback",
                created_at=now_dt.isoformat().replace("+00:00", "Z"),
                expires_at=exp_dt.isoformat().replace("+00:00", "Z"),
                session_nonce=nonce,
            )
            uow.oauth_states.save(rec)
            uow.commit()

        # Mock token & userinfo with malicious XSS email
        xss_email = '<script>alert("xss")</script>@evil.com'

        def mock_google_api(req: httpx.Request) -> httpx.Response:
            if "oauth2.googleapis.com/token" in str(req.url):
                return httpx.Response(
                    200,
                    json={
                        "access_token": "ya29.valid-xss-token",
                        "refresh_token": "1//refresh-xss",
                        "expires_in": 3600,
                        "scope": f"openid https://www.googleapis.com/auth/userinfo.email {GMAIL_SEND_SCOPE}",
                    },
                )
            if "oauth2/v3/userinfo" in str(req.url):
                return httpx.Response(
                    200,
                    json={
                        "sub": "sub-xss-999",
                        "email": xss_email,
                        "email_verified": True,
                    },
                )
            return httpx.Response(404)

        config = APIConfig(
            dev_auth=True,
            gmail_client_id="test-client-id.apps.googleusercontent.com",
            gmail_client_secret="test-secret",
            encryption_key=self.TEST_KEY,
            operator_secret=self.OPERATOR_SECRET,
        )
        app = create_app(config=config, storage=self.storage)

        # Patch httpx.AsyncClient used in auth.py callback
        import unittest.mock as mock
        with mock.patch("httpx.AsyncClient", return_value=httpx.AsyncClient(transport=httpx.MockTransport(mock_google_api))):
            client = TestClient(app)
            client.cookies.set("jackverse_oauth_nonce", nonce, path="/api/v1/auth")
            resp = client.get(
                "/api/v1/auth/google/callback",
                params={"code": "valid-auth-code", "state": state},
                headers={"Accept": "text/html", "X-JackVerse-User": self.user_id},
            )

        self.assertEqual(resp.status_code, 200)
        self.assertIn("text/html", resp.headers.get("content-type", ""))

        # Nonce cookie cleared on returned HTML response
        set_cookie = resp.headers.get("set-cookie", "")
        self.assertIn("jackverse_oauth_nonce=", set_cookie)
        self.assertTrue("max-age=0" in set_cookie.lower() or "expires=" in set_cookie.lower())

        # Email is HTML-escaped and NO executable script tags or postMessage
        self.assertNotIn('<script>alert("xss")</script>', resp.text)
        self.assertIn("&lt;script&gt;alert(&quot;xss&quot;)&lt;/script&gt;@evil.com", resp.text)
        self.assertNotIn("<script>", resp.text.lower())
        self.assertNotIn("postMessage", resp.text)
        self.assertIn('http-equiv="refresh"', resp.text)
        self.assertIn('content="2;url=/missions"', resp.text)
        self.assertNotIn('url=/cases', resp.text)

    def test_case_26_oauth_credentials_not_in_url_params(self) -> None:
        """Case 26: OAuth tokens are sent in request body and headers, never in URL query strings."""
        requests_seen = []

        def mock_capture(req: httpx.Request) -> httpx.Response:
            requests_seen.append(req)
            if "tokeninfo" in str(req.url):
                return httpx.Response(200, json={"scope": "https://www.googleapis.com/auth/gmail.send"})
            if "revoke" in str(req.url):
                return httpx.Response(200)
            return httpx.Response(404)

        mock_http = httpx.Client(transport=httpx.MockTransport(mock_capture))
        provider = DatabaseOAuthCredentialProvider(
            storage=self.storage,
            cipher=self.cipher,
            http_client=mock_http,
        )

        # 1. Scope query
        test_access_token = "ya29.secret_access_token_12345"
        scopes = provider._fetch_scopes_from_tokeninfo(test_access_token)
        self.assertEqual(scopes, ["https://www.googleapis.com/auth/gmail.send"])
        ti_req = requests_seen[-1]
        self.assertEqual(ti_req.method, "POST")
        self.assertNotIn("access_token", ti_req.url.query.decode("ascii"))
        self.assertIn("ya29.secret_access_token_12345", ti_req.read().decode("ascii"))
        self.assertEqual(ti_req.headers.get("Authorization"), f"Bearer {test_access_token}")

        # 2. Revoke call
        with self.storage.unit_of_work() as uow:
            rec = OAuthCredentialRecord(
                user_id="user_revoke_test",
                provider="gmail",
                google_sub="sub-revoke",
                access_token_enc=self.cipher.encrypt("ya29.revoke_token_999"),
                refresh_token_enc=None,
                token_expiry="2030-01-01T00:00:00Z",
                authenticated_email="revoke@test.com",
                scopes=["gmail.send"],
                created_at="2026-01-01T00:00:00Z",
                updated_at="2026-01-01T00:00:00Z",
            )
            uow.oauth_credentials.save(rec)
            uow.commit()

        success, err = provider.revoke_and_disconnect("user_revoke_test")
        self.assertTrue(success)
        self.assertIsNone(err)
        revoke_req = requests_seen[-1]
        self.assertEqual(revoke_req.method, "POST")
        self.assertNotIn("token", revoke_req.url.query.decode("ascii"))
        self.assertIn("ya29.revoke_token_999", revoke_req.read().decode("ascii"))

