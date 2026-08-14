"""get_supplier_scope_filter — Supplier isolation (Step: supplier scope,
round 5). suppliers has no RLS policy, so this app-layer filter is the only
isolation mechanism. Mirrors the role-priority matrix documented in
app/api/deps/scope.py's module docstring, reusing _resolve_scope for it:

  internal:super_admin / internal:admin / farmlog:supervisor → all suppliers
  farmlog:field_officer                                      → suppliers reachable via plot assignments
  supplier:owner (or is_supplier_admin)                       → own supplier only
  supplier:staff (has supplier_id, not owner/admin)            → own supplier only
  no supplier_id and no recognized scope role                 → none

No DB fixture exists in this repo — get_supplier_scope_filter only builds
SQLAlchemy WHERE-clause objects (never executes them), so these tests call
it directly with fake User/Role objects and inspect the returned clause
structure. No mocking needed.
"""
from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

from app.api.deps.scope import get_supplier_scope_filter


def _user(*, roles, supplier_id=None, is_supplier_admin=False):
    return SimpleNamespace(
        id=uuid4(),
        supplier_id=supplier_id,
        is_supplier_admin=is_supplier_admin,
        roles=[SimpleNamespace(name=r) for r in roles],
    )


async def test_super_admin_sees_all_suppliers_no_filter() -> None:
    conditions = await get_supplier_scope_filter(_user(roles=["internal:super_admin"]))
    assert conditions == []


async def test_internal_admin_sees_all_suppliers_no_filter() -> None:
    conditions = await get_supplier_scope_filter(_user(roles=["internal:admin"]))
    assert conditions == []


async def test_farmlog_supervisor_sees_all_suppliers_no_filter() -> None:
    conditions = await get_supplier_scope_filter(_user(roles=["farmlog:supervisor"]))
    assert conditions == []


async def test_supplier_owner_sees_only_own_supplier() -> None:
    sid = uuid4()
    conditions = await get_supplier_scope_filter(
        _user(roles=["supplier:owner"], supplier_id=sid, is_supplier_admin=True)
    )
    assert len(conditions) == 1
    assert conditions[0].right.value == sid


async def test_supplier_admin_flag_scopes_to_own_supplier_regardless_of_role_name() -> None:
    """is_supplier_admin is OR'd with the supplier:owner role name in
    _resolve_scope — a user with the flag but a different role must still
    be scoped to their own supplier, not fall through to 'assigned'."""
    sid = uuid4()
    conditions = await get_supplier_scope_filter(
        _user(roles=["supplier:staff"], supplier_id=sid, is_supplier_admin=True)
    )
    assert len(conditions) == 1
    assert conditions[0].right.value == sid


async def test_supplier_staff_sees_only_own_supplier() -> None:
    """supplier:staff doesn't carry suppliers.read by default (see
    app/seed.py DEFAULT_ROLES), but the scope function must still resolve
    correctly for the case a per-user permission override grants it."""
    sid = uuid4()
    conditions = await get_supplier_scope_filter(
        _user(roles=["supplier:staff"], supplier_id=sid, is_supplier_admin=False)
    )
    assert len(conditions) == 1
    assert conditions[0].right.value == sid


async def test_supplier_role_without_supplier_id_sees_nothing() -> None:
    conditions = await get_supplier_scope_filter(
        _user(roles=["supplier:staff"], supplier_id=None)
    )
    assert len(conditions) == 1
    assert conditions[0].value is False


async def test_field_officer_scoped_via_plot_assignments() -> None:
    conditions = await get_supplier_scope_filter(_user(roles=["farmlog:field_officer"]))
    assert len(conditions) == 1
    compiled = str(conditions[0]).lower()
    assert "plot_assignments" in compiled
    assert "plots" in compiled
    assert "suppliers.id in" in compiled


async def test_no_recognized_scope_role_sees_nothing() -> None:
    conditions = await get_supplier_scope_filter(
        _user(roles=["internal:user"], supplier_id=None)
    )
    assert len(conditions) == 1
    assert conditions[0].value is False
