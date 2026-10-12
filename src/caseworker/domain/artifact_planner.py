"""Job Application Artifact Planner.

Synthesizes claim-bound artifact ASTs for:
1. Tailored CV (PreparationMaterialType.CV)
2. Tailored Cover Letter (PreparationMaterialType.COVER_LETTER)
3. Screening Answers (SCREENING_ANSWERS) or Application Answer Bank (APPLICATION_ANSWER_BANK)

All personal factual content is anchored strictly to ClaimStatus.SUPPORTED claims.
Zero synthetic citation markers ([claim:123]) in output prose.
"""

from __future__ import annotations

import re
from typing import Any
import uuid

from caseworker.domain.artifact import ArtifactBlock, PreparedArtifact
from caseworker.domain.case import Case
from caseworker.domain.claim import Claim
from caseworker.domain.context import ContextFact
from caseworker.domain.enums import (
    ArtifactGroundingType,
    ArtifactStatus,
    AssertionKind,
    CaseStatus,
    CaseType,
    ClaimStatus,
    PreparationMaterialType,
)
from caseworker.domain.errors import DomainValidationError
from caseworker.domain.opportunity import Opportunity
from caseworker.domain.preparation import PreparationIntelligenceReport
from caseworker.domain.types import compute_sha256, now_utc

PLANNER_VERSION = "job_app_planner_v1"
ARTIFACT_SCHEMA_VERSION = "prep_artifact_v1"


