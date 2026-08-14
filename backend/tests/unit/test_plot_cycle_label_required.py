"""Round 8-17A.1 — cycleLabel is required (nonblank) on every "start a NEW
planting cycle" flow, independent of Auto vs Manual lot, while historical
cycles with cycle_label=None must keep reading back fine.

`PlotCycleCreate` is the ONE schema shared by all four lifecycle endpoints
that open a new cycle (create_plot_with_cycle, start_plot_cycle,
rollover_plot_cycle's new_cycle, reactivate_plot_with_cycle) — confirmed
from source (grep for "payload: PlotCycleCreate", "new_cycle: PlotCycleCreate"
and "cycle: PlotCycleCreate" in app/api/v1/plots.py and app/schemas/plot.py),
so a schema-level test here covers all four without duplicating the
assertion per endpoint. update_plot_cycle's PATCH-specific "never clear an
existing label" rule is separate (exclude_unset semantics need the CURRENT
value, which only the endpoint — not the schema — has) and is tested
directly against that endpoint below.
"""
from __future__ import annotations

import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from app.api.v1.plots import update_plot_cycle
from app.schemas.plot import PlotCycleCreate, PlotCycleRead, PlotCycleUpdate

pytestmark = pytest.mark.nodefault_crop_variety

_NOW = datetime.datetime(2026, 7, 14, tzinfo=datetime.timezone.utc)
_P = "app.api.v1.plots"


def _valid_kwargs(**over):
    d = dict(poNumber="PO25001", pCode="Melon-A", cycleLabel="jun2026")
    d.update(over)
    return d


# --- PlotCycleCreate: shared by create/start/rollover/reactivate -----------

def test_plot_cycle_create_requires_nonblank_cycle_label() -> None:
    with pytest.raises(ValidationError):
        PlotCycleCreate(**_valid_kwargs(cycleLabel=None))


@pytest.mark.parametrize("blank", ["", "   ", "\t", "\n"])
def test_plot_cycle_create_rejects_blank_or_whitespace_cycle_label(blank: str) -> None:
    with pytest.raises(ValidationError) as exc:
        PlotCycleCreate(**_valid_kwargs(cycleLabel=blank))
    assert "cycleLabel" in str(exc.value)


def test_plot_cycle_create_rejects_cycle_label_omitted_entirely() -> None:
    kwargs = _valid_kwargs()
    del kwargs["cycleLabel"]
    with pytest.raises(ValidationError):
        PlotCycleCreate(**kwargs)


def test_plot_cycle_create_trims_a_valid_cycle_label() -> None:
    payload = PlotCycleCreate(**_valid_kwargs(cycleLabel="  jun2026  "))
    assert payload.cycle_label == "jun2026"


def test_plot_cycle_create_required_independent_of_manual_lot() -> None:
    """Round 8-17A.1's core new rule: a Manual lot (explicit lotNo) must NOT
    exempt cycleLabel — the pre-round contract only required it for Auto."""
    with pytest.raises(ValidationError) as exc:
        PlotCycleCreate(**_valid_kwargs(cycleLabel="", lotNo="HAND-01"))
    assert "cycleLabel" in str(exc.value)


def test_plot_cycle_create_error_message_matches_the_mandated_thai_text() -> None:
    with pytest.raises(ValidationError) as exc:
        PlotCycleCreate(**_valid_kwargs(cycleLabel=""))
    assert "กรุณาระบุชื่อรอบปลูก เนื่องจากใช้ระบุรอบและสร้าง Lot No อัตโนมัติ" in str(exc.value)


def test_plot_cycle_create_max_length_unchanged() -> None:
    with pytest.raises(ValidationError):
        PlotCycleCreate(**_valid_kwargs(cycleLabel="x" * 101))
    # 100 chars is still fine.
    payload = PlotCycleCreate(**_valid_kwargs(cycleLabel="x" * 100))
    assert len(payload.cycle_label) == 100


# --- PlotCycleRead / PlotCycleUpdate: legacy null must stay readable --------

def test_plot_cycle_read_still_accepts_null_cycle_label_for_legacy_rows() -> None:
    """The READ model must never become non-null — a cycle created before
    this round has cycle_label=None in the DB and must keep serializing."""
    row = SimpleNamespace(
        id=uuid4(), plot_id=uuid4(), cycle_no=1, status="active",
        crop=None, variety=None, cycle_label=None, lot_no=None,
        po_number=None, p_code=None, lot_no_source=None, lot_running_no=None,
        supplier_lot_no=None, planting_date=None, plant_count=None,
        expected_yield_full=None, expected_yield_unit=None,
        started_at=_NOW, closed_at=None, closed_by_id=None, close_reason=None,
        final_yield_pct=None, final_estimated_yield=None, final_inspection_record_id=None,
        harvest_yield=None, final_yield_after_clean=None, final_yield_unit=None,
        harvest_date=None, final_note=None, created_at=_NOW, updated_at=_NOW,
    )
    read = PlotCycleRead.model_validate(row)
    assert read.cycle_label is None


def test_plot_cycle_update_still_allows_omitting_cycle_label() -> None:
    """PATCH keeps exclude_unset semantics — omitting the field entirely
    must not raise (unlike PlotCycleCreate, which requires the key)."""
    payload = PlotCycleUpdate(plantCount=200)
    assert "cycle_label" not in payload.model_dump(exclude_unset=True)


def test_plot_cycle_update_still_accepts_explicit_null() -> None:
    """The schema itself still allows null (blank trims to None) — the
    ENDPOINT is what blocks clearing an existing value (see below); the
    schema alone can't know what the current value is."""
    payload = PlotCycleUpdate(cycleLabel=None)
    assert payload.cycle_label is None


