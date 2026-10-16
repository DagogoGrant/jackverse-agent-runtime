"""Execution plan compiler synthesizing deterministic, content-addressed external execution plans."""

from __future__ import annotations

from datetime import datetime
import re
from typing import Any
import uuid

from caseworker.domain.artifact import PreparedArtifact
from caseworker.domain.case import Case
from caseworker.domain.claim import Claim
from caseworker.domain.context import ContextFact
from caseworker.domain.enums import (
    DestinationProvenance,
    ExecutionDestinationType,
    ExternalExecutionPlanStatus,
    PreparationMaterialType,
    VerificationStatus,
)
from caseworker.domain.errors import (
    DomainValidationError,
    EntityNotFoundError,
    PackageVerificationError,
)
from caseworker.domain.execution_plan import (
    EXEC_ACTION_PLANNER_VERSION,
    EXEC_PLAN_SCHEMA_VERSION,
    ExecutionDestination,
    ExecutionFieldMapping,
    ExecutionPlanFinding,
    ExecutionScreeningResponse,
    ExecutionUploadAsset,
    ExternalExecutionPlan,
    TargetInteractionSchema,
    TargetSchemaField,
    TargetSubmitControlSpec,
    assert_safe_channel_metadata,
    assert_safe_destination,
    compute_execution_input_hash,
)
from caseworker.domain.opportunity import Opportunity
from caseworker.domain.package import PreparationPackage
from caseworker.domain.types import compute_sha256


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
    - number: valid int or float.
    - text: string representation.
    """
    ftype = (tf.field_type or "text").strip().lower()

    if ftype == "email":
        val = str(raw_value).strip()
        email_re = re.compile(r"^[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+$")
        if email_re.match(val):
            return True, val, ""
        return False, None, f"Value '{raw_value}' is not a valid email address."

    if ftype == "phone":
        val = str(raw_value).strip()
        if any(c.isalpha() for c in val):
            return False, None, f"Phone value '{raw_value}' contains forbidden alphabetic characters."
        digit_count = sum(c.isdigit() for c in val)
        phone_re = re.compile(r"^\+?[0-9\s\-\(\)\.]+$")
        if digit_count >= 7 and phone_re.match(val):
            return True, val, ""
        return False, None, f"Phone value '{raw_value}' does not match standard phone format (at least 7 digits required)."

    if ftype == "select":
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

    if ftype == "boolean":
        if isinstance(raw_value, bool):
            return True, raw_value, ""
        val = str(raw_value).strip().lower()
        if val in ("true", "yes", "1"):
            return True, True, ""
        if val in ("false", "no", "0"):
            return True, False, ""
        return False, None, f"Value '{raw_value}' cannot be deterministically converted to boolean."

    if ftype == "date":
        val = str(raw_value).strip()
        date_re = re.compile(r"^\d{4}-\d{2}-\d{2}$")
        if date_re.match(val):
            return True, val, ""
        return False, None, f"Date value '{raw_value}' does not conform to canonical format YYYY-MM-DD."

    if ftype == "number":
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


class ExecutionPlanCompiler:
    """Compiler synthesizing an ExternalExecutionPlan under strict cryptographic and semantic constraints."""

    SCHEMA_VERSION = EXEC_PLAN_SCHEMA_VERSION
    PLANNER_VERSION = EXEC_ACTION_PLANNER_VERSION

    @classmethod
    def compile_plan(
        cls,
        case: Case,
        opportunity: Opportunity,
        package: PreparationPackage,
        artifacts: list[PreparedArtifact],
        context_facts: list[ContextFact] | None = None,
        destination_override: str | ExecutionDestination | None = None,
        destination_provenance: DestinationProvenance | str = DestinationProvenance.USER_PROVIDED,
        target_schema: TargetInteractionSchema | None = None,
        channel_metadata: dict[str, Any] | None = None,
        claims: list[Claim] | None = None,
        facts: list[ContextFact] | None = None,
        final_submit_control: TargetSubmitControlSpec | None = None,
    ) -> ExternalExecutionPlan:
        """Compile an ExternalExecutionPlan deterministically.

        Constitutional Invariant:
        - Planning does NOT equal approval, transmission, or execution authority.
        - Zero outbound network transmission is executed here.
        """
        findings: list[ExecutionPlanFinding] = []
        metadata = dict(channel_metadata or {})
        active_facts_list = facts if facts is not None else (context_facts or [])

        # 1. Destination Resolution with Security Gating (Clarification 4)
        resolved_provenance = (
            destination_provenance.value
            if hasattr(destination_provenance, "value")
            else str(destination_provenance)
        )
        dest_type = ExecutionDestinationType.ATS_FORM.value
        dest_org = opportunity.organization or "Target Organization"

        if destination_override:
            if isinstance(destination_override, ExecutionDestination):
                canonical_url = destination_override.canonical_url.strip()
                resolved_provenance = destination_override.destination_provenance
                is_exact_dest = destination_override.is_exact_destination
                dest_type = destination_override.destination_type
                dest_org = destination_override.organization or dest_org
            else:
                canonical_url = str(destination_override).strip()
                is_exact_dest = True
            findings.append(
                ExecutionPlanFinding(
                    check_name="destination_resolution",
                    passed=True,
                    severity="info",
                    code="EXACT_DESTINATION_SPECIFIED",
                    message=f"Application endpoint specified via provenance '{resolved_provenance}'.",
                    details={"url": canonical_url},
                )
            )
        elif opportunity.metadata.get("apply_url") or opportunity.metadata.get("application_url"):
            canonical_url = str(opportunity.metadata.get("apply_url") or opportunity.metadata.get("application_url")).strip()
            resolved_provenance = DestinationProvenance.OPPORTUNITY_APPLICATION_URL.value
            is_exact_dest = True
            findings.append(
                ExecutionPlanFinding(
                    check_name="destination_resolution",
                    passed=True,
                    severity="info",
                    code="OPPORTUNITY_APPLY_URL_RESOLVED",
                    message="Application endpoint resolved from opportunity application metadata.",
                    details={"url": canonical_url},
                )
            )
        else:
            # Listing URL alone is not an application endpoint
            canonical_url = opportunity.source_url.strip() if opportunity.source_url else ""
            is_exact_dest = False
            findings.append(
                ExecutionPlanFinding(
                    check_name="destination_resolution",
                    passed=False,
                    severity="error",
                    code="DESTINATION_REQUIRED",
                    message="Exact application destination is unknown. General listing URL cannot be assumed to be an application form.",
                    details={"source_url": canonical_url},
                )
            )

        destination = ExecutionDestination(
            destination_type=dest_type,
            destination_provenance=resolved_provenance,
            canonical_url=canonical_url,
            organization=dest_org,
            channel_metadata=metadata,
            is_exact_destination=is_exact_dest,
        )

        # 2. Check Target Interaction Schema
        if target_schema is None:
            findings.append(
                ExecutionPlanFinding(
                    check_name="target_schema_verification",
                    passed=False,
                    severity="error",
                    code="TARGET_SCHEMA_REQUIRED",
                    message="Target interaction schema is absent. Without a declared interaction schema, exact form fields and controls cannot be resolved.",
                )
            )

        # 3. Field Mappings from Verified ContextFacts with Type Semantic Validation (Clarification 5)
        field_mappings: list[ExecutionFieldMapping] = []
        active_facts_by_key: dict[str, list[ContextFact]] = {}
        for f in active_facts_list:
            if getattr(f, "superseded_by_fact_id", None) is not None:
                continue
            active_facts_by_key.setdefault(f.key, []).append(f)

        if target_schema is not None:
            for tf in target_schema.fields:
                matching_facts = active_facts_by_key.get(tf.target_field_id, [])
                if not matching_facts:
                    alias_key = tf.target_field_id.replace("-", "_")
                    matching_facts = active_facts_by_key.get(alias_key, [])

                # Filter by verification status & allowed purpose
                valid_fact = None
                invalid_reason = ""
                for fact in matching_facts:
                    v_status = fact.verification_status.value if hasattr(fact.verification_status, "value") else str(fact.verification_status)
                    if v_status not in (VerificationStatus.USER_VERIFIED.value, VerificationStatus.SOURCE_VERIFIED.value):
                        invalid_reason = f"Fact is '{v_status}', requires USER_VERIFIED or SOURCE_VERIFIED"
                        continue
                    if "job_application" not in (fact.allowed_purposes or []):
                        invalid_reason = "Fact lacks purpose 'job_application'"
                        continue
                    valid_fact = fact
                    break

                if valid_fact is not None:
                    is_valid, norm_val, err_msg = validate_and_normalize_field_value(tf, valid_fact.value)
                    if is_valid:
                        field_mappings.append(
                            ExecutionFieldMapping(
                                target_field_id=tf.target_field_id,
                                field_name=tf.label,
                                field_value=norm_val,
                                source_fact_id=valid_fact.fact_id,
                                source_fact_version=valid_fact.version,
                                verification_status=valid_fact.verification_status.value if hasattr(valid_fact.verification_status, "value") else str(valid_fact.verification_status),
                                required=tf.required,
                            )
                        )
                    else:
                        if tf.required:
                            findings.append(
                                ExecutionPlanFinding(
                                    check_name="field_mapping_resolution",
                                    passed=False,
                                    severity="error",
                                    code="INVALID_FIELD_TYPE_CONVERSION",
                                    message=f"Required target field '{tf.target_field_id}' (type '{tf.field_type}') failed semantic validation: {err_msg}",
                                    details={"target_field_id": tf.target_field_id, "field_type": tf.field_type, "error": err_msg},
                                )
                            )
                else:
                    if tf.required:
                        findings.append(
                            ExecutionPlanFinding(
                                check_name="field_mapping_resolution",
                                passed=False,
                                severity="error",
                                code="UNRESOLVED_REQUIRED_FIELD",
                                message=f"Required target field '{tf.target_field_id}' could not be resolved from verified ContextFacts ({invalid_reason or 'No matching fact found'}).",
                                details={"target_field_id": tf.target_field_id, "label": tf.label},
                            )
                        )

        # 4. Upload Mappings & Byte Asset Materialization (Clarification 2)
        upload_mappings: list[ExecutionUploadAsset] = []
        artifacts_by_type: dict[str, PreparedArtifact] = {}
        for art in artifacts:
            m_type = art.material_type.value if hasattr(art.material_type, "value") else str(art.material_type)
            artifacts_by_type[m_type] = art

        if target_schema is not None:
            for slot in target_schema.upload_slots:
                slot_role = (slot.role or "").strip().lower()
                if slot_role in ("cv", "resume"):
                    matching_mat_type = PreparationMaterialType.CV.value
                elif slot_role in ("cover_letter", "cover-letter", "coverletter"):
                    matching_mat_type = PreparationMaterialType.COVER_LETTER.value
                else:
                    matching_mat_type = slot.role
                art = artifacts_by_type.get(matching_mat_type)
                if art is None or art.artifact_id not in package.artifact_ids:
                    if slot.required:
                        findings.append(
                            ExecutionPlanFinding(
                                check_name="upload_slot_resolution",
                                passed=False,
                                severity="error",
                                code="MISSING_UPLOAD_ARTIFACT",
                                message=f"Required upload slot '{slot.slot_id}' (role '{slot.role}') has no corresponding approved artifact in package.",
                                details={"slot_id": slot.slot_id, "role": slot.role},
                            )
                        )
                    continue

                # Materialize raw export byte asset
                rendered_text = art.compute_rendered_payload()
                raw_bytes = rendered_text.encode("utf-8")
                byte_sha256 = compute_sha256(raw_bytes.decode("utf-8", errors="replace"))

                # Check acceptable mime type
                mime_type = "text/plain"
                ext = "txt"
                if "text/plain" in slot.accepted_mime_types:
                    mime_type = "text/plain"
                    ext = "txt"
                elif "application/pdf" in slot.accepted_mime_types and len(slot.accepted_mime_types) == 1:
                    # Slot strictly requires PDF bytes, but we only have text payload in this environment
                    deterministic_asset_id = f"asset-{compute_sha256(f'{case.case_id}:{art.artifact_id}:{slot.role}')[:12]}"
                    upload_mappings.append(
                        ExecutionUploadAsset(
                            asset_id=deterministic_asset_id,
                            role=slot.role,
                            source_artifact_id=art.artifact_id,
                            source_artifact_version=art.version,
                            filename=f"{slot.role}_{case.user_id}.pdf",
                            mime_type="application/pdf",
                            content_sha256="",
                            byte_size=0,
                            materialized=False,
                            content_bytes=b"",
                            case_id=case.case_id,
                            user_id=case.user_id,
                        )
                    )
                    findings.append(
                        ExecutionPlanFinding(
                            check_name="upload_slot_resolution",
                            passed=False,
                            severity="error",
                            code="UNMATERIALIZED_UPLOAD",
                            message=f"Upload slot '{slot.slot_id}' requires application/pdf bytes, but binary PDF asset has not been compiled.",
                            details={"slot_id": slot.slot_id, "role": slot.role},
                        )
                    )
                    continue

                clean_user = re.sub(r"[^a-zA-Z0-9_-]", "_", case.user_id)
                deterministic_asset_id = f"asset-{compute_sha256(f'{case.case_id}:{art.artifact_id}:{slot.role}')[:12]}"
                upload_mappings.append(
                    ExecutionUploadAsset(
                        asset_id=deterministic_asset_id,
                        role=slot.role,
                        source_artifact_id=art.artifact_id,
                        source_artifact_version=art.version,
                        filename=f"{slot.role}_{clean_user}.{ext}",
                        mime_type=mime_type,
                        content_sha256=byte_sha256,
                        byte_size=len(raw_bytes),
                        materialized=True,
                        content_bytes=raw_bytes,
                        case_id=case.case_id,
                        user_id=case.user_id,
                    )
                )

        # 5. Screening Responses (SCREENING_ANSWERS only; ANSWER_BANK strictly prohibited)
        screening_responses: list[ExecutionScreeningResponse] = []
        screening_artifact = artifacts_by_type.get(PreparationMaterialType.SCREENING_ANSWERS.value)
        answer_bank_artifact = artifacts_by_type.get(PreparationMaterialType.APPLICATION_ANSWER_BANK.value) or artifacts_by_type.get(PreparationMaterialType.ANSWER_BANK.value)

        # Extract answered questions if screening_answers artifact exists
        screening_qna_items: dict[str, tuple[str, list[str], str]] = {}
        if screening_artifact is not None:
            for block in screening_artifact.blocks:
                if block.block_type in ("qna_item", "screening_answer"):
                    meta = block.metadata or {}
                    q_text = meta.get("question") or ""
                    ans_text = meta.get("answer") or ""
                    status = meta.get("answer_status")
                    if q_text and ans_text and status in ("ANSWERED", "COMPLETED"):
                        screening_qna_items[q_text.strip().lower()] = (ans_text.strip(), list(block.claim_ids), screening_artifact.artifact_id)

        if target_schema is not None and target_schema.screening_questions:
            if screening_artifact is None and answer_bank_artifact is not None:
                # Package contains only Answer Bank; cannot use as transmission payload
                findings.append(
                    ExecutionPlanFinding(
                        check_name="screening_resolution",
                        passed=False,
                        severity="error",
                        code="ANSWER_BANK_TRANSMISSION_PROHIBITED",
                        message="Approved package contains APPLICATION_ANSWER_BANK (internal preparation only), which cannot be used as an external transmission payload.",
                    )
                )
            else:
                for sq in target_schema.screening_questions:
                    match = screening_qna_items.get(sq.question_text.strip().lower())
                    if match is not None:
                        ans_val, claim_ids, art_id = match
                        screening_responses.append(
                            ExecutionScreeningResponse(
                                question_id=sq.question_id,
                                question=sq.question_text,
                                answer=ans_val,
                                source_artifact_id=art_id,
                                grounding_claim_ids=claim_ids,
                            )
                        )
                    elif sq.required:
                        findings.append(
                            ExecutionPlanFinding(
                                check_name="screening_resolution",
                                passed=False,
                                severity="error",
                                code="UNRESOLVED_SCREENING_QUESTION",
                                message=f"Required screening question '{sq.question_id}' has no matching answered entry in SCREENING_ANSWERS.",
                                details={"question_id": sq.question_id, "question_text": sq.question_text},
                            )
                        )

        # 6. Final Submit Control
        final_control = target_schema.final_submit_control if target_schema else None
        if target_schema is not None and final_control is None:
            findings.append(
                ExecutionPlanFinding(
                    check_name="submit_control_resolution",
                    passed=False,
                    severity="error",
                    code="FINAL_SUBMIT_CONTROL_REQUIRED",
                    message="Target schema does not specify a final submit control descriptor.",
                )
            )

        # 7. Evaluate Durable Upload Assets Precondition (Clarification 2)
        for u in upload_mappings:
            if not u.materialized or u.byte_size <= 0:
                findings.append(
                    ExecutionPlanFinding(
                        check_name="upload_asset_durability",
                        passed=False,
                        severity="error",
                        code="UNMATERIALIZED_UPLOAD",
                        message=f"Upload asset '{u.role}' has not been materialized or is empty (byte_size={u.byte_size}).",
                        details={"role": u.role, "asset_id": u.asset_id},
                    )
                )
            elif u.content_bytes is not None:
                computed_hash = compute_sha256(u.content_bytes.decode("utf-8", errors="replace"))
                if computed_hash != u.content_sha256:
                    findings.append(
                        ExecutionPlanFinding(
                            check_name="upload_asset_durability",
                            passed=False,
                            severity="error",
                            code="UPLOAD_HASH_MISMATCH",
                            message=f"Upload asset '{u.role}' content hash mismatch.",
                            details={"role": u.role, "computed": computed_hash, "stored": u.content_sha256},
                        )
                    )

        # 8. Evaluate Overall Plan Status
        has_errors = any(f.severity == "error" for f in findings)
        plan_status = (
            ExternalExecutionPlanStatus.READY_FOR_AUTHORIZATION
            if not has_errors
            else ExternalExecutionPlanStatus.DRAFT
        )

        input_hash = compute_execution_input_hash(field_mappings)

        return ExternalExecutionPlan(
            case_id=case.case_id,
            opportunity_id=opportunity.opportunity_id,
            package_id=package.package_id,
            package_fingerprint=package.package_fingerprint,
            user_id=case.user_id,
            destination=destination,
            destination_provenance=destination.destination_provenance,
            status=plan_status,
            target_schema=target_schema,
            target_schema_hash=target_schema.schema_hash if target_schema else "",
            field_mappings=field_mappings,
            upload_mappings=upload_mappings,
            screening_responses=screening_responses,
            final_submit_control=final_control,
            findings=findings,
            execution_input_hash=input_hash,
            schema_version=cls.SCHEMA_VERSION,
            planner_version=cls.PLANNER_VERSION,
        )
