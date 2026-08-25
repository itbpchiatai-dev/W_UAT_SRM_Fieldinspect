"""Plot + cycle Excel import (round 7.5; rollover round 7.8; unified
start_next_cycle round 8-2.7.1; reactivate_plot_with_cycle round 8-6H) —
parse, validate, execute.

Six actions, nothing else:
  create_plot_with_cycle    — new Plot (+ QR key) + its first active cycle
  start_new_cycle           — existing active plot with NO active cycle → new cycle
  update_current_cycle      — existing active plot WITH an active cycle → edit plan
  close_and_start_new_cycle — existing active plot WITH an active cycle → close it
                              (harvested) and open a fresh active cycle in one row
  start_next_cycle          — unified "start the next cycle" action for everyday
                              use (round 8-2.7.1): the backend inspects the
                              plot's CURRENT state and resolves to whichever of
                              the two behaviors above actually applies — no
                              active cycle → behaves like start_new_cycle; an
                              active cycle → behaves like close_and_start_new_
                              cycle. The resolution (`resolved_action`) is
                              computed fresh under the plot's row lock at
                              commit time — a preview result is never trusted.
                              start_new_cycle/close_and_start_new_cycle remain
                              fully supported for backward compatibility; their
                              own behavior is unchanged by this action's
                              existence. NEVER opens an inactive plot — an
                              inactive plot must use reactivate_plot_with_cycle
                              instead (an explicit, separate action).
  reactivate_plot_with_cycle — existing INACTIVE plot with NO active cycle →
                              reopens the plot (is_active=true) AND starts its
                              first new cycle, atomically (round 8-6H). The
                              only action that ever flips a plot from inactive
                              to active; requires the caller to hold BOTH the
                              activation permission (plots.delete) and
                              plots.update (checked by the endpoint layer for
                              the API; ctx.can_reactivate + ctx.can_update for
                              this importer). Never runs for an already-active
                              plot — that is always create_plot_with_cycle's or
                              start_next_cycle's territory instead.

Never deactivates a plot, regenerates a QR key, hard-deletes anything, or
creates an inspection record. The only actions that close a cycle are
close_and_start_new_cycle and a start_next_cycle row that resolves to a
rollover — both close the existing active cycle as harvested (history
preserved) and open the next one; the other actions never touch a cycle's
status. Preview is read-only; commit is all-or-nothing (the endpoint's single
get_db transaction — helpers only flush, so any failure rolls the whole file
back). Commit re-parses + re-validates server-side; it never trusts a preview
result sent by the client.

No spreadsheet-parser dependency — rows come from services/excel_reader.py's
hand-rolled reader (same rationale as excel_workbook.py's writer).
"""
from __future__ import annotations

import asyncio
import datetime
import hashlib
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.plot_access_password import (
    PlotAccessPasswordPolicyError,
    PlotAccessPepperMissingError,
    build_plot_access_password_lookup_digest,
    hash_plot_access_password,
    validate_plot_access_password,
)
from app.core.phone import normalize_thai_mobile
from app.db.models.plot import Plot
from app.db.models.plot_cycle import CYCLE_STATUS_HARVESTED, PlotCycle
from app.db.models.record import Record
from app.db.models.supplier import Supplier
from app.repositories import plot_access_credential_repository as credential_repo
from app.repositories import plot_access_phone_repository as phone_repo
from app.repositories import plot_cycle_repository as plot_cycle_repo
from app.repositories import plot_repository as plot_repo
from app.repositories import supplier_repository as supplier_repo
from app.schemas.plot import PlotAccessPhoneConfig, PlotCreate, normalize_and_validate_phone_config
from app.services.cycle_reference_fields import normalize_cycle_reference_text
from app.schemas.plot_import import (
    PlotImportCommitResult,
    PlotImportCredentialPreviewStateRow,
    PlotImportFinalPlotPreviewStateRow,
    PlotImportPreview,
    PlotImportPreviewState,
    PlotImportPreviewStateRow,
    PlotImportRowPayload,
    PlotImportRowResult,
)
from app.services.excel_reader import ExcelParseError, read_first_sheet
from app.services.lot_number import (
    AutoLotMissingComponentError,
    LotNumberTooLongError,
    auto_lot_preview,
    format_auto_lot_no,
    normalize_supplier_lot_no,
    normalize_p_code,
    normalize_po_number,
)
from app.services.loggers.activity_logger import ActivityLogger
from app.services import master_data_validation

# --- Actions --------------------------------------------------------------
ACTION_CREATE = "create_plot_with_cycle"
ACTION_START = "start_new_cycle"
ACTION_UPDATE = "update_current_cycle"
ACTION_ROLLOVER = "close_and_start_new_cycle"
ACTION_START_NEXT = "start_next_cycle"
# Round 8-6H — explicit-only action: opens an INACTIVE plot and starts its
# first new cycle atomically. Never inferred from any other action; a row
# must name this action by exact string to reactivate a plot. See the
# module docstring for the full precondition/permission contract.
ACTION_REACTIVATE_WITH_CYCLE = "reactivate_plot_with_cycle"
# Round 8-7A — explicit-only action: closes the plot's ACTIVE cycle as
# harvested and stamps the REAL harvested-yield figures, while leaving
# Plot.is_active untouched (true) so a new cycle can start later. Never
# creates a new cycle (unlike every _NEW_CYCLE_ACTIONS member below) and
# never flips is_active — the one action that is neither "open a cycle" nor
# "change the plot's activation state".
ACTION_FINAL = "final_plot"
SUPPORTED_ACTIONS: tuple[str, ...] = (
    ACTION_CREATE, ACTION_START, ACTION_UPDATE, ACTION_ROLLOVER, ACTION_START_NEXT,
    ACTION_REACTIVATE_WITH_CYCLE, ACTION_FINAL,
)

# New-cycle actions that require pCode nonblank (round 8-13A: poNumber is no
# longer required here — see the check below) and are subject to the Auto Lot
# length pre-check (round 8-6H folds reactivate_plot_with_cycle into this
# existing set rather than repeating it at each call site).
# ACTION_FINAL is deliberately EXCLUDED — it never opens a cycle, so it has no
# lot/PO of its own to require.
_NEW_CYCLE_ACTIONS: tuple[str, ...] = (
    ACTION_CREATE, ACTION_START, ACTION_ROLLOVER, ACTION_START_NEXT,
    ACTION_REACTIVATE_WITH_CYCLE,
)

# Default close reason stamped on the cycle a rollover closes (round 7.8). The
# import contract is 18 columns (cycleLabel added round 8.0).
ROLLOVER_CLOSE_REASON = "Closed by Excel import rollover"

# Close reason for a start_next_cycle row that RESOLVES to a rollover (round
# 8-2.7.1) — kept distinct from ROLLOVER_CLOSE_REASON (used by the explicit
# close_and_start_new_cycle action) so the closed cycle's audit trail shows
# which import action actually triggered the close; nothing depends on the
# two being equal.
ROLLOVER_CLOSE_REASON_START_NEXT = "Closed by Excel import start_next_cycle"

# Close reason stamped on the cycle a final_plot row closes (round 8-7A) —
# its own distinct string, same rationale as ROLLOVER_CLOSE_REASON_START_NEXT.
FINAL_PLOT_CLOSE_REASON = "Closed by Excel import final_plot"

# DB field length limits — must mirror the ORM models so a validation error is
# raised before the DB ever sees the value (prevents DataError → HTTP 500).
_MAX_SUPPLIER_CODE = 50
_MAX_PLOT_CODE = 50
_MAX_PLOT_NAME = 255
_MAX_VILLAGE = 255
_MAX_DISTRICT = 255
_MAX_PROVINCE = 100
_MAX_CROP = 100
_MAX_VARIETY = 100
_MAX_CYCLE_LABEL = 100
_MAX_LOT_NO = 100
_MAX_PO_NUMBER = 100
_MAX_P_CODE = 100
_MAX_EXPECTED_YIELD_UNIT = 20
# Round 8-21A — mirrors plot_cycles.oracle_supplier_code/oracle_invoice/
# ref_account (VARCHAR(255), migration 0050).
_MAX_ORACLE_SUPPLIER_CODE = 255
_MAX_ORACLE_INVOICE = 255
_MAX_REF_ACCOUNT = 255

# Round 8-10B — final_plot's yield figures are ALWAYS kilograms and the server
# stamps the unit itself. Round 8-7A.1's finalYieldUnit allowlist
# ({"kg","g","ตัน","ผล","ลัง"}) and its length cap are gone with the column
# they policed: an import can no longer express any other unit, so there is
# nothing left to validate. expectedYieldUnit is untouched — it is a PLAN
# field, still free-form, still only length-checked.
#
# This does NOT rewrite history: a PlotCycle closed before this round keeps
# whatever unit it was given, and PlotCycle.final_yield_unit / the report's
# own column are unchanged.
FINAL_PLOT_FIXED_YIELD_UNIT = "kg"

# Column headers the template ships with (== the keys rows are read by). Kept
# in one place so the endpoint's template builder and the reader agree.
# primaryPhone/additionalPhones (round 8-3E) sit right after plotName — the
# reader maps by header NAME (services/excel_reader.py), never position, so
# an older file uploaded without these two columns simply has no such keys in
# its row dicts and is read exactly as before (see _parse_row/_str below).
# Round 8-5B — poNumber/pCode added right after cycleLabel and before lotNo
# (22 columns total). The reader maps by header NAME, so an older 20-column
# file simply has no such keys (see _parse_row / old-file handling in
# _validate_row).
# Round 8-6J — currentPlotStatus appended last: purely informational (never
# read by _parse_row / _Parsed below), so an older 22-column file uploaded
# without it parses exactly as before, and editing this cell in a downloaded
# file has zero effect on what gets executed — only the `action` column does.
# Round 8-7A — six more columns appended for the final_plot action
# (harvestYield/finalYieldAfterClean/finalYieldUnit/harvestDate/
# finalInspectionRecordId/finalNote). Same backward-compat guarantee: the
# reader maps by header NAME, so a pre-8-7A 23-column file simply has no
# such keys and every existing action parses exactly as before.
# Round 8-21A — oracleSupplierCode/oracleInvoice/refAccount added right after
# supplierLotNo: three independent, OPTIONAL, free-text back-office reference
# columns (see app/services/cycle_reference_fields.py). Same backward-compat
# guarantee as every other addition here: an older file with no such columns
# simply has no such keys in its row dicts. update_current_cycle treats these
# three DIFFERENTLY from poNumber/pCode/supplierLotNo though — see
# _column_present/_execute_row: a MISSING column preserves the existing
# value, but a PRESENT-and-blank cell clears it (poNumber/pCode/
# supplierLotNo never clear via a blank Excel cell at all).
IMPORT_COLUMNS: list[str] = [
    "action", "supplierCode", "plotCode", "plotName", "primaryPhone", "additionalPhones",
    "village", "district",
    "province", "latitude", "longitude", "rai", "crop", "variety", "cycleLabel",
    "poNumber", "pCode", "lotNo", "supplierLotNo",
    "oracleSupplierCode", "oracleInvoice", "refAccount",
    "plantingDate", "plantCount", "expectedYieldFull", "expectedYieldUnit",
    "currentPlotStatus",
    # Round 8-10B — finalYieldUnit and finalInspectionRecordId were REMOVED
    # from the input contract. Both were things a user could get wrong but
    # never had a reason to decide: the figures are always kilograms, and the
    # record to snapshot is always the cycle's own latest active one, which the
    # server already resolved for a blank cell. They live on in PlotCycle, the
    # report, and every read model — only the Excel columns are gone. A legacy
    # workbook that still carries them is accepted while the cells are blank
    # and REJECTED with a clear message when they are not (see _legacy_final_
    # plot_column_errors) — never silently ignored.
    "harvestYield", "finalYieldAfterClean", "harvestDate", "finalNote",
    # Round 8-9B.1 — plot inspection password ("รหัสยืนยันแปลง").
    #   inspectionPasswordStatus — INFORMATIONAL ONLY, exactly like
    #     currentPlotStatus: exported so the user can see which plots already
    #     have a password, NEVER read by the parser. Editing it in the file has
    #     no effect whatsoever on what a commit writes.
    #   newInspectionPassword — the ONE input column. Blank = keep whatever the
    #     plot already has (the overwhelmingly common case); non-blank = set or
    #     replace. Deliberately composable with EVERY action rather than being
    #     an action of its own, so one row can change the cycle and the
    #     password together.
    "inspectionPasswordStatus", "newInspectionPassword",
]
MAX_IMPORT_ROWS = 1000

# inspectionPasswordStatus cell values (informational). Never parsed back.
INSPECTION_PASSWORD_STATUS_CONFIGURED = "configured"
INSPECTION_PASSWORD_STATUS_NOT_CONFIGURED = "not_configured"

# What a row will do to the plot's credential — the SAFE metadata the preview
# and the result workbook show. Never accompanied by the password itself.
CREDENTIAL_CHANGE_SET = "set"          # no credential yet → this row creates one
CREDENTIAL_CHANGE_REPLACE = "replace"  # a credential exists → this row replaces it
# (a blank newInspectionPassword is None — "keep the existing password")

# Same cap PlotAccessPhoneConfig enforces (schemas/plot.py) — kept as its own
# constant here (not imported) so a row over the limit gets a Thai error
# message from THIS file's own vocabulary rather than a raw pydantic one.
MAX_ADDITIONAL_PHONES = 10

# Row 2 of the shipped template (round 8-2.1) is a human-readable Thai guidance
# row, not data: its `action` cell starts with this exact, stable marker so the
# importer can detect and skip exactly that row. See _is_template_description_
# row, which matches on this PREFIX rather than full equality — round 8-2.7
# appended the "which action do I use" guidance to the visible cell text, and
# prefix-matching is what lets a file generated before that change (a fixture,
# or a template a user downloaded earlier and kept re-using) still be
# recognized, instead of suddenly failing to skip and validating as a bogus
# "unknown action" row.
TEMPLATE_DESCRIPTION_MARKER = "คำอธิบาย (ระบบไม่นำเข้าแถวนี้)"

# The full row-2 `action` cell text: the stable marker above, followed by the
# "which action do I use" guidance (round 8-2.7; narrowed to start_next_cycle
# round 8-2.7.1). Never compared for equality elsewhere — only ever built here
# and matched by prefix, so this text can keep changing across rounds without
# breaking the skip detection of any file (old or new) that starts with it.
TEMPLATE_DESCRIPTION_ACTION = (
    TEMPLATE_DESCRIPTION_MARKER + " — action หลักมี 3 แบบ: "
    "create_plot_with_cycle = สร้างแปลงใหม่พร้อมรอบปลูกแรก, "
    "update_current_cycle = แก้ข้อมูลรอบปลูกที่กำลังเปิดอยู่ โดยไม่สร้างรอบใหม่, "
    "start_next_cycle = เริ่มรอบถัดไป ระบบจะตรวจสถานะแปลงและปิดรอบเดิมให้อัตโนมัติ"
    "ถ้ายังมีรอบเปิดอยู่ (ต้องระบุ cycleLabel). "
    "start_new_cycle/close_and_start_new_cycle ยังใช้งานได้เหมือนเดิม "
    "(ไม่แสดงเป็นตัวอย่างหลักอีกต่อไป)"
)

