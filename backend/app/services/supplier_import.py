"""Supplier Excel import (round 8-20A) — template, parse, validate, commit.
Backend foundation only; no frontend UI this round.

Single worksheet "ข้อมูล Supplier", 9 columns. Row 1 = header, row 2 = a Thai
description row the parser skips (same TEMPLATE_DESCRIPTION_MARKER convention
as services/plot_import.py and services/master_data_crop_variety_import.py),
row 3+ = data. No spreadsheet-parser dependency — rows come from services/
excel_reader.py's hand-rolled reader and the template is written with
services/excel_workbook.py's hand-rolled writer. This module reimplements
NEITHER; it is the third caller of both.

Business rules:

  action        — exactly one accepted value, ACTION_SAVE ("save_supplier").
                  Anything else is a row error. There is deliberately no
                  delete/deactivate action: a Supplier is never hard-deleted
                  by this importer, and closing one is expressed as
                  status=inactive on its own save row.

  supplierCode  — required, trimmed, max 50. This is the IDENTITY: an
                  existing code updates that Supplier, an unknown code
                  creates one. A code is never changed by this importer, so
                  there is no "rename" path and no row can re-point one
                  Supplier's data at another. Matched case-insensitively
                  (repo.get_supplier_by_code lowers both sides), matching the
                  app's own create-time duplicate check.

  supplierName  — required, trimmed, max 255.
  taxId         — optional, max 20.
  contactName   — optional, max 255.
  contactEmail  — optional, max 255; must look like an email when non-blank.
  contactPhone  — optional, max 50.
  address       — optional, unbounded (Text column).
  status        — required, exactly "active" or "inactive".

  A blank optional cell on an UPDATE row CLEARS the stored value (writes
  NULL). That is the documented contract for this template: what you see in
  the sheet is what the Supplier will look like afterwards — there is no
  "leave blank to keep" mode, because that would make it impossible to clear
  a field through the file at all.

  Deactivating a Supplier (status active → inactive) sets is_active=False and
  NOTHING else. It deliberately does NOT cascade: the Supplier's Plots,
  PlotCycles and Records are untouched — this module imports none of those
  models and issues no write against them. Preview attaches a plain-language
  warning saying exactly that.

Permissions are per ROW, not per file: a create row needs suppliers.create, an
update/status row needs suppliers.update. The caller passes the two booleans
it resolved from the request's own permission set; a row whose operation the
caller may not perform is an ERROR row, which (like any error) blocks the
whole commit.

Scope: a Supplier outside the caller's scope is never created, updated, or
described. `scope_conditions` are the SAME app-layer conditions
app/api/deps/scope.py::get_supplier_scope_filter builds for the list/get
endpoints — reused, never re-derived here.

Preview never flushes/commits/updates/deletes anything — pure read + compute.
Commit re-parses and re-validates the SAME uploaded file server-side (never
trusting the client's JSON preview) and re-checks live Supplier state; ANY
drift from what Preview showed raises SupplierImportStateConflict before a
single row executes. All-or-nothing: every write here is flush-only (via
app.repositories.supplier_repository) — the caller's single `get_db` session
transaction is the only thing that ever commits or rolls back, and there is
no commit inside the row loop.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.supplier import Supplier
from app.repositories import supplier_repository as repo
from app.schemas.supplier import SupplierCreate, SupplierUpdate
from app.schemas.supplier_import import (
    SupplierImportCommitResult,
    SupplierImportPreview,
    SupplierImportPreviewState,
    SupplierImportPreviewStateRow,
    SupplierImportProcessedRow,
    SupplierImportRowResult,
    SupplierImportSummary,
)
from app.services.excel_reader import ExcelParseError, read_first_sheet

# --- Sheet/column contract -------------------------------------------------
SHEET_NAME = "ข้อมูล Supplier"
EXAMPLE_SHEET_NAME = "ตัวอย่าง"

IMPORT_COLUMNS: list[str] = [
    "action",
    "supplierCode",
    "supplierName",
    "taxId",
    "contactName",
    "contactEmail",
    "contactPhone",
    "address",
    "status",
]

# Columns the parser insists on finding in row 1. `address` is the only
# optional-to-omit column, because a workbook round-tripped through some
# editors can drop a trailing all-blank column — everything else is required
# for a row to even be interpretable.
REQUIRED_HEADERS: list[str] = [c for c in IMPORT_COLUMNS if c != "address"]

MAX_IMPORT_ROWS = 1000  # same cap Plot Import / Master Data Import use

# Each cap mirrors the matching suppliers column in app/db/models/supplier.py,
# so an over-length value is rejected as a clean row error before the DB ever
# sees it. `address` is a Text column — no cap.
MAX_CODE_LEN = 50
MAX_NAME_LEN = 255
MAX_TAX_ID_LEN = 20
MAX_CONTACT_NAME_LEN = 255
MAX_CONTACT_EMAIL_LEN = 255
MAX_CONTACT_PHONE_LEN = 50

ACTION_SAVE = "save_supplier"

STATUS_ACTIVE = "active"
STATUS_INACTIVE = "inactive"
STATUS_VALUES: tuple[str, str] = (STATUS_ACTIVE, STATUS_INACTIVE)

# --- Operation vocabulary (machine-readable, never parsed from Thai) -------
OPERATION_CREATE = "create"
OPERATION_UPDATE = "update"
OPERATION_NO_CHANGE = "no_change"

ROW_STATUS_READY = "READY"
ROW_STATUS_ERROR = "ERROR"

# Row 2 of the shipped template is human-readable Thai guidance, not data —
# its `action` cell starts with this exact marker so the parser can detect and
# skip exactly that row (same convention as plot_import /
# master_data_crop_variety_import; kept as this module's own constant rather
# than importing theirs, since the importers are otherwise unrelated).
TEMPLATE_DESCRIPTION_MARKER = "คำอธิบาย (ระบบไม่นำเข้าแถวนี้)"

TEMPLATE_COLUMN_DESCRIPTIONS: dict[str, str] = {
    "action": (
        TEMPLATE_DESCRIPTION_MARKER + f" — ใส่ '{ACTION_SAVE}' ทุกแถว "
        "(ระบบไม่มีคำสั่งลบ Supplier)"
    ),
    "supplierCode": (
        "รหัส Supplier (จำเป็น) ห้ามเปลี่ยน — รหัสที่มีอยู่แล้ว = แก้ไข, "
        "รหัสใหม่ = สร้างใหม่"
    ),
    "supplierName": "ชื่อ Supplier (จำเป็น)",
    "taxId": "เลขประจำตัวผู้เสียภาษี (ไม่บังคับ) — เว้นว่างในแถวแก้ไข = ล้างค่าเดิม",
    "contactName": "ชื่อผู้ติดต่อ (ไม่บังคับ) — เว้นว่างในแถวแก้ไข = ล้างค่าเดิม",
    "contactEmail": "อีเมลผู้ติดต่อ (ไม่บังคับ) — เว้นว่างในแถวแก้ไข = ล้างค่าเดิม",
    "contactPhone": "เบอร์โทรผู้ติดต่อ (ไม่บังคับ) — เว้นว่างในแถวแก้ไข = ล้างค่าเดิม",
    "address": "ที่อยู่ (ไม่บังคับ) — เว้นว่างในแถวแก้ไข = ล้างค่าเดิม",
    "status": (
        f"สถานะ (จำเป็น): '{STATUS_ACTIVE}' หรือ '{STATUS_INACTIVE}' เท่านั้น — "
        "ปิดใช้งาน Supplier ไม่ลบแปลง/รอบปลูก/ประวัติการตรวจ"
    ),
}

# The Preview-time reassurance for a row that closes a Supplier. Deliberately
# a WARNING (never an error): deactivating is a legitimate, supported action.
DEACTIVATE_WARNING = (
    "Supplier จะถูกปิดใช้งาน แต่ข้อมูลแปลง รอบปลูก และประวัติการตรวจจะไม่ถูกลบ"
)

# Pragmatic email shape check — one @, a non-empty local part, a dotted
# domain, no whitespace. Deliberately NOT a full RFC 5322 parser and
# deliberately not a new dependency (`email-validator`): this is an input
# sanity check for a spreadsheet cell, and the column is plain contact text,
# never an authentication identity.
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s.]+(\.[^@\s.]+)+$")


class SupplierImportFileError(ValueError):
    """The uploaded file isn't a usable Supplier import workbook (unreadable,
    wrong/missing headers, or too many rows)."""


class SupplierImportHasErrors(Exception):
    """Commit refused: at least one row failed validation. Carries the full
    preview so the caller can show exactly which rows and why. NOTHING was
    written."""

    def __init__(self, preview: SupplierImportPreview) -> None:
        super().__init__("supplier import has row errors")
        self.preview = preview


class SupplierImportStateConflict(Exception):
    """Commit refused: the file, the row set, or a Supplier's stored data
    changed after the Preview the caller approved. NOTHING was written — the
    caller must Preview again."""

    message = "ข้อมูลเปลี่ยนแปลงหลังจากตรวจสอบ กรุณาตรวจสอบไฟล์ใหม่อีกครั้ง"


def file_digest(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def supplier_state_digest(supplier: Supplier) -> str:
    """Compact fingerprint of a Supplier's material field values, used as the
    optimistic-concurrency binding in previewState (see the schema's
    docstring for why a digest rather than the values themselves). Covers
    every field this importer can write, so any concurrent edit to one of
    them — through Excel or the ordinary admin UI — is detected at Commit.
    `code` is included even though it is the lookup key: it costs nothing and
    makes the digest a complete description of the row's target."""
    parts = [
        supplier.code or "",
        supplier.name or "",
        supplier.tax_id or "",
        supplier.contact_name or "",
        supplier.contact_email or "",
        supplier.contact_phone or "",
        supplier.address or "",
        "1" if supplier.is_active else "0",
    ]
    # \x1f (unit separator) can't occur in these values, so the join is
    # unambiguous — "a" + "" can never collide with "" + "a".
    return hashlib.sha256("\x1f".join(parts).encode("utf-8")).hexdigest()


