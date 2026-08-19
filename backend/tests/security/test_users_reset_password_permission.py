"""users.reset_password permission wiring (round 8-23A).

Setting another account's password is account takeover, so the capability
gets its own permission key rather than riding on users.update. These
tests lock in that separation, the conservative default grant
(internal:super_admin only), and the fact that it cannot be handed out
through a per-user override by a non-super-admin.

Source-inspection + catalog assertions, same style as
tests/security/test_users_approve_permission.py and
test_patch_user_self_guard.py — no DB, no HTTP.
"""
from __future__ import annotations

import inspect

from app.api.v1 import users as users_module
from app.auth.permissions import PermissionKey
from app.seed import DEFAULT_PERMISSIONS, DEFAULT_ROLES

_KEY = "users.reset_password"


def test_permission_key_constant_exists() -> None:
    assert PermissionKey.USERS_RESET_PASSWORD == _KEY


def test_key_is_in_the_seed_catalog() -> None:
    keys = {k for k, _display, _cat, _menu in DEFAULT_PERMISSIONS}
    assert _KEY in keys, "adding the constant without seeding makes it deny-always"


def test_key_is_categorised_under_users_and_is_not_a_menu() -> None:
    row = next(r for r in DEFAULT_PERMISSIONS if r[0] == _KEY)
    _key, display_name, category, is_menu = row
    assert category == "users"
    assert is_menu is False
    assert display_name.strip(), "needs a human-readable Thai label for the Roles UI"


def test_endpoint_is_gated_by_the_new_key_not_users_update() -> None:
    src = inspect.getsource(users_module.reset_user_password)
    # The dependency list is on the decorator, which getsource includes.
    assert "USERS_RESET_PASSWORD" in src
    assert "USERS_UPDATE" not in src


def test_route_declares_the_permission_dependency() -> None:
    route = next(
        r for r in users_module.router.routes
        if getattr(r, "path", None) == "/{user_id}/reset-password"
        and "POST" in getattr(r, "methods", set())
    )
    keys: set[str] = set()
    for dep in route.dependencies:
        closure = getattr(dep.dependency, "__closure__", None) or ()
        for cell in closure:
            if isinstance(cell.cell_contents, str) and "." in cell.cell_contents:
                keys.add(cell.cell_contents)
    assert keys == {_KEY}, "exactly one permission gates this route"


# --- conservative default grant ----------------------------------------

def test_no_default_role_lists_the_key_explicitly() -> None:
    """internal:super_admin is the `keys=None` role and binds the whole
    catalog, so it picks this up automatically. NO other role may list it
    — that would broaden a takeover capability by default."""
    for name, _display, _scope, keys in DEFAULT_ROLES:
        if keys is None:
            continue
        assert _KEY not in keys, f"role {name} must not be granted {_KEY} by default"


def test_super_admin_is_the_all_permissions_role() -> None:
    """Guards the assumption the test above relies on: if super_admin ever
    stops being `keys=None`, this key would silently be granted to nobody
    and the reset endpoint would be dead."""
    super_admin = next(r for r in DEFAULT_ROLES if r[0] == "internal:super_admin")
    assert super_admin[3] is None


def test_internal_admin_has_users_update_but_not_reset_password() -> None:
    """The exact escalation this split prevents: internal:admin can edit
    users, and must NOT thereby be able to take over their accounts."""
    admin = next(r for r in DEFAULT_ROLES if r[0] == "internal:admin")
    keys = admin[3] or []
    assert "users.update" in keys
    assert _KEY not in keys


# --- not grantable via per-user override -------------------------------

def test_key_is_in_the_privilege_management_deny_list() -> None:
    """Round-5 HIGH-2 pattern: a holder of permissions.grant_override who
    is not a super_admin must not be able to grant this to a confederate."""
    src = inspect.getsource(users_module.add_override)
    assert _KEY in src, (
        "users.reset_password must be listed in _PRIVILEGE_MANAGEMENT_KEYS"
    )


def test_deny_list_still_covers_the_previously_guarded_keys() -> None:
    """Regression guard — adding our key must not have displaced any."""
    src = inspect.getsource(users_module.add_override)
    for key in (
        "users.approve", "users.delete", "users.deactivate", "users.create",
        "admin_settings.update", "roles.assign", "menus.delete",
    ):
        assert key in src