# One Thai description per import column, keyed by the exact technical header so
# the description row can never drift from IMPORT_COLUMNS — the template builds
# row 2 as [TEMPLATE_COLUMN_DESCRIPTIONS[col] for col in IMPORT_COLUMNS]. The
# `action` column's own cell is the skip marker itself (it doubles as the
# "not imported" note); the three example rows below it (rows 3-5) document
# the three everyday action values (round 8-2.7.1).
TEMPLATE_COLUMN_DESCRIPTIONS: dict[str, str] = {
    "action": TEMPLATE_DESCRIPTION_ACTION,
    "supplierCode": "รหัส Supplier ที่มีอยู่ในระบบ เช่น SUP001 (จำเป็นทุก action)",
    "plotCode": "รหัสแปลง เช่น P001: สร้างใหม่ต้องไม่ซ้ำ; action อื่นต้องเป็นแปลงที่มีอยู่แล้ว",
    "plotName": "ชื่อแปลง (จำเป็นเฉพาะ create_plot_with_cycle)",
    "primaryPhone": "เบอร์หลักสำหรับเข้าตรวจแปลง เช่น 0845552162; "
                    "เว้นว่างทั้งเบอร์หลักและเบอร์เสริมเพื่อคงค่าเดิมในแปลงที่มีอยู่",
    "additionalPhones": "เบอร์เสริมสำหรับเข้าตรวจแปลง คั่นหลายเบอร์ด้วย comma "
                         "เช่น 0855551234,0866661234; ต้องมีเบอร์หลักเมื่อระบุเบอร์เสริม",
    "village": "หมู่บ้าน/ตำบลของแปลง (ใช้ตอนสร้างแปลงใหม่, ไม่บังคับ)",
    "district": "อำเภอของแปลง (ใช้ตอนสร้างแปลงใหม่, ไม่บังคับ)",
    "province": "จังหวัดของแปลง (ใช้ตอนสร้างแปลงใหม่, ไม่บังคับ)",
    "latitude": "ละติจูด เช่น 18.7883 ค่าระหว่าง -90 ถึง 90 (ใช้ตอนสร้างใหม่, ไม่บังคับ)",
    "longitude": "ลองจิจูด เช่น 98.9853 ค่าระหว่าง -180 ถึง 180 (ใช้ตอนสร้างใหม่, ไม่บังคับ)",
    "rai": "พื้นที่แปลง หน่วยไร่ เป็นตัวเลข 0 ขึ้นไป (ใช้ตอนสร้างใหม่, ไม่บังคับ)",
    "crop": "ชนิดพืชของรอบปลูก เช่น พริก หรือ เมล่อน (ไม่บังคับ)",
    "variety": "พันธุ์/สายพันธุ์ของรอบปลูก เช่น พริกขี้หนู (ไม่บังคับ)",
    # Round 8-17A.1 — required (nonblank) for every action that opens a NEW
    # cycle (create_plot_with_cycle/start_new_cycle/close_and_start_new_cycle/
    # start_next_cycle/reactivate_plot_with_cycle), regardless of Auto or
    # Manual lot. update_current_cycle also requires it UNLESS the row leaves
    # the cell blank on a cycle that is already unlabeled (legacy data — no
    # forced backfill). final_plot never uses it.
    "cycleLabel": "ชื่อรอบปลูกที่ผู้ใช้เข้าใจ เช่น jun2026 หรือ รอบ มิ.ย. 2026 "
                  "(จำเป็นสำหรับ action ที่เริ่มรอบปลูกใหม่ทุกกรณี — ทั้ง Auto และ Manual Lot; "
                  "update_current_cycle บังคับเช่นกันหากรอบปัจจุบันมีชื่อรอบอยู่แล้ว; "
                  "final_plot ไม่ใช้คอลัมน์นี้)",
    "poNumber": "เลข PO ของรอบปลูก (ไม่บังคับ) เว้นว่างได้ เช่น PO25001 "
                "(ระบบแปลงเป็นตัวพิมพ์ใหญ่เมื่อกรอก); "
                "update_current_cycle เว้นว่างเพื่อคงค่าเดิม",
    "pCode": "รหัสสินค้า (P.Code) ของรอบปลูก เช่น Melon-A; "
             "จำเป็นสำหรับ create_plot_with_cycle และ start_next_cycle; "
             "update_current_cycle เว้นว่างเพื่อคงค่าเดิม",
    "lotNo": "เลข Lot ของรอบปลูก: เว้นว่าง = ระบบสร้าง Auto Lot ({ชื่อรอบปลูก}-{รหัส Supplier}-{P.Code}-{เลขรัน}); กรอกเอง = ใช้ค่าที่กรอก (Manual)",
    "supplierLotNo": "เลข Lot ที่ Supplier กำหนดสำหรับรอบปลูกนี้ ไม่เกี่ยวกับ Auto Lot ของระบบ",
    # Round 8-21A — three independent, OPTIONAL reference columns. The
    # update_current_cycle note is deliberately different from poNumber/
    # pCode/supplierLotNo above: those three only ever PRESERVE on a blank
    # cell (clearing needs the admin UI). These three CLEAR on a blank cell
    # when the column is present in the file at all — only a file with the
    # column entirely absent (e.g. an older download) preserves the
    # existing value.
    "oracleSupplierCode": "รหัส Supplier ฝั่ง Oracle สำหรับรอบปลูกนี้ (ไม่บังคับ); "
                           "update_current_cycle: เว้นว่างเซลล์นี้ (แต่คอลัมน์ยังอยู่ในไฟล์) "
                           "= ล้างค่า, ไม่มีคอลัมน์นี้ในไฟล์เลย = คงค่าเดิม",
    "oracleInvoice": "เลขที่ใบแจ้งหนี้ (Invoice) ฝั่ง Oracle สำหรับรอบปลูกนี้ (ไม่บังคับ); "
                      "update_current_cycle: เว้นว่างเซลล์นี้ (แต่คอลัมน์ยังอยู่ในไฟล์) "
                      "= ล้างค่า, ไม่มีคอลัมน์นี้ในไฟล์เลย = คงค่าเดิม",
    "refAccount": "รหัสบัญชีอ้างอิง (Ref Account) สำหรับรอบปลูกนี้ (ไม่บังคับ); "
                  "update_current_cycle: เว้นว่างเซลล์นี้ (แต่คอลัมน์ยังอยู่ในไฟล์) "
                  "= ล้างค่า, ไม่มีคอลัมน์นี้ในไฟล์เลย = คงค่าเดิม",
    "plantingDate": "วันที่ปลูก รูปแบบ YYYY-MM-DD เช่น 2026-06-01 (ไม่บังคับ)",
    "plantCount": "จำนวนต้น/จำนวนปลูก ต้องเป็นจำนวนเต็ม 0 ขึ้นไป (ไม่บังคับ)",
    "expectedYieldFull": "ผลผลิตที่คาดเมื่อได้ 100% เป็นตัวเลข 0 ขึ้นไป เช่น 800 (ไม่บังคับ)",
    "expectedYieldUnit": "หน่วยผลผลิต เช่น kg, g, ตัน, ผล หรือ ลัง; ต้องกรอกเมื่อมี expectedYieldFull",
    "currentPlotStatus": (
        "สถานะแปลงปัจจุบันเพื่อใช้อ้างอิงเท่านั้น การเปลี่ยนค่าในช่องนี้ไม่ทำให้สถานะแปลงเปลี่ยน "
        "กรุณาใช้ action ที่ถูกต้อง"
    ),
    # Round 8-10B — the unit is stated in the description because the column
    # that used to carry it is gone; these two numbers are ALWAYS kilograms.
    "harvestYield": "ผลผลิตตอนเก็บเกี่ยว หน่วยกิโลกรัม (kg) สำหรับ final_plot",
    "finalYieldAfterClean": "ผลผลิตจริงหลังทำความสะอาด หน่วยกิโลกรัม (kg) สำหรับ final_plot",
    "harvestDate": "วันที่เก็บเกี่ยว รูปแบบ YYYY-MM-DD",
    "finalNote": "หมายเหตุผลการเก็บเกี่ยว ไม่บังคับ",
    "inspectionPasswordStatus": "สถานะรหัสยืนยันแปลงปัจจุบัน ใช้ดูข้อมูลเท่านั้น",
    "newInspectionPassword": (
        "กรอกตัวเลข 4 ถึง 20 หลักเมื่อต้องการตั้งหรือเปลี่ยนรหัส เว้นว่างเพื่อคงรหัสเดิม"
    ),
}

# Excel serial-date epoch (accounts for the historical 1900 leap-year bug):
# serial 1 == 1900-01-01, and 1899-12-30 + N days lands correctly.
_EXCEL_EPOCH = datetime.date(1899, 12, 30)


class ImportFileError(ValueError):
    """The file can't be imported at all (bad/empty/oversized/wrong columns) —
    a whole-request 422, distinct from per-row validation errors."""


class ImportHasErrors(Exception):
    """Commit was asked to run but at least one row is invalid — nothing is
    written. Carries the full preview so the caller can surface row errors, and
    (round 8-2.4) the raw row states so the commit-report endpoint can render a
    BLOCKED validation workbook (raw input + structured error codes)."""

    def __init__(
        self, preview: PlotImportPreview, states: list["_RowState"] | None = None
    ) -> None:
        super().__init__("Import has row errors")
        self.preview = preview
        self.states = states or []


# --- Preview-state conflict (round 8-2.7.2) -------------------------------

# Machine-readable reason codes for a preview-state conflict — surfaced to the
# frontend so it can show the right message and disable Commit until the user
# Previews again (never parsed from Thai text).
PREVIEW_STATE_CONFLICT_CODE = "preview_state_conflict"

# Reason sub-codes (internal + reported in `reason`); the user-facing message
# is chosen per case below, never derived from these.
_PS_MISSING = "missing_preview_state"
_PS_DIGEST_MISMATCH = "file_digest_mismatch"
_PS_ROW_SET_MISMATCH = "row_set_mismatch"
_PS_RESOLUTION_CHANGED = "resolution_changed"

_MSG_MISSING_PREVIEW_STATE = "กรุณาตรวจสอบไฟล์ด้วย Preview ก่อนยืนยันนำเข้า"
_MSG_FILE_DIGEST_MISMATCH = "ไฟล์มีการเปลี่ยนแปลงหลังการตรวจสอบ กรุณา Preview ใหม่"
_MSG_STATE_CHANGED = "สถานะรอบปลูกมีการเปลี่ยนแปลง กรุณาตรวจสอบไฟล์อีกครั้งก่อนนำเข้า"
# Round 8-10B — the drift message a user sees when the record the server would
# snapshot is no longer the one they approved in Preview.
_MSG_FINAL_RECORD_CHANGED = (
    "บันทึกการตรวจล่าสุดของรอบปลูกมีการเปลี่ยนแปลง กรุณาตรวจสอบไฟล์อีกครั้งก่อนนำเข้า"
)
# Informational preview notes (never errors, never warnings).
_MSG_FINAL_RECORD_FOUND = "พบบันทึกการตรวจที่ใช้สรุป"
_MSG_FINAL_RECORD_NONE = "ไม่มีบันทึกการตรวจที่ใช้สรุป"


class ImportPreviewStateConflict(Exception):
    """Round 8-2.7.2: the file bytes or a plot's live state diverged from the
    read-only preview the user approved, so a start_next_cycle row would now
    resolve to a DIFFERENT branch (start ⇄ rollover) or close a DIFFERENT
    cycle than shown. Raised BEFORE any row executes — nothing is written.

    Carries a machine-readable `reason`, the user-facing `message`, and the
    Excel row numbers whose resolution changed (empty for a file-level digest/
    missing-state conflict) so the endpoint can report precisely — without the
    frontend ever parsing Thai text."""

    def __init__(self, reason: str, message: str, changed_rows: list[int] | None = None) -> None:
        super().__init__(message)
        self.reason = reason
        self.message = message
        self.changed_rows = changed_rows or []


def file_digest(content: bytes) -> str:
    """SHA-256 of the raw uploaded file bytes, hex. Binds a preview-state
    expectation to the exact file that was Previewed (round 8-2.7.2). Never
    logs or returns the file content itself — only its digest."""
    return hashlib.sha256(content).hexdigest()


@dataclass
class ImportContext:
    # None ⇒ caller sees all suppliers (scope 'all'); otherwise the single
    # supplier a supplier-scoped caller may import for.
    allowed_supplier_id: UUID | None
    can_create: bool
    can_update: bool
    # Round 8-6H — the activation privilege (plots.delete), required IN
    # ADDITION to can_update for reactivate_plot_with_cycle rows. Defaults to
    # False so every existing ImportContext(...) call site (tests included)
    # that doesn't pass it explicitly is correctly denied this new action,
    # never silently granted it.
    can_reactivate: bool = False
    # Stamped as closed_by_id on the cycle a close_and_start_new_cycle row closes
    # (round 7.8). None only in DB-free unit tests that don't exercise rollover.
    user_id: UUID | None = None


@dataclass
class _Parsed:
    action: str | None = None
    supplier_code: str | None = None
    plot_code: str | None = None
    plot_name: str | None = None
    village: str | None = None
    district: str | None = None
    province: str | None = None
    latitude: Decimal | None = None
    longitude: Decimal | None = None
    rai: Decimal | None = None
    crop: str | None = None
    variety: str | None = None
    cycle_label: str | None = None
    lot_no: str | None = None
    # PO / P.Code (round 8-5B) — normalized (PO upper-cased, both trimmed) in
    # _parse_row. None when the column is blank or absent (old 20-column file).
    po_number: str | None = None
    p_code: str | None = None
    # Round 8-12A — the SUPPLIER's own lot number for the cycle. Independent of
    # lot_no: never feeds the Auto Lot formula or the running number. None when
    # the column is blank or absent (a pre-8-12A workbook simply has no key).
    supplier_lot_no: str | None = None
    # Round 8-21A — three independent, OPTIONAL back-office reference fields
    # (trim, blank->None; see app/services/cycle_reference_fields.py). The
    # _given flags are True iff the COLUMN itself exists in this workbook's
    # header row (regardless of whether this row's cell is blank) — the one
    # extra bit update_current_cycle needs to tell "column absent → preserve"
    # from "column present, cell blank → clear" (see _execute_row). Every
    # other action ignores the _given flags entirely; a new cycle simply gets
    # whatever value was parsed (None if blank/absent, same as any other
    # optional new-cycle field).
    oracle_supplier_code: str | None = None
    oracle_invoice: str | None = None
    ref_account: str | None = None
    oracle_supplier_code_given: bool = False
    oracle_invoice_given: bool = False
    ref_account_given: bool = False
    planting_date: datetime.date | None = None
    plant_count: int | None = None
    expected_yield_full: Decimal | None = None
    expected_yield_unit: str | None = None
    # Access-phone columns (round 8-3E) — canonical Thai-mobile digits, or
    # None/[] when both cells were left blank. See _phone_config for the
    # parsing/validation rules and _execute_row for how a row applies them
    # (create/preserve/replace — see plot_import.py's module docstring).
    primary_phone: str | None = None
    additional_phones: list[str] = field(default_factory=list)
    # Actual harvest — final_plot only (round 8-7A). harvest_yield/
    # final_yield_after_clean/harvest_date are the real figures read from the
    # file. Round 8-10B: final_yield_unit is no longer read at all — _parse_row
    # stamps FINAL_PLOT_FIXED_YIELD_UNIT — and final_inspection_record_id is
    # always None here, resolved from the database instead (see
    # _resolve_final_inspection_record). Round 8-10B.1: final_yield_unit is
    # stamped ONLY when the row's action is final_plot — every other action
    # gets None here too, since none of these five fields mean anything
    # outside final_plot. Both stay on this dataclass so the preview payload
    # and the commit path keep their existing shape.
    harvest_yield: Decimal | None = None
    final_yield_after_clean: Decimal | None = None
    final_yield_unit: str | None = None
    harvest_date: datetime.date | None = None
    final_inspection_record_id: UUID | None = None
    final_note: str | None = None
    # Round 8-9B.1 — PLAINTEXT plot inspection password, or None when the
    # newInspectionPassword cell was blank ("keep the existing one").
    #
    # This is the ONLY place in the import pipeline that holds the plaintext,
    # and _Parsed is an internal dataclass that is never serialized: it is not
    # a Pydantic model, never returned from an endpoint, and _row_result
    # deliberately copies every OTHER field into PlotImportRowPayload but not
    # this one. Never log, echo, or put it in preview_state.
    new_inspection_password: str | None = None


@dataclass
class _RowState:
    row_number: int
    parsed: _Parsed
    errors: list[str] = field(default_factory=list)
    supplier: Supplier | None = None
    plot: Plot | None = None
    existing_plot_id: UUID | None = None
    active_cycle_id: UUID | None = None
    # Original untrimmed cell values keyed by header (round 8-2.4) — so the
    # result workbook can echo exactly what the user typed for an errored row
    # (e.g. an unparseable "not-a-date"), not the parser's None.
    raw: dict[str, str] = field(default_factory=dict)
    # Machine-readable reason for a structured error (round 8-2.4) — currently
    # ERROR_CODE_DUPLICATE_ROLLOVER or (round 8-2.7.1) ERROR_CODE_SAME_ACTIVE_
    # CYCLE_LABEL. Lets the report classify DUPLICATE vs ERROR without parsing
    # the Thai message text.
    error_code: str | None = None
    # Round 8-6K Part B — True when this row still needs the batched
    # cycleLabel-reuse check (reactivate_plot_with_cycle, plot resolved,
    # genuinely inactive, cycle_label given). Set by _validate_row; the
    # actual DB lookup + error assignment happens in _validate_all's second
    # pass (_apply_cycle_label_history_checks), never here — this flag is
    # the only thing that crosses that boundary.
    needs_cycle_label_history_check: bool = False
    # cycle_no of the cycle this row created/edited on a successful commit
    # (round 8-2.4) — set by _execute_row. None for preview / errored rows.
    result_cycle_no: int | None = None
    # start_next_cycle only (round 8-2.7.1): which of the two legacy actions
    # this row resolves to — ACTION_START (no active cycle) or ACTION_ROLLOVER
    # (an active cycle exists). Set during validation (a preview/estimate) and
    # OVERWRITTEN with the real outcome by _execute_row at commit time, since
    # state may have changed between preview and commit. None for every other
    # action, and None for a start_next_cycle row that errors before resolving
    # (e.g. same_active_cycle_label).
    resolved_action: str | None = None
    # start_next_cycle only, when it resolves to a rollover: the cycle_no/label
    # of the CURRENTLY active cycle it would close — lets the preview/report
    # tell the user exactly which cycle is about to be closed, using data
    # already loaded by the active-cycle lookup (no extra query).
    current_cycle_no: int | None = None
    current_cycle_label: str | None = None
    # Round 8-5B — the active cycle's existing lot/PO, captured during
    # validation for update_current_cycle's lot-preview (blank lot + existing
    # lot → preserve). None when there's no active cycle.
    active_cycle_lot_no: str | None = None
    active_cycle_po_number: str | None = None
    # Round 8-12A — the active cycle's cycleLabel/pCode, captured in the SAME
    # lookup, so update_current_cycle's Auto Lot preview can regenerate from
    # the EFFECTIVE values (row value if given, else the cycle's own) exactly
    # as the repository will at commit.
    active_cycle_label: str | None = None
    active_cycle_p_code: str | None = None
    # Round 8-15D — the active cycle's own crop/variety, captured in the SAME
    # lookup as the lot/PO/label fields above, so update_current_cycle's
    # batched Master Data check (_apply_master_data_crop_variety_checks) can
    # tell "unchanged legacy value" (exempt, even if since deactivated) from
    # a genuine change (must be active + correctly parented) with no extra
    # query. None when there's no active cycle (new-cycle actions).
    active_cycle_crop: str | None = None
    active_cycle_variety: str | None = None
    # Round 8-15D — True when this row needs the batched crop/variety-vs-
    # Master-Data check (every action except final_plot, which never touches
    # crop/variety). Set by _validate_row; the actual DB lookup + error
    # assignment happens in _validate_all's second pass
    # (_apply_master_data_crop_variety_checks), never here — same
    # cross-boundary-flag pattern as needs_cycle_label_history_check.
    needs_master_data_check: bool = False
    # Round 8-7A — active cycle's cycle_no/updated_at, captured alongside the
    # id/lot/PO above (same query, no extra cost) for final_plot's
    # preview_state binding (PlotImportFinalPlotPreviewStateRow). None when
    # there's no active cycle.
    active_cycle_no: int | None = None
    active_cycle_updated_at: datetime.datetime | None = None
    # Round 8-5B lot preview/result (see _compute_lot_preview / _execute_row):
    #   lot_mode        — 'auto' | 'manual' | 'preserve' (what this row will do).
    #   proposed_lot_no — preview string; Auto shows {cycleLabel}-{supplierCode}-
    #                     {pCode}-### (round 8-12A; running not authoritative
    #                     until commit).
    #   result_lot_no / result_lot_no_source / result_lot_running_no — the REAL
    #                     values the backend produced, set by _execute_row.
    lot_mode: str | None = None
    proposed_lot_no: str | None = None
    result_lot_no: str | None = None
    result_lot_no_source: str | None = None
    result_lot_running_no: int | None = None
    # final_plot only (round 8-7A):
    #   final_resolved_record_id — the record that WILL be snapshotted (the
    #     server's own "latest active record of this cycle" pick — see
    #     plot_cycle_repository.get_latest_active_record_for_cycle — always
    #     this, never the row's raw finalInspectionRecordId verbatim, even
    #     when the row gave one and it validated fine; see _validate_row's
    #     ACTION_FINAL branch docstring-comment for why). None when the
    #     cycle has no active record at all — finalize still proceeds with
    #     every estimate field NULL. This is what preview_state binds to and
    #     what the commit-time re-check compares against.
    #   final_warning — non-blocking (finalYieldAfterClean > harvestYield).
    #     Never sets .errors / never flips status to "error".
    final_resolved_record_id: UUID | None = None
    final_warning: str | None = None
    #   final_record_note — round 8-10B, informational: whether the server
    #     found an inspection record to snapshot. Never blocking.
    final_record_note: str | None = None
    # Round 8-9B.1 — plot inspection password, all SAFE metadata (no plaintext,
    # no hash, no digest):
    #   credential_configured — does this plot have an ACTIVE credential right
    #     now? False for a create_plot_with_cycle row (brand-new plot) and for
    #     an existing plot that has never had one. Loaded in ONE bulk query
    #     (_apply_credential_status), never per row.
    #   credential_version — the existing row's version, for the preview-state
    #     binding. None when there is no credential row at all.
    #   credential_change — CREDENTIAL_CHANGE_SET / _REPLACE / None(keep).
    #   credential_hash / credential_digest — filled ONLY during the commit's
    #     pre-lock hashing phase (_hash_credential_rows) and consumed by
    #     _execute_row. Never on the preview path, never serialized.
    credential_configured: bool | None = None
    credential_version: int | None = None
    credential_change: str | None = None
    credential_hash: str | None = None
    credential_digest: str | None = None


