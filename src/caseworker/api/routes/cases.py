"""Cases API endpoints."""

from __future__ import annotations

from typing import Annotated
from fastapi import APIRouter, Depends, Request, Response, status

from caseworker.api.config import APIConfig
from caseworker.api.dependencies import (
    PaginationParams,
    get_action_service,
    get_artifact_service,
    get_config,
    get_execution_plan_service,
    get_governed_execution_service,
    get_package_service,
    get_case_service,
    get_mission_service,
    get_principal,
    get_storage,
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
from caseworker.api.schemas.execution_plan import (
    CreateExecutionPlanRequest,
    EmailAcceptanceEvidenceResponse,
    EmailAttachmentManifestResponse,
    EmailPayloadManifestResponse,
    ExecutionDestinationSchema,
    ExecutionFieldMappingResponse,
    ExecutionPlanFindingResponse,
    ExecutionScreeningResponseDTO,
    ExecutionUploadAssetResponse,
    ExternalExecutionPlanListResponse,
    ExternalExecutionPlanResponse,
    TargetInteractionSchemaSchema,
    TargetSchemaFieldSchema,
    TargetSchemaQuestionSchema,
    TargetSchemaUploadSlotSchema,
    TargetSubmitControlSpecSchema,
)
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
from caseworker.domain.execution_plan import (
    ExecutionDestination,
    ExternalExecutionPlan,
    TargetFieldBinding,
    TargetInteractionSchema,
    TargetSchemaField,
    TargetSchemaQuestion,
    TargetSchemaUploadSlot,
    TargetSubmitControlSpec,
)
from caseworker.domain.preparation import PreparationIntelligenceReport
from caseworker.domain.types import to_iso_utc
from caseworker.services.artifact_service import ArtifactPreparationService
from caseworker.services.case_service import CaseService
from caseworker.services.execution_plan_service import ExecutionPlanService
from caseworker.services.mission_service import MissionService
from caseworker.api.schemas.action import ActionResponse
from caseworker.api.schemas.approval import ApprovalDecisionResponse, ApprovalResponse
from caseworker.api.routes.actions import _to_action_response
from caseworker.api.routes.approvals import _to_approval_response
from caseworker.api.schemas.package import (
    ArtifactPackageSnapshotResponse,
    PreparationPackageListResponse,
    PreparationPackageResponse,
    SafeClaimSnapshotResponse,
    VerificationFindingResponse,
    VerifyPackageRequest,
    VerifyPackageResponse,
)
from caseworker.domain.package import PreparationPackage
from caseworker.services.package_service import PackageService
from caseworker.api.schemas.execution import (
    ExecuteActionRequest,
    ExecuteActionResponse,
    ExecutionAttemptListResponse,
    ExecutionReceiptResponse,
)
from caseworker.api.routes.actions import (
    _to_attempt_response,
    _to_receipt_response,
    execute_action,
)
from caseworker.persistence.sqlite import SQLiteCaseworkerStorage
from caseworker.services.action_service import ActionService
from caseworker.services.governed_execution_service import GovernedExecutionService

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


def _to_artifact_response(a: PreparedArtifact, is_stale: bool = False) -> PreparedArtifactResponse:
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
        is_stale=is_stale,
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
                answer_status=b.answer_status,
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
    dtos = [_to_artifact_response(a, is_stale=False) for a in artifacts]
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
    dtos = []
    for a in artifacts:
        stale_bool, _ = artifact_service.is_artifact_stale(principal.user_id, case_id, a)
        dtos.append(_to_artifact_response(a, is_stale=stale_bool))
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
    is_stale, _ = artifact_service.is_artifact_stale(principal.user_id, case_id, artifact)
    return _to_artifact_response(artifact, is_stale=is_stale)


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
    is_stale, _ = artifact_service.is_artifact_stale(principal.user_id, case_id, artifact)
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
        is_stale=is_stale,
    )






