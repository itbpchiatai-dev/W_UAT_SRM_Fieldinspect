"""Round B — plots Auto Plot Code bookkeeping (migration 0053). Source
inspection (the local backend/alembic package shadows the installed alembic, so
the module can't be imported standalone — same approach as the other migration
tests)."""
from __future__ import annotations

import re
from pathlib import Path

_MIGRATION = (
    Path(__file__).resolve().parents[2]
    / "alembic" / "versions" / "2026_09_04_0000-0053_plots_auto_plot_code.py"
)
_SRC = _MIGRATION.read_text(encoding="utf-8")


def _upgrade() -> str:
    return _SRC[_SRC.index("def upgrade"):_SRC.index("def downgrade")]


def _downgrade() -> str:
    return _SRC[_SRC.index("def downgrade"):]


def test_revision_chain() -> None:
    revision = re.search(r'^revision = "([^"]+)"', _SRC, re.MULTILINE).group(1)
    down = re.search(r'^down_revision = "([^"]+)"', _SRC, re.MULTILINE).group(1)
    assert revision == "0053_plots_auto_plot_code"
    assert down == "0052_suppliers_menu_update"
    assert len(revision) <= 32  # alembic_version.version_num limit


def test_adds_three_nullable_columns_to_plots() -> None:
    up = _upgrade()
    assert "ALTER TABLE plots ADD COLUMN plot_code_source VARCHAR(20)" in up
    assert "ALTER TABLE plots ADD COLUMN plot_code_series_key VARCHAR(255)" in up
    assert "ALTER TABLE plots ADD COLUMN plot_code_running_no INTEGER" in up
    # All nullable: NULL is what every existing plot reads, meaning "this code
    # predates the generator".
    assert "VARCHAR(20) NOT NULL" not in up
    assert "VARCHAR(255) NOT NULL" not in up
    assert "INTEGER NOT NULL" not in up
    assert "SET NOT NULL" not in up


def test_touches_no_data_at_all() -> None:
    """The 43 existing plots keep their hand-entered codes untouched: a plot
    code is printed on field signage AND is the object-storage folder for that
    plot's photos, so renaming one would orphan real data."""
    up = _upgrade()
    assert "UPDATE " not in up
    assert "INSERT INTO" not in up
    assert "DELETE " not in up
    # and it changes nothing about the plot_code column itself
    assert "ALTER COLUMN plot_code " not in up
    assert "DROP COLUMN plot_code;" not in up


def test_touches_only_the_plots_table() -> None:
    up = _upgrade()
    tables = set(re.findall(r"ALTER TABLE (\w+)", up))
    assert tables == {"plots"}
    # no RLS/grant/role/ownership changes
    for forbidden in ("POLICY", "GRANT", "REVOKE", "OWNER TO", "ROW LEVEL SECURITY"):
        assert forbidden not in up


def test_adds_the_three_check_constraints() -> None:
    up = _upgrade()
    assert "ck_plots_plot_code_source_allowed" in up
    assert "plot_code_source IN ('auto', 'manual', 'legacy')" in up
    # NULL-safe: an existing row (NULL source) must satisfy every CHECK.
    assert "plot_code_source IS NULL" in up

    assert "ck_plots_auto_plot_code_requires_fields" in up
    assert "plot_code_source IS DISTINCT FROM 'auto'" in up
    assert "plot_code_series_key IS NOT NULL" in up
    assert "plot_code_running_no IS NOT NULL" in up

    assert "ck_plots_plot_code_running_no_positive" in up
    assert "plot_code_running_no IS NULL OR plot_code_running_no >= 1" in up


def test_adds_the_partial_unique_index_scoped_to_auto_rows() -> None:
    """THE concurrency backstop. Two imports creating plots for one supplier in
    one month can both read the same "max running" before either inserts, and
    nothing locks a supplier row on a plot insert — so the DB is what refuses
    the duplicate. Scoped to 'auto' so legacy/manual rows never participate."""
    up = _upgrade()
    assert "CREATE UNIQUE INDEX uq_plots_auto_code_series_running" in up
    assert "ON plots (plot_code_series_key, plot_code_running_no)" in up
    assert "WHERE plot_code_source = 'auto'" in up


def test_downgrade_drops_exactly_what_upgrade_added() -> None:
    down = _downgrade()
    for name in (
        "uq_plots_auto_code_series_running",
        "ck_plots_plot_code_running_no_positive",
        "ck_plots_auto_plot_code_requires_fields",
        "ck_plots_plot_code_source_allowed",
        "plot_code_running_no",
        "plot_code_series_key",
        "plot_code_source",
    ):
        assert name in down
    # IF EXISTS everywhere so a partial upgrade can still be rolled back
    assert down.count("IF EXISTS") >= 7
    # and the downgrade must not touch data either
    assert "UPDATE " not in down
    assert "DELETE " not in down


def test_model_metadata_matches_the_migration() -> None:
    """The ORM's __table_args__ and this migration must agree on every name, or
    a future autogenerate would propose spurious drops/creates."""
    from app.db.models.plot import Plot

    names = {c.name for c in Plot.__table__.constraints if c.name} | {
        i.name for i in Plot.__table__.indexes
    }
    assert "uq_plots_auto_code_series_running" in names
    assert "ck_plots_plot_code_source_allowed" in names
    assert "ck_plots_auto_plot_code_requires_fields" in names
    assert "ck_plots_plot_code_running_no_positive" in names

    columns = {c.name for c in Plot.__table__.columns}
    assert {"plot_code_source", "plot_code_series_key", "plot_code_running_no"} <= columns
