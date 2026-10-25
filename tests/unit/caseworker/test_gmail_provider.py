"""Unit tests for GmailApplicationExecutionProvider and RFC 2822 MIME compilation."""

from __future__ import annotations

import base64
import unittest
import httpx

from caseworker.adapters.gmail_provider import (
    GmailApplicationExecutionProvider,
    StaticOAuthCredentialProvider,
)
from caseworker.domain.email_mime import (
    serialize_mime_message,
    validate_email_header_field,
)
from caseworker.domain.enums import (
    ExecutionDestinationType,
    ExecutionErrorCategory,
    ExecutionProviderKind,
    ExecutionReceiptStatus,
    ExternalExecutionPlanStatus,
)
from caseworker.domain.errors import DomainValidationError
from caseworker.domain.execution_plan import (
    EmailAttachmentManifest,
    EmailPayloadManifest,
    ExecutionDestination,
    ExecutionFieldMapping,
    ExecutionUploadAsset,
    ExternalExecutionPlan,
    TargetSubmitControlSpec,
)
from caseworker.domain.package import PreparationPackage
from caseworker.domain.provider_adapter import ExecutionAttemptContext
from caseworker.domain.types import compute_sha256, compute_sha256_bytes


def _make_dummy_plan(
    plan_id: str = "plan-email-001",
    to_email: str = "jobs@acme.corp",
    from_email: str = "alice@example.com",
    subject: str = "Application: Staff Engineer - Alice",
    body_text: str = "Please find my application attached.",
    attachments: list[EmailAttachmentManifest] | None = None,
    sealed_payload_hash: str = "",
) -> ExternalExecutionPlan:
    dest = ExecutionDestination(
        destination_type=ExecutionDestinationType.EMAIL.value,
        destination_provenance="user_provided",
        canonical_url=f"mailto:{to_email}",
        organization="Acme Corp",
        channel_metadata={"email_subject": subject},
        is_exact_destination=True,
    )
    manifest = EmailPayloadManifest(
        to_email=to_email,
        from_email=from_email,
        subject=subject,
        message_id=f"<plan-{plan_id}@acme.corp>",
        date_rfc2822="Sun, 25 Oct 2026 10:00:00 GMT",
        body_text=body_text,
        mime_boundary=f"==_JackVerse_MIME_Part_{plan_id}_==",
        attachments=attachments or [],
        sealed_payload_hash=sealed_payload_hash,
    )
    return ExternalExecutionPlan(
        case_id="case-001",
        opportunity_id="opp-001",
        package_id="pkg-001",
        package_fingerprint="pkg-fp-001",
        user_id="user_alice",
        destination=dest,
        destination_provenance="user_provided",
        status=ExternalExecutionPlanStatus.READY_FOR_AUTHORIZATION,
        target_schema=None,
        target_schema_hash="",
        field_mappings=[
            ExecutionFieldMapping(
                target_field_id="applicant_email",
                field_name="Applicant Email",
                field_value=from_email,
                source_fact_id="fact-1",
                source_fact_version=1,
                verification_status="user_verified",
            ),
        ],
        upload_mappings=[],
        screening_responses=[],
        final_submit_control=TargetSubmitControlSpec(
            control_type="email_send",
            semantic_target="Send Governed Application Email",
            method="GMAIL_API_SEND",
            selector="gmail:users.messages.send",
        ),
        findings=[],
        execution_input_hash="input-hash-001",
        plan_id=plan_id,
        email_manifest=manifest,
    )


