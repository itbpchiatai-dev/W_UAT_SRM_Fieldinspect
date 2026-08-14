/**
 * Plot + cycle Excel import modal (round 7.5; result-workbook flow round
 * 8-2.5).
 *
 * Flow: download template → pick .xlsx → Preview (read-only JSON, for the
 * on-screen table) → optionally download a Validation report (.xlsx,
 * read-only) → "ยืนยันนำเข้า" commits through commit-report (ONE mutation,
 * ONE request — never the legacy JSON /import/commit) → on success the
 * Completed workbook downloads automatically and the modal locks Commit
 * permanently for this instance; on a 422 "blocked" response nothing was
 * written and the table silently re-previews (read-only) so the user sees
 * what still needs fixing.
 *
 * commit-report is the ONLY endpoint that ever mutates data in this modal —
 * the JSON commitPlotImport() helper is kept in api/plots.ts purely for
 * backward compatibility and must never be called from here.
 */
import { useState } from 'react';
import axios from 'axios';
import { useMutation } from '@tanstack/react-query';
import {
  AlertTriangle, CheckCircle2, Download, FileSpreadsheet, Loader2, RefreshCw, Unlock, Upload,
} from 'lucide-react';
import {
  commitPlotImportWithReport,
  downloadPlotImportPreviewReport,
  downloadPlotImportTemplate,
  previewPlotImport,
  PlotImportReportError,
  PREVIEW_STATE_CONFLICT_CODE,
  type PlotImportFinalPlotPreviewStateRow,
  type PlotImportPreview,
  type PlotImportPreviewState,
  type PlotImportReportFile,
  type PlotImportRowResult,
} from '../../api/plots';
import { downloadBlob } from '../../lib/downloadBlob';
import { formatThaiMobile } from '../../lib/phone';
import { formatYieldQuantity } from '../../lib/yield-planning';

// Round 8-10C — final_plot's actual-harvest unit is a server-side constant
// (round 8-10B: FINAL_PLOT_FIXED_YIELD_UNIT = "kg"); the Excel contract no
// longer has a column for it and the payload no longer carries a field for
// it either. Displayed with this fixed constant, never read off the row.
const FINAL_PLOT_YIELD_UNIT = 'kg';

// Static fallback labels, keyed by the raw technical action (round 8-2.7.1:
// all 5 supported actions must resolve to a Thai label — never a raw
// technical value). start_next_cycle's OWN entry is only ever shown when the
// server hasn't resolved it yet (an error row, e.g. same_active_cycle_label,
// blocked before resolution) — the normal case uses rowActionLabel() below,
// which prefers the server's resolvedAction over this generic fallback.
const ACTION_LABEL: Record<string, string> = {
  create_plot_with_cycle: 'สร้างแปลง + รอบแรก',
  start_new_cycle: 'เริ่มรอบในแปลงที่ว่าง',
  update_current_cycle: 'แก้รอบปัจจุบัน',
  close_and_start_new_cycle: 'จบรอบเดิม + เริ่มรอบใหม่',
  start_next_cycle: 'เริ่มรอบถัดไป',
  // Round 8-6J — inactive-plot-only action (Excel template's status-aware
  // Sheet 1 now seeds these rows directly; see Plots.tsx's "Excel ตามตัวกรอง").
  reactivate_plot_with_cycle: 'เปิดใช้งานแปลง + เริ่มรอบปลูกใหม่',
  // Round 8-7A/8-7B — closes the plot's active cycle as harvested and
  // records the actual harvest figures; Plot.isActive stays true.
  final_plot: 'ลงผลผลิตสุดท้ายและปิดรอบปลูก',
};

const DUPLICATE_ERROR_CODE = 'duplicate_rollover';
const SAME_ACTIVE_CYCLE_LABEL_ERROR_CODE = 'same_active_cycle_label';

// Round 8-6E — preview-state conflict `reason` sub-codes (backend
// `plot_import.py`'s `_PS_*` constants, mirrored here as the frontend's own
// copy since the value is data on the wire, not an import). Two reasons get
// a fixed Thai message per this round's spec; row_set_mismatch/
// resolution_changed (and anything unrecognized) fall back to the backend's
// own `message` — never a frontend guess.
const REASON_MISSING_PREVIEW_STATE = 'missing_preview_state';
const REASON_FILE_DIGEST_MISMATCH = 'file_digest_mismatch';

/** Maps a preview-state conflict's `reason` to the Thai text to show — the
 * two reasons with a fixed mapping, or the backend's own `message` as the
 * generic fallback (also covers row_set_mismatch/resolution_changed, which
 * are specified to just use the backend message). */
function stateConflictMessage(error: PlotImportReportError): string {
  if (error.reason === REASON_MISSING_PREVIEW_STATE) {
    // Round 8-6F Part D — supersedes round 8-6E's wording for this one
    // reason: "หมดอายุ" (expired) more accurately describes what happened
    // now that the round 8-6F multipart contract fix means this reason
    // should mean the approved preview snapshot is genuinely stale/gone,
    // not that the request was silently malformed.
    return 'ข้อมูลตรวจสอบหมดอายุ กรุณากดตรวจสอบไฟล์อีกครั้งก่อนนำเข้า';
  }
  if (error.reason === REASON_FILE_DIGEST_MISMATCH) {
    return 'ไฟล์มีการเปลี่ยนแปลงหลังการตรวจสอบ กรุณาตรวจสอบไฟล์ใหม่';
  }
  return error.message;
}

/**
 * The label to show in the preview table's "การกระทำ" column. For a plain
 * legacy action this is just ACTION_LABEL. For start_next_cycle (round
 * 8-2.7.1) it prefers the server's resolvedAction — read-only data the
 * backend computed, never inferred here — so the row shows exactly what will
 * actually happen: which existing cycle (by label or number) a resolved
 * rollover would close, or a plain "starts fresh" label when the plot has no
 * active cycle. Falls back to the generic start_next_cycle label only when
 * resolvedAction is absent (the row errored before the server could resolve it).
 */
function rowActionLabel(row: PlotImportRowResult): string {
  if (row.action === 'start_next_cycle') {
    if (row.resolvedAction === 'start_new_cycle') return 'เริ่มรอบใหม่ในแปลงว่าง';
    if (row.resolvedAction === 'close_and_start_new_cycle') {
      const cycleRef = row.currentCycleLabel || (row.currentCycleNo != null ? `รอบที่ ${row.currentCycleNo}` : 'เดิม');
      return `ปิดรอบ ${cycleRef} + เริ่มรอบใหม่`;
    }
  }
  return row.action ? (ACTION_LABEL[row.action] ?? row.action) : '—';
}

/** True when a row will (or did) close an existing cycle — an explicit
 * close_and_start_new_cycle row, or a start_next_cycle row the server
 * resolved to one — so the table can give it the same amber warning tone
 * either way. */
function isRolloverRow(row: PlotImportRowResult): boolean {
  return row.action === 'close_and_start_new_cycle'
    || (row.action === 'start_next_cycle' && row.resolvedAction === 'close_and_start_new_cycle');
}

/** True for a reactivate_plot_with_cycle row (round 8-6J) — the only action
 * that flips a plot from inactive back to active, so it gets its own
 * distinct warning tone in the preview table (never conflated with a
 * rollover, which never touches is_active at all). */
