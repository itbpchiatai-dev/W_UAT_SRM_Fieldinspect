/**
 * Suppliers API — list / get / create / update / deactivate.
 *
 * Round 8-20B adds the Supplier Excel import client (backend round 8-20A) —
 * Template / Preview / Preview-Report / Commit-Report. See the section at the
 * bottom of this file.
 */
import axios from 'axios';
import { apiClient } from './client';

export interface SupplierSummary {
  id: string;
  code: string;
  name: string;
  isActive: boolean;
  contactName: string | null;
  contactEmail: string | null;
}

export interface SupplierDetail {
  id: string;
  code: string;
  name: string;
  taxId: string | null;
  contactName: string | null;
  contactEmail: string | null;
  contactPhone: string | null;
  address: string | null;
  isActive: boolean;
  createdAt: string;
  updatedAt: string;
}

export interface SupplierListParams {
  limit?: number;
  offset?: number;
  q?: string;
  activeOnly?: boolean;
}

export interface SupplierCreatePayload {
  code: string;
  name: string;
  taxId?: string | null;
  contactName?: string | null;
  contactEmail?: string | null;
  contactPhone?: string | null;
  address?: string | null;
}

export interface SupplierUpdatePayload {
  name?: string;
  taxId?: string | null;
  contactName?: string | null;
  contactEmail?: string | null;
  contactPhone?: string | null;
  address?: string | null;
  isActive?: boolean;
}

export async function listSuppliers(params: SupplierListParams = {}): Promise<SupplierSummary[]> {
  const res = await apiClient.get<SupplierSummary[]>('/api/v1/suppliers', { params });
  return res.data;
}

/** Round 8-20D — three-way status filter for the Suppliers page. */
export type SupplierStatusFilter = 'active' | 'inactive' | 'all';

export interface SupplierSearchParams {
  /** Same semantics GET /suppliers?q= has always had: partial,
   * case-insensitive, matched against supplier code OR name. Unchanged. */
  q?: string;
  contactName?: string;
  /** MUST be 4-10 ASCII digits when present (the caller validates before
   * calling; the backend re-checks by hand and answers with one generic
   * message). Travels in the POST BODY only — never a query parameter, so it
   * cannot land in a URL, browser history, or the backend's access log. */
  contactPhoneDigits?: string;
  status?: SupplierStatusFilter;
  limit?: number;
  offset?: number;
}

/**
 * Round 8-20D — the Suppliers page's filter row. POST-and-body-only because
 * ONE of its filters (contactPhoneDigits) is PII; every filter is ANDed by
 * the backend. Same suppliers.read permission and Supplier scope as
 * GET /suppliers, which is deliberately left in place for every other caller.
 *
 * Response is the identical SupplierSummary shape, so the page renders a
 * filtered result with the same table.
 */
export async function searchSuppliers(
  params: SupplierSearchParams = {},
): Promise<SupplierSummary[]> {
  const res = await apiClient.post<SupplierSummary[]>('/api/v1/suppliers/search', {
    q: params.q,
    contactName: params.contactName,
    contactPhoneDigits: params.contactPhoneDigits,
    status: params.status,
    limit: params.limit,
    offset: params.offset,
  });
  return res.data;
}

export async function getSupplier(id: string): Promise<SupplierDetail> {
  const res = await apiClient.get<SupplierDetail>(`/api/v1/suppliers/${id}`);
  return res.data;
}

export async function createSupplier(payload: SupplierCreatePayload): Promise<SupplierDetail> {
  const res = await apiClient.post<SupplierDetail>('/api/v1/suppliers', payload);
  return res.data;
}

export async function updateSupplier(id: string, payload: SupplierUpdatePayload): Promise<SupplierDetail> {
  const res = await apiClient.patch<SupplierDetail>(`/api/v1/suppliers/${id}`, payload);
  return res.data;
}

export async function deactivateSupplier(id: string): Promise<SupplierDetail> {
  const res = await apiClient.post<SupplierDetail>(`/api/v1/suppliers/${id}/deactivate`, {});
  return res.data;
}

// --- Supplier Excel import (round 8-20B; backend round 8-20A) --------------
// Template → Preview (read-only, JSON, for the on-screen table) →
// Commit-Report (the ONE mutation — the plain JSON /import/commit endpoint is
// deliberately NOT exposed by this module, so the UI cannot call it by
// mistake and double-write). Preview-Report is an optional read-only .xlsx
// download of the same validation Preview already shows on screen.
//
// Mirrors the SHAPE of api/masterdata.ts's crop/variety import helpers
// (postForXlsxOrThrow, blob/Content-Disposition handling, a typed report
// error) but is its own independent copy, exactly as that module is an
// independent copy of api/plots.ts's — the three importers' backends are
// unrelated and none of their rounds touches the others.
//
// Same asymmetry as Master Data Import: this backend's report endpoints
// return an .xlsx ONLY on HTTP 200; a 422 (row errors / bad file) or 409
// (state conflict) is ALWAYS a plain JSON error body, never a "blocked"
// workbook. So every non-200 becomes a thrown SupplierImportReportError.

