"""Unit tests for ClaimLedgerService application service."""

import unittest

from caseworker.domain.enums import ClaimStatus, VerificationStatus
from caseworker.domain.errors import EntityNotFoundError
from caseworker.persistence.sqlite import SQLiteCaseworkerStorage
from caseworker.services.claim_service import ClaimLedgerService
from caseworker.services.vault_service import ContextVaultService


class TestClaimLedgerService(unittest.TestCase):
    def setUp(self) -> None:
        self.storage = SQLiteCaseworkerStorage(":memory:")
        self.vault_service = ContextVaultService(self.storage)
        self.claim_service = ClaimLedgerService(self.storage)
        self.user_id = "user_claim_test"

    def tearDown(self) -> None:
        self.storage.close()

    def test_propose_claim_with_auto_evaluate_supported(self) -> None:
        # Create supporting fact and verify it
        fact = self.vault_service.record_fact(
            user_id=self.user_id,
            namespace="skills",
            key="rust",
            value="3 years production experience",
            allowed_purposes=["job_application"],
            verification_status=VerificationStatus.USER_VERIFIED,
        )

        claim = self.claim_service.propose_claim(
            user_id=self.user_id,
            purpose="job_application",
            text="Experienced Rust developer with 3 years production track record",
            supporting_fact_ids=[fact.fact_id],
            auto_evaluate=True,
        )
        self.assertEqual(claim.status, ClaimStatus.SUPPORTED)
        self.assertTrue(claim.is_supported)
        self.assertIsNotNone(claim.verified_at)

        # Check recorded domain events
        with self.storage.unit_of_work() as uow:
            events = uow.events.get_events_by_user(self.user_id)
            claim_events = [e for e in events if e.aggregate_type == "claim"]
            self.assertEqual(len(claim_events), 2)
            event_types = {e.event_type for e in claim_events}
            self.assertEqual(event_types, {"claim.proposed", "claim.supported"})

    def test_propose_claim_unsupported(self) -> None:
        # Fact is unverified but purpose requires verified
        unverified_fact = self.vault_service.record_fact(
            user_id=self.user_id,
            namespace="education",
            key="degree",
            value="PhD Robotics",
            allowed_purposes=["job_application"],
            verification_status=VerificationStatus.UNVERIFIED,
        )
        claim = self.claim_service.propose_claim(
            user_id=self.user_id,
            purpose="job_application",
            text="Holds PhD in Robotics",
            supporting_fact_ids=[unverified_fact.fact_id],
            auto_evaluate=True,
        )
        self.assertEqual(claim.status, ClaimStatus.UNSUPPORTED)
        self.assertIn("below required", claim.rejection_reason or "")

    def test_propose_claim_conflicted(self) -> None:
        f1 = self.vault_service.record_fact(
            user_id=self.user_id,
            namespace="career",
            key="current_salary",
            value=95000,
            allowed_purposes=["job_application"],
            verification_status=VerificationStatus.USER_VERIFIED,
        )
        f2 = self.vault_service.record_fact(
            user_id=self.user_id,
            namespace="career",
            key="current_salary",
            value=120000,
            allowed_purposes=["job_application"],
            verification_status=VerificationStatus.USER_VERIFIED,
        )
        claim = self.claim_service.propose_claim(
            user_id=self.user_id,
            purpose="job_application",
            text="Salary verification",
            supporting_fact_ids=[f1.fact_id, f2.fact_id],
            auto_evaluate=True,
        )
        self.assertEqual(claim.status, ClaimStatus.CONFLICTED)
        self.assertIn("Contradictory", claim.rejection_reason or "")

    def test_evaluate_claim_on_fact_superseded(self) -> None:
        fact = self.vault_service.record_fact(
            user_id=self.user_id,
            namespace="contact",
            key="address",
            value="123 Main St",
            allowed_purposes=["housing_search"],
            verification_status=VerificationStatus.USER_VERIFIED,
        )
        claim = self.claim_service.propose_claim(
            user_id=self.user_id,
            purpose="housing_search",
            text="Currently resides at 123 Main St",
            supporting_fact_ids=[fact.fact_id],
            auto_evaluate=True,
        )
        self.assertEqual(claim.status, ClaimStatus.SUPPORTED)

        # Now supersede fact
        self.vault_service.supersede_fact(
            old_fact_id=fact.fact_id,
            new_value="456 New Ave",
        )

        # Re-evaluate claim
        reevaluated = self.claim_service.evaluate_claim(claim.claim_id)
        self.assertEqual(reevaluated.status, ClaimStatus.UNSUPPORTED)
        self.assertIn("superseded", reevaluated.rejection_reason or "")

    def test_reject_and_expire_claim(self) -> None:
        claim = self.claim_service.propose_claim(
            user_id=self.user_id,
            purpose="general",
            text="Can start immediately",
            supporting_fact_ids=[],
            auto_evaluate=False,
        )
        self.assertEqual(claim.status, ClaimStatus.PROPOSED)

        rejected = self.claim_service.reject_claim(claim.claim_id, reason="User retracted claim")
        self.assertEqual(rejected.status, ClaimStatus.REJECTED)
        self.assertTrue(rejected.is_terminal)

        # Expire another claim
        claim2 = self.claim_service.propose_claim(
            user_id=self.user_id,
            purpose="general",
            text="Valid through end of month",
            supporting_fact_ids=[],
            auto_evaluate=False,
        )
        expired = self.claim_service.expire_claim(claim2.claim_id, reason="Month ended")
        self.assertEqual(expired.status, ClaimStatus.EXPIRED)
        self.assertTrue(expired.is_terminal)

    def test_query_claims_filtering(self) -> None:
        claim = self.claim_service.propose_claim(
            user_id=self.user_id,
            purpose="scholarship_application",
            text="Published paper in NeurIPS",
            supporting_fact_ids=[],
            auto_evaluate=False,
        )
        by_user = self.claim_service.list_user_claims(self.user_id)
        self.assertEqual(len(by_user), 1)

        by_purpose = self.claim_service.list_purpose_claims(self.user_id, "scholarship_application")
        self.assertEqual(len(by_purpose), 1)

        by_wrong_purpose = self.claim_service.list_purpose_claims(self.user_id, "job_application")
        self.assertEqual(len(by_wrong_purpose), 0)


if __name__ == "__main__":
    unittest.main()
