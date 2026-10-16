"""ExecutionPlanCompiler synthesizing exact, reviewable external action plans."""

from __future__ import annotations

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
    compute_execution_input_hash,
)
from caseworker.domain.opportunity import Opportunity
from caseworker.domain.package import PreparationPackage
from caseworker.domain.types import compute_sha256


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

        Invariants:
        - Never guesses ATS destination or fields.
        - Rejects unverified ContextFacts or facts lacking 'job_application' purpose.
        - Rejects APPLICATION_ANSWER_BANK as transmission payload.
        - Computes byte-level hash for materialized upload assets.
        - Resolves to READY_FOR_AUTHORIZATION only if 100% of required fields/files/controls are resolved.
        """
        findings: list[ExecutionPlanFinding] = []
        metadata = dict(channel_metadata or {})
        active_facts_list = context_facts if context_facts is not None else (facts or [])

        # 1. Resolve Exact Destination
        canonical_url = ""
        resolved_provenance = (
            destination_provenance.value
            if hasattr(destination_provenance, "value")
            else str(destination_provenance)
        )
        is_exact_dest = False
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

        # 3. Field Mappings from Verified ContextFacts
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
                    # Also try matching common alias
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
                    field_mappings.append(
                        ExecutionFieldMapping(
                            target_field_id=tf.target_field_id,
                            field_name=tf.label,
                            field_value=valid_fact.value,
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
                                code="UNRESOLVED_REQUIRED_FIELD",
                                message=f"Required target field '{tf.target_field_id}' could not be resolved from verified ContextFacts ({invalid_reason or 'No matching fact found'}).",
                                details={"target_field_id": tf.target_field_id, "label": tf.label},
                            )
                        )

        # 4. Upload Mappings & Byte Asset Materialization
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
                    # Mark unmaterialized so plan stays DRAFT
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

        # 7. Evaluate Plan Status
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
            status=plan_status,
            target_schema=target_schema,
            field_mappings=field_mappings,
            upload_mappings=upload_mappings,
            screening_responses=screening_responses,
            final_submit_control=final_control,
            findings=findings,
            execution_input_hash=input_hash,
            schema_version=cls.SCHEMA_VERSION,
            planner_version=cls.PLANNER_VERSION,
        )
