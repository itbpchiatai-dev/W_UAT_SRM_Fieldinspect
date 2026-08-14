"""PlotCycle repository — planting-cycle reads, lifecycle, and plot-mirror
sync (round 7.1 foundation).

Foundation only: these helpers are what the round-7.2 lifecycle endpoints
(open/close cycle) will call. This round wires just get_active_cycle_for_plot
into record-create (records._create_record / public_records); create_cycle /
close_cycle are ready but not yet exposed via any API.

Mirror vs snapshot (see app/db/models/plot_cycle.py's docstring):
  - sync_plot_mirror_from_cycle keeps the plot's MASTER/planting mirror
    columns equal to the active cycle.
  - clear_plot_inspection_snapshot clears only the INSPECTION-derived snapshot
    (same field set as plot_repository.resync_current_status_from_latest's
    "no active record" branch) — never the mirror.
"""
from __future__ import annotations

import datetime
from decimal import ROUND_HALF_UP, Decimal
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.plot import Plot
from app.db.models.plot_cycle import (
    CYCLE_CLOSED_STATUSES,
    CYCLE_STATUS_ACTIVE,
    LOT_SOURCE_AUTO,
    LOT_SOURCE_MANUAL,
    PlotCycle,
)
from app.db.models.record import Record
from app.db.models.supplier import Supplier
from app.services.cycle_reference_fields import normalize_cycle_reference_text
from app.services.lot_number import (
    AutoLotMissingComponentError,
    build_auto_lot_series_key,
    format_auto_lot_no,
    normalize_cycle_label,
    normalize_p_code,
    normalize_po_number,
    normalize_supplier_lot_no,
)


async def get_active_cycle_for_plot(db: AsyncSession, plot_id: UUID) -> PlotCycle | None:
    """The plot's single active cycle, or None. The partial unique index
    (uq_plot_cycles_active_per_plot) guarantees at most one, so limit(1) is
    exact, not a "pick one of many"."""
    result = await db.execute(
        select(PlotCycle)
        .where(PlotCycle.plot_id == plot_id, PlotCycle.status == CYCLE_STATUS_ACTIVE)
        .limit(1)
    )
    return result.scalar_one_or_none()


async def get_active_cycle_for_plot_for_update(
    db: AsyncSession, plot_id: UUID
) -> PlotCycle | None:
    """Like get_active_cycle_for_plot but takes a row lock (SELECT ... FOR
    UPDATE) so a lifecycle transition (round 7.2B close/open) can't race a
    concurrent one into two active cycles. Same single-row guarantee (partial
    unique index)."""
    result = await db.execute(
        select(PlotCycle)
        .where(PlotCycle.plot_id == plot_id, PlotCycle.status == CYCLE_STATUS_ACTIVE)
        .with_for_update()
        .limit(1)
    )
    return result.scalar_one_or_none()


async def assert_no_active_cycle(db: AsyncSession, plot_id: UUID) -> None:
    """Raise if the plot already has an active cycle — the clean-error guard
    round 7.2B's "open new cycle" endpoint calls before creating one. The
    partial unique index (uq_plot_cycles_active_per_plot) is the real
    race-proof backstop; the caller should hold a row lock
    (get_active_cycle_for_plot_for_update) around the check+insert too."""
    if await get_active_cycle_for_plot(db, plot_id) is not None:
        raise ValueError(f"Plot {plot_id} already has an active cycle")


async def get_cycles_for_plot(db: AsyncSession, plot_id: UUID) -> list[PlotCycle]:
    """All cycles for a plot, newest cycle_no first (active cycle, if any,
    leads since it always has the highest number)."""
    result = await db.execute(
        select(PlotCycle)
        .where(PlotCycle.plot_id == plot_id)
        .order_by(PlotCycle.cycle_no.desc())
    )
    return list(result.scalars().all())


async def list_cycles_for_plot(
    db: AsyncSession, plot_id: UUID, limit: int = 50, offset: int = 0
) -> list[PlotCycle]:
    """Paginated cycle history for a plot, newest cycle_no first — the read
    model behind GET /plots/{plotId}/cycles (round 7.2A). RLS (srm_app) scopes
    the rows to the caller; the endpoint additionally verifies the plot itself
    is in scope first."""
    result = await db.execute(
        select(PlotCycle)
        .where(PlotCycle.plot_id == plot_id)
        .order_by(PlotCycle.cycle_no.desc())
        .limit(limit)
        .offset(offset)
    )
    return list(result.scalars().all())


