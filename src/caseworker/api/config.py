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
