"""Unit tests for DomainEvent immutability, factories, and versioning."""

from datetime import datetime
import unittest

from caseworker.domain.events import (
    DomainEvent,
    make_action_proposed_event,
    make_approval_requested_event,
    make_case_created_event,
    make_case_resolved_event,
    make_case_status_changed_event,
    make_context_fact_created_event,
    make_mission_created_event,
    make_mission_status_changed_event,
    make_opportunity_discovered_event,
)
from caseworker.domain.types import now_utc


class TestDomainEvents(unittest.TestCase):
    def test_domain_event_immutability(self) -> None:
        ev = DomainEvent(
            event_type="test.event",
            aggregate_type="test",
            aggregate_id="123",
            aggregate_version=1,
            user_id="user_1",
            payload={"key": "val"},
        )
        with self.assertRaises(Exception):  # FrozenInstanceError
            ev.event_type = "mutated"  # type: ignore

    def test_aggregate_version_validation(self) -> None:
        with self.assertRaises(ValueError):
            DomainEvent(
                event_type="test.event",
                aggregate_type="test",
                aggregate_id="123",
                aggregate_version=0,  # Version must be >= 1
                user_id="user_1",
                payload={},
            )

    def test_naive_datetime_rejected(self) -> None:
        naive = datetime(2026, 9, 30, 10, 0, 0)
        with self.assertRaises(ValueError):
            DomainEvent(
                event_type="test.event",
                aggregate_type="test",
                aggregate_id="123",
                aggregate_version=1,
                user_id="user_1",
                payload={},
                occurred_at=naive,
            )

    def test_concrete_factories(self) -> None:
        ev_mission = make_mission_created_event("m1", "u1", 1, {"title": "Find Work"})
        self.assertEqual(ev_mission.event_type, "mission.created")
        self.assertEqual(ev_mission.aggregate_type, "mission")

        ev_case = make_case_created_event("c1", "u1", 1, {"title": "Apply"})
        self.assertEqual(ev_case.event_type, "case.created")
        self.assertEqual(ev_case.aggregate_id, "c1")

        ev_resolved = make_case_resolved_event("c1", "u1", 2, "Offer accepted")
        self.assertEqual(ev_resolved.event_type, "case.resolved")
        self.assertEqual(ev_resolved.payload["outcome"], "Offer accepted")

        ev_action = make_action_proposed_event("a1", "c1", "u1", 1, {"description": "Draft letter"})
        self.assertEqual(ev_action.event_type, "action.proposed")

        ev_app = make_approval_requested_event("app1", "a1", "c1", "u1", 1, "fingerprint_123")
        self.assertEqual(ev_app.event_type, "approval.requested")
        self.assertEqual(ev_app.payload["action_fingerprint"], "fingerprint_123")

    def test_serialization_roundtrip(self) -> None:
        ev = make_case_status_changed_event("c1", "u1", 3, "new", "intake", reason="Intake begun")
        d = ev.to_dict()
        self.assertEqual(d["event_type"], "case.status_changed")
        self.assertEqual(d["aggregate_version"], 3)
        self.assertEqual(d["payload"]["old_status"], "new")

        reconstructed = DomainEvent.from_dict(d)
        self.assertEqual(reconstructed.event_id, ev.event_id)
        self.assertEqual(reconstructed.event_type, ev.event_type)
        self.assertEqual(reconstructed.aggregate_version, 3)
        self.assertEqual(reconstructed.payload, ev.payload)


if __name__ == "__main__":
    unittest.main()
