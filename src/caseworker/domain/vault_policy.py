"""Purpose-aware access policies and evaluation rules for Personal Context Vault."""

from __future__ import annotations

from dataclasses import dataclass

from caseworker.domain.context import ContextFact
from caseworker.domain.enums import SensitivityLevel, VerificationStatus


@dataclass(frozen=True)
class AccessDecision:
    """The result of evaluating access to a specific ContextFact for an intended purpose."""

    is_granted: bool
    reason: str
    fact_id: str

    @classmethod
    def grant(cls, fact_id: str, reason: str = "Access granted") -> AccessDecision:
        return cls(is_granted=True, reason=reason, fact_id=fact_id)

    @classmethod
    def deny(cls, fact_id: str, reason: str) -> AccessDecision:
        return cls(is_granted=False, reason=reason, fact_id=fact_id)


class ContextAccessPolicy:
    """Evaluates whether an agent or task is permitted to access a ContextFact.

    Guarantees:
    - Conservative Default: SENSITIVE facts are inaccessible unless explicitly authorized for the requested purpose.
    - Lifecycle Guard: Expired, superseded, or rejected facts are never granted.
    - Decoupled from Agent: Evaluates pure domain attributes (purpose, sensitivity, verification).
    """

    def __init__(self, default_min_confidence: float = 0.0) -> None:
        self.default_min_confidence = default_min_confidence

    def evaluate_access(
        self,
        fact: ContextFact,
        requested_purpose: str,
        require_verified: bool = False,
        min_confidence: float | None = None,
    ) -> AccessDecision:
        """Evaluate access permission for a single fact against a requested purpose."""
        confidence_threshold = min_confidence if min_confidence is not None else self.default_min_confidence

        if not requested_purpose or not requested_purpose.strip():
            return AccessDecision.deny(fact.fact_id, "Requested purpose cannot be empty")

        norm_purpose = requested_purpose.strip().lower()

        # Lifecycle checks
        if fact.is_rejected:
            return AccessDecision.deny(fact.fact_id, f"Fact has been rejected: {fact.rejection_reason or 'No reason provided'}")

        if fact.is_superseded:
            return AccessDecision.deny(fact.fact_id, f"Fact has been superseded by fact '{fact.superseded_by_fact_id}'")

        if fact.is_expired:
            return AccessDecision.deny(fact.fact_id, "Fact has expired")

        # Confidence check
        if fact.confidence < confidence_threshold:
            return AccessDecision.deny(
                fact.fact_id,
                f"Fact confidence ({fact.confidence}) is below required threshold ({confidence_threshold})",
            )

        # Verification requirement check
        if require_verified and fact.verification_status == VerificationStatus.UNVERIFIED:
            return AccessDecision.deny(fact.fact_id, "Fact is unverified but purpose requires verified knowledge")

        # Privacy and purpose gating
        if fact.sensitivity == SensitivityLevel.PUBLIC:
            return AccessDecision.grant(fact.fact_id, "Public fact is accessible for all purposes")

        if fact.allowed_purposes:
            allowed_norm = [p.strip().lower() for p in fact.allowed_purposes]
            if norm_purpose not in allowed_norm:
                return AccessDecision.deny(
                    fact.fact_id,
                    f"Requested purpose '{requested_purpose}' is not authorized in allowed_purposes",
                )

        if fact.sensitivity == SensitivityLevel.SENSITIVE:
            # Sensitive facts without explicit allowed_purposes are blocked by conservative default
            if not fact.allowed_purposes:
                return AccessDecision.deny(
                    fact.fact_id,
                    "Sensitive facts require explicit purpose-based authorization in allowed_purposes",
                )

        return AccessDecision.grant(fact.fact_id, "Access authorized by context policy")