# --- Row parsing -----------------------------------------------------------


def _cell(raw: dict[str, str], key: str) -> str:
    return (raw.get(key) or "").strip()


def _optional(raw: dict[str, str], key: str) -> str | None:
    value = _cell(raw, key)
    return value or None


def _is_template_description_row(raw: dict[str, str]) -> bool:
    return _cell(raw, "action").startswith(TEMPLATE_DESCRIPTION_MARKER)


@dataclass
class _Parsed:
    action: str
    code: str
    name: str
    tax_id: str | None
    contact_name: str | None
    contact_email: str | None
    contact_phone: str | None
    address: str | None
    status: str


@dataclass
class _RowState:
    row_number: int
    raw: dict[str, str]
    parsed: _Parsed
    operation: str = OPERATION_NO_CHANGE
    error: str = ""
    warning: str = ""
    existing: Supplier | None = None
    existed: bool = False
    was_active: bool | None = None
    existing_digest: str | None = None

    @property
    def is_error(self) -> bool:
        return bool(self.error)


def _parse_row(raw: dict[str, str]) -> _Parsed:
    return _Parsed(
        action=_cell(raw, "action"),
        code=_cell(raw, "supplierCode"),
        name=_cell(raw, "supplierName"),
        tax_id=_optional(raw, "taxId"),
        contact_name=_optional(raw, "contactName"),
        contact_email=_optional(raw, "contactEmail"),
        contact_phone=_optional(raw, "contactPhone"),
        address=_optional(raw, "address"),
        status=_cell(raw, "status"),
    )