def _to_package_response(pkg: PreparationPackage) -> PreparationPackageResponse:
    return PreparationPackageResponse(
        package_id=pkg.package_id,
        case_id=pkg.case_id,
        artifact_set_id=pkg.artifact_set_id,
        source_report_id=pkg.source_report_id,
        user_id=pkg.user_id,
        status=pkg.status.value,
        artifact_ids=list(pkg.artifact_ids),
        artifact_versions=dict(pkg.artifact_versions),
        artifact_snapshots=[
            ArtifactPackageSnapshotResponse(
                artifact_id=s.artifact_id,
                artifact_version=s.artifact_version,
                material_type=s.material_type,
                rendered_payload_hash=s.rendered_payload_hash,
                grounding_snapshot_hash=s.grounding_snapshot_hash,
                publication_role=s.publication_role,
            )
            for s in pkg.artifact_snapshots
        ],
        claim_snapshots=[
            SafeClaimSnapshotResponse(
                claim_id=c.claim_id,
                claim_version=c.claim_version,
                claim_status=c.claim_status,
                support_snapshot_hash=c.support_snapshot_hash,
                assertion_hash=c.assertion_hash,
            )
            for c in pkg.claim_snapshots
        ],
        rendered_payload_hashes=dict(pkg.rendered_payload_hashes),
        verification_status=pkg.verification_status.value,
        verification_findings=[
            VerificationFindingResponse(
                check_name=f.get("check_name", ""),
                passed=f.get("passed", True),
                severity=f.get("severity", "info"),
                message=f.get("message", ""),
                details=f.get("details", {}),
            )
            if isinstance(f, dict)
            else VerificationFindingResponse(
                check_name=f.check_name,
                passed=f.passed,
                severity=f.severity,
                message=f.message,
                details=f.details,
            )
            for f in pkg.verification_findings
        ],
        frozen_at=pkg.frozen_at.isoformat().replace("+00:00", "Z"),
        package_fingerprint=pkg.package_fingerprint,
        schema_version=pkg.schema_version,
        verifier_version=pkg.verifier_version,
        created_at=pkg.created_at.isoformat().replace("+00:00", "Z"),
        updated_at=pkg.updated_at.isoformat().replace("+00:00", "Z"),
        reason=pkg.reason,
        version=pkg.version,
    )


@router.post(
    "/api/v1/cases/{case_id}/preparation/packages/verify",
    response_model=VerifyPackageResponse,
)
async def verify_preparation_package(
    case_id: str,
    req: VerifyPackageRequest,
    request: Request,
    response: Response,
    principal: Annotated[Principal, Depends(get_principal)],
    case_service: Annotated[CaseService, Depends(get_case_service)],
    package_service: Annotated[PackageService, Depends(get_package_service)],
) -> VerifyPackageResponse:
    """Verify DRAFT candidate artifacts against 7 strict deterministic checks, freeze them, and request approval."""
    existing_bundle = package_service.get_existing_package_bundle(
        user_id=principal.user_id,
        case_id=case_id,
        artifact_set_id=req.artifact_set_id,
    )
    if existing_bundle is not None:
        package, action, approval, findings, case = existing_bundle
        set_etag_header(response, "case", case.case_id, case.version)
    else:
        case = case_service.get_case_for_user(principal.user_id, case_id)
        if case is None:
            raise EntityNotFoundError("Case", case_id)

        check_if_match(request, "case", case.case_id, case.version)

        package, action, approval, findings, case = package_service.verify_and_prepare_package(
            user_id=principal.user_id,
            case_id=case_id,
            artifact_set_id=req.artifact_set_id,
            expected_case_version=case.version,
        )
        set_etag_header(response, "case", case.case_id, case.version)

    pkg_dto = _to_package_response(package)
    act_dto = ActionResponse(
        action_id=action.action_id,
        case_id=action.case_id,
        action_type=action.action_type_str,
        description=action.description,
        parameters=action.parameters,
        status=action.status,
        risk_level=action.risk_level,
        requires_approval=action.requires_approval,
        fingerprint=action.compute_fingerprint(),
        created_at=action.created_at.isoformat().replace("+00:00", "Z"),
        executed_at=action.executed_at.isoformat().replace("+00:00", "Z") if action.executed_at else None,
        result=action.result,
        idempotency_key=action.idempotency_key,
        version=action.version,
    )
    appr_dto = ApprovalResponse(
        approval_id=approval.approval_id,
        action_id=approval.action_id,
        case_id=approval.case_id,
        user_id=approval.user_id,
        action_fingerprint=approval.action_fingerprint,
        status=approval.status.value,
        requested_at=approval.requested_at.isoformat().replace("+00:00", "Z"),
        decided_at=approval.decided_at.isoformat().replace("+00:00", "Z") if approval.decided_at else None,
        expires_at=approval.expires_at.isoformat().replace("+00:00", "Z") if approval.expires_at else None,
        reason=approval.reason,
        version=approval.version,
    )
    findings_dtos = [
        VerificationFindingResponse(
            check_name=f.get("check_name", ""),
            passed=f.get("passed", True),
            severity=f.get("severity", "info"),
            message=f.get("message", ""),
            details=f.get("details", {}),
        )
        if isinstance(f, dict)
        else VerificationFindingResponse(
            check_name=f.check_name,
            passed=f.passed,
            severity=f.severity,
            message=f.message,
            details=f.details,
        )
        for f in findings
    ]

    return VerifyPackageResponse(
        package=pkg_dto,
        action=act_dto,
        approval=appr_dto,
        findings=findings_dtos,
        case_id=case.case_id,
        case_status=case.status.value if hasattr(case.status, "value") else str(case.status),
        case_version=case.version,
    )


