"""Pydantic schemas for employer acceptance evidence verification."""

from __future__ import annotations

from pydantic import BaseModel, Field


class VerifyEmailAcceptanceRequest(BaseModel):
    """Operator request to verify employer email application acceptance."""

    instructions_text: str = Field(..., description="Verbatim employer application instructions text.")
    verified_recipient_email: str = Field(..., description="Verified recipient email address specified in employer instructions.")
    source_uri: str = Field(..., description="Public employer URL where instructions were published.")
    evidence_source: str = Field("official_job_posting", description="Provenance channel/source.")
    employer_domain: str | None = Field(None, description="Employer domain (e.g. example.com).")


class VerifyEmailAcceptanceResponse(BaseModel):
    """Response containing immutable verified employer acceptance record."""

    evidence_id: str
    opportunity_id: str
    user_id: str
    evidence_source: str
    source_uri: str
    instructions_text: str
    verified_recipient_email: str
    employer_domain: str
    snapshot_hash: str
    evidence_digest: str
    verified_by: str
    verified_at: str
    evidence_version: int
    is_active: bool
