"""Unit tests for Phase 5.1 Opportunity Engine: ingestion, normalization, evaluation, fit, and conversion."""

from __future__ import annotations

import unittest

from caseworker.domain.context import ContextFact
from caseworker.domain.enums import (
    CaseType,
    EligibilityStatus,
    FitTier,
    OpportunityStatus,
    OpportunityType,
    RequirementCategory,
    RequirementMatchResult,
    SensitivityLevel,
    VerificationStatus,
)
from caseworker.domain.errors import InvalidStateTransitionError
from caseworker.domain.normalized_requirement import parse_requirements
from caseworker.domain.opportunity import Opportunity
from caseworker.domain.opportunity_evaluator import (
    OpportunityEvaluationSnapshot,
    OpportunityEvaluator,
)
from caseworker.domain.opportunity_provider import ManualOpportunityProvider
from caseworker.persistence.sqlite import SQLiteCaseworkerStorage
from caseworker.services.opportunity_service import OpportunityService


class TestOpportunityEngineDomain(unittest.TestCase):
    """Test domain logic: provider, parser, evaluator, staleness, and fingerprint v2."""

    def test_manual_provider_ingestion(self) -> None:
        provider = ManualOpportunityProvider()
        raw = {
            "title": "Senior AI Systems Engineer",
            "organization": "JackVerse Labs",
            "opportunity_type": "job",
            "location": "Berlin, Germany",
            "requirements": ["Python 5+ years", "React preferred", "Work authorization in EU"],
        }
        opp_raw = provider.ingest(raw)
        self.assertEqual(opp_raw.title, "Senior AI Systems Engineer")
        self.assertEqual(opp_raw.organization, "JackVerse Labs")
        self.assertEqual(opp_raw.opportunity_type, OpportunityType.JOB)
        self.assertEqual(len(opp_raw.requirements), 3)

    def test_parse_requirements_templates_and_unparsed(self) -> None:
        raw_reqs = [
            "5+ years of experience in distributed systems",
            "Proficiency in Python and Kubernetes",
            "Valid work authorization in Germany",
            "Master's degree in Computer Science",
            "German B2 proficiency required",
            "Experience with GraphQL preferred",
            "Strong team collaboration and empathy across diverse cultures",  # Unparsed custom
        ]
        parsed = parse_requirements(raw_reqs)
        self.assertEqual(len(parsed), 7)

        # Check criterion mapping
        self.assertEqual(parsed[0].criterion, "years_experience")
        self.assertEqual(parsed[0].value, 5)
        self.assertEqual(parsed[0].category, RequirementCategory.MANDATORY)

        self.assertEqual(parsed[1].criterion, "skill")
        self.assertEqual(parsed[1].category, RequirementCategory.MANDATORY)

        self.assertEqual(parsed[2].criterion, "work_authorization")
        self.assertEqual(parsed[2].category, RequirementCategory.MANDATORY)

        self.assertEqual(parsed[3].criterion, "degree")
        self.assertEqual(parsed[3].category, RequirementCategory.MANDATORY)

        self.assertEqual(parsed[4].criterion, "language")
        self.assertEqual(parsed[4].category, RequirementCategory.MANDATORY)

        self.assertEqual(parsed[5].criterion, "skill")
        self.assertEqual(parsed[5].category, RequirementCategory.PREFERRED)

        # Custom / unparsed
        self.assertEqual(parsed[6].criterion, "custom")
        self.assertEqual(parsed[6].normalization_status, "unparsed")
        self.assertEqual(parsed[6].category, RequirementCategory.MANDATORY)

    def test_tri_state_eligibility_unknown_when_facts_missing(self) -> None:
        """UNKNOWN != INELIGIBLE: missing evidence must produce UNKNOWN, not INELIGIBLE or ELIGIBLE."""
        opp = Opportunity(
            user_id="user_test",
            opportunity_type=OpportunityType.JOB,
            title="AI Engineer",
            organization="TechCo",
        )
        reqs = parse_requirements(["German B2 proficiency required"])
        # No German language fact in context
        empty_facts: list[ContextFact] = []

        snapshot = OpportunityEvaluator.evaluate(opp, reqs, empty_facts)
        self.assertEqual(snapshot.eligibility.status, EligibilityStatus.UNKNOWN)
        self.assertEqual(snapshot.eligibility.coverage, 0.0)
        self.assertEqual(snapshot.eligibility.mandatory_met, 0)

    def test_tri_state_eligibility_ineligible_on_contradiction(self) -> None:
        """INELIGIBLE: when a mandatory requirement is explicitly contradicted."""
        opp = Opportunity(
            user_id="user_test",
            opportunity_type=OpportunityType.JOB,
            title="Senior Architect",
            organization="TechCo",
        )
        reqs = parse_requirements(["8+ years of work experience required"])
        facts = [
            ContextFact(
                user_id="user_test",
                namespace="experience",
                key="years_experience",
                value="3",
                verification_status=VerificationStatus.USER_VERIFIED,
            )
        ]

        snapshot = OpportunityEvaluator.evaluate(opp, reqs, facts)
        self.assertEqual(snapshot.eligibility.status, EligibilityStatus.INELIGIBLE)
        self.assertIn(reqs[0].requirement_id, snapshot.eligibility.contradicted_requirements)

    def test_tri_state_eligibility_eligible_when_all_mandatory_met(self) -> None:
        """ELIGIBLE: when all mandatory requirements have positive supporting evidence."""
        opp = Opportunity(
            user_id="user_test",
            opportunity_type=OpportunityType.JOB,
            title="Python Developer",
            organization="TechCo",
            location="Remote",
        )
        reqs = parse_requirements(["Proficiency in Python", "Remote position available"])
        facts = [
            ContextFact(
                user_id="user_test",
                namespace="skills",
                key="python",
                value="Expert Python Developer",
                verification_status=VerificationStatus.USER_VERIFIED,
            )
        ]

        snapshot = OpportunityEvaluator.evaluate(opp, reqs, facts)
        self.assertEqual(snapshot.eligibility.status, EligibilityStatus.ELIGIBLE)
        self.assertEqual(snapshot.eligibility.mandatory_met, 2)
        self.assertEqual(snapshot.eligibility.coverage, 1.0)

    def test_fingerprint_v2_cross_source_deduplication(self) -> None:
        """V2 fingerprint excludes source_url so cross-source duplicates are identical."""
        opp_linkedin = Opportunity(
            user_id="user_1",
            opportunity_type=OpportunityType.JOB,
            title="Staff Engineer",
            organization="Google",
            location="Munich",
            source_url="https://linkedin.com/jobs/view/123",
        )
        opp_indeed = Opportunity(
            user_id="user_1",
            opportunity_type=OpportunityType.JOB,
            title="Staff Engineer",
            organization="Google",
            location="Munich",
            source_url="https://indeed.com/viewjob?id=456",
        )
        self.assertNotEqual(opp_linkedin.fingerprint, opp_indeed.fingerprint)  # V1 differs
        self.assertEqual(opp_linkedin.fingerprint_v2, opp_indeed.fingerprint_v2)  # V2 matches!


