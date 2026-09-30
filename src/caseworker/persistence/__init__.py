"""Caseworker persistence protocols and SQLite implementation."""

from caseworker.persistence.base import (
    ActionRepository,
    ApprovalRepository,
    CaseRepository,
    CaseworkerUnitOfWork,
    ContextRepository,
    EventStore,
    MissionRepository,
    OpportunityRepository,
)
from caseworker.persistence.sqlite import (
    SQLiteActionRepository,
    SQLiteApprovalRepository,
    SQLiteCaseRepository,
    SQLiteCaseworkerStorage,
    SQLiteCaseworkerUnitOfWork,
    SQLiteContextRepository,
    SQLiteEventStore,
    SQLiteMissionRepository,
    SQLiteOpportunityRepository,
)

__all__ = [
    # Protocols
    "MissionRepository",
    "CaseRepository",
    "OpportunityRepository",
    "ActionRepository",
    "ApprovalRepository",
    "ContextRepository",
    "EventStore",
    "CaseworkerUnitOfWork",
    # SQLite Implementations
    "SQLiteCaseworkerStorage",
    "SQLiteCaseworkerUnitOfWork",
    "SQLiteMissionRepository",
    "SQLiteCaseRepository",
    "SQLiteOpportunityRepository",
    "SQLiteActionRepository",
    "SQLiteApprovalRepository",
    "SQLiteContextRepository",
    "SQLiteEventStore",
]
