"""Verified real provider adapter for governed email application submission via the official Gmail API."""

from __future__ import annotations

from abc import ABC, abstractmethod
import base64
import time
from typing import Any
import httpx

from caseworker.domain.email_mime import serialize_mime_message
from caseworker.domain.enums import (
    ExecutionDestinationType,
    ExecutionErrorCategory,
    ExecutionProviderKind,
    ExecutionReceiptStatus,
)
from caseworker.domain.execution_plan import (
    ExecutionUploadAsset,
    ExternalExecutionPlan,
)
from caseworker.domain.package import PreparationPackage
from caseworker.domain.provider_adapter import (
    ExecutionAttemptContext,
    ExecutionProviderAdapter,
    ProviderSubmissionResult,
    StagingOutcome,
)
from caseworker.domain.types import compute_sha256_bytes

GMAIL_SEND_URL = "https://gmail.googleapis.com/gmail/v1/users/me/messages/send"
GMAIL_USERINFO_URL = "https://www.googleapis.com/oauth2/v2/userinfo"


class OAuthCredentialProvider(ABC):
    """Abstract interface for managing OAuth2 credentials and identity verification."""

    @abstractmethod
    def get_access_token(self, user_id: str) -> str:
        """Return an active OAuth2 access token for the given user."""
        ...

    @abstractmethod
    def get_authenticated_email(self, user_id: str) -> str:
        """Return the primary email address bound to this OAuth account."""
        ...


class StaticOAuthCredentialProvider(OAuthCredentialProvider):
    """Static credential provider for tests and explicit environment bindings."""

    def __init__(self, access_token: str, authenticated_email: str) -> None:
        self._access_token = access_token
        self._authenticated_email = authenticated_email

    def get_access_token(self, user_id: str) -> str:
        return self._access_token

    def get_authenticated_email(self, user_id: str) -> str:
        return self._authenticated_email


