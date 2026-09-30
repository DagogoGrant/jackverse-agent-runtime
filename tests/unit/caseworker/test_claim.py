"""Unit tests for Claim aggregate root and state machine in Claim Ledger."""

import unittest

from caseworker.domain.claim import Claim
from caseworker.domain.enums import ClaimStatus
from caseworker.domain.errors import DomainValidationError, InvalidStateTransitionError


class TestClaimEntity(unittest.TestCase):
    def test_claim_creation_defaults(self) -> None:
        claim = Claim(
            user_id="user_123",
            purpose="job_application",
            text="Led migration of core monolith to event-driven microservices.",
            supporting_fact_ids=["fact_1", "fact_2"],
        )
        self.assertEqual(claim.user_id, "user_123")
        self.assertEqual(claim.purpose, "job_application")
        self.assertEqual(claim.status, ClaimStatus.PROPOSED)
        self.assertEqual(claim.supporting_fact_ids, ["fact_1", "fact_2"])
        self.assertEqual(claim.version, 1)
        self.assertFalse(claim.is_supported)
        self.assertFalse(claim.is_terminal)
        self.assertIsNotNone(claim.created_at)

    def test_claim_validation_invariants(self) -> None:
        with self.assertRaises(DomainValidationError):
            Claim(user_id="", purpose="job", text="Valid text")
        with self.assertRaises(DomainValidationError):
            Claim(user_id="u1", purpose="", text="Valid text")
        with self.assertRaises(DomainValidationError):
            Claim(user_id="u1", purpose="job", text="")
        with self.assertRaises(DomainValidationError):
            Claim(user_id="u1", purpose="job", text="Valid text", version=0)

    def test_valid_lifecycle_transitions(self) -> None:
        claim = Claim(
            user_id="u1",
            purpose="job_application",
            text="Completed Bachelor thesis on Deep Reinforcement Learning.",
        )
        # PROPOSED -> SUPPORTED
        claim.set_supported()
        self.assertEqual(claim.status, ClaimStatus.SUPPORTED)
        self.assertTrue(claim.is_supported)
        self.assertEqual(claim.version, 2)
        self.assertIsNotNone(claim.verified_at)

        # SUPPORTED -> UNSUPPORTED (e.g. supporting fact expired or revoked)
        claim.set_unsupported("Supporting diploma expired")
        self.assertEqual(claim.status, ClaimStatus.UNSUPPORTED)
        self.assertEqual(claim.rejection_reason, "Supporting diploma expired")
        self.assertEqual(claim.version, 3)

        # UNSUPPORTED -> CONFLICTED (contradiction found)
        claim.set_conflicted("Conflicting graduation years reported in context")
        self.assertEqual(claim.status, ClaimStatus.CONFLICTED)
        self.assertEqual(claim.version, 4)

        # CONFLICTED -> REJECTED (terminal)
        claim.reject("User confirmed factual error")
        self.assertEqual(claim.status, ClaimStatus.REJECTED)
        self.assertTrue(claim.is_terminal)
        self.assertEqual(claim.version, 5)

    def test_terminal_states_cannot_transition(self) -> None:
        claim = Claim(
            user_id="u1",
            purpose="job",
            text="Managed 10 engineers",
        )
        claim.reject("Factually incorrect")
        with self.assertRaises(InvalidStateTransitionError):
            claim.set_supported()
        with self.assertRaises(InvalidStateTransitionError):
            claim.expire("Lapsed")

        expired_claim = Claim(
            user_id="u1",
            purpose="job",
            text="Certified Kubernetes Administrator",
        )
        expired_claim.expire("Certification lapsed")
        self.assertTrue(expired_claim.is_terminal)
        with self.assertRaises(InvalidStateTransitionError):
            expired_claim.set_supported()

    def test_serialization_roundtrip(self) -> None:
        claim = Claim(
            user_id="u1",
            case_id="case_1",
            mission_id="mission_1",
            purpose="scholarship",
            text="GPA 3.9/4.0 in Computer Science",
            supporting_fact_ids=["fact_gpa"],
        )
        claim.set_supported()
        data = claim.to_dict()

        restored = Claim.from_dict(data)
        self.assertEqual(restored.claim_id, claim.claim_id)
        self.assertEqual(restored.user_id, claim.user_id)
        self.assertEqual(restored.case_id, "case_1")
        self.assertEqual(restored.mission_id, "mission_1")
        self.assertEqual(restored.purpose, "scholarship")
        self.assertEqual(restored.status, ClaimStatus.SUPPORTED)
        self.assertEqual(restored.supporting_fact_ids, ["fact_gpa"])
        self.assertEqual(restored.version, 2)


if __name__ == "__main__":
    unittest.main()
