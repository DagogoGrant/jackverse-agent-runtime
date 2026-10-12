"""Tri-state eligibility evaluation and explainable fit scoring for opportunities."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
import json
import re
from typing import Any
import uuid

from caseworker.domain.context import ContextFact
from caseworker.domain.enums import (
    EligibilityStatus,
    FitTier,
    RequirementCategory,
    RequirementMatchResult,
    VerificationStatus,
)
from caseworker.domain.normalized_requirement import NormalizedRequirement
from caseworker.domain.opportunity import Opportunity
from caseworker.domain.types import canonical_json_dumps, compute_sha256, from_iso_utc, now_utc, to_iso_utc


@dataclass
class RequirementEvidence:
    """Safe projection of user context evidence for a single requirement.

    CRITICAL PRIVACY INVARIANT:
    Never store raw context fact values or sensitive data in RequirementEvidence.
    Only store pointers (fact_id, fact_version), verification status, and match results.
    """

    requirement_id: str
    fact_id: str | None = None
    fact_version: int | None = None
    verification_status: VerificationStatus | str | None = None
    match_result: RequirementMatchResult = RequirementMatchResult.UNKNOWN
    notes: str = ""

    def to_dict(self) -> dict[str, Any]:
        v_status = self.verification_status
        if isinstance(v_status, VerificationStatus):
            v_status = v_status.value
        return {
            "requirement_id": self.requirement_id,
            "fact_id": self.fact_id,
            "fact_version": self.fact_version,
            "verification_status": v_status,
            "match_result": self.match_result.value if isinstance(self.match_result, RequirementMatchResult) else str(self.match_result),
            "notes": self.notes,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> RequirementEvidence:
        res = data.get("match_result", RequirementMatchResult.UNKNOWN)
        if isinstance(res, str):
            try:
                res = RequirementMatchResult(res)
            except ValueError:
                res = RequirementMatchResult.UNKNOWN
        return cls(
            requirement_id=data["requirement_id"],
            fact_id=data.get("fact_id"),
            fact_version=data.get("fact_version"),
            verification_status=data.get("verification_status"),
            match_result=res,
            notes=data.get("notes", ""),
        )


@dataclass
class EligibilityResult:
    """Tri-state eligibility evaluation output."""

    status: EligibilityStatus = EligibilityStatus.UNKNOWN
    coverage: float = 0.0  # Fraction of mandatory requirements with non-UNKNOWN evidence (0.0 to 1.0)
    mandatory_met: int = 0
    mandatory_total: int = 0
    contradicted_requirements: list[str] = field(default_factory=list)
    missing_or_unknown_requirements: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status.value if isinstance(self.status, EligibilityStatus) else str(self.status),
            "coverage": round(self.coverage, 4),
            "mandatory_met": self.mandatory_met,
            "mandatory_total": self.mandatory_total,
            "contradicted_requirements": list(self.contradicted_requirements),
            "missing_or_unknown_requirements": list(self.missing_or_unknown_requirements),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> EligibilityResult:
        st = data.get("status", EligibilityStatus.UNKNOWN)
        if isinstance(st, str):
            try:
                st = EligibilityStatus(st)
            except ValueError:
                st = EligibilityStatus.UNKNOWN
        return cls(
            status=st,
            coverage=float(data.get("coverage", 0.0)),
            mandatory_met=int(data.get("mandatory_met", 0)),
            mandatory_total=int(data.get("mandatory_total", 0)),
            contradicted_requirements=list(data.get("contradicted_requirements", [])),
            missing_or_unknown_requirements=list(data.get("missing_or_unknown_requirements", [])),
        )


@dataclass
class FitScoreComponents:
    """Explainable components summing up to total fit score (0-100)."""

    skills_score: float = 0.0          # 0.0 to 50.0
    preferred_reqs_score: float = 0.0  # 0.0 to 20.0
    location_score: float = 0.0        # 0.0 to 15.0
    seniority_score: float = 0.0       # 0.0 to 15.0
    total_score: float = 0.0           # 0.0 to 100.0
    fit_tier: FitTier = FitTier.LOW
    fit_coverage: float = 0.0          # 0.0 to 1.0 (applicable weight / total possible weight)
    explanations: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "skills_score": round(self.skills_score, 2),
            "preferred_reqs_score": round(self.preferred_reqs_score, 2),
            "location_score": round(self.location_score, 2),
            "seniority_score": round(self.seniority_score, 2),
            "total_score": round(self.total_score, 2),
            "fit_tier": self.fit_tier.value if isinstance(self.fit_tier, FitTier) else str(self.fit_tier),
            "fit_coverage": round(self.fit_coverage, 4),
            "explanations": list(self.explanations),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> FitScoreComponents:
        ft = data.get("fit_tier", FitTier.LOW)
        if isinstance(ft, str):
            try:
                ft = FitTier(ft)
            except ValueError:
                ft = FitTier.LOW
        return cls(
            skills_score=float(data.get("skills_score", 0.0)),
            preferred_reqs_score=float(data.get("preferred_reqs_score", 0.0)),
            location_score=float(data.get("location_score", 0.0)),
            seniority_score=float(data.get("seniority_score", 0.0)),
            total_score=float(data.get("total_score", 0.0)),
            fit_tier=ft,
            fit_coverage=float(data.get("fit_coverage", 0.0)),
            explanations=list(data.get("explanations", [])),
        )


@dataclass
class FitAssessment:
    """Explainable fit assessment result."""

    components: FitScoreComponents = field(default_factory=FitScoreComponents)
    scoring_policy: str = "fit_policy_v1"

    def to_dict(self) -> dict[str, Any]:
        return {
            "components": self.components.to_dict(),
            "scoring_policy": self.scoring_policy,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> FitAssessment:
        return cls(
            components=FitScoreComponents.from_dict(data.get("components", {})),
            scoring_policy=data.get("scoring_policy", "fit_policy_v1"),
        )


@dataclass
class OpportunityEvaluationSnapshot:
    """Versioned immutable snapshot of opportunity evaluation."""

    evaluation_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    evaluated_at: datetime = field(default_factory=now_utc)
    evaluation_policy: str = "opp_eval_v1"
    eligibility: EligibilityResult = field(default_factory=EligibilityResult)
    fit: FitAssessment = field(default_factory=FitAssessment)
    evidence: list[RequirementEvidence] = field(default_factory=list)
    evaluation_input_hash: str = ""
    context_snapshot_hash: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "evaluation_id": self.evaluation_id,
            "evaluated_at": to_iso_utc(self.evaluated_at),
            "evaluation_policy": self.evaluation_policy,
            "eligibility": self.eligibility.to_dict(),
            "fit": self.fit.to_dict(),
            "evidence": [e.to_dict() for e in self.evidence],
            "evaluation_input_hash": self.evaluation_input_hash,
            "context_snapshot_hash": self.context_snapshot_hash,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> OpportunityEvaluationSnapshot:
        return cls(
            evaluation_id=data.get("evaluation_id") or str(uuid.uuid4()),
            evaluated_at=from_iso_utc(data.get("evaluated_at")) or now_utc(),
            evaluation_policy=data.get("evaluation_policy", "opp_eval_v1"),
            eligibility=EligibilityResult.from_dict(data.get("eligibility", {})),
            fit=FitAssessment.from_dict(data.get("fit", {})),
            evidence=[RequirementEvidence.from_dict(e) for e in data.get("evidence", [])],
            evaluation_input_hash=data.get("evaluation_input_hash", ""),
            context_snapshot_hash=data.get("context_snapshot_hash", ""),
        )


from caseworker.domain.requirement_matcher import (
    RequirementMatcher,
    extract_cefr_level,
    is_fact_verified,
)

_extract_cefr_level = extract_cefr_level
_is_fact_verified = is_fact_verified


class OpportunityEvaluator:
    """Evaluates an Opportunity against purpose-gated user ContextFacts."""

    CRITERION_TO_NAMESPACES = RequirementMatcher.CRITERION_TO_NAMESPACES

    @classmethod
    def get_relevant_context_facts(
        cls,
        normalized_requirements: list[NormalizedRequirement],
        active_facts: list[ContextFact],
    ) -> list[ContextFact]:
        """Filter active facts to those in namespaces relevant to requirement criteria."""
        target_namespaces: set[str] = set()
        for req in normalized_requirements:
            ns_set = cls.CRITERION_TO_NAMESPACES.get(req.criterion)
            if ns_set:
                target_namespaces.update(ns_set)

        if not target_namespaces:
            target_namespaces = {
                "skills",
                "skill",
                "experience",
                "career",
                "education",
                "legal",
                "citizenship",
                "work_authorization",
                "languages",
                "language",
                "preferences",
                "location",
                "seniority",
            }

        return [f for f in active_facts if f.namespace.lower() in target_namespaces]

    @staticmethod
    def compute_context_snapshot_hash(granted_facts: list[ContextFact]) -> str:
        """Compute deterministic hash from relevant evidence facts (fact_id, version)."""
        components = sorted([(f.fact_id, f.version) for f in granted_facts])
        return compute_sha256(canonical_json_dumps(components))

    @classmethod
    def evaluate(
        cls,
        opportunity: Opportunity,
        normalized_requirements: list[NormalizedRequirement],
        context_facts: list[ContextFact],
        mission_goal: str = "",
        mission_constraints: list[str] | None = None,
    ) -> OpportunityEvaluationSnapshot:
        """Perform tri-state eligibility evaluation and explainable fit scoring."""
        # 1. Filter active facts (not superseded, not rejected)
        active_facts = [
            f for f in context_facts
            if f.superseded_by_fact_id is None
            and f.verification_status != VerificationStatus.REJECTED
        ]

        # Index active facts by namespace and keys for efficient matching
        facts_by_ns: dict[str, list[ContextFact]] = {}
        for f in active_facts:
            facts_by_ns.setdefault(f.namespace.lower(), []).append(f)

        evidence_list: list[RequirementEvidence] = []
        contradicted_ids: list[str] = []
        unknown_ids: list[str] = []
        mandatory_total = 0
        mandatory_met = 0

        for req in normalized_requirements:
            is_mandatory = req.category == RequirementCategory.MANDATORY
            if is_mandatory:
                mandatory_total += 1

            # If unparsed / custom, we cannot verify it automatically -> UNKNOWN
            if req.normalization_status == "unparsed" or req.criterion == "custom":
                evidence = RequirementEvidence(
                    requirement_id=req.requirement_id,
                    match_result=RequirementMatchResult.UNKNOWN,
                    notes=f"Unparsed custom requirement cannot be automatically verified: '{req.raw_text}'",
                )
                evidence_list.append(evidence)
                if is_mandatory:
                    unknown_ids.append(req.requirement_id)
                continue

            # Evaluate parsed criteria against context facts
            match_res, matched_fact, notes = cls._match_requirement(req, facts_by_ns, active_facts)

            fact_id = matched_fact.fact_id if matched_fact else None
            fact_ver = matched_fact.version if matched_fact else None
            v_status = matched_fact.verification_status if matched_fact else None

            evidence = RequirementEvidence(
                requirement_id=req.requirement_id,
                fact_id=fact_id,
                fact_version=fact_ver,
                verification_status=v_status,
                match_result=match_res,
                notes=notes,
            )
            evidence_list.append(evidence)

            if is_mandatory:
                if match_res == RequirementMatchResult.MET:
                    mandatory_met += 1
                elif match_res == RequirementMatchResult.NOT_MET:
                    contradicted_ids.append(req.requirement_id)
                else:
                    unknown_ids.append(req.requirement_id)

        # 2. Tri-state Eligibility determination
        if len(contradicted_ids) > 0:
            elig_status = EligibilityStatus.INELIGIBLE
        elif mandatory_total == 0 or mandatory_met == mandatory_total:
            elig_status = EligibilityStatus.ELIGIBLE
        else:
            elig_status = EligibilityStatus.UNKNOWN

        coverage = (
            (mandatory_met + len(contradicted_ids)) / mandatory_total
            if mandatory_total > 0
            else 1.0
        )

        eligibility_result = EligibilityResult(
            status=elig_status,
            coverage=coverage,
            mandatory_met=mandatory_met,
            mandatory_total=mandatory_total,
            contradicted_requirements=contradicted_ids,
            missing_or_unknown_requirements=unknown_ids,
        )

        # 3. Explainable Fit Scoring with coverage guard
        fit_components = cls._calculate_fit(
            opportunity=opportunity,
            normalized_requirements=normalized_requirements,
            evidence_list=evidence_list,
            facts_by_ns=facts_by_ns,
            active_facts=active_facts,
        )
        fit_assessment = FitAssessment(
            components=fit_components,
            scoring_policy="fit_policy_v1",
        )

        # 4. Compute Hashes
        input_hash = opportunity.compute_input_hash(
            mission_goal=mission_goal,
            mission_constraints=mission_constraints,
        )
        relevant_facts = cls.get_relevant_context_facts(normalized_requirements, active_facts)
        context_hash = cls.compute_context_snapshot_hash(relevant_facts)

        return OpportunityEvaluationSnapshot(
            evaluation_id=str(uuid.uuid4()),
            evaluated_at=now_utc(),
            evaluation_policy="opp_eval_v1",
            eligibility=eligibility_result,
            fit=fit_assessment,
            evidence=evidence_list,
            evaluation_input_hash=input_hash,
            context_snapshot_hash=context_hash,
        )

    @classmethod
    def _match_requirement(
        cls,
        req: NormalizedRequirement,
        facts_by_ns: dict[str, list[ContextFact]],
        active_facts: list[ContextFact],
    ) -> tuple[RequirementMatchResult, ContextFact | None, str]:
        """Match a normalized requirement against relevant active facts."""
        return RequirementMatcher.match(req, facts_by_ns, active_facts)

    @classmethod
    def _calculate_fit(
        cls,
        opportunity: Opportunity,
        normalized_requirements: list[NormalizedRequirement],
        evidence_list: list[RequirementEvidence],
        facts_by_ns: dict[str, list[ContextFact]],
        active_facts: list[ContextFact],
    ) -> FitScoreComponents:
        """Calculate explainable fit components summing up to 100 with coverage guard."""
        evidence_by_req_id = {e.requirement_id: e for e in evidence_list}
        explanations: list[str] = []

        applicable_weight = 0.0

        # 1. Skills Score (0 - 50)
        skill_reqs = [r for r in normalized_requirements if r.criterion == "skill"]
        if skill_reqs:
            applicable_weight += 50.0
            skills_met = 0
            skills_unverified = 0
            for r in skill_reqs:
                ev = evidence_by_req_id.get(r.requirement_id)
                if ev is not None and ev.match_result == RequirementMatchResult.MET:
                    if _is_fact_verified(ev.verification_status):
                        skills_met += 1
                    else:
                        skills_unverified += 1
            skills_score = round(50.0 * (skills_met / len(skill_reqs)), 2)
            if skills_unverified > 0:
                explanations.append(f"Skills: {skills_met}/{len(skill_reqs)} verified matched ({skills_score}/50 pts; {skills_unverified} unverified potential match)")
            else:
                explanations.append(f"Skills: {skills_met}/{len(skill_reqs)} matched ({skills_score}/50 pts)")
        else:
            skills_score = 0.0
            explanations.append("Skills: No explicit skill requirements parsed (0/0 weight)")

        # 2. Preferred Requirements Score (0 - 20)
        preferred_reqs = [r for r in normalized_requirements if r.category == RequirementCategory.PREFERRED]
        if preferred_reqs:
            applicable_weight += 20.0
            pref_met = 0
            pref_unverified = 0
            for r in preferred_reqs:
                ev = evidence_by_req_id.get(r.requirement_id)
                if ev is not None and ev.match_result == RequirementMatchResult.MET:
                    if _is_fact_verified(ev.verification_status):
                        pref_met += 1
                    else:
                        pref_unverified += 1
            pref_score = round(20.0 * (pref_met / len(preferred_reqs)), 2)
            if pref_unverified > 0:
                explanations.append(f"Preferred: {pref_met}/{len(preferred_reqs)} verified met ({pref_score}/20 pts; {pref_unverified} unverified potential)")
            else:
                explanations.append(f"Preferred: {pref_met}/{len(preferred_reqs)} met ({pref_score}/20 pts)")
        else:
            pref_score = 0.0
            explanations.append("Preferred: No preferred requirements listed (0/0 weight)")

        # 3. Location Score (0 - 15)
        loc_str = (opportunity.location or "").lower()
        has_loc_req = any(r.criterion == "location" for r in normalized_requirements)
        if loc_str or has_loc_req:
            applicable_weight += 15.0
            if "remote" in loc_str:
                location_score = 15.0
                explanations.append("Location: Fully remote position (15/15 pts)")
            else:
                loc_facts = facts_by_ns.get("preferences", []) + facts_by_ns.get("location", [])
                matched_loc = False
                unverified_loc = False
                for lf in loc_facts:
                    if any(place in str(lf.value).lower() for place in loc_str.split() if len(place) > 3):
                        if _is_fact_verified(lf.verification_status):
                            matched_loc = True
                            break
                        else:
                            unverified_loc = True
                if matched_loc:
                    location_score = 15.0
                    explanations.append(f"Location: Local match for '{opportunity.location}' (15/15 pts)")
                elif unverified_loc:
                    location_score = 0.0
                    explanations.append(f"Location: Potential match for '{opportunity.location}' pending verification (0/15 pts, unverified alignment)")
                else:
                    location_score = 0.0
                    explanations.append(f"Location: Location '{opportunity.location}' compatibility pending (0/15 pts)")
        else:
            location_score = 0.0
            explanations.append("Location: Unspecified location (0/0 weight)")

        # 4. Seniority Score (0 - 15)
        sen_reqs = [r for r in normalized_requirements if r.criterion in ("seniority", "years_experience")]
        if sen_reqs:
            applicable_weight += 15.0
            sen_met = 0
            sen_unverified = 0
            for r in sen_reqs:
                ev = evidence_by_req_id.get(r.requirement_id)
                if ev is not None and ev.match_result == RequirementMatchResult.MET:
                    if _is_fact_verified(ev.verification_status):
                        sen_met += 1
                    else:
                        sen_unverified += 1
            sen_score = round(15.0 * (sen_met / len(sen_reqs)), 2)
            if sen_unverified > 0:
                explanations.append(f"Seniority: Experience aligned ({sen_score}/15 pts; {sen_unverified} unverified potential)")
            else:
                explanations.append(f"Seniority: Experience aligned ({sen_score}/15 pts)")
        else:
            sen_score = 0.0
            explanations.append("Seniority: No seniority requirements listed (0/0 weight)")

        # Coverage guard: ratio of applicable weight to total policy maximum (100.0)
        fit_coverage = round(applicable_weight / 100.0, 4)
        earned_points = skills_score + pref_score + location_score + sen_score

        if applicable_weight > 0.0:
            total = round(min(100.0, max(0.0, (earned_points / applicable_weight) * 100.0)), 2)
        else:
            total = 0.0

        if fit_coverage < 0.40:
            tier = FitTier.INSUFFICIENT_EVIDENCE
            explanations.insert(
                0,
                f"Limited fit signal (coverage {int(fit_coverage * 100)}%); Insufficient evidence for strong-fit determination",
            )
        elif total >= 80.0:
            tier = FitTier.STRONG
        elif total >= 55.0:
            tier = FitTier.MODERATE
        else:
            tier = FitTier.LOW

        return FitScoreComponents(
            skills_score=skills_score,
            preferred_reqs_score=pref_score,
            location_score=location_score,
            seniority_score=sen_score,
            total_score=total,
            fit_tier=tier,
            fit_coverage=fit_coverage,
            explanations=explanations,
        )
