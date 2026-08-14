"""Round 8-8A — records yield-in-kg columns (migration 0044). Source
inspection (the local backend/alembic package shadows the installed alembic,
so the module can't be imported standalone — same approach as
test_plot_cycle_actual_harvest_migration.py)."""
from __future__ import annotations

import re
from pathlib import Path

_MIGRATION = (
    Path(__file__).resolve().parents[2]
    / "alembic" / "versions" / "2026_07_30_0000-0044_yield_quantity_kg.py"
)
_SRC = _MIGRATION.read_text(encoding="utf-8")


def _upgrade() -> str:
    return _SRC[_SRC.index("def upgrade"):_SRC.index("def downgrade")]


def _downgrade() -> str:
    return _SRC[_SRC.index("def downgrade"):]


def test_revision_chain() -> None:
    revision = re.search(r'^revision = "([^"]+)"', _SRC, re.MULTILINE).group(1)
    down = re.search(r'^down_revision = "([^"]+)"', _SRC, re.MULTILINE).group(1)
    assert revision == "0044_yield_quantity_kg"
    assert down == "0043_actual_harvest"
    assert len(revision) <= 32  # alembic_version.version_num limit


def test_adds_two_nullable_columns_no_backfill() -> None:
    up = _upgrade()
    assert "ADD COLUMN yield_quantity_kg NUMERIC(12, 2)" in up
    assert "ADD COLUMN yield_target_kg_snapshot NUMERIC(12, 2)" in up
    assert "NUMERIC(12, 2) NOT NULL" not in up
    assert "SET NOT NULL" not in up
    assert "UPDATE " not in up
    assert "INSERT INTO" not in up
    assert "DELETE " not in up


def test_adds_two_check_constraints() -> None:
    up = _upgrade()
    assert "ck_records_yield_quantity_kg_non_negative" in up
    assert "yield_quantity_kg IS NULL OR yield_quantity_kg >= 0" in up
    assert "ck_records_yield_target_kg_snapshot_non_negative" in up
    assert "yield_target_kg_snapshot IS NULL OR yield_target_kg_snapshot >= 0" in up


def test_upgrade_no_rls_no_grant_no_other_table() -> None:
    up = _upgrade()
    for token in (
        "ENABLE ROW LEVEL SECURITY", "DISABLE ROW LEVEL SECURITY",
        "CREATE POLICY", "DROP POLICY", "ALTER POLICY", "GRANT ", "REVOKE ",
        "OWNER TO",
    ):
        assert token not in up, f"unexpected RLS/grant/ownership statement: {token}"
    for other in ("plot_cycles", "plots ", "plot_access_phones", "suppliers"):
        assert other not in up, f"migration should not touch {other!r}"


def test_downgrade_drops_constraints_then_columns() -> None:
    down = _downgrade()
    for ck in (
        "ck_records_yield_target_kg_snapshot_non_negative",
        "ck_records_yield_quantity_kg_non_negative",
    ):
        assert f"DROP CONSTRAINT IF EXISTS {ck}" in down
    for col in ("yield_target_kg_snapshot", "yield_quantity_kg"):
        assert f"DROP COLUMN IF EXISTS {col}" in down
    assert down.index("DROP CONSTRAINT") < down.index("DROP COLUMN IF EXISTS yield_quantity_kg")
    assert "DROP TABLE" not in down


def test_model_metadata_matches_migration() -> None:
    """The Record model must declare exactly what this migration leaves
    behind: two nullable NUMERIC(12,2) columns + two CHECK constraints."""
    from app.db.models.record import Record

    cols = Record.__table__.c
    for name in ("yield_quantity_kg", "yield_target_kg_snapshot"):
        assert name in cols, f"model missing {name}"
        assert cols[name].nullable is True, f"{name} must be nullable"
        assert "NUMERIC" in str(cols[name].type).upper()
        assert "(12, 2)" in str(cols[name].type)

    ck_names = {
        c.name for c in Record.__table__.constraints
        if c.__class__.__name__ == "CheckConstraint"
    }
    assert "ck_records_yield_quantity_kg_non_negative" in ck_names
    assert "ck_records_yield_target_kg_snapshot_non_negative" in ck_names


def test_yield_pct_column_untouched() -> None:
    """This round must not touch the pre-existing yield_pct column's
    precision/nullability/default — only add the two new kg columns."""
    from app.db.models.record import Record

    col = Record.__table__.c["yield_pct"]
    assert col.nullable is True
    assert "NUMERIC" in str(col.type).upper()
    assert "(5, 1)" in str(col.type)
