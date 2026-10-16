"""PreparationPackage domain aggregate representing an immutable snapshot of verified materials."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
import uuid

from caseworker.domain.enums import (
    ArtifactPublicationRole,
    PackageVerificationStatus,
    PreparationPackageStatus,
)
from caseworker.domain.errors import DomainValidationError, InvalidStateTransitionError
from caseworker.domain.types import (
    canonical_json_dumps,
    compute_sha256,
    ensure_utc,
    from_iso_utc,
    now_utc,
    to_iso_utc,
)

SCHEMA_VERSION = "prep_pkg_v1"
VERIFIER_VERSION = "package_verifier_v1"


@dataclass(frozen=True)
class ArtifactPackageSnapshot:
    """Immutable snapshot of a frozen artifact included in a PreparationPackage."""

    artifact_id: str
    artifact_version: int
    material_type: str
    rendered_payload_hash: str
    grounding_snapshot_hash: str
    publication_role: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "artifact_id": self.artifact_id,
            "artifact_version": self.artifact_version,
            "material_type": self.material_type,
            "rendered_payload_hash": self.rendered_payload_hash,
            "grounding_snapshot_hash": self.grounding_snapshot_hash,
            "publication_role": self.publication_role,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ArtifactPackageSnapshot:
        return cls(
            artifact_id=data["artifact_id"],
            artifact_version=int(data["artifact_version"]),
            material_type=data["material_type"],
            rendered_payload_hash=data["rendered_payload_hash"],
            grounding_snapshot_hash=data.get("grounding_snapshot_hash", ""),
            publication_role=data.get("publication_role", ArtifactPublicationRole.SUBMISSION_MATERIAL.value),
        )


@dataclass(frozen=True)
class SafeClaimSnapshot:
    """Safe, privacy-preserving snapshot of a claim referenced by packaged artifacts.

    Contains zero raw ContextFact or personal identification values.
    """

    claim_id: str
    claim_version: int
    claim_status: str
    support_snapshot_hash: str | None
    assertion_hash: str | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "claim_id": self.claim_id,
            "claim_version": self.claim_version,
            "claim_status": self.claim_status,
            "support_snapshot_hash": self.support_snapshot_hash,
            "assertion_hash": self.assertion_hash,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SafeClaimSnapshot:
        return cls(
            claim_id=data["claim_id"],
            claim_version=int(data["claim_version"]),
            claim_status=data["claim_status"],
            support_snapshot_hash=data.get("support_snapshot_hash"),
            assertion_hash=data.get("assertion_hash"),
        )


def compute_package_fingerprint(
    case_id: str,
    artifact_set_id: str,
    source_report_id: str,
    artifact_snapshots: list[ArtifactPackageSnapshot],
    claim_snapshots: list[SafeClaimSnapshot],
    schema_version: str = SCHEMA_VERSION,
    verifier_version: str = VERIFIER_VERSION,
) -> str:
    """Compute content-addressed SHA-256 fingerprint for a PreparationPackage.

    Invariant Rules:
    - Content-addressed only.
    - DO NOT include package_id, status, version, timestamps, or decision reason.
    - Two independent packages with identical content must yield identical fingerprints.
    """
    sorted_artifacts = [
        [
            s.artifact_id,
            s.artifact_version,
            s.material_type,
            s.rendered_payload_hash,
            s.grounding_snapshot_hash,
            s.publication_role,
        ]
        for s in sorted(artifact_snapshots, key=lambda a: (a.material_type, a.artifact_id))
    ]
    sorted_claims = [
        [
            c.claim_id,
            c.claim_version,
            c.claim_status,
            c.support_snapshot_hash or "",
            c.assertion_hash or "",
        ]
        for c in sorted(claim_snapshots, key=lambda c: c.claim_id)
    ]

    canonical_payload = [
        schema_version,
        verifier_version,
        case_id,
        artifact_set_id,
        source_report_id,
        sorted_artifacts,
        sorted_claims,
    ]
    return compute_sha256(canonical_json_dumps(canonical_payload))


_VALID_PACKAGE_TRANSITIONS: dict[PreparationPackageStatus, set[PreparationPackageStatus]] = {
    PreparationPackageStatus.FROZEN: {
        PreparationPackageStatus.APPROVED,
        PreparationPackageStatus.REJECTED,
        PreparationPackageStatus.SUPERSEDED,
    },
    PreparationPackageStatus.APPROVED: {
        PreparationPackageStatus.SUPERSEDED,
    },
    PreparationPackageStatus.REJECTED: set(),
    PreparationPackageStatus.SUPERSEDED: set(),
}


@dataclass
class PreparationPackage:
    """An immutable, verified application materials snapshot ready for human governance.

    Constitutional boundary:
    - Authorizing this package approves these exact local materials.
    - It does NOT authorize external transmission or submission.
    """

    case_id: str
    artifact_set_id: str
    source_report_id: str
    user_id: str
    artifact_ids: list[str]
    artifact_versions: dict[str, int]
    artifact_snapshots: list[ArtifactPackageSnapshot]
    claim_snapshots: list[SafeClaimSnapshot]
    rendered_payload_hashes: dict[str, str]
    package_id: str = field(default_factory=lambda: f"pkg-{uuid.uuid4().hex[:16]}")
    status: PreparationPackageStatus = PreparationPackageStatus.FROZEN
    verification_status: PackageVerificationStatus = PackageVerificationStatus.VERIFIED
    verification_findings: list[dict[str, Any]] = field(default_factory=list)
    frozen_at: datetime = field(default_factory=now_utc)
    package_fingerprint: str = ""
    schema_version: str = SCHEMA_VERSION
    verifier_version: str = VERIFIER_VERSION
    created_at: datetime = field(default_factory=now_utc)
    updated_at: datetime = field(default_factory=now_utc)
    reason: str | None = None
    version: int = 1

    def __post_init__(self) -> None:
        if not self.package_id or not self.package_id.strip():
            raise DomainValidationError("PreparationPackage package_id cannot be empty.")
        if not self.case_id or not self.case_id.strip():
            raise DomainValidationError("PreparationPackage case_id cannot be empty.")
        if not self.artifact_set_id or not self.artifact_set_id.strip():
            raise DomainValidationError("PreparationPackage artifact_set_id cannot be empty.")
        if not self.source_report_id or not self.source_report_id.strip():
            raise DomainValidationError("PreparationPackage source_report_id cannot be empty.")
        if not self.user_id or not self.user_id.strip():
            raise DomainValidationError("PreparationPackage user_id cannot be empty.")
        if not self.artifact_ids:
            raise DomainValidationError("PreparationPackage must contain at least one artifact_id.")
        if self.version < 1:
            raise DomainValidationError(f"PreparationPackage version must be >= 1, got {self.version}")

        self.frozen_at = ensure_utc(self.frozen_at) or now_utc()
        self.created_at = ensure_utc(self.created_at) or now_utc()
        self.updated_at = ensure_utc(self.updated_at) or now_utc()

        if isinstance(self.status, str) and not isinstance(self.status, PreparationPackageStatus):
            self.status = PreparationPackageStatus(self.status)
        if isinstance(self.verification_status, str) and not isinstance(self.verification_status, PackageVerificationStatus):
            self.verification_status = PackageVerificationStatus(self.verification_status)

        # Compute or verify content-addressed fingerprint
        computed_fp = self.compute_fingerprint()
        if not self.package_fingerprint:
            self.package_fingerprint = computed_fp
        elif self.package_fingerprint != computed_fp:
            raise DomainValidationError(
                f"Package fingerprint mismatch (stored '{self.package_fingerprint}' vs computed '{computed_fp}')."
            )

    def compute_fingerprint(self) -> str:
        """Compute content-addressed fingerprint for this package snapshot."""
        return compute_package_fingerprint(
            case_id=self.case_id,
            artifact_set_id=self.artifact_set_id,
            source_report_id=self.source_report_id,
            artifact_snapshots=self.artifact_snapshots,
            claim_snapshots=self.claim_snapshots,
            schema_version=self.schema_version,
            verifier_version=self.verifier_version,
        )

    def approve(self, reason: str | None = None) -> None:
        """Record human approval for this exact package."""
        self.transition_to(PreparationPackageStatus.APPROVED, reason=reason)

    def reject(self, reason: str | None = None) -> None:
        """Record human rejection of this package."""
        self.transition_to(PreparationPackageStatus.REJECTED, reason=reason)

    def mark_superseded(self, reason: str | None = None) -> None:
        """Mark package superseded when dependencies become stale or redrafting occurs."""
        self.transition_to(PreparationPackageStatus.SUPERSEDED, reason=reason)

    def transition_to(self, new_status: PreparationPackageStatus, reason: str | None = None) -> None:
        """Execute state transition with invariant checks."""
        if isinstance(new_status, str) and not isinstance(new_status, PreparationPackageStatus):
            new_status = PreparationPackageStatus(new_status)

        if new_status == self.status:
            return

        allowed = _VALID_PACKAGE_TRANSITIONS.get(self.status, set())
        if new_status not in allowed:
            raise InvalidStateTransitionError(
                "PreparationPackage",
                self.status.value,
                new_status.value,
                f"Valid transitions from '{self.status.value}' are: {[s.value for s in allowed]}",
            )

        self.status = new_status
        self.reason = reason
        self.version += 1
        self.updated_at = now_utc()

    def assert_immutable_content_matches(self, other: PreparationPackage) -> None:
        """Enforce that content-addressed fields have not been altered."""
        if (
            self.user_id != other.user_id
            or self.case_id != other.case_id
            or self.artifact_set_id != other.artifact_set_id
            or self.source_report_id != other.source_report_id
            or self.artifact_ids != other.artifact_ids
            or self.artifact_versions != other.artifact_versions
            or self.artifact_snapshots != other.artifact_snapshots
            or self.claim_snapshots != other.claim_snapshots
            or self.rendered_payload_hashes != other.rendered_payload_hashes
            or self.verification_findings != other.verification_findings
            or to_iso_utc(self.frozen_at) != to_iso_utc(other.frozen_at)
            or self.package_fingerprint != other.package_fingerprint
            or self.schema_version != other.schema_version
            or self.verifier_version != other.verifier_version
        ):
            raise DomainValidationError(
                f"Cannot mutate content-addressed fields of PreparationPackage '{self.package_id}'."
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "package_id": self.package_id,
            "case_id": self.case_id,
            "artifact_set_id": self.artifact_set_id,
            "source_report_id": self.source_report_id,
            "user_id": self.user_id,
            "status": self.status.value,
            "artifact_ids": list(self.artifact_ids),
            "artifact_versions": dict(self.artifact_versions),
            "artifact_snapshots": [s.to_dict() for s in self.artifact_snapshots],
            "claim_snapshots": [c.to_dict() for c in self.claim_snapshots],
            "rendered_payload_hashes": dict(self.rendered_payload_hashes),
            "verification_status": self.verification_status.value,
            "verification_findings": list(self.verification_findings),
            "frozen_at": to_iso_utc(self.frozen_at),
            "package_fingerprint": self.package_fingerprint,
            "schema_version": self.schema_version,
            "verifier_version": self.verifier_version,
            "created_at": to_iso_utc(self.created_at),
            "updated_at": to_iso_utc(self.updated_at),
            "reason": self.reason,
            "version": self.version,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> PreparationPackage:
        artifact_snaps = [
            ArtifactPackageSnapshot.from_dict(s)
            for s in (data.get("artifact_snapshots") or [])
        ]
        claim_snaps = [
            SafeClaimSnapshot.from_dict(c)
            for c in (data.get("claim_snapshots") or [])
        ]
        return cls(
            package_id=data["package_id"],
            case_id=data["case_id"],
            artifact_set_id=data["artifact_set_id"],
            source_report_id=data["source_report_id"],
            user_id=data["user_id"],
            status=PreparationPackageStatus(data.get("status", PreparationPackageStatus.FROZEN)),
            artifact_ids=list(data.get("artifact_ids") or []),
            artifact_versions=dict(data.get("artifact_versions") or {}),
            artifact_snapshots=artifact_snaps,
            claim_snapshots=claim_snaps,
            rendered_payload_hashes=dict(data.get("rendered_payload_hashes") or {}),
            verification_status=PackageVerificationStatus(data.get("verification_status", PackageVerificationStatus.VERIFIED)),
            verification_findings=list(data.get("verification_findings") or []),
            frozen_at=from_iso_utc(data["frozen_at"]) or now_utc(),
            package_fingerprint=data.get("package_fingerprint", ""),
            schema_version=data.get("schema_version", SCHEMA_VERSION),
            verifier_version=data.get("verifier_version", VERIFIER_VERSION),
            created_at=from_iso_utc(data["created_at"]) or now_utc(),
            updated_at=from_iso_utc(data["updated_at"]) or now_utc(),
            reason=data.get("reason"),
            version=int(data.get("version", 1)),
        )
