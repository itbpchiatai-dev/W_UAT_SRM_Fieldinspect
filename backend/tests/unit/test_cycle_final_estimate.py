"""Round 8-2.8A — final estimated-yield snapshot: migration 0038 shape, model
metadata, read-schema serialization, and close-path wiring.

Source inspection for the migration (the local backend/alembic package shadows
the installed `alembic`, so the module can't be imported standalone — same
approach as test_plot_cycles_migration.py); ORM/Pydantic metadata for the
model + schema; getsource for the wiring guards.
"""
from __future__ import annotations

import datetime
import inspect
import re
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

from app.db.models.plot_cycle import PlotCycle
from app.schemas.plot import PlotCycleCreate, PlotCycleRead, PlotCycleUpdate

_MIGRATION = (
    Path(__file__).resolve().parents[2]
    / "alembic" / "versions" / "2026_07_17_0000-0038_cycle_final_estimate.py"
)
_SRC = _MIGRATION.read_text(encoding="utf-8")


def _upgrade() -> str:
    return _SRC[_SRC.index("def upgrade"):_SRC.index("def downgrade")]


def _downgrade() -> str:
    return _SRC[_SRC.index("def downgrade"):]


# --- migration: revision chain + length -----------------------------------

def test_revision_chain_and_length() -> None:
    revision = re.search(r'^revision = "([^"]+)"', _SRC, re.MULTILINE).group(1)
    down = re.search(r'^down_revision = "([^"]+)"', _SRC, re.MULTILINE).group(1)
    assert revision == "0038_cycle_final_estimate"
    assert down == "0037_rls_uuid_guard"
    assert len(revision) <= 32  # alembic_version.version_num limit


# --- migration: columns / types / nullability -----------------------------

def test_upgrade_adds_nullable_columns_with_types() -> None:
    up = _upgrade()
    assert "ADD COLUMN final_yield_pct NUMERIC(5, 1)" in up
    assert "ADD COLUMN final_estimated_yield NUMERIC(14, 2)" in up
    assert "ADD COLUMN final_inspection_record_id UUID" in up
    # nullable (no NOT NULL on these adds)
    assert "final_yield_pct NUMERIC(5, 1) NOT NULL" not in up
    assert "final_estimated_yield NUMERIC(14, 2) NOT NULL" not in up


# --- migration: FK + CHECK constraints ------------------------------------

def test_upgrade_adds_fk_and_check_constraints() -> None:
    up = _upgrade()
    assert "fk_plot_cycles_final_inspection_record_id_records" in up
    assert "REFERENCES records(id) ON DELETE SET NULL" in up
    assert "ck_plot_cycles_final_yield_pct_range" in up
    assert "final_yield_pct >= 0 AND final_yield_pct <= 150" in up
    assert "ck_plot_cycles_final_estimated_yield_non_negative" in up
    assert "final_estimated_yield IS NULL OR final_estimated_yield >= 0" in up


# --- migration: no backfill / no data mutation ----------------------------

def test_upgrade_has_no_backfill_or_data_mutation() -> None:
    # Statement-level bans — "ON DELETE SET NULL" is a constraint clause, not a
    # DELETE statement, so ban the statement forms.
    up = _upgrade().upper()
    for banned in ("UPDATE ", "INSERT INTO", "DELETE FROM", "SELECT "):
        assert banned not in up, f"migration must not mutate/backfill data ({banned!r})"


# --- migration: downgrade drops everything --------------------------------

def test_downgrade_drops_constraints_then_columns() -> None:
    down = _downgrade()
    for frag in (
        "DROP CONSTRAINT IF EXISTS ck_plot_cycles_final_estimated_yield_non_negative",
        "DROP CONSTRAINT IF EXISTS ck_plot_cycles_final_yield_pct_range",
        "DROP CONSTRAINT IF EXISTS fk_plot_cycles_final_inspection_record_id_records",
        "DROP COLUMN IF EXISTS final_inspection_record_id",
        "DROP COLUMN IF EXISTS final_estimated_yield",
        "DROP COLUMN IF EXISTS final_yield_pct",
    ):
        assert frag in down, f"downgrade missing: {frag}"
    # FK dropped before its column
    assert down.index("DROP CONSTRAINT IF EXISTS fk_plot_cycles_final") < \
        down.index("DROP COLUMN IF EXISTS final_inspection_record_id")


