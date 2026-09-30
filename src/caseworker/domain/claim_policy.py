"""Deterministic claim verification policies and support evaluation rules."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from caseworker.domain.claim import Claim
from caseworker.domain.context import ContextFact
from caseworker.domain.enums import ClaimStatus, VerificationStatus


_VERIFICATION_RANKS: dict[VerificationStatus, int] = {
    VerificationStatus.REJECTED: -1,
    VerificationStatus.UNVERIFIED: 0,
    VerificationStatus.USER_VERIFIED: 1,
    VerificationStatus.SOURCE_VERIFIED: 2,
}


@dataclass
class ClaimVerificationPolicy:
    """Configurable, deterministic policy for evaluating claim support.

    Guarantees:
    - Pure Domain Logic: Decoupled from LLMs and external storage.
    - Zero Hallucination: Claims without active, authorized, verified supporting facts cannot be SUPPORTED.
    - Contradiction Detection: Contradictory supporting facts on the same key yield CONFLICTED.
    """

    default_minimum_verification: VerificationStatus = VerificationStatus.USER_VERIFIED
    minimum_verification_by_purpose: dict[str, VerificationStatus] = field(
        default_factory=lambda: {
            "job_application": VerificationStatus.USER_VERIFIED,
            "scholarship_application": VerificationStatus.USER_VERIFIED,
            "grant_pursuit": VerificationStatus.USER_VERIFIED,
            "housing_search": VerificationStatus.USER_VERIFIED,
            "internal_research": VerificationStatus.UNVERIFIED,
        }
    )

    def get_minimum_verification(self, purpose: str) -> VerificationStatus:
        """Lookup the minimum verification requirement for a given purpose."""
        norm = purpose.strip().lower()
        return self.minimum_verification_by_purpose.get(norm, self.default_minimum_verification)

    def evaluate_support(
        self,
        claim: Claim,
        supporting_facts: list[ContextFact],
    ) -> tuple[ClaimStatus, str | None]:
        """Deterministically evaluate whether a claim is SUPPORTED, UNSUPPORTED, or CONFLICTED."""
        if not supporting_facts:
            return ClaimStatus.UNSUPPORTED, "No supporting facts provided for claim."

        # Check that all declared fact IDs are present
        found_ids = {f.fact_id for f in supporting_facts}
        for declared_id in claim.supporting_fact_ids:
            if declared_id not in found_ids:
                return ClaimStatus.UNSUPPORTED, f"Supporting fact '{declared_id}' was not found in vault."

        min_req = self.get_minimum_verification(claim.purpose)
        req_rank = _VERIFICATION_RANKS.get(min_req, 1)

        # Map to detect contradictory facts on identical (namespace, key)
        seen_values: dict[tuple[str, str], Any] = {}

        for fact in supporting_facts:
            # 1. User ownership check
            if fact.user_id != claim.user_id:
                return ClaimStatus.UNSUPPORTED, f"Supporting fact '{fact.fact_id}' does not belong to user '{claim.user_id}'."

            # 2. Lifecycle active check
            if fact.is_rejected:
                return ClaimStatus.UNSUPPORTED, f"Supporting fact '{fact.fact_id}' has been rejected."

            if fact.is_superseded:
                return ClaimStatus.UNSUPPORTED, f"Supporting fact '{fact.fact_id}' is superseded by '{fact.superseded_by_fact_id}'."

            if fact.is_expired:
                return ClaimStatus.UNSUPPORTED, f"Supporting fact '{fact.fact_id}' has expired."

            # 3. Purpose authorization check
            if not fact.is_valid_for_purpose(claim.purpose):
                return (
                    ClaimStatus.UNSUPPORTED,
                    f"Supporting fact '{fact.fact_id}' is not authorized for purpose '{claim.purpose}'.",
                )

            # 4. Minimum verification rank check
            fact_rank = _VERIFICATION_RANKS.get(fact.verification_status, 0)
            if fact_rank < req_rank:
                return (
                    ClaimStatus.UNSUPPORTED,
                    f"Supporting fact '{fact.fact_id}' verification '{fact.verification_status.value}' "
                    f"is below required '{min_req.value}' for purpose '{claim.purpose}'.",
                )

            # 5. Contradiction detection
            key_tuple = (fact.namespace.strip().lower(), fact.key.strip().lower())
            if key_tuple in seen_values and seen_values[key_tuple] != fact.value:
                return (
                    ClaimStatus.CONFLICTED,
                    f"Contradictory values detected across supporting facts for '{fact.namespace}.{fact.key}': "
                    f"'{seen_values[key_tuple]}' vs '{fact.value}'.",
                )
            seen_values[key_tuple] = fact.value

        return ClaimStatus.SUPPORTED, None
