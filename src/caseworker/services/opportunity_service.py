"""Application service managing Opportunity discovery, deduplication, and lifecycle transitions."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any

from caseworker.domain.enums import OpportunityStatus, OpportunityType
from caseworker.domain.errors import EntityNotFoundError
from caseworker.domain.events import (
    make_opportunity_discovered_event,
    make_opportunity_status_changed_event,
)
from caseworker.domain.opportunity import Opportunity

if TYPE_CHECKING:
    from caseworker.persistence.sqlite import SQLiteCaseworkerStorage


class OpportunityService:
    """Application service managing external opportunities with user-scoped deduplication and atomic persistence."""

    def __init__(self, storage: SQLiteCaseworkerStorage) -> None:
        self.storage = storage

    def create_opportunity(
        self,
        user_id: str,
        title: str,
        opportunity_type: OpportunityType | str,
        organization: str = "",
        source_url: str = "",
        source_name: str = "",
        location: str = "",
        status: OpportunityStatus | str = OpportunityStatus.DISCOVERED,
        requirements: list[str] | None = None,
        metadata: dict[str, Any] | None = None,
        deadline: datetime | None = None,
        mission_id: str | None = None,
    ) -> tuple[Opportunity, bool]:
        """Create an opportunity or return an existing deduplicated record scoped to this user."""
        with self.storage.unit_of_work() as uow:
            if mission_id is not None:
                mission = uow.missions.get_by_id(mission_id)
                if mission is None or mission.user_id != user_id:
                    raise EntityNotFoundError("Mission", mission_id)

            opp = Opportunity(
                user_id=user_id,
                title=title,
                opportunity_type=opportunity_type,
                organization=organization,
                source_url=source_url,
                source_name=source_name,
                location=location,
                status=OpportunityStatus(status) if isinstance(status, str) else status,
                requirements=list(requirements or []),
                metadata=dict(metadata or {}),
                deadline=deadline,
                mission_id=mission_id,
            )

            # User-scoped deduplication check
            existing = uow.opportunities.get_by_fingerprint(user_id, opp.fingerprint)
            if existing is not None:
                return existing, False

            uow.opportunities.save(opp)
            event = make_opportunity_discovered_event(
                opportunity_id=opp.opportunity_id,
                user_id=opp.user_id,
                aggregate_version=opp.version,
                payload=opp.to_dict(),
            )
            uow.events.append(event)
            return opp, True

    def get_opportunity(self, opportunity_id: str) -> Opportunity | None:
        """Retrieve an opportunity by ID."""
        with self.storage.unit_of_work() as uow:
            return uow.opportunities.get_by_id(opportunity_id)

    def get_opportunity_for_user(self, user_id: str, opportunity_id: str) -> Opportunity | None:
        """Retrieve an opportunity by ID, asserting user ownership."""
        with self.storage.unit_of_work() as uow:
            opp = uow.opportunities.get_by_id(opportunity_id)
            if opp is None or opp.user_id != user_id:
                return None
            return opp

    def list_user_opportunities(
        self,
        user_id: str,
        status: OpportunityStatus | None = None,
    ) -> list[Opportunity]:
        """List opportunities belonging to a user, optionally filtered by status."""
        with self.storage.unit_of_work() as uow:
            return uow.opportunities.list_by_user(user_id, status=status)

    def list_mission_opportunities_for_user(
        self,
        user_id: str,
        mission_id: str,
    ) -> list[Opportunity]:
        """List opportunities for a mission, asserting user ownership of the mission."""
        with self.storage.unit_of_work() as uow:
            mission = uow.missions.get_by_id(mission_id)
            if mission is None or mission.user_id != user_id:
                return []
            return [o for o in uow.opportunities.list_by_mission(mission_id) if o.user_id == user_id]

    def transition_opportunity_for_user(
        self,
        user_id: str,
        opportunity_id: str,
        new_status: OpportunityStatus | str,
        reason: str | None = None,
    ) -> Opportunity:
        """Transition an opportunity's state, asserting user ownership."""
        with self.storage.unit_of_work() as uow:
            opp = uow.opportunities.get_by_id(opportunity_id)
            if opp is None or opp.user_id != user_id:
                raise EntityNotFoundError("Opportunity", opportunity_id)

            old_status = opp.status.value
            opp.transition_to(new_status, reason=reason)
            uow.opportunities.save(opp)

            event = make_opportunity_status_changed_event(
                opportunity_id=opp.opportunity_id,
                user_id=opp.user_id,
                aggregate_version=opp.version,
                old_status=old_status,
                new_status=opp.status.value,
                reason=reason,
            )
            uow.events.append(event)
            return opp
