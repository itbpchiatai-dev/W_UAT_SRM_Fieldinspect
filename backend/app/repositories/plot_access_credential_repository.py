"""PlotAccessCredential repository — read a plot's credential status and set/
replace its inspection password (round 8-9A).

Locking (Plot aggregate lock, round 8.0.7): the CALLER must already hold the
Plot row lock (plot_repository.get_plot_for_update) BEFORE calling
set_or_replace_plot_credential — Plot first, then this credential row (locked
here with SELECT ... FOR UPDATE). Never the other way around. Flush-only: the
caller's transaction owns the commit/rollback; a UNIQUE(plot_id) clash from a
concurrent first-set raises IntegrityError for the endpoint to turn into a
clean 409.

Rows are NEVER hard-deleted. Changing a password updates the single row in
place: new hash, new blind-index digest, credential_version + 1, is_active=true.
The first set starts at version 1. Several plots may deliberately hold the same
password — nothing here is unique on the digest/hash, so changing one plot's
password can never disturb another's.

This module never receives, returns, or logs a plaintext password: callers pass
an already-hashed password plus its already-computed digest (built by
app.auth.plot_access_password), and the ORM rows it returns must never be
serialized into a response schema.
"""
from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.db.models.plot import Plot
from app.db.models.plot_access_credential import PlotAccessCredential
from app.db.models.plot_access_phone import PlotAccessPhone
from app.db.models.supplier import Supplier


async def get_credential_status_by_plot_id(
    db: AsyncSession, plot_id: UUID
) -> PlotAccessCredential | None:
    """The plot's credential row REGARDLESS of is_active, or None if it has
    never had one. Used by the admin status read, which reports `configured`
    from is_active — an inactive row is "not configured" to a caller, but its
    version still exists and must not be silently restarted at 1 by a later
    set. RLS (srm_app) scopes the row to the caller; the endpoint verifies the
    plot itself is in scope first."""
    result = await db.execute(
        select(PlotAccessCredential).where(PlotAccessCredential.plot_id == plot_id)
    )
    return result.scalar_one_or_none()


async def get_credential_status_for_plots(
    db: AsyncSession, plot_ids: list[UUID]
) -> dict[UUID, tuple[bool, int]]:
    """{plot_id: (configured, credential_version)} for every plot that HAS a
    credential row — one query for the whole list, never one per plot.

    Round 8-9B.1: the Excel import previews hundreds of rows at once and must
    show each one's credential status; doing that per row would be a textbook
    N+1. A plot absent from the mapping has never had a credential
    (configured=False, no version). `configured` is the row's is_active, so an
    inactive row reports (False, version) — the version is still returned so
    the preview-state binding can detect a change to it.

    Empty list → empty dict, with NO query issued. RLS (srm_app) scopes the
    rows to the caller exactly like the per-plot reads.
    """
    if not plot_ids:
        return {}
    result = await db.execute(
        select(
            PlotAccessCredential.plot_id,
            PlotAccessCredential.is_active,
            PlotAccessCredential.credential_version,
        ).where(PlotAccessCredential.plot_id.in_(plot_ids))
    )
    return {row[0]: (bool(row[1]), int(row[2])) for row in result.all()}


async def get_active_credential_by_plot_id(
    db: AsyncSession, plot_id: UUID
) -> PlotAccessCredential | None:
    """The plot's ACTIVE credential, or None. This is the one a future
    enforcement round (8-9C) verifies against — an inactive row must never
    authorize anything."""
    result = await db.execute(
        select(PlotAccessCredential).where(
            PlotAccessCredential.plot_id == plot_id,
            PlotAccessCredential.is_active.is_(True),
        )
    )
    return result.scalar_one_or_none()


async def set_or_replace_plot_credential(
    db: AsyncSession,
    plot: Plot,
    *,
    password_hash: str,
    password_lookup_digest: str,
    updated_by_id: UUID | None = None,
) -> PlotAccessCredential:
    """Make `password_hash`/`password_lookup_digest` the plot's ONE credential.

    The caller must hold the Plot row lock already (Plot-before-credential
    order) and must have hashed/digested the plaintext itself — no plaintext
    ever reaches this layer.

    - no row yet  → INSERT with credential_version = 1, is_active = true.
    - row exists  → UPDATE in place: new hash + digest, credential_version + 1,
      is_active = true (so this doubles as reactivation), updated_by_id set.
      The row is never deleted and its id never changes, so history and any
      future references to it survive a password change.

    Flush-only — never commits. A concurrent first-set racing on
    UNIQUE(plot_id) surfaces as IntegrityError for the endpoint to map to 409.
    """
    result = await db.execute(
        select(PlotAccessCredential)
        .where(PlotAccessCredential.plot_id == plot.id)
        .with_for_update()
    )
    row = result.scalar_one_or_none()

    if row is None:
        row = PlotAccessCredential(
            plot_id=plot.id,
            password_hash=password_hash,
            password_lookup_digest=password_lookup_digest,
            credential_version=1,
            is_active=True,
            updated_by_id=updated_by_id,
        )
        db.add(row)
    else:
        row.password_hash = password_hash
        row.password_lookup_digest = password_lookup_digest
        row.credential_version = (row.credential_version or 0) + 1
        row.is_active = True
        row.updated_by_id = updated_by_id

    await db.flush()
    return row


# --- round 8-9C groundwork: phone + password blind-index lookup --------------

