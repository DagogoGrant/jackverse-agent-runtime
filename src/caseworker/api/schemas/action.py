"""Pydantic schemas for Actions API."""

from __future__ import annotations

from typing import Any
from pydantic import BaseModel, ConfigDict, Field
from caseworker.domain.enums import ActionStatus, RiskLevel


class ProposeActionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action_type: str = Field(..., min_length=1, max_length=64, description="Action classification type.")
    description: str = Field(..., min_length=1, max_length=4000, description="Human-readable description of intended action.")
    parameters: dict[str, Any] = Field(default_factory=dict, description="Action execution arguments.")
    idempotency_key: str | None = Field(None, max_length=128, description="Optional caller idempotency key.")


class ActionResponse(BaseModel):
    action_id: str
    case_id: str
    action_type: str
    description: str
    parameters: dict[str, Any] = Field(default_factory=dict)
    status: ActionStatus
    risk_level: RiskLevel
    requires_approval: bool
    fingerprint: str
    created_at: str
    executed_at: str | None = None
    result: dict[str, Any] = Field(default_factory=dict)
    idempotency_key: str | None = None
    version: int
