"""Suppliers CRUD — FarmLog master data.

Round 8-20A adds the Supplier Excel import (backend foundation only, no
frontend UI this round) — template/preview/commit plus read-only result-
workbook variants, all under `import/`. Registered BEFORE `/{supplier_id}` in
file order for the same reason plots.py orders its /import/ routes and
masterdata.py its crop-variety-import/ routes first: a static multi-segment
path can't collide with a single-segment `{supplier_id}` match anyway, but
keeping the static routes first matches the established convention.
"""
from __future__ import annotations

import datetime
from typing import Any
from uuid import UUID

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    Request,
    UploadFile,
    status,
)
from fastapi.responses import Response
from pydantic import ValidationError
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps.scope import SupplierScopeFilter
from app.auth.dependencies import CurrentUser, require_permission
from app.auth.permissions import PermissionKey
from app.db.session import get_db
from app.repositories import supplier_repository as repo
from app.schemas.supplier import (
    SupplierCreate,
    SupplierRead,
    SupplierSearchRequest,
    SupplierSummary,
    SupplierUpdate,
)
from app.schemas.supplier_import import (
    SupplierImportCommitResult,
    SupplierImportPreview,
    SupplierImportPreviewState,
)
from app.services import supplier_import
from app.services.loggers.activity_logger import ActivityLogger
from app.services.supplier_import_report import (
    PHASE_COMMIT,
    PHASE_PREVIEW,
    build_supplier_import_result_workbook,
    result_filename,
)

router = APIRouter(tags=["suppliers"])

# Same file-size cap Plot Import / Master Data Import enforce — an admin
# Supplier import is small.
_IMPORT_MAX_BYTES = 2 * 1024 * 1024

# previewState is a client-echoed JSON blob, one compact entry per row (code
# + operation + two flags + a 64-char digest, well under 200 bytes/row), so
# 1,000 rows is ~200 KB. Capped at the same 2 MB as the upload for one
# consistent story, with generous headroom. Checked BEFORE any JSON parsing.
_PREVIEW_STATE_MAX_BYTES = 2 * 1024 * 1024

_MSG_INVALID_PREVIEW_STATE = "previewState ไม่ถูกต้อง"


async def _read_import_upload(file: UploadFile) -> bytes:
    if not (file.filename or "").lower().endswith(".xlsx"):
        raise HTTPException(status_code=422, detail="รองรับเฉพาะไฟล์ .xlsx เท่านั้น")
    content = await file.read()
    if not content:
        raise HTTPException(status_code=422, detail="ไฟล์ว่างเปล่า")
    if len(content) > _IMPORT_MAX_BYTES:
        raise HTTPException(status_code=422, detail="ไฟล์ใหญ่เกินไป (สูงสุด 2 MB)")
    return content


def _parse_preview_state(raw: str | None) -> SupplierImportPreviewState | None:
    """Parse + bounds-check the optional multipart previewState field before
    it reaches the service layer. Absent/blank -> None (the service then
    raises a state conflict — every commit needs an approved preview).

    Every failure path raises the SAME generic 422 and never echoes the raw
    string or any submitted value back: previewState is not a credential, but
    it IS attacker-reachable input, so a rejection must reveal nothing about
    what was rejected or why. Mirrors plots.py/masterdata.py's equivalents —
    an input-boundary check only, never an authorization or state decision.
    """
    if raw is None or raw.strip() == "":
        return None
    if len(raw.encode("utf-8")) > _PREVIEW_STATE_MAX_BYTES:
        raise HTTPException(status_code=422, detail=_MSG_INVALID_PREVIEW_STATE)
    try:
        state = SupplierImportPreviewState.model_validate_json(raw)
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=_MSG_INVALID_PREVIEW_STATE) from exc
    if len(state.rows) > supplier_import.MAX_IMPORT_ROWS:
        raise HTTPException(status_code=422, detail=_MSG_INVALID_PREVIEW_STATE)
    row_numbers = [r.row_number for r in state.rows]
    if len(row_numbers) != len(set(row_numbers)):
        raise HTTPException(status_code=422, detail=_MSG_INVALID_PREVIEW_STATE)
    return state