async def get_latest_cycles_for_plots(
    db: AsyncSession, plot_ids: list[UUID]
) -> dict[UUID, PlotCycle]:
    """Batch-load each plot's most recent cycle (any status — active,
    harvested, or cancelled), one query total, keyed by plot_id (round 8-6J).

    For the Excel template's reactivate_plot_with_cycle rows: an inactive
    plot has no active cycle by definition, so its most recent HISTORICAL
    cycle is the only reliable starting point (the plot's mirror columns may
    already be cleared by the deactivate flow) — never inferred one row at a
    time (N+1) when building a whole workbook. Uses Postgres DISTINCT ON,
    same pattern as plot_repository.list_plot_provinces's own `.distinct()`
    usage elsewhere in this codebase. A plot with no cycles at all is simply
    absent from the returned dict — callers must use `.get(plot_id)`."""
    if not plot_ids:
        return {}
    stmt = (
        select(PlotCycle)
        .where(PlotCycle.plot_id.in_(plot_ids))
        .distinct(PlotCycle.plot_id)
        .order_by(PlotCycle.plot_id, PlotCycle.cycle_no.desc())
    )
    result = await db.execute(stmt)
    return {cycle.plot_id: cycle for cycle in result.scalars().all()}


async def get_cycle_labels_for_plots(
    db: AsyncSession, plot_ids: list[UUID]
) -> dict[UUID, set[str]]:
    """Batch-load every non-null historical cycle_label per plot, ONE query
    total, keyed by plot_id (round 8-6K Part B — fixes the importer's
    reactivate_plot_with_cycle cycleLabel-reuse check, which previously ran
    plot_cycle_repository.get_cycles_for_plot once PER ROW).

    Selects only (plot_id, cycle_label) — never full PlotCycle rows, since
    the caller (plot_import._label_reused_in_history) only needs the label
    text. `cycle_label IS NOT NULL` is filtered in SQL (cheap, index-
    friendly); the trim+casefold NORMALIZATION the reuse-check needs stays in
    Python (same contract as the single-plot check this replaces) rather
    than folded into the query, which would complicate/defeat any index on
    this column for no benefit here. A plot with no labelled cycles at all is
    simply absent from the returned dict — callers must use `.get(plot_id,
    set())`."""
    if not plot_ids:
        return {}
    stmt = (
        select(PlotCycle.plot_id, PlotCycle.cycle_label)
        .where(PlotCycle.plot_id.in_(plot_ids), PlotCycle.cycle_label.is_not(None))
    )
    result = await db.execute(stmt)
    labels_by_plot: dict[UUID, set[str]] = {}
    for plot_id, cycle_label in result.all():
        labels_by_plot.setdefault(plot_id, set()).add(cycle_label)
    return labels_by_plot


async def get_cycle(db: AsyncSession, cycle_id: UUID) -> PlotCycle | None:
    """Single cycle by id. RLS still applies at the DB layer (a cycle whose
    plot is out of scope is invisible), but prefer get_cycle_for_plot in the
    API so the URL's plot_id is verified against the cycle too."""
    result = await db.execute(select(PlotCycle).where(PlotCycle.id == cycle_id))
    return result.scalar_one_or_none()


async def get_cycle_for_plot(
    db: AsyncSession, plot_id: UUID, cycle_id: UUID
) -> PlotCycle | None:
    """A cycle that belongs to `plot_id` — app-layer defense-in-depth on top
    of RLS so a /plots/{plotId}/cycles/{cycleId} route can never act on a
    cycle from a DIFFERENT plot (even one in the same scope). None if the
    cycle doesn't exist, isn't this plot's, or is out of scope → the caller
    turns that into a 404 (round 7.2B)."""
    result = await db.execute(
        select(PlotCycle).where(
            PlotCycle.id == cycle_id, PlotCycle.plot_id == plot_id
        )
    )
    return result.scalar_one_or_none()


async def _next_cycle_no(db: AsyncSession, plot_id: UUID) -> int:
    result = await db.execute(
        select(func.max(PlotCycle.cycle_no)).where(PlotCycle.plot_id == plot_id)
    )
    return (result.scalar_one_or_none() or 0) + 1


