/**
 * Supplier import API client (round 8-20B; backend round 8-20A). Mirrors
 * api/masterdata.import.test.ts's pattern for the shared shape (multipart
 * file, responseType blob, Content-Disposition filename, JSON-error-blob
 * decoding, 409/422/network normalization) — an independent test file for an
 * independent API module.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';

const postMock = vi.fn();
const getMock = vi.fn();
vi.mock('./client', () => ({
  apiClient: { post: (...a: unknown[]) => postMock(...a), get: (...a: unknown[]) => getMock(...a) },
}));

import {
  commitSupplierImport,
  downloadSupplierCommitReport,
  downloadSupplierImportTemplate,
  downloadSupplierPreviewReport,
  previewSupplierImport,
  SupplierImportReportError,
  type SupplierImportPreviewState,
} from './suppliers';

beforeEach(() => {
  postMock.mockReset();
  getMock.mockReset();
});

const XLSX_MIME = 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet';

function xlsxFile(name = 'suppliers.xlsx') {
  return new File([new Uint8Array([1, 2, 3])], name, { type: XLSX_MIME });
}

function axiosError(status: number, data: unknown, headers: Record<string, string> = {}) {
  return Object.assign(new Error('Request failed'), {
    isAxiosError: true,
    response: { status, data, headers },
  });
}

function jsonErrorBlob(detail: unknown) {
  return new Blob([JSON.stringify({ detail })], { type: 'application/json' });
}

function previewState(overrides: Partial<SupplierImportPreviewState> = {}): SupplierImportPreviewState {
  return {
    fileSha256: 'a'.repeat(64),
    rows: [{
      rowNumber: 3,
      supplierCode: 'SUP001',
      operation: 'update',
      supplierExisted: true,
      supplierWasActive: true,
      existingStateDigest: 'b'.repeat(64),
    }],
    ...overrides,
  };
}

function formFields(form: FormData): Record<string, unknown> {
  const out: Record<string, unknown> = {};
  form.forEach((value, key) => { out[key] = value; });
  return out;
}

// --- Template ---------------------------------------------------------------

describe('downloadSupplierImportTemplate', () => {
  it('GETs the template endpoint as a blob', async () => {
    getMock.mockResolvedValue({
      data: new Blob(['x'], { type: XLSX_MIME }),
      status: 200,
      headers: { 'content-type': XLSX_MIME },
    });

    await downloadSupplierImportTemplate();

    expect(getMock).toHaveBeenCalledWith(
      '/api/v1/suppliers/import/template', { responseType: 'blob' },
    );
  });

  it('takes the filename from Content-Disposition', async () => {
    getMock.mockResolvedValue({
      data: new Blob(['x'], { type: XLSX_MIME }),
      status: 200,
      headers: {
        'content-type': XLSX_MIME,
        'content-disposition': 'attachment; filename="supplier-import-template.xlsx"',
      },
    });

    const { filename } = await downloadSupplierImportTemplate();
    expect(filename).toBe('supplier-import-template.xlsx');
  });

  it('falls back to the server-contract filename when the header is missing', async () => {
    getMock.mockResolvedValue({
      data: new Blob(['x'], { type: XLSX_MIME }),
      status: 200,
      headers: { 'content-type': XLSX_MIME },
    });

    const { filename } = await downloadSupplierImportTemplate();
    expect(filename).toBe('supplier-import-template.xlsx');
  });

  it('rejects a path-traversing filename from the header', async () => {
    getMock.mockResolvedValue({
      data: new Blob(['x'], { type: XLSX_MIME }),
      status: 200,
      headers: {
        'content-type': XLSX_MIME,
        'content-disposition': 'attachment; filename="../../etc/passwd.xlsx"',
      },
    });

    const { filename } = await downloadSupplierImportTemplate();
    // What actually matters is that no PATH SEPARATOR survives — without one
    // the name cannot traverse anywhere, whatever dots it still contains.
    // (Same sanitizer behaviour as api/masterdata.ts / api/plots.ts.)
    expect(/[/\\]/.test(filename)).toBe(false);
    expect(filename.endsWith('.xlsx')).toBe(true);
  });

  it('throws a typed error carrying the status when the response is not a workbook', async () => {
    getMock.mockRejectedValue(axiosError(403, jsonErrorBlob('ไม่มีสิทธิ์')));

    await expect(downloadSupplierImportTemplate()).rejects.toMatchObject({
      name: 'SupplierImportReportError',
      status: 403,
      message: 'ไม่มีสิทธิ์',
    });
  });

  it('reports a network failure with status null (outcome unknown)', async () => {
    getMock.mockRejectedValue(new Error('Network Error'));

    await expect(downloadSupplierImportTemplate()).rejects.toMatchObject({
      name: 'SupplierImportReportError',
      status: null,
    });
  });
});

// --- Preview (JSON) ---------------------------------------------------------

describe('previewSupplierImport', () => {
  it('POSTs multipart to the preview endpoint with the file field', async () => {
    postMock.mockResolvedValue({ data: { summary: {}, rows: [] } });

    await previewSupplierImport(xlsxFile());

    const [url, form] = postMock.mock.calls[0];
    expect(url).toBe('/api/v1/suppliers/import/preview');
    expect(form).toBeInstanceOf(FormData);
    expect(formFields(form as FormData).file).toBeInstanceOf(File);
  });

  it('never sets Content-Type itself (the browser must add the boundary)', async () => {
    postMock.mockResolvedValue({ data: { summary: {}, rows: [] } });

    await previewSupplierImport(xlsxFile());

    const config = postMock.mock.calls[0][2];
    expect(config).toBeUndefined();
  });

  it('never puts file data in the URL', async () => {
    postMock.mockResolvedValue({ data: { summary: {}, rows: [] } });

    await previewSupplierImport(xlsxFile());

    expect(postMock.mock.calls[0][0]).not.toContain('?');
  });

  it('returns the parsed preview body', async () => {
    const body = { summary: { totalRows: 1 }, rows: [{ rowNumber: 3 }], previewState: previewState() };
    postMock.mockResolvedValue({ data: body });

    expect(await previewSupplierImport(xlsxFile())).toEqual(body);
  });

  it('lets a raw axios error propagate for the caller to translate', async () => {
    postMock.mockRejectedValue(axiosError(422, { detail: 'ไฟล์ไม่ถูกต้อง' }));

    await expect(previewSupplierImport(xlsxFile())).rejects.toMatchObject({
      response: { status: 422 },
    });
  });
});

// --- Preview report (blob) --------------------------------------------------

describe('downloadSupplierPreviewReport', () => {
  it('POSTs to the preview-report endpoint expecting a blob', async () => {
    postMock.mockResolvedValue({
      data: new Blob(['x'], { type: XLSX_MIME }),
      status: 200,
      headers: { 'content-type': XLSX_MIME },
    });

    const report = await downloadSupplierPreviewReport(xlsxFile());

    const [url, , config] = postMock.mock.calls[0];
    expect(url).toBe('/api/v1/suppliers/import/preview-report');
    expect(config).toEqual({ responseType: 'blob' });
    expect(report.kind).toBe('validation');
    expect(report.httpStatus).toBe(200);
  });

  it('sends only the file — no previewState on a read-only report', async () => {
    postMock.mockResolvedValue({
      data: new Blob(['x'], { type: XLSX_MIME }),
      status: 200,
      headers: { 'content-type': XLSX_MIME },
    });

    await downloadSupplierPreviewReport(xlsxFile());

    expect(Object.keys(formFields(postMock.mock.calls[0][1] as FormData))).toEqual(['file']);
  });

  it('normalizes a JSON error blob into a typed error', async () => {
    postMock.mockRejectedValue(axiosError(422, jsonErrorBlob('ไม่พบคอลัมน์ status')));

    await expect(downloadSupplierPreviewReport(xlsxFile())).rejects.toMatchObject({
      name: 'SupplierImportReportError',
      status: 422,
      message: 'ไม่พบคอลัมน์ status',
    });
  });

  it('never mistakes a JSON body served with HTTP 200 for a workbook', async () => {
    postMock.mockResolvedValue({
      data: jsonErrorBlob('บางอย่างผิดพลาด'),
      status: 200,
      headers: { 'content-type': 'application/json' },
    });

    await expect(downloadSupplierPreviewReport(xlsxFile())).rejects.toBeInstanceOf(
      SupplierImportReportError,
    );
  });
});

// --- Commit (blob) ----------------------------------------------------------

describe('commitSupplierImport', () => {
  function okBlobResponse() {
    return {
      data: new Blob(['x'], { type: XLSX_MIME }),
      status: 200,
      headers: {
        'content-type': XLSX_MIME,
        'content-disposition': 'attachment; filename="supplier-import-result-20260813-103000.xlsx"',
      },
    };
  }

  it('POSTs to the commit-report endpoint (never the plain JSON /commit)', async () => {
    postMock.mockResolvedValue(okBlobResponse());

    await commitSupplierImport(xlsxFile(), previewState());

    expect(postMock.mock.calls[0][0]).toBe('/api/v1/suppliers/import/commit-report');
  });

  it('sends the multipart field named exactly "previewState"', async () => {
    postMock.mockResolvedValue(okBlobResponse());

    await commitSupplierImport(xlsxFile(), previewState());

    const fields = formFields(postMock.mock.calls[0][1] as FormData);
    expect(Object.keys(fields).sort()).toEqual(['file', 'previewState']);
  });

  it('serializes previewState as JSON of the WHOLE object', async () => {
    postMock.mockResolvedValue(okBlobResponse());
    const state = previewState();

    await commitSupplierImport(xlsxFile(), state);

    const raw = formFields(postMock.mock.calls[0][1] as FormData).previewState as string;
    expect(typeof raw).toBe('string');
    expect(JSON.parse(raw)).toEqual(state);
    // Not "[object Object]" and not a partially-serialized shape.
    expect(raw).not.toContain('[object');
    expect(JSON.parse(raw).rows[0].existingStateDigest).toBe('b'.repeat(64));
  });

  it('returns a completed report with the server filename', async () => {
    postMock.mockResolvedValue(okBlobResponse());

    const report = await commitSupplierImport(xlsxFile(), previewState());

    expect(report.kind).toBe('completed');
    expect(report.filename).toBe('supplier-import-result-20260813-103000.xlsx');
  });

  it('maps a 409 state conflict to a typed error with status 409', async () => {
    postMock.mockRejectedValue(axiosError(409, jsonErrorBlob('ข้อมูลเปลี่ยนแปลงหลังจากตรวจสอบ')));

    await expect(commitSupplierImport(xlsxFile(), previewState())).rejects.toMatchObject({
      name: 'SupplierImportReportError',
      status: 409,
      message: 'ข้อมูลเปลี่ยนแปลงหลังจากตรวจสอบ',
    });
  });

  it('maps a 422 row-error response and carries the embedded preview', async () => {
    const embedded = { summary: { errorRows: 1 }, rows: [{ rowNumber: 4 }] };
    postMock.mockRejectedValue(axiosError(422, jsonErrorBlob({
      message: 'พบข้อผิดพลาดในบางแถว — ไม่ได้บันทึกข้อมูลใด ๆ',
      preview: embedded,
    })));

    await expect(commitSupplierImport(xlsxFile(), previewState())).rejects.toMatchObject({
      status: 422,
      message: 'พบข้อผิดพลาดในบางแถว — ไม่ได้บันทึกข้อมูลใด ๆ',
      preview: embedded,
    });
  });

  it('reports a network failure with status null so the caller cannot claim nothing was written', async () => {
    postMock.mockRejectedValue(new Error('Network Error'));

    await expect(commitSupplierImport(xlsxFile(), previewState())).rejects.toMatchObject({
      name: 'SupplierImportReportError',
      status: null,
    });
  });

  it('downloadSupplierCommitReport is the same function (no second mutation path)', () => {
    expect(downloadSupplierCommitReport).toBe(commitSupplierImport);
  });
});
