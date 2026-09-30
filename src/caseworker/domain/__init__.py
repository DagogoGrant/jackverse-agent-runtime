"""JackVerse Caseworker domain models, enumerations, events, and errors."""

from caseworker.domain.action import Action
from caseworker.domain.approval import Approval
from caseworker.domain.case import Case
from caseworker.domain.claim import Claim
from caseworker.domain.claim_policy import ClaimVerificationPolicy
from caseworker.domain.completeness import (
    CompletenessResult,
    ProfileCompletenessEvaluator,
    ProfileRequirement,
    RequirementSet,
    standard_housing_application_requirements,
    standard_job_application_requirements,
)
from caseworker.domain.context import ContextFact
from caseworker.domain.context_package import ContextPackage, ContextPackageBuilder
from caseworker.domain.enums import (
    ActionStatus,
    ActionType,
    ApprovalStatus,
    CaseStatus,
    CaseType,
    ClaimStatus,
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
    make_claim_proposed_event,
    make_claim_status_changed_event,
    make_context_access_denied_event,
    make_context_access_granted_event,
    make_context_fact_created_event,
    make_context_fact_rejected_event,
    make_context_fact_superseded_event,
    make_context_fact_updated_event,
    make_context_fact_verified_event,
    make_context_source_registered_event,
    make_mission_created_event,
    make_mission_status_changed_event,
    make_opportunity_discovered_event,
    make_opportunity_status_changed_event,
)
from caseworker.domain.mission import Mission
from caseworker.domain.namespaces import (
    STANDARD_ROOT_NAMESPACES,
    get_namespace_root,
    is_valid_namespace,
    matches_namespace_filter,
    normalize_namespace,
)
from caseworker.domain.opportunity import Opportunity
from caseworker.domain.source import ContextSource
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
from caseworker.domain.vault_policy import AccessDecision, ContextAccessPolicy

__all__ = [
    # Aggregate roots & Entities
    "Mission",
    "Case",
    "Opportunity",
    "Action",
    "Approval",
    "ContextFact",
    "ContextSource",
    "Claim",
    "ContextPackage",
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
    "make_context_source_registered_event",
    "make_context_fact_verified_event",
    "make_context_fact_superseded_event",
    "make_context_fact_rejected_event",
    "make_context_access_granted_event",
    "make_context_access_denied_event",
    "make_claim_proposed_event",
    "make_claim_status_changed_event",
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
    "ClaimStatus",
    # Errors
    "CaseworkerError",
    "EntityNotFoundError",
    "InvalidStateTransitionError",
    "OptimisticLockError",
    "DomainValidationError",
    "ApprovalValidationError",
    "PersistenceError",
    # Policies, Evaluation & Builders
    "ContextAccessPolicy",
    "AccessDecision",
    "ClaimVerificationPolicy",
    "ContextPackageBuilder",
    "ProfileRequirement",
    "RequirementSet",
    "CompletenessResult",
    "ProfileCompletenessEvaluator",
    "standard_job_application_requirements",
    "standard_housing_application_requirements",
    # Namespaces
    "STANDARD_ROOT_NAMESPACES",
    "is_valid_namespace",
    "matches_namespace_filter",
    "get_namespace_root",
    "normalize_namespace",
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