@router.get(
    "/api/v1/cases/{case_id}/preparation/packages/latest",
    response_model=PreparationPackageResponse,
)
async def get_latest_preparation_package(
    case_id: str,
    response: Response,
    principal: Annotated[Principal, Depends(get_principal)],
    package_service: Annotated[PackageService, Depends(get_package_service)],
) -> PreparationPackageResponse:
    """Retrieve the latest verified preparation package for a case."""
    package = package_service.get_latest_package_for_case(
        user_id=principal.user_id,
        case_id=case_id,
    )
    if package is None:
        raise EntityNotFoundError("PreparationPackage", f"case:{case_id}")

    set_etag_header(response, "package", package.package_id, package.version)
    return _to_package_response(package)


@router.get(
    "/api/v1/cases/{case_id}/preparation/packages/{package_id}",
    response_model=PreparationPackageResponse,
)
async def get_preparation_package(
    case_id: str,
    package_id: str,
    response: Response,
    principal: Annotated[Principal, Depends(get_principal)],
    package_service: Annotated[PackageService, Depends(get_package_service)],
) -> PreparationPackageResponse:
    """Retrieve a specific preparation package by ID."""
    package = package_service.get_package(
        user_id=principal.user_id,
        package_id=package_id,
    )
    if package is None or package.case_id != case_id:
        raise EntityNotFoundError("PreparationPackage", package_id)

    set_etag_header(response, "package", package.package_id, package.version)
    return _to_package_response(package)