def _validate_fields(p: _Parsed) -> str:
    """First failing field's message, or "" when the row's own cells are all
    acceptable. Order is deliberate — action, then identity, then the rest —
    so a row with several problems always reports the most fundamental one."""
    if p.action != ACTION_SAVE:
        return f"action ต้องเป็น '{ACTION_SAVE}' เท่านั้น"
    if not p.code:
        return "supplierCode จำเป็นต้องระบุ"
    if len(p.code) > MAX_CODE_LEN:
        return f"supplierCode ยาวเกิน {MAX_CODE_LEN} ตัวอักษร"
    if not p.name:
        return "supplierName จำเป็นต้องระบุ"
    if len(p.name) > MAX_NAME_LEN:
        return f"supplierName ยาวเกิน {MAX_NAME_LEN} ตัวอักษร"
    if p.tax_id is not None and len(p.tax_id) > MAX_TAX_ID_LEN:
        return f"taxId ยาวเกิน {MAX_TAX_ID_LEN} ตัวอักษร"
    if p.contact_name is not None and len(p.contact_name) > MAX_CONTACT_NAME_LEN:
        return f"contactName ยาวเกิน {MAX_CONTACT_NAME_LEN} ตัวอักษร"
    if p.contact_email is not None:
        if len(p.contact_email) > MAX_CONTACT_EMAIL_LEN:
            return f"contactEmail ยาวเกิน {MAX_CONTACT_EMAIL_LEN} ตัวอักษร"
        if not _EMAIL_RE.match(p.contact_email):
            return "contactEmail ไม่ใช่รูปแบบอีเมลที่ถูกต้อง"
    if p.contact_phone is not None and len(p.contact_phone) > MAX_CONTACT_PHONE_LEN:
        return f"contactPhone ยาวเกิน {MAX_CONTACT_PHONE_LEN} ตัวอักษร"
    if not p.status:
        return "status จำเป็นต้องระบุ"
    if p.status not in STATUS_VALUES:
        return f"status ต้องเป็น '{STATUS_ACTIVE}' หรือ '{STATUS_INACTIVE}' เท่านั้น"
    return ""


