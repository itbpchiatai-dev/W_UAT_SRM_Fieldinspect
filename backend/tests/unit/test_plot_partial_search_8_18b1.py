"""Partial plot-identity / access-number search (round 8-18B.1).

Round 8-18B split the Plots page's search into two boxes but both halves
still matched too strictly to be useful: `q` was already a substring, but the
access number had to be typed in full and canonical (normalize_thai_mobile),
so an admin who only remembers "5552" could not look anyone up.

This round makes the ADMIN lookup partial on BOTH halves:
  - q            → ILIKE %q% on plot_code / name (unchanged from 8-18B)
  - phone_digits → LIKE  %digits% on plot_access_phones.phone_normalized

The security boundary that must NOT move with it is /public/inspect: granting
inspection access still requires the exact, complete, canonical 10-digit
number (plot_access_phone_repository.lookup_active_access_rows_by_phone +
public_inspection_access.py, both untouched) — asserted at the bottom of this
file, because a partial match there would turn a 4-digit guess into an access
bypass rather than a convenience.

Same two-layer split as test_plot_phone_search_8_17a2.py: repository tests
compile the statement (literal binds) to inspect the WHERE clause without a
real database; endpoint tests call the route directly and patch the
repository.
"""
from __future__ import annotations

import inspect
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest
from fastapi import HTTPException, Response

import app.api.v1.public_inspection_access as public_access_module
import app.repositories.plot_access_phone_repository as phone_repo_module
from app.api.v1.plots import (
    _PHONE_SEARCH_MAX_DIGITS,
    _PHONE_SEARCH_MIN_DIGITS,
    search_plots_by_phone,
)
from app.repositories.plot_repository import list_plots as repo_list_plots
from app.repositories.plot_repository import (
    search_plots_by_phone as repo_search_plots_by_phone,
)
from app.schemas.plot import PlotPhoneSearchRequest


def _compiled(stmt) -> str:
    return str(stmt.compile(compile_kwargs={"literal_binds": True}))


class _FakeResult:
    def __init__(self, rows):
        self._rows = rows

    def scalars(self):
        return SimpleNamespace(all=lambda: self._rows)


def _capturing_db(rows=None):
    captured: dict = {}

    async def _execute(stmt):
        captured["stmt"] = stmt
        return _FakeResult(rows or [])

    return SimpleNamespace(execute=_execute), captured


def _plot(**overrides):
    defaults = dict(
        id=uuid4(), supplier_id=uuid4(), plot_code="SUP010-P002", name="แปลงเมล่อน",
        village=None, district=None, province="Chiang Mai", latitude=None, longitude=None,
        is_active=True, assignments=[], qr_key="qr_key", current_yield_pct=None,
        expected_yield_full=None, expected_yield_unit=None, plant_count=None,
        current_crop=None, current_variety=None, current_lot_no=None, current_planting_date=None,
    )
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


# --- Part A acceptance: partial identity search -----------------------------

async def test_partial_code_002_matches_sup010_p002():
    """Acceptance: รหัส SUP010-P002 ค้นด้วย 002 แล้วพบ."""
    db, captured = _capturing_db()
    await repo_list_plots(db, q="002")
    compiled = _compiled(captured["stmt"])
    # A bare-digit q is a legitimate identity search now — the substring
    # pattern is what makes "002" reach "SUP010-P002".
    assert "lower(plots.plot_code) LIKE lower('%002%')" in compiled


async def test_partial_name_matches_a_thai_substring():
    """Acceptance: ชื่อ แปลงเมล่อน ค้นด้วย เมล่อน แล้วพบ."""
    db, captured = _capturing_db()
    await repo_list_plots(db, q="เมล่อน")
    compiled = _compiled(captured["stmt"])
    assert "lower(plots.name) LIKE lower('%เมล่อน%')" in compiled


