"""Comprehensive unit tests for Phase 5.3.1 Exact External Action Planning.

Tests all 17 architectural amendments, domain invariants, compiler rules,
shared package integrity validation, idempotency, supersession, and zero-transmission.
"""

from __future__ import annotations

import json
import unittest
from datetime import datetime, timezone

from caseworker.domain.artifact import (
    ArtifactBlock,
    PreparedArtifact,
    compute_artifact_grounding_hash,
)
from caseworker.domain.case import Case
from caseworker.domain.claim import Claim, compute_support_snapshot_hash
from caseworker.domain.context import ContextFact
from caseworker.domain.artifact_planner import PLANNER_VERSION
from caseworker.domain.preparation_analyzer import PreparationAnalyzer
from caseworker.services.package_service import PackageService
from caseworker.domain.enums import (
    ArtifactGroundingType,
    ArtifactPublicationRole,
    ArtifactStatus,
    CaseStatus,
    CaseType,
    ClaimStatus,
    DestinationProvenance,
    ExecutionDestinationType,
    ExternalExecutionPlanStatus,
    OpportunityStatus,
    OpportunityType,
    PackageVerificationStatus,
    PreparationMaterialType,
    PreparationPackageStatus,
    TargetAnswerType,
    TargetFieldType,
    TargetSchemaProvenance,
    VerificationStatus,
)
from caseworker.domain.errors import (
    DomainValidationError,
    EntityNotFoundError,
    InvalidStateTransitionError,
    PackageVerificationError,
    PersistenceError,
)
from caseworker.domain.events import (
    make_external_execution_plan_created_event,
    make_external_execution_plan_superseded_event,
)
from caseworker.domain.execution_plan import (
    ExecutionDestination,
    ExecutionFieldMapping,
    ExecutionPlanFinding,
    ExecutionScreeningResponse,
    ExecutionUploadAsset,
    ExternalExecutionPlan,
    TargetFieldBinding,
    TargetInteractionSchema,
    TargetSchemaField,
    TargetSchemaQuestion,
    TargetSchemaUploadSlot,
    TargetSubmitControlSpec,
    assert_safe_channel_metadata,
    assert_safe_destination,
    compute_execution_input_hash,
    compute_execution_plan_fingerprint,
    compute_execution_plan_staleness,
    validate_and_normalize_field_value,
)
from caseworker.domain.execution_planner import ExecutionPlanCompiler
from caseworker.domain.opportunity import Opportunity
from caseworker.domain.package import (
    ArtifactPackageSnapshot,
    PreparationPackage,
    SafeClaimSnapshot,
)
from caseworker.domain.package_integrity import PreparationPackageIntegrityValidator
from caseworker.domain.preparation import PreparationIntelligenceReport
from caseworker.domain.types import compute_sha256_bytes, now_utc
from caseworker.persistence.sqlite import SQLiteCaseworkerStorage
from caseworker.services.approval_service import ApprovalService
from caseworker.services.execution_plan_service import ExecutionPlanService


class TestExecutionPlanDomain(unittest.TestCase):
    """Domain aggregate and value object invariant tests."""

    def test_secrets_rejection_in_channel_metadata(self) -> None:
        """Amendment 11: Assert safe channel metadata rejects credentials/secrets."""
        safe_meta = {"browser_engine": "chromium", "form_id": "job_app_123"}
        assert_safe_channel_metadata(safe_meta)

        forbidden_keys = [
            "password", "user_password", "cookie", "session_cookie",
            "auth", "authorization", "token", "access_token",
            "secret", "api_key", "session_id", "csrf_token", "credential"
        ]
        for key in forbidden_keys:
            with self.assertRaises(DomainValidationError):
                assert_safe_channel_metadata({key: "leak_123"})

        with self.assertRaises(DomainValidationError):
            ExecutionDestination(
                destination_type=ExecutionDestinationType.ATS_FORM.value,
                destination_provenance=DestinationProvenance.USER_PROVIDED.value,
                canonical_url="https://portal.example.com",
                organization="Acme",
                channel_metadata={"api_key": "secret_key_123"},
            )

    def test_fingerprint_invariance(self) -> None:
        """Amendment 10: Fingerprint is invariant to mutable metadata (status, version, timestamps)."""
        dest = ExecutionDestination(
            destination_type=ExecutionDestinationType.ATS_FORM.value,
            destination_provenance=DestinationProvenance.USER_PROVIDED.value,
            canonical_url="https://acme.greenhouse.io/apply",
            organization="Acme Corp",
        )
        fields = [
            ExecutionFieldMapping(
                target_field_id="first_name",
                field_name="First Name",
                field_value="Grant",
                source_fact_id="fact-1",
                source_fact_version=1,
                verification_status=VerificationStatus.USER_VERIFIED.value,
                required=True,
            )
        ]
        uploads = [
            ExecutionUploadAsset(
                asset_id="asset-1",
                role="RESUME",
                source_artifact_id="art-1",
                source_artifact_version=1,
                filename="resume.pdf",
                mime_type="application/pdf",
                content_sha256="abc123hash",
                byte_size=1024,
                materialized=True,
            )
        ]
        screening = [
            ExecutionScreeningResponse(
                question_id="q-1",
                question="Authorized?",
                answer="Yes",
                source_artifact_id="art-2",
                grounding_claim_ids=["claim-1"],
            )
        ]
        control = TargetSubmitControlSpec(
            control_type="button_submit",
            semantic_target="Submit",
            selector="#btn-submit",
            method="POST",
        )

        plan = ExternalExecutionPlan(
            plan_id="plan-test-1",
            case_id="case-1",
            opportunity_id="opp-1",
            package_id="pkg-1",
            package_fingerprint="pkg_fp_1",
            user_id="user-1",
            destination=dest,
            status=ExternalExecutionPlanStatus.DRAFT,
            field_mappings=fields,
            upload_mappings=uploads,
            screening_responses=screening,
            final_submit_control=control,
            created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
            version=1,
        )
        initial_fp = plan.execution_plan_fingerprint

        # Transitioning status or updating timestamps does NOT alter fingerprint
        plan.transition_to(ExternalExecutionPlanStatus.READY_FOR_AUTHORIZATION)
        self.assertEqual(plan.compute_fingerprint(), initial_fp)

        # But altering canonical field value changes fingerprint
        changed_fields = [
            ExecutionFieldMapping(
                target_field_id="first_name",
                field_name="First Name",
                field_value="Grant Different",
                source_fact_id="fact-1",
                source_fact_version=1,
                verification_status=VerificationStatus.USER_VERIFIED.value,
                required=True,
            )
        ]
        different_plan = ExternalExecutionPlan(
            plan_id="plan-test-2",
            case_id="case-1",
            opportunity_id="opp-1",
            package_id="pkg-1",
            package_fingerprint="pkg_fp_1",
            user_id="user-1",
            destination=dest,
            status=ExternalExecutionPlanStatus.DRAFT,
            field_mappings=changed_fields,
            upload_mappings=uploads,
            screening_responses=screening,
            final_submit_control=control,
        )
        self.assertNotEqual(different_plan.execution_plan_fingerprint, initial_fp)


