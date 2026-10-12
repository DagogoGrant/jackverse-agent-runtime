"""Cases API endpoints."""

from __future__ import annotations

from typing import Annotated
from fastapi import APIRouter, Depends, Request, Response, status

from caseworker.api.dependencies import (
    PaginationParams,
    get_artifact_service,
    get_case_service,
    get_mission_service,
    get_principal,
)
from caseworker.api.etag import check_if_match, set_etag_header
from caseworker.api.identity import Principal
from caseworker.api.schemas.artifact import (
    ArtifactBlockResponse,
    ArtifactProvenanceResponse,
    PreparedArtifactListResponse,
    PreparedArtifactResponse,
)
from caseworker.api.schemas.case import (
    CaseResponse,
    CreateCaseRequest,
    ResolveCaseRequest,
    TransitionCaseRequest,
)
from caseworker.api.schemas.common import PaginatedResponse
from caseworker.api.schemas.preparation import (
    ClaimIntentResponse,
    EvidenceMappingItemResponse,
    GapItemResponse,
    PreparationIntelligenceReportResponse,
    PreparationStrategyResponse,
)
from caseworker.domain.artifact import PreparedArtifact
from caseworker.domain.case import Case
from caseworker.domain.enums import CaseStatus
from caseworker.domain.errors import EntityNotFoundError
from caseworker.domain.preparation import PreparationIntelligenceReport
from caseworker.domain.types import to_iso_utc
from caseworker.services.artifact_service import ArtifactPreparationService
from caseworker.services.case_service import CaseService
from caseworker.services.mission_service import MissionService

router = APIRouter(tags=["cases"])


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


@router.post("/api/v1/cases", response_model=CaseResponse, status_code=status.HTTP_201_CREATED)
async def create_case(
    req: CreateCaseRequest,
    principal: Annotated[Principal, Depends(get_principal)],
    service: Annotated[CaseService, Depends(get_case_service)],
    response: Response,
) -> CaseResponse:
    """Create a new case bound strictly to the authenticated caller."""
    case = service.create_case(
        user_id=principal.user_id,
        title=req.title,
        goal=req.goal,
        case_type=req.case_type,
        mission_id=req.mission_id,
        success_criteria=req.success_criteria,
        constraints=req.constraints,
        deadline=req.deadline,
    )
    set_etag_header(response, "case", case.case_id, case.version)
    return _to_case_response(case)


@router.post("/api/v1/missions/{mission_id}/cases", response_model=CaseResponse, status_code=status.HTTP_201_CREATED)
async def create_mission_case(
    mission_id: str,
    req: CreateCaseRequest,
    principal: Annotated[Principal, Depends(get_principal)],
    service: Annotated[CaseService, Depends(get_case_service)],
    response: Response,
) -> CaseResponse:
    """Convenience endpoint to create a case linked to a verified user mission."""
    case = service.create_case(
        user_id=principal.user_id,
        title=req.title,
        goal=req.goal,
        case_type=req.case_type,
        mission_id=mission_id,
        success_criteria=req.success_criteria,
        constraints=req.constraints,
        deadline=req.deadline,
    )
    set_etag_header(response, "case", case.case_id, case.version)
    return _to_case_response(case)


@router.get("/api/v1/missions/{mission_id}/cases", response_model=list[CaseResponse])
async def list_mission_cases(
    mission_id: str,
    principal: Annotated[Principal, Depends(get_principal)],
    case_service: Annotated[CaseService, Depends(get_case_service)],
    mission_service: Annotated[MissionService, Depends(get_mission_service)],
) -> list[CaseResponse]:
    """List cases for a mission, verifying mission ownership (404 if not found)."""
    mission = mission_service.get_mission_for_user(principal.user_id, mission_id)
    if mission is None:
        raise EntityNotFoundError("Mission", mission_id)

    cases = case_service.list_mission_cases_for_user(principal.user_id, mission_id)
    return [_to_case_response(c) for c in cases]


