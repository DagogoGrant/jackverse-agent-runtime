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
    compute_prep_context_snapshot_hash,
    compute_prep_input_hash,
)
from caseworker.domain.requirement_matcher import RequirementMatcher, is_fact_verified
from caseworker.domain.types import now_utc
from caseworker.domain.vault_policy import AccessDecision


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
        denied_facts: list[ContextFact] | None = None,
        denied_decisions: list[AccessDecision] | None = None,
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

        # Filter active denied facts
        active_denied = [
            f for f in (denied_facts or [])
            if f.superseded_by_fact_id is None and f.verification_status != VerificationStatus.REJECTED
        ]
        denied_by_ns: dict[str, list[ContextFact]] = {}
        for f in active_denied:
            denied_by_ns.setdefault(f.namespace.lower(), []).append(f)

        denial_reason_by_fact_id: dict[str, str] = {}
        if denied_decisions:
            for d in denied_decisions:
                denial_reason_by_fact_id[d.fact_id] = d.reason

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
                # Check if requirement matches a restricted fact in denied context
                denied_match_res, denied_matched_fact, denied_notes = RequirementMatcher.match(
                    req=req,
                    facts_by_ns=denied_by_ns,
                    active_facts=active_denied,
                )
                if denied_matched_fact is not None:
                    status = EvidenceAlignmentStatus.RESTRICTED
                    matched_fact = denied_matched_fact
                    reason = denial_reason_by_fact_id.get(
                        denied_matched_fact.fact_id,
                        "Purpose 'case_preparation' not authorized",
                    )
                    notes = f"Relevant vault evidence exists but access was restricted: {reason}"
                else:
                    status = EvidenceAlignmentStatus.GAP
                    notes = notes or "No matching fact found in context vault."

            # Construct safe EvidenceMappingItem (CRITICAL PRIVACY INVARIANT: no sensitive fact values)
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
                        severity="critical",
                        mitigation_guidance=guidance,
                        fact_id=matched_fact.fact_id if matched_fact else None,
                    )
                    gaps.append(gap)
                    mitigations.append(f"[MANDATORY_EVIDENCE_GAP] {req.raw_text}: {guidance}")
                else:
                    if status == EvidenceAlignmentStatus.POTENTIAL:
                        guidance = "Verify context fact to strengthen alignment with preferred criteria."
                    elif status == EvidenceAlignmentStatus.RESTRICTED:
                        guidance = "Grant access to restricted fact if advantageous for application narrative."
                    else:
                        guidance = "Emphasize transferable skills or address in cover letter narrative."

                    gap = GapItem(
                        gap_id=f"gap-{uuid.uuid4().hex[:8]}",
                        requirement_id=req.requirement_id,
                        requirement_type=req.criterion,
                        category=GapCategory.PREFERRED_EVIDENCE_GAP,
                        description=f"Preferred requirement '{req.raw_text}' is {status.value.lower()}.",
                        severity="moderate",
                        mitigation_guidance=guidance,
                        fact_id=matched_fact.fact_id if matched_fact else None,
                    )
                    gaps.append(gap)
                    mitigations.append(f"[PREFERRED_EVIDENCE_GAP] {req.raw_text}: {guidance}")

            # Formulate ClaimIntents for SUPPORTED and POTENTIAL items
            if status in (EvidenceAlignmentStatus.SUPPORTED, EvidenceAlignmentStatus.POTENTIAL):
                target_material = (
                    PreparationMaterialType.COVER_LETTER
                    if req.criterion in ("language", "degree") and not is_mandatory
                    else PreparationMaterialType.CV
                )
                supporting_ids = [matched_fact.fact_id] if matched_fact else []
                angle = (
                    f"Highlight verified {req.criterion} meeting '{req.raw_text}'"
                    if status == EvidenceAlignmentStatus.SUPPORTED
                    else f"Reference {req.criterion} experience for '{req.raw_text}' pending formal verification"
                )
                intent = ClaimIntent(
                    intent_id=f"intent-{uuid.uuid4().hex[:8]}",
                    requirement_id=req.requirement_id,
                    target_material=target_material,
                    claim_topic=req.raw_text,
                    supporting_fact_ids=supporting_ids,
                    suggested_angle=angle,
                )
                claim_intents.append(intent)

                if status == EvidenceAlignmentStatus.SUPPORTED:
                    strengths.append(f"Confirmed {req.criterion}: '{req.raw_text}'")

        # 4. Formulate PreparationStrategy
        recommended_materials = [
            PreparationMaterialType.CV,
            PreparationMaterialType.COVER_LETTER,
        ]
        materials_checklist = [
            {"material_type": PreparationMaterialType.CV.value, "required": True, "status": "PENDING_DRAFT"},
            {"material_type": PreparationMaterialType.COVER_LETTER.value, "required": True, "status": "PENDING_DRAFT"},
        ]

        strategy = PreparationStrategy(
            recommended_materials=recommended_materials,
            strengths=strengths,
            mitigations=mitigations,
            claim_intents=claim_intents,
            materials_checklist=materials_checklist,
        )

        # 5. Hashes and Metadata
        input_hash = compute_prep_input_hash(opportunity, mission_goal, mission_constraints)
        context_snapshot_hash = compute_prep_context_snapshot_hash(active_granted)

        eval_meta = opportunity.metadata.get("evaluation") or {}
        source_eval_id = eval_meta.get("evaluation_id")

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