function isReactivateRow(row: PlotImportRowResult): boolean {
  return row.action === 'reactivate_plot_with_cycle';
}

/** True for a final_plot row (round 8-7A/8-7B) — closes the active cycle as
 * harvested and records the real harvest figures; gets its own distinct
 * (non-error) tone, same "informational, not blocking" family as rollover/
 * reactivate above. */
function isFinalPlotRow(row: PlotImportRowResult): boolean {
  return row.action === 'final_plot';
}

/**
 * "สถานะแปลงปัจจุบัน" for the preview table (round 8-6J Part I) — derived
 * purely from the row's OWN action, never a separate field: the backend's
 * validation rules already guarantee reactivate_plot_with_cycle only ever
 * targets an inactive plot, and start_next_cycle only ever targets an
 * active one (see plot_import.py's _validate_row) — so the action itself is
 * the plot's current-status signal, not a frontend guess. null for
 * create/legacy actions, which don't have a meaningful "current status" to
 * show (create_plot_with_cycle has no plot yet; the legacy actions apply to
 * either status depending on context already spelled out by their label).
 */
function rowCurrentPlotStatusLabel(row: PlotImportRowResult): string | null {
  if (row.action === 'reactivate_plot_with_cycle') return 'ปิดใช้งาน';
  if (row.action === 'start_next_cycle') return 'ใช้งานอยู่';
  return null;
}

function importErrorMessage(error: unknown): { message: string; preview?: PlotImportPreview } {
  if (axios.isAxiosError(error)) {
    const status = error.response?.status;
    const data = error.response?.data as { detail?: unknown } | string | undefined;
    let text = '';
    let preview: PlotImportPreview | undefined;
    if (typeof data === 'string') {
      text = data;
    } else if (data && typeof data === 'object' && 'detail' in data) {
      const d = (data as { detail: unknown }).detail;
      if (typeof d === 'string') {
        text = d;
      } else if (Array.isArray(d)) {
        text = d.map((i) => (i && typeof i === 'object' && 'msg' in i ? String((i as { msg: unknown }).msg) : String(i))).join(', ');
      } else if (d && typeof d === 'object') {
        const obj = d as { message?: unknown; preview?: unknown };
        text = obj.message ? String(obj.message) : '';
        if (obj.preview) preview = obj.preview as PlotImportPreview;
      }
    }
    return { message: `${status ? `HTTP ${status}` : 'Network error'}${text ? ` — ${text}` : ''}`, preview };
  }
  return { message: error instanceof Error ? error.message : 'Network error' };
}

/**
 * Compact "เบอร์" cell (round 8-3E Part E: "แสดงแบบกระชับ") — the preview
 * table stays a single scrollable-on-mobile column rather than two wide
 * primary/additional columns. Blank phone columns on a row (both null/[])
 * show "—" here regardless of preserve-vs-none semantics — the help text
 * above the table is what explains that distinction.
 */
/** Round 8-5B — short Thai label for a preview row's lotMode. */
function lotModeLabel(mode: string | null | undefined): string | null {
  if (mode === 'auto') return 'อัตโนมัติ';
  if (mode === 'manual') return 'กรอกเอง';
  if (mode === 'preserve') return 'คงเดิม';
  return null;
}

function phoneSummary(row: PlotImportRowResult): string {
  const primary = row.payload?.primaryPhone;
  const additionalCount = row.payload?.additionalPhones?.length ?? 0;
  if (!primary) return '—';
  const primaryText = formatThaiMobile(primary);
  return additionalCount > 0 ? `${primaryText} (+${additionalCount} เบอร์เสริม)` : primaryText;
}

/** Round 8-9B.1 — what this row does to the plot's inspection password.
 *
 * Reads ONLY the server's structured intent. The password itself is never sent
 * to the browser (no response field carries it), so there is nothing here that
 * could render it — not the value, not its length, not its last digits. */
function credentialChangeLabel(row: PlotImportRowResult): string {
  if (row.inspectionPasswordChange === 'set') return 'ตั้งรหัสใหม่';
  if (row.inspectionPasswordChange === 'replace') return 'เปลี่ยนรหัส';
  return 'คงรหัสเดิม';
}

function credentialChangeClass(row: PlotImportRowResult): string {
  // Amber = this row changes a credential (worth noticing); plain muted =
  // nothing happens to the password, which is the overwhelmingly common case.
  return row.inspectionPasswordChange
    ? 'font-medium text-amber-700'
    : 'text-muted-foreground';
}

/** final_plot only (round 8-7A/8-7B) — the actual-harvest figures echoed
 * back from the row's own payload, formatted for the preview table. Read
 * verbatim, never recomputed here. Round 8-10B/8-10C — the unit is always
 * the fixed FINAL_PLOT_YIELD_UNIT constant; the payload carries no unit
 * field to read (and never did echo anything the server didn't fix). Uses
 * the shared formatYieldQuantity helper (lib/yield-planning.ts) so grouping/
 * decimal formatting matches every other yield figure in the app. */
function finalHarvestSummary(row: PlotImportRowResult): { yieldLine: string; dateLine: string | null } | null {
  if (!isFinalPlotRow(row)) return null;
  const p = row.payload;
  const harvest = formatYieldQuantity(p?.harvestYield, FINAL_PLOT_YIELD_UNIT) ?? '—';
  const afterClean = formatYieldQuantity(p?.finalYieldAfterClean, FINAL_PLOT_YIELD_UNIT) ?? '—';
  return {
    yieldLine: `เก็บเกี่ยว ${harvest} → หลังทำความสะอาด ${afterClean}`,
    dateLine: p?.harvestDate ?? null,
  };
}

const FINAL_RECORD_FOUND_TEXT = 'พบบันทึกการตรวจที่ใช้สรุป';
const FINAL_RECORD_NONE_TEXT = 'ไม่มีบันทึกการตรวจที่ใช้สรุป';
const FINAL_RECORD_NOT_YET_CHECKED_TEXT = 'ยังไม่สามารถตรวจสอบบันทึกที่ใช้สรุป';

/** final_plot only — what to tell the user about the inspection record that
 * will summarize this cycle's close (round 8-10B/8-10C). Never a record id,
 * not even truncated: this is words only, by construction (the return type
 * has no id-shaped branch to accidentally render).
 *
 * row.finalRecordNote (the server's own words, round 8-10B) is the primary
 * source — used verbatim whenever present. The previewState binding
 * (finalPlotRowsByNumber) is read only as a FALLBACK for an older response
 * shape that predates finalRecordNote: it only ever exists for a fully valid
 * row (see backend plot_import.py's final_plot_rows — `not s.errors`), so an
 * errored row with no binding correctly falls through to "not yet checked"
 * rather than guessing found/not-found for a row that never resolved. */
function finalRecordStatusText(
  row: PlotImportRowResult,
  finalPlotRowsByNumber: Map<number, PlotImportFinalPlotPreviewStateRow>,
): string {
  if (row.finalRecordNote) return row.finalRecordNote;
  const binding = finalPlotRowsByNumber.get(row.rowNumber);
  if (!binding) return FINAL_RECORD_NOT_YET_CHECKED_TEXT;
  return binding.resolvedFinalInspectionRecordId != null ? FINAL_RECORD_FOUND_TEXT : FINAL_RECORD_NONE_TEXT;
}

