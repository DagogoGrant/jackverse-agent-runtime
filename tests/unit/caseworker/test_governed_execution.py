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
from caseworker.domain.provider_adapter import (
    ProviderSubmissionResult,
    SimulatedExecutionProvider,
    StagingOutcome,
)
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
        self.exec_service = GovernedExecutionService(self.storage, allow_simulation=True)

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
            self.assertEqual(attempt.status, ExecutionAttemptStatus.FAILED)
            self.assertEqual(attempt.error_category, ExecutionErrorCategory.INTEGRITY_VIOLATION)
            receipt = uow.execution_receipts.get_by_action_id(self.action.action_id)
            self.assertIsNone(receipt)

    def test_barrier_b_durability_and_lease_release(self) -> None:
        """Barrier B revalidation failure durably persists FAILED attempt and releases lease in SQLite."""
        self._approve_transmission()

        # Mutate approval right before Barrier B by hooking into provider stage_submission
        class ApprovalTamperingProvider(SimulatedExecutionProvider):
            def __init__(outer_self, storage: SQLiteCaseworkerStorage, approval_id: str):
                super().__init__(simulation_mode="success")
                outer_self.storage = storage
                outer_self.approval_id = approval_id

            def stage_submission(outer_self, plan, package, upload_assets):
                with outer_self.storage.unit_of_work() as uow:
                    app = uow.approvals.get_by_id(outer_self.approval_id)
                    app.status = ApprovalStatus.REJECTED
                    app.version += 1
                    uow.approvals.save(app)
                    uow.commit()
                return super().stage_submission(plan, package, upload_assets)

        tampering_provider = ApprovalTamperingProvider(self.storage, self.approval.approval_id)

        with self.assertRaises(ApprovalValidationError) as ctx:
            self.exec_service.execute_action(
                user_id=self.user_id,
                action_id=self.action.action_id,
                provider=tampering_provider,
            )
        self.assertIn("Barrier B", str(ctx.exception))

        # Check durable SQLite state
        with self.storage.unit_of_work() as uow:
            # 1. Attempt was durably saved as FAILED with INTEGRITY_VIOLATION
            attempt = uow.execution_attempts.get_latest_for_action(self.action.action_id)
            self.assertIsNotNone(attempt)
            self.assertEqual(attempt.status, ExecutionAttemptStatus.FAILED)
            self.assertEqual(attempt.error_category, ExecutionErrorCategory.INTEGRITY_VIOLATION)
            self.assertFalse(attempt.irreversible_action_taken)

            # 2. Lease was durably released (not left hanging or rolled back)
            active_lease = uow.execution_leases.get_active_lease(self.case_id, self.plan.plan_id, self.action.action_id)
            self.assertIsNone(active_lease)

    def test_complete_staging_validation_missing_field_and_submit_control(self) -> None:
        """Staging missing required field keys or missing submit control fails with STAGING_VALIDATION_FAILED."""
        self._approve_transmission()

        # 1. Staging provider returning missing field keys
        class IncompleteFieldProvider(SimulatedExecutionProvider):
            def stage_submission(self, plan, package, upload_assets):
                return StagingOutcome(
                    success=True,
                    staged_field_count=0,
                    staged_upload_count=0,
                    submit_control_ready=True,
                    staged_field_keys=[],  # Missing required fields!
                    staged_upload_slot_ids=[],
                    staged_screening_question_ids=[],
                )

        with self.assertRaises(ApprovalValidationError) as ctx:
            self.exec_service.execute_action(
                user_id=self.user_id,
                action_id=self.action.action_id,
                provider=IncompleteFieldProvider(),
            )
        self.assertIn("Missing required staged field keys", str(ctx.exception))

        # Verify safe state: Action remains APPROVED, lease released, attempt marked FAILED
        with self.storage.unit_of_work() as uow:
            act = uow.actions.get_by_id(self.action.action_id)
            self.assertEqual(act.status, ActionStatus.APPROVED)
            attempt = uow.execution_attempts.get_latest_for_action(self.action.action_id)
            self.assertEqual(attempt.status, ExecutionAttemptStatus.FAILED)
            self.assertEqual(attempt.error_category, ExecutionErrorCategory.STAGING_VALIDATION_FAILED)
            self.assertFalse(attempt.irreversible_action_taken)
            lease = uow.execution_leases.get_active_lease(self.case_id, self.plan.plan_id, self.action.action_id)
            self.assertIsNone(lease)

        # 2. Staging provider returning submit_control_ready = False
        class NotReadyControlProvider(SimulatedExecutionProvider):
            def stage_submission(self, plan, package, upload_assets):
                return StagingOutcome(
                    success=True,
                    staged_field_count=1,
                    staged_upload_count=0,
                    submit_control_ready=False,  # Not ready!
                    staged_field_keys=[m.target_field_id for m in plan.field_mappings],
                    staged_upload_slot_ids=[],
                    staged_screening_question_ids=[],
                )

        with self.assertRaises(ApprovalValidationError) as ctx2:
            self.exec_service.execute_action(
                user_id=self.user_id,
                action_id=self.action.action_id,
                provider=NotReadyControlProvider(),
            )
        self.assertIn("Final submit control is not ready", str(ctx2.exception))

    def test_unexpected_provider_exception_before_irreversible_dispatch(self) -> None:
        """Unexpected exception before irreversible dispatch safely preserves Action and allows retry."""
        self._approve_transmission()

        class CrashingStagingProvider(SimulatedExecutionProvider):
            def stage_submission(self, plan, package, upload_assets):
                raise RuntimeError("Unexpected driver crash during form staging")

        with self.assertRaises(ApprovalValidationError) as ctx:
            self.exec_service.execute_action(
                user_id=self.user_id,
                action_id=self.action.action_id,
                provider=CrashingStagingProvider(),
            )
        self.assertIn("Provider staging failed unexpectedly", str(ctx.exception))

        with self.storage.unit_of_work() as uow:
            act = uow.actions.get_by_id(self.action.action_id)
            self.assertEqual(act.status, ActionStatus.APPROVED)
            case = uow.cases.get_by_id(self.case_id)
            self.assertEqual(case.status, CaseStatus.ACTION_REQUIRED)
            attempt = uow.execution_attempts.get_latest_for_action(self.action.action_id)
            self.assertEqual(attempt.status, ExecutionAttemptStatus.FAILED)
            self.assertFalse(attempt.irreversible_action_taken)
            self.assertIsNone(uow.execution_leases.get_active_lease(self.case_id, self.plan.plan_id, self.action.action_id))

        # Safe retry succeeds
        attempt2, receipt2 = self.exec_service.execute_action(
            user_id=self.user_id,
            action_id=self.action.action_id,
            provider=SimulatedExecutionProvider(simulation_mode="success"),
        )
        self.assertEqual(attempt2.status, ExecutionAttemptStatus.SUCCEEDED)

    def test_unexpected_provider_exception_during_irreversible_dispatch(self) -> None:
        """Unexpected exception during irreversible submit dispatch is conservatively treated as AMBIGUOUS."""
        self._approve_transmission()

        class CrashingSubmitProvider(SimulatedExecutionProvider):
            def execute_irreversible_submit(self, plan, attempt_id):
                raise ConnectionResetError("Connection severed immediately after submitting form payload")

        with self.assertRaises(ApprovalValidationError) as ctx:
            self.exec_service.execute_action(
                user_id=self.user_id,
                action_id=self.action.action_id,
                provider=CrashingSubmitProvider(),
            )
        self.assertIn("AMBIGUOUS submission", str(ctx.exception))

        with self.storage.unit_of_work() as uow:
            act = uow.actions.get_by_id(self.action.action_id)
            self.assertEqual(act.status, ActionStatus.FAILED)
            case = uow.cases.get_by_id(self.case_id)
            self.assertEqual(case.status, CaseStatus.ACTION_REQUIRED)
            attempt = uow.execution_attempts.get_latest_for_action(self.action.action_id)
            self.assertEqual(attempt.status, ExecutionAttemptStatus.AMBIGUOUS)
            self.assertTrue(attempt.irreversible_action_taken)
            receipt = uow.execution_receipts.get_by_action_id(self.action.action_id)
            self.assertIsNotNone(receipt)
            self.assertEqual(receipt.status, ExecutionReceiptStatus.AMBIGUOUS_SUBMISSION)

        # Retry blocked
        with self.assertRaises(ApprovalValidationError):
            self.exec_service.execute_action(
                user_id=self.user_id,
                action_id=self.action.action_id,
            )

    def test_crash_recovery_for_unresolved_submitting_attempt(self) -> None:
        """Step 1 crash recovery reconciles an unresolved SUBMITTING attempt as AMBIGUOUS and blocks retry."""
        self._approve_transmission()

        # Simulate a crash right after crossing irreversible boundary
        with self.storage.unit_of_work() as uow:
            lease = uow.execution_leases.acquire_lease(
                case_id=self.case_id,
                plan_id=self.plan.plan_id,
                action_id=self.action.action_id,
                user_id=self.user_id,
                attempt_id="crashed_submitting_att_001",
                ttl_seconds=0,  # Expired
            )
            crashed_attempt = ExecutionAttempt(
                attempt_id="crashed_submitting_att_001",
                action_id=self.action.action_id,
                approval_id=self.approval.approval_id,
                plan_id=self.plan.plan_id,
                case_id=self.case_id,
                user_id=self.user_id,
                lease_token=lease.lease_token,
                fencing_token=lease.fencing_token,
                status=ExecutionAttemptStatus.SUBMITTING,
                provider_kind=ExecutionProviderKind.SIMULATED_TEST,
                irreversible_action_taken=True,
            )
            uow.execution_attempts.save(crashed_attempt)
            act = uow.actions.get_by_id(self.action.action_id)
            act.status = ActionStatus.EXECUTING
            act.version += 1
            uow.actions.save(act)
            uow.commit()

        # Invoking execute_action must discover the crashed attempt and reconcile as AMBIGUOUS
        with self.assertRaises(ApprovalValidationError) as ctx:
            self.exec_service.execute_action(
                user_id=self.user_id,
                action_id=self.action.action_id,
            )
        self.assertIn("crossed the irreversible boundary", str(ctx.exception))
        self.assertIn("manual operator resolution is required", str(ctx.exception))

        with self.storage.unit_of_work() as uow:
            attempt = uow.execution_attempts.get_by_id("crashed_submitting_att_001")
            self.assertEqual(attempt.status, ExecutionAttemptStatus.AMBIGUOUS)
            receipt = uow.execution_receipts.get_by_action_id(self.action.action_id)
            self.assertIsNotNone(receipt)
            self.assertEqual(receipt.status, ExecutionReceiptStatus.AMBIGUOUS_SUBMISSION)
            act = uow.actions.get_by_id(self.action.action_id)
            self.assertEqual(act.status, ActionStatus.FAILED)
            case = uow.cases.get_by_id(self.case_id)
            self.assertEqual(case.status, CaseStatus.ACTION_REQUIRED)

    def test_crash_recovery_for_abandoned_pre_boundary_attempt(self) -> None:
        """Step 1 crash recovery cleans up an abandoned pre-boundary attempt with LEASE_EXPIRED and allows clean execution."""
        self._approve_transmission()

        # Simulate a crash during STAGING with expired lease
        with self.storage.unit_of_work() as uow:
            lease = uow.execution_leases.acquire_lease(
                case_id=self.case_id,
                plan_id=self.plan.plan_id,
                action_id=self.action.action_id,
                user_id=self.user_id,
                attempt_id="abandoned_staging_att_002",
                ttl_seconds=0,  # Expired
            )
            abandoned_attempt = ExecutionAttempt(
                attempt_id="abandoned_staging_att_002",
                action_id=self.action.action_id,
                approval_id=self.approval.approval_id,
                plan_id=self.plan.plan_id,
                case_id=self.case_id,
                user_id=self.user_id,
                lease_token=lease.lease_token,
                fencing_token=lease.fencing_token,
                status=ExecutionAttemptStatus.STAGING,
                provider_kind=ExecutionProviderKind.SIMULATED_TEST,
                irreversible_action_taken=False,
            )
            uow.execution_attempts.save(abandoned_attempt)
            uow.commit()

        # Next execution recovers the abandoned attempt as LEASE_EXPIRED and proceeds to success
        attempt, receipt = self.exec_service.execute_action(
            user_id=self.user_id,
            action_id=self.action.action_id,
            provider=SimulatedExecutionProvider(simulation_mode="success"),
        )
        self.assertEqual(attempt.status, ExecutionAttemptStatus.SUCCEEDED)
        self.assertEqual(receipt.status, ExecutionReceiptStatus.VERIFIED_SUCCESS)

        # Verify abandoned attempt was reconciled as LEASE_EXPIRED
        with self.storage.unit_of_work() as uow:
            old_att = uow.execution_attempts.get_by_id("abandoned_staging_att_002")
            self.assertEqual(old_att.status, ExecutionAttemptStatus.FAILED)
            self.assertEqual(old_att.error_category, ExecutionErrorCategory.LEASE_EXPIRED)

    def test_sensitive_diagnostic_redaction(self) -> None:
        """Diagnostics with Bearer tokens, secrets, or keys are redacted before persistence."""
        self._approve_transmission()

        class LeakingProvider(SimulatedExecutionProvider):
            def execute_irreversible_submit(self, plan, attempt_id):
                return ProviderSubmissionResult(
                    status=ExecutionReceiptStatus.CONFIRMED_FAILED,
                    external_confirmation_id=None,
                    error_category=ExecutionErrorCategory.SUBMIT_REJECTED,
                    error_message="Authentication failed for Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.secret_part_12345",
                    raw_response_snippet="HTTP 401 Unauthorized: Authorization: Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.secret_part_12345, password=supersecretpass",
                    provider_metadata={
                        "authorization": "Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.secret_part_12345",
                        "password": "supersecretpass",
                        "safe_flag": True,
                    },
                )

        with self.assertRaises(ApprovalValidationError):
            self.exec_service.execute_action(
                user_id=self.user_id,
                action_id=self.action.action_id,
                provider=LeakingProvider(),
            )

        with self.storage.unit_of_work() as uow:
            attempt = uow.execution_attempts.get_latest_for_action(self.action.action_id)
            receipt = uow.execution_receipts.get_by_action_id(self.action.action_id)
            act = uow.actions.get_by_id(self.action.action_id)

            # Error message must NOT contain secret
            self.assertNotIn("secret_part_12345", attempt.error_message)
            self.assertIn("[REDACTED]", attempt.error_message)

            # Snippet must NOT contain password
            self.assertNotIn("supersecretpass", attempt.raw_response_snippet)
            self.assertIn("[REDACTED]", attempt.raw_response_snippet)

            # Metadata must drop/redact sensitive keys
            self.assertNotIn("authorization", receipt.safe_provider_metadata)
            self.assertNotIn("password", receipt.safe_provider_metadata)
            self.assertNotIn("secret_part_12345", str(receipt.safe_provider_metadata))
            self.assertNotIn("supersecretpass", str(receipt.safe_provider_metadata))

            # Action result must be redacted
            self.assertNotIn("secret_part_12345", act.result.get("error_message", ""))

    def test_truthful_simulation_outcome_semantics(self) -> None:
        """Simulated execution outcome explicitly declares simulation mode without genuine external effects."""
        self._approve_transmission()

        provider = SimulatedExecutionProvider(simulation_mode="success")
        attempt, receipt = self.exec_service.execute_action(
            user_id=self.user_id,
            action_id=self.action.action_id,
            provider=provider,
        )

        with self.storage.unit_of_work() as uow:
            case = uow.cases.get_by_id(self.case_id)
            act = uow.actions.get_by_id(self.action.action_id)

            # Verify truthful outcome text
            self.assertIn("Simulated transmission completed (Simulation Mode — No genuine external transmission)", case.outcome)
            # Verify safe_provider_metadata marks simulation
            self.assertTrue(receipt.safe_provider_metadata.get("is_simulated"))
            self.assertEqual(attempt.provider_kind, ExecutionProviderKind.SIMULATED_TEST)

    def test_active_worker_lease_protected_from_interference(self) -> None:
        """Active SUBMITTING worker with unexpired lease is protected; execution_in_progress raised without modifying state."""
        self._approve_transmission()

        with self.storage.unit_of_work() as uow:
            lease = uow.execution_leases.acquire_lease(
                case_id=self.case_id,
                plan_id=self.plan.plan_id,
                action_id=self.action.action_id,
                user_id=self.user_id,
                attempt_id="active_worker_att_001",
                ttl_seconds=300,  # Active, unexpired
            )
            attempt = ExecutionAttempt(
                attempt_id="active_worker_att_001",
                action_id=self.action.action_id,
                approval_id=self.approval.approval_id,
                plan_id=self.plan.plan_id,
                case_id=self.case_id,
                user_id=self.user_id,
                lease_token=lease.lease_token,
                fencing_token=lease.fencing_token,
                status=ExecutionAttemptStatus.SUBMITTING,
                provider_kind=ExecutionProviderKind.SIMULATED_TEST,
                irreversible_action_taken=True,
            )
            uow.execution_attempts.save(attempt)
            uow.commit()

        # Invoking execute_action must NOT reconcile or cancel the active worker
        with self.assertRaises(ApprovalValidationError) as ctx:
            self.exec_service.execute_action(
                user_id=self.user_id,
                action_id=self.action.action_id,
            )
        self.assertEqual(ctx.exception.details.get("failure_kind"), "execution_in_progress")

        # Verify worker attempt and lease were NOT touched or released
        with self.storage.unit_of_work() as uow:
            persisted_att = uow.execution_attempts.get_by_id("active_worker_att_001")
            self.assertEqual(persisted_att.status, ExecutionAttemptStatus.SUBMITTING)
            active_lease = uow.execution_leases.get_active_lease(self.case_id, self.plan.plan_id, self.action.action_id)
            self.assertIsNotNone(active_lease)
            self.assertEqual(active_lease.lease_token, lease.lease_token)
            # No receipt created
            rcpt = uow.execution_receipts.get_by_action_id(self.action.action_id)
            self.assertIsNone(rcpt)

    def test_expired_worker_reconciled_with_audit_events_and_action_recovery(self) -> None:
        """Expired SUBMITTING worker is reconciled as AMBIGUOUS, Action transitioned to FAILED via recovery method, and events emitted."""
        self._approve_transmission()

        with self.storage.unit_of_work() as uow:
            lease = uow.execution_leases.acquire_lease(
                case_id=self.case_id,
                plan_id=self.plan.plan_id,
                action_id=self.action.action_id,
                user_id=self.user_id,
                attempt_id="expired_worker_att_001",
                ttl_seconds=0,  # Expired
            )
            attempt = ExecutionAttempt(
                attempt_id="expired_worker_att_001",
                action_id=self.action.action_id,
                approval_id=self.approval.approval_id,
                plan_id=self.plan.plan_id,
                case_id=self.case_id,
                user_id=self.user_id,
                lease_token=lease.lease_token,
                fencing_token=lease.fencing_token,
                status=ExecutionAttemptStatus.SUBMITTING,
                provider_kind=ExecutionProviderKind.SIMULATED_TEST,
                irreversible_action_taken=True,
            )
            uow.execution_attempts.save(attempt)
            # Normal SUBMITTING attempt corresponds to EXECUTING action
            act = uow.actions.get_by_id(self.action.action_id)
            act.status = ActionStatus.EXECUTING
            act.version += 1
            uow.actions.save(act)
            uow.commit()

        # Invoking execute_action must discover expired attempt and reconcile durably
        with self.assertRaises(ApprovalValidationError) as ctx:
            self.exec_service.execute_action(
                user_id=self.user_id,
                action_id=self.action.action_id,
            )
        self.assertEqual(ctx.exception.details.get("failure_kind"), "manual_resolution_required")

        # Verify durable reconciliation and audit trail
        with self.storage.unit_of_work() as uow:
            reconciled_att = uow.execution_attempts.get_by_id("expired_worker_att_001")
            self.assertEqual(reconciled_att.status, ExecutionAttemptStatus.AMBIGUOUS)

            rcpt = uow.execution_receipts.get_by_action_id(self.action.action_id)
            self.assertIsNotNone(rcpt)
            self.assertEqual(rcpt.status, ExecutionReceiptStatus.AMBIGUOUS_SUBMISSION)
            self.assertTrue(rcpt.safe_provider_metadata.get("reconciled_after_interruption"))

            act = uow.actions.get_by_id(self.action.action_id)
            self.assertEqual(act.status, ActionStatus.FAILED)

            case = uow.cases.get_by_id(self.case_id)
            self.assertEqual(case.status, CaseStatus.ACTION_REQUIRED)

            # Check audit events
            events = uow.events.get_events_for_aggregate("action", self.action.action_id)
            action_status_events = [e for e in events if e.event_type == "action.status_changed"]
            self.assertTrue(any(e.payload.get("new_status") == "failed" for e in action_status_events))

            case_events = uow.events.get_events_for_aggregate("case", self.case_id)
            case_status_events = [e for e in case_events if e.event_type == "case.status_changed"]
            self.assertTrue(any(e.payload.get("new_status") == "action_required" for e in case_status_events))

    def test_late_worker_superseded_after_reconciliation(self) -> None:
        """Worker returning after lease expiration and reconciliation is discarded and cannot overwrite ambiguous outcome."""
        self._approve_transmission()

        class SlowWorkerProvider(SimulatedExecutionProvider):
            def __init__(self, on_submit):
                super().__init__(simulation_mode="success")
                self.on_submit = on_submit

            def execute_irreversible_submit(self, plan, attempt_id):
                # Simulate intermediate crash recovery happening while this slow worker was processing
                self.on_submit()
                return super().execute_irreversible_submit(plan, attempt_id)

        def intermediate_reconciliation():
            # Worker B intervenes while Worker A is submitting: lease expires and Worker B reconciles
            with self.storage.unit_of_work() as uow:
                att = uow.execution_attempts.get_latest_for_action(self.action.action_id)
                att.mark_ambiguous("Worker B reconciled expired lease.")
                uow.execution_attempts.save(att)

                reconciled_rcpt = ExecutionReceipt(
                    attempt_id=att.attempt_id,
                    action_id=self.action.action_id,
                    approval_id=self.approval.approval_id,
                    plan_id=self.plan.plan_id,
                    case_id=self.case_id,
                    user_id=self.user_id,
                    status=ExecutionReceiptStatus.AMBIGUOUS_SUBMISSION,
                    destination_hostname="jobs.example.com",
                    destination_url="https://jobs.example.com/apply",
                    destination_type="ats_form",
                    execution_plan_fingerprint=self.plan.execution_plan_fingerprint,
                    authorized_action_fingerprint=self.approval.action_fingerprint,
                    package_fingerprint=self.plan.package_fingerprint,
                    external_reference_id=None,
                    safe_provider_metadata={"reconciled_after_interruption": True, "provider_request_id": "worker_b"},
                )
                uow.execution_receipts.save(reconciled_rcpt)

                act = uow.actions.get_by_id(self.action.action_id)
                act.reconcile_interrupted_failure("Reconciled by Worker B")
                uow.actions.save(act)

                # Release lease
                lease_rec = uow.execution_leases.get_active_lease(self.case_id, self.plan.plan_id, self.action.action_id)
                if lease_rec:
                    uow.execution_leases.release_lease(lease_rec.lease_id, lease_rec.lease_token)
                uow.commit()

        provider = SlowWorkerProvider(on_submit=intermediate_reconciliation)

        # Worker A tries to finish and persist its late success outcome
        with self.assertRaises(ApprovalValidationError) as ctx:
            self.exec_service.execute_action(
                user_id=self.user_id,
                action_id=self.action.action_id,
                provider=provider,
            )
        self.assertEqual(ctx.exception.details.get("failure_kind"), "late_worker_superseded")

        # Verify that Worker A's success outcome DID NOT overwrite Worker B's ambiguous receipt or attempt
        with self.storage.unit_of_work() as uow:
            att = uow.execution_attempts.get_latest_for_action(self.action.action_id)
            self.assertEqual(att.status, ExecutionAttemptStatus.AMBIGUOUS)

            rcpt = uow.execution_receipts.get_by_action_id(self.action.action_id)
            self.assertEqual(rcpt.status, ExecutionReceiptStatus.AMBIGUOUS_SUBMISSION)
            self.assertEqual(rcpt.safe_provider_metadata.get("provider_request_id"), "worker_b")
            self.assertTrue(rcpt.safe_provider_metadata.get("reconciled_after_interruption"))

            act = uow.actions.get_by_id(self.action.action_id)
            self.assertEqual(act.status, ActionStatus.FAILED)

    def test_simulation_fails_closed_in_production_environment(self) -> None:
        """Simulated execution provider strictly fails closed when allow_simulation is False."""
        self._approve_transmission()

        prod_service = GovernedExecutionService(self.storage, allow_simulation=False)

        with self.assertRaises(ApprovalValidationError) as ctx:
            prod_service.execute_action(
                user_id=self.user_id,
                action_id=self.action.action_id,
                provider=SimulatedExecutionProvider(simulation_mode="success"),
            )
        self.assertEqual(ctx.exception.details.get("failure_kind"), "simulation_not_permitted")

        # Verify domain state was not altered
        with self.storage.unit_of_work() as uow:
            act = uow.actions.get_by_id(self.action.action_id)
            self.assertEqual(act.status, ActionStatus.APPROVED)
            plan = uow.execution_plans.get_by_id(self.plan.plan_id)
            self.assertEqual(plan.status, ExternalExecutionPlanStatus.READY_FOR_AUTHORIZATION)
            rcpt = uow.execution_receipts.get_by_action_id(self.action.action_id)
            self.assertIsNone(rcpt)