/** create → a supplierCode not yet in the system; update → an existing one;
 * no_change → the row matches what is already stored, so it writes nothing. */
export type SupplierImportOperation = 'create' | 'update' | 'no_change';

export interface SupplierImportRowResult {
  rowNumber: number;
  action: string | null;
  supplierCode: string | null;
  supplierName: string | null;
  taxId: string | null;
  contactName: string | null;
  contactEmail: string | null;
  contactPhone: string | null;
  address: string | null;
  status: string | null;
  rowStatus: 'READY' | 'ERROR';
  operation: SupplierImportOperation | string;
  errorMessage: string;
  /** Non-blocking notice (today: the deactivation reassurance). Never an
   * error — a row with a warning is still READY. */
  warningMessage: string;
}

export interface SupplierImportSummary {
  totalRows: number;
  readyRows: number;
  errorRows: number;
  suppliersToCreate: number;
  suppliersToUpdate: number;
  suppliersToActivate: number;
  suppliersToDeactivate: number;
  unchangedRows: number;
}

export interface SupplierImportPreviewStateRow {
  rowNumber: number;
  supplierCode: string;
  operation: SupplierImportOperation | string;
  supplierExisted: boolean;
  supplierWasActive: boolean | null;
  existingStateDigest: string | null;
}

/**
 * Opaque optimistic-concurrency token from Preview, echoed back verbatim as
 * the `previewState` multipart field on Commit. NOT a credential and NOT an
 * authorization grant — the backend re-derives the whole plan from a fresh
 * parse + fresh DB query every time; this only lets it detect that the file,
 * the row set, or a Supplier changed since the user approved the preview.
 */
export interface SupplierImportPreviewState {
  fileSha256: string;
  rows: SupplierImportPreviewStateRow[];
}

export interface SupplierImportPreview {
  summary: SupplierImportSummary;
  rows: SupplierImportRowResult[];
  /** Present on the read-only preview endpoint; absent on the error-preview
   * embedded in a commit's 422. */
  previewState?: SupplierImportPreviewState | null;
}

export interface SupplierImportProcessedRow {
  rowNumber: number;
  supplierCode: string | null;
  status: string;
  message: string;
}

export interface SupplierImportCommitResult {
  totalRows: number;
  createdSuppliers: number;
  updatedSuppliers: number;
  activatedSuppliers: number;
  deactivatedSuppliers: number;
  unchangedRows: number;
  errorRows: number;
  processedRows: SupplierImportProcessedRow[];
}

export interface SupplierImportReportFile {
  blob: Blob;
  filename: string;
  kind: 'validation' | 'completed';
  httpStatus: number;
}

/**
 * Thrown by every blob-returning helper below whenever the response is NOT a
 * workbook — always the case for this backend's 422/409, and for a network
 * failure.
 *
 * `status`: the HTTP status if a response arrived; the backend's transaction
 * has already rolled back by then (every error path raises BEFORE any write).
 * `status === null` means NO response arrived (network error/timeout) — the
 * commit's outcome is NOT confirmed, and the caller must not claim nothing
 * was written.
 *
 * `preview`: present only for a Commit's 422 "has row errors" response — the
 * backend's fresh re-validation result, so the UI can refresh its row table
 * even though the call went through commit-report.
 */
export class SupplierImportReportError extends Error {
  status: number | null;
  preview: SupplierImportPreview | null;
  constructor(
    message: string,
    status: number | null,
    preview: SupplierImportPreview | null = null,
  ) {
    super(message);
    this.name = 'SupplierImportReportError';
    this.status = status;
    this.preview = preview;
  }
}

function isXlsxContentType(contentType: unknown): boolean {
  return typeof contentType === 'string' && contentType.includes('spreadsheetml');
}

