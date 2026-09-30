"""Unit tests for Opportunity domain entity and deduplication fingerprinting."""

from datetime import datetime
import unittest

from caseworker.domain.enums import OpportunityStatus, OpportunityType
from caseworker.domain.errors import InvalidStateTransitionError
from caseworker.domain.opportunity import Opportunity
from caseworker.domain.types import now_utc


class TestOpportunityEntity(unittest.TestCase):
    def test_opportunity_type_coverage(self) -> None:
        types = [
            OpportunityType.JOB,
            OpportunityType.SCHOLARSHIP,
            OpportunityType.GRANT,
            OpportunityType.FELLOWSHIP,
            OpportunityType.RESEARCH,
            OpportunityType.HOUSING,
            OpportunityType.FREELANCE,
            OpportunityType.HACKATHON,
            OpportunityType.CONFERENCE,
            OpportunityType.COMPETITION,
            OpportunityType.OTHER,
        ]
        self.assertEqual(len(types), 11)

    def test_deterministic_fingerprint_deduplication(self) -> None:
        opp1 = Opportunity(
            user_id="user_1",
            mission_id="mission_alpha",
            opportunity_type=OpportunityType.JOB,
            title="Senior AI Engineer",
            organization="OpenAI",
            source_url="https://openai.com/careers/123",
            discovered_at=now_utc(),
        )

        # Same opportunity discovered later under a DIFFERENT mission and timestamp
        opp2 = Opportunity(
            user_id="user_1",
            mission_id="mission_beta",
            opportunity_type=OpportunityType.JOB,
            title=" senior ai engineer ",  # whitespace / casing differences
            organization="OPENAI",
            source_url="https://openai.com/careers/123",
            discovered_at=now_utc(),
        )

        # Fingerprints MUST be identical for deduplication
        self.assertEqual(opp1.fingerprint, opp2.fingerprint)

    def test_fingerprint_changes_on_different_source_or_title(self) -> None:
        opp1 = Opportunity(
            user_id="user_1",
            opportunity_type=OpportunityType.JOB,
            title="Senior AI Engineer",
            organization="OpenAI",
            source_url="https://openai.com/careers/123",
        )
        opp2 = Opportunity(
            user_id="user_1",
            opportunity_type=OpportunityType.JOB,
            title="Staff AI Engineer",
            organization="OpenAI",
            source_url="https://openai.com/careers/123",
        )
        self.assertNotEqual(opp1.fingerprint, opp2.fingerprint)

    def test_lifecycle_transitions(self) -> None:
        opp = Opportunity(
            user_id="user_1",
            opportunity_type=OpportunityType.SCHOLARSHIP,
            title="DAAD Research Grant",
            organization="DAAD",
        )
        self.assertEqual(opp.status, OpportunityStatus.DISCOVERED)

        opp.transition_to(OpportunityStatus.NORMALIZED)
        self.assertEqual(opp.status, OpportunityStatus.NORMALIZED)

        opp.transition_to(OpportunityStatus.EVALUATING)
        self.assertEqual(opp.status, OpportunityStatus.EVALUATING)

        opp.transition_to(OpportunityStatus.SHORTLISTED)
        self.assertEqual(opp.status, OpportunityStatus.SHORTLISTED)

        opp.transition_to(OpportunityStatus.CONVERTED_TO_CASE)
        self.assertEqual(opp.status, OpportunityStatus.CONVERTED_TO_CASE)
        self.assertTrue(opp.status.is_terminal)

        with self.assertRaises(InvalidStateTransitionError):
            opp.transition_to(OpportunityStatus.DISCOVERED)

    def test_serialization_roundtrip(self) -> None:
        opp = Opportunity(
            user_id="user_1",
            opportunity_type=OpportunityType.HOUSING,
            title="2-room apartment Maxvorstadt",
            organization="ImmoScout",
            location="Munich, Germany",
            requirements=["Deposit 3000 EUR", "Proof of income"],
            metadata={"warm_rent": 1450, "floor": 3},
        )
        d = opp.to_dict()
        self.assertEqual(d["opportunity_type"], "housing")
        self.assertIn("warm_rent", d["metadata"])

        reconstructed = Opportunity.from_dict(d)
        self.assertEqual(reconstructed.opportunity_id, opp.opportunity_id)
        self.assertEqual(reconstructed.fingerprint, opp.fingerprint)
        self.assertEqual(reconstructed.metadata["warm_rent"], 1450)


if __name__ == "__main__":
    unittest.main()
