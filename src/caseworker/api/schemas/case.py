"""Pydantic schemas for Cases API."""

from __future__ import annotations

from datetime import datetime
from pydantic import BaseModel, ConfigDict, Field
from caseworker.domain.enums import CaseStatus, CaseType


class CreateCaseRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(..., min_length=1, max_length=255, description="Case title.")
    goal: str = Field(..., min_length=1, max_length=4000, description="Clear objective or goal of the case.")
    case_type: CaseType = Field(CaseType.GENERAL, description="Type of case.")
    mission_id: str | None = Field(None, description="Optional parent mission ID.")
    success_criteria: list[str] = Field(default_factory=list, description="Target outcomes.")
    constraints: list[str] = Field(default_factory=list, description="Operational constraints.")
    deadline: datetime | None = Field(None, description="Completion deadline.")


class TransitionCaseRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    new_status: CaseStatus = Field(..., description="Target case status.")
    reason: str | None = Field(None, max_length=1000, description="Reason for transition.")


class ResolveCaseRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    outcome: str = Field(..., min_length=1, max_length=1000, description="Case resolution summary.")


class CaseResponse(BaseModel):
    case_id: str
    mission_id: str | None = None
    user_id: str
    case_type: str
    title: str
    goal: str
    status: str
    success_criteria: list[str] = Field(default_factory=list)
    constraints: list[str] = Field(default_factory=list)
    created_at: str
    updated_at: str
    deadline: str | None = None
    resolved_at: str | None = None
    outcome: str | None = None
    version: int
