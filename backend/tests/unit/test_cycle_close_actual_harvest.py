"""Round D — closing a cycle records its ACTUAL harvest, carried forward from
the field team's own inspection instead of retyped by an admin.

Round C moved the CAPTURE to the inspection form (records.yield_quantity_kg /
records.final_yield_after_clean). This round is what consumes it: both the web
close endpoint and the Excel final_plot action fill blank figures from the
cycle's records, so an admin confirms numbers rather than entering them twice.
"""
from __future__ import annotations

import datetime
import inspect
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from fastapi import HTTPException

import app.api.v1.plots as plots_module
from app.api.v1.plots import close_plot_cycle, preview_plot_cycle_close
from app.db.models.plot_cycle import ACTUAL_HARVEST_YIELD_UNIT
from app.repositories import plot_cycle_repository as repo
from app.schemas.plot import PlotCycleClose

_P = "app.api.v1.plots"
_NOW = datetime.datetime(2026, 8, 20, tzinfo=datetime.timezone.utc)


def _record(**o):
    d = dict(
        id=uuid4(),
        record_date=datetime.date(2026, 8, 18),
        growth_stage="ผลผลิตสุดท้าย",
        yield_quantity_kg=Decimal("1250.00"),
        final_yield_after_clean=Decimal("1180.00"),
    )
    d.update(o)
    return SimpleNamespace(**d)


def _cycle(**o):
    d = dict(
        id=uuid4(), plot_id=uuid4(), cycle_no=1, status="active",
        crop="พริก", variety="พริกขี้หนู", cycle_label="jun2026", lot_no="L1",
        p_code=None, planting_date=None, plant_count=None,
        expected_yield_full=None, expected_yield_unit="kg",
        started_at=_NOW, closed_at=None, closed_by_id=None, close_reason=None,
        harvest_yield=None, final_yield_after_clean=None,
        final_yield_unit=None, harvest_date=None, final_note=None,
        created_at=_NOW, updated_at=_NOW,
    )
    d.update(o)
    return SimpleNamespace(**d)


def _plot(**o):
    d = dict(id=uuid4(), is_active=True)
    d.update(o)
    return SimpleNamespace(**d)


# --- the pure resolver ------------------------------------------------------

def test_figures_come_from_the_source_record() -> None:
    record = _record()
    figures = repo.actual_harvest_from_record(record)
    assert figures["harvest_yield"] == Decimal("1250.00")
    assert figures["final_yield_after_clean"] == Decimal("1180.00")
    # The report that states the harvested quantity IS the report of the
    # harvest, so its own date is the harvest date.
    assert figures["harvest_date"] == datetime.date(2026, 8, 18)
    assert figures["final_yield_unit"] == ACTUAL_HARVEST_YIELD_UNIT == "kg"


def test_a_closing_note_is_never_taken_from_the_field_report() -> None:
    """A close note is the admin's remark about the close, not the inspector's
    comment about the crop."""
    assert repo.actual_harvest_from_record(_record())["final_note"] is None


def test_nothing_is_carried_forward_without_a_harvested_quantity() -> None:
    """Without ผลผลิตที่เก็บได้ the all-or-none set cannot be completed, so a
    half set is never offered — the DB would reject it anyway
    (ck_plot_cycles_actual_harvest_all_or_none)."""
    figures = repo.actual_harvest_from_record(
        _record(yield_quantity_kg=None, final_yield_after_clean=Decimal("900.00"))
    )
    assert all(v is None for v in figures.values())


def test_no_record_at_all_resolves_to_nothing() -> None:
    assert all(v is None for v in repo.actual_harvest_from_record(None).values())


def test_a_plain_harvest_report_leaves_the_after_cleaning_gap_visible() -> None:
    """A เก็บเกี่ยว record has no after-cleaning figure. That gap is real and
    must stay None for the admin to fill, never invented from the harvested
    quantity."""
    figures = repo.actual_harvest_from_record(
        _record(growth_stage="เก็บเกี่ยว", final_yield_after_clean=None)
    )
    assert figures["harvest_yield"] == Decimal("1250.00")
    assert figures["final_yield_after_clean"] is None


def test_the_source_query_prefers_a_final_yield_report_without_matching_stage_names() -> None:
    """Source-level guard. The backend must find the ผลผลิตสุดท้าย report by
    the DATA it carries (a non-null final_yield_after_clean), never by matching
    a Thai stage name — stage names are admin-editable Master Data."""
    src = inspect.getsource(repo.get_actual_harvest_source_record)
    assert "final_yield_after_clean.is_not(None)" in src
    assert "yield_quantity_kg.is_not(None)" in src
    # Assert on the CODE, not the docstring — which legitimately names the
    # stage while explaining why the query does not.
    code = src[src.index('"""', src.index('"""') + 3) + 3:]
    assert "ผลผลิตสุดท้าย" not in code
    assert "growth_stage" not in code
    # newest by insert time, never the field-reported (backdatable) record_date
    assert "Record.created_at.desc()" in code
    assert "record_date.desc()" not in code