class TestOpportunityServiceLifecycle(unittest.TestCase):
    """Test full service lifecycle: create, normalize, evaluate with audit, shortlist, and convert."""

    def setUp(self) -> None:
        self.storage = SQLiteCaseworkerStorage(":memory:")
        self.service = OpportunityService(self.storage)
        self.user_id = "user_eva"

    def tearDown(self) -> None:
        self.storage.close()

    def test_full_pipeline_to_case_conversion(self) -> None:
        from caseworker.domain.mission import Mission
        from caseworker.domain.enums import MissionKind, MissionStatus

        # 1. Create active mission
        with self.storage.unit_of_work() as uow:
            mission = Mission(
                user_id=self.user_id,
                title="Academic Pursuit",
                goal="Secure research role",
                kind=MissionKind.OPPORTUNITY_PURSUIT,
                status=MissionStatus.ACTIVE,
            )
            uow.missions.save(mission)

        # 2. Create opportunity in DISCOVERED state linked to mission
        opp, created = self.service.create_opportunity(
            user_id=self.user_id,
            title="Principal AI Research Scientist",
            opportunity_type=OpportunityType.RESEARCH,
            organization="Max Planck Institute",
            location="Munich, Germany",
            requirements=["Ph.D. degree required", "Python skills", "Publication record preferred"],
            mission_id=mission.mission_id,
        )
        self.assertTrue(created)
        self.assertEqual(opp.status, OpportunityStatus.DISCOVERED)

        # 3. Add supporting context facts (including location preference)
        with self.storage.unit_of_work() as uow:
            uow.context.save(
                ContextFact(
                    user_id=self.user_id,
                    namespace="education",
                    key="doctorate",
                    value="Ph.D. in Machine Learning",
                    verification_status=VerificationStatus.USER_VERIFIED,
                    allowed_purposes=["opportunity_evaluation"],
                )
            )
            uow.context.save(
                ContextFact(
                    user_id=self.user_id,
                    namespace="skills",
                    key="python",
                    value="Python, PyTorch",
                    verification_status=VerificationStatus.USER_VERIFIED,
                    allowed_purposes=["opportunity_evaluation"],
                )
            )
            uow.context.save(
                ContextFact(
                    user_id=self.user_id,
                    namespace="preferences",
                    key="location",
                    value="Munich, Germany",
                    verification_status=VerificationStatus.USER_VERIFIED,
                    allowed_purposes=["opportunity_evaluation"],
                )
            )

        # 4. Normalize
        opp_norm, normalized = self.service.normalize_opportunity_for_user(self.user_id, opp.opportunity_id)
        self.assertEqual(opp_norm.status, OpportunityStatus.NORMALIZED)
        self.assertEqual(len(normalized), 3)

        # 4. Evaluate with purpose-gated context access and audit
        opp_eval, snapshot = self.service.evaluate_opportunity_for_user(self.user_id, opp.opportunity_id)
        self.assertEqual(opp_eval.status, OpportunityStatus.EVALUATING)
        self.assertEqual(snapshot.eligibility.status, EligibilityStatus.ELIGIBLE)
        self.assertGreaterEqual(snapshot.fit.components.total_score, 70.0)

        # Verify audit events were appended
        with self.storage.unit_of_work() as uow:
            events = uow.events.get_events_by_user(self.user_id)
            event_types = [e.event_type for e in events]
            self.assertIn("context.access_granted", event_types)
            self.assertIn("opportunity.evaluated", event_types)

        # 5. Shortlist
        opp_short = self.service.shortlist_opportunity_for_user(
            self.user_id, opp.opportunity_id, reason="Strong research match"
        )
        self.assertEqual(opp_short.status, OpportunityStatus.SHORTLISTED)

        # 6. Convert to Case atomically
        case, opp_conv, case_created = self.service.convert_opportunity_to_case(
            user_id=self.user_id,
            opportunity_id=opp.opportunity_id,
        )
        self.assertTrue(case_created)
        self.assertEqual(opp_conv.status, OpportunityStatus.CONVERTED_TO_CASE)
        self.assertEqual(opp_conv.converted_case_id, case.case_id)
        self.assertEqual(case.case_type, CaseType.GENERAL)  # RESEARCH maps to GENERAL

        # 7. Idempotent replay: converting again returns existing case with created=False
        replay_case, replay_opp, replay_created = self.service.convert_opportunity_to_case(
            user_id=self.user_id,
            opportunity_id=opp.opportunity_id,
        )
        self.assertFalse(replay_created)
        self.assertEqual(replay_case.case_id, case.case_id)
        self.assertEqual(replay_opp.converted_case_id, case.case_id)

    def test_shortlist_blocks_ineligible_without_override(self) -> None:
        """Shortlisting an ineligible opportunity must fail unless override_ineligible=True and reason provided."""
        opp, _ = self.service.create_opportunity(
            user_id=self.user_id,
            title="Senior Architect",
            opportunity_type=OpportunityType.JOB,
            requirements=["10+ years of experience required"],
        )
        with self.storage.unit_of_work() as uow:
            uow.context.save(
                ContextFact(
                    user_id=self.user_id,
                    namespace="experience",
                    key="years_experience",
                    value="2",
                    verification_status=VerificationStatus.USER_VERIFIED,
                    allowed_purposes=["opportunity_evaluation"],
                )
            )

        self.service.normalize_opportunity_for_user(self.user_id, opp.opportunity_id)
        self.service.evaluate_opportunity_for_user(self.user_id, opp.opportunity_id)

        # Shortlist without override should raise InvalidStateTransitionError
        with self.assertRaises(InvalidStateTransitionError):
            self.service.shortlist_opportunity_for_user(
                self.user_id, opp.opportunity_id, override_ineligible=False
            )

        # Shortlist with override and reason should succeed
        opp_short = self.service.shortlist_opportunity_for_user(
            self.user_id,
            opp.opportunity_id,
            override_ineligible=True,
            reason="Exceptional portfolio compensates for years of experience",
        )
        self.assertEqual(opp_short.status, OpportunityStatus.SHORTLISTED)


if __name__ == "__main__":
    unittest.main()