# --- update_plot_cycle endpoint: forbid clearing an existing label ---------

def _plot(**o):
    d = dict(id=uuid4(), is_active=True)
    d.update(o)
    return SimpleNamespace(**d)


def _cycle(**o):
    d = dict(
        id=uuid4(), plot_id=uuid4(), cycle_no=1, status="active",
        crop=None, variety=None, cycle_label="jun2026", lot_no=None,
        planting_date=None, plant_count=None, expected_yield_full=None,
        expected_yield_unit=None, po_number=None, p_code=None,
        lot_no_source=None, lot_running_no=None, supplier_lot_no=None,
        started_at=_NOW, closed_at=None, closed_by_id=None, close_reason=None,
        created_at=_NOW, updated_at=_NOW,
    )
    d.update(o)
    return SimpleNamespace(**d)


def _db():
    return AsyncMock()


async def test_update_cycle_rejects_clearing_an_existing_label_via_blank() -> None:
    plot = _plot()
    cycle = _cycle(plot_id=plot.id, cycle_label="jun2026")
    payload = PlotCycleUpdate(cycleLabel="")
    with patch(f"{_P}.repo.get_plot_for_update", AsyncMock(return_value=plot)), \
         patch(f"{_P}.plot_cycle_repo.get_cycle_for_plot", AsyncMock(return_value=cycle)), \
         patch(f"{_P}.plot_cycle_repo.get_active_cycle_for_plot_for_update", AsyncMock(return_value=cycle)), \
         patch(f"{_P}.plot_cycle_repo.update_cycle", AsyncMock()) as mk_update:
        with pytest.raises(HTTPException) as exc:
            await update_plot_cycle(plot_id=plot.id, cycle_id=cycle.id, payload=payload, db=_db())
    assert exc.value.status_code == 422
    assert "กรุณาระบุชื่อรอบปลูก" in exc.value.detail
    mk_update.assert_not_awaited()


async def test_update_cycle_omitted_label_preserves_existing_value() -> None:
    """exclude_unset — not sending cycleLabel at all must NOT be treated as
    clearing it (this is the normal 'edit an unrelated field' case)."""
    plot = _plot()
    cycle = _cycle(plot_id=plot.id, cycle_label="jun2026")
    payload = PlotCycleUpdate(plantCount=500)
    with patch(f"{_P}.repo.get_plot_for_update", AsyncMock(return_value=plot)), \
         patch(f"{_P}.plot_cycle_repo.get_cycle_for_plot", AsyncMock(return_value=cycle)), \
         patch(f"{_P}.plot_cycle_repo.get_active_cycle_for_plot_for_update", AsyncMock(return_value=cycle)), \
         patch(f"{_P}.plot_cycle_repo.update_cycle", AsyncMock()) as mk_update, \
         patch(f"{_P}.plot_cycle_repo.sync_plot_mirror_from_cycle", AsyncMock()):
        result = await update_plot_cycle(plot_id=plot.id, cycle_id=cycle.id, payload=payload, db=_db())
    mk_update.assert_awaited_once()
    assert result.id == cycle.id


async def test_update_cycle_legacy_null_label_left_untouched_is_a_no_op_not_a_clear() -> None:
    """A cycle that is ALREADY unlabeled (legacy, pre-8-17A.1) and stays
    blank on this PATCH is not "clearing an existing value" — nothing
    existed to clear. Must be allowed (no forced backfill via the API)."""
    plot = _plot()
    cycle = _cycle(plot_id=plot.id, cycle_label=None)
    payload = PlotCycleUpdate(plantCount=500)
    with patch(f"{_P}.repo.get_plot_for_update", AsyncMock(return_value=plot)), \
         patch(f"{_P}.plot_cycle_repo.get_cycle_for_plot", AsyncMock(return_value=cycle)), \
         patch(f"{_P}.plot_cycle_repo.get_active_cycle_for_plot_for_update", AsyncMock(return_value=cycle)), \
         patch(f"{_P}.plot_cycle_repo.update_cycle", AsyncMock()) as mk_update, \
         patch(f"{_P}.plot_cycle_repo.sync_plot_mirror_from_cycle", AsyncMock()):
        await update_plot_cycle(plot_id=plot.id, cycle_id=cycle.id, payload=payload, db=_db())
    mk_update.assert_awaited_once()


async def test_update_cycle_changing_to_a_new_nonblank_label_is_allowed() -> None:
    plot = _plot()
    cycle = _cycle(plot_id=plot.id, cycle_label="jun2026")
    payload = PlotCycleUpdate(cycleLabel="jul2026")
    with patch(f"{_P}.repo.get_plot_for_update", AsyncMock(return_value=plot)), \
         patch(f"{_P}.plot_cycle_repo.get_cycle_for_plot", AsyncMock(return_value=cycle)), \
         patch(f"{_P}.plot_cycle_repo.get_active_cycle_for_plot_for_update", AsyncMock(return_value=cycle)), \
         patch(f"{_P}.plot_cycle_repo.update_cycle", AsyncMock()) as mk_update, \
         patch(f"{_P}.plot_cycle_repo.sync_plot_mirror_from_cycle", AsyncMock()):
        await update_plot_cycle(plot_id=plot.id, cycle_id=cycle.id, payload=payload, db=_db())
    mk_update.assert_awaited_once()
    assert mk_update.call_args.args[3]["cycle_label"] == "jul2026"
