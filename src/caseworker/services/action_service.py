"""Application service managing Action proposals, server-side risk evaluation, and approval requests."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any

from caseworker.domain.action import Action
from caseworker.domain.action_policy import ActionPolicy
from caseworker.domain.approval import Approval
from caseworker.domain.enums import ActionStatus, ActionType, ApprovalStatus
from caseworker.domain.errors import EntityNotFoundError, InvalidStateTransitionError
from caseworker.domain.events import (
    make_action_proposed_event,
    make_action_status_changed_event,
    make_approval_requested_event,
)

if TYPE_CHECKING:
    from caseworker.persistence.sqlite import SQLiteCaseworkerStorage


class ActionService:
    """Application service managing case actions with server-side safety policy enforcement."""

    def __init__(self, storage: SQLiteCaseworkerStorage) -> None:
        self.storage = storage

    def propose_action(
        self,
        user_id: str,
        case_id: str,
        action_type: ActionType | str,
        description: str,
        parameters: dict[str, Any] | None = None,
        idempotency_key: str | None = None,
    ) -> Action:
        """Propose an action for a case, strictly deriving risk level and approval requirement server-side."""
        with self.storage.unit_of_work() as uow:
            case = uow.cases.get_by_id(case_id)
            if case is None or case.user_id != user_id:
                raise EntityNotFoundError("Case", case_id)

            if idempotency_key:
                existing = uow.actions.get_by_idempotency_key(case_id, idempotency_key)
                if existing is not None:
                    return existing

            # Server-side safety evaluation: clients cannot downgrade risk or bypass approvals
            decision = ActionPolicy.evaluate(action_type)

            action = Action(
                case_id=case_id,
                action_type=action_type,
                description=description,
                parameters=dict(parameters or {}),
                risk_level=decision.risk_level,
                requires_approval=decision.requires_approval,
                idempotency_key=idempotency_key,
                status=ActionStatus.PROPOSED,
            )
            uow.actions.save(action)

            event = make_action_proposed_event(
                action_id=action.action_id,
                case_id=action.case_id,
                user_id=user_id,
                aggregate_version=action.version,
                payload=action.to_dict(),
            )
            uow.events.append(event)
            return action

    def get_action_for_user(self, user_id: str, action_id: str) -> Action | None:
        """Retrieve an action asserting parent case ownership."""
        with self.storage.unit_of_work() as uow:
            action = uow.actions.get_by_id(action_id)
            if action is None:
                return None
            case = uow.cases.get_by_id(action.case_id)
            if case is None or case.user_id != user_id:
                return None
            return action

    def list_actions_for_case_for_user(self, user_id: str, case_id: str) -> list[Action]:
        """List all actions for a case, asserting case ownership."""
        with self.storage.unit_of_work() as uow:
            case = uow.cases.get_by_id(case_id)
            if case is None or case.user_id != user_id:
                return []
            return uow.actions.list_by_case(case_id)

    def request_approval(self, user_id: str, action_id: str) -> tuple[Action, Approval]:
        """Request human authorization for an action, binding an immutable approval to the action's fingerprint."""
        with self.storage.unit_of_work() as uow:
            action = uow.actions.get_by_id(action_id)
            if action is None:
                raise EntityNotFoundError("Action", action_id)

            case = uow.cases.get_by_id(action.case_id)
            if case is None or case.user_id != user_id:
                raise EntityNotFoundError("Action", action_id)

            old_status = action.status.value
            action.transition_to(ActionStatus.AWAITING_APPROVAL, reason="Approval requested")
            uow.actions.save(action)

            action_event = make_action_status_changed_event(
                action_id=action.action_id,
                user_id=user_id,
                aggregate_version=action.version,
                old_status=old_status,
                new_status=action.status.value,
                reason="Approval requested",
            )
            uow.events.append(action_event)

            # Check if an existing pending approval already exists
            existing_approval = uow.approvals.get_by_action_id(action.action_id)
            if existing_approval is not None and existing_approval.status == ApprovalStatus.PENDING:
                return action, existing_approval

            # Cryptographically bind approval to the current action fingerprint
            fingerprint = action.compute_fingerprint()
            approval = Approval(
                action_id=action.action_id,
                case_id=action.case_id,
                user_id=user_id,
                action_fingerprint=fingerprint,
                status=ApprovalStatus.PENDING,
            )
            uow.approvals.save(approval)

            approval_event = make_approval_requested_event(
                approval_id=approval.approval_id,
                action_id=approval.action_id,
                case_id=approval.case_id,
                user_id=user_id,
                aggregate_version=approval.version,
                action_fingerprint=approval.action_fingerprint,
            )
            uow.events.append(approval_event)

            return action, approval
