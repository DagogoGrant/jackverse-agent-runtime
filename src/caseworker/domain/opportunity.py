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

_VALID_OPPORTUNITY_TRANSITIONS: dict[OpportunityStatus, set[OpportunityStatus]] = {
    OpportunityStatus.DISCOVERED: {
        OpportunityStatus.NORMALIZED,
        OpportunityStatus.EVALUATING,
        OpportunityStatus.ARCHIVED,
        OpportunityStatus.REJECTED,
    },
    OpportunityStatus.NORMALIZED: {
        OpportunityStatus.EVALUATING,
        OpportunityStatus.SHORTLISTED,
        OpportunityStatus.ARCHIVED,
        OpportunityStatus.REJECTED,
    },
    OpportunityStatus.EVALUATING: {
        OpportunityStatus.SHORTLISTED,
        OpportunityStatus.CONVERTED_TO_CASE,
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
        The deduplication fingerprint is deterministically computed from:
        (schema_version, opportunity_type, organization.lower(), title.lower(), source_url.lower()).
        Notice that `mission_id` and `discovered_at` are intentionally EXCLUDED so the same
        real-world opportunity can be recognized across multiple missions and discovery runs.
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

        # Ensure deterministic deduplication fingerprint
        if not self.fingerprint:
            self.fingerprint = self.compute_fingerprint()

    @property
    def opportunity_type_str(self) -> str:
        return (
            self.opportunity_type.value
            if isinstance(self.opportunity_type, OpportunityType)
            else str(self.opportunity_type)
        )

    def compute_fingerprint(self) -> str:
        """Compute stable SHA-256 fingerprint for identity and deduplication."""
        components = [
            FINGERPRINT_SCHEMA_V1,
            self.opportunity_type_str,
            (self.organization or "").strip().lower(),
            self.title.strip().lower(),
            (self.source_url or "").strip().lower(),
        ]
        canonical = canonical_json_dumps(components)
        return compute_sha256(canonical)

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
        )
