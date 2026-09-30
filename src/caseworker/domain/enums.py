"""Domain enumerations for JackVerse Caseworker."""

from __future__ import annotations

from enum import Enum


class StrEnum(str, Enum):
    """String enumeration for deterministic serialization and equality."""

    def __str__(self) -> str:
        return self.value


class MissionStatus(StrEnum):
    """Lifecycle states for a Mission."""

    DRAFT = "draft"
    ACTIVE = "active"
    PAUSED = "paused"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    FAILED = "failed"

    @property
    def is_terminal(self) -> bool:
        return self in (MissionStatus.COMPLETED, MissionStatus.CANCELLED, MissionStatus.FAILED)


class MissionKind(StrEnum):
    """High-level classification of user objective."""

    OPPORTUNITY_PURSUIT = "opportunity_pursuit"
    PROBLEM_RESOLUTION = "problem_resolution"
    GENERAL_GOAL = "general_goal"


class CaseStatus(StrEnum):
    """Lifecycle states for a Case."""

    NEW = "new"
    INTAKE = "intake"
    INVESTIGATING = "investigating"
    PLANNING = "planning"
    ACTION_REQUIRED = "action_required"
    AWAITING_APPROVAL = "awaiting_approval"
    ACTION_IN_PROGRESS = "action_in_progress"
    WAITING_EXTERNAL = "waiting_external"
    FOLLOW_UP_DUE = "follow_up_due"
    RESOLVED = "resolved"
    BLOCKED = "blocked"
    ESCALATED = "escalated"
    CANCELLED = "cancelled"

    @property
    def is_terminal(self) -> bool:
        return self in (CaseStatus.RESOLVED, CaseStatus.CANCELLED)


class CaseType(StrEnum):
    """Standard case types with extensible categories."""

    JOB_APPLICATION = "job_application"
    SCHOLARSHIP_APPLICATION = "scholarship_application"
    GRANT_PURSUIT = "grant_pursuit"
    HOUSING_SEARCH = "housing_search"
    PACKAGE_INVESTIGATION = "package_investigation"
    REFUND_REQUEST = "refund_request"
    SERVICE_COMPLAINT = "service_complaint"
    GENERAL = "general"


class OpportunityType(StrEnum):
    """Classification of external opportunities."""

    JOB = "job"
    SCHOLARSHIP = "scholarship"
    GRANT = "grant"
    FELLOWSHIP = "fellowship"
    RESEARCH = "research"
    HOUSING = "housing"
    FREELANCE = "freelance"
    HACKATHON = "hackathon"
    CONFERENCE = "conference"
    COMPETITION = "competition"
    OTHER = "other"


class OpportunityStatus(StrEnum):
    """Lifecycle status of a discovered opportunity."""

    DISCOVERED = "discovered"
    NORMALIZED = "normalized"
    EVALUATING = "evaluating"
    SHORTLISTED = "shortlisted"
    CONVERTED_TO_CASE = "converted_to_case"
    ARCHIVED = "archived"
    REJECTED = "rejected"

    @property
    def is_terminal(self) -> bool:
        return self in (OpportunityStatus.CONVERTED_TO_CASE, OpportunityStatus.ARCHIVED, OpportunityStatus.REJECTED)


class ActionType(StrEnum):
    """Types of actions proposed or executed in service of a case."""

    DRAFT_APPLICATION = "draft_application"
    SEND_COMMUNICATION = "send_communication"
    SUBMIT_FORM = "submit_form"
    REQUEST_INFORMATION = "request_information"
    REQUEST_REFUND = "request_refund"
    ESCALATE = "escalate"
    FOLLOW_UP = "follow_up"
    CUSTOM = "custom"


class ActionStatus(StrEnum):
    """Lifecycle status for a real-world Action."""

    PROPOSED = "proposed"
    AWAITING_APPROVAL = "awaiting_approval"
    APPROVED = "approved"
    REJECTED = "rejected"
    EXECUTING = "executing"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"

    @property
    def is_terminal(self) -> bool:
        return self in (
            ActionStatus.REJECTED,
            ActionStatus.SUCCEEDED,
            ActionStatus.FAILED,
            ActionStatus.CANCELLED,
        )


class RiskLevel(StrEnum):
    """Risk severity classification for consequential actions."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class ApprovalStatus(StrEnum):
    """Status of an approval decision."""

    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    EXPIRED = "expired"
    CANCELLED = "cancelled"

    @property
    def is_terminal(self) -> bool:
        return self in (
            ApprovalStatus.APPROVED,
            ApprovalStatus.REJECTED,
            ApprovalStatus.EXPIRED,
            ApprovalStatus.CANCELLED,
        )


class SourceType(StrEnum):
    """Provenance origin of personal context facts."""

    USER_INPUT = "user_input"
    USER_UPLOAD = "user_upload"
    DOCUMENT = "document"
    THIRD_PARTY_VERIFIER = "third_party_verifier"
    AGENT_INFERENCE = "agent_inference"
    SYSTEM = "system"


class VerificationStatus(StrEnum):
    """Trust and verification level of a fact."""

    UNVERIFIED = "unverified"
    USER_VERIFIED = "user_verified"
    SOURCE_VERIFIED = "source_verified"
    REJECTED = "rejected"


class SensitivityLevel(StrEnum):
    """Privacy and exposure boundaries for facts."""

    PUBLIC = "public"
    PERSONAL = "personal"
    SENSITIVE = "sensitive"
