"""Unit tests for Stage C guarded dispatch, replay idempotency, and transmission barrier enforcement."""

from __future__ import annotations

import unittest
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
