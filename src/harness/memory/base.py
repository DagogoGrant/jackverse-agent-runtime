"""Domain models and storage protocol for Persistent Long-Term Memory (Phase 3A baseline and Phase 3B firewall)."""

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Protocol, runtime_checkable


class MemorySource(str, Enum):
    """Source origin of a memory entry."""

    USER_INPUT = "USER_INPUT"
    TOOL_OBSERVATION = "TOOL_OBSERVATION"


class AdmissionAction(str, Enum):
    """Action decision for a memory candidate."""

    ACCEPT = "accept"
    QUARANTINE = "quarantine"
    REJECT = "reject"


class MemoryStatus(str, Enum):
    """Persisted storage status of a memory entry."""

    ACCEPTED = "accepted"
    QUARANTINED = "quarantined"
    SUPERSEDED = "superseded"


class MemoryType(str, Enum):
    """Classification of memory: declarative knowledge vs. procedural recovery lesson."""

    DECLARATIVE = "declarative"
    PROCEDURAL = "procedural"


def normalize_content(text: str) -> str:
    """Canonicalize content for deterministic duplicate detection.

    1. Unicode NFKC normalization
    2. Lowercase
    3. Collapse consecutive whitespace
    4. Strip harmless terminal punctuation
    5. Strip leading/trailing whitespace
    """
    if not text:
        return ""
    import re
    import unicodedata

    normalized = unicodedata.normalize("NFKC", text).lower()
    normalized = re.sub(r"\s+", " ", normalized)
    normalized = normalized.strip()
    normalized = re.sub(r"[.,!?;:]+$", "", normalized).strip()
    return normalized


@dataclass(frozen=True)
class MemoryEntry:
    """A persisted memory item with identity, content, lifecycle, and provenance fields."""

    # Identity
    id: str
    created_at: str

    # Content
    content: str
    source: MemorySource
    normalized_content: str = ""

    # Lifecycle state
    status: MemoryStatus = MemoryStatus.ACCEPTED
    memory_type: MemoryType = MemoryType.DECLARATIVE
    memory_key: str | None = None
    memory_value: str | None = None
    expires_at: str | None = None
    supersedes_id: str | None = None
    superseded_by: str | None = None
    superseded_at: str | None = None

    # Provenance metadata
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class AdmissionDecision:
    """Outcome of an admission policy or firewall evaluation."""

    action: AdmissionAction = AdmissionAction.ACCEPT
    reason: str = ""
    admitted: bool = True
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        # Keep legacy boolean admitted and modern action in sync
        if not self.admitted and self.action == AdmissionAction.ACCEPT:
            object.__setattr__(self, "action", AdmissionAction.REJECT)
        elif self.action == AdmissionAction.REJECT and self.admitted:
            object.__setattr__(self, "admitted", False)


@runtime_checkable
class MemoryStore(Protocol):
    """Protocol for persistent long-term memory storage."""

    def add(self, entry: MemoryEntry) -> None:
        """Persist a memory entry."""
        ...

    def get(self, entry_id: str) -> MemoryEntry | None:
        """Retrieve a memory entry by ID."""
        ...

    def list_all(
        self,
        limit: int | None = 100,
        status: MemoryStatus | None = None,
    ) -> list[MemoryEntry]:
        """List stored memories ordered by created_at descending."""
        ...

    def find_exact_content(self, content: str) -> MemoryEntry | None:
        """Find an existing entry with identical content."""
        ...

    def find_normalized_content(self, normalized_content: str) -> MemoryEntry | None:
        """Find an existing entry with identical normalized content."""
        ...

    def find_active_by_key(self, memory_key: str) -> MemoryEntry | None:
        """Find the currently accepted active entry for a structured memory key."""
        ...

    def supersede_and_add(self, new_entry: MemoryEntry, memory_key: str) -> str | None:
        """Atomically transition any active entry for memory_key to SUPERSEDED and insert new_entry."""
        ...

    def count(self, status: MemoryStatus | None = None, memory_key: str | None = None) -> int:
        """Return total number of stored memory entries matching criteria."""
        ...

    def close(self) -> None:
        """Close the underlying storage connection."""
        ...
