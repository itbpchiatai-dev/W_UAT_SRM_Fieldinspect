"""Round 8-8B.1 — relax plot_cycles.final_yield_pct's business cap from 150
to the NUMERIC(5,1) storage ceiling 9999.9 (migration 0045). Source
inspection (the local backend/alembic package shadows the installed alembic,
so the module can't be imported standalone — same approach as
test_records_yield_kg_migration.py / test_plot_cycle_actual_harvest_migration.py).
"""
from __future__ import annotations

import re
from pathlib import Path

_MIGRATION = (
    Path(__file__).resolve().parents[2]
    / "alembic" / "versions" / "2026_07_30_0100-0045_relax_yield_pct_cap.py"
)
_SRC = _MIGRATION.read_text(encoding="utf-8")


def _upgrade() -> str:
    return _SRC[_SRC.index("def upgrade"):_SRC.index("def downgrade")]


def _downgrade() -> str:
    return _SRC[_SRC.index("def downgrade"):]


def test_revision_chain() -> None:
    revision = re.search(r'^revision = "([^"]+)"', _SRC, re.MULTILINE).group(1)
    down = re.search(r'^down_revision = "([^"]+)"', _SRC, re.MULTILINE).group(1)
    assert revision == "0045_relax_yield_pct_cap"
    assert down == "0044_yield_quantity_kg"
    assert len(revision) <= 32  # alembic_version.version_num limit


def test_upgrade_drops_and_recreates_the_same_constraint_name() -> None:
    up = _upgrade()
    assert "DROP CONSTRAINT IF EXISTS ck_plot_cycles_final_yield_pct_range" in up
    assert "ADD CONSTRAINT ck_plot_cycles_final_yield_pct_range" in up
    assert "final_yield_pct <= 9999.9" in up
    # The old 150 ceiling must not survive into the upgraded constraint text
    # (it may still legitimately appear elsewhere, e.g. in a comment, so
    # scope this to the actual CHECK clause).
    check_start = up.index("CHECK (final_yield_pct")
    check_clause = up[check_start:up.index(");", check_start)]
    assert "150" not in check_clause


def test_upgrade_no_column_type_change_no_backfill_no_other_table() -> None:
    up = _upgrade()
    for token in ("ALTER COLUMN", "TYPE NUMERIC", "UPDATE ", "INSERT INTO", "DELETE "):
        assert token not in up, f"unexpected statement: {token}"
    for other in ("records", "plots ", "plot_access_phones", "suppliers"):
        assert other not in up, f"migration should not touch {other!r}"


def test_upgrade_no_rls_no_grant_no_ownership_change() -> None:
    up = _upgrade()
    for token in (
        "ENABLE ROW LEVEL SECURITY", "DISABLE ROW LEVEL SECURITY",
        "CREATE POLICY", "DROP POLICY", "ALTER POLICY", "GRANT ", "REVOKE ",
        "OWNER TO",
    ):
        assert token not in up, f"unexpected RLS/grant/ownership statement: {token}"


def test_downgrade_has_a_preflight_guard_that_aborts_on_data_over_150() -> None:
    """The downgrade must never silently revert to the old <=150 constraint
    while a row already holds a value that constraint would reject — it must
    RAISE EXCEPTION and abort instead of deleting/rewriting data to force
    the ALTER through (round 8-8B.1 Part C)."""
    down = _downgrade()
    assert "RAISE EXCEPTION" in down
    assert "final_yield_pct > 150" in down
    assert "DELETE " not in down
    assert "UPDATE " not in down
    # The guard must run BEFORE the constraint is reverted.
    assert down.index("RAISE EXCEPTION") < down.index("<= 150")


def test_downgrade_reverts_to_the_original_150_constraint_when_safe() -> None:
    down = _downgrade()
    assert "DROP CONSTRAINT IF EXISTS ck_plot_cycles_final_yield_pct_range" in down
    assert "ADD CONSTRAINT ck_plot_cycles_final_yield_pct_range" in down
    assert "final_yield_pct <= 150" in down
    assert "DROP TABLE" not in down


def test_model_metadata_matches_the_widened_constraint() -> None:
    """The PlotCycle model must declare the SAME 9999.9 ceiling this
    migration leaves behind, under the identical constraint name."""
    from app.db.models.plot_cycle import PlotCycle

    ck_defs = {
        c.name: str(c.sqltext)
        for c in PlotCycle.__table__.constraints
        if c.__class__.__name__ == "CheckConstraint"
    }
    assert "ck_plot_cycles_final_yield_pct_range" in ck_defs
    definition = ck_defs["ck_plot_cycles_final_yield_pct_range"]
    assert "9999.9" in definition
    assert "150" not in definition


def test_migration_0038_historical_text_is_unmodified() -> None:
    """Round 8-8B.1 must never edit historical migration 0038's own text to
    retroactively "fix" what it says — it correctly described the rule that
    was true when it was written."""
    migration_0038 = (
        Path(__file__).resolve().parents[2]
        / "alembic" / "versions" / "2026_07_17_0000-0038_cycle_final_estimate.py"
    )
    src_0038 = migration_0038.read_text(encoding="utf-8")
    assert "final_yield_pct >= 0 AND final_yield_pct <= 150" in src_0038
