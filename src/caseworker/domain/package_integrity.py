"""Shared PreparationPackage integrity validator ensuring cryptographic consistency of frozen packages."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from caseworker.domain.action import Action
from caseworker.domain.artifact import PreparedArtifact
from caseworker.domain.case import Case
from caseworker.domain.claim import Claim
from caseworker.domain.enums import ArtifactStatus, PreparationPackageStatus
from caseworker.domain.package import (
    PreparationPackage,
    compute_package_fingerprint,
)
from caseworker.domain.package_verifier import (
    PackageVerificationEngine,
    VerificationResult,
)


@dataclass(frozen=True)
class PackageIntegrityValidationResult:
    """Outcome of package integrity validation."""

    is_valid: bool
    reason: str
    findings: list[dict[str, Any]]
    artifacts: list[PreparedArtifact]
    verification_result: VerificationResult | None
    claims: list[Claim] = field(default_factory=list)


class PreparationPackageIntegrityValidator:
    """Canonical validator for PreparationPackage integrity.

    Shared between ApprovalService (human review gate) and ExecutionPlanService (action planning).
    """

    @classmethod
    def validate(
        cls,
        uow: Any,
        case: Case,
        package: PreparationPackage,
        user_id: str,
        expected_package_status: PreparationPackageStatus | None = None,
        action: Action | None = None,
    ) -> PackageIntegrityValidationResult:
        """Validate all cryptographic and semantic invariants of a PreparationPackage."""
        findings: list[dict[str, Any]] = []

        if expected_package_status is not None and package.status != expected_package_status:
            return PackageIntegrityValidationResult(
                is_valid=False,
                reason=f"Package status '{package.status.value}' does not match expected '{expected_package_status.value}'",
                findings=[],
                artifacts=[],
                verification_result=None,
            )

        # 1. Exact Artifact Payload & Snapshot Revalidation
        artifacts: list[PreparedArtifact] = []
        artifacts_by_id: dict[str, PreparedArtifact] = {}
        for snapshot in package.artifact_snapshots:
            art = uow.preparation_artifacts.get_by_id(snapshot.artifact_id)
            if art is None:
                return PackageIntegrityValidationResult(
                    is_valid=False,
                    reason=f"Artifact '{snapshot.artifact_id}' missing from database",
                    findings=[],
                    artifacts=[],
                    verification_result=None,
                )
            if art.version != snapshot.artifact_version:
                return PackageIntegrityValidationResult(
                    is_valid=False,
                    reason=(
                        f"Artifact '{snapshot.artifact_id}' version mismatch "
                        f"(expected {snapshot.artifact_version}, got {art.version})"
                    ),
                    findings=[],
                    artifacts=[],
                    verification_result=None,
                )
            if art.status != ArtifactStatus.FROZEN:
                return PackageIntegrityValidationResult(
                    is_valid=False,
                    reason=f"Artifact '{snapshot.artifact_id}' has invalid status '{art.status.value}', expected FROZEN",
                    findings=[],
                    artifacts=[],
                    verification_result=None,
                )

            current_rendered_hash = art.compute_rendered_payload_hash()
            if current_rendered_hash != snapshot.rendered_payload_hash:
                return PackageIntegrityValidationResult(
                    is_valid=False,
                    reason=(
                        f"Artifact '{snapshot.artifact_id}' rendered payload hash mismatch "
                        f"(expected {snapshot.rendered_payload_hash}, got {current_rendered_hash})"
                    ),
                    findings=[],
                    artifacts=[],
                    verification_result=None,
                )

            if art.grounding_snapshot_hash != snapshot.grounding_snapshot_hash:
                return PackageIntegrityValidationResult(
                    is_valid=False,
                    reason=(
                        f"Artifact '{snapshot.artifact_id}' grounding snapshot hash mismatch "
                        f"(expected {snapshot.grounding_snapshot_hash}, got {art.grounding_snapshot_hash})"
                    ),
                    findings=[],
                    artifacts=[],
                    verification_result=None,
                )

            expected_mat_type = (
                art.material_type.value if hasattr(art.material_type, "value") else str(art.material_type)
            )
            if snapshot.material_type != expected_mat_type:
                return PackageIntegrityValidationResult(
                    is_valid=False,
                    reason=(
                        f"Artifact '{snapshot.artifact_id}' material_type mismatch "
                        f"(expected '{expected_mat_type}', got '{snapshot.material_type}')"
                    ),
                    findings=[],
                    artifacts=[],
                    verification_result=None,
                )

            expected_role = PackageVerificationEngine.get_publication_role(art.material_type)
            expected_role_str = expected_role.value if hasattr(expected_role, "value") else str(expected_role)
            if snapshot.publication_role != expected_role_str:
                return PackageIntegrityValidationResult(
                    is_valid=False,
                    reason=(
                        f"Artifact '{snapshot.artifact_id}' publication_role mismatch "
                        f"(expected '{expected_role_str}', got '{snapshot.publication_role}')"
                    ),
                    findings=[],
                    artifacts=[],
                    verification_result=None,
                )

            artifacts.append(art)
            artifacts_by_id[art.artifact_id] = art

        if set(package.artifact_ids) != set(artifacts_by_id.keys()):
            return PackageIntegrityValidationResult(
                is_valid=False,
                reason="Package artifact_ids does not match artifact_snapshots set",
                findings=[],
                artifacts=[],
                verification_result=None,
            )

        # 2. Latest report check
        latest_report = uow.preparation_reports.get_latest_for_case(case.case_id)
        if latest_report is None or latest_report.report_id != package.source_report_id:
            return PackageIntegrityValidationResult(
                is_valid=False,
                reason="Preparation report is missing or newer report exists",
                findings=[],
                artifacts=artifacts,
                verification_result=None,
            )

        # Check if report is stale (e.g. newer claims or facts)
        from caseworker.services.package_service import PackageService
        if PackageService.is_report_stale_in_uow(uow, case, latest_report):
            return PackageIntegrityValidationResult(
                is_valid=False,
                reason="Source preparation intelligence report has become stale",
                findings=[],
                artifacts=artifacts,
                verification_result=None,
            )

        # 3. Claims & Facts verification engine check
        claims = uow.claims.list_by_case(case.case_id)
        claims_by_id = {c.claim_id: c for c in claims}
        facts = uow.context.list_active(user_id=user_id)
        facts_by_id = {f.fact_id: f for f in facts}

        v_result = PackageVerificationEngine.verify(
            case_id=case.case_id,
            artifact_set_id=package.artifact_set_id,
            artifacts=artifacts,
            latest_report=latest_report,
            is_report_stale=False,
            claims_by_id=claims_by_id,
            facts_by_id=facts_by_id,
            allow_frozen_artifacts=True,
        )
        findings = [f.to_dict() for f in v_result.findings]

        if not v_result.passed:
            return PackageIntegrityValidationResult(
                is_valid=False,
                reason="Package verification engine checks failed",
                findings=findings,
                artifacts=artifacts,
                verification_result=v_result,
            )
        if v_result.claim_snapshots != package.claim_snapshots:
            return PackageIntegrityValidationResult(
                is_valid=False,
                reason="Package claim snapshots mismatch against current verification result",
                findings=findings,
                artifacts=artifacts,
                verification_result=v_result,
            )
        if v_result.rendered_payload_hashes != package.rendered_payload_hashes:
            return PackageIntegrityValidationResult(
                is_valid=False,
                reason="Package rendered payload hashes mismatch against current verification result",
                findings=findings,
                artifacts=artifacts,
                verification_result=v_result,
            )
        if v_result.artifact_snapshots != package.artifact_snapshots:
            return PackageIntegrityValidationResult(
                is_valid=False,
                reason="Package artifact snapshots mismatch against current verification result",
                findings=findings,
                artifacts=artifacts,
                verification_result=v_result,
            )

        # 4. Fingerprint check
        computed_fp = package.compute_fingerprint()
        live_fp = compute_package_fingerprint(
            case_id=case.case_id,
            artifact_set_id=package.artifact_set_id,
            source_report_id=package.source_report_id,
            artifact_snapshots=v_result.artifact_snapshots,
            claim_snapshots=v_result.claim_snapshots,
            schema_version=package.schema_version,
            verifier_version=package.verifier_version,
        )
        if computed_fp != package.package_fingerprint or live_fp != package.package_fingerprint:
            return PackageIntegrityValidationResult(
                is_valid=False,
                reason="Package content fingerprint mismatch",
                findings=findings,
                artifacts=artifacts,
                verification_result=v_result,
            )

        # 5. Semantic Action / Package Binding Check (if action provided)
        if action is not None:
            params = action.parameters or {}
            if (
                action.case_id != package.case_id
                or params.get("package_id") != package.package_id
                or params.get("package_fingerprint") != package.package_fingerprint
                or params.get("artifact_set_id") != package.artifact_set_id
                or params.get("source_report_id") != package.source_report_id
                or params.get("case_id") != package.case_id
            ):
                return PackageIntegrityValidationResult(
                    is_valid=False,
                    reason="Action parameters do not match preparation package semantic binding",
                    findings=findings,
                    artifacts=artifacts,
                    verification_result=v_result,
                )

        return PackageIntegrityValidationResult(
            is_valid=True,
            reason="",
            findings=findings,
            artifacts=artifacts,
            verification_result=v_result,
            claims=claims,
        )
