"""Unit tests for ClaimVerificationPolicy support evaluation and contradiction detection."""

from datetime import datetime, timedelta
import unittest

from caseworker.domain.claim import Claim
from caseworker.domain.claim_policy import ClaimVerificationPolicy
from caseworker.domain.context import ContextFact
from caseworker.domain.enums import ClaimStatus, SensitivityLevel, VerificationStatus
from caseworker.domain.types import now_utc


class TestClaimVerificationPolicy(unittest.TestCase):
    def setUp(self) -> None:
        self.policy = ClaimVerificationPolicy()

    def test_evaluate_support_no_supporting_facts(self) -> None:
        claim = Claim(
            user_id="u1",
            purpose="job_application",
            text="Experienced in Rust",
            supporting_fact_ids=[],
        )
        status, reason = self.policy.evaluate_support(claim, [])
        self.assertEqual(status, ClaimStatus.UNSUPPORTED)
        self.assertIn("No supporting facts", reason or "")

    def test_evaluate_support_missing_declared_fact(self) -> None:
        claim = Claim(
            user_id="u1",
            purpose="job_application",
            text="Experienced in Rust",
            supporting_fact_ids=["fact_rust_1", "fact_rust_2"],
        )
        # Only 1 fact provided in list
        f1 = ContextFact(
            fact_id="fact_rust_1",
            user_id="u1",
            namespace="skills",
            key="rust",
            value="3 years",
            verification_status=VerificationStatus.USER_VERIFIED,
        )
        status, reason = self.policy.evaluate_support(claim, [f1])
        self.assertEqual(status, ClaimStatus.UNSUPPORTED)
        self.assertIn("fact_rust_2", reason or "")

    def test_evaluate_support_user_mismatch(self) -> None:
        claim = Claim(
            user_id="u1",
            purpose="job_application",
            text="Experienced in Go",
            supporting_fact_ids=["fact_go"],
        )
        alien_fact = ContextFact(
            fact_id="fact_go",
            user_id="other_user",
            namespace="skills",
            key="go",
            value="5 years",
            verification_status=VerificationStatus.USER_VERIFIED,
        )
        status, reason = self.policy.evaluate_support(claim, [alien_fact])
        self.assertEqual(status, ClaimStatus.UNSUPPORTED)
        self.assertIn("does not belong to user", reason or "")

    def test_evaluate_support_lifecycle_guards(self) -> None:
        claim = Claim(
            user_id="u1",
            purpose="job_application",
            text="Certified in Kubernetes",
            supporting_fact_ids=["fact_cka"],
        )
        # Expired fact
        expired_fact = ContextFact(
            fact_id="fact_cka",
            user_id="u1",
            namespace="certifications",
            key="cka",
            value="CKA-2023",
            verification_status=VerificationStatus.USER_VERIFIED,
            expires_at=now_utc() - timedelta(days=1),
        )
        status, reason = self.policy.evaluate_support(claim, [expired_fact])
        self.assertEqual(status, ClaimStatus.UNSUPPORTED)
        self.assertIn("expired", reason or "")

        # Superseded fact
        superseded_fact = ContextFact(
            fact_id="fact_cka",
            user_id="u1",
            namespace="certifications",
            key="cka",
            value="CKA-2023",
            verification_status=VerificationStatus.USER_VERIFIED,
            superseded_by_fact_id="fact_cka_2026",
        )
        status, reason = self.policy.evaluate_support(claim, [superseded_fact])
        self.assertEqual(status, ClaimStatus.UNSUPPORTED)
        self.assertIn("superseded", reason or "")

        # Rejected fact
        rejected_fact = ContextFact(
            fact_id="fact_cka",
            user_id="u1",
            namespace="certifications",
            key="cka",
            value="CKA-2023",
            verification_status=VerificationStatus.REJECTED,
        )
        status, reason = self.policy.evaluate_support(claim, [rejected_fact])
        self.assertEqual(status, ClaimStatus.UNSUPPORTED)
        self.assertIn("rejected", reason or "")

    def test_evaluate_support_verification_threshold(self) -> None:
        claim = Claim(
            user_id="u1",
            purpose="job_application",  # requires USER_VERIFIED
            text="BSc Computer Science",
            supporting_fact_ids=["fact_bsc"],
        )
        unverified_fact = ContextFact(
            fact_id="fact_bsc",
            user_id="u1",
            namespace="education",
            key="bsc",
            value="CS",
            verification_status=VerificationStatus.UNVERIFIED,
        )
        status, reason = self.policy.evaluate_support(claim, [unverified_fact])
        self.assertEqual(status, ClaimStatus.UNSUPPORTED)
        self.assertIn("below required", reason or "")

    def test_evaluate_support_contradiction_detection(self) -> None:
        claim = Claim(
            user_id="u1",
            purpose="job_application",
            text="Graduated in 2024 with Honors",
            supporting_fact_ids=["fact_grad_1", "fact_grad_2"],
        )
        fact1 = ContextFact(
            fact_id="fact_grad_1",
            user_id="u1",
            namespace="education",
            key="graduation_year",
            value=2024,
            verification_status=VerificationStatus.USER_VERIFIED,
        )
        fact2 = ContextFact(
            fact_id="fact_grad_2",
            user_id="u1",
            namespace="education",
            key="graduation_year",
            value=2025,  # Contradicts fact1!
            verification_status=VerificationStatus.USER_VERIFIED,
        )
        status, reason = self.policy.evaluate_support(claim, [fact1, fact2])
        self.assertEqual(status, ClaimStatus.CONFLICTED)
        self.assertIn("Contradictory values detected", reason or "")

    def test_evaluate_support_success(self) -> None:
        claim = Claim(
            user_id="u1",
            purpose="job_application",
            text="Senior Python Developer with 6 years experience",
            supporting_fact_ids=["fact_py", "fact_exp"],
        )
        fact1 = ContextFact(
            fact_id="fact_py",
            user_id="u1",
            namespace="skills",
            key="python",
            value="Advanced",
            verification_status=VerificationStatus.USER_VERIFIED,
        )
        fact2 = ContextFact(
            fact_id="fact_exp",
            user_id="u1",
            namespace="career",
            key="total_experience_years",
            value=6,
            verification_status=VerificationStatus.SOURCE_VERIFIED,
        )
        status, reason = self.policy.evaluate_support(claim, [fact1, fact2])
        self.assertEqual(status, ClaimStatus.SUPPORTED)
        self.assertIsNone(reason)


if __name__ == "__main__":
    unittest.main()