def _is_unchanged(existing: Supplier, p: _Parsed) -> bool:
    """True when applying this row would write nothing. Compares every field
    the importer can set, INCLUDING the blank-clears-value semantics (a blank
    cell is None here, so "had a value, cell now blank" correctly reads as a
    change)."""
    return (
        existing.name == p.name
        and existing.tax_id == p.tax_id
        and existing.contact_name == p.contact_name
        and existing.contact_email == p.contact_email
        and existing.contact_phone == p.contact_phone
        and existing.address == p.address
        and existing.is_active == (p.status == STATUS_ACTIVE)
    )


async def _load_data_rows(content: bytes) -> list[tuple[int, dict[str, str]]]:
    """Rows 3+ of the FIRST sheet only.

    The importer reads the first worksheet (services/excel_reader.py's
    read_first_sheet) and build_template always writes "ข้อมูล Supplier"
    first — so the red "ตัวอย่าง" sheet is never parsed, by construction
    rather than by name-matching.
    """
    try:
        headers, rows = read_first_sheet(content)
    except ExcelParseError as exc:
        raise SupplierImportFileError(str(exc)) from exc

    missing = [h for h in REQUIRED_HEADERS if h not in headers]
    if missing:
        raise SupplierImportFileError(
            "ไฟล์ไม่ถูกต้อง: ไม่พบคอลัมน์ " + ", ".join(missing)
        )

    data_rows = [(n, raw) for (n, raw) in rows if not _is_template_description_row(raw)]
    if len(data_rows) > MAX_IMPORT_ROWS:
        raise SupplierImportFileError(
            f"ไฟล์มีข้อมูลเกิน {MAX_IMPORT_ROWS:,} แถว กรุณาแบ่งไฟล์ให้เล็กลง"
        )
    return data_rows


