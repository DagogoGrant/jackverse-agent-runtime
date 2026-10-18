"""Comprehensive unit tests for Phase 5.3.2 Exact Transmission Authorization.

Tests:
1. Exact plan_id authorization request transitions Case ACTION_REQUIRED -> AWAITING_APPROVAL.
2. Idempotency ordering: Retrying authorization while Case is already AWAITING_APPROVAL returns existing pair.
3. Already-approved authorization returns existing pair without creating duplicates.
4. Minimal privacy-safe Action parameters (hashes/counts only, zero raw applicant/screening values).
5. ActionPolicy derives RiskLevel.HIGH and requires_approval=True server-side.
6. Approval success transitions Approval -> APPROVED, Action -> APPROVED (NOT executing/succeeded), Case -> ACTION_REQUIRED.
7. Explicit human rejection transitions Approval -> REJECTED, Action -> REJECTED, Case -> ACTION_REQUIRED,
   while ExternalExecutionPlan strictly remains READY_FOR_AUTHORIZATION.
8. Approval-time revalidation failure due to stale plan (e.g. modified Vault fact, tampered asset bytes)
   supersedes plan, cancels Approval/Action, and unwinds Case to ACTION_REQUIRED.
9. Approval-time failure due to action binding tampering cancels Approval/Action and unwinds Case,
   while preserving the valid ExternalExecutionPlan in READY_FOR_AUTHORIZATION.
10. DRAFT plan authorization attempt is rejected without mutating or superseding the plan.
11. API route POST /api/v1/cases/{case_id}/execution/plan/{plan_id}/request-authorization.
"""

from __future__ import annotations

import unittest
from fastapi.testclient import TestClient

from caseworker.api.app import create_app
from caseworker.api.config import APIConfig
from caseworker.domain.action_policy import ActionPolicy
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
    RiskLevel,
    VerificationStatus,
)
from caseworker.domain.errors import (
    ApprovalValidationError,
    DomainValidationError,
    EntityNotFoundError,
    InvalidStateTransitionError,
)
from caseworker.domain.execution_plan import (
    TargetFieldBinding,
    TargetInteractionSchema,
    TargetSchemaField,
    TargetSchemaUploadSlot,
    TargetSubmitControlSpec,
)
from caseworker.domain.opportunity import Opportunity
from caseworker.domain.preparation_analyzer import PreparationAnalyzer
from caseworker.domain.types import compute_sha256_bytes
from caseworker.persistence.sqlite import SQLiteCaseworkerStorage
from caseworker.services.approval_service import ApprovalService
from caseworker.services.execution_plan_service import ExecutionPlanService
from caseworker.services.package_service import PackageService