# --- Field parsing --------------------------------------------------------

def _str(raw: dict[str, str], key: str) -> str | None:
    v = raw.get(key)
    if v is None:
        return None
    v = v.strip()
    return v or None


def _decimal(raw: dict[str, str], key: str, errors: list[str], label: str) -> Decimal | None:
    v = _str(raw, key)
    if v is None:
        return None
    try:
        return Decimal(v)
    except (InvalidOperation, ValueError):
        errors.append(f"{label} ไม่ใช่ตัวเลข ('{v}')")
        return None


def _int(raw: dict[str, str], key: str, errors: list[str], label: str) -> int | None:
    v = _str(raw, key)
    if v is None:
        return None
    try:
        d = Decimal(v)
        if d != d.to_integral_value():
            errors.append(f"{label} ต้องเป็นจำนวนเต็ม ('{v}')")
            return None
        return int(d)
    except (InvalidOperation, ValueError):
        errors.append(f"{label} ไม่ใช่จำนวนเต็ม ('{v}')")
        return None


def _date(raw: dict[str, str], key: str, errors: list[str], label: str) -> datetime.date | None:
    v = _str(raw, key)
    if v is None:
        return None
    # yyyy-mm-dd text (what the template ships and recommends)…
    try:
        return datetime.date.fromisoformat(v[:10])
    except ValueError:
        pass
    # …or an Excel serial number, if the cell was entered as a real date.
    try:
        serial = int(Decimal(v))
        if 1 <= serial <= 60000:
            return _EXCEL_EPOCH + datetime.timedelta(days=serial)
    except (InvalidOperation, ValueError):
        pass
    errors.append(f"{label} ต้องเป็นวันที่รูปแบบ YYYY-MM-DD ('{v}')")
    return None


def _phone_config(raw: dict[str, str], errors: list[str]) -> tuple[str | None, list[str]]:
    """Parse+normalize the primaryPhone/additionalPhones columns (round 8-3E).

    Reuses normalize_thai_mobile for every number-format check (never
    reimplements it); only the STRUCTURAL rules below belong to this file —
    they mirror PlotAccessPhoneConfig's own business rules exactly (schemas/
    plot.py) so a row that passes here is guaranteed to pass that schema too
    when _execute_row builds it for phone_repo.replace_plot_access_phones.

    Returns (primary, additionals). Both None/[] means the row's phone columns
    were left ENTIRELY blank — existing-plot actions preserve the plot's
    current config unchanged; create_plot_with_cycle creates none. Every error
    is Thai, names the column, and never echoes a submitted phone number.
    """
    primary_raw = _str(raw, "primaryPhone")
    additional_raw = _str(raw, "additionalPhones")
    # Split on comma WITHOUT silently dropping empty segments (round 8-3E.1
    # Part C) — "0855551234,,0866661234" / leading "," / trailing "," /
    # whitespace-only segments must all be a row error, never a quietly
    # shortened list. A cell that's entirely blank (additional_raw is None,
    # already trimmed by _str) has zero segments and is never an error here.
    raw_segments = additional_raw.split(",") if additional_raw else []
    has_empty_segment = any(not seg.strip() for seg in raw_segments)
    additional_items = [seg.strip() for seg in raw_segments if seg.strip()]

    if primary_raw is None and not additional_items and not has_empty_segment:
        return None, []

    primary: str | None = None
    if primary_raw is not None:
        try:
            primary = normalize_thai_mobile(primary_raw)
        except ValueError:
            errors.append("primaryPhone รูปแบบไม่ถูกต้อง")

    if has_empty_segment:
        errors.append("additionalPhones มีรายการว่างระหว่าง comma")

    if len(additional_items) > MAX_ADDITIONAL_PHONES:
        errors.append(f"additionalPhones เกินจำนวนสูงสุด ({MAX_ADDITIONAL_PHONES} เบอร์)")

    additionals: list[str] = []
    had_format_error = False
    for item in additional_items:
        try:
            additionals.append(normalize_thai_mobile(item))
        except ValueError:
            had_format_error = True
    if had_format_error:
        errors.append("additionalPhones มีเบอร์ที่รูปแบบไม่ถูกต้อง")

    if primary_raw is None and (additional_items or has_empty_segment):
        errors.append("ต้องระบุ primaryPhone เมื่อระบุ additionalPhones")

    if not had_format_error:
        if len(set(additionals)) != len(additionals):
            errors.append("additionalPhones มีเบอร์ซ้ำกัน")
        if primary is not None and primary in additionals:
            errors.append("primaryPhone ซ้ำกับ additionalPhones")

    return primary, additionals


def _inspection_password(raw: dict[str, str], errors: list[str]) -> str | None:
    """Parse the newInspectionPassword column (round 8-9B.1).

    Blank/absent → None, meaning "keep whatever password the plot already has".
    Non-blank → validated by the SHARED backend policy
    (app.auth.plot_access_password.validate_plot_access_password) — this file
    deliberately does NOT restate the 4-20-digit rule, so the Excel path and
    the admin PUT can never drift apart. The returned value is the trimmed
    plaintext.

    The error message is the policy's own static Thai string. It names the
    column but NEVER echoes the submitted value — an Excel row error is
    rendered into the result workbook, which the user may forward to someone
    else.

    NOTE: inspectionPasswordStatus is deliberately not read here (or anywhere).
    It is export-only; a user editing it changes nothing.
    """
    raw_value = raw.get("newInspectionPassword")
    if raw_value is None or not raw_value.strip():
        return None
    try:
        return validate_plot_access_password(raw_value)
    except PlotAccessPasswordPolicyError as exc:
        errors.append(f"newInspectionPassword: {exc}")
        return None


def _parse_row(raw: dict[str, str], columns_present: frozenset[str] = frozenset()) -> tuple[_Parsed, list[str]]:
    errors: list[str] = []
    action = _str(raw, "action")
    p = _Parsed(
        action=action,
        supplier_code=_str(raw, "supplierCode"),
        plot_code=_str(raw, "plotCode"),
        plot_name=_str(raw, "plotName"),
        village=_str(raw, "village"),
        district=_str(raw, "district"),
        province=_str(raw, "province"),
        latitude=_decimal(raw, "latitude", errors, "Latitude"),
        longitude=_decimal(raw, "longitude", errors, "Longitude"),
        rai=_decimal(raw, "rai", errors, "พื้นที่ (ไร่)"),
        crop=_str(raw, "crop"),
        variety=_str(raw, "variety"),
        cycle_label=_str(raw, "cycleLabel"),
        po_number=normalize_po_number(_str(raw, "poNumber")),
        p_code=normalize_p_code(_str(raw, "pCode")),
        lot_no=_str(raw, "lotNo"),
        supplier_lot_no=normalize_supplier_lot_no(_str(raw, "supplierLotNo")),
        oracle_supplier_code=normalize_cycle_reference_text(_str(raw, "oracleSupplierCode")),
        oracle_invoice=normalize_cycle_reference_text(_str(raw, "oracleInvoice")),
        ref_account=normalize_cycle_reference_text(_str(raw, "refAccount")),
        oracle_supplier_code_given="oracleSupplierCode" in columns_present,
        oracle_invoice_given="oracleInvoice" in columns_present,
        ref_account_given="refAccount" in columns_present,
        planting_date=_date(raw, "plantingDate", errors, "วันที่ปลูก"),
        plant_count=_int(raw, "plantCount", errors, "จำนวนต้น"),
        expected_yield_full=_decimal(raw, "expectedYieldFull", errors, "Expected Yield"),
        expected_yield_unit=_str(raw, "expectedYieldUnit"),
        harvest_yield=_decimal(raw, "harvestYield", errors, "Harvest Yield"),
        final_yield_after_clean=_decimal(raw, "finalYieldAfterClean", errors, "Final Yield After Clean"),
        # Round 8-10B — server-owned, never read from the file: the unit is a
        # constant and the record is resolved from the database. Round
        # 8-10B.1 — scoped to final_plot ONLY. 8-10B stamped this for every
        # action's row (the file's own action column, not yet validated
        # against SUPPORTED_ACTIONS at this point, so compare the raw string):
        # a create_plot_with_cycle/start_new_cycle/... row has no yield to
        # speak of and must echo null, not a fabricated "kg".
        final_yield_unit=FINAL_PLOT_FIXED_YIELD_UNIT if action == ACTION_FINAL else None,
        harvest_date=_date(raw, "harvestDate", errors, "วันที่เก็บเกี่ยว"),
        final_inspection_record_id=None,
        final_note=_str(raw, "finalNote"),
    )
    p.primary_phone, p.additional_phones = _phone_config(raw, errors)
    p.new_inspection_password = _inspection_password(raw, errors)
    errors.extend(_legacy_final_plot_column_errors(raw))
    # Plot codes are stored upper-cased (plot_repository.create_plot); normalize
    # here so preview/dedup/lookup all agree with what would be persisted.
    if p.plot_code:
        p.plot_code = p.plot_code.upper()
    return p, errors


def _legacy_final_plot_column_errors(raw: dict[str, str]) -> list[str]:
    """Round 8-10B — a pre-8-10B workbook still carries finalYieldUnit and
    finalInspectionRecordId. Blank cells are fine (the file is simply older
    than the contract), but a cell the user actually FILLED IN must fail
    loudly.

    Silently ignoring a value the user typed is the worst option available: they
    would reasonably believe the unit they wrote, or the record they chose, was
    what got used — and would only discover otherwise by reading the closed
    cycle later. An error naming the column, on the row it came from, costs one
    re-upload and removes all doubt.

    The offending VALUE is never echoed. finalInspectionRecordId is a record
    identifier, and repeating a user-supplied id back into a workbook and a log
    line is exactly the habit these columns are being removed to stop."""
    errors: list[str] = []
    if _str(raw, "finalYieldUnit") is not None:
        errors.append(
            "ไม่ต้องระบุ finalYieldUnit ระบบใช้หน่วย kg อัตโนมัติ กรุณาลบค่าจากคอลัมน์นี้"
        )
    if _str(raw, "finalInspectionRecordId") is not None:
        errors.append(
            "ไม่ต้องระบุ finalInspectionRecordId ระบบเลือกบันทึกการตรวจล่าสุดให้อัตโนมัติ "
            "กรุณาลบค่าจากคอลัมน์นี้"
        )
    return errors


def _range_errors(p: _Parsed) -> list[str]:
    errors: list[str] = []
    if p.latitude is not None and not (-90 <= p.latitude <= 90):
        errors.append("Latitude ต้องอยู่ระหว่าง -90 ถึง 90")
    if p.longitude is not None and not (-180 <= p.longitude <= 180):
        errors.append("Longitude ต้องอยู่ระหว่าง -180 ถึง 180")
    if p.rai is not None and p.rai < 0:
        errors.append("พื้นที่ (ไร่) ต้องไม่ติดลบ")
    if p.plant_count is not None and p.plant_count < 0:
        errors.append("จำนวนต้น ต้องไม่ติดลบ")
    if p.expected_yield_full is not None and p.expected_yield_full < 0:
        errors.append("Expected Yield ต้องไม่ติดลบ")
    if p.expected_yield_full is not None and not p.expected_yield_unit:
        errors.append("ต้องระบุหน่วย (expectedYieldUnit) เมื่อมี Expected Yield")
    # Round 8-7A — 0 is a valid actual yield (harvest failure/total loss is a
    # real, reportable outcome); only negative is rejected.
    if p.harvest_yield is not None and p.harvest_yield < 0:
        errors.append("Harvest Yield ต้องไม่ติดลบ")
    if p.final_yield_after_clean is not None and p.final_yield_after_clean < 0:
        errors.append("Final Yield After Clean ต้องไม่ติดลบ")
    return errors


def _check_length(
    value: str | None, max_len: int, field: str, errors: list[str],
    label: str | None = None,
) -> None:
    if value is not None and len(value) > max_len:
        name = label or field
        errors.append(f"{name} ต้องไม่เกิน {max_len} ตัวอักษร")


def _dup_key(p: _Parsed) -> tuple[str, str] | None:
    if not p.supplier_code or not p.plot_code:
        return None
    return (p.supplier_code.lower(), p.plot_code)


# --- Duplicate-rollover protection (round 8-2.3) --------------------------

# Machine-readable code for the duplicate-rollover error (round 8-2.4) — lets
# the result workbook classify a DUPLICATE row from structured state instead of
# parsing the Thai message. Additive; never removes the valid/error status.
ERROR_CODE_DUPLICATE_ROLLOVER = "duplicate_rollover"

# Shown when a close_and_start_new_cycle row's plan is IDENTICAL to the plot's
# current active cycle — a probable re-upload of an already-imported file.
_DUPLICATE_ROLLOVER_MSG = (
    "ข้อมูลรอบใหม่ตรงกับรอบปลูกที่เปิดอยู่ทั้งหมด ระบบจึงไม่จบรอบซ้ำ "
    "กรุณาตรวจสอบชื่อรอบปลูก, Lot No และวันที่ปลูก หรือใช้ update_current_cycle "
    "หากต้องการแก้รอบปัจจุบัน"
)


def _norm_plan_str(v: str | None) -> str | None:
    """Trim + treat blank as None — matches how _str normalizes import cells,
    so a stored '' and an omitted cell compare equal."""
    if v is None:
        return None
    v = v.strip()
    return v or None


def _cycle_plan_matches_import(cycle: PlotCycle, p: _Parsed) -> bool:
    """True when ALL 8 planting-plan fields of the plot's active cycle equal
    the import row's parsed plan — i.e. a close_and_start_new_cycle row that
    would just recreate the current cycle verbatim (probable duplicate upload).
    Any single differing field means a genuinely new cycle.

    Deliberately conservative: NO case-folding of crop/variety/unit and NO
    unit conversion (kg ≠ g); Decimals compare by numeric value so 1600 ==
    1600.00; blank/whitespace strings normalize to None (both sides)."""
    return (
        _norm_plan_str(cycle.crop) == _norm_plan_str(p.crop)
        and _norm_plan_str(cycle.variety) == _norm_plan_str(p.variety)
        and _norm_plan_str(cycle.cycle_label) == _norm_plan_str(p.cycle_label)
        and _norm_plan_str(cycle.lot_no) == _norm_plan_str(p.lot_no)
        and cycle.planting_date == p.planting_date
        and cycle.plant_count == p.plant_count
        and cycle.expected_yield_full == p.expected_yield_full
        and _norm_plan_str(cycle.expected_yield_unit) == _norm_plan_str(p.expected_yield_unit)
    )