# --- the close endpoint -----------------------------------------------------

def _close_patches(plot, cycle, source):
    return (
        patch(f"{_P}.repo.get_plot_for_update", AsyncMock(return_value=plot)),
        patch(f"{_P}.plot_cycle_repo.get_cycle_for_plot", AsyncMock(return_value=cycle)),
        patch(f"{_P}.plot_cycle_repo.get_active_cycle_for_plot_for_update",
              AsyncMock(return_value=cycle)),
        patch(f"{_P}.plot_cycle_repo.get_actual_harvest_source_record",
              AsyncMock(return_value=source)),
        patch(f"{_P}.plot_cycle_repo.close_cycle", AsyncMock()),
        patch(f"{_P}.plot_cycle_repo.clear_plot_cycle_mirror_and_inspection_snapshot",
              AsyncMock()),
    )


async def _close(plot, cycle, source, payload):
    p1, p2, p3, p4, p5, p6 = _close_patches(plot, cycle, source)
    with p1, p2, p3, p4, p5, p6:
        return await close_plot_cycle(
            plot_id=plot.id, cycle_id=cycle.id, payload=payload,
            current_user=SimpleNamespace(id=uuid4()), db=AsyncMock(),
        )


async def test_close_carries_the_field_figures_forward_with_an_empty_body() -> None:
    """The round's whole point: the admin confirms, the server fills in."""
    plot, cycle, record = _plot(), _cycle(), _record()
    await _close(plot, cycle, record, PlotCycleClose(status="harvested"))

    assert cycle.harvest_yield == Decimal("1250.00")
    assert cycle.final_yield_after_clean == Decimal("1180.00")
    assert cycle.harvest_date == datetime.date(2026, 8, 18)
    assert cycle.final_yield_unit == "kg"


async def test_an_admin_override_wins_field_by_field() -> None:
    """Correcting one figure must not force retyping the other two."""
    plot, cycle, record = _plot(), _cycle(), _record()
    await _close(plot, cycle, record, PlotCycleClose(
        status="harvested", finalYieldAfterClean="1175.50",
    ))

    assert cycle.final_yield_after_clean == Decimal("1175.50")   # overridden
    assert cycle.harvest_yield == Decimal("1250.00")             # carried
    assert cycle.harvest_date == datetime.date(2026, 8, 18)      # carried


async def test_a_cycle_with_no_report_still_closes_with_no_figures() -> None:
    """Unchanged pre-round-D behaviour: a cycle nobody inspected can be closed,
    it simply records no actual harvest."""
    plot, cycle = _plot(), _cycle()
    await _close(plot, cycle, None, PlotCycleClose(status="harvested"))

    assert cycle.harvest_yield is None
    assert cycle.final_yield_after_clean is None
    assert cycle.harvest_date is None
    assert cycle.final_yield_unit is None


async def test_a_partial_set_that_cannot_be_completed_is_a_422() -> None:
    """ck_plot_cycles_actual_harvest_all_or_none surfaced as a clean error
    naming what is missing — never a raw DB constraint violation."""
    plot, cycle = _plot(), _cycle()
    with pytest.raises(HTTPException) as exc:
        await _close(plot, cycle, None, PlotCycleClose(
            status="harvested", harvestYield="1250.00",
        ))
    assert exc.value.status_code == 422
    assert "ผลผลิตหลังทำความสะอาด" in exc.value.detail
    assert "วันที่เก็บเกี่ยว" in exc.value.detail
    # nothing was written onto the cycle
    assert cycle.harvest_yield is None


async def test_a_cancelled_cycle_refuses_harvest_figures() -> None:
    """A cancelled cycle was never harvested; recording one would be a claim
    the data does not support. Refused, not silently dropped — the admin chose
    'cancelled' on purpose."""
    plot, cycle = _plot(), _cycle()
    with pytest.raises(HTTPException) as exc:
        await _close(plot, cycle, _record(), PlotCycleClose(
            status="cancelled", harvestYield="1250.00",
        ))
    assert exc.value.status_code == 422
    assert cycle.harvest_yield is None


async def test_a_plain_cancel_never_carries_figures_forward() -> None:
    plot, cycle, record = _plot(), _cycle(), _record()
    await _close(plot, cycle, record, PlotCycleClose(status="cancelled"))
    assert cycle.harvest_yield is None
    assert cycle.final_yield_unit is None


