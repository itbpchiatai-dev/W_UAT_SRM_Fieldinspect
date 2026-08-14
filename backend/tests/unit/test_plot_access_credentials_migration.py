"""Round 8-9A — plot_access_credentials table + RLS (migration 0046). Source
inspection (the local backend/alembic package shadows the installed alembic, so
the module can't be imported standalone — same approach as the other migration
tests), plus a metadata cross-check against the ORM model."""
from __future__ import annotations

import re
from pathlib import Path

from app.db.models.plot_access_credential import PlotAccessCredential

_MIGRATION = (
    Path(__file__).resolve().parents[2]
    / "alembic" / "versions" / "2026_07_31_0000-0046_plot_access_credentials.py"
)
_SRC = _MIGRATION.read_text(encoding="utf-8")


def _upgrade() -> str:
    return _SRC[_SRC.index("def upgrade"):_SRC.index("def downgrade")]


def _downgrade() -> str:
    return _SRC[_SRC.index("def downgrade"):]


def _downgrade_sql() -> str:
    """downgrade() with its `#` comment lines stripped — the "never touches
    other tables" assertions must read the SQL, not the prose explaining it."""
    return "\n".join(
        line for line in _downgrade().splitlines()
        if not line.strip().startswith("#")
    )


def test_revision_chain_from_actual_head() -> None:
    revision = re.search(r'^revision = "([^"]+)"', _SRC, re.MULTILINE).group(1)
    down = re.search(r'^down_revision = "([^"]+)"', _SRC, re.MULTILINE).group(1)
    assert revision == "0046_plot_access_credentials"
    # The actual head at the time of this round — never a guessed revision.
    assert down == "0045_relax_yield_pct_cap"


def test_revision_id_within_alembic_version_limit() -> None:
    revision = re.search(r'^revision = "([^"]+)"', _SRC, re.MULTILINE).group(1)
    assert len(revision) <= 32  # alembic_version.version_num limit


def test_creates_table_with_columns() -> None:
    up = _upgrade()
    assert "CREATE TABLE plot_access_credentials" in up
    for col in (
        "id UUID PRIMARY KEY",
        "plot_id UUID NOT NULL",
        "password_hash VARCHAR(255) NOT NULL",
        "password_lookup_digest CHAR(64) NOT NULL",
        "credential_version INTEGER NOT NULL DEFAULT 1",
        "is_active BOOLEAN NOT NULL DEFAULT true",
        "updated_by_id UUID",
        "created_at TIMESTAMPTZ NOT NULL",
        "updated_at TIMESTAMPTZ NOT NULL",
    ):
        assert col in up, f"missing column def: {col}"


def test_table_constraints_and_indexes() -> None:
    up = _upgrade()
    # one credential per plot
    assert "CONSTRAINT uq_plot_access_credentials_plot_id UNIQUE (plot_id)" in up
    # FKs
    assert "fk_plot_access_credentials_plot_id_plots" in up
    assert "REFERENCES plots(id) ON DELETE CASCADE" in up
    assert "fk_plot_access_credentials_updated_by_id_users" in up
    assert "REFERENCES users(id) ON DELETE SET NULL" in up
    # CHECKs
    assert "ck_plot_access_credentials_credential_version_positive" in up
    assert "CHECK (credential_version >= 1)" in up
    assert "ck_plot_access_credentials_password_lookup_digest_format" in up
    assert "password_lookup_digest ~ '^[0-9a-f]{64}$'" in up
    # blind-index lookup index
    assert "ix_plot_access_credentials_password_lookup_digest" in up


def test_digest_and_hash_are_not_unique_several_plots_may_share_a_password() -> None:
    """Locked business rule: several plots MAY deliberately use the same
    password, so nothing may be unique on the digest or the hash — only on
    plot_id."""
    up = _upgrade()
    assert "UNIQUE (password_lookup_digest)" not in up
    assert "UNIQUE (password_hash)" not in up
    assert "CREATE UNIQUE INDEX" not in up
    # the one legitimate uniqueness is per-plot
    assert up.count("UNIQUE") == 1
    assert "UNIQUE (plot_id)" in up


def test_no_plaintext_password_column() -> None:
    up = _upgrade()
    assert "password_plain" not in up
    assert "password VARCHAR" not in up
    assert "password TEXT" not in up


