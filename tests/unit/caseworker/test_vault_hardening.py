"""Unit tests for Milestone 2.1 Security & Integrity Hardening Pass.

Covers:
1. Monotonic versioning of context vault access events without collisions.
2. Structured reason codes and payload minimization for denied access.
3. Cross-user source rejection in supersede_fact with atomic rollback.
4. Cross-user case rejection in propose_claim with atomic rollback.
5. Cross-user mission rejection in propose_claim with atomic rollback.
6. Case-Mission relationship validation in propose_claim with atomic rollback.
7. Cross-user mission rejection in CaseService.create_case with atomic rollback.
8. Cross-user supporting fact rejection in propose_claim with atomic rollback.
9. Privacy audit ensuring sensitive values, credentials, and full claim prose are omitted from event log.
10. Atomic rollback on event store failure.
"""

from __future__ import annotations

import json
import unittest
from unittest.mock import patch

from caseworker.domain.enums import (
    ClaimStatus,
    MissionKind,
    SensitivityLevel,
    SourceType,
    VerificationStatus,
)
from caseworker.domain.errors import DomainValidationError, EntityNotFoundError
from caseworker.domain.events import (
    compute_sha256,
    make_claim_proposed_event,
    make_context_access_denied_event,
    make_context_access_granted_event,
)
from caseworker.domain.vault_policy import ContextAccessPolicy
from caseworker.persistence.sqlite import SQLiteCaseworkerStorage, SQLiteEventStore
from caseworker.services.case_service import CaseService
from caseworker.services.claim_service import ClaimLedgerService
from caseworker.services.mission_service import MissionService
from caseworker.services.vault_service import ContextVaultService


