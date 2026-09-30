"""Pydantic schemas for Domain Events cursor streaming API."""

from __future__ import annotations

from typing import Any
from pydantic import BaseModel, Field


class DomainEventResponse(BaseModel):
    event_id: str
    event_type: str
    aggregate_type: str
    aggregate_id: str
    aggregate_version: int
    user_id: str
    occurred_at: str
    payload: dict[str, Any] = Field(default_factory=dict)
    schema_version: int = 1
    position: int | None = Field(None, description="Monotonic sequence position for cursor pagination.")