def _row_permissions(user: Any) -> tuple[bool, bool]:
    """(can_create, can_update) from the caller's own effective permission
    set — the SAME set require_permission checks, read here rather than
    re-derived, because these two are per-ROW decisions the service makes and
    a route-level Depends cannot express.

    Every import route is already gated on suppliers.read; a caller without
    suppliers.create simply gets an ERROR on each create row (and likewise for
    update), which blocks the whole commit — it never silently skips them.
    """
    perms: set[str] = getattr(user, "_effective_permissions", set())
    return (
        PermissionKey.SUPPLIERS_CREATE in perms,
        PermissionKey.SUPPLIERS_UPDATE in perms,
    )


def _xlsx_response(content: bytes, filename: str) -> Response:
    return Response(
        content=content,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Cache-Control": "no-store",
        },
    )


# Round 8-20D — bounds for the contact-number fragment filter. The lower
# bound keeps a 1-3 digit fragment (which would match a large share of every
# number in scope) from being a usable enumeration probe; the upper bound is a
# full Thai mobile. Mirrors plots.py's _PHONE_SEARCH_MIN/MAX_DIGITS — kept as
# this module's own constants rather than imported, since the two searches are
# otherwise unrelated and must never be coupled.
_PHONE_SEARCH_MIN_DIGITS = 4
_PHONE_SEARCH_MAX_DIGITS = 10

_MSG_INVALID_PHONE_FILTER = "รูปแบบหมายเลขติดต่อไม่ถูกต้อง"


def _validated_phone_digits(raw: object) -> str | None:
    """Round 8-20D — hand-written check for the ONE PII filter on this
    endpoint. Blank/absent means "no phone filter" (not an error).

    contact_phone_digits is SkipValidation (see SupplierSearchRequest), so
    type AND shape are both checked here, by hand, and never round-trip
    through Pydantic's auto-422 — which would echo the submitted fragment
    back in `input`. Every rejection reason answers with the SAME fixed
    message: the caller cannot distinguish "too short" from "not a string"
    from "contains letters", and the message never contains the value.

    `isascii()` is required alongside `isdigit()`: str.isdigit() is True for
    non-ASCII digits (Arabic-Indic '٤', full-width '４', superscript '²'),
    none of which may reach a LIKE pattern.
    """
    if raw is None:
        return None
    if not isinstance(raw, str):
        raise HTTPException(status_code=422, detail=_MSG_INVALID_PHONE_FILTER)
    fragment = raw.strip()
    if not fragment:
        return None
    if (
        not fragment.isascii()
        or not fragment.isdigit()
        or not (_PHONE_SEARCH_MIN_DIGITS <= len(fragment) <= _PHONE_SEARCH_MAX_DIGITS)
    ):
        raise HTTPException(status_code=422, detail=_MSG_INVALID_PHONE_FILTER)
    return fragment


@router.post("/search", response_model=list[SupplierSummary], dependencies=[
    Depends(require_permission(PermissionKey.SUPPLIERS_READ)),
])
async def search_suppliers(
    payload: SupplierSearchRequest,
    response: Response,
    scope: SupplierScopeFilter,
    db: AsyncSession = Depends(get_db),
) -> list[SupplierSummary]:
    """Round 8-20D — the Suppliers page's filter row: name/code, contact name,
    contact-number fragment, and status, all ANDed.

    POST-and-body-only BY DESIGN, for the contact number: a phone fragment in
    a GET query string lands verbatim in Uvicorn's access log on every
    request. Same reasoning (and the same shape) as plots.py's
    search-by-phone. GET /suppliers is untouched and still serves every
    pre-8-20D caller.

    Same suppliers.read + SupplierScopeFilter wiring as GET /suppliers — no
    scope widening; a Supplier-scoped caller still sees only their own.
    Response is the identical SupplierSummary shape, so the page renders both
    result sets with the same table.

    `Cache-Control: no-store` — a result set derived from someone's contact
    number must never be served from a shared/browser cache to a different
    caller.
    """
    response.headers["Cache-Control"] = "no-store"
    # Validated BEFORE any DB work: a rejected fragment must never reach a
    # query, so an invalid filter costs zero database round-trips.
    phone_digits = _validated_phone_digits(payload.contact_phone_digits)
    suppliers = await repo.search_suppliers(
        db,
        scope_conditions=scope,
        q=payload.q,
        contact_name=payload.contact_name,
        contact_phone_digits=phone_digits,
        status=payload.status,
        limit=payload.limit,
        offset=payload.offset,
    )
    return [SupplierSummary.model_validate(s) for s in suppliers]


