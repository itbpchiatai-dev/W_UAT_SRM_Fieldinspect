"""Public phone-access endpoints (round 8-3B) — lookup / plots / select-plot.

DB-less: mocks the repo calls and exercises the real endpoint logic via
`.__wrapped__` (bypassing the @limiter.limit slowapi decorator, same as the
other public endpoint tests). Verifies primary/additional parity, multi-plot/
multi-supplier, QR matching, inspectedToday, generic 404/401/409, the minted
inspection token's claims, and that NO response ever carries a phone.
"""
from __future__ import annotations

import datetime
import inspect
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest
from fastapi import HTTPException
from jose import jwt

import app.api.v1.public_inspection_access as mod
from app.auth.phone_access_session import encode_phone_access_session_token
from app.core.config import get_settings
from app.schemas.phone_access import (
    PublicPhoneAccessListRequest,
    PublicPhoneAccessLookupRequest,
    PublicPhoneAccessSelectPlotRequest,
)

_lookup = mod.phone_access_lookup.__wrapped__
_plots = mod.phone_access_plots.__wrapped__
_select = mod.phone_access_select_plot.__wrapped__
_M = "app.api.v1.public_inspection_access"
_PHONE = "0845552162"  # placeholder, never a real number


def _cycle(**o):
    d = dict(
        id=uuid4(), cycle_no=1, cycle_label="jun2026", crop="พริก",
        variety="พริกขี้หนู", lot_no="LOT-01",
        planting_date=datetime.date(2026, 6, 1), plant_count=1000,
        expected_yield_full=800, expected_yield_unit="kg",
    )
    d.update(o)
    return SimpleNamespace(**d)


def _supplier(**o):
    d = dict(id=uuid4(), code="SUP001", name="Supplier One", is_active=True)
    d.update(o)
    return SimpleNamespace(**d)


def _plot(supplier, **o):
    d = dict(
        id=uuid4(), plot_code="P001", name="Plot One", is_active=True,
        supplier_id=supplier.id, supplier=supplier, active_cycle=_cycle(),
        last_inspected_at=None, current_yield_pct=None, current_stage=None,
    )
    d.update(o)
    return SimpleNamespace(**d)


def _access(access_type="primary", **o):
    d = dict(id=uuid4(), access_type=access_type, phone_normalized=_PHONE)
    d.update(o)
    return SimpleNamespace(**d)


def _row(access_type="primary", supplier=None, plot=None):
    supplier = supplier or _supplier()
    plot = plot or _plot(supplier)
    return (_access(access_type), plot, supplier)


def _db():
    return AsyncMock()


def _phone_token(ids):
    token, _ = encode_phone_access_session_token(access_phone_ids=ids)
    return token


# --- lookup -----------------------------------------------------------------

async def test_lookup_primary_success() -> None:
    row = _row("primary")
    with patch(f"{_M}.phone_repo.lookup_active_access_rows_by_phone", AsyncMock(return_value=[row])), \
         patch(f"{_M}.plot_cycle_repo.get_latest_active_record_dates_for_cycles", AsyncMock(return_value={})):
        res = await _lookup(payload=PublicPhoneAccessLookupRequest(phone=_PHONE), request=AsyncMock(), db=_db())
    assert res.phone_access_session_token
    assert len(res.plots) == 1
    assert res.plots[0].access_type == "primary"
    assert res.plots[0].can_inspect is True
    assert res.qr_matched_plot_id is None


async def test_lookup_additional_success_equal_rights() -> None:
    row = _row("additional")
    with patch(f"{_M}.phone_repo.lookup_active_access_rows_by_phone", AsyncMock(return_value=[row])), \
         patch(f"{_M}.plot_cycle_repo.get_latest_active_record_dates_for_cycles", AsyncMock(return_value={})):
        res = await _lookup(payload=PublicPhoneAccessLookupRequest(phone=_PHONE), request=AsyncMock(), db=_db())
    assert res.plots[0].access_type == "additional"
    assert res.plots[0].can_inspect is True