# --- start_next_cycle resolution (round 8-2.7.1) ---------------------------

# Machine-readable code for a start_next_cycle row whose cycleLabel matches the
# plot's current active cycle — lets the report/frontend classify this without
# parsing the Thai message. Distinct from ERROR_CODE_DUPLICATE_ROLLOVER (which
# compares the FULL 8-field plan): this fires on the label alone, since
# cycleLabel is the one field start_next_cycle requires precisely so a typo'd
# resubmission can be caught before it closes the wrong cycle.
ERROR_CODE_SAME_ACTIVE_CYCLE_LABEL = "same_active_cycle_label"

_SAME_ACTIVE_CYCLE_LABEL_MSG = (
    "ชื่อรอบปลูก (cycleLabel) ซ้ำกับรอบที่กำลังเปิดอยู่ กรุณาเปลี่ยนเป็นชื่อรอบใหม่ "
    "หากต้องการแก้ข้อมูลรอบเดิมให้ใช้ update_current_cycle"
)

# Commit-time-only message (round 8-2.7.1 Part D/H): the plot's active-cycle
# state changed between preview and this row's commit-time re-check under the
# plot lock (a same-label collision or a full duplicate plan appeared that
# preview didn't see) — distinct wording from the preview-time messages above,
# matching the existing convention that a commit-time race gets its own
# phrasing (see ACTION_START/ACTION_ROLLOVER's own re-check messages below).
_RACE_STATE_CHANGED_MSG = "สถานะรอบปลูกมีการเปลี่ยนแปลง กรุณาตรวจสอบไฟล์อีกครั้ง"


# Round 8-6J Part E — business decision: a reactivate_plot_with_cycle row's
# cycleLabel must never reuse ANY of the plot's historical cycle labels
# (active/harvested/cancelled alike). Distinct from ERROR_CODE_SAME_ACTIVE_
# CYCLE_LABEL (start_next_cycle, compares only the currently-open cycle) —
# an inactive plot never has an active cycle, so that check can never fire
# for this action; this is the reactivate-specific equivalent, checked
# against the plot's FULL history instead.
ERROR_CODE_REACTIVATE_CYCLE_LABEL_REUSED = "reactivate_cycle_label_reused"


def _reactivate_cycle_label_reused_msg(label: str) -> str:
    return (
        f"ชื่อรอบปลูก '{label}' เคยถูกใช้กับแปลงนี้แล้ว กรุณาเปลี่ยนชื่อรอบปลูกก่อนนำเข้า"
    )


def _label_reused_in_history_labels(labels: set[str], label: str | None) -> bool:
    """Case/whitespace-insensitive match against a plot's historical cycle
    labels (round 8-6K Part B: a batch-loaded set of raw label strings from
    plot_cycle_repository.get_cycle_labels_for_plots — never a list of full
    PlotCycle objects, and never one query per plot). Same normalization
    contract as _same_active_cycle_label: trim, then casefold."""
    norm_label = _norm_plan_str(label)
    if norm_label is None or not labels:
        return False
    target = norm_label.casefold()
    return any(
        (existing := _norm_plan_str(candidate)) is not None and existing.casefold() == target
        for candidate in labels
    )


def _same_active_cycle_label(cycle: PlotCycle, p: _Parsed) -> bool:
    """True when the row's cycleLabel matches the active cycle's, after
    trim + casefold — the case/whitespace-insensitive comparison start_
    next_cycle's contract requires. Both sides None never counts as a match
    (an active cycle with no label is not "the same" as any label)."""
    active_label = _norm_plan_str(cycle.cycle_label)
    incoming_label = _norm_plan_str(p.cycle_label)
    return (
        active_label is not None and incoming_label is not None
        and active_label.casefold() == incoming_label.casefold()
    )


def _cycle_label_matches_active(active_cycle_label: str | None, row_cycle_label: str | None) -> bool:
    """final_plot's "is this the SAME cycle I think it is" confirmation
    (round 8-7A, item 4) — the opposite polarity from _same_active_cycle_
    label above (which treats both-None as "not a match", correct for its
    own duplicate-detection use). Here, both blank legitimately counts as a
    match (an active cycle with no label, confirmed by a row that also left
    cycleLabel blank, is not a mismatch) — trim + casefold otherwise."""
    a = _norm_plan_str(active_cycle_label)
    b = _norm_plan_str(row_cycle_label)
    if a is None and b is None:
        return True
    if a is None or b is None:
        return False
    return a.casefold() == b.casefold()


async def _resolve_final_inspection_record(
    db: AsyncSession, cycle_id: UUID,
) -> Record | None:
    """The ONE place that decides which Record a final_plot row's estimate
    snapshot comes from. Shared by validation (_validate_row, Preview), the
    commit-time drift check (_verify_final_plot_snapshot) and the actual write
    (_execute_row), so the three can never disagree.

    Round 8-10B — the answer is now purely server-side: the cycle's own latest
    ACTIVE record, or None. The file has no say in it.

    Round 8-7A.1 let a row name a record explicitly (finalInspectionRecordId),
    which meant validating that the id belonged to this plot+cycle, deciding
    what to do when it didn't, and keeping all three call sites in agreement
    about the answer. That column is gone: users never had a reason to pick a
    record other than the latest one, and letting a spreadsheet cell point at
    an arbitrary record id was a sharp edge with no upside. Scoping is now
    structural — the query is by cycle, and the cycle is already the plot's own
    locked active cycle, so a record from another cycle or another plot cannot
    be reached at all.

    None is a perfectly valid answer: a cycle with no inspection record still
    closes, it just gets no final ESTIMATE snapshot (unchanged behaviour)."""
    return await plot_cycle_repo.get_latest_active_record_for_cycle(db, cycle_id)


# --- Per-row validation ---------------------------------------------------

async def _validate_row(
    db: AsyncSession, row_no: int, raw: dict[str, str], ctx: ImportContext,
    duplicate_keys: set[tuple[str, str]],
    columns_present: frozenset[str] = frozenset(),
) -> _RowState:
    p, errors = _parse_row(raw, columns_present)
    errors.extend(_range_errors(p))
    state = _RowState(row_number=row_no, parsed=p, errors=errors, raw=dict(raw))

    if not p.action or p.action not in SUPPORTED_ACTIONS:
        errors.append(
            "action ต้องเป็นหนึ่งใน: " + ", ".join(SUPPORTED_ACTIONS)
        )
    if not p.supplier_code:
        errors.append("ต้องระบุ supplierCode")
    if not p.plot_code:
        errors.append("ต้องระบุ plotCode")
    # Round 8-5B — P.Code is required (nonblank) on every action that creates
    # a NEW cycle. update_current_cycle keeps it optional (blank = preserve).
    # close_and_start_new_cycle/start_new_cycle/reactivate_plot_with_cycle
    # (round 8-6H) also open a fresh cycle → required too.
    # Round 8-13A — poNumber is OPTIONAL here (dropped from this check): PO
    # left the Auto Lot formula back in round 8-12A, and this was the last
    # place a new-cycle row was still gated on it. A blank poNumber cell
    # normalizes to None (_parse_row/normalize_po_number), same as every
    # other optional column.
    # Round 8-17A.1 — cycleLabel is now required on this SAME allowlist,
    # unconditionally (independent of Auto vs Manual lot; the API-level
    # PlotCycleCreate._require_cycle_label enforces the identical rule for
    # the single-plot lifecycle endpoints that share this action set).
    # Consolidates the old start_next_cycle-only check that used to run much
    # earlier (before supplier/plot resolution) — moving it here makes
    # cycleLabel behave exactly like pCode already does: required for every
    # _NEW_CYCLE_ACTIONS member, checked at the same point, same early-return
    # semantics if the supplier/plot can't be resolved first.
    if p.action in _NEW_CYCLE_ACTIONS:
        if not p.p_code:
            errors.append("ต้องระบุ pCode สำหรับการเริ่มรอบปลูกใหม่")
        if not p.cycle_label:
            errors.append(
                "กรุณาระบุชื่อรอบปลูก เนื่องจากใช้ระบุรอบและสร้าง Lot No อัตโนมัติ"
            )
    # Round 8-7A — final_plot's four actual-harvest fields are required
    # together (mirrors the DB's own ck_plot_cycles_actual_harvest_all_or_
    # none — surfaced here as a clean per-row error, never a raw DB
    # constraint violation). 0 passes (checked separately in _range_errors);
    # only "missing" is caught here.
    if p.action == ACTION_FINAL:
        if p.harvest_yield is None:
            errors.append("ต้องระบุ harvestYield สำหรับ final_plot")
        if p.final_yield_after_clean is None:
            errors.append("ต้องระบุ finalYieldAfterClean สำหรับ final_plot")
        # Round 8-10B — no finalYieldUnit check: the file cannot supply one and
        # _parse_row always stamps FINAL_PLOT_FIXED_YIELD_UNIT, so "missing" and
        # "not in the allowlist" are both unreachable states now.
        if p.harvest_date is None:
            errors.append("ต้องระบุ harvestDate สำหรับ final_plot")

    # String-length guards — must match DB column definitions (prevents DataError).
    _check_length(p.supplier_code, _MAX_SUPPLIER_CODE, "supplierCode", errors)
    _check_length(p.plot_code, _MAX_PLOT_CODE, "plotCode", errors)
    _check_length(p.plot_name, _MAX_PLOT_NAME, "plotName", errors)
    _check_length(p.village, _MAX_VILLAGE, "village", errors)
    _check_length(p.district, _MAX_DISTRICT, "district", errors)
    _check_length(p.province, _MAX_PROVINCE, "province", errors)
    _check_length(p.crop, _MAX_CROP, "crop", errors)
    _check_length(p.variety, _MAX_VARIETY, "variety", errors)
    _check_length(p.cycle_label, _MAX_CYCLE_LABEL, "cycleLabel", errors,
                  label="ชื่อรอบปลูก (cycleLabel)")
    _check_length(p.po_number, _MAX_PO_NUMBER, "poNumber", errors)
    _check_length(p.p_code, _MAX_P_CODE, "pCode", errors)
    _check_length(p.lot_no, _MAX_LOT_NO, "lotNo", errors)
    _check_length(p.supplier_lot_no, _MAX_LOT_NO, "supplierLotNo", errors)
    _check_length(p.oracle_supplier_code, _MAX_ORACLE_SUPPLIER_CODE, "oracleSupplierCode", errors)
    _check_length(p.oracle_invoice, _MAX_ORACLE_INVOICE, "oracleInvoice", errors)
    _check_length(p.ref_account, _MAX_REF_ACCOUNT, "refAccount", errors)
    _check_length(p.expected_yield_unit, _MAX_EXPECTED_YIELD_UNIT, "expectedYieldUnit", errors)
    # Round 8-10B — final_yield_unit is a server constant ("kg"), so there is
    # no user input left to length-check.

    key = _dup_key(p)
    if key is not None and key in duplicate_keys:
        errors.append("มี supplierCode+plotCode ซ้ำในไฟล์นี้ (อนุญาต 1 แถวต่อแปลง)")

    # Per-action permission — a caller with only one of plots.create/plots.update
    # can still run the actions their permission covers.
    if p.action == ACTION_CREATE and not ctx.can_create:
        errors.append("ต้องมีสิทธิ์ plots.create สำหรับ create_plot_with_cycle")
    if (
        p.action in (ACTION_START, ACTION_UPDATE, ACTION_ROLLOVER, ACTION_START_NEXT, ACTION_FINAL)
        and not ctx.can_update
    ):
        errors.append(
            "ต้องมีสิทธิ์ plots.update สำหรับ start_new_cycle/update_current_cycle/"
            "close_and_start_new_cycle/start_next_cycle/final_plot"
        )
    # Round 8-6H — reactivate_plot_with_cycle requires BOTH the activation
    # privilege (plots.delete, mirrored by ctx.can_reactivate — same
    # privilege plain deactivate/reactivate require) AND plots.update (every
    # other cycle-creating action's requirement). Neither alone is enough; a
    # Supplier Owner who only has plots.update is denied here exactly like
    # the API endpoint's stacked require_permission dependencies deny them.
    if p.action == ACTION_REACTIVATE_WITH_CYCLE and not (ctx.can_reactivate and ctx.can_update):
        errors.append(
            "ต้องมีสิทธิ์ plots.delete และ plots.update สำหรับ reactivate_plot_with_cycle"
        )

    # Stop before DB lookups if the row is already unusable (no action/codes).
    if not p.action or p.action not in SUPPORTED_ACTIONS or not p.supplier_code or not p.plot_code:
        return state

    supplier = await supplier_repo.get_supplier_by_code(db, p.supplier_code)
    if supplier is None:
        errors.append(f"ไม่พบ Supplier รหัส '{p.supplier_code}'")
        return state
    if ctx.allowed_supplier_id is not None and supplier.id != ctx.allowed_supplier_id:
        errors.append("Supplier นี้อยู่นอกขอบเขตของคุณ")
        return state
    if not supplier.is_active:
        errors.append("Supplier นี้ถูกปิดใช้งาน")
        return state
    state.supplier = supplier

    # Round 8-12A — Auto Lot V2 overlength pre-check. MUST run after
    # state.supplier is set: the formula's supplier component is the
    # AUTHORITATIVE code from the resolved Supplier (never the row's own
    # supplierCode cell), so checking earlier would always see None and
    # silently skip. Probed at running=1000 (4 digits) so the 3→4-digit
    # growth can't slip past preview and overflow only at commit.
    supplier_code_for_lot = ctx_supplier_code(state)
    if (
        p.action in _NEW_CYCLE_ACTIONS
        and not p.lot_no and p.cycle_label and p.p_code and supplier_code_for_lot
    ):
        try:
            format_auto_lot_no(
                cycle_label=p.cycle_label, supplier_code=supplier_code_for_lot,
                p_code=p.p_code, running=1000,
            )
        except LotNumberTooLongError:
            errors.append(
                "Auto Lot ที่จะสร้าง ({ชื่อรอบปลูก}-{รหัส Supplier}-{P.Code}-{เลขรัน}) "
                "ยาวเกิน 100 ตัวอักษร กรุณาย่อ cycleLabel หรือ pCode หรือกรอก lotNo เอง"
            )

    # Round 8-12A.1 — a NEW-cycle row with a blank lotNo is asking for an Auto
    # Lot, so every component of the V2 formula must be present. Round 8-12A
    # let such a row through and silently created a cycle with NO lot at all;
    # it is now a per-row Preview error naming the missing field, which also
    # means Commit refuses the whole file (all-or-nothing) rather than writing
    # a lotless cycle. A row that gives a Manual lotNo needs none of this.
    if p.action in _NEW_CYCLE_ACTIONS and not p.lot_no:
        missing_auto: list[str] = []
        if not p.cycle_label:
            missing_auto.append("ชื่อรอบปลูก (cycleLabel)")
        if not p.p_code:
            missing_auto.append("P.Code (pCode)")
        if missing_auto:
            errors.append(
                "ต้องระบุ " + " และ ".join(missing_auto)
                + " เมื่อเว้น lotNo ว่าง (ระบบจะสร้าง Auto Lot ให้) "
                "หรือกรอก lotNo เอง"
            )
        elif not supplier_code_for_lot:
            errors.append("ไม่พบ Supplier ของแปลง กรุณาตรวจสอบข้อมูลแปลง")

    plot = await plot_repo.get_plot_by_code(db, supplier.id, p.plot_code)
    if plot is not None:
        state.plot = plot
        state.existing_plot_id = plot.id

    if p.action == ACTION_CREATE:
        if not p.plot_name:
            errors.append("ต้องระบุ plotName สำหรับ create_plot_with_cycle")
        if plot is not None:
            errors.append("plotCode นี้มีอยู่แล้วสำหรับ Supplier นี้")
        # Round 8-15D — a brand-new plot's first cycle has no "current" pair,
        # so this is always a full new-cycle crop/variety check.
        state.needs_master_data_check = True
        return state

    # start_new_cycle / update_current_cycle / close_and_start_new_cycle /
    # start_next_cycle / reactivate_plot_with_cycle all need an EXISTING
    # plot; every action except reactivate_plot_with_cycle additionally
    # needs it ACTIVE (reactivate_plot_with_cycle needs the opposite —
    # inactive — since reopening an already-active plot makes no sense).
    if plot is None:
        if p.action == ACTION_START_NEXT:
            # Round 8-2.7.1 Part B item 1: point the user at the right action
            # instead of the generic message (which still applies to the
            # three legacy actions unchanged).
            errors.append(
                "ไม่พบแปลง (plotCode) สำหรับ Supplier นี้ — หากต้องการสร้างแปลงใหม่ "
                "ให้ใช้ create_plot_with_cycle"
            )
        else:
            errors.append("ไม่พบแปลง (plotCode) สำหรับ Supplier นี้")
        return state
    if p.action == ACTION_REACTIVATE_WITH_CYCLE:
        # Round 8-6H — inverted precondition: this action ONLY runs against
        # an already-inactive plot. An active plot is a clean per-row error,
        # never silently reinterpreted as some other action.
        if plot.is_active:
            errors.append(
                "แปลงนี้เปิดใช้งานอยู่แล้ว ไม่ต้องเปิดใช้งานซ้ำ — หากต้องการเริ่มรอบปลูกใหม่ "
                "ให้ใช้ start_next_cycle"
            )
            return state
    elif not plot.is_active:
        if p.action == ACTION_START_NEXT:
            errors.append("แปลงนี้ปิดใช้งานอยู่ ไม่สามารถเริ่มรอบปลูกใหม่ได้")
        else:
            errors.append("แปลงนี้ถูกปิดถาวร (inactive) — เริ่ม/แก้รอบปลูกไม่ได้")
        return state

    active = await plot_cycle_repo.get_active_cycle_for_plot(db, plot.id)
    if active is not None:
        state.active_cycle_id = active.id
        # Round 8-5B — remember the active cycle's lot/PO so update_current_cycle
        # can preview "preserve existing lot" (blank lot + existing lot).
        state.active_cycle_lot_no = active.lot_no
        state.active_cycle_po_number = active.po_number
        state.active_cycle_label = active.cycle_label
        state.active_cycle_p_code = active.p_code
        state.active_cycle_no = active.cycle_no
        state.active_cycle_updated_at = active.updated_at
        # Round 8-15D — the active cycle's own crop/variety, for
        # update_current_cycle's "unchanged legacy value" exemption.
        state.active_cycle_crop = active.crop
        state.active_cycle_variety = active.variety
    # Round 8-15D — every action reaching this point except final_plot
    # touches crop/variety (start/rollover/start_next open a NEW cycle;
    # update_current_cycle replaces the plan; reactivate opens a first new
    # cycle on a reopened plot) — flag for the batched Master Data check.
    if p.action != ACTION_FINAL:
        state.needs_master_data_check = True
    if p.action == ACTION_START and active is not None:
        # active is already the loaded PlotCycle for this row's own plot (no
        # extra query) — naming it in the message helps the user tell whether
        # they meant the cycle that's already open (round 8-2.7).
        cycle_ref = active.cycle_label or f"รอบที่ {active.cycle_no}"
        errors.append(
            f"แปลงนี้มีรอบปลูกที่เปิดอยู่แล้ว ({cycle_ref}) — หากเป็นรอบเดิมให้ใช้ "
            "update_current_cycle หรือหากต้องการขึ้นรอบใหม่ให้ใช้ "
            "close_and_start_new_cycle"
        )
    if p.action == ACTION_UPDATE and active is None:
        errors.append("แปลงนี้ยังไม่มีรอบปลูกที่เปิดอยู่ — ใช้ start_new_cycle แทน")
    elif p.action == ACTION_UPDATE and active is not None:
        # Round 8-17A.1 — update_current_cycle replaces cycle_label in full
        # (see the fields dict built in _execute_row: unlike poNumber/pCode/
        # lotNo, cycle_label has no "blank cell = preserve" carve-out). A
        # blank cell would therefore CLEAR an existing label; block that
        # specific case only — a row that leaves a legacy (already-None)
        # label blank is a no-op, not a clear, and must keep reading back
        # fine (no forced backfill for old cycles).
        if not p.cycle_label and active.cycle_label:
            errors.append(
                "กรุณาระบุชื่อรอบปลูก เนื่องจากใช้ระบุรอบและสร้าง Lot No อัตโนมัติ"
            )
    if p.action == ACTION_ROLLOVER:
        if active is None:
            errors.append("ไม่พบรอบปลูกที่เปิดอยู่สำหรับปิดรอบ")
        elif _cycle_plan_matches_import(active, p):
            # Probable duplicate re-upload — refuse to close+recreate an
            # identical cycle (round 8-2.3). Only rollover is guarded;
            # update_current_cycle with the same plan stays valid/idempotent.
            errors.append(_DUPLICATE_ROLLOVER_MSG)
            state.error_code = ERROR_CODE_DUPLICATE_ROLLOVER
    if p.action == ACTION_START_NEXT:
        # Round 8-2.7.1 Part B: resolve to whichever legacy action the plot's
        # CURRENT state calls for. This is a preview-time estimate only —
        # _execute_row recomputes it fresh under the plot's row lock at
        # commit time and never trusts this value (Part A).
        if active is None:
            state.resolved_action = ACTION_START
        elif _same_active_cycle_label(active, p):
            # A typo'd resubmission of the SAME cycle's label would otherwise
            # silently close it — refuse (Part B item 7 / Part H).
            errors.append(_SAME_ACTIVE_CYCLE_LABEL_MSG)
            state.error_code = ERROR_CODE_SAME_ACTIVE_CYCLE_LABEL
        else:
            state.resolved_action = ACTION_ROLLOVER
            state.current_cycle_no = active.cycle_no
            state.current_cycle_label = active.cycle_label
            if _cycle_plan_matches_import(active, p):
                # Defense-in-depth reuse of the round 8-2.3 full-plan guard
                # (Part C) — the cycleLabel check above already catches the
                # everyday re-upload case since cycleLabel always differs
                # once labels don't match, but this stays as a second layer
                # for any other 7-field-identical scenario.
                errors.append(_DUPLICATE_ROLLOVER_MSG)
                state.error_code = ERROR_CODE_DUPLICATE_ROLLOVER
    if p.action == ACTION_REACTIVATE_WITH_CYCLE:
        if active is not None:
            # Defensive (round 8-6H Part A): an inactive plot should never
            # have an active cycle after the hardened deactivate invariant
            # (Part B) — this guards against any pre-existing/legacy
            # inconsistent row rather than silently rolling it over.
            errors.append(
                "พบข้อมูลไม่สอดคล้องกัน (แปลงปิดใช้งานแต่มีรอบปลูกที่เปิดอยู่) "
                "กรุณาติดต่อผู้ดูแลระบบ"
            )
        elif p.cycle_label:
            # Round 8-6K Part B — the actual history lookup is now batched
            # across every row in the file by _validate_all's second pass
            # (_apply_cycle_label_history_checks); this row only marks
            # itself as needing that check (no DB call here — the whole
            # point is ONE query for every reactivate row, not one per row).
            state.needs_cycle_label_history_check = True
    if p.action == ACTION_FINAL:
        if active is None:
            errors.append("แปลงนี้ไม่มีรอบปลูกที่เปิดอยู่ จึงไม่สามารถลงผลผลิตสุดท้ายได้")
        else:
            if not _cycle_label_matches_active(active.cycle_label, p.cycle_label):
                errors.append(
                    "ชื่อรอบปลูกในไฟล์ไม่ตรงกับรอบที่เปิดอยู่ กรุณาดาวน์โหลดข้อมูลล่าสุดและตรวจสอบอีกครั้ง"
                )
            # Round 8-10B — the snapshot source is whatever the server
            # resolves right now: the cycle's own latest ACTIVE record, or
            # nothing. This is what preview_state binds to, so a record that
            # appears or disappears between Preview and Commit is caught by
            # _verify_final_plot_snapshot rather than silently changing what
            # gets snapshotted.
            resolved_record = await _resolve_final_inspection_record(db, active.id)
            state.final_resolved_record_id = (
                resolved_record.id if resolved_record is not None else None
            )
            # Informational only — a user needs to know WHETHER a record was
            # found, since it decides whether the closed cycle gets a final
            # estimate. Never blocking: a cycle with no inspection still
            # finalizes. Never the record's id.
            state.final_record_note = (
                _MSG_FINAL_RECORD_FOUND if resolved_record is not None
                else _MSG_FINAL_RECORD_NONE
            )
            # Item 11 — non-blocking: never appended to errors, never flips
            # status to "error". Only meaningful once both values are present
            # (the "required together" check above already covers "missing").
            if (
                p.harvest_yield is not None and p.final_yield_after_clean is not None
                and p.final_yield_after_clean > p.harvest_yield
            ):
                state.final_warning = (
                    "ผลผลิตหลังทำความสะอาด (finalYieldAfterClean) มากกว่าผลผลิตตอนเก็บเกี่ยว "
                    "(harvestYield) กรุณาตรวจสอบข้อมูลอีกครั้ง"
                )
    return state


