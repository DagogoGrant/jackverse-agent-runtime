"""Comprehensive unit tests for Phase 5.2.1 Preparation Intelligence."""

import json
from pathlib import Path
import tempfile
import unittest

from caseworker.domain.case import Case
from caseworker.domain.context import ContextFact
from caseworker.domain.enums import (
    CaseStatus,
    CaseType,
    EvidenceAlignmentStatus,
    GapCategory,
    OpportunityStatus,
    OpportunityType,
    PreparationMaterialType,
    RequirementCategory,
    VerificationStatus,
)
from caseworker.domain.normalized_requirement import NormalizedRequirement
from caseworker.domain.opportunity import Opportunity
from caseworker.domain.preparation import (
    EvidenceMappingItem,
    GapItem,
    PreparationIntelligenceReport,
    PreparationStrategy,
    compute_prep_context_snapshot_hash,
    compute_prep_input_hash,
)
from caseworker.domain.preparation_analyzer import PreparationAnalyzer
from caseworker.domain.vault_policy import AccessDecision
from caseworker.persistence.sqlite import SQLiteCaseworkerStorage
from caseworker.services.case_service import CaseService


class TestPreparationIntelligence(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "test_caseworker.db"
        self.storage = SQLiteCaseworkerStorage(str(self.db_path))
        self.service = CaseService(self.storage)
        self.user_id = "test-user-prep"

    def tearDown(self) -> None:
        self.storage.close()
        self.temp_dir.cleanup()

    def test_five_evidence_alignment_states_and_three_gap_classes(self) -> None:
        """Verify all 5 evidence states and 3 gap classes are correctly computed."""
        case = Case(
            case_id="case-101",
            user_id=self.user_id,
            title="Senior Backend Role",
            goal="Secure Senior Backend position",
            case_type=CaseType.JOB_APPLICATION,
        )

        opportunity = Opportunity(
            opportunity_id="opp-101",
            user_id=self.user_id,
            title="Senior Backend Engineer",
            organization="Tech Corp",
            opportunity_type=OpportunityType.JOB,
            location="Berlin",
            requirements=[
                "Python expertise (Mandatory)",
                "German B2 (Mandatory)",
                "5+ years experience (Mandatory)",
                "Docker experience (Preferred)",
                "Kubernetes mastery (Mandatory)",
                "Security clearance (Mandatory)",
            ],
            converted_case_id=case.case_id,
        )

        # Explicitly configure normalized requirements for deterministic testing
        opportunity.metadata["normalized_requirements"] = [
            NormalizedRequirement(
                requirement_id="req-py",
                raw_text="Python expertise",
                category=RequirementCategory.MANDATORY,
                criterion="skill",
                value="Python",
            ).to_dict(),
            NormalizedRequirement(
                requirement_id="req-ger",
                raw_text="German B2",
                category=RequirementCategory.MANDATORY,
                criterion="language",
                value="German B2",
            ).to_dict(),
            NormalizedRequirement(
                requirement_id="req-exp",
                raw_text="5+ years experience",
                category=RequirementCategory.MANDATORY,
                criterion="years_experience",
                value=5,
            ).to_dict(),
            NormalizedRequirement(
                requirement_id="req-doc",
                raw_text="Docker experience",
                category=RequirementCategory.PREFERRED,
                criterion="skill",
                value="Docker",
            ).to_dict(),
            NormalizedRequirement(
                requirement_id="req-k8s",
                raw_text="Kubernetes mastery",
                category=RequirementCategory.MANDATORY,
                criterion="skill",
                value="Kubernetes",
            ).to_dict(),
            NormalizedRequirement(
                requirement_id="req-sec",
                raw_text="Security clearance",
                category=RequirementCategory.MANDATORY,
                criterion="custom",
                value="Security clearance",
            ).to_dict(),
        ]

        # 1. SUPPORTED: Python verified
        fact_py = ContextFact(
            fact_id="f-py",
            user_id=self.user_id,
            namespace="skills",
            key="programming",
            value="Python senior dev",
            verification_status=VerificationStatus.USER_VERIFIED,
            allowed_purposes=["case_preparation"],
        )

        # 2. CONTRADICTED: German A2 verified (lower than B2)
        fact_ger = ContextFact(
            fact_id="f-ger",
            user_id=self.user_id,
            namespace="languages",
            key="german",
            value="German A2 elementary",
            verification_status=VerificationStatus.USER_VERIFIED,
            allowed_purposes=["case_preparation"],
        )

        # 3. POTENTIAL: 6 years experience documented but UNVERIFIED
        fact_exp = ContextFact(
            fact_id="f-exp",
            user_id=self.user_id,
            namespace="experience",
            key="years_experience",
            value="6 years experience",
            verification_status=VerificationStatus.UNVERIFIED,
            allowed_purposes=["case_preparation"],
        )

        # 4. RESTRICTED: Kubernetes fact exists in vault, but purpose 'case_preparation' is denied
        fact_k8s = ContextFact(
            fact_id="f-k8s",
            user_id=self.user_id,
            namespace="skills",
            key="kubernetes",
            value="Kubernetes production",
            verification_status=VerificationStatus.USER_VERIFIED,
            allowed_purposes=["internal_audit"],  # Not authorized for case_preparation
        )

        # 5. GAP: Docker (no fact in vault) and Security clearance (custom, no fact)

        granted_facts = [fact_py, fact_ger, fact_exp]
        denied_facts = [fact_k8s]
        denied_decisions = [
            AccessDecision.deny(
                fact_id=fact_k8s.fact_id,
                reason="Purpose 'case_preparation' not authorized",
                reason_code="PURPOSE_NOT_AUTHORIZED",
                namespace="skills",
                key="kubernetes",
            )
        ]

        report = PreparationAnalyzer.analyze(
            case=case,
            opportunity=opportunity,
            granted_facts=granted_facts,
            denied_facts=denied_facts,
            denied_decisions=denied_decisions,
        )

        ev_by_req = {e.requirement_id: e for e in report.evidence_map}

        # Verify 5 Evidence Alignment States
        self.assertEqual(ev_by_req["req-py"].status, EvidenceAlignmentStatus.SUPPORTED)
        self.assertEqual(ev_by_req["req-ger"].status, EvidenceAlignmentStatus.CONTRADICTED)
        self.assertEqual(ev_by_req["req-exp"].status, EvidenceAlignmentStatus.POTENTIAL)
        self.assertEqual(ev_by_req["req-k8s"].status, EvidenceAlignmentStatus.RESTRICTED)
        self.assertEqual(ev_by_req["req-doc"].status, EvidenceAlignmentStatus.GAP)
        self.assertEqual(ev_by_req["req-sec"].status, EvidenceAlignmentStatus.GAP)

        # Verify Gap Categories
        gaps_by_req = {g.requirement_id: g for g in report.gaps}

        # req-py is SUPPORTED -> no gap
        self.assertNotIn("req-py", gaps_by_req)

        # req-ger is CONTRADICTED -> HARD_CONFLICT
        self.assertEqual(gaps_by_req["req-ger"].category, GapCategory.HARD_CONFLICT)
        self.assertEqual(gaps_by_req["req-ger"].severity, "critical")

        # req-exp is POTENTIAL but MANDATORY -> MANDATORY_EVIDENCE_GAP
        self.assertEqual(gaps_by_req["req-exp"].category, GapCategory.MANDATORY_EVIDENCE_GAP)
        self.assertEqual(gaps_by_req["req-exp"].severity, "critical")

        # req-k8s is RESTRICTED and MANDATORY -> MANDATORY_EVIDENCE_GAP
        self.assertEqual(gaps_by_req["req-k8s"].category, GapCategory.MANDATORY_EVIDENCE_GAP)
        self.assertEqual(gaps_by_req["req-k8s"].severity, "critical")

        # req-doc is GAP but PREFERRED -> PREFERRED_EVIDENCE_GAP
        self.assertEqual(gaps_by_req["req-doc"].category, GapCategory.PREFERRED_EVIDENCE_GAP)
        self.assertEqual(gaps_by_req["req-doc"].severity, "moderate")

        # Verify Strategy Claim Intents formulated for supported and potential items
        intents_by_req = {ci.requirement_id: ci for ci in report.strategy.claim_intents}
        self.assertIn("req-py", intents_by_req)
        self.assertEqual(intents_by_req["req-py"].target_material, PreparationMaterialType.CV)
        self.assertEqual(intents_by_req["req-py"].supporting_fact_ids, ["f-py"])

        self.assertIn("req-exp", intents_by_req)

    def test_case_service_lifecycle_progression_and_audit_events(self) -> None:
        """Verify Case lifecycle advances NEW -> INTAKE -> INVESTIGATING -> PLANNING and audit events are emitted."""
        with self.storage.unit_of_work() as uow:
            case = Case(
                user_id=self.user_id,
                title="Frontend Lead Role",
                goal="Prepare application for Frontend Lead",
                case_type=CaseType.JOB_APPLICATION,
            )
            uow.cases.save(case)

            opp = Opportunity(
                user_id=self.user_id,
                title="Frontend Lead",
                organization="Scale AI",
                opportunity_type=OpportunityType.JOB,
                location="Remote",
                requirements=["React proficiency (Mandatory)", "TypeScript (Preferred)"],
                converted_case_id=case.case_id,
                status=OpportunityStatus.SHORTLISTED,
            )
            uow.opportunities.save(opp)

            # Add context fact with purpose case_preparation
            f1 = ContextFact(
                fact_id="f-react",
                user_id=self.user_id,
                namespace="skills",
                key="react",
                value="Expert React 19",
                verification_status=VerificationStatus.USER_VERIFIED,
                allowed_purposes=["case_preparation"],
            )
            # Add context fact without purpose case_preparation (denied)
            f2 = ContextFact(
                fact_id="f-ts",
                user_id=self.user_id,
                namespace="skills",
                key="typescript",
                value="TypeScript advanced",
                verification_status=VerificationStatus.USER_VERIFIED,
                allowed_purposes=["tax_filing"],
            )
            uow.context.save(f1)
            uow.context.save(f2)

        # Initial state is NEW
        case_before = self.service.get_case(case.case_id)
        self.assertIsNotNone(case_before)
        self.assertEqual(case_before.status, CaseStatus.NEW)

        # Generate preparation intelligence
        report = self.service.generate_preparation_intelligence(
            user_id=self.user_id,
            case_id=case.case_id,
        )

        # Case must now be in PLANNING
        case_after = self.service.get_case(case.case_id)
        self.assertIsNotNone(case_after)
        self.assertEqual(case_after.status, CaseStatus.PLANNING)

        # Verify report persistence
        with self.storage.unit_of_work() as uow:
            saved_report = uow.preparation_reports.get_by_id(report.report_id)
            self.assertIsNotNone(saved_report)
            self.assertEqual(saved_report.case_id, case.case_id)
            self.assertEqual(saved_report.opportunity_id, opp.opportunity_id)
            self.assertEqual(saved_report.version, 1)

            # Verify audit events emitted
            events = uow.events.get_events_by_user(self.user_id, limit=20)
            event_types = [e.event_type for e in events]
            self.assertIn("context.access_granted", event_types)
            self.assertIn("context.access_denied", event_types)
            self.assertIn("case.status_changed", event_types)
            self.assertIn("preparation.report_created", event_types)

    def test_report_staleness_detection(self) -> None:
        """Verify report staleness is detected when context facts or opportunity requirements change."""
        with self.storage.unit_of_work() as uow:
            case = Case(
                user_id=self.user_id,
                title="Staff Architect",
                goal="Prepare Staff Architect package",
                case_type=CaseType.JOB_APPLICATION,
            )
            uow.cases.save(case)

            opp = Opportunity(
                user_id=self.user_id,
                title="Staff Architect",
                organization="Google DeepMind",
                opportunity_type=OpportunityType.JOB,
                location="London",
                requirements=["Distributed Systems (Mandatory)"],
                converted_case_id=case.case_id,
                status=OpportunityStatus.SHORTLISTED,
            )
            uow.opportunities.save(opp)

            fact = ContextFact(
                fact_id="f-dist",
                user_id=self.user_id,
                namespace="skills",
                key="distributed_systems",
                value="Raft, Paxos, Spanner",
                verification_status=VerificationStatus.USER_VERIFIED,
                allowed_purposes=["case_preparation"],
            )
            uow.context.save(fact)

        report = self.service.generate_preparation_intelligence(self.user_id, case.case_id)

        # Initially, report is not stale
        latest_report, is_stale = self.service.get_latest_preparation_report(self.user_id, case.case_id)
        self.assertIsNotNone(latest_report)
        self.assertFalse(is_stale)

        # Mutate context fact (version increments)
        with self.storage.unit_of_work() as uow:
            fact.version += 1
            fact.value = "Updated distributed systems expertise"
            uow.context.save(fact)

        # Now report must be flagged stale
        _, is_stale_after = self.service.get_latest_preparation_report(self.user_id, case.case_id)
        self.assertTrue(is_stale_after)

    def test_privacy_invariant_no_raw_values_stored(self) -> None:
        """Verify no raw context values appear in EvidenceMappingItem or serialized report."""
        item = EvidenceMappingItem(
            requirement_id="req-1",
            requirement_type="skill",
            description="Python 3",
            mandatory=True,
            status=EvidenceAlignmentStatus.SUPPORTED,
            matched_fact_id="fact-secret-123",
            matched_fact_version=1,
            verification_status=VerificationStatus.USER_VERIFIED,
            fact_namespace="skills",
            fact_key="python",
            notes="Matched fact 'fact-secret-123'",
        )
        serialized = item.to_dict()
        self.assertNotIn("secret_salary", json.dumps(serialized))
        self.assertNotIn("raw_value", serialized)
        self.assertIn("matched_fact_id", serialized)


class TestPreparationIntelligenceAPI(unittest.TestCase):
    def setUp(self) -> None:
        from fastapi.testclient import TestClient
        from caseworker.api.app import create_app
        from caseworker.api.config import APIConfig

        self.storage = SQLiteCaseworkerStorage(db_path=":memory:")
        self.config = APIConfig(dev_auth=True)
        self.app = create_app(config=self.config, storage=self.storage)
        self.client = TestClient(self.app)
        self.user_headers = {"X-JackVerse-User": "user_prep_api"}
        self.other_user_headers = {"X-JackVerse-User": "user_other"}

    def tearDown(self) -> None:
        self.storage.close()

    def test_api_generate_and_get_preparation_report(self) -> None:
        user_id = "user_prep_api"
        with self.storage.unit_of_work() as uow:
            case = Case(
                user_id=user_id,
                title="ML Engineer Application",
                goal="Prepare ML Engineer application",
                case_type=CaseType.JOB_APPLICATION,
            )
            uow.cases.save(case)

            opp = Opportunity(
                user_id=user_id,
                title="ML Engineer",
                organization="Anthropic",
                opportunity_type=OpportunityType.JOB,
                location="San Francisco",
                requirements=["PyTorch (Mandatory)", "RLHF experience (Preferred)"],
                converted_case_id=case.case_id,
                status=OpportunityStatus.SHORTLISTED,
            )
            uow.opportunities.save(opp)

            fact = ContextFact(
                fact_id="f-pytorch",
                user_id=user_id,
                namespace="skills",
                key="pytorch",
                value="PyTorch transformer training",
                verification_status=VerificationStatus.USER_VERIFIED,
                allowed_purposes=["case_preparation"],
            )
            uow.context.save(fact)

        case_id = case.case_id

        # 1. GET before generation returns 404
        get_before = self.client.get(
            f"/api/v1/cases/{case_id}/preparation/intelligence",
            headers=self.user_headers,
        )
        self.assertEqual(get_before.status_code, 404)

        # 2. POST generates report
        post_res = self.client.post(
            f"/api/v1/cases/{case_id}/preparation/intelligence",
            headers=self.user_headers,
        )
        self.assertEqual(post_res.status_code, 201)
        data = post_res.json()
        self.assertEqual(data["case_id"], case_id)
        self.assertEqual(data["opportunity_id"], opp.opportunity_id)
        self.assertEqual(data["schema_version"], "prep_intel_v1")
        self.assertFalse(data["is_stale"])
        self.assertGreater(len(data["evidence_map"]), 0)

        # 3. GET retrieves report
        get_after = self.client.get(
            f"/api/v1/cases/{case_id}/preparation/intelligence",
            headers=self.user_headers,
        )
        self.assertEqual(get_after.status_code, 200)
        get_data = get_after.json()
        self.assertEqual(get_data["report_id"], data["report_id"])
        self.assertFalse(get_data["is_stale"])

        # 4. Multi-tenant isolation: other user cannot see report
        other_res = self.client.get(
            f"/api/v1/cases/{case_id}/preparation/intelligence",
            headers=self.other_user_headers,
        )
        self.assertEqual(other_res.status_code, 404)


if __name__ == "__main__":
    unittest.main()

