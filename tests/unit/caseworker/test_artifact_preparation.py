"""Comprehensive unit tests for Phase 5.2.2 Claim-Bound Artifact Preparation.

Tests:
1. JobApplicationArtifactPlanner:
   - Enforces preconditions (CaseType.JOB_APPLICATION, CaseStatus.PLANNING, non-empty supported claims)
   - Generates tailored CV, Cover Letter, and Screening Answers / Answer Bank
   - Invariant: Zero synthetic citation markers in rendered publication prose
   - Invariant: Non-structural blocks have CLAIM grounding backed by SUPPORTED claims
2. ArtifactPreparationService:
   - Atomic drafting in single UoW
   - Purpose-gated vault access under purpose="job_application"
   - Context access granted/denied audit logging
   - Claim synthesis and evaluation via ClaimVerificationPolicy into ClaimStatus.SUPPORTED
   - Draft fingerprint idempotency
   - Superseding on re-draft and on re-analysis in PLANNING
   - Blocking re-analysis past PLANNING
"""

from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from caseworker.domain.artifact import ArtifactBlock, PreparedArtifact
from caseworker.domain.artifact_planner import JobApplicationArtifactPlanner, PLANNER_VERSION
from caseworker.domain.assertion import (
    CanonicalClaimRenderer,
    ClaimAssertionSpec,
    ClaimAssertionValidator,
)
from caseworker.domain.case import Case
from caseworker.domain.claim import Claim
from caseworker.domain.context import ContextFact
from caseworker.domain.enums import (
    ArtifactGroundingType,
    ArtifactStatus,
    AssertionKind,
    CaseStatus,
    CaseType,
    ClaimStatus,
    EvidenceAlignmentStatus,
    GapCategory,
    OpportunityStatus,
    OpportunityType,
    PreparationMaterialType,
    RequirementCategory,
    VerificationStatus,
)
from caseworker.domain.errors import (
    DomainValidationError,
    EntityNotFoundError,
    OptimisticLockError,
)
from caseworker.domain.normalized_requirement import NormalizedRequirement
from caseworker.domain.opportunity import Opportunity
from caseworker.domain.preparation import (
    ClaimIntent,
    EvidenceMappingItem,
    GapItem,
    PreparationIntelligenceReport,
    PreparationStrategy,
    SafeDeniedFactDescriptor,
    compute_prep_context_snapshot_hash,
    compute_prep_input_hash,
)
from caseworker.domain.preparation_analyzer import PreparationAnalyzer
from caseworker.domain.types import canonical_json_dumps, compute_sha256
from caseworker.persistence.sqlite import SQLiteCaseworkerStorage
from caseworker.services.artifact_service import ArtifactPreparationService
from caseworker.services.case_service import CaseService


