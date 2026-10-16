"""Pydantic schemas for preparation package verification, snapshotting, and review."""

from __future__ import annotations

from typing import Any
from pydantic import BaseModel, ConfigDict, Field

from caseworker.api.schemas.action import ActionResponse
from caseworker.api.schemas.approval import ApprovalResponse


class VerifyPackageRequest(BaseModel):
    """Request payload for verifying and freezing a candidate preparation package."""

    model_config = ConfigDict(extra="forbid")

    artifact_set_id: str = Field(
        ...,
        min_length=1,
        max_length=128,
        description="Unique ID of the candidate artifact set to verify and freeze.",
    )


class VerificationFindingResponse(BaseModel):
    """Detailed result of an individual package verification check."""

    check_name: str
    passed: bool
    severity: str
    message: str
    details: dict[str, Any] = Field(default_factory=dict)


class ArtifactPackageSnapshotResponse(BaseModel):
    """Immutable snapshot of an artifact frozen inside a preparation package."""

    artifact_id: str
    artifact_version: int
    material_type: str
    rendered_payload_hash: str
    grounding_snapshot_hash: str
    publication_role: str


class SafeClaimSnapshotResponse(BaseModel):
    """Privacy-safe snapshot of a claim grounding an artifact inside a preparation package."""

    claim_id: str
    claim_version: int
    claim_status: str
    support_snapshot_hash: str | None = None
    assertion_hash: str | None = None


class PreparationPackageResponse(BaseModel):
    """Verified and frozen preparation package DTO."""

    package_id: str
    case_id: str
    artifact_set_id: str
    source_report_id: str
    user_id: str
    status: str
    artifact_ids: list[str]
    artifact_versions: dict[str, int]
    artifact_snapshots: list[ArtifactPackageSnapshotResponse]
    claim_snapshots: list[SafeClaimSnapshotResponse]
    rendered_payload_hashes: dict[str, str]
    verification_status: str
    verification_findings: list[VerificationFindingResponse]
    frozen_at: str
    package_fingerprint: str
    schema_version: str
    verifier_version: str
    created_at: str
    updated_at: str
    reason: str | None = None
    version: int


class VerifyPackageResponse(BaseModel):
    """Response returned upon successful package verification and review preparation."""

    package: PreparationPackageResponse
    action: ActionResponse
    approval: ApprovalResponse
    findings: list[VerificationFindingResponse]
    case_id: str
    case_status: str
    case_version: int


class PreparationPackageListResponse(BaseModel):
    """List of preparation packages for a case."""

    packages: list[PreparationPackageResponse]
    total: int
