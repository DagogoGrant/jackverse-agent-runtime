"""Unit tests for PackageService and ApprovalService orchestration of Phase 5.2.3."""

import unittest

from caseworker.domain.action import Action
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
    ArtifactStatus,
    CaseStatus,
    CaseType,
    ClaimStatus,
    OpportunityStatus,
    OpportunityType,
    PackageVerificationStatus,
    PreparationMaterialType,
    PreparationPackageStatus,
    SensitivityLevel,
    VerificationStatus,
)
from caseworker.domain.errors import (
    ApprovalValidationError,
    DomainValidationError,
    InvalidStateTransitionError,
    PackageVerificationError,
)
from caseworker.domain.opportunity import Opportunity
from caseworker.domain.preparation_analyzer import PreparationAnalyzer
from caseworker.persistence.sqlite import SQLiteCaseworkerStorage
from caseworker.services.approval_service import ApprovalService
from caseworker.services.package_service import PackageService


class TestPackageService(unittest.TestCase):
    def setUp(self) -> None:
        self.storage = SQLiteCaseworkerStorage(":memory:")
        self.package_service = PackageService(self.storage)
        self.approval_service = ApprovalService(self.storage)

        self.user_id = "usr_pack_test"
        self.case_id = "case_pack_test"
        self.opp_id = "opp_pack_test"
        self.set_id = "set_pack_test"

        with self.storage.unit_of_work() as uow:
            self.case = Case(
                case_id=self.case_id,
                user_id=self.user_id,
                title="Principal Staff Pursuit",
                goal="Secure role",
                case_type=CaseType.JOB_APPLICATION,
                status=CaseStatus.PLANNING,
            )
            uow.cases.save(self.case)

            self.opportunity = Opportunity(
                opportunity_id=self.opp_id,
                user_id=self.user_id,
                title="Principal Engineer",
                organization="JackVerse Core",
                opportunity_type=OpportunityType.JOB,
                location="Munich, Germany",
                requirements=["Python architecture (Mandatory)"],
                converted_case_id=self.case_id,
                status=OpportunityStatus.SHORTLISTED,
            )
            uow.opportunities.save(self.opportunity)

            self.fact = ContextFact(
                fact_id="fact-py",
                user_id=self.user_id,
                namespace="skills",
                key="python",
                value="10+ years expert Python architecture",
                sensitivity=SensitivityLevel.PERSONAL,
                verification_status=VerificationStatus.USER_VERIFIED,
                allowed_purposes=["job_application", "case_preparation"],
            )
            uow.context.save(self.fact)

            analyzer = PreparationAnalyzer()
            self.report = analyzer.analyze(
                case=self.case,
                opportunity=self.opportunity,
                granted_facts=[self.fact],
                denied_descriptors=[],
            )
            uow.preparation_reports.save(self.report)
            self.report_id = self.report.report_id

            supp_hash = compute_support_snapshot_hash([self.fact])

            self.claim = Claim(
                claim_id="claim-py",
                user_id=self.user_id,
                purpose="job_application",
                text="Expert Python architecture",
                status=ClaimStatus.SUPPORTED,
                supporting_fact_ids=[self.fact.fact_id],
                case_id=self.case_id,
                source_report_id=self.report_id,
                source_claim_intent_id="intent-1",
                assertion_schema="skill_possession",
                assertion_json='{"kind": "skill_possession", "fact_ids": ["fact-py"], "fields": {"skill": "Python"}}',
                support_snapshot_hash=supp_hash,
                version=1,
            )
            uow.claims.save(self.claim)

            # Artifacts (CV, Cover Letter, Screening Answers)
            cv_grounding = compute_artifact_grounding_hash(claims=[self.claim], direct_facts=[self.fact])
            cl_grounding = compute_artifact_grounding_hash(claims=[], direct_facts=[self.fact])
            qa_grounding = compute_artifact_grounding_hash(claims=[self.claim], direct_facts=[])

            self.cv = PreparedArtifact(
                artifact_id="art-cv",
                artifact_set_id=self.set_id,
                case_id=self.case_id,
                opportunity_id=self.opp_id,
                user_id=self.user_id,
                material_type=PreparationMaterialType.CV,
                title="Staff Resume",
                planner_version=PLANNER_VERSION,
                blocks=[
                    ArtifactBlock(
                        block_id="b-cv-1",
                        block_type="header",
                        content="Profile Summary",
                        grounding_type="context_fact",
                        fact_ids=[self.fact.fact_id],
                    ),
                    ArtifactBlock(
                        block_id="b-cv-2",
                        block_type="bullet",
                        content="Lead developer for JackVerse core",
                        grounding_type="claim",
                        claim_ids=[self.claim.claim_id],
                    ),
                ],
                source_report_id=self.report_id,
                input_hash="hash_cv_inp",
                grounding_snapshot_hash=cv_grounding,
                status=ArtifactStatus.DRAFT,
            )
            uow.preparation_artifacts.save(self.cv)

            self.cl = PreparedArtifact(
                artifact_id="art-cl",
                artifact_set_id=self.set_id,
                case_id=self.case_id,
                opportunity_id=self.opp_id,
                user_id=self.user_id,
                material_type=PreparationMaterialType.COVER_LETTER,
                title="Cover Letter",
                planner_version=PLANNER_VERSION,
                blocks=[
                    ArtifactBlock(
                        block_id="b-cl-1",
                        block_type="paragraph",
                        content="Dear Hiring Manager...",
                        grounding_type="context_fact",
                        fact_ids=[self.fact.fact_id],
                    ),
                ],
                source_report_id=self.report_id,
                input_hash="hash_cl_inp",
                grounding_snapshot_hash=cl_grounding,
                status=ArtifactStatus.DRAFT,
            )
            uow.preparation_artifacts.save(self.cl)

            self.qa = PreparedArtifact(
                artifact_id="art-qa",
                artifact_set_id=self.set_id,
                case_id=self.case_id,
                opportunity_id=self.opp_id,
                user_id=self.user_id,
                material_type=PreparationMaterialType.SCREENING_ANSWERS,
                title="Screening Q&A",
                planner_version=PLANNER_VERSION,
                blocks=[
                    ArtifactBlock(
                        block_id="b-qa-1",
                        block_type="screening_answer",
                        content="Yes, 10 years Python experience.",
                        grounding_type="claim",
                        claim_ids=[self.claim.claim_id],
                        metadata={"answer_status": "COMPLETED", "answer": "Yes, 10 years Python experience."},
                    ),
                ],
                source_report_id=self.report_id,
                input_hash="hash_qa_inp",
                grounding_snapshot_hash=qa_grounding,
                status=ArtifactStatus.DRAFT,
            )
            uow.preparation_artifacts.save(self.qa)

    def tearDown(self) -> None:
        self.storage.close()

    def test_verify_and_prepare_package_happy_path(self) -> None:
        """Amendment 1, 2, 3, 12: Verify package freezes artifacts, creates package & action, updates case."""
        package, action, approval, findings, case = self.package_service.verify_and_prepare_package(
            user_id=self.user_id,
            case_id=self.case_id,
            artifact_set_id=self.set_id,
            expected_case_version=1,
        )

        self.assertIsNotNone(package)
        self.assertEqual(package.status, PreparationPackageStatus.FROZEN)
        self.assertEqual(package.verification_status, PackageVerificationStatus.VERIFIED)
        self.assertEqual(len(package.artifact_ids), 3)

        # Action verification
        self.assertIsNotNone(action)
        self.assertEqual(action.action_type, ActionType.APPROVE_PREPARATION_PACKAGE)
        self.assertEqual(action.status, ActionStatus.AWAITING_APPROVAL)
        self.assertEqual(action.idempotency_key, f"pkg_act:{package.package_fingerprint}")

        # Approval verification
        self.assertIsNotNone(approval)
        self.assertEqual(approval.status, ApprovalStatus.PENDING)

        # Returned case verification
        self.assertIsNotNone(case)
        self.assertEqual(case.status, CaseStatus.AWAITING_APPROVAL)

        # Database state: Artifacts must be FROZEN with incremented version
        with self.storage.unit_of_work() as uow:
            cv_db = uow.preparation_artifacts.get_by_id(self.cv.artifact_id)
            assert cv_db is not None
            self.assertEqual(cv_db.status, ArtifactStatus.FROZEN)
            self.assertEqual(cv_db.version, 2)

            cl_db = uow.preparation_artifacts.get_by_id(self.cl.artifact_id)
            assert cl_db is not None
            self.assertEqual(cl_db.status, ArtifactStatus.FROZEN)
            self.assertEqual(cl_db.version, 2)

            # Case status must be AWAITING_APPROVAL
            case_db = uow.cases.get_by_id(self.case_id)
            assert case_db is not None
            self.assertEqual(case_db.status, CaseStatus.AWAITING_APPROVAL)

            # Action audit lineage: verify action.proposed (v1) and action.status_changed (v2) events
            action_events = uow.events.get_events_for_aggregate("action", action.action_id)
            event_types = [e.event_type for e in action_events]
            self.assertIn("action.proposed", event_types)
            self.assertIn("action.status_changed", event_types)

    def test_verify_and_prepare_package_idempotent_replay(self) -> None:
        """Amendment 2: Calling verify on an already packaged set returns the existing package."""
        pkg1, act1, app1, _, c1 = self.package_service.verify_and_prepare_package(
            user_id=self.user_id,
            case_id=self.case_id,
            artifact_set_id=self.set_id,
            expected_case_version=1,
        )

        # Fetch current case version
        with self.storage.unit_of_work() as uow:
            case_db = uow.cases.get_by_id(self.case_id)
            assert case_db is not None
            current_ver = case_db.version

        # Second call
        pkg2, act2, app2, _, c2 = self.package_service.verify_and_prepare_package(
            user_id=self.user_id,
            case_id=self.case_id,
            artifact_set_id=self.set_id,
            expected_case_version=current_ver,
        )

        self.assertEqual(pkg1.package_id, pkg2.package_id)
        self.assertEqual(pkg1.package_fingerprint, pkg2.package_fingerprint)
        self.assertEqual(act1.action_id, act2.action_id)
        self.assertEqual(app1.approval_id, app2.approval_id)
        self.assertEqual(c1.case_id, c2.case_id)
        self.assertEqual(c1.version, c2.version)

    def test_verify_and_prepare_package_side_effect_free_failure(self) -> None:
        """Amendment 5: Check failure raises PackageVerificationError with zero side effects."""
        # Unresolve screening question
        with self.storage.unit_of_work() as uow:
            qa_art = uow.preparation_artifacts.get_by_id(self.qa.artifact_id)
            assert qa_art is not None
            qa_art.blocks[0].metadata = {"answer_status": "NEEDS_USER_INPUT", "question": "Clearance?"}
            qa_art.version = 2
            uow.preparation_artifacts.save(qa_art)

        with self.assertRaises(PackageVerificationError) as ctx:
            self.package_service.verify_and_prepare_package(
                user_id=self.user_id,
                case_id=self.case_id,
                artifact_set_id=self.set_id,
                expected_case_version=1,
            )

        self.assertTrue(
            any(
                getattr(f, "check_name", f.get("check_name") if isinstance(f, dict) else "") == "questionnaire_completeness"
                for f in ctx.exception.findings
            )
        )

        # Verify zero side-effects in DB
        with self.storage.unit_of_work() as uow:
            # No package created
            pkgs = uow.preparation_packages.list_for_case(self.case_id)
            self.assertEqual(len(pkgs), 0)

            # Artifacts remain DRAFT
            cv_db = uow.preparation_artifacts.get_by_id(self.cv.artifact_id)
            assert cv_db is not None
            self.assertEqual(cv_db.status, ArtifactStatus.DRAFT)

            # Case remains in PLANNING
            case_db = uow.cases.get_by_id(self.case_id)
            assert case_db is not None
            self.assertEqual(case_db.status, CaseStatus.PLANNING)

    def test_approval_service_approve_preparation_package_happy_path(self) -> None:
        """Amendment 8: Approve action completes locally: Action SUCCEEDED, Package APPROVED, Case ACTION_REQUIRED."""
        package, action, approval, _, _ = self.package_service.verify_and_prepare_package(
            user_id=self.user_id,
            case_id=self.case_id,
            artifact_set_id=self.set_id,
            expected_case_version=1,
        )

        app_res, act_res = self.approval_service.approve_action(
            user_id=self.user_id,
            approval_id=approval.approval_id,
            reason="User approves local application package",
        )

        self.assertEqual(app_res.status, ApprovalStatus.APPROVED)
        self.assertEqual(act_res.status, ActionStatus.SUCCEEDED)

        with self.storage.unit_of_work() as uow:
            pkg_db = uow.preparation_packages.get_by_id(package.package_id)
            assert pkg_db is not None
            self.assertEqual(pkg_db.status, PreparationPackageStatus.APPROVED)

            case_db = uow.cases.get_by_id(self.case_id)
            assert case_db is not None
            self.assertEqual(case_db.status, CaseStatus.ACTION_REQUIRED)

    def test_approval_service_approve_fails_and_unwinds_when_dependency_stale(self) -> None:
        """Amendment 6 & 7: Approval-time revalidation failure unwinds package, artifacts, action, and case."""
        package, action, approval, _, _ = self.package_service.verify_and_prepare_package(
            user_id=self.user_id,
            case_id=self.case_id,
            artifact_set_id=self.set_id,
            expected_case_version=1,
        )

        # Simulate dependency invalidation: add a newer intelligence report
        with self.storage.unit_of_work() as uow:
            analyzer = PreparationAnalyzer()
            newer_report = analyzer.analyze(
                case=self.case,
                opportunity=self.opportunity,
                granted_facts=[],
                denied_descriptors=[],
            )
            uow.preparation_reports.save(newer_report)

        # Attempt to approve
        with self.assertRaises(ApprovalValidationError):
            self.approval_service.approve_action(
                user_id=self.user_id,
                approval_id=approval.approval_id,
                reason="Approving stale package",
            )

        # Verify unwind state
        with self.storage.unit_of_work() as uow:
            pkg_db = uow.preparation_packages.get_by_id(package.package_id)
            assert pkg_db is not None
            self.assertEqual(pkg_db.status, PreparationPackageStatus.SUPERSEDED)

            cv_db = uow.preparation_artifacts.get_by_id(self.cv.artifact_id)
            assert cv_db is not None
            self.assertEqual(cv_db.status, ArtifactStatus.SUPERSEDED)

            act_db = uow.actions.get_by_id(action.action_id)
            assert act_db is not None
            self.assertEqual(act_db.status, ActionStatus.CANCELLED)

            app_db = uow.approvals.get_by_id(approval.approval_id)
            assert app_db is not None
            self.assertEqual(app_db.status, ApprovalStatus.CANCELLED)

            case_db = uow.cases.get_by_id(self.case_id)
            assert case_db is not None
            self.assertEqual(case_db.status, CaseStatus.PLANNING)

    def test_approval_service_reject_preparation_package_unwind(self) -> None:
        """Amendment 9: Rejection unwinds package to REJECTED, artifacts to SUPERSEDED, case to PLANNING."""
        package, action, approval, _, _ = self.package_service.verify_and_prepare_package(
            user_id=self.user_id,
            case_id=self.case_id,
            artifact_set_id=self.set_id,
            expected_case_version=1,
        )

        app_res, act_res = self.approval_service.reject_action(
            user_id=self.user_id,
            approval_id=approval.approval_id,
            reason="User decided not to apply with these materials",
        )

        self.assertEqual(app_res.status, ApprovalStatus.REJECTED)
        self.assertEqual(act_res.status, ActionStatus.REJECTED)

        with self.storage.unit_of_work() as uow:
            pkg_db = uow.preparation_packages.get_by_id(package.package_id)
            assert pkg_db is not None
            self.assertEqual(pkg_db.status, PreparationPackageStatus.REJECTED)

            cv_db = uow.preparation_artifacts.get_by_id(self.cv.artifact_id)
            assert cv_db is not None
            self.assertEqual(cv_db.status, ArtifactStatus.SUPERSEDED)

            case_db = uow.cases.get_by_id(self.case_id)
            assert case_db is not None
            self.assertEqual(case_db.status, CaseStatus.PLANNING)

    def test_strict_planning_gate_enforced(self) -> None:
        """Case must be in PLANNING status for new verification to start."""
        with self.storage.unit_of_work() as uow:
            c = uow.cases.get_by_id(self.case_id)
            assert c is not None
            c.status = CaseStatus.ACTION_REQUIRED
            c.version = 2
            uow.cases.save(c)

        with self.assertRaises(InvalidStateTransitionError) as ctx:
            self.package_service.verify_and_prepare_package(
                user_id=self.user_id,
                case_id=self.case_id,
                artifact_set_id=self.set_id,
                expected_case_version=2,
            )
        self.assertIn("can only begin from PLANNING status", str(ctx.exception))

    def test_candidate_artifact_tenant_and_case_isolation(self) -> None:
        """Candidate artifacts must match the user_id and case_id of the case."""
        # Mutate artifact user_id
        with self.storage.unit_of_work() as uow:
            cv_art = uow.preparation_artifacts.get_by_id(self.cv.artifact_id)
            assert cv_art is not None
            cv_art.user_id = "alien_user"
            cv_art.version = 2
            uow.preparation_artifacts.save(cv_art)

        with self.assertRaises(PackageVerificationError) as ctx:
            self.package_service.verify_and_prepare_package(
                user_id=self.user_id,
                case_id=self.case_id,
                artifact_set_id=self.set_id,
                expected_case_version=1,
            )
        self.assertIn("tenant/case isolation", str(ctx.exception))

    def test_get_existing_package_bundle(self) -> None:
        """get_existing_package_bundle returns None when unverified, and bundle when verified."""
        # Before verification: returns None
        self.assertIsNone(
            self.package_service.get_existing_package_bundle(
                user_id=self.user_id,
                case_id=self.case_id,
                artifact_set_id=self.set_id,
            )
        )

        pkg, act, appr, findings, case = self.package_service.verify_and_prepare_package(
            user_id=self.user_id,
            case_id=self.case_id,
            artifact_set_id=self.set_id,
            expected_case_version=1,
        )

        bundle = self.package_service.get_existing_package_bundle(
            user_id=self.user_id,
            case_id=self.case_id,
            artifact_set_id=self.set_id,
        )
        self.assertIsNotNone(bundle)
        assert bundle is not None
        b_pkg, b_act, b_appr, b_findings, b_case = bundle
        self.assertEqual(b_pkg.package_id, pkg.package_id)
        self.assertEqual(b_act.action_id, act.action_id)
        self.assertEqual(b_appr.approval_id, appr.approval_id)
        self.assertEqual(b_case.case_id, case.case_id)

    def test_approval_service_semantic_binding_failure(self) -> None:
        """Check 5: Semantic binding failure rejects approval if action parameters are tampered."""
        package, action, approval, _, _ = self.package_service.verify_and_prepare_package(
            user_id=self.user_id,
            case_id=self.case_id,
            artifact_set_id=self.set_id,
            expected_case_version=1,
        )

        # Tamper with action parameters package_fingerprint
        with self.storage.unit_of_work() as uow:
            act = uow.actions.get_by_id(action.action_id)
            assert act is not None
            act.parameters["package_fingerprint"] = "tampered_fingerprint"
            act.version = 3
            uow.actions.save(act)

        with self.assertRaises(ApprovalValidationError) as ctx:
            self.approval_service.approve_action(
                user_id=self.user_id,
                approval_id=approval.approval_id,
                reason="Approving tampered action",
            )
        self.assertIn("semantic binding", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
