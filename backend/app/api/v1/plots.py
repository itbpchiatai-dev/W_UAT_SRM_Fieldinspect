"""Plots CRUD + user assignment — FarmLog field/แปลง management."""
from __future__ import annotations

import asyncio
from datetime import date, datetime, time, timezone
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile, status
from fastapi.responses import Response
from pydantic import ValidationError
from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.db.models.plot_cycle import CYCLE_STATUS_ACTIVE, PlotCycle

from app.api.deps.scope import _resolve_scope, get_rls_context, get_supplier_scope_filter
from app.auth.dependencies import CurrentUser, require_any_permission, require_permission
from app.auth.permissions import PermissionKey
from app.auth.plot_access_password import (
    PlotAccessPasswordPolicyError,
    PlotAccessPepperMissingError,
    build_plot_access_password_lookup_digest,
    hash_plot_access_password,
    validate_plot_access_password,
)
from app.db.models.plot import Plot
from app.db.models.plot_assignment import PlotAssignment
from app.db.models.supplier import Supplier
from app.db.models.user import User
from app.db.session import get_db
from app.repositories import plot_access_credential_repository as credential_repo
from app.repositories import plot_access_phone_repository as phone_repo
from app.repositories import plot_cycle_repository as plot_cycle_repo
from app.repositories import plot_repository as repo
from app.repositories import supplier_repository as supplier_repo
from app.schemas.plot import (
    AssignedUserSummary,
    PlotAccessPhoneConfig,
    PlotAccessPhoneConfigResponse,
    PlotAccessPhoneRead,
    PlotAssignRequest,
    PlotCreate,
    PlotCycleClose,
    PlotCycleCreate,
    PlotCycleRead,
    PlotCycleRollover,
    PlotCycleRolloverResult,
    PlotCredentialReadiness,
    PlotCredentialReadinessPlot,
    PlotCycleUpdate,
    PlotInspectionCredentialSet,
    PlotInspectionCredentialStatus,
    PlotLookupRead,
    PlotPhoneSearchRequest,
    PlotRead,
    PlotSummary,
    PlotUpdate,
    PlotWithCycleCreate,
    PlotWithCycleCreateResult,
    normalize_and_validate_phone_config,
)
from app.schemas.plot_import import (
    PlotImportCommitResult,
    PlotImportPreview,
    PlotImportPreviewState,
)
from app.services import master_data_validation, plot_import, plot_import_report
from app.services.excel_workbook import Cell, CellStyle, StyledCell, build_xlsx
from app.services.lot_number import AutoLotMissingComponentError, LotNumberTooLongError
from app.services.loggers.activity_logger import ActivityLogger

router = APIRouter(tags=["plots"])

# Round 8-12A.1 — ONE Thai wording for "an Auto Lot was requested but a
# component is missing", shared by every create/update path so the message a
# user sees never depends on which endpoint they hit. Names the missing FIELD
# only — never the submitted value, and never a stack trace.
_AUTO_LOT_FIELD_LABELS = {
    "cycleLabel": "ชื่อรอบปลูก (cycleLabel)",
    "supplierCode": "รหัส Supplier ของแปลง",
    "pCode": "P.Code",
}


def _auto_lot_missing_detail(missing: tuple[str, ...]) -> str:
    """Thai 422 detail for AutoLotMissingComponentError."""
    if missing == ("supplierCode",):
        # Not something the caller can fix by filling a form field — the plot's
        # supplier could not be resolved at all.
        return "ไม่พบ Supplier ของแปลง กรุณาตรวจสอบข้อมูลแปลง"
    fields = ", ".join(_AUTO_LOT_FIELD_LABELS.get(m, m) for m in missing)
    return (
        f"กรุณาระบุ {fields} ก่อนสร้าง Auto Lot "
        "(รูปแบบ: ชื่อรอบปลูก-รหัส Supplier-P.Code-เลขรัน)"
    )

_FULL_ACCESS_ROLES = {"internal:super_admin", "internal:admin", "farmlog:supervisor"}


def _populate_active_cycle(target, plot) -> None:
    """Fill the shared active_cycle_* read-model (round 7.3.1) on a
    PlotRead/PlotSummary from the eager-loaded Plot.active_cycle relationship
    (filtered to status='active'). Leaves every field None when the plot has
    no active cycle — the frontend reads active_cycle_id != null as the
    authoritative "has an active planting cycle" signal."""
    cycle = getattr(plot, "active_cycle", None)
    if cycle is None:
        return
    target.active_cycle_id = cycle.id
    target.active_cycle_no = cycle.cycle_no
    target.active_cycle_status = cycle.status
    target.active_cycle_crop = cycle.crop
    target.active_cycle_variety = cycle.variety
    target.active_cycle_label = cycle.cycle_label
    target.active_cycle_lot_no = cycle.lot_no
    target.active_cycle_po_number = cycle.po_number
    target.active_cycle_p_code = cycle.p_code
    target.active_cycle_supplier_lot_no = cycle.supplier_lot_no
    target.active_cycle_planting_date = cycle.planting_date
    target.active_cycle_plant_count = cycle.plant_count
    target.active_cycle_expected_yield_full = cycle.expected_yield_full
    target.active_cycle_expected_yield_unit = cycle.expected_yield_unit


def _populate_access_phones(target, plot) -> None:
    """Fill primaryPhone/additionalPhones (round 8-3A) on a PlotRead/PlotSummary
    from the eager-loaded Plot.access_phones relationship (active rows,
    primary-first). Defensive default [] so endpoint unit tests that build a
    plain plot object without this relationship don't trip an attribute error."""
    phones = [
        p for p in (getattr(plot, "access_phones", None) or []) if p.is_active
    ]
    primary = next((p for p in phones if p.access_type == "primary"), None)
    target.primary_phone = primary.phone_normalized if primary is not None else None
    target.additional_phones = [
        p.phone_normalized for p in phones if p.access_type == "additional"
    ]