@router.get("/import/template", dependencies=[
    Depends(require_permission(PermissionKey.SUPPLIERS_READ)),
])
async def download_supplier_import_template(
    scope: SupplierScopeFilter,
    db: AsyncSession = Depends(get_db),
) -> Response:
    """Read-only — never writes to the DB. Pre-filled with the caller's own
    in-scope Suppliers (active and inactive alike, so either can be edited or
    flipped through the file). See services/supplier_import.build_template."""
    content = await supplier_import.build_template(db, scope_conditions=scope)
    return _xlsx_response(content, "supplier-import-template.xlsx")


@router.post("/import/preview", response_model=SupplierImportPreview, dependencies=[
    Depends(require_permission(PermissionKey.SUPPLIERS_READ)),
])
async def preview_supplier_import(
    user: CurrentUser,
    scope: SupplierScopeFilter,
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
) -> SupplierImportPreview:
    """Parse + validate every row WITHOUT writing anything. Safe/read-only.

    A row whose operation the caller lacks the permission for comes back as an
    ERROR row (which, like any error, blocks commit) — the preview itself is
    still allowed to run so the user can see the whole file.
    """
    content = await _read_import_upload(file)
    can_create, can_update = _row_permissions(user)
    try:
        return await supplier_import.build_preview(
            db, content, scope_conditions=scope,
            can_create=can_create, can_update=can_update,
        )
    except supplier_import.SupplierImportFileError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/import/commit", response_model=SupplierImportCommitResult, dependencies=[
    Depends(require_permission(PermissionKey.SUPPLIERS_READ)),
])
async def commit_supplier_import(
    request: Request,
    user: CurrentUser,
    scope: SupplierScopeFilter,
    file: UploadFile = File(...),
    # Explicit alias — every other API field on the wire is camelCase, and a
    # client sends the multipart field as "previewState" (same convention as
    # plots.py/masterdata.py's own commit endpoints).
    preview_state: str | None = Form(None, alias="previewState"),
    db: AsyncSession = Depends(get_db),
) -> SupplierImportCommitResult:
    """Re-validate server-side (never trusting a client preview) and execute
    every row in ONE transaction — this endpoint's single get_db session; the
    service only ever flushes, and never commits inside its row loop.

    Any invalid row -> 422 with the full preview, nothing written. A stale
    file/row-set/Supplier state -> 409, nothing written. A unique-constraint
    race -> 409, nothing written. Any other exception propagates so get_db
    rolls the whole transaction back.
    """
    content = await _read_import_upload(file)
    parsed_state = _parse_preview_state(preview_state)
    can_create, can_update = _row_permissions(user)
    try:
        result = await supplier_import.commit(
            db, content, preview_state=parsed_state, scope_conditions=scope,
            can_create=can_create, can_update=can_update,
        )
    except supplier_import.SupplierImportHasErrors as exc:
        raise HTTPException(
            status_code=422,
            detail={
                "message": "พบข้อผิดพลาดในบางแถว — ไม่ได้บันทึกข้อมูลใด ๆ",
                "preview": exc.preview.model_dump(by_alias=True, mode="json"),
            },
        ) from exc
    except supplier_import.SupplierImportStateConflict as exc:
        raise HTTPException(status_code=409, detail=exc.message) from exc
    except supplier_import.SupplierImportFileError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except IntegrityError as exc:
        # A concurrent insert of the same code between our existence check and
        # the flush — the DB's unique index is the real guard. Never echoes the
        # driver's message (it can carry the offending value and the SQL).
        raise HTTPException(
            status_code=409,
            detail="นำเข้าไม่สำเร็จเนื่องจากมีการเปลี่ยนแปลงที่ขัดแย้ง — ไม่ได้บันทึกข้อมูลใด ๆ",
        ) from exc

    await ActivityLogger(db).log(
        action="suppliers.import.commit",
        action_type="update",
        resource_type="supplier",
        user=user,
        request=request,
        risk_level="medium",
        metadata={
            "totalRows": result.total_rows,
            "createdSuppliers": result.created_suppliers,
            "updatedSuppliers": result.updated_suppliers,
            "activatedSuppliers": result.activated_suppliers,
            "deactivatedSuppliers": result.deactivated_suppliers,
            "unchangedRows": result.unchanged_rows,
        },
    )
    return result