async def _supplier_code_for_plot(db: AsyncSession, plot: Plot) -> str | None:
    """The AUTHORITATIVE supplier code for a plot's Auto Lot (round 8-12A).

    Read server-side from the Supplier row the plot points at — never from a
    request body, so a client cannot steer which supplier code ends up in
    another supplier's lot number. Queried explicitly rather than through
    `plot.supplier` because that relationship is lazy="select": touching it
    from async code triggers a lazy load mid-await and raises MissingGreenlet.
    """
    result = await db.execute(
        select(Supplier.code).where(Supplier.id == plot.supplier_id)
    )
    return result.scalar_one_or_none()


async def _next_lot_running_no(db: AsyncSession, series_key: str) -> int:
    """The next Auto Lot V2 running number for a series (round 8-12A):
    MAX(lot_running_no) + 1 over existing 'auto' cycles sharing the SAME
    auto_lot_series_key — i.e. the same (supplier, cycleLabel, pCode).

    The scope is deliberately NOT the plot: V2's formula
    {cycleLabel}-{supplierCode}-{pCode}-{running} contains no plot code, so two
    plots of one supplier in the same series must draw from ONE sequence or
    they would produce identical lot numbers. Running therefore continues
    across plots (…, 002, 003) and restarts at 1 for any new series.

    Pre-8-12A rows carry auto_lot_series_key IS NULL and so never contribute to
    (or collide with) a V2 series count.

    Caller must hold the Plot row lock (Plot → PlotCycle order). That lock does
    NOT serialize two different plots in the same series, so the partial unique
    index uq_plot_cycles_auto_lot_series_running is the real backstop: a losing
    racer hits IntegrityError, which the endpoint surfaces as a clean 409 —
    never a duplicate lot, never a 500."""
    result = await db.execute(
        select(func.max(PlotCycle.lot_running_no)).where(
            PlotCycle.auto_lot_series_key == series_key,
            PlotCycle.lot_no_source == LOT_SOURCE_AUTO,
        )
    )
    return (result.scalar_one_or_none() or 0) + 1


async def _resolve_lot_fields(
    db: AsyncSession,
    plot: Plot,
    *,
    cycle_label: str | None,
    p_code: str | None,
    lot_no: str | None,
) -> tuple[str | None, str | None, int | None, str | None]:
    """The single Auto/Manual/legacy lot decision shared by create and update
    (round 8-5A; formula V2 round 8-12A). Returns
    (lot_no, lot_no_source, lot_running_no, auto_lot_series_key):

      - a nonblank `lot_no` → MANUAL wins over Auto (verbatim, no running, no
        series key). Unchanged by V2, and unaffected by supplier_lot_no.
      - blank/None `lot_no` + cycleLabel + supplierCode + pCode all present →
        AUTO: next running number in the (supplier, cycleLabel, pCode) series +
        generated {cycleLabel}-{supplierCode}-{pCode}-{running} (may raise
        LotNumberTooLongError).
      - blank/None `lot_no` + any missing component → ALWAYS raise
        AutoLotMissingComponentError naming the blank field(s).

    Round 8-12A.1 — that last rule is now unconditional. Round 8-12A kept a
    create-path exemption that returned a NULL lot when a component was
    missing, which silently produced ACTIVE cycles with no lot identifier at
    all: the caller asked for an Auto Lot (blank lotNo) and got nothing, with
    no error to act on. A missing component is a data problem the user must
    fix, so both create and update now reject it and the transaction rolls
    back. (The old `auto_required` flag is gone — it no longer selected
    between two behaviours, and keeping it would have implied it did.)

    This binds only what is being WRITTEN now — existing cycles with a NULL lot
    are untouched and still read normally, and a Manual lot still needs no Auto
    component at all.

    `p_code`/`cycle_label` are normalized here; the supplier code is resolved
    server-side. Never logs the lot or its components."""
    trimmed_lot = lot_no.strip() if isinstance(lot_no, str) else None
    if trimmed_lot:
        return trimmed_lot, LOT_SOURCE_MANUAL, None, None

    label = normalize_cycle_label(cycle_label)
    code = normalize_p_code(p_code)
    supplier_code = await _supplier_code_for_plot(db, plot)

    if label and code and supplier_code:
        series_key = build_auto_lot_series_key(supplier_code, label, code)
        running = await _next_lot_running_no(db, series_key)
        generated = format_auto_lot_no(
            cycle_label=label, supplier_code=supplier_code,
            p_code=code, running=running,
        )
        return generated, LOT_SOURCE_AUTO, running, series_key

    missing: list[str] = []
    if not label:
        missing.append("cycleLabel")
    if not supplier_code:
        missing.append("supplierCode")
    if not code:
        missing.append("pCode")
    raise AutoLotMissingComponentError(tuple(missing))


