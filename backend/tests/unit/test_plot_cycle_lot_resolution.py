"""Auto/Manual/legacy lot resolution inside plot_cycle_repository.create_cycle
/ update_cycle, plus the next-running-number helper.

Round 8-5A introduced the V1 formula {PO}-{plotCode}-{running}; round 8-12A
replaced it with V2:

    {cycleLabel}-{supplierCode}-{pCode}-{running}   (running >= 3 digits)

and moved the running sequence's scope from (plot, PO) to
(supplier, cycleLabel, pCode) — counted ACROSS plots, because V2's formula has
no plot code and two plots in one series would otherwise mint identical lots.

Mock-db unit tests, same style as test_plot_cycle_repository.py.
"""
from __future__ import annotations

import inspect
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from app.repositories import plot_cycle_repository as repo
from app.services.lot_number import AutoLotMissingComponentError

_MOD = "app.repositories.plot_cycle_repository"

SUPPLIER_CODE = "SUP010"


def _result(value):
    r = MagicMock()
    r.scalar_one_or_none = MagicMock(return_value=value)
    return r


def _plot(plot_code: str = "SUP010-P001"):
    return SimpleNamespace(id=uuid4(), plot_code=plot_code, supplier_id=uuid4())


def _mock_db():
    db = MagicMock()
    db.add = MagicMock()
    db.flush = AsyncMock()
    return db


def _patch_supplier(code: str | None = SUPPLIER_CODE):
    """The supplier code is resolved server-side from the plot's supplier_id."""
    return patch(f"{_MOD}._supplier_code_for_plot", AsyncMock(return_value=code))


# --- _next_lot_running_no: the V2 series scope ------------------------------

async def test_next_running_is_max_plus_one() -> None:
    db = MagicMock()
    db.execute = AsyncMock(return_value=_result(4))
    assert await repo._next_lot_running_no(db, "series-key") == 5


async def test_next_running_starts_at_one_when_none() -> None:
    db = MagicMock()
    db.execute = AsyncMock(return_value=_result(None))
    # A brand-new (supplier, cycleLabel, pCode) series → 1.
    assert await repo._next_lot_running_no(db, "series-key") == 1


def test_next_running_counts_by_series_not_by_plot() -> None:
    """The scope must be the series key, NOT plot_id — otherwise two plots of
    one supplier sharing a series would both mint running 1 and produce the
    same lot number (V2 has no plot code to tell them apart)."""
    src = inspect.getsource(repo._next_lot_running_no)
    assert "auto_lot_series_key" in src
    assert "LOT_SOURCE_AUTO" in src
    assert "func.max(PlotCycle.lot_running_no)" in src
    assert "PlotCycle.plot_id" not in src


# --- create_cycle: MANUAL wins ----------------------------------------------

async def test_create_manual_lot_sets_source_manual_no_generator() -> None:
    plot = _plot()
    db = _mock_db()
    with patch(f"{_MOD}._next_cycle_no", AsyncMock(return_value=1)), \
         patch(f"{_MOD}._next_lot_running_no", AsyncMock()) as mk_running, \
         _patch_supplier(), \
         patch(f"{_MOD}.sync_plot_mirror_from_cycle", AsyncMock()):
        cycle = await repo.create_cycle(
            db, plot, lot_no="  HAND-01  ", po_number="PO9",
            cycle_label="2605", p_code="WM-141",
        )

    # Manual lot is stored verbatim (trimmed), Auto generator is never consulted.
    assert cycle.lot_no == "HAND-01"
    assert cycle.lot_no_source == "manual"
    assert cycle.lot_running_no is None
    assert cycle.auto_lot_series_key is None      # manual rows join no series
    mk_running.assert_not_awaited()
    # PO is still normalized + stored (manual lot doesn't discard the PO).
    assert cycle.po_number == "PO9"


# --- create_cycle: AUTO V2 --------------------------------------------------

