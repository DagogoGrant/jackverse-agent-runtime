"""Tests for Preparation Package Verification & Review API endpoints and multi-tenant isolation."""

from __future__ import annotations

import unittest
from fastapi.testclient import TestClient

from caseworker.api.app import create_app
from caseworker.api.config import APIConfig
from caseworker.domain.case import Case
from caseworker.domain.context import ContextFact
from caseworker.domain.enums import (
    CaseStatus,
    CaseType,
    OpportunityStatus,
    OpportunityType,
    VerificationStatus,
)
from caseworker.domain.opportunity import Opportunity
from caseworker.domain.preparation_analyzer import PreparationAnalyzer
from caseworker.persistence.sqlite import SQLiteCaseworkerStorage


class TestPackageAPI(unittest.TestCase):
    """Test package verification, RFC 9457 problem details, retrieval, and multi-tenant isolation."""

    def setUp(self) -> None:
        self.storage = SQLiteCaseworkerStorage(db_path=":memory:")
        self.config = APIConfig(dev_auth=True)
        self.app = create_app(config=self.config, storage=self.storage)
        self.client = TestClient(self.app)
        self.user_a_headers = {"X-JackVerse-User": "user_pkg_a"}
        self.user_b_headers = {"X-JackVerse-User": "user_pkg_b"}

        # Seed data for User A: Case, Opportunity, Facts, and Preparation Report
        with self.storage.unit_of_work() as uow:
            self.case_a = Case(
                case_id="case-pkg-a-1",
                user_id="user_pkg_a",
                title="Principal Infrastructure Pursuit",
                goal="Secure role",
                case_type=CaseType.JOB_APPLICATION,
                status=CaseStatus.PLANNING,
            )
            uow.cases.save(self.case_a)

            self.opp_a = Opportunity(
                opportunity_id="opp-pkg-a-1",
                user_id="user_pkg_a",
                title="Principal Infrastructure Engineer",
                organization="HyperScale Systems",
                opportunity_type=OpportunityType.JOB,
                location="Berlin, Germany",
                requirements=[
                    "Expert Go & Rust services (Mandatory)",
                    "Kubernetes operator architecture (Mandatory)",
                    "Mentorship & hiring (Preferred)",
                ],
                converted_case_id=self.case_a.case_id,
                status=OpportunityStatus.SHORTLISTED,
            )
            uow.opportunities.save(self.opp_a)

            self.fact_go = ContextFact(
                fact_id="fact-go",
                user_id="user_pkg_a",
                namespace="skills",
                key="go_rust",
                value="Expert Go & Rust microservices architecture",
                verification_status=VerificationStatus.USER_VERIFIED,
                allowed_purposes=["case_preparation", "job_application"],
            )
            uow.context.save(self.fact_go)

            self.fact_k8s = ContextFact(
                fact_id="fact-k8s",
                user_id="user_pkg_a",
                namespace="skills",
                key="k8s",
                value="Kubernetes operators and custom controllers",
                verification_status=VerificationStatus.USER_VERIFIED,
                allowed_purposes=["case_preparation", "job_application"],
            )
            uow.context.save(self.fact_k8s)

            analyzer = PreparationAnalyzer()
            self.report_a = analyzer.analyze(
                case=self.case_a,
                opportunity=self.opp_a,
                granted_facts=[self.fact_go, self.fact_k8s],
                denied_descriptors=[],
            )
            uow.preparation_reports.save(self.report_a)

        # Draft candidate artifacts using API endpoint
        valid_etag = f'"case:{self.case_a.case_id}:v{self.case_a.version}"'
        res_draft = self.client.post(
            f"/api/v1/cases/{self.case_a.case_id}/preparation/artifacts",
            headers={**self.user_a_headers, "If-Match": valid_etag},
        )
        self.assertEqual(res_draft.status_code, 201)
        draft_data = res_draft.json()
        self.set_id = draft_data["artifacts"][0]["artifact_set_id"]

        # Refresh case_a version after drafting
        with self.storage.unit_of_work() as uow:
            refreshed_case = uow.cases.get_by_id(self.case_a.case_id)
            assert refreshed_case is not None
            self.case_a = refreshed_case

    def tearDown(self) -> None:
        self.storage.close()

    def test_post_verify_missing_and_mismatched_if_match(self) -> None:
        """Amendment 2: POST /preparation/packages/verify enforces If-Match precondition."""
        case_id = self.case_a.case_id

        # Missing If-Match returns 428 Precondition Required
        res_missing = self.client.post(
            f"/api/v1/cases/{case_id}/preparation/packages/verify",
            headers=self.user_a_headers,
            json={"artifact_set_id": self.set_id},
        )
        self.assertEqual(res_missing.status_code, 428)

        # Mismatched If-Match returns 412 Precondition Failed
        res_mismatch = self.client.post(
            f"/api/v1/cases/{case_id}/preparation/packages/verify",
            headers={**self.user_a_headers, "If-Match": f'"case:{case_id}:v999"'},
            json={"artifact_set_id": self.set_id},
        )
        self.assertEqual(res_mismatch.status_code, 412)

    def test_post_verify_happy_path_and_retrieval(self) -> None:
        """Amendment 1, 2, 3, 11: Happy path verify returns package, action, approval, and ETag."""
        case_id = self.case_a.case_id
        valid_etag = f'"case:{case_id}:v{self.case_a.version}"'

        res = self.client.post(
            f"/api/v1/cases/{case_id}/preparation/packages/verify",
            headers={**self.user_a_headers, "If-Match": valid_etag},
            json={"artifact_set_id": self.set_id},
        )
        self.assertEqual(res.status_code, 200, res.text)
        self.assertIn("etag", res.headers)
        data = res.json()

        package = data["package"]
        action = data["action"]
        approval = data["approval"]
        findings = data["findings"]

        self.assertEqual(package["status"], "frozen")
        self.assertEqual(package["verification_status"], "verified")
        self.assertEqual(action["action_type"], "approve_preparation_package")
        self.assertEqual(action["status"], "awaiting_approval")
        self.assertEqual(approval["status"], "pending")
        self.assertEqual(len(findings), 7)

        self.assertEqual(data["case_id"], case_id)
        self.assertEqual(data["case_status"], "awaiting_approval")
        self.assertGreater(data["case_version"], self.case_a.version)

        package_id = package["package_id"]

        # GET latest package
        res_latest = self.client.get(
            f"/api/v1/cases/{case_id}/preparation/packages/latest",
            headers=self.user_a_headers,
        )
        self.assertEqual(res_latest.status_code, 200)
        self.assertEqual(res_latest.json()["package_id"], package_id)

        # GET package by ID
        res_by_id = self.client.get(
            f"/api/v1/cases/{case_id}/preparation/packages/{package_id}",
            headers=self.user_a_headers,
        )
        self.assertEqual(res_by_id.status_code, 200)
        self.assertEqual(res_by_id.json()["package_id"], package_id)

        # Multi-tenant isolation: User B cannot view User A's package
        res_user_b = self.client.get(
            f"/api/v1/cases/{case_id}/preparation/packages/{package_id}",
            headers=self.user_b_headers,
        )
        self.assertEqual(res_user_b.status_code, 404)

    def test_post_verify_true_retry_idempotency_with_original_if_match(self) -> None:
        """Amendment: True retry idempotency allows client to replay original request with stale If-Match."""
        case_id = self.case_a.case_id
        original_etag = f'"case:{case_id}:v{self.case_a.version}"'

        # Initial request
        res1 = self.client.post(
            f"/api/v1/cases/{case_id}/preparation/packages/verify",
            headers={**self.user_a_headers, "If-Match": original_etag},
            json={"artifact_set_id": self.set_id},
        )
        self.assertEqual(res1.status_code, 200)
        data1 = res1.json()

        # Retry request immediately with the EXACT SAME original If-Match without fetching new Case ETag
        res2 = self.client.post(
            f"/api/v1/cases/{case_id}/preparation/packages/verify",
            headers={**self.user_a_headers, "If-Match": original_etag},
            json={"artifact_set_id": self.set_id},
        )
        self.assertEqual(res2.status_code, 200)
        data2 = res2.json()

        # Must return identical package, action, approval, and case concurrency details
        self.assertEqual(data1["package"]["package_id"], data2["package"]["package_id"])
        self.assertEqual(data1["package"]["package_fingerprint"], data2["package"]["package_fingerprint"])
        self.assertEqual(data1["action"]["action_id"], data2["action"]["action_id"])
        self.assertEqual(data1["approval"]["approval_id"], data2["approval"]["approval_id"])
        self.assertEqual(data1["case_id"], data2["case_id"])
        self.assertEqual(data1["case_version"], data2["case_version"])
        self.assertEqual(data1["case_status"], data2["case_status"])
        self.assertEqual(res1.headers.get("etag"), res2.headers.get("etag"))

    def test_post_verify_failure_returns_409_problem_details(self) -> None:
        """Amendment 5: Check failure returns RFC 9457 Problem Details (409 Conflict) with findings."""
        case_id = self.case_a.case_id

        # Invalidate artifacts by deleting CV
        with self.storage.unit_of_work() as uow:
            artifacts = uow.preparation_artifacts.list_by_set(self.set_id)
            cv_art = next((a for a in artifacts if a.material_type.value == "cv"), None)
            assert cv_art is not None
            # Mutate to missing
            cur = uow.conn.cursor()
            cur.execute("DELETE FROM preparation_artifacts WHERE artifact_id = ?", (cv_art.artifact_id,))

        valid_etag = f'"case:{case_id}:v{self.case_a.version}"'
        res = self.client.post(
            f"/api/v1/cases/{case_id}/preparation/packages/verify",
            headers={**self.user_a_headers, "If-Match": valid_etag},
            json={"artifact_set_id": self.set_id},
        )
        self.assertEqual(res.status_code, 409)
        problem = res.json()
        self.assertEqual(problem["status"], 409)
        self.assertIn("findings", problem)
        self.assertTrue(any(f["check_name"] == "material_set_completeness" and not f["passed"] for f in problem["findings"]))


if __name__ == "__main__":
    unittest.main()
