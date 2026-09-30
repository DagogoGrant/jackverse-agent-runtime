"""Unit tests for ContextAccessPolicy and purpose-gating evaluations."""

from datetime import datetime, timedelta
import unittest

from caseworker.domain.context import ContextFact
from caseworker.domain.enums import SensitivityLevel, VerificationStatus
from caseworker.domain.types import now_utc
from caseworker.domain.vault_policy import ContextAccessPolicy


class TestContextAccessPolicy(unittest.TestCase):
    def setUp(self) -> None:
        self.policy = ContextAccessPolicy()

    def test_empty_purpose_denied(self) -> None:
        fact = ContextFact(user_id="u1", namespace="skills", key="python", value="advanced")
        decision = self.policy.evaluate_access(fact, "")
        self.assertFalse(decision.is_granted)
        self.assertIn("cannot be empty", decision.reason)

    def test_rejected_fact_denied(self) -> None:
        fact = ContextFact(user_id="u1", namespace="skills", key="python", value="advanced")
        fact.reject("Candidate requested removal")
        decision = self.policy.evaluate_access(fact, "job_application")
        self.assertFalse(decision.is_granted)
        self.assertIn("rejected", decision.reason)

    def test_superseded_fact_denied(self) -> None:
        fact = ContextFact(user_id="u1", namespace="contact", key="email", value="old@test.com")
        fact.supersede("fact_new_email")
        decision = self.policy.evaluate_access(fact, "contact")
        self.assertFalse(decision.is_granted)
        self.assertIn("superseded", decision.reason)

    def test_expired_fact_denied(self) -> None:
        past_time = now_utc() - timedelta(days=1)
        fact = ContextFact(
            user_id="u1",
            namespace="certifications",
            key="aws_cert",
            value="Solutions Architect",
            expires_at=past_time,
        )
        decision = self.policy.evaluate_access(fact, "job_application")
        self.assertFalse(decision.is_granted)
        self.assertIn("expired", decision.reason)

    def test_confidence_threshold_gating(self) -> None:
        fact = ContextFact(
            user_id="u1",
            namespace="interests",
            key="ai_safety",
            value="keen",
            confidence=0.6,
        )
        # With high required threshold
        decision = self.policy.evaluate_access(fact, "research", min_confidence=0.8)
        self.assertFalse(decision.is_granted)
        self.assertIn("confidence", decision.reason)

        # With lower required threshold
        decision = self.policy.evaluate_access(fact, "research", min_confidence=0.5)
        self.assertTrue(decision.is_granted)

    def test_require_verified_gating(self) -> None:
        unverified_fact = ContextFact(
            user_id="u1",
            namespace="education",
            key="degree",
            value="MSc AI",
            verification_status=VerificationStatus.UNVERIFIED,
        )
        decision = self.policy.evaluate_access(unverified_fact, "job_application", require_verified=True)
        self.assertFalse(decision.is_granted)
        self.assertIn("unverified", decision.reason)

        verified_fact = ContextFact(
            user_id="u1",
            namespace="education",
            key="degree",
            value="MSc AI",
            verification_status=VerificationStatus.USER_VERIFIED,
        )
        decision = self.policy.evaluate_access(verified_fact, "job_application", require_verified=True)
        self.assertTrue(decision.is_granted)

    def test_public_sensitivity_always_accessible(self) -> None:
        public_fact = ContextFact(
            user_id="u1",
            namespace="links",
            key="github",
            value="https://github.com/alice",
            sensitivity=SensitivityLevel.PUBLIC,
        )
        decision = self.policy.evaluate_access(public_fact, "arbitrary_external_purpose")
        self.assertTrue(decision.is_granted)

    def test_allowed_purposes_filtering(self) -> None:
        fact = ContextFact(
            user_id="u1",
            namespace="preferences",
            key="max_rent",
            value=1200,
            allowed_purposes=["housing_search", "financial_planning"],
        )
        # Authorized purpose (case-insensitive check)
        self.assertTrue(self.policy.evaluate_access(fact, "housing_search").is_granted)
        self.assertTrue(self.policy.evaluate_access(fact, "HOUSING_SEARCH").is_granted)
        self.assertTrue(self.policy.evaluate_access(fact, "financial_planning").is_granted)

        # Unauthorized purpose
        decision = self.policy.evaluate_access(fact, "job_application")
        self.assertFalse(decision.is_granted)
        self.assertIn("not authorized", decision.reason)

    def test_sensitive_fact_conservative_default(self) -> None:
        # SENSITIVE fact without explicit allowed_purposes must be blocked by conservative default
        sensitive_fact = ContextFact(
            user_id="u1",
            namespace="identity",
            key="ssn",
            value="000-11-2222",
            sensitivity=SensitivityLevel.SENSITIVE,
            allowed_purposes=[],
        )
        decision = self.policy.evaluate_access(sensitive_fact, "job_application")
        self.assertFalse(decision.is_granted)
        self.assertIn("Sensitive facts require", decision.reason)

        # Authorized when purpose explicitly declared
        authorized_sensitive_fact = ContextFact(
            user_id="u1",
            namespace="identity",
            key="ssn",
            value="000-11-2222",
            sensitivity=SensitivityLevel.SENSITIVE,
            allowed_purposes=["tax_filing"],
        )
        self.assertTrue(self.policy.evaluate_access(authorized_sensitive_fact, "tax_filing").is_granted)


if __name__ == "__main__":
    unittest.main()
