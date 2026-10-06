"""Unit tests for Mission aggregate root."""

from datetime import datetime, timezone
import unittest

from caseworker.domain.enums import MissionKind, MissionStatus
from caseworker.domain.errors import (
    DomainValidationError,
    InvalidStateTransitionError,
    MissionArchivalConflictError,
)
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
        self.assertFalse(reconstructed.archived)
        self.assertIsNone(reconstructed.archived_at)

    def test_cancel_mission_is_terminal_lifecycle_transition(self) -> None:
        m = Mission(
            user_id="u1",
            title="House Hunt",
            goal="Find house",
            kind=MissionKind.GENERAL_GOAL,
        )
        self.assertEqual(m.status, MissionStatus.DRAFT)
        m.transition_to(MissionStatus.ACTIVE)
        self.assertEqual(m.status, MissionStatus.ACTIVE)

        # Cancel from ACTIVE
        m.transition_to(MissionStatus.CANCELLED)
        self.assertEqual(m.status, MissionStatus.CANCELLED)
        self.assertTrue(m.status.is_terminal)

        # Terminal state cannot be transitioned further
        with self.assertRaises(InvalidStateTransitionError):
            m.transition_to(MissionStatus.ACTIVE)

    def test_archive_and_restore_behavior_and_invariants(self) -> None:
        m = Mission(
            user_id="u1",
            title="Archival Test",
            goal="Test archival separation",
            kind=MissionKind.GENERAL_GOAL,
        )
        m.transition_to(MissionStatus.ACTIVE)
        self.assertEqual(m.version, 2)

        # Invariant 1: Active missions cannot be archived
        with self.assertRaises(MissionArchivalConflictError):
            m.archive()
        self.assertFalse(m.archived)
        self.assertEqual(m.version, 2)

        # Pause mission
        m.transition_to(MissionStatus.PAUSED)
        self.assertEqual(m.version, 3)

        # Now archive paused mission
        changed = m.archive()
        self.assertTrue(changed)
        self.assertTrue(m.archived)
        self.assertIsNotNone(m.archived_at)
        self.assertEqual(m.status, MissionStatus.PAUSED)  # Lifecycle status unchanged!
        self.assertEqual(m.version, 4)

        # Idempotence: archiving already archived mission is a no-op
        changed_again = m.archive()
        self.assertFalse(changed_again)
        self.assertEqual(m.version, 4)

        # Invariant 2 (Bidirectional): Cannot transition archived mission to ACTIVE
        with self.assertRaises(MissionArchivalConflictError):
            m.transition_to(MissionStatus.ACTIVE)
        self.assertEqual(m.status, MissionStatus.PAUSED)

        # Restore from archive
        restored = m.restore()
        self.assertTrue(restored)
        self.assertFalse(m.archived)
        self.assertIsNone(m.archived_at)
        self.assertEqual(m.status, MissionStatus.PAUSED)
        self.assertEqual(m.version, 5)

        # Idempotence: restoring already unarchived mission is a no-op
        restored_again = m.restore()
        self.assertFalse(restored_again)
        self.assertEqual(m.version, 5)

        # Now activating succeeds
        m.transition_to(MissionStatus.ACTIVE)
        self.assertEqual(m.status, MissionStatus.ACTIVE)
        self.assertEqual(m.version, 6)

    def test_archived_serialization_roundtrip(self) -> None:
        m = Mission(
            user_id="u1",
            title="Archive Serialization",
            goal="Roundtrip verification",
            kind=MissionKind.GENERAL_GOAL,
        )
        m.archive()
        self.assertTrue(m.archived)
        self.assertIsNotNone(m.archived_at)

        data = m.to_dict()
        self.assertTrue(data["archived"])
        self.assertIsNotNone(data["archived_at"])

        reconstructed = Mission.from_dict(data)
        self.assertTrue(reconstructed.archived)
        self.assertIsNotNone(reconstructed.archived_at)
        self.assertEqual(reconstructed.archived_at, m.archived_at)


if __name__ == "__main__":
    unittest.main()
