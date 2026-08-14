"""scope_where — app-layer isolation dependency + RLS session config setter.

Two exports:
  RLSContext  — sets app.* GUCs (app.scope / app.user_id / app.supplier_id)
                for Postgres RLS.  Use on endpoints that INSERT (no WHERE
                conditions needed at app layer).
  ScopeFilter — sets GUCs AND returns app-layer WHERE conditions for the
                Record model (defense-in-depth on top of RLS).

Scope type matrix:
  internal:super_admin / internal:admin / farmlog:supervisor → 'all'
  farmlog:field_officer                                      → 'assigned'
  supplier:owner (or is_supplier_admin)                      → 'supplier'
  supplier:staff                                             → 'assigned'
  (anything else)                                            → 'none'
"""
from __future__ import annotations

from typing import Annotated, Any

from fastapi import Depends
from sqlalchemy import literal, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import CurrentUser
from app.db.models.plot_assignment import PlotAssignment
from app.db.session import get_db

_FULL_ACCESS_ROLES = {"internal:super_admin", "internal:admin", "farmlog:supervisor"}

# Sentinel for "no real user" (the public/unauthenticated flows). Must be a
# syntactically valid UUID, NOT "": plots_scope/records_scope's 'assigned'
# branch does `user_id = current_setting('app.user_id', true)::uuid` inside
# an uncorrelated subquery, and Postgres's planner hoists that subquery into
# an InitPlan that runs once, unconditionally — regardless of which CASE
# branch actually applies at runtime. So an empty string there throws
# "invalid input syntax for type uuid" even under scope='all'/'supplier',
# where app.user_id is never otherwise consulted. This value never matches a
# real plot_assignments.user_id row.
#
# Round 8-1 (migration 0037): plots_scope/records_scope's cast is now
# `NULLIF(current_setting('app.user_id', true), '')::uuid` — the DB policy
# itself no longer crashes on an empty/missing GUC (same fix migration 0035
# already applied to plot_cycles_scope). This sentinel stays as belt-and-
# suspenders defense-in-depth on top of that DB-level fix, not a replacement
# for it — do not remove it or change its value.
_NO_USER_ID = "00000000-0000-0000-0000-000000000000"


def _resolve_scope(user: Any, role_names: set[str]) -> tuple[str, str]:
    """Return (scope_type, supplier_id_str) for RLS GUC values."""
    if role_names & _FULL_ACCESS_ROLES:
        return "all", ""
    if "farmlog:field_officer" in role_names:
        return "assigned", ""
    if user.supplier_id is not None:
        if "supplier:owner" in role_names or user.is_supplier_admin:
            return "supplier", str(user.supplier_id)
        return "assigned", ""
    return "none", ""


async def _set_rls_config(
    db: AsyncSession, scope: str, user_id: str, supplier_id: str
) -> None:
    """SET LOCAL app.* GUCs so Postgres RLS policies can read them.

    true = transaction-local — cleared automatically at transaction end,
    safe with connection pooling.
    """
    await db.execute(
        text(
            "SELECT set_config('app.scope', :scope, true),"
            " set_config('app.user_id', :uid, true),"
            " set_config('app.supplier_id', :sid, true)"
        ),
        {"scope": scope, "uid": user_id, "sid": supplier_id},
    )


async def get_rls_context(
    user: CurrentUser,
    db: AsyncSession = Depends(get_db),
) -> None:
    """Dependency: sets app.* GUCs for Postgres RLS, returns nothing.

    Use on endpoints that don't need app-layer WHERE conditions (e.g. INSERT,
    or admin read endpoints on tables with RLS enabled).
    """
    role_names = {r.name for r in user.roles}
    scope, supplier_id = _resolve_scope(user, role_names)
    await _set_rls_config(db, scope, str(user.id), supplier_id)


