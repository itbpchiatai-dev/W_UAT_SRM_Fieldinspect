/**
 * Master Data crop/variety Excel import modal (round 8-15B; backend rounds
 * 8-15A/8-15A.1).
 *
 * Flow: download template → pick .xlsx → Preview (read-only JSON, for the
 * on-screen table) → optionally download a validation report (.xlsx,
 * read-only) → "ยืนยันนำเข้า" commits through commit-report (ONE mutation,
 * ONE request — the plain JSON /commit endpoint is not exposed by
 * api/masterdata.ts at all this round, so it cannot be called from here
 * even by mistake) — on success the Completed workbook downloads
 * automatically and Commit locks permanently for this modal instance.
 *
 * commit-report is the ONLY endpoint that ever mutates data in this modal.
 * Unlike Plot Import, this backend never returns a downloadable "blocked"
 * workbook for a 422 from commit-report (see api/masterdata.ts's module
 * docstring) — a row-error/state-conflict outcome here is always a plain
 * message banner, never a second file download.
 */
import { useState } from 'react';
import axios from 'axios';
import { useMutation } from '@tanstack/react-query';
import {
  AlertTriangle, CheckCircle2, Download, FileSpreadsheet, Loader2, Upload,
} from 'lucide-react';
import {
  commitCropVarietyImportWithReport,
  downloadCropVarietyImportPreviewReport,
  downloadCropVarietyImportTemplate,
  previewCropVarietyImport,
  MasterDataImportReportError,
  type CropVarietyImportAction,
  type CropVarietyImportPCodeAction,
  type CropVarietyImportPreview,
  type CropVarietyImportReportFile,
  type CropVarietyImportRowResult,
} from '../../api/masterdata';
import { downloadBlob } from '../../lib/downloadBlob';

const ACTION_LABEL: Record<CropVarietyImportAction, string> = {
  create_crop: 'สร้างชนิดพืช',
  create_variety: 'สร้างพันธุ์',
  create_crop_and_variety: 'สร้างชนิดพืชและพันธุ์',
  activate_variety: 'เปิดใช้งานพันธุ์',
  deactivate_variety: 'ปิดใช้งานพันธุ์',
  none: 'ไม่มีการเปลี่ยนแปลง',
};

// Round 8-26B — rendered alongside ACTION_LABEL, not merged into it: the two
// actions are independent, so a row can legitimately show both.
const P_CODE_ACTION_LABEL: Record<CropVarietyImportPCodeAction, string> = {
  create_p_code: 'สร้าง P.Code',
  activate_p_code: 'เปิดใช้งาน P.Code',
  none: '',
};

/** One row's plan as one readable phrase. A row that changes both its
 * crop/variety AND its P.Code shows both, joined — never only the first. */
function actionLabel(row: CropVarietyImportRowResult): string {
  const parts: string[] = [];
  if (row.action !== 'none') parts.push(ACTION_LABEL[row.action] ?? row.action);
  if (row.pCodeAction !== 'none') parts.push(P_CODE_ACTION_LABEL[row.pCodeAction] ?? row.pCodeAction);
  return parts.length > 0 ? parts.join(' + ') : ACTION_LABEL.none;
}

function rowBadge(row: CropVarietyImportRowResult): { label: string; className: string } {
  if (row.rowStatus === 'ERROR') return { label: 'ผิดพลาด', className: 'bg-destructive/15 text-destructive' };
  if (row.rowStatus === 'READY') return { label: 'พร้อมนำเข้า', className: 'bg-success/15 text-success-readable' };
  return { label: 'ข้าม', className: 'bg-secondary text-secondary-foreground' };
}

const GENERIC_CONNECTION_ERROR = 'เชื่อมต่อระบบไม่สำเร็จ กรุณาลองใหม่อีกครั้ง';

/** Reads a plain-JSON preview error's `detail` (a flat string for this
 * endpoint's file-level errors — see services/master_data_crop_variety_
 * import.CropVarietyImportFileError) into a Thai-friendly message. Never
 * surfaces raw JSON. */
