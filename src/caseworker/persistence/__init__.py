"""Caseworker persistence protocols and SQLite implementation."""

from caseworker.persistence.base import (
    ActionRepository,
    ApprovalRepository,
    CaseRepository,
    CaseworkerUnitOfWork,
    ClaimRepository,
    ContextRepository,
    ContextSourceRepository,
    EventStore,
    MissionRepository,
    OpportunityRepository,
)
from caseworker.persistence.migration import SQLiteMigrator
from caseworker.persistence.sqlite import (
    SQLiteActionRepository,
    SQLiteApprovalRepository,
    SQLiteCaseRepository,
    SQLiteCaseworkerStorage,
    SQLiteCaseworkerUnitOfWork,
    SQLiteClaimRepository,
    SQLiteContextRepository,
    SQLiteContextSourceRepository,
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
    "ContextSourceRepository",
    "ContextRepository",
    "ClaimRepository",
    "EventStore",
    "CaseworkerUnitOfWork",
    # Migration
    "SQLiteMigrator",
    # SQLite Implementations
    "SQLiteCaseworkerStorage",
    "SQLiteCaseworkerUnitOfWork",
    "SQLiteMissionRepository",
    "SQLiteCaseRepository",
    "SQLiteOpportunityRepository",
    "SQLiteActionRepository",
    "SQLiteApprovalRepository",
    "SQLiteContextSourceRepository",
    "SQLiteContextRepository",
    "SQLiteClaimRepository",
    "SQLiteEventStore",
]

