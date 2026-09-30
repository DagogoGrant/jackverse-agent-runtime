"""Pydantic schemas for Opportunities API."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from pydantic import BaseModel, ConfigDict, Field
from caseworker.domain.enums import OpportunityStatus, OpportunityType


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
