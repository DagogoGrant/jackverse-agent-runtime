"""Health and readiness probe endpoints."""

from __future__ import annotations

import sqlite3
from typing import Annotated
from fastapi import APIRouter, Depends, HTTPException, status
from caseworker.api.dependencies import get_storage
from caseworker.persistence.sqlite import SQLiteCaseworkerStorage

router = APIRouter(tags=["health"])


@router.get("/health/live")
async def health_live() -> dict[str, str]:
    """Lightweight liveness probe checking process responsiveness."""
    return {"status": "alive"}


@router.get("/health/ready")
async def health_ready(
    storage: Annotated[SQLiteCaseworkerStorage, Depends(get_storage)],
) -> dict[str, str]:
    """Lightweight readiness probe executing read-only database query without running migrations."""
    try:
        with storage.unit_of_work() as uow:
            cursor = uow.conn.cursor()
            cursor.execute("SELECT 1;")
            cursor.fetchone()
        return {"status": "ready", "database": "connected"}
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Database readiness check failed: {exc}",
        )
