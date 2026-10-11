"""Opportunities API endpoints."""

from __future__ import annotations

from typing import Annotated
from fastapi import APIRouter, Depends, Request, Response, status

from caseworker.api.dependencies import (
    PaginationParams,
    get_opportunity_service,
    get_principal,
)
from caseworker.api.etag import check_if_match, set_etag_header
from caseworker.api.identity import Principal
from caseworker.api.schemas.case import CaseResponse
from caseworker.api.schemas.common import PaginatedResponse
from caseworker.api.schemas.opportunity import (
    ConvertToCaseRequest,
    ConvertToCaseResponse,
    CreateOpportunityRequest,
    EligibilityResultResponse,
    EvaluationSnapshotResponse,
    FitScoreComponentsResponse,
    NormalizeOpportunityResponse,
    NormalizedRequirementResponse,
    OpportunityResponse,
    RequirementEvidenceResponse,
    ShortlistOpportunityRequest,
    TransitionOpportunityRequest,
    make_opportunity_response,
)
from caseworker.domain.case import Case
from caseworker.domain.enums import OpportunityStatus
from caseworker.domain.errors import EntityNotFoundError, InvalidStateTransitionError
from caseworker.domain.opportunity_evaluator import OpportunityEvaluationSnapshot
from caseworker.domain.types import to_iso_utc
from caseworker.services.opportunity_service import OpportunityService

router = APIRouter(prefix="/api/v1/opportunities", tags=["opportunities"])


def _to_case_response(c: Case) -> CaseResponse:
    return CaseResponse(
        case_id=c.case_id,
        mission_id=c.mission_id,
        user_id=c.user_id,
        case_type=c.case_type_str,
        title=c.title,
        goal=c.goal,
        status=c.status.value if hasattr(c.status, "value") else str(c.status),
        success_criteria=list(c.success_criteria),
        constraints=list(c.constraints),
        created_at=to_iso_utc(c.created_at),
        updated_at=to_iso_utc(c.updated_at),
        deadline=to_iso_utc(c.deadline) if c.deadline else None,
        resolved_at=to_iso_utc(c.resolved_at) if c.resolved_at else None,
        outcome=c.outcome,
        version=c.version,
    )


def _snapshot_to_response(
    s: OpportunityEvaluationSnapshot,
    is_stale: bool = False,
) -> EvaluationSnapshotResponse:
    return EvaluationSnapshotResponse(
        evaluation_id=s.evaluation_id,
        evaluated_at=to_iso_utc(s.evaluated_at),
        evaluation_policy=s.evaluation_policy,
        eligibility=EligibilityResultResponse(
            status=s.eligibility.status.value,
            coverage=s.eligibility.coverage,
            mandatory_met=s.eligibility.mandatory_met,
            mandatory_total=s.eligibility.mandatory_total,
            contradicted_requirements=list(s.eligibility.contradicted_requirements),
            missing_or_unknown_requirements=list(s.eligibility.missing_or_unknown_requirements),
        ),
        fit=FitScoreComponentsResponse(
            skills_score=s.fit.components.skills_score,
            preferred_reqs_score=s.fit.components.preferred_reqs_score,
            location_score=s.fit.components.location_score,
            seniority_score=s.fit.components.seniority_score,
            total_score=s.fit.components.total_score,
            fit_tier=s.fit.components.fit_tier.value,
            explanations=list(s.fit.components.explanations),
        ),
        evidence=[
            RequirementEvidenceResponse(
                requirement_id=e.requirement_id,
                fact_id=e.fact_id,
                fact_version=e.fact_version,
                verification_status=e.verification_status.value if hasattr(e.verification_status, "value") else str(e.verification_status) if e.verification_status else None,
                match_result=e.match_result.value,
                notes=e.notes,
            )
            for e in s.evidence
        ],
        evaluation_input_hash=s.evaluation_input_hash,
        context_snapshot_hash=s.context_snapshot_hash,
        is_stale=is_stale,
    )


@router.post("", response_model=OpportunityResponse)
async def create_or_deduplicate_opportunity(
    req: CreateOpportunityRequest,
    principal: Annotated[Principal, Depends(get_principal)],
    service: Annotated[OpportunityService, Depends(get_opportunity_service)],
    response: Response,
) -> OpportunityResponse:
    """Discover opportunity with user-scoped deduplication (returns 201 for new, 200 for existing)."""
    opp, created = service.create_opportunity(
        user_id=principal.user_id,
        title=req.title,
        opportunity_type=req.opportunity_type,
        organization=req.organization,
        source_url=req.source_url,
        source_name=req.source_name,
        location=req.location,
        requirements=req.requirements,
        metadata=req.metadata,
        deadline=req.deadline,
        mission_id=req.mission_id,
        external_posting_id=req.external_posting_id,
    )
    if created:
        response.status_code = status.HTTP_201_CREATED
    else:
        response.status_code = status.HTTP_200_OK

    set_etag_header(response, "opportunity", opp.opportunity_id, opp.version)
    return make_opportunity_response(opp)


