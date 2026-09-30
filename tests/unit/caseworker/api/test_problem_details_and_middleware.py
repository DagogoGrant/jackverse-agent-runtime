"""Tests for RFC 9457 Problem Details and Security/Correlation Middleware."""

from __future__ import annotations

import unittest
from fastapi.testclient import TestClient

from caseworker.api.app import create_app
from caseworker.api.config import APIConfig
from caseworker.persistence.sqlite import SQLiteCaseworkerStorage


class TestProblemDetailsAndMiddleware(unittest.TestCase):
    """Test RFC 9457 error format, correlation IDs, and security response headers."""

    def setUp(self) -> None:
        self.storage = SQLiteCaseworkerStorage(db_path=":memory:")
        self.config = APIConfig(dev_auth=True)
        self.app = create_app(config=self.config, storage=self.storage)
        self.client = TestClient(self.app)
        self.headers = {"X-JackVerse-User": "test_user"}

    def tearDown(self) -> None:
        self.storage.close()

    def test_correlation_id_generated_and_propagated(self) -> None:
        """When X-Request-ID is missing, a new UUID is generated and attached to response."""
        res = self.client.get("/health/live")
        self.assertEqual(res.status_code, 200)
        self.assertIn("x-request-id", res.headers)
        self.assertTrue(len(res.headers["x-request-id"]) > 10)

    def test_correlation_id_preserved_when_supplied(self) -> None:
        """Incoming X-Request-ID is echoed back on response and included in error instance."""
        custom_id = "req-custom-xyz-123"
        res = self.client.get(
            "/api/v1/missions/non-existent-mission",
            headers={**self.headers, "X-Request-ID": custom_id},
        )
        self.assertEqual(res.status_code, 404)
        self.assertEqual(res.headers.get("x-request-id"), custom_id)
        self.assertEqual(res.headers.get("content-type"), "application/problem+json")
        body = res.json()
        self.assertEqual(body["instance"], f"urn:request:{custom_id}")

    def test_security_headers_present(self) -> None:
        """Verify X-Content-Type-Options: nosniff and Cache-Control: no-store."""
        res = self.client.get("/api/v1/missions", headers=self.headers)
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.headers.get("x-content-type-options"), "nosniff")
        self.assertEqual(res.headers.get("x-frame-options"), "DENY")
        self.assertIn("no-store", res.headers.get("cache-control", ""))

    def test_rfc9457_not_found_format(self) -> None:
        """404 errors adhere to RFC 9457 Problem Details schema."""
        res = self.client.get("/api/v1/missions/nonexistent", headers=self.headers)
        self.assertEqual(res.status_code, 404)
        self.assertEqual(res.headers.get("content-type"), "application/problem+json")
        body = res.json()
        self.assertEqual(body["type"], "urn:caseworker:error:not-found")
        self.assertEqual(body["title"], "Resource Not Found")
        self.assertEqual(body["status"], 404)
        self.assertIn("nonexistent", body["detail"])

    def test_rfc9457_validation_error_format(self) -> None:
        """422 Request Validation errors adhere to RFC 9457 with detailed error locations."""
        res = self.client.post("/api/v1/missions", headers=self.headers, json={})
        self.assertEqual(res.status_code, 422)
        self.assertEqual(res.headers.get("content-type"), "application/problem+json")
        body = res.json()
        self.assertEqual(body["type"], "urn:caseworker:error:validation")
        self.assertEqual(body["title"], "Request Validation Error")
        self.assertEqual(body["status"], 422)
        self.assertIn("errors", body)
        self.assertTrue(len(body["errors"]) > 0)

    def test_forbidden_extra_fields_rejected_with_422(self) -> None:
        """Write requests with server-owned extra fields (user_id, version, status) are rejected."""
        res = self.client.post(
            "/api/v1/missions",
            headers=self.headers,
            json={
                "title": "Hacker Mission",
                "kind": "general_goal",
                "user_id": "other_user",  # FORBIDDEN
                "version": 99,  # FORBIDDEN
            },
        )
        self.assertEqual(res.status_code, 422)
        self.assertEqual(res.headers.get("content-type"), "application/problem+json")
        body = res.json()
        self.assertEqual(body["type"], "urn:caseworker:error:validation")
        errors = [err["loc"][-1] for err in body.get("errors", [])]
        self.assertTrue("user_id" in errors or "version" in errors)


if __name__ == "__main__":
    unittest.main()