@router.post("/import/preview-report", dependencies=[
    Depends(require_permission(PermissionKey.SUPPLIERS_READ)),
])
async def preview_supplier_import_report(
    user: CurrentUser,
    scope: SupplierScopeFilter,
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
) -> Response:
    """Read-only: the same core as .../import/preview, returned as a
    validation workbook (READY/ERROR per row) instead of JSON. Never writes."""
    content = await _read_import_upload(file)
    can_create, can_update = _row_permissions(user)
    try:
        summary, row_views = await supplier_import.preview_row_views(
            db, content, scope_conditions=scope,
            can_create=can_create, can_update=can_update,
        )
    except supplier_import.SupplierImportFileError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    processed_at = datetime.datetime.now(datetime.timezone.utc)
    workbook = build_supplier_import_result_workbook(
        row_views, phase=PHASE_PREVIEW, completed=False,
        original_filename=file.filename, processed_at=processed_at,
        suppliers_to_create=summary.suppliers_to_create,
        suppliers_to_update=summary.suppliers_to_update,
        suppliers_to_activate=summary.suppliers_to_activate,
        suppliers_to_deactivate=summary.suppliers_to_deactivate,
        unchanged_rows=summary.unchanged_rows,
    )
    return _xlsx_response(workbook, result_filename(PHASE_PREVIEW, processed_at))


@router.post("/import/commit-report", dependencies=[
    Depends(require_permission(PermissionKey.SUPPLIERS_READ)),
])
async def commit_supplier_import_report(
    request: Request,
    user: CurrentUser,
    scope: SupplierScopeFilter,
    file: UploadFile = File(...),
    preview_state: str | None = Form(None, alias="previewState"),
    db: AsyncSession = Depends(get_db),
) -> Response:
    """Same core as .../import/commit (re-validates, checks drift, executes
    ONCE in this endpoint's single transaction), returned as a completed-
    result workbook (COMPLETED per row) instead of JSON."""
    content = await _read_import_upload(file)
    parsed_state = _parse_preview_state(preview_state)
    can_create, can_update = _row_permissions(user)
    try:
        result, row_views = await supplier_import.commit_row_views(
            db, content, preview_state=parsed_state, scope_conditions=scope,
            can_create=can_create, can_update=can_update,
        )
    except supplier_import.SupplierImportHasErrors as exc:
        raise HTTPException(
            status_code=422,
            detail={
                "message": "พบข้อผิดพลาดในบางแถว — ไม่ได้บันทึกข้อมูลใด ๆ",
                "preview": exc.preview.model_dump(by_alias=True, mode="json"),
            },
        ) from exc
    except supplier_import.SupplierImportStateConflict as exc:
        raise HTTPException(status_code=409, detail=exc.message) from exc
    except supplier_import.SupplierImportFileError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except IntegrityError as exc:
        raise HTTPException(
            status_code=409,
            detail="นำเข้าไม่สำเร็จเนื่องจากมีการเปลี่ยนแปลงที่ขัดแย้ง — ไม่ได้บันทึกข้อมูลใด ๆ",
        ) from exc

    await ActivityLogger(db).log(
        action="suppliers.import.commit",
        action_type="update",
        resource_type="supplier",
        user=user,
        request=request,
        risk_level="medium",
        metadata={
            "totalRows": result.total_rows,
            "createdSuppliers": result.created_suppliers,
            "updatedSuppliers": result.updated_suppliers,
            "activatedSuppliers": result.activated_suppliers,
            "deactivatedSuppliers": result.deactivated_suppliers,
            "unchangedRows": result.unchanged_rows,
        },
    )
    processed_at = datetime.datetime.now(datetime.timezone.utc)
    workbook = build_supplier_import_result_workbook(
        row_views, phase=PHASE_COMMIT, completed=True,
        original_filename=file.filename, processed_at=processed_at,
        suppliers_to_create=result.created_suppliers,
        suppliers_to_update=result.updated_suppliers,
        suppliers_to_activate=result.activated_suppliers,
        suppliers_to_deactivate=result.deactivated_suppliers,
        unchanged_rows=result.unchanged_rows,
    )
    return _xlsx_response(workbook, result_filename(PHASE_COMMIT, processed_at))

