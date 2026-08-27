"""Credential encryption/decryption using Fernet symmetric encryption."""

from __future__ import annotations

import os
from cryptography.fernet import Fernet


def get_fernet() -> Fernet:
    """Get Fernet instance using the secret key from environment."""
    key = os.environ.get("DASHBOARD_SECRET_KEY")
    if not key:
        raise RuntimeError(
            "DASHBOARD_SECRET_KEY environment variable not set. "
            "Generate one with: python -c \"from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())\""
        )
    return Fernet(key.encode() if isinstance(key, str) else key)


def encrypt(value: str) -> str:
    """Encrypt a plaintext string, return base64 ciphertext."""
    if not value:
        return ""
    return get_fernet().encrypt(value.encode()).decode()


def decrypt(token: str) -> str:
    """Decrypt a Fernet token back to plaintext."""
    if not token:
        return ""
    return get_fernet().decrypt(token.encode()).decode()