# Round 8-5B — lot modes surfaced in the preview (lotMode). 'preserve' is
# update-only (blank lot + an existing lot → keep it).
LOT_MODE_AUTO = "auto"
LOT_MODE_MANUAL = "manual"
LOT_MODE_PRESERVE = "preserve"


def ctx_supplier_code(state: "_RowState") -> str | None:
    """The AUTHORITATIVE supplier code for this row's Auto Lot (round 8-12A) —
    read off the Supplier resolved during validation (RLS/scope-checked), never
    the supplierCode CELL the user typed. The cell is only ever a lookup key;
    letting it into the lot itself would let a file mislabel another supplier's
    lot. None until the row's supplier has been resolved (or if it failed)."""
    return state.supplier.code if state.supplier is not None else None


def _compute_lot_preview(state: _RowState) -> None:
    """Set lot_mode + proposed_lot_no on a VALID row (round 8-5B; formula V2
    round 8-12A). Manual wins when a nonblank lotNo is given; otherwise Auto for
    new-cycle actions and for update_current_cycle only when there's no existing
    lot to preserve.

    The Auto preview shows {cycleLabel}-{supplierCode}-{pCode}-### — the "###"
    stands for the running number, which is allocated ONLY at commit under the
    plot lock, so preview stays read-only and never reserves a number."""
    p = state.parsed
    supplier_code = ctx_supplier_code(state)
    if p.action == ACTION_FINAL:
        # Round 8-7A — final_plot never touches lot_no/po_number at all (it
        # closes the existing cycle as-is); leave both None rather than
        # falling into the "new cycle" branch below, which would otherwise
        # invent a nonsense Auto Lot preview from this action's unrelated
        # (always-blank) po_number/lot_no fields.
        return
    if p.action == ACTION_UPDATE:
        if p.lot_no:
            state.lot_mode, state.proposed_lot_no = LOT_MODE_MANUAL, p.lot_no
        elif state.active_cycle_lot_no:
            state.lot_mode, state.proposed_lot_no = LOT_MODE_PRESERVE, state.active_cycle_lot_no
        else:
            # Round 8-12A — an update regenerates only from the EFFECTIVE
            # cycleLabel/pCode (row value, else the active cycle's own).
            label = p.cycle_label or state.active_cycle_label
            code = p.p_code or state.active_cycle_p_code
            if label and code and supplier_code:
                state.lot_mode = LOT_MODE_AUTO
                state.proposed_lot_no = auto_lot_preview(label, supplier_code, code)
            else:
                state.lot_mode, state.proposed_lot_no = LOT_MODE_PRESERVE, None
        return
    # create_plot_with_cycle / start_new_cycle / close_and_start_new_cycle /
    # start_next_cycle all open a NEW cycle → Auto unless a Manual lot is given.
    if p.lot_no:
        state.lot_mode, state.proposed_lot_no = LOT_MODE_MANUAL, p.lot_no
    else:
        state.lot_mode = LOT_MODE_AUTO
        state.proposed_lot_no = auto_lot_preview(p.cycle_label, supplier_code, p.p_code)


def _capture_lot_result(state: _RowState, cycle: PlotCycle) -> None:
    """Record the REAL lot values the backend produced (round 8-5B) so the
    commit result workbook shows the actual lotNo/source/running number, not
    the preview's XX placeholder."""
    state.result_lot_no = cycle.lot_no
    state.result_lot_no_source = cycle.lot_no_source
    state.result_lot_running_no = cycle.lot_running_no


def _row_result(state: _RowState) -> PlotImportRowResult:
    p = state.parsed
    if not state.errors:
        _compute_lot_preview(state)
    payload = PlotImportRowPayload(
        action=p.action, supplier_code=p.supplier_code, plot_code=p.plot_code,
        plot_name=p.plot_name,
        primary_phone=p.primary_phone, additional_phones=list(p.additional_phones),
        village=p.village, district=p.district,
        province=p.province, latitude=p.latitude, longitude=p.longitude,
        rai=p.rai, crop=p.crop, variety=p.variety,
        cycle_label=p.cycle_label, po_number=p.po_number, p_code=p.p_code, lot_no=p.lot_no,
        supplier_lot_no=p.supplier_lot_no,
        oracle_supplier_code=p.oracle_supplier_code, oracle_invoice=p.oracle_invoice,
        ref_account=p.ref_account,
        planting_date=p.planting_date, plant_count=p.plant_count,
        expected_yield_full=p.expected_yield_full,
        expected_yield_unit=p.expected_yield_unit,
        harvest_yield=p.harvest_yield,
        final_yield_after_clean=p.final_yield_after_clean,
        final_yield_unit=p.final_yield_unit,
        harvest_date=p.harvest_date,
        final_note=p.final_note,
        final_inspection_record_id=p.final_inspection_record_id,
    )
    return PlotImportRowResult(
        row_number=state.row_number,
        action=p.action,
        supplier_code=p.supplier_code,
        plot_code=p.plot_code,
        status="error" if state.errors else "valid",
        message="; ".join(state.errors),
        payload=payload,
        existing_plot_id=state.existing_plot_id,
        active_cycle_id=state.active_cycle_id,
        error_code=state.error_code,
        result_cycle_no=state.result_cycle_no,
        resolved_action=state.resolved_action,
        current_cycle_no=state.current_cycle_no,
        current_cycle_label=state.current_cycle_label,
        lot_mode=state.lot_mode,
        proposed_lot_no=state.proposed_lot_no,
        result_lot_no=state.result_lot_no,
        result_lot_no_source=state.result_lot_no_source,
        result_lot_running_no=state.result_lot_running_no,
        warning=state.final_warning,
        final_record_note=state.final_record_note,
        # Round 8-9B.1 — SAFE metadata only. state.parsed.new_inspection_
        # password is deliberately NOT copied anywhere into this result.
        inspection_password_configured=state.credential_configured,
        inspection_password_change=state.credential_change,
    )


def _is_template_description_row(row_no: int, raw: dict[str, str]) -> bool:
    """The shipped template's row 2 (round 8-2.1) is a Thai guidance row, not
    data. Skip it — and ONLY it — when it sits where the template puts it
    (Excel row 2) AND its action cell starts with the stable marker.

    Prefix match (not full equality) so a file whose row 2 was generated
    before round 8-2.7 appended extra guidance to the cell — an older
    fixture, or a template a user downloaded earlier and kept re-using — is
    still recognized and skipped, not misread as a bogus data row.

    A marker anywhere else is left in place so it validates as an unknown
    action (never silently dropped), and a legacy template whose row 2 is a
    real action row is untouched (its action cell is a real action, not the
    marker). Deliberately kept here, not in excel_reader.read_first_sheet,
    which is a generic reader used elsewhere and must return every data row.
    """
    return (
        row_no == 2
        and (raw.get("action") or "").strip().startswith(TEMPLATE_DESCRIPTION_MARKER)
    )


async def _validate_all(
    db: AsyncSession, content: bytes, ctx: ImportContext,
) -> list[_RowState]:
    try:
        headers, rows = read_first_sheet(content)
    except ExcelParseError as exc:
        raise ImportFileError(f"ไฟล์ไม่ถูกต้อง: {exc}") from exc

    if not any(h in headers for h in ("action", "plotCode", "supplierCode")):
        raise ImportFileError(
            "ไม่พบคอลัมน์ที่ต้องการ (action / supplierCode / plotCode) — "
            "โปรดใช้เทมเพลตล่าสุด"
        )

    # Drop the template's guidance row before anything counts, dedups,
    # validates, previews, or commits it. Everything below sees data rows only.
    rows = [rc for rc in rows if not _is_template_description_row(*rc)]

    if not rows:
        raise ImportFileError("ไม่มีข้อมูลในไฟล์ (sheet ว่าง)")
    if len(rows) > MAX_IMPORT_ROWS:
        raise ImportFileError(f"เกินจำนวนแถวสูงสุด ({MAX_IMPORT_ROWS} แถวต่อไฟล์)")

    # Round 8-21A — which columns exist in THIS workbook at all, computed once
    # from the sheet's own header row (never from IMPORT_COLUMNS, which is
    # every column the CURRENT template ships, not what this particular file
    # has). update_current_cycle's oracleSupplierCode/oracleInvoice/refAccount
    # handling needs this to tell "column absent → preserve" from "column
    # present, cell blank → clear" — see _Parsed's *_given flags.
    columns_present = frozenset(headers)

    # Duplicate supplierCode+plotCode within one file → error on every copy
    # (avoids ambiguous "create then update" sequencing in a single import).
    seen: dict[tuple[str, str], int] = {}
    for _row_no, raw in rows:
        p, _ = _parse_row(raw, columns_present)
        k = _dup_key(p)
        if k is not None:
            seen[k] = seen.get(k, 0) + 1
    duplicate_keys = {k for k, n in seen.items() if n > 1}

    states: list[_RowState] = []
    for row_no, raw in rows:
        states.append(
            await _validate_row(db, row_no, raw, ctx, duplicate_keys, columns_present)
        )
    await _apply_cycle_label_history_checks(db, states)
    await _apply_credential_status(db, states)
    await _apply_master_data_crop_variety_checks(db, states)
    return states


