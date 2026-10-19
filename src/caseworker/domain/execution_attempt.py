"""Domain models for ExecutionLease and ExecutionAttempt in governed execution."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
import uuid

from caseworker.domain.enums import (
    ExecutionAttemptStatus,
    ExecutionErrorCategory,
    ExecutionLeaseStatus,
    ExecutionProviderKind,
)
from caseworker.domain.errors import DomainValidationError
from caseworker.domain.types import ensure_utc, from_iso_utc, now_utc, to_iso_utc

_VALID_ATTEMPT_TRANSITIONS: dict[ExecutionAttemptStatus, set[ExecutionAttemptStatus]] = {
    ExecutionAttemptStatus.INITIALIZING: {
        ExecutionAttemptStatus.STAGING,
        ExecutionAttemptStatus.FAILED,
    },
    ExecutionAttemptStatus.STAGING: {
        ExecutionAttemptStatus.AT_REVERSIBLE_POINT,
        ExecutionAttemptStatus.FAILED,
    },
    ExecutionAttemptStatus.AT_REVERSIBLE_POINT: {
        ExecutionAttemptStatus.SUBMITTING,
        ExecutionAttemptStatus.FAILED,
    },
    ExecutionAttemptStatus.SUBMITTING: {
        ExecutionAttemptStatus.SUCCEEDED,
        ExecutionAttemptStatus.FAILED,
        ExecutionAttemptStatus.AMBIGUOUS,
    },
    ExecutionAttemptStatus.SUCCEEDED: set(),
    ExecutionAttemptStatus.FAILED: set(),
    ExecutionAttemptStatus.AMBIGUOUS: set(),
}


@dataclass
class ExecutionLease:
    """Mutual exclusion lock preventing concurrent or duplicate external transmissions.

    Uses a monotonically increasing fencing_token (generation number) to guard against stale workers.
    """

    case_id: str
    plan_id: str
    action_id: str
    user_id: str
    attempt_id: str
    lease_token: str = field(default_factory=lambda: f"lease-{uuid.uuid4().hex[:16]}")
    fencing_token: int = 1
    lease_id: str = field(default_factory=lambda: f"ls-{uuid.uuid4().hex[:16]}")
    status: ExecutionLeaseStatus = ExecutionLeaseStatus.ACTIVE
    acquired_at: datetime = field(default_factory=now_utc)
    heartbeat_at: datetime = field(default_factory=now_utc)
    expires_at: datetime = field(default_factory=lambda: now_utc())
    released_at: datetime | None = None

    def __post_init__(self) -> None:
        if not self.case_id or not self.case_id.strip():
            raise DomainValidationError("ExecutionLease case_id cannot be empty.")
        if not self.plan_id or not self.plan_id.strip():
            raise DomainValidationError("ExecutionLease plan_id cannot be empty.")
        if not self.action_id or not self.action_id.strip():
            raise DomainValidationError("ExecutionLease action_id cannot be empty.")
        if not self.user_id or not self.user_id.strip():
            raise DomainValidationError("ExecutionLease user_id cannot be empty.")
        if not self.attempt_id or not self.attempt_id.strip():
            raise DomainValidationError("ExecutionLease attempt_id cannot be empty.")
        if self.fencing_token < 1:
            raise DomainValidationError("ExecutionLease fencing_token must be positive integer >= 1.")

        self.acquired_at = ensure_utc(self.acquired_at) or now_utc()
        self.heartbeat_at = ensure_utc(self.heartbeat_at) or now_utc()
        self.expires_at = ensure_utc(self.expires_at) or now_utc()
        if self.released_at:
            self.released_at = ensure_utc(self.released_at)

        if isinstance(self.status, str) and not isinstance(self.status, ExecutionLeaseStatus):
            self.status = ExecutionLeaseStatus(self.status)

    def is_expired(self, current_time: datetime | None = None) -> bool:
        check_time = ensure_utc(current_time) or now_utc()
        return self.status == ExecutionLeaseStatus.EXPIRED or check_time >= self.expires_at

    def release(self) -> None:
        self.status = ExecutionLeaseStatus.RELEASED
        self.released_at = now_utc()

    def renew(self, extension_seconds: int = 300) -> None:
        now = now_utc()
        self.heartbeat_at = now
        self.expires_at = datetime.fromtimestamp(now.timestamp() + extension_seconds, tz=now.tzinfo)

    def to_dict(self) -> dict[str, Any]:
        return {
            "lease_id": self.lease_id,
            "case_id": self.case_id,
            "plan_id": self.plan_id,
            "action_id": self.action_id,
            "user_id": self.user_id,
            "attempt_id": self.attempt_id,
            "lease_token": self.lease_token,
            "fencing_token": self.fencing_token,
            "status": self.status.value,
            "acquired_at": to_iso_utc(self.acquired_at),
            "heartbeat_at": to_iso_utc(self.heartbeat_at),
            "expires_at": to_iso_utc(self.expires_at),
            "released_at": to_iso_utc(self.released_at) if self.released_at else None,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ExecutionLease:
        return cls(
            lease_id=data["lease_id"],
            case_id=data["case_id"],
            plan_id=data["plan_id"],
            action_id=data["action_id"],
            user_id=data["user_id"],
            attempt_id=data["attempt_id"],
            lease_token=data["lease_token"],
            fencing_token=int(data.get("fencing_token", 1)),
            status=ExecutionLeaseStatus(data["status"]),
            acquired_at=from_iso_utc(data["acquired_at"]),
            heartbeat_at=from_iso_utc(data["heartbeat_at"]),
            expires_at=from_iso_utc(data["expires_at"]),
            released_at=from_iso_utc(data["released_at"]) if data.get("released_at") else None,
        )


@dataclass
class ExecutionAttempt:
    """Individual attempt to execute an authorized transmission.

    Tracks staging progress, the last reversible point, and whether the irreversible action was taken.
    """

    action_id: str
    approval_id: str
    plan_id: str
    case_id: str
    user_id: str
    lease_token: str
    fencing_token: int = 1
    attempt_id: str = field(default_factory=lambda: f"att-{uuid.uuid4().hex[:16]}")
    status: ExecutionAttemptStatus = ExecutionAttemptStatus.INITIALIZING
    provider_kind: ExecutionProviderKind = ExecutionProviderKind.SIMULATED_TEST
    started_at: datetime = field(default_factory=now_utc)
    completed_at: datetime | None = None
    last_reversible_point_reached_at: datetime | None = None
    irreversible_action_taken: bool = False
    staged_field_count: int = 0
    staged_upload_count: int = 0
    error_category: ExecutionErrorCategory | None = None
    error_message: str | None = None
    raw_response_snippet: str | None = None
    attempt_number: int = 1

    def __post_init__(self) -> None:
        if not self.attempt_id or not self.attempt_id.strip():
            raise DomainValidationError("ExecutionAttempt attempt_id cannot be empty.")
        if not self.action_id or not self.action_id.strip():
            raise DomainValidationError("ExecutionAttempt action_id cannot be empty.")
        if not self.approval_id or not self.approval_id.strip():
            raise DomainValidationError("ExecutionAttempt approval_id cannot be empty.")
        if not self.plan_id or not self.plan_id.strip():
            raise DomainValidationError("ExecutionAttempt plan_id cannot be empty.")
        if not self.case_id or not self.case_id.strip():
            raise DomainValidationError("ExecutionAttempt case_id cannot be empty.")
        if not self.user_id or not self.user_id.strip():
            raise DomainValidationError("ExecutionAttempt user_id cannot be empty.")
        if not self.lease_token or not self.lease_token.strip():
            raise DomainValidationError("ExecutionAttempt lease_token cannot be empty.")
        if self.fencing_token < 1:
            raise DomainValidationError("ExecutionAttempt fencing_token must be positive integer >= 1.")

        self.started_at = ensure_utc(self.started_at) or now_utc()
        if self.completed_at:
            self.completed_at = ensure_utc(self.completed_at)
        if self.last_reversible_point_reached_at:
            self.last_reversible_point_reached_at = ensure_utc(self.last_reversible_point_reached_at)

        if isinstance(self.status, str) and not isinstance(self.status, ExecutionAttemptStatus):
            self.status = ExecutionAttemptStatus(self.status)
        if isinstance(self.provider_kind, str) and not isinstance(self.provider_kind, ExecutionProviderKind):
            self.provider_kind = ExecutionProviderKind(self.provider_kind)
        if self.error_category and isinstance(self.error_category, str) and not isinstance(self.error_category, ExecutionErrorCategory):
            self.error_category = ExecutionErrorCategory(self.error_category)

    def transition_to(self, new_status: ExecutionAttemptStatus) -> None:
        if new_status == self.status:
            return
        valid = _VALID_ATTEMPT_TRANSITIONS.get(self.status, set())
        if new_status not in valid:
            raise DomainValidationError(
                f"Invalid ExecutionAttempt transition from '{self.status.value}' to '{new_status.value}'."
            )
        self.status = new_status
        if new_status in (ExecutionAttemptStatus.SUCCEEDED, ExecutionAttemptStatus.FAILED, ExecutionAttemptStatus.AMBIGUOUS):
            self.completed_at = now_utc()

    def record_last_reversible_point(self, field_count: int, upload_count: int) -> None:
        self.transition_to(ExecutionAttemptStatus.AT_REVERSIBLE_POINT)
        self.last_reversible_point_reached_at = now_utc()
        self.staged_field_count = field_count
        self.staged_upload_count = upload_count

    def mark_submitting(self) -> None:
        self.transition_to(ExecutionAttemptStatus.SUBMITTING)
        self.irreversible_action_taken = True

    def mark_succeeded(self) -> None:
        self.transition_to(ExecutionAttemptStatus.SUCCEEDED)

    def mark_failed(
        self,
        category: ExecutionErrorCategory,
        message: str,
        snippet: str | None = None,
    ) -> None:
        self.error_category = category
        self.error_message = message
        self.raw_response_snippet = snippet
        self.transition_to(ExecutionAttemptStatus.FAILED)

    def mark_ambiguous(
        self,
        message: str,
        snippet: str | None = None,
    ) -> None:
        self.error_category = ExecutionErrorCategory.SUBMIT_TIMEOUT
        self.error_message = message
        self.raw_response_snippet = snippet
        self.transition_to(ExecutionAttemptStatus.AMBIGUOUS)

    def to_dict(self) -> dict[str, Any]:
        return {
            "attempt_id": self.attempt_id,
            "action_id": self.action_id,
            "approval_id": self.approval_id,
            "plan_id": self.plan_id,
            "case_id": self.case_id,
            "user_id": self.user_id,
            "lease_token": self.lease_token,
            "fencing_token": self.fencing_token,
            "status": self.status.value,
            "provider_kind": self.provider_kind.value,
            "started_at": to_iso_utc(self.started_at),
            "completed_at": to_iso_utc(self.completed_at) if self.completed_at else None,
            "last_reversible_point_reached_at": to_iso_utc(self.last_reversible_point_reached_at) if self.last_reversible_point_reached_at else None,
            "irreversible_action_taken": self.irreversible_action_taken,
            "staged_field_count": self.staged_field_count,
            "staged_upload_count": self.staged_upload_count,
            "error_category": self.error_category.value if self.error_category else None,
            "error_message": self.error_message,
            "raw_response_snippet": self.raw_response_snippet,
            "attempt_number": self.attempt_number,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ExecutionAttempt:
        return cls(
            attempt_id=data["attempt_id"],
            action_id=data["action_id"],
            approval_id=data["approval_id"],
            plan_id=data["plan_id"],
            case_id=data["case_id"],
            user_id=data["user_id"],
            lease_token=data["lease_token"],
            fencing_token=int(data.get("fencing_token", 1)),
            status=ExecutionAttemptStatus(data["status"]),
            provider_kind=ExecutionProviderKind(data.get("provider_kind", ExecutionProviderKind.SIMULATED_TEST.value)),
            started_at=from_iso_utc(data["started_at"]),
            completed_at=from_iso_utc(data["completed_at"]) if data.get("completed_at") else None,
            last_reversible_point_reached_at=from_iso_utc(data["last_reversible_point_reached_at"]) if data.get("last_reversible_point_reached_at") else None,
            irreversible_action_taken=bool(data.get("irreversible_action_taken", False)),
            staged_field_count=int(data.get("staged_field_count", 0)),
            staged_upload_count=int(data.get("staged_upload_count", 0)),
            error_category=ExecutionErrorCategory(data["error_category"]) if data.get("error_category") else None,
            error_message=data.get("error_message"),
            raw_response_snippet=data.get("raw_response_snippet"),
            attempt_number=int(data.get("attempt_number", 1)),
        )
