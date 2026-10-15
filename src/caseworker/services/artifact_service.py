"""Artifact Preparation Application Service.

Orchestrates Phase 5.2.2 Claim-Bound Artifact Preparation:
- Single atomic Unit of Work
- Entry Gate validation (CaseType.JOB_APPLICATION in CaseStatus.PLANNING with fresh report)
- Purpose-gated vault reacquisition strictly under purpose="job_application"
- Claim synthesis from ClaimAssertionSpecs validated by ClaimAssertionValidator and ClaimVerificationPolicy
- Compilation of artifact ASTs (CV, Cover Letter, Screening Answers / Answer Bank)
- Deterministic draft fingerprinting and idempotency
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any
import uuid

from caseworker.domain.artifact import (
    PreparedArtifact,
    compute_artifact_grounding_hash,
    compute_artifact_staleness,
)
from caseworker.domain.artifact_planner import JobApplicationArtifactPlanner, PLANNER_VERSION
from caseworker.domain.assertion import (
    CanonicalClaimRenderer,
    ClaimAssertionSpec,
    ClaimAssertionValidator,
)
from caseworker.domain.case import Case
from caseworker.domain.claim import Claim, compute_support_snapshot_hash
from caseworker.domain.claim_policy import ClaimVerificationPolicy
from caseworker.domain.context import ContextFact
from caseworker.domain.context_package import ContextPackageBuilder
from caseworker.domain.enums import (
    ArtifactStatus,
    AssertionKind,
    CaseStatus,
    CaseType,
    ClaimStatus,
    PreparationMaterialType,
)
from caseworker.domain.errors import (
    DomainValidationError,
    EntityNotFoundError,
    OptimisticLockError,
)
from caseworker.domain.events import (
    make_claim_proposed_event,
    make_claim_status_changed_event,
    make_context_access_denied_event,
    make_context_access_granted_event,
    make_preparation_artifact_prepared_event,
)
from caseworker.domain.normalized_requirement import NormalizedRequirement, parse_requirements
from caseworker.domain.preparation import (
    SafeDeniedFactDescriptor,
    compute_prep_context_snapshot_hash,
    compute_prep_input_hash,
    is_fact_or_descriptor_relevant_to_requirements,
)
from caseworker.domain.preparation_analyzer import PreparationAnalyzer
from caseworker.domain.types import canonical_json_dumps, compute_sha256, now_utc
from caseworker.domain.vault_policy import ContextAccessPolicy

if TYPE_CHECKING:
    from caseworker.persistence.sqlite import SQLiteCaseworkerStorage


class ArtifactPreparationService:
    """Service orchestrating claim-bound application material synthesis."""

    def __init__(self, storage: SQLiteCaseworkerStorage) -> None:
        self.storage = storage
        self.verification_policy = ClaimVerificationPolicy()
        self.planner = JobApplicationArtifactPlanner()

    def draft_job_application_artifacts(
        self,
        user_id: str,
        case_id: str,
        expected_case_version: int | None = None,
    ) -> tuple[list[PreparedArtifact], Case]:
        """Atomically draft claim-bound CV, Cover Letter, and Screening Answers / Answer Bank."""
        with self.storage.unit_of_work() as uow:
            # 1. Entry Gate: Case retrieval and validation
            case = uow.cases.get_by_id(case_id)
            if case is None or case.user_id != user_id:
                raise EntityNotFoundError("Case", case_id)

            if expected_case_version is not None and case.version != expected_case_version:
                raise OptimisticLockError("Case", case.case_id, expected_case_version)

            case_type_str = case.case_type.value if hasattr(case.case_type, "value") else str(case.case_type)
            if case_type_str.lower() not in ("job_application", CaseType.JOB_APPLICATION.value):
                raise DomainValidationError(
                    f"Artifact preparation currently only supports JOB_APPLICATION cases, got '{case_type_str}'."
                )

            if case.status != CaseStatus.PLANNING:
                raise DomainValidationError(
                    f"Drafting requires case to be in PLANNING status, got '{case.status.value}'."
                )

            # 2. Opportunity & Report retrieval
            opportunity = uow.opportunities.get_by_converted_case_id(case_id)
            if opportunity is None and case.opportunity_id:
                opportunity = uow.opportunities.get_by_id(case.opportunity_id)
            if opportunity is None:
                raise EntityNotFoundError("Opportunity for case", case_id)

            report = uow.preparation_reports.get_latest_for_case(case_id)
            if report is None:
                raise DomainValidationError(
                    f"Case '{case_id}' has no preparation intelligence report; run preparation analysis first."
                )

            # 3. Check report staleness against current inputs
            all_facts = uow.context.list_active(user_id=user_id)
            if self._is_report_stale_in_uow(uow, case, report, all_facts=all_facts, opportunity=opportunity):
                raise DomainValidationError(
                    "Preparation intelligence report is stale; run re-analysis before drafting artifacts."
                )


            # 4. Purpose-gated vault reacquisition strictly under purpose="job_application"
            app_builder = ContextPackageBuilder(ContextAccessPolicy())
            app_package, app_granted, app_denied = app_builder.build_with_report(
                user_id=user_id,
                purpose="job_application",
                available_facts=all_facts,
            )
            granted_facts = app_package.facts
            granted_facts_by_id = {f.fact_id: f for f in granted_facts}

            # Emit audit events for vault access under job_application
            vault_ver = uow.events.get_next_aggregate_version("context_vault", user_id)
            if app_granted:
                uow.events.append(
                    make_context_access_granted_event(
                        user_id=user_id,
                        aggregate_version=vault_ver,
                        purpose="job_application",
                        fact_ids=[g.fact_id for g in app_granted],
                        namespaces=sorted(list({g.namespace for g in app_granted if g.namespace})),
                    )
                )
                vault_ver += 1
            for d in app_denied:
                uow.events.append(
                    make_context_access_denied_event(
                        user_id=user_id,
                        aggregate_version=vault_ver,
                        purpose="job_application",
                        fact_id=d.fact_id,
                        reason=d.reason,
                        reason_code=d.reason_code,
                        namespace=d.namespace,
                        key=d.key,
                    )
                )
                vault_ver += 1

            # 5. Claim synthesis, validation, and support evaluation
            supported_claims: list[Claim] = []
            used_fact_ids: set[str] = set()

            existing_case_claims = uow.claims.list_by_case(case_id)
            existing_by_intent = {
                c.source_claim_intent_id: c for c in existing_case_claims
                if c.source_claim_intent_id and c.status == ClaimStatus.SUPPORTED
            }
            existing_by_assertion = {
                c.assertion_json: c for c in existing_case_claims
                if c.assertion_json and c.status == ClaimStatus.SUPPORTED
            }

            # Synthesize from ClaimIntents in report strategy
            for intent in report.strategy.claim_intents:
                # Invariant: All supporting facts must be purpose-granted under job_application
                if not intent.supporting_fact_ids:
                    continue
                missing_or_denied = [fid for fid in intent.supporting_fact_ids if fid not in granted_facts_by_id]
                if missing_or_denied:
                    continue

                matching_facts = [granted_facts_by_id[fid] for fid in intent.supporting_fact_ids]
                spec = self._build_spec_for_intent(intent, matching_facts)
                if spec is None:
                    continue

                valid, err = ClaimAssertionValidator.validate(spec, granted_facts)
                if not valid:
                    continue

                spec_json = canonical_json_dumps(spec.to_dict())
                rendered_text = CanonicalClaimRenderer.render(spec)
                snapshot_hash = compute_support_snapshot_hash(matching_facts)

                if intent.intent_id in existing_by_intent:
                    claim = existing_by_intent[intent.intent_id]
                    supported_claims.append(claim)
                    used_fact_ids.update(spec.fact_ids)
                    continue
                elif spec_json in existing_by_assertion:
                    claim = existing_by_assertion[spec_json]
                    supported_claims.append(claim)
                    used_fact_ids.update(spec.fact_ids)
                    continue

                det_claim_id = f"clm-{compute_sha256(f'{case_id}:{intent.intent_id}:{spec_json}')[:16]}"
                claim = Claim(
                    claim_id=det_claim_id,
                    user_id=user_id,
                    purpose="job_application",
                    text=rendered_text,
                    case_id=case_id,
                    mission_id=case.mission_id,
                    source_report_id=report.report_id,
                    source_claim_intent_id=intent.intent_id,
                    assertion_schema=spec.assertion_schema,
                    assertion_json=spec_json,
                    support_snapshot_hash=snapshot_hash,
                    supporting_fact_ids=spec.fact_ids,
                    status=ClaimStatus.PROPOSED,
                    version=1,
                )

                # Step 1 & 2: Persist claim at version 1 (PROPOSED) and emit claim.proposed
                uow.claims.save(claim)
                uow.events.append(
                    make_claim_proposed_event(
                        claim_id=claim.claim_id,
                        user_id=user_id,
                        purpose=claim.purpose,
                        text=claim.text,
                        supporting_fact_ids=claim.supporting_fact_ids,
                        aggregate_version=1,
                    )
                )

                # Step 3 & 4: Evaluate support via verification_policy
                status, reason = self.verification_policy.evaluate_support(claim, matching_facts)
                if status == ClaimStatus.SUPPORTED:
                    claim.set_supported()
                    uow.claims.save(claim)
                    uow.events.append(
                        make_claim_status_changed_event(
                            claim.claim_id, user_id, claim.version, "proposed", "supported"
                        )
                    )
                    supported_claims.append(claim)
                    used_fact_ids.update(spec.fact_ids)
                    existing_by_intent[intent.intent_id] = claim
                    existing_by_assertion[spec_json] = claim
                else:
                    claim.set_unsupported(reason=reason)
                    uow.claims.save(claim)
                    uow.events.append(
                        make_claim_status_changed_event(
                            claim.claim_id, user_id, claim.version, "proposed", "unsupported"
                        )
                    )

            if not supported_claims:
                raise DomainValidationError(
                    "Cannot draft application materials: zero SUPPORTED claims could be substantiated "
                    "from verified vault facts under purpose 'job_application'."
                )

            # 6. Compute draft fingerprint for idempotency
            sorted_claim_ids = sorted(c.claim_id for c in supported_claims)
            draft_fingerprint = compute_sha256(
                f"{case_id}:{report.report_id}:{PLANNER_VERSION}:{','.join(sorted_claim_ids)}"
            )

            # Idempotency check: Return existing DRAFT set if exact fingerprint matches
            existing_artifacts = uow.preparation_artifacts.list_by_case(case_id)
            active_matching = [
                a for a in existing_artifacts
                if a.status == ArtifactStatus.DRAFT and a.input_hash == draft_fingerprint
            ]
            if len(active_matching) >= 3:
                return active_matching, case

            # Supersede any prior DRAFT artifacts for this case
            uow.preparation_artifacts.update_status_for_case(
                case_id, ArtifactStatus.DRAFT, ArtifactStatus.SUPERSEDED
            )

            # 7. Compile Artifact ASTs
            artifact_set_id = f"artset-{uuid.uuid4().hex[:12]}"
            grounding_snapshot_hash = compute_artifact_grounding_hash(
                claims=supported_claims,
                direct_facts=granted_facts,
            )

            artifacts = self.planner.plan_artifacts(
                case=case,
                opportunity=opportunity,
                report=report,
                supported_claims=supported_claims,
                context_facts=granted_facts,
                artifact_set_id=artifact_set_id,
                input_hash=draft_fingerprint,
                grounding_snapshot_hash=grounding_snapshot_hash,
            )

            # Save artifacts and append domain events
            for artifact in artifacts:
                uow.preparation_artifacts.save(artifact)
                uow.events.append(
                    make_preparation_artifact_prepared_event(
                        artifact_id=artifact.artifact_id,
                        set_id=artifact.artifact_set_id,
                        case_id=case.case_id,
                        material_type=artifact.material_type.value,
                        user_id=user_id,
                        aggregate_version=1,
                        source_report_id=report.report_id,
                        input_hash=artifact.input_hash,
                        grounding_snapshot_hash=artifact.grounding_snapshot_hash,
                    )
                )

            uow.commit()
            return artifacts, case

    def get_case_artifacts(self, user_id: str, case_id: str) -> list[PreparedArtifact]:
        """Retrieve all prepared artifacts for a case (strictly side-effect free)."""
        with self.storage.unit_of_work() as uow:
            case = uow.cases.get_by_id(case_id)
            if case is None or case.user_id != user_id:
                raise EntityNotFoundError("Case", case_id)
            return uow.preparation_artifacts.list_by_case(case_id)

    def get_artifact(self, user_id: str, case_id: str, artifact_id: str) -> PreparedArtifact:
        """Retrieve a specific prepared artifact (strictly side-effect free)."""
        with self.storage.unit_of_work() as uow:
            case = uow.cases.get_by_id(case_id)
            if case is None or case.user_id != user_id:
                raise EntityNotFoundError("Case", case_id)
            artifact = uow.preparation_artifacts.get_by_id(artifact_id)
            if artifact is None or artifact.case_id != case_id:
                raise EntityNotFoundError("PreparedArtifact", artifact_id)
            return artifact

    def is_artifact_stale(self, user_id: str, case_id: str, artifact: PreparedArtifact) -> tuple[bool, str | None]:
        """Compute whether an artifact is stale dynamically without modifying database state."""
        with self.storage.unit_of_work() as uow:
            case = uow.cases.get_by_id(case_id)
            if case is None or case.user_id != user_id:
                raise EntityNotFoundError("Case", case_id)
            return self._compute_staleness_in_uow(uow, case, artifact)

    def _compute_staleness_in_uow(
        self,
        uow: Any,
        case: Case,
        artifact: PreparedArtifact,
    ) -> tuple[bool, str | None]:
        report = uow.preparation_reports.get_latest_for_case(case.case_id)
        if report is None:
            return True, "No preparation intelligence report exists for case."

        is_rep_stale = self._is_report_stale_in_uow(uow, case, report)
        case_claims = {c.claim_id: c for c in uow.claims.list_by_case(case.case_id)}
        all_facts = {f.fact_id: f for f in uow.context.list_active(user_id=case.user_id)}

        return compute_artifact_staleness(
            artifact=artifact,
            latest_report=report,
            is_report_stale=is_rep_stale,
            claims_by_id=case_claims,
            facts_by_id=all_facts,
            current_planner_version=PLANNER_VERSION,
        )

    def export_artifact(
        self,
        user_id: str,
        case_id: str,
        artifact_id: str,
        format_type: str = "markdown",
    ) -> str:
        """Export prepared artifact cleanly in Markdown or plain text with zero citation tags."""
        with self.storage.unit_of_work() as uow:
            case = uow.cases.get_by_id(case_id)
            if case is None or case.user_id != user_id:
                raise EntityNotFoundError("Case", case_id)
            artifact = uow.preparation_artifacts.get_by_id(artifact_id)
            if artifact is None or artifact.case_id != case_id:
                raise EntityNotFoundError("PreparedArtifact", artifact_id)

            is_stale, reason = self._compute_staleness_in_uow(uow, case, artifact)
            if is_stale or artifact.status in (ArtifactStatus.STALE, ArtifactStatus.SUPERSEDED):
                raise DomainValidationError(
                    f"Cannot export stale or superseded artifact '{artifact_id}': {reason or artifact.status.value}."
                )

            if artifact.material_type == PreparationMaterialType.SCREENING_ANSWERS:
                for b in artifact.blocks:
                    if b.metadata.get("answer_status") == "NEEDS_USER_INPUT" or not b.metadata.get("answer"):
                        raise DomainValidationError(
                            "Cannot export screening questionnaire with unresolved required questions."
                        )

            if format_type.lower() in ("plain_text", "text"):
                return artifact.render_plain_text()
            return artifact.render_markdown()

    def _is_report_stale_in_uow(
        self,
        uow: Any,
        case: Case,
        report: PreparationIntelligenceReport,
        all_facts: list[ContextFact] | None = None,
        opportunity: Opportunity | None = None,
    ) -> bool:
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

    def _extract_fact_meta(self, fact: ContextFact) -> dict[str, Any]:
        """Safely extract metadata dictionary from ContextFact."""
        if isinstance(fact.value, dict):
            return dict(fact.value)
        if hasattr(fact, "metadata") and isinstance(getattr(fact, "metadata"), dict):
            return dict(getattr(fact, "metadata"))
        return {}

    def _build_spec_for_intent(self, intent: Any, facts: list[ContextFact]) -> ClaimAssertionSpec | None:
        """Construct ClaimAssertionSpec from ClaimIntent and matching ContextFacts."""
        lead_fact = facts[0]
        ns = lead_fact.namespace.lower()
        key = lead_fact.key.lower()
        meta = self._extract_fact_meta(lead_fact)
        val = str(lead_fact.value)

        # 1. Employment duration
        if any(k in key for k in ("duration", "years_experience", "experience_years")) or (
            ns in ("experience", "employment") and re.search(r"(\d+(?:\.\d+)?)\s*(?:years?|yrs?|y\b)", val.lower())
        ):
            match = re.search(r"(\d+(?:\.\d+)?)", val)
            years = float(match.group(1)) if match else float(meta.get("years", 1))
            fields_dur: dict[str, Any] = {"duration_years": int(years) if years.is_integer() else years}
            if meta.get("field"):
                fields_dur["field"] = str(meta["field"])
            if meta.get("is_professional") or "professional" in val.lower() or "engineer" in val.lower():
                fields_dur["is_professional"] = True
            return ClaimAssertionSpec(
                kind=AssertionKind.EMPLOYMENT_DURATION,
                fact_ids=[lead_fact.fact_id],
                fields=fields_dur,
            )

        # 2. Skills
        if ns in ("skills", "skill") or "skill" in key:
            skill = str(meta.get("skill") or val)
            fields_sk: dict[str, Any] = {"skill": skill}
            if "production" in val.lower():
                fields_sk["production_experience"] = True
            return ClaimAssertionSpec(
                kind=AssertionKind.SKILL_POSSESSION,
                fact_ids=[lead_fact.fact_id],
                fields=fields_sk,
            )

        # 3. Employment role
        if ns in ("experience", "employment", "roles") or any(k in key for k in ("role", "title", "position")):
            role = str(meta.get("role") or meta.get("title") or val)
            org = str(meta.get("organization") or meta.get("company") or "")
            return ClaimAssertionSpec(
                kind=AssertionKind.EMPLOYMENT_ROLE,
                fact_ids=[lead_fact.fact_id],
                fields={"role": role, "organization": org},
            )

        # 4. Education
        if ns == "education" or any(k in key for k in ("degree", "credential", "diploma")):
            degree = str(meta.get("degree") or val)
            field_study = str(meta.get("field_of_study") or "")
            if not field_study and " in " in val:
                parts = val.split(" in ", 1)
                degree = parts[0].strip()
                field_study = parts[1].strip()
            return ClaimAssertionSpec(
                kind=AssertionKind.EDUCATION_CREDENTIAL,
                fact_ids=[lead_fact.fact_id],
                fields={"degree": degree, "field_of_study": field_study},
            )

        # 5. Language
        if ns == "language" or "language" in key:
            lang = str(meta.get("language") or val)
            cefr = str(meta.get("cefr_level") or "")
            return ClaimAssertionSpec(
                kind=AssertionKind.LANGUAGE_PROFICIENCY,
                fact_ids=[lead_fact.fact_id],
                fields={"language": lang, "cefr_level": cefr},
            )

        # 6. Quantified Achievement
        if any(k in key for k in ("achievement", "metric", "quantified")):
            match = re.search(r"(\d+(?:\.\d+)?)\s*(%|[a-zA-Z]+)?", val)
            if match and match.group(1):
                metric = float(match.group(1))
                if metric.is_integer():
                    metric = int(metric)
                unit = match.group(2) or str(meta.get("metric_unit", "%"))
            else:
                metric = meta.get("metric", 1)
                unit = str(meta.get("metric_unit", "%"))

            fields_ach: dict[str, Any] = {"metric": metric, "metric_unit": unit}
            direction = str(meta.get("direction") or "")
            if not direction:
                val_l = val.lower()
                if "reduc" in val_l or "decreas" in val_l:
                    fields_ach["direction"] = "reduction"
                elif "improv" in val_l:
                    fields_ach["direction"] = "improvement"
                elif "increas" in val_l:
                    fields_ach["direction"] = "increase"
            else:
                fields_ach["direction"] = direction
            if meta.get("domain"):
                fields_ach["domain"] = str(meta["domain"])

            return ClaimAssertionSpec(
                kind=AssertionKind.QUANTIFIED_ACHIEVEMENT,
                fact_ids=[lead_fact.fact_id],
                fields=fields_ach,
            )

        # 7. Project
        if any(k in key for k in ("project", "architecture", "system")):
            project_name = str(meta.get("project_name") or val)
            fields_proj: dict[str, Any] = {"project_name": project_name}
            if "production" in val.lower():
                fields_proj["production_environment"] = True
            return ClaimAssertionSpec(
                kind=AssertionKind.PROJECT_EXPERIENCE,
                fact_ids=[lead_fact.fact_id],
                fields=fields_proj,
            )

        # 8. Work auth
        if any(k in key for k in ("authorization", "visa", "citizenship", "work_auth")):
            auth_status = str(meta.get("status") or val)
            jurisdiction = str(meta.get("jurisdiction") or "")
            fields_auth: dict[str, Any] = {"status": auth_status}
            if jurisdiction:
                fields_auth["jurisdiction"] = jurisdiction
            val_l = val.lower()
            if meta.get("no_sponsorship_required") or any(
                t in val_l for t in ("no sponsorship", "without sponsorship", "without visa sponsorship", "citizen", "permanent resident")
            ):
                fields_auth["no_sponsorship_required"] = True
            return ClaimAssertionSpec(
                kind=AssertionKind.WORK_AUTHORIZATION,
                fact_ids=[lead_fact.fact_id],
                fields=fields_auth,
            )

        # Fallback to skill possession based on fact value
        return ClaimAssertionSpec(
            kind=AssertionKind.SKILL_POSSESSION,
            fact_ids=[lead_fact.fact_id],
            fields={"skill": val},
        )

    def _build_spec_for_fact(self, fact: ContextFact) -> ClaimAssertionSpec | None:
        """Construct baseline ClaimAssertionSpec directly from an active granted ContextFact."""
        ns = fact.namespace.lower()
        key = fact.key.lower()
        meta = self._extract_fact_meta(fact)
        val = str(fact.value)

        if ns in ("skills", "skill") or "skill" in key:
            skill = str(meta.get("skill") or val)
            return ClaimAssertionSpec(
                kind=AssertionKind.SKILL_POSSESSION,
                fact_ids=[fact.fact_id],
                fields={"skill": skill},
            )
        if ns in ("experience", "employment", "roles") or any(k in key for k in ("role", "title", "position")):
            role = str(meta.get("role") or meta.get("title") or val)
            org = str(meta.get("organization") or meta.get("company") or "")
            return ClaimAssertionSpec(
                kind=AssertionKind.EMPLOYMENT_ROLE,
                fact_ids=[fact.fact_id],
                fields={"role": role, "organization": org},
            )
        if ns == "education" or any(k in key for k in ("degree", "credential", "diploma")):
            degree = str(meta.get("degree") or val)
            field_study = str(meta.get("field_of_study") or "")
            return ClaimAssertionSpec(
                kind=AssertionKind.EDUCATION_CREDENTIAL,
                fact_ids=[fact.fact_id],
                fields={"degree": degree, "field_of_study": field_study},
            )
        if ns == "language" or "language" in key:
            lang = str(meta.get("language") or val)
            cefr = str(meta.get("cefr_level") or "")
            return ClaimAssertionSpec(
                kind=AssertionKind.LANGUAGE_PROFICIENCY,
                fact_ids=[fact.fact_id],
                fields={"language": lang, "cefr_level": cefr},
            )
        if any(k in key for k in ("achievement", "metric")):
            metric = meta.get("metric", "significant")
            unit = str(meta.get("metric_unit", "%"))
            return ClaimAssertionSpec(
                kind=AssertionKind.QUANTIFIED_ACHIEVEMENT,
                fact_ids=[fact.fact_id],
                fields={"metric": metric, "metric_unit": unit},
            )
        if any(k in key for k in ("project", "architecture")):
            project_name = str(meta.get("project_name") or val)
            return ClaimAssertionSpec(
                kind=AssertionKind.PROJECT_EXPERIENCE,
                fact_ids=[fact.fact_id],
                fields={"project_name": project_name},
            )
        if any(k in key for k in ("authorization", "visa", "citizenship", "work_auth")):
            auth_status = str(meta.get("status") or val)
            jurisdiction = str(meta.get("jurisdiction") or "")
            return ClaimAssertionSpec(
                kind=AssertionKind.WORK_AUTHORIZATION,
                fact_ids=[fact.fact_id],
                fields={"status": auth_status, "jurisdiction": jurisdiction},
            )
        return None