async def test_lookup_multiple_plots_and_suppliers() -> None:
    s1, s2 = _supplier(code="SUP001"), _supplier(code="SUP002", name="Supplier Two")
    rows = [_row("primary", supplier=s1), _row("additional", supplier=s2)]
    with patch(f"{_M}.phone_repo.lookup_active_access_rows_by_phone", AsyncMock(return_value=rows)), \
         patch(f"{_M}.plot_cycle_repo.get_latest_active_record_dates_for_cycles", AsyncMock(return_value={})):
        res = await _lookup(payload=PublicPhoneAccessLookupRequest(phone=_PHONE), request=AsyncMock(), db=_db())
    assert len(res.plots) == 2
    assert {p.supplier_code for p in res.plots} == {"SUP001", "SUP002"}


async def test_lookup_no_active_cycle_cannot_inspect() -> None:
    supplier = _supplier()
    plot = _plot(supplier, active_cycle=None)
    row = (_access("primary"), plot, supplier)
    with patch(f"{_M}.phone_repo.lookup_active_access_rows_by_phone", AsyncMock(return_value=[row])), \
         patch(f"{_M}.plot_cycle_repo.get_latest_active_record_dates_for_cycles", AsyncMock(return_value={})):
        res = await _lookup(payload=PublicPhoneAccessLookupRequest(phone=_PHONE), request=AsyncMock(), db=_db())
    assert res.plots[0].can_inspect is False
    assert res.plots[0].unavailable_reason == "no_active_cycle"
    assert res.plots[0].plot_cycle_id is None


async def test_lookup_unknown_phone_generic_404() -> None:
    with patch(f"{_M}.phone_repo.lookup_active_access_rows_by_phone", AsyncMock(return_value=[])):
        with pytest.raises(HTTPException) as exc:
            await _lookup(payload=PublicPhoneAccessLookupRequest(phone=_PHONE), request=AsyncMock(), db=_db())
    assert exc.value.status_code == 404


async def test_lookup_invalid_phone_422_without_echo() -> None:
    with pytest.raises(HTTPException) as exc:
        await _lookup(payload=PublicPhoneAccessLookupRequest(phone="abc-not-phone"), request=AsyncMock(), db=_db())
    assert exc.value.status_code == 422
    assert "abc" not in str(exc.value.detail)  # never echo the raw input


async def test_lookup_qr_assigned_sets_matched_plot() -> None:
    row = _row("primary")
    plot = row[1]
    with patch(f"{_M}.phone_repo.lookup_active_access_rows_by_phone", AsyncMock(return_value=[row])), \
         patch(f"{_M}.plot_cycle_repo.get_latest_active_record_dates_for_cycles", AsyncMock(return_value={})), \
         patch(f"{_M}.plot_repo.get_plot_by_qr_key", AsyncMock(return_value=plot)):
        res = await _lookup(
            payload=PublicPhoneAccessLookupRequest(phone=_PHONE, qrKey="qr-abc"),
            request=AsyncMock(), db=_db(),
        )
    assert res.qr_matched_plot_id == plot.id


async def test_lookup_qr_not_assigned_generic_404() -> None:
    row = _row("primary")
    other_plot = _plot(_supplier())  # a plot NOT among the phone's rows
    with patch(f"{_M}.phone_repo.lookup_active_access_rows_by_phone", AsyncMock(return_value=[row])), \
         patch(f"{_M}.plot_cycle_repo.get_latest_active_record_dates_for_cycles", AsyncMock(return_value={})), \
         patch(f"{_M}.plot_repo.get_plot_by_qr_key", AsyncMock(return_value=other_plot)):
        with pytest.raises(HTTPException) as exc:
            await _lookup(
                payload=PublicPhoneAccessLookupRequest(phone=_PHONE, qrKey="qr-x"),
                request=AsyncMock(), db=_db(),
            )
    assert exc.value.status_code == 404


