"""Unit tests for Phase 5.2.3 Preparation Package domain and verifier."""

import unittest
from datetime import datetime

from caseworker.domain.artifact import (
    ArtifactBlock,
    ArtifactGroundingType,
    PreparedArtifact,
    compute_artifact_grounding_hash,
)
from caseworker.domain.artifact_planner import PLANNER_VERSION
from caseworker.domain.claim import Claim, compute_support_snapshot_hash
from caseworker.domain.context import ContextFact
from caseworker.domain.enums import (
    ArtifactPublicationRole,
    ArtifactStatus,
    ClaimStatus,
    PackageVerificationStatus,
    PreparationMaterialType,
    PreparationPackageStatus,
    SensitivityLevel,
    VerificationStatus,
)
from caseworker.domain.errors import DomainValidationError, InvalidStateTransitionError
from caseworker.domain.package import (
    ArtifactPackageSnapshot,
    PreparationPackage,
    SafeClaimSnapshot,
    compute_package_fingerprint,
)
from caseworker.domain.package_verifier import PackageVerificationEngine
from caseworker.domain.preparation import PreparationIntelligenceReport, PreparationStrategy


class TestPreparationPackageDomain(unittest.TestCase):
    """Test PreparationPackage dataclass, content-addressed fingerprint, and immutability."""

    def setUp(self) -> None:
        self.art_snap = ArtifactPackageSnapshot(
            artifact_id="art-1",
            artifact_version=2,
            material_type=PreparationMaterialType.CV.value,
            rendered_payload_hash="payload_hash_1",
            grounding_snapshot_hash="grounding_hash_1",
            publication_role=ArtifactPublicationRole.SUBMISSION_MATERIAL.value,
        )
        self.claim_snap = SafeClaimSnapshot(
            claim_id="claim-1",
            claim_version=2,
            claim_status=ClaimStatus.SUPPORTED.value,
            support_snapshot_hash="supp_hash_1",
            assertion_hash="assert_hash_1",
        )

    def test_content_addressed_fingerprint_deterministic_and_excludes_package_id(self) -> None:
        """Amendment 1: Fingerprint is content-addressed, not instance-addressed."""
        fp1 = compute_package_fingerprint(
            case_id="case-100",
            artifact_set_id="set-200",
            source_report_id="rep-300",
            artifact_snapshots=[self.art_snap],
            claim_snapshots=[self.claim_snap],
        )

        fp2 = compute_package_fingerprint(
            case_id="case-100",
            artifact_set_id="set-200",
            source_report_id="rep-300",
            artifact_snapshots=[self.art_snap],
            claim_snapshots=[self.claim_snap],
        )

        self.assertEqual(fp1, fp2)

        # Create two packages with different package_id, status, version, timestamps
        pkg1 = PreparationPackage(
            package_id="pkg_alpha",
            case_id="case-100",
            artifact_set_id="set-200",
            source_report_id="rep-300",
            user_id="user-1",
            status=PreparationPackageStatus.FROZEN,
            artifact_ids=["art-1"],
            artifact_versions={"art-1": 2},
            artifact_snapshots=[self.art_snap],
            claim_snapshots=[self.claim_snap],
            rendered_payload_hashes={"art-1": "payload_hash_1"},
            version=1,
        )

        pkg2 = PreparationPackage(
            package_id="pkg_beta",
            case_id="case-100",
            artifact_set_id="set-200",
            source_report_id="rep-300",
            user_id="user-1",
            status=PreparationPackageStatus.APPROVED,
            artifact_ids=["art-1"],
            artifact_versions={"art-1": 2},
            artifact_snapshots=[self.art_snap],
            claim_snapshots=[self.claim_snap],
            rendered_payload_hashes={"art-1": "payload_hash_1"},
            version=5,
        )

        self.assertEqual(pkg1.package_fingerprint, pkg2.package_fingerprint)
        self.assertEqual(pkg1.package_fingerprint, fp1)

    def test_fingerprint_changes_when_content_changes(self) -> None:
        """Fingerprint changes when any content field is altered."""
        fp_baseline = compute_package_fingerprint(
            case_id="case-100",
            artifact_set_id="set-200",
            source_report_id="rep-300",
            artifact_snapshots=[self.art_snap],
            claim_snapshots=[self.claim_snap],
        )

        # Mutate artifact version in snapshot
        altered_art = ArtifactPackageSnapshot(
            artifact_id="art-1",
            artifact_version=3,
            material_type=PreparationMaterialType.CV.value,
            rendered_payload_hash="payload_hash_1",
            grounding_snapshot_hash="grounding_hash_1",
            publication_role=ArtifactPublicationRole.SUBMISSION_MATERIAL.value,
        )
        fp_altered = compute_package_fingerprint(
            case_id="case-100",
            artifact_set_id="set-200",
            source_report_id="rep-300",
            artifact_snapshots=[altered_art],
            claim_snapshots=[self.claim_snap],
        )
        self.assertNotEqual(fp_baseline, fp_altered)

    def test_immutability_assertion(self) -> None:
        """Amendment 4: PreparationPackage rejects mutation of content-addressed fields."""
        pkg1 = PreparationPackage(
            package_id="pkg-1",
            case_id="case-1",
            artifact_set_id="set-1",
            source_report_id="rep-1",
            user_id="user-1",
            status=PreparationPackageStatus.FROZEN,
            artifact_ids=["art-1"],
            artifact_versions={"art-1": 2},
            artifact_snapshots=[self.art_snap],
            claim_snapshots=[self.claim_snap],
            rendered_payload_hashes={"art-1": "payload_hash_1"},
        )

        pkg_clone = PreparationPackage.from_dict(pkg1.to_dict())
        # Identical matches OK
        pkg1.assert_immutable_content_matches(pkg_clone)

        # Clone with modified artifact_set_id
        data_altered = pkg1.to_dict()
        data_altered["artifact_set_id"] = "set-MUTATED"
        data_altered["package_fingerprint"] = ""  # allow recomputation
        pkg_altered = PreparationPackage.from_dict(data_altered)

        with self.assertRaises(DomainValidationError):
            pkg1.assert_immutable_content_matches(pkg_altered)

    def test_lifecycle_transitions(self) -> None:
        """Test valid and invalid transitions on PreparationPackage."""
        pkg = PreparationPackage(
            package_id="pkg-1",
            case_id="case-1",
            artifact_set_id="set-1",
            source_report_id="rep-1",
            user_id="user-1",
            status=PreparationPackageStatus.FROZEN,
            artifact_ids=["art-1"],
            artifact_versions={"art-1": 2},
            artifact_snapshots=[self.art_snap],
            claim_snapshots=[self.claim_snap],
            rendered_payload_hashes={"art-1": "payload_hash_1"},
        )
        self.assertEqual(pkg.version, 1)

        pkg.approve(reason="Approved by user")
        self.assertEqual(pkg.status, PreparationPackageStatus.APPROVED)
        self.assertEqual(pkg.version, 2)

        # Terminal state - cannot transition out of APPROVED
        with self.assertRaises(InvalidStateTransitionError):
            pkg.reject("Try to reject approved")

        # Can supersede from approved
        pkg.mark_superseded("Superceded by newer suite")
        self.assertEqual(pkg.status, PreparationPackageStatus.SUPERSEDED)

        # Terminal state - cannot transition from superseded
        with self.assertRaises(InvalidStateTransitionError):
            pkg.approve("Try to approve superseded")