async def test_create_blank_lot_generates_auto_v2() -> None:
    plot = _plot()
    db = _mock_db()
    with patch(f"{_MOD}._next_cycle_no", AsyncMock(return_value=1)), \
         patch(f"{_MOD}._next_lot_running_no", AsyncMock(return_value=3)), \
         _patch_supplier(), \
         patch(f"{_MOD}.sync_plot_mirror_from_cycle", AsyncMock()):
        cycle = await repo.create_cycle(
            db, plot, lot_no=None, cycle_label="2605", p_code="WM-141",
            po_number="po25001",
        )

    # The exact contract example from the round brief.
    assert cycle.lot_no == "2605-SUP010-WM-141-003"
    assert cycle.lot_no_source == "auto"
    assert cycle.lot_running_no == 3
    assert cycle.auto_lot_series_key is not None
    # PO is still stored and normalized — it just no longer builds the lot.
    assert cycle.po_number == "PO25001"
    assert "PO25001" not in cycle.lot_no


async def test_create_auto_v2_succeeds_with_no_po_at_all() -> None:
    """Round 8-13A — PO Number is optional on every new-cycle flow. Auto Lot
    V2 never needed it (see the two tests above's PO already being absent
    from the rendered lot); this proves creation with po_number=None all the
    way through — not just "the lot text happens to omit PO", but "a cycle
    with NO PO on file at all" — succeeds and produces a normal V2 lot."""
    plot = _plot()
    db = _mock_db()
    with patch(f"{_MOD}._next_cycle_no", AsyncMock(return_value=1)), \
         patch(f"{_MOD}._next_lot_running_no", AsyncMock(return_value=1)), \
         _patch_supplier(), \
         patch(f"{_MOD}.sync_plot_mirror_from_cycle", AsyncMock()):
        cycle = await repo.create_cycle(
            db, plot, lot_no=None, cycle_label="2605", p_code="WM-141",
            po_number=None,
        )

    assert cycle.po_number is None
    assert cycle.lot_no == "2605-SUP010-WM-141-001"
    assert cycle.lot_no_source == "auto"
    assert cycle.lot_running_no == 1
    assert cycle.auto_lot_series_key is not None


async def test_create_manual_lot_succeeds_with_no_po_at_all() -> None:
    """Round 8-13A — Manual Lot mode with po_number=None: the value is used
    verbatim, PO stays None (never defaulted/invented), and the Auto
    generator is never consulted."""
    plot = _plot()
    db = _mock_db()
    with patch(f"{_MOD}._next_lot_running_no", AsyncMock()) as mk_running, \
         patch(f"{_MOD}._next_cycle_no", AsyncMock(return_value=1)), \
         _patch_supplier(), \
         patch(f"{_MOD}.sync_plot_mirror_from_cycle", AsyncMock()):
        cycle = await repo.create_cycle(
            db, plot, lot_no="HAND-NO-PO", po_number=None, p_code="X",
        )

    assert cycle.po_number is None
    assert cycle.lot_no == "HAND-NO-PO"
    assert cycle.lot_no_source == "manual"
    assert cycle.lot_running_no is None
    assert cycle.auto_lot_series_key is None
    mk_running.assert_not_awaited()


async def test_create_auto_keeps_full_p_code_and_arbitrary_label() -> None:
    plot = _plot()
    db = _mock_db()
    with patch(f"{_MOD}._next_cycle_no", AsyncMock(return_value=1)), \
         patch(f"{_MOD}._next_lot_running_no", AsyncMock(return_value=4)), \
         _patch_supplier(), \
         patch(f"{_MOD}.sync_plot_mirror_from_cycle", AsyncMock()):
        cycle = await repo.create_cycle(
            db, plot, cycle_label="  26-may  ", p_code="  WM-141  ",
        )
    # label trimmed but NOT parsed/reformatted; pCode kept in full.
    assert cycle.lot_no == "26-may-SUP010-WM-141-004"
    assert cycle.cycle_label == "26-may"
    assert cycle.p_code == "WM-141"


