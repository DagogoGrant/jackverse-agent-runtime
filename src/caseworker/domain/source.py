"""ContextSource domain entity representing trusted provenance and origin registries."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
import uuid

from caseworker.domain.enums import SensitivityLevel, SourceType
from caseworker.domain.errors import DomainValidationError
from caseworker.domain.types import ensure_utc, from_iso_utc, now_utc, to_iso_utc


@dataclass
class ContextSource:
    """A registered provenance origin for personal context (e.g. CV upload, user profile, third-party record).

    Design Guarantees:
    - Provenance Registry: Distinct entity tracking where facts originated.
    - AGENT_INFERENCE is provenance, NOT verification (must remain unverified unless confirmed).
    - Optimistic Concurrency: Tracks monotonic `version` for safe concurrent updates.
    - Privacy: Sensitive metadata is segregated and safe representation redacts private content.
    """

    user_id: str
    title: str
    source_type: SourceType = SourceType.USER_INPUT
    source_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    source_reference: str = ""
    content_hash: str = ""
    sensitivity: SensitivityLevel = SensitivityLevel.PERSONAL
    created_at: datetime = field(default_factory=now_utc)
    updated_at: datetime = field(default_factory=now_utc)
    metadata: dict[str, Any] = field(default_factory=dict)
    version: int = 1

    def __post_init__(self) -> None:
        if not self.source_id or not self.source_id.strip():
            raise DomainValidationError("ContextSource source_id cannot be empty.")
        if not self.user_id or not self.user_id.strip():
            raise DomainValidationError("ContextSource user_id cannot be empty.")
        if not self.title or not self.title.strip():
            raise DomainValidationError("ContextSource title cannot be empty.")
        if self.version < 1:
            raise DomainValidationError(f"ContextSource version must be >= 1, got {self.version}")

        # Enforce UTC timezone awareness
        self.created_at = ensure_utc(self.created_at) or now_utc()
        self.updated_at = ensure_utc(self.updated_at) or now_utc()

        # Normalize enum types
        if isinstance(self.source_type, str) and not isinstance(self.source_type, SourceType):
            self.source_type = SourceType(self.source_type)
        if isinstance(self.sensitivity, str) and not isinstance(self.sensitivity, SensitivityLevel):
            self.sensitivity = SensitivityLevel(self.sensitivity)

    @property
    def is_inferred(self) -> bool:
        """Return True if this source originated from an agent inference."""
        return self.source_type == SourceType.AGENT_INFERENCE

    def update_metadata(self, new_metadata: dict[str, Any]) -> None:
        """Update source metadata with version bump."""
        self.metadata.update(new_metadata)
        self.updated_at = now_utc()
        self.version += 1

    def to_dict(self) -> dict[str, Any]:
        """Serialize ContextSource to a JSON-compatible dictionary for internal storage."""
        return {
            "source_id": self.source_id,
            "user_id": self.user_id,
            "source_type": self.source_type.value,
            "title": self.title,
            "source_reference": self.source_reference,
            "content_hash": self.content_hash,
            "sensitivity": self.sensitivity.value,
            "created_at": to_iso_utc(self.created_at),
            "updated_at": to_iso_utc(self.updated_at),
            "metadata": dict(self.metadata),
            "version": self.version,
        }

    def to_safe_dict(self) -> dict[str, Any]:
        """Serialize ContextSource redacting sensitive references if SENSITIVE."""
        d = self.to_dict()
        if self.sensitivity == SensitivityLevel.SENSITIVE:
            d["source_reference"] = "[REDACTED]"
            d["metadata"] = {"redacted": True}
        return d

    def to_audit_payload(self) -> dict[str, Any]:
        """Serialize ContextSource for domain events, strictly omitting raw references and metadata."""
        return {
            "source_id": self.source_id,
            "user_id": self.user_id,
            "source_type": self.source_type.value,
            "title": self.title,
            "sensitivity": self.sensitivity.value,
            "version": self.version,
        }


    def __repr__(self) -> str:
        ref_display = "[REDACTED]" if self.sensitivity == SensitivityLevel.SENSITIVE else repr(self.source_reference)
        return (
            f"ContextSource(source_id={self.source_id!r}, user_id={self.user_id!r}, "
            f"title={self.title!r}, source_type={self.source_type.value!r}, "
            f"source_reference={ref_display}, sensitivity={self.sensitivity.value!r}, version={self.version})"
        )

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ContextSource:
        """Reconstruct ContextSource from serialized dictionary."""
        return cls(
            source_id=data["source_id"],
            user_id=data["user_id"],
            source_type=SourceType(data["source_type"]),
            title=data["title"],
            source_reference=data.get("source_reference", ""),
            content_hash=data.get("content_hash", ""),
            sensitivity=SensitivityLevel(data.get("sensitivity", SensitivityLevel.PERSONAL.value)),
            created_at=from_iso_utc(data["created_at"]) or now_utc(),
            updated_at=from_iso_utc(data["updated_at"]) or now_utc(),
            metadata=dict(data.get("metadata") or {}),
            version=int(data.get("version", 1)),
        )
