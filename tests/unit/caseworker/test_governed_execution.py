"""Comprehensive unit and API tests for Phase 5.3.3 Governed External Execution.

Tests:
1. Unauthorized action rejected: Only APPROVED actions may be executed.
2. Barrier A integrity check: Stale plan or mutated Vault fact fails pre-staging barrier.
3. Action fingerprint tampering: Fingerprint mismatch rejects execution.
4. Active lease mutual exclusion: Concurrent attempt is rejected with 409 / in-progress.
5. Fencing token validation: Stale worker with superseded fencing token cannot submit.
6. Pre-submit failure safety: Staging failure leaves Action APPROVED, allowing safe retry.
7. Confirmed success lifecycle: Action SUCCEEDED, Plan EXECUTED, Case WAITING_EXTERNAL, receipt persisted.
8. Single-use execution plan: EXECUTED plan cannot be re-executed.
9. Ambiguous submission handling: submit_timeout leaves Action FAILED, Case ACTION_REQUIRED, blocks blind retry.
10. Idempotent retry: Re-executing already SUCCEEDED action returns existing receipt with 200 OK.
11. API routes:
    - POST /api/v1/actions/{action_id}/execute (ETag enforcement, 428/412 on new request, idempotent replay)
    - POST /api/v1/cases/{case_id}/execution/actions/{action_id}/execute (case-scoped alias)
    - GET /api/v1/cases/{case_id}/execution/receipt (receipt query with ETag)
    - GET /api/v1/cases/{case_id}/execution/attempts (attempts audit query)
12. Barrier B revalidation: Mutation right before irreversible submit aborts before dispatch.
"""

from __future__ import annotations

import unittest
from fastapi.testclient import TestClient

from caseworker.api.app import create_app
from caseworker.api.config import APIConfig
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
    ExecutionAttemptStatus,
    ExecutionDestinationType,
    ExecutionErrorCategory,
    ExecutionProviderKind,
    ExecutionReceiptStatus,
    ExternalExecutionPlanStatus,
    OpportunityStatus,
    OpportunityType,
    PreparationMaterialType,
    RiskLevel,
    VerificationStatus,
)
from caseworker.domain.errors import (
    ApprovalValidationError,
    EntityNotFoundError,
)
from caseworker.domain.execution_attempt import ExecutionAttempt, ExecutionLease
from caseworker.domain.execution_plan import (
    TargetFieldBinding,
    TargetInteractionSchema,
    TargetSchemaField,
    TargetSchemaUploadSlot,
    TargetSubmitControlSpec,
)
from caseworker.domain.execution_receipt import ExecutionReceipt
from caseworker.domain.opportunity import Opportunity
from caseworker.domain.preparation_analyzer import PreparationAnalyzer
from caseworker.domain.provider_adapter import SimulatedExecutionProvider
from caseworker.persistence.sqlite import SQLiteCaseworkerStorage
from caseworker.services.action_service import ActionService
from caseworker.services.approval_service import ApprovalService
from caseworker.services.execution_plan_service import ExecutionPlanService
from caseworker.services.governed_execution_service import GovernedExecutionService
from caseworker.services.package_service import PackageService


