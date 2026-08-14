"""Latest inspection date scoped to the ACTIVE plot cycle (round 8-19).

/public/inspect's authorized-plot list used to answer "ตรวจแล้ววันนี้" from
Record.plot_access_phone_id (phone_repo.access_phone_ids_inspected_today), so
the SAME plot reported different statuses to its เบอร์หลัก and its เบอร์เสริม —
field work done from one number was invisible to the other. It also showed
plot.last_inspected_at, a Plot-level denormalized column that is not scoped to
a cycle at all, so a brand-new cycle could display the previous cycle's
inspection.

Round 8-19 makes both answers plot+cycle level, keyed by PlotCycle.id (never
cycleLabel text, which two cycles can share, and never plot_id, which would
let a closed cycle's records leak in).

DB-less, matching this repo's convention: repository tests compile the
statement (literal binds) to inspect the SQL; endpoint tests call the route
via `.__wrapped__` and patch the repository.
"""
from __future__ import annotations

import datetime
import inspect
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4

from sqlalchemy.dialects import postgresql

import app.api.v1.public_inspection_access as mod
from app.repositories.plot_cycle_repository import (
    get_latest_active_record_dates_for_cycles,
    get_latest_active_records_for_cycles,
)
from app.schemas.phone_access import (
    PublicPhoneAccessListRequest,
    PublicPhoneAccessLookupRequest,
)

_lookup = mod.phone_access_lookup.__wrapped__
_plots = mod.phone_access_plots.__wrapped__
_M = "app.api.v1.public_inspection_access"
_PHONE = "0845552162"  # placeholder, never a real number

_TODAY = datetime.datetime.now(datetime.timezone.utc).date()
_YESTERDAY = _TODAY - datetime.timedelta(days=1)


def _compiled(stmt) -> str:
    # Postgres dialect explicitly: DISTINCT ON is a PG feature that the
    # default dialect silently DROPS from the rendered SQL, which would make
    # the assertions below pass against a statement that never dedupes.
    return str(
        stmt.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True})
    )


class _FakeRowResult:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return self._rows


def _capturing_db(rows=()):
    captured: dict = {}

    async def _execute(stmt):
        captured["stmt"] = stmt
        return _FakeRowResult(list(rows))

    return SimpleNamespace(execute=_execute), captured


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


def _phone_token(ids):
    from app.auth.phone_access_session import encode_phone_access_session_token

    token, _ = encode_phone_access_session_token(access_phone_ids=ids)
    return token


async def _lookup_items(rows, latest_dates):
    with patch(f"{_M}.phone_repo.lookup_active_access_rows_by_phone", AsyncMock(return_value=rows)), \
         patch(f"{_M}.plot_cycle_repo.get_latest_active_record_dates_for_cycles",
               AsyncMock(return_value=latest_dates)) as batch:
        res = await _lookup(
            payload=PublicPhoneAccessLookupRequest(phone=_PHONE),
            request=AsyncMock(),
            db=AsyncMock(),
        )
    return res.plots, batch


# --- repository: one cycle-keyed batch query --------------------------------

async def test_query_is_scoped_by_plot_cycle_id_not_label_or_plot():
    cycle_id = uuid4()
    db, captured = _capturing_db()
    await get_latest_active_record_dates_for_cycles(db, [cycle_id])
    compiled = _compiled(captured["stmt"])
    assert "records.plot_cycle_id IN" in compiled
    # The PG dialect renders a UUID literal in its hyphenated form.
    assert str(cycle_id) in compiled
    # Never joined/filtered on the label text or the plot itself.
    assert "cycle_label" not in compiled
    assert "records.plot_id" not in compiled


async def test_query_counts_active_records_only():
    db, captured = _capturing_db()
    await get_latest_active_record_dates_for_cycles(db, [uuid4()])
    assert "records.is_active IS true" in _compiled(captured["stmt"])


async def test_query_orders_record_date_then_created_at_then_id():
    db, captured = _capturing_db()
    await get_latest_active_record_dates_for_cycles(db, [uuid4()])
    compiled = _compiled(captured["stmt"])
    order = compiled.split("ORDER BY")[1]
    assert order.index("records.record_date DESC") < order.index("records.created_at DESC")
    assert order.index("records.created_at DESC") < order.index("records.id DESC")


async def test_query_picks_one_row_per_cycle_via_distinct_on():
    db, captured = _capturing_db()
    await get_latest_active_record_dates_for_cycles(db, [uuid4()])
    compiled = _compiled(captured["stmt"])
    assert "DISTINCT ON (records.plot_cycle_id)" in compiled


async def test_query_is_a_single_batch_for_many_cycles_no_n_plus_1():
    cycle_ids = [uuid4() for _ in range(25)]
    calls = 0

    async def _execute(stmt):
        nonlocal calls
        calls += 1
        return _FakeRowResult([])

    await get_latest_active_record_dates_for_cycles(
        SimpleNamespace(execute=_execute), cycle_ids,
    )
    assert calls == 1


