"""Unit tests for ContextFact entity, provenance, superseding, and purpose gating."""

from datetime import datetime, timedelta
import unittest

from caseworker.domain.context import ContextFact
from caseworker.domain.enums import SensitivityLevel, SourceType, VerificationStatus
from caseworker.domain.errors import DomainValidationError
from caseworker.domain.types import now_utc


class TestContextFactEntity(unittest.TestCase):
    def test_fact_creation_and_provenance(self) -> None:
        fact = ContextFact(
            user_id="user_1",
            namespace="education",
            key="master_degree",
            value={"institution": "University of Passau", "field": "AI", "year": 2026},
            source_type=SourceType.DOCUMENT,
            source_reference="diploma_scan.pdf",
            confidence=0.98,
            verification_status=VerificationStatus.SOURCE_VERIFIED,
            sensitivity=SensitivityLevel.PERSONAL,
            allowed_purposes=["job_application", "scholarship"],
        )
        self.assertEqual(fact.source_type, SourceType.DOCUMENT)
        self.assertEqual(fact.confidence, 0.98)
        self.assertEqual(fact.verification_status, VerificationStatus.SOURCE_VERIFIED)
        self.assertTrue(fact.is_active)

    def test_confidence_boundary_validation(self) -> None:
        with self.assertRaises(DomainValidationError):
            ContextFact(
                user_id="u1",
                namespace="profile",
                key="name",
                value="Alice",
                confidence=1.5,
            )
        with self.assertRaises(DomainValidationError):
            ContextFact(
                user_id="u1",
                namespace="profile",
                key="name",
                value="Alice",
                confidence=-0.1,
            )

    def test_purpose_gating(self) -> None:
        restricted_fact = ContextFact(
            user_id="u1",
            namespace="legal",
            key="passport_number",
            value="X12345678",
            sensitivity=SensitivityLevel.SENSITIVE,
            allowed_purposes=["visa_application", "official_registration"],
        )
        self.assertTrue(restricted_fact.is_valid_for_purpose("visa_application"))
        self.assertTrue(restricted_fact.is_valid_for_purpose("official_registration"))
        self.assertFalse(restricted_fact.is_valid_for_purpose("job_application"))
        self.assertFalse(restricted_fact.is_valid_for_purpose("marketing"))

        public_fact = ContextFact(
            user_id="u1",
            namespace="profile",
            key="github_url",
            value="https://github.com/alice",
            sensitivity=SensitivityLevel.PUBLIC,
        )
        self.assertTrue(public_fact.is_valid_for_purpose("any_purpose"))

    def test_superseding_lifecycle(self) -> None:
        old_fact = ContextFact(
            user_id="u1",
            namespace="contact",
            key="phone",
            value="+49 151 0000000",
        )
        self.assertTrue(old_fact.is_active)
        self.assertIsNone(old_fact.superseded_by_fact_id)

        old_fact.supersede("fact_new_phone_999")
        self.assertFalse(old_fact.is_active)
        self.assertEqual(old_fact.superseded_by_fact_id, "fact_new_phone_999")
        self.assertIsNotNone(old_fact.superseded_at)
        self.assertEqual(old_fact.version, 2)

        # Cannot supersede twice
        with self.assertRaises(DomainValidationError):
            old_fact.supersede("fact_another_id")

    def test_serialization_roundtrip(self) -> None:
        fact = ContextFact(
            user_id="u1",
            namespace="preferences",
            key="remote_only",
            value=True,
            allowed_purposes=["job_search"],
        )
        d = fact.to_dict()
        self.assertEqual(d["key"], "remote_only")
        self.assertEqual(d["value"], True)

        reconstructed = ContextFact.from_dict(d)
        self.assertEqual(reconstructed.fact_id, fact.fact_id)
        self.assertEqual(reconstructed.value, True)
        self.assertEqual(reconstructed.allowed_purposes, ["job_search"])


if __name__ == "__main__":
    unittest.main()