function rowBadge(row: PlotImportRowResult): { label: string; className: string } {
  if (row.status === 'valid') {
    return { label: 'พร้อม', className: 'bg-green-50 text-green-700' };
  }
  if (row.errorCode === DUPLICATE_ERROR_CODE) {
    return { label: 'ข้อมูลซ้ำ', className: 'bg-orange-50 text-orange-700' };
  }
  if (row.errorCode === SAME_ACTIVE_CYCLE_LABEL_ERROR_CODE) {
    return { label: 'ชื่อรอบซ้ำ', className: 'bg-orange-50 text-orange-700' };
  }
  return { label: 'ผิดพลาด', className: 'bg-destructive/15 text-destructive' };
}

// Commit outcome — drives which banner/buttons the footer+body show. 'idle'
// is the normal preview-driven flow; the others are terminal per file
// selection ('completed' permanently locks Commit for this modal instance).
// 'stateConflict' (round 8-2.7.2) is a preview-state binding conflict: the
// file/plot state diverged from the approved preview — Commit stays disabled
// until the user Previews again (no auto-retry).
type CommitOutcome =
  | { phase: 'idle' }
  | { phase: 'completed'; report: PlotImportReportFile }
  | { phase: 'blocked'; report: PlotImportReportFile }
  | { phase: 'stateConflict'; detail: string; changedRows: number[] }
  | { phase: 'error'; confirmed: boolean; detail: string };

// Round 8-6E Part B — commit-report's file + previewState must be an
// explicit snapshot of what the USER approved at click-time, never read via
// closure from whatever `preview` happens to be by the time the request
// actually fires (that's exactly the race this round fixes: a stale
// previewState surviving into a commit sent after a newer Preview started).
type CommitVariables = {
  file: File;
  previewState: PlotImportPreviewState | null;
};

