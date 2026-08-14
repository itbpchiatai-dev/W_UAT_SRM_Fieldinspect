/**
 * Supplier import modal (round 8-20B; backend round 8-20A).
 *
 * Covers the whole flow: template download, file pick, Preview, the two
 * standing warnings, error rows blocking Commit, the Commit request's own
 * payload (same file + the previewState Preview returned), 409/422 handling,
 * success counts, and the state-reset rules.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor, within } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { SupplierImportModal } from './SupplierImportModal';
import {
  SupplierImportReportError,
  type SupplierImportPreview,
  type SupplierImportPreviewState,
  type SupplierImportRowResult,
  type SupplierImportSummary,
} from '../../api/suppliers';

const templateMock = vi.fn();
const previewMock = vi.fn();
const commitMock = vi.fn();
const previewReportMock = vi.fn();

vi.mock('../../api/suppliers', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../api/suppliers')>();
  return {
    ...actual,
    downloadSupplierImportTemplate: (...a: unknown[]) => templateMock(...a),
    previewSupplierImport: (...a: unknown[]) => previewMock(...a),
    commitSupplierImport: (...a: unknown[]) => commitMock(...a),
    downloadSupplierPreviewReport: (...a: unknown[]) => previewReportMock(...a),
  };
});

const downloadBlobMock = vi.fn();
vi.mock('../../lib/downloadBlob', () => ({
  downloadBlob: (...a: unknown[]) => downloadBlobMock(...a),
}));

const XLSX_MIME = 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet';

function xlsxFile(name = 'suppliers.xlsx') {
  return new File([new Uint8Array([1, 2, 3])], name, { type: XLSX_MIME });
}

function summary(overrides: Partial<SupplierImportSummary> = {}): SupplierImportSummary {
  return {
    totalRows: 1, readyRows: 1, errorRows: 0,
    suppliersToCreate: 1, suppliersToUpdate: 0,
    suppliersToActivate: 0, suppliersToDeactivate: 0, unchangedRows: 0,
    ...overrides,
  };
}

function row(overrides: Partial<SupplierImportRowResult> = {}): SupplierImportRowResult {
  return {
    rowNumber: 3, action: 'save_supplier',
    supplierCode: 'SUP100', supplierName: 'Supplier Hundred',
    taxId: null, contactName: null, contactEmail: null, contactPhone: null,
    address: null, status: 'active',
    rowStatus: 'READY', operation: 'create', errorMessage: '', warningMessage: '',
    ...overrides,
  };
}

function previewState(): SupplierImportPreviewState {
  return {
    fileSha256: 'a'.repeat(64),
    rows: [{
      rowNumber: 3, supplierCode: 'SUP100', operation: 'create',
      supplierExisted: false, supplierWasActive: null, existingStateDigest: null,
    }],
  };
}

function preview(overrides: Partial<SupplierImportPreview> = {}): SupplierImportPreview {
  return {
    summary: summary(),
    rows: [row()],
    previewState: previewState(),
    ...overrides,
  };
}

function reportFile(filename = 'supplier-import-result.xlsx') {
  return {
    blob: new Blob(['x'], { type: XLSX_MIME }),
    filename,
    kind: 'completed' as const,
    httpStatus: 200,
  };
}

const onClose = vi.fn();
const onImported = vi.fn();

function renderModal() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <SupplierImportModal onClose={onClose} onImported={onImported} />
    </QueryClientProvider>,
  );
}

function pickFile(file = xlsxFile()) {
  const input = screen.getByLabelText('เลือกไฟล์ Excel') as HTMLInputElement;
  fireEvent.change(input, { target: { files: [file] } });
  return input;
}

async function pickAndPreview(result: SupplierImportPreview = preview(), file = xlsxFile()) {
  previewMock.mockResolvedValue(result);
  pickFile(file);
  fireEvent.click(screen.getByRole('button', { name: /ตรวจสอบไฟล์/ }));
  await waitFor(() => expect(previewMock).toHaveBeenCalled());
  return file;
}

beforeEach(() => {
  vi.clearAllMocks();
  templateMock.mockResolvedValue({ blob: new Blob(['t']), filename: 'supplier-import-template.xlsx' });
  commitMock.mockResolvedValue(reportFile());
  previewReportMock.mockResolvedValue({
    blob: new Blob(['v']), filename: 'supplier-import-validation.xlsx',
    kind: 'validation' as const, httpStatus: 200,
  });
});

// --- template + file handling ----------------------------------------------

describe('SupplierImportModal — template and file handling', () => {
  it('downloads the template', async () => {
    renderModal();
    fireEvent.click(screen.getByRole('button', { name: /ดาวน์โหลด Template/ }));

    await waitFor(() => expect(templateMock).toHaveBeenCalled());
    expect(downloadBlobMock).toHaveBeenCalledWith(expect.any(Blob), 'supplier-import-template.xlsx');
  });

  it('accepts only .xlsx', () => {
    renderModal();
    const input = screen.getByLabelText('เลือกไฟล์ Excel') as HTMLInputElement;
    expect(input.accept).toBe('.xlsx');
  });

  it('shows the selected filename', () => {
    renderModal();
    pickFile(xlsxFile('my-suppliers.xlsx'));
    expect(screen.getByText('my-suppliers.xlsx')).toBeTruthy();
  });

  it('keeps ตรวจสอบไฟล์ disabled until a file is picked', () => {
    renderModal();
    const btn = screen.getByRole('button', { name: /ตรวจสอบไฟล์/ }) as HTMLButtonElement;
    expect(btn.disabled).toBe(true);
    pickFile();
    expect((screen.getByRole('button', { name: /ตรวจสอบไฟล์/ }) as HTMLButtonElement).disabled).toBe(false);
  });

  it('changing the file clears the previous preview and its previewState', async () => {
    renderModal();
    await pickAndPreview();
    expect(await screen.findByText('SUP100')).toBeTruthy();

    pickFile(xlsxFile('another.xlsx'));

    expect(screen.queryByText('SUP100')).toBeNull();
    expect((screen.getByRole('button', { name: 'ยืนยันนำเข้า' }) as HTMLButtonElement).disabled).toBe(true);
  });

  it('a fresh mount starts with no leftover state (close then reopen)', async () => {
    const first = renderModal();
    await pickAndPreview();
    await screen.findByText('SUP100');
    first.unmount();

    renderModal();
    expect(screen.queryByText('SUP100')).toBeNull();
    expect((screen.getByLabelText('เลือกไฟล์ Excel') as HTMLInputElement).value).toBe('');
    expect((screen.getByRole('button', { name: 'ยืนยันนำเข้า' }) as HTMLButtonElement).disabled).toBe(true);
  });
});

// --- preview ----------------------------------------------------------------

describe('SupplierImportModal — preview', () => {
  it('sends the picked file to the preview endpoint', async () => {
    renderModal();
    const file = await pickAndPreview();
    expect(previewMock).toHaveBeenCalledWith(file);
  });

  it('renders the required preview columns', async () => {
    renderModal();
    await pickAndPreview();

    for (const header of ['แถว Excel', 'รหัส Supplier', 'ชื่อ Supplier', 'การดำเนินการ', 'ผลตรวจ', 'ข้อความ']) {
      expect(await screen.findByText(header)).toBeTruthy();
    }
  });

  it('translates every operation into Thai', async () => {
    renderModal();
    await pickAndPreview(preview({
      summary: summary({ totalRows: 3, readyRows: 3, suppliersToCreate: 1, suppliersToUpdate: 1, unchangedRows: 1 }),
      rows: [
        row({ rowNumber: 3, supplierCode: 'SUP100', operation: 'create' }),
        row({ rowNumber: 4, supplierCode: 'SUP001', operation: 'update' }),
        row({ rowNumber: 5, supplierCode: 'SUP002', operation: 'no_change' }),
      ],
    }));

    const table = await screen.findByRole('table');
    expect(within(table).getByText('สร้างใหม่')).toBeTruthy();
    expect(within(table).getByText('แก้ไข')).toBeTruthy();
    expect(within(table).getByText('ไม่เปลี่ยนแปลง')).toBeTruthy();
  });

  it('shows a Thai message and keeps the modal open when preview fails', async () => {
    renderModal();
    previewMock.mockRejectedValue(Object.assign(new Error('bad'), {
      isAxiosError: true,
      response: { status: 422, data: { detail: 'ไฟล์ไม่ถูกต้อง: ไม่พบคอลัมน์ status' } },
    }));
    pickFile();
    fireEvent.click(screen.getByRole('button', { name: /ตรวจสอบไฟล์/ }));

    expect(await screen.findByText('ไฟล์ไม่ถูกต้อง: ไม่พบคอลัมน์ status')).toBeTruthy();
    expect(onClose).not.toHaveBeenCalled();
  });

  it('downloads the validation report for the same file', async () => {
    renderModal();
    const file = await pickAndPreview();
    fireEvent.click(await screen.findByRole('button', { name: /ดาวน์โหลดผลตรวจสอบ/ }));

    await waitFor(() => expect(previewReportMock).toHaveBeenCalledWith(file));
    expect(downloadBlobMock).toHaveBeenCalledWith(expect.any(Blob), 'supplier-import-validation.xlsx');
  });
});

// --- warnings ---------------------------------------------------------------

describe('SupplierImportModal — warnings', () => {
  it('always shows the blank-clears-value warning, even before a file is picked', () => {
    renderModal();
    expect(screen.getByText('ช่องข้อมูลเสริมที่เว้นว่างใน Excel จะล้างค่าเดิมของ Supplier')).toBeTruthy();
  });

  it('shows the blank-clears warning alongside a preview too', async () => {
    renderModal();
    await pickAndPreview();
    await screen.findByText('SUP100');
    expect(screen.getByText('ช่องข้อมูลเสริมที่เว้นว่างใน Excel จะล้างค่าเดิมของ Supplier')).toBeTruthy();
  });

  it('shows the no-cascade warning when the file deactivates a Supplier', async () => {
    renderModal();
    await pickAndPreview(preview({
      summary: summary({ suppliersToCreate: 0, suppliersToUpdate: 1, suppliersToDeactivate: 1 }),
      rows: [row({
        supplierCode: 'SUP001', operation: 'update', status: 'inactive',
        warningMessage: 'Supplier จะถูกปิดใช้งาน แต่ข้อมูลแปลง รอบปลูก และประวัติการตรวจจะไม่ถูกลบ',
      })],
    }));

    expect(await screen.findAllByText(
      'Supplier จะถูกปิดใช้งาน แต่ข้อมูลแปลง รอบปลูก และประวัติการตรวจจะไม่ถูกลบ',
    )).not.toHaveLength(0);
  });

  it('does not show the no-cascade warning when nothing is deactivated', async () => {
    renderModal();
    await pickAndPreview();
    await screen.findByText('SUP100');
    expect(screen.queryByText(
      'Supplier จะถูกปิดใช้งาน แต่ข้อมูลแปลง รอบปลูก และประวัติการตรวจจะไม่ถูกลบ',
    )).toBeNull();
  });
});

// --- commit gating ----------------------------------------------------------

describe('SupplierImportModal — commit gating', () => {
  it('disables Commit and says nothing was imported when a row has an error', async () => {
    renderModal();
    await pickAndPreview(preview({
      summary: summary({ totalRows: 2, readyRows: 1, errorRows: 1 }),
      rows: [
        row({ rowNumber: 3 }),
        row({ rowNumber: 4, rowStatus: 'ERROR', errorMessage: 'status ไม่ถูกต้อง' }),
      ],
    }));

    expect((await screen.findByRole('button', { name: 'ยืนยันนำเข้า' }) as HTMLButtonElement).disabled).toBe(true);
    expect(screen.getByText(/ไม่มีข้อมูลในไฟล์นี้ถูกนำเข้า/)).toBeTruthy();
    expect(screen.getByText('status ไม่ถูกต้อง')).toBeTruthy();
  });

  it('enables Commit when every row passes', async () => {
    renderModal();
    await pickAndPreview();
    expect((await screen.findByRole('button', { name: 'ยืนยันนำเข้า' }) as HTMLButtonElement).disabled).toBe(false);
  });

  it('keeps Commit disabled when there is nothing to import', async () => {
    renderModal();
    await pickAndPreview(preview({
      summary: summary({ readyRows: 0, suppliersToCreate: 0, unchangedRows: 0, totalRows: 0 }),
      rows: [],
    }));

    expect((await screen.findByRole('button', { name: 'ยืนยันนำเข้า' }) as HTMLButtonElement).disabled).toBe(true);
  });
});

// --- commit -----------------------------------------------------------------

describe('SupplierImportModal — commit', () => {
  async function commitOnce(result?: SupplierImportPreview) {
    renderModal();
    const file = await pickAndPreview(result ?? preview());
    fireEvent.click(await screen.findByRole('button', { name: 'ยืนยันนำเข้า' }));
    return file;
  }

  it('sends the SAME file and the previewState from Preview', async () => {
    const file = await commitOnce();
    await waitFor(() => expect(commitMock).toHaveBeenCalled());
    expect(commitMock).toHaveBeenCalledWith(file, previewState());
  });

  it('downloads the completed workbook and reports success upward', async () => {
    await commitOnce();
    await waitFor(() => expect(onImported).toHaveBeenCalled());
    expect(downloadBlobMock).toHaveBeenCalledWith(expect.any(Blob), 'supplier-import-result.xlsx');
    expect(await screen.findByText('นำเข้าสำเร็จ')).toBeTruthy();
  });

  it('shows all five counts after a successful commit', async () => {
    await commitOnce(preview({
      summary: summary({
        totalRows: 5, readyRows: 5,
        suppliersToCreate: 2, suppliersToUpdate: 1,
        suppliersToActivate: 1, suppliersToDeactivate: 1, unchangedRows: 1,
      }),
    }));

    // The counts are JSX interpolations, so they land in separate text
    // nodes — assert against the whole banner paragraph, not one node.
    await screen.findByText('นำเข้าสำเร็จ');
    const banner = document.querySelector('.bg-green-50') as HTMLElement;
    expect(banner.textContent).toContain('สร้างใหม่ 2');
    expect(banner.textContent).toContain('แก้ไข 1');
    expect(banner.textContent).toContain('เปิดใช้งาน 1');
    expect(banner.textContent).toContain('ปิดใช้งาน 1');
    expect(banner.textContent).toContain('ไม่เปลี่ยนแปลง 1');
  });

  it('locks Commit permanently after success (no second write)', async () => {
    await commitOnce();
    await screen.findByText('นำเข้าสำเร็จ');
    expect(screen.queryByRole('button', { name: 'ยืนยันนำเข้า' })).toBeNull();
    expect(commitMock).toHaveBeenCalledTimes(1);
  });

  it('lets the user re-download the completed workbook without re-committing', async () => {
    await commitOnce();
    await screen.findByText('นำเข้าสำเร็จ');
    downloadBlobMock.mockClear();

    fireEvent.click(screen.getByRole('button', { name: /ดาวน์โหลดผลการนำเข้าอีกครั้ง/ }));

    expect(downloadBlobMock).toHaveBeenCalledTimes(1);
    expect(commitMock).toHaveBeenCalledTimes(1);
  });

  it('does not double-submit when Commit is clicked twice', async () => {
    let resolve: ((v: unknown) => void) | undefined;
    commitMock.mockReturnValue(new Promise((r) => { resolve = r; }));
    renderModal();
    await pickAndPreview();

    const btn = await screen.findByRole('button', { name: 'ยืนยันนำเข้า' });
    fireEvent.click(btn);
    await screen.findByRole('button', { name: /กำลังนำเข้า/ });
    fireEvent.click(screen.getByRole('button', { name: /กำลังนำเข้า/ }));

    expect(commitMock).toHaveBeenCalledTimes(1);
    resolve?.(reportFile());
  });

  it('cannot be closed while a commit is in flight', async () => {
    commitMock.mockReturnValue(new Promise(() => {}));
    renderModal();
    await pickAndPreview();
    fireEvent.click(await screen.findByRole('button', { name: 'ยืนยันนำเข้า' }));

    await screen.findByRole('button', { name: /กำลังนำเข้า/ });
    fireEvent.click(screen.getByRole('button', { name: 'ปิด' }));
    expect(onClose).not.toHaveBeenCalled();
  });
});

// --- commit failures --------------------------------------------------------

describe('SupplierImportModal — commit failures', () => {
  async function failCommitWith(error: unknown) {
    commitMock.mockRejectedValue(error);
    renderModal();
    await pickAndPreview();
    fireEvent.click(await screen.findByRole('button', { name: 'ยืนยันนำเข้า' }));
  }

  it('409 clears the approved state and demands a fresh Preview', async () => {
    await failCommitWith(new SupplierImportReportError(
      'ข้อมูลเปลี่ยนแปลงหลังจากตรวจสอบ กรุณาตรวจสอบไฟล์ใหม่อีกครั้ง', 409,
    ));

    expect(await screen.findByText('ข้อมูลเปลี่ยนแปลงหลังจากตรวจสอบ กรุณาตรวจสอบไฟล์ใหม่อีกครั้ง')).toBeTruthy();
    expect(screen.getByText(/ไฟล์หรือข้อมูล Supplier มีการเปลี่ยนแปลงหลังการตรวจสอบ/)).toBeTruthy();
    // The stale preview is gone, so Commit has nothing to retry with.
    expect(screen.queryByRole('table')).toBeNull();
    expect((screen.getByRole('button', { name: 'ยืนยันนำเข้า' }) as HTMLButtonElement).disabled).toBe(true);
    expect(screen.getByRole('button', { name: /ตรวจสอบไฟล์อีกครั้ง/ })).toBeTruthy();
    expect(onImported).not.toHaveBeenCalled();
  });

  it('a fresh Preview after a 409 re-enables Commit', async () => {
    await failCommitWith(new SupplierImportReportError('เปลี่ยนแปลง', 409));
    await screen.findByRole('button', { name: /ตรวจสอบไฟล์อีกครั้ง/ });

    previewMock.mockResolvedValue(preview());
    fireEvent.click(screen.getByRole('button', { name: /ตรวจสอบไฟล์อีกครั้ง/ }));

    await waitFor(() =>
      expect((screen.getByRole('button', { name: 'ยืนยันนำเข้า' }) as HTMLButtonElement).disabled).toBe(false));
  });

  it('422 shows the embedded preview and keeps Commit disabled', async () => {
    const embedded = preview({
      summary: summary({ totalRows: 1, readyRows: 0, errorRows: 1, suppliersToCreate: 0 }),
      rows: [row({ rowNumber: 3, rowStatus: 'ERROR', errorMessage: 'supplierCode ซ้ำกันในไฟล์' })],
      previewState: null,
    });
    await failCommitWith(new SupplierImportReportError(
      'พบข้อผิดพลาดในบางแถว — ไม่ได้บันทึกข้อมูลใด ๆ', 422, embedded,
    ));

    expect(await screen.findByText('supplierCode ซ้ำกันในไฟล์')).toBeTruthy();
    expect((screen.getByRole('button', { name: 'ยืนยันนำเข้า' }) as HTMLButtonElement).disabled).toBe(true);
    expect(onImported).not.toHaveBeenCalled();
  });

  it('a network failure never claims that nothing was written', async () => {
    await failCommitWith(new SupplierImportReportError('Network Error', null));

    expect(await screen.findByText(/ไม่สามารถยืนยันผลการนำเข้าได้/)).toBeTruthy();
    expect(screen.queryByText('ไม่มีข้อมูลในไฟล์นี้ถูกนำเข้า')).toBeNull();
    expect(onClose).not.toHaveBeenCalled();
  });
});

// --- layout -----------------------------------------------------------------

describe('SupplierImportModal — layout', () => {
  it('keeps the wide preview table scrollable instead of overflowing on mobile', async () => {
    renderModal();
    await pickAndPreview();

    const table = await screen.findByRole('table');
    const scroller = table.parentElement as HTMLElement;
    expect(scroller.className).toContain('overflow-x-auto');
  });

  it('constrains the dialog height so a long file cannot push the footer off-screen', () => {
    const { container } = renderModal();
    const panel = container.querySelector('.max-h-\\[95vh\\]');
    expect(panel).toBeTruthy();
  });
});
