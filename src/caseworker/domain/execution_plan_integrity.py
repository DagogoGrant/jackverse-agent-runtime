"""ExecutionPlan integrity and transmission authorization validation.

Constitutional Principles:
$$\\text{Planning authority} \\neq \\text{Content approval} \\neq \\text{Transmission authority} \\neq \\text{Execution authority}$$

Phase 5.3.2: Revalidates exact immutable execution plan and its cryptographic bindings
before granting transmission authorization. Zero external transmission occurs.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import TYPE_CHECKING

from caseworker.domain.action import Action
from caseworker.domain.enums import (
    ExternalExecutionPlanStatus,
    PreparationPackageStatus,
)
from caseworker.domain.execution_plan import (
    ExternalExecutionPlan,
    compute_execution_plan_staleness,
)
from caseworker.domain.package_integrity import PreparationPackageIntegrityValidator

if TYPE_CHECKING:
    from caseworker.domain.approval import Approval
    from caseworker.domain.case import Case
    from caseworker.persistence.base import CaseworkerUnitOfWork


class ExecutionPlanIntegrityFailureKind(StrEnum):
    """Classification of integrity or authorization binding failures."""

    NONE = "none"
    PLAN_INVALID = "plan_invalid"
    AUTHORIZATION_BINDING_INVALID = "authorization_binding_invalid"
    PLAN_NOT_READY = "plan_not_ready"


@dataclass(frozen=True)
class ExecutionPlanIntegrityValidationResult:
    """Outcome of execution plan integrity and authorization binding validation."""

    is_valid: bool
    failure_kind: ExecutionPlanIntegrityFailureKind
    reason: str
    findings: list[str] = field(default_factory=list)


class ExecutionPlanIntegrityValidator:
    """Canonical validator for ExternalExecutionPlan integrity and transmission authorization binding.

    Composes:
    - PreparationPackageIntegrityValidator (package & artifact freeze verification)
    - compute_execution_plan_staleness (Vault facts, DB upload asset BLOBs, screening deps, schema hashes)
    """

    @classmethod
    def validate(
        cls,
        uow: CaseworkerUnitOfWork,
        case: Case,
        plan: ExternalExecutionPlan,
        user_id: str,
        expected_plan_status: ExternalExecutionPlanStatus | None = ExternalExecutionPlanStatus.READY_FOR_AUTHORIZATION,
        action: Action | None = None,
        approval: Approval | None = None,
    ) -> ExecutionPlanIntegrityValidationResult:
        """Validate all cryptographic and semantic invariants for transmission authorization.

        Distinguishes:
        - PLAN_INVALID: Plan dependencies stale, tampered, or invalid. (Supersedes plan on unwind)
        - AUTHORIZATION_BINDING_INVALID: Action parameters / fingerprint tampered while plan is intact. (Keeps plan)
        - PLAN_NOT_READY: Plan is in DRAFT or unready state. (Rejects request without mutating plan)
        """
        # 1. Ownership & Case Alignment
        if plan.case_id != case.case_id or plan.user_id != user_id:
            return ExecutionPlanIntegrityValidationResult(
                is_valid=False,
                failure_kind=ExecutionPlanIntegrityFailureKind.PLAN_INVALID,
                reason=f"Plan ownership mismatch (plan case '{plan.case_id}' vs case '{case.case_id}', plan user '{plan.user_id}' vs '{user_id}').",
                findings=["PLAN_OWNERSHIP_MISMATCH"],
            )

        # 2. Plan Status Invariants
        if plan.status == ExternalExecutionPlanStatus.DRAFT:
            return ExecutionPlanIntegrityValidationResult(
                is_valid=False,
                failure_kind=ExecutionPlanIntegrityFailureKind.PLAN_NOT_READY,
                reason="Execution plan is in DRAFT status and cannot be authorized.",
                findings=["PLAN_IN_DRAFT_STATUS"],
            )

        if plan.status == ExternalExecutionPlanStatus.SUPERSEDED:
            return ExecutionPlanIntegrityValidationResult(
                is_valid=False,
                failure_kind=ExecutionPlanIntegrityFailureKind.PLAN_INVALID,
                reason="Execution plan has been SUPERSEDED.",
                findings=["PLAN_SUPERSEDED"],
            )

        if expected_plan_status and plan.status != expected_plan_status:
            return ExecutionPlanIntegrityValidationResult(
                is_valid=False,
                failure_kind=ExecutionPlanIntegrityFailureKind.PLAN_INVALID,
                reason=f"Execution plan status is '{plan.status.value}', expected '{expected_plan_status.value}'.",
                findings=["PLAN_STATUS_MISMATCH"],
            )

        # 3. Plan Content Fingerprint Invariant
        computed_plan_fp = plan.compute_fingerprint()
        if computed_plan_fp != plan.execution_plan_fingerprint:
            return ExecutionPlanIntegrityValidationResult(
                is_valid=False,
                failure_kind=ExecutionPlanIntegrityFailureKind.PLAN_INVALID,
                reason=(
                    f"Execution plan content fingerprint mismatch "
                    f"(stored '{plan.execution_plan_fingerprint}', computed '{computed_plan_fp}')."
                ),
                findings=["PLAN_FINGERPRINT_TAMPERED"],
            )

        # 4. Underlying PreparationPackage Integrity Revalidation
        package = uow.preparation_packages.get_by_id(plan.package_id)
        if package is None or package.user_id != user_id or package.case_id != case.case_id:
            return ExecutionPlanIntegrityValidationResult(
                is_valid=False,
                failure_kind=ExecutionPlanIntegrityFailureKind.PLAN_INVALID,
                reason=f"PreparationPackage '{plan.package_id}' not found or ownership mismatch.",
                findings=["PREPARATION_PACKAGE_MISSING"],
            )

        if package.package_fingerprint != plan.package_fingerprint:
            return ExecutionPlanIntegrityValidationResult(
                is_valid=False,
                failure_kind=ExecutionPlanIntegrityFailureKind.PLAN_INVALID,
                reason="PreparationPackage fingerprint changed since execution plan compilation.",
                findings=["PREPARATION_PACKAGE_FINGERPRINT_MISMATCH"],
            )

        pkg_val = PreparationPackageIntegrityValidator.validate(
            uow=uow,
            case=case,
            package=package,
            user_id=user_id,
            expected_package_status=PreparationPackageStatus.APPROVED,
        )
        if not pkg_val.is_valid:
            findings_reasons = [f.get("reason", "") for f in pkg_val.findings] if pkg_val.findings else [pkg_val.reason]
            return ExecutionPlanIntegrityValidationResult(
                is_valid=False,
                failure_kind=ExecutionPlanIntegrityFailureKind.PLAN_INVALID,
                reason=f"PreparationPackage integrity revalidation failed: {pkg_val.reason}",
                findings=findings_reasons,
            )

        # 5. Live Staleness Check (Vault facts, DB upload asset BLOBs, screening deps, schema hashes)
        current_facts = uow.context.list_active(user_id=user_id)
        artifacts = uow.preparation_artifacts.list_by_case(plan.case_id)
        claims = uow.claims.list_by_case(plan.case_id)
        upload_assets = uow.upload_assets.list_by_plan(plan.plan_id)

        is_stale, staleness_reasons = compute_execution_plan_staleness(
            plan=plan,
            package=package,
            current_facts=current_facts,
            artifacts=artifacts,
            claims=claims,
            upload_assets=upload_assets,
        )
        if is_stale:
            return ExecutionPlanIntegrityValidationResult(
                is_valid=False,
                failure_kind=ExecutionPlanIntegrityFailureKind.PLAN_INVALID,
                reason=f"Execution plan dependencies are stale: {'; '.join(staleness_reasons)}",
                findings=staleness_reasons,
            )

        # 6. Action-to-Plan Cryptographic Binding Revalidation
        if action is not None:
            act_params = action.parameters or {}

            # Scope & schema
            if act_params.get("scope") != "external_transmission":
                return ExecutionPlanIntegrityValidationResult(
                    is_valid=False,
                    failure_kind=ExecutionPlanIntegrityFailureKind.AUTHORIZATION_BINDING_INVALID,
                    reason=f"Action scope '{act_params.get('scope')}' is not 'external_transmission'.",
                    findings=["ACTION_SCOPE_MISMATCH"],
                )

            if act_params.get("authorization_schema") != "tx_auth_v1":
                return ExecutionPlanIntegrityValidationResult(
                    is_valid=False,
                    failure_kind=ExecutionPlanIntegrityFailureKind.AUTHORIZATION_BINDING_INVALID,
                    reason=f"Action authorization schema '{act_params.get('authorization_schema')}' is not 'tx_auth_v1'.",
                    findings=["AUTHORIZATION_SCHEMA_MISMATCH"],
                )

            # Cryptographic references
            if act_params.get("plan_id") != plan.plan_id:
                return ExecutionPlanIntegrityValidationResult(
                    is_valid=False,
                    failure_kind=ExecutionPlanIntegrityFailureKind.AUTHORIZATION_BINDING_INVALID,
                    reason=f"Action plan_id '{act_params.get('plan_id')}' does not match plan '{plan.plan_id}'.",
                    findings=["PLAN_ID_MISMATCH"],
                )

            if act_params.get("execution_plan_fingerprint") != plan.execution_plan_fingerprint:
                return ExecutionPlanIntegrityValidationResult(
                    is_valid=False,
                    failure_kind=ExecutionPlanIntegrityFailureKind.AUTHORIZATION_BINDING_INVALID,
                    reason="Action execution_plan_fingerprint does not match plan fingerprint.",
                    findings=["PLAN_FINGERPRINT_MISMATCH"],
                )

            if act_params.get("package_id") != plan.package_id:
                return ExecutionPlanIntegrityValidationResult(
                    is_valid=False,
                    failure_kind=ExecutionPlanIntegrityFailureKind.AUTHORIZATION_BINDING_INVALID,
                    reason=f"Action package_id '{act_params.get('package_id')}' does not match plan '{plan.package_id}'.",
                    findings=["PACKAGE_ID_MISMATCH"],
                )

            if act_params.get("package_fingerprint") != plan.package_fingerprint:
                return ExecutionPlanIntegrityValidationResult(
                    is_valid=False,
                    failure_kind=ExecutionPlanIntegrityFailureKind.AUTHORIZATION_BINDING_INVALID,
                    reason="Action package_fingerprint does not match plan package_fingerprint.",
                    findings=["PACKAGE_FINGERPRINT_MISMATCH"],
                )

            if act_params.get("target_schema_hash") != plan.target_schema_hash:
                return ExecutionPlanIntegrityValidationResult(
                    is_valid=False,
                    failure_kind=ExecutionPlanIntegrityFailureKind.AUTHORIZATION_BINDING_INVALID,
                    reason="Action target_schema_hash does not match plan target_schema_hash.",
                    findings=["TARGET_SCHEMA_HASH_MISMATCH"],
                )

            if act_params.get("execution_input_hash") != plan.execution_input_hash:
                return ExecutionPlanIntegrityValidationResult(
                    is_valid=False,
                    failure_kind=ExecutionPlanIntegrityFailureKind.AUTHORIZATION_BINDING_INVALID,
                    reason="Action execution_input_hash does not match plan execution_input_hash.",
                    findings=["EXECUTION_INPUT_HASH_MISMATCH"],
                )

            # Action Fingerprint Integrity: catch ANY Action fingerprint tampering
            if approval is not None:
                if approval.action_id != action.action_id or approval.case_id != action.case_id:
                    return ExecutionPlanIntegrityValidationResult(
                        is_valid=False,
                        failure_kind=ExecutionPlanIntegrityFailureKind.AUTHORIZATION_BINDING_INVALID,
                        reason=(
                            f"Approval action binding mismatch (approval action '{approval.action_id}' vs "
                            f"action '{action.action_id}', approval case '{approval.case_id}' vs action case '{action.case_id}')."
                        ),
                        findings=["ACTION_ID_MISMATCH"],
                    )
                computed_action_fp = action.compute_fingerprint()
                if approval.action_fingerprint != computed_action_fp:
                    return ExecutionPlanIntegrityValidationResult(
                        is_valid=False,
                        failure_kind=ExecutionPlanIntegrityFailureKind.AUTHORIZATION_BINDING_INVALID,
                        reason=(
                            f"Action fingerprint mismatch. Action parameters or description have changed since "
                            f"authorization was requested (expected {approval.action_fingerprint[:12]}..., got {computed_action_fp[:12]}...)."
                        ),
                        findings=["ACTION_FINGERPRINT_MISMATCH"],
                    )

        return ExecutionPlanIntegrityValidationResult(
            is_valid=True,
            failure_kind=ExecutionPlanIntegrityFailureKind.NONE,
            reason="Execution plan and transmission authorization bindings verified successfully.",
            findings=[],
        )
