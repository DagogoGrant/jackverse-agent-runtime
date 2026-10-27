"""ExternalExecutionPlan domain aggregate representing an exact, content-addressed external action plan."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
import re
from typing import Any
import urllib.parse
import uuid

from caseworker.domain.enums import (
    ClaimStatus,
    DestinationProvenance,
    ExecutionDestinationType,
    ExternalExecutionPlanStatus,
    PreparationPackageStatus,
    TargetAnswerType,
    TargetFieldType,
    TargetSchemaProvenance,
    VerificationStatus,
)
from caseworker.domain.errors import DomainValidationError
from caseworker.domain.types import (
    canonical_json_dumps,
    compute_sha256,
    compute_sha256_bytes,
    ensure_utc,
    from_iso_utc,
    now_utc,
    to_iso_utc,
)

EXEC_PLAN_SCHEMA_VERSION = "exec_plan_v1"
EXEC_ACTION_PLANNER_VERSION = "action_planner_v1"

ALLOWED_CHANNEL_METADATA_KEYS = {
    "browser_engine",
    "form_id",
    "portal_type",
    "viewport",
    "render_mode",
    "user_agent_category",
    "wait_selector",
    "page_title",
    "notes",
    "email_subject",
    "reply_to",
    "application_channel",
}

FORBIDDEN_METADATA_PATTERN = re.compile(
    r"(pass(word)?|cookie|auth(orization)?|token|secret|api_?key|session|csrf|credential|bearer|eyj)",
    re.IGNORECASE,
)

FORBIDDEN_SECRET_VALUE_PATTERN = re.compile(
    r"(bearer\s+[a-zA-Z0-9_\-\.]+|eyj[a-zA-Z0-9_\-\.]+|ghp_[a-zA-Z0-9]+|ak_[a-zA-Z0-9]+|sk_[a-zA-Z0-9]+|-----BEGIN[ A-Z0-9_-]*PRIVATE KEY-----)",
    re.IGNORECASE,
)

FORBIDDEN_PARAM_PATTERN = re.compile(
    r"^(pass(word)?|cookie|auth(orization)?|token|secret|api_?key|session|csrf|sig(nature)?|jwt|credential|access_token|refresh_token)$",
    re.IGNORECASE,
)

FORBIDDEN_FIELD_PATTERN = re.compile(
    r"(pass(word)?|passwd|auth_token|access_token|refresh_token|api_?key|session_token|session_id|cookie|csrf|credential|otp|secret_key|bearer_token)",
    re.IGNORECASE,
)


def assert_safe_channel_metadata(metadata: dict[str, Any]) -> None:
    """Validate that channel metadata conforms to strict technical allowlist and does not contain secrets."""
    def _inspect_val(v: Any, path: str) -> None:
        if isinstance(v, str):
            if FORBIDDEN_SECRET_VALUE_PATTERN.search(v):
                raise DomainValidationError(
                    f"Secret or token pattern detected in channel_metadata value for '{path}'."
                )
        elif isinstance(v, dict):
            for sub_k, sub_v in v.items():
                if FORBIDDEN_METADATA_PATTERN.search(str(sub_k)):
                    raise DomainValidationError(
                        f"Forbidden key '{sub_k}' in channel_metadata at '{path}'."
                    )
                _inspect_val(sub_v, f"{path}.{sub_k}")
        elif isinstance(v, (list, tuple, set)):
            for idx, item in enumerate(v):
                _inspect_val(item, f"{path}[{idx}]")

    for key, val in metadata.items():
        if key not in ALLOWED_CHANNEL_METADATA_KEYS:
            raise DomainValidationError(
                f"Key '{key}' is not in allowed channel_metadata keys ({sorted(ALLOWED_CHANNEL_METADATA_KEYS)})."
            )
        if FORBIDDEN_METADATA_PATTERN.search(key):
            raise DomainValidationError(
                f"Secret or credential key '{key}' is forbidden in channel_metadata."
            )
        _inspect_val(val, key)


def assert_safe_destination(
    canonical_url: str,
    destination_type: str,
    channel_metadata: dict[str, Any] | None = None,
    is_exact_destination: bool = True,
) -> tuple[str, str]:
    """Validate destination security boundaries and return (normalized_origin, normalized_hostname).

    Security Rules (Clarification 4 & Hardening 5/6):
    - Rejects embedded username/password credentials.
    - Rejects dangerous non-web schemes (javascript:, data:, file:, etc.).
    - For ATS_FORM and DIRECT_PORTAL, strictly requires HTTPS.
    - Rejects credentials and secrets in channel_metadata and URL query/fragment parameters.
    - Preserves exact path and query components.
    - For non-exact destinations, empty canonical_url returns ("", "").
    - Computes and returns canonical (origin, hostname).
    """
    if channel_metadata:
        assert_safe_channel_metadata(channel_metadata)

    clean_url = canonical_url.strip() if canonical_url else ""
    if not clean_url:
        if not is_exact_destination:
            return "", ""
        raise DomainValidationError("Destination canonical_url cannot be empty for exact destination.")

    parsed = urllib.parse.urlparse(clean_url)

    scheme = (parsed.scheme or "").lower()
    if scheme in ("javascript", "data", "file", "vbscript", "about", "blob"):
        raise DomainValidationError(f"Forbidden or dangerous destination URL scheme '{scheme}'.")

    dest_val = destination_type.value if hasattr(destination_type, "value") else str(destination_type)
    if dest_val == ExecutionDestinationType.EMAIL.value:
        # Check for control characters or CRLF injection
        if any(c in clean_url for c in ("\r", "\n", "\t", "\x00")):
            raise DomainValidationError("Destination email contains forbidden control or CRLF characters.")
        # Reject dangerous schemes
        if scheme and scheme not in ("mailto", ""):
            raise DomainValidationError(f"Invalid scheme '{scheme}' for email destination. Must be 'mailto:' or omit scheme.")

        # Check if URL contains query parameters or fragments
        if parsed.query or parsed.fragment:
            raise DomainValidationError("Email destination must not contain URL query parameters or fragments.")

        raw_addr = clean_url
        if raw_addr.lower().startswith("mailto:"):
            raw_addr = raw_addr[7:].strip()

        # Reject multiple addresses, brackets, or delimiters
        if any(sep in raw_addr for sep in (",", ";", " ", "<", ">", '"')):
            raise DomainValidationError("Email destination must specify exactly one valid email address.")

        email_re = re.compile(r"^[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+$")
        if not email_re.match(raw_addr):
            raise DomainValidationError(f"Invalid email address '{raw_addr}' for email destination.")

        domain = raw_addr.split("@", 1)[1].lower()
        if not domain:
            raise DomainValidationError("Email destination must include a valid domain.")

        origin = f"mailto:{raw_addr.lower()}"
        hostname = domain
        return origin, hostname

    if dest_val in (ExecutionDestinationType.ATS_FORM.value, ExecutionDestinationType.DIRECT_PORTAL.value):
        if scheme != "https":
            raise DomainValidationError(
                f"Destination URL must use HTTPS scheme for destination type '{dest_val}' (got '{scheme}')."
            )

    if parsed.username or parsed.password:
        raise DomainValidationError("Destination URL must not contain embedded username or password credentials.")

    hostname = (parsed.hostname or "").lower()
    if not hostname:
        raise DomainValidationError("Destination URL must include a valid hostname.")

    # URL parameter and fragment secret inspection (Hardening Point 6)
    query_params = urllib.parse.parse_qsl(parsed.query, keep_blank_values=True)
    fragment_params = urllib.parse.parse_qsl(parsed.fragment, keep_blank_values=True)
    for p_name, p_val in query_params + fragment_params:
        if FORBIDDEN_PARAM_PATTERN.match(p_name):
            raise DomainValidationError(
                f"Secret-bearing parameter '{p_name}' detected in destination URL query/fragment."
            )
        if FORBIDDEN_SECRET_VALUE_PATTERN.search(p_val) or FORBIDDEN_SECRET_VALUE_PATTERN.search(p_name):
            raise DomainValidationError(
                f"Secret or credential value detected in destination URL parameter '{p_name}'."
            )

    origin = f"{scheme}://{parsed.netloc}".lower()
    return origin, hostname


def validate_and_normalize_field_value(tf: TargetSchemaField, raw_value: Any) -> tuple[bool, Any, str]:
    """Validate and normalize a ContextFact value to match target field semantics (Clarification 5).

    Rules:
    - select: exact match to declared allowed_options (or case-insensitive exact single match).
      If allowed_options are yes/no and value is boolean-equivalent, maps canonically.
      Ambiguous free text is never guessed.
    - boolean: deterministic boolean (bool, or true/false/yes/no/1/0). Ambiguous text rejected.
    - date: canonical YYYY-MM-DD. Unstructured text rejected.
    - email: RFC-valid email syntax.
    - phone: valid standard phone syntax (>= 7 digits, no alphabetic characters).
    - url: valid standard HTTP/HTTPS syntax.
    - number: valid int or float.
    - text: string representation.
    """
    ftype = (tf.field_type or TargetFieldType.TEXT.value).strip().lower()

    if ftype == TargetFieldType.EMAIL.value:
        val = str(raw_value).strip()
        email_re = re.compile(r"^[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+$")
        if email_re.match(val):
            return True, val, ""
        return False, None, f"Value '{raw_value}' is not a valid email address."

    if ftype == TargetFieldType.PHONE.value:
        val = str(raw_value).strip()
        if any(c.isalpha() for c in val):
            return False, None, f"Phone value '{raw_value}' contains forbidden alphabetic characters."
        digit_count = sum(c.isdigit() for c in val)
        phone_re = re.compile(r"^\+?[0-9\s\-\(\)\.]+$")
        if digit_count >= 7 and phone_re.match(val):
            return True, val, ""
        return False, None, f"Phone value '{raw_value}' does not match standard phone format (at least 7 digits required)."

    if ftype == TargetFieldType.URL.value:
        val = str(raw_value).strip()
        parsed = urllib.parse.urlparse(val)
        if parsed.scheme in ("http", "https") and parsed.netloc:
            return True, val, ""
        return False, None, f"Value '{raw_value}' is not a valid HTTP/HTTPS URL."

    if ftype == TargetFieldType.SELECT.value:
        allowed = tf.allowed_options or []
        if not allowed:
            return False, None, "Target select field has no declared allowed_options."
        val = str(raw_value).strip()
        # Direct exact match
        if val in allowed:
            return True, val, ""
        # Case-insensitive single match
        ci_matches = [opt for opt in allowed if opt.lower() == val.lower()]
        if len(ci_matches) == 1:
            return True, ci_matches[0], ""
        # Deterministic yes/no boolean equivalence
        allowed_lower_set = {opt.lower() for opt in allowed}
        if allowed_lower_set == {"yes", "no"}:
            if val.lower() in ("true", "1", "yes"):
                canonical = [opt for opt in allowed if opt.lower() == "yes"][0]
                return True, canonical, ""
            if val.lower() in ("false", "0", "no"):
                canonical = [opt for opt in allowed if opt.lower() == "no"][0]
                return True, canonical, ""
        # Free-text or unresolvable option -> do not guess
        return False, None, f"Value '{raw_value}' does not match any declared option in {allowed}."

    if ftype == TargetFieldType.BOOLEAN.value:
        if isinstance(raw_value, bool):
            return True, raw_value, ""
        val = str(raw_value).strip().lower()
        if val in ("true", "yes", "1"):
            return True, True, ""
        if val in ("false", "no", "0"):
            return True, False, ""
        return False, None, f"Value '{raw_value}' cannot be deterministically converted to boolean."

    if ftype == TargetFieldType.DATE.value:
        val = str(raw_value).strip()
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", val):
            try:
                date.fromisoformat(val)
                return True, val, ""
            except (ValueError, TypeError):
                pass
        return False, None, f"Date value '{raw_value}' does not conform to canonical format YYYY-MM-DD."

    if ftype == TargetFieldType.NUMBER.value:
        try:
            if isinstance(raw_value, (int, float)):
                return True, raw_value, ""
            s = str(raw_value).strip()
            if "." in s:
                return True, float(s), ""
            return True, int(s), ""
        except ValueError:
            return False, None, f"Value '{raw_value}' is not a valid number."

    return True, str(raw_value).strip(), ""


@dataclass(frozen=True)
class TargetFieldBinding:
    """Explicit binding of an external target field ID to an internal ContextFact fact_id."""

    target_field_id: str
    source_fact_id: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "target_field_id": self.target_field_id,
            "source_fact_id": self.source_fact_id,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> TargetFieldBinding:
        return cls(
            target_field_id=data["target_field_id"],
            source_fact_id=data["source_fact_id"],
        )


@dataclass(frozen=True)
class TargetSchemaField:
    """Declared field specification from a target interaction schema."""

    target_field_id: str
    label: str
    field_type: str = TargetFieldType.TEXT.value
    required: bool = True
    allowed_options: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        ftype = (self.field_type or TargetFieldType.TEXT.value).strip().lower()
        if ftype not in TargetFieldType._value2member_map_:
            raise DomainValidationError(
                f"Unsupported field_type '{self.field_type}'. Must be one of {[e.value for e in TargetFieldType]}."
            )
        if ftype != self.field_type:
            object.__setattr__(self, "field_type", ftype)

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
            field_type=data.get("field_type", TargetFieldType.TEXT.value),
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
    answer_type: str = TargetAnswerType.TEXT.value
    allowed_options: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        atype = (self.answer_type or TargetAnswerType.TEXT.value).strip().lower()
        if atype not in TargetAnswerType._value2member_map_:
            raise DomainValidationError(
                f"Unsupported answer_type '{self.answer_type}'. Must be one of {[e.value for e in TargetAnswerType]}."
            )
        if atype != self.answer_type:
            object.__setattr__(self, "answer_type", atype)

    def to_dict(self) -> dict[str, Any]:
        return {
            "question_id": self.question_id,
            "question_text": self.question_text,
            "required": self.required,
            "answer_type": self.answer_type,
            "allowed_options": list(self.allowed_options),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> TargetSchemaQuestion:
        return cls(
            question_id=data["question_id"],
            question_text=data["question_text"],
            required=bool(data.get("required", True)),
            answer_type=data.get("answer_type", TargetAnswerType.TEXT.value),
            allowed_options=list(data.get("allowed_options", [])),
        )


@dataclass(frozen=True)
class TargetSubmitControlSpec:
    """Specification of the final submission control on the target destination."""

    control_type: str = ""
    semantic_target: str = ""
    selector: str = ""
    control_id: str = ""
    method: str = ""
    control_provenance: str = TargetSchemaProvenance.USER_PROVIDED.value
    requires_double_confirmation: bool = True

    def __post_init__(self) -> None:
        raw_prov = (self.control_provenance or TargetSchemaProvenance.USER_PROVIDED.value).strip().lower()
        if raw_prov not in TargetSchemaProvenance._value2member_map_:
            raise DomainValidationError(
                f"Unsupported control_provenance '{self.control_provenance}'. Must be one of {[e.value for e in TargetSchemaProvenance]}."
            )
        if raw_prov != self.control_provenance:
            object.__setattr__(self, "control_provenance", raw_prov)

    def to_dict(self) -> dict[str, Any]:
        return {
            "control_type": self.control_type,
            "semantic_target": self.semantic_target,
            "selector": self.selector,
            "control_id": self.control_id,
            "method": self.method,
            "control_provenance": self.control_provenance,
            "requires_double_confirmation": self.requires_double_confirmation,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> TargetSubmitControlSpec:
        return cls(
            control_type=data.get("control_type", ""),
            semantic_target=data.get("semantic_target", ""),
            selector=data.get("selector", ""),
            control_id=data.get("control_id", ""),
            method=data.get("method", ""),
            control_provenance=data.get("control_provenance", TargetSchemaProvenance.USER_PROVIDED.value),
            requires_double_confirmation=bool(data.get("requires_double_confirmation", True)),
        )


@dataclass(frozen=True)
class TargetInteractionSchema:
    """Authoritative interaction schema declaring target fields, upload slots, questions, and submit control."""

    schema_id: str
    destination_type: str
    target_schema_provenance: str = TargetSchemaProvenance.USER_PROVIDED.value
    fields: list[TargetSchemaField] = field(default_factory=list)
    upload_slots: list[TargetSchemaUploadSlot] = field(default_factory=list)
    screening_questions: list[TargetSchemaQuestion] = field(default_factory=list)
    final_submit_control: TargetSubmitControlSpec | None = None
    schema_hash: str = ""

    def __post_init__(self) -> None:
        if not self.destination_type or not self.destination_type.strip():
            raise DomainValidationError("TargetInteractionSchema destination_type cannot be empty.")
        raw_prov = (self.target_schema_provenance or TargetSchemaProvenance.USER_PROVIDED.value).strip().lower()
        if raw_prov not in TargetSchemaProvenance._value2member_map_:
            raise DomainValidationError(
                f"Unsupported target_schema_provenance '{self.target_schema_provenance}'. Must be one of {[e.value for e in TargetSchemaProvenance]}."
            )
        if raw_prov != self.target_schema_provenance:
            object.__setattr__(self, "target_schema_provenance", raw_prov)

        f_ids = [f.target_field_id for f in self.fields]
        if len(f_ids) != len(set(f_ids)):
            raise DomainValidationError("Duplicate target_field_id found in TargetInteractionSchema fields.")
        s_ids = [s.slot_id for s in self.upload_slots]
        if len(s_ids) != len(set(s_ids)):
            raise DomainValidationError("Duplicate slot_id found in TargetInteractionSchema upload_slots.")
        q_ids = [q.question_id for q in self.screening_questions]
        if len(q_ids) != len(set(q_ids)):
            raise DomainValidationError("Duplicate question_id found in TargetInteractionSchema screening_questions.")

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
            [q.question_id, q.question_text, q.required, q.answer_type, sorted(q.allowed_options)]
            for q in sorted(self.screening_questions, key=lambda q: q.question_id)
        ]
        canonical_control = self.final_submit_control.to_dict() if self.final_submit_control else {}
        payload = [
            self.schema_id,
            self.destination_type,
            self.target_schema_provenance,
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
            "target_schema_provenance": self.target_schema_provenance,
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
        if "destination_type" not in data or not str(data["destination_type"]).strip():
            raise DomainValidationError("TargetInteractionSchema destination_type is required.")
        return cls(
            schema_id=data["schema_id"],
            destination_type=data["destination_type"],
            target_schema_provenance=data.get("target_schema_provenance", TargetSchemaProvenance.USER_PROVIDED.value),
            fields=[TargetSchemaField.from_dict(f) for f in data.get("fields", [])],
            upload_slots=[TargetSchemaUploadSlot.from_dict(s) for s in data.get("upload_slots", [])],
            screening_questions=[TargetSchemaQuestion.from_dict(q) for q in data.get("screening_questions", [])],
            final_submit_control=control,
            schema_hash=data.get("schema_hash", ""),
        )


@dataclass(frozen=True)
class ExecutionDestination:
    """Exact destination specification with explicit provenance and security boundaries."""

    destination_type: str
    destination_provenance: str
    canonical_url: str
    organization: str
    channel_metadata: dict[str, Any] = field(default_factory=dict)
    is_exact_destination: bool = True
    normalized_origin: str = ""
    normalized_hostname: str = ""

    def __post_init__(self) -> None:
        raw_prov = (self.destination_provenance or DestinationProvenance.USER_PROVIDED.value).strip().lower()
        if raw_prov not in DestinationProvenance._value2member_map_:
            raise DomainValidationError(
                f"Unsupported destination_provenance '{self.destination_provenance}'. Must be one of {[e.value for e in DestinationProvenance]}."
            )
        if raw_prov != self.destination_provenance:
            object.__setattr__(self, "destination_provenance", raw_prov)

        origin, hostname = assert_safe_destination(
            canonical_url=self.canonical_url,
            destination_type=self.destination_type,
            channel_metadata=self.channel_metadata,
            is_exact_destination=self.is_exact_destination,
        )
        if not self.normalized_origin:
            object.__setattr__(self, "normalized_origin", origin)
        if not self.normalized_hostname:
            object.__setattr__(self, "normalized_hostname", hostname)

    def to_dict(self) -> dict[str, Any]:
        return {
            "destination_type": self.destination_type,
            "destination_provenance": self.destination_provenance,
            "canonical_url": self.canonical_url,
            "organization": self.organization,
            "channel_metadata": dict(self.channel_metadata),
            "is_exact_destination": self.is_exact_destination,
            "normalized_origin": self.normalized_origin,
            "normalized_hostname": self.normalized_hostname,
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
            normalized_origin=data.get("normalized_origin", ""),
            normalized_hostname=data.get("normalized_hostname", ""),
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
    allowed_purposes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "target_field_id": self.target_field_id,
            "field_name": self.field_name,
            "field_value": self.field_value,
            "source_fact_id": self.source_fact_id,
            "source_fact_version": self.source_fact_version,
            "verification_status": self.verification_status,
            "required": self.required,
            "allowed_purposes": list(self.allowed_purposes),
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
            allowed_purposes=list(data.get("allowed_purposes", [])),
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
    content_bytes: bytes | None = None
    plan_id: str = ""
    case_id: str = ""
    user_id: str = ""
    created_at: datetime = field(default_factory=now_utc)

    def to_dict(self, include_bytes: bool = False) -> dict[str, Any]:
        data: dict[str, Any] = {
            "asset_id": self.asset_id,
            "role": self.role,
            "source_artifact_id": self.source_artifact_id,
            "source_artifact_version": self.source_artifact_version,
            "filename": self.filename,
            "mime_type": self.mime_type,
            "content_sha256": self.content_sha256,
            "byte_size": self.byte_size,
            "materialized": self.materialized,
            "plan_id": self.plan_id,
            "case_id": self.case_id,
            "user_id": self.user_id,
            "created_at": to_iso_utc(self.created_at),
        }
        if include_bytes and self.content_bytes is not None:
            data["content_bytes"] = self.content_bytes
        return data

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
            content_bytes=data.get("content_bytes"),
            plan_id=data.get("plan_id", ""),
            case_id=data.get("case_id", ""),
            user_id=data.get("user_id", ""),
            created_at=from_iso_utc(data["created_at"]) if data.get("created_at") else now_utc(),
        )


@dataclass(frozen=True)
class ExecutionScreeningResponse:
    """Exact screening answer response mapped from approved SCREENING_ANSWERS artifact."""

    question_id: str
    question: str
    answer: str
    source_artifact_id: str
    grounding_claim_ids: list[str] = field(default_factory=list)
    source_artifact_version: int = 1
    source_payload_hash: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "question_id": self.question_id,
            "question": self.question,
            "answer": self.answer,
            "source_artifact_id": self.source_artifact_id,
            "grounding_claim_ids": list(self.grounding_claim_ids),
            "source_artifact_version": self.source_artifact_version,
            "source_payload_hash": self.source_payload_hash,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ExecutionScreeningResponse:
        return cls(
            question_id=data["question_id"],
            question=data["question"],
            answer=data["answer"],
            source_artifact_id=data["source_artifact_id"],
            grounding_claim_ids=list(data.get("grounding_claim_ids", [])),
            source_artifact_version=int(data.get("source_artifact_version", 1)),
            source_payload_hash=data.get("source_payload_hash", ""),
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


@dataclass(frozen=True)
class EmailAttachmentManifest:
    """Exact specification of an email attachment bound to a package artifact."""

    filename: str
    mime_type: str
    content_sha256: str
    byte_size: int
    asset_id: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "filename": self.filename,
            "mime_type": self.mime_type,
            "content_sha256": self.content_sha256,
            "byte_size": self.byte_size,
            "asset_id": self.asset_id,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> EmailAttachmentManifest:
        return cls(
            filename=data["filename"],
            mime_type=data["mime_type"],
            content_sha256=data["content_sha256"],
            byte_size=int(data["byte_size"]),
            asset_id=data.get("asset_id", ""),
        )


@dataclass(frozen=True)
class EmailAcceptanceEvidence:
    """Trustworthy provenance connecting opportunity, employer, recipient, and application instructions."""

    evidence_source: str
    source_uri: str
    instructions_text: str
    verified_recipient_email: str
    employer_domain: str
    evidence_snapshot_hash: str = ""
    verified_by: str = ""
    verified_at: str = ""
    evidence_digest: str = ""

    def __post_init__(self) -> None:
        if not self.evidence_source or not self.evidence_source.strip():
            raise DomainValidationError("evidence_source cannot be empty.")
        if not self.source_uri or not self.source_uri.strip():
            raise DomainValidationError("source_uri cannot be empty.")
        if not self.instructions_text or not self.instructions_text.strip():
            raise DomainValidationError("instructions_text cannot be empty.")
        if not self.verified_recipient_email or not self.verified_recipient_email.strip():
            raise DomainValidationError("verified_recipient_email cannot be empty.")
        if not self.employer_domain or not self.employer_domain.strip():
            raise DomainValidationError("employer_domain cannot be empty.")
        if not self.verified_by or not self.verified_by.strip():
            raise DomainValidationError("verified_by cannot be empty.")
        if not self.verified_at or not self.verified_at.strip():
            raise DomainValidationError("verified_at cannot be empty.")

        expected_snapshot_hash = compute_sha256(self.instructions_text.strip())
        if self.evidence_snapshot_hash and self.evidence_snapshot_hash.lower() != expected_snapshot_hash.lower():
            raise DomainValidationError(
                f"evidence_snapshot_hash mismatch (expected {expected_snapshot_hash}, got {self.evidence_snapshot_hash})."
            )
        if not self.evidence_snapshot_hash:
            object.__setattr__(self, "evidence_snapshot_hash", expected_snapshot_hash)

        computed_digest = self.compute_digest()
        if self.evidence_digest and self.evidence_digest.lower() != computed_digest.lower():
            raise DomainValidationError(
                f"evidence_digest mismatch (expected {computed_digest}, got {self.evidence_digest})."
            )
        if not self.evidence_digest:
            object.__setattr__(self, "evidence_digest", computed_digest)

    def compute_digest(self) -> str:
        components = [
            self.evidence_source.strip().lower(),
            self.source_uri.strip(),
            self.instructions_text.strip(),
            self.verified_recipient_email.strip().lower(),
            self.employer_domain.strip().lower(),
            self.evidence_snapshot_hash.strip().lower(),
            self.verified_by.strip(),
            self.verified_at.strip(),
        ]
        return compute_sha256(canonical_json_dumps(components))

    def to_dict(self) -> dict[str, Any]:
        return {
            "evidence_source": self.evidence_source,
            "source_uri": self.source_uri,
            "instructions_text": self.instructions_text,
            "verified_recipient_email": self.verified_recipient_email,
            "employer_domain": self.employer_domain,
            "evidence_snapshot_hash": self.evidence_snapshot_hash,
            "verified_by": self.verified_by,
            "verified_at": self.verified_at,
            "evidence_digest": self.evidence_digest,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> EmailAcceptanceEvidence:
        return cls(
            evidence_source=data["evidence_source"],
            source_uri=data["source_uri"],
            instructions_text=data["instructions_text"],
            verified_recipient_email=data["verified_recipient_email"],
            employer_domain=data["employer_domain"],
            evidence_snapshot_hash=data.get("evidence_snapshot_hash", ""),
            verified_by=data.get("verified_by", ""),
            verified_at=data.get("verified_at", ""),
            evidence_digest=data.get("evidence_digest", ""),
        )


@dataclass(frozen=True)
class VerifiedEmailAcceptanceRecord:
    """Immutable persistent record of verified employer acceptance of email applications."""

    evidence_id: str
    opportunity_id: str
    user_id: str
    evidence_source: str
    source_uri: str
    instructions_text: str
    verified_recipient_email: str
    employer_domain: str
    snapshot_hash: str
    evidence_digest: str
    verified_by: str
    verified_at: str
    evidence_version: int = 1
    is_active: bool = True
    superseded_at: str | None = None

    def to_evidence(self) -> EmailAcceptanceEvidence:
        return EmailAcceptanceEvidence(
            evidence_source=self.evidence_source,
            source_uri=self.source_uri,
            instructions_text=self.instructions_text,
            verified_recipient_email=self.verified_recipient_email,
            employer_domain=self.employer_domain,
            evidence_snapshot_hash=self.snapshot_hash,
            verified_by=self.verified_by,
            verified_at=self.verified_at,
            evidence_digest=self.evidence_digest,
        )


@dataclass(frozen=True)
class EmailPayloadManifest:
    """Immutable, content-addressed email serialization manifest frozen before authorization."""

    to_email: str
    from_email: str
    subject: str
    message_id: str
    date_rfc2822: str
    body_text: str
    mime_boundary: str
    attachments: list[EmailAttachmentManifest] = field(default_factory=list)
    sealed_payload_hash: str = ""
    acceptance_evidence: EmailAcceptanceEvidence | None = None

    @property
    def date_header(self) -> str:
        return self.date_rfc2822


    def compute_manifest_hash(self) -> str:
        """Deterministic canonical hash of the manifest inputs."""
        canonical_attachments = [
            [a.filename, a.mime_type, a.content_sha256, a.byte_size, a.asset_id]
            for a in sorted(self.attachments, key=lambda a: (a.filename, a.content_sha256))
        ]
        payload = [
            self.to_email.strip().lower(),
            self.from_email.strip().lower(),
            self.subject.strip(),
            self.message_id.strip(),
            self.date_rfc2822.strip(),
            self.body_text.strip(),
            self.mime_boundary.strip(),
            canonical_attachments,
            self.acceptance_evidence.evidence_digest if self.acceptance_evidence else "",
        ]
        return compute_sha256(canonical_json_dumps(payload))

    def to_dict(self) -> dict[str, Any]:
        return {
            "to_email": self.to_email,
            "from_email": self.from_email,
            "subject": self.subject,
            "message_id": self.message_id,
            "date_rfc2822": self.date_rfc2822,
            "body_text": self.body_text,
            "mime_boundary": self.mime_boundary,
            "attachments": [a.to_dict() for a in self.attachments],
            "sealed_payload_hash": self.sealed_payload_hash,
            "acceptance_evidence": self.acceptance_evidence.to_dict() if self.acceptance_evidence else None,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> EmailPayloadManifest:
        ev_data = data.get("acceptance_evidence")
        return cls(
            to_email=data["to_email"],
            from_email=data["from_email"],
            subject=data["subject"],
            message_id=data["message_id"],
            date_rfc2822=data["date_rfc2822"],
            body_text=data["body_text"],
            mime_boundary=data["mime_boundary"],
            attachments=[EmailAttachmentManifest.from_dict(a) for a in data.get("attachments", [])],
            sealed_payload_hash=data.get("sealed_payload_hash", ""),
            acceptance_evidence=EmailAcceptanceEvidence.from_dict(ev_data) if ev_data else None,
        )


def compute_execution_input_hash(field_mappings: list[ExecutionFieldMapping]) -> str:
    """Compute deterministic dependency hash across all backing ContextFacts including purpose authorization state."""
    canonical_deps = [
        [m.source_fact_id, m.source_fact_version, str(m.verification_status), sorted(m.allowed_purposes)]
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
    email_manifest: EmailPayloadManifest | None = None,
) -> str:
    """Compute content-addressed SHA-256 fingerprint for an ExternalExecutionPlan.

    Invariant Rules:
    - Binds destination, target schema hash, fact versions, upload byte hashes, questions, and submit control.
    - Excludes plan_id, status, version, timestamps, and findings.
    - When email_manifest is provided, cryptographically binds exact email headers, body, and sealed_payload_hash.
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
            s.source_artifact_version,
            s.source_payload_hash,
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
    if email_manifest is not None:
        payload.append([
            email_manifest.compute_manifest_hash(),
            email_manifest.sealed_payload_hash,
        ])
    return compute_sha256(canonical_json_dumps(payload))


