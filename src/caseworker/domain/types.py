"""Common domain types, timezone-aware UTC helpers, and JSON serialization."""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from typing import Any, Mapping, Sequence, TypeAlias

# Reusable strictly typed JSON aliases
JSONScalar: TypeAlias = str | int | float | bool | None
JSONValue: TypeAlias = JSONScalar | Sequence["JSONValue"] | Mapping[str, "JSONValue"]
JSONObject: TypeAlias = dict[str, Any]


def now_utc() -> datetime:
    """Return the current time in timezone-aware UTC."""
    return datetime.now(timezone.utc)


def ensure_utc(dt: datetime | None) -> datetime | None:
    """Validate that a datetime is timezone-aware UTC, or convert aware datetimes to UTC.

    Raises:
        ValueError: If a naive datetime (without timezone info) is passed.
    """
    if dt is None:
        return None
    if dt.tzinfo is None or dt.tzinfo.utcoffset(dt) is None:
        raise ValueError(f"Naive datetime '{dt}' is prohibited. All timestamps must be timezone-aware UTC.")
    return dt.astimezone(timezone.utc)


def to_iso_utc(dt: datetime | None) -> str | None:
    """Serialize a timezone-aware datetime to ISO-8601 UTC string."""
    if dt is None:
        return None
    return ensure_utc(dt).isoformat()


def from_iso_utc(iso_str: str | None) -> datetime | None:
    """Parse an ISO-8601 string into a timezone-aware UTC datetime."""
    if iso_str is None or not iso_str.strip():
        return None
    dt = datetime.fromisoformat(iso_str.strip())
    if dt.tzinfo is None:
        # If string lacked timezone offset, reject
        raise ValueError(f"ISO timestamp string '{iso_str}' lacked timezone information.")
    return dt.astimezone(timezone.utc)


def canonical_json_dumps(data: Any) -> str:
    """Produce deterministic, whitespace-compact, sorted JSON string for hashing and storage."""
    return json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def compute_sha256(canonical_text: str) -> str:
    """Compute SHA-256 hex digest for a canonical string."""
    return hashlib.sha256(canonical_text.encode("utf-8")).hexdigest()
