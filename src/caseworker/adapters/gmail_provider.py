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
from datetime import datetime, timedelta, timezone
from caseworker.domain.errors import ProviderAuthError
from caseworker.adapters.crypto.token_cipher import TokenCipher
from caseworker.domain.types import compute_sha256_bytes, now_utc
from caseworker.persistence.sqlite import OAuthCredentialRecord, SQLiteCaseworkerStorage

GMAIL_SEND_URL = "https://gmail.googleapis.com/gmail/v1/users/me/messages/send"
GMAIL_USERINFO_URL = "https://www.googleapis.com/oauth2/v3/userinfo"
GMAIL_REVOKE_URL = "https://oauth2.googleapis.com/revoke"


class OAuthCredentialProvider(ABC):
    """Abstract interface for managing OAuth2 credentials and identity verification."""

    @abstractmethod
    def is_connected(self, user_id: str) -> bool:
        """Return True if an OAuth account connection exists for this user."""
        ...

    @abstractmethod
    def get_access_token(self, user_id: str) -> str:
        """Return an active OAuth2 access token for the given user, refreshing if needed."""
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

    def is_connected(self, user_id: str) -> bool:
        return bool(self._access_token and self._authenticated_email)

    def get_access_token(self, user_id: str) -> str:
        if not self._access_token:
            raise ProviderAuthError("No static OAuth access token configured.")
        return self._access_token

    def get_authenticated_email(self, user_id: str) -> str:
        if not self._authenticated_email:
            raise ProviderAuthError("No static authenticated email configured.")
        return self._authenticated_email


