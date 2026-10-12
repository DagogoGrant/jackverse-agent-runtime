"""Unified deterministic requirement matching primitive.

Shared by both OpportunityEvaluator (Phase 5.1) and PreparationAnalyzer (Phase 5.2)
to guarantee identical matching semantics across discovery and preparation.
"""

from __future__ import annotations

import re
from typing import Any

from caseworker.domain.context import ContextFact
from caseworker.domain.enums import (
    EvidenceAlignmentStatus,
    RequirementCategory,
    RequirementMatchResult,
    VerificationStatus,
)
from caseworker.domain.normalized_requirement import NormalizedRequirement

_CEFR_LEVELS: dict[str, int] = {
    "a1": 1,
    "beginner": 1,
    "basic": 1,
    "elementary": 1,
    "a2": 2,
    "pre-intermediate": 2,
    "b1": 3,
    "intermediate": 3,
    "b2": 4,
    "upper-intermediate": 4,
    "professional": 4,
    "c1": 5,
    "advanced": 5,
    "fluent": 5,
    "c2": 6,
    "mastery": 6,
    "native": 6,
    "bilingual": 6,
}


def extract_cefr_level(text: str) -> int | None:
    """Extract CEFR proficiency level integer rank (1-6) from arbitrary text."""
    if not text:
        return None
    normalized = text.lower()
    for key, lvl in sorted(_CEFR_LEVELS.items(), key=lambda kv: len(kv[0]), reverse=True):
        if re.search(r"\b" + re.escape(key) + r"\b", normalized):
            return lvl
    return None


def is_fact_verified(verification_status: VerificationStatus | str | None) -> bool:
    """Check if a ContextFact verification status is USER_VERIFIED or SOURCE_VERIFIED."""
    if verification_status is None:
        return False
    if isinstance(verification_status, VerificationStatus):
        return verification_status in (VerificationStatus.USER_VERIFIED, VerificationStatus.SOURCE_VERIFIED)
    return str(verification_status).lower() in (
        VerificationStatus.USER_VERIFIED.value,
        VerificationStatus.SOURCE_VERIFIED.value,
        "user_verified",
        "source_verified",
    )


