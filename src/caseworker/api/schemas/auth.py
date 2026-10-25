"""Pydantic schemas for Google OAuth endpoints."""

from __future__ import annotations

from pydantic import BaseModel, Field


class GoogleAuthStatusResponse(BaseModel):
    """Google OAuth connection status for the authenticated user."""

    connected: bool = Field(..., description="Whether a valid OAuth credential exists for this user.")
    authenticated_email: str | None = Field(None, description="The authenticated Google email address.")
    scopes: list[str] = Field(default_factory=list, description="Granted OAuth scopes.")
    expires_at: str | None = Field(None, description="ISO UTC timestamp when the access token expires.")


class GoogleAuthAuthorizeResponse(BaseModel):
    """Response containing the Google OAuth authorization redirect URL and state."""

    authorization_url: str = Field(..., description="Google OAuth authorization consent URL.")
    state: str = Field(..., description="Cryptographically random one-time state token.")


class GoogleAuthCallbackResponse(BaseModel):
    """Response returned upon successful OAuth authorization code exchange."""

    success: bool = Field(..., description="Whether token exchange and credential storage succeeded.")
    email: str = Field(..., description="Verified Google email address.")
    user_id: str = Field(..., description="Caseworker user ID bound to this credential.")
    provider: str = Field("google", description="Identity and OAuth provider name.")


class GoogleAuthDisconnectResponse(BaseModel):
    """Response returned upon disconnecting Google OAuth credentials."""

    success: bool = Field(..., description="Whether disconnect was processed.")
    disconnected: bool = Field(..., description="Whether local credential record was deleted.")


class OperatorSessionRequest(BaseModel):
    """Request to establish an operator session cookie."""

    operator_token: str = Field(..., description="Time-limited signed operator bearer token.")


class OperatorSessionResponse(BaseModel):
    """Response returned after establishing or terminating an operator session."""

    success: bool = Field(..., description="Whether the operator session operation succeeded.")
    operator_id: str | None = Field(None, description="Authenticated operator ID if session active.")

