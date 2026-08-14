/**
 * Plot import API client (round 7.5) — the preview/commit calls must POST a
 * multipart FormData with a single "file" field to the right endpoint, and
 * must NOT set Content-Type manually (the browser adds the boundary).
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';

const postMock = vi.fn();
vi.mock('./client', () => ({
  apiClient: { post: (...a: unknown[]) => postMock(...a), get: vi.fn() },
}));

import {
  previewPlotImport, commitPlotImport,
  downloadPlotImportPreviewReport, commitPlotImportWithReport,
  PlotImportReportError, PREVIEW_STATE_CONFLICT_CODE,
} from './plots';

beforeEach(() => postMock.mockReset());

function xlsxFile() {
  return new File([new Uint8Array([1, 2, 3])], 'import.xlsx', {
    type: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
  });
}

const XLSX_MIME = 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet';

function axiosError(status: number, data: unknown, headers: Record<string, string> = {}) {
  return Object.assign(new Error('Request failed'), {
    isAxiosError: true,
    response: { status, data, headers },
  });
}

function jsonErrorBlob(detail: string) {
  return new Blob([JSON.stringify({ detail })], { type: 'application/json' });
}

describe('previewPlotImport', () => {
  it('posts FormData("file") to the preview endpoint with no manual headers', async () => {
    postMock.mockResolvedValue({ data: { totalRows: 0, validRows: 0, errorRows: 0, rows: [] } });
    const file = xlsxFile();

    await previewPlotImport(file);

    expect(postMock).toHaveBeenCalledTimes(1);
    const call = postMock.mock.calls[0];
    expect(call[0]).toBe('/api/v1/plots/import/preview');
    expect(call[1]).toBeInstanceOf(FormData);
    expect((call[1] as FormData).get('file')).toBe(file);
    // no third (config) arg → axios sets the multipart boundary itself
    expect(call.length).toBe(2);
  });
});

describe('commitPlotImport', () => {
  it('posts FormData("file") to the commit endpoint', async () => {
    postMock.mockResolvedValue({
      data: { createdPlots: 1, startedCycles: 0, updatedCycles: 0, skippedRows: 0, rowResults: [] },
    });
    const file = xlsxFile();

    const result = await commitPlotImport(file);

    const call = postMock.mock.calls[0];
    expect(call[0]).toBe('/api/v1/plots/import/commit');
    expect((call[1] as FormData).get('file')).toBe(file);
    expect(result.createdPlots).toBe(1);
  });
});

// --- round 8-2.5: result-workbook endpoints --------------------------------

describe('downloadPlotImportPreviewReport', () => {
  it('posts multipart to preview-report with responseType blob, no manual Content-Type', async () => {
    const blob = new Blob(['xlsx-bytes'], { type: XLSX_MIME });
    postMock.mockResolvedValue({
      status: 200,
      headers: { 'content-type': XLSX_MIME, 'content-disposition': 'attachment; filename="plot-import-validation-20260101-000000.xlsx"' },
      data: blob,
    });
    const file = xlsxFile();

    const report = await downloadPlotImportPreviewReport(file);

    const call = postMock.mock.calls[0];
    expect(call[0]).toBe('/api/v1/plots/import/preview-report');
    expect((call[1] as FormData).get('file')).toBe(file);
    expect(call[2]).toMatchObject({ responseType: 'blob' });
    expect(call[2]).not.toHaveProperty('headers'); // browser sets the multipart boundary itself
    expect(report.blob).toBe(blob);
    expect(report.kind).toBe('validation');
    expect(report.httpStatus).toBe(200);
  });

  it('extracts the filename from Content-Disposition', async () => {
    postMock.mockResolvedValue({
      status: 200,
      headers: { 'content-type': XLSX_MIME, 'content-disposition': 'attachment; filename="plot-import-validation-20260101-000000.xlsx"' },
      data: new Blob(['x'], { type: XLSX_MIME }),
    });

    const report = await downloadPlotImportPreviewReport(xlsxFile());

    expect(report.filename).toBe('plot-import-validation-20260101-000000.xlsx');
  });

  it('falls back to a safe filename when Content-Disposition is missing', async () => {
    postMock.mockResolvedValue({
      status: 200,
      headers: { 'content-type': XLSX_MIME },
      data: new Blob(['x'], { type: XLSX_MIME }),
    });

    const report = await downloadPlotImportPreviewReport(xlsxFile());

    expect(report.filename).toBe('plot-import-validation.xlsx');
  });

  it('a JSON error blob (never a workbook) is decoded and thrown as PlotImportReportError', async () => {
    postMock.mockRejectedValueOnce(axiosError(422, jsonErrorBlob('ไม่พบคอลัมน์ที่ต้องการ'), { 'content-type': 'application/json' }));

    await expect(downloadPlotImportPreviewReport(xlsxFile())).rejects.toMatchObject({
      message: 'ไม่พบคอลัมน์ที่ต้องการ',
      status: 422,
    });
  });
});

describe('commitPlotImportWithReport', () => {
  it('HTTP 200 xlsx → outcome completed', async () => {
    const blob = new Blob(['xlsx-bytes'], { type: XLSX_MIME });
    postMock.mockResolvedValue({
      status: 200,
      headers: { 'content-type': XLSX_MIME, 'content-disposition': 'attachment; filename="plot-import-result-20260101-000000.xlsx"' },
      data: blob,
    });

    const res = await commitPlotImportWithReport(xlsxFile());

    expect(res.outcome).toBe('completed');
    expect(res.report.kind).toBe('completed');
    expect(res.report.filename).toBe('plot-import-result-20260101-000000.xlsx');
    expect(res.report.httpStatus).toBe(200);
  });

  it('posts to commit-report (never the legacy /import/commit endpoint)', async () => {
    postMock.mockResolvedValue({
      status: 200,
      headers: { 'content-type': XLSX_MIME },
      data: new Blob(['x'], { type: XLSX_MIME }),
    });

    await commitPlotImportWithReport(xlsxFile());

    expect(postMock.mock.calls[0][0]).toBe('/api/v1/plots/import/commit-report');
  });

  it('HTTP 422 xlsx (validation blocked) → outcome blocked, not an error', async () => {
    const blob = new Blob(['xlsx-bytes'], { type: XLSX_MIME });
    postMock.mockResolvedValue({
      status: 422,
      headers: { 'content-type': XLSX_MIME },
      data: blob,
    });

    const res = await commitPlotImportWithReport(xlsxFile());

    expect(res.outcome).toBe('blocked');
    expect(res.report.kind).toBe('validation');
    expect(res.report.httpStatus).toBe(422);
    expect(res.report.filename).toBe('plot-import-validation.xlsx');
  });

  it('HTTP 422 JSON body (file-level error, not a workbook) → normalized PlotImportReportError with status 422', async () => {
    postMock.mockResolvedValue({
      status: 422,
      headers: { 'content-type': 'application/json' },
      data: jsonErrorBlob('ไฟล์ใหญ่เกินไป'),
    });

    await expect(commitPlotImportWithReport(xlsxFile())).rejects.toMatchObject({
      message: 'ไฟล์ใหญ่เกินไป',
      status: 422,
    });
  });

  it('HTTP 409 conflict (JSON blob) → normalized PlotImportReportError with status 409', async () => {
    postMock.mockRejectedValueOnce(axiosError(409, jsonErrorBlob('มีการเปลี่ยนแปลงที่ขัดแย้ง'), { 'content-type': 'application/json' }));

    let caught: unknown;
    try {
      await commitPlotImportWithReport(xlsxFile());
    } catch (e) {
      caught = e;
    }
    expect(caught).toBeInstanceOf(PlotImportReportError);
    expect((caught as PlotImportReportError).status).toBe(409);
    expect((caught as PlotImportReportError).message).toBe('มีการเปลี่ยนแปลงที่ขัดแย้ง');
  });

  it('no response at all (network error/timeout) → PlotImportReportError with status null', async () => {
    postMock.mockRejectedValueOnce(Object.assign(new Error('timeout of 15000ms exceeded'), { isAxiosError: true }));

    let caught: unknown;
    try {
      await commitPlotImportWithReport(xlsxFile());
    } catch (e) {
      caught = e;
    }
    expect(caught).toBeInstanceOf(PlotImportReportError);
    expect((caught as PlotImportReportError).status).toBeNull();
  });

  it('does not set a manual Content-Type header (browser sets the multipart boundary)', async () => {
    postMock.mockResolvedValue({
      status: 200,
      headers: { 'content-type': XLSX_MIME },
      data: new Blob(['x'], { type: XLSX_MIME }),
    });

    await commitPlotImportWithReport(xlsxFile());

    const config = postMock.mock.calls[0][2];
    expect(config).not.toHaveProperty('headers');
    expect(config.responseType).toBe('blob');
  });

  // --- round 8-2.7.2: previewState field + structured conflict code ---------

  it('appends the previewState JSON as a multipart field when provided', async () => {
    postMock.mockResolvedValue({
      status: 200,
      headers: { 'content-type': XLSX_MIME },
      data: new Blob(['x'], { type: XLSX_MIME }),
    });
    const previewState = {
      fileSha256: 'a'.repeat(64),
      startNextRows: [{
        rowNumber: 3, supplierCode: 'SUP010', plotCode: 'P010',
        resolvedAction: 'start_new_cycle', activeCycleId: null,
      }],
    };

    await commitPlotImportWithReport(xlsxFile(), previewState);

    const form = postMock.mock.calls[0][1] as FormData;
    expect(form.get('file')).toBeInstanceOf(File);
    expect(JSON.parse(form.get('previewState') as string)).toEqual(previewState);
    // Round 8-6F — the multipart field must be exactly "previewState"
    // (camelCase); the backend's Form(alias="previewState") binds to that
    // name specifically, so a stray snake_case field would silently be
    // dropped server-side exactly like the original round 8-6F bug.
    expect(form.has('preview_state')).toBe(false);
  });

  it('omits the previewState field entirely when not provided (legacy compatible)', async () => {
    postMock.mockResolvedValue({
      status: 200,
      headers: { 'content-type': XLSX_MIME },
      data: new Blob(['x'], { type: XLSX_MIME }),
    });

    await commitPlotImportWithReport(xlsxFile());

    const form = postMock.mock.calls[0][1] as FormData;
    expect(form.has('previewState')).toBe(false);
  });

  it('a 409 preview_state_conflict (structured detail) → PlotImportReportError carrying the code, reason, and changedRows (round 8-6E item 9)', async () => {
    const conflictBlob = new Blob([JSON.stringify({
      detail: {
        code: 'preview_state_conflict', reason: 'resolution_changed',
        message: 'สถานะรอบปลูกมีการเปลี่ยนแปลง กรุณาตรวจสอบไฟล์อีกครั้งก่อนนำเข้า',
        changedRows: [3],
      },
    })], { type: 'application/json' });
    postMock.mockRejectedValueOnce(axiosError(409, conflictBlob, { 'content-type': 'application/json' }));

    let caught: unknown;
    try {
      await commitPlotImportWithReport(xlsxFile());
    } catch (e) {
      caught = e;
    }
    expect(caught).toBeInstanceOf(PlotImportReportError);
    expect((caught as PlotImportReportError).status).toBe(409);
    expect((caught as PlotImportReportError).code).toBe(PREVIEW_STATE_CONFLICT_CODE);
    expect((caught as PlotImportReportError).message).toBe(
      'สถานะรอบปลูกมีการเปลี่ยนแปลง กรุณาตรวจสอบไฟล์อีกครั้งก่อนนำเข้า',
    );
    expect((caught as PlotImportReportError).reason).toBe('resolution_changed');
    expect((caught as PlotImportReportError).changedRows).toEqual([3]);
  });

  it('multiple changedRows entries are all kept, in order (round 8-6E)', async () => {
    const conflictBlob = new Blob([JSON.stringify({
      detail: {
        code: 'preview_state_conflict', reason: 'resolution_changed',
        message: 'สถานะรอบปลูกมีการเปลี่ยนแปลง', changedRows: [3, 5, 8],
      },
    })], { type: 'application/json' });
    postMock.mockRejectedValueOnce(axiosError(409, conflictBlob, { 'content-type': 'application/json' }));

    let caught: unknown;
    try {
      await commitPlotImportWithReport(xlsxFile());
    } catch (e) {
      caught = e;
    }
    expect((caught as PlotImportReportError).changedRows).toEqual([3, 5, 8]);
  });

  it('a missing_preview_state conflict has reason set and an empty changedRows (round 8-6E)', async () => {
    const conflictBlob = new Blob([JSON.stringify({
      detail: {
        code: 'preview_state_conflict', reason: 'missing_preview_state',
        message: 'กรุณาตรวจสอบไฟล์ด้วย Preview ก่อนยืนยันนำเข้า',
      },
    })], { type: 'application/json' });
    postMock.mockRejectedValueOnce(axiosError(409, conflictBlob, { 'content-type': 'application/json' }));

    let caught: unknown;
    try {
      await commitPlotImportWithReport(xlsxFile());
    } catch (e) {
      caught = e;
    }
    expect((caught as PlotImportReportError).reason).toBe('missing_preview_state');
    expect((caught as PlotImportReportError).changedRows).toEqual([]);
  });

  it('non-number entries in changedRows are filtered out, never a crash (round 8-6E)', async () => {
    const conflictBlob = new Blob([JSON.stringify({
      detail: {
        code: 'preview_state_conflict', reason: 'resolution_changed',
        message: 'สถานะรอบปลูกมีการเปลี่ยนแปลง', changedRows: [3, 'x', null, 5],
      },
    })], { type: 'application/json' });
    postMock.mockRejectedValueOnce(axiosError(409, conflictBlob, { 'content-type': 'application/json' }));

    let caught: unknown;
    try {
      await commitPlotImportWithReport(xlsxFile());
    } catch (e) {
      caught = e;
    }
    expect((caught as PlotImportReportError).changedRows).toEqual([3, 5]);
  });

  it('a conflict with no reason/changedRows at all still parses (backward compatible)', async () => {
    postMock.mockRejectedValueOnce(axiosError(409, jsonErrorBlob('มีการเปลี่ยนแปลงที่ขัดแย้ง'), { 'content-type': 'application/json' }));

    let caught: unknown;
    try {
      await commitPlotImportWithReport(xlsxFile());
    } catch (e) {
      caught = e;
    }
    expect((caught as PlotImportReportError).reason).toBeNull();
    expect((caught as PlotImportReportError).changedRows).toEqual([]);
  });
});
