"""Tests for External Execution Plan API endpoints, precondition checks, replay idempotency, and multi-tenancy."""

from __future__ import annotations

import unittest
from fastapi.testclient import TestClient

from caseworker.api.app import create_app
from caseworker.api.config import APIConfig
from caseworker.domain.artifact import (
    ArtifactBlock,
    PreparedArtifact,
    compute_artifact_grounding_hash,
)
from caseworker.domain.artifact_planner import PLANNER_VERSION
from caseworker.domain.case import Case
from caseworker.domain.claim import Claim, compute_support_snapshot_hash
from caseworker.domain.context import ContextFact
from caseworker.domain.enums import (
    ArtifactGroundingType,
    ArtifactStatus,
    CaseStatus,
    CaseType,
    ClaimStatus,
    DestinationProvenance,
    ExecutionDestinationType,
    OpportunityStatus,
    OpportunityType,
    PreparationMaterialType,
    VerificationStatus,
)
from caseworker.domain.opportunity import Opportunity
from caseworker.domain.preparation_analyzer import PreparationAnalyzer
from caseworker.persistence.sqlite import SQLiteCaseworkerStorage
from caseworker.services.approval_service import ApprovalService
from caseworker.services.package_service import PackageService


class TestExecutionPlanAPI(unittest.TestCase):
    """Test execution plan compilation, RFC 9457 errors, ETag preconditions, replay idempotency, and multi-tenancy."""

    def setUp(self) -> None:
        self.storage = SQLiteCaseworkerStorage(db_path=":memory:")
        self.config = APIConfig(dev_auth=True)
        self.app = create_app(config=self.config, storage=self.storage)
        self.client = TestClient(self.app)
        self.package_service = PackageService(self.storage)
        self.approval_service = ApprovalService(self.storage)

        self.user_a_headers = {"X-JackVerse-User": "user_plan_a"}
        self.user_b_headers = {"X-JackVerse-User": "user_plan_b"}

        # Seed data for User A: Case, Opportunity, Facts, Claim, Artifacts, and Approved Package
        with self.storage.unit_of_work() as uow:
            self.case_a = Case(
                case_id="case-plan-a-1",
                user_id="user_plan_a",
                title="Principal Infrastructure Pursuit",
                goal="Secure role",
                case_type=CaseType.JOB_APPLICATION,
                status=CaseStatus.PLANNING,
            )
            uow.cases.save(self.case_a)

            self.opp_a = Opportunity(
                opportunity_id="opp-plan-a-1",
                user_id="user_plan_a",
                title="Principal Infrastructure Engineer",
                organization="HyperScale Systems",
                opportunity_type=OpportunityType.JOB,
                location="Berlin, Germany",
                requirements=["Expert Python architecture (Mandatory)"],
                metadata={"application_url": "https://hyperscale.apply.workable.com/roles/123"},
                converted_case_id=self.case_a.case_id,
                status=OpportunityStatus.SHORTLISTED,
            )
            uow.opportunities.save(self.opp_a)

            self.fact_name = ContextFact(
                fact_id="fact-name-a",
                user_id="user_plan_a",
                namespace="identity",
                key="name",
                value="Grant Dagogo-Jack",
                verification_status=VerificationStatus.USER_VERIFIED,
                allowed_purposes=["case_preparation", "job_application"],
            )
            uow.context.save(self.fact_name)

            self.fact_email = ContextFact(
                fact_id="fact-email-a",
                user_id="user_plan_a",
                namespace="identity",
                key="email",
                value="grant@example.com",
                verification_status=VerificationStatus.USER_VERIFIED,
                allowed_purposes=["case_preparation", "job_application"],
            )
            uow.context.save(self.fact_email)

            self.fact_py = ContextFact(
                fact_id="fact-py-a",
                user_id="user_plan_a",
                namespace="skills",
                key="python",
                value="10+ years expert Python architecture",
                verification_status=VerificationStatus.USER_VERIFIED,
                allowed_purposes=["case_preparation", "job_application"],
            )
            uow.context.save(self.fact_py)

            analyzer = PreparationAnalyzer()
            self.report_a = analyzer.analyze(
                case=self.case_a,
                opportunity=self.opp_a,
                granted_facts=[self.fact_name, self.fact_email, self.fact_py],
                denied_descriptors=[],
            )
            uow.preparation_reports.save(self.report_a)

            supp_hash = compute_support_snapshot_hash([self.fact_py])
            self.claim = Claim(
                claim_id="claim-plan-a-1",
                user_id="user_plan_a",
                purpose="job_application",
                text="Python Architecture",
                status=ClaimStatus.SUPPORTED,
                supporting_fact_ids=[self.fact_py.fact_id],
                case_id=self.case_a.case_id,
                source_report_id=self.report_a.report_id,
                source_claim_intent_id="intent-1",
                assertion_schema="skill_possession",
                assertion_json='{"kind": "skill_possession", "fact_ids": ["fact-py-a"], "fields": {"skill": "Python"}}',
                support_snapshot_hash=supp_hash,
                version=1,
            )
            uow.claims.save(self.claim)

            cv_grounding = compute_artifact_grounding_hash(claims=[self.claim], direct_facts=[self.fact_name])
            cl_grounding = compute_artifact_grounding_hash(claims=[], direct_facts=[self.fact_name])
            qa_grounding = compute_artifact_grounding_hash(claims=[self.claim], direct_facts=[])

            self.set_id = "set-plan-a-1"
            self.art_resume = PreparedArtifact(
                artifact_id="art-plan-resume",
                artifact_set_id=self.set_id,
                case_id=self.case_a.case_id,
                opportunity_id=self.opp_a.opportunity_id,
                user_id="user_plan_a",
                material_type=PreparationMaterialType.CV,
                title="Resume",
                planner_version=PLANNER_VERSION,
                status=ArtifactStatus.DRAFT,
                source_report_id=self.report_a.report_id,
                grounding_snapshot_hash=cv_grounding,
                blocks=[
                    ArtifactBlock(block_id="b-h", block_type="header", content="Grant", grounding_type=ArtifactGroundingType.CONTEXT_FACT, fact_ids=[self.fact_name.fact_id]),
                    ArtifactBlock(block_id="b-s", content="Python Lead", grounding_type=ArtifactGroundingType.CLAIM, claim_ids=[self.claim.claim_id]),
                ],
            )
            uow.preparation_artifacts.save(self.art_resume)

            self.art_cl = PreparedArtifact(
                artifact_id="art-plan-cl",
                artifact_set_id=self.set_id,
                case_id=self.case_a.case_id,
                opportunity_id=self.opp_a.opportunity_id,
                user_id="user_plan_a",
                material_type=PreparationMaterialType.COVER_LETTER,
                title="Cover Letter",
                planner_version=PLANNER_VERSION,
                status=ArtifactStatus.DRAFT,
                source_report_id=self.report_a.report_id,
                grounding_snapshot_hash=cl_grounding,
                blocks=[ArtifactBlock(block_id="b-cl", content="Cover letter", grounding_type=ArtifactGroundingType.CONTEXT_FACT, fact_ids=[self.fact_name.fact_id])],
            )
            uow.preparation_artifacts.save(self.art_cl)

            self.art_qa = PreparedArtifact(
                artifact_id="art-plan-qa",
                artifact_set_id=self.set_id,
                case_id=self.case_a.case_id,
                opportunity_id=self.opp_a.opportunity_id,
                user_id="user_plan_a",
                material_type=PreparationMaterialType.SCREENING_ANSWERS,
                title="Screening Q&A",
                planner_version=PLANNER_VERSION,
                status=ArtifactStatus.DRAFT,
                source_report_id=self.report_a.report_id,
                grounding_snapshot_hash=qa_grounding,
                blocks=[
                    ArtifactBlock(
                        block_id="b-qa",
                        block_type="qna_item",
                        content="### Q: How many years of Python?\n\n10+ years.",
                        grounding_type=ArtifactGroundingType.CLAIM,
                        claim_ids=[self.claim.claim_id],
                        metadata={
                            "question": "How many years of Python?",
                            "answer": "10+ years.",
                            "answer_status": "ANSWERED",
                        },
                    )
                ],
            )
            uow.preparation_artifacts.save(self.art_qa)

        # Prepare and approve package for User A
        pkg, _, approval, _, _ = self.package_service.verify_and_prepare_package(
            user_id="user_plan_a",
            case_id=self.case_a.case_id,
            artifact_set_id=self.set_id,
            expected_case_version=1,
        )
        self.package_id = pkg.package_id

        self.approval_service.approve_action(
            user_id="user_plan_a",
            approval_id=approval.approval_id,
            reason="Approved for external planning",
        )

        # Refresh case_a
        with self.storage.unit_of_work() as uow:
            refreshed = uow.cases.get_by_id(self.case_a.case_id)
            assert refreshed is not None
            self.case_a = refreshed

        # Sample valid target schema request
        self.sample_request = {
            "package_id": self.package_id,
            "target_schema": {
                "schema_id": "schema-workable-v1",
                "destination_type": "ats_form",
                "fields": [
                    {"target_field_id": "name", "label": "Full Name", "field_type": "text", "required": True},
                    {"target_field_id": "email", "label": "Email Address", "field_type": "text", "required": True},
                ],
                "upload_slots": [
                    {"slot_id": "slot-cv", "role": "RESUME", "label": "Resume / CV", "required": True, "accepted_mime_types": ["application/pdf", "text/plain"]},
                ],
                "screening_questions": [
                    {"question_id": "q-py", "question_text": "How many years of Python?", "required": True, "answer_type": "text"},
                ],
                "final_submit_control": {
                    "control_type": "button_submit",
                    "semantic_target": "Submit Application",
                    "selector": "#btn-submit",
                    "method": "POST",
                    "requires_double_confirmation": True,
                },
            },
        }

    def tearDown(self) -> None:
        self.storage.close()

    def test_create_execution_plan_precondition_required_and_failed(self) -> None:
        """Amendment 15: If-Match header is required on new execution plan creation."""
        case_id = self.case_a.case_id

        # Missing If-Match -> 428 Precondition Required
        res_missing = self.client.post(
            f"/api/v1/cases/{case_id}/execution/plan",
            headers=self.user_a_headers,
            json=self.sample_request,
        )
        self.assertEqual(res_missing.status_code, 428)

        # Mismatched If-Match -> 412 Precondition Failed
        res_mismatch = self.client.post(
            f"/api/v1/cases/{case_id}/execution/plan",
            headers={**self.user_a_headers, "If-Match": f'"case:{case_id}:v999"'},
            json=self.sample_request,
        )
        self.assertEqual(res_mismatch.status_code, 412)

    def test_create_execution_plan_happy_path(self) -> None:
        """Amendment 1, 10, 11, 12, 13: Happy path returns 200, valid plan schema, ETag."""
        case_id = self.case_a.case_id
        valid_etag = f'"case:{case_id}:v{self.case_a.version}"'

        res = self.client.post(
            f"/api/v1/cases/{case_id}/execution/plan",
            headers={**self.user_a_headers, "If-Match": valid_etag},
            json=self.sample_request,
        )
        self.assertEqual(res.status_code, 200, res.text)
        self.assertIn("etag", res.headers)
        data = res.json()

        self.assertEqual(data["case_id"], case_id)
        self.assertEqual(data["package_id"], self.package_id)
        self.assertEqual(data["status"], "ready_for_authorization")
        self.assertEqual(data["destination"]["destination_type"], "ats_form")
        self.assertEqual(data["destination"]["canonical_url"], "https://hyperscale.apply.workable.com/roles/123")
        self.assertEqual(data["destination"]["destination_provenance"], "opportunity_application_url")

        # Field mappings
        self.assertEqual(len(data["field_mappings"]), 2)
        field_keys = {f["target_field_id"] for f in data["field_mappings"]}
        self.assertEqual(field_keys, {"name", "email"})

        # Upload mappings
        self.assertEqual(len(data["upload_mappings"]), 1)
        self.assertEqual(data["upload_mappings"][0]["role"], "RESUME")
        self.assertTrue(data["upload_mappings"][0]["materialized"])
        self.assertEqual(data["upload_mappings"][0]["mime_type"], "text/plain")

        # Screening responses
        self.assertEqual(len(data["screening_responses"]), 1)
        self.assertEqual(data["screening_responses"][0]["question_id"], "q-py")
        self.assertEqual(data["screening_responses"][0]["answer"], "10+ years.")

        # Findings
        self.assertTrue(all(f["passed"] for f in data["findings"]))
        self.assertTrue(len(data["execution_plan_fingerprint"]) > 10)

    def test_create_execution_plan_exact_replay_resolves_before_if_match(self) -> None:
        """Amendment 15: Exact replay resolves before If-Match check."""
        case_id = self.case_a.case_id
        valid_etag = f'"case:{case_id}:v{self.case_a.version}"'

        res1 = self.client.post(
            f"/api/v1/cases/{case_id}/execution/plan",
            headers={**self.user_a_headers, "If-Match": valid_etag},
            json=self.sample_request,
        )
        self.assertEqual(res1.status_code, 200)
        plan1 = res1.json()

        # Replay with missing If-Match header -> must succeed idempotently with 200!
        res2 = self.client.post(
            f"/api/v1/cases/{case_id}/execution/plan",
            headers=self.user_a_headers,
            json=self.sample_request,
        )
        self.assertEqual(res2.status_code, 200)
        plan2 = res2.json()

        self.assertEqual(plan1["plan_id"], plan2["plan_id"])
        self.assertEqual(plan1["execution_plan_fingerprint"], plan2["execution_plan_fingerprint"])
        self.assertEqual(res1.headers.get("etag"), res2.headers.get("etag"))

        # Replay with stale / arbitrary If-Match header -> must also succeed idempotently
        res3 = self.client.post(
            f"/api/v1/cases/{case_id}/execution/plan",
            headers={**self.user_a_headers, "If-Match": f'"case:{case_id}:v999"'},
            json=self.sample_request,
        )
        self.assertEqual(res3.status_code, 200)
        self.assertEqual(res3.json()["plan_id"], plan1["plan_id"])

    def test_get_execution_plan_retrieval_and_listing(self) -> None:
        """Amendment 12, 14: GET latest, GET by ID, and GET list endpoints."""
        case_id = self.case_a.case_id

        # Before creation: latest returns 404
        res_none = self.client.get(
            f"/api/v1/cases/{case_id}/execution/plan/latest",
            headers=self.user_a_headers,
        )
        self.assertEqual(res_none.status_code, 404)

        # Create Plan 1
        valid_etag = f'"case:{case_id}:v{self.case_a.version}"'
        res1 = self.client.post(
            f"/api/v1/cases/{case_id}/execution/plan",
            headers={**self.user_a_headers, "If-Match": valid_etag},
            json=self.sample_request,
        )
        self.assertEqual(res1.status_code, 200)
        plan1_id = res1.json()["plan_id"]

        # GET latest
        res_latest = self.client.get(
            f"/api/v1/cases/{case_id}/execution/plan/latest",
            headers=self.user_a_headers,
        )
        self.assertEqual(res_latest.status_code, 200)
        self.assertEqual(res_latest.json()["plan_id"], plan1_id)
        self.assertIn("etag", res_latest.headers)

        # GET by ID
        res_by_id = self.client.get(
            f"/api/v1/cases/{case_id}/execution/plan/{plan1_id}",
            headers=self.user_a_headers,
        )
        self.assertEqual(res_by_id.status_code, 200)
        self.assertEqual(res_by_id.json()["plan_id"], plan1_id)

        # Non-existent ID returns 404
        res_missing = self.client.get(
            f"/api/v1/cases/{case_id}/execution/plan/plan-nonexistent",
            headers=self.user_a_headers,
        )
        self.assertEqual(res_missing.status_code, 404)

        # Create Plan 2 with destination override to supersede Plan 1
        override_req = dict(self.sample_request)
        override_req["destination_override"] = {
            "destination_type": "ats_form",
            "destination_provenance": "user_provided",
            "canonical_url": "https://hyperscale.custom-ats.com/apply/123",
            "organization": "HyperScale Custom",
            "channel_metadata": {"browser_engine": "chromium"},
            "is_exact_destination": True,
        }
        res2 = self.client.post(
            f"/api/v1/cases/{case_id}/execution/plan",
            headers={**self.user_a_headers, "If-Match": valid_etag},
            json=override_req,
        )
        self.assertEqual(res2.status_code, 200)
        plan2_id = res2.json()["plan_id"]
        self.assertNotEqual(plan1_id, plan2_id)

        # GET latest now points to Plan 2
        res_latest2 = self.client.get(
            f"/api/v1/cases/{case_id}/execution/plan/latest",
            headers=self.user_a_headers,
        )
        self.assertEqual(res_latest2.json()["plan_id"], plan2_id)
        self.assertEqual(res_latest2.json()["status"], "ready_for_authorization")

        # GET by ID for Plan 1 shows it was superseded
        res_plan1_superseded = self.client.get(
            f"/api/v1/cases/{case_id}/execution/plan/{plan1_id}",
            headers=self.user_a_headers,
        )
        self.assertEqual(res_plan1_superseded.json()["status"], "superseded")

        # GET list returns both plans
        res_list = self.client.get(
            f"/api/v1/cases/{case_id}/execution/plans",
            headers=self.user_a_headers,
        )
        self.assertEqual(res_list.status_code, 200)
        list_data = res_list.json()
        self.assertEqual(list_data["total"], 2)
        self.assertEqual([p["plan_id"] for p in list_data["plans"]], [plan2_id, plan1_id])

    def test_multi_tenant_isolation(self) -> None:
        """Amendment 14: User B cannot access or create plans for User A's case."""
        case_id = self.case_a.case_id
        valid_etag = f'"case:{case_id}:v{self.case_a.version}"'

        # User A creates a plan
        res_a = self.client.post(
            f"/api/v1/cases/{case_id}/execution/plan",
            headers={**self.user_a_headers, "If-Match": valid_etag},
            json=self.sample_request,
        )
        self.assertEqual(res_a.status_code, 200)
        plan_id = res_a.json()["plan_id"]

        # User B attempts to access latest plan -> 404
        res_b_latest = self.client.get(
            f"/api/v1/cases/{case_id}/execution/plan/latest",
            headers=self.user_b_headers,
        )
        self.assertEqual(res_b_latest.status_code, 404)

        # User B attempts to access plan by ID -> 404
        res_b_id = self.client.get(
            f"/api/v1/cases/{case_id}/execution/plan/{plan_id}",
            headers=self.user_b_headers,
        )
        self.assertEqual(res_b_id.status_code, 404)

        # User B attempts to list plans -> 404
        res_b_list = self.client.get(
            f"/api/v1/cases/{case_id}/execution/plans",
            headers=self.user_b_headers,
        )
        self.assertEqual(res_b_list.status_code, 404)

        # User B attempts to post plan for User A's case -> 404
        res_b_post = self.client.post(
            f"/api/v1/cases/{case_id}/execution/plan",
            headers={**self.user_b_headers, "If-Match": valid_etag},
            json=self.sample_request,
        )
        self.assertEqual(res_b_post.status_code, 404)

    def test_clarifications_api_response_and_staleness(self) -> None:
        """Verify API returns all Phase 5.3.1 clarification fields and side-effect free staleness."""
        case_id = self.case_a.case_id
        valid_etag = f'"case:{case_id}:v{self.case_a.version}"'

        res = self.client.post(
            f"/api/v1/cases/{case_id}/execution/plan",
            headers={**self.user_a_headers, "If-Match": valid_etag},
            json=self.sample_request,
        )
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertIn("destination_provenance", data)
        self.assertIn("target_schema_hash", data)
        self.assertIn("planning_findings", data)
        self.assertIn("is_stale", data)
        self.assertIn("staleness_reasons", data)
        self.assertFalse(data["is_stale"])
        self.assertEqual(data["staleness_reasons"], [])

        dest = data["destination"]
        self.assertIn("normalized_origin", dest)
        self.assertIn("normalized_hostname", dest)
        self.assertEqual(dest["normalized_hostname"], "hyperscale.apply.workable.com")

        # Mutate fact to trigger staleness
        with self.storage.unit_of_work() as uow:
            fact = uow.context.get_by_id(self.fact_name.fact_id)
            self.assertIsNotNone(fact)
            updated = ContextFact(
                fact_id=fact.fact_id,
                user_id=fact.user_id,
                namespace=fact.namespace,
                key=fact.key,
                value="New Name",
                source_type=fact.source_type,
                source_reference=fact.source_reference,
                confidence=fact.confidence,
                verification_status=fact.verification_status,
                sensitivity=fact.sensitivity,
                allowed_purposes=fact.allowed_purposes,
                created_at=fact.created_at,
                updated_at=fact.updated_at,
                version=fact.version + 1,
            )
            uow.context.save(updated)
            uow.commit()

        # GET latest should report is_stale=True side-effect free
        res_get = self.client.get(
            f"/api/v1/cases/{case_id}/execution/plan/latest",
            headers=self.user_a_headers,
        )
        self.assertEqual(res_get.status_code, 200)
        get_data = res_get.json()
        self.assertTrue(get_data["is_stale"])
        self.assertTrue(len(get_data["staleness_reasons"]) > 0)
        # Database status is unchanged
        self.assertEqual(get_data["status"], "ready_for_authorization")

    def test_destination_security_api_rejection(self) -> None:
        """Verify API rejects non-https URLs and embedded credentials with 400 Problem Details."""
        case_id = self.case_a.case_id
        valid_etag = f'"case:{case_id}:v{self.case_a.version}"'

        bad_req = dict(self.sample_request)
        bad_req["destination_override"] = {
            "destination_type": "ats_form",
            "destination_provenance": "user_provided",
            "canonical_url": "https://admin:secret@portal.com/apply",
            "organization": "Evil",
            "channel_metadata": {},
            "is_exact_destination": True,
        }
        res = self.client.post(
            f"/api/v1/cases/{case_id}/execution/plan",
            headers={**self.user_a_headers, "If-Match": valid_etag},
            json=bad_req,
        )
        self.assertEqual(res.status_code, 422)
        problem = res.json()
        self.assertIn("embedded", problem["detail"].lower())


if __name__ == "__main__":
    unittest.main()
