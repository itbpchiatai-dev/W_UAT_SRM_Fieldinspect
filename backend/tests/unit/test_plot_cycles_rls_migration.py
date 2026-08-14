"""Round 7.2A — plot_cycles RLS (migration 0035). Source inspection (the
local backend/alembic package shadows the installed alembic, so the module
can't be imported standalone — same approach as the other migration tests)."""
from __future__ import annotations

import re
from pathlib import Path

_MIGRATION = (
    Path(__file__).resolve().parents[2]
    / "alembic" / "versions" / "2026_07_14_0000-0035_plot_cycles_rls.py"
)
_SRC = _MIGRATION.read_text(encoding="utf-8")


def _upgrade() -> str:
    return _SRC[_SRC.index("def upgrade"):_SRC.index("def downgrade")]


def _downgrade() -> str:
    return _SRC[_SRC.index("def downgrade"):]


def test_revision_chain() -> None:
    revision = re.search(r'^revision = "([^"]+)"', _SRC, re.MULTILINE).group(1)
    down = re.search(r'^down_revision = "([^"]+)"', _SRC, re.MULTILINE).group(1)
    assert revision == "0035_plot_cycles_rls"
    assert down == "0034_plot_cycles"
    assert len(revision) <= 32


def test_enables_and_forces_rls_on_plot_cycles() -> None:
    up = _upgrade()
    assert "ALTER TABLE plot_cycles ENABLE ROW LEVEL SECURITY;" in up
    assert "ALTER TABLE plot_cycles FORCE ROW LEVEL SECURITY;" in up
    assert "GRANT SELECT, INSERT, UPDATE, DELETE ON plot_cycles TO srm_app;" in up


def test_policy_uses_the_existing_scope_vocabulary_via_parent_plot() -> None:
    up = _upgrade()
    assert "CREATE POLICY plot_cycles_scope ON plot_cycles" in up
    assert "TO srm_app" in up
    # same GUC vocabulary as 0016 (no new RLS vocabulary invented)
    assert "current_setting('app.scope', true)" in up
    assert "current_setting('app.supplier_id', true)" in up
    assert "current_setting('app.user_id', true)" in up
    # resolved THROUGH the parent plot (plot_cycles has no supplier_id)
    assert "SELECT id FROM plots" in up
    assert "supplier_id::text = current_setting('app.supplier_id', true)" in up
    assert "SELECT plot_id FROM plot_assignments" in up


def test_no_wide_open_using_true() -> None:
    up = _upgrade()
    # scope='all' is the only true branch, inside the CASE — never a blanket
    # USING (true) / WITH CHECK (true) that would ignore scope entirely.
    assert "USING (true)" not in up
    assert "USING(true)" not in up
    assert "WITH CHECK (true)" not in up


def test_uuid_cast_guarded_against_empty_string() -> None:
    up = _upgrade()
    # NULLIF turns an empty app.user_id into NULL (no rows) instead of an
    # "invalid input syntax for uuid" InitPlan crash — see the migration's
    # docstring and app/api/deps/scope.py's _NO_USER_ID note.
    assert "NULLIF(current_setting('app.user_id', true), '')::uuid" in up
    # never a bare ::uuid straight off current_setting
    assert "current_setting('app.user_id', true)::uuid" not in up


def test_write_check_excludes_the_assigned_branch() -> None:
    """Defense-in-depth: a field officer (scope='assigned') can SEE cycles of
    their plots (USING) but must NOT create/modify them (WITH CHECK). So the
    plot_assignments subquery appears in USING only, never in WITH CHECK."""
    up = _upgrade()
    assert up.count("plot_assignments") == 1
    check = up[up.index("WITH CHECK"):]
    assert "plot_assignments" not in check


def test_downgrade_drops_policy_and_rls() -> None:
    down = _downgrade()
    assert "DROP POLICY IF EXISTS plot_cycles_scope ON plot_cycles;" in down
    assert "ALTER TABLE plot_cycles DISABLE ROW LEVEL SECURITY;" in down
    assert "REVOKE ALL ON plot_cycles FROM srm_app;" in down
