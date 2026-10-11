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
    explanations: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "skills_score": round(self.skills_score, 2),
            "preferred_reqs_score": round(self.preferred_reqs_score, 2),
            "location_score": round(self.location_score, 2),
            "seniority_score": round(self.seniority_score, 2),
            "total_score": round(self.total_score, 2),
            "fit_tier": self.fit_tier.value if isinstance(self.fit_tier, FitTier) else str(self.fit_tier),
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


class OpportunityEvaluator:
    """Evaluates an Opportunity against purpose-gated user ContextFacts."""

    @staticmethod
    def compute_context_snapshot_hash(granted_facts: list[ContextFact]) -> str:
        """Compute deterministic hash solely from granted evidence facts (fact_id, version)."""
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

        # We also collect facts that actually provided evidence to hash the context snapshot
        evidence_facts_used: set[str] = set()

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

            if matched_fact:
                evidence_facts_used.add(matched_fact.fact_id)

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
        # INELIGIBLE if any mandatory requirement is positively contradicted
        if len(contradicted_ids) > 0:
            elig_status = EligibilityStatus.INELIGIBLE
        # ELIGIBLE if all mandatory requirements are met
        elif mandatory_total == 0 or mandatory_met == mandatory_total:
            elig_status = EligibilityStatus.ELIGIBLE
        # Otherwise UNKNOWN (missing evidence or unparsed mandatory requirements)
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

        # 3. Explainable Fit Scoring (0 - 100)
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
        used_facts_list = [f for f in active_facts if f.fact_id in evidence_facts_used]
        # If no specific facts were matched, snapshot hash includes all active facts
        context_hash = cls.compute_context_snapshot_hash(used_facts_list or active_facts)

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
        crit = req.criterion

        if crit == "skill":
            skill_name = str(req.value).lower() if req.value else ""
            # Search skills namespace or any fact mentioning skill
            skill_facts = facts_by_ns.get("skills", []) + facts_by_ns.get("skill", [])
            for sf in skill_facts:
                val_str = str(sf.value).lower()
                key_str = sf.key.lower()
                if skill_name in val_str or skill_name in key_str:
                    return RequirementMatchResult.MET, sf, f"Found verified skill in context"
            # Check general facts
            for af in active_facts:
                if skill_name in str(af.value).lower() or skill_name in af.key.lower():
                    return RequirementMatchResult.MET, af, f"Found matching skill mention"
            return RequirementMatchResult.UNKNOWN, None, f"Skill not recorded in context vault"

        elif crit == "years_experience":
            needed_years = int(req.value) if isinstance(req.value, (int, float)) else 0
            exp_facts = facts_by_ns.get("experience", []) + facts_by_ns.get("career", [])
            for ef in exp_facts:
                if "years" in ef.key.lower() or "experience" in ef.key.lower():
                    try:
                        # Extract int from value
                        val_num = int(re.search(r"\d+", str(ef.value)).group(0))
                        if val_num >= needed_years:
                            return RequirementMatchResult.MET, ef, f"User has sufficient years of experience"
                        else:
                            return RequirementMatchResult.NOT_MET, ef, f"User experience is below required threshold"
                    except Exception:
                        pass
            return RequirementMatchResult.UNKNOWN, None, f"Experience duration not verified in context vault"

        elif crit == "degree":
            req_degree = str(req.value).lower().replace(".", "")
            edu_facts = facts_by_ns.get("education", [])
            for ef in edu_facts:
                val_str = str(ef.value).lower().replace(".", "")
                key_str = ef.key.lower()
                is_doctorate = any(t in val_str or t in key_str for t in ["phd", "doctorate", "doctoral"])
                is_master = any(t in val_str or t in key_str for t in ["master", "msc", "ma", "ms"]) or is_doctorate
                is_bachelor = any(t in val_str or t in key_str for t in ["bachelor", "bsc", "ba", "bs"]) or is_master

                if any(t in req_degree for t in ["phd", "doctorate", "doctoral"]):
                    if is_doctorate:
                        return RequirementMatchResult.MET, ef, "User doctorate satisfies requirement"
                elif any(t in req_degree for t in ["master", "ms"]):
                    if is_master:
                        return RequirementMatchResult.MET, ef, "User degree satisfies master requirement"
                elif any(t in req_degree for t in ["bachelor", "bs", "degree"]):
                    if is_bachelor:
                        return RequirementMatchResult.MET, ef, "User degree satisfies bachelor requirement"
                elif req_degree in val_str or req_degree in key_str:
                    return RequirementMatchResult.MET, ef, "User degree matches requirement"
            return RequirementMatchResult.UNKNOWN, None, "Degree not found in context vault"

        elif crit == "work_authorization":
            req_text = str(req.value).lower()
            auth_facts = facts_by_ns.get("legal", []) + facts_by_ns.get("citizenship", []) + facts_by_ns.get("work_authorization", [])
            for af in auth_facts:
                val_str = str(af.value).lower()
                if "unauthorized" in val_str or "no work permit" in val_str or "requires visa" in val_str:
                    return RequirementMatchResult.NOT_MET, af, f"User context explicitly notes lack of authorization"
                if any(w in val_str for w in ["eu citizen", "authorized", "permanent resident", "citizen", "work permit"]):
                    return RequirementMatchResult.MET, af, f"User has verified work authorization"
            return RequirementMatchResult.UNKNOWN, None, f"Work authorization not documented in context vault"

        elif crit == "language":
            req_lang = str(req.value).lower()
            lang_facts = facts_by_ns.get("languages", []) + facts_by_ns.get("language", [])
            for lf in lang_facts:
                val_str = str(lf.value).lower()
                key_str = lf.key.lower()
                if any(lang in key_str or lang in val_str for lang in ["english", "german", "french", "spanish"] if lang in req_lang):
                    # Check level contradiction if explicit
                    return RequirementMatchResult.MET, lf, f"Language documented in context vault"
            return RequirementMatchResult.UNKNOWN, None, f"Language proficiency not documented in context vault"

        elif crit == "location":
            req_loc = str(req.value).lower()
            if "remote" in req_loc:
                return RequirementMatchResult.MET, None, "Remote position satisfies location criteria"
            pref_facts = facts_by_ns.get("preferences", []) + facts_by_ns.get("location", [])
            for pf in pref_facts:
                val_str = str(pf.value).lower()
                if any(city in val_str for city in ["berlin", "munich", "germany", "eu", "london", "zurich"] if city in req_loc):
                    return RequirementMatchResult.MET, pf, "User location matches position"
            return RequirementMatchResult.UNKNOWN, None, f"Location compatibility unknown"

        elif crit == "seniority":
            req_sen = str(req.value).lower()
            career_facts = facts_by_ns.get("career", []) + facts_by_ns.get("seniority", [])
            for cf in career_facts:
                val_str = str(cf.value).lower()
                if req_sen in val_str:
                    return RequirementMatchResult.MET, cf, "Seniority level aligns"
            return RequirementMatchResult.UNKNOWN, None, f"Seniority level not documented"

        return RequirementMatchResult.UNKNOWN, None, "No specific matching rule applied"

    @classmethod
    def _calculate_fit(
        cls,
        opportunity: Opportunity,
        normalized_requirements: list[NormalizedRequirement],
        evidence_list: list[RequirementEvidence],
        facts_by_ns: dict[str, list[ContextFact]],
        active_facts: list[ContextFact],
    ) -> FitScoreComponents:
        """Calculate explainable fit components summing up to 100."""
        evidence_by_req_id = {e.requirement_id: e for e in evidence_list}
        explanations: list[str] = []

        # 1. Skills Score (0 - 50)
        skill_reqs = [r for r in normalized_requirements if r.criterion == "skill"]
        if skill_reqs:
            skills_met = sum(
                1 for r in skill_reqs
                if evidence_by_req_id.get(r.requirement_id, None) is not None
                and evidence_by_req_id[r.requirement_id].match_result == RequirementMatchResult.MET
            )
            skills_score = round(50.0 * (skills_met / len(skill_reqs)), 2)
            explanations.append(f"Skills: {skills_met}/{len(skill_reqs)} matched ({skills_score}/50 pts)")
        else:
            # Baseline when no explicit skill requirements are listed
            skills_score = 35.0
            explanations.append(f"Skills: No specific skill requirements parsed (default 35/50 pts)")

        # 2. Preferred Requirements Score (0 - 20)
        preferred_reqs = [r for r in normalized_requirements if r.category == RequirementCategory.PREFERRED]
        if preferred_reqs:
            pref_met = sum(
                1 for r in preferred_reqs
                if evidence_by_req_id.get(r.requirement_id, None) is not None
                and evidence_by_req_id[r.requirement_id].match_result == RequirementMatchResult.MET
            )
            pref_score = round(20.0 * (pref_met / len(preferred_reqs)), 2)
            explanations.append(f"Preferred: {pref_met}/{len(preferred_reqs)} met ({pref_score}/20 pts)")
        else:
            pref_score = 15.0
            explanations.append(f"Preferred: No bonus requirements listed (default 15/20 pts)")

        # 3. Location Score (0 - 15)
        loc_str = (opportunity.location or "").lower()
        if "remote" in loc_str:
            location_score = 15.0
            explanations.append("Location: Fully remote position (15/15 pts)")
        else:
            # Check user location preference
            loc_facts = facts_by_ns.get("preferences", []) + facts_by_ns.get("location", [])
            matched_loc = False
            for lf in loc_facts:
                if any(place in str(lf.value).lower() for place in loc_str.split() if len(place) > 3):
                    matched_loc = True
                    break
            if matched_loc:
                location_score = 15.0
                explanations.append(f"Location: Local match for '{opportunity.location}' (15/15 pts)")
            elif not loc_str:
                location_score = 10.0
                explanations.append("Location: Unspecified location (10/15 pts)")
            else:
                location_score = 8.0
                explanations.append(f"Location: Location '{opportunity.location}' compatibility pending (8/15 pts)")

        # 4. Seniority Score (0 - 15)
        sen_reqs = [r for r in normalized_requirements if r.criterion in ("seniority", "years_experience")]
        if sen_reqs:
            sen_met = sum(
                1 for r in sen_reqs
                if evidence_by_req_id.get(r.requirement_id, None) is not None
                and evidence_by_req_id[r.requirement_id].match_result == RequirementMatchResult.MET
            )
            sen_score = round(15.0 * (sen_met / len(sen_reqs)), 2)
            explanations.append(f"Seniority: Experience aligned ({sen_score}/15 pts)")
        else:
            sen_score = 12.0
            explanations.append("Seniority: Open level / standard seniority (12/15 pts)")

        total = round(min(100.0, max(0.0, skills_score + pref_score + location_score + sen_score)), 2)

        if total >= 80.0:
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
            explanations=explanations,
        )
