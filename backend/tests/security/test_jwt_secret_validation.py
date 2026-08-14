"""Round-4 HIGH-1 regression — JWT_SECRET_KEY must be mandatory,
length-validated, and reject placeholder/low-entropy values."""
from __future__ import annotations

import pytest

# Round 8-16D.1 — weak-JWT rejection is JwtSecretConfigError (a RuntimeError),
# NOT a pydantic ValidationError: a ValueError raised in a validator makes
# pydantic echo the raw settings dict (every env secret in plaintext) into the
# error. Round 8-16D.2 extended the same reasoning to MISSING required
# settings. See SettingsConfigError in app/core/config.py.
from app.core.config import (
    JwtSecretConfigError,
    MissingRequiredSettingsError,
    Settings,
)


def _env(**overrides: str) -> dict[str, str]:
    base = {
        "DB_PASSWORD": "test-only",
        "API_CORS_ORIGINS": "http://localhost:5173",
        "APP_ENV": "dev",
    }
    base.update(overrides)
    return base


def test_jwt_secret_missing_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    # conftest set JWT_SECRET_KEY on the test env; clear it for this test.
    monkeypatch.delenv("JWT_SECRET_KEY", raising=False)
    for k, v in _env().items():
        monkeypatch.setenv(k, v)
    # Round 8-16D.2 CHANGED this path. It previously raised a pydantic
    # ValidationError, and the 8-16D.1 note here argued that was harmless
    # because "a missing field carries no value to echo". That reasoning was
    # wrong: the field is missing, but pydantic's error still captures the
    # whole MERGED settings mapping as its `input`, so DB_PASSWORD,
    # SMTP_PASSWORD and the pepper were all readable via `exc.errors()` —
    # invisible in `str(exc)` (which is truncated), which is how it was
    # missed. A mode="before" preflight now pre-empts field parsing.
    # Full coverage lives in tests/security/test_settings_fail_closed.py.
    with pytest.raises(MissingRequiredSettingsError):
        Settings(_env_file=None)


def test_jwt_secret_short_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("JWT_SECRET_KEY", raising=False)
    for k, v in _env(JWT_SECRET_KEY="abc123" * 4).items():  # 24 chars
        monkeypatch.setenv(k, v)
    with pytest.raises(JwtSecretConfigError, match="at least 32"):
        Settings(_env_file=None)


@pytest.mark.parametrize("placeholder", [
    "changeme" + "x" * 24,
    "secret" + "x" * 26,
    "your-secret-here" + "0" * 16,
])
def test_jwt_secret_placeholder_rejected(
    monkeypatch: pytest.MonkeyPatch, placeholder: str,
) -> None:
    # Length-valid (>=32) but the prefix-only check is too soft for
    # placeholder detection; the validator rejects EXACT placeholder
    # equality (case-insensitive). So we test the entropy guard catches
    # variants that contain a placeholder but pad it out.
    monkeypatch.delenv("JWT_SECRET_KEY", raising=False)
    for k, v in _env(JWT_SECRET_KEY=placeholder).items():
        monkeypatch.setenv(k, v)
    # Either placeholder OR low-entropy will trip the guard — both are
    # acceptable rejections.
    with pytest.raises(JwtSecretConfigError):
        Settings(_env_file=None)


def test_jwt_secret_low_entropy_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("JWT_SECRET_KEY", raising=False)
    # 64 chars but only 1 distinct character
    for k, v in _env(JWT_SECRET_KEY="x" * 64).items():
        monkeypatch.setenv(k, v)
    with pytest.raises(JwtSecretConfigError, match="entropy"):
        Settings(_env_file=None)


def test_jwt_secret_valid_accepted(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("JWT_SECRET_KEY", raising=False)
    valid = "deadbeef0123456789abcdeffedcba9876543210abcdef0123456789cafebabe"
    for k, v in _env(JWT_SECRET_KEY=valid).items():
        monkeypatch.setenv(k, v)
    s = Settings(_env_file=None)
    assert s.JWT_SECRET_KEY == valid
