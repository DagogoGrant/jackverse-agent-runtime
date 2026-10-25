"""Google OAuth endpoints with PKCE, encrypted credential storage, and revocation."""

from __future__ import annotations

import base64
from datetime import datetime, timedelta, timezone
import hashlib
import secrets
from typing import Annotated
import urllib.parse

from fastapi import APIRouter, Depends, HTTPException, Query, status
import httpx

from caseworker.adapters.crypto.token_cipher import TokenCipher
from caseworker.adapters.gmail_provider import DatabaseOAuthCredentialProvider
from caseworker.api.config import APIConfig
from caseworker.api.dependencies import get_config, get_principal, get_storage
from caseworker.api.identity import Principal
from caseworker.api.schemas.auth import (
    GoogleAuthAuthorizeResponse,
    GoogleAuthCallbackResponse,
    GoogleAuthDisconnectResponse,
    GoogleAuthStatusResponse,
)
from caseworker.domain.types import now_utc
from caseworker.persistence.sqlite import (
    OAuthCredentialRecord,
    OAuthStateRecord,
    SQLiteCaseworkerStorage,
)

router = APIRouter(tags=["auth"])

GOOGLE_AUTH_BASE_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_USERINFO_URL = "https://www.googleapis.com/oauth2/v3/userinfo"
REQUIRED_SCOPES = [
    "openid",
    "https://www.googleapis.com/auth/userinfo.email",
    "https://www.googleapis.com/auth/gmail.send",
]


@router.get("/api/v1/auth/google/status", response_model=GoogleAuthStatusResponse)
async def get_google_auth_status(
    principal: Annotated[Principal, Depends(get_principal)],
    storage: Annotated[SQLiteCaseworkerStorage, Depends(get_storage)],
) -> GoogleAuthStatusResponse:
    """Return Google OAuth connection status for the authenticated user."""
    with storage.unit_of_work() as uow:
        cred = uow.oauth_credentials.get_by_user_and_provider(principal.user_id, "gmail")
        if cred is None:
            return GoogleAuthStatusResponse(
                connected=False,
                authenticated_email=None,
                scopes=[],
                expires_at=None,
            )
        return GoogleAuthStatusResponse(
            connected=True,
            authenticated_email=cred.authenticated_email,
            scopes=cred.scopes,
            expires_at=cred.token_expiry,
        )


@router.get("/api/v1/auth/google/authorize", response_model=GoogleAuthAuthorizeResponse)
async def authorize_google(
    principal: Annotated[Principal, Depends(get_principal)],
    storage: Annotated[SQLiteCaseworkerStorage, Depends(get_storage)],
    config: Annotated[APIConfig, Depends(get_config)],
) -> GoogleAuthAuthorizeResponse:
    """Generate PKCE challenge, save one-time state, and return Google OAuth authorization URL."""
    if not config.gmail_client_id:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Google OAuth client ID is not configured (CASEWORKER_GMAIL_CLIENT_ID).",
        )

    code_verifier = secrets.token_urlsafe(64)
    digest = hashlib.sha256(code_verifier.encode("ascii")).digest()
    code_challenge = base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")
    state = secrets.token_urlsafe(32)

    now = now_utc()
    expires_dt = now + timedelta(minutes=10)
    created_at_iso = now.isoformat().replace("+00:00", "Z")
    expires_at_iso = expires_dt.isoformat().replace("+00:00", "Z")

    with storage.unit_of_work() as uow:
        state_record = OAuthStateRecord(
            state=state,
            user_id=principal.user_id,
            provider="gmail",
            code_verifier=code_verifier,
            redirect_uri=config.gmail_redirect_uri,
            created_at=created_at_iso,
            expires_at=expires_at_iso,
        )
        uow.oauth_states.save(state_record)
        uow.commit()

    params = {
        "client_id": config.gmail_client_id,
        "redirect_uri": config.gmail_redirect_uri,
        "response_type": "code",
        "scope": " ".join(REQUIRED_SCOPES),
        "state": state,
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
        "access_type": "offline",
        "prompt": "consent",
    }
    authorization_url = f"{GOOGLE_AUTH_BASE_URL}?{urllib.parse.urlencode(params)}"
    return GoogleAuthAuthorizeResponse(authorization_url=authorization_url, state=state)


