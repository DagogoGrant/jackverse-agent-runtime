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

from caseworker.domain.artifact import (
    ArtifactBlock,
    PreparedArtifact,
    compute_artifact_grounding_hash,
)
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
from caseworker.domain.requirement_matcher import is_fact_verified
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

        # Extract verified user profile info
        applicant_name, name_fact_ids = self._extract_applicant_name(context_facts)
        contact_info_text, contact_fact_ids = self._extract_contact_info(context_facts)

        # 1. Compile Tailored CV
        cv = self._compile_cv(
            case=case,
            opportunity=opportunity,
            report=report,
            supported_claims=supported_claims,
            applicant_name=applicant_name,
            name_fact_ids=name_fact_ids,
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
            name_fact_ids=name_fact_ids,
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
            art_claim_ids = {cid for b in artifact.blocks for cid in b.claim_ids}
            art_fact_ids = {fid for b in artifact.blocks for fid in b.fact_ids}
            art_claims = [claims_by_id[cid] for cid in art_claim_ids if cid in claims_by_id]
            art_facts = [facts_by_id[fid] for fid in art_fact_ids if fid in facts_by_id]
            artifact.grounding_snapshot_hash = compute_artifact_grounding_hash(art_claims, art_facts)

            self._verify_artifact_invariants(
                artifact=artifact,
                supported_claims_by_id=claims_by_id,
                context_facts_by_id=facts_by_id,
                opportunity=opportunity,
            )

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

    def _extract_applicant_name(self, facts: list[ContextFact]) -> tuple[str | None, list[str]]:
        """Extract applicant name requiring USER_VERIFIED or SOURCE_VERIFIED status and job_application purpose."""
        for f in facts:
            if (
                f.namespace.lower() == "profile"
                and f.key.lower() in ("name", "full_name", "candidate_name")
                and is_fact_verified(f.verification_status)
                and "job_application" in (f.allowed_purposes or [])
                and f.superseded_by_fact_id is None
            ):
                return str(f.value).strip(), [f.fact_id]
        return None, []

    def _extract_contact_info(self, facts: list[ContextFact]) -> tuple[str | None, list[str]]:
        """Extract contact info requiring USER_VERIFIED or SOURCE_VERIFIED status and job_application purpose."""
        contact_parts: list[str] = []
        fact_ids: list[str] = []
        for f in facts:
            if (
                f.namespace.lower() == "profile"
                and f.key.lower() in ("email", "phone", "location", "linkedin", "github")
                and is_fact_verified(f.verification_status)
                and "job_application" in (f.allowed_purposes or [])
                and f.superseded_by_fact_id is None
            ):
                contact_parts.append(f"{f.key.title()}: {f.value}")
                fact_ids.append(f.fact_id)
        if not contact_parts:
            return None, []
        return " | ".join(contact_parts), fact_ids

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
        applicant_name: str | None,
        name_fact_ids: list[str],
        contact_info_text: str | None,
        contact_fact_ids: list[str],
        artifact_set_id: str,
        input_hash: str,
        grounding_snapshot_hash: str,
    ) -> PreparedArtifact:
        blocks: list[ArtifactBlock] = []

        # Header: Verified identity or clean structural header (never 'User <id>')
        if applicant_name and name_fact_ids:
            blocks.append(
                ArtifactBlock(
                    block_type="header",
                    content=applicant_name,
                    grounding_type=ArtifactGroundingType.CONTEXT_FACT,
                    fact_ids=name_fact_ids,
                )
            )
        else:
            blocks.append(
                ArtifactBlock(
                    block_type="header",
                    content="Curriculum Vitae",
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

        # Contact Info (only if verified)
        if contact_info_text and contact_fact_ids:
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

        # Build summary strictly from top 1-2 claims
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

        # Bullets from all supported claims strictly from canonical claim text
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
        applicant_name: str | None,
        name_fact_ids: list[str],
        artifact_set_id: str,
        input_hash: str,
        grounding_snapshot_hash: str,
    ) -> PreparedArtifact:
        blocks: list[ArtifactBlock] = []

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

        # Opening paragraph: assembled strictly from canonical claim text + opportunity fields
        lead_claim = supported_claims[0]
        opening_text = (
            f"I am writing to express my interest in the {opportunity.title} position at {org}. "
            f"{lead_claim.text}"
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

        # Body paragraph(s) from remaining claims: strictly assembled from canonical text
        if len(supported_claims) > 1:
            body_claims = supported_claims[1:4]
            body_text = " ".join(c.text for c in body_claims)
            blocks.append(
                ArtifactBlock(
                    block_type="paragraph",
                    content=body_text,
                    grounding_type=ArtifactGroundingType.CLAIM,
                    claim_ids=[c.claim_id for c in body_claims],
                )
            )

        # Closing paragraph: Allowlisted non-factual connective template
        closing_text = (
            f"Thank you for your consideration. I look forward to discussing how my experience aligns with the needs of {org}."
        )
        blocks.append(
            ArtifactBlock(
                block_type="paragraph",
                content=closing_text,
                grounding_type=ArtifactGroundingType.STRUCTURAL,
                opportunity_fields=["organization"],
            )
        )

        # Sign-off: Grounded if name verified, structural otherwise
        if applicant_name and name_fact_ids:
            blocks.append(
                ArtifactBlock(
                    block_type="footer",
                    content=f"Sincerely,\n{applicant_name}",
                    grounding_type=ArtifactGroundingType.CONTEXT_FACT,
                    fact_ids=name_fact_ids,
                )
            )
        else:
            blocks.append(
                ArtifactBlock(
                    block_type="footer",
                    content="Sincerely,",
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

    def _get_claim_kind(self, claim: Claim) -> AssertionKind | None:
        if claim.assertion_json:
            try:
                import json
                data = json.loads(claim.assertion_json)
                k = data.get("kind")
                if k:
                    return AssertionKind(k)
            except Exception:
                pass
        return None

    def _get_claim_skill(self, claim: Claim) -> str | None:
        if claim.assertion_json:
            try:
                import json
                data = json.loads(claim.assertion_json)
                fields = data.get("fields", {})
                return fields.get("skill") or fields.get("project_name") or fields.get("role")
            except Exception:
                pass
        return None

    def _match_claim_for_question(self, question: str, supported_claims: list[Claim]) -> Claim | None:
        """Deterministically match a screening question to a relevant supported claim."""
        q_lower = question.lower()

        # 1. Work authorization / visa / sponsorship
        auth_keywords = [
            "visa",
            "sponsor",
            "sponsorship",
            "authorized",
            "authorization",
            "permit",
            "citizen",
            "citizenship",
            "work eligibility",
            "eligible to work",
            "right to work",
        ]
        if any(kw in q_lower for kw in auth_keywords):
            for c in supported_claims:
                spec_kind = self._get_claim_kind(c)
                if spec_kind == AssertionKind.WORK_AUTHORIZATION or "authorized to work" in c.text.lower():
                    return c

        # 2. Language proficiency
        lang_keywords = ["language", "english", "german", "french", "spanish", "fluent", "proficiency", "cefr"]
        if any(kw in q_lower for kw in lang_keywords):
            for c in supported_claims:
                spec_kind = self._get_claim_kind(c)
                if spec_kind == AssertionKind.LANGUAGE_PROFICIENCY or "proficient in" in c.text.lower():
                    for specific_lang in ["english", "german", "french", "spanish"]:
                        if specific_lang in q_lower:
                            if specific_lang in c.text.lower():
                                return c
                    return c

        # 3. Employment duration / years of experience
        duration_keywords = [
            "years of experience",
            "how many years",
            "years have you",
            "experience duration",
            "duration of experience",
            "years' experience",
            "years of professional",
        ]
        if any(kw in q_lower for kw in duration_keywords):
            for c in supported_claims:
                spec_kind = self._get_claim_kind(c)
                if (
                    spec_kind == AssertionKind.EMPLOYMENT_DURATION
                    or "years of experience" in c.text.lower()
                    or "years of professional" in c.text.lower()
                ):
                    return c

        # 4. Specific technical skill / domain
        for c in supported_claims:
            skill_name = self._get_claim_skill(c)
            if skill_name and skill_name.lower() in q_lower:
                return c

        for c in supported_claims:
            words = [
                w
                for w in re.findall(r"\b[a-zA-Z]{4,}\b", c.text.lower())
                if w not in ("experience", "possesses", "engineered", "demonstrated", "served")
            ]
            if any(w in q_lower for w in words):
                return c

        return None

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

        for q in questions:
            matching_claim = self._match_claim_for_question(q, supported_claims)
            if matching_claim is not None:
                blocks.append(
                    ArtifactBlock(
                        block_type="qna_item",
                        content=f"Q: {q}\n\nA: {matching_claim.text}",
                        grounding_type=ArtifactGroundingType.CLAIM,
                        claim_ids=[matching_claim.claim_id],
                        opportunity_fields=["organization"],
                        metadata={
                            "question": q,
                            "answer_status": "ANSWERED",
                            "answer": matching_claim.text,
                        },
                    )
                )
            else:
                blocks.append(
                    ArtifactBlock(
                        block_type="qna_item",
                        content=f"Q: {q}",
                        grounding_type=ArtifactGroundingType.STRUCTURAL,
                        claim_ids=[],
                        opportunity_fields=["organization"],
                        metadata={
                            "question": q,
                            "answer_status": "NEEDS_USER_INPUT",
                            "answer": None,
                        },
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
                    f"Preparation Answer Bank: Structured responses for common application questions, "
                    f"tailored for {org} and grounded in verified vault claims."
                ),
                grounding_type=ArtifactGroundingType.STRUCTURAL,
                opportunity_fields=["organization"],
            )
        )

        lead_claim = supported_claims[0]
        achievement_claim = next(
            (
                c
                for c in supported_claims
                if self._get_claim_kind(c) in (AssertionKind.QUANTIFIED_ACHIEVEMENT, AssertionKind.PROJECT_EXPERIENCE)
            ),
            None,
        )
        skill_claims = [c for c in supported_claims if self._get_claim_kind(c) == AssertionKind.SKILL_POSSESSION]
        auth_claim = next(
            (
                c
                for c in supported_claims
                if self._get_claim_kind(c) == AssertionKind.WORK_AUTHORIZATION
                or "authorized to work" in c.text.lower()
            ),
            None,
        )

        # Question 1: Role interest
        q1 = f"Why are you interested in this role at {org}?"
        a1 = f"I am targeting the {role} position at {org} based on my qualifications: {lead_claim.text}"
        blocks.append(
            ArtifactBlock(
                block_type="qna_item",
                content=f"Q: {q1}\n\nA: {a1}",
                grounding_type=ArtifactGroundingType.CLAIM,
                claim_ids=[lead_claim.claim_id],
                opportunity_fields=["organization", "title"],
                metadata={"question": q1, "answer_status": "ANSWERED", "answer": a1},
            )
        )

        # Question 2: Technical accomplishment
        q2 = "Describe a significant technical accomplishment or complex challenge you solved."
        if achievement_claim is not None:
            a2 = achievement_claim.text
            blocks.append(
                ArtifactBlock(
                    block_type="qna_item",
                    content=f"Q: {q2}\n\nA: {a2}",
                    grounding_type=ArtifactGroundingType.CLAIM,
                    claim_ids=[achievement_claim.claim_id],
                    opportunity_fields=[],
                    metadata={"question": q2, "answer_status": "ANSWERED", "answer": a2},
                )
            )
        else:
            blocks.append(
                ArtifactBlock(
                    block_type="qna_item",
                    content=f"Q: {q2}",
                    grounding_type=ArtifactGroundingType.STRUCTURAL,
                    claim_ids=[],
                    opportunity_fields=[],
                    metadata={"question": q2, "answer_status": "NEEDS_USER_INPUT", "answer": None},
                )
            )

        # Question 3: Core competencies
        q3 = "What are your core technical competencies and areas of expertise?"
        if skill_claims:
            a3 = " ".join(c.text for c in skill_claims[:3])
            blocks.append(
                ArtifactBlock(
                    block_type="qna_item",
                    content=f"Q: {q3}\n\nA: {a3}",
                    grounding_type=ArtifactGroundingType.CLAIM,
                    claim_ids=[c.claim_id for c in skill_claims[:3]],
                    opportunity_fields=[],
                    metadata={"question": q3, "answer_status": "ANSWERED", "answer": a3},
                )
            )
        else:
            blocks.append(
                ArtifactBlock(
                    block_type="qna_item",
                    content=f"Q: {q3}",
                    grounding_type=ArtifactGroundingType.STRUCTURAL,
                    claim_ids=[],
                    opportunity_fields=[],
                    metadata={"question": q3, "answer_status": "NEEDS_USER_INPUT", "answer": None},
                )
            )

        # Question 4: Work authorization
        q4 = "What is your work authorization status?"
        if auth_claim is not None:
            a4 = auth_claim.text
            blocks.append(
                ArtifactBlock(
                    block_type="qna_item",
                    content=f"Q: {q4}\n\nA: {a4}",
                    grounding_type=ArtifactGroundingType.CLAIM,
                    claim_ids=[auth_claim.claim_id],
                    opportunity_fields=[],
                    metadata={"question": q4, "answer_status": "ANSWERED", "answer": a4},
                )
            )
        else:
            blocks.append(
                ArtifactBlock(
                    block_type="qna_item",
                    content=f"Q: {q4}",
                    grounding_type=ArtifactGroundingType.STRUCTURAL,
                    claim_ids=[],
                    opportunity_fields=[],
                    metadata={"question": q4, "answer_status": "NEEDS_USER_INPUT", "answer": None},
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
        context_facts_by_id: dict[str, ContextFact] | None = None,
        opportunity: Opportunity | None = None,
    ) -> None:
        """Enforce Phase 5.2.2.1 strict claim-bound invariants."""
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

        # 2. Check block-level grounding rules
        for block in artifact.blocks:
            if block.grounding_type == ArtifactGroundingType.STRUCTURAL:
                if block.claim_ids:
                    raise DomainValidationError(
                        f"Structural block '{block.block_id}' must not have claim_ids."
                    )
                if block.fact_ids:
                    raise DomainValidationError(
                        f"Structural block '{block.block_id}' must not have fact_ids."
                    )

            elif block.grounding_type == ArtifactGroundingType.CLAIM:
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

            elif block.grounding_type == ArtifactGroundingType.CONTEXT_FACT:
                if not block.fact_ids:
                    raise DomainValidationError(
                        f"Block '{block.block_id}' is CONTEXT_FACT grounded but has no fact_ids."
                    )
                if context_facts_by_id:
                    for fid in block.fact_ids:
                        if fid not in context_facts_by_id:
                            raise DomainValidationError(
                                f"Block '{block.block_id}' references unknown fact '{fid}'."
                            )
                        fact = context_facts_by_id[fid]
                        if not is_fact_verified(fact.verification_status):
                            raise DomainValidationError(
                                f"Block '{block.block_id}' references unverified fact '{fid}'."
                            )
                        if "job_application" not in (fact.allowed_purposes or []):
                            raise DomainValidationError(
                                f"Block '{block.block_id}' references fact '{fid}' not authorized for job_application."
                            )

            elif block.grounding_type == ArtifactGroundingType.OPPORTUNITY:
                if not block.opportunity_fields:
                    raise DomainValidationError(
                        f"Block '{block.block_id}' is OPPORTUNITY grounded but has no opportunity_fields."
                    )
                if opportunity:
                    for field_name in block.opportunity_fields:
                        val = getattr(opportunity, field_name, None)
                        if not val and field_name not in opportunity.metadata:
                            raise DomainValidationError(
                                f"Block '{block.block_id}' references missing opportunity field '{field_name}'."
                            )
