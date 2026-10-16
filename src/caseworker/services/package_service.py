"""Application service managing PreparationPackage verification, freezing, and approval orchestration."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
import uuid

from caseworker.domain.action import Action
from caseworker.domain.action_policy import ActionPolicy
from caseworker.domain.approval import Approval
from caseworker.domain.artifact import (
    ArtifactBlock,
    PreparedArtifact,
    compute_artifact_grounding_hash,
)
from caseworker.domain.case import Case
from caseworker.domain.context import ContextFact
from caseworker.domain.context_package import ContextAccessPolicy, ContextPackageBuilder
from caseworker.domain.enums import (
    ActionStatus,
    ActionType,
    ApprovalStatus,
    ArtifactPublicationRole,
    ArtifactStatus,
    CaseStatus,
    PackageVerificationStatus,
    PreparationMaterialType,
    PreparationPackageStatus,
    VerificationStatus,
)
from caseworker.domain.errors import (
    EntityNotFoundError,
    InvalidStateTransitionError,
    OptimisticLockError,
    PackageVerificationError,
)
from caseworker.domain.events import (
    make_action_proposed_event,
    make_action_status_changed_event,
    make_approval_requested_event,
    make_case_status_changed_event,
    make_preparation_package_verified_event,
)
from caseworker.domain.opportunity import Opportunity
from caseworker.domain.package import (
    ArtifactPackageSnapshot,
    PreparationPackage,
    compute_package_fingerprint,
)
from caseworker.domain.package_verifier import (
    PackageVerificationEngine,
    PackageVerificationFinding,
    VerificationResult,
)
from caseworker.domain.preparation_analyzer import PreparationAnalyzer
from caseworker.domain.preparation import (
    is_fact_or_descriptor_relevant_to_requirements,
    SafeDeniedFactDescriptor,
        PreparationIntelligenceReport,
    compute_prep_context_snapshot_hash,
    compute_prep_input_hash,
)
from caseworker.domain.normalized_requirement import NormalizedRequirement, parse_requirements
from caseworker.domain.types import now_utc

if TYPE_CHECKING:
    from caseworker.persistence.sqlite import SQLiteCaseworkerStorage


class PackageService:
    """Application service for deterministic preparation package verification and review preparation."""

    def __init__(self, storage: SQLiteCaseworkerStorage) -> None:
        self.storage = storage

    @staticmethod
    def is_report_stale_in_uow(
        uow: Any,
        case: Case,
        report: PreparationIntelligenceReport,
        all_facts: list[ContextFact] | None = None,
        opportunity: Opportunity | None = None,
    ) -> bool:
        """Evaluate if the intelligence report has become stale relative to active facts or opportunity."""
        if opportunity is None:
            opportunity = uow.opportunities.get_by_converted_case_id(case.case_id)
            if opportunity is None and case.opportunity_id:
                opportunity = uow.opportunities.get_by_id(case.opportunity_id)
        if opportunity is None:
            return True

        mission_goal = ""
        mission_constraints: list[str] = []
        if case.mission_id:
            mission = uow.missions.get_by_id(case.mission_id)
            if mission:
                mission_goal = mission.goal
                mission_constraints = list(mission.constraints)

        if all_facts is None:
            all_facts = uow.context.list_active(user_id=case.user_id)

        prep_builder = ContextPackageBuilder(ContextAccessPolicy())
        prep_package, prep_granted, prep_denied = prep_builder.build_with_report(
            user_id=case.user_id,
            purpose="case_preparation",
            available_facts=all_facts,
        )

        raw_normalized = opportunity.metadata.get("normalized_requirements")
        if raw_normalized:
            normalized_reqs = [NormalizedRequirement.from_dict(d) for d in raw_normalized]
        else:
            normalized_reqs = parse_requirements(opportunity.opportunity_id, opportunity.requirements)

        current_input_hash = compute_prep_input_hash(case, opportunity, mission_goal, mission_constraints)
        relevant_granted = [
            f for f in prep_package.facts
            if is_fact_or_descriptor_relevant_to_requirements(f.namespace, f.key, normalized_reqs)
        ]
        facts_by_id_all = {f.fact_id: f for f in all_facts}
        safe_denied_desc = [
            SafeDeniedFactDescriptor(
                fact_id=d.fact_id,
                fact_version=facts_by_id_all[d.fact_id].version if d.fact_id in facts_by_id_all else None,
                namespace=d.namespace or (facts_by_id_all[d.fact_id].namespace if d.fact_id in facts_by_id_all else ""),
                key=d.key or (facts_by_id_all[d.fact_id].key if d.fact_id in facts_by_id_all else ""),
                verification_status=facts_by_id_all[d.fact_id].verification_status if d.fact_id in facts_by_id_all else None,
                reason_code=d.reason_code,
                reason=d.reason,
            )
            for d in prep_denied
        ]
        relevant_denied = [
            d for d in safe_denied_desc
            if is_fact_or_descriptor_relevant_to_requirements(d.namespace, d.key, normalized_reqs)
        ]
        current_context_hash = compute_prep_context_snapshot_hash(relevant_granted, relevant_denied)
        eval_meta = opportunity.metadata.get("evaluation") or {}
        current_eval_id = eval_meta.get("evaluation_id") or opportunity.metadata.get("latest_evaluation_id")

        return report.is_stale(
            current_input_hash=current_input_hash,
            current_context_hash=current_context_hash,
            current_opportunity_evaluation_id=current_eval_id,
            current_analyzer_version=PreparationAnalyzer.ANALYZER_VERSION,
            current_schema_version=PreparationAnalyzer.SCHEMA_VERSION,
        )

    def get_existing_package_bundle(
        self,
        user_id: str,
        case_id: str,
        artifact_set_id: str,
    ) -> tuple[PreparationPackage, Action, Approval, list[dict[str, Any]], Case] | None:
        """Check whether user_id, case_id, and artifact_set_id already have a completed verification bundle."""
        with self.storage.unit_of_work() as uow:
            case = uow.cases.get_by_id(case_id)
            if case is None or case.user_id != user_id:
                return None

            existing_packages = uow.preparation_packages.list_by_set(artifact_set_id)
            for existing_pkg in existing_packages:
                if (
                    existing_pkg.user_id == user_id
                    and existing_pkg.case_id == case_id
                    and existing_pkg.artifact_set_id == artifact_set_id
                    and existing_pkg.status in (PreparationPackageStatus.FROZEN, PreparationPackageStatus.APPROVED)
                ):
                    act_fp_key = f"pkg_act:{existing_pkg.package_fingerprint}"
                    action = uow.actions.get_by_idempotency_key(existing_pkg.case_id, act_fp_key)
                    if action is not None:
                        approval = uow.approvals.get_by_action_id(action.action_id)
                        if approval is not None:
                            return (
                                existing_pkg,
                                action,
                                approval,
                                [f if isinstance(f, dict) else f.to_dict() for f in existing_pkg.verification_findings],
                                case,
                            )
            return None

    def verify_and_prepare_package(
        self,
        user_id: str,
        case_id: str,
        artifact_set_id: str,
        expected_case_version: int | None = None,
    ) -> tuple[PreparationPackage, Action, Approval, list[dict[str, Any]], Case]:
        """Atomically verify DRAFT artifacts, freeze them, persist package, and request human approval."""
        with self.storage.unit_of_work() as uow:
            case = uow.cases.get_by_id(case_id)
            if case is None or case.user_id != user_id:
                raise EntityNotFoundError("Case", case_id)

            # Scoped idempotency check: must match user_id, case_id, and artifact_set_id
            existing_packages = uow.preparation_packages.list_by_set(artifact_set_id)
            for existing_pkg in existing_packages:
                if (
                    existing_pkg.user_id == user_id
                    and existing_pkg.case_id == case_id
                    and existing_pkg.artifact_set_id == artifact_set_id
                    and existing_pkg.status in (PreparationPackageStatus.FROZEN, PreparationPackageStatus.APPROVED)
                ):
                    act_fp_key = f"pkg_act:{existing_pkg.package_fingerprint}"
                    action = uow.actions.get_by_idempotency_key(existing_pkg.case_id, act_fp_key)
                    if action is not None:
                        approval = uow.approvals.get_by_action_id(action.action_id)
                        if approval is not None:
                            return (
                                existing_pkg,
                                action,
                                approval,
                                [f if isinstance(f, dict) else f.to_dict() for f in existing_pkg.verification_findings],
                                case,
                            )

            # Strict PLANNING gate: new verification must begin from PLANNING status
            if case.status != CaseStatus.PLANNING:
                raise InvalidStateTransitionError(
                    "Case",
                    case.status.value,
                    CaseStatus.AWAITING_APPROVAL.value,
                    f"A new preparation package verification can only begin from PLANNING status (current: {case.status.value}).",
                )

            if expected_case_version is not None and case.version != expected_case_version:
                raise OptimisticLockError("Case", case_id, expected_case_version)

            # 1. Fetch Candidate Artifacts with strict tenant and case isolation
            all_set_artifacts = uow.preparation_artifacts.list_by_set(artifact_set_id)
            if not all_set_artifacts:
                finding = PackageVerificationFinding(
                    check_name="artifact_set_coherence",
                    passed=False,
                    severity="error",
                    message=f"No artifacts found for artifact_set_id '{artifact_set_id}'.",
                    details={"artifact_set_id": artifact_set_id},
                )
                raise PackageVerificationError(
                    f"No artifacts found for artifact_set_id '{artifact_set_id}'.",
                    findings=[finding],
                )

            user_mismatches = [a.artifact_id for a in all_set_artifacts if a.user_id != user_id]
            case_mismatches = [a.artifact_id for a in all_set_artifacts if a.case_id != case_id]
            if user_mismatches or case_mismatches:
                finding = PackageVerificationFinding(
                    check_name="artifact_set_coherence",
                    passed=False,
                    severity="error",
                    message="Candidate artifacts violate tenant or case isolation.",
                    details={"user_mismatches": user_mismatches, "case_mismatches": case_mismatches},
                )
                raise PackageVerificationError(
                    "Candidate artifacts violate tenant/case isolation.",
                    findings=[finding],
                )
            artifacts = all_set_artifacts

            # 2. Fetch Dependencies for Verification
            latest_report = uow.preparation_reports.get_latest_for_case(case_id)
            is_stale = self.is_report_stale_in_uow(uow, case, latest_report) if latest_report else True
            claims = uow.claims.list_by_case(case_id)
            claims_by_id = {c.claim_id: c for c in claims}
            facts = uow.context.list_active(user_id=user_id)
            facts_by_id = {f.fact_id: f for f in facts}

            # 3. Execute 7 Pre-verification checks
            result: VerificationResult = PackageVerificationEngine.verify(
                case_id=case_id,
                artifact_set_id=artifact_set_id,
                artifacts=artifacts,
                latest_report=latest_report,
                is_report_stale=is_stale,
                claims_by_id=claims_by_id,
                facts_by_id=facts_by_id,
                allow_frozen_artifacts=False,
            )

            # Side-effect-free failure: do NOT freeze or create package if checks fail
            if not result.passed:
                raise PackageVerificationError(
                    "Preparation package pre-verification failed.",
                    findings=result.findings,
                )

            assert latest_report is not None  # Guaranteed by passing result.passed check

            # 4. Freeze Order (Amendment 3)
            # Freeze selected artifacts and persist new FROZEN versions
            frozen_artifacts: list[PreparedArtifact] = []
            for art in artifacts:
                art.freeze(reason="Verified as part of preparation package")
                uow.preparation_artifacts.save(art)
                frozen_artifacts.append(art)

            # Build post-freeze snapshots
            sorted_frozen = sorted(
                frozen_artifacts,
                key=lambda x: (
                    x.material_type.value if hasattr(x.material_type, "value") else str(x.material_type),
                    x.artifact_id,
                ),
            )
            frozen_artifact_ids: list[str] = []
            frozen_artifact_versions: dict[str, int] = {}
            frozen_artifact_snapshots: list[ArtifactPackageSnapshot] = []
            frozen_rendered_hashes: dict[str, str] = {}

            for a in sorted_frozen:
                role = PackageVerificationEngine.get_publication_role(a.material_type)
                rendered_hash = a.compute_rendered_payload_hash()
                snap = ArtifactPackageSnapshot(
                    artifact_id=a.artifact_id,
                    artifact_version=a.version,
                    material_type=a.material_type.value if hasattr(a.material_type, "value") else str(a.material_type),
                    rendered_payload_hash=rendered_hash,
                    grounding_snapshot_hash=a.grounding_snapshot_hash,
                    publication_role=role.value if hasattr(role, "value") else str(role),
                )
                frozen_artifact_ids.append(a.artifact_id)
                frozen_artifact_versions[a.artifact_id] = a.version
                frozen_artifact_snapshots.append(snap)
                frozen_rendered_hashes[a.artifact_id] = rendered_hash

            # Compute content-addressed package fingerprint
            package_fingerprint = compute_package_fingerprint(
                case_id=case_id,
                artifact_set_id=artifact_set_id,
                source_report_id=latest_report.report_id,
                artifact_snapshots=frozen_artifact_snapshots,
                claim_snapshots=result.claim_snapshots,
            )

            package = PreparationPackage(
                package_id=f"pkg_{uuid.uuid4().hex[:12]}",
                case_id=case_id,
                artifact_set_id=artifact_set_id,
                source_report_id=latest_report.report_id,
                user_id=user_id,
                status=PreparationPackageStatus.FROZEN,
                artifact_ids=frozen_artifact_ids,
                artifact_versions=frozen_artifact_versions,
                artifact_snapshots=frozen_artifact_snapshots,
                claim_snapshots=result.claim_snapshots,
                rendered_payload_hashes=frozen_rendered_hashes,
                verification_status=PackageVerificationStatus.VERIFIED,
                verification_findings=[f.to_dict() for f in result.findings],
                frozen_at=now_utc(),
                package_fingerprint=package_fingerprint,
            )
            uow.preparation_packages.save(package)

            uow.events.append(
                make_preparation_package_verified_event(
                    package_id=package.package_id,
                    case_id=case_id,
                    artifact_set_id=artifact_set_id,
                    source_report_id=latest_report.report_id,
                    package_fingerprint=package.package_fingerprint,
                    user_id=user_id,
                    aggregate_version=package.version,
                    artifact_count=len(package.artifact_ids),
                    claim_count=len(package.claim_snapshots),
                )
            )

            # 5. Propose APPROVE_PREPARATION_PACKAGE Action & Request Approval
            decision = ActionPolicy.evaluate(ActionType.APPROVE_PREPARATION_PACKAGE)
            action_idempotency_key = f"pkg_act:{package.package_fingerprint}"
            description = (
                f"Local human verification approval for preparation package {package.package_id} "
                f"({len(package.artifact_ids)} materials, fingerprint {package.package_fingerprint[:12]}...)"
            )

            action = Action(
                action_id=f"act_{uuid.uuid4().hex[:12]}",
                case_id=case_id,
                action_type=ActionType.APPROVE_PREPARATION_PACKAGE,
                description=description,
                risk_level=decision.risk_level,
                requires_approval=decision.requires_approval,
                parameters={
                    "package_id": package.package_id,
                    "package_fingerprint": package.package_fingerprint,
                    "artifact_set_id": artifact_set_id,
                    "case_id": case_id,
                    "source_report_id": latest_report.report_id,
                    "artifact_count": len(package.artifact_ids),
                    "scope": "local_preparation_review",
                },
                idempotency_key=action_idempotency_key,
            )

            # Save at PROPOSED (version 1) and emit action.proposed event
            uow.actions.save(action)
            uow.events.append(
                make_action_proposed_event(
                    action_id=action.action_id,
                    case_id=case_id,
                    user_id=user_id,
                    aggregate_version=action.version,
                    payload={
                        "action_type": action.action_type_str,
                        "risk_level": action.risk_level.value,
                        "requires_approval": action.requires_approval,
                        "parameters": action.parameters,
                        "idempotency_key": action.idempotency_key,
                    },
                )
            )

            # Transition PROPOSED -> AWAITING_APPROVAL (version 2) and emit action_status_changed event
            old_action_status = action.status.value
            action.transition_to(
                ActionStatus.AWAITING_APPROVAL,
                reason="Approval requested for verified preparation package",
            )
            uow.actions.save(action)

            uow.events.append(
                make_action_status_changed_event(
                    action_id=action.action_id,
                    user_id=user_id,
                    aggregate_version=action.version,
                    old_status=old_action_status,
                    new_status=action.status.value,
                    reason="Approval requested for verified preparation package",
                )
            )

            approval = Approval(
                action_id=action.action_id,
                case_id=case_id,
                user_id=user_id,
                action_fingerprint=action.compute_fingerprint(),
                status=ApprovalStatus.PENDING,
            )
            uow.approvals.save(approval)

            uow.events.append(
                make_approval_requested_event(
                    approval_id=approval.approval_id,
                    action_id=action.action_id,
                    case_id=case_id,
                    user_id=user_id,
                    aggregate_version=approval.version,
                    action_fingerprint=approval.action_fingerprint,
                )
            )

            # 6. Case state transition to AWAITING_APPROVAL
            if case.status == CaseStatus.PLANNING:
                old_status = case.status.value
                case.transition_to(CaseStatus.ACTION_REQUIRED, reason="Preparation package verified")
                uow.cases.save(case)
                uow.events.append(
                    make_case_status_changed_event(
                        case_id=case.case_id,
                        user_id=user_id,
                        aggregate_version=case.version,
                        old_status=old_status,
                        new_status=case.status.value,
                        reason="Preparation package verified",
                    )
                )
                old_status = case.status.value
                case.transition_to(CaseStatus.AWAITING_APPROVAL, reason="Preparation package awaiting approval")
                uow.cases.save(case)
                uow.events.append(
                    make_case_status_changed_event(
                        case_id=case.case_id,
                        user_id=user_id,
                        aggregate_version=case.version,
                        old_status=old_status,
                        new_status=case.status.value,
                        reason="Preparation package awaiting approval",
                    )
                )
            elif case.status == CaseStatus.ACTION_REQUIRED:
                old_status = case.status.value
                case.transition_to(CaseStatus.AWAITING_APPROVAL, reason="Preparation package awaiting approval")
                uow.cases.save(case)
                uow.events.append(
                    make_case_status_changed_event(
                        case_id=case.case_id,
                        user_id=user_id,
                        aggregate_version=case.version,
                        old_status=old_status,
                        new_status=case.status.value,
                        reason="Preparation package awaiting approval",
                    )
                )

            return package, action, approval, [f.to_dict() for f in result.findings], case

    def get_latest_package_for_case(self, user_id: str, case_id: str) -> PreparationPackage | None:
        """Fetch the latest preparation package for a case ensuring user isolation."""
        with self.storage.unit_of_work() as uow:
            case = uow.cases.get_by_id(case_id)
            if case is None or case.user_id != user_id:
                return None
            return uow.preparation_packages.get_latest_for_case(case_id)

    def get_package(self, user_id: str, package_id: str) -> PreparationPackage | None:
        """Fetch preparation package by ID ensuring user isolation."""
        with self.storage.unit_of_work() as uow:
            package = uow.preparation_packages.get_by_id(package_id)
            if package is None or package.user_id != user_id:
                return None
            return package

    def list_packages_for_case(self, user_id: str, case_id: str) -> list[PreparationPackage]:
        """List all preparation packages for a case ensuring user isolation."""
        with self.storage.unit_of_work() as uow:
            case = uow.cases.get_by_id(case_id)
            if case is None or case.user_id != user_id:
                return []
            return uow.preparation_packages.list_for_case(case_id)
