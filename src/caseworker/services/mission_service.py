"""Application service orchestrating Mission aggregate operations and domain events."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from caseworker.domain.enums import MissionKind, MissionStatus
from caseworker.domain.errors import EntityNotFoundError
from caseworker.domain.events import (
    make_mission_created_event,
    make_mission_status_changed_event,
)
from caseworker.domain.mission import Mission

if TYPE_CHECKING:
    from caseworker.persistence.sqlite import SQLiteCaseworkerStorage


class MissionService:
    """Application service for managing Mission aggregates with atomic state + event persistence."""

    def __init__(self, storage: SQLiteCaseworkerStorage) -> None:
        self.storage = storage

    def create_mission(
        self,
        user_id: str,
        title: str,
        goal: str,
        kind: MissionKind | str = MissionKind.GENERAL_GOAL,
        success_criteria: list[str] | None = None,
        constraints: list[str] | None = None,
        deadline: datetime | None = None,
    ) -> Mission:
        """Create a new mission, commit state and append MissionCreated event atomically."""
        mission = Mission(
            user_id=user_id,
            title=title,
            goal=goal,
            kind=MissionKind(kind) if isinstance(kind, str) else kind,
            success_criteria=list(success_criteria or []),
            constraints=list(constraints or []),
            deadline=deadline,
        )

        with self.storage.unit_of_work() as uow:
            uow.missions.save(mission)
            event = make_mission_created_event(
                mission_id=mission.mission_id,
                user_id=mission.user_id,
                aggregate_version=mission.version,
                payload=mission.to_dict(),
            )
            uow.events.append(event)

        return mission

    def get_mission(self, mission_id: str) -> Mission | None:
        """Retrieve a mission by its identifier."""
        with self.storage.unit_of_work() as uow:
            return uow.missions.get_by_id(mission_id)

    def list_user_missions(self, user_id: str, status: MissionStatus | None = None) -> list[Mission]:
        """List all missions belonging to a user, optionally filtered by status."""
        with self.storage.unit_of_work() as uow:
            return uow.missions.list_by_user(user_id, status=status)

    def transition_mission(
        self,
        mission_id: str,
        new_status: MissionStatus | str,
        reason: str | None = None,
    ) -> Mission:
        """Transition a mission's state and atomically record state change and event."""
        with self.storage.unit_of_work() as uow:
            mission = uow.missions.get_by_id(mission_id)
            if mission is None:
                raise EntityNotFoundError("Mission", mission_id)

            old_status = mission.status.value
            mission.transition_to(new_status, reason=reason)
            uow.missions.save(mission)

            event = make_mission_status_changed_event(
                mission_id=mission.mission_id,
                user_id=mission.user_id,
                aggregate_version=mission.version,
                old_status=old_status,
                new_status=mission.status.value,
                reason=reason,
            )
            uow.events.append(event)

        return mission
