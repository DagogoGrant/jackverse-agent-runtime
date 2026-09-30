"""Context Vault API endpoints for sources, facts, and policy-gated access packaging."""

from __future__ import annotations

from typing import Annotated
from fastapi import APIRouter, Depends, Request, Response, status

from caseworker.api.dependencies import (
    PaginationParams,
    get_principal,
    get_vault_service,
)
from caseworker.api.etag import check_if_match, set_etag_header
from caseworker.api.identity import Principal
from caseworker.api.schemas.common import PaginatedResponse
from caseworker.api.schemas.context import (
    ContextPackageResponse,
    CreatePackageRequest,
    FactResponse,
    RecordFactRequest,
    RegisterSourceRequest,
    RejectFactRequest,
    SafeSourceResponse,
    SupersedeFactRequest,
    VerifyFactRequest,
)
from caseworker.domain.context import ContextFact
from caseworker.domain.context_package import ContextPackage
from caseworker.domain.errors import EntityNotFoundError
from caseworker.domain.source import ContextSource
from caseworker.domain.types import to_iso_utc
from caseworker.services.vault_service import ContextVaultService

router = APIRouter(prefix="/api/v1/context", tags=["context"])


def _to_safe_source_response(s: ContextSource) -> SafeSourceResponse:
    return SafeSourceResponse(
        source_id=s.source_id,
        user_id=s.user_id,
        title=s.title,
        source_type=s.source_type.value if hasattr(s.source_type, "value") else str(s.source_type),
        sensitivity=s.sensitivity.value if hasattr(s.sensitivity, "value") else str(s.sensitivity),
        created_at=to_iso_utc(s.created_at),
        version=s.version,
    )


def _to_fact_response(f: ContextFact) -> FactResponse:
    return FactResponse(
        fact_id=f.fact_id,
        user_id=f.user_id,
        namespace=f.namespace,
        key=f.key,
        value=f.value,
        source_id=f.source_id,
        source_type=f.source_type.value if hasattr(f.source_type, "value") else str(f.source_type),
        source_reference=f.source_reference,
        confidence=f.confidence,
        verification_status=(
            f.verification_status.value
            if hasattr(f.verification_status, "value")
            else str(f.verification_status)
        ),
        rejection_reason=f.rejection_reason,
        sensitivity=f.sensitivity.value if hasattr(f.sensitivity, "value") else str(f.sensitivity),
        allowed_purposes=list(f.allowed_purposes),
        created_at=to_iso_utc(f.created_at),
        updated_at=to_iso_utc(f.updated_at),
        expires_at=to_iso_utc(f.expires_at) if f.expires_at else None,
        superseded_by_fact_id=f.superseded_by_fact_id,
        version=f.version,
    )


def _to_package_response(p: ContextPackage) -> ContextPackageResponse:
    return ContextPackageResponse(
        package_id=p.package_id,
        user_id=p.user_id,
        purpose=p.purpose,
        fact_ids=[f.fact_id for f in p.facts],
        facts=[_to_fact_response(f) for f in p.facts],
        generated_at=to_iso_utc(p.generated_at),
        expires_at=to_iso_utc(p.expires_at) if p.expires_at else None,
        fingerprint=p.fingerprint,
    )


# -----------------------------------------------------------------------------
# Sources
# -----------------------------------------------------------------------------

@router.post("/sources", response_model=SafeSourceResponse, status_code=status.HTTP_201_CREATED)
async def register_source(
    req: RegisterSourceRequest,
    principal: Annotated[Principal, Depends(get_principal)],
    service: Annotated[ContextVaultService, Depends(get_vault_service)],
    response: Response,
) -> SafeSourceResponse:
    """Register a context source, returning a sanitized response omitting sensitive credentials."""
    source = service.register_source(
        user_id=principal.user_id,
        title=req.title,
        source_type=req.source_type,
        source_reference=req.source_reference,
        sensitivity=req.sensitivity,
        metadata=req.metadata,
    )
    set_etag_header(response, "source", source.source_id, source.version)
    return _to_safe_source_response(source)


@router.get("/sources", response_model=list[SafeSourceResponse])
async def list_sources(
    principal: Annotated[Principal, Depends(get_principal)],
    service: Annotated[ContextVaultService, Depends(get_vault_service)],
) -> list[SafeSourceResponse]:
    """List context sources registered for the authenticated user."""
    sources = service.list_sources(principal.user_id)
    return [_to_safe_source_response(s) for s in sources]


@router.get("/sources/{source_id}", response_model=SafeSourceResponse)
async def get_source(
    source_id: str,
    principal: Annotated[Principal, Depends(get_principal)],
    service: Annotated[ContextVaultService, Depends(get_vault_service)],
    response: Response,
) -> SafeSourceResponse:
    """Retrieve a context source by ID, asserting user ownership (404 on unowned sources)."""
    source = service.get_source_for_user(principal.user_id, source_id)
    if source is None:
        raise EntityNotFoundError("ContextSource", source_id)
    set_etag_header(response, "source", source.source_id, source.version)
    return _to_safe_source_response(source)


# -----------------------------------------------------------------------------
# Facts
# -----------------------------------------------------------------------------

