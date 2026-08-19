"""POST /users/{user_id}/reset-password — real HTTP-level proof (round 8-23A).

The unit suite (tests/unit/test_admin_password_reset_endpoint.py) calls the
route FUNCTION directly, which bypasses FastAPI's dependency pipeline and
its request-body validation entirely. Two things only a real request can
prove, and both are security-critical here:

  1. The PERMISSION gate actually runs. `users.reset_password` must be
     required; holding only `users.update` (which internal:admin has by
     default) must give a 403.
  2. A body that fails FastAPI/Pydantic validation BEFORE the endpoint
     body runs must not echo the submitted password. AdminPasswordResetRequest
     declares new_password as SkipValidation[str] with no Field constraints
     precisely so this path cannot fire on the password's value — this
     asserts that holds.

Same dependency-override pattern as
tests/integration/test_plot_phone_search_http_8_17b.py — real ASGI dispatch
via httpx + ASGITransport, auth/DB faked. No real database, no real user.

The password constants here are obviously-fake local test values; every
assertion about them is a NOT-in check proving they did not leak.
"""
from __future__ import annotations

from collections.abc import AsyncIterator
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import httpx
import pytest
from httpx import ASGITransport

from app.auth.dependencies import get_current_user
from app.db.session import get_db
from app.main import app

_SECRET_PASSWORD = "Correct-Horse-Battery-42"
_TARGET_ID = uuid4()
_CALLER_ID = uuid4()

# Mutated per-test by _grant() before the request is made.
_caller_perms: set[str] = set()


def _caller_user() -> SimpleNamespace:
    return SimpleNamespace(
        id=_CALLER_ID,
        email="admin@example.invalid",
        roles=[SimpleNamespace(name="internal:super_admin")],
        is_active=True,
        auth_version=0,
        _effective_permissions=set(_caller_perms),
    )


def _target_row() -> SimpleNamespace:
    return SimpleNamespace(
        id=_TARGET_ID,
        email="target.user@example.invalid",
        auth_provider="local",
        password_hash="$2b$12$oldhasholdhasholdhasholdhasholdhasholdhasholdhasholdha",
        auth_version=0,
        is_active=True,
    )


class _FakeSession:
    """Minimal async session: execute() always resolves to the target row,
    flush/commit are no-ops. Never touches a real connection."""

    def __init__(self, row: object) -> None:
        self._row = row
        self.added: list[object] = []

    async def execute(self, *_args, **_kwargs):
        row = self._row
        return SimpleNamespace(scalar_one_or_none=lambda: row)

    async def flush(self) -> None:
        return None

    async def commit(self) -> None:
        return None

    async def rollback(self) -> None:
        return None

    def add(self, obj: object) -> None:
        self.added.append(obj)


_session_row: SimpleNamespace | None = None


async def _fake_get_db() -> AsyncIterator[object]:
    yield _FakeSession(_session_row)


@pytest.fixture(autouse=True)
def _override_dependencies():
    global _session_row, _caller_perms
    _session_row = _target_row()
    _caller_perms = {"users.reset_password"}
    app.dependency_overrides[get_current_user] = _caller_user
    app.dependency_overrides[get_db] = _fake_get_db
    yield
    app.dependency_overrides.clear()


def _grant(*keys: str) -> None:
    global _caller_perms
    _caller_perms = set(keys)


async def _post(body: dict, user_id=None) -> httpx.Response:
    target = user_id if user_id is not None else _TARGET_ID
    async with httpx.AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        return await client.post(f"/api/v1/users/{target}/reset-password", json=body)


# --- permission gate ----------------------------------------------------

async def test_caller_without_the_permission_gets_403():
    _grant()
    with patch("app.api.v1.users.ActivityLogger") as logger_cls:
        logger_cls.return_value.log = AsyncMock()
        resp = await _post({"newPassword": _SECRET_PASSWORD})
    assert resp.status_code == 403
    assert _SECRET_PASSWORD not in resp.text


