"""Cases API endpoints."""

from __future__ import annotations

from typing import Annotated
from fastapi import APIRouter, Depends, Request, Response, status

from caseworker.api.dependencies import (
    PaginationParams,
    get_case_service,
    get_mission_service,
    get_principal,
)
from caseworker.api.etag import check_if_match, set_etag_header
from caseworker.api.identity import Principal
from caseworker.api.schemas.case import (
    CaseResponse,
    CreateCaseRequest,
    ResolveCaseRequest,
    TransitionCaseRequest,
)
from caseworker.api.schemas.common import PaginatedResponse
from caseworker.domain.case import Case
from caseworker.domain.enums import CaseStatus
from caseworker.domain.errors import EntityNotFoundError
from caseworker.domain.types import to_iso_utc
from caseworker.services.case_service import CaseService
from caseworker.services.mission_service import MissionService

router = APIRouter(tags=["cases"])


def _to_case_response(c: Case) -> CaseResponse:
    return CaseResponse(
        case_id=c.case_id,
        mission_id=c.mission_id,
        user_id=c.user_id,
        case_type=c.case_type_str,
        title=c.title,
        goal=c.goal,
        status=c.status.value if hasattr(c.status, "value") else str(c.status),
        success_criteria=list(c.success_criteria),
        constraints=list(c.constraints),
        created_at=to_iso_utc(c.created_at),
        updated_at=to_iso_utc(c.updated_at),
        deadline=to_iso_utc(c.deadline) if c.deadline else None,
        resolved_at=to_iso_utc(c.resolved_at) if c.resolved_at else None,
        outcome=c.outcome,
        version=c.version,
    )


@router.post("/api/v1/cases", response_model=CaseResponse, status_code=status.HTTP_201_CREATED)
async def create_case(
    req: CreateCaseRequest,
    principal: Annotated[Principal, Depends(get_principal)],
    service: Annotated[CaseService, Depends(get_case_service)],
    response: Response,
) -> CaseResponse:
    """Create a new case bound strictly to the authenticated caller."""
    case = service.create_case(
        user_id=principal.user_id,
        title=req.title,
        goal=req.goal,
        case_type=req.case_type,
        mission_id=req.mission_id,
        success_criteria=req.success_criteria,
        constraints=req.constraints,
        deadline=req.deadline,
    )
    set_etag_header(response, "case", case.case_id, case.version)
    return _to_case_response(case)


@router.post("/api/v1/missions/{mission_id}/cases", response_model=CaseResponse, status_code=status.HTTP_201_CREATED)
async def create_mission_case(
    mission_id: str,
    req: CreateCaseRequest,
    principal: Annotated[Principal, Depends(get_principal)],
    service: Annotated[CaseService, Depends(get_case_service)],
    response: Response,
) -> CaseResponse:
    """Convenience endpoint to create a case linked to a verified user mission."""
    case = service.create_case(
        user_id=principal.user_id,
        title=req.title,
        goal=req.goal,
        case_type=req.case_type,
        mission_id=mission_id,
        success_criteria=req.success_criteria,
        constraints=req.constraints,
        deadline=req.deadline,
    )
    set_etag_header(response, "case", case.case_id, case.version)
    return _to_case_response(case)


@router.get("/api/v1/missions/{mission_id}/cases", response_model=list[CaseResponse])
async def list_mission_cases(
    mission_id: str,
    principal: Annotated[Principal, Depends(get_principal)],
    case_service: Annotated[CaseService, Depends(get_case_service)],
    mission_service: Annotated[MissionService, Depends(get_mission_service)],
) -> list[CaseResponse]:
    """List cases for a mission, verifying mission ownership (404 if not found)."""
    mission = mission_service.get_mission_for_user(principal.user_id, mission_id)
    if mission is None:
        raise EntityNotFoundError("Mission", mission_id)

    cases = case_service.list_mission_cases_for_user(principal.user_id, mission_id)
    return [_to_case_response(c) for c in cases]


@router.get("/api/v1/cases", response_model=PaginatedResponse[CaseResponse])
async def list_cases(
    principal: Annotated[Principal, Depends(get_principal)],
    service: Annotated[CaseService, Depends(get_case_service)],
    pagination: Annotated[PaginationParams, Depends()],
    status_filter: CaseStatus | None = None,
) -> PaginatedResponse[CaseResponse]:
    """List cases belonging to the authenticated user."""
    all_cases = service.list_user_cases(principal.user_id, status=status_filter)
    total = len(all_cases)
    page_items = all_cases[pagination.offset : pagination.offset + pagination.limit]
    return PaginatedResponse(
        items=[_to_case_response(c) for c in page_items],
        total=total,
        limit=pagination.limit,
        offset=pagination.offset,
    )


@router.get("/api/v1/cases/{case_id}", response_model=CaseResponse)
async def get_case(
    case_id: str,
    principal: Annotated[Principal, Depends(get_principal)],
    service: Annotated[CaseService, Depends(get_case_service)],
    response: Response,
) -> CaseResponse:
    """Retrieve a case asserting user ownership (404 on unowned cases)."""
    case = service.get_case_for_user(principal.user_id, case_id)
    if case is None:
        raise EntityNotFoundError("Case", case_id)
    set_etag_header(response, "case", case.case_id, case.version)
    return _to_case_response(case)


@router.post("/api/v1/cases/{case_id}/transition", response_model=CaseResponse)
async def transition_case(
    case_id: str,
    req: TransitionCaseRequest,
    request: Request,
    response: Response,
    principal: Annotated[Principal, Depends(get_principal)],
    service: Annotated[CaseService, Depends(get_case_service)],
) -> CaseResponse:
    """Execute state machine transition with ETag precondition enforcement."""
    existing = service.get_case_for_user(principal.user_id, case_id)
    if existing is None:
        raise EntityNotFoundError("Case", case_id)

    check_if_match(request, "case", existing.case_id, existing.version)

    updated = service.transition_case_for_user(
        user_id=principal.user_id,
        case_id=case_id,
        new_status=req.new_status,
        reason=req.reason,
    )
    set_etag_header(response, "case", updated.case_id, updated.version)
    return _to_case_response(updated)


@router.post("/api/v1/cases/{case_id}/resolve", response_model=CaseResponse)
async def resolve_case(
    case_id: str,
    req: ResolveCaseRequest,
    request: Request,
    response: Response,
    principal: Annotated[Principal, Depends(get_principal)],
    service: Annotated[CaseService, Depends(get_case_service)],
) -> CaseResponse:
    """Resolve a case with recorded outcome and ETag precondition enforcement."""
    existing = service.get_case_for_user(principal.user_id, case_id)
    if existing is None:
        raise EntityNotFoundError("Case", case_id)

    check_if_match(request, "case", existing.case_id, existing.version)

    updated = service.resolve_case_for_user(
        user_id=principal.user_id,
        case_id=case_id,
        outcome=req.outcome,
    )
    set_etag_header(response, "case", updated.case_id, updated.version)
    return _to_case_response(updated)