def _access_phone_read(row) -> PlotAccessPhoneRead:
    """One plot_access_phones ORM row → PlotAccessPhoneRead (the schema's `phone`
    is the ORM's phone_normalized, so this is built explicitly, not
    model_validate'd)."""
    return PlotAccessPhoneRead(
        id=row.id,
        phone=row.phone_normalized,
        access_type=row.access_type,
        is_active=row.is_active,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _config_response(phones: list) -> PlotAccessPhoneConfigResponse:
    """Active plot_access_phones rows → the GET/PUT config response (round
    8-3A). `phones` is already active-only + primary-first (repo order)."""
    primary = next((p for p in phones if p.access_type == "primary"), None)
    return PlotAccessPhoneConfigResponse(
        primary_phone=primary.phone_normalized if primary is not None else None,
        additional_phones=[
            p.phone_normalized for p in phones if p.access_type == "additional"
        ],
        items=[_access_phone_read(p) for p in phones],
    )


def _to_read(plot) -> PlotRead:
    read = PlotRead.model_validate(plot)
    read.assigned_users = [
        AssignedUserSummary(
            user_id=a.user_id,
            email=a.user.email,
            full_name=a.user.full_name,
            assigned_at=a.assigned_at,
        )
        for a in plot.assignments
        if a.user is not None
    ]
    if getattr(plot, "supplier", None) is not None:
        read.supplier_code = plot.supplier.code
        read.supplier_name = plot.supplier.name
    _populate_active_cycle(read, plot)
    _populate_access_phones(read, plot)
    return read


def _to_summary(plot) -> PlotSummary:
    s = PlotSummary.model_validate(plot)
    s.assigned_count = len(plot.assignments)
    if getattr(plot, "supplier", None) is not None:
        s.supplier_code = plot.supplier.code
        s.supplier_name = plot.supplier.name
    _populate_active_cycle(s, plot)
    _populate_access_phones(s, plot)
    return s


def _role_names(user: User) -> set[str]:
    return {role.name for role in user.roles}


async def _template_suppliers(db: AsyncSession, user: User) -> list[Supplier]:
    roles = _role_names(user)
    stmt = select(Supplier).where(Supplier.is_active.is_(True)).order_by(Supplier.code.asc())
    if roles & _FULL_ACCESS_ROLES:
        result = await db.execute(stmt)
        return list(result.scalars().all())
    if user.supplier_id is not None:
        result = await db.execute(stmt.where(Supplier.id == user.supplier_id))
        return list(result.scalars().all())
    if "farmlog:field_officer" in roles:
        result = await db.execute(
            stmt.join(Plot, Plot.supplier_id == Supplier.id)
            .join(PlotAssignment, PlotAssignment.plot_id == Plot.id)
            .where(PlotAssignment.user_id == user.id)
            .distinct()
        )
        return list(result.scalars().all())
    return []


# Single-sheet plot+cycle IMPORT template (round 7.5). One "plots" sheet whose
# columns == plot_import.IMPORT_COLUMNS (kept in one place so the reader and the
# template can't drift). Unlike the old download-only blank form, this feeds a
# real importer. Its rows (round 8-2.1; example set narrowed round 8-2.7 and
# again round 8-2.7.1):
#   row 1  — technical headers (== IMPORT_COLUMNS), `action` first
#   row 2  — one Thai description row (skipped on import; see plot_import
#            .TEMPLATE_DESCRIPTION_ACTION)
#   rows 3-5 — three example rows, one per common workflow
#            (create_plot_with_cycle / update_current_cycle / start_next_cycle).
#            start_next_cycle replaced close_and_start_new_cycle as the example
#            for "advance the plot to its next cycle" (round 8-2.7.1) — it
#            resolves to start_new_cycle or close_and_start_new_cycle depending
#            on the plot's actual state, so a working-level user never has to
#            choose between them. start_new_cycle and close_and_start_new_cycle
#            are still fully supported and parsed/executed unchanged; they are
#            just no longer the promoted default examples.
# plantingDate is entered as YYYY-MM-DD text.
_PLOT_TEMPLATE_HEADERS: list[str] = plot_import.IMPORT_COLUMNS


def _template_example_rows(supplier_code: str) -> list[dict[str, str]]:
    """The three worked-example rows (create / update / start_next_cycle,
    round 8-2.7.1) shared by the generic single-sheet template's rows 3-5
    (_plot_template_workbook, unchanged) AND round 8-6A's contextual
    workbook's separate "ตัวอย่าง" sheet (Part E) — extracted so both stay
    byte-identical example data instead of maintaining two copies."""
    return [
        {
            "action": "create_plot_with_cycle",
            "supplierCode": supplier_code, "plotCode": "P101",
            "plotName": "แปลงตัวอย่าง (สร้างใหม่)",
            "primaryPhone": "0845552162", "additionalPhones": "0855551234",
            "village": "ต.ตัวอย่าง", "district": "อ.ตัวอย่าง", "province": "เชียงใหม่",
            "latitude": "18.7883", "longitude": "98.9853", "rai": "5",
            "crop": "พริก", "variety": "พริกขี้หนู", "cycleLabel": "jun2026",
            "poNumber": "PO25001", "pCode": "Melon-A",
            "lotNo": "LOT-01",
            # Round 8-12A — the Supplier's OWN lot number, unrelated to the
            # system's Auto Lot. Free-form; leave blank when there isn't one.
            "supplierLotNo": "SUP-LOT-2026-01",
            # Round 8-21A — independent back-office reference fields; leave
            # blank when there is nothing to record for this cycle.
            "oracleSupplierCode": "ORC-SUP-001",
            "oracleInvoice": "INV-2026-0001",
            "refAccount": "ACC-0001",
            "plantingDate": "2026-06-01", "plantCount": "1000",
            "expectedYieldFull": "800", "expectedYieldUnit": "kg",
            # Round 8-9B.1 — EXAMPLE value only. Example rows live on the
            # red-highlighted "ตัวอย่าง" sheet (contextual workbook) or rows
            # 3-5 of the blank generic template; the importer never reads
            # either, and no current-data row is ever pre-filled with this.
            "inspectionPasswordStatus": "not_configured",
            "newInspectionPassword": "1357",
        },
        {
            "action": "update_current_cycle",
            "supplierCode": supplier_code, "plotCode": "P002",
            "primaryPhone": "0899991234",
            "crop": "พริก", "variety": "พริกหยวก", "cycleLabel": "may2026",
            "poNumber": "PO25002", "pCode": "Chili-B",
            "lotNo": "LOT-03",
            "supplierLotNo": "SUP-LOT-2026-02",
            # Round 8-21A — example shows a genuine edit: nonblank text is
            # trimmed and saved. Leaving a cell like this blank on an
            # update_current_cycle row CLEARS the stored value (unlike
            # poNumber/pCode/supplierLotNo, which preserve on blank) — see
            # this column's description row / IMPORT_COLUMNS docstring.
            "oracleSupplierCode": "ORC-SUP-002",
            "oracleInvoice": "INV-2026-0002",
            "refAccount": "ACC-0002",
            "plantingDate": "2026-05-15", "plantCount": "800",
            "expectedYieldFull": "1000", "expectedYieldUnit": "kg",
            "inspectionPasswordStatus": "configured",
            "newInspectionPassword": "135790",
        },
        {
            "action": "start_next_cycle",
            "supplierCode": supplier_code, "plotCode": "P003",
            "primaryPhone": "0866661234", "additionalPhones": "0877771234,0888881234",
            "crop": "แตงโม", "variety": "กินรี", "cycleLabel": "aug2026",
            # Round 8-13A — PO Number is OPTIONAL on every new-cycle action;
            # this example deliberately leaves it blank (still a fully valid
            # row) to show that. pCode stays required — see below.
            "poNumber": None, "pCode": "Melon-C",
            "lotNo": "LOT-04",
            # Blank = this cycle has no supplier lot number (also what an
            # older workbook without the column effectively means).
            "supplierLotNo": None,
            "plantingDate": "2026-08-01", "plantCount": "600",
            "expectedYieldFull": "3000", "expectedYieldUnit": "kg",
            # Blank = keep the plot's existing password (the common case).
            "inspectionPasswordStatus": "configured",
        },
        {
            # Round 8-7A — final_plot: closes the active cycle as harvested
            # (Plot stays is_active=true). Round 8-10B: the numbers are always
            # kilograms and the inspection record is resolved server-side, so
            # neither has a column here any more.
            "action": "final_plot",
            "supplierCode": supplier_code, "plotCode": "P001",
            "cycleLabel": "jul2026",
            "harvestYield": "1250", "finalYieldAfterClean": "1180",
            "harvestDate": "2026-07-28",
            "finalNote": "ผลผลิตหลังคัดแยกและทำความสะอาด",
        },
    ]


def _plot_template_workbook(suppliers: list[Supplier]) -> bytes:
    supplier_code = suppliers[0].code if suppliers else "SUP001"
    examples = _template_example_rows(supplier_code)
    rows: list[list[str | int | float | None]] = [list(_PLOT_TEMPLATE_HEADERS)]
    # Row 2: Thai description of every column (skipped on import).
    rows.append([
        plot_import.TEMPLATE_COLUMN_DESCRIPTIONS[col] for col in _PLOT_TEMPLATE_HEADERS
    ])
    # Rows 3-5: one worked example per common workflow (round 8-2.7.1).
    for ex in examples:
        rows.append([ex.get(col) for col in _PLOT_TEMPLATE_HEADERS])
    return build_xlsx([("plots", rows)])


# --- Round 8-6A: filter-aware contextual template (Backend foundation) -----
# Same import-template endpoint, but when the caller passes a Supplier/
# province/crop/variety/q filter (matching the Plots page), the response is a
# 3-sheet workbook seeded from REAL active plots instead of the generic blank
# form above (which stays completely unchanged for the no-filter case — see
# download_plot_import_template). Read-only: building this workbook never
# writes anything; only a later Commit of the downloaded-and-edited file can
# close/open a cycle (plot_import.commit_import, unchanged by this round).

_SHEET_NEW_CYCLE = "นำเข้ารอบใหม่"
_SHEET_CURRENT_SNAPSHOT = "ข้อมูลปัจจุบัน"
_SHEET_EXCLUDED = "รายการที่ไม่รวม"
_SHEET_EXAMPLES = "ตัวอย่าง"

# Column → style classification for Sheet 1 (Part C "Style"). Every
# IMPORT_COLUMNS entry is in exactly one of these two sets — asserted once at
# import time below so the split can never silently drift from IMPORT_COLUMNS.
_REFERENCE_COLUMNS: frozenset[str] = frozenset({
    "action", "supplierCode", "plotCode", "plotName", "primaryPhone", "additionalPhones",
    "village", "district", "province", "latitude", "longitude", "rai",
    # Round 8-6J — informational only; never read by the importer (see
    # plot_import.IMPORT_COLUMNS's own comment), so it's a reference column
    # like the others: styled gray, never yellow/editable.
    "currentPlotStatus",
    # Round 8-9B.1 — same deal: exported so the user can SEE which plots
    # already have an inspection password, never read back by the importer.
    # Gray/reference, so it never invites editing.
    "inspectionPasswordStatus",
})
_EDITABLE_COLUMNS: frozenset[str] = frozenset({
    "crop", "variety", "cycleLabel", "poNumber", "pCode", "lotNo", "supplierLotNo",
    # Round 8-21A — same category as supplierLotNo above: genuine, optional
    # user input, never plot/supplier identity.
    "oracleSupplierCode", "oracleInvoice", "refAccount",
    "plantingDate", "plantCount", "expectedYieldFull", "expectedYieldUnit",
    # Round 8-7A — final_plot's actual-harvest columns; all genuine user
    # input (none are plot/supplier identity columns), so every one of them is
    # editable/yellow, none reference/gray. Round 8-10B dropped two of the
    # original six: the figures are always kg and the server picks the
    # inspection record itself, so neither was ever a real user decision.
    "harvestYield", "finalYieldAfterClean", "harvestDate", "finalNote",
    # Round 8-9B.1 — the ONE password input column: genuine user input, so
    # editable/yellow. Always exported BLANK (see _new_cycle_row_values) — a
    # downloaded template never carries an existing password back out.
    "newInspectionPassword",
})
assert _REFERENCE_COLUMNS | _EDITABLE_COLUMNS == set(plot_import.IMPORT_COLUMNS)
assert not (_REFERENCE_COLUMNS & _EDITABLE_COLUMNS)

# Style constants (Part C/E) — a solid background (+ bold/font color where
# specified), always legible text-on-fill. Colors are 8-digit ARGB hex.
_STYLE_HEADER = CellStyle(bg="FFDCE6F1", bold=True)          # header row
_STYLE_DESCRIPTION = CellStyle(bg="FFD9D9D9")                # description row
_STYLE_REFERENCE = CellStyle(bg="FFF2F2F2")                  # identity/reference cols
_STYLE_EDITABLE = CellStyle(bg="FFFFF9C4")                   # new-cycle editable cols
_STYLE_EXAMPLE = CellStyle(bg="FFFFCDD2", font_color="FFB71C1C")  # example rows (Sheet 4)
# Round 8-6G — excluded-plot sheet styles: deliberately distinct from Sheet
# 1's reference gray (FFF2F2F2) and from the bright example red, so this
# read-only "why was this left out" sheet never looks like an editable or
# importable one at a glance.
_STYLE_EXCLUDED_NOTICE = CellStyle(bg="FFFFCDD2", font_color="FFB71C1C", bold=True)
_STYLE_EXCLUDED_ROW = CellStyle(bg="FFF5F5F5")

_EXAMPLE_ONLY_NOTICE = "ข้อมูลตัวอย่างเท่านั้น — ระบบจะไม่นำเข้าชีตนี้"
_EXCLUDED_ONLY_NOTICE = "ชีตนี้เป็นข้อมูลสำหรับตรวจสอบ ระบบจะไม่นำเข้าข้อมูลจากชีตนี้"


def _header_row() -> list[StyledCell]:
    return [StyledCell(col, _STYLE_HEADER) for col in _PLOT_TEMPLATE_HEADERS]


def _description_row() -> list[StyledCell]:
    return [
        StyledCell(plot_import.TEMPLATE_COLUMN_DESCRIPTIONS[col], _STYLE_DESCRIPTION)
        for col in _PLOT_TEMPLATE_HEADERS
    ]


def _plot_access_phone_fields(plot: Plot) -> tuple[str | None, str | None]:
    """(primaryPhone, additionalPhones-CSV) from the plot's ACTIVE access
    phones — same source/order as _populate_access_phones (Plot.access_phones,
    already eager-loaded by plot_repository.list_plots), just CSV-joined for
    the Excel cell instead of a list."""
    phones = [p for p in (plot.access_phones or []) if p.is_active]
    primary = next((p for p in phones if p.access_type == "primary"), None)
    additional = [p.phone_normalized for p in phones if p.access_type == "additional"]
    return (
        primary.phone_normalized if primary is not None else None,
        ",".join(additional) if additional else None,
    )


def _inspection_password_status(configured: bool) -> str:
    """The informational inspectionPasswordStatus cell (round 8-9B.1) — the
    ONLY credential fact any exported workbook may carry. Never a password,
    never a hash/digest, never a version, never a last-digits hint."""
    return (
        plot_import.INSPECTION_PASSWORD_STATUS_CONFIGURED if configured
        else plot_import.INSPECTION_PASSWORD_STATUS_NOT_CONFIGURED
    )


def _new_cycle_row_values(
    plot: Plot, *, password_configured: bool = False,
) -> dict[str, str | None]:
    """Sheet 1 ("นำเข้ารอบใหม่") row for one real active Plot (Part C).

    action is always start_next_cycle. Physical-plot fields come from the
    Plot itself; planting-cycle fields come from Plot.active_cycle (None when
    the plot has no active cycle — its fields are then blank, and this
    deliberately never falls back to a closed/historical cycle, so the user
    always fills in a fresh plan rather than accidentally re-importing stale
    data). lotNo and plantingDate are ALWAYS blank here regardless of the
    active cycle: lotNo so the Auto Lot generator
    ({cycleLabel}-{supplierCode}-{pCode}-{running}, round 8-12A) can run when
    the user leaves it blank; plantingDate because it must be the NEW
    cycle's planting date, never copied from the old one.

    Round 8-7A pre-filled finalInspectionRecordId here from the cycle's latest
    active record; round 8-10B removed that column (the server resolves the
    record itself at import time), so this row no longer needs the record at
    all — and the template download no longer queries for it.
    """
    cycle = plot.active_cycle
    primary_phone, additional_phones = _plot_access_phone_fields(plot)
    return {
        "action": plot_import.ACTION_START_NEXT,
        "supplierCode": plot.supplier.code if plot.supplier is not None else None,
        "plotCode": plot.plot_code,
        "plotName": plot.name,
        "primaryPhone": primary_phone,
        "additionalPhones": additional_phones,
        "village": plot.village,
        "district": plot.district,
        "province": plot.province,
        "latitude": str(plot.latitude) if plot.latitude is not None else None,
        "longitude": str(plot.longitude) if plot.longitude is not None else None,
        "rai": str(plot.rai) if plot.rai is not None else None,
        "crop": cycle.crop if cycle is not None else None,
        "variety": cycle.variety if cycle is not None else None,
        "cycleLabel": cycle.cycle_label if cycle is not None else None,
        "poNumber": cycle.po_number if cycle is not None else None,
        "pCode": cycle.p_code if cycle is not None else None,
        "lotNo": None,
        # Round 8-12A — prefill the active cycle's CURRENT supplier lot number
        # so an edit round-trips it unchanged; blank when the cycle has none.
        "supplierLotNo": cycle.supplier_lot_no if cycle is not None else None,
        # Round 8-21A — prefill the active cycle's CURRENT reference fields, same
        # round-trip-unchanged rationale as supplierLotNo above. Because this row's
        # action is start_next_cycle (a NEW cycle), leaving one of these blank on
        # download and then re-uploading unedited creates a new cycle with that
        # field NULL — never a silent "keep the old cycle's value" (rollover never
        # auto-copies these; see plot_cycle_repository.create_cycle).
        "oracleSupplierCode": cycle.oracle_supplier_code if cycle is not None else None,
        "oracleInvoice": cycle.oracle_invoice if cycle is not None else None,
        "refAccount": cycle.ref_account if cycle is not None else None,
        "plantingDate": None,
        "plantCount": str(cycle.plant_count) if cycle is not None and cycle.plant_count is not None else None,
        "expectedYieldFull": (
            str(cycle.expected_yield_full)
            if cycle is not None and cycle.expected_yield_full is not None else None
        ),
        "expectedYieldUnit": cycle.expected_yield_unit if cycle is not None else None,
        "currentPlotStatus": _CURRENT_PLOT_STATUS_ACTIVE_LABEL,
        "inspectionPasswordStatus": _inspection_password_status(password_configured),
        # ALWAYS blank — a downloaded template must never carry an existing
        # password (or anything derived from one) back out of the server.
        "newInspectionPassword": None,
    }


def _reactivate_row_values(
    plot: Plot, latest_cycle: PlotCycle | None, *, password_configured: bool = False,
) -> dict[str, str | None]:
    """Sheet 1 row for an INACTIVE plot (round 8-6J Part D): action is always
    reactivate_plot_with_cycle. Unlike _new_cycle_row_values (which always
    blanks lotNo/plantingDate for a rollover-in-place), every planting-cycle
    field here is pre-filled from the plot's most recent HISTORICAL cycle
    (`latest_cycle` — any status, batch-loaded by the caller via
    plot_cycle_repository.get_latest_cycles_for_plots) as a starting point
    the user edits: an inactive plot's mirror columns may already be cleared
    by the deactivate flow, so history is the only reliable source.
    cycleLabel is deliberately copied too (not blanked) so the user SEES the
    old value and can rename it — reusing it unedited is caught by the
    historical-cycleLabel duplicate check (plot_import._cycle_label_reused_
    in_history) at Preview time, before Commit. `latest_cycle=None` (no cycle
    history at all) still produces a row — every cycle field is blank for
    the user to fill in from scratch, never invented.
    """
    primary_phone, additional_phones = _plot_access_phone_fields(plot)
    cycle = latest_cycle
    return {
        "action": plot_import.ACTION_REACTIVATE_WITH_CYCLE,
        "supplierCode": plot.supplier.code if plot.supplier is not None else None,
        "plotCode": plot.plot_code,
        "plotName": plot.name,
        "primaryPhone": primary_phone,
        "additionalPhones": additional_phones,
        "village": plot.village,
        "district": plot.district,
        "province": plot.province,
        "latitude": str(plot.latitude) if plot.latitude is not None else None,
        "longitude": str(plot.longitude) if plot.longitude is not None else None,
        "rai": str(plot.rai) if plot.rai is not None else None,
        "crop": cycle.crop if cycle is not None else None,
        "variety": cycle.variety if cycle is not None else None,
        "cycleLabel": cycle.cycle_label if cycle is not None else None,
        "poNumber": cycle.po_number if cycle is not None else None,
        "pCode": cycle.p_code if cycle is not None else None,
        "lotNo": cycle.lot_no if cycle is not None else None,
        # Round 8-12A — the cycle's current supplier lot number.
        "supplierLotNo": cycle.supplier_lot_no if cycle is not None else None,
        # Round 8-21A — the historical cycle's reference fields, same
        # "starting point the user edits" rationale as every other field
        # here (see this function's docstring); reactivate never auto-copies
        # these into the new cycle either — an unedited re-upload creates the
        # new cycle with whatever value this cell carries.
        "oracleSupplierCode": cycle.oracle_supplier_code if cycle is not None else None,
        "oracleInvoice": cycle.oracle_invoice if cycle is not None else None,
        "refAccount": cycle.ref_account if cycle is not None else None,
        "plantingDate": (
            cycle.planting_date.isoformat()
            if cycle is not None and cycle.planting_date is not None else None
        ),
        "plantCount": str(cycle.plant_count) if cycle is not None and cycle.plant_count is not None else None,
        "expectedYieldFull": (
            str(cycle.expected_yield_full)
            if cycle is not None and cycle.expected_yield_full is not None else None
        ),
        "expectedYieldUnit": cycle.expected_yield_unit if cycle is not None else None,
        "currentPlotStatus": _CURRENT_PLOT_STATUS_INACTIVE_LABEL,
        "inspectionPasswordStatus": _inspection_password_status(password_configured),
        # ALWAYS blank — see _new_cycle_row_values.
        "newInspectionPassword": None,
    }


def _new_cycle_sheet(
    plots: list[Plot],
    latest_cycles: dict[UUID, PlotCycle] | None = None,
    credential_status: dict[UUID, tuple[bool, int]] | None = None,
) -> list[list[Cell]]:
    latest_cycles = latest_cycles or {}
    # Round 8-9B.1 — batch-loaded by the caller (one query for the whole sheet,
    # never per plot). Only the boolean is ever read here; the version is
    # deliberately never exported.
    credential_status = credential_status or {}
    rows: list[list[Cell]] = [_header_row(), _description_row()]
    for plot in plots:
        configured = credential_status.get(plot.id, (False, 0))[0]
        if plot.is_active:
            values = _new_cycle_row_values(plot, password_configured=configured)
        else:
            values = _reactivate_row_values(
                plot, latest_cycles.get(plot.id), password_configured=configured,
            )
        rows.append([
            StyledCell(
                values.get(col),
                _STYLE_EDITABLE if col in _EDITABLE_COLUMNS else _STYLE_REFERENCE,
            )
            for col in _PLOT_TEMPLATE_HEADERS
        ])
    return rows


# Sheet 2 ("ข้อมูลปัจจุบัน") — its own fixed header set (Part D); this sheet is
# never read by the importer (excel_reader.read_first_sheet only ever reads
# the FIRST worksheet), so it has no obligation to match IMPORT_COLUMNS.
_CURRENT_SNAPSHOT_HEADERS: list[str] = [
    "supplierCode", "supplierName", "plotCode", "plotName", "plotIsActive",
    "primaryPhone", "additionalPhones", "village", "district", "province",
    "latitude", "longitude", "rai",
    "activeCycleNo", "activeCycleStatus", "activeCycleLabel",
    "crop", "variety", "poNumber", "pCode", "lotNo",
    "plantingDate", "plantCount", "expectedYieldFull", "expectedYieldUnit",
    # Round 8-9B.1 — informational status only (configured / not_configured).
    "inspectionPasswordStatus",
]


def _current_snapshot_row_values(
    plot: Plot, *, password_configured: bool = False,
) -> dict[str, str | None]:
    """One reference row per Plot (Part D) — backend truth from
    Plot.active_cycle (never the plots.current_* mirror columns, which can
    theoretically lag behind the active cycle for a brief window). Keeps
    lotNo/plantingDate/cycleLabel intact (unlike Sheet 1) so the user can
    compare old vs new before editing."""
    cycle = plot.active_cycle
    primary_phone, additional_phones = _plot_access_phone_fields(plot)
    return {
        "supplierCode": plot.supplier.code if plot.supplier is not None else None,
        "supplierName": plot.supplier.name if plot.supplier is not None else None,
        "plotCode": plot.plot_code,
        "plotName": plot.name,
        "plotIsActive": "true" if plot.is_active else "false",
        "primaryPhone": primary_phone,
        "additionalPhones": additional_phones,
        "village": plot.village,
        "district": plot.district,
        "province": plot.province,
        "latitude": str(plot.latitude) if plot.latitude is not None else None,
        "longitude": str(plot.longitude) if plot.longitude is not None else None,
        "rai": str(plot.rai) if plot.rai is not None else None,
        "activeCycleNo": str(cycle.cycle_no) if cycle is not None else None,
        "activeCycleStatus": cycle.status if cycle is not None else None,
        "activeCycleLabel": cycle.cycle_label if cycle is not None else None,
        "crop": cycle.crop if cycle is not None else None,
        "variety": cycle.variety if cycle is not None else None,
        "poNumber": cycle.po_number if cycle is not None else None,
        "pCode": cycle.p_code if cycle is not None else None,
        "lotNo": cycle.lot_no if cycle is not None else None,
        # Round 8-12A — the cycle's current supplier lot number.
        "supplierLotNo": cycle.supplier_lot_no if cycle is not None else None,
        "plantingDate": (
            cycle.planting_date.isoformat()
            if cycle is not None and cycle.planting_date is not None else None
        ),
        "plantCount": str(cycle.plant_count) if cycle is not None and cycle.plant_count is not None else None,
        "expectedYieldFull": (
            str(cycle.expected_yield_full)
            if cycle is not None and cycle.expected_yield_full is not None else None
        ),
        "expectedYieldUnit": cycle.expected_yield_unit if cycle is not None else None,
        # Round 8-9B.1 — status ONLY (configured / not_configured). This
        # reference sheet carries no password, hash, digest or version.
        "inspectionPasswordStatus": _inspection_password_status(password_configured),
    }


def _current_snapshot_sheet(
    plots: list[Plot], credential_status: dict[UUID, tuple[bool, int]] | None = None,
) -> list[list[Cell]]:
    credential_status = credential_status or {}
    rows: list[list[Cell]] = [
        [StyledCell(h, _STYLE_HEADER) for h in _CURRENT_SNAPSHOT_HEADERS]
    ]
    for plot in plots:
        values = _current_snapshot_row_values(
            plot, password_configured=credential_status.get(plot.id, (False, 0))[0],
        )
        rows.append([values.get(h) for h in _CURRENT_SNAPSHOT_HEADERS])
    return rows


def _examples_sheet(supplier_code: str) -> list[list[Cell]]:
    """Sheet 4 ("ตัวอย่าง", Part E) — the same 3 worked examples the generic
    template ships (create/update/start_next_cycle), moved to their own sheet
    and styled red so they can never be mistaken for real rows and are never
    on the sheet the importer actually reads."""
    rows: list[list[Cell]] = [_header_row(), _description_row()]
    rows.append([StyledCell(_EXAMPLE_ONLY_NOTICE, _STYLE_EXAMPLE)])
    for ex in _template_example_rows(supplier_code):
        rows.append([
            StyledCell(ex.get(col), _STYLE_EXAMPLE) for col in _PLOT_TEMPLATE_HEADERS
        ])
    return rows


# --- Round 8-6G Part C: "รายการที่ไม่รวม" (excluded) sheet ------------------
# Read-only, informational: explains which plots were left OUT of Sheet 1 and
# why — an inactive plot (never allowed into an import sheet: it cannot
# start_next_cycle), or an active plot whose Supplier itself is inactive
# (all_suppliers mode only ever aggregates ACTIVE suppliers' plots). Never a
# sheet the importer reads, never carries an `action` column at all, so it
# cannot be executed by the importer even by accident.

_EXCLUDED_HEADERS: list[str] = [
    "supplierCode", "supplierName", "plotCode", "plotName", "province",
    "plotStatus", "cycleStatus", "cycleLabel", "exclusionReason",
    "inspectionPasswordStatus",
]

_CYCLE_STATUS_LABELS: dict[str, str] = {
    "cancelled": "ยกเลิก",
    "harvested": "เก็บเกี่ยวแล้ว",
    "active": "เปิดอยู่",
}
_CYCLE_STATUS_NONE_LABEL = "ไม่มีรอบที่เปิดอยู่"

_EXCLUSION_REASON_SUPPLIER_INACTIVE = "Supplier ปิดใช้งาน"
_EXCLUSION_REASON_PLOT_INACTIVE = "แปลงปิดใช้งาน ไม่สามารถเริ่มรอบปลูกใหม่ได้"
# Round 8-6J Part F — a plot excluded purely because the CALLER asked for one
# status and this plot is the other, never because being inactive is
# inherently disqualifying (that's only true for plotStatus='active', where
# an inactive plot genuinely can't start_next_cycle — same wording as
# _EXCLUSION_REASON_PLOT_INACTIVE above, kept as the plotStatus='all' has no
# such reason at all: see _exclusion_reason).
_EXCLUSION_REASON_STATUS_FILTER_INACTIVE = "ไม่รวมตามตัวกรองสถานะแปลง: ปิดใช้งาน"
_EXCLUSION_REASON_STATUS_FILTER_ACTIVE = "ไม่รวมตามตัวกรองสถานะแปลง: ใช้งาน"

# Round 8-6J — shared Thai labels for the currentPlotStatus column (Part C)
# and the excluded sheet's plotStatus column (pre-existing, Part C of round
# 8-6G) — one place so the two can never drift apart.
_CURRENT_PLOT_STATUS_ACTIVE_LABEL = "ใช้งานอยู่"
_CURRENT_PLOT_STATUS_INACTIVE_LABEL = "ปิดใช้งาน"


def _latest_cycle(plot: Plot):
    """The plot's most recent รอบปลูก regardless of status (cancelled/
    harvested/active) — for the excluded sheet's cycleStatus/cycleLabel
    columns only. Reads `plot.cycles` (the full history relationship), never
    `plot.active_cycle` (filtered to status='active', which an excluded plot
    almost never has). None when the plot has never had a cycle at all."""
    cycles = plot.cycles or []
    if not cycles:
        return None
    return max(cycles, key=lambda c: c.cycle_no)


def _exclusion_reason(plot: Plot, plot_status: str = "all") -> str:
    """Round 8-6J Part F — Supplier-inactive always wins (unrelated to the
    plotStatus filter); otherwise the reason depends on which single status
    the caller asked for. plotStatus='all' never reaches the two status-
    filter branches at all, because _fetch_excluded_plots's own WHERE clause
    for 'all' never selects a plot for being merely active/inactive — so
    _EXCLUSION_REASON_PLOT_INACTIVE is a defensive fallback that should be
    unreachable in normal operation, kept only so this function is still
    total."""
    if plot.supplier is not None and not plot.supplier.is_active:
        return _EXCLUSION_REASON_SUPPLIER_INACTIVE
    if plot_status == "active" and not plot.is_active:
        return _EXCLUSION_REASON_STATUS_FILTER_INACTIVE
    if plot_status == "inactive" and plot.is_active:
        return _EXCLUSION_REASON_STATUS_FILTER_ACTIVE
    return _EXCLUSION_REASON_PLOT_INACTIVE


def _excluded_row_values(
    plot: Plot, plot_status: str = "all", *, password_configured: bool = False,
) -> dict[str, str | None]:
    cycle = _latest_cycle(plot)
    cycle_status = (
        _CYCLE_STATUS_LABELS.get(cycle.status, cycle.status) if cycle is not None
        else _CYCLE_STATUS_NONE_LABEL
    )
    return {
        "supplierCode": plot.supplier.code if plot.supplier is not None else None,
        "supplierName": plot.supplier.name if plot.supplier is not None else None,
        "plotCode": plot.plot_code,
        "plotName": plot.name,
        "province": plot.province,
        "plotStatus": _CURRENT_PLOT_STATUS_ACTIVE_LABEL if plot.is_active else _CURRENT_PLOT_STATUS_INACTIVE_LABEL,
        "cycleStatus": cycle_status,
        "cycleLabel": cycle.cycle_label if cycle is not None else None,
        "exclusionReason": _exclusion_reason(plot, plot_status),
        # Round 8-9B.1 — status only, same rule as every other sheet.
        "inspectionPasswordStatus": _inspection_password_status(password_configured),
    }


def _excluded_sheet(
    plots: list[Plot], plot_status: str = "all",
    credential_status: dict[UUID, tuple[bool, int]] | None = None,
) -> list[list[Cell]]:
    credential_status = credential_status or {}
    rows: list[list[Cell]] = [
        [StyledCell(h, _STYLE_HEADER) for h in _EXCLUDED_HEADERS],
        [StyledCell(_EXCLUDED_ONLY_NOTICE, _STYLE_EXCLUDED_NOTICE)],
    ]
    for plot in plots:
        values = _excluded_row_values(
            plot, plot_status,
            password_configured=credential_status.get(plot.id, (False, 0))[0],
        )
        rows.append([StyledCell(values.get(h), _STYLE_EXCLUDED_ROW) for h in _EXCLUDED_HEADERS])
    return rows


def _contextual_plot_template_workbook(
    plots: list[Plot],
    excluded_plots: list[Plot] | None = None,
    latest_cycles: dict[UUID, PlotCycle] | None = None,
    plot_status: str = "all",
    credential_status: dict[UUID, tuple[bool, int]] | None = None,
) -> bytes:
    """Build the round 8-6A/8-6G/8-6J/8-7A 4-sheet contextual template (Part
    C). Sheet order is load-bearing: excel_reader.read_first_sheet only ever
    reads whichever sheet is written as sheet1.xml, which build_xlsx always
    assigns to the FIRST tuple in the list passed to it — so "นำเข้ารอบใหม่"
    must stay first. `excluded_plots` defaults to none (an empty "รายการที่ไม่
    รวม" sheet, just the header + notice rows) for any caller that hasn't
    wired the excluded-plots query. `latest_cycles` (round 8-6J) seeds an
    INACTIVE plot's row from its most recent historical cycle — see
    _reactivate_row_values. `plot_status` (round 8-6J) only affects the
    excluded sheet's exclusionReason wording — Sheet 1 itself dispatches
    purely on each plot's own is_active (_new_cycle_sheet).

    Round 8-10B removed the `latest_active_records` parameter along with the
    finalInspectionRecordId column it fed: the importer resolves that record
    itself, so a template download no longer queries for it."""
    supplier_code = plots[0].supplier.code if plots and plots[0].supplier is not None else "SUP001"
    return build_xlsx([
        (
            _SHEET_NEW_CYCLE,
            _new_cycle_sheet(plots, latest_cycles, credential_status),
        ),
        (_SHEET_CURRENT_SNAPSHOT, _current_snapshot_sheet(plots, credential_status)),
        (_SHEET_EXCLUDED, _excluded_sheet(excluded_plots or [], plot_status, credential_status)),
        (_SHEET_EXAMPLES, _examples_sheet(supplier_code)),
    ])


PlotStatusFilter = Literal["all", "active", "inactive"]


def _check_plot_status_conflict(plot_status: PlotStatusFilter, active_only: bool) -> None:
    """Round 8-6I Part B — the one combination that's genuinely contradictory:
    active_only=True (caller explicitly wants active-only) together with
    plot_status='inactive' (caller explicitly wants inactive-only). Every
    other combination (including active_only=True + plot_status='active',
    which is merely redundant) is allowed to proceed. Literal[...] on the
    query param itself already rejects any other string value with a 422
    before this ever runs."""
    if active_only and plot_status == "inactive":
        raise HTTPException(
            status_code=422,
            detail="active_only=true ขัดแย้งกับ plot_status=inactive",
        )


@router.get("", response_model=list[PlotSummary], dependencies=[
    Depends(require_permission(PermissionKey.PLOTS_READ)),
    Depends(get_rls_context),
])
async def list_plots(
    db: AsyncSession = Depends(get_db),
    supplier_id: UUID | None = None,
    province: str | None = None,
    crop: str | None = None,
    variety: str | None = None,
    limit: int = 50,
    offset: int = 0,
    q: str | None = None,
    active_only: bool = False,
    plot_status: PlotStatusFilter = "all",
    cycle_label: str | None = None,
    planting_date_from: date | None = None,
    planting_date_to: date | None = None,
) -> list[PlotSummary]:
    _check_plot_status_conflict(plot_status, active_only)
    plots = await repo.list_plots(
        db,
        supplier_id=supplier_id,
        province=province,
        crop=crop,
        variety=variety,
        limit=limit,
        offset=offset,
        q=q,
        active_only=active_only,
        plot_status=plot_status,
        cycle_label=cycle_label,
        planting_date_from=planting_date_from,
        planting_date_to=planting_date_to,
    )
    return [_to_summary(p) for p in plots]


@router.get("/provinces", response_model=list[str], dependencies=[
    Depends(require_permission(PermissionKey.PLOTS_READ)),
    Depends(get_rls_context),
])
async def list_plot_provinces(
    db: AsyncSession = Depends(get_db),
    supplier_id: UUID | None = None,
    active_only: bool = False,
    plot_status: PlotStatusFilter = "all",
) -> list[str]:
    _check_plot_status_conflict(plot_status, active_only)
    return await repo.list_plot_provinces(
        db, supplier_id=supplier_id, active_only=active_only, plot_status=plot_status,
    )


@router.get("/cycle-labels", response_model=list[str], dependencies=[
    Depends(require_permission(PermissionKey.PLOTS_READ)),
    Depends(get_rls_context),
])
async def list_plot_cycle_labels(
    db: AsyncSession = Depends(get_db),
    supplier_id: UUID | None = None,
    plot_status: PlotStatusFilter = "all",
) -> list[str]:
    """"รอบปลูกปัจจุบัน" filter's own dropdown source — distinct cycleLabel
    values from ACTIVE PlotCycle rows only, within the caller's scope
    (RLS + optional supplier_id/plot_status narrowing), sorted. Same
    plots.read + RLS wiring as GET /plots/provinces — no scope widening.
    """
    return await repo.list_plot_cycle_labels(
        db, supplier_id=supplier_id, plot_status=plot_status,
    )


# Round 8-18B.1 — bounds for the ADMIN partial access-number lookup. The
# lower bound keeps a 1-3 digit fragment (which would match a large share of
# every number in scope) from being a usable enumeration probe; the upper
# bound is simply a full Thai mobile. /public/inspect is unaffected — it
# still requires the complete number via normalize_thai_mobile.
_PHONE_SEARCH_MIN_DIGITS = 4
_PHONE_SEARCH_MAX_DIGITS = 10


@router.post("/search-by-phone", response_model=list[PlotSummary], dependencies=[
    Depends(require_permission(PermissionKey.PLOTS_READ)),
    Depends(get_rls_context),
])
async def search_plots_by_phone(
    payload: PlotPhoneSearchRequest,
    response: Response,
    db: AsyncSession = Depends(get_db),
) -> list[PlotSummary]:
    """Round 8-17A.2 — secure search by a plot's access phone (primary OR
    additional; both carry equal search rights, same as their equal
    inspection rights). POST-only and body-only BY DESIGN: a phone in a GET
    query string (`?q=...`) lands verbatim in Uvicorn's access log on every
    request, which the existing GET /plots `q` free-text search already does
    for plot code/name — acceptable for those, never for a phone number.
    This endpoint exists specifically so a phone never has to take that path.

    Round 8-18B — the optional `q` (ชื่อแปลง/รหัสแปลง) narrows the SAME
    request as an intersection, so the Plots page's two search boxes can be
    used together without the phone ever leaving the body for a GET ?q=.

    Same plots.read + get_rls_context wiring as GET /plots — no scope
    widening; a Supplier-scoped caller still only sees their own supplier's
    plots (RLS enforces this transparently, same as every other list here).
    Response is the exact same PlotSummary shape as GET /plots — the
    frontend renders both result sets with the same table.

    `Cache-Control: no-store` — the previous searcher's plot list (which can
    include phone-derived data like who's authorized) must never be served
    from a shared/browser cache to a different caller.
    """
    response.headers["Cache-Control"] = "no-store"
    # Round 8-18B.1 — this admin lookup now takes a PARTIAL number (a
    # fragment such as "5552"), so normalize_thai_mobile — which by design
    # only accepts a complete canonical 10-digit Thai mobile — can no longer
    # be the validator here. It is still the validator for /public/inspect
    # (public_inspection_access.py), which is untouched: granting inspection
    # access continues to require the exact, full number.
    #
    # payload.phone is SkipValidation[str] (see PlotPhoneSearchRequest
    # docstring), so type AND shape must both be checked here, by hand, so
    # neither ever round-trips through Pydantic's auto-422 (which would echo
    # the value). Same generic message/status for every rejection reason —
    # the caller can't distinguish "too short" from "not a string" from
    # "contains letters".
    phone_raw = payload.phone
    if (
        not isinstance(phone_raw, str)
        or not phone_raw.isascii()
        or not phone_raw.isdigit()
        or not (_PHONE_SEARCH_MIN_DIGITS <= len(phone_raw) <= _PHONE_SEARCH_MAX_DIGITS)
    ):
        # Generic — never echo the rejected value (PII). Matches the
        # existing app.core.phone contract every other phone entry point
        # in this codebase already follows.
        #
        # Round 8-17B Part A — `headers=` here is required, not decorative:
        # raising HTTPException makes FastAPI build a brand-new JSONResponse
        # from the exception itself, NOT from the `response` object above —
        # the Cache-Control set on `response` a few lines up is silently
        # dropped for this (or any) raised-exception path unless it's
        # re-attached via the exception's own `headers`.
        raise HTTPException(
            status_code=422,
            detail="รูปแบบหมายเลขสำหรับเข้าตรวจไม่ถูกต้อง",
            headers={"Cache-Control": "no-store"},
        )
    plots = await repo.search_plots_by_phone(
        db,
        phone_raw,
        supplier_id=payload.supplier_id,
        province=payload.province,
        crop=payload.crop,
        variety=payload.variety,
        limit=payload.limit,
        offset=payload.offset,
        plot_status=payload.plot_status,
        cycle_label=payload.cycle_label,
        q=payload.q,
        planting_date_from=payload.planting_date_from,
        planting_date_to=payload.planting_date_to,
    )
    return [_to_summary(p) for p in plots]


_MAX_TEMPLATE_PLOTS = 5000

# Round 8-6G Part B — the only recognized explicit template_mode value.
# Deliberately NOT inferred from "no supplier_id" (that already means the
# pre-8-6A generic single-sheet template — backward compatibility this round
# must not break), so an all-suppliers download always has to be asked for
# by name.
_TEMPLATE_MODE_ALL_SUPPLIERS = "all_suppliers"


async def _list_active_plots_all_suppliers(
    db: AsyncSession, *, limit: int, plot_status: str = "all",
) -> list[Plot]:
    """Sheet 1 rows for template_mode=all_suppliers (Part B/C; plotStatus-
    aware round 8-6J): every plot of an ACTIVE Supplier, across the caller's
    whole scope (the caller is already confirmed scope == "all" before this
    is called — RLS on `plots`/`suppliers` still applies as defense-in-
    depth), narrowed by `plot_status` — 'all' (default) returns both active
    and inactive plots (each becomes a start_next_cycle or
    reactivate_plot_with_cycle Sheet-1 row respectively — see
    _new_cycle_sheet); 'active'/'inactive' narrow to exactly one. The
    Supplier itself must always be active regardless of plot_status — an
    inactive Supplier's plots never appear here at all (they surface on the
    excluded sheet instead, via _fetch_excluded_plots). Sorted by
    (Supplier.code, Plot.plot_code) at the DB level via a dedicated join
    (Part D) — never Supplier UUID, and never plot_repository.list_plots's
    own supplier_id-then-plot_code ordering, which stays untouched for every
    other caller."""
    stmt = (
        select(Plot)
        .join(Supplier, Plot.supplier_id == Supplier.id)
        .where(Supplier.is_active.is_(True))
        .options(
            selectinload(Plot.supplier),
            selectinload(Plot.active_cycle),
            selectinload(Plot.access_phones),
        )
        .order_by(Supplier.code.asc(), Plot.plot_code.asc())
        .limit(limit)
    )
    if plot_status == "active":
        stmt = stmt.where(Plot.is_active.is_(True))
    elif plot_status == "inactive":
        stmt = stmt.where(Plot.is_active.is_(False))
    result = await db.execute(stmt)
    return list(result.scalars().all())


async def _fetch_excluded_plots(
    db: AsyncSession,
    *,
    supplier_id: UUID | None,
    province: str | None = None,
    q: str | None = None,
    plot_status: str = "all",
    limit: int = _MAX_TEMPLATE_PLOTS,
) -> list[Plot]:
    """Plots for the "รายการที่ไม่รวม" sheet (Part C; plotStatus-aware round
    8-6J Part F): a Supplier-inactive plot always qualifies regardless of
    plot_status; ADDITIONALLY, a plot whose OWN status contradicts the
    caller's plotStatus filter (an inactive plot when plotStatus='active', or
    an active plot when plotStatus='inactive') — never merely "is inactive"
    when plotStatus='all', since round 8-6J puts both active and inactive
    plots into Sheet 1 in that case (_new_cycle_sheet's own is_active
    dispatch). `supplier_id=None` means "every supplier" (all_suppliers mode
    — the caller has already confirmed there's no other filter, per Part B).
    `province`/`q` are physical-plot filters (still meaningful for an
    inactive plot; round 8-18B — `q` is plot_code/name only, never province);
    crop/variety/cycle_label are deliberately NEVER applied
    here — they filter on the plot's active-cycle data, which an excluded
    plot (almost always with no active cycle) cannot meaningfully "match"
    (Part C; round 8-18 extends this same reasoning to cycle_label).
    Sorted the same way as the actionable query — a dedicated join, never
    plot_repository.list_plots.

    Capped at `limit` (default the same 5,000 as the actionable-plot cap) —
    unlike Sheet 1, silently capping this informational-only sheet is
    acceptable: nothing here is ever imported, so a cap here can never hide
    an importable row, only truncate how much "why was this excluded"
    context is shown for an implausibly large result.
    """
    stmt = (
        select(Plot)
        .join(Supplier, Plot.supplier_id == Supplier.id)
        .options(selectinload(Plot.supplier), selectinload(Plot.cycles))
        .order_by(Supplier.code.asc(), Plot.plot_code.asc())
        .limit(limit)
    )
    if plot_status == "active":
        stmt = stmt.where(or_(Plot.is_active.is_(False), Supplier.is_active.is_(False)))
    elif plot_status == "inactive":
        stmt = stmt.where(or_(Plot.is_active.is_(True), Supplier.is_active.is_(False)))
    else:  # "all" — never exclude for is_active alone, only a genuinely inactive Supplier
        stmt = stmt.where(Supplier.is_active.is_(False))
    if supplier_id is not None:
        stmt = stmt.where(Plot.supplier_id == supplier_id)
    if province:
        stmt = stmt.where(func.lower(Plot.province) == province.strip().lower())
    # Round 8-18B — the SAME plot_code/name-only helper Sheet 1 uses
    # (repo.apply_plot_text_filter), so this sheet can't disagree with the
    # actionable one about what `q` means.
    stmt = repo.apply_plot_text_filter(stmt, q=q)
    result = await db.execute(stmt)
    return list(result.scalars().all())


@router.get("/import-template", dependencies=[
    Depends(require_permission(PermissionKey.PLOTS_READ)),
    Depends(get_rls_context),
])
async def download_plot_import_template(
    current_user: CurrentUser,
    db: AsyncSession = Depends(get_db),
    supplier_id: UUID | None = None,
    province: str | None = None,
    crop: str | None = None,
    variety: str | None = None,
    q: str | None = None,
    template_mode: str | None = None,
    plot_status: PlotStatusFilter = "all",
    cycle_label: str | None = None,
) -> Response:
    """GET /plots/import-template (round 7.5) — round 8-6A adds optional
    Supplier/province/crop/variety/q filters matching the Plots page; round
    8-6G adds an explicit `template_mode=all_suppliers` for a full-access
    caller to download every active Supplier's actionable plots in one file;
    round 8-6J adds `plot_status` (default 'all') to BOTH the filtered and
    all_suppliers modes — 'all' seeds a start_next_cycle row for each active
    plot and a reactivate_plot_with_cycle row for each inactive one in the
    SAME Sheet 1; 'active'/'inactive' narrow to exactly one action. Round
    8-18 adds `cycle_label` ("รอบปลูกปัจจุบัน") alongside crop/variety — same
    active-cycle-only filter as GET /plots, same treatment in every mode
    below. Never applies to the no-filter generic template (mode 1 below),
    which stays byte-for-byte the pre-8-6A/8-6J behavior.

    Three mutually exclusive modes:

    1. No param at all → the EXACT pre-8-6A generic single-sheet template
       (path/permission unchanged) — never inferred, always backward
       compatible.
    2. `supplier_id` (+ optional province/crop/variety/cycle_label/q/
       plot_status) → the contextual workbook scoped to that one Supplier
       (round 8-6A/8-6B, unchanged filter semantics; round 8-6G adds the
       "รายการที่ไม่รวม" sheet — Part C — to explain which of that Supplier's
       plots were left out).
    3. `template_mode=all_suppliers` (+ optional plot_status) → the
       contextual workbook aggregated across every ACTIVE Supplier the
       caller's scope covers. Requires scope == "all" (checked via the SAME
       `_resolve_scope` helper get_rls_context/get_supplier_scope_filter
       use — never a new role decision); a supplier-scoped or
       assigned-scope caller gets 403. Mutually exclusive with
       supplier_id/province/crop/variety/cycle_label/q (422 if combined) —
       an explicit mode, never inferred from "no supplier_id".

    Every mode is read-only: downloading/Previewing never closes a cycle,
    reactivates a plot, or writes to the DB; only a later Commit of the
    edited file does (plot_import.commit_import, unchanged by this round).
    """
    if template_mode is not None and template_mode != _TEMPLATE_MODE_ALL_SUPPLIERS:
        raise HTTPException(
            status_code=422,
            detail=f"template_mode ไม่ถูกต้อง (รองรับเฉพาะ {_TEMPLATE_MODE_ALL_SUPPLIERS!r})",
        )

    if template_mode == _TEMPLATE_MODE_ALL_SUPPLIERS:
        if any(v is not None for v in (supplier_id, province, crop, variety, cycle_label, q)):
            raise HTTPException(
                status_code=422,
                detail="ไม่สามารถระบุ Supplier หรือตัวกรองอื่นพร้อมกับการดาวน์โหลดทุก Supplier ได้",
            )
        # Same scope helper every other full-access decision in this app
        # uses (get_rls_context/get_supplier_scope_filter) — never a
        # separate role check invented for this one endpoint.
        scope, _supplier_id = _resolve_scope(current_user, _role_names(current_user))
        if scope != "all":
            raise HTTPException(status_code=403, detail="ไม่มีสิทธิ์ดาวน์โหลด Excel ทุก Supplier")
        return await _all_suppliers_template_response(db, plot_status=plot_status)

    has_filter = any(v is not None for v in (supplier_id, province, crop, variety, cycle_label, q))
    if not has_filter:
        suppliers = await _template_suppliers(db, current_user)
        content = _plot_template_workbook(suppliers)
        return Response(
            content=content,
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers={
                "Content-Disposition": 'attachment; filename="plot-import-template.xlsx"',
                "Cache-Control": "no-store",
            },
        )

    if supplier_id is None:
        raise HTTPException(
            status_code=422,
            detail="กรุณาเลือก Supplier ก่อนดาวน์โหลด Excel ตามตัวกรอง",
        )

    # A generic 404 whether supplier_id doesn't exist at all or exists but is
    # outside this caller's scope — neither response may leak which case it
    # was. get_supplier_scope_filter reuses the exact same scope decision
    # every other Supplier-scoped read in this app uses; never re-derived here.
    scope_conditions = await get_supplier_scope_filter(current_user)
    supplier = (
        await db.execute(select(Supplier).where(Supplier.id == supplier_id, *scope_conditions))
    ).scalar_one_or_none()
    if supplier is None:
        raise HTTPException(status_code=404, detail="ไม่พบ Supplier")

    # Query one MORE than the cap to detect overflow without truncating
    # silently (Part A item 8). plot_status (round 8-6J, default "all") —
    # replaces the old hardcoded active_only=True: 'all' now lets an
    # inactive plot seed a reactivate_plot_with_cycle row instead of being
    # dropped. Same filter semantics as GET /plots (plot_repository.
    # list_plots), reused directly rather than re-implemented here.
    plots = await repo.list_plots(
        db,
        supplier_id=supplier_id,
        province=province,
        crop=crop,
        variety=variety,
        q=q,
        plot_status=plot_status,
        cycle_label=cycle_label,
        limit=_MAX_TEMPLATE_PLOTS + 1,
        offset=0,
    )
    if len(plots) > _MAX_TEMPLATE_PLOTS:
        raise HTTPException(
            status_code=422,
            detail=(
                f"พบแปลงเกิน {_MAX_TEMPLATE_PLOTS:,} รายการ "
                "กรุณากรอง Supplier/จังหวัด/พืชให้แคบลงก่อนดาวน์โหลด"
            ),
        )
    if not plots:
        raise HTTPException(
            status_code=422,
            detail="ไม่พบแปลงตรงตามตัวกรองที่ระบุ",
        )

    # Round 8-6G Part C — the same Supplier's plots that DIDN'T make Sheet 1
    # (crop/variety are deliberately never applied here — see
    # _fetch_excluded_plots's docstring). Round 8-6J — plot_status forwarded
    # so the exclusion reason matches what was actually asked for.
    excluded_plots = await _fetch_excluded_plots(
        db, supplier_id=supplier_id, province=province, q=q, plot_status=plot_status,
    )
    # Round 8-6J Part D — batch-load the latest historical cycle for every
    # INACTIVE plot in Sheet 1 (one query, never N+1), so
    # _reactivate_row_values has real starting data to pre-fill.
    inactive_plot_ids = [p.id for p in plots if not p.is_active]
    latest_cycles = (
        await plot_cycle_repo.get_latest_cycles_for_plots(db, inactive_plot_ids)
        if inactive_plot_ids else {}
    )
    # Round 8-7A — batch-load the latest ACTIVE record for every ACTIVE
    # plot's active cycle (one query, never N+1), so an active-plot row can
    # be repurposed for final_plot with finalInspectionRecordId pre-filled.
    # Round 8-9B.1 — ONE query for every plot on every sheet (Sheet 1 +
    # snapshot + excluded), never per plot. Only the configured boolean is
    # ever written into a cell; no password/hash/digest/version is exported.
    credential_status = await credential_repo.get_credential_status_for_plots(
        db, [p.id for p in [*plots, *excluded_plots]],
    )
    content = _contextual_plot_template_workbook(
        plots, excluded_plots, latest_cycles=latest_cycles, plot_status=plot_status,
        credential_status=credential_status,
    )
    return Response(
        content=content,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={
            "Content-Disposition": 'attachment; filename="plot-import-template.xlsx"',
            "Cache-Control": "no-store",
        },
    )


async def _all_suppliers_template_response(db: AsyncSession, *, plot_status: str = "all") -> Response:
    """template_mode=all_suppliers (Part B/C; plotStatus-aware round 8-6J):
    every plot (matching plot_status) of every ACTIVE Supplier in Sheet 1,
    that same aggregate's excluded plots (Supplier-inactive, or contradicting
    a non-'all' plot_status) in "รายการที่ไม่รวม". Caller has already
    confirmed scope == "all" — this never re-checks or narrows by supplier,
    since 'all' means exactly that."""
    plots = await _list_active_plots_all_suppliers(db, limit=_MAX_TEMPLATE_PLOTS + 1, plot_status=plot_status)
    if len(plots) > _MAX_TEMPLATE_PLOTS:
        raise HTTPException(
            status_code=422,
            detail=(
                f"พบแปลงเกิน {_MAX_TEMPLATE_PLOTS:,} รายการ "
                "กรุณาใช้การดาวน์โหลดตามตัวกรองแทน"
            ),
        )
    if not plots:
        raise HTTPException(
            status_code=422,
            detail="ไม่พบแปลงตรงตามตัวกรองสถานะแปลงของ Supplier ที่ใช้งานอยู่",
        )

    excluded_plots = await _fetch_excluded_plots(db, supplier_id=None, plot_status=plot_status)
    inactive_plot_ids = [p.id for p in plots if not p.is_active]
    latest_cycles = (
        await plot_cycle_repo.get_latest_cycles_for_plots(db, inactive_plot_ids)
        if inactive_plot_ids else {}
    )
    # Round 8-9B.1 — ONE query for every plot on every sheet (Sheet 1 +
    # snapshot + excluded), never per plot. Only the configured boolean is
    # ever written into a cell; no password/hash/digest/version is exported.
    credential_status = await credential_repo.get_credential_status_for_plots(
        db, [p.id for p in [*plots, *excluded_plots]],
    )
    content = _contextual_plot_template_workbook(
        plots, excluded_plots, latest_cycles=latest_cycles, plot_status=plot_status,
        credential_status=credential_status,
    )
    return Response(
        content=content,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={
            "Content-Disposition": 'attachment; filename="plot-import-template.xlsx"',
            "Cache-Control": "no-store",
        },
    )


# --- Plot + cycle Excel import (round 7.5) --------------------------------
# Two endpoints, both under /import/ so they register before /{plot_id} (a
# path segment "import" would otherwise be parsed as a plot UUID). Gated by
# ANY of plots.create/plots.update; the service additionally checks per-action
# that the caller actually holds the permission that row's action needs.
_IMPORT_MAX_BYTES = 2 * 1024 * 1024  # 2 MB — an admin plot import is small


async def _read_import_upload(file: UploadFile) -> bytes:
    if not (file.filename or "").lower().endswith(".xlsx"):
        raise HTTPException(status_code=422, detail="รองรับเฉพาะไฟล์ .xlsx เท่านั้น")
    content = await file.read()
    if not content:
        raise HTTPException(status_code=422, detail="ไฟล์ว่างเปล่า")
    if len(content) > _IMPORT_MAX_BYTES:
        raise HTTPException(status_code=422, detail="ไฟล์ใหญ่เกินไป (สูงสุด 2 MB)")
    return content


def _build_import_ctx(current_user: User) -> plot_import.ImportContext:
    role_names = {r.name for r in current_user.roles}
    scope, scope_supplier_id = _resolve_scope(current_user, role_names)
    # Only full-access and single-supplier callers may import plots. Field
    # officers ('assigned') and unscoped users never manage plots this way.
    if scope not in ("all", "supplier"):
        raise HTTPException(status_code=403, detail="ไม่มีสิทธิ์นำเข้าแปลง")
    perms: set[str] = getattr(current_user, "_effective_permissions", set())
    return plot_import.ImportContext(
        allowed_supplier_id=UUID(scope_supplier_id) if scope == "supplier" else None,
        can_create=PermissionKey.PLOTS_CREATE in perms,
        can_update=PermissionKey.PLOTS_UPDATE in perms,
        # Round 8-6H — the activation privilege for reactivate_plot_with_cycle
        # rows, same permission the reactivate API endpoints require.
        can_reactivate=PermissionKey.PLOTS_DELETE in perms,
        user_id=current_user.id,
    )


def _parse_preview_state(raw: str | None) -> PlotImportPreviewState | None:
    """Parse the optional multipart previewState field (round 8-2.7.2;
    input-boundary hardened round 8-7A.2). Absent/blank → None (legacy path;
    the service enforces "required for start_next_cycle/final_plot files").
    Malformed JSON, an oversized snapshot, or one with duplicate row numbers
    → 422 with a short, generic message — never a stack trace, and never the
    raw previewState/UUIDs/supplierCode/plotCode echoed back.

    This is purely an INPUT-BOUNDARY check (shape/size), never an
    authorization or state check: previewState is not a credential, and the
    service layer's under-lock verification (_verify_start_next_snapshot /
    _verify_final_plot_snapshot, round 8-7A.1) remains the sole authority on
    whether a row's resolution is still valid at commit time.
    """
    if raw is None or raw.strip() == "":
        return None
    try:
        state = PlotImportPreviewState.model_validate_json(raw)
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail="previewState ไม่ถูกต้อง") from exc
    # Round 8-7A.2 — start_next_rows and final_plot_rows are two mutually
    # exclusive subsets of the SAME uploaded file (one row resolves to at most
    # one of the two actions), and that file itself is capped at
    # MAX_IMPORT_ROWS data rows — so checking their COMBINED length against
    # that same cap is both necessary and sufficient. It strictly implies
    # each list is individually within the cap too (a non-negative second
    # list can only push the sum up, never down), so no separate per-list
    # check is needed on top of this one.
    if len(state.start_next_rows) + len(state.final_plot_rows) > plot_import.MAX_IMPORT_ROWS:
        raise HTTPException(status_code=422, detail="previewState มีจำนวนแถวเกินกำหนด")
    # Round 8-7A.2 — rowNumber must be unique across BOTH lists combined (a
    # duplicate within one list, or the same number appearing in both, is
    # equally a malformed snapshot the service should never have to reason
    # about under lock).
    row_numbers = [r.row_number for r in state.start_next_rows]
    row_numbers += [r.row_number for r in state.final_plot_rows]
    if len(row_numbers) != len(set(row_numbers)):
        raise HTTPException(status_code=422, detail="previewState มีเลขแถวซ้ำกัน")
    return state


