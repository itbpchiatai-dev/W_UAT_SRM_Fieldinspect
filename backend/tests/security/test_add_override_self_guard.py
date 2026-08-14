"""Round-4 HIGH-4 regression — add_override must reject self-id AND
reject permissions.* overrides from non-super_admin callers, with audit
log rows for both denial paths."""
from __future__ import annotations

import inspect

from app.api.v1 import users as users_module


def test_add_override_blocks_self_id() -> None:
    src = inspect.getsource(users_module.add_override)
    # The self-guard MUST run before the permission lookup (so a
    # denied attempt audits the attempt, not a 404).
    assert "target_user.id == user.id" in src, \
        "Round-4 HIGH-4 — add_override must reject self-id"
    assert "Cannot grant permission overrides on your own account" in src
    assert "user.override_self_blocked" in src, \
        "Round-4 HIGH-4 — self-denial must write a security audit row"


def test_add_override_blocks_non_super_admin_priv_management() -> None:
    src = inspect.getsource(users_module.add_override)
    assert "permissions." in src
    assert "SUPER_ADMIN_ROLE not in caller_role_names" in src
    assert "user.override_priv_escalation_blocked" in src
    assert "Only super_admin can grant permission overrides" in src


def test_add_override_audit_includes_attempted_key() -> None:
    """Denials must record WHICH permission key was attempted — without
    this metadata the SOC has no signal on what the attacker was after.
    """
    src = inspect.getsource(users_module.add_override)
    assert "attempted_permission_key" in src
    assert "attempted_granted" in src


def test_add_override_commits_audit_before_raising() -> None:
    """The audit row must survive the request rollback. The denied
    paths call db.commit() before HTTPException — verify the pattern is
    intact so a future refactor that drops the commit is caught here.
    """
    src = inspect.getsource(users_module.add_override)
    assert src.count("await db.commit()") >= 2, \
        "Round-4 HIGH-4 — both denial paths must commit the audit row"


# ── Round-5 HIGH-2 — expanded deny-list ─────────────────────────────

def test_add_override_deny_list_covers_all_privilege_management_keys() -> None:
    """Round-5 HIGH-2 — the deny-list must explicitly include every
    perm whose override is a privilege-escalation primitive. Round-4
    only blocked permissions.*; that left users.approve, users.delete,
    admin_settings.update, roles.* etc. as bypass paths."""
    src = inspect.getsource(users_module.add_override)
    required_keys = {
        # Approval-flow bypass — confederate could approve themselves.
        "users.approve",
        # Account-management escalation surfaces.
        "users.delete", "users.deactivate", "users.create",
        # Settings tampering — redirect notification recipients,
        # change CORS, flip SSO.
        "admin_settings.update", "admin_settings.read",
        # Role-management surfaces — craft super-admin-equivalent roles
        # or assign roles directly.
        "roles.create", "roles.update", "roles.delete", "roles.assign",
        # Destructive metadata changes.
        "menus.delete",
        # Direct privilege-management (original Round-4 coverage).
        "permissions.grant_override", "permissions.revoke_override",
    }
    for key in required_keys:
        assert key in src, \
            f"Round-5 HIGH-2 — add_override deny-list missing {key!r}"


def test_add_override_uses_explicit_deny_set() -> None:
    """The deny-list must be a literal set/frozenset in the function
    body (not constructed by string startswith() — which would miss
    keys like 'users.approve' that don\'t share a common prefix)."""
    src = inspect.getsource(users_module.add_override)
    assert "_PRIVILEGE_MANAGEMENT_KEYS" in src, \
        "Round-5 HIGH-2 — deny-list must be named _PRIVILEGE_MANAGEMENT_KEYS for explicit visibility"
    assert "in _PRIVILEGE_MANAGEMENT_KEYS" in src
