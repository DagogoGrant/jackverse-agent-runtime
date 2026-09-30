"""Tests for resource-bound ETags, If-Match precondition enforcement, and SQLite request concurrency."""

from __future__ import annotations

import concurrent.futures
import os
import tempfile
import unittest
from fastapi.testclient import TestClient

from caseworker.api.app import create_app
from caseworker.api.config import APIConfig
from caseworker.persistence.sqlite import SQLiteCaseworkerStorage


class TestETagsAndConcurrency(unittest.TestCase):
    """Test resource-bound ETags and thread-safe SQLite concurrency under file-based databases."""

    def setUp(self) -> None:
        self.temp_file = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.temp_file.close()
        self.db_path = self.temp_file.name

        self.storage = SQLiteCaseworkerStorage(db_path=self.db_path)
        self.config = APIConfig(db_path=self.db_path, dev_auth=True)
        self.app = create_app(config=self.config, storage=self.storage)
        self.client = TestClient(self.app)
        self.headers = {"X-JackVerse-User": "test_user"}

    def tearDown(self) -> None:
        self.storage.close()
        if os.path.exists(self.db_path):
            os.remove(self.db_path)
        # WAL and SHM files cleanup
        for ext in ("-wal", "-shm"):
            if os.path.exists(self.db_path + ext):
                os.remove(self.db_path + ext)

    def test_etag_flow_and_preconditions(self) -> None:
        """Verify full ETag lifecycle: creation -> 428 on missing -> 412 on mismatch -> success on match."""
        # 1. Create mission
        create_res = self.client.post(
            "/api/v1/missions",
            headers=self.headers,
            json={"title": "Test Mission", "kind": "opportunity_pursuit"},
        )
        self.assertEqual(create_res.status_code, 201)
        mission_id = create_res.json()["mission_id"]
        etag_v1 = create_res.headers.get("etag")
        self.assertEqual(etag_v1, f'"mission:{mission_id}:v1"')

        # 2. Transition without If-Match -> 428 Precondition Required
        no_etag_res = self.client.post(
            f"/api/v1/missions/{mission_id}/transition",
            headers=self.headers,
            json={"new_status": "active"},
        )
        self.assertEqual(no_etag_res.status_code, 428)
        self.assertEqual(no_etag_res.headers.get("content-type"), "application/problem+json")
        self.assertIn("Precondition Required", no_etag_res.json()["title"])

        # 3. Transition with mismatched If-Match -> 412 Precondition Failed
        mismatch_res = self.client.post(
            f"/api/v1/missions/{mission_id}/transition",
            headers={**self.headers, "If-Match": f'"mission:{mission_id}:v999"'},
            json={"new_status": "active"},
        )
        self.assertEqual(mismatch_res.status_code, 412)
        self.assertEqual(mismatch_res.headers.get("content-type"), "application/problem+json")
        self.assertIn("Precondition Failed", mismatch_res.json()["title"])

        # 4. Transition with valid If-Match -> 200 OK and new ETag
        valid_res = self.client.post(
            f"/api/v1/missions/{mission_id}/transition",
            headers={**self.headers, "If-Match": etag_v1},
            json={"new_status": "active"},
        )
        self.assertEqual(valid_res.status_code, 200)
        etag_v2 = valid_res.headers.get("etag")
        self.assertEqual(etag_v2, f'"mission:{mission_id}:v2"')
        self.assertEqual(valid_res.json()["status"], "active")
        self.assertEqual(valid_res.json()["version"], 2)

    def test_sqlite_multithreaded_request_concurrency(self) -> None:
        """File-based SQLite connection closing on UoW exit handles concurrent requests cleanly."""
        num_threads = 10
        requests_per_thread = 5

        def worker(thread_idx: int) -> list[int]:
            user = f"user_thread_{thread_idx}"
            client = TestClient(self.app)
            statuses = []
            for i in range(requests_per_thread):
                res = client.post(
                    "/api/v1/missions",
                    headers={"X-JackVerse-User": user},
                    json={"title": f"Mission {i}", "kind": "general_goal"},
                )
                statuses.append(res.status_code)
            return statuses

        with concurrent.futures.ThreadPoolExecutor(max_workers=num_threads) as executor:
            futures = [executor.submit(worker, i) for i in range(num_threads)]
            all_statuses = []
            for f in concurrent.futures.as_completed(futures):
                all_statuses.extend(f.result())

        self.assertEqual(len(all_statuses), num_threads * requests_per_thread)
        self.assertTrue(all(code == 201 for code in all_statuses))


if __name__ == "__main__":
    unittest.main()
