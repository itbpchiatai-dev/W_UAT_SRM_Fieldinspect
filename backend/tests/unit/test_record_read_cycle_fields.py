"""RecordRead cycle read-model (round 7.4).

_to_read denormalises the record's planting-cycle detail (status/crop/variety/
lot/plan) from the eager-loaded Record.plot_cycle relationship — the record's
OWN cycle, bound at create time — NOT the plot's current mirror. RecordSummary
stays lightweight (cycle number only) so the history list carries no per-row
cycle-detail overhead. DB-free: builds a record-like SimpleNamespace and calls
the mapper directly, matching test_record_create_endpoint.py's approach.
"""
from __future__ import annotations

import datetime
from decimal import Decimal
from types import SimpleNamespace
from uuid import uuid4

from app.api.v1.records import _to_read, _to_summary
from app.schemas.record import RecordRead, RecordSummary


def _fake_cycle(**overrides) -> SimpleNamespace:
    defaults = dict(
        id=uuid4(), cycle_no=2, status="active",
        # User-facing season name (round 8.0.5) — see Record.cycle_label.
        cycle_label="jun2026",
        crop="พริก", variety="พริกขี้หนู", lot_no="LOT-02",
        planting_date=datetime.date(2026, 6, 1), plant_count=500,
        expected_yield_full=Decimal("1000.00"), expected_yield_unit="kg",
    )
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def _fake_record(*, plot_cycle=None, **overrides) -> SimpleNamespace:
    defaults = dict(
        id=uuid4(), plot_id=uuid4(), supplier_id=uuid4(), recorded_by_id=uuid4(),
        submitted_by_code="FIELD01", submitted_by_name=None,
        record_date=datetime.date(2026, 7, 1),
        crop="พริก", variety=None, growth_stage=None, planting_date=None,
        yield_pct=Decimal("100"),
        weather_condition=None, field_prep_score=None, weather_score=None,
        care_score=None, variety_resistance_score=None,
        recommendation=None, notes=None, latitude=None, longitude=None,
        photo_urls=[], custom_fields={}, is_active=True,
        created_at=datetime.datetime(2026, 7, 1, tzinfo=datetime.timezone.utc),
        updated_at=datetime.datetime(2026, 7, 1, tzinfo=datetime.timezone.utc),
        # relationships _to_read/_to_summary read
        plot=None, supplier=None, recorded_by=None, plot_cycle=plot_cycle,
    )
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def test_record_read_has_cycle_detail_fields_defaulting_none() -> None:
    fields = RecordRead.model_fields
    for name in (
        "cycle_status", "cycle_label", "cycle_crop", "cycle_variety", "cycle_lot_no",
        "cycle_planting_date", "cycle_plant_count",
        "cycle_expected_yield_full", "cycle_expected_yield_unit",
    ):
        assert name in fields, name
        assert fields[name].default is None


def test_record_summary_stays_lightweight_cycle_number_and_label_only() -> None:
    # The history list needs the cycle NUMBER and LABEL (round 8.0.5, for the
    # cycleLabel-then-"รอบที่ N" display) but not the full cycle detail — that
    # lives on RecordRead, fetched per-record on demand.
    fields = RecordSummary.model_fields
    assert "cycle_no" in fields
    assert "cycle_label" in fields
    assert "plot_cycle_id" in fields
    assert "cycle_crop" not in fields
    assert "cycle_expected_yield_full" not in fields


def test_to_read_populates_cycle_detail_from_record_plot_cycle() -> None:
    read = _to_read(_fake_record(plot_cycle=_fake_cycle()))
    assert read.cycle_no == 2
    assert read.cycle_status == "active"
    assert read.cycle_label == "jun2026"
    assert read.cycle_crop == "พริก"
    assert read.cycle_variety == "พริกขี้หนู"
    assert read.cycle_lot_no == "LOT-02"
    assert read.cycle_planting_date == datetime.date(2026, 6, 1)
    assert read.cycle_plant_count == 500
    assert read.cycle_expected_yield_full == Decimal("1000.00")
    assert read.cycle_expected_yield_unit == "kg"


def test_to_read_cycle_fields_none_when_record_has_no_cycle() -> None:
    read = _to_read(_fake_record(plot_cycle=None))
    assert read.cycle_no is None
    assert read.cycle_status is None
    assert read.cycle_label is None
    assert read.cycle_crop is None
    assert read.cycle_expected_yield_full is None


