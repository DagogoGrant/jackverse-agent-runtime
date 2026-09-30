"""Action domain entity representing proposed or executed real-world work."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
import uuid

from caseworker.domain.enums import ActionStatus, ActionType, RiskLevel
from caseworker.domain.errors import DomainValidationError, InvalidStateTransitionError
from caseworker.domain.types import (
    canonical_json_dumps,
    compute_sha256,
    ensure_utc,
    from_iso_utc,
    now_utc,
    to_iso_utc,
)

ACTION_FINGERPRINT_SCHEMA_V1 = "act_fp_v1"

_VALID_ACTION_TRANSITIONS: dict[ActionStatus, set[ActionStatus]] = {
    ActionStatus.PROPOSED: {
        ActionStatus.AWAITING_APPROVAL,
        ActionStatus.APPROVED,
        ActionStatus.REJECTED,
        ActionStatus.CANCELLED,
    },
    ActionStatus.AWAITING_APPROVAL: {
        ActionStatus.APPROVED,
        ActionStatus.REJECTED,
        ActionStatus.CANCELLED,
    },
    ActionStatus.APPROVED: {
        ActionStatus.EXECUTING,
        ActionStatus.CANCELLED,
    },
    ActionStatus.EXECUTING: {
        ActionStatus.SUCCEEDED,
        ActionStatus.FAILED,
    },
    ActionStatus.REJECTED: set(),
    ActionStatus.SUCCEEDED: set(),
    ActionStatus.FAILED: set(),
    ActionStatus.CANCELLED: set(),
}


@dataclass
class Action:
    """An operation proposed or executed in service of a Case."""

    case_id: str
    action_type: ActionType | str
    description: str
    action_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    status: ActionStatus = ActionStatus.PROPOSED
    risk_level: RiskLevel = RiskLevel.LOW
    requires_approval: bool = False
    created_at: datetime = field(default_factory=now_utc)
    executed_at: datetime | None = None
    parameters: dict[str, Any] = field(default_factory=dict)
    result: dict[str, Any] = field(default_factory=dict)
    idempotency_key: str | None = None
    version: int = 1

    def __post_init__(self) -> None:
        if not self.action_id or not self.action_id.strip():
            raise DomainValidationError("Action action_id cannot be empty.")
        if not self.case_id or not self.case_id.strip():
            raise DomainValidationError("Action case_id cannot be empty.")
        if not self.description or not self.description.strip():
            raise DomainValidationError("Action description cannot be empty.")
        if self.version < 1:
            raise DomainValidationError(f"Action version must be >= 1, got {self.version}")

        # Enforce UTC timezone awareness
        self.created_at = ensure_utc(self.created_at) or now_utc()
        if self.executed_at is not None:
            self.executed_at = ensure_utc(self.executed_at)

        # Normalize enum types
        if isinstance(self.action_type, str):
            try:
                self.action_type = ActionType(self.action_type)
            except ValueError:
                pass
        if isinstance(self.status, str) and not isinstance(self.status, ActionStatus):
            self.status = ActionStatus(self.status)
        if isinstance(self.risk_level, str) and not isinstance(self.risk_level, RiskLevel):
            self.risk_level = RiskLevel(self.risk_level)

        # High/Critical risk actions inherently require approval
        if self.risk_level in (RiskLevel.HIGH, RiskLevel.CRITICAL):
            self.requires_approval = True

    @property
    def action_type_str(self) -> str:
        return self.action_type.value if isinstance(self.action_type, ActionType) else str(self.action_type)

    def compute_fingerprint(self) -> str:
        """Compute parameter-bound, tamper-evident SHA-256 fingerprint for approval binding.

        Binds:
        - Schema version (act_fp_v1)
        - action_id
        - case_id
        - action_type
        - human-visible description snapshot
        - canonical parameters
        """
        payload = [
            ACTION_FINGERPRINT_SCHEMA_V1,
            self.action_id,
            self.case_id,
            self.action_type_str,
            self.description.strip(),
            self.parameters,
        ]
        canonical = canonical_json_dumps(payload)
        return compute_sha256(canonical)

    def transition_to(self, new_status: ActionStatus, reason: str | None = None) -> None:
        """Execute action status transition with invariant enforcement."""
        if isinstance(new_status, str) and not isinstance(new_status, ActionStatus):
            new_status = ActionStatus(new_status)

        if new_status == self.status:
            return

        if self.status.is_terminal:
            raise InvalidStateTransitionError(
                "Action",
                self.status.value,
                new_status.value,
                "Cannot transition from terminal state.",
            )

        allowed = _VALID_ACTION_TRANSITIONS.get(self.status, set())
        if new_status not in allowed:
            raise InvalidStateTransitionError(
                "Action",
                self.status.value,
                new_status.value,
                f"Valid transitions from '{self.status.value}' are: {[s.value for s in allowed]}",
            )

        self.status = new_status
        self.version += 1
        if new_status in (ActionStatus.SUCCEEDED, ActionStatus.FAILED) and self.executed_at is None:
            self.executed_at = now_utc()

    def to_dict(self) -> dict[str, Any]:
        """Serialize Action to a JSON-compatible dictionary."""
        return {
            "action_id": self.action_id,
            "case_id": self.case_id,
            "action_type": self.action_type_str,
            "description": self.description,
            "status": self.status.value,
            "risk_level": self.risk_level.value,
            "requires_approval": self.requires_approval,
            "created_at": to_iso_utc(self.created_at),
            "executed_at": to_iso_utc(self.executed_at),
            "parameters": dict(self.parameters),
            "result": dict(self.result),
            "idempotency_key": self.idempotency_key,
            "fingerprint": self.compute_fingerprint(),
            "version": self.version,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Action:
        """Reconstruct Action from serialized dictionary."""
        return cls(
            action_id=data["action_id"],
            case_id=data["case_id"],
            action_type=data["action_type"],
            description=data["description"],
            status=ActionStatus(data["status"]),
            risk_level=RiskLevel(data["risk_level"]),
            requires_approval=bool(data.get("requires_approval", False)),
            created_at=from_iso_utc(data["created_at"]) or now_utc(),
            executed_at=from_iso_utc(data.get("executed_at")),
            parameters=dict(data.get("parameters") or {}),
            result=dict(data.get("result") or {}),
            idempotency_key=data.get("idempotency_key"),
            version=int(data.get("version", 1)),
        )
