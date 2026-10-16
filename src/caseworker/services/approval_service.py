"""Application service managing human approvals with cryptographic action fingerprint binding."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from caseworker.domain.action import Action
from caseworker.domain.approval import Approval
from caseworker.domain.enums import (
    ActionStatus,
    ActionType,
    ApprovalStatus,
    ArtifactStatus,
    CaseStatus,
    VerificationStatus,
)
from caseworker.domain.errors import (
    ApprovalValidationError,
    EntityNotFoundError,
    InvalidStateTransitionError,
)
from caseworker.domain.events import (
    make_action_status_changed_event,
    make_approval_decided_event,
    make_case_status_changed_event,
    make_preparation_package_approved_event,
    make_preparation_package_rejected_event,
    make_preparation_package_superseded_event,
)
from caseworker.domain.package import compute_package_fingerprint
from caseworker.domain.package_integrity import PreparationPackageIntegrityValidator
from caseworker.domain.package_verifier import PackageVerificationEngine
from caseworker.services.package_service import PackageService

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

            is_prep_pkg = action.action_type == ActionType.APPROVE_PREPARATION_PACKAGE or (
                hasattr(action.action_type, "value") and action.action_type.value == ActionType.APPROVE_PREPARATION_PACKAGE.value
            )

            if is_prep_pkg:
                package_id = action.parameters.get("package_id")
                package = uow.preparation_packages.get_by_id(package_id) if package_id else None
                if package is None:
                    raise EntityNotFoundError("PreparationPackage", package_id or "unknown")

                # Phase 5.2.3.1 / Phase 5.3.1: Approval-time revalidation & exact payload verification
                integrity_result = PreparationPackageIntegrityValidator.validate(
                    uow=uow,
                    case=case,
                    package=package,
                    user_id=user_id,
                    expected_package_status=None,
                    action=action,
                )
                reval_ok = integrity_result.is_valid
                reval_reason = integrity_result.reason
                findings = integrity_result.findings
                artifacts = integrity_result.artifacts

                # Amendment 7: Stale package unwind on revalidation failure
                if not reval_ok:
                    package.mark_superseded(reason=f"Approval revalidation failed: {reval_reason}")
                    uow.preparation_packages.save(package)
                    uow.events.append(
                        make_preparation_package_superseded_event(
                            package_id=package.package_id,
                            case_id=case.case_id,
                            package_fingerprint=package.package_fingerprint,
                            user_id=user_id,
                            aggregate_version=package.version,
                            reason=reval_reason,
                        )
                    )

                    for aid in package.artifact_ids:
                        art = uow.preparation_artifacts.get_by_id(aid)
                        if art and art.status == ArtifactStatus.FROZEN:
                            art.transition_to(ArtifactStatus.SUPERSEDED, reason="Package superseded during approval revalidation")
                            uow.preparation_artifacts.save(art)

                    approval.cancel(reason=f"Approval revalidation failed: {reval_reason}")
                    uow.approvals.save(approval)
                    uow.events.append(
                        make_approval_decided_event(
                            approval_id=approval.approval_id,
                            user_id=user_id,
                            aggregate_version=approval.version,
                            decision="cancelled",
                            reason=f"Approval revalidation failed: {reval_reason}",
                        )
                    )

                    old_action_status = action.status.value
                    action.transition_to(ActionStatus.CANCELLED, reason=f"Approval revalidation failed: {reval_reason}")
                    uow.actions.save(action)
                    uow.events.append(
                        make_action_status_changed_event(
                            action_id=action.action_id,
                            user_id=user_id,
                            aggregate_version=action.version,
                            old_status=old_action_status,
                            new_status=action.status.value,
                            reason=f"Approval revalidation failed: {reval_reason}",
                        )
                    )

                    if case.status == CaseStatus.AWAITING_APPROVAL:
                        old_case_status = case.status.value
                        case.transition_to(CaseStatus.PLANNING, reason=f"Preparation package invalidated: {reval_reason}")
                        uow.cases.save(case)
                        uow.events.append(
                            make_case_status_changed_event(
                                case_id=case.case_id,
                                user_id=user_id,
                                aggregate_version=case.version,
                                old_status=old_case_status,
                                new_status=case.status.value,
                                reason=f"Preparation package invalidated: {reval_reason}",
                            )
                        )

                    # Commit the unwind state so changes are persisted despite raising the error
                    uow.commit()
                    raise ApprovalValidationError(
                        f"Preparation package approval failed revalidation: {reval_reason}",
                        details={"findings": findings, "reval_reason": reval_reason},
                    )

            # Approve domain model (validates action fingerprint and updates status/version)
            approval.approve(action=action, reason=reason)
            uow.approvals.save(approval)

            approval_event = make_approval_decided_event(
                approval_id=approval.approval_id,
                user_id=user_id,
                aggregate_version=approval.version,
                decision="approved",
                reason=reason,
            )
            uow.events.append(approval_event)

            if is_prep_pkg:
                # Amendment 8: Local approval action completion
                # AWAITING_APPROVAL -> APPROVED -> EXECUTING -> SUCCEEDED
                old_act_status = action.status.value
                action.transition_to(ActionStatus.APPROVED, reason=reason)
                uow.actions.save(action)
                uow.events.append(
                    make_action_status_changed_event(
                        action_id=action.action_id,
                        user_id=user_id,
                        aggregate_version=action.version,
                        old_status=old_act_status,
                        new_status=action.status.value,
                        reason=reason,
                    )
                )

                old_act_status = action.status.value
                action.transition_to(ActionStatus.EXECUTING, reason="Executing local package approval")
                uow.actions.save(action)
                uow.events.append(
                    make_action_status_changed_event(
                        action_id=action.action_id,
                        user_id=user_id,
                        aggregate_version=action.version,
                        old_status=old_act_status,
                        new_status=action.status.value,
                        reason="Executing local package approval",
                    )
                )

                old_act_status = action.status.value
                action.transition_to(ActionStatus.SUCCEEDED, reason="Package approved locally. Ready for Phase 5.3.")
                uow.actions.save(action)
                uow.events.append(
                    make_action_status_changed_event(
                        action_id=action.action_id,
                        user_id=user_id,
                        aggregate_version=action.version,
                        old_status=old_act_status,
                        new_status=action.status.value,
                        reason="Package approved locally. Ready for Phase 5.3.",
                    )
                )

                package.approve(reason=reason)
                uow.preparation_packages.save(package)
                uow.events.append(
                    make_preparation_package_approved_event(
                        package_id=package.package_id,
                        case_id=case.case_id,
                        package_fingerprint=package.package_fingerprint,
                        user_id=user_id,
                        aggregate_version=package.version,
                        reason=reason,
                    )
                )

                if case.status == CaseStatus.AWAITING_APPROVAL:
                    old_case_status = case.status.value
                    case.transition_to(CaseStatus.ACTION_REQUIRED, reason="Preparation package approved locally. Ready for Phase 5.3.")
                    uow.cases.save(case)
                    uow.events.append(
                        make_case_status_changed_event(
                            case_id=case.case_id,
                            user_id=user_id,
                            aggregate_version=case.version,
                            old_status=old_case_status,
                            new_status=case.status.value,
                            reason="Preparation package approved locally. Ready for Phase 5.3.",
                        )
                    )
            else:
                old_action_status = action.status.value
                action.transition_to(ActionStatus.APPROVED, reason=reason)
                uow.actions.save(action)

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

            is_prep_pkg = action.action_type == ActionType.APPROVE_PREPARATION_PACKAGE or (
                hasattr(action.action_type, "value") and action.action_type.value == ActionType.APPROVE_PREPARATION_PACKAGE.value
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

            if is_prep_pkg:
                # Amendment 9: Rejection unwind
                package_id = action.parameters.get("package_id")
                package = uow.preparation_packages.get_by_id(package_id) if package_id else None
                if package is not None:
                    package.reject(reason=reason)
                    uow.preparation_packages.save(package)
                    uow.events.append(
                        make_preparation_package_rejected_event(
                            package_id=package.package_id,
                            case_id=case.case_id,
                            package_fingerprint=package.package_fingerprint,
                            user_id=user_id,
                            aggregate_version=package.version,
                            reason=reason,
                        )
                    )
                    for aid in package.artifact_ids:
                        art = uow.preparation_artifacts.get_by_id(aid)
                        if art and art.status == ArtifactStatus.FROZEN:
                            art.transition_to(ArtifactStatus.SUPERSEDED, reason=f"Package rejected: {reason}")
                            uow.preparation_artifacts.save(art)

                if case.status == CaseStatus.AWAITING_APPROVAL:
                    old_case_status = case.status.value
                    case.transition_to(CaseStatus.PLANNING, reason=f"Preparation package rejected: {reason}")
                    uow.cases.save(case)
                    uow.events.append(
                        make_case_status_changed_event(
                            case_id=case.case_id,
                            user_id=user_id,
                            aggregate_version=case.version,
                            old_status=old_case_status,
                            new_status=case.status.value,
                            reason=f"Preparation package rejected: {reason}",
                        )
                    )

            return approval, action
