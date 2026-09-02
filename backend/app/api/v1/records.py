"""Records CRUD — FarmLog field inspection records."""
from __future__ import annotations

import datetime
from uuid import UUID

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile, status
from fastapi import Path as PathParam
from fastapi.responses import FileResponse, RedirectResponse
from pydantic import ValidationError as PydanticValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps.scope import ScopeFilter, get_rls_context
from app.auth.dependencies import CurrentUser, require_permission
from app.auth.permissions import PermissionKey
from app.core.rate_limit import get_client_ip
from app.db.session import get_db
from app.repositories import plot_cycle_repository as plot_cycle_repo
from app.repositories import plot_repository as plot_repo
from app.repositories import record_repository as repo
from app.schemas.record import RecordCreate, RecordRead, RecordSummary
from app.services.inspection_photos import (
    PHOTO_FILENAME_PATTERN,
    OBSPhotoStorage,
    cleanup_photos,
    get_photo_storage,
    media_type_for,
    photo_filename_from_url,
    validate_and_save_photos,
)
from app.services import inspection_protocols as protocol_service
from app.services.inspection_protocols import ProtocolValidationError
from app.services import yield_calculation
from app.services.yield_calculation import YieldValidationError

router = APIRouter(tags=["records"])


def _to_summary(record) -> RecordSummary:
    s = RecordSummary.model_validate(record)
    if record.plot is not None:
        s.plot_code = record.plot.plot_code
        s.plot_name = record.plot.name
    if getattr(record, "plot_cycle", None) is not None:
        s.cycle_no = record.plot_cycle.cycle_no
        s.cycle_label = record.plot_cycle.cycle_label
    if record.supplier is not None:
        s.supplier_name = record.supplier.name
    return s


def _to_read(record) -> RecordRead:
    r = RecordRead.model_validate(record)
    if record.plot is not None:
        r.plot_code = record.plot.plot_code
        r.plot_name = record.plot.name
    if getattr(record, "plot_cycle", None) is not None:
        c = record.plot_cycle
        r.cycle_no = c.cycle_no
        r.cycle_status = c.status
        r.cycle_label = c.cycle_label
        r.cycle_crop = c.crop
        r.cycle_variety = c.variety
        r.cycle_lot_no = c.lot_no
        r.cycle_planting_date = c.planting_date
        r.cycle_plant_count = c.plant_count
        r.cycle_expected_yield_full = c.expected_yield_full
        r.cycle_expected_yield_unit = c.expected_yield_unit
    if record.supplier is not None:
        r.supplier_name = record.supplier.name
    if getattr(record, "recorded_by", None) is not None:
        r.recorded_by_email = record.recorded_by.email
        r.recorded_by_name = record.recorded_by.full_name
    return r


@router.get("", response_model=list[RecordSummary], dependencies=[
    Depends(require_permission(PermissionKey.RECORDS_READ))
])
async def list_records(
    scope: ScopeFilter,
    db: AsyncSession = Depends(get_db),
    plot_id: UUID | None = None,
    supplier_id: UUID | None = None,
    date_from: datetime.date | None = None,
    date_to: datetime.date | None = None,
    active_only: bool = True,
    limit: int = 50,
    offset: int = 0,
) -> list[RecordSummary]:
    records = await repo.list_records(
        db, scope_conditions=scope,
        plot_id=plot_id, supplier_id=supplier_id,
        date_from=date_from, date_to=date_to,
        active_only=active_only, limit=limit, offset=offset,
    )
    return [_to_summary(r) for r in records]