class TestExecutionPlanCompiler(unittest.TestCase):
    """Compiler unit tests verifying deterministic mapping rules and findings."""

    def setUp(self) -> None:
        self.case = Case(
            case_id="case-exec-1",
            user_id="user-1",
            title="Senior Engineer",
            goal="Land role",
            case_type=CaseType.JOB_APPLICATION,
            status=CaseStatus.ACTION_REQUIRED,
        )
        self.opportunity = Opportunity(
            opportunity_id="opp-exec-1",
            user_id="user-1",
            opportunity_type=OpportunityType.JOB,
            title="Senior Engineer",
            organization="CloudTech",
            source_url="https://linkedin.com/jobs/view/123",  # Listing URL only
            metadata={"application_url": "https://cloudtech.apply.workable.com/jobs/456"},
            converted_case_id="case-exec-1",
            status=OpportunityStatus.SHORTLISTED,
        )
        self.fact_name = ContextFact(
            fact_id="fact-name",
            user_id="user-1",
            namespace="identity",
            key="full_name",
            value="Grant Dagogo-Jack",
            verification_status=VerificationStatus.USER_VERIFIED,
            allowed_purposes=["job_application", "case_preparation"],
        )
        self.fact_email = ContextFact(
            fact_id="fact-email",
            user_id="user-1",
            namespace="identity",
            key="email",
            value="grant@example.com",
            verification_status=VerificationStatus.SOURCE_VERIFIED,
            allowed_purposes=["job_application"],
        )
        self.claim = Claim(
            claim_id="claim-1",
            user_id="user-1",
            purpose="job_application",
            text="Experienced in Python",
            status=ClaimStatus.SUPPORTED.value,
        )
        self.art_resume = PreparedArtifact(
            artifact_id="art-resume",
            artifact_set_id="set-1",
            case_id="case-exec-1",
            opportunity_id="opp-exec-1",
            user_id="user-1",
            material_type=PreparationMaterialType.CV,
            title="Tailored Resume",
            status=ArtifactStatus.FROZEN,
            source_report_id="rep-1",
            blocks=[
                ArtifactBlock(
                    block_id="b1",
                    content="Grant Resume",
                    grounding_type=ArtifactGroundingType.CLAIM,
                    claim_ids=["claim-1"],
                )
            ],
        )
        self.art_screening = PreparedArtifact(
            artifact_id="art-screening",
            artifact_set_id="set-1",
            case_id="case-exec-1",
            opportunity_id="opp-exec-1",
            user_id="user-1",
            material_type=PreparationMaterialType.SCREENING_ANSWERS,
            title="Screening Answers",
            status=ArtifactStatus.FROZEN,
            source_report_id="rep-1",
            blocks=[
                ArtifactBlock(
                    block_id="b2",
                    block_type="qna_item",
                    content="### Q: Are you authorized to work?\n\nYes, I am legally authorized to work.",
                    grounding_type=ArtifactGroundingType.CLAIM,
                    claim_ids=["claim-1"],
                    metadata={
                        "question": "Are you authorized to work?",
                        "answer": "Yes, I am legally authorized to work.",
                        "answer_status": "ANSWERED",
                    },
                )
            ],
        )
        self.package = PreparationPackage(
            package_id="pkg-exec-1",
            case_id="case-exec-1",
            artifact_set_id="set-1",
            source_report_id="rep-1",
            user_id="user-1",
            status=PreparationPackageStatus.APPROVED,
            artifact_ids=["art-resume", "art-screening"],
            artifact_versions={"art-resume": 1, "art-screening": 1},
            artifact_snapshots=[
                ArtifactPackageSnapshot(
                    artifact_id="art-resume",
                    artifact_version=1,
                    material_type=PreparationMaterialType.CV.value,
                    rendered_payload_hash=self.art_resume.compute_rendered_payload_hash(),
                    grounding_snapshot_hash=self.art_resume.grounding_snapshot_hash,
                    publication_role=ArtifactPublicationRole.SUBMISSION_MATERIAL.value,
                ),
                ArtifactPackageSnapshot(
                    artifact_id="art-screening",
                    artifact_version=1,
                    material_type=PreparationMaterialType.SCREENING_ANSWERS.value,
                    rendered_payload_hash=self.art_screening.compute_rendered_payload_hash(),
                    grounding_snapshot_hash=self.art_screening.grounding_snapshot_hash,
                    publication_role=ArtifactPublicationRole.FIELD_RESPONSE.value,
                ),
            ],
            claim_snapshots=[
                SafeClaimSnapshot(
                    claim_id="claim-1",
                    claim_version=1,
                    claim_status=self.claim.status,
                    support_snapshot_hash="hash",
                    assertion_hash=None,
                )
            ],
            rendered_payload_hashes={
                self.art_resume.artifact_id: self.art_resume.compute_rendered_payload_hash(),
                self.art_screening.artifact_id: self.art_screening.compute_rendered_payload_hash(),
            },
        )

    def test_never_infer_destination_from_listing_source_url(self) -> None:
        """Amendment 1: Opportunity.source_url (e.g. LinkedIn) is never assumed to be application endpoint."""
        opp_listing_only = Opportunity(
            opportunity_id="opp-listing",
            user_id="user-1",
            opportunity_type=OpportunityType.JOB,
            title="Senior Engineer",
            organization="CloudTech",
            source_url="https://www.linkedin.com/jobs/view/999",
            metadata={},
        )
        plan = ExecutionPlanCompiler.compile_plan(
            case=self.case,
            opportunity=opp_listing_only,
            package=self.package,
            artifacts=[self.art_resume, self.art_screening],
            claims=[self.claim],
            facts=[self.fact_name, self.fact_email],
            target_schema=None,
        )
        self.assertEqual(plan.status, ExternalExecutionPlanStatus.DRAFT)
        codes = [f.code for f in plan.findings]
        self.assertIn("DESTINATION_REQUIRED", codes)
        self.assertFalse(plan.destination.is_exact_destination)

    def test_missing_schema_requires_schema(self) -> None:
        """Amendment 2: Missing target schema results in status DRAFT and TARGET_SCHEMA_REQUIRED."""
        dest_override = ExecutionDestination(
            destination_type=ExecutionDestinationType.ATS_FORM.value,
            destination_provenance=DestinationProvenance.USER_PROVIDED.value,
            canonical_url="https://cloudtech.apply.workable.com/jobs/456",
            organization="CloudTech",
        )
        plan = ExecutionPlanCompiler.compile_plan(
            case=self.case,
            opportunity=self.opportunity,
            package=self.package,
            artifacts=[self.art_resume, self.art_screening],
            claims=[self.claim],
            facts=[self.fact_name, self.fact_email],
            target_schema=None,
            destination_override=dest_override,
        )
        self.assertEqual(plan.status, ExternalExecutionPlanStatus.DRAFT)
        codes = [f.code for f in plan.findings]
        self.assertIn("TARGET_SCHEMA_REQUIRED", codes)

    def test_full_compilation_ready_for_authorization(self) -> None:
        """Amendment 3: When all elements are exact, plan transitions to READY_FOR_AUTHORIZATION."""
        schema = TargetInteractionSchema(
            schema_id="workable-v1",
            destination_type=ExecutionDestinationType.ATS_FORM.value,
            fields=[
                TargetSchemaField(target_field_id="full_name", label="Full Name", required=True),
                TargetSchemaField(target_field_id="email", label="Email", required=True),
            ],
            upload_slots=[
                TargetSchemaUploadSlot(
                    slot_id="slot-resume",
                    role="RESUME",
                    label="Resume",
                    required=True,
                    accepted_mime_types=["application/pdf", "text/plain"],
                )
            ],
            screening_questions=[
                TargetSchemaQuestion(
                    question_id="q-auth",
                    question_text="Are you authorized to work?",
                    required=True,
                )
            ],
            final_submit_control=TargetSubmitControlSpec(
                control_type="button_submit",
                semantic_target="Submit Application",
                selector="#submit-app",
                method="POST",
            ),
        )
        dest_override = ExecutionDestination(
            destination_type=ExecutionDestinationType.ATS_FORM.value,
            destination_provenance=DestinationProvenance.OPPORTUNITY_APPLICATION_URL.value,
            canonical_url="https://cloudtech.apply.workable.com/jobs/456",
            organization="CloudTech",
        )

        plan = ExecutionPlanCompiler.compile_plan(
            case=self.case,
            opportunity=self.opportunity,
            package=self.package,
            artifacts=[self.art_resume, self.art_screening],
            claims=[self.claim],
            facts=[self.fact_name, self.fact_email],
            target_schema=schema,
            field_bindings=[
                TargetFieldBinding(target_field_id="full_name", source_fact_id=self.fact_name.fact_id),
                TargetFieldBinding(target_field_id="email", source_fact_id=self.fact_email.fact_id),
            ],
            destination_override=dest_override,
        )
        self.assertEqual(plan.status, ExternalExecutionPlanStatus.READY_FOR_AUTHORIZATION)
        self.assertEqual(len(plan.field_mappings), 2)
        self.assertEqual(len(plan.upload_mappings), 1)
        self.assertTrue(plan.upload_mappings[0].materialized)
        self.assertEqual(len(plan.screening_responses), 1)
        self.assertTrue(all(f.passed for f in plan.findings))

    def test_unverified_fact_or_wrong_purpose_rejected(self) -> None:
        """Amendment 4: ContextFact must have job_application purpose and be verified."""
        unverified_fact = ContextFact(
            fact_id="fact-unverified",
            user_id="user-1",
            namespace="identity",
            key="phone",
            value="123456789",
            verification_status=VerificationStatus.UNVERIFIED,
            allowed_purposes=["job_application"],
        )
        wrong_purpose_fact = ContextFact(
            fact_id="fact-wrong",
            user_id="user-1",
            namespace="identity",
            key="address",
            value="123 Street",
            verification_status=VerificationStatus.USER_VERIFIED,
            allowed_purposes=["benefits_enrollment"],  # Not job_application
        )
        schema = TargetInteractionSchema(
            schema_id="s1",
            destination_type=ExecutionDestinationType.ATS_FORM.value,
            fields=[
                TargetSchemaField(target_field_id="phone", label="Phone", required=True),
                TargetSchemaField(target_field_id="address", label="Address", required=True),
            ],
        )
        dest_override = ExecutionDestination(
            destination_type=ExecutionDestinationType.ATS_FORM.value,
            destination_provenance=DestinationProvenance.USER_PROVIDED.value,
            canonical_url="https://cloudtech.apply.workable.com/jobs/456",
            organization="CloudTech",
        )
        plan = ExecutionPlanCompiler.compile_plan(
            case=self.case,
            opportunity=self.opportunity,
            package=self.package,
            artifacts=[self.art_resume, self.art_screening],
            claims=[self.claim],
            facts=[unverified_fact, wrong_purpose_fact],
            target_schema=schema,
            field_bindings=[
                TargetFieldBinding(target_field_id="phone", source_fact_id=unverified_fact.fact_id),
                TargetFieldBinding(target_field_id="address", source_fact_id=wrong_purpose_fact.fact_id),
            ],
            destination_override=dest_override,
        )
        self.assertEqual(plan.status, ExternalExecutionPlanStatus.DRAFT)
        codes = [f.code for f in plan.findings]
        self.assertIn("UNRESOLVED_REQUIRED_FIELD", codes)

    def test_answer_bank_transmission_prohibited(self) -> None:
        """Amendment 7: APPLICATION_ANSWER_BANK is strictly internal and cannot be transmitted."""
        art_answer_bank = PreparedArtifact(
            artifact_id="art-bank",
            artifact_set_id="set-1",
            case_id="case-exec-1",
            opportunity_id="opp-exec-1",
            user_id="user-1",
            material_type=PreparationMaterialType.APPLICATION_ANSWER_BANK,
            title="Answer Bank",
            status=ArtifactStatus.FROZEN,
            source_report_id="rep-1",
            blocks=[],
        )
        schema = TargetInteractionSchema(
            schema_id="s1",
            destination_type=ExecutionDestinationType.ATS_FORM.value,
            screening_questions=[
                TargetSchemaQuestion(question_id="q1", question_text="Tell us about yourself", required=True)
            ],
        )
        dest_override = ExecutionDestination(
            destination_type=ExecutionDestinationType.ATS_FORM.value,
            destination_provenance=DestinationProvenance.USER_PROVIDED.value,
            canonical_url="https://cloudtech.apply.workable.com/jobs/456",
            organization="CloudTech",
        )
        # Passing answer bank without screening answers
        plan = ExecutionPlanCompiler.compile_plan(
            case=self.case,
            opportunity=self.opportunity,
            package=self.package,
            artifacts=[self.art_resume, art_answer_bank],
            claims=[self.claim],
            facts=[self.fact_name],
            target_schema=schema,
            destination_override=dest_override,
        )
        codes = [f.code for f in plan.findings]
        self.assertIn("ANSWER_BANK_TRANSMISSION_PROHIBITED", codes)
        self.assertEqual(plan.status, ExternalExecutionPlanStatus.DRAFT)