async def test_lookup_response_carries_no_phone_or_forbidden_fields() -> None:
    row = _row("primary")
    with patch(f"{_M}.phone_repo.lookup_active_access_rows_by_phone", AsyncMock(return_value=[row])), \
         patch(f"{_M}.plot_cycle_repo.get_latest_active_record_dates_for_cycles", AsyncMock(return_value={})):
        res = await _lookup(payload=PublicPhoneAccessLookupRequest(phone=_PHONE), request=AsyncMock(), db=_db())
    # The phone NUMBER must never appear (the only field carrying "phone" in its
    # name is the session token, which is opaque and carries no phone).
    assert _PHONE not in res.model_dump_json()
    # No forbidden fields on the plot item (GPS/address/phone/qrKey/access id).
    # Round 8-4C Part B intentionally added expectedYieldFull/expectedYieldUnit/
    # plantCount/currentYieldPct/currentStage (offline continuity) — no longer
    # forbidden, see test_lookup_plot_item_includes_yield_plan_and_current_status.
    item_keys = set(res.plots[0].model_dump(by_alias=True))
    forbidden = {
        "phone", "phoneNormalized", "phoneLast4", "qrKey", "accessPhoneId",
        "latitude", "longitude", "province", "village", "district",
    }
    assert forbidden.isdisjoint(item_keys)


async def test_lookup_plot_item_includes_lot_no_and_planting_date_from_active_cycle() -> None:
    """Round 8-3K: a phone with several plots needs Lot No/planting date to
    pick the right one before selecting — sourced verbatim from the SAME
    active cycle as crop/variety (never recomputed, never the plot mirror)."""
    row = _row("primary")
    with patch(f"{_M}.phone_repo.lookup_active_access_rows_by_phone", AsyncMock(return_value=[row])), \
         patch(f"{_M}.plot_cycle_repo.get_latest_active_record_dates_for_cycles", AsyncMock(return_value={})):
        res = await _lookup(payload=PublicPhoneAccessLookupRequest(phone=_PHONE), request=AsyncMock(), db=_db())
    assert res.plots[0].lot_no == "LOT-01"
    assert res.plots[0].planting_date == datetime.date(2026, 6, 1)


async def test_lookup_plot_item_lot_no_and_planting_date_null_without_active_cycle() -> None:
    """No active cycle -> null, never a fallback to the plot's current_*
    mirror (same rule as crop/variety/unavailable_reason above)."""
    supplier = _supplier()
    plot = _plot(supplier, active_cycle=None)
    row = (_access("primary"), plot, supplier)
    with patch(f"{_M}.phone_repo.lookup_active_access_rows_by_phone", AsyncMock(return_value=[row])), \
         patch(f"{_M}.plot_cycle_repo.get_latest_active_record_dates_for_cycles", AsyncMock(return_value={})):
        res = await _lookup(payload=PublicPhoneAccessLookupRequest(phone=_PHONE), request=AsyncMock(), db=_db())
    assert res.plots[0].lot_no is None
    assert res.plots[0].planting_date is None


def test_plot_item_schema_lot_no_and_planting_date_are_nullable() -> None:
    """Round 8-3K contract: additive, defaults to None when omitted."""
    from app.schemas.phone_access import PublicPhoneAccessPlotItem

    item = PublicPhoneAccessPlotItem(
        plotId=uuid4(), plotCode="P001", plotName="Plot One",
        supplierId=uuid4(), supplierCode="SUP001", supplierName="Supplier One",
        accessType="primary", canInspect=True,
    )
    assert item.lot_no is None
    assert item.planting_date is None