async def test_partial_identity_search_is_parameterized_not_string_built():
    """The pattern must go through SQLAlchemy's bind parameters — compiling
    WITHOUT literal_binds must leave a placeholder, not the raw value."""
    db, captured = _capturing_db()
    await repo_list_plots(db, q="002")
    assert "002" not in str(captured["stmt"].compile())


# --- Part B acceptance: partial access-number search ------------------------

async def test_partial_number_5552_matches_a_full_number_containing_it():
    """Acceptance: หมายเลข 0845552162 ค้นด้วย 5552 แล้วพบ."""
    db, captured = _capturing_db()
    await repo_search_plots_by_phone(db, "5552")
    compiled = _compiled(captured["stmt"])
    assert "plot_access_phones.phone_normalized LIKE '%5552%'" in compiled


async def test_partial_number_search_is_parameterized_not_string_built():
    db, captured = _capturing_db()
    await repo_search_plots_by_phone(db, "5552")
    assert "5552" not in str(captured["stmt"].compile())


async def test_partial_number_covers_primary_and_additional_in_one_query():
    """One search, both access types — still no access_type predicate, so a
    เบอร์หลัก and a เบอร์เสริม match on exactly equal terms."""
    db, captured = _capturing_db()
    await repo_search_plots_by_phone(db, "5552")
    compiled = _compiled(captured["stmt"])
    assert "access_type" not in compiled


async def test_partial_number_matches_active_rows_only():
    """A revoked (inactive) access phone must not resurface a plot, partial
    match or not."""
    db, captured = _capturing_db()
    await repo_search_plots_by_phone(db, "5552")
    assert "plot_access_phones.is_active IS true" in _compiled(captured["stmt"])


async def test_partial_number_never_duplicates_a_plot():
    """A fragment can now match SEVERAL of one plot's numbers at once (e.g.
    both its primary and an additional contain "5552") — precisely the case
    where a JOIN would emit the plot twice. EXISTS keeps it to one row."""
    db, captured = _capturing_db()
    await repo_search_plots_by_phone(db, "5552")
    compiled = _compiled(captured["stmt"])
    assert "EXISTS" in compiled
    assert " JOIN " not in compiled
    assert "DISTINCT" not in compiled


async def test_partial_number_and_q_are_an_intersection():
    db, captured = _capturing_db()
    await repo_search_plots_by_phone(db, "5552", q="002")
    compiled = _compiled(captured["stmt"])
    assert "plot_access_phones.phone_normalized LIKE '%5552%'" in compiled
    assert "lower(plots.plot_code) LIKE lower('%002%')" in compiled


async def test_partial_number_keeps_supplier_status_cycle_and_pagination_filters():
    supplier_id = uuid4()
    db, captured = _capturing_db()
    await repo_search_plots_by_phone(
        db, "5552", supplier_id=supplier_id, province="เชียงใหม่", crop="พริก",
        variety="พริกขี้หนู", plot_status="active", cycle_label="jun2026",
        limit=20, offset=40,
    )
    compiled = _compiled(captured["stmt"])
    assert supplier_id.hex in compiled
    assert "เชียงใหม่" in compiled
    assert "plots.current_crop = 'พริก'" in compiled
    assert "plots.current_variety = 'พริกขี้หนู'" in compiled
    assert "plots.is_active IS true" in compiled
    assert "plot_cycles.cycle_label = 'jun2026'" in compiled
    assert "LIMIT 20" in compiled
    assert "OFFSET 40" in compiled


# --- endpoint validation: 4-10 digits, generic 422, no DB touch -------------

@pytest.mark.parametrize("good", ["5552", "12345", "0845552162", "0000"])
async def test_endpoint_accepts_a_4_to_10_digit_fragment(good):
    with patch(
        "app.api.v1.plots.repo.search_plots_by_phone", AsyncMock(return_value=[]),
    ) as mocked:
        await search_plots_by_phone(
            payload=PlotPhoneSearchRequest(phone=good), response=Response(), db=AsyncMock(),
        )
    assert mocked.await_args.args[1] == good