async def create_cycle(
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
) -> PlotCycle:
    """Open a new ACTIVE cycle on `plot` (cycle_no = max+1) and sync the plot
    mirror to it. The caller must ensure no other active cycle exists first
    (round 7.2's open endpoint will close the current one before calling
    here); the partial unique index is the backstop — a second active cycle
    raises IntegrityError.

    Round 8-5A / 8-12A — po_number/p_code/cycle_label/supplier_lot_no are
    normalized (PO upper-cased; the rest trimmed, blank→None) and the lot is
    resolved through the single _resolve_lot_fields decision: a nonblank lot_no
    is stored MANUAL; a blank lot with cycleLabel + pCode (and a resolvable
    supplier code) gets an AUTO
    {cycleLabel}-{supplierCode}-{pCode}-{running}; a blank lot missing any of
    those stays NULL (legacy caller compat). This is the shared create path for
    the start-cycle / plot-with-cycle / rollover endpoints AND the Excel
    import.

    supplier_lot_no is stored verbatim beside the system lot and never
    influences the Manual/Auto decision or the running number.

    Round 8-21A — oracle_supplier_code/oracle_invoice/ref_account are the same
    kind of independent, free-text field as supplier_lot_no (trim, blank→None;
    see app/services/cycle_reference_fields.py): stored verbatim, never part of
    any lot/business decision. A rollover NEVER copies these from the closing
    cycle — a caller that wants them on the new cycle must pass them explicitly
    (see rollover_cycle below)."""
    po = normalize_po_number(po_number)
    label = normalize_cycle_label(cycle_label)
    code = normalize_p_code(p_code)
    resolved_lot, lot_source, running, series_key = await _resolve_lot_fields(
        db, plot, cycle_label=label, p_code=code, lot_no=lot_no,
    )
    cycle = PlotCycle(
        plot_id=plot.id,
        cycle_no=await _next_cycle_no(db, plot.id),
        status=CYCLE_STATUS_ACTIVE,
        crop=crop,
        variety=variety,
        cycle_label=label,
        lot_no=resolved_lot,
        po_number=po,
        p_code=code,
        supplier_lot_no=normalize_supplier_lot_no(supplier_lot_no),
        oracle_supplier_code=normalize_cycle_reference_text(oracle_supplier_code),
        oracle_invoice=normalize_cycle_reference_text(oracle_invoice),
        ref_account=normalize_cycle_reference_text(ref_account),
        lot_no_source=lot_source,
        lot_running_no=running,
        auto_lot_series_key=series_key,
        planting_date=planting_date,
        plant_count=plant_count,
        expected_yield_full=expected_yield_full,
        expected_yield_unit=expected_yield_unit,
        started_at=started_at or datetime.datetime.now(datetime.timezone.utc),
    )
    db.add(cycle)
    await db.flush()
    await sync_plot_mirror_from_cycle(db, plot, cycle)
    return cycle


# Plain plan/planting fields an edit may set as-is — never status/cycle_no/
# started_at/closed_*, so a caller can't smuggle a lifecycle change through the
# edit path (round 7.2B PATCH). lot_no / po_number / p_code are handled
# specially below (lot resolution + normalization), so they are NOT in this set.
_EDITABLE_PLAN_FIELDS = frozenset(
    {"crop", "variety", "cycle_label", "planting_date",
     "plant_count", "expected_yield_full", "expected_yield_unit"}
)


