"""Tests for Health / Readiness probes and Fail-Closed Authentication."""

from __future__ import annotations

import unittest
from fastapi.testclient import TestClient

from caseworker.api.app import create_app
from caseworker.api.config import APIConfig
from caseworker.persistence.sqlite import SQLiteCaseworkerStorage


class TestHealthAndAuth(unittest.TestCase):
    """Test health endpoints and authentication security policies."""

    def setUp(self) -> None:
        self.storage = SQLiteCaseworkerStorage(db_path=":memory:")

    def tearDown(self) -> None:
        self.storage.close()

    def test_health_live(self) -> None:
        config = APIConfig(dev_auth=False)
        app = create_app(config=config, storage=self.storage)
        client = TestClient(app)

        res = client.get("/health/live")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json(), {"status": "alive"})

    def test_health_ready(self) -> None:
        config = APIConfig(dev_auth=False)
        app = create_app(config=config, storage=self.storage)
        client = TestClient(app)

        res = client.get("/health/ready")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json(), {"status": "ready", "database": "connected"})

    def test_dev_auth_disabled_fails_closed(self) -> None:
        """When CASEWORKER_DEV_AUTH=false, dev headers must be ignored and rejected with 401."""
        config = APIConfig(dev_auth=False)
        app = create_app(config=config, storage=self.storage)
        client = TestClient(app)

        res = client.get("/api/v1/missions", headers={"X-JackVerse-User": "alice"})
        self.assertEqual(res.status_code, 401)
        self.assertEqual(res.headers.get("content-type"), "application/problem+json")
        body = res.json()
        self.assertEqual(body["status"], 401)
        self.assertEqual(body["title"], "Unauthorized")
        self.assertIn("CASEWORKER_DEV_AUTH is disabled", body["detail"])

    def test_dev_auth_enabled_missing_header_returns_401(self) -> None:
        """When dev auth is enabled, missing user header returns 401 Problem Detail."""
        config = APIConfig(dev_auth=True)
        app = create_app(config=config, storage=self.storage)
        client = TestClient(app)

        res = client.get("/api/v1/missions")
        self.assertEqual(res.status_code, 401)
        self.assertEqual(res.headers.get("content-type"), "application/problem+json")
        body = res.json()
        self.assertEqual(body["status"], 401)
        self.assertIn("X-JackVerse-User", body["detail"])

    def test_dev_auth_invalid_user_id_returns_400(self) -> None:
        """User identifiers with illegal characters return 400 Bad Request."""
        config = APIConfig(dev_auth=True)
        app = create_app(config=config, storage=self.storage)
        client = TestClient(app)

        res = client.get("/api/v1/missions", headers={"X-JackVerse-User": "alice evil; DROP TABLE;"})
        self.assertEqual(res.status_code, 400)
        self.assertEqual(res.headers.get("content-type"), "application/problem+json")
        body = res.json()
        self.assertEqual(body["status"], 400)
        self.assertIn("Invalid X-JackVerse-User identifier", body["detail"])

    def test_dev_auth_valid_user_succeeds(self) -> None:
        """Valid alphanumeric user identifier allows access."""
        config = APIConfig(dev_auth=True)
        app = create_app(config=config, storage=self.storage)
        client = TestClient(app)

        res = client.get("/api/v1/missions", headers={"X-JackVerse-User": "alice_123"})
        self.assertEqual(res.status_code, 200)
        body = res.json()
        self.assertEqual(body["items"], [])
        self.assertEqual(body["total"], 0)

    def test_dev_auth_bearer_fallback(self) -> None:
        """Authorization: Bearer dev:<user_id> is supported when dev auth is enabled."""
        config = APIConfig(dev_auth=True)
        app = create_app(config=config, storage=self.storage)
        client = TestClient(app)

        res = client.get("/api/v1/missions", headers={"Authorization": "Bearer dev:bob_456"})
        self.assertEqual(res.status_code, 200)


if __name__ == "__main__":
    unittest.main()
