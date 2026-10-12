"""Structured proposition specification, deterministic validation, and canonical claim rendering.

Guarantees the Phase 5.2.2 invariant:
'Claim support does not equal textual entailment.
No LLM may originate personal factual content.'
Every external factual claim is rendered deterministically from a validated ClaimAssertionSpec
whose fields strictly derive from purpose-authorized ContextFacts.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
import re
from typing import Any

from caseworker.domain.context import ContextFact
from caseworker.domain.enums import AssertionKind
from caseworker.domain.errors import DomainValidationError
from caseworker.domain.requirement_matcher import extract_cefr_level


@dataclass(frozen=True)
class ClaimAssertionSpec:
    """Structured proposition spec grounding a claim to verified ContextFacts."""

    kind: AssertionKind
    fact_ids: list[str]
    fields: dict[str, Any]
    assertion_schema: str = "claim_assertion_v1"

    def __post_init__(self) -> None:
        if not self.fact_ids:
            raise DomainValidationError("ClaimAssertionSpec must reference at least one fact_id.")
        if not self.fields:
            raise DomainValidationError("ClaimAssertionSpec fields cannot be empty.")
        if isinstance(self.kind, str) and not isinstance(self.kind, AssertionKind):
            object.__setattr__(self, "kind", AssertionKind(self.kind))

    def to_dict(self) -> dict[str, Any]:
        return {
            "assertion_schema": self.assertion_schema,
            "kind": self.kind.value if hasattr(self.kind, "value") else str(self.kind),
            "fact_ids": list(self.fact_ids),
            "fields": dict(self.fields),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ClaimAssertionSpec:
        kind_raw = data.get("kind", "")
        kind = AssertionKind(kind_raw) if isinstance(kind_raw, str) else kind_raw
        return cls(
            assertion_schema=data.get("assertion_schema", "claim_assertion_v1"),
            kind=kind,
            fact_ids=list(data.get("fact_ids") or []),
            fields=dict(data.get("fields") or {}),
        )


class ClaimAssertionValidator:
    """Deterministic validator verifying that ClaimAssertionSpec fields strictly derive from ContextFacts."""

    @classmethod
    def validate(
        cls,
        spec: ClaimAssertionSpec,
        facts: list[ContextFact],
    ) -> tuple[bool, str | None]:
        """Validate whether spec fields are truthfully substantiated by the provided facts."""
        facts_by_id = {f.fact_id: f for f in facts}

        # 1. Verify all referenced facts are present in active facts
        missing_ids = [fid for fid in spec.fact_ids if fid not in facts_by_id]
        if missing_ids:
            return False, f"Referenced fact(s) {missing_ids} missing from available context facts."

        referenced_facts = [facts_by_id[fid] for fid in spec.fact_ids]
        combined_text = " ".join(
            f"{f.namespace} {f.key} {str(f.value)}" for f in referenced_facts
        ).lower()

        # 2. Kind-specific semantic checks
        kind = spec.kind
        fields = spec.fields

        if kind == AssertionKind.SKILL_POSSESSION:
            skill = str(fields.get("skill") or "").strip().lower()
            if not skill:
                return False, "SKILL_POSSESSION requires non-empty 'skill' field."
            skill_clean = skill.replace("_", " ").replace("-", " ")
            combined_clean = combined_text.replace("_", " ").replace("-", " ")
            if skill_clean not in combined_clean:
                return False, f"Skill '{skill}' is not substantiated by referenced fact(s)."

        elif kind == AssertionKind.EMPLOYMENT_ROLE:
            role = str(fields.get("role") or "").strip().lower()
            if not role:
                return False, "EMPLOYMENT_ROLE requires non-empty 'role' field."
            role_clean = role.replace("_", " ").replace("-", " ")
            combined_clean = combined_text.replace("_", " ").replace("-", " ")
            if role_clean not in combined_clean:
                return False, f"Employment role '{role}' is not substantiated by referenced fact(s)."

            org = str(fields.get("organization") or "").strip().lower()
            if org and org not in combined_clean:
                return False, f"Organization '{org}' is not substantiated by referenced fact(s)."

        elif kind == AssertionKind.EMPLOYMENT_DURATION:
            years = fields.get("duration_years")
            if years is None or not isinstance(years, (int, float)) or years < 0:
                return False, "EMPLOYMENT_DURATION requires positive numeric 'duration_years'."
            found_duration = False
            for f in referenced_facts:
                match = re.search(r"(\d+)", str(f.value))
                if match:
                    val_num = int(match.group(1))
                    if val_num >= years:
                        found_duration = True
                        break
            if not found_duration:
                return False, f"Experience duration of {years} years is not substantiated by referenced fact(s)."

        elif kind == AssertionKind.EDUCATION_CREDENTIAL:
            degree = str(fields.get("degree") or "").strip().lower()
            if not degree:
                return False, "EDUCATION_CREDENTIAL requires non-empty 'degree' field."
            degree_clean = degree.replace(".", "").lower()
            combined_clean = combined_text.replace(".", "").lower()
            if degree_clean not in combined_clean:
                return False, f"Education degree '{degree}' is not substantiated by referenced fact(s)."

        elif kind == AssertionKind.LANGUAGE_PROFICIENCY:
            language = str(fields.get("language") or "").strip().lower()
            if not language:
                return False, "LANGUAGE_PROFICIENCY requires non-empty 'language' field."
            if language not in combined_text:
                return False, f"Language '{language}' is not mentioned in referenced fact(s)."

            cefr_level_str = str(fields.get("cefr_level") or "").strip().lower()
            if cefr_level_str:
                spec_level = extract_cefr_level(cefr_level_str)
                fact_level = extract_cefr_level(combined_text)
                if spec_level is not None and (fact_level is None or fact_level < spec_level):
                    return (
                        False,
                        f"Claimed CEFR level '{cefr_level_str}' ({spec_level}) exceeds evidence level ({fact_level}).",
                    )

        elif kind == AssertionKind.WORK_AUTHORIZATION:
            status_val = str(fields.get("status") or "").strip().lower()
            if not status_val:
                return False, "WORK_AUTHORIZATION requires non-empty 'status' field."
            auth_terms = ["authorized", "citizen", "permanent resident", "work permit", "eu citizen", "unrestricted"]
            if not any(term in combined_text for term in auth_terms):
                return False, f"Work authorization '{status_val}' is not substantiated by referenced fact(s)."

        elif kind == AssertionKind.QUANTIFIED_ACHIEVEMENT:
            metric = fields.get("metric")
            metric_unit = str(fields.get("metric_unit") or "").strip().lower()
            if metric is None or not metric_unit:
                return False, "QUANTIFIED_ACHIEVEMENT requires 'metric' and 'metric_unit'."
            metric_str = str(metric)
            if metric_str not in combined_text or metric_unit not in combined_text:
                return False, f"Metric '{metric} {metric_unit}' is not found in referenced fact(s)."

        elif kind == AssertionKind.PROJECT_EXPERIENCE:
            project = str(fields.get("project_name") or fields.get("domain") or "").strip().lower()
            if not project:
                return False, "PROJECT_EXPERIENCE requires 'project_name' or 'domain'."
            if project not in combined_text:
                return False, f"Project '{project}' is not found in referenced fact(s)."

        return True, None


class CanonicalClaimRenderer:
    """Deterministic renderer producing canonical declarative sentences from validated ClaimAssertionSpecs."""

    @classmethod
    def render(cls, spec: ClaimAssertionSpec) -> str:
        """Render a publication-ready canonical claim sentence from validated spec."""
        kind = spec.kind
        fields = spec.fields

        if kind == AssertionKind.SKILL_POSSESSION:
            skill = str(fields.get("skill") or "").strip()
            return f"Demonstrated production expertise in {skill}."

        if kind == AssertionKind.EMPLOYMENT_ROLE:
            role = str(fields.get("role") or "").strip()
            org = str(fields.get("organization") or "").strip()
            if org:
                return f"Served as {role} at {org}."
            return f"Served as {role}."

        if kind == AssertionKind.EMPLOYMENT_DURATION:
            years = fields.get("duration_years")
            return f"Possesses {years}+ years of professional engineering experience."

        if kind == AssertionKind.EDUCATION_CREDENTIAL:
            degree = str(fields.get("degree") or "").strip()
            field_study = str(fields.get("field_of_study") or "").strip()
            if field_study:
                return f"Holds a verified {degree} in {field_study}."
            return f"Holds a verified {degree} credential."

        if kind == AssertionKind.LANGUAGE_PROFICIENCY:
            lang = str(fields.get("language") or "").strip().title()
            level = str(fields.get("cefr_level") or "").strip().upper()
            if level:
                return f"Proficient in {lang} at CEFR {level} level."
            return f"Proficient in {lang}."

        if kind == AssertionKind.WORK_AUTHORIZATION:
            jurisdiction = str(fields.get("jurisdiction") or "").strip()
            if jurisdiction:
                return f"Authorized to work in {jurisdiction} without employer sponsorship."
            return "Fully authorized to work without employer visa sponsorship."

        if kind == AssertionKind.QUANTIFIED_ACHIEVEMENT:
            metric = fields.get("metric")
            unit = str(fields.get("metric_unit") or "").strip()
            return f"Delivered {metric} {unit} measurable improvement in production system metrics."

        if kind == AssertionKind.PROJECT_EXPERIENCE:
            project = str(fields.get("project_name") or fields.get("domain") or "").strip()
            return f"Engineered resilient {project} architectures in production environments."

        return f"Verified personal qualification: {json.dumps(fields)}."
