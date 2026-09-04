"""Auto Plot Code generation (round B) — services/plot_code.py's formatter and
series key, plus plot_repository's resolve/next-running behaviour.

Format V1:

    {supplierCode}-{YYMM}-{running}     e.g. "JPS-2605-001"

The month is YYMM in the Christian era (2605 = May 2026), matching the Auto Lot
examples so the two identifiers a plot carries agree about what "2605" means.
The running number counts within a (supplier, YYMM) series ACROSS plots and
restarts at 1 for every new month.

Mock-db unit tests, same style as test_plot_cycle_lot_resolution.py (the Auto
Lot sibling this module deliberately mirrors).
"""
from __future__ import annotations

import datetime
import inspect
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from app.repositories import plot_repository as repo
from app.services.plot_code import (
    PlotCodeSupplierCodeUnusableError,
    PlotCodeTooLongError,
    build_plot_code_series_key,
    format_auto_plot_code,
    month_stamp,
    normalize_supplier_code_for_plot_code,
    preview_auto_plot_code,
)

_MOD = "app.repositories.plot_repository"


def _result(value):
    r = MagicMock()
    r.scalar_one_or_none = MagicMock(return_value=value)
    return r


def _mock_db():
    db = MagicMock()
    db.add = MagicMock()
    db.flush = AsyncMock()
    db.refresh = AsyncMock()
    return db


def _patch_supplier(code: str | None = "JPS"):
    """The supplier code is resolved server-side from the plot's supplier_id."""
    return patch(f"{_MOD}._supplier_code_for_id", AsyncMock(return_value=code))


# --- month_stamp ------------------------------------------------------------

@pytest.mark.parametrize(
    "on,expected",
    [
        (datetime.date(2026, 5, 1), "2605"),
        (datetime.date(2026, 5, 31), "2605"),
        (datetime.date(2026, 11, 30), "2611"),
        (datetime.date(2026, 1, 9), "2601"),      # month is zero-padded
        (datetime.date(2030, 12, 25), "3012"),
        (datetime.date(2100, 3, 4), "0003"),      # century rolls over cleanly
    ],
)
def test_month_stamp_is_yymm_christian_era(on, expected) -> None:
    assert month_stamp(on) == expected


def test_month_stamp_is_not_buddhist_era() -> None:
    """A deliberate, user-confirmed decision: YYMM matches the Auto Lot's own
    examples (lot_number.py renders "2605-SUP010-…"), so a plot and its lot
    never disagree about which year "26" is."""
    assert month_stamp(datetime.date(2026, 5, 1)) == "2605"
    assert month_stamp(datetime.date(2026, 5, 1)) != "6905"


# --- format_auto_plot_code --------------------------------------------------

def test_format_renders_the_contract_example() -> None:
    assert format_auto_plot_code(
        supplier_code="JPS", month="2605", running=1,
    ) == "JPS-2605-001"


def test_running_is_padded_to_three_then_grows_naturally() -> None:
    for running, expected in ((1, "001"), (9, "009"), (38, "038"), (999, "999")):
        assert format_auto_plot_code(
            supplier_code="JPS", month="2605", running=running,
        ).endswith(expected)
    # never truncated, never wrapped back to "001"
    assert format_auto_plot_code(
        supplier_code="JPS", month="2605", running=1000,
    ) == "JPS-2605-1000"


def test_supplier_code_is_embedded_in_full_never_abbreviated() -> None:
    code = format_auto_plot_code(supplier_code="TESTSUP01", month="2605", running=7)
    assert code == "TESTSUP01-2605-007"


def test_an_overlong_code_raises_instead_of_truncating() -> None:
    """Truncating would silently produce a code that could collide with another
    plot's — the whole point of generating them is that they cannot."""
    with pytest.raises(PlotCodeTooLongError):
        format_auto_plot_code(supplier_code="S" * 45, month="2605", running=1)


def test_format_is_keyword_only() -> None:
    """Three same-typed components in a row: a positional call is exactly the
    mistake that would render "2605-JPS-001" and nobody would notice."""
    with pytest.raises(TypeError):
        format_auto_plot_code("JPS", "2605", 1)  # type: ignore[misc]


# --- supplier code normalization -------------------------------------------

@pytest.mark.parametrize(
    "raw,expected",
    [("JPS", "JPS"), ("  jps  ", "JPS"), ("TESTSUP01", "TESTSUP01"),
     ("A-B_C", "A-B_C"), ("9X", "9X")],
)
def test_supplier_code_is_trimmed_and_upper_cased(raw, expected) -> None:
    assert normalize_supplier_code_for_plot_code(raw) == expected


