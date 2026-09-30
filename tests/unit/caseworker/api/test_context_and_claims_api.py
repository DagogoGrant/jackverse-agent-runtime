"""Tests for Context Vault and Claim Ledger API endpoints."""

from __future__ import annotations

import unittest
from fastapi.testclient import TestClient

from caseworker.api.app import create_app
from caseworker.api.config import APIConfig
from caseworker.persistence.sqlite import SQLiteCaseworkerStorage


class TestContextAndClaimsAPI(unittest.TestCase):
    """Test safe source DTO responses, context fact lifecycle, packaging, and claim ledger."""

    def setUp(self) -> None:
        self.storage = SQLiteCaseworkerStorage(db_path=":memory:")
        self.config = APIConfig(dev_auth=True)
        self.app = create_app(config=self.config, storage=self.storage)
        self.client = TestClient(self.app)
        self.user_a_headers = {"X-JackVerse-User": "user_a"}
        self.user_b_headers = {"X-JackVerse-User": "user_b"}

    def tearDown(self) -> None:
        self.storage.close()

    def test_safe_source_response_omits_sensitive_credentials(self) -> None:
        """Addendum Requirement 7: Omit private URL tokens, query credentials, and metadata in source DTOs."""
        res = self.client.post(
            "/api/v1/context/sources",
            headers=self.user_a_headers,
            json={
                "title": "Private Resume Google Doc",
                "source_type": "document",
                "source_reference": "https://docs.google.com/document/d/123?token=SECRET_PRIVATE_API_TOKEN",
                "sensitivity": "personal",
                "metadata": {"access_token": "bearer-secret-token", "author": "Alice"},
            },
        )
        self.assertEqual(res.status_code, 201)
        source = res.json()
        self.assertEqual(source["title"], "Private Resume Google Doc")
        # Ensure URI credentials and metadata are omitted from DTO
        self.assertNotIn("source_reference", source)
        self.assertNotIn("metadata", source)
        self.assertNotIn("SECRET_PRIVATE_API_TOKEN", str(source))
        self.assertNotIn("bearer-secret-token", str(source))

    def test_context_fact_lifecycle_and_cross_user_isolation(self) -> None:
        """Create fact, verify with ETag, supersede with ETag, verify cross-user 404."""
        # 1. User A records a fact
        f_res = self.client.post(
            "/api/v1/context/facts",
            headers=self.user_a_headers,
            json={
                "namespace": "profile.education",
                "key": "degree",
                "value": "B.S. Computer Science",
                "confidence": 1.0,
            },
        )
        self.assertEqual(f_res.status_code, 201)
        fact_id = f_res.json()["fact_id"]
        etag_v1 = f_res.headers.get("etag")

        # 2. User B cannot see fact -> 404
        b_res = self.client.get(f"/api/v1/context/facts/{fact_id}", headers=self.user_b_headers)
        self.assertEqual(b_res.status_code, 404)

        # 3. User A verifies fact
        v_res = self.client.post(
            f"/api/v1/context/facts/{fact_id}/verify",
            headers={**self.user_a_headers, "If-Match": etag_v1},
            json={"status": "user_verified"},
        )
        self.assertEqual(v_res.status_code, 200)
        self.assertEqual(v_res.json()["verification_status"], "user_verified")
        etag_v2 = v_res.headers.get("etag")

        # 4. User A supersedes fact
        s_res = self.client.post(
            f"/api/v1/context/facts/{fact_id}/supersede",
            headers={**self.user_a_headers, "If-Match": etag_v2},
            json={"new_value": "M.S. Computer Science", "reason": "Completed Master's degree"},
        )
        self.assertEqual(s_res.status_code, 200)
        new_fact = s_res.json()
        self.assertEqual(new_fact["value"], "M.S. Computer Science")

    def test_context_packaging_with_purpose_gating(self) -> None:
        """Assemble a context package for a specific purpose."""
        # Record facts with specific purposes
        self.client.post(
            "/api/v1/context/facts",
            headers=self.user_a_headers,
            json={
                "namespace": "career.resume",
                "key": "headline",
                "value": "Senior Systems Engineer",
                "allowed_purposes": ["job_application"],
            },
        )

        pkg_res = self.client.post(
            "/api/v1/context/packages",
            headers=self.user_a_headers,
            json={"purpose": "job_application"},
        )
        self.assertEqual(pkg_res.status_code, 201)
        pkg = pkg_res.json()
        self.assertEqual(pkg["purpose"], "job_application")
        self.assertEqual(len(pkg["facts"]), 1)
        self.assertEqual(pkg["facts"][0]["key"], "headline")

    def test_claims_api_crud_and_evaluations(self) -> None:
        """Propose a claim, evaluate, and reject under ETag enforcement."""
        # Record fact first
        f_res = self.client.post(
            "/api/v1/context/facts",
            headers=self.user_a_headers,
            json={
                "namespace": "experience.metrics",
                "key": "latency_reduction",
                "value": "Reduced P99 latency by 45%",
                "allowed_purposes": ["resume_submission"],
            },
        )
        fact_id = f_res.json()["fact_id"]

        # Propose claim backed by this fact
        claim_res = self.client.post(
            "/api/v1/claims",
            headers=self.user_a_headers,
            json={
                "purpose": "resume_submission",
                "text": "Reduced system latency by 45%",
                "supporting_fact_ids": [fact_id],
                "auto_evaluate": True,
            },
        )
        self.assertEqual(claim_res.status_code, 201)
        claim_id = claim_res.json()["claim_id"]
        etag_v1 = claim_res.headers.get("etag")

        # Reject claim
        rej_res = self.client.post(
            f"/api/v1/claims/{claim_id}/reject",
            headers={**self.user_a_headers, "If-Match": etag_v1},
            json={"reason": "Metric superseded by newer data"},
        )
        self.assertEqual(rej_res.status_code, 200)
        self.assertEqual(rej_res.json()["status"], "rejected")


if __name__ == "__main__":
    unittest.main()
