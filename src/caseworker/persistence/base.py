"""Repository and Unit-of-Work protocol definitions for Caseworker persistence."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import Protocol, runtime_checkable

from caseworker.domain.action import Action
from caseworker.domain.approval import Approval
from caseworker.domain.case import Case
from caseworker.domain.claim import Claim
from caseworker.domain.context import ContextFact
from caseworker.domain.enums import CaseStatus, ClaimStatus, MissionStatus, OpportunityStatus
from caseworker.domain.events import DomainEvent
from caseworker.domain.mission import Mission
from caseworker.domain.opportunity import Opportunity
from caseworker.domain.source import ContextSource


@runtime_checkable
class MissionRepository(Protocol):
    """Repository interface for Mission aggregate roots."""

    def save(self, mission: Mission) -> None:
        """Save a new mission or update an existing one with optimistic lock check."""
        ...

    def get_by_id(self, mission_id: str) -> Mission | None:
        """Retrieve a mission by its unique ID."""
        ...

    def list_by_user(self, user_id: str, status: MissionStatus | None = None) -> list[Mission]:
        """List missions for a given user, optionally filtered by status."""
        ...


@runtime_checkable
class CaseRepository(Protocol):
    """Repository interface for Case aggregate roots."""

    def save(self, case: Case) -> None:
        """Save a new case or update an existing one with optimistic lock check."""
        ...

    def get_by_id(self, case_id: str) -> Case | None:
        """Retrieve a case by its unique ID."""
        ...

    def list_by_user(self, user_id: str, status: CaseStatus | None = None) -> list[Case]:
        """List cases for a given user, optionally filtered by status."""
        ...

    def list_by_mission(self, mission_id: str) -> list[Case]:
        """List all cases associated with a given mission."""
        ...


@runtime_checkable
class OpportunityRepository(Protocol):
    """Repository interface for Opportunity entities."""

    def save(self, opportunity: Opportunity) -> None:
        """Save a new opportunity or update an existing one."""
        ...

    def get_by_id(self, opportunity_id: str) -> Opportunity | None:
        """Retrieve an opportunity by its unique ID."""
        ...

    def get_by_fingerprint(self, user_id: str, fingerprint: str) -> Opportunity | None:
        """Retrieve an opportunity by user ID and deduplication fingerprint."""
        ...

    def list_by_mission(self, mission_id: str) -> list[Opportunity]:
        """List opportunities discovered for a mission."""
        ...

    def list_by_user(self, user_id: str, status: OpportunityStatus | None = None) -> list[Opportunity]:
        """List opportunities for a user, optionally filtered by status."""
        ...


@runtime_checkable
class ActionRepository(Protocol):
    """Repository interface for Action entities."""

    def save(self, action: Action) -> None:
        """Save a new action or update an existing one."""
        ...

    def get_by_id(self, action_id: str) -> Action | None:
        """Retrieve an action by its unique ID."""
        ...

    def get_by_idempotency_key(self, case_id: str, idempotency_key: str) -> Action | None:
        """Retrieve an action by case ID and idempotency key."""
        ...

    def list_by_case(self, case_id: str) -> list[Action]:
        """List all actions proposed or executed for a case."""
        ...


@runtime_checkable
class ApprovalRepository(Protocol):
    """Repository interface for Approval entities."""

    def save(self, approval: Approval) -> None:
        """Save a new approval or update an existing one."""
        ...

    def get_by_id(self, approval_id: str) -> Approval | None:
        """Retrieve an approval by its unique ID."""
        ...

    def get_by_action_id(self, action_id: str) -> Approval | None:
        """Retrieve an approval associated with an action."""
        ...

    def list_by_case(self, case_id: str) -> list[Approval]:
        """List all approvals for a case."""
        ...

    def list_pending_by_user(self, user_id: str) -> list[Approval]:
        """List all pending approvals for a user."""
        ...


@runtime_checkable
class ContextSourceRepository(Protocol):
    """Repository interface for ContextSource entities."""

    def save(self, source: ContextSource) -> None:
        """Save a new context source or update an existing one."""
        ...

    def get_by_id(self, source_id: str) -> ContextSource | None:
        """Retrieve a context source by its unique ID."""
        ...

    def list_by_user(self, user_id: str) -> list[ContextSource]:
        """List all context sources belonging to a user."""
        ...


@runtime_checkable
class ContextRepository(Protocol):
    """Repository interface for ContextFact entities."""

    def save(self, fact: ContextFact) -> None:
        """Save a new fact or update an existing one."""
        ...

    def get_by_id(self, fact_id: str) -> ContextFact | None:
        """Retrieve a context fact by ID."""
        ...

    def list_active(self, user_id: str, namespace: str | None = None) -> list[ContextFact]:
        """List currently active (non-superseded, non-expired, non-rejected) facts for a user."""
        ...

    def list_history(self, user_id: str, namespace: str, key: str) -> list[ContextFact]:
        """List full revision history for a specific fact key."""
        ...

    def list_by_namespace(self, user_id: str, namespace_prefix: str) -> list[ContextFact]:
        """List active facts matching a hierarchical namespace prefix (e.g. 'career.*')."""
        ...

    def get_by_source_id(self, source_id: str) -> list[ContextFact]:
        """List all facts derived from a specific context source."""
        ...


@runtime_checkable
class ClaimRepository(Protocol):
    """Repository interface for Claim entities in the Claim Ledger."""

    def save(self, claim: Claim) -> None:
        """Save a new claim or update an existing one."""
        ...

    def get_by_id(self, claim_id: str) -> Claim | None:
        """Retrieve a claim by its unique ID."""
        ...

    def list_by_user(self, user_id: str, status: ClaimStatus | None = None) -> list[Claim]:
        """List claims belonging to a user, optionally filtered by status."""
        ...

    def list_by_case(self, case_id: str) -> list[Claim]:
        """List all claims linked to a specific case."""
        ...

    def list_by_purpose(self, user_id: str, purpose: str) -> list[Claim]:
        """List all claims for a user matching an intended purpose."""
        ...


@runtime_checkable
class EventStore(Protocol):
    """Append-only store for domain events."""

    def append(self, event: DomainEvent) -> None:
        """Append a single domain event atomically."""
        ...

    def append_many(self, events: Sequence[DomainEvent]) -> None:
        """Append a sequence of domain events atomically."""
        ...

    def get_events_for_aggregate(self, aggregate_type: str, aggregate_id: str) -> list[DomainEvent]:
        """Retrieve ordered stream of events for an aggregate root."""
        ...

    def get_events_by_user(self, user_id: str, limit: int = 100) -> list[DomainEvent]:
        """Retrieve recent events for a user."""
        ...

    def get_next_aggregate_version(self, aggregate_type: str, aggregate_id: str) -> int:
        """Return the next strictly monotonic aggregate version for an aggregate stream."""
        ...



@runtime_checkable
class CaseworkerUnitOfWork(Protocol):
    """Transaction / Unit-of-Work boundary ensuring atomic domain state and event persistence."""

    missions: MissionRepository
    cases: CaseRepository
    opportunities: OpportunityRepository
    actions: ActionRepository
    approvals: ApprovalRepository
    sources: ContextSourceRepository
    context: ContextRepository
    claims: ClaimRepository
    events: EventStore

    def commit(self) -> None:
        """Commit all pending database changes."""
        ...

    def rollback(self) -> None:
        """Roll back all pending database changes."""
        ...

    def __enter__(self) -> CaseworkerUnitOfWork:
        ...

    def __exit__(self, exc_type: type[BaseException] | None, exc_val: BaseException | None, exc_tb: Any) -> None:
        ...
