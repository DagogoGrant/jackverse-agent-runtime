"""Unit tests for SQLite persistence, Unit-of-Work, optimistic locking, and constraints."""

from datetime import timedelta
import tempfile
import unittest

from caseworker.domain.action import Action
from caseworker.domain.approval import Approval
from caseworker.domain.case import Case
from caseworker.domain.context import ContextFact
from caseworker.domain.enums import (
    ActionStatus,
    ActionType,
    ApprovalStatus,
    CaseStatus,
    CaseType,
    MissionKind,
    MissionStatus,
    OpportunityStatus,
    OpportunityType,
    RiskLevel,
    SensitivityLevel,
    SourceType,
    VerificationStatus,
)
from caseworker.domain.errors import OptimisticLockError, PersistenceError
from caseworker.domain.events import make_case_created_event, make_mission_created_event
from caseworker.domain.mission import Mission
from caseworker.domain.opportunity import Opportunity
from caseworker.domain.types import now_utc
from caseworker.persistence.sqlite import SQLiteCaseworkerStorage


class TestSQLitePersistence(unittest.TestCase):
    def setUp(self) -> None:
        self.storage = SQLiteCaseworkerStorage(":memory:")

    def tearDown(self) -> None:
        self.storage.close()

    def test_schema_initialization_idempotent(self) -> None:
        # Re-initializing storage or creating another storage on same DB must succeed
        with tempfile.NamedTemporaryFile(suffix=".db") as tmp:
            s1 = SQLiteCaseworkerStorage(tmp.name)
            s1.close()
            s2 = SQLiteCaseworkerStorage(tmp.name)
            s2.close()

    def test_mission_crud_and_optimistic_locking(self) -> None:
        mission = Mission(
            user_id="user_1",
            title="Relocate to Berlin",
            goal="Secure flat and job in Berlin",
            kind=MissionKind.OPPORTUNITY_PURSUIT,
        )

        with self.storage.unit_of_work() as uow:
            uow.missions.save(mission)

        # Retrieve
        with self.storage.unit_of_work() as uow:
            retrieved = uow.missions.get_by_id(mission.mission_id)
            self.assertIsNotNone(retrieved)
            assert retrieved is not None
            self.assertEqual(retrieved.title, "Relocate to Berlin")
            self.assertEqual(retrieved.version, 1)

            # Valid update
            retrieved.transition_to(MissionStatus.ACTIVE)
            uow.missions.save(retrieved)

        # Verify updated version
        with self.storage.unit_of_work() as uow:
            updated = uow.missions.get_by_id(mission.mission_id)
            assert updated is not None
            self.assertEqual(updated.status, MissionStatus.ACTIVE)
            self.assertEqual(updated.version, 2)

            # Simulate concurrent conflict with stale version
            stale_mission = Mission(
                mission_id=mission.mission_id,
                user_id=mission.user_id,
                title="Stale Update",
                goal="Goal",
                kind=MissionKind.OPPORTUNITY_PURSUIT,
                version=2,  # pretends to transition from version 1 (2 - 1 = 1), but DB is at version 2
            )
            with self.assertRaises(OptimisticLockError):
                uow.missions.save(stale_mission)

    def test_case_crud_and_mission_linking(self) -> None:
        mission = Mission(
            user_id="user_1",
            title="M",
            goal="G",
            kind=MissionKind.GENERAL_GOAL,
        )
        case = Case(
            user_id="user_1",
            mission_id=mission.mission_id,
            title="C",
            goal="G",
            case_type=CaseType.JOB_APPLICATION,
        )

        with self.storage.unit_of_work() as uow:
            uow.missions.save(mission)
            uow.cases.save(case)

        with self.storage.unit_of_work() as uow:
            cases_for_mission = uow.cases.list_by_mission(mission.mission_id)
            self.assertEqual(len(cases_for_mission), 1)
            self.assertEqual(cases_for_mission[0].case_id, case.case_id)

    def test_opportunity_deduplication_constraint(self) -> None:
        opp1 = Opportunity(
            user_id="user_1",
            opportunity_type=OpportunityType.JOB,
            title="Senior Engineer",
            organization="Google",
            source_url="https://google.com/jobs/1",
        )
        opp2 = Opportunity(
            user_id="user_1",
            opportunity_type=OpportunityType.JOB,
            title="Senior Engineer",
            organization="Google",
            source_url="https://google.com/jobs/1",
        )

        with self.storage.unit_of_work() as uow:
            uow.opportunities.save(opp1)

        # Attempting to save another opportunity with the identical fingerprint for the same user
        with self.storage.unit_of_work() as uow:
            with self.assertRaises(PersistenceError):
                uow.opportunities.save(opp2)

    def test_action_idempotency_constraint(self) -> None:
        case = Case(user_id="user_1", title="C", goal="G", case_type=CaseType.GENERAL)
        act1 = Action(
            case_id=case.case_id,
            action_type=ActionType.SUBMIT_FORM,
            description="Submit",
            idempotency_key="unique_submission_key_1",
        )
        act2 = Action(
            case_id=case.case_id,
            action_type=ActionType.SUBMIT_FORM,
            description="Duplicate Submit",
            idempotency_key="unique_submission_key_1",
        )

        with self.storage.unit_of_work() as uow:
            uow.cases.save(case)
            uow.actions.save(act1)

        with self.storage.unit_of_work() as uow:
            with self.assertRaises(PersistenceError):
                uow.actions.save(act2)

    def test_action_nullable_idempotency_allows_multiple(self) -> None:
        case = Case(user_id="user_1", title="C", goal="G", case_type=CaseType.GENERAL)
        act1 = Action(case_id=case.case_id, action_type=ActionType.CUSTOM, description="A1")
        act2 = Action(case_id=case.case_id, action_type=ActionType.CUSTOM, description="A2")

        with self.storage.unit_of_work() as uow:
            uow.cases.save(case)
            uow.actions.save(act1)
            uow.actions.save(act2)

        with self.storage.unit_of_work() as uow:
            actions = uow.actions.list_by_case(case.case_id)
            self.assertEqual(len(actions), 2)

    def test_approval_crud_and_query(self) -> None:
        case = Case(user_id="user_1", title="C", goal="G", case_type=CaseType.GENERAL)
        act = Action(case_id=case.case_id, action_type=ActionType.CUSTOM, description="A1")
        approval = Approval(
            action_id=act.action_id,
            case_id=case.case_id,
            user_id="user_1",
            action_fingerprint=act.compute_fingerprint(),
        )

        with self.storage.unit_of_work() as uow:
            uow.cases.save(case)
            uow.actions.save(act)
            uow.approvals.save(approval)

        with self.storage.unit_of_work() as uow:
            pending = uow.approvals.list_pending_by_user("user_1")
            self.assertEqual(len(pending), 1)
            self.assertEqual(pending[0].approval_id, approval.approval_id)

    def test_context_facts_active_and_history(self) -> None:
        f1 = ContextFact(
            user_id="u1",
            namespace="profile",
            key="location",
            value="Munich",
            version=1,
        )
        f2 = ContextFact(
            user_id="u1",
            namespace="profile",
            key="location",
            value="Berlin",
            version=2,
        )
        f1.supersede(f2.fact_id)

        with self.storage.unit_of_work() as uow:
            uow.context.save(f1)
            uow.context.save(f2)

        with self.storage.unit_of_work() as uow:
            active = uow.context.list_active("u1", "profile")
            self.assertEqual(len(active), 1)
            self.assertEqual(active[0].value, "Berlin")

            history = uow.context.list_history("u1", "profile", "location")
            self.assertEqual(len(history), 2)
            self.assertEqual(history[0].value, "Munich")
            self.assertEqual(history[1].value, "Berlin")

    def test_event_store_append_and_unique_aggregate_version(self) -> None:
        ev1 = make_mission_created_event("m1", "u1", 1, {"title": "M"})
        ev2 = make_mission_created_event("m1", "u1", 1, {"title": "Duplicate Version"})

        with self.storage.unit_of_work() as uow:
            uow.events.append(ev1)

        # Duplicate version for same aggregate must fail
        with self.storage.unit_of_work() as uow:
            with self.assertRaises(OptimisticLockError):
                uow.events.append(ev2)

        with self.storage.unit_of_work() as uow:
            events = uow.events.get_events_for_aggregate("mission", "m1")
            self.assertEqual(len(events), 1)
            self.assertEqual(events[0].event_id, ev1.event_id)

    def test_atomic_unit_of_work_rollback(self) -> None:
        mission = Mission(user_id="u1", title="Tx Test", goal="G", kind=MissionKind.GENERAL_GOAL)
        event = make_mission_created_event(mission.mission_id, "u1", 1, mission.to_dict())

        # Transaction that raises an exception halfway
        try:
            with self.storage.unit_of_work() as uow:
                uow.missions.save(mission)
                uow.events.append(event)
                raise RuntimeError("Simulated failure before transaction completion")
        except RuntimeError:
            pass

        # Verify zero divergence: neither mission nor event committed
        with self.storage.unit_of_work() as uow:
            self.assertIsNone(uow.missions.get_by_id(mission.mission_id))
            events = uow.events.get_events_for_aggregate("mission", mission.mission_id)
            self.assertEqual(len(events), 0)


if __name__ == "__main__":
    unittest.main()
