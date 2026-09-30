"""Pydantic schemas for Context Vault and Provenance Sources API."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from pydantic import BaseModel, ConfigDict, Field
from caseworker.domain.enums import SensitivityLevel, SourceType, VerificationStatus


class RegisterSourceRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(..., min_length=1, max_length=255, description="Human-readable title.")
    source_type: SourceType = Field(SourceType.USER_INPUT, description="Classification of context source.")
    source_reference: str = Field("", max_length=2048, description="Source URI or reference.")
    sensitivity: SensitivityLevel = Field(SensitivityLevel.PERSONAL, description="Default source sensitivity.")
    metadata: dict[str, Any] = Field(default_factory=dict, description="Metadata dictionary.")


class SafeSourceResponse(BaseModel):
    """Safe source representation omitting private tokens, credentials, and raw metadata."""

    source_id: str
    user_id: str
    title: str
    source_type: str
    sensitivity: str
    created_at: str
    version: int


class RecordFactRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    namespace: str = Field(..., min_length=1, max_length=255, description="Hierarchical namespace.")
    key: str = Field(..., min_length=1, max_length=255, description="Fact key.")
    value: Any = Field(..., description="Fact payload value.")
    source_id: str | None = Field(None, description="Optional parent source ID.")
    source_type: SourceType = Field(SourceType.USER_INPUT, description="Provenance source type.")
    source_reference: str = Field("", max_length=2048, description="Provenance reference.")
    confidence: float = Field(1.0, ge=0.0, le=1.0, description="Confidence score.")
    sensitivity: SensitivityLevel = Field(SensitivityLevel.PERSONAL, description="Sensitivity level.")
    allowed_purposes: list[str] = Field(default_factory=list, description="Allowed access purposes.")
    expires_at: datetime | None = Field(None, description="Expiration timestamp.")


class VerifyFactRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: VerificationStatus = Field(VerificationStatus.USER_VERIFIED, description="Verification status.")


class RejectFactRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str | None = Field(None, max_length=1000, description="Reason for rejection.")


class SupersedeFactRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    new_value: Any = Field(..., description="Replacement value.")
    new_source_id: str | None = Field(None, description="New context source ID if different.")
    new_confidence: float = Field(1.0, ge=0.0, le=1.0, description="New confidence score.")
    reason: str | None = Field(None, max_length=1000, description="Reason for superseding.")
    expires_at: datetime | None = Field(None, description="New expiration timestamp.")


class FactResponse(BaseModel):
    fact_id: str
    user_id: str
    namespace: str
    key: str
    value: Any
    source_id: str | None = None
    source_type: str
    source_reference: str = ""
    confidence: float
    verification_status: str
    rejection_reason: str | None = None
    sensitivity: str
    allowed_purposes: list[str] = Field(default_factory=list)
    created_at: str
    updated_at: str
    expires_at: str | None = None
    superseded_by_fact_id: str | None = None
    version: int


class CreatePackageRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    purpose: str = Field(..., min_length=1, max_length=255, description="Access purpose.")
    namespaces: list[str] | None = Field(None, description="Filter by namespace prefixes.")
    require_verified: bool = Field(False, description="Require verified facts only.")
    expires_at: datetime | None = Field(None, description="Package expiration timestamp.")


class ContextPackageResponse(BaseModel):
    package_id: str
    user_id: str
    purpose: str
    fact_ids: list[str] = Field(default_factory=list)
    facts: list[FactResponse] = Field(default_factory=list)
    generated_at: str
    expires_at: str | None = None
    fingerprint: str