class GmailApplicationExecutionProvider(ExecutionProviderAdapter):
    """Governed real provider adapter for submitting job applications via Google Gmail API.

    Guarantees:
    - Never modifies or generates Date, Message-ID, or MIME boundaries post-authorization.
    - Barrier B byte-for-byte verification of sealed payload against authorized plan hash.
    - Zero live network requests when feature flags are disabled (fail-closed).
    - Granular error classification: 200 = GMAIL_API_ACCEPTED, 401 = PROVIDER_AUTH_ERROR,
      408/5xx/timeout = AMBIGUOUS_SUBMISSION.
    - Zero transport-level retries on irreversible send calls.
    """

    def __init__(
        self,
        credential_provider: OAuthCredentialProvider | None = None,
        http_client: httpx.Client | None = None,
        real_execution_enabled: bool = False,
        gmail_execution_enabled: bool = False,
    ) -> None:
        self.credential_provider = credential_provider
        self.http_client = http_client
        self.real_execution_enabled = real_execution_enabled
        self.gmail_execution_enabled = gmail_execution_enabled

    @property
    def provider_kind(self) -> ExecutionProviderKind:
        return ExecutionProviderKind.GMAIL_API

    def stage_submission(
        self,
        plan: ExternalExecutionPlan,
        package: PreparationPackage,
        upload_assets: list[ExecutionUploadAsset],
        context: ExecutionAttemptContext | None = None,
    ) -> StagingOutcome:
        """Stage materials for email submission up to the Last Reversible Point.

        Verifies:
        1. Destination type is EMAIL.
        2. Plan has an existing EmailPayloadManifest.
        3. Sender email matches the authenticated Google account identity.
        4. Sealed MIME bytes match the authorized manifest hash exactly.
        5. Populates context with sealed payload bytes and hash.
        """
        dest_val = (
            plan.destination.destination_type.value
            if hasattr(plan.destination.destination_type, "value")
            else str(plan.destination.destination_type)
        )
        if dest_val != ExecutionDestinationType.EMAIL.value:
            return StagingOutcome(
                success=False,
                error_category=ExecutionErrorCategory.UNSUPPORTED_DESTINATION,
                error_message=f"GmailApplicationExecutionProvider only supports EMAIL destinations (got '{dest_val}').",
                submit_control_ready=False,
            )

        if plan.email_manifest is None:
            return StagingOutcome(
                success=False,
                error_category=ExecutionErrorCategory.PRE_SUBMIT_CONTRACT_VIOLATION,
                error_message="Plan does not contain an EmailPayloadManifest required for email execution.",
                submit_control_ready=False,
            )

        manifest = plan.email_manifest

        # Verify authenticated identity matches sender
        if self.credential_provider is None:
            return StagingOutcome(
                success=False,
                error_category=ExecutionErrorCategory.PROVIDER_AUTH_ERROR,
                error_message="OAuth credential provider is not configured for Gmail execution.",
                submit_control_ready=False,
            )

        try:
            auth_email = self.credential_provider.get_authenticated_email(plan.user_id)
        except Exception as exc:
            return StagingOutcome(
                success=False,
                error_category=ExecutionErrorCategory.PROVIDER_AUTH_ERROR,
                error_message=f"Failed to verify authenticated Google account identity: {exc}",
                submit_control_ready=False,
            )

        expected_from = manifest.from_email.strip().lower()
        if not auth_email or auth_email.strip().lower() != expected_from:
            return StagingOutcome(
                success=False,
                error_category=ExecutionErrorCategory.PROVIDER_AUTH_ERROR,
                error_message=(
                    f"Sender email '{manifest.from_email}' does not match authenticated "
                    f"Google account identity '{auth_email}'."
                ),
                submit_control_ready=False,
            )

        # Recompile exact MIME bytes and verify integrity
        try:
            mime_bytes = serialize_mime_message(manifest, upload_assets)
        except Exception as exc:
            return StagingOutcome(
                success=False,
                error_category=ExecutionErrorCategory.STAGING_VALIDATION_FAILED,
                error_message=f"Failed to compile RFC 2822 MIME message during staging: {exc}",
                submit_control_ready=False,
            )

        computed_hash = compute_sha256_bytes(mime_bytes)
        if computed_hash != manifest.sealed_payload_hash:
            return StagingOutcome(
                success=False,
                error_category=ExecutionErrorCategory.PAYLOAD_INTEGRITY_MISMATCH,
                error_message=(
                    f"Staged MIME payload hash '{computed_hash}' does not match "
                    f"authorized manifest hash '{manifest.sealed_payload_hash}'."
                ),
                submit_control_ready=False,
            )

        # Populate attempt context with verified bytes
        if context is not None:
            context.sealed_payload_bytes = mime_bytes
            context.sealed_payload_hash = manifest.sealed_payload_hash

        field_keys = [m.target_field_id for m in plan.field_mappings]
        upload_slot_ids = [u.asset_id for u in upload_assets]
        screening_ids = [q.question_id for q in plan.screening_responses]

        return StagingOutcome(
            success=True,
            staged_field_count=len(field_keys) + len(screening_ids),
            staged_upload_count=len(upload_assets),
            submit_control_ready=True,
            staged_field_keys=field_keys,
            staged_upload_slot_ids=upload_slot_ids,
            staged_screening_question_ids=screening_ids,
            sealed_payload_hash=manifest.sealed_payload_hash,
            sealed_payload_bytes=mime_bytes,
        )

    def execute_irreversible_submit(
        self,
        plan: ExternalExecutionPlan,
        attempt_id: str,
        context: ExecutionAttemptContext | None = None,
    ) -> ProviderSubmissionResult:
        """Execute irreversible send via official Gmail API.

        Guarantees:
        - Fails closed if feature flags are disabled.
        - Verifies context sealed payload bytes match authorized plan hash.
        - Dispatches raw bytes via POST users/me/messages/send without retries.
        - Correctly maps HTTP status codes into granular receipt statuses.
        """
        # 1. Feature Flag Guard (Hardening Amendment F)
        if not self.real_execution_enabled or not self.gmail_execution_enabled:
            return ProviderSubmissionResult(
                status=ExecutionReceiptStatus.CONFIRMED_FAILED,
                external_confirmation_id=None,
                error_category=ExecutionErrorCategory.SUBMIT_REJECTED,
                error_message=(
                    "Real Gmail execution is disabled by configuration "
                    "(CASEWORKER_REAL_EXECUTION_ENABLED=false or CASEWORKER_GMAIL_EXECUTION_ENABLED=false)."
                ),
                provider_metadata={
                    "is_simulated": False,
                    "provider_kind": ExecutionProviderKind.GMAIL_API.value,
                    "real_execution_enabled": self.real_execution_enabled,
                    "gmail_execution_enabled": self.gmail_execution_enabled,
                },
            )

        # 2. Plan Manifest Guard
        if plan.email_manifest is None:
            return ProviderSubmissionResult(
                status=ExecutionReceiptStatus.CONFIRMED_FAILED,
                external_confirmation_id=None,
                error_category=ExecutionErrorCategory.PRE_SUBMIT_CONTRACT_VIOLATION,
                error_message="Plan does not contain an EmailPayloadManifest.",
                provider_metadata={"provider_kind": ExecutionProviderKind.GMAIL_API.value},
            )

        # 3. Barrier B Payload Integrity Guard (Hardening Amendment A)
        if context is None or context.sealed_payload_bytes is None:
            return ProviderSubmissionResult(
                status=ExecutionReceiptStatus.CONFIRMED_FAILED,
                external_confirmation_id=None,
                error_category=ExecutionErrorCategory.PAYLOAD_INTEGRITY_MISMATCH,
                error_message="Barrier B failure: execution context is missing staged sealed payload bytes.",
                provider_metadata={"provider_kind": ExecutionProviderKind.GMAIL_API.value},
            )

        computed_hash = compute_sha256_bytes(context.sealed_payload_bytes)
        if computed_hash != plan.email_manifest.sealed_payload_hash:
            return ProviderSubmissionResult(
                status=ExecutionReceiptStatus.CONFIRMED_FAILED,
                external_confirmation_id=None,
                error_category=ExecutionErrorCategory.PAYLOAD_INTEGRITY_MISMATCH,
                error_message=(
                    f"Barrier B failure: Staged payload bytes hash '{computed_hash}' does not match "
                    f"authorized plan hash '{plan.email_manifest.sealed_payload_hash}'."
                ),
                provider_metadata={"provider_kind": ExecutionProviderKind.GMAIL_API.value},
            )

        # 4. OAuth Credential Resolution
        if self.credential_provider is None:
            return ProviderSubmissionResult(
                status=ExecutionReceiptStatus.CONFIRMED_FAILED,
                external_confirmation_id=None,
                error_category=ExecutionErrorCategory.PROVIDER_AUTH_ERROR,
                error_message="OAuth credential provider is not configured.",
                provider_metadata={"provider_kind": ExecutionProviderKind.GMAIL_API.value},
            )

        try:
            token = self.credential_provider.get_access_token(plan.user_id)
        except Exception as exc:
            return ProviderSubmissionResult(
                status=ExecutionReceiptStatus.CONFIRMED_FAILED,
                external_confirmation_id=None,
                error_category=ExecutionErrorCategory.PROVIDER_AUTH_ERROR,
                error_message=f"Failed to obtain OAuth access token: {exc}",
                provider_metadata={"provider_kind": ExecutionProviderKind.GMAIL_API.value},
            )

        # 5. Base64url encode sealed payload bytes for Gmail REST API
        raw_b64 = base64.urlsafe_b64encode(context.sealed_payload_bytes).decode("ascii")
        request_body = {"raw": raw_b64}
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

        # 6. Outbound Dispatch (Zero transport retries on send)
        client = self.http_client or httpx.Client(timeout=30.0)
        close_client = self.http_client is None
        start_time = time.monotonic()

        try:
            resp = client.post(
                GMAIL_SEND_URL,
                json=request_body,
                headers=headers,
            )
            duration_ms = int((time.monotonic() - start_time) * 1000)
        except (httpx.TimeoutException, httpx.NetworkError) as net_err:
            duration_ms = int((time.monotonic() - start_time) * 1000)
            return ProviderSubmissionResult(
                status=ExecutionReceiptStatus.AMBIGUOUS_SUBMISSION,
                external_confirmation_id=None,
                error_category=ExecutionErrorCategory.SUBMIT_TIMEOUT,
                error_message=f"Network error/timeout communicating with Gmail API: {net_err.__class__.__name__}",
                raw_response_snippet=str(net_err)[:200],
                provider_metadata={
                    "duration_ms": duration_ms,
                    "error_type": net_err.__class__.__name__,
                    "provider_kind": ExecutionProviderKind.GMAIL_API.value,
                    "is_simulated": False,
                },
            )
        except Exception as unk_err:
            duration_ms = int((time.monotonic() - start_time) * 1000)
            return ProviderSubmissionResult(
                status=ExecutionReceiptStatus.AMBIGUOUS_SUBMISSION,
                external_confirmation_id=None,
                error_category=ExecutionErrorCategory.SUBMIT_TIMEOUT,
                error_message=f"Unexpected transport failure communicating with Gmail API: {unk_err}",
                raw_response_snippet=str(unk_err)[:200],
                provider_metadata={
                    "duration_ms": duration_ms,
                    "provider_kind": ExecutionProviderKind.GMAIL_API.value,
                    "is_simulated": False,
                },
            )
        finally:
            if close_client:
                client.close()

        # 7. Granular Status Code Classification (Amendment E)
        status_code = resp.status_code
        snippet = resp.text[:300] if resp.text else ""

        if status_code == 200:
            try:
                body = resp.json()
            except Exception:
                body = {}
            gmail_msg_id = body.get("id") or ""
            gmail_thread_id = body.get("threadId") or ""

            return ProviderSubmissionResult(
                status=ExecutionReceiptStatus.VERIFIED_SUCCESS,
                external_confirmation_id=gmail_msg_id or plan.email_manifest.message_id,
                raw_response_snippet=snippet,
                provider_metadata={
                    "http_status": 200,
                    "duration_ms": duration_ms,
                    "provider_status": "GMAIL_API_ACCEPTED",
                    "response_category": "success",
                    "gmail_message_id": gmail_msg_id,
                    "gmail_thread_id": gmail_thread_id,
                    "rfc822_message_id": plan.email_manifest.message_id,
                    "sealed_payload_hash": plan.email_manifest.sealed_payload_hash,
                    "recipient_email": plan.email_manifest.to_email,
                    "sender_email": plan.email_manifest.from_email,
                    "subject": plan.email_manifest.subject,
                    "provider_kind": ExecutionProviderKind.GMAIL_API.value,
                    "is_simulated": False,
                },
            )

        if status_code == 400:
            return ProviderSubmissionResult(
                status=ExecutionReceiptStatus.CONFIRMED_FAILED,
                external_confirmation_id=None,
                error_category=ExecutionErrorCategory.SUBMIT_REJECTED,
                error_message=f"Gmail API rejected submission payload (HTTP 400): {snippet}",
                raw_response_snippet=snippet,
                provider_metadata={
                    "http_status": 400,
                    "duration_ms": duration_ms,
                    "provider_kind": ExecutionProviderKind.GMAIL_API.value,
                    "is_simulated": False,
                },
            )

        if status_code == 401:
            return ProviderSubmissionResult(
                status=ExecutionReceiptStatus.CONFIRMED_FAILED,
                external_confirmation_id=None,
                error_category=ExecutionErrorCategory.PROVIDER_AUTH_ERROR,
                error_message="Gmail API authentication failed (HTTP 401): invalid or expired token.",
                raw_response_snippet=snippet,
                provider_metadata={
                    "http_status": 401,
                    "duration_ms": duration_ms,
                    "provider_kind": ExecutionProviderKind.GMAIL_API.value,
                    "is_simulated": False,
                },
            )

        if status_code in (403, 429):
            return ProviderSubmissionResult(
                status=ExecutionReceiptStatus.CONFIRMED_FAILED,
                external_confirmation_id=None,
                error_category=ExecutionErrorCategory.SUBMIT_REJECTED,
                error_message=f"Gmail API quota exceeded or forbidden (HTTP {status_code}).",
                raw_response_snippet=snippet,
                provider_metadata={
                    "http_status": status_code,
                    "duration_ms": duration_ms,
                    "provider_kind": ExecutionProviderKind.GMAIL_API.value,
                    "is_simulated": False,
                },
            )

        if status_code in (408, 500, 502, 503, 504):
            return ProviderSubmissionResult(
                status=ExecutionReceiptStatus.AMBIGUOUS_SUBMISSION,
                external_confirmation_id=None,
                error_category=ExecutionErrorCategory.SUBMIT_TIMEOUT,
                error_message=f"Gmail API upstream error or timeout (HTTP {status_code}). Irreversible state ambiguous.",
                raw_response_snippet=snippet,
                provider_metadata={
                    "http_status": status_code,
                    "duration_ms": duration_ms,
                    "provider_kind": ExecutionProviderKind.GMAIL_API.value,
                    "is_simulated": False,
                },
            )

        # Default fallback for other unexpected non-200 responses
        return ProviderSubmissionResult(
            status=ExecutionReceiptStatus.CONFIRMED_FAILED,
            external_confirmation_id=None,
            error_category=ExecutionErrorCategory.SUBMIT_REJECTED,
            error_message=f"Gmail API returned unexpected HTTP status {status_code}.",
            raw_response_snippet=snippet,
            provider_metadata={
                "http_status": status_code,
                "duration_ms": duration_ms,
                "provider_kind": ExecutionProviderKind.GMAIL_API.value,
                "is_simulated": False,
            },
        )
