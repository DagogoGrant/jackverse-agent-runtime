"""ContextFact domain entity representing persistent, verifiable user context."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
import uuid

from caseworker.domain.enums import SensitivityLevel, SourceType, VerificationStatus
from caseworker.domain.errors import DomainValidationError
from caseworker.domain.types import ensure_utc, from_iso_utc, now_utc, to_iso_utc


@dataclass
class ContextFact:
    """A durable item of personal context (e.g. bio, education, preferences, constraints).

    Milestone 2 Invariants:
    - Provenance: explicit `source_type` (SourceType enum), optional `source_reference`, and optional `source_id` referencing a ContextSource.
    - Confidence: float (0.0 to 1.0), decoupled from `verification_status`.
    - Verification: `verification_status` (VerificationStatus enum: UNVERIFIED, USER_VERIFIED, SOURCE_VERIFIED, REJECTED).
    - Privacy & Gating: `sensitivity` (SensitivityLevel enum) and `allowed_purposes` list.
    - Historical Lineage: When superseded, facts are preserved with `superseded_by_fact_id` and `superseded_at`.
    - Safe Display: Values marked SENSITIVE are redacted from `__repr__` and `to_safe_dict()` to prevent credential or PII leaks in logs.
    """

    user_id: str
    namespace: str
    key: str
    value: Any
    fact_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    source_type: SourceType = SourceType.USER_INPUT
    source_reference: str = ""
    source_id: str | None = None
    confidence: float = 1.0
    verification_status: VerificationStatus = VerificationStatus.UNVERIFIED
    rejection_reason: str | None = None
    sensitivity: SensitivityLevel = SensitivityLevel.PERSONAL
    allowed_purposes: list[str] = field(default_factory=list)
    created_at: datetime = field(default_factory=now_utc)
    updated_at: datetime = field(default_factory=now_utc)
    expires_at: datetime | None = None
    superseded_by_fact_id: str | None = None
    superseded_at: datetime | None = None
    version: int = 1

    def __post_init__(self) -> None:
        if not self.fact_id or not self.fact_id.strip():
            raise DomainValidationError("ContextFact fact_id cannot be empty.")
        if not self.user_id or not self.user_id.strip():
            raise DomainValidationError("ContextFact user_id cannot be empty.")
        if not self.namespace or not self.namespace.strip():
            raise DomainValidationError("ContextFact namespace cannot be empty.")
        if not self.key or not self.key.strip():
            raise DomainValidationError("ContextFact key cannot be empty.")
        if not (0.0 <= self.confidence <= 1.0):
            raise DomainValidationError(f"ContextFact confidence must be between 0.0 and 1.0, got {self.confidence}")
        if self.version < 1:
            raise DomainValidationError(f"ContextFact version must be >= 1, got {self.version}")

        # Enforce UTC timezone awareness
        self.created_at = ensure_utc(self.created_at) or now_utc()
        self.updated_at = ensure_utc(self.updated_at) or now_utc()
        if self.expires_at is not None:
            self.expires_at = ensure_utc(self.expires_at)
        if self.superseded_at is not None:
            self.superseded_at = ensure_utc(self.superseded_at)

        # Normalize enum types
        if isinstance(self.source_type, str) and not isinstance(self.source_type, SourceType):
            self.source_type = SourceType(self.source_type)
        if isinstance(self.verification_status, str) and not isinstance(self.verification_status, VerificationStatus):
            self.verification_status = VerificationStatus(self.verification_status)
        if isinstance(self.sensitivity, str) and not isinstance(self.sensitivity, SensitivityLevel):
            self.sensitivity = SensitivityLevel(self.sensitivity)

    @property
    def is_active(self) -> bool:
        """Check whether the fact is currently active (not superseded, not expired, and not rejected)."""
        if self.is_superseded:
            return False
        if self.is_rejected:
            return False
        if self.is_expired:
            return False
        return True

    @property
    def is_expired(self) -> bool:
        """Check whether the fact has expired."""
        return self.expires_at is not None and now_utc() > self.expires_at

    @property
    def is_superseded(self) -> bool:
        """Check whether the fact has been superseded by a newer fact."""
        return self.superseded_by_fact_id is not None

    @property
    def is_rejected(self) -> bool:
        """Check whether the fact has been explicitly rejected."""
        return self.verification_status == VerificationStatus.REJECTED

    def is_valid_for_purpose(self, purpose: str) -> bool:
        """Evaluate purpose-based access gating."""
        if not purpose or not purpose.strip():
            return False
        if not self.is_active:
            return False
        if self.sensitivity == SensitivityLevel.PUBLIC:
            return True
        if not self.allowed_purposes:
            # If not explicitly restricted, allowed within user domain unless SENSITIVE
            return self.sensitivity != SensitivityLevel.SENSITIVE
        return purpose.strip().lower() in [p.strip().lower() for p in self.allowed_purposes]

    def verify(self, status: VerificationStatus = VerificationStatus.USER_VERIFIED) -> None:
        """Update verification status with timestamp update and version bump."""
        if isinstance(status, str) and not isinstance(status, VerificationStatus):
            status = VerificationStatus(status)
        if status == VerificationStatus.REJECTED:
            self.reject("Verified as rejected.")
            return
        self.verification_status = status
        self.rejection_reason = None
        self.updated_at = now_utc()
        self.version += 1

    def reject(self, reason: str | None = None) -> None:
        """Mark this fact as rejected with an optional explanation."""
        self.verification_status = VerificationStatus.REJECTED
        self.rejection_reason = reason
        self.updated_at = now_utc()
        self.version += 1

    def supersede(self, new_fact_id: str) -> None:
        """Mark this fact as superseded by a newer fact in the user's vault."""
        if not new_fact_id or not new_fact_id.strip():
            raise DomainValidationError("superseded_by_fact_id cannot be empty.")
        if self.superseded_by_fact_id is not None:
            raise DomainValidationError(
                f"Fact '{self.fact_id}' has already been superseded by '{self.superseded_by_fact_id}'."
            )
        self.superseded_by_fact_id = new_fact_id.strip()
        self.superseded_at = now_utc()
        self.updated_at = self.superseded_at
        self.version += 1

    def to_dict(self) -> dict[str, Any]:
        """Serialize ContextFact to a JSON-compatible dictionary for internal storage."""
        return {
            "fact_id": self.fact_id,
            "user_id": self.user_id,
            "namespace": self.namespace,
            "key": self.key,
            "value": self.value,
            "source_type": self.source_type.value,
            "source_reference": self.source_reference,
            "source_id": self.source_id,
            "confidence": self.confidence,
            "verification_status": self.verification_status.value,
            "rejection_reason": self.rejection_reason,
            "sensitivity": self.sensitivity.value,
            "allowed_purposes": list(self.allowed_purposes),
            "created_at": to_iso_utc(self.created_at),
            "updated_at": to_iso_utc(self.updated_at),
            "expires_at": to_iso_utc(self.expires_at),
            "superseded_by_fact_id": self.superseded_by_fact_id,
            "superseded_at": to_iso_utc(self.superseded_at),
            "version": self.version,
        }

    def to_safe_dict(self) -> dict[str, Any]:
        """Serialize ContextFact for safe display/logging, redacting sensitive values."""
        d = self.to_dict()
        if self.sensitivity == SensitivityLevel.SENSITIVE:
            d["value"] = "[REDACTED]"
            d["source_reference"] = "[REDACTED]"
        return d

    def to_audit_payload(self) -> dict[str, Any]:
        """Serialize ContextFact for domain events, strictly omitting raw values and private references."""
        return {
            "fact_id": self.fact_id,
            "user_id": self.user_id,
            "namespace": self.namespace,
            "key": self.key,
            "source_type": self.source_type.value,
            "source_id": self.source_id,
            "confidence": self.confidence,
            "verification_status": self.verification_status.value,
            "sensitivity": self.sensitivity.value,
            "allowed_purposes": list(self.allowed_purposes),
            "version": self.version,
        }


    def __repr__(self) -> str:
        val_display = "[REDACTED]" if self.sensitivity == SensitivityLevel.SENSITIVE else repr(self.value)
        return (
            f"ContextFact(fact_id={self.fact_id!r}, user_id={self.user_id!r}, "
            f"namespace={self.namespace!r}, key={self.key!r}, value={val_display}, "
            f"status={self.verification_status.value!r}, sensitivity={self.sensitivity.value!r}, "
            f"active={self.is_active}, version={self.version})"
        )

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ContextFact:
        """Reconstruct ContextFact from serialized dictionary with backwards compatibility."""
        return cls(
            fact_id=data["fact_id"],
            user_id=data["user_id"],
            namespace=data["namespace"],
            key=data["key"],
            value=data["value"],
            source_type=SourceType(data["source_type"]),
            source_reference=data.get("source_reference", ""),
            source_id=data.get("source_id"),
            confidence=float(data.get("confidence", 1.0)),
            verification_status=VerificationStatus(data["verification_status"]),
            rejection_reason=data.get("rejection_reason"),
            sensitivity=SensitivityLevel(data["sensitivity"]),
            allowed_purposes=list(data.get("allowed_purposes") or []),
            created_at=from_iso_utc(data["created_at"]) or now_utc(),
            updated_at=from_iso_utc(data["updated_at"]) or now_utc(),
            expires_at=from_iso_utc(data.get("expires_at")),
            superseded_by_fact_id=data.get("superseded_by_fact_id"),
            superseded_at=from_iso_utc(data.get("superseded_at")),
            version=int(data.get("version", 1)),
        )
