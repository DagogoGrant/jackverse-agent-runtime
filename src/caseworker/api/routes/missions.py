"""Missions API endpoints."""

from __future__ import annotations

from typing import Annotated
from fastapi import APIRouter, Depends, HTTPException, Request, Response, status

from caseworker.api.dependencies import (
    PaginationParams,
    get_mission_service,
    get_principal,
)
from caseworker.api.etag import check_if_match, set_etag_header
from caseworker.api.identity import Principal
from caseworker.api.schemas.common import PaginatedResponse
from caseworker.api.schemas.mission import (
    ArchiveMissionRequest,
    CreateMissionRequest,
    MissionResponse,
    TransitionMissionRequest,
)
from caseworker.domain.enums import MissionStatus
from caseworker.domain.errors import EntityNotFoundError
from caseworker.domain.mission import Mission
from caseworker.domain.types import to_iso_utc
from caseworker.services.mission_service import MissionService

router = APIRouter(prefix="/api/v1/missions", tags=["missions"])


def _to_mission_response(m: Mission) -> MissionResponse:
    return MissionResponse(
        mission_id=m.mission_id,
        user_id=m.user_id,
        title=m.title,
        goal=m.goal,
        kind=m.kind.value if hasattr(m.kind, "value") else str(m.kind),
        status=m.status.value if hasattr(m.status, "value") else str(m.status),
        success_criteria=list(m.success_criteria),
        constraints=list(m.constraints),
        created_at=to_iso_utc(m.created_at),
        updated_at=to_iso_utc(m.updated_at),
        deadline=to_iso_utc(m.deadline) if m.deadline else None,
        version=m.version,
        archived=m.archived,
        archived_at=to_iso_utc(m.archived_at) if m.archived_at else None,
    )


@router.post("", response_model=MissionResponse, status_code=status.HTTP_201_CREATED)
async def create_mission(
    req: CreateMissionRequest,
    principal: Annotated[Principal, Depends(get_principal)],
    service: Annotated[MissionService, Depends(get_mission_service)],
    response: Response,
) -> MissionResponse:
    """Create a new user mission, binding ownership strictly to the authenticated Principal."""
    effective_goal = req.goal.strip() if (req.goal and req.goal.strip()) else req.title
    mission = service.create_mission(
        user_id=principal.user_id,
        title=req.title,
        goal=effective_goal,
        kind=req.kind,
        success_criteria=req.success_criteria,
        constraints=req.constraints,
        deadline=req.deadline,
    )
    set_etag_header(response, "mission", mission.mission_id, mission.version)
    return _to_mission_response(mission)


@router.get("", response_model=PaginatedResponse[MissionResponse])
async def list_missions(
    principal: Annotated[Principal, Depends(get_principal)],
    service: Annotated[MissionService, Depends(get_mission_service)],
    pagination: Annotated[PaginationParams, Depends()],
    status_filter: MissionStatus | None = None,
    archived: bool = False,
) -> PaginatedResponse[MissionResponse]:
    """List missions belonging to the authenticated user with offset pagination."""
    all_missions = service.list_user_missions(principal.user_id, status=status_filter, archived=archived)
    total = len(all_missions)
    page_items = all_missions[pagination.offset : pagination.offset + pagination.limit]
    return PaginatedResponse(
        items=[_to_mission_response(m) for m in page_items],
        total=total,
        limit=pagination.limit,
        offset=pagination.offset,
    )


@router.get("/{mission_id}", response_model=MissionResponse)
async def get_mission(
    mission_id: str,
    principal: Annotated[Principal, Depends(get_principal)],
    service: Annotated[MissionService, Depends(get_mission_service)],
    response: Response,
) -> MissionResponse:
    """Retrieve mission ensuring strict cross-user isolation (404 on unowned missions)."""
    mission = service.get_mission_for_user(principal.user_id, mission_id)
    if mission is None:
        raise EntityNotFoundError("Mission", mission_id)
    set_etag_header(response, "mission", mission.mission_id, mission.version)
    return _to_mission_response(mission)


@router.post("/{mission_id}/transition", response_model=MissionResponse)
async def transition_mission(
    mission_id: str,
    req: TransitionMissionRequest,
    request: Request,
    response: Response,
    principal: Annotated[Principal, Depends(get_principal)],
    service: Annotated[MissionService, Depends(get_mission_service)],
) -> MissionResponse:
    """Transition mission status with strict ETag precondition verification."""
    existing = service.get_mission_for_user(principal.user_id, mission_id)
    if existing is None:
        raise EntityNotFoundError("Mission", mission_id)

    check_if_match(request, "mission", existing.mission_id, existing.version)

    updated = service.transition_mission_for_user(
        user_id=principal.user_id,
        mission_id=mission_id,
        new_status=req.new_status,
        reason=req.reason,
    )
    set_etag_header(response, "mission", updated.mission_id, updated.version)
    return _to_mission_response(updated)


