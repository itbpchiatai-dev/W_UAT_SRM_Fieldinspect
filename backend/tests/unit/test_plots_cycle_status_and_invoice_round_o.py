"""The Plots list can finally tell a finished plot from one that never started (round O).

Three gaps, all on the same screen, all rooted in one missing distinction.

`PlotSummary.active_cycle_id` is null for TWO very different plots: one that
has never started a cycle, and one whose season is over. The list could not
separate them, so it labelled both "รอเริ่มรอบปลูก" — telling an admin that a
harvested, closed plot was waiting to begin.

That also explains why the existing "สถานะแปลง: ใช้งาน" filter never hid
finished plots and never could: it filters `Plot.is_active`, which is whether
an ADMIN has taken the plot out of service. Under "one plot, one cycle"
(round E) a harvested plot stays is_active=true until somebody deactivates it,
so the two are different axes and the page needed both.

Round O adds:
  * latest_cycle_* on PlotSummary — the plot's highest-cycle_no cycle whatever
    its status, alongside the active-only active_cycle_*.
  * a cycle_status filter (unfinished/active/none/closed/all) on the season
    axis, separate from plot_status.
  * an invoice filter that searches cycles of ANY status, because the invoice
    being looked up usually belongs to a season that is already closed.

The endpoint defaults cycle_status to "all", NOT "unfinished": GET /plots also
feeds SmartPlotPicker (round 7-11 shows no-active-cycle plots disabled rather
than hidden, deliberately) and the Dashboard map. The new default belongs to
the Plots page, which sends it explicitly. That is pinned below.
"""
from __future__ import annotations

import datetime
import inspect
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.api.v1 import plots as plots_module
from app.repositories import plot_repository as repo
from app.schemas.plot import PlotPhoneSearchRequest, PlotSummary


def _cycle(**over):
    base = {
        "id": uuid4(), "cycle_no": 1, "status": "active", "crop": "แตงโม",
        "variety": "กินรี", "cycle_label": "sep2026", "lot_no": "LOT-09",
        "po_number": "PO25009", "p_code": "Melon-I", "supplier_lot_no": None,
        "planting_date": None, "plant_count": 500, "expected_yield_full": None,
        "expected_yield_unit": None, "closed_at": None, "oracle_invoice": None,
    }
    base.update(over)
    return SimpleNamespace(**base)


def _plot(cycles, **over):
    active = next((c for c in cycles if c.status == "active"), None)
    base = {
        "id": uuid4(), "supplier_id": uuid4(), "plot_code": "P001", "name": "แปลง",
        "province": "ขอนแก่น", "is_active": True, "assignments": [], "supplier": None,
        "access_phones": [], "cycles": cycles, "active_cycle": active,
        "current_stage": None, "last_inspected_at": None,
    }
    base.update(over)
    return SimpleNamespace(**base)


def _summary(plot) -> PlotSummary:
    s = PlotSummary.model_construct(
        id=plot.id, supplier_id=plot.supplier_id, plot_code=plot.plot_code,
        name=plot.name, province=plot.province, is_active=plot.is_active,
    )
    plots_module._populate_latest_cycle(s, plot)
    return s


# --- the distinction the list was missing ------------------------------------

def test_a_plot_that_never_started_reports_no_latest_cycle() -> None:
    s = _summary(_plot([]))
    assert s.latest_cycle_status is None
    assert s.latest_cycle_closed_at is None


def test_a_growing_plot_reports_active() -> None:
    s = _summary(_plot([_cycle(status="active")]))
    assert s.latest_cycle_status == "active"


@pytest.mark.parametrize("status", ["harvested", "cancelled"])
def test_a_closed_plot_reports_how_it_closed_not_none(status: str) -> None:
    """The bug this round exists for: before it, this plot was
    indistinguishable from one that had never planted anything."""
    closed = datetime.datetime(2026, 9, 1, tzinfo=datetime.timezone.utc)
    s = _summary(_plot([_cycle(status=status, closed_at=closed)]))
    assert s.latest_cycle_status == status, (
        "a finished plot still looks like one that never started"
    )
    assert s.latest_cycle_closed_at == closed


def test_latest_means_highest_cycle_no_not_first_in_the_list() -> None:
    """Pre-round-E plots can carry several cycles, in whatever order the
    relationship happens to load them."""
    s = _summary(_plot([
        _cycle(cycle_no=2, status="harvested"),
        _cycle(cycle_no=1, status="cancelled"),
    ]))
    assert s.latest_cycle_status == "harvested"


def test_an_active_cycle_wins_even_beside_older_closed_ones() -> None:
    s = _summary(_plot([
        _cycle(cycle_no=1, status="harvested"),
        _cycle(cycle_no=2, status="active"),
    ]))
    assert s.latest_cycle_status == "active"


