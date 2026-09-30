"""Identity primitives, Principal abstraction, and fail-closed authentication."""

from __future__ import annotations

from dataclasses import dataclass, field
import re
from typing import Protocol

from fastapi import HTTPException, Request, status

_SAFE_USER_ID_REGEX = re.compile(r"^[a-zA-Z0-9_\-\.]{1,128}$")


@dataclass(frozen=True)
class Principal:
    """Security principal representing an authenticated caller."""

    user_id: str
    roles: tuple[str, ...] = ("user",)
    is_authenticated: bool = True

    def __post_init__(self) -> None:
        if not self.user_id or not _SAFE_USER_ID_REGEX.match(self.user_id):
            raise ValueError(f"Invalid user_id format: {self.user_id!r}")

    def has_role(self, role: str) -> bool:
        return role in self.roles


class IdentityProvider(Protocol):
    """Protocol for extracting Principal from incoming HTTP requests."""

    async def get_principal(self, request: Request) -> Principal:
        ...


class FailClosedIdentityProvider:
    """Production identity provider that fails closed when dev auth is disabled."""

    async def get_principal(self, request: Request) -> Principal:
        # In production without external IdP configured, fail closed
        auth_header = request.headers.get("Authorization")
        if not auth_header:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Missing Authorization header.",
                headers={"WWW-Authenticate": "Bearer"},
            )
        # Bearer token verification hook for external JWT/OIDC
        if not auth_header.startswith("Bearer "):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid Authorization header scheme. Expected Bearer token.",
                headers={"WWW-Authenticate": "Bearer"},
            )
        token = auth_header[7:].strip()
        if not token:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Empty Bearer token.",
                headers={"WWW-Authenticate": "Bearer"},
            )
        # If not dev mode and no production token validator configured yet:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Production token validation failed or unconfigured.",
            headers={"WWW-Authenticate": "Bearer"},
        )


class DevelopmentIdentityProvider:
    """Development identity provider enabled ONLY when CASEWORKER_DEV_AUTH=true."""

    def __init__(self, dev_auth_enabled: bool) -> None:
        self.dev_auth_enabled = dev_auth_enabled

    async def get_principal(self, request: Request) -> Principal:
        if not self.dev_auth_enabled:
            # When disabled, completely ignore X-JackVerse-User and fail closed
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Authentication required. CASEWORKER_DEV_AUTH is disabled.",
                headers={"WWW-Authenticate": "Bearer"},
            )

        # Check for X-JackVerse-User header
        user_header = request.headers.get("X-JackVerse-User")
        if not user_header or not user_header.strip():
            # Check Bearer dev:<user_id> fallback
            auth_header = request.headers.get("Authorization", "")
            if auth_header.startswith("Bearer dev:"):
                user_header = auth_header[11:].strip()

        if not user_header or not user_header.strip():
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Missing required authentication header 'X-JackVerse-User'.",
                headers={"WWW-Authenticate": "Custom realm='X-JackVerse-User'"},
            )

        user_id = user_header.strip()
        if not _SAFE_USER_ID_REGEX.match(user_id):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Invalid X-JackVerse-User identifier '{user_id}'. Must match ^[a-zA-Z0-9_\\-\\.]{{1,128}}$",
            )

        roles_header = request.headers.get("X-JackVerse-Roles", "user")
        roles = tuple(r.strip() for r in roles_header.split(",") if r.strip())

        return Principal(user_id=user_id, roles=roles)