@router.get("/api/v1/cases", response_model=PaginatedResponse[CaseResponse])
async def list_cases(
    principal: Annotated[Principal, Depends(get_principal)],
    service: Annotated[CaseService, Depends(get_case_service)],
    pagination: Annotated[PaginationParams, Depends()],
    status_filter: CaseStatus | None = None,
) -> PaginatedResponse[CaseResponse]:
    """List cases belonging to the authenticated user."""
    all_cases = service.list_user_cases(principal.user_id, status=status_filter)
    total = len(all_cases)
    page_items = all_cases[pagination.offset : pagination.offset + pagination.limit]
    return PaginatedResponse(
        items=[_to_case_response(c) for c in page_items],
        total=total,
        limit=pagination.limit,
        offset=pagination.offset,
    )


@router.get("/api/v1/cases/{case_id}", response_model=CaseResponse)
async def get_case(
    case_id: str,
    principal: Annotated[Principal, Depends(get_principal)],
    service: Annotated[CaseService, Depends(get_case_service)],
    response: Response,
) -> CaseResponse:
    """Retrieve a case asserting user ownership (404 on unowned cases)."""
    case = service.get_case_for_user(principal.user_id, case_id)
    if case is None:
        raise EntityNotFoundError("Case", case_id)
    set_etag_header(response, "case", case.case_id, case.version)
    return _to_case_response(case)


@router.post("/api/v1/cases/{case_id}/transition", response_model=CaseResponse)
async def transition_case(
    case_id: str,
    req: TransitionCaseRequest,
    request: Request,
    response: Response,
    principal: Annotated[Principal, Depends(get_principal)],
    service: Annotated[CaseService, Depends(get_case_service)],
) -> CaseResponse:
    """Execute state machine transition with ETag precondition enforcement."""
    existing = service.get_case_for_user(principal.user_id, case_id)
    if existing is None:
        raise EntityNotFoundError("Case", case_id)

    check_if_match(request, "case", existing.case_id, existing.version)

    updated = service.transition_case_for_user(
        user_id=principal.user_id,
        case_id=case_id,
        new_status=req.new_status,
        reason=req.reason,
    )
    set_etag_header(response, "case", updated.case_id, updated.version)
    return _to_case_response(updated)


@router.post("/api/v1/cases/{case_id}/resolve", response_model=CaseResponse)
async def resolve_case(
    case_id: str,
    req: ResolveCaseRequest,
    request: Request,
    response: Response,
    principal: Annotated[Principal, Depends(get_principal)],
    service: Annotated[CaseService, Depends(get_case_service)],
) -> CaseResponse:
    """Resolve a case with recorded outcome and ETag precondition enforcement."""
    existing = service.get_case_for_user(principal.user_id, case_id)
    if existing is None:
        raise EntityNotFoundError("Case", case_id)

    check_if_match(request, "case", existing.case_id, existing.version)

    updated = service.resolve_case_for_user(
        user_id=principal.user_id,
        case_id=case_id,
        outcome=req.outcome,
    )
    set_etag_header(response, "case", updated.case_id, updated.version)
    return _to_case_response(updated)


def _to_prep_report_response(
    report: PreparationIntelligenceReport,
    is_stale: bool = False,
) -> PreparationIntelligenceReportResponse:
    evidence_items = [
        EvidenceMappingItemResponse(
            requirement_id=e.requirement_id,
            requirement_type=e.requirement_type,
            description=e.description,
            mandatory=e.mandatory,
            status=e.status.value if hasattr(e.status, "value") else str(e.status),
            matched_fact_id=e.matched_fact_id,
            matched_fact_version=e.matched_fact_version,
            verification_status=(
                e.verification_status.value
                if hasattr(e.verification_status, "value")
                else (str(e.verification_status) if e.verification_status else None)
            ),
            fact_namespace=e.fact_namespace,
            fact_key=e.fact_key,
            notes=e.notes,
        )
        for e in report.evidence_map
    ]

    gap_items = [
        GapItemResponse(
            gap_id=g.gap_id,
            requirement_id=g.requirement_id,
            requirement_type=g.requirement_type,
            category=g.category.value if hasattr(g.category, "value") else str(g.category),
            description=g.description,
            severity=g.severity,
            mitigation_guidance=g.mitigation_guidance,
            fact_id=g.fact_id,
        )
        for g in report.gaps
    ]

    claim_intents = [
        ClaimIntentResponse(
            intent_id=c.intent_id,
            requirement_id=c.requirement_id,
            target_material=c.target_material.value if hasattr(c.target_material, "value") else str(c.target_material),
            claim_topic=c.claim_topic,
            supporting_fact_ids=list(c.supporting_fact_ids),
            suggested_angle=c.suggested_angle,
        )
        for c in report.strategy.claim_intents
    ]

    strategy_res = PreparationStrategyResponse(
        recommended_materials=[
            m.value if hasattr(m, "value") else str(m)
            for m in report.strategy.recommended_materials
        ],
        strengths=list(report.strategy.strengths),
        mitigations=list(report.strategy.mitigations),
        claim_intents=claim_intents,
        materials_checklist=list(report.strategy.materials_checklist),
    )

    return PreparationIntelligenceReportResponse(
        report_id=report.report_id,
        case_id=report.case_id,
        opportunity_id=report.opportunity_id,
        user_id=report.user_id,
        schema_version=report.schema_version,
        analyzer_version=report.analyzer_version,
        evidence_map=evidence_items,
        gaps=gap_items,
        strategy=strategy_res,
        input_hash=report.input_hash,
        context_snapshot_hash=report.context_snapshot_hash,
        source_evaluation_id=report.source_evaluation_id,
        created_at=to_iso_utc(report.created_at) or "",
        version=report.version,
        is_stale=is_stale,
    )


