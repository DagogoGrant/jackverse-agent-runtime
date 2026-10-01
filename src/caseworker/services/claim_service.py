"""Application service managing Claim Ledger operations, evaluations, and state transitions."""

from __future__ import annotations

from typing import TYPE_CHECKING

from caseworker.domain.claim import Claim
from caseworker.domain.claim_policy import ClaimVerificationPolicy
from caseworker.domain.enums import ClaimStatus
from caseworker.domain.errors import DomainValidationError, EntityNotFoundError
from caseworker.domain.events import (
    make_claim_proposed_event,
    make_claim_status_changed_event,
)

if TYPE_CHECKING:
    from caseworker.persistence.sqlite import SQLiteCaseworkerStorage


class ClaimLedgerService:
    """Application service managing verifiable external assertions with atomic state + event persistence."""

    def __init__(
        self,
        storage: SQLiteCaseworkerStorage,
        verification_policy: ClaimVerificationPolicy | None = None,
    ) -> None:
        self.storage = storage
        self.policy = verification_policy or ClaimVerificationPolicy()

    def propose_claim(
        self,
        user_id: str,
        purpose: str,
        text: str,
        supporting_fact_ids: list[str],
        case_id: str | None = None,
        mission_id: str | None = None,
        auto_evaluate: bool = True,
    ) -> Claim:
        """Propose a factual assertion, link supporting facts, and optionally evaluate support atomically."""
        with self.storage.unit_of_work() as uow:
            if case_id is not None:
                case = uow.cases.get_by_id(case_id)
                if case is None:
                    raise EntityNotFoundError("Case", case_id)
                if case.user_id != user_id:
                    raise DomainValidationError(
                        f"Case '{case_id}' belongs to user '{case.user_id}', not '{user_id}'."
                    )

            if mission_id is not None:
                mission = uow.missions.get_by_id(mission_id)
                if mission is None:
                    raise EntityNotFoundError("Mission", mission_id)
                if mission.user_id != user_id:
                    raise DomainValidationError(
                        f"Mission '{mission_id}' belongs to user '{mission.user_id}', not '{user_id}'."
                    )

            if case_id is not None and mission_id is not None:
                if case.mission_id != mission_id:
                    raise DomainValidationError(
                        f"Case '{case_id}' is associated with mission '{case.mission_id}', not '{mission_id}'."
                    )

            for fid in supporting_fact_ids:
                fact = uow.context.get_by_id(fid)
                if fact is None:
                    raise EntityNotFoundError("ContextFact", fid)
                if fact.user_id != user_id:
                    raise DomainValidationError(
                        f"Supporting fact '{fid}' belongs to user '{fact.user_id}', not '{user_id}'."
                    )

            claim = Claim(
                user_id=user_id,
                purpose=purpose,
                text=text,
                supporting_fact_ids=list(supporting_fact_ids),
                case_id=case_id,
                mission_id=mission_id,
                status=ClaimStatus.PROPOSED,
            )
            uow.claims.save(claim)

            proposed_event = make_claim_proposed_event(
                claim_id=claim.claim_id,
                user_id=claim.user_id,
                aggregate_version=claim.version,
                purpose=claim.purpose,
                text=claim.text,
                supporting_fact_ids=claim.supporting_fact_ids,
                case_id=claim.case_id,
                mission_id=claim.mission_id,
            )
            uow.events.append(proposed_event)

            if auto_evaluate:
                supporting_facts = [
                    f
                    for fid in claim.supporting_fact_ids
                    if (f := uow.context.get_by_id(fid)) is not None
                ]
                status, reason = self.policy.evaluate_support(claim, supporting_facts)
                old_status = claim.status.value

                if status == ClaimStatus.SUPPORTED:
                    claim.set_supported()
                elif status == ClaimStatus.CONFLICTED:
                    claim.set_conflicted(reason)
                else:
                    claim.set_unsupported(reason)

                uow.claims.save(claim)

                changed_event = make_claim_status_changed_event(
                    claim_id=claim.claim_id,
                    user_id=claim.user_id,
                    aggregate_version=claim.version,
                    old_status=old_status,
                    new_status=claim.status.value,
                    reason=reason,
                )
                uow.events.append(changed_event)

        return claim

    def evaluate_claim(self, claim_id: str) -> Claim:
        """Re-evaluate an existing claim against currently active facts in the Personal Context Vault."""
        with self.storage.unit_of_work() as uow:
            claim = uow.claims.get_by_id(claim_id)
            if claim is None:
                raise EntityNotFoundError("Claim", claim_id)

            if claim.is_terminal:
                return claim

            supporting_facts = [
                f
                for fid in claim.supporting_fact_ids
                if (f := uow.context.get_by_id(fid)) is not None
            ]
            new_status, reason = self.policy.evaluate_support(claim, supporting_facts)

            if new_status != claim.status:
                old_status = claim.status.value
                claim.transition_to(new_status, reason=reason)
                uow.claims.save(claim)

                event = make_claim_status_changed_event(
                    claim_id=claim.claim_id,
                    user_id=claim.user_id,
                    aggregate_version=claim.version,
                    old_status=old_status,
                    new_status=claim.status.value,
                    reason=reason,
                )
                uow.events.append(event)

        return claim

    def reject_claim(self, claim_id: str, reason: str | None = None) -> Claim:
        """Explicitly reject a claim, marking it terminal with recorded reason."""
        with self.storage.unit_of_work() as uow:
            claim = uow.claims.get_by_id(claim_id)
            if claim is None:
                raise EntityNotFoundError("Claim", claim_id)

            old_status = claim.status.value
            claim.reject(reason=reason)
            uow.claims.save(claim)

            event = make_claim_status_changed_event(
                claim_id=claim.claim_id,
                user_id=claim.user_id,
                aggregate_version=claim.version,
                old_status=old_status,
                new_status=claim.status.value,
                reason=reason,
            )
            uow.events.append(event)

        return claim

    def expire_claim(self, claim_id: str, reason: str | None = None) -> Claim:
        """Expire a claim whose supporting context has lapsed."""
        with self.storage.unit_of_work() as uow:
            claim = uow.claims.get_by_id(claim_id)
            if claim is None:
                raise EntityNotFoundError("Claim", claim_id)

            old_status = claim.status.value
            claim.expire(reason=reason)
            uow.claims.save(claim)

            event = make_claim_status_changed_event(
                claim_id=claim.claim_id,
                user_id=claim.user_id,
                aggregate_version=claim.version,
                old_status=old_status,
                new_status=claim.status.value,
                reason=reason,
            )
            uow.events.append(event)

        return claim

    def get_claim(self, claim_id: str) -> Claim | None:
        """Retrieve a claim by its unique ID."""
        with self.storage.unit_of_work() as uow:
            return uow.claims.get_by_id(claim_id)

    def get_claim_for_user(self, user_id: str, claim_id: str) -> Claim | None:
        """Retrieve a claim by ID, returning None if non-existent or owned by another user."""
        with self.storage.unit_of_work() as uow:
            claim = uow.claims.get_by_id(claim_id)
            if claim is None or claim.user_id != user_id:
                return None
            return claim

    def evaluate_claim_for_user(self, user_id: str, claim_id: str) -> Claim:
        """Evaluate an existing claim, asserting user ownership."""
        with self.storage.unit_of_work() as uow:
            claim = uow.claims.get_by_id(claim_id)
            if claim is None or claim.user_id != user_id:
                raise EntityNotFoundError("Claim", claim_id)

        return self.evaluate_claim(claim_id)

    def reject_claim_for_user(self, user_id: str, claim_id: str, reason: str | None = None) -> Claim:
        """Reject an existing claim, asserting user ownership."""
        with self.storage.unit_of_work() as uow:
            claim = uow.claims.get_by_id(claim_id)
            if claim is None or claim.user_id != user_id:
                raise EntityNotFoundError("Claim", claim_id)

        return self.reject_claim(claim_id, reason=reason)

    def list_user_claims(
        self,
        user_id: str,
        status: ClaimStatus | None = None,
        case_id: str | None = None,
        mission_id: str | None = None,
    ) -> list[Claim]:
        """List claims belonging to a user, optionally filtered by status, case_id, or mission_id."""
        with self.storage.unit_of_work() as uow:
            return uow.claims.list_by_user(
                user_id=user_id,
                status=status,
                case_id=case_id,
                mission_id=mission_id,
            )

    def list_case_claims(self, case_id: str) -> list[Claim]:
        """List all claims linked to a specific case."""
        with self.storage.unit_of_work() as uow:
            return uow.claims.list_by_case(case_id=case_id)

    def list_purpose_claims(self, user_id: str, purpose: str) -> list[Claim]:
        """List all claims for a user matching an intended purpose."""
        with self.storage.unit_of_work() as uow:
            return uow.claims.list_by_purpose(user_id=user_id, purpose=purpose)
