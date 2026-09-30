"""Unit tests for Case aggregate root."""

from datetime import datetime, timezone
import unittest

from caseworker.domain.case import Case
from caseworker.domain.enums import CaseStatus, CaseType
from caseworker.domain.errors import DomainValidationError, InvalidStateTransitionError
from caseworker.domain.types import now_utc


class TestCaseAggregate(unittest.TestCase):
    def test_create_case_standalone(self) -> None:
        c = Case(
            user_id="user_abc",
            title="Apply to Anthropic Research Engineer",
            goal="Submit tailored application and follow up",
            case_type=CaseType.JOB_APPLICATION,
        )
        self.assertIsNone(c.mission_id)
        self.assertEqual(c.status, CaseStatus.NEW)
        self.assertEqual(c.version, 1)
        self.assertIsNotNone(c.created_at.tzinfo)
        self.assertEqual(c.created_at.tzinfo, timezone.utc)

    def test_create_case_with_mission(self) -> None:
        c = Case(
            user_id="user_abc",
            mission_id="m_123",
            title="Contest Disputed Charge",
            goal="Obtain full refund of 149 EUR",
            case_type=CaseType.REFUND_REQUEST,
        )
        self.assertEqual(c.mission_id, "m_123")

    def test_empty_invariants(self) -> None:
        with self.assertRaises(DomainValidationError):
            Case(user_id="", title="T", goal="G", case_type=CaseType.GENERAL)
        with self.assertRaises(DomainValidationError):
            Case(user_id="u", title="", goal="G", case_type=CaseType.GENERAL)
        with self.assertRaises(DomainValidationError):
            Case(user_id="u", title="T", goal="", case_type=CaseType.GENERAL)

    def test_lifecycle_transitions(self) -> None:
        c = Case(
            user_id="u1",
            title="T",
            goal="G",
            case_type=CaseType.JOB_APPLICATION,
        )
        self.assertEqual(c.status, CaseStatus.NEW)

        c.transition_to(CaseStatus.INTAKE)
        self.assertEqual(c.status, CaseStatus.INTAKE)
        self.assertEqual(c.version, 2)

        c.transition_to(CaseStatus.INVESTIGATING)
        self.assertEqual(c.status, CaseStatus.INVESTIGATING)
        self.assertEqual(c.version, 3)

        c.transition_to(CaseStatus.PLANNING)
        self.assertEqual(c.status, CaseStatus.PLANNING)

        c.transition_to(CaseStatus.ACTION_REQUIRED)
        self.assertEqual(c.status, CaseStatus.ACTION_REQUIRED)

        c.transition_to(CaseStatus.AWAITING_APPROVAL)
        self.assertEqual(c.status, CaseStatus.AWAITING_APPROVAL)

        c.transition_to(CaseStatus.ACTION_IN_PROGRESS)
        self.assertEqual(c.status, CaseStatus.ACTION_IN_PROGRESS)

        c.transition_to(CaseStatus.WAITING_EXTERNAL)
        self.assertEqual(c.status, CaseStatus.WAITING_EXTERNAL)

        c.transition_to(CaseStatus.FOLLOW_UP_DUE)
        self.assertEqual(c.status, CaseStatus.FOLLOW_UP_DUE)

    def test_resolution(self) -> None:
        c = Case(
            user_id="u1",
            title="T",
            goal="G",
            case_type=CaseType.JOB_APPLICATION,
        )
        c.transition_to(CaseStatus.INTAKE)
        c.transition_to(CaseStatus.INVESTIGATING)

        c.resolve("Offer accepted with 110k compensation package.")
        self.assertEqual(c.status, CaseStatus.RESOLVED)
        self.assertTrue(c.status.is_terminal)
        self.assertEqual(c.outcome, "Offer accepted with 110k compensation package.")
        self.assertIsNotNone(c.resolved_at)

        # Cannot transition from terminal state
        with self.assertRaises(InvalidStateTransitionError):
            c.transition_to(CaseStatus.ACTION_REQUIRED)

    def test_serialization_roundtrip(self) -> None:
        c = Case(
            user_id="u1",
            mission_id="m_1",
            title="Visa Application",
            goal="Apply for Blue Card",
            case_type=CaseType.GENERAL,
            success_criteria=["Appointment booked", "Documents verified"],
            constraints=["Must be in English or German"],
            deadline=now_utc(),
        )
        c.transition_to(CaseStatus.INTAKE)

        d = c.to_dict()
        self.assertEqual(d["title"], "Visa Application")
        self.assertEqual(d["status"], "intake")

        reconstructed = Case.from_dict(d)
        self.assertEqual(reconstructed.case_id, c.case_id)
        self.assertEqual(reconstructed.mission_id, "m_1")
        self.assertEqual(reconstructed.status, CaseStatus.INTAKE)
        self.assertEqual(reconstructed.version, 2)


if __name__ == "__main__":
    unittest.main()
