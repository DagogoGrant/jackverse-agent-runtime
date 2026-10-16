"""ExternalExecutionPlan domain aggregate representing an exact, content-addressed external action plan."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
import re
from typing import Any
import uuid

from caseworker.domain.enums import (
    DestinationProvenance,
    ExecutionDestinationType,
    ExternalExecutionPlanStatus,
    VerificationStatus,
)
from caseworker.domain.errors import DomainValidationError
from caseworker.domain.types import (
    canonical_json_dumps,
    compute_sha256,
    ensure_utc,
    from_iso_utc,
    now_utc,
    to_iso_utc,
)

EXEC_PLAN_SCHEMA_VERSION = "exec_plan_v1"
EXEC_ACTION_PLANNER_VERSION = "action_planner_v1"

FORBIDDEN_METADATA_PATTERN = re.compile(
    r"(pass(word)?|cookie|auth(orization)?|token|secret|api_?key|session|csrf|credential)",
    re.IGNORECASE,
)


def assert_safe_channel_metadata(metadata: dict[str, Any]) -> None:
    """Validate that channel metadata does not contain sensitive secrets or tokens."""
    for key in metadata.keys():
        if FORBIDDEN_METADATA_PATTERN.search(key):
            raise DomainValidationError(
                f"Secret or credential key '{key}' is forbidden in channel_metadata."
            )


@dataclass(frozen=True)
class TargetSchemaField:
    """Declared field specification from a target interaction schema."""

    target_field_id: str
    label: str
    field_type: str = "text"
    required: bool = True
    allowed_options: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "target_field_id": self.target_field_id,
            "label": self.label,
            "field_type": self.field_type,
            "required": self.required,
            "allowed_options": list(self.allowed_options),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> TargetSchemaField:
        return cls(
            target_field_id=data["target_field_id"],
            label=data["label"],
            field_type=data.get("field_type", "text"),
            required=bool(data.get("required", True)),
            allowed_options=list(data.get("allowed_options", [])),
        )


@dataclass(frozen=True)
class TargetSchemaUploadSlot:
    """Declared upload slot specification from a target interaction schema."""

    slot_id: str
    role: str
    label: str
    required: bool = True
    accepted_mime_types: list[str] = field(default_factory=lambda: ["application/pdf", "text/plain"])

    def to_dict(self) -> dict[str, Any]:
        return {
            "slot_id": self.slot_id,
            "role": self.role,
            "label": self.label,
            "required": self.required,
            "accepted_mime_types": list(self.accepted_mime_types),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> TargetSchemaUploadSlot:
        return cls(
            slot_id=data["slot_id"],
            role=data["role"],
            label=data["label"],
            required=bool(data.get("required", True)),
            accepted_mime_types=list(data.get("accepted_mime_types", ["application/pdf", "text/plain"])),
        )


@dataclass(frozen=True)
class TargetSchemaQuestion:
    """Declared screening question from a target interaction schema."""

    question_id: str
    question_text: str
    required: bool = True
    answer_type: str = "text"

    def to_dict(self) -> dict[str, Any]:
        return {
            "question_id": self.question_id,
            "question_text": self.question_text,
            "required": self.required,
            "answer_type": self.answer_type,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> TargetSchemaQuestion:
        return cls(
            question_id=data["question_id"],
            question_text=data["question_text"],
            required=bool(data.get("required", True)),
            answer_type=data.get("answer_type", "text"),
        )


@dataclass(frozen=True)
class TargetSubmitControlSpec:
    """Specification of the final submission control on the target destination."""

    control_type: str = "button_submit"
    semantic_target: str = "Submit Application"
    selector: str = ""
    method: str = "POST"
    requires_double_confirmation: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "control_type": self.control_type,
            "semantic_target": self.semantic_target,
            "selector": self.selector,
            "method": self.method,
            "requires_double_confirmation": self.requires_double_confirmation,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> TargetSubmitControlSpec:
        return cls(
            control_type=data.get("control_type", "button_submit"),
            semantic_target=data.get("semantic_target", "Submit Application"),
            selector=data.get("selector", ""),
            method=data.get("method", "POST"),
            requires_double_confirmation=bool(data.get("requires_double_confirmation", True)),
        )


@dataclass(frozen=True)
class TargetInteractionSchema:
    """Authoritative interaction schema declaring target fields, upload slots, questions, and submit control."""

    schema_id: str
    destination_type: str
    fields: list[TargetSchemaField] = field(default_factory=list)
    upload_slots: list[TargetSchemaUploadSlot] = field(default_factory=list)
    screening_questions: list[TargetSchemaQuestion] = field(default_factory=list)
    final_submit_control: TargetSubmitControlSpec | None = None
    schema_hash: str = ""

    def __post_init__(self) -> None:
        computed = self.compute_hash()
        if not self.schema_hash:
            object.__setattr__(self, "schema_hash", computed)
        elif self.schema_hash != computed:
            raise DomainValidationError(
                f"TargetInteractionSchema hash mismatch (stored '{self.schema_hash}' vs computed '{computed}')."
            )

    def compute_hash(self) -> str:
        canonical_fields = [
            [f.target_field_id, f.label, f.field_type, f.required, sorted(f.allowed_options)]
            for f in sorted(self.fields, key=lambda f: f.target_field_id)
        ]
        canonical_slots = [
            [s.slot_id, s.role, s.label, s.required, sorted(s.accepted_mime_types)]
            for s in sorted(self.upload_slots, key=lambda s: s.slot_id)
        ]
        canonical_questions = [
            [q.question_id, q.question_text, q.required, q.answer_type]
            for q in sorted(self.screening_questions, key=lambda q: q.question_id)
        ]
        canonical_control = self.final_submit_control.to_dict() if self.final_submit_control else {}
        payload = [
            self.schema_id,
            self.destination_type,
            canonical_fields,
            canonical_slots,
            canonical_questions,
            canonical_control,
        ]
        return compute_sha256(canonical_json_dumps(payload))

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_id": self.schema_id,
            "destination_type": self.destination_type,
            "fields": [f.to_dict() for f in self.fields],
            "upload_slots": [s.to_dict() for s in self.upload_slots],
            "screening_questions": [q.to_dict() for q in self.screening_questions],
            "final_submit_control": self.final_submit_control.to_dict() if self.final_submit_control else None,
            "schema_hash": self.schema_hash,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> TargetInteractionSchema:
        control = (
            TargetSubmitControlSpec.from_dict(data["final_submit_control"])
            if data.get("final_submit_control")
            else None
        )
        return cls(
            schema_id=data["schema_id"],
            destination_type=data.get("destination_type", "ats_form"),
            fields=[TargetSchemaField.from_dict(f) for f in data.get("fields", [])],
            upload_slots=[TargetSchemaUploadSlot.from_dict(s) for s in data.get("upload_slots", [])],
            screening_questions=[TargetSchemaQuestion.from_dict(q) for q in data.get("screening_questions", [])],
            final_submit_control=control,
            schema_hash=data.get("schema_hash", ""),
        )


@dataclass(frozen=True)
class ExecutionDestination:
    """Exact destination specification with explicit provenance."""

    destination_type: str
    destination_provenance: str
    canonical_url: str
    organization: str
    channel_metadata: dict[str, Any] = field(default_factory=dict)
    is_exact_destination: bool = True

    def __post_init__(self) -> None:
        assert_safe_channel_metadata(self.channel_metadata)

    def to_dict(self) -> dict[str, Any]:
        return {
            "destination_type": self.destination_type,
            "destination_provenance": self.destination_provenance,
            "canonical_url": self.canonical_url,
            "organization": self.organization,
            "channel_metadata": dict(self.channel_metadata),
            "is_exact_destination": self.is_exact_destination,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ExecutionDestination:
        return cls(
            destination_type=data["destination_type"],
            destination_provenance=data["destination_provenance"],
            canonical_url=data["canonical_url"],
            organization=data.get("organization", ""),
            channel_metadata=data.get("channel_metadata", {}),
            is_exact_destination=bool(data.get("is_exact_destination", True)),
        )


@dataclass(frozen=True)
class ExecutionFieldMapping:
    """Exact mapping of a target form field to a verified Vault ContextFact."""

    target_field_id: str
    field_name: str
    field_value: Any
    source_fact_id: str
    source_fact_version: int
    verification_status: str
    required: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "target_field_id": self.target_field_id,
            "field_name": self.field_name,
            "field_value": self.field_value,
            "source_fact_id": self.source_fact_id,
            "source_fact_version": self.source_fact_version,
            "verification_status": self.verification_status,
            "required": self.required,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ExecutionFieldMapping:
        return cls(
            target_field_id=data["target_field_id"],
            field_name=data["field_name"],
            field_value=data["field_value"],
            source_fact_id=data["source_fact_id"],
            source_fact_version=int(data["source_fact_version"]),
            verification_status=data["verification_status"],
            required=bool(data.get("required", True)),
        )


@dataclass(frozen=True)
class ExecutionUploadAsset:
    """Exact materialized upload asset bound to a frozen package artifact."""

    asset_id: str
    role: str
    source_artifact_id: str
    source_artifact_version: int
    filename: str
    mime_type: str
    content_sha256: str
    byte_size: int
    materialized: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "asset_id": self.asset_id,
            "role": self.role,
            "source_artifact_id": self.source_artifact_id,
            "source_artifact_version": self.source_artifact_version,
            "filename": self.filename,
            "mime_type": self.mime_type,
            "content_sha256": self.content_sha256,
            "byte_size": self.byte_size,
            "materialized": self.materialized,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ExecutionUploadAsset:
        return cls(
            asset_id=data["asset_id"],
            role=data["role"],
            source_artifact_id=data["source_artifact_id"],
            source_artifact_version=int(data["source_artifact_version"]),
            filename=data["filename"],
            mime_type=data["mime_type"],
            content_sha256=data["content_sha256"],
            byte_size=int(data["byte_size"]),
            materialized=bool(data.get("materialized", True)),
        )


@dataclass(frozen=True)
class ExecutionScreeningResponse:
    """Exact screening answer response mapped from approved SCREENING_ANSWERS artifact."""

    question_id: str
    question: str
    answer: str
    source_artifact_id: str
    grounding_claim_ids: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "question_id": self.question_id,
            "question": self.question,
            "answer": self.answer,
            "source_artifact_id": self.source_artifact_id,
            "grounding_claim_ids": list(self.grounding_claim_ids),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ExecutionScreeningResponse:
        return cls(
            question_id=data["question_id"],
            question=data["question"],
            answer=data["answer"],
            source_artifact_id=data["source_artifact_id"],
            grounding_claim_ids=list(data.get("grounding_claim_ids", [])),
        )


@dataclass(frozen=True)
class ExecutionPlanFinding:
    """Deterministic validation finding evaluating plan completeness."""

    check_name: str
    passed: bool
    severity: str  # "info", "warning", "error"
    code: str
    message: str
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "check_name": self.check_name,
            "passed": self.passed,
            "severity": self.severity,
            "code": self.code,
            "message": self.message,
            "details": dict(self.details),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ExecutionPlanFinding:
        return cls(
            check_name=data["check_name"],
            passed=bool(data.get("passed", True)),
            severity=data.get("severity", "info"),
            code=data.get("code", ""),
            message=data.get("message", ""),
            details=data.get("details", {}),
        )


def compute_execution_input_hash(field_mappings: list[ExecutionFieldMapping]) -> str:
    """Compute deterministic dependency hash across all backing ContextFacts."""
    canonical_deps = [
        [m.source_fact_id, m.source_fact_version, str(m.verification_status)]
        for m in sorted(field_mappings, key=lambda m: (m.source_fact_id, m.target_field_id))
    ]
    return compute_sha256(canonical_json_dumps(canonical_deps))


def compute_execution_plan_fingerprint(
    case_id: str,
    opportunity_id: str,
    package_id: str,
    package_fingerprint: str,
    destination: ExecutionDestination,
    target_schema_hash: str,
    field_mappings: list[ExecutionFieldMapping],
    upload_mappings: list[ExecutionUploadAsset],
    screening_responses: list[ExecutionScreeningResponse],
    final_submit_control: TargetSubmitControlSpec | None,
    execution_input_hash: str,
    schema_version: str = EXEC_PLAN_SCHEMA_VERSION,
    planner_version: str = EXEC_ACTION_PLANNER_VERSION,
) -> str:
    """Compute content-addressed SHA-256 fingerprint for an ExternalExecutionPlan.

    Invariant Rules:
    - Binds destination, target schema hash, fact versions, upload byte hashes, questions, and submit control.
    - Excludes plan_id, status, version, timestamps, and findings.
    """
    canonical_destination = [
        destination.destination_type,
        destination.destination_provenance,
        destination.canonical_url,
        destination.organization,
        sorted([[k, str(v)] for k, v in destination.channel_metadata.items()]),
    ]
    canonical_fields = [
        [
            m.target_field_id,
            m.field_name,
            str(m.field_value),
            m.source_fact_id,
            m.source_fact_version,
            str(m.verification_status),
            m.required,
        ]
        for m in sorted(field_mappings, key=lambda m: m.target_field_id)
    ]
    canonical_uploads = [
        [
            u.role,
            u.source_artifact_id,
            u.source_artifact_version,
            u.filename,
            u.mime_type,
            u.content_sha256,
            u.byte_size,
            u.materialized,
        ]
        for u in sorted(upload_mappings, key=lambda u: (u.role, u.source_artifact_id))
    ]
    canonical_screening = [
        [
            s.question_id,
            s.question,
            s.answer,
            s.source_artifact_id,
            sorted(s.grounding_claim_ids),
        ]
        for s in sorted(screening_responses, key=lambda s: s.question_id)
    ]
    canonical_control = final_submit_control.to_dict() if final_submit_control else {}

    payload = [
        schema_version,
        planner_version,
        case_id,
        opportunity_id,
        package_id,
        package_fingerprint,
        canonical_destination,
        target_schema_hash,
        canonical_fields,
        canonical_uploads,
        canonical_screening,
        canonical_control,
        execution_input_hash,
    ]
    return compute_sha256(canonical_json_dumps(payload))


_VALID_PLAN_TRANSITIONS: dict[ExternalExecutionPlanStatus, set[ExternalExecutionPlanStatus]] = {
    ExternalExecutionPlanStatus.DRAFT: {
        ExternalExecutionPlanStatus.READY_FOR_AUTHORIZATION,
        ExternalExecutionPlanStatus.SUPERSEDED,
    },
    ExternalExecutionPlanStatus.READY_FOR_AUTHORIZATION: {
        ExternalExecutionPlanStatus.SUPERSEDED,
    },
    ExternalExecutionPlanStatus.SUPERSEDED: set(),
}


@dataclass
class ExternalExecutionPlan:
    """Immutable, content-addressed external execution plan ready for governance.

    Constitutional boundary:
    - Authorizing this plan does NOT execute it.
    - Zero outbound network transmission occurs in Phase 5.3.1.
    """

    case_id: str
    opportunity_id: str
    package_id: str
    package_fingerprint: str
    user_id: str
    destination: ExecutionDestination
    status: ExternalExecutionPlanStatus = ExternalExecutionPlanStatus.DRAFT
    target_schema: TargetInteractionSchema | None = None
    field_mappings: list[ExecutionFieldMapping] = field(default_factory=list)
    upload_mappings: list[ExecutionUploadAsset] = field(default_factory=list)
    screening_responses: list[ExecutionScreeningResponse] = field(default_factory=list)
    final_submit_control: TargetSubmitControlSpec | None = None
    findings: list[ExecutionPlanFinding] = field(default_factory=list)
    execution_input_hash: str = ""
    execution_plan_fingerprint: str = ""
    plan_id: str = field(default_factory=lambda: f"plan-{uuid.uuid4().hex[:16]}")
    schema_version: str = EXEC_PLAN_SCHEMA_VERSION
    planner_version: str = EXEC_ACTION_PLANNER_VERSION
    created_at: datetime = field(default_factory=now_utc)
    updated_at: datetime = field(default_factory=now_utc)
    version: int = 1

    def __post_init__(self) -> None:
        if not self.plan_id or not self.plan_id.strip():
            raise DomainValidationError("ExternalExecutionPlan plan_id cannot be empty.")
        if not self.case_id or not self.case_id.strip():
            raise DomainValidationError("ExternalExecutionPlan case_id cannot be empty.")
        if not self.opportunity_id or not self.opportunity_id.strip():
            raise DomainValidationError("ExternalExecutionPlan opportunity_id cannot be empty.")
        if not self.package_id or not self.package_id.strip():
            raise DomainValidationError("ExternalExecutionPlan package_id cannot be empty.")
        if not self.package_fingerprint or not self.package_fingerprint.strip():
            raise DomainValidationError("ExternalExecutionPlan package_fingerprint cannot be empty.")
        if not self.user_id or not self.user_id.strip():
            raise DomainValidationError("ExternalExecutionPlan user_id cannot be empty.")

        self.created_at = ensure_utc(self.created_at) or now_utc()
        self.updated_at = ensure_utc(self.updated_at) or now_utc()

        if isinstance(self.status, str) and not isinstance(self.status, ExternalExecutionPlanStatus):
            self.status = ExternalExecutionPlanStatus(self.status)

        # Compute execution input hash if empty
        if not self.execution_input_hash:
            self.execution_input_hash = compute_execution_input_hash(self.field_mappings)

        # Compute or verify fingerprint
        computed_fp = self.compute_fingerprint()
        if not self.execution_plan_fingerprint:
            self.execution_plan_fingerprint = computed_fp
        elif self.execution_plan_fingerprint != computed_fp:
            raise DomainValidationError(
                f"Execution plan fingerprint mismatch (stored '{self.execution_plan_fingerprint}' vs computed '{computed_fp}')."
            )

    def compute_fingerprint(self) -> str:
        target_schema_hash = self.target_schema.schema_hash if self.target_schema else ""
        return compute_execution_plan_fingerprint(
            case_id=self.case_id,
            opportunity_id=self.opportunity_id,
            package_id=self.package_id,
            package_fingerprint=self.package_fingerprint,
            destination=self.destination,
            target_schema_hash=target_schema_hash,
            field_mappings=self.field_mappings,
            upload_mappings=self.upload_mappings,
            screening_responses=self.screening_responses,
            final_submit_control=self.final_submit_control,
            execution_input_hash=self.execution_input_hash,
            schema_version=self.schema_version,
            planner_version=self.planner_version,
        )

    def transition_to(self, new_status: ExternalExecutionPlanStatus, reason: str | None = None) -> None:
        """State machine transition."""
        if new_status == self.status:
            return
        valid = _VALID_PLAN_TRANSITIONS.get(self.status, set())
        if new_status not in valid:
            raise DomainValidationError(
                f"Invalid ExternalExecutionPlan transition from '{self.status.value}' to '{new_status.value}'."
            )
        self.status = new_status
        self.updated_at = now_utc()
        self.version += 1

    def mark_superseded(self, reason: str = "Superseded by newer execution plan") -> None:
        self.transition_to(ExternalExecutionPlanStatus.SUPERSEDED, reason=reason)

    def to_dict(self) -> dict[str, Any]:
        return {
            "plan_id": self.plan_id,
            "case_id": self.case_id,
            "opportunity_id": self.opportunity_id,
            "package_id": self.package_id,
            "package_fingerprint": self.package_fingerprint,
            "user_id": self.user_id,
            "status": self.status.value,
            "destination": self.destination.to_dict(),
            "target_schema": self.target_schema.to_dict() if self.target_schema else None,
            "field_mappings": [m.to_dict() for m in self.field_mappings],
            "upload_mappings": [u.to_dict() for u in self.upload_mappings],
            "screening_responses": [s.to_dict() for s in self.screening_responses],
            "final_submit_control": self.final_submit_control.to_dict() if self.final_submit_control else None,
            "findings": [f.to_dict() for f in self.findings],
            "execution_input_hash": self.execution_input_hash,
            "execution_plan_fingerprint": self.execution_plan_fingerprint,
            "schema_version": self.schema_version,
            "planner_version": self.planner_version,
            "created_at": to_iso_utc(self.created_at),
            "updated_at": to_iso_utc(self.updated_at),
            "version": self.version,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ExternalExecutionPlan:
        destination = ExecutionDestination.from_dict(data["destination"])
        schema = TargetInteractionSchema.from_dict(data["target_schema"]) if data.get("target_schema") else None
        control = TargetSubmitControlSpec.from_dict(data["final_submit_control"]) if data.get("final_submit_control") else None
        return cls(
            plan_id=data["plan_id"],
            case_id=data["case_id"],
            opportunity_id=data["opportunity_id"],
            package_id=data["package_id"],
            package_fingerprint=data["package_fingerprint"],
            user_id=data["user_id"],
            destination=destination,
            status=ExternalExecutionPlanStatus(data["status"]),
            target_schema=schema,
            field_mappings=[ExecutionFieldMapping.from_dict(m) for m in data.get("field_mappings", [])],
            upload_mappings=[ExecutionUploadAsset.from_dict(u) for u in data.get("upload_mappings", [])],
            screening_responses=[ExecutionScreeningResponse.from_dict(s) for s in data.get("screening_responses", [])],
            final_submit_control=control,
            findings=[ExecutionPlanFinding.from_dict(f) for f in data.get("findings", [])],
            execution_input_hash=data.get("execution_input_hash", ""),
            execution_plan_fingerprint=data.get("execution_plan_fingerprint", ""),
            schema_version=data.get("schema_version", EXEC_PLAN_SCHEMA_VERSION),
            planner_version=data.get("planner_version", EXEC_ACTION_PLANNER_VERSION),
            created_at=from_iso_utc(data["created_at"]),
            updated_at=from_iso_utc(data["updated_at"]),
            version=int(data.get("version", 1)),
        )
