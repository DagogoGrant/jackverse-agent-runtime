"""Pydantic schemas for Approvals API."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field
from caseworker.api.schemas.action import ActionResponse


class ApproveActionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str | None = Field(None, max_length=1000, description="Optional rationale for human approval.")


class RejectActionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str | None = Field(None, max_length=1000, description="Optional rationale for rejection.")


class ApprovalResponse(BaseModel):
    approval_id: str
    action_id: str
    case_id: str
    user_id: str
    action_fingerprint: str
    status: str
    requested_at: str
    decided_at: str | None = None
    expires_at: str | None = None
    reason: str | None = None
    version: int


class ApprovalDecisionResponse(BaseModel):
    approval: ApprovalResponse
    action: ActionResponse