async def _build_row_states(
    db: AsyncSession,
    data_rows: list[tuple[int, dict[str, str]]],
    *,
    scope_conditions: list[Any],
    can_create: bool,
    can_update: bool,
) -> list[_RowState]:
    """Parse + validate + resolve each row against live Supplier state.

    Read-only: every DB access here is a SELECT. Suppliers are loaded ONCE
    for the whole file (a single scoped query keyed by the file's distinct
    codes) — never one query per row.
    """
    states: list[_RowState] = []
    for row_number, raw in data_rows:
        parsed = _parse_row(raw)
        states.append(_RowState(row_number=row_number, raw=raw, parsed=parsed))

    # --- field-level validation ------------------------------------------
    for state in states:
        state.error = _validate_fields(state.parsed)

    # --- duplicate supplierCode WITHIN the file (case-insensitive) --------
    # Every affected row is flagged, never silently merged or last-one-wins.
    seen: dict[str, int] = {}
    duplicated: set[str] = set()
    for state in states:
        if state.is_error or not state.parsed.code:
            continue
        key = state.parsed.code.lower()
        if key in seen:
            duplicated.add(key)
        else:
            seen[key] = state.row_number
    for state in states:
        if state.is_error or not state.parsed.code:
            continue
        if state.parsed.code.lower() in duplicated:
            state.error = "supplierCode ซ้ำกันในไฟล์ (ไม่สนตัวพิมพ์เล็ก/ใหญ่)"

    # --- resolve existing suppliers: ONE query for the whole file ---------
    codes = {s.parsed.code.lower() for s in states if not s.is_error and s.parsed.code}
    existing_by_code = await repo.get_suppliers_by_codes(db, sorted(codes))
    in_scope_ids = (
        await repo.filter_supplier_ids_in_scope(
            db, [s.id for s in existing_by_code.values()], scope_conditions
        )
        if existing_by_code
        else set()
    )

    for state in states:
        if state.is_error:
            continue
        existing = existing_by_code.get(state.parsed.code.lower())
        if existing is None:
            state.operation = OPERATION_CREATE
            state.existed = False
            if not can_create:
                state.error = "ไม่มีสิทธิ์สร้าง Supplier ใหม่ (ต้องมีสิทธิ์ suppliers.create)"
            continue

        # An existing Supplier the caller cannot see is neither described nor
        # written. Generic message — it never reports which fields it holds.
        if existing.id not in in_scope_ids:
            state.error = "ไม่มีสิทธิ์เข้าถึง Supplier รหัสนี้"
            continue

        state.existing = existing
        state.existed = True
        state.was_active = existing.is_active
        state.existing_digest = supplier_state_digest(existing)
        if _is_unchanged(existing, state.parsed):
            state.operation = OPERATION_NO_CHANGE
            continue
        state.operation = OPERATION_UPDATE
        if not can_update:
            state.error = "ไม่มีสิทธิ์แก้ไข Supplier (ต้องมีสิทธิ์ suppliers.update)"
            continue
        if existing.is_active and state.parsed.status == STATUS_INACTIVE:
            state.warning = DEACTIVATE_WARNING

    return states


# --- Preview / result shaping ----------------------------------------------


def _row_result(state: _RowState) -> SupplierImportRowResult:
    p = state.parsed
    return SupplierImportRowResult(
        row_number=state.row_number,
        action=p.action or None,
        supplier_code=p.code or None,
        supplier_name=p.name or None,
        tax_id=p.tax_id,
        contact_name=p.contact_name,
        contact_email=p.contact_email,
        contact_phone=p.contact_phone,
        address=p.address,
        status=p.status or None,
        row_status=ROW_STATUS_ERROR if state.is_error else ROW_STATUS_READY,
        operation=state.operation,
        error_message=state.error,
        warning_message=state.warning,
    )


def _summarize(states: list[_RowState]) -> SupplierImportSummary:
    error_rows = sum(1 for s in states if s.is_error)
    ok = [s for s in states if not s.is_error]
    return SupplierImportSummary(
        total_rows=len(states),
        ready_rows=len(ok),
        error_rows=error_rows,
        suppliers_to_create=sum(1 for s in ok if s.operation == OPERATION_CREATE),
        suppliers_to_update=sum(1 for s in ok if s.operation == OPERATION_UPDATE),
        suppliers_to_activate=sum(
            1 for s in ok
            if s.operation == OPERATION_UPDATE
            and s.was_active is False and s.parsed.status == STATUS_ACTIVE
        ),
        suppliers_to_deactivate=sum(
            1 for s in ok
            if s.operation == OPERATION_UPDATE
            and s.was_active is True and s.parsed.status == STATUS_INACTIVE
        ),
        unchanged_rows=sum(1 for s in ok if s.operation == OPERATION_NO_CHANGE),
    )


def _to_preview_state_row(state: _RowState) -> SupplierImportPreviewStateRow:
    return SupplierImportPreviewStateRow(
        row_number=state.row_number,
        supplier_code=state.parsed.code,
        operation=state.operation,
        supplier_existed=state.existed,
        supplier_was_active=state.was_active,
        existing_state_digest=state.existing_digest,
    )


def _to_preview(
    states: list[_RowState], *, include_state: bool, digest: str | None,
) -> SupplierImportPreview:
    preview_state = None
    if include_state and digest is not None:
        preview_state = SupplierImportPreviewState(
            file_sha256=digest,
            # Error rows carry no usable plan (supplier_code may be blank, and
            # the schema requires it) — only well-formed rows are bound.
            rows=[_to_preview_state_row(s) for s in states if not s.is_error],
        )
    return SupplierImportPreview(
        summary=_summarize(states),
        rows=[_row_result(s) for s in states],
        preview_state=preview_state,
    )


