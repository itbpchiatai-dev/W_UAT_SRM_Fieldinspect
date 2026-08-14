"""Thai business date for "วันนี้" (round 8-19.1).

Round 8-19 resolved inspectedToday against the UTC date. Thailand is UTC+7
with no DST, so for the first seven hours of every Thai day (00:00-06:59 ICT)
the UTC date is still YESTERDAY: an inspection recorded at 01:00 ICT was
compared against the previous day and its plot card never flipped to
"ตรวจแล้ววันนี้".

_today() now resolves in Asia/Bangkok. Nothing about STORAGE changes — this is
purely how a stored calendar date (Record.record_date) is compared against
"now"; created_at and the DB's timezone are untouched.
"""
from __future__ import annotations

import datetime
import inspect
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4
from zoneinfo import ZoneInfo

import pytest

import app.api.v1.public_inspection_access as mod
from app.schemas.phone_access import PublicPhoneAccessLookupRequest

_lookup = mod.phone_access_lookup.__wrapped__
_M = "app.api.v1.public_inspection_access"
_PHONE = "0845552162"  # placeholder, never a real number

_UTC = datetime.timezone.utc
_BKK = ZoneInfo("Asia/Bangkok")


def _cycle(**o):
    d = dict(
        id=uuid4(), cycle_no=1, cycle_label="jun2026", crop=None, variety=None,
        lot_no=None, planting_date=None, plant_count=None,
        expected_yield_full=None, expected_yield_unit=None,
    )
    d.update(o)
    return SimpleNamespace(**d)


def _supplier():
    return SimpleNamespace(id=uuid4(), code="SUP001", name="Supplier One", is_active=True)


def _plot(supplier):
    return SimpleNamespace(
        id=uuid4(), plot_code="P001", name="Plot One", is_active=True,
        supplier_id=supplier.id, supplier=supplier, active_cycle=_cycle(),
        last_inspected_at=None, current_yield_pct=None, current_stage=None,
    )


def _access():
    return SimpleNamespace(id=uuid4(), access_type="primary", phone_normalized=_PHONE)


# --- _today() resolves in Asia/Bangkok --------------------------------------

def test_today_uses_the_bangkok_timezone_not_utc():
    assert mod._BANGKOK_TZ is not None
    assert str(mod._BANGKOK_TZ) == "Asia/Bangkok"
    src = inspect.getsource(mod._today)
    assert "_BANGKOK_TZ" in src
    assert "timezone.utc" not in src


def test_today_matches_the_real_bangkok_calendar_date():
    assert mod._today() == datetime.datetime.now(_BKK).date()


@pytest.mark.parametrize(
    "utc_moment,expected_thai_date",
    [
        # 16:59 UTC = 23:59 ICT — still the SAME Thai day.
        (datetime.datetime(2026, 8, 13, 16, 59, tzinfo=_UTC), datetime.date(2026, 8, 13)),
        # 17:00 UTC = 00:00 ICT the next day — the Thai day has rolled over.
        (datetime.datetime(2026, 8, 13, 17, 0, tzinfo=_UTC), datetime.date(2026, 8, 14)),
        # 00:00 UTC = 07:00 ICT — same date either way.
        (datetime.datetime(2026, 8, 13, 0, 0, tzinfo=_UTC), datetime.date(2026, 8, 13)),
        # 23:30 UTC = 06:30 ICT next day — the window the old UTC date got wrong.
        (datetime.datetime(2026, 8, 13, 23, 30, tzinfo=_UTC), datetime.date(2026, 8, 14)),
    ],
)
def test_bangkok_date_at_the_utc_day_boundary(utc_moment, expected_thai_date):
    assert utc_moment.astimezone(_BKK).date() == expected_thai_date


def test_the_early_morning_window_is_exactly_what_utc_got_wrong():
    """01:00 ICT: the UTC date is still the previous day — the bug."""
    moment = datetime.datetime(2026, 8, 13, 18, 0, tzinfo=_UTC)  # 01:00 ICT, Aug 14
    assert moment.date() == datetime.date(2026, 8, 13)               # old behaviour
    assert moment.astimezone(_BKK).date() == datetime.date(2026, 8, 14)  # new


# --- inspectedToday follows the Thai day ------------------------------------

async def _inspected_today_for(record_date, today):
    supplier = _supplier()
    plot = _plot(supplier)
    with patch(f"{_M}._today", return_value=today), \
         patch(f"{_M}.phone_repo.lookup_active_access_rows_by_phone",
               AsyncMock(return_value=[(_access(), plot, supplier)])), \
         patch(f"{_M}.plot_cycle_repo.get_latest_active_record_dates_for_cycles",
               AsyncMock(return_value={plot.active_cycle.id: record_date})):
        res = await _lookup(
            payload=PublicPhoneAccessLookupRequest(phone=_PHONE),
            request=AsyncMock(),
            db=AsyncMock(),
        )
    return res.plots[0]


async def test_record_on_the_current_thai_day_is_inspected_today():
    thai_today = datetime.date(2026, 8, 14)
    item = await _inspected_today_for(thai_today, thai_today)
    assert item.inspected_today is True
    assert item.last_inspection_date == thai_today


async def test_record_dated_the_previous_thai_day_is_not_inspected_today():
    item = await _inspected_today_for(datetime.date(2026, 8, 13), datetime.date(2026, 8, 14))
    assert item.inspected_today is False
    # The date is still reported — only the "today" flag differs.
    assert item.last_inspection_date == datetime.date(2026, 8, 13)


async def test_the_same_record_flips_when_the_thai_day_rolls_over():
    """One record dated Aug 14: "today" at 23:59 ICT on Aug 14, no longer
    "today" once ICT has ticked into Aug 15 — even though UTC is still Aug 14
    at that moment."""
    record_date = datetime.date(2026, 8, 14)
    before = await _inspected_today_for(record_date, datetime.date(2026, 8, 14))
    after = await _inspected_today_for(record_date, datetime.date(2026, 8, 15))
    assert before.inspected_today is True
    assert after.inspected_today is False


async def test_inspected_today_still_compares_against_record_date():
    """The comparison itself is unchanged by this round — only the value of
    "today" moved. A record's own date is what decides."""
    src = inspect.getsource(mod._plot_item)
    assert "last_inspection_date == _today()" in src


# --- nothing about storage moved -------------------------------------------

def test_the_module_never_rewrites_stored_timestamps():
    """Guard: this round changed a COMPARISON, not storage. No created_at
    assignment and no tz coercion of stored values appeared anywhere here."""
    src = inspect.getsource(mod)
    assert "created_at =" not in src
    assert "astimezone" not in src
