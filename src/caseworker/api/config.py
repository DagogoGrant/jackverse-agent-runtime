"""API configuration settings and environment variable parsing."""

from __future__ import annotations

import os
from dataclasses import dataclass, field


@dataclass
class APIConfig:
    """Runtime configuration for Caseworker FastAPI service."""

    db_path: str = field(
        default_factory=lambda: os.getenv("CASEWORKER_DB_PATH", ".caseworker/caseworker.db")
    )
    dev_auth: bool = field(
        default_factory=lambda: os.getenv("CASEWORKER_DEV_AUTH", "false").lower() in ("true", "1", "yes")
    )
    simulation_enabled: bool = field(
        default_factory=lambda: os.getenv("CASEWORKER_SIMULATION_ENABLED", "false").lower() in ("true", "1", "yes")
    )
    real_execution_enabled: bool = field(
        default_factory=lambda: os.getenv("CASEWORKER_REAL_EXECUTION_ENABLED", "false").lower() in ("true", "1", "yes")
    )
    gmail_execution_enabled: bool = field(
        default_factory=lambda: os.getenv("CASEWORKER_GMAIL_EXECUTION_ENABLED", "false").lower() in ("true", "1", "yes")
    )
    gmail_client_id: str = field(
        default_factory=lambda: os.getenv("CASEWORKER_GMAIL_CLIENT_ID", "")
    )
    gmail_client_secret: str = field(
        default_factory=lambda: os.getenv("CASEWORKER_GMAIL_CLIENT_SECRET", "")
    )
    gmail_refresh_token: str = field(
        default_factory=lambda: os.getenv("CASEWORKER_GMAIL_REFRESH_TOKEN", "")
    )
    gmail_redirect_uri: str = field(
        default_factory=lambda: os.getenv("CASEWORKER_GMAIL_REDIRECT_URI", "http://localhost:8000/api/v1/auth/google/callback")
    )
    encryption_key: str = field(
        default_factory=lambda: os.getenv("CASEWORKER_ENCRYPTION_KEY", "")
    )
    encryption_fallback_keys: list[str] = field(
        default_factory=lambda: [
            k.strip()
            for k in os.getenv("CASEWORKER_ENCRYPTION_FALLBACK_KEYS", "").split(",")
            if k.strip()
        ]
    )
    operator_secret: str = field(
        default_factory=lambda: os.getenv("CASEWORKER_OPERATOR_SECRET", "")
    )
    allowed_origins: list[str] = field(
        default_factory=lambda: [
            origin.strip()
            for origin in os.getenv(
                "CASEWORKER_ALLOWED_ORIGINS",
                "http://localhost:3000,http://localhost:5173,http://127.0.0.1:3000,http://127.0.0.1:5173",
            ).split(",")
            if origin.strip()
        ]
    )
    default_page_limit: int = 50
    max_page_limit: int = 100