async def test_create_auto_running_grows_past_999() -> None:
    plot = _plot()
    db = _mock_db()
    with patch(f"{_MOD}._next_cycle_no", AsyncMock(return_value=2)), \
         patch(f"{_MOD}._next_lot_running_no", AsyncMock(return_value=1000)), \
         _patch_supplier(), \
         patch(f"{_MOD}.sync_plot_mirror_from_cycle", AsyncMock()):
        cycle = await repo.create_cycle(db, plot, cycle_label="MAY26", p_code="ABC")

    assert cycle.lot_no == "MAY26-SUP010-ABC-1000"   # never truncated/wrapped
    assert cycle.lot_running_no == 1000


# --- create_cycle: series identity ------------------------------------------

async def _series_key_for(cycle_label: str, p_code: str, supplier: str = SUPPLIER_CODE):
    plot = _plot()
    db = _mock_db()
    with patch(f"{_MOD}._next_cycle_no", AsyncMock(return_value=1)), \
         patch(f"{_MOD}._next_lot_running_no", AsyncMock(return_value=1)), \
         _patch_supplier(supplier), \
         patch(f"{_MOD}.sync_plot_mirror_from_cycle", AsyncMock()):
        cycle = await repo.create_cycle(
            db, plot, cycle_label=cycle_label, p_code=p_code,
        )
    return cycle.auto_lot_series_key


async def test_series_key_differs_per_supplier_label_and_p_code() -> None:
    base = await _series_key_for("2605", "WM-141")
    assert base != await _series_key_for("2605", "WM-142")            # pCode
    assert base != await _series_key_for("26-may", "WM-141")          # label
    assert base != await _series_key_for("2605", "WM-141", "SUP011")  # supplier


async def test_series_key_cannot_be_confused_by_a_dash_in_a_component() -> None:
    """("26", "may-1") and ("26-may", "1") must NOT collapse into one series —
    they are different series and would otherwise share a running sequence."""
    assert await _series_key_for("26", "may-1") != await _series_key_for("26-may", "1")


async def test_two_plots_in_one_series_share_the_running_sequence() -> None:
    """Running continues ACROSS plots within a series: the query is keyed by
    the series alone, so plot B's next number follows plot A's."""
    db = MagicMock()
    db.execute = AsyncMock(return_value=_result(2))
    key = await _series_key_for("2605", "WM-141")
    assert await repo._next_lot_running_no(db, key) == 3


# --- create_cycle: missing components ---------------------------------------

@pytest.mark.parametrize(
    "label,code,expect_missing",
    [
        (None, "WM-141", "cycleLabel"),
        ("2605", None, "pCode"),
        ("   ", "WM-141", "cycleLabel"),
        ("2605", "  ", "pCode"),
    ],
)
async def test_create_blank_lot_without_auto_components_is_rejected(
    label, code, expect_missing,
) -> None:
    """Round 8-12A.1 — a blank lotNo REQUESTS an Auto Lot, so a missing
    component is an error, not a silent NULL lot.

    Round 8-12A returned (None, None, None, None) here, which created ACTIVE
    cycles carrying no lot identifier at all: the caller asked for a lot and
    got nothing, with no error to act on. Nothing is written now — the
    caller's transaction rolls back."""
    plot = _plot()
    db = _mock_db()
    with patch(f"{_MOD}._next_cycle_no", AsyncMock(return_value=1)), \
         patch(f"{_MOD}._next_lot_running_no", AsyncMock()) as mk_running, \
         _patch_supplier(), \
         patch(f"{_MOD}.sync_plot_mirror_from_cycle", AsyncMock()):
        with pytest.raises(AutoLotMissingComponentError) as exc:
            await repo.create_cycle(db, plot, lot_no=None, cycle_label=label, p_code=code)

    assert expect_missing in exc.value.missing
    mk_running.assert_not_awaited()      # no running number was burned
    db.add.assert_not_called()           # no cycle row was staged


async def test_create_blank_lot_with_both_components_missing_reports_both() -> None:
    plot = _plot()
    db = _mock_db()
    with patch(f"{_MOD}._next_cycle_no", AsyncMock(return_value=1)), \
         _patch_supplier(), \
         patch(f"{_MOD}.sync_plot_mirror_from_cycle", AsyncMock()):
        with pytest.raises(AutoLotMissingComponentError) as exc:
            await repo.create_cycle(db, plot, lot_no=None, cycle_label=None, p_code=None)
    assert exc.value.missing == ("cycleLabel", "pCode")


