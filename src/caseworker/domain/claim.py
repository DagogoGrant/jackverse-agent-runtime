"""Claim domain entity representing verified or unverified factual assertions in the Claim Ledger."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
import uuid

from caseworker.domain.enums import ClaimStatus
from caseworker.domain.errors import DomainValidationError, InvalidStateTransitionError
from caseworker.domain.types import ensure_utc, from_iso_utc, now_utc, to_iso_utc


_VALID_CLAIM_TRANSITIONS: dict[ClaimStatus, set[ClaimStatus]] = {
    ClaimStatus.PROPOSED: {
        ClaimStatus.SUPPORTED,
        ClaimStatus.UNSUPPORTED,
        ClaimStatus.CONFLICTED,
        ClaimStatus.REJECTED,
        ClaimStatus.EXPIRED,
    },
    ClaimStatus.SUPPORTED: {
        ClaimStatus.UNSUPPORTED,
        ClaimStatus.CONFLICTED,
        ClaimStatus.REJECTED,
        ClaimStatus.EXPIRED,
    },
    ClaimStatus.UNSUPPORTED: {
        ClaimStatus.SUPPORTED,
        ClaimStatus.CONFLICTED,
        ClaimStatus.REJECTED,
        ClaimStatus.EXPIRED,
    },
    ClaimStatus.CONFLICTED: {
        ClaimStatus.SUPPORTED,
        ClaimStatus.UNSUPPORTED,
        ClaimStatus.REJECTED,
        ClaimStatus.EXPIRED,
    },
    ClaimStatus.REJECTED: set(),
    ClaimStatus.EXPIRED: set(),
}


@dataclass
class Claim:
    """A factual assertion intended for an external application or artifact.

    Architectural Principle:
        The LLM must never be allowed to invent personal facts.
        Every external assertion (e.g. 'I reduced processing time by 40%') must be
        anchored in explicit, active, verified ContextFacts in the user's vault.
    """

    user_id: str
    purpose: str
    text: str
    claim_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    case_id: str | None = None
    mission_id: str | None = None
    status: ClaimStatus = ClaimStatus.PROPOSED
    supporting_fact_ids: list[str] = field(default_factory=list)
    created_at: datetime = field(default_factory=now_utc)
    updated_at: datetime = field(default_factory=now_utc)
    verified_at: datetime | None = None
    rejection_reason: str | None = None
    version: int = 1

    def __post_init__(self) -> None:
        if not self.claim_id or not self.claim_id.strip():
            raise DomainValidationError("Claim claim_id cannot be empty.")
        if not self.user_id or not self.user_id.strip():
            raise DomainValidationError("Claim user_id cannot be empty.")
        if not self.purpose or not self.purpose.strip():
            raise DomainValidationError("Claim purpose cannot be empty.")
        if not self.text or not self.text.strip():
            raise DomainValidationError("Claim text cannot be empty.")
        if self.version < 1:
            raise DomainValidationError(f"Claim version must be >= 1, got {self.version}")

        # Enforce UTC timezone awareness
        self.created_at = ensure_utc(self.created_at) or now_utc()
        self.updated_at = ensure_utc(self.updated_at) or now_utc()
        if self.verified_at is not None:
            self.verified_at = ensure_utc(self.verified_at)

        # Normalize enum types
        if isinstance(self.status, str) and not isinstance(self.status, ClaimStatus):
            self.status = ClaimStatus(self.status)

    @property
    def is_supported(self) -> bool:
        return self.status == ClaimStatus.SUPPORTED

    @property
    def is_terminal(self) -> bool:
        return self.status.is_terminal

    def transition_to(self, new_status: ClaimStatus, reason: str | None = None) -> None:
        """Execute state transition with invariant validation and version bump."""
        if isinstance(new_status, str) and not isinstance(new_status, ClaimStatus):
            new_status = ClaimStatus(new_status)

        if new_status == self.status:
            return

        if self.is_terminal:
            raise InvalidStateTransitionError(
                "Claim",
                self.status.value,
                new_status.value,
                "Cannot transition from terminal state.",
            )

        allowed = _VALID_CLAIM_TRANSITIONS.get(self.status, set())
        if new_status not in allowed:
            raise InvalidStateTransitionError(
                "Claim",
                self.status.value,
                new_status.value,
                f"Valid transitions from '{self.status.value}' are: {[s.value for s in allowed]}",
            )

        self.status = new_status
        self.rejection_reason = reason
        self.updated_at = now_utc()
        self.version += 1

    def set_supported(self, verified_at: datetime | None = None) -> None:
        """Mark claim as verified and supported by valid context facts."""
        self.transition_to(ClaimStatus.SUPPORTED)
        self.verified_at = ensure_utc(verified_at) or now_utc()
        self.rejection_reason = None

    def set_unsupported(self, reason: str | None = None) -> None:
        """Mark claim as unsupported by current context facts."""
        self.transition_to(ClaimStatus.UNSUPPORTED, reason=reason)
        self.verified_at = None

    def set_conflicted(self, reason: str | None = None) -> None:
        """Mark claim as conflicted due to contradictory supporting facts."""
        self.transition_to(ClaimStatus.CONFLICTED, reason=reason)
        self.verified_at = None

    def reject(self, reason: str | None = None) -> None:
        """Explicitly reject claim."""
        self.transition_to(ClaimStatus.REJECTED, reason=reason)

    def expire(self, reason: str | None = None) -> None:
        """Mark claim as expired."""
        self.transition_to(ClaimStatus.EXPIRED, reason=reason)

    def to_dict(self) -> dict[str, Any]:
        """Serialize Claim to a JSON-compatible dictionary."""
        return {
            "claim_id": self.claim_id,
            "user_id": self.user_id,
            "purpose": self.purpose,
            "text": self.text,
            "case_id": self.case_id,
            "mission_id": self.mission_id,
            "status": self.status.value,
            "supporting_fact_ids": list(self.supporting_fact_ids),
            "created_at": to_iso_utc(self.created_at),
            "updated_at": to_iso_utc(self.updated_at),
            "verified_at": to_iso_utc(self.verified_at),
            "rejection_reason": self.rejection_reason,
            "version": self.version,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Claim:
        """Reconstruct Claim from serialized dictionary."""
        return cls(
            claim_id=data["claim_id"],
            user_id=data["user_id"],
            purpose=data["purpose"],
            text=data["text"],
            case_id=data.get("case_id"),
            mission_id=data.get("mission_id"),
            status=ClaimStatus(data["status"]),
            supporting_fact_ids=list(data.get("supporting_fact_ids") or []),
            created_at=from_iso_utc(data["created_at"]) or now_utc(),
            updated_at=from_iso_utc(data["updated_at"]) or now_utc(),
            verified_at=from_iso_utc(data.get("verified_at")),
            rejection_reason=data.get("rejection_reason"),
            version=int(data.get("version", 1)),
        )
