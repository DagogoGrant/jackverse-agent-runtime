"""Pydantic schemas for Claim Ledger API."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field
from caseworker.domain.enums import ClaimStatus


class ProposeClaimRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    purpose: str = Field(..., min_length=1, max_length=255, description="Intended purpose for the assertion.")
    text: str = Field(..., min_length=1, max_length=4000, description="Assertion statement text.")
    supporting_fact_ids: list[str] = Field(default_factory=list, description="IDs of backing context facts.")
    case_id: str | None = Field(None, description="Optional linked case ID.")
    mission_id: str | None = Field(None, description="Optional linked mission ID.")
    auto_evaluate: bool = Field(True, description="Immediately evaluate against supporting facts.")


class EvaluateClaimRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")


class RejectClaimRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str | None = Field(None, max_length=1000, description="Reason for rejection.")


class ClaimResponse(BaseModel):
    claim_id: str
    user_id: str
    purpose: str
    text: str
    case_id: str | None = None
    mission_id: str | None = None
    status: str
    supporting_fact_ids: list[str] = Field(default_factory=list)
    created_at: str
    updated_at: str
    verified_at: str | None = None
    rejection_reason: str | None = None
    version: int