def _preview_state_conflict_http(
    exc: plot_import.ImportPreviewStateConflict,
) -> HTTPException:
    """Round 8-2.7.2: a preview-state conflict is an optimistic-concurrency
    failure (409) — nothing was written. The machine-readable `code` lets the
    frontend show the right message and disable Commit until the user Previews
    again, without parsing Thai text; `changedRows` is advisory only."""
    return HTTPException(
        status_code=409,
        detail={
            "code": plot_import.PREVIEW_STATE_CONFLICT_CODE,
            "reason": exc.reason,
            "message": exc.message,
            "changedRows": exc.changed_rows,
        },
    )


@router.post("/import/preview", response_model=PlotImportPreview, dependencies=[
    Depends(require_any_permission(PermissionKey.PLOTS_CREATE, PermissionKey.PLOTS_UPDATE)),
    Depends(get_rls_context),
])
async def preview_plot_import(
    current_user: CurrentUser,
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
) -> PlotImportPreview:
    """Parse + validate every row, WITHOUT writing anything. Safe/read-only."""
    content = await _read_import_upload(file)
    ctx = _build_import_ctx(current_user)
    try:
        return await plot_import.build_preview(db, content, ctx=ctx)
    except plot_import.ImportFileError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/import/commit", response_model=PlotImportCommitResult, dependencies=[
    Depends(require_any_permission(PermissionKey.PLOTS_CREATE, PermissionKey.PLOTS_UPDATE)),
    Depends(get_rls_context),
])
async def commit_plot_import(
    current_user: CurrentUser,
    file: UploadFile = File(...),
    # Round 8-6F — explicit alias: every other API field on the wire is
    # camelCase (AGENTS.md §12; CamelBaseModel elsewhere in this app), and
    # the frontend sends the multipart field as "previewState". Form(None)
    # with NO alias bound to the literal name "preview_state" instead, so a
    # real "previewState" field was silently dropped to None — every
    # start_next_cycle commit hit missing_preview_state regardless of the
    # round 8-6E race fix. The Python parameter name stays preview_state
    # (snake_case, unaffected) — only the wire-facing alias changes.
    preview_state: str | None = Form(None, alias="previewState"),
    db: AsyncSession = Depends(get_db),
) -> PlotImportCommitResult:
    """Re-validate server-side (never trusting a client preview) and execute
    every row in ONE transaction. Any invalid row → 422 with the full preview,
    nothing written. A concurrent conflict (e.g. a cycle opened between preview
    and commit) surfaces as a clean 409 with the whole file rolled back.

    Round 8-2.7.2: a file containing start_next_cycle rows must carry the
    previewState the user approved (multipart field); a stale digest or a
    diverged resolution → 409 before any mutation."""
    content = await _read_import_upload(file)
    ctx = _build_import_ctx(current_user)
    parsed_state = _parse_preview_state(preview_state)
    try:
        return await plot_import.commit_import(db, content, ctx=ctx, preview_state=parsed_state)
    except plot_import.ImportHasErrors as exc:
        raise HTTPException(
            status_code=422,
            detail={
                "message": "พบข้อผิดพลาดในบางแถว — ไม่ได้บันทึกข้อมูลใด ๆ",
                "preview": exc.preview.model_dump(by_alias=True, mode="json"),
            },
        ) from exc
    except plot_import.ImportPreviewStateConflict as exc:
        raise _preview_state_conflict_http(exc) from exc
    except plot_import.ImportFileError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except IntegrityError as exc:
        raise HTTPException(
            status_code=409,
            detail="นำเข้าไม่สำเร็จเนื่องจากมีการเปลี่ยนแปลงที่ขัดแย้ง — ไม่ได้บันทึกข้อมูลใด ๆ",
        ) from exc