class TestPackageVerificationEngine(unittest.TestCase):
    """Test the 7 strict deterministic checks in PackageVerificationEngine."""

    def setUp(self) -> None:
        self.user_id = "user-test"
        self.case_id = "case-test"
        self.set_id = "set-test"
        self.report_id = "rep-test"

        self.report = PreparationIntelligenceReport(
            report_id=self.report_id,
            case_id=self.case_id,
            opportunity_id="opp-test",
            user_id=self.user_id,
            strategy=PreparationStrategy(
                recommended_materials=["cv", "cover_letter", "screening_answers"],
                strengths=[],
                mitigations=[],
                claim_intents=[],
                materials_checklist=[],
            ),
            input_hash="input_hash_val",
            context_snapshot_hash="context_hash_val",
            source_evaluation_id="eval-1",
        )

        self.fact = ContextFact(
            fact_id="fact-1",
            user_id=self.user_id,
            namespace="skills",
            key="python",
            value="Expert Python developer",
            sensitivity=SensitivityLevel.PERSONAL,
            verification_status=VerificationStatus.USER_VERIFIED,
            allowed_purposes=["job_application", "case_preparation"],
        )

        supp_hash = compute_support_snapshot_hash([self.fact])

        self.claim = Claim(
            claim_id="claim-1",
            user_id=self.user_id,
            purpose="job_application",
            text="Expert Python developer",
            status=ClaimStatus.SUPPORTED,
            supporting_fact_ids=["fact-1"],
            case_id=self.case_id,
            source_report_id=self.report_id,
            source_claim_intent_id="intent-1",
            assertion_schema="skill_possession",
            assertion_json='{"kind": "skill_possession", "fact_ids": ["fact-1"], "fields": {"skill": "Python"}}',
            support_snapshot_hash=supp_hash,
            version=1,
        )

        cv_grounding_hash = compute_artifact_grounding_hash(claims=[self.claim], direct_facts=[self.fact])
        cl_grounding_hash = compute_artifact_grounding_hash(claims=[], direct_facts=[self.fact])
        qa_grounding_hash = compute_artifact_grounding_hash(claims=[self.claim], direct_facts=[])

        # CV Artifact
        self.cv = PreparedArtifact(
            artifact_id="art-cv",
            artifact_set_id=self.set_id,
            case_id=self.case_id,
            opportunity_id="opp-test",
            user_id=self.user_id,
            material_type=PreparationMaterialType.CV,
            title="Tailored Resume",
            planner_version=PLANNER_VERSION,
            blocks=[
                ArtifactBlock(
                    block_id="b-1",
                    block_type="header",
                    content="Resume Title",
                    grounding_type="context_fact",
                    fact_ids=["fact-1"],
                ),
                ArtifactBlock(
                    block_id="b-2",
                    block_type="bullet",
                    content="10 years of Python",
                    grounding_type="claim",
                    claim_ids=["claim-1"],
                ),
            ],
            source_report_id=self.report_id,
            input_hash="inp_1",
            grounding_snapshot_hash=cv_grounding_hash,
            status=ArtifactStatus.DRAFT,
        )

        # Cover Letter Artifact
        self.cl = PreparedArtifact(
            artifact_id="art-cl",
            artifact_set_id=self.set_id,
            case_id=self.case_id,
            opportunity_id="opp-test",
            user_id=self.user_id,
            material_type=PreparationMaterialType.COVER_LETTER,
            title="Tailored Cover Letter",
            planner_version=PLANNER_VERSION,
            blocks=[
                ArtifactBlock(
                    block_id="b-3",
                    block_type="paragraph",
                    content="Dear Hiring Manager...",
                    grounding_type="context_fact",
                    fact_ids=["fact-1"],
                ),
            ],
            source_report_id=self.report_id,
            input_hash="inp_1",
            grounding_snapshot_hash=cl_grounding_hash,
            status=ArtifactStatus.DRAFT,
        )

        # Screening Answers Artifact (fully answered)
        self.qa = PreparedArtifact(
            artifact_id="art-qa",
            artifact_set_id=self.set_id,
            case_id=self.case_id,
            opportunity_id="opp-test",
            user_id=self.user_id,
            material_type=PreparationMaterialType.SCREENING_ANSWERS,
            title="Screening Answers",
            planner_version=PLANNER_VERSION,
            blocks=[
                ArtifactBlock(
                    block_id="b-4",
                    block_type="screening_answer",
                    content="Yes, I have Python experience.",
                    grounding_type="claim",
                    claim_ids=["claim-1"],
                    metadata={"answer_status": "COMPLETED", "answer": "Yes, I have Python experience."},
                ),
            ],
            source_report_id=self.report_id,
            input_hash="inp_1",
            grounding_snapshot_hash=qa_grounding_hash,
            status=ArtifactStatus.DRAFT,
        )

    def test_all_checks_pass_on_valid_suite(self) -> None:
        """Verify that a complete, fresh, substantiated suite passes all 7 checks."""
        artifacts = [self.cv, self.cl, self.qa]
        result = PackageVerificationEngine.verify(
            case_id=self.case_id,
            artifact_set_id=self.set_id,
            artifacts=artifacts,
            latest_report=self.report,
            is_report_stale=False,
            claims_by_id={self.claim.claim_id: self.claim},
            facts_by_id={self.fact.fact_id: self.fact},
            allow_frozen_artifacts=False,
        )
        self.assertTrue(result.passed, f"Verification failed with findings: {[f.to_dict() for f in result.findings if not f.passed]}")
        self.assertEqual(result.status, PackageVerificationStatus.VERIFIED)
        self.assertEqual(len(result.artifact_snapshots), 3)
        self.assertEqual(len(result.claim_snapshots), 1)

    def test_screening_answers_unresolved_blocks_verification(self) -> None:
        """Amendment 10: Unresolved question in SCREENING_ANSWERS fails verification."""
        unresolved_qa = PreparedArtifact(
            artifact_id="art-qa",
            artifact_set_id=self.set_id,
            case_id=self.case_id,
            opportunity_id="opp-test",
            user_id=self.user_id,
            material_type=PreparationMaterialType.SCREENING_ANSWERS,
            title="Screening Answers",
            planner_version=PLANNER_VERSION,
            blocks=[
                ArtifactBlock(
                    block_id="b-4",
                    block_type="screening_answer",
                    content="[PENDING_USER_INPUT: Do you have a security clearance?]",
                    grounding_type="claim",
                    claim_ids=["claim-1"],
                    metadata={"answer_status": "NEEDS_USER_INPUT", "question": "Do you have clearance?"},
                ),
            ],
            source_report_id=self.report_id,
            input_hash="inp_1",
            grounding_snapshot_hash="",
            status=ArtifactStatus.DRAFT,
        )

        result = PackageVerificationEngine.verify(
            case_id=self.case_id,
            artifact_set_id=self.set_id,
            artifacts=[self.cv, self.cl, unresolved_qa],
            latest_report=self.report,
            is_report_stale=False,
            claims_by_id={self.claim.claim_id: self.claim},
            facts_by_id={self.fact.fact_id: self.fact},
        )
        self.assertFalse(result.passed)
        self.assertEqual(result.status, PackageVerificationStatus.FAILED)
        findings = {f.check_name: f for f in result.findings}
        self.assertFalse(findings["questionnaire_completeness"].passed)
        self.assertEqual(findings["questionnaire_completeness"].severity, "error")

    def test_application_answer_bank_unresolved_yields_warning_only(self) -> None:
        """Amendment 10: Unresolved question in APPLICATION_ANSWER_BANK is warning, not error."""
        unresolved_bank = PreparedArtifact(
            artifact_id="art-bank",
            artifact_set_id=self.set_id,
            case_id=self.case_id,
            opportunity_id="opp-test",
            user_id=self.user_id,
            material_type=PreparationMaterialType.APPLICATION_ANSWER_BANK,
            title="Answer Bank",
            planner_version=PLANNER_VERSION,
            blocks=[
                ArtifactBlock(
                    block_id="b-5",
                    block_type="screening_answer",
                    content="Optional question not yet answered",
                    grounding_type="context_fact",
                    fact_ids=["fact-1"],
                    metadata={"answer_status": "NEEDS_USER_INPUT", "question": "Do you have clearance?"},
                ),
            ],
            source_report_id=self.report_id,
            input_hash="inp_1",
            grounding_snapshot_hash="",
            status=ArtifactStatus.DRAFT,
        )

        result = PackageVerificationEngine.verify(
            case_id=self.case_id,
            artifact_set_id=self.set_id,
            artifacts=[self.cv, self.cl, unresolved_bank],
            latest_report=self.report,
            is_report_stale=False,
            claims_by_id={self.claim.claim_id: self.claim},
            facts_by_id={self.fact.fact_id: self.fact},
        )
        # Passed because answer bank is INTERNAL_PREPARATION
        self.assertTrue(result.passed, f"Findings: {[f.to_dict() for f in result.findings]}")
        findings = {f.check_name: f for f in result.findings}
        self.assertTrue(findings["questionnaire_completeness"].passed)
        self.assertEqual(findings["questionnaire_completeness"].severity, "warning")

    def test_missing_cv_blocks_verification(self) -> None:
        """Check 3: Missing CV fails material_set_completeness check."""
        result = PackageVerificationEngine.verify(
            case_id=self.case_id,
            artifact_set_id=self.set_id,
            artifacts=[self.cl, self.qa],  # No CV
            latest_report=self.report,
            is_report_stale=False,
            claims_by_id={self.claim.claim_id: self.claim},
            facts_by_id={self.fact.fact_id: self.fact},
        )
        self.assertFalse(result.passed)
        findings = {f.check_name: f for f in result.findings}
        self.assertFalse(findings["material_set_completeness"].passed)

    def test_duplicate_cv_fails_cardinality_check(self) -> None:
        """Check 3: Exactly 1 CV required; duplicate CV fails."""
        cv2 = PreparedArtifact(
            artifact_id="art-cv-2",
            artifact_set_id=self.set_id,
            case_id=self.case_id,
            opportunity_id="opp-test",
            user_id=self.user_id,
            material_type=PreparationMaterialType.CV,
            title="Second Resume",
            planner_version=PLANNER_VERSION,
            blocks=self.cv.blocks,
            source_report_id=self.report_id,
            input_hash="inp_1",
            grounding_snapshot_hash=self.cv.grounding_snapshot_hash,
            status=ArtifactStatus.DRAFT,
        )
        result = PackageVerificationEngine.verify(
            case_id=self.case_id,
            artifact_set_id=self.set_id,
            artifacts=[self.cv, cv2, self.cl, self.qa],
            latest_report=self.report,
            is_report_stale=False,
            claims_by_id={self.claim.claim_id: self.claim},
            facts_by_id={self.fact.fact_id: self.fact},
        )
        self.assertFalse(result.passed)
        findings = {f.check_name: f for f in result.findings}
        self.assertFalse(findings["material_set_completeness"].passed)
        self.assertIn("Duplicate CV artifacts detected", findings["material_set_completeness"].message)

    def test_duplicate_cover_letter_fails_cardinality_check(self) -> None:
        """Check 3: Exactly 1 Cover Letter required; duplicate Cover Letter fails."""
        cl2 = PreparedArtifact(
            artifact_id="art-cl-2",
            artifact_set_id=self.set_id,
            case_id=self.case_id,
            opportunity_id="opp-test",
            user_id=self.user_id,
            material_type=PreparationMaterialType.COVER_LETTER,
            title="Second Cover Letter",
            planner_version=PLANNER_VERSION,
            blocks=self.cl.blocks,
            source_report_id=self.report_id,
            input_hash="inp_1",
            grounding_snapshot_hash=self.cl.grounding_snapshot_hash,
            status=ArtifactStatus.DRAFT,
        )
        result = PackageVerificationEngine.verify(
            case_id=self.case_id,
            artifact_set_id=self.set_id,
            artifacts=[self.cv, self.cl, cl2, self.qa],
            latest_report=self.report,
            is_report_stale=False,
            claims_by_id={self.claim.claim_id: self.claim},
            facts_by_id={self.fact.fact_id: self.fact},
        )
        self.assertFalse(result.passed)
        findings = {f.check_name: f for f in result.findings}
        self.assertFalse(findings["material_set_completeness"].passed)
        self.assertIn("Duplicate COVER_LETTER artifacts detected", findings["material_set_completeness"].message)

    def test_both_screening_answers_and_answer_bank_fails_cardinality_check(self) -> None:
        """Check 3: Mutually exclusive Q&A; cannot include both screening answers and answer bank."""
        bank = PreparedArtifact(
            artifact_id="art-bank",
            artifact_set_id=self.set_id,
            case_id=self.case_id,
            opportunity_id="opp-test",
            user_id=self.user_id,
            material_type=PreparationMaterialType.APPLICATION_ANSWER_BANK,
            title="Answer Bank",
            planner_version=PLANNER_VERSION,
            blocks=self.qa.blocks,
            source_report_id=self.report_id,
            input_hash="inp_1",
            grounding_snapshot_hash=self.qa.grounding_snapshot_hash,
            status=ArtifactStatus.DRAFT,
        )
        result = PackageVerificationEngine.verify(
            case_id=self.case_id,
            artifact_set_id=self.set_id,
            artifacts=[self.cv, self.cl, self.qa, bank],
            latest_report=self.report,
            is_report_stale=False,
            claims_by_id={self.claim.claim_id: self.claim},
            facts_by_id={self.fact.fact_id: self.fact},
        )
        self.assertFalse(result.passed)
        findings = {f.check_name: f for f in result.findings}
        self.assertFalse(findings["material_set_completeness"].passed)
        self.assertIn("Conflicting question materials", findings["material_set_completeness"].message)


if __name__ == "__main__":
    unittest.main()

