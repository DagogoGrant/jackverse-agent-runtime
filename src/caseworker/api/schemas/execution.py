"""Pydantic schemas for Phase 5.3.3 Governed Execution API."""

from __future__ import annotations

from typing import Any
from pydantic import BaseModel, ConfigDict, Field

from caseworker.api.schemas.action import ActionResponse
from caseworker.domain.enums import (
    ExecutionAttemptStatus,
    ExecutionErrorCategory,
    ExecutionProviderKind,
    ExecutionReceiptStatus,
)


class ExecuteActionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    simulation_mode: str | None = Field(
        None,
        description="Optional simulation mode for test execution (e.g. success, pre_stage_failure, staging_failure, submit_confirmed_failure, submit_timeout).",
    )


class ExecutionAttemptResponse(BaseModel):
    attempt_id: str
    action_id: str
    approval_id: str
    plan_id: str
    case_id: str
    user_id: str
    lease_token: str
    fencing_token: int
    status: ExecutionAttemptStatus
    provider_kind: ExecutionProviderKind
    started_at: str
    completed_at: str | None = None
    last_reversible_point_reached_at: str | None = None
    irreversible_action_taken: bool
    staged_field_count: int
    staged_upload_count: int
    error_category: ExecutionErrorCategory | None = None
    error_message: str | None = None
    raw_response_snippet: str | None = None
    attempt_number: int


class ExecutionReceiptResponse(BaseModel):
    receipt_id: str
    attempt_id: str
    action_id: str
    approval_id: str
    plan_id: str
    case_id: str
    user_id: str
    status: ExecutionReceiptStatus
    destination_hostname: str
    destination_url: str
    destination_type: str
    execution_plan_fingerprint: str
    authorized_action_fingerprint: str
    package_fingerprint: str
    executed_at: str
    external_reference_id: str | None = None
    field_count: int
    upload_asset_ids: list[str] = Field(default_factory=list)
    upload_asset_shas: list[str] = Field(default_factory=list)
    screening_response_count: int
    safe_provider_metadata: dict[str, Any] = Field(default_factory=dict)
    receipt_hash: str
    version: int


class ExecuteActionResponse(BaseModel):
    action: ActionResponse
    attempt: ExecutionAttemptResponse
    receipt: ExecutionReceiptResponse | None = None
    case_status: str


class ExecutionAttemptListResponse(BaseModel):
    attempts: list[ExecutionAttemptResponse] = Field(default_factory=list)
    total: int
