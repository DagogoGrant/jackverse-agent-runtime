"""Generic profile completeness evaluation and requirement definitions."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from caseworker.domain.claim_policy import _VERIFICATION_RANKS
from caseworker.domain.context import ContextFact
from caseworker.domain.enums import VerificationStatus
from caseworker.domain.namespaces import matches_namespace_filter


@dataclass(frozen=True)
class ProfileRequirement:
    """A single informational requirement expected for a specific task or purpose."""

    requirement_id: str
    purpose: str
    namespace: str
    key: str
    label: str
    is_mandatory: bool = True
    minimum_verification: VerificationStatus = VerificationStatus.UNVERIFIED


@dataclass
class RequirementSet:
    """A collection of profile requirements governing an application domain."""

    purpose: str
    title: str
    requirements: list[ProfileRequirement] = field(default_factory=list)


@dataclass
class CompletenessResult:
    """Detailed evaluation result of user profile readiness for a purpose."""

    purpose: str
    satisfied: list[ProfileRequirement]
    missing: list[ProfileRequirement]
    unverifiable: list[ProfileRequirement]
    expired: list[ProfileRequirement]
    completeness_ratio: float
    is_ready: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "purpose": self.purpose,
            "satisfied_count": len(self.satisfied),
            "missing_count": len(self.missing),
            "unverifiable_count": len(self.unverifiable),
            "expired_count": len(self.expired),
            "completeness_ratio": self.completeness_ratio,
            "is_ready": self.is_ready,
            "satisfied_keys": [f"{r.namespace}.{r.key}" for r in self.satisfied],
            "missing_keys": [f"{r.namespace}.{r.key}" for r in self.missing],
            "unverifiable_keys": [f"{r.namespace}.{r.key}" for r in self.unverifiable],
            "expired_keys": [f"{r.namespace}.{r.key}" for r in self.expired],
        }


class ProfileCompletenessEvaluator:
    """Evaluates a user's active context facts against a RequirementSet."""

    @staticmethod
    def evaluate(requirement_set: RequirementSet, facts: list[ContextFact]) -> CompletenessResult:
        satisfied: list[ProfileRequirement] = []
        missing: list[ProfileRequirement] = []
        unverifiable: list[ProfileRequirement] = []
        expired: list[ProfileRequirement] = []

        for req in requirement_set.requirements:
            # Find facts matching namespace and key
            matching_facts = [
                f for f in facts
                if matches_namespace_filter(f.namespace, req.namespace) and f.key.strip().lower() == req.key.strip().lower()
            ]

            if not matching_facts:
                missing.append(req)
                continue

            # Check if all matching facts are expired
            active_matching = [f for f in matching_facts if not f.is_superseded and not f.is_rejected]
            if not active_matching:
                missing.append(req)
                continue

            unexpired = [f for f in active_matching if not f.is_expired]
            if not unexpired:
                expired.append(req)
                continue

            # Check verification requirement
            req_rank = _VERIFICATION_RANKS.get(req.minimum_verification, 0)
            verified_matching = [
                f for f in unexpired
                if _VERIFICATION_RANKS.get(f.verification_status, 0) >= req_rank
            ]

            if not verified_matching:
                unverifiable.append(req)
                continue

            satisfied.append(req)

        total_reqs = len(requirement_set.requirements)
        ratio = (len(satisfied) / total_reqs) if total_reqs > 0 else 1.0

        # Ready if all mandatory requirements are satisfied
        mandatory_satisfied = all(
            req in satisfied for req in requirement_set.requirements if req.is_mandatory
        )

        return CompletenessResult(
            purpose=requirement_set.purpose,
            satisfied=satisfied,
            missing=missing,
            unverifiable=unverifiable,
            expired=expired,
            completeness_ratio=round(ratio, 4),
            is_ready=mandatory_satisfied,
        )


def standard_job_application_requirements() -> RequirementSet:
    """Factory creating standard requirements for job applications."""
    return RequirementSet(
        purpose="job_application",
        title="Job Application Readiness",
        requirements=[
            ProfileRequirement("req_legal_name", "job_application", "identity", "legal_name", "Legal Full Name", is_mandatory=True),
            ProfileRequirement("req_email", "job_application", "contact", "email", "Contact Email", is_mandatory=True),
            ProfileRequirement("req_phone", "job_application", "contact", "phone", "Contact Phone Number", is_mandatory=False),
            ProfileRequirement("req_experience", "job_application", "career", "roles", "Professional Roles / Experience", is_mandatory=True),
            ProfileRequirement("req_skills", "job_application", "skills", "technical", "Primary Technical Skills", is_mandatory=True),
            ProfileRequirement("req_degree", "job_application", "education", "highest_degree", "Highest Degree / Education", is_mandatory=False),
        ],
    )


def standard_housing_application_requirements() -> RequirementSet:
    """Factory creating standard requirements for housing applications."""
    return RequirementSet(
        purpose="housing_search",
        title="Housing Search Readiness",
        requirements=[
            ProfileRequirement("req_house_name", "housing_search", "identity", "legal_name", "Full Name", is_mandatory=True),
            ProfileRequirement("req_house_email", "housing_search", "contact", "email", "Contact Email", is_mandatory=True),
            ProfileRequirement("req_house_phone", "housing_search", "contact", "phone", "Phone Number", is_mandatory=True),
            ProfileRequirement("req_house_budget", "housing_search", "preferences", "housing_budget", "Target Monthly Budget", is_mandatory=True),
            ProfileRequirement("req_house_location", "housing_search", "preferences", "target_city", "Target City / Area", is_mandatory=True),
        ],
    )
