"""Actions API endpoints with server-side risk derivation and approval requests."""

from __future__ import annotations

from typing import Annotated
from fastapi import APIRouter, Depends, Request, Response, status

from caseworker.api.dependencies import (
    get_action_service,
    get_case_service,
    get_governed_execution_service,
    get_principal,
    get_storage,
)
from caseworker.api.etag import check_if_match, set_etag_header
from caseworker.api.identity import Principal
from caseworker.api.schemas.action import ActionResponse, ProposeActionRequest
from caseworker.api.schemas.approval import ApprovalResponse
from caseworker.api.schemas.execution import (
    ExecuteActionRequest,
    ExecuteActionResponse,
    ExecutionAttemptResponse,
    ExecutionReceiptResponse,
)
from caseworker.domain.action import Action
from caseworker.domain.approval import Approval
from caseworker.domain.enums import (
    ExecutionAttemptStatus,
    ExecutionDestinationType,
    ExecutionReceiptStatus,
)
from caseworker.domain.errors import ApprovalValidationError, EntityNotFoundError
from caseworker.domain.execution_attempt import ExecutionAttempt
from caseworker.domain.execution_receipt import ExecutionReceipt
from caseworker.domain.provider_adapter import SimulatedExecutionProvider, SimulationMode
from caseworker.domain.types import to_iso_utc
from caseworker.persistence.sqlite import SQLiteCaseworkerStorage
from caseworker.services.action_service import ActionService
from caseworker.services.case_service import CaseService
from caseworker.services.governed_execution_service import GovernedExecutionService

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


def _to_attempt_response(att: ExecutionAttempt) -> ExecutionAttemptResponse:
    return ExecutionAttemptResponse(
        attempt_id=att.attempt_id,
        action_id=att.action_id,
        approval_id=att.approval_id,
        plan_id=att.plan_id,
        case_id=att.case_id,
        user_id=att.user_id,
        lease_token=att.lease_token,
        fencing_token=att.fencing_token,
        status=att.status,
        provider_kind=att.provider_kind,
        started_at=to_iso_utc(att.started_at),
        completed_at=to_iso_utc(att.completed_at) if att.completed_at else None,
        last_reversible_point_reached_at=to_iso_utc(att.last_reversible_point_reached_at) if att.last_reversible_point_reached_at else None,
        irreversible_action_taken=att.irreversible_action_taken,
        staged_field_count=att.staged_field_count,
        staged_upload_count=att.staged_upload_count,
        error_category=att.error_category,
        error_message=att.error_message,
        raw_response_snippet=att.raw_response_snippet,
        attempt_number=att.attempt_number,
    )


def _to_receipt_response(r: ExecutionReceipt) -> ExecutionReceiptResponse:
    return ExecutionReceiptResponse(
        receipt_id=r.receipt_id,
        attempt_id=r.attempt_id,
        action_id=r.action_id,
        approval_id=r.approval_id,
        plan_id=r.plan_id,
        case_id=r.case_id,
        user_id=r.user_id,
        status=r.status,
        destination_hostname=r.destination_hostname,
        destination_url=r.destination_url,
        destination_type=r.destination_type,
        execution_plan_fingerprint=r.execution_plan_fingerprint,
        authorized_action_fingerprint=r.authorized_action_fingerprint,
        package_fingerprint=r.package_fingerprint,
        executed_at=to_iso_utc(r.executed_at),
        external_reference_id=r.external_reference_id,
        field_count=r.field_count,
        upload_asset_ids=list(r.upload_asset_ids),
        upload_asset_shas=list(r.upload_asset_shas),
        screening_response_count=r.screening_response_count,
        safe_provider_metadata=dict(r.safe_provider_metadata),
        receipt_hash=r.receipt_hash,
        version=r.version,
    )


