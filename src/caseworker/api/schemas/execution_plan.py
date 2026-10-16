"""Pydantic schemas for exact external action planning."""

from __future__ import annotations

from typing import Any
from pydantic import BaseModel, ConfigDict, Field


class TargetSchemaFieldSchema(BaseModel):
    model_config = ConfigDict(extra="forbid")

    target_field_id: str
    label: str
    field_type: str = "text"
    required: bool = True
    allowed_options: list[str] = Field(default_factory=list)


class TargetSchemaUploadSlotSchema(BaseModel):
    model_config = ConfigDict(extra="forbid")

    slot_id: str
    role: str
    label: str
    required: bool = True
    accepted_mime_types: list[str] = Field(default_factory=lambda: ["application/pdf", "text/plain"])


class TargetSchemaQuestionSchema(BaseModel):
    model_config = ConfigDict(extra="forbid")

    question_id: str
    question_text: str
    required: bool = True
    answer_type: str = "text"


class TargetSubmitControlSpecSchema(BaseModel):
    model_config = ConfigDict(extra="forbid")

    control_type: str = "button_submit"
    semantic_target: str = "Submit Application"
    selector: str = ""
    method: str = "POST"
    requires_double_confirmation: bool = True


class TargetInteractionSchemaSchema(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_id: str
    destination_type: str = "ats_form"
    fields: list[TargetSchemaFieldSchema] = Field(default_factory=list)
    upload_slots: list[TargetSchemaUploadSlotSchema] = Field(default_factory=list)
    screening_questions: list[TargetSchemaQuestionSchema] = Field(default_factory=list)
    final_submit_control: TargetSubmitControlSpecSchema | None = None
    schema_hash: str = ""


class ExecutionDestinationSchema(BaseModel):
    model_config = ConfigDict(extra="forbid")

    destination_type: str
    destination_provenance: str
    canonical_url: str
    organization: str
    channel_metadata: dict[str, Any] = Field(default_factory=dict)
    is_exact_destination: bool = True
    normalized_origin: str = ""
    normalized_hostname: str = ""


class CreateExecutionPlanRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    package_id: str | None = None
    destination_override: ExecutionDestinationSchema | None = None
    target_schema: TargetInteractionSchemaSchema | None = None
    channel_metadata: dict[str, Any] | None = None
    final_submit_control: TargetSubmitControlSpecSchema | None = None


class ExecutionFieldMappingResponse(BaseModel):
    target_field_id: str
    field_name: str
    field_value: Any
    source_fact_id: str
    source_fact_version: int
    verification_status: str
    required: bool


class ExecutionUploadAssetResponse(BaseModel):
    asset_id: str
    role: str
    source_artifact_id: str
    source_artifact_version: int
    filename: str
    mime_type: str
    content_sha256: str
    byte_size: int
    materialized: bool


class ExecutionScreeningResponseDTO(BaseModel):
    question_id: str
    question: str
    answer: str
    source_artifact_id: str
    grounding_claim_ids: list[str]


class ExecutionPlanFindingResponse(BaseModel):
    check_name: str
    passed: bool
    severity: str
    code: str
    message: str
    details: dict[str, Any] = Field(default_factory=dict)


class ExternalExecutionPlanResponse(BaseModel):
    plan_id: str
    case_id: str
    opportunity_id: str
    package_id: str
    package_fingerprint: str
    user_id: str
    status: str
    destination_provenance: str = ""
    destination: ExecutionDestinationSchema
    target_schema: TargetInteractionSchemaSchema | None = None
    target_schema_hash: str = ""
    field_mappings: list[ExecutionFieldMappingResponse]
    upload_mappings: list[ExecutionUploadAssetResponse]
    screening_responses: list[ExecutionScreeningResponseDTO]
    final_submit_control: TargetSubmitControlSpecSchema | None = None
    findings: list[ExecutionPlanFindingResponse]
    planning_findings: list[ExecutionPlanFindingResponse] = Field(default_factory=list)
    execution_input_hash: str
    execution_plan_fingerprint: str
    schema_version: str
    planner_version: str
    created_at: str
    updated_at: str
    version: int
    is_stale: bool = False
    staleness_reasons: list[str] = Field(default_factory=list)


class ExternalExecutionPlanListResponse(BaseModel):
    plans: list[ExternalExecutionPlanResponse]
    total: int
