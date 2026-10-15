"""Pydantic schemas for prepared application material artifacts and AST responses."""

from __future__ import annotations

from typing import Any
from pydantic import BaseModel, Field


class ArtifactBlockResponse(BaseModel):
    """Semantic block within an artifact AST."""

    block_id: str
    block_type: str
    content: str
    grounding_type: str
    claim_ids: list[str] = Field(default_factory=list)
    fact_ids: list[str] = Field(default_factory=list)
    opportunity_fields: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
    answer_status: str | None = None


class PreparedArtifactResponse(BaseModel):
    """Prepared application material response DTO."""

    artifact_id: str
    artifact_set_id: str
    case_id: str
    opportunity_id: str
    user_id: str
    material_type: str
    title: str
    schema_version: str
    planner_version: str
    status: str
    blocks: list[ArtifactBlockResponse]
    source_report_id: str
    input_hash: str
    grounding_snapshot_hash: str
    rendered_markdown: str
    created_at: str
    updated_at: str
    version: int
    is_stale: bool = False


class PreparedArtifactListResponse(BaseModel):
    """Collection of prepared artifacts for a case."""

    artifacts: list[PreparedArtifactResponse]
    total: int


class ArtifactProvenanceResponse(BaseModel):
    """Block-level grounding provenance map for an artifact."""

    artifact_id: str
    artifact_set_id: str
    case_id: str
    opportunity_id: str
    material_type: str
    title: str
    status: str
    source_report_id: str
    input_hash: str
    grounding_snapshot_hash: str
    version: int
    blocks: list[ArtifactBlockResponse]
    is_stale: bool = False