@router.get("", response_model=PaginatedResponse[OpportunityResponse])
async def list_opportunities(
    principal: Annotated[Principal, Depends(get_principal)],
    service: Annotated[OpportunityService, Depends(get_opportunity_service)],
    pagination: Annotated[PaginationParams, Depends()],
    status_filter: OpportunityStatus | None = None,
) -> PaginatedResponse[OpportunityResponse]:
    """List opportunities belonging to the authenticated user."""
    all_opps = service.list_user_opportunities(principal.user_id, status=status_filter)
    total = len(all_opps)
    page_items = all_opps[pagination.offset : pagination.offset + pagination.limit]
    items = []
    for o in page_items:
        is_stale = service.compute_evaluation_staleness(principal.user_id, o)
        items.append(make_opportunity_response(o, evaluation_is_stale=is_stale))
    return PaginatedResponse(
        items=items,
        total=total,
        limit=pagination.limit,
        offset=pagination.offset,
    )


@router.get("/{opportunity_id}", response_model=OpportunityResponse)
async def get_opportunity(
    opportunity_id: str,
    principal: Annotated[Principal, Depends(get_principal)],
    service: Annotated[OpportunityService, Depends(get_opportunity_service)],
    response: Response,
) -> OpportunityResponse:
    """Retrieve an opportunity asserting user ownership (404 on unowned records)."""
    opp = service.get_opportunity_for_user(principal.user_id, opportunity_id)
    if opp is None:
        raise EntityNotFoundError("Opportunity", opportunity_id)
    is_stale = service.compute_evaluation_staleness(principal.user_id, opp)
    set_etag_header(response, "opportunity", opp.opportunity_id, opp.version)
    return make_opportunity_response(opp, evaluation_is_stale=is_stale)


@router.post("/{opportunity_id}/normalize", response_model=NormalizeOpportunityResponse)
async def normalize_opportunity(
    opportunity_id: str,
    request: Request,
    response: Response,
    principal: Annotated[Principal, Depends(get_principal)],
    service: Annotated[OpportunityService, Depends(get_opportunity_service)],
) -> NormalizeOpportunityResponse:
    """Normalize opportunity requirements into opp_req_v1 structured criteria."""
    existing = service.get_opportunity_for_user(principal.user_id, opportunity_id)
    if existing is None:
        raise EntityNotFoundError("Opportunity", opportunity_id)

    check_if_match(request, "opportunity", existing.opportunity_id, existing.version)

    opp, normalized = service.normalize_opportunity_for_user(principal.user_id, opportunity_id)
    set_etag_header(response, "opportunity", opp.opportunity_id, opp.version)
    return NormalizeOpportunityResponse(
        opportunity_id=opp.opportunity_id,
        requirements=[
            NormalizedRequirementResponse(
                requirement_id=r.requirement_id,
                raw_text=r.raw_text,
                criterion=r.criterion,
                category=r.category.value,
                value=r.value,
                normalization_status=r.normalization_status,
            )
            for r in normalized
        ],
        status=opp.status.value,
        version=opp.version,
    )


@router.post("/{opportunity_id}/evaluate", response_model=EvaluationSnapshotResponse)
async def evaluate_opportunity(
    opportunity_id: str,
    request: Request,
    response: Response,
    principal: Annotated[Principal, Depends(get_principal)],
    service: Annotated[OpportunityService, Depends(get_opportunity_service)],
) -> EvaluationSnapshotResponse:
    """Evaluate tri-state eligibility and explainable fit against purpose-gated context facts."""
    existing = service.get_opportunity_for_user(principal.user_id, opportunity_id)
    if existing is None:
        raise EntityNotFoundError("Opportunity", opportunity_id)

    check_if_match(request, "opportunity", existing.opportunity_id, existing.version)

    opp, snapshot = service.evaluate_opportunity_for_user(principal.user_id, opportunity_id)
    set_etag_header(response, "opportunity", opp.opportunity_id, opp.version)
    return _snapshot_to_response(snapshot)


@router.get("/{opportunity_id}/evaluation", response_model=EvaluationSnapshotResponse)
async def get_opportunity_evaluation(
    opportunity_id: str,
    principal: Annotated[Principal, Depends(get_principal)],
    service: Annotated[OpportunityService, Depends(get_opportunity_service)],
) -> EvaluationSnapshotResponse:
    """Retrieve latest evaluation snapshot for an opportunity."""
    opp = service.get_opportunity_for_user(principal.user_id, opportunity_id)
    if opp is None:
        raise EntityNotFoundError("Opportunity", opportunity_id)

    eval_dict = opp.metadata.get("evaluation")
    if not eval_dict or not isinstance(eval_dict, dict):
        raise EntityNotFoundError("OpportunityEvaluation", opportunity_id)

    is_stale = service.compute_evaluation_staleness(principal.user_id, opp)
    snapshot = OpportunityEvaluationSnapshot.from_dict(eval_dict)
    return _snapshot_to_response(snapshot, is_stale=is_stale)


