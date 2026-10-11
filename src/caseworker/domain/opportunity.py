"""Opportunity domain entity representing discovered leads (jobs, housing, scholarships, etc.)."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
import uuid

from caseworker.domain.enums import OpportunityStatus, OpportunityType
from caseworker.domain.errors import DomainValidationError, InvalidStateTransitionError
from caseworker.domain.types import (
    canonical_json_dumps,
    compute_sha256,
    ensure_utc,
    from_iso_utc,
    now_utc,
    to_iso_utc,
)

FINGERPRINT_SCHEMA_V1 = "opp_v1"
FINGERPRINT_SCHEMA_V2 = "opp_v2"

_VALID_OPPORTUNITY_TRANSITIONS: dict[OpportunityStatus, set[OpportunityStatus]] = {
    OpportunityStatus.DISCOVERED: {
        OpportunityStatus.NORMALIZED,
        OpportunityStatus.ARCHIVED,
        OpportunityStatus.REJECTED,
    },
    OpportunityStatus.NORMALIZED: {
        OpportunityStatus.EVALUATING,
        OpportunityStatus.ARCHIVED,
        OpportunityStatus.REJECTED,
    },
    OpportunityStatus.EVALUATING: {
        OpportunityStatus.SHORTLISTED,
        OpportunityStatus.NORMALIZED,
        OpportunityStatus.EVALUATING,
        OpportunityStatus.ARCHIVED,
        OpportunityStatus.REJECTED,
    },
    OpportunityStatus.SHORTLISTED: {
        OpportunityStatus.CONVERTED_TO_CASE,
        OpportunityStatus.ARCHIVED,
        OpportunityStatus.REJECTED,
    },
    OpportunityStatus.CONVERTED_TO_CASE: set(),
    OpportunityStatus.ARCHIVED: {OpportunityStatus.EVALUATING, OpportunityStatus.SHORTLISTED},
    OpportunityStatus.REJECTED: set(),
}


@dataclass
class Opportunity:
    """An external prospect discovered for a user (e.g. job posting, grant, rental unit).

    Deduplication Scope:
        - V1 fingerprint (legacy / backwards compatibility):
          (schema_version, opportunity_type, organization.lower(), title.lower(), source_url.lower()).
        - V2 fingerprint (cross-source deduplication):
          (schema_version, opportunity_type, organization.lower(), title.lower(), location.lower()).
          Notice that `source_url` is intentionally EXCLUDED in V2 so cross-source duplicates
          (e.g. same job on LinkedIn vs Indeed) are detected.
    """

    user_id: str
    opportunity_type: OpportunityType | str
    title: str
    organization: str = ""
    source_url: str = ""
    source_name: str = ""
    location: str = ""
    opportunity_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    mission_id: str | None = None
    status: OpportunityStatus = OpportunityStatus.DISCOVERED
    requirements: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
    fingerprint: str = ""
    discovered_at: datetime = field(default_factory=now_utc)
    deadline: datetime | None = None
    version: int = 1
    converted_case_id: str | None = None
    fingerprint_v2: str = ""
    external_posting_id: str | None = None

    def __post_init__(self) -> None:
        if not self.opportunity_id or not self.opportunity_id.strip():
            raise DomainValidationError("Opportunity opportunity_id cannot be empty.")
        if not self.user_id or not self.user_id.strip():
            raise DomainValidationError("Opportunity user_id cannot be empty.")
        if not self.title or not self.title.strip():
            raise DomainValidationError("Opportunity title cannot be empty.")
        if self.version < 1:
            raise DomainValidationError(f"Opportunity version must be >= 1, got {self.version}")

        # Enforce UTC timezone awareness
        self.discovered_at = ensure_utc(self.discovered_at) or now_utc()
        if self.deadline is not None:
            self.deadline = ensure_utc(self.deadline)

        # Normalize enum types
        if isinstance(self.opportunity_type, str):
            try:
                self.opportunity_type = OpportunityType(self.opportunity_type)
            except ValueError:
                pass
        if isinstance(self.status, str) and not isinstance(self.status, OpportunityStatus):
            self.status = OpportunityStatus(self.status)

        # Ensure deterministic deduplication fingerprints
        if not self.fingerprint:
            self.fingerprint = self.compute_fingerprint()
        if not self.fingerprint_v2:
            self.fingerprint_v2 = self.compute_fingerprint_v2()

    @property
    def opportunity_type_str(self) -> str:
        return (
            self.opportunity_type.value
            if isinstance(self.opportunity_type, OpportunityType)
            else str(self.opportunity_type)
        )

    def compute_fingerprint(self) -> str:
        """Compute stable SHA-256 fingerprint for identity and deduplication (V1)."""
        components = [
            FINGERPRINT_SCHEMA_V1,
            self.opportunity_type_str,
            (self.organization or "").strip().lower(),
            self.title.strip().lower(),
            (self.source_url or "").strip().lower(),
        ]
        if self.external_posting_id and self.external_posting_id.strip():
            components.append(self.external_posting_id.strip().lower())
        canonical = canonical_json_dumps(components)
        return compute_sha256(canonical)

    def compute_fingerprint_v2(self) -> str:
        """Compute stable SHA-256 fallback fingerprint for cross-source deduplication (V2).

        Always represents:
            opp_fp_v2 + opportunity_type + organization + title + location.
        Notice that external_posting_id is a separate stronger identity attribute
        and does NOT replace this fallback fingerprint.
        """
        components = [
            FINGERPRINT_SCHEMA_V2,
            self.opportunity_type_str,
            (self.organization or "").strip().lower(),
            self.title.strip().lower(),
            (self.location or "").strip().lower(),
        ]
        canonical = canonical_json_dumps(components)
        return compute_sha256(canonical)

    def compute_input_hash(self, mission_goal: str = "", mission_constraints: list[str] | None = None) -> str:
        """Compute deterministic hash of evaluation inputs (opportunity fields and mission context)."""
        raw_reqs = sorted(self.requirements)
        norm_reqs = self.metadata.get("normalized_requirements", [])
        constraints = sorted(mission_constraints or [])
        components = [
            self.title.strip(),
            (self.organization or "").strip(),
            self.opportunity_type_str,
            (self.location or "").strip(),
            (self.external_posting_id or "").strip(),
            raw_reqs,
            norm_reqs,
            mission_goal.strip(),
            constraints,
        ]
        return compute_sha256(canonical_json_dumps(components))

    def record_evaluation(self, snapshot_dict: dict[str, Any]) -> None:
        """Record versioned evaluation snapshot in metadata."""
        self.metadata["evaluation"] = snapshot_dict

    def is_evaluation_stale(self, current_input_hash: str, current_context_hash: str) -> bool:
        """Check if recorded evaluation is stale compared to current hashes."""
        eval_meta = self.metadata.get("evaluation")
        if not eval_meta or not isinstance(eval_meta, dict):
            return True
        stored_input_hash = eval_meta.get("evaluation_input_hash")
        stored_context_hash = eval_meta.get("context_snapshot_hash")
        if not stored_input_hash or not stored_context_hash:
            return True
        return stored_input_hash != current_input_hash or stored_context_hash != current_context_hash

    def transition_to(self, new_status: OpportunityStatus, reason: str | None = None) -> None:
        """Execute state machine transition with invariant check."""
        if isinstance(new_status, str) and not isinstance(new_status, OpportunityStatus):
            new_status = OpportunityStatus(new_status)

        if new_status == self.status:
            return

        if self.status.is_terminal and self.status != OpportunityStatus.ARCHIVED:
            raise InvalidStateTransitionError(
                "Opportunity",
                self.status.value,
                new_status.value,
                "Cannot transition from terminal state.",
            )

        allowed = _VALID_OPPORTUNITY_TRANSITIONS.get(self.status, set())
        if new_status not in allowed:
            raise InvalidStateTransitionError(
                "Opportunity",
                self.status.value,
                new_status.value,
                f"Valid transitions from '{self.status.value}' are: {[s.value for s in allowed]}",
            )

        self.status = new_status
        self.version += 1

    def invalidate_evaluation_for_renormalization(self) -> None:
        """Invalidate existing evaluation and return state to NORMALIZED.

        Enforces aggregate encapsulation: clears evaluation snapshot,
        sets status to NORMALIZED, and increments version exactly once.
        """
        if self.status != OpportunityStatus.EVALUATING:
            raise InvalidStateTransitionError(
                "Opportunity",
                self.status.value,
                OpportunityStatus.NORMALIZED.value,
                "Can only re-normalize from EVALUATING state.",
            )
        self.metadata.pop("evaluation", None)
        self.status = OpportunityStatus.NORMALIZED
        self.version += 1

    def to_dict(self) -> dict[str, Any]:
        """Serialize Opportunity to a JSON-compatible dictionary."""
        return {
            "opportunity_id": self.opportunity_id,
            "mission_id": self.mission_id,
            "user_id": self.user_id,
            "opportunity_type": self.opportunity_type_str,
            "title": self.title,
            "organization": self.organization,
            "source_url": self.source_url,
            "source_name": self.source_name,
            "location": self.location,
            "status": self.status.value,
            "requirements": list(self.requirements),
            "metadata": dict(self.metadata),
            "fingerprint": self.fingerprint,
            "discovered_at": to_iso_utc(self.discovered_at),
            "deadline": to_iso_utc(self.deadline),
            "version": self.version,
            "converted_case_id": self.converted_case_id,
            "fingerprint_v2": self.fingerprint_v2,
            "external_posting_id": self.external_posting_id,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Opportunity:
        """Reconstruct Opportunity from serialized dictionary."""
        return cls(
            opportunity_id=data["opportunity_id"],
            mission_id=data.get("mission_id"),
            user_id=data["user_id"],
            opportunity_type=data["opportunity_type"],
            title=data["title"],
            organization=data.get("organization", ""),
            source_url=data.get("source_url", ""),
            source_name=data.get("source_name", ""),
            location=data.get("location", ""),
            status=OpportunityStatus(data["status"]),
            requirements=list(data.get("requirements") or []),
            metadata=dict(data.get("metadata") or {}),
            fingerprint=data.get("fingerprint", ""),
            discovered_at=from_iso_utc(data["discovered_at"]) or now_utc(),
            deadline=from_iso_utc(data.get("deadline")),
            version=int(data.get("version", 1)),
            converted_case_id=data.get("converted_case_id"),
            fingerprint_v2=data.get("fingerprint_v2", ""),
            external_posting_id=data.get("external_posting_id"),
        )