@router.get("/api/v1/auth/google/callback", response_model=GoogleAuthCallbackResponse)
async def google_auth_callback(
    storage: Annotated[SQLiteCaseworkerStorage, Depends(get_storage)],
    config: Annotated[APIConfig, Depends(get_config)],
    code: str | None = Query(None, description="Google OAuth authorization code"),
    state: str | None = Query(None, description="One-time state parameter"),
    error: str | None = Query(None, description="Google OAuth error code"),
    error_description: str | None = Query(None, description="Google OAuth error message"),
) -> GoogleAuthCallbackResponse:
    """Validate Google OAuth callback, exchange code with PKCE, verify identity, and encrypt tokens."""
    if error:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Google OAuth consent denied or failed: {error} ({error_description or ''})",
        )

    if not code or not state:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Missing required OAuth 'code' or 'state' parameter.",
        )

    now_iso = now_utc().isoformat().replace("+00:00", "Z")

    with storage.unit_of_work() as uow:
        state_rec = uow.oauth_states.get_by_state(state)
        if state_rec is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid or unrecognized OAuth state parameter.",
            )
        if state_rec.used_at is not None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="OAuth state parameter has already been used.",
            )

        # Check expiration
        try:
            exp_dt = datetime.fromisoformat(state_rec.expires_at.replace("Z", "+00:00"))
            if exp_dt.tzinfo is None:
                exp_dt = exp_dt.replace(tzinfo=timezone.utc)
            if exp_dt < datetime.now(timezone.utc):
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="OAuth state parameter has expired. Please re-authorize.",
                )
        except HTTPException:
            raise
        except Exception:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid OAuth state expiration timestamp.",
            )

        # Mark used atomically
        marked = uow.oauth_states.mark_used(state, now_iso)
        if not marked:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="OAuth state parameter has already been consumed.",
            )
        uow.commit()

    # Exchange authorization code for tokens using PKCE verifier
    token_payload = {
        "client_id": config.gmail_client_id,
        "client_secret": config.gmail_client_secret,
        "code": code,
        "code_verifier": state_rec.code_verifier,
        "grant_type": "authorization_code",
        "redirect_uri": state_rec.redirect_uri or config.gmail_redirect_uri,
    }

    async with httpx.AsyncClient(timeout=15.0) as client:
        try:
            token_resp = await client.post(GOOGLE_TOKEN_URL, data=token_payload)
        except Exception as exc:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=f"Network error communicating with Google token endpoint: {exc}",
            )

        if token_resp.status_code != 200:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Google OAuth token exchange failed (HTTP {token_resp.status_code}): {token_resp.text[:200]}",
            )

        try:
            token_data = token_resp.json()
        except Exception:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail="Google token endpoint returned invalid non-JSON payload.",
            )

        access_token = token_data.get("access_token")
        if not access_token:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail="Google token response missing 'access_token'.",
            )

        refresh_token = token_data.get("refresh_token")
        expires_in = int(token_data.get("expires_in", 3600))
        granted_scope_str = token_data.get("scope", "")
        granted_scopes = [s for s in granted_scope_str.split(" ") if s] or REQUIRED_SCOPES

        # Fetch Google userinfo to verify sub, email, and email_verified
        try:
            userinfo_resp = await client.get(
                GOOGLE_USERINFO_URL,
                headers={"Authorization": f"Bearer {access_token}"},
            )
        except Exception as exc:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=f"Network error fetching Google userinfo: {exc}",
            )

        if userinfo_resp.status_code != 200:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Failed to retrieve Google user profile (HTTP {userinfo_resp.status_code}): {userinfo_resp.text[:200]}",
            )

        try:
            userinfo = userinfo_resp.json()
        except Exception:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail="Google userinfo endpoint returned invalid non-JSON payload.",
            )

    google_sub = userinfo.get("sub")
    email = userinfo.get("email")
    email_verified = userinfo.get("email_verified", False)

    if not google_sub or not email:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Google userinfo did not contain required 'sub' or 'email'.",
        )

    if not email_verified:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Google account email '{email}' is unverified.",
        )

    # Encrypt tokens with TokenCipher
    cipher = TokenCipher(config.encryption_key)
    access_token_enc = cipher.encrypt(access_token)
    refresh_token_enc = cipher.encrypt(refresh_token) if refresh_token else None

    token_expiry_dt = datetime.now(timezone.utc) + timedelta(seconds=expires_in)
    token_expiry_iso = token_expiry_dt.isoformat().replace("+00:00", "Z")

    cred_record = OAuthCredentialRecord(
        user_id=state_rec.user_id,
        provider="gmail",
        google_sub=str(google_sub),
        access_token_enc=access_token_enc,
        refresh_token_enc=refresh_token_enc,
        token_expiry=token_expiry_iso,
        authenticated_email=str(email),
        scopes=granted_scopes,
        created_at=now_iso,
        updated_at=now_iso,
    )

    with storage.unit_of_work() as uow:
        uow.oauth_credentials.save(cred_record)
        uow.commit()

    return GoogleAuthCallbackResponse(
        success=True,
        email=str(email),
        user_id=state_rec.user_id,
        provider="gmail",
    )


@router.post("/api/v1/auth/google/disconnect", response_model=GoogleAuthDisconnectResponse)
async def disconnect_google(
    principal: Annotated[Principal, Depends(get_principal)],
    storage: Annotated[SQLiteCaseworkerStorage, Depends(get_storage)],
    config: Annotated[APIConfig, Depends(get_config)],
) -> GoogleAuthDisconnectResponse:
    """Disconnect Google OAuth credentials, revoking remote tokens best-effort and deleting local storage."""
    cipher = TokenCipher(config.encryption_key)
    provider = DatabaseOAuthCredentialProvider(
        storage=storage,
        cipher=cipher,
        client_id=config.gmail_client_id,
        client_secret=config.gmail_client_secret,
    )
    provider.revoke_and_disconnect(principal.user_id)
    return GoogleAuthDisconnectResponse(success=True, disconnected=True)
