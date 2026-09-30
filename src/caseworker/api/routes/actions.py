"""Actions API endpoints with server-side risk derivation and approval requests."""

from __future__ import annotations

from typing import Annotated
from fastapi import APIRouter, Depends, Request, Response, status

from caseworker.api.dependencies import (
    get_action_service,
    get_case_service,
    get_principal,
)
from caseworker.api.etag import check_if_match, set_etag_header
from caseworker.api.identity import Principal
from caseworker.api.schemas.action import ActionResponse, ProposeActionRequest
from caseworker.api.schemas.approval import ApprovalResponse
from caseworker.domain.action import Action
from caseworker.domain.approval import Approval
from caseworker.domain.errors import EntityNotFoundError
from caseworker.domain.types import to_iso_utc
from caseworker.services.action_service import ActionService
from caseworker.services.case_service import CaseService

router = APIRouter(tags=["actions"])


def _to_action_response(a: Action) -> ActionResponse:
    return ActionResponse(
        action_id=a.action_id,
        case_id=a.case_id,
        action_type=a.action_type_str,
        description=a.description,
        parameters=dict(a.parameters),
        status=a.status.value if hasattr(a.status, "value") else str(a.status),
        risk_level=a.risk_level.value if hasattr(a.risk_level, "value") else str(a.risk_level),
        requires_approval=a.requires_approval,
        fingerprint=a.compute_fingerprint(),
        created_at=to_iso_utc(a.created_at),
        executed_at=to_iso_utc(a.executed_at) if a.executed_at else None,
        result=dict(a.result),
        idempotency_key=a.idempotency_key,
        version=a.version,
    )


def _to_approval_response(app: Approval) -> ApprovalResponse:
    return ApprovalResponse(
        approval_id=app.approval_id,
        action_id=app.action_id,
        case_id=app.case_id,
        user_id=app.user_id,
        action_fingerprint=app.action_fingerprint,
        status=app.status.value if hasattr(app.status, "value") else str(app.status),
        requested_at=to_iso_utc(app.requested_at),
        decided_at=to_iso_utc(app.decided_at) if app.decided_at else None,
        expires_at=to_iso_utc(app.expires_at) if app.expires_at else None,
        reason=app.reason,
        version=app.version,
    )


@router.post("/api/v1/cases/{case_id}/actions", response_model=ActionResponse, status_code=status.HTTP_201_CREATED)
async def propose_action(
    case_id: str,
    req: ProposeActionRequest,
    principal: Annotated[Principal, Depends(get_principal)],
    service: Annotated[ActionService, Depends(get_action_service)],
    response: Response,
) -> ActionResponse:
    """Propose an action with strict server-side risk level and approval requirement derivation."""
    action = service.propose_action(
        user_id=principal.user_id,
        case_id=case_id,
        action_type=req.action_type,
        description=req.description,
        parameters=req.parameters,
        idempotency_key=req.idempotency_key,
    )
    set_etag_header(response, "action", action.action_id, action.version)
    return _to_action_response(action)


@router.get("/api/v1/cases/{case_id}/actions", response_model=list[ActionResponse])
async def list_case_actions(
    case_id: str,
    principal: Annotated[Principal, Depends(get_principal)],
    case_service: Annotated[CaseService, Depends(get_case_service)],
    action_service: Annotated[ActionService, Depends(get_action_service)],
) -> list[ActionResponse]:
    """List actions belonging to a case, verifying case ownership (404 on unowned cases)."""
    case = case_service.get_case_for_user(principal.user_id, case_id)
    if case is None:
        raise EntityNotFoundError("Case", case_id)

    actions = action_service.list_actions_for_case_for_user(principal.user_id, case_id)
    return [_to_action_response(a) for a in actions]


@router.get("/api/v1/actions/{action_id}", response_model=ActionResponse)
async def get_action(
    action_id: str,
    principal: Annotated[Principal, Depends(get_principal)],
    service: Annotated[ActionService, Depends(get_action_service)],
    response: Response,
) -> ActionResponse:
    """Retrieve an action asserting case user ownership (404 on unowned actions)."""
    action = service.get_action_for_user(principal.user_id, action_id)
    if action is None:
        raise EntityNotFoundError("Action", action_id)
    set_etag_header(response, "action", action.action_id, action.version)
    return _to_action_response(action)


@router.post("/api/v1/actions/{action_id}/request-approval", response_model=ActionResponse)
async def request_approval(
    action_id: str,
    request: Request,
    response: Response,
    principal: Annotated[Principal, Depends(get_principal)],
    service: Annotated[ActionService, Depends(get_action_service)],
) -> ActionResponse:
    """Request human approval for an action with ETag precondition enforcement."""
    existing = service.get_action_for_user(principal.user_id, action_id)
    if existing is None:
        raise EntityNotFoundError("Action", action_id)

    check_if_match(request, "action", existing.action_id, existing.version)

    action, _approval = service.request_approval(principal.user_id, action_id)
    set_etag_header(response, "action", action.action_id, action.version)
    return _to_action_response(action)
