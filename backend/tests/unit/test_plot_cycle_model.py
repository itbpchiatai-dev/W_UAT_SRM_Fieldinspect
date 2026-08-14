"""PlotCycle model + records.plot_cycle_id — metadata inspection (round 7.1).

No DB fixture in this repo; assert the ORM declares the columns, constraints,
partial-unique index and relationships the migration 0034 creates, so model
metadata and the DB agree.
"""
from __future__ import annotations

from app.db.models.plot import Plot
from app.db.models.plot_cycle import (
    CYCLE_CLOSED_STATUSES,
    CYCLE_STATUS_ACTIVE,
    CYCLE_STATUS_CANCELLED,
    CYCLE_STATUS_HARVESTED,
    CYCLE_STATUSES,
    PlotCycle,
)
from app.db.models.record import Record


def test_status_constants() -> None:
    assert CYCLE_STATUS_ACTIVE == "active"
    assert CYCLE_STATUS_HARVESTED == "harvested"
    assert CYCLE_STATUS_CANCELLED == "cancelled"
    assert CYCLE_STATUSES == ("active", "harvested", "cancelled")
    # active is deliberately NOT a closed state.
    assert CYCLE_CLOSED_STATUSES == ("harvested", "cancelled")
    assert CYCLE_STATUS_ACTIVE not in CYCLE_CLOSED_STATUSES


def test_table_and_columns() -> None:
    cols = PlotCycle.__table__.c
    for name in (
        "id", "plot_id", "cycle_no", "status", "crop", "variety", "lot_no",
        "planting_date", "plant_count", "expected_yield_full",
        "expected_yield_unit", "started_at", "closed_at", "closed_by_id",
        "close_reason", "created_at", "updated_at",
    ):
        assert name in cols, f"missing column {name}"
    assert cols["plot_id"].nullable is False
    assert cols["cycle_no"].nullable is False
    assert cols["status"].nullable is False
    assert cols["started_at"].nullable is False
    # close fields optional (only set when closed)
    assert cols["closed_at"].nullable is True
    assert cols["closed_by_id"].nullable is True
    assert cols["close_reason"].nullable is True


def test_declares_constraints_matching_migration() -> None:
    names = {c.name for c in PlotCycle.__table__.constraints if c.name}
    assert "ck_plot_cycles_cycle_no_positive" in names
    assert "ck_plot_cycles_status_allowed" in names
    assert "ck_plot_cycles_plant_count_non_negative" in names
    assert "ck_plot_cycles_expected_yield_full_non_negative" in names
    assert "uq_plot_cycles_plot_id_cycle_no" in names
    # FKs → plots / users
    assert "fk_plot_cycles_plot_id_plots" in names
    assert "fk_plot_cycles_closed_by_id_users" in names


def test_partial_unique_active_index() -> None:
    idx = {i.name: i for i in PlotCycle.__table__.indexes}
    assert "uq_plot_cycles_active_per_plot" in idx
    active_idx = idx["uq_plot_cycles_active_per_plot"]
    assert active_idx.unique is True
    # partial: only status='active' rows participate
    where = active_idx.dialect_options["postgresql"]["where"]
    assert "active" in str(where)


def test_plot_id_fk_ondelete_restrict() -> None:
    fk = next(iter(PlotCycle.__table__.c["plot_id"].foreign_keys))
    assert fk.column.table.name == "plots"
    assert fk.ondelete == "RESTRICT"


def test_records_have_plot_cycle_id_not_null_fk() -> None:
    col = Record.__table__.c["plot_cycle_id"]
    assert col.nullable is False
    fk = next(iter(col.foreign_keys))
    assert fk.column.table.name == "plot_cycles"
    assert fk.ondelete == "RESTRICT"


def test_relationships_wired() -> None:
    assert "cycles" in Plot.__mapper__.relationships
    assert "plot_cycle" in Record.__mapper__.relationships
    assert "records" in PlotCycle.__mapper__.relationships
    assert "plot" in PlotCycle.__mapper__.relationships