# --- Excel result-workbook variants (round 8-2.4) -------------------------
# Same permission/RLS/upload guards + same import services as the JSON
# endpoints above; these just render an .xlsx result file instead of JSON. The
# JSON endpoints are unchanged. Bytes are built in memory (no temp file, no
# streaming) so the transaction lifecycle stays exactly the get_db one.

def _xlsx_response(content: bytes, filename: str, *, status_code: int = 200) -> Response:
    # filename is server-generated (fixed prefix + timestamp) — never derived
    # from the user's upload name, so it is always header-safe.
    return Response(
        content=content,
        status_code=status_code,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Cache-Control": "no-store",
        },
    )


@router.post("/import/preview-report", dependencies=[
    Depends(require_any_permission(PermissionKey.PLOTS_CREATE, PermissionKey.PLOTS_UPDATE)),
    Depends(get_rls_context),
])
async def preview_plot_import_report(
    current_user: CurrentUser,
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
) -> Response:
    """Read-only: validate the file (same core as /import/preview) and return an
    .xlsx validation report — never writes. Returns 200 even when rows have
    errors (the workbook itself carries per-row READY/ERROR/DUPLICATE)."""
    content = await _read_import_upload(file)
    ctx = _build_import_ctx(current_user)
    try:
        states = await plot_import.preview_states(db, content, ctx=ctx)
    except plot_import.ImportFileError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    views = [plot_import.report_row_view(s) for s in states]
    processed_at = datetime.now(timezone.utc)
    workbook = plot_import_report.build_plot_import_result_workbook(
        views, phase=plot_import_report.PHASE_PREVIEW, completed=False,
        original_filename=file.filename, processed_at=processed_at,
    )
    return _xlsx_response(
        workbook,
        plot_import_report.result_filename(plot_import_report.PHASE_PREVIEW, processed_at),
    )


