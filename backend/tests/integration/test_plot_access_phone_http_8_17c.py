"""PUT /plots/{plotId}/access-phones (+ POST /plots/with-cycle's nested
accessPhones) — real HTTP-level PII-echo regression tests (rounds 8-17C /
8-17C.1).

Round 8-17C fixed PlotAccessPhoneConfig's PII-echo hole: normalization/
validation moved out of a Pydantic model_validator (whose ValueError used to
round-trip through FastAPI's automatic RequestValidationError handler,
echoing the rejected raw phone back in the response's `input` key) into
normalize_and_validate_phone_config(), called by hand inside the endpoint,
converted to a plain-string HTTPException(422, detail=...) that never echoes
anything. tests/unit/test_plot_access_phone_endpoint.py already proves this
at the function-call level (bypassing FastAPI's real body-validation
pipeline); this file proves it over a genuine HTTP request — same
dependency-override pattern as test_plot_phone_search_http_8_17b.py /
test_plot_import_multipart_contract.py. No real DB is touched.
"""
from __future__ import annotations

from collections.abc import AsyncIterator
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import httpx
import pytest
from httpx import ASGITransport

from app.api.deps.scope import get_rls_context
from app.auth.dependencies import get_current_user
from app.db.session import get_db
from app.main import app


def _updater_user() -> SimpleNamespace:
    return SimpleNamespace(
        id=uuid4(),
        roles=[SimpleNamespace(name="internal:admin")],
        supplier_id=None,
        is_supplier_admin=False,
        _effective_permissions={"plots.update", "plots.read"},
        is_active=True,
    )


async def _fake_get_db() -> AsyncIterator[object]:
    # get_plot_for_update must never even be reached for an invalid payload
    # (validated before any DB call) — this session is never awaited against
    # a real connection in the tests below.
    yield object()


async def _noop_rls_context() -> None:
    return None


@pytest.fixture(autouse=True)
def _override_dependencies():
    app.dependency_overrides[get_current_user] = _updater_user
    app.dependency_overrides[get_db] = _fake_get_db
    app.dependency_overrides[get_rls_context] = _noop_rls_context
    yield
    app.dependency_overrides.clear()


async def _put_access_phones(plot_id, body: dict) -> httpx.Response:
    async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        return await client.put(f"/api/v1/plots/{plot_id}/access-phones", json=body)


async def test_invalid_primary_phone_is_422_and_never_echoed_over_real_http():
    secret_bad_phone = "0712345678-not-a-real-number"
    resp = await _put_access_phones(uuid4(), {"primaryPhone": secret_bad_phone})
    assert resp.status_code == 422
    assert secret_bad_phone not in resp.text


async def test_duplicate_additional_phones_is_422_and_never_echoed_over_real_http():
    secret_phone = "0812345678"
    resp = await _put_access_phones(
        uuid4(), {"additionalPhones": [secret_phone, "081-234-5678"]},
    )
    assert resp.status_code == 422
    assert secret_phone not in resp.text


async def test_additional_without_primary_is_422_and_never_echoed_over_real_http():
    secret_phone = "0899998888"
    resp = await _put_access_phones(uuid4(), {"additionalPhones": [secret_phone]})
    assert resp.status_code == 422
    assert secret_phone not in resp.text


async def test_error_response_detail_is_a_plain_string_not_a_pydantic_error_list():
    """Regression guard for the exact shape change round 8-17C made: the
    response body's `detail` must be a bare string now — never Pydantic's
    auto-generated list of error objects (where the `input` PII echo used
    to live)."""
    resp = await _put_access_phones(uuid4(), {"primaryPhone": "0712345678"})
    assert resp.status_code == 422
    body = resp.json()
    assert isinstance(body["detail"], str)


async def test_valid_payload_still_reaches_past_validation_over_real_http():
    """A well-formed body must NOT 422 at validation — it should get as far
    as the Plot lookup, surfacing as a 404 here since the (mocked) lookup
    reports no such plot. Proves the fix didn't make validation overly
    strict."""
    with patch("app.api.v1.plots.repo.get_plot_for_update", AsyncMock(return_value=None)):
        resp = await _put_access_phones(uuid4(), {"primaryPhone": "0845552162"})
    assert resp.status_code == 404


# --- round 8-17C.1: type-confusion payloads — 422, never 500, never echoed --
# Round 8-17C fixed the value-level PII echo but left both fields as plain
# `str`/`list[str]`, so a WRONG-TYPED payload was still rejected by Pydantic
# itself (echoing the whole submitted list back in `input`). Both fields are
# now SkipValidation + hand-checked; these prove the fix over real HTTP.

async def test_primary_phone_as_int_is_422_never_500_never_echoed():
    with patch("app.api.v1.plots.repo.get_plot_for_update", AsyncMock()) as mk:
        resp = await _put_access_phones(uuid4(), {"primaryPhone": 812345678})
    assert resp.status_code == 422
    assert "812345678" not in resp.text
    mk.assert_not_awaited()


async def test_primary_phone_as_object_is_422_never_500_never_echoed():
    with patch("app.api.v1.plots.repo.get_plot_for_update", AsyncMock()) as mk:
        resp = await _put_access_phones(uuid4(), {"primaryPhone": {"secret": "0899998888"}})
    assert resp.status_code == 422
    assert "0899998888" not in resp.text
    mk.assert_not_awaited()


