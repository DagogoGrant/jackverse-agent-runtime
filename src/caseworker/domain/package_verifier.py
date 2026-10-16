"""Deterministic pre-verification engine for JackVerse preparation packages."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from caseworker.domain.artifact import PreparedArtifact, compute_artifact_grounding_hash, compute_artifact_staleness
from caseworker.domain.artifact_planner import PLANNER_VERSION
from caseworker.domain.claim import Claim, compute_support_snapshot_hash
from caseworker.domain.context import ContextFact
from caseworker.domain.enums import (
    ArtifactPublicationRole,
    ArtifactStatus,
    ClaimStatus,
    PackageVerificationStatus,
    PreparationMaterialType,
)
from caseworker.domain.errors import PackageVerificationError
from caseworker.domain.package import (
    ArtifactPackageSnapshot,
    SafeClaimSnapshot,
    VERIFIER_VERSION,
)
from caseworker.domain.preparation import PreparationIntelligenceReport
from caseworker.domain.requirement_matcher import is_fact_verified
from caseworker.domain.types import compute_sha256


@dataclass(frozen=True)
class PackageVerificationFinding:
    """Detailed finding from a single verification check."""

    check_name: str
    passed: bool
    severity: str  # "error", "warning", "info"
    message: str
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "check_name": self.check_name,
            "passed": self.passed,
            "severity": self.severity,
            "message": self.message,
            "details": dict(self.details),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> PackageVerificationFinding:
        return cls(
            check_name=data["check_name"],
            passed=bool(data["passed"]),
            severity=data.get("severity", "error"),
            message=data.get("message", ""),
            details=dict(data.get("details") or {}),
        )


@dataclass
class VerificationResult:
    """Complete pre-verification assessment of an artifact set."""

    passed: bool
    status: PackageVerificationStatus
    findings: list[PackageVerificationFinding]
    rendered_payload_hashes: dict[str, str]
    artifact_snapshots: list[ArtifactPackageSnapshot]
    claim_snapshots: list[SafeClaimSnapshot]


class PackageVerificationEngine:
    """Pre-verification engine executing 7 strict deterministic checks before packaging."""

    VERIFIER_VERSION = VERIFIER_VERSION

    @classmethod
    def get_publication_role(cls, material_type: PreparationMaterialType | str) -> ArtifactPublicationRole:
        """Resolve publication role for an artifact material type."""
        mat_str = material_type.value if hasattr(material_type, "value") else str(material_type)
        if mat_str == PreparationMaterialType.SCREENING_ANSWERS.value:
            return ArtifactPublicationRole.FIELD_RESPONSE
        if mat_str in (
            PreparationMaterialType.APPLICATION_ANSWER_BANK.value,
            PreparationMaterialType.ANSWER_BANK.value,
        ):
            return ArtifactPublicationRole.INTERNAL_PREPARATION
        return ArtifactPublicationRole.SUBMISSION_MATERIAL

    @classmethod
    def verify(
        cls,
        case_id: str,
        artifact_set_id: str,
        artifacts: list[PreparedArtifact],
        latest_report: PreparationIntelligenceReport | None,
        is_report_stale: bool,
        claims_by_id: dict[str, Claim],
        facts_by_id: dict[str, ContextFact],
        allow_frozen_artifacts: bool = False,
    ) -> VerificationResult:
        """Execute all 7 checks deterministically.

        Guarantees:
        - Side-effect free: Does not mutate artifacts, claims, or database state.
        - Screening Answers vs Answer Bank differentiation: Unresolved screening questions fail
          verification; unresolved answer bank entries yield warnings.
        """
        findings: list[PackageVerificationFinding] = []
        overall_passed = True

        # Check 1: Artifact Set Coherence
        if not artifacts:
            findings.append(
                PackageVerificationFinding(
                    check_name="artifact_set_coherence",
                    passed=False,
                    severity="error",
                    message=f"No artifacts found for artifact_set_id '{artifact_set_id}'.",
                    details={"artifact_set_id": artifact_set_id},
                )
            )
            return VerificationResult(
                passed=False,
                status=PackageVerificationStatus.FAILED,
                findings=findings,
                rendered_payload_hashes={},
                artifact_snapshots=[],
                claim_snapshots=[],
            )

        set_id_mismatch = [a.artifact_id for a in artifacts if a.artifact_set_id != artifact_set_id]
        case_id_mismatch = [a.artifact_id for a in artifacts if a.case_id != case_id]
        if set_id_mismatch or case_id_mismatch:
            overall_passed = False
            findings.append(
                PackageVerificationFinding(
                    check_name="artifact_set_coherence",
                    passed=False,
                    severity="error",
                    message="Artifacts in set have mismatched artifact_set_id or case_id.",
                    details={
                        "set_id_mismatches": set_id_mismatch,
                        "case_id_mismatches": case_id_mismatch,
                    },
                )
            )
        else:
            findings.append(
                PackageVerificationFinding(
                    check_name="artifact_set_coherence",
                    passed=True,
                    severity="info",
                    message=f"All {len(artifacts)} artifacts belong coherently to artifact_set_id '{artifact_set_id}'.",
                )
            )

        # Check 2: Source Report Freshness
        source_report_ids = {a.source_report_id for a in artifacts}
        if len(source_report_ids) > 1:
            overall_passed = False
            findings.append(
                PackageVerificationFinding(
                    check_name="source_report_freshness",
                    passed=False,
                    severity="error",
                    message="Artifacts in set reference conflicting source_report_ids.",
                    details={"referenced_report_ids": list(source_report_ids)},
                )
            )
        else:
            expected_report_id = next(iter(source_report_ids))
            if latest_report is None or latest_report.report_id != expected_report_id:
                overall_passed = False
                findings.append(
                    PackageVerificationFinding(
                        check_name="source_report_freshness",
                        passed=False,
                        severity="error",
                        message="Latest preparation intelligence report is missing or newer report exists.",
                        details={
                            "artifact_report_id": expected_report_id,
                            "latest_report_id": getattr(latest_report, "report_id", None),
                        },
                    )
                )
            elif is_report_stale:
                overall_passed = False
                findings.append(
                    PackageVerificationFinding(
                        check_name="source_report_freshness",
                        passed=False,
                        severity="error",
                        message="Source preparation intelligence report is stale.",
                        details={"report_id": latest_report.report_id},
                    )
                )
            else:
                findings.append(
                    PackageVerificationFinding(
                        check_name="source_report_freshness",
                        passed=True,
                        severity="info",
                        message=f"Source preparation report '{latest_report.report_id}' is active and fresh.",
                    )
                )

        # Check 3: Material Set Completeness & Exact Cardinality
        cv_artifacts = [a for a in artifacts if (a.material_type.value if hasattr(a.material_type, "value") else str(a.material_type)) == PreparationMaterialType.CV.value]
        cl_artifacts = [a for a in artifacts if (a.material_type.value if hasattr(a.material_type, "value") else str(a.material_type)) == PreparationMaterialType.COVER_LETTER.value]
        screening_artifacts = [a for a in artifacts if (a.material_type.value if hasattr(a.material_type, "value") else str(a.material_type)) == PreparationMaterialType.SCREENING_ANSWERS.value]
        bank_artifacts = [a for a in artifacts if (a.material_type.value if hasattr(a.material_type, "value") else str(a.material_type)) in (
            PreparationMaterialType.APPLICATION_ANSWER_BANK.value,
            PreparationMaterialType.ANSWER_BANK.value,
        )]

        cardinality_errors: list[str] = []
        if len(cv_artifacts) == 0:
            cardinality_errors.append("Missing CV (exactly 1 required)")
        elif len(cv_artifacts) > 1:
            cardinality_errors.append(f"Duplicate CV artifacts detected ({len(cv_artifacts)} found, exactly 1 required)")

        if len(cl_artifacts) == 0:
            cardinality_errors.append("Missing COVER_LETTER (exactly 1 required)")
        elif len(cl_artifacts) > 1:
            cardinality_errors.append(f"Duplicate COVER_LETTER artifacts detected ({len(cl_artifacts)} found, exactly 1 required)")

        total_qa_count = len(screening_artifacts) + len(bank_artifacts)
        if total_qa_count == 0:
            cardinality_errors.append("Missing SCREENING_ANSWERS or APPLICATION_ANSWER_BANK (exactly 1 required)")
        elif len(screening_artifacts) > 0 and len(bank_artifacts) > 0:
            cardinality_errors.append(
                f"Conflicting question materials: set cannot contain both SCREENING_ANSWERS ({len(screening_artifacts)}) and APPLICATION_ANSWER_BANK ({len(bank_artifacts)})"
            )
        elif len(screening_artifacts) > 1:
            cardinality_errors.append(f"Duplicate SCREENING_ANSWERS artifacts detected ({len(screening_artifacts)} found, exactly 1 required)")
        elif len(bank_artifacts) > 1:
            cardinality_errors.append(f"Duplicate APPLICATION_ANSWER_BANK artifacts detected ({len(bank_artifacts)} found, exactly 1 required)")

        present_types = [a.material_type.value if hasattr(a.material_type, "value") else str(a.material_type) for a in artifacts]
        if cardinality_errors:
            overall_passed = False
            findings.append(
                PackageVerificationFinding(
                    check_name="material_set_completeness",
                    passed=False,
                    severity="error",
                    message=f"Material set cardinality violation: {'; '.join(cardinality_errors)}.",
                    details={
                        "cardinality_errors": cardinality_errors,
                        "present": present_types,
                        "counts": {
                            "cv": len(cv_artifacts),
                            "cover_letter": len(cl_artifacts),
                            "screening_answers": len(screening_artifacts),
                            "application_answer_bank": len(bank_artifacts),
                        },
                    },
                )
            )
        else:
            findings.append(
                PackageVerificationFinding(
                    check_name="material_set_completeness",
                    passed=True,
                    severity="info",
                    message="All required pursuit materials meet exact cardinality (1 CV, 1 Cover Letter, 1 Q&A material).",
                    details={"materials": present_types},
                )
            )

        # Check 4: Artifact Freshness & Status
        stale_artifacts: list[tuple[str, str]] = []
        for artifact in artifacts:
            # Artifact status check
            allowed_statuses = (ArtifactStatus.DRAFT, ArtifactStatus.FROZEN) if allow_frozen_artifacts else (ArtifactStatus.DRAFT,)
            if artifact.status not in allowed_statuses:
                stale_artifacts.append((artifact.artifact_id, f"Invalid status: {artifact.status.value}"))
                continue

            stale, reason = compute_artifact_staleness(
                artifact=artifact,
                latest_report=latest_report,
                is_report_stale=is_report_stale,
                claims_by_id=claims_by_id,
                facts_by_id=facts_by_id,
                current_planner_version=PLANNER_VERSION,
            )
            if stale:
                stale_artifacts.append((artifact.artifact_id, reason or "Stale grounding dependencies"))

        if stale_artifacts:
            overall_passed = False
            findings.append(
                PackageVerificationFinding(
                    check_name="artifact_freshness",
                    passed=False,
                    severity="error",
                    message=f"{len(stale_artifacts)} artifact(s) in set are stale or in invalid lifecycle state.",
                    details={"stale_artifacts": stale_artifacts},
                )
            )
        else:
            findings.append(
                PackageVerificationFinding(
                    check_name="artifact_freshness",
                    passed=True,
                    severity="info",
                    message="All artifacts in set are current and substantiated.",
                )
            )

        # Check 5: Questionnaire Completeness (Screening Answers vs Answer Bank)
        screening_unresolved: list[dict[str, Any]] = []
        answer_bank_unresolved: list[dict[str, Any]] = []

        for artifact in artifacts:
            mat_type_str = artifact.material_type.value if hasattr(artifact.material_type, "value") else str(artifact.material_type)
            is_actual_screening = mat_type_str == PreparationMaterialType.SCREENING_ANSWERS.value
            is_answer_bank = mat_type_str in (
                PreparationMaterialType.APPLICATION_ANSWER_BANK.value,
                PreparationMaterialType.ANSWER_BANK.value,
            )

            if is_actual_screening or is_answer_bank:
                for b in artifact.blocks:
                    status = b.metadata.get("answer_status")
                    answer = b.metadata.get("answer")
                    question = b.metadata.get("question", b.content[:60])
                    if status == "NEEDS_USER_INPUT" or not answer or not str(answer).strip():
                        item = {"artifact_id": artifact.artifact_id, "question": question}
                        if is_actual_screening:
                            screening_unresolved.append(item)
                        else:
                            answer_bank_unresolved.append(item)

        if screening_unresolved:
            overall_passed = False
            findings.append(
                PackageVerificationFinding(
                    check_name="questionnaire_completeness",
                    passed=False,
                    severity="error",
                    message=f"Screening questionnaire contains {len(screening_unresolved)} unresolved required question(s).",
                    details={"unresolved_screening": screening_unresolved},
                )
            )
        elif answer_bank_unresolved:
            # Answer bank is internal preparation aid: warnings only, NOT a package blocker!
            findings.append(
                PackageVerificationFinding(
                    check_name="questionnaire_completeness",
                    passed=True,
                    severity="warning",
                    message=f"Application Answer Bank has {len(answer_bank_unresolved)} unpopulated potential entry/entries (internal preparation only).",
                    details={"unresolved_internal_entries": answer_bank_unresolved},
                )
            )
        else:
            findings.append(
                PackageVerificationFinding(
                    check_name="questionnaire_completeness",
                    passed=True,
                    severity="info",
                    message="All employer screening questionnaire items are fully answered.",
                )
            )

        # Check 6: Claim Grounding & Verification Invariants
        referenced_claim_ids: set[str] = set()
        for artifact in artifacts:
            for b in artifact.blocks:
                for cid in b.claim_ids:
                    referenced_claim_ids.add(cid)

        invalid_claims: list[dict[str, Any]] = []
        claim_snapshots: list[SafeClaimSnapshot] = []

        for cid in sorted(referenced_claim_ids):
            claim = claims_by_id.get(cid)
            if claim is None:
                invalid_claims.append({"claim_id": cid, "reason": "Claim not found in case ledger"})
                continue

            status_val = claim.status.value if hasattr(claim.status, "value") else str(claim.status)
            if status_val != ClaimStatus.SUPPORTED.value:
                invalid_claims.append({"claim_id": cid, "reason": f"Claim status is '{status_val}', expected 'supported'"})
                continue

            # Verify supporting facts
            missing_or_unverified_facts = []
            matching_facts = []
            for fid in getattr(claim, "supporting_fact_ids", []):
                fact = facts_by_id.get(fid)
                if fact is None or getattr(fact, "superseded_by_fact_id", None) is not None:
                    missing_or_unverified_facts.append((fid, "Fact missing or superseded"))
                elif not is_fact_verified(getattr(fact, "verification_status", None)):
                    missing_or_unverified_facts.append((fid, "Fact not verified"))
                elif "job_application" not in (getattr(fact, "allowed_purposes", []) or []):
                    missing_or_unverified_facts.append((fid, "Lacks purpose 'job_application'"))
                else:
                    matching_facts.append(fact)

            if missing_or_unverified_facts:
                invalid_claims.append({
                    "claim_id": cid,
                    "reason": f"Supporting facts defective: {missing_or_unverified_facts}",
                })
                continue

            # Check support snapshot hash integrity
            current_snapshot_hash = compute_support_snapshot_hash(matching_facts)
            if claim.support_snapshot_hash and claim.support_snapshot_hash != current_snapshot_hash:
                invalid_claims.append({
                    "claim_id": cid,
                    "reason": "Claim support snapshot hash changed since synthesis",
                })
                continue

            # Compute safe assertion hash
            assertion_hash = compute_sha256(claim.assertion_json.strip()) if claim.assertion_json else None
            claim_snapshots.append(
                SafeClaimSnapshot(
                    claim_id=claim.claim_id,
                    claim_version=claim.version,
                    claim_status=status_val,
                    support_snapshot_hash=claim.support_snapshot_hash,
                    assertion_hash=assertion_hash,
                )
            )

        if invalid_claims:
            overall_passed = False
            findings.append(
                PackageVerificationFinding(
                    check_name="claim_grounding_validity",
                    passed=False,
                    severity="error",
                    message=f"{len(invalid_claims)} referenced claim(s) failed support or verification checks.",
                    details={"invalid_claims": invalid_claims},
                )
            )
        else:
            findings.append(
                PackageVerificationFinding(
                    check_name="claim_grounding_validity",
                    passed=True,
                    severity="info",
                    message=f"All {len(claim_snapshots)} referenced claims are SUPPORTED by active verified facts.",
                )
            )

        # Check 7: Rendered Payload Hashing & Snapshots
        rendered_payload_hashes: dict[str, str] = {}
        artifact_snapshots: list[ArtifactPackageSnapshot] = []

        for artifact in sorted(artifacts, key=lambda a: (a.material_type.value, a.artifact_id)):
            rendered_md = artifact.render_markdown()
            payload_hash = compute_sha256(rendered_md)
            mat_type_str = artifact.material_type.value if hasattr(artifact.material_type, "value") else str(artifact.material_type)
            rendered_payload_hashes[mat_type_str] = payload_hash
            pub_role = cls.get_publication_role(artifact.material_type)

            artifact_snapshots.append(
                ArtifactPackageSnapshot(
                    artifact_id=artifact.artifact_id,
                    # Note: caller freeze order determines post-freeze artifact version
                    artifact_version=artifact.version,
                    material_type=mat_type_str,
                    rendered_payload_hash=payload_hash,
                    grounding_snapshot_hash=artifact.grounding_snapshot_hash,
                    publication_role=pub_role.value,
                )
            )

        findings.append(
            PackageVerificationFinding(
                check_name="rendered_payload_hashing",
                passed=True,
                severity="info",
                message=f"Rendered markdown payloads deterministically hashed for {len(artifact_snapshots)} artifacts.",
            )
        )

        return VerificationResult(
            passed=overall_passed,
            status=PackageVerificationStatus.VERIFIED if overall_passed else PackageVerificationStatus.FAILED,
            findings=findings,
            rendered_payload_hashes=rendered_payload_hashes,
            artifact_snapshots=artifact_snapshots,
            claim_snapshots=claim_snapshots,
        )
