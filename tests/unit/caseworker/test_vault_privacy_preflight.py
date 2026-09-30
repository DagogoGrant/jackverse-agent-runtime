"""Regression test verifying that sensitive rejection reasons do not leak into audit event payloads."""

from __future__ import annotations

import json
import unittest

from caseworker.domain.enums import VerificationStatus
from caseworker.domain.vault_policy import ContextAccessPolicy
from caseworker.persistence.sqlite import SQLiteCaseworkerStorage
from caseworker.services.vault_service import ContextVaultService


class TestVaultPrivacyPreflight(unittest.TestCase):
    def setUp(self) -> None:
        self.storage = SQLiteCaseworkerStorage(":memory:")
        self.service = ContextVaultService(self.storage)
        self.user_id = "user_privacy_test"

    def tearDown(self) -> None:
        self.storage.close()

    def test_sensitive_rejection_reason_does_not_leak_into_denied_event(self) -> None:
        """Verify that user-provided rejection reason text is never written to context.access_denied event payloads."""
        sensitive_reason = "Fact has been rejected because my passport number is 987-654-321 and tax ID is 11-223344"

        # Record a fact
        fact = self.service.record_fact(
            user_id=self.user_id,
            namespace="identity",
            key="passport_number",
            value="SECRET_PASSPORT_VAL",
            allowed_purposes=["travel_booking"],
            verification_status=VerificationStatus.USER_VERIFIED,
        )

        # Reject it with sensitive reason text
        rejected_fact = self.service.reject_fact(fact.fact_id, reason=sensitive_reason)

        # Evaluate access directly on the rejected fact:
        policy = ContextAccessPolicy()
        decision = policy.evaluate_access(rejected_fact, requested_purpose="travel_booking")
        self.assertFalse(decision.is_granted)
        self.assertEqual(decision.reason_code, "FACT_REJECTED")

        # Now simulate generating a denied event using the factory
        from caseworker.domain.events import make_context_access_denied_event
        event = make_context_access_denied_event(
            user_id=self.user_id,
            aggregate_version=1,
            purpose="travel_booking",
            fact_id=fact.fact_id,
            reason=decision.reason,  # Contains the sensitive text
            reason_code=decision.reason_code,
            namespace=fact.namespace,
            key=fact.key,
        )

        payload_json = json.dumps(event.payload)
        self.assertNotIn("987-654-321", payload_json)
        self.assertNotIn("11-223344", payload_json)
        self.assertNotIn(sensitive_reason, payload_json)
        self.assertEqual(event.payload["reason"], "Fact has been rejected")
        self.assertEqual(event.payload["reason_code"], "FACT_REJECTED")


if __name__ == "__main__":
    unittest.main()
