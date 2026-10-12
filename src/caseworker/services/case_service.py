"""Application service orchestrating Case aggregate operations, transitions, and resolutions."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from caseworker.domain.context_package import ContextPackageBuilder
from caseworker.domain.enums import CaseStatus, CaseType
from caseworker.domain.errors import DomainValidationError, EntityNotFoundError, OptimisticLockError
from caseworker.domain.events import (
    make_case_created_event,
    make_case_resolved_event,
    make_case_status_changed_event,
    make_context_access_denied_event,
    make_context_access_granted_event,
    make_preparation_report_created_event,
)
from caseworker.domain.case import Case
from caseworker.domain.normalized_requirement import NormalizedRequirement, parse_requirements
from caseworker.domain.preparation import (
    PreparationIntelligenceReport,
    SafeDeniedFactDescriptor,
    compute_prep_context_snapshot_hash,
    compute_prep_input_hash,
    is_fact_or_descriptor_relevant_to_requirements,
)
from caseworker.domain.preparation_analyzer import PreparationAnalyzer
from caseworker.domain.vault_policy import ContextAccessPolicy

if TYPE_CHECKING:
    from caseworker.persistence.sqlite import SQLiteCaseworkerStorage


class CaseService:
    """Application service for managing Case aggregates with atomic state + event persistence."""

    def __init__(self, storage: SQLiteCaseworkerStorage) -> None:
        self.storage = storage

    def create_case(
        self,
        user_id: str,
        title: str,
        goal: str,
        case_type: CaseType | str = CaseType.GENERAL,
        mission_id: str | None = None,
        success_criteria: list[str] | None = None,
        constraints: list[str] | None = None,
        deadline: datetime | None = None,
    ) -> Case:
        """Create a new case, verifying optional parent mission existence and saving atomically."""
        with self.storage.unit_of_work() as uow:
            if mission_id is not None:
                mission = uow.missions.get_by_id(mission_id)
                if mission is None:
                    raise EntityNotFoundError("Mission", mission_id)
                if mission.user_id != user_id:
                    raise DomainValidationError(
                        f"Mission '{mission_id}' belongs to user '{mission.user_id}', not '{user_id}'."
                    )


            case = Case(
                user_id=user_id,
                title=title,
                goal=goal,
                case_type=case_type,
                mission_id=mission_id,
                success_criteria=list(success_criteria or []),
                constraints=list(constraints or []),
                deadline=deadline,
            )
            uow.cases.save(case)

            event = make_case_created_event(
                case_id=case.case_id,
                user_id=case.user_id,
                aggregate_version=case.version,
                payload=case.to_dict(),
            )
            uow.events.append(event)

        return case

    def get_case(self, case_id: str) -> Case | None:
        """Retrieve a case by its identifier."""
        with self.storage.unit_of_work() as uow:
            return uow.cases.get_by_id(case_id)

    def get_case_for_user(self, user_id: str, case_id: str) -> Case | None:
        """Retrieve a case by identifier, returning None if non-existent or owned by another user."""
        with self.storage.unit_of_work() as uow:
            case = uow.cases.get_by_id(case_id)
            if case is None or case.user_id != user_id:
                return None
            return case

    def list_user_cases(self, user_id: str, status: CaseStatus | None = None) -> list[Case]:
        """List all cases belonging to a user, optionally filtered by status."""
        with self.storage.unit_of_work() as uow:
            return uow.cases.list_by_user(user_id, status=status)

    def list_mission_cases(self, mission_id: str) -> list[Case]:
        """List all cases linked to a specific mission."""
        with self.storage.unit_of_work() as uow:
            return uow.cases.list_by_mission(mission_id)

    def list_mission_cases_for_user(self, user_id: str, mission_id: str) -> list[Case]:
        """List cases for a mission owned by user, returning empty list if mission not owned."""
        with self.storage.unit_of_work() as uow:
            mission = uow.missions.get_by_id(mission_id)
            if mission is None or mission.user_id != user_id:
                return []
            return [c for c in uow.cases.list_by_mission(mission_id) if c.user_id == user_id]

    def transition_case(
        self,
        case_id: str,
        new_status: CaseStatus | str,
        reason: str | None = None,
    ) -> Case:
        """Transition case state and record state change and event atomically."""
        with self.storage.unit_of_work() as uow:
            case = uow.cases.get_by_id(case_id)
            if case is None:
                raise EntityNotFoundError("Case", case_id)

            old_status = case.status.value
            case.transition_to(new_status, reason=reason)
            uow.cases.save(case)

            event = make_case_status_changed_event(
                case_id=case.case_id,
                user_id=case.user_id,
                aggregate_version=case.version,
                old_status=old_status,
                new_status=case.status.value,
                reason=reason,
            )
            uow.events.append(event)

        return case

    def transition_case_for_user(
        self,
        user_id: str,
        case_id: str,
        new_status: CaseStatus | str,
        reason: str | None = None,
    ) -> Case:
        """Transition case state, raising EntityNotFoundError if non-existent or owned by another user."""
        with self.storage.unit_of_work() as uow:
            case = uow.cases.get_by_id(case_id)
            if case is None or case.user_id != user_id:
                raise EntityNotFoundError("Case", case_id)

            old_status = case.status.value
            case.transition_to(new_status, reason=reason)
            uow.cases.save(case)

            event = make_case_status_changed_event(
                case_id=case.case_id,
                user_id=case.user_id,
                aggregate_version=case.version,
                old_status=old_status,
                new_status=case.status.value,
                reason=reason,
            )
            uow.events.append(event)

        return case

    def resolve_case(self, case_id: str, outcome: str) -> Case:
        """Resolve a case with recorded outcome and emit CaseResolved event atomically."""
        with self.storage.unit_of_work() as uow:
            case = uow.cases.get_by_id(case_id)
            if case is None:
                raise EntityNotFoundError("Case", case_id)

            case.resolve(outcome)
            uow.cases.save(case)

            event = make_case_resolved_event(
                case_id=case.case_id,
                user_id=case.user_id,
                aggregate_version=case.version,
                outcome=outcome,
            )
            uow.events.append(event)

        return case

    def resolve_case_for_user(self, user_id: str, case_id: str, outcome: str) -> Case:
        """Resolve a case, raising EntityNotFoundError if non-existent or owned by another user."""
        with self.storage.unit_of_work() as uow:
            case = uow.cases.get_by_id(case_id)
            if case is None or case.user_id != user_id:
                raise EntityNotFoundError("Case", case_id)

            case.resolve(outcome)
            uow.cases.save(case)

            event = make_case_resolved_event(
                case_id=case.case_id,
                user_id=case.user_id,
                aggregate_version=case.version,
                outcome=outcome,
            )
            uow.events.append(event)

        return case

    def generate_preparation_intelligence(
        self,
        user_id: str,
        case_id: str,
        expected_case_version: int | None = None,
    ) -> tuple[PreparationIntelligenceReport, Case]:
        """Analyze preparation requirements, gate Context Vault access, advance Case to PLANNING, and store report."""
        with self.storage.unit_of_work() as uow:
            case = uow.cases.get_by_id(case_id)
            if case is None or case.user_id != user_id:
                raise EntityNotFoundError("Case", case_id)

            if expected_case_version is not None and case.version != expected_case_version:
                raise OptimisticLockError("Case", case.case_id, expected_case_version)

            opportunity = uow.opportunities.get_by_converted_case_id(case_id)
            if opportunity is None:
                raise DomainValidationError(
                    f"Case '{case_id}' has no associated source Opportunity."
                )

            # Retrieve mission goal and constraints if parent mission exists
            mission_goal = ""
            mission_constraints: list[str] = []
            if case.mission_id:
                mission = uow.missions.get_by_id(case.mission_id)
                if mission:
                    mission_goal = mission.goal
                    mission_constraints = list(mission.constraints)

            # Query context vault strictly with purpose='case_preparation'
            all_facts = uow.context.list_active(user_id=user_id)
            builder = ContextPackageBuilder(ContextAccessPolicy())
            package, granted_decisions, denied_decisions = builder.build_with_report(
                user_id=user_id,
                purpose="case_preparation",
                available_facts=all_facts,
            )
            granted_facts = package.facts

            # Build safe denied descriptors without exposing sensitive raw fact values
            facts_by_id = {f.fact_id: f for f in all_facts}
            safe_denied_descriptors: list[SafeDeniedFactDescriptor] = []
            for d in denied_decisions:
                f = facts_by_id.get(d.fact_id)
                safe_denied_descriptors.append(
                    SafeDeniedFactDescriptor(
                        fact_id=d.fact_id,
                        fact_version=f.version if f else None,
                        namespace=d.namespace or (f.namespace if f else ""),
                        key=d.key or (f.key if f else ""),
                        verification_status=f.verification_status if f else None,
                        reason_code=d.reason_code,
                        reason=d.reason,
                    )
                )

            # Emit audited context access events
            vault_ver = uow.events.get_next_aggregate_version("context_vault", user_id)
            if granted_facts:
                grant_event = make_context_access_granted_event(
                    user_id=user_id,
                    aggregate_version=vault_ver,
                    purpose="case_preparation",
                    fact_ids=[f.fact_id for f in granted_facts],
                    namespaces=list({f.namespace for f in granted_facts}),
                )
                uow.events.append(grant_event)
                vault_ver += 1

            for d in denied_decisions:
                deny_event = make_context_access_denied_event(
                    user_id=user_id,
                    aggregate_version=vault_ver,
                    purpose="case_preparation",
                    fact_id=d.fact_id,
                    reason=d.reason,
                    reason_code=d.reason_code,
                    namespace=d.namespace,
                    key=d.key,
                )
                uow.events.append(deny_event)
                vault_ver += 1

            # Case state machine progression: NEW -> INTAKE -> INVESTIGATING
            if case.status == CaseStatus.NEW:
                old_st = case.status.value
                case.transition_to(CaseStatus.INTAKE, "Starting case intake")
                uow.cases.save(case)
                uow.events.append(
                    make_case_status_changed_event(
                        case.case_id, user_id, case.version, old_st, case.status.value, "Starting case intake"
                    )
                )
                old_st = case.status.value
                case.transition_to(CaseStatus.INVESTIGATING, "Analyzing preparation requirements")
                uow.cases.save(case)
                uow.events.append(
                    make_case_status_changed_event(
                        case.case_id, user_id, case.version, old_st, case.status.value, "Analyzing preparation requirements"
                    )
                )
            elif case.status == CaseStatus.INTAKE:
                old_st = case.status.value
                case.transition_to(CaseStatus.INVESTIGATING, "Analyzing preparation requirements")
                uow.cases.save(case)
                uow.events.append(
                    make_case_status_changed_event(
                        case.case_id, user_id, case.version, old_st, case.status.value, "Analyzing preparation requirements"
                    )
                )
            elif case.status == CaseStatus.PLANNING:
                old_st = case.status.value
                case.transition_to(CaseStatus.INVESTIGATING, "Re-analyzing preparation requirements")
                uow.cases.save(case)
                uow.events.append(
                    make_case_status_changed_event(
                        case.case_id, user_id, case.version, old_st, case.status.value, "Re-analyzing preparation requirements"
                    )
                )

            # Check existing version
            latest_existing = uow.preparation_reports.get_latest_for_case(case_id)
            previous_version = latest_existing.version if latest_existing else 0

            # Execute preparation analysis
            report = PreparationAnalyzer.analyze(
                case=case,
                opportunity=opportunity,
                granted_facts=granted_facts,
                denied_descriptors=safe_denied_descriptors,
                mission_goal=mission_goal,
                mission_constraints=mission_constraints,
                previous_version=previous_version,
            )
            uow.preparation_reports.save(report)

            # Advance Case to PLANNING (report finalized)
            if case.status == CaseStatus.INVESTIGATING:
                old_st = case.status.value
                case.transition_to(CaseStatus.PLANNING, "Preparation intelligence report finalized")
                uow.cases.save(case)
                uow.events.append(
                    make_case_status_changed_event(
                        case.case_id, user_id, case.version, old_st, case.status.value, "Preparation intelligence report finalized"
                    )
                )
            else:
                uow.cases.save(case)

            # Emit preparation report created event (CRITICAL INVARIANT: own aggregate stream)
            report_event = make_preparation_report_created_event(
                report_id=report.report_id,
                case_id=case.case_id,
                opportunity_id=opportunity.opportunity_id,
                user_id=user_id,
                aggregate_version=1,
                input_hash=report.input_hash,
                context_snapshot_hash=report.context_snapshot_hash,
                schema_version=report.schema_version,
            )
            uow.events.append(report_event)

        return report, case

    def get_latest_preparation_report(
        self,
        user_id: str,
        case_id: str,
    ) -> tuple[PreparationIntelligenceReport | None, bool]:
        """Retrieve latest preparation report for case and check if it is stale."""
        with self.storage.unit_of_work() as uow:
            case = uow.cases.get_by_id(case_id)
            if case is None or case.user_id != user_id:
                raise EntityNotFoundError("Case", case_id)

            report = uow.preparation_reports.get_latest_for_case(case_id)
            if report is None:
                return None, False

            opportunity = uow.opportunities.get_by_converted_case_id(case_id)
            if opportunity is None:
                return report, True

            mission_goal = ""
            mission_constraints: list[str] = []
            if case.mission_id:
                mission = uow.missions.get_by_id(case.mission_id)
                if mission:
                    mission_goal = mission.goal
                    mission_constraints = list(mission.constraints)

            all_facts = uow.context.list_active(user_id=user_id)
            builder = ContextPackageBuilder(ContextAccessPolicy())
            package, granted_decisions, denied_decisions = builder.build_with_report(
                user_id=user_id,
                purpose="case_preparation",
                available_facts=all_facts,
            )
            granted_facts = package.facts

            facts_by_id = {f.fact_id: f for f in all_facts}
            safe_denied_descriptors: list[SafeDeniedFactDescriptor] = []
            for d in denied_decisions:
                f = facts_by_id.get(d.fact_id)
                safe_denied_descriptors.append(
                    SafeDeniedFactDescriptor(
                        fact_id=d.fact_id,
                        fact_version=f.version if f else None,
                        namespace=d.namespace or (f.namespace if f else ""),
                        key=d.key or (f.key if f else ""),
                        verification_status=f.verification_status if f else None,
                        reason_code=d.reason_code,
                        reason=d.reason,
                    )
                )

            # Obtain normalized requirements for relevance filtering
            raw_normalized = opportunity.metadata.get("normalized_requirements")
            if raw_normalized:
                normalized_reqs = [NormalizedRequirement.from_dict(d) for d in raw_normalized]
            else:
                normalized_reqs = parse_requirements(opportunity.opportunity_id, opportunity.requirements)

            current_input_hash = compute_prep_input_hash(case, opportunity, mission_goal, mission_constraints)

            relevant_granted = [
                f for f in granted_facts
                if is_fact_or_descriptor_relevant_to_requirements(f.namespace, f.key, normalized_reqs)
            ]
            relevant_denied = [
                d for d in safe_denied_descriptors
                if is_fact_or_descriptor_relevant_to_requirements(d.namespace, d.key, normalized_reqs)
            ]
            current_context_hash = compute_prep_context_snapshot_hash(relevant_granted, relevant_denied)

            eval_meta = opportunity.metadata.get("evaluation") or {}
            current_source_eval_id = eval_meta.get("evaluation_id") or opportunity.metadata.get("latest_evaluation_id")

            is_stale = report.is_stale(
                current_input_hash=current_input_hash,
                current_context_hash=current_context_hash,
                current_opportunity_evaluation_id=current_source_eval_id,
                current_analyzer_version=PreparationAnalyzer.ANALYZER_VERSION,
                current_schema_version=PreparationAnalyzer.SCHEMA_VERSION,
            )
            return report, is_stale

