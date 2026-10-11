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

        # Transition: DISCOVERED -> NORMALIZED
        trans_res = self.client.post(
            f"/api/v1/opportunities/{opp_id}/transition",
            headers={**self.user_a_headers, "If-Match": etag_v1},
            json={"new_status": "normalized", "reason": "Parsed requirements"},
        )
        self.assertEqual(trans_res.status_code, 200)
        self.assertEqual(trans_res.json()["status"], "normalized")
        etag_v2 = trans_res.headers.get("etag")
        self.assertEqual(etag_v2, f'"opportunity:{opp_id}:v2"')

        # Transition: NORMALIZED -> EVALUATING
        trans_res2 = self.client.post(
            f"/api/v1/opportunities/{opp_id}/transition",
            headers={**self.user_a_headers, "If-Match": etag_v2},
            json={"new_status": "evaluating", "reason": "Passed initial keyword screening"},
        )
        self.assertEqual(trans_res2.status_code, 200)
        self.assertEqual(trans_res2.json()["status"], "evaluating")
        etag_v3 = trans_res2.headers.get("etag")
        self.assertEqual(etag_v3, f'"opportunity:{opp_id}:v3"')

    def test_normalize_and_evaluate_endpoints(self) -> None:
        """Test dedicated normalize, evaluate, and get evaluation endpoints."""
        payload = {
            "title": "Backend Systems Engineer",
            "opportunity_type": "job",
            "organization": "Stripe",
            "requirements": ["Python 4+ years", "PostgreSQL", "Remote EU"],
        }
        create_res = self.client.post("/api/v1/opportunities", headers=self.user_a_headers, json=payload)
        opp_id = create_res.json()["opportunity_id"]
        etag_v1 = create_res.headers.get("etag")

        # 1. Normalize requirements
        norm_res = self.client.post(
            f"/api/v1/opportunities/{opp_id}/normalize",
            headers={**self.user_a_headers, "If-Match": etag_v1},
        )
        self.assertEqual(norm_res.status_code, 200)
        norm_data = norm_res.json()
        self.assertEqual(norm_data["status"], "normalized")
        self.assertEqual(len(norm_data["requirements"]), 3)
        etag_v2 = norm_res.headers.get("etag")

        # 2. Evaluate requirements
        eval_res = self.client.post(
            f"/api/v1/opportunities/{opp_id}/evaluate",
            headers={**self.user_a_headers, "If-Match": etag_v2},
        )
        self.assertEqual(eval_res.status_code, 200)
        eval_data = eval_res.json()
        self.assertIn("eligibility", eval_data)
        self.assertIn("fit", eval_data)
        self.assertEqual(eval_data["evaluation_policy"], "opp_eval_v1")

        # 3. GET evaluation
        get_eval_res = self.client.get(
            f"/api/v1/opportunities/{opp_id}/evaluation",
            headers=self.user_a_headers,
        )
        self.assertEqual(get_eval_res.status_code, 200)
        self.assertEqual(get_eval_res.json()["evaluation_id"], eval_data["evaluation_id"])

    def test_shortlist_and_convert_to_case_with_idempotency(self) -> None:
        """Test shortlisting, case conversion, 201 Created Location header, and idempotent 200 replay."""
        # Setup context facts for user_a
        with self.storage.unit_of_work() as uow:
            from caseworker.domain.context import ContextFact
            from caseworker.domain.enums import VerificationStatus
            uow.context.save(
                ContextFact(
                    user_id="user_a",
                    namespace="skills",
                    key="python",
                    value="Expert Python Developer",
                    verification_status=VerificationStatus.USER_VERIFIED,
                    allowed_purposes=["opportunity_evaluation"],
                )
            )

        # Create opportunity
        payload = {
            "title": "Machine Learning Engineer",
            "opportunity_type": "job",
            "organization": "Mistral AI",
            "location": "Remote",
            "requirements": ["Python proficiency", "Remote friendly"],
        }
        create_res = self.client.post("/api/v1/opportunities", headers=self.user_a_headers, json=payload)
        opp_id = create_res.json()["opportunity_id"]
        etag_v1 = create_res.headers.get("etag")

        # Normalize
        norm_res = self.client.post(
            f"/api/v1/opportunities/{opp_id}/normalize",
            headers={**self.user_a_headers, "If-Match": etag_v1},
        )
        etag_v2 = norm_res.headers.get("etag")

        # Evaluate
        eval_res = self.client.post(
            f"/api/v1/opportunities/{opp_id}/evaluate",
            headers={**self.user_a_headers, "If-Match": etag_v2},
        )
        self.assertEqual(eval_res.json()["eligibility"]["status"], "eligible")
        etag_v3 = eval_res.headers.get("etag")

        # Generic /transition to shortlisted is BLOCKED
        bad_trans = self.client.post(
            f"/api/v1/opportunities/{opp_id}/transition",
            headers={**self.user_a_headers, "If-Match": etag_v3},
            json={"new_status": "shortlisted"},
        )
        self.assertEqual(bad_trans.status_code, 409)

        # Dedicated /shortlist endpoint succeeds
        short_res = self.client.post(
            f"/api/v1/opportunities/{opp_id}/shortlist",
            headers={**self.user_a_headers, "If-Match": etag_v3},
            json={"reason": "Ideal ML stack match"},
        )
        self.assertEqual(short_res.status_code, 200)
        self.assertEqual(short_res.json()["status"], "shortlisted")
        etag_v4 = short_res.headers.get("etag")

        # Convert to Case: 1st time -> 201 Created with Location header
        conv_res = self.client.post(
            f"/api/v1/opportunities/{opp_id}/convert-to-case",
            headers={**self.user_a_headers, "If-Match": etag_v4},
            json={"title": "Custom Application Title"},
        )
        self.assertEqual(conv_res.status_code, 201)
        conv_data = conv_res.json()
        case_id = conv_data["case_id"]
        self.assertTrue(conv_data["created"])
        self.assertIn("location", conv_res.headers)
        self.assertEqual(conv_res.headers["location"], f"/api/v1/cases/{case_id}")
        self.assertEqual(conv_data["case"]["title"], "Custom Application Title")
        self.assertEqual(conv_data["case"]["case_type"], "job_application")  # mapped

        # Convert to Case: Replay -> 200 OK without requiring ETag
        replay_res = self.client.post(
            f"/api/v1/opportunities/{opp_id}/convert-to-case",
            headers=self.user_a_headers,
            json={},
        )
        self.assertEqual(replay_res.status_code, 200)
        self.assertFalse(replay_res.json()["created"])
        self.assertEqual(replay_res.json()["case_id"], case_id)

    def test_mission_opportunities_list(self) -> None:
        """GET /api/v1/missions/{mission_id}/opportunities lists associated opportunities."""
        # 1. Create a mission
        m_res = self.client.post(
            "/api/v1/missions",
            headers=self.user_a_headers,
            json={"title": "Relocate to Berlin", "goal": "Find a job in Berlin", "kind": "opportunity_pursuit"},
        )
        mission_id = m_res.json()["mission_id"]

        # 2. Create opportunity linked to mission
        self.client.post(
            "/api/v1/opportunities",
            headers=self.user_a_headers,
            json={"title": "Berlin Job 1", "opportunity_type": "job", "mission_id": mission_id},
        )
        self.client.post(
            "/api/v1/opportunities",
            headers=self.user_a_headers,
            json={"title": "Berlin Job 2", "opportunity_type": "job", "mission_id": mission_id},
        )

        # 3. Fetch opportunities via mission route
        list_res = self.client.get(f"/api/v1/missions/{mission_id}/opportunities", headers=self.user_a_headers)
        self.assertEqual(list_res.status_code, 200)
        items = list_res.json()
        self.assertEqual(len(items), 2)
        titles = {i["title"] for i in items}
        self.assertIn("Berlin Job 1", titles)
        self.assertIn("Berlin Job 2", titles)


if __name__ == "__main__":
    unittest.main()