@router.post(
    "/api/v1/cases/{case_id}/preparation/intelligence",
    response_model=PreparationIntelligenceReportResponse,
    status_code=status.HTTP_201_CREATED,
)
async def generate_preparation_intelligence(
    case_id: str,
    request: Request,
    response: Response,
    principal: Annotated[Principal, Depends(get_principal)],
    service: Annotated[CaseService, Depends(get_case_service)],
) -> PreparationIntelligenceReportResponse:
    """Analyze preparation intelligence for converted case, advance lifecycle, and return versioned report."""
    existing = service.get_case_for_user(principal.user_id, case_id)
    if existing is None:
        raise EntityNotFoundError("Case", case_id)

    check_if_match(request, "case", existing.case_id, existing.version)

    report, updated_case = service.generate_preparation_intelligence(
        user_id=principal.user_id,
        case_id=case_id,
        expected_case_version=existing.version,
    )
    set_etag_header(response, "case", updated_case.case_id, updated_case.version)
    return _to_prep_report_response(report, is_stale=False)


@router.get(
    "/api/v1/cases/{case_id}/preparation/intelligence",
    response_model=PreparationIntelligenceReportResponse,
)
async def get_latest_preparation_intelligence(
    case_id: str,
    principal: Annotated[Principal, Depends(get_principal)],
    service: Annotated[CaseService, Depends(get_case_service)],
) -> PreparationIntelligenceReportResponse:
    """Retrieve the latest preparation intelligence report for case, including staleness status."""
    report, is_stale = service.get_latest_preparation_report(
        user_id=principal.user_id,
        case_id=case_id,
    )
    if report is None:
        raise EntityNotFoundError("PreparationIntelligenceReport", case_id)
    return _to_prep_report_response(report, is_stale=is_stale)


def _to_artifact_response(a: PreparedArtifact) -> PreparedArtifactResponse:
    return PreparedArtifactResponse(
        artifact_id=a.artifact_id,
        artifact_set_id=a.artifact_set_id,
        case_id=a.case_id,
        opportunity_id=a.opportunity_id,
        user_id=a.user_id,
        material_type=a.material_type.value,
        title=a.title,
        schema_version=a.schema_version,
        planner_version=a.planner_version,
        status=a.status.value,
        blocks=[
            ArtifactBlockResponse(
                block_id=b.block_id,
                block_type=b.block_type,
                content=b.content,
                grounding_type=b.grounding_type.value,
                claim_ids=list(b.claim_ids),
                fact_ids=list(b.fact_ids),
                opportunity_fields=list(b.opportunity_fields),
                metadata=dict(b.metadata),
            )
            for b in a.blocks
        ],
        source_report_id=a.source_report_id,
        input_hash=a.input_hash,
        grounding_snapshot_hash=a.grounding_snapshot_hash,
        rendered_markdown=a.render_markdown(),
        created_at=to_iso_utc(a.created_at),
        updated_at=to_iso_utc(a.updated_at),
        version=a.version,
    )


