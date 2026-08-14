"""Round 8-7A — PlotCycle actual-harvest fields (migration 0043). Source
inspection (the local backend/alembic package shadows the installed alembic,
so the module can't be imported standalone — same approach as
test_plot_cycle_po_lot_migration.py)."""
from __future__ import annotations

import re
from pathlib import Path

_MIGRATION = (
    Path(__file__).resolve().parents[2]
    / "alembic" / "versions" / "2026_07_28_0000-0043_actual_harvest.py"
)
_SRC = _MIGRATION.read_text(encoding="utf-8")


def _upgrade() -> str:
    return _SRC[_SRC.index("def upgrade"):_SRC.index("def downgrade")]


def _downgrade() -> str:
    return _SRC[_SRC.index("def downgrade"):]


def test_revision_chain() -> None:
    revision = re.search(r'^revision = "([^"]+)"', _SRC, re.MULTILINE).group(1)
    down = re.search(r'^down_revision = "([^"]+)"', _SRC, re.MULTILINE).group(1)
    assert revision == "0043_actual_harvest"
    assert down == "0042_plot_cycle_po_lot"
    assert len(revision) <= 32  # alembic_version.version_num limit


def test_adds_five_nullable_columns_no_backfill() -> None:
    up = _upgrade()
    assert "ADD COLUMN harvest_yield NUMERIC(14, 2)" in up
    assert "ADD COLUMN final_yield_after_clean NUMERIC(14, 2)" in up
    assert "ADD COLUMN final_yield_unit VARCHAR(20)" in up
    assert "ADD COLUMN harvest_date DATE" in up
    assert "ADD COLUMN final_note TEXT" in up
    # All nullable — no column-level NOT NULL on any ADD COLUMN (the only
    # "IS NOT NULL" occurrences are inside the all-or-none CHECK predicate),
    # no backfill/mutation.
    assert "NUMERIC(14, 2) NOT NULL" not in up
    assert "VARCHAR(20) NOT NULL" not in up
    assert "DATE NOT NULL" not in up
    assert "TEXT NOT NULL" not in up
    assert "SET NOT NULL" not in up
    assert "UPDATE " not in up
    assert "INSERT INTO" not in up
    assert "DELETE " not in up


def test_adds_three_check_constraints() -> None:
    up = _upgrade()
    assert "ck_plot_cycles_harvest_yield_non_negative" in up
    assert "harvest_yield IS NULL OR harvest_yield >= 0" in up
    assert "ck_plot_cycles_final_yield_after_clean_non_negative" in up
    assert "final_yield_after_clean IS NULL OR final_yield_after_clean >= 0" in up
    assert "ck_plot_cycles_actual_harvest_all_or_none" in up


def test_all_or_none_constraint_excludes_final_note() -> None:
    """final_note is independently optional — never part of the all-or-none
    group (harvest_yield/final_yield_after_clean/final_yield_unit/
    harvest_date)."""
    up = _upgrade()
    ck_start = up.index("ck_plot_cycles_actual_harvest_all_or_none")
    ck_clause = up[ck_start:up.index(");", ck_start)]
    assert "final_note" not in ck_clause


def test_no_migration_reuses_or_duplicates_final_inspection_record_id() -> None:
    """final_inspection_record_id already exists (migration 0038) — this
    migration must never re-declare or duplicate it."""
    up = _upgrade()
    assert "final_inspection_record_id" not in up


def test_upgrade_no_rls_no_grant_no_other_table() -> None:
    up = _upgrade()
    for token in (
        "ENABLE ROW LEVEL SECURITY", "DISABLE ROW LEVEL SECURITY",
        "CREATE POLICY", "DROP POLICY", "ALTER POLICY", "GRANT ", "REVOKE ",
        "OWNER TO",
    ):
        assert token not in up, f"unexpected RLS/grant/ownership statement: {token}"
    for other in ("records", "plots ", "plot_access_phones", "suppliers"):
        assert other not in up, f"migration should not touch {other!r}"


def test_downgrade_drops_constraints_then_columns() -> None:
    down = _downgrade()
    for ck in (
        "ck_plot_cycles_actual_harvest_all_or_none",
        "ck_plot_cycles_final_yield_after_clean_non_negative",
        "ck_plot_cycles_harvest_yield_non_negative",
    ):
        assert f"DROP CONSTRAINT IF EXISTS {ck}" in down
    for col in (
        "final_note", "harvest_date", "final_yield_unit",
        "final_yield_after_clean", "harvest_yield",
    ):
        assert f"DROP COLUMN IF EXISTS {col}" in down
    # Constraints dropped before the columns they reference.
    assert down.index("DROP CONSTRAINT") < down.index("DROP COLUMN IF EXISTS harvest_yield")
    assert "DROP TABLE" not in down


def test_model_metadata_matches_migration() -> None:
    """The PlotCycle model must declare exactly what this migration leaves
    behind: five nullable columns + three CHECK constraints."""
    from app.db.models.plot_cycle import PlotCycle

    cols = PlotCycle.__table__.c
    for name, length, sqltype in (
        ("harvest_yield", None, "NUMERIC"),
        ("final_yield_after_clean", None, "NUMERIC"),
        ("final_yield_unit", 20, "VARCHAR"),
        ("harvest_date", None, "DATE"),
        ("final_note", None, "TEXT"),
    ):
        assert name in cols, f"model missing {name}"
        assert cols[name].nullable is True, f"{name} must be nullable"
        assert sqltype in str(cols[name].type).upper()
        if length is not None:
            assert f"({length})" in str(cols[name].type)

    ck_names = {
        c.name for c in PlotCycle.__table__.constraints
        if c.__class__.__name__ == "CheckConstraint"
    }
    assert "ck_plot_cycles_harvest_yield_non_negative" in ck_names
    assert "ck_plot_cycles_final_yield_after_clean_non_negative" in ck_names
    assert "ck_plot_cycles_actual_harvest_all_or_none" in ck_names