async def update_cycle(
    db: AsyncSession, plot: Plot, cycle: PlotCycle, fields: dict
) -> PlotCycle:
    """Set only the provided plan fields on an active cycle (round 7.2B). Any
    key outside the handled set is ignored — defense-in-depth so status/
    closed_* can never be changed here even if the schema regresses. The caller
    re-syncs the plot mirror afterwards (sync_plot_mirror_from_cycle); the
    inspection snapshot is deliberately NOT touched by an edit.

    Round 8-5A / 8-12A — the caller passes exclude_unset fields, so a key's
    PRESENCE means "the client sent this":
      - po_number / p_code / supplier_lot_no present → normalized and set (PO
        upper-cased). All three are independent of lot_no; in particular
        supplier_lot_no NEVER regenerates the system lot.
      - lot_no present + nonblank → the lot becomes MANUAL (verbatim, running
        and series key cleared). lot_no present + blank/null → re-resolve: AUTO
        from the EFFECTIVE cycleLabel/pCode (the just-updated values above) and
        the plot's supplier. If any component is blank this RAISES
        AutoLotMissingComponentError instead of clearing the lot to NULL — the
        effective values are read AFTER the updates above, so a caller that
        blanks pCode in the same request never falls back to the old one.
      - lot_no ABSENT → the lot (value/source/running/series key) is left
        UNTOUCHED, even when cycle_label or p_code changes — so renaming a
        cycle or correcting a product code never silently renumbers or rewrites
        an existing lot identifier.

    Round 8-21A — oracle_supplier_code/oracle_invoice/ref_account follow the
    SAME exclude_unset-style presence rule as po_number/p_code/supplier_lot_no
    above: key ABSENT from `fields` → untouched; key PRESENT (even as None or
    blank) → normalized (trim, blank→None) and written, clearing the field
    when blank. The API's PlotCycleUpdate reaches this via ordinary
    exclude_unset; the Excel importer decides presence from whether the
    column exists in the workbook at all (see plot_import.py's
    update_current_cycle handling) rather than from whether the row's action
    is present, since Excel has no other way to say "the client sent this".

    `plot` is needed to resolve the supplier code (Auto Lot) and is already
    loaded+locked by every caller (Plot → PlotCycle lock order)."""
    if "po_number" in fields:
        cycle.po_number = normalize_po_number(fields["po_number"])
    if "p_code" in fields:
        cycle.p_code = normalize_p_code(fields["p_code"])
    if "supplier_lot_no" in fields:
        cycle.supplier_lot_no = normalize_supplier_lot_no(fields["supplier_lot_no"])
    if "oracle_supplier_code" in fields:
        cycle.oracle_supplier_code = normalize_cycle_reference_text(fields["oracle_supplier_code"])
    if "oracle_invoice" in fields:
        cycle.oracle_invoice = normalize_cycle_reference_text(fields["oracle_invoice"])
    if "ref_account" in fields:
        cycle.ref_account = normalize_cycle_reference_text(fields["ref_account"])
    for key in _EDITABLE_PLAN_FIELDS:
        if key in fields:
            setattr(cycle, key, normalize_cycle_label(fields[key])
                    if key == "cycle_label" else fields[key])
    if "lot_no" in fields:
        # An explicit blank lotNo asks to regenerate an Auto Lot; with a
        # missing component this raises (never clears the lot).
        # Resolved BEFORE any lot attr is written, so a raise leaves the
        # existing lot/source/running/series key untouched (and the txn rolls
        # back).
        resolved_lot, lot_source, running, series_key = await _resolve_lot_fields(
            db, plot, cycle_label=cycle.cycle_label, p_code=cycle.p_code,
            lot_no=fields["lot_no"],
        )
        cycle.lot_no = resolved_lot
        cycle.lot_no_source = lot_source
        cycle.lot_running_no = running
        cycle.auto_lot_series_key = series_key
    await db.flush()
    return cycle


async def get_latest_active_record_for_cycle(db: AsyncSession, cycle_id: UUID) -> Record | None:
    """The newest ACTIVE record of one cycle by created_at (monotonic insert
    time — never record_date, which is field-reported and can be backdated).
    Shared by _snapshot_final_estimate (close-time ESTIMATE snapshot, round
    8-2.8A) and the final_plot Excel action's finalInspectionRecordId
    resolution (round 8-7A) — one query, never reimplemented twice."""
    result = await db.execute(
        select(Record)
        .where(
            Record.plot_cycle_id == cycle_id,
            Record.is_active.is_(True),
        )
        .order_by(Record.created_at.desc())
        .limit(1)
    )
    return result.scalar_one_or_none()


async def get_latest_active_records_for_cycles(
    db: AsyncSession, cycle_ids: list[UUID]
) -> dict[UUID, Record]:
    """Batch form of get_latest_active_record_for_cycle — ONE query for
    however many cycles need their latest active record (round 8-7A: the
    filtered Excel template pre-fills every active-plot row's
    finalInspectionRecordId, so building a whole workbook must never do this
    per-row). Postgres DISTINCT ON, same pattern as this module's own
    get_latest_cycles_for_plots. A cycle with no active record at all is
    simply absent from the returned dict."""
    if not cycle_ids:
        return {}
    stmt = (
        select(Record)
        .where(Record.plot_cycle_id.in_(cycle_ids), Record.is_active.is_(True))
        .distinct(Record.plot_cycle_id)
        .order_by(Record.plot_cycle_id, Record.created_at.desc())
    )
    result = await db.execute(stmt)
    return {record.plot_cycle_id: record for record in result.scalars().all()}


