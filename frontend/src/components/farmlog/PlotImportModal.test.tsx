/**
 * PlotImportModal (round 7.5; result-workbook flow round 8-2.5).
 *
 * Preview still calls the JSON preview endpoint (for the on-screen table);
 * Commit now goes through commitPlotImportWithReport (ONE mutation, ONE
 * request to /import/commit-report) — the legacy JSON commitPlotImport()
 * must never be called from this modal. A completed commit auto-downloads
 * the Completed workbook and locks Commit permanently for the modal
 * instance; a 422 "blocked" response writes nothing and silently
 * re-previews (read-only) so the table reflects what still needs fixing.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { PlotImportModal } from './PlotImportModal';
import {
  PlotImportReportError,
  type PlotImportPreview,
  type PlotImportRowResult,
  type PlotImportReportFile,
} from '../../api/plots';

const previewMock = vi.fn();
const commitReportMock = vi.fn();
const commitJsonMock = vi.fn(); // legacy JSON commit — must NEVER be called from this modal
const previewReportMock = vi.fn();
const templateMock = vi.fn();

vi.mock('../../api/plots', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../api/plots')>();
  return {
    ...actual,
    previewPlotImport: (...a: unknown[]) => previewMock(...a),
    commitPlotImport: (...a: unknown[]) => commitJsonMock(...a),
    commitPlotImportWithReport: (...a: unknown[]) => commitReportMock(...a),
    downloadPlotImportPreviewReport: (...a: unknown[]) => previewReportMock(...a),
    downloadPlotImportTemplate: (...a: unknown[]) => templateMock(...a),
  };
});

function row(overrides: Partial<PlotImportRowResult> = {}): PlotImportRowResult {
  return {
    rowNumber: 3, action: 'create_plot_with_cycle', supplierCode: 'SUP001',
    plotCode: 'P101', status: 'valid', message: '', payload: null,
    existingPlotId: null, activeCycleId: null, errorCode: null, resultCycleNo: null,
    ...overrides,
  };
}

function preview(overrides: Partial<PlotImportPreview> = {}): PlotImportPreview {
  return {
    totalRows: 1, validRows: 1, errorRows: 0,
    rows: [row()],
    ...overrides,
  };
}

function reportFile(overrides: Partial<PlotImportReportFile> = {}): PlotImportReportFile {
  return {
    blob: new Blob(['xlsx-bytes']),
    filename: 'plot-import-validation-20260101-000000.xlsx',
    kind: 'validation',
    httpStatus: 200,
    ...overrides,
  };
}

function renderModal(onImported = vi.fn()) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  render(
    <QueryClientProvider client={qc}>
      <PlotImportModal onClose={vi.fn()} onImported={onImported} />
    </QueryClientProvider>,
  );
  return { onImported };
}

// Round 8-6E — a manually-resolved Promise for tests that must observe the
// UI DURING a pending mutation (not just before/after it), e.g. asserting
// Commit is disabled WHILE a re-preview is still in flight. A plain
// `mockResolvedValueOnce` settles synchronously-ish and can't represent that
// pending window at all — this is exactly why the original "re-enables
// Commit" test couldn't have caught the round 8-6E race.
function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason?: unknown) => void;
  const promise = new Promise<T>((res, rej) => { resolve = res; reject = rej; });
  return { promise, resolve, reject };
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
  commitJsonMock.mockReset();
  previewReportMock.mockReset();
  templateMock.mockReset();
});

describe('PlotImportModal — preview', () => {
  it('calls the preview endpoint with the chosen file and shows the row table', async () => {
    previewMock.mockResolvedValue(preview());
    renderModal();
    const file = await doPreview();

    expect(previewMock).toHaveBeenCalledWith(file);
    expect(await screen.findByText('P101')).toBeTruthy();
    expect(screen.getByText('พร้อม')).toBeTruthy();
  });

  it('shows "ยังไม่มีข้อมูลถูกนำเข้า" and the pass summary for an all-valid preview', async () => {
    previewMock.mockResolvedValue(preview());
    renderModal();
    await doPreview();

    expect(await screen.findByText('ตรวจสอบผ่าน')).toBeTruthy();
    expect(screen.getByText('ผ่านการตรวจสอบ 1 แถว')).toBeTruthy();
    expect(screen.getByText('ยังไม่มีข้อมูลถูกนำเข้า')).toBeTruthy();
  });

  it('shows the all-or-nothing message and "ยังไม่มีข้อมูลถูกนำเข้า" for an errored preview', async () => {
    previewMock.mockResolvedValue(preview({
      validRows: 0, errorRows: 1,
      rows: [row({ status: 'error', message: 'plotCode นี้มีอยู่แล้ว' })],
    }));
    renderModal();
    await doPreview();

    expect(await screen.findByText('ตรวจสอบไม่ผ่าน')).toBeTruthy();
    expect(screen.getByText('พบข้อผิดพลาด กรุณาแก้ไขทุกแถวก่อนนำเข้า')).toBeTruthy();
    expect(screen.getByText('ยังไม่มีข้อมูลถูกนำเข้า')).toBeTruthy();
    const commitBtn = screen.getByRole('button', { name: 'ยืนยันนำเข้า' }) as HTMLButtonElement;
    expect(commitBtn.disabled).toBe(true);
    expect(commitReportMock).not.toHaveBeenCalled();
  });

  it('shows a distinct "ข้อมูลซ้ำ" badge (from errorCode, not the message) for a duplicate-rollover row', async () => {
    previewMock.mockResolvedValue(preview({
      validRows: 0, errorRows: 1,
      rows: [row({
        status: 'error', message: 'ข้อมูลรอบใหม่ตรงกับรอบปลูกที่เปิดอยู่ทั้งหมด',
        errorCode: 'duplicate_rollover',
      })],
    }));
    renderModal();
    await doPreview();

    expect(await screen.findByText('ข้อมูลซ้ำ')).toBeTruthy();
    expect(screen.queryByText('ผิดพลาด')).toBeNull(); // distinct badge, not the generic error one
  });

  it('reports the correct duplicate count separately from the plain-error count', async () => {
    previewMock.mockResolvedValue(preview({
      totalRows: 3, validRows: 1, errorRows: 2,
      rows: [
        row({ rowNumber: 3, status: 'valid' }),
        row({ rowNumber: 4, status: 'error', message: 'bad', errorCode: null }),
        row({ rowNumber: 5, status: 'error', message: 'dup', errorCode: 'duplicate_rollover' }),
      ],
    }));
    renderModal();
    await doPreview();

    expect(await screen.findByText('ผิดพลาด 1')).toBeTruthy();
    expect(screen.getByText('ข้อมูลซ้ำ 1')).toBeTruthy();
  });

  it('renders the close_and_start_new_cycle action label in the preview table', async () => {
    previewMock.mockResolvedValue(preview({
      rows: [row({ action: 'close_and_start_new_cycle', plotCode: 'P003' })],
    }));
    renderModal();
    await doPreview();

    expect(await screen.findByText('จบรอบเดิม + เริ่มรอบใหม่')).toBeTruthy();
  });

  it('shows ชื่อรอบปลูก column and cycleLabel value in the preview table', async () => {
    previewMock.mockResolvedValue(preview({
      rows: [row({
        payload: {
          action: 'create_plot_with_cycle', supplierCode: 'SUP001', plotCode: 'P101',
          plotName: 'แปลงใหม่',
          primaryPhone: null, additionalPhones: [],
          village: null, district: null, province: null,
          latitude: null, longitude: null, rai: null,
          crop: 'พริก', variety: null, cycleLabel: 'jun2026', lotNo: null, supplierLotNo: null,
          poNumber: null, pCode: null,
          oracleSupplierCode: null, oracleInvoice: null, refAccount: null,
          plantingDate: null, plantCount: null, expectedYieldFull: null, expectedYieldUnit: null,
          harvestYield: null, finalYieldAfterClean: null,
          harvestDate: null, finalNote: null,
        },
      })],
    }));
    renderModal();
    await doPreview();

    expect(await screen.findByText('ชื่อรอบปลูก')).toBeTruthy();
    expect(screen.getByText('jun2026')).toBeTruthy();
  });

  it('shows the Excel Row column header (server rowNumber, used directly)', async () => {
    previewMock.mockResolvedValue(preview({ rows: [row({ rowNumber: 7 })] }));
    renderModal();
    await doPreview();

    expect(await screen.findByText('Excel Row')).toBeTruthy();
    expect(screen.getByText('7')).toBeTruthy();
  });

  it('shows the proposed Auto Lot ({PO}-{plotCode}-XX) + mode label in the preview (round 8-5B)', async () => {
    previewMock.mockResolvedValue(preview({
      rows: [row({ lotMode: 'auto', proposedLotNo: 'PO25001-P101-XX' })],
    }));
    renderModal();
    await doPreview();

    expect(await screen.findByText(/PO25001-P101-XX/)).toBeTruthy();
    expect(screen.getByText(/อัตโนมัติ/)).toBeTruthy();
  });

  it('shows the real committed Lot + source over the preview after commit (round 8-5B)', async () => {
    previewMock.mockResolvedValue(preview({
      rows: [row({ lotMode: 'auto', proposedLotNo: 'PO25001-P101-XX',
                   resultLotNo: 'PO25001-P101-03', resultLotNoSource: 'auto', resultLotRunningNo: 3 })],
    }));
    renderModal();
    await doPreview();

    // resultLotNo wins over the proposed XX once the real value exists.
    expect(await screen.findByText(/PO25001-P101-03/)).toBeTruthy();
    expect(screen.queryByText(/PO25001-P101-XX/)).toBeNull();
  });

  it('maps all four action values to their Thai preview labels (round 8-2.7)', async () => {
    previewMock.mockResolvedValue(preview({
      totalRows: 4, validRows: 4, errorRows: 0,
      rows: [
        row({ rowNumber: 3, action: 'create_plot_with_cycle', plotCode: 'P101' }),
        row({ rowNumber: 4, action: 'start_new_cycle', plotCode: 'P003' }),
        row({ rowNumber: 5, action: 'update_current_cycle', plotCode: 'P002' }),
        row({ rowNumber: 6, action: 'close_and_start_new_cycle', plotCode: 'P004' }),
      ],
    }));
    renderModal();
    await doPreview();

    expect(await screen.findByText('สร้างแปลง + รอบแรก')).toBeTruthy();
    expect(screen.getByText('เริ่มรอบในแปลงที่ว่าง')).toBeTruthy();
    expect(screen.getByText('แก้รอบปัจจุบัน')).toBeTruthy();
    expect(screen.getByText('จบรอบเดิม + เริ่มรอบใหม่')).toBeTruthy();
  });

  it('resolved start_next_cycle → start_new_cycle shows "เริ่มรอบใหม่ในแปลงว่าง" with normal tone (round 8-2.7.1)', async () => {
    previewMock.mockResolvedValue(preview({
      rows: [row({ action: 'start_next_cycle', plotCode: 'P010', resolvedAction: 'start_new_cycle' })],
    }));
    renderModal();
    await doPreview();

    const label = await screen.findByText('เริ่มรอบใหม่ในแปลงว่าง');
    expect(label.closest('tr')?.className).not.toContain('amber');
  });

  it('resolved start_next_cycle → close_and_start_new_cycle names the current cycle label being closed', async () => {
    previewMock.mockResolvedValue(preview({
      rows: [row({
        action: 'start_next_cycle', plotCode: 'P010',
        resolvedAction: 'close_and_start_new_cycle',
        currentCycleNo: 3, currentCycleLabel: 'aug2026',
      })],
    }));
    renderModal();
    await doPreview();

    expect(await screen.findByText('ปิดรอบ aug2026 + เริ่มรอบใหม่')).toBeTruthy();
  });

  it('resolved start_next_cycle rollover falls back to "รอบที่ N" when the current cycle has no label', async () => {
    previewMock.mockResolvedValue(preview({
      rows: [row({
        action: 'start_next_cycle', plotCode: 'P010',
        resolvedAction: 'close_and_start_new_cycle',
        currentCycleNo: 3, currentCycleLabel: null,
      })],
    }));
    renderModal();
    await doPreview();

    expect(await screen.findByText('ปิดรอบ รอบที่ 3 + เริ่มรอบใหม่')).toBeTruthy();
  });

  it('gives a resolved start_next_cycle rollover the same amber warning tone as an explicit close_and_start_new_cycle row', async () => {
    previewMock.mockResolvedValue(preview({
      rows: [row({
        action: 'start_next_cycle', plotCode: 'P010',
        resolvedAction: 'close_and_start_new_cycle', currentCycleNo: 3, currentCycleLabel: 'aug2026',
      })],
    }));
    renderModal();
    await doPreview();

    const label = await screen.findByText('ปิดรอบ aug2026 + เริ่มรอบใหม่');
    const tr = label.closest('tr');
    expect(tr?.className).toContain('amber');
    expect(tr?.querySelector('svg')).toBeTruthy();
  });

  it('shows a distinct "ชื่อรอบซ้ำ" badge and the correct message for a same_active_cycle_label error', async () => {
    previewMock.mockResolvedValue(preview({
      validRows: 0, errorRows: 1,
      rows: [row({
        action: 'start_next_cycle', plotCode: 'P010', status: 'error',
        errorCode: 'same_active_cycle_label',
        message: 'ชื่อรอบปลูกนี้ตรงกับรอบที่กำลังเปิดอยู่ หากต้องการแก้ข้อมูลรอบเดิมให้ใช้ update_current_cycle',
      })],
    }));
    renderModal();
    await doPreview();

    expect(await screen.findByText('ชื่อรอบซ้ำ')).toBeTruthy();
    expect(screen.getByText(/หากต้องการแก้ข้อมูลรอบเดิมให้ใช้ update_current_cycle/)).toBeTruthy();
    // Commit stays disabled while any row errors.
    const commitBtn = screen.getByRole('button', { name: 'ยืนยันนำเข้า' }) as HTMLButtonElement;
    expect(commitBtn.disabled).toBe(true);
  });

  it('start_next_cycle row with no resolvedAction yet (blocked before resolving) never shows the raw technical value', async () => {
    previewMock.mockResolvedValue(preview({
      validRows: 0, errorRows: 1,
      rows: [row({
        action: 'start_next_cycle', plotCode: 'P010', status: 'error',
        errorCode: 'same_active_cycle_label', resolvedAction: null,
      })],
    }));
    renderModal();
    await doPreview();

    expect(await screen.findByText('เริ่มรอบถัดไป')).toBeTruthy(); // generic ACTION_LABEL fallback
  });

  it('does not rewrite action=start_next_cycle before commit — the same file is sent through commitPlotImportWithReport', async () => {
    // Round 8-6E Part C item 5 — a real backend preview response ALWAYS
    // carries previewState alongside a resolved start_next_cycle row (see
    // backend _build_preview_state); a mock lacking it now correctly blocks
    // Commit instead of silently sending previewState: null.
    const previewState = {
      fileSha256: 'c'.repeat(64),
      startNextRows: [
        { rowNumber: 3, supplierCode: 'SUP001', plotCode: 'P010', resolvedAction: 'start_new_cycle', activeCycleId: null },
      ],
    };
    previewMock.mockResolvedValue(preview({
      rows: [row({ action: 'start_next_cycle', plotCode: 'P010', resolvedAction: 'start_new_cycle' })],
      previewState: previewState as PlotImportPreview['previewState'],
    }));
    commitReportMock.mockResolvedValue({ outcome: 'completed', report: reportFile({ kind: 'completed' }) });
    renderModal();
    const file = await doPreview();

    const commitBtn = await screen.findByRole('button', { name: 'ยืนยันนำเข้า' });
    await waitFor(() => expect((commitBtn as HTMLButtonElement).disabled).toBe(false));
    fireEvent.click(commitBtn);

    // previewState is echoed verbatim, and the action itself is never rewritten.
    await waitFor(() => expect(commitReportMock).toHaveBeenCalledWith(file, previewState));
    expect(commitReportMock).toHaveBeenCalledTimes(1);
  });

  it('completed summary splits a resolved start_next_cycle row into started vs rolled-over counts', async () => {
    previewMock.mockResolvedValue(preview({
      totalRows: 2, validRows: 2, errorRows: 0,
      rows: [
        row({ rowNumber: 3, action: 'start_next_cycle', plotCode: 'P010', resolvedAction: 'start_new_cycle' }),
        row({
          rowNumber: 4, action: 'start_next_cycle', plotCode: 'P011',
          resolvedAction: 'close_and_start_new_cycle', currentCycleNo: 2, currentCycleLabel: 'jul2026',
        }),
      ],
      previewState: {
        fileSha256: 'd'.repeat(64),
        startNextRows: [
          { rowNumber: 3, supplierCode: 'SUP001', plotCode: 'P010', resolvedAction: 'start_new_cycle', activeCycleId: null },
          { rowNumber: 4, supplierCode: 'SUP001', plotCode: 'P011', resolvedAction: 'close_and_start_new_cycle', activeCycleId: 'cycle-2' },
        ],
      } as PlotImportPreview['previewState'],
    }));
    commitReportMock.mockResolvedValue({ outcome: 'completed', report: reportFile({ kind: 'completed' }) });
    renderModal();
    await doPreview();

    const commitBtn = await screen.findByRole('button', { name: 'ยืนยันนำเข้า' });
    await waitFor(() => expect((commitBtn as HTMLButtonElement).disabled).toBe(false));
    fireEvent.click(commitBtn);

    await screen.findByText('นำเข้าสำเร็จ');
    expect(screen.getByText(/เริ่มรอบปลูก 1/)).toBeTruthy();
    expect(screen.getByText(/จบรอบเดิม\+เริ่มใหม่ 1/)).toBeTruthy();
  });

  it('gives a close_and_start_new_cycle row a distinct warning tone in the table (no confirmation modal)', async () => {
    previewMock.mockResolvedValue(preview({
      rows: [row({ action: 'close_and_start_new_cycle', plotCode: 'P003' })],
    }));
    renderModal();
    await doPreview();

    const label = await screen.findByText('จบรอบเดิม + เริ่มรอบใหม่');
    const tr = label.closest('tr');
    expect(tr?.className).toContain('amber');
    expect(tr?.querySelector('svg')).toBeTruthy(); // warning icon next to the label
    // No stacked confirmation dialog — same table, same modal instance.
    expect(screen.getAllByText('นำเข้าแปลง + รอบปลูก (Excel)').length).toBe(1);
  });

  it('never rewrites a row action based on its cycleLabel — the label always reflects the raw action from the API', async () => {
    previewMock.mockResolvedValue(preview({
      rows: [row({
        action: 'update_current_cycle',
        payload: {
          action: 'update_current_cycle', supplierCode: 'SUP001', plotCode: 'P002',
          plotName: null,
          primaryPhone: null, additionalPhones: [],
          village: null, district: null, province: null,
          latitude: null, longitude: null, rai: null,
          crop: null, variety: null, cycleLabel: 'close_and_start_new_cycle-ish', lotNo: null, supplierLotNo: null,
          poNumber: null, pCode: null,
          oracleSupplierCode: null, oracleInvoice: null, refAccount: null,
          plantingDate: null, plantCount: null, expectedYieldFull: null, expectedYieldUnit: null,
          harvestYield: null, finalYieldAfterClean: null,
          harvestDate: null, finalNote: null,
        },
      })],
    }));
    renderModal();
    await doPreview();

    // cycleLabel deliberately resembles a different action's name — the
    // rendered label must still come straight from r.action, untouched.
    expect(await screen.findByText('แก้รอบปัจจุบัน')).toBeTruthy();
    expect(screen.queryByText('จบรอบเดิม + เริ่มรอบใหม่')).toBeNull();
  });
});

describe('PlotImportModal — workflow help copy (round 8-2.7.1)', () => {
  it('shows the four common workflows as the primary help copy, including start_next_cycle and reactivate_plot_with_cycle (round 8-6J)', () => {
    renderModal();

    expect(screen.getByText(/การกระทำหลัก 4 แบบ/)).toBeTruthy();
    const items = screen.getAllByRole('listitem');
    expect(items).toHaveLength(4);
    const actionNames = items.map((li) => li.querySelector('span.font-mono')?.textContent);
    expect(actionNames).toEqual([
      'create_plot_with_cycle', 'update_current_cycle', 'start_next_cycle', 'reactivate_plot_with_cycle',
    ]);
  });

  it('shows the start_next_cycle auto-detect warning copy', () => {
    renderModal();

    expect(screen.getByText(
      'ระบบจะตรวจสถานะแปลงให้เอง หากไม่มีรอบเปิดอยู่จะเริ่มรอบใหม่ทันที '
      + 'หากมีรอบเปิดอยู่จะปิดรอบเดิมเป็นเก็บเกี่ยวแล้วและเริ่มรอบใหม่ในครั้งเดียว',
    )).toBeTruthy();
  });

  it('puts start_new_cycle and close_and_start_new_cycle under a "กรณีพิเศษ/ไฟล์เก่า" note, not among the three common workflows', () => {
    renderModal();

    const specialCase = screen.getByText('กรณีพิเศษ/ไฟล์เก่า:');
    expect(specialCase.parentElement?.textContent).toContain('start_new_cycle');
    expect(specialCase.parentElement?.textContent).toContain('close_and_start_new_cycle');
    // The three bulleted workflow items are exactly create/update/start_next —
    // neither legacy rollover action is one of them.
    const items = screen.getAllByRole('listitem');
    const actionNames = items.map((li) => li.querySelector('span.font-mono')?.textContent);
    expect(actionNames).not.toContain('start_new_cycle');
    expect(actionNames).not.toContain('close_and_start_new_cycle');
  });
});

describe('PlotImportModal — validation report download', () => {
  it('clicking the report button calls downloadPlotImportPreviewReport with the file', async () => {
    previewMock.mockResolvedValue(preview());
    previewReportMock.mockResolvedValue(reportFile());
    renderModal();
    const file = await doPreview();

    fireEvent.click(await screen.findByRole('button', { name: 'ดาวน์โหลดผลการตรวจสอบ' }));

    await waitFor(() => expect(previewReportMock).toHaveBeenCalledWith(file));
    // does not touch the JSON preview state or auto-close the modal
    expect(previewMock).toHaveBeenCalledTimes(1);
  });

  it('shows "ดาวน์โหลดรายการที่ต้องแก้" instead when the preview has errors', async () => {
    previewMock.mockResolvedValue(preview({ validRows: 0, errorRows: 1, rows: [row({ status: 'error' })] }));
    renderModal();
    await doPreview();

    expect(await screen.findByRole('button', { name: 'ดาวน์โหลดรายการที่ต้องแก้' })).toBeTruthy();
  });

  it('revokes the object URL after triggering the download', async () => {
    const revokeSpy = vi.spyOn(URL, 'revokeObjectURL');
    previewMock.mockResolvedValue(preview());
    previewReportMock.mockResolvedValue(reportFile());
    renderModal();
    await doPreview();

    fireEvent.click(await screen.findByRole('button', { name: 'ดาวน์โหลดผลการตรวจสอบ' }));
    await waitFor(() => expect(previewReportMock).toHaveBeenCalled());
    await vi.waitFor(() => expect(revokeSpy).toHaveBeenCalled(), { timeout: 2000 });
  });
});

describe('PlotImportModal — commit (single mutation, exactly one request)', () => {
  it('clicking ยืนยันนำเข้า calls commitPlotImportWithReport exactly once and NEVER the legacy JSON commit', async () => {
    previewMock.mockResolvedValue(preview());
    commitReportMock.mockResolvedValue({ outcome: 'completed', report: reportFile({ kind: 'completed', filename: 'plot-import-result-x.xlsx' }) });
    renderModal();
    const file = await doPreview();

    const commitBtn = await screen.findByRole('button', { name: 'ยืนยันนำเข้า' });
    await waitFor(() => expect((commitBtn as HTMLButtonElement).disabled).toBe(false));
    fireEvent.click(commitBtn);

    await waitFor(() => expect(commitReportMock).toHaveBeenCalledWith(file, null));
    expect(commitReportMock).toHaveBeenCalledTimes(1);
    expect(commitJsonMock).not.toHaveBeenCalled();
  });

  it('rapid double-click while pending calls the commit mutation exactly once', async () => {
    previewMock.mockResolvedValue(preview());
    let resolveCommit!: (v: { outcome: 'completed'; report: PlotImportReportFile }) => void;
    commitReportMock.mockReturnValue(new Promise((res) => { resolveCommit = res; }));
    renderModal();
    await doPreview();

    const commitBtn = (await screen.findByRole('button', { name: 'ยืนยันนำเข้า' })) as HTMLButtonElement;
    await waitFor(() => expect(commitBtn.disabled).toBe(false));

    fireEvent.click(commitBtn);
    await waitFor(() => expect(commitBtn.disabled).toBe(true));
    fireEvent.click(commitBtn);
    fireEvent.click(commitBtn);

    expect(commitReportMock).toHaveBeenCalledTimes(1);

    resolveCommit({ outcome: 'completed', report: reportFile({ kind: 'completed' }) });
    expect(await screen.findByText('นำเข้าสำเร็จ')).toBeTruthy();
  });

  it('disables Commit, file input, Preview, validation-report download, and Close while pending; shows "กำลังนำเข้า..."', async () => {
    previewMock.mockResolvedValue(preview());
    let resolveCommit!: (v: unknown) => void;
    commitReportMock.mockReturnValue(new Promise((res) => { resolveCommit = res; }));
    renderModal();
    await doPreview();

    const commitBtn = (await screen.findByRole('button', { name: 'ยืนยันนำเข้า' })) as HTMLButtonElement;
    await waitFor(() => expect(commitBtn.disabled).toBe(false));
    fireEvent.click(commitBtn);

    await screen.findByText('กำลังนำเข้า...');
    expect((screen.getByLabelText('เลือกไฟล์ Excel') as HTMLInputElement).disabled).toBe(true);
    expect((screen.getByRole('button', { name: 'ตรวจสอบไฟล์' }) as HTMLButtonElement).disabled).toBe(true);
    expect((screen.getByRole('button', { name: 'ดาวน์โหลดผลการตรวจสอบ' }) as HTMLButtonElement).disabled).toBe(true);
    expect((screen.getByRole('button', { name: 'ยกเลิก' }) as HTMLButtonElement).disabled).toBe(true);
    expect((screen.getByLabelText('ปิด') as HTMLButtonElement).disabled).toBe(true);

    resolveCommit({ outcome: 'completed', report: reportFile({ kind: 'completed' }) });
    await screen.findByText('นำเข้าสำเร็จ');
  });
});

describe('PlotImportModal — commit success', () => {
  it('auto-downloads the Completed workbook and calls onImported exactly once', async () => {
    const createSpy = vi.spyOn(URL, 'createObjectURL');
    previewMock.mockResolvedValue(preview());
    const completedReport = reportFile({ kind: 'completed', filename: 'plot-import-result-x.xlsx' });
    commitReportMock.mockResolvedValue({ outcome: 'completed', report: completedReport });
    const { onImported } = renderModal();
    await doPreview();

    const commitBtn = await screen.findByRole('button', { name: 'ยืนยันนำเข้า' });
    await waitFor(() => expect((commitBtn as HTMLButtonElement).disabled).toBe(false));
    fireEvent.click(commitBtn);

    await screen.findByText('นำเข้าสำเร็จ');
    expect(createSpy).toHaveBeenCalledWith(completedReport.blob);
    expect(onImported).toHaveBeenCalledTimes(1);
  });

  it('shows the action-count summary derived from the preview rows', async () => {
    previewMock.mockResolvedValue(preview({
      totalRows: 2, validRows: 2, errorRows: 0,
      rows: [
        row({ rowNumber: 3, action: 'create_plot_with_cycle' }),
        row({ rowNumber: 4, action: 'close_and_start_new_cycle', plotCode: 'P003' }),
      ],
    }));
    commitReportMock.mockResolvedValue({ outcome: 'completed', report: reportFile({ kind: 'completed' }) });
    renderModal();
    await doPreview();

    const commitBtn = await screen.findByRole('button', { name: 'ยืนยันนำเข้า' });
    await waitFor(() => expect((commitBtn as HTMLButtonElement).disabled).toBe(false));
    fireEvent.click(commitBtn);

    await screen.findByText('นำเข้าสำเร็จ');
    expect(screen.getByText(/สร้างแปลง 1/)).toBeTruthy();
    expect(screen.getByText(/จบรอบเดิม\+เริ่มใหม่ 1/)).toBeTruthy();
    expect(screen.getByText(/รวมทั้งหมด 2/)).toBeTruthy();
  });

  it('re-download uses the stored Blob, calls no API, and revokes the URL', async () => {
    const createSpy = vi.spyOn(URL, 'createObjectURL');
    const revokeSpy = vi.spyOn(URL, 'revokeObjectURL');
    previewMock.mockResolvedValue(preview());
    const completedReport = reportFile({ kind: 'completed', filename: 'plot-import-result-x.xlsx' });
    commitReportMock.mockResolvedValue({ outcome: 'completed', report: completedReport });
    renderModal();
    await doPreview();

    const commitBtn = await screen.findByRole('button', { name: 'ยืนยันนำเข้า' });
    await waitFor(() => expect((commitBtn as HTMLButtonElement).disabled).toBe(false));
    fireEvent.click(commitBtn);
    await screen.findByText('นำเข้าสำเร็จ');

    createSpy.mockClear();
    commitReportMock.mockClear();

    fireEvent.click(screen.getByRole('button', { name: 'ดาวน์โหลดผลการนำเข้าอีกครั้ง' }));

    expect(createSpy).toHaveBeenCalledWith(completedReport.blob); // same Blob instance
    expect(commitReportMock).not.toHaveBeenCalled(); // no re-commit
    await vi.waitFor(() => expect(revokeSpy).toHaveBeenCalled(), { timeout: 2000 });
  });

  it('permanently hides Commit for this modal instance after success', async () => {
    previewMock.mockResolvedValue(preview());
    commitReportMock.mockResolvedValue({ outcome: 'completed', report: reportFile({ kind: 'completed' }) });
    renderModal();
    await doPreview();

    const commitBtn = await screen.findByRole('button', { name: 'ยืนยันนำเข้า' });
    await waitFor(() => expect((commitBtn as HTMLButtonElement).disabled).toBe(false));
    fireEvent.click(commitBtn);
    await screen.findByText('นำเข้าสำเร็จ');

    expect(screen.queryByRole('button', { name: 'ยืนยันนำเข้า' })).toBeNull();
    expect((screen.getByLabelText('เลือกไฟล์ Excel') as HTMLInputElement).disabled).toBe(true);
  });
});

describe('PlotImportModal — commit blocked (422 workbook, nothing imported)', () => {
  it('does not call onImported and shows that nothing was imported', async () => {
    previewMock.mockResolvedValueOnce(preview()).mockResolvedValueOnce(preview({
      validRows: 0, errorRows: 1, rows: [row({ status: 'error', message: 'เปลี่ยนไปแล้ว' })],
    }));
    commitReportMock.mockResolvedValue({ outcome: 'blocked', report: reportFile({ kind: 'validation' }) });
    const { onImported } = renderModal();
    await doPreview();

    const commitBtn = await screen.findByRole('button', { name: 'ยืนยันนำเข้า' });
    await waitFor(() => expect((commitBtn as HTMLButtonElement).disabled).toBe(false));
    fireEvent.click(commitBtn);

    expect(await screen.findByText('ไม่มีข้อมูลในไฟล์นี้ถูกนำเข้า')).toBeTruthy();
    expect(onImported).not.toHaveBeenCalled();
    expect(screen.queryByText('นำเข้าสำเร็จ')).toBeNull();
  });

  it('offers a download button for the returned (blocked) report', async () => {
    previewMock.mockResolvedValue(preview());
    commitReportMock.mockResolvedValue({ outcome: 'blocked', report: reportFile({ kind: 'validation' }) });
    renderModal();
    await doPreview();

    const commitBtn = await screen.findByRole('button', { name: 'ยืนยันนำเข้า' });
    await waitFor(() => expect((commitBtn as HTMLButtonElement).disabled).toBe(false));
    fireEvent.click(commitBtn);

    expect(await screen.findByRole('button', { name: 'ดาวน์โหลดรายการที่ต้องแก้' })).toBeTruthy();
  });

  it('automatically re-runs a read-only JSON preview to refresh the table (never auto-commits)', async () => {
    const refreshed = preview({
      validRows: 0, errorRows: 1, rows: [row({ status: 'error', message: 'เปลี่ยนไปแล้ว' })],
    });
    previewMock.mockResolvedValueOnce(preview()).mockResolvedValueOnce(refreshed);
    commitReportMock.mockResolvedValue({ outcome: 'blocked', report: reportFile({ kind: 'validation' }) });
    renderModal();
    await doPreview();

    const commitBtn = await screen.findByRole('button', { name: 'ยืนยันนำเข้า' });
    await waitFor(() => expect((commitBtn as HTMLButtonElement).disabled).toBe(false));
    fireEvent.click(commitBtn);

    await waitFor(() => expect(previewMock).toHaveBeenCalledTimes(2));
    expect(await screen.findByText('เปลี่ยนไปแล้ว')).toBeTruthy();
    expect(commitReportMock).toHaveBeenCalledTimes(1); // the refresh never re-commits
  });
});

describe('PlotImportModal — unexpected commit error / network ambiguity', () => {
  it('a confirmed HTTP error (status present) states nothing was imported', async () => {
    previewMock.mockResolvedValue(preview());
    commitReportMock.mockRejectedValue(new PlotImportReportError('มีการเปลี่ยนแปลงที่ขัดแย้ง', 409));
    renderModal();
    await doPreview();

    const commitBtn = await screen.findByRole('button', { name: 'ยืนยันนำเข้า' });
    await waitFor(() => expect((commitBtn as HTMLButtonElement).disabled).toBe(false));
    fireEvent.click(commitBtn);

    expect(await screen.findByText('นำเข้าไม่สำเร็จ')).toBeTruthy();
    expect(screen.getByText('ไม่มีข้อมูลในไฟล์นี้ถูกนำเข้า')).toBeTruthy();
  });

  it('network ambiguity (status null) never claims data was definitely not imported, and never auto-retries', async () => {
    previewMock.mockResolvedValue(preview());
    commitReportMock.mockRejectedValue(new PlotImportReportError('Network Error', null));
    renderModal();
    await doPreview();

    const commitBtn = await screen.findByRole('button', { name: 'ยืนยันนำเข้า' });
    await waitFor(() => expect((commitBtn as HTMLButtonElement).disabled).toBe(false));
    fireEvent.click(commitBtn);

    expect(await screen.findByText('ไม่สามารถยืนยันผลการนำเข้าได้ กรุณารีเฟรชรายการแปลงและตรวจสอบรอบปลูกก่อนลองอีกครั้ง')).toBeTruthy();
    expect(screen.queryByText('ไม่มีข้อมูลในไฟล์นี้ถูกนำเข้า')).toBeNull();
    // no automatic retry — exactly the one click's call
    expect(commitReportMock).toHaveBeenCalledTimes(1);
  });
});

describe('PlotImportModal — misc', () => {
  it('changing the file resets preview, report state, and commit outcome', async () => {
    previewMock.mockResolvedValue(preview());
    commitReportMock.mockResolvedValue({ outcome: 'completed', report: reportFile({ kind: 'completed' }) });
    renderModal();
    await doPreview();
    const commitBtn = await screen.findByRole('button', { name: 'ยืนยันนำเข้า' });
    await waitFor(() => expect((commitBtn as HTMLButtonElement).disabled).toBe(false));
    fireEvent.click(commitBtn);
    await screen.findByText('นำเข้าสำเร็จ');

    selectFile(); // pick a (new) file

    expect(screen.queryByText('นำเข้าสำเร็จ')).toBeNull();
    expect(screen.queryByText('P101')).toBeNull();
    expect(screen.getByRole('button', { name: 'ยืนยันนำเข้า' })).toBeTruthy(); // Commit is back
  });

  it('downloads the template without touching preview/commit', async () => {
    templateMock.mockResolvedValue(new Blob(['x']));
    renderModal();

    fireEvent.click(screen.getByRole('button', { name: 'ดาวน์โหลดเทมเพลต' }));

    await waitFor(() => expect(templateMock).toHaveBeenCalled());
    expect(previewMock).not.toHaveBeenCalled();
    expect(commitReportMock).not.toHaveBeenCalled();
    expect(commitJsonMock).not.toHaveBeenCalled();
  });

  // --- round 8-6B Part H items 31/32/33/35: generic template regression ---

  it('generic download calls downloadPlotImportTemplate() with NO argument (item 31)', async () => {
    templateMock.mockResolvedValue(new Blob(['x']));
    renderModal();

    fireEvent.click(screen.getByRole('button', { name: 'ดาวน์โหลดเทมเพลต' }));

    await waitFor(() => expect(templateMock).toHaveBeenCalled());
    expect(templateMock).toHaveBeenCalledWith();
  });

  it('generic template download still succeeds (item 32)', async () => {
    const blob = new Blob(['x']);
    templateMock.mockResolvedValue(blob);
    renderModal();

    fireEvent.click(screen.getByRole('button', { name: 'ดาวน์โหลดเทมเพลต' }));

    await waitFor(() => expect(templateMock).toHaveBeenCalledTimes(1));
    // No error surfaced anywhere in the modal from the download itself.
    expect(screen.queryByText('ดาวน์โหลดเทมเพลตไม่สำเร็จ')).toBeNull();
  });

  it('shows guidance pointing to the Plots page "Excel ตามตัวกรอง" button for a filtered file (item 33)', () => {
    renderModal();

    expect(screen.getAllByText(/Excel ตามตัวกรอง/).length).toBeGreaterThan(0);
    expect(screen.getByText(/เลือก Supplier\/จังหวัดในหน้าแปลง/)).toBeTruthy();
  });

  it('explains the 4-sheet workbook structure (นำเข้ารอบใหม่/ข้อมูลปัจจุบัน/รายการที่ไม่รวม/ตัวอย่าง, round 8-6J)', () => {
    renderModal();

    expect(screen.getByText(/นำเข้ารอบใหม่/)).toBeTruthy();
    expect(screen.getByText(/ข้อมูลปัจจุบัน/)).toBeTruthy();
    expect(screen.getByText(/รายการที่ไม่รวม/)).toBeTruthy();
    expect(screen.getAllByText(/ตัวอย่าง/).length).toBeGreaterThan(0);
  });

  it('downloading the template never auto-commits or auto-uploads (item 35)', async () => {
    templateMock.mockResolvedValue(new Blob(['x']));
    renderModal();

    fireEvent.click(screen.getByRole('button', { name: 'ดาวน์โหลดเทมเพลต' }));
    await waitFor(() => expect(templateMock).toHaveBeenCalled());

    // No file was ever selected/previewed/committed as a side effect.
    expect(previewMock).not.toHaveBeenCalled();
    expect(commitReportMock).not.toHaveBeenCalled();
    expect(commitJsonMock).not.toHaveBeenCalled();
    expect(screen.queryByText('นำเข้าสำเร็จ')).toBeNull();
  });

  it('shows — for a null cycleLabel in the preview table', async () => {
    previewMock.mockResolvedValue(preview({
      rows: [row({
        payload: {
          action: 'create_plot_with_cycle', supplierCode: 'SUP001', plotCode: 'P101',
          plotName: 'แปลงใหม่',
          primaryPhone: null, additionalPhones: [],
          village: null, district: null, province: null,
          latitude: null, longitude: null, rai: null,
          crop: 'พริก', variety: null, cycleLabel: null, lotNo: null, supplierLotNo: null,
          poNumber: null, pCode: null,
          oracleSupplierCode: null, oracleInvoice: null, refAccount: null,
          plantingDate: null, plantCount: null, expectedYieldFull: null, expectedYieldUnit: null,
          harvestYield: null, finalYieldAfterClean: null,
          harvestDate: null, finalNote: null,
        },
      })],
    }));
    renderModal();
    await doPreview();

    await screen.findByText('ชื่อรอบปลูก');
    expect(screen.getAllByText('—').length).toBeGreaterThan(0);
  });
});

describe('PlotImportModal — preview-state binding (round 8-2.7.2)', () => {
  const sampleState = {
    fileSha256: 'a'.repeat(64),
    startNextRows: [
      {
        rowNumber: 3, supplierCode: 'SUP010', plotCode: 'P010',
        resolvedAction: 'start_new_cycle', activeCycleId: null,
      },
    ],
  };

  function startNextPreview(previewState: unknown) {
    return preview({
      rows: [row({ action: 'start_next_cycle', plotCode: 'P010', resolvedAction: 'start_new_cycle' })],
      previewState: previewState as PlotImportPreview['previewState'],
    });
  }

  it('stores previewState from the preview response and echoes it on commit', async () => {
    previewMock.mockResolvedValue(startNextPreview(sampleState));
    commitReportMock.mockResolvedValue({ outcome: 'completed', report: reportFile({ kind: 'completed' }) });
    renderModal();
    const file = await doPreview();

    const commitBtn = await screen.findByRole('button', { name: 'ยืนยันนำเข้า' });
    await waitFor(() => expect((commitBtn as HTMLButtonElement).disabled).toBe(false));
    fireEvent.click(commitBtn);

    await waitFor(() => expect(commitReportMock).toHaveBeenCalledWith(file, sampleState));
    expect(commitReportMock).toHaveBeenCalledTimes(1); // still exactly one request
  });

  it('sends null previewState for a legacy file (no start_next / no previewState)', async () => {
    previewMock.mockResolvedValue(preview()); // no previewState field
    commitReportMock.mockResolvedValue({ outcome: 'completed', report: reportFile({ kind: 'completed' }) });
    renderModal();
    const file = await doPreview();

    const commitBtn = await screen.findByRole('button', { name: 'ยืนยันนำเข้า' });
    await waitFor(() => expect((commitBtn as HTMLButtonElement).disabled).toBe(false));
    fireEvent.click(commitBtn);

    await waitFor(() => expect(commitReportMock).toHaveBeenCalledWith(file, null));
  });

  it('shows the state-conflict banner, disables Commit, and does NOT auto-retry on a preview_state_conflict', async () => {
    previewMock.mockResolvedValue(startNextPreview(sampleState));
    commitReportMock.mockRejectedValue(new PlotImportReportError(
      'สถานะรอบปลูกมีการเปลี่ยนแปลง กรุณาตรวจสอบไฟล์อีกครั้งก่อนนำเข้า', 409, 'preview_state_conflict',
    ));
    const { onImported } = renderModal();
    await doPreview();

    const commitBtn = await screen.findByRole('button', { name: 'ยืนยันนำเข้า' });
    await waitFor(() => expect((commitBtn as HTMLButtonElement).disabled).toBe(false));
    fireEvent.click(commitBtn);

    expect(await screen.findByText('สถานะรอบปลูกมีการเปลี่ยนแปลง กรุณาตรวจสอบไฟล์อีกครั้งก่อนนำเข้า')).toBeTruthy();
    expect(onImported).not.toHaveBeenCalled();
    expect(screen.queryByText('นำเข้าสำเร็จ')).toBeNull();
    // exactly the one commit call — never a silent re-commit
    expect(commitReportMock).toHaveBeenCalledTimes(1);
    // Commit is disabled until the user Previews again
    expect((screen.getByRole('button', { name: 'ยืนยันนำเข้า' }) as HTMLButtonElement).disabled).toBe(true);
  });

  // Round 8-6E — the original version of this test used back-to-back
  // `mockResolvedValueOnce` calls, which settle before the test ever gets a
  // chance to observe the PENDING window in between — so it could never
  // have caught the real race (a stale previewState reaching Commit while a
  // re-preview was still in flight). Rewritten with a deferred Promise for
  // the second Preview so the pending window is real and observable.
  it('re-enables Commit only once a fresh Preview actually resolves, and never before', async () => {
    const second = deferred<PlotImportPreview>();
    previewMock
      .mockResolvedValueOnce(startNextPreview(sampleState))
      .mockReturnValueOnce(second.promise);
    commitReportMock.mockRejectedValueOnce(new PlotImportReportError(
      'สถานะรอบปลูกมีการเปลี่ยนแปลง กรุณาตรวจสอบไฟล์อีกครั้งก่อนนำเข้า', 409, 'preview_state_conflict',
    ));
    renderModal();
    await doPreview();

    const commitBtn = await screen.findByRole('button', { name: 'ยืนยันนำเข้า' });
    await waitFor(() => expect((commitBtn as HTMLButtonElement).disabled).toBe(false));
    fireEvent.click(commitBtn);
    await screen.findByText(/สถานะรอบปลูกมีการเปลี่ยนแปลง/);

    // "ตรวจสอบไฟล์อีกครั้ง" in the conflict banner re-previews (no auto-commit)
    fireEvent.click(screen.getByRole('button', { name: 'ตรวจสอบไฟล์อีกครั้ง' }));
    await waitFor(() => expect(previewMock).toHaveBeenCalledTimes(2));

    // The second Preview has NOT resolved yet — Commit must still be
    // disabled right now, not just "eventually".
    expect((screen.getByRole('button', { name: 'ยืนยันนำเข้า' }) as HTMLButtonElement).disabled).toBe(true);
    expect(commitReportMock).toHaveBeenCalledTimes(1); // still no re-commit

    second.resolve(startNextPreview({ ...sampleState, fileSha256: 'b'.repeat(64) }));
    await waitFor(() =>
      expect((screen.getByRole('button', { name: 'ยืนยันนำเข้า' }) as HTMLButtonElement).disabled).toBe(false),
    );
    expect(commitReportMock).toHaveBeenCalledTimes(1); // the re-preview itself never auto-commits
  });

  it('while a re-preview is pending, clicking Commit (if somehow reachable) never calls the commit API again (round 8-6E item 3)', async () => {
    const second = deferred<PlotImportPreview>();
    previewMock
      .mockResolvedValueOnce(startNextPreview(sampleState))
      .mockReturnValueOnce(second.promise);
    commitReportMock.mockRejectedValueOnce(new PlotImportReportError(
      'สถานะรอบปลูกมีการเปลี่ยนแปลง กรุณาตรวจสอบไฟล์อีกครั้งก่อนนำเข้า', 409, 'preview_state_conflict',
    ));
    renderModal();
    await doPreview();

    const commitBtn = await screen.findByRole('button', { name: 'ยืนยันนำเข้า' });
    await waitFor(() => expect((commitBtn as HTMLButtonElement).disabled).toBe(false));
    fireEvent.click(commitBtn);
    await screen.findByText(/สถานะรอบปลูกมีการเปลี่ยนแปลง/);
    fireEvent.click(screen.getByRole('button', { name: 'ตรวจสอบไฟล์อีกครั้ง' }));
    await waitFor(() => expect(previewMock).toHaveBeenCalledTimes(2));

    // A disabled <button> never dispatches a click in a real browser (or
    // jsdom) — this click is a no-op either way, but asserts the invariant
    // explicitly rather than just trusting the disabled attribute.
    fireEvent.click(screen.getByRole('button', { name: 'ยืนยันนำเข้า' }));
    expect(commitReportMock).toHaveBeenCalledTimes(1);

    second.resolve(startNextPreview(sampleState)); // let it settle, avoid an unhandled-rejection-style warning
    await waitFor(() => expect((screen.getByRole('button', { name: 'ยืนยันนำเข้า' }) as HTMLButtonElement).disabled).toBe(false));
  });

  it('a re-preview that resolves sends the SECOND previewState on the next commit, never the first (round 8-6E item 4)', async () => {
    const second = deferred<PlotImportPreview>();
    const secondState = { ...sampleState, fileSha256: 'b'.repeat(64) };
    previewMock
      .mockResolvedValueOnce(startNextPreview(sampleState))
      .mockReturnValueOnce(second.promise);
    commitReportMock
      .mockRejectedValueOnce(new PlotImportReportError(
        'สถานะรอบปลูกมีการเปลี่ยนแปลง กรุณาตรวจสอบไฟล์อีกครั้งก่อนนำเข้า', 409, 'preview_state_conflict',
      ))
      .mockResolvedValueOnce({ outcome: 'completed', report: reportFile({ kind: 'completed' }) });
    renderModal();
    const file = await doPreview();

    const commitBtn = await screen.findByRole('button', { name: 'ยืนยันนำเข้า' });
    await waitFor(() => expect((commitBtn as HTMLButtonElement).disabled).toBe(false));
    fireEvent.click(commitBtn);
    await screen.findByText(/สถานะรอบปลูกมีการเปลี่ยนแปลง/);
    fireEvent.click(screen.getByRole('button', { name: 'ตรวจสอบไฟล์อีกครั้ง' }));
    await waitFor(() => expect(previewMock).toHaveBeenCalledTimes(2));

    second.resolve(startNextPreview(secondState));
    await waitFor(() =>
      expect((screen.getByRole('button', { name: 'ยืนยันนำเข้า' }) as HTMLButtonElement).disabled).toBe(false),
    );

    fireEvent.click(screen.getByRole('button', { name: 'ยืนยันนำเข้า' }));
    await waitFor(() => expect(commitReportMock).toHaveBeenCalledTimes(2));
    expect(commitReportMock).toHaveBeenLastCalledWith(file, secondState);
  });

  it('a re-preview that REJECTS keeps Commit disabled and never reuses the first previewState (round 8-6E item 5)', async () => {
    const second = deferred<PlotImportPreview>();
    previewMock
      .mockResolvedValueOnce(startNextPreview(sampleState))
      .mockReturnValueOnce(second.promise);
    commitReportMock.mockRejectedValueOnce(new PlotImportReportError(
      'สถานะรอบปลูกมีการเปลี่ยนแปลง กรุณาตรวจสอบไฟล์อีกครั้งก่อนนำเข้า', 409, 'preview_state_conflict',
    ));
    renderModal();
    await doPreview();

    const commitBtn = await screen.findByRole('button', { name: 'ยืนยันนำเข้า' });
    await waitFor(() => expect((commitBtn as HTMLButtonElement).disabled).toBe(false));
    fireEvent.click(commitBtn);
    await screen.findByText(/สถานะรอบปลูกมีการเปลี่ยนแปลง/);
    fireEvent.click(screen.getByRole('button', { name: 'ตรวจสอบไฟล์อีกครั้ง' }));
    await waitFor(() => expect(previewMock).toHaveBeenCalledTimes(2));

    second.reject(new Error('preview failed'));
    // Part C item 1 — the earlier stateConflict banner may keep showing.
    await waitFor(() => expect(screen.queryByText(/สถานะรอบปลูกมีการเปลี่ยนแปลง/)).toBeTruthy());
    expect((screen.getByRole('button', { name: 'ยืนยันนำเข้า' }) as HTMLButtonElement).disabled).toBe(true);
    fireEvent.click(screen.getByRole('button', { name: 'ยืนยันนำเข้า' }));
    expect(commitReportMock).toHaveBeenCalledTimes(1); // never re-sent with the stale first previewState
  });

  it('binds Commit to the exact file+previewState from the click-time render (round 8-6E items 1/2)', async () => {
    const commitDeferred = deferred<{ outcome: 'completed'; report: PlotImportReportFile }>();
    previewMock.mockResolvedValueOnce(startNextPreview(sampleState));
    commitReportMock.mockReturnValueOnce(commitDeferred.promise);
    renderModal();
    const file = await doPreview();

    const commitBtn = await screen.findByRole('button', { name: 'ยืนยันนำเข้า' });
    await waitFor(() => expect((commitBtn as HTMLButtonElement).disabled).toBe(false));
    fireEvent.click(commitBtn);

    // Asserted while the commit request is still in flight (never resolved
    // yet) — the exact click-time file+previewState were already sent as
    // explicit mutation variables, not read from a live component closure.
    await waitFor(() => expect(commitReportMock).toHaveBeenCalledWith(file, sampleState));
    expect(commitReportMock).toHaveBeenCalledTimes(1);

    commitDeferred.resolve({ outcome: 'completed', report: reportFile({ kind: 'completed' }) });
    await screen.findByText('นำเข้าสำเร็จ');
  });

  it('a start_next_cycle file whose preview response is missing previewState blocks Commit and shows guidance (round 8-6E item 6)', async () => {
    previewMock.mockResolvedValue(preview({
      rows: [row({ action: 'start_next_cycle', plotCode: 'P099', resolvedAction: 'start_new_cycle' })],
      // no previewState field at all — the defensive case Part C item 5 guards.
    }));
    renderModal();
    await doPreview();

    expect(await screen.findByText('P099')).toBeTruthy();
    expect((screen.getByRole('button', { name: 'ยืนยันนำเข้า' }) as HTMLButtonElement).disabled).toBe(true);
    expect(screen.getByText(/ไม่พบข้อมูลตรวจสอบสถานะรอบปลูก/)).toBeTruthy();
  });

  it('a state conflict never triggers an automatic re-preview or re-commit on its own (round 8-6E item 12)', async () => {
    previewMock.mockResolvedValueOnce(startNextPreview(sampleState));
    commitReportMock.mockRejectedValueOnce(new PlotImportReportError(
      'สถานะรอบปลูกมีการเปลี่ยนแปลง กรุณาตรวจสอบไฟล์อีกครั้งก่อนนำเข้า', 409, 'preview_state_conflict',
    ));
    renderModal();
    await doPreview();

    const commitBtn = await screen.findByRole('button', { name: 'ยืนยันนำเข้า' });
    await waitFor(() => expect((commitBtn as HTMLButtonElement).disabled).toBe(false));
    fireEvent.click(commitBtn);
    await screen.findByText(/สถานะรอบปลูกมีการเปลี่ยนแปลง/);

    expect(previewMock).toHaveBeenCalledTimes(1); // no auto re-preview
    expect(commitReportMock).toHaveBeenCalledTimes(1); // no auto re-commit
  });

  it('conflict banner shows the backend message and changed row numbers, never activeCycleId/fileSha256 (round 8-6E items 10/11)', async () => {
    previewMock.mockResolvedValue(preview({
      rows: [row({
        action: 'start_next_cycle', plotCode: 'P010',
        resolvedAction: 'close_and_start_new_cycle', currentCycleNo: 2, currentCycleLabel: 'aug2026',
      })],
      previewState: {
        fileSha256: 'deadbeef'.repeat(8),
        startNextRows: [{
          rowNumber: 3, supplierCode: 'SUP010', plotCode: 'P010',
          resolvedAction: 'close_and_start_new_cycle', activeCycleId: 'secret-cycle-uuid-999',
        }],
      } as PlotImportPreview['previewState'],
    }));
    commitReportMock.mockRejectedValueOnce(new PlotImportReportError(
      'พบแถวที่ผลลัพธ์เปลี่ยนไปจากที่ตรวจสอบไว้ กรุณาตรวจสอบไฟล์อีกครั้ง',
      409, 'preview_state_conflict', 'resolution_changed', [3, 5, 8],
    ));
    renderModal();
    await doPreview();

    const commitBtn = await screen.findByRole('button', { name: 'ยืนยันนำเข้า' });
    await waitFor(() => expect((commitBtn as HTMLButtonElement).disabled).toBe(false));
    fireEvent.click(commitBtn);

    expect(await screen.findByText('พบแถวที่ผลลัพธ์เปลี่ยนไปจากที่ตรวจสอบไว้ กรุณาตรวจสอบไฟล์อีกครั้ง')).toBeTruthy();
    expect(screen.getByText(/แถวที่ต้องตรวจสอบ: 3, 5, 8/)).toBeTruthy();
    expect(screen.queryByText(/secret-cycle-uuid-999/)).toBeNull();
    expect(screen.queryByText(/deadbeef/)).toBeNull();
  });

  it('maps reason=missing_preview_state to the exact required Thai text (round 8-6F Part D)', async () => {
    previewMock.mockResolvedValue(startNextPreview(sampleState));
    commitReportMock.mockRejectedValueOnce(new PlotImportReportError(
      'some backend detail sentence not meant to be shown verbatim', 409, 'preview_state_conflict', 'missing_preview_state',
    ));
    renderModal();
    await doPreview();
    const commitBtn = await screen.findByRole('button', { name: 'ยืนยันนำเข้า' });
    await waitFor(() => expect((commitBtn as HTMLButtonElement).disabled).toBe(false));
    fireEvent.click(commitBtn);

    expect(await screen.findByText('ข้อมูลตรวจสอบหมดอายุ กรุณากดตรวจสอบไฟล์อีกครั้งก่อนนำเข้า')).toBeTruthy();
  });

  it('maps reason=file_digest_mismatch to the exact required Thai text (round 8-6E Part D)', async () => {
    previewMock.mockResolvedValue(startNextPreview(sampleState));
    commitReportMock.mockRejectedValueOnce(new PlotImportReportError(
      'some backend detail sentence not meant to be shown verbatim', 409, 'preview_state_conflict', 'file_digest_mismatch',
    ));
    renderModal();
    await doPreview();
    const commitBtn = await screen.findByRole('button', { name: 'ยืนยันนำเข้า' });
    await waitFor(() => expect((commitBtn as HTMLButtonElement).disabled).toBe(false));
    fireEvent.click(commitBtn);

    expect(await screen.findByText('ไฟล์มีการเปลี่ยนแปลงหลังการตรวจสอบ กรุณาตรวจสอบไฟล์ใหม่')).toBeTruthy();
  });

  it('the commit-outcome error banner renders outside the scrollable preview area, near the footer (round 8-6F Part D)', async () => {
    previewMock.mockResolvedValue(preview());
    commitReportMock.mockRejectedValueOnce(new PlotImportReportError('เกิดข้อผิดพลาดที่ไม่คาดคิด', 500));
    renderModal();
    await doPreview();
    const commitBtn = await screen.findByRole('button', { name: 'ยืนยันนำเข้า' });
    await waitFor(() => expect((commitBtn as HTMLButtonElement).disabled).toBe(false));
    fireEvent.click(commitBtn);

    const banner = await screen.findByText('นำเข้าไม่สำเร็จ');
    const scrollableBody = document.querySelector('.overflow-y-auto');
    expect(scrollableBody).toBeTruthy();
    // The failure banner must NOT be a descendant of the scrollable preview
    // area — otherwise it can be scrolled out of view exactly like the
    // original bug report described ("ปุ่มไม่ได้เสีย" but the user never saw
    // why nothing happened).
    expect(scrollableBody?.contains(banner)).toBe(false);
  });

  it('modal stays open and never shows "นำเข้าสำเร็จ" when commit is blocked (round 8-6F Part D)', async () => {
    previewMock.mockResolvedValueOnce(preview()).mockResolvedValueOnce(preview());
    commitReportMock.mockResolvedValueOnce({ outcome: 'blocked', report: reportFile({ kind: 'validation' }) });
    renderModal();
    await doPreview();
    const commitBtn = await screen.findByRole('button', { name: 'ยืนยันนำเข้า' });
    await waitFor(() => expect((commitBtn as HTMLButtonElement).disabled).toBe(false));
    fireEvent.click(commitBtn);

    await screen.findByText('นำเข้าไม่สำเร็จ');
    expect(screen.queryByText('นำเข้าสำเร็จ')).toBeNull();
    expect(screen.getByRole('button', { name: 'ยกเลิก' })).toBeTruthy(); // modal still open, not auto-closed
  });

  it('modal stays open and never shows "นำเข้าสำเร็จ" when commit hits an unexpected error (round 8-6F Part D)', async () => {
    previewMock.mockResolvedValue(preview());
    commitReportMock.mockRejectedValueOnce(new PlotImportReportError('เกิดข้อผิดพลาดที่ไม่คาดคิด', 500));
    renderModal();
    await doPreview();
    const commitBtn = await screen.findByRole('button', { name: 'ยืนยันนำเข้า' });
    await waitFor(() => expect((commitBtn as HTMLButtonElement).disabled).toBe(false));
    fireEvent.click(commitBtn);

    await screen.findByText('นำเข้าไม่สำเร็จ');
    expect(screen.queryByText('นำเข้าสำเร็จ')).toBeNull();
    expect(screen.getByRole('button', { name: 'ยกเลิก' })).toBeTruthy();
  });

  it('changing the file clears preview + previewState (no stale binding left)', async () => {
    previewMock.mockResolvedValue(startNextPreview(sampleState));
    renderModal();
    await doPreview();
    expect(await screen.findByText('P010')).toBeTruthy();

    selectFile(); // new file → preview (and its previewState) reset
    expect(screen.queryByText('P010')).toBeNull();
    // Commit disabled until a fresh preview is run.
    expect((screen.getByRole('button', { name: 'ยืนยันนำเข้า' }) as HTMLButtonElement).disabled).toBe(true);
  });

  it('a failed Preview leaves no preview table and a disabled Commit (no previewState to send)', async () => {
    previewMock.mockRejectedValue(new Error('boom'));
    renderModal();
    selectFile();
    fireEvent.click(screen.getByRole('button', { name: 'ตรวจสอบไฟล์' }));

    await waitFor(() => expect(previewMock).toHaveBeenCalled());
    expect(screen.queryByText('P010')).toBeNull();
    expect((screen.getByRole('button', { name: 'ยืนยันนำเข้า' }) as HTMLButtonElement).disabled).toBe(true);
  });

  it('never renders activeCycleId or fileSha256 anywhere in the UI', async () => {
    previewMock.mockResolvedValue(preview({
      rows: [row({
        action: 'start_next_cycle', plotCode: 'P010',
        resolvedAction: 'close_and_start_new_cycle', currentCycleNo: 3, currentCycleLabel: 'aug2026',
      })],
      previewState: {
        fileSha256: 'deadbeef'.repeat(8),
        startNextRows: [{
          rowNumber: 3, supplierCode: 'SUP010', plotCode: 'P010',
          resolvedAction: 'close_and_start_new_cycle', activeCycleId: 'secret-cycle-uuid-123',
        }],
      },
    }));
    renderModal();
    await doPreview();

    expect(await screen.findByText('ปิดรอบ aug2026 + เริ่มรอบใหม่')).toBeTruthy();
    expect(screen.queryByText(/secret-cycle-uuid-123/)).toBeNull();
    expect(screen.queryByText(/deadbeef/)).toBeNull();
  });
});

// --- round 8-3E: primaryPhone/additionalPhones columns ----------------------

describe('PlotImportModal — access-phone columns (round 8-3E)', () => {
  it('shows the primary phone formatted, plus an additional-count summary', async () => {
    previewMock.mockResolvedValue(preview({
      rows: [row({
        payload: {
          action: 'create_plot_with_cycle', supplierCode: 'SUP001', plotCode: 'P101',
          plotName: 'แปลงใหม่', primaryPhone: '0845552162',
          additionalPhones: ['0855551234', '0866661234'],
          village: null, district: null, province: null,
          latitude: null, longitude: null, rai: null,
          crop: null, variety: null, cycleLabel: null, lotNo: null, supplierLotNo: null,
          poNumber: null, pCode: null,
          oracleSupplierCode: null, oracleInvoice: null, refAccount: null,
          plantingDate: null, plantCount: null, expectedYieldFull: null, expectedYieldUnit: null,
          harvestYield: null, finalYieldAfterClean: null,
          harvestDate: null, finalNote: null,
        },
      })],
    }));
    renderModal();
    await doPreview();

    expect(await screen.findByText(/084-555-2162/)).toBeTruthy();
    expect(screen.getByText(/\+2 เบอร์เสริม/)).toBeTruthy();
  });

  it('shows a dash for a row with no access-phone columns set', async () => {
    previewMock.mockResolvedValue(preview({ rows: [row({ payload: null })] }));
    renderModal();
    await doPreview();

    const cells = await screen.findAllByRole('cell');
    expect(cells.some((c) => c.textContent === '—')).toBe(true);
  });

  it('help text explains preserve-vs-replace semantics for existing plots', async () => {
    renderModal();
    expect(screen.getByText(/เว้นว่างทั้ง 2 ช่องในแปลงที่มีอยู่แล้ว/)).toBeTruthy();
    expect(screen.getByText(/แทนที่ชุดเบอร์ทั้งหมดของแปลงนั้น/)).toBeTruthy();
  });
});

// --- round 8-6J: status-aware template + reactivate_plot_with_cycle UX -----

describe('PlotImportModal — reactivate_plot_with_cycle (round 8-6J)', () => {
  it('shows the reactivate action label for a reactivate_plot_with_cycle row', async () => {
    previewMock.mockResolvedValue(preview({
      rows: [row({ action: 'reactivate_plot_with_cycle', plotCode: 'P002' })],
    }));
    renderModal();
    await doPreview();

    expect(await screen.findByText('เปิดใช้งานแปลง + เริ่มรอบปลูกใหม่')).toBeTruthy();
  });

  it('shows "สถานะแปลงปัจจุบัน: ปิดใช้งาน" for a reactivate row', async () => {
    previewMock.mockResolvedValue(preview({
      rows: [row({ action: 'reactivate_plot_with_cycle', plotCode: 'P002' })],
    }));
    renderModal();
    await doPreview();

    expect(await screen.findByText(/สถานะแปลงปัจจุบัน: ปิดใช้งาน/)).toBeTruthy();
  });

  it('shows "สถานะแปลงปัจจุบัน: ใช้งานอยู่" for a start_next_cycle row (no regression)', async () => {
    previewMock.mockResolvedValue(preview({
      rows: [row({ action: 'start_next_cycle', plotCode: 'P010', resolvedAction: 'start_new_cycle' })],
    }));
    renderModal();
    await doPreview();

    expect(await screen.findByText(/สถานะแปลงปัจจุบัน: ใช้งานอยู่/)).toBeTruthy();
  });

  it('shows no current-plot-status line for a create_plot_with_cycle row (no plot to have a status yet)', async () => {
    previewMock.mockResolvedValue(preview({
      rows: [row({ action: 'create_plot_with_cycle', plotCode: 'P101' })],
    }));
    renderModal();
    await doPreview();

    expect(screen.queryByText(/สถานะแปลงปัจจุบัน:/)).toBeNull();
  });

  it('shows the reactivation warning banner when the file has a reactivate row', async () => {
    previewMock.mockResolvedValue(preview({
      rows: [row({ action: 'reactivate_plot_with_cycle', plotCode: 'P002' })],
    }));
    renderModal();
    await doPreview();

    expect(await screen.findByText(/เมื่อยืนยันนำเข้า ระบบจะเปิดแปลงนี้กลับมาใช้งานและเริ่มรอบปลูกใหม่/)).toBeTruthy();
  });

  it('does NOT show the reactivation warning banner when no row is a reactivate row', async () => {
    previewMock.mockResolvedValue(preview({
      rows: [row({ action: 'create_plot_with_cycle', plotCode: 'P101' })],
    }));
    renderModal();
    await doPreview();

    expect(screen.queryByText(/เมื่อยืนยันนำเข้า ระบบจะเปิดแปลงนี้กลับมาใช้งานและเริ่มรอบปลูกใหม่/)).toBeNull();
  });

  it('completed banner shows a เปิดใช้งานแปลง count derived from the preview rows', async () => {
    previewMock.mockResolvedValue(preview({
      totalRows: 2, validRows: 2,
      rows: [
        row({ rowNumber: 3, action: 'reactivate_plot_with_cycle', plotCode: 'P002' }),
        row({ rowNumber: 4, action: 'create_plot_with_cycle', plotCode: 'P101' }),
      ],
    }));
    commitReportMock.mockResolvedValue({
      outcome: 'completed',
      report: { blob: new Blob(['x']), filename: 'r.xlsx', kind: 'completed', httpStatus: 200 },
    });
    renderModal();
    await doPreview();
    fireEvent.click(screen.getByRole('button', { name: 'ยืนยันนำเข้า' }));

    expect(await screen.findByText(/เปิดใช้งานแปลง 1/)).toBeTruthy();
  });

  it('help copy lists reactivate_plot_with_cycle and the currentPlotStatus column note', () => {
    renderModal();
    expect(screen.getAllByText(/reactivate_plot_with_cycle/).length).toBeGreaterThan(0);
    expect(screen.getAllByText(/currentPlotStatus/).length).toBeGreaterThan(0);
  });
});

// --- round 8-7A/8-7A.1/8-7B: final_plot action -----------------------------

describe('PlotImportModal — final_plot (round 8-7A/8-7B)', () => {
  function finalPlotPayload(over: Partial<PlotImportRowResult['payload']> = {}) {
    return {
      action: 'final_plot', supplierCode: 'SUP001', plotCode: 'P001', plotName: null,
      primaryPhone: null, additionalPhones: [],
      village: null, district: null, province: null,
      latitude: null, longitude: null, rai: null,
      crop: null, variety: null, cycleLabel: 'jul2026', lotNo: null, supplierLotNo: null,
      oracleSupplierCode: null, oracleInvoice: null, refAccount: null,
      poNumber: null, pCode: null,
      plantingDate: null, plantCount: null, expectedYieldFull: null, expectedYieldUnit: null,
      harvestYield: 1250, finalYieldAfterClean: 1180,
      harvestDate: '2026-07-28', finalNote: 'ผลผลิตหลังคัดแยก',
      ...over,
    };
  }

  const finalPlotState = {
    fileSha256: 'e'.repeat(64),
    startNextRows: [],
    finalPlotRows: [{
      rowNumber: 3, supplierCode: 'SUP001', plotCode: 'P001',
      plotUpdatedAt: '2026-07-01T00:00:00Z',
      activeCycleId: 'cycle-1', activeCycleNo: 3,
      activeCycleUpdatedAt: '2026-07-01T00:00:00Z',
      cycleLabel: 'jul2026', resolvedFinalInspectionRecordId: 'resolved-record-id-999',
    }],
  };

  function finalPlotPreview(overrides: Partial<PlotImportPreview> = {}) {
    return preview({
      rows: [row({
        action: 'final_plot', plotCode: 'P001', payload: finalPlotPayload(),
        finalRecordNote: 'พบบันทึกการตรวจที่ใช้สรุป',
      })],
      previewState: finalPlotState as PlotImportPreview['previewState'],
      ...overrides,
    });
  }

  it('shows the final_plot action label', async () => {
    previewMock.mockResolvedValue(finalPlotPreview());
    renderModal();
    await doPreview();

    expect(await screen.findByText('ลงผลผลิตสุดท้ายและปิดรอบปลูก')).toBeTruthy();
  });

  // Items 4/5 — round 8-10B fixed the unit at "kg" server-side; round 8-10C
  // displays it via the shared formatYieldQuantity helper, which also adds
  // thousands grouping ("1,250", not "1250") — same formatting every other
  // yield figure in the app uses.
  it('shows harvestYield and finalYieldAfterClean formatted in kg (item 4/5)', async () => {
    previewMock.mockResolvedValue(finalPlotPreview());
    renderModal();
    await doPreview();

    expect(await screen.findByText(/เก็บเกี่ยว 1,250 kg/)).toBeTruthy();
    expect(screen.getByText(/หลังทำความสะอาด 1,180 kg/)).toBeTruthy();
    expect(screen.getByText(/เก็บเกี่ยวเมื่อ 2026-07-28/)).toBeTruthy();
    expect(screen.getByText(/หมายเหตุ: ผลผลิตหลังคัดแยก/)).toBeTruthy();
  });

  // Item 6 — the unit is a fixed UI constant, never read off the payload:
  // even a stray/foreign field on the payload object (simulating an old or
  // malformed response shape smuggling a unit back in) must not change what
  // renders.
  it('never reads a unit from the payload — a stray payload field cannot change the displayed unit (item 6)', async () => {
    const staleShapePayload = {
      ...finalPlotPayload(),
      finalYieldUnit: 'g',
    } as unknown as PlotImportRowResult['payload'];
    previewMock.mockResolvedValue(finalPlotPreview({
      rows: [row({
        action: 'final_plot', plotCode: 'P001', payload: staleShapePayload,
        finalRecordNote: 'พบบันทึกการตรวจที่ใช้สรุป',
      })],
    }));
    renderModal();
    await doPreview();

    expect(await screen.findByText(/เก็บเกี่ยว 1,250 kg/)).toBeTruthy();
    expect(screen.queryByText(/1,250 g/)).toBeNull();
  });

  // Item 7 — null must render "—", never a blank gap or "NaN".
  it('null harvestYield/finalYieldAfterClean render "—" (item 7)', async () => {
    previewMock.mockResolvedValue(finalPlotPreview({
      rows: [row({
        action: 'final_plot', plotCode: 'P001',
        payload: finalPlotPayload({ harvestYield: null, finalYieldAfterClean: null }),
        finalRecordNote: 'พบบันทึกการตรวจที่ใช้สรุป',
      })],
    }));
    renderModal();
    await doPreview();

    expect(await screen.findByText('เก็บเกี่ยว — → หลังทำความสะอาด —')).toBeTruthy();
  });

  // Item 8 — a non-final action must never show final-harvest metadata at all.
  it('non-final action rows never render final-harvest summary or record-status text (item 8)', async () => {
    previewMock.mockResolvedValue(preview({
      rows: [row({ action: 'create_plot_with_cycle', plotCode: 'P101' })],
    }));
    renderModal();
    await doPreview();

    // The static help copy elsewhere on the page legitimately contains the
    // substring "เก็บเกี่ยว" (e.g. "...ปิดรอบเดิมเป็นเก็บเกี่ยวแล้ว...") — match
    // the harvest-summary LINE specifically, not the bare word.
    expect(screen.queryByText(/เก็บเกี่ยว.*หลังทำความสะอาด/)).toBeNull();
    expect(screen.queryByText(/พบบันทึกการตรวจที่ใช้สรุป|ไม่มีบันทึกการตรวจที่ใช้สรุป|ยังไม่สามารถตรวจสอบ/)).toBeNull();
  });

  // Items 9/10 — row.finalRecordNote (round 8-10B) is shown VERBATIM.
  it('shows "พบบันทึกการตรวจที่ใช้สรุป" verbatim from row.finalRecordNote (item 9)', async () => {
    previewMock.mockResolvedValue(finalPlotPreview({
      rows: [row({
        action: 'final_plot', plotCode: 'P001', payload: finalPlotPayload(),
        finalRecordNote: 'พบบันทึกการตรวจที่ใช้สรุป',
      })],
    }));
    renderModal();
    await doPreview();

    expect(await screen.findByText('พบบันทึกการตรวจที่ใช้สรุป')).toBeTruthy();
  });

  it('shows "ไม่มีบันทึกการตรวจที่ใช้สรุป" verbatim from row.finalRecordNote (item 10)', async () => {
    previewMock.mockResolvedValue(finalPlotPreview({
      rows: [row({
        action: 'final_plot', plotCode: 'P001', payload: finalPlotPayload(),
        finalRecordNote: 'ไม่มีบันทึกการตรวจที่ใช้สรุป',
      })],
    }));
    renderModal();
    await doPreview();

    expect(await screen.findByText('ไม่มีบันทึกการตรวจที่ใช้สรุป')).toBeTruthy();
  });

  // Items 11/12 — replaces the pre-8-10C behaviour (which used to assert the
  // OPPOSITE: that a truncated id string appeared). The record's id — full
  // or truncated — must never reach the DOM, regardless of whether it came
  // from the row's own note or the previewState fallback.
  it('never shows the resolved inspection record id, full or truncated (item 11/12)', async () => {
    previewMock.mockResolvedValue(finalPlotPreview({
      rows: [row({
        action: 'final_plot', plotCode: 'P001', payload: finalPlotPayload(),
        finalRecordNote: 'พบบันทึกการตรวจที่ใช้สรุป',
      })],
    }));
    renderModal();
    await doPreview();

    expect(await screen.findByText('พบบันทึกการตรวจที่ใช้สรุป')).toBeTruthy();
    // finalPlotState's resolvedFinalInspectionRecordId, full and truncated —
    // neither may appear anywhere in the rendered document.
    expect(screen.queryByText(/resolved-record-id-999/)).toBeNull();
    expect(screen.queryByText(/resolved…/)).toBeNull();
    expect(document.body.textContent).not.toContain('resolved-record-id-999');
  });

  // Item 13 — an older response shape (no finalRecordNote field at all)
  // falls back to the previewState binding's resolvedFinalInspectionRecordId
  // presence/absence — never the id's VALUE, only whether it is null.
  it('falls back to the previewState binding when finalRecordNote is absent — resolved id present → "found" (item 13)', async () => {
    previewMock.mockResolvedValue(finalPlotPreview({
      rows: [row({
        action: 'final_plot', plotCode: 'P001', payload: finalPlotPayload(),
        // finalRecordNote intentionally omitted — simulates an old response.
      })],
      // finalPlotState (the default previewState) has a non-null resolved id.
    }));
    renderModal();
    await doPreview();

    expect(await screen.findByText('พบบันทึกการตรวจที่ใช้สรุป')).toBeTruthy();
  });

  it('falls back to the previewState binding when finalRecordNote is absent — resolved id null → "none" (item 13)', async () => {
    previewMock.mockResolvedValue(finalPlotPreview({
      rows: [row({
        action: 'final_plot', plotCode: 'P001', payload: finalPlotPayload(),
      })],
      previewState: {
        ...finalPlotState,
        finalPlotRows: [{ ...finalPlotState.finalPlotRows[0], resolvedFinalInspectionRecordId: null }],
      } as PlotImportPreview['previewState'],
    }));
    renderModal();
    await doPreview();

    expect(await screen.findByText('ไม่มีบันทึกการตรวจที่ใช้สรุป')).toBeTruthy();
  });

  // Item 14 — an errored row with no previewState binding (never resolved:
  // no active cycle / cycle-label mismatch) must not guess found/not-found.
  it('an errored final_plot row with no previewState binding shows "ยังไม่สามารถตรวจสอบบันทึกที่ใช้สรุป" (item 14)', async () => {
    previewMock.mockResolvedValue(preview({
      rows: [row({
        action: 'final_plot', plotCode: 'P001', status: 'error',
        message: 'แปลงนี้ไม่มีรอบปลูกที่เปิดอยู่ จึงไม่สามารถลงผลผลิตสุดท้ายได้',
        payload: finalPlotPayload(),
      })],
      previewState: { fileSha256: 'f'.repeat(64), startNextRows: [], finalPlotRows: [] } as PlotImportPreview['previewState'],
    }));
    renderModal();
    await doPreview();

    expect(await screen.findByText('ยังไม่สามารถตรวจสอบบันทึกที่ใช้สรุป')).toBeTruthy();
  });

  it('shows row.warning in amber and the row stays READY (พร้อม) — Commit is never blocked by it', async () => {
    previewMock.mockResolvedValue(finalPlotPreview({
      rows: [row({
        action: 'final_plot', plotCode: 'P001', status: 'valid',
        payload: finalPlotPayload(),
        warning: 'ผลผลิตหลังทำความสะอาด (finalYieldAfterClean) มากกว่าผลผลิตตอนเก็บเกี่ยว (harvestYield)',
      })],
    }));
    commitReportMock.mockResolvedValue({ outcome: 'completed', report: reportFile({ kind: 'completed' }) });
    renderModal();
    await doPreview();

    expect(await screen.findByText('พร้อม')).toBeTruthy(); // still READY, not an error badge
    const warningText = screen.getByText(/ผลผลิตหลังทำความสะอาด.*มากกว่าผลผลิตตอนเก็บเกี่ยว/);
    expect(warningText.closest('p')?.className).toContain('amber');
    expect(warningText.closest('p')?.className).not.toContain('destructive');

    // Commit is still enabled and works.
    const commitBtn = await screen.findByRole('button', { name: 'ยืนยันนำเข้า' });
    await waitFor(() => expect((commitBtn as HTMLButtonElement).disabled).toBe(false));
    fireEvent.click(commitBtn);
    await waitFor(() => expect(commitReportMock).toHaveBeenCalledWith(expect.anything(), finalPlotState));
  });

  it('a final_plot file whose preview response is missing previewState blocks Commit and shows guidance', async () => {
    previewMock.mockResolvedValue(preview({
      rows: [row({ action: 'final_plot', plotCode: 'P001', payload: finalPlotPayload() })],
      // no previewState field at all
    }));
    renderModal();
    await doPreview();

    expect(await screen.findByText('P001')).toBeTruthy();
    expect((screen.getByRole('button', { name: 'ยืนยันนำเข้า' }) as HTMLButtonElement).disabled).toBe(true);
    expect(screen.getByText(/ไม่พบข้อมูลตรวจสอบสถานะรอบปลูก/)).toBeTruthy();
  });

  it('echoes previewState.finalPlotRows verbatim on commit (never reconstructed client-side)', async () => {
    previewMock.mockResolvedValue(finalPlotPreview());
    commitReportMock.mockResolvedValue({ outcome: 'completed', report: reportFile({ kind: 'completed' }) });
    renderModal();
    const file = await doPreview();

    const commitBtn = await screen.findByRole('button', { name: 'ยืนยันนำเข้า' });
    await waitFor(() => expect((commitBtn as HTMLButtonElement).disabled).toBe(false));
    fireEvent.click(commitBtn);

    await waitFor(() => expect(commitReportMock).toHaveBeenCalledWith(file, finalPlotState));
    expect(commitReportMock).toHaveBeenCalledTimes(1);
    expect(commitJsonMock).not.toHaveBeenCalled();
  });

  it('a final_plot preview_state_conflict shows the conflict banner, disables Commit, and does not auto-retry', async () => {
    previewMock.mockResolvedValue(finalPlotPreview());
    commitReportMock.mockRejectedValue(new PlotImportReportError(
      'สถานะรอบปลูกมีการเปลี่ยนแปลง กรุณาตรวจสอบไฟล์อีกครั้งก่อนนำเข้า', 409, 'preview_state_conflict',
    ));
    const { onImported } = renderModal();
    await doPreview();

    const commitBtn = await screen.findByRole('button', { name: 'ยืนยันนำเข้า' });
    await waitFor(() => expect((commitBtn as HTMLButtonElement).disabled).toBe(false));
    fireEvent.click(commitBtn);

    expect(await screen.findByText('สถานะรอบปลูกมีการเปลี่ยนแปลง กรุณาตรวจสอบไฟล์อีกครั้งก่อนนำเข้า')).toBeTruthy();
    expect(onImported).not.toHaveBeenCalled();
    expect(commitReportMock).toHaveBeenCalledTimes(1);
    expect((screen.getByRole('button', { name: 'ยืนยันนำเข้า' }) as HTMLButtonElement).disabled).toBe(true);
  });

  it('completed summary shows "ลงผลผลิตสุดท้ายและปิดรอบ X แปลง"', async () => {
    previewMock.mockResolvedValue(finalPlotPreview());
    commitReportMock.mockResolvedValue({ outcome: 'completed', report: reportFile({ kind: 'completed' }) });
    renderModal();
    await doPreview();

    const commitBtn = await screen.findByRole('button', { name: 'ยืนยันนำเข้า' });
    await waitFor(() => expect((commitBtn as HTMLButtonElement).disabled).toBe(false));
    fireEvent.click(commitBtn);

    await screen.findByText('นำเข้าสำเร็จ');
    expect(screen.getByText(/ลงผลผลิตสุดท้ายและปิดรอบ 1 แปลง/)).toBeTruthy();
  });

  // --- Part E regression coverage: round 8-10B's record-drift conflict
  // surfaces through the SAME generic stateConflict UI as any other preview-
  // state conflict — these confirm that existing, unmodified UI still shows
  // the backend's real detail/row numbers and still requires an explicit
  // re-Preview (no auto-retry, no stale previewState) for THIS specific
  // final_plot conflict reason too.

  // Item 16 — the backend's exact 8-10B record-drift message, plus the
  // changed row number.
  it('a final_plot record-drift conflict shows the backend detail and the changed row number (item 16)', async () => {
    previewMock.mockResolvedValue(finalPlotPreview());
    commitReportMock.mockRejectedValue(new PlotImportReportError(
      'บันทึกการตรวจล่าสุดของรอบปลูกมีการเปลี่ยนแปลง กรุณาตรวจสอบไฟล์อีกครั้งก่อนนำเข้า',
      409, 'preview_state_conflict', 'resolution_changed', [3],
    ));
    renderModal();
    await doPreview();

    const commitBtn = await screen.findByRole('button', { name: 'ยืนยันนำเข้า' });
    await waitFor(() => expect((commitBtn as HTMLButtonElement).disabled).toBe(false));
    fireEvent.click(commitBtn);

    expect(await screen.findByText(
      'บันทึกการตรวจล่าสุดของรอบปลูกมีการเปลี่ยนแปลง กรุณาตรวจสอบไฟล์อีกครั้งก่อนนำเข้า',
    )).toBeTruthy();
    expect(screen.getByText(/แถวที่ต้องตรวจสอบ: 3/)).toBeTruthy();
    expect(screen.getByText('ไม่มีข้อมูลในไฟล์นี้ถูกนำเข้า')).toBeTruthy();
  });

  // Items 17/18 — "ตรวจสอบไฟล์อีกครั้ง" re-Previews the SAME file and Commit
  // stays disabled (no auto-retry) until that fresh Preview actually lands.
  it('final_plot record-drift conflict: "ตรวจสอบไฟล์อีกครั้ง" re-previews the same file and never auto-retries Commit (items 17/18)', async () => {
    const second = deferred<PlotImportPreview>();
    previewMock
      .mockResolvedValueOnce(finalPlotPreview())
      .mockReturnValueOnce(second.promise);
    commitReportMock.mockRejectedValueOnce(new PlotImportReportError(
      'บันทึกการตรวจล่าสุดของรอบปลูกมีการเปลี่ยนแปลง กรุณาตรวจสอบไฟล์อีกครั้งก่อนนำเข้า',
      409, 'preview_state_conflict', 'resolution_changed', [3],
    ));
    renderModal();
    const file = await doPreview();

    const commitBtn = await screen.findByRole('button', { name: 'ยืนยันนำเข้า' });
    await waitFor(() => expect((commitBtn as HTMLButtonElement).disabled).toBe(false));
    fireEvent.click(commitBtn);
    await screen.findByText(/บันทึกการตรวจล่าสุดของรอบปลูกมีการเปลี่ยนแปลง/);

    fireEvent.click(screen.getByRole('button', { name: 'ตรวจสอบไฟล์อีกครั้ง' }));
    await waitFor(() => expect(previewMock).toHaveBeenCalledTimes(2));
    expect(previewMock).toHaveBeenNthCalledWith(2, file);

    // The second Preview hasn't resolved yet — Commit must still be
    // disabled right now, and no automatic re-commit must have fired.
    expect((screen.getByRole('button', { name: 'ยืนยันนำเข้า' }) as HTMLButtonElement).disabled).toBe(true);
    expect(commitReportMock).toHaveBeenCalledTimes(1);

    second.resolve(finalPlotPreview({ previewState: { ...finalPlotState, fileSha256: 'b'.repeat(64) } as PlotImportPreview['previewState'] }));
    await waitFor(() =>
      expect((screen.getByRole('button', { name: 'ยืนยันนำเข้า' }) as HTMLButtonElement).disabled).toBe(false),
    );
    expect(commitReportMock).toHaveBeenCalledTimes(1); // the re-preview itself never auto-commits
  });
});

// --- round 8-12B: Auto Lot V2 + Supplier Lot No in the preview -------------

describe('PlotImportModal — Auto Lot V2 / Supplier Lot No (round 8-12B)', () => {
  function lotRow(over: Partial<PlotImportRowResult> = {}): PlotImportRowResult {
    return row({
      action: 'start_next_cycle',
      payload: {
        action: 'start_next_cycle', supplierCode: 'SUP010', plotCode: 'P001',
        plotName: null, primaryPhone: null, additionalPhones: [],
        village: null, district: null, province: null,
        latitude: null, longitude: null, rai: null,
        crop: null, variety: null, cycleLabel: '2605',
        poNumber: 'PO25001', pCode: 'WM-141', lotNo: null,
        supplierLotNo: 'SUP-OWN-1',
        oracleSupplierCode: null, oracleInvoice: null, refAccount: null,
        plantingDate: null, plantCount: null,
        expectedYieldFull: null, expectedYieldUnit: null,
        harvestYield: null, finalYieldAfterClean: null,
        harvestDate: null, finalNote: null,
      },
      lotMode: 'auto',
      proposedLotNo: '2605-SUP010-WM-141-###',
      ...over,
    });
  }

  it('renders the backend V2 proposed lot verbatim, labelled as the system lot', async () => {
    previewMock.mockResolvedValue(preview({ rows: [lotRow()] }));
    renderModal();
    await doPreview();

    expect(await screen.findByText(/Lot ระบบ 2605-SUP010-WM-141-###/)).toBeTruthy();
  });

  it('never shows the retired V1 formula', async () => {
    previewMock.mockResolvedValue(preview({ rows: [lotRow()] }));
    renderModal();
    await doPreview();

    await screen.findByText(/Lot ระบบ/);
    expect(screen.queryByText(/PO25001-P101-XX/)).toBeNull();
    expect(screen.queryByText(/-XX\b/)).toBeNull();
  });

  it('shows the Supplier Lot No on its own line', async () => {
    previewMock.mockResolvedValue(preview({ rows: [lotRow()] }));
    renderModal();
    await doPreview();

    expect(await screen.findByText(/Supplier Lot SUP-OWN-1/)).toBeTruthy();
  });

  it('shows an em dash when the row has no Supplier Lot No', async () => {
    previewMock.mockResolvedValue(preview({
      rows: [lotRow({
        payload: { ...lotRow().payload!, supplierLotNo: null },
      })],
    }));
    renderModal();
    await doPreview();

    expect(await screen.findByText(/Supplier Lot —/)).toBeTruthy();
  });

  it('help copy states the V2 formula and that Supplier Lot is independent', () => {
    renderModal();

    expect(screen.getAllByText(/2605-SUP010-WM-141-003/).length).toBeGreaterThan(0);
    expect(screen.getAllByText(/ไม่กระทบเลข Lot ที่ระบบสร้าง/).length).toBeGreaterThan(0);
  });

  it('help copy no longer describes the V1 formula', () => {
    renderModal();

    expect(screen.queryByText(/\{PO\}-\{plotCode\}/)).toBeNull();
    expect(screen.queryByText(/PO25001-P101-XX/)).toBeNull();
  });
});

// --- round 8-21B: Oracle Supplier Code / Oracle Invoice / Ref Account ------

describe('PlotImportModal — Oracle reference fields (round 8-21B)', () => {
  function oracleRow(over: Partial<PlotImportRowResult> = {}): PlotImportRowResult {
    return row({
      action: 'create_plot_with_cycle',
      payload: {
        action: 'create_plot_with_cycle', supplierCode: 'SUP010', plotCode: 'P001',
        plotName: 'แปลงใหม่', primaryPhone: null, additionalPhones: [],
        village: null, district: null, province: null,
        latitude: null, longitude: null, rai: null,
        crop: null, variety: null, cycleLabel: '2605',
        poNumber: null, pCode: 'WM-141', lotNo: null,
        supplierLotNo: 'SUP-OWN-1',
        oracleSupplierCode: 'ORC-SUP-1', oracleInvoice: 'INV-1', refAccount: 'ACC-1',
        plantingDate: null, plantCount: null,
        expectedYieldFull: null, expectedYieldUnit: null,
        harvestYield: null, finalYieldAfterClean: null,
        harvestDate: null, finalNote: null,
      },
      ...over,
    });
  }

  it('shows all three values on their own line, grouped near Supplier Lot', async () => {
    previewMock.mockResolvedValue(preview({ rows: [oracleRow()] }));
    renderModal();
    await doPreview();

    expect(await screen.findByText(/Oracle ORC-SUP-1 · Invoice INV-1 · Ref Account ACC-1/)).toBeTruthy();
  });

  it('shows an em dash for each field that is null', async () => {
    previewMock.mockResolvedValue(preview({
      rows: [oracleRow({
        payload: { ...oracleRow().payload!, oracleSupplierCode: null, oracleInvoice: null, refAccount: null },
      })],
    }));
    renderModal();
    await doPreview();

    expect(await screen.findByText(/Oracle — · Invoice — · Ref Account —/)).toBeTruthy();
  });

  it('shows the blank-clears warning when the file has an update_current_cycle row', async () => {
    previewMock.mockResolvedValue(preview({
      rows: [oracleRow({ action: 'update_current_cycle' })],
    }));
    renderModal();
    await doPreview();

    expect(
      await screen.findByText('สำหรับ update_current_cycle: ช่อง Oracle ที่เว้นว่างจะล้างค่าเดิม'),
    ).toBeTruthy();
  });

  it('does NOT show the blank-clears warning when the file has no update_current_cycle row', async () => {
    previewMock.mockResolvedValue(preview({
      rows: [oracleRow({ action: 'create_plot_with_cycle' })],
    }));
    renderModal();
    await doPreview();

    await screen.findByText(/Oracle ORC-SUP-1/);
    expect(
      screen.queryByText('สำหรับ update_current_cycle: ช่อง Oracle ที่เว้นว่างจะล้างค่าเดิม'),
    ).toBeNull();
  });

  it('shows the warning when a start_next_cycle row resolves to update-like behavior is irrelevant — only the literal action counts', async () => {
    // start_next_cycle never resolves to update_current_cycle (it resolves to
    // start_new_cycle or close_and_start_new_cycle only) — the warning must
    // stay OFF for a file that mixes those with no literal update_current_cycle row.
    previewMock.mockResolvedValue(preview({
      rows: [
        oracleRow({ action: 'start_next_cycle', resolvedAction: 'close_and_start_new_cycle' }),
      ],
    }));
    renderModal();
    await doPreview();

    expect(
      screen.queryByText('สำหรับ update_current_cycle: ช่อง Oracle ที่เว้นว่างจะล้างค่าเดิม'),
    ).toBeNull();
  });

  it('help copy explains the differing blank-cell rule vs poNumber/pCode/supplierLotNo', () => {
    renderModal();

    expect(
      screen.getAllByText(/ต่างจาก poNumber\/pCode\/supplierLotNo ด้านบนที่เว้นว่างแล้วคงค่าเดิม/).length,
    ).toBeGreaterThan(0);
  });
});

// --- round 8-9B.1: plot inspection password column -------------------------

describe('PlotImportModal — inspection password (round 8-9B.1)', () => {
  const PIN = '135790';  // test-only

  it('warns that the file may contain plot inspection passwords', () => {
    renderModal();
    expect(screen.getByText(
      'ไฟล์ Excel อาจมีรหัสยืนยันแปลง กรุณาจำกัดผู้เข้าถึงไฟล์และลบไฟล์เมื่อใช้งานเสร็จ',
    )).toBeTruthy();
  });

  it('shows the รหัสยืนยันแปลง column header after a preview', async () => {
    previewMock.mockResolvedValue(preview());
    renderModal();
    await doPreview();
    expect(await screen.findByText('รหัสยืนยันแปลง')).toBeTruthy();
  });

  it('shows "ตั้งรหัสใหม่" for a set row', async () => {
    previewMock.mockResolvedValue(preview({
      rows: [row({ inspectionPasswordChange: 'set', inspectionPasswordConfigured: false })],
    }));
    renderModal();
    await doPreview();
    expect(await screen.findByText('ตั้งรหัสใหม่')).toBeTruthy();
  });

  it('shows "เปลี่ยนรหัส" for a replace row', async () => {
    previewMock.mockResolvedValue(preview({
      rows: [row({ inspectionPasswordChange: 'replace', inspectionPasswordConfigured: true })],
    }));
    renderModal();
    await doPreview();
    expect(await screen.findByText('เปลี่ยนรหัส')).toBeTruthy();
  });

  it('shows "คงรหัสเดิม" when the row leaves the password alone', async () => {
    previewMock.mockResolvedValue(preview({ rows: [row({ inspectionPasswordChange: null })] }));
    renderModal();
    await doPreview();
    expect(await screen.findByText('คงรหัสเดิม')).toBeTruthy();
  });

  it('never renders a raw PIN, hash or digest anywhere in the table', async () => {
    previewMock.mockResolvedValue(preview({
      rows: [row({ inspectionPasswordChange: 'replace', inspectionPasswordConfigured: true })],
    }));
    renderModal();
    await doPreview();
    await screen.findByText('เปลี่ยนรหัส');
    const text = document.body.textContent ?? '';
    expect(text).not.toContain(PIN);
    expect(text).not.toContain('$2b$');
    expect(text.toLowerCase()).not.toContain('digest');
  });

  it('never shows a PIN in an error row message', async () => {
    previewMock.mockResolvedValue(preview({
      validRows: 0, errorRows: 1,
      rows: [row({
        status: 'error',
        message: 'newInspectionPassword: รหัสยืนยันแปลงต้องเป็นตัวเลข 0-9 จำนวน 4 ถึง 20 หลัก',
        inspectionPasswordChange: null,
      })],
    }));
    renderModal();
    await doPreview();
    const message = await screen.findByText(/newInspectionPassword/);
    expect(message.textContent).not.toContain(PIN);
  });

  it('echoes the credentialRows preview state back on commit without any secret', async () => {
    const previewState = {
      fileSha256: 'a'.repeat(64),
      startNextRows: [],
      credentialRows: [{
        rowNumber: 3, supplierCode: 'SUP001', plotCode: 'P101', plotId: null,
        expectedConfigured: false, expectedCredentialVersion: null, intendedChange: 'set',
      }],
    };
    previewMock.mockResolvedValue(preview({
      rows: [row({ inspectionPasswordChange: 'set' })],
      previewState,
    }));
    commitReportMock.mockResolvedValue(reportFile());
    renderModal();
    const file = await doPreview();
    fireEvent.click(await screen.findByRole('button', { name: /ยืนยันนำเข้า/ }));
    await waitFor(() => expect(commitReportMock).toHaveBeenCalledWith(file, previewState));
    expect(JSON.stringify(previewState)).not.toContain(PIN);
  });

  it('clears the previous preview when a new file is chosen', async () => {
    previewMock.mockResolvedValue(preview({
      rows: [row({ inspectionPasswordChange: 'replace' })],
    }));
    renderModal();
    await doPreview();
    await screen.findByText('เปลี่ยนรหัส');

    selectFile();   // pick a different file — old preview must be dropped
    await waitFor(() => expect(screen.queryByText('เปลี่ยนรหัส')).toBeNull());
  });

  it('never writes anything from the import flow into browser storage', async () => {
    previewMock.mockResolvedValue(preview({
      rows: [row({ inspectionPasswordChange: 'set' })],
    }));
    renderModal();
    await doPreview();
    await screen.findByText('ตั้งรหัสใหม่');

    const dump = (store: Storage | undefined): string => {
      if (!store) return '';
      let out = '';
      for (let i = 0; i < store.length; i += 1) {
        const key = store.key(i);
        if (key) out += `${key}=${store.getItem(key)}|`;
      }
      return out;
    };
    const all = dump(globalThis.localStorage) + dump(globalThis.sessionStorage);
    expect(all).not.toContain(PIN);
    expect(all).not.toContain('previewState');
  });
});