async def _apply_credential_status(db: AsyncSession, states: list["_RowState"]) -> None:
    """Round 8-9B.1 — fill every row's credential_configured / credential_
    version / credential_change from ONE bulk query.

    Read-only: this decides what the Preview SHOWS and what the preview-state
    binding records. It never hashes, never builds a digest, and never touches
    the pepper — a Preview works fine on a deployment that has no pepper yet.

    Rows for a plot that doesn't exist yet (create_plot_with_cycle) are
    configured=False by construction — a brand-new plot has no credential — so
    a password on such a row is always a "set". Errored rows are skipped
    entirely: they block the whole file at commit anyway.

    Runs ONLY when the file actually carries at least one password — the same
    "don't query unless a row needs it" rule _apply_cycle_label_history_checks
    follows. An ordinary import (no password column filled in, which is the
    overwhelming majority) issues no extra query at all, and every row's
    credential_configured stays None = "not looked up", never a misleading
    False.
    """
    if not any(s.parsed.new_inspection_password is not None and not s.errors for s in states):
        return

    plot_ids = [
        s.existing_plot_id for s in states
        if s.existing_plot_id is not None and not s.errors
    ]
    # One query for the whole file — never one per row (N+1).
    status_by_plot = await credential_repo.get_credential_status_for_plots(db, plot_ids)

    for s in states:
        if s.errors:
            continue
        if s.existing_plot_id is None:
            s.credential_configured = False   # brand-new plot: nothing to replace
        else:
            configured, version = status_by_plot.get(s.existing_plot_id, (False, None))
            s.credential_configured = configured
            s.credential_version = version
        if s.parsed.new_inspection_password is None:
            continue   # blank cell → keep the existing password, no change
        s.credential_change = (
            CREDENTIAL_CHANGE_REPLACE if s.credential_configured else CREDENTIAL_CHANGE_SET
        )


async def _apply_master_data_crop_variety_checks(db: AsyncSession, states: list["_RowState"]) -> None:
    """Round 8-15D — the batched second pass enforcing that a NEW cycle's
    crop/variety (create_plot_with_cycle, start_new_cycle,
    close_and_start_new_cycle, start_next_cycle, reactivate_plot_with_cycle)
    and any CHANGED crop/variety on update_current_cycle reference an
    existing, ACTIVE Master Data value — and that a variety belongs to the
    chosen crop. An update_current_cycle row whose effective crop/variety is
    IDENTICAL to the plot's current active cycle is exempt even if that
    legacy value has since been deactivated (history is never invalidated
    retroactively); final_plot never reaches this pass at all
    (needs_master_data_check is never set for it).

    Batched exactly like _apply_cycle_label_history_checks: ONE query for
    every crop value + ONE for every variety value across the WHOLE file,
    never one pair of queries per row."""
    relevant = [s for s in states if s.needs_master_data_check]
    if not relevant:
        return
    crop_values = {s.parsed.crop for s in relevant if s.parsed.crop}
    variety_values = {s.parsed.variety for s in relevant if s.parsed.variety}
    # Round 8-26C — pCode joins the same batch (one more query for the whole
    # file, never one per row). An update_current_cycle row with a BLANK
    # pCode cell preserves the cycle's existing value (see _execute_row), so
    # the value actually validated is the effective one, not the raw cell —
    # otherwise a blank cell would read as "clearing to None" and a legacy
    # P.Code would look changed on every untouched row.
    p_code_values = {s.parsed.p_code for s in relevant if s.parsed.p_code}
    lookup = await master_data_validation.load_crop_variety_lookup(
        db, crop_values, variety_values, p_code_values,
    )
    for s in relevant:
        p = s.parsed
        is_update = p.action == ACTION_UPDATE
        effective_p_code = p.p_code or (s.active_cycle_p_code if is_update else None)
        s.errors.extend(
            master_data_validation.crop_variety_errors(
                lookup, p.crop, p.variety,
                current_crop=s.active_cycle_crop if is_update else None,
                current_variety=s.active_cycle_variety if is_update else None,
                p_code=effective_p_code,
                current_p_code=s.active_cycle_p_code if is_update else None,
            )
        )


async def _apply_cycle_label_history_checks(db: AsyncSession, states: list["_RowState"]) -> None:
    """Round 8-6K Part B — the batched second pass for reactivate_plot_with_
    cycle's cycleLabel-reuse check: ONE query for every row flagged by
    _validate_row (needs_cycle_label_history_check), never one per row. Rows
    that already errored on something else earlier (bad supplier/plot/
    action, wrong permission, etc.) never set that flag in the first place,
    so they're never part of the batch. Mutates `states` in place (appends
    to .errors / sets .error_code) — same observable effect as the old
    per-row check, just computed from one shared query result."""
    plot_ids = {s.plot.id for s in states if s.needs_cycle_label_history_check and s.plot is not None}
    if not plot_ids:
        return
    labels_by_plot = await plot_cycle_repo.get_cycle_labels_for_plots(db, list(plot_ids))
    for s in states:
        if not s.needs_cycle_label_history_check or s.plot is None:
            continue
        history_labels = labels_by_plot.get(s.plot.id, set())
        if _label_reused_in_history_labels(history_labels, s.parsed.cycle_label):
            s.errors.append(_reactivate_cycle_label_reused_msg(s.parsed.cycle_label or ""))
            s.error_code = ERROR_CODE_REACTIVATE_CYCLE_LABEL_REUSED


def _build_preview_state(
    states: list[_RowState], content: bytes,
) -> PlotImportPreviewState:
    """The read-only expectation the client echoes back on commit (round
    8-2.7.2): the file's SHA-256 plus, for every VALID start_next_cycle row,
    the resolution it was shown (start ⇄ rollover) and the authoritative
    active-cycle id. Errored start_next rows are omitted — they block the
    whole file at commit re-validation anyway, before this is ever checked."""
    start_next_rows = [
        PlotImportPreviewStateRow(
            row_number=s.row_number,
            supplier_code=s.parsed.supplier_code or "",
            plot_code=s.parsed.plot_code or "",
            resolved_action=s.resolved_action or "",
            active_cycle_id=s.active_cycle_id,
        )
        for s in states
        if s.parsed.action == ACTION_START_NEXT and not s.errors
    ]
    # Round 8-7A — same "valid rows only" rule as start_next_rows above: an
    # errored final_plot row blocks the whole file at commit re-validation
    # anyway, before this binding is ever consulted. plot/active_cycle_id are
    # always set for a valid final_plot row (both required to reach "valid").
    final_plot_rows = [
        PlotImportFinalPlotPreviewStateRow(
            row_number=s.row_number,
            supplier_code=s.parsed.supplier_code or "",
            plot_code=s.parsed.plot_code or "",
            plot_updated_at=s.plot.updated_at,
            active_cycle_id=s.active_cycle_id,
            active_cycle_no=s.active_cycle_no,
            active_cycle_updated_at=s.active_cycle_updated_at,
            cycle_label=s.parsed.cycle_label,
            resolved_final_inspection_record_id=s.final_resolved_record_id,
        )
        for s in states
        if s.parsed.action == ACTION_FINAL and not s.errors
        and s.plot is not None and s.active_cycle_id is not None
        and s.active_cycle_no is not None and s.active_cycle_updated_at is not None
    ]
    # Round 8-9B.1 — one entry per VALID row that will set/replace a password,
    # binding it to the credential state the user was shown. Same "valid rows
    # only" rule as above. Never carries the password, a hash or a digest.
    credential_rows = [
        PlotImportCredentialPreviewStateRow(
            row_number=s.row_number,
            supplier_code=s.parsed.supplier_code or "",
            plot_code=s.parsed.plot_code or "",
            plot_id=s.existing_plot_id,
            expected_configured=bool(s.credential_configured),
            expected_credential_version=s.credential_version,
            intended_change=s.credential_change or "",
        )
        for s in states
        if s.credential_change is not None and not s.errors
    ]
    return PlotImportPreviewState(
        file_sha256=file_digest(content),
        start_next_rows=start_next_rows,
        final_plot_rows=final_plot_rows,
        credential_rows=credential_rows,
    )


def _build_preview(
    states: list[_RowState], *, content: bytes | None = None,
) -> PlotImportPreview:
    results = [_row_result(s) for s in states]
    error_rows = sum(1 for r in results if r.status == "error")
    # preview_state only when we have the raw bytes to digest (the read-only
    # preview endpoints) — never on the error-preview embedded in a commit's
    # ImportHasErrors detail.
    preview_state = _build_preview_state(states, content) if content is not None else None
    return PlotImportPreview(
        total_rows=len(results),
        valid_rows=len(results) - error_rows,
        error_rows=error_rows,
        rows=results,
        preview_state=preview_state,
    )


async def build_preview(
    db: AsyncSession, content: bytes, *, ctx: ImportContext,
) -> PlotImportPreview:
    """Read-only: parse + validate every row, never touch the DB for writes.
    Attaches the preview_state expectation (round 8-2.7.2) for the client to
    echo back on commit."""
    return _build_preview(await _validate_all(db, content, ctx), content=content)


async def preview_states(
    db: AsyncSession, content: bytes, *, ctx: ImportContext,
) -> list["_RowState"]:
    """Read-only row states behind build_preview (round 8-2.4) — same validation
    core, exposed so the preview-report endpoint can render a workbook with raw
    input + structured error codes. Never writes."""
    return await _validate_all(db, content, ctx)


def report_row_view(state: "_RowState") -> dict[str, Any]:
    """Neutral per-row view for the result-workbook builder (round 8-2.4): raw
    input (18 cols) + status + Thai message + structured error code + result
    cycle no + (round 8-2.7.1) resolved_action for a start_next_cycle row.
    Keeps the report builder decoupled from _RowState internals and never
    exposes any UUID/token/internal object. Round 8-5B adds the lot preview
    (lot_mode/proposed_lot_no) and the real committed lot (result_lot_*)."""
    if not state.errors:
        _compute_lot_preview(state)
    return {
        "row_number": state.row_number,
        "action": state.parsed.action,
        "status": "error" if state.errors else "valid",
        "message": "; ".join(state.errors),
        "error_code": state.error_code,
        "result_cycle_no": state.result_cycle_no,
        "resolved_action": state.resolved_action,
        "lot_mode": state.lot_mode,
        "proposed_lot_no": state.proposed_lot_no,
        "result_lot_no": state.result_lot_no,
        "result_lot_no_source": state.result_lot_no_source,
        "result_lot_running_no": state.result_lot_running_no,
        "warning": state.final_warning,
        "finalRecordNote": state.final_record_note,
        "credential_change": state.credential_change,
        "credential_configured": state.credential_configured,
        # Round 8-9B.1 — the result workbook echoes `raw` cell-for-cell so an
        # errored row shows exactly what the user typed. The password column is
        # the ONE cell that must never come back out, so it is stripped HERE,
        # at the source, rather than trusting every future consumer of this
        # view to remember. (plot_import_report blanks it again on the way out
        # — defence in depth, not a substitute for this.)
        "raw": _redacted_raw(state.raw),
    }


def _redacted_raw(raw: dict[str, str]) -> dict[str, str]:
    """A copy of the row's raw cells with newInspectionPassword removed.

    Removed, not masked with a fixed string: an empty cell says nothing at all,
    whereas any placeholder would still confirm "this row carried a password"
    to whoever the workbook gets forwarded to. What the row DID is reported
    separately, in the safe status column."""
    return {k: v for k, v in raw.items() if k != "newInspectionPassword"}


# --- Commit (execution) ---------------------------------------------------

def _started_at(planting_date: datetime.date | None) -> datetime.datetime | None:
    if planting_date is None:
        return None
    return datetime.datetime.combine(
        planting_date, datetime.time.min, tzinfo=datetime.timezone.utc
    )


async def _lock_existing_plots(
    db: AsyncSession, states: list[_RowState],
) -> dict[UUID, Plot]:
    """Row-lock (SELECT ... FOR UPDATE) every EXISTING plot this file's rows
    will mutate (start_new_cycle / update_current_cycle /
    close_and_start_new_cycle / start_next_cycle) — acquired up front, in one
    deterministic order (sorted by plot id), before any row is executed.
    create_plot_with_cycle rows insert a brand-new plot and need no lock here.

    Round 8.0.7: the plot row is the aggregate lock for its own cycle
    transitions (same convention as the single-plot lifecycle endpoints).
    Locking every needed plot up front in one fixed, sorted order — rather
    than each row locking its own plot as execution reaches it — is what
    prevents two concurrent imports whose rows reference the same plots in a
    different order from deadlocking each other.

    Also revalidates plot.is_active under the lock: a plot deactivated
    between preview and commit fails the whole import (all-or-nothing) here,
    rather than surfacing as a confusing failure deeper in _execute_row.

    Round 8-6H — this check is action-aware: reactivate_plot_with_cycle is
    the one action whose precondition is INVERTED (the plot must still be
    inactive at commit time; an already-active plot is the race condition to
    reject), because _dup_key/duplicate_keys already guarantee at most one
    row per (supplierCode, plotCode) in a file, so first_row_for_id[plot_id]
    is always THE row that will act on this plot.
    """
    first_row_for_id: dict[UUID, _RowState] = {}
    for s in states:
        if s.existing_plot_id is not None and s.existing_plot_id not in first_row_for_id:
            first_row_for_id[s.existing_plot_id] = s

    locked: dict[UUID, Plot] = {}
    for plot_id in sorted(first_row_for_id, key=str):
        row = first_row_for_id[plot_id]
        plot = await plot_repo.get_plot_for_update(db, plot_id)
        if plot is None:
            raise ImportFileError(
                f"แปลง {row.parsed.plot_code} ถูกปิดใช้งานหรือหายไประหว่างนำเข้า"
            )
        if row.parsed.action == ACTION_REACTIVATE_WITH_CYCLE:
            if plot.is_active:
                raise ImportFileError(
                    f"แปลง {row.parsed.plot_code} เปิดใช้งานอยู่แล้วระหว่างนำเข้า "
                    "— ไม่ต้องเปิดใช้งานซ้ำ"
                )
        elif not plot.is_active:
            raise ImportFileError(
                f"แปลง {row.parsed.plot_code} ถูกปิดใช้งานหรือหายไประหว่างนำเข้า"
            )
        locked[plot_id] = plot
    return locked


async def _apply_phone_config(db: AsyncSession, plot: Plot, p: "_Parsed") -> None:
    """Apply a row's primaryPhone/additionalPhones columns (round 8-3E), if
    any were given. Both None/[] means the columns were left entirely blank —
    for an EXISTING plot this is "preserve the current config unchanged" (no
    call at all); for a freshly-created plot it just means no config to set.
    Otherwise the two columns are the plot's COMPLETE desired active set —
    replace_plot_access_phones full-replaces (an omitted additional number is
    deactivated, never hard-deleted). Reuses the exact same repository helper
    the PUT /plots/{plotId}/access-phones endpoint and POST /plots/with-cycle
    already call — no parallel write path. Caller must already hold the Plot
    row lock (existing-plot actions: _lock_existing_plots; create: the plot is
    brand-new and owned by this transaction, same as POST /plots/with-cycle)."""
    if p.primary_phone is None and not p.additional_phones:
        return
    # Round 8-3E.1 Part B: the real constructor, never model_construct.
    # Round 8-17C: normalize_and_validate_phone_config() — called explicitly,
    # same as every other endpoint that accepts this schema — is now the
    # FINAL defense-in-depth validation before the repository write (moved
    # out of a model_validator so a rejection never round-trips through
    # FastAPI's PII-echoing auto-422; see app/schemas/plot.py). A row that
    # reaches here already passed _phone_config's own Thai-message checks,
    # so this should never raise in practice; if it somehow does (a rule
    # this file's checks missed), the ValueError propagates out of
    # _execute_row same as any other exception here, and the caller's
    # single get_db transaction rolls the whole file back — never a silent
    # skip, never a fallback to model_construct.
    config = PlotAccessPhoneConfig(
        primary_phone=p.primary_phone, additional_phones=list(p.additional_phones),
    )
    normalize_and_validate_phone_config(config)
    await phone_repo.replace_plot_access_phones(db, plot, config)


