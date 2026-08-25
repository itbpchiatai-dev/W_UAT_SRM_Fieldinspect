/**
 * MasterDataCropVarietyImportModal (round 8-15B).
 *
 * Preview calls the JSON preview endpoint (for the on-screen table); Commit
 * goes through commitCropVarietyImportWithReport (ONE mutation, ONE request
 * to /crop-variety-import/commit-report). api/masterdata.ts exports NO
 * plain-JSON commit function at all this round, so there is structurally no
 * way for this modal (or any other code) to call one.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MasterDataCropVarietyImportModal } from './MasterDataCropVarietyImportModal';
import {
  MasterDataImportReportError,
  type CropVarietyImportPreview,
  type CropVarietyImportPreviewState,
  type CropVarietyImportReportFile,
  type CropVarietyImportRowResult,
} from '../../api/masterdata';

const previewMock = vi.fn();
const commitReportMock = vi.fn();
const previewReportMock = vi.fn();
const templateMock = vi.fn();

vi.mock('../../api/masterdata', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../api/masterdata')>();
  return {
    ...actual,
    previewCropVarietyImport: (...a: unknown[]) => previewMock(...a),
    commitCropVarietyImportWithReport: (...a: unknown[]) => commitReportMock(...a),
    downloadCropVarietyImportPreviewReport: (...a: unknown[]) => previewReportMock(...a),
    downloadCropVarietyImportTemplate: (...a: unknown[]) => templateMock(...a),
  };
});

function row(overrides: Partial<CropVarietyImportRowResult> = {}): CropVarietyImportRowResult {
  return {
    rowNumber: 3, crop: 'พริก', variety: 'พริกขี้หนู', pCode: null, varietyStatus: 'เปิดใช้งาน',
    rowStatus: 'READY', action: 'create_crop_and_variety', pCodeAction: 'none', errorMessage: '',
    ...overrides,
  };
}

function statePreview(overrides: Partial<CropVarietyImportPreviewState> = {}): CropVarietyImportPreviewState {
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

function preview(overrides: Partial<CropVarietyImportPreview> = {}): CropVarietyImportPreview {
  return {
    summary: { totalRows: 1, readyRows: 1, skippedRows: 0, errorRows: 0, cropsToCreate: 1, varietiesToCreate: 1, varietiesToActivate: 0, varietiesToDeactivate: 0, pCodesToCreate: 0, pCodesToActivate: 0 },
    rows: [row()],
    previewState: statePreview(),
    ...overrides,
  };
}

function reportFile(overrides: Partial<CropVarietyImportReportFile> = {}): CropVarietyImportReportFile {
  return {
    blob: new Blob(['xlsx-bytes']),
    filename: 'crop-variety-import-result-20260101-000000.xlsx',
    kind: 'completed',
    httpStatus: 200,
    ...overrides,
  };
}

function renderModal(onImported = vi.fn(), onClose = vi.fn()) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  render(
    <QueryClientProvider client={qc}>
      <MasterDataCropVarietyImportModal onClose={onClose} onImported={onImported} />
    </QueryClientProvider>,
  );
  return { onImported, onClose };
}

function selectFile() {
  const input = screen.getByLabelText('เลือกไฟล์ Excel') as HTMLInputElement;
  const file = new File([new Uint8Array([1, 2, 3])], 'import.xlsx', {
    type: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
  });
  fireEvent.change(input, { target: { files: [file] } });
  return file;
}

async function doPreview() {
  const file = selectFile();
  fireEvent.click(screen.getByRole('button', { name: 'ตรวจสอบไฟล์' }));
  await waitFor(() => expect(previewMock).toHaveBeenCalledWith(file));
  return file;
}

beforeEach(() => {
  previewMock.mockReset();
  commitReportMock.mockReset();
  previewReportMock.mockReset();
  templateMock.mockReset();
});

describe('MasterDataCropVarietyImportModal — template download', () => {
  it('downloads the template on click', async () => {
    const createSpy = vi.spyOn(URL, 'createObjectURL');
    const blob = new Blob(['xlsx']);
    templateMock.mockResolvedValue({ blob, filename: 'crop-variety-import-template.xlsx' });
    renderModal();

    fireEvent.click(screen.getByRole('button', { name: /ดาวน์โหลดเทมเพลต/ }));

    await waitFor(() => expect(templateMock).toHaveBeenCalledTimes(1));
    await waitFor(() => expect(createSpy).toHaveBeenCalledWith(blob));
  });
});

describe('MasterDataCropVarietyImportModal — upload and preview', () => {
  it('previews the selected file and shows the row table', async () => {
    previewMock.mockResolvedValue(preview());
    renderModal();

    await doPreview();

    expect(await screen.findByText('พริก')).toBeTruthy();
    expect(screen.getByText('พริกขี้หนู')).toBeTruthy();
    expect(screen.getByText('เปิดใช้งาน')).toBeTruthy();
  });

  it('shows the summary counts from the preview response', async () => {
    previewMock.mockResolvedValue(preview({
      summary: { totalRows: 5, readyRows: 2, skippedRows: 2, errorRows: 1, cropsToCreate: 1, varietiesToCreate: 3, varietiesToActivate: 1, varietiesToDeactivate: 1, pCodesToCreate: 0, pCodesToActivate: 0 },
    }));
    renderModal();

    await doPreview();

    // 'ปิดใช้งานพันธุ์' is a literal substring of 'เปิดใช้งานพันธุ์' (Thai
    // เปิด/open vs ปิด/close differ only by a leading เ) — an exact-text
    // matcher avoids the false substring match a loose regex would hit.
    const exactText = (text: string) => (_: string, el: Element | null) => el?.textContent === text;
    expect(await screen.findByText(exactText('สร้างชนิดพืช 1'))).toBeTruthy();
    expect(screen.getByText(exactText('สร้างพันธุ์ 3'))).toBeTruthy();
    expect(screen.getByText(exactText('เปิดใช้งานพันธุ์ 1'))).toBeTruthy();
    expect(screen.getByText(exactText('ปิดใช้งานพันธุ์ 1'))).toBeTruthy();
    expect(screen.getByText(exactText('พร้อมนำเข้า 2'))).toBeTruthy();
    expect(screen.getByText(exactText('ข้าม 2'))).toBeTruthy();
    expect(screen.getByText(exactText('ผิดพลาด 1'))).toBeTruthy();
  });

  it('renders READY/SKIPPED/ERROR row status badges and action labels', async () => {
    previewMock.mockResolvedValue(preview({
      summary: { totalRows: 3, readyRows: 1, skippedRows: 1, errorRows: 1, cropsToCreate: 0, varietiesToCreate: 1, varietiesToActivate: 0, varietiesToDeactivate: 0, pCodesToCreate: 0, pCodesToActivate: 0 },
      rows: [
        row({ rowNumber: 3, rowStatus: 'READY', action: 'create_variety' }),
        row({ rowNumber: 4, rowStatus: 'SKIPPED', action: 'none', crop: 'เมล่อน', variety: null, varietyStatus: null }),
        row({ rowNumber: 5, rowStatus: 'ERROR', action: 'none', errorMessage: 'ต้องระบุ crop', crop: null, variety: null, varietyStatus: null }),
      ],
    }));
    renderModal();

    await doPreview();

    expect(await screen.findByText('พร้อมนำเข้า')).toBeTruthy();
    expect(screen.getByText('ข้าม')).toBeTruthy();
    expect(screen.getByText('ผิดพลาด')).toBeTruthy();
    expect(screen.getByText('สร้างพันธุ์')).toBeTruthy();
    // Both the SKIPPED row (action: 'none') and the ERROR row (also
    // action: 'none', per real backend behavior — an errored row never
    // resolves to a real action) legitimately show this same label.
    expect(screen.getAllByText('ไม่มีการเปลี่ยนแปลง')).toHaveLength(2);
    expect(screen.getByText('ต้องระบุ crop')).toBeTruthy();
  });

  it('a JSON 422 preview error shows a Thai message, not raw JSON', async () => {
    previewMock.mockRejectedValue(
      Object.assign(new Error('fail'), { isAxiosError: true, response: { status: 422, data: { detail: 'รูปแบบคอลัมน์ไม่ถูกต้อง' } } }),
    );
    renderModal();

    await doPreview();

    expect(await screen.findByText('รูปแบบคอลัมน์ไม่ถูกต้อง')).toBeTruthy();
  });
});

describe('MasterDataCropVarietyImportModal — Commit button gating', () => {
  it('ERROR row disables Commit', async () => {
    previewMock.mockResolvedValue(preview({
      summary: { totalRows: 1, readyRows: 0, skippedRows: 0, errorRows: 1, cropsToCreate: 0, varietiesToCreate: 0, varietiesToActivate: 0, varietiesToDeactivate: 0, pCodesToCreate: 0, pCodesToActivate: 0 },
      rows: [row({ rowStatus: 'ERROR', errorMessage: 'ต้องระบุ crop' })],
    }));
    renderModal();

    await doPreview();

    const commitBtn = await screen.findByRole('button', { name: 'ยืนยันนำเข้า' });
    expect((commitBtn as HTMLButtonElement).disabled).toBe(true);
  });

  it('readyRows=0 (all skipped, no errors) disables Commit and shows the no-changes message', async () => {
    previewMock.mockResolvedValue(preview({
      summary: { totalRows: 1, readyRows: 0, skippedRows: 1, errorRows: 0, cropsToCreate: 0, varietiesToCreate: 0, varietiesToActivate: 0, varietiesToDeactivate: 0, pCodesToCreate: 0, pCodesToActivate: 0 },
      rows: [row({ rowStatus: 'SKIPPED', action: 'none' })],
    }));
    renderModal();

    await doPreview();

    const commitBtn = await screen.findByRole('button', { name: 'ยืนยันนำเข้า' });
    expect((commitBtn as HTMLButtonElement).disabled).toBe(true);
    expect(screen.getByText(/ไม่มีข้อมูลที่ต้องนำเข้า/)).toBeTruthy();
  });

  it('a preview with no previewState disables Commit', async () => {
    previewMock.mockResolvedValue(preview({ previewState: null }));
    renderModal();

    await doPreview();

    const commitBtn = await screen.findByRole('button', { name: 'ยืนยันนำเข้า' });
    expect((commitBtn as HTMLButtonElement).disabled).toBe(true);
  });

  it('a clean READY preview enables Commit', async () => {
    previewMock.mockResolvedValue(preview());
    renderModal();

    await doPreview();

    const commitBtn = await screen.findByRole('button', { name: 'ยืนยันนำเข้า' });
    await waitFor(() => expect((commitBtn as HTMLButtonElement).disabled).toBe(false));
  });

  it('changing the file clears the old preview, error, and commit outcome', async () => {
    previewMock.mockResolvedValue(preview());
    renderModal();
    await doPreview();
    await screen.findByText('พริก');

    selectFile(); // pick a (new) file again — must wipe the table immediately

    expect(screen.queryByText('พริก')).toBeNull();
  });
});

describe('MasterDataCropVarietyImportModal — commit (sole mutation)', () => {
  it('calls commitCropVarietyImportWithReport exactly once per click', async () => {
    previewMock.mockResolvedValue(preview());
    commitReportMock.mockResolvedValue(reportFile());
    renderModal();
    await doPreview();

    const commitBtn = await screen.findByRole('button', { name: 'ยืนยันนำเข้า' });
    await waitFor(() => expect((commitBtn as HTMLButtonElement).disabled).toBe(false));
    fireEvent.click(commitBtn);

    await waitFor(() => expect(commitReportMock).toHaveBeenCalledTimes(1));
    expect(commitReportMock).toHaveBeenCalledWith(expect.any(File), preview().previewState);
  });

  it('Commit is disabled while a commit request is already in flight (no double-submit)', async () => {
    previewMock.mockResolvedValue(preview());
    let resolveCommit!: (v: CropVarietyImportReportFile) => void;
    commitReportMock.mockReturnValue(new Promise((res) => { resolveCommit = res; }));
    renderModal();
    await doPreview();

    const commitBtn = await screen.findByRole('button', { name: 'ยืนยันนำเข้า' });
    await waitFor(() => expect((commitBtn as HTMLButtonElement).disabled).toBe(false));
    fireEvent.click(commitBtn);

    await waitFor(() => expect((commitBtn as HTMLButtonElement).disabled).toBe(true));
    fireEvent.click(commitBtn); // second click while pending — must be a no-op
    expect(commitReportMock).toHaveBeenCalledTimes(1);

    resolveCommit(reportFile());
    await screen.findByText('นำเข้าสำเร็จ');
  });

  it('success auto-downloads the completed workbook and calls onImported exactly once', async () => {
    const createSpy = vi.spyOn(URL, 'createObjectURL');
    previewMock.mockResolvedValue(preview());
    const completed = reportFile({ filename: 'crop-variety-import-result-x.xlsx' });
    commitReportMock.mockResolvedValue(completed);
    const { onImported } = renderModal();
    await doPreview();

    const commitBtn = await screen.findByRole('button', { name: 'ยืนยันนำเข้า' });
    await waitFor(() => expect((commitBtn as HTMLButtonElement).disabled).toBe(false));
    fireEvent.click(commitBtn);

    await screen.findByText('นำเข้าสำเร็จ');
    expect(createSpy).toHaveBeenCalledWith(completed.blob);
    expect(onImported).toHaveBeenCalledTimes(1);
  });

  it('after success, Commit stays locked (permanently) for this modal instance', async () => {
    previewMock.mockResolvedValue(preview());
    commitReportMock.mockResolvedValue(reportFile());
    renderModal();
    await doPreview();

    const commitBtn = await screen.findByRole('button', { name: 'ยืนยันนำเข้า' });
    await waitFor(() => expect((commitBtn as HTMLButtonElement).disabled).toBe(false));
    fireEvent.click(commitBtn);

    await screen.findByText('นำเข้าสำเร็จ');
    expect(screen.queryByRole('button', { name: 'ยืนยันนำเข้า' })).toBeNull();
    // Two elements share the accessible name 'ปิด': the header ✕ button
    // (aria-label) and the footer button (its text changes from
    // 'ยกเลิก' to 'ปิด' once committed) — both are expected to exist.
    expect(screen.getAllByRole('button', { name: 'ปิด' })).toHaveLength(2);
  });
});

describe('MasterDataCropVarietyImportModal — 409/422 commit error handling', () => {
  it('a 409 state conflict shows the exact backend message, clears the preview, and requires a fresh Preview', async () => {
    previewMock.mockResolvedValue(preview());
    commitReportMock.mockRejectedValue(
      new MasterDataImportReportError('ข้อมูล Master Data มีการเปลี่ยนแปลง กรุณาตรวจสอบไฟล์อีกครั้งก่อนนำเข้า', 409),
    );
    renderModal();
    await doPreview();

    const commitBtn = await screen.findByRole('button', { name: 'ยืนยันนำเข้า' });
    await waitFor(() => expect((commitBtn as HTMLButtonElement).disabled).toBe(false));
    fireEvent.click(commitBtn);

    expect(await screen.findByText('ข้อมูล Master Data มีการเปลี่ยนแปลง กรุณาตรวจสอบไฟล์อีกครั้งก่อนนำเข้า')).toBeTruthy();
    // The table (bound to the now-stale preview) is gone — nothing left to
    // commit against until a genuinely fresh Preview lands.
    expect(screen.queryByText('พริก')).toBeNull();
    const newCommitBtn = screen.getByRole('button', { name: 'ยืนยันนำเข้า' });
    expect((newCommitBtn as HTMLButtonElement).disabled).toBe(true);
  });

  it('a 422 (row errors on server re-check) blocks Commit and prompts a fresh Preview — no workbook is downloaded', async () => {
    const createSpy = vi.spyOn(URL, 'createObjectURL');
    previewMock.mockResolvedValue(preview());
    commitReportMock.mockRejectedValue(
      new MasterDataImportReportError('พบข้อผิดพลาดในบางแถว — ไม่ได้บันทึกข้อมูลใด ๆ', 422),
    );
    renderModal();
    await doPreview();

    const commitBtn = await screen.findByRole('button', { name: 'ยืนยันนำเข้า' });
    await waitFor(() => expect((commitBtn as HTMLButtonElement).disabled).toBe(false));
    createSpy.mockClear();
    fireEvent.click(commitBtn);

    expect(await screen.findByText('พบข้อผิดพลาดในบางแถว — ไม่ได้บันทึกข้อมูลใด ๆ')).toBeTruthy();
    expect(createSpy).not.toHaveBeenCalled(); // no blob was ever produced for this error
    const newCommitBtn = screen.getByRole('button', { name: 'ยืนยันนำเข้า' });
    expect((newCommitBtn as HTMLButtonElement).disabled).toBe(true);
  });

  it('a 422 with an embedded preview refreshes the on-screen table with the fresh errors', async () => {
    previewMock.mockResolvedValue(preview());
    const freshPreview = preview({
      summary: { totalRows: 1, readyRows: 0, skippedRows: 0, errorRows: 1, cropsToCreate: 0, varietiesToCreate: 0, varietiesToActivate: 0, varietiesToDeactivate: 0, pCodesToCreate: 0, pCodesToActivate: 0 },
      rows: [row({ rowStatus: 'ERROR', errorMessage: 'ชนิดพืชนี้ปิดใช้งานอยู่', crop: 'ฟักทอง' })],
      previewState: null,
    });
    commitReportMock.mockRejectedValue(
      new MasterDataImportReportError('พบข้อผิดพลาดในบางแถว', 422, freshPreview),
    );
    renderModal();
    await doPreview();

    const commitBtn = await screen.findByRole('button', { name: 'ยืนยันนำเข้า' });
    await waitFor(() => expect((commitBtn as HTMLButtonElement).disabled).toBe(false));
    fireEvent.click(commitBtn);

    expect(await screen.findByText('ฟักทอง')).toBeTruthy();
    expect(screen.getByText('ชนิดพืชนี้ปิดใช้งานอยู่')).toBeTruthy();
  });

  it('re-Preview after a blocked/conflict outcome routes through the same runPreview path and re-enables Commit', async () => {
    previewMock.mockResolvedValueOnce(preview());
    commitReportMock.mockRejectedValue(new MasterDataImportReportError('conflict', 409));
    renderModal();
    await doPreview();

    const commitBtn = await screen.findByRole('button', { name: 'ยืนยันนำเข้า' });
    await waitFor(() => expect((commitBtn as HTMLButtonElement).disabled).toBe(false));
    fireEvent.click(commitBtn);
    await screen.findByText('conflict');

    previewMock.mockResolvedValueOnce(preview());
    fireEvent.click(screen.getByRole('button', { name: 'ตรวจสอบไฟล์อีกครั้ง' }));

    await waitFor(() => expect(previewMock).toHaveBeenCalledTimes(2));
    const reCommitBtn = await screen.findByRole('button', { name: 'ยืนยันนำเข้า' });
    await waitFor(() => expect((reCommitBtn as HTMLButtonElement).disabled).toBe(false));
  });
});

describe('MasterDataCropVarietyImportModal — network error', () => {
  it('a network error (no response) shows a generic message and does NOT close the modal', async () => {
    previewMock.mockResolvedValue(preview());
    commitReportMock.mockRejectedValue(new MasterDataImportReportError('Network Error', null));
    const { onClose } = renderModal();
    await doPreview();

    const commitBtn = await screen.findByRole('button', { name: 'ยืนยันนำเข้า' });
    await waitFor(() => expect((commitBtn as HTMLButtonElement).disabled).toBe(false));
    fireEvent.click(commitBtn);

    expect(await screen.findByText('เชื่อมต่อระบบไม่สำเร็จ กรุณาลองใหม่อีกครั้ง')).toBeTruthy();
    expect(onClose).not.toHaveBeenCalled();
  });

  it('an unexpected 500 also shows the generic connection message, never a raw error', async () => {
    previewMock.mockResolvedValue(preview());
    commitReportMock.mockRejectedValue(new MasterDataImportReportError('Internal Server Error', 500));
    renderModal();
    await doPreview();

    const commitBtn = await screen.findByRole('button', { name: 'ยืนยันนำเข้า' });
    await waitFor(() => expect((commitBtn as HTMLButtonElement).disabled).toBe(false));
    fireEvent.click(commitBtn);

    expect(await screen.findByText('เชื่อมต่อระบบไม่สำเร็จ กรุณาลองใหม่อีกครั้ง')).toBeTruthy();
  });
});

describe('MasterDataCropVarietyImportModal — preview-report download', () => {
  it('downloads the validation report for the current file', async () => {
    const createSpy = vi.spyOn(URL, 'createObjectURL');
    previewMock.mockResolvedValue(preview());
    const blob = new Blob(['xlsx']);
    previewReportMock.mockResolvedValue({ blob, filename: 'crop-variety-import-validation.xlsx', kind: 'validation', httpStatus: 200 });
    renderModal();
    const file = await doPreview();

    fireEvent.click(await screen.findByRole('button', { name: /ดาวน์โหลดผลตรวจสอบ/ }));

    await waitFor(() => expect(previewReportMock).toHaveBeenCalledWith(file));
    await waitFor(() => expect(createSpy).toHaveBeenCalledWith(blob));
  });
});

describe('MasterDataCropVarietyImportModal — mobile-friendly table', () => {
  it('wraps the preview table in a horizontally-scrollable container', async () => {
    previewMock.mockResolvedValue(preview());
    renderModal();
    await doPreview();

    const table = await screen.findByRole('table');
    expect(table.parentElement?.className).toContain('overflow-x-auto');
  });
});

describe('MasterDataCropVarietyImportModal — round 8-26B: the pCode column', () => {
  it('renders a P.Code column with the row value', async () => {
    previewMock.mockResolvedValue(preview({
      rows: [row({ pCode: 'WM-111', pCodeAction: 'create_p_code' })],
    }));
    renderModal();

    await doPreview();

    expect(await screen.findByRole('columnheader', { name: 'P.Code' })).toBeTruthy();
    expect(screen.getByRole('cell', { name: 'WM-111' })).toBeTruthy();
  });

  it('shows an em dash for a row with no P.Code', async () => {
    previewMock.mockResolvedValue(preview({ rows: [row({ pCode: null })] }));
    renderModal();

    await doPreview();

    await screen.findByText('พริกขี้หนู');
    expect(screen.getAllByRole('cell').some((c) => c.textContent === '—')).toBe(true);
  });

  it('shows BOTH actions when a row changes its variety AND its P.Code', async () => {
    // The two actions are independent — showing only the crop/variety one
    // would hide the fact that a P.Code is about to be created.
    previewMock.mockResolvedValue(preview({
      rows: [row({ action: 'create_variety', pCodeAction: 'create_p_code', pCode: 'WM-111' })],
    }));
    renderModal();

    await doPreview();

    expect(await screen.findByText('สร้างพันธุ์ + สร้าง P.Code')).toBeTruthy();
  });

  it('shows the P.Code action alone when the crop/variety are unchanged', async () => {
    previewMock.mockResolvedValue(preview({
      rows: [row({ action: 'none', pCodeAction: 'activate_p_code', pCode: 'WM-111' })],
    }));
    renderModal();

    await doPreview();

    expect(await screen.findByText('เปิดใช้งาน P.Code')).toBeTruthy();
  });

  it('still shows "ไม่มีการเปลี่ยนแปลง" when neither action does anything', async () => {
    previewMock.mockResolvedValue(preview({
      rows: [row({ rowStatus: 'SKIPPED', action: 'none', pCodeAction: 'none' })],
    }));
    renderModal();

    await doPreview();

    expect(await screen.findByText('ไม่มีการเปลี่ยนแปลง')).toBeTruthy();
  });

  it('shows the P.Code counts in the preview summary', async () => {
    previewMock.mockResolvedValue(preview({
      summary: {
        totalRows: 2, readyRows: 2, skippedRows: 0, errorRows: 0,
        cropsToCreate: 0, varietiesToCreate: 0, varietiesToActivate: 0, varietiesToDeactivate: 0,
        pCodesToCreate: 2, pCodesToActivate: 1,
      },
    }));
    renderModal();

    await doPreview();

    const exactText = (text: string) => (_: string, el: Element | null) => el?.textContent === text;
    expect(await screen.findByText(exactText('สร้าง P.Code 2'))).toBeTruthy();
    expect(screen.getByText(exactText('เปิดใช้งาน P.Code 1'))).toBeTruthy();
  });

  it('documents the pCode column in the format help text', async () => {
    renderModal();

    expect(screen.getByText('pCode')).toBeTruthy();
    expect(screen.getByText(/เว้นว่าง = คงค่าเดิม/)).toBeTruthy();
  });
});
