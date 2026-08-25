"""Master Data — editable dropdown option source (Step 12.5, Spec §3.5).

Read is gated by `records.read` so the record form can load options. Mutations
are gated by `masterdata.*` (Super Admin / Supervisor — manageMaster).

Round 8-15A adds the crop/variety Excel import (backend foundation only, no
frontend UI this round) — template/preview/commit + read-only result-
workbook variants, all under crop-variety-import/. Registered BEFORE
`/{item_id}` in file order for the same reason plots.py orders its /import/
routes before /{plot_id}: a static multi-segment path can't collide with a
single-segment `{item_id}` match anyway, but keeping the static routes first
matches the established convention.
"""
from __future__ import annotations

import datetime
from uuid import UUID

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile, status
from fastapi.responses import Response
from pydantic import ValidationError
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import CurrentUser, require_any_permission, require_permission
from app.auth.permissions import PermissionKey
from app.db.models.master_data import MasterData
from app.db.session import get_db
from app.repositories import master_data_repository as repo
from app.schemas.master_data import MasterDataCreate, MasterDataRead, MasterDataUpdate
from app.schemas.master_data_import import (
    CropVarietyImportCommitResult,
    CropVarietyImportPreview,
    CropVarietyImportPreviewState,
)
from app.services import master_data_crop_variety_import as cv_import
from app.services import p_code_master
from app.services.loggers.activity_logger import ActivityLogger
from app.services.master_data_crop_variety_import_report import (
    PHASE_COMMIT,
    PHASE_PREVIEW,
    build_crop_variety_import_result_workbook,
    result_filename,
)

router = APIRouter(tags=["masterdata"])

# Same file-size cap Plot Import enforces (app/api/v1/plots.py's
# _IMPORT_MAX_BYTES) — an admin master-data import is small.
_CV_IMPORT_MAX_BYTES = 2 * 1024 * 1024

# Round 8-15A.1 — previewState is a client-echoed JSON blob, one entry per
# import row, so its worst-case size scales with MAX_IMPORT_ROWS × the
# widest possible row (crop/variety/variety_parent_at_preview each up to 255
# chars + field overhead ≈ under 1 KB/row worst case). 1000 rows × ~1 KB ≈
# 1 MB; this cap matches the file-upload cap above for a simple, consistent
# story ("nothing in this feature accepts more than 2 MB") with ~2x headroom
# over the worst-case row content. Checked BEFORE json.loads/model_validate_
# json ever runs, so an oversized blob is rejected without the cost of
# parsing it.
_CV_PREVIEW_STATE_MAX_BYTES = 2 * 1024 * 1024

_MSG_INVALID_PREVIEW_STATE = "previewState ไม่ถูกต้อง"


async def _read_cv_import_upload(file: UploadFile) -> bytes:
    if not (file.filename or "").lower().endswith(".xlsx"):
        raise HTTPException(status_code=422, detail="รองรับเฉพาะไฟล์ .xlsx เท่านั้น")
    content = await file.read()
    if not content:
        raise HTTPException(status_code=422, detail="ไฟล์ว่างเปล่า")
    if len(content) > _CV_IMPORT_MAX_BYTES:
        raise HTTPException(status_code=422, detail="ไฟล์ใหญ่เกินไป (สูงสุด 2 MB)")
    return content


