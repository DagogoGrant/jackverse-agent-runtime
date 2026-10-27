"""FastAPI dependency injection providers for storage, identity, services, and pagination."""

from __future__ import annotations

from typing import Annotated
from fastapi import Depends, HTTPException, Query, Request, status

from caseworker.adapters.crypto.token_cipher import TokenCipher
from caseworker.adapters.gmail_provider import DatabaseOAuthCredentialProvider
from caseworker.api.config import APIConfig
from caseworker.api.identity import (
    DevelopmentIdentityProvider,
    FailClosedIdentityProvider,
    Principal,
)
from caseworker.persistence.sqlite import SQLiteCaseworkerStorage
from caseworker.services.action_service import ActionService
from caseworker.services.approval_service import ApprovalService
from caseworker.services.artifact_service import ArtifactPreparationService
from caseworker.services.execution_plan_service import ExecutionPlanService
from caseworker.services.package_service import PackageService
from caseworker.services.case_service import CaseService
from caseworker.services.claim_service import ClaimLedgerService as ClaimService
from caseworker.services.governed_execution_service import GovernedExecutionService
from caseworker.services.mission_service import MissionService
from caseworker.services.opportunity_service import OpportunityService
from caseworker.services.vault_service import ContextVaultService


def get_config(request: Request) -> APIConfig:
    """Retrieve API configuration from application state."""
    config = getattr(request.app.state, "config", None)
    if config is None:
        config = APIConfig()
    return config


def get_storage(request: Request) -> SQLiteCaseworkerStorage:
    """Retrieve SQLiteCaseworkerStorage instance from application state."""
    storage = getattr(request.app.state, "storage", None)
    if storage is None:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Storage is not initialized on application state.",
        )
    return storage


async def get_principal(
    request: Request,
    config: Annotated[APIConfig, Depends(get_config)],
) -> Principal:
    """Extract authenticated Principal using fail-closed development or production provider."""
    provider = DevelopmentIdentityProvider(config.dev_auth, config.operator_secret)
    return await provider.get_principal(request)


def get_mission_service(
    storage: Annotated[SQLiteCaseworkerStorage, Depends(get_storage)],
) -> MissionService:
    return MissionService(storage)


def get_case_service(
    storage: Annotated[SQLiteCaseworkerStorage, Depends(get_storage)],
) -> CaseService:
    return CaseService(storage)


def get_opportunity_service(
    storage: Annotated[SQLiteCaseworkerStorage, Depends(get_storage)],
) -> OpportunityService:
    return OpportunityService(storage)


def get_vault_service(
    storage: Annotated[SQLiteCaseworkerStorage, Depends(get_storage)],
) -> ContextVaultService:
    return ContextVaultService(storage)


def get_claim_service(
    storage: Annotated[SQLiteCaseworkerStorage, Depends(get_storage)],
) -> ClaimService:
    return ClaimService(storage)


def get_action_service(
    storage: Annotated[SQLiteCaseworkerStorage, Depends(get_storage)],
) -> ActionService:
    return ActionService(storage)


def get_approval_service(
    storage: Annotated[SQLiteCaseworkerStorage, Depends(get_storage)],
) -> ApprovalService:
    return ApprovalService(storage)


def get_artifact_service(
    storage: Annotated[SQLiteCaseworkerStorage, Depends(get_storage)],
) -> ArtifactPreparationService:
    return ArtifactPreparationService(storage)

def get_package_service(
    storage: Annotated[SQLiteCaseworkerStorage, Depends(get_storage)],
) -> PackageService:
    return PackageService(storage)


def get_execution_plan_service(
    storage: Annotated[SQLiteCaseworkerStorage, Depends(get_storage)],
) -> ExecutionPlanService:
    return ExecutionPlanService(storage)


def get_governed_execution_service(
    storage: Annotated[SQLiteCaseworkerStorage, Depends(get_storage)],
    config: Annotated[APIConfig, Depends(get_config)],
) -> GovernedExecutionService:
    credential_provider = None
    if config.encryption_key and len(config.encryption_key.strip()) >= 32:
        cipher = TokenCipher(
            primary_key=config.encryption_key,
            rotation_fallback_keys=config.encryption_fallback_keys,
        )
        credential_provider = DatabaseOAuthCredentialProvider(
            storage=storage,
            cipher=cipher,
            client_id=config.gmail_client_id,
            client_secret=config.gmail_client_secret,
        )

    return GovernedExecutionService(
        storage=storage,
        credential_provider=credential_provider,
        allow_simulation=config.simulation_enabled,
        real_execution_enabled=config.real_execution_enabled,
        gmail_execution_enabled=config.gmail_execution_enabled,
        dev_auth=config.dev_auth,
    )



class PaginationParams:
    """Offset-based pagination parameters with enforced limits (max 100)."""

    def __init__(
        self,
        limit: Annotated[int, Query(ge=1, le=100, description="Items per page (max 100)")] = 50,
        offset: Annotated[int, Query(ge=0, description="Pagination offset")] = 0,
    ) -> None:
        self.limit = limit
        self.offset = offset


class CursorPaginationParams:
    """Cursor-based event pagination parameters using monotonic position cursors."""

    def __init__(
        self,
        limit: Annotated[int, Query(ge=1, le=100, description="Items per page (max 100)")] = 50,
        after_position: Annotated[int | None, Query(ge=0, description="Monotonic event position cursor")] = None,
    ) -> None:
        self.limit = limit
        self.after_position = after_position
