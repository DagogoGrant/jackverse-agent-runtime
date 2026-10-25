"""Authenticated symmetric token cipher with rotation support and strict failure handling."""

from __future__ import annotations

import base64
import hashlib
from typing import Sequence

from cryptography.fernet import Fernet, InvalidToken

from caseworker.domain.errors import CryptoConfigurationError, TokenDecryptionError


class TokenCipher:
    """Authenticated encryption cipher for OAuth access and refresh tokens.

    Guarantees:
    - Never uses an ephemeral or default key.
    - Requires high-entropy key (minimum 32 characters/bytes).
    - Uses Fernet (AES-128-CBC + HMAC-SHA256 authenticated encryption).
    - Prefixes ciphertext with key format version ('v1:').
    - Supports key rotation via optional fallback keys.
    - Fails closed with TokenDecryptionError on corrupted or tampered ciphertexts.
    """

    CURRENT_VERSION = "v1"

    def __init__(
        self,
        primary_key: str,
        rotation_fallback_keys: Sequence[str] | None = None,
    ) -> None:
        if not primary_key or not isinstance(primary_key, str) or not primary_key.strip():
            raise CryptoConfigurationError(
                "CASEWORKER_ENCRYPTION_KEY is required for token encryption but is not configured."
            )
        clean_key = primary_key.strip()
        if len(clean_key) < 32:
            raise CryptoConfigurationError(
                f"CASEWORKER_ENCRYPTION_KEY has insufficient entropy ({len(clean_key)} chars). Minimum 32 required."
            )

        self._primary_fernet = Fernet(self._derive_fernet_key(clean_key))
        self._fallback_fernets: list[Fernet] = []
        if rotation_fallback_keys:
            for fallback in rotation_fallback_keys:
                if fallback and isinstance(fallback, str) and len(fallback.strip()) >= 32:
                    self._fallback_fernets.append(Fernet(self._derive_fernet_key(fallback.strip())))

    @staticmethod
    def _derive_fernet_key(raw_secret: str) -> bytes:
        """Derive standard 32-byte base64url Fernet key from raw secret via SHA-256."""
        digest = hashlib.sha256(raw_secret.encode("utf-8")).digest()
        return base64.urlsafe_b64encode(digest)

    def encrypt(self, plaintext: str) -> str:
        """Encrypt plaintext string using authenticated symmetric encryption with format tag."""
        if not plaintext:
            return ""
        encrypted_bytes = self._primary_fernet.encrypt(plaintext.encode("utf-8"))
        return f"{self.CURRENT_VERSION}:{encrypted_bytes.decode('ascii')}"

    def decrypt(self, ciphertext: str) -> str:
        """Decrypt ciphertext string, trying primary key then rotation fallbacks."""
        if not ciphertext:
            return ""

        raw_payload = ciphertext
        if ciphertext.startswith(f"{self.CURRENT_VERSION}:"):
            raw_payload = ciphertext[len(self.CURRENT_VERSION) + 1 :]

        # Try primary key first
        try:
            decrypted = self._primary_fernet.decrypt(raw_payload.encode("ascii"))
            return decrypted.decode("utf-8")
        except InvalidToken:
            pass
        except Exception as exc:
            raise TokenDecryptionError(f"Unexpected token decryption error: {exc}")

        # Try rotation fallback keys
        for fallback_fernet in self._fallback_fernets:
            try:
                decrypted = fallback_fernet.decrypt(raw_payload.encode("ascii"))
                return decrypted.decode("utf-8")
            except InvalidToken:
                continue

        raise TokenDecryptionError(
            "Failed to decrypt OAuth token: authentication tag invalid, corrupted, or encryption key mismatch."
        )