async def test_lookup_plot_item_includes_yield_plan_and_current_status() -> None:
    """Round 8-4C Part B: enough on the LIST item to open a full offline
    inspection form without another round-trip. plantCount/expectedYield* come
    from the active cycle (same source as crop/variety/lotNo above);
    currentYieldPct/currentStage come from the Plot's inspection-derived
    snapshot (same source as lastInspectedAt, unconditional on cycle state)."""
    supplier = _supplier()
    inspected_at = datetime.datetime(2026, 7, 10, 9, 30, tzinfo=datetime.timezone.utc)
    plot = _plot(
        supplier,
        current_yield_pct=Decimal("62.5"),
        current_stage="ออกดอก",
        last_inspected_at=inspected_at,
    )
    row = (_access("primary"), plot, supplier)
    with patch(f"{_M}.phone_repo.lookup_active_access_rows_by_phone", AsyncMock(return_value=[row])), \
         patch(f"{_M}.plot_cycle_repo.get_latest_active_record_dates_for_cycles", AsyncMock(return_value={})):
        res = await _lookup(payload=PublicPhoneAccessLookupRequest(phone=_PHONE), request=AsyncMock(), db=_db())
    item = res.plots[0]
    assert item.plant_count == plot.active_cycle.plant_count
    assert item.expected_yield_full == plot.active_cycle.expected_yield_full
    assert item.expected_yield_unit == plot.active_cycle.expected_yield_unit
    assert item.current_yield_pct == Decimal("62.5")
    assert item.current_stage == "ออกดอก"
    assert item.last_inspected_at == inspected_at


async def test_lookup_plot_item_all_cycle_and_snapshot_fields_null_without_active_cycle() -> None:
    """Round 8-4C.1 Part E: no active cycle -> EVERY cycle/yield-plan/
    inspection-derived field is null, including the Plot-owned snapshot trio
    (currentYieldPct/currentStage/lastInspectedAt) — reversed from round
    8-4C's original unconditional behavior. A closed cycle's last inspection
    must never keep looking like current information on the authorized plot
    list; the plot fixture below deliberately carries STALE non-null values
    on all three Plot columns to prove they're actively suppressed, not just
    coincidentally absent."""
    supplier = _supplier()
    inspected_at = datetime.datetime(2026, 7, 10, 9, 30, tzinfo=datetime.timezone.utc)
    plot = _plot(
        supplier, active_cycle=None,
        current_yield_pct=Decimal("40"), current_stage="ติดผล",
        last_inspected_at=inspected_at,
    )
    row = (_access("primary"), plot, supplier)
    with patch(f"{_M}.phone_repo.lookup_active_access_rows_by_phone", AsyncMock(return_value=[row])), \
         patch(f"{_M}.plot_cycle_repo.get_latest_active_record_dates_for_cycles", AsyncMock(return_value={})):
        res = await _lookup(payload=PublicPhoneAccessLookupRequest(phone=_PHONE), request=AsyncMock(), db=_db())
    item = res.plots[0]
    assert item.can_inspect is False
    assert item.plant_count is None
    assert item.expected_yield_full is None
    assert item.expected_yield_unit is None
    assert item.current_yield_pct is None
    assert item.current_stage is None
    assert item.last_inspected_at is None


async def test_lookup_inspected_today_is_plot_cycle_level_not_per_access_row() -> None:
    """Round 8-19 reversed this deliberately: the status used to be keyed by
    plot_access_phone_id, so the same plot's เบอร์หลัก and เบอร์เสริม disagreed
    about whether it had been inspected today. It is keyed by the active
    PlotCycle now, so every authorized number sees the same answer."""
    supplier = _supplier()
    plot = _plot(supplier)
    today = datetime.datetime.now(datetime.timezone.utc).date()
    latest = {plot.active_cycle.id: today}

    seen = []
    for access_type in ("primary", "additional"):
        row = (_access(access_type), plot, supplier)
        with patch(f"{_M}.phone_repo.lookup_active_access_rows_by_phone", AsyncMock(return_value=[row])), \
             patch(f"{_M}.plot_cycle_repo.get_latest_active_record_dates_for_cycles",
                   AsyncMock(return_value=latest)):
            res = await _lookup(
                payload=PublicPhoneAccessLookupRequest(phone=_PHONE), request=AsyncMock(), db=_db(),
            )
        seen.append((res.plots[0].inspected_today, res.plots[0].last_inspection_date))

    assert seen[0] == (True, today)
    assert seen[0] == seen[1]


