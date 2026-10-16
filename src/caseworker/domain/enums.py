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
    APPROVE_PREPARATION_PACKAGE = "approve_preparation_package"
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


class ClaimStatus(StrEnum):
    """Lifecycle states for factual claims in the Claim Ledger."""

    PROPOSED = "proposed"
    SUPPORTED = "supported"
    UNSUPPORTED = "unsupported"
    CONFLICTED = "conflicted"
    REJECTED = "rejected"
    EXPIRED = "expired"

    @property
    def is_terminal(self) -> bool:
        return self in (ClaimStatus.REJECTED, ClaimStatus.EXPIRED)


class SourceType(StrEnum):
    """Provenance origin of personal context facts and sources."""

    USER_INPUT = "user_input"
    DOCUMENT = "document"
    PROFILE_IMPORT = "profile_import"
    SYSTEM_DERIVED = "system_derived"
    AGENT_INFERENCE = "agent_inference"
    THIRD_PARTY = "third_party"
    # Backwards-compatible aliases with Milestone 1
    USER_UPLOAD = "user_upload"
    THIRD_PARTY_VERIFIER = "third_party_verifier"
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


class EligibilityStatus(StrEnum):
    """Tri-state eligibility status for an opportunity against user context."""

    ELIGIBLE = "eligible"
    INELIGIBLE = "ineligible"
    UNKNOWN = "unknown"


class RequirementCategory(StrEnum):
    """Classification of requirement importance."""

    MANDATORY = "mandatory"
    PREFERRED = "preferred"


class RequirementMatchResult(StrEnum):
    """Result of evaluating a single requirement against user evidence."""

    MET = "met"
    NOT_MET = "not_met"
    UNKNOWN = "unknown"


class FitTier(StrEnum):
    """Qualitative tier for explainable fit score."""

    STRONG = "strong"
    MODERATE = "moderate"
    LOW = "low"
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"


class EvidenceAlignmentStatus(StrEnum):
    """Alignment status of a requirement against user Context Vault evidence."""

    SUPPORTED = "supported"
    POTENTIAL = "potential"
    GAP = "gap"
    CONTRADICTED = "contradicted"
    RESTRICTED = "restricted"


class GapCategory(StrEnum):
    """Classification of requirement evidence deficit or conflict."""

    MANDATORY_EVIDENCE_GAP = "mandatory_evidence_gap"
    PREFERRED_EVIDENCE_GAP = "preferred_evidence_gap"
    HARD_CONFLICT = "hard_conflict"


class PreparationMaterialType(StrEnum):
    """Type of pursuit artifact to be synthesized."""

    CV = "cv"
    COVER_LETTER = "cover_letter"
    SCREENING_ANSWERS = "screening_answers"
    APPLICATION_ANSWER_BANK = "application_answer_bank"
    ANSWER_BANK = "application_answer_bank"
    PORTFOLIO_EXCERPT = "portfolio_excerpt"
    STATEMENT_OF_PURPOSE = "statement_of_purpose"
    CUSTOM = "custom"


class ArtifactGroundingType(StrEnum):
    """Grounding category for an artifact content block."""

    STRUCTURAL = "structural"
    OPPORTUNITY = "opportunity"
    CONTEXT_FACT = "context_fact"
    CLAIM = "claim"


class ArtifactStatus(StrEnum):
    """Lifecycle status of a prepared artifact or artifact set."""

    DRAFT = "draft"
    STALE = "stale"
    SUPERSEDED = "superseded"
    FROZEN = "frozen"


class AssertionKind(StrEnum):
    """Structured proposition kind for deterministic claim synthesis."""

    SKILL_POSSESSION = "skill_possession"
    EMPLOYMENT_ROLE = "employment_role"
    EMPLOYMENT_DURATION = "employment_duration"
    EDUCATION_CREDENTIAL = "education_credential"
    QUANTIFIED_ACHIEVEMENT = "quantified_achievement"
    PROJECT_EXPERIENCE = "project_experience"
    WORK_AUTHORIZATION = "work_authorization"
    LANGUAGE_PROFICIENCY = "language_proficiency"


class PackageVerificationStatus(StrEnum):
    """Result of package pre-verification checks."""

    UNVERIFIED = "unverified"
    VERIFIED = "verified"
    FAILED = "failed"


class PreparationPackageStatus(StrEnum):
    """Lifecycle status of a verified preparation package."""

    FROZEN = "frozen"
    APPROVED = "approved"
    REJECTED = "rejected"
    SUPERSEDED = "superseded"


class ArtifactPublicationRole(StrEnum):
    """Publication and transmission role for prepared artifacts."""

    SUBMISSION_MATERIAL = "submission_material"
    FIELD_RESPONSE = "field_response"
    INTERNAL_PREPARATION = "internal_preparation"


class ExecutionDestinationType(StrEnum):
    """Destination channel category for external action execution."""

    ATS_FORM = "ats_form"
    DIRECT_PORTAL = "direct_portal"
    EMAIL = "email"
    API = "api"
    CUSTOM = "custom"


class DestinationProvenance(StrEnum):
    """Provenance origin verifying the authenticity of an application destination."""

    USER_PROVIDED = "user_provided"
    OPPORTUNITY_APPLICATION_URL = "opportunity_application_url"
    VERIFIED_PROVIDER_METADATA = "verified_provider_metadata"


class ExternalExecutionPlanStatus(StrEnum):
    """Lifecycle states for an ExternalExecutionPlan."""

    DRAFT = "draft"
    READY_FOR_AUTHORIZATION = "ready_for_authorization"
    SUPERSEDED = "superseded"