def test_rls_enabled_forced_and_granted() -> None:
    up = _upgrade()
    assert "ALTER TABLE plot_access_credentials ENABLE ROW LEVEL SECURITY;" in up
    assert "ALTER TABLE plot_access_credentials FORCE ROW LEVEL SECURITY;" in up
    assert (
        "GRANT SELECT, INSERT, UPDATE, DELETE ON plot_access_credentials TO srm_app;"
        in up
    )


def test_rls_policy_mirrors_plot_scope() -> None:
    up = _upgrade()
    assert "CREATE POLICY plot_access_credentials_scope ON plot_access_credentials" in up
    assert "TO srm_app" in up
    # existing GUC vocabulary (no new RLS vocabulary invented)
    assert "current_setting('app.scope', true)" in up
    assert "current_setting('app.supplier_id', true)" in up
    # resolved THROUGH the parent plot, exactly like plot_access_phones
    assert "SELECT id FROM plots" in up
    assert "supplier_id::text = current_setting('app.supplier_id', true)" in up
    assert "SELECT plot_id FROM plot_assignments" in up
    # NULLIF guard, never a bare ::uuid straight off current_setting
    assert "NULLIF(current_setting('app.user_id', true), '')::uuid" in up
    assert "current_setting('app.user_id', true)::uuid" not in up


def test_rls_using_and_with_check_are_consistent_and_never_wide_open() -> None:
    # Slice the POLICY statement itself, not the explanatory comment above it.
    up = _upgrade()[_upgrade().index("CREATE POLICY plot_access_credentials_scope"):]
    assert "USING (true)" not in up
    assert "WITH CHECK (true)" not in up
    using = up[up.index("USING ("):up.index("WITH CHECK (")]
    with_check = up[up.index("WITH CHECK ("):]
    for branch in (
        "WHEN 'all'      THEN true",
        "WHEN 'supplier' THEN plot_id IN (",
        "WHEN 'assigned' THEN plot_id IN (",
        "ELSE false",
    ):
        assert branch in using, f"USING missing branch: {branch}"
        assert branch in with_check, f"WITH CHECK missing branch: {branch}"


def test_no_data_mutation_or_backfill() -> None:
    """Pure DDL — the new table starts at 0 rows and nothing else is touched."""
    up = _upgrade()
    assert "INSERT INTO" not in up
    assert "UPDATE " not in up
    assert "DELETE FROM" not in up


def test_downgrade_drops_only_this_migrations_objects() -> None:
    down = _downgrade()
    assert (
        "DROP POLICY IF EXISTS plot_access_credentials_scope ON plot_access_credentials;"
        in down
    )
    assert "ALTER TABLE plot_access_credentials DISABLE ROW LEVEL SECURITY;" in down
    assert "REVOKE ALL ON plot_access_credentials FROM srm_app;" in down
    assert "DROP INDEX IF EXISTS ix_plot_access_credentials_password_lookup_digest;" in down
    assert "DROP TABLE IF EXISTS plot_access_credentials;" in down
    # never touches any other table's data, policies or columns
    sql = _downgrade_sql()
    for other in (
        "plots_scope", "records_scope", "plot_cycles", "plot_access_phones",
        "DROP TABLE IF EXISTS plots", "ALTER TABLE records", "ALTER TABLE plots",
        "DELETE FROM", "UPDATE ", "INSERT INTO",
    ):
        assert other not in sql, f"downgrade must not touch: {other}"


def test_model_metadata_matches_migration() -> None:
    table = PlotAccessCredential.__table__
    assert table.name == "plot_access_credentials"
    assert sorted(c.name for c in table.columns) == [
        "created_at", "credential_version", "id", "is_active", "password_hash",
        "password_lookup_digest", "plot_id", "updated_at", "updated_by_id",
    ]
    assert table.c.plot_id.unique is True
    assert table.c.plot_id.nullable is False
    assert table.c.password_hash.nullable is False
    assert table.c.password_lookup_digest.nullable is False
    assert table.c.updated_by_id.nullable is True
    # names resolve through the ORM naming convention to the migration's names
    names = {c.name for c in table.constraints if c.name}
    assert "ck_plot_access_credentials_credential_version_positive" in names
    assert "ck_plot_access_credentials_password_lookup_digest_format" in names


def test_plot_read_model_does_not_eager_load_the_credential() -> None:
    """The credential/hash must never ride along on a Plot list/read model —
    there is deliberately no Plot relationship to this table at all."""
    from app.db.models.plot import Plot

    assert not any(
        "credential" in rel.key for rel in Plot.__mapper__.relationships
    ), "Plot must not expose a credential relationship"
