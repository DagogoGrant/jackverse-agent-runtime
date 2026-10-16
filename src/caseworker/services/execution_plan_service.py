"""Application service managing ExternalExecutionPlan compilation, lifecycle, and governance.

Constitutional Principles:
$$\\text{Planning authority} \\neq \\text{Content approval} \\neq \\text{Transmission authority} \\neq \\text{Execution authority}$$
- Phase 5.3.1 produces a sealed, reviewable specification of the intended external action.
- Zero outbound network requests, browser execution, or email sending.
- Case remains in ACTION_REQUIRED; no Action or Approval aggregate is created in Phase 5.3.1.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from caseworker.domain.case import Case
from caseworker.domain.enums import (
    CaseStatus,
    CaseType,
    ExternalExecutionPlanStatus,
    PreparationPackageStatus,
)
from caseworker.domain.errors import (
    DomainValidationError,
    EntityNotFoundError,
    InvalidStateTransitionError,
    PackageVerificationError,
)
from caseworker.domain.events import (
    make_external_execution_plan_created_event,
    make_external_execution_plan_superseded_event,
)
from caseworker.domain.execution_plan import (
    ExecutionDestination,
    ExternalExecutionPlan,
    TargetInteractionSchema,
    TargetSubmitControlSpec,
    compute_execution_plan_staleness,
)
from caseworker.domain.execution_planner import ExecutionPlanCompiler
from caseworker.domain.package_integrity import PreparationPackageIntegrityValidator

if TYPE_CHECKING:
    from caseworker.persistence.base import CaseworkerUnitOfWork
    from caseworker.persistence.sqlite import SQLiteCaseworkerStorage


class ExecutionPlanService:
    """Application service for compiling and managing exact external execution plans."""

    def __init__(self, storage: SQLiteCaseworkerStorage) -> None:
        self.storage = storage

    def find_matching_plan(
        self,
        user_id: str,
        case_id: str,
        package_id: str | None = None,
        destination_override: ExecutionDestination | None = None,
        target_schema: TargetInteractionSchema | None = None,
        channel_metadata: dict[str, Any] | None = None,
        final_submit_control: TargetSubmitControlSpec | None = None,
    ) -> tuple[ExternalExecutionPlan, Case] | None:
        """Find an existing execution plan if exact inputs match an already stored plan."""
        with self.storage.unit_of_work() as uow:
            case = uow.cases.get_by_id(case_id)
            if case is None or case.user_id != user_id:
                return None
            if case.status != CaseStatus.ACTION_REQUIRED or case.case_type != CaseType.JOB_APPLICATION:
                return None
            opportunity = uow.opportunities.get_by_converted_case_id(case.case_id)
            if opportunity is None and case.opportunity_id:
                opportunity = uow.opportunities.get_by_id(case.opportunity_id)
            if opportunity is None:
                return None

            if package_id:
                package = uow.preparation_packages.get_by_id(package_id)
            else:
                package = uow.preparation_packages.get_latest_for_case(case.case_id)
            if package is None or package.user_id != user_id or package.case_id != case.case_id:
                return None
            if package.status != PreparationPackageStatus.APPROVED:
                return None

            integrity_result = PreparationPackageIntegrityValidator.validate(
                uow=uow,
                case=case,
                package=package,
                user_id=user_id,
                expected_package_status=PreparationPackageStatus.APPROVED,
            )
            if not integrity_result.is_valid:
                return None

            facts = uow.context.list_active(user_id=user_id)
            try:
                compiled = ExecutionPlanCompiler.compile_plan(
                    case=case,
                    opportunity=opportunity,
                    package=package,
                    artifacts=integrity_result.artifacts,
                    claims=integrity_result.claims,
                    facts=facts,
                    target_schema=target_schema,
                    destination_override=destination_override,
                    channel_metadata=channel_metadata,
                    final_submit_control=final_submit_control,
                )
            except Exception:
                return None

            existing = uow.execution_plans.get_by_fingerprint(case.case_id, compiled.execution_plan_fingerprint)
            if existing is not None:
                return existing, case
            return None

    def create_execution_plan(
        self,
        user_id: str,
        case_id: str,
        package_id: str | None = None,
        destination_override: ExecutionDestination | None = None,
        target_schema: TargetInteractionSchema | None = None,
        channel_metadata: dict[str, Any] | None = None,
        final_submit_control: TargetSubmitControlSpec | None = None,
    ) -> ExternalExecutionPlan:
        """Compile and persist an ExternalExecutionPlan.

        Invariants:
        1. Case must belong to user, exist, and be in ACTION_REQUIRED status.
        2. CaseType must be JOB_APPLICATION (focused initial vertical).
        3. Associated Opportunity must exist.
        4. Target PreparationPackage must be APPROVED and pass full integrity revalidation.
        5. Idempotent replay: if an identical execution plan fingerprint exists, return it without mutation.
        6. Supersession: if destination/schema/inputs changed, mark prior active plans SUPERSEDED atomically.
        7. Zero transmission: no external calls or Action/Approval records created.
        """
        with self.storage.unit_of_work() as uow:
            # 1. Validate Case
            case = uow.cases.get_by_id(case_id)
            if case is None or case.user_id != user_id:
                raise EntityNotFoundError(f"Case '{case_id}' not found for user '{user_id}'.")

            if case.status != CaseStatus.ACTION_REQUIRED:
                raise InvalidStateTransitionError(
                    f"Cannot compile execution plan for case '{case_id}' in status '{case.status.value}'. "
                    f"Must be ACTION_REQUIRED."
                )

            if case.case_type != CaseType.JOB_APPLICATION:
                raise DomainValidationError(
                    f"Execution planning is currently supported only for JOB_APPLICATION cases "
                    f"(got '{case.case_type.value}')."
                )

            # 2. Validate Opportunity
            opportunity = uow.opportunities.get_by_converted_case_id(case.case_id)
            if opportunity is None and case.opportunity_id:
                opportunity = uow.opportunities.get_by_id(case.opportunity_id)
            if opportunity is None:
                raise EntityNotFoundError(f"Opportunity for case '{case_id}' not found.")

            # 3. Retrieve & Validate Package
            if package_id:
                package = uow.preparation_packages.get_by_id(package_id)
                if package is None:
                    raise EntityNotFoundError(f"PreparationPackage '{package_id}' not found.")
            else:
                package = uow.preparation_packages.get_latest_for_case(case.case_id)
                if package is None:
                    raise EntityNotFoundError(f"No preparation package found for case '{case_id}'.")

            if package.user_id != user_id or package.case_id != case.case_id:
                raise EntityNotFoundError(f"PreparationPackage '{package.package_id}' not found for case '{case_id}'.")

            if package.status != PreparationPackageStatus.APPROVED:
                raise DomainValidationError(
                    f"PreparationPackage '{package.package_id}' status is '{package.status.value}', expected APPROVED."
                )

            # 4. Shared Package Integrity Revalidation
            integrity_result = PreparationPackageIntegrityValidator.validate(
                uow=uow,
                case=case,
                package=package,
                user_id=user_id,
                expected_package_status=PreparationPackageStatus.APPROVED,
            )
            if not integrity_result.is_valid:
                raise PackageVerificationError(
                    f"Preparation package integrity revalidation failed: {integrity_result.reason}"
                )

            # 5. Fetch verified active ContextFacts
            facts = uow.context.list_active(user_id=user_id)

            # 6. Compile Plan
            compiled_plan = ExecutionPlanCompiler.compile_plan(
                case=case,
                opportunity=opportunity,
                package=package,
                artifacts=integrity_result.artifacts,
                claims=integrity_result.claims,
                facts=facts,
                target_schema=target_schema,
                destination_override=destination_override,
                channel_metadata=channel_metadata,
                final_submit_control=final_submit_control,
            )

            # 7. Check Idempotency by Fingerprint
            existing = uow.execution_plans.get_by_fingerprint(case.case_id, compiled_plan.execution_plan_fingerprint)
            if existing is not None:
                # Idempotent replay: identical inputs produce identical fingerprint
                return existing

            # 8. Supersession of Prior Active Plans
            prior_active = uow.execution_plans.list_active_for_case(case.case_id)
            for prior in prior_active:
                prior.mark_superseded(reason=f"Superseded by newer execution plan '{compiled_plan.plan_id}'")
                uow.execution_plans.save(prior)

                superseded_event = make_external_execution_plan_superseded_event(
                    user_id=user_id,
                    case_id=case.case_id,
                    plan_id=prior.plan_id,
                    superseded_by_plan_id=compiled_plan.plan_id,
                    package_id=prior.package_id,
                    reason=f"Superseded by newer execution plan '{compiled_plan.plan_id}'",
                )
                uow.events.append(superseded_event)

            # 9. Save New Plan & Emit Privacy-Safe Created Event
            uow.execution_plans.save(compiled_plan)

            created_event = make_external_execution_plan_created_event(
                user_id=user_id,
                case_id=case.case_id,
                plan_id=compiled_plan.plan_id,
                package_id=compiled_plan.package_id,
                package_fingerprint=compiled_plan.package_fingerprint,
                destination_type=compiled_plan.destination.destination_type,
                canonical_url=compiled_plan.destination.canonical_url,
                plan_status=compiled_plan.status.value,
                plan_fingerprint=compiled_plan.execution_plan_fingerprint,
                field_mapping_count=len(compiled_plan.field_mappings),
                upload_asset_count=len(compiled_plan.upload_mappings),
                screening_response_count=len(compiled_plan.screening_responses),
                unresolved_findings_count=len([f for f in compiled_plan.findings if not f.passed]),
            )
            uow.events.append(created_event)

            uow.commit()
            return compiled_plan

    def _attach_staleness(self, uow: CaseworkerUnitOfWork, plan: ExternalExecutionPlan) -> ExternalExecutionPlan:
        """Compute live staleness against current dependencies side-effect free (Clarification 3)."""
        package = uow.preparation_packages.get_by_id(plan.package_id)
        current_facts = uow.context.list_active(user_id=plan.user_id)
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
        plan.is_stale = is_stale
        plan.staleness_reasons = staleness_reasons
        return plan

    def get_latest_execution_plan(self, user_id: str, case_id: str) -> ExternalExecutionPlan | None:
        """Retrieve the latest execution plan for a case with side-effect free staleness."""
        with self.storage.unit_of_work() as uow:
            case = uow.cases.get_by_id(case_id)
            if case is None or case.user_id != user_id:
                raise EntityNotFoundError("Case", case_id)
            plan = uow.execution_plans.get_latest_for_case(case_id)
            if plan is not None:
                return self._attach_staleness(uow, plan)
            return None

    def get_execution_plan(self, user_id: str, case_id: str, plan_id: str) -> ExternalExecutionPlan | None:
        """Retrieve a specific execution plan by plan_id with side-effect free staleness."""
        with self.storage.unit_of_work() as uow:
            case = uow.cases.get_by_id(case_id)
            if case is None or case.user_id != user_id:
                raise EntityNotFoundError("Case", case_id)
            plan = uow.execution_plans.get_by_id(plan_id)
            if plan is None or plan.case_id != case_id or plan.user_id != user_id:
                return None
            return self._attach_staleness(uow, plan)

    def list_execution_plans(self, user_id: str, case_id: str) -> list[ExternalExecutionPlan]:
        """List all execution plans for a case, newest first, with side-effect free staleness."""
        with self.storage.unit_of_work() as uow:
            case = uow.cases.get_by_id(case_id)
            if case is None or case.user_id != user_id:
                raise EntityNotFoundError("Case", case_id)
            plans = uow.execution_plans.list_for_case(case_id)
            return [self._attach_staleness(uow, p) for p in plans]
