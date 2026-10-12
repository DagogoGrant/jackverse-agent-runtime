"""Tests for Prepared Artifacts API endpoints, provenance, export, and multi-tenant isolation."""

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
    PreparationMaterialType,
    VerificationStatus,
)
from caseworker.domain.opportunity import Opportunity
from caseworker.domain.preparation_analyzer import PreparationAnalyzer
from caseworker.persistence.sqlite import SQLiteCaseworkerStorage


class TestArtifactsAPI(unittest.TestCase):
    """Test artifact drafting, listing, export, provenance inspection, and multi-tenant isolation."""

    def setUp(self) -> None:
        self.storage = SQLiteCaseworkerStorage(db_path=":memory:")
        self.config = APIConfig(dev_auth=True)
        self.app = create_app(config=self.config, storage=self.storage)
        self.client = TestClient(self.app)
        self.user_a_headers = {"X-JackVerse-User": "user_art_a"}
        self.user_b_headers = {"X-JackVerse-User": "user_art_b"}

        # Seed data for User A: Case, Opportunity, Facts, and Preparation Report
        with self.storage.unit_of_work() as uow:
            self.case_a = Case(
                case_id="case-art-a-1",
                user_id="user_art_a",
                title="Principal Infrastructure Engineer Application",
                goal="Land Principal Infrastructure Engineer role",
                case_type=CaseType.JOB_APPLICATION,
                status=CaseStatus.PLANNING,
            )
            uow.cases.save(self.case_a)

            self.opp_a = Opportunity(
                opportunity_id="opp-art-a-1",
                user_id="user_art_a",
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
                user_id="user_art_a",
                namespace="skills",
                key="go_rust",
                value="Expert Go & Rust microservices architecture",
                verification_status=VerificationStatus.USER_VERIFIED,
                allowed_purposes=["case_preparation", "job_application"],
            )
            uow.context.save(self.fact_go)

            self.fact_k8s = ContextFact(
                fact_id="fact-k8s",
                user_id="user_art_a",
                namespace="skills",
                key="k8s",
                value="Kubernetes operators and custom controllers",
                verification_status=VerificationStatus.USER_VERIFIED,
                allowed_purposes=["case_preparation", "job_application"],
            )
            uow.context.save(self.fact_k8s)

            # Analyze preparation intelligence
            analyzer = PreparationAnalyzer()
            self.report_a = analyzer.analyze(
                case=self.case_a,
                opportunity=self.opp_a,
                granted_facts=[self.fact_go, self.fact_k8s],
                denied_descriptors=[],
            )
            uow.preparation_reports.save(self.report_a)

    def tearDown(self) -> None:
        self.storage.close()

    def test_post_draft_artifacts_concurrency_and_creation(self) -> None:
        """POST /artifacts requires valid If-Match ETag, creates 3 artifacts, and returns ETag."""
        case_id = self.case_a.case_id

        # 1. Missing If-Match returns 428 Precondition Required
        res_missing = self.client.post(
            f"/api/v1/cases/{case_id}/preparation/artifacts",
            headers=self.user_a_headers,
        )
        self.assertEqual(res_missing.status_code, 428)

        # 2. Stale If-Match returns 412 Precondition Failed
        res_stale = self.client.post(
            f"/api/v1/cases/{case_id}/preparation/artifacts",
            headers={**self.user_a_headers, "If-Match": f'"case:{case_id}:v999"'},
        )
        self.assertEqual(res_stale.status_code, 412)

        # 3. Valid If-Match returns 201 Created and new ETag
        valid_etag = f'"case:{case_id}:v{self.case_a.version}"'
        res_created = self.client.post(
            f"/api/v1/cases/{case_id}/preparation/artifacts",
            headers={**self.user_a_headers, "If-Match": valid_etag},
        )
        self.assertEqual(res_created.status_code, 201)
        self.assertIn("etag", res_created.headers)
        data = res_created.json()
        self.assertEqual(data["total"], 3)
        self.assertEqual(len(data["artifacts"]), 3)

        materials = {a["material_type"] for a in data["artifacts"]}
        self.assertIn("cv", materials)
        self.assertIn("cover_letter", materials)
        self.assertIn("application_answer_bank", materials)

    def test_get_artifacts_listing_and_detail(self) -> None:
        """List and retrieve specific artifact details."""
        case_id = self.case_a.case_id
        valid_etag = f'"case:{case_id}:v{self.case_a.version}"'
        res_draft = self.client.post(
            f"/api/v1/cases/{case_id}/preparation/artifacts",
            headers={**self.user_a_headers, "If-Match": valid_etag},
        )
        self.assertEqual(res_draft.status_code, 201)
        drafted_list = res_draft.json()["artifacts"]
        cv_item = next(a for a in drafted_list if a["material_type"] == "cv")
        artifact_id = cv_item["artifact_id"]

        # GET /artifacts list
        res_list = self.client.get(
            f"/api/v1/cases/{case_id}/preparation/artifacts",
            headers=self.user_a_headers,
        )
        self.assertEqual(res_list.status_code, 200)
        self.assertEqual(res_list.json()["total"], 3)

        # GET /artifacts/{artifact_id}
        res_detail = self.client.get(
            f"/api/v1/cases/{case_id}/preparation/artifacts/{artifact_id}",
            headers=self.user_a_headers,
        )
        self.assertEqual(res_detail.status_code, 200)
        detail = res_detail.json()
        self.assertEqual(detail["artifact_id"], artifact_id)
        self.assertEqual(detail["material_type"], "cv")
        self.assertGreater(len(detail["blocks"]), 0)
        self.assertIn("Candidate for Principal Infrastructure Engineer", detail["rendered_markdown"])

    def test_export_artifact_clean_prose(self) -> None:
        """GET /artifacts/{artifact_id}/export returns publication text with zero synthetic citations."""
        case_id = self.case_a.case_id
        valid_etag = f'"case:{case_id}:v{self.case_a.version}"'
        res_draft = self.client.post(
            f"/api/v1/cases/{case_id}/preparation/artifacts",
            headers={**self.user_a_headers, "If-Match": valid_etag},
        )
        self.assertEqual(res_draft.status_code, 201)
        cv_item = next(a for a in res_draft.json()["artifacts"] if a["material_type"] == "cv")
        artifact_id = cv_item["artifact_id"]

        # 1. Export as Markdown (default)
        res_md = self.client.get(
            f"/api/v1/cases/{case_id}/preparation/artifacts/{artifact_id}/export?format=markdown",
            headers=self.user_a_headers,
        )
        self.assertEqual(res_md.status_code, 200)
        self.assertIn("text/plain", res_md.headers.get("content-type", ""))
        md_text = res_md.text
        self.assertIn("Candidate for Principal Infrastructure Engineer", md_text)
        self.assertNotIn("[claim:", md_text)
        self.assertNotIn("[fact:", md_text)

        # 2. Export as plain text
        res_txt = self.client.get(
            f"/api/v1/cases/{case_id}/preparation/artifacts/{artifact_id}/export?format=plain_text",
            headers=self.user_a_headers,
        )
        self.assertEqual(res_txt.status_code, 200)
        txt_text = res_txt.text
        self.assertIn("Candidate for Principal Infrastructure Engineer", txt_text)
        self.assertNotIn("[claim:", txt_text)

    def test_get_provenance_inspection(self) -> None:
        """GET /artifacts/{artifact_id}/provenance returns full block grounding map."""
        case_id = self.case_a.case_id
        valid_etag = f'"case:{case_id}:v{self.case_a.version}"'
        res_draft = self.client.post(
            f"/api/v1/cases/{case_id}/preparation/artifacts",
            headers={**self.user_a_headers, "If-Match": valid_etag},
        )
        self.assertEqual(res_draft.status_code, 201)
        cv_item = next(a for a in res_draft.json()["artifacts"] if a["material_type"] == "cv")
        artifact_id = cv_item["artifact_id"]

        res_prov = self.client.get(
            f"/api/v1/cases/{case_id}/preparation/artifacts/{artifact_id}/provenance",
            headers=self.user_a_headers,
        )
        self.assertEqual(res_prov.status_code, 200)
        prov = res_prov.json()
        self.assertEqual(prov["artifact_id"], artifact_id)
        self.assertIn("blocks", prov)
        self.assertGreater(len(prov["blocks"]), 0)

        # Confirm at least one block has claim grounding
        claim_blocks = [b for b in prov["blocks"] if b["grounding_type"] == "claim"]
        self.assertGreater(len(claim_blocks), 0)
        self.assertGreater(len(claim_blocks[0]["claim_ids"]), 0)

    def test_cross_user_isolation(self) -> None:
        """User B cannot see, draft, export, or inspect provenance for User A's artifacts."""
        case_id = self.case_a.case_id
        valid_etag = f'"case:{case_id}:v{self.case_a.version}"'

        # User B attempts to draft User A's case
        res_draft_b = self.client.post(
            f"/api/v1/cases/{case_id}/preparation/artifacts",
            headers={**self.user_b_headers, "If-Match": valid_etag},
        )
        self.assertEqual(res_draft_b.status_code, 404)

        # User A drafts
        res_draft_a = self.client.post(
            f"/api/v1/cases/{case_id}/preparation/artifacts",
            headers={**self.user_a_headers, "If-Match": valid_etag},
        )
        self.assertEqual(res_draft_a.status_code, 201)
        artifact_id = res_draft_a.json()["artifacts"][0]["artifact_id"]

        # User B attempts to list artifacts
        res_list_b = self.client.get(
            f"/api/v1/cases/{case_id}/preparation/artifacts",
            headers=self.user_b_headers,
        )
        self.assertEqual(res_list_b.status_code, 404)

        # User B attempts to get specific artifact
        res_get_b = self.client.get(
            f"/api/v1/cases/{case_id}/preparation/artifacts/{artifact_id}",
            headers=self.user_b_headers,
        )
        self.assertEqual(res_get_b.status_code, 404)

        # User B attempts to export artifact
        res_exp_b = self.client.get(
            f"/api/v1/cases/{case_id}/preparation/artifacts/{artifact_id}/export",
            headers=self.user_b_headers,
        )
        self.assertEqual(res_exp_b.status_code, 404)

        # User B attempts to inspect provenance
        res_prov_b = self.client.get(
            f"/api/v1/cases/{case_id}/preparation/artifacts/{artifact_id}/provenance",
            headers=self.user_b_headers,
        )
        self.assertEqual(res_prov_b.status_code, 404)


if __name__ == "__main__":
    unittest.main()