@router.post("/import/commit-report", dependencies=[
    Depends(require_any_permission(PermissionKey.PLOTS_CREATE, PermissionKey.PLOTS_UPDATE)),
    Depends(get_rls_context),
])
async def commit_plot_import_report(
    current_user: CurrentUser,
    file: UploadFile = File(...),
    # Round 8-6F — explicit alias: every other API field on the wire is
    # camelCase (AGENTS.md §12; CamelBaseModel elsewhere in this app), and
    # the frontend sends the multipart field as "previewState". Form(None)
    # with NO alias bound to the literal name "preview_state" instead, so a
    # real "previewState" field was silently dropped to None — every
    # start_next_cycle commit hit missing_preview_state regardless of the
    # round 8-6E race fix. The Python parameter name stays preview_state
    # (snake_case, unaffected) — only the wire-facing alias changes.
    preview_state: str | None = Form(None, alias="previewState"),
    db: AsyncSession = Depends(get_db),
) -> Response:
    """Re-validate + commit ONCE (same core as /import/commit), then return a
    COMPLETED .xlsx result file. If any row is invalid, nothing is written and a
    BLOCKED validation workbook is returned with HTTP 422. An unexpected DB
    error propagates so get_db rolls the whole file back (never a partial-flush
    workbook).

    Round 8-2.7.2: a start_next_cycle file must carry the approved previewState
    (multipart field). A stale digest or a diverged resolution → 409 JSON
    (never a COMPLETED workbook, since no mutation happened)."""
    content = await _read_import_upload(file)
    ctx = _build_import_ctx(current_user)
    parsed_state = _parse_preview_state(preview_state)
    processed_at = datetime.now(timezone.utc)
    try:
        states = await plot_import.commit_import_execute(
            db, content, ctx=ctx, preview_state=parsed_state,
        )
    except plot_import.ImportHasErrors as exc:
        # Validation failed BEFORE any mutation — all-or-nothing, nothing
        # written. Return the validation workbook (not COMPLETED) with 422.
        views = [plot_import.report_row_view(s) for s in exc.states]
        workbook = plot_import_report.build_plot_import_result_workbook(
            views, phase=plot_import_report.PHASE_COMMIT, completed=False,
            original_filename=file.filename, processed_at=processed_at,
        )
        return _xlsx_response(
            workbook,
            plot_import_report.result_filename(plot_import_report.PHASE_PREVIEW, processed_at),
            status_code=422,
        )
    except plot_import.ImportPreviewStateConflict as exc:
        # State/file diverged from the approved preview — nothing written. A
        # clean 409 JSON (never a COMPLETED workbook): the frontend shows the
        # "please Preview again" message and disables Commit until it does.
        raise _preview_state_conflict_http(exc) from exc
    except plot_import.ImportFileError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except IntegrityError as exc:
        raise HTTPException(
            status_code=409,
            detail="นำเข้าไม่สำเร็จเนื่องจากมีการเปลี่ยนแปลงที่ขัดแย้ง — ไม่ได้บันทึกข้อมูลใด ๆ",
        ) from exc
    views = [plot_import.report_row_view(s) for s in states]
    workbook = plot_import_report.build_plot_import_result_workbook(
        views, phase=plot_import_report.PHASE_COMMIT, completed=True,
        original_filename=file.filename, processed_at=processed_at,
    )
    return _xlsx_response(
        workbook,
        plot_import_report.result_filename(plot_import_report.PHASE_COMMIT, processed_at),
    )


@router.post("", response_model=PlotRead, status_code=status.HTTP_201_CREATED,
             dependencies=[
                 Depends(require_permission(PermissionKey.PLOTS_CREATE)),
                 Depends(get_rls_context),
             ])
async def create_plot(
    payload: PlotCreate,
    current_user: CurrentUser,
    db: AsyncSession = Depends(get_db),
) -> PlotRead:
    # Physical-plot-only (round 8.0.4 — PlotCreate no longer carries
    # planting-cycle/yield-plan fields at all). For creating a plot that's
    # ready to inspect immediately, use POST /plots/with-cycle instead; this
    # endpoint stays for the deliberate "reserve a plot, start its first
    # cycle later" case, so the plot sits in a "รอเริ่มรอบปลูก" state.
    #
    # Supplier self-service: a supplier-scoped caller (supplier:owner) may
    # only create plots for their OWN supplier. RLS's WITH CHECK would also
    # reject a foreign supplier_id, but only as an opaque DB error — and
    # only when running as the non-BYPASSRLS role — so enforce it here for
    # a clean 403 that doesn't depend on the connection role.
    role_names = {r.name for r in current_user.roles}
    scope, scope_supplier_id = _resolve_scope(current_user, role_names)
    if scope == "supplier" and str(payload.supplier_id) != scope_supplier_id:
        raise HTTPException(status_code=403, detail="Cannot create a plot for another supplier")

    existing = await repo.get_plot_by_code(db, payload.supplier_id, payload.plot_code)
    if existing is not None:
        raise HTTPException(status_code=409, detail="Plot code already exists for this supplier")
    plot = await repo.create_plot(db, payload)
    return _to_read(plot)


