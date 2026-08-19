"""users.auth_version + users.reset_password permission (round 8-23A).

Backend foundation for admin-initiated password reset of LOCAL accounts.

Three additive changes, no data mutation:

  1. users.auth_version INTEGER NOT NULL DEFAULT 0
     A monotonic session generation counter. Every access/refresh token
     minted from now on carries the value that was live at mint time
     (claim `auth_version`); get_current_user and /refresh compare the
     claim against the live row and reject on ANY mismatch (fail-closed).
     Bumping it therefore invalidates every outstanding token for that
     user at once — something the existing per-jti `revoked_tokens`
     blocklist cannot do, because jtis are never recorded at mint time.

     Existing rows backfill to 0, and a token with NO auth_version claim
     is read as 0, so every session already in flight keeps working
     across this upgrade. Rollout is seamless in both directions.

     An INTEGER counter (not a timestamp) is deliberate: a
     "token issued before password_changed_at" comparison is unreliable
     at second resolution — a token minted in the same second as the
     reset could pass. An exact-equality integer check has no such window.

  2. permissions row 'users.reset_password'
     A SEPARATE key from users.update on purpose: holding users.update
     (which internal:admin has by default) must NOT confer the ability
     to take over another account by setting its password.

  3. role_permissions mapping for internal:super_admin ONLY
     Conservative by design — no other role is broadened here. Matches
     app/seed.py, where internal:super_admin is the `keys=None` role
     (binds the whole catalog) and no other DEFAULT_ROLES entry lists
     this key.

Both inserts are idempotent (ON CONFLICT DO NOTHING against
permissions.key's UNIQUE and role_permissions' composite PK), so this
migration is safe to re-run and safe on a DB where `python -m app.seed`
has already created the same rows.

Revision ID: 0051_user_auth_version
Revises: 0050_plot_cycle_oracle_refs
Create Date: 2026-08-17 00:00:00

(Revision id kept <= 32 chars for alembic_version.version_num.)
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0051_user_auth_version"
down_revision = "0050_plot_cycle_oracle_refs"
branch_labels = None
depends_on = None

_PERMISSION_KEY = "users.reset_password"
_SUPER_ADMIN_ROLE = "internal:super_admin"


def upgrade() -> None:
    # 1. Session-generation counter. server_default=0 backfills every
    #    existing row without a separate UPDATE, and keeps future INSERTs
    #    (seed, JIT SSO provisioning) valid without naming the column.
    op.add_column(
        "users",
        sa.Column("auth_version", sa.Integer(), nullable=False, server_default=sa.text("0")),
    )

    # 2. Permission catalog row. `permissions` has no created_at/updated_at
    #    columns (see migration 0005) — do not add them here.
    op.execute(
        """
        INSERT INTO permissions (id, key, display_name, category, is_menu)
        VALUES (gen_random_uuid(), 'users.reset_password',
                'ตั้งรหัสผ่านใหม่ให้ผู้ใช้', 'users', FALSE)
        ON CONFLICT (key) DO NOTHING
        """
    )

    # 3. Grant to internal:super_admin only. The SELECT yields zero rows
    #    (and inserts nothing) if either the role or the permission is
    #    missing, so this never fails on a partially-seeded database.
    op.execute(
        """
        INSERT INTO role_permissions (role_id, permission_id)
        SELECT r.id, p.id
        FROM roles r
        CROSS JOIN permissions p
        WHERE r.name = 'internal:super_admin'
          AND p.key = 'users.reset_password'
        ON CONFLICT (role_id, permission_id) DO NOTHING
        """
    )


def downgrade() -> None:
    # role_permissions / user_permission_overrides both FK the permission
    # with ON DELETE CASCADE (migration 0005), so deleting the permission
    # row is sufficient. The explicit role_permissions delete first is
    # defensive and harmless.
    op.execute(
        """
        DELETE FROM role_permissions
        WHERE permission_id IN (SELECT id FROM permissions WHERE key = 'users.reset_password')
        """
    )
    op.execute("DELETE FROM permissions WHERE key = 'users.reset_password'")
    op.drop_column("users", "auth_version")
