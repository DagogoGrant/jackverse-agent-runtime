"""Application service orchestrating Case aggregate operations, transitions, and resolutions."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from caseworker.domain.enums import CaseStatus, CaseType
from caseworker.domain.errors import DomainValidationError, EntityNotFoundError
from caseworker.domain.events import (
    make_case_created_event,
    make_case_resolved_event,
    make_case_status_changed_event,
)
from caseworker.domain.case import Case

if TYPE_CHECKING:
    from caseworker.persistence.sqlite import SQLiteCaseworkerStorage


class CaseService:
    """Application service for managing Case aggregates with atomic state + event persistence."""

    def __init__(self, storage: SQLiteCaseworkerStorage) -> None:
        self.storage = storage

    def create_case(
        self,
        user_id: str,
        title: str,
        goal: str,
        case_type: CaseType | str = CaseType.GENERAL,
        mission_id: str | None = None,
        success_criteria: list[str] | None = None,
        constraints: list[str] | None = None,
        deadline: datetime | None = None,
    ) -> Case:
        """Create a new case, verifying optional parent mission existence and saving atomically."""
        with self.storage.unit_of_work() as uow:
            if mission_id is not None:
                mission = uow.missions.get_by_id(mission_id)
                if mission is None:
                    raise EntityNotFoundError("Mission", mission_id)
                if mission.user_id != user_id:
                    raise DomainValidationError(
                        f"Mission '{mission_id}' belongs to user '{mission.user_id}', not '{user_id}'."
                    )


            case = Case(
                user_id=user_id,
                title=title,
                goal=goal,
                case_type=case_type,
                mission_id=mission_id,
                success_criteria=list(success_criteria or []),
                constraints=list(constraints or []),
                deadline=deadline,
            )
            uow.cases.save(case)

            event = make_case_created_event(
                case_id=case.case_id,
                user_id=case.user_id,
                aggregate_version=case.version,
                payload=case.to_dict(),
            )
            uow.events.append(event)

        return case

    def get_case(self, case_id: str) -> Case | None:
        """Retrieve a case by its identifier."""
        with self.storage.unit_of_work() as uow:
            return uow.cases.get_by_id(case_id)

    def get_case_for_user(self, user_id: str, case_id: str) -> Case | None:
        """Retrieve a case by identifier, returning None if non-existent or owned by another user."""
        with self.storage.unit_of_work() as uow:
            case = uow.cases.get_by_id(case_id)
            if case is None or case.user_id != user_id:
                return None
            return case

    def list_user_cases(self, user_id: str, status: CaseStatus | None = None) -> list[Case]:
        """List all cases belonging to a user, optionally filtered by status."""
        with self.storage.unit_of_work() as uow:
            return uow.cases.list_by_user(user_id, status=status)

    def list_mission_cases(self, mission_id: str) -> list[Case]:
        """List all cases linked to a specific mission."""
        with self.storage.unit_of_work() as uow:
            return uow.cases.list_by_mission(mission_id)

    def list_mission_cases_for_user(self, user_id: str, mission_id: str) -> list[Case]:
        """List cases for a mission owned by user, returning empty list if mission not owned."""
        with self.storage.unit_of_work() as uow:
            mission = uow.missions.get_by_id(mission_id)
            if mission is None or mission.user_id != user_id:
                return []
            return [c for c in uow.cases.list_by_mission(mission_id) if c.user_id == user_id]

    def transition_case(
        self,
        case_id: str,
        new_status: CaseStatus | str,
        reason: str | None = None,
    ) -> Case:
        """Transition case state and record state change and event atomically."""
        with self.storage.unit_of_work() as uow:
            case = uow.cases.get_by_id(case_id)
            if case is None:
                raise EntityNotFoundError("Case", case_id)

            old_status = case.status.value
            case.transition_to(new_status, reason=reason)
            uow.cases.save(case)

            event = make_case_status_changed_event(
                case_id=case.case_id,
                user_id=case.user_id,
                aggregate_version=case.version,
                old_status=old_status,
                new_status=case.status.value,
                reason=reason,
            )
            uow.events.append(event)

        return case

    def transition_case_for_user(
        self,
        user_id: str,
        case_id: str,
        new_status: CaseStatus | str,
        reason: str | None = None,
    ) -> Case:
        """Transition case state, raising EntityNotFoundError if non-existent or owned by another user."""
        with self.storage.unit_of_work() as uow:
            case = uow.cases.get_by_id(case_id)
            if case is None or case.user_id != user_id:
                raise EntityNotFoundError("Case", case_id)

            old_status = case.status.value
            case.transition_to(new_status, reason=reason)
            uow.cases.save(case)

            event = make_case_status_changed_event(
                case_id=case.case_id,
                user_id=case.user_id,
                aggregate_version=case.version,
                old_status=old_status,
                new_status=case.status.value,
                reason=reason,
            )
            uow.events.append(event)

        return case

    def resolve_case(self, case_id: str, outcome: str) -> Case:
        """Resolve a case with recorded outcome and emit CaseResolved event atomically."""
        with self.storage.unit_of_work() as uow:
            case = uow.cases.get_by_id(case_id)
            if case is None:
                raise EntityNotFoundError("Case", case_id)

            case.resolve(outcome)
            uow.cases.save(case)

            event = make_case_resolved_event(
                case_id=case.case_id,
                user_id=case.user_id,
                aggregate_version=case.version,
                outcome=outcome,
            )
            uow.events.append(event)

        return case

    def resolve_case_for_user(self, user_id: str, case_id: str, outcome: str) -> Case:
        """Resolve a case, raising EntityNotFoundError if non-existent or owned by another user."""
        with self.storage.unit_of_work() as uow:
            case = uow.cases.get_by_id(case_id)
            if case is None or case.user_id != user_id:
                raise EntityNotFoundError("Case", case_id)

            case.resolve(outcome)
            uow.cases.save(case)

            event = make_case_resolved_event(
                case_id=case.case_id,
                user_id=case.user_id,
                aggregate_version=case.version,
                outcome=outcome,
            )
            uow.events.append(event)

        return case
