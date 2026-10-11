"""Structured normalized requirement domain entity and parsing logic for opportunities."""

from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import re
from typing import Any
import uuid

from caseworker.domain.enums import RequirementCategory


@dataclass
class NormalizedRequirement:
    """A single normalized requirement extracted from an opportunity description."""

    requirement_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    raw_text: str = ""
    criterion: str = "custom"  # "work_authorization" | "years_experience" | "seniority" | "skill" | "location" | "degree" | "language" | "custom"
    category: RequirementCategory = RequirementCategory.MANDATORY
    value: Any = None
    normalization_status: str = "parsed"  # "parsed" | "unparsed"

    def to_dict(self) -> dict[str, Any]:
        return {
            "requirement_id": self.requirement_id,
            "raw_text": self.raw_text,
            "criterion": self.criterion,
            "category": self.category.value if isinstance(self.category, RequirementCategory) else str(self.category),
            "value": self.value,
            "normalization_status": self.normalization_status,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> NormalizedRequirement:
        cat = data.get("category", RequirementCategory.MANDATORY)
        if isinstance(cat, str):
            try:
                cat = RequirementCategory(cat)
            except ValueError:
                cat = RequirementCategory.MANDATORY
        return cls(
            requirement_id=data.get("requirement_id") or str(uuid.uuid4()),
            raw_text=data.get("raw_text", ""),
            criterion=data.get("criterion", "custom"),
            category=cat,
            value=data.get("value"),
            normalization_status=data.get("normalization_status", "parsed"),
        )


_PREFERRED_RE = re.compile(
    r"\b(preferred|nice to have|plus|bonus|optional|desired|advantage)\b",
    re.IGNORECASE,
)

_YEARS_EXP_RE = re.compile(
    r"(\d+)\+?\s*years?(?:\s+of)?\s+(?:experience|work)",
    re.IGNORECASE,
)

_SENIORITY_RE = re.compile(
    r"\b(intern|internship|junior|entry-level|mid-level|mid|senior|staff|principal|lead|head of|director)\b",
    re.IGNORECASE,
)

_WORK_AUTH_RE = re.compile(
    r"\b(work authorization|work permit|eligible to work|authorized to work|visa sponsorship|visa|eu citizen|us citizen|residence permit)\b",
    re.IGNORECASE,
)

_DEGREE_RE = re.compile(
    r"\b(bachelor'?s?|master'?s?|ph\.?d\.?|doctorate|b\.?s\.?|m\.?s\.?|degree in [a-zA-Z\s]+|university degree)\b",
    re.IGNORECASE,
)

_LANGUAGE_RE = re.compile(
    r"\b(english|german|deutsch|french|spanish|mandarin|italian)\b(?:\s+(?:fluent|native|proficient|b1|b2|c1|c2|a1|a2))?",
    re.IGNORECASE,
)

_LOCATION_RE = re.compile(
    r"\b(remote|onsite|on-site|hybrid|relocation|berlin|munich|frankfurt|hamburg|london|zurich|amsterdam|paris|eu|germany|remote-first)\b",
    re.IGNORECASE,
)

_SKILL_RE = re.compile(
    r"\b(python|typescript|javascript|react|vue|angular|node\.?js|docker|kubernetes|aws|gcp|azure|sql|postgresql|postgres|graphql|fastapi|django|flask|rust|golang|go|c\+\+|java|pytorch|tensorflow|scikit-learn|langchain|llm|rag|agentic|machine learning|ai|git|ci/cd|terraform|linux)\b",
    re.IGNORECASE,
)


def parse_requirements(
    arg1: str | list[str],
    arg2: list[str] | str | None = None,
) -> list[NormalizedRequirement]:
    """Parse a list of raw string requirements into structured NormalizedRequirement items.

    Supports both signatures:
    - parse_requirements(opportunity_id: str, raw_requirements: list[str])
    - parse_requirements(raw_requirements: list[str], opportunity_id: str = "default")

    Recognized patterns become structured criteria (opp_req_v1).
    Unrecognized items are categorized as custom/unparsed.
    Requirement IDs are deterministically derived from opportunity_id, ordinal, and canonical text.
    """
    if isinstance(arg1, str):
        opportunity_id = arg1
        raw_requirements = arg2 if isinstance(arg2, list) else []
    else:
        raw_requirements = arg1
        opportunity_id = str(arg2) if isinstance(arg2, str) else "default"

    normalized: list[NormalizedRequirement] = []

    for idx, raw in enumerate(raw_requirements):
        cleaned = raw.strip()
        if not cleaned:
            continue

        canonical_text = cleaned.lower()
        req_hash = hashlib.sha256(
            f"opp_req_v1:{opportunity_id}:{idx}:{canonical_text}".encode("utf-8")
        ).hexdigest()[:16]
        req_id = f"req_{req_hash}"

        # Check category (mandatory vs preferred)
        category = (
            RequirementCategory.PREFERRED
            if _PREFERRED_RE.search(cleaned)
            else RequirementCategory.MANDATORY
        )

        criterion = "custom"
        value: Any = cleaned
        status = "unparsed"

        # Check specific criterion extractors
        years_match = _YEARS_EXP_RE.search(cleaned)
        if years_match:
            criterion = "years_experience"
            value = int(years_match.group(1))
            status = "parsed"
        elif _WORK_AUTH_RE.search(cleaned):
            criterion = "work_authorization"
            value = cleaned
            status = "parsed"
        elif _DEGREE_RE.search(cleaned):
            criterion = "degree"
            deg_match = _DEGREE_RE.search(cleaned)
            value = deg_match.group(0).title() if deg_match else cleaned
            status = "parsed"
        elif _LANGUAGE_RE.search(cleaned):
            criterion = "language"
            lang_match = _LANGUAGE_RE.search(cleaned)
            value = lang_match.group(0).title() if lang_match else cleaned
            status = "parsed"
        elif _SENIORITY_RE.search(cleaned):
            criterion = "seniority"
            sen_match = _SENIORITY_RE.search(cleaned)
            value = sen_match.group(0).title() if sen_match else cleaned
            status = "parsed"
        elif _SKILL_RE.search(cleaned):
            criterion = "skill"
            # Extract all matched skills in the requirement
            matched_skills = [m.lower() for m in _SKILL_RE.findall(cleaned)]
            value = matched_skills[0] if len(matched_skills) == 1 else matched_skills
            status = "parsed"
        elif _LOCATION_RE.search(cleaned):
            criterion = "location"
            loc_match = _LOCATION_RE.search(cleaned)
            value = loc_match.group(0).title() if loc_match else cleaned
            status = "parsed"
        else:
            criterion = "custom"
            value = cleaned
            status = "unparsed"

        normalized.append(
            NormalizedRequirement(
                requirement_id=req_id,
                raw_text=cleaned,
                criterion=criterion,
                category=category,
                value=value,
                normalization_status=status,
            )
        )

    return normalized
