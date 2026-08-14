/**
 * Master Data API — editable dropdown option source (Step 12.5).
 *
 * Round 8-15B adds the crop/variety Excel import client (backend rounds
 * 8-15A/8-15A.1) — Template/Preview/Preview-Report/Commit-Report. Mirrors
 * the SHAPE of api/plots.ts's Plot Import helpers (postForXlsxOrThrow,
 * blob/Content-Disposition handling, PlotImportReportError) but is its own
 * independent copy, not a shared import — the two importers' backends are
 * unrelated and neither this file nor PlotImportModal/api/plots.ts is
 * touched by the other's round.
 *
 * IMPORTANT asymmetry from Plot Import: this backend's preview-report and
 * commit-report endpoints return an .xlsx ONLY on HTTP 200 — a 422 (row
 * errors / bad file) or 409 (state conflict) is ALWAYS a plain JSON error
 * body here, never a "blocked" workbook (unlike Plot Import's commit-report,
 * which renders a downloadable ERROR-status workbook even for a 422). So
 * every non-200 response from postForXlsxOrThrow below is normalized into a
 * thrown MasterDataImportReportError — there is no "blocked report" success
 * path to return.
 */
import axios from 'axios';
import { apiClient } from './client';

export interface MasterDataItem {
  id: string;
  type: string;
  value: string;
  parent: string | null;
  orderIndex: number;
  active: boolean;
  createdAt: string;
  updatedAt: string;
}

export interface MasterDataListParams {
  type?: string;
  parent?: string;
  activeOnly?: boolean;
}

export interface MasterDataCreatePayload {
  type: string;
  value: string;
  parent?: string | null;
  orderIndex?: number;
}

export type MasterDataUpdatePayload = Partial<{
  value: string;
  parent: string | null;
  orderIndex: number;
  active: boolean;
}>;

export const listMasterData = (params: MasterDataListParams = {}) =>
  apiClient.get<MasterDataItem[]>('/api/v1/masterdata', { params }).then((r) => r.data);

export const createMasterData = (payload: MasterDataCreatePayload) =>
  apiClient.post<MasterDataItem>('/api/v1/masterdata', payload).then((r) => r.data);

export const updateMasterData = (id: string, payload: MasterDataUpdatePayload) =>
  apiClient.patch<MasterDataItem>(`/api/v1/masterdata/${id}`, payload).then((r) => r.data);

export const deleteMasterData = (id: string) =>
  apiClient.delete(`/api/v1/masterdata/${id}`).then(() => undefined);

// --- Crop/Variety Excel import (round 8-15B; backend rounds 8-15A/8-15A.1) --
// Template → Preview (read-only, JSON, for the on-screen table) →
// Commit-Report (the ONE mutation — never call the JSON /commit endpoint
// from the UI). Preview-Report is an optional read-only .xlsx download of
// the same validation Preview already shows on screen.

export type CropVarietyImportAction =
  | 'create_crop'
  | 'create_variety'
  | 'create_crop_and_variety'
  | 'activate_variety'
  | 'deactivate_variety'
  | 'none';

export interface CropVarietyImportRowResult {
  rowNumber: number;
  crop: string | null;
  variety: string | null;
  // Raw Thai cell text ("เปิดใช้งาน"/"ปิดใช้งาน"/null) — never a boolean; see
  // CropVarietyImportPreviewStateRow.varietyStatus for the normalized flag.
  varietyStatus: string | null;
  rowStatus: 'READY' | 'SKIPPED' | 'ERROR';
  action: CropVarietyImportAction;
  errorMessage: string;
}

export interface CropVarietyImportSummary {
  totalRows: number;
  readyRows: number;
  skippedRows: number;
  errorRows: number;
  cropsToCreate: number;
  varietiesToCreate: number;
  varietiesToActivate: number;
  varietiesToDeactivate: number;
}

/** One row's normalized plan + the live master_data snapshot it was
 * resolved against, as returned by Preview — opaque to this app beyond
 * being echoed back verbatim on Commit. Never construct or edit this
 * client-side. */
export interface CropVarietyImportPreviewStateRow {
  rowNumber: number;
  crop: string;
  variety: string | null;
  varietyStatus: boolean | null;
  action: CropVarietyImportAction;
  cropExisted: boolean;
  cropWasActive: boolean | null;
  varietyExisted: boolean;
  varietyWasActive: boolean | null;
  varietyParentAtPreview: string | null;
}

/** Read-only optimistic-concurrency expectation from Preview — echoed back
 * verbatim as the `previewState` multipart field on Commit. NOT a
 * credential: masterdata.create + masterdata.update are still required
 * regardless, and the backend always re-derives the real plan from a fresh
 * parse + fresh DB query. */
export interface CropVarietyImportPreviewState {
  fileSha256: string;
  rows: CropVarietyImportPreviewStateRow[];
}

