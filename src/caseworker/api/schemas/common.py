"""Common pagination schemas."""

from __future__ import annotations

from typing import Generic, TypeVar
from pydantic import BaseModel, Field

T = TypeVar("T")


class PaginatedResponse(BaseModel, Generic[T]):
    """Standard offset-paginated collection envelope."""

    items: list[T] = Field(..., description="Collection items for current page.")
    total: int = Field(..., description="Total number of items matching filter.")
    limit: int = Field(..., description="Current page size limit.")
    offset: int = Field(..., description="Offset position.")


class CursorPaginatedResponse(BaseModel, Generic[T]):
    """Cursor-paginated collection envelope for monotonic event streams."""

    items: list[T] = Field(..., description="Events in sequential order.")
    next_position: int | None = Field(None, description="Next monotonic position cursor.")
    has_more: bool = Field(False, description="Whether more events exist.")
    limit: int = Field(..., description="Requested limit.")
