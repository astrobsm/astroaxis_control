"""AES encryption helpers for sensitive Wi-Fi / RADIUS secrets.

Uses Fernet (AES-128 in CBC mode with HMAC-SHA256 authentication) from the
`cryptography` package. The symmetric key is derived from the WIFI_ENCRYPTION_KEY
environment variable. If that variable is not set, the key is derived
deterministically (PBKDF2-HMAC-SHA256) from the application SECRET_KEY so the
system still functions in development — but production deployments MUST set a
dedicated WIFI_ENCRYPTION_KEY.

Never log or return decrypted secrets to clients.
"""
from __future__ import annotations

import base64
import hashlib
import os
from typing import Optional

from cryptography.fernet import Fernet, InvalidToken

# Fixed salt for key derivation. The real secret is the env var / SECRET_KEY,
# the salt only needs to be stable across restarts.
_KDF_SALT = b"astrobsm-wifi-kdf-salt-v1"


def _derive_key() -> bytes:
    """Return a urlsafe-base64 32-byte key suitable for Fernet."""
    raw_key = os.getenv("WIFI_ENCRYPTION_KEY")
    if raw_key:
        # Allow either an already-valid Fernet key or arbitrary passphrase.
        try:
            # Valid Fernet keys are 44-char urlsafe base64 of 32 bytes.
            decoded = base64.urlsafe_b64decode(raw_key)
            if len(decoded) == 32:
                return raw_key.encode()
        except Exception:
            pass
        secret = raw_key.encode()
    else:
        secret = os.getenv(
            "SECRET_KEY", "astroasix-secret-key-change-in-production"
        ).encode()

    derived = hashlib.pbkdf2_hmac("sha256", secret, _KDF_SALT, 100_000, dklen=32)
    return base64.urlsafe_b64encode(derived)


_fernet = Fernet(_derive_key())


def encrypt_secret(plaintext: Optional[str]) -> Optional[str]:
    """Encrypt a plaintext secret. Returns None for empty/None input."""
    if plaintext is None or plaintext == "":
        return None
    token = _fernet.encrypt(plaintext.encode("utf-8"))
    return token.decode("utf-8")


def decrypt_secret(ciphertext: Optional[str]) -> Optional[str]:
    """Decrypt a previously encrypted secret. Returns None on empty/invalid input."""
    if not ciphertext:
        return None
    try:
        return _fernet.decrypt(ciphertext.encode("utf-8")).decode("utf-8")
    except (InvalidToken, ValueError, TypeError):
        return None


def has_secret(ciphertext: Optional[str]) -> bool:
    """True when a (non-empty) encrypted value is stored."""
    return bool(ciphertext)
