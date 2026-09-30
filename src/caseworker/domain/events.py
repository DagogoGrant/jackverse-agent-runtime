"""Immutable domain event models with aggregate versioning and serialization."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any
import uuid

from caseworker.domain.types import ensure_utc, from_iso_utc, now_utc, to_iso_utc


@dataclass(frozen=True)
class DomainEvent:
    """Base immutable domain event representing a state change in an aggregate."""

    event_type: str
    aggregate_type: str
    aggregate_id: str
    aggregate_version: int
    user_id: str
    payload: dict[str, Any]
    event_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    occurred_at: datetime = field(default_factory=now_utc)
    schema_version: int = 1

    def __post_init__(self) -> None:
        if self.aggregate_version < 1:
            raise ValueError(f"aggregate_version must be >= 1, got {self.aggregate_version}")
        # Enforce timezone-aware UTC datetime
        if self.occurred_at.tzinfo is None:
            raise ValueError("Event occurred_at timestamp must be timezone-aware UTC.")
        object.__setattr__(self, "occurred_at", ensure_utc(self.occurred_at))

    def to_dict(self) -> dict[str, Any]:
        """Serialize event to a clean JSON-compatible dictionary."""
        return {
            "event_id": self.event_id,
            "event_type": self.event_type,
            "aggregate_type": self.aggregate_type,
            "aggregate_id": self.aggregate_id,
            "aggregate_version": self.aggregate_version,
            "user_id": self.user_id,
            "occurred_at": to_iso_utc(self.occurred_at),
            "payload": self.payload,
            "schema_version": self.schema_version,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> DomainEvent:
        """Reconstruct event from serialized dictionary."""
        occurred_raw = data["occurred_at"]
        occurred_dt = from_iso_utc(occurred_raw) if isinstance(occurred_raw, str) else occurred_raw
        return cls(
            event_id=data["event_id"],
            event_type=data["event_type"],
            aggregate_type=data["aggregate_type"],
            aggregate_id=data["aggregate_id"],
            aggregate_version=int(data["aggregate_version"]),
            user_id=data["user_id"],
            payload=dict(data.get("payload") or {}),
            occurred_at=occurred_dt,
            schema_version=int(data.get("schema_version", 1)),
        )


# Concrete event factories / helpers for typed event creation

def make_mission_created_event(
    mission_id: str,
    user_id: str,
    aggregate_version: int,
    payload: dict[str, Any],
) -> DomainEvent:
    return DomainEvent(
        event_type="mission.created",
        aggregate_type="mission",
        aggregate_id=mission_id,
        aggregate_version=aggregate_version,
        user_id=user_id,
        payload=payload,
    )


def make_mission_status_changed_event(
    mission_id: str,
    user_id: str,
    aggregate_version: int,
    old_status: str,
    new_status: str,
    reason: str | None = None,
) -> DomainEvent:
    return DomainEvent(
        event_type="mission.status_changed",
        aggregate_type="mission",
        aggregate_id=mission_id,
        aggregate_version=aggregate_version,
        user_id=user_id,
        payload={"old_status": old_status, "new_status": new_status, "reason": reason},
    )


def make_case_created_event(
    case_id: str,
    user_id: str,
    aggregate_version: int,
    payload: dict[str, Any],
) -> DomainEvent:
    return DomainEvent(
        event_type="case.created",
        aggregate_type="case",
        aggregate_id=case_id,
        aggregate_version=aggregate_version,
        user_id=user_id,
        payload=payload,
    )


def make_case_status_changed_event(
    case_id: str,
    user_id: str,
    aggregate_version: int,
    old_status: str,
    new_status: str,
    reason: str | None = None,
) -> DomainEvent:
    return DomainEvent(
        event_type="case.status_changed",
        aggregate_type="case",
        aggregate_id=case_id,
        aggregate_version=aggregate_version,
        user_id=user_id,
        payload={"old_status": old_status, "new_status": new_status, "reason": reason},
    )


def make_case_resolved_event(
    case_id: str,
    user_id: str,
    aggregate_version: int,
    outcome: str,
) -> DomainEvent:
    return DomainEvent(
        event_type="case.resolved",
        aggregate_type="case",
        aggregate_id=case_id,
        aggregate_version=aggregate_version,
        user_id=user_id,
        payload={"outcome": outcome},
    )


def make_opportunity_discovered_event(
    opportunity_id: str,
    user_id: str,
    aggregate_version: int,
    payload: dict[str, Any],
) -> DomainEvent:
    return DomainEvent(
        event_type="opportunity.discovered",
        aggregate_type="opportunity",
        aggregate_id=opportunity_id,
        aggregate_version=aggregate_version,
        user_id=user_id,
        payload=payload,
    )


def make_opportunity_status_changed_event(
    opportunity_id: str,
    user_id: str,
    aggregate_version: int,
    old_status: str,
    new_status: str,
    reason: str | None = None,
) -> DomainEvent:
    return DomainEvent(
        event_type="opportunity.status_changed",
        aggregate_type="opportunity",
        aggregate_id=opportunity_id,
        aggregate_version=aggregate_version,
        user_id=user_id,
        payload={"old_status": old_status, "new_status": new_status, "reason": reason},
    )


def make_action_proposed_event(
    action_id: str,
    case_id: str,
    user_id: str,
    aggregate_version: int,
    payload: dict[str, Any],
) -> DomainEvent:
    return DomainEvent(
        event_type="action.proposed",
        aggregate_type="action",
        aggregate_id=action_id,
        aggregate_version=aggregate_version,
        user_id=user_id,
        payload={"case_id": case_id, **payload},
    )


def make_action_status_changed_event(
    action_id: str,
    user_id: str,
    aggregate_version: int,
    old_status: str,
    new_status: str,
    reason: str | None = None,
) -> DomainEvent:
    return DomainEvent(
        event_type="action.status_changed",
        aggregate_type="action",
        aggregate_id=action_id,
        aggregate_version=aggregate_version,
        user_id=user_id,
        payload={"old_status": old_status, "new_status": new_status, "reason": reason},
    )


def make_approval_requested_event(
    approval_id: str,
    action_id: str,
    case_id: str,
    user_id: str,
    aggregate_version: int,
    action_fingerprint: str,
) -> DomainEvent:
    return DomainEvent(
        event_type="approval.requested",
        aggregate_type="approval",
        aggregate_id=approval_id,
        aggregate_version=aggregate_version,
        user_id=user_id,
        payload={
            "action_id": action_id,
            "case_id": case_id,
            "action_fingerprint": action_fingerprint,
        },
    )


def make_approval_decided_event(
    approval_id: str,
    user_id: str,
    aggregate_version: int,
    decision: str,
    reason: str | None = None,
) -> DomainEvent:
    return DomainEvent(
        event_type="approval.decided",
        aggregate_type="approval",
        aggregate_id=approval_id,
        aggregate_version=aggregate_version,
        user_id=user_id,
        payload={"decision": decision, "reason": reason},
    )


def make_context_fact_created_event(
    fact_id: str,
    user_id: str,
    aggregate_version: int,
    payload: dict[str, Any],
) -> DomainEvent:
    return DomainEvent(
        event_type="context_fact.created",
        aggregate_type="context_fact",
        aggregate_id=fact_id,
        aggregate_version=aggregate_version,
        user_id=user_id,
        payload=payload,
    )


def make_context_fact_updated_event(
    fact_id: str,
    user_id: str,
    aggregate_version: int,
    payload: dict[str, Any],
) -> DomainEvent:
    return DomainEvent(
        event_type="context_fact.updated",
        aggregate_type="context_fact",
        aggregate_id=fact_id,
        aggregate_version=aggregate_version,
        user_id=user_id,
        payload=payload,
    )
