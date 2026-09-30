"""Standard personal context namespaces and hierarchical filtering utilities."""

from __future__ import annotations

import re

# Standard Root Context Namespaces
NAMESPACE_IDENTITY = "identity"
NAMESPACE_EDUCATION = "education"
NAMESPACE_CAREER = "career"
NAMESPACE_SKILLS = "skills"
NAMESPACE_PROJECTS = "projects"
NAMESPACE_ACHIEVEMENTS = "achievements"
NAMESPACE_PREFERENCES = "preferences"
NAMESPACE_CONSTRAINTS = "constraints"
NAMESPACE_DOCUMENTS = "documents"
NAMESPACE_CONTACT = "contact"
NAMESPACE_LOCATION = "location"

STANDARD_ROOT_NAMESPACES: frozenset[str] = frozenset({
    NAMESPACE_IDENTITY,
    NAMESPACE_EDUCATION,
    NAMESPACE_CAREER,
    NAMESPACE_SKILLS,
    NAMESPACE_PROJECTS,
    NAMESPACE_ACHIEVEMENTS,
    NAMESPACE_PREFERENCES,
    NAMESPACE_CONSTRAINTS,
    NAMESPACE_DOCUMENTS,
    NAMESPACE_CONTACT,
    NAMESPACE_LOCATION,
})

_NAMESPACE_SEGMENT_REGEX = re.compile(r"^[a-z0-9_]+$")


def is_valid_namespace(namespace: str) -> bool:
    """Validate whether a namespace string follows hierarchical lowercase dot notation.

    Examples:
        'career' -> True
        'career.roles' -> True
        'preferences.job.location' -> True
        'Invalid Namespace' -> False
    """
    if not namespace or not isinstance(namespace, str):
        return False
    parts = namespace.strip().split(".")
    if not parts or any(not p for p in parts):
        return False
    return all(bool(_NAMESPACE_SEGMENT_REGEX.match(p)) for p in parts)


def normalize_namespace(namespace: str) -> str:
    """Normalize namespace string by lowercasing and trimming whitespace."""
    return namespace.strip().lower()


def get_namespace_root(namespace: str) -> str:
    """Extract the top-level root prefix of a hierarchical namespace."""
    norm = normalize_namespace(namespace)
    return norm.split(".")[0]


def matches_namespace_filter(fact_namespace: str, filter_prefix: str) -> bool:
    """Evaluate whether a fact's namespace satisfies a given query filter.

    Supports:
    - Exact match: 'career.roles' matches 'career.roles'
    - Root match: 'career' matches 'career.roles' and 'career.years_experience'
    - Wildcard match: 'career.*' matches 'career.roles' and 'career'
    """
    norm_fact = normalize_namespace(fact_namespace)
    norm_filter = normalize_namespace(filter_prefix)

    # Strip trailing wildcard if present
    if norm_filter.endswith(".*"):
        prefix = norm_filter[:-2]
    else:
        prefix = norm_filter

    if norm_fact == prefix:
        return True

    return norm_fact.startswith(prefix + ".")