_VALID_PLAN_TRANSITIONS: dict[ExternalExecutionPlanStatus, set[ExternalExecutionPlanStatus]] = {
    ExternalExecutionPlanStatus.DRAFT: {
        ExternalExecutionPlanStatus.READY_FOR_AUTHORIZATION,
        ExternalExecutionPlanStatus.SUPERSEDED,
    },
    ExternalExecutionPlanStatus.READY_FOR_AUTHORIZATION: {
        ExternalExecutionPlanStatus.EXECUTED,
        ExternalExecutionPlanStatus.SUPERSEDED,
    },
    ExternalExecutionPlanStatus.EXECUTED: set(),
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
    target_schema_hash: str = ""
    destination_provenance: str = ""
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
    is_stale: bool = False
    staleness_reasons: list[str] = field(default_factory=list)
    email_manifest: EmailPayloadManifest | None = None

    @property
    def planning_findings(self) -> list[ExecutionPlanFinding]:
        return self.findings

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

        if not self.target_schema_hash and self.target_schema:
            self.target_schema_hash = self.target_schema.schema_hash
        if not self.destination_provenance and self.destination:
            self.destination_provenance = self.destination.destination_provenance

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
        target_schema_hash = self.target_schema_hash or (self.target_schema.schema_hash if self.target_schema else "")
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
            email_manifest=self.email_manifest,
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

    def mark_executed(self, reason: str = "External transmission executed successfully") -> None:
        if self.status == ExternalExecutionPlanStatus.EXECUTED:
            raise DomainValidationError("Execution plan has already been executed and cannot be re-executed.")
        self.transition_to(ExternalExecutionPlanStatus.EXECUTED, reason=reason)

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
            "destination_provenance": self.destination_provenance,
            "target_schema": self.target_schema.to_dict() if self.target_schema else None,
            "target_schema_hash": self.target_schema_hash,
            "field_mappings": [m.to_dict() for m in self.field_mappings],
            "upload_mappings": [u.to_dict() for u in self.upload_mappings],
            "screening_responses": [s.to_dict() for s in self.screening_responses],
            "final_submit_control": self.final_submit_control.to_dict() if self.final_submit_control else None,
            "findings": [f.to_dict() for f in self.findings],
            "planning_findings": [f.to_dict() for f in self.findings],
            "execution_input_hash": self.execution_input_hash,
            "execution_plan_fingerprint": self.execution_plan_fingerprint,
            "schema_version": self.schema_version,
            "planner_version": self.planner_version,
            "created_at": to_iso_utc(self.created_at),
            "updated_at": to_iso_utc(self.updated_at),
            "version": self.version,
            "is_stale": self.is_stale,
            "staleness_reasons": list(self.staleness_reasons),
            "email_manifest": self.email_manifest.to_dict() if self.email_manifest else None,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ExternalExecutionPlan:
        destination = ExecutionDestination.from_dict(data["destination"])
        schema = TargetInteractionSchema.from_dict(data["target_schema"]) if data.get("target_schema") else None
        control = TargetSubmitControlSpec.from_dict(data["final_submit_control"]) if data.get("final_submit_control") else None
        raw_findings = data.get("findings") or data.get("planning_findings") or []
        email_manifest = (
            EmailPayloadManifest.from_dict(data["email_manifest"])
            if data.get("email_manifest")
            else None
        )
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
            target_schema_hash=data.get("target_schema_hash", schema.schema_hash if schema else ""),
            destination_provenance=data.get("destination_provenance", destination.destination_provenance),
            field_mappings=[ExecutionFieldMapping.from_dict(m) for m in data.get("field_mappings", [])],
            upload_mappings=[ExecutionUploadAsset.from_dict(u) for u in data.get("upload_mappings", [])],
            screening_responses=[ExecutionScreeningResponse.from_dict(s) for s in data.get("screening_responses", [])],
            final_submit_control=control,
            findings=[ExecutionPlanFinding.from_dict(f) for f in raw_findings],
            execution_input_hash=data.get("execution_input_hash", ""),
            execution_plan_fingerprint=data.get("execution_plan_fingerprint", ""),
            schema_version=data.get("schema_version", EXEC_PLAN_SCHEMA_VERSION),
            planner_version=data.get("planner_version", EXEC_ACTION_PLANNER_VERSION),
            created_at=from_iso_utc(data["created_at"]),
            updated_at=from_iso_utc(data["updated_at"]),
            version=int(data.get("version", 1)),
            is_stale=bool(data.get("is_stale", False)),
            staleness_reasons=list(data.get("staleness_reasons", [])),
            email_manifest=email_manifest,
        )