class TestTransmissionAuthorization(unittest.TestCase):
    """Unit and service-level test suite for Phase 5.3.2 transmission authorization."""

    def setUp(self) -> None:
        self.storage = SQLiteCaseworkerStorage(db_path=":memory:")
        self.package_service = PackageService(self.storage)
        self.approval_service = ApprovalService(self.storage)
        self.plan_service = ExecutionPlanService(self.storage)

        self.user_id = "user_grant_tx"
        self.case_id = "case_tx_001"
        self.opp_id = "opp_tx_001"
        self.set_id = "set_tx_001"

        with self.storage.unit_of_work() as uow:
            # 1. Create Case
            self.case = Case(
                case_id=self.case_id,
                user_id=self.user_id,
                title="SRE Application at Apex",
                goal="Submit verified job application",
                case_type=CaseType.JOB_APPLICATION,
                status=CaseStatus.PLANNING,
            )
            uow.cases.save(self.case)

            # 2. Create Opportunity
            self.opportunity = Opportunity(
                opportunity_id=self.opp_id,
                user_id=self.user_id,
                opportunity_type=OpportunityType.JOB,
                title="Staff Site Reliability Engineer",
                organization="Apex Infrastructure",
                source_url="https://linkedin.com/jobs/tx1",
                requirements=["Python architecture (Mandatory)"],
                metadata={"application_url": "https://apex.apply.com/roles/tx1"},
                converted_case_id=self.case_id,
                status=OpportunityStatus.SHORTLISTED,
            )
            uow.opportunities.save(self.opportunity)

            # 3. Create Verified Vault Facts
            self.fact_name = ContextFact(
                fact_id="fact-tx-name",
                user_id=self.user_id,
                namespace="identity",
                key="name",
                value="Grant Dagogo-Jack",
                verification_status=VerificationStatus.USER_VERIFIED,
                allowed_purposes=["job_application", "case_preparation"],
            )
            uow.context.save(self.fact_name)

            self.fact_py = ContextFact(
                fact_id="fact-tx-py",
                user_id=self.user_id,
                namespace="skills",
                key="python",
                value="10+ years expert Python architecture",
                verification_status=VerificationStatus.USER_VERIFIED,
                allowed_purposes=["job_application", "case_preparation"],
            )
            uow.context.save(self.fact_py)

            # 4. Preparation Report & Claims
            analyzer = PreparationAnalyzer()
            self.report = analyzer.analyze(
                case=self.case,
                opportunity=self.opportunity,
                granted_facts=[self.fact_name, self.fact_py],
                denied_descriptors=[],
            )
            uow.preparation_reports.save(self.report)

            supp_hash = compute_support_snapshot_hash([self.fact_py])
            self.claim = Claim(
                claim_id="claim-tx-1",
                user_id=self.user_id,
                purpose="job_application",
                text="SRE Expertise in Python",
                status=ClaimStatus.SUPPORTED,
                supporting_fact_ids=[self.fact_py.fact_id],
                case_id=self.case_id,
                source_report_id=self.report.report_id,
                source_claim_intent_id="intent-tx-1",
                assertion_schema="skill_possession",
                assertion_json='{"kind": "skill_possession", "fact_ids": ["fact-tx-py"], "fields": {"skill": "Python"}}',
                support_snapshot_hash=supp_hash,
                version=1,
            )
            uow.claims.save(self.claim)

            # 5. Prepared Artifacts (Resume & Cover Letter)
            cv_grounding = compute_artifact_grounding_hash(claims=[self.claim], direct_facts=[self.fact_name])
            cl_grounding = compute_artifact_grounding_hash(claims=[], direct_facts=[self.fact_name])

            self.art_resume = PreparedArtifact(
                artifact_id="art-tx-resume",
                artifact_set_id=self.set_id,
                case_id=self.case_id,
                opportunity_id=self.opp_id,
                user_id=self.user_id,
                material_type=PreparationMaterialType.CV,
                title="Resume",
                planner_version=PLANNER_VERSION,
                status=ArtifactStatus.DRAFT,
                source_report_id=self.report.report_id,
                grounding_snapshot_hash=cv_grounding,
                blocks=[
                    ArtifactBlock(
                        block_id="b-header",
                        block_type="header",
                        content="Profile",
                        grounding_type=ArtifactGroundingType.CONTEXT_FACT,
                        fact_ids=[self.fact_name.fact_id],
                    ),
                    ArtifactBlock(
                        block_id="b1",
                        content="SRE skills in Python",
                        grounding_type=ArtifactGroundingType.CLAIM,
                        claim_ids=[self.claim.claim_id],
                    ),
                ],
            )
            uow.preparation_artifacts.save(self.art_resume)

            self.art_cl = PreparedArtifact(
                artifact_id="art-tx-cl",
                artifact_set_id=self.set_id,
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
                        block_id="b2",
                        content="Cover letter text",
                        grounding_type=ArtifactGroundingType.CONTEXT_FACT,
                        fact_ids=[self.fact_name.fact_id],
                    )
                ],
            )
            uow.preparation_artifacts.save(self.art_cl)

            qa_grounding = compute_artifact_grounding_hash(claims=[self.claim], direct_facts=[])
            self.art_qa = PreparedArtifact(
                artifact_id="art-tx-qa",
                artifact_set_id=self.set_id,
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
                        block_id="b3",
                        block_type="qna_item",
                        content="### Q: Tell us about your Python experience?\n\nYes, 10 years Python experience.",
                        grounding_type=ArtifactGroundingType.CLAIM,
                        claim_ids=[self.claim.claim_id],
                        metadata={
                            "question": "Tell us about your Python experience?",
                            "answer": "Yes, 10 years Python experience.",
                            "answer_status": "ANSWERED",
                        },
                    )
                ],
            )
            uow.preparation_artifacts.save(self.art_qa)

        # 6. Seal & Approve Preparation Package locally
        package, _, app, _, _ = self.package_service.verify_and_prepare_package(
            user_id=self.user_id,
            case_id=self.case_id,
            artifact_set_id=self.set_id,
            expected_case_version=1,
        )
        self.approval_service.approve_action(
            user_id=self.user_id,
            approval_id=app.approval_id,
            reason="User confirmed local package review",
        )

        with self.storage.unit_of_work() as uow:
            self.package = uow.preparation_packages.get_by_id(package.package_id)
            c = uow.cases.get_by_id(self.case_id)
            self.assertEqual(c.status, CaseStatus.ACTION_REQUIRED)

        # 7. Exact Target Interaction Schema
        self.schema = TargetInteractionSchema(
            schema_id="schema-tx-apex",
            destination_type=ExecutionDestinationType.ATS_FORM.value,
            fields=[TargetSchemaField(target_field_id="full_name", label="Full Name", required=True)],
            upload_slots=[
                TargetSchemaUploadSlot(
                    slot_id="slot-resume",
                    role="RESUME",
                    label="Resume (PDF)",
                    required=True,
                )
            ],
            final_submit_control=TargetSubmitControlSpec(
                control_type="button_submit",
                semantic_target="Submit Application",
                selector="#btn-submit",
                method="POST",
            ),
        )
        self.bindings = [
            TargetFieldBinding(target_field_id="full_name", source_fact_id=self.fact_name.fact_id)
        ]

        # 8. Compile ExternalExecutionPlan
        self.plan = self.plan_service.create_execution_plan(
            user_id=self.user_id,
            case_id=self.case_id,
            target_schema=self.schema,
            field_bindings=self.bindings,
        )
        self.assertEqual(self.plan.status, ExternalExecutionPlanStatus.READY_FOR_AUTHORIZATION)

    def tearDown(self) -> None:
        self.storage.close()

    def test_request_transmission_authorization_success(self) -> None:
        """Test requesting transmission authorization creates SUBMIT_FORM Action, Approval, and transitions Case."""
        action, approval = self.plan_service.request_transmission_authorization(
            user_id=self.user_id,
            case_id=self.case_id,
            plan_id=self.plan.plan_id,
        )

        self.assertIsNotNone(action)
        self.assertIsNotNone(approval)
        self.assertEqual(action.action_type, ActionType.SUBMIT_FORM)
        self.assertEqual(action.status, ActionStatus.AWAITING_APPROVAL)
        self.assertEqual(action.risk_level, RiskLevel.HIGH)
        self.assertTrue(action.requires_approval)

        # Server-side ActionPolicy verification
        decision = ActionPolicy.evaluate(ActionType.SUBMIT_FORM)
        self.assertEqual(action.risk_level, decision.risk_level)
        self.assertEqual(action.requires_approval, decision.requires_approval)

        # Check minimal privacy-safe cryptographic parameters (hashes and counts only)
        params = action.parameters
        self.assertEqual(params["authorization_schema"], "tx_auth_v1")
        self.assertEqual(params["scope"], "external_transmission")
        self.assertEqual(params["plan_id"], self.plan.plan_id)
        self.assertEqual(params["execution_plan_fingerprint"], self.plan.execution_plan_fingerprint)
        self.assertEqual(params["package_id"], self.plan.package_id)
        self.assertEqual(params["package_fingerprint"], self.plan.package_fingerprint)
        self.assertEqual(params["target_schema_hash"], self.plan.target_schema_hash)
        self.assertEqual(params["execution_input_hash"], self.plan.execution_input_hash)
        self.assertEqual(params["destination_type"], self.plan.destination.destination_type)
        self.assertEqual(params["destination_hostname"], "apex.apply.com")
        self.assertEqual(params["field_mapping_count"], 1)
        self.assertEqual(params["upload_asset_count"], 1)
        self.assertEqual(params["screening_response_count"], 0)

        # Ensure NO raw personal values or screening answers in action parameters
        self.assertNotIn("Grant Dagogo-Jack", str(params))
        self.assertNotIn("grant@", str(params))

        # Check Approval state
        self.assertEqual(approval.status, ApprovalStatus.PENDING)
        self.assertEqual(approval.action_id, action.action_id)
        self.assertEqual(approval.action_fingerprint, action.compute_fingerprint())

        # Check Case state transition: ACTION_REQUIRED -> AWAITING_APPROVAL
        with self.storage.unit_of_work() as uow:
            c = uow.cases.get_by_id(self.case_id)
            self.assertEqual(c.status, CaseStatus.AWAITING_APPROVAL)

            # Check emitted events
            events = uow.events.get_events_by_user(self.user_id)
            event_types = [e.event_type for e in events]
            self.assertIn("action.proposed", event_types)
            self.assertIn("action.status_changed", event_types)
            self.assertIn("approval.requested", event_types)
            self.assertIn("case.status_changed", event_types)

            # Ensure event payloads contain no raw personal values
            for ev in events:
                self.assertNotIn("Grant Dagogo-Jack", str(ev.payload))

    def test_idempotency_ordering_retry_when_awaiting_approval(self) -> None:
        """Correction 1: Retrying request authorization when Case is already AWAITING_APPROVAL returns existing pair."""
        action1, approval1 = self.plan_service.request_transmission_authorization(
            user_id=self.user_id,
            case_id=self.case_id,
            plan_id=self.plan.plan_id,
        )

        with self.storage.unit_of_work() as uow:
            c = uow.cases.get_by_id(self.case_id)
            self.assertEqual(c.status, CaseStatus.AWAITING_APPROVAL)

        # Retry call: must NOT fail with InvalidStateTransitionError because Case is AWAITING_APPROVAL
        action2, approval2 = self.plan_service.request_transmission_authorization(
            user_id=self.user_id,
            case_id=self.case_id,
            plan_id=self.plan.plan_id,
        )

        self.assertEqual(action1.action_id, action2.action_id)
        self.assertEqual(approval1.approval_id, approval2.approval_id)

    def test_idempotency_when_already_approved(self) -> None:
        """Calling request_transmission_authorization after approval returns existing approved pair without duplicating."""
        action, approval = self.plan_service.request_transmission_authorization(
            user_id=self.user_id,
            case_id=self.case_id,
            plan_id=self.plan.plan_id,
        )

        self.approval_service.approve_action(
            user_id=self.user_id,
            approval_id=approval.approval_id,
            reason="Approved transmission",
        )

        action_repeat, approval_repeat = self.plan_service.request_transmission_authorization(
            user_id=self.user_id,
            case_id=self.case_id,
            plan_id=self.plan.plan_id,
        )

        self.assertEqual(action.action_id, action_repeat.action_id)
        self.assertEqual(approval.approval_id, approval_repeat.approval_id)
        self.assertEqual(action_repeat.status, ActionStatus.APPROVED)
        self.assertEqual(approval_repeat.status, ApprovalStatus.APPROVED)

    def test_exact_plan_id_required(self) -> None:
        """Correction 2: Non-existent plan_id or ownership mismatch raises EntityNotFoundError."""
        with self.assertRaises(EntityNotFoundError):
            self.plan_service.request_transmission_authorization(
                user_id=self.user_id,
                case_id=self.case_id,
                plan_id="non_existent_plan",
            )

        with self.assertRaises(EntityNotFoundError):
            self.plan_service.request_transmission_authorization(
                user_id="different_user",
                case_id=self.case_id,
                plan_id=self.plan.plan_id,
            )

    def test_draft_plan_authorization_fails_without_superseding(self) -> None:
        """Correction 5: Attempting to authorize a DRAFT plan raises DomainValidationError without mutating plan."""
        # Create a schema missing final submit control -> results in DRAFT plan
        draft_schema = TargetInteractionSchema(
            schema_id="schema-draft",
            destination_type=ExecutionDestinationType.ATS_FORM.value,
            fields=[TargetSchemaField(target_field_id="full_name", label="Full Name", required=True)],
            final_submit_control=None,  # Missing submit control -> stays DRAFT
        )
        draft_plan = self.plan_service.create_execution_plan(
            user_id=self.user_id,
            case_id=self.case_id,
            target_schema=draft_schema,
            field_bindings=self.bindings,
        )
        self.assertEqual(draft_plan.status, ExternalExecutionPlanStatus.DRAFT)

        with self.assertRaises(DomainValidationError) as ctx:
            self.plan_service.request_transmission_authorization(
                user_id=self.user_id,
                case_id=self.case_id,
                plan_id=draft_plan.plan_id,
            )
        self.assertIn("DRAFT status", str(ctx.exception))

        # Assert plan was NOT mutated or superseded
        with self.storage.unit_of_work() as uow:
            refreshed = uow.execution_plans.get_by_id(draft_plan.plan_id)
            self.assertEqual(refreshed.status, ExternalExecutionPlanStatus.DRAFT)
            c = uow.cases.get_by_id(self.case_id)
            self.assertEqual(c.status, CaseStatus.ACTION_REQUIRED)

    def test_approve_transmission_success(self) -> None:
        """Correction 8: Approving transmission marks Approval -> APPROVED, Action -> APPROVED, Case -> ACTION_REQUIRED."""
        action, approval = self.plan_service.request_transmission_authorization(
            user_id=self.user_id,
            case_id=self.case_id,
            plan_id=self.plan.plan_id,
        )

        app_res, act_res = self.approval_service.approve_action(
            user_id=self.user_id,
            approval_id=approval.approval_id,
            reason="I authorize these exact materials and values to be transmitted to this exact destination.",
        )

        self.assertEqual(app_res.status, ApprovalStatus.APPROVED)
        # Invariant: Action is APPROVED, NOT EXECUTING or SUCCEEDED (Phase 5.3.3 executes)
        self.assertEqual(act_res.status, ActionStatus.APPROVED)

        with self.storage.unit_of_work() as uow:
            c = uow.cases.get_by_id(self.case_id)
            self.assertEqual(c.status, CaseStatus.ACTION_REQUIRED)
            p = uow.execution_plans.get_by_id(self.plan.plan_id)
            self.assertEqual(p.status, ExternalExecutionPlanStatus.READY_FOR_AUTHORIZATION)

    def test_reject_transmission_does_not_supersede_plan(self) -> None:
        """Correction 5 & 6: Human rejection marks Approval/Action REJECTED but preserves READY_FOR_AUTHORIZATION plan."""
        action, approval = self.plan_service.request_transmission_authorization(
            user_id=self.user_id,
            case_id=self.case_id,
            plan_id=self.plan.plan_id,
        )

        app_res, act_res = self.approval_service.reject_action(
            user_id=self.user_id,
            approval_id=approval.approval_id,
            reason="Declined by operator after reviewing mapped fields",
        )

        self.assertEqual(app_res.status, ApprovalStatus.REJECTED)
        self.assertEqual(act_res.status, ActionStatus.REJECTED)

        with self.storage.unit_of_work() as uow:
            c = uow.cases.get_by_id(self.case_id)
            self.assertEqual(c.status, CaseStatus.ACTION_REQUIRED)
            # Plan must NOT be superseded on simple human rejection
            p = uow.execution_plans.get_by_id(self.plan.plan_id)
            self.assertEqual(p.status, ExternalExecutionPlanStatus.READY_FOR_AUTHORIZATION)

    def test_approval_time_stale_vault_fact_supersedes_and_cancels(self) -> None:
        """Correction 4 & 6: If a bound Vault fact is modified, approve_action fails closed, superseding plan."""
        action, approval = self.plan_service.request_transmission_authorization(
            user_id=self.user_id,
            case_id=self.case_id,
            plan_id=self.plan.plan_id,
        )

        # Mutate the bound Vault fact in DB behind the scenes
        with self.storage.unit_of_work() as uow:
            fact = uow.context.get_by_id(self.fact_name.fact_id)
            fact.value = "Grant Modified-Name"
            fact.version += 1
            uow.context.save(fact)

        # Attempt to approve: must fail revalidation and unwind
        with self.assertRaises(ApprovalValidationError) as ctx:
            self.approval_service.approve_action(
                user_id=self.user_id,
                approval_id=approval.approval_id,
                reason="Attempting to approve stale plan",
            )
        self.assertIn("Transmission approval failed revalidation", str(ctx.exception))

        with self.storage.unit_of_work() as uow:
            # Plan is SUPERSEDED
            p = uow.execution_plans.get_by_id(self.plan.plan_id)
            self.assertEqual(p.status, ExternalExecutionPlanStatus.SUPERSEDED)

            # Action and Approval are CANCELLED
            act = uow.actions.get_by_id(action.action_id)
            self.assertEqual(act.status, ActionStatus.CANCELLED)
            app = uow.approvals.get_by_id(approval.approval_id)
            self.assertEqual(app.status, ApprovalStatus.CANCELLED)

            # Case unwinds to ACTION_REQUIRED
            c = uow.cases.get_by_id(self.case_id)
            self.assertEqual(c.status, CaseStatus.ACTION_REQUIRED)

    def test_approval_time_tampered_asset_bytes_supersedes_and_cancels(self) -> None:
        """Correction 4 & 6: Tampered upload asset BLOB bytes in DB fail revalidation and supersede plan."""
        action, approval = self.plan_service.request_transmission_authorization(
            user_id=self.user_id,
            case_id=self.case_id,
            plan_id=self.plan.plan_id,
        )

        # Tamper upload asset in DB directly
        with self.storage.unit_of_work() as uow:
            cur = uow.conn.cursor()
            cur.execute(
                "UPDATE execution_upload_assets SET content_bytes = ? WHERE plan_id = ?",
                (b"MALICIOUS_CORRUPTED_PDF_BYTES", self.plan.plan_id),
            )

        with self.assertRaises(ApprovalValidationError) as ctx:
            self.approval_service.approve_action(
                user_id=self.user_id,
                approval_id=approval.approval_id,
            )
        self.assertIn("Transmission approval failed revalidation", str(ctx.exception))

        with self.storage.unit_of_work() as uow:
            p = uow.execution_plans.get_by_id(self.plan.plan_id)
            self.assertEqual(p.status, ExternalExecutionPlanStatus.SUPERSEDED)
            act = uow.actions.get_by_id(action.action_id)
            self.assertEqual(act.status, ActionStatus.CANCELLED)
            c = uow.cases.get_by_id(self.case_id)
            self.assertEqual(c.status, CaseStatus.ACTION_REQUIRED)

    def test_approval_time_action_binding_tampering_cancels_without_superseding_plan(self) -> None:
        """Correction 6: Tampered action parameters cancel authorization but preserve READY_FOR_AUTHORIZATION plan."""
        action, approval = self.plan_service.request_transmission_authorization(
            user_id=self.user_id,
            case_id=self.case_id,
            plan_id=self.plan.plan_id,
        )

        # Tamper action parameter directly in database
        with self.storage.unit_of_work() as uow:
            act = uow.actions.get_by_id(action.action_id)
            act.parameters["plan_id"] = "tampered_plan_id_999"
            act.version += 1
            uow.actions.save(act)

        with self.assertRaises(ApprovalValidationError) as ctx:
            self.approval_service.approve_action(
                user_id=self.user_id,
                approval_id=approval.approval_id,
            )
        self.assertIn("Transmission approval failed revalidation", str(ctx.exception))

        with self.storage.unit_of_work() as uow:
            # Plan remains READY_FOR_AUTHORIZATION because the underlying plan itself is intact!
            p = uow.execution_plans.get_by_id(self.plan.plan_id)
            self.assertEqual(p.status, ExternalExecutionPlanStatus.READY_FOR_AUTHORIZATION)

            # Action and Approval are CANCELLED
            act = uow.actions.get_by_id(action.action_id)
            self.assertEqual(act.status, ActionStatus.CANCELLED)
            app = uow.approvals.get_by_id(approval.approval_id)
            self.assertEqual(app.status, ApprovalStatus.CANCELLED)

            # Case unwinds to ACTION_REQUIRED
            c = uow.cases.get_by_id(self.case_id)
            self.assertEqual(c.status, CaseStatus.ACTION_REQUIRED)

    def test_api_route_request_authorization_enforces_if_match_and_allows_idempotent_retry(self) -> None:
        """Enforces Case If-Match on genuinely new request, and preserves idempotent retries with stale or missing If-Match."""
        config = APIConfig(dev_auth=True)
        app = create_app(config=config, storage=self.storage)
        client = TestClient(app)

        with self.storage.unit_of_work() as uow:
            c = uow.cases.get_by_id(self.case_id)
            case_version = c.version

        # 1. Missing If-Match on new request: 428 Precondition Required
        res_missing = client.post(
            f"/api/v1/cases/{self.case_id}/execution/plan/{self.plan.plan_id}/request-authorization",
            headers={"X-JackVerse-User": self.user_id},
        )
        self.assertEqual(res_missing.status_code, 428)

        # 2. Mismatched If-Match on new request: 412 Precondition Failed
        res_mismatch = client.post(
            f"/api/v1/cases/{self.case_id}/execution/plan/{self.plan.plan_id}/request-authorization",
            headers={
                "X-JackVerse-User": self.user_id,
                "If-Match": f'"case:{self.case_id}:v999"',
            },
        )
        self.assertEqual(res_mismatch.status_code, 412)

        # 3. Matching If-Match on new request: 200 OK
        correct_etag = f'"case:{self.case_id}:v{case_version}"'
        res_ok = client.post(
            f"/api/v1/cases/{self.case_id}/execution/plan/{self.plan.plan_id}/request-authorization",
            headers={
                "X-JackVerse-User": self.user_id,
                "If-Match": correct_etag,
            },
        )
        self.assertEqual(res_ok.status_code, 200)
        data = res_ok.json()
        self.assertIn("action", data)
        self.assertIn("approval", data)
        self.assertEqual(data["action"]["action_type"], "submit_form")
        self.assertEqual(data["action"]["status"], "awaiting_approval")
        self.assertEqual(data["approval"]["status"], "pending")
        self.assertIsNotNone(res_ok.headers.get("etag"))

        # 4. Idempotent retry with original (now stale) If-Match header: 200 OK
        retry_stale = client.post(
            f"/api/v1/cases/{self.case_id}/execution/plan/{self.plan.plan_id}/request-authorization",
            headers={
                "X-JackVerse-User": self.user_id,
                "If-Match": correct_etag,
            },
        )
        self.assertEqual(retry_stale.status_code, 200)
        retry_data = retry_stale.json()
        self.assertEqual(data["action"]["action_id"], retry_data["action"]["action_id"])
        self.assertEqual(data["approval"]["approval_id"], retry_data["approval"]["approval_id"])

        # 5. Idempotent retry with NO If-Match header: 200 OK
        retry_no_etag = client.post(
            f"/api/v1/cases/{self.case_id}/execution/plan/{self.plan.plan_id}/request-authorization",
            headers={"X-JackVerse-User": self.user_id},
        )
        self.assertEqual(retry_no_etag.status_code, 200)
        self.assertEqual(data["action"]["action_id"], retry_no_etag.json()["action"]["action_id"])

    def test_approval_time_action_fingerprint_tampering_unwinds_as_authorization_binding_invalid(self) -> None:
        """Tampering top-level action description changes fingerprint, unwinding as AUTHORIZATION_BINDING_INVALID."""
        action, approval = self.plan_service.request_transmission_authorization(
            user_id=self.user_id,
            case_id=self.case_id,
            plan_id=self.plan.plan_id,
        )

        # Tamper action description in DB directly (alters action.compute_fingerprint())
        with self.storage.unit_of_work() as uow:
            act = uow.actions.get_by_id(action.action_id)
            act.description = "Tampered description altering action fingerprint"
            act.version += 1
            uow.actions.save(act)

        with self.assertRaises(ApprovalValidationError) as ctx:
            self.approval_service.approve_action(
                user_id=self.user_id,
                approval_id=approval.approval_id,
            )
        self.assertIn("Transmission approval failed revalidation", str(ctx.exception))
        self.assertEqual(
            ctx.exception.details.get("failure_kind"),
            "authorization_binding_invalid",
        )

        with self.storage.unit_of_work() as uow:
            # Plan remains READY_FOR_AUTHORIZATION (not superseded)
            p = uow.execution_plans.get_by_id(self.plan.plan_id)
            self.assertEqual(p.status, ExternalExecutionPlanStatus.READY_FOR_AUTHORIZATION)

            # Action and Approval are CANCELLED
            act = uow.actions.get_by_id(action.action_id)
            self.assertEqual(act.status, ActionStatus.CANCELLED)
            app = uow.approvals.get_by_id(approval.approval_id)
            self.assertEqual(app.status, ApprovalStatus.CANCELLED)

            # Case unwinds to ACTION_REQUIRED
            c = uow.cases.get_by_id(self.case_id)
            self.assertEqual(c.status, CaseStatus.ACTION_REQUIRED)

    def test_approval_time_action_parameter_tampering_unwinds_as_authorization_binding_invalid(self) -> None:
        """Tampering non-plan action parameter (e.g. destination_hostname) unwinds as AUTHORIZATION_BINDING_INVALID."""
        action, approval = self.plan_service.request_transmission_authorization(
            user_id=self.user_id,
            case_id=self.case_id,
            plan_id=self.plan.plan_id,
        )

        # Tamper destination_hostname parameter in DB
        with self.storage.unit_of_work() as uow:
            act = uow.actions.get_by_id(action.action_id)
            act.parameters["destination_hostname"] = "evil-destination.example.com"
            act.version += 1
            uow.actions.save(act)

        with self.assertRaises(ApprovalValidationError) as ctx:
            self.approval_service.approve_action(
                user_id=self.user_id,
                approval_id=approval.approval_id,
            )
        self.assertIn("Transmission approval failed revalidation", str(ctx.exception))
        self.assertEqual(
            ctx.exception.details.get("failure_kind"),
            "authorization_binding_invalid",
        )

        with self.storage.unit_of_work() as uow:
            p = uow.execution_plans.get_by_id(self.plan.plan_id)
            self.assertEqual(p.status, ExternalExecutionPlanStatus.READY_FOR_AUTHORIZATION)
            act = uow.actions.get_by_id(action.action_id)
            self.assertEqual(act.status, ActionStatus.CANCELLED)
            app = uow.approvals.get_by_id(approval.approval_id)
            self.assertEqual(app.status, ApprovalStatus.CANCELLED)
            c = uow.cases.get_by_id(self.case_id)
            self.assertEqual(c.status, CaseStatus.ACTION_REQUIRED)


if __name__ == "__main__":
    unittest.main()
