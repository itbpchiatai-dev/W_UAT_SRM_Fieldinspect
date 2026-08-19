"""Migration 0051 — users.auth_version + users.reset_password (round 8-23A).

Source inspection: backend/alembic/ (the local versions package) shadows
the installed `alembic` library on sys.path when pytest runs from
backend/, so a migration module cannot be imported standalone. Same
approach as test_rls_migration_0016_password_security.py and
test_rls_uuid_guard_migration.py.
"""
from __future__ import annotations

import re
from pathlib import Path

_MIGRATION = (
    Path(__file__).resolve().parents[2]
    / "alembic" / "versions" / "2026_08_17_0000-0051_user_auth_version.py"
)
_SRC = _MIGRATION.read_text(encoding="utf-8")


def _upgrade() -> str:
    return _SRC[_SRC.index("def upgrade"):_SRC.index("def downgrade")]


def _downgrade() -> str:
    return _SRC[_SRC.index("def downgrade"):]


def test_revision_chain_targets_the_verified_head() -> None:
    revision = re.search(r'^revision = "([^"]+)"', _SRC, re.MULTILINE).group(1)
    down = re.search(r'^down_revision = "([^"]+)"', _SRC, re.MULTILINE).group(1)
    assert revision == "0051_user_auth_version"
    # Preflight confirmed this was the single head, in code AND in the DB.
    assert down == "0050_plot_cycle_oracle_refs"
    assert len(revision) <= 32, "alembic_version.version_num is VARCHAR(32)"


def test_adds_auth_version_as_not_null_with_a_zero_default() -> None:
    up = _upgrade()
    assert '"users"' in up and "auth_version" in up
    assert "nullable=False" in up
    # server_default backfills existing rows without a separate UPDATE.
    assert 'server_default=sa.text("0")' in up
    assert "sa.Integer()" in up


def test_upgrade_is_additive_only() -> None:
    """No column drop, no table drop, no UPDATE/DELETE of user data."""
    up = _upgrade().upper()
    for forbidden in ("DROP COLUMN", "DROP TABLE", "TRUNCATE", "UPDATE USERS", "DELETE FROM USERS"):
        assert forbidden not in up, f"upgrade must not {forbidden}"


def test_permission_insert_is_idempotent() -> None:
    up = _upgrade()
    assert "INSERT INTO permissions" in up
    assert "ON CONFLICT (key) DO NOTHING" in up
    assert "users.reset_password" in up


def test_permission_insert_matches_the_real_table_shape() -> None:
    """`permissions` has no created_at/updated_at (migration 0005) —
    naming them would make the INSERT fail at runtime."""
    up = _upgrade()
    insert = up[up.index("INSERT INTO permissions"):]
    insert = insert[:insert.index("ON CONFLICT")]
    assert "created_at" not in insert
    assert "updated_at" not in insert
    for column in ("id", "key", "display_name", "category", "is_menu"):
        assert column in insert


def test_role_mapping_grants_super_admin_only_and_is_idempotent() -> None:
    up = _upgrade()
    assert "INSERT INTO role_permissions" in up
    assert "internal:super_admin" in up
    assert "ON CONFLICT (role_id, permission_id) DO NOTHING" in up
    # No other role may be named in the mapping.
    mapping = up[up.index("INSERT INTO role_permissions"):]
    for role in ("internal:admin", "farmlog:supervisor", "supplier:owner",
                 "external:admin", "internal:user"):
        assert role not in mapping


def test_downgrade_reverses_all_three_changes() -> None:
    down = _downgrade()
    assert "DELETE FROM role_permissions" in down
    assert "DELETE FROM permissions WHERE key = 'users.reset_password'" in down
    assert 'op.drop_column("users", "auth_version")' in down


def test_downgrade_never_touches_passwords_or_other_user_columns() -> None:
    """The only `password` token allowed in the downgrade is the
    permission KEY 'users.reset_password'. Nothing may read, write, or
    drop users.password_hash — a rollback must never disturb credentials."""
    down = _downgrade()
    residue = down.replace("users.reset_password", "")
    assert "password" not in residue.lower()
    assert "DELETE FROM users" not in down.upper()
    assert "DROP TABLE" not in down.upper()
    # auth_version is the ONLY column dropped.
    assert down.upper().count("DROP_COLUMN") + down.upper().count("DROP COLUMN") == 1


def test_migration_contains_no_hardcoded_secret_or_password_value() -> None:
    """Migration 0016 shipped a hardcoded DB role password once (fixed in
    the Git UAT round) — this asserts 0051 never repeats that."""
    lowered = _SRC.lower()
    assert "password_hash" not in lowered
    assert "bcrypt" not in lowered
    assert "$2b$" not in _SRC
    assert "os.environ" not in lowered, "0051 needs no runtime secret at all"