/** Blob.text() is standard in every real browser; jsdom's Blob (this
 * project's vitest environment) doesn't implement it, so fall back to
 * FileReader rather than relying on a test-only polyfill in production code.
 * Independent copy of api/masterdata.ts's own — see the section header. */
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
): Promise<{ message: string; preview: SupplierImportPreview | null }> {
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
          ? (obj.preview as SupplierImportPreview)
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

/** Read-only: GET the pre-filled template as a blob. Never writes. */
export async function downloadSupplierImportTemplate(): Promise<{ blob: Blob; filename: string }> {
  try {
    const res = await apiClient.get<Blob>('/api/v1/suppliers/import/template', {
      responseType: 'blob',
    });
    if (!isXlsxContentType(res.headers['content-type'])) {
      const { message } = await extractErrorDetailFromBlob(res.data);
      throw new SupplierImportReportError(message, res.status);
    }
    return {
      blob: res.data,
      filename: filenameFromContentDisposition(
        res.headers['content-disposition'], 'supplier-import-template.xlsx',
      ),
    };
  } catch (error) {
    if (error instanceof SupplierImportReportError) throw error;
    if (axios.isAxiosError(error) && error.response) {
      const blob = error.response.data as Blob;
      const { message } = await extractErrorDetailFromBlob(blob);
      throw new SupplierImportReportError(message, error.response.status);
    }
    throw new SupplierImportReportError(
      error instanceof Error ? error.message : 'เชื่อมต่อระบบไม่สำเร็จ กรุณาลองใหม่อีกครั้ง',
      null,
    );
  }
}

/** Read-only: parse + validate the file WITHOUT writing anything. Plain JSON
 * endpoint (not a blob) — errors propagate as raw axios errors; the caller
 * reads `error.response.data.detail` (a flat string for this endpoint's
 * file-level errors). */
export async function previewSupplierImport(file: File): Promise<SupplierImportPreview> {
  const form = new FormData();
  form.append('file', file);
  // Let the browser set the multipart boundary — never set Content-Type here.
  const res = await apiClient.post<SupplierImportPreview>(
    '/api/v1/suppliers/import/preview', form,
  );
  return res.data;
}

/** Shared low-level POST: sends the file (+ optional extra multipart fields)
 * as multipart/form-data and expects an .xlsx blob back on HTTP 200. Any
 * other outcome — a JSON error body at any status, or no response at all —
 * is normalized into a thrown SupplierImportReportError, so a JSON error blob
 * can never be mistaken for a workbook. */
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
    res = await apiClient.post<Blob>(url, form, { responseType: 'blob' });
  } catch (error) {
    if (axios.isAxiosError(error) && error.response) {
      const blob = error.response.data as Blob;
      const { message, preview } = await extractErrorDetailFromBlob(blob);
      throw new SupplierImportReportError(message, error.response.status, preview);
    }
    // No response at all — network error/timeout. Outcome unknown.
    throw new SupplierImportReportError(
      error instanceof Error ? error.message : 'เชื่อมต่อระบบไม่สำเร็จ กรุณาลองใหม่อีกครั้ง',
      null,
    );
  }
  const headers = res.headers as Record<string, unknown>;
  if (!isXlsxContentType(headers['content-type'])) {
    const { message, preview } = await extractErrorDetailFromBlob(res.data);
    throw new SupplierImportReportError(message, res.status, preview);
  }
  return { blob: res.data, httpStatus: res.status, headers };
}

/** Read-only: same core as previewSupplierImport, returned as an .xlsx
 * validation workbook (READY/ERROR per row) instead of JSON. Never writes. */
export async function downloadSupplierPreviewReport(file: File): Promise<SupplierImportReportFile> {
  const { blob, httpStatus, headers } = await postForXlsxOrThrow(
    '/api/v1/suppliers/import/preview-report', file,
  );
  return {
    blob,
    filename: filenameFromContentDisposition(
      headers['content-disposition'], 'supplier-import-validation.xlsx',
    ),
    kind: 'validation',
    httpStatus,
  };
}

/**
 * THE commit mutation — re-validates server-side (never trusting the client's
 * previewState) and executes every row in ONE transaction, then returns an
 * .xlsx completed-result workbook. Call EXACTLY once per confirm click.
 *
 * `previewState` — the exact object from the last successful
 * previewSupplierImport() response, echoed back verbatim (JSON.stringify of
 * the whole object, as the `previewState` multipart field the backend's
 * Form(alias="previewState") expects) so the backend can detect drift and
 * reject with 409 before writing anything.
 */
export async function commitSupplierImport(
  file: File,
  previewState: SupplierImportPreviewState,
): Promise<SupplierImportReportFile> {
  const { blob, httpStatus, headers } = await postForXlsxOrThrow(
    '/api/v1/suppliers/import/commit-report', file,
    { previewState: JSON.stringify(previewState) },
  );
  return {
    blob,
    filename: filenameFromContentDisposition(
      headers['content-disposition'], 'supplier-import-result.xlsx',
    ),
    kind: 'completed',
    httpStatus,
  };
}

/** Alias kept for symmetry with downloadSupplierPreviewReport — the commit
 * result workbook IS what commitSupplierImport returns, so re-downloading it
 * never re-runs the mutation (the caller keeps the Blob and calls
 * downloadBlob again). */
export const downloadSupplierCommitReport = commitSupplierImport;