export interface CropVarietyImportPreview {
  summary: CropVarietyImportSummary;
  rows: CropVarietyImportRowResult[];
  // Present on the read-only Preview response; absent on the row-errors
  // preview embedded in a Commit's 422 error body (see
  // MasterDataImportReportError.preview).
  previewState?: CropVarietyImportPreviewState | null;
}

export interface CropVarietyImportReportFile {
  blob: Blob;
  filename: string;
  kind: 'validation' | 'completed';
  httpStatus: number;
}

/**
 * Thrown by downloadCropVarietyImportTemplate/downloadCropVarietyImportPreviewReport/
 * commitCropVarietyImportWithReport whenever the response is NOT a workbook
 * — always the case for this backend's 422/409 (see this file's module
 * docstring) and for a network failure.
 *
 * `status`: the HTTP status if a response was received; the backend's
 * transaction has already rolled back by then (every error path in
 * masterdata.py's crop-variety-import router raises BEFORE any write).
 * `status === null` means NO response arrived (network error/timeout) — the
 * commit's outcome is NOT confirmed in that case; the caller must not claim
 * nothing was written.
 *
 * `preview`: present only for a Commit's 422 "has row errors" response —
 * the backend's fresh re-validation result, embedded exactly like the JSON
 * /commit endpoint's own 422 body would carry it, so the UI can refresh its
 * row table even though this call went through commit-report.
 */
export class MasterDataImportReportError extends Error {
  status: number | null;
  preview: CropVarietyImportPreview | null;
  constructor(message: string, status: number | null, preview: CropVarietyImportPreview | null = null) {
    super(message);
    this.name = 'MasterDataImportReportError';
    this.status = status;
    this.preview = preview;
  }
}

function isXlsxContentType(contentType: unknown): boolean {
  return typeof contentType === 'string' && contentType.includes('spreadsheetml');
}

/** Blob.text() is standard in every real browser; jsdom's Blob (this
 * project's vitest environment) doesn't implement it, so fall back to
 * FileReader (which jsdom does support) rather than relying on a test-only
 * polyfill for production code. Same helper as api/plots.ts's own
 * (independent copy — see this file's module docstring). */
async function blobToText(blob: Blob): Promise<string> {
  if (typeof blob.text === 'function') return blob.text();
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result ?? ''));
    reader.onerror = () => reject(reader.error);
    reader.readAsText(blob);
  });
}

async function extractErrorDetailFromBlob(
  blob: Blob,
): Promise<{ message: string; preview: CropVarietyImportPreview | null }> {
  try {
    const text = await blobToText(blob);
    const data: unknown = JSON.parse(text);
    if (data && typeof data === 'object' && 'detail' in data) {
      const detail = (data as { detail: unknown }).detail;
      if (typeof detail === 'string') return { message: detail, preview: null };
      if (detail && typeof detail === 'object') {
        const obj = detail as { message?: unknown; preview?: unknown };
        const message = typeof obj.message === 'string' ? obj.message : text;
        const preview = obj.preview && typeof obj.preview === 'object'
          ? (obj.preview as CropVarietyImportPreview)
          : null;
        return { message, preview };
      }
    }
    return { message: text || 'เกิดข้อผิดพลาด', preview: null };
  } catch {
    return { message: 'เกิดข้อผิดพลาด', preview: null };
  }
}

/** Strip path separators/control chars and require a plausible .xlsx name —
 * defense-in-depth even though the backend always generates a safe,
 * server-side filename (never derived from the client's upload name). */
function sanitizeDownloadFilename(name: string): string | null {
  const cleaned = name.replace(/[/\\:*?"<>|]/g, '').trim();
  if (!cleaned || !cleaned.toLowerCase().endsWith('.xlsx')) return null;
  return cleaned.slice(0, 200);
}

function filenameFromContentDisposition(headerValue: unknown, fallback: string): string {
  if (typeof headerValue === 'string') {
    const match = /filename="?([^";]+)"?/i.exec(headerValue);
    const safe = match?.[1] ? sanitizeDownloadFilename(match[1]) : null;
    if (safe) return safe;
  }
  return fallback;
}

/** Read-only: GET the template as a blob. Never writes — see
 * services/master_data_crop_variety_import.build_template's contract
 * (backend). Filename comes from the response's Content-Disposition, same
 * as every other download in this module. */
export async function downloadCropVarietyImportTemplate(): Promise<{ blob: Blob; filename: string }> {
  try {
    const res = await apiClient.get<Blob>('/api/v1/masterdata/crop-variety-import/template', {
      responseType: 'blob',
    });
    if (!isXlsxContentType(res.headers['content-type'])) {
      const { message } = await extractErrorDetailFromBlob(res.data);
      throw new MasterDataImportReportError(message, res.status);
    }
    return {
      blob: res.data,
      filename: filenameFromContentDisposition(res.headers['content-disposition'], 'crop-variety-import-template.xlsx'),
    };
  } catch (error) {
    if (error instanceof MasterDataImportReportError) throw error;
    if (axios.isAxiosError(error) && error.response) {
      const blob = error.response.data as Blob;
      const { message } = await extractErrorDetailFromBlob(blob);
      throw new MasterDataImportReportError(message, error.response.status);
    }
    // No response received at all — network error/timeout.
    throw new MasterDataImportReportError(
      error instanceof Error ? error.message : 'เชื่อมต่อระบบไม่สำเร็จ กรุณาลองใหม่อีกครั้ง',
      null,
    );
  }
}

