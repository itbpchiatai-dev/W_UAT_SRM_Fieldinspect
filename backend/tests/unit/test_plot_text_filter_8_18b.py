"""Split plot-identity / access-number search (round 8-18B).

The Plots page used to have ONE combined search box whose `q` matched plot
code, plot name AND province, and which also had to guess whether what was
typed was actually an access phone. Round 8-18B splits that into two
independent inputs, which forced two backend changes proven here:

  1. `q` now matches Plot.plot_code / Plot.name ONLY — province is gone from
     it (the page has had a dedicated province filter for several rounds;
     folding province into the free-text box made the two disagree). One
     shared helper (apply_plot_text_filter) is used by list_plots,
     search_plots_by_phone and the template endpoint's _fetch_excluded_plots
     so the three can never drift.
  2. POST /plots/search-by-phone accepts an optional `q`, applied as an
     INTERSECTION with the phone match — so filling both boxes stays ONE
     secure request and the phone never has to fall back to a GET ?q=.

Same two-layer split as test_plot_phone_search_8_17a2.py / test_plot_cycle_
label_filter_8_18.py: repository tests compile the SQLAlchemy statement
(literal binds) to inspect the WHERE clause without a real database;
endpoint tests call the route function directly and patch the repository.
"""
from __future__ import annotations

import typing
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest
from fastapi import HTTPException, Response

from app.api.v1.plots import search_plots_by_phone
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
        id=uuid4(), supplier_id=uuid4(), plot_code="SUP001-P001", name="Plot One",
        village=None, district=None, province="Chiang Mai", latitude=None, longitude=None,
        is_active=True, assignments=[], qr_key="qr_key", current_yield_pct=None,
        expected_yield_full=None, expected_yield_unit=None, plant_count=None,
        current_crop=None, current_variety=None, current_lot_no=None, current_planting_date=None,
    )
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


# --- q matches plot_code / name, and NOT province ---------------------------

async def test_list_plots_q_matches_plot_code():
    db, captured = _capturing_db()
    await repo_list_plots(db, q="SUP001-P002")
    compiled = _compiled(captured["stmt"])
    assert "lower(plots.plot_code) LIKE lower('%SUP001-P002%')" in compiled


async def test_list_plots_q_matches_plot_name():
    db, captured = _capturing_db()
    await repo_list_plots(db, q="หนองบัว")
    compiled = _compiled(captured["stmt"])
    assert "lower(plots.name) LIKE lower('%หนองบัว%')" in compiled


async def test_list_plots_q_no_longer_matches_province():
    """The round 8-18B change: province is a separate filter now, so it must
    NOT also be reachable through the free-text box."""
    db, captured = _capturing_db()
    await repo_list_plots(db, q="เชียงใหม่")
    compiled = _compiled(captured["stmt"])
    assert "lower(plots.province) LIKE" not in compiled


async def test_list_plots_q_is_case_insensitive_ilike_not_like():
    db, captured = _capturing_db()
    await repo_list_plots(db, q="sup001")
    compiled = _compiled(captured["stmt"])
    # .ilike() — SQLAlchemy's default compiler renders it as a
    # lower(col) LIKE lower(pattern) pair, i.e. genuinely case-insensitive,
    # never a bare case-sensitive LIKE against the raw column.
    assert "lower(plots.plot_code) LIKE lower('%sup001%')" in compiled
    assert "lower(plots.name) LIKE lower('%sup001%')" in compiled
    assert "plots.plot_code LIKE" not in compiled
    assert "plots.name LIKE" not in compiled


async def test_list_plots_q_is_trimmed():
    db, captured = _capturing_db()
    await repo_list_plots(db, q="  SUP001  ")
    compiled = _compiled(captured["stmt"])
    assert "'%SUP001%'" in compiled


@pytest.mark.parametrize("blank", [None, "", "   "])
async def test_list_plots_blank_q_applies_no_text_filter(blank):
    db, captured = _capturing_db()
    await repo_list_plots(db, q=blank)
    compiled = _compiled(captured["stmt"])
    assert "LIKE" not in compiled


# --- the dedicated province filter is untouched by the q change ------------

async def test_list_plots_province_filter_still_works_independently():
    db, captured = _capturing_db()
    await repo_list_plots(db, province="เชียงใหม่")
    compiled = _compiled(captured["stmt"])
    # Still the exact, case-folded equality match it has always been —
    # never converted into (or replaced by) the free-text ILIKE.
    assert "lower(plots.province) = 'เชียงใหม่'" in compiled
    assert "lower(plots.province) LIKE" not in compiled


# --- search_plots_by_phone: phone AND q (intersection) ----------------------

async def test_phone_search_q_is_applied_as_intersection():
    """Both predicates must be present in the SAME statement — one AND, not
    two separate queries or an either/or branch."""
    db, captured = _capturing_db()
    await repo_search_plots_by_phone(db, "0812345678", q="SUP001-P002")
    compiled = _compiled(captured["stmt"])
    assert "EXISTS" in compiled
    assert "plot_access_phones.phone_normalized LIKE '%0812345678%'" in compiled
    assert "lower(plots.plot_code) LIKE lower('%SUP001-P002%')" in compiled
    assert "lower(plots.name) LIKE lower('%SUP001-P002%')" in compiled


async def test_phone_search_q_does_not_match_province():
    db, captured = _capturing_db()
    await repo_search_plots_by_phone(db, "0812345678", q="เชียงใหม่")
    compiled = _compiled(captured["stmt"])
    assert "lower(plots.province) LIKE" not in compiled


