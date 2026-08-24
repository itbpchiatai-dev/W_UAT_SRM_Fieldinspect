"""Plot CRUD + assignment repository."""
from __future__ import annotations

import datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.db.models.plot import Plot
from app.db.models.plot_access_phone import PlotAccessPhone
from app.db.models.plot_assignment import PlotAssignment
from app.db.models.plot_cycle import PlotCycle
from app.db.models.record import Record
from app.db.models.user import User
from app.repositories import plot_cycle_repository as plot_cycle_repo
from app.schemas.plot import PlotCreate, PlotUpdate
from app.services.plot_qr_key import generate_qr_key


class PlotAlreadyActiveError(Exception):
    """Raised by reactivate_plot / reactivate_plot_with_cycle when the plot
    is already active — no HTTPException here (repository layer has no HTTP
    concerns); the caller (API endpoint / Excel importer) maps this to a 409
    / row error."""


class PlotHasActiveCycleError(Exception):
    """Raised when an inactive plot is inconsistently found to still have an
    active cycle. Round 8-6H's hardened deactivate invariant (Part B) should
    make this unreachable in normal operation — this is a defensive guard,
    not a normal-flow branch."""


def _plot_read_options():
    """selectinload options shared by get_plot and get_plot_for_update — one
    place so the two loader shapes (read vs row-locked-for-write) can't drift
    apart (round 8.0.7; same one-definition pattern as record_repository's
    _with_relations)."""
    return (
        selectinload(Plot.assignments).selectinload(PlotAssignment.user),
        # supplier for PlotRead's denormalised supplier_code/name (round
        # 6.1); Plot.supplier is lazy="select" which can't lazy-load async.
        selectinload(Plot.supplier),
        # active planting cycle for PlotRead's active_cycle_* read-model
        # (round 7.3.1) — filtered relationship, loads the one active
        # cycle (or nothing), not the full history.
        selectinload(Plot.active_cycle),
        # active access phones for PlotRead's primary/additionalPhones (round
        # 8-3A) — filtered relationship (active rows, primary-first), one
        # IN-query, no N+1.
        selectinload(Plot.access_phones),
    )


async def get_plot(db: AsyncSession, plot_id: UUID) -> Plot | None:
    result = await db.execute(
        select(Plot).where(Plot.id == plot_id).options(*_plot_read_options())
    )
    return result.scalar_one_or_none()