def test_invoice_is_carried_from_the_latest_cycle() -> None:
    s = _summary(_plot([_cycle(status="harvested", oracle_invoice="INV-2026-0042")]))
    assert s.latest_cycle_oracle_invoice == "INV-2026-0042"


def test_populate_is_safe_on_an_object_with_no_cycles_attribute() -> None:
    """Endpoint unit tests build bare plot stand-ins; this must not explode."""
    s = PlotSummary.model_construct(
        id=uuid4(), supplier_id=uuid4(), plot_code="P", name="n",
        province=None, is_active=True,
    )
    plots_module._populate_latest_cycle(s, SimpleNamespace())
    assert s.latest_cycle_status is None


# --- the two filters are wired into BOTH list paths --------------------------

@pytest.mark.parametrize("fn", [repo.list_plots, repo.search_plots_by_phone])
def test_both_list_paths_accept_the_new_filters(fn) -> None:
    """search_plots_by_phone is the same page's other query. A filter added to
    one and not the other makes the phone box silently widen the results the
    rest of the filter bar narrowed."""
    params = inspect.signature(fn).parameters
    assert "cycle_status" in params, f"{fn.__name__} ignores the cycle-status filter"
    assert "invoice" in params, f"{fn.__name__} ignores the invoice filter"


@pytest.mark.parametrize("fn", [repo.list_plots, repo.search_plots_by_phone])
def test_the_repository_default_stays_all(fn) -> None:
    params = inspect.signature(fn).parameters
    assert params["cycle_status"].default == "all"


def test_the_endpoint_default_stays_all_so_other_callers_are_untouched() -> None:
    """GET /plots also feeds SmartPlotPicker and the Dashboard map. Making
    'unfinished' the server-side default would silently hide plots from both —
    including the no-active-cycle plots round 7-11 deliberately shows
    (disabled) so a user knows they exist. The Plots page sends 'unfinished'
    itself."""
    params = inspect.signature(plots_module.list_plots).parameters
    assert params["cycle_status"].default == "all", (
        "the Plots page's default leaked onto the endpoint — SmartPlotPicker "
        "and the Dashboard map would change behaviour with it"
    )
    assert PlotPhoneSearchRequest.model_fields["cycle_status"].default == "all"


def test_cycle_status_and_plot_status_stay_separate_parameters() -> None:
    """Collapsing them would recreate the original bug: they answer different
    questions, and a plot is routinely 'active' on one axis and 'closed' on
    the other."""
    params = inspect.signature(repo.list_plots).parameters
    assert "plot_status" in params and "cycle_status" in params


# --- filter semantics --------------------------------------------------------

def test_closed_means_has_a_cycle_and_none_of_them_is_active() -> None:
    """Not "has a harvested cycle" — that would also match a legacy plot that
    closed one cycle and opened another, which is still growing."""
    src = inspect.getsource(repo._apply_cycle_status_filter)
    assert "has_any & ~has_active" in src


def test_unfinished_includes_plots_that_never_started() -> None:
    """Filtering to active-only would hide a freshly created plot from the very
    admin whose job is to start its cycle."""
    src = inspect.getsource(repo._apply_cycle_status_filter)
    assert "has_active | ~has_any" in src


def test_invoice_search_is_not_limited_to_the_active_cycle() -> None:
    """The whole point is finding the plot a FINISHED season's invoice belonged
    to. A status predicate here would make it useless for exactly that."""
    src = inspect.getsource(repo._apply_invoice_filter)
    assert 'PlotCycle.status == "active"' not in src, (
        "the invoice filter was scoped to the active cycle — it can no longer "
        "find the closed seasons it exists for"
    )
    assert "ilike" in src, "invoice must be a partial match, not exact"


@pytest.mark.parametrize("fn", [
    repo._apply_cycle_status_filter,
    repo._apply_invoice_filter,
])
def test_the_new_filters_use_exists_never_a_join(fn) -> None:
    """A plot with several cycles would come back once per matching row and
    duplicate the list — the reason every cycle filter here is an EXISTS."""
    src = inspect.getsource(fn)
    assert ".exists()" in src
    assert ".join(" not in src


def test_both_list_queries_eager_load_the_cycle_history() -> None:
    """Plot.cycles is lazy="select". Reading it in _populate_latest_cycle
    without this raises MissingGreenlet under asyncio — the round-7.7 failure
    mode — and only on the path that forgot it, so a test on the other path
    would still pass."""
    src = inspect.getsource(repo)
    list_src = src[src.index("async def list_plots"):src.index("async def search_plots_by_phone")]
    phone_src = src[src.index("async def search_plots_by_phone"):src.index("async def list_plot_provinces")]
    for name, body in (("list_plots", list_src), ("search_plots_by_phone", phone_src)):
        assert "selectinload(Plot.cycles)" in body, (
            f"{name} reads latest_cycle_* without eager-loading Plot.cycles"
        )
