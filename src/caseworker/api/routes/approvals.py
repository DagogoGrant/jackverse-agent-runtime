"""Approvals API endpoints for human authorizations with cryptographic action binding."""

from __future__ import annotations

from typing import Annotated
from fastapi import APIRouter, Depends, Request, Response, status

from caseworker.api.dependencies import (
    get_approval_service,
    get_principal,
)
from caseworker.api.etag import check_if_match, set_etag_header
from caseworker.api.identity import Principal
from caseworker.api.routes.actions import _to_action_response
from caseworker.api.schemas.approval import (
    ApprovalDecisionResponse,
    ApprovalResponse,
    ApproveActionRequest,
    RejectActionRequest,
)
from caseworker.domain.approval import Approval
from caseworker.domain.enums import ApprovalStatus
from caseworker.domain.errors import EntityNotFoundError
from caseworker.domain.types import to_iso_utc
from caseworker.services.approval_service import ApprovalService

router = APIRouter(prefix="/api/v1/approvals", tags=["approvals"])


def _to_approval_response(a: Approval) -> ApprovalResponse:
    return ApprovalResponse(
        approval_id=a.approval_id,
        action_id=a.action_id,
        case_id=a.case_id,
        user_id=a.user_id,
        action_fingerprint=a.action_fingerprint,
        status=a.status.value if hasattr(a.status, "value") else str(a.status),
        requested_at=to_iso_utc(a.requested_at),
        decided_at=to_iso_utc(a.decided_at) if a.decided_at else None,
        expires_at=to_iso_utc(a.expires_at) if a.expires_at else None,
        reason=a.reason,
        version=a.version,
    )


@router.get("", response_model=list[ApprovalResponse])
async def list_approvals(
    principal: Annotated[Principal, Depends(get_principal)],
    service: Annotated[ApprovalService, Depends(get_approval_service)],
    status_filter: ApprovalStatus | None = None,
) -> list[ApprovalResponse]:
    """List approvals belonging to the authenticated user."""
    approvals = service.list_user_approvals(principal.user_id, status=status_filter)
    return [_to_approval_response(a) for a in approvals]


@router.get("/{approval_id}", response_model=ApprovalResponse)
async def get_approval(
    approval_id: str,
    principal: Annotated[Principal, Depends(get_principal)],
    service: Annotated[ApprovalService, Depends(get_approval_service)],
    response: Response,
) -> ApprovalResponse:
    """Retrieve an approval asserting user ownership (404 on unowned records)."""
    approval = service.get_approval_for_user(principal.user_id, approval_id)
    if approval is None:
        raise EntityNotFoundError("Approval", approval_id)
    set_etag_header(response, "approval", approval.approval_id, approval.version)
    return _to_approval_response(approval)


@router.post("/{approval_id}/approve", response_model=ApprovalDecisionResponse)
async def approve_action(
    approval_id: str,
    req: ApproveActionRequest,
    request: Request,
    response: Response,
    principal: Annotated[Principal, Depends(get_principal)],
    service: Annotated[ApprovalService, Depends(get_approval_service)],
) -> ApprovalDecisionResponse:
    """Approve an action with fingerprint verification and idempotent handling under ETag control."""
    existing = service.get_approval_for_user(principal.user_id, approval_id)
    if existing is None:
        raise EntityNotFoundError("Approval", approval_id)

    check_if_match(request, "approval", existing.approval_id, existing.version)

    approval, action = service.approve_action(
        user_id=principal.user_id,
        approval_id=approval_id,
        reason=req.reason,
    )
    set_etag_header(response, "approval", approval.approval_id, approval.version)
    return ApprovalDecisionResponse(
        approval=_to_approval_response(approval),
        action=_to_action_response(action),
    )


@router.post("/{approval_id}/reject", response_model=ApprovalDecisionResponse)
async def reject_action(
    approval_id: str,
    req: RejectActionRequest,
    request: Request,
    response: Response,
    principal: Annotated[Principal, Depends(get_principal)],
    service: Annotated[ApprovalService, Depends(get_approval_service)],
) -> ApprovalDecisionResponse:
    """Reject an action with idempotent handling under ETag control."""
    existing = service.get_approval_for_user(principal.user_id, approval_id)
    if existing is None:
        raise EntityNotFoundError("Approval", approval_id)

    check_if_match(request, "approval", existing.approval_id, existing.version)

    approval, action = service.reject_action(
        user_id=principal.user_id,
        approval_id=approval_id,
        reason=req.reason,
    )
    set_etag_header(response, "approval", approval.approval_id, approval.version)
    return ApprovalDecisionResponse(
        approval=_to_approval_response(approval),
        action=_to_action_response(action),
    )