async def test_create_auto_needs_a_resolvable_supplier_code() -> None:
    """An unresolvable supplier is a clean domain error too — never a lotless
    cycle, and never an AttributeError/500."""
    plot = _plot()
    db = _mock_db()
    with patch(f"{_MOD}._next_cycle_no", AsyncMock(return_value=1)), \
         _patch_supplier(None), \
         patch(f"{_MOD}.sync_plot_mirror_from_cycle", AsyncMock()):
        with pytest.raises(AutoLotMissingComponentError) as exc:
            await repo.create_cycle(db, plot, cycle_label="2605", p_code="WM-141")
    assert exc.value.missing == ("supplierCode",)
    db.add.assert_not_called()


async def test_manual_lot_still_works_without_any_auto_component() -> None:
    """A Manual lot is complete on its own — it must NOT be blocked by the new
    Auto-component requirement."""
    plot = _plot()
    db = _mock_db()
    with patch(f"{_MOD}._next_cycle_no", AsyncMock(return_value=1)), \
         _patch_supplier(None), \
         patch(f"{_MOD}.sync_plot_mirror_from_cycle", AsyncMock()):
        cycle = await repo.create_cycle(
            db, plot, lot_no="HAND-1", cycle_label=None, p_code=None,
        )
    assert cycle.lot_no == "HAND-1"
    assert cycle.lot_no_source == "manual"


def test_supplier_code_is_read_server_side_never_from_a_request() -> None:
    """The supplier code embedded in a lot must come from the Supplier row the
    plot points at — never a client-supplied field."""
    src = inspect.getsource(repo._supplier_code_for_plot)
    assert "Supplier.code" in src
    assert "plot.supplier_id" in src
    # queried explicitly, NOT via the lazy="select" relationship (MissingGreenlet)
    assert "plot.supplier." not in src


async def test_create_normalizes_p_code() -> None:
    plot = _plot()
    db = _mock_db()
    with patch(f"{_MOD}._next_cycle_no", AsyncMock(return_value=1)), \
         patch(f"{_MOD}._next_lot_running_no", AsyncMock(return_value=1)), \
         _patch_supplier(), \
         patch(f"{_MOD}.sync_plot_mirror_from_cycle", AsyncMock()):
        cycle = await repo.create_cycle(
            db, plot, cycle_label="2605", p_code="  Melon-A  ",
        )
    assert cycle.p_code == "Melon-A"  # trimmed, case preserved


# --- create_cycle: supplier_lot_no ------------------------------------------

async def test_create_stores_supplier_lot_no_without_touching_the_system_lot() -> None:
    plot = _plot()
    db = _mock_db()
    with patch(f"{_MOD}._next_cycle_no", AsyncMock(return_value=1)), \
         patch(f"{_MOD}._next_lot_running_no", AsyncMock(return_value=1)), \
         _patch_supplier(), \
         patch(f"{_MOD}.sync_plot_mirror_from_cycle", AsyncMock()):
        cycle = await repo.create_cycle(
            db, plot, cycle_label="2605", p_code="WM-141",
            supplier_lot_no="  SUP-OWN-77  ",
        )
    assert cycle.supplier_lot_no == "SUP-OWN-77"          # trimmed
    assert cycle.lot_no == "2605-SUP010-WM-141-001"       # unaffected
    assert cycle.lot_no_source == "auto"
    assert cycle.lot_running_no == 1


@pytest.mark.parametrize("blank", [None, "", "   "])
async def test_create_blank_supplier_lot_no_is_null(blank) -> None:
    plot = _plot()
    db = _mock_db()
    with patch(f"{_MOD}._next_cycle_no", AsyncMock(return_value=1)), \
         patch(f"{_MOD}._next_lot_running_no", AsyncMock(return_value=1)), \
         _patch_supplier(), \
         patch(f"{_MOD}.sync_plot_mirror_from_cycle", AsyncMock()):
        cycle = await repo.create_cycle(
            db, plot, cycle_label="2605", p_code="WM-141", supplier_lot_no=blank,
        )
    assert cycle.supplier_lot_no is None


