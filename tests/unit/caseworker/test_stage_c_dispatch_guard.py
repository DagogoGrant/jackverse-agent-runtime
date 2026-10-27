"""Unit tests for Stage C guarded dispatch, replay idempotency, and transmission barrier enforcement."""

from __future__ import annotations

import io
import json
from pathlib import Path
import unittest
import urllib.error
from unittest.mock import MagicMock, patch
from fastapi.testclient import TestClient

from caseworker.api.app import create_app
from caseworker.api.config import APIConfig
from caseworker.domain.action import Action
from caseworker.domain.approval import Approval
from caseworker.domain.case import Case
from caseworker.domain.enums import (
    ActionStatus,
    ActionType,
    ApprovalStatus,
    CaseStatus,
    CaseType,
    ExecutionAttemptStatus,
    ExecutionProviderKind,
    ExecutionReceiptStatus,
    ExternalExecutionPlanStatus,
    RiskLevel,
)
from caseworker.domain.execution_attempt import ExecutionAttempt
from caseworker.domain.execution_plan import ExecutionDestination, ExternalExecutionPlan
from caseworker.domain.execution_receipt import ExecutionReceipt
from caseworker.persistence.sqlite import SQLiteCaseworkerStorage
from scripts.guarded_stage_c_dispatch import (
    CONTROLLED_ACTION_ID,
    CONTROLLED_APPROVAL_ID,
    CONTROLLED_CASE_ID,
    CONTROLLED_PLAN_ID,
    CONTROLLED_PLAN_FINGERPRINT,
    EXPECTED_DESTINATION_EMAIL,
    EXPECTED_SEALED_MIME_HASH,
    REQUIRED_EXECUTION_DECISION_TOKEN,
    perform_guarded_dispatch,
    print_four_tier_report,
    verify_complete_api_chain_of_custody,
)


