"""Governed execution application service enforcing Phase 5.3.3 runtime safety and audit discipline."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
import uuid

from caseworker.domain.action import Action
from caseworker.domain.approval import Approval
from caseworker.domain.enums import (
    ActionStatus,
    ActionType,
    ApprovalStatus,
    CaseStatus,
    ExecutionAttemptStatus,
    ExecutionErrorCategory,
    ExecutionLeaseStatus,
    ExecutionProviderKind,
    ExecutionReceiptStatus,
    ExternalExecutionPlanStatus,
)
from caseworker.domain.errors import (
    ApprovalValidationError,
    DomainValidationError,
    EntityNotFoundError,
)
from caseworker.domain.events import (
    make_action_status_changed_event,
    make_case_status_changed_event,
    make_execution_attempt_completed_event,
    make_execution_attempt_started_event,
    make_execution_receipt_created_event,
    make_execution_reversible_point_passed_event,
    make_external_execution_plan_executed_event,
)
from caseworker.domain.execution_attempt import ExecutionAttempt, ExecutionLease
from caseworker.domain.execution_plan_integrity import ExecutionPlanIntegrityValidator
from caseworker.domain.execution_receipt import ExecutionReceipt
from caseworker.domain.provider_adapter import (
    ExecutionProviderAdapter,
    SimulatedExecutionProvider,
    SimulationMode,
)

if TYPE_CHECKING:
    from caseworker.persistence.sqlite import SQLiteCaseworkerStorage


class GovernedExecutionService:
    """Service orchestrating governed external transmission execution under strict runtime safety boundaries."""

    def __init__(
        self,
        storage: SQLiteCaseworkerStorage,
        default_provider: ExecutionProviderAdapter | None = None,
    ) -> None:
        self.storage = storage
        self.default_provider = default_provider or SimulatedExecutionProvider(SimulationMode.SUCCESS)

    def execute_action(
        self,
        user_id: str,
        action_id: str,
        provider: ExecutionProviderAdapter | None = None,
    ) -> tuple[ExecutionAttempt, ExecutionReceipt | None]:
        """Execute an approved transmission action through the two-barrier governed pipeline."""
        active_provider = provider or self.default_provider

        # Step 1: Pre-Execution Resolution and Idempotency / Concurrency Pre-checks
        with self.storage.unit_of_work() as uow:
            action = uow.actions.get_by_id(action_id)
            if action is None:
                raise EntityNotFoundError("Action", action_id)

            case = uow.cases.get_by_id(action.case_id)
            if case is None or case.user_id != user_id:
                raise EntityNotFoundError("Action", action_id)

            # Enforce that action is a tx_auth_v1 transmission action
            is_tx_action = (
                action.action_type == ActionType.SUBMIT_FORM
                and action.parameters.get("scope") == "external_transmission"
                and action.parameters.get("authorization_schema") == "tx_auth_v1"
            )
            if not is_tx_action:
                raise DomainValidationError(
                    f"Action '{action_id}' is not an authorized external transmission action."
                )

            # Idempotency Check: Existing Receipt Resolution
            existing_receipt = uow.execution_receipts.get_by_action_id(action_id)
            if existing_receipt is not None:
                if existing_receipt.status == ExecutionReceiptStatus.VERIFIED_SUCCESS:
                    attempt = uow.execution_attempts.get_by_id(existing_receipt.attempt_id)
                    if attempt is not None:
                        return attempt, existing_receipt
                elif existing_receipt.status == ExecutionReceiptStatus.AMBIGUOUS_SUBMISSION:
                    raise ApprovalValidationError(
                        "Previous transmission attempt resulted in an AMBIGUOUS submission. Automatic retry is strictly forbidden; manual operator resolution is required.",
                        details={
                            "failure_kind": "manual_resolution_required",
                            "receipt_id": existing_receipt.receipt_id,
                            "attempt_id": existing_receipt.attempt_id,
                        },
                    )

            # Check for existing unverified ambiguous attempts
            latest_attempt = uow.execution_attempts.get_latest_for_action(action_id)
            if latest_attempt is not None:
                if (
                    latest_attempt.status == ExecutionAttemptStatus.AMBIGUOUS
                    or (latest_attempt.irreversible_action_taken and latest_attempt.status != ExecutionAttemptStatus.SUCCEEDED)
                ):
                    raise ApprovalValidationError(
                        "Previous transmission attempt crossed the irreversible boundary with unverified outcome. Automatic retry is strictly forbidden; manual operator resolution is required.",
                        details={
                            "failure_kind": "manual_resolution_required",
                            "attempt_id": latest_attempt.attempt_id,
                        },
                    )

            # Check for active unexpired execution lease
            plan_id = action.parameters.get("plan_id")
            if not plan_id:
                raise DomainValidationError(f"Action '{action_id}' parameters missing 'plan_id'.")

            active_lease = uow.execution_leases.get_active_lease(action.case_id, plan_id, action_id)
            if active_lease is not None and not active_lease.is_expired():
                raise ApprovalValidationError(
                    f"An execution attempt is currently active under lease token '{active_lease.lease_token}'.",
                    details={
                        "failure_kind": "execution_in_progress",
                        "lease_token": active_lease.lease_token,
                    },
                )

            # Verify Action status
            if action.status == ActionStatus.SUCCEEDED:
                # Plan already fulfilled
                if existing_receipt is not None:
                    attempt = uow.execution_attempts.get_by_id(existing_receipt.attempt_id)
                    if attempt is not None:
                        return attempt, existing_receipt
                raise DomainValidationError("Action has already SUCCEEDED.")

            if action.status != ActionStatus.APPROVED:
                raise ApprovalValidationError(
                    f"Action '{action_id}' status is '{action.status.value}'. Only APPROVED actions can be executed.",
                    details={"failure_kind": "action_not_approved"},
                )

            # Verify Approval binding
            approval = uow.approvals.get_by_action_id(action.action_id)
            if approval is None or approval.status != ApprovalStatus.APPROVED:
                raise ApprovalValidationError(
                    "No approved authorization record found for this action.",
                    details={"failure_kind": "approval_not_approved"},
                )

            current_act_fp = action.compute_fingerprint()
            if approval.action_fingerprint != current_act_fp:
                raise ApprovalValidationError(
                    "Approval action fingerprint mismatch (Action has been modified since approval).",
                    details={"failure_kind": "authorization_binding_invalid"},
                )

            if approval.case_id != action.case_id:
                raise ApprovalValidationError(
                    "Approval case ID does not match action case ID.",
                    details={"failure_kind": "authorization_binding_invalid"},
                )

            # Verify Case Status
            if case.status != CaseStatus.ACTION_REQUIRED:
                raise ApprovalValidationError(
                    f"Case status must be ACTION_REQUIRED for execution, but is '{case.status.value}'.",
                    details={"failure_kind": "invalid_case_status"},
                )

            # Verify Plan
            plan = uow.execution_plans.get_by_id(plan_id)
            if plan is None or plan.case_id != case.case_id:
                raise EntityNotFoundError("ExternalExecutionPlan", plan_id)

            if plan.status == ExternalExecutionPlanStatus.EXECUTED:
                raise DomainValidationError("Execution plan has already been EXECUTED.")

            if plan.status != ExternalExecutionPlanStatus.READY_FOR_AUTHORIZATION:
                raise DomainValidationError(
                    f"Execution plan status must be READY_FOR_AUTHORIZATION, but is '{plan.status.value}'."
                )

            action_plan_fp = action.parameters.get("execution_plan_fingerprint") or action.parameters.get("plan_fingerprint")
            if plan.execution_plan_fingerprint != action_plan_fp:
                raise ApprovalValidationError(
                    "Plan fingerprint does not match action authorization parameter binding.",
                    details={"failure_kind": "authorization_binding_invalid"},
                )

            # Verify Package
            package = uow.preparation_packages.get_by_id(plan.package_id)
            if package is None:
                raise EntityNotFoundError("PreparationPackage", plan.package_id)

            # Load Upload Assets
            upload_assets = uow.upload_assets.list_by_plan(plan.plan_id)

            # Step 2: Barrier A — Live Pre-Staging Revalidation
            val_result = ExecutionPlanIntegrityValidator.validate(
                uow=uow,
                case=case,
                plan=plan,
                user_id=user_id,
                expected_plan_status=ExternalExecutionPlanStatus.READY_FOR_AUTHORIZATION,
                action=action,
                approval=approval,
            )
            if not val_result.is_valid:
                raise ApprovalValidationError(
                    f"Barrier A runtime revalidation failed before provider invocation: {val_result.reason}",
                    details={"failure_kind": val_result.failure_kind.value},
                )

            # Step 3: Acquire Lease and Initialize Attempt
            prev_attempts = uow.execution_attempts.list_by_case(case.case_id)
            action_attempts = [a for a in prev_attempts if a.action_id == action.action_id]
            attempt_number = len(action_attempts) + 1

            attempt_id = f"att-{uuid.uuid4().hex[:16]}"
            lease = uow.execution_leases.acquire_lease(
                case_id=case.case_id,
                plan_id=plan.plan_id,
                action_id=action.action_id,
                user_id=user_id,
                attempt_id=attempt_id,
            )

            attempt = ExecutionAttempt(
                attempt_id=attempt_id,
                action_id=action.action_id,
                approval_id=approval.approval_id,
                plan_id=plan.plan_id,
                case_id=case.case_id,
                user_id=user_id,
                lease_token=lease.lease_token,
                fencing_token=lease.fencing_token,
                status=ExecutionAttemptStatus.INITIALIZING,
                provider_kind=active_provider.provider_kind,
                attempt_number=attempt_number,
            )

            uow.execution_attempts.save(attempt)
            uow.events.append(
                make_execution_attempt_started_event(
                    user_id=user_id,
                    case_id=case.case_id,
                    plan_id=plan.plan_id,
                    action_id=action.action_id,
                    attempt_id=attempt.attempt_id,
                    provider_kind=attempt.provider_kind.value,
                    fencing_token=attempt.fencing_token,
                    attempt_number=attempt.attempt_number,
                )
            )

        # Step 4: Provider Staging (Reversible Phase)
        attempt.transition_to(ExecutionAttemptStatus.STAGING)
        with self.storage.unit_of_work() as uow:
            uow.execution_attempts.save(attempt)

        staging_outcome = active_provider.stage_submission(
            plan=plan,
            package=package,
            upload_assets=upload_assets,
        )

        if not staging_outcome.success:
            # Reversible staging failure: Action remains APPROVED, Case remains ACTION_REQUIRED
            with self.storage.unit_of_work() as uow:
                attempt.mark_failed(
                    category=staging_outcome.error_category or ExecutionErrorCategory.STAGING_VALIDATION_FAILED,
                    message=staging_outcome.error_message or "Pre-submit staging failed.",
                )
                uow.execution_attempts.save(attempt)
                uow.execution_leases.release_lease(lease.lease_id, lease.lease_token)
                uow.events.append(
                    make_execution_attempt_completed_event(
                        user_id=user_id,
                        case_id=case.case_id,
                        plan_id=plan.plan_id,
                        action_id=action.action_id,
                        attempt_id=attempt.attempt_id,
                        status=attempt.status.value,
                        irreversible_action_taken=False,
                        error_category=attempt.error_category.value if attempt.error_category else None,
                        error_message=attempt.error_message,
                    )
                )
            raise ApprovalValidationError(
                f"Pre-submit staging failed: {staging_outcome.error_message}",
                details={"failure_kind": "staging_failed", "attempt_id": attempt.attempt_id},
            )

        # Step 5: Last Reversible Point Reached
        attempt.record_last_reversible_point(
            field_count=staging_outcome.staged_field_count,
            upload_count=staging_outcome.staged_upload_count,
        )
        with self.storage.unit_of_work() as uow:
            uow.execution_attempts.save(attempt)
            uow.events.append(
                make_execution_reversible_point_passed_event(
                    user_id=user_id,
                    case_id=case.case_id,
                    plan_id=plan.plan_id,
                    action_id=action.action_id,
                    attempt_id=attempt.attempt_id,
                    fencing_token=attempt.fencing_token,
                    staged_field_count=attempt.staged_field_count,
                    staged_upload_count=attempt.staged_upload_count,
                )
            )

        # Step 6: Barrier B — Live Revalidation Immediately At Last Reversible Point + Fencing Token Check
        with self.storage.unit_of_work() as uow:
            # Verify lease and fencing token are still current and active
            current_lease = uow.execution_leases.get_by_id(lease.lease_id)
            if (
                current_lease is None
                or current_lease.status != ExecutionLeaseStatus.ACTIVE
                or current_lease.lease_token != lease.lease_token
                or current_lease.fencing_token != lease.fencing_token
                or current_lease.is_expired()
            ):
                attempt.mark_failed(
                    category=ExecutionErrorCategory.FENCING_VIOLATION,
                    message="Execution lease expired or fencing token superseded at Last Reversible Point.",
                )
                uow.execution_attempts.save(attempt)
                raise ApprovalValidationError(
                    "Execution fencing token is no longer active at Last Reversible Point. Submit aborted.",
                    details={"failure_kind": "fencing_violation"},
                )

            # Full cryptographic revalidation of Action, Approval, Plan, Package, Vault facts, and Upload BLOBs
            fresh_action = uow.actions.get_by_id(action_id)
            fresh_approval = uow.approvals.get_by_action_id(action_id)
            fresh_plan = uow.execution_plans.get_by_id(plan.plan_id)
            fresh_case = uow.cases.get_by_id(fresh_plan.case_id if fresh_plan else attempt.case_id)

            if fresh_plan is None or fresh_case is None or fresh_action is None or fresh_approval is None:
                attempt.mark_failed(
                    category=ExecutionErrorCategory.INTEGRITY_VIOLATION,
                    message="Barrier B entity lookup failed: plan, case, action, or approval missing.",
                )
                uow.execution_attempts.save(attempt)
                uow.execution_leases.release_lease(lease.lease_id, lease.lease_token)
                raise ApprovalValidationError(
                    "Barrier B live revalidation failed at Last Reversible Point: entity missing.",
                    details={"failure_kind": "entity_not_found"},
                )

            val_result_b = ExecutionPlanIntegrityValidator.validate(
                uow=uow,
                case=fresh_case,
                plan=fresh_plan,
                user_id=user_id,
                expected_plan_status=ExternalExecutionPlanStatus.READY_FOR_AUTHORIZATION,
                action=fresh_action,
                approval=fresh_approval,
            )
            if not val_result_b.is_valid:
                attempt.mark_failed(
                    category=ExecutionErrorCategory.INTEGRITY_VIOLATION,
                    message=f"Barrier B live revalidation failed: {val_result_b.reason}",
                )
                uow.execution_attempts.save(attempt)
                uow.execution_leases.release_lease(lease.lease_id, lease.lease_token)
                raise ApprovalValidationError(
                    f"Barrier B live revalidation failed at Last Reversible Point: {val_result_b.reason}",
                    details={"failure_kind": val_result_b.failure_kind.value},
                )

            # Step 7: Crossing the Irreversible Boundary
            attempt.mark_submitting()  # irreversible_action_taken = True
            uow.execution_attempts.save(attempt)

            old_act_status = fresh_action.status.value
            fresh_action.transition_to(
                ActionStatus.EXECUTING,
                reason=f"Dispatching external transmission attempt '{attempt.attempt_id}'.",
            )
            uow.actions.save(fresh_action)
            uow.events.append(
                make_action_status_changed_event(
                    action_id=fresh_action.action_id,
                    user_id=user_id,
                    aggregate_version=fresh_action.version,
                    old_status=old_act_status,
                    new_status=fresh_action.status.value,
                    reason=f"Dispatching external transmission attempt '{attempt.attempt_id}'.",
                )
            )

            old_case_status = case.status.value
            case.transition_to(
                CaseStatus.ACTION_IN_PROGRESS,
                reason=f"Dispatching external transmission attempt '{attempt.attempt_id}'.",
            )
            uow.cases.save(case)
            uow.events.append(
                make_case_status_changed_event(
                    case_id=case.case_id,
                    user_id=user_id,
                    aggregate_version=case.version,
                    old_status=old_case_status,
                    new_status=case.status.value,
                    reason=f"Dispatching external transmission attempt '{attempt.attempt_id}'.",
                )
            )

        # Dispatch irreversible submit through provider
        submit_result = active_provider.execute_irreversible_submit(
            plan=plan,
            attempt_id=attempt.attempt_id,
        )

        # Step 8: Outcome & Receipt Processing
        with self.storage.unit_of_work() as uow:
            persisted_action = uow.actions.get_by_id(action_id)
            persisted_case = uow.cases.get_by_id(case.case_id)
            persisted_plan = uow.execution_plans.get_by_id(plan.plan_id)

            if submit_result.status == ExecutionReceiptStatus.VERIFIED_SUCCESS:
                attempt.mark_succeeded()
                uow.execution_attempts.save(attempt)

                receipt = ExecutionReceipt(
                    attempt_id=attempt.attempt_id,
                    action_id=action.action_id,
                    approval_id=approval.approval_id,
                    plan_id=plan.plan_id,
                    case_id=case.case_id,
                    user_id=user_id,
                    status=ExecutionReceiptStatus.VERIFIED_SUCCESS,
                    destination_hostname=plan.destination.normalized_hostname or plan.destination.canonical_url,
                    destination_url=plan.destination.canonical_url,
                    destination_type=plan.destination.destination_type,
                    execution_plan_fingerprint=plan.execution_plan_fingerprint,
                    authorized_action_fingerprint=approval.action_fingerprint,
                    package_fingerprint=package.package_fingerprint,
                    external_reference_id=submit_result.external_confirmation_id,
                    field_count=len(plan.field_mappings),
                    upload_asset_ids=[a.asset_id for a in upload_assets],
                    upload_asset_shas=[a.content_sha256 for a in upload_assets],
                    screening_response_count=len(plan.screening_responses),
                    safe_provider_metadata=submit_result.provider_metadata,
                )
                uow.execution_receipts.save(receipt)

                # Action APPROVED -> EXECUTING -> SUCCEEDED
                old_act_status = persisted_action.status.value
                persisted_action.transition_to(
                    ActionStatus.SUCCEEDED,
                    reason=f"Transmission verified successfully. Confirmation: {receipt.external_reference_id or receipt.receipt_id}",
                )
                persisted_action.result = {
                    "receipt_id": receipt.receipt_id,
                    "receipt_hash": receipt.receipt_hash,
                    "external_reference_id": receipt.external_reference_id,
                    "status": receipt.status.value,
                }
                uow.actions.save(persisted_action)
                uow.events.append(
                    make_action_status_changed_event(
                        action_id=persisted_action.action_id,
                        user_id=user_id,
                        aggregate_version=persisted_action.version,
                        old_status=old_act_status,
                        new_status=persisted_action.status.value,
                        reason=f"Transmission verified successfully. Confirmation: {receipt.external_reference_id or receipt.receipt_id}",
                    )
                )

                # Plan transitions to EXECUTED (terminal state)
                persisted_plan.mark_executed(reason=f"Fulfilled by execution receipt '{receipt.receipt_id}'.")
                uow.execution_plans.save(persisted_plan)
                uow.events.append(
                    make_external_execution_plan_executed_event(
                        user_id=user_id,
                        case_id=case.case_id,
                        plan_id=persisted_plan.plan_id,
                        plan_fingerprint=persisted_plan.execution_plan_fingerprint,
                        receipt_id=receipt.receipt_id,
                        reason=f"Fulfilled by execution receipt '{receipt.receipt_id}'.",
                        aggregate_version=persisted_plan.version,
                    )
                )

                # Case transitions to WAITING_EXTERNAL
                old_case_status = persisted_case.status.value
                persisted_case.transition_to(
                    CaseStatus.WAITING_EXTERNAL,
                    reason=f"Transmission successfully executed to {receipt.destination_hostname}. Waiting for external response.",
                )
                persisted_case.outcome = f"Application transmitted. Confirmation: {receipt.external_reference_id or receipt.receipt_id}"
                uow.cases.save(persisted_case)
                uow.events.append(
                    make_case_status_changed_event(
                        case_id=persisted_case.case_id,
                        user_id=user_id,
                        aggregate_version=persisted_case.version,
                        old_status=old_case_status,
                        new_status=persisted_case.status.value,
                        reason=f"Transmission successfully executed to {receipt.destination_hostname}. Waiting for external response.",
                    )
                )

                uow.execution_leases.release_lease(lease.lease_id, lease.lease_token)
                uow.events.append(
                    make_execution_receipt_created_event(
                        user_id=user_id,
                        case_id=case.case_id,
                        plan_id=plan.plan_id,
                        action_id=action.action_id,
                        attempt_id=attempt.attempt_id,
                        receipt_id=receipt.receipt_id,
                        receipt_hash=receipt.receipt_hash,
                        status=receipt.status.value,
                        destination_hostname=receipt.destination_hostname,
                        destination_type=receipt.destination_type,
                        external_reference_id=receipt.external_reference_id,
                    )
                )
                uow.events.append(
                    make_execution_attempt_completed_event(
                        user_id=user_id,
                        case_id=case.case_id,
                        plan_id=plan.plan_id,
                        action_id=action.action_id,
                        attempt_id=attempt.attempt_id,
                        status=attempt.status.value,
                        irreversible_action_taken=True,
                        receipt_id=receipt.receipt_id,
                    )
                )
                return attempt, receipt

            elif submit_result.status == ExecutionReceiptStatus.AMBIGUOUS_SUBMISSION:
                attempt.mark_ambiguous(
                    message=submit_result.error_message or "Ambiguous submission dispatch outcome.",
                    snippet=submit_result.raw_response_snippet,
                )
                uow.execution_attempts.save(attempt)

                receipt = ExecutionReceipt(
                    attempt_id=attempt.attempt_id,
                    action_id=action.action_id,
                    approval_id=approval.approval_id,
                    plan_id=plan.plan_id,
                    case_id=case.case_id,
                    user_id=user_id,
                    status=ExecutionReceiptStatus.AMBIGUOUS_SUBMISSION,
                    destination_hostname=plan.destination.normalized_hostname or plan.destination.canonical_url,
                    destination_url=plan.destination.canonical_url,
                    destination_type=plan.destination.destination_type,
                    execution_plan_fingerprint=plan.execution_plan_fingerprint,
                    authorized_action_fingerprint=approval.action_fingerprint,
                    package_fingerprint=package.package_fingerprint,
                    external_reference_id=None,
                    field_count=len(plan.field_mappings),
                    upload_asset_ids=[a.asset_id for a in upload_assets],
                    upload_asset_shas=[a.content_sha256 for a in upload_assets],
                    screening_response_count=len(plan.screening_responses),
                    safe_provider_metadata=submit_result.provider_metadata,
                )
                uow.execution_receipts.save(receipt)

                old_act_status = persisted_action.status.value
                persisted_action.transition_to(
                    ActionStatus.FAILED,
                    reason=f"Transmission ambiguous: {submit_result.error_message}",
                )
                persisted_action.result = {
                    "receipt_id": receipt.receipt_id,
                    "receipt_hash": receipt.receipt_hash,
                    "status": receipt.status.value,
                    "error_message": submit_result.error_message,
                }
                uow.actions.save(persisted_action)

                old_case_status = persisted_case.status.value
                persisted_case.transition_to(
                    CaseStatus.ACTION_REQUIRED,
                    reason="Ambiguous external transmission dispatched. Automated retry blocked; operator intervention required.",
                )
                uow.cases.save(persisted_case)

                uow.execution_leases.release_lease(lease.lease_id, lease.lease_token)
                uow.events.append(
                    make_execution_receipt_created_event(
                        user_id=user_id,
                        case_id=case.case_id,
                        plan_id=plan.plan_id,
                        action_id=action.action_id,
                        attempt_id=attempt.attempt_id,
                        receipt_id=receipt.receipt_id,
                        receipt_hash=receipt.receipt_hash,
                        status=receipt.status.value,
                        destination_hostname=receipt.destination_hostname,
                        destination_type=receipt.destination_type,
                        external_reference_id=None,
                    )
                )
                uow.events.append(
                    make_execution_attempt_completed_event(
                        user_id=user_id,
                        case_id=case.case_id,
                        plan_id=plan.plan_id,
                        action_id=action.action_id,
                        attempt_id=attempt.attempt_id,
                        status=attempt.status.value,
                        irreversible_action_taken=True,
                        receipt_id=receipt.receipt_id,
                        error_category=attempt.error_category.value if attempt.error_category else None,
                        error_message=attempt.error_message,
                    )
                )
                uow.commit()
                raise ApprovalValidationError(
                    f"AMBIGUOUS submission: External transmission dispatched past irreversible boundary with unverified outcome ({submit_result.error_message}). Manual operator resolution required.",
                    details={"failure_kind": "ambiguous_submission", "receipt_id": receipt.receipt_id, "attempt_id": attempt.attempt_id},
                )

            else:  # CONFIRMED_FAILED
                attempt.mark_failed(
                    category=submit_result.error_category or ExecutionErrorCategory.SUBMIT_REJECTED,
                    message=submit_result.error_message or "Transmission rejected by destination.",
                    snippet=submit_result.raw_response_snippet,
                )
                uow.execution_attempts.save(attempt)

                receipt = ExecutionReceipt(
                    attempt_id=attempt.attempt_id,
                    action_id=action.action_id,
                    approval_id=approval.approval_id,
                    plan_id=plan.plan_id,
                    case_id=case.case_id,
                    user_id=user_id,
                    status=ExecutionReceiptStatus.CONFIRMED_FAILED,
                    destination_hostname=plan.destination.normalized_hostname or plan.destination.canonical_url,
                    destination_url=plan.destination.canonical_url,
                    destination_type=plan.destination.destination_type,
                    execution_plan_fingerprint=plan.execution_plan_fingerprint,
                    authorized_action_fingerprint=approval.action_fingerprint,
                    package_fingerprint=package.package_fingerprint,
                    external_reference_id=None,
                    field_count=len(plan.field_mappings),
                    upload_asset_ids=[a.asset_id for a in upload_assets],
                    upload_asset_shas=[a.content_sha256 for a in upload_assets],
                    screening_response_count=len(plan.screening_responses),
                    safe_provider_metadata=submit_result.provider_metadata,
                )
                uow.execution_receipts.save(receipt)

                old_act_status = persisted_action.status.value
                persisted_action.transition_to(
                    ActionStatus.FAILED,
                    reason=f"Transmission rejected: {submit_result.error_message}",
                )
                persisted_action.result = {
                    "receipt_id": receipt.receipt_id,
                    "receipt_hash": receipt.receipt_hash,
                    "status": receipt.status.value,
                    "error_message": submit_result.error_message,
                }
                uow.actions.save(persisted_action)

                old_case_status = persisted_case.status.value
                persisted_case.transition_to(
                    CaseStatus.ACTION_REQUIRED,
                    reason=f"Transmission rejected by destination: {submit_result.error_message}",
                )
                uow.cases.save(persisted_case)

                uow.execution_leases.release_lease(lease.lease_id, lease.lease_token)
                uow.events.append(
                    make_execution_receipt_created_event(
                        user_id=user_id,
                        case_id=case.case_id,
                        plan_id=plan.plan_id,
                        action_id=action.action_id,
                        attempt_id=attempt.attempt_id,
                        receipt_id=receipt.receipt_id,
                        receipt_hash=receipt.receipt_hash,
                        status=receipt.status.value,
                        destination_hostname=receipt.destination_hostname,
                        destination_type=receipt.destination_type,
                        external_reference_id=None,
                    )
                )
                uow.events.append(
                    make_execution_attempt_completed_event(
                        user_id=user_id,
                        case_id=case.case_id,
                        plan_id=plan.plan_id,
                        action_id=action.action_id,
                        attempt_id=attempt.attempt_id,
                        status=attempt.status.value,
                        irreversible_action_taken=True,
                        receipt_id=receipt.receipt_id,
                        error_category=attempt.error_category.value if attempt.error_category else None,
                        error_message=attempt.error_message,
                    )
                )
                uow.commit()
                raise ApprovalValidationError(
                    f"External transmission rejected by destination: {submit_result.error_message}",
                    details={"failure_kind": "submit_rejected", "receipt_id": receipt.receipt_id, "attempt_id": attempt.attempt_id},
                )

    def get_receipt_for_case(self, user_id: str, case_id: str) -> ExecutionReceipt | None:
        """Retrieve execution receipt for a case, verifying user ownership."""
        with self.storage.unit_of_work() as uow:
            case = uow.cases.get_by_id(case_id)
            if case is None or case.user_id != user_id:
                return None
            return uow.execution_receipts.get_by_case_id(case_id)

    def list_attempts_for_case(self, user_id: str, case_id: str) -> list[ExecutionAttempt]:
        """List execution attempts for a case, verifying user ownership."""
        with self.storage.unit_of_work() as uow:
            case = uow.cases.get_by_id(case_id)
            if case is None or case.user_id != user_id:
                return []
            return uow.execution_attempts.list_by_case(case_id)