def compute_execution_plan_staleness(
    plan: ExternalExecutionPlan,
    package: Any | None,
    current_facts: list[Any],
    artifacts: list[Any],
    claims: list[Any],
    upload_assets: list[ExecutionUploadAsset] | None = None,
) -> tuple[bool, list[str]]:
    """Deterministically evaluate plan staleness against live Case and Vault dependencies (Clarification 3).

    Checks:
    - Approved package fingerprint & status
    - Target schema hash
    - Destination HTTPS and exactness
    - ContextFact IDs, versions, verification status, and job_application purpose
    - Upload asset byte hashes
    - Screening source artifact version & grounding claim statuses
    - Planner and schema version
    """
    reasons: list[str] = []

    # 1. Approved package fingerprint / status
    if package is None:
        reasons.append("Approved preparation package not found.")
    else:
        pkg_status = package.status.value if hasattr(package.status, "value") else str(package.status)
        if pkg_status != PreparationPackageStatus.APPROVED.value:
            reasons.append(f"Preparation package is '{pkg_status}', expected APPROVED.")
        if package.package_fingerprint != plan.package_fingerprint:
            reasons.append("Preparation package fingerprint changed.")
        if package.package_id != plan.package_id:
            reasons.append("Preparation package ID mismatch.")

    # 2. Target schema hash
    if plan.target_schema is None:
        reasons.append("Target interaction schema is missing.")
    elif plan.target_schema.schema_hash != plan.target_schema_hash:
        reasons.append("Target interaction schema hash changed.")

    # 3. Destination validity
    if not plan.destination.is_exact_destination:
        reasons.append("Destination is not an exact application endpoint.")
    dest_type_val = plan.destination.destination_type.value if hasattr(plan.destination.destination_type, "value") else str(plan.destination.destination_type)
    if dest_type_val in (ExecutionDestinationType.ATS_FORM.value, ExecutionDestinationType.DIRECT_PORTAL.value):
        if not plan.destination.canonical_url.startswith("https://"):
            reasons.append("Destination URL does not use HTTPS.")
    elif dest_type_val == ExecutionDestinationType.EMAIL.value:
        if "@" not in plan.destination.canonical_url:
            reasons.append("Email destination does not contain a valid email address.")
        if plan.email_manifest is not None and not plan.email_manifest.sealed_payload_hash:
            reasons.append("Email payload manifest lacks sealed payload hash.")

    # 4. ContextFact ID / version / verification / purpose / value state (Correction 3 & 5)
    facts_by_id = {f.fact_id: f for f in current_facts}
    schema_fields_by_id = {
        f.target_field_id: f for f in (plan.target_schema.fields if plan.target_schema else [])
    }
    for m in plan.field_mappings:
        fact = facts_by_id.get(m.source_fact_id)
        if fact is None:
            reasons.append(f"ContextFact '{m.source_fact_id}' for field '{m.target_field_id}' no longer exists.")
        else:
            if fact.version != m.source_fact_version:
                reasons.append(f"ContextFact '{m.source_fact_id}' version changed ({fact.version} != {m.source_fact_version}).")
            v_status = fact.verification_status.value if hasattr(fact.verification_status, "value") else str(fact.verification_status)
            if v_status not in (VerificationStatus.USER_VERIFIED.value, VerificationStatus.SOURCE_VERIFIED.value):
                reasons.append(f"ContextFact '{m.source_fact_id}' is no longer verified ({v_status}).")
            if "job_application" not in (fact.allowed_purposes or []):
                reasons.append(f"ContextFact '{m.source_fact_id}' lacks purpose 'job_application'.")
            if getattr(fact, "superseded_by_fact_id", None) is not None:
                reasons.append(f"ContextFact '{m.source_fact_id}' is superseded.")
            # Check purpose authorization state
            if sorted(fact.allowed_purposes or []) != sorted(m.allowed_purposes):
                reasons.append(f"ContextFact '{m.source_fact_id}' allowed purposes changed.")
            # Rerun deterministic normalization and check value equality (Correction 3)
            tf = schema_fields_by_id.get(m.target_field_id)
            if tf is not None:
                is_valid, norm_val, _ = validate_and_normalize_field_value(tf, fact.value)
                if not is_valid or norm_val != m.field_value:
                    reasons.append(f"ContextFact '{m.source_fact_id}' for field '{m.target_field_id}' resolved value changed.")
            else:
                if fact.value != m.field_value:
                    reasons.append(f"ContextFact '{m.source_fact_id}' value changed.")

    # 5. Upload asset byte hashes and durable existence (Hardening Point 1, 2, 3)
    upload_assets_by_id = {u.asset_id: u for u in (upload_assets or [])}
    for u in plan.upload_mappings:
        if not u.materialized:
            reasons.append(f"Upload asset '{u.role}' is not materialized.")
        if u.byte_size <= 0:
            reasons.append(f"Upload asset '{u.role}' has empty byte payload.")
        if upload_assets is not None:
            stored_asset = upload_assets_by_id.get(u.asset_id)
            if stored_asset is None:
                reasons.append(f"Durable upload asset '{u.asset_id}' for slot '{u.role}' is missing from database.")
            else:
                if (
                    stored_asset.asset_id != u.asset_id
                    or stored_asset.plan_id != plan.plan_id
                    or stored_asset.case_id != plan.case_id
                    or stored_asset.user_id != plan.user_id
                    or stored_asset.source_artifact_id != u.source_artifact_id
                    or stored_asset.source_artifact_version != u.source_artifact_version
                    or stored_asset.role != u.role
                    or stored_asset.filename != u.filename
                    or stored_asset.mime_type != u.mime_type
                    or stored_asset.byte_size != u.byte_size
                    or stored_asset.content_sha256 != u.content_sha256
                ):
                    reasons.append(f"Upload asset '{u.role}' ownership or metadata mismatch in database.")
                actual_bytes = stored_asset.content_bytes or b""
                actual_hash = compute_sha256_bytes(actual_bytes)
                if actual_hash != u.content_sha256 or len(actual_bytes) != u.byte_size:
                    reasons.append(f"Upload asset '{u.role}' content bytes or byte hash mismatch.")

    # 6. Screening source artifact version, rendered hash & grounding claims (Hardening Point 7, 8)
    artifacts_by_id = {a.artifact_id: a for a in artifacts}
    claims_by_id = {c.claim_id: c for c in claims}
    for s in plan.screening_responses:
        art = artifacts_by_id.get(s.source_artifact_id)
        if art is None:
            reasons.append(f"Screening artifact '{s.source_artifact_id}' no longer exists.")
        else:
            if art.version != s.source_artifact_version:
                reasons.append(f"Screening artifact '{s.source_artifact_id}' version changed ({art.version} != {s.source_artifact_version}).")
            if s.source_payload_hash and art.compute_rendered_payload_hash() != s.source_payload_hash:
                reasons.append(f"Screening artifact '{s.source_artifact_id}' rendered payload hash changed.")
        for cid in s.grounding_claim_ids:
            claim = claims_by_id.get(cid)
            c_status = claim.status.value if hasattr(claim.status, "value") else str(claim.status) if claim else ""
            if claim is None or c_status != ClaimStatus.SUPPORTED.value:
                reasons.append(f"Screening grounding claim '{cid}' is not SUPPORTED.")

    # 7. Planner and schema version
    if plan.schema_version != EXEC_PLAN_SCHEMA_VERSION:
        reasons.append("Plan schema version mismatch.")
    if plan.planner_version != EXEC_ACTION_PLANNER_VERSION:
        reasons.append("Plan compiler version mismatch.")

    return (len(reasons) > 0, reasons)