# --- plots (list) -----------------------------------------------------------

async def test_plots_lists_remaining_assignments() -> None:
    row = _row("primary")
    ids = [row[0].id]
    token = _phone_token(ids)
    with patch(f"{_M}.phone_repo.list_active_access_rows_by_ids", AsyncMock(return_value=[row])) as mk, \
         patch(f"{_M}.plot_cycle_repo.get_latest_active_record_dates_for_cycles", AsyncMock(return_value={})):
        res = await _plots(payload=PublicPhoneAccessListRequest(phoneAccessSessionToken=token), request=AsyncMock(), db=_db())
    assert len(res.plots) == 1
    # re-queried by exactly the token's ids
    assert mk.call_args.args[1] == ids


async def test_plots_deactivated_assignment_disappears() -> None:
    token = _phone_token([uuid4()])
    with patch(f"{_M}.phone_repo.list_active_access_rows_by_ids", AsyncMock(return_value=[])), \
         patch(f"{_M}.plot_cycle_repo.get_latest_active_record_dates_for_cycles", AsyncMock(return_value={})):
        res = await _plots(payload=PublicPhoneAccessListRequest(phoneAccessSessionToken=token), request=AsyncMock(), db=_db())
    assert res.plots == []


async def test_plots_expired_or_bad_token_401() -> None:
    with pytest.raises(HTTPException) as exc:
        await _plots(payload=PublicPhoneAccessListRequest(phoneAccessSessionToken="not-a-jwt"), request=AsyncMock(), db=_db())
    assert exc.value.status_code == 401


async def test_plots_relist_includes_lot_no_and_planting_date() -> None:
    row = _row("primary")
    token = _phone_token([row[0].id])
    with patch(f"{_M}.phone_repo.list_active_access_rows_by_ids", AsyncMock(return_value=[row])), \
         patch(f"{_M}.plot_cycle_repo.get_latest_active_record_dates_for_cycles", AsyncMock(return_value={})):
        res = await _plots(payload=PublicPhoneAccessListRequest(phoneAccessSessionToken=token), request=AsyncMock(), db=_db())
    assert res.plots[0].lot_no == "LOT-01"
    assert res.plots[0].planting_date == datetime.date(2026, 6, 1)


async def test_plots_response_has_no_phone() -> None:
    row = _row("primary")
    token = _phone_token([row[0].id])
    with patch(f"{_M}.phone_repo.list_active_access_rows_by_ids", AsyncMock(return_value=[row])), \
         patch(f"{_M}.plot_cycle_repo.get_latest_active_record_dates_for_cycles", AsyncMock(return_value={})):
        res = await _plots(payload=PublicPhoneAccessListRequest(phoneAccessSessionToken=token), request=AsyncMock(), db=_db())
    assert _PHONE not in res.model_dump_json()


# --- select-plot ------------------------------------------------------------

def _decode(token: str) -> dict:
    s = get_settings()
    return jwt.decode(token, s.JWT_SECRET_KEY, algorithms=[s.JWT_ALGORITHM])


async def test_select_success_mints_phone_bound_inspection_token() -> None:
    supplier = _supplier()
    plot = _plot(supplier)
    access = _access("primary")
    token = _phone_token([access.id])
    with patch(f"{_M}.phone_repo.get_access_row_for_plot_from_ids", AsyncMock(return_value=access)), \
         patch(f"{_M}.plot_repo.get_plot", AsyncMock(return_value=plot)):
        res = await _select(
            payload=PublicPhoneAccessSelectPlotRequest(
                phoneAccessSessionToken=token, plotId=plot.id, inspectorType="farmer"),
            request=AsyncMock(), db=_db(),
        )
    claims = _decode(res.inspection_session_token)
    assert claims["type"] == "inspection_session"
    assert claims["plot_id"] == str(plot.id)
    assert claims["plot_cycle_id"] == str(plot.active_cycle.id)
    assert claims["plot_access_phone_id"] == str(access.id)
    assert claims["inspector_type"] == "farmer"
    # response carries no phone
    assert _PHONE not in res.model_dump_json()


