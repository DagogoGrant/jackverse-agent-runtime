"""Identity primitives, Principal abstraction, and fail-closed authentication."""

from __future__ import annotations

from dataclasses import dataclass, field
import re
from typing import Protocol

from fastapi import HTTPException, Request, status

import hmac
import hashlib
import time

_SAFE_USER_ID_REGEX = re.compile(r"^[a-zA-Z0-9_\-\.]{1,128}$")


def generate_operator_token(operator_id: str, secret: str, ttl_seconds: int = 86400) -> str:
    """Generate a signed, time-limited operator bearer token."""
    if not secret or len(secret) < 32:
        raise ValueError("Operator secret must be at least 32 characters.")
    clean_id = operator_id.strip()
    if not _SAFE_USER_ID_REGEX.match(clean_id):
        raise ValueError(f"Invalid operator_id format: {operator_id!r}")
    exp = int(time.time()) + ttl_seconds
    msg = f"{clean_id}:{exp}".encode("utf-8")
    sig = hmac.new(secret.encode("utf-8"), msg, hashlib.sha256).hexdigest()
    return f"op.{clean_id}.{exp}.{sig}"


def verify_operator_token(token: str, secret: str) -> str | None:
    """Verify an operator token and return operator_id if valid and unexpired."""
    if not token or not secret or len(secret) < 32:
        return None

    # Signed token format op.<operator_id>.<expires>.<sig>
    parts = token.strip().split(".")
    if len(parts) == 4 and parts[0] == "op":
        op_id, exp_str, sig = parts[1], parts[2], parts[3]
        try:
            exp = int(exp_str)
            if exp < int(time.time()):
                return None  # Expired
            msg = f"{op_id}:{exp}".encode("utf-8")
            expected_sig = hmac.new(secret.encode("utf-8"), msg, hashlib.sha256).hexdigest()
            if hmac.compare_digest(sig, expected_sig) and _SAFE_USER_ID_REGEX.match(op_id):
                return op_id
        except (ValueError, TypeError):
            return None
    return None


@dataclass(frozen=True)
class Principal:
    """Security principal representing an authenticated caller."""

    user_id: str
    roles: tuple[str, ...] = ("user",)
    is_authenticated: bool = True
    auth_method: str = "dev_header"  # "operator_token", "dev_header", "production_token"

    def __post_init__(self) -> None:
        if not self.user_id or not _SAFE_USER_ID_REGEX.match(self.user_id):
            raise ValueError(f"Invalid user_id format: {self.user_id!r}")

    def has_role(self, role: str) -> bool:
        return role in self.roles

    @property
    def is_operator(self) -> bool:
        return self.has_role("operator") and self.auth_method in ("operator_token", "production_token", "dev_operator_token")


class IdentityProvider(Protocol):
    """Protocol for extracting Principal from incoming HTTP requests."""

    async def get_principal(self, request: Request) -> Principal:
        ...


class FailClosedIdentityProvider:
    """Production identity provider that verifies operator tokens or fails closed."""

    def __init__(self, operator_secret: str = "") -> None:
        self.operator_secret = operator_secret

    async def get_principal(self, request: Request) -> Principal:
        auth_header = request.headers.get("Authorization")
        token = ""
        if auth_header and auth_header.startswith("Bearer "):
            token = auth_header[7:].strip()
        elif request.cookies.get("jackverse_operator_session"):
            token = request.cookies.get("jackverse_operator_session", "").strip()

        if not token:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Missing Authorization header or operator session.",
                headers={"WWW-Authenticate": "Bearer"},
            )

        if self.operator_secret and len(self.operator_secret) >= 32:
            op_id = verify_operator_token(token, self.operator_secret)
            if op_id:
                if op_id.startswith("dev-") or op_id.startswith("dev_"):
                    raise HTTPException(
                        status_code=status.HTTP_401_UNAUTHORIZED,
                        detail="Developer operator tokens are strictly forbidden in production mode.",
                        headers={"WWW-Authenticate": "Bearer"},
                    )
                return Principal(
                    user_id=op_id,
                    roles=("operator", "user"),
                    is_authenticated=True,
                    auth_method="operator_token",
                )

        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Production token validation failed or unconfigured.",
            headers={"WWW-Authenticate": "Bearer"},
        )


class DevelopmentIdentityProvider:
    """Development identity provider supporting operator tokens and X-JackVerse-User dev headers."""

    def __init__(self, dev_auth_enabled: bool, operator_secret: str = "") -> None:
        self.dev_auth_enabled = dev_auth_enabled
        self.operator_secret = operator_secret

    async def get_principal(self, request: Request) -> Principal:
        # 1. Check for genuine operator bearer token or session cookie first
        auth_header = request.headers.get("Authorization", "")
        token = ""
        if auth_header.startswith("Bearer "):
            token = auth_header[7:].strip()
        elif request.cookies.get("jackverse_operator_session"):
            token = request.cookies.get("jackverse_operator_session", "").strip()

        if token and self.operator_secret and len(self.operator_secret) >= 32:
            op_id = verify_operator_token(token, self.operator_secret)
            if op_id:
                auth_method = "dev_operator_token" if (op_id.startswith("dev-") or op_id.startswith("dev_")) else "operator_token"
                return Principal(
                    user_id=op_id,
                    roles=("operator", "user"),
                    is_authenticated=True,
                    auth_method=auth_method,
                )

        if not self.dev_auth_enabled:
            # When dev auth disabled and no operator token verified, fail closed
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Authentication required. CASEWORKER_DEV_AUTH is disabled.",
                headers={"WWW-Authenticate": "Bearer"},
            )

        # 2. Check for X-JackVerse-User header (dev mode only)
        user_header = request.headers.get("X-JackVerse-User")
        if not user_header or not user_header.strip():
            # Check Bearer dev:<user_id> fallback
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

        return Principal(user_id=user_id, roles=roles, is_authenticated=True, auth_method="dev_header")
