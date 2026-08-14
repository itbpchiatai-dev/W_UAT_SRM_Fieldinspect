"""Round 8-5A — PlotCycle PO / P.Code + Auto Lot metadata (migration 0042).
Source inspection (the local backend/alembic package shadows the installed
alembic, so the module can't be imported standalone — same approach as the
other migration tests)."""
from __future__ import annotations

import re
from pathlib import Path

_MIGRATION = (
    Path(__file__).resolve().parents[2]
    / "alembic" / "versions" / "2026_07_21_0000-0042_plot_cycle_po_lot.py"
)
_SRC = _MIGRATION.read_text(encoding="utf-8")


def _upgrade() -> str:
    return _SRC[_SRC.index("def upgrade"):_SRC.index("def downgrade")]


def _downgrade() -> str:
    return _SRC[_SRC.index("def downgrade"):]


def test_revision_chain() -> None:
    revision = re.search(r'^revision = "([^"]+)"', _SRC, re.MULTILINE).group(1)
    down = re.search(r'^down_revision = "([^"]+)"', _SRC, re.MULTILINE).group(1)
    assert revision == "0042_plot_cycle_po_lot"
    assert down == "0041_offline_submission"
    assert len(revision) <= 32  # alembic_version.version_num limit


def test_adds_four_nullable_columns() -> None:
    up = _upgrade()
    assert "ADD COLUMN po_number VARCHAR(100)" in up
    assert "ADD COLUMN p_code VARCHAR(100)" in up
    assert "ADD COLUMN lot_no_source VARCHAR(20)" in up
    assert "ADD COLUMN lot_running_no INTEGER" in up
    # All nullable — no column-level NOT NULL on any ADD COLUMN (the only
    # "IS NOT NULL" occurrences are inside the auto-lot CHECK predicate), and
    # no backfill/mutation.
    assert "VARCHAR(100) NOT NULL" not in up
    assert "VARCHAR(20) NOT NULL" not in up
    assert "INTEGER NOT NULL" not in up
    assert "SET NOT NULL" not in up
    assert "UPDATE " not in up
    assert "INSERT INTO" not in up
    assert "DELETE " not in up


def test_adds_three_check_constraints() -> None:
    up = _upgrade()
    assert "ck_plot_cycles_lot_no_source_allowed" in up
    assert "lot_no_source IN ('auto', 'manual', 'legacy')" in up
    assert "ck_plot_cycles_lot_running_no_positive" in up
    assert "lot_running_no IS NULL OR lot_running_no >= 1" in up
    assert "ck_plot_cycles_auto_lot_requires_fields" in up
    # auto rows must carry po_number + lot_no + lot_running_no; NULL-safe form.
    assert "lot_no_source IS DISTINCT FROM 'auto'" in up


def test_partial_unique_index_only_covers_auto_rows() -> None:
    up = _upgrade()
    assert "CREATE UNIQUE INDEX uq_plot_cycles_auto_lot_running" in up
    assert "ON plot_cycles (plot_id, po_number, lot_running_no)" in up
    assert "WHERE lot_no_source = 'auto'" in up


def test_upgrade_no_global_lot_no_unique_no_rls_no_grant() -> None:
    up = _upgrade()
    # No global unique on lot_no this round (plot_code is only supplier-scoped).
    assert "UNIQUE (lot_no)" not in up
    assert "uq_plot_cycles_lot_no " not in up
    for token in (
        "ENABLE ROW LEVEL SECURITY", "DISABLE ROW LEVEL SECURITY",
        "CREATE POLICY", "DROP POLICY", "ALTER POLICY", "GRANT ", "REVOKE ",
        "OWNER TO",
    ):
        assert token not in up, f"unexpected RLS/grant/ownership statement: {token}"
    # Touches only plot_cycles.
    for other in ("records", "plots ", "plot_access_phones", "suppliers"):
        assert other not in up, f"migration should not touch {other!r}"


def test_downgrade_drops_index_constraints_then_columns() -> None:
    down = _downgrade()
    assert "DROP INDEX IF EXISTS uq_plot_cycles_auto_lot_running" in down
    for ck in (
        "ck_plot_cycles_auto_lot_requires_fields",
        "ck_plot_cycles_lot_running_no_positive",
        "ck_plot_cycles_lot_no_source_allowed",
    ):
        assert f"DROP CONSTRAINT IF EXISTS {ck}" in down
    for col in ("lot_running_no", "lot_no_source", "p_code", "po_number"):
        assert f"DROP COLUMN IF EXISTS {col}" in down
    # Index dropped before the columns it covers.
    assert down.index("DROP INDEX") < down.index("DROP COLUMN IF EXISTS po_number")
    # Downgrade removes ONLY what this round added — never the pre-existing
    # plot_cycles columns/constraints.
    assert "DROP TABLE" not in down
    assert "lot_no " not in down.replace("lot_no_source", "").replace("lot_running_no", "")


def test_model_metadata_matches_migration() -> None:
    """The PlotCycle model must declare exactly what this migration leaves
    behind: four nullable columns, three CHECK constraints, and the partial
    unique index with the same name."""
    from app.db.models.plot_cycle import PlotCycle

    cols = PlotCycle.__table__.c
    for name, length, sqltype in (
        ("po_number", 100, "VARCHAR"),
        ("p_code", 100, "VARCHAR"),
        ("lot_no_source", 20, "VARCHAR"),
        ("lot_running_no", None, "INTEGER"),
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
    assert "ck_plot_cycles_lot_no_source_allowed" in ck_names
    assert "ck_plot_cycles_lot_running_no_positive" in ck_names
    assert "ck_plot_cycles_auto_lot_requires_fields" in ck_names

    idx = next(
        (i for i in PlotCycle.__table__.indexes
         if i.name == "uq_plot_cycles_auto_lot_running"),
        None,
    )
    assert idx is not None, "model is missing uq_plot_cycles_auto_lot_running"
    assert idx.unique is True
    assert [c.name for c in idx.columns] == ["plot_id", "po_number", "lot_running_no"]