@pytest.mark.parametrize("itype", ["farmer", "supplier", "chiatai"])
async def test_select_all_inspector_types(itype: str) -> None:
    supplier = _supplier()
    plot = _plot(supplier)
    access = _access("additional")
    token = _phone_token([access.id])
    with patch(f"{_M}.phone_repo.get_access_row_for_plot_from_ids", AsyncMock(return_value=access)), \
         patch(f"{_M}.plot_repo.get_plot", AsyncMock(return_value=plot)):
        res = await _select(
            payload=PublicPhoneAccessSelectPlotRequest(
                phoneAccessSessionToken=token, plotId=plot.id, inspectorType=itype),
            request=AsyncMock(), db=_db(),
        )
    assert _decode(res.inspection_session_token)["inspector_type"] == itype


def test_select_invalid_inspector_type_422() -> None:
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        PublicPhoneAccessSelectPlotRequest(
            phoneAccessSessionToken="t", plotId=uuid4(), inspectorType="agronomist")


async def test_select_revoked_assignment_generic_404() -> None:
    token = _phone_token([uuid4()])
    with patch(f"{_M}.phone_repo.get_access_row_for_plot_from_ids", AsyncMock(return_value=None)), \
         patch(f"{_M}.plot_repo.get_plot", AsyncMock()) as mk_plot:
        with pytest.raises(HTTPException) as exc:
            await _select(
                payload=PublicPhoneAccessSelectPlotRequest(
                    phoneAccessSessionToken=token, plotId=uuid4(), inspectorType="farmer"),
                request=AsyncMock(), db=_db(),
            )
    assert exc.value.status_code == 404
    mk_plot.assert_not_awaited()


async def test_select_inactive_plot_generic_404() -> None:
    supplier = _supplier()
    plot = _plot(supplier, is_active=False)
    access = _access("primary")
    token = _phone_token([access.id])
    with patch(f"{_M}.phone_repo.get_access_row_for_plot_from_ids", AsyncMock(return_value=access)), \
         patch(f"{_M}.plot_repo.get_plot", AsyncMock(return_value=plot)):
        with pytest.raises(HTTPException) as exc:
            await _select(
                payload=PublicPhoneAccessSelectPlotRequest(
                    phoneAccessSessionToken=token, plotId=plot.id, inspectorType="farmer"),
                request=AsyncMock(), db=_db(),
            )
    assert exc.value.status_code == 404


async def test_select_no_active_cycle_409_public_safe() -> None:
    supplier = _supplier()
    plot = _plot(supplier, active_cycle=None)
    access = _access("primary")
    token = _phone_token([access.id])
    with patch(f"{_M}.phone_repo.get_access_row_for_plot_from_ids", AsyncMock(return_value=access)), \
         patch(f"{_M}.plot_repo.get_plot", AsyncMock(return_value=plot)):
        with pytest.raises(HTTPException) as exc:
            await _select(
                payload=PublicPhoneAccessSelectPlotRequest(
                    phoneAccessSessionToken=token, plotId=plot.id, inspectorType="farmer"),
                request=AsyncMock(), db=_db(),
            )
    assert exc.value.status_code == 409
    assert exc.value.detail == {"code": "no_active_cycle"}