@router.post(
    "/with-cycle",
    response_model=PlotWithCycleCreateResult,
    status_code=status.HTTP_201_CREATED,
    dependencies=[
        Depends(require_permission(PermissionKey.PLOTS_CREATE)),
        Depends(get_rls_context),
    ],
)
async def create_plot_with_cycle(
    payload: PlotWithCycleCreate,
    current_user: CurrentUser,
    db: AsyncSession = Depends(get_db),
) -> PlotWithCycleCreateResult:
    """Atomically create a physical Plot AND its first active PlotCycle
    (round 8.0.4) — the counterpart of plain POST /plots (still
    physical-only, for reserving a plot with no cycle yet). Logged-in only,
    same plots.create permission as POST /plots; no public equivalent.

    One get_db transaction covers both inserts: plot_repository.create_plot
    and plot_cycle_repository.create_cycle each only flush, so if the cycle
    insert fails (e.g. a concurrent import wins the plot_code race — the
    partial unique index/uq_plots_supplier_code backstop), the whole request
    rolls back and the plot is never left stranded with no cycle. The QR key
    is generated once, inside create_plot, exactly like plain POST /plots —
    this schema has no qrKey field for a client to supply.

    Round 8-17C — an invalid optional accessPhones is validated by hand,
    first thing, same fail-fast position Pydantic's automatic validation
    used to run at (before any DB call) — see
    normalize_and_validate_phone_config.
    """
    if payload.access_phones is not None:
        try:
            normalize_and_validate_phone_config(payload.access_phones)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from None
    role_names = {r.name for r in current_user.roles}
    scope, scope_supplier_id = _resolve_scope(current_user, role_names)
    if scope == "supplier" and str(payload.plot.supplier_id) != scope_supplier_id:
        raise HTTPException(status_code=403, detail="Cannot create a plot for another supplier")

    existing = await repo.get_plot_by_code(db, payload.plot.supplier_id, payload.plot.plot_code)
    if existing is not None:
        raise HTTPException(status_code=409, detail="Plot code already exists for this supplier")

    nc = payload.cycle
    # Round 8-15D — a brand-new cycle's crop/variety (if given) must exist and
    # be active in Master Data; a variety must belong to the chosen crop.
    await master_data_validation.assert_crop_variety_valid(db, nc.crop, nc.variety)
    started_at = (
        datetime.combine(nc.planting_date, time.min, tzinfo=timezone.utc)
        if nc.planting_date is not None
        else None
    )
    try:
        plot = await repo.create_plot(db, payload.plot)
        cycle = await plot_cycle_repo.create_cycle(
            db, plot,
            crop=nc.crop, variety=nc.variety, cycle_label=nc.cycle_label,
            lot_no=nc.lot_no, po_number=nc.po_number, p_code=nc.p_code,
            supplier_lot_no=nc.supplier_lot_no,
            oracle_supplier_code=nc.oracle_supplier_code, oracle_invoice=nc.oracle_invoice,
            ref_account=nc.ref_account,
            planting_date=nc.planting_date,
            plant_count=nc.plant_count,
            expected_yield_full=nc.expected_yield_full,
            expected_yield_unit=nc.expected_yield_unit,
            started_at=started_at,
        )
        # Optional access phones (round 8-3A) — inside the SAME try/transaction,
        # so a phone-config failure rolls the plot AND cycle back too (the plot
        # is never stranded). Omitted → skipped, behavior exactly as before. The
        # plot is brand-new and owned by this transaction, so no separate Plot
        # lock is needed (nothing else can see it yet).
        if payload.access_phones is not None:
            await phone_repo.replace_plot_access_phones(db, plot, payload.access_phones)
    except AutoLotMissingComponentError as exc:
        # Round 8-12A.1 — a blank lotNo asks for an Auto Lot but a V2 component
        # is missing. Clean 422 (never a 500, never an active cycle with a NULL
        # lot); the transaction rolls back so nothing is written.
        raise HTTPException(
            status_code=422, detail=_auto_lot_missing_detail(exc.missing),
        ) from exc
    except LotNumberTooLongError as exc:
        # Auto Lot would exceed lot_no's 100-char limit — a clean 422 (never a
        # 500, never a truncated lot). Round 8-5A; V2 formula since 8-12A.
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except IntegrityError as exc:
        raise HTTPException(
            status_code=409, detail="Conflict creating the plot or its first planting cycle"
        ) from exc

    # A freshly-inserted plot has no inspection snapshot yet (all those
    # columns default to NULL) — no clear_plot_inspection_snapshot call
    # needed, unlike start_plot_cycle which reuses an existing plot that may
    # carry a previous cycle's snapshot. Re-load both rows before serialising
    # (round 7.7 MissingGreenlet pattern, fixed for THIS endpoint round
    # 8-17A.1): create_cycle's sync_plot_mirror_from_cycle UPDATEs the plot
    # row (mirror columns), which expires plot's server-side onupdate column
    # (updated_at, via TimestampMixin) — SQLAlchemy cannot know the new
    # server-computed value without a re-SELECT. A plain `db.refresh(plot,
    # attribute_names=[...])` only reloads the NAMED attributes, never the
    # scalar columns it omits, so `updated_at` stayed expired and
    # PlotRead.model_validate's read of it lazy-loaded outside the request's
    # greenlet → 500 MissingGreenlet. The scalar refresh (no attribute_names)
    # must run FIRST; same order as reactivate_plot_with_cycle below, which
    # this endpoint's original round 7.7 comment described but didn't apply.
    # access_phones is refreshed too so PlotRead's primary/additionalPhones
    # reflect any just-created config (round 8-3A).
    await db.refresh(plot)
    await db.refresh(
        plot, attribute_names=["assignments", "supplier", "active_cycle", "access_phones"]
    )
    await db.refresh(cycle)
    return PlotWithCycleCreateResult(plot=_to_read(plot), cycle=PlotCycleRead.model_validate(cycle))


@router.get("/lookup", response_model=PlotLookupRead, dependencies=[
    Depends(require_any_permission(PermissionKey.PLOTS_READ, PermissionKey.RECORDS_CREATE)),
    Depends(get_rls_context),
])
async def lookup_plot(
    supplier_code: str = Query(..., alias="supplierCode", min_length=1, max_length=50),
    plot_code: str = Query(..., alias="plotCode", min_length=1, max_length=50),
    db: AsyncSession = Depends(get_db),
) -> PlotLookupRead:
    """QR-scan lookup for the "บันทึกการตรวจแปลงใหม่" auto-fill flow.

    404 is intentionally the same generic message whether the supplier code
    doesn't exist, the plot code doesn't exist under that supplier, either is
    inactive, or the plot exists but is outside the caller's RLS scope — so
    the response can't be used to enumerate supplier/plot codes.
    """
    supplier = await supplier_repo.get_supplier_by_code(db, supplier_code)
    plot = None
    if supplier is not None and supplier.is_active:
        plot = await repo.get_plot_by_code(db, supplier.id, plot_code)
    if plot is None or not plot.is_active:
        raise HTTPException(status_code=404, detail="Plot not found")
    # This lookup exists solely to auto-fill the "บันทึกการตรวจแปลงใหม่" form
    # (RecordForm's QR scan) — a plot with no active planting cycle can't take
    # a new record (records._create_record 409s), so resolving it here would
    # just lead the inspector into a form they can't submit. Same generic 404
    # (round 7.1.1), consistent with the public verify + create guards.
    if await plot_cycle_repo.get_active_cycle_for_plot(db, plot.id) is None:
        raise HTTPException(status_code=404, detail="Plot not found")
    return PlotLookupRead(
        plot_id=plot.id,
        plot_code=plot.plot_code,
        plot_name=plot.name,
        supplier_id=supplier.id,
        supplier_code=supplier.code,
        supplier_name=supplier.name,
    )


@router.get("/lookup-by-qr", response_model=PlotLookupRead, dependencies=[
    Depends(require_any_permission(PermissionKey.PLOTS_READ, PermissionKey.RECORDS_CREATE)),
    Depends(get_rls_context),
])
async def lookup_plot_by_qr(
    qr_key: str = Query(..., alias="qrKey", min_length=1, max_length=64),
    db: AsyncSession = Depends(get_db),
) -> PlotLookupRead:
    """QR-scan lookup for the round-20 opaque-qr-key deep link format —
    sibling of /lookup (supplierCode+plotCode), same generic-404/RLS-scoped
    semantics, keyed by qr_key instead. Must stay registered before
    /{plot_id} for the same reason /lookup does (otherwise FastAPI tries
    to parse "lookup-by-qr" as a UUID).
    """
    plot = await repo.get_plot_by_qr_key(db, qr_key)
    if plot is None or not plot.is_active or not plot.supplier.is_active:
        raise HTTPException(status_code=404, detail="Plot not found")
    # Same active-cycle gate as the legacy /lookup above (round 7.1.1) — this
    # feeds the same new-record auto-fill flow.
    if await plot_cycle_repo.get_active_cycle_for_plot(db, plot.id) is None:
        raise HTTPException(status_code=404, detail="Plot not found")
    return PlotLookupRead(
        plot_id=plot.id,
        plot_code=plot.plot_code,
        plot_name=plot.name,
        supplier_id=plot.supplier_id,
        supplier_code=plot.supplier.code,
        supplier_name=plot.supplier.name,
    )


@router.get(
    "/inspection-access-credentials/readiness",
    response_model=PlotCredentialReadiness,
    dependencies=[
        Depends(require_permission(PermissionKey.PLOTS_READ)),
        Depends(get_rls_context),
    ],
)
async def get_plot_credential_readiness(
    current_user: CurrentUser,
    db: AsyncSession = Depends(get_db),
) -> PlotCredentialReadiness:
    """Which plots still need an inspection password before
    PUBLIC_PLOT_PASSWORD_ENFORCEMENT can safely be turned on (round 8-9C).

    Read-only, plots.read + RLS. Scope is the caller's own: a Supplier Owner
    sees only their supplier's plots (get_supplier_scope_filter — the SAME
    helper every other supplier-scoped read uses, never a new role decision),
    an admin with scope 'all' sees everything.

    ONE set-based query, DISTINCT per plot (see the repository) — never one
    query per plot. Returns identity only: no phone, no credential id/version,
    no password/hash/digest, no qrKey.

    This route is declared BEFORE /{plot_id} on purpose: FastAPI matches in
    declaration order, and a literal path registered after a parameterized one
    would be swallowed by it.
    """
    scope_conditions = await get_supplier_scope_filter(current_user)
    rows = await credential_repo.get_credential_readiness_rows(db, list(scope_conditions))

    missing = [
        PlotCredentialReadinessPlot(
            plot_id=plot.id,
            plot_code=plot.plot_code,
            plot_name=plot.name,
            supplier_id=supplier.id,
            supplier_code=supplier.code,
            supplier_name=supplier.name,
        )
        for plot, supplier, configured in rows
        if not configured
    ]
    eligible = len(rows)
    configured_count = eligible - len(missing)
    return PlotCredentialReadiness(
        eligible_plots=eligible,
        configured_plots=configured_count,
        missing_credential_plots=len(missing),
        # Never "ready" with zero eligible plots — that means nothing is set up
        # at all, not that everything is done.
        ready=eligible > 0 and not missing,
        missing_plots=missing,
    )


@router.get("/{plot_id}", response_model=PlotRead, dependencies=[
    Depends(require_permission(PermissionKey.PLOTS_READ)),
    Depends(get_rls_context),
])
async def get_plot(
    plot_id: UUID,
    db: AsyncSession = Depends(get_db),
) -> PlotRead:
    plot = await repo.get_plot(db, plot_id)
    if plot is None:
        raise HTTPException(status_code=404, detail="Plot not found")
    return _to_read(plot)


@router.get(
    "/{plot_id}/access-phones",
    response_model=PlotAccessPhoneConfigResponse,
    dependencies=[
        Depends(require_permission(PermissionKey.PLOTS_READ)),
        Depends(get_rls_context),
    ],
)
async def get_plot_access_phones(
    plot_id: UUID,
    db: AsyncSession = Depends(get_db),
) -> PlotAccessPhoneConfigResponse:
    """Read a plot's ACTIVE access-phone config (round 8-3A). Same plots.read +
    RLS as GET /{plot_id}; an out-of-scope or unknown plot is the same generic
    404 (RLS + this explicit check both apply), never leaking existence."""
    plot = await repo.get_plot(db, plot_id)
    if plot is None:
        raise HTTPException(status_code=404, detail="Plot not found")
    phones = await phone_repo.list_active_plot_access_phones(db, plot_id)
    return _config_response(phones)


@router.put(
    "/{plot_id}/access-phones",
    response_model=PlotAccessPhoneConfigResponse,
    dependencies=[
        Depends(require_permission(PermissionKey.PLOTS_UPDATE)),
        Depends(get_rls_context),
    ],
)
async def replace_plot_access_phones(
    plot_id: UUID,
    payload: PlotAccessPhoneConfig,
    db: AsyncSession = Depends(get_db),
) -> PlotAccessPhoneConfigResponse:
    """Replace a plot's ENTIRE access-phone config in one transaction (round
    8-3A). plots.update + RLS. Locks the Plot row FIRST (get_plot_for_update;
    the aggregate lock, then the phone rows in the repo) so this serializes with
    the plot's other mutations. Out-of-scope/unknown → generic 404; a unique
    clash / concurrent race → clean 409; invalid numbers are a 422 raised by
    hand (round 8-17C — see normalize_and_validate_phone_config), never a
    Pydantic-auto 422, so a rejected phone is never echoed. The whole config
    is replaced atomically (get_db commits once)."""
    try:
        normalize_and_validate_phone_config(payload)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None
    plot = await repo.get_plot_for_update(db, plot_id)
    if plot is None:
        raise HTTPException(status_code=404, detail="Plot not found")
    try:
        phones = await phone_repo.replace_plot_access_phones(db, plot, payload)
    except IntegrityError as exc:
        raise HTTPException(
            status_code=409,
            detail="Conflicting change to this plot's access phones — please retry",
        ) from exc
    return _config_response(phones)


def _credential_status(row) -> PlotInspectionCredentialStatus:
    """One plot_access_credentials row (or None) → the STATUS response. Never
    reads password_hash/password_lookup_digest — the response schema has no
    field for them, and this is the only place a credential row is turned into
    anything client-visible. An inactive row reports configured=false with no
    version/timestamp: to a caller the plot simply has no usable password."""
    configured = row is not None and bool(row.is_active)
    return PlotInspectionCredentialStatus(
        configured=configured,
        credential_version=row.credential_version if configured else None,
        updated_at=row.updated_at if configured else None,
    )


@router.get(
    "/{plot_id}/inspection-access-credential",
    response_model=PlotInspectionCredentialStatus,
    dependencies=[
        Depends(require_permission(PermissionKey.PLOTS_READ)),
        Depends(get_rls_context),
    ],
)
async def get_plot_inspection_access_credential(
    plot_id: UUID,
    db: AsyncSession = Depends(get_db),
) -> PlotInspectionCredentialStatus:
    """Whether this plot has an inspection password, and which version (round
    8-9A). Same plots.read + RLS as GET /{plot_id}; an out-of-scope or unknown
    plot is the same generic 404, never leaking existence. Returns STATUS only
    — there is no endpoint that reveals an existing password."""
    plot = await repo.get_plot(db, plot_id)
    if plot is None:
        raise HTTPException(status_code=404, detail="Plot not found")
    row = await credential_repo.get_credential_status_by_plot_id(db, plot_id)
    return _credential_status(row)


