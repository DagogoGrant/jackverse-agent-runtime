"""Tests for Actions and Approvals API endpoints, server-side risk policy, and approval idempotency."""

from __future__ import annotations

import unittest
from fastapi.testclient import TestClient

from caseworker.api.app import create_app
from caseworker.api.config import APIConfig
from caseworker.persistence.sqlite import SQLiteCaseworkerStorage


class TestActionsAndApprovalsAPI(unittest.TestCase):
    """Test server-side ActionPolicy enforcement and idempotent human approvals."""

    def setUp(self) -> None:
        self.storage = SQLiteCaseworkerStorage(db_path=":memory:")
        self.config = APIConfig(dev_auth=True)
        self.app = create_app(config=self.config, storage=self.storage)
        self.client = TestClient(self.app)
        self.headers = {"X-JackVerse-User": "test_user"}

        # Create a test case
        case_res = self.client.post(
            "/api/v1/cases",
            headers=self.headers,
            json={"title": "Test Case", "goal": "Demonstrate action approval"},
        )
        self.assertEqual(case_res.status_code, 201)
        self.case_id = case_res.json()["case_id"]

    def tearDown(self) -> None:
        self.storage.close()

    def test_server_side_action_policy_derives_risk_and_approval(self) -> None:
        """Client cannot downgrade risk or bypass approvals; server derives risk_level and requires_approval."""
        # 1. High risk action: SUBMIT_FORM
        res = self.client.post(
            f"/api/v1/cases/{self.case_id}/actions",
            headers=self.headers,
            json={
                "action_type": "submit_form",
                "description": "Submit application to external portal",
                "parameters": {"portal": "example.com"},
            },
        )
        self.assertEqual(res.status_code, 201)
        action = res.json()
        self.assertEqual(action["risk_level"], "high")
        self.assertTrue(action["requires_approval"])

        # 2. Unknown custom action fails closed
        res_custom = self.client.post(
            f"/api/v1/cases/{self.case_id}/actions",
            headers=self.headers,
            json={
                "action_type": "custom_unknown_action",
                "description": "Execute unknown external action",
            },
        )
        self.assertEqual(res_custom.status_code, 201)
        action_custom = res_custom.json()
        self.assertTrue(action_custom["requires_approval"])

    def test_client_cannot_pass_risk_level_or_requires_approval(self) -> None:
        """Extra domain-owned fields in request body are rejected with 422."""
        res = self.client.post(
            f"/api/v1/cases/{self.case_id}/actions",
            headers=self.headers,
            json={
                "action_type": "submit_form",
                "description": "Attempt to bypass approval",
                "risk_level": "low",  # FORBIDDEN
                "requires_approval": False,  # FORBIDDEN
            },
        )
        self.assertEqual(res.status_code, 422)
        self.assertEqual(res.headers.get("content-type"), "application/problem+json")

    def test_approval_flow_and_idempotency(self) -> None:
        """Request approval, approve action, test idempotency on duplicate approve, test 409 on reversal."""
        # 1. Propose action requiring approval
        act_res = self.client.post(
            f"/api/v1/cases/{self.case_id}/actions",
            headers=self.headers,
            json={
                "action_type": "submit_form",
                "description": "Submit visa application",
            },
        )
        action_id = act_res.json()["action_id"]
        etag_act_v1 = act_res.headers.get("etag")

        # 2. Request approval
        req_res = self.client.post(
            f"/api/v1/actions/{action_id}/request-approval",
            headers={**self.headers, "If-Match": etag_act_v1},
        )
        self.assertEqual(req_res.status_code, 200)
        self.assertEqual(req_res.json()["status"], "awaiting_approval")

        # 3. Retrieve the pending approval
        approvals_res = self.client.get("/api/v1/approvals", headers=self.headers)
        self.assertEqual(approvals_res.status_code, 200)
        approvals = approvals_res.json()
        self.assertEqual(len(approvals), 1)
        approval_id = approvals[0]["approval_id"]

        # Get approval for ETag
        app_res = self.client.get(f"/api/v1/approvals/{approval_id}", headers=self.headers)
        etag_app_v1 = app_res.headers.get("etag")

        # 4. Approve action
        decide_res = self.client.post(
            f"/api/v1/approvals/{approval_id}/approve",
            headers={**self.headers, "If-Match": etag_app_v1},
            json={"reason": "User confirmed all application details"},
        )
        self.assertEqual(decide_res.status_code, 200)
        decision = decide_res.json()
        self.assertEqual(decision["approval"]["status"], "approved")
        self.assertEqual(decision["action"]["status"], "approved")
        etag_app_v2 = decide_res.headers.get("etag")

        # 5. Idempotent approval: Repeated call with current ETag returns 200 safely
        repeat_res = self.client.post(
            f"/api/v1/approvals/{approval_id}/approve",
            headers={**self.headers, "If-Match": etag_app_v2},
            json={"reason": "User confirmed again"},
        )
        self.assertEqual(repeat_res.status_code, 200)
        self.assertEqual(repeat_res.json()["approval"]["status"], "approved")

        # 6. Conflicting state reversal: Attempting to reject an already approved approval returns 409 Conflict
        conflict_res = self.client.post(
            f"/api/v1/approvals/{approval_id}/reject",
            headers={**self.headers, "If-Match": etag_app_v2},
            json={"reason": "Attempting to reverse approval"},
        )
        self.assertEqual(conflict_res.status_code, 409)
        self.assertEqual(conflict_res.headers.get("content-type"), "application/problem+json")
        self.assertEqual(conflict_res.json()["title"], "Invalid State Transition")


if __name__ == "__main__":
    unittest.main()
