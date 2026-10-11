"""Comprehensive unit tests for Phase 5.1.1 Opportunity Engine Integrity Hardening.

Covers:
1. Creation locked to DISCOVERED & reserved metadata key protection.
2. Protected pipeline transitions & re-normalization invalidation.
3. Hard eligibility verified evidence guard.
4. CEFR hierarchy and work authorization disambiguation.
5. Deterministic requirement IDs.
6. Context dependency snapshot hashing & truthful staleness guard on shortlisting.
7. Re-evaluation aggregate versioning and event emission.
8. External posting ID deduplication & V2 collision safety.
9. Zero unearned default points & fit coverage guard (< 0.40 -> INSUFFICIENT_EVIDENCE).
10. Mandatory mission conversion lifecycle validation & replay conflict detection.
"""

from __future__ import annotations

import unittest
from pydantic import ValidationError

from caseworker.api.schemas.opportunity import CreateOpportunityRequest
from caseworker.domain.context import ContextFact
from caseworker.domain.enums import (
    EligibilityStatus,
    FitTier,
    MissionKind,
    MissionStatus,
    OpportunityStatus,
    OpportunityType,
    RequirementCategory,
    RequirementMatchResult,
    VerificationStatus,
)
from caseworker.domain.errors import (
    DomainValidationError,
    InvalidStateTransitionError,
)
from caseworker.domain.mission import Mission
from caseworker.domain.normalized_requirement import parse_requirements
from caseworker.domain.opportunity import Opportunity
from caseworker.domain.opportunity_evaluator import (
    OpportunityEvaluator,
    _extract_cefr_level,
)
from caseworker.persistence.sqlite import SQLiteCaseworkerStorage
from caseworker.services.opportunity_service import OpportunityService


