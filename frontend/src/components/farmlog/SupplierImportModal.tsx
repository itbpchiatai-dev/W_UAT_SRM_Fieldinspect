/**
 * Supplier Excel import modal (round 8-20B; backend round 8-20A).
 *
 * Flow: download template → pick .xlsx → Preview (read-only JSON, for the
 * on-screen table) → optionally download a validation report (.xlsx,
 * read-only) → "ยืนยันนำเข้า" commits through commit-report (ONE mutation,
 * ONE request — the plain JSON /import/commit endpoint is not exposed by
 * api/suppliers.ts at all, so it cannot be called from here even by mistake)
 * — on success the Completed workbook downloads automatically and Commit
 * locks permanently for this modal instance.
 *
 * Structure follows MasterDataCropVarietyImportModal (round 8-15B) rather
 * than reinventing the flow: same commit-outcome state machine, same
 * "a fresh Preview is the only thing that re-enables Commit" rule, same
 * blob/report handling. The differences are this importer's own: a 9-column
 * contract, create/update/no_change operations, and two standing warnings
 * (blank-clears and deactivation).
 *
 * commit-report is the ONLY endpoint here that ever mutates data.
 */
import { useState } from 'react';
import axios from 'axios';
import { useMutation } from '@tanstack/react-query';
import {
  AlertTriangle, CheckCircle2, Download, FileSpreadsheet, Info, Loader2, Upload,
} from 'lucide-react';
import {
  commitSupplierImport,
  downloadSupplierImportTemplate,
  downloadSupplierPreviewReport,
  previewSupplierImport,
  SupplierImportReportError,
  type SupplierImportOperation,
  type SupplierImportPreview,
  type SupplierImportReportFile,
  type SupplierImportRowResult,
} from '../../api/suppliers';
import { downloadBlob } from '../../lib/downloadBlob';

const OPERATION_LABEL: Record<SupplierImportOperation, string> = {
  create: 'สร้างใหม่',
  update: 'แก้ไข',
  no_change: 'ไม่เปลี่ยนแปลง',
};

function operationLabel(operation: string): string {
  return OPERATION_LABEL[operation as SupplierImportOperation] ?? operation;
}

function rowBadge(row: SupplierImportRowResult): { label: string; className: string } {
  if (row.rowStatus === 'ERROR') {
    return { label: 'ผิดพลาด', className: 'bg-destructive/15 text-destructive' };
  }
  return { label: 'พร้อมนำเข้า', className: 'bg-success/15 text-success-readable' };
}

const GENERIC_CONNECTION_ERROR = 'เชื่อมต่อระบบไม่สำเร็จ กรุณาลองใหม่อีกครั้ง';

/** Shown on every preview, not only when a row happens to clear something —
 * it is a property of the file format itself, so the user must know it
 * BEFORE deciding whether the preview looks right. */
const BLANK_CLEARS_WARNING =
  'ช่องข้อมูลเสริมที่เว้นว่างใน Excel จะล้างค่าเดิมของ Supplier';

/** Mirrors the backend's own DEACTIVATE_WARNING text (services/
 * supplier_import.py). Shown once at the summary level whenever the file
 * contains at least one deactivation, in addition to the per-row warning the
 * backend attaches. */
const DEACTIVATE_WARNING =
  'Supplier จะถูกปิดใช้งาน แต่ข้อมูลแปลง รอบปลูก และประวัติการตรวจจะไม่ถูกลบ';

/** Reads a plain-JSON preview error's `detail` (a flat string for this
 * endpoint's file-level errors) into a Thai-friendly message. Never surfaces
 * raw JSON. */
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

// Commit outcome — drives which banner/buttons the footer + body show.
// 'idle' is the normal preview-driven flow; the others are terminal per file
// selection ('completed' permanently locks Commit for this modal instance).
type CommitOutcome =
  | { phase: 'idle' }
  | { phase: 'completed'; report: SupplierImportReportFile; summary: CommittedSummary }
  | { phase: 'blocked'; detail: string }
  | { phase: 'stateConflict'; detail: string }
  | { phase: 'error'; confirmed: boolean; detail: string };

/** The counts shown after a successful commit. Taken from the PREVIEW the
 * user approved (the commit-report endpoint returns a workbook, not JSON),
 * which is exactly what the backend just executed — the same file and the
 * same previewState it verified before writing. */
interface CommittedSummary {
  created: number;
  updated: number;
  activated: number;
  deactivated: number;
  unchanged: number;
}