async def get_latest_active_record_dates_for_cycles(
    db: AsyncSession, cycle_ids: list[UUID]
) -> dict[UUID, datetime.date]:
    """Round 8-19 — the latest ACTIVE record DATE per cycle, for display.

    Deliberately a sibling of get_latest_active_records_for_cycles rather than
    a reuse of it: that one orders by created_at alone, on purpose ("never
    record_date, which is field-reported and can be backdated"), because its
    consumers are audit-ish (the close-time ESTIMATE snapshot and the Excel
    action's finalInspectionRecordId) and want the last row actually WRITTEN.
    This one answers a different question — "what date did the field last
    report an inspection for this cycle" — so it orders by record_date first
    and uses created_at, then id, only to break ties deterministically.
    Changing the other helper's ordering to serve this would have silently
    moved close-time snapshot semantics, so the two stay separate.

    ONE query for however many cycles (Postgres DISTINCT ON, same pattern as
    this module's get_latest_cycles_for_plots / get_latest_active_records_for_
    cycles) — the public plot list must never go per-plot. Every ORDER BY
    column is also selected, so DISTINCT ON is valid regardless of planner
    strictness. A cycle with no active record is simply absent from the dict.

    Scoped by plot_cycle_id — the cycle's real identity, never cycle_label
    text (two cycles can carry the same label), and never plot_id (which
    would let a closed cycle's records leak into the current one).
    """
    if not cycle_ids:
        return {}
    stmt = (
        select(Record.plot_cycle_id, Record.record_date, Record.created_at, Record.id)
        .where(Record.plot_cycle_id.in_(cycle_ids), Record.is_active.is_(True))
        .distinct(Record.plot_cycle_id)
        .order_by(
            Record.plot_cycle_id,
            Record.record_date.desc(),
            Record.created_at.desc(),
            Record.id.desc(),
        )
    )
    result = await db.execute(stmt)
    return {row.plot_cycle_id: row.record_date for row in result.all()}


def set_actual_harvest(
    cycle: PlotCycle,
    *,
    harvest_yield: Decimal | None,
    final_yield_after_clean: Decimal | None,
    final_yield_unit: str | None,
    harvest_date: datetime.date | None,
    final_note: str | None,
) -> None:
    """Stamp the REAL harvested-yield fields on `cycle` (round 8-7A, Excel
    action final_plot) — a pure in-memory attribute setter, no DB call, no
    flush. The caller (plot_import._execute_row) sets these BEFORE calling
    close_cycle, so both this write and close_cycle's own status-flip +
    final-ESTIMATE snapshot land in the SAME flush — one logical "finalize"
    step, matching every other close path in this module. Never touches
    final_yield_pct/final_estimated_yield/final_inspection_record_id (the
    ESTIMATE snapshot) — those stay _snapshot_final_estimate's exclusive
    responsibility, called unchanged by close_cycle right after this."""
    cycle.harvest_yield = harvest_yield
    cycle.final_yield_after_clean = final_yield_after_clean
    cycle.final_yield_unit = final_yield_unit
    cycle.harvest_date = harvest_date
    cycle.final_note = final_note


def _apply_final_estimate_snapshot(cycle: PlotCycle, record: Record | None) -> None:
    """Pure: stamp final_inspection_record_id/final_yield_pct/
    final_estimated_yield on `cycle` from an ALREADY-resolved Record (or clear
    all three when None). Extracted from _snapshot_final_estimate (round
    8-7A.1) so a caller that has already resolved+verified the EXACT record it
    means to snapshot (final_plot's explicit finalInspectionRecordId, or its
    own re-resolved "latest" at commit time) can apply that record directly —
    never validate record A and then snapshot from a second, independently
    re-queried record B.

    Only records of THIS cycle should ever be passed in — this function
    trusts the caller, it does not re-check plot_cycle_id itself. Sets, on
    `cycle`:
      final_inspection_record_id — the source record (kept even when its
                                   yield_pct is NULL, so the source stays known).
      final_yield_pct            — record.yield_pct (0 is a real value, not NULL).
      final_estimated_yield      — expected_yield_full × yield_pct / 100,
                                   quantized to 2 dp; NULL when either input is
                                   NULL. Never resets to NULL when yield is 0.
    record is None → all three NULL."""
    if record is None:
        cycle.final_inspection_record_id = None
        cycle.final_yield_pct = None
        cycle.final_estimated_yield = None
        return

    # Record the source even if its yield_pct is NULL (traceability).
    cycle.final_inspection_record_id = record.id
    yield_pct = record.yield_pct
    if yield_pct is None:
        cycle.final_yield_pct = None
        cycle.final_estimated_yield = None
        return

    cycle.final_yield_pct = yield_pct
    expected = cycle.expected_yield_full
    if expected is None:
        cycle.final_estimated_yield = None
    else:
        # expected × pct / 100, explicit 2-dp quantize (ROUND_HALF_UP). 0 in →
        # 0.00, a real value.
        cycle.final_estimated_yield = (
            expected * yield_pct / Decimal("100")
        ).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