class RequirementMatcher:
    """Deterministic matcher evaluating NormalizedRequirement against ContextFacts."""

    CRITERION_TO_NAMESPACES: dict[str, set[str]] = {
        "skill": {"skills", "skill"},
        "years_experience": {"experience", "career"},
        "degree": {"education"},
        "work_authorization": {"legal", "citizenship", "work_authorization"},
        "language": {"languages", "language"},
        "location": {"preferences", "location"},
        "seniority": {"career", "seniority"},
    }

    @classmethod
    def match(
        cls,
        req: NormalizedRequirement,
        facts_by_ns: dict[str, list[ContextFact]],
        active_facts: list[ContextFact],
    ) -> tuple[RequirementMatchResult, ContextFact | None, str]:
        """Match a normalized requirement against relevant active facts."""
        crit = req.criterion
        match_res = RequirementMatchResult.UNKNOWN
        matched_fact: ContextFact | None = None
        notes = "No specific matching rule applied"

        if crit == "skill":
            skill_name = str(req.value).lower() if req.value else ""
            skill_facts = facts_by_ns.get("skills", []) + facts_by_ns.get("skill", [])
            for sf in skill_facts:
                val_str = str(sf.value).lower()
                key_str = sf.key.lower()
                if skill_name in val_str or skill_name in key_str:
                    match_res, matched_fact, notes = RequirementMatchResult.MET, sf, "Found skill in context"
                    break
            if not matched_fact:
                for af in active_facts:
                    if skill_name in str(af.value).lower() or skill_name in af.key.lower():
                        match_res, matched_fact, notes = RequirementMatchResult.MET, af, "Found matching skill mention"
                        break
            if not matched_fact:
                match_res, matched_fact, notes = RequirementMatchResult.UNKNOWN, None, "Skill not recorded in context vault"

        elif crit == "years_experience":
            needed_years = int(req.value) if isinstance(req.value, (int, float)) else 0
            exp_facts = facts_by_ns.get("experience", []) + facts_by_ns.get("career", [])
            found_exp = False
            for ef in exp_facts:
                if "years" in ef.key.lower() or "experience" in ef.key.lower():
                    found_exp = True
                    try:
                        val_num = int(re.search(r"\d+", str(ef.value)).group(0))
                        if val_num >= needed_years:
                            match_res, matched_fact, notes = RequirementMatchResult.MET, ef, "User has sufficient years of experience"
                        else:
                            match_res, matched_fact, notes = RequirementMatchResult.NOT_MET, ef, "User experience is below required threshold"
                        break
                    except Exception:
                        pass
            if not found_exp:
                match_res, matched_fact, notes = RequirementMatchResult.UNKNOWN, None, "Experience duration not verified in context vault"

        elif crit == "degree":
            req_degree = str(req.value).lower().replace(".", "")
            edu_facts = facts_by_ns.get("education", [])
            found_degree = False
            for ef in edu_facts:
                val_str = str(ef.value).lower().replace(".", "")
                key_str = ef.key.lower()
                is_doctorate = any(t in val_str or t in key_str for t in ["phd", "doctorate", "doctoral"])
                is_master = any(t in val_str or t in key_str for t in ["master", "msc", "ma", "ms"]) or is_doctorate
                is_bachelor = any(t in val_str or t in key_str for t in ["bachelor", "bsc", "ba", "bs"]) or is_master

                if any(t in req_degree for t in ["phd", "doctorate", "doctoral"]):
                    if is_doctorate:
                        match_res, matched_fact, notes = RequirementMatchResult.MET, ef, "User doctorate satisfies requirement"
                        found_degree = True
                        break
                elif any(t in req_degree for t in ["master", "ms"]):
                    if is_master:
                        match_res, matched_fact, notes = RequirementMatchResult.MET, ef, "User degree satisfies master requirement"
                        found_degree = True
                        break
                elif any(t in req_degree for t in ["bachelor", "bs", "degree"]):
                    if is_bachelor:
                        match_res, matched_fact, notes = RequirementMatchResult.MET, ef, "User degree satisfies bachelor requirement"
                        found_degree = True
                        break
                elif req_degree in val_str or req_degree in key_str:
                    match_res, matched_fact, notes = RequirementMatchResult.MET, ef, "User degree matches requirement"
                    found_degree = True
                    break
            if not found_degree:
                match_res, matched_fact, notes = RequirementMatchResult.UNKNOWN, None, "Degree not found in context vault"

        elif crit == "work_authorization":
            req_text = (str(req.value) + " " + req.raw_text).lower()
            if "sponsorship" in req_text:
                return RequirementMatchResult.UNKNOWN, None, "Ambiguous visa sponsorship requirement requires manual confirmation"
            auth_facts = facts_by_ns.get("legal", []) + facts_by_ns.get("citizenship", []) + facts_by_ns.get("work_authorization", [])
            found_auth = False
            for af in auth_facts:
                val_str = str(af.value).lower()
                if "sponsorship" in val_str or "requires visa" in val_str:
                    return RequirementMatchResult.UNKNOWN, af, "User context indicates visa sponsorship may be needed; pending manual verification"
                if "unauthorized" in val_str or "no work permit" in val_str:
                    return RequirementMatchResult.NOT_MET, af, "User context explicitly notes lack of authorization"
                if any(w in val_str for w in ["eu citizen", "authorized", "permanent resident", "citizen", "work permit"]):
                    match_res, matched_fact, notes = RequirementMatchResult.MET, af, "User has verified work authorization"
                    found_auth = True
                    break
            if not found_auth:
                match_res, matched_fact, notes = RequirementMatchResult.UNKNOWN, None, "Work authorization not documented in context vault"

        elif crit == "language":
            req_text = (str(req.value) + " " + req.raw_text).lower()
            req_level = extract_cefr_level(req_text)
            lang_facts = facts_by_ns.get("languages", []) + facts_by_ns.get("language", [])
            matched_lf = None
            for lf in lang_facts:
                val_str = str(lf.value).lower()
                key_str = lf.key.lower()
                for lang in ["english", "german", "deutsch", "french", "spanish", "mandarin", "italian"]:
                    if lang in req_text and (lang in key_str or lang in val_str):
                        matched_lf = lf
                        fact_level = extract_cefr_level(val_str) or extract_cefr_level(key_str)
                        if req_level is not None:
                            if fact_level is None:
                                match_res = RequirementMatchResult.UNKNOWN
                                notes = "Required CEFR level specified, but context fact proficiency level is unstated/unknown"
                            elif fact_level < req_level:
                                return RequirementMatchResult.NOT_MET, lf, f"Language level ({fact_level}) is below required level ({req_level})"
                            else:
                                if is_fact_verified(lf.verification_status):
                                    match_res = RequirementMatchResult.MET
                                    notes = f"Language level ({fact_level}) meets or exceeds requirement ({req_level})"
                                else:
                                    match_res = RequirementMatchResult.UNKNOWN
                                    notes = f"Language level ({fact_level}) meets requirement ({req_level}) but context evidence is unverified"
                        else:
                            match_res = RequirementMatchResult.MET
                            notes = "Language documented in context vault"
                        break
                if matched_lf:
                    matched_fact = matched_lf
                    break
            if not matched_fact:
                match_res, matched_fact, notes = RequirementMatchResult.UNKNOWN, None, "Language proficiency not documented in context vault"

        elif crit == "location":
            req_loc = str(req.value).lower()
            if "remote" in req_loc:
                return RequirementMatchResult.MET, None, "Remote position satisfies location criteria"
            pref_facts = facts_by_ns.get("preferences", []) + facts_by_ns.get("location", [])
            matched_loc = False
            for pf in pref_facts:
                val_str = str(pf.value).lower()
                if any(city in val_str for city in ["berlin", "munich", "germany", "eu", "london", "zurich"] if city in req_loc):
                    match_res, matched_fact, notes = RequirementMatchResult.MET, pf, "User location matches position"
                    matched_loc = True
                    break
            if not matched_loc:
                match_res, matched_fact, notes = RequirementMatchResult.UNKNOWN, None, "Location compatibility unknown"

        elif crit == "seniority":
            req_sen = str(req.value).lower()
            career_facts = facts_by_ns.get("career", []) + facts_by_ns.get("seniority", [])
            matched_sen = False
            for cf in career_facts:
                val_str = str(cf.value).lower()
                if req_sen in val_str:
                    match_res, matched_fact, notes = RequirementMatchResult.MET, cf, "Seniority level aligns"
                    matched_sen = True
                    break
            if not matched_sen:
                match_res, matched_fact, notes = RequirementMatchResult.UNKNOWN, None, "Seniority level not documented"

        # HARD ELIGIBILITY VERIFICATION GUARD:
        # Mandatory requirements can ONLY be satisfied (MET) by USER_VERIFIED or SOURCE_VERIFIED context facts.
        # Unverified, claim-only, or pending context facts MUST produce UNKNOWN.
        if match_res == RequirementMatchResult.MET and req.category == RequirementCategory.MANDATORY:
            if matched_fact is not None:
                if not is_fact_verified(matched_fact.verification_status):
                    v_stat = matched_fact.verification_status
                    v_label = v_stat.value if isinstance(v_stat, VerificationStatus) else str(v_stat)
                    return (
                        RequirementMatchResult.UNKNOWN,
                        matched_fact,
                        f"Matched fact '{matched_fact.fact_id}' is unverified ({v_label}); mandatory requirement requires verified evidence",
                    )

        return match_res, matched_fact, notes