# Deterministic ordering for the future public list, identical to the phone-only
# flow's (supplier name, code, plot code, id) so 8-9C's list can't reorder
# itself relative to 8-3B's.
_CREDENTIAL_ACCESS_ORDER = (
    Supplier.name.asc(),
    Supplier.code.asc(),
    Plot.plot_code.asc(),
    Plot.id.asc(),
)


async def list_active_access_rows_by_grants(
    db: AsyncSession, access_phone_ids: list[UUID]
) -> list[tuple[PlotAccessPhone, Plot, Supplier, PlotAccessCredential]]:
    """Re-resolve a password-verified session's rows from the access-phone ids
    its token carries (round 8-9C) — ONE set-based query, never one per grant.

    Same active-everything filter as the phone+digest lookup: the access row,
    the plot, the supplier and the credential must all still be active. A row
    (or its plot/supplier/credential) deactivated since the token was minted
    simply disappears from the result, which is what makes a revoked
    assignment or a disabled credential end the session.

    Does NOT check credential id/version — the caller compares those against
    its own grants, because "the version moved" and "the row vanished" are the
    same outcome to the user but different things to assert in a test.
    """
    if not access_phone_ids:
        return []
    result = await db.execute(
        select(PlotAccessPhone, Plot, Supplier, PlotAccessCredential)
        .join(Plot, PlotAccessPhone.plot_id == Plot.id)
        .join(Supplier, Plot.supplier_id == Supplier.id)
        .join(PlotAccessCredential, PlotAccessCredential.plot_id == Plot.id)
        .where(
            PlotAccessPhone.id.in_(access_phone_ids),
            PlotAccessPhone.is_active.is_(True),
            Plot.is_active.is_(True),
            Supplier.is_active.is_(True),
            PlotAccessCredential.is_active.is_(True),
        )
        .options(selectinload(Plot.active_cycle))
        .order_by(*_CREDENTIAL_ACCESS_ORDER)
    )
    return [tuple(row) for row in result.all()]


async def get_credential_readiness_rows(
    db: AsyncSession, scope_conditions: list | None = None
) -> list[tuple[Plot, Supplier, bool]]:
    """Every ELIGIBLE plot plus whether it already has an active credential
    (round 8-9C readiness) — ONE set-based query, DISTINCT per plot, no N+1.

    Eligible = the plot is active, its supplier is active, and it has at least
    one ACTIVE access phone. Deliberately NOT "has an active cycle": a plot
    between cycles still needs its password configured before enforcement is
    switched on, or it would be locked out the moment its next cycle opens.

    A plot with several active phones counts ONCE — the EXISTS subquery is what
    keeps that true, where a join would multiply the row.

    `scope_conditions` are the caller's own RLS/supplier-scope predicates on
    Plot (from get_supplier_scope_filter); RLS applies on top regardless.
    """
    has_active_phone = (
        select(PlotAccessPhone.id)
        .where(
            PlotAccessPhone.plot_id == Plot.id,
            PlotAccessPhone.is_active.is_(True),
        )
        .exists()
    )
    active_credential = (
        select(PlotAccessCredential.id)
        .where(
            PlotAccessCredential.plot_id == Plot.id,
            PlotAccessCredential.is_active.is_(True),
        )
        .exists()
    )
    stmt = (
        select(Plot, Supplier, active_credential.label("configured"))
        .join(Supplier, Plot.supplier_id == Supplier.id)
        .where(
            Plot.is_active.is_(True),
            Supplier.is_active.is_(True),
            has_active_phone,
            *(scope_conditions or []),
        )
        .order_by(Supplier.code.asc(), Plot.plot_code.asc())
    )
    result = await db.execute(stmt)
    return [(row[0], row[1], bool(row[2])) for row in result.all()]


async def lookup_active_access_rows_by_phone_and_digest(
    db: AsyncSession, phone_normalized: str, password_lookup_digest: str
) -> list[tuple[PlotAccessPhone, Plot, Supplier, PlotAccessCredential]]:
    """EXACT-match candidates for "this phone + this password" (round 8-9C).

    Both filters are exact: the canonical phone AND the HMAC blind-index digest.
    Everything in the chain must be active — the access row, the plot, the
    supplier, and the credential. Several plots MAY share one password, and all
    of them are returned; a caller that only wanted one must not assume a
    single row.

    NOT an authorization decision. The digest narrows candidates with one
    indexed lookup instead of bcrypt-verifying every plot; the caller must
    still verify_plot_access_password() against each returned credential's
    password_hash before granting anything.

    RLS (srm_app) still applies — the public endpoint will run this under
    scope='all', since one phone/password pair can span suppliers.
    """
    result = await db.execute(
        select(PlotAccessPhone, Plot, Supplier, PlotAccessCredential)
        .join(Plot, PlotAccessPhone.plot_id == Plot.id)
        .join(Supplier, Plot.supplier_id == Supplier.id)
        .join(PlotAccessCredential, PlotAccessCredential.plot_id == Plot.id)
        .where(
            PlotAccessPhone.phone_normalized == phone_normalized,
            PlotAccessCredential.password_lookup_digest == password_lookup_digest,
            PlotAccessPhone.is_active.is_(True),
            Plot.is_active.is_(True),
            Supplier.is_active.is_(True),
            PlotAccessCredential.is_active.is_(True),
        )
        .options(selectinload(Plot.active_cycle))
        .order_by(*_CREDENTIAL_ACCESS_ORDER)
    )
    return [tuple(row) for row in result.all()]
