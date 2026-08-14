"""POST /plots/search-by-phone — real HTTP-level PII-echo/header proof
(round 8-17B Part A).

Round 8-17A.2.1 fixed the phone-echo hole INSIDE the endpoint function body
(a hand-written type/length check before normalize_thai_mobile, generic
422). But every existing test for this endpoint (tests/unit/
test_plot_phone_search_8_17a2.py) calls the route FUNCTION directly with an
already-constructed PlotPhoneSearchRequest — that bypasses FastAPI's own
request-body-validation pipeline entirely, so it never proved two things
that only a real HTTP request can:

  1. A body that fails PYDANTIC's own validation (e.g. an unknown field —
     model_config is extra="forbid") is rejected by FastAPI's automatic
     RequestValidationError handler BEFORE the endpoint function ever runs
     — the sub-dependencies (require_permission, get_rls_context) already
     ran by that point, but response.headers["Cache-Control"] = "no-store"
     (set at the top of the endpoint body) NEVER executes. That handler
     must still never echo the phone.
  2. Whether Cache-Control: no-store actually reaches the client on THAT
     response — this is the "small fix if missing" this round's brief
     anticipated.

Same dependency-override pattern as
tests/integration/test_plot_import_multipart_contract.py — real ASGI
dispatch via httpx + ASGITransport, auth/RLS/DB faked (no real DB touched).
"""
from __future__ import annotations

from collections.abc import AsyncIterator
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest
from httpx import ASGITransport

from app.api.deps.scope import get_rls_context
from app.auth.dependencies import get_current_user
from app.db.session import get_db
from app.main import app


def _reader_user() -> SimpleNamespace:
    return SimpleNamespace(
        id=uuid4(),
        roles=[SimpleNamespace(name="internal:admin")],
        supplier_id=None,
        is_supplier_admin=False,
        _effective_permissions={"plots.read"},
        is_active=True,
    )


async def _fake_get_db() -> AsyncIterator[object]:
    # search_plots_by_phone (repository) is patched in every test below, so
    # this session is passed through but never awaited against a real
    # connection.
    yield object()


async def _noop_rls_context() -> None:
    return None


@pytest.fixture(autouse=True)
def _override_dependencies():
    app.dependency_overrides[get_current_user] = _reader_user
    app.dependency_overrides[get_db] = _fake_get_db
    app.dependency_overrides[get_rls_context] = _noop_rls_context
    yield
    app.dependency_overrides.clear()


async def _post_search(body: dict) -> httpx.Response:
    async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        return await client.post("/api/v1/plots/search-by-phone", json=body)


_SECRET_PHONE = "0899998888"


async def test_pydantic_level_rejection_before_endpoint_runs_is_422_never_echoes_phone():
    """extra='forbid' rejects an unknown field at the Pydantic layer, before
    the endpoint function (and its Cache-Control line) ever executes."""
    resp = await _post_search({"phone": _SECRET_PHONE, "notAField": "x"})
    assert resp.status_code == 422
    assert _SECRET_PHONE not in resp.text


async def test_pydantic_level_rejection_still_sets_cache_control_no_store():
    """The gap this round closes: Cache-Control must reach the client even
    on a validation error raised before the endpoint body runs."""
    resp = await _post_search({"phone": _SECRET_PHONE, "notAField": "x"})
    assert resp.status_code == 422
    assert resp.headers.get("cache-control") == "no-store"


async def test_pagination_out_of_range_is_422_never_echoes_phone():
    """limit/offset bounds are also Pydantic-layer — same pre-endpoint
    rejection path, and the phone in the same body must still never echo."""
    resp = await _post_search({"phone": _SECRET_PHONE, "limit": 0})
    assert resp.status_code == 422
    assert _SECRET_PHONE not in resp.text
    assert resp.headers.get("cache-control") == "no-store"


async def test_endpoint_level_invalid_phone_is_422_never_echoes_and_sets_no_store():
    """A body that PASSES Pydantic (phone is SkipValidation[str], so any
    string is accepted at that layer) but fails normalize_thai_mobile inside
    the endpoint — this DOES reach the endpoint body's own Cache-Control
    line."""
    resp = await _post_search({"phone": "not-a-real-phone-number"})
    assert resp.status_code == 422
    assert "not-a-real-phone-number" not in resp.text
    assert resp.headers.get("cache-control") == "no-store"
