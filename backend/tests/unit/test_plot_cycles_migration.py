"""Round 7.1 — plot_cycles table + records.plot_cycle_id (migration 0034).

Source inspection (no DB fixture; the local backend/alembic package shadows
the installed `alembic` so the module can't be imported standalone — same
approach as test_protocol_constraints_migration.py). Verifies the table/
column/constraint/index shape, the phased-safe records.plot_cycle_id add →
backfill → NOT NULL, and the non-mutating preflight/postflight aborts.
"""
from __future__ import annotations

import re
from pathlib import Path

_MIGRATION = (
    Path(__file__).resolve().parents[2]
    / "alembic" / "versions" / "2026_07_13_0000-0034_plot_cycles.py"
)
_SRC = _MIGRATION.read_text(encoding="utf-8")


def _upgrade() -> str:
    return _SRC[_SRC.index("def upgrade"):_SRC.index("def downgrade")]


def _downgrade() -> str:
    return _SRC[_SRC.index("def downgrade"):]


def test_revision_chain() -> None:
    revision = re.search(r'^revision = "([^"]+)"', _SRC, re.MULTILINE).group(1)
    down = re.search(r'^down_revision = "([^"]+)"', _SRC, re.MULTILINE).group(1)
    assert revision == "0034_plot_cycles"
    assert down == "0033_protocol_constraints"
    assert len(revision) <= 32  # alembic_version.version_num limit


def test_creates_plot_cycles_table_with_columns() -> None:
    up = _upgrade()
    assert "CREATE TABLE plot_cycles" in up
    for col in (
        "plot_id UUID NOT NULL", "cycle_no INTEGER NOT NULL",
        "status VARCHAR(20) NOT NULL", "crop VARCHAR(100)",
        "variety VARCHAR(100)", "lot_no VARCHAR(100)", "planting_date DATE",
        "plant_count INTEGER", "expected_yield_full NUMERIC(12, 2)",
        "expected_yield_unit VARCHAR(20)", "started_at TIMESTAMPTZ NOT NULL",
        "closed_at TIMESTAMPTZ", "closed_by_id UUID", "close_reason TEXT",
    ):
        assert col in up, f"missing column def: {col}"


def test_table_constraints_and_indexes() -> None:
    up = _upgrade()
    # names match the ORM naming convention (test_plot_cycle_model asserts the
    # model side)
    assert "ck_plot_cycles_cycle_no_positive" in up and "cycle_no >= 1" in up
    assert "ck_plot_cycles_status_allowed" in up
    assert "status IN ('active', 'harvested', 'cancelled')" in up
    assert "ck_plot_cycles_plant_count_non_negative" in up
    assert "ck_plot_cycles_expected_yield_full_non_negative" in up
    assert "uq_plot_cycles_plot_id_cycle_no" in up
    assert "fk_plot_cycles_plot_id_plots" in up
    assert "ON DELETE RESTRICT" in up
    assert "fk_plot_cycles_closed_by_id_users" in up
    # the crucial invariant: at most one active cycle per plot
    assert "CREATE UNIQUE INDEX uq_plot_cycles_active_per_plot" in up
    assert "WHERE status = 'active'" in up


def test_adds_records_plot_cycle_id_phased_to_not_null() -> None:
    up = _upgrade()
    # nullable first
    assert "ALTER TABLE records ADD COLUMN plot_cycle_id UUID;" in up
    assert "fk_records_plot_cycle_id_plot_cycles" in up
    assert "ix_records_plot_cycle_id" in up
    # ... NOT NULL only AFTER the backfill (index-order check)
    add_at = up.index("ADD COLUMN plot_cycle_id")
    notnull_at = up.index("ALTER COLUMN plot_cycle_id SET NOT NULL")
    backfill_at = up.index("UPDATE records r")
    assert add_at < backfill_at < notnull_at


def test_backfills_one_cycle_per_plot_and_links_records() -> None:
    up = _upgrade()
    assert "INSERT INTO plot_cycles" in up
    assert "FROM plots p" in up
    # cycle_no = 1, status by the plot's permanent is_active flag
    assert "CASE WHEN p.is_active THEN 'active' ELSE 'cancelled' END" in up
    # master data copied from the plot mirror
    for src in ("p.current_crop", "p.current_variety", "p.current_lot_no",
                "p.current_planting_date", "p.plant_count",
                "p.expected_yield_full", "p.expected_yield_unit"):
        assert src in up, f"backfill should copy {src}"
    # started_at = planting date else plot creation time
    assert "COALESCE(p.current_planting_date::timestamptz, p.created_at)" in up
    # every record linked to its plot's cycle_no=1
    assert "UPDATE records r" in up
    assert "c.plot_id = r.plot_id AND c.cycle_no = 1" in up
    # backfilled cancelled cycles must NOT claim a real harvest
    assert "harvested" not in up.split("INSERT INTO plot_cycles")[1].split("UPDATE records")[0] \
        or "original close reason unknown" in up


def test_preflight_and_postflight_abort_without_autofix() -> None:
    up = _upgrade()
    # preflight: orphan records (no plot) abort
    assert "Preflight (0034)" in up
    assert "LEFT JOIN plots p" in up
    # postflight: unlinked records + multi-active abort before NOT NULL
    assert "Postflight (0034)" in up
    assert "plot_cycle_id IS NULL" in up
    assert "more than one active cycle" in up
    assert up.count("RAISE EXCEPTION") >= 3


def test_downgrade_drops_column_and_table() -> None:
    down = _downgrade()
    assert "DROP CONSTRAINT IF EXISTS fk_records_plot_cycle_id_plot_cycles" in down
    assert "DROP COLUMN IF EXISTS plot_cycle_id" in down
    assert "DROP TABLE IF EXISTS plot_cycles" in down