async def test_query_short_circuits_on_an_empty_cycle_list():
    calls = 0

    async def _execute(stmt):
        nonlocal calls
        calls += 1
        return _FakeRowResult([])

    result = await get_latest_active_record_dates_for_cycles(
        SimpleNamespace(execute=_execute), [],
    )
    assert result == {}
    assert calls == 0


async def test_query_returns_a_date_per_cycle():
    c1, c2 = uuid4(), uuid4()
    rows = [
        SimpleNamespace(plot_cycle_id=c1, record_date=_TODAY),
        SimpleNamespace(plot_cycle_id=c2, record_date=_YESTERDAY),
    ]
    db, _ = _capturing_db(rows)
    assert await get_latest_active_record_dates_for_cycles(db, [c1, c2]) == {
        c1: _TODAY, c2: _YESTERDAY,
    }


def test_the_audit_helper_keeps_its_created_at_only_ordering():
    """Regression guard: the pre-existing batch helper feeds the close-time
    ESTIMATE snapshot and the Excel finalInspectionRecordId, which want the
    last row WRITTEN (created_at), not the last date REPORTED. Round 8-19
    added a sibling rather than retuning this one — if these ever merge, that
    snapshot silently changes meaning."""
    src = inspect.getsource(get_latest_active_records_for_cycles)
    assert "created_at.desc()" in src
    assert "record_date" not in src


# --- endpoint: lookup -------------------------------------------------------

async def test_record_today_in_active_cycle_sets_inspected_today_and_date():
    supplier = _supplier()
    plot = _plot(supplier)
    items, _ = await _lookup_items(
        [(_access(), plot, supplier)], {plot.active_cycle.id: _TODAY},
    )
    assert items[0].inspected_today is True
    assert items[0].last_inspection_date == _TODAY


async def test_older_record_in_active_cycle_keeps_the_date_but_not_today():
    supplier = _supplier()
    plot = _plot(supplier)
    items, _ = await _lookup_items(
        [(_access(), plot, supplier)], {plot.active_cycle.id: _YESTERDAY},
    )
    assert items[0].inspected_today is False
    assert items[0].last_inspection_date == _YESTERDAY


async def test_active_cycle_with_no_record_reports_null():
    supplier = _supplier()
    plot = _plot(supplier)
    items, _ = await _lookup_items([(_access(), plot, supplier)], {})
    assert items[0].last_inspection_date is None
    assert items[0].inspected_today is False
    # can_inspect is unaffected — the plot IS inspectable, just not yet done.
    assert items[0].can_inspect is True


async def test_a_previous_cycles_record_never_surfaces_on_the_new_cycle():
    """The old cycle has records; the new (active) one does not. The batch is
    keyed by the ACTIVE cycle's id, so the old cycle's entry can't be read."""
    supplier = _supplier()
    plot = _plot(supplier)
    old_cycle_id = uuid4()
    items, batch = await _lookup_items(
        [(_access(), plot, supplier)], {old_cycle_id: _TODAY},
    )
    assert items[0].last_inspection_date is None
    assert items[0].inspected_today is False
    # Only the ACTIVE cycle id was ever asked about.
    assert batch.await_args.args[1] == [plot.active_cycle.id]
    assert old_cycle_id not in batch.await_args.args[1]


async def test_when_both_cycles_have_records_the_active_one_wins():
    supplier = _supplier()
    plot = _plot(supplier)
    old_cycle_id = uuid4()
    items, _ = await _lookup_items(
        [(_access(), plot, supplier)],
        {old_cycle_id: _TODAY, plot.active_cycle.id: _YESTERDAY},
    )
    assert items[0].last_inspection_date == _YESTERDAY
    assert items[0].inspected_today is False


async def test_a_plot_with_no_active_cycle_shows_no_date_at_all():
    supplier = _supplier()
    plot = _plot(supplier, active_cycle=None)
    items, batch = await _lookup_items([(_access(), plot, supplier)], {})
    assert items[0].last_inspection_date is None
    assert items[0].inspected_today is False
    assert items[0].can_inspect is False
    assert items[0].unavailable_reason == "no_active_cycle"
    # It contributes no cycle id, so it can never pick up a stale date.
    assert batch.await_args.args[1] == []


async def test_a_stale_plot_last_inspected_at_does_not_become_the_cycle_date():
    """plot.last_inspected_at is the Plot-level denormalized timestamp and is
    NOT cycle-scoped — it must never stand in for the cycle's own date."""
    supplier = _supplier()
    plot = _plot(
        supplier,
        last_inspected_at=datetime.datetime(2020, 1, 1, tzinfo=datetime.timezone.utc),
    )
    items, _ = await _lookup_items([(_access(), plot, supplier)], {})
    assert items[0].last_inspection_date is None
    assert items[0].inspected_today is False


