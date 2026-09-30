"""Unit tests for MissionService and CaseService application services."""

import unittest

from caseworker.domain.enums import CaseStatus, CaseType, MissionKind, MissionStatus
from caseworker.domain.errors import EntityNotFoundError, InvalidStateTransitionError
from caseworker.persistence.sqlite import SQLiteCaseworkerStorage
from caseworker.services.case_service import CaseService
from caseworker.services.mission_service import MissionService


class TestCaseworkerServices(unittest.TestCase):
    def setUp(self) -> None:
        self.storage = SQLiteCaseworkerStorage(":memory:")
        self.mission_service = MissionService(self.storage)
        self.case_service = CaseService(self.storage)

    def tearDown(self) -> None:
        self.storage.close()

    def test_mission_service_create_and_event(self) -> None:
        mission = self.mission_service.create_mission(
            user_id="user_1",
            title="Land AI Engineering Role",
            goal="Secure an AI Engineering offer",
            kind=MissionKind.OPPORTUNITY_PURSUIT,
            success_criteria=["Comp >= 110k", "Full-time"],
        )
        self.assertEqual(mission.status, MissionStatus.DRAFT)

        # Verify event emitted in event store
        with self.storage.unit_of_work() as uow:
            events = uow.events.get_events_for_aggregate("mission", mission.mission_id)
            self.assertEqual(len(events), 1)
            self.assertEqual(events[0].event_type, "mission.created")
            self.assertEqual(events[0].payload["title"], "Land AI Engineering Role")

    def test_mission_service_transition_and_event(self) -> None:
        mission = self.mission_service.create_mission(
            user_id="user_1",
            title="Goal",
            goal="Goal Description",
        )
        updated = self.mission_service.transition_mission(
            mission_id=mission.mission_id,
            new_status=MissionStatus.ACTIVE,
            reason="User activated goal",
        )
        self.assertEqual(updated.status, MissionStatus.ACTIVE)
        self.assertEqual(updated.version, 2)

        with self.storage.unit_of_work() as uow:
            events = uow.events.get_events_for_aggregate("mission", mission.mission_id)
            self.assertEqual(len(events), 2)
            self.assertEqual(events[1].event_type, "mission.status_changed")
            self.assertEqual(events[1].payload["new_status"], "active")
            self.assertEqual(events[1].payload["reason"], "User activated goal")

    def test_mission_service_not_found(self) -> None:
        with self.assertRaises(EntityNotFoundError):
            self.mission_service.transition_mission("nonexistent_id", MissionStatus.ACTIVE)

    def test_case_service_create_standalone(self) -> None:
        case = self.case_service.create_case(
            user_id="user_1",
            title="File Complaint for Delayed Flight",
            goal="Receive 600 EUR compensation",
            case_type=CaseType.SERVICE_COMPLAINT,
        )
        self.assertEqual(case.status, CaseStatus.NEW)
        self.assertIsNone(case.mission_id)

        with self.storage.unit_of_work() as uow:
            events = uow.events.get_events_for_aggregate("case", case.case_id)
            self.assertEqual(len(events), 1)
            self.assertEqual(events[0].event_type, "case.created")

    def test_case_service_create_linked_to_mission(self) -> None:
        mission = self.mission_service.create_mission(
            user_id="user_1",
            title="Relocation",
            goal="Move to Germany",
        )
        case = self.case_service.create_case(
            user_id="user_1",
            mission_id=mission.mission_id,
            title="Apply for Visa",
            goal="Submit Visa application",
            case_type=CaseType.GENERAL,
        )
        self.assertEqual(case.mission_id, mission.mission_id)

        # Listing cases by mission
        cases = self.case_service.list_mission_cases(mission.mission_id)
        self.assertEqual(len(cases), 1)
        self.assertEqual(cases[0].case_id, case.case_id)

    def test_case_service_rejects_nonexistent_mission(self) -> None:
        with self.assertRaises(EntityNotFoundError):
            self.case_service.create_case(
                user_id="user_1",
                mission_id="nonexistent_mission_id",
                title="Apply",
                goal="Goal",
            )

    def test_case_service_transition_and_resolve(self) -> None:
        case = self.case_service.create_case(
            user_id="user_1",
            title="Refund Request",
            goal="Refund 50 EUR",
            case_type=CaseType.REFUND_REQUEST,
        )
        self.case_service.transition_case(case.case_id, CaseStatus.INTAKE)
        self.case_service.transition_case(case.case_id, CaseStatus.INVESTIGATING)

        resolved = self.case_service.resolve_case(case.case_id, "Refund credited back to credit card.")
        self.assertEqual(resolved.status, CaseStatus.RESOLVED)
        self.assertEqual(resolved.outcome, "Refund credited back to credit card.")

        with self.storage.unit_of_work() as uow:
            events = uow.events.get_events_for_aggregate("case", case.case_id)
            self.assertEqual(len(events), 4)  # created, intake, investigating, resolved
            self.assertEqual(events[-1].event_type, "case.resolved")
            self.assertEqual(events[-1].payload["outcome"], "Refund credited back to credit card.")


if __name__ == "__main__":
    unittest.main()
