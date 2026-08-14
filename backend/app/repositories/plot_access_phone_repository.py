"""PlotAccessPhone repository — read the active config + atomically replace it
(round 8-3A).

replace_plot_access_phones is a FULL replacement: the config the caller passes
becomes the plot's complete active phone set. Rows are never hard-deleted —
one dropped from the config is deactivated (is_active=false) and a previously
deactivated number that returns is reactivated, so history and any records that
reference an old row survive.

Locking (Plot aggregate lock, round 8.0.7): the CALLER must already hold the
Plot row lock (plot_repository.get_plot_for_update) BEFORE calling replace —
Plot first, then these phone rows (locked here with SELECT ... FOR UPDATE).
Never the other way around. Flush-only: the caller's transaction owns the
commit/rollback; a unique-index clash raises IntegrityError for the endpoint to
turn into a clean 409.
"""
from __future__ import annotations

import datetime
from collections import defaultdict
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.db.models.plot import Plot
from app.db.models.plot_access_phone import (
    ACCESS_TYPE_ADDITIONAL,
    ACCESS_TYPE_PRIMARY,
    PlotAccessPhone,
)
from app.db.models.record import Record
from app.db.models.supplier import Supplier
from app.schemas.plot import PlotAccessPhoneConfig

# Deterministic ordering for the active config: primary first (access_type
# 'primary' > 'additional' lexically, so DESC), then oldest-first, then id.
_ACTIVE_ORDER = (
    PlotAccessPhone.access_type.desc(),
    PlotAccessPhone.created_at.asc(),
    PlotAccessPhone.id.asc(),
)


async def list_active_plot_access_phones(
    db: AsyncSession, plot_id: UUID
) -> list[PlotAccessPhone]:
    """The plot's ACTIVE access phones, primary-first then deterministic. RLS
    (srm_app) scopes the rows to the caller; the endpoint verifies the plot
    itself is in scope first. Matches the Plot.access_phones relationship's
    order so the read model and this repo agree."""
    result = await db.execute(
        select(PlotAccessPhone)
        .where(
            PlotAccessPhone.plot_id == plot_id,
            PlotAccessPhone.is_active.is_(True),
        )
        .order_by(*_ACTIVE_ORDER)
    )
    return list(result.scalars().all())


async def replace_plot_access_phones(
    db: AsyncSession, plot: Plot, config: PlotAccessPhoneConfig
) -> list[PlotAccessPhone]:
    """Make `config` the plot's COMPLETE active phone set, atomically.

    `config` is already normalized/validated by PlotAccessPhoneConfig (canonical
    numbers, no duplicates, primary not repeated in additional). The caller must
    hold the Plot row lock already (Plot-before-phones order).

    - primaryPhone null + additionalPhones [] → deactivate every active row.
    - a row whose number isn't in the new config → is_active=false (not deleted).
    - a previously deactivated number that's back in the config → reactivated,
      with its access_type set to match (primary ↔ additional freely).
    - the final active set equals the config exactly.

    Two-phase so a single flush can't trip the partial unique indexes on
    itself (e.g. inserting a new active primary before the old one is
    deactivated): phase 1 deactivates all rows and flushes; phase 2 reactivates/
    inserts the desired set. Flush-only — never commits.
    """
    # Desired final active set: phone_normalized → access_type. The schema
    # guarantees the primary isn't also in additional and additionals are unique,
    # so this mapping is 1:1.
    desired: dict[str, str] = {}
    if config.primary_phone is not None:
        desired[config.primary_phone] = ACCESS_TYPE_PRIMARY
    for phone in config.additional_phones:
        desired[phone] = ACCESS_TYPE_ADDITIONAL

    # Lock every existing row for this plot (active + inactive) — phone rows
    # AFTER the Plot lock the caller already holds.
    existing = list(
        (
            await db.execute(
                select(PlotAccessPhone)
                .where(PlotAccessPhone.plot_id == plot.id)
                .with_for_update()
            )
        )
        .scalars()
        .all()
    )

    # Phase 1: deactivate everything, flush → no active rows remain, so phase 2
    # can't collide with a row it's about to replace.
    changed = False
    for row in existing:
        if row.is_active:
            row.is_active = False
            changed = True
    if changed:
        await db.flush()

    # Phase 2: for each desired number, reactivate an existing row (deterministic
    # pick: newest by created_at, then id) or insert a fresh one.
    by_phone: dict[str, list[PlotAccessPhone]] = defaultdict(list)
    for row in existing:
        by_phone[row.phone_normalized].append(row)

    for phone, access_type in desired.items():
        rows = by_phone.get(phone)
        if rows:
            keeper = sorted(
                rows, key=lambda r: (r.created_at, str(r.id)), reverse=True
            )[0]
            keeper.access_type = access_type
            keeper.is_active = True
        else:
            db.add(
                PlotAccessPhone(
                    plot_id=plot.id,
                    phone_normalized=phone,
                    access_type=access_type,
                    is_active=True,
                )
            )

    await db.flush()
    return await list_active_plot_access_phones(db, plot.id)


