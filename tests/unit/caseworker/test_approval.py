"""Unit tests for Approval entity and fingerprint binding validation."""

from datetime import datetime, timedelta
import unittest

from caseworker.domain.action import Action
from caseworker.domain.approval import Approval
from caseworker.domain.enums import ActionType, ApprovalStatus, RiskLevel
from caseworker.domain.errors import ApprovalValidationError, InvalidStateTransitionError
from caseworker.domain.types import now_utc


class TestApprovalEntity(unittest.TestCase):
    def setUp(self) -> None:
        self.action = Action(
            action_id="act_001",
            case_id="case_001",
            action_type=ActionType.SUBMIT_FORM,
            description="Submit job application to OpenAI",
            parameters={"resume_id": "res_123", "cover_letter_id": "cl_456"},
            risk_level=RiskLevel.HIGH,
        )
        self.fingerprint = self.action.compute_fingerprint()

    def test_approval_binding_success(self) -> None:
        app = Approval(
            action_id=self.action.action_id,
            case_id=self.action.case_id,
            user_id="user_1",
            action_fingerprint=self.fingerprint,
        )
        self.assertEqual(app.status, ApprovalStatus.PENDING)
        self.assertFalse(app.is_valid_for(self.action))  # Not approved yet

        app.approve(self.action, reason="Reviewed and confirmed")
        self.assertEqual(app.status, ApprovalStatus.APPROVED)
        self.assertIsNotNone(app.decided_at)
        self.assertEqual(app.reason, "Reviewed and confirmed")
        self.assertTrue(app.is_valid_for(self.action))

    def test_approval_fails_if_action_tampered(self) -> None:
        app = Approval(
            action_id=self.action.action_id,
            case_id=self.action.case_id,
            user_id="user_1",
            action_fingerprint=self.fingerprint,
        )

        # Action parameters modified after approval request was generated
        self.action.parameters["resume_id"] = "res_MALICIOUS_SUBSTITUTION"

        # Attempting to approve tampered action must raise ApprovalValidationError
        with self.assertRaises(ApprovalValidationError):
            app.approve(self.action)

    def test_approval_rejection_and_expiration(self) -> None:
        app = Approval(
            action_id=self.action.action_id,
            case_id=self.action.case_id,
            user_id="user_1",
            action_fingerprint=self.fingerprint,
        )
        app.reject("User declined application fee")
        self.assertEqual(app.status, ApprovalStatus.REJECTED)
        self.assertFalse(app.is_valid_for(self.action))

        # Cannot transition once rejected
        with self.assertRaises(InvalidStateTransitionError):
            app.approve(self.action)

    def test_expired_approval(self) -> None:
        past_time = now_utc() - timedelta(minutes=10)
        app = Approval(
            action_id=self.action.action_id,
            case_id=self.action.case_id,
            user_id="user_1",
            action_fingerprint=self.fingerprint,
            expires_at=past_time,
        )
        self.assertTrue(app.is_expired)
        with self.assertRaises(ApprovalValidationError):
            app.approve(self.action)

    def test_serialization_roundtrip(self) -> None:
        app = Approval(
            action_id=self.action.action_id,
            case_id=self.action.case_id,
            user_id="user_1",
            action_fingerprint=self.fingerprint,
            expires_at=now_utc() + timedelta(days=1),
        )
        d = app.to_dict()
        self.assertEqual(d["status"], "pending")
        self.assertEqual(d["action_fingerprint"], self.fingerprint)

        reconstructed = Approval.from_dict(d)
        self.assertEqual(reconstructed.approval_id, app.approval_id)
        self.assertEqual(reconstructed.action_fingerprint, self.fingerprint)


if __name__ == "__main__":
    unittest.main()
