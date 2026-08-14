"""Round 8-8A — RecordCreate/PublicRecordCreate/RecordRead contract for the
yield-in-kg fields. No DB — schema-level validation and read-model mapping
only (matches test_cycle_final_estimate.py's schema-test style).
"""
from __future__ import annotations

import datetime
from decimal import Decimal
from types import SimpleNamespace
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.schemas.record import PublicRecordCreate, RecordCreate, RecordRead, RecordSummary, RecordUpdate


def _create_payload(**overrides) -> dict:
    defaults = dict(
        plot_id=uuid4(), supplier_id=uuid4(),
        record_date=datetime.date(2026, 7, 1),
    )
    defaults.update(overrides)
    return defaults


def test_record_create_accepts_optional_yield_quantity_kg() -> None:
    payload = RecordCreate(**_create_payload(yield_quantity_kg="800.5"))
    assert payload.yield_quantity_kg == Decimal("800.5")


def test_record_create_yield_quantity_kg_defaults_to_none() -> None:
    payload = RecordCreate(**_create_payload())
    assert payload.yield_quantity_kg is None


def test_record_create_rejects_negative_yield_quantity_kg() -> None:
    with pytest.raises(ValidationError):
        RecordCreate(**_create_payload(yield_quantity_kg="-1"))


# --- round 8-8A.1: Decimal boundary hardening (YieldQuantityKg) -------------

def test_record_create_rejects_more_than_two_decimal_places() -> None:
    """Round 8-8A.1 bug #2 / contract: reject, never silently round, a
    quantity with more than 2 decimal places."""
    with pytest.raises(ValidationError):
        RecordCreate(**_create_payload(yield_quantity_kg="123.456"))


def test_record_create_accepts_max_numeric_12_2_value() -> None:
    """The exact NUMERIC(12,2) ceiling (9,999,999,999.99) must be accepted —
    it fits the column precisely."""
    payload = RecordCreate(**_create_payload(yield_quantity_kg="9999999999.99"))
    assert payload.yield_quantity_kg == Decimal("9999999999.99")


def test_record_create_rejects_value_over_numeric_12_2_ceiling() -> None:
    """One digit beyond the column's capacity — 422 at the API boundary,
    never a DB overflow error at insert time (round 8-8A.1 bug #2)."""
    with pytest.raises(ValidationError):
        RecordCreate(**_create_payload(yield_quantity_kg="99999999999.99"))


def test_public_record_create_rejects_more_than_two_decimal_places() -> None:
    with pytest.raises(ValidationError):
        PublicRecordCreate(
            inspection_session_token="tok",
            record_date=datetime.date(2026, 7, 1),
            yield_quantity_kg="123.456",
        )


def test_public_record_create_accepts_max_numeric_12_2_value() -> None:
    payload = PublicRecordCreate(
        inspection_session_token="tok",
        record_date=datetime.date(2026, 7, 1),
        yield_quantity_kg="9999999999.99",
    )
    assert payload.yield_quantity_kg == Decimal("9999999999.99")


def test_public_record_create_rejects_value_over_numeric_12_2_ceiling() -> None:
    with pytest.raises(ValidationError):
        PublicRecordCreate(
            inspection_session_token="tok",
            record_date=datetime.date(2026, 7, 1),
            yield_quantity_kg="99999999999.99",
        )


def test_record_create_and_public_record_create_share_the_same_yield_quantity_kg_type() -> None:
    """Part B's "one shared type/validator, never duplicated" contract,
    proven directly: both schemas' field annotation is the literal same
    Annotated object."""
    assert (
        RecordCreate.model_fields["yield_quantity_kg"].annotation
        == PublicRecordCreate.model_fields["yield_quantity_kg"].annotation
    )


def test_public_record_create_accepts_optional_yield_quantity_kg() -> None:
    payload = PublicRecordCreate(
        inspection_session_token="tok",
        record_date=datetime.date(2026, 7, 1),
        yield_quantity_kg="500",
    )
    assert payload.yield_quantity_kg == Decimal("500")


