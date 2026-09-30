"""Approval domain entity representing durable authorization for consequential actions."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
import uuid

from caseworker.domain.action import Action
from caseworker.domain.enums import ApprovalStatus
from caseworker.domain.errors import ApprovalValidationError, DomainValidationError, InvalidStateTransitionError
from caseworker.domain.types import ensure_utc, from_iso_utc, now_utc, to_iso_utc

_VALID_APPROVAL_TRANSITIONS: dict[ApprovalStatus, set[ApprovalStatus]] = {
    ApprovalStatus.PENDING: {
        ApprovalStatus.APPROVED,
        ApprovalStatus.REJECTED,
        ApprovalStatus.EXPIRED,
        ApprovalStatus.CANCELLED,
    },
    ApprovalStatus.APPROVED: set(),
    ApprovalStatus.REJECTED: set(),
    ApprovalStatus.EXPIRED: set(),
    ApprovalStatus.CANCELLED: set(),
}


@dataclass
class Approval:
    """A durable approval request and decision bound to a specific Action fingerprint.

    Security Guarantee:
        Approvals are cryptographically bound to the SHA-256 fingerprint of the Action
        at the moment approval was requested. If the action's type, parameters, or
        description change later, the action's new fingerprint will fail `is_valid_for(action)`
        and `approve(action)`.
    """

    action_id: str
    case_id: str
    user_id: str
    action_fingerprint: str
    approval_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    status: ApprovalStatus = ApprovalStatus.PENDING
    requested_at: datetime = field(default_factory=now_utc)
    decided_at: datetime | None = None
    expires_at: datetime | None = None
    reason: str | None = None
    version: int = 1

    def __post_init__(self) -> None:
        if not self.approval_id or not self.approval_id.strip():
            raise DomainValidationError("Approval approval_id cannot be empty.")
        if not self.action_id or not self.action_id.strip():
            raise DomainValidationError("Approval action_id cannot be empty.")
        if not self.case_id or not self.case_id.strip():
            raise DomainValidationError("Approval case_id cannot be empty.")
        if not self.user_id or not self.user_id.strip():
            raise DomainValidationError("Approval user_id cannot be empty.")
        if not self.action_fingerprint or not self.action_fingerprint.strip():
            raise DomainValidationError("Approval action_fingerprint cannot be empty.")
        if self.version < 1:
            raise DomainValidationError(f"Approval version must be >= 1, got {self.version}")

        # Enforce UTC timezone awareness
        self.requested_at = ensure_utc(self.requested_at) or now_utc()
        if self.decided_at is not None:
            self.decided_at = ensure_utc(self.decided_at)
        if self.expires_at is not None:
            self.expires_at = ensure_utc(self.expires_at)

        # Normalize enum types
        if isinstance(self.status, str) and not isinstance(self.status, ApprovalStatus):
            self.status = ApprovalStatus(self.status)

    @property
    def is_expired(self) -> bool:
        """Check whether the approval request has expired."""
        if self.status == ApprovalStatus.EXPIRED:
            return True
        if self.expires_at is not None and now_utc() > self.expires_at:
            return True
        return False

    def is_valid_for(self, action: Action) -> bool:
        """Verify whether this approval is valid and authorizes the given action.

        Returns True only if:
        1. Status is APPROVED
        2. Not expired
        3. Action ID and Case ID match
        4. Action's current computed fingerprint matches this approval's action_fingerprint
        """
        if self.status != ApprovalStatus.APPROVED:
            return False
        if self.is_expired:
            return False
        if self.action_id != action.action_id or self.case_id != action.case_id:
            return False
        return self.action_fingerprint == action.compute_fingerprint()

    def approve(self, action: Action, reason: str | None = None) -> None:
        """Grant approval for the action, enforcing fingerprint integrity."""
        if self.status != ApprovalStatus.PENDING:
            raise InvalidStateTransitionError(
                "Approval",
                self.status.value,
                ApprovalStatus.APPROVED.value,
                "Approval is no longer pending.",
            )

        if self.is_expired:
            self.expire()
            raise ApprovalValidationError("Cannot approve an expired approval request.")

        if action.action_id != self.action_id or action.case_id != self.case_id:
            raise ApprovalValidationError(
                f"Action ID mismatch (approval action '{self.action_id}' vs action '{action.action_id}')."
            )

        current_fp = action.compute_fingerprint()
        if current_fp != self.action_fingerprint:
            raise ApprovalValidationError(
                f"Action fingerprint mismatch. Action parameters or description have changed since "
                f"approval was requested (expected {self.action_fingerprint[:12]}..., got {current_fp[:12]}...)."
            )

        self.status = ApprovalStatus.APPROVED
        self.decided_at = now_utc()
        self.reason = reason
        self.version += 1

    def reject(self, reason: str | None = None) -> None:
        """Reject the approval request."""
        if self.status != ApprovalStatus.PENDING:
            raise InvalidStateTransitionError(
                "Approval",
                self.status.value,
                ApprovalStatus.REJECTED.value,
                "Approval is no longer pending.",
            )
        self.status = ApprovalStatus.REJECTED
        self.decided_at = now_utc()
        self.reason = reason
        self.version += 1

    def expire(self) -> None:
        """Mark the approval request as expired."""
        if self.status == ApprovalStatus.EXPIRED:
            return
        if self.status != ApprovalStatus.PENDING:
            raise InvalidStateTransitionError(
                "Approval",
                self.status.value,
                ApprovalStatus.EXPIRED.value,
                "Only pending approvals can expire.",
            )
        self.status = ApprovalStatus.EXPIRED
        self.decided_at = now_utc()
        self.version += 1

    def cancel(self, reason: str | None = None) -> None:
        """Cancel the approval request."""
        if self.status != ApprovalStatus.PENDING:
            raise InvalidStateTransitionError(
                "Approval",
                self.status.value,
                ApprovalStatus.CANCELLED.value,
                "Only pending approvals can be cancelled.",
            )
        self.status = ApprovalStatus.CANCELLED
        self.decided_at = now_utc()
        self.reason = reason
        self.version += 1

    def to_dict(self) -> dict[str, Any]:
        """Serialize Approval to a JSON-compatible dictionary."""
        return {
            "approval_id": self.approval_id,
            "action_id": self.action_id,
            "case_id": self.case_id,
            "user_id": self.user_id,
            "action_fingerprint": self.action_fingerprint,
            "status": self.status.value,
            "requested_at": to_iso_utc(self.requested_at),
            "decided_at": to_iso_utc(self.decided_at),
            "expires_at": to_iso_utc(self.expires_at),
            "reason": self.reason,
            "version": self.version,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Approval:
        """Reconstruct Approval from serialized dictionary."""
        return cls(
            approval_id=data["approval_id"],
            action_id=data["action_id"],
            case_id=data["case_id"],
            user_id=data["user_id"],
            action_fingerprint=data["action_fingerprint"],
            status=ApprovalStatus(data["status"]),
            requested_at=from_iso_utc(data["requested_at"]) or now_utc(),
            decided_at=from_iso_utc(data.get("decided_at")),
            expires_at=from_iso_utc(data.get("expires_at")),
            reason=data.get("reason"),
            version=int(data.get("version", 1)),
        )
