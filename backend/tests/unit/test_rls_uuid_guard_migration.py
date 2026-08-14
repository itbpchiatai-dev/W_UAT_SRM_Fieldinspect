"""Round 8-1 — RLS uuid-cast hardening (migration 0037). Source inspection
(the local backend/alembic package shadows the installed alembic, so the
module can't be imported standalone — same approach as
test_plot_cycles_rls_migration.py)."""
from __future__ import annotations

import re
from pathlib import Path

_MIGRATION = (
    Path(__file__).resolve().parents[2]
    / "alembic" / "versions" / "2026_07_16_0000-0037_rls_uuid_guard.py"
)
_SRC = _MIGRATION.read_text(encoding="utf-8")

_GUARDED_CAST = "NULLIF(current_setting('app.user_id', true), '')::uuid"
_BARE_CAST = "current_setting('app.user_id', true)::uuid"


def _upgrade() -> str:
    return _SRC[_SRC.index("def upgrade"):_SRC.index("def downgrade")]


def _downgrade() -> str:
    return _SRC[_SRC.index("def downgrade"):]


def test_revision_chain() -> None:
    revision = re.search(r'^revision = "([^"]+)"', _SRC, re.MULTILINE).group(1)
    down = re.search(r'^down_revision = "([^"]+)"', _SRC, re.MULTILINE).group(1)
    assert revision == "0037_rls_uuid_guard"
    assert down == "0036_plot_cycle_label"
    assert len(revision) <= 32


def test_upgrade_only_touches_records_scope_and_plots_scope() -> None:
    up = _upgrade()
    assert "ALTER POLICY records_scope ON records" in up
    assert "ALTER POLICY plots_scope ON plots" in up
    # No other policy/table is touched by this migration.
    assert "plot_cycles" not in up
    assert "CREATE POLICY" not in up
    assert "DROP POLICY" not in up


def test_upgrade_has_all_four_guarded_casts() -> None:
    """records USING assigned, records WITH CHECK assigned, plots USING
    assigned, plots WITH CHECK assigned — exactly 4 guarded casts."""
    up = _upgrade()
    assert up.count(_GUARDED_CAST) == 4


def test_upgrade_has_no_bare_uuid_cast() -> None:
    up = _upgrade()
    # The bare (unguarded) substring must not appear at all — the guarded
    # form (NULLIF(...) wrapping the same current_setting(...)::uuid) is a
    # textually distinct string, so this is a true zero, not an artifact of
    # substring overlap with the guarded form.
    assert _BARE_CAST not in up
    assert up.count(_BARE_CAST) == 0


def test_upgrade_preserves_all_supplier_assigned_else_branches() -> None:
    up = _upgrade()
    for policy_block_start in ("ALTER POLICY records_scope", "ALTER POLICY plots_scope"):
        idx = up.index(policy_block_start)
        # Slice up to the next ALTER POLICY (or end of upgrade()) so each
        # policy's own USING+WITH CHECK pair is checked independently.
        rest = up[idx:]
        next_alter = rest.find("ALTER POLICY", 1)
        block = rest if next_alter == -1 else rest[:next_alter]
        assert block.count("WHEN 'all'") == 2       # USING + WITH CHECK
        assert block.count("WHEN 'supplier'") == 2
        assert block.count("WHEN 'assigned'") == 2
        assert block.count("ELSE false") == 2


def test_upgrade_no_wide_open_using_true() -> None:
    up = _upgrade()
    assert "USING (true)" not in up
    assert "USING(true)" not in up
    assert "WITH CHECK (true)" not in up


def test_upgrade_never_disables_rls_or_touches_grants_or_roles() -> None:
    up = _upgrade()
    assert "DISABLE ROW LEVEL SECURITY" not in up
    assert "GRANT" not in up
    assert "REVOKE" not in up
    assert "CREATE ROLE" not in up
    assert "DROP ROLE" not in up
    assert "ALTER ROLE" not in up


def test_upgrade_never_mutates_application_data() -> None:
    up = _upgrade().upper()
    assert "INSERT INTO" not in up
    assert "UPDATE " not in up
    assert "DELETE FROM" not in up


def test_downgrade_restores_bare_cast_four_times() -> None:
    down = _downgrade()
    assert down.count(_BARE_CAST) == 4
    # The downgrade must not itself contain the guarded form (that would mean
    # it didn't actually restore 0016's original expression).
    assert _GUARDED_CAST not in down


def test_downgrade_only_touches_records_scope_and_plots_scope() -> None:
    down = _downgrade()
    assert "ALTER POLICY records_scope ON records" in down
    assert "ALTER POLICY plots_scope ON plots" in down
    assert "plot_cycles" not in down
    assert "DROP POLICY" not in down
    assert "DISABLE ROW LEVEL SECURITY" not in down


def test_no_secrets_or_env_reads_in_migration() -> None:
    # Unlike 0016 (which creates the srm_app role and reads DB_APP_PASSWORD),
    # this migration only ALTERs existing policies — no os.environ, no
    # password, no new role.
    assert "os.environ" not in _SRC
    assert "PASSWORD" not in _SRC
    assert "import os" not in _SRC