async def test_supplier_lot_no_does_not_join_the_running_series() -> None:
    """Two cycles differing ONLY in supplier_lot_no belong to the same series
    and must not get different series keys."""
    plot = _plot()
    db = _mock_db()
    keys = []
    for slot in ("A-1", "B-2"):
        with patch(f"{_MOD}._next_cycle_no", AsyncMock(return_value=1)), \
             patch(f"{_MOD}._next_lot_running_no", AsyncMock(return_value=1)), \
             _patch_supplier(), \
             patch(f"{_MOD}.sync_plot_mirror_from_cycle", AsyncMock()):
            c = await repo.create_cycle(
                db, plot, cycle_label="2605", p_code="WM-141", supplier_lot_no=slot,
            )
        keys.append(c.auto_lot_series_key)
    assert keys[0] == keys[1]


# --- update_cycle -----------------------------------------------------------

def _active_cycle(**over):
    base = dict(
        id=uuid4(), plot_id=uuid4(), status="active",
        crop=None, variety=None, cycle_label=None,
        lot_no="OLD-LOT", lot_no_source="manual", lot_running_no=None,
        auto_lot_series_key=None,
        po_number=None, p_code=None, supplier_lot_no=None,
        planting_date=None, plant_count=None,
        expected_yield_full=None, expected_yield_unit=None,
    )
    base.update(over)
    return SimpleNamespace(**base)


async def test_update_omitted_lot_leaves_lot_untouched() -> None:
    db = _mock_db()
    plot = _plot()
    cycle = _active_cycle(lot_no="KEEP-ME", lot_no_source="manual")
    with _patch_supplier():
        await repo.update_cycle(db, plot, cycle, {"crop": "พริก"})
    assert cycle.lot_no == "KEEP-ME"
    assert cycle.lot_no_source == "manual"


async def test_update_changing_label_or_p_code_alone_never_rewrites_the_lot() -> None:
    """Renaming a cycle or fixing a product code must not renumber or rewrite
    an existing lot identifier — lotNo ABSENT means "leave the lot alone"."""
    db = _mock_db()
    plot = _plot()
    cycle = _active_cycle(
        lot_no="2605-SUP010-WM-141-001", lot_no_source="auto", lot_running_no=1,
        auto_lot_series_key="k", cycle_label="2605", p_code="WM-141",
    )
    with _patch_supplier(), patch(f"{_MOD}._next_lot_running_no", AsyncMock()) as mk:
        await repo.update_cycle(db, plot, cycle, {"cycle_label": "26-may", "p_code": "WM-999"})
    assert cycle.lot_no == "2605-SUP010-WM-141-001"
    assert cycle.lot_running_no == 1
    assert cycle.auto_lot_series_key == "k"
    mk.assert_not_awaited()


async def test_update_manual_lot_sets_source_manual_and_clears_series() -> None:
    db = _mock_db()
    plot = _plot()
    cycle = _active_cycle(
        lot_no="2605-SUP010-WM-141-001", lot_no_source="auto",
        lot_running_no=1, auto_lot_series_key="k",
    )
    with _patch_supplier():
        await repo.update_cycle(db, plot, cycle, {"lot_no": "  MANUAL-9 "})
    assert cycle.lot_no == "MANUAL-9"
    assert cycle.lot_no_source == "manual"
    assert cycle.lot_running_no is None
    assert cycle.auto_lot_series_key is None


async def test_update_explicit_blank_lot_regenerates_auto_from_effective_values() -> None:
    db = _mock_db()
    plot = _plot()
    cycle = _active_cycle(lot_no="OLD", cycle_label="2605", p_code="WM-141")
    with _patch_supplier(), \
         patch(f"{_MOD}._next_lot_running_no", AsyncMock(return_value=7)):
        await repo.update_cycle(db, plot, cycle, {"lot_no": None})
    assert cycle.lot_no == "2605-SUP010-WM-141-007"
    assert cycle.lot_no_source == "auto"
    assert cycle.lot_running_no == 7
    assert cycle.auto_lot_series_key is not None