def _to_execution_plan_response(plan: ExternalExecutionPlan) -> ExternalExecutionPlanResponse:
    dest_type_val = (
        plan.destination.destination_type.value
        if hasattr(plan.destination.destination_type, "value")
        else str(plan.destination.destination_type)
    )
    dest_schema = ExecutionDestinationSchema(
        destination_type=dest_type_val,
        destination_provenance=plan.destination.destination_provenance,
        canonical_url=plan.destination.canonical_url,
        organization=plan.destination.organization,
        channel_metadata=plan.destination.channel_metadata,
        is_exact_destination=plan.destination.is_exact_destination,
        normalized_origin=plan.destination.normalized_origin,
        normalized_hostname=plan.destination.normalized_hostname,
    )
    target_schema_dto = None
    if plan.target_schema:
        control_dto = None
        if plan.target_schema.final_submit_control:
            ctrl = plan.target_schema.final_submit_control
            control_dto = TargetSubmitControlSpecSchema(
                control_type=ctrl.control_type,
                semantic_target=ctrl.semantic_target,
                selector=ctrl.selector,
                control_id=ctrl.control_id,
                control_provenance=ctrl.control_provenance,
                method=ctrl.method,
                requires_double_confirmation=ctrl.requires_double_confirmation,
            )
        target_schema_dto = TargetInteractionSchemaSchema(
            schema_id=plan.target_schema.schema_id,
            destination_type=plan.target_schema.destination_type,
            target_schema_provenance=plan.target_schema.target_schema_provenance,
            fields=[
                TargetSchemaFieldSchema(
                    target_field_id=f.target_field_id,
                    label=f.label,
                    field_type=f.field_type,
                    required=f.required,
                    allowed_options=f.allowed_options,
                )
                for f in plan.target_schema.fields
            ],
            upload_slots=[
                TargetSchemaUploadSlotSchema(
                    slot_id=s.slot_id,
                    role=s.role,
                    label=s.label,
                    required=s.required,
                    accepted_mime_types=s.accepted_mime_types,
                )
                for s in plan.target_schema.upload_slots
            ],
            screening_questions=[
                TargetSchemaQuestionSchema(
                    question_id=q.question_id,
                    question_text=q.question_text,
                    required=q.required,
                    answer_type=q.answer_type,
                )
                for q in plan.target_schema.screening_questions
            ],
            final_submit_control=control_dto,
            schema_hash=plan.target_schema.schema_hash,
        )

    submit_control_dto = None
    if plan.final_submit_control:
        ctrl = plan.final_submit_control
        submit_control_dto = TargetSubmitControlSpecSchema(
            control_type=ctrl.control_type,
            semantic_target=ctrl.semantic_target,
            selector=ctrl.selector,
            control_id=ctrl.control_id,
            control_provenance=ctrl.control_provenance,
            method=ctrl.method,
            requires_double_confirmation=ctrl.requires_double_confirmation,
        )

    findings_dtos = [
        ExecutionPlanFindingResponse(
            check_name=f.check_name,
            passed=f.passed,
            severity=f.severity,
            code=f.code,
            message=f.message,
            details=f.details,
        )
        for f in plan.findings
    ]

    email_manifest_dto = None
    if plan.email_manifest:
        m = plan.email_manifest
        ev_dto = None
        if m.acceptance_evidence:
            ev = m.acceptance_evidence
            ev_dto = EmailAcceptanceEvidenceResponse(
                evidence_source=ev.evidence_source,
                source_uri=ev.source_uri,
                instructions_text=ev.instructions_text,
                verified_recipient_email=ev.verified_recipient_email,
                employer_domain=ev.employer_domain,
                verified_by=ev.verified_by,
                verified_at=ev.verified_at,
                evidence_snapshot_hash=ev.evidence_snapshot_hash,
                evidence_digest=ev.evidence_digest,
            )
        email_manifest_dto = EmailPayloadManifestResponse(
            to_email=m.to_email,
            from_email=m.from_email,
            subject=m.subject,
            body_text=m.body_text,
            message_id=m.message_id,
            date_header=getattr(m, "date_rfc2822", getattr(m, "date_header", "")),
            sealed_payload_hash=m.sealed_payload_hash,
            attachments=[
                EmailAttachmentManifestResponse(
                    asset_id=a.asset_id,
                    filename=a.filename,
                    mime_type=a.mime_type,
                    content_sha256=a.content_sha256,
                    byte_size=a.byte_size,
                )
                for a in m.attachments
            ],
            acceptance_evidence=ev_dto,
        )

    return ExternalExecutionPlanResponse(
        plan_id=plan.plan_id,
        case_id=plan.case_id,
        opportunity_id=plan.opportunity_id,
        package_id=plan.package_id,
        package_fingerprint=plan.package_fingerprint,
        user_id=plan.user_id,
        status=plan.status.value,
        destination_provenance=plan.destination_provenance or plan.destination.destination_provenance,
        destination=dest_schema,
        target_schema=target_schema_dto,
        target_schema_hash=plan.target_schema_hash or (plan.target_schema.schema_hash if plan.target_schema else ""),
        field_mappings=[
            ExecutionFieldMappingResponse(
                target_field_id=m.target_field_id,
                field_name=m.field_name,
                field_value=m.field_value,
                source_fact_id=m.source_fact_id,
                source_fact_version=m.source_fact_version,
                verification_status=m.verification_status,
                required=m.required,
                allowed_purposes=list(m.allowed_purposes),
            )
            for m in plan.field_mappings
        ],
        upload_mappings=[
            ExecutionUploadAssetResponse(
                asset_id=u.asset_id,
                role=u.role,
                source_artifact_id=u.source_artifact_id,
                source_artifact_version=u.source_artifact_version,
                filename=u.filename,
                mime_type=u.mime_type,
                content_sha256=u.content_sha256,
                byte_size=u.byte_size,
                materialized=u.materialized,
            )
            for u in plan.upload_mappings
        ],
        screening_responses=[
            ExecutionScreeningResponseDTO(
                question_id=s.question_id,
                question=s.question,
                answer=s.answer,
                source_artifact_id=s.source_artifact_id,
                source_artifact_version=s.source_artifact_version,
                source_payload_hash=s.source_payload_hash,
                grounding_claim_ids=s.grounding_claim_ids,
            )
            for s in plan.screening_responses
        ],
        final_submit_control=submit_control_dto,
        email_manifest=email_manifest_dto,
        findings=findings_dtos,
        planning_findings=findings_dtos,
        execution_input_hash=plan.execution_input_hash,
        execution_plan_fingerprint=plan.execution_plan_fingerprint,
        schema_version=plan.schema_version,
        planner_version=plan.planner_version,
        created_at=to_iso_utc(plan.created_at),
        updated_at=to_iso_utc(plan.updated_at),
        version=plan.version,
        is_stale=plan.is_stale,
        staleness_reasons=list(plan.staleness_reasons),
    )


