"""Tests for Context Fact list privacy (FactSummaryResponse), Profile Readiness endpoint, and Claim filtering."""

from __future__ import annotations

import unittest
from fastapi.testclient import TestClient

from caseworker.api.app import create_app
from caseworker.api.config import APIConfig
from caseworker.persistence.sqlite import SQLiteCaseworkerStorage


class TestContextPrivacyAndReadinessAPI(unittest.TestCase):
    def setUp(self) -> None:
        self.storage = SQLiteCaseworkerStorage(db_path=":memory:")
        self.config = APIConfig(dev_auth=True)
        self.app = create_app(config=self.config, storage=self.storage)
        self.client_ctx = TestClient(self.app)
        self.client = self.client_ctx.__enter__()
        self.headers = {"X-JackVerse-User": "alice"}

    def tearDown(self) -> None:
        self.client_ctx.__exit__(None, None, None)
        self.storage.close()

    def test_fact_list_omits_sensitive_value_and_source_reference(self) -> None:
        """Verify that GET /context/facts omits raw value and source_reference for SENSITIVE facts."""
        # 1. Register a source
        src_resp = self.client.post(
            "/api/v1/context/sources",
            json={"title": "Official Tax Doc", "source_type": "document"},
            headers=self.headers,
        )
        self.assertEqual(src_resp.status_code, 201)
        source_id = src_resp.json()["source_id"]

        # 2. Record a SENSITIVE fact
        sens_resp = self.client.post(
            "/api/v1/context/facts",
            json={
                "namespace": "identity",
                "key": "tax_id",
                "value": "DE123456789SECRET",
                "source_id": source_id,
                "sensitivity": "sensitive",
                "source_reference": "doc://private/tax_file.pdf",
            },
            headers=self.headers,
        )
        self.assertEqual(sens_resp.status_code, 201)
        sens_fact_id = sens_resp.json()["fact_id"]

        # 3. Record a PUBLIC fact
        pub_resp = self.client.post(
            "/api/v1/context/facts",
            json={
                "namespace": "profile",
                "key": "display_name",
                "value": "Alice Doe",
                "sensitivity": "public",
            },
            headers=self.headers,
        )
        self.assertEqual(pub_resp.status_code, 201)
        pub_fact_id = pub_resp.json()["fact_id"]

        # 4. List facts
        list_resp = self.client.get("/api/v1/context/facts", headers=self.headers)
        self.assertEqual(list_resp.status_code, 200)
        items = list_resp.json()["items"]
        self.assertEqual(len(items), 2)

        items_by_id = {item["fact_id"]: item for item in items}

        # Verify SENSITIVE item in list
        sens_summary = items_by_id[sens_fact_id]
        self.assertNotIn("value", sens_summary)
        self.assertNotIn("source_reference", sens_summary)
        self.assertTrue(sens_summary["has_value"])
        self.assertIsNone(sens_summary["preview"])
        self.assertEqual(sens_summary["sensitivity"], "sensitive")

        # Verify PUBLIC item in list
        pub_summary = items_by_id[pub_fact_id]
        self.assertNotIn("value", pub_summary)
        self.assertNotIn("source_reference", pub_summary)
        self.assertTrue(pub_summary["has_value"])
        self.assertEqual(pub_summary["preview"], "Alice Doe")

        # 5. Verify detail endpoint explicitly exposes value to owner
        detail_resp = self.client.get(f"/api/v1/context/facts/{sens_fact_id}", headers=self.headers)
        self.assertEqual(detail_resp.status_code, 200)
        detail_data = detail_resp.json()
        self.assertEqual(detail_data["value"], "DE123456789SECRET")
        self.assertEqual(detail_data["source_reference"], "doc://private/tax_file.pdf")

    def test_profile_readiness_endpoint(self) -> None:
        """Verify GET /api/v1/context/readiness/{purpose} evaluates completeness."""
        # Check initial readiness for job_application (no facts recorded yet)
        resp = self.client.get("/api/v1/context/readiness/job_application", headers=self.headers)
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["purpose"], "job_application")
        self.assertFalse(data["is_ready"])
        self.assertGreater(data["missing_count"], 0)
        self.assertEqual(data["satisfied_count"], 0)

        # Record required facts: legal_name, email, roles, technical skills
        self.client.post(
            "/api/v1/context/facts",
            json={"namespace": "identity", "key": "legal_name", "value": "Alice Doe"},
            headers=self.headers,
        )
        self.client.post(
            "/api/v1/context/facts",
            json={"namespace": "contact", "key": "email", "value": "alice@example.com"},
            headers=self.headers,
        )
        self.client.post(
            "/api/v1/context/facts",
            json={"namespace": "career", "key": "roles", "value": ["Senior Engineer"]},
            headers=self.headers,
        )
        self.client.post(
            "/api/v1/context/facts",
            json={"namespace": "skills", "key": "technical", "value": ["Python", "FastAPI"]},
            headers=self.headers,
        )

        # Check readiness again
        ready_resp = self.client.get("/api/v1/context/readiness/job_application", headers=self.headers)
        self.assertEqual(ready_resp.status_code, 200)
        ready_data = ready_resp.json()
        self.assertTrue(ready_data["is_ready"])
        self.assertGreaterEqual(ready_data["satisfied_count"], 4)
        self.assertGreater(ready_data["completeness_ratio"], 0.5)

        # Check unsupported purpose returns 400 Problem Detail
        bad_resp = self.client.get("/api/v1/context/readiness/astrology_matching", headers=self.headers)
        self.assertEqual(bad_resp.status_code, 422)
        self.assertEqual(bad_resp.headers["content-type"], "application/problem+json")

    def test_claims_filtering_by_case_and_mission(self) -> None:
        """Verify GET /api/v1/claims filters by case_id and mission_id."""
        # Create 2 missions
        m1 = self.client.post("/api/v1/missions", json={"title": "M1", "goal": "G1", "kind": "opportunity_pursuit"}, headers=self.headers).json()["mission_id"]
        m2 = self.client.post("/api/v1/missions", json={"title": "M2", "goal": "G2", "kind": "opportunity_pursuit"}, headers=self.headers).json()["mission_id"]

        # Create 2 cases
        c1 = self.client.post(f"/api/v1/missions/{m1}/cases", json={ "title": "C1", "goal": "G1", "case_type": "job_application"}, headers=self.headers).json()["case_id"]
        c2 = self.client.post(f"/api/v1/missions/{m2}/cases", json={ "title": "C2", "goal": "G2", "case_type": "job_application"}, headers=self.headers).json()["case_id"]

        # Record a verified fact
        fact_id = self.client.post("/api/v1/context/facts", json={"namespace": "skills", "key": "python", "value": True}, headers=self.headers).json()["fact_id"]
        self.client.post(f"/api/v1/context/facts/{fact_id}/verify", json={"status": "user_verified"}, headers={**self.headers, "If-Match": f"\"fact:{fact_id}:v1\""})

        # Propose claims for c1 (m1) and c2 (m2)
        cl1 = self.client.post(
            "/api/v1/claims",
            json={"purpose": "job_application", "text": "Python expert", "supporting_fact_ids": [fact_id], "case_id": c1, "mission_id": m1},
            headers=self.headers,
        ).json()["claim_id"]

        cl2 = self.client.post(
            "/api/v1/claims",
            json={"purpose": "housing_search", "text": "Budget under 1000", "supporting_fact_ids": [], "case_id": c2, "mission_id": m2},
            headers=self.headers,
        ).json()["claim_id"]

        # 1. Filter by case_id=c1
        c1_claims = self.client.get(f"/api/v1/claims?case_id={c1}", headers=self.headers).json()["items"]
        self.assertEqual(len(c1_claims), 1)
        self.assertEqual(c1_claims[0]["claim_id"], cl1)

        # 2. Filter by case_id=c2
        c2_claims = self.client.get(f"/api/v1/claims?case_id={c2}", headers=self.headers).json()["items"]
        self.assertEqual(len(c2_claims), 1)
        self.assertEqual(c2_claims[0]["claim_id"], cl2)

        # 3. Filter by mission_id=m1
        m1_claims = self.client.get(f"/api/v1/claims?mission_id={m1}", headers=self.headers).json()["items"]
        self.assertEqual(len(m1_claims), 1)
        self.assertEqual(m1_claims[0]["claim_id"], cl1)

        # 4. Filter by non-existent case
        empty_claims = self.client.get("/api/v1/claims?case_id=nonexistent", headers=self.headers).json()["items"]
        self.assertEqual(len(empty_claims), 0)


if __name__ == "__main__":
    unittest.main()
