"""Unit tests for Phase 5.2.2.1: Artifact Semantic & Grounding Integrity.

Verifies:
1. Semantic assertion validation: no unproven qualifiers, duration year units, work auth jurisdiction, adverse metric direction.
2. Conservative canonical claim rendering: natural external prose without internal governance terms or invented qualifiers.
3. Side-effect-free GET operations: reading artifacts or checking staleness does not mutate database state.
4. Deterministic screening question intent matching and export rejection for unresolved questions.
5. Strict tailoring boundary: vault facts outside preparation report intents are not synthesized into claims.
6. Complete Claim audit event history: claim.proposed at v1 followed by claim.supported at v2.
7. Privacy-safe hashing: snapshot hashes contain zero raw personal values.
8. Identity grounding: verified context facts for applicant info or non-assertive structural fallback.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from caseworker.domain.artifact import (
    ArtifactBlock,
    PreparedArtifact,
    compute_artifact_grounding_hash,
    compute_artifact_staleness,
)
from caseworker.domain.artifact_planner import JobApplicationArtifactPlanner, PLANNER_VERSION
from caseworker.domain.assertion import (
    CanonicalClaimRenderer,
    ClaimAssertionSpec,
    ClaimAssertionValidator,
)
from caseworker.domain.case import Case
from caseworker.domain.claim import Claim, compute_support_snapshot_hash
from caseworker.domain.context import ContextFact
from caseworker.domain.enums import (
    ArtifactGroundingType,
    ArtifactStatus,
    AssertionKind,
    CaseStatus,
    CaseType,
    ClaimStatus,
    EvidenceAlignmentStatus,
    OpportunityStatus,
    OpportunityType,
    PreparationMaterialType,
    RequirementCategory,
    VerificationStatus,
)
from caseworker.domain.errors import DomainValidationError
from caseworker.domain.opportunity import Opportunity
from caseworker.domain.preparation import (
    ClaimIntent,
    EvidenceMappingItem,
    PreparationIntelligenceReport,
    PreparationStrategy,
)
from caseworker.domain.preparation_analyzer import PreparationAnalyzer
from caseworker.domain.types import now_utc
from caseworker.persistence.sqlite import SQLiteCaseworkerStorage
from caseworker.services.artifact_service import ArtifactPreparationService
from caseworker.services.case_service import CaseService


class TestArtifactSemanticIntegrity(unittest.TestCase):
    """Deep verification of the 8 review amendments for Phase 5.2.2.1."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "caseworker_integrity.db"
        self.storage = SQLiteCaseworkerStorage(str(self.db_path))
        self.service = ArtifactPreparationService(self.storage)
        self.case_service = CaseService(self.storage)

        self.user_id = "user_integrity_1"
        self.now = now_utc()

        # Seed Case and Opportunity
        with self.storage.unit_of_work() as uow:
            self.case = Case(
                case_id="case-int-1",
                user_id=self.user_id,
                title="Distributed Systems Lead Application",
                goal="Obtain Distributed Systems Lead position",
                case_type=CaseType.JOB_APPLICATION,
                status=CaseStatus.PLANNING,
                version=1,
            )
            uow.cases.save(self.case)

            self.opportunity = Opportunity(
                opportunity_id="opp-int-1",
                user_id=self.user_id,
                title="Distributed Systems Lead",
                organization="Acme Cloud",
                opportunity_type=OpportunityType.JOB,
                location="Remote, US",
                status=OpportunityStatus.SHORTLISTED,
                requirements=[
                    "5+ years of experience in distributed systems (Mandatory)",
                    "Python programming (Mandatory)",
                    "US Work Authorization (Mandatory)",
                ],
                converted_case_id=self.case.case_id,
                metadata={
                    "screening_questions": [
                        "Are you legally authorized to work in the United States?",
                        "How many years of distributed systems experience do you have?",
                        "Do you hold an active Top Secret clearance?",
                    ]
                },
            )
            uow.opportunities.save(self.opportunity)

            # Seed Context Facts
            self.f_dur = ContextFact(
                fact_id="fact-dur-1",
                user_id=self.user_id,
                namespace="experience",
                key="systems_years",
                value="7 years of distributed systems engineering",
                verification_status=VerificationStatus.USER_VERIFIED,
                allowed_purposes=["case_preparation", "job_application"],
                created_at=self.now,
                updated_at=self.now,
            )
            self.f_skill = ContextFact(
                fact_id="fact-skill-1",
                user_id=self.user_id,
                namespace="skills",
                key="python",
                value="Python, FastAPI, Docker",
                verification_status=VerificationStatus.USER_VERIFIED,
                allowed_purposes=["case_preparation", "job_application"],
                created_at=self.now,
                updated_at=self.now,
            )
            self.f_auth = ContextFact(
                fact_id="fact-auth-1",
                user_id=self.user_id,
                namespace="work_authorization",
                key="work_permit",
                value="Authorized citizen in United States",
                verification_status=VerificationStatus.USER_VERIFIED,
                allowed_purposes=["case_preparation", "job_application"],
                created_at=self.now,
                updated_at=self.now,
            )
            self.f_unrelated = ContextFact(
                fact_id="fact-unrelated-1",
                user_id=self.user_id,
                namespace="hobbies",
                key="guitar",
                value="Plays acoustic guitar",
                verification_status=VerificationStatus.USER_VERIFIED,
                allowed_purposes=["case_preparation", "job_application"],
                created_at=self.now,
                updated_at=self.now,
            )
            uow.context.save(self.f_dur)
            uow.context.save(self.f_skill)
            uow.context.save(self.f_auth)
            uow.context.save(self.f_unrelated)

            # Generate preparation report using analyzer
            analyzer = PreparationAnalyzer()
            self.report = analyzer.analyze(
                case=self.case,
                opportunity=self.opportunity,
                granted_facts=[self.f_dur, self.f_skill, self.f_auth],
                denied_descriptors=[],
            )
            uow.preparation_reports.save(self.report)

    def tearDown(self) -> None:
        self.storage.close()
        self.temp_dir.cleanup()

    # -------------------------------------------------------------------------
    # 1. Conservative Claim Rendering & Semantic Validator
    # -------------------------------------------------------------------------

    def test_duration_requires_year_units(self) -> None:
        """Duration validator must reject numbers that do not denote years."""
        fact_num = ContextFact(
            fact_id="f-num",
            user_id=self.user_id,
            namespace="experience",
            key="projects",
            value="Delivered 7 high-impact enterprise projects",
            verification_status=VerificationStatus.USER_VERIFIED,
            allowed_purposes=["job_application"],
        )
        spec = ClaimAssertionSpec(
            kind=AssertionKind.EMPLOYMENT_DURATION,
            fact_ids=["f-num"],
            fields={"duration_years": 5},
        )
        valid, err = ClaimAssertionValidator.validate(spec, [fact_num])
        self.assertFalse(valid)
        self.assertIn("not substantiated", err or "")

    def test_work_authorization_jurisdiction_and_sponsorship_guard(self) -> None:
        """Work authorization fails if jurisdiction or lack of sponsorship is not substantiated."""
        # Fact proves US, but spec asks for Germany
        spec_de = ClaimAssertionSpec(
            kind=AssertionKind.WORK_AUTHORIZATION,
            fact_ids=["fact-auth-1"],
            fields={"status": "Authorized citizen", "jurisdiction": "Germany"},
        )
        valid, err = ClaimAssertionValidator.validate(spec_de, [self.f_auth])
        self.assertFalse(valid)
        self.assertIn("jurisdiction 'germany' is not substantiated", (err or "").lower())

        # Fact without sponsorship info cannot claim no_sponsorship_required
        fact_auth_only = ContextFact(
            fact_id="f-auth-simple",
            user_id=self.user_id,
            namespace="authorization",
            key="work",
            value="Authorized to work in United States",
            verification_status=VerificationStatus.USER_VERIFIED,
            allowed_purposes=["job_application"],
        )
        spec_sponsor = ClaimAssertionSpec(
            kind=AssertionKind.WORK_AUTHORIZATION,
            fact_ids=["f-auth-simple"],
            fields={"status": "Authorized citizen", "jurisdiction": "United States", "no_sponsorship_required": True},
        )
        valid_sp, err_sp = ClaimAssertionValidator.validate(spec_sponsor, [fact_auth_only])
        self.assertFalse(valid_sp)
        self.assertIn("sponsorship", (err_sp or "").lower())

    def test_quantified_achievement_rejects_adverse_metric_increase(self) -> None:
        """Claiming an increase in error rate or latency as an improvement must be rejected."""
        fact_err = ContextFact(
            fact_id="f-err",
            user_id=self.user_id,
            namespace="metrics",
            key="error_rate",
            value="Observed 20% error rate increase during deployment",
            verification_status=VerificationStatus.USER_VERIFIED,
            allowed_purposes=["job_application"],
        )
        spec = ClaimAssertionSpec(
            kind=AssertionKind.QUANTIFIED_ACHIEVEMENT,
            fact_ids=["f-err"],
            fields={"metric": 20, "metric_unit": "%", "direction": "improvement"},
        )
        valid, err = ClaimAssertionValidator.validate(spec, [fact_err])
        self.assertFalse(valid)
        self.assertIn("adverse metric increase", (err or "").lower())

    def test_canonical_renderer_zero_invented_qualifiers(self) -> None:
        """Renderer must output conservative natural language without 'verified' or unproven qualifiers."""
        # Skill without production
        spec_skill = ClaimAssertionSpec(
            kind=AssertionKind.SKILL_POSSESSION,
            fact_ids=["fact-skill-1"],
            fields={"skill": "Python"},
        )
        rendered = CanonicalClaimRenderer.render(spec_skill)
        self.assertEqual(rendered, "Experience with Python.")
        self.assertNotIn("production", rendered.lower())
        self.assertNotIn("expertise", rendered.lower())
        self.assertNotIn("verified", rendered.lower())

        # Education
        spec_edu = ClaimAssertionSpec(
            kind=AssertionKind.EDUCATION_CREDENTIAL,
            fact_ids=["fact-1"],
            fields={"degree": "M.S.", "field_of_study": "Computer Science"},
        )
        rendered_edu = CanonicalClaimRenderer.render(spec_edu)
        self.assertEqual(rendered_edu, "Holds a M.S. in Computer Science.")
        self.assertNotIn("verified", rendered_edu.lower())

    # -------------------------------------------------------------------------
    # 2. Side-Effect-Free GET Operations & Staleness
    # -------------------------------------------------------------------------

    def test_side_effect_free_read_and_staleness(self) -> None:
        """GET / is_artifact_stale must not mutate SQLite records."""
        artifacts, _ = self.service.draft_job_application_artifacts(
            user_id=self.user_id,
            case_id=self.case.case_id,
        )
        cv = next(a for a in artifacts if a.material_type == PreparationMaterialType.CV)

        # Baseline DB state
        with self.storage.unit_of_work() as uow:
            initial = uow.preparation_artifacts.get_by_id(cv.artifact_id)
            self.assertIsNotNone(initial)
            assert initial is not None
            self.assertEqual(initial.status, ArtifactStatus.DRAFT)
            self.assertEqual(initial.version, 1)

        # Reading artifact and checking staleness
        stale_bool, reason = self.service.is_artifact_stale(self.user_id, self.case.case_id, cv)
        self.assertFalse(stale_bool)
        self.assertIsNone(reason)

        retrieved = self.service.get_artifact(self.user_id, self.case.case_id, cv.artifact_id)
        self.assertEqual(retrieved.status, ArtifactStatus.DRAFT)

        # DB must still be completely unmutated
        with self.storage.unit_of_work() as uow:
            after_read = uow.preparation_artifacts.get_by_id(cv.artifact_id)
            assert after_read is not None
            self.assertEqual(after_read.status, ArtifactStatus.DRAFT)
            self.assertEqual(after_read.version, 1)

        # Now invalidate a supporting fact in vault
        with self.storage.unit_of_work() as uow:
            self.f_dur.version += 1
            self.f_dur.verification_status = VerificationStatus.UNVERIFIED
            uow.context.save(self.f_dur)

        # is_artifact_stale should dynamically detect it
        is_stale_dyn, reason_dyn = self.service.is_artifact_stale(self.user_id, self.case.case_id, cv)
        self.assertTrue(is_stale_dyn)
        self.assertIsNotNone(reason_dyn)

        # But persisted DB status must still remain DRAFT (GETs never mutate state!)
        with self.storage.unit_of_work() as uow:
            still_draft = uow.preparation_artifacts.get_by_id(cv.artifact_id)
            assert still_draft is not None
            self.assertEqual(still_draft.status, ArtifactStatus.DRAFT)
            self.assertEqual(still_draft.version, 1)

    # -------------------------------------------------------------------------
    # 3. Deterministic Screening Answers & Export Validation
    # -------------------------------------------------------------------------

    def test_screening_answers_matching_and_export_rejection(self) -> None:
        """Screening questions matching claims succeed; unresolved questions block export."""
        artifacts, _ = self.service.draft_job_application_artifacts(
            user_id=self.user_id,
            case_id=self.case.case_id,
        )
        screen = next(a for a in artifacts if a.material_type == PreparationMaterialType.SCREENING_ANSWERS)

        blocks_by_q = {b.metadata.get("question"): b for b in screen.blocks if b.metadata.get("question")}
        self.assertIn("Are you legally authorized to work in the United States?", blocks_by_q)
        q1_block = blocks_by_q["Are you legally authorized to work in the United States?"]
        self.assertEqual(q1_block.metadata.get("answer_status"), "ANSWERED")
        self.assertIsNotNone(q1_block.metadata.get("answer"))

        self.assertIn("Do you hold an active Top Secret clearance?", blocks_by_q)
        q3_block = blocks_by_q["Do you hold an active Top Secret clearance?"]
        self.assertEqual(q3_block.metadata.get("answer_status"), "NEEDS_USER_INPUT")
        self.assertIsNone(q3_block.metadata.get("answer"))

        # Rendered markdown should NOT leak internal placeholder tokens
        md = screen.render_markdown()
        self.assertNotIn("[NEEDS_USER_INPUT", md)
        self.assertNotIn("[claim:", md)
        self.assertIn("### Q: Do you hold an active Top Secret clearance?", md)

        # Export must be rejected with DomainValidationError because required question is unresolved
        with self.assertRaises(DomainValidationError) as ctx:
            self.service.export_artifact(
                user_id=self.user_id,
                case_id=self.case.case_id,
                artifact_id=screen.artifact_id,
            )
        self.assertIn("unresolved required questions", str(ctx.exception))

    # -------------------------------------------------------------------------
    # 4. Strict Tailoring Boundary
    # -------------------------------------------------------------------------

    def test_tailoring_boundary_no_unsolicited_claims(self) -> None:
        """Unrelated vault facts (acoustic guitar) are NOT drafted into claims."""
        self.service.draft_job_application_artifacts(
            user_id=self.user_id,
            case_id=self.case.case_id,
        )
        with self.storage.unit_of_work() as uow:
            claims = uow.claims.list_by_case(self.case.case_id)
            self.assertEqual(len(claims), 3)
            all_claim_texts = " ".join(c.text for c in claims).lower()
            self.assertNotIn("guitar", all_claim_texts)

    # -------------------------------------------------------------------------
    # 5. Complete Claim Audit Event History
    # -------------------------------------------------------------------------

    def test_claim_lifecycle_event_history(self) -> None:
        """Each drafted claim emits claim.proposed at v1, then claim.supported at v2."""
        self.service.draft_job_application_artifacts(
            user_id=self.user_id,
            case_id=self.case.case_id,
        )
        with self.storage.unit_of_work() as uow:
            events = uow.events.get_events_by_user(self.user_id)
            proposed_events = [e for e in events if e.event_type == "claim.proposed"]
            supported_events = [e for e in events if e.event_type == "claim.supported"]

            self.assertEqual(len(proposed_events), 3)
            self.assertEqual(len(supported_events), 3)

            for pe in proposed_events:
                self.assertEqual(pe.aggregate_version, 1)

            for se in supported_events:
                self.assertEqual(se.aggregate_version, 2)
                self.assertEqual(se.payload["new_status"], "supported")

    # -------------------------------------------------------------------------
    # 6. Privacy-Safe Dependency Hashes
    # -------------------------------------------------------------------------

    def test_privacy_safe_hashes_contain_zero_raw_values(self) -> None:
        """Snapshot hashes contain only identifiers and metadata, zero personal values."""
        h1 = compute_support_snapshot_hash([self.f_dur])
        f_mutated = ContextFact(
            fact_id=self.f_dur.fact_id,
            user_id=self.f_dur.user_id,
            namespace=self.f_dur.namespace,
            key=self.f_dur.key,
            value="Secret private raw value that should never enter the hash",
            version=self.f_dur.version,
            verification_status=self.f_dur.verification_status,
            allowed_purposes=list(self.f_dur.allowed_purposes),
            created_at=self.f_dur.created_at,
            updated_at=self.f_dur.updated_at,
        )
        h2 = compute_support_snapshot_hash([f_mutated])
        self.assertEqual(h1, h2)

        f_mutated.verification_status = VerificationStatus.UNVERIFIED
        h3 = compute_support_snapshot_hash([f_mutated])
        self.assertNotEqual(h1, h3)

    # -------------------------------------------------------------------------
    # 7. Identity & Structural Grounding
    # -------------------------------------------------------------------------

    def test_identity_grounding_verified_vs_unverified(self) -> None:
        """Applicant info appears when verified; otherwise neutral structural fallback is used."""
        planner = JobApplicationArtifactPlanner()
        supported_claims = [
            Claim(
                claim_id="clm-1",
                user_id=self.user_id,
                purpose="job_application",
                text="Experience with Python.",
                case_id=self.case.case_id,
                status=ClaimStatus.SUPPORTED,
                version=2,
            )
        ]

        # Case 1: No verified name facts
        arts_no_name = planner.plan_artifacts(
            case=self.case,
            opportunity=self.opportunity,
            report=self.report,
            supported_claims=supported_claims,
            context_facts=[],
            artifact_set_id="set-1",
            input_hash="hash-in",
            grounding_snapshot_hash="hash-ground",
        )
        cv_no_name = next(a for a in arts_no_name if a.material_type == PreparationMaterialType.CV)
        self.assertEqual(cv_no_name.blocks[0].content, "Curriculum Vitae")
        self.assertNotIn(self.user_id, cv_no_name.blocks[0].content)

        # Case 2: Verified user name fact
        f_name = ContextFact(
            fact_id="fact-name-1",
            user_id=self.user_id,
            namespace="profile",
            key="full_name",
            value="Ada Lovelace",
            verification_status=VerificationStatus.USER_VERIFIED,
            allowed_purposes=["job_application"],
        )
        arts_name = planner.plan_artifacts(
            case=self.case,
            opportunity=self.opportunity,
            report=self.report,
            supported_claims=supported_claims,
            context_facts=[f_name],
            artifact_set_id="set-2",
            input_hash="hash-in",
            grounding_snapshot_hash="hash-ground",
        )
        cv_name = next(a for a in arts_name if a.material_type == PreparationMaterialType.CV)
        self.assertEqual(cv_name.blocks[0].content, "Ada Lovelace")
        self.assertEqual(cv_name.blocks[0].grounding_type, ArtifactGroundingType.CONTEXT_FACT)
        self.assertIn("fact-name-1", cv_name.blocks[0].fact_ids)


if __name__ == "__main__":
    unittest.main()
