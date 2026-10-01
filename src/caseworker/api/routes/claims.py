"""Claims API endpoints for verified propositions and Claim Ledger auditing."""

from __future__ import annotations

from typing import Annotated
from fastapi import APIRouter, Depends, Request, Response, status

from caseworker.api.dependencies import (
    PaginationParams,
    get_claim_service,
    get_principal,
)
from caseworker.api.etag import check_if_match, set_etag_header
from caseworker.api.identity import Principal
from caseworker.api.schemas.claim import (
    ClaimResponse,
    EvaluateClaimRequest,
    ProposeClaimRequest,
    RejectClaimRequest,
)
from caseworker.api.schemas.common import PaginatedResponse
from caseworker.domain.claim import Claim
from caseworker.domain.enums import ClaimStatus
from caseworker.domain.errors import EntityNotFoundError
from caseworker.domain.types import to_iso_utc
from caseworker.services.claim_service import ClaimLedgerService

router = APIRouter(prefix="/api/v1/claims", tags=["claims"])


def _to_claim_response(c: Claim) -> ClaimResponse:
    return ClaimResponse(
        claim_id=c.claim_id,
        user_id=c.user_id,
        purpose=c.purpose,
        text=c.text,
        case_id=c.case_id,
        mission_id=c.mission_id,
        status=c.status.value if hasattr(c.status, "value") else str(c.status),
        supporting_fact_ids=list(c.supporting_fact_ids),
        created_at=to_iso_utc(c.created_at),
        updated_at=to_iso_utc(c.updated_at),
        verified_at=to_iso_utc(c.verified_at) if c.verified_at else None,
        rejection_reason=c.rejection_reason,
        version=c.version,
    )


@router.post("", response_model=ClaimResponse, status_code=status.HTTP_201_CREATED)
async def propose_claim(
    req: ProposeClaimRequest,
    principal: Annotated[Principal, Depends(get_principal)],
    service: Annotated[ClaimLedgerService, Depends(get_claim_service)],
    response: Response,
) -> ClaimResponse:
    """Propose a verifiable assertion bound strictly to the caller's context vault facts."""
    claim = service.propose_claim(
        user_id=principal.user_id,
        purpose=req.purpose,
        text=req.text,
        supporting_fact_ids=req.supporting_fact_ids,
        case_id=req.case_id,
        mission_id=req.mission_id,
        auto_evaluate=req.auto_evaluate,
    )
    set_etag_header(response, "claim", claim.claim_id, claim.version)
    return _to_claim_response(claim)


@router.get("", response_model=PaginatedResponse[ClaimResponse])
async def list_claims(
    principal: Annotated[Principal, Depends(get_principal)],
    service: Annotated[ClaimLedgerService, Depends(get_claim_service)],
    pagination: Annotated[PaginationParams, Depends()],
    status_filter: ClaimStatus | None = None,
    case_id: str | None = None,
    mission_id: str | None = None,
) -> PaginatedResponse[ClaimResponse]:
    """List claims belonging to the authenticated user, optionally filtered by status, case_id, or mission_id."""
    all_claims = service.list_user_claims(
        principal.user_id,
        status=status_filter,
        case_id=case_id,
        mission_id=mission_id,
    )
    total = len(all_claims)
    page_items = all_claims[pagination.offset : pagination.offset + pagination.limit]
    return PaginatedResponse(
        items=[_to_claim_response(c) for c in page_items],
        total=total,
        limit=pagination.limit,
        offset=pagination.offset,
    )


@router.get("/{claim_id}", response_model=ClaimResponse)
async def get_claim(
    claim_id: str,
    principal: Annotated[Principal, Depends(get_principal)],
    service: Annotated[ClaimLedgerService, Depends(get_claim_service)],
    response: Response,
) -> ClaimResponse:
    """Retrieve a claim asserting user ownership (404 on unowned claims)."""
    claim = service.get_claim_for_user(principal.user_id, claim_id)
    if claim is None:
        raise EntityNotFoundError("Claim", claim_id)
    set_etag_header(response, "claim", claim.claim_id, claim.version)
    return _to_claim_response(claim)


@router.post("/{claim_id}/evaluate", response_model=ClaimResponse)
async def evaluate_claim(
    claim_id: str,
    req: EvaluateClaimRequest,
    request: Request,
    response: Response,
    principal: Annotated[Principal, Depends(get_principal)],
    service: Annotated[ClaimLedgerService, Depends(get_claim_service)],
) -> ClaimResponse:
    """Re-evaluate claim validity under ETag precondition enforcement."""
    existing = service.get_claim_for_user(principal.user_id, claim_id)
    if existing is None:
        raise EntityNotFoundError("Claim", claim_id)

    check_if_match(request, "claim", existing.claim_id, existing.version)

    updated = service.evaluate_claim_for_user(principal.user_id, claim_id)
    set_etag_header(response, "claim", updated.claim_id, updated.version)
    return _to_claim_response(updated)


@router.post("/{claim_id}/reject", response_model=ClaimResponse)
async def reject_claim(
    claim_id: str,
    req: RejectClaimRequest,
    request: Request,
    response: Response,
    principal: Annotated[Principal, Depends(get_principal)],
    service: Annotated[ClaimLedgerService, Depends(get_claim_service)],
) -> ClaimResponse:
    """Reject a claim under ETag precondition enforcement."""
    existing = service.get_claim_for_user(principal.user_id, claim_id)
    if existing is None:
        raise EntityNotFoundError("Claim", claim_id)

    check_if_match(request, "claim", existing.claim_id, existing.version)

    updated = service.reject_claim_for_user(principal.user_id, claim_id, reason=req.reason)
    set_etag_header(response, "claim", updated.claim_id, updated.version)
    return _to_claim_response(updated)
