"""Execution plan compiler synthesizing deterministic, content-addressed external execution plans."""

from __future__ import annotations

from datetime import date, datetime, timezone
import decimal
import email.utils
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
    TargetAnswerType,
    TargetFieldType,
    TargetSchemaProvenance,
    VerificationStatus,
)
from caseworker.domain.errors import (
    DomainValidationError,
    EntityNotFoundError,
    PackageVerificationError,
)
from caseworker.domain.email_mime import (
    serialize_mime_message,
    validate_email_header_field,
)
from caseworker.domain.execution_plan import (
    EXEC_ACTION_PLANNER_VERSION,
    EXEC_PLAN_SCHEMA_VERSION,
    FORBIDDEN_FIELD_PATTERN,
    EmailAttachmentManifest,
    EmailPayloadManifest,
    ExecutionDestination,
    ExecutionFieldMapping,
    ExecutionPlanFinding,
    ExecutionScreeningResponse,
    ExecutionUploadAsset,
    ExternalExecutionPlan,
    TargetFieldBinding,
    TargetInteractionSchema,
    TargetSchemaField,
    TargetSchemaUploadSlot,
    TargetSubmitControlSpec,
    assert_safe_channel_metadata,
    assert_safe_destination,
    compute_execution_input_hash,
    validate_and_normalize_field_value,
)
from caseworker.domain.opportunity import Opportunity
from caseworker.domain.package import PreparationPackage
from caseworker.domain.types import compute_sha256, compute_sha256_bytes
from caseworker.domain.vault_policy import ContextAccessPolicy




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
        field_bindings: list[TargetFieldBinding] | None = None,
    ) -> ExternalExecutionPlan:
        """Compile an ExternalExecutionPlan deterministically.

        Constitutional Invariant:
        - Planning does NOT equal approval, transmission, or execution authority.
        - Zero outbound network transmission is executed here.
        """
        # Pre-generate plan_id before asset construction (Correction 1)
        plan_id = f"plan-{uuid.uuid4().hex[:16]}"

        findings: list[ExecutionPlanFinding] = []
        metadata = dict(channel_metadata or {})
        active_facts_list = facts if facts is not None else (context_facts or [])

        # 1. Destination Resolution with Security Gating (Hardening Point 4, 5, 6)
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
                is_exact_dest = bool(canonical_url)
                if canonical_url.lower().startswith("mailto:") or ("@" in canonical_url and "://" not in canonical_url):
                    dest_type = ExecutionDestinationType.EMAIL.value
                    if not canonical_url.lower().startswith("mailto:"):
                        canonical_url = f"mailto:{canonical_url}"
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
        elif opportunity.metadata.get("application_email") or opportunity.metadata.get("apply_email"):
            email_addr = str(opportunity.metadata.get("application_email") or opportunity.metadata.get("apply_email")).strip()
            canonical_url = f"mailto:{email_addr}" if not email_addr.lower().startswith("mailto:") else email_addr
            dest_type = ExecutionDestinationType.EMAIL.value
            resolved_provenance = DestinationProvenance.OPPORTUNITY_APPLICATION_URL.value
            is_exact_dest = True
            findings.append(
                ExecutionPlanFinding(
                    check_name="destination_resolution",
                    passed=True,
                    severity="info",
                    code="OPPORTUNITY_EMAIL_RESOLVED",
                    message="Application email endpoint resolved from opportunity application metadata.",
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
        elif opportunity.source_url and opportunity.source_url.strip():
            # Listing URL alone is not an application endpoint
            canonical_url = opportunity.source_url.strip()
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
        else:
            # Neither apply url nor source url exists (Hardening Point 5)
            canonical_url = ""
            is_exact_dest = False
            findings.append(
                ExecutionPlanFinding(
                    check_name="destination_resolution",
                    passed=False,
                    severity="error",
                    code="DESTINATION_REQUIRED",
                    message="Exact application destination is unknown. No destination URL exists on opportunity.",
                    details={"source_url": ""},
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

        # Destination channel boundary restrictions (Hardening Point 4)
        dest_type_val = destination.destination_type.value if hasattr(destination.destination_type, "value") else str(destination.destination_type)
        if dest_type_val not in (ExecutionDestinationType.ATS_FORM.value, ExecutionDestinationType.DIRECT_PORTAL.value, ExecutionDestinationType.EMAIL.value):
            findings.append(
                ExecutionPlanFinding(
                    check_name="destination_boundary_validation",
                    passed=False,
                    severity="error",
                    code="UNSUPPORTED_DESTINATION_TYPE",
                    message=f"Destination type '{dest_type_val}' is not supported for external execution authorization in v1.",
                    details={"destination_type": dest_type_val},
                )
            )

        # Affirmative employer acceptance verification for email applications (Amendment C)
        if dest_type_val == ExecutionDestinationType.EMAIL.value:
            is_email_accepted = (
                opportunity.metadata.get("application_channel") == "email"
                or opportunity.metadata.get("accepts_email_applications") is True
                or bool(opportunity.metadata.get("application_email_evidence"))
            )
            if not is_email_accepted:
                findings.append(
                    ExecutionPlanFinding(
                        check_name="email_application_acceptance_verification",
                        passed=False,
                        severity="error",
                        code="EMAIL_APPLICATION_NOT_ACCEPTED",
                        message=(
                            "The opportunity does not provide affirmative evidence that applications by email are accepted. "
                            "An email address alone is not sufficient without verified employer acceptance."
                        ),
                        details={
                            "opportunity_id": opportunity.opportunity_id,
                            "destination_url": canonical_url,
                        },
                    )
                )

        # 2. Check Target Interaction Schema & Destination Type Alignment
        if target_schema is None:
            if dest_type_val == ExecutionDestinationType.EMAIL.value:
                # For EMAIL destinations, construct authoritative interaction schema if none provided
                target_schema = TargetInteractionSchema(
                    schema_id=f"schema_email_{opportunity.opportunity_id[:8]}",
                    destination_type=ExecutionDestinationType.EMAIL.value,
                    target_schema_provenance=TargetSchemaProvenance.USER_PROVIDED.value,
                    fields=[
                        TargetSchemaField(target_field_id="applicant_email", label="Applicant Email", field_type=TargetFieldType.EMAIL.value, required=True),
                        TargetSchemaField(target_field_id="full_name", label="Full Name", field_type=TargetFieldType.TEXT.value, required=True),
                    ],
                    upload_slots=[
                        TargetSchemaUploadSlot(slot_id="cv_slot", role="cv", label="Curriculum Vitae / Resume", required=True, accepted_mime_types=["application/pdf", "text/plain"]),
                    ],
                    final_submit_control=TargetSubmitControlSpec(
                        control_type="email_send",
                        semantic_target="Send Governed Application Email",
                        method="GMAIL_API_SEND",
                        selector="gmail:users.messages.send",
                        control_provenance=TargetSchemaProvenance.USER_PROVIDED.value,
                        requires_double_confirmation=True,
                    ),
                )
            else:
                findings.append(
                    ExecutionPlanFinding(
                        check_name="target_schema_verification",
                        passed=False,
                        severity="error",
                        code="TARGET_SCHEMA_REQUIRED",
                        message="Target interaction schema is absent. Without a declared interaction schema, exact form fields and controls cannot be resolved.",
                    )
                )
        else:
            schema_dest_type = target_schema.destination_type.value if hasattr(target_schema.destination_type, "value") else str(target_schema.destination_type)
            if schema_dest_type != dest_type_val:
                findings.append(
                    ExecutionPlanFinding(
                        check_name="target_schema_verification",
                        passed=False,
                        severity="error",
                        code="DESTINATION_TYPE_MISMATCH",
                        message=f"Target schema destination_type '{schema_dest_type}' does not match destination type '{dest_type_val}'.",
                        details={"schema_destination_type": schema_dest_type, "destination_type": dest_type_val},
                    )
                )

        # 3. Field Mappings from Explicit TargetFieldBinding & Centralized Vault Access Policy (Correction 2 & 5)
        field_mappings: list[ExecutionFieldMapping] = []
        facts_by_id: dict[str, ContextFact] = {
            f.fact_id: f for f in active_facts_list if getattr(f, "superseded_by_fact_id", None) is None
        }
        bindings_by_id = {b.target_field_id: b.source_fact_id for b in (field_bindings or [])}
        vault_policy = ContextAccessPolicy()

        if target_schema is not None:
            for tf in target_schema.fields:
                # Reject authentication secret fields (Correction 5)
                if FORBIDDEN_FIELD_PATTERN.search(tf.target_field_id) or FORBIDDEN_FIELD_PATTERN.search(tf.label):
                    findings.append(
                        ExecutionPlanFinding(
                            check_name="field_mapping_resolution",
                            passed=False,
                            severity="error",
                            code="AUTH_CREDENTIAL_FIELD_PROHIBITED",
                            message=f"Target field '{tf.target_field_id}' ('{tf.label}') represents an authentication secret, which is strictly prohibited in execution plans.",
                            details={"target_field_id": tf.target_field_id, "label": tf.label},
                        )
                    )
                    continue

                # Explicit binding resolution (Correction 2)
                bound_fact_id = bindings_by_id.get(tf.target_field_id)
                if bound_fact_id is None and dest_type_val == ExecutionDestinationType.EMAIL.value:
                    for f in active_facts_list:
                        if getattr(f, "superseded_by_fact_id", None) is not None:
                            continue
                        f_type = (getattr(f, "key", "") or getattr(f, "fact_type", "")).lower()
                        if tf.target_field_id in ("applicant_email", "email") and ("email" in f_type or "@" in str(f.value)):
                            bound_fact_id = f.fact_id
                            break
                        elif tf.target_field_id in ("full_name", "name") and ("name" in f_type or f_type in ("full_name", "name")):
                            bound_fact_id = f.fact_id
                            break

                if bound_fact_id is None:
                    if tf.required:
                        findings.append(
                            ExecutionPlanFinding(
                                check_name="field_mapping_resolution",
                                passed=False,
                                severity="error",
                                code="UNRESOLVED_REQUIRED_FIELD",
                                message=f"Required target field '{tf.target_field_id}' has no explicit ContextFact binding.",
                                details={"target_field_id": tf.target_field_id, "label": tf.label},
                            )
                        )
                    continue

                valid_fact = facts_by_id.get(bound_fact_id)
                if valid_fact is None:
                    if tf.required:
                        findings.append(
                            ExecutionPlanFinding(
                                check_name="field_mapping_resolution",
                                passed=False,
                                severity="error",
                                code="UNRESOLVED_REQUIRED_FIELD",
                                message=f"Bound ContextFact '{bound_fact_id}' for field '{tf.target_field_id}' was not found in active vault facts.",
                                details={"target_field_id": tf.target_field_id, "source_fact_id": bound_fact_id},
                            )
                        )
                    continue

                # Centralized Vault policy evaluation with job_application purpose
                access_decision = vault_policy.evaluate_access(
                    valid_fact,
                    requested_purpose="job_application",
                    require_verified=True,
                )
                v_status = (
                    valid_fact.verification_status.value
                    if hasattr(valid_fact.verification_status, "value")
                    else str(valid_fact.verification_status)
                )

                if (
                    not access_decision.is_granted
                    or v_status not in (VerificationStatus.USER_VERIFIED.value, VerificationStatus.SOURCE_VERIFIED.value)
                ):
                    if tf.required:
                        findings.append(
                            ExecutionPlanFinding(
                                check_name="field_mapping_resolution",
                                passed=False,
                                severity="error",
                                code="UNRESOLVED_REQUIRED_FIELD",
                                message=f"ContextFact '{bound_fact_id}' for field '{tf.target_field_id}' is denied by Vault policy: {access_decision.reason} (verification: {v_status})",
                                details={
                                    "target_field_id": tf.target_field_id,
                                    "source_fact_id": bound_fact_id,
                                    "reason": access_decision.reason,
                                    "reason_code": access_decision.reason_code,
                                },
                            )
                        )
                    continue

                # Deterministic field value normalization
                is_valid, norm_val, err_msg = validate_and_normalize_field_value(tf, valid_fact.value)
                if is_valid:
                    field_mappings.append(
                        ExecutionFieldMapping(
                            target_field_id=tf.target_field_id,
                            field_name=tf.label,
                            field_value=norm_val,
                            source_fact_id=valid_fact.fact_id,
                            source_fact_version=valid_fact.version,
                            verification_status=v_status,
                            required=tf.required,
                            allowed_purposes=list(valid_fact.allowed_purposes or []),
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

        # 4. Upload Mappings & Byte Asset Materialization (Correction 1 & Hardening Point 1, 2, 3)
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

                # Materialize raw export byte asset using binary-safe sha256 (Hardening Point 3)
                rendered_text = art.compute_rendered_payload()
                raw_bytes = rendered_text.encode("utf-8")
                byte_sha256 = compute_sha256_bytes(raw_bytes)

                # Check acceptable mime type
                mime_type = "text/plain"
                ext = "txt"
                if "text/plain" in slot.accepted_mime_types:
                    mime_type = "text/plain"
                    ext = "txt"
                elif "application/pdf" in slot.accepted_mime_types and len(slot.accepted_mime_types) == 1:
                    # Slot strictly requires PDF bytes, but we only have text payload in this environment
                    deterministic_asset_id = (
                        f"asset-{compute_sha256(f'{plan_id}:{slot.slot_id}:{art.artifact_id}:{art.version}:{byte_sha256}')[:16]}"
                    )
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
                            plan_id=plan_id,
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
                deterministic_asset_id = (
                    f"asset-{compute_sha256(f'{plan_id}:{slot.slot_id}:{art.artifact_id}:{art.version}:{byte_sha256}')[:16]}"
                )
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
                        plan_id=plan_id,
                        case_id=case.case_id,
                        user_id=case.user_id,
                    )
                )

        # 5. Screening Responses (SCREENING_ANSWERS only; ANSWER_BANK strictly prohibited; Hardening Point 7)
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
                        # Semantic validation of screening answers (Hardening Point 7)
                        sq_type = (sq.answer_type or TargetAnswerType.TEXT.value).lower()
                        if sq_type == TargetAnswerType.SELECT.value:
                            allowed = sq.allowed_options or []
                            if not allowed:
                                findings.append(
                                    ExecutionPlanFinding(
                                        check_name="screening_resolution",
                                        passed=False,
                                        severity="error",
                                        code="SCREENING_SELECT_OPTIONS_EMPTY",
                                        message=f"Screening question '{sq.question_id}' of type 'select' has no declared allowed_options.",
                                        details={"question_id": sq.question_id},
                                    )
                                )
                                continue
                            matched_opt = None
                            if ans_val in allowed:
                                matched_opt = ans_val
                            else:
                                ci = [opt for opt in allowed if opt.lower() == ans_val.lower()]
                                if len(ci) == 1:
                                    matched_opt = ci[0]
                                elif {opt.lower() for opt in allowed} == {"yes", "no"}:
                                    if ans_val.lower() in ("true", "1", "yes"):
                                        matched_opt = [opt for opt in allowed if opt.lower() == "yes"][0]
                                    elif ans_val.lower() in ("false", "0", "no"):
                                        matched_opt = [opt for opt in allowed if opt.lower() == "no"][0]
                            if matched_opt is None:
                                findings.append(
                                    ExecutionPlanFinding(
                                        check_name="screening_resolution",
                                        passed=False,
                                        severity="error",
                                        code="INVALID_QUESTION_ANSWER_CONVERSION",
                                        message=f"Screening question '{sq.question_id}' of type 'select' could not be resolved from answer text '{ans_val}'. Must match one of {allowed}.",
                                        details={"question_id": sq.question_id, "answer": ans_val, "allowed_options": allowed},
                                    )
                                )
                                continue
                            ans_val = matched_opt
                        elif sq_type == TargetAnswerType.BOOLEAN.value:
                            if ans_val.lower() in ("true", "yes", "1"):
                                ans_val = "true"
                            elif ans_val.lower() in ("false", "no", "0"):
                                ans_val = "false"
                            else:
                                findings.append(
                                    ExecutionPlanFinding(
                                        check_name="screening_resolution",
                                        passed=False,
                                        severity="error",
                                        code="INVALID_QUESTION_ANSWER_CONVERSION",
                                        message=f"Screening question '{sq.question_id}' of type 'boolean' cannot be deterministically converted from '{ans_val}'.",
                                        details={"question_id": sq.question_id, "answer": ans_val},
                                    )
                                )
                                continue
                        elif sq_type == TargetAnswerType.DATE.value:
                            raw_clean = ans_val.strip()
                            is_iso = bool(re.fullmatch(r"\d{4}-\d{2}-\d{2}", raw_clean))
                            if is_iso:
                                try:
                                    date.fromisoformat(raw_clean)
                                    ans_val = raw_clean
                                except (ValueError, TypeError):
                                    is_iso = False
                            if not is_iso:
                                findings.append(
                                    ExecutionPlanFinding(
                                        check_name="screening_resolution",
                                        passed=False,
                                        severity="error",
                                        code="INVALID_QUESTION_ANSWER_CONVERSION",
                                        message=f"Screening question '{sq.question_id}' of type 'date' cannot be parsed as ISO date (YYYY-MM-DD) from '{ans_val}'.",
                                        details={"question_id": sq.question_id, "answer": ans_val},
                                    )
                                )
                                continue
                        elif sq_type == TargetAnswerType.NUMBER.value:
                            raw_clean = ans_val.strip()
                            if "," in raw_clean or " " in raw_clean:
                                findings.append(
                                    ExecutionPlanFinding(
                                        check_name="screening_resolution",
                                        passed=False,
                                        severity="error",
                                        code="INVALID_QUESTION_ANSWER_CONVERSION",
                                        message=f"Screening question '{sq.question_id}' of type 'number' contains ambiguous formatting or invalid characters: '{ans_val}'.",
                                        details={"question_id": sq.question_id, "answer": ans_val},
                                    )
                                )
                                continue
                            try:
                                dec = decimal.Decimal(raw_clean)
                                if not dec.is_finite():
                                    raise decimal.InvalidOperation("Non-finite decimal")
                                ans_val = str(dec)
                            except (decimal.InvalidOperation, TypeError, ValueError):
                                findings.append(
                                    ExecutionPlanFinding(
                                        check_name="screening_resolution",
                                        passed=False,
                                        severity="error",
                                        code="INVALID_QUESTION_ANSWER_CONVERSION",
                                        message=f"Screening question '{sq.question_id}' of type 'number' cannot be parsed as a valid decimal number from '{ans_val}'.",
                                        details={"question_id": sq.question_id, "answer": ans_val},
                                    )
                                )
                                continue

                        screening_responses.append(
                            ExecutionScreeningResponse(
                                question_id=sq.question_id,
                                question=sq.question_text,
                                answer=ans_val,
                                source_artifact_id=art_id,
                                grounding_claim_ids=claim_ids,
                                source_artifact_version=screening_artifact.version if screening_artifact else 1,
                                source_payload_hash=screening_artifact.compute_rendered_payload_hash() if screening_artifact else "",
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

        # 6. Final Submit Control (Correction 4)
        final_control = final_submit_control or (target_schema.final_submit_control if target_schema else None)
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
        elif final_control is not None:
            if not final_control.control_type or not final_control.control_type.strip():
                findings.append(
                    ExecutionPlanFinding(
                        check_name="submit_control_resolution",
                        passed=False,
                        severity="error",
                        code="SUBMIT_CONTROL_TYPE_REQUIRED",
                        message="Final submit control must explicitly specify control_type.",
                    )
                )
            if not final_control.semantic_target or not final_control.semantic_target.strip():
                findings.append(
                    ExecutionPlanFinding(
                        check_name="submit_control_resolution",
                        passed=False,
                        severity="error",
                        code="SUBMIT_CONTROL_SEMANTIC_TARGET_REQUIRED",
                        message="Final submit control must explicitly specify semantic_target.",
                    )
                )
            if not final_control.method or not final_control.method.strip():
                findings.append(
                    ExecutionPlanFinding(
                        check_name="submit_control_resolution",
                        passed=False,
                        severity="error",
                        code="SUBMIT_CONTROL_METHOD_REQUIRED",
                        message="Final submit control must explicitly specify submission method (e.g. POST, PUT, CLICK).",
                        details={"control_type": final_control.control_type},
                    )
                )
            if not final_control.selector and not final_control.control_id:
                findings.append(
                    ExecutionPlanFinding(
                        check_name="submit_control_resolution",
                        passed=False,
                        severity="error",
                        code="EXACT_SUBMIT_LOCATOR_REQUIRED",
                        message="Final submit control must specify an exact stable locator (selector or control_id).",
                        details={"control_type": final_control.control_type},
                    )
                )

        # 7. Evaluate Durable Upload Assets Precondition (Hardening Point 1, 2, 3)
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
                computed_hash = compute_sha256_bytes(u.content_bytes)
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

        # 8. Assemble EmailPayloadManifest if destination is EMAIL (Amendment A)
        email_manifest: EmailPayloadManifest | None = None
        if dest_type_val == ExecutionDestinationType.EMAIL.value:
            from_email = ""
            applicant_name = ""
            for fm in field_mappings:
                if fm.target_field_id in ("applicant_email", "email"):
                    from_email = str(fm.field_value).strip()
                elif fm.target_field_id in ("full_name", "name"):
                    applicant_name = str(fm.field_value).strip()

            to_email = destination.canonical_url
            if to_email.lower().startswith("mailto:"):
                to_email = to_email[7:].strip()

            subject = str(destination.channel_metadata.get("email_subject") or "").strip()
            if not subject:
                role = getattr(opportunity, "role", None) or getattr(case, "title", None) or "Role"
                org = getattr(opportunity, "organization", None) or destination.organization or "Employer"
                if applicant_name:
                    subject = f"Application: {role} at {org} - {applicant_name}"
                else:
                    subject = f"Application: {role} at {org}"

            cover_letter_art = artifacts_by_type.get(PreparationMaterialType.COVER_LETTER.value)
            if cover_letter_art is not None:
                body_text = cover_letter_art.compute_rendered_payload()
            else:
                body_text = (
                    f"Dear Hiring Team,\n\n"
                    f"Please accept my application for {opportunity.role or 'the open position'}.\n\n"
                    f"Best regards,\n{applicant_name or 'Applicant'}"
                )

            dt = case.created_at
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            date_rfc2822 = email.utils.format_datetime(dt, usegmt=True)

            host = destination.normalized_hostname or "jackverse.local"
            message_id = f"<plan-{plan_id}@{host}>"
            mime_boundary = f"==_JackVerse_MIME_Part_{plan_id}_=="

            att_manifests = [
                EmailAttachmentManifest(
                    filename=u.filename,
                    mime_type=u.mime_type,
                    content_sha256=u.content_sha256,
                    byte_size=u.byte_size,
                    asset_id=u.asset_id,
                )
                for u in upload_mappings
            ]

            temp_manifest = EmailPayloadManifest(
                to_email=to_email,
                from_email=from_email,
                subject=subject,
                message_id=message_id,
                date_rfc2822=date_rfc2822,
                body_text=body_text,
                mime_boundary=mime_boundary,
                attachments=att_manifests,
                sealed_payload_hash="",
            )

            # Check if any errors exist prior to sealing
            initial_has_errors = any(f.severity == "error" for f in findings)
            if not initial_has_errors:
                try:
                    sealed_bytes = serialize_mime_message(temp_manifest, upload_mappings)
                    sealed_hash = compute_sha256_bytes(sealed_bytes)
                    email_manifest = EmailPayloadManifest(
                        to_email=to_email,
                        from_email=from_email,
                        subject=subject,
                        message_id=message_id,
                        date_rfc2822=date_rfc2822,
                        body_text=body_text,
                        mime_boundary=mime_boundary,
                        attachments=att_manifests,
                        sealed_payload_hash=sealed_hash,
                    )
                except Exception as exc:
                    findings.append(
                        ExecutionPlanFinding(
                            check_name="email_manifest_sealing",
                            passed=False,
                            severity="error",
                            code="MIME_SEALING_ERROR",
                            message=f"Failed to seal RFC 2822 MIME payload: {exc}",
                        )
                    )
            else:
                email_manifest = temp_manifest

        # 9. Evaluate Overall Plan Status
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
            plan_id=plan_id,
            schema_version=cls.SCHEMA_VERSION,
            planner_version=cls.PLANNER_VERSION,
            email_manifest=email_manifest,
        )