@router.put(
    "/{plot_id}/inspection-access-credential",
    response_model=PlotInspectionCredentialStatus,
    dependencies=[
        Depends(require_permission(PermissionKey.PLOTS_UPDATE)),
        Depends(get_rls_context),
    ],
)
async def set_plot_inspection_access_credential(
    plot_id: UUID,
    payload: PlotInspectionCredentialSet,
    current_user: CurrentUser,
    db: AsyncSession = Depends(get_db),
) -> PlotInspectionCredentialStatus:
    """Set or replace this plot's inspection password (round 8-9A; hardened in
    8-9A.1). plots.update + RLS.

    Phase order matters, and it is not the obvious one:

      1. SCOPED READ (repo.get_plot — no lock). Authorization first, so an
         out-of-scope caller is a generic 404 before any work at all.
      2. Policy, then blind-index digest — both cheap, both fail before bcrypt.
      3. bcrypt in a WORKER THREAD (asyncio.to_thread). Cost 12 is ~250ms of
         pure CPU; running it inline would stall the whole event loop, and
         holding a row lock across it would stall every other writer on this
         plot for the same 250ms. So it happens while NO lock is held.
      4. ONLY NOW take the Plot aggregate lock (repo.get_plot_for_update) and
         re-check the plot — step 1's read was unlocked, so this closes the
         TOCTOU window (plot deleted / moved out of scope while we hashed).
         Lock order for the mutation is unchanged: Plot → PlotAccessCredential.

    Errors:
      - out-of-scope/unknown plot → generic 404 (RLS + this check both apply),
        at step 1 or again at step 4
      - malformed code (not 4-20 ASCII digits) → 422 with the policy's own
        static Thai message;
        those messages are constants that never contain the submitted value
      - pepper not deployed → controlled 503 (a config gap, not a crash) and
        NO fallback to another secret
      - concurrent first-set racing UNIQUE(plot_id) → clean 409

    An INACTIVE plot may still be given a password — that is what makes a later
    reactivation usable immediately. The response is status only; the plaintext
    is never stored, echoed, or logged anywhere.
    """
    # 1. Authorization before work — unlocked, scoped read.
    if await repo.get_plot(db, plot_id) is None:
        raise HTTPException(status_code=404, detail="Plot not found")

    # 2. Cheap checks first.
    try:
        pin = validate_plot_access_password(payload.password.get_secret_value())
    except PlotAccessPasswordPolicyError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    try:
        digest = build_plot_access_password_lookup_digest(pin)
    except PlotAccessPepperMissingError as exc:
        raise HTTPException(
            status_code=503,
            detail="ระบบยังไม่พร้อมตั้งรหัสยืนยันแปลง กรุณาติดต่อผู้ดูแลระบบ",
        ) from exc

    # 3. bcrypt off the event loop, holding no lock.
    password_hash = await asyncio.to_thread(hash_plot_access_password, pin)

    # 4. Now take the aggregate lock and re-check under it (TOCTOU).
    plot = await repo.get_plot_for_update(db, plot_id)
    if plot is None:
        raise HTTPException(status_code=404, detail="Plot not found")

    try:
        row = await credential_repo.set_or_replace_plot_credential(
            db, plot,
            password_hash=password_hash,
            password_lookup_digest=digest,
            updated_by_id=current_user.id,
        )
    except IntegrityError as exc:
        raise HTTPException(
            status_code=409,
            detail="Conflicting change to this plot's inspection credential — please retry",
        ) from exc

    # Security event, high risk. resource_id is the plot; the ONLY metadata is
    # the new version number — never the password, hash, digest, or any phone.
    # Read credential_version BEFORE the log call: ActivityLogger only does
    # db.add(), so the entry stays PENDING, and any attribute read after it is
    # what springs the trap described below.
    await ActivityLogger(db).log(
        user=current_user, action_type="update",
        action="plot.inspection_access_credential_set",
        resource_type="plot", resource_id=str(plot.id),
        is_security_event=True, risk_level="high",
        metadata={"credential_version": row.credential_version},
    )

    # Round 8-9F.0 — MUST come after the log() above and before serialising.
    #
    # updated_at is server-generated (TimestampMixin: server_default=now(),
    # onupdate=now()), so after the repository's flush it is an EXPIRED
    # attribute. Reading it inside _credential_status() would trigger a lazy
    # SELECT, and SQLAlchemy autoflushes before any ORM query — which would try
    # to INSERT the ActivityLog entry that log() left pending. Both of those are
    # IO from a synchronous attribute access, so asyncpg raises MissingGreenlet
    # and the whole PUT 500s (observed on both first-set and replace).
    #
    # Refreshing here does the same two things EXPLICITLY, inside an await: the
    # pending log INSERT is autoflushed safely, and is_active/credential_version/
    # updated_at come back materialised. Same fix, same reasoning, and the same
    # comment as the plot-update endpoints above (see _to_read callers). A
    # failure here still propagates, so get_db's transaction rolls the whole
    # request back — never a half-written credential.
    await db.refresh(row)
    return _credential_status(row)


@router.get("/{plot_id}/cycles", response_model=list[PlotCycleRead], dependencies=[
    Depends(require_permission(PermissionKey.PLOTS_READ)),
    Depends(get_rls_context),
])
async def list_plot_cycles(
    plot_id: UUID,
    db: AsyncSession = Depends(get_db),
    limit: int = 50,
    offset: int = 0,
) -> list[PlotCycleRead]:
    """Planting-cycle history for one plot (round 7.2A read foundation).

    The plot must be in the caller's scope — an out-of-scope or unknown plot
    is the same generic 404 as GET /{plot_id} (RLS + this explicit check both
    apply). No write here; open/close/create cycle endpoints arrive in 7.2B.
    """
    plot = await repo.get_plot(db, plot_id)
    if plot is None:
        raise HTTPException(status_code=404, detail="Plot not found")
    cycles = await plot_cycle_repo.list_cycles_for_plot(
        db, plot_id, limit=limit, offset=offset
    )
    return [PlotCycleRead.model_validate(c) for c in cycles]


@router.post("/{plot_id}/cycles", response_model=PlotCycleRead,
             status_code=status.HTTP_201_CREATED, dependencies=[
    Depends(require_permission(PermissionKey.PLOTS_UPDATE)),
    Depends(get_rls_context),
])
async def start_plot_cycle(
    plot_id: UUID,
    payload: PlotCycleCreate,
    db: AsyncSession = Depends(get_db),
) -> PlotCycleRead:
    """Start a new planting cycle (รอบปลูก) on a plot (round 7.2B).

    Guards: the plot must be in scope (404) and permanently open (409 if
    is_active=false), and must have NO active cycle already. Round 8.0.7 —
    the plot row is locked FIRST (get_plot_for_update; the aggregate lock for
    this plot's cycle transitions), then the active-cycle check takes its own
    row lock; the partial unique index (uq_plot_cycles_active_per_plot) stays
    the final race guard — a concurrent start that loses the race gets the
    same clean 409, not a 500.

    On success the new cycle becomes the plot's mirror source (crop/variety/
    lot/planting/yield-plan) and the plot's inspection-derived snapshot is
    cleared — a fresh cycle has no inspections yet. startedAt = plantingDate at
    00:00 UTC when given, else the creation time.
    """
    plot = await repo.get_plot_for_update(db, plot_id)
    if plot is None:
        raise HTTPException(status_code=404, detail="Plot not found")
    if not plot.is_active:
        raise HTTPException(status_code=409, detail="Plot is inactive")

    existing = await plot_cycle_repo.get_active_cycle_for_plot_for_update(db, plot.id)
    if existing is not None:
        raise HTTPException(
            status_code=409, detail="Plot already has an active planting cycle"
        )

    # Round 8-15D — new cycle's crop/variety (if given) must exist and be
    # active in Master Data; a variety must belong to the chosen crop.
    await master_data_validation.assert_crop_variety_valid(db, payload.crop, payload.variety)
    started_at = (
        datetime.combine(payload.planting_date, time.min, tzinfo=timezone.utc)
        if payload.planting_date is not None
        else None
    )
    try:
        cycle = await plot_cycle_repo.create_cycle(
            db, plot,
            crop=payload.crop, variety=payload.variety,
            cycle_label=payload.cycle_label, lot_no=payload.lot_no,
            po_number=payload.po_number, p_code=payload.p_code,
            supplier_lot_no=payload.supplier_lot_no,
            oracle_supplier_code=payload.oracle_supplier_code,
            oracle_invoice=payload.oracle_invoice, ref_account=payload.ref_account,
            planting_date=payload.planting_date, plant_count=payload.plant_count,
            expected_yield_full=payload.expected_yield_full,
            expected_yield_unit=payload.expected_yield_unit,
            started_at=started_at,
        )
    except AutoLotMissingComponentError as exc:
        # Round 8-12A.1 — a blank lotNo asks for an Auto Lot but a V2 component
        # is missing. Clean 422 (never a 500, never an active cycle with a NULL
        # lot); the transaction rolls back so nothing is written.
        raise HTTPException(
            status_code=422, detail=_auto_lot_missing_detail(exc.missing),
        ) from exc
    except LotNumberTooLongError as exc:
        # Auto Lot would exceed lot_no's 100-char limit — clean 422 (round 8-5A).
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except IntegrityError as exc:
        # Lost the race to a concurrent start, OR an Auto Lot running-number
        # collision (uq_plot_cycles_auto_lot_running) — either way a clean 409,
        # never a 500. Round 8-5A.
        raise HTTPException(
            status_code=409, detail="Plot already has an active planting cycle"
        ) from exc

    # Fresh cycle → clear the inspection snapshot carried over from the
    # previous cycle's records (the mirror was already synced by create_cycle).
    await plot_cycle_repo.clear_plot_inspection_snapshot(db, plot)
    # Re-load the server-computed timestamp columns (created_at/updated_at) the
    # preceding flush expired — reading them lazily inside model_validate would
    # attempt async IO outside the greenlet and 500 (round 7.7 fix; same
    # refresh-after-write pattern as plot_repository.create_plot).
    await db.refresh(cycle)
    return PlotCycleRead.model_validate(cycle)


@router.patch("/{plot_id}/cycles/{cycle_id}", response_model=PlotCycleRead, dependencies=[
    Depends(require_permission(PermissionKey.PLOTS_UPDATE)),
    Depends(get_rls_context),
])
async def update_plot_cycle(
    plot_id: UUID,
    cycle_id: UUID,
    payload: PlotCycleUpdate,
    db: AsyncSession = Depends(get_db),
) -> PlotCycleRead:
    """Edit the ACTIVE cycle's plan (round 7.2B). Only planting/plan fields
    change; status/cycle_no/closed_* are not editable here (absent from
    PlotCycleUpdate, and update_cycle ignores them anyway).

    Re-syncs the plot mirror. Deliberately does NOT clear the inspection
    snapshot — correcting a crop/yield plan mustn't erase the plot's latest
    inspection status. Records are never touched.

    Round 8.0.7 — locks the plot FIRST (get_plot_for_update), then the
    active cycle: the unlocked get_cycle_for_plot lookup above only checks
    ownership/status for the clean 404/409; the row-locked re-check just
    below additionally guards against a concurrent close/rollover racing in
    between that lookup and this write (same pattern as rollover_plot_cycle).
    """
    plot = await repo.get_plot_for_update(db, plot_id)
    if plot is None:
        raise HTTPException(status_code=404, detail="Plot not found")
    cycle = await plot_cycle_repo.get_cycle_for_plot(db, plot_id, cycle_id)
    if cycle is None:
        raise HTTPException(status_code=404, detail="Plot cycle not found")
    if cycle.status != CYCLE_STATUS_ACTIVE:
        raise HTTPException(
            status_code=409, detail="Only active planting cycle can be updated"
        )
    locked_cycle = await plot_cycle_repo.get_active_cycle_for_plot_for_update(db, plot.id)
    if locked_cycle is None or locked_cycle.id != cycle.id:
        raise HTTPException(
            status_code=409, detail="Only active planting cycle can be updated"
        )

    fields = payload.model_dump(exclude_unset=True)
    # Round 8-17A.1 — an edit must never CLEAR an existing cycle label via an
    # explicit blank/whitespace submission (cycle_label absent from the PATCH
    # body still means "keep the current value" — exclude_unset semantics,
    # unchanged). A currently-blank (legacy, pre-8-17A.1) label may stay
    # blank when the row doesn't touch it — read-back compatibility, no
    # forced backfill. Only a REAL attempt to blank out an existing label is
    # rejected, using the same Thai message the create-time requirement uses.
    effective_cycle_label = fields.get("cycle_label", cycle.cycle_label)
    if cycle.cycle_label and not effective_cycle_label:
        raise HTTPException(
            status_code=422,
            detail="กรุณาระบุชื่อรอบปลูก เนื่องจากใช้ระบุรอบและสร้าง Lot No อัตโนมัติ",
        )
    # Round 8-15D — an edit that changes crop/variety must land on an
    # active, existing Master Data pair; a field absent from the PATCH body
    # (exclude_unset) leaves the cycle's own current value as "effective",
    # so an unchanged legacy pair that's since been deactivated still passes.
    await master_data_validation.assert_crop_variety_valid(
        db,
        fields.get("crop", cycle.crop),
        fields.get("variety", cycle.variety),
        current_crop=cycle.crop,
        current_variety=cycle.variety,
    )
    try:
        await plot_cycle_repo.update_cycle(db, plot, cycle, fields)
    except AutoLotMissingComponentError as exc:
        # Round 8-5B.1 / 8-12A — an edit asked to regenerate an Auto Lot (blank
        # lotNo) but a component of the V2 formula
        # ({cycleLabel}-{supplierCode}-{pCode}-{running}) is blank. Refuse with
        # a clean 422 naming the missing FIELD (never a submitted value); the
        # txn rolls back so the existing lot is preserved (never cleared).
        raise HTTPException(
            status_code=422, detail=_auto_lot_missing_detail(exc.missing),
        ) from exc
    except LotNumberTooLongError as exc:
        # A re-resolved Auto Lot would exceed lot_no's 100-char limit — clean
        # 422 (round 8-5A). Nothing is committed (the flush failed inside).
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except IntegrityError as exc:
        # Auto Lot running-number collision (uq_plot_cycles_auto_lot_running) —
        # clean 409, never a 500. Round 8-5A.
        raise HTTPException(
            status_code=409, detail="Conflict assigning the planting cycle lot number"
        ) from exc
    await plot_cycle_repo.sync_plot_mirror_from_cycle(db, plot, cycle)
    # Re-load the onupdate-computed updated_at the flush expired (round 7.7 fix
    # — see start_plot_cycle) before serialising.
    await db.refresh(cycle)
    return PlotCycleRead.model_validate(cycle)


@router.post("/{plot_id}/cycles/{cycle_id}/close", response_model=PlotCycleRead, dependencies=[
    Depends(require_permission(PermissionKey.PLOTS_UPDATE)),
    Depends(get_rls_context),
])
async def close_plot_cycle(
    plot_id: UUID,
    cycle_id: UUID,
    payload: PlotCycleClose,
    current_user: CurrentUser,
    db: AsyncSession = Depends(get_db),
) -> PlotCycleRead:
    """Close the active cycle as harvested/cancelled (round 7.2B). Preserves
    history (never deletes the cycle or its records), clears the plot's mirror
    AND inspection snapshot (the plot now has no active cycle), and leaves
    plot.is_active and the QR untouched. After this, creating an inspection
    fails (no active cycle) until a new cycle is started.

    Round 8.0.7 — locks the plot FIRST (get_plot_for_update), then the active
    cycle (same Plot-before-PlotCycle order as update_plot_cycle/
    rollover_plot_cycle); the row-locked re-check guards against a concurrent
    close/rollover racing in between the unlocked lookup above and this write.
    """
    plot = await repo.get_plot_for_update(db, plot_id)
    if plot is None:
        raise HTTPException(status_code=404, detail="Plot not found")
    cycle = await plot_cycle_repo.get_cycle_for_plot(db, plot_id, cycle_id)
    if cycle is None:
        raise HTTPException(status_code=404, detail="Plot cycle not found")
    if cycle.status != CYCLE_STATUS_ACTIVE:
        raise HTTPException(
            status_code=409, detail="Only active planting cycle can be closed"
        )
    locked_cycle = await plot_cycle_repo.get_active_cycle_for_plot_for_update(db, plot.id)
    if locked_cycle is None or locked_cycle.id != cycle.id:
        raise HTTPException(
            status_code=409, detail="Only active planting cycle can be closed"
        )

    await plot_cycle_repo.close_cycle(
        db, cycle, status=payload.status,
        closed_by_id=current_user.id, reason=payload.close_reason,
    )
    await plot_cycle_repo.clear_plot_cycle_mirror_and_inspection_snapshot(db, plot)
    # Re-load the onupdate-computed updated_at the flush expired (round 7.7 fix
    # — see start_plot_cycle) before serialising.
    await db.refresh(cycle)
    return PlotCycleRead.model_validate(cycle)