class TestStageCGuardedDispatch(unittest.TestCase):
    """Test Stage C dispatch safeguards, replay idempotency, and execution barriers."""

    def setUp(self) -> None:
        self.storage = SQLiteCaseworkerStorage(db_path=":memory:")
        self.config = APIConfig(dev_auth=True)
        self.app = create_app(config=self.config, storage=self.storage)
        self.client = TestClient(self.app)
        self.user_id = "test-operator"
        self.headers = {"X-JackVerse-User": self.user_id}
        self.case_id = "case-controlled-test-5332"
        self.plan_id = "plan-d4819278ab3f45af"
        self.action_id = "act_91966280c188"
        self.approval_id = "65cb25bb-0539-4f61-917c-46cf4a84f9dd"

        # Disable foreign keys for unit test fixture population in memory
        if self.storage._shared_conn:
            self.storage._shared_conn.execute("PRAGMA foreign_keys = OFF;")

        with self.storage.unit_of_work() as uow:
            self.case = Case(
                case_id=self.case_id,
                user_id=self.user_id,
                title="Controlled Test Case",
                goal="Controlled Gmail Transmission",
                case_type=CaseType.JOB_APPLICATION,
                status=CaseStatus.ACTION_REQUIRED,
            )
            uow.cases.save(self.case)

            dest = ExecutionDestination(
                destination_type="email",
                destination_provenance="user_provided",
                canonical_url="mailto:test@example.com",
                organization="Acme Corp",
                is_exact_destination=True,
            )
            self.plan = ExternalExecutionPlan(
                plan_id=self.plan_id,
                case_id=self.case.case_id,
                opportunity_id="opp-1",
                package_id="pkg-1",
                package_fingerprint="pkg-fp-1",
                user_id=self.user_id,
                destination=dest,
                destination_provenance="user_provided",
                target_schema=None,
                target_schema_hash="",
                status=ExternalExecutionPlanStatus.READY_FOR_AUTHORIZATION,
            )
            uow.execution_plans.save(self.plan)

            self.action = Action(
                action_id=self.action_id,
                case_id=self.case.case_id,
                action_type=ActionType.SUBMIT_FORM,
                description="Controlled Gmail transmission",
                parameters={"plan_id": self.plan.plan_id},
                status=ActionStatus.APPROVED,
                risk_level=RiskLevel.HIGH,
                requires_approval=True,
                version=3,
            )
            uow.actions.save(self.action)

            self.approval = Approval(
                approval_id=self.approval_id,
                action_id=self.action.action_id,
                case_id=self.case.case_id,
                user_id=self.user_id,
                action_fingerprint=self.action.compute_fingerprint(),
                status=ApprovalStatus.APPROVED,
                version=1,
            )
            uow.approvals.save(self.approval)

    def tearDown(self) -> None:
        self.storage.close()

    def test_idempotent_replay_does_not_reexecute_provider(self) -> None:
        """When an action has succeeded, subsequent execute requests return 200 with existing receipt and 0 re-sends."""
        with self.storage.unit_of_work() as uow:
            attempt = ExecutionAttempt(
                attempt_id="att-first",
                action_id=self.action_id,
                approval_id=self.approval_id,
                plan_id=self.plan_id,
                case_id=self.case_id,
                user_id=self.user_id,
                lease_token="lease-1",
                fencing_token=1,
                status=ExecutionAttemptStatus.SUCCEEDED,
                provider_kind=ExecutionProviderKind.SIMULATED_TEST,
                attempt_number=1,
                irreversible_action_taken=True,
            )
            uow.execution_attempts.save(attempt)

            receipt = ExecutionReceipt(
                receipt_id="rcpt-original",
                attempt_id=attempt.attempt_id,
                action_id=self.action_id,
                approval_id=self.approval_id,
                plan_id=self.plan_id,
                case_id=self.case_id,
                user_id=self.user_id,
                destination_hostname="gmail.com",
                destination_url="mailto:test@example.com",
                destination_type="email",
                execution_plan_fingerprint=self.plan.execution_plan_fingerprint,
                authorized_action_fingerprint=self.action.compute_fingerprint(),
                package_fingerprint="pkg-fp-1",
                status=ExecutionReceiptStatus.VERIFIED_SUCCESS,
                version=1,
            )
            uow.execution_receipts.save(receipt)

        # Call execute endpoint
        res = self.client.post(
            f"/api/v1/actions/{self.action_id}/execute",
            headers=self.headers,
            json={"simulation_mode": "success"},
        )

        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertEqual(data["receipt"]["receipt_id"], "rcpt-original")
        self.assertEqual(data["receipt"]["status"], "verified_success")

        # Verify no second attempt was created
        with self.storage.unit_of_work() as uow:
            all_attempts = uow.execution_attempts.list_by_case(self.case_id)
            self.assertEqual(len(all_attempts), 1)

    def test_ambiguous_submission_strictly_blocks_automatic_retry(self) -> None:
        """When a previous attempt resulted in AMBIGUOUS_SUBMISSION, retrying is blocked."""
        with self.storage.unit_of_work() as uow:
            attempt = ExecutionAttempt(
                attempt_id="att-ambig",
                action_id=self.action_id,
                approval_id=self.approval_id,
                plan_id=self.plan_id,
                case_id=self.case_id,
                user_id=self.user_id,
                lease_token="lease-ambig",
                fencing_token=1,
                status=ExecutionAttemptStatus.FAILED,
                provider_kind=ExecutionProviderKind.SIMULATED_TEST,
                attempt_number=1,
                irreversible_action_taken=True,
            )
            uow.execution_attempts.save(attempt)

            receipt = ExecutionReceipt(
                receipt_id="rcpt-ambig",
                attempt_id=attempt.attempt_id,
                action_id=self.action_id,
                approval_id=self.approval_id,
                plan_id=self.plan_id,
                case_id=self.case_id,
                user_id=self.user_id,
                destination_hostname="gmail.com",
                destination_url="mailto:test@example.com",
                destination_type="email",
                execution_plan_fingerprint=self.plan.execution_plan_fingerprint,
                authorized_action_fingerprint=self.action.compute_fingerprint(),
                package_fingerprint="pkg-fp-1",
                status=ExecutionReceiptStatus.AMBIGUOUS_SUBMISSION,
                version=1,
            )
            uow.execution_receipts.save(receipt)

        res = self.client.post(
            f"/api/v1/actions/{self.action_id}/execute",
            headers=self.headers,
            json={"simulation_mode": "success"},
        )

        self.assertEqual(res.status_code, 409)
        self.assertIn("AMBIGUOUS submission", res.json().get("detail", ""))

    def test_missing_if_match_returns_428_on_unexecuted_action(self) -> None:
        """Executing an unexecuted action without If-Match returns HTTP 428 Precondition Required."""
        res = self.client.post(
            f"/api/v1/actions/{self.action_id}/execute",
            headers=self.headers,
            json={"simulation_mode": "success"},
        )
        self.assertEqual(res.status_code, 428)

    def test_stale_etag_returns_412(self) -> None:
        """Executing an unexecuted action with stale ETag returns HTTP 412 Precondition Failed."""
        res = self.client.post(
            f"/api/v1/actions/{self.action_id}/execute",
            headers={**self.headers, "If-Match": f'"action:{self.action_id}:v1"'},
            json={"simulation_mode": "success"},
        )
        self.assertEqual(res.status_code, 412)

    def test_four_tier_reporting_does_not_substitute_destination_url(self) -> None:
        """print_four_tier_report must NOT substitute destination_url when provider_message_id is missing."""
        dispatch_result = {
            "outcome": "CONFIRMED_COMPLETION",
            "http_code": 200,
            "latency_seconds": 0.42,
            "etag": '"action:act_91966280c188:v3"',
            "data": {
                "case_status": "waiting_external",
                "receipt": {
                    "receipt_id": "rcpt-test-123",
                    "status": "verified_success",
                    "receipt_hash": "hash-receipt-abc",
                    "destination_url": "mailto:dagogograntjack@gmail.com",
                    "safe_provider_metadata": {},  # No provider_message_id
                },
            },
        }
        plan_info = {
            "email_manifest": {
                "subject": "[JACKVERSE INTERNAL TEST] Governed Gmail Transmission Verification",
                "message_id": "<test-msg-id@gmail.com>",
            }
        }

        with patch("sys.stdout", new=io.StringIO()) as fake_out:
            print_four_tier_report(dispatch_result, plan_info, "dagogograntjack@gmail.com")
            output = fake_out.getvalue()

        self.assertIn("[TIER 1: HTTP REQUEST ACCEPTED BY JACKVERSE]", output)
        self.assertIn("[TIER 2: GMAIL PROVIDER ACCEPTANCE]", output)
        self.assertIn("[TIER 3: VERIFIED EXECUTION RECEIPT]", output)
        self.assertIn("[TIER 4: MAILBOX DELIVERY OBSERVED BY OPERATOR]", output)

        # Must report None rather than substituting mailto:dagogograntjack@gmail.com
        self.assertIn("Gmail Message ID:     None (no provider message ID returned)", output)
        self.assertNotIn("Gmail Message ID:     mailto:", output)
        self.assertNotIn("External Reference:   mailto:", output)

    @patch("scripts.guarded_stage_c_dispatch._api_get")
    def test_verify_complete_api_chain_of_custody_hard_refusal_on_attempts(self, mock_api_get) -> None:
        """API preflight must hard-fail if execution attempts count > 0."""
        # 1. Health
        mock_api_get.side_effect = [
            (200, {}, {"status": "ready", "database": "connected"}),
            (200, {}, {"status": "action_required", "user_id": "operator-1"}),
            (200, {"ETag": '"action:act_91966280c188:v3"'}, {
                "status": "approved",
                "fingerprint": "e16f0e85ba41ddd43dd5401c7c0c65fd0b2f0f4172b1f22a77024dafb0f0ec86",
                "parameters": {
                    "plan_id": CONTROLLED_PLAN_ID,
                    "execution_plan_fingerprint": CONTROLLED_PLAN_FINGERPRINT,
                    "package_id": "pkg_d17af0209e42",
                    "package_fingerprint": "1ebfd0bbaf5c371b089d6a412fb8fc0115b9c37bab834fb7845d9f92846dea53",
                },
            }),
            (200, {}, {
                "status": "approved",
                "action_id": CONTROLLED_ACTION_ID,
                "action_fingerprint": "e16f0e85ba41ddd43dd5401c7c0c65fd0b2f0f4172b1f22a77024dafb0f0ec86",
            }),
            (200, {}, {
                "plans": [{
                    "plan_id": CONTROLLED_PLAN_ID,
                    "status": "ready_for_authorization",
                    "execution_plan_fingerprint": CONTROLLED_PLAN_FINGERPRINT,
                    "destination": {"canonical_url": f"mailto:{EXPECTED_DESTINATION_EMAIL}"},
                    "email_manifest": {
                        "to_email": EXPECTED_DESTINATION_EMAIL,
                        "from_email": EXPECTED_DESTINATION_EMAIL,
                        "sealed_payload_hash": EXPECTED_SEALED_MIME_HASH,
                        "attachments": [{
                            "filename": "cv_operator-1.txt",
                            "byte_size": 131,
                            "content_sha256": "43057a3551057a31e6cc00494ce435943b2a1e25f4f61a23a253ac58326a3573",
                        }],
                    },
                }]
            }),
            # Attempts endpoint returns 1 attempt -> MUST HARD FAIL
            (200, {}, {"attempts": [{"attempt_id": "att-1"}], "total": 1}),
        ]

        with self.assertRaises(RuntimeError) as ctx:
            verify_complete_api_chain_of_custody("https://localhost:3443", Path("certs/localhost.crt"), "dummy-token")
        self.assertIn("HARD REFUSAL: Execution attempts already exist", str(ctx.exception))

    @patch("scripts.guarded_stage_c_dispatch._api_get")
    def test_verify_complete_api_chain_of_custody_hard_refusal_on_receipt(self, mock_api_get) -> None:
        """API preflight must hard-fail if execution receipt endpoint returns HTTP 200 (receipt exists)."""
        mock_api_get.side_effect = [
            (200, {}, {"status": "ready", "database": "connected"}),
            (200, {}, {"status": "action_required", "user_id": "operator-1"}),
            (200, {"ETag": '"action:act_91966280c188:v3"'}, {
                "status": "approved",
                "fingerprint": "e16f0e85ba41ddd43dd5401c7c0c65fd0b2f0f4172b1f22a77024dafb0f0ec86",
                "parameters": {
                    "plan_id": CONTROLLED_PLAN_ID,
                    "execution_plan_fingerprint": CONTROLLED_PLAN_FINGERPRINT,
                    "package_id": "pkg_d17af0209e42",
                    "package_fingerprint": "1ebfd0bbaf5c371b089d6a412fb8fc0115b9c37bab834fb7845d9f92846dea53",
                },
            }),
            (200, {}, {
                "status": "approved",
                "action_id": CONTROLLED_ACTION_ID,
                "action_fingerprint": "e16f0e85ba41ddd43dd5401c7c0c65fd0b2f0f4172b1f22a77024dafb0f0ec86",
            }),
            (200, {}, {
                "plans": [{
                    "plan_id": CONTROLLED_PLAN_ID,
                    "status": "ready_for_authorization",
                    "execution_plan_fingerprint": CONTROLLED_PLAN_FINGERPRINT,
                    "destination": {"canonical_url": f"mailto:{EXPECTED_DESTINATION_EMAIL}"},
                    "email_manifest": {
                        "to_email": EXPECTED_DESTINATION_EMAIL,
                        "from_email": EXPECTED_DESTINATION_EMAIL,
                        "sealed_payload_hash": EXPECTED_SEALED_MIME_HASH,
                        "attachments": [{
                            "filename": "cv_operator-1.txt",
                            "byte_size": 131,
                            "content_sha256": "43057a3551057a31e6cc00494ce435943b2a1e25f4f61a23a253ac58326a3573",
                        }],
                    },
                }]
            }),
            (200, {}, {"attempts": [], "total": 0}),
            # Receipt endpoint returns HTTP 200 -> MUST HARD FAIL
            (200, {}, {"receipt_id": "rcpt-existing", "status": "verified_success"}),
        ]

        with self.assertRaises(RuntimeError) as ctx:
            verify_complete_api_chain_of_custody("https://localhost:3443", Path("certs/localhost.crt"), "dummy-token")
        self.assertIn("HARD REFUSAL: Execution receipt already exists", str(ctx.exception))

    def test_four_tier_reporting_with_real_gmail_schema(self) -> None:
        """print_four_tier_report consumes real Gmail metadata fields (gmail_message_id, gmail_thread_id, rfc822_message_id, external_reference_id)."""
        gmail_msg_id = "18c2a3f9e8d1b2c3"
        gmail_thread_id = "18c2a3f9e8d1b2c3"
        rfc822_msg_id = "<jv-test-msg-123@gmail.com>"
        dispatch_result = {
            "outcome": "CONFIRMED_COMPLETION",
            "http_code": 200,
            "latency_seconds": 0.354,
            "etag": '"action:act_91966280c188:v3"',
            "data": {
                "case_status": "waiting_external",
                "receipt": {
                    "receipt_id": "rcpt-live-test-123",
                    "status": "verified_success",
                    "receipt_hash": "6b72a6b22591739c9480bf13e1f57913cf673e5bf02ab7bf6c25aaad825cb3b0",
                    "destination_url": "mailto:dagogograntjack@gmail.com",
                    "external_reference_id": gmail_msg_id,
                    "safe_provider_metadata": {
                        "http_status": 200,
                        "duration_ms": 350,
                        "provider_status": "GMAIL_API_ACCEPTED",
                        "response_category": "success",
                        "gmail_message_id": gmail_msg_id,
                        "gmail_thread_id": gmail_thread_id,
                        "rfc822_message_id": rfc822_msg_id,
                        "sealed_payload_hash": EXPECTED_SEALED_MIME_HASH,
                        "recipient_email": "dagogograntjack@gmail.com",
                        "sender_email": "dagogograntjack@gmail.com",
                        "subject": "[JACKVERSE INTERNAL TEST] Governed Gmail Transmission Verification",
                        "provider_kind": "gmail_api",
                        "is_simulated": False,
                    },
                },
            },
        }
        plan_info = {
            "email_manifest": {
                "subject": "[JACKVERSE INTERNAL TEST] Governed Gmail Transmission Verification",
                "message_id": rfc822_msg_id,
            }
        }

        with patch("sys.stdout", new=io.StringIO()) as fake_out:
            print_four_tier_report(dispatch_result, plan_info, "dagogograntjack@gmail.com")
            output = fake_out.getvalue()

        self.assertIn(f"Gmail Message ID:     {gmail_msg_id}", output)
        self.assertIn(f"Gmail Thread ID:      {gmail_thread_id}", output)
        self.assertIn(f"RFC 822 Message-ID:   {rfc822_msg_id}", output)
        self.assertIn("Provider Status:      GMAIL_API_ACCEPTED", output)
        self.assertIn("Provider Acceptance:  CONFIRMED (Accepted by Google Gmail API)", output)
        self.assertIn("Receipt ID:           rcpt-live-test-123", output)
        self.assertIn(f"External Reference:   {gmail_msg_id}", output)

    @patch("scripts.guarded_stage_c_dispatch._create_ssl_context")
    @patch("urllib.request.urlopen")
    def test_perform_guarded_dispatch_confirmed_completion(self, mock_urlopen, mock_ssl) -> None:
        """HTTP 200 with verified_success receipt returns CONFIRMED_COMPLETION."""
        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_resp.headers = {"ETag": '"action:act_91966280c188:v3"'}
        payload = {
            "case_status": "waiting_external",
            "receipt": {
                "receipt_id": "rcpt-1",
                "status": "verified_success",
            },
        }
        mock_resp.read.return_value = json.dumps(payload).encode("utf-8")
        mock_urlopen.return_value.__enter__.return_value = mock_resp

        res = perform_guarded_dispatch("https://localhost:3443", Path("dummy.crt"), "act_1", '"v1"', "tok")
        self.assertEqual(res["outcome"], "CONFIRMED_COMPLETION")
        self.assertEqual(res["http_code"], 200)

    @patch("scripts.guarded_stage_c_dispatch._create_ssl_context")
    @patch("urllib.request.urlopen")
    def test_perform_guarded_dispatch_incomplete_http_200(self, mock_urlopen, mock_ssl) -> None:
        """HTTP 200 lacking verified_success receipt returns AMBIGUOUS_OUTCOME."""
        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_resp.headers = {"ETag": '"action:act_91966280c188:v3"'}
        payload = {
            "case_status": "action_required",
            "receipt": {
                "receipt_id": "rcpt-1",
                "status": "ambiguous_submission",
            },
        }
        mock_resp.read.return_value = json.dumps(payload).encode("utf-8")
        mock_urlopen.return_value.__enter__.return_value = mock_resp

        res = perform_guarded_dispatch("https://localhost:3443", Path("dummy.crt"), "act_1", '"v1"', "tok")
        self.assertEqual(res["outcome"], "AMBIGUOUS_OUTCOME")
        self.assertEqual(res["http_code"], 200)

    @patch("scripts.guarded_stage_c_dispatch._create_ssl_context")
    @patch("urllib.request.urlopen")
    def test_perform_guarded_dispatch_unexpected_http_500(self, mock_urlopen, mock_ssl) -> None:
        """HTTP 500 returns AMBIGUOUS_OUTCOME and does not classify as pre-dispatch."""
        err_body = json.dumps({"detail": "Database lock timeout"}).encode("utf-8")
        mock_urlopen.side_effect = urllib.error.HTTPError(
            url="https://localhost:3443",
            code=500,
            msg="Internal Server Error",
            hdrs={},
            fp=io.BytesIO(err_body),
        )

        res = perform_guarded_dispatch("https://localhost:3443", Path("dummy.crt"), "act_1", '"v1"', "tok")
        self.assertEqual(res["outcome"], "AMBIGUOUS_OUTCOME")
        self.assertEqual(res["http_code"], 500)

    @patch("scripts.guarded_stage_c_dispatch._create_ssl_context")
    @patch("urllib.request.urlopen")
    def test_perform_guarded_dispatch_ambiguous_http_409(self, mock_urlopen, mock_ssl) -> None:
        """HTTP 409 with in-flight or ambiguous failure_kind returns AMBIGUOUS_OUTCOME."""
        ambiguous_kinds = ["manual_resolution_required", "execution_in_progress", "ambiguous_submission", "unknown_conflict"]
        for kind in ambiguous_kinds:
            err_body = json.dumps({"detail": f"Conflict: {kind}", "failure_kind": kind}).encode("utf-8")
            mock_urlopen.side_effect = urllib.error.HTTPError(
                url="https://localhost:3443",
                code=409,
                msg="Conflict",
                hdrs={},
                fp=io.BytesIO(err_body),
            )
            res = perform_guarded_dispatch("https://localhost:3443", Path("dummy.crt"), "act_1", '"v1"', "tok")
            self.assertEqual(res["outcome"], "AMBIGUOUS_OUTCOME", f"Failed for failure_kind: {kind}")
            self.assertEqual(res["http_code"], 409)

    @patch("scripts.guarded_stage_c_dispatch._create_ssl_context")
    @patch("urllib.request.urlopen")
    def test_perform_guarded_dispatch_proven_pre_dispatch_rejections(self, mock_urlopen, mock_ssl) -> None:
        """Proven pre-dispatch barrier rejections (401, 403, 404, 412, 428, and proven 409) return CONFIRMED_PRE_DISPATCH_REJECTION."""
        # 1. HTTP 409 with proven pre-dispatch failure kinds
        for kind in ["real_execution_disabled", "action_not_approved", "insecure_configuration_rejected"]:
            err_body = json.dumps({"detail": f"Rejected: {kind}", "failure_kind": kind}).encode("utf-8")
            mock_urlopen.side_effect = urllib.error.HTTPError(
                url="https://localhost:3443",
                code=409,
                msg="Conflict",
                hdrs={},
                fp=io.BytesIO(err_body),
            )
            res = perform_guarded_dispatch("https://localhost:3443", Path("dummy.crt"), "act_1", '"v1"', "tok")
            self.assertEqual(res["outcome"], "CONFIRMED_PRE_DISPATCH_REJECTION")
            self.assertEqual(res["http_code"], 409)

        # 2. HTTP 428 Precondition Required (e.g. missing If-Match)
        mock_urlopen.side_effect = urllib.error.HTTPError(
            url="https://localhost:3443",
            code=428,
            msg="Precondition Required",
            hdrs={},
            fp=io.BytesIO(b'{"detail":"If-Match required"}'),
        )
        res = perform_guarded_dispatch("https://localhost:3443", Path("dummy.crt"), "act_1", '"v1"', "tok")
        self.assertEqual(res["outcome"], "CONFIRMED_PRE_DISPATCH_REJECTION")
        self.assertEqual(res["http_code"], 428)

        # 3. HTTP 412 Precondition Failed (e.g. stale ETag)
        mock_urlopen.side_effect = urllib.error.HTTPError(
            url="https://localhost:3443",
            code=412,
            msg="Precondition Failed",
            hdrs={},
            fp=io.BytesIO(b'{"detail":"Stale ETag"}'),
        )
        res = perform_guarded_dispatch("https://localhost:3443", Path("dummy.crt"), "act_1", '"v1"', "tok")
        self.assertEqual(res["outcome"], "CONFIRMED_PRE_DISPATCH_REJECTION")
        self.assertEqual(res["http_code"], 412)

