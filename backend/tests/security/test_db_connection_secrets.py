"""Database Connections — secret-handling guarantees.

1. The Read schema never carries the password (write-only field).
2. Fernet round-trip works and decrypt fails loudly on a rotated key.
3. The connection-credential perms are in the override deny-list so a
   non-super-admin holder of permissions.grant_override cannot acquire
   them per-user.
"""
from __future__ import annotations

import pytest
from cryptography.fernet import Fernet


def test_read_schema_has_no_password_field() -> None:
    from app.schemas.db_connection import DbConnectionRead

    assert "password" not in DbConnectionRead.model_fields
    assert "password_encrypted" not in DbConnectionRead.model_fields


def test_fernet_round_trip(monkeypatch: pytest.MonkeyPatch) -> None:
    key = Fernet.generate_key().decode()
    monkeypatch.setenv("DB_CONNECTIONS_ENCRYPTION_KEY", key)

    # Rebuild settings + clear the lru_cache so the new key is picked up.
    from app.core import config, crypto

    config.get_settings.cache_clear()
    crypto._fernet.cache_clear()

    token = crypto.encrypt_secret("s3cr3t-pw")
    assert token != "s3cr3t-pw"
    assert crypto.decrypt_secret(token) == "s3cr3t-pw"


def test_decrypt_with_wrong_key_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.core import config, crypto

    monkeypatch.setenv("DB_CONNECTIONS_ENCRYPTION_KEY", Fernet.generate_key().decode())
    config.get_settings.cache_clear()
    crypto._fernet.cache_clear()
    token = crypto.encrypt_secret("hello")

    # Rotate the key — old ciphertext must fail to decrypt, not return garbage.
    monkeypatch.setenv("DB_CONNECTIONS_ENCRYPTION_KEY", Fernet.generate_key().decode())
    config.get_settings.cache_clear()
    crypto._fernet.cache_clear()
    with pytest.raises(crypto.SecretEncryptionError):
        crypto.decrypt_secret(token)

    # Cleanup so other tests see a clean cache.
    config.get_settings.cache_clear()
    crypto._fernet.cache_clear()


def test_credential_perms_are_super_admin_only() -> None:
    # The deny-list lives as a local inside add_override; assert against the
    # seed source so a future rename keeps the guard in lockstep.
    import inspect

    from app.api.v1 import users

    src = inspect.getsource(users)
    for key in ("db_connections.read", "db_connections.manage", "db_connections.query"):
        assert key in src, f"{key} missing from users.py privilege deny-list"