async def test_update_regenerate_uses_the_NEW_label_sent_in_the_same_request() -> None:
    db = _mock_db()
    plot = _plot()
    cycle = _active_cycle(lot_no="OLD", cycle_label="2605", p_code="WM-141")
    with _patch_supplier(), \
         patch(f"{_MOD}._next_lot_running_no", AsyncMock(return_value=1)):
        await repo.update_cycle(
            db, plot, cycle, {"cycle_label": "26-may", "lot_no": None},
        )
    assert cycle.lot_no == "26-may-SUP010-WM-141-001"


@pytest.mark.parametrize(
    "fields,expect_missing",
    [
        ({"cycle_label": None, "lot_no": None}, "cycleLabel"),
        ({"p_code": None, "lot_no": None}, "pCode"),
    ],
)
async def test_update_regenerate_without_a_component_raises_and_preserves_lot(
    fields, expect_missing,
) -> None:
    db = _mock_db()
    plot = _plot()
    cycle = _active_cycle(lot_no="KEEP", lot_no_source="manual",
                          cycle_label="2605", p_code="WM-141")
    with _patch_supplier():
        with pytest.raises(AutoLotMissingComponentError) as exc:
            await repo.update_cycle(db, plot, cycle, fields)
    assert expect_missing in exc.value.missing
    # the existing lot is untouched — never cleared to NULL
    assert cycle.lot_no == "KEEP"
    assert cycle.lot_no_source == "manual"


async def test_update_supplier_lot_no_alone_never_regenerates_the_lot() -> None:
    db = _mock_db()
    plot = _plot()
    cycle = _active_cycle(
        lot_no="2605-SUP010-WM-141-001", lot_no_source="auto",
        lot_running_no=1, auto_lot_series_key="k",
    )
    with _patch_supplier(), patch(f"{_MOD}._next_lot_running_no", AsyncMock()) as mk:
        await repo.update_cycle(db, plot, cycle, {"supplier_lot_no": " NEW-SUP-1 "})
    assert cycle.supplier_lot_no == "NEW-SUP-1"
    assert cycle.lot_no == "2605-SUP010-WM-141-001"
    assert cycle.lot_running_no == 1
    mk.assert_not_awaited()


async def test_update_can_clear_supplier_lot_no_explicitly() -> None:
    db = _mock_db()
    plot = _plot()
    cycle = _active_cycle(supplier_lot_no="OLD-SUP")
    with _patch_supplier():
        await repo.update_cycle(db, plot, cycle, {"supplier_lot_no": None})
    assert cycle.supplier_lot_no is None


async def test_update_ignores_non_editable_keys() -> None:
    plot = _plot()
    cycle = _active_cycle(status="active")
    db = _mock_db()
    # status/cycle_no/closed_* are never editable via update_cycle, and the
    # INTERNAL series key can never be set by a caller either.
    with _patch_supplier():
        await repo.update_cycle(
            db, plot, cycle,
            {"status": "harvested", "cycle_no": 99, "auto_lot_series_key": "hack"},
        )
    assert cycle.status == "active"
    assert cycle.auto_lot_series_key is None


# --- lock order (source-level) ---------------------------------------------

def test_endpoints_lock_plot_before_cycle() -> None:
    import app.api.v1.plots as plots_mod

    for fn in (plots_mod.start_plot_cycle, plots_mod.update_plot_cycle,
               plots_mod.rollover_plot_cycle):
        src = inspect.getsource(fn)
        assert "get_plot_for_update" in src, f"{fn.__name__} must lock the plot"
        # Plot lock appears before the cycle-for-update lock (Plot → PlotCycle).
        if "get_active_cycle_for_plot_for_update" in src:
            assert src.index("get_plot_for_update") < src.index(
                "get_active_cycle_for_plot_for_update"
            ), f"{fn.__name__}: Plot lock must precede PlotCycle lock"