# --- Public entry points ---------------------------------------------------


async def build_preview(
    db: AsyncSession,
    content: bytes,
    *,
    scope_conditions: list[Any],
    can_create: bool,
    can_update: bool,
) -> SupplierImportPreview:
    """Parse + validate every row WITHOUT writing anything. Safe/read-only —
    no flush, no commit, no update, no delete anywhere in this call path."""
    data_rows = await _load_data_rows(content)
    states = await _build_row_states(
        db, data_rows, scope_conditions=scope_conditions,
        can_create=can_create, can_update=can_update,
    )
    return _to_preview(states, include_state=True, digest=file_digest(content))


def _row_states_match_preview(
    states: list[_RowState], expected: SupplierImportPreviewState,
) -> bool:
    bindable = [s for s in states if not s.is_error]
    if len(bindable) != len(expected.rows):
        return False
    expected_by_number = {r.row_number: r for r in expected.rows}
    for state in bindable:
        exp = expected_by_number.get(state.row_number)
        if exp is None or _to_preview_state_row(state) != exp:
            return False
    return True


async def _commit_execute(
    db: AsyncSession,
    content: bytes,
    *,
    preview_state: SupplierImportPreviewState | None,
    scope_conditions: list[Any],
    can_create: bool,
    can_update: bool,
) -> tuple[SupplierImportCommitResult, list[_RowState]]:
    """Re-validate server-side (never trusting the client's preview) and
    execute every row in the caller's SINGLE `db` session — this function only
    ever flushes; the caller's transaction is the one that commits or rolls
    back, and there is no commit inside the row loop.

    Any invalid row raises SupplierImportHasErrors with nothing written. Any
    drift from the approved preview (the file, the row set, or a Supplier's
    stored data) raises SupplierImportStateConflict before a single row runs.

    Returns the row states as they were BEFORE execution — the correct plan
    for reporting "what this commit did", never re-derived afterwards (which
    would see the just-written rows as already matching and misreport every
    one of them as no_change).
    """
    digest = file_digest(content)
    data_rows = await _load_data_rows(content)
    states = await _build_row_states(
        db, data_rows, scope_conditions=scope_conditions,
        can_create=can_create, can_update=can_update,
    )

    if preview_state is None or preview_state.file_sha256 != digest:
        raise SupplierImportStateConflict()
    if not _row_states_match_preview(states, preview_state):
        raise SupplierImportStateConflict()

    preview = _to_preview(states, include_state=False, digest=None)
    if preview.summary.error_rows:
        raise SupplierImportHasErrors(preview)

    created = updated = activated = deactivated = 0
    processed: list[SupplierImportProcessedRow] = []

    for state in states:
        p = state.parsed
        if state.operation == OPERATION_CREATE:
            supplier = await repo.create_supplier(
                db,
                SupplierCreate(
                    code=p.code, name=p.name, taxId=p.tax_id,
                    contactName=p.contact_name, contactEmail=p.contact_email,
                    contactPhone=p.contact_phone, address=p.address,
                ),
            )
            # SupplierCreate has no is_active field (it is shared with the
            # plain admin CRUD endpoint and new rows default active) — an
            # explicit inactive target needs one follow-up flush.
            if p.status == STATUS_INACTIVE:
                await repo.update_supplier(db, supplier, SupplierUpdate(isActive=False))
            created += 1
            processed.append(SupplierImportProcessedRow(
                row_number=state.row_number, supplier_code=p.code,
                status="COMPLETED", message="สร้าง Supplier ใหม่",
            ))
        elif state.operation == OPERATION_UPDATE and state.existing is not None:
            # Every field is passed explicitly (never exclude_unset) — that is
            # what makes a blank optional cell CLEAR the stored value.
            await repo.update_supplier(
                db, state.existing,
                SupplierUpdate(
                    name=p.name, taxId=p.tax_id, contactName=p.contact_name,
                    contactEmail=p.contact_email, contactPhone=p.contact_phone,
                    address=p.address, isActive=p.status == STATUS_ACTIVE,
                ),
            )
            updated += 1
            message = "แก้ไขข้อมูล Supplier"
            if state.was_active is False and p.status == STATUS_ACTIVE:
                activated += 1
                message = "เปิดใช้งาน Supplier"
            elif state.was_active is True and p.status == STATUS_INACTIVE:
                deactivated += 1
                message = "ปิดใช้งาน Supplier (ข้อมูลแปลง/รอบปลูก/ประวัติการตรวจไม่ถูกลบ)"
            processed.append(SupplierImportProcessedRow(
                row_number=state.row_number, supplier_code=p.code,
                status="COMPLETED", message=message,
            ))
        else:
            processed.append(SupplierImportProcessedRow(
                row_number=state.row_number, supplier_code=p.code or None,
                status="NO_CHANGE", message="ข้อมูลไม่เปลี่ยนแปลง",
            ))

    result = SupplierImportCommitResult(
        total_rows=len(states),
        created_suppliers=created,
        updated_suppliers=updated,
        activated_suppliers=activated,
        deactivated_suppliers=deactivated,
        unchanged_rows=preview.summary.unchanged_rows,
        error_rows=0,
        processed_rows=processed,
    )
    return result, states