def _parse_cv_preview_state(raw: str | None) -> CropVarietyImportPreviewState | None:
    """Parse + bounds-check the optional multipart previewState field before
    it ever reaches the service layer (round 8-15A.1). Absent/blank → None
    (the service itself then raises a state-conflict — every commit needs an
    approved preview).

    Every failure path below — oversized, malformed JSON, a field outside
    its schema bounds (see schemas/master_data_import.py's Field
    constraints: row_number>0, crop/variety/parent<=255 chars, file_sha256
    must be 64 lowercase hex chars, action must be one of the 6 known
    values), too many rows, or a duplicate row_number — raises the SAME
    generic HTTPException(422, "previewState ไม่ถูกต้อง"). None of them ever
    echoes the raw string, a row's crop/variety, or any other submitted
    value back to the caller — previewState is never a credential, but it
    IS attacker-reachable input, so a validation error must reveal nothing
    about what was rejected or why.

    This mirrors plot_import's own _parse_preview_state (plots.py): an
    input-boundary check only, never an authorization/state decision — the
    service always re-derives the real plan from a fresh parse + fresh DB
    query and treats a syntactically-valid previewState as nothing more than
    "one candidate expectation to compare against," never as trusted state.
    """
    if raw is None or raw.strip() == "":
        return None
    # Checked BEFORE any JSON parsing — an oversized blob is rejected by its
    # byte size alone, without the cost of parsing it.
    if len(raw.encode("utf-8")) > _CV_PREVIEW_STATE_MAX_BYTES:
        raise HTTPException(status_code=422, detail=_MSG_INVALID_PREVIEW_STATE)
    try:
        state = CropVarietyImportPreviewState.model_validate_json(raw)
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=_MSG_INVALID_PREVIEW_STATE) from exc
    # Row-list length and row_number uniqueness aren't expressible as a
    # single pydantic Field constraint on CropVarietyImportPreviewStateRow
    # itself (they're properties of the LIST, and the cap needs the service's
    # own MAX_IMPORT_ROWS) — checked here, same as plot_import's own
    # equivalent list-level checks in _parse_preview_state.
    if len(state.rows) > cv_import.MAX_IMPORT_ROWS:
        raise HTTPException(status_code=422, detail=_MSG_INVALID_PREVIEW_STATE)
    row_numbers = [r.row_number for r in state.rows]
    if len(row_numbers) != len(set(row_numbers)):
        raise HTTPException(status_code=422, detail=_MSG_INVALID_PREVIEW_STATE)
    return state


@router.get("/crop-variety-import/template", dependencies=[
    Depends(require_permission(PermissionKey.MASTERDATA_READ)),
])
async def download_crop_variety_import_template(
    db: AsyncSession = Depends(get_db),
) -> Response:
    """Read-only — never writes to the DB. See
    services/master_data_crop_variety_import.build_template for the full
    content contract (active crops only, active+inactive varieties, one
    blank-variety row for a crop with none, dropdowns on crop/varietyStatus)."""
    content = await cv_import.build_template(db)
    return Response(
        content=content,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={
            "Content-Disposition": 'attachment; filename="crop-variety-import-template.xlsx"',
            "Cache-Control": "no-store",
        },
    )


@router.post(
    "/crop-variety-import/preview", response_model=CropVarietyImportPreview,
    dependencies=[
        Depends(require_permission(PermissionKey.MASTERDATA_CREATE)),
        Depends(require_permission(PermissionKey.MASTERDATA_UPDATE)),
    ],
)
async def preview_crop_variety_import(
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
) -> CropVarietyImportPreview:
    """Parse + validate every row, WITHOUT writing anything. Safe/read-only."""
    content = await _read_cv_import_upload(file)
    try:
        return await cv_import.build_preview(db, content)
    except cv_import.CropVarietyImportFileError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post(
    "/crop-variety-import/commit", response_model=CropVarietyImportCommitResult,
    dependencies=[
        Depends(require_permission(PermissionKey.MASTERDATA_CREATE)),
        Depends(require_permission(PermissionKey.MASTERDATA_UPDATE)),
    ],
)
async def commit_crop_variety_import(
    request: Request,
    user: CurrentUser,
    file: UploadFile = File(...),
    # Explicit alias — every other API field on the wire is camelCase, and a
    # client sends the multipart field as "previewState" (same convention as
    # plots.py's commit_plot_import).
    preview_state: str | None = Form(None, alias="previewState"),
    db: AsyncSession = Depends(get_db),
) -> CropVarietyImportCommitResult:
    """Re-validate server-side (never trusting a client preview) and execute
    every row in ONE transaction (this endpoint's single get_db session —
    the service only ever flushes). Any invalid row → 422 with the full
    preview, nothing written. A stale file/plan/master_data-state → 409,
    nothing written."""
    content = await _read_cv_import_upload(file)
    parsed_state = _parse_cv_preview_state(preview_state)
    try:
        result = await cv_import.commit(db, content, preview_state=parsed_state)
    except cv_import.CropVarietyImportHasErrors as exc:
        raise HTTPException(
            status_code=422,
            detail={
                "message": "พบข้อผิดพลาดในบางแถว — ไม่ได้บันทึกข้อมูลใด ๆ",
                "preview": exc.preview.model_dump(by_alias=True, mode="json"),
            },
        ) from exc
    except cv_import.CropVarietyImportStateConflict as exc:
        raise HTTPException(status_code=409, detail=exc.message) from exc
    except cv_import.CropVarietyImportFileError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except IntegrityError as exc:
        raise HTTPException(
            status_code=409,
            detail="นำเข้าไม่สำเร็จเนื่องจากมีการเปลี่ยนแปลงที่ขัดแย้ง — ไม่ได้บันทึกข้อมูลใด ๆ",
        ) from exc

    await ActivityLogger(db).log(
        action="masterdata.crop_variety_import.commit",
        action_type="update",
        resource_type="master_data",
        user=user,
        request=request,
        risk_level="low",
        metadata={
            "createdCrops": result.created_crops,
            "createdVarieties": result.created_varieties,
            "activatedVarieties": result.activated_varieties,
            "deactivatedVarieties": result.deactivated_varieties,
            "skippedRows": result.skipped_rows,
            "totalRows": result.total_rows,
        },
    )
    return result


