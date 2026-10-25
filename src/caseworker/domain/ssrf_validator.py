"""SSRF protection and URI safety validation for employer evidence sources."""

from __future__ import annotations

import ipaddress
import re
from urllib.parse import urlparse

from caseworker.domain.errors import SSRFValidationError

_PROHIBITED_HOSTS = {
    "localhost",
    "metadata.google.internal",
    "metadata.internal",
    "instance-data",
}

_SAFE_FQDN_PATTERN = re.compile(
    r"^(?:[a-zA-Z0-9](?:[a-zA-Z0-9\-]{0,61}[a-zA-Z0-9])?\.)+[a-zA-Z]{2,63}$"
)


def validate_evidence_source_uri(uri: str, allow_http: bool = True) -> str:
    """Validate that an evidence URI is safe, syntactically valid, and does not target internal services.

    Rules:
    - Must be a valid URI.
    - Scheme must be 'https' (or 'http' if allow_http=True).
    - Hostname must be present and must not be loopback, private, link-local, or cloud metadata.
    - No userinfo (e.g., user:pass@host).
    - Port must be standard (80, 443) or omitted.
    """
    if not uri or not isinstance(uri, str) or not uri.strip():
        raise SSRFValidationError("Evidence source URI cannot be empty.")

    cleaned_uri = uri.strip()
    try:
        parsed = urlparse(cleaned_uri)
    except Exception as exc:
        raise SSRFValidationError(f"Invalid URI format: {exc}")

    # Check scheme
    allowed_schemes = ("https", "http") if allow_http else ("https",)
    if parsed.scheme.lower() not in allowed_schemes:
        raise SSRFValidationError(
            f"Evidence source URI scheme '{parsed.scheme}' is not allowed. Expected one of {allowed_schemes}."
        )

    # Check userinfo
    if parsed.username or parsed.password:
        raise SSRFValidationError("Evidence source URI must not contain embedded user credentials.")

    # Check hostname
    host = parsed.hostname
    if not host:
        raise SSRFValidationError("Evidence source URI missing valid hostname.")

    host_lower = host.lower()

    if host_lower in _PROHIBITED_HOSTS:
        raise SSRFValidationError(f"Evidence source URI targets prohibited host '{host}'.")

    # Check IP address targets
    try:
        ip = ipaddress.ip_address(host_lower)
        if ip.is_loopback or ip.is_private or ip.is_link_local or ip.is_reserved or ip.is_multicast:
            raise SSRFValidationError(f"Evidence source URI targets prohibited non-public IP '{host}'.")
    except ValueError:
        # Not an IP address string; check FQDN syntax
        if not _SAFE_FQDN_PATTERN.match(host_lower):
            # Allow common simple domains like 'example.com' or 'test.local' only if not targeting local loopback
            if host_lower.endswith(".local") or host_lower.endswith(".internal"):
                raise SSRFValidationError(f"Evidence source URI targets internal domain '{host}'.")

    # Check port
    if parsed.port and parsed.port not in (80, 443):
        raise SSRFValidationError(f"Evidence source URI specifies non-standard port '{parsed.port}'.")

    return cleaned_uri
