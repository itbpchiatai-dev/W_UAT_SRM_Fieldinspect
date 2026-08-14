"""HIGH-1 regression — verify the @limiter.limit decorators are actually
present at the source level for the sensitive endpoints.

We read the source file as text rather than importing the route
functions, because importing `app.api.v1.auth` pulls in Pydantic schema
generation that needs the full settings stack — too heavy for a fast
unit test. The check is shape-based but is enough to fail loudly if
someone removes the decorator during a future cleanup pass.

Per-endpoint integration tests that exercise an actual 429 belong in
tests/integration/ where the test harness owns the FastAPI app.
"""
from __future__ import annotations

import inspect

from app.api.v1 import auth as auth_module
from app.api.v1 import user_approval as approval_module

AUTH_SRC = inspect.getsource(auth_module)
APPROVAL_SRC = inspect.getsource(approval_module)


def test_login_rate_limited_5_per_minute() -> None:
    # Ordering: decorator MUST appear in the @router.post + @limiter.limit
    # stack above `async def login(`.
    assert '@limiter.limit("5/minute")' in AUTH_SRC, \
        "Deep-Audit HIGH-1 — /login must rate-limit at 5/minute"
    assert "async def login(" in AUTH_SRC


def test_sso_callback_rate_limited_3_per_minute() -> None:
    assert '@limiter.limit("3/minute")' in AUTH_SRC, \
        "Deep-Audit HIGH-1 — /sso/callback must rate-limit at 3/minute"
    assert "async def sso_callback(" in AUTH_SRC


def test_refresh_rate_limited() -> None:
    # Defence-in-depth on token rotation — value isn\'t mandated by the
    # finding, just presence of the decorator.
    assert "limiter.limit" in AUTH_SRC and "async def refresh(" in AUTH_SRC


def test_approval_endpoints_rate_limited_30_per_minute() -> None:
    assert APPROVAL_SRC.count('@limiter.limit("30/minute")') >= 3, \
        "Deep-Audit HIGH-1 — resolve/approve/reject token endpoints must all rate-limit"
    for fn in ("resolve_token", "approve_via_token", "reject_via_token"):
        assert f"async def {fn}(" in APPROVAL_SRC


def test_rate_limit_singleton_is_imported() -> None:
    assert "from app.core.rate_limit import limiter" in AUTH_SRC
    assert "from app.core.rate_limit import limiter" in APPROVAL_SRC