def _cv_xlsx_response(content: bytes, filename: str) -> Response:
    return Response(
        content=content,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Cache-Control": "no-store",
        },
    )


@router.post(
    "/crop-variety-import/preview-report",
    dependencies=[
        Depends(require_permission(PermissionKey.MASTERDATA_CREATE)),
        Depends(require_permission(PermissionKey.MASTERDATA_UPDATE)),
    ],
)
async def preview_crop_variety_import_report(
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
) -> Response:
    """Read-only: same core as .../preview, returned as a validation
    workbook (READY/SKIPPED/ERROR per row) instead of JSON. Never writes."""
    content = await _read_cv_import_upload(file)
    try:
        summary, row_views = await cv_import.preview_row_views(db, content)
    except cv_import.CropVarietyImportFileError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    processed_at = datetime.datetime.now(datetime.timezone.utc)
    # Round 8-15A.1 — counts come from the authoritative `summary` (already
    # correctly deduplicated), never re-tallied from row_views.
    workbook = build_crop_variety_import_result_workbook(
        row_views, phase=PHASE_PREVIEW, completed=False,
        original_filename=file.filename, processed_at=processed_at,
        crops_to_create=summary.crops_to_create, varieties_to_create=summary.varieties_to_create,
        varieties_to_activate=summary.varieties_to_activate,
        varieties_to_deactivate=summary.varieties_to_deactivate,
    )
    return _cv_xlsx_response(workbook, result_filename(PHASE_PREVIEW, processed_at))


