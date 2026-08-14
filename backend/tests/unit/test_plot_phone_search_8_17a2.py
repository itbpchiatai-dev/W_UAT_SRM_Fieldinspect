"""POST /api/v1/plots/search-by-phone — secure phone search (round 8-17A.2).

Two layers, same split as test_plot_status_filter.py:
  - repository: compile the SQLAlchemy statement (literal binds) to inspect
    the EXISTS subquery/WHERE clause without a real database.
  - endpoint: call the route function directly and patch the repository,
    matching this repo's existing convention (RLS/permission wiring is on
    the real route's dependencies — verified structurally, not by actually
    triggering FastAPI's DI here).

PII discipline (round 8-17A.2 Part C/F): every test that touches an invalid
phone asserts the rejected value is NEVER echoed back in the HTTPException
detail — the whole reason this endpoint validates by hand instead of via a
Pydantic field/model validator (see PlotPhoneSearchRequest's docstring).
"""
from __future__ import annotations

import inspect
import typing
from types import SimpleNamespace
from unittest.mock import ANY, AsyncMock, patch
from uuid import uuid4

import pydantic
import pytest
from fastapi import HTTPException, Response

import app.api.v1.plots as plots_module
from app.api.v1.plots import search_plots_by_phone
from app.repositories.plot_repository import search_plots_by_phone as repo_search_plots_by_phone
from app.schemas.plot import PlotPhoneSearchRequest


def _compiled(stmt) -> str:
    return str(stmt.compile(compile_kwargs={"literal_binds": True}))


class _FakeResult:
    def __init__(self, rows):
        self._rows = rows

    def scalars(self):
        return SimpleNamespace(all=lambda: self._rows)


def _capturing_db(rows=None):
    """A fake AsyncSession whose .execute captures the statement it was
    given (so the compiled SQL can be inspected) and returns `rows` as if
    they were the query result — no real database involved."""
    captured: dict = {}

    async def _execute(stmt):
        captured["stmt"] = stmt
        return _FakeResult(rows or [])

    return SimpleNamespace(execute=_execute), captured


def _plot(**overrides):
    defaults = dict(
        id=uuid4(), supplier_id=uuid4(), plot_code="SUP001-P001", name="Plot One",
        village=None, district=None, province="Chiang Mai", latitude=None, longitude=None,
        is_active=True, assignments=[], qr_key="qr_key", current_yield_pct=None,
        expected_yield_full=None, expected_yield_unit=None, plant_count=None,
        current_crop=None, current_variety=None, current_lot_no=None, current_planting_date=None,
    )
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


# --- repository: search_plots_by_phone --------------------------------------

async def test_repo_uses_exists_not_a_join():
    """A plot with several matching active access phones must never produce
    duplicate Plot rows — EXISTS, never a JOIN that would need its own
    DISTINCT to get the same guarantee."""
    db, captured = _capturing_db()
    await repo_search_plots_by_phone(db, "0812345678")
    compiled = _compiled(captured["stmt"])
    assert "EXISTS" in compiled
    assert " JOIN " not in compiled


async def test_repo_matches_active_rows_only():
    db, captured = _capturing_db()
    await repo_search_plots_by_phone(db, "0812345678")
    compiled = _compiled(captured["stmt"])
    assert "plot_access_phones.is_active IS true" in compiled


async def test_repo_matches_phone_normalized_as_a_substring():
    """Round 8-18B.1 — this ADMIN lookup became a partial (substring) match;
    it was an exact full-number match through round 8-18B. /public/inspect's
    own lookup (plot_access_phone_repository) is unchanged and still exact."""
    db, captured = _capturing_db()
    await repo_search_plots_by_phone(db, "0812345678")
    compiled = _compiled(captured["stmt"])
    assert "plot_access_phones.phone_normalized LIKE '%0812345678%'" in compiled


async def test_repo_does_not_branch_on_access_type():
    """Both 'primary' and 'additional' carry equal search rights — the
    EXISTS subquery must never filter on access_type at all."""
    db, captured = _capturing_db()
    await repo_search_plots_by_phone(db, "0812345678")
    compiled = _compiled(captured["stmt"])
    assert "access_type" not in compiled


