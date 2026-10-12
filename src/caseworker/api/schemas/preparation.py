"""Pydantic schemas for Preparation Intelligence API."""

from __future__ import annotations

from typing import Any
from pydantic import BaseModel, ConfigDict, Field


class EvidenceMappingItemResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")

    requirement_id: str
    requirement_type: str
    description: str
    mandatory: bool
    status: str
    matched_fact_id: str | None = None
    matched_fact_version: int | None = None
    verification_status: str | None = None
    fact_namespace: str | None = None
    fact_key: str | None = None
    notes: str = ""


class GapItemResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")

    gap_id: str
    requirement_id: str
    requirement_type: str
    category: str
    description: str
    severity: str
    mitigation_guidance: str
    fact_id: str | None = None


class ClaimIntentResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")

    intent_id: str
    requirement_id: str
    target_material: str
    claim_topic: str
    supporting_fact_ids: list[str] = Field(default_factory=list)
    suggested_angle: str = ""


class PreparationStrategyResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")

    recommended_materials: list[str] = Field(default_factory=list)
    strengths: list[str] = Field(default_factory=list)
    mitigations: list[str] = Field(default_factory=list)
    claim_intents: list[ClaimIntentResponse] = Field(default_factory=list)
    materials_checklist: list[dict[str, Any]] = Field(default_factory=list)


class PreparationIntelligenceReportResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")

    report_id: str
    case_id: str
    opportunity_id: str
    user_id: str
    schema_version: str
    analyzer_version: str
    evidence_map: list[EvidenceMappingItemResponse] = Field(default_factory=list)
    gaps: list[GapItemResponse] = Field(default_factory=list)
    strategy: PreparationStrategyResponse
    input_hash: str
    context_snapshot_hash: str
    source_evaluation_id: str | None = None
    created_at: str
    version: int
    is_stale: bool = False