@router.post("/{opportunity_id}/shortlist", response_model=OpportunityResponse)
async def shortlist_opportunity(
    opportunity_id: str,
    req: ShortlistOpportunityRequest,
    request: Request,
    response: Response,
    principal: Annotated[Principal, Depends(get_principal)],
    service: Annotated[OpportunityService, Depends(get_opportunity_service)],
) -> OpportunityResponse:
    """Shortlist an opportunity enforcing non-stale evaluation and eligibility policy."""
    existing = service.get_opportunity_for_user(principal.user_id, opportunity_id)
    if existing is None:
        raise EntityNotFoundError("Opportunity", opportunity_id)

    check_if_match(request, "opportunity", existing.opportunity_id, existing.version)

    updated = service.shortlist_opportunity_for_user(
        user_id=principal.user_id,
        opportunity_id=opportunity_id,
        reason=req.reason,
        override_ineligible=req.override_ineligible,
    )
    set_etag_header(response, "opportunity", updated.opportunity_id, updated.version)
    return make_opportunity_response(updated)


@router.post("/{opportunity_id}/convert-to-case", response_model=ConvertToCaseResponse)
async def convert_opportunity_to_case(
    opportunity_id: str,
    req: ConvertToCaseRequest,
    request: Request,
    response: Response,
    principal: Annotated[Principal, Depends(get_principal)],
    service: Annotated[OpportunityService, Depends(get_opportunity_service)],
) -> ConvertToCaseResponse:
    """Convert a shortlisted opportunity to a Case atomically and idempotently.

    Idempotent replay precedes ETag check.
    First conversion returns 201 Created with Location header.
    Replay returns 200 OK.
    """
    existing = service.get_opportunity_for_user(principal.user_id, opportunity_id)
    if existing is None:
        raise EntityNotFoundError("Opportunity", opportunity_id)

    # 1. Idempotent check BEFORE ETag evaluation
    if existing.status == OpportunityStatus.CONVERTED_TO_CASE and existing.converted_case_id:
        case, opp, created = service.convert_opportunity_to_case(
            user_id=principal.user_id,
            opportunity_id=opportunity_id,
            mission_id=req.mission_id,
            case_type=req.case_type,
            title=req.title,
            goal=req.goal,
        )
        response.status_code = status.HTTP_200_OK
        set_etag_header(response, "opportunity", opp.opportunity_id, opp.version)
        return ConvertToCaseResponse(
            case_id=case.case_id,
            opportunity_id=opp.opportunity_id,
            case=_to_case_response(case),
            opportunity=make_opportunity_response(opp),
            created=False,
        )

    # 2. First conversion requires valid ETag
    check_if_match(request, "opportunity", existing.opportunity_id, existing.version)

    case, opp, created = service.convert_opportunity_to_case(
        user_id=principal.user_id,
        opportunity_id=opportunity_id,
        mission_id=req.mission_id,
        case_type=req.case_type,
        title=req.title,
        goal=req.goal,
    )

    response.status_code = status.HTTP_201_CREATED
    response.headers["Location"] = f"/api/v1/cases/{case.case_id}"
    set_etag_header(response, "opportunity", opp.opportunity_id, opp.version)

    return ConvertToCaseResponse(
        case_id=case.case_id,
        opportunity_id=opp.opportunity_id,
        case=_to_case_response(case),
        opportunity=make_opportunity_response(opp),
        created=created,
    )


@router.post("/{opportunity_id}/transition", response_model=OpportunityResponse)
async def transition_opportunity(
    opportunity_id: str,
    req: TransitionOpportunityRequest,
    request: Request,
    response: Response,
    principal: Annotated[Principal, Depends(get_principal)],
    service: Annotated[OpportunityService, Depends(get_opportunity_service)],
) -> OpportunityResponse:
    """Transition opportunity status with strict ETag precondition verification.

    Protected transitions (normalized, evaluating, shortlisted, converted_to_case) are blocked.
    """
    if req.new_status in (
        OpportunityStatus.NORMALIZED,
        OpportunityStatus.EVALUATING,
        OpportunityStatus.SHORTLISTED,
        OpportunityStatus.CONVERTED_TO_CASE,
    ):
        raise InvalidStateTransitionError(
            "Opportunity",
            "current",
            req.new_status.value,
            f"Cannot transition to '{req.new_status.value}' via generic transition. Use dedicated workflow endpoint.",
        )

    existing = service.get_opportunity_for_user(principal.user_id, opportunity_id)
    if existing is None:
        raise EntityNotFoundError("Opportunity", opportunity_id)

    check_if_match(request, "opportunity", existing.opportunity_id, existing.version)

    updated = service.transition_opportunity_for_user(
        user_id=principal.user_id,
        opportunity_id=opportunity_id,
        new_status=req.new_status,
        reason=req.reason,
    )
    set_etag_header(response, "opportunity", updated.opportunity_id, updated.version)
    return make_opportunity_response(updated)