export function PlotImportModal({ onClose, onImported }: { onClose: () => void; onImported: () => void }) {
  const [file, setFile] = useState<File | null>(null);
  const [preview, setPreview] = useState<PlotImportPreview | null>(null);
  const [previewErrorMsg, setPreviewErrorMsg] = useState<string | null>(null);
  const [reportErrorMsg, setReportErrorMsg] = useState<string | null>(null);
  const [commitOutcome, setCommitOutcome] = useState<CommitOutcome>({ phase: 'idle' });

  const committed = commitOutcome.phase === 'completed';

  const templateM = useMutation({
    mutationFn: () => downloadPlotImportTemplate(),
    onSuccess: (blob) => downloadBlob(blob, 'plot-import-template.xlsx'),
  });

  const previewM = useMutation({
    mutationFn: (f: File) => previewPlotImport(f),
    onSuccess: (result) => {
      setPreview(result);
      setPreviewErrorMsg(null);
      // Round 8-6E Part C — a fresh Preview landing is the ONLY point that
      // clears a stateConflict/blocked outcome and re-enables Commit. Never
      // reset this eagerly when a re-preview merely STARTS (see runPreview
      // below) — that's exactly what let a stale previewState slip through.
      setCommitOutcome({ phase: 'idle' });
    },
    onError: (error) => {
      const { message, preview: p } = importErrorMessage(error);
      setPreviewErrorMsg(message);
      if (p) setPreview(p);
      // commitOutcome is deliberately left untouched here (Part C item 3): a
      // failed re-preview must not resurrect Commit — it stays disabled
      // until a NEW preview actually succeeds.
    },
  });

  // Round 8-6E Part C — the ONE path both "ตรวจสอบไฟล์" and "ตรวจสอบไฟล์
  // อีกครั้ง" call, so re-preview can never behave differently from the
  // first preview. Clears the OLD preview/previewState up front (defense in
  // depth on top of canCommit's own previewM.isPending/commitOutcome checks
  // below) — Commit has nothing valid to read until the new preview's
  // onSuccess actually lands.
  function runPreview(f: File) {
    setPreview(null);
    setPreviewErrorMsg(null);
    previewM.mutate(f);
  }

  const validationReportM = useMutation({
    mutationFn: (f: File) => downloadPlotImportPreviewReport(f),
    onSuccess: (report) => {
      downloadBlob(report.blob, report.filename);
      setReportErrorMsg(null);
    },
    onError: (error) => {
      setReportErrorMsg(
        error instanceof PlotImportReportError ? error.message : 'ดาวน์โหลดรายงานไม่สำเร็จ',
      );
    },
  });

  // Read-only refresh of the JSON table after a 422 "blocked" commit-report —
  // NOT the previewM mutation (that's reserved for the user's own "ตรวจสอบ
  // ไฟล์" click) so it can't clobber the 'blocked' banner or previewM's own
  // pending/error state. Swallows its own failure: the blocked workbook stays
  // downloadable either way.
  async function refreshPreviewReadOnly(f: File) {
    try {
      const result = await previewPlotImport(f);
      setPreview(result);
    } catch {
      // keep the last known preview + the blocked report
    }
  }

  const commitM = useMutation({
    // Round 8-2.7.2: echo the approved preview_state (from the last successful
    // Preview) so the backend can reject a start_next_cycle commit whose file
    // or plot state has diverged. Round 8-6E Part B — file/previewState are
    // now explicit mutation VARIABLES (the render-time snapshot taken at the
    // moment Commit was clicked — see the button's onClick below), never read
    // via closure here: reading `preview` directly in mutationFn meant a
    // stale previewState from BEFORE a re-preview could still be sent if the
    // button was clickable again before the new preview actually landed.
    mutationFn: ({ file: f, previewState }: CommitVariables) =>
      commitPlotImportWithReport(f, previewState),
    onSuccess: (res) => {
      if (res.outcome === 'completed') {
        downloadBlob(res.report.blob, res.report.filename);
        setCommitOutcome({ phase: 'completed', report: res.report });
        onImported();
        return;
      }
      // outcome === 'blocked' — HTTP 422, nothing written.
      setCommitOutcome({ phase: 'blocked', report: res.report });
      if (file) void refreshPreviewReadOnly(file);
    },
    onError: (error) => {
      // Preview-state binding conflict (round 8-2.7.2): the file/plot state
      // changed after Preview — nothing written. Distinct banner + Commit
      // disabled until the user Previews again; NO auto-retry, NO auto-commit.
      if (error instanceof PlotImportReportError && error.code === PREVIEW_STATE_CONFLICT_CODE) {
        // Round 8-6E Part D — show the backend's REAL reason, not a single
        // hardcoded sentence for every case; changedRows (row numbers only,
        // never activeCycleId/fileSha256) renders as a follow-up hint below.
        setCommitOutcome({
          phase: 'stateConflict',
          detail: stateConflictMessage(error),
          changedRows: error.changedRows,
        });
        return;
      }
      // A response WAS received (409 / JSON 422 / 500) → the backend's
      // transaction already rolled back before responding, so the outcome
      // is confirmed. No response at all (network error/timeout) → status is
      // null → the outcome is NOT confirmed; never claim rollback there.
      const confirmed = error instanceof PlotImportReportError ? error.status !== null : true;
      const detail = error instanceof PlotImportReportError
        ? error.message
        : (error instanceof Error ? error.message : 'เกิดข้อผิดพลาด');
      setCommitOutcome({ phase: 'error', confirmed, detail });
    },
  });

  const busy = commitM.isPending;

  function onFileChange(e: React.ChangeEvent<HTMLInputElement>) {
    setFile(e.target.files?.[0] ?? null);
    setPreview(null);
    setPreviewErrorMsg(null);
    setReportErrorMsg(null);
    setCommitOutcome({ phase: 'idle' });
  }

  function handleClose() {
    if (busy) return; // never let a pending commit-report request get orphaned
    onClose();
  }

  // Round 8-6E Part C — routes through the same runPreview() the initial
  // "ตรวจสอบไฟล์" button uses. Does NOT flip commitOutcome to idle itself
  // any more — previewM's own onSuccess does that, and only once the new
  // preview has actually landed (see runPreview/previewM above).
  function retryAfterBlockedOrError() {
    if (file) runPreview(file);
  }

  // Round 8-6E Part C item 5 (extended round 8-7A.1/8-7B to also cover
  // final_plot) — a start_next_cycle OR final_plot row's commit is bound to
  // the previewState the backend computed for THIS file; a preview response
  // missing it (contract drift, or a stubbed/mocked response in tests) must
  // block Commit rather than send `previewState: null` for a row type that
  // requires it. Legacy actions never set this — previewState stays null for
  // them by contract, and canCommit must not require it in that case.
  const hasPreviewBoundRow = preview?.rows.some(
    (r) => r.action === 'start_next_cycle' || r.action === 'final_plot',
  ) ?? false;
  const missingPreviewStateForBoundAction = hasPreviewBoundRow && !preview?.previewState;

  // Round 8-7B, narrowed round 8-10C — resolvedFinalInspectionRecordId per
  // row, keyed by rowNumber. No longer used to render the id itself (see
  // finalRecordStatusText); kept only as the fallback signal for an older
  // response shape that predates row.finalRecordNote.
  const finalPlotRowsByNumber = new Map(
    (preview?.previewState?.finalPlotRows ?? []).map((s) => [s.rowNumber, s]),
  );

  const canCommit =
    !!file && !!preview && !previewM.isPending &&
    preview.errorRows === 0 && preview.validRows > 0 &&
    commitOutcome.phase === 'idle' && !busy &&
    !missingPreviewStateForBoundAction;

  const duplicateCount = preview
    ? preview.rows.filter((r) => r.errorCode === DUPLICATE_ERROR_CODE).length
    : 0;
  const errorOnlyCount = preview ? preview.errorRows - duplicateCount : 0;
  const previewAllValid = !!preview && preview.errorRows === 0;

  const reportButtonLabel = preview && preview.errorRows > 0
    ? 'ดาวน์โหลดรายการที่ต้องแก้'
    : 'ดาวน์โหลดผลการตรวจสอบ';

  // Action-count summary for the completed banner — derived from the JSON
  // preview rows (never re-parsed from the xlsx): commit-report revalidated
  // server-side and the whole file succeeded, so every row's action counts.
  // A start_next_cycle row (round 8-2.7.1) is bucketed by the server's
  // resolvedAction — read directly from the report contract, never guessed
  // client-side — so it lands in whichever of the two existing buckets
  // (startedCycles/rolledOverCycles) actually happened.
  const completedSummary = preview ? {
    createdPlots: preview.rows.filter((r) => r.action === 'create_plot_with_cycle').length,
    startedCycles: preview.rows.filter((r) =>
      r.action === 'start_new_cycle'
      || (r.action === 'start_next_cycle' && r.resolvedAction === 'start_new_cycle')).length,
    updatedCycles: preview.rows.filter((r) => r.action === 'update_current_cycle').length,
    rolledOverCycles: preview.rows.filter((r) =>
      r.action === 'close_and_start_new_cycle'
      || (r.action === 'start_next_cycle' && r.resolvedAction === 'close_and_start_new_cycle')).length,
    // Round 8-6J — reactivate_plot_with_cycle is unambiguous (never a
    // resolvedAction bucket like start_next_cycle), so a direct action count.
    reactivatedPlots: preview.rows.filter((r) => r.action === 'reactivate_plot_with_cycle').length,
    // Round 8-7A/8-7B — final_plot is likewise unambiguous (its own literal
    // action, never a resolvedAction bucket).
    finalizedPlots: preview.rows.filter((r) => r.action === 'final_plot').length,
    total: preview.rows.length,
  } : null;

  // Round 8-6J Part I — true when the file has at least one reactivate row,
  // so the Preview shows one aggregate warning rather than repeating the
  // same sentence on every matching row.
  const hasReactivateRow = preview?.rows.some(isReactivateRow) ?? false;

  // Round 8-21B — true when the file has at least one update_current_cycle
  // row, so the blank-clears warning for the three Oracle reference columns
  // shows only when it's actually relevant to this file (those columns'
  // "blank cell clears the stored value" rule applies ONLY to
  // update_current_cycle — every other action just gets whatever value the
  // row carries for its brand-new/rolled-over cycle).
  const hasUpdateCurrentCycleRow = preview?.rows.some((r) => r.action === 'update_current_cycle') ?? false;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-2 sm:p-4">
      <div className="flex max-h-[95vh] w-full max-w-3xl flex-col rounded-xl border border-border bg-card shadow-xl">
        <div className="flex items-center justify-between border-b border-border px-4 py-3 sm:px-6 sm:py-4">
          <h2 className="flex items-center gap-2 text-sm font-semibold sm:text-base">
            <FileSpreadsheet className="h-4 w-4 shrink-0 text-green-600" />
            <span className="truncate">นำเข้าแปลง + รอบปลูก (Excel)</span>
          </h2>
          <button
            type="button"
            onClick={handleClose}
            disabled={busy}
            aria-label="ปิด"
            className="shrink-0 text-muted-foreground hover:text-foreground disabled:opacity-40"
          >
            ✕
          </button>
        </div>

        <div className="space-y-4 overflow-y-auto px-4 py-4 sm:px-6 sm:py-5">
          <div className="rounded-md border border-border bg-secondary/30 p-3 text-xs text-muted-foreground">
            <p className="font-medium text-foreground">
              การกระทำหลัก 4 แบบที่ใช้บ่อย (คอลัมน์ <span className="font-mono">action</span>):
            </p>
            <ul className="mt-1.5 list-disc space-y-1 pl-4">
              <li><span className="font-mono">create_plot_with_cycle</span> — สร้างแปลงพร้อมรอบแรก</li>
              <li><span className="font-mono">update_current_cycle</span> — แก้รอบปลูกปัจจุบัน</li>
              <li><span className="font-mono">start_next_cycle</span> — เริ่มรอบถัดไป (เฉพาะแปลงที่ใช้งานอยู่)</li>
              <li><span className="font-mono">reactivate_plot_with_cycle</span> — เปิดใช้งานแปลง + เริ่มรอบปลูกใหม่ (เฉพาะแปลงที่ปิดใช้งาน)</li>
            </ul>
            <p className="mt-2 rounded-md border border-amber-300 bg-amber-50 px-2 py-1.5 text-amber-800">
              ระบบจะตรวจสถานะแปลงให้เอง หากไม่มีรอบเปิดอยู่จะเริ่มรอบใหม่ทันที
              หากมีรอบเปิดอยู่จะปิดรอบเดิมเป็นเก็บเกี่ยวแล้วและเริ่มรอบใหม่ในครั้งเดียว
            </p>
            <p className="mt-2 rounded-md border border-blue-300 bg-blue-50 px-2 py-1.5 text-blue-800">
              แปลงที่ปิดใช้งานอยู่: ใช้ <span className="font-mono">reactivate_plot_with_cycle</span> เท่านั้น
              (ใช้ <span className="font-mono">start_next_cycle</span> กับแปลงปิดใช้งานไม่ได้) —
              เมื่อยืนยันนำเข้าระบบจะเปิดแปลงกลับมาใช้งานและเริ่มรอบปลูกใหม่ในครั้งเดียว
            </p>
            <p className="mt-2">
              <span className="font-medium text-foreground">กรณีพิเศษ/ไฟล์เก่า:</span>{' '}
              <span className="font-mono">start_new_cycle</span> และ{' '}
              <span className="font-mono">close_and_start_new_cycle</span> ยังใช้งานได้เหมือนเดิม
              (ไม่จำเป็นต้องเลือกเองแล้วสำหรับงานประจำวัน — ใช้ start_next_cycle แทนได้)
            </p>
            <p className="mt-1.5">
              ต้องระบุชื่อรอบปลูก (cycleLabel) เมื่อใช้ start_next_cycle · วันที่ปลูกใช้รูปแบบ YYYY-MM-DD ·
              1 แถวต่อแปลงต่อไฟล์
            </p>
            {/* Round 8-12B — the Auto Lot V2 contract, stated where the user
                is about to fill the file in. */}
            <p className="mt-2 rounded-md border border-border bg-background px-2 py-1.5">
              <span className="font-medium text-foreground">Lot No ระบบ (lotNo):</span>{' '}
              เว้น <span className="font-mono">lotNo</span> ว่าง เพื่อให้ระบบสร้างจาก
              ชื่อรอบปลูก + รหัส Supplier + P.Code + เลขรัน
              (เช่น <span className="font-mono">2605-SUP010-WM-141-003</span>) —
              ต้องกรอก <span className="font-mono">cycleLabel</span> และ{' '}
              <span className="font-mono">pCode</span> ด้วย มิฉะนั้นแถวนั้นจะไม่ผ่านการตรวจสอบ ·
              กรอก <span className="font-mono">lotNo</span> เอง = ใช้ค่าที่กรอก (ไม่สร้างให้)
            </p>
            <p className="mt-2 rounded-md border border-border bg-background px-2 py-1.5">
              <span className="font-medium text-foreground">Supplier Lot No (supplierLotNo):</span>{' '}
              กรอกได้หาก Supplier มีเลข Lot ของตนเอง เว้นว่างได้
              และไม่กระทบเลข Lot ที่ระบบสร้าง
            </p>
            {/* Round 8-21B — deliberately spells out the DIFFERENT blank-cell
                rule for these three columns vs. every other optional column
                in this same box (poNumber/pCode/supplierLotNo above all
                preserve on blank). */}
            <p className="mt-2 rounded-md border border-border bg-background px-2 py-1.5">
              <span className="font-medium text-foreground">
                Oracle Supplier Code / Oracle Invoice / Ref Account (oracleSupplierCode/oracleInvoice/refAccount):
              </span>{' '}
              ข้อมูลอ้างอิงระดับรอบปลูก ไม่บังคับ เว้นว่างได้ — สำหรับ{' '}
              <span className="font-mono">update_current_cycle</span> ช่องที่เว้นว่าง (แต่คอลัมน์ยังอยู่ในไฟล์)
              จะล้างค่าเดิม ต่างจาก poNumber/pCode/supplierLotNo ด้านบนที่เว้นว่างแล้วคงค่าเดิม
            </p>
            <p className="mt-2 rounded-md border border-border bg-background px-2 py-1.5">
              <span className="font-medium text-foreground">เบอร์เข้าตรวจ (primaryPhone/additionalPhones):</span>{' '}
              เว้นว่างทั้ง 2 ช่องในแปลงที่มีอยู่แล้ว = คงเบอร์เดิมไว้ ไม่แก้ไข ·
              ระบุ primaryPhone = แทนที่ชุดเบอร์ทั้งหมดของแปลงนั้นด้วยค่าที่ให้
              (additionalPhones ว่าง = ล้างเบอร์เสริมทั้งหมด) ·
              ต้องการล้างเบอร์ทั้งหมดให้ใช้หน้าจัดการเบอร์เข้าตรวจของแปลงแทน
            </p>
            {/* Round 8-6B Part F — points at the filtered/contextual template
                (round 8-6A) without changing what this button does: it still
                always downloads the generic blank template with no filter. */}
            <p className="mt-2 rounded-md border border-border bg-background px-2 py-1.5">
              <span className="font-medium text-foreground">ต้องการไฟล์ที่มีข้อมูลแปลงปัจจุบัน?</span>{' '}
              ให้เลือก Supplier/จังหวัดในหน้าแปลง แล้วกด &quot;Excel ตามตัวกรอง&quot; แทนปุ่มด้านล่างนี้
              (ปุ่มนี้ดาวน์โหลดเทมเพลตเปล่าเสมอ)
            </p>
            <p className="mt-1.5">
              ไฟล์จาก &quot;Excel ตามตัวกรอง&quot; มี 4 ชีต: ชีต{' '}
              <span className="font-mono">นำเข้ารอบใหม่</span> = ชีตที่ระบบอ่าน (อัปโหลดกลับมาที่นี่ — รวมทั้งแปลงที่ใช้งานและปิดใช้งานตามตัวกรองสถานะแปลง) · ชีต{' '}
              <span className="font-mono">ข้อมูลปัจจุบัน</span> = ใช้เปรียบเทียบก่อนแก้ (ระบบไม่อ่าน) · ชีต{' '}
              <span className="font-mono">รายการที่ไม่รวม</span> = แปลง/Supplier ที่ถูกตัดออกพร้อมเหตุผล (ระบบไม่อ่าน) · ชีต{' '}
              <span className="font-mono">ตัวอย่าง</span> = ข้อมูลสีแดง ระบบไม่อ่านชีตนี้
              (เทมเพลตเปล่าจากปุ่มด้านล่างมีชีตเดียว)
            </p>
            <p className="mt-1.5">
              คอลัมน์ <span className="font-mono">currentPlotStatus</span> ไว้อ้างอิงเท่านั้น —
              แก้ค่าในช่องนี้ไม่ทำให้สถานะแปลงเปลี่ยน คอลัมน์ <span className="font-mono">action</span> เท่านั้นที่กำหนดว่าระบบจะทำอะไร
            </p>
          </div>

          <div className="flex flex-wrap items-center gap-2 sm:gap-3">
            <button
              type="button"
              onClick={() => templateM.mutate()}
              disabled={templateM.isPending || busy}
              className="inline-flex items-center gap-2 rounded-md border border-border bg-background px-3 py-2 text-sm font-medium shadow-sm hover:bg-secondary disabled:opacity-60"
            >
              {templateM.isPending ? <Loader2 className="h-4 w-4 animate-spin" /> : <Download className="h-4 w-4" />}
              ดาวน์โหลดเทมเพลต
            </button>

            <input
              type="file"
              accept=".xlsx"
              onChange={onFileChange}
              disabled={committed || busy}
              aria-label="เลือกไฟล์ Excel"
              className="min-w-0 max-w-full text-sm file:mr-3 file:rounded-md file:border file:border-border file:bg-background file:px-3 file:py-1.5 file:text-sm file:font-medium hover:file:bg-secondary disabled:opacity-60"
            />

            <button
              type="button"
              onClick={() => file && runPreview(file)}
              disabled={!file || previewM.isPending || committed || busy}
              className="inline-flex items-center gap-2 rounded-md bg-primary px-3 py-2 text-sm font-medium text-primary-foreground shadow-sm hover:bg-primary/90 disabled:opacity-60"
            >
              {previewM.isPending ? <Loader2 className="h-4 w-4 animate-spin" /> : <Upload className="h-4 w-4" />}
              ตรวจสอบไฟล์
            </button>
          </div>

          {/* Round 8-9B.1 — the uploaded file may contain plot inspection
              passwords in plaintext. The server never stores the upload and
              never echoes a password back, but the file on the user's own
              machine is outside our control, so say so plainly. */}
          <p className="flex items-start gap-2 rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-800">
            <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" aria-hidden="true" />
            <span>
              ไฟล์ Excel อาจมีรหัสยืนยันแปลง กรุณาจำกัดผู้เข้าถึงไฟล์และลบไฟล์เมื่อใช้งานเสร็จ
            </span>
          </p>

          {previewErrorMsg && (
            <div className="flex items-start gap-2 rounded-md border border-destructive/30 bg-destructive/10 px-3 py-2 text-sm text-destructive">
              <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />
              <span className="min-w-0 break-words">{previewErrorMsg}</span>
            </div>
          )}

          {reportErrorMsg && (
            <div className="flex items-start gap-2 rounded-md border border-destructive/30 bg-destructive/10 px-3 py-2 text-sm text-destructive">
              <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />
              <span className="min-w-0 break-words">{reportErrorMsg}</span>
            </div>
          )}

          {commitOutcome.phase === 'completed' && (
            <div className="rounded-md border border-green-300 bg-green-50 px-3 py-3 text-sm text-green-800">
              <div className="flex items-start gap-2">
                <CheckCircle2 className="mt-0.5 h-4 w-4 shrink-0 text-green-600" />
                <div className="min-w-0">
                  <p className="font-medium">นำเข้าสำเร็จ</p>
                  <p className="mt-0.5 text-xs">ข้อมูลทุกแถวถูกนำเข้าเรียบร้อยแล้ว</p>
                  {completedSummary && (
                    <p className="mt-1 text-xs">
                      สร้างแปลง {completedSummary.createdPlots} · เริ่มรอบปลูก {completedSummary.startedCycles} ·
                      แก้รอบปลูก {completedSummary.updatedCycles} · จบรอบเดิม+เริ่มใหม่ {completedSummary.rolledOverCycles} ·
                      เปิดใช้งานแปลง {completedSummary.reactivatedPlots} ·
                      ลงผลผลิตสุดท้ายและปิดรอบ {completedSummary.finalizedPlots} แปลง ·
                      รวมทั้งหมด {completedSummary.total}
                    </p>
                  )}
                </div>
              </div>
              <div className="mt-3 flex flex-wrap gap-2">
                <button
                  type="button"
                  onClick={() => downloadBlob(commitOutcome.report.blob, commitOutcome.report.filename)}
                  className="inline-flex items-center gap-2 rounded-md border border-green-300 bg-white px-3 py-1.5 text-xs font-medium text-green-800 shadow-sm hover:bg-green-50"
                >
                  <Download className="h-3.5 w-3.5" />
                  ดาวน์โหลดผลการนำเข้าอีกครั้ง
                </button>
              </div>
            </div>
          )}

          {preview && !committed && (
            <div className="space-y-2">
              <div>
                <p className="text-sm font-medium">{previewAllValid ? 'ตรวจสอบผ่าน' : 'ตรวจสอบไม่ผ่าน'}</p>
                <p className="text-xs text-muted-foreground">
                  {previewAllValid
                    ? `ผ่านการตรวจสอบ ${preview.totalRows} แถว`
                    : 'พบข้อผิดพลาด กรุณาแก้ไขทุกแถวก่อนนำเข้า'}
                </p>
                <p className="text-xs text-muted-foreground">ยังไม่มีข้อมูลถูกนำเข้า</p>
              </div>

              <div className="flex flex-wrap gap-3 text-sm">
                <span className="text-muted-foreground">ทั้งหมด {preview.totalRows} แถว</span>
                <span className="font-medium text-green-700">พร้อม {preview.validRows}</span>
                <span className={errorOnlyCount > 0 ? 'font-medium text-destructive' : 'text-muted-foreground'}>
                  ผิดพลาด {errorOnlyCount}
                </span>
                <span className={duplicateCount > 0 ? 'font-medium text-orange-700' : 'text-muted-foreground'}>
                  ข้อมูลซ้ำ {duplicateCount}
                </span>
              </div>

              <div className="flex flex-wrap gap-2">
                <button
                  type="button"
                  onClick={() => file && validationReportM.mutate(file)}
                  disabled={!file || validationReportM.isPending || busy}
                  className="inline-flex items-center gap-2 rounded-md border border-border bg-background px-3 py-1.5 text-xs font-medium shadow-sm hover:bg-secondary disabled:opacity-60"
                >
                  {validationReportM.isPending ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Download className="h-3.5 w-3.5" />}
                  {reportButtonLabel}
                </button>
              </div>

              {hasReactivateRow && (
                <p className="flex items-start gap-2 rounded-md border border-blue-300 bg-blue-50 px-3 py-2 text-xs text-blue-800">
                  <Unlock className="mt-0.5 h-3.5 w-3.5 shrink-0" aria-hidden="true" />
                  <span>
                    เมื่อยืนยันนำเข้า ระบบจะเปิดแปลงนี้กลับมาใช้งานและเริ่มรอบปลูกใหม่
                    ({completedSummary?.reactivatedPlots ?? 0} แปลง) — Download/ตรวจสอบไฟล์ยังไม่เปลี่ยนสถานะแปลงใดๆ
                  </span>
                </p>
              )}

              {/* Round 8-21B — shown ONLY when the file has an
                  update_current_cycle row: the three Oracle reference columns
                  are the one place a blank Excel cell CLEARS a stored value
                  instead of preserving it (unlike poNumber/pCode/
                  supplierLotNo). */}
              {hasUpdateCurrentCycleRow && (
                <p className="rounded-md border border-amber-300 bg-amber-50 px-3 py-2 text-xs text-amber-800">
                  สำหรับ update_current_cycle: ช่อง Oracle ที่เว้นว่างจะล้างค่าเดิม
                </p>
              )}

              {!previewAllValid && (
                <p className="rounded-md border border-amber-300 bg-amber-50 px-3 py-2 text-xs text-amber-800">
                  ต้องแก้ไขแถวที่ผิดพลาดทั้งหมดก่อนจึงจะยืนยันนำเข้าได้ — ระบบนำเข้าทั้งไฟล์
                  (all-or-nothing) จะไม่มีข้อมูลแถวใดถูกนำเข้าจนกว่าทุกแถวจะผ่าน
                </p>
              )}

              {/* Round 8-6E Part C item 5 (extended round 8-7A.1/8-7B) — a
                  start_next_cycle OR final_plot row's commit needs the
                  previewState this preview should have carried; if it's
                  structurally missing, block Commit and say why rather than
                  silently sending previewState: null. */}
              {missingPreviewStateForBoundAction && (
                <p className="rounded-md border border-amber-300 bg-amber-50 px-3 py-2 text-xs text-amber-800">
                  กรุณาตรวจสอบไฟล์อีกครั้งก่อนยืนยันนำเข้า — ไม่พบข้อมูลตรวจสอบสถานะรอบปลูก (previewState)
                  สำหรับไฟล์นี้
                </p>
              )}

              <div className="max-h-72 overflow-x-auto overflow-y-auto rounded-md border border-border">
                <table className="min-w-full divide-y divide-border text-sm">
                  <thead className="sticky top-0 bg-muted/60">
                    <tr>
                      {['Excel Row', 'การกระทำ', 'Supplier', 'แปลง', 'เบอร์', 'ชื่อรอบปลูก', 'รหัสยืนยันแปลง', 'สถานะ', 'ข้อความ'].map((h) => (
                        <th key={h} className="whitespace-nowrap px-3 py-2 text-left text-xs font-medium text-muted-foreground">{h}</th>
                      ))}
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-border">
                    {preview.rows.map((r) => {
                      const badge = rowBadge(r);
                      const finalHarvest = finalHarvestSummary(r);
                      const recordStatusText = finalRecordStatusText(r, finalPlotRowsByNumber);
                      return (
                        <tr
                          key={r.rowNumber}
                          className={
                            r.status === 'error'
                              ? 'bg-destructive/5'
                              : isRolloverRow(r)
                                ? 'bg-amber-50/60'
                                : isReactivateRow(r)
                                  ? 'bg-blue-50/60'
                                  : isFinalPlotRow(r)
                                    ? 'bg-emerald-50/60'
                                    : ''
                          }
                        >
                          <td className="whitespace-nowrap px-3 py-2 text-muted-foreground">{r.rowNumber}</td>
                          <td className="whitespace-nowrap px-3 py-2">
                            <span className="inline-flex items-center gap-1">
                              {isRolloverRow(r) && (
                                <AlertTriangle className="h-3.5 w-3.5 shrink-0 text-amber-600" aria-hidden="true" />
                              )}
                              {isReactivateRow(r) && (
                                <Unlock className="h-3.5 w-3.5 shrink-0 text-blue-600" aria-hidden="true" />
                              )}
                              {isFinalPlotRow(r) && (
                                <CheckCircle2 className="h-3.5 w-3.5 shrink-0 text-emerald-600" aria-hidden="true" />
                              )}
                              {rowActionLabel(r)}
                            </span>
                            {rowCurrentPlotStatusLabel(r) && (
                              <div className="mt-0.5 whitespace-nowrap text-[10px] text-muted-foreground">
                                สถานะแปลงปัจจุบัน: {rowCurrentPlotStatusLabel(r)}
                              </div>
                            )}
                          </td>
                          <td className="whitespace-nowrap px-3 py-2">{r.supplierCode ?? '—'}</td>
                          <td className="whitespace-nowrap px-3 py-2">{r.plotCode ?? '—'}</td>
                          <td className="whitespace-nowrap px-3 py-2 font-mono text-xs">{phoneSummary(r)}</td>
                          <td className="px-3 py-2 text-xs text-muted-foreground">
                            <div className="whitespace-nowrap">{r.payload?.cycleLabel ?? '—'}</div>
                            {/* Round 8-5B — PO + resulting Lot as a compact
                                secondary line (no extra column → no mobile
                                overflow). Shows the real committed lotNo after
                                commit, else the preview's proposed value.
                                Round 8-12B — that proposed value is the
                                BACKEND's own V2 string
                                ({cycleLabel}-{supplierCode}-{pCode}-###) and
                                is rendered verbatim: the running number is
                                server-assigned, so the client never builds or
                                guesses one. Labelled "Lot ระบบ" to separate it
                                from the supplier's own lot below. */}
                            {(r.payload?.poNumber || r.proposedLotNo || r.resultLotNo) && (
                              <div className="mt-0.5 whitespace-nowrap font-mono text-[11px] text-foreground/70">
                                {r.payload?.poNumber && <span>PO {r.payload.poNumber}</span>}
                                {(r.resultLotNo ?? r.proposedLotNo) && (
                                  <span className={r.payload?.poNumber ? 'ml-2' : ''}>
                                    Lot ระบบ {r.resultLotNo ?? r.proposedLotNo}
                                    {lotModeLabel(r.lotMode) && (
                                      <span className="ml-1 text-[10px] text-muted-foreground">({lotModeLabel(r.lotMode)})</span>
                                    )}
                                  </span>
                                )}
                              </div>
                            )}
                            {/* Round 8-12B — the SUPPLIER's own lot number for
                                this row, always its own line so it is never
                                mistaken for the system lot. "—" when the cell
                                was blank (or the column absent in an older
                                workbook). */}
                            {r.payload && (
                              <div className="mt-0.5 whitespace-nowrap font-mono text-[11px] text-foreground/70">
                                Supplier Lot {r.payload.supplierLotNo ?? '—'}
                              </div>
                            )}
                            {/* Round 8-21B — three independent, OPTIONAL
                                back-office reference fields, grouped right
                                after Supplier Lot (never a "—" hidden line —
                                always shown so the user can tell "blank" from
                                "column absent in this file" via the
                                update_current_cycle warning banner below the
                                table, not from this row alone). Wraps instead
                                of overflowing on narrow screens. */}
                            {r.payload && (
                              <div className="mt-0.5 break-words font-mono text-[11px] text-foreground/70">
                                Oracle {r.payload.oracleSupplierCode ?? '—'} · Invoice {r.payload.oracleInvoice ?? '—'} · Ref Account {r.payload.refAccount ?? '—'}
                              </div>
                            )}
                            {/* Round 8-7A/8-7B — final_plot's actual-harvest
                                detail: yield before/after cleaning (always
                                kg — round 8-10B/8-10C), harvest date, and
                                whether the server found a record to
                                summarize the close (words only — never an
                                id, see finalRecordStatusText). */}
                            {finalHarvest && (
                              <div className="mt-0.5 whitespace-nowrap text-[11px] text-foreground/70">
                                <div>{finalHarvest.yieldLine}</div>
                                {finalHarvest.dateLine && <div>เก็บเกี่ยวเมื่อ {finalHarvest.dateLine}</div>}
                                <div>{recordStatusText}</div>
                                {r.payload?.finalNote && <div>หมายเหตุ: {r.payload.finalNote}</div>}
                              </div>
                            )}
                          </td>
                          {/* Round 8-9B.1 — what this row does to the plot's
                              inspection password. Shows ONLY the intent; the
                              password itself never reaches the browser (it is
                              not in any response field), so there is nothing
                              here that could leak it. */}
                          <td className="whitespace-nowrap px-3 py-2 text-xs">
                            <span className={credentialChangeClass(r)}>{credentialChangeLabel(r)}</span>
                          </td>
                          <td className="whitespace-nowrap px-3 py-2">
                            <span className={`inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium ${badge.className}`}>
                              {badge.label}
                            </span>
                          </td>
                          <td className="max-w-xs whitespace-normal break-words px-3 py-2 text-xs text-muted-foreground">
                            {r.message || '—'}
                            {/* Round 8-7A/8-7B — non-blocking warning (e.g.
                                finalYieldAfterClean > harvestYield): amber,
                                never red/destructive — the row stays READY
                                and Commit is never blocked by it. */}
                            {r.warning && (
                              <p className="mt-1 flex items-start gap-1 text-amber-700">
                                <AlertTriangle className="mt-0.5 h-3 w-3 shrink-0" aria-hidden="true" />
                                <span>{r.warning}</span>
                              </p>
                            )}
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            </div>
          )}
        </div>

        {/* Round 8-6F Part D — commit-outcome failure banners moved OUT of
            the scrollable body (above) and into this fixed section right
            above the footer, so a user who clicked "ยืนยันนำเข้า" with the
            preview table scrolled down still sees the result immediately —
            never scrolled out of view. The scrollable body above still holds
            the preview-error/report-error banners and the preview table
            itself; only commit-outcome feedback moved. */}
        {(commitOutcome.phase === 'blocked' || commitOutcome.phase === 'stateConflict' || commitOutcome.phase === 'error') && (
          <div className="border-t border-border px-4 py-3 sm:px-6">
            {commitOutcome.phase === 'blocked' && (
              <div className="rounded-md border border-destructive/30 bg-destructive/10 px-3 py-3 text-sm text-destructive">
                <div className="flex items-start gap-2">
                  <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />
                  <div className="min-w-0">
                    <p className="font-medium">นำเข้าไม่สำเร็จ</p>
                    <p className="mt-0.5 text-xs">ข้อมูลมีการเปลี่ยนแปลงหลังการตรวจสอบ หรือพบข้อผิดพลาดใหม่</p>
                    <p className="mt-0.5 text-xs font-medium">ไม่มีข้อมูลในไฟล์นี้ถูกนำเข้า</p>
                  </div>
                </div>
                <div className="mt-3 flex flex-wrap gap-2">
                  <button
                    type="button"
                    onClick={() => downloadBlob(commitOutcome.report.blob, commitOutcome.report.filename)}
                    className="inline-flex items-center gap-2 rounded-md border border-destructive/30 bg-white px-3 py-1.5 text-xs font-medium text-destructive shadow-sm hover:bg-destructive/5"
                  >
                    <Download className="h-3.5 w-3.5" />
                    ดาวน์โหลดรายการที่ต้องแก้
                  </button>
                  <button
                    type="button"
                    onClick={retryAfterBlockedOrError}
                    disabled={previewM.isPending}
                    className="inline-flex items-center gap-2 rounded-md border border-border bg-white px-3 py-1.5 text-xs font-medium text-foreground shadow-sm hover:bg-secondary disabled:opacity-60"
                  >
                    <RefreshCw className="h-3.5 w-3.5" />
                    ตรวจสอบไฟล์อีกครั้ง
                  </button>
                </div>
              </div>
            )}

            {commitOutcome.phase === 'stateConflict' && (
              <div className="rounded-md border border-amber-300 bg-amber-50 px-3 py-3 text-sm text-amber-800">
                <div className="flex items-start gap-2">
                  <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-amber-600" />
                  <div className="min-w-0">
                    {/* Round 8-6E Part D — the REAL backend reason/message, not
                        a single hardcoded sentence for every conflict case. */}
                    <p className="font-medium break-words">{commitOutcome.detail}</p>
                    {commitOutcome.changedRows.length > 0 && (
                      <p className="mt-1 text-xs">
                        แถวที่ต้องตรวจสอบ: {commitOutcome.changedRows.join(', ')}
                      </p>
                    )}
                    <p className="mt-0.5 text-xs font-medium">ไม่มีข้อมูลในไฟล์นี้ถูกนำเข้า</p>
                  </div>
                </div>
                <div className="mt-3 flex flex-wrap gap-2">
                  <button
                    type="button"
                    onClick={retryAfterBlockedOrError}
                    disabled={previewM.isPending}
                    className="inline-flex items-center gap-2 rounded-md border border-amber-300 bg-white px-3 py-1.5 text-xs font-medium text-amber-800 shadow-sm hover:bg-amber-100 disabled:opacity-60"
                  >
                    <RefreshCw className="h-3.5 w-3.5" />
                    ตรวจสอบไฟล์อีกครั้ง
                  </button>
                </div>
              </div>
            )}

            {commitOutcome.phase === 'error' && (
              <div className="rounded-md border border-destructive/30 bg-destructive/10 px-3 py-3 text-sm text-destructive">
                <div className="flex items-start gap-2">
                  <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />
                  <div className="min-w-0">
                    <p className="font-medium">นำเข้าไม่สำเร็จ</p>
                    <p className="mt-0.5 text-xs">ระบบไม่ได้ยืนยันว่ามีข้อมูลถูกนำเข้า กรุณาตรวจสอบสถานะแปลงก่อนลองใหม่</p>
                    <p className="mt-0.5 text-xs font-medium">
                      {commitOutcome.confirmed
                        ? 'ไม่มีข้อมูลในไฟล์นี้ถูกนำเข้า'
                        : 'ไม่สามารถยืนยันผลการนำเข้าได้ กรุณารีเฟรชรายการแปลงและตรวจสอบรอบปลูกก่อนลองอีกครั้ง'}
                    </p>
                  </div>
                </div>
                <div className="mt-3">
                  <button
                    type="button"
                    onClick={retryAfterBlockedOrError}
                    className="inline-flex items-center gap-2 rounded-md border border-border bg-white px-3 py-1.5 text-xs font-medium text-foreground shadow-sm hover:bg-secondary"
                  >
                    <RefreshCw className="h-3.5 w-3.5" />
                    ปิดข้อความนี้
                  </button>
                </div>
              </div>
            )}
          </div>
        )}

        <div className="flex flex-wrap justify-end gap-2 border-t border-border px-4 py-3 sm:px-6 sm:py-4">
          <button
            type="button"
            onClick={handleClose}
            disabled={busy}
            className="rounded-md border border-border px-4 py-2 text-sm hover:bg-secondary disabled:opacity-40"
          >
            {committed ? 'ปิด' : 'ยกเลิก'}
          </button>
          {!committed && (
            <button
              type="button"
              onClick={() => file && preview && commitM.mutate({
                file,
                previewState: preview.previewState ?? null,
              })}
              disabled={!canCommit}
              className="inline-flex items-center gap-2 rounded-md bg-green-600 px-4 py-2 text-sm font-medium text-white shadow-sm hover:bg-green-700 disabled:opacity-50"
            >
              {busy && <Loader2 className="h-4 w-4 animate-spin" />}
              {busy ? 'กำลังนำเข้า...' : 'ยืนยันนำเข้า'}
            </button>
          )}
        </div>
      </div>
    </div>
  );
}

export default PlotImportModal;