async def _snapshot_final_estimate(db: AsyncSession, cycle: PlotCycle) -> None:
    """Freeze this cycle's FINAL ESTIMATED yield from its latest active
    inspection (round 8-2.8A). Called by close_cycle before the status flips,
    for BOTH terminal statuses:
      - harvested: the next cycle shows "ผลผลิตประมาณการสุดท้าย"
      - cancelled: the next cycle shows "ประมาณการล่าสุดก่อนยกเลิก"

    This is an ESTIMATE, never actual harvested yield — see harvest_yield/
    final_yield_after_clean (round 8-7A) for the actual figures, set
    separately by set_actual_harvest.

    Resolves the cycle's own latest active record internally, then delegates
    the field-stamping to _apply_final_estimate_snapshot — this is the
    default path close_cycle uses when the caller has no already-resolved
    record of its own (every close path except final_plot). No inspection at
    all → all three NULL. Flush-only path (close_cycle flushes)."""
    latest = await get_latest_active_record_for_cycle(db, cycle.id)
    _apply_final_estimate_snapshot(cycle, latest)


# Sentinel distinguishing "no override given" from an explicit `None` (which
# legitimately means "clear the estimate snapshot, no record to source it
# from") for close_cycle's final_estimate_record parameter below.
_RESOLVE_ESTIMATE_INTERNALLY = object()


async def close_cycle(
    db: AsyncSession,
    cycle: PlotCycle,
    *,
    status: str,
    closed_by_id: UUID | None,
    reason: str | None = None,
    final_estimate_record: Record | None = _RESOLVE_ESTIMATE_INTERNALLY,  # type: ignore[assignment]
) -> PlotCycle:
    """Move a cycle to a terminal state (harvested/cancelled), stamping when/
    who/why. History is preserved — this only flips status + close fields (and
    freezes the final estimated-yield snapshot, round 8-2.8A), never deletes the
    cycle or its records.

    The shared close path: the single-plot close endpoint, the rollover
    endpoint (via rollover_cycle), and the Excel close_and_start_new_cycle /
    start_next_cycle-resolved-to-rollover / final_plot actions ALL land here,
    so the snapshot is taken once, identically, for every close. Flush-only —
    the caller's transaction owns the commit; a later create failure in
    rollover_cycle rolls this snapshot back with everything else.

    final_estimate_record (round 8-7A.1): omitted (the default) → the
    final-estimate snapshot resolves the cycle's latest active record
    internally, exactly as before — every pre-existing caller (single-plot
    close, rollover, Excel close_and_start_new_cycle / start_next_cycle-
    resolved-rollover) is unaffected. Pass an explicit Record (or None) only
    when the caller has ALREADY resolved+verified the exact record itself
    (final_plot's authoritative finalInspectionRecordId resolution) — this
    guarantees the record validated is the record snapshotted, never a
    second, possibly-diverged query result.
    """
    if status not in CYCLE_CLOSED_STATUSES:
        raise ValueError(
            f"close_cycle status must be one of {CYCLE_CLOSED_STATUSES}, got {status!r}"
        )
    # Freeze the final estimate BEFORE flipping status (snapshot reads the
    # cycle's own latest inspection; status is irrelevant to the query, but
    # taking it first keeps "close = stamp + freeze" a single logical step).
    if final_estimate_record is _RESOLVE_ESTIMATE_INTERNALLY:
        await _snapshot_final_estimate(db, cycle)
    else:
        _apply_final_estimate_snapshot(cycle, final_estimate_record)
    cycle.status = status
    cycle.closed_at = datetime.datetime.now(datetime.timezone.utc)
    cycle.closed_by_id = closed_by_id
    cycle.close_reason = reason
    await db.flush()
    return cycle