async def test_repo_forwards_supplier_province_crop_variety_filters():
    supplier_id = uuid4()
    db, captured = _capturing_db()
    await repo_search_plots_by_phone(
        db, "0812345678", supplier_id=supplier_id, province="เชียงใหม่",
        crop="พริก", variety="พริกขี้หนู",
    )
    compiled = _compiled(captured["stmt"])
    # Postgres UUID literal binds render as bare hex (no hyphens).
    assert supplier_id.hex in compiled
    assert "เชียงใหม่" in compiled
    assert "plots.current_crop = 'พริก'" in compiled
    assert "plots.current_variety = 'พริกขี้หนู'" in compiled


async def test_repo_plot_status_active_filters_to_true():
    db, captured = _capturing_db()
    await repo_search_plots_by_phone(db, "0812345678", plot_status="active")
    assert "plots.is_active IS true" in _compiled(captured["stmt"])


async def test_repo_plot_status_inactive_filters_to_false():
    db, captured = _capturing_db()
    await repo_search_plots_by_phone(db, "0812345678", plot_status="inactive")
    assert "plots.is_active IS false" in _compiled(captured["stmt"])


async def test_repo_plot_status_all_applies_no_is_active_filter():
    db, captured = _capturing_db()
    await repo_search_plots_by_phone(db, "0812345678", plot_status="all")
    compiled = _compiled(captured["stmt"])
    assert "plots.is_active IS" not in compiled


async def test_repo_forwards_limit_and_offset():
    db, captured = _capturing_db()
    await repo_search_plots_by_phone(db, "0812345678", limit=20, offset=40)
    compiled = _compiled(captured["stmt"])
    assert "LIMIT 20" in compiled
    assert "OFFSET 40" in compiled


async def test_repo_returns_rows_from_query_result():
    plot = _plot()
    db, _ = _capturing_db(rows=[plot])
    result = await repo_search_plots_by_phone(db, "0812345678")
    assert result == [plot]


async def test_repo_eager_loads_same_relations_as_list_plots():
    """PlotSummary needs assignments/supplier/active_cycle/access_phones —
    same eager-loading shape as list_plots, so _to_summary works identically
    on either result set."""
    db, captured = _capturing_db()
    await repo_search_plots_by_phone(db, "0812345678")
    paths = " ".join(str(o.context[0].path) for o in captured["stmt"]._with_options)
    for rel in ("Plot.assignments", "Plot.supplier", "Plot.active_cycle", "Plot.access_phones"):
        assert rel in paths


# --- endpoint: search_plots_by_phone ----------------------------------------

async def test_endpoint_forwards_the_validated_digits_to_the_repository():
    plot = _plot()
    with patch(
        "app.api.v1.plots.repo.search_plots_by_phone", AsyncMock(return_value=[plot]),
    ) as mocked:
        result = await search_plots_by_phone(
            payload=PlotPhoneSearchRequest(phone="0812345678"),
            response=Response(),
            db=AsyncMock(),
        )
    mocked.assert_awaited_once_with(
        ANY, "0812345678", supplier_id=None, province=None, crop=None, variety=None,
        limit=50, offset=0, plot_status="all", cycle_label=None, q=None,
    )
    assert len(result) == 1


async def test_endpoint_forwards_every_optional_filter():
    supplier_id = uuid4()
    with patch(
        "app.api.v1.plots.repo.search_plots_by_phone", AsyncMock(return_value=[]),
    ) as mocked:
        await search_plots_by_phone(
            payload=PlotPhoneSearchRequest(
                phone="0812345678", supplierId=supplier_id, province="เชียงใหม่",
                crop="พริก", variety="พริกขี้หนู", plotStatus="active", limit=10, offset=20,
                cycleLabel="jun2026", q="SUP001-P002",
            ),
            response=Response(),
            db=AsyncMock(),
        )
    mocked.assert_awaited_once_with(
        ANY, "0812345678", supplier_id=supplier_id, province="เชียงใหม่",
        crop="พริก", variety="พริกขี้หนู", limit=10, offset=20, plot_status="active",
        cycle_label="jun2026", q="SUP001-P002",
    )


async def test_endpoint_one_phone_many_plots_returns_all_without_dedup_logic():
    """The endpoint itself does no deduplication — it trusts the repository's
    EXISTS-based query to already return each plot at most once."""
    p1, p2 = _plot(plot_code="P001"), _plot(plot_code="P002")
    with patch("app.api.v1.plots.repo.search_plots_by_phone", AsyncMock(return_value=[p1, p2])):
        result = await search_plots_by_phone(
            payload=PlotPhoneSearchRequest(phone="0812345678"), response=Response(), db=AsyncMock(),
        )
    assert len(result) == 2
    assert {r.plot_code for r in result} == {"P001", "P002"}


