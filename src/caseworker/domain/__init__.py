"""JackVerse Caseworker domain models, enumerations, events, and errors."""

from caseworker.domain.action import Action
from caseworker.domain.approval import Approval
from caseworker.domain.case import Case
from caseworker.domain.context import ContextFact
from caseworker.domain.enums import (
    ActionStatus,
    ActionType,
    ApprovalStatus,
    CaseStatus,
    CaseType,
    MissionKind,
    MissionStatus,
    OpportunityStatus,
    OpportunityType,
    RiskLevel,
    SensitivityLevel,
    SourceType,
    VerificationStatus,
)
from caseworker.domain.errors import (
    ApprovalValidationError,
    CaseworkerError,
    DomainValidationError,
    EntityNotFoundError,
    InvalidStateTransitionError,
    OptimisticLockError,
    PersistenceError,
)
from caseworker.domain.events import (
    DomainEvent,
    make_action_proposed_event,
    make_action_status_changed_event,
    make_approval_decided_event,
    make_approval_requested_event,
    make_case_created_event,
    make_case_resolved_event,
    make_case_status_changed_event,
    make_context_fact_created_event,
    make_context_fact_updated_event,
    make_mission_created_event,
    make_mission_status_changed_event,
    make_opportunity_discovered_event,
    make_opportunity_status_changed_event,
)
from caseworker.domain.mission import Mission
from caseworker.domain.opportunity import Opportunity
from caseworker.domain.types import (
    JSONObject,
    JSONScalar,
    JSONValue,
    canonical_json_dumps,
    compute_sha256,
    ensure_utc,
    from_iso_utc,
    now_utc,
    to_iso_utc,
)

__all__ = [
    # Aggregate roots & Entities
    "Mission",
    "Case",
    "Opportunity",
    "Action",
    "Approval",
    "ContextFact",
    # Events
    "DomainEvent",
    "make_mission_created_event",
    "make_mission_status_changed_event",
    "make_case_created_event",
    "make_case_status_changed_event",
    "make_case_resolved_event",
    "make_opportunity_discovered_event",
    "make_opportunity_status_changed_event",
    "make_action_proposed_event",
    "make_action_status_changed_event",
    "make_approval_requested_event",
    "make_approval_decided_event",
    "make_context_fact_created_event",
    "make_context_fact_updated_event",
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
    # Types & Helpers
    "JSONScalar",
    "JSONValue",
    "JSONObject",
    "now_utc",
    "ensure_utc",
    "to_iso_utc",
    "from_iso_utc",
    "canonical_json_dumps",
    "compute_sha256",
]
