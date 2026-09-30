"""ContextPackage entity and ContextPackageBuilder for purpose-scoped context delivery."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
import uuid

from caseworker.domain.context import ContextFact
from caseworker.domain.errors import DomainValidationError
from caseworker.domain.namespaces import matches_namespace_filter
from caseworker.domain.types import (
    canonical_json_dumps,
    compute_sha256,
    ensure_utc,
    from_iso_utc,
    now_utc,
    to_iso_utc,
)
from caseworker.domain.vault_policy import AccessDecision, ContextAccessPolicy

CONTEXT_PACKAGE_SCHEMA_V1 = "ctx_pkg_v1"


@dataclass
class ContextPackage:
    """A prepared, immutable bundle of personal context authorized for a specific task.

    Guarantees:
    - Structured Context: Contains typed facts, not prompt instructions.
    - Cryptographic Fingerprint: Deterministically binds package content and purpose.
    - Zero Leakage: Only authorized, active facts matching the requested purpose are included.
    """

    user_id: str
    purpose: str
    facts: list[ContextFact] = field(default_factory=list)
    package_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    generated_at: datetime = field(default_factory=now_utc)
    expires_at: datetime | None = None
    fingerprint: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.package_id or not self.package_id.strip():
            raise DomainValidationError("ContextPackage package_id cannot be empty.")
        if not self.user_id or not self.user_id.strip():
            raise DomainValidationError("ContextPackage user_id cannot be empty.")
        if not self.purpose or not self.purpose.strip():
            raise DomainValidationError("ContextPackage purpose cannot be empty.")

        # Enforce UTC timezone awareness
        self.generated_at = ensure_utc(self.generated_at) or now_utc()
        if self.expires_at is not None:
            self.expires_at = ensure_utc(self.expires_at)

        # Ensure deterministic fingerprint
        if not self.fingerprint:
            self.fingerprint = self.compute_fingerprint()

    @property
    def is_expired(self) -> bool:
        return self.expires_at is not None and now_utc() > self.expires_at

    def compute_fingerprint(self) -> str:
        """Compute stable SHA-256 fingerprint for identity and tamper detection."""
        sorted_facts = sorted(self.facts, key=lambda f: (f.namespace, f.key, f.fact_id))
        components = [
            CONTEXT_PACKAGE_SCHEMA_V1,
            self.package_id,
            self.user_id,
            self.purpose.strip().lower(),
            [f.fact_id for f in sorted_facts],
            [f.to_dict() for f in sorted_facts],
        ]
        return compute_sha256(canonical_json_dumps(components))

    def to_dict(self) -> dict[str, Any]:
        """Serialize ContextPackage to a JSON-compatible dictionary."""
        return {
            "package_id": self.package_id,
            "user_id": self.user_id,
            "purpose": self.purpose,
            "facts": [f.to_dict() for f in self.facts],
            "generated_at": to_iso_utc(self.generated_at),
            "expires_at": to_iso_utc(self.expires_at),
            "fingerprint": self.fingerprint,
            "metadata": dict(self.metadata),
        }

    def to_safe_dict(self) -> dict[str, Any]:
        """Serialize ContextPackage with redacted sensitive fact contents for display."""
        return {
            "package_id": self.package_id,
            "user_id": self.user_id,
            "purpose": self.purpose,
            "facts": [f.to_safe_dict() for f in self.facts],
            "generated_at": to_iso_utc(self.generated_at),
            "expires_at": to_iso_utc(self.expires_at),
            "fingerprint": self.fingerprint,
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ContextPackage:
        """Reconstruct ContextPackage from serialized dictionary."""
        facts = [ContextFact.from_dict(f) for f in data.get("facts", [])]
        return cls(
            package_id=data["package_id"],
            user_id=data["user_id"],
            purpose=data["purpose"],
            facts=facts,
            generated_at=from_iso_utc(data["created_at"]) if "created_at" in data else from_iso_utc(data["generated_at"]) or now_utc(),
            expires_at=from_iso_utc(data.get("expires_at")),
            fingerprint=data.get("fingerprint", ""),
            metadata=dict(data.get("metadata") or {}),
        )


class ContextPackageBuilder:
    """Constructs verifiable, purpose-filtered ContextPackages from user facts."""

    def __init__(self, policy: ContextAccessPolicy | None = None) -> None:
        self.policy = policy or ContextAccessPolicy()

    def build_with_report(
        self,
        user_id: str,
        purpose: str,
        available_facts: list[ContextFact],
        namespaces: list[str] | None = None,
        expires_at: datetime | None = None,
        require_verified: bool = False,
    ) -> tuple[ContextPackage, list[AccessDecision], list[AccessDecision]]:
        """Filter facts and produce a deterministic ContextPackage along with granted and denied audit decisions."""
        filtered_facts: list[ContextFact] = []
        granted_decisions: list[AccessDecision] = []
        denied_decisions: list[AccessDecision] = []

        for fact in available_facts:
            # 1. User ownership check (cross-user facts silently dropped from candidates)
            if fact.user_id != user_id:
                continue

            # 2. Namespace filtering (if requested)
            if namespaces:
                if not any(matches_namespace_filter(fact.namespace, ns) for ns in namespaces):
                    continue

            # 3. Policy evaluation
            decision = self.policy.evaluate_access(
                fact=fact,
                requested_purpose=purpose,
                require_verified=require_verified,
            )
            if decision.is_granted:
                filtered_facts.append(fact)
                granted_decisions.append(decision)
            else:
                denied_decisions.append(decision)

        # Deterministic order
        filtered_facts.sort(key=lambda f: (f.namespace, f.key, f.fact_id))

        package = ContextPackage(
            user_id=user_id,
            purpose=purpose,
            facts=filtered_facts,
            expires_at=expires_at,
            metadata={
                "requested_namespaces": list(namespaces or []),
                "fact_count": len(filtered_facts),
            },
        )
        return package, granted_decisions, denied_decisions

    def build(
        self,
        user_id: str,
        purpose: str,
        available_facts: list[ContextFact],
        namespaces: list[str] | None = None,
        expires_at: datetime | None = None,
        require_verified: bool = False,
    ) -> ContextPackage:
        """Filter facts and produce a deterministic ContextPackage for the target task."""
        package, _, _ = self.build_with_report(
            user_id=user_id,
            purpose=purpose,
            available_facts=available_facts,
            namespaces=namespaces,
            expires_at=expires_at,
            require_verified=require_verified,
        )
        return package

