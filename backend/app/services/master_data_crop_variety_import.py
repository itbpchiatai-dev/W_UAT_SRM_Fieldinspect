"""Master Data crop/variety Excel import (round 8-15A) — parse, validate,
commit. Backend foundation only — no frontend UI this round.

Single worksheet "พืชและพันธุ์", 3 columns: crop / variety / varietyStatus.
Row 1 = header, row 2 = a Thai description row the parser skips (same
TEMPLATE_DESCRIPTION_MARKER convention as services/plot_import.py), row 3+ =
data. No spreadsheet-parser dependency — rows come from services/
excel_reader.py's hand-rolled reader, written back out with services/
excel_workbook.py's hand-rolled writer (same reuse as Plot Import; this
module does NOT reimplement either).

Business rules (see the round 8-15A brief for the full spec):

  crop  — required every row, trimmed, max length = master_data.value's own
          255-char column cap. A new value creates type=crop (parent=null,
          active=true). An existing ACTIVE crop is reused as-is. An existing
          INACTIVE crop blocks the row — Excel can never open/close a crop;
          that stays an App-only action (masterdata.update via the normal
          admin UI).

  variety — optional. Blank variety means the row is crop-only (varietyStatus
          must also be blank — a status with no variety is an error). A
          non-blank variety's parent is ALWAYS the row's own crop (never
          re-parented — an existing variety already bound to a DIFFERENT
          crop is a hard error, never migrated). varietyStatus blank
          defaults to active; "เปิดใช้งาน"/"ปิดใช้งาน" set the target
          explicitly; anything else is an error. An existing variety with an
          unchanged status is SKIPPED (no write); a changed status is READY
          to activate/deactivate — never renamed, never hard-deleted.

  Duplicates (all caught within the SAME uploaded file, every affected row
  reported, never silently merged):
    - the same (crop, variety) pair appearing on more than one row
    - the same crop-only row (blank variety) appearing more than once
    - the same variety value appearing under two DIFFERENT crops in the file
      (this mirrors the DB's own unique (type, value) index on master_data —
      a variety value can only ever belong to ONE crop, in the file just as
      in the table)
  A crop value appearing on MANY rows because it owns many varieties is
  normal, not a duplicate.

Preview never flushes/commits/updates/deletes anything — pure read + compute.
Commit re-parses and re-validates the SAME uploaded file server-side (never
trusts the client's JSON preview) and additionally re-checks live
master_data state; ANY drift from what Preview showed — a different file, a
different cell, or master_data changed underneath — raises
CropVarietyImportStateConflict before a single row executes. All-or-nothing:
crops are created first, then varieties (create/activate/deactivate only —
never hard-deleted, never re-parented, never renamed); every DB write in
this module is flush-only (app.repositories.master_data_repository.create/
update) — the caller's single `get_db` session transaction is the only
thing that ever commits or rolls back.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.master_data import MasterData
from app.repositories import master_data_repository as repo
from app.schemas.master_data import MasterDataCreate, MasterDataUpdate
from app.schemas.master_data_import import (
    CropVarietyImportCommitResult,
    CropVarietyImportPreview,
    CropVarietyImportPreviewState,
    CropVarietyImportPreviewStateRow,
    CropVarietyImportRowResult,
    CropVarietyImportSummary,
)
from app.services.excel_reader import ExcelParseError, read_first_sheet

# --- Sheet/column contract -------------------------------------------------
SHEET_NAME = "พืชและพันธุ์"
IMPORT_COLUMNS: list[str] = ["crop", "variety", "varietyStatus"]
MAX_IMPORT_ROWS = 1000  # same cap Plot Import uses (services/plot_import.py)

# master_data.value is varchar(255) (app/db/models/master_data.py) — the
# cap here mirrors the DB column exactly, so a validation error is raised
# before the DB ever sees an over-length value.
MAX_CROP_LEN = 255
MAX_VARIETY_LEN = 255

STATUS_ACTIVE = "เปิดใช้งาน"
STATUS_INACTIVE = "ปิดใช้งาน"
STATUS_VALUES: tuple[str, str] = (STATUS_ACTIVE, STATUS_INACTIVE)

# Row 2 of the shipped template is a human-readable Thai guidance row, not
# data — its `crop` cell starts with this exact marker so the importer can
# detect and skip exactly that row (same convention as
# plot_import.TEMPLATE_DESCRIPTION_MARKER; kept as this module's own
# constant rather than importing that one, since the two importers are
# otherwise unrelated and must never be coupled).
TEMPLATE_DESCRIPTION_MARKER = "คำอธิบาย (ระบบไม่นำเข้าแถวนี้)"

TEMPLATE_COLUMN_DESCRIPTIONS: dict[str, str] = {
    "crop": (
        TEMPLATE_DESCRIPTION_MARKER + " — ชนิดพืช จำเป็นทุกแถว "
        "พิมพ์ชนิดใหม่ได้ (ไม่ต้องเลือกจากรายการ)"
    ),
    "variety": "พันธุ์/สายพันธุ์ภายใต้ชนิดพืชในแถวเดียวกัน ไม่บังคับ — เว้นว่างหมายถึงแถวนี้มีแค่ชนิดพืช",
    "varietyStatus": (
        f"สถานะพันธุ์: '{STATUS_ACTIVE}' หรือ '{STATUS_INACTIVE}' "
        "เว้นว่าง = เปิดใช้งาน (ต้องเว้นว่างเมื่อไม่มี variety); "
        "ระบบไม่อนุญาตให้เปิด/ปิดชนิดพืชผ่านไฟล์นี้"
    ),
}

# --- Row action vocabulary (machine-readable, never parsed from Thai) -----
ACTION_CREATE_CROP = "create_crop"
ACTION_CREATE_VARIETY = "create_variety"
ACTION_CREATE_CROP_AND_VARIETY = "create_crop_and_variety"
ACTION_ACTIVATE_VARIETY = "activate_variety"
ACTION_DEACTIVATE_VARIETY = "deactivate_variety"
ACTION_NONE = "none"  # SKIPPED — a valid row that changes nothing

ROW_STATUS_READY = "READY"
ROW_STATUS_SKIPPED = "SKIPPED"
ROW_STATUS_ERROR = "ERROR"

_MSG_STATE_CHANGED = "ข้อมูล Master Data มีการเปลี่ยนแปลง กรุณาตรวจสอบไฟล์อีกครั้งก่อนนำเข้า"
_MSG_CROP_INACTIVE = "ชนิดพืชนี้ปิดใช้งานอยู่ กรุณาเปิดใช้งานผ่านหน้า Master Data ก่อน"


class CropVarietyImportFileError(ValueError):
    """The file can't be imported at all (bad/empty/oversized/wrong sheet
    shape) — a whole-request 422, distinct from per-row validation errors."""


class CropVarietyImportHasErrors(Exception):
    """Commit was asked to run but at least one row is invalid (or the
    server-side re-check found one) — nothing is written. Carries the full
    (re-computed) preview so the caller can surface row errors."""

    def __init__(self, preview: CropVarietyImportPreview) -> None:
        super().__init__("Import has row errors")
        self.preview = preview


class CropVarietyImportStateConflict(Exception):
    """The file bytes, a row's cell values, or master_data's live state
    diverged from the read-only preview the user approved. Raised BEFORE any
    row executes — nothing is written."""

    def __init__(self, message: str = _MSG_STATE_CHANGED) -> None:
        super().__init__(message)
        self.message = message


def file_digest(content: bytes) -> str:
    """SHA-256 of the raw uploaded file bytes, hex. Binds a preview-state
    expectation to the exact file that was Previewed. Never logs or returns
    the file content itself — only its digest."""
    return hashlib.sha256(content).hexdigest()


# --- Row parsing -------------------------------------------------------


def _str(raw: dict[str, str], key: str) -> str | None:
    v = raw.get(key)
    if v is None:
        return None
    v = v.strip()
    return v or None


def _is_template_description_row(raw: dict[str, str]) -> bool:
    return (raw.get("crop") or "").startswith(TEMPLATE_DESCRIPTION_MARKER)


@dataclass
class _Parsed:
    crop: str | None = None
    variety: str | None = None
    variety_status_raw: str | None = None


@dataclass
class _RowState:
    row_number: int
    parsed: _Parsed
    raw: dict[str, str] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)
    action: str = ACTION_NONE
    target_active: bool | None = None
    crop_existing: MasterData | None = None
    variety_existing: MasterData | None = None

    @property
    def row_status(self) -> str:
        if self.errors:
            return ROW_STATUS_ERROR
        if self.action == ACTION_NONE:
            return ROW_STATUS_SKIPPED
        return ROW_STATUS_READY


async def _load_data_rows(content: bytes) -> list[tuple[int, dict[str, str]]]:
    try:
        headers, raw_rows = read_first_sheet(content)
    except ExcelParseError as exc:
        raise CropVarietyImportFileError("ไม่สามารถอ่านไฟล์ได้ กรุณาใช้ไฟล์ .xlsx ที่ถูกต้อง") from exc
    if set(headers) != set(IMPORT_COLUMNS):
        raise CropVarietyImportFileError(
            "รูปแบบคอลัมน์ไม่ถูกต้อง กรุณาใช้ Template ที่ระบบสร้างให้ "
            f"(ต้องมีคอลัมน์: {', '.join(IMPORT_COLUMNS)})"
        )
    data_rows = [(n, r) for n, r in raw_rows if not _is_template_description_row(r)]
    if len(data_rows) > MAX_IMPORT_ROWS:
        raise CropVarietyImportFileError(f"ไฟล์มีจำนวนแถวเกิน {MAX_IMPORT_ROWS} แถว")
    if not data_rows:
        raise CropVarietyImportFileError("ไม่พบข้อมูลในไฟล์")
    return data_rows


async def _build_row_states(db: AsyncSession, data_rows: list[tuple[int, dict[str, str]]]) -> list[_RowState]:
    parsed_rows: list[tuple[int, dict[str, str], _Parsed]] = []
    for row_no, raw in data_rows:
        p = _Parsed(
            crop=_str(raw, "crop"),
            variety=_str(raw, "variety"),
            variety_status_raw=_str(raw, "varietyStatus"),
        )
        parsed_rows.append((row_no, raw, p))

    crop_values = {p.crop for _, _, p in parsed_rows if p.crop}
    variety_values = {p.variety for _, _, p in parsed_rows if p.variety}
    existing_crops = {m.value: m for m in await repo.list_by_type_values(db, "crop", crop_values)}
    existing_varieties = {m.value: m for m in await repo.list_by_type_values(db, "variety", variety_values)}

    # --- duplicate detection (within this file only) -----------------
    pair_counts: dict[tuple[str, str], int] = {}
    crop_only_counts: dict[str, int] = {}
    variety_crop_map: dict[str, set[str]] = {}
    for _, _, p in parsed_rows:
        if not p.crop:
            continue
        if p.variety:
            key = (p.crop, p.variety)
            pair_counts[key] = pair_counts.get(key, 0) + 1
            variety_crop_map.setdefault(p.variety, set()).add(p.crop)
        else:
            crop_only_counts[p.crop] = crop_only_counts.get(p.crop, 0) + 1

    states: list[_RowState] = []
    for row_no, raw, p in parsed_rows:
        state = _RowState(row_number=row_no, parsed=p, raw=raw)
        errors = state.errors

        if not p.crop:
            errors.append("ต้องระบุ crop")
        elif len(p.crop) > MAX_CROP_LEN:
            errors.append(f"crop ต้องไม่เกิน {MAX_CROP_LEN} ตัวอักษร")

        if p.variety and len(p.variety) > MAX_VARIETY_LEN:
            errors.append(f"variety ต้องไม่เกิน {MAX_VARIETY_LEN} ตัวอักษร")

        if not p.variety and p.variety_status_raw is not None:
            errors.append("variety ว่างแต่ varietyStatus มีค่า — ต้องเว้นว่าง varietyStatus เมื่อไม่มี variety")

        target_active: bool | None = None
        if p.variety:
            if p.variety_status_raw is None:
                target_active = True
            elif p.variety_status_raw == STATUS_ACTIVE:
                target_active = True
            elif p.variety_status_raw == STATUS_INACTIVE:
                target_active = False
            else:
                errors.append(
                    f"varietyStatus ต้องเป็น '{STATUS_ACTIVE}' หรือ '{STATUS_INACTIVE}' เท่านั้น "
                    f"(พบ: '{p.variety_status_raw}')"
                )
        state.target_active = target_active

        if p.crop:
            if p.variety:
                if pair_counts.get((p.crop, p.variety), 0) > 1:
                    errors.append("ข้อมูล crop และ variety นี้ซ้ำกันในไฟล์")
                if len(variety_crop_map.get(p.variety, set())) > 1:
                    errors.append(f"variety '{p.variety}' ซ้ำกันภายใต้ crop ต่างกันในไฟล์เดียวกัน")
            elif crop_only_counts.get(p.crop, 0) > 1:
                errors.append("แถว crop-only นี้ซ้ำกันในไฟล์ (ไม่มี variety)")

        crop_existing = existing_crops.get(p.crop) if p.crop else None
        state.crop_existing = crop_existing
        crop_is_new = p.crop is not None and crop_existing is None
        if crop_existing is not None and not crop_existing.active:
            errors.append(_MSG_CROP_INACTIVE)

        variety_existing = existing_varieties.get(p.variety) if p.variety else None
        state.variety_existing = variety_existing
        if p.variety and variety_existing is not None and variety_existing.parent != p.crop:
            errors.append(f"พันธุ์นี้ผูกกับชนิดพืชอื่นอยู่แล้ว ('{variety_existing.parent}') ไม่สามารถย้ายพืชได้")

        if not errors:
            if p.variety:
                variety_is_new = variety_existing is None
                if crop_is_new and variety_is_new:
                    state.action = ACTION_CREATE_CROP_AND_VARIETY
                elif variety_is_new:
                    state.action = ACTION_CREATE_VARIETY
                elif variety_existing.active == target_active:
                    state.action = ACTION_NONE
                elif target_active:
                    state.action = ACTION_ACTIVATE_VARIETY
                else:
                    state.action = ACTION_DEACTIVATE_VARIETY
            else:
                state.action = ACTION_CREATE_CROP if crop_is_new else ACTION_NONE

        states.append(state)
    return states


# --- Preview / result shaping ----------------------------------------------


def _row_result(state: _RowState) -> CropVarietyImportRowResult:
    return CropVarietyImportRowResult(
        row_number=state.row_number,
        crop=state.parsed.crop,
        variety=state.parsed.variety,
        variety_status=state.parsed.variety_status_raw,
        row_status=state.row_status,
        action=state.action,
        error_message="; ".join(state.errors),
    )


def _summarize(states: list[_RowState]) -> CropVarietyImportSummary:
    ready = [s for s in states if s.row_status == ROW_STATUS_READY]
    skipped = [s for s in states if s.row_status == ROW_STATUS_SKIPPED]
    errored = [s for s in states if s.row_status == ROW_STATUS_ERROR]
    crops_to_create = {
        s.parsed.crop for s in ready
        if s.action in (ACTION_CREATE_CROP, ACTION_CREATE_CROP_AND_VARIETY)
    }
    varieties_to_create = [
        s for s in ready if s.action in (ACTION_CREATE_VARIETY, ACTION_CREATE_CROP_AND_VARIETY)
    ]
    activate = [s for s in ready if s.action == ACTION_ACTIVATE_VARIETY]
    deactivate = [s for s in ready if s.action == ACTION_DEACTIVATE_VARIETY]
    return CropVarietyImportSummary(
        total_rows=len(states),
        ready_rows=len(ready),
        skipped_rows=len(skipped),
        error_rows=len(errored),
        crops_to_create=len(crops_to_create),
        varieties_to_create=len(varieties_to_create),
        varieties_to_activate=len(activate),
        varieties_to_deactivate=len(deactivate),
    )


def _to_preview_state_row(state: _RowState) -> CropVarietyImportPreviewStateRow:
    """Round 8-15A.1 — the schema caps crop/variety at 255 chars (mirrors
    master_data.value's own column, and doubles as the previewState
    input-boundary check on the wire). An over-length cell is ALREADY a
    row-level ERROR by the time this runs (see the MAX_CROP_LEN/
    MAX_VARIETY_LEN check above) — it must still be representable here so
    the row can appear in the preview response and be re-matched at commit,
    so the RAW value is truncated to fit rather than raising. Truncation is
    applied identically at Preview time and at Commit's fresh re-parse (both
    call this same function), so the two truncated values always compare
    equal for an over-length row — it stays an unconditional ERROR either
    way, never committable, so losing the exact tail past 255 chars changes
    nothing about the outcome. variety_parent_at_preview never needs this:
    it comes from an EXISTING master_data row, already <=255 by the DB's own
    column constraint."""
    ce = state.crop_existing
    ve = state.variety_existing
    return CropVarietyImportPreviewStateRow(
        row_number=state.row_number,
        crop=(state.parsed.crop or "")[:MAX_CROP_LEN],
        variety=state.parsed.variety[:MAX_VARIETY_LEN] if state.parsed.variety else None,
        variety_status=state.target_active,
        action=state.action,
        crop_existed=ce is not None,
        crop_was_active=ce.active if ce is not None else None,
        variety_existed=ve is not None,
        variety_was_active=ve.active if ve is not None else None,
        variety_parent_at_preview=ve.parent if ve is not None else None,
    )


def _to_preview(states: list[_RowState], *, include_state: bool, digest: str | None) -> CropVarietyImportPreview:
    preview_state = None
    if include_state:
        preview_state = CropVarietyImportPreviewState(
            file_sha256=digest or "",
            rows=[_to_preview_state_row(s) for s in states],
        )
    return CropVarietyImportPreview(
        summary=_summarize(states),
        rows=[_row_result(s) for s in states],
        preview_state=preview_state,
    )


# --- Public entry points -----------------------------------------------


async def build_preview(db: AsyncSession, content: bytes) -> CropVarietyImportPreview:
    """Parse + validate every row, WITHOUT writing anything. Safe/read-only —
    no flush, no commit, no update, no delete anywhere in this call path."""
    data_rows = await _load_data_rows(content)
    states = await _build_row_states(db, data_rows)
    return _to_preview(states, include_state=True, digest=file_digest(content))


def _row_states_match_preview(
    states: list[_RowState], expected: CropVarietyImportPreviewState,
) -> bool:
    if len(states) != len(expected.rows):
        return False
    expected_by_number = {r.row_number: r for r in expected.rows}
    for state in states:
        exp = expected_by_number.get(state.row_number)
        if exp is None or _to_preview_state_row(state) != exp:
            return False
    return True


async def _commit_execute(
    db: AsyncSession,
    content: bytes,
    *,
    preview_state: CropVarietyImportPreviewState | None,
) -> tuple[CropVarietyImportCommitResult, list[_RowState]]:
    """Re-validate server-side (never trusting the client's preview) and
    execute every row in the caller's SINGLE `db` session — this function
    only ever flushes; the caller's transaction is the one that commits or
    rolls back. Any invalid row (server re-check finds one) raises
    CropVarietyImportHasErrors with nothing written. Any drift from the
    approved preview (file, a row's plan, or live master_data state) raises
    CropVarietyImportStateConflict before a single row executes.

    Returns the row states as they were BEFORE execution (the correct plan
    for reporting "what this commit did") — never re-derived after the
    writes, which would see the just-created/just-flipped rows as already
    matching and misreport them as no-ops."""
    digest = file_digest(content)
    data_rows = await _load_data_rows(content)
    states = await _build_row_states(db, data_rows)

    if preview_state is None or preview_state.file_sha256 != digest:
        raise CropVarietyImportStateConflict()
    if not _row_states_match_preview(states, preview_state):
        raise CropVarietyImportStateConflict()

    preview = _to_preview(states, include_state=False, digest=None)
    if preview.summary.error_rows:
        raise CropVarietyImportHasErrors(preview)

    # --- Execute: crops first, then varieties (business-required order;
    # not a DB necessity since `parent` carries no FK — see module docstring).
    new_crop_values = sorted({
        s.parsed.crop for s in states
        if s.action in (ACTION_CREATE_CROP, ACTION_CREATE_CROP_AND_VARIETY) and s.parsed.crop
    })
    created_crops = 0
    for value in new_crop_values:
        await repo.create(db, MasterDataCreate(type="crop", value=value, parent=None))
        created_crops += 1

    created_varieties = 0
    activated = 0
    deactivated = 0
    for state in states:
        if state.action in (ACTION_CREATE_VARIETY, ACTION_CREATE_CROP_AND_VARIETY):
            item = await repo.create(
                db,
                MasterDataCreate(type="variety", value=state.parsed.variety or "", parent=state.parsed.crop),
            )
            created_varieties += 1
            # MasterDataCreate has no `active` field (shared with the plain
            # admin CRUD endpoint) — new rows default active=True at the ORM
            # level; an explicit inactive target needs one follow-up flush.
            if state.target_active is False:
                await repo.update(db, item, MasterDataUpdate(active=False))
        elif state.action == ACTION_ACTIVATE_VARIETY and state.variety_existing is not None:
            await repo.update(db, state.variety_existing, MasterDataUpdate(active=True))
            activated += 1
        elif state.action == ACTION_DEACTIVATE_VARIETY and state.variety_existing is not None:
            await repo.update(db, state.variety_existing, MasterDataUpdate(active=False))
            deactivated += 1

    result = CropVarietyImportCommitResult(
        created_crops=created_crops,
        created_varieties=created_varieties,
        activated_varieties=activated,
        deactivated_varieties=deactivated,
        skipped_rows=preview.summary.skipped_rows,
        total_rows=len(states),
    )
    return result, states


async def commit(
    db: AsyncSession,
    content: bytes,
    *,
    preview_state: CropVarietyImportPreviewState | None,
) -> CropVarietyImportCommitResult:
    result, _states = await _commit_execute(db, content, preview_state=preview_state)
    return result


# --- Report-builder support (services/master_data_crop_variety_import_report.py) ---


def row_view(state: _RowState) -> dict[str, Any]:
    """Neutral dict view of one row for the result-workbook builder (mirrors
    plot_import.report_row_view's role) — never exposes anything beyond what
    the JSON preview/commit responses already carry."""
    return {
        "row_number": state.row_number,
        "raw": dict(state.raw),
        "row_status": state.row_status,
        "action": state.action,
        "error_message": "; ".join(state.errors),
    }


async def preview_row_views(
    db: AsyncSession, content: bytes,
) -> tuple[CropVarietyImportSummary, list[dict[str, Any]]]:
    """Read-only: same core as build_preview, shaped for the report builder.

    Round 8-15A.1 — ALSO returns the authoritative CropVarietyImportSummary
    (the same one build_preview's JSON response carries — already correctly
    deduplicated: `crops_to_create` counts DISTINCT crop VALUES, never rows),
    so the preview-report workbook's summary sheet can use these numbers
    directly instead of re-tallying row_views by action string — a per-ROW
    tally double-counts a crop that owns more than one NEW variety row."""
    data_rows = await _load_data_rows(content)
    states = await _build_row_states(db, data_rows)
    return _summarize(states), [row_view(s) for s in states]


async def commit_row_views(
    db: AsyncSession, content: bytes, *, preview_state: CropVarietyImportPreviewState | None,
) -> tuple[CropVarietyImportCommitResult, list[dict[str, Any]]]:
    """Same core as commit — re-validates, checks drift, executes once, and
    returns the row views (the PRE-execution plan — see _commit_execute's
    docstring) for the COMPLETED result workbook.

    Round 8-15A.1 — ALSO returns the authoritative CropVarietyImportCommitResult
    (the same one the JSON /commit endpoint returns and logs to
    ActivityLogger — created_crops is already correctly deduplicated by
    _commit_execute's `new_crop_values` set), so every place that reports
    "what this commit did" (JSON response, commit-report workbook, activity
    log) sources the SAME numbers instead of each re-deriving its own
    (previously-inconsistent) count from row_views."""
    result, states = await _commit_execute(db, content, preview_state=preview_state)
    return result, [row_view(s) for s in states]


# --- Template builder --------------------------------------------------


def _template_rows(crops: list[MasterData], varieties_by_crop: dict[str, list[MasterData]]) -> list[list]:
    rows: list[list] = [
        list(IMPORT_COLUMNS),
        [TEMPLATE_COLUMN_DESCRIPTIONS[c] for c in IMPORT_COLUMNS],
    ]
    for crop in crops:
        variety_rows = varieties_by_crop.get(crop.value, [])
        if not variety_rows:
            rows.append([crop.value, "", ""])
            continue
        for v in variety_rows:
            status = STATUS_ACTIVE if v.active else STATUS_INACTIVE
            rows.append([crop.value, v.value, status])
    return rows


# Round 8-15A.1 — the crop dropdown moved OFF an inline literal list (Excel
# caps a list-validation's inline formula around 255 characters — too small
# once there are more than a handful of crops) and ONTO a workbook defined
# name pointing at a HIDDEN reference sheet. Both names are this importer's
# own private contract with the workbook it generates — excel_workbook.py
# (the generic writer) never hardcodes either; it only knows "some sheet is
# hidden" and "some defined name points at some range," both supplied by
# this module.
_REFERENCE_SHEET_NAME = "_reference"
CROP_OPTIONS_DEFINED_NAME = "_CV_CROP_OPTIONS"


async def build_template(db: AsyncSession) -> bytes:
    """GET .../template — active crops only (item 6: an inactive crop never
    appears), each crop's varieties both active AND inactive (item 4/7/8), a
    crop with none gets one blank-variety row (item 5). No UUID/internal
    type/database id anywhere in the sheet (item 8/12) — only value/parent
    strings and the fixed varietyStatus vocabulary. Read-only: never writes
    to the DB.

    `พืชและพันธุ์` is ALWAYS the first sheet in the workbook (index 0 in the
    `sheets` list build_xlsx receives) — services/excel_reader.py's
    read_first_sheet always reads xl/worksheets/sheet1.xml, so the hidden
    `_reference` sheet (added, when present, as the SECOND sheet) never
    shifts what a re-uploaded file's "first sheet" resolves to.
    """
    # Local import avoids a module-level cycle risk (excel_workbook is a
    # generic writer with no knowledge of this importer).
    from app.services.excel_workbook import DataValidationRule, DefinedName, build_xlsx

    crops = await repo.list_items(db, type="crop", active_only=True)
    all_varieties = await repo.list_items(db, type="variety", active_only=False)
    varieties_by_crop: dict[str, list[MasterData]] = {}
    for v in all_varieties:
        if v.parent:
            varieties_by_crop.setdefault(v.parent, []).append(v)

    rows = _template_rows(crops, varieties_by_crop)
    sheets: list[tuple[str, list[list]]] = [(SHEET_NAME, rows)]
    hidden_sheets: set[str] = set()
    defined_names: list[DefinedName] = []

    validations: dict[str, list[DataValidationRule]] = {
        SHEET_NAME: [
            # varietyStatus — closed set (item 9), UNCHANGED by this round:
            # typing anything else is blocked by Excel itself, AND still
            # re-validated server-side. Small fixed vocabulary — an inline
            # literal list is fine here; it never approaches the 255-char cap.
            DataValidationRule(
                sqref="C3:C5000", formula1=f'"{STATUS_ACTIVE},{STATUS_INACTIVE}"',
                show_error_message=True,
            ),
        ],
    }
    # crop — SUGGESTION list from active crops only (item 10): typing a new
    # crop name must still be allowed, so show_error_message=False. Skipped
    # entirely when there are no active crops yet (an empty named range is
    # invalid OOXML) — a brand-new install still gets a usable template, just
    # without the suggestion dropdown until at least one crop exists (item 7).
    if crops:
        ref_rows: list[list] = [[c.value] for c in crops]
        sheets.append((_REFERENCE_SHEET_NAME, ref_rows))
        hidden_sheets.add(_REFERENCE_SHEET_NAME)
        defined_names.append(DefinedName(
            name=CROP_OPTIONS_DEFINED_NAME,
            ref=f"'{_REFERENCE_SHEET_NAME}'!$A$1:$A${len(crops)}",
        ))
        validations[SHEET_NAME].append(
            DataValidationRule(
                sqref="A3:A5000", formula1=CROP_OPTIONS_DEFINED_NAME, show_error_message=False,
            )
        )

    return build_xlsx(sheets, validations, hidden_sheets=hidden_sheets, defined_names=defined_names)