async def get_scope_filter(
    user: CurrentUser,
    db: AsyncSession = Depends(get_db),
) -> list[Any]:
    """Dependency: sets app.* GUCs AND returns app-layer WHERE conditions.

    Defense-in-depth: RLS handles DB-level isolation; these WHERE conditions
    add an independent app-layer check for the Record model.
    """
    role_names = {r.name for r in user.roles}
    scope, supplier_id = _resolve_scope(user, role_names)
    await _set_rls_config(db, scope, str(user.id), supplier_id)

    if scope == "all":
        return []

    if scope == "assigned":
        from app.db.models.record import Record

        return [
            Record.plot_id.in_(
                select(PlotAssignment.plot_id).where(
                    PlotAssignment.user_id == user.id
                )
            )
        ]

    if scope == "supplier":
        from app.db.models.record import Record

        return [Record.supplier_id == user.supplier_id]

    return [literal(False)]


async def get_supplier_scope_filter(user: CurrentUser) -> list[Any]:
    """App-layer WHERE conditions for the Supplier model.

    suppliers has no RLS policy (unlike plots/records — see module
    docstring), so this is the only isolation layer for it. Reuses
    _resolve_scope's role-priority decision, but 'assigned' means two
    different things depending on which branch produced it, so it's
    disambiguated here rather than in _resolve_scope (which only needs to
    know about the Record/Plot shape, not Supplier's):
      - farmlog:field_officer  → suppliers reachable via their plot assignments
      - supplier:staff (has supplier_id, not owner/admin) → their own supplier only
    """
    role_names = {r.name for r in user.roles}
    scope, _ = _resolve_scope(user, role_names)

    from app.db.models.plot import Plot
    from app.db.models.supplier import Supplier

    if scope == "all":
        return []

    if scope == "supplier":
        return [Supplier.id == user.supplier_id]

    if scope == "assigned":
        if "farmlog:field_officer" in role_names:
            return [
                Supplier.id.in_(
                    select(Plot.supplier_id)
                    .join(PlotAssignment, PlotAssignment.plot_id == Plot.id)
                    .where(PlotAssignment.user_id == user.id)
                )
            ]
        if user.supplier_id is not None:
            return [Supplier.id == user.supplier_id]
        return [literal(False)]

    return [literal(False)]


async def set_public_record_rls_context(db: AsyncSession, supplier_id: Any) -> None:
    """RLS context for the public (unauthenticated) record-creation flow
    (round 8) — scoped to exactly the supplier_id proven by a verified
    inspection_session_token, NOT scope='all'. Deliberately narrower than
    get_public_plot_rls_context: minting a record is a write, so it gets
    the tightest scope the existing RLS policy vocabulary supports for an
    unauthenticated caller.

    This is NOT a FastAPI dependency (no Depends() wiring) — supplier_id
    only becomes known after the caller decodes the request body's
    inspection_session_token, which happens inside the route body, not at
    dependency-resolution time. Call it directly there.

    Still not sufficient on its own: the records_scope RLS policy's
    'supplier' branch only constrains by supplier_id, not by the specific
    plot_id the token names, so the caller (app/api/v1/public_records.py)
    must additionally verify plot.supplier_id == supplier_id in app code
    before building the record — see that module for the full chain.
    """
    await _set_rls_config(db, "supplier", _NO_USER_ID, str(supplier_id))


async def get_public_plot_rls_context(db: AsyncSession = Depends(get_db)) -> None:
    """RLS context for PUBLIC (unauthenticated) endpoints that must be able
    to see any active plot — currently only the public inspection-code
    verification flow (round 7).

    There's no user to derive a scope from, and deliberately so: the
    security boundary on that flow is knowing the plot's QR code
    (supplierCode|plotCode) *and* its inspection code, not RLS visibility
    scope. So this always sets scope='all' rather than 'none' — the same
    scope value _resolve_scope already grants internal full-access roles,
    just reached a different way. It does NOT change the plots_scope RLS
    policy itself, only how this one caller participates in it.
    """
    await _set_rls_config(db, "all", _NO_USER_ID, "")


RLSContext = Annotated[None, Depends(get_rls_context)]
ScopeFilter = Annotated[list[Any], Depends(get_scope_filter)]
SupplierScopeFilter = Annotated[list[Any], Depends(get_supplier_scope_filter)]
PublicPlotRLSContext = Annotated[None, Depends(get_public_plot_rls_context)]