# --- model metadata matches migration -------------------------------------

def test_model_declares_final_columns_matching_migration() -> None:
    cols = PlotCycle.__table__.c
    assert str(cols["final_yield_pct"].type) == "NUMERIC(5, 1)"
    assert str(cols["final_estimated_yield"].type) == "NUMERIC(14, 2)"
    for n in ("final_yield_pct", "final_estimated_yield", "final_inspection_record_id"):
        assert cols[n].nullable is True, f"{n} must be nullable"


def test_model_fk_ondelete_set_null_and_check_constraints() -> None:
    cols = PlotCycle.__table__.c
    fk = next(iter(cols["final_inspection_record_id"].foreign_keys))
    assert fk.column.table.name == "records"
    assert fk.ondelete == "SET NULL"
    names = {c.name for c in PlotCycle.__table__.constraints if c.name}
    assert "ck_plot_cycles_final_yield_pct_range" in names
    assert "ck_plot_cycles_final_estimated_yield_non_negative" in names


# --- read schema: default None; old cycle (no attrs) serializes -----------

def _old_cycle_ns(**over) -> SimpleNamespace:
    now = datetime.datetime.now(datetime.timezone.utc)
    base = dict(
        id=uuid4(), plot_id=uuid4(), cycle_no=1, status="harvested",
        crop=None, variety=None, cycle_label=None, lot_no=None, planting_date=None,
        plant_count=None, expected_yield_full=None, expected_yield_unit=None,
        started_at=now, closed_at=now, closed_by_id=None, close_reason=None,
        created_at=now, updated_at=now,
    )
    base.update(over)
    return SimpleNamespace(**base)


def test_read_schema_old_cycle_without_final_attrs_serializes_as_null() -> None:
    # A cycle object that predates 0038 (no final_* attributes at all) must
    # validate and serialize with the fields defaulting to None — no crash.
    read = PlotCycleRead.model_validate(_old_cycle_ns())
    dumped = read.model_dump(by_alias=True)
    assert dumped["finalYieldPct"] is None
    assert dumped["finalEstimatedYield"] is None
    assert dumped["finalInspectionRecordId"] is None


def test_read_schema_emits_camel_final_fields() -> None:
    rid = uuid4()
    read = PlotCycleRead.model_validate(_old_cycle_ns(
        final_yield_pct=None, final_estimated_yield=None, final_inspection_record_id=rid,
    ))
    dumped = read.model_dump(by_alias=True)
    assert dumped["finalInspectionRecordId"] == rid


def test_final_fields_absent_from_create_and_update_schemas() -> None:
    # Read-only: never client-supplied.
    for schema in (PlotCycleCreate, PlotCycleUpdate):
        fields = set(schema.model_fields)
        assert "final_yield_pct" not in fields
        assert "final_estimated_yield" not in fields
        assert "final_inspection_record_id" not in fields


# --- close-path wiring (all paths funnel through the shared close_cycle) ---

def test_rollover_cycle_delegates_to_shared_close_cycle() -> None:
    from app.repositories import plot_cycle_repository as repo
    src = inspect.getsource(repo.rollover_cycle)
    assert "close_cycle(" in src  # rollover closes via the shared helper


def test_close_and_rollover_endpoints_use_shared_helpers() -> None:
    from app.api.v1 import plots as plots_api
    src = inspect.getsource(plots_api)
    assert "plot_cycle_repo.close_cycle(" in src        # close endpoint
    assert "plot_cycle_repo.rollover_cycle(" in src     # rollover endpoint


def test_excel_import_rollovers_use_shared_rollover() -> None:
    from app.services import plot_import
    src = inspect.getsource(plot_import)
    # close_and_start_new_cycle AND start_next_cycle-resolved-to-rollover both
    # go through the shared rollover_cycle (→ close_cycle → snapshot).
    assert src.count("plot_cycle_repo.rollover_cycle(") >= 2


def test_snapshot_is_taken_before_status_flip_in_close_cycle() -> None:
    from app.repositories import plot_cycle_repository as repo
    src = inspect.getsource(repo.close_cycle)
    assert "_snapshot_final_estimate(" in src
    assert src.index("_snapshot_final_estimate(") < src.index("cycle.status = status")
