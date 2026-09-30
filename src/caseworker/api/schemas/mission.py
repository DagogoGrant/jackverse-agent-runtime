"""Pydantic schemas for Missions API."""

from __future__ import annotations

from datetime import datetime
from pydantic import BaseModel, ConfigDict, Field
from caseworker.domain.enums import MissionKind, MissionStatus


class CreateMissionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(..., min_length=1, max_length=255, description="Title of the mission.")
    goal: str | None = Field(None, max_length=4000, description="Clear objective or goal of the mission.")
    kind: MissionKind = Field(MissionKind.GENERAL_GOAL, description="Classification of the mission.")
    success_criteria: list[str] = Field(default_factory=list, description="Target outcomes.")
    constraints: list[str] = Field(default_factory=list, description="Operational constraints.")
    deadline: datetime | None = Field(None, description="Completion deadline.")


class TransitionMissionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    new_status: MissionStatus = Field(..., description="Target lifecycle status.")
    reason: str | None = Field(None, max_length=1000, description="Reason for the status transition.")


class MissionResponse(BaseModel):
    mission_id: str
    user_id: str
    title: str
    goal: str
    kind: str
    status: str
    success_criteria: list[str] = Field(default_factory=list)
    constraints: list[str] = Field(default_factory=list)
    created_at: str
    updated_at: str
    deadline: str | None = None
    version: int