function previewErrorMessage(error: unknown): string {
  if (axios.isAxiosError(error)) {
    const status = error.response?.status;
    const data = error.response?.data as { detail?: unknown } | string | undefined;
    let text = '';
    if (typeof data === 'string') {
      text = data;
    } else if (data && typeof data === 'object' && 'detail' in data) {
      const d = (data as { detail: unknown }).detail;
      if (typeof d === 'string') text = d;
    }
    return text || (status ? `เกิดข้อผิดพลาด (HTTP ${status})` : GENERIC_CONNECTION_ERROR);
  }
  return error instanceof Error ? error.message : GENERIC_CONNECTION_ERROR;
}

// Commit outcome — drives which banner/buttons the footer+body show. 'idle'
// is the normal preview-driven flow; the others are terminal per file
// selection ('completed' permanently locks Commit for this modal instance).
type CommitOutcome =
  | { phase: 'idle' }
  | { phase: 'completed'; report: CropVarietyImportReportFile }
  | { phase: 'blocked'; detail: string }
  | { phase: 'stateConflict'; detail: string }
  | { phase: 'error'; confirmed: boolean; detail: string };

export function MasterDataCropVarietyImportModal({
  onClose, onImported,
}: {
  onClose: () => void;
  onImported: () => void;
}) {
  const [file, setFile] = useState<File | null>(null);
  const [preview, setPreview] = useState<CropVarietyImportPreview | null>(null);
  const [previewErrorMsg, setPreviewErrorMsg] = useState<string | null>(null);
  const [reportErrorMsg, setReportErrorMsg] = useState<string | null>(null);
  const [commitOutcome, setCommitOutcome] = useState<CommitOutcome>({ phase: 'idle' });

  const committed = commitOutcome.phase === 'completed';

  const templateM = useMutation({
    mutationFn: () => downloadCropVarietyImportTemplate(),
    onSuccess: ({ blob, filename }) => downloadBlob(blob, filename),
  });

  const previewM = useMutation({
    mutationFn: (f: File) => previewCropVarietyImport(f),
    onSuccess: (result) => {
      setPreview(result);
      setPreviewErrorMsg(null);
      // A fresh Preview landing is the ONLY point that clears a
      // blocked/stateConflict/error outcome and re-enables Commit.
      setCommitOutcome({ phase: 'idle' });
    },
    onError: (error) => {
      setPreviewErrorMsg(previewErrorMessage(error));
      // commitOutcome is deliberately left untouched — a failed re-preview
      // must not resurrect Commit.
    },
  });

  // The ONE path both "ตรวจสอบไฟล์" and "ตรวจสอบไฟล์อีกครั้ง" call, so
  // re-preview can never behave differently from the first preview. Clears
  // the OLD preview/previewState up front — Commit has nothing valid to
  // read until the new preview's onSuccess actually lands.
  function runPreview(f: File) {
    setPreview(null);
    setPreviewErrorMsg(null);
    previewM.mutate(f);
  }

  const reportM = useMutation({
    mutationFn: (f: File) => downloadCropVarietyImportPreviewReport(f),
    onSuccess: (report) => {
      downloadBlob(report.blob, report.filename);
      setReportErrorMsg(null);
    },
    onError: (error) => {
      setReportErrorMsg(
        error instanceof MasterDataImportReportError ? error.message : 'ดาวน์โหลดรายงานไม่สำเร็จ',
      );
    },
  });

  const commitM = useMutation({
    // file/previewState are explicit mutation VARIABLES (the render-time
    // snapshot at the moment Commit was clicked), never read via closure —
    // avoids a stale previewState from before a re-preview being sent.
    mutationFn: (vars: { file: File; previewState: NonNullable<CropVarietyImportPreview['previewState']> }) =>
      commitCropVarietyImportWithReport(vars.file, vars.previewState),
    onSuccess: (report) => {
      downloadBlob(report.blob, report.filename);
      setCommitOutcome({ phase: 'completed', report });
      onImported();
    },
    onError: (error) => {
      if (error instanceof MasterDataImportReportError) {
        if (error.status === 409) {
          // State conflict — file/master_data drifted since Preview. Clear
          // the stale preview entirely so Commit has nothing to read until
          // a genuinely fresh Preview lands.
          setPreview(null);
          setCommitOutcome({ phase: 'stateConflict', detail: error.message });
          return;
        }
        if (error.status === 422) {
          // Row errors found on the server's fresh re-check. If the backend
          // embedded its own re-computed preview, show it so the table
          // reflects exactly what still needs fixing; otherwise just clear
          // the stale one — either way Commit stays disabled until the user
          // Previews again.
          setPreview(error.preview ?? null);
          setCommitOutcome({ phase: 'blocked', detail: error.message });
          return;
        }
      }
      // Network error (status null) or an unexpected 500 — generic message,
      // modal never closes itself, outcome intentionally vague (never claim
      // nothing was written when the response is unconfirmed).
      const confirmed = error instanceof MasterDataImportReportError ? error.status !== null : true;
      setCommitOutcome({ phase: 'error', confirmed, detail: GENERIC_CONNECTION_ERROR });
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

  function retryAfterBlockedOrError() {
    if (file) runPreview(file);
  }

  const summary = preview?.summary ?? null;
  const canCommit =
    !!file && !!preview && !!summary && !!preview.previewState &&
    !previewM.isPending && summary.errorRows === 0 && summary.readyRows > 0 &&
    commitOutcome.phase !== 'completed' && !busy;

  const noChangesToImport = !!summary && summary.errorRows === 0 && summary.readyRows === 0;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-2 sm:p-4">
      <div className="flex max-h-[95vh] w-full max-w-3xl flex-col rounded-xl border border-border bg-card shadow-xl">
        <div className="flex items-center justify-between border-b border-border px-4 py-3 sm:px-6 sm:py-4">
          <h2 className="flex items-center gap-2 text-sm font-semibold sm:text-base">
            <FileSpreadsheet className="h-4 w-4 shrink-0 text-green-600" />
            <span className="truncate">นำเข้าชนิดพืชและพันธุ์ (Excel)</span>
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
            <p className="font-medium text-foreground">คอลัมน์ในไฟล์ (3 คอลัมน์):</p>
            <ul className="mt-1.5 list-disc space-y-1 pl-4">
              <li><span className="font-mono">crop</span> — ชนิดพืช จำเป็นทุกแถว พิมพ์ชนิดใหม่ได้</li>
              <li><span className="font-mono">variety</span> — พันธุ์/สายพันธุ์ ไม่บังคับ (เว้นว่าง = แถวนี้มีแค่ชนิดพืช)</li>
              <li><span className="font-mono">pCode</span> — รหัสสินค้าของพันธุ์ ไม่บังคับ (เว้นว่าง = คงค่าเดิม ไม่ลบ) · 1 พันธุ์มีได้ 1 P.Code</li>
              <li><span className="font-mono">varietyStatus</span> — &apos;เปิดใช้งาน&apos;/&apos;ปิดใช้งาน&apos; เว้นว่าง = เปิดใช้งาน</li>
            </ul>
            <p className="mt-2 rounded-md border border-amber-300 bg-amber-50 px-2 py-1.5 text-amber-800">
              ชนิดพืชสร้างใหม่ได้เท่านั้นผ่านไฟล์นี้ — เปิด/ปิดชนิดพืชทำได้ที่หน้า Master Data เท่านั้น
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

          {commitOutcome.phase === 'completed' && summary && (
            <div className="rounded-md border border-green-300 bg-green-50 px-3 py-3 text-sm text-green-800">
              <div className="flex items-start gap-2">
                <CheckCircle2 className="mt-0.5 h-4 w-4 shrink-0 text-green-600" />
                <div className="min-w-0">
                  <p className="font-medium">นำเข้าสำเร็จ</p>
                  <p className="mt-1 text-xs">
                    สร้างชนิดพืช {summary.cropsToCreate} · สร้างพันธุ์ {summary.varietiesToCreate} ·
                    เปิดใช้งานพันธุ์ {summary.varietiesToActivate} · ปิดใช้งานพันธุ์ {summary.varietiesToDeactivate} ·
                    สร้าง P.Code {summary.pCodesToCreate} · เปิดใช้งาน P.Code {summary.pCodesToActivate}
                  </p>
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

          {preview && summary && !committed && (
            <div className="space-y-2">
              <div>
                <p className="text-sm font-medium">{summary.errorRows === 0 ? 'ตรวจสอบผ่าน' : 'ตรวจสอบไม่ผ่าน'}</p>
                <p className="text-xs text-muted-foreground">ยังไม่มีข้อมูลถูกนำเข้า</p>
              </div>

              <div className="flex flex-wrap gap-x-3 gap-y-1 text-sm">
                <span className="text-muted-foreground">สร้างชนิดพืช {summary.cropsToCreate}</span>
                <span className="text-muted-foreground">สร้างพันธุ์ {summary.varietiesToCreate}</span>
                <span className="text-muted-foreground">เปิดใช้งานพันธุ์ {summary.varietiesToActivate}</span>
                <span className="text-muted-foreground">ปิดใช้งานพันธุ์ {summary.varietiesToDeactivate}</span>
                <span className="text-muted-foreground">สร้าง P.Code {summary.pCodesToCreate}</span>
                <span className="text-muted-foreground">เปิดใช้งาน P.Code {summary.pCodesToActivate}</span>
              </div>
              <div className="flex flex-wrap gap-3 text-sm">
                <span className="font-medium text-green-700">พร้อมนำเข้า {summary.readyRows}</span>
                <span className="text-muted-foreground">ข้าม {summary.skippedRows}</span>
                <span className={summary.errorRows > 0 ? 'font-medium text-destructive' : 'text-muted-foreground'}>
                  ผิดพลาด {summary.errorRows}
                </span>
              </div>

              <div className="flex flex-wrap gap-2">
                <button
                  type="button"
                  onClick={() => file && reportM.mutate(file)}
                  disabled={!file || reportM.isPending || busy}
                  className="inline-flex items-center gap-2 rounded-md border border-border bg-background px-3 py-1.5 text-xs font-medium shadow-sm hover:bg-secondary disabled:opacity-60"
                >
                  {reportM.isPending ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Download className="h-3.5 w-3.5" />}
                  ดาวน์โหลดผลตรวจสอบ
                </button>
              </div>

              {summary.errorRows > 0 && (
                <p className="rounded-md border border-amber-300 bg-amber-50 px-3 py-2 text-xs text-amber-800">
                  ต้องแก้ไขแถวที่ผิดพลาดทั้งหมดก่อนจึงจะยืนยันนำเข้าได้ — ระบบนำเข้าทั้งไฟล์
                  (all-or-nothing) จะไม่มีข้อมูลแถวใดถูกนำเข้าจนกว่าทุกแถวจะผ่าน
                </p>
              )}

              {noChangesToImport && (
                <p className="rounded-md border border-border bg-secondary/30 px-3 py-2 text-xs text-muted-foreground">
                  ไม่มีข้อมูลที่ต้องนำเข้า — ทุกแถวตรงกับข้อมูลในระบบอยู่แล้ว
                </p>
              )}

              <div className="max-h-72 overflow-x-auto overflow-y-auto rounded-md border border-border">
                <table className="min-w-full divide-y divide-border text-sm">
                  <thead className="sticky top-0 bg-muted/60">
                    <tr>
                      {['แถว Excel', 'ชนิดพืช', 'พันธุ์/สายพันธุ์', 'P.Code', 'สถานะพันธุ์', 'การดำเนินการ', 'ผลตรวจ', 'รายละเอียดข้อผิดพลาด'].map((h) => (
                        <th key={h} className="whitespace-nowrap px-3 py-2 text-left text-xs font-medium text-muted-foreground">{h}</th>
                      ))}
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-border">
                    {preview.rows.map((r) => {
                      const badge = rowBadge(r);
                      return (
                        <tr key={r.rowNumber} className={r.rowStatus === 'ERROR' ? 'bg-destructive/5' : ''}>
                          <td className="whitespace-nowrap px-3 py-2 text-muted-foreground">{r.rowNumber}</td>
                          <td className="whitespace-nowrap px-3 py-2">{r.crop ?? '—'}</td>
                          <td className="whitespace-nowrap px-3 py-2">{r.variety ?? '—'}</td>
                          <td className="whitespace-nowrap px-3 py-2">{r.pCode ?? '—'}</td>
                          <td className="whitespace-nowrap px-3 py-2 text-xs text-muted-foreground">{r.varietyStatus ?? '—'}</td>
                          <td className="whitespace-nowrap px-3 py-2">{actionLabel(r)}</td>
                          <td className="whitespace-nowrap px-3 py-2">
                            <span className={`inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium ${badge.className}`}>
                              {badge.label}
                            </span>
                          </td>
                          <td className="max-w-xs whitespace-normal break-words px-3 py-2 text-xs text-muted-foreground">
                            {r.errorMessage || '—'}
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

        {/* Commit-outcome failure banners live OUTSIDE the scrollable body,
            right above the footer, so a user who scrolled the preview table
            down still sees the result immediately. */}
        {(commitOutcome.phase === 'blocked' || commitOutcome.phase === 'stateConflict' || commitOutcome.phase === 'error') && (
          <div className="border-t border-border px-4 py-3 sm:px-6">
            {commitOutcome.phase === 'blocked' && (
              <div className="rounded-md border border-destructive/30 bg-destructive/10 px-3 py-3 text-sm text-destructive">
                <div className="flex items-start gap-2">
                  <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />
                  <div className="min-w-0">
                    <p className="font-medium break-words">{commitOutcome.detail}</p>
                    <p className="mt-0.5 text-xs font-medium">ไม่มีข้อมูลในไฟล์นี้ถูกนำเข้า — กรุณาตรวจสอบไฟล์อีกครั้ง</p>
                  </div>
                </div>
              </div>
            )}

            {commitOutcome.phase === 'stateConflict' && (
              <div className="rounded-md border border-amber-300 bg-amber-50 px-3 py-3 text-sm text-amber-800">
                <div className="flex items-start gap-2">
                  <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-amber-600" />
                  <div className="min-w-0">
                    <p className="font-medium break-words">{commitOutcome.detail}</p>
                    <p className="mt-0.5 text-xs font-medium">ไม่มีข้อมูลในไฟล์นี้ถูกนำเข้า</p>
                  </div>
                </div>
              </div>
            )}

            {commitOutcome.phase === 'error' && (
              <div className="rounded-md border border-destructive/30 bg-destructive/10 px-3 py-3 text-sm text-destructive">
                <div className="flex items-start gap-2">
                  <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />
                  <div className="min-w-0">
                    <p className="font-medium">{commitOutcome.detail}</p>
                    <p className="mt-0.5 text-xs font-medium">
                      {commitOutcome.confirmed
                        ? 'ไม่มีข้อมูลในไฟล์นี้ถูกนำเข้า'
                        : 'ไม่สามารถยืนยันผลการนำเข้าได้ กรุณาตรวจสอบข้อมูล Master Data ก่อนลองอีกครั้ง'}
                    </p>
                  </div>
                </div>
              </div>
            )}

            <div className="mt-3 flex flex-wrap gap-2">
              <button
                type="button"
                onClick={retryAfterBlockedOrError}
                disabled={!file || previewM.isPending}
                className="inline-flex items-center gap-2 rounded-md border border-border bg-white px-3 py-1.5 text-xs font-medium text-foreground shadow-sm hover:bg-secondary disabled:opacity-60"
              >
                {previewM.isPending ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Upload className="h-3.5 w-3.5" />}
                ตรวจสอบไฟล์อีกครั้ง
              </button>
            </div>
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
              onClick={() => file && preview?.previewState && commitM.mutate({
                file, previewState: preview.previewState,
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