@router.get("", response_model=list[SupplierSummary], dependencies=[
    Depends(require_permission(PermissionKey.SUPPLIERS_READ))
])
async def list_suppliers(
    scope: SupplierScopeFilter,
    db: AsyncSession = Depends(get_db),
    limit: int = 50,
    offset: int = 0,
    q: str | None = None,
    active_only: bool = False,
) -> list[SupplierSummary]:
    suppliers = await repo.list_suppliers(
        db, scope_conditions=scope, limit=limit, offset=offset, q=q, active_only=active_only
    )
    return [SupplierSummary.model_validate(s) for s in suppliers]


@router.post("", response_model=SupplierRead, status_code=status.HTTP_201_CREATED,
             dependencies=[Depends(require_permission(PermissionKey.SUPPLIERS_CREATE))])
async def create_supplier(
    payload: SupplierCreate,
    db: AsyncSession = Depends(get_db),
) -> SupplierRead:
    existing = await repo.get_supplier_by_code(db, payload.code)
    if existing is not None:
        raise HTTPException(status_code=409, detail="Supplier code already exists")
    supplier = await repo.create_supplier(db, payload)
    return SupplierRead.model_validate(supplier)


@router.get("/{supplier_id}", response_model=SupplierRead, dependencies=[
    Depends(require_permission(PermissionKey.SUPPLIERS_READ))
])
async def get_supplier(
    supplier_id: UUID,
    scope: SupplierScopeFilter,
    db: AsyncSession = Depends(get_db),
) -> SupplierRead:
    supplier = await repo.get_supplier_scoped(db, supplier_id, scope)
    if supplier is None:
        raise HTTPException(status_code=404, detail="Supplier not found")
    return SupplierRead.model_validate(supplier)


@router.patch("/{supplier_id}", response_model=SupplierRead, dependencies=[
    Depends(require_permission(PermissionKey.SUPPLIERS_UPDATE))
])
async def update_supplier(
    supplier_id: UUID,
    payload: SupplierUpdate,
    db: AsyncSession = Depends(get_db),
) -> SupplierRead:
    supplier = await repo.get_supplier(db, supplier_id)
    if supplier is None:
        raise HTTPException(status_code=404, detail="Supplier not found")
    supplier = await repo.update_supplier(db, supplier, payload)
    return SupplierRead.model_validate(supplier)


@router.post("/{supplier_id}/deactivate", response_model=SupplierRead, dependencies=[
    Depends(require_permission(PermissionKey.SUPPLIERS_DELETE))
])
async def deactivate_supplier(
    supplier_id: UUID,
    db: AsyncSession = Depends(get_db),
) -> SupplierRead:
    supplier = await repo.get_supplier(db, supplier_id)
    if supplier is None:
        raise HTTPException(status_code=404, detail="Supplier not found")
    supplier = await repo.update_supplier(db, supplier, SupplierUpdate(is_active=False))
    return SupplierRead.model_validate(supplier)