class TestGmailProvider(unittest.TestCase):
    """Test suite verifying RFC 2822 MIME compilation, exact byte sealing, and Gmail API adapter."""

    def test_mime_compilation_and_exact_byte_reproducibility(self) -> None:
        """Calling serialize_mime_message multiple times produces identical bytes and hashes."""
        raw_cv_bytes = b"%PDF-1.4 Mock CV Content Alice"
        cv_hash = compute_sha256_bytes(raw_cv_bytes)
        att = EmailAttachmentManifest(
            filename="cv_alice.pdf",
            mime_type="application/pdf",
            content_sha256=cv_hash,
            byte_size=len(raw_cv_bytes),
            asset_id="asset-cv-1",
        )
        manifest = EmailPayloadManifest(
            to_email="jobs@example.com",
            from_email="alice@example.com",
            subject="Job Application: Software Engineer",
            message_id="<plan-123@example.com>",
            date_rfc2822="Sun, 25 Oct 2026 10:00:00 GMT",
            body_text="Dear Team,\n\nPlease see attached CV.",
            mime_boundary="==_Fixed_Boundary_123_==",
            attachments=[att],
        )
        upload_assets = [
            ExecutionUploadAsset(
                asset_id="asset-cv-1",
                role="cv",
                source_artifact_id="art-1",
                source_artifact_version=1,
                filename="cv_alice.pdf",
                mime_type="application/pdf",
                content_sha256=cv_hash,
                byte_size=len(raw_cv_bytes),
                materialized=True,
                content_bytes=raw_cv_bytes,
            )
        ]

        bytes_1 = serialize_mime_message(manifest, upload_assets)
        bytes_2 = serialize_mime_message(manifest, upload_assets)

        self.assertEqual(bytes_1, bytes_2)
        self.assertEqual(compute_sha256_bytes(bytes_1), compute_sha256_bytes(bytes_2))
        self.assertIn(b"jobs@example.com", bytes_1)
        self.assertIn(b"alice@example.com", bytes_1)
        self.assertIn(b"==_Fixed_Boundary_123_==", bytes_1)
        self.assertIn(base64.b64encode(raw_cv_bytes), bytes_1)

    def test_crlf_header_injection_rejection(self) -> None:
        """Header fields containing CRLF or null bytes are rejected with DomainValidationError."""
        with self.assertRaises(DomainValidationError) as ctx:
            validate_email_header_field("Subject", "Application\r\nBcc: evil@attacker.com")
        self.assertIn("Header injection detected", str(ctx.exception))

        with self.assertRaises(DomainValidationError) as ctx:
            validate_email_header_field("To", "user@example.com\nCc: victim@example.com")
        self.assertIn("Header injection detected", str(ctx.exception))

        with self.assertRaises(DomainValidationError):
            validate_email_header_field("Subject", "")

    def test_fail_closed_when_flags_disabled(self) -> None:
        """execute_irreversible_submit fails closed without dispatch when feature flags are false."""
        plan = _make_dummy_plan()
        context = ExecutionAttemptContext(
            attempt_id="att-1",
            lease_token="lease-1",
            fencing_token=1,
            user_id="user_alice",
            case_id="case-1",
            plan_id=plan.plan_id,
            action_id="act-1",
            sealed_payload_bytes=b"sample_bytes",
        )
        provider = GmailApplicationExecutionProvider(
            real_execution_enabled=False,
            gmail_execution_enabled=False,
        )
        res = provider.execute_irreversible_submit(plan, "att-1", context=context)
        self.assertEqual(res.status, ExecutionReceiptStatus.CONFIRMED_FAILED)
        self.assertEqual(res.error_category, ExecutionErrorCategory.SUBMIT_REJECTED)
        self.assertIn("disabled by configuration", res.error_message or "")

    def test_oauth_sender_mismatch_rejection(self) -> None:
        """stage_submission fails closed when authenticated Google identity doesn't match from_email."""
        raw_bytes = b"sample_bytes"
        sealed_hash = compute_sha256_bytes(raw_bytes)
        plan = _make_dummy_plan(from_email="alice@example.com", sealed_payload_hash=sealed_hash)
        package = PreparationPackage(
            case_id="case-001",
            artifact_set_id="artset-1",
            source_report_id="rep-1",
            user_id="user_alice",
            artifact_ids=["art-1"],
            artifact_versions={"art-1": 1},
            artifact_snapshots=[],
            claim_snapshots=[],
            rendered_payload_hashes={"art-1": "hash"},
            package_id="pkg-001",
        )
        # Auth account is bob, but plan is alice
        cred = StaticOAuthCredentialProvider(
            access_token="valid-token-bob",
            authenticated_email="bob@example.com",
        )
        provider = GmailApplicationExecutionProvider(credential_provider=cred)

        staging = provider.stage_submission(plan, package, [], context=None)
        self.assertFalse(staging.success)
        self.assertEqual(staging.error_category, ExecutionErrorCategory.PROVIDER_AUTH_ERROR)
        self.assertIn("does not match authenticated Google account identity", staging.error_message or "")

    def test_staged_payload_tampering_rejected_at_barrier_b(self) -> None:
        """Irreversible submit rejects execution if staged bytes do not match authorized manifest hash."""
        original_bytes = b"Authorized bytes"
        original_hash = compute_sha256_bytes(original_bytes)
        plan = _make_dummy_plan(sealed_payload_hash=original_hash)

        # Context has tampered bytes
        tampered_bytes = b"Tampered bytes"
        context = ExecutionAttemptContext(
            attempt_id="att-1",
            lease_token="lease-1",
            fencing_token=1,
            user_id="user_alice",
            case_id="case-1",
            plan_id=plan.plan_id,
            action_id="act-1",
            sealed_payload_bytes=tampered_bytes,
            sealed_payload_hash=compute_sha256_bytes(tampered_bytes),
        )

        cred = StaticOAuthCredentialProvider("valid-tok", "alice@example.com")
        provider = GmailApplicationExecutionProvider(
            credential_provider=cred,
            real_execution_enabled=True,
            gmail_execution_enabled=True,
        )
        res = provider.execute_irreversible_submit(plan, "att-1", context=context)
        self.assertEqual(res.status, ExecutionReceiptStatus.CONFIRMED_FAILED)
        self.assertEqual(res.error_category, ExecutionErrorCategory.PAYLOAD_INTEGRITY_MISMATCH)
        self.assertIn("Barrier B failure", res.error_message or "")

    def test_mocked_gmail_api_success_200(self) -> None:
        """200 OK from Gmail API yields VERIFIED_SUCCESS with GMAIL_API_ACCEPTED metadata."""
        plan = _make_dummy_plan()
        sealed_bytes = serialize_mime_message(plan.email_manifest, [])
        sealed_hash = compute_sha256_bytes(sealed_bytes)
        # Update plan with sealed hash
        object.__setattr__(plan.email_manifest, "sealed_payload_hash", sealed_hash)

        context = ExecutionAttemptContext(
            attempt_id="att-1",
            lease_token="lease-1",
            fencing_token=1,
            user_id="user_alice",
            case_id="case-1",
            plan_id=plan.plan_id,
            action_id="act-1",
            sealed_payload_bytes=sealed_bytes,
            sealed_payload_hash=sealed_hash,
        )

        def mock_handler(request: httpx.Request) -> httpx.Response:
            self.assertEqual(request.method, "POST")
            self.assertIn("users/me/messages/send", str(request.url))
            self.assertEqual(request.headers.get("authorization"), "Bearer token-12345")
            return httpx.Response(
                200,
                json={"id": "msg-gmail-abc-999", "threadId": "th-gmail-xyz-888"},
            )

        client = httpx.Client(transport=httpx.MockTransport(mock_handler))
        cred = StaticOAuthCredentialProvider("token-12345", "alice@example.com")
        provider = GmailApplicationExecutionProvider(
            credential_provider=cred,
            http_client=client,
            real_execution_enabled=True,
            gmail_execution_enabled=True,
        )

        res = provider.execute_irreversible_submit(plan, "att-1", context=context)
        self.assertEqual(res.status, ExecutionReceiptStatus.VERIFIED_SUCCESS)
        self.assertEqual(res.external_confirmation_id, "msg-gmail-abc-999")
        self.assertEqual(res.provider_metadata.get("provider_status"), "GMAIL_API_ACCEPTED")
        self.assertEqual(res.provider_metadata.get("gmail_message_id"), "msg-gmail-abc-999")
        self.assertEqual(res.provider_metadata.get("gmail_thread_id"), "th-gmail-xyz-888")

    def test_mocked_gmail_api_auth_error_401(self) -> None:
        """401 Unauthorized from Gmail API maps to CONFIRMED_FAILED with PROVIDER_AUTH_ERROR."""
        plan = _make_dummy_plan()
        sealed_bytes = serialize_mime_message(plan.email_manifest, [])
        sealed_hash = compute_sha256_bytes(sealed_bytes)
        object.__setattr__(plan.email_manifest, "sealed_payload_hash", sealed_hash)

        context = ExecutionAttemptContext(
            attempt_id="att-1",
            lease_token="lease-1",
            fencing_token=1,
            user_id="user_alice",
            case_id="case-1",
            plan_id=plan.plan_id,
            action_id="act-1",
            sealed_payload_bytes=sealed_bytes,
            sealed_payload_hash=sealed_hash,
        )

        def mock_handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(401, text="Invalid Credentials")

        client = httpx.Client(transport=httpx.MockTransport(mock_handler))
        cred = StaticOAuthCredentialProvider("expired-tok", "alice@example.com")
        provider = GmailApplicationExecutionProvider(
            credential_provider=cred,
            http_client=client,
            real_execution_enabled=True,
            gmail_execution_enabled=True,
        )

        res = provider.execute_irreversible_submit(plan, "att-1", context=context)
        self.assertEqual(res.status, ExecutionReceiptStatus.CONFIRMED_FAILED)
        self.assertEqual(res.error_category, ExecutionErrorCategory.PROVIDER_AUTH_ERROR)

    def test_mocked_gmail_api_rate_limit_429(self) -> None:
        """429 Quota Exceeded from Gmail API maps to CONFIRMED_FAILED with SUBMIT_REJECTED."""
        plan = _make_dummy_plan()
        sealed_bytes = serialize_mime_message(plan.email_manifest, [])
        sealed_hash = compute_sha256_bytes(sealed_bytes)
        object.__setattr__(plan.email_manifest, "sealed_payload_hash", sealed_hash)

        context = ExecutionAttemptContext(
            attempt_id="att-1",
            lease_token="lease-1",
            fencing_token=1,
            user_id="user_alice",
            case_id="case-1",
            plan_id=plan.plan_id,
            action_id="act-1",
            sealed_payload_bytes=sealed_bytes,
            sealed_payload_hash=sealed_hash,
        )

        def mock_handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(429, text="Resource has been exhausted (quota limit).")

        client = httpx.Client(transport=httpx.MockTransport(mock_handler))
        cred = StaticOAuthCredentialProvider("valid-tok", "alice@example.com")
        provider = GmailApplicationExecutionProvider(
            credential_provider=cred,
            http_client=client,
            real_execution_enabled=True,
            gmail_execution_enabled=True,
        )

        res = provider.execute_irreversible_submit(plan, "att-1", context=context)
        self.assertEqual(res.status, ExecutionReceiptStatus.CONFIRMED_FAILED)
        self.assertEqual(res.error_category, ExecutionErrorCategory.SUBMIT_REJECTED)

    def test_mocked_gmail_api_timeout_504_produces_ambiguous(self) -> None:
        """504 Gateway Timeout or Network Timeout yields AMBIGUOUS_SUBMISSION and SUBMIT_TIMEOUT."""
        plan = _make_dummy_plan()
        sealed_bytes = serialize_mime_message(plan.email_manifest, [])
        sealed_hash = compute_sha256_bytes(sealed_bytes)
        object.__setattr__(plan.email_manifest, "sealed_payload_hash", sealed_hash)

        context = ExecutionAttemptContext(
            attempt_id="att-1",
            lease_token="lease-1",
            fencing_token=1,
            user_id="user_alice",
            case_id="case-1",
            plan_id=plan.plan_id,
            action_id="act-1",
            sealed_payload_bytes=sealed_bytes,
            sealed_payload_hash=sealed_hash,
        )

        def mock_handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ReadTimeout("Connection timed out after 30 seconds.")

        client = httpx.Client(transport=httpx.MockTransport(mock_handler))
        cred = StaticOAuthCredentialProvider("valid-tok", "alice@example.com")
        provider = GmailApplicationExecutionProvider(
            credential_provider=cred,
            http_client=client,
            real_execution_enabled=True,
            gmail_execution_enabled=True,
        )

        res = provider.execute_irreversible_submit(plan, "att-1", context=context)
        self.assertEqual(res.status, ExecutionReceiptStatus.AMBIGUOUS_SUBMISSION)
        self.assertEqual(res.error_category, ExecutionErrorCategory.SUBMIT_TIMEOUT)
        self.assertIn("ReadTimeout", res.error_message or "")


if __name__ == "__main__":
    unittest.main()
