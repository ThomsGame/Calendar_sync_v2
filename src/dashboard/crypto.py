"""Credential encryption/decryption using Fernet symmetric encryption."""

from __future__ import annotations

import os
from cryptography.fernet import Fernet

# Module-level cached instance so the key is only validated once per process.
_fernet: Fernet | None = None


def _is_valid_fernet_key(key: str) -> bool:
    """Return True if key is a valid 32-byte url-safe base64 Fernet key."""
    try:
        Fernet(key.encode() if isinstance(key, str) else key)
        return True
    except Exception:
        return False


def _generate_and_persist_key() -> str:
    """Generate a new Fernet key, save it to .env.dashboard, and return it."""
    import pathlib

    new_key = Fernet.generate_key().decode()
    os.environ["DASHBOARD_SECRET_KEY"] = new_key

    # Persist to .env.dashboard so the same key survives restarts
    env_path = pathlib.Path(__file__).parents[3] / ".env.dashboard"
    try:
        if env_path.exists():
            content = env_path.read_text()
            if "DASHBOARD_SECRET_KEY" in content:
                import re
                content = re.sub(
                    r"^DASHBOARD_SECRET_KEY=.*$",
                    f"DASHBOARD_SECRET_KEY={new_key}",
                    content,
                    flags=re.MULTILINE,
                )
            else:
                content += f"\nDASHBOARD_SECRET_KEY={new_key}\n"
            env_path.write_text(content)
        else:
            env_path.write_text(f"DASHBOARD_SECRET_KEY={new_key}\n")
        print(f"[CRYPTO] Generated new DASHBOARD_SECRET_KEY and saved to {env_path}")
    except Exception as e:
        print(f"[CRYPTO] WARNING: could not persist DASHBOARD_SECRET_KEY to {env_path}: {e}")
        print(f"[CRYPTO] Add this to your environment to keep it stable across restarts:")
        print(f"[CRYPTO]   DASHBOARD_SECRET_KEY={new_key}")

    return new_key


def get_fernet() -> Fernet:
    """Get Fernet instance, auto-generating the key if missing or invalid."""
    global _fernet
    if _fernet is not None:
        return _fernet

    key = os.environ.get("DASHBOARD_SECRET_KEY", "")
    if not key or not _is_valid_fernet_key(key):
        if key:
            print(
                "[CRYPTO] WARNING: DASHBOARD_SECRET_KEY is set but not a valid Fernet key "
                f"(value: {key[:20]}...). Generating a new one."
            )
        else:
            print("[CRYPTO] WARNING: DASHBOARD_SECRET_KEY not set. Generating a new one.")
        key = _generate_and_persist_key()

    _fernet = Fernet(key.encode() if isinstance(key, str) else key)
    return _fernet


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