async def get_plot_for_update(db: AsyncSession, plot_id: UUID) -> Plot | None:
    """Like get_plot but takes a row lock (SELECT ... FOR UPDATE) — the Plot
    row is the aggregate lock for this plot (round 8.0.7): every mutation
    that touches the plot itself, its active planting cycle, or its
    inspection-derived snapshot must acquire this FIRST, before locking any
    PlotCycle row, so concurrent mutations of the same plot always
    serialize through here instead of racing each other (e.g. a rollover
    committing between a deactivate's snapshot read and write). Never lock
    a PlotCycle row before calling this for the same plot — that ordering
    is what would let two transactions deadlock on each other.

    `populate_existing=True` forces a refresh of already-loaded attributes
    (including the active_cycle relationship) on a Plot object that's
    already in this session's identity map from an earlier, unlocked read —
    without it, SQLAlchemy would silently keep serving the stale
    already-loaded active_cycle instead of the one visible under this lock.

    Must be called within the transaction that will do the mutation (the
    lock releases at commit/rollback); never commits and never catches a DB
    exception itself. Unknown/out-of-scope plot → None, same as get_plot.
    """
    result = await db.execute(
        select(Plot)
        .where(Plot.id == plot_id)
        .options(*_plot_read_options())
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    return result.scalar_one_or_none()


async def get_plot_by_code(db: AsyncSession, supplier_id: UUID, code: str) -> Plot | None:
    result = await db.execute(
        select(Plot).where(
            Plot.supplier_id == supplier_id,
            func.lower(Plot.plot_code) == code.strip().lower(),
        )
    )
    return result.scalar_one_or_none()


async def get_plot_by_qr_key(db: AsyncSession, qr_key: str) -> Plot | None:
    """Opaque-locator lookup for the round-20 QR deep link — sibling of
    get_plot_by_code, keyed by the unguessable qr_key instead of
    supplier/plot code. Eager-loads supplier (unlike get_plot_by_code,
    whose callers already have the supplier row from a separate lookup)
    since callers here need it and Plot.supplier is lazy="select", which
    can't lazy-load under asyncio.
    """
    result = await db.execute(
        select(Plot)
        .where(Plot.qr_key == qr_key)
        .options(selectinload(Plot.supplier))
    )
    return result.scalar_one_or_none()


def _apply_plot_status_filter(stmt, *, plot_status: str, active_only: bool):
    """Round 8-6I Part B — the single place that turns (plot_status,
    active_only) into a Plot.is_active WHERE clause, shared by list_plots and
    list_plot_provinces so the two filters can't drift.

    active_only=True is backward-compat with every existing caller — it's
    equivalent to plot_status='active'. The endpoint layer (plots.py) already
    rejects the one genuinely conflicting combination (active_only=True with
    plot_status='inactive') with a 422 BEFORE this is ever called, so this
    function only has three real cases to handle: active-only (either flag
    says so), inactive-only, or no filter at all ('all', the default).
    """
    if plot_status == "active" or active_only:
        return stmt.where(Plot.is_active.is_(True))
    if plot_status == "inactive":
        return stmt.where(Plot.is_active.is_(False))
    return stmt


def _apply_cycle_label_filter(stmt, *, cycle_label: str | None):
    """"รอบปลูกปัจจุบัน" filter — shared by list_plots and
    search_plots_by_phone so the two can't drift (same one-place pattern as
    _apply_plot_status_filter above).

    Matches ONLY the plot's ACTIVE PlotCycle.cycle_label, never a closed/
    cancelled historical cycle — a plot whose active cycle's label doesn't
    match (or that has no active cycle at all) never matches, even if some
    past cycle once carried this label. EXISTS (not a JOIN on
    Plot.active_cycle) for the same reason search_plots_by_phone uses EXISTS
    against plot_access_phones: a JOIN would need an explicit uniqueness
    guarantee to avoid duplicate Plot rows, and PlotCycle's own partial
    unique index (at most one 'active' row per plot) already makes EXISTS
    the simpler, equally-correct choice. Exact match after trim — cycle
    labels are free text (round 8-0's user/admin-chosen season label), so no
    case-folding (unlike province, which folds; cycle_label doesn't, same
    treatment as crop/variety's exact-match convention above)."""
    if not cycle_label:
        return stmt
    trimmed = cycle_label.strip()
    if not trimmed:
        return stmt
    exists_clause = (
        select(PlotCycle.id)
        .where(
            PlotCycle.plot_id == Plot.id,
            PlotCycle.status == "active",
            PlotCycle.cycle_label == trimmed,
        )
        .exists()
    )
    return stmt.where(exists_clause)


def _apply_planting_date_filter(
    stmt, *, planting_date_from: datetime.date | None, planting_date_to: datetime.date | None,
):
    """"วันที่เริ่ม...ถึง" filter (round 8-25K) — shared by list_plots and
    search_plots_by_phone, same one-place pattern as
    _apply_cycle_label_filter above (kept next to it deliberately: both are
    EXISTS clauses scoped to the plot's ACTIVE PlotCycle only).

    Filters on PlotCycle.planting_date — the same date already shown on this
    page as "ปลูก: <date>" (Plots.tsx's plantingDateLabel, sourced from
    activeCyclePlantingDate) — not started_at/closed_at, which belong to a
    DIFFERENT, already-existing "สถานะแปลง" filter one dropdown over. Explicit
    product decision (round 8-25K brief): a closed/historical cycle's
    planting_date never matches, even if it falls inside the range — same
    "active cycle only" scope as cycle_label, so the two filters can be
    combined without surprising interaction (e.g. "รอบปลูกปัจจุบัน" + this one
    always describe the SAME cycle, never two different ones on the same
    plot).

    Each bound is independent and inclusive (>=/<=) — either can be given
    alone, matching the record-date filter's date_from/date_to convention in
    record_repository.list_records. No date_from > date_to guard: neither
    that filter nor this one enforces ordering — an inverted range is simply
    over-constrained and returns zero rows, which is self-evidently wrong to
    the person who typed it, not a state worth a 422 for.
    """
    if planting_date_from is None and planting_date_to is None:
        return stmt
    conditions = [
        PlotCycle.plot_id == Plot.id,
        PlotCycle.status == "active",
    ]
    if planting_date_from is not None:
        conditions.append(PlotCycle.planting_date >= planting_date_from)
    if planting_date_to is not None:
        conditions.append(PlotCycle.planting_date <= planting_date_to)
    exists_clause = select(PlotCycle.id).where(*conditions).exists()
    return stmt.where(exists_clause)


def apply_plot_text_filter(stmt, *, q: str | None):
    """Round 8-18B — the free-text "ชื่อแปลงหรือรหัสแปลง" filter, in ONE place
    shared by list_plots, search_plots_by_phone and the template endpoint's
    _fetch_excluded_plots (same one-helper convention as
    _apply_plot_status_filter/_apply_cycle_label_filter above; public name
    because plots.py imports it across the module boundary).

    Matches Plot.plot_code OR Plot.name ONLY. Province was deliberately
    DROPPED from this filter in round 8-18B: the Plots page has had a
    dedicated province filter for several rounds, so folding province into
    the free-text box made the two disagree (a q that matched a province
    silently widened results past what the province dropdown showed).
    Case-insensitive substring (ilike) after trim — unchanged from the
    pre-8-18B behavior for the two columns that remain.
    """
    if not q:
        return stmt
    trimmed = q.strip()
    if not trimmed:
        return stmt
    pattern = f"%{trimmed}%"
    return stmt.where(Plot.plot_code.ilike(pattern) | Plot.name.ilike(pattern))


async def list_plots(
    db: AsyncSession,
    supplier_id: UUID | None = None,
    province: str | None = None,
    crop: str | None = None,
    variety: str | None = None,
    limit: int = 50,
    offset: int = 0,
    q: str | None = None,
    active_only: bool = False,
    plot_status: str = "all",
    cycle_label: str | None = None,
    planting_date_from: datetime.date | None = None,
    planting_date_to: datetime.date | None = None,
) -> list[Plot]:
    stmt = (
        select(Plot)
        # supplier + active cycle eager-loaded for PlotSummary's denormalised
        # supplier_code/name (round 6.1) and active_cycle_* read-model (round
        # 7.3.1) — one extra IN-query each for the whole page, not N+1. The
        # active_cycle relationship is filtered to status='active', so it
        # loads at most one cycle per plot, not the full history.
        .options(
            selectinload(Plot.assignments),
            selectinload(Plot.supplier),
            selectinload(Plot.active_cycle),
            # active access phones for PlotSummary's primary/additionalPhones
            # (round 8-3A) — one IN-query for the whole page, no N+1.
            selectinload(Plot.access_phones),
        )
        .order_by(Plot.supplier_id.asc(), Plot.plot_code.asc())
    )
    if supplier_id is not None:
        stmt = stmt.where(Plot.supplier_id == supplier_id)
    if province:
        stmt = stmt.where(func.lower(Plot.province) == province.strip().lower())
    # Exact match, not lowered: crop/variety values are master-data-driven
    # (picked from a dropdown on both the write and the filter side), so
    # there's no case drift to normalize — same treatment as
    # report_repository's plot_status_rows crop filter.
    if crop:
        stmt = stmt.where(Plot.current_crop == crop)
    if variety:
        stmt = stmt.where(Plot.current_variety == variety)
    stmt = _apply_cycle_label_filter(stmt, cycle_label=cycle_label)
    stmt = _apply_planting_date_filter(
        stmt, planting_date_from=planting_date_from, planting_date_to=planting_date_to,
    )
    stmt = _apply_plot_status_filter(stmt, plot_status=plot_status, active_only=active_only)
    stmt = apply_plot_text_filter(stmt, q=q)
    stmt = stmt.limit(limit).offset(offset)
    result = await db.execute(stmt)
    return list(result.scalars().all())


async def search_plots_by_phone(
    db: AsyncSession,
    phone_digits: str,
    supplier_id: UUID | None = None,
    province: str | None = None,
    crop: str | None = None,
    variety: str | None = None,
    limit: int = 50,
    offset: int = 0,
    plot_status: str = "all",
    cycle_label: str | None = None,
    q: str | None = None,
    planting_date_from: datetime.date | None = None,
    planting_date_to: datetime.date | None = None,
) -> list[Plot]:
    """Round 8-17A.2 — secure phone search: same result shape/eager-loading
    and same supplier/province/crop/variety/plot_status/cycle_label filters
    as list_plots (kept in sync by hand; both are small enough that a shared
    filter-builder would be more indirection than it saves — cycle_label
    reuses _apply_cycle_label_filter directly rather than duplicating that
    one), but the match itself is an EXISTS correlated subquery against
    plot_access_phones rather than a text ILIKE on Plot's own columns.

    Round 8-18B — `q` (ชื่อแปลง/รหัสแปลง) is now accepted here too, applied
    through the SAME apply_plot_text_filter helper GET /plots uses, so the
    two boxes on the Plots page combine as an INTERSECTION: a plot must both
    be authorized by the phone AND match the text. Passing q here (POST body)
    rather than falling back to GET /plots?q= is what lets the phone stay out
    of the URL/access log while still narrowing by text.

    EXISTS (not a JOIN) so a plot with several active phones — or a phone
    that happens to be both this plot's primary AND (in theory) matched by
    some other join condition — can never produce duplicate Plot rows; a
    JOIN would need an extra DISTINCT to get the same guarantee. Only ACTIVE
    rows count — a deactivated phone must not resurface a plot. Both
    access_type values ('primary' and 'additional') are covered by the same
    EXISTS with no separate branch — they carry equal search rights, exactly
    like they carry equal inspection rights.

    Round 8-18B.1 — `phone_digits` is now a PARTIAL match (substring), not
    the exact full number it was through 8-18B: an admin looking up "who is
    5552" should not have to know the whole number. This is an ADMIN-ONLY
    lookup, already behind plots.read + RLS + Supplier scope; it deliberately
    does NOT change plot_access_phone_repository.lookup_active_access_rows_
    by_phone, which /public/inspect uses and which still demands the exact,
    complete, normalized 10-digit number to grant inspection access.

    Caller MUST pass a DIGITS-ONLY fragment, already validated (the endpoint
    does this by hand — see api/v1/plots.py). That guarantee is what makes
    the LIKE pattern below safe: with no '%' or '_' able to reach it, the
    fragment can never widen its own match. (The value itself is
    parameterized by SQLAlchemy either way — this is about LIKE wildcard
    semantics, not SQL injection.) Same "caller resolves input, repository
    just filters" division of responsibility as list_plots.
    """
    phone_exists = (
        select(PlotAccessPhone.id)
        .where(
            PlotAccessPhone.plot_id == Plot.id,
            PlotAccessPhone.phone_normalized.like(f"%{phone_digits}%"),
            PlotAccessPhone.is_active.is_(True),
        )
        .exists()
    )
    stmt = (
        select(Plot)
        .options(
            selectinload(Plot.assignments),
            selectinload(Plot.supplier),
            selectinload(Plot.active_cycle),
            selectinload(Plot.access_phones),
        )
        .where(phone_exists)
        .order_by(Plot.supplier_id.asc(), Plot.plot_code.asc())
    )
    if supplier_id is not None:
        stmt = stmt.where(Plot.supplier_id == supplier_id)
    if province:
        stmt = stmt.where(func.lower(Plot.province) == province.strip().lower())
    if crop:
        stmt = stmt.where(Plot.current_crop == crop)
    if variety:
        stmt = stmt.where(Plot.current_variety == variety)
    stmt = _apply_cycle_label_filter(stmt, cycle_label=cycle_label)
    stmt = _apply_planting_date_filter(
        stmt, planting_date_from=planting_date_from, planting_date_to=planting_date_to,
    )
    stmt = _apply_plot_status_filter(stmt, plot_status=plot_status, active_only=False)
    stmt = apply_plot_text_filter(stmt, q=q)
    stmt = stmt.limit(limit).offset(offset)
    result = await db.execute(stmt)
    return list(result.scalars().all())


async def list_plot_provinces(
    db: AsyncSession,
    supplier_id: UUID | None = None,
    active_only: bool = False,
    plot_status: str = "all",
) -> list[str]:
    stmt = (
        select(Plot.province)
        .where(Plot.province.is_not(None), func.length(func.trim(Plot.province)) > 0)
        .distinct()
        .order_by(Plot.province.asc())
    )
    if supplier_id is not None:
        stmt = stmt.where(Plot.supplier_id == supplier_id)
    stmt = _apply_plot_status_filter(stmt, plot_status=plot_status, active_only=active_only)
    result = await db.execute(stmt)
    return [province for province in result.scalars().all() if province]


async def list_plot_cycle_labels(
    db: AsyncSession,
    supplier_id: UUID | None = None,
    plot_status: str = "all",
) -> list[str]:
    """Distinct PlotCycle.cycle_label values across the caller's scope — the
    "รอบปลูกปัจจุบัน" filter's own dropdown source. Same shape/precedent as
    list_plot_provinces above (distinct, sorted, supplier_id + plot_status
    scoped), but sourced from PlotCycle (joined to Plot for scoping/RLS),
    since cycle_label has no Plot mirror column.

    ONLY the plot's ACTIVE cycle's label counts — same rule the filter
    itself (_apply_cycle_label_filter) enforces — so a closed/cancelled
    cycle's old label is never offered here; a value from this list is
    always guaranteed to match at least one plot right now.
    """
    stmt = (
        select(PlotCycle.cycle_label)
        .join(Plot, PlotCycle.plot_id == Plot.id)
        .where(
            PlotCycle.status == "active",
            PlotCycle.cycle_label.is_not(None),
            func.length(func.trim(PlotCycle.cycle_label)) > 0,
        )
        .distinct()
        .order_by(PlotCycle.cycle_label.asc())
    )
    if supplier_id is not None:
        stmt = stmt.where(Plot.supplier_id == supplier_id)
    stmt = _apply_plot_status_filter(stmt, plot_status=plot_status, active_only=False)
    result = await db.execute(stmt)
    return [label for label in result.scalars().all() if label]


async def create_plot(db: AsyncSession, payload: PlotCreate) -> Plot:
    """Insert a physical Plot only — no planting-cycle/yield-plan data (round
    8.0.4 ownership lock; PlotCreate no longer carries those fields at all).
    The mirror columns (current_crop/current_variety/current_lot_no/
    current_planting_date/plant_count/expected_yield_*) stay NULL until a
    PlotCycle is created for this plot (plot_cycle_repository.create_cycle
    syncs them) — see POST /plots/with-cycle for the atomic plot+first-cycle
    create flow.
    """
    plot = Plot(
        supplier_id=payload.supplier_id,
        plot_code=payload.plot_code.strip().upper(),
        name=payload.name.strip(),
        village=payload.village,
        district=payload.district,
        province=payload.province,
        latitude=payload.latitude,
        longitude=payload.longitude,
        rai=payload.rai,
        qr_key=generate_qr_key(),
    )
    db.add(plot)
    await db.flush()
    # supplier alongside assignments so the PlotRead response can show the
    # denormalised supplier_code/name (round 6.1) right after create;
    # active_cycle (round 7.3.1) loads as None for a brand-new plot (no cycle
    # yet) — refreshing it here avoids an async lazy-load when _to_read reads
    # it (a plot's first cycle is opened separately, via the lifecycle API).
    await db.refresh(
        plot, attribute_names=["assignments", "supplier", "active_cycle", "access_phones"]
    )
    return plot


async def update_plot(db: AsyncSession, plot: Plot, payload: PlotUpdate) -> Plot:
    data = payload.model_dump(exclude_unset=True)
    for field, value in data.items():
        setattr(plot, field, value)
    await db.flush()
    if plot.id is None:
        # Unit tests exercise this helper with a transient Plot() that was
        # never flushed by SQLAlchemy and therefore has no UUID to re-query.
        # Real persisted plots always have an id; only the real path below
        # needs the eager-loaded assignments.user relation for PlotRead.
        return plot
    refreshed = await get_plot(db, plot.id)
    if refreshed is None:
        raise ValueError(f"Plot {plot.id} disappeared during update")
    return refreshed


async def set_plot_assignments(
    db: AsyncSession, plot: Plot, user_ids: list[UUID]
) -> Plot:
    """Replace the full set of assigned users (idempotent)."""
    # Remove assignments not in the new list
    existing = {a.user_id: a for a in plot.assignments}
    new_set = set(user_ids)
    for uid, assignment in list(existing.items()):
        if uid not in new_set:
            await db.delete(assignment)
    # Add missing assignments
    for uid in new_set:
        if uid not in existing:
            user = (await db.execute(select(User).where(User.id == uid))).scalar_one_or_none()
            if user is not None:
                db.add(PlotAssignment(plot_id=plot.id, user_id=uid))
    await db.flush()
    refreshed = await get_plot(db, plot.id)
    if refreshed is None:
        raise ValueError(f"Plot {plot.id} disappeared during assignment update")
    return refreshed


async def sync_current_status_from_record(db: AsyncSession, record: Record) -> Plot:
    """Update the parent plot's inspection-derived current-status snapshot
    from a just-created record — shared by both POST /api/v1/records and
    POST /api/v1/public/records so the two create flows can't drift.

    Round 17.1 field-ownership split (see Plot model's comments for the
    full rationale): plots.current_crop / current_variety / current_lot_no
    / current_planting_date are PLOT MASTER / planting-cycle data — a
    mirror of the plot's active PlotCycle, kept in sync exclusively by
    plot_cycle_repository (round 8.0.4 ownership lock; PlotCreate/PlotUpdate
    no longer carry these fields at all) — and deliberately NEVER touched
    here, even though `record` carries its own crop/variety/planting_date.
    Before round 17.1 this function copied those three from the record too,
    silently clobbering whatever the active cycle had set the moment any
    inspection was submitted — current_lot_no was already exempt (no
    records column backs it) and is the template this fix now applies to
    the other three.

    Only genuinely inspection-derived fields are synced below: stage,
    yield %, the 4 condition scores, GPS-at-visit, and the
    last-inspected-when/by/which-record trio.

    Append-only on the records side: this never modifies `record` or any
    other row in `records`, only the parent Plot's current_* columns.

    Always overwrites unconditionally with this record's values, no
    "is this newer" comparison — callers are expected to call this
    immediately after creating the record that should become the new
    snapshot. That also means last_inspected_at is sourced from
    record.created_at (actual insert time, monotonic) rather than
    record.record_date (the field worker's self-reported visit date, which
    can be backdated) — a backdated late entry must not un-advance the
    snapshot to an earlier moment than a more recent real visit already
    recorded.

    Must run in the same DB session/transaction as the record insert: both
    call sites use FastAPI's get_db dependency, which commits once at the
    end of the request and rolls back the whole transaction — including
    the record insert — if this raises (e.g. plot not found, or the
    ck_plots_current_scores_range check constraint rejects an out-of-range
    score that somehow got past Pydantic validation).

    Round 8.0.5 cycle-aware guard: refuses to sync from a record that isn't
    bound to the plot's CURRENT active cycle — a record from a closed/older
    cycle must never move plots.current_*. Raises (rather than silently
    skipping) so the caller's whole transaction rolls back instead of leaving
    a record inserted with a snapshot that doesn't match it. In practice this
    should never trip on the create paths, which bind the record to the
    active cycle moments earlier under a row lock (records._create_record /
    public_records._finish_creating_record); it's a defensive invariant, not
    a normal-flow branch.

    Round 8.0.7: acquires the Plot row lock itself (get_plot_for_update)
    rather than trusting the caller to already hold it — re-locking a row
    this same transaction already locked earlier (every call site locks the
    plot before reaching this point) is a no-op wait, never a deadlock, so
    this stays correct however it's called.
    """
    plot = await get_plot_for_update(db, record.plot_id)
    if plot is None:
        raise ValueError(f"Plot {record.plot_id} not found for record {record.id}")
    active_cycle = plot.active_cycle
    if active_cycle is None or record.plot_cycle_id != active_cycle.id:
        raise ValueError(
            f"Record {record.id} is bound to cycle {record.plot_cycle_id}, which "
            f"is not plot {plot.id}'s current active cycle "
            f"({active_cycle.id if active_cycle else None}) — refusing to sync "
            "the current-status snapshot from an off-cycle record"
        )

    # created_at has a DB-side server_default; refresh so it's populated
    # regardless of whether the caller already did (idempotent either way).
    await db.refresh(record, attribute_names=["created_at"])

    # current_crop / current_variety / current_lot_no / current_planting_date
    # are plot master data, not set here — see docstring above.
    plot.current_stage = record.growth_stage
    plot.current_yield_pct = record.yield_pct
    plot.current_field_prep_score = record.field_prep_score
    plot.current_weather_score = record.weather_score
    plot.current_care_score = record.care_score
    plot.current_variety_resistance_score = record.variety_resistance_score
    plot.current_gps_lat = record.latitude
    plot.current_gps_lng = record.longitude
    plot.last_inspected_at = record.created_at
    plot.last_inspected_by_code = record.submitted_by_code
    plot.last_inspection_record_id = record.id

    await db.flush()
    return plot


async def resync_current_status_from_latest(db: AsyncSession, plot_id: UUID) -> Plot:
    """Recompute the plot's inspection-derived snapshot from its LATEST
    ACTIVE record IN THE PLOT'S CURRENT ACTIVE CYCLE — the counterpart of
    sync_current_status_from_record for the mutation path (POST
    /records/{id}/deactivate; round 8.0.5 removed the PATCH mutation path).

    Create-time sync alone leaves the snapshot stale the moment the latest
    record is deactivated (the plot keeps advertising values from a record
    that no longer counts). This keeps the invariant: plot current_* == the
    newest active record OF THE ACTIVE CYCLE, no matter which record was
    just changed.

    "Latest" is by created_at desc, matching sync_current_status_from_record's
    monotonic-insert-time semantic (record_date is field-worker-reported and
    can be backdated). Round 8.0.5 — cycle-aware: only records bound to the
    plot's CURRENT active cycle are considered; a record from a closed/older
    cycle is NEVER used, even if it's the newest active record overall — no
    fallback to an old cycle once the active cycle has no records of its own.
    When there's no active cycle, or the active cycle has no active record,
    the inspection-derived fields are cleared to NULL; plot master /
    planting-cycle data (current_crop / current_variety / current_lot_no /
    current_planting_date / plant_count / expected_yield_*) is PlotCycle-owned
    (round 8.0.4) and never touched here — same field-ownership split the
    sync function observes.

    Round 8.0.7: acquires the Plot row lock itself (get_plot_for_update),
    same reasoning as sync_current_status_from_record above — this is what
    closes the race the user reported: a deactivate reading "the active
    cycle is X" and a concurrent rollover replacing X with Y must now
    serialize on this row instead of interleaving.
    """
    plot = await get_plot_for_update(db, plot_id)
    if plot is None:
        raise ValueError(f"Plot {plot_id} not found for status resync")
    active_cycle = plot.active_cycle

    latest = None
    if active_cycle is not None:
        result = await db.execute(
            select(Record)
            .where(
                Record.plot_id == plot_id,
                Record.plot_cycle_id == active_cycle.id,
                Record.is_active.is_(True),
            )
            .order_by(Record.created_at.desc())
            .limit(1)
        )
        latest = result.scalar_one_or_none()

    if latest is not None:
        return await sync_current_status_from_record(db, latest)

    plot.current_stage = None
    plot.current_yield_pct = None
    plot.current_field_prep_score = None
    plot.current_weather_score = None
    plot.current_care_score = None
    plot.current_variety_resistance_score = None
    plot.current_gps_lat = None
    plot.current_gps_lng = None
    plot.last_inspected_at = None
    plot.last_inspected_by_code = None
    plot.last_inspection_record_id = None

    await db.flush()
    return plot


# --- Round 8-6H: plot reactivation --------------------------------------
# Single source of truth reused by BOTH POST /plots/{plotId}/reactivate[-with-
# cycle] (app/api/v1/plots.py) and the Excel reactivate_plot_with_cycle
# action (app/services/plot_import.py) — so the two callers can never drift
# on what "reactivate" actually does. Flush-only, never commits (the caller's
# transaction — get_db for the API, the import endpoint's single transaction
# for Excel — owns the commit/rollback); no permission/HTTPException here,
# only domain state + the two errors above, which each caller maps to its
# own error shape (HTTPException / Excel row error).
#
# Caller MUST already hold the Plot row lock (get_plot_for_update /
# _lock_existing_plots) before calling either function — same Plot-before-
# PlotCycle lock order as every other lifecycle mutation in this app.

async def reactivate_plot(db: AsyncSession, plot: Plot) -> Plot:
    """Reopen `plot` WITHOUT starting a new cycle — it becomes active again
    but still has no active cycle (can't be inspected) until a separate
    start-cycle call. Clears the stale mirror/inspection snapshot (same
    helper the hardened deactivate endpoint now calls, Part B) so a plot
    that's been closed for a while never keeps advertising crop/yield/
    last-inspection data from before closure. QR key, access phones,
    assignments, and cycle/record history are untouched — none of those are
    read or written here."""
    if plot.is_active:
        raise PlotAlreadyActiveError(f"Plot {plot.id} is already active")
    if await plot_cycle_repo.get_active_cycle_for_plot_for_update(db, plot.id) is not None:
        raise PlotHasActiveCycleError(
            f"Plot {plot.id} is inactive but has an active cycle"
        )
    plot.is_active = True
    await plot_cycle_repo.clear_plot_cycle_mirror_and_inspection_snapshot(db, plot)
    return plot


async def reactivate_plot_with_cycle(
    db: AsyncSession,
    plot: Plot,
    *,
    crop: str | None = None,
    variety: str | None = None,
    cycle_label: str | None = None,
    lot_no: str | None = None,
    po_number: str | None = None,
    p_code: str | None = None,
    supplier_lot_no: str | None = None,
    oracle_supplier_code: str | None = None,
    oracle_invoice: str | None = None,
    ref_account: str | None = None,
    planting_date: datetime.date | None = None,
    plant_count: int | None = None,
    expected_yield_full: Decimal | None = None,
    expected_yield_unit: str | None = None,
    started_at: datetime.datetime | None = None,
) -> tuple[Plot, PlotCycle]:
    """Atomically reopen `plot` AND start its first new cycle (round 8-6H
    Part D/E) — the shared core behind both the API endpoint and the Excel
    reactivate_plot_with_cycle action.

    Raises PlotAlreadyActiveError / PlotHasActiveCycleError up front, before
    any state changes. Otherwise: flips is_active, then delegates to the
    SAME plot_cycle_repository.create_cycle every other "start a cycle" path
    uses (cycle_no = max+1, Auto/Manual lot resolution, plot mirror sync) —
    never a parallel cycle-creation implementation — and finally clears the
    inspection-derived snapshot (create_cycle already synced the master/
    planting mirror to the new cycle; only the inspection half needs
    clearing, same as start_plot_cycle's post-create step).

    Never commits — flush-only. If create_cycle raises (LotNumberTooLongError,
    IntegrityError, or anything else), the exception propagates and the
    caller's transaction rolls back EVERYTHING in this function, including
    the `plot.is_active = True` flip above (it was never committed) — the
    plot is guaranteed to come back inactive on any failure, with no manual
    revert code needed.
    """
    if plot.is_active:
        raise PlotAlreadyActiveError(f"Plot {plot.id} is already active")
    if await plot_cycle_repo.get_active_cycle_for_plot_for_update(db, plot.id) is not None:
        raise PlotHasActiveCycleError(
            f"Plot {plot.id} is inactive but has an active cycle"
        )
    plot.is_active = True
    cycle = await plot_cycle_repo.create_cycle(
        db, plot,
        crop=crop, variety=variety, cycle_label=cycle_label, lot_no=lot_no,
        po_number=po_number, p_code=p_code, supplier_lot_no=supplier_lot_no,
        oracle_supplier_code=oracle_supplier_code, oracle_invoice=oracle_invoice,
        ref_account=ref_account,
        planting_date=planting_date,
        plant_count=plant_count, expected_yield_full=expected_yield_full,
        expected_yield_unit=expected_yield_unit, started_at=started_at,
    )
    await plot_cycle_repo.clear_plot_inspection_snapshot(db, plot)
    return plot, cycle