async def commit(
    db: AsyncSession,
    content: bytes,
    *,
    preview_state: SupplierImportPreviewState | None,
    scope_conditions: list[Any],
    can_create: bool,
    can_update: bool,
) -> SupplierImportCommitResult:
    result, _states = await _commit_execute(
        db, content, preview_state=preview_state, scope_conditions=scope_conditions,
        can_create=can_create, can_update=can_update,
    )
    return result


# --- Report-builder support (services/supplier_import_report.py) -----------


def row_view(state: _RowState) -> dict[str, Any]:
    """Neutral dict view of one row for the result-workbook builder (mirrors
    plot_import.report_row_view / master_data_crop_variety_import.row_view) —
    never exposes anything beyond what the JSON preview/commit responses
    already carry, and never a UUID or a stack trace."""
    return {
        "row_number": state.row_number,
        "raw": dict(state.raw),
        "row_status": ROW_STATUS_ERROR if state.is_error else ROW_STATUS_READY,
        "operation": state.operation,
        "error_message": state.error,
        "warning_message": state.warning,
    }


async def preview_row_views(
    db: AsyncSession,
    content: bytes,
    *,
    scope_conditions: list[Any],
    can_create: bool,
    can_update: bool,
) -> tuple[SupplierImportSummary, list[dict[str, Any]]]:
    data_rows = await _load_data_rows(content)
    states = await _build_row_states(
        db, data_rows, scope_conditions=scope_conditions,
        can_create=can_create, can_update=can_update,
    )
    return _summarize(states), [row_view(s) for s in states]


async def commit_row_views(
    db: AsyncSession,
    content: bytes,
    *,
    preview_state: SupplierImportPreviewState | None,
    scope_conditions: list[Any],
    can_create: bool,
    can_update: bool,
) -> tuple[SupplierImportCommitResult, list[dict[str, Any]]]:
    result, states = await _commit_execute(
        db, content, preview_state=preview_state, scope_conditions=scope_conditions,
        can_create=can_create, can_update=can_update,
    )
    return result, [row_view(s) for s in states]


# --- Template builder ------------------------------------------------------

_EXAMPLE_ONLY_NOTICE = "ข้อมูลตัวอย่างเท่านั้น — ระบบจะไม่นำเข้าชีตนี้"

# Same palette as the Plot Import template (app/api/v1/plots.py) so the two
# downloads read alike: blue header, gray description, red example rows.
_STYLE_HEADER_ARGS = dict(bg="FFDCE6F1", bold=True)
_STYLE_DESCRIPTION_ARGS = dict(bg="FFD9D9D9")
_STYLE_EXAMPLE_ARGS = dict(bg="FFFFCDD2", font_color="FFB71C1C")

# Column widths, in IMPORT_COLUMNS order — address is the wide one.
_COLUMN_WIDTHS: list[int] = [16, 16, 30, 18, 22, 28, 18, 45, 12]

