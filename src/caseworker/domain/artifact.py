"""Domain models for prepared application materials AST and provenance."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
import re
from typing import Any
import uuid

from caseworker.domain.enums import ArtifactGroundingType, ArtifactStatus, PreparationMaterialType
from caseworker.domain.errors import DomainValidationError
from caseworker.domain.types import ensure_utc, from_iso_utc, now_utc, to_iso_utc


@dataclass
class ArtifactBlock:
    """A semantic content node in an artifact AST."""

    block_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    block_type: str = "paragraph"
    content: str = ""
    grounding_type: ArtifactGroundingType = ArtifactGroundingType.STRUCTURAL
    claim_ids: list[str] = field(default_factory=list)
    fact_ids: list[str] = field(default_factory=list)
    opportunity_fields: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.block_id or not self.block_id.strip():
            raise DomainValidationError("ArtifactBlock block_id cannot be empty.")
        if not isinstance(self.content, str):
            raise DomainValidationError("ArtifactBlock content must be a string.")
        if isinstance(self.grounding_type, str) and not isinstance(self.grounding_type, ArtifactGroundingType):
            self.grounding_type = ArtifactGroundingType(self.grounding_type)

        # Invariant validations
        if self.grounding_type == ArtifactGroundingType.CLAIM:
            if not self.claim_ids:
                raise DomainValidationError("CLAIM grounded block must reference at least one claim_id.")
        elif self.grounding_type == ArtifactGroundingType.CONTEXT_FACT:
            if not self.fact_ids:
                raise DomainValidationError("CONTEXT_FACT grounded block must reference at least one fact_id.")
        elif self.grounding_type == ArtifactGroundingType.OPPORTUNITY:
            if not self.opportunity_fields:
                raise DomainValidationError("OPPORTUNITY grounded block must reference at least one opportunity_field.")

    def to_dict(self) -> dict[str, Any]:
        return {
            "block_id": self.block_id,
            "block_type": self.block_type,
            "content": self.content,
            "grounding_type": self.grounding_type.value,
            "claim_ids": list(self.claim_ids),
            "fact_ids": list(self.fact_ids),
            "opportunity_fields": list(self.opportunity_fields),
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ArtifactBlock:
        return cls(
            block_id=data["block_id"],
            block_type=data.get("block_type", "paragraph"),
            content=data.get("content", ""),
            grounding_type=ArtifactGroundingType(data["grounding_type"]),
            claim_ids=list(data.get("claim_ids") or []),
            fact_ids=list(data.get("fact_ids") or []),
            opportunity_fields=list(data.get("opportunity_fields") or []),
            metadata=dict(data.get("metadata") or {}),
        )


@dataclass
class PreparedArtifact:
    """A claim-bound prepared application material (e.g. CV, Cover Letter)."""

    artifact_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    artifact_set_id: str = ""
    case_id: str = ""
    opportunity_id: str = ""
    user_id: str = ""
    material_type: PreparationMaterialType = PreparationMaterialType.CV
    title: str = ""
    schema_version: str = "prep_artifact_v1"
    planner_version: str = "artifact_planner_v1"
    status: ArtifactStatus = ArtifactStatus.DRAFT
    blocks: list[ArtifactBlock] = field(default_factory=list)
    source_report_id: str = ""
    input_hash: str = ""
    grounding_snapshot_hash: str = ""
    created_at: datetime = field(default_factory=now_utc)
    updated_at: datetime = field(default_factory=now_utc)
    version: int = 1

    def __post_init__(self) -> None:
        if not self.artifact_id or not self.artifact_id.strip():
            raise DomainValidationError("PreparedArtifact artifact_id cannot be empty.")
        if not self.artifact_set_id or not self.artifact_set_id.strip():
            raise DomainValidationError("PreparedArtifact artifact_set_id cannot be empty.")
        if not self.case_id or not self.case_id.strip():
            raise DomainValidationError("PreparedArtifact case_id cannot be empty.")
        if not self.opportunity_id or not self.opportunity_id.strip():
            raise DomainValidationError("PreparedArtifact opportunity_id cannot be empty.")
        if not self.user_id or not self.user_id.strip():
            raise DomainValidationError("PreparedArtifact user_id cannot be empty.")
        if not self.title or not self.title.strip():
            raise DomainValidationError("PreparedArtifact title cannot be empty.")
        if not self.source_report_id or not self.source_report_id.strip():
            raise DomainValidationError("PreparedArtifact source_report_id cannot be empty.")
        if self.version < 1:
            raise DomainValidationError(f"PreparedArtifact version must be >= 1, got {self.version}")

        self.created_at = ensure_utc(self.created_at) or now_utc()
        self.updated_at = ensure_utc(self.updated_at) or now_utc()

        if isinstance(self.material_type, str) and not isinstance(self.material_type, PreparationMaterialType):
            self.material_type = PreparationMaterialType(self.material_type)
        if isinstance(self.status, str) and not isinstance(self.status, ArtifactStatus):
            self.status = ArtifactStatus(self.status)

    def render_markdown(self) -> str:
        """Render AST blocks to cleanly formatted Markdown without synthetic markers."""
        lines: list[str] = []
        in_bullet_list = False

        for block in self.blocks:
            btype = block.block_type
            content = block.content.strip()

            if btype == "header":
                in_bullet_list = False
                lines.append(f"# {content}\n")
            elif btype == "section_title":
                in_bullet_list = False
                lines.append(f"\n## {content}\n")
            elif btype == "cv_bullet":
                in_bullet_list = True
                lines.append(f"- {content}")
            elif btype == "qna_item":
                in_bullet_list = False
                question = block.metadata.get("question")
                answer = block.metadata.get("answer") or content
                if question:
                    lines.append(f"\n### Q: {question}\n\n{answer}\n")
                else:
                    lines.append(f"\n{content}\n")
            elif btype == "contact_info":
                in_bullet_list = False
                lines.append(f"{content}\n")
            else:
                if in_bullet_list:
                    lines.append("")  # Blank line to exit list
                    in_bullet_list = False
                lines.append(f"{content}\n")

        rendered = "\n".join(lines)
        # Normalize redundant blank lines
        rendered = re.sub(r"\n{3,}", "\n\n", rendered).strip()
        return rendered

    def render_plain_text(self) -> str:
        """Render AST blocks to plain text without Markdown markdown headers or bold markers."""
        md = self.render_markdown()
        # Strip header markers (#, ##, etc.)
        plain = re.sub(r"^#{1,6}\s+", "", md, flags=re.MULTILINE)
        # Strip bold/italic formatting
        plain = re.sub(r"\*\*([^*]+)\*\*", r"\1", plain)
        plain = re.sub(r"\*([^*]+)\*", r"\1", plain)
        plain = re.sub(r"`([^`]+)`", r"\1", plain)
        return plain.strip()

    def render_provenance_map(self) -> dict[str, Any]:
        """Produce the block-level grounding map for verification and UI inspection."""
        return {
            "artifact_id": self.artifact_id,
            "artifact_set_id": self.artifact_set_id,
            "case_id": self.case_id,
            "opportunity_id": self.opportunity_id,
            "material_type": self.material_type.value,
            "title": self.title,
            "status": self.status.value,
            "source_report_id": self.source_report_id,
            "input_hash": self.input_hash,
            "grounding_snapshot_hash": self.grounding_snapshot_hash,
            "version": self.version,
            "blocks": [b.to_dict() for b in self.blocks],
        }

    def mark_stale(self) -> None:
        """Mark artifact as STALE if currently DRAFT."""
        if self.status == ArtifactStatus.DRAFT:
            self.status = ArtifactStatus.STALE
            self.updated_at = now_utc()
            self.version += 1

    def mark_superseded(self) -> None:
        """Mark artifact as SUPERSEDED by newer re-analysis/re-drafting."""
        if self.status != ArtifactStatus.SUPERSEDED:
            self.status = ArtifactStatus.SUPERSEDED
            self.updated_at = now_utc()
            self.version += 1

    def freeze(self) -> None:
        """Freeze artifact when verified/locked."""
        if self.status != ArtifactStatus.FROZEN:
            self.status = ArtifactStatus.FROZEN
            self.updated_at = now_utc()
            self.version += 1

    def to_dict(self) -> dict[str, Any]:
        return {
            "artifact_id": self.artifact_id,
            "artifact_set_id": self.artifact_set_id,
            "case_id": self.case_id,
            "opportunity_id": self.opportunity_id,
            "user_id": self.user_id,
            "material_type": self.material_type.value,
            "title": self.title,
            "schema_version": self.schema_version,
            "planner_version": self.planner_version,
            "status": self.status.value,
            "blocks": [b.to_dict() for b in self.blocks],
            "source_report_id": self.source_report_id,
            "input_hash": self.input_hash,
            "grounding_snapshot_hash": self.grounding_snapshot_hash,
            "created_at": to_iso_utc(self.created_at),
            "updated_at": to_iso_utc(self.updated_at),
            "version": self.version,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> PreparedArtifact:
        blocks = [ArtifactBlock.from_dict(b) for b in (data.get("blocks") or [])]
        return cls(
            artifact_id=data["artifact_id"],
            artifact_set_id=data["artifact_set_id"],
            case_id=data["case_id"],
            opportunity_id=data["opportunity_id"],
            user_id=data["user_id"],
            material_type=PreparationMaterialType(data["material_type"]),
            title=data["title"],
            schema_version=data.get("schema_version", "prep_artifact_v1"),
            planner_version=data.get("planner_version", "artifact_planner_v1"),
            status=ArtifactStatus(data.get("status", ArtifactStatus.DRAFT)),
            blocks=blocks,
            source_report_id=data["source_report_id"],
            input_hash=data.get("input_hash", ""),
            grounding_snapshot_hash=data.get("grounding_snapshot_hash", ""),
            created_at=from_iso_utc(data["created_at"]) or now_utc(),
            updated_at=from_iso_utc(data["updated_at"]) or now_utc(),
            version=int(data.get("version", 1)),
        )


@dataclass
class ArtifactSet:
    """Group of prepared application materials sharing an artifact_set_id and source_report_id."""

    set_id: str
    case_id: str
    opportunity_id: str
    user_id: str
    source_report_id: str
    draft_fingerprint: str
    status: ArtifactStatus = ArtifactStatus.DRAFT
    artifacts: list[PreparedArtifact] = field(default_factory=list)
    created_at: datetime = field(default_factory=now_utc)

    def to_dict(self) -> dict[str, Any]:
        return {
            "set_id": self.set_id,
            "case_id": self.case_id,
            "opportunity_id": self.opportunity_id,
            "user_id": self.user_id,
            "source_report_id": self.source_report_id,
            "draft_fingerprint": self.draft_fingerprint,
            "status": self.status.value,
            "artifacts": [a.to_dict() for a in self.artifacts],
            "created_at": to_iso_utc(self.created_at),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ArtifactSet:
        artifacts = [PreparedArtifact.from_dict(a) for a in (data.get("artifacts") or [])]
        return cls(
            set_id=data["set_id"],
            case_id=data["case_id"],
            opportunity_id=data["opportunity_id"],
            user_id=data["user_id"],
            source_report_id=data["source_report_id"],
            draft_fingerprint=data.get("draft_fingerprint", ""),
            status=ArtifactStatus(data.get("status", ArtifactStatus.DRAFT)),
            artifacts=artifacts,
            created_at=from_iso_utc(data["created_at"]) or now_utc(),
        )
