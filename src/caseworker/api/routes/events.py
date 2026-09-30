"""Domain Events cursor streaming API endpoints using native monotonic rowid position cursors."""

from __future__ import annotations

from typing import Annotated
from fastapi import APIRouter, Depends

from caseworker.api.dependencies import (
    CursorPaginationParams,
    get_principal,
    get_storage,
)
from caseworker.api.identity import Principal
from caseworker.api.schemas.common import CursorPaginatedResponse
from caseworker.api.schemas.events import DomainEventResponse
from caseworker.domain.events import DomainEvent
from caseworker.domain.types import to_iso_utc
from caseworker.persistence.sqlite import SQLiteCaseworkerStorage

router = APIRouter(prefix="/api/v1/events", tags=["events"])


def _to_event_response(pos: int, ev: DomainEvent) -> DomainEventResponse:
    return DomainEventResponse(
        event_id=ev.event_id,
        event_type=ev.event_type,
        aggregate_type=ev.aggregate_type,
        aggregate_id=ev.aggregate_id,
        aggregate_version=ev.aggregate_version,
        user_id=ev.user_id,
        occurred_at=to_iso_utc(ev.occurred_at),
        payload=dict(ev.payload),
        schema_version=ev.schema_version,
        position=pos,
    )


@router.get("", response_model=CursorPaginatedResponse[DomainEventResponse])
async def list_events(
    principal: Annotated[Principal, Depends(get_principal)],
    storage: Annotated[SQLiteCaseworkerStorage, Depends(get_storage)],
    pagination: Annotated[CursorPaginationParams, Depends()],
    aggregate_type: str | None = None,
    aggregate_id: str | None = None,
) -> CursorPaginatedResponse[DomainEventResponse]:
    """Stream domain events for the authenticated user using monotonic cursor pagination."""
    # Fetch limit + 1 to detect whether more events follow
    fetch_limit = pagination.limit + 1
    with storage.unit_of_work() as uow:
        results = uow.events.list_user_events(
            user_id=principal.user_id,
            aggregate_type=aggregate_type,
            aggregate_id=aggregate_id,
            after_position=pagination.after_position,
            limit=fetch_limit,
        )

    has_more = len(results) > pagination.limit
    if has_more:
        results = results[: pagination.limit]

    items = [_to_event_response(pos, ev) for pos, ev in results]
    next_pos = items[-1].position if (has_more and items) else None

    return CursorPaginatedResponse(
        items=items,
        next_position=next_pos,
        has_more=has_more,
        limit=pagination.limit,
    )
