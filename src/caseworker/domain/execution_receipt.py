"""ExecutionReceipt domain aggregate representing immutable, cryptographic proof of an executed transmission."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
import re
from typing import Any
import uuid

from caseworker.domain.enums import ExecutionReceiptStatus
from caseworker.domain.errors import DomainValidationError
from caseworker.domain.types import (
    canonical_json_dumps,
    compute_sha256,
    ensure_utc,
    from_iso_utc,
    now_utc,
    to_iso_utc,
)

EXEC_RECEIPT_SCHEMA_VERSION = "exec_receipt_v1"

SAFE_PROVIDER_METADATA_ALLOWLIST = {
    "http_status",
    "provider_request_id",
    "duration_ms",
    "response_category",
    "confirmation_reference",
    "engine",
    "status_code",
    "mode",
}

FORBIDDEN_METADATA_PATTERN = re.compile(
    r"(pass(word)?|cookie|auth(orization)?|token|secret|api_?key|session|csrf|credential|bearer|eyj)",
    re.IGNORECASE,
)


def sanitize_provider_metadata(meta: dict[str, Any] | None) -> dict[str, Any]:
    """Sanitize provider metadata using a strict key allowlist and credential filter."""
    if not meta:
        return {}
    sanitized: dict[str, Any] = {}
    for k, v in meta.items():
        k_clean = str(k).strip()
        if FORBIDDEN_METADATA_PATTERN.search(k_clean):
            continue
        if k_clean not in SAFE_PROVIDER_METADATA_ALLOWLIST:
            continue
        v_str = str(v)
        if FORBIDDEN_METADATA_PATTERN.search(v_str):
            continue
        sanitized[k_clean] = v
    return sanitized


@dataclass(frozen=True)
class ExecutionReceipt:
    """Immutable, content-addressed execution receipt providing verifiable audit proof of transmission."""

    attempt_id: str
    action_id: str
    approval_id: str
    plan_id: str
    case_id: str
    user_id: str
    destination_hostname: str
    destination_url: str
    destination_type: str
    execution_plan_fingerprint: str
    authorized_action_fingerprint: str
    package_fingerprint: str
    status: ExecutionReceiptStatus = ExecutionReceiptStatus.VERIFIED_SUCCESS
    executed_at: datetime = field(default_factory=now_utc)
    external_reference_id: str | None = None
    field_count: int = 0
    upload_asset_ids: list[str] = field(default_factory=list)
    upload_asset_shas: list[str] = field(default_factory=list)
    screening_response_count: int = 0
    safe_provider_metadata: dict[str, Any] = field(default_factory=dict)
    receipt_id: str = field(default_factory=lambda: f"rcpt-{uuid.uuid4().hex[:16]}")
    receipt_hash: str = ""
    version: int = 1

    def __post_init__(self) -> None:
        if not self.receipt_id or not self.receipt_id.strip():
            raise DomainValidationError("ExecutionReceipt receipt_id cannot be empty.")
        if not self.attempt_id or not self.attempt_id.strip():
            raise DomainValidationError("ExecutionReceipt attempt_id cannot be empty.")
        if not self.action_id or not self.action_id.strip():
            raise DomainValidationError("ExecutionReceipt action_id cannot be empty.")
        if not self.approval_id or not self.approval_id.strip():
            raise DomainValidationError("ExecutionReceipt approval_id cannot be empty.")
        if not self.plan_id or not self.plan_id.strip():
            raise DomainValidationError("ExecutionReceipt plan_id cannot be empty.")
        if not self.case_id or not self.case_id.strip():
            raise DomainValidationError("ExecutionReceipt case_id cannot be empty.")
        if not self.user_id or not self.user_id.strip():
            raise DomainValidationError("ExecutionReceipt user_id cannot be empty.")
        if not self.execution_plan_fingerprint or not self.execution_plan_fingerprint.strip():
            raise DomainValidationError("ExecutionReceipt execution_plan_fingerprint cannot be empty.")
        if not self.authorized_action_fingerprint or not self.authorized_action_fingerprint.strip():
            raise DomainValidationError("ExecutionReceipt authorized_action_fingerprint cannot be empty.")
        if not self.package_fingerprint or not self.package_fingerprint.strip():
            raise DomainValidationError("ExecutionReceipt package_fingerprint cannot be empty.")

        clean_dt = ensure_utc(self.executed_at) or now_utc()
        object.__setattr__(self, "executed_at", clean_dt)

        if isinstance(self.status, str) and not isinstance(self.status, ExecutionReceiptStatus):
            object.__setattr__(self, "status", ExecutionReceiptStatus(self.status))

        # Enforce sanitization on provider metadata
        clean_meta = sanitize_provider_metadata(self.safe_provider_metadata)
        object.__setattr__(self, "safe_provider_metadata", clean_meta)

        # Compute deterministic receipt hash if not set
        computed_hash = self.compute_receipt_hash()
        if not self.receipt_hash:
            object.__setattr__(self, "receipt_hash", computed_hash)
        elif self.receipt_hash != computed_hash:
            raise DomainValidationError(
                f"ExecutionReceipt receipt_hash mismatch. Provided '{self.receipt_hash}', expected '{computed_hash}'."
            )

    def compute_receipt_hash(self) -> str:
        """Deterministic canonical SHA-256 hash across receipt audit attributes."""
        payload = {
            "schema_version": EXEC_RECEIPT_SCHEMA_VERSION,
            "receipt_id": self.receipt_id,
            "attempt_id": self.attempt_id,
            "action_id": self.action_id,
            "approval_id": self.approval_id,
            "plan_id": self.plan_id,
            "case_id": self.case_id,
            "user_id": self.user_id,
            "status": self.status.value,
            "destination_hostname": self.destination_hostname,
            "destination_url": self.destination_url,
            "destination_type": self.destination_type,
            "execution_plan_fingerprint": self.execution_plan_fingerprint,
            "authorized_action_fingerprint": self.authorized_action_fingerprint,
            "package_fingerprint": self.package_fingerprint,
            "executed_at": to_iso_utc(self.executed_at),
            "external_reference_id": self.external_reference_id,
            "field_count": self.field_count,
            "upload_asset_ids": sorted(self.upload_asset_ids),
            "upload_asset_shas": sorted(self.upload_asset_shas),
            "screening_response_count": self.screening_response_count,
            "safe_provider_metadata": self.safe_provider_metadata,
        }
        return compute_sha256(canonical_json_dumps(payload))

    def to_dict(self) -> dict[str, Any]:
        return {
            "receipt_id": self.receipt_id,
            "attempt_id": self.attempt_id,
            "action_id": self.action_id,
            "approval_id": self.approval_id,
            "plan_id": self.plan_id,
            "case_id": self.case_id,
            "user_id": self.user_id,
            "status": self.status.value,
            "destination_hostname": self.destination_hostname,
            "destination_url": self.destination_url,
            "destination_type": self.destination_type,
            "execution_plan_fingerprint": self.execution_plan_fingerprint,
            "authorized_action_fingerprint": self.authorized_action_fingerprint,
            "package_fingerprint": self.package_fingerprint,
            "executed_at": to_iso_utc(self.executed_at),
            "external_reference_id": self.external_reference_id,
            "field_count": self.field_count,
            "upload_asset_ids": list(self.upload_asset_ids),
            "upload_asset_shas": list(self.upload_asset_shas),
            "screening_response_count": self.screening_response_count,
            "safe_provider_metadata": dict(self.safe_provider_metadata),
            "receipt_hash": self.receipt_hash,
            "version": self.version,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ExecutionReceipt:
        return cls(
            receipt_id=data["receipt_id"],
            attempt_id=data["attempt_id"],
            action_id=data["action_id"],
            approval_id=data["approval_id"],
            plan_id=data["plan_id"],
            case_id=data["case_id"],
            user_id=data["user_id"],
            status=ExecutionReceiptStatus(data["status"]),
            destination_hostname=data["destination_hostname"],
            destination_url=data["destination_url"],
            destination_type=data["destination_type"],
            execution_plan_fingerprint=data["execution_plan_fingerprint"],
            authorized_action_fingerprint=data["authorized_action_fingerprint"],
            package_fingerprint=data["package_fingerprint"],
            executed_at=from_iso_utc(data["executed_at"]),
            external_reference_id=data.get("external_reference_id"),
            field_count=int(data.get("field_count", 0)),
            upload_asset_ids=list(data.get("upload_asset_ids", [])),
            upload_asset_shas=list(data.get("upload_asset_shas", [])),
            screening_response_count=int(data.get("screening_response_count", 0)),
            safe_provider_metadata=dict(data.get("safe_provider_metadata", {})),
            receipt_hash=data["receipt_hash"],
            version=int(data.get("version", 1)),
        )