async def test_select_returns_plot_current_status_snapshot_verbatim() -> None:
    """Round 8-3J: current_yield_pct/current_stage/last_inspected_at come
    straight off the loaded Plot row — the SAME columns the logged-in
    RecordForm reads via plot_repository.sync_current_status_from_record —
    never recomputed from records or read off PlotCycle (expected_yield_full
    stays the 100%-target; current_yield_pct is a separate inspection
    snapshot, so the two must be free to differ)."""
    supplier = _supplier()
    inspected_at = datetime.datetime(2026, 7, 10, 9, 30, tzinfo=datetime.timezone.utc)
    plot = _plot(
        supplier,
        current_yield_pct=Decimal("62.5"),
        current_stage="ออกดอก",
        last_inspected_at=inspected_at,
    )
    access = _access("primary")
    token = _phone_token([access.id])
    with patch(f"{_M}.phone_repo.get_access_row_for_plot_from_ids", AsyncMock(return_value=access)), \
         patch(f"{_M}.plot_repo.get_plot", AsyncMock(return_value=plot)):
        res = await _select(
            payload=PublicPhoneAccessSelectPlotRequest(
                phoneAccessSessionToken=token, plotId=plot.id, inspectorType="farmer"),
            request=AsyncMock(), db=_db(),
        )
    assert res.current_yield_pct == Decimal("62.5")
    assert res.current_stage == "ออกดอก"
    assert res.last_inspected_at == inspected_at
    # Not derived from the active cycle's target — a different number proves it.
    assert res.current_yield_pct != plot.active_cycle.expected_yield_full


async def test_select_null_current_status_snapshot_returns_null() -> None:
    """A plot with no inspection history yet (current_yield_pct still NULL,
    e.g. right after a cycle rollover) must return null, not 0 or a computed
    fallback — the frontend, not this endpoint, decides the 100% default."""
    supplier = _supplier()
    plot = _plot(supplier)  # current_yield_pct/current_stage/last_inspected_at default None
    access = _access("primary")
    token = _phone_token([access.id])
    with patch(f"{_M}.phone_repo.get_access_row_for_plot_from_ids", AsyncMock(return_value=access)), \
         patch(f"{_M}.plot_repo.get_plot", AsyncMock(return_value=plot)):
        res = await _select(
            payload=PublicPhoneAccessSelectPlotRequest(
                phoneAccessSessionToken=token, plotId=plot.id, inspectorType="farmer"),
            request=AsyncMock(), db=_db(),
        )
    assert res.current_yield_pct is None
    assert res.current_stage is None
    assert res.last_inspected_at is None


def test_select_plot_response_schema_new_fields_are_nullable() -> None:
    """Round 8-3J contract: the three new fields default to None when
    omitted — additive, not required."""
    from app.schemas.phone_access import PublicPhoneAccessSelectPlotResponse

    res = PublicPhoneAccessSelectPlotResponse(
        inspectionSessionToken="t", expiresIn=60,
        plotId=uuid4(), plotCode="P001", plotName="Plot One",
        supplierId=uuid4(), supplierCode="SUP001", supplierName="Supplier One",
        plotCycleId=uuid4(), cycleNo=1,
    )
    assert res.current_yield_pct is None
    assert res.current_stage is None
    assert res.last_inspected_at is None


async def test_select_bad_phone_token_401() -> None:
    with pytest.raises(HTTPException) as exc:
        await _select(
            payload=PublicPhoneAccessSelectPlotRequest(
                phoneAccessSessionToken="not-a-jwt", plotId=uuid4(), inspectorType="farmer"),
            request=AsyncMock(), db=_db(),
        )
    assert exc.value.status_code == 401


# --- wiring: rate limits + RLS context --------------------------------------

def test_endpoints_are_rate_limited_and_rls_scoped() -> None:
    src = inspect.getsource(mod)
    assert src.count("get_public_plot_rls_context") >= 3  # all three POST endpoints
    assert '@limiter.limit("5/minute")' in src
    # /plots + /select-plot, and (round 8-9D) the GET /config capability probe.
    # The config route deliberately has NO RLS dependency: it runs no query, so
    # there is no session to scope — see test_public_inspection_access_config.py.
    assert src.count('@limiter.limit("30/minute")') == 3


def test_router_registered_under_public_prefix() -> None:
    import app.api.v1.installed_routers as ir
    entry = next(
        (prefix for r, prefix in ir.ROUTERS if r is mod.router), None
    )
    assert entry == "/api/v1/public"