def test_the_unit_is_never_a_client_field() -> None:
    """Always kilograms, server-stamped — the same contract round 8-10B set for
    the Excel path."""
    assert "final_yield_unit" not in PlotCycleClose.model_fields
    src = inspect.getsource(plots_module.close_plot_cycle)
    assert "final_yield_unit=ACTUAL_HARVEST_YIELD_UNIT" in src


def test_the_estimate_snapshot_is_still_close_cycles_own_business() -> None:
    """set_actual_harvest writes the MEASURED figures; final_yield_pct /
    final_estimated_yield / final_inspection_record_id stay derived by
    close_cycle from the cycle's latest record."""
    for field in ("final_yield_pct", "final_estimated_yield", "final_inspection_record_id"):
        assert field not in PlotCycleClose.model_fields


# --- the read-only preview --------------------------------------------------

async def _preview(plot, cycle, source):
    with patch(f"{_P}.repo.get_plot", AsyncMock(return_value=plot)), \
         patch(f"{_P}.plot_cycle_repo.get_cycle_for_plot", AsyncMock(return_value=cycle)), \
         patch(f"{_P}.plot_cycle_repo.get_actual_harvest_source_record",
               AsyncMock(return_value=source)):
        return await preview_plot_cycle_close(
            plot_id=plot.id, cycle_id=cycle.id, db=AsyncMock(),
        )


async def test_preview_reports_the_figures_and_names_their_source() -> None:
    plot, cycle, record = _plot(), _cycle(), _record()
    result = await _preview(plot, cycle, record)

    assert result.resolved is True
    assert result.harvest_yield == Decimal("1250.00")
    assert result.final_yield_after_clean == Decimal("1180.00")
    assert result.harvest_date == datetime.date(2026, 8, 18)
    assert result.final_yield_unit == "kg"
    # so the screen can say WHICH report the admin is confirming
    assert result.source_record_id == record.id
    assert result.source_record_date == record.record_date
    assert result.source_growth_stage == "ผลผลิตสุดท้าย"


async def test_preview_says_unresolved_rather_than_showing_four_blanks() -> None:
    plot, cycle = _plot(), _cycle()
    result = await _preview(plot, cycle, None)
    assert result.resolved is False
    assert result.harvest_yield is None
    assert result.source_record_id is None


async def test_preview_never_names_a_record_whose_figures_were_unusable() -> None:
    """A record with no harvested quantity contributes nothing, so citing it
    would tell the admin they are confirming numbers that do not exist."""
    plot, cycle = _plot(), _cycle()
    result = await _preview(plot, cycle, _record(yield_quantity_kg=None))
    assert result.resolved is False
    assert result.source_record_id is None


async def test_preview_writes_nothing() -> None:
    """Read-only by construction: closing stays an explicit POST."""
    plot, cycle = _plot(), _cycle()
    db = AsyncMock()
    with patch(f"{_P}.repo.get_plot", AsyncMock(return_value=plot)), \
         patch(f"{_P}.plot_cycle_repo.get_cycle_for_plot", AsyncMock(return_value=cycle)), \
         patch(f"{_P}.plot_cycle_repo.get_actual_harvest_source_record",
               AsyncMock(return_value=_record())), \
         patch(f"{_P}.plot_cycle_repo.set_actual_harvest", MagicMock()) as mk_set, \
         patch(f"{_P}.plot_cycle_repo.close_cycle", AsyncMock()) as mk_close:
        await preview_plot_cycle_close(plot_id=plot.id, cycle_id=cycle.id, db=db)

    mk_set.assert_not_called()
    mk_close.assert_not_awaited()
    db.flush.assert_not_awaited()
    # and it takes no row lock — a preview must never block a concurrent close
    src = inspect.getsource(plots_module.preview_plot_cycle_close)
    assert "get_plot_for_update" not in src
    assert "for_update" not in src


async def test_preview_404s_for_a_plot_out_of_scope() -> None:
    plot, cycle = _plot(), _cycle()
    with patch(f"{_P}.repo.get_plot", AsyncMock(return_value=None)):
        with pytest.raises(HTTPException) as exc:
            await preview_plot_cycle_close(
                plot_id=plot.id, cycle_id=cycle.id, db=AsyncMock(),
            )
    assert exc.value.status_code == 404


def test_preview_is_gated_by_the_same_permission_as_the_close() -> None:
    """It reveals figures, so it must not be readable by anyone who could not
    close the cycle anyway."""
    preview_src = inspect.getsource(plots_module.preview_plot_cycle_close)
    close_src = inspect.getsource(plots_module.close_plot_cycle)
    assert "PLOTS_UPDATE" in preview_src
    assert "PLOTS_UPDATE" in close_src
    assert "get_rls_context" in preview_src