@router.post("/facts", response_model=FactResponse, status_code=status.HTTP_201_CREATED)
async def record_fact(
    req: RecordFactRequest,
    principal: Annotated[Principal, Depends(get_principal)],
    service: Annotated[ContextVaultService, Depends(get_vault_service)],
    response: Response,
) -> FactResponse:
    """Record a factual context item bound to the authenticated user."""
    fact = service.record_fact(
        user_id=principal.user_id,
        namespace=req.namespace,
        key=req.key,
        value=req.value,
        source_id=req.source_id,
        source_type=req.source_type,
        source_reference=req.source_reference,
        confidence=req.confidence,
        sensitivity=req.sensitivity,
        allowed_purposes=req.allowed_purposes,
        expires_at=req.expires_at,
    )
    set_etag_header(response, "fact", fact.fact_id, fact.version)
    return _to_fact_response(fact)


@router.get("/facts", response_model=PaginatedResponse[FactResponse])
async def list_facts(
    principal: Annotated[Principal, Depends(get_principal)],
    service: Annotated[ContextVaultService, Depends(get_vault_service)],
    pagination: Annotated[PaginationParams, Depends()],
    namespace: str | None = None,
) -> PaginatedResponse[FactResponse]:
    """List active context facts belonging to the authenticated user."""
    all_facts = service.list_active_facts(principal.user_id, namespace=namespace)
    total = len(all_facts)
    page_items = all_facts[pagination.offset : pagination.offset + pagination.limit]
    return PaginatedResponse(
        items=[_to_fact_response(f) for f in page_items],
        total=total,
        limit=pagination.limit,
        offset=pagination.offset,
    )


@router.get("/facts/{fact_id}", response_model=FactResponse)
async def get_fact(
    fact_id: str,
    principal: Annotated[Principal, Depends(get_principal)],
    service: Annotated[ContextVaultService, Depends(get_vault_service)],
    response: Response,
) -> FactResponse:
    """Retrieve a context fact by ID, asserting user ownership (404 on unowned facts)."""
    fact = service.get_fact_for_user(principal.user_id, fact_id)
    if fact is None:
        raise EntityNotFoundError("ContextFact", fact_id)
    set_etag_header(response, "fact", fact.fact_id, fact.version)
    return _to_fact_response(fact)


@router.post("/facts/{fact_id}/verify", response_model=FactResponse)
async def verify_fact(
    fact_id: str,
    req: VerifyFactRequest,
    request: Request,
    response: Response,
    principal: Annotated[Principal, Depends(get_principal)],
    service: Annotated[ContextVaultService, Depends(get_vault_service)],
) -> FactResponse:
    """Verify a context fact with ETag precondition enforcement."""
    existing = service.get_fact_for_user(principal.user_id, fact_id)
    if existing is None:
        raise EntityNotFoundError("ContextFact", fact_id)

    check_if_match(request, "fact", existing.fact_id, existing.version)

    updated = service.verify_fact_for_user(
        user_id=principal.user_id,
        fact_id=fact_id,
        status=req.status,
    )
    set_etag_header(response, "fact", updated.fact_id, updated.version)
    return _to_fact_response(updated)


@router.post("/facts/{fact_id}/reject", response_model=FactResponse)
async def reject_fact(
    fact_id: str,
    req: RejectFactRequest,
    request: Request,
    response: Response,
    principal: Annotated[Principal, Depends(get_principal)],
    service: Annotated[ContextVaultService, Depends(get_vault_service)],
) -> FactResponse:
    """Reject a context fact with ETag precondition enforcement."""
    existing = service.get_fact_for_user(principal.user_id, fact_id)
    if existing is None:
        raise EntityNotFoundError("ContextFact", fact_id)

    check_if_match(request, "fact", existing.fact_id, existing.version)

    updated = service.reject_fact_for_user(
        user_id=principal.user_id,
        fact_id=fact_id,
        reason=req.reason,
    )
    set_etag_header(response, "fact", updated.fact_id, updated.version)
    return _to_fact_response(updated)


@router.post("/facts/{fact_id}/supersede", response_model=FactResponse)
async def supersede_fact(
    fact_id: str,
    req: SupersedeFactRequest,
    request: Request,
    response: Response,
    principal: Annotated[Principal, Depends(get_principal)],
    service: Annotated[ContextVaultService, Depends(get_vault_service)],
) -> FactResponse:
    """Supersede a context fact with a newer version under ETag precondition enforcement."""
    existing = service.get_fact_for_user(principal.user_id, fact_id)
    if existing is None:
        raise EntityNotFoundError("ContextFact", fact_id)

    check_if_match(request, "fact", existing.fact_id, existing.version)

    _old, new_fact = service.supersede_fact_for_user(
        user_id=principal.user_id,
        old_fact_id=fact_id,
        new_value=req.new_value,
        new_source_id=req.new_source_id,
        new_confidence=req.new_confidence,
        expires_at=req.expires_at,
        reason=req.reason,
    )
    set_etag_header(response, "fact", new_fact.fact_id, new_fact.version)
    return _to_fact_response(new_fact)


# -----------------------------------------------------------------------------
# Context Packages
# -----------------------------------------------------------------------------

@router.post("/packages", response_model=ContextPackageResponse, status_code=status.HTTP_201_CREATED)
async def create_context_package(
    req: CreatePackageRequest,
    principal: Annotated[Principal, Depends(get_principal)],
    service: Annotated[ContextVaultService, Depends(get_vault_service)],
    response: Response,
) -> ContextPackageResponse:
    """Assemble a purpose-scoped, policy-gated context package with immutable access auditing."""
    pkg = service.create_context_package(
        user_id=principal.user_id,
        purpose=req.purpose,
        namespaces=req.namespaces,
        require_verified=req.require_verified,
        expires_at=req.expires_at,
    )
    set_etag_header(response, "package", pkg.package_id, 1)
    return _to_package_response(pkg)
