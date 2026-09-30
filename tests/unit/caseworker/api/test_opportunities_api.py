"""Tests for Opportunities API endpoints and User-Scoped Deduplication."""

from __future__ import annotations

import unittest
from fastapi.testclient import TestClient

from caseworker.api.app import create_app
from caseworker.api.config import APIConfig
from caseworker.persistence.sqlite import SQLiteCaseworkerStorage


class TestOpportunitiesAPI(unittest.TestCase):
    """Test opportunity discovery, transitions, and user-scoped deduplication."""

    def setUp(self) -> None:
        self.storage = SQLiteCaseworkerStorage(db_path=":memory:")
        self.config = APIConfig(dev_auth=True)
        self.app = create_app(config=self.config, storage=self.storage)
        self.client = TestClient(self.app)
        self.user_a_headers = {"X-JackVerse-User": "user_a"}
        self.user_b_headers = {"X-JackVerse-User": "user_b"}

    def tearDown(self) -> None:
        self.storage.close()

    def test_user_scoped_deduplication(self) -> None:
        """User A discovers opportunity twice (201 then 200); User B discovers same opportunity (201 separate ID)."""
        payload = {
            "title": "Machine Learning Resident",
            "opportunity_type": "job",
            "organization": "OpenAI",
            "source_url": "https://example.com/jobs/ml-resident",
            "location": "San Francisco, CA",
        }

        # 1. User A discovers opportunity -> 201 Created
        res1 = self.client.post("/api/v1/opportunities", headers=self.user_a_headers, json=payload)
        self.assertEqual(res1.status_code, 201)
        opp_a1 = res1.json()
        id_a = opp_a1["opportunity_id"]

        # 2. User A discovers identical opportunity again -> 200 OK (deduplicated)
        res2 = self.client.post("/api/v1/opportunities", headers=self.user_a_headers, json=payload)
        self.assertEqual(res2.status_code, 200)
        opp_a2 = res2.json()
        self.assertEqual(opp_a2["opportunity_id"], id_a)

        # 3. User B discovers the identical real-world opportunity -> 201 Created (NOT deduplicated against User A!)
        res_b = self.client.post("/api/v1/opportunities", headers=self.user_b_headers, json=payload)
        self.assertEqual(res_b.status_code, 201)
        opp_b = res_b.json()
        id_b = opp_b["opportunity_id"]
        self.assertNotEqual(id_a, id_b)
        self.assertEqual(opp_b["user_id"], "user_b")

    def test_opportunity_transitions_with_etag(self) -> None:
        """Opportunity status transition enforces ETag preconditions."""
        payload = {
            "title": "Full Stack Engineer",
            "opportunity_type": "job",
            "organization": "Vercel",
        }
        create_res = self.client.post("/api/v1/opportunities", headers=self.user_a_headers, json=payload)
        self.assertEqual(create_res.status_code, 201)
        opp_id = create_res.json()["opportunity_id"]
        etag_v1 = create_res.headers.get("etag")

        # Transition: DISCOVERED -> EVALUATING
        trans_res = self.client.post(
            f"/api/v1/opportunities/{opp_id}/transition",
            headers={**self.user_a_headers, "If-Match": etag_v1},
            json={"new_status": "evaluating", "reason": "Passed initial keyword screening"},
        )
        self.assertEqual(trans_res.status_code, 200)
        self.assertEqual(trans_res.json()["status"], "evaluating")
        etag_v2 = trans_res.headers.get("etag")
        self.assertEqual(etag_v2, f'"opportunity:{opp_id}:v2"')


if __name__ == "__main__":
    unittest.main()