@router.post(
    "/crop-variety-import/commit-report",
    dependencies=[
        Depends(require_permission(PermissionKey.MASTERDATA_CREATE)),
        Depends(require_permission(PermissionKey.MASTERDATA_UPDATE)),
    ],
)
async def commit_crop_variety_import_report(
    request: Request,
    user: CurrentUser,
    file: UploadFile = File(...),
    preview_state: str | None = Form(None, alias="previewState"),
    db: AsyncSession = Depends(get_db),
) -> Response:
    """Same core as .../commit (re-validates, checks drift, executes ONCE in
    this endpoint's single transaction), returned as a completed-result
    workbook (COMPLETED/SKIPPED per row) instead of JSON."""
    content = await _read_cv_import_upload(file)
    parsed_state = _parse_cv_preview_state(preview_state)
    try:
        # Round 8-15A.1 — `result` is the SAME CropVarietyImportCommitResult
        # the JSON /commit endpoint returns (created_crops already
        # deduplicated by _commit_execute's `new_crop_values` set). Sourcing
        # BOTH the activity log AND the workbook summary from this ONE
        # object — never re-deriving from row_views — is what keeps all
        # three (JSON result, workbook, activity log) in agreement.
        result, row_views = await cv_import.commit_row_views(db, content, preview_state=parsed_state)
    except cv_import.CropVarietyImportHasErrors as exc:
        raise HTTPException(
            status_code=422,
            detail={
                "message": "พบข้อผิดพลาดในบางแถว — ไม่ได้บันทึกข้อมูลใด ๆ",
                "preview": exc.preview.model_dump(by_alias=True, mode="json"),
            },
        ) from exc
    except cv_import.CropVarietyImportStateConflict as exc:
        raise HTTPException(status_code=409, detail=exc.message) from exc
    except cv_import.CropVarietyImportFileError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except IntegrityError as exc:
        raise HTTPException(
            status_code=409,
            detail="นำเข้าไม่สำเร็จเนื่องจากมีการเปลี่ยนแปลงที่ขัดแย้ง — ไม่ได้บันทึกข้อมูลใด ๆ",
        ) from exc

    await ActivityLogger(db).log(
        action="masterdata.crop_variety_import.commit",
        action_type="update",
        resource_type="master_data",
        user=user,
        request=request,
        risk_level="low",
        metadata={
            "createdCrops": result.created_crops,
            "createdVarieties": result.created_varieties,
            "activatedVarieties": result.activated_varieties,
            "deactivatedVarieties": result.deactivated_varieties,
            "skippedRows": result.skipped_rows,
            "totalRows": result.total_rows,
            "via": "commit-report",
        },
    )

    processed_at = datetime.datetime.now(datetime.timezone.utc)
    workbook = build_crop_variety_import_result_workbook(
        row_views, phase=PHASE_COMMIT, completed=True,
        original_filename=file.filename, processed_at=processed_at,
        crops_to_create=result.created_crops, varieties_to_create=result.created_varieties,
        varieties_to_activate=result.activated_varieties, varieties_to_deactivate=result.deactivated_varieties,
    )
    return _cv_xlsx_response(workbook, result_filename(PHASE_COMMIT, processed_at))


# Round 8-22A — master_data has a UNIQUE(type, value) index (migration 0019,
# uq_master_data_type_value) spanning BOTH active and inactive rows, so
# re-adding a value that already exists (even a deactivated one) always
# fails at the DB. Before this round, create/update never caught that
# IntegrityError at all — it propagated out of the endpoint as an unhandled
# 500 (Starlette's generic, stack-trace-free response, since APP_DEBUG is
# false outside dev — see app/main.py's `debug=settings.APP_DEBUG`), which
# read to the user as "Add ชนิดพืช ไม่สำเร็จ" with no explanation of why.
# The messages below never echo the DB driver's own error text (it can
# carry the raw SQL) — only the user's own already-known input value.
_MSG_DUPLICATE_ACTIVE = 'มี "{value}" อยู่แล้วในระบบ'
_MSG_DUPLICATE_INACTIVE = (
    'มี "{value}" อยู่แล้วแต่ถูกปิดใช้งานอยู่ — กรุณาเปิดใช้งานรายการเดิมแทนการสร้างรายการใหม่'
)


def _duplicate_master_data_detail(existing: MasterData | None, value: str) -> str:
    if existing is not None and not existing.active:
        return _MSG_DUPLICATE_INACTIVE.format(value=value)
    return _MSG_DUPLICATE_ACTIVE.format(value=value)


# Read accepts records.read OR records.create (round 5.6) — the RecordForm
# loads these options and /farmlog/records/new is opened with records.create.
# Mutations below stay gated by masterdata.* (unchanged).
@router.get("", response_model=list[MasterDataRead], dependencies=[
    Depends(require_any_permission(PermissionKey.RECORDS_READ, PermissionKey.RECORDS_CREATE))
])
async def list_master_data(
    db: AsyncSession = Depends(get_db),
    type: str | None = None,
    parent: str | None = None,
    active_only: bool = False,
) -> list[MasterDataRead]:
    items = await repo.list_items(db, type=type, parent=parent, active_only=active_only)
    return [MasterDataRead.model_validate(i) for i in items]


@router.post("", response_model=MasterDataRead, status_code=status.HTTP_201_CREATED,
             dependencies=[Depends(require_permission(PermissionKey.MASTERDATA_CREATE))])
