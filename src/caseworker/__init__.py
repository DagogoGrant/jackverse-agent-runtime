"""JackVerse Caseworker: Governed Case Management Layer.

"Give it a goal or a problem. JackVerse manages the case."
"""

from caseworker.domain import (
    Action,
    ActionStatus,
    ActionType,
    Approval,
    ApprovalStatus,
    ApprovalValidationError,
    Case,
    CaseStatus,
    CaseType,
    CaseworkerError,
    ContextFact,
    DomainEvent,
    DomainValidationError,
    EntityNotFoundError,
    InvalidStateTransitionError,
    Mission,
    MissionKind,
    MissionStatus,
    Opportunity,
    OpportunityStatus,
    OpportunityType,
    OptimisticLockError,
    PersistenceError,
    RiskLevel,
    SensitivityLevel,
    SourceType,
    VerificationStatus,
)
from caseworker.persistence import (
    ActionRepository,
    ApprovalRepository,
    CaseRepository,
    CaseworkerUnitOfWork,
    ContextRepository,
    EventStore,
    MissionRepository,
    OpportunityRepository,
    SQLiteCaseworkerStorage,
    SQLiteCaseworkerUnitOfWork,
)
from caseworker.services import CaseService, MissionService

__version__ = "0.1.0"

__all__ = [
    # Domain entities & aggregates
    "Mission",
    "Case",
    "Opportunity",
    "Action",
    "Approval",
    "ContextFact",
    "DomainEvent",
    # Enums
    "MissionStatus",
    "MissionKind",
    "CaseStatus",
    "CaseType",
    "OpportunityType",
    "OpportunityStatus",
    "ActionType",
    "ActionStatus",
    "RiskLevel",
    "ApprovalStatus",
    "SourceType",
    "VerificationStatus",
    "SensitivityLevel",
    # Errors
    "CaseworkerError",
    "EntityNotFoundError",
    "InvalidStateTransitionError",
    "OptimisticLockError",
    "DomainValidationError",
    "ApprovalValidationError",
    "PersistenceError",
    # Persistence
    "MissionRepository",
    "CaseRepository",
    "OpportunityRepository",
    "ActionRepository",
    "ApprovalRepository",
    "ContextRepository",
    "EventStore",
    "CaseworkerUnitOfWork",
    "SQLiteCaseworkerStorage",
    "SQLiteCaseworkerUnitOfWork",
    # Services
    "MissionService",
    "CaseService",
]