async def test_primary_and_additional_phones_see_identical_status():
    """The whole point of round 8-19 — the old plot_access_phone_id keying
    made these two disagree."""
    supplier = _supplier()
    plot = _plot(supplier)
    latest = {plot.active_cycle.id: _TODAY}
    seen = []
    for access_type in ("primary", "additional"):
        items, _ = await _lookup_items([(_access(access_type), plot, supplier)], latest)
        seen.append((items[0].inspected_today, items[0].last_inspection_date))
    assert seen[0] == (True, _TODAY)
    assert seen[0] == seen[1]


async def test_many_plots_still_use_exactly_one_batch_call():
    supplier = _supplier()
    plots = [_plot(supplier) for _ in range(10)]
    rows = [(_access(), p, supplier) for p in plots]
    latest = {p.active_cycle.id: _TODAY for p in plots}
    items, batch = await _lookup_items(rows, latest)
    assert len(items) == 10
    assert batch.await_count == 1
    assert sorted(map(str, batch.await_args.args[1])) == sorted(
        str(p.active_cycle.id) for p in plots
    )
    assert all(i.inspected_today for i in items)


async def test_the_old_per_access_row_helper_is_no_longer_used_for_this_list():
    """Guard against a revert: if access_phone_ids_inspected_today comes back
    into _build_items, primary/additional disagree again."""
    src = inspect.getsource(mod._build_items)
    # The CALL, not the name — the docstring legitimately explains why the
    # old helper was dropped, so a bare substring check would self-trip.
    assert "phone_repo.access_phone_ids_inspected_today" not in src
    assert "plot_cycle_repo.get_latest_active_record_dates_for_cycles" in src


# --- endpoint: relist (same contract as lookup) -----------------------------

async def test_relist_uses_the_same_cycle_scoped_contract():
    supplier = _supplier()
    plot = _plot(supplier)
    access = _access()
    with patch(f"{_M}.phone_repo.list_active_access_rows_by_ids",
               AsyncMock(return_value=[(access, plot, supplier)])), \
         patch(f"{_M}.plot_cycle_repo.get_latest_active_record_dates_for_cycles",
               AsyncMock(return_value={plot.active_cycle.id: _TODAY})) as batch:
        res = await _plots(
            payload=PublicPhoneAccessListRequest(
                phoneAccessSessionToken=_phone_token([str(access.id)]),
            ),
            request=AsyncMock(),
            db=AsyncMock(),
        )
    assert res.plots[0].inspected_today is True
    assert res.plots[0].last_inspection_date == _TODAY
    assert batch.await_count == 1


async def test_relist_after_an_inspection_flips_the_status_to_today():
    """Re-fetching is what turns the card into "ตรวจแล้ววันนี้" — the same
    plot, same session, only the batch result differs."""
    supplier = _supplier()
    plot = _plot(supplier)
    access = _access()
    token = _phone_token([str(access.id)])

    async def _relist(latest):
        with patch(f"{_M}.phone_repo.list_active_access_rows_by_ids",
                   AsyncMock(return_value=[(access, plot, supplier)])), \
             patch(f"{_M}.plot_cycle_repo.get_latest_active_record_dates_for_cycles",
                   AsyncMock(return_value=latest)):
            res = await _plots(
                payload=PublicPhoneAccessListRequest(phoneAccessSessionToken=token),
                request=AsyncMock(),
                db=AsyncMock(),
            )
        return res.plots[0]

    before = await _relist({})
    after = await _relist({plot.active_cycle.id: _TODAY})
    assert (before.inspected_today, before.last_inspection_date) == (False, None)
    assert (after.inspected_today, after.last_inspection_date) == (True, _TODAY)


# --- contract shape ---------------------------------------------------------

async def test_list_item_still_carries_no_phone_and_keeps_last_inspected_at():
    supplier = _supplier()
    plot = _plot(supplier)
    items, _ = await _lookup_items(
        [(_access(), plot, supplier)], {plot.active_cycle.id: _TODAY},
    )
    dumped = items[0].model_dump(by_alias=True)
    assert "lastInspectionDate" in dumped
    assert "inspectedToday" in dumped
    # Round 8-19 item 7 — the pre-existing field stays, no breaking change.
    assert "lastInspectedAt" in dumped
    for forbidden in ("phone", "phoneNormalized", "qrKey", "accessPhoneId", "inspectionCode"):
        assert forbidden not in dumped


async def test_last_inspection_date_serializes_as_a_plain_date_no_time():
    supplier = _supplier()
    plot = _plot(supplier)
    items, _ = await _lookup_items(
        [(_access(), plot, supplier)], {plot.active_cycle.id: _TODAY},
    )
    assert items[0].model_dump(mode="json", by_alias=True)["lastInspectionDate"] == _TODAY.isoformat()
