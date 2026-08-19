"""POST /users (create_user) — bcrypt 72-UTF-8-byte boundary (round 8-23A.1).

create_user already wraps hash_password() in `except PasswordPolicyError`
(app/api/v1/users.py, pre-dates this round) — before this round's fix to
app/auth/password.py, a >72-UTF-8-byte password raised bcrypt's own bare
ValueError there instead, which is NOT caught by that except clause and
escaped as an unhandled 500. This file proves the zero-touch fix: no
change to create_user itself was needed, only to the shared helper.

DB-less: the route function is called directly with a mocked session,
same style as tests/unit/test_admin_password_reset_endpoint.py.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from fastapi import HTTPException

from app.api.v1.users import create_user
from app.schemas.auth import UserCreate

_M = "app.api.v1.users"

_THAI_OVER_LIMIT = "รหัสผ่านยาวมากของฉันนะจ๊ะA1"  # 27 chars / 77 UTF-8 bytes
_GOOD_PASSWORD = "Correct-Horse-Battery-42"


def _caller() -> SimpleNamespace:
    return SimpleNamespace(id=uuid4(), email="admin@example.invalid", roles=[])


def _db_no_existing_email() -> AsyncMock:
    db = AsyncMock()
    db.execute = AsyncMock(
        return_value=SimpleNamespace(scalar_one_or_none=lambda: None)
    )
    db.flush = AsyncMock()
    # Session.add() is SYNC — an AsyncMock would return an un-awaited
    # coroutine and emit a RuntimeWarning on every call.
    db.add = MagicMock()
    return db


def _payload(password: str) -> UserCreate:
    return UserCreate(
        email="new.local.user@example.invalid",
        full_name="New Local User",
        auth_provider="local",
        password=password,
    )


async def test_password_over_72_utf8_bytes_is_400_not_500() -> None:
    db = _db_no_existing_email()
    with pytest.raises(HTTPException) as exc:
        await create_user(
            payload=_payload(_THAI_OVER_LIMIT), request=AsyncMock(),
            user=_caller(), db=db,
        )
    assert exc.value.status_code == 400
    assert _THAI_OVER_LIMIT not in str(exc.value.detail)


async def test_password_over_72_utf8_bytes_never_adds_a_user_row() -> None:
    db = _db_no_existing_email()
    with pytest.raises(HTTPException):
        await create_user(
            payload=_payload(_THAI_OVER_LIMIT), request=AsyncMock(),
            user=_caller(), db=db,
        )
    db.add.assert_not_called()
    db.flush.assert_not_awaited()


async def test_password_within_the_byte_limit_still_creates_successfully() -> None:
    """Regression sanity — the fix must not have broken the ordinary path.
    _load_user (a second, unrelated DB round-trip this test isn't about)
    is stubbed out so this stays focused on the hashing step."""
    db = _db_no_existing_email()
    with patch(f"{_M}._load_user", AsyncMock(return_value=SimpleNamespace())), \
         patch(f"{_M}.ActivityLogger") as logger_cls:
        logger_cls.return_value.log = AsyncMock()
        await create_user(
            payload=_payload(_GOOD_PASSWORD), request=AsyncMock(),
            user=_caller(), db=db,
        )
    # db.add is called twice on the success path: the new User row, then
    # (inside ActivityLogger.log) the audit-log entry — the User row is
    # always the first call.
    assert db.add.call_count >= 1
    created = db.add.call_args_list[0].args[0]
    assert created.password_hash is not None
    assert created.password_hash.startswith("$2b$")
    assert _GOOD_PASSWORD not in created.password_hash
