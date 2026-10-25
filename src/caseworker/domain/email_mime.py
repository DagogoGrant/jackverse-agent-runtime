"""Deterministic MIME serialization engine for governed email applications under RFC 2822."""

from __future__ import annotations

import base64
from email.message import EmailMessage
import email.policy
from typing import Any
import re

from caseworker.domain.errors import DomainValidationError
from caseworker.domain.execution_plan import (
    EmailAttachmentManifest,
    EmailPayloadManifest,
    ExecutionUploadAsset,
)
from caseworker.domain.types import compute_sha256_bytes

_CRLF_PATTERN = re.compile(r"[\r\n\x00]")


def validate_email_header_field(name: str, value: str) -> str:
    """Validate that an email header field contains no CRLF or null bytes, preventing header injection."""
    if not value or not str(value).strip():
        raise DomainValidationError(f"Email header '{name}' cannot be empty.")
    clean = str(value).strip()
    if _CRLF_PATTERN.search(clean):
        raise DomainValidationError(
            f"Header injection detected: Email header '{name}' contains forbidden newline or null characters."
        )
    return clean


def serialize_mime_message(
    manifest: EmailPayloadManifest,
    upload_assets: list[ExecutionUploadAsset] | dict[str, bytes] | None = None,
) -> bytes:
    """Deterministically compile an RFC 2822 MIME message from an EmailPayloadManifest.

    Guarantees:
    - Exact header encoding and CRLF injection protection.
    - Deterministic MIME boundary matching manifest.mime_boundary.
    - Verified attachment bytes matching content_sha256.
    - Reproducible byte output: calling this multiple times with the same inputs produces identical bytes.
    """
    to_email = validate_email_header_field("To", manifest.to_email)
    from_email = validate_email_header_field("From", manifest.from_email)
    subject = validate_email_header_field("Subject", manifest.subject)
    message_id = validate_email_header_field("Message-ID", manifest.message_id)
    date_rfc2822 = validate_email_header_field("Date", manifest.date_rfc2822)

    msg = EmailMessage(policy=email.policy.SMTP)
    msg["From"] = from_email
    msg["To"] = to_email
    msg["Subject"] = subject
    msg["Date"] = date_rfc2822
    msg["Message-ID"] = message_id

    # Plain text body
    body_text = manifest.body_text or ""
    msg.set_content(body_text, charset="utf-8")

    # Map available asset bytes by asset_id or filename
    asset_bytes_map: dict[str, bytes] = {}
    if isinstance(upload_assets, dict):
        asset_bytes_map = upload_assets
    elif isinstance(upload_assets, list):
        for u in upload_assets:
            if u.content_bytes is not None:
                if u.asset_id:
                    asset_bytes_map[u.asset_id] = u.content_bytes
                if u.filename:
                    asset_bytes_map[u.filename] = u.content_bytes

    # Process attachments in deterministic order
    sorted_attachments = sorted(manifest.attachments, key=lambda a: (a.filename, a.content_sha256))
    if sorted_attachments:
        for att in sorted_attachments:
            content_bytes = asset_bytes_map.get(att.asset_id)
            if content_bytes is None:
                content_bytes = asset_bytes_map.get(att.filename)

            if content_bytes is None:
                raise DomainValidationError(
                    f"Attachment bytes missing for '{att.filename}' (asset_id: '{att.asset_id}')."
                )

            computed_sha = compute_sha256_bytes(content_bytes)
            if computed_sha != att.content_sha256:
                raise DomainValidationError(
                    f"Attachment content hash mismatch for '{att.filename}'. Expected '{att.content_sha256}', computed '{computed_sha}'."
                )

            m_type = att.mime_type or "application/octet-stream"
            if "/" in m_type:
                maintype, subtype = m_type.split("/", 1)
            else:
                maintype, subtype = "application", "octet-stream"

            msg.add_attachment(
                content_bytes,
                maintype=maintype,
                subtype=subtype,
                filename=att.filename,
            )

        # Set fixed boundary for reproducible multipart serialization
        msg.set_boundary(manifest.mime_boundary)

    return msg.as_bytes()
