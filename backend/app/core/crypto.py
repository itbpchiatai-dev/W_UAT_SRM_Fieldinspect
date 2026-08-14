"""Symmetric encryption for secrets stored at rest in the app DB.

Used by the Database Connections feature to keep external-DB passwords
out of plaintext columns. Backed by Fernet (AES-128-CBC + HMAC) from the
`cryptography` package, keyed by the `DB_CONNECTIONS_ENCRYPTION_KEY` env
var — a url-safe base64 32-byte key.

Generate a key:
    python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"

The key lives in `.env` only (never in source / project.config / DB).
Rotating it invalidates every stored ciphertext — re-enter passwords in
the UI after a rotation. `encrypt`/`decrypt` raise a clear RuntimeError
when the key is unset so the feature fails loudly instead of silently
persisting recoverable secrets.
"""
from __future__ import annotations

from functools import lru_cache

from cryptography.fernet import Fernet, InvalidToken

from app.core.config import get_settings


class SecretEncryptionError(RuntimeError):
    """Raised when encryption/decryption cannot proceed."""


@lru_cache
def _fernet() -> Fernet:
    key = (get_settings().DB_CONNECTIONS_ENCRYPTION_KEY or "").strip()
    if not key:
        raise SecretEncryptionError(
            "DB_CONNECTIONS_ENCRYPTION_KEY is not set. Generate one with "
            "`python -c \"from cryptography.fernet import Fernet; "
            "print(Fernet.generate_key().decode())\"` and add it to backend/.env "
            "before managing database connections."
        )
    try:
        return Fernet(key.encode())
    except (ValueError, TypeError) as exc:
        raise SecretEncryptionError(
            "DB_CONNECTIONS_ENCRYPTION_KEY is not a valid Fernet key "
            "(expected url-safe base64, 32 bytes)."
        ) from exc


def encrypt_secret(plaintext: str) -> str:
    """Encrypt a UTF-8 secret; returns url-safe base64 ciphertext (str)."""
    return _fernet().encrypt(plaintext.encode("utf-8")).decode("ascii")


def decrypt_secret(ciphertext: str) -> str:
    """Decrypt ciphertext produced by `encrypt_secret`."""
    try:
        return _fernet().decrypt(ciphertext.encode("ascii")).decode("utf-8")
    except InvalidToken as exc:
        raise SecretEncryptionError(
            "Stored secret could not be decrypted — the encryption key may "
            "have changed since it was saved. Re-enter the password in the UI."
        ) from exc