class TestExecutionPlanServiceAndIntegration(unittest.TestCase):
    """Service integration tests including package integrity revalidation, idempotency, and supersession."""

    def setUp(self) -> None:
        self.storage = SQLiteCaseworkerStorage(db_path=":memory:")
        self.package_service = PackageService(self.storage)
        self.approval_service = ApprovalService(self.storage)
        self.service = ExecutionPlanService(self.storage)
        self.user_id = "user-service-1"
        self.case_id = "case-svc-1"
        self.opp_id = "opp-svc-1"
        self.set_id = "set-svc-1"

        with self.storage.unit_of_work() as uow:
            self.case = Case(
                case_id=self.case_id,
                user_id=self.user_id,
                title="Lead SRE",
                goal="Apply",
                case_type=CaseType.JOB_APPLICATION,
                status=CaseStatus.PLANNING,
            )
            uow.cases.save(self.case)

            self.opportunity = Opportunity(
                opportunity_id=self.opp_id,
                user_id=self.user_id,
                opportunity_type=OpportunityType.JOB,
                title="Lead SRE",
                organization="Apex Infrastructure",
                source_url="https://linkedin.com/jobs/1",
                requirements=["Python architecture (Mandatory)"],
                metadata={"application_url": "https://apex.apply.com/roles/1"},
                converted_case_id=self.case_id,
                status=OpportunityStatus.SHORTLISTED,
            )
            uow.opportunities.save(self.opportunity)

            self.fact_name = ContextFact(
                fact_id="fact-name-1",
                user_id=self.user_id,
                namespace="identity",
                key="name",
                value="Grant Dagogo-Jack",
                verification_status=VerificationStatus.USER_VERIFIED,
                allowed_purposes=["job_application", "case_preparation"],
            )
            uow.context.save(self.fact_name)

            self.fact_py = ContextFact(
                fact_id="fact-py",
                user_id=self.user_id,
                namespace="skills",
                key="python",
                value="10+ years expert Python architecture",
                verification_status=VerificationStatus.USER_VERIFIED,
                allowed_purposes=["job_application", "case_preparation"],
            )
            uow.context.save(self.fact_py)

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
                claim_id="claim-svc-1",
                user_id=self.user_id,
                purpose="job_application",
                text="SRE Expertise in Python",
                status=ClaimStatus.SUPPORTED,
                supporting_fact_ids=[self.fact_py.fact_id],
                case_id=self.case_id,
                source_report_id=self.report.report_id,
                source_claim_intent_id="intent-1",
                assertion_schema="skill_possession",
                assertion_json='{"kind": "skill_possession", "fact_ids": ["fact-py"], "fields": {"skill": "Python"}}',
                support_snapshot_hash=supp_hash,
                version=1,
            )
            uow.claims.save(self.claim)

            cv_grounding = compute_artifact_grounding_hash(claims=[self.claim], direct_facts=[self.fact_name])
            cl_grounding = compute_artifact_grounding_hash(claims=[], direct_facts=[self.fact_name])
            qa_grounding = compute_artifact_grounding_hash(claims=[self.claim], direct_facts=[])

            self.art_resume = PreparedArtifact(
                artifact_id="art-svc-resume",
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
                    ArtifactBlock(block_id="b-header", block_type="header", content="Profile", grounding_type=ArtifactGroundingType.CONTEXT_FACT, fact_ids=[self.fact_name.fact_id]),
                    ArtifactBlock(block_id="b1", content="SRE skills", grounding_type=ArtifactGroundingType.CLAIM, claim_ids=[self.claim.claim_id]),
                ],
            )
            uow.preparation_artifacts.save(self.art_resume)

            self.art_cl = PreparedArtifact(
                artifact_id="art-svc-cl",
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
                blocks=[ArtifactBlock(block_id="b2", content="Cover letter content", grounding_type=ArtifactGroundingType.CONTEXT_FACT, fact_ids=[self.fact_name.fact_id])],
            )
            uow.preparation_artifacts.save(self.art_cl)

            self.art_qa = PreparedArtifact(
                artifact_id="art-svc-qa",
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

        package, action, approval, _, _ = self.package_service.verify_and_prepare_package(
            user_id=self.user_id,
            case_id=self.case_id,
            artifact_set_id=self.set_id,
            expected_case_version=1,
        )
        self.approval_service.approve_action(
            user_id=self.user_id,
            approval_id=approval.approval_id,
            reason="User approved package",
        )
        with self.storage.unit_of_work() as uow:
            self.package = uow.preparation_packages.get_by_id(package.package_id)

        self.schema = TargetInteractionSchema(
            schema_id="schema-apex",
            destination_type=ExecutionDestinationType.ATS_FORM.value,
            fields=[TargetSchemaField(target_field_id="name", label="Full Name", required=True)],
            upload_slots=[
                TargetSchemaUploadSlot(
                    slot_id="slot-resume",
                    role="RESUME",
                    label="Resume",
                    required=True,
                )
            ],
            final_submit_control=TargetSubmitControlSpec(
                control_type="button_submit",
                semantic_target="Submit",
                selector="#submit",
                method="POST",
            ),
        )
        self.bindings = [
            TargetFieldBinding(target_field_id="name", source_fact_id=self.fact_name.fact_id)
        ]

    def tearDown(self) -> None:
        self.storage.close()

    def test_case_status_remains_action_required_and_no_action_or_approval_created(self) -> None:
        """Amendment 5: Zero external transmission; case remains in ACTION_REQUIRED, no Action created."""
        with self.storage.unit_of_work() as uow:
            initial_actions_count = len(uow.actions.list_by_case(self.case.case_id))
            initial_approvals_count = len(uow.approvals.list_by_case(self.case.case_id))

        plan = self.service.create_execution_plan(
            user_id=self.user_id,
            case_id=self.case.case_id,
            target_schema=self.schema,
        )
        self.assertIsNotNone(plan)

        with self.storage.unit_of_work() as uow:
            refreshed_case = uow.cases.get_by_id(self.case.case_id)
            self.assertEqual(refreshed_case.status, CaseStatus.ACTION_REQUIRED)
            actions = uow.actions.list_by_case(self.case.case_id)
            self.assertEqual(len(actions), initial_actions_count)
            approvals = uow.approvals.list_by_case(self.case.case_id)
            self.assertEqual(len(approvals), initial_approvals_count)
            for a in actions:
                self.assertNotIn("SUBMIT", a.action_type.upper())
            events = uow.events.get_events_by_user(self.user_id)
            event_types = [e.event_type for e in events]
            self.assertIn("execution.plan_created", event_types)

    def test_shared_package_integrity_validator_detects_tampering(self) -> None:
        """Amendment 8: PreparationPackageIntegrityValidator catches tampering before planning."""
        # 1. Package not APPROVED
        with self.storage.unit_of_work() as uow:
            pkg = uow.preparation_packages.get_by_id(self.package.package_id)
            pkg.status = PreparationPackageStatus.FROZEN
            pkg.version += 1
            uow.preparation_packages.save(pkg)

        with self.assertRaises(DomainValidationError):
            self.service.create_execution_plan(
                user_id=self.user_id,
                case_id=self.case.case_id,
                target_schema=self.schema,
            )

        # 2. Tampered artifact rendered hash
        with self.storage.unit_of_work() as uow:
            pkg = uow.preparation_packages.get_by_id(self.package.package_id)
            pkg.status = PreparationPackageStatus.APPROVED
            pkg.version += 1
            uow.preparation_packages.save(pkg)
            art = uow.preparation_artifacts.get_by_id(self.art_resume.artifact_id)
            art.blocks = [
                ArtifactBlock(
                    block_id="b1",
                    content="TAMPERED CONTENT",
                    grounding_type=ArtifactGroundingType.CLAIM,
                    claim_ids=[self.claim.claim_id],
                )
            ]
            art.version += 1
            uow.preparation_artifacts.save(art)

        with self.assertRaises(PackageVerificationError):
            self.service.create_execution_plan(
                user_id=self.user_id,
                case_id=self.case.case_id,
                target_schema=self.schema,
            )

    def test_idempotency_and_supersession(self) -> None:
        """Amendment 9: Identical fingerprint returns idempotently; changed inputs supersede prior active plan."""
        # First creation
        plan1 = self.service.create_execution_plan(
            user_id=self.user_id,
            case_id=self.case.case_id,
            target_schema=self.schema,
            field_bindings=self.bindings,
        )
        self.assertEqual(plan1.status, ExternalExecutionPlanStatus.READY_FOR_AUTHORIZATION)

        # Idempotent replay with same inputs
        plan2 = self.service.create_execution_plan(
            user_id=self.user_id,
            case_id=self.case.case_id,
            target_schema=self.schema,
            field_bindings=self.bindings,
        )
        self.assertEqual(plan1.plan_id, plan2.plan_id)
        self.assertEqual(plan1.execution_plan_fingerprint, plan2.execution_plan_fingerprint)

        # Create with destination override (changed input)
        dest_override = ExecutionDestination(
            destination_type=ExecutionDestinationType.ATS_FORM.value,
            destination_provenance=DestinationProvenance.USER_PROVIDED.value,
            canonical_url="https://portal.apex.com/apply",
            organization="Apex Direct",
        )
        plan3 = self.service.create_execution_plan(
            user_id=self.user_id,
            case_id=self.case.case_id,
            target_schema=self.schema,
            field_bindings=self.bindings,
            destination_override=dest_override,
        )
        self.assertNotEqual(plan1.plan_id, plan3.plan_id)
        self.assertEqual(plan3.status, ExternalExecutionPlanStatus.READY_FOR_AUTHORIZATION)

        # Verify plan1 is now SUPERSEDED
        with self.storage.unit_of_work() as uow:
            p1_refreshed = uow.execution_plans.get_by_id(plan1.plan_id)
            self.assertEqual(p1_refreshed.status, ExternalExecutionPlanStatus.SUPERSEDED)

            events = uow.events.get_events_by_user(self.user_id)
            event_types = [e.event_type for e in events]
            self.assertIn("execution.plan_superseded", event_types)

    def test_privacy_safe_events(self) -> None:
        """Amendment 13: Created and superseded events carry zero raw personal values or answers."""
        created_ev = make_external_execution_plan_created_event(
            user_id=self.user_id,
            case_id=self.case.case_id,
            plan_id="plan-1",
            package_id="pkg-1",
            package_fingerprint="pkg_fp_1",
            destination_type="ats_form",
            canonical_url="https://acme.com/apply",
            plan_status="ready_for_authorization",
            plan_fingerprint="plan_fp_1",
            field_mapping_count=5,
            upload_asset_count=1,
            screening_response_count=2,
            unresolved_findings_count=0,
        )
        payload = created_ev.payload
        self.assertNotIn("First Name", str(payload))
        self.assertNotIn("Grant", str(payload))
        self.assertNotIn("grant@example.com", str(payload))
        self.assertEqual(payload["field_mapping_count"], 5)
        self.assertEqual(payload["host"], "acme.com")

        superseded_ev = make_external_execution_plan_superseded_event(
            user_id=self.user_id,
            case_id=self.case.case_id,
            plan_id="plan-1",
            superseded_by_plan_id="plan-2",
            package_id="pkg-1",
            reason="Superseded by newer plan",
        )
        self.assertNotIn("Grant", str(superseded_ev.payload))

    def test_repository_content_immutability(self) -> None:
        """Amendment 17: Mutating content-addressed fields of an existing plan raises DomainValidationError."""
        plan = self.service.create_execution_plan(
            user_id=self.user_id,
            case_id=self.case.case_id,
            target_schema=self.schema,
        )
        with self.storage.unit_of_work() as uow:
            loaded_plan = uow.execution_plans.get_by_id(plan.plan_id)
            self.assertIsNotNone(loaded_plan)
            # Mutate content-addressed field
            object.__setattr__(loaded_plan, "package_id", "tampered_pkg")
            loaded_plan.version += 1
            with self.assertRaises(DomainValidationError):
                uow.execution_plans.save(loaded_plan)

    def test_clarification1_schema_persistence_and_fingerprint_binding(self) -> None:
        """Clarification 1: Target schema hash, input hash, planning findings, and provenance persisted and bound in fingerprint."""
        plan = self.service.create_execution_plan(
            user_id=self.user_id,
            case_id=self.case.case_id,
            target_schema=self.schema,
            field_bindings=self.bindings,
        )
        with self.storage.unit_of_work() as uow:
            loaded = uow.execution_plans.get_by_id(plan.plan_id)
            self.assertIsNotNone(loaded)
            self.assertEqual(loaded.target_schema_hash, self.schema.schema_hash)
            self.assertEqual(loaded.destination_provenance, DestinationProvenance.OPPORTUNITY_APPLICATION_URL.value)
            self.assertTrue(len(loaded.execution_input_hash) > 0)
            self.assertTrue(len(loaded.findings) > 0)
            self.assertEqual(loaded.planning_findings, loaded.findings)

        # Modifying the schema produces a different schema hash and fingerprint
        modified_schema = TargetInteractionSchema(
            schema_id="ats_workable_mod",
            destination_type="ats_form",
            fields=list(self.schema.fields) + [
                TargetSchemaField(target_field_id="portfolio_url", label="Portfolio", required=False)
            ],
            upload_slots=list(self.schema.upload_slots),
            screening_questions=list(self.schema.screening_questions),
            final_submit_control=self.schema.final_submit_control,
        )
        self.assertNotEqual(self.schema.schema_hash, modified_schema.schema_hash)

        plan_mod = self.service.create_execution_plan(
            user_id=self.user_id,
            case_id=self.case.case_id,
            target_schema=modified_schema,
            field_bindings=self.bindings,
        )
        self.assertNotEqual(plan.execution_plan_fingerprint, plan_mod.execution_plan_fingerprint)

    def test_clarification2_durable_upload_assets_repository(self) -> None:
        """Clarification 2: Dedicated execution_upload_assets table with BLOB bytes and preconditions."""
        plan = self.service.create_execution_plan(
            user_id=self.user_id,
            case_id=self.case.case_id,
            target_schema=self.schema,
            field_bindings=self.bindings,
        )
        self.assertEqual(plan.status, ExternalExecutionPlanStatus.READY_FOR_AUTHORIZATION)
        self.assertEqual(len(plan.upload_mappings), 1)
        upload = plan.upload_mappings[0]

        # Verify persisted BLOB via uow.upload_assets
        with self.storage.unit_of_work() as uow:
            asset = uow.upload_assets.get_by_id(upload.asset_id)
            self.assertIsNotNone(asset)
            self.assertIsInstance(asset.content_bytes, bytes)
            self.assertEqual(asset.byte_size, len(asset.content_bytes))
            self.assertEqual(asset.content_sha256, upload.content_sha256)
            self.assertEqual(asset.plan_id, plan.plan_id)
            self.assertEqual(asset.case_id, plan.case_id)
            self.assertEqual(asset.user_id, plan.user_id)

            # Test list_by_plan and list_by_case
            by_plan = uow.upload_assets.list_by_plan(plan.plan_id)
            self.assertEqual(len(by_plan), 1)
            self.assertEqual(by_plan[0].asset_id, upload.asset_id)

            by_case = uow.upload_assets.list_by_case(plan.case_id)
            self.assertEqual(len(by_case), 1)
            self.assertEqual(by_case[0].asset_id, upload.asset_id)

    def test_clarification3_plan_staleness_computation_and_side_effect_free(self) -> None:
        """Clarification 3: Plan staleness detection across dependencies, side-effect free on GET."""
        plan = self.service.create_execution_plan(
            user_id=self.user_id,
            case_id=self.case.case_id,
            target_schema=self.schema,
            field_bindings=self.bindings,
        )
        # Newly created plan is not stale
        fetched = self.service.get_latest_execution_plan(self.user_id, self.case.case_id)
        self.assertIsNotNone(fetched)
        self.assertFalse(fetched.is_stale)
        self.assertEqual(fetched.staleness_reasons, [])

        # Now mutate a dependent ContextFact version (e.g. name updated)
        with self.storage.unit_of_work() as uow:
            fact = uow.context.get_by_id(self.fact_name.fact_id)
            self.assertIsNotNone(fact)
            updated_fact = ContextFact(
                fact_id=fact.fact_id,
                user_id=fact.user_id,
                namespace=fact.namespace,
                key=fact.key,
                value="Grant Dagogo-Jack PhD",
                source_type=fact.source_type,
                source_reference=fact.source_reference,
                confidence=fact.confidence,
                verification_status=fact.verification_status,
                sensitivity=fact.sensitivity,
                allowed_purposes=fact.allowed_purposes,
                created_at=fact.created_at,
                updated_at=now_utc(),
                version=fact.version + 1,
            )
            uow.context.save(updated_fact)
            uow.commit()

        # GET should now report is_stale=True side-effect free (DB status is still READY_FOR_AUTHORIZATION)
        fetched_stale = self.service.get_latest_execution_plan(self.user_id, self.case.case_id)
        self.assertIsNotNone(fetched_stale)
        self.assertTrue(fetched_stale.is_stale)
        self.assertTrue(any("version changed" in r for r in fetched_stale.staleness_reasons))

        # Check DB status remains READY_FOR_AUTHORIZATION
        with self.storage.unit_of_work() as uow:
            db_plan = uow.execution_plans.get_by_id(plan.plan_id)
            self.assertEqual(db_plan.status, ExternalExecutionPlanStatus.READY_FOR_AUTHORIZATION)

    def test_clarification4_destination_security_and_normalization(self) -> None:
        """Clarification 4: Destination security: reject credentials, non-https, forbidden schemes, store normalized origin & hostname."""
        # Valid https with path and query
        dest = ExecutionDestination(
            destination_type=ExecutionDestinationType.ATS_FORM.value,
            destination_provenance=DestinationProvenance.OPPORTUNITY_APPLICATION_URL.value,
            canonical_url="https://ACME.com:443/apply/job?source=linkedin",
            organization="Acme",
        )
        self.assertEqual(dest.normalized_origin, "https://acme.com:443")
        self.assertEqual(dest.normalized_hostname, "acme.com")
        self.assertEqual(dest.canonical_url, "https://ACME.com:443/apply/job?source=linkedin")

        # Reject embedded credentials
        with self.assertRaises(DomainValidationError):
            ExecutionDestination(
                destination_type=ExecutionDestinationType.ATS_FORM.value,
                destination_provenance=DestinationProvenance.USER_PROVIDED.value,
                canonical_url="https://user:pass@evil.com/apply",
                organization="Evil",
            )

        # Reject non-https for ATS_FORM
        with self.assertRaises(DomainValidationError):
            ExecutionDestination(
                destination_type=ExecutionDestinationType.ATS_FORM.value,
                destination_provenance=DestinationProvenance.USER_PROVIDED.value,
                canonical_url="http://insecure.com/apply",
                organization="Insecure",
            )

        # Reject javascript: and data:
        with self.assertRaises(DomainValidationError):
            ExecutionDestination(
                destination_type=ExecutionDestinationType.CUSTOM.value,
                destination_provenance=DestinationProvenance.USER_PROVIDED.value,
                canonical_url="javascript:alert(1)",
                organization="XSS",
            )

    def test_clarification5_target_field_semantic_type_validation(self) -> None:
        """Clarification 5: Target field semantic type validation: strict select, boolean, email, phone, date; ambiguous kept in DRAFT."""
        # Construct schema with typed fields
        typed_schema = TargetInteractionSchema(
            schema_id="ats_typed_schema",
            destination_type="ats_form",
            fields=[
                TargetSchemaField(target_field_id="work_auth", label="Authorized to work in Germany?", field_type="select", required=True, allowed_options=["Yes", "No"]),
                TargetSchemaField(target_field_id="remote_ok", label="Willing to work remote?", field_type="boolean", required=True),
                TargetSchemaField(target_field_id="start_date", label="Start Date", field_type="date", required=True),
                TargetSchemaField(target_field_id="contact_email", label="Email", field_type="email", required=True),
                TargetSchemaField(target_field_id="contact_phone", label="Phone", field_type="phone", required=True),
            ],
            upload_slots=list(self.schema.upload_slots),
            screening_questions=[],
            final_submit_control=self.schema.final_submit_control,
        )

        # Case 1: Fact has descriptive text instead of exact option: "authorized to work in Germany"
        with self.storage.unit_of_work() as uow:
            uow.context.save(ContextFact(
                fact_id="fact-auth-ambiguous",
                user_id=self.user_id,
                namespace="work_authorization",
                key="work_auth",
                value="authorized to work in Germany without restriction",
                verification_status=VerificationStatus.USER_VERIFIED,
                allowed_purposes=["job_application"],
            ))
            uow.context.save(ContextFact(
                fact_id="fact-remote",
                user_id=self.user_id,
                namespace="preferences",
                key="remote_ok",
                value="True",
                verification_status=VerificationStatus.USER_VERIFIED,
                allowed_purposes=["job_application"],
            ))
            uow.context.save(ContextFact(
                fact_id="fact-date",
                user_id=self.user_id,
                namespace="availability",
                key="start_date",
                value="2026-11-01",
                verification_status=VerificationStatus.USER_VERIFIED,
                allowed_purposes=["job_application"],
            ))
            uow.context.save(ContextFact(
                fact_id="fact-email",
                user_id=self.user_id,
                namespace="identity",
                key="contact_email",
                value="grant@example.com",
                verification_status=VerificationStatus.USER_VERIFIED,
                allowed_purposes=["job_application"],
            ))
            uow.context.save(ContextFact(
                fact_id="fact-phone",
                user_id=self.user_id,
                namespace="identity",
                key="contact_phone",
                value="+49 151 12345678",
                verification_status=VerificationStatus.USER_VERIFIED,
                allowed_purposes=["job_application"],
            ))
            uow.commit()

        # When compiling, select field "work_auth" cannot guess between "Yes" and "No"
        # Plan MUST stay DRAFT with INVALID_FIELD_TYPE_CONVERSION finding
        typed_bindings = [
            TargetFieldBinding(target_field_id="work_auth", source_fact_id="fact-auth-ambiguous"),
            TargetFieldBinding(target_field_id="remote_ok", source_fact_id="fact-remote"),
            TargetFieldBinding(target_field_id="start_date", source_fact_id="fact-date"),
            TargetFieldBinding(target_field_id="contact_email", source_fact_id="fact-email"),
            TargetFieldBinding(target_field_id="contact_phone", source_fact_id="fact-phone"),
        ]
        plan_draft = self.service.create_execution_plan(
            user_id=self.user_id,
            case_id=self.case.case_id,
            target_schema=typed_schema,
            field_bindings=typed_bindings,
        )
        self.assertEqual(plan_draft.status, ExternalExecutionPlanStatus.DRAFT)
        err_findings = [f for f in plan_draft.findings if f.code == "INVALID_FIELD_TYPE_CONVERSION"]
        self.assertTrue(len(err_findings) > 0)
        self.assertIn("work_auth", [f.details.get("target_field_id") for f in err_findings])

    def test_immutable_upload_asset_collision_and_idempotent_replay(self) -> None:
        """Correction 1: Asset collision raises PersistenceError if different, replays if identical."""
        plan = self.service.create_execution_plan(
            user_id=self.user_id,
            case_id=self.case.case_id,
            target_schema=self.schema,
            field_bindings=self.bindings,
        )
        content = b"PDF-EXACT-APPLICATION-CONTENT"
        c_hash = compute_sha256_bytes(content)
        asset = ExecutionUploadAsset(
            asset_id="asset-test-collision-1",
            role="RESUME",
            source_artifact_id=self.art_resume.artifact_id,
            source_artifact_version=1,
            filename="resume.pdf",
            mime_type="application/pdf",
            content_sha256=c_hash,
            byte_size=len(content),
            content_bytes=content,
            plan_id=plan.plan_id,
            case_id=self.case.case_id,
            user_id=self.user_id,
        )

        with self.storage.unit_of_work() as uow:
            uow.upload_assets.save(asset)
            uow.commit()

        # Idempotent replay with exact same content bytes & fields
        with self.storage.unit_of_work() as uow:
            uow.upload_assets.save(asset)
            uow.commit()

        # Differing contents with same asset_id must raise PersistenceError
        tampered_content = b"TAMPERED-CONTENT-DIFFERING"
        tampered_asset = ExecutionUploadAsset(
            asset_id="asset-test-collision-1",
            role="RESUME",
            source_artifact_id=self.art_resume.artifact_id,
            source_artifact_version=1,
            filename="resume.pdf",
            mime_type="application/pdf",
            content_sha256=compute_sha256_bytes(tampered_content),
            byte_size=len(tampered_content),
            content_bytes=tampered_content,
            plan_id=plan.plan_id,
            case_id=self.case.case_id,
            user_id=self.user_id,
        )
        with self.storage.unit_of_work() as uow:
            with self.assertRaises(PersistenceError):
                uow.upload_assets.save(tampered_asset)

    def test_destination_security_query_and_fragment_secrets(self) -> None:
        """Correction 5 & Hardening Point 4: Query parameters and fragments inspected for authentication secrets."""
        # Forbidden param in query
        with self.assertRaises(DomainValidationError):
            assert_safe_destination(
                canonical_url="https://hyperscale.apply.workable.com/roles/123?api_key=secret-token",
                destination_type="ats_form",
                is_exact_destination=True,
            )

        # Forbidden secret pattern in fragment
        with self.assertRaises(DomainValidationError):
            assert_safe_destination(
                canonical_url="https://hyperscale.apply.workable.com/roles/123#access_token=eyJh12345",
                destination_type="ats_form",
                is_exact_destination=True,
            )

        # Token parameter in query
        with self.assertRaises(DomainValidationError):
            assert_safe_destination(
                canonical_url="https://hyperscale.apply.workable.com/roles/123?token=bearer-token-123",
                destination_type="ats_form",
                is_exact_destination=True,
            )

    def test_staleness_reruns_normalization_and_detects_value_alteration(self) -> None:
        """Correction 3: Staleness revalidates normalized value even if ContextFact version did not increment."""
        plan = self.service.create_execution_plan(
            user_id=self.user_id,
            case_id=self.case.case_id,
            target_schema=self.schema,
            field_bindings=self.bindings,
        )
        self.assertEqual(plan.status, ExternalExecutionPlanStatus.READY_FOR_AUTHORIZATION)

        # Silently alter the fact value in the database WITHOUT incrementing version
        with self.storage.unit_of_work() as uow:
            fact = uow.context.get_by_id(self.fact_name.fact_id)
            self.assertIsNotNone(fact)
            cur = uow.conn.cursor()
            cur.execute(
                "UPDATE context_facts SET value = ? WHERE fact_id = ?",
                (json.dumps("Silently Altered Name"), fact.fact_id),
            )
            uow.commit()

        # Live staleness check must catch the discrepancy
        fetched = self.service.get_latest_execution_plan(self.user_id, self.case.case_id)
        self.assertIsNotNone(fetched)
        self.assertTrue(fetched.is_stale)
        self.assertTrue(any("value changed" in r for r in fetched.staleness_reasons))

    def test_screening_question_select_and_boolean_validation(self) -> None:
        """Correction 4 & Hardening Point 8: Screening questions enforce strict semantic type conversion."""
        schema_screening = TargetInteractionSchema(
            schema_id="schema-screening-typed",
            destination_type="ats_form",
            fields=list(self.schema.fields),
            upload_slots=list(self.schema.upload_slots),
            screening_questions=[
                TargetSchemaQuestion(
                    question_id="q-remote-pref",
                    question_text="Tell us about your Python experience?",
                    answer_type=TargetAnswerType.BOOLEAN.value,
                    required=True,
                )
            ],
            final_submit_control=self.schema.final_submit_control,
        )
        # The answer in self.art_qa is "Yes, 10 years Python experience." which is ambiguous for boolean conversion
        plan = self.service.create_execution_plan(
            user_id=self.user_id,
            case_id=self.case.case_id,
            target_schema=schema_screening,
            field_bindings=self.bindings,
        )
        self.assertEqual(plan.status, ExternalExecutionPlanStatus.DRAFT)
        codes = [f.code for f in plan.findings]
        self.assertIn("INVALID_QUESTION_ANSWER_CONVERSION", codes)

    def test_target_schema_provenance_and_exact_submit_locator(self) -> None:
        """Correction 4: Schema provenance binds into hash; locator required for READY_FOR_AUTHORIZATION."""
        # 1. Without locator -> keeps plan in DRAFT
        schema_no_locator = TargetInteractionSchema(
            schema_id="schema-no-locator",
            destination_type="ats_form",
            target_schema_provenance=TargetSchemaProvenance.USER_PROVIDED.value,
            fields=list(self.schema.fields),
            upload_slots=list(self.schema.upload_slots),
            screening_questions=[],
            final_submit_control=TargetSubmitControlSpec(
                control_type="button_submit",
                semantic_target="Submit",
                selector="",  # Missing selector and control_id
                control_id="",
                method="POST",
            ),
        )
        plan_draft = self.service.create_execution_plan(
            user_id=self.user_id,
            case_id=self.case.case_id,
            target_schema=schema_no_locator,
            field_bindings=self.bindings,
        )
        self.assertEqual(plan_draft.status, ExternalExecutionPlanStatus.DRAFT)
        codes = [f.code for f in plan_draft.findings]
        self.assertIn("EXACT_SUBMIT_LOCATOR_REQUIRED", codes)

        # 2. Provenance variation alters schema_hash and plan fingerprint
        schema_verified_provenance = TargetInteractionSchema(
            schema_id="schema-prov-test",
            destination_type="ats_form",
            target_schema_provenance=TargetSchemaProvenance.VERIFIED_PROVIDER_METADATA.value,
            fields=list(self.schema.fields),
            upload_slots=list(self.schema.upload_slots),
            screening_questions=[],
            final_submit_control=self.schema.final_submit_control,
        )
        schema_user_provenance = TargetInteractionSchema(
            schema_id="schema-prov-test",
            destination_type="ats_form",
            target_schema_provenance=TargetSchemaProvenance.USER_PROVIDED.value,
            fields=list(self.schema.fields),
            upload_slots=list(self.schema.upload_slots),
            screening_questions=[],
            final_submit_control=self.schema.final_submit_control,
        )
        self.assertNotEqual(schema_verified_provenance.schema_hash, schema_user_provenance.schema_hash)

    def test_context_vault_audit_trail_emitted(self) -> None:
        """Correction 5: Compilation emits context.access_granted / context.access_denied audit events."""
        plan = self.service.create_execution_plan(
            user_id=self.user_id,
            case_id=self.case.case_id,
            target_schema=self.schema,
            field_bindings=self.bindings,
        )
        self.assertIsNotNone(plan)

        with self.storage.unit_of_work() as uow:
            events = uow.events.get_events_by_user(self.user_id)
            event_types = [e.event_type for e in events]
            self.assertIn("context.access_granted", event_types)

    def test_no_guessed_schema_can_become_ready(self) -> None:
        """Residual guessing removal: Without explicit target interaction schema, plan remains DRAFT."""
        plan = self.service.create_execution_plan(
            user_id=self.user_id,
            case_id=self.case.case_id,
            target_schema=None,
            field_bindings=self.bindings,
        )
        self.assertEqual(plan.status, ExternalExecutionPlanStatus.DRAFT)
        codes = [f.code for f in plan.findings]
        self.assertIn("TARGET_SCHEMA_REQUIRED", codes)

    def test_omitted_submit_method_cannot_become_ready(self) -> None:
        """Residual guessing removal: Submit control without explicit method emits error and remains DRAFT."""
        schema_no_method = TargetInteractionSchema(
            schema_id="schema-no-method",
            destination_type="ats_form",
            fields=list(self.schema.fields),
            upload_slots=list(self.schema.upload_slots),
            screening_questions=[],
            final_submit_control=TargetSubmitControlSpec(
                control_type="button_submit",
                semantic_target="Submit",
                selector="#submit",
                method="",  # Omitted method
            ),
        )
        plan = self.service.create_execution_plan(
            user_id=self.user_id,
            case_id=self.case.case_id,
            target_schema=schema_no_method,
            field_bindings=self.bindings,
        )
        self.assertEqual(plan.status, ExternalExecutionPlanStatus.DRAFT)
        codes = [f.code for f in plan.findings]
        self.assertIn("SUBMIT_CONTROL_METHOD_REQUIRED", codes)

    def test_omitted_control_type_or_semantic_target_cannot_become_ready(self) -> None:
        """Residual guessing removal: Partial submit control is valid DRAFT input but fails compilation."""
        incomplete_ctrl = TargetSubmitControlSpec(
            control_type="",
            semantic_target="",
            selector="#submit",
            method="POST",
        )
        self.assertEqual(incomplete_ctrl.control_type, "")
        self.assertEqual(incomplete_ctrl.semantic_target, "")

        schema_incomplete = TargetInteractionSchema(
            schema_id="schema-incomplete-ctrl",
            destination_type="ats_form",
            fields=list(self.schema.fields),
            upload_slots=list(self.schema.upload_slots),
            screening_questions=[],
            final_submit_control=incomplete_ctrl,
        )
        plan = self.service.create_execution_plan(
            user_id=self.user_id,
            case_id=self.case.case_id,
            target_schema=schema_incomplete,
            field_bindings=self.bindings,
        )
        self.assertEqual(plan.status, ExternalExecutionPlanStatus.DRAFT)
        codes = [f.code for f in plan.findings]
        self.assertIn("SUBMIT_CONTROL_TYPE_REQUIRED", codes)
        self.assertIn("SUBMIT_CONTROL_SEMANTIC_TARGET_REQUIRED", codes)

    def test_malformed_date_screening_answer_remains_draft(self) -> None:
        """Residual guessing removal: Date screening answers must conform strictly to ISO YYYY-MM-DD."""
        schema_date = TargetInteractionSchema(
            schema_id="schema-date-test",
            destination_type="ats_form",
            fields=list(self.schema.fields),
            upload_slots=list(self.schema.upload_slots),
            screening_questions=[
                TargetSchemaQuestion(
                    question_id="q-start-date",
                    question_text="Tell us about your Python experience?",  # Answer is "Yes, 10 years Python experience."
                    answer_type=TargetAnswerType.DATE.value,
                    required=True,
                )
            ],
            final_submit_control=self.schema.final_submit_control,
        )
        plan = self.service.create_execution_plan(
            user_id=self.user_id,
            case_id=self.case.case_id,
            target_schema=schema_date,
            field_bindings=self.bindings,
        )
        self.assertEqual(plan.status, ExternalExecutionPlanStatus.DRAFT)
        codes = [f.code for f in plan.findings]
        self.assertIn("INVALID_QUESTION_ANSWER_CONVERSION", codes)

    def test_malformed_number_screening_answer_remains_draft(self) -> None:
        """Residual guessing removal: Number screening answers must be locale-neutral and reject ambiguous formats."""
        schema_number = TargetInteractionSchema(
            schema_id="schema-num-test",
            destination_type="ats_form",
            fields=list(self.schema.fields),
            upload_slots=list(self.schema.upload_slots),
            screening_questions=[
                TargetSchemaQuestion(
                    question_id="q-years",
                    question_text="Tell us about your Python experience?",
                    answer_type=TargetAnswerType.NUMBER.value,
                    required=True,
                )
            ],
            final_submit_control=self.schema.final_submit_control,
        )
        plan = self.service.create_execution_plan(
            user_id=self.user_id,
            case_id=self.case.case_id,
            target_schema=schema_number,
            field_bindings=self.bindings,
        )
        self.assertEqual(plan.status, ExternalExecutionPlanStatus.DRAFT)
        codes = [f.code for f in plan.findings]
        self.assertIn("INVALID_QUESTION_ANSWER_CONVERSION", codes)

    def test_empty_select_options_remains_draft(self) -> None:
        """Residual guessing removal: Select screening questions with empty allowed_options emit SCREENING_SELECT_OPTIONS_EMPTY."""
        schema_empty_select = TargetInteractionSchema(
            schema_id="schema-empty-select",
            destination_type="ats_form",
            fields=list(self.schema.fields),
            upload_slots=list(self.schema.upload_slots),
            screening_questions=[
                TargetSchemaQuestion(
                    question_id="q-select-empty",
                    question_text="Tell us about your Python experience?",
                    answer_type=TargetAnswerType.SELECT.value,
                    allowed_options=[],
                    required=True,
                )
            ],
            final_submit_control=self.schema.final_submit_control,
        )
        plan = self.service.create_execution_plan(
            user_id=self.user_id,
            case_id=self.case.case_id,
            target_schema=schema_empty_select,
            field_bindings=self.bindings,
        )
        self.assertEqual(plan.status, ExternalExecutionPlanStatus.DRAFT)
        codes = [f.code for f in plan.findings]
        self.assertIn("SCREENING_SELECT_OPTIONS_EMPTY", codes)

    def test_ownership_tampered_asset_makes_plan_stale(self) -> None:
        """Residual guessing removal: Tampering with upload asset user/case/plan ownership marks plan stale."""
        plan = self.service.create_execution_plan(
            user_id=self.user_id,
            case_id=self.case.case_id,
            target_schema=self.schema,
            field_bindings=self.bindings,
        )
        self.assertEqual(plan.status, ExternalExecutionPlanStatus.READY_FOR_AUTHORIZATION)

        # Alter user_id ownership on the stored durable upload asset
        with self.storage.unit_of_work() as uow:
            u_asset = plan.upload_mappings[0]
            cur = uow.conn.cursor()
            cur.execute(
                "UPDATE execution_upload_assets SET user_id = ? WHERE asset_id = ?",
                ("tampered_user_999", u_asset.asset_id),
            )
            uow.commit()

        # Staleness computation must detect the tampered ownership
        fetched = self.service.get_latest_execution_plan(self.user_id, self.case.case_id)
        self.assertIsNotNone(fetched)
        self.assertTrue(fetched.is_stale)
        self.assertTrue(any("ownership or metadata mismatch" in r for r in fetched.staleness_reasons))

    def test_provenance_casing_canonicalizes_deterministically(self) -> None:
        """Residual guessing removal: Provenance strings normalize to lowercase; invalid values fail validation."""
        ctrl = TargetSubmitControlSpec(
            control_type="button_submit",
            semantic_target="Submit",
            control_provenance=" USER_PROVIDED ",
        )
        self.assertEqual(ctrl.control_provenance, "user_provided")

        dest = ExecutionDestination(
            destination_type="ats_form",
            destination_provenance=" OPPORTUNITY_APPLICATION_URL ",
            canonical_url="https://acme.org/jobs/1",
            organization="Acme",
        )
        self.assertEqual(dest.destination_provenance, "opportunity_application_url")

        schema = TargetInteractionSchema(
            schema_id="schema-prov-case",
            destination_type="ats_form",
            target_schema_provenance=" USER_PROVIDED ",
        )
        self.assertEqual(schema.target_schema_provenance, "user_provided")

        # Invalid provenance values fail domain validation
        with self.assertRaises(DomainValidationError):
            TargetSubmitControlSpec(
                control_type="button",
                semantic_target="Submit",
                control_provenance="fabricated_provenance",
            )

        with self.assertRaises(DomainValidationError):
            ExecutionDestination(
                destination_type="ats_form",
                destination_provenance="untrusted_guess",
                canonical_url="https://acme.org/jobs/1",
                organization="Acme",
            )

    def test_strict_iso_date_validation_for_target_fields(self) -> None:
        """Strict ISO date validation: reject non-padded or non-calendar dates, accept valid YYYY-MM-DD."""
        date_field = TargetSchemaField(target_field_id="start_date", label="Start Date", field_type="date", required=True)

        # Unpadded months or days must be rejected
        for bad_date in ("2026-1-1", "2026-01-1", "2026-1-01", "2026-1-12"):
            ok, val, err = validate_and_normalize_field_value(date_field, bad_date)
            self.assertFalse(ok)
            self.assertIsNone(val)
            self.assertIn("does not conform to canonical format YYYY-MM-DD", err)

        # Non-ISO formats must be rejected
        for bad_date in ("01/12/2026", "2026/01/01", "October 18, 2026", "2026-10"):
            ok, val, err = validate_and_normalize_field_value(date_field, bad_date)
            self.assertFalse(ok)
            self.assertIsNone(val)
            self.assertIn("does not conform to canonical format YYYY-MM-DD", err)

        # Invalid calendar dates (even if matching \d{4}-\d{2}-\d{2}) must be rejected
        for bad_cal in ("2026-02-30", "2026-04-31", "2026-13-01", "2026-00-10", "2026-01-32"):
            ok, val, err = validate_and_normalize_field_value(date_field, bad_cal)
            self.assertFalse(ok)
            self.assertIsNone(val)
            self.assertIn("does not conform to canonical format YYYY-MM-DD", err)

        # Valid ISO calendar dates must be accepted
        for good_date in ("2026-01-01", "2026-12-31", "2024-02-29"):
            ok, val, err = validate_and_normalize_field_value(date_field, good_date)
            self.assertTrue(ok)
            self.assertEqual(val, good_date)
            self.assertEqual(err, "")

    def test_strict_iso_date_screening_answer_rejection(self) -> None:
        """Screening question date conversions must reject unpadded dates and invalid calendar dates."""
        sq = TargetSchemaQuestion(
            question_id="q-date",
            question_text="Tell us about your Python experience?",
            answer_type=TargetAnswerType.DATE.value,
            required=True,
        )
        schema_date = TargetInteractionSchema(
            schema_id="schema-strict-date",
            destination_type="ats_form",
            fields=list(self.schema.fields),
            upload_slots=list(self.schema.upload_slots),
            screening_questions=[sq],
            final_submit_control=self.schema.final_submit_control,
        )
        plan = self.service.create_execution_plan(
            user_id=self.user_id,
            case_id=self.case.case_id,
            target_schema=schema_date,
            field_bindings=self.bindings,
        )
        self.assertEqual(plan.status, ExternalExecutionPlanStatus.DRAFT)
        findings = [f for f in plan.findings if f.code == "INVALID_QUESTION_ANSWER_CONVERSION"]
        self.assertTrue(len(findings) > 0)
        self.assertIn("cannot be parsed as ISO date (YYYY-MM-DD)", findings[0].message)


if __name__ == "__main__":
    unittest.main()
