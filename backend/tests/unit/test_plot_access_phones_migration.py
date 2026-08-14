"""Round 8-3A — plot_access_phones table + records phone columns + RLS
(migration 0039). Source inspection (the local backend/alembic package shadows
the installed alembic, so the module can't be imported standalone — same
approach as the other migration tests)."""
from __future__ import annotations

import re
from pathlib import Path

_MIGRATION = (
    Path(__file__).resolve().parents[2]
    / "alembic" / "versions" / "2026_07_18_0000-0039_plot_access_phones.py"
)
_SRC = _MIGRATION.read_text(encoding="utf-8")


def _upgrade() -> str:
    return _SRC[_SRC.index("def upgrade"):_SRC.index("def downgrade")]


def _downgrade() -> str:
    return _SRC[_SRC.index("def downgrade"):]


def test_revision_chain() -> None:
    revision = re.search(r'^revision = "([^"]+)"', _SRC, re.MULTILINE).group(1)
    down = re.search(r'^down_revision = "([^"]+)"', _SRC, re.MULTILINE).group(1)
    assert revision == "0039_plot_access_phones"
    assert down == "0038_cycle_final_estimate"
    assert len(revision) <= 32  # alembic_version.version_num limit


def test_creates_table_with_columns() -> None:
    up = _upgrade()
    assert "CREATE TABLE plot_access_phones" in up
    for col in (
        "plot_id UUID NOT NULL",
        "phone_normalized VARCHAR(20) NOT NULL",
        "access_type VARCHAR(20) NOT NULL",
        "is_active BOOLEAN NOT NULL DEFAULT true",
        "created_at TIMESTAMPTZ NOT NULL",
        "updated_at TIMESTAMPTZ NOT NULL",
    ):
        assert col in up, f"missing column def: {col}"


def test_table_constraints() -> None:
    up = _upgrade()
    assert "ck_plot_access_phones_access_type_allowed" in up
    assert "access_type IN ('primary', 'additional')" in up
    # canonical Thai-mobile CHECK
    assert "ck_plot_access_phones_phone_normalized_format" in up
    assert "phone_normalized ~ '^0[689][0-9]{8}$'" in up
    # FK to plots with CASCADE
    assert "fk_plot_access_phones_plot_id_plots" in up
    assert "REFERENCES plots(id) ON DELETE CASCADE" in up


def test_indexes_including_partial_uniques() -> None:
    up = _upgrade()
    assert "ix_plot_access_phones_plot_id" in up
    # exact-match phone lookup for round 8-3B
    assert "ix_plot_access_phones_phone_normalized" in up
    # at most one active primary per plot
    assert "uq_plot_access_phones_active_primary_per_plot" in up
    assert "WHERE is_active = true AND access_type = 'primary'" in up
    # no duplicate active phone within a plot (per plot_id + phone)
    assert "uq_plot_access_phones_active_phone_per_plot" in up
    assert "ON plot_access_phones (plot_id, phone_normalized)" in up
    assert "WHERE is_active = true" in up


def test_adds_records_phone_columns_nullable_with_checks() -> None:
    up = _upgrade()
    for col in (
        "ADD COLUMN plot_access_phone_id UUID",
        "ADD COLUMN submitted_phone_snapshot VARCHAR(20)",
        "ADD COLUMN submitted_phone_type VARCHAR(20)",
        "ADD COLUMN inspector_type VARCHAR(20)",
    ):
        assert col in up, f"missing records column: {col}"
        assert col + " NOT NULL" not in up  # nullable (no backfill)
    # FK ON DELETE SET NULL — deactivating a phone never deletes record history
    assert "fk_records_plot_access_phone_id_plot_access_phones" in up
    assert "REFERENCES plot_access_phones(id) ON DELETE SET NULL" in up
    # CHECKs bind only non-NULL values
    assert "ck_records_submitted_phone_type_allowed" in up
    assert "submitted_phone_type IS NULL" in up
    assert "ck_records_inspector_type_allowed" in up
    assert "inspector_type IN ('farmer', 'supplier', 'extension')" in up
    assert "ix_records_plot_access_phone_id" in up


def test_rls_enabled_forced_and_granted() -> None:
    up = _upgrade()
    assert "ALTER TABLE plot_access_phones ENABLE ROW LEVEL SECURITY;" in up
    assert "ALTER TABLE plot_access_phones FORCE ROW LEVEL SECURITY;" in up
    assert "GRANT SELECT, INSERT, UPDATE, DELETE ON plot_access_phones TO srm_app;" in up


def test_rls_policy_scopes_through_parent_plot() -> None:
    up = _upgrade()
    assert "CREATE POLICY plot_access_phones_scope ON plot_access_phones" in up
    assert "TO srm_app" in up
    # existing GUC vocabulary (no new RLS vocabulary invented)
    assert "current_setting('app.scope', true)" in up
    assert "current_setting('app.supplier_id', true)" in up
    # resolved THROUGH the parent plot
    assert "SELECT id FROM plots" in up
    assert "supplier_id::text = current_setting('app.supplier_id', true)" in up
    assert "SELECT plot_id FROM plot_assignments" in up


def test_rls_uuid_cast_is_nullif_guarded() -> None:
    up = _upgrade()
    assert "NULLIF(current_setting('app.user_id', true), '')::uuid" in up
    # never a bare ::uuid straight off current_setting
    assert "current_setting('app.user_id', true)::uuid" not in up


def test_rls_never_wide_open() -> None:
    up = _upgrade()
    assert "USING (true)" not in up
    assert "WITH CHECK (true)" not in up
    # USING and WITH CHECK are consistent — both present (mirrors plots_scope)
    assert "USING (" in up and "WITH CHECK (" in up


def test_no_data_mutation_or_backfill() -> None:
    # Pure DDL — no backfill/reseed. Assert no data-mutation SQL (the word
    # "backfill" itself appears only in a comment explaining its absence).
    up = _upgrade()
    assert "INSERT INTO" not in up
    assert "UPDATE " not in up
    assert "DELETE FROM" not in up


def test_downgrade_drops_only_this_migrations_objects() -> None:
    down = _downgrade()
    assert "DROP POLICY IF EXISTS plot_access_phones_scope ON plot_access_phones;" in down
    assert "ALTER TABLE plot_access_phones DISABLE ROW LEVEL SECURITY;" in down
    assert "REVOKE ALL ON plot_access_phones FROM srm_app;" in down
    assert "ck_records_inspector_type_allowed" in down
    assert "ck_records_submitted_phone_type_allowed" in down
    assert "fk_records_plot_access_phone_id_plot_access_phones" in down
    assert "DROP COLUMN IF EXISTS inspector_type" in down
    assert "DROP COLUMN IF EXISTS submitted_phone_snapshot" in down
    assert "DROP COLUMN IF EXISTS plot_access_phone_id" in down
    assert "DROP TABLE IF EXISTS plot_access_phones;" in down
    # never touches other tables' RLS/policies
    assert "plots_scope" not in down
    assert "records_scope" not in down
    assert "plot_cycles" not in down
