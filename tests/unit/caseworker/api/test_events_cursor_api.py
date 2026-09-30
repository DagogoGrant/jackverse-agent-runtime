"""Tests for Domain Events cursor pagination streaming and user isolation."""

from __future__ import annotations

import unittest
from fastapi.testclient import TestClient

from caseworker.api.app import create_app
from caseworker.api.config import APIConfig
from caseworker.persistence.sqlite import SQLiteCaseworkerStorage


class TestEventsCursorAPI(unittest.TestCase):
    """Test cursor pagination streaming of domain events with monotonic position cursors."""

    def setUp(self) -> None:
        self.storage = SQLiteCaseworkerStorage(db_path=":memory:")
        self.config = APIConfig(dev_auth=True)
        self.app = create_app(config=self.config, storage=self.storage)
        self.client = TestClient(self.app)
        self.user_a_headers = {"X-JackVerse-User": "user_a"}
        self.user_b_headers = {"X-JackVerse-User": "user_b"}

    def tearDown(self) -> None:
        self.storage.close()

    def test_cursor_pagination_streaming_and_user_isolation(self) -> None:
        """Create multiple events for User A, stream with cursor pagination, and verify User B receives empty."""
        # 1. Generate 5 events for User A
        for i in range(5):
            res = self.client.post(
                "/api/v1/missions",
                headers=self.user_a_headers,
                json={"title": f"Mission {i}", "kind": "general_goal"},
            )
            self.assertEqual(res.status_code, 201)

        # Also generate 2 events for User B
        for i in range(2):
            res = self.client.post(
                "/api/v1/missions",
                headers=self.user_b_headers,
                json={"title": f"User B Mission {i}", "kind": "general_goal"},
            )
            self.assertEqual(res.status_code, 201)

        # 2. Fetch page 1 for User A with limit=2
        page1_res = self.client.get("/api/v1/events?limit=2", headers=self.user_a_headers)
        self.assertEqual(page1_res.status_code, 200)
        page1 = page1_res.json()
        self.assertEqual(len(page1["items"]), 2)
        self.assertTrue(page1["has_more"])
        self.assertIsNotNone(page1["next_position"])
        cursor_pos1 = page1["next_position"]

        # Verify items have monotonic positions
        self.assertEqual(page1["items"][0]["position"], 1)
        self.assertEqual(page1["items"][1]["position"], 2)

        # 3. Fetch page 2 using after_position cursor
        page2_res = self.client.get(
            f"/api/v1/events?limit=2&after_position={cursor_pos1}",
            headers=self.user_a_headers,
        )
        self.assertEqual(page2_res.status_code, 200)
        page2 = page2_res.json()
        self.assertEqual(len(page2["items"]), 2)
        self.assertTrue(page2["has_more"])
        cursor_pos2 = page2["next_position"]
        self.assertEqual(page2["items"][0]["position"], 3)
        self.assertEqual(page2["items"][1]["position"], 4)

        # 4. Fetch page 3
        page3_res = self.client.get(
            f"/api/v1/events?limit=2&after_position={cursor_pos2}",
            headers=self.user_a_headers,
        )
        self.assertEqual(page3_res.status_code, 200)
        page3 = page3_res.json()
        self.assertEqual(len(page3["items"]), 1)
        self.assertFalse(page3["has_more"])
        self.assertIsNone(page3["next_position"])
        self.assertEqual(page3["items"][0]["position"], 5)

        # 5. User B only sees their own events (positions 6 and 7)
        b_res = self.client.get("/api/v1/events", headers=self.user_b_headers)
        self.assertEqual(b_res.status_code, 200)
        b_events = b_res.json()["items"]
        self.assertEqual(len(b_events), 2)
        self.assertTrue(all(ev["user_id"] == "user_b" for ev in b_events))

    def test_max_limit_enforced(self) -> None:
        """Limit exceeding 100 is rejected with 422 Request Validation Error."""
        res = self.client.get("/api/v1/events?limit=150", headers=self.user_a_headers)
        self.assertEqual(res.status_code, 422)
        self.assertEqual(res.headers.get("content-type"), "application/problem+json")


if __name__ == "__main__":
    unittest.main()
