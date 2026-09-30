"""Mission domain aggregate root with state machine and optimistic concurrency."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
import uuid

from caseworker.domain.enums import MissionKind, MissionStatus
from caseworker.domain.errors import DomainValidationError, InvalidStateTransitionError
from caseworker.domain.types import ensure_utc, from_iso_utc, now_utc, to_iso_utc


_VALID_MISSION_TRANSITIONS: dict[MissionStatus, set[MissionStatus]] = {
    MissionStatus.DRAFT: {MissionStatus.ACTIVE, MissionStatus.CANCELLED},
    MissionStatus.ACTIVE: {
        MissionStatus.PAUSED,
        MissionStatus.COMPLETED,
        MissionStatus.FAILED,
        MissionStatus.CANCELLED,
    },
    MissionStatus.PAUSED: {MissionStatus.ACTIVE, MissionStatus.CANCELLED},
    MissionStatus.COMPLETED: set(),
    MissionStatus.CANCELLED: set(),
    MissionStatus.FAILED: set(),
}


@dataclass
class Mission:
    """A long-running user objective that can span multiple Cases."""

    user_id: str
    title: str
    goal: str
    kind: MissionKind
    mission_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    status: MissionStatus = MissionStatus.DRAFT
    success_criteria: list[str] = field(default_factory=list)
    constraints: list[str] = field(default_factory=list)
    created_at: datetime = field(default_factory=now_utc)
    updated_at: datetime = field(default_factory=now_utc)
    deadline: datetime | None = None
    version: int = 1

    def __post_init__(self) -> None:
        if not self.mission_id or not self.mission_id.strip():
            raise DomainValidationError("Mission mission_id cannot be empty.")
        if not self.user_id or not self.user_id.strip():
            raise DomainValidationError("Mission user_id cannot be empty.")
        if not self.title or not self.title.strip():
            raise DomainValidationError("Mission title cannot be empty.")
        if not self.goal or not self.goal.strip():
            raise DomainValidationError("Mission goal cannot be empty.")
        if self.version < 1:
            raise DomainValidationError(f"Mission version must be >= 1, got {self.version}")

        # Enforce UTC timezone awareness
        self.created_at = ensure_utc(self.created_at) or now_utc()
        self.updated_at = ensure_utc(self.updated_at) or now_utc()
        if self.deadline is not None:
            self.deadline = ensure_utc(self.deadline)

        # Normalize enum types
        if isinstance(self.kind, str) and not isinstance(self.kind, MissionKind):
            self.kind = MissionKind(self.kind)
        if isinstance(self.status, str) and not isinstance(self.status, MissionStatus):
            self.status = MissionStatus(self.status)

    def transition_to(self, new_status: MissionStatus, reason: str | None = None) -> None:
        """Execute a state machine transition with invariant enforcement and version bumping."""
        if isinstance(new_status, str) and not isinstance(new_status, MissionStatus):
            new_status = MissionStatus(new_status)

        if new_status == self.status:
            return

        if self.status.is_terminal:
            raise InvalidStateTransitionError(
                "Mission",
                self.status.value,
                new_status.value,
                "Cannot transition from terminal state.",
            )

        allowed = _VALID_MISSION_TRANSITIONS.get(self.status, set())
        if new_status not in allowed:
            raise InvalidStateTransitionError(
                "Mission",
                self.status.value,
                new_status.value,
                f"Valid transitions from '{self.status.value}' are: {[s.value for s in allowed]}",
            )

        self.status = new_status
        self.version += 1
        self.updated_at = now_utc()

    def to_dict(self) -> dict[str, Any]:
        """Serialize Mission to a JSON-compatible dictionary."""
        return {
            "mission_id": self.mission_id,
            "user_id": self.user_id,
            "title": self.title,
            "goal": self.goal,
            "kind": self.kind.value,
            "status": self.status.value,
            "success_criteria": list(self.success_criteria),
            "constraints": list(self.constraints),
            "created_at": to_iso_utc(self.created_at),
            "updated_at": to_iso_utc(self.updated_at),
            "deadline": to_iso_utc(self.deadline),
            "version": self.version,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Mission:
        """Reconstruct Mission from serialized dictionary."""
        return cls(
            mission_id=data["mission_id"],
            user_id=data["user_id"],
            title=data["title"],
            goal=data["goal"],
            kind=MissionKind(data["kind"]),
            status=MissionStatus(data["status"]),
            success_criteria=list(data.get("success_criteria") or []),
            constraints=list(data.get("constraints") or []),
            created_at=from_iso_utc(data["created_at"]) or now_utc(),
            updated_at=from_iso_utc(data["updated_at"]) or now_utc(),
            deadline=from_iso_utc(data.get("deadline")),
            version=int(data.get("version", 1)),
        )