# Round 8-18B.1 — "12345"/"0512345678" are no longer invalid: any 4-10 digit
# fragment is a legitimate partial lookup now. What stays invalid is anything
# non-digit, too short (<4), or too long (>10).
@pytest.mark.parametrize("bad", ["", " ", "123", "not-a-phone", "089123456789012", "081-234-5678"])
async def test_endpoint_invalid_phone_is_422_generic_never_echoed(bad):
    # Round 8-17A.2.1 — phone is schema-level Any now (see PlotPhoneSearchRequest
    # docstring), so constructing the payload with "" must NOT raise at
    # construction time either; the 422 only happens inside the endpoint.
    payload = PlotPhoneSearchRequest(phone=bad)
    with patch("app.api.v1.plots.repo.search_plots_by_phone", AsyncMock()) as mocked:
        with pytest.raises(HTTPException) as exc:
            await search_plots_by_phone(payload=payload, response=Response(), db=AsyncMock())
    assert exc.value.status_code == 422
    # Generic message — the rejected value must never appear in the detail.
    assert bad not in exc.value.detail if bad else True
    assert not any(ch.isdigit() for ch in exc.value.detail)  # no stray digits leaked
    mocked.assert_not_awaited()


# --- round 8-17A.2.1: pagination bounds + oversized/wrong-type phone --------
# Found in the 8-17A.2 review: the old `phone: str = Field(min_length=1,
# max_length=32)` meant an oversized or wrong-type phone was rejected by
# Pydantic itself, BEFORE the endpoint ran — and FastAPI's automatic
# RequestValidationError handler echoes the rejected value in `input`. These
# tests prove that path no longer exists: phone is unchecked at the schema
# layer and only ever rejected by hand, generically, inside the endpoint.


def test_schema_phone_oversized_does_not_raise_at_construction():
    """A 33+ char phone must reach the endpoint (not be rejected — and
    therefore echoed — by a Pydantic max_length constraint)."""
    huge = "0" * 5000
    req = PlotPhoneSearchRequest(phone=huge)
    assert req.phone == huge


async def test_endpoint_oversized_phone_is_422_generic_never_echoed():
    huge = "0812345678" * 10  # 100 digits — well past the old 32-char cap
    with patch("app.api.v1.plots.repo.search_plots_by_phone", AsyncMock()) as mocked:
        with pytest.raises(HTTPException) as exc:
            await search_plots_by_phone(
                payload=PlotPhoneSearchRequest(phone=huge), response=Response(), db=AsyncMock(),
            )
    assert exc.value.status_code == 422
    assert huge not in exc.value.detail
    mocked.assert_not_awaited()


@pytest.mark.parametrize("wrong_type", [12345678, 845552162.0, ["0812345678"], {"n": "0812345678"}, None, True])
def test_schema_wrong_type_phone_does_not_raise_at_construction(wrong_type):
    """phone: Any accepts any JSON-decoded type without a Pydantic-level
    rejection (and therefore without a Pydantic-level echo)."""
    req = PlotPhoneSearchRequest(phone=wrong_type)
    assert req.phone == wrong_type


@pytest.mark.parametrize("wrong_type", [12345678, 845552162.0, ["0812345678"], {"n": "0812345678"}, None, True])
async def test_endpoint_wrong_type_phone_is_422_generic_never_echoed(wrong_type):
    with patch("app.api.v1.plots.repo.search_plots_by_phone", AsyncMock()) as mocked:
        with pytest.raises(HTTPException) as exc:
            await search_plots_by_phone(
                payload=PlotPhoneSearchRequest(phone=wrong_type), response=Response(), db=AsyncMock(),
            )
    assert exc.value.status_code == 422
    assert str(wrong_type) not in exc.value.detail
    mocked.assert_not_awaited()


@pytest.mark.parametrize("bad_limit", [0, -1, 101, 1000])
def test_schema_limit_out_of_range_raises_validation_error(bad_limit):
    """limit is ordinary Field(ge=1, le=100) — a Pydantic auto-422 (with the
    number echoed) is expected and fine here; limit/offset aren't PII."""
    with pytest.raises(pydantic.ValidationError):
        PlotPhoneSearchRequest(phone="0812345678", limit=bad_limit)