@router.post(
    "/api/v1/cases/{case_id}/preparation/artifacts",
    response_model=PreparedArtifactListResponse,
    status_code=status.HTTP_201_CREATED,
)
async def draft_preparation_artifacts(
    case_id: str,
    request: Request,
    response: Response,
    principal: Annotated[Principal, Depends(get_principal)],
    case_service: Annotated[CaseService, Depends(get_case_service)],
    artifact_service: Annotated[ArtifactPreparationService, Depends(get_artifact_service)],
) -> PreparedArtifactListResponse:
    """Draft claim-bound CV, Cover Letter, and Screening Answers / Answer Bank for case."""
    existing = case_service.get_case_for_user(principal.user_id, case_id)
    if existing is None:
        raise EntityNotFoundError("Case", case_id)

    check_if_match(request, "case", existing.case_id, existing.version)

    artifacts, updated_case = artifact_service.draft_job_application_artifacts(
        user_id=principal.user_id,
        case_id=case_id,
        expected_case_version=existing.version,
    )
    set_etag_header(response, "case", updated_case.case_id, updated_case.version)
    dtos = [_to_artifact_response(a) for a in artifacts]
    return PreparedArtifactListResponse(artifacts=dtos, total=len(dtos))


@router.get(
    "/api/v1/cases/{case_id}/preparation/artifacts",
    response_model=PreparedArtifactListResponse,
)
async def list_preparation_artifacts(
    case_id: str,
    principal: Annotated[Principal, Depends(get_principal)],
    artifact_service: Annotated[ArtifactPreparationService, Depends(get_artifact_service)],
) -> PreparedArtifactListResponse:
    """List all prepared application artifacts for a case."""
    artifacts = artifact_service.get_case_artifacts(
        user_id=principal.user_id,
        case_id=case_id,
    )
    dtos = [_to_artifact_response(a) for a in artifacts]
    return PreparedArtifactListResponse(artifacts=dtos, total=len(dtos))


@router.get(
    "/api/v1/cases/{case_id}/preparation/artifacts/{artifact_id}",
    response_model=PreparedArtifactResponse,
)
async def get_preparation_artifact(
    case_id: str,
    artifact_id: str,
    principal: Annotated[Principal, Depends(get_principal)],
    artifact_service: Annotated[ArtifactPreparationService, Depends(get_artifact_service)],
) -> PreparedArtifactResponse:
    """Retrieve a single prepared artifact with full AST blocks."""
    artifact = artifact_service.get_artifact(
        user_id=principal.user_id,
        case_id=case_id,
        artifact_id=artifact_id,
    )
    return _to_artifact_response(artifact)


@router.get(
    "/api/v1/cases/{case_id}/preparation/artifacts/{artifact_id}/export",
)
async def export_preparation_artifact(
    case_id: str,
    artifact_id: str,
    principal: Annotated[Principal, Depends(get_principal)],
    artifact_service: Annotated[ArtifactPreparationService, Depends(get_artifact_service)],
    format: str = "markdown",
) -> Response:
    """Export clean rendered publication text with zero synthetic citations."""
    rendered = artifact_service.export_artifact(
        user_id=principal.user_id,
        case_id=case_id,
        artifact_id=artifact_id,
        format_type=format,
    )
    return Response(content=rendered, media_type="text/plain; charset=utf-8")


@router.get(
    "/api/v1/cases/{case_id}/preparation/artifacts/{artifact_id}/provenance",
    response_model=ArtifactProvenanceResponse,
)
async def get_artifact_provenance(
    case_id: str,
    artifact_id: str,
    principal: Annotated[Principal, Depends(get_principal)],
    artifact_service: Annotated[ArtifactPreparationService, Depends(get_artifact_service)],
) -> ArtifactProvenanceResponse:
    """Retrieve block-level grounding provenance map for inspection."""
    artifact = artifact_service.get_artifact(
        user_id=principal.user_id,
        case_id=case_id,
        artifact_id=artifact_id,
    )
    prov = artifact.render_provenance_map()
    return ArtifactProvenanceResponse(
        artifact_id=prov["artifact_id"],
        artifact_set_id=prov["artifact_set_id"],
        case_id=prov["case_id"],
        opportunity_id=prov["opportunity_id"],
        material_type=prov["material_type"],
        title=prov["title"],
        status=prov["status"],
        source_report_id=prov["source_report_id"],
        input_hash=prov["input_hash"],
        grounding_snapshot_hash=prov["grounding_snapshot_hash"],
        version=prov["version"],
        blocks=[ArtifactBlockResponse(**b) for b in prov["blocks"]],
    )


