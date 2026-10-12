"""Comprehensive unit tests for Phase 5.2.1 and Phase 5.2.1.1 Preparation Intelligence."""

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
    SafeDeniedFactDescriptor,
    compute_prep_context_snapshot_hash,
    compute_prep_input_hash,
)
from caseworker.domain.preparation_analyzer import PreparationAnalyzer
from caseworker.domain.vault_policy import AccessDecision
from caseworker.domain.errors import PersistenceError
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

        # 4. RESTRICTED: Kubernetes fact exists in vault, but safe descriptor passed (not raw fact)
        fact_k8s_desc = SafeDeniedFactDescriptor(
            fact_id="f-k8s",
            fact_version=1,
            namespace="skills",
            key="kubernetes",
            verification_status=VerificationStatus.USER_VERIFIED,
            reason_code="PURPOSE_NOT_AUTHORIZED",
            reason="Purpose 'case_preparation' not authorized",
        )

        # 5. GAP: Docker (no fact in vault) and Security clearance (custom, no fact)

        granted_facts = [fact_py, fact_ger, fact_exp]
        denied_descriptors = [fact_k8s_desc]

        report = PreparationAnalyzer.analyze(
            case=case,
            opportunity=opportunity,
            granted_facts=granted_facts,
            denied_descriptors=denied_descriptors,
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
        self.assertEqual(gaps_by_req["req-exp"].severity, "high")

        # req-k8s is RESTRICTED and MANDATORY -> MANDATORY_EVIDENCE_GAP
        self.assertEqual(gaps_by_req["req-k8s"].category, GapCategory.MANDATORY_EVIDENCE_GAP)
        self.assertEqual(gaps_by_req["req-k8s"].severity, "high")

        # req-doc is GAP but PREFERRED -> PREFERRED_EVIDENCE_GAP
        self.assertEqual(gaps_by_req["req-doc"].category, GapCategory.PREFERRED_EVIDENCE_GAP)
        self.assertEqual(gaps_by_req["req-doc"].severity, "moderate")

        # Verify Strategy Claim Intents formulated ONLY for SUPPORTED items (Phase 5.2.1.1 invariant)
        intents_by_req = {ci.requirement_id: ci for ci in report.strategy.claim_intents}
        self.assertIn("req-py", intents_by_req)
        self.assertEqual(intents_by_req["req-py"].target_material, PreparationMaterialType.CV)
        self.assertEqual(intents_by_req["req-py"].supporting_fact_ids, ["f-py"])

        # POTENTIAL evidence MUST NOT become an assertable claim intent
        self.assertNotIn("req-exp", intents_by_req)

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
        report, updated_case = self.service.generate_preparation_intelligence(
            user_id=self.user_id,
            case_id=case.case_id,
        )

        # Case must now be in PLANNING
        case_after = self.service.get_case(case.case_id)
        self.assertIsNotNone(case_after)
        self.assertEqual(case_after.status, CaseStatus.PLANNING)
        self.assertEqual(updated_case.status, CaseStatus.PLANNING)

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

            # CRITICAL INVARIANT: preparation.report_created event uses own aggregate stream
            prep_events = [e for e in events if e.event_type == "preparation.report_created"]
            self.assertEqual(len(prep_events), 1)
            self.assertEqual(prep_events[0].aggregate_type, "preparation_report")
            self.assertEqual(prep_events[0].aggregate_id, report.report_id)
            self.assertEqual(prep_events[0].aggregate_version, 1)
            self.assertEqual(prep_events[0].payload["case_id"], case.case_id)

    def test_reanalysis_from_planning_without_event_version_collision(self) -> None:
        """Verify re-analysis from PLANNING succeeds without aggregate event collision and streams remain monotonic."""
        with self.storage.unit_of_work() as uow:
            case = Case(
                user_id=self.user_id,
                title="Principal Systems Engineer",
                goal="Prepare Principal Systems application",
                case_type=CaseType.JOB_APPLICATION,
            )
            uow.cases.save(case)

            opp = Opportunity(
                user_id=self.user_id,
                title="Principal Systems Engineer",
                organization="NVIDIA",
                opportunity_type=OpportunityType.JOB,
                location="Munich",
                requirements=["C++ proficiency (Mandatory)"],
                converted_case_id=case.case_id,
                status=OpportunityStatus.SHORTLISTED,
            )
            uow.opportunities.save(opp)

            fact = ContextFact(
                fact_id="f-cpp",
                user_id=self.user_id,
                namespace="skills",
                key="cpp",
                value="Modern C++ 20",
                verification_status=VerificationStatus.USER_VERIFIED,
                allowed_purposes=["case_preparation"],
            )
            uow.context.save(fact)

        # 1. Initial report generation
        report1, case_v1 = self.service.generate_preparation_intelligence(
            user_id=self.user_id,
            case_id=case.case_id,
        )
        self.assertEqual(case_v1.status, CaseStatus.PLANNING)

        # 2. Re-analysis from PLANNING
        report2, case_v2 = self.service.generate_preparation_intelligence(
            user_id=self.user_id,
            case_id=case.case_id,
            expected_case_version=case_v1.version,
        )
        self.assertNotEqual(report1.report_id, report2.report_id)
        self.assertEqual(case_v2.status, CaseStatus.PLANNING)

        # Verify event stream integrity and monotonicity
        with self.storage.unit_of_work() as uow:
            case_events = uow.events.get_events_for_aggregate("case", case.case_id)
            versions = [e.aggregate_version for e in case_events]
            # Ensure versions strictly monotonically increase without collisions
            self.assertEqual(versions, sorted(versions))
            self.assertEqual(len(versions), len(set(versions)))

            # Verify report 1 and report 2 each have exactly 1 event on their own stream
            r1_events = uow.events.get_events_for_aggregate("preparation_report", report1.report_id)
            self.assertEqual(len(r1_events), 1)
            self.assertEqual(r1_events[0].aggregate_version, 1)

            r2_events = uow.events.get_events_for_aggregate("preparation_report", report2.report_id)
            self.assertEqual(len(r2_events), 1)
            self.assertEqual(r2_events[0].aggregate_version, 1)

    def test_denied_sensitive_fact_values_cannot_leak(self) -> None:
        """Verify denied sensitive fact values never leak into report notes, strategy, gaps, or claim intents."""
        secret_value = "TOP_SECRET_CODE_NAME_VALKYRIE_99"
        with self.storage.unit_of_work() as uow:
            case = Case(
                user_id=self.user_id,
                title="Staff Security Role",
                goal="Prepare security package",
                case_type=CaseType.JOB_APPLICATION,
            )
            uow.cases.save(case)

            opp = Opportunity(
                user_id=self.user_id,
                title="Staff Security Engineer",
                organization="Defence Corp",
                opportunity_type=OpportunityType.JOB,
                location="Berlin",
                requirements=["Security clearance (Mandatory)"],
                converted_case_id=case.case_id,
                status=OpportunityStatus.SHORTLISTED,
            )
            uow.opportunities.save(opp)

            # Fact with secret value exists, but is NOT authorized for case_preparation
            fact_secret = ContextFact(
                fact_id="f-secret",
                user_id=self.user_id,
                namespace="security",
                key="clearance",
                value=secret_value,
                verification_status=VerificationStatus.USER_VERIFIED,
                allowed_purposes=["gov_audit"],  # Not authorized for case_preparation
            )
            uow.context.save(fact_secret)

        report, _ = self.service.generate_preparation_intelligence(self.user_id, case.case_id)

        # Inspect serialized report
        report_json = json.dumps(report.to_dict())
        self.assertNotIn(secret_value, report_json)

        # Inspect all fields
        for ev in report.evidence_map:
            self.assertNotIn(secret_value, ev.notes)
            self.assertNotIn(secret_value, ev.description)
        for g in report.gaps:
            self.assertNotIn(secret_value, g.description)
            self.assertNotIn(secret_value, g.mitigation_guidance)
        for s in report.strategy.strengths:
            self.assertNotIn(secret_value, s)
        for m in report.strategy.mitigations:
            self.assertNotIn(secret_value, m)
        for ci in report.strategy.claim_intents:
            self.assertNotIn(secret_value, ci.suggested_angle)

    def test_report_staleness_dependencies(self) -> None:
        """Verify fine-grained staleness dependencies: irrelevant facts do not invalidate, relevant facts and inputs do."""
        with self.storage.unit_of_work() as uow:
            case = Case(
                user_id=self.user_id,
                title="Staff Architect",
                goal="Prepare Staff Architect package",
                case_type=CaseType.JOB_APPLICATION,
                constraints=["Must be EU timezone"],
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
                metadata={"evaluation": {"evaluation_id": "eval-v1"}},
            )
            uow.opportunities.save(opp)

            fact_relevant = ContextFact(
                fact_id="f-dist",
                user_id=self.user_id,
                namespace="skills",
                key="distributed_systems",
                value="Raft, Paxos, Spanner",
                verification_status=VerificationStatus.USER_VERIFIED,
                allowed_purposes=["case_preparation"],
            )
            fact_unrelated = ContextFact(
                fact_id="f-phone",
                user_id=self.user_id,
                namespace="contact",
                key="phone_number",
                value="+49123456789",
                verification_status=VerificationStatus.USER_VERIFIED,
                allowed_purposes=["case_preparation"],
            )
            uow.context.save(fact_relevant)
            uow.context.save(fact_unrelated)

        report, _ = self.service.generate_preparation_intelligence(self.user_id, case.case_id)

        # 1. Initially fresh
        _, is_stale = self.service.get_latest_preparation_report(self.user_id, case.case_id)
        self.assertFalse(is_stale)

        # 2. Mutating an UNRELATED fact (phone number) does NOT make report stale
        with self.storage.unit_of_work() as uow:
            fact_unrelated.version += 1
            fact_unrelated.value = "+49987654321"
            uow.context.save(fact_unrelated)

        _, is_stale_phone = self.service.get_latest_preparation_report(self.user_id, case.case_id)
        self.assertFalse(is_stale_phone)

        # 3. Mutating a RELEVANT fact (distributed systems) DOES make report stale
        with self.storage.unit_of_work() as uow:
            fact_relevant.version += 1
            fact_relevant.value = "Updated distributed systems expertise"
            uow.context.save(fact_relevant)

        _, is_stale_dist = self.service.get_latest_preparation_report(self.user_id, case.case_id)
        self.assertTrue(is_stale_dist)

        # Re-generate to reset freshness
        report2, c2 = self.service.generate_preparation_intelligence(self.user_id, case.case_id)
        _, is_fresh2 = self.service.get_latest_preparation_report(self.user_id, case.case_id)
        self.assertFalse(is_fresh2)

        # 4. Opportunity re-evaluation (evaluation_id change) makes report stale
        with self.storage.unit_of_work() as uow:
            opp_db = uow.opportunities.get_by_id(opp.opportunity_id)
            opp_db.metadata["evaluation"] = {"evaluation_id": "eval-v2"}
            opp_db.version += 1
            uow.opportunities.save(opp_db)

        _, is_stale_eval = self.service.get_latest_preparation_report(self.user_id, case.case_id)
        self.assertTrue(is_stale_eval)

        # Re-generate to reset freshness
        report3, c3 = self.service.generate_preparation_intelligence(self.user_id, case.case_id)
        _, is_fresh3 = self.service.get_latest_preparation_report(self.user_id, case.case_id)
        self.assertFalse(is_fresh3)

        # 5. Case constraint change makes report stale
        with self.storage.unit_of_work() as uow:
            case_db = uow.cases.get_by_id(case.case_id)
            case_db.constraints.append("Must permit 100% remote work")
            case_db.version += 1
            uow.cases.save(case_db)

        _, is_stale_constraints = self.service.get_latest_preparation_report(self.user_id, case.case_id)
        self.assertTrue(is_stale_constraints)

    def test_case_type_truthfulness_material_strategy(self) -> None:
        """Verify JOB_APPLICATION recommends CV + Cover Letter, while other types return empty recommendations."""
        # 1. Job application case
        case_job = Case(
            user_id=self.user_id,
            title="Software Engineer",
            goal="Secure SE role",
            case_type=CaseType.JOB_APPLICATION,
        )
        opp_job = Opportunity(
            user_id=self.user_id,
            title="Software Engineer",
            organization="Tech GmbH",
            opportunity_type=OpportunityType.JOB,
            location="Berlin",
            requirements=["Python (Mandatory)"],
        )
        report_job = PreparationAnalyzer.analyze(case=case_job, opportunity=opp_job, granted_facts=[])
        self.assertIn(PreparationMaterialType.CV, report_job.strategy.recommended_materials)
        self.assertIn(PreparationMaterialType.COVER_LETTER, report_job.strategy.recommended_materials)
        self.assertGreater(len(report_job.strategy.materials_checklist), 0)

        # 2. Non-job case (e.g. Housing or General)
        case_housing = Case(
            user_id=self.user_id,
            title="Apartment Search",
            goal="Secure 2-room apartment",
            case_type=CaseType.HOUSING_SEARCH,
        )
        opp_housing = Opportunity(
            user_id=self.user_id,
            title="2-Room Apartment",
            organization="Property Co",
            opportunity_type=OpportunityType.JOB,
            location="Berlin",
            requirements=["Proof of income (Mandatory)"],
        )
        report_housing = PreparationAnalyzer.analyze(case=case_housing, opportunity=opp_housing, granted_facts=[])
        self.assertEqual(report_housing.strategy.recommended_materials, [])
        self.assertEqual(report_housing.strategy.materials_checklist, [])
        self.assertTrue(any("not supported in Phase 5.2" in m for m in report_housing.strategy.mitigations))

    def test_immutable_preparation_report_repository(self) -> None:
        """Verify PreparationReportRepository enforces insert-only semantics and rejects mutation of existing snapshots."""
        with self.storage.unit_of_work() as uow:
            case = Case(
                case_id="case-immut",
                user_id=self.user_id,
                title="Immutability Test",
                goal="Testing immutable reports",
                case_type=CaseType.JOB_APPLICATION,
            )
            uow.cases.save(case)

            opp = Opportunity(
                opportunity_id="opp-immut",
                user_id=self.user_id,
                title="Immutability Role",
                organization="Test Corp",
                opportunity_type=OpportunityType.JOB,
                location="Remote",
                requirements=["Testing (Mandatory)"],
                converted_case_id=case.case_id,
            )
            uow.opportunities.save(opp)

            report = PreparationIntelligenceReport(
                report_id="prep-immut-1",
                case_id=case.case_id,
                opportunity_id=opp.opportunity_id,
                user_id=self.user_id,
                schema_version="prep_intel_v1",
                analyzer_version="prep_analyzer_v1",
            )
            uow.preparation_reports.save(report)

        # Attempt to save again with same report_id must raise PersistenceError
        with self.storage.unit_of_work() as uow:
            with self.assertRaises(PersistenceError) as cm:
                uow.preparation_reports.save(report)
            self.assertIn("immutable and cannot be updated", str(cm.exception))

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

        # 2. POST without If-Match returns 428 Precondition Required
        post_missing_etag = self.client.post(
            f"/api/v1/cases/{case_id}/preparation/intelligence",
            headers=self.user_headers,
        )
        self.assertEqual(post_missing_etag.status_code, 428)

        # 3. POST with stale If-Match returns 412 Precondition Failed
        post_stale_etag = self.client.post(
            f"/api/v1/cases/{case_id}/preparation/intelligence",
            headers={**self.user_headers, "If-Match": f'"case:{case_id}:v999"'},
        )
        self.assertEqual(post_stale_etag.status_code, 412)

        # 4. POST with correct If-Match generates report, returns 201 and Case ETag
        valid_etag = f'"case:{case_id}:v{case.version}"'
        post_res = self.client.post(
            f"/api/v1/cases/{case_id}/preparation/intelligence",
            headers={**self.user_headers, "If-Match": valid_etag},
        )
        self.assertEqual(post_res.status_code, 201)
        self.assertIn("etag", post_res.headers)
        data = post_res.json()
        self.assertEqual(data["case_id"], case_id)
        self.assertEqual(data["opportunity_id"], opp.opportunity_id)
        self.assertEqual(data["schema_version"], "prep_intel_v1")
        self.assertFalse(data["is_stale"])
        self.assertGreater(len(data["evidence_map"]), 0)

        # 5. GET retrieves report
        get_after = self.client.get(
            f"/api/v1/cases/{case_id}/preparation/intelligence",
            headers=self.user_headers,
        )
        self.assertEqual(get_after.status_code, 200)
        get_data = get_after.json()
        self.assertEqual(get_data["report_id"], data["report_id"])
        self.assertFalse(get_data["is_stale"])

        # 6. Multi-tenant isolation: other user cannot see report
        other_res = self.client.get(
            f"/api/v1/cases/{case_id}/preparation/intelligence",
            headers=self.other_user_headers,
        )
        self.assertEqual(other_res.status_code, 404)


if __name__ == "__main__":
    unittest.main()