async def test_users_update_alone_cannot_reset_a_password():
    """The whole point of splitting the key: internal:admin holds
    users.update by default and must NOT be able to take over an account."""
    _grant("users.update")
    with patch("app.api.v1.users.ActivityLogger") as logger_cls:
        logger_cls.return_value.log = AsyncMock()
        resp = await _post({"newPassword": _SECRET_PASSWORD})
    assert resp.status_code == 403
    assert _SECRET_PASSWORD not in resp.text


async def test_caller_with_users_reset_password_succeeds():
    _grant("users.reset_password")
    with patch("app.api.v1.users.ActivityLogger") as logger_cls:
        logger_cls.return_value.log = AsyncMock()
        resp = await _post({"newPassword": _SECRET_PASSWORD})
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["authVersion"] == 1
    assert body["sessionsInvalidated"] is True


# --- response never carries the secret ----------------------------------

async def test_success_response_never_contains_the_password_or_a_hash():
    _grant("users.reset_password")
    with patch("app.api.v1.users.ActivityLogger") as logger_cls:
        logger_cls.return_value.log = AsyncMock()
        resp = await _post({"newPassword": _SECRET_PASSWORD})
    assert resp.status_code == 200
    assert _SECRET_PASSWORD not in resp.text
    assert "$2b$" not in resp.text
    assert set(resp.json()) == {"status", "userId", "authVersion", "sessionsInvalidated"}


async def test_weak_password_400_never_echoes_the_value():
    _grant("users.reset_password")
    with patch("app.api.v1.users.ActivityLogger") as logger_cls:
        logger_cls.return_value.log = AsyncMock()
        resp = await _post({"newPassword": "short"})
    assert resp.status_code == 400
    assert "short" not in resp.text


async def test_thai_password_over_72_utf8_bytes_is_400_not_500():
    """Round 8-23A.1 root cause, over real HTTP. 27 characters — far under
    the endpoint's 200-CHARACTER coarse guard — but 77 UTF-8 BYTES, which
    bcrypt refuses outright. Before the shared-helper fix this surfaced as
    an uncaught ValueError -> HTTP 500."""
    _grant("users.reset_password")
    thai_over_limit = "รหัสผ่านยาวมากของฉันนะจ๊ะA1"
    assert len(thai_over_limit) < 200
    assert len(thai_over_limit.encode("utf-8")) > 72
    with patch("app.api.v1.users.ActivityLogger") as logger_cls:
        logger_cls.return_value.log = AsyncMock()
        resp = await _post({"newPassword": thai_over_limit})
    assert resp.status_code == 400, "must be a clean 400, never a 500"
    assert thai_over_limit not in resp.text
    # The row must be untouched — no partial write on a rejected reset.
    assert _session_row.auth_version == 0
    assert _session_row.password_hash.startswith("$2b$12$oldhash")


async def test_thai_password_over_limit_response_never_leaks_byte_counts():
    _grant("users.reset_password")
    thai_over_limit = "รหัสผ่านยาวมากของฉันนะจ๊ะA1"
    with patch("app.api.v1.users.ActivityLogger") as logger_cls:
        logger_cls.return_value.log = AsyncMock()
        resp = await _post({"newPassword": thai_over_limit})
    assert resp.status_code == 400
    # Neither the submitted password nor ITS byte count (77) may appear.
    # (The static policy LIMIT of 72 is allowed — it is a fixed fact about
    # the policy, not a fact about what the caller submitted.)
    assert "77" not in resp.text
    assert thai_over_limit not in resp.text


async def test_thai_password_at_exactly_72_utf8_bytes_succeeds():
    """The boundary is inclusive — exactly 72 bytes must still work."""
    _grant("users.reset_password")
    pw = "Aa1" + "ก" * 23  # 3 + 69 = exactly 72 bytes
    assert len(pw.encode("utf-8")) == 72
    with patch("app.api.v1.users.ActivityLogger") as logger_cls:
        logger_cls.return_value.log = AsyncMock()
        resp = await _post({"newPassword": pw})
    assert resp.status_code == 200
    assert resp.json()["authVersion"] == 1
    assert pw not in resp.text


