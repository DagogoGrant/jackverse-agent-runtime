"""Preparation intelligence analyzer and strategy formulation engine.

Evaluates opportunity requirements against purpose-gated context facts, maps evidence
across 5 alignment states, categorizes gaps into 3 classes, and plans claim intents.
"""

from __future__ import annotations

from typing import Any
import uuid

from caseworker.domain.case import Case
from caseworker.domain.context import ContextFact
from caseworker.domain.enums import (
    CaseType,
    EvidenceAlignmentStatus,
    GapCategory,
    PreparationMaterialType,
    RequirementCategory,
    RequirementMatchResult,
    VerificationStatus,
)
from caseworker.domain.normalized_requirement import NormalizedRequirement, parse_requirements
from caseworker.domain.opportunity import Opportunity
from caseworker.domain.preparation import (
    ClaimIntent,
    EvidenceMappingItem,
    GapItem,
    PreparationIntelligenceReport,
    PreparationStrategy,
    SafeDeniedFactDescriptor,
    compute_prep_context_snapshot_hash,
    compute_prep_input_hash,
    is_fact_or_descriptor_relevant_to_requirements,
)
from caseworker.domain.requirement_matcher import RequirementMatcher, is_fact_verified
from caseworker.domain.types import now_utc


class PreparationAnalyzer:
    """Orchestrates deterministic preparation intelligence analysis."""

    ANALYZER_VERSION = "prep_analyzer_v1"
    SCHEMA_VERSION = "prep_intel_v1"

    @classmethod
    def analyze(
        cls,
        case: Case,
        opportunity: Opportunity,
        granted_facts: list[ContextFact],
        denied_descriptors: list[SafeDeniedFactDescriptor] | None = None,
        mission_goal: str = "",
        mission_constraints: list[str] | None = None,
        previous_version: int = 0,
    ) -> PreparationIntelligenceReport:
        """Execute preparation intelligence evaluation and produce a versioned report snapshot."""
        # 1. Obtain normalized requirements from opportunity
        raw_normalized = opportunity.metadata.get("normalized_requirements")
        if raw_normalized:
            normalized_reqs = [NormalizedRequirement.from_dict(d) for d in raw_normalized]
        else:
            normalized_reqs = parse_requirements(opportunity.opportunity_id, opportunity.requirements)
            opportunity.metadata["normalized_requirements"] = [r.to_dict() for r in normalized_reqs]
            opportunity.metadata["requirement_schema"] = "opp_req_v1"

        # 2. Filter active granted facts (not superseded, not rejected)
        active_granted = [
            f for f in granted_facts
            if f.superseded_by_fact_id is None and f.verification_status != VerificationStatus.REJECTED
        ]
        granted_by_ns: dict[str, list[ContextFact]] = {}
        for f in active_granted:
            granted_by_ns.setdefault(f.namespace.lower(), []).append(f)

        evidence_map: list[EvidenceMappingItem] = []
        gaps: list[GapItem] = []
        claim_intents: list[ClaimIntent] = []
        strengths: list[str] = []
        mitigations: list[str] = []

        # 3. Analyze each requirement against available evidence
        for req in normalized_reqs:
            is_mandatory = req.category == RequirementCategory.MANDATORY

            # Match against granted facts
            match_res, matched_fact, notes = RequirementMatcher.match(
                req=req,
                facts_by_ns=granted_by_ns,
                active_facts=active_granted,
            )

            matched_denied: SafeDeniedFactDescriptor | None = None

            # Determine alignment status across 5 states
            if match_res == RequirementMatchResult.MET and matched_fact is not None:
                if is_fact_verified(matched_fact.verification_status):
                    status = EvidenceAlignmentStatus.SUPPORTED
                else:
                    status = EvidenceAlignmentStatus.POTENTIAL
            elif match_res == RequirementMatchResult.NOT_MET and matched_fact is not None:
                status = EvidenceAlignmentStatus.CONTRADICTED
            elif matched_fact is not None and not is_fact_verified(matched_fact.verification_status):
                # Matched unverified fact for mandatory requirement returns UNKNOWN from matcher
                status = EvidenceAlignmentStatus.POTENTIAL
            else:
                # Check if requirement matches a restricted fact via safe metadata descriptor
                # CRITICAL SECURITY INVARIANT: Never inspect denied values
                if denied_descriptors:
                    relevant_ns = RequirementMatcher.CRITERION_TO_NAMESPACES.get(req.criterion, set())
                    for desc in denied_descriptors:
                        desc_ns = desc.namespace.lower()
                        desc_key = desc.key.lower()
                        if desc_ns in relevant_ns:
                            if req.criterion == "skill":
                                skill_name = str(req.value or "").lower()
                                if not skill_name or skill_name in desc_key or desc_key in skill_name:
                                    matched_denied = desc
                                    break
                            elif req.criterion == "language":
                                lang_name = str(req.value or "").lower()
                                if not lang_name or lang_name in desc_key or desc_key in lang_name:
                                    matched_denied = desc
                                    break
                            else:
                                matched_denied = desc
                                break
                        else:
                            val_str = str(req.value or "").lower()
                            if val_str and (val_str in desc_key or desc_key in val_str):
                                matched_denied = desc
                                break

                if matched_denied is not None:
                    status = EvidenceAlignmentStatus.RESTRICTED
                    reason = matched_denied.reason or "Purpose 'case_preparation' not authorized"
                    notes = f"Relevant vault evidence exists but access was restricted: {reason}"
                else:
                    status = EvidenceAlignmentStatus.GAP
                    notes = notes or "No matching fact found in context vault."

            # Construct safe EvidenceMappingItem (CRITICAL PRIVACY INVARIANT: no sensitive fact values)
            if status == EvidenceAlignmentStatus.RESTRICTED and matched_denied is not None:
                ev_item = EvidenceMappingItem(
                    requirement_id=req.requirement_id,
                    requirement_type=req.criterion,
                    description=req.raw_text,
                    mandatory=is_mandatory,
                    status=status,
                    matched_fact_id=matched_denied.fact_id,
                    matched_fact_version=matched_denied.fact_version,
                    verification_status=matched_denied.verification_status,
                    fact_namespace=matched_denied.namespace,
                    fact_key=matched_denied.key,
                    notes=notes,
                )
            else:
                ev_item = EvidenceMappingItem(
                    requirement_id=req.requirement_id,
                    requirement_type=req.criterion,
                    description=req.raw_text,
                    mandatory=is_mandatory,
                    status=status,
                    matched_fact_id=matched_fact.fact_id if matched_fact else None,
                    matched_fact_version=matched_fact.version if matched_fact else None,
                    verification_status=matched_fact.verification_status if matched_fact else None,
                    fact_namespace=matched_fact.namespace if matched_fact else None,
                    fact_key=matched_fact.key if matched_fact else None,
                    notes=notes,
                )
            evidence_map.append(ev_item)

            # Categorize Gaps into 3 classes
            if status == EvidenceAlignmentStatus.CONTRADICTED:
                guidance = "Review conflicting evidence or explore eligibility waiver/clarification."
                gap = GapItem(
                    gap_id=f"gap-{uuid.uuid4().hex[:8]}",
                    requirement_id=req.requirement_id,
                    requirement_type=req.criterion,
                    category=GapCategory.HARD_CONFLICT,
                    description=f"Direct conflict with verified evidence for '{req.raw_text}': {notes}",
                    severity="critical",
                    mitigation_guidance=guidance,
                    fact_id=matched_fact.fact_id if matched_fact else None,
                )
                gaps.append(gap)
                mitigations.append(f"[HARD_CONFLICT] {req.raw_text}: {guidance}")

            elif status in (EvidenceAlignmentStatus.GAP, EvidenceAlignmentStatus.POTENTIAL, EvidenceAlignmentStatus.RESTRICTED):
                if is_mandatory:
                    if status == EvidenceAlignmentStatus.POTENTIAL:
                        guidance = "Verify existing context evidence in Context Vault to upgrade to supported."
                    elif status == EvidenceAlignmentStatus.RESTRICTED:
                        guidance = "Authorize 'case_preparation' access for restricted context fact in vault policy."
                    else:
                        guidance = "Provide new verified credential or experience documentation for requirement."

                    gap = GapItem(
                        gap_id=f"gap-{uuid.uuid4().hex[:8]}",
                        requirement_id=req.requirement_id,
                        requirement_type=req.criterion,
                        category=GapCategory.MANDATORY_EVIDENCE_GAP,
                        description=f"Mandatory requirement '{req.raw_text}' is {status.value.lower()}.",
                        severity="high",
                        mitigation_guidance=guidance,
                        fact_id=matched_fact.fact_id if matched_fact else (matched_denied.fact_id if matched_denied else None),
                    )
                    gaps.append(gap)
                    mitigations.append(f"[MANDATORY_EVIDENCE_GAP] {req.raw_text}: {guidance}")

                else:
                    if status == EvidenceAlignmentStatus.POTENTIAL:
                        guidance = "Verify preferred skill/experience fact to strengthen candidacy."
                    elif status == EvidenceAlignmentStatus.RESTRICTED:
                        guidance = "Grant access to restricted fact to include preferred qualification."
                    else:
                        guidance = "Optional qualification; can be addressed in narrative or left as gap."

                    gap = GapItem(
                        gap_id=f"gap-{uuid.uuid4().hex[:8]}",
                        requirement_id=req.requirement_id,
                        requirement_type=req.criterion,
                        category=GapCategory.PREFERRED_EVIDENCE_GAP,
                        description=f"Preferred requirement '{req.raw_text}' is {status.value.lower()}.",
                        severity="moderate",
                        mitigation_guidance=guidance,
                        fact_id=matched_fact.fact_id if matched_fact else (matched_denied.fact_id if matched_denied else None),
                    )
                    gaps.append(gap)
                    mitigations.append(f"[PREFERRED_EVIDENCE_GAP] {req.raw_text}: {guidance}")

            # Formulate ClaimIntents ONLY for SUPPORTED items
            # CRITICAL GOVERNANCE INVARIANT:
            # POTENTIAL evidence is explicitly unverified and MUST NOT become an assertable drafting seed.
            if status == EvidenceAlignmentStatus.SUPPORTED and matched_fact is not None:
                target_material = (
                    PreparationMaterialType.COVER_LETTER
                    if req.criterion in ("language", "degree") and not is_mandatory
                    else PreparationMaterialType.CV
                )
                supporting_ids = [matched_fact.fact_id]
                angle = f"Highlight verified {req.criterion} meeting '{req.raw_text}'"
                intent = ClaimIntent(
                    intent_id=f"intent-{uuid.uuid4().hex[:8]}",
                    requirement_id=req.requirement_id,
                    target_material=target_material,
                    claim_topic=req.raw_text,
                    supporting_fact_ids=supporting_ids,
                    suggested_angle=angle,
                )
                claim_intents.append(intent)
                strengths.append(f"Confirmed {req.criterion}: '{req.raw_text}'")

        # 4. Formulate PreparationStrategy with CaseType Truthfulness
        case_type_str = case.case_type.value if hasattr(case.case_type, "value") else str(case.case_type)
        if case_type_str.lower() in ("job_application", CaseType.JOB_APPLICATION.value):
            has_questions = bool(
                opportunity.metadata.get("screening_questions")
                or opportunity.metadata.get("questions")
            )
            third_mat = (
                PreparationMaterialType.SCREENING_ANSWERS
                if has_questions
                else PreparationMaterialType.APPLICATION_ANSWER_BANK
            )
            recommended_materials = [
                PreparationMaterialType.CV,
                PreparationMaterialType.COVER_LETTER,
                third_mat,
            ]
            materials_checklist = [
                {"material_type": PreparationMaterialType.CV.value, "required": True, "status": "PENDING_DRAFT"},
                {"material_type": PreparationMaterialType.COVER_LETTER.value, "required": True, "status": "PENDING_DRAFT"},
                {"material_type": third_mat.value, "required": True, "status": "PENDING_DRAFT"},
            ]
        else:
            recommended_materials = []
            materials_checklist = []
            mitigations.append(
                f"Material strategy for case type '{case_type_str}' is not supported in Phase 5.2. Preparation intelligence provided for discovery analysis only."
            )

        strategy = PreparationStrategy(
            recommended_materials=recommended_materials,
            strengths=strengths,
            mitigations=mitigations,
            claim_intents=claim_intents,
            materials_checklist=materials_checklist,
        )

        # 5. Hashes and Metadata with Case Inputs and Relevant Context Dependencies
        input_hash = compute_prep_input_hash(case, opportunity, mission_goal, mission_constraints)

        relevant_granted = [
            f for f in active_granted
            if is_fact_or_descriptor_relevant_to_requirements(f.namespace, f.key, normalized_reqs)
        ]
        relevant_denied = [
            d for d in (denied_descriptors or [])
            if is_fact_or_descriptor_relevant_to_requirements(d.namespace, d.key, normalized_reqs)
        ]
        context_snapshot_hash = compute_prep_context_snapshot_hash(relevant_granted, relevant_denied)

        eval_meta = opportunity.metadata.get("evaluation") or {}
        source_eval_id = eval_meta.get("evaluation_id") or opportunity.metadata.get("latest_evaluation_id")

        return PreparationIntelligenceReport(
            report_id=f"prep-{uuid.uuid4().hex[:12]}",
            case_id=case.case_id,
            opportunity_id=opportunity.opportunity_id,
            user_id=case.user_id,
            schema_version=cls.SCHEMA_VERSION,
            analyzer_version=cls.ANALYZER_VERSION,
            evidence_map=evidence_map,
            gaps=gaps,
            strategy=strategy,
            input_hash=input_hash,
            context_snapshot_hash=context_snapshot_hash,
            source_evaluation_id=source_eval_id,
            created_at=now_utc(),
            version=previous_version + 1,
        )
