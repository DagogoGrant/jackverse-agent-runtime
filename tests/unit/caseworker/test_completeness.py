"""Unit tests for ProfileCompletenessEvaluator and application requirement sets."""

from datetime import datetime, timedelta
import unittest

from caseworker.domain.completeness import (
    ProfileCompletenessEvaluator,
    ProfileRequirement,
    RequirementSet,
    standard_housing_application_requirements,
    standard_job_application_requirements,
)
from caseworker.domain.context import ContextFact
from caseworker.domain.enums import VerificationStatus
from caseworker.domain.types import now_utc


class TestProfileCompleteness(unittest.TestCase):
    def test_completeness_all_satisfied(self) -> None:
        req_set = RequirementSet(
            purpose="internship",
            title="Internship Requirements",
            requirements=[
                ProfileRequirement("r1", "internship", "identity", "full_name", "Full Name"),
                ProfileRequirement("r2", "internship", "contact", "email", "Email Address"),
            ],
        )
        facts = [
            ContextFact(user_id="u1", namespace="identity", key="full_name", value="Alice Smith"),
            ContextFact(user_id="u1", namespace="contact", key="email", value="alice@test.com"),
        ]
        result = ProfileCompletenessEvaluator.evaluate(req_set, facts)
        self.assertEqual(len(result.satisfied), 2)
        self.assertEqual(len(result.missing), 0)
        self.assertEqual(result.completeness_ratio, 1.0)
        self.assertTrue(result.is_ready)

    def test_completeness_missing_facts(self) -> None:
        req_set = RequirementSet(
            purpose="internship",
            title="Internship Requirements",
            requirements=[
                ProfileRequirement("r1", "internship", "identity", "full_name", "Full Name"),
                ProfileRequirement("r2", "internship", "contact", "email", "Email Address"),
            ],
        )
        facts = [
            ContextFact(user_id="u1", namespace="identity", key="full_name", value="Alice Smith"),
        ]
        result = ProfileCompletenessEvaluator.evaluate(req_set, facts)
        self.assertEqual(len(result.satisfied), 1)
        self.assertEqual(len(result.missing), 1)
        self.assertEqual(result.missing[0].key, "email")
        self.assertEqual(result.completeness_ratio, 0.5)
        self.assertFalse(result.is_ready)

    def test_completeness_unverifiable(self) -> None:
        req_set = RequirementSet(
            purpose="job",
            title="Job Requirements",
            requirements=[
                ProfileRequirement(
                    "r1", "job", "education", "degree", "Degree",
                    minimum_verification=VerificationStatus.USER_VERIFIED,
                ),
            ],
        )
        unverified_fact = ContextFact(
            user_id="u1",
            namespace="education",
            key="degree",
            value="BSc",
            verification_status=VerificationStatus.UNVERIFIED,
        )
        result = ProfileCompletenessEvaluator.evaluate(req_set, [unverified_fact])
        self.assertEqual(len(result.satisfied), 0)
        self.assertEqual(len(result.unverifiable), 1)
        self.assertFalse(result.is_ready)

    def test_completeness_expired(self) -> None:
        req_set = RequirementSet(
            purpose="driving_job",
            title="Driving Job",
            requirements=[
                ProfileRequirement("r1", "driving_job", "documents", "license", "Driving License"),
            ],
        )
        expired_fact = ContextFact(
            user_id="u1",
            namespace="documents",
            key="license",
            value="DL-12345",
            expires_at=now_utc() - timedelta(days=1),
        )
        result = ProfileCompletenessEvaluator.evaluate(req_set, [expired_fact])
        self.assertEqual(len(result.satisfied), 0)
        self.assertEqual(len(result.expired), 1)
        self.assertFalse(result.is_ready)

    def test_standard_job_and_housing_requirement_sets(self) -> None:
        job_reqs = standard_job_application_requirements()
        self.assertEqual(job_reqs.purpose, "job_application")
        self.assertGreater(len(job_reqs.requirements), 0)

        housing_reqs = standard_housing_application_requirements()
        self.assertEqual(housing_reqs.purpose, "housing_search")
        self.assertGreater(len(housing_reqs.requirements), 0)


if __name__ == "__main__":
    unittest.main()
