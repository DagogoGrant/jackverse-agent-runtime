"""Opportunities API endpoints."""

from __future__ import annotations

from typing import Annotated
from fastapi import APIRouter, Depends, Request, Response, status

from caseworker.api.dependencies import (
    PaginationParams,
    get_opportunity_service,
    get_principal,
)
from caseworker.api.etag import check_if_match, set_etag_header
from caseworker.api.identity import Principal
from caseworker.api.schemas.common import PaginatedResponse
from caseworker.api.schemas.opportunity import (
    CreateOpportunityRequest,
    OpportunityResponse,
    TransitionOpportunityRequest,
)
from caseworker.domain.enums import OpportunityStatus
from caseworker.domain.errors import EntityNotFoundError
from caseworker.domain.opportunity import Opportunity
from caseworker.domain.types import to_iso_utc
from caseworker.services.opportunity_service import OpportunityService

router = APIRouter(prefix="/api/v1/opportunities", tags=["opportunities"])


def _to_opportunity_response(o: Opportunity) -> OpportunityResponse:
    return OpportunityResponse(
        opportunity_id=o.opportunity_id,
        user_id=o.user_id,
        mission_id=o.mission_id,
        opportunity_type=o.opportunity_type_str,
        title=o.title,
        organization=o.organization,
        source_url=o.source_url,
        source_name=o.source_name,
        location=o.location,
        status=o.status.value if hasattr(o.status, "value") else str(o.status),
        requirements=list(o.requirements),
        metadata=dict(o.metadata),
        fingerprint=o.fingerprint,
        discovered_at=to_iso_utc(o.discovered_at),
        deadline=to_iso_utc(o.deadline) if o.deadline else None,
        version=o.version,
    )


@router.post("", response_model=OpportunityResponse)
async def create_or_deduplicate_opportunity(
    req: CreateOpportunityRequest,
    principal: Annotated[Principal, Depends(get_principal)],
    service: Annotated[OpportunityService, Depends(get_opportunity_service)],
    response: Response,
) -> OpportunityResponse:
    """Discover opportunity with user-scoped deduplication (returns 201 for new, 200 for existing)."""
    opp, created = service.create_opportunity(
        user_id=principal.user_id,
        title=req.title,
        opportunity_type=req.opportunity_type,
        organization=req.organization,
        source_url=req.source_url,
        source_name=req.source_name,
        location=req.location,
        status=req.status,
        requirements=req.requirements,
        metadata=req.metadata,
        deadline=req.deadline,
        mission_id=req.mission_id,
    )
    if created:
        response.status_code = status.HTTP_201_CREATED
    else:
        response.status_code = status.HTTP_200_OK

    set_etag_header(response, "opportunity", opp.opportunity_id, opp.version)
    return _to_opportunity_response(opp)


@router.get("", response_model=PaginatedResponse[OpportunityResponse])
async def list_opportunities(
    principal: Annotated[Principal, Depends(get_principal)],
    service: Annotated[OpportunityService, Depends(get_opportunity_service)],
    pagination: Annotated[PaginationParams, Depends()],
    status_filter: OpportunityStatus | None = None,
) -> PaginatedResponse[OpportunityResponse]:
    """List opportunities belonging to the authenticated user."""
    all_opps = service.list_user_opportunities(principal.user_id, status=status_filter)
    total = len(all_opps)
    page_items = all_opps[pagination.offset : pagination.offset + pagination.limit]
    return PaginatedResponse(
        items=[_to_opportunity_response(o) for o in page_items],
        total=total,
        limit=pagination.limit,
        offset=pagination.offset,
    )


@router.get("/{opportunity_id}", response_model=OpportunityResponse)
async def get_opportunity(
    opportunity_id: str,
    principal: Annotated[Principal, Depends(get_principal)],
    service: Annotated[OpportunityService, Depends(get_opportunity_service)],
    response: Response,
) -> OpportunityResponse:
    """Retrieve an opportunity asserting user ownership (404 on unowned records)."""
    opp = service.get_opportunity_for_user(principal.user_id, opportunity_id)
    if opp is None:
        raise EntityNotFoundError("Opportunity", opportunity_id)
    set_etag_header(response, "opportunity", opp.opportunity_id, opp.version)
    return _to_opportunity_response(opp)


@router.post("/{opportunity_id}/transition", response_model=OpportunityResponse)
async def transition_opportunity(
    opportunity_id: str,
    req: TransitionOpportunityRequest,
    request: Request,
    response: Response,
    principal: Annotated[Principal, Depends(get_principal)],
    service: Annotated[OpportunityService, Depends(get_opportunity_service)],
) -> OpportunityResponse:
    """Transition opportunity status with strict ETag precondition verification."""
    existing = service.get_opportunity_for_user(principal.user_id, opportunity_id)
    if existing is None:
        raise EntityNotFoundError("Opportunity", opportunity_id)

    check_if_match(request, "opportunity", existing.opportunity_id, existing.version)

    updated = service.transition_opportunity_for_user(
        user_id=principal.user_id,
        opportunity_id=opportunity_id,
        new_status=req.new_status,
        reason=req.reason,
    )
    set_etag_header(response, "opportunity", updated.opportunity_id, updated.version)
    return _to_opportunity_response(updated)