def test_public_record_create_rejects_negative_yield_quantity_kg() -> None:
    with pytest.raises(ValidationError):
        PublicRecordCreate(
            inspection_session_token="tok",
            record_date=datetime.date(2026, 7, 1),
            yield_quantity_kg="-0.01",
        )


def test_public_record_create_rejects_forged_target_snapshot() -> None:
    """extra="forbid" on PublicRecordCreate — a client sending
    yieldTargetKgSnapshot must be rejected (422), never silently ignored."""
    with pytest.raises(ValidationError):
        PublicRecordCreate(
            inspection_session_token="tok",
            record_date=datetime.date(2026, 7, 1),
            yield_target_kg_snapshot="999999",
        )


def test_yield_target_kg_snapshot_absent_from_create_schemas() -> None:
    """Server-derived only — never a field a client can set on either create
    schema (same "reject/ignore, never trust" principle as plot_cycle_id)."""
    assert "yield_target_kg_snapshot" not in RecordCreate.model_fields
    assert "yield_target_kg_snapshot" not in PublicRecordCreate.model_fields


def test_record_read_has_both_yield_kg_fields_defaulting_none() -> None:
    fields = RecordRead.model_fields
    for name in ("yield_quantity_kg", "yield_target_kg_snapshot"):
        assert name in fields, name
        assert fields[name].default is None


def _fake_record(**overrides) -> SimpleNamespace:
    defaults = dict(
        id=uuid4(), plot_id=uuid4(), supplier_id=uuid4(), recorded_by_id=uuid4(),
        submitted_by_code=None, submitted_by_name=None,
        record_date=datetime.date(2026, 7, 1),
        crop=None, variety=None, growth_stage=None, planting_date=None,
        yield_pct=Decimal("100"),
        weather_condition=None, field_prep_score=None, weather_score=None,
        care_score=None, variety_resistance_score=None,
        recommendation=None, notes=None, latitude=None, longitude=None,
        photo_urls=[], custom_fields={}, is_active=True,
        created_at=datetime.datetime(2026, 7, 1, tzinfo=datetime.timezone.utc),
        updated_at=datetime.datetime(2026, 7, 1, tzinfo=datetime.timezone.utc),
        plot=None, supplier=None, recorded_by=None, plot_cycle=None,
    )
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def test_record_read_old_record_without_yield_kg_attrs_serializes_as_null() -> None:
    """A record created before this round has no yield_quantity_kg/
    yield_target_kg_snapshot ATTRIBUTE AT ALL (not just None) — RecordRead
    must still validate and serialize both as null, never crash."""
    old_record = _fake_record()
    assert not hasattr(old_record, "yield_quantity_kg")
    assert not hasattr(old_record, "yield_target_kg_snapshot")

    read = RecordRead.model_validate(old_record)
    dumped = read.model_dump(by_alias=True)
    assert dumped["yieldQuantityKg"] is None
    assert dumped["yieldTargetKgSnapshot"] is None


def test_record_read_emits_camel_yield_kg_fields_when_present() -> None:
    record = _fake_record(
        yield_quantity_kg=Decimal("800.00"), yield_target_kg_snapshot=Decimal("1000.00"),
    )
    read = RecordRead.model_validate(record)
    dumped = read.model_dump(by_alias=True)
    assert dumped["yieldQuantityKg"] == Decimal("800.00")
    assert dumped["yieldTargetKgSnapshot"] == Decimal("1000.00")


# --- round 8-8B.1: yield_pct range widened 150 -> 9999.9 (warning, not cap) -

def test_record_create_accepts_yield_pct_over_150() -> None:
    """150 used to be RecordCreate.yield_pct's hard ceiling; it's now only a
    non-blocking frontend warning threshold — the schema must accept it."""
    payload = RecordCreate(**_create_payload(yield_pct="200.0"))
    assert payload.yield_pct == Decimal("200.0")