async def test_unencodable_password_is_400_not_500():
    r"""A lone UTF-16 surrogate cannot be UTF-8 encoded, so it must fail
    closed as a clean 400 rather than an uncaught UnicodeEncodeError.

    Sent as a RAW body, not via httpx's `json=`: httpx encodes the request
    body with json.dumps(ensure_ascii=False).encode("utf-8"), which itself
    raises UnicodeEncodeError client-side, so `json=` cannot even express
    this input. A literal `\uD800` ESCAPE inside the JSON text is valid
    JSON syntax, survives the wire as ASCII, and Python's json.loads
    decodes it into exactly the lone surrogate the server must survive —
    which is how this reaches a real deployment.
    """
    _grant("users.reset_password")
    raw_body = '{"newPassword": "Aa1!\\uD800\\uD800\\uD800\\uD800\\uD800\\uD800\\uD800\\uD800"}'
    with patch("app.api.v1.users.ActivityLogger") as logger_cls:
        logger_cls.return_value.log = AsyncMock()
        async with httpx.AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.post(
                f"/api/v1/users/{_TARGET_ID}/reset-password",
                content=raw_body.encode("ascii"),
                headers={"Content-Type": "application/json"},
            )
    assert resp.status_code == 400, "must fail closed, never a 500"
    assert _session_row.auth_version == 0
    assert _session_row.password_hash.startswith("$2b$12$oldhash")


async def test_missing_field_422_does_not_echo_any_password():
    """FastAPI's own RequestValidationError path — fires before the
    endpoint body. The body carries no password at all here, but the
    handler must still not surface one."""
    resp = await _post({})
    assert resp.status_code == 422
    assert _SECRET_PASSWORD not in resp.text


async def test_wrong_type_password_is_a_clean_400_not_a_500_and_never_echoes():
    """SkipValidation lets a non-string through Pydantic, so the endpoint's
    hand-written isinstance check is the only guard. A 500 here would mean
    an unhandled TypeError inside bcrypt — with the value in the traceback."""
    _grant("users.reset_password")
    with patch("app.api.v1.users.ActivityLogger") as logger_cls:
        logger_cls.return_value.log = AsyncMock()
        resp = await _post({"newPassword": {"nested": _SECRET_PASSWORD}})
    assert resp.status_code == 400
    assert _SECRET_PASSWORD not in resp.text


# --- account rules over real HTTP ---------------------------------------

async def test_azure_ad_target_is_refused_over_http():
    global _session_row
    _session_row = _target_row()
    _session_row.auth_provider = "azure_ad"
    _grant("users.reset_password")
    with patch("app.api.v1.users.ActivityLogger") as logger_cls:
        logger_cls.return_value.log = AsyncMock()
        resp = await _post({"newPassword": _SECRET_PASSWORD})
    assert resp.status_code == 400
    assert "Microsoft" in resp.json()["detail"]
    assert _SECRET_PASSWORD not in resp.text


async def test_missing_target_is_404_over_http():
    global _session_row
    _session_row = None
    _grant("users.reset_password")
    with patch("app.api.v1.users.ActivityLogger") as logger_cls:
        logger_cls.return_value.log = AsyncMock()
        resp = await _post({"newPassword": _SECRET_PASSWORD}, user_id=uuid4())
    assert resp.status_code == 404


async def test_self_reset_is_403_over_http():
    _grant("users.reset_password")
    with patch("app.api.v1.users.ActivityLogger") as logger_cls:
        logger_cls.return_value.log = AsyncMock()
        resp = await _post({"newPassword": _SECRET_PASSWORD}, user_id=_CALLER_ID)
    assert resp.status_code == 403
    assert _SECRET_PASSWORD not in resp.text


async def test_password_is_never_present_in_the_request_url():
    """Body-only by construction — asserts nobody later "helpfully" moves
    it to a query param where it would land in every access log."""
    _grant("users.reset_password")
    with patch("app.api.v1.users.ActivityLogger") as logger_cls:
        logger_cls.return_value.log = AsyncMock()
        async with httpx.AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.post(
                f"/api/v1/users/{_TARGET_ID}/reset-password",
                json={"newPassword": _SECRET_PASSWORD},
            )
    assert _SECRET_PASSWORD not in str(resp.request.url)