@pytest.mark.parametrize(
    "bad",
    [None, "", "   ", "JP S", "เจียไต๋", "JPS/01", "JPS.01", "-JPS", "JPS+1"],
)
def test_an_unusable_supplier_code_is_refused_not_sanitised(bad) -> None:
    """A plot code becomes the object-storage folder name for that plot's
    photos, so a space or a Thai character in it recreates exactly the problem
    this round exists to fix. Refusing names the supplier to fix; sanitising
    would silently produce a code that no longer matches the supplier."""
    with pytest.raises(PlotCodeSupplierCodeUnusableError) as exc:
        normalize_supplier_code_for_plot_code(bad)
    assert exc.value.supplier_code == bad


def test_case_only_difference_is_one_supplier_not_two() -> None:
    """Upper-casing before the series key matters: "jps" and "JPS" must draw
    from ONE running sequence, or they would mint the same stored code twice
    (plot_code is stored upper-cased)."""
    a = normalize_supplier_code_for_plot_code("jps")
    b = normalize_supplier_code_for_plot_code("JPS")
    assert build_plot_code_series_key(a, "2605") == build_plot_code_series_key(b, "2605")


# --- series key -------------------------------------------------------------

def test_series_key_differs_per_supplier_and_per_month() -> None:
    base = build_plot_code_series_key("JPS", "2605")
    assert base != build_plot_code_series_key("TDS", "2605")   # supplier
    assert base != build_plot_code_series_key("JPS", "2606")   # month


def test_series_key_cannot_be_confused_by_a_dash_in_the_supplier_code() -> None:
    """Length-prefixed, not delimiter-joined: a supplier code may contain "-",
    so a plain join could collapse two distinct pairs into one key and let two
    different series share a running sequence."""
    assert build_plot_code_series_key("A", "B-2605") != build_plot_code_series_key("A-B", "2605")


def test_series_key_is_version_stamped() -> None:
    """A future encoding change must be distinguishable from stored keys rather
    than silently colliding with them (same rule as the Auto Lot's scheme)."""
    assert build_plot_code_series_key("JPS", "2605").startswith("pcv1|")


def test_series_key_is_deterministic_across_calls() -> None:
    """Never Python's hash(), whose value is not stable across processes and
    would break the DB index it is stored in."""
    assert build_plot_code_series_key("JPS", "2605") == build_plot_code_series_key("JPS", "2605")


# --- preview ----------------------------------------------------------------

def test_preview_shows_hashes_for_the_unallocated_running_number() -> None:
    assert preview_auto_plot_code("JPS", "2605") == "JPS-2605-###"


def test_preview_never_raises_on_a_half_filled_form() -> None:
    """A preview must render while the user is still typing — including for a
    supplier code that would ultimately be REFUSED, which is why this does not
    go through normalize_supplier_code_for_plot_code."""
    assert preview_auto_plot_code(None, None) == "<รหัส Supplier>-<ปีเดือน>-###"
    assert preview_auto_plot_code("เจียไต๋", "2605") == "เจียไต๋-2605-###"


# --- _next_plot_code_running_no --------------------------------------------

async def test_next_running_is_max_plus_one() -> None:
    db = MagicMock()
    db.execute = AsyncMock(return_value=_result(37))
    assert await repo._next_plot_code_running_no(db, "series") == 38


async def test_next_running_starts_at_one_for_a_new_series() -> None:
    db = MagicMock()
    db.execute = AsyncMock(return_value=_result(None))
    assert await repo._next_plot_code_running_no(db, "series") == 1


def test_next_running_counts_by_series_and_only_auto_rows() -> None:
    """Source-level guard. Counting by supplier alone would never restart the
    number in a new month; counting legacy rows (NULL series key, hand-typed
    codes) would corrupt the sequence with numbers that mean nothing."""
    src = inspect.getsource(repo._next_plot_code_running_no)
    assert "plot_code_series_key" in src
    assert "PLOT_CODE_SOURCE_AUTO" in src
    assert "func.max(Plot.plot_code_running_no)" in src


# --- _resolve_plot_code -----------------------------------------------------

async def test_blank_code_generates_and_records_the_series() -> None:
    db = _mock_db()
    with _patch_supplier("JPS"), \
         patch(f"{_MOD}._next_plot_code_running_no", AsyncMock(return_value=7)):
        code, source, key, running = await repo._resolve_plot_code(
            db, supplier_id=uuid4(), plot_code=None,
            month_source=datetime.date(2026, 5, 20),
        )
    assert code == "JPS-2605-007"
    assert source == "auto"
    assert running == 7
    assert key == build_plot_code_series_key("JPS", "2605")


@pytest.mark.parametrize("blank", [None, "", "   "])
async def test_every_blank_form_means_generate(blank) -> None:
    """None, "" and "   " must all mean the same thing — a user cannot end up
    with a whitespace plot code by typing spaces."""
    db = _mock_db()
    with _patch_supplier("JPS"), \
         patch(f"{_MOD}._next_plot_code_running_no", AsyncMock(return_value=1)):
        code, source, _key, _running = await repo._resolve_plot_code(
            db, supplier_id=uuid4(), plot_code=blank,
            month_source=datetime.date(2026, 5, 1),
        )
    assert code == "JPS-2605-001"
    assert source == "auto"