async def test_phone_search_without_q_applies_no_text_filter():
    db, captured = _capturing_db()
    await repo_search_plots_by_phone(db, "0812345678")
    compiled = _compiled(captured["stmt"])
    # The number's own predicate is a LIKE (round 8-18B.1 partial match), so
    # assert specifically that no PLOT-column text filter was added.
    assert "lower(plots.plot_code) LIKE" not in compiled
    assert "lower(plots.name) LIKE" not in compiled


async def test_phone_search_with_q_still_uses_exists_never_a_join():
    """Adding q must not reintroduce the duplicate-row risk EXISTS avoids —
    a plot with BOTH a matching primary and a matching additional number,
    which also matches the text, still appears exactly once."""
    db, captured = _capturing_db()
    await repo_search_plots_by_phone(db, "0812345678", q="SUP001")
    compiled = _compiled(captured["stmt"])
    assert "EXISTS" in compiled
    assert " JOIN " not in compiled
    assert "DISTINCT" not in compiled


async def test_phone_search_with_q_still_matches_active_phone_rows_only():
    """An inactive (revoked) access phone must not resurface a plot, with or
    without a text filter alongside it."""
    db, captured = _capturing_db()
    await repo_search_plots_by_phone(db, "0812345678", q="SUP001")
    compiled = _compiled(captured["stmt"])
    assert "plot_access_phones.is_active IS true" in compiled


async def test_phone_search_with_q_does_not_branch_on_access_type():
    """เบอร์หลัก and เบอร์เสริม keep equal search rights when q is combined —
    still no access_type predicate anywhere."""
    db, captured = _capturing_db()
    await repo_search_plots_by_phone(db, "0812345678", q="SUP001")
    assert "access_type" not in _compiled(captured["stmt"])


async def test_phone_search_q_combines_with_status_and_cycle_label():
    db, captured = _capturing_db()
    await repo_search_plots_by_phone(
        db, "0812345678", q="SUP001", plot_status="active", cycle_label="jun2026",
    )
    compiled = _compiled(captured["stmt"])
    assert "plots.is_active IS true" in compiled
    assert "plot_cycles.cycle_label = 'jun2026'" in compiled
    assert "lower(plots.plot_code) LIKE lower('%SUP001%')" in compiled


async def test_phone_search_q_combines_with_pagination():
    db, captured = _capturing_db()
    await repo_search_plots_by_phone(db, "0812345678", q="SUP001", limit=20, offset=40)
    compiled = _compiled(captured["stmt"])
    assert "LIMIT 20" in compiled
    assert "OFFSET 40" in compiled
    assert "lower(plots.plot_code) LIKE lower('%SUP001%')" in compiled


# --- endpoint + schema ------------------------------------------------------

def test_schema_accepts_optional_q_defaulting_to_none():
    req = PlotPhoneSearchRequest(phone="0812345678")
    assert req.q is None
    assert PlotPhoneSearchRequest(phone="0812345678", q="SUP001-P002").q == "SUP001-P002"


async def test_endpoint_forwards_q_to_the_repository():
    with patch(
        "app.api.v1.plots.repo.search_plots_by_phone", AsyncMock(return_value=[]),
    ) as mocked:
        await search_plots_by_phone(
            payload=PlotPhoneSearchRequest(phone="0812345678", q="SUP001-P002"),
            response=Response(),
            db=AsyncMock(),
        )
    assert mocked.await_args.kwargs["q"] == "SUP001-P002"


async def test_endpoint_rejects_an_invalid_phone_even_when_q_is_valid():
    """A bad number must send NOTHING — not even the q half on its own,
    which would silently show a wider result set than was asked for."""
    with patch("app.api.v1.plots.repo.search_plots_by_phone", AsyncMock()) as mocked:
        with pytest.raises(HTTPException) as exc:
            await search_plots_by_phone(
                payload=PlotPhoneSearchRequest(phone="99", q="SUP001-P002"),
                response=Response(),
                db=AsyncMock(),
            )
    assert exc.value.status_code == 422
    mocked.assert_not_awaited()


async def test_endpoint_invalid_phone_detail_never_echoes_phone_or_q():
    """PII discipline is unchanged by round 8-18B: the generic 422 must not
    gain an echo of the submitted values now that there are two of them."""
    bad_phone = "0899999999999"
    with pytest.raises(HTTPException) as exc:
        await search_plots_by_phone(
            payload=PlotPhoneSearchRequest(phone=bad_phone, q="SUP001-P002"),
            response=Response(),
            db=AsyncMock(),
        )
    assert bad_phone not in exc.value.detail
    assert "SUP001-P002" not in exc.value.detail


async def test_endpoint_one_plot_appears_once_for_a_phone_and_q_match():
    """End-to-end shape check: the endpoint adds no dedup logic of its own —
    it trusts the EXISTS-based repository query."""
    plot = _plot()
    with patch("app.api.v1.plots.repo.search_plots_by_phone", AsyncMock(return_value=[plot])):
        result = await search_plots_by_phone(
            payload=PlotPhoneSearchRequest(phone="0812345678", q="SUP001-P001"),
            response=Response(),
            db=AsyncMock(),
        )
    assert len(result) == 1
    assert result[0].plot_code == "SUP001-P001"


async def test_endpoint_q_never_reaches_a_url_still_body_only():
    """Structural guard: q was added to the BODY schema, not as a new query
    parameter — adding it must not have opened a GET-shaped path that could
    later be reused for the phone."""
    hints = typing.get_type_hints(search_plots_by_phone)
    assert hints["payload"] is PlotPhoneSearchRequest
    assert "q" not in hints