_EXAMPLE_ROWS: list[dict[str, str]] = [
    {
        "action": ACTION_SAVE,
        "supplierCode": "SUP999",
        "supplierName": "ตัวอย่าง Supplier (สร้างใหม่)",
        "taxId": "0105500000001",
        "contactName": "คุณตัวอย่าง",
        "contactEmail": "example@example.com",
        "contactPhone": "021234567",
        "address": "123 ถนนตัวอย่าง ต.ตัวอย่าง อ.ตัวอย่าง จ.เชียงใหม่ 50000",
        "status": STATUS_ACTIVE,
    },
    {
        "action": ACTION_SAVE,
        "supplierCode": "SUP001",
        "supplierName": "ตัวอย่าง Supplier (แก้ไขของเดิม)",
        "taxId": "",
        "contactName": "คุณตัวอย่าง สอง",
        "contactEmail": "",
        "contactPhone": "0812345678",
        "address": "",
        "status": STATUS_INACTIVE,
    },
]


def _supplier_row(supplier: Supplier) -> list[str]:
    """One existing Supplier as a pre-filled, ready-to-edit data row.

    Every cell is a STRING — that is what keeps a leading zero on taxId /
    contactPhone / supplierCode: excel_workbook writes a str as an inline
    string cell (t="inlineStr"), which Excel shows verbatim, whereas a
    numeric cell would render 0812345678 as 812345678.
    """
    return [
        ACTION_SAVE,
        supplier.code or "",
        supplier.name or "",
        supplier.tax_id or "",
        supplier.contact_name or "",
        supplier.contact_email or "",
        supplier.contact_phone or "",
        supplier.address or "",
        STATUS_ACTIVE if supplier.is_active else STATUS_INACTIVE,
    ]


async def build_template(db: AsyncSession, *, scope_conditions: list[Any]) -> bytes:
    """GET .../import/template — the caller's own in-scope Suppliers, active
    and inactive alike, pre-filled and ready to edit.

    Read-only: never writes to the DB. Carries no UUID and nothing outside
    the 9-column Supplier contract — no plot counts, no credentials, no
    internal ids.

    "ข้อมูล Supplier" is ALWAYS the first sheet (index 0 in the `sheets` list)
    — services/excel_reader.py's read_first_sheet always reads
    xl/worksheets/sheet1.xml, so the red "ตัวอย่าง" sheet can never become the
    sheet a re-uploaded file is parsed from.
    """
    # Local import mirrors master_data_crop_variety_import.build_template —
    # excel_workbook is a generic writer that must not be coupled to any
    # importer at module import time.
    from app.services.excel_workbook import (
        Cell,
        CellStyle,
        DataValidationRule,
        StyledCell,
        build_xlsx,
    )

    style_header = CellStyle(**_STYLE_HEADER_ARGS)
    style_description = CellStyle(**_STYLE_DESCRIPTION_ARGS)
    style_example = CellStyle(**_STYLE_EXAMPLE_ARGS)

    suppliers = await repo.list_suppliers_for_template(db, scope_conditions=scope_conditions)

    data_rows: list[list[Cell]] = [
        [StyledCell(col, style_header) for col in IMPORT_COLUMNS],
        [StyledCell(TEMPLATE_COLUMN_DESCRIPTIONS[col], style_description) for col in IMPORT_COLUMNS],
    ]
    for supplier in suppliers:
        data_rows.append(list(_supplier_row(supplier)))

    example_rows: list[list[Cell]] = [
        [StyledCell(col, style_header) for col in IMPORT_COLUMNS],
        [StyledCell(_EXAMPLE_ONLY_NOTICE, style_example)],
    ]
    for example in _EXAMPLE_ROWS:
        example_rows.append([
            StyledCell(example.get(col, ""), style_example) for col in IMPORT_COLUMNS
        ])

    validations = {
        SHEET_NAME: [
            # status — a closed two-value set, so Excel blocks anything else
            # outright. Small enough for an inline literal list (nowhere near
            # Excel's ~255-char inline-formula cap). A UX aid only: the server
            # re-validates every cell regardless.
            DataValidationRule(
                sqref="I3:I5000",
                formula1=f'"{STATUS_ACTIVE},{STATUS_INACTIVE}"',
                show_error_message=True,
            ),
        ],
    }

    return build_xlsx(
        [(SHEET_NAME, data_rows), (EXAMPLE_SHEET_NAME, example_rows)],
        validations,
        column_widths={SHEET_NAME: _COLUMN_WIDTHS, EXAMPLE_SHEET_NAME: _COLUMN_WIDTHS},
        freeze_header_sheets={SHEET_NAME},
        auto_filter_sheets={SHEET_NAME},
    )