@router.post(
    "/api/v1/cases/{case_id}/execution/plan",
    response_model=ExternalExecutionPlanResponse,
    status_code=status.HTTP_200_OK,
)
async def create_execution_plan(
    case_id: str,
    req: CreateExecutionPlanRequest,
    request: Request,
    response: Response,
    principal: Annotated[Principal, Depends(get_principal)],
    case_service: Annotated[CaseService, Depends(get_case_service)],
    execution_plan_service: Annotated[ExecutionPlanService, Depends(get_execution_plan_service)],
) -> ExternalExecutionPlanResponse:
    """Compile and seal an exact ExternalExecutionPlan for a case.

    Concurrency & Idempotency:
    - Exact replay resolution occurs before If-Match check.
    - If a plan with matching content fingerprint already exists, it is returned idempotently.
    - If compiling a new plan, If-Match header on Case is strictly validated.
    """
    dest_override = None
    if req.destination_override:
        dest_override = ExecutionDestination(
            destination_type=req.destination_override.destination_type,
            destination_provenance=req.destination_override.destination_provenance,
            canonical_url=req.destination_override.canonical_url,
            organization=req.destination_override.organization,
            channel_metadata=req.destination_override.channel_metadata,
            is_exact_destination=req.destination_override.is_exact_destination,
        )

    target_schema = None
    if req.target_schema:
        control = None
        if req.target_schema.final_submit_control:
            c = req.target_schema.final_submit_control
            control = TargetSubmitControlSpec(
                control_type=c.control_type,
                semantic_target=c.semantic_target,
                selector=c.selector,
                control_id=c.control_id,
                control_provenance=c.control_provenance,
                method=c.method,
                requires_double_confirmation=c.requires_double_confirmation,
            )
        target_schema = TargetInteractionSchema(
            schema_id=req.target_schema.schema_id,
            destination_type=req.target_schema.destination_type,
            target_schema_provenance=req.target_schema.target_schema_provenance,
            fields=[
                TargetSchemaField(
                    target_field_id=f.target_field_id,
                    label=f.label,
                    field_type=f.field_type,
                    required=f.required,
                    allowed_options=f.allowed_options,
                )
                for f in req.target_schema.fields
            ],
            upload_slots=[
                TargetSchemaUploadSlot(
                    slot_id=s.slot_id,
                    role=s.role,
                    label=s.label,
                    required=s.required,
                    accepted_mime_types=s.accepted_mime_types,
                )
                for s in req.target_schema.upload_slots
            ],
            screening_questions=[
                TargetSchemaQuestion(
                    question_id=q.question_id,
                    question_text=q.question_text,
                    required=q.required,
                    answer_type=q.answer_type,
                    allowed_options=q.allowed_options,
                )
                for q in req.target_schema.screening_questions
            ],
            final_submit_control=control,
            schema_hash=req.target_schema.schema_hash,
        )

    submit_control = None
    if req.final_submit_control:
        c = req.final_submit_control
        submit_control = TargetSubmitControlSpec(
            control_type=c.control_type,
            semantic_target=c.semantic_target,
            selector=c.selector,
            control_id=c.control_id,
            control_provenance=c.control_provenance,
            method=c.method,
            requires_double_confirmation=c.requires_double_confirmation,
        )

    field_bindings = None
    if req.field_bindings:
        field_bindings = [
            TargetFieldBinding(
                target_field_id=b.target_field_id,
                source_fact_id=b.source_fact_id,
            )
            for b in req.field_bindings
        ]

    # 1. Exact replay resolution before If-Match check (Amendment 15)
    existing_match = execution_plan_service.find_matching_plan(
        user_id=principal.user_id,
        case_id=case_id,
        package_id=req.package_id,
        destination_override=dest_override,
        target_schema=target_schema,
        field_bindings=field_bindings,
        channel_metadata=req.channel_metadata,
        final_submit_control=submit_control,
    )
    if existing_match is not None:
        plan, case = existing_match
        set_etag_header(response, "case", case.case_id, case.version)
        set_etag_header(response, "execution_plan", plan.plan_id, plan.version)
        return _to_execution_plan_response(plan)

    # 2. Case presence & concurrency validation
    case = case_service.get_case_for_user(principal.user_id, case_id)
    if case is None:
        raise EntityNotFoundError("Case", case_id)

    check_if_match(request, "case", case.case_id, case.version)

    # 3. Create and persist new plan
    plan = execution_plan_service.create_execution_plan(
        user_id=principal.user_id,
        case_id=case_id,
        package_id=req.package_id,
        destination_override=dest_override,
        target_schema=target_schema,
        field_bindings=field_bindings,
        channel_metadata=req.channel_metadata,
        final_submit_control=submit_control,
    )
    set_etag_header(response, "case", case.case_id, case.version)
    set_etag_header(response, "execution_plan", plan.plan_id, plan.version)
    return _to_execution_plan_response(plan)


