"""Domain models for prepared application materials AST and provenance."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
import re
from typing import Any
import uuid

from caseworker.domain.enums import (
    ArtifactGroundingType,
    ArtifactStatus,
    ClaimStatus,
    PreparationMaterialType,
)
from caseworker.domain.errors import DomainValidationError
from caseworker.domain.requirement_matcher import is_fact_verified
from caseworker.domain.types import (
    canonical_json_dumps,
    compute_sha256,
    ensure_utc,
    from_iso_utc,
    now_utc,
    to_iso_utc,
)



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

    @property
    def answer_status(self) -> str | None:
        """Structured Q&A status: 'ANSWERED' or 'NEEDS_USER_INPUT'."""
        return self.metadata.get("answer_status")

    @property
    def answer(self) -> str | None:
        """Structured Q&A answer text, or None if awaiting user input."""
        return self.metadata.get("answer")


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
            "answer_status": self.answer_status,
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
                answer_status = block.metadata.get("answer_status")
                answer = block.metadata.get("answer")
                if question:
                    if answer_status == "NEEDS_USER_INPUT" or not answer:
                        lines.append(f"\n### Q: {question}\n")
                    else:
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

    def freeze(self, reason: str | None = None) -> None:
        """Freeze artifact when verified/locked."""
        if self.status != ArtifactStatus.FROZEN:
            self.status = ArtifactStatus.FROZEN
            self.updated_at = now_utc()
            self.version += 1

    def compute_rendered_payload(self) -> str:
        """Return the rendered markdown payload."""
        return self.render_markdown()

    def compute_rendered_payload_hash(self) -> str:
        """Compute sha256 hash of the rendered markdown payload."""
        return compute_sha256(self.render_markdown())

    def transition_to(self, new_status: ArtifactStatus | str, reason: str | None = None) -> None:
        """Transition artifact lifecycle status."""
        if isinstance(new_status, str):
            new_status = ArtifactStatus(new_status)
        if new_status == self.status:
            return
        self.status = new_status
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


def compute_artifact_grounding_hash(
    claims: list[Any],
    direct_facts: list[Any] | None = None,
) -> str:
    """Compute privacy-safe hash of all artifact grounding dependencies.

    Zero raw private values in hash.
    Hashes only:
    - claims: (claim_id, version, status, support_snapshot_hash)
    - direct_facts: (fact_id, version, verification_status, active, "job_application" in allowed_purposes)
    """
    safe_claims = [
        (
            getattr(c, "claim_id", ""),
            getattr(c, "version", 1),
            getattr(c.status, "value", str(c.status)) if hasattr(c, "status") and c.status is not None else None,
            getattr(c, "support_snapshot_hash", None),
        )
        for c in sorted(claims, key=lambda x: getattr(x, "claim_id", ""))
    ]
    safe_facts = [
        (
            getattr(f, "fact_id", ""),
            getattr(f, "version", 1),
            getattr(f.verification_status, "value", str(f.verification_status))
            if hasattr(f, "verification_status") and f.verification_status is not None
            else None,
            getattr(f, "superseded_by_fact_id", None) is None,
            "job_application" in (getattr(f, "allowed_purposes", []) or []),
        )
        for f in sorted(direct_facts or [], key=lambda x: getattr(x, "fact_id", ""))
    ]
    return compute_sha256(canonical_json_dumps({"claims": safe_claims, "facts": safe_facts}))


def compute_artifact_staleness(
    artifact: PreparedArtifact,
    latest_report: Any | None,
    is_report_stale: bool,
    claims_by_id: dict[str, Any],
    facts_by_id: dict[str, Any],
    current_planner_version: str = "job_app_planner_v1",
    current_schema_version: str = "prep_artifact_v1",
) -> tuple[bool, str | None]:
    """Dynamically determine whether an artifact is stale without mutating state."""
    if artifact.status in (ArtifactStatus.STALE, ArtifactStatus.SUPERSEDED):
        return True, f"Artifact status is {artifact.status.value}."

    if latest_report is None or artifact.source_report_id != getattr(latest_report, "report_id", None):
        return True, "Source preparation report is missing or newer report exists."

    if is_report_stale:
        return True, "Source preparation intelligence report is stale."

    if artifact.planner_version != current_planner_version:
        return True, f"Planner version mismatch ({artifact.planner_version} vs {current_planner_version})."

    if artifact.schema_version != current_schema_version:
        return True, f"Schema version mismatch ({artifact.schema_version} vs {current_schema_version})."

    referenced_claims: list[Any] = []
    referenced_claim_ids: set[str] = set()
    referenced_fact_ids: set[str] = set()

    for block in artifact.blocks:
        for cid in block.claim_ids:
            referenced_claim_ids.add(cid)
        for fid in block.fact_ids:
            referenced_fact_ids.add(fid)

    for cid in referenced_claim_ids:
        claim = claims_by_id.get(cid)
        if claim is None:
            return True, f"Referenced claim '{cid}' is missing."
        status_val = getattr(claim.status, "value", str(claim.status))
        if status_val != ClaimStatus.SUPPORTED.value:
            return True, f"Referenced claim '{cid}' is no longer SUPPORTED (status: {status_val})."
        referenced_claims.append(claim)
        for fid in getattr(claim, "supporting_fact_ids", []):
            fact = facts_by_id.get(fid)
            if fact is None or getattr(fact, "superseded_by_fact_id", None) is not None:
                return True, f"Supporting fact '{fid}' of claim '{cid}' is missing or superseded."
            if "job_application" not in (getattr(fact, "allowed_purposes", []) or []):
                return True, f"Supporting fact '{fid}' lacks purpose 'job_application'."

    direct_facts: list[Any] = []
    for fid in referenced_fact_ids:
        fact = facts_by_id.get(fid)
        if fact is None or getattr(fact, "superseded_by_fact_id", None) is not None:
            return True, f"Direct fact '{fid}' is missing or superseded."
        if not is_fact_verified(getattr(fact, "verification_status", None)):
            return True, f"Direct fact '{fid}' is not verified."
        if "job_application" not in (getattr(fact, "allowed_purposes", []) or []):
            return True, f"Direct fact '{fid}' lacks purpose 'job_application'."
        direct_facts.append(fact)

    expected_grounding_hash = compute_artifact_grounding_hash(referenced_claims, direct_facts)
    if artifact.grounding_snapshot_hash and artifact.grounding_snapshot_hash != expected_grounding_hash:
        return True, "Grounding dependency snapshot hash changed."

    return False, None