async def test_additional_phones_not_a_list_is_422_never_500_never_echoed():
    secret_phone = "0899998888"
    with patch("app.api.v1.plots.repo.get_plot_for_update", AsyncMock()) as mk:
        resp = await _put_access_phones(uuid4(), {"additionalPhones": secret_phone})
    assert resp.status_code == 422
    assert secret_phone not in resp.text
    mk.assert_not_awaited()


async def test_additional_phones_item_as_int_is_422_never_500_never_echoed():
    with patch("app.api.v1.plots.repo.get_plot_for_update", AsyncMock()) as mk:
        resp = await _put_access_phones(uuid4(), {"additionalPhones": [812345678]})
    assert resp.status_code == 422
    assert "812345678" not in resp.text
    mk.assert_not_awaited()


async def test_additional_phones_item_as_object_is_422_never_500_never_echoed():
    with patch("app.api.v1.plots.repo.get_plot_for_update", AsyncMock()) as mk:
        resp = await _put_access_phones(uuid4(), {"additionalPhones": [{"secret": "0899998888"}]})
    assert resp.status_code == 422
    assert "0899998888" not in resp.text
    mk.assert_not_awaited()


async def test_additional_phones_over_ten_is_422_never_500_never_echoed():
    secret_phone = "0899998888"
    eleven = [f"08100000{i:02d}" for i in range(10)] + [secret_phone]
    with patch("app.api.v1.plots.repo.get_plot_for_update", AsyncMock()) as mk:
        resp = await _put_access_phones(uuid4(), {"additionalPhones": eleven})
    assert resp.status_code == 422
    assert secret_phone not in resp.text
    mk.assert_not_awaited()


# --- round 8-17C.1: same type-confusion payloads, nested under POST
# /plots/with-cycle's optional accessPhones — proves the fix applies to
# BOTH endpoints that accept this schema, not just PUT access-phones.

def _create_admin_user() -> SimpleNamespace:
    return SimpleNamespace(
        id=uuid4(),
        roles=[SimpleNamespace(name="internal:admin")],
        supplier_id=None,
        is_supplier_admin=False,
        _effective_permissions={"plots.create", "plots.read"},
        is_active=True,
    )


async def _post_with_cycle(body: dict) -> httpx.Response:
    app.dependency_overrides[get_current_user] = _create_admin_user
    try:
        async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            return await client.post("/api/v1/plots/with-cycle", json=body)
    finally:
        app.dependency_overrides[get_current_user] = _updater_user


def _with_cycle_body(access_phones: dict) -> dict:
    return {
        "plot": {"supplierId": str(uuid4()), "plotCode": "QA-8-17C1", "name": "QA probe"},
        "cycle": {"cycleLabel": "aug2026"},
        "accessPhones": access_phones,
    }


async def test_with_cycle_nested_primary_phone_as_int_is_422_never_500_never_echoed():
    with patch("app.api.v1.plots.repo.get_plot_by_code", AsyncMock()) as mk_lookup, \
         patch("app.api.v1.plots.repo.create_plot", AsyncMock()) as mk_create:
        resp = await _post_with_cycle(_with_cycle_body({"primaryPhone": 812345678}))
    assert resp.status_code == 422
    assert "812345678" not in resp.text
    mk_lookup.assert_not_awaited()
    mk_create.assert_not_awaited()


async def test_with_cycle_nested_additional_phones_not_a_list_is_422_never_500_never_echoed():
    secret_phone = "0899998888"
    with patch("app.api.v1.plots.repo.get_plot_by_code", AsyncMock()) as mk_lookup, \
         patch("app.api.v1.plots.repo.create_plot", AsyncMock()) as mk_create:
        resp = await _post_with_cycle(_with_cycle_body({"additionalPhones": secret_phone}))
    assert resp.status_code == 422
    assert secret_phone not in resp.text
    mk_lookup.assert_not_awaited()
    mk_create.assert_not_awaited()


async def test_with_cycle_nested_additional_phones_item_as_object_is_422_never_500_never_echoed():
    with patch("app.api.v1.plots.repo.get_plot_by_code", AsyncMock()) as mk_lookup, \
         patch("app.api.v1.plots.repo.create_plot", AsyncMock()) as mk_create:
        resp = await _post_with_cycle(_with_cycle_body({"additionalPhones": [{"secret": "0899998888"}]}))
    assert resp.status_code == 422
    assert "0899998888" not in resp.text
    mk_lookup.assert_not_awaited()
    mk_create.assert_not_awaited()


async def test_with_cycle_nested_additional_phones_over_ten_is_422_never_500_never_echoed():
    secret_phone = "0899998888"
    eleven = [f"08100000{i:02d}" for i in range(10)] + [secret_phone]
    with patch("app.api.v1.plots.repo.get_plot_by_code", AsyncMock()) as mk_lookup, \
         patch("app.api.v1.plots.repo.create_plot", AsyncMock()) as mk_create:
        resp = await _post_with_cycle(_with_cycle_body({"additionalPhones": eleven}))
    assert resp.status_code == 422
    assert secret_phone not in resp.text
    mk_lookup.assert_not_awaited()
    mk_create.assert_not_awaited()