class TestOpportunityIntegrityHardening(unittest.TestCase):
    """Deep verification of all 10 integrity rules."""

    def setUp(self) -> None:
        self.storage = SQLiteCaseworkerStorage(":memory:")
        self.service = OpportunityService(self.storage)
        self.user_id = "integrity_tester"

    def tearDown(self) -> None:
        self.storage.close()

    def _create_active_mission(self, user_id: str | None = None) -> Mission:
        uid = user_id or self.user_id
        with self.storage.unit_of_work() as uow:
            mission = Mission(
                user_id=uid,
                title="Primary Career Goal",
                goal="Obtain principal engineering position",
                kind=MissionKind.OPPORTUNITY_PURSUIT,
                status=MissionStatus.ACTIVE,
            )
            uow.missions.save(mission)
            return mission

    # -------------------------------------------------------------------------
    # 1. Creation Locked to DISCOVERED & Reserved Metadata Keys
    # -------------------------------------------------------------------------
    def test_schema_rejects_reserved_metadata_keys(self) -> None:
        reserved_keys = [
            "evaluation",
            "normalized_requirements",
            "requirement_schema",
            "converted_case_id",
            "fingerprint",
            "fingerprint_v2",
            "is_stale",
        ]
        for key in reserved_keys:
            with self.subTest(reserved_key=key):
                with self.assertRaises(ValidationError) as ctx:
                    CreateOpportunityRequest(
                        title="Engineer",
                        opportunity_type="job",
                        metadata={key: "malicious_payload"},
                    )
                self.assertIn("Reserved metadata key", str(ctx.exception))

    def test_service_create_forces_discovered_and_rejects_reserved_metadata(self) -> None:
        # Service directly rejects reserved metadata
        with self.assertRaises(DomainValidationError) as ctx:
            self.service.create_opportunity(
                user_id=self.user_id,
                title="Engineer",
                opportunity_type=OpportunityType.JOB,
                metadata={"evaluation": {"fake": "score"}},
            )
        self.assertIn("Reserved metadata key", str(ctx.exception))

        # Successfully created opportunity is ALWAYS DISCOVERED
        opp, _ = self.service.create_opportunity(
            user_id=self.user_id,
            title="Lead Engineer",
            opportunity_type=OpportunityType.JOB,
        )
        self.assertEqual(opp.status, OpportunityStatus.DISCOVERED)

    # -------------------------------------------------------------------------
    # 2. Protected Pipeline States & Re-normalization
    # -------------------------------------------------------------------------
    def test_generic_transition_blocks_governed_pipeline_states(self) -> None:
        opp, _ = self.service.create_opportunity(
            user_id=self.user_id,
            title="Lead Engineer",
            opportunity_type=OpportunityType.JOB,
        )
        blocked_targets = [
            OpportunityStatus.NORMALIZED,
            OpportunityStatus.EVALUATING,
            OpportunityStatus.SHORTLISTED,
            OpportunityStatus.CONVERTED_TO_CASE,
        ]
        for target in blocked_targets:
            with self.subTest(target=target):
                with self.assertRaises(InvalidStateTransitionError) as ctx:
                    self.service.transition_opportunity_for_user(
                        self.user_id, opp.opportunity_id, target
                    )
                self.assertIn("requires dedicated workflow endpoint", str(ctx.exception))

    def test_renormalization_from_evaluating_invalidates_evaluation(self) -> None:
        opp, _ = self.service.create_opportunity(
            user_id=self.user_id,
            title="Senior Engineer",
            opportunity_type=OpportunityType.JOB,
            requirements=["Python proficiency required"],
        )
        # Normalize
        self.service.normalize_opportunity_for_user(self.user_id, opp.opportunity_id)
        # Evaluate
        opp_eval, snapshot = self.service.evaluate_opportunity_for_user(self.user_id, opp.opportunity_id)
        self.assertEqual(opp_eval.status, OpportunityStatus.EVALUATING)
        self.assertIsNotNone(opp_eval.metadata.get("evaluation"))

        # Re-normalize from EVALUATING
        opp_renorm, new_reqs = self.service.normalize_opportunity_for_user(
            self.user_id,
            opp.opportunity_id,
        )
        self.assertEqual(opp_renorm.status, OpportunityStatus.NORMALIZED)
        self.assertIsNone(opp_renorm.metadata.get("evaluation"))
        self.assertEqual(len(new_reqs), 1)

    # -------------------------------------------------------------------------
    # 3. Verified Evidence for Hard Eligibility
    # -------------------------------------------------------------------------
    def test_mandatory_eligibility_requires_verified_evidence(self) -> None:
        # Unverified context facts (UNVERIFIED, CLAIM_ONLY, PENDING) cannot satisfy mandatory criteria
        fact = ContextFact(
            user_id=self.user_id,
            namespace="skills",
            key="python",
            value="Expert Python Developer",
            verification_status=VerificationStatus.UNVERIFIED,
            allowed_purposes=["opportunity_evaluation"],
        )
        with self.storage.unit_of_work() as uow:
            uow.context.save(fact)

        opp, _ = self.service.create_opportunity(
            user_id=self.user_id,
            title="Backend Dev",
            opportunity_type=OpportunityType.JOB,
            requirements=["Python proficiency required"],
        )
        self.service.normalize_opportunity_for_user(self.user_id, opp.opportunity_id)
        _, snapshot = self.service.evaluate_opportunity_for_user(self.user_id, opp.opportunity_id)

        # Mandatory requirement matched against UNVERIFIED fact must yield UNKNOWN, not MET
        self.assertEqual(snapshot.eligibility.status, EligibilityStatus.UNKNOWN)
        evidence = snapshot.evidence[0]
        self.assertEqual(evidence.match_result, RequirementMatchResult.UNKNOWN)
        self.assertIn("unverified", evidence.notes)

        # When verified, update the fact and re-evaluate -> satisfies the requirement
        fact.verification_status = VerificationStatus.USER_VERIFIED
        fact.version += 1
        with self.storage.unit_of_work() as uow:
            uow.context.save(fact)

        _, snapshot_verified = self.service.evaluate_opportunity_for_user(self.user_id, opp.opportunity_id)
        self.assertEqual(snapshot_verified.eligibility.status, EligibilityStatus.ELIGIBLE)
        self.assertEqual(snapshot_verified.evidence[0].match_result, RequirementMatchResult.MET)

    # -------------------------------------------------------------------------
    # 4. CEFR Hierarchy & Work Authorization Correctness
    # -------------------------------------------------------------------------
    def test_cefr_hierarchy_ordering(self) -> None:
        self.assertLess(_extract_cefr_level("A1"), _extract_cefr_level("A2"))
        self.assertLess(_extract_cefr_level("A2"), _extract_cefr_level("B1"))
        self.assertLess(_extract_cefr_level("B1"), _extract_cefr_level("B2"))
        self.assertLess(_extract_cefr_level("B2"), _extract_cefr_level("C1"))
        self.assertLess(_extract_cefr_level("C1"), _extract_cefr_level("C2"))

        # User has B2 German in context
        with self.storage.unit_of_work() as uow:
            uow.context.save(
                ContextFact(
                    user_id=self.user_id,
                    namespace="languages",
                    key="german",
                    value="B2 Upper-Intermediate",
                    verification_status=VerificationStatus.USER_VERIFIED,
                    allowed_purposes=["opportunity_evaluation"],
                )
            )

        # Opportunity requires C1 -> NOT_MET
        opp_c1, _ = self.service.create_opportunity(
            user_id=self.user_id,
            title="Legal Counsel",
            opportunity_type=OpportunityType.JOB,
            requirements=["German C1 proficiency required"],
        )
        self.service.normalize_opportunity_for_user(self.user_id, opp_c1.opportunity_id)
        _, snap_c1 = self.service.evaluate_opportunity_for_user(self.user_id, opp_c1.opportunity_id)
        self.assertEqual(snap_c1.evidence[0].match_result, RequirementMatchResult.NOT_MET)
        self.assertEqual(snap_c1.eligibility.status, EligibilityStatus.INELIGIBLE)

        # Opportunity requires B1 -> MET
        opp_b1, _ = self.service.create_opportunity(
            user_id=self.user_id,
            title="Junior Dev",
            opportunity_type=OpportunityType.JOB,
            requirements=["German B1 proficiency required"],
        )
        self.service.normalize_opportunity_for_user(self.user_id, opp_b1.opportunity_id)
        _, snap_b1 = self.service.evaluate_opportunity_for_user(self.user_id, opp_b1.opportunity_id)
        self.assertEqual(snap_b1.evidence[0].match_result, RequirementMatchResult.MET)
        self.assertEqual(snap_b1.eligibility.status, EligibilityStatus.ELIGIBLE)

    def test_work_authorization_ambiguous_yields_unknown(self) -> None:
        opp, _ = self.service.create_opportunity(
            user_id=self.user_id,
            title="Software Engineer",
            opportunity_type=OpportunityType.JOB,
            requirements=["Visa sponsorship available for exceptional candidates only"],
        )
        self.service.normalize_opportunity_for_user(self.user_id, opp.opportunity_id)
        _, snap = self.service.evaluate_opportunity_for_user(self.user_id, opp.opportunity_id)
        self.assertEqual(snap.evidence[0].match_result, RequirementMatchResult.UNKNOWN)

    # -------------------------------------------------------------------------
    # 5. Deterministic Requirement IDs
    # -------------------------------------------------------------------------
    def test_deterministic_requirement_ids(self) -> None:
        raw_reqs = ["Python proficiency required", "Remote eligible preferred"]
        parsed_1 = parse_requirements("opp_fixed_123", raw_reqs)
        parsed_2 = parse_requirements("opp_fixed_123", raw_reqs)

        self.assertEqual(parsed_1[0].requirement_id, parsed_2[0].requirement_id)
        self.assertEqual(parsed_1[1].requirement_id, parsed_2[1].requirement_id)

        # Different opp_id produces distinct IDs
        parsed_diff = parse_requirements("opp_other_456", raw_reqs)
        self.assertNotEqual(parsed_1[0].requirement_id, parsed_diff[0].requirement_id)

    # -------------------------------------------------------------------------
    # 6. Context Dependency Snapshot Hashing & Staleness Guard
    # -------------------------------------------------------------------------
    def test_shortlist_blocks_stale_evaluation(self) -> None:
        mission = self._create_active_mission()

        # Context fact
        with self.storage.unit_of_work() as uow:
            uow.context.save(
                ContextFact(
                    user_id=self.user_id,
                    namespace="skills",
                    key="python",
                    value="Python Expert",
                    verification_status=VerificationStatus.USER_VERIFIED,
                    allowed_purposes=["opportunity_evaluation"],
                )
            )

        opp, _ = self.service.create_opportunity(
            user_id=self.user_id,
            title="Senior Python Engineer",
            opportunity_type=OpportunityType.JOB,
            requirements=["Python proficiency required"],
            mission_id=mission.mission_id,
        )
        self.service.normalize_opportunity_for_user(self.user_id, opp.opportunity_id)
        self.service.evaluate_opportunity_for_user(self.user_id, opp.opportunity_id)

        # Mutate context fact in relevant namespace
        with self.storage.unit_of_work() as uow:
            uow.context.save(
                ContextFact(
                    user_id=self.user_id,
                    namespace="skills",
                    key="python",
                    value="Python Staff Architect",
                    verification_status=VerificationStatus.USER_VERIFIED,
                    allowed_purposes=["opportunity_evaluation"],
                )
            )

        # Shortlisting must fail with staleness error
        with self.assertRaises(InvalidStateTransitionError) as ctx:
            self.service.shortlist_opportunity_for_user(self.user_id, opp.opportunity_id)
        self.assertIn("evaluation is stale", str(ctx.exception).lower())

        # Re-evaluating clears staleness and allows shortlisting
        self.service.evaluate_opportunity_for_user(self.user_id, opp.opportunity_id)
        opp_short = self.service.shortlist_opportunity_for_user(self.user_id, opp.opportunity_id)
        self.assertEqual(opp_short.status, OpportunityStatus.SHORTLISTED)

    # -------------------------------------------------------------------------
    # 7. Re-Evaluation Aggregate Versioning & Event Emission
    # -------------------------------------------------------------------------
    def test_reevaluation_increments_version(self) -> None:
        opp, _ = self.service.create_opportunity(
            user_id=self.user_id,
            title="Software Architect",
            opportunity_type=OpportunityType.JOB,
            requirements=["Architecture experience"],
        )
        v1 = opp.version
        self.service.normalize_opportunity_for_user(self.user_id, opp.opportunity_id)
        opp_eval_1, _ = self.service.evaluate_opportunity_for_user(self.user_id, opp.opportunity_id)
        v2 = opp_eval_1.version
        self.assertGreater(v2, v1)

        # Re-evaluating in EVALUATING state increments version again
        opp_eval_2, _ = self.service.evaluate_opportunity_for_user(self.user_id, opp.opportunity_id)
        self.assertEqual(opp_eval_2.version, v2 + 1)

        with self.storage.unit_of_work() as uow:
            events = uow.events.get_events_by_user(self.user_id)
            eval_events = sorted(
                [e for e in events if e.event_type == "opportunity.evaluated"],
                key=lambda e: e.aggregate_version,
            )
            self.assertEqual(len(eval_events), 2)
            self.assertEqual(eval_events[0].aggregate_version, opp_eval_1.version)
            self.assertEqual(eval_events[-1].aggregate_version, opp_eval_2.version)

    # -------------------------------------------------------------------------
    # 8. External Posting ID Deduplication & Collision Safety
    # -------------------------------------------------------------------------
    def test_external_posting_id_deduplication_and_collision_safety(self) -> None:
        # Ingest first opportunity with external posting id
        opp1, created1 = self.service.create_opportunity(
            user_id=self.user_id,
            title="Senior Staff AI Engineer",
            organization="Anthropic",
            external_posting_id="REQ-2026-999",
            opportunity_type=OpportunityType.JOB,
        )
        self.assertTrue(created1)
        self.assertEqual(opp1.external_posting_id, "REQ-2026-999")

        # Second creation with same external posting id and organization deduplicates
        opp2, created2 = self.service.create_opportunity(
            user_id=self.user_id,
            title="Staff AI Engineer (Re-advertised)",
            organization="Anthropic",
            external_posting_id="REQ-2026-999",
            opportunity_type=OpportunityType.JOB,
        )
        self.assertFalse(created2)
        self.assertEqual(opp2.opportunity_id, opp1.opportunity_id)

        # Collision safety: If repository holds >1 records with the same fingerprint_v2
        # (e.g. legacy dirty state), service must NOT merge or corrupt, but create new
        with self.storage.unit_of_work() as uow:
            duplicate_opp = Opportunity(
                user_id=self.user_id,
                title="Cloned Listing",
                organization="Anthropic",
                external_posting_id="COLLISION-1",
                fingerprint_v2="collision_fp_v2",
                opportunity_type=OpportunityType.JOB,
            )
            uow.opportunities.save(duplicate_opp)
            duplicate_opp_2 = Opportunity(
                user_id=self.user_id,
                title="Cloned Listing 2",
                organization="Anthropic",
                external_posting_id="COLLISION-1",
                fingerprint_v2="collision_fp_v2",
                opportunity_type=OpportunityType.JOB,
            )
            uow.opportunities.save(duplicate_opp_2)

        # Ingesting now finds >1 matches, so it creates a distinct new record
        opp_new, created_new = self.service.create_opportunity(
            user_id=self.user_id,
            title="Cloned Listing 3",
            organization="Anthropic",
            external_posting_id="COLLISION-1",
            opportunity_type=OpportunityType.JOB,
        )
        self.assertTrue(created_new)
        self.assertNotIn(opp_new.opportunity_id, {duplicate_opp.opportunity_id, duplicate_opp_2.opportunity_id})

    # -------------------------------------------------------------------------
    # 9. Fit Coverage Guard & Zero Unearned Points
    # -------------------------------------------------------------------------
    def test_fit_coverage_guard_and_zero_unearned_points(self) -> None:
        # Opportunity with only location requirement -> only 15 points applicable (coverage = 0.15 < 0.40)
        opp, _ = self.service.create_opportunity(
            user_id=self.user_id,
            title="Remote Generalist",
            opportunity_type=OpportunityType.JOB,
            location="Remote",
            requirements=[],
        )
        self.service.normalize_opportunity_for_user(self.user_id, opp.opportunity_id)
        _, snap = self.service.evaluate_opportunity_for_user(self.user_id, opp.opportunity_id)

        # Location matched (15/15), total score scaled to 100%, but coverage is 0.15 (< 0.40)
        # MUST be assigned INSUFFICIENT_EVIDENCE
        self.assertEqual(snap.fit.components.fit_coverage, 0.15)
        self.assertEqual(snap.fit.components.fit_tier, FitTier.INSUFFICIENT_EVIDENCE)
        self.assertIn("Insufficient evidence", snap.fit.components.explanations[0])

        # Zero unearned points: absent categories earn 0 pts, not legacy defaults
        self.assertEqual(snap.fit.components.skills_score, 0.0)
        self.assertEqual(snap.fit.components.seniority_score, 0.0)
        self.assertEqual(snap.fit.components.preferred_reqs_score, 0.0)

    # -------------------------------------------------------------------------
    # 10. Mandatory Mission Conversion Lifecycle & Replay Guard
    # -------------------------------------------------------------------------
    def test_conversion_requires_mission_and_validates_lifecycle(self) -> None:
        # 1. Conversion fails when no mission is linked or supplied
        opp, _ = self.service.create_opportunity(
            user_id=self.user_id,
            title="Staff Engineer",
            opportunity_type=OpportunityType.JOB,
        )
        self.service.normalize_opportunity_for_user(self.user_id, opp.opportunity_id)
        self.service.evaluate_opportunity_for_user(self.user_id, opp.opportunity_id)
        self.service.shortlist_opportunity_for_user(self.user_id, opp.opportunity_id)

        with self.assertRaises(DomainValidationError) as ctx:
            self.service.convert_opportunity_to_case(self.user_id, opp.opportunity_id)
        self.assertIn("A mission is required", str(ctx.exception))

        # 2. Conversion fails if mission is archived
        archived_mission = Mission(
            user_id=self.user_id,
            title="Archived Pursuit",
            goal="Past goal",
            kind=MissionKind.OPPORTUNITY_PURSUIT,
            status=MissionStatus.ACTIVE,
            archived=True,
        )
        with self.storage.unit_of_work() as uow:
            uow.missions.save(archived_mission)

        with self.assertRaises(InvalidStateTransitionError) as ctx:
            self.service.convert_opportunity_to_case(
                self.user_id, opp.opportunity_id, mission_id=archived_mission.mission_id
            )
        self.assertIn("archived mission", str(ctx.exception).lower())

        # 3. Conversion fails if mission status is not DRAFT or ACTIVE (e.g. PAUSED or COMPLETED)
        paused_mission = Mission(
            user_id=self.user_id,
            title="Paused Pursuit",
            goal="Paused goal",
            kind=MissionKind.OPPORTUNITY_PURSUIT,
            status=MissionStatus.PAUSED,
        )
        with self.storage.unit_of_work() as uow:
            uow.missions.save(paused_mission)

        with self.assertRaises(InvalidStateTransitionError) as ctx:
            self.service.convert_opportunity_to_case(
                self.user_id, opp.opportunity_id, mission_id=paused_mission.mission_id
            )
        self.assertIn("paused", str(ctx.exception).lower())

        # 4. Conversion succeeds with active mission
        active_mission = self._create_active_mission()
        case, opp_conv, created = self.service.convert_opportunity_to_case(
            self.user_id,
            opp.opportunity_id,
            mission_id=active_mission.mission_id,
            title="My Target Job Application",
        )
        self.assertTrue(created)
        self.assertEqual(case.title, "My Target Job Application")
        self.assertEqual(opp_conv.status, OpportunityStatus.CONVERTED_TO_CASE)

        # 5. Replay with identical / compatible parameters succeeds
        replay_case, _, replay_created = self.service.convert_opportunity_to_case(
            self.user_id,
            opp.opportunity_id,
            mission_id=active_mission.mission_id,
            title="My Target Job Application",
        )
        self.assertFalse(replay_created)
        self.assertEqual(replay_case.case_id, case.case_id)

        # 6. Replay with conflicting parameters raises InvalidStateTransitionError (maps to 409)
        with self.assertRaises(InvalidStateTransitionError) as ctx:
            self.service.convert_opportunity_to_case(
                self.user_id,
                opp.opportunity_id,
                title="Conflicting New Title",
            )
        self.assertIn("Replay conflict", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
