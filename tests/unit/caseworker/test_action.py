"""Unit tests for Action domain entity, fingerprinting, and risk levels."""

import unittest

from caseworker.domain.action import Action
from caseworker.domain.enums import ActionStatus, ActionType, RiskLevel
from caseworker.domain.errors import InvalidStateTransitionError


class TestActionEntity(unittest.TestCase):
    def test_risk_level_inference(self) -> None:
        low_action = Action(
            case_id="case_1",
            action_type=ActionType.DRAFT_APPLICATION,
            description="Draft motivation letter",
            risk_level=RiskLevel.LOW,
        )
        self.assertFalse(low_action.requires_approval)

        high_action = Action(
            case_id="case_1",
            action_type=ActionType.SUBMIT_FORM,
            description="Submit official application with fee",
            risk_level=RiskLevel.HIGH,
        )
        self.assertTrue(high_action.requires_approval)

        crit_action = Action(
            case_id="case_1",
            action_type=ActionType.REQUEST_REFUND,
            description="Authorize binding financial charge dispute",
            risk_level=RiskLevel.CRITICAL,
        )
        self.assertTrue(crit_action.requires_approval)

    def test_canonical_fingerprint_computation(self) -> None:
        act = Action(
            action_id="act_100",
            case_id="case_50",
            action_type=ActionType.SEND_COMMUNICATION,
            description="Send interview confirmation email",
            parameters={"recipient": "hr@corp.com", "slot": "2026-10-05T10:00:00Z"},
        )
        fp1 = act.compute_fingerprint()

        # Changing dict key insertion order must NOT change fingerprint
        act.parameters = {"slot": "2026-10-05T10:00:00Z", "recipient": "hr@corp.com"}
        fp2 = act.compute_fingerprint()
        self.assertEqual(fp1, fp2)

        # Modifying a parameter must change fingerprint
        act.parameters["slot"] = "2026-10-05T14:00:00Z"
        fp3 = act.compute_fingerprint()
        self.assertNotEqual(fp1, fp3)

    def test_action_lifecycle_transitions(self) -> None:
        act = Action(
            case_id="case_1",
            action_type=ActionType.SUBMIT_FORM,
            description="Submit job application",
            risk_level=RiskLevel.HIGH,
        )
        self.assertEqual(act.status, ActionStatus.PROPOSED)

        act.transition_to(ActionStatus.AWAITING_APPROVAL)
        self.assertEqual(act.status, ActionStatus.AWAITING_APPROVAL)

        act.transition_to(ActionStatus.APPROVED)
        self.assertEqual(act.status, ActionStatus.APPROVED)

        act.transition_to(ActionStatus.EXECUTING)
        self.assertEqual(act.status, ActionStatus.EXECUTING)

        act.transition_to(ActionStatus.SUCCEEDED)
        self.assertEqual(act.status, ActionStatus.SUCCEEDED)
        self.assertTrue(act.status.is_terminal)
        self.assertIsNotNone(act.executed_at)

        with self.assertRaises(InvalidStateTransitionError):
            act.transition_to(ActionStatus.PROPOSED)

    def test_serialization_roundtrip(self) -> None:
        act = Action(
            action_id="act_xyz",
            case_id="case_abc",
            action_type=ActionType.REQUEST_REFUND,
            description="Dispute transaction #999",
            parameters={"amount": 49.99, "currency": "EUR"},
            idempotency_key="dispute_tx_999",
        )
        d = act.to_dict()
        self.assertEqual(d["idempotency_key"], "dispute_tx_999")
        self.assertIn("fingerprint", d)

        reconstructed = Action.from_dict(d)
        self.assertEqual(reconstructed.action_id, act.action_id)
        self.assertEqual(reconstructed.compute_fingerprint(), act.compute_fingerprint())
        self.assertEqual(reconstructed.idempotency_key, "dispute_tx_999")


if __name__ == "__main__":
    unittest.main()
