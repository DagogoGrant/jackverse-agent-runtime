"""Unit tests for ContextVaultService application service."""

import unittest

from caseworker.domain.completeness import RequirementSet, ProfileRequirement
from caseworker.domain.enums import SensitivityLevel, SourceType, VerificationStatus
from caseworker.domain.errors import DomainValidationError, EntityNotFoundError
from caseworker.persistence.sqlite import SQLiteCaseworkerStorage
from caseworker.services.vault_service import ContextVaultService


class TestContextVaultService(unittest.TestCase):
    def setUp(self) -> None:
        self.storage = SQLiteCaseworkerStorage(":memory:")
        self.service = ContextVaultService(self.storage)
        self.user_id = "user_service_test"

    def tearDown(self) -> None:
        self.storage.close()

    def test_register_and_list_sources(self) -> None:
        source = self.service.register_source(
            user_id=self.user_id,
            title="Degree Certificate",
            source_type=SourceType.DOCUMENT,
            source_reference="s3://vault/degree.pdf",
            metadata={"institution": "Passau"},
        )
        self.assertIsNotNone(source.source_id)
        self.assertEqual(source.title, "Degree Certificate")

        # Verify query
        retrieved = self.service.get_source(source.source_id)
        self.assertIsNotNone(retrieved)
        self.assertEqual(retrieved.title, "Degree Certificate")

        # Verify listing
        sources = self.service.list_sources(self.user_id)
        self.assertEqual(len(sources), 1)

        # Verify domain event recorded
        with self.storage.unit_of_work() as uow:
            events = uow.events.get_events_by_user(self.user_id)
            source_events = [e for e in events if e.event_type == "context.source_registered"]
            self.assertEqual(len(source_events), 1)
            self.assertEqual(source_events[0].payload["title"], "Degree Certificate")

    def test_record_fact_with_valid_source(self) -> None:
        source = self.service.register_source(
            user_id=self.user_id,
            title="LinkedIn",
            source_type=SourceType.PROFILE_IMPORT,
        )
        fact = self.service.record_fact(
            user_id=self.user_id,
            namespace="career",
            key="current_role",
            value="Staff Engineer",
            source_id=source.source_id,
            verification_status=VerificationStatus.USER_VERIFIED,
        )
        self.assertEqual(fact.key, "current_role")
        self.assertEqual(fact.source_id, source.source_id)
        self.assertEqual(fact.source_type, SourceType.PROFILE_IMPORT)

        # Query by source
        derived_facts = self.service.get_facts_by_source(source.source_id)
        self.assertEqual(len(derived_facts), 1)
        self.assertEqual(derived_facts[0].fact_id, fact.fact_id)

    def test_record_fact_with_invalid_source_raises(self) -> None:
        with self.assertRaises(EntityNotFoundError):
            self.service.record_fact(
                user_id=self.user_id,
                namespace="career",
                key="role",
                value="Lead",
                source_id="non_existent_source_id",
            )

    def test_agent_inference_invariant(self) -> None:
        # AGENT_INFERENCE is provenance, NOT verification: forced to UNVERIFIED
        fact = self.service.record_fact(
            user_id=self.user_id,
            namespace="interests",
            key="favorite_topic",
            value="Neuroscience",
            source_type=SourceType.AGENT_INFERENCE,
            verification_status=VerificationStatus.SOURCE_VERIFIED,  # Should be overridden to UNVERIFIED
        )
        self.assertEqual(fact.verification_status, VerificationStatus.UNVERIFIED)

    def test_supersede_fact(self) -> None:
        old_fact = self.service.record_fact(
            user_id=self.user_id,
            namespace="contact",
            key="city",
            value="Berlin",
        )
        old_res, new_fact = self.service.supersede_fact(
            old_fact_id=old_fact.fact_id,
            new_value="Munich",
            reason="Relocated",
        )
        self.assertFalse(old_res.is_active)
        self.assertEqual(old_res.superseded_by_fact_id, new_fact.fact_id)
        self.assertTrue(new_fact.is_active)
        self.assertEqual(new_fact.value, "Munich")

        # History query returns both
        history = self.service.list_history(self.user_id, "contact", "city")
        self.assertEqual(len(history), 2)

    def test_verify_and_reject_fact(self) -> None:
        fact = self.service.record_fact(
            user_id=self.user_id,
            namespace="education",
            key="gpa",
            value=3.95,
        )
        self.assertEqual(fact.verification_status, VerificationStatus.UNVERIFIED)

        verified = self.service.verify_fact(fact.fact_id, VerificationStatus.USER_VERIFIED)
        self.assertEqual(verified.verification_status, VerificationStatus.USER_VERIFIED)

        rejected = self.service.reject_fact(fact.fact_id, reason="Typo in GPA")
        self.assertEqual(rejected.verification_status, VerificationStatus.REJECTED)
        self.assertFalse(rejected.is_active)

    def test_create_context_package_and_events(self) -> None:
        self.service.record_fact(
            user_id=self.user_id,
            namespace="skills",
            key="python",
            value="Expert",
            allowed_purposes=["job_application"],
        )
        pkg = self.service.create_context_package(
            user_id=self.user_id,
            purpose="job_application",
        )
        self.assertEqual(len(pkg.facts), 1)
        self.assertEqual(pkg.facts[0].key, "python")

        # Verify access granted event
        with self.storage.unit_of_work() as uow:
            events = uow.events.get_events_by_user(self.user_id)
            granted = [e for e in events if e.event_type == "context.access_granted"]
            self.assertEqual(len(granted), 1)
            self.assertEqual(granted[0].payload["purpose"], "job_application")

    def test_evaluate_completeness(self) -> None:
        req_set = RequirementSet(
            purpose="onboarding",
            title="User Onboarding",
            requirements=[
                ProfileRequirement("r1", "onboarding", "identity", "username", "Username"),
            ],
        )
        self.service.record_fact(
            user_id=self.user_id,
            namespace="identity",
            key="username",
            value="johndoe",
        )
        result = self.service.evaluate_completeness(self.user_id, req_set)
        self.assertTrue(result.is_ready)
        self.assertEqual(result.completeness_ratio, 1.0)


if __name__ == "__main__":
    unittest.main()
