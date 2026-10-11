"""Pydantic schemas for Opportunities API."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from pydantic import BaseModel, ConfigDict, Field
from caseworker.domain.enums import OpportunityStatus, OpportunityType
from caseworker.domain.opportunity import Opportunity
from caseworker.domain.types import to_iso_utc


class CreateOpportunityRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(..., min_length=1, max_length=255, description="Opportunity title.")
    opportunity_type: OpportunityType = Field(..., description="Classification of opportunity.")
    organization: str = Field("", max_length=255, description="Provider/Host organization.")
    source_url: str = Field("", max_length=2048, description="Web address for the opportunity.")
    source_name: str = Field("", max_length=255, description="Source platform name.")
    location: str = Field("", max_length=255, description="Geographic or remote location.")
    status: OpportunityStatus = Field(OpportunityStatus.DISCOVERED, description="Initial discovery status.")
    requirements: list[str] = Field(default_factory=list, description="Eligibility requirements.")
    metadata: dict[str, Any] = Field(default_factory=dict, description="Custom metadata attributes.")
    deadline: datetime | None = Field(None, description="Application or submission deadline.")
    mission_id: str | None = Field(None, description="Optional associated mission ID.")


class TransitionOpportunityRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    new_status: OpportunityStatus = Field(..., description="Target opportunity status.")
    reason: str | None = Field(None, max_length=1000, description="Reason for transition.")


class ShortlistOpportunityRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str | None = Field(None, max_length=1000, description="Shortlisting rationale.")
    override_ineligible: bool = Field(False, description="Explicit override if evaluated as INELIGIBLE.")


class ConvertToCaseRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    case_type: str | None = Field(None, description="Optional override for CaseType.")
    title: str | None = Field(None, max_length=255, description="Optional custom title for the Case.")
    goal: str | None = Field(None, max_length=1000, description="Optional custom goal for the Case.")


class EvaluationSummary(BaseModel):
    eligibility_status: str
    fit_score: float
    fit_tier: str
    coverage: float
    evaluated_at: str
    is_stale: bool = False


class OpportunityResponse(BaseModel):
    opportunity_id: str
    user_id: str
    mission_id: str | None = None
    opportunity_type: str
    title: str
    organization: str = ""
    source_url: str = ""
    source_name: str = ""
    location: str = ""
    status: str
    requirements: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
    fingerprint: str
    discovered_at: str
    deadline: str | None = None
    version: int
    converted_case_id: str | None = None
    fingerprint_v2: str | None = None
    evaluation_summary: EvaluationSummary | None = None


class NormalizedRequirementResponse(BaseModel):
    requirement_id: str
    raw_text: str
    criterion: str
    category: str
    value: Any = None
    normalization_status: str


class NormalizeOpportunityResponse(BaseModel):
    opportunity_id: str
    requirements: list[NormalizedRequirementResponse]
    status: str
    version: int


class RequirementEvidenceResponse(BaseModel):
    requirement_id: str
    fact_id: str | None = None
    fact_version: int | None = None
    verification_status: str | None = None
    match_result: str
    notes: str = ""


class EligibilityResultResponse(BaseModel):
    status: str
    coverage: float
    mandatory_met: int
    mandatory_total: int
    contradicted_requirements: list[str] = Field(default_factory=list)
    missing_or_unknown_requirements: list[str] = Field(default_factory=list)


class FitScoreComponentsResponse(BaseModel):
    skills_score: float
    preferred_reqs_score: float
    location_score: float
    seniority_score: float
    total_score: float
    fit_tier: str
    explanations: list[str] = Field(default_factory=list)


class EvaluationSnapshotResponse(BaseModel):
    evaluation_id: str
    evaluated_at: str
    evaluation_policy: str
    eligibility: EligibilityResultResponse
    fit: FitScoreComponentsResponse
    evidence: list[RequirementEvidenceResponse] = Field(default_factory=list)
    evaluation_input_hash: str
    context_snapshot_hash: str


class ConvertToCaseResponse(BaseModel):
    case_id: str
    opportunity_id: str
    case: dict[str, Any]
    opportunity: OpportunityResponse
    created: bool


def make_opportunity_response(opp: Opportunity) -> OpportunityResponse:
    """Project domain Opportunity entity into safe API response, stripping raw evaluation payload."""
    safe_metadata = dict(opp.metadata)
    eval_meta = safe_metadata.pop("evaluation", None)

    eval_summary: EvaluationSummary | None = None
    if eval_meta and isinstance(eval_meta, dict):
        elig = eval_meta.get("eligibility", {})
        fit = eval_meta.get("fit", {}).get("components", {})
        eval_summary = EvaluationSummary(
            eligibility_status=str(elig.get("status", "unknown")),
            fit_score=float(fit.get("total_score", 0.0)),
            fit_tier=str(fit.get("fit_tier", "low")),
            coverage=float(elig.get("coverage", 0.0)),
            evaluated_at=str(eval_meta.get("evaluated_at", "")),
            is_stale=False,
        )

    return OpportunityResponse(
        opportunity_id=opp.opportunity_id,
        user_id=opp.user_id,
        mission_id=opp.mission_id,
        opportunity_type=opp.opportunity_type_str,
        title=opp.title,
        organization=opp.organization,
        source_url=opp.source_url,
        source_name=opp.source_name,
        location=opp.location,
        status=opp.status.value,
        requirements=list(opp.requirements),
        metadata=safe_metadata,
        fingerprint=opp.fingerprint,
        discovered_at=to_iso_utc(opp.discovered_at),
        deadline=to_iso_utc(opp.deadline) if opp.deadline else None,
        version=opp.version,
        converted_case_id=opp.converted_case_id,
        fingerprint_v2=opp.fingerprint_v2,
        evaluation_summary=eval_summary,
    )