/** Read-only: parse + validate the file, WITHOUT writing anything. Plain
 * JSON endpoint (not a blob) — errors propagate as raw axios errors; the
 * caller (the import modal) is responsible for reading `error.response.data
 * .detail` (always a plain string for this endpoint's 422 — see
 * services/master_data_crop_variety_import.CropVarietyImportFileError). */
export async function previewCropVarietyImport(file: File): Promise<CropVarietyImportPreview> {
  const form = new FormData();
  form.append('file', file);
  // Let the browser set the multipart boundary — never set Content-Type here.
  const res = await apiClient.post<CropVarietyImportPreview>(
    '/api/v1/masterdata/crop-variety-import/preview', form,
  );
  return res.data;
}

/** Shared low-level POST: sends the file (+ optional extra multipart
 * fields) as multipart/form-data, expects an .xlsx blob back on HTTP 200.
 * Any other outcome — a JSON error body at any status, or no response at
 * all (network/timeout) — is normalized into a thrown
 * MasterDataImportReportError. Never lets a JSON error blob be mistaken for
 * a workbook. */
async function postForXlsxOrThrow(
  url: string,
  file: File,
  extraFields?: Record<string, string>,
): Promise<{ blob: Blob; httpStatus: number; headers: Record<string, unknown> }> {
  const form = new FormData();
  form.append('file', file);
  if (extraFields) {
    for (const [key, value] of Object.entries(extraFields)) form.append(key, value);
  }
  let res;
  try {
    // Default validateStatus (2xx only) — this backend's 422/409 are always
    // plain JSON errors, never a workbook (see module docstring), so there
    // is no "accept 422 as success" case to configure here.
    res = await apiClient.post<Blob>(url, form, { responseType: 'blob' });
  } catch (error) {
    if (axios.isAxiosError(error) && error.response) {
      const blob = error.response.data as Blob;
      const { message, preview } = await extractErrorDetailFromBlob(blob);
      throw new MasterDataImportReportError(message, error.response.status, preview);
    }
    // No response received at all — network error/timeout. Outcome unknown.
    throw new MasterDataImportReportError(
      error instanceof Error ? error.message : 'เชื่อมต่อระบบไม่สำเร็จ กรุณาลองใหม่อีกครั้ง',
      null,
    );
  }
  const headers = res.headers as Record<string, unknown>;
  if (!isXlsxContentType(headers['content-type'])) {
    const { message, preview } = await extractErrorDetailFromBlob(res.data);
    throw new MasterDataImportReportError(message, res.status, preview);
  }
  return { blob: res.data, httpStatus: res.status, headers };
}

/** Read-only: same core as previewCropVarietyImport, returned as an .xlsx
 * validation workbook (READY/SKIPPED/ERROR per row) instead of JSON. Never
 * writes. */
export async function downloadCropVarietyImportPreviewReport(file: File): Promise<CropVarietyImportReportFile> {
  const { blob, httpStatus, headers } = await postForXlsxOrThrow(
    '/api/v1/masterdata/crop-variety-import/preview-report', file,
  );
  return {
    blob,
    filename: filenameFromContentDisposition(headers['content-disposition'], 'crop-variety-import-validation.xlsx'),
    kind: 'validation',
    httpStatus,
  };
}

/**
 * THE commit mutation — re-validates server-side (never trusts the
 * client's previewState) and executes every row in ONE transaction, then
 * returns an .xlsx completed-result workbook instead of JSON. Call this
 * EXACTLY once per confirm click — never call the plain JSON /commit
 * endpoint (not exposed by this module at all this round) either before or
 * after this one for the same file.
 *
 * `previewState` — the exact object from the last successful
 * previewCropVarietyImport() response, echoed back verbatim so the backend
 * can detect drift (file changed, a row changed, or master_data changed
 * underneath) and reject with 409 before writing anything.
 *
 * Always resolves with kind: 'completed' (HTTP 200) — see this module's
 * docstring: unlike Plot Import, this backend never returns a "blocked"
 * workbook for a 422; any row-error/conflict outcome throws
 * MasterDataImportReportError instead.
 */
export async function commitCropVarietyImportWithReport(
  file: File,
  previewState: CropVarietyImportPreviewState,
): Promise<CropVarietyImportReportFile> {
  const { blob, httpStatus, headers } = await postForXlsxOrThrow(
    '/api/v1/masterdata/crop-variety-import/commit-report', file,
    { previewState: JSON.stringify(previewState) },
  );
  return {
    blob,
    filename: filenameFromContentDisposition(headers['content-disposition'], 'crop-variety-import-result.xlsx'),
    kind: 'completed',
    httpStatus,
  };
}