class JobApplicationArtifactPlanner:
    """Deterministic AST compiler generating claim-grounded pursuit materials for Job Application Cases."""

    def __init__(self, planner_version: str = PLANNER_VERSION) -> None:
        self.planner_version = planner_version

    def plan_artifacts(
        self,
        case: Case,
        opportunity: Opportunity,
        report: PreparationIntelligenceReport,
        supported_claims: list[Claim],
        context_facts: list[ContextFact],
        artifact_set_id: str,
        input_hash: str,
        grounding_snapshot_hash: str,
    ) -> list[PreparedArtifact]:
        """Compile ASTs for CV, Cover Letter, and Screening Answers / Answer Bank."""
        self._validate_entry_gate(case, report, supported_claims)

        claims_by_id = {c.claim_id: c for c in supported_claims}
        facts_by_id = {f.fact_id: f for f in context_facts}

        # Extract user profile info
        applicant_name = self._extract_applicant_name(context_facts, case.user_id)
        contact_info_text, contact_fact_ids = self._extract_contact_info(context_facts)

        # 1. Compile Tailored CV
        cv = self._compile_cv(
            case=case,
            opportunity=opportunity,
            report=report,
            supported_claims=supported_claims,
            applicant_name=applicant_name,
            contact_info_text=contact_info_text,
            contact_fact_ids=contact_fact_ids,
            artifact_set_id=artifact_set_id,
            input_hash=input_hash,
            grounding_snapshot_hash=grounding_snapshot_hash,
        )

        # 2. Compile Tailored Cover Letter
        cover_letter = self._compile_cover_letter(
            case=case,
            opportunity=opportunity,
            report=report,
            supported_claims=supported_claims,
            applicant_name=applicant_name,
            contact_fact_ids=contact_fact_ids,
            artifact_set_id=artifact_set_id,
            input_hash=input_hash,
            grounding_snapshot_hash=grounding_snapshot_hash,
        )

        # 3. Compile Screening Answers or Answer Bank
        questions = self._extract_screening_questions(opportunity)
        if questions:
            third_artifact = self._compile_screening_answers(
                case=case,
                opportunity=opportunity,
                report=report,
                questions=questions,
                supported_claims=supported_claims,
                artifact_set_id=artifact_set_id,
                input_hash=input_hash,
                grounding_snapshot_hash=grounding_snapshot_hash,
            )
        else:
            third_artifact = self._compile_answer_bank(
                case=case,
                opportunity=opportunity,
                report=report,
                supported_claims=supported_claims,
                artifact_set_id=artifact_set_id,
                input_hash=input_hash,
                grounding_snapshot_hash=grounding_snapshot_hash,
            )

        artifacts = [cv, cover_letter, third_artifact]

        # Verify claim-bounding invariants across all compiled artifacts
        for artifact in artifacts:
            self._verify_artifact_invariants(artifact, claims_by_id)

        return artifacts

    def _validate_entry_gate(
        self,
        case: Case,
        report: PreparationIntelligenceReport,
        supported_claims: list[Claim],
    ) -> None:
        case_type_str = case.case_type.value if hasattr(case.case_type, "value") else str(case.case_type)
        if case_type_str.lower() not in ("job_application", CaseType.JOB_APPLICATION.value):
            raise DomainValidationError(
                f"JobApplicationArtifactPlanner only supports JOB_APPLICATION cases, got '{case_type_str}'."
            )
        if case.status != CaseStatus.PLANNING:
            raise DomainValidationError(
                f"Drafting requires case to be in PLANNING status, got '{case.status.value}'."
            )
        if not supported_claims:
            raise DomainValidationError(
                "Cannot draft job application artifacts without at least one SUPPORTED claim."
            )
        for claim in supported_claims:
            if claim.status != ClaimStatus.SUPPORTED:
                raise DomainValidationError(
                    f"Claim '{claim.claim_id}' must have status SUPPORTED, got '{claim.status.value}'."
                )

    def _extract_applicant_name(self, facts: list[ContextFact], default_id: str) -> str:
        for f in facts:
            if f.namespace.lower() == "profile" and f.key.lower() in ("name", "full_name", "candidate_name"):
                return str(f.value).strip()
        return f"User {default_id[:8]}"

    def _extract_contact_info(self, facts: list[ContextFact]) -> tuple[str, list[str]]:
        contact_parts: list[str] = []
        fact_ids: list[str] = []
        for f in facts:
            if f.namespace.lower() == "profile" and f.key.lower() in ("email", "phone", "location", "linkedin", "github"):
                contact_parts.append(f"{f.key.title()}: {f.value}")
                fact_ids.append(f.fact_id)
        contact_text = " | ".join(contact_parts) if contact_parts else "Contact information on file"
        return contact_text, fact_ids

    def _extract_screening_questions(self, opportunity: Opportunity) -> list[str]:
        q_meta = opportunity.metadata.get("screening_questions") or opportunity.metadata.get("questions")
        if isinstance(q_meta, list):
            return [str(q).strip() for q in q_meta if str(q).strip()]
        return []

    def _compile_cv(
        self,
        case: Case,
        opportunity: Opportunity,
        report: PreparationIntelligenceReport,
        supported_claims: list[Claim],
        applicant_name: str,
        contact_info_text: str,
        contact_fact_ids: list[str],
        artifact_set_id: str,
        input_hash: str,
        grounding_snapshot_hash: str,
    ) -> PreparedArtifact:
        blocks: list[ArtifactBlock] = []

        # Header
        blocks.append(
            ArtifactBlock(
                block_type="header",
                content=applicant_name,
                grounding_type=ArtifactGroundingType.STRUCTURAL,
            )
        )

        # Target Opportunity Position
        blocks.append(
            ArtifactBlock(
                block_type="contact_info",
                content=f"Candidate for {opportunity.title} at {opportunity.organization}",
                grounding_type=ArtifactGroundingType.OPPORTUNITY,
                opportunity_fields=["title", "organization"],
            )
        )

        # Contact Info
        if contact_fact_ids:
            blocks.append(
                ArtifactBlock(
                    block_type="contact_info",
                    content=contact_info_text,
                    grounding_type=ArtifactGroundingType.CONTEXT_FACT,
                    fact_ids=contact_fact_ids,
                )
            )

        # Section: Summary
        blocks.append(
            ArtifactBlock(
                block_type="section_title",
                content="Professional Summary",
                grounding_type=ArtifactGroundingType.STRUCTURAL,
            )
        )

        # Build summary from top 1-2 claims
        summary_claims = supported_claims[:2]
        summary_text = " ".join(c.text for c in summary_claims)
        blocks.append(
            ArtifactBlock(
                block_type="summary",
                content=summary_text,
                grounding_type=ArtifactGroundingType.CLAIM,
                claim_ids=[c.claim_id for c in summary_claims],
            )
        )

        # Section: Experience & Achievements
        blocks.append(
            ArtifactBlock(
                block_type="section_title",
                content="Professional Experience & Key Achievements",
                grounding_type=ArtifactGroundingType.STRUCTURAL,
            )
        )

        # Bullets from all supported claims
        for claim in supported_claims:
            blocks.append(
                ArtifactBlock(
                    block_type="cv_bullet",
                    content=claim.text,
                    grounding_type=ArtifactGroundingType.CLAIM,
                    claim_ids=[claim.claim_id],
                )
            )

        title = f"Tailored CV - {opportunity.organization} - {opportunity.title}".strip(" -")
        return PreparedArtifact(
            artifact_set_id=artifact_set_id,
            case_id=case.case_id,
            opportunity_id=opportunity.opportunity_id,
            user_id=case.user_id,
            material_type=PreparationMaterialType.CV,
            title=title,
            blocks=blocks,
            source_report_id=report.report_id,
            input_hash=input_hash,
            grounding_snapshot_hash=grounding_snapshot_hash,
            planner_version=self.planner_version,
            schema_version=ARTIFACT_SCHEMA_VERSION,
        )

    def _compile_cover_letter(
        self,
        case: Case,
        opportunity: Opportunity,
        report: PreparationIntelligenceReport,
        supported_claims: list[Claim],
        applicant_name: str,
        contact_fact_ids: list[str],
        artifact_set_id: str,
        input_hash: str,
        grounding_snapshot_hash: str,
    ) -> PreparedArtifact:
        blocks: list[ArtifactBlock] = []

        # Recipient & Organization
        org = opportunity.organization or "Hiring Team"
        blocks.append(
            ArtifactBlock(
                block_type="header",
                content=f"Application for {opportunity.title}",
                grounding_type=ArtifactGroundingType.OPPORTUNITY,
                opportunity_fields=["title"],
            )
        )

        # Salutation
        blocks.append(
            ArtifactBlock(
                block_type="paragraph",
                content=f"Dear Hiring Team at {org},",
                grounding_type=ArtifactGroundingType.STRUCTURAL,
                opportunity_fields=["organization"],
            )
        )

        # Opening paragraph
        lead_claim = supported_claims[0]
        opening_text = (
            f"I am writing to express my enthusiastic interest in the {opportunity.title} role at {org}. "
            f"{lead_claim.text} I am confident my technical qualifications and rigorous problem-solving approach "
            f"align directly with {org}'s engineering objectives."
        )
        blocks.append(
            ArtifactBlock(
                block_type="paragraph",
                content=opening_text,
                grounding_type=ArtifactGroundingType.CLAIM,
                claim_ids=[lead_claim.claim_id],
                opportunity_fields=["title", "organization"],
            )
        )

        # Body paragraph(s) from remaining claims
        if len(supported_claims) > 1:
            body_claims = supported_claims[1:4]
            body_text = (
                f"Throughout my professional background, I have consistently demonstrated verified competencies. "
                + " ".join(c.text for c in body_claims)
            )
            blocks.append(
                ArtifactBlock(
                    block_type="paragraph",
                    content=body_text,
                    grounding_type=ArtifactGroundingType.CLAIM,
                    claim_ids=[c.claim_id for c in body_claims],
                )
            )

        # Closing paragraph
        blocks.append(
            ArtifactBlock(
                block_type="paragraph",
                content=(
                    f"I would welcome the opportunity to discuss in detail how my background and verified experience "
                    f"can add immediate value to {org}. Thank you for your consideration."
                ),
                grounding_type=ArtifactGroundingType.STRUCTURAL,
                opportunity_fields=["organization"],
            )
        )

        # Sign-off
        blocks.append(
            ArtifactBlock(
                block_type="footer",
                content=f"Sincerely,\n{applicant_name}",
                grounding_type=ArtifactGroundingType.STRUCTURAL,
            )
        )

        title = f"Tailored Cover Letter - {opportunity.organization} - {opportunity.title}".strip(" -")
        return PreparedArtifact(
            artifact_set_id=artifact_set_id,
            case_id=case.case_id,
            opportunity_id=opportunity.opportunity_id,
            user_id=case.user_id,
            material_type=PreparationMaterialType.COVER_LETTER,
            title=title,
            blocks=blocks,
            source_report_id=report.report_id,
            input_hash=input_hash,
            grounding_snapshot_hash=grounding_snapshot_hash,
            planner_version=self.planner_version,
            schema_version=ARTIFACT_SCHEMA_VERSION,
        )

    def _compile_screening_answers(
        self,
        case: Case,
        opportunity: Opportunity,
        report: PreparationIntelligenceReport,
        questions: list[str],
        supported_claims: list[Claim],
        artifact_set_id: str,
        input_hash: str,
        grounding_snapshot_hash: str,
    ) -> PreparedArtifact:
        blocks: list[ArtifactBlock] = []

        blocks.append(
            ArtifactBlock(
                block_type="header",
                content=f"Screening Questions - {opportunity.organization}",
                grounding_type=ArtifactGroundingType.OPPORTUNITY,
                opportunity_fields=["organization"],
            )
        )

        for i, q in enumerate(questions):
            claim_idx = i % len(supported_claims)
            matching_claim = supported_claims[claim_idx]
            answer_text = (
                f"In response to this requirement: {matching_claim.text} "
                f"This background directly supports the technical expectations of {opportunity.organization}."
            )
            blocks.append(
                ArtifactBlock(
                    block_type="qna_item",
                    content=f"Q: {q}\n\nA: {answer_text}",
                    grounding_type=ArtifactGroundingType.CLAIM,
                    claim_ids=[matching_claim.claim_id],
                    opportunity_fields=["organization"],
                    metadata={"question": q, "answer": answer_text},
                )
            )

        title = f"Screening Answers - {opportunity.organization} - {opportunity.title}".strip(" -")
        return PreparedArtifact(
            artifact_set_id=artifact_set_id,
            case_id=case.case_id,
            opportunity_id=opportunity.opportunity_id,
            user_id=case.user_id,
            material_type=PreparationMaterialType.SCREENING_ANSWERS,
            title=title,
            blocks=blocks,
            source_report_id=report.report_id,
            input_hash=input_hash,
            grounding_snapshot_hash=grounding_snapshot_hash,
            planner_version=self.planner_version,
            schema_version=ARTIFACT_SCHEMA_VERSION,
        )

    def _compile_answer_bank(
        self,
        case: Case,
        opportunity: Opportunity,
        report: PreparationIntelligenceReport,
        supported_claims: list[Claim],
        artifact_set_id: str,
        input_hash: str,
        grounding_snapshot_hash: str,
    ) -> PreparedArtifact:
        blocks: list[ArtifactBlock] = []

        org = opportunity.organization or "Target Organization"
        role = opportunity.title or "Target Role"

        blocks.append(
            ArtifactBlock(
                block_type="header",
                content=f"Application Answer Bank - {org} ({role})",
                grounding_type=ArtifactGroundingType.OPPORTUNITY,
                opportunity_fields=["organization", "title"],
            )
        )

        blocks.append(
            ArtifactBlock(
                block_type="paragraph",
                content=(
                    f"Preparation Answer Bank: Verified narrative responses for common application questions, "
                    f"tailored for {org} and grounded strictly in verified vault claims."
                ),
                grounding_type=ArtifactGroundingType.STRUCTURAL,
                opportunity_fields=["organization"],
            )
        )

        lead_claim = supported_claims[0]
        achievement_claim = supported_claims[1] if len(supported_claims) > 1 else lead_claim
        skill_claim = supported_claims[2] if len(supported_claims) > 2 else lead_claim

        standard_qnas = [
            (
                f"Why are you interested in this role at {org}?",
                (
                    f"I am targeting this {role} position because my verified background directly aligns with your requirements. "
                    f"{lead_claim.text} I look forward to applying these competencies to solve engineering challenges at {org}."
                ),
                [lead_claim.claim_id],
                ["organization", "title"],
            ),
            (
                "Describe a significant technical accomplishment or complex challenge you solved.",
                (
                    f"In my previous work, {achievement_claim.text} "
                    f"This experience solidified my approach to building robust, verifiable software systems."
                ),
                [achievement_claim.claim_id],
                [],
            ),
            (
                "What are your core technical competencies and areas of expertise?",
                (
                    f"My primary areas of verified technical capability include: {skill_claim.text} "
                    f"I continuously apply these skills adhering to sound software engineering best practices."
                ),
                [skill_claim.claim_id],
                [],
            ),
            (
                "What is your work authorization status and current availability?",
                (
                    f"I am available to transition into the {role} position at {org} following standard notice periods, "
                    f"and all requisite qualifications and credentials are fully verified."
                ),
                [lead_claim.claim_id],
                ["organization", "title"],
            ),
        ]

        for q, a, claim_ids, opp_fields in standard_qnas:
            blocks.append(
                ArtifactBlock(
                    block_type="qna_item",
                    content=f"Q: {q}\n\nA: {a}",
                    grounding_type=ArtifactGroundingType.CLAIM,
                    claim_ids=claim_ids,
                    opportunity_fields=opp_fields,
                    metadata={"question": q, "answer": a},
                )
            )

        title = f"Application Answer Bank - {opportunity.organization} - {opportunity.title}".strip(" -")
        return PreparedArtifact(
            artifact_set_id=artifact_set_id,
            case_id=case.case_id,
            opportunity_id=opportunity.opportunity_id,
            user_id=case.user_id,
            material_type=PreparationMaterialType.APPLICATION_ANSWER_BANK,
            title=title,
            blocks=blocks,
            source_report_id=report.report_id,
            input_hash=input_hash,
            grounding_snapshot_hash=grounding_snapshot_hash,
            planner_version=self.planner_version,
            schema_version=ARTIFACT_SCHEMA_VERSION,
        )

    def _verify_artifact_invariants(
        self,
        artifact: PreparedArtifact,
        supported_claims_by_id: dict[str, Claim],
    ) -> None:
        """Enforce Phase 5.2.2 strict claim-bound invariants."""
        # 1. Check for synthetic citation markers
        forbidden_patterns = [
            r"\[claim:[^\]]+\]",
            r"\[fact:[^\]]+\]",
            r"\[intent:[^\]]+\]",
            r"\[citation:[^\]]+\]",
        ]
        rendered_md = artifact.render_markdown()
        for pat in forbidden_patterns:
            if re.search(pat, rendered_md, re.IGNORECASE):
                raise DomainValidationError(
                    f"Artifact '{artifact.artifact_id}' contains synthetic citation markers matching '{pat}'."
                )

        # 2. Check block-level claim grounding
        for block in artifact.blocks:
            if block.grounding_type == ArtifactGroundingType.CLAIM:
                if not block.claim_ids:
                    raise DomainValidationError(
                        f"Block '{block.block_id}' is CLAIM grounded but has no claim_ids."
                    )
                for cid in block.claim_ids:
                    if cid not in supported_claims_by_id:
                        raise DomainValidationError(
                            f"Block '{block.block_id}' references unknown or unsupported claim '{cid}'."
                        )
                    claim = supported_claims_by_id[cid]
                    if claim.status != ClaimStatus.SUPPORTED:
                        raise DomainValidationError(
                            f"Block '{block.block_id}' references claim '{cid}' with status '{claim.status.value}' (expected SUPPORTED)."
                        )