@pytest.mark.parametrize(
    "bad",
    [
        "", " ", "1", "12", "123",            # shorter than 4
        "08455521621", "0845552162123",       # longer than 10
        "084-555-2162", "084 555 2162",       # formatting characters
        "+66845552162", "08a5552162", "abcd", # non-digit
        "١٢٣٤",                                # non-ASCII digits
        "５５５２",                              # full-width digits
        "55%2", "_552", "%",                  # LIKE wildcards must not slip through
    ],
)
async def test_endpoint_rejects_anything_outside_4_to_10_digits(bad):
    with patch("app.api.v1.plots.repo.search_plots_by_phone", AsyncMock()) as mocked:
        with pytest.raises(HTTPException) as exc:
            await search_plots_by_phone(
                payload=PlotPhoneSearchRequest(phone=bad), response=Response(), db=AsyncMock(),
            )
    assert exc.value.status_code == 422
    # Never query the DB on a rejected input.
    mocked.assert_not_awaited()


@pytest.mark.parametrize("bad", ["123", "0845552162123", "08a5552162", "55%2"])
async def test_endpoint_rejection_is_a_fixed_message_that_never_echoes_input(bad):
    with pytest.raises(HTTPException) as exc:
        await search_plots_by_phone(
            payload=PlotPhoneSearchRequest(phone=bad), response=Response(), db=AsyncMock(),
        )
    assert exc.value.detail == "รูปแบบหมายเลขสำหรับเข้าตรวจไม่ถูกต้อง"
    assert bad not in exc.value.detail
    assert not any(ch.isdigit() for ch in exc.value.detail)


@pytest.mark.parametrize("wrong_type", [1234, 5552.0, ["5552"], {"n": "5552"}, None, True])
async def test_endpoint_wrong_type_is_422_generic_and_never_queries(wrong_type):
    with patch("app.api.v1.plots.repo.search_plots_by_phone", AsyncMock()) as mocked:
        with pytest.raises(HTTPException) as exc:
            await search_plots_by_phone(
                payload=PlotPhoneSearchRequest(phone=wrong_type),
                response=Response(),
                db=AsyncMock(),
            )
    assert exc.value.status_code == 422
    assert str(wrong_type) not in exc.value.detail
    mocked.assert_not_awaited()


async def test_endpoint_rejection_still_sets_cache_control_no_store():
    with pytest.raises(HTTPException) as exc:
        await search_plots_by_phone(
            payload=PlotPhoneSearchRequest(phone="123"), response=Response(), db=AsyncMock(),
        )
    assert exc.value.headers["Cache-Control"] == "no-store"


def test_digit_bounds_are_4_and_10():
    assert (_PHONE_SEARCH_MIN_DIGITS, _PHONE_SEARCH_MAX_DIGITS) == (4, 10)


# --- the boundary that must NOT move: /public/inspect stays exact-match -----

def test_public_inspect_lookup_is_still_an_exact_match_never_a_substring():
    """Regression guard for the whole point of this round's blast radius: a
    partial match HERE would let a 4-digit guess reach someone else's plot."""
    src = inspect.getsource(phone_repo_module.lookup_active_access_rows_by_phone)
    assert "PlotAccessPhone.phone_normalized == phone_normalized" in src
    assert "like(" not in src.lower()


def test_public_inspect_still_normalizes_to_a_full_thai_mobile():
    """public_inspection_access.py must still run every submitted number
    through normalize_thai_mobile, which only accepts a complete canonical
    10-digit Thai mobile — no 4-10 digit fragment path exists there."""
    src = inspect.getsource(public_access_module)
    assert "normalize_thai_mobile" in src
    assert "_PHONE_SEARCH_MIN_DIGITS" not in src
    assert "isdigit()" not in src


def test_admin_partial_search_does_not_reuse_the_public_lookup():
    """The two lookups stay separate functions — the admin one must never be
    routed through the public exact-match helper (or vice versa)."""
    src = inspect.getsource(repo_search_plots_by_phone)
    assert "lookup_active_access_rows_by_phone" not in src