@router.post("/api/v1/actions/{action_id}/execute", response_model=ExecuteActionResponse)
async def execute_action(
    action_id: str,
    request: Request,
    response: Response,
    principal: Annotated[Principal, Depends(get_principal)],
    action_service: Annotated[ActionService, Depends(get_action_service)],
    exec_service: Annotated[GovernedExecutionService, Depends(get_governed_execution_service)],
    storage: Annotated[SQLiteCaseworkerStorage, Depends(get_storage)],
    req: ExecuteActionRequest | None = None,
) -> ExecuteActionResponse:
    """Execute an approved transmission action under two-barrier live integrity and lease fencing."""
    # 1. Assert action exists and user owns the parent case
    action = action_service.get_action_for_user(principal.user_id, action_id)
    if action is None:
        raise EntityNotFoundError("Action", action_id)

    # 2. Idempotency, Active Lease & Crash Recovery Resolution BEFORE If-Match check (Refinements 1, 2, 3)
    is_recovery_request = False
    with storage.unit_of_work() as uow:
        existing_receipt = uow.execution_receipts.get_by_action_id(action_id)
        if existing_receipt is not None:
            if existing_receipt.status == ExecutionReceiptStatus.VERIFIED_SUCCESS:
                attempt = uow.execution_attempts.get_by_id(existing_receipt.attempt_id)
                case = uow.cases.get_by_id(action.case_id)
                set_etag_header(response, "action", action.action_id, action.version)
                return ExecuteActionResponse(
                    action=_to_action_response(action),
                    attempt=_to_attempt_response(attempt) if attempt else None,  # type: ignore[arg-type]
                    receipt=_to_receipt_response(existing_receipt),
                    case_status=case.status.value if case else "waiting_external",
                )
            elif existing_receipt.status == ExecutionReceiptStatus.AMBIGUOUS_SUBMISSION:
                raise ApprovalValidationError(
                    "Previous transmission attempt resulted in an AMBIGUOUS submission. Automatic retry is strictly forbidden; manual operator resolution is required.",
                    details={"failure_kind": "manual_resolution_required", "receipt_id": existing_receipt.receipt_id},
                )

        # Check active lease FIRST: protect actively executing worker
        plan_id = action.parameters.get("plan_id", "")
        active_lease = uow.execution_leases.get_active_lease(action.case_id, plan_id, action_id) if plan_id else None
        if active_lease is not None and not active_lease.is_expired():
            raise ApprovalValidationError(
                f"An execution attempt is currently active under lease token '{active_lease.lease_token}'.",
                details={"failure_kind": "execution_in_progress", "lease_token": active_lease.lease_token},
            )

        # Check if an unresolved attempt crossed irreversible boundary without a receipt
        latest_attempt = uow.execution_attempts.get_latest_for_action(action_id)
        if latest_attempt is not None and (
            latest_attempt.status == ExecutionAttemptStatus.SUBMITTING
            or (latest_attempt.irreversible_action_taken and latest_attempt.status != ExecutionAttemptStatus.SUCCEEDED)
        ):
            # Authorized recovery of an interrupted attempt whose lease is expired or absent.
            # Recovery must NOT be blocked by a stale Action ETag for new execution requests.
            is_recovery_request = True

    # 3. Genuinely new execution request: strictly enforce Action If-Match
    if not is_recovery_request:
        check_if_match(request, "action", action.action_id, action.version)

    # 4. Resolve provider adapter (support test simulation mode if requested)
    provider = None
    if req and req.simulation_mode:
        if not exec_service.allow_simulation:
            raise ApprovalValidationError(
                "Simulated execution provider is restricted to trusted, isolated demonstration or test environments. "
                "In live production environments, real-world execution requires an authorized genuine provider adapter.",
                details={"failure_kind": "simulation_not_permitted"},
            )
        # Email transmissions must use genuine governed provider; simulation mode rejected
        with storage.unit_of_work() as uow:
            plan_id = action.parameters.get("plan_id", "")
            plan = uow.execution_plans.get_by_id(plan_id) if plan_id else None
            if plan and (
                (hasattr(plan.destination.destination_type, "value") and plan.destination.destination_type.value == ExecutionDestinationType.EMAIL.value)
                or str(plan.destination.destination_type) == ExecutionDestinationType.EMAIL.value
            ):
                raise ApprovalValidationError(
                    "Simulation request rejected for live real-transmission approval. Email transmissions must use the genuine governed provider.",
                    details={"failure_kind": "simulation_not_permitted"},
                )
        provider = SimulatedExecutionProvider(req.simulation_mode)

    # 5. Execute through governed pipeline
    attempt, receipt = exec_service.execute_action(
        user_id=principal.user_id,
        action_id=action_id,
        provider=provider,
        principal_auth_method=principal.auth_method,
    )

    fresh_action = action_service.get_action_for_user(principal.user_id, action_id) or action
    with storage.unit_of_work() as uow:
        fresh_case = uow.cases.get_by_id(fresh_action.case_id)
        case_status = fresh_case.status.value if fresh_case else "unknown"

    set_etag_header(response, "action", fresh_action.action_id, fresh_action.version)
    return ExecuteActionResponse(
        action=_to_action_response(fresh_action),
        attempt=_to_attempt_response(attempt),
        receipt=_to_receipt_response(receipt) if receipt else None,
        case_status=case_status,
    )