def test_record_create_accepts_yield_pct_at_9999_point_9() -> None:
    payload = RecordCreate(**_create_payload(yield_pct="9999.9"))
    assert payload.yield_pct == Decimal("9999.9")


def test_record_create_rejects_yield_pct_over_9999_point_9() -> None:
    with pytest.raises(ValidationError):
        RecordCreate(**_create_payload(yield_pct="10000.0"))


def test_record_update_accepts_yield_pct_over_150() -> None:
    payload = RecordUpdate(yield_pct="300.0")
    assert payload.yield_pct == Decimal("300.0")


def test_record_update_rejects_yield_pct_over_9999_point_9() -> None:
    with pytest.raises(ValidationError):
        RecordUpdate(yield_pct="10000.0")


def test_public_record_create_accepts_yield_pct_over_150() -> None:
    """PublicRecordCreate.yield_pct had NO upper bound at all before round
    8-8B.1 (a latent numeric-overflow gap, round 8-8B.1 Part A/B) — now
    bounded the same way as RecordCreate: accepts >150, rejects >9999.9."""
    payload = PublicRecordCreate(
        inspection_session_token="tok",
        record_date=datetime.date(2026, 7, 1),
        yield_pct="200.0",
    )
    assert payload.yield_pct == Decimal("200.0")


def test_public_record_create_rejects_yield_pct_over_9999_point_9() -> None:
    with pytest.raises(ValidationError):
        PublicRecordCreate(
            inspection_session_token="tok",
            record_date=datetime.date(2026, 7, 1),
            yield_pct="10000.0",
        )


# --- round 8-8C: RecordSummary carries the same read-only kg fields --------
# (the history-list read model — record_repository.list_records / _to_summary
# in app/api/v1/records.py) so RecordList/PlotDetail history can show kg
# without a per-row RecordRead fetch. Same schema-level contract as
# RecordRead above: both fields read-only, defaulting None, never a create-
# payload field on any schema.

def test_record_summary_has_both_yield_kg_fields_defaulting_none() -> None:
    fields = RecordSummary.model_fields
    for name in ("yield_quantity_kg", "yield_target_kg_snapshot"):
        assert name in fields, name
        assert fields[name].default is None


def test_record_summary_old_record_without_yield_kg_attrs_serializes_as_null() -> None:
    """A record created before round 8-8A has no yield_quantity_kg/
    yield_target_kg_snapshot ATTRIBUTE AT ALL — RecordSummary must still
    validate and serialize both as null, never crash, same as RecordRead."""
    old_record = _fake_record()
    assert not hasattr(old_record, "yield_quantity_kg")
    assert not hasattr(old_record, "yield_target_kg_snapshot")

    summary = RecordSummary.model_validate(old_record)
    dumped = summary.model_dump(by_alias=True)
    assert dumped["yieldQuantityKg"] is None
    assert dumped["yieldTargetKgSnapshot"] is None


def test_record_summary_emits_camel_yield_kg_fields_when_present() -> None:
    record = _fake_record(
        yield_quantity_kg=Decimal("800.00"), yield_target_kg_snapshot=Decimal("1000.00"),
    )
    summary = RecordSummary.model_validate(record)
    dumped = summary.model_dump(by_alias=True)
    assert dumped["yieldQuantityKg"] == Decimal("800.00")
    assert dumped["yieldTargetKgSnapshot"] == Decimal("1000.00")


def test_record_summary_yield_quantity_kg_zero_is_not_null() -> None:
    """quantity=0 is a real, storable value (a genuinely-zero harvest) — must
    round-trip as 0, never coerced to/confused with the legacy-null case."""
    record = _fake_record(
        yield_quantity_kg=Decimal("0.00"), yield_target_kg_snapshot=Decimal("1000.00"),
    )
    summary = RecordSummary.model_validate(record)
    assert summary.yield_quantity_kg == Decimal("0.00")
    assert summary.yield_quantity_kg is not None
