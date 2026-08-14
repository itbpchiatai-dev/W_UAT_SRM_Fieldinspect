"""HIGH-2 regression — verify the seed catalog actually contains the new
`users.approve` permission, that internal:admin gets it by default, and
that internal:user does NOT (only super_admin + admin can approve)."""
from __future__ import annotations

from app.seed import DEFAULT_PERMISSIONS, DEFAULT_ROLES


def test_users_approve_in_default_permissions() -> None:
    keys = {p[0] for p in DEFAULT_PERMISSIONS}
    assert "users.approve" in keys, \
        "Deep-Audit HIGH-2 — users.approve must seed into the catalog"


def test_internal_admin_has_users_approve() -> None:
    admin = next(r for r in DEFAULT_ROLES if r[0] == "internal:admin")
    perms = admin[3] or []
    assert "users.approve" in perms


def test_internal_user_does_not_have_users_approve() -> None:
    user = next(r for r in DEFAULT_ROLES if r[0] == "internal:user")
    perms = user[3] or []
    assert "users.approve" not in perms


def test_external_admin_has_users_approve() -> None:
    ext = next(r for r in DEFAULT_ROLES if r[0] == "external:admin")
    perms = ext[3] or []
    assert "users.approve" in perms