@pytest.mark.parametrize("bad_offset", [-1, -100])
def test_schema_offset_out_of_range_raises_validation_error(bad_offset):
    with pytest.raises(pydantic.ValidationError):
        PlotPhoneSearchRequest(phone="0812345678", offset=bad_offset)


def test_schema_limit_default_is_50():
    req = PlotPhoneSearchRequest(phone="0812345678")
    assert req.limit == 50


def test_schema_offset_default_is_0():
    req = PlotPhoneSearchRequest(phone="0812345678")
    assert req.offset == 0


@pytest.mark.parametrize("good_limit", [1, 50, 100])
def test_schema_limit_boundary_values_are_accepted(good_limit):
    req = PlotPhoneSearchRequest(phone="0812345678", limit=good_limit)
    assert req.limit == good_limit


async def test_endpoint_sets_cache_control_no_store():
    response = Response()
    with patch("app.api.v1.plots.repo.search_plots_by_phone", AsyncMock(return_value=[])):
        await search_plots_by_phone(
            payload=PlotPhoneSearchRequest(phone="0812345678"), response=response, db=AsyncMock(),
        )
    assert response.headers["Cache-Control"] == "no-store"


async def test_endpoint_sets_cache_control_even_on_invalid_phone():
    """no-store must apply before the phone is even validated — an early
    422 response must not be cacheable either."""
    response = Response()
    with pytest.raises(HTTPException):
        await search_plots_by_phone(
            payload=PlotPhoneSearchRequest(phone="bad"), response=response, db=AsyncMock(),
        )
    assert response.headers["Cache-Control"] == "no-store"


async def test_endpoint_response_shape_is_plot_summary():
    plot = _plot()
    with patch("app.api.v1.plots.repo.search_plots_by_phone", AsyncMock(return_value=[plot])):
        result = await search_plots_by_phone(
            payload=PlotPhoneSearchRequest(phone="0812345678"), response=Response(), db=AsyncMock(),
        )
    row = result[0]
    # Same PlotSummary fields GET /plots returns — no phone/secret leaked in
    # the response shape itself.
    assert hasattr(row, "plot_code")
    assert not hasattr(row, "phone")
    assert not hasattr(row, "password")
    assert not hasattr(row, "credential")


# --- schema: PlotPhoneSearchRequest ------------------------------------------

def test_schema_phone_field_has_no_validator_that_would_echo_it():
    """Regression guard for the exact PII-echo bug this endpoint avoids: if
    a future edit adds a field/model_validator to `phone` that raises
    ValueError, an invalid phone would round-trip through FastAPI's
    automatic RequestValidationError handler, which echoes the rejected
    value in each error's `input` key. Constructing the schema directly
    with an obviously-invalid phone string must NOT raise — proving
    normalization still only happens by hand inside the endpoint."""
    req = PlotPhoneSearchRequest(phone="not a phone number")
    assert req.phone == "not a phone number"


def test_schema_forbids_unknown_fields():
    with pytest.raises(Exception):
        PlotPhoneSearchRequest(phone="0812345678", extraField="nope")


def test_schema_plot_status_defaults_to_all():
    req = PlotPhoneSearchRequest(phone="0812345678")
    assert req.plot_status == "all"


# --- structural: RLS/permission wiring, POST-only, body-only ---------------

def test_route_requires_plots_read_and_rls_context():
    src = inspect.getsource(plots_module)
    idx = src.index('@router.post("/search-by-phone"')
    end = src.index("async def search_plots_by_phone(")
    block = src[idx:end]
    assert "PermissionKey.PLOTS_READ" in block
    assert "get_rls_context" in block


def test_route_is_post_not_get():
    matched = [r for r in plots_module.router.routes if getattr(r, "path", "") == "/search-by-phone"]
    assert len(matched) == 1
    assert matched[0].methods == {"POST"}


def test_route_takes_a_body_payload_not_query_params():
    """The phone must arrive as a Pydantic request-body model, never a
    FastAPI Query(...)/plain str default — that's what would let it leak
    into a GET-style query string."""
    hints = typing.get_type_hints(search_plots_by_phone)
    assert hints["payload"] is PlotPhoneSearchRequest


def test_route_response_model_is_plot_summary_list():
    matched = [r for r in plots_module.router.routes if getattr(r, "path", "") == "/search-by-phone"]
    assert matched[0].response_model == list[plots_module.PlotSummary]
