"""Application service managing human approvals with cryptographic action fingerprint binding."""

from __future__ import annotations

from typing import TYPE_CHECKING

from caseworker.domain.action import Action
from caseworker.domain.approval import Approval
from caseworker.domain.enums import ActionStatus, ApprovalStatus
from caseworker.domain.errors import (
    ApprovalValidationError,
    EntityNotFoundError,
    InvalidStateTransitionError,
)
from caseworker.domain.events import (
    make_action_status_changed_event,
    make_approval_decided_event,
)

if TYPE_CHECKING:
    from caseworker.persistence.sqlite import SQLiteCaseworkerStorage


class ApprovalService:
    """Application service managing human approval workflows with idempotent state transitions."""

    def __init__(self, storage: SQLiteCaseworkerStorage) -> None:
        self.storage = storage

    def get_approval_for_user(self, user_id: str, approval_id: str) -> Approval | None:
        """Fetch approval ensuring strict user isolation."""
        with self.storage.unit_of_work() as uow:
            approval = uow.approvals.get_by_id(approval_id)
            if approval is None or approval.user_id != user_id:
                return None
            return approval

    def list_user_approvals(
        self,
        user_id: str,
        status: ApprovalStatus | None = None,
    ) -> list[Approval]:
        """List approvals belonging to a user, optionally filtered by status."""
        with self.storage.unit_of_work() as uow:
            return uow.approvals.list_by_user(user_id=user_id, status=status)

    def approve_action(
        self,
        user_id: str,
        approval_id: str,
        reason: str | None = None,
    ) -> tuple[Approval, Action]:
        """Approve an action request with fingerprint validation and idempotent handling."""
        with self.storage.unit_of_work() as uow:
            approval = uow.approvals.get_by_id(approval_id)
            if approval is None or approval.user_id != user_id:
                raise EntityNotFoundError("Approval", approval_id)

            action = uow.actions.get_by_id(approval.action_id)
            if action is None:
                raise EntityNotFoundError("Action", approval.action_id)

            case = uow.cases.get_by_id(action.case_id)
            if case is None or case.user_id != user_id:
                raise EntityNotFoundError("Approval", approval_id)

            # Idempotency check:
            if approval.status == ApprovalStatus.APPROVED:
                # Already approved - return existing state idempotently
                return approval, action

            if approval.status == ApprovalStatus.REJECTED:
                raise InvalidStateTransitionError(
                    "Approval",
                    approval.status.value,
                    ApprovalStatus.APPROVED.value,
                    "Cannot approve an already rejected approval request.",
                )

            if approval.status in (ApprovalStatus.CANCELLED, ApprovalStatus.EXPIRED):
                raise InvalidStateTransitionError(
                    "Approval",
                    approval.status.value,
                    ApprovalStatus.APPROVED.value,
                    f"Cannot approve an approval in status '{approval.status.value}'.",
                )

            # Approve domain model (validates action fingerprint and updates status/version)
            approval.approve(action=action, reason=reason)
            old_action_status = action.status.value
            action.transition_to(ActionStatus.APPROVED, reason=reason)

            uow.approvals.save(approval)
            uow.actions.save(action)

            approval_event = make_approval_decided_event(
                approval_id=approval.approval_id,
                user_id=user_id,
                aggregate_version=approval.version,
                decision="approved",
                reason=reason,
            )
            uow.events.append(approval_event)

            action_event = make_action_status_changed_event(
                action_id=action.action_id,
                user_id=user_id,
                aggregate_version=action.version,
                old_status=old_action_status,
                new_status=action.status.value,
                reason=reason,
            )
            uow.events.append(action_event)

            return approval, action

    def reject_action(
        self,
        user_id: str,
        approval_id: str,
        reason: str | None = None,
    ) -> tuple[Approval, Action]:
        """Reject an action request with idempotent handling."""
        with self.storage.unit_of_work() as uow:
            approval = uow.approvals.get_by_id(approval_id)
            if approval is None or approval.user_id != user_id:
                raise EntityNotFoundError("Approval", approval_id)

            action = uow.actions.get_by_id(approval.action_id)
            if action is None:
                raise EntityNotFoundError("Action", approval.action_id)

            case = uow.cases.get_by_id(action.case_id)
            if case is None or case.user_id != user_id:
                raise EntityNotFoundError("Approval", approval_id)

            # Idempotency check:
            if approval.status == ApprovalStatus.REJECTED:
                # Already rejected - return existing state idempotently
                return approval, action

            if approval.status == ApprovalStatus.APPROVED:
                raise InvalidStateTransitionError(
                    "Approval",
                    approval.status.value,
                    ApprovalStatus.REJECTED.value,
                    "Cannot reject an already approved approval request.",
                )

            if approval.status in (ApprovalStatus.CANCELLED, ApprovalStatus.EXPIRED):
                raise InvalidStateTransitionError(
                    "Approval",
                    approval.status.value,
                    ApprovalStatus.REJECTED.value,
                    f"Cannot reject an approval in status '{approval.status.value}'.",
                )

            approval.reject(reason=reason)
            old_action_status = action.status.value
            action.transition_to(ActionStatus.REJECTED, reason=reason)

            uow.approvals.save(approval)
            uow.actions.save(action)

            approval_event = make_approval_decided_event(
                approval_id=approval.approval_id,
                user_id=user_id,
                aggregate_version=approval.version,
                decision="rejected",
                reason=reason,
            )
            uow.events.append(approval_event)

            action_event = make_action_status_changed_event(
                action_id=action.action_id,
                user_id=user_id,
                aggregate_version=action.version,
                old_status=old_action_status,
                new_status=action.status.value,
                reason=reason,
            )
            uow.events.append(action_event)

            return approval, action