async def rollover_cycle(
    db: AsyncSession,
    plot: Plot,
    current_cycle: PlotCycle,
    *,
    close_status: str,
    closed_by_id: UUID | None,
    close_reason: str | None,
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
) -> tuple[PlotCycle, PlotCycle]:
    """Atomically close `current_cycle` (harvested/cancelled) and open a fresh
    ACTIVE cycle on the same plot. Returns (closed_cycle, new_cycle).

    The shared core of both the single-plot rollover endpoint (round 7.9B) and
    the Excel close_and_start_new_cycle import action (round 7.8) — one place
    for the close→create→clear-snapshot sequence so the two callers can't drift.

    Flushes only, never commits: the caller's single transaction makes the whole
    close+create atomic, so if create_cycle fails the close rolls back too and
    the plot never ends up with no active cycle. The caller MUST have verified
    current_cycle is the plot's active cycle and be holding its row lock
    (get_active_cycle_for_plot_for_update); the partial unique index is the final
    race backstop (a second active cycle raises IntegrityError).

    create_cycle syncs the plot mirror to the NEW cycle; the closed cycle's
    inspection snapshot is then cleared so the fresh (un-inspected) cycle doesn't
    keep advertising the old one's last inspection. Records and the QR key are
    never touched; plot.is_active is left as-is.

    Round 8-21A — oracle_supplier_code/oracle_invoice/ref_account are passed
    straight through to create_cycle for the NEW cycle only; the CLOSED
    cycle's own values (whatever they were) are left exactly as they already
    are. Never copied from `current_cycle` — a caller that wants the new
    cycle to carry the same value must pass it explicitly.
    """
    closed = await close_cycle(
        db, current_cycle, status=close_status,
        closed_by_id=closed_by_id, reason=close_reason,
    )
    new_cycle = await create_cycle(
        db, plot,
        crop=crop, variety=variety, cycle_label=cycle_label, lot_no=lot_no,
        po_number=po_number, p_code=p_code, supplier_lot_no=supplier_lot_no,
        oracle_supplier_code=oracle_supplier_code, oracle_invoice=oracle_invoice,
        ref_account=ref_account,
        planting_date=planting_date, plant_count=plant_count,
        expected_yield_full=expected_yield_full,
        expected_yield_unit=expected_yield_unit,
        started_at=started_at,
    )
    await clear_plot_inspection_snapshot(db, plot)
    return closed, new_cycle


async def sync_plot_mirror_from_cycle(
    db: AsyncSession, plot: Plot, cycle: PlotCycle
) -> Plot:
    """Keep the plot's MASTER/planting mirror columns equal to the active
    `cycle`. Does NOT touch the inspection-derived snapshot (current_stage/
    current_yield_pct/scores/gps/last_inspection*) — that stays owned by
    plot_repository.sync_current_status_from_record."""
    plot.current_crop = cycle.crop
    plot.current_variety = cycle.variety
    plot.current_lot_no = cycle.lot_no
    plot.current_planting_date = cycle.planting_date
    plot.plant_count = cycle.plant_count
    plot.expected_yield_full = cycle.expected_yield_full
    plot.expected_yield_unit = cycle.expected_yield_unit
    await db.flush()
    return plot


async def clear_plot_inspection_snapshot(db: AsyncSession, plot: Plot) -> Plot:
    """Clear ONLY the inspection-derived snapshot on the plot (same field set
    as plot_repository.resync_current_status_from_latest's empty branch). The
    MASTER/planting mirror is deliberately left alone. Round 7.2 will call this
    when a cycle closes so a new/empty cycle doesn't keep advertising the old
    cycle's last inspection."""
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


async def clear_plot_cycle_mirror_and_inspection_snapshot(
    db: AsyncSession, plot: Plot
) -> Plot:
    """Clear BOTH the plot's master/planting mirror (current_crop/variety/
    lot_no/planting_date/plant_count/expected_yield_*) AND the
    inspection-derived snapshot — for when a plot has NO active cycle (round
    7.2B closes the last cycle without opening a new one), so the plot stops
    advertising a cycle that no longer exists. Superset of
    clear_plot_inspection_snapshot (which clears only the inspection half)."""
    plot.current_crop = None
    plot.current_variety = None
    plot.current_lot_no = None
    plot.current_planting_date = None
    plot.plant_count = None
    plot.expected_yield_full = None
    plot.expected_yield_unit = None
    # also clears the inspection snapshot + flushes (one flush covers both).
    await clear_plot_inspection_snapshot(db, plot)
    return plot