export function SupplierImportModal({
  onClose, onImported,
}: {
  onClose: () => void;
  onImported: () => void;
}) {
  const [file, setFile] = useState<File | null>(null);
  const [preview, setPreview] = useState<SupplierImportPreview | null>(null);
  const [previewErrorMsg, setPreviewErrorMsg] = useState<string | null>(null);
  const [reportErrorMsg, setReportErrorMsg] = useState<string | null>(null);
  const [commitOutcome, setCommitOutcome] = useState<CommitOutcome>({ phase: 'idle' });

  const committed = commitOutcome.phase === 'completed';

  const templateM = useMutation({
    mutationFn: () => downloadSupplierImportTemplate(),
    onSuccess: ({ blob, filename }) => downloadBlob(blob, filename),
  });

  const previewM = useMutation({
    mutationFn: (f: File) => previewSupplierImport(f),
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
  // the OLD preview/previewState up front — Commit has nothing valid to read
  // until the new preview's onSuccess actually lands.
  function runPreview(f: File) {
    setPreview(null);
    setPreviewErrorMsg(null);
    previewM.mutate(f);
  }

  const reportM = useMutation({
    mutationFn: (f: File) => downloadSupplierPreviewReport(f),
    onSuccess: (report) => {
      downloadBlob(report.blob, report.filename);
      setReportErrorMsg(null);
    },
    onError: (error) => {
      setReportErrorMsg(
        error instanceof SupplierImportReportError ? error.message : 'ดาวน์โหลดรายงานไม่สำเร็จ',
      );
    },
  });

  const commitM = useMutation({
    // file/previewState/summary are explicit mutation VARIABLES (the
    // render-time snapshot at the moment Commit was clicked), never read via
    // closure — avoids a stale previewState from before a re-preview.
    mutationFn: (vars: {
      file: File;
      previewState: NonNullable<SupplierImportPreview['previewState']>;
      summary: CommittedSummary;
    }) => commitSupplierImport(vars.file, vars.previewState),
    onSuccess: (report, vars) => {
      downloadBlob(report.blob, report.filename);
      setCommitOutcome({ phase: 'completed', report, summary: vars.summary });
      onImported();
    },
    onError: (error) => {
      if (error instanceof SupplierImportReportError) {
        if (error.status === 409) {
          // State conflict — the file or a Supplier drifted since Preview.
          // Clear the stale preview entirely so Commit has nothing to read
          // until a genuinely fresh Preview lands (never retry with the old
          // previewState).
          setPreview(null);
          setCommitOutcome({ phase: 'stateConflict', detail: error.message });
          return;
        }
        if (error.status === 422) {
          // Row errors on the server's fresh re-check. If the backend
          // embedded its re-computed preview, show it so the table reflects
          // exactly what still needs fixing; otherwise clear the stale one.
          // Either way Commit stays disabled until the user Previews again.
          setPreview(error.preview ?? null);
          setCommitOutcome({ phase: 'blocked', detail: error.message });
          return;
        }
      }
      // Network error (status null) or an unexpected 500 — generic message,
      // modal never closes itself, outcome intentionally vague (never claim
      // nothing was written when the response is unconfirmed).
      const confirmed = error instanceof SupplierImportReportError ? error.status !== null : true;
      setCommitOutcome({ phase: 'error', confirmed, detail: GENERIC_CONNECTION_ERROR });
    },
  });

  const busy = commitM.isPending;

  function onFileChange(e: React.ChangeEvent<HTMLInputElement>) {
    // A new file invalidates everything derived from the old one — preview,
    // previewState, report errors, and any commit outcome.
    setFile(e.target.files?.[0] ?? null);
    setPreview(null);
    setPreviewErrorMsg(null);
    setReportErrorMsg(null);
    setCommitOutcome({ phase: 'idle' });
  }

  function handleClose() {
    if (busy) return; // never orphan a pending commit-report request
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

  const nothingToImport =
    !!summary && summary.errorRows === 0
    && summary.suppliersToCreate === 0 && summary.suppliersToUpdate === 0;

  const hasDeactivation = !!summary && summary.suppliersToDeactivate > 0;

  function handleCommit() {
    if (!file || !preview?.previewState || !summary) return;
    commitM.mutate({
      file,
      previewState: preview.previewState,
      summary: {
        created: summary.suppliersToCreate,
        updated: summary.suppliersToUpdate,
        activated: summary.suppliersToActivate,
        deactivated: summary.suppliersToDeactivate,
        unchanged: summary.unchangedRows,
      },
    });
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-2 sm:p-4">
      <div className="flex max-h-[95vh] w-full max-w-4xl flex-col rounded-xl border border-border bg-card shadow-xl">
        <div className="flex items-center justify-between border-b border-border px-4 py-3 sm:px-6 sm:py-4">
          <h2 className="flex items-center gap-2 text-sm font-semibold sm:text-base">
            <FileSpreadsheet className="h-4 w-4 shrink-0 text-green-600" />
            <span className="truncate">นำเข้า Supplier (Excel)</span>
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
            <p className="font-medium text-foreground">คอลัมน์ในไฟล์ (9 คอลัมน์):</p>
            <ul className="mt-1.5 list-disc space-y-1 pl-4">
              <li><span className="font-mono">action</span> — ใส่ <span className="font-mono">save_supplier</span> ทุกแถว (ไม่มีคำสั่งลบ)</li>
              <li><span className="font-mono">supplierCode</span> — จำเป็น และห้ามเปลี่ยน (รหัสเดิม = แก้ไข, รหัสใหม่ = สร้างใหม่)</li>
              <li><span className="font-mono">supplierName</span> — จำเป็น</li>
              <li><span className="font-mono">taxId / contactName / contactEmail / contactPhone / address</span> — ไม่บังคับ</li>
              <li><span className="font-mono">status</span> — <span className="font-mono">active</span> หรือ <span className="font-mono">inactive</span> เท่านั้น</li>
            </ul>
            {/* Standing warning — a property of the file format, shown before
                the user has even picked a file. */}
            <p className="mt-2 flex items-start gap-1.5 rounded-md border border-amber-300 bg-amber-50 px-2 py-1.5 text-amber-800">
              <Info className="mt-0.5 h-3.5 w-3.5 shrink-0" />
              <span>{BLANK_CLEARS_WARNING}</span>
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
              ดาวน์โหลด Template
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

          {file && (
            <p className="text-xs text-muted-foreground">
              ไฟล์ที่เลือก: <span className="font-medium text-foreground break-all">{file.name}</span>
            </p>
          )}

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
                  <p className="mt-1 text-xs">
                    สร้างใหม่ {commitOutcome.summary.created} ·
                    แก้ไข {commitOutcome.summary.updated} ·
                    เปิดใช้งาน {commitOutcome.summary.activated} ·
                    ปิดใช้งาน {commitOutcome.summary.deactivated} ·
                    ไม่เปลี่ยนแปลง {commitOutcome.summary.unchanged}
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
                <span className="text-muted-foreground">สร้างใหม่ {summary.suppliersToCreate}</span>
                <span className="text-muted-foreground">แก้ไข {summary.suppliersToUpdate}</span>
                <span className="text-muted-foreground">เปิดใช้งาน {summary.suppliersToActivate}</span>
                <span className="text-muted-foreground">ปิดใช้งาน {summary.suppliersToDeactivate}</span>
                <span className="text-muted-foreground">ไม่เปลี่ยนแปลง {summary.unchangedRows}</span>
              </div>
              <div className="flex flex-wrap gap-3 text-sm">
                <span className="font-medium text-green-700">พร้อมนำเข้า {summary.readyRows}</span>
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

              {hasDeactivation && (
                <p className="flex items-start gap-1.5 rounded-md border border-amber-300 bg-amber-50 px-3 py-2 text-xs text-amber-800">
                  <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0 text-amber-600" />
                  <span>{DEACTIVATE_WARNING}</span>
                </p>
              )}

              {summary.errorRows > 0 && (
                <p className="rounded-md border border-destructive/30 bg-destructive/10 px-3 py-2 text-xs font-medium text-destructive">
                  ไม่มีข้อมูลในไฟล์นี้ถูกนำเข้า — ต้องแก้ไขแถวที่ผิดพลาดทั้งหมดก่อนจึงจะยืนยันนำเข้าได้
                  (ระบบนำเข้าทั้งไฟล์ all-or-nothing)
                </p>
              )}

              {nothingToImport && (
                <p className="rounded-md border border-border bg-secondary/30 px-3 py-2 text-xs text-muted-foreground">
                  ไม่มีข้อมูลที่ต้องนำเข้า — ทุกแถวตรงกับข้อมูลในระบบอยู่แล้ว
                </p>
              )}

              <div className="max-h-72 overflow-x-auto overflow-y-auto rounded-md border border-border">
                <table className="min-w-full divide-y divide-border text-sm">
                  <thead className="sticky top-0 bg-muted/60">
                    <tr>
                      {['แถว Excel', 'รหัส Supplier', 'ชื่อ Supplier', 'การดำเนินการ', 'ผลตรวจ', 'ข้อความ'].map((h) => (
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
                          <td className="whitespace-nowrap px-3 py-2 font-mono text-xs">{r.supplierCode ?? '—'}</td>
                          <td className="px-3 py-2">{r.supplierName ?? '—'}</td>
                          <td className="whitespace-nowrap px-3 py-2">{operationLabel(r.operation)}</td>
                          <td className="whitespace-nowrap px-3 py-2">
                            <span className={`inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium ${badge.className}`}>
                              {badge.label}
                            </span>
                          </td>
                          <td className="max-w-xs whitespace-normal break-words px-3 py-2 text-xs text-muted-foreground">
                            {r.errorMessage || r.warningMessage || '—'}
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
                    <p className="mt-0.5 text-xs font-medium">
                      ไฟล์หรือข้อมูล Supplier มีการเปลี่ยนแปลงหลังการตรวจสอบ —
                      ไม่มีข้อมูลในไฟล์นี้ถูกนำเข้า กรุณาตรวจสอบไฟล์ใหม่อีกครั้งก่อนยืนยัน
                    </p>
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
                        : 'ไม่สามารถยืนยันผลการนำเข้าได้ กรุณาตรวจสอบข้อมูล Supplier ก่อนลองอีกครั้ง'}
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
              onClick={handleCommit}
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