@router.get(
    "/api/v1/cases/{case_id}/execution/plan/latest",
    response_model=ExternalExecutionPlanResponse,
)
async def get_latest_execution_plan(
    case_id: str,
    response: Response,
    principal: Annotated[Principal, Depends(get_principal)],
    execution_plan_service: Annotated[ExecutionPlanService, Depends(get_execution_plan_service)],
) -> ExternalExecutionPlanResponse:
    """Retrieve the latest execution plan for a case."""
    plan = execution_plan_service.get_latest_execution_plan(
        user_id=principal.user_id,
        case_id=case_id,
    )
    if plan is None:
        raise EntityNotFoundError("ExternalExecutionPlan", f"case:{case_id}")

    set_etag_header(response, "execution_plan", plan.plan_id, plan.version)
    return _to_execution_plan_response(plan)


@router.get(
    "/api/v1/cases/{case_id}/execution/plan/{plan_id}",
    response_model=ExternalExecutionPlanResponse,
)
async def get_execution_plan(
    case_id: str,
    plan_id: str,
    response: Response,
    principal: Annotated[Principal, Depends(get_principal)],
    execution_plan_service: Annotated[ExecutionPlanService, Depends(get_execution_plan_service)],
) -> ExternalExecutionPlanResponse:
    """Retrieve a specific execution plan by plan_id."""
    plan = execution_plan_service.get_execution_plan(
        user_id=principal.user_id,
        case_id=case_id,
        plan_id=plan_id,
    )
    if plan is None:
        raise EntityNotFoundError("ExternalExecutionPlan", plan_id)

    set_etag_header(response, "execution_plan", plan.plan_id, plan.version)
    return _to_execution_plan_response(plan)


@router.get(
    "/api/v1/cases/{case_id}/execution/plans",
    response_model=ExternalExecutionPlanListResponse,
)
async def list_execution_plans(
    case_id: str,
    principal: Annotated[Principal, Depends(get_principal)],
    execution_plan_service: Annotated[ExecutionPlanService, Depends(get_execution_plan_service)],
) -> ExternalExecutionPlanListResponse:
    """List all execution plans for a case."""
    plans = execution_plan_service.list_execution_plans(
        user_id=principal.user_id,
        case_id=case_id,
    )
    return ExternalExecutionPlanListResponse(
        plans=[_to_execution_plan_response(p) for p in plans],
        total=len(plans),
    )