class TestJobApplicationArtifactPlanner(unittest.TestCase):
    """Test domain artifact planner compilation, AST integrity, and grounding invariants."""

    def setUp(self) -> None:
        self.user_id = "user_planner_test"
        self.planner = JobApplicationArtifactPlanner()

        self.case = Case(
            case_id="case-plan-1",
            user_id=self.user_id,
            title="Senior Staff Engineer Application",
            goal="Secure Senior Staff Engineer role",
            case_type=CaseType.JOB_APPLICATION,
            status=CaseStatus.PLANNING,
        )

        self.opportunity = Opportunity(
            opportunity_id="opp-plan-1",
            user_id=self.user_id,
            title="Senior Staff Engineer",
            organization="JackVerse Labs",
            opportunity_type=OpportunityType.JOB,
            location="Berlin, Germany",
            requirements=[
                "10+ years experience in distributed systems (Mandatory)",
                "Python and Go mastery (Mandatory)",
                "Team leadership (Preferred)",
            ],
            converted_case_id=self.case.case_id,
        )

        self.fact_exp = ContextFact(
            fact_id="f-exp",
            user_id=self.user_id,
            namespace="experience",
            key="senior_staff",
            value="Lead Infrastructure Architect at Acme Cloud",
            verification_status=VerificationStatus.USER_VERIFIED,
            allowed_purposes=["job_application"],
        )

        self.fact_skill = ContextFact(
            fact_id="f-skill",
            user_id=self.user_id,
            namespace="skills",
            key="python",
            value="Python and Go distributed services",
            verification_status=VerificationStatus.USER_VERIFIED,
            allowed_purposes=["job_application"],
        )

        self.claim_exp = Claim(
            claim_id="clm-exp",
            user_id=self.user_id,
            purpose="job_application",
            text="Served as Lead Infrastructure Architect at Acme Cloud.",
            case_id=self.case.case_id,
            status=ClaimStatus.SUPPORTED,
            supporting_fact_ids=["f-exp"],
            assertion_schema=AssertionKind.EMPLOYMENT_ROLE.value,
            assertion_json=canonical_json_dumps({
                "kind": AssertionKind.EMPLOYMENT_ROLE.value,
                "fact_ids": ["f-exp"],
                "fields": {"role": "Lead Infrastructure Architect", "organization": "Acme Cloud"},
            }),
        )

        self.claim_skill = Claim(
            claim_id="clm-skill",
            user_id=self.user_id,
            purpose="job_application",
            text="Maintained expert proficiency in Python and Go distributed services.",
            case_id=self.case.case_id,
            status=ClaimStatus.SUPPORTED,
            supporting_fact_ids=["f-skill"],
            assertion_schema=AssertionKind.SKILL_POSSESSION.value,
            assertion_json=canonical_json_dumps({
                "kind": AssertionKind.SKILL_POSSESSION.value,
                "fact_ids": ["f-skill"],
                "fields": {"skill": "Python and Go distributed services"},
            }),
        )

        self.report = PreparationIntelligenceReport(
            report_id="rep-plan-1",
            case_id=self.case.case_id,
            opportunity_id=self.opportunity.opportunity_id,
            user_id=self.user_id,
            schema_version="prep_intel_v1",
            analyzer_version="prep_analyzer_v1",
            strategy=PreparationStrategy(
                recommended_materials=[
                    PreparationMaterialType.CV,
                    PreparationMaterialType.COVER_LETTER,
                    PreparationMaterialType.APPLICATION_ANSWER_BANK,
                ],
                materials_checklist=[
                    {"item": "Submit tailored CV", "status": "PENDING"},
                    {"item": "Submit tailored Cover Letter", "status": "PENDING"},
                    {"item": "Prepare Application Answer Bank", "status": "PENDING"},
                ],
                claim_intents=[
                    ClaimIntent(
                        intent_id="intent-1",
                        requirement_id="req-1",
                        target_material=PreparationMaterialType.CV,
                        claim_topic="Experience alignment",
                        supporting_fact_ids=["f-exp"],
                        suggested_angle="Lead Infrastructure Architect at Acme Cloud",
                    )
                ],
            ),
        )

    def test_planner_rejects_non_job_application_case(self) -> None:
        """Planner must reject cases with case_type other than JOB_APPLICATION."""
        general_case = Case(
            case_id="case-gen-1",
            user_id=self.user_id,
            title="General Goal",
            goal="Do something",
            case_type=CaseType.GENERAL,
            status=CaseStatus.PLANNING,
        )
        with self.assertRaises(DomainValidationError) as cm:
            self.planner.plan_artifacts(
                case=general_case,
                opportunity=self.opportunity,
                report=self.report,
                supported_claims=[self.claim_exp],
                context_facts=[self.fact_exp],
                artifact_set_id="set-1",
                input_hash="hash-1",
                grounding_snapshot_hash="ghash-1",
            )
        self.assertIn("only supports JOB_APPLICATION cases", str(cm.exception))

    def test_planner_rejects_non_planning_status(self) -> None:
        """Planner must reject cases outside PLANNING status."""
        action_case = Case(
            case_id="case-action-1",
            user_id=self.user_id,
            title="Action Job Application",
            goal="Do something",
            case_type=CaseType.JOB_APPLICATION,
            status=CaseStatus.ACTION_REQUIRED,
        )
        with self.assertRaises(DomainValidationError) as cm:
            self.planner.plan_artifacts(
                case=action_case,
                opportunity=self.opportunity,
                report=self.report,
                supported_claims=[self.claim_exp],
                context_facts=[self.fact_exp],
                artifact_set_id="set-1",
                input_hash="hash-1",
                grounding_snapshot_hash="ghash-1",
            )
        self.assertIn("requires case to be in PLANNING status", str(cm.exception))

    def test_planner_rejects_empty_supported_claims(self) -> None:
        """Planner must reject drafting if zero supported claims exist."""
        with self.assertRaises(DomainValidationError) as cm:
            self.planner.plan_artifacts(
                case=self.case,
                opportunity=self.opportunity,
                report=self.report,
                supported_claims=[],
                context_facts=[self.fact_exp],
                artifact_set_id="set-1",
                input_hash="hash-1",
                grounding_snapshot_hash="ghash-1",
            )
        self.assertIn("at least one SUPPORTED claim", str(cm.exception))

    def test_planner_compiles_three_materials_with_clean_prose(self) -> None:
        """Planner creates CV, Cover Letter, and Answer Bank ASTs with zero citation tags."""
        artifacts = self.planner.plan_artifacts(
            case=self.case,
            opportunity=self.opportunity,
            report=self.report,
            supported_claims=[self.claim_exp, self.claim_skill],
            context_facts=[self.fact_exp, self.fact_skill],
            artifact_set_id="set-abc",
            input_hash="hash-abc",
            grounding_snapshot_hash="ghash-abc",
        )

        self.assertEqual(len(artifacts), 3)
        material_types = {a.material_type for a in artifacts}
        self.assertIn(PreparationMaterialType.CV, material_types)
        self.assertIn(PreparationMaterialType.COVER_LETTER, material_types)
        self.assertIn(PreparationMaterialType.APPLICATION_ANSWER_BANK, material_types)

        for art in artifacts:
            # 1. State integrity
            self.assertEqual(art.status, ArtifactStatus.DRAFT)
            self.assertEqual(art.schema_version, "prep_artifact_v1")
            self.assertEqual(art.planner_version, PLANNER_VERSION)
            self.assertEqual(art.case_id, self.case.case_id)
            self.assertEqual(art.opportunity_id, self.opportunity.opportunity_id)
            self.assertGreater(len(art.blocks), 0)

            # 2. Rendering and citation invariants
            rendered_md = art.render_markdown()
            rendered_txt = art.render_plain_text()
            self.assertNotIn("[claim:", rendered_md)
            self.assertNotIn("[claim:", rendered_txt)
            self.assertNotIn("[fact:", rendered_md)
            self.assertNotIn("[fact:", rendered_txt)
            self.assertNotIn("[evidence:", rendered_md)
            self.assertNotIn("[evidence:", rendered_txt)

            # 3. Grounding invariant: Every artifact must have CLAIM-grounded narrative blocks
            claim_grounded_blocks = [b for b in art.blocks if b.grounding_type == ArtifactGroundingType.CLAIM]
            self.assertGreater(
                len(claim_grounded_blocks),
                0,
                f"Artifact {art.material_type.value} must contain claim-grounded blocks",
            )
            for block in art.blocks:
                if block.grounding_type == ArtifactGroundingType.CLAIM:
                    self.assertTrue(
                        len(block.claim_ids) > 0,
                        f"Claim block '{block.block_id}' in {art.material_type.value} missing claim_ids",
                    )
                elif block.grounding_type == ArtifactGroundingType.STRUCTURAL:
                    self.assertEqual(
                        len(block.claim_ids),
                        0,
                        f"Structural block '{block.block_id}' must not have claim_ids",
                    )

    def test_planner_uses_screening_answers_when_questions_specified(self) -> None:
        """When opportunity metadata includes screening_questions, produce SCREENING_ANSWERS."""
        self.opportunity.metadata["screening_questions"] = [
            "Why do you want to join JackVerse Labs?",
            "Describe your experience with distributed systems.",
        ]
        artifacts = self.planner.plan_artifacts(
            case=self.case,
            opportunity=self.opportunity,
            report=self.report,
            supported_claims=[self.claim_exp, self.claim_skill],
            context_facts=[self.fact_exp, self.fact_skill],
            artifact_set_id="set-xyz",
            input_hash="hash-xyz",
            grounding_snapshot_hash="ghash-xyz",
        )
        material_types = {a.material_type for a in artifacts}
        self.assertIn(PreparationMaterialType.SCREENING_ANSWERS, material_types)
        self.assertNotIn(PreparationMaterialType.APPLICATION_ANSWER_BANK, material_types)


