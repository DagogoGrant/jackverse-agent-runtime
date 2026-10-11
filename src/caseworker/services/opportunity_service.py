"""Application service managing Opportunity discovery, deduplication, and lifecycle transitions."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any

from caseworker.api.schemas.opportunity import RESERVED_METADATA_KEYS
from caseworker.domain.case import Case
from caseworker.domain.context_package import ContextPackageBuilder
from caseworker.domain.enums import (
    CaseType,
    EligibilityStatus,
    MissionStatus,
    OpportunityStatus,
    OpportunityType,
)
from caseworker.domain.errors import (
    DomainValidationError,
    EntityNotFoundError,
    InvalidStateTransitionError,
)
from caseworker.domain.events import (
    make_case_created_event,
    make_context_access_denied_event,
    make_context_access_granted_event,
    make_opportunity_converted_to_case_event,
    make_opportunity_discovered_event,
    make_opportunity_evaluated_event,
    make_opportunity_normalized_event,
    make_opportunity_shortlisted_event,
    make_opportunity_status_changed_event,
)
from caseworker.domain.normalized_requirement import NormalizedRequirement, parse_requirements
from caseworker.domain.opportunity import Opportunity
from caseworker.domain.opportunity_evaluator import (
    OpportunityEvaluationSnapshot,
    OpportunityEvaluator,
)
from caseworker.domain.opportunity_provider import (
    ManualOpportunityProvider,
    OpportunityProvider,
    RawOpportunity,
)
from caseworker.domain.vault_policy import ContextAccessPolicy

if TYPE_CHECKING:
    from caseworker.persistence.sqlite import SQLiteCaseworkerStorage


OPPORTUNITY_TO_CASE_TYPE_MAP: dict[OpportunityType, CaseType] = {
    OpportunityType.JOB: CaseType.JOB_APPLICATION,
    OpportunityType.SCHOLARSHIP: CaseType.SCHOLARSHIP_APPLICATION,
    OpportunityType.GRANT: CaseType.GRANT_PURSUIT,
    OpportunityType.FELLOWSHIP: CaseType.SCHOLARSHIP_APPLICATION,
    OpportunityType.RESEARCH: CaseType.GENERAL,
    OpportunityType.HOUSING: CaseType.HOUSING_SEARCH,
    OpportunityType.FREELANCE: CaseType.JOB_APPLICATION,
    OpportunityType.HACKATHON: CaseType.GENERAL,
    OpportunityType.CONFERENCE: CaseType.GENERAL,
    OpportunityType.COMPETITION: CaseType.GENERAL,
    OpportunityType.OTHER: CaseType.GENERAL,
}


class OpportunityService:
    """Application service managing external opportunities with user-scoped deduplication and atomic persistence."""

    def __init__(self, storage: SQLiteCaseworkerStorage) -> None:
        self.storage = storage

    def ingest_opportunity(
        self,
        user_id: str,
        provider: OpportunityProvider,
        raw_data: dict[str, Any],
        mission_id: str | None = None,
    ) -> tuple[Opportunity, bool]:
        """Ingest raw data via an OpportunityProvider into a persisted Opportunity in DISCOVERED state."""
        raw_opp = provider.ingest(raw_data)
        return self.create_opportunity(
            user_id=user_id,
            title=raw_opp.title,
            opportunity_type=raw_opp.opportunity_type,
            organization=raw_opp.organization,
            source_url=raw_opp.source_url,
            source_name=raw_opp.source_name,
            location=raw_opp.location,
            requirements=raw_opp.requirements,
            metadata=raw_opp.metadata,
            deadline=raw_opp.deadline,
            mission_id=mission_id,
            external_posting_id=raw_opp.external_posting_id,
        )

    def create_opportunity(
        self,
        user_id: str,
        title: str,
        opportunity_type: OpportunityType | str,
        organization: str = "",
        source_url: str = "",
        source_name: str = "",
        location: str = "",
        requirements: list[str] | None = None,
        metadata: dict[str, Any] | None = None,
        deadline: datetime | None = None,
        mission_id: str | None = None,
        external_posting_id: str | None = None,
        status: OpportunityStatus | str = OpportunityStatus.DISCOVERED,
    ) -> tuple[Opportunity, bool]:
        """Create an opportunity or return an existing deduplicated record scoped to this user."""
        meta = dict(metadata or {})
        for k in meta:
            if k in RESERVED_METADATA_KEYS:
                raise DomainValidationError(f"Reserved metadata key '{k}' cannot be provided directly.")

        with self.storage.unit_of_work() as uow:
            if mission_id is not None:
                mission = uow.missions.get_by_id(mission_id)
                if mission is None or mission.user_id != user_id:
                    raise EntityNotFoundError("Mission", mission_id)

            opp = Opportunity(
                user_id=user_id,
                title=title,
                opportunity_type=opportunity_type,
                organization=organization,
                source_url=source_url,
                source_name=source_name,
                location=location,
                status=OpportunityStatus.DISCOVERED,
                requirements=list(requirements or []),
                metadata=meta,
                deadline=deadline,
                mission_id=mission_id,
                external_posting_id=external_posting_id,
            )

            # Check cross-source V2 deduplication fingerprint first
            matches_v2 = uow.opportunities.list_by_fingerprint_v2(user_id, opp.fingerprint_v2)
            if len(matches_v2) == 1:
                return matches_v2[0], False
            elif len(matches_v2) > 1:
                # Ambiguous collision: do NOT merge; create new record
                pass
            else:
                # Check legacy V1 fingerprint
                existing = uow.opportunities.get_by_fingerprint(user_id, opp.fingerprint)
                if existing is not None:
                    return existing, False

            uow.opportunities.save(opp)
            event = make_opportunity_discovered_event(
                opportunity_id=opp.opportunity_id,
                user_id=opp.user_id,
                aggregate_version=opp.version,
                payload=opp.to_dict(),
            )
            uow.events.append(event)
            return opp, True

    def get_opportunity(self, opportunity_id: str) -> Opportunity | None:
        """Retrieve an opportunity by ID."""
        with self.storage.unit_of_work() as uow:
            return uow.opportunities.get_by_id(opportunity_id)

    def get_opportunity_for_user(self, user_id: str, opportunity_id: str) -> Opportunity | None:
        """Retrieve an opportunity by ID, asserting user ownership."""
        with self.storage.unit_of_work() as uow:
            opp = uow.opportunities.get_by_id(opportunity_id)
            if opp is None or opp.user_id != user_id:
                return None
            return opp

    def list_user_opportunities(
        self,
        user_id: str,
        status: OpportunityStatus | None = None,
    ) -> list[Opportunity]:
        """List opportunities belonging to a user, optionally filtered by status."""
        with self.storage.unit_of_work() as uow:
            return uow.opportunities.list_by_user(user_id, status=status)

    def list_mission_opportunities_for_user(
        self,
        user_id: str,
        mission_id: str,
    ) -> list[Opportunity]:
        """List opportunities for a mission, asserting user ownership of the mission."""
        with self.storage.unit_of_work() as uow:
            mission = uow.missions.get_by_id(mission_id)
            if mission is None or mission.user_id != user_id:
                return []
            return [o for o in uow.opportunities.list_by_mission(mission_id) if o.user_id == user_id]

    def normalize_opportunity_for_user(
        self,
        user_id: str,
        opportunity_id: str,
    ) -> tuple[Opportunity, list[NormalizedRequirement]]:
        """Normalize raw requirements into opp_req_v1 criteria and advance state to NORMALIZED."""
        with self.storage.unit_of_work() as uow:
            opp = uow.opportunities.get_by_id(opportunity_id)
            if opp is None or opp.user_id != user_id:
                raise EntityNotFoundError("Opportunity", opportunity_id)

            if opp.status in (
                OpportunityStatus.SHORTLISTED,
                OpportunityStatus.CONVERTED_TO_CASE,
                OpportunityStatus.ARCHIVED,
                OpportunityStatus.REJECTED,
            ):
                raise InvalidStateTransitionError(
                    "Opportunity",
                    opp.status.value,
                    OpportunityStatus.NORMALIZED.value,
                    f"Cannot normalize opportunity in status '{opp.status.value}'.",
                )

            normalized = parse_requirements(opp.opportunity_id, opp.requirements)
            opp.metadata["normalized_requirements"] = [r.to_dict() for r in normalized]
            opp.metadata["requirement_schema"] = "opp_req_v1"

            if opp.status == OpportunityStatus.DISCOVERED:
                opp.transition_to(OpportunityStatus.NORMALIZED)
            elif opp.status == OpportunityStatus.EVALUATING:
                opp.invalidate_evaluation_for_renormalization()
            else:
                # NORMALIZED re-normalization
                opp.version += 1

            uow.opportunities.save(opp)

            event = make_opportunity_normalized_event(
                opportunity_id=opp.opportunity_id,
                user_id=user_id,
                aggregate_version=opp.version,
                requirements_count=len(normalized),
            )
            uow.events.append(event)
            return opp, normalized

    def evaluate_opportunity_for_user(
        self,
        user_id: str,
        opportunity_id: str,
    ) -> tuple[Opportunity, OpportunityEvaluationSnapshot]:
        """Evaluate eligibility and explainable fit with audited purpose-gated context access."""
        with self.storage.unit_of_work() as uow:
            opp = uow.opportunities.get_by_id(opportunity_id)
            if opp is None or opp.user_id != user_id:
                raise EntityNotFoundError("Opportunity", opportunity_id)

            if opp.status not in (OpportunityStatus.NORMALIZED, OpportunityStatus.EVALUATING):
                raise InvalidStateTransitionError(
                    "Opportunity",
                    opp.status.value,
                    OpportunityStatus.EVALUATING.value,
                    f"Can only evaluate opportunities in NORMALIZED or EVALUATING state, got '{opp.status.value}'.",
                )

            # Ensure requirements are normalized
            raw_normalized = opp.metadata.get("normalized_requirements")
            if not raw_normalized:
                normalized = parse_requirements(opp.opportunity_id, opp.requirements)
                opp.metadata["normalized_requirements"] = [r.to_dict() for r in normalized]
                opp.metadata["requirement_schema"] = "opp_req_v1"
            else:
                normalized = [NormalizedRequirement.from_dict(d) for d in raw_normalized]

            mission_goal = ""
            mission_constraints: list[str] = []
            if opp.mission_id:
                mission = uow.missions.get_by_id(opp.mission_id)
                if mission:
                    mission_goal = mission.goal
                    mission_constraints = list(mission.constraints)

            # Purpose-gated context access via uow.context
            all_facts = uow.context.list_active(user_id=user_id)
            builder = ContextPackageBuilder(ContextAccessPolicy())
            pkg, granted_decisions, denied_decisions = builder.build_with_report(
                user_id=user_id,
                purpose="opportunity_evaluation",
                available_facts=all_facts,
            )

            # Audit events for context vault access
            current_version = uow.events.get_next_aggregate_version("context_vault", user_id)
            if pkg.facts:
                granted_event = make_context_access_granted_event(
                    user_id=user_id,
                    aggregate_version=current_version,
                    purpose="opportunity_evaluation",
                    fact_ids=[f.fact_id for f in pkg.facts],
                    namespaces=list({f.namespace for f in pkg.facts}),
                )
                uow.events.append(granted_event)
                current_version += 1

            for decision in denied_decisions:
                denied_event = make_context_access_denied_event(
                    user_id=user_id,
                    aggregate_version=current_version,
                    purpose="opportunity_evaluation",
                    fact_id=decision.fact_id,
                    reason=decision.reason,
                    reason_code=decision.reason_code,
                    namespace=decision.namespace,
                    key=decision.key,
                )
                uow.events.append(denied_event)
                current_version += 1

            # Perform evaluation using granted context facts only
            snapshot = OpportunityEvaluator.evaluate(
                opportunity=opp,
                normalized_requirements=normalized,
                context_facts=pkg.facts,
                mission_goal=mission_goal,
                mission_constraints=mission_constraints,
            )

            # Record snapshot and advance status / version
            opp.record_evaluation(snapshot.to_dict())
            if opp.status == OpportunityStatus.NORMALIZED:
                opp.transition_to(OpportunityStatus.EVALUATING)
            elif opp.status == OpportunityStatus.EVALUATING:
                opp.version += 1

            uow.opportunities.save(opp)

            opp_event = make_opportunity_evaluated_event(
                opportunity_id=opp.opportunity_id,
                user_id=user_id,
                aggregate_version=opp.version,
                eligibility_status=snapshot.eligibility.status.value,
                fit_score=snapshot.fit.components.total_score,
                fit_tier=snapshot.fit.components.fit_tier.value,
                coverage=snapshot.eligibility.coverage,
            )
            uow.events.append(opp_event)
            return opp, snapshot

    def compute_evaluation_staleness(self, user_id: str, opportunity: Opportunity) -> bool:
        """Deterministically determine if an opportunity's evaluation snapshot is stale."""
        eval_meta = opportunity.metadata.get("evaluation")
        if not eval_meta or not isinstance(eval_meta, dict):
            return False

        with self.storage.unit_of_work() as uow:
            mission_goal = ""
            mission_constraints: list[str] = []
            if opportunity.mission_id:
                mission = uow.missions.get_by_id(opportunity.mission_id)
                if mission:
                    mission_goal = mission.goal
                    mission_constraints = list(mission.constraints)

            current_input_hash = opportunity.compute_input_hash(mission_goal, mission_constraints)
            stored_input_hash = eval_meta.get("evaluation_input_hash")
            if stored_input_hash and stored_input_hash != current_input_hash:
                return True

            active_facts = uow.context.list_active(user_id=user_id)
            raw_normalized = opportunity.metadata.get("normalized_requirements")
            if raw_normalized:
                normalized = [NormalizedRequirement.from_dict(d) for d in raw_normalized]
            else:
                normalized = parse_requirements(opportunity.opportunity_id, opportunity.requirements)

            relevant_facts = OpportunityEvaluator.get_relevant_context_facts(normalized, active_facts)
            current_context_hash = OpportunityEvaluator.compute_context_snapshot_hash(relevant_facts)
            stored_context_hash = eval_meta.get("context_snapshot_hash")
            if stored_context_hash and stored_context_hash != current_context_hash:
                return True

            return False

    def shortlist_opportunity_for_user(
        self,
        user_id: str,
        opportunity_id: str,
        reason: str | None = None,
        override_ineligible: bool = False,
    ) -> Opportunity:
        """Shortlist an evaluated opportunity, enforcing non-stale evaluation and eligibility policy."""
        with self.storage.unit_of_work() as uow:
            opp = uow.opportunities.get_by_id(opportunity_id)
            if opp is None or opp.user_id != user_id:
                raise EntityNotFoundError("Opportunity", opportunity_id)

            eval_meta = opp.metadata.get("evaluation")
            if not eval_meta or not isinstance(eval_meta, dict):
                raise InvalidStateTransitionError(
                    "Opportunity",
                    opp.status.value,
                    OpportunityStatus.SHORTLISTED.value,
                    "Cannot shortlist an opportunity that has not been evaluated.",
                )

            # Check staleness: input hash + relevant context hash
            mission_goal = ""
            mission_constraints: list[str] = []
            if opp.mission_id:
                mission = uow.missions.get_by_id(opp.mission_id)
                if mission:
                    mission_goal = mission.goal
                    mission_constraints = list(mission.constraints)

            current_input_hash = opp.compute_input_hash(mission_goal, mission_constraints)
            stored_input_hash = eval_meta.get("evaluation_input_hash")
            if stored_input_hash and stored_input_hash != current_input_hash:
                raise InvalidStateTransitionError(
                    "Opportunity",
                    opp.status.value,
                    OpportunityStatus.SHORTLISTED.value,
                    "Evaluation is stale. Re-evaluate before shortlisting.",
                )

            active_facts = uow.context.list_active(user_id=user_id)
            raw_normalized = opp.metadata.get("normalized_requirements")
            if raw_normalized:
                normalized = [NormalizedRequirement.from_dict(d) for d in raw_normalized]
            else:
                normalized = parse_requirements(opp.opportunity_id, opp.requirements)

            relevant_facts = OpportunityEvaluator.get_relevant_context_facts(normalized, active_facts)
            current_context_hash = OpportunityEvaluator.compute_context_snapshot_hash(relevant_facts)
            stored_context_hash = eval_meta.get("context_snapshot_hash")
            if stored_context_hash and stored_context_hash != current_context_hash:
                raise InvalidStateTransitionError(
                    "Opportunity",
                    opp.status.value,
                    OpportunityStatus.SHORTLISTED.value,
                    "Evaluation is stale. Re-evaluate before shortlisting.",
                )

            # Eligibility policy checks
            elig_status = eval_meta.get("eligibility", {}).get("status")
            if elig_status == EligibilityStatus.INELIGIBLE.value:
                if not override_ineligible:
                    raise InvalidStateTransitionError(
                        "Opportunity",
                        opp.status.value,
                        OpportunityStatus.SHORTLISTED.value,
                        "Opportunity has been evaluated as INELIGIBLE. Set override_ineligible=true and provide a reason to shortlist.",
                    )
                if not reason or not reason.strip():
                    raise DomainValidationError(
                        "A non-empty reason is required when shortlisting an INELIGIBLE opportunity."
                    )

            opp.transition_to(OpportunityStatus.SHORTLISTED, reason=reason)
            uow.opportunities.save(opp)

            event = make_opportunity_shortlisted_event(
                opportunity_id=opp.opportunity_id,
                user_id=user_id,
                aggregate_version=opp.version,
                reason=reason,
                overridden=override_ineligible,
            )
            uow.events.append(event)
            return opp

    def convert_opportunity_to_case(
        self,
        user_id: str,
        opportunity_id: str,
        mission_id: str | None = None,
        case_type: CaseType | str | None = None,
        title: str | None = None,
        goal: str | None = None,
    ) -> tuple[Case, Opportunity, bool]:
        """Convert a shortlisted opportunity to a Case atomically and idempotently.

        Returns (case, opportunity, created).
        """
        with self.storage.unit_of_work() as uow:
            opp = uow.opportunities.get_by_id(opportunity_id)
            if opp is None or opp.user_id != user_id:
                raise EntityNotFoundError("Opportunity", opportunity_id)

            # Idempotent replay: if already converted, return existing case
            if opp.status == OpportunityStatus.CONVERTED_TO_CASE and opp.converted_case_id:
                existing_case = uow.cases.get_by_id(opp.converted_case_id)
                if existing_case is not None:
                    # Check replay parameter conflict
                    if mission_id is not None and existing_case.mission_id != mission_id:
                        raise InvalidStateTransitionError(
                            "Opportunity",
                            opp.status.value,
                            OpportunityStatus.CONVERTED_TO_CASE.value,
                            f"Replay conflict: requested mission_id '{mission_id}' conflicts with existing converted case mission_id '{existing_case.mission_id}'.",
                        )
                    if case_type is not None:
                        expected_ct = case_type.value if isinstance(case_type, CaseType) else str(case_type)
                        if existing_case.case_type_str != expected_ct:
                            raise InvalidStateTransitionError(
                                "Opportunity",
                                opp.status.value,
                                OpportunityStatus.CONVERTED_TO_CASE.value,
                                f"Replay conflict: requested case_type '{case_type}' conflicts with existing converted case.",
                            )
                    if title is not None and title.strip() and existing_case.title != title.strip():
                        raise InvalidStateTransitionError(
                            "Opportunity",
                            opp.status.value,
                            OpportunityStatus.CONVERTED_TO_CASE.value,
                            "Replay conflict: requested title conflicts with existing converted case.",
                        )
                    if goal is not None and goal.strip() and existing_case.goal != goal.strip():
                        raise InvalidStateTransitionError(
                            "Opportunity",
                            opp.status.value,
                            OpportunityStatus.CONVERTED_TO_CASE.value,
                            "Replay conflict: requested goal conflicts with existing converted case.",
                        )
                    return existing_case, opp, False

            # Invariant: Must be in SHORTLISTED status to convert
            if opp.status != OpportunityStatus.SHORTLISTED:
                raise InvalidStateTransitionError(
                    "Opportunity",
                    opp.status.value,
                    OpportunityStatus.CONVERTED_TO_CASE.value,
                    "Only shortlisted opportunities can be converted to cases.",
                )

            # Target mission resolution and validation
            target_mission_id = mission_id or opp.mission_id
            if not target_mission_id:
                raise DomainValidationError("A mission is required to convert an opportunity to a case.")

            mission = uow.missions.get_by_id(target_mission_id)
            if mission is None or mission.user_id != user_id:
                raise EntityNotFoundError("Mission", target_mission_id)

            if mission.archived:
                raise InvalidStateTransitionError(
                    "Opportunity",
                    opp.status.value,
                    OpportunityStatus.CONVERTED_TO_CASE.value,
                    "Cannot convert opportunity to an archived mission.",
                )

            if mission.status not in (MissionStatus.DRAFT, MissionStatus.ACTIVE):
                raise InvalidStateTransitionError(
                    "Opportunity",
                    opp.status.value,
                    OpportunityStatus.CONVERTED_TO_CASE.value,
                    f"Cannot convert opportunity to mission in status '{mission.status.value}'. Mission must be DRAFT or ACTIVE.",
                )

            # Resolve CaseType from mapping table or user override
            if case_type is not None:
                resolved_case_type = CaseType(case_type) if isinstance(case_type, str) else case_type
            else:
                opp_enum = opp.opportunity_type if isinstance(opp.opportunity_type, OpportunityType) else OpportunityType(opp.opportunity_type)
                resolved_case_type = OPPORTUNITY_TO_CASE_TYPE_MAP.get(opp_enum, CaseType.GENERAL)

            # Construct Case
            case_title = (title or "").strip() or (
                f"Application: {opp.title} - {opp.organization}"
                if opp.organization
                else f"Application: {opp.title}"
            )
            case_goal = (goal or "").strip() or (
                f"Pursue and apply for {opp.title} at {opp.organization or 'target organization'}"
            )

            from caseworker.domain.enums import CaseStatus
            new_case = Case(
                user_id=user_id,
                mission_id=target_mission_id,
                case_type=resolved_case_type,
                title=case_title,
                goal=case_goal,
                status=CaseStatus.NEW,
                success_criteria=[f"Complete application for {opp.title}"],
                constraints=[],
                deadline=opp.deadline,
            )

            uow.cases.save(new_case)

            case_event = make_case_created_event(
                case_id=new_case.case_id,
                user_id=user_id,
                aggregate_version=new_case.version,
                payload=new_case.to_dict(),
            )
            uow.events.append(case_event)

            # Update Opportunity with mission_id, converted_case_id and transition to CONVERTED_TO_CASE
            old_status = opp.status.value
            opp.mission_id = target_mission_id
            opp.converted_case_id = new_case.case_id
            opp.transition_to(OpportunityStatus.CONVERTED_TO_CASE)
            uow.opportunities.save(opp)

            # SINGLE EVENT PER AGGREGATE VERSION INVARIANT
            opp_event = make_opportunity_converted_to_case_event(
                opportunity_id=opp.opportunity_id,
                user_id=user_id,
                aggregate_version=opp.version,
                case_id=new_case.case_id,
                mission_id=target_mission_id,
                old_status=old_status,
                new_status=opp.status.value,
            )
            uow.events.append(opp_event)

            return new_case, opp, True

    def transition_opportunity_for_user(
        self,
        user_id: str,
        opportunity_id: str,
        new_status: OpportunityStatus | str,
        reason: str | None = None,
    ) -> Opportunity:
        """Transition an opportunity's state, asserting user ownership.

        Protected states (NORMALIZED, EVALUATING, SHORTLISTED, CONVERTED_TO_CASE) cannot be transitioned via this method.
        """
        target = OpportunityStatus(new_status) if isinstance(new_status, str) else new_status
        if target in (
            OpportunityStatus.NORMALIZED,
            OpportunityStatus.EVALUATING,
            OpportunityStatus.SHORTLISTED,
            OpportunityStatus.CONVERTED_TO_CASE,
        ):
            raise InvalidStateTransitionError(
                "Opportunity",
                "generic",
                target.value,
                f"Transition to '{target.value}' requires dedicated workflow endpoint.",
            )

        with self.storage.unit_of_work() as uow:
            opp = uow.opportunities.get_by_id(opportunity_id)
            if opp is None or opp.user_id != user_id:
                raise EntityNotFoundError("Opportunity", opportunity_id)

            old_status = opp.status.value
            opp.transition_to(target, reason=reason)
            uow.opportunities.save(opp)

            event = make_opportunity_status_changed_event(
                opportunity_id=opp.opportunity_id,
                user_id=opp.user_id,
                aggregate_version=opp.version,
                old_status=old_status,
                new_status=opp.status.value,
                reason=reason,
            )
            uow.events.append(event)
            return opp