async def create_master_data(
    payload: MasterDataCreate,
    db: AsyncSession = Depends(get_db),
) -> MasterDataRead:
    # repo.create() itself strips type/value before writing, so the
    # pre-check below compares against the SAME stripped value that would
    # actually be persisted.
    type_ = payload.type.strip()
    value = payload.value.strip()
    existing = await repo.get_by_type_value(db, type_, value)
    if existing is not None:
        raise HTTPException(status_code=409, detail=_duplicate_master_data_detail(existing, value))
    # Round 8-26A — a P.Code must name an existing variety, and that variety
    # must not already own an ACTIVE one. The UNIQUE(type, value) index above
    # covers "this P.Code value is free"; this covers "this variety is free".
    # New rows are always created active (repo.create never sets active), so
    # the rule always applies here — no effective-state resolution needed,
    # unlike update below.
    if type_ == p_code_master.P_CODE_TYPE:
        await p_code_master.assert_p_code_assignable(db, payload.parent)
    try:
        item = await repo.create(db, payload)
    except IntegrityError as exc:
        # Race: a concurrent insert of the same (type, value) landed between
        # the check above and this flush — the DB's unique index is the
        # real guard. Never echoes the driver's message.
        raise HTTPException(
            status_code=409, detail=_MSG_DUPLICATE_ACTIVE.format(value=value),
        ) from exc
    return MasterDataRead.model_validate(item)


@router.patch("/{item_id}", response_model=MasterDataRead, dependencies=[
    Depends(require_permission(PermissionKey.MASTERDATA_UPDATE))
])
async def update_master_data(
    item_id: UUID,
    payload: MasterDataUpdate,
    db: AsyncSession = Depends(get_db),
) -> MasterDataRead:
    item = await repo.get(db, item_id)
    if item is None:
        raise HTTPException(status_code=404, detail="master data not found")
    # `type` isn't part of MasterDataUpdate (immutable) — only a `value`
    # change can collide with another row of the same type. repo.update()
    # writes payload.value as-is (unstripped, unlike create) — compare
    # against that exact same value so this pre-check predicts what would
    # actually be written.
    if payload.value is not None and payload.value != item.value:
        existing = await repo.get_by_type_value(db, item.type, payload.value)
        if existing is not None and existing.id != item.id:
            raise HTTPException(
                status_code=409, detail=_duplicate_master_data_detail(existing, payload.value),
            )
    # Round 8-26A — same one-active-P.Code-per-variety rule as create, but
    # against the EFFECTIVE state this PATCH would leave behind: `parent` and
    # `active` are both optional and both mean "leave as-is" when omitted, so
    # each falls back to the stored row (model_fields_set, not a None check —
    # `parent: None` is a real value meaning "unassign"). Checked when the row
    # ENDS UP active: turning a P.Code off is always allowed (that is exactly
    # how a variety's P.Code gets replaced), and re-activating one is exactly
    # when a second active row could sneak in. exclude_id keeps a plain
    # re-save of the same row from colliding with itself.
    if item.type == p_code_master.P_CODE_TYPE:
        fields = payload.model_fields_set
        effective_parent = payload.parent if "parent" in fields else item.parent
        effective_active = payload.active if "active" in fields else item.active
        if effective_active:
            await p_code_master.assert_p_code_assignable(
                db, effective_parent, exclude_id=item.id,
            )
    try:
        item = await repo.update(db, item, payload)
    except IntegrityError as exc:
        raise HTTPException(
            status_code=409,
            detail=_MSG_DUPLICATE_ACTIVE.format(value=payload.value or item.value),
        ) from exc
    return MasterDataRead.model_validate(item)


@router.delete("/{item_id}", status_code=status.HTTP_204_NO_CONTENT, dependencies=[
    Depends(require_permission(PermissionKey.MASTERDATA_DELETE))
])
async def delete_master_data(
    item_id: UUID,
    db: AsyncSession = Depends(get_db),
) -> None:
    item = await repo.get(db, item_id)
    if item is None:
        raise HTTPException(status_code=404, detail="master data not found")
    await repo.delete(db, item)