class DatabaseOAuthCredentialProvider(OAuthCredentialProvider):
    """Encrypted database-backed OAuth credential provider supporting automatic token refresh."""

    def __init__(
        self,
        storage: SQLiteCaseworkerStorage,
        cipher: TokenCipher,
        client_id: str = "",
        client_secret: str = "",
        token_uri: str = "https://oauth2.googleapis.com/token",
        http_client: httpx.Client | None = None,
    ) -> None:
        self.storage = storage
        self.cipher = cipher
        self.client_id = client_id
        self.client_secret = client_secret
        self.token_uri = token_uri
        self.http_client = http_client

    def is_connected(self, user_id: str) -> bool:
        with self.storage.unit_of_work() as uow:
            rec = uow.oauth_credentials.get_by_user_and_provider(user_id, "gmail")
            return rec is not None

    def get_authenticated_email(self, user_id: str) -> str:
        with self.storage.unit_of_work() as uow:
            rec = uow.oauth_credentials.get_by_user_and_provider(user_id, "gmail")
            if not rec:
                raise ProviderAuthError(f"No Google OAuth connection found for user '{user_id}'.")
            return rec.authenticated_email

    def get_access_token(self, user_id: str) -> str:
        with self.storage.unit_of_work() as uow:
            rec = uow.oauth_credentials.get_by_user_and_provider(user_id, "gmail")
            if not rec:
                raise ProviderAuthError(f"No Google OAuth connection found for user '{user_id}'.")

            # Check if token is expired or within 60 seconds of expiry
            is_expired = False
            try:
                exp_dt = datetime.fromisoformat(rec.token_expiry.replace("Z", "+00:00"))
                if exp_dt.tzinfo is None:
                    exp_dt = exp_dt.replace(tzinfo=timezone.utc)
                if (exp_dt - datetime.now(timezone.utc)).total_seconds() < 60:
                    is_expired = True
            except Exception:
                is_expired = True

            if not is_expired:
                return self.cipher.decrypt(rec.access_token_enc)

            # Access token expired: refresh token required
            if not rec.refresh_token_enc:
                raise ProviderAuthError("Google OAuth access token expired and no refresh token is stored. Please reconnect.")

            refresh_token = self.cipher.decrypt(rec.refresh_token_enc)
            new_access_token, new_refresh_token, new_expiry, granted_scopes = self._refresh_token(refresh_token)

            # Preserve existing refresh token if Google omitted replacement
            saved_refresh_enc = (
                self.cipher.encrypt(new_refresh_token)
                if new_refresh_token
                else rec.refresh_token_enc
            )

            rec.access_token_enc = self.cipher.encrypt(new_access_token)
            rec.refresh_token_enc = saved_refresh_enc
            rec.token_expiry = new_expiry
            if granted_scopes:
                rec.scopes = granted_scopes
            rec.updated_at = now_utc().isoformat().replace("+00:00", "Z")

            uow.oauth_credentials.save(rec)
            uow.commit()

            return new_access_token

    def _refresh_token(self, refresh_token: str) -> tuple[str, str | None, str, list[str]]:
        client = self.http_client or httpx.Client(timeout=15.0)
        close_client = self.http_client is None
        try:
            resp = client.post(
                self.token_uri,
                data={
                    "client_id": self.client_id,
                    "client_secret": self.client_secret,
                    "refresh_token": refresh_token,
                    "grant_type": "refresh_token",
                },
                headers={"Accept": "application/json"},
            )
        except Exception as exc:
            raise ProviderAuthError(f"Failed to communicate with Google token endpoint during refresh: {exc}")
        finally:
            if close_client:
                client.close()

        if resp.status_code != 200:
            raise ProviderAuthError(f"Google token refresh failed (HTTP {resp.status_code}): {resp.text[:200]}")

        try:
            data = resp.json()
        except Exception:
            raise ProviderAuthError("Google token refresh returned non-JSON response body.")

        new_access = data.get("access_token")
        if not new_access:
            raise ProviderAuthError("Google token refresh response missing 'access_token'.")

        new_refresh = data.get("refresh_token")  # May be None
        expires_in = int(data.get("expires_in", 3600))
        new_expiry_dt = datetime.now(timezone.utc) + timedelta(seconds=expires_in)
        new_expiry_iso = new_expiry_dt.isoformat().replace("+00:00", "Z")
        scopes = [s for s in data.get("scope", "").split(" ") if s]

        return new_access, new_refresh, new_expiry_iso, scopes

    def revoke_and_disconnect(self, user_id: str) -> tuple[bool, str | None]:
        """Revoke tokens at Google (best-effort) and delete credentials from storage."""
        with self.storage.unit_of_work() as uow:
            rec = uow.oauth_credentials.get_by_user_and_provider(user_id, "gmail")
            token_to_revoke = None
            if rec:
                try:
                    token_to_revoke = self.cipher.decrypt(rec.access_token_enc)
                except Exception:
                    pass
            # Always delete local record
            uow.oauth_credentials.delete_by_user_and_provider(user_id, "gmail")
            uow.commit()

        if not token_to_revoke:
            return True, None

        remote_revoked = False
        remote_error = None
        client = self.http_client or httpx.Client(timeout=10.0)
        close_client = self.http_client is None
        try:
            resp = client.post(
                GMAIL_REVOKE_URL,
                params={"token": token_to_revoke},
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
            remote_revoked = resp.status_code == 200
            if resp.status_code != 200:
                remote_error = f"HTTP {resp.status_code}: {resp.text[:100]}"
        except Exception as exc:
            remote_error = str(exc)
        finally:
            if close_client:
                client.close()

        return remote_revoked, remote_error


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
                return ProviderSubmissionResult(
                    status=ExecutionReceiptStatus.AMBIGUOUS_SUBMISSION,
                    external_confirmation_id=None,
                    error_category=ExecutionErrorCategory.UNKNOWN,
                    error_message="Gmail API returned HTTP 200 with non-JSON or malformed response body. Irreversible state ambiguous.",
                    raw_response_snippet=snippet,
                    provider_metadata={
                        "http_status": 200,
                        "duration_ms": duration_ms,
                        "provider_status": "GMAIL_API_MALFORMED_RESPONSE",
                        "provider_kind": ExecutionProviderKind.GMAIL_API.value,
                        "is_simulated": False,
                    },
                )

            raw_id = body.get("id")
            if not isinstance(raw_id, str) or not raw_id.strip():
                return ProviderSubmissionResult(
                    status=ExecutionReceiptStatus.AMBIGUOUS_SUBMISSION,
                    external_confirmation_id=None,
                    error_category=ExecutionErrorCategory.UNKNOWN,
                    error_message="Gmail API returned HTTP 200 without a valid message ID in response body. Irreversible state ambiguous; manual operator reconciliation required.",
                    raw_response_snippet=snippet,
                    provider_metadata={
                        "http_status": 200,
                        "duration_ms": duration_ms,
                        "provider_status": "GMAIL_API_MISSING_MESSAGE_ID",
                        "provider_kind": ExecutionProviderKind.GMAIL_API.value,
                        "is_simulated": False,
                    },
                )

            gmail_msg_id = raw_id.strip()
            gmail_thread_id = str(body.get("threadId") or "").strip()

            return ProviderSubmissionResult(
                status=ExecutionReceiptStatus.VERIFIED_SUCCESS,
                external_confirmation_id=gmail_msg_id,
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