async def _create_record(
    db: AsyncSession,
    payload: RecordCreate,
    *,
    current_user_id: UUID,
    submitted_ip: str | None,
) -> RecordRead:
    # A plot belongs to exactly one supplier (plots.supplier_id), so the
    # record's supplier is DERIVED from the plot — never trusted from the
    # client body. Without this, the admin form (a separate Supplier select
    # + plot picker/QR scan) could submit a supplier_id that doesn't own the
    # chosen plot, producing records that leak across suppliers and pollute
    # a plot's inspection history. Same "server-derived wins" principle the
    # public flow already enforces (public_records._finish_creating_record).
    #
    # Round 8.0.7 — locks the plot row FIRST (get_plot_for_update; the
    # aggregate lock for this plot's cycle/snapshot state), before the active
    # cycle lock just below — same Plot-before-PlotCycle order used
    # everywhere else this round.
    plot = await plot_repo.get_plot_for_update(db, payload.plot_id)
    if plot is None:
        raise HTTPException(status_code=404, detail="Plot not found")
    # A permanently-closed (is_active=false) plot takes no new inspections.
    # 404, not 403 — same generic "not found or out of scope" message the rest
    # of this flow uses, so it never leaks that the plot exists but is closed.
    if not plot.is_active:
        raise HTTPException(status_code=404, detail="Plot not found")

    # Round 7.1: a record must attach to the plot's ACTIVE planting cycle.
    # No active cycle → 409 (the plot exists and is in scope, but isn't
    # currently accepting inspections — distinct from 404 so the caller can
    # tell "wrong/closed plot" from "plot between cycles").
    #
    # Round 8.0.5 — row-locks the active cycle (SELECT ... FOR UPDATE) so a
    # concurrent close/rollover can't race this create: the lock is held for
    # the rest of this get_db transaction, which also does the record insert
    # and the plot snapshot sync below, so a lifecycle transition either
    # commits fully before this read (and we correctly see "no active cycle"
    # or the new cycle) or blocks until this transaction finishes.
    active_cycle = await plot_cycle_repo.get_active_cycle_for_plot_for_update(db, plot.id)
    if active_cycle is None:
        raise HTTPException(
            status_code=409, detail="No active planting cycle for this plot"
        )

    # Round 8-8A — derive yield_pct/yield_quantity_kg from the client's kg
    # quantity (if any) against the active cycle's own expected_yield_full/
    # expected_yield_unit target, using the SAME cycle object just locked
    # above (no extra query). A legacy client that sends only yieldPct is
    # untouched (derive_yield's None-quantity branch passes it through as-is).
    # Round 8-8B.1 — over 150% is a real, storable result (raises nothing);
    # only a result over MAX_STORABLE_YIELD_PCT (9999.9%, the column's own
    # NUMERIC(5,1) capacity) or a target/quantity that breaks the technical
    # contract raises before anything is written.
    try:
        yield_derivation = yield_calculation.derive_yield(
            yield_quantity_kg=payload.yield_quantity_kg,
            client_yield_pct=payload.yield_pct,
            expected_yield_full=active_cycle.expected_yield_full,
            expected_yield_unit=active_cycle.expected_yield_unit,
        )
    except YieldValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    # supplier_id is DERIVED from the plot (never trusted from the client
    # body); crop/variety/planting_date are SNAPSHOT from the active cycle —
    # the client can't record a crop/variety/planting date that disagrees with
    # the cycle in progress. Aligns the logged-in flow with the public flow's
    # round-20.2 lockdown (public_records._finish_creating_record). yield_pct/
    # yield_quantity_kg are overwritten with the server-derived values above
    # (yield_target_kg_snapshot is NOT a RecordCreate field — passed to
    # repo.create_record as its own keyword-only arg below).
    payload = payload.model_copy(update={
        "supplier_id": plot.supplier_id,
        "crop": active_cycle.crop,
        "variety": active_cycle.variety,
        "planting_date": active_cycle.planting_date,
        "yield_pct": yield_derivation.yield_pct,
        "yield_quantity_kg": yield_derivation.yield_quantity_kg,
    })

    # Freeze the growth-stage inspection protocol (labels + scores) into
    # custom_fields, server-side — a protocol stage additionally requires all
    # 4 scores (→ 422). Non-protocol / no stage passes through unchanged. Any
    # client-supplied snapshot is stripped here, never trusted. The protocol
    # is the admin-editable config (round 5.5), read fresh per create.
    protocol_map = await protocol_service.get_protocol_map(db)
    try:
        payload = protocol_service.apply_protocol_snapshot(payload, protocol_map)
    except ProtocolValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    record = await repo.create_record(
        db, payload, recorded_by_id=current_user_id, submitted_ip=submitted_ip,
        plot_cycle_id=active_cycle.id,
        yield_target_kg_snapshot=yield_derivation.yield_target_kg_snapshot,
    )
    await plot_repo.sync_current_status_from_record(db, record)
    full = await repo.get_record_full(db, record.id)
    if full is None:
        raise HTTPException(status_code=500, detail="Record not found after create")
    return _to_read(full)