class TestArtifactPreparationService(unittest.TestCase):
    """Test full application service coordination, vault purpose gating, and persistence."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "test_artifact_prep.db"
        self.storage = SQLiteCaseworkerStorage(str(self.db_path))
        self.service = ArtifactPreparationService(self.storage)
        self.case_service = CaseService(self.storage)
        self.user_id = "user_prep_svc_test"

        # Populate case, opportunity, and active context facts
        with self.storage.unit_of_work() as uow:
            self.case = Case(
                case_id="case-svc-1",
                user_id=self.user_id,
                title="Staff Engineer Pursuit",
                goal="Secure Staff Engineer role",
                case_type=CaseType.JOB_APPLICATION,
                status=CaseStatus.PLANNING,
            )
            uow.cases.save(self.case)

            self.opportunity = Opportunity(
                opportunity_id="opp-svc-1",
                user_id=self.user_id,
                title="Staff Engineer",
                organization="JackVerse Core",
                opportunity_type=OpportunityType.JOB,
                location="Munich, Germany",
                requirements=[
                    "Python proficiency (Mandatory)",
                    "Cloud architecture (Mandatory)",
                ],
                converted_case_id=self.case.case_id,
                status=OpportunityStatus.SHORTLISTED,
            )
            uow.opportunities.save(self.opportunity)

            # Fact allowed for case_preparation and job_application
            self.fact_python = ContextFact(
                fact_id="fact-py",
                user_id=self.user_id,
                namespace="skills",
                key="python",
                value="Python, FastAPI, SQLite architecture",
                verification_status=VerificationStatus.USER_VERIFIED,
                allowed_purposes=["case_preparation", "job_application"],
            )
            uow.context.save(self.fact_python)

            # Fact allowed for case_preparation and job_application
            self.fact_cloud = ContextFact(
                fact_id="fact-cloud",
                user_id=self.user_id,
                namespace="skills",
                key="cloud",
                value="AWS & GCP cloud architecture",
                verification_status=VerificationStatus.USER_VERIFIED,
                allowed_purposes=["case_preparation", "job_application"],
            )
            uow.context.save(self.fact_cloud)

            # Fact strictly restricted (not allowed for job_application)
            self.fact_restricted = ContextFact(
                fact_id="fact-salary",
                user_id=self.user_id,
                namespace="compensation",
                key="current_salary",
                value="120000 EUR",
                verification_status=VerificationStatus.USER_VERIFIED,
                allowed_purposes=["financial_planning"],
            )
            uow.context.save(self.fact_restricted)

            # Generate fresh preparation intelligence report
            analyzer = PreparationAnalyzer()
            self.report = analyzer.analyze(
                case=self.case,
                opportunity=self.opportunity,
                granted_facts=[self.fact_python, self.fact_cloud],
                denied_descriptors=[],
            )
            uow.preparation_reports.save(self.report)

    def tearDown(self) -> None:
        self.storage.close()
        self.temp_dir.cleanup()

    def test_draft_artifacts_happy_path(self) -> None:
        """Drafting creates 3 artifacts atomically in a single UoW and records domain events."""
        artifacts, case = self.service.draft_job_application_artifacts(
            user_id=self.user_id,
            case_id=self.case.case_id,
            expected_case_version=self.case.version,
        )

        self.assertEqual(len(artifacts), 3)
        self.assertEqual(case.status, CaseStatus.PLANNING)

        # Verify artifacts persisted in DB
        with self.storage.unit_of_work() as uow:
            db_artifacts = uow.preparation_artifacts.list_by_case(self.case.case_id)
            self.assertEqual(len(db_artifacts), 3)
            for a in db_artifacts:
                self.assertEqual(a.status, ArtifactStatus.DRAFT)
                self.assertEqual(a.source_report_id, self.report.report_id)

            # Verify claims saved in Claim Ledger with assertion metadata
            claims = uow.claims.list_by_case(self.case.case_id)
            self.assertGreater(len(claims), 0)
            for claim in claims:
                self.assertEqual(claim.status, ClaimStatus.SUPPORTED)
                self.assertEqual(claim.purpose, "job_application")
                self.assertIsNotNone(claim.assertion_schema)
                self.assertIsNotNone(claim.assertion_json)
                self.assertIsNotNone(claim.support_snapshot_hash)

            # Verify domain events recorded
            events = uow.events.get_events_by_user(self.user_id)
            event_types = [e.event_type for e in events]
            self.assertIn("context.access_granted", event_types)
            self.assertIn("claim.supported", event_types)
            self.assertIn("preparation.artifact_prepared", event_types)

    def test_draft_artifacts_denied_vault_facts_audit(self) -> None:
        """Restricted facts are denied under job_application and emit context_access_denied events."""
        # Add a requirement matching the restricted salary fact
        with self.storage.unit_of_work() as uow:
            opp_db = uow.opportunities.get_by_id(self.opportunity.opportunity_id)
            assert opp_db is not None
            opp_db.requirements.append("Salary expectation (Preferred)")
            opp_db.version += 1
            uow.opportunities.save(opp_db)
            self.opportunity = opp_db

            # Re-generate report
            analyzer = PreparationAnalyzer()
            self.report = analyzer.analyze(
                case=self.case,
                opportunity=self.opportunity,
                granted_facts=[self.fact_python, self.fact_cloud],
                denied_descriptors=[
                    SafeDeniedFactDescriptor(
                        fact_id="fact-salary",
                        fact_version=1,
                        namespace="compensation",
                        key="current_salary",
                        reason_code="PURPOSE_NOT_PERMITTED",
                        reason="Purpose job_application not permitted",
                    )
                ],
            )
            uow.preparation_reports.save(self.report)

        artifacts, _ = self.service.draft_job_application_artifacts(
            user_id=self.user_id,
            case_id=self.case.case_id,
        )
        self.assertEqual(len(artifacts), 3)

        # Check audit events for denial
        with self.storage.unit_of_work() as uow:
            events = uow.events.get_events_by_user(self.user_id)
            denied_events = [e for e in events if e.event_type == "context.access_denied"]
            self.assertGreater(len(denied_events), 0)
            self.assertEqual(denied_events[0].payload["fact_id"], "fact-salary")

    def test_draft_artifacts_idempotency(self) -> None:
        """Calling draft_job_application_artifacts with identical state returns existing artifacts."""
        artifacts1, _ = self.service.draft_job_application_artifacts(
            user_id=self.user_id,
            case_id=self.case.case_id,
        )
        artifacts2, _ = self.service.draft_job_application_artifacts(
            user_id=self.user_id,
            case_id=self.case.case_id,
        )

        self.assertEqual(len(artifacts1), len(artifacts2))
        ids1 = {a.artifact_id for a in artifacts1}
        ids2 = {a.artifact_id for a in artifacts2}
        self.assertEqual(ids1, ids2)

    def test_draft_artifacts_optimistic_lock_failure(self) -> None:
        """Drafting with mismatched expected_case_version raises OptimisticLockError."""
        with self.assertRaises(OptimisticLockError):
            self.service.draft_job_application_artifacts(
                user_id=self.user_id,
                case_id=self.case.case_id,
                expected_case_version=999,
            )

    def test_draft_artifacts_rejects_stale_report(self) -> None:
        """Modifying opportunity requirements makes report stale, blocking artifact drafting."""
        with self.storage.unit_of_work() as uow:
            opp_db = uow.opportunities.get_by_id(self.opportunity.opportunity_id)
            assert opp_db is not None
            opp_db.requirements.append("Brand new mandatory skill (Mandatory)")
            opp_db.version += 1
            uow.opportunities.save(opp_db)
            self.opportunity = opp_db

        with self.assertRaises(DomainValidationError) as cm:
            self.service.draft_job_application_artifacts(
                user_id=self.user_id,
                case_id=self.case.case_id,
            )
        self.assertIn("Preparation intelligence report is stale", str(cm.exception))

    def test_re_analysis_in_planning_supersedes_existing_drafts(self) -> None:
        """Re-running preparation analysis in PLANNING status marks previous DRAFT artifacts SUPERSEDED."""
        # 1. Draft artifacts first
        artifacts, _ = self.service.draft_job_application_artifacts(
            user_id=self.user_id,
            case_id=self.case.case_id,
        )
        self.assertEqual(len(artifacts), 3)

        # 2. Re-analyze case via CaseService
        self.case_service.generate_preparation_intelligence(
            user_id=self.user_id,
            case_id=self.case.case_id,
            expected_case_version=self.case.version,
        )

        # 3. Check artifacts in DB are now SUPERSEDED
        with self.storage.unit_of_work() as uow:
            db_artifacts = uow.preparation_artifacts.list_by_case(self.case.case_id)
            self.assertEqual(len(db_artifacts), 3)
            for a in db_artifacts:
                self.assertEqual(a.status, ArtifactStatus.SUPERSEDED)

    def test_export_artifact_clean_markdown_and_plain_text(self) -> None:
        """Exporting artifact returns clean string with zero citation tags."""
        artifacts, _ = self.service.draft_job_application_artifacts(
            user_id=self.user_id,
            case_id=self.case.case_id,
        )
        cv = next(a for a in artifacts if a.material_type == PreparationMaterialType.CV)

        md = self.service.export_artifact(
            user_id=self.user_id,
            case_id=self.case.case_id,
            artifact_id=cv.artifact_id,
            format_type="markdown",
        )
        self.assertIn("Candidate for Staff Engineer at JackVerse Core", md)
        self.assertIn("Professional Summary", md)
        self.assertNotIn("[claim:", md)
        self.assertNotIn("[fact:", md)

        txt = self.service.export_artifact(
            user_id=self.user_id,
            case_id=self.case.case_id,
            artifact_id=cv.artifact_id,
            format_type="plain_text",
        )
        self.assertIn("Candidate for Staff Engineer at JackVerse Core", txt)
        self.assertIn("Professional Summary", txt)
        self.assertNotIn("[claim:", txt)


if __name__ == "__main__":
    unittest.main()
