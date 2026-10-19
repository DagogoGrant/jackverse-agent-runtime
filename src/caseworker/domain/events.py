"""Immutable domain event models with aggregate versioning and serialization."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any
import uuid

from caseworker.domain.types import compute_sha256, ensure_utc, from_iso_utc, now_utc, to_iso_utc


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


def make_mission_archived_event(
    mission_id: str,
    user_id: str,
    aggregate_version: int,
    archived_at: str,
) -> DomainEvent:
    return DomainEvent(
        event_type="mission.archived",
        aggregate_type="mission",
        aggregate_id=mission_id,
        aggregate_version=aggregate_version,
        user_id=user_id,
        payload={"archived_at": archived_at},
    )


def make_mission_restored_event(
    mission_id: str,
    user_id: str,
    aggregate_version: int,
) -> DomainEvent:
    return DomainEvent(
        event_type="mission.restored",
        aggregate_type="mission",
        aggregate_id=mission_id,
        aggregate_version=aggregate_version,
        user_id=user_id,
        payload={},
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


def make_opportunity_normalized_event(
    opportunity_id: str,
    user_id: str,
    aggregate_version: int,
    requirements_count: int,
) -> DomainEvent:
    return DomainEvent(
        event_type="opportunity.normalized",
        aggregate_type="opportunity",
        aggregate_id=opportunity_id,
        aggregate_version=aggregate_version,
        user_id=user_id,
        payload={"requirements_count": requirements_count},
    )


def make_opportunity_evaluated_event(
    opportunity_id: str,
    user_id: str,
    aggregate_version: int,
    eligibility_status: str,
    fit_score: float,
    fit_tier: str,
    coverage: float,
) -> DomainEvent:
    return DomainEvent(
        event_type="opportunity.evaluated",
        aggregate_type="opportunity",
        aggregate_id=opportunity_id,
        aggregate_version=aggregate_version,
        user_id=user_id,
        payload={
            "eligibility_status": eligibility_status,
            "fit_score": fit_score,
            "fit_tier": fit_tier,
            "coverage": coverage,
        },
    )


def make_opportunity_shortlisted_event(
    opportunity_id: str,
    user_id: str,
    aggregate_version: int,
    reason: str | None = None,
    overridden: bool = False,
) -> DomainEvent:
    return DomainEvent(
        event_type="opportunity.shortlisted",
        aggregate_type="opportunity",
        aggregate_id=opportunity_id,
        aggregate_version=aggregate_version,
        user_id=user_id,
        payload={"reason": reason, "overridden": overridden},
    )


def make_opportunity_converted_to_case_event(
    opportunity_id: str,
    user_id: str,
    aggregate_version: int,
    case_id: str,
    mission_id: str | None,
    old_status: str,
    new_status: str,
) -> DomainEvent:
    return DomainEvent(
        event_type="opportunity.converted_to_case",
        aggregate_type="opportunity",
        aggregate_id=opportunity_id,
        aggregate_version=aggregate_version,
        user_id=user_id,
        payload={
            "case_id": case_id,
            "mission_id": mission_id,
            "old_status": old_status,
            "new_status": new_status,
        },
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


def make_context_source_registered_event(
    source_id: str,
    user_id: str,
    aggregate_version: int,
    source_type: str,
    title: str,
) -> DomainEvent:
    return DomainEvent(
        event_type="context.source_registered",
        aggregate_type="context_source",
        aggregate_id=source_id,
        aggregate_version=aggregate_version,
        user_id=user_id,
        payload={"source_id": source_id, "source_type": source_type, "title": title},
    )


def make_context_fact_verified_event(
    fact_id: str,
    user_id: str,
    aggregate_version: int,
    status: str,
) -> DomainEvent:
    return DomainEvent(
        event_type="context.fact_verified",
        aggregate_type="context_fact",
        aggregate_id=fact_id,
        aggregate_version=aggregate_version,
        user_id=user_id,
        payload={"fact_id": fact_id, "verification_status": status},
    )


def make_context_fact_superseded_event(
    fact_id: str,
    user_id: str,
    aggregate_version: int,
    superseded_by_fact_id: str,
) -> DomainEvent:
    return DomainEvent(
        event_type="context.fact_superseded",
        aggregate_type="context_fact",
        aggregate_id=fact_id,
        aggregate_version=aggregate_version,
        user_id=user_id,
        payload={"fact_id": fact_id, "superseded_by_fact_id": superseded_by_fact_id},
    )


def make_context_fact_rejected_event(
    fact_id: str,
    user_id: str,
    aggregate_version: int,
    reason: str | None = None,
) -> DomainEvent:
    return DomainEvent(
        event_type="context.fact_rejected",
        aggregate_type="context_fact",
        aggregate_id=fact_id,
        aggregate_version=aggregate_version,
        user_id=user_id,
        payload={"fact_id": fact_id, "reason": reason},
    )


def make_context_access_granted_event(
    user_id: str,
    aggregate_version: int,
    purpose: str,
    fact_ids: list[str],
    namespaces: list[str],
) -> DomainEvent:
    return DomainEvent(
        event_type="context.access_granted",
        aggregate_type="context_vault",
        aggregate_id=user_id,
        aggregate_version=aggregate_version,
        user_id=user_id,
        payload={
            "purpose": purpose,
            "accessed_fact_ids": list(fact_ids),
            "namespaces": list(namespaces),
        },
    )


CANONICAL_DENIAL_REASONS: dict[str, str] = {
    "PURPOSE_REQUIRED": "Requested purpose cannot be empty",
    "FACT_REJECTED": "Fact has been rejected",
    "FACT_SUPERSEDED": "Fact has been superseded",
    "FACT_EXPIRED": "Fact has expired",
    "LOW_CONFIDENCE": "Fact confidence is below required threshold",
    "UNVERIFIED_FACT": "Fact is unverified",
    "PURPOSE_NOT_AUTHORIZED": "Requested purpose is not authorized",
    "SENSITIVE_FACT_RESTRICTED": "Sensitive fact requires explicit purpose authorization",
}


def make_context_access_denied_event(
    user_id: str,
    aggregate_version: int,
    purpose: str,
    fact_id: str,
    reason: str | None = None,
    reason_code: str = "DENIED",
    namespace: str = "",
    key: str = "",
) -> DomainEvent:
    safe_reason = CANONICAL_DENIAL_REASONS.get(reason_code, "Access denied by context policy")
    return DomainEvent(
        event_type="context.access_denied",
        aggregate_type="context_vault",
        aggregate_id=user_id,
        aggregate_version=aggregate_version,
        user_id=user_id,
        payload={
            "purpose": purpose,
            "fact_id": fact_id,
            "reason": safe_reason,
            "reason_code": reason_code,
            "namespace": namespace,
            "key": key,
        },
    )


def make_claim_proposed_event(
    claim_id: str,
    user_id: str,
    aggregate_version: int,
    purpose: str,
    text: str,
    supporting_fact_ids: list[str],
    case_id: str | None = None,
    mission_id: str | None = None,
) -> DomainEvent:
    return DomainEvent(
        event_type="claim.proposed",
        aggregate_type="claim",
        aggregate_id=claim_id,
        aggregate_version=aggregate_version,
        user_id=user_id,
        payload={
            "claim_id": claim_id,
            "purpose": purpose,
            "claim_text_hash": compute_sha256(text.strip()),
            "supporting_fact_ids": list(supporting_fact_ids),
            "case_id": case_id,
            "mission_id": mission_id,
        },
    )



def make_claim_status_changed_event(
    claim_id: str,
    user_id: str,
    aggregate_version: int,
    old_status: str,
    new_status: str,
    reason: str | None = None,
) -> DomainEvent:
    return DomainEvent(
        event_type=f"claim.{new_status}",
        aggregate_type="claim",
        aggregate_id=claim_id,
        aggregate_version=aggregate_version,
        user_id=user_id,
        payload={
            "claim_id": claim_id,
            "old_status": old_status,
            "new_status": new_status,
            "reason": reason,
        },
    )


def make_preparation_report_created_event(
    report_id: str,
    case_id: str,
    opportunity_id: str,
    user_id: str,
    aggregate_version: int = 1,
    input_hash: str = "",
    context_snapshot_hash: str = "",
    schema_version: str = "prep_intel_v1",
) -> DomainEvent:
    return DomainEvent(
        event_type="preparation.report_created",
        aggregate_type="preparation_report",
        aggregate_id=report_id,
        aggregate_version=aggregate_version,
        user_id=user_id,
        payload={
            "report_id": report_id,
            "case_id": case_id,
            "opportunity_id": opportunity_id,
            "input_hash": input_hash,
            "context_snapshot_hash": context_snapshot_hash,
            "schema_version": schema_version,
        },
    )


def make_preparation_artifact_prepared_event(
    artifact_id: str,
    set_id: str,
    case_id: str,
    material_type: str,
    user_id: str,
    aggregate_version: int = 1,
    source_report_id: str = "",
    input_hash: str = "",
    grounding_snapshot_hash: str = "",
) -> DomainEvent:
    return DomainEvent(
        event_type="preparation.artifact_prepared",
        aggregate_type="preparation_artifact",
        aggregate_id=artifact_id,
        aggregate_version=aggregate_version,
        user_id=user_id,
        payload={
            "artifact_id": artifact_id,
            "set_id": set_id,
            "case_id": case_id,
            "material_type": material_type,
            "source_report_id": source_report_id,
            "input_hash": input_hash,
            "grounding_snapshot_hash": grounding_snapshot_hash,
        },
    )


def make_preparation_package_verified_event(
    package_id: str,
    case_id: str,
    artifact_set_id: str,
    source_report_id: str,
    package_fingerprint: str,
    user_id: str,
    aggregate_version: int = 1,
    artifact_count: int = 0,
    claim_count: int = 0,
) -> DomainEvent:
    return DomainEvent(
        event_type="preparation.package_verified",
        aggregate_type="preparation_package",
        aggregate_id=package_id,
        aggregate_version=aggregate_version,
        user_id=user_id,
        payload={
            "package_id": package_id,
            "case_id": case_id,
            "artifact_set_id": artifact_set_id,
            "source_report_id": source_report_id,
            "package_fingerprint": package_fingerprint,
            "artifact_count": artifact_count,
            "claim_count": claim_count,
        },
    )


def make_preparation_package_approved_event(
    package_id: str,
    case_id: str,
    package_fingerprint: str,
    user_id: str,
    aggregate_version: int = 2,
    reason: str | None = None,
) -> DomainEvent:
    return DomainEvent(
        event_type="preparation.package_approved",
        aggregate_type="preparation_package",
        aggregate_id=package_id,
        aggregate_version=aggregate_version,
        user_id=user_id,
        payload={
            "package_id": package_id,
            "case_id": case_id,
            "package_fingerprint": package_fingerprint,
            "reason": reason,
        },
    )


def make_preparation_package_rejected_event(
    package_id: str,
    case_id: str,
    package_fingerprint: str,
    user_id: str,
    aggregate_version: int = 2,
    reason: str | None = None,
) -> DomainEvent:
    return DomainEvent(
        event_type="preparation.package_rejected",
        aggregate_type="preparation_package",
        aggregate_id=package_id,
        aggregate_version=aggregate_version,
        user_id=user_id,
        payload={
            "package_id": package_id,
            "case_id": case_id,
            "package_fingerprint": package_fingerprint,
            "reason": reason,
        },
    )


def make_preparation_package_superseded_event(
    package_id: str,
    case_id: str,
    package_fingerprint: str,
    user_id: str,
    aggregate_version: int = 2,
    reason: str | None = None,
) -> DomainEvent:
    return DomainEvent(
        event_type="preparation.package_superseded",
        aggregate_type="preparation_package",
        aggregate_id=package_id,
        aggregate_version=aggregate_version,
        user_id=user_id,
        payload={
            "package_id": package_id,
            "case_id": case_id,
            "package_fingerprint": package_fingerprint,
            "reason": reason,
        },
    )


def make_external_execution_plan_created_event(
    user_id: str,
    case_id: str,
    plan_id: str,
    package_id: str,
    package_fingerprint: str,
    destination_type: str,
    canonical_url: str,
    plan_status: str,
    plan_fingerprint: str,
    field_mapping_count: int,
    upload_asset_count: int,
    screening_response_count: int,
    unresolved_findings_count: int,
    aggregate_version: int = 1,
) -> DomainEvent:
    """Create privacy-safe execution.plan_created event with zero raw personal values."""
    from urllib.parse import urlparse
    host = urlparse(canonical_url).netloc or canonical_url
    return DomainEvent(
        event_type="execution.plan_created",
        aggregate_type="external_execution_plan",
        aggregate_id=plan_id,
        aggregate_version=aggregate_version,
        user_id=user_id,
        payload={
            "plan_id": plan_id,
            "case_id": case_id,
            "package_id": package_id,
            "package_fingerprint": package_fingerprint,
            "destination_type": destination_type,
            "host": host,
            "plan_status": plan_status,
            "plan_fingerprint": plan_fingerprint,
            "field_mapping_count": field_mapping_count,
            "upload_asset_count": upload_asset_count,
            "screening_response_count": screening_response_count,
            "unresolved_findings_count": unresolved_findings_count,
        },
    )


def make_external_execution_plan_superseded_event(
    user_id: str,
    case_id: str,
    plan_id: str,
    superseded_by_plan_id: str,
    package_id: str,
    reason: str | None = None,
    aggregate_version: int = 2,
) -> DomainEvent:
    """Create privacy-safe execution.plan_superseded event."""
    return DomainEvent(
        event_type="execution.plan_superseded",
        aggregate_type="external_execution_plan",
        aggregate_id=plan_id,
        aggregate_version=aggregate_version,
        user_id=user_id,
        payload={
            "plan_id": plan_id,
            "case_id": case_id,
            "superseded_by_plan_id": superseded_by_plan_id,
            "package_id": package_id,
            "reason": reason,
        },
    )


def make_external_execution_plan_executed_event(
    user_id: str,
    case_id: str,
    plan_id: str,
    plan_fingerprint: str,
    receipt_id: str,
    reason: str | None = None,
    aggregate_version: int = 2,
) -> DomainEvent:
    """Create privacy-safe execution.plan_executed event."""
    return DomainEvent(
        event_type="execution.plan_executed",
        aggregate_type="external_execution_plan",
        aggregate_id=plan_id,
        aggregate_version=aggregate_version,
        user_id=user_id,
        payload={
            "plan_id": plan_id,
            "case_id": case_id,
            "plan_fingerprint": plan_fingerprint,
            "receipt_id": receipt_id,
            "reason": reason,
        },
    )


def make_execution_attempt_started_event(
    user_id: str,
    case_id: str,
    plan_id: str,
    action_id: str,
    attempt_id: str,
    provider_kind: str,
    fencing_token: int,
    attempt_number: int = 1,
) -> DomainEvent:
    """Create privacy-safe execution.attempt_started event."""
    return DomainEvent(
        event_type="execution.attempt_started",
        aggregate_type="execution_attempt",
        aggregate_id=attempt_id,
        aggregate_version=1,
        user_id=user_id,
        payload={
            "attempt_id": attempt_id,
            "case_id": case_id,
            "plan_id": plan_id,
            "action_id": action_id,
            "provider_kind": provider_kind,
            "fencing_token": fencing_token,
            "attempt_number": attempt_number,
        },
    )


def make_execution_reversible_point_passed_event(
    user_id: str,
    case_id: str,
    plan_id: str,
    action_id: str,
    attempt_id: str,
    fencing_token: int,
    staged_field_count: int,
    staged_upload_count: int,
) -> DomainEvent:
    """Create privacy-safe execution.last_reversible_point_reached event."""
    return DomainEvent(
        event_type="execution.last_reversible_point_reached",
        aggregate_type="execution_attempt",
        aggregate_id=attempt_id,
        aggregate_version=2,
        user_id=user_id,
        payload={
            "attempt_id": attempt_id,
            "case_id": case_id,
            "plan_id": plan_id,
            "action_id": action_id,
            "fencing_token": fencing_token,
            "staged_field_count": staged_field_count,
            "staged_upload_count": staged_upload_count,
        },
    )


def make_execution_attempt_completed_event(
    user_id: str,
    case_id: str,
    plan_id: str,
    action_id: str,
    attempt_id: str,
    status: str,
    irreversible_action_taken: bool,
    receipt_id: str | None = None,
    error_category: str | None = None,
    error_message: str | None = None,
) -> DomainEvent:
    """Create privacy-safe execution.attempt_completed event."""
    return DomainEvent(
        event_type="execution.attempt_completed",
        aggregate_type="execution_attempt",
        aggregate_id=attempt_id,
        aggregate_version=3,
        user_id=user_id,
        payload={
            "attempt_id": attempt_id,
            "case_id": case_id,
            "plan_id": plan_id,
            "action_id": action_id,
            "status": status,
            "irreversible_action_taken": irreversible_action_taken,
            "receipt_id": receipt_id,
            "error_category": error_category,
            "error_message": error_message,
        },
    )


def make_execution_receipt_created_event(
    user_id: str,
    case_id: str,
    plan_id: str,
    action_id: str,
    attempt_id: str,
    receipt_id: str,
    receipt_hash: str,
    status: str,
    destination_hostname: str,
    destination_type: str,
    external_reference_id: str | None = None,
) -> DomainEvent:
    """Create privacy-safe execution.receipt_created event."""
    return DomainEvent(
        event_type="execution.receipt_created",
        aggregate_type="execution_receipt",
        aggregate_id=receipt_id,
        aggregate_version=1,
        user_id=user_id,
        payload={
            "receipt_id": receipt_id,
            "attempt_id": attempt_id,
            "case_id": case_id,
            "plan_id": plan_id,
            "action_id": action_id,
            "receipt_hash": receipt_hash,
            "status": status,
            "destination_hostname": destination_hostname,
            "destination_type": destination_type,
            "external_reference_id": external_reference_id,
        },
    )