class TestVaultSecurityHardening(unittest.TestCase):
    def setUp(self) -> None:
        self.storage = SQLiteCaseworkerStorage(":memory:")
        self.access_policy = ContextAccessPolicy(default_min_confidence=0.5)
        self.vault_service = ContextVaultService(self.storage, access_policy=self.access_policy)
        self.mission_service = MissionService(self.storage)
        self.case_service = CaseService(self.storage)
        self.claim_service = ClaimLedgerService(self.storage)
        self.user_alice = "user_alice_security"
        self.user_bob = "user_bob_security"

    def tearDown(self) -> None:
        self.storage.close()

    def test_repeated_context_access_monotonic_versioning(self) -> None:
        """Verify that repeated access evaluations for a user produce monotonic aggregate_versions without collision."""
        # Alice creates 2 facts for 'job_application' and 2 facts for 'tax_filing'
        f1 = self.vault_service.record_fact(
            user_id=self.user_alice,
            namespace="career",
            key="title",
            value="Staff Engineer",
            allowed_purposes=["job_application"],
            verification_status=VerificationStatus.USER_VERIFIED,
        )
        f2 = self.vault_service.record_fact(
            user_id=self.user_alice,
            namespace="career",
            key="years_exp",
            value=10,
            allowed_purposes=["job_application"],
            verification_status=VerificationStatus.USER_VERIFIED,
        )
        f3 = self.vault_service.record_fact(
            user_id=self.user_alice,
            namespace="finance",
            key="w2_income",
            value=180000,
            allowed_purposes=["tax_filing"],
            verification_status=VerificationStatus.USER_VERIFIED,
        )
        f4 = self.vault_service.record_fact(
            user_id=self.user_alice,
            namespace="finance",
            key="deductions",
            value=12000,
            allowed_purposes=["tax_filing"],
            verification_status=VerificationStatus.USER_VERIFIED,
        )

        # Call 1: purpose='job_application' -> 2 facts granted (1 granted event), 2 facts denied (2 denied events)
        # Sequence: versions 1, 2, 3
        pkg1 = self.vault_service.create_context_package(
            user_id=self.user_alice,
            purpose="job_application",
        )
        self.assertEqual(len(pkg1.facts), 2)

        # Call 2: purpose='job_application' again -> 2 facts granted (1 granted event), 2 denied (2 denied events)
        # Sequence: versions 4, 5, 6
        pkg2 = self.vault_service.create_context_package(
            user_id=self.user_alice,
            purpose="job_application",
        )
        self.assertEqual(len(pkg2.facts), 2)

        # Call 3: purpose='tax_filing' -> 2 facts granted (1 granted event), 2 denied (2 denied events)
        # Sequence: versions 7, 8, 9
        pkg3 = self.vault_service.create_context_package(
            user_id=self.user_alice,
            purpose="tax_filing",
        )
        self.assertEqual(len(pkg3.facts), 2)

        # Query all access events for user_alice
        with self.storage.unit_of_work() as uow:
            events = uow.events.get_events_by_user(self.user_alice)
            vault_events = [e for e in events if e.aggregate_type == "context_vault"]

            # There should be exactly 3 granted events and 6 denied events = 9 events total
            granted_events = [e for e in vault_events if e.event_type == "context.access_granted"]
            denied_events = [e for e in vault_events if e.event_type == "context.access_denied"]

            self.assertEqual(len(granted_events), 3)
            self.assertEqual(len(denied_events), 6)
            self.assertEqual(len(vault_events), 9)

            # Ensure aggregate_version contains 1..9 with no duplicates
            versions = sorted([e.aggregate_version for e in vault_events])
            self.assertEqual(versions, list(range(1, 10)))

            # Verify each event has correct aggregate_type and aggregate_id
            for e in vault_events:
                self.assertEqual(e.aggregate_type, "context_vault")
                self.assertEqual(e.aggregate_id, self.user_alice)

    def test_denied_access_structured_reason_codes_and_payload_minimization(self) -> None:
        """Verify that denied access events contain structured reason codes and do not leak fact values."""
        # 1. Fact rejected due to purpose mismatch
        f_purpose = self.vault_service.record_fact(
            user_id=self.user_alice,
            namespace="finance",
            key="bank_account",
            value="SECRET_ACCOUNT_123",
            allowed_purposes=["tax_filing"],
            verification_status=VerificationStatus.USER_VERIFIED,
        )

        # 2. Fact rejected due to SENSITIVE without explicit allowed_purposes
        f_sensitive = self.vault_service.record_fact(
            user_id=self.user_alice,
            namespace="health",
            key="diagnosis",
            value="SECRET_HEALTH_CONDITION",
            allowed_purposes=[],
            sensitivity=SensitivityLevel.SENSITIVE,
            verification_status=VerificationStatus.USER_VERIFIED,
        )

        # 3. Fact rejected due to UNVERIFIED status when require_verified=True
        f_unverified = self.vault_service.record_fact(
            user_id=self.user_alice,
            namespace="skills",
            key="c_plus_plus",
            value="SECRET_SKILL_PROSE",
            allowed_purposes=["job_search"],
            verification_status=VerificationStatus.UNVERIFIED,
        )

        # 4. Fact rejected due to low confidence (0.3 < 0.5)
        f_low_conf = self.vault_service.record_fact(
            user_id=self.user_alice,
            namespace="skills",
            key="rust",
            value="SECRET_RUST_PROSE",
            allowed_purposes=["job_search"],
            confidence=0.3,
            verification_status=VerificationStatus.USER_VERIFIED,
        )

        # Build package for 'job_search' with require_verified=True
        pkg = self.vault_service.create_context_package(
            user_id=self.user_alice,
            purpose="job_search",
            require_verified=True,
        )
        self.assertEqual(len(pkg.facts), 0)

        with self.storage.unit_of_work() as uow:
            events = uow.events.get_events_by_user(self.user_alice)
            denied_events = [e for e in events if e.event_type == "context.access_denied"]

            self.assertEqual(len(denied_events), 4)
            reason_codes = {e.payload["reason_code"] for e in denied_events}
            self.assertIn("PURPOSE_NOT_AUTHORIZED", reason_codes)
            self.assertIn("SENSITIVE_FACT_RESTRICTED", reason_codes)
            self.assertIn("UNVERIFIED_FACT", reason_codes)
            self.assertIn("LOW_CONFIDENCE", reason_codes)

            # Check that NONE of the denied event payloads or reasons leak secret values
            for e in denied_events:
                payload_str = json.dumps(e.payload)
                self.assertNotIn("SECRET_ACCOUNT_123", payload_str)
                self.assertNotIn("SECRET_HEALTH_CONDITION", payload_str)
                self.assertNotIn("SECRET_SKILL_PROSE", payload_str)
                self.assertNotIn("SECRET_RUST_PROSE", payload_str)
                self.assertNotIn("value", e.payload)
                self.assertTrue(len(e.payload["namespace"]) > 0)
                self.assertTrue(len(e.payload["key"]) > 0)
                self.assertTrue(len(e.payload["reason_code"]) > 0)

    def test_cross_user_source_rejection_in_supersede_fact(self) -> None:
        """Verify that superseding a fact using another user's context source is rejected and rolled back."""
        # Alice creates a fact
        alice_fact = self.vault_service.record_fact(
            user_id=self.user_alice,
            namespace="profile",
            key="email",
            value="alice@example.com",
            verification_status=VerificationStatus.USER_VERIFIED,
        )

        # Bob creates a source
        bob_source = self.vault_service.register_source(
            user_id=self.user_bob,
            title="Bob Document",
            source_type=SourceType.DOCUMENT,
        )

        # Attempt to supersede Alice's fact using Bob's source
        with self.assertRaises(DomainValidationError) as ctx:
            self.vault_service.supersede_fact(
                old_fact_id=alice_fact.fact_id,
                new_value="alice_new@example.com",
                new_source_id=bob_source.source_id,
            )
        self.assertIn("belongs to user 'user_bob_security', not user 'user_alice_security'", str(ctx.exception))

        # Verify rollback: Alice's fact is still active and unchanged
        refreshed = self.vault_service.get_fact(alice_fact.fact_id)
        self.assertIsNotNone(refreshed)
        self.assertTrue(refreshed.is_active)
        self.assertIsNone(refreshed.superseded_by_fact_id)
        self.assertEqual(refreshed.version, 1)

        # Ensure no new facts or supersession events exist for Alice
        active_facts = self.vault_service.list_active_facts(self.user_alice)
        self.assertEqual(len(active_facts), 1)
        self.assertEqual(active_facts[0].value, "alice@example.com")

    def test_cross_user_case_rejection_in_propose_claim(self) -> None:
        """Verify that proposing a claim referencing another user's case is rejected and rolled back."""
        # Alice creates a case
        alice_case = self.case_service.create_case(
            user_id=self.user_alice,
            title="Alice Case",
            goal="Secure contract",
        )

        # Bob tries to propose a claim referencing Alice's case
        with self.assertRaises(DomainValidationError) as ctx:
            self.claim_service.propose_claim(
                user_id=self.user_bob,
                purpose="general",
                text="Bob's assertion",
                supporting_fact_ids=[],
                case_id=alice_case.case_id,
            )
        self.assertIn("belongs to user 'user_alice_security', not 'user_bob_security'", str(ctx.exception))

        # Verify rollback: Bob has no claims
        bob_claims = self.claim_service.list_user_claims(self.user_bob)
        self.assertEqual(len(bob_claims), 0)

    def test_cross_user_mission_rejection_in_propose_claim(self) -> None:
        """Verify that proposing a claim referencing another user's mission is rejected and rolled back."""
        # Alice creates a mission
        alice_mission = self.mission_service.create_mission(
            user_id=self.user_alice,
            title="Alice Mission",
            goal="Q4 Revenue",
            kind=MissionKind.GENERAL_GOAL,
        )

        # Bob tries to propose a claim referencing Alice's mission
        with self.assertRaises(DomainValidationError) as ctx:
            self.claim_service.propose_claim(
                user_id=self.user_bob,
                purpose="general",
                text="Bob's assertion",
                supporting_fact_ids=[],
                mission_id=alice_mission.mission_id,
            )
        self.assertIn("belongs to user 'user_alice_security', not 'user_bob_security'", str(ctx.exception))

        # Verify rollback
        bob_claims = self.claim_service.list_user_claims(self.user_bob)
        self.assertEqual(len(bob_claims), 0)

    def test_case_mission_relationship_mismatch_in_propose_claim(self) -> None:
        """Verify that proposing a claim where case_id and mission_id do not match is rejected and rolled back."""
        # Alice creates two missions and links a case to mission 1
        m1 = self.mission_service.create_mission(
            user_id=self.user_alice,
            title="Mission 1",
            goal="Goal 1",
            kind=MissionKind.GENERAL_GOAL,
        )
        m2 = self.mission_service.create_mission(
            user_id=self.user_alice,
            title="Mission 2",
            goal="Goal 2",
            kind=MissionKind.GENERAL_GOAL,
        )
        c1 = self.case_service.create_case(
            user_id=self.user_alice,
            title="Case 1",
            goal="Goal C1",
            mission_id=m1.mission_id,
        )

        # Propose claim with c1 and m2 (mismatch)
        with self.assertRaises(DomainValidationError) as ctx:
            self.claim_service.propose_claim(
                user_id=self.user_alice,
                purpose="general",
                text="Assertion",
                supporting_fact_ids=[],
                case_id=c1.case_id,
                mission_id=m2.mission_id,
            )
        self.assertIn("is associated with mission", str(ctx.exception))

        # Verify rollback
        alice_claims = self.claim_service.list_user_claims(self.user_alice)
        self.assertEqual(len(alice_claims), 0)

    def test_cross_user_mission_rejection_in_create_case(self) -> None:
        """Verify that creating a case referencing another user's mission is rejected and rolled back."""
        alice_mission = self.mission_service.create_mission(
            user_id=self.user_alice,
            title="Alice Mission",
            goal="Goal A",
            kind=MissionKind.GENERAL_GOAL,
        )

        # Bob attempts to create a case under Alice's mission
        with self.assertRaises(DomainValidationError) as ctx:
            self.case_service.create_case(
                user_id=self.user_bob,
                title="Bob Case",
                goal="Goal B",
                mission_id=alice_mission.mission_id,
            )
        self.assertIn("belongs to user 'user_alice_security', not 'user_bob_security'", str(ctx.exception))

        # Verify rollback: Bob has no cases
        bob_cases = self.case_service.list_user_cases(self.user_bob)
        self.assertEqual(len(bob_cases), 0)

    def test_cross_user_supporting_fact_rejection_in_propose_claim(self) -> None:
        """Verify that proposing a claim referencing another user's context fact is rejected and rolled back."""
        alice_fact = self.vault_service.record_fact(
            user_id=self.user_alice,
            namespace="credentials",
            key="license",
            value="PMP-12345",
            allowed_purposes=["job_application"],
            verification_status=VerificationStatus.USER_VERIFIED,
        )

        # Bob attempts to claim Alice's PMP license
        with self.assertRaises(DomainValidationError) as ctx:
            self.claim_service.propose_claim(
                user_id=self.user_bob,
                purpose="job_application",
                text="Certified PMP License Holder",
                supporting_fact_ids=[alice_fact.fact_id],
            )
        self.assertIn("belongs to user 'user_alice_security', not 'user_bob_security'", str(ctx.exception))

        # Verify rollback
        bob_claims = self.claim_service.list_user_claims(self.user_bob)
        self.assertEqual(len(bob_claims), 0)

    def test_privacy_audit_events_do_not_leak_sensitive_values(self) -> None:
        """Verify that sensitive values, credentials, and full claim prose DO NOT appear in any event payloads."""
        secret_ssn = "999-00-1111"
        secret_token = "BEARER_SECRET_TOKEN_XYZ_987"
        secret_diagnosis = "RareCardiovascularCondition"
        secret_salary = "$275,000"
        secret_claim_prose = "I solemnly swear I earn $275,000 and my SSN is 999-00-1111"

        # 1. Register source with private reference
        source = self.vault_service.register_source(
            user_id=self.user_alice,
            title="Alice Tax Return",
            source_type=SourceType.DOCUMENT,
            source_reference=f"https://irs.gov/doc?token={secret_token}",
            metadata={"secret_auth": secret_token},
        )

        # 2. Record facts with sensitive values
        f_ssn = self.vault_service.record_fact(
            user_id=self.user_alice,
            namespace="identity",
            key="ssn",
            value=secret_ssn,
            source_id=source.source_id,
            source_reference=f"page_1_{secret_token}",
            allowed_purposes=["tax_filing"],
            verification_status=VerificationStatus.USER_VERIFIED,
        )
        f_salary = self.vault_service.record_fact(
            user_id=self.user_alice,
            namespace="career",
            key="base_salary",
            value=secret_salary,
            source_id=source.source_id,
            allowed_purposes=["job_application"],
            verification_status=VerificationStatus.USER_VERIFIED,
        )
        f_health = self.vault_service.record_fact(
            user_id=self.user_alice,
            namespace="health",
            key="condition",
            value=secret_diagnosis,
            source_id=source.source_id,
            sensitivity=SensitivityLevel.SENSITIVE,
            allowed_purposes=["insurance_claim"],
            verification_status=VerificationStatus.USER_VERIFIED,
        )

        # 3. Supersede salary fact
        old_sal, new_sal = self.vault_service.supersede_fact(
            old_fact_id=f_salary.fact_id,
            new_value="$300,000",
            new_source_id=source.source_id,
            new_source_reference=f"supersede_ref_{secret_token}",
        )

        # 4. Propose claim
        claim = self.claim_service.propose_claim(
            user_id=self.user_alice,
            purpose="job_application",
            text=secret_claim_prose,
            supporting_fact_ids=[new_sal.fact_id],
            auto_evaluate=True,
        )

        # 5. Access context packages (one matching, one denying)
        self.vault_service.create_context_package(
            user_id=self.user_alice,
            purpose="job_application",
        )
        self.vault_service.create_context_package(
            user_id=self.user_alice,
            purpose="insurance_claim",
        )

        # Retrieve ALL events for Alice
        with self.storage.unit_of_work() as uow:
            events = uow.events.get_events_by_user(self.user_alice)
            self.assertGreater(len(events), 5)

            # Assert claim proposed payload uses claim_text_hash
            claim_events = [e for e in events if e.event_type == "claim.proposed"]
            self.assertEqual(len(claim_events), 1)
            proposed_payload = claim_events[0].payload
            self.assertIn("claim_text_hash", proposed_payload)
            self.assertEqual(proposed_payload["claim_text_hash"], compute_sha256(secret_claim_prose.strip()))
            self.assertNotIn("text", proposed_payload)

            # Audit every single event in Alice's stream
            secrets = [
                secret_ssn,
                secret_token,
                secret_diagnosis,
                secret_salary,
                "$300,000",
                secret_claim_prose,
            ]
            for ev in events:
                payload_json = json.dumps(ev.payload)
                for secret in secrets:
                    self.assertNotIn(
                        secret,
                        payload_json,
                        f"Secret '{secret}' leaked in event {ev.event_type} payload: {payload_json}",
                    )

    def test_atomic_rollback_on_event_store_failure(self) -> None:
        """Verify that if an event append fails, the whole unit-of-work rolls back atomically."""
        with patch.object(
            SQLiteEventStore,
            "append",
            side_effect=RuntimeError("Simulated EventStore Failure"),
        ):
            with self.assertRaises(RuntimeError):
                self.vault_service.record_fact(
                    user_id=self.user_alice,
                    namespace="skills",
                    key="python",
                    value="Expert",
                )

        # Verify fact was NOT saved
        facts = self.vault_service.list_active_facts(self.user_alice)
        self.assertEqual(len(facts), 0)


if __name__ == "__main__":
    unittest.main()