@router.post(
    "/{plot_id}/cycles/{cycle_id}/rollover",
    response_model=PlotCycleRolloverResult,
    dependencies=[
        Depends(require_permission(PermissionKey.PLOTS_UPDATE)),
        Depends(get_rls_context),
    ],
)
async def rollover_plot_cycle(
    plot_id: UUID,
    cycle_id: UUID,
    payload: PlotCycleRollover,
    current_user: CurrentUser,
    db: AsyncSession = Depends(get_db),
) -> PlotCycleRolloverResult:
    """Atomically close the active cycle and open a fresh one on the same plot
    (round 7.9B) — the single-plot equivalent of the Excel rollover import
    action. One transaction, so the frontend never calls close then start as two
    requests: a start failure after a successful close would strand the plot
    with NO active cycle. If the new cycle can't be created, the close rolls back
    too. The QR key, inspection records, and plot.is_active are untouched.

    Round 8.0.7 — locks the plot FIRST (get_plot_for_update; the plot is the
    aggregate lock for its own cycle transitions), then the active cycle
    below — same Plot-before-PlotCycle order as start/update/close.
    """
    plot = await repo.get_plot_for_update(db, plot_id)
    if plot is None:
        raise HTTPException(status_code=404, detail="Plot not found")
    if not plot.is_active:
        raise HTTPException(status_code=409, detail="Plot is inactive")
    cycle = await plot_cycle_repo.get_cycle_for_plot(db, plot_id, cycle_id)
    if cycle is None:
        raise HTTPException(status_code=404, detail="Plot cycle not found")
    if cycle.status != CYCLE_STATUS_ACTIVE:
        raise HTTPException(
            status_code=409, detail="Only active planting cycle can be rolled over"
        )

    # Row-lock the plot's active cycle so a concurrent close/rollover can't race
    # us into two active cycles. A plot has at most one active cycle (partial
    # unique index) and `cycle` is active, so the locked row IS `cycle` — unless
    # it was closed between our read and the lock, which we treat as the same 409
    # as a non-active cycle.
    locked = await plot_cycle_repo.get_active_cycle_for_plot_for_update(db, plot.id)
    if locked is None or locked.id != cycle.id:
        raise HTTPException(
            status_code=409, detail="Only active planting cycle can be rolled over"
        )

    nc = payload.new_cycle
    # Round 8-15D — the fresh cycle opened by rollover is a NEW cycle, so its
    # crop/variety (if given) must exist and be active in Master Data.
    await master_data_validation.assert_crop_variety_valid(db, nc.crop, nc.variety)
    started_at = (
        datetime.combine(nc.planting_date, time.min, tzinfo=timezone.utc)
        if nc.planting_date is not None
        else None
    )
    try:
        closed, new_cycle = await plot_cycle_repo.rollover_cycle(
            db, plot, locked,
            close_status=payload.close_status,
            closed_by_id=current_user.id,
            close_reason=payload.close_reason or "Closed by rollover",
            crop=nc.crop, variety=nc.variety, cycle_label=nc.cycle_label,
            lot_no=nc.lot_no, po_number=nc.po_number, p_code=nc.p_code,
            supplier_lot_no=nc.supplier_lot_no,
            oracle_supplier_code=nc.oracle_supplier_code, oracle_invoice=nc.oracle_invoice,
            ref_account=nc.ref_account,
            planting_date=nc.planting_date, plant_count=nc.plant_count,
            expected_yield_full=nc.expected_yield_full,
            expected_yield_unit=nc.expected_yield_unit,
            started_at=started_at,
        )
    except AutoLotMissingComponentError as exc:
        # Round 8-12A.1 — a blank lotNo asks for an Auto Lot but a V2 component
        # is missing. Clean 422 (never a 500, never an active cycle with a NULL
        # lot); the transaction rolls back so nothing is written.
        raise HTTPException(
            status_code=422, detail=_auto_lot_missing_detail(exc.missing),
        ) from exc
    except LotNumberTooLongError as exc:
        # The new cycle's Auto Lot would exceed lot_no's 100-char limit — clean
        # 422 (round 8-5A). The close rolls back with it (single transaction).
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except IntegrityError as exc:
        # Lost the race to a concurrent start, OR an Auto Lot running-number
        # collision — either way a clean 409, never a 500.
        raise HTTPException(
            status_code=409, detail="Plot already has an active planting cycle"
        ) from exc

    # Re-load the flush-expired server-computed columns on BOTH returned cycles
    # before serialising (round 7.7 MissingGreenlet fix — see start_plot_cycle).
    await db.refresh(closed)
    await db.refresh(new_cycle)
    return PlotCycleRolloverResult(
        plot_id=plot.id,
        active_cycle_id=new_cycle.id,
        active_cycle_no=new_cycle.cycle_no,
        closed_cycle=PlotCycleRead.model_validate(closed),
        new_cycle=PlotCycleRead.model_validate(new_cycle),
    )


@router.patch("/{plot_id}", response_model=PlotRead, dependencies=[
    Depends(require_permission(PermissionKey.PLOTS_UPDATE)),
    Depends(get_rls_context),
])
async def update_plot(
    plot_id: UUID,
    payload: PlotUpdate,
    db: AsyncSession = Depends(get_db),
) -> PlotRead:
    # is_active is NOT toggleable through this generic PATCH (gated by the
    # weaker plots.update). Permanently closing a plot is a distinct, more
    # privileged action — it must go through POST /{plot_id}/deactivate, which
    # is gated by plots.delete. Rejecting an explicit isActive here (rather
    # than silently dropping it) makes the boundary visible to the caller.
    # `model_fields_set` is true only when the client actually sent the key,
    # so a normal edit that omits it is unaffected; the deactivate endpoint
    # bypasses this route entirely (it builds PlotUpdate(is_active=False) and
    # calls the repo directly).
    #
    # Round 8.0.7 — locks the plot row (get_plot_for_update) even for a plain
    # field edit, so every write to this plot serializes through the same
    # aggregate lock as its lifecycle/record-snapshot mutations.
    if "is_active" in payload.model_fields_set:
        raise HTTPException(
            status_code=400,
            detail="isActive cannot be changed here; use the deactivate endpoint",
        )
    plot = await repo.get_plot_for_update(db, plot_id)
    if plot is None:
        raise HTTPException(status_code=404, detail="Plot not found")
    plot = await repo.update_plot(db, plot, payload)
    return _to_read(plot)


@router.post("/{plot_id}/deactivate", response_model=PlotRead, dependencies=[
    Depends(require_permission(PermissionKey.PLOTS_DELETE)),
    Depends(get_rls_context),
])
async def deactivate_plot(
    plot_id: UUID,
    db: AsyncSession = Depends(get_db),
) -> PlotRead:
    """Round 8.0.7 — locks the plot row before flipping is_active, so this
    can't interleave with a concurrent record create on the same plot: the
    two now deterministically serialize on the Plot row (whichever
    transaction acquires the lock first completes fully before the other's
    lock request is granted).

    Round 8-6H invariant: a plot with an active planting cycle can't be
    deactivated directly — 409, no mutation, before is_active is ever
    touched. The user must close (or roll over) the active cycle first via
    the existing lifecycle endpoints. This keeps "inactive" and "has an open
    cycle" mutually exclusive, which reactivate-with-cycle (Part D) and the
    Excel reactivate_plot_with_cycle action (Part F) both rely on — neither
    has to handle "reactivating a plot that already has an active cycle".
    The active-cycle check runs UNDER the plot lock already taken above, so
    a concurrent cycle-open can't race this into an inconsistent state.

    On success, also clears the plot's stale mirror/inspection snapshot
    (round 8-6H — previously left untouched) so a plot that's reactivated
    later never keeps advertising crop/yield/last-inspection data from
    before it was closed. QR key, access phones, assignments, and cycle/
    record history are all untouched.
    """
    plot = await repo.get_plot_for_update(db, plot_id)
    if plot is None:
        raise HTTPException(status_code=404, detail="Plot not found")
    if await plot_cycle_repo.get_active_cycle_for_plot_for_update(db, plot.id) is not None:
        raise HTTPException(
            status_code=409, detail="กรุณาปิดรอบปลูกปัจจุบันก่อนปิดใช้งานแปลง",
        )
    plot = await repo.update_plot(db, plot, PlotUpdate(is_active=False))
    await plot_cycle_repo.clear_plot_cycle_mirror_and_inspection_snapshot(db, plot)
    # update_plot/clear_* flush server-managed scalar columns such as
    # updated_at. Reload scalars before Pydantic reads them, then reload the
    # relationships PlotRead exposes. Without the scalar refresh an async
    # lazy-load can raise MissingGreenlet while serialising the response.
    await db.refresh(plot)
    await db.refresh(
        plot, attribute_names=["assignments", "supplier", "active_cycle", "access_phones"]
    )
    return _to_read(plot)


@router.post("/{plot_id}/reactivate", response_model=PlotRead, dependencies=[
    Depends(require_permission(PermissionKey.PLOTS_DELETE)),
    Depends(get_rls_context),
])
async def reactivate_plot(
    plot_id: UUID,
    current_user: CurrentUser,
    db: AsyncSession = Depends(get_db),
) -> PlotRead:
    """Reopen a permanently-deactivated plot (round 8-6H) WITHOUT starting a
    new planting cycle. Same permission as deactivate — plots.delete, never
    the weaker plots.update — reactivation is exactly as privileged an
    action as deactivation.

    After this the plot shows as active but still has no active cycle, so
    it can't be inspected (public or logged-in record-create both require
    one) until a separate POST /{plot_id}/cycles call, or use
    /{plot_id}/reactivate-with-cycle below to do both atomically. QR key,
    access phones, assignments, and cycle/record history are all untouched
    — reactivate_plot only flips is_active and clears the stale mirror/
    inspection snapshot (round 8-6H Part B/C).

    Out-of-scope/unknown plot → generic 404 (RLS + this explicit check both
    apply). Already-active or an inconsistent inactive-with-active-cycle
    state (should never happen after Part B's hardened deactivate — a
    defensive guard) → 409, no mutation.
    """
    plot = await repo.get_plot_for_update(db, plot_id)
    if plot is None:
        raise HTTPException(status_code=404, detail="Plot not found")
    try:
        plot = await repo.reactivate_plot(db, plot)
    except repo.PlotAlreadyActiveError as exc:
        raise HTTPException(status_code=409, detail="แปลงนี้เปิดใช้งานอยู่แล้ว") from exc
    except repo.PlotHasActiveCycleError as exc:
        raise HTTPException(
            status_code=409,
            detail="พบข้อมูลไม่สอดคล้องกัน (แปลงปิดใช้งานแต่มีรอบปลูกที่เปิดอยู่) กรุณาติดต่อผู้ดูแลระบบ",
        ) from exc
    # reactivate_plot flushes server-managed scalar columns (notably
    # updated_at). Reload scalars first so _to_read never attempts an async
    # lazy-load outside greenlet context, then load response relationships.
    await db.refresh(plot)
    await db.refresh(
        plot, attribute_names=["assignments", "supplier", "active_cycle", "access_phones"]
    )
    await ActivityLogger(db).log(
        user=current_user, action_type="update", action="plot.reactivated",
        resource_type="plot", resource_id=str(plot.id), risk_level="medium",
    )
    return _to_read(plot)


@router.post(
    "/{plot_id}/reactivate-with-cycle",
    response_model=PlotWithCycleCreateResult,
    dependencies=[
        Depends(require_permission(PermissionKey.PLOTS_DELETE)),
        Depends(require_permission(PermissionKey.PLOTS_UPDATE)),
        Depends(get_rls_context),
    ],
)
async def reactivate_plot_with_cycle(
    plot_id: UUID,
    payload: PlotCycleCreate,
    current_user: CurrentUser,
    db: AsyncSession = Depends(get_db),
) -> PlotWithCycleCreateResult:
    """Atomically reopen a permanently-deactivated plot AND start its first
    new planting cycle (round 8-6H Part D) — one transaction, so a cycle-
    create failure never leaves the plot stranded active-with-no-cycle (see
    reactivate_plot_with_cycle's docstring in plot_repository.py for exactly
    how the rollback guarantee holds).

    Requires BOTH plots.delete (the activation privilege — same as plain
    deactivate/reactivate) AND plots.update (the privilege every other
    cycle-creating endpoint requires) — listed as two separate
    require_permission dependencies (AND, never OR); a caller with only one
    of the two (e.g. a Supplier Owner who only has plots.update) is a clean
    403 before the handler ever runs. Reuses PlotCycleCreate as-is (same
    schema/validation as POST /{plot_id}/cycles) — no new request schema.

    Guards, in order: plot must exist in scope (404); must be inactive, not
    already active (409); must have no active cycle already (409 —
    defensive, should be unreachable after Part B). On success returns the
    same {plot, cycle} shape as POST /plots/with-cycle (PlotWithCycleCreateResult).
    """
    plot = await repo.get_plot_for_update(db, plot_id)
    if plot is None:
        raise HTTPException(status_code=404, detail="Plot not found")

    # Round 8-15D — reactivation starts a brand-new cycle, so its
    # crop/variety (if given) must exist and be active in Master Data.
    await master_data_validation.assert_crop_variety_valid(db, payload.crop, payload.variety)
    started_at = (
        datetime.combine(payload.planting_date, time.min, tzinfo=timezone.utc)
        if payload.planting_date is not None
        else None
    )
    try:
        plot, cycle = await repo.reactivate_plot_with_cycle(
            db, plot,
            crop=payload.crop, variety=payload.variety, cycle_label=payload.cycle_label,
            lot_no=payload.lot_no, po_number=payload.po_number, p_code=payload.p_code,
            supplier_lot_no=payload.supplier_lot_no,
            oracle_supplier_code=payload.oracle_supplier_code,
            oracle_invoice=payload.oracle_invoice, ref_account=payload.ref_account,
            planting_date=payload.planting_date, plant_count=payload.plant_count,
            expected_yield_full=payload.expected_yield_full,
            expected_yield_unit=payload.expected_yield_unit,
            started_at=started_at,
        )
    except repo.PlotAlreadyActiveError as exc:
        raise HTTPException(status_code=409, detail="แปลงนี้เปิดใช้งานอยู่แล้ว") from exc
    except repo.PlotHasActiveCycleError as exc:
        raise HTTPException(
            status_code=409,
            detail="พบข้อมูลไม่สอดคล้องกัน (แปลงปิดใช้งานแต่มีรอบปลูกที่เปิดอยู่) กรุณาติดต่อผู้ดูแลระบบ",
        ) from exc
    except AutoLotMissingComponentError as exc:
        # Round 8-12A.1 — a blank lotNo asks for an Auto Lot but a V2 component
        # is missing. Clean 422 (never a 500, never an active cycle with a NULL
        # lot); the transaction rolls back so nothing is written.
        raise HTTPException(
            status_code=422, detail=_auto_lot_missing_detail(exc.missing),
        ) from exc
    except LotNumberTooLongError as exc:
        # Auto Lot would exceed lot_no's 100-char limit — clean 422 (round
        # 8-5A convention; V2 formula since 8-12A). The transaction rolls
        # back, so the plot never ends up stranded active-with-no-cycle.
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except IntegrityError as exc:
        raise HTTPException(
            status_code=409,
            detail="Conflict reactivating the plot or starting its new planting cycle",
        ) from exc

    # Both rows have server-computed timestamps that can be expired by flush.
    # Reload plot scalars before its relationships, then reload the cycle.
    await db.refresh(plot)
    await db.refresh(
        plot, attribute_names=["assignments", "supplier", "active_cycle", "access_phones"]
    )
    await db.refresh(cycle)
    await ActivityLogger(db).log(
        user=current_user, action_type="update", action="plot.reactivated_with_cycle",
        resource_type="plot", resource_id=str(plot.id), risk_level="medium",
    )
    return PlotWithCycleCreateResult(plot=_to_read(plot), cycle=PlotCycleRead.model_validate(cycle))


@router.put("/{plot_id}/assignments", response_model=PlotRead, dependencies=[
    Depends(require_permission(PermissionKey.PLOTS_ASSIGN)),
    Depends(get_rls_context),
])
async def assign_users(
    plot_id: UUID,
    payload: PlotAssignRequest,
    db: AsyncSession = Depends(get_db),
) -> PlotRead:
    """Replace the full set of users assigned to a plot (idempotent PUT)."""
    plot = await repo.get_plot(db, plot_id)
    if plot is None:
        raise HTTPException(status_code=404, detail="Plot not found")
    plot = await repo.set_plot_assignments(db, plot, payload.user_ids)
    return _to_read(plot)
