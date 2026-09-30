"""Unit tests for Mission aggregate root."""

from datetime import datetime, timezone
import unittest

from caseworker.domain.enums import MissionKind, MissionStatus
from caseworker.domain.errors import DomainValidationError, InvalidStateTransitionError
from caseworker.domain.mission import Mission
from caseworker.domain.types import now_utc


class TestMissionAggregate(unittest.TestCase):
    def test_create_mission_success(self) -> None:
        m = Mission(
            user_id="user_123",
            title="Land a Lead AI Engineer Role",
            goal="Secure an AI Engineer position in Germany by Q3",
            kind=MissionKind.OPPORTUNITY_PURSUIT,
            success_criteria=["Offer >= 100k", "Hybrid in Munich"],
            constraints=["Must sponsor visa"],
        )
        self.assertEqual(m.status, MissionStatus.DRAFT)
        self.assertEqual(m.version, 1)
        self.assertIsNotNone(m.created_at.tzinfo)
        self.assertEqual(m.created_at.tzinfo, timezone.utc)

    def test_validation_errors_on_empty_fields(self) -> None:
        with self.assertRaises(DomainValidationError):
            Mission(user_id="", title="Goal", goal="Test", kind=MissionKind.GENERAL_GOAL)
        with self.assertRaises(DomainValidationError):
            Mission(user_id="u1", title="", goal="Test", kind=MissionKind.GENERAL_GOAL)
        with self.assertRaises(DomainValidationError):
            Mission(user_id="u1", title="Goal", goal="", kind=MissionKind.GENERAL_GOAL)

    def test_naive_datetime_rejected(self) -> None:
        naive = datetime(2026, 9, 30, 12, 0, 0)
        with self.assertRaises(ValueError):
            Mission(
                user_id="u1",
                title="Goal",
                goal="Test",
                kind=MissionKind.GENERAL_GOAL,
                deadline=naive,
            )

    def test_valid_state_transitions(self) -> None:
        m = Mission(
            user_id="u1",
            title="Goal",
            goal="Test",
            kind=MissionKind.GENERAL_GOAL,
        )
        self.assertEqual(m.version, 1)

        # DRAFT -> ACTIVE
        m.transition_to(MissionStatus.ACTIVE)
        self.assertEqual(m.status, MissionStatus.ACTIVE)
        self.assertEqual(m.version, 2)

        # ACTIVE -> PAUSED
        m.transition_to(MissionStatus.PAUSED)
        self.assertEqual(m.status, MissionStatus.PAUSED)
        self.assertEqual(m.version, 3)

        # PAUSED -> ACTIVE
        m.transition_to(MissionStatus.ACTIVE)
        self.assertEqual(m.status, MissionStatus.ACTIVE)
        self.assertEqual(m.version, 4)

        # ACTIVE -> COMPLETED
        m.transition_to(MissionStatus.COMPLETED)
        self.assertEqual(m.status, MissionStatus.COMPLETED)
        self.assertTrue(m.status.is_terminal)
        self.assertEqual(m.version, 5)

    def test_invalid_state_transitions(self) -> None:
        m = Mission(
            user_id="u1",
            title="Goal",
            goal="Test",
            kind=MissionKind.GENERAL_GOAL,
        )
        # Cannot jump from DRAFT to COMPLETED
        with self.assertRaises(InvalidStateTransitionError):
            m.transition_to(MissionStatus.COMPLETED)

        # Transition to terminal state CANCELLED
        m.transition_to(MissionStatus.CANCELLED)
        self.assertTrue(m.status.is_terminal)

        # Cannot transition out of terminal state
        with self.assertRaises(InvalidStateTransitionError):
            m.transition_to(MissionStatus.ACTIVE)

    def test_serialization_roundtrip(self) -> None:
        deadline = now_utc()
        m = Mission(
            user_id="u1",
            title="Find Apartment",
            goal="2-bedroom in Munich",
            kind=MissionKind.OPPORTUNITY_PURSUIT,
            success_criteria=["Balcony", "Near U-Bahn"],
            constraints=["Budget <= 1800"],
            deadline=deadline,
        )
        m.transition_to(MissionStatus.ACTIVE)

        d = m.to_dict()
        self.assertEqual(d["title"], "Find Apartment")
        self.assertEqual(d["status"], "active")
        self.assertEqual(d["version"], 2)

        reconstructed = Mission.from_dict(d)
        self.assertEqual(reconstructed.mission_id, m.mission_id)
        self.assertEqual(reconstructed.title, m.title)
        self.assertEqual(reconstructed.status, MissionStatus.ACTIVE)
        self.assertEqual(reconstructed.version, 2)
        self.assertEqual(reconstructed.deadline.isoformat(), deadline.isoformat())


if __name__ == "__main__":
    unittest.main()
