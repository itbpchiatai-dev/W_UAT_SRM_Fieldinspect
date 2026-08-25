/**
 * Master Data crop/variety import API client (round 8-15B). Mirrors
 * api/plots.import.test.ts's pattern for the shared shape (multipart file,
 * responseType blob, Content-Disposition filename, JSON-error-blob
 * decoding, 409/422/network normalization) — independent test file for an
 * independent API module.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';

const postMock = vi.fn();
const getMock = vi.fn();
vi.mock('./client', () => ({
  apiClient: { post: (...a: unknown[]) => postMock(...a), get: (...a: unknown[]) => getMock(...a) },
}));

import {
  downloadCropVarietyImportTemplate,
  previewCropVarietyImport,
  downloadCropVarietyImportPreviewReport,
  commitCropVarietyImportWithReport,
  MasterDataImportReportError,
  type CropVarietyImportPreviewState,
} from './masterdata';

beforeEach(() => {
  postMock.mockReset();
  getMock.mockReset();
});

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

function jsonErrorBlob(detail: unknown) {
  return new Blob([JSON.stringify({ detail })], { type: 'application/json' });
}

function previewState(overrides: Partial<CropVarietyImportPreviewState> = {}): CropVarietyImportPreviewState {
  return {
    fileSha256: 'a'.repeat(64),
    rows: [{
      rowNumber: 3, crop: 'พริก', variety: 'พริกขี้หนู', varietyStatus: true,
      action: 'create_crop_and_variety', cropExisted: false, cropWasActive: null,
      varietyExisted: false, varietyWasActive: null, varietyParentAtPreview: null,
      pCode: null, pCodeAction: 'none', pCodeExisted: false, pCodeWasActive: null,
      pCodeParentAtPreview: null, varietyActivePCodeAtPreview: null,
    }],
    ...overrides,
  };
}

// --- Template -------------------------------------------------------------

describe('downloadCropVarietyImportTemplate', () => {
  it('GETs the template endpoint with responseType blob', async () => {
    getMock.mockResolvedValue({
      status: 200,
      headers: { 'content-type': XLSX_MIME, 'content-disposition': 'attachment; filename="crop-variety-import-template.xlsx"' },
      data: new Blob(['x'], { type: XLSX_MIME }),
    });

    const result = await downloadCropVarietyImportTemplate();

    expect(getMock).toHaveBeenCalledWith(
      '/api/v1/masterdata/crop-variety-import/template',
      expect.objectContaining({ responseType: 'blob' }),
    );
    expect(result.filename).toBe('crop-variety-import-template.xlsx');
  });

  it('falls back to a safe filename when Content-Disposition is missing', async () => {
    getMock.mockResolvedValue({
      status: 200, headers: { 'content-type': XLSX_MIME }, data: new Blob(['x'], { type: XLSX_MIME }),
    });

    const result = await downloadCropVarietyImportTemplate();

    expect(result.filename).toBe('crop-variety-import-template.xlsx');
  });

  it('a JSON error body (never a workbook) is decoded and thrown as MasterDataImportReportError', async () => {
    getMock.mockRejectedValueOnce(axiosError(403, jsonErrorBlob('ไม่มีสิทธิ์'), { 'content-type': 'application/json' }));

    await expect(downloadCropVarietyImportTemplate()).rejects.toMatchObject({
      message: 'ไม่มีสิทธิ์', status: 403,
    });
  });

  it('network error (no response) → MasterDataImportReportError with status null', async () => {
    getMock.mockRejectedValueOnce(Object.assign(new Error('Network Error'), { isAxiosError: true }));

    let caught: unknown;
    try { await downloadCropVarietyImportTemplate(); } catch (e) { caught = e; }

    expect(caught).toBeInstanceOf(MasterDataImportReportError);
    expect((caught as MasterDataImportReportError).status).toBeNull();
  });
});

// --- Preview (JSON) --------------------------------------------------------

describe('previewCropVarietyImport', () => {
  it('posts FormData("file") to the preview endpoint with no manual headers', async () => {
    postMock.mockResolvedValue({
      data: { summary: { totalRows: 0, readyRows: 0, skippedRows: 0, errorRows: 0, cropsToCreate: 0, varietiesToCreate: 0, varietiesToActivate: 0, varietiesToDeactivate: 0 }, rows: [] },
    });
    const file = xlsxFile();

    await previewCropVarietyImport(file);

    expect(postMock).toHaveBeenCalledTimes(1);
    const call = postMock.mock.calls[0];
    expect(call[0]).toBe('/api/v1/masterdata/crop-variety-import/preview');
    expect(call[1]).toBeInstanceOf(FormData);
    expect((call[1] as FormData).get('file')).toBe(file);
    // no third (config) arg → axios sets the multipart boundary itself
    expect(call.length).toBe(2);
  });

  it('returns the parsed JSON preview response verbatim', async () => {
    const summary = { totalRows: 1, readyRows: 1, skippedRows: 0, errorRows: 0, cropsToCreate: 1, varietiesToCreate: 1, varietiesToActivate: 0, varietiesToDeactivate: 0 };
    postMock.mockResolvedValue({ data: { summary, rows: [], previewState: previewState() } });

    const result = await previewCropVarietyImport(xlsxFile());

    expect(result.summary).toEqual(summary);
    expect(result.previewState).toEqual(previewState());
  });
});

// --- Preview Report (blob) -------------------------------------------------

describe('downloadCropVarietyImportPreviewReport', () => {
  it('posts multipart to preview-report with responseType blob, no manual Content-Type', async () => {
    const blob = new Blob(['xlsx-bytes'], { type: XLSX_MIME });
    postMock.mockResolvedValue({
      status: 200,
      headers: { 'content-type': XLSX_MIME, 'content-disposition': 'attachment; filename="crop-variety-import-validation-20260101-000000.xlsx"' },
      data: blob,
    });
    const file = xlsxFile();

    const report = await downloadCropVarietyImportPreviewReport(file);

    const call = postMock.mock.calls[0];
    expect(call[0]).toBe('/api/v1/masterdata/crop-variety-import/preview-report');
    expect((call[1] as FormData).get('file')).toBe(file);
    expect(call[2]).toMatchObject({ responseType: 'blob' });
    expect(call[2]).not.toHaveProperty('headers');
    expect(report.blob).toBe(blob);
    expect(report.kind).toBe('validation');
    expect(report.httpStatus).toBe(200);
    expect(report.filename).toBe('crop-variety-import-validation-20260101-000000.xlsx');
  });

  it('a JSON error body (never a workbook) is decoded and thrown as MasterDataImportReportError', async () => {
    postMock.mockRejectedValueOnce(axiosError(422, jsonErrorBlob('รูปแบบไฟล์ไม่ถูกต้อง'), { 'content-type': 'application/json' }));

    await expect(downloadCropVarietyImportPreviewReport(xlsxFile())).rejects.toMatchObject({
      message: 'รูปแบบไฟล์ไม่ถูกต้อง', status: 422,
    });
  });
});

// --- Commit Report (blob, THE mutation) ------------------------------------

describe('commitCropVarietyImportWithReport', () => {
  it('posts to commit-report (never a JSON /commit endpoint — none is exported by this module)', async () => {
    postMock.mockResolvedValue({
      status: 200, headers: { 'content-type': XLSX_MIME }, data: new Blob(['x'], { type: XLSX_MIME }),
    });

    await commitCropVarietyImportWithReport(xlsxFile(), previewState());

    expect(postMock.mock.calls[0][0]).toBe('/api/v1/masterdata/crop-variety-import/commit-report');
    // The plain JSON commit endpoint is simply not something this module
    // exposes — nothing in this file's public API can reach /commit at all.
    expect(postMock.mock.calls.every((c) => c[0] !== '/api/v1/masterdata/crop-variety-import/commit')).toBe(true);
  });

  it('appends the previewState JSON as a multipart field named exactly "previewState"', async () => {
    postMock.mockResolvedValue({
      status: 200, headers: { 'content-type': XLSX_MIME }, data: new Blob(['x'], { type: XLSX_MIME }),
    });
    const state = previewState();

    await commitCropVarietyImportWithReport(xlsxFile(), state);

    const form = postMock.mock.calls[0][1] as FormData;
    expect(form.get('file')).toBeInstanceOf(File);
    expect(JSON.parse(form.get('previewState') as string)).toEqual(state);
    expect(form.has('preview_state')).toBe(false);
  });

  it('does not set a manual Content-Type header (browser sets the multipart boundary)', async () => {
    postMock.mockResolvedValue({
      status: 200, headers: { 'content-type': XLSX_MIME }, data: new Blob(['x'], { type: XLSX_MIME }),
    });

    await commitCropVarietyImportWithReport(xlsxFile(), previewState());

    const config = postMock.mock.calls[0][2];
    expect(config).not.toHaveProperty('headers');
    expect(config.responseType).toBe('blob');
  });

  it('HTTP 200 xlsx → kind completed, filename from Content-Disposition', async () => {
    const blob = new Blob(['xlsx-bytes'], { type: XLSX_MIME });
    postMock.mockResolvedValue({
      status: 200,
      headers: { 'content-type': XLSX_MIME, 'content-disposition': 'attachment; filename="crop-variety-import-result-20260101-000000.xlsx"' },
      data: blob,
    });

    const report = await commitCropVarietyImportWithReport(xlsxFile(), previewState());

    expect(report.kind).toBe('completed');
    expect(report.filename).toBe('crop-variety-import-result-20260101-000000.xlsx');
    expect(report.httpStatus).toBe(200);
  });

  it('HTTP 422 (row errors, JSON body — never a workbook for this backend) → MasterDataImportReportError with the embedded preview', async () => {
    const embeddedPreview = {
      summary: { totalRows: 1, readyRows: 0, skippedRows: 0, errorRows: 1, cropsToCreate: 0, varietiesToCreate: 0, varietiesToActivate: 0, varietiesToDeactivate: 0 },
      rows: [{ rowNumber: 3, crop: 'พริก', variety: null, varietyStatus: null, rowStatus: 'ERROR', action: 'none', errorMessage: 'ต้องระบุ crop' }],
    };
    postMock.mockRejectedValueOnce(axiosError(422, jsonErrorBlob({ message: 'พบข้อผิดพลาดในบางแถว', preview: embeddedPreview }), { 'content-type': 'application/json' }));

    let caught: unknown;
    try { await commitCropVarietyImportWithReport(xlsxFile(), previewState()); } catch (e) { caught = e; }

    expect(caught).toBeInstanceOf(MasterDataImportReportError);
    expect((caught as MasterDataImportReportError).status).toBe(422);
    expect((caught as MasterDataImportReportError).message).toBe('พบข้อผิดพลาดในบางแถว');
    expect((caught as MasterDataImportReportError).preview).toEqual(embeddedPreview);
  });

  it('HTTP 409 conflict (JSON body) → MasterDataImportReportError with status 409 and the backend message verbatim', async () => {
    postMock.mockRejectedValueOnce(axiosError(409, jsonErrorBlob('ข้อมูล Master Data มีการเปลี่ยนแปลง กรุณาตรวจสอบไฟล์อีกครั้งก่อนนำเข้า'), { 'content-type': 'application/json' }));

    let caught: unknown;
    try { await commitCropVarietyImportWithReport(xlsxFile(), previewState()); } catch (e) { caught = e; }

    expect(caught).toBeInstanceOf(MasterDataImportReportError);
    expect((caught as MasterDataImportReportError).status).toBe(409);
    expect((caught as MasterDataImportReportError).message).toBe('ข้อมูล Master Data มีการเปลี่ยนแปลง กรุณาตรวจสอบไฟล์อีกครั้งก่อนนำเข้า');
    expect((caught as MasterDataImportReportError).preview).toBeNull();
  });

  it('no response at all (network error/timeout) → MasterDataImportReportError with status null (outcome unconfirmed)', async () => {
    postMock.mockRejectedValueOnce(Object.assign(new Error('timeout of 15000ms exceeded'), { isAxiosError: true }));

    let caught: unknown;
    try { await commitCropVarietyImportWithReport(xlsxFile(), previewState()); } catch (e) { caught = e; }

    expect(caught).toBeInstanceOf(MasterDataImportReportError);
    expect((caught as MasterDataImportReportError).status).toBeNull();
  });

  it('a non-axios throw still normalizes to a MasterDataImportReportError with status null', async () => {
    postMock.mockRejectedValueOnce(new Error('boom'));

    let caught: unknown;
    try { await commitCropVarietyImportWithReport(xlsxFile(), previewState()); } catch (e) { caught = e; }

    expect(caught).toBeInstanceOf(MasterDataImportReportError);
    expect((caught as MasterDataImportReportError).status).toBeNull();
  });
});
