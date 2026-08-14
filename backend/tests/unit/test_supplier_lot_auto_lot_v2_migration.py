"""Round 8-12A — migration 0048: plot_cycles.supplier_lot_no +
auto_lot_series_key, the reworked auto-lot CHECK, and the two partial unique
indexes that back Auto Lot V1/V2 concurrency.

Source inspection (the local backend/alembic package shadows the installed
alembic, so the module can't be imported standalone — same approach as the
other migration tests), plus model-metadata agreement checks.
"""
from __future__ import annotations

import re
from pathlib import Path

from app.db.models.plot_cycle import PlotCycle

_MIGRATION = (
    Path(__file__).resolve().parents[2]
    / "alembic" / "versions" / "2026_08_05_0000-0048_supplier_lot_auto_lot_v2.py"
)
_SRC = _MIGRATION.read_text(encoding="utf-8")


def _upgrade() -> str:
    return _SRC[_SRC.index("def upgrade"):_SRC.index("def downgrade")]


def _downgrade() -> str:
    return _SRC[_SRC.index("def downgrade"):]


# --- revision chain ---------------------------------------------------------

def test_revision_chain() -> None:
    revision = re.search(r'^revision = "([^"]+)"', _SRC, re.MULTILINE).group(1)
    down = re.search(r'^down_revision = "([^"]+)"', _SRC, re.MULTILINE).group(1)
    assert revision == "0048_supplier_lot_auto_lot_v2"
    assert down == "0047_inspector_type_chiatai"
    assert len(revision) <= 32  # alembic_version.version_num limit


# --- columns ----------------------------------------------------------------

def test_adds_both_columns_nullable() -> None:
    up = _upgrade()
    assert "ADD COLUMN supplier_lot_no VARCHAR(100)" in up
    assert "ADD COLUMN auto_lot_series_key VARCHAR(255)" in up
    # Nullable — no NOT NULL, so every existing row is valid untouched.
    assert "supplier_lot_no VARCHAR(100) NOT NULL" not in up
    assert "auto_lot_series_key VARCHAR(255) NOT NULL" not in up


def test_model_matches_the_migration_column_types() -> None:
    cols = PlotCycle.__table__.columns
    assert cols["supplier_lot_no"].nullable is True
    assert cols["supplier_lot_no"].type.length == 100
    assert cols["auto_lot_series_key"].nullable is True
    assert cols["auto_lot_series_key"].type.length == 255


# --- the reworked auto-lot CHECK -------------------------------------------

def test_auto_lot_check_accepts_v1_by_po_and_v2_by_series_key() -> None:
    """V1 rows carry a po_number; V2 rows carry a series key and no PO in the
    formula. Requiring a PO (the old rule) would block every new row; dropping
    the rule entirely would let a half-built auto row through."""
    up = _upgrade()
    assert "ck_plot_cycles_auto_lot_requires_fields" in up
    assert "lot_no_source IS DISTINCT FROM 'auto'" in up
    assert "lot_no IS NOT NULL" in up
    assert "lot_running_no IS NOT NULL" in up
    assert "auto_lot_series_key IS NOT NULL OR po_number IS NOT NULL" in up


def test_model_check_constraint_mirrors_the_migration() -> None:
    """Round 8-12A.1 (migration 0049) strengthened this CHECK: the V1 and V2
    branches are now explicit, and a V2 row must carry the components its lot
    number was rendered from. See test_auto_lot_v2_integrity_migration.py for
    the 0049 assertions themselves."""
    checks = [
        str(c.sqltext) for c in PlotCycle.__table__.constraints
        if getattr(c, "name", None) == "ck_plot_cycles_auto_lot_requires_fields"
    ]
    assert len(checks) == 1
    sql = checks[0]
    assert "auto_lot_series_key IS NULL AND po_number IS NOT NULL" in sql
    assert "auto_lot_series_key IS NOT NULL" in sql


# --- indexes ----------------------------------------------------------------

def test_v1_index_is_rebuilt_scoped_to_legacy_rows_not_dropped() -> None:
    """The V1 backstop must keep protecting V1 rows. It is recreated with an
    extra 'series key IS NULL' predicate so it no longer also constrains V2
    rows (which still store a po_number and would collide across series)."""
    up = _upgrade()
    assert "uq_plot_cycles_auto_lot_running" in up
    assert "ON plot_cycles (plot_id, po_number, lot_running_no)" in up
    assert "WHERE lot_no_source = 'auto' AND auto_lot_series_key IS NULL" in up


def test_v2_series_index_is_added() -> None:
    up = _upgrade()
    assert "uq_plot_cycles_auto_lot_series_running" in up
    assert "ON plot_cycles (auto_lot_series_key, lot_running_no)" in up
    assert "WHERE auto_lot_series_key IS NOT NULL" in up


def test_model_declares_both_partial_unique_indexes() -> None:
    by_name = {ix.name: ix for ix in PlotCycle.__table__.indexes}
    v1 = by_name["uq_plot_cycles_auto_lot_running"]
    v2 = by_name["uq_plot_cycles_auto_lot_series_running"]
    assert v1.unique and v2.unique
    v1_where = str(v1.dialect_options["postgresql"]["where"])
    v2_where = str(v2.dialect_options["postgresql"]["where"])
    assert "auto_lot_series_key IS NULL" in v1_where
    assert "auto_lot_series_key IS NOT NULL" in v2_where
    assert [c.name for c in v2.columns] == ["auto_lot_series_key", "lot_running_no"]


# --- no data mutation -------------------------------------------------------

def test_migration_never_touches_existing_row_data() -> None:
    """The whole point: no lot is regenerated, renumbered or backfilled, and
    no row is added or removed — in EITHER direction."""
    for sql in (_upgrade(), _downgrade()):
        upper = sql.upper()
        assert "UPDATE PLOT_CYCLES" not in upper
        assert "DELETE" not in upper
        assert "INSERT" not in upper
        assert "TRUNCATE" not in upper
        assert "DROP TABLE" not in upper


def test_migration_never_rewrites_lot_columns() -> None:
    for sql in (_upgrade(), _downgrade()):
        assert "SET lot_no" not in sql
        assert "SET lot_running_no" not in sql
        assert "SET lot_no_source" not in sql


def test_migration_touches_only_plot_cycles() -> None:
    for sql in (_upgrade(), _downgrade()):
        for table in ("plots ", "records ", "suppliers ", "plot_access_phones",
                      "plot_access_credentials"):
            assert table not in sql, f"migration must not touch {table}"


# --- downgrade --------------------------------------------------------------

def test_downgrade_restores_the_original_index_and_check() -> None:
    down = _downgrade()
    # V2 index removed, V1 index restored to migration 0042's exact predicate.
    assert "DROP INDEX IF EXISTS uq_plot_cycles_auto_lot_series_running" in down
    assert "WHERE lot_no_source = 'auto';" in down
    # 0042's CHECK text, PO required again.
    assert "po_number IS NOT NULL AND lot_no IS NOT NULL AND lot_running_no IS NOT NULL" in down


def test_downgrade_drops_both_new_columns() -> None:
    down = _downgrade()
    assert "DROP COLUMN IF EXISTS auto_lot_series_key" in down
    assert "DROP COLUMN IF EXISTS supplier_lot_no" in down


def test_downgrade_drops_the_series_index_before_the_column() -> None:
    """Dropping a column an index depends on would fail; order matters."""
    down = _downgrade()
    assert down.index("DROP INDEX IF EXISTS uq_plot_cycles_auto_lot_series_running") < \
        down.index("DROP COLUMN IF EXISTS auto_lot_series_key")
