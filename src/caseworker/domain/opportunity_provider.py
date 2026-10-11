"""Opportunity ingestion providers protocol and manual intake provider."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Protocol, runtime_checkable

from caseworker.domain.enums import OpportunityType
from caseworker.domain.types import ensure_utc, from_iso_utc


@dataclass
class RawOpportunity:
    """Standard raw opportunity payload returned by an ingestion provider."""

    title: str
    organization: str = ""
    opportunity_type: OpportunityType | str = OpportunityType.JOB
    source_url: str = ""
    source_name: str = ""
    location: str = ""
    requirements: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
    deadline: datetime | None = None
    external_posting_id: str | None = None

    def __post_init__(self) -> None:
        if self.deadline is not None:
            self.deadline = ensure_utc(self.deadline)
        if isinstance(self.opportunity_type, str):
            try:
                self.opportunity_type = OpportunityType(self.opportunity_type)
            except ValueError:
                pass


@runtime_checkable
class OpportunityProvider(Protocol):
    """Protocol for external and manual opportunity discovery and ingestion providers."""

    @property
    def name(self) -> str:
        ...

    def ingest(self, raw_data: dict[str, Any]) -> RawOpportunity:
        ...


class ManualOpportunityProvider:
    """Manual intake provider for user-submitted opportunity leads."""

    @property
    def name(self) -> str:
        return "manual"

    def ingest(self, raw_data: dict[str, Any]) -> RawOpportunity:
        title = (raw_data.get("title") or "").strip()
        if not title:
            raise ValueError("Opportunity title is required.")

        deadline = raw_data.get("deadline")
        if isinstance(deadline, str):
            deadline = from_iso_utc(deadline)

        ext_id = raw_data.get("external_posting_id")
        return RawOpportunity(
            title=title,
            organization=(raw_data.get("organization") or "").strip(),
            opportunity_type=raw_data.get("opportunity_type", OpportunityType.JOB),
            source_url=(raw_data.get("source_url") or "").strip(),
            source_name=raw_data.get("source_name") or "manual",
            location=(raw_data.get("location") or "").strip(),
            requirements=list(raw_data.get("requirements") or []),
            metadata=dict(raw_data.get("metadata") or {}),
            deadline=deadline,
            external_posting_id=str(ext_id).strip() if ext_id else None,
        )
