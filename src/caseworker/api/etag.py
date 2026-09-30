"""Resource-bound ETag generation and HTTP conditional precondition validation."""

from __future__ import annotations

import re
from fastapi import HTTPException, Request, Response, status

_ETAG_REGEX = re.compile(r'^"?([a-zA-Z0-9_\-]+):([a-zA-Z0-9_\-]+):v(\d+)"?$')


def make_etag(resource_type: str, resource_id: str, version: int) -> str:
    """Generate strongly typed, resource-bound ETag in format: '"{resource_type}:{resource_id}:v{version}"'."""
    return f'"{resource_type}:{resource_id}:v{version}"'


def set_etag_header(response: Response, resource_type: str, resource_id: str, version: int) -> str:
    """Attach resource-bound ETag to an outgoing HTTP response."""
    tag = make_etag(resource_type, resource_id, version)
    response.headers["ETag"] = tag
    return tag


def check_if_match(
    request: Request,
    resource_type: str,
    resource_id: str,
    current_version: int,
) -> None:
    """Verify incoming If-Match header on mutation endpoints.

    Raises:
        HTTPException(428): If 'If-Match' is missing.
        HTTPException(412): If 'If-Match' does not match the resource's current ETag.
    """
    if_match = request.headers.get("If-Match")
    if not if_match:
        raise HTTPException(
            status_code=status.HTTP_428_PRECONDITION_REQUIRED,
            detail="Precondition Required: 'If-Match' header with resource ETag is required for mutating operations.",
        )

    expected_etag = make_etag(resource_type, resource_id, current_version)
    cleaned_if_match = if_match.strip()

    # Wildcard match
    if cleaned_if_match == "*":
        return

    # Check for direct or unquoted match
    if cleaned_if_match != expected_etag and cleaned_if_match.strip('"') != expected_etag.strip('"'):
        raise HTTPException(
            status_code=status.HTTP_412_PRECONDITION_FAILED,
            detail=f"Precondition Failed: Provided ETag {if_match} does not match current resource ETag {expected_etag}.",
        )