async def test_a_supplied_code_is_kept_verbatim_and_joins_no_series() -> None:
    """Round B generates, it does not forbid: an admin mirroring a code that
    already exists on paper still can, and that code takes no running number
    so it can never disturb the generated sequence."""
    db = _mock_db()
    with _patch_supplier("JPS"), \
         patch(f"{_MOD}._next_plot_code_running_no", AsyncMock()) as mk_running:
        code, source, key, running = await repo._resolve_plot_code(
            db, supplier_id=uuid4(), plot_code="  p001  ", month_source=None,
        )
    assert code == "P001"          # trimmed + upper-cased, exactly as before
    assert source == "manual"
    assert key is None and running is None
    mk_running.assert_not_awaited()


async def test_the_month_comes_from_the_planting_date_not_today() -> None:
    """A plot registered in April for a May planting must read 2605 — that is
    the season it will be filed under for the rest of its life."""
    db = _mock_db()
    with _patch_supplier("JPS"), \
         patch(f"{_MOD}._next_plot_code_running_no", AsyncMock(return_value=1)), \
         patch(f"{_MOD}.today_in_bangkok", MagicMock(return_value=datetime.date(2026, 4, 28))):
        code, _s, _k, _r = await repo._resolve_plot_code(
            db, supplier_id=uuid4(), plot_code=None,
            month_source=datetime.date(2026, 5, 3),
        )
    assert code == "JPS-2605-001"


async def test_with_no_planting_date_the_month_falls_back_to_today_in_bangkok() -> None:
    db = _mock_db()
    with _patch_supplier("JPS"), \
         patch(f"{_MOD}._next_plot_code_running_no", AsyncMock(return_value=1)), \
         patch(f"{_MOD}.today_in_bangkok", MagicMock(return_value=datetime.date(2026, 4, 28))):
        code, _s, _k, _r = await repo._resolve_plot_code(
            db, supplier_id=uuid4(), plot_code=None, month_source=None,
        )
    assert code == "JPS-2604-001"


async def test_an_unusable_supplier_code_stops_the_insert() -> None:
    """Raised rather than falling back to some invented code: the caller turns
    this into a 422 naming the supplier, and nothing is written."""
    db = _mock_db()
    with _patch_supplier("เจียไต๋"), \
         patch(f"{_MOD}._next_plot_code_running_no", AsyncMock()) as mk_running:
        with pytest.raises(PlotCodeSupplierCodeUnusableError):
            await repo._resolve_plot_code(
                db, supplier_id=uuid4(), plot_code=None, month_source=None,
            )
    mk_running.assert_not_awaited()      # no running number was burned


async def test_a_missing_supplier_row_is_a_clean_domain_error() -> None:
    """An unresolvable supplier must never become an AttributeError/500, and
    never a plot code built from the string "None"."""
    db = _mock_db()
    with _patch_supplier(None):
        with pytest.raises(PlotCodeSupplierCodeUnusableError):
            await repo._resolve_plot_code(
                db, supplier_id=uuid4(), plot_code=None, month_source=None,
            )


# --- create_plot integration ------------------------------------------------

def _payload(plot_code=None):
    return SimpleNamespace(
        supplier_id=uuid4(), plot_code=plot_code, name="  แปลงใหม่  ",
        village=None, district=None, province=None,
        latitude=None, longitude=None, rai=None,
    )


async def test_create_plot_stores_the_generated_code_and_its_bookkeeping() -> None:
    db = _mock_db()
    with _patch_supplier("JPS"), \
         patch(f"{_MOD}._next_plot_code_running_no", AsyncMock(return_value=3)), \
         patch(f"{_MOD}.generate_qr_key", MagicMock(return_value="QR")):
        plot = await repo.create_plot(
            db, _payload(), month_source=datetime.date(2026, 5, 2),
        )
    assert plot.plot_code == "JPS-2605-003"
    assert plot.plot_code_source == "auto"
    assert plot.plot_code_running_no == 3
    assert plot.plot_code_series_key == build_plot_code_series_key("JPS", "2605")
    db.add.assert_called_once()


async def test_create_plot_with_a_supplied_code_leaves_the_bookkeeping_null() -> None:
    db = _mock_db()
    with _patch_supplier("JPS"), \
         patch(f"{_MOD}.generate_qr_key", MagicMock(return_value="QR")):
        plot = await repo.create_plot(db, _payload(plot_code="p001"))
    assert plot.plot_code == "P001"
    assert plot.plot_code_source == "manual"
    assert plot.plot_code_series_key is None
    assert plot.plot_code_running_no is None