@router.post(
    "/api/v1/cases/{case_id}/execution/plan/{plan_id}/request-authorization",
    response_model=ApprovalDecisionResponse,
    status_code=status.HTTP_200_OK,
)
async def request_plan_authorization(
    case_id: str,
    plan_id: str,
    request: Request,
    response: Response,
    principal: Annotated[Principal, Depends(get_principal)],
    case_service: Annotated[CaseService, Depends(get_case_service)],
    execution_plan_service: Annotated[ExecutionPlanService, Depends(get_execution_plan_service)],
) -> ApprovalDecisionResponse:
    """Request human authorization for an exact ExternalExecutionPlan under ETag control."""
    # 1. Exact replay / idempotent retry resolution before If-Match check
    existing_auth = execution_plan_service.find_existing_transmission_authorization(
        user_id=principal.user_id,
        case_id=case_id,
        plan_id=plan_id,
    )
    if existing_auth is not None:
        action, approval = existing_auth
        set_etag_header(response, "approval", approval.approval_id, approval.version)
        return ApprovalDecisionResponse(
            approval=_to_approval_response(approval),
            action=_to_action_response(action),
        )

    # 2. Case presence & concurrency validation on genuinely new requests
    case = case_service.get_case_for_user(principal.user_id, case_id)
    if case is None:
        raise EntityNotFoundError("Case", case_id)

    check_if_match(request, "case", case.case_id, case.version)

    # 3. Genuinely new authorization request
    action, approval = execution_plan_service.request_transmission_authorization(
        user_id=principal.user_id,
        case_id=case_id,
        plan_id=plan_id,
    )
    set_etag_header(response, "approval", approval.approval_id, approval.version)
    return ApprovalDecisionResponse(
        approval=_to_approval_response(approval),
        action=_to_action_response(action),
    )


@router.post(
    "/api/v1/cases/{case_id}/execution/actions/{action_id}/execute",
    response_model=ExecuteActionResponse,
)
async def execute_case_action(
    case_id: str,
    action_id: str,
    request: Request,
    response: Response,
    principal: Annotated[Principal, Depends(get_principal)],
    case_service: Annotated[CaseService, Depends(get_case_service)],
    action_service: Annotated[ActionService, Depends(get_action_service)],
    exec_service: Annotated[GovernedExecutionService, Depends(get_governed_execution_service)],
    storage: Annotated[SQLiteCaseworkerStorage, Depends(get_storage)],
    config: Annotated[APIConfig, Depends(get_config)],
    req: ExecuteActionRequest | None = None,
) -> ExecuteActionResponse:
    """Execute an approved transmission action for a case under governed execution pipeline."""
    case = case_service.get_case_for_user(principal.user_id, case_id)
    if case is None:
        raise EntityNotFoundError("Case", case_id)
    action = action_service.get_action_for_user(principal.user_id, action_id)
    if action is None or action.case_id != case_id:
        raise EntityNotFoundError("Action", action_id)
    return await execute_action(
        action_id=action_id,
        request=request,
        response=response,
        principal=principal,
        action_service=action_service,
        exec_service=exec_service,
        storage=storage,
        config=config,
        req=req,
    )


@router.get(
    "/api/v1/cases/{case_id}/execution/receipt",
    response_model=ExecutionReceiptResponse,
)
async def get_execution_receipt(
    case_id: str,
    response: Response,
    principal: Annotated[Principal, Depends(get_principal)],
    case_service: Annotated[CaseService, Depends(get_case_service)],
    storage: Annotated[SQLiteCaseworkerStorage, Depends(get_storage)],
) -> ExecutionReceiptResponse:
    """Get the latest verified execution receipt for a case."""
    case = case_service.get_case_for_user(principal.user_id, case_id)
    if case is None:
        raise EntityNotFoundError("Case", case_id)
    with storage.unit_of_work() as uow:
        receipt = uow.execution_receipts.get_by_case_id(case_id)
        if receipt is None:
            raise EntityNotFoundError("ExecutionReceipt", case_id)
        set_etag_header(response, "execution_receipt", receipt.receipt_id, receipt.version)
        return _to_receipt_response(receipt)


@router.get(
    "/api/v1/cases/{case_id}/execution/attempts",
    response_model=ExecutionAttemptListResponse,
)
async def list_execution_attempts(
    case_id: str,
    principal: Annotated[Principal, Depends(get_principal)],
    case_service: Annotated[CaseService, Depends(get_case_service)],
    storage: Annotated[SQLiteCaseworkerStorage, Depends(get_storage)],
) -> ExecutionAttemptListResponse:
    """List all execution attempts for a case in chronological order."""
    case = case_service.get_case_for_user(principal.user_id, case_id)
    if case is None:
        raise EntityNotFoundError("Case", case_id)
    with storage.unit_of_work() as uow:
        attempts = uow.execution_attempts.list_by_case(case_id)
        return ExecutionAttemptListResponse(
            attempts=[_to_attempt_response(att) for att in attempts],
            total=len(attempts),
        )