def test_to_read_cycle_is_records_own_not_its_snapshot() -> None:
    # The record's own crop snapshot differs from the cycle's crop, proving
    # cycle_* is sourced from record.plot_cycle — not record.crop / a mirror.
    cycle = _fake_cycle(crop="เมล่อน")
    read = _to_read(_fake_record(crop="พริกเก่า", plot_cycle=cycle))
    assert read.crop == "พริกเก่า"
    assert read.cycle_crop == "เมล่อน"


def test_to_read_cycle_label_falls_back_to_none_when_cycle_predates_the_field() -> None:
    read = _to_read(_fake_record(plot_cycle=_fake_cycle(cycle_label=None)))
    assert read.cycle_label is None
    assert read.cycle_no == 2  # rest of the cycle detail still populates


def test_to_summary_populates_cycle_no_and_label_from_record_plot_cycle() -> None:
    summary = _to_summary(_fake_record(plot_cycle=_fake_cycle(cycle_no=3, cycle_label="jul2026")))
    assert summary.cycle_no == 3
    assert summary.cycle_label == "jul2026"


def test_to_summary_cycle_label_none_when_record_has_no_cycle() -> None:
    summary = _to_summary(_fake_record(plot_cycle=None))
    assert summary.cycle_no is None
    assert summary.cycle_label is None


def test_to_summary_populates_yield_quantity_kg_and_target_snapshot_verbatim() -> None:
    """Round 8-8C — RecordSummary.yieldQuantityKg/yieldTargetKgSnapshot are
    plain Record columns (migration 0044), read straight off the row by
    RecordSummary.model_validate(record) inside _to_summary — never
    recomputed, never touching plot_cycle."""
    record = _fake_record(
        yield_quantity_kg=Decimal("800.00"), yield_target_kg_snapshot=Decimal("1000.00"),
    )
    summary = _to_summary(record)
    assert summary.yield_quantity_kg == Decimal("800.00")
    assert summary.yield_target_kg_snapshot == Decimal("1000.00")


def test_to_summary_yield_quantity_kg_zero_is_not_null() -> None:
    record = _fake_record(
        yield_quantity_kg=Decimal("0.00"), yield_target_kg_snapshot=Decimal("1000.00"),
    )
    summary = _to_summary(record)
    assert summary.yield_quantity_kg == Decimal("0.00")
    assert summary.yield_quantity_kg is not None


def test_to_summary_yield_kg_fields_none_for_legacy_record() -> None:
    record = _fake_record()  # no yield_quantity_kg/yield_target_kg_snapshot attr at all
    assert not hasattr(record, "yield_quantity_kg")
    assert not hasattr(record, "yield_target_kg_snapshot")
    summary = _to_summary(record)
    assert summary.yield_quantity_kg is None
    assert summary.yield_target_kg_snapshot is None


def test_to_summary_yield_kg_fields_need_no_extra_query() -> None:
    """Same proof as the cycle-fields test below, for the kg fields: a bare
    SimpleNamespace with no db/session object still round-trips through
    _to_summary fine, because yield_quantity_kg/yield_target_kg_snapshot are
    plain columns already present on whatever the caller's SELECT loaded —
    no selectinload, no lazy relationship, nothing that could trigger a
    query if this really were a live ORM instance."""
    record = _fake_record(
        yield_quantity_kg=Decimal("800.00"), yield_target_kg_snapshot=Decimal("1000.00"),
    )
    assert not hasattr(record, "db") and not hasattr(record, "session")
    summary = _to_summary(record)
    assert summary.yield_quantity_kg == Decimal("800.00")


def test_to_read_and_to_summary_use_the_eager_loaded_relationship_no_extra_query() -> None:
    """Round 8.0.5 — cycle_label must come from the ALREADY eager-loaded
    Record.plot_cycle relationship (selectinload in record_repository's
    _with_relations/list_records), never a fresh per-record lookup. Both
    mappers are plain attribute access on the SimpleNamespace fixture here
    (no db/session object exists at all) — if either read triggered a real
    query, it would need a session to run against and this test would
    error, not silently pass."""
    cycle = _fake_cycle()
    record = _fake_record(plot_cycle=cycle)
    assert not hasattr(record, "db") and not hasattr(record, "session")
    read = _to_read(record)
    summary = _to_summary(record)
    assert read.cycle_label == cycle.cycle_label
    assert summary.cycle_label == cycle.cycle_label