# --- round 8-3B: public phone-access lookup ---------------------------------

# One (access_row, plot, supplier) tuple per usable access row. Ordered so the
# public list is stable: supplier name, then code, then plot code, then id.
_PHONE_ACCESS_ORDER = (
    Supplier.name.asc(),
    Supplier.code.asc(),
    Plot.plot_code.asc(),
    Plot.id.asc(),
)


def _active_access_rows_stmt():
    """Base SELECT for the public phone-access flow: active access rows whose
    plot AND supplier are both active, joined to plot+supplier, with the plot's
    active cycle eager-loaded (selectinload → one IN-query, no N+1). Callers add
    the phone/id filter."""
    return (
        select(PlotAccessPhone, Plot, Supplier)
        .join(Plot, PlotAccessPhone.plot_id == Plot.id)
        .join(Supplier, Plot.supplier_id == Supplier.id)
        .where(
            PlotAccessPhone.is_active.is_(True),
            Plot.is_active.is_(True),
            Supplier.is_active.is_(True),
        )
        .options(selectinload(Plot.active_cycle))
        .order_by(*_PHONE_ACCESS_ORDER)
    )


async def lookup_active_access_rows_by_phone(
    db: AsyncSession, phone_normalized: str
) -> list[tuple[PlotAccessPhone, Plot, Supplier]]:
    """EXACT-match lookup of the plots a (canonical) phone may inspect. No
    partial/prefix search. Excludes inactive access rows, inactive plots, and
    inactive suppliers. A phone on many plots/suppliers returns many rows; the
    same phone on a different plot is a separate row (per-plot access). RLS
    (srm_app) still applies — the public endpoint runs this under scope='all'
    since a phone can span suppliers."""
    result = await db.execute(
        _active_access_rows_stmt().where(
            PlotAccessPhone.phone_normalized == phone_normalized
        )
    )
    return [tuple(row) for row in result.all()]


async def list_active_access_rows_by_ids(
    db: AsyncSession, access_phone_ids: list[UUID]
) -> list[tuple[PlotAccessPhone, Plot, Supplier]]:
    """Re-resolve a phone-access session's rows from the ids carried in its
    token — same active-only/active-plot/active-supplier filter as the phone
    lookup, so a row (or its plot/supplier) deactivated after the token was
    minted simply disappears from the session. Never mints a new token; never
    consults the phone."""
    if not access_phone_ids:
        return []
    result = await db.execute(
        _active_access_rows_stmt().where(PlotAccessPhone.id.in_(access_phone_ids))
    )
    return [tuple(row) for row in result.all()]


async def get_access_row_for_plot_from_ids(
    db: AsyncSession,
    access_phone_ids: list[UUID],
    plot_id: UUID,
    *,
    for_update: bool = False,
) -> PlotAccessPhone | None:
    """The single ACTIVE access row for `plot_id` whose id is one the token
    carries. None (→ generic 404) if the row was deactivated/removed or never
    belonged to this plot — the phone is NEVER accepted from the client, only
    the row ids proven by the token. for_update takes the row lock immediately
    before a record insert (Plot → PlotCycle → PlotAccessPhone order)."""
    if not access_phone_ids:
        return None
    stmt = (
        select(PlotAccessPhone)
        .where(
            PlotAccessPhone.id.in_(access_phone_ids),
            PlotAccessPhone.plot_id == plot_id,
            PlotAccessPhone.is_active.is_(True),
        )
        .limit(1)
    )
    if for_update:
        stmt = stmt.with_for_update()
    result = await db.execute(stmt)
    return result.scalar_one_or_none()


async def access_phone_ids_inspected_today(
    db: AsyncSession, access_phone_ids: list[UUID], today: datetime.date
) -> set[UUID]:
    """Which of the session's access rows already have an ACTIVE record dated
    today — for the "ตรวจแล้ววันนี้" hint. Keyed by plot_access_phone_id, so a
    record made from a DIFFERENT phone on the same plot does NOT count as this
    session having inspected. One query, not per-plot."""
    if not access_phone_ids:
        return set()
    result = await db.execute(
        select(Record.plot_access_phone_id)
        .where(
            Record.plot_access_phone_id.in_(access_phone_ids),
            Record.is_active.is_(True),
            Record.record_date == today,
        )
        .distinct()
    )
    return {rid for rid in result.scalars().all() if rid is not None}
