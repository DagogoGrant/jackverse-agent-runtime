"""Case domain aggregate root with state machine and optimistic concurrency."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
import uuid

from caseworker.domain.enums import CaseStatus, CaseType
from caseworker.domain.errors import DomainValidationError, InvalidStateTransitionError
from caseworker.domain.types import ensure_utc, from_iso_utc, now_utc, to_iso_utc


_VALID_CASE_TRANSITIONS: dict[CaseStatus, set[CaseStatus]] = {
    CaseStatus.NEW: {CaseStatus.INTAKE, CaseStatus.CANCELLED},
    CaseStatus.INTAKE: {CaseStatus.INVESTIGATING, CaseStatus.BLOCKED, CaseStatus.CANCELLED},
    CaseStatus.INVESTIGATING: {
        CaseStatus.PLANNING,
        CaseStatus.ACTION_REQUIRED,
        CaseStatus.BLOCKED,
        CaseStatus.CANCELLED,
        CaseStatus.RESOLVED,
    },
    CaseStatus.PLANNING: {
        CaseStatus.ACTION_REQUIRED,
        CaseStatus.INVESTIGATING,
        CaseStatus.BLOCKED,
        CaseStatus.CANCELLED,
    },
    CaseStatus.ACTION_REQUIRED: {
        CaseStatus.AWAITING_APPROVAL,
        CaseStatus.ACTION_IN_PROGRESS,
        CaseStatus.BLOCKED,
        CaseStatus.CANCELLED,
    },
    CaseStatus.AWAITING_APPROVAL: {
        CaseStatus.ACTION_IN_PROGRESS,
        CaseStatus.PLANNING,
        CaseStatus.BLOCKED,
        CaseStatus.CANCELLED,
    },
    CaseStatus.ACTION_IN_PROGRESS: {
        CaseStatus.WAITING_EXTERNAL,
        CaseStatus.INVESTIGATING,
        CaseStatus.PLANNING,
        CaseStatus.RESOLVED,
        CaseStatus.BLOCKED,
        CaseStatus.ESCALATED,
        CaseStatus.CANCELLED,
    },
    CaseStatus.WAITING_EXTERNAL: {
        CaseStatus.FOLLOW_UP_DUE,
        CaseStatus.INVESTIGATING,
        CaseStatus.RESOLVED,
        CaseStatus.BLOCKED,
        CaseStatus.CANCELLED,
    },
    CaseStatus.FOLLOW_UP_DUE: {
        CaseStatus.INVESTIGATING,
        CaseStatus.ACTION_REQUIRED,
        CaseStatus.WAITING_EXTERNAL,
        CaseStatus.BLOCKED,
        CaseStatus.CANCELLED,
    },
    CaseStatus.BLOCKED: {
        CaseStatus.INTAKE,
        CaseStatus.INVESTIGATING,
        CaseStatus.PLANNING,
        CaseStatus.ACTION_REQUIRED,
        CaseStatus.ESCALATED,
        CaseStatus.CANCELLED,
    },
    CaseStatus.ESCALATED: {
        CaseStatus.INVESTIGATING,
        CaseStatus.PLANNING,
        CaseStatus.RESOLVED,
        CaseStatus.CANCELLED,
    },
    CaseStatus.RESOLVED: set(),
    CaseStatus.CANCELLED: set(),
}


@dataclass
class Case:
    """One concrete, trackable unit of work (e.g. application, dispute, search)."""

    user_id: str
    title: str
    goal: str
    case_type: CaseType | str
    case_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    mission_id: str | None = None
    status: CaseStatus = CaseStatus.NEW
    success_criteria: list[str] = field(default_factory=list)
    constraints: list[str] = field(default_factory=list)
    created_at: datetime = field(default_factory=now_utc)
    updated_at: datetime = field(default_factory=now_utc)
    deadline: datetime | None = None
    resolved_at: datetime | None = None
    outcome: str | None = None
    version: int = 1

    def __post_init__(self) -> None:
        if not self.case_id or not self.case_id.strip():
            raise DomainValidationError("Case case_id cannot be empty.")
        if not self.user_id or not self.user_id.strip():
            raise DomainValidationError("Case user_id cannot be empty.")
        if not self.title or not self.title.strip():
            raise DomainValidationError("Case title cannot be empty.")
        if not self.goal or not self.goal.strip():
            raise DomainValidationError("Case goal cannot be empty.")
        if self.version < 1:
            raise DomainValidationError(f"Case version must be >= 1, got {self.version}")

        # Enforce UTC timezone awareness
        self.created_at = ensure_utc(self.created_at) or now_utc()
        self.updated_at = ensure_utc(self.updated_at) or now_utc()
        if self.deadline is not None:
            self.deadline = ensure_utc(self.deadline)
        if self.resolved_at is not None:
            self.resolved_at = ensure_utc(self.resolved_at)

        # Normalize enum types
        if isinstance(self.case_type, str):
            try:
                self.case_type = CaseType(self.case_type)
            except ValueError:
                # Custom extensible case type preserved as string
                pass
        if isinstance(self.status, str) and not isinstance(self.status, CaseStatus):
            self.status = CaseStatus(self.status)

    @property
    def case_type_str(self) -> str:
        return self.case_type.value if isinstance(self.case_type, CaseType) else str(self.case_type)

    def transition_to(self, new_status: CaseStatus, reason: str | None = None) -> None:
        """Execute a state machine transition with invariant enforcement and version bumping."""
        if isinstance(new_status, str) and not isinstance(new_status, CaseStatus):
            new_status = CaseStatus(new_status)

        if new_status == self.status:
            return

        if self.status.is_terminal:
            raise InvalidStateTransitionError(
                "Case",
                self.status.value,
                new_status.value,
                "Cannot transition from terminal state.",
            )

        allowed = _VALID_CASE_TRANSITIONS.get(self.status, set())
        if new_status not in allowed:
            raise InvalidStateTransitionError(
                "Case",
                self.status.value,
                new_status.value,
                f"Valid transitions from '{self.status.value}' are: {[s.value for s in allowed]}",
            )

        self.status = new_status
        self.version += 1
        self.updated_at = now_utc()
        if new_status == CaseStatus.RESOLVED and self.resolved_at is None:
            self.resolved_at = self.updated_at

    def resolve(self, outcome: str) -> None:
        """Convenience method to transition case to RESOLVED with recorded outcome."""
        if not outcome or not outcome.strip():
            raise DomainValidationError("Resolution outcome description cannot be empty.")
        self.transition_to(CaseStatus.RESOLVED, reason=f"Resolved: {outcome}")
        self.outcome = outcome.strip()
        self.resolved_at = self.updated_at

    def to_dict(self) -> dict[str, Any]:
        """Serialize Case to a JSON-compatible dictionary."""
        return {
            "case_id": self.case_id,
            "mission_id": self.mission_id,
            "user_id": self.user_id,
            "case_type": self.case_type_str,
            "title": self.title,
            "goal": self.goal,
            "status": self.status.value,
            "success_criteria": list(self.success_criteria),
            "constraints": list(self.constraints),
            "created_at": to_iso_utc(self.created_at),
            "updated_at": to_iso_utc(self.updated_at),
            "deadline": to_iso_utc(self.deadline),
            "resolved_at": to_iso_utc(self.resolved_at),
            "outcome": self.outcome,
            "version": self.version,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Case:
        """Reconstruct Case from serialized dictionary."""
        return cls(
            case_id=data["case_id"],
            mission_id=data.get("mission_id"),
            user_id=data["user_id"],
            case_type=data["case_type"],
            title=data["title"],
            goal=data["goal"],
            status=CaseStatus(data["status"]),
            success_criteria=list(data.get("success_criteria") or []),
            constraints=list(data.get("constraints") or []),
            created_at=from_iso_utc(data["created_at"]) or now_utc(),
            updated_at=from_iso_utc(data["updated_at"]) or now_utc(),
            deadline=from_iso_utc(data.get("deadline")),
            resolved_at=from_iso_utc(data.get("resolved_at")),
            outcome=data.get("outcome"),
            version=int(data.get("version", 1)),
        )
