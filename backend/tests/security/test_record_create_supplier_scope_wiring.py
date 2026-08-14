"""Supplier Owner may create inspection records for their OWN supplier —
role-catalog + endpoint/scope wiring checks (round 8-4F).

Confirms:
- The seed grants supplier:owner records.read + records.create on top of its
  existing reads/plot writes — NOT records.update/delete, and NOT
  plots.delete/plots.assign.
- supplier:staff stays read-only (records.read, NO records.create).
- internal:admin and farmlog:field_officer keep records.create (no regression).
- POST /api/v1/records and /records/with-photos still require records.create
  AND set the RLS context (granting the permission must not have loosened the
  endpoint or dropped its scope wiring).
- _resolve_scope still limits the DATA: supplier:owner WITH supplier_id →
  scope 'supplier' (own rows only); WITHOUT supplier_id → 'none' (fail-closed);
  supplier:staff is NOT broadened to 'supplier'.

No DB fixture exists in this repo, so — matching the established pattern
(tests/security/test_plot_create_supplier_scope_wiring.py) — this is a
source/route-table + pure-function inspection, not a live HTTP request.

The create-time guards (foreign plot → generic 404, inactive plot → 404, no
active cycle → 409, supplier_id derived from the plot, crop/variety/planting
snapshot from the active cycle, plot_cycle_id server-bound) are already
covered by tests/unit/test_record_create_endpoint.py and are unchanged by
this round — this file only proves the authorization surface.
"""
from __future__ import annotations

import inspect
from types import SimpleNamespace
from uuid import uuid4

from app.api.deps.scope import _resolve_scope
from app.api.v1 import records as records_module
from app.auth.permissions import PermissionKey
from app.seed import DEFAULT_ROLES


def _role_keys(name: str) -> list[str]:
    return next(keys for role_name, _, _, keys in DEFAULT_ROLES if role_name == name)


def _user(*, roles, supplier_id=None, is_supplier_admin=False):
    return SimpleNamespace(
        id=uuid4(),
        supplier_id=supplier_id,
        is_supplier_admin=is_supplier_admin,
        roles=[SimpleNamespace(name=r) for r in roles],
    )


def _post_route(path: str):
    return next(
        r for r in records_module.router.routes
        if r.path == path and "POST" in r.methods
    )


# --- role catalog ------------------------------------------------------------

def test_supplier_owner_gains_records_read_and_create() -> None:
    keys = set(_role_keys("supplier:owner"))
    assert {"records.read", "records.create"} <= keys


def test_supplier_owner_has_no_record_update_or_delete() -> None:
    keys = set(_role_keys("supplier:owner"))
    assert "records.update" not in keys
    assert "records.delete" not in keys


def test_supplier_owner_still_has_no_plot_delete_or_assign() -> None:
    keys = set(_role_keys("supplier:owner"))
    assert "plots.delete" not in keys
    assert "plots.assign" not in keys


def test_supplier_staff_stays_read_only_no_create() -> None:
    keys = set(_role_keys("supplier:staff"))
    assert "records.read" in keys
    assert "records.create" not in keys
    assert "records.update" not in keys
    assert "records.delete" not in keys


def test_internal_admin_still_creates_records() -> None:
    assert "records.create" in set(_role_keys("internal:admin"))


def test_field_officer_still_creates_records() -> None:
    assert "records.create" in set(_role_keys("farmlog:field_officer"))


# --- endpoint wiring (permission + RLS context unchanged) --------------------

def test_post_records_routes_require_records_create_and_set_rls_context() -> None:
    for path in ("", "/with-photos"):
        route = _post_route(path)
        qualnames = {dep.call.__qualname__ for dep in route.dependant.dependencies}
        assert any("require_permission" in q for q in qualnames), path
        assert any(q.startswith("get_rls_context") for q in qualnames), path

    module_src = inspect.getsource(records_module)
    assert "require_permission(PermissionKey.RECORDS_CREATE)" in module_src
    assert PermissionKey.RECORDS_CREATE == "records.create"


# --- data boundary: scope resolution unchanged -------------------------------

def test_owner_with_supplier_resolves_to_supplier_scope() -> None:
    sid = uuid4()
    user = _user(roles=["supplier:owner"], supplier_id=sid)
    scope, supplier_id = _resolve_scope(user, {"supplier:owner"})
    assert scope == "supplier"
    assert supplier_id == str(sid)


def test_owner_without_supplier_is_fail_closed_none() -> None:
    user = _user(roles=["supplier:owner"], supplier_id=None)
    scope, supplier_id = _resolve_scope(user, {"supplier:owner"})
    assert scope == "none"
    assert supplier_id == ""


def test_staff_is_not_broadened_to_supplier_scope() -> None:
    sid = uuid4()
    user = _user(roles=["supplier:staff"], supplier_id=sid)
    scope, _ = _resolve_scope(user, {"supplier:staff"})
    # staff (not owner, not is_supplier_admin) resolves to 'assigned',
    # never 'supplier' — the create grant is owner-only.
    assert scope == "assigned"