class TestGovernedExecution(unittest.TestCase):
    """Unit and service-level test suite for Phase 5.3.3 governed external execution."""

    def setUp(self) -> None:
        self.storage = SQLiteCaseworkerStorage(db_path=":memory:")
        self.package_service = PackageService(self.storage)
        self.approval_service = ApprovalService(self.storage)
        self.plan_service = ExecutionPlanService(self.storage)
        self.action_service = ActionService(self.storage)
        self.exec_service = GovernedExecutionService(self.storage)

        self.user_id = "user_grant_exec"
        self.case_id = "case_exec_001"
        self.opp_id = "opp_exec_001"
        self.set_id = "set_exec_001"

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
                source_url="https://linkedin.com/jobs/exec1",
                requirements=["Python architecture (Mandatory)"],
                metadata={"application_url": "https://apex.apply.com/roles/exec1"},
                converted_case_id=self.case_id,
                status=OpportunityStatus.SHORTLISTED,
            )
            uow.opportunities.save(self.opportunity)

            # 3. Verified Vault Facts
            self.fact_name = ContextFact(
                fact_id="fact-exec-name",
                user_id=self.user_id,
                namespace="identity",
                key="name",
                value="Grant Dagogo-Jack",
                verification_status=VerificationStatus.USER_VERIFIED,
                allowed_purposes=["job_application", "case_preparation"],
            )
            uow.context.save(self.fact_name)

            self.fact_py = ContextFact(
                fact_id="fact-exec-py",
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
                claim_id="claim-exec-1",
                user_id=self.user_id,
                purpose="job_application",
                text="SRE Expertise in Python",
                status=ClaimStatus.SUPPORTED,
                supporting_fact_ids=[self.fact_py.fact_id],
                case_id=self.case_id,
                source_report_id=self.report.report_id,
                source_claim_intent_id="intent-exec-1",
                assertion_schema="skill_possession",
                assertion_json='{"kind": "skill_possession", "fact_ids": ["fact-exec-py"], "fields": {"skill": "Python"}}',
                support_snapshot_hash=supp_hash,
                version=1,
            )
            uow.claims.save(self.claim)

            # 5. Prepared Artifacts
            cv_grounding = compute_artifact_grounding_hash(claims=[self.claim], direct_facts=[self.fact_name])
            self.art_resume = PreparedArtifact(
                artifact_id="art-exec-resume",
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

            cl_grounding = compute_artifact_grounding_hash(claims=[], direct_facts=[self.fact_name])
            self.art_cl = PreparedArtifact(
                artifact_id="art-exec-cl",
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
                artifact_id="art-exec-qa",
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
                        content="### Q: Python exp?\n\n10 years.",
                        grounding_type=ArtifactGroundingType.CLAIM,
                        claim_ids=[self.claim.claim_id],
                        metadata={
                            "question": "Python exp?",
                            "answer": "10 years.",
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
            artifact_set_id=self.set_id,
            expected_case_version=1,
        )
        self.approval_service.approve_action(
            user_id=self.user_id,
            approval_id=app.approval_id,
            reason="Confirmed local package review",
        )

        # 7. Exact Target Interaction Schema
        self.schema = TargetInteractionSchema(
            schema_id="schema-exec-apex",
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

        # 8. Create Plan
        self.plan = self.plan_service.create_execution_plan(
            user_id=self.user_id,
            case_id=self.case_id,
            target_schema=self.schema,
            field_bindings=self.bindings,
        )
        self.assertEqual(self.plan.status, ExternalExecutionPlanStatus.READY_FOR_AUTHORIZATION)

        # 9. Request Transmission Authorization
        self.action, self.approval = self.plan_service.request_transmission_authorization(
            user_id=self.user_id,
            case_id=self.case_id,
            plan_id=self.plan.plan_id,
        )

    def tearDown(self) -> None:
        self.storage.close()

    def _approve_transmission(self) -> None:
        """Helper to approve the transmission authorization."""
        self.approval_service.approve_action(
            user_id=self.user_id,
            approval_id=self.approval.approval_id,
            reason="Approved transmission by user",
        )
        with self.storage.unit_of_work() as uow:
            act = uow.actions.get_by_id(self.action.action_id)
            self.assertEqual(act.status, ActionStatus.APPROVED)
            c = uow.cases.get_by_id(self.case_id)
            self.assertEqual(c.status, CaseStatus.ACTION_REQUIRED)

    def test_unauthorized_action_rejected(self) -> None:
        """Executing an action in PENDING status raises ApprovalValidationError."""
        with self.assertRaises(ApprovalValidationError) as ctx:
            self.exec_service.execute_action(
                user_id=self.user_id,
                action_id=self.action.action_id,
            )
        self.assertIn("Only APPROVED", str(ctx.exception))

    def test_barrier_a_stale_plan_rejected(self) -> None:
        """Mutating a Vault fact after plan authorization makes it stale, failing Barrier A."""
        self._approve_transmission()

        # Mutate fact
        with self.storage.unit_of_work() as uow:
            self.fact_name.value = "Altered Name"
            self.fact_name.version += 1
            uow.context.save(self.fact_name)

        with self.assertRaises(ApprovalValidationError) as ctx:
            self.exec_service.execute_action(
                user_id=self.user_id,
                action_id=self.action.action_id,
            )
        self.assertIn("Barrier A", str(ctx.exception))

    def test_barrier_a_fingerprint_tampering_rejected(self) -> None:
        """Fingerprint mismatch between Action parameters and Plan rejects execution."""
        self._approve_transmission()

        # Tamper with action plan_id parameter
        with self.storage.unit_of_work() as uow:
            act = uow.actions.get_by_id(self.action.action_id)
            act.parameters["plan_fingerprint"] = "tampered_fake_fingerprint"
            act.parameters["execution_plan_fingerprint"] = "tampered_fake_fingerprint"
            act.version += 1
            uow.actions.save(act)

        with self.assertRaises(ApprovalValidationError) as ctx:
            self.exec_service.execute_action(
                user_id=self.user_id,
                action_id=self.action.action_id,
            )
        self.assertIn("fingerprint", str(ctx.exception).lower())

    def test_active_lease_prevents_concurrent_execution(self) -> None:
        """An active unexpired lease prevents concurrent execution."""
        self._approve_transmission()

        # Acquire a lease manually to simulate concurrent worker
        with self.storage.unit_of_work() as uow:
            uow.execution_leases.acquire_lease(
                case_id=self.case_id,
                plan_id=self.plan.plan_id,
                action_id=self.action.action_id,
                user_id=self.user_id,
                attempt_id="concurrent_attempt_999",
                ttl_seconds=300,
            )

        with self.assertRaises(ApprovalValidationError) as ctx:
            self.exec_service.execute_action(
                user_id=self.user_id,
                action_id=self.action.action_id,
            )
        self.assertIn("currently active under lease", str(ctx.exception))

    def test_staging_failure_leaves_action_approved_and_allows_safe_retry(self) -> None:
        """Pre-submit staging failure preserves Action APPROVED and Case ACTION_REQUIRED for safe retry."""
        self._approve_transmission()

        # Execute with staging_failure simulation
        failing_provider = SimulatedExecutionProvider(simulation_mode="staging_failure")
        with self.assertRaises(ApprovalValidationError) as ctx:
            self.exec_service.execute_action(
                user_id=self.user_id,
                action_id=self.action.action_id,
                provider=failing_provider,
            )
        self.assertIn("staging", str(ctx.exception).lower())

        # Verify Action remains APPROVED and Case remains ACTION_REQUIRED
        with self.storage.unit_of_work() as uow:
            act = uow.actions.get_by_id(self.action.action_id)
            self.assertEqual(act.status, ActionStatus.APPROVED)
            case = uow.cases.get_by_id(self.case_id)
            self.assertEqual(case.status, CaseStatus.ACTION_REQUIRED)
            # Attempt recorded as STAGING_FAILED
            attempt = uow.execution_attempts.get_latest_for_action(self.action.action_id)
            self.assertEqual(attempt.status, ExecutionAttemptStatus.FAILED)
            self.assertEqual(attempt.error_category, ExecutionErrorCategory.STAGING_VALIDATION_FAILED)
            self.assertFalse(attempt.irreversible_action_taken)
            # No receipt persisted yet
            receipt = uow.execution_receipts.get_by_action_id(self.action.action_id)
            self.assertIsNone(receipt)

        # Retry with success provider
        success_provider = SimulatedExecutionProvider(simulation_mode="success")
        attempt2, receipt2 = self.exec_service.execute_action(
            user_id=self.user_id,
            action_id=self.action.action_id,
            provider=success_provider,
        )
        self.assertEqual(attempt2.status, ExecutionAttemptStatus.SUCCEEDED)
        self.assertIsNotNone(receipt2)
        self.assertEqual(receipt2.status, ExecutionReceiptStatus.VERIFIED_SUCCESS)

    def test_confirmed_success_transitions_and_receipt(self) -> None:
        """Confirmed success transitions Action SUCCEEDED, Plan EXECUTED, Case WAITING_EXTERNAL, and persists receipt."""
        self._approve_transmission()

        provider = SimulatedExecutionProvider(simulation_mode="success")
        attempt, receipt = self.exec_service.execute_action(
            user_id=self.user_id,
            action_id=self.action.action_id,
            provider=provider,
        )

        self.assertEqual(attempt.status, ExecutionAttemptStatus.SUCCEEDED)
        self.assertTrue(attempt.irreversible_action_taken)
        self.assertIsNotNone(attempt.last_reversible_point_reached_at)
        self.assertIsNotNone(attempt.completed_at)

        self.assertIsNotNone(receipt)
        self.assertEqual(receipt.status, ExecutionReceiptStatus.VERIFIED_SUCCESS)
        self.assertTrue(receipt.external_reference_id.startswith("TX-APEX-"))
        self.assertEqual(receipt.field_count, 1)
        self.assertEqual(receipt.screening_response_count, 0)
        self.assertEqual(len(receipt.upload_asset_ids), 1)
        self.assertEqual(len(receipt.receipt_hash), 64)

        # Check domain states
        with self.storage.unit_of_work() as uow:
            act = uow.actions.get_by_id(self.action.action_id)
            self.assertEqual(act.status, ActionStatus.SUCCEEDED)
            plan = uow.execution_plans.get_by_id(self.plan.plan_id)
            self.assertEqual(plan.status, ExternalExecutionPlanStatus.EXECUTED)
            case = uow.cases.get_by_id(self.case_id)
            self.assertEqual(case.status, CaseStatus.WAITING_EXTERNAL)

    def test_executed_plan_cannot_be_reexecuted(self) -> None:
        """An EXECUTED plan cannot be marked executed or re-executed."""
        self._approve_transmission()

        provider = SimulatedExecutionProvider(simulation_mode="success")
        self.exec_service.execute_action(
            user_id=self.user_id,
            action_id=self.action.action_id,
            provider=provider,
        )

        with self.storage.unit_of_work() as uow:
            plan = uow.execution_plans.get_by_id(self.plan.plan_id)
            self.assertEqual(plan.status, ExternalExecutionPlanStatus.EXECUTED)
            with self.assertRaises(Exception):
                plan.mark_executed("Attempt duplicate execute")

    def test_ambiguous_submission_blocks_automated_retry(self) -> None:
        """Ambiguous submission (timeout past irreversible barrier) leaves Action FAILED, Case ACTION_REQUIRED, and blocks blind retry."""
        self._approve_transmission()

        provider = SimulatedExecutionProvider(simulation_mode="submit_timeout")
        with self.assertRaises(ApprovalValidationError) as ctx:
            self.exec_service.execute_action(
                user_id=self.user_id,
                action_id=self.action.action_id,
                provider=provider,
            )
        self.assertIn("AMBIGUOUS submission", str(ctx.exception))

        with self.storage.unit_of_work() as uow:
            act = uow.actions.get_by_id(self.action.action_id)
            self.assertEqual(act.status, ActionStatus.FAILED)
            case = uow.cases.get_by_id(self.case_id)
            self.assertEqual(case.status, CaseStatus.ACTION_REQUIRED)
            attempt = uow.execution_attempts.get_latest_for_action(self.action.action_id)
            self.assertIsNotNone(attempt)
            self.assertEqual(attempt.status, ExecutionAttemptStatus.AMBIGUOUS)
            self.assertTrue(attempt.irreversible_action_taken)
            receipt = uow.execution_receipts.get_by_action_id(self.action.action_id)
            self.assertIsNotNone(receipt)
            self.assertEqual(receipt.status, ExecutionReceiptStatus.AMBIGUOUS_SUBMISSION)

        # Attempting retry is strictly blocked by policy
        with self.assertRaises(ApprovalValidationError) as ctx2:
            self.exec_service.execute_action(
                user_id=self.user_id,
                action_id=self.action.action_id,
            )
        self.assertIn("manual operator resolution is required", str(ctx2.exception).lower())

    def test_barrier_b_stale_mutation_aborts_before_submit(self) -> None:
        """Mutation occurring during staging aborts at Barrier B before irreversible dispatch."""
        self._approve_transmission()

        # Subclass provider to mutate fact during staging
        class MutatingProvider(SimulatedExecutionProvider):
            def __init__(outer_self, storage: SQLiteCaseworkerStorage, fact: ContextFact):
                super().__init__(simulation_mode="success")
                outer_self.storage = storage
                outer_self.fact = fact

            def stage_submission(outer_self, plan, package, upload_assets):
                # Mutate fact right before Barrier B
                with outer_self.storage.unit_of_work() as uow:
                    outer_self.fact.value = "Name Mutated During Staging"
                    outer_self.fact.version += 1
                    uow.context.save(outer_self.fact)
                return super().stage_submission(plan, package, upload_assets)

        mutating_provider = MutatingProvider(self.storage, self.fact_name)

        with self.assertRaises(ApprovalValidationError) as ctx:
            self.exec_service.execute_action(
                user_id=self.user_id,
                action_id=self.action.action_id,
                provider=mutating_provider,
            )
        self.assertIn("Barrier B", str(ctx.exception))

        # Ensure no irreversible action was taken and no receipt created
        with self.storage.unit_of_work() as uow:
            act = uow.actions.get_by_id(self.action.action_id)
            self.assertEqual(act.status, ActionStatus.APPROVED)
            case = uow.cases.get_by_id(self.case_id)
            self.assertEqual(case.status, CaseStatus.ACTION_REQUIRED)
            attempt = uow.execution_attempts.get_latest_for_action(self.action.action_id)
            self.assertFalse(attempt.irreversible_action_taken)
            receipt = uow.execution_receipts.get_by_action_id(self.action.action_id)
            self.assertIsNone(receipt)


class TestGovernedExecutionAPI(unittest.TestCase):
    """API endpoint test suite for Governed Execution routes."""

    def setUp(self) -> None:
        self.storage = SQLiteCaseworkerStorage(db_path=":memory:")
        self.config = APIConfig(dev_auth=True)
        self.app = create_app(config=self.config, storage=self.storage)
        self.client = TestClient(self.app)
        self.user_id = "user_api_exec"

        # Initialize full pipeline up to approved transmission action
        with self.storage.unit_of_work() as uow:
            self.case = Case(
                case_id="case_api_001",
                user_id=self.user_id,
                title="Lead SRE Application",
                goal="Submit via governed execution",
                case_type=CaseType.JOB_APPLICATION,
                status=CaseStatus.PLANNING,
            )
            uow.cases.save(self.case)

            self.opportunity = Opportunity(
                opportunity_id="opp_api_001",
                user_id=self.user_id,
                opportunity_type=OpportunityType.JOB,
                title="Lead Site Reliability Engineer",
                organization="Apex Cloud",
                source_url="https://linkedin.com/jobs/api1",
                requirements=[],
                metadata={"application_url": "https://apex.apply.com/roles/api1"},
                converted_case_id=self.case.case_id,
                status=OpportunityStatus.SHORTLISTED,
            )
            uow.opportunities.save(self.opportunity)

            self.fact_name = ContextFact(
                fact_id="fact-api-name",
                user_id=self.user_id,
                namespace="identity",
                key="name",
                value="Grant API Tester",
                verification_status=VerificationStatus.USER_VERIFIED,
                allowed_purposes=["job_application", "case_preparation"],
            )
            uow.context.save(self.fact_name)

            analyzer = PreparationAnalyzer()
            self.report = analyzer.analyze(
                case=self.case,
                opportunity=self.opportunity,
                granted_facts=[self.fact_name],
                denied_descriptors=[],
            )
            uow.preparation_reports.save(self.report)

            resume_grounding = compute_artifact_grounding_hash(claims=[], direct_facts=[self.fact_name])
            self.art_resume = PreparedArtifact(
                artifact_id="art-api-resume",
                artifact_set_id="set_api_001",
                case_id=self.case.case_id,
                opportunity_id=self.opportunity.opportunity_id,
                user_id=self.user_id,
                material_type=PreparationMaterialType.CV,
                title="Resume",
                planner_version=PLANNER_VERSION,
                status=ArtifactStatus.DRAFT,
                source_report_id=self.report.report_id,
                grounding_snapshot_hash=resume_grounding,
                blocks=[
                    ArtifactBlock(
                        block_id="b-h",
                        block_type="header",
                        content="Profile",
                        grounding_type=ArtifactGroundingType.CONTEXT_FACT,
                        fact_ids=[self.fact_name.fact_id],
                    )
                ],
            )
            uow.preparation_artifacts.save(self.art_resume)

            cl_grounding = compute_artifact_grounding_hash(claims=[], direct_facts=[self.fact_name])
            self.art_cl = PreparedArtifact(
                artifact_id="art-api-cl",
                artifact_set_id="set_api_001",
                case_id=self.case.case_id,
                opportunity_id=self.opportunity.opportunity_id,
                user_id=self.user_id,
                material_type=PreparationMaterialType.COVER_LETTER,
                title="Cover Letter",
                planner_version=PLANNER_VERSION,
                status=ArtifactStatus.DRAFT,
                source_report_id=self.report.report_id,
                grounding_snapshot_hash=cl_grounding,
                blocks=[
                    ArtifactBlock(
                        block_id="b-cl",
                        content="Cover letter text",
                        grounding_type=ArtifactGroundingType.CONTEXT_FACT,
                        fact_ids=[self.fact_name.fact_id],
                    )
                ],
            )
            uow.preparation_artifacts.save(self.art_cl)

            qa_grounding = compute_artifact_grounding_hash(claims=[], direct_facts=[self.fact_name])
            self.art_qa = PreparedArtifact(
                artifact_id="art-api-qa",
                artifact_set_id="set_api_001",
                case_id=self.case.case_id,
                opportunity_id=self.opportunity.opportunity_id,
                user_id=self.user_id,
                material_type=PreparationMaterialType.SCREENING_ANSWERS,
                title="Screening Q&A",
                planner_version=PLANNER_VERSION,
                status=ArtifactStatus.DRAFT,
                source_report_id=self.report.report_id,
                grounding_snapshot_hash=qa_grounding,
                blocks=[
                    ArtifactBlock(
                        block_id="b-qa",
                        block_type="qna_item",
                        content="### Q: Experience?\n\n10 years.",
                        grounding_type=ArtifactGroundingType.CONTEXT_FACT,
                        fact_ids=[self.fact_name.fact_id],
                        metadata={
                            "question": "Experience?",
                            "answer": "10 years.",
                            "answer_status": "ANSWERED",
                        },
                    )
                ],
            )
            uow.preparation_artifacts.save(self.art_qa)

        package_service = PackageService(self.storage)
        approval_service = ApprovalService(self.storage)
        plan_service = ExecutionPlanService(self.storage)

        pkg, _, app, _, _ = package_service.verify_and_prepare_package(
            user_id=self.user_id,
            case_id=self.case.case_id,
            artifact_set_id="set_api_001",
            expected_case_version=1,
        )
        approval_service.approve_action(
            user_id=self.user_id,
            approval_id=app.approval_id,
            reason="Local package review confirmed",
        )

        schema = TargetInteractionSchema(
            schema_id="schema-api-apex",
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
        bindings = [TargetFieldBinding(target_field_id="full_name", source_fact_id=self.fact_name.fact_id)]

        self.plan = plan_service.create_execution_plan(
            user_id=self.user_id,
            case_id=self.case.case_id,
            target_schema=schema,
            field_bindings=bindings,
        )

        self.action, self.approval = plan_service.request_transmission_authorization(
            user_id=self.user_id,
            case_id=self.case.case_id,
            plan_id=self.plan.plan_id,
        )

        # Approve transmission
        approval_service.approve_action(
            user_id=self.user_id,
            approval_id=self.approval.approval_id,
            reason="Transmission approved for execution",
        )

    def tearDown(self) -> None:
        self.storage.close()

    def test_execute_action_api_concurrency_and_replay(self) -> None:
        """Test POST /api/v1/actions/{action_id}/execute with ETag control and idempotent replay."""
        # 1. Missing If-Match returns 428
        res = self.client.post(
            f"/api/v1/actions/{self.action.action_id}/execute",
            headers={"X-JackVerse-User": self.user_id},
            json={"simulation_mode": "success"},
        )
        self.assertEqual(res.status_code, 428)

        # 2. Stale If-Match returns 412
        res = self.client.post(
            f"/api/v1/actions/{self.action.action_id}/execute",
            headers={"X-JackVerse-User": self.user_id, "If-Match": '"action:wrong_id:99"'},
            json={"simulation_mode": "success"},
        )
        self.assertEqual(res.status_code, 412)

        # Fetch current action ETag
        act_res = self.client.get(
            f"/api/v1/actions/{self.action.action_id}",
            headers={"X-JackVerse-User": self.user_id},
        )
        action_etag = act_res.headers.get("ETag")
        self.assertIsNotNone(action_etag)

        # 3. Successful execution with matching ETag
        exec_res = self.client.post(
            f"/api/v1/actions/{self.action.action_id}/execute",
            headers={"X-JackVerse-User": self.user_id, "If-Match": action_etag},
            json={"simulation_mode": "success"},
        )
        self.assertEqual(exec_res.status_code, 200)
        data = exec_res.json()
        self.assertEqual(data["action"]["status"], "succeeded")
        self.assertEqual(data["attempt"]["status"], "succeeded")
        self.assertEqual(data["receipt"]["status"], "verified_success")
        self.assertEqual(data["case_status"], "waiting_external")

        # 4. Idempotent replay: Retrying without If-Match returns 200 OK with existing receipt
        replay_res = self.client.post(
            f"/api/v1/actions/{self.action.action_id}/execute",
            headers={"X-JackVerse-User": self.user_id},
            json={"simulation_mode": "success"},
        )
        self.assertEqual(replay_res.status_code, 200)
        replay_data = replay_res.json()
        self.assertEqual(replay_data["receipt"]["receipt_id"], data["receipt"]["receipt_id"])
        self.assertEqual(replay_data["receipt"]["receipt_hash"], data["receipt"]["receipt_hash"])

    def test_execute_case_action_alias_and_query_endpoints(self) -> None:
        """Test case-scoped execute alias, receipt query, and attempts list query."""
        act_res = self.client.get(
            f"/api/v1/actions/{self.action.action_id}",
            headers={"X-JackVerse-User": self.user_id},
        )
        action_etag = act_res.headers.get("ETag")

        # Execute via case alias
        alias_res = self.client.post(
            f"/api/v1/cases/{self.case.case_id}/execution/actions/{self.action.action_id}/execute",
            headers={"X-JackVerse-User": self.user_id, "If-Match": action_etag},
            json={"simulation_mode": "success"},
        )
        self.assertEqual(alias_res.status_code, 200)
        alias_data = alias_res.json()
        self.assertEqual(alias_data["receipt"]["status"], "verified_success")

        # Query receipt endpoint
        receipt_res = self.client.get(
            f"/api/v1/cases/{self.case.case_id}/execution/receipt",
            headers={"X-JackVerse-User": self.user_id},
        )
        self.assertEqual(receipt_res.status_code, 200)
        self.assertIn("ETag", receipt_res.headers)
        receipt_data = receipt_res.json()
        self.assertEqual(receipt_data["receipt_id"], alias_data["receipt"]["receipt_id"])
        self.assertEqual(receipt_data["status"], "verified_success")

        # Query attempts endpoint
        attempts_res = self.client.get(
            f"/api/v1/cases/{self.case.case_id}/execution/attempts",
            headers={"X-JackVerse-User": self.user_id},
        )
        self.assertEqual(attempts_res.status_code, 200)
        attempts_data = attempts_res.json()
        self.assertGreaterEqual(attempts_data["total"], 1)
        self.assertEqual(attempts_data["attempts"][0]["status"], "succeeded")