@router.post("", response_model=RecordRead, status_code=status.HTTP_201_CREATED,
             dependencies=[
                 Depends(require_permission(PermissionKey.RECORDS_CREATE)),
                 Depends(get_rls_context),
             ])
async def create_record(
    payload: RecordCreate,
    current_user: CurrentUser,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> RecordRead:
    # Audit: store the submitting client's IP (records.submitted_ip),
    # resolved with the rate limiter's trusted-proxy rules — never from
    # the request body.
    return await _create_record(
        db, payload,
        current_user_id=current_user.id,
        submitted_ip=get_client_ip(request),
    )


@router.post(
    "/with-photos", response_model=RecordRead, status_code=status.HTTP_201_CREATED,
    dependencies=[
        Depends(require_permission(PermissionKey.RECORDS_CREATE)),
        Depends(get_rls_context),
    ],
)
async def create_record_with_photos(
    current_user: CurrentUser,
    request: Request,
    payload: str = Form(..., description="RecordCreate fields, JSON-encoded"),
    photos: list[UploadFile] = File(...),
    db: AsyncSession = Depends(get_db),
) -> RecordRead:
    """Multipart variant of POST /api/v1/records for the inspection flow
    that carries 1-5 photos (round 13; photos became optional
    later - a zero-photo submit uses the plain JSON endpoint instead). `payload` carries the same
    fields as RecordCreate — FastAPI can't mix a Pydantic JSON body with
    File parts in one multipart request, so the fields travel as a single
    JSON-encoded Form field instead of exploding into ~20 individual Form
    params. Any `photoUrls` in `payload` is ignored — the 4 uploaded files
    are always what gets saved, same principle as plot/supplier IDs being
    server-derived rather than client-supplied elsewhere in this API.

    Round 8-14A — each photo may be up to 15 MiB on the wire, but what gets
    stored is a normalized JPEG of at most 2 MiB: EXIF-rotated, downscaled to
    2560px on its longest edge, and stripped of all metadata (EXIF/GPS/ICC).
    The public with-photos endpoint runs the identical pipeline — both call
    `validate_and_save_photos`, so neither can drift from the other.
    """
    try:
        record_payload = RecordCreate.model_validate_json(payload)
    except PydanticValidationError as exc:
        raise HTTPException(status_code=422, detail=exc.errors()) from exc

    # Round 8-16B — OBS keys are namespaced by plot_code
    # ({env_prefix}/{plot_code}/{uuid}.webp); a plain read here (no row
    # lock — _create_record below takes the real one) so an OBS upload
    # never runs under a blank plot_code. Also fails fast: no point
    # compressing/uploading photos for a plot that doesn't exist or is out
    # of this caller's scope — _create_record would 404 on it anyway.
    plot_for_storage = await plot_repo.get_plot(db, record_payload.plot_id)
    if plot_for_storage is None:
        raise HTTPException(status_code=404, detail="Plot not found")
    storage = get_photo_storage(plot_code=plot_for_storage.plot_code)
    urls = await validate_and_save_photos(photos, storage)
    record_payload = record_payload.model_copy(update={"photo_urls": urls})

    try:
        return await _create_record(
            db, record_payload,
            current_user_id=current_user.id,
            submitted_ip=get_client_ip(request),
        )
    except Exception:
        # round 13.1: photos are already on disk at this point — if the DB
        # step fails, remove them rather than leaving orphans. Best-effort in
        # two layers: cleanup_photos already swallows its own per-file
        # errors, and this outer guard additionally ensures that even a bug
        # inside cleanup_photos itself can never replace the original error
        # below — that's always what gets raised.
        try:
            await cleanup_photos(urls, storage)
        except Exception:
            pass
        raise


@router.get(
    "/{record_id}/photos/{photo_id}",
    dependencies=[Depends(require_permission(PermissionKey.RECORDS_READ))],
)
async def get_record_photo(
    record_id: UUID,
    scope: ScopeFilter,
    photo_id: str = PathParam(..., pattern=PHOTO_FILENAME_PATTERN),
    db: AsyncSession = Depends(get_db),
) -> FileResponse:
    """Scoped photo download (round 13.1) — deliberately NOT a static-file
    mount (see inspection_photos.py's module docstring for why).

    Uses the exact same scope-filtered lookup as GET /{record_id}, so a
    caller who can't already read this record gets the identical 404 —
    no separate signal distinguishing "wrong scope" from "doesn't exist".
    `photo_id` must additionally be one of the filenames actually present
    in this record's own photo_urls: PHOTO_FILENAME_PATTERN alone accepts
    any syntactically-valid-looking uuid4-shaped filename, so without this
    check a caller could still guess another record's (even another
    supplier's) photo filename and pull it through a record they DO have
    access to.
    """
    record = await repo.get_record_scoped(db, record_id, scope)
    if record is None:
        raise HTTPException(status_code=404, detail="Record not found")

    known_filenames = {photo_filename_from_url(u) for u in record.photo_urls}
    if photo_id not in known_filenames:
        raise HTTPException(status_code=404, detail="Photo not found")

    # Locate the full stored URL so we can distinguish local vs OBS keys.
    stored_url = next(u for u in record.photo_urls if photo_filename_from_url(u) == photo_id)

    storage = get_photo_storage()
    if not stored_url.startswith("/"):
        # Round 8-16B — OBS key (no leading slash): generate a presigned URL
        # and redirect. The object is private; the presigned URL grants
        # time-limited access without making the bucket public.
        if not isinstance(storage, OBSPhotoStorage):
            raise HTTPException(status_code=404, detail="Photo not found")
        return RedirectResponse(
            url=storage.get_presigned_url(stored_url), status_code=302
        )

    # Local filesystem path (legacy or dev):
    try:
        path = storage.resolve_existing_path(photo_id)
    except (FileNotFoundError, AttributeError):
        raise HTTPException(status_code=404, detail="Photo not found")

    return FileResponse(path, media_type=media_type_for(photo_id))


@router.get("/{record_id}", response_model=RecordRead, dependencies=[
    Depends(require_permission(PermissionKey.RECORDS_READ))
])
async def get_record(
    record_id: UUID,
    scope: ScopeFilter,
    db: AsyncSession = Depends(get_db),
) -> RecordRead:
    record = await repo.get_record_scoped(db, record_id, scope)
    if record is None:
        raise HTTPException(status_code=404, detail="Record not found")
    full = await repo.get_record_full(db, record_id)
    if full is None:
        raise HTTPException(status_code=404, detail="Record not found")
    return _to_read(full)


@router.post("/{record_id}/deactivate", response_model=RecordRead, dependencies=[
    Depends(require_permission(PermissionKey.RECORDS_DELETE))
])
async def deactivate_record(
    record_id: UUID,
    scope: ScopeFilter,
    db: AsyncSession = Depends(get_db),
) -> RecordRead:
    """The only mutation an existing record can undergo (round 8.0.5
    append-only lock) — an administrative correction, not a general edit.
    There is deliberately no PATCH /records/{recordId}: a record's
    inspection fields (crop/variety/stage/yield/scores/GPS/photos/notes/
    customFields) can never change after creation; a new inspection always
    means a new Record.
    """
    record = await repo.get_record_scoped(db, record_id, scope)
    if record is None:
        raise HTTPException(status_code=404, detail="Record not found")
    # Round 8.0.7 — lock the plot BEFORE flipping is_active, so a concurrent
    # rollover/close on the same plot can't interleave between this
    # deactivate and the resync below; resync_current_status_from_latest
    # re-locks the same row itself (safe — same transaction, same session),
    # this call's only purpose is to hold the lock across the deactivate too.
    await plot_repo.get_plot_for_update(db, record.plot_id)
    await repo.deactivate_record(db, record)
    # Deactivating (possibly the latest) record must roll the plot snapshot
    # back to the newest record that still counts — or clear it if none left.
    await plot_repo.resync_current_status_from_latest(db, record.plot_id)
    full = await repo.get_record_full(db, record_id)
    if full is None:
        raise HTTPException(status_code=404, detail="Record not found")
    return _to_read(full)