@router.post("/{mission_id}/pause", response_model=MissionResponse)
async def pause_mission(
    mission_id: str,
    request: Request,
    response: Response,
    principal: Annotated[Principal, Depends(get_principal)],
    service: Annotated[MissionService, Depends(get_mission_service)],
    reason: str | None = None,
) -> MissionResponse:
    """Convenience endpoint to pause an active mission."""
    existing = service.get_mission_for_user(principal.user_id, mission_id)
    if existing is None:
        raise EntityNotFoundError("Mission", mission_id)

    check_if_match(request, "mission", existing.mission_id, existing.version)

    updated = service.transition_mission_for_user(
        user_id=principal.user_id,
        mission_id=mission_id,
        new_status=MissionStatus.PAUSED,
        reason=reason or "Mission paused via API",
    )
    set_etag_header(response, "mission", updated.mission_id, updated.version)
    return _to_mission_response(updated)


@router.post("/{mission_id}/resume", response_model=MissionResponse)
async def resume_mission(
    mission_id: str,
    request: Request,
    response: Response,
    principal: Annotated[Principal, Depends(get_principal)],
    service: Annotated[MissionService, Depends(get_mission_service)],
    reason: str | None = None,
) -> MissionResponse:
    """Convenience endpoint to resume a paused mission."""
    existing = service.get_mission_for_user(principal.user_id, mission_id)
    if existing is None:
        raise EntityNotFoundError("Mission", mission_id)

    check_if_match(request, "mission", existing.mission_id, existing.version)

    updated = service.transition_mission_for_user(
        user_id=principal.user_id,
        mission_id=mission_id,
        new_status=MissionStatus.ACTIVE,
        reason=reason or "Mission resumed via API",
    )
    set_etag_header(response, "mission", updated.mission_id, updated.version)
    return _to_mission_response(updated)


@router.post("/{mission_id}/cancel", response_model=MissionResponse)
async def cancel_mission(
    mission_id: str,
    request: Request,
    response: Response,
    principal: Annotated[Principal, Depends(get_principal)],
    service: Annotated[MissionService, Depends(get_mission_service)],
    reason: str | None = None,
) -> MissionResponse:
    """Convenience endpoint to cancel a mission."""
    existing = service.get_mission_for_user(principal.user_id, mission_id)
    if existing is None:
        raise EntityNotFoundError("Mission", mission_id)

    check_if_match(request, "mission", existing.mission_id, existing.version)

    updated = service.transition_mission_for_user(
        user_id=principal.user_id,
        mission_id=mission_id,
        new_status=MissionStatus.CANCELLED,
        reason=reason or "Mission cancelled via API",
    )
    set_etag_header(response, "mission", updated.mission_id, updated.version)
    return _to_mission_response(updated)


@router.post("/{mission_id}/archive", response_model=MissionResponse)
async def archive_mission(
    mission_id: str,
    request: Request,
    response: Response,
    principal: Annotated[Principal, Depends(get_principal)],
    service: Annotated[MissionService, Depends(get_mission_service)],
) -> MissionResponse:
    """Archive a non-active mission. Idempotent. Requires valid ETag if provided."""
    existing = service.get_mission_for_user(principal.user_id, mission_id)
    if existing is None:
        raise EntityNotFoundError("Mission", mission_id)

    check_if_match(request, "mission", existing.mission_id, existing.version)

    updated = service.archive_mission_for_user(principal.user_id, mission_id)
    set_etag_header(response, "mission", updated.mission_id, updated.version)
    return _to_mission_response(updated)


@router.post("/{mission_id}/restore", response_model=MissionResponse)
async def restore_mission(
    mission_id: str,
    request: Request,
    response: Response,
    principal: Annotated[Principal, Depends(get_principal)],
    service: Annotated[MissionService, Depends(get_mission_service)],
) -> MissionResponse:
    """Restore an archived mission. Idempotent. Requires valid ETag if provided."""
    existing = service.get_mission_for_user(principal.user_id, mission_id)
    if existing is None:
        raise EntityNotFoundError("Mission", mission_id)

    check_if_match(request, "mission", existing.mission_id, existing.version)

    updated = service.restore_mission_for_user(principal.user_id, mission_id)
    set_etag_header(response, "mission", updated.mission_id, updated.version)
    return _to_mission_response(updated)