class TestGovernedExecutionAPI(unittest.TestCase):
    """API endpoint test suite for Governed Execution routes."""

    def setUp(self) -> None:
        self.storage = SQLiteCaseworkerStorage(db_path=":memory:")
        self.config = APIConfig(dev_auth=True, simulation_enabled=True)
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

    def test_api_active_worker_and_expired_worker_recovery_bypasses_if_match(self) -> None:
        """API protects active worker leases (409 execution_in_progress) and allows recovery of expired worker bypassing If-Match."""
        # 1. Setup an active unexpired worker
        with self.storage.unit_of_work() as uow:
            lease = uow.execution_leases.acquire_lease(
                case_id=self.case.case_id,
                plan_id=self.plan.plan_id,
                action_id=self.action.action_id,
                user_id=self.user_id,
                attempt_id="api_active_att_001",
                ttl_seconds=300,
            )
            attempt = ExecutionAttempt(
                attempt_id="api_active_att_001",
                action_id=self.action.action_id,
                approval_id=self.approval.approval_id,
                plan_id=self.plan.plan_id,
                case_id=self.case.case_id,
                user_id=self.user_id,
                lease_token=lease.lease_token,
                fencing_token=lease.fencing_token,
                status=ExecutionAttemptStatus.SUBMITTING,
                provider_kind=ExecutionProviderKind.SIMULATED_TEST,
                irreversible_action_taken=True,
            )
            uow.execution_attempts.save(attempt)
            uow.commit()

        # Try to execute while active lease exists -> 409 conflict with execution_in_progress
        res = self.client.post(
            f"/api/v1/actions/{self.action.action_id}/execute",
            headers={"X-JackVerse-User": self.user_id},
            json={"simulation_mode": "success"},
        )
        self.assertEqual(res.status_code, 409)
        self.assertEqual(res.json().get("failure_kind"), "execution_in_progress")

        # 2. Transition lease to expired, and action to EXECUTING (simulating crash during submit)
        with self.storage.unit_of_work() as uow:
            uow.execution_leases.release_lease(lease.lease_id, lease.lease_token)
            # Re-acquire with ttl=0 (expired)
            expired_lease = uow.execution_leases.acquire_lease(
                case_id=self.case.case_id,
                plan_id=self.plan.plan_id,
                action_id=self.action.action_id,
                user_id=self.user_id,
                attempt_id="api_expired_att_002",
                ttl_seconds=0,
            )
            expired_attempt = ExecutionAttempt(
                attempt_id="api_expired_att_002",
                action_id=self.action.action_id,
                approval_id=self.approval.approval_id,
                plan_id=self.plan.plan_id,
                case_id=self.case.case_id,
                user_id=self.user_id,
                lease_token=expired_lease.lease_token,
                fencing_token=expired_lease.fencing_token,
                status=ExecutionAttemptStatus.SUBMITTING,
                provider_kind=ExecutionProviderKind.SIMULATED_TEST,
                irreversible_action_taken=True,
            )
            uow.execution_attempts.save(expired_attempt)
            act = uow.actions.get_by_id(self.action.action_id)
            act.status = ActionStatus.EXECUTING
            act.version += 1
            uow.actions.save(act)
            uow.commit()

        # Recovery request: even WITHOUT If-Match (or with stale If-Match), recovery is permitted
        # and triggers reconciliation, returning 409 manual_resolution_required
        recovery_res = self.client.post(
            f"/api/v1/actions/{self.action.action_id}/execute",
            headers={"X-JackVerse-User": self.user_id, "If-Match": '"action:stale:1"'},
            json={"simulation_mode": "success"},
        )
        self.assertEqual(recovery_res.status_code, 409)
        self.assertEqual(recovery_res.json().get("failure_kind"), "manual_resolution_required")

        # Verify durable recovery happened in database
        with self.storage.unit_of_work() as uow:
            reconciled_att = uow.execution_attempts.get_by_id("api_expired_att_002")
            self.assertEqual(reconciled_att.status, ExecutionAttemptStatus.AMBIGUOUS)
            receipt = uow.execution_receipts.get_by_action_id(self.action.action_id)
            self.assertIsNotNone(receipt)
            self.assertEqual(receipt.status, ExecutionReceiptStatus.AMBIGUOUS_SUBMISSION)

    def test_api_simulation_fails_closed_when_disabled(self) -> None:
        """API configured with simulation_enabled=False strictly rejects simulation with 409 simulation_not_permitted."""
        prod_config = APIConfig(dev_auth=True, simulation_enabled=False)
        prod_app = create_app(config=prod_config, storage=self.storage)
        prod_client = TestClient(prod_app)

        act_res = prod_client.get(
            f"/api/v1/actions/{self.action.action_id}",
            headers={"X-JackVerse-User": self.user_id},
        )
        etag = act_res.headers.get("ETag")

        res = prod_client.post(
            f"/api/v1/actions/{self.action.action_id}/execute",
            headers={"X-JackVerse-User": self.user_id, "If-Match": etag},
            json={"simulation_mode": "success"},
        )
        self.assertEqual(res.status_code, 409)
        self.assertEqual(res.json().get("failure_kind"), "simulation_not_permitted")
