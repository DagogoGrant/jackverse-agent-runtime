"""Domain models for Phase 5.2.1 Preparation Intelligence.

Captures evidence mapping across 5 alignment states, gap categorization into 3 classes,
strategy formulation, claim intent outlines, and versioned immutable report snapshots.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
import uuid

from caseworker.domain.case import Case
from caseworker.domain.context import ContextFact
from caseworker.domain.enums import (
    EvidenceAlignmentStatus,
    GapCategory,
    PreparationMaterialType,
    VerificationStatus,
)
from caseworker.domain.normalized_requirement import NormalizedRequirement
from caseworker.domain.opportunity import Opportunity
from caseworker.domain.types import (
    canonical_json_dumps,
    compute_sha256,
    ensure_utc,
    from_iso_utc,
    now_utc,
    to_iso_utc,
)


@dataclass(frozen=True)
class SafeDeniedFactDescriptor:
    """Safe descriptor of a denied ContextFact access decision without sensitive values."""

    fact_id: str
    fact_version: int | None
    namespace: str
    key: str
    verification_status: VerificationStatus | str | None = None
    reason_code: str = "DENIED"
    reason: str = ""


@dataclass
class EvidenceMappingItem:
    """Safe projection of user context evidence for a specific opportunity requirement.

    CRITICAL PRIVACY INVARIANT:
    Never store raw context fact values or sensitive data in EvidenceMappingItem.
    Only store pointers (matched_fact_id, matched_fact_version), verification status,
    namespace/key metadata, alignment status, and safe notes.
    """

    requirement_id: str
    requirement_type: str
    description: str
    mandatory: bool
    status: EvidenceAlignmentStatus
    matched_fact_id: str | None = None
    matched_fact_version: int | None = None
    verification_status: VerificationStatus | str | None = None
    fact_namespace: str | None = None
    fact_key: str | None = None
    notes: str = ""

    def to_dict(self) -> dict[str, Any]:
        v_status = self.verification_status
        if isinstance(v_status, VerificationStatus):
            v_status = v_status.value
        return {
            "requirement_id": self.requirement_id,
            "requirement_type": self.requirement_type,
            "description": self.description,
            "mandatory": self.mandatory,
            "status": self.status.value if isinstance(self.status, EvidenceAlignmentStatus) else str(self.status),
            "matched_fact_id": self.matched_fact_id,
            "matched_fact_version": self.matched_fact_version,
            "verification_status": v_status,
            "fact_namespace": self.fact_namespace,
            "fact_key": self.fact_key,
            "notes": self.notes,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> EvidenceMappingItem:
        st = data.get("status", EvidenceAlignmentStatus.GAP)
        if isinstance(st, str):
            try:
                st = EvidenceAlignmentStatus(st)
            except ValueError:
                st = EvidenceAlignmentStatus.GAP
        v_stat = data.get("verification_status")
        if isinstance(v_stat, str):
            try:
                v_stat = VerificationStatus(v_stat)
            except ValueError:
                pass
        return cls(
            requirement_id=data["requirement_id"],
            requirement_type=data.get("requirement_type", "criteria"),
            description=data.get("description", ""),
            mandatory=bool(data.get("mandatory", False)),
            status=st,
            matched_fact_id=data.get("matched_fact_id"),
            matched_fact_version=data.get("matched_fact_version"),
            verification_status=v_stat,
            fact_namespace=data.get("fact_namespace"),
            fact_key=data.get("fact_key"),
            notes=data.get("notes", ""),
        )


@dataclass
class GapItem:
    """Actionable requirement deficiency categorized into one of 3 gap classes."""

    gap_id: str
    requirement_id: str
    requirement_type: str
    category: GapCategory
    description: str
    severity: str  # "critical" or "moderate"
    mitigation_guidance: str
    fact_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "gap_id": self.gap_id,
            "requirement_id": self.requirement_id,
            "requirement_type": self.requirement_type,
            "category": self.category.value if isinstance(self.category, GapCategory) else str(self.category),
            "description": self.description,
            "severity": self.severity,
            "mitigation_guidance": self.mitigation_guidance,
            "fact_id": self.fact_id,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> GapItem:
        cat = data.get("category", GapCategory.MANDATORY_EVIDENCE_GAP)
        if isinstance(cat, str):
            try:
                cat = GapCategory(cat)
            except ValueError:
                cat = GapCategory.MANDATORY_EVIDENCE_GAP
        return cls(
            gap_id=data["gap_id"],
            requirement_id=data["requirement_id"],
            requirement_type=data.get("requirement_type", "criteria"),
            category=cat,
            description=data.get("description", ""),
            severity=data.get("severity", "moderate"),
            mitigation_guidance=data.get("mitigation_guidance", ""),
            fact_id=data.get("fact_id"),
        )


@dataclass
class ClaimIntent:
    """Candidate claim outline planned for later artifact generation.

    CRITICAL BOUNDARY:
    ClaimIntent is an informational outline only. No Claim Ledger entries are created
    until Phase 5.2.2 when actual text exists.
    """

    intent_id: str
    requirement_id: str
    target_material: PreparationMaterialType
    claim_topic: str
    supporting_fact_ids: list[str] = field(default_factory=list)
    suggested_angle: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "intent_id": self.intent_id,
            "requirement_id": self.requirement_id,
            "target_material": self.target_material.value if isinstance(self.target_material, PreparationMaterialType) else str(self.target_material),
            "claim_topic": self.claim_topic,
            "supporting_fact_ids": list(self.supporting_fact_ids),
            "suggested_angle": self.suggested_angle,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ClaimIntent:
        mat = data.get("target_material", PreparationMaterialType.CV)
        if isinstance(mat, str):
            try:
                mat = PreparationMaterialType(mat)
            except ValueError:
                mat = PreparationMaterialType.CV
        return cls(
            intent_id=data["intent_id"],
            requirement_id=data["requirement_id"],
            target_material=mat,
            claim_topic=data.get("claim_topic", ""),
            supporting_fact_ids=list(data.get("supporting_fact_ids") or []),
            suggested_angle=data.get("suggested_angle", ""),
        )


@dataclass
class PreparationStrategy:
    """Actionable pursuit strategy outlining strengths, mitigations, and claim candidates."""

    recommended_materials: list[PreparationMaterialType] = field(default_factory=list)
    strengths: list[str] = field(default_factory=list)
    mitigations: list[str] = field(default_factory=list)
    claim_intents: list[ClaimIntent] = field(default_factory=list)
    materials_checklist: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "recommended_materials": [m.value if isinstance(m, PreparationMaterialType) else str(m) for m in self.recommended_materials],
            "strengths": list(self.strengths),
            "mitigations": list(self.mitigations),
            "claim_intents": [c.to_dict() for c in self.claim_intents],
            "materials_checklist": list(self.materials_checklist),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> PreparationStrategy:
        mats: list[PreparationMaterialType] = []
        for m in data.get("recommended_materials") or []:
            if isinstance(m, str):
                try:
                    mats.append(PreparationMaterialType(m))
                except ValueError:
                    pass
            elif isinstance(m, PreparationMaterialType):
                mats.append(m)
        intents = [ClaimIntent.from_dict(c) for c in (data.get("claim_intents") or [])]
        return cls(
            recommended_materials=mats,
            strengths=list(data.get("strengths") or []),
            mitigations=list(data.get("mitigations") or []),
            claim_intents=intents,
            materials_checklist=list(data.get("materials_checklist") or []),
        )


@dataclass
class PreparationIntelligenceReport:
    """Immutable, versioned preparation intelligence report snapshot.

    Persisted in preparation_intelligence_reports under Schema V6 (prep_intel_v1).
    """

    report_id: str
    case_id: str
    opportunity_id: str
    user_id: str
    schema_version: str = "prep_intel_v1"
    analyzer_version: str = "prep_analyzer_v1"
    evidence_map: list[EvidenceMappingItem] = field(default_factory=list)
    gaps: list[GapItem] = field(default_factory=list)
    strategy: PreparationStrategy = field(default_factory=PreparationStrategy)
    input_hash: str = ""
    context_snapshot_hash: str = ""
    source_evaluation_id: str | None = None
    created_at: datetime = field(default_factory=now_utc)
    version: int = 1

    def __post_init__(self) -> None:
        if self.created_at.tzinfo is None:
            raise ValueError("Report created_at timestamp must be timezone-aware UTC.")
        object.__setattr__(self, "created_at", ensure_utc(self.created_at))

    def is_stale(
        self,
        current_input_hash: str,
        current_context_hash: str,
        current_opportunity_evaluation_id: str | None = None,
        current_analyzer_version: str | None = None,
        current_schema_version: str | None = None,
    ) -> bool:
        """Deterministically determine if report is stale compared to current input and context hashes."""
        if self.input_hash != current_input_hash:
            return True
        if self.context_snapshot_hash != current_context_hash:
            return True
        if (
            current_opportunity_evaluation_id is not None
            and self.source_evaluation_id != current_opportunity_evaluation_id
        ):
            return True
        if (
            current_analyzer_version is not None
            and self.analyzer_version != current_analyzer_version
        ):
            return True
        if (
            current_schema_version is not None
            and self.schema_version != current_schema_version
        ):
            return True
        return False

    def to_dict(self) -> dict[str, Any]:
        return {
            "report_id": self.report_id,
            "case_id": self.case_id,
            "opportunity_id": self.opportunity_id,
            "user_id": self.user_id,
            "schema_version": self.schema_version,
            "analyzer_version": self.analyzer_version,
            "evidence_map": [e.to_dict() for e in self.evidence_map],
            "gaps": [g.to_dict() for g in self.gaps],
            "strategy": self.strategy.to_dict(),
            "input_hash": self.input_hash,
            "context_snapshot_hash": self.context_snapshot_hash,
            "source_evaluation_id": self.source_evaluation_id,
            "created_at": to_iso_utc(self.created_at),
            "version": self.version,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> PreparationIntelligenceReport:
        evidence_map = [EvidenceMappingItem.from_dict(e) for e in (data.get("evidence_map") or [])]
        gaps = [GapItem.from_dict(g) for g in (data.get("gaps") or [])]
        strat_data = data.get("strategy") or {}
        strategy = PreparationStrategy.from_dict(strat_data) if isinstance(strat_data, dict) else PreparationStrategy()

        created_at_raw = data.get("created_at")
        created_at_dt = from_iso_utc(created_at_raw) if isinstance(created_at_raw, str) else created_at_raw or now_utc()

        return cls(
            report_id=data["report_id"],
            case_id=data["case_id"],
            opportunity_id=data["opportunity_id"],
            user_id=data["user_id"],
            schema_version=data.get("schema_version", "prep_intel_v1"),
            analyzer_version=data.get("analyzer_version", "prep_analyzer_v1"),
            evidence_map=evidence_map,
            gaps=gaps,
            strategy=strategy,
            input_hash=data.get("input_hash", ""),
            context_snapshot_hash=data.get("context_snapshot_hash", ""),
            source_evaluation_id=data.get("source_evaluation_id"),
            created_at=created_at_dt,
            version=int(data.get("version", 1)),
        )


def compute_prep_input_hash(
    case: Case,
    opportunity: Opportunity,
    mission_goal: str = "",
    mission_constraints: list[str] | None = None,
) -> str:
    """Compute deterministic input hash for preparation intelligence including Case inputs."""
    if not opportunity.metadata.get("normalized_requirements") and opportunity.requirements:
        from caseworker.domain.normalized_requirement import parse_requirements

        normalized = parse_requirements(opportunity.opportunity_id, opportunity.requirements)
        opportunity.metadata["normalized_requirements"] = [r.to_dict() for r in normalized]
    opp_hash = opportunity.compute_input_hash(mission_goal, mission_constraints)
    case_payload = [
        "prep_input_v1",
        opp_hash,
        case.case_type.value if hasattr(case.case_type, "value") else str(case.case_type),
        case.goal,
        sorted(list(case.constraints)),
        sorted(list(case.success_criteria)),
    ]
    return compute_sha256(canonical_json_dumps(case_payload))


def is_fact_or_descriptor_relevant_to_requirements(
    namespace: str,
    key: str,
    normalized_reqs: list[NormalizedRequirement],
) -> bool:
    """Check if a fact namespace or key is relevant to any normalized requirement selector."""
    from caseworker.domain.requirement_matcher import RequirementMatcher

    ns_lower = (namespace or "").lower()
    key_lower = (key or "").lower()
    key_clean = key_lower.replace("_", " ").replace("-", " ").strip()

    for req in normalized_reqs:
        relevant_namespaces = RequirementMatcher.CRITERION_TO_NAMESPACES.get(req.criterion, set())
        if ns_lower in relevant_namespaces:
            return True

        val_clean = str(req.value or "").lower().replace("_", " ").replace("-", " ").strip()
        raw_clean = (req.raw_text or "").lower().replace("_", " ").replace("-", " ").strip()

        if key_clean:
            if key_clean in raw_clean or key_clean in val_clean:
                return True
            if val_clean and val_clean in key_clean:
                return True
            tokens = [t for t in key_clean.split() if len(t) >= 3]
            if tokens and all(t in raw_clean for t in tokens):
                return True

    return False


def compute_prep_context_snapshot_hash(
    relevant_granted_facts: list[ContextFact],
    relevant_denied_descriptors: list[SafeDeniedFactDescriptor] | None = None,
) -> str:
    """Compute deterministic context dependency hash from relevant granted facts and safe denied descriptors."""
    components: list[tuple[str, int, str, str]] = []
    for f in relevant_granted_facts:
        v_status = (
            f.verification_status.value
            if hasattr(f.verification_status, "value")
            else str(f.verification_status)
        )
        components.append((f.fact_id, f.version, "granted", v_status))

    if relevant_denied_descriptors:
        for d in relevant_denied_descriptors:
            components.append((d.fact_id, d.fact_version or 0, "denied", d.reason_code))

    components.sort()
    return compute_sha256(canonical_json_dumps(components))