async def _execute_row(
    db: AsyncSession, state: _RowState, ctx: ImportContext, locked_plots: dict[UUID, Plot],
) -> str:
    p = state.parsed
    if p.action == ACTION_CREATE:
        assert state.supplier is not None
        plot = await plot_repo.create_plot(db, PlotCreate(
            supplier_id=state.supplier.id,
            plot_code=p.plot_code or "",
            name=p.plot_name or "",
            village=p.village, district=p.district, province=p.province,
            latitude=p.latitude, longitude=p.longitude, rai=p.rai,
            # PlotCreate is physical-only (round 8.0.4) — crop/variety/
            # cycleLabel/lotNo/plantingDate/plantCount/expectedYield* go to
            # create_cycle below instead, which syncs the plot mirror.
        ))
        cycle = await plot_cycle_repo.create_cycle(
            db, plot,
            crop=p.crop, variety=p.variety, cycle_label=p.cycle_label, lot_no=p.lot_no,
            supplier_lot_no=p.supplier_lot_no,
            po_number=p.po_number, p_code=p.p_code,
            oracle_supplier_code=p.oracle_supplier_code, oracle_invoice=p.oracle_invoice,
            ref_account=p.ref_account,
            planting_date=p.planting_date, plant_count=p.plant_count,
            expected_yield_full=p.expected_yield_full,
            expected_yield_unit=p.expected_yield_unit,
            started_at=_started_at(p.planting_date),
        )
        _capture_lot_result(state, cycle)
        await _apply_phone_config(db, plot, p)
        # Round 8-9B.1 — same transaction as the plot + cycle above, so a
        # failure anywhere leaves neither a plot nor an orphan credential.
        await _apply_credential(db, plot, state, ctx)
        state.result_cycle_no = cycle.cycle_no
        return ACTION_CREATE

    # start_new_cycle / update_current_cycle / close_and_start_new_cycle /
    # start_next_cycle all act on the plot _lock_existing_plots already
    # row-locked above (Plot lock first, PlotCycle lock next — never the
    # reverse).
    assert state.existing_plot_id is not None
    plot = locked_plots[state.existing_plot_id]
    # Round 8-9B.1 — the password rides along with whatever action this row
    # performs (it is a field, never an action of its own), under the Plot lock
    # already held. A blank cell makes this a no-op.
    await _apply_credential(db, plot, state, ctx)

    if p.action == ACTION_START:
        # Re-check under the plot lock that no active cycle exists NOW — the
        # partial unique index remains the final backstop, but with every
        # cycle-mutating path locking the plot first this round, that race
        # should no longer be reachable in practice.
        existing_active = await plot_cycle_repo.get_active_cycle_for_plot_for_update(db, plot.id)
        if existing_active is not None:
            raise ImportFileError(
                f"แปลง {p.plot_code} มีรอบปลูกที่เปิดอยู่แล้ว — เกิดขึ้นระหว่างนำเข้า"
            )
        cycle = await plot_cycle_repo.create_cycle(
            db, plot,
            crop=p.crop, variety=p.variety, cycle_label=p.cycle_label, lot_no=p.lot_no,
            supplier_lot_no=p.supplier_lot_no,
            po_number=p.po_number, p_code=p.p_code,
            oracle_supplier_code=p.oracle_supplier_code, oracle_invoice=p.oracle_invoice,
            ref_account=p.ref_account,
            planting_date=p.planting_date, plant_count=p.plant_count,
            expected_yield_full=p.expected_yield_full,
            expected_yield_unit=p.expected_yield_unit,
            started_at=_started_at(p.planting_date),
        )
        # Fresh cycle → clear the previous cycle's inspection snapshot (mirror
        # was already synced by create_cycle). Same as the start-cycle endpoint.
        await plot_cycle_repo.clear_plot_inspection_snapshot(db, plot)
        _capture_lot_result(state, cycle)
        await _apply_phone_config(db, plot, p)
        state.result_cycle_no = cycle.cycle_no
        return ACTION_START

    if p.action == ACTION_ROLLOVER:
        # Close the existing active cycle as harvested (history preserved — its
        # records are never touched), then open a fresh active cycle from this
        # same row. Re-fetch under a row lock so a concurrent transition can't
        # race us into two active cycles; the partial unique index is the final
        # backstop (surfaces as a 409 at the endpoint). Records and the QR key
        # are untouched; the plot stays active. Uses the shared rollover_cycle
        # helper — same close→create→clear-snapshot core as the single-plot
        # rollover endpoint (round 7.9B), so the two can't drift.
        cycle = await plot_cycle_repo.get_active_cycle_for_plot_for_update(db, plot.id)
        if cycle is None:
            # Validated as present; only reachable if it closed between preview
            # and commit — fail the whole transaction rather than silently skip.
            raise ImportFileError("รอบปลูกที่เปิดอยู่หายไประหว่างนำเข้า")
        # Race-safe duplicate re-check under the plot lock (round 8-2.3): the
        # active cycle may have changed between preview and this locked
        # re-fetch. If it now matches the row's plan exactly, refuse — never
        # close/create/clear an identical cycle. The exception propagates so
        # the endpoint's transaction rolls the whole file back.
        if _cycle_plan_matches_import(cycle, p):
            raise ImportFileError(_DUPLICATE_ROLLOVER_MSG)
        _closed, new_cycle = await plot_cycle_repo.rollover_cycle(
            db, plot, cycle,
            close_status=CYCLE_STATUS_HARVESTED,
            closed_by_id=ctx.user_id, close_reason=ROLLOVER_CLOSE_REASON,
            crop=p.crop, variety=p.variety, cycle_label=p.cycle_label, lot_no=p.lot_no,
            supplier_lot_no=p.supplier_lot_no,
            po_number=p.po_number, p_code=p.p_code,
            oracle_supplier_code=p.oracle_supplier_code, oracle_invoice=p.oracle_invoice,
            ref_account=p.ref_account,
            planting_date=p.planting_date, plant_count=p.plant_count,
            expected_yield_full=p.expected_yield_full,
            expected_yield_unit=p.expected_yield_unit,
            started_at=_started_at(p.planting_date),
        )
        _capture_lot_result(state, new_cycle)
        await _apply_phone_config(db, plot, p)
        state.result_cycle_no = new_cycle.cycle_no
        return ACTION_ROLLOVER

    if p.action == ACTION_START_NEXT:
        # Round 8-2.7.1 Part D: recompute the resolution FRESH under the plot
        # lock — never trust state.resolved_action from validation/preview,
        # since the plot's active-cycle state may have changed since then.
        active = await plot_cycle_repo.get_active_cycle_for_plot_for_update(db, plot.id)
        if active is None:
            cycle = await plot_cycle_repo.create_cycle(
                db, plot,
                crop=p.crop, variety=p.variety, cycle_label=p.cycle_label, lot_no=p.lot_no,
                supplier_lot_no=p.supplier_lot_no,
                po_number=p.po_number, p_code=p.p_code,
                oracle_supplier_code=p.oracle_supplier_code, oracle_invoice=p.oracle_invoice,
                ref_account=p.ref_account,
                planting_date=p.planting_date, plant_count=p.plant_count,
                expected_yield_full=p.expected_yield_full,
                expected_yield_unit=p.expected_yield_unit,
                started_at=_started_at(p.planting_date),
            )
            # Same "fresh cycle → clear the old inspection snapshot" behavior
            # as the plain start_new_cycle branch above.
            _capture_lot_result(state, cycle)
            await plot_cycle_repo.clear_plot_inspection_snapshot(db, plot)
            await _apply_phone_config(db, plot, p)
            state.resolved_action = ACTION_START
            state.result_cycle_no = cycle.cycle_no
            return ACTION_START_NEXT

        # An active cycle exists NOW — re-check the same two guards preview
        # did (label collision, full-plan duplicate), under the lock. Either
        # match means the plot's state diverged from what preview saw; abort
        # the whole file with the race-flavored message rather than silently
        # closing a cycle the user didn't knowingly approve closing.
        if _same_active_cycle_label(active, p) or _cycle_plan_matches_import(active, p):
            raise ImportFileError(_RACE_STATE_CHANGED_MSG)
        _closed, new_cycle = await plot_cycle_repo.rollover_cycle(
            db, plot, active,
            close_status=CYCLE_STATUS_HARVESTED,
            closed_by_id=ctx.user_id, close_reason=ROLLOVER_CLOSE_REASON_START_NEXT,
            crop=p.crop, variety=p.variety, cycle_label=p.cycle_label, lot_no=p.lot_no,
            supplier_lot_no=p.supplier_lot_no,
            po_number=p.po_number, p_code=p.p_code,
            oracle_supplier_code=p.oracle_supplier_code, oracle_invoice=p.oracle_invoice,
            ref_account=p.ref_account,
            planting_date=p.planting_date, plant_count=p.plant_count,
            expected_yield_full=p.expected_yield_full,
            expected_yield_unit=p.expected_yield_unit,
            started_at=_started_at(p.planting_date),
        )
        _capture_lot_result(state, new_cycle)
        await _apply_phone_config(db, plot, p)
        state.resolved_action = ACTION_ROLLOVER
        state.result_cycle_no = new_cycle.cycle_no
        return ACTION_START_NEXT

    if p.action == ACTION_REACTIVATE_WITH_CYCLE:
        # Round 8-6H — delegate to the SAME shared helper the API endpoint
        # calls (plot_repository.reactivate_plot_with_cycle): no parallel
        # "flip is_active + create cycle" implementation. `plot` is already
        # locked (Plot-before-PlotCycle order, via _lock_existing_plots
        # above); the helper re-checks is_active/active-cycle under that
        # lock itself and raises the two domain errors below if the plot's
        # state diverged from what preview/the up-front lock check saw —
        # mapped here to the same whole-file-abort ImportFileError every
        # other commit-time race in this module uses.
        try:
            plot, cycle = await plot_repo.reactivate_plot_with_cycle(
                db, plot,
                crop=p.crop, variety=p.variety, cycle_label=p.cycle_label, lot_no=p.lot_no,
                supplier_lot_no=p.supplier_lot_no,
                po_number=p.po_number, p_code=p.p_code,
                oracle_supplier_code=p.oracle_supplier_code, oracle_invoice=p.oracle_invoice,
                ref_account=p.ref_account,
                planting_date=p.planting_date, plant_count=p.plant_count,
                expected_yield_full=p.expected_yield_full,
                expected_yield_unit=p.expected_yield_unit,
                started_at=_started_at(p.planting_date),
            )
        except (plot_repo.PlotAlreadyActiveError, plot_repo.PlotHasActiveCycleError) as exc:
            raise ImportFileError(
                f"แปลง {p.plot_code}: สถานะเปลี่ยนแปลงระหว่างนำเข้า ({exc})"
            ) from exc
        _capture_lot_result(state, cycle)
        await _apply_phone_config(db, plot, p)
        state.result_cycle_no = cycle.cycle_no
        return ACTION_REACTIVATE_WITH_CYCLE

    if p.action == ACTION_FINAL:
        # Round 8-7A.1 — lock order (per this action's own contract): Plot
        # (already locked via locked_plots) -> active PlotCycle FOR UPDATE ->
        # resolve/validate Record -> write actual yield -> close cycle. Every
        # check below is re-fetched FRESH under this lock — never trusts
        # state.active_cycle_id / state.final_resolved_record_id from
        # validation, which may be stale by the time this row actually
        # executes (a concurrent commit, or a duplicate/replay of this same
        # file). _verify_final_plot_snapshot has already compared this exact
        # resolution against the approved preview_state before any row here
        # ran; this re-fetch is the defense-in-depth that guarantees the
        # record actually snapshotted is the one just re-validated, never a
        # second, independently-diverged query. No new cycle is ever created
        # here; Plot.is_active is never touched; Records are never written or
        # deleted; the QR key is untouched (close_cycle never touches any of
        # these).
        cycle = await plot_cycle_repo.get_active_cycle_for_plot_for_update(db, plot.id)
        if cycle is None:
            # The active cycle this row targeted is gone — either it was
            # already finalized/closed by an earlier run of this exact file
            # (replay) or by a genuinely concurrent transaction. Either way,
            # never write again.
            raise ImportFileError("รอบปลูกนี้ถูกปิดแล้ว ไม่สามารถลงผลผลิตสุดท้ายซ้ำได้")
        if not _cycle_label_matches_active(cycle.cycle_label, p.cycle_label):
            raise ImportFileError(
                "ชื่อรอบปลูกในไฟล์ไม่ตรงกับรอบที่เปิดอยู่ กรุณาดาวน์โหลดข้อมูลล่าสุดและตรวจสอบอีกครั้ง"
            )
        resolved_record = await _resolve_final_inspection_record(db, cycle.id)
        # Write the actual-harvest fields, THEN close — one flush, same
        # transaction; close_cycle is handed the SAME resolved_record just
        # validated above (round 8-7A.1) so it snapshots exactly that record
        # (or exactly None) rather than re-querying "latest" a second time
        # and risking a different answer.
        plot_cycle_repo.set_actual_harvest(
            cycle,
            harvest_yield=p.harvest_yield,
            final_yield_after_clean=p.final_yield_after_clean,
            final_yield_unit=p.final_yield_unit,
            harvest_date=p.harvest_date,
            final_note=p.final_note,
        )
        await plot_cycle_repo.close_cycle(
            db, cycle, status=CYCLE_STATUS_HARVESTED,
            closed_by_id=ctx.user_id, reason=FINAL_PLOT_CLOSE_REASON,
            final_estimate_record=resolved_record,
        )
        state.result_cycle_no = cycle.cycle_no
        return ACTION_FINAL

    # ACTION_UPDATE — re-fetch the active cycle under a row lock (race-safe) and
    # replace its plan with the row's values, then re-sync the plot mirror. The
    # inspection snapshot is deliberately NOT cleared (a plan edit mustn't erase
    # the plot's latest inspection status).
    cycle = await plot_cycle_repo.get_active_cycle_for_plot_for_update(db, plot.id)
    if cycle is None:
        # Validated as present; only reachable if it closed between preview and
        # commit — fail the whole transaction rather than silently skip.
        raise ImportFileError("รอบปลูกที่เปิดอยู่หายไประหว่างนำเข้า")
    # Round 8-5B — update_current_cycle preserve semantics (exclude_unset):
    #   - crop/variety/cycleLabel/plantingDate/plantCount/expectedYield* are
    #     replaced as before (Excel edits the whole plan).
    #   - poNumber/pCode: sent only when nonblank (blank = preserve existing).
    #   - supplierLotNo (round 8-12A): sent only when nonblank, same preserve
    #     rule — so an older workbook with no such column, or a row that leaves
    #     the cell empty, never wipes a supplier lot number that is already
    #     stored. Clearing one is done through the API, not a blank Excel cell.
    #   - oracleSupplierCode/oracleInvoice/refAccount (round 8-21A) —
    #     DELIBERATELY DIFFERENT from poNumber/pCode/supplierLotNo above: sent
    #     (even as None, i.e. included in `fields`) whenever the COLUMN itself
    #     exists in this workbook (`p.*_given`), regardless of whether the
    #     cell is blank — so a present-but-blank cell CLEARS the stored value
    #     (update_cycle's `"key" in fields` presence check applies it). Only a
    #     column entirely ABSENT from the workbook (an older download with no
    #     such header) is omitted from `fields`, which preserves the existing
    #     value. See _Parsed's *_given flags / _validate_all's columns_present.
    #   - lotNo: nonblank → Manual; blank + no existing lot + usable Auto
    #     components → Auto; blank + an existing lot → OMIT (preserve existing
    #     lot, so a plain edit never wipes it).
    fields: dict[str, Any] = {
        "crop": p.crop, "variety": p.variety, "cycle_label": p.cycle_label,
        "planting_date": p.planting_date, "plant_count": p.plant_count,
        "expected_yield_full": p.expected_yield_full,
        "expected_yield_unit": p.expected_yield_unit,
    }
    if p.po_number:
        fields["po_number"] = p.po_number
    if p.p_code:
        fields["p_code"] = p.p_code
    if p.supplier_lot_no:
        fields["supplier_lot_no"] = p.supplier_lot_no
    if p.oracle_supplier_code_given:
        fields["oracle_supplier_code"] = p.oracle_supplier_code
    if p.oracle_invoice_given:
        fields["oracle_invoice"] = p.oracle_invoice
    if p.ref_account_given:
        fields["ref_account"] = p.ref_account
    if p.lot_no:
        fields["lot_no"] = p.lot_no
    elif cycle.lot_no is None and (p.cycle_label or cycle.cycle_label) and (p.p_code or cycle.p_code):
        # Round 8-12A — blank lot + no existing lot + the V2 components are
        # available (row value, else the cycle's own) → ask for an Auto Lot.
        fields["lot_no"] = None
    # else: blank lot + an existing lot (or no PO) → omit → preserve.
    await plot_cycle_repo.update_cycle(db, plot, cycle, fields)
    await plot_cycle_repo.sync_plot_mirror_from_cycle(db, plot, cycle)
    _capture_lot_result(state, cycle)
    await _apply_phone_config(db, plot, p)
    state.result_cycle_no = cycle.cycle_no
    return ACTION_UPDATE


def _counts_from_states(states: list[_RowState]) -> dict[str, int]:
    """Bucket every executed row into the action counters. A start_next_cycle
    row (round 8-2.7.1) is bucketed by state.resolved_action — which
    _execute_row overwrites with the ACTUAL outcome — never by its own
    literal action string, so PlotImportCommitResult's existing started_
    cycles/rolled_over_cycles counters stay accurate without adding a new
    field (matches the frontend's equivalent resolved-action grouping).
    reactivate_plot_with_cycle (round 8-6H) is unambiguous — always bucketed
    by its own literal action string, no resolution step needed."""
    counts = {
        ACTION_CREATE: 0, ACTION_START: 0, ACTION_UPDATE: 0, ACTION_ROLLOVER: 0,
        ACTION_REACTIVATE_WITH_CYCLE: 0, ACTION_FINAL: 0,
    }
    for s in states:
        action = s.parsed.action
        if action == ACTION_START_NEXT:
            action = s.resolved_action
        if action in counts:
            counts[action] += 1
    return counts


# --- Preview-state binding for start_next_cycle (round 8-2.7.2) -----------

def _check_preview_state_file(
    content: bytes, preview_state: PlotImportPreviewState | None,
) -> PlotImportPreviewState:
    """File-level gate, run BEFORE any lock (Part D step 2): a file with
    start_next_cycle and/or final_plot rows (round 8-7A.1) MUST carry a
    preview_state whose digest matches the exact bytes being committed.
    Returns the validated preview_state so the caller can pass it on to the
    under-lock resolution check(s)."""
    if preview_state is None:
        raise ImportPreviewStateConflict(_PS_MISSING, _MSG_MISSING_PREVIEW_STATE)
    if preview_state.file_sha256 != file_digest(content):
        raise ImportPreviewStateConflict(_PS_DIGEST_MISMATCH, _MSG_FILE_DIGEST_MISMATCH)
    return preview_state


