"""Governed execution application service enforcing Phase 5.3.3 runtime safety and audit discipline."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
import inspect
import os
import re
import uuid

from caseworker.domain.action import Action
from caseworker.domain.approval import Approval
from caseworker.domain.enums import (
    ActionStatus,
    ActionType,
    ApprovalStatus,
    CaseStatus,
    ExecutionAttemptStatus,
    ExecutionDestinationType,
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
    ExecutionAttemptContext,
    ExecutionProviderAdapter,
    ProviderSubmissionResult,
    SimulatedExecutionProvider,
    SimulationMode,
)
from caseworker.adapters.gmail_provider import (
    GmailApplicationExecutionProvider,
    OAuthCredentialProvider,
)
from caseworker.domain.types import compute_sha256_bytes, now_utc

if TYPE_CHECKING:
    from caseworker.persistence.sqlite import SQLiteCaseworkerStorage


_SENSITIVE_PATTERNS = [
    re.compile(r'(?i)(bearer\s+)[a-zA-Z0-9_\-\.]{10,}'),
    re.compile(r'(?i)(token[=:\s]+)[a-zA-Z0-9_\-\.]{10,}'),
    re.compile(r'(?i)(password[=:\s]+)[^\s,;&]+'),
    re.compile(r'(?i)(secret[=:\s]+)[^\s,;&]+'),
    re.compile(r'(?i)(api[_\-]?key[=:\s]+)[a-zA-Z0-9_\-\.]{10,}'),
    re.compile(r'(?i)(authorization[=:\s]+)[^\r\n]+'),
    re.compile(r'(?i)(cookie[=:\s]+)[^\r\n]+'),
]


def _sanitize_diagnostic_message(msg: str | None) -> str:
    """Sanitize diagnostic text by redacting credentials, tokens, and sensitive headers."""
    if not msg:
        return ""
    cleaned = str(msg)
    for pat in _SENSITIVE_PATTERNS:
        cleaned = pat.sub(r'\1[REDACTED]', cleaned)
    if len(cleaned) > 500:
        cleaned = cleaned[:497] + "..."
    return cleaned


def _sanitize_provider_metadata(meta: dict[str, Any] | None) -> dict[str, Any]:
    """Sanitize provider metadata dictionary by redacting sensitive keys and values."""
    if not meta:
        return {}
    clean: dict[str, Any] = {}
    for k, v in meta.items():
        if any(secret in k.lower() for secret in ("auth", "token", "password", "secret", "cookie", "key", "credential")):
            clean[k] = "[REDACTED]"
        elif isinstance(v, str):
            clean[k] = _sanitize_diagnostic_message(v)
        elif isinstance(v, (int, float, bool)):
            clean[k] = v
        elif isinstance(v, (list, tuple)):
            clean[k] = [
                _sanitize_diagnostic_message(str(item)) if isinstance(item, str) else item
                for item in v
            ]
        elif isinstance(v, dict):
            clean[k] = _sanitize_provider_metadata(v)
        else:
            clean[k] = str(v)[:200]
    return clean


def _dispatch_stage_submission(
    provider: ExecutionProviderAdapter,
    plan: Any,
    package: Any,
    upload_assets: Any,
    context: ExecutionAttemptContext,
) -> Any:
    """Invoke stage_submission with backward-compatibility for providers without context parameter."""
    sig = inspect.signature(provider.stage_submission)
    if "context" in sig.parameters or any(p.kind == inspect.Parameter.VAR_KEYWORD for p in sig.parameters.values()):
        return provider.stage_submission(
            plan=plan,
            package=package,
            upload_assets=upload_assets,
            context=context,
        )
    return provider.stage_submission(
        plan=plan,
        package=package,
        upload_assets=upload_assets,
    )


def _dispatch_execute_irreversible_submit(
    provider: ExecutionProviderAdapter,
    plan: Any,
    attempt_id: str,
    context: ExecutionAttemptContext,
) -> Any:
    """Invoke execute_irreversible_submit with backward-compatibility for providers without context parameter."""
    sig = inspect.signature(provider.execute_irreversible_submit)
    if "context" in sig.parameters or any(p.kind == inspect.Parameter.VAR_KEYWORD for p in sig.parameters.values()):
        return provider.execute_irreversible_submit(
            plan=plan,
            attempt_id=attempt_id,
            context=context,
        )
    return provider.execute_irreversible_submit(
        plan=plan,
        attempt_id=attempt_id,
    )


class GovernedExecutionService:
    """Service orchestrating governed external transmission execution under strict runtime safety boundaries."""

    def __init__(
        self,
        storage: SQLiteCaseworkerStorage,
        default_provider: ExecutionProviderAdapter | None = None,
        email_provider: ExecutionProviderAdapter | None = None,
        allow_simulation: bool | None = None,
        real_execution_enabled: bool | None = None,
        gmail_execution_enabled: bool | None = None,
        credential_provider: OAuthCredentialProvider | None = None,
    ) -> None:
        self.storage = storage
        if allow_simulation is None:
            self.allow_simulation = os.getenv("CASEWORKER_SIMULATION_ENABLED", "false").lower() in ("true", "1", "yes")
        else:
            self.allow_simulation = bool(allow_simulation)

        if real_execution_enabled is None:
            self.real_execution_enabled = os.getenv("CASEWORKER_REAL_EXECUTION_ENABLED", "false").lower() in ("true", "1", "yes")
        else:
            self.real_execution_enabled = bool(real_execution_enabled)

        if gmail_execution_enabled is None:
            self.gmail_execution_enabled = os.getenv("CASEWORKER_GMAIL_EXECUTION_ENABLED", "false").lower() in ("true", "1", "yes")
        else:
            self.gmail_execution_enabled = bool(gmail_execution_enabled)

        self.credential_provider = credential_provider
        self.default_provider = default_provider or SimulatedExecutionProvider(SimulationMode.SUCCESS)
        self.email_provider = email_provider

    def execute_action(
        self,
        user_id: str,
        action_id: str,
        provider: ExecutionProviderAdapter | None = None,
    ) -> tuple[ExecutionAttempt, ExecutionReceipt | None]:
        """Execute an approved transmission action through the two-barrier governed pipeline."""
        active_provider = provider
        if active_provider is not None:
            # Trusted Simulation Environment Boundary (Refinement 1)
            if active_provider.provider_kind == ExecutionProviderKind.SIMULATED_TEST and not self.allow_simulation:
                raise ApprovalValidationError(
                    "Simulated execution provider is restricted to trusted, isolated demonstration or test environments. "
                    "In live production environments, real-world execution requires an authorized genuine provider adapter.",
                    details={"failure_kind": "simulation_not_permitted"},
                )

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

            # Active Lease Check FIRST (Refinement 1 & 2): Protect active unexpired workers
            plan_id_param = action.parameters.get("plan_id", "")
            active_lease = uow.execution_leases.get_active_lease(action.case_id, plan_id_param, action_id) if plan_id_param else None
            if active_lease is not None and not active_lease.is_expired():
                raise ApprovalValidationError(
                    f"An execution attempt is currently active under lease token '{active_lease.lease_token}'.",
                    details={"failure_kind": "execution_in_progress", "lease_token": active_lease.lease_token},
                )

            # Check for existing unverified ambiguous attempts or interrupted dispatch (Refinements 1, 2, 3)
            latest_attempt = uow.execution_attempts.get_latest_for_action(action_id)
            if latest_attempt is not None:
                if (
                    latest_attempt.status == ExecutionAttemptStatus.AMBIGUOUS
                    or (latest_attempt.irreversible_action_taken and latest_attempt.status != ExecutionAttemptStatus.SUCCEEDED)
                ):
                    # Interrupted past irreversible boundary without an existing ambiguous receipt
                    if existing_receipt is None and latest_attempt.status == ExecutionAttemptStatus.SUBMITTING:
                        latest_attempt.mark_ambiguous(
                            message="Transmission attempt was interrupted during irreversible submission dispatch. Outcome is unverified.",
                        )
                        uow.execution_attempts.save(latest_attempt)

                        plan_rec = uow.execution_plans.get_by_id(plan_id_param) if plan_id_param else None
                        approval_rec = uow.approvals.get_by_action_id(action_id)

                        reconciled_receipt = ExecutionReceipt(
                            attempt_id=latest_attempt.attempt_id,
                            action_id=action.action_id,
                            approval_id=approval_rec.approval_id if approval_rec else "app-unknown",
                            plan_id=plan_rec.plan_id if plan_rec else "plan-unknown",
                            case_id=case.case_id,
                            user_id=user_id,
                            status=ExecutionReceiptStatus.AMBIGUOUS_SUBMISSION,
                            destination_hostname=plan_rec.destination.normalized_hostname if plan_rec else "unknown",
                            destination_url=plan_rec.destination.canonical_url if plan_rec else "unknown",
                            destination_type=plan_rec.destination.destination_type if plan_rec else "form_submission",
                            execution_plan_fingerprint=plan_rec.execution_plan_fingerprint if plan_rec else "",
                            authorized_action_fingerprint=approval_rec.action_fingerprint if approval_rec else "",
                            package_fingerprint=plan_rec.package_fingerprint if plan_rec else "",
                            external_reference_id=None,
                            safe_provider_metadata={
                                "reconciled_after_interruption": True,
                                "is_simulated": latest_attempt.provider_kind == ExecutionProviderKind.SIMULATED_TEST,
                                "provider_kind": latest_attempt.provider_kind.value,
                            },
                        )
                        uow.execution_receipts.save(reconciled_receipt)

                        old_act_status = action.status.value
                        if action.status == ActionStatus.EXECUTING:
                            action.reconcile_interrupted_failure(
                                reason="Interrupted transmission reconciled as ambiguous. Operator intervention required."
                            )
                            uow.actions.save(action)
                            uow.events.append(
                                make_action_status_changed_event(
                                    action_id=action.action_id,
                                    user_id=user_id,
                                    aggregate_version=action.version,
                                    old_status=old_act_status,
                                    new_status=action.status.value,
                                    reason="Interrupted transmission reconciled as ambiguous. Operator intervention required.",
                                )
                            )
                        else:
                            # An unexpected status (e.g. APPROVED paired with SUBMITTING) is inconsistent
                            raise ApprovalValidationError(
                                f"Action '{action.action_id}' is in inconsistent state '{action.status.value}' while attempt is SUBMITTING.",
                                details={"failure_kind": "inconsistent_recovery_state", "action_id": action.action_id},
                            )

                        old_case_status = case.status.value
                        if case.status != CaseStatus.ACTION_REQUIRED:
                            case.transition_to(
                                CaseStatus.ACTION_REQUIRED,
                                reason="Interrupted transmission reconciled as ambiguous. Operator intervention required.",
                            )
                            uow.cases.save(case)
                            uow.events.append(
                                make_case_status_changed_event(
                                    case_id=case.case_id,
                                    user_id=user_id,
                                    aggregate_version=case.version,
                                    old_status=old_case_status,
                                    new_status=case.status.value,
                                    reason="Interrupted transmission reconciled as ambiguous. Operator intervention required.",
                                )
                            )

                        # Durably close the expired lease for this interrupted attempt
                        lease_to_close = (
                            uow.execution_leases.get_by_token(latest_attempt.lease_token)
                            if latest_attempt.lease_token
                            else None
                        )
                        if lease_to_close is None and latest_attempt.lease_token:
                            lease_to_close = uow.execution_leases.get_by_id(latest_attempt.lease_token)
                        if lease_to_close is None and active_lease:
                            lease_to_close = active_lease

                        if lease_to_close is not None:
                            # Verify Action, plan, attempt, and token binding
                            if (
                                lease_to_close.action_id == action.action_id
                                and lease_to_close.case_id == case.case_id
                                and (not plan_id_param or lease_to_close.plan_id == plan_id_param)
                                and lease_to_close.attempt_id == latest_attempt.attempt_id
                                and (not latest_attempt.lease_token or lease_to_close.lease_token == latest_attempt.lease_token)
                            ):
                                if lease_to_close.status == ExecutionLeaseStatus.ACTIVE:
                                    uow.execution_leases.release_lease(lease_to_close.lease_id, lease_to_close.lease_token)

                        uow.events.append(
                            make_execution_receipt_created_event(
                                user_id=user_id,
                                case_id=case.case_id,
                                plan_id=plan_rec.plan_id if plan_rec else "",
                                action_id=action.action_id,
                                attempt_id=latest_attempt.attempt_id,
                                receipt_id=reconciled_receipt.receipt_id,
                                receipt_hash=reconciled_receipt.receipt_hash,
                                status=reconciled_receipt.status.value,
                                destination_hostname=reconciled_receipt.destination_hostname,
                                destination_type=reconciled_receipt.destination_type,
                                external_reference_id=None,
                            )
                        )
                        uow.events.append(
                            make_execution_attempt_completed_event(
                                user_id=user_id,
                                case_id=case.case_id,
                                plan_id=plan_rec.plan_id if plan_rec else "",
                                action_id=action.action_id,
                                attempt_id=latest_attempt.attempt_id,
                                status=latest_attempt.status.value,
                                irreversible_action_taken=True,
                                receipt_id=reconciled_receipt.receipt_id,
                                error_category=ExecutionErrorCategory.UNKNOWN.value,
                                error_message=latest_attempt.error_message,
                            )
                        )
                        uow.commit()

                    raise ApprovalValidationError(
                        "Previous transmission attempt crossed the irreversible boundary with unverified outcome. Automatic retry is strictly forbidden; manual operator resolution is required.",
                        details={
                            "failure_kind": "manual_resolution_required",
                            "attempt_id": latest_attempt.attempt_id,
                        },
                    )

                elif not latest_attempt.irreversible_action_taken and latest_attempt.status in (
                    ExecutionAttemptStatus.INITIALIZING,
                    ExecutionAttemptStatus.STAGING,
                    ExecutionAttemptStatus.AT_REVERSIBLE_POINT,
                ):
                    plan_id_param = action.parameters.get("plan_id", "")
                    active_lease_check = uow.execution_leases.get_active_lease(action.case_id, plan_id_param, action_id) if plan_id_param else None
                    if active_lease_check is None or active_lease_check.is_expired():
                        latest_attempt.mark_failed(
                            category=ExecutionErrorCategory.LEASE_EXPIRED,
                            message="Previous execution attempt abandoned before crossing irreversible boundary: lease expired.",
                        )
                        uow.execution_attempts.save(latest_attempt)
                        # Durably close the expired lease for this abandoned attempt
                        lease_to_close = (
                            uow.execution_leases.get_by_token(latest_attempt.lease_token)
                            if latest_attempt.lease_token
                            else None
                        )
                        if lease_to_close is None and latest_attempt.lease_token:
                            lease_to_close = uow.execution_leases.get_by_id(latest_attempt.lease_token)
                        if lease_to_close is None and active_lease_check:
                            lease_to_close = active_lease_check

                        if lease_to_close is not None:
                            # Verify Action, plan, attempt, and token binding
                            if (
                                lease_to_close.action_id == action.action_id
                                and lease_to_close.case_id == case.case_id
                                and (not plan_id_param or lease_to_close.plan_id == plan_id_param)
                                and lease_to_close.attempt_id == latest_attempt.attempt_id
                                and (not latest_attempt.lease_token or lease_to_close.lease_token == latest_attempt.lease_token)
                            ):
                                if lease_to_close.status == ExecutionLeaseStatus.ACTIVE:
                                    uow.execution_leases.release_lease(lease_to_close.lease_id, lease_to_close.lease_token)
                        uow.events.append(
                            make_execution_attempt_completed_event(
                                user_id=user_id,
                                case_id=case.case_id,
                                plan_id=plan_id_param,
                                action_id=action.action_id,
                                attempt_id=latest_attempt.attempt_id,
                                status=latest_attempt.status.value,
                                irreversible_action_taken=False,
                                error_category=ExecutionErrorCategory.LEASE_EXPIRED.value,
                                error_message=latest_attempt.error_message,
                            )
                        )
                        uow.commit()

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

            # Resolve provider adapter if not explicitly supplied
            if active_provider is None:
                dest_val = (
                    plan.destination.destination_type.value
                    if hasattr(plan.destination.destination_type, "value")
                    else str(plan.destination.destination_type)
                )
                if dest_val == ExecutionDestinationType.EMAIL.value:
                    active_provider = self.email_provider or GmailApplicationExecutionProvider(
                        credential_provider=self.credential_provider,
                        real_execution_enabled=self.real_execution_enabled,
                        gmail_execution_enabled=self.gmail_execution_enabled,
                    )
                else:
                    active_provider = self.default_provider

                if active_provider.provider_kind == ExecutionProviderKind.SIMULATED_TEST and not self.allow_simulation:
                    raise ApprovalValidationError(
                        "Simulated execution provider is restricted to trusted, isolated demonstration or test environments. "
                        "In live production environments, real-world execution requires an authorized genuine provider adapter.",
                        details={"failure_kind": "simulation_not_permitted"},
                    )

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

            attempt_context = ExecutionAttemptContext(
                attempt_id=attempt.attempt_id,
                lease_token=lease.lease_token,
                fencing_token=lease.fencing_token,
                user_id=user_id,
                case_id=case.case_id,
                plan_id=plan.plan_id,
                action_id=action.action_id,
            )

        # Step 4: Provider Staging (Reversible Phase)
        attempt.transition_to(ExecutionAttemptStatus.STAGING)
        with self.storage.unit_of_work() as uow:
            uow.execution_attempts.save(attempt)

        try:
            staging_outcome = _dispatch_stage_submission(
                provider=active_provider,
                plan=plan,
                package=package,
                upload_assets=upload_assets,
                context=attempt_context,
            )
        except Exception as exc:
            sanitized_err = _sanitize_diagnostic_message(str(exc))
            with self.storage.unit_of_work() as uow:
                attempt.mark_failed(
                    category=ExecutionErrorCategory.PRE_SUBMIT_NETWORK,
                    message=f"Provider staging raised unexpected exception: {sanitized_err}",
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
                uow.commit()
            raise ApprovalValidationError(
                f"Provider staging failed unexpectedly: {sanitized_err}",
                details={"failure_kind": "staging_failed", "attempt_id": attempt.attempt_id},
            )

        # Complete staging verification (Amendment 2)
        staging_failures: list[str] = []
        if not staging_outcome.success:
            staging_failures.append(staging_outcome.error_message or "Provider reported staging failure.")
        if not staging_outcome.submit_control_ready:
            staging_failures.append("Final submit control is not ready at destination.")
        if plan.final_submit_control is None:
            staging_failures.append("Execution plan has no sealed final submit control.")

        # Check required fields
        required_target_keys = {m.target_field_id for m in plan.field_mappings}
        staged_keys_set = set(staging_outcome.staged_field_keys)
        if staging_outcome.staged_field_keys or plan.field_mappings:
            missing_fields = required_target_keys - staged_keys_set
            if missing_fields:
                staging_failures.append(f"Missing required staged field keys: {sorted(missing_fields)}")

        # Check required upload slots
        required_upload_slots = {u.asset_id for u in upload_assets if u.asset_id}
        staged_slots_set = set(staging_outcome.staged_upload_slot_ids)
        if staging_outcome.staged_upload_slot_ids or required_upload_slots:
            missing_slots = required_upload_slots - staged_slots_set
            if missing_slots:
                staging_failures.append(f"Missing required staged upload slot IDs: {sorted(missing_slots)}")

        # Check required screening responses
        required_screening_ids = {q.question_id for q in plan.screening_responses}
        staged_screening_set = set(staging_outcome.staged_screening_question_ids)
        if staging_outcome.staged_screening_question_ids or required_screening_ids:
            missing_screening = required_screening_ids - staged_screening_set
            if missing_screening:
                staging_failures.append(f"Missing required staged screening response IDs: {sorted(missing_screening)}")

        if staging_failures:
            err_msg = _sanitize_diagnostic_message("; ".join(staging_failures))
            with self.storage.unit_of_work() as uow:
                attempt.mark_failed(
                    category=staging_outcome.error_category or ExecutionErrorCategory.STAGING_VALIDATION_FAILED,
                    message=f"Pre-submit staging validation failed: {err_msg}",
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
                uow.commit()
            raise ApprovalValidationError(
                f"Pre-submit staging validation failed: {err_msg}",
                details={"failure_kind": "staging_failed", "attempt_id": attempt.attempt_id},
            )

        if staging_outcome.sealed_payload_hash:
            attempt_context.sealed_payload_hash = staging_outcome.sealed_payload_hash
        if staging_outcome.sealed_payload_bytes is not None:
            attempt_context.sealed_payload_bytes = staging_outcome.sealed_payload_bytes

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
            # 1. Verify lease and fencing token are still current and active
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
                if current_lease and current_lease.lease_token == lease.lease_token:
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
                        error_category=ExecutionErrorCategory.FENCING_VIOLATION.value,
                        error_message=attempt.error_message,
                    )
                )
                uow.commit()
                raise ApprovalValidationError(
                    "Execution fencing token is no longer active at Last Reversible Point. Submit aborted.",
                    details={"failure_kind": "fencing_violation"},
                )

            # 2. Fresh entity lookups
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
                uow.events.append(
                    make_execution_attempt_completed_event(
                        user_id=user_id,
                        case_id=case.case_id,
                        plan_id=plan.plan_id,
                        action_id=action.action_id,
                        attempt_id=attempt.attempt_id,
                        status=attempt.status.value,
                        irreversible_action_taken=False,
                        error_category=ExecutionErrorCategory.INTEGRITY_VIOLATION.value,
                        error_message=attempt.error_message,
                    )
                )
                uow.commit()
                raise ApprovalValidationError(
                    "Barrier B live revalidation failed at Last Reversible Point: entity missing.",
                    details={"failure_kind": "entity_not_found"},
                )

            # 3. Fresh check of Action, Approval, and Case statuses (Amendment 4 from review)
            if fresh_action.status != ActionStatus.APPROVED:
                attempt.mark_failed(
                    category=ExecutionErrorCategory.INTEGRITY_VIOLATION,
                    message=f"Barrier B live revalidation failed: Action status is '{fresh_action.status.value}', expected APPROVED.",
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
                        error_category=ExecutionErrorCategory.INTEGRITY_VIOLATION.value,
                        error_message=attempt.error_message,
                    )
                )
                uow.commit()
                raise ApprovalValidationError(
                    f"Barrier B live revalidation failed at Last Reversible Point: Action status changed to '{fresh_action.status.value}'.",
                    details={"failure_kind": "action_not_approved"},
                )

            if fresh_approval.status != ApprovalStatus.APPROVED:
                attempt.mark_failed(
                    category=ExecutionErrorCategory.INTEGRITY_VIOLATION,
                    message=f"Barrier B live revalidation failed: Approval status is '{fresh_approval.status.value}', expected APPROVED.",
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
                        error_category=ExecutionErrorCategory.INTEGRITY_VIOLATION.value,
                        error_message=attempt.error_message,
                    )
                )
                uow.commit()
                raise ApprovalValidationError(
                    f"Barrier B live revalidation failed at Last Reversible Point: Approval status changed to '{fresh_approval.status.value}'.",
                    details={"failure_kind": "approval_not_approved"},
                )

            if fresh_case.status != CaseStatus.ACTION_REQUIRED:
                attempt.mark_failed(
                    category=ExecutionErrorCategory.INTEGRITY_VIOLATION,
                    message=f"Barrier B live revalidation failed: Case status is '{fresh_case.status.value}', expected ACTION_REQUIRED.",
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
                        error_category=ExecutionErrorCategory.INTEGRITY_VIOLATION.value,
                        error_message=attempt.error_message,
                    )
                )
                uow.commit()
                raise ApprovalValidationError(
                    f"Barrier B live revalidation failed at Last Reversible Point: Case status changed to '{fresh_case.status.value}'.",
                    details={"failure_kind": "invalid_case_status"},
                )

            # 4. Fingerprint binding check
            computed_act_fp = fresh_action.compute_fingerprint()
            if fresh_approval.action_fingerprint != computed_act_fp:
                attempt.mark_failed(
                    category=ExecutionErrorCategory.INTEGRITY_VIOLATION,
                    message="Barrier B live revalidation failed: Action fingerprint mismatch with approval.",
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
                        error_category=ExecutionErrorCategory.INTEGRITY_VIOLATION.value,
                        error_message=attempt.error_message,
                    )
                )
                uow.commit()
                raise ApprovalValidationError(
                    "Barrier B live revalidation failed at Last Reversible Point: Action fingerprint mismatch.",
                    details={"failure_kind": "authorization_binding_invalid"},
                )

            # 5. Full plan integrity validator
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
                uow.events.append(
                    make_execution_attempt_completed_event(
                        user_id=user_id,
                        case_id=case.case_id,
                        plan_id=plan.plan_id,
                        action_id=action.action_id,
                        attempt_id=attempt.attempt_id,
                        status=attempt.status.value,
                        irreversible_action_taken=False,
                        error_category=ExecutionErrorCategory.INTEGRITY_VIOLATION.value,
                        error_message=attempt.error_message,
                    )
                )
                uow.commit()
                raise ApprovalValidationError(
                    f"Barrier B live revalidation failed at Last Reversible Point: {val_result_b.reason}",
                    details={"failure_kind": val_result_b.failure_kind.value},
                )

            # 6. Sealed payload integrity verification for email plans (Amendment A)
            if fresh_plan.email_manifest is not None:
                actual_bytes_hash = (
                    compute_sha256_bytes(attempt_context.sealed_payload_bytes)
                    if attempt_context.sealed_payload_bytes is not None
                    else ""
                )
                if (
                    not attempt_context.sealed_payload_bytes
                    or attempt_context.sealed_payload_hash != fresh_plan.email_manifest.sealed_payload_hash
                    or actual_bytes_hash != fresh_plan.email_manifest.sealed_payload_hash
                ):
                    attempt.mark_failed(
                        category=ExecutionErrorCategory.PAYLOAD_INTEGRITY_MISMATCH,
                        message="Barrier B live revalidation failed: Staged sealed payload hash does not match authorized plan manifest hash.",
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
                            error_category=ExecutionErrorCategory.PAYLOAD_INTEGRITY_MISMATCH.value,
                            error_message=attempt.error_message,
                        )
                    )
                    uow.commit()
                    raise ApprovalValidationError(
                        "Barrier B live revalidation failed at Last Reversible Point: Sealed payload hash mismatch.",
                        details={"failure_kind": "payload_integrity_mismatch"},
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

        # Dispatch irreversible submit through provider with conservative error handling
        try:
            submit_result = _dispatch_execute_irreversible_submit(
                provider=active_provider,
                plan=plan,
                attempt_id=attempt.attempt_id,
                context=attempt_context,
            )
        except Exception as exc:
            # Conservative handling of unexpected exception after irreversible boundary crossing (Amendment 3)
            sanitized_exc = _sanitize_diagnostic_message(str(exc))
            submit_result = ProviderSubmissionResult(
                status=ExecutionReceiptStatus.AMBIGUOUS_SUBMISSION,
                external_confirmation_id=None,
                error_category=ExecutionErrorCategory.UNKNOWN,
                error_message=f"Unexpected exception during irreversible submit dispatch: {sanitized_exc}",
                raw_response_snippet=_sanitize_diagnostic_message(repr(exc)),
                provider_metadata={
                    "exception_type": type(exc).__name__,
                    "sanitized_error": sanitized_exc,
                    "is_simulated": active_provider.provider_kind == ExecutionProviderKind.SIMULATED_TEST,
                    "provider_kind": active_provider.provider_kind.value,
                },
            )

        # Step 8: Outcome & Receipt Processing
        with self.storage.unit_of_work() as uow:
            current_attempt = uow.execution_attempts.get_by_id(attempt.attempt_id)
            existing_rcpt = uow.execution_receipts.get_by_action_id(action_id)
            persisted_lease = uow.execution_leases.get_by_id(lease.lease_id)

            # Late-Worker Protection (Refinement 2)
            # An expired lease does not prove that the original worker has terminated.
            # If another recovery transaction already marked the attempt AMBIGUOUS or created a receipt,
            # or if the lease was released/expired, the original worker must not overwrite that decision.
            is_superseded = (
                current_attempt is None
                or current_attempt.status != ExecutionAttemptStatus.SUBMITTING
                or existing_rcpt is not None
                or persisted_lease is None
                or persisted_lease.lease_token != lease.lease_token
                or persisted_lease.status != ExecutionLeaseStatus.ACTIVE
                or persisted_lease.is_expired()
            )
            if is_superseded:
                if (
                    persisted_lease
                    and persisted_lease.lease_token == lease.lease_token
                    and persisted_lease.status == ExecutionLeaseStatus.ACTIVE
                ):
                    uow.execution_leases.release_lease(persisted_lease.lease_id, persisted_lease.lease_token)
                    uow.commit()

                raise ApprovalValidationError(
                    "Late execution outcome discarded: Worker lease expired or attempt was reconciled by another process. Manual operator resolution is required.",
                    details={
                        "failure_kind": "late_worker_superseded",
                        "attempt_id": attempt.attempt_id,
                        "existing_receipt_id": existing_rcpt.receipt_id if existing_rcpt else None,
                    },
                )

            persisted_action = uow.actions.get_by_id(action_id)
            persisted_case = uow.cases.get_by_id(case.case_id)
            persisted_plan = uow.execution_plans.get_by_id(plan.plan_id)

            sanitized_provider_metadata = _sanitize_provider_metadata(submit_result.provider_metadata)

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
                    safe_provider_metadata=sanitized_provider_metadata,
                )
                uow.execution_receipts.save(receipt)

                is_sim = (
                    attempt.provider_kind == ExecutionProviderKind.SIMULATED_TEST
                    or sanitized_provider_metadata.get("is_simulated") is True
                )
                if is_sim:
                    act_reason = f"Simulated transmission completed (Simulation Mode). Reference: {receipt.external_reference_id or receipt.receipt_id}"
                    case_reason = f"Simulated external transmission executed via {attempt.provider_kind.value} to {receipt.destination_hostname}. (Simulation only)."
                    case_outcome = f"Simulated transmission completed (Simulation Mode — No genuine external transmission). Reference: {receipt.external_reference_id or receipt.receipt_id}"
                else:
                    act_reason = f"Transmission verified successfully. Confirmation: {receipt.external_reference_id or receipt.receipt_id}"
                    case_reason = f"Transmission successfully executed to {receipt.destination_hostname}. Waiting for external response."
                    case_outcome = f"Application transmitted. Confirmation: {receipt.external_reference_id or receipt.receipt_id}"

                # Action APPROVED -> EXECUTING -> SUCCEEDED
                old_act_status = persisted_action.status.value
                persisted_action.transition_to(
                    ActionStatus.SUCCEEDED,
                    reason=act_reason,
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
                        reason=act_reason,
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
                    reason=case_reason,
                )
                persisted_case.outcome = case_outcome
                uow.cases.save(persisted_case)
                uow.events.append(
                    make_case_status_changed_event(
                        case_id=persisted_case.case_id,
                        user_id=user_id,
                        aggregate_version=persisted_case.version,
                        old_status=old_case_status,
                        new_status=persisted_case.status.value,
                        reason=case_reason,
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
                sanitized_err = _sanitize_diagnostic_message(submit_result.error_message or "Ambiguous submission dispatch outcome.")
                sanitized_snippet = _sanitize_diagnostic_message(submit_result.raw_response_snippet)
                attempt.mark_ambiguous(
                    message=sanitized_err,
                    snippet=sanitized_snippet,
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
                    safe_provider_metadata=sanitized_provider_metadata,
                )
                uow.execution_receipts.save(receipt)

                old_act_status = persisted_action.status.value
                persisted_action.transition_to(
                    ActionStatus.FAILED,
                    reason=f"Transmission ambiguous: {sanitized_err}",
                )
                persisted_action.result = {
                    "receipt_id": receipt.receipt_id,
                    "receipt_hash": receipt.receipt_hash,
                    "status": receipt.status.value,
                    "error_message": sanitized_err,
                }
                uow.actions.save(persisted_action)
                uow.events.append(
                    make_action_status_changed_event(
                        action_id=persisted_action.action_id,
                        user_id=user_id,
                        aggregate_version=persisted_action.version,
                        old_status=old_act_status,
                        new_status=persisted_action.status.value,
                        reason=f"Transmission ambiguous: {sanitized_err}",
                    )
                )

                old_case_status = persisted_case.status.value
                persisted_case.transition_to(
                    CaseStatus.ACTION_REQUIRED,
                    reason="Ambiguous external transmission dispatched. Automated retry blocked; operator intervention required.",
                )
                uow.cases.save(persisted_case)
                uow.events.append(
                    make_case_status_changed_event(
                        case_id=persisted_case.case_id,
                        user_id=user_id,
                        aggregate_version=persisted_case.version,
                        old_status=old_case_status,
                        new_status=persisted_case.status.value,
                        reason="Ambiguous external transmission dispatched. Automated retry blocked; operator intervention required.",
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
                    f"AMBIGUOUS submission: External transmission dispatched past irreversible boundary with unverified outcome ({sanitized_err}). Manual operator resolution required.",
                    details={"failure_kind": "ambiguous_submission", "receipt_id": receipt.receipt_id, "attempt_id": attempt.attempt_id},
                )

            else:  # CONFIRMED_FAILED
                sanitized_err = _sanitize_diagnostic_message(submit_result.error_message or "Transmission rejected by destination.")
                sanitized_snippet = _sanitize_diagnostic_message(submit_result.raw_response_snippet)
                attempt.mark_failed(
                    category=submit_result.error_category or ExecutionErrorCategory.SUBMIT_REJECTED,
                    message=sanitized_err,
                    snippet=sanitized_snippet,
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
                    safe_provider_metadata=sanitized_provider_metadata,
                )
                uow.execution_receipts.save(receipt)

                old_act_status = persisted_action.status.value
                persisted_action.transition_to(
                    ActionStatus.FAILED,
                    reason=f"Transmission rejected: {sanitized_err}",
                )
                persisted_action.result = {
                    "receipt_id": receipt.receipt_id,
                    "receipt_hash": receipt.receipt_hash,
                    "status": receipt.status.value,
                    "error_message": sanitized_err,
                }
                uow.actions.save(persisted_action)
                uow.events.append(
                    make_action_status_changed_event(
                        action_id=persisted_action.action_id,
                        user_id=user_id,
                        aggregate_version=persisted_action.version,
                        old_status=old_act_status,
                        new_status=persisted_action.status.value,
                        reason=f"Transmission rejected: {sanitized_err}",
                    )
                )

                old_case_status = persisted_case.status.value
                persisted_case.transition_to(
                    CaseStatus.ACTION_REQUIRED,
                    reason=f"Transmission rejected by destination: {sanitized_err}",
                )
                uow.cases.save(persisted_case)
                uow.events.append(
                    make_case_status_changed_event(
                        case_id=persisted_case.case_id,
                        user_id=user_id,
                        aggregate_version=persisted_case.version,
                        old_status=old_case_status,
                        new_status=persisted_case.status.value,
                        reason=f"Transmission rejected by destination: {sanitized_err}",
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
                    f"External transmission rejected by destination: {sanitized_err}",
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
