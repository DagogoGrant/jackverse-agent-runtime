"""Tests for Missions and Cases API endpoints, cross-user isolation, and state transitions."""

from __future__ import annotations

import unittest
from fastapi.testclient import TestClient

from caseworker.api.app import create_app
from caseworker.api.config import APIConfig
from caseworker.persistence.sqlite import SQLiteCaseworkerStorage


class TestMissionsAndCasesAPI(unittest.TestCase):
    """Test mission and case endpoints, pagination, and multi-tenant isolation."""

    def setUp(self) -> None:
        self.storage = SQLiteCaseworkerStorage(db_path=":memory:")
        self.config = APIConfig(dev_auth=True)
        self.app = create_app(config=self.config, storage=self.storage)
        self.client = TestClient(self.app)
        self.user_a_headers = {"X-JackVerse-User": "user_a"}
        self.user_b_headers = {"X-JackVerse-User": "user_b"}

    def tearDown(self) -> None:
        self.storage.close()

    def test_missions_crud_and_cross_user_isolation(self) -> None:
        """User A creates mission; User B cannot see it, transition it, or detect its existence."""
        # 1. User A creates mission
        res = self.client.post(
            "/api/v1/missions",
            headers=self.user_a_headers,
            json={"title": "Find Housing in Austin", "kind": "opportunity_pursuit"},
        )
        self.assertEqual(res.status_code, 201)
        mission_a = res.json()
        mission_id = mission_a["mission_id"]

        # 2. User A can retrieve it
        get_res = self.client.get(f"/api/v1/missions/{mission_id}", headers=self.user_a_headers)
        self.assertEqual(get_res.status_code, 200)
        self.assertEqual(get_res.json()["title"], "Find Housing in Austin")

        # 3. User B receives 404 Not Found (object enumeration defense)
        user_b_res = self.client.get(f"/api/v1/missions/{mission_id}", headers=self.user_b_headers)
        self.assertEqual(user_b_res.status_code, 404)
        self.assertEqual(user_b_res.headers.get("content-type"), "application/problem+json")

        # 4. User B cannot transition User A's mission
        user_b_trans = self.client.post(
            f"/api/v1/missions/{mission_id}/transition",
            headers={**self.user_b_headers, "If-Match": f'"mission:{mission_id}:v1"'},
            json={"new_status": "active"},
        )
        self.assertEqual(user_b_trans.status_code, 404)

    def test_mission_pause_and_resume(self) -> None:
        """Pause and resume convenience endpoints update mission status."""
        res = self.client.post(
            "/api/v1/missions",
            headers=self.user_a_headers,
            json={"title": "Fellowship Search", "kind": "opportunity_pursuit"},
        )
        mission_id = res.json()["mission_id"]
        etag_v1 = res.headers.get("etag")

        # First activate
        act_res = self.client.post(
            f"/api/v1/missions/{mission_id}/transition",
            headers={**self.user_a_headers, "If-Match": etag_v1},
            json={"new_status": "active"},
        )
        etag_v2 = act_res.headers.get("etag")

        # Pause
        pause_res = self.client.post(
            f"/api/v1/missions/{mission_id}/pause",
            headers={**self.user_a_headers, "If-Match": etag_v2},
        )
        self.assertEqual(pause_res.status_code, 200)
        self.assertEqual(pause_res.json()["status"], "paused")
        etag_v3 = pause_res.headers.get("etag")

        # Resume
        resume_res = self.client.post(
            f"/api/v1/missions/{mission_id}/resume",
            headers={**self.user_a_headers, "If-Match": etag_v3},
        )
        self.assertEqual(resume_res.status_code, 200)
        self.assertEqual(resume_res.json()["status"], "active")

    def test_cases_crud_transitions_and_resolution(self) -> None:
        """Create case under mission, transition through state machine, and resolve."""
        # 1. Create parent mission
        m_res = self.client.post(
            "/api/v1/missions",
            headers=self.user_a_headers,
            json={"title": "Career Move", "kind": "opportunity_pursuit"},
        )
        mission_id = m_res.json()["mission_id"]

        # 2. Create case under mission
        c_res = self.client.post(
            f"/api/v1/missions/{mission_id}/cases",
            headers=self.user_a_headers,
            json={
                "title": "Software Engineer Application at TechCorp",
                "goal": "Submit tailored resume and cover letter",
                "case_type": "job_application",
            },
        )
        self.assertEqual(c_res.status_code, 201)
        case_data = c_res.json()
        case_id = case_data["case_id"]
        etag_v1 = c_res.headers.get("etag")
        self.assertEqual(case_data["status"], "new")

        # 3. List mission cases
        list_res = self.client.get(f"/api/v1/missions/{mission_id}/cases", headers=self.user_a_headers)
        self.assertEqual(list_res.status_code, 200)
        self.assertEqual(len(list_res.json()), 1)

        # 4. User B gets 404 listing User A's mission cases
        b_list_res = self.client.get(f"/api/v1/missions/{mission_id}/cases", headers=self.user_b_headers)
        self.assertEqual(b_list_res.status_code, 404)

        # 5. Transition: NEW -> INTAKE
        t_res = self.client.post(
            f"/api/v1/cases/{case_id}/transition",
            headers={**self.user_a_headers, "If-Match": etag_v1},
            json={"new_status": "intake", "reason": "Beginning intake assessment"},
        )
        self.assertEqual(t_res.status_code, 200)
        self.assertEqual(t_res.json()["status"], "intake")
        etag_v2 = t_res.headers.get("etag")

        # 6. Transition: INTAKE -> INVESTIGATING
        t_res2 = self.client.post(
            f"/api/v1/cases/{case_id}/transition",
            headers={**self.user_a_headers, "If-Match": etag_v2},
            json={"new_status": "investigating"},
        )
        etag_v3 = t_res2.headers.get("etag")

        # 7. Resolve case
        r_res = self.client.post(
            f"/api/v1/cases/{case_id}/resolve",
            headers={**self.user_a_headers, "If-Match": etag_v3},
            json={"outcome": "Application successfully submitted and confirmed by recruiter"},
        )
        self.assertEqual(r_res.status_code, 200)
        resolved_data = r_res.json()
        self.assertEqual(resolved_data["status"], "resolved")
        self.assertEqual(resolved_data["outcome"], "Application successfully submitted and confirmed by recruiter")


if __name__ == "__main__":
    unittest.main()