async def _verify_start_next_snapshot(
    db: AsyncSession,
    start_next_states: list[_RowState],
    preview_state: PlotImportPreviewState,
    locked_plots: dict[UUID, Plot],
) -> None:
    """Under-lock resolution check (Part D step 4): recompute each start_next_
    cycle row's ACTUAL resolution from the plot's live active cycle — held
    under the plot row lock — and compare it to the expectation the user
    approved in Preview. Any divergence aborts the whole file BEFORE a single
    row executes (the caller runs this before the execute loop).

    Divergence includes: the set of start_next rows not matching the snapshot
    (row added/removed/identity-swapped, or a snapshot row missing/extra); a
    row whose (resolved_action, active_cycle_id) pair changed — i.e. an active
    cycle appeared where Preview saw none, vanished where Preview saw one, or
    the active cycle is now a DIFFERENT cycle (compared by authoritative
    active_cycle_id, never the editable cycle_label).

    Never mutates. The active-cycle SELECT ... FOR UPDATE re-uses the plot lock
    already held; a second SELECT here + in _execute_row can't diverge within
    the one transaction."""
    expected_by_row = {r.row_number: r for r in preview_state.start_next_rows}
    actual_row_numbers = {s.row_number for s in start_next_states}

    # Set/identity: the start_next rows in the (re-validated) file must be
    # exactly those in the snapshot — a missing/extra snapshot row, or a row
    # added/removed since Preview, is a conflict.
    if actual_row_numbers != set(expected_by_row):
        changed = sorted(actual_row_numbers.symmetric_difference(expected_by_row))
        raise ImportPreviewStateConflict(_PS_ROW_SET_MISMATCH, _MSG_STATE_CHANGED, changed)

    changed_rows: list[int] = []
    for s in start_next_states:
        expected = expected_by_row[s.row_number]
        # Row identity must match too (supplier+plot) — defense-in-depth on
        # top of the file digest.
        if (expected.supplier_code, expected.plot_code) != (
            s.parsed.supplier_code, s.parsed.plot_code
        ):
            changed_rows.append(s.row_number)
            continue
        assert s.existing_plot_id is not None  # valid start_next → plot exists
        plot = locked_plots[s.existing_plot_id]
        active = await plot_cycle_repo.get_active_cycle_for_plot_for_update(db, plot.id)
        actual_resolved = ACTION_START if active is None else ACTION_ROLLOVER
        actual_active_id = None if active is None else active.id
        if (
            expected.resolved_action != actual_resolved
            or expected.active_cycle_id != actual_active_id
        ):
            changed_rows.append(s.row_number)

    if changed_rows:
        raise ImportPreviewStateConflict(
            _PS_RESOLUTION_CHANGED, _MSG_STATE_CHANGED, sorted(changed_rows)
        )


# --- Preview-state binding for final_plot (round 8-7A.1) -------------------

async def _verify_final_plot_snapshot(
    db: AsyncSession,
    final_states: list[_RowState],
    preview_state: PlotImportPreviewState,
    locked_plots: dict[UUID, Plot],
) -> None:
    """Under-lock resolution check for final_plot, mirroring
    _verify_start_next_snapshot's role: recompute each row's ACTUAL Plot/
    PlotCycle/Record state — held under the Plot row lock, then the Cycle row
    lock (never the reverse) — and compare it to the expectation the user
    approved in Preview. ANY divergence aborts the whole file BEFORE a single
    row executes (the caller runs this before the execute loop) — nothing is
    written.

    Divergence includes: the set of final_plot rows not matching the snapshot
    (row added/removed/identity-swapped, or a snapshot row missing/extra);
    supplierCode/plotCode identity mismatch; Plot.updated_at drift (something
    about the plot changed since Preview); the active cycle vanishing, being a
    DIFFERENT cycle (by id), or its cycle_no/updated_at/cycle_label drifting;
    or the resolved finalInspectionRecordId (see _resolve_final_inspection_
    record) coming out different now than it did at Preview — e.g. a NEW
    inspection record was added to this cycle after Preview, which would
    otherwise silently change what final_plot snapshots without the user ever
    seeing it.

    Never mutates. The PlotCycle SELECT ... FOR UPDATE here re-locks the same
    row _execute_row will re-fetch (same transaction — can't diverge)."""
    expected_by_row = {r.row_number: r for r in preview_state.final_plot_rows}
    actual_row_numbers = {s.row_number for s in final_states}

    if actual_row_numbers != set(expected_by_row):
        changed = sorted(actual_row_numbers.symmetric_difference(expected_by_row))
        raise ImportPreviewStateConflict(_PS_ROW_SET_MISMATCH, _MSG_STATE_CHANGED, changed)

    changed_rows: list[int] = []
    # Round 8-10B — record drift is reported with its own message (see below).
    record_changed_rows: list[int] = []
    for s in final_states:
        expected = expected_by_row[s.row_number]
        p = s.parsed
        if (expected.supplier_code, expected.plot_code) != (p.supplier_code, p.plot_code):
            changed_rows.append(s.row_number)
            continue
        assert s.existing_plot_id is not None  # valid final_plot → plot exists
        plot = locked_plots[s.existing_plot_id]  # Plot already locked (lock order)
        if plot.updated_at != expected.plot_updated_at:
            changed_rows.append(s.row_number)
            continue
        cycle = await plot_cycle_repo.get_active_cycle_for_plot_for_update(db, plot.id)
        if (
            cycle is None
            or cycle.id != expected.active_cycle_id
            or cycle.cycle_no != expected.active_cycle_no
            or cycle.updated_at != expected.active_cycle_updated_at
            or not _cycle_label_matches_active(cycle.cycle_label, expected.cycle_label)
        ):
            changed_rows.append(s.row_number)
            continue
        resolved_record = await _resolve_final_inspection_record(db, cycle.id)
        resolved_id = resolved_record.id if resolved_record is not None else None
        if resolved_id != expected.resolved_final_inspection_record_id:
            # Round 8-10B — tracked separately from the plot/cycle drift above
            # so the user is told WHICH thing moved. Since they no longer name
            # the record themselves, "สถานะรอบปลูกมีการเปลี่ยนแปลง" would leave
            # them hunting through a file that is, in fact, still correct: what
            # changed is that someone submitted an inspection in the meantime.
            # None -> a record, or a record -> a newer one, both land here.
            record_changed_rows.append(s.row_number)

    if changed_rows:
        raise ImportPreviewStateConflict(
            _PS_RESOLUTION_CHANGED, _MSG_STATE_CHANGED, sorted(changed_rows)
        )
    if record_changed_rows:
        raise ImportPreviewStateConflict(
            _PS_RESOLUTION_CHANGED, _MSG_FINAL_RECORD_CHANGED, sorted(record_changed_rows)
        )


# --- Plot inspection password: hashing + snapshot check (round 8-9B.1) -----

# The one message a caller sees when the server has no blind-index pepper
# deployed. Names no setting, quotes no value, carries no stack trace.
_MSG_PEPPER_UNAVAILABLE = (
    "ระบบยังไม่พร้อมตั้งรหัสยืนยันแปลง กรุณาติดต่อผู้ดูแลระบบ "
    "— ไม่ได้บันทึกข้อมูลใด ๆ"
)


async def _hash_credential_rows(states: list[_RowState]) -> None:
    """Hash + digest every row that carries a password, BEFORE any lock is
    taken (round 8-9B.1).

    Why here and not in _execute_row: bcrypt cost 12 is ~250ms of blocking CPU
    per row. Doing it inside the execute loop would hold every plot lock this
    file acquired for the whole time, and doing it inline would stall the event
    loop for the entire import. Running it before _lock_existing_plots means no
    lock is held while hashing, and the mutation phase stays fast.

    SEQUENTIAL, deliberately — not asyncio.gather. An unbounded gather over a
    1000-row file would launch 1000 threads and 1000 concurrent bcrypt rounds,
    turning one admin upload into a CPU denial-of-service against the whole
    process. One at a time keeps the cost proportional and predictable; each
    call still yields the event loop via to_thread, so the server stays
    responsive.

    Each row gets its OWN bcrypt salt (gensalt per hash inside the helper), so
    two plots given the SAME password get DIFFERENT hashes — an attacker with
    the table can't tell they match. Their HMAC digests DO match, by design:
    that is what lets round 8-9C find both plots from one entered password.

    Pepper missing → ImportFileError before anything is locked or written.
    """
    for state in states:
        pin = state.parsed.new_inspection_password
        if pin is None:
            continue
        try:
            # Digest first: the cheap call that fails when no pepper is
            # deployed, so a misconfigured server never burns a bcrypt round.
            state.credential_digest = build_plot_access_password_lookup_digest(pin)
        except PlotAccessPepperMissingError as exc:
            raise ImportFileError(_MSG_PEPPER_UNAVAILABLE) from exc
        state.credential_hash = await asyncio.to_thread(hash_plot_access_password, pin)


async def _verify_credential_snapshot(
    db: AsyncSession,
    credential_states: list[_RowState],
    preview_state: PlotImportPreviewState,
    locked_plots: dict[UUID, Plot],
) -> None:
    """Under-lock credential check, mirroring _verify_start_next_snapshot: for
    every row that will set/replace a password, re-read the plot's LIVE
    credential under the Plot row lock and compare it to what Preview showed.

    Divergence includes: the set of credential rows not matching the snapshot
    (a row added/removed since Preview, or a snapshot row missing/extra);
    supplierCode/plotCode identity mismatch; and — the real point — the
    credential having appeared, disappeared, or been bumped to a different
    version by someone else in between. Any of those means the user approved
    "set" when it is now a "replace" (or vice versa), so the WHOLE file is
    rejected before a single row executes.

    Never mutates, never hashes, never touches the pepper. A create_plot_with_
    cycle row has no plot yet (plot_id None) — its expectation is simply "no
    credential", which nothing else can contradict, so there is nothing to
    re-read for it.
    """
    expected_by_row = {r.row_number: r for r in preview_state.credential_rows}
    actual_row_numbers = {s.row_number for s in credential_states}

    if actual_row_numbers != set(expected_by_row):
        changed = sorted(actual_row_numbers.symmetric_difference(expected_by_row))
        raise ImportPreviewStateConflict(_PS_ROW_SET_MISMATCH, _MSG_STATE_CHANGED, changed)

    # One bulk re-read under the locks — never one query per row.
    live = await credential_repo.get_credential_status_for_plots(
        db, [s.existing_plot_id for s in credential_states if s.existing_plot_id is not None]
    )

    changed_rows: list[int] = []
    for s in credential_states:
        expected = expected_by_row[s.row_number]
        if (expected.supplier_code, expected.plot_code) != (
            s.parsed.supplier_code, s.parsed.plot_code
        ):
            changed_rows.append(s.row_number)
            continue
        if expected.intended_change != s.credential_change:
            changed_rows.append(s.row_number)
            continue
        if s.existing_plot_id is None:
            # Brand-new plot: expectation must be "no credential yet".
            if expected.expected_configured or expected.expected_credential_version is not None:
                changed_rows.append(s.row_number)
            continue
        # The plot is locked (lock order: Plot first), so this read is stable
        # for the rest of the transaction.
        assert s.existing_plot_id in locked_plots
        actual_configured, actual_version = live.get(s.existing_plot_id, (False, None))
        if (
            expected.expected_configured != actual_configured
            or expected.expected_credential_version != actual_version
        ):
            changed_rows.append(s.row_number)

    if changed_rows:
        raise ImportPreviewStateConflict(
            _PS_RESOLUTION_CHANGED, _MSG_STATE_CHANGED, sorted(changed_rows)
        )


async def _apply_credential(db: AsyncSession, plot: Plot, state: _RowState, ctx: ImportContext) -> None:
    """Write this row's inspection password, if it has one (round 8-9B.1).

    Blank cell → returns immediately: no hash, no digest, no repository call,
    no version bump, no activity log, and the plot's existing credential is
    left completely untouched.

    Reuses the SAME repository helper the admin PUT calls — no parallel write
    path — and runs inside the import's single transaction, so a later row
    failing rolls this back with everything else. The caller already holds the
    Plot row lock (Plot → PlotAccessCredential order, unchanged).

    The plaintext never reaches this function: only the hash and digest
    computed in _hash_credential_rows do.
    """
    if state.credential_hash is None or state.credential_digest is None:
        return
    row = await credential_repo.set_or_replace_plot_credential(
        db, plot,
        password_hash=state.credential_hash,
        password_lookup_digest=state.credential_digest,
        updated_by_id=ctx.user_id,
    )
    # Security event per plot, inside the same transaction — a rolled-back
    # import leaves no "success" log behind. Metadata is the new version and
    # what the row did; never the password, hash, digest, or any phone number.
    await ActivityLogger(db).log(
        action="plot.inspection_access_credential_set",
        actor_id=ctx.user_id,
        action_type="update",
        resource_type="plot",
        resource_id=str(plot.id),
        is_security_event=True,
        risk_level="high",
        metadata={
            "credential_version": row.credential_version,
            "change": state.credential_change,
            "source": "excel_import",
        },
    )


async def commit_import_execute(
    db: AsyncSession, content: bytes, *, ctx: ImportContext,
    preview_state: PlotImportPreviewState | None = None,
) -> list[_RowState]:
    """Shared commit core (round 8-2.4): re-validate, lock, execute every row in
    the caller's single transaction, and return the executed row states (each
    now carrying result_cycle_no). Any invalid row → ImportHasErrors (carrying
    both the preview and the states, nothing written); a file/lock problem →
    ImportFileError; an unexpected DB error propagates so get_db rolls back.

    Round 8-2.7.2 ordering (Part D; extended round 8-7A.1 Part B/C to also
    cover final_plot): (1) re-validate file, (2) check the preview-state file
    digest, (3) lock every existing plot, (4) verify EVERY start_next_cycle
    AND final_plot row's resolution against the approved snapshot under those
    locks, (5) only then execute any row. A start_next_cycle or final_plot
    file with no preview_state, a stale digest, or a diverged resolution
    raises ImportPreviewStateConflict before step 5 — nothing is written.
    Files with only the four legacy actions ignore preview_state entirely
    (backward compatible).

    Both commit_import (JSON) and the commit-report endpoint go through here, so
    a commit is executed exactly once per request and the two paths can't drift.
    """
    states = await _validate_all(db, content, ctx)
    if any(s.errors for s in states):
        raise ImportHasErrors(_build_preview(states), states)

    # (2) File-digest gate for start_next_cycle/final_plot/password files —
    # before any lock, and before any bcrypt.
    start_next_states = [s for s in states if s.parsed.action == ACTION_START_NEXT]
    final_states = [s for s in states if s.parsed.action == ACTION_FINAL]
    credential_states = [s for s in states if s.credential_change is not None]
    checked_preview_state = (
        _check_preview_state_file(content, preview_state)
        if start_next_states or final_states or credential_states
        else None
    )

    # (2b) Round 8-9B.1 — hash every password row NOW: after validation and the
    # digest gate (so a bad file never costs a bcrypt round), but BEFORE any
    # lock is taken (so no plot is held for the ~250ms/row it costs). Also the
    # point where a missing pepper aborts, before anything is locked or
    # written. Sequential, never gather — see the helper's docstring.
    await _hash_credential_rows(states)

    # (3) Lock every existing plot in one deterministic order.
    locked_plots = await _lock_existing_plots(db, states)

    # (4) Verify all start_next/final_plot/credential expectations under the
    # locks, before ANY execute.
    if start_next_states:
        assert checked_preview_state is not None
        await _verify_start_next_snapshot(
            db, start_next_states, checked_preview_state, locked_plots
        )
    if final_states:
        assert checked_preview_state is not None
        await _verify_final_plot_snapshot(
            db, final_states, checked_preview_state, locked_plots
        )
    if credential_states:
        assert checked_preview_state is not None
        await _verify_credential_snapshot(
            db, credential_states, checked_preview_state, locked_plots
        )

    # (5) Execute every row.
    for state in states:
        try:
            await _execute_row(db, state, ctx, locked_plots)
        except LotNumberTooLongError as exc:
            # Validation pre-checks Auto Lot length at running=1000 (round
            # 8-12A); a real running >1000 could in theory still overflow at
            # commit. Fail the whole file cleanly (all-or-nothing) with the row.
            raise ImportFileError(
                f"แถวที่ {state.row_number}: Auto Lot ที่จะสร้างยาวเกิน 100 ตัวอักษร "
                f"({exc}) กรุณาย่อ cycleLabel/pCode หรือกรอก lotNo เอง"
            ) from exc
        except AutoLotMissingComponentError as exc:
            # Round 8-12A.1 — validation already rejects this per row, so
            # reaching here means the plot's supplier could not be resolved
            # under the commit lock (or state drifted). Fail the WHOLE file
            # cleanly rather than writing a cycle with no lot; the caller's
            # transaction rolls every earlier row back. Names the missing
            # field only, never a submitted value.
            raise ImportFileError(
                f"แถวที่ {state.row_number}: ต้องระบุ {', '.join(exc.missing)} "
                "ก่อนสร้าง Auto Lot หรือกรอก lotNo เอง"
            ) from exc
    return states


async def commit_import(
    db: AsyncSession, content: bytes, *, ctx: ImportContext,
    preview_state: PlotImportPreviewState | None = None,
) -> PlotImportCommitResult:
    """All-or-nothing JSON commit: re-validate server-side, then execute every
    row in the caller's single transaction. Any invalid row → ImportHasErrors
    (nothing written). Helpers only flush; the get_db dependency commits on
    success or rolls the whole file back on any exception.

    Round 8.0.7: every existing plot the file will mutate is row-locked
    up front (_lock_existing_plots), in sorted order, before any row executes
    — see that helper's docstring for why the ordering matters. Round 8-2.7.2:
    start_next_cycle rows are additionally bound to the approved preview_state
    (see commit_import_execute).
    """
    states = await commit_import_execute(db, content, ctx=ctx, preview_state=preview_state)
    counts = _counts_from_states(states)
    return PlotImportCommitResult(
        created_plots=counts[ACTION_CREATE],
        started_cycles=counts[ACTION_START],
        updated_cycles=counts[ACTION_UPDATE],
        rolled_over_cycles=counts[ACTION_ROLLOVER],
        reactivated_plots=counts[ACTION_REACTIVATE_WITH_CYCLE],
        finalized_plots=counts[ACTION_FINAL],
        skipped_rows=0,
        row_results=[_row_result(s) for s in states],
    )
