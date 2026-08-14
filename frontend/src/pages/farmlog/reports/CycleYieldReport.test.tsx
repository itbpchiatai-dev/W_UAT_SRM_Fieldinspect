/**
 * CycleYieldReport (round 8-2.8B) — shows the FROZEN final estimated-yield
 * snapshot per cycle (read verbatim, never recomputed): harvested vs cancelled
 * vs active vs NULL wording, cycleLabel fallback, the records.read-gated
 * "record used" link, and filter/export plumbing.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { CycleYieldReport } from './CycleYieldReport';
import type { CycleYieldRow } from '../../../api/reports';

const listMock = vi.fn();
const downloadMock = vi.fn();
let hasPerm = true;

vi.mock('../../../api/reports', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../../api/reports')>();
  return {
    ...actual,
    listCycleYieldReport: (...a: unknown[]) => listMock(...a),
    downloadCycleYieldReport: (...a: unknown[]) => downloadMock(...a),
  };
});

vi.mock('../../../api/suppliers', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../../api/suppliers')>();
  return { ...actual, listSuppliers: () => Promise.resolve([]) };
});

vi.mock('../../../api/masterdata', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../../api/masterdata')>();
  return { ...actual, listMasterData: () => Promise.resolve([]) };
});

vi.mock('../../../hooks/useHasPermission', () => ({
  useHasPermission: () => hasPerm,
}));

function row(overrides: Partial<CycleYieldRow> = {}): CycleYieldRow {
  return {
    supplierId: 'sup-1', supplierCode: 'SUP001', supplierName: 'Supplier One',
    plotId: 'plot-1', plotCode: 'SUP001-P001', plotName: 'แปลงทดสอบ',
    province: 'เชียงใหม่', plotIsActive: true,
    cycleId: 'cycle-1', cycleNo: 2, cycleLabel: 'jun2026', cycleStatus: 'harvested',
    crop: 'พริก', variety: 'พริกขี้หนู', lotNo: 'LOT-01', plantingDate: '2026-06-01',
    plantCount: 1000, expectedYieldFull: '1000.00', expectedYieldUnit: 'kg',
    startedAt: '2026-06-01T00:00:00Z', closedAt: '2026-09-01T00:00:00Z',
    closeReason: 'เก็บเกี่ยวเสร็จ',
    finalYieldPct: '80.0', finalEstimatedYield: '800.00', finalInspectionRecordId: 'rec-1',
    // Round 8-7C.1 — ACTUAL harvest fields; default null (estimate-only
    // baseline) so every pre-8-7C.1 test using this fixture is unaffected.
    harvestYield: null, finalYieldAfterClean: null, finalYieldUnit: null,
    harvestDate: null, finalNote: null,
    ...overrides,
  };
}

function renderReport() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter><CycleYieldReport /></MemoryRouter>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  listMock.mockReset();
  downloadMock.mockReset();
  hasPerm = true;
});

describe('CycleYieldReport — final estimate wording', () => {
  it('harvested cycle shows "ผลผลิตประมาณการสุดท้าย" + the stored value', async () => {
    listMock.mockResolvedValue([row()]);
    renderReport();

    expect(await screen.findByText('ผลผลิตประมาณการสุดท้าย')).toBeTruthy();
    expect(screen.getByText('800 kg (80%)')).toBeTruthy();
  });

  it('cancelled cycle shows "ประมาณการล่าสุดก่อนยกเลิก", not the harvested label', async () => {
    listMock.mockResolvedValue([row({ cycleStatus: 'cancelled', closeReason: 'น้ำท่วม' })]);
    renderReport();

    // the cancelled-specific label is unique to the row (not a header/filter option)
    expect(await screen.findByText('ประมาณการล่าสุดก่อนยกเลิก')).toBeTruthy();
    // never the harvested-cycle label on a cancelled row
    expect(screen.queryByText('ผลผลิตประมาณการสุดท้าย')).toBeNull();
  });

  it('active cycle points to the current-yield tab, shows no final value', async () => {
    listMock.mockResolvedValue([row({
      cycleStatus: 'active', closedAt: null, finalYieldPct: null, finalEstimatedYield: null,
      finalInspectionRecordId: null,
    })]);
    renderReport();

    expect(await screen.findByText(/ยังไม่ปิดรอบ/)).toBeTruthy();
    expect(screen.queryByText('ผลผลิตประมาณการสุดท้าย')).toBeNull();
  });

  it('closed cycle with a NULL snapshot shows "ไม่มีข้อมูลประมาณการตอนปิดรอบ"', async () => {
    listMock.mockResolvedValue([row({
      finalYieldPct: null, finalEstimatedYield: null, finalInspectionRecordId: null,
    })]);
    renderReport();

    expect(await screen.findByText('ไม่มีข้อมูลประมาณการตอนปิดรอบ')).toBeTruthy();
  });

  it('renders no actual-harvest text when the row has no actual-harvest fields', async () => {
    // Round 8-7C.1 — supersedes the old blanket "never renders ผลผลิตจริง"
    // contract: the report NOW can show actual-harvest figures, but only
    // when the cycle actually has them (row()'s default is null).
    listMock.mockResolvedValue([row()]);
    const { container } = renderReport();
    await screen.findByText('ผลผลิตประมาณการสุดท้าย');
    expect(container.textContent).not.toContain('ผลผลิตจริง');
  });

  it('does NOT recompute the estimate — echoes the stored value even if it ≠ expected×pct', async () => {
    // stored 999 while expected×pct would be 800 — must show 999.
    listMock.mockResolvedValue([row({ finalEstimatedYield: '999.00' })]);
    renderReport();
    expect(await screen.findByText('999 kg (80%)')).toBeTruthy();
    expect(screen.queryByText('800 kg (80%)')).toBeNull();
  });

  it('falls back to "รอบที่ N" when cycleLabel is null', async () => {
    listMock.mockResolvedValue([row({ cycleLabel: null, cycleNo: 4 })]);
    renderReport();
    expect(await screen.findByText('รอบที่ 4')).toBeTruthy();
  });

  it('round 8-5B: shows this row\'s PO / P.Code and Lot with its source label', async () => {
    listMock.mockResolvedValue([row({
      poNumber: 'PO25009', pCode: 'Melon-I', lotNo: 'PO25009-P003-02', lotNoSource: 'auto',
    })]);
    renderReport();
    expect(await screen.findByText(/PO PO25009 · P\.Code Melon-I/)).toBeTruthy();
    expect(screen.getByText(/PO25009-P003-02/)).toBeTruthy();
    expect(screen.getByText(/\(อัตโนมัติ\)/)).toBeTruthy();
  });

  it('round 8-13B: a null PO Number (blank-PO cycle) shows "PO —", never a crash', async () => {
    listMock.mockResolvedValue([row({
      poNumber: null, pCode: 'WM-141', lotNo: '2605-SUP010-WM-141-001', lotNoSource: 'auto',
    })]);
    renderReport();
    expect(await screen.findByText(/PO — · P\.Code WM-141/)).toBeTruthy();
  });
});

describe('CycleYieldReport — Supplier Lot No (round 8-12C)', () => {
  it('shows the System Lot (V2 formula) and Supplier Lot No on separate lines', async () => {
    listMock.mockResolvedValue([row({
      lotNo: '2605-SUP010-WM-141-003', lotNoSource: 'auto', supplierLotNo: 'SUP-OWN-7',
    })]);
    renderReport();

    expect(await screen.findByText(/Lot ระบบ 2605-SUP010-WM-141-003/)).toBeTruthy();
    expect(screen.getByText(/Supplier Lot SUP-OWN-7/)).toBeTruthy();
  });

  it('shows an em dash for Supplier Lot No when the cycle has none, without hiding the System Lot', async () => {
    listMock.mockResolvedValue([row({
      lotNo: '2605-SUP010-WM-141-003', lotNoSource: 'auto', supplierLotNo: null,
    })]);
    renderReport();

    expect(await screen.findByText(/Lot ระบบ 2605-SUP010-WM-141-003/)).toBeTruthy();
    expect(screen.getByText(/Supplier Lot —/)).toBeTruthy();
  });

  it('never merges the Supplier Lot value into the System Lot line', async () => {
    listMock.mockResolvedValue([row({
      lotNo: '2605-SUP010-WM-141-003', lotNoSource: 'auto', supplierLotNo: 'SUP-OWN-7',
    })]);
    renderReport();

    await screen.findByText(/Lot ระบบ 2605-SUP010-WM-141-003/);
    expect(screen.queryByText(/2605-SUP010-WM-141-003.*SUP-OWN-7/)).toBeNull();
  });
});

describe('CycleYieldReport — record link permission gate', () => {
  it('renders the "บันทึกที่ใช้สรุป" link with records.read', async () => {
    hasPerm = true;
    listMock.mockResolvedValue([row()]);
    renderReport();

    const link = await screen.findByRole('link', { name: /บันทึกที่ใช้สรุป/ });
    expect(link.getAttribute('href')).toBe('/farmlog/records/rec-1/preview');
  });

  it('hides the record link without records.read', async () => {
    hasPerm = false;
    listMock.mockResolvedValue([row()]);
    renderReport();

    await screen.findByText('ผลผลิตประมาณการสุดท้าย');
    expect(screen.queryByRole('link', { name: /บันทึกที่ใช้สรุป/ })).toBeNull();
  });
});

describe('CycleYieldReport — record source link is cycle-level, not estimate-gated (round 8-7C.2)', () => {
  it('estimate present + record ID + records.read → shows the link exactly once (no duplicate)', async () => {
    hasPerm = true;
    listMock.mockResolvedValue([row()]); // default fixture: estimate present, finalInspectionRecordId 'rec-1'
    renderReport();

    const links = await screen.findAllByRole('link', { name: /บันทึกที่ใช้สรุป/ });
    expect(links).toHaveLength(1);
    expect(links[0].getAttribute('href')).toBe('/farmlog/records/rec-1/preview');
  });

  it('estimate null + actual harvest present + record ID → still shows the link', async () => {
    hasPerm = true;
    listMock.mockResolvedValue([row({
      finalYieldPct: null, finalEstimatedYield: null,
      harvestYield: 1250, finalYieldAfterClean: 1180, finalYieldUnit: 'kg',
      finalInspectionRecordId: 'rec-2',
    })]);
    renderReport();

    await screen.findByText('ไม่มีข้อมูลประมาณการตอนปิดรอบ');
    const link = await screen.findByRole('link', { name: /บันทึกที่ใช้สรุป/ });
    expect(link.getAttribute('href')).toBe('/farmlog/records/rec-2/preview');
  });

  it('estimate null + actual null + record ID (closed cycle) → still shows the link', async () => {
    hasPerm = true;
    listMock.mockResolvedValue([row({
      finalYieldPct: null, finalEstimatedYield: null,
      finalInspectionRecordId: 'rec-3',
    })]);
    renderReport();

    await screen.findByText('ไม่มีข้อมูลประมาณการตอนปิดรอบ');
    const link = await screen.findByRole('link', { name: /บันทึกที่ใช้สรุป/ });
    expect(link.getAttribute('href')).toBe('/farmlog/records/rec-3/preview');
  });

  it('without records.read → no link, no matter what else is present', async () => {
    hasPerm = false;
    listMock.mockResolvedValue([row({
      finalYieldPct: null, finalEstimatedYield: null,
      harvestYield: 1250, finalYieldAfterClean: 1180, finalYieldUnit: 'kg',
      finalInspectionRecordId: 'rec-4',
    })]);
    renderReport();

    await screen.findByText('ไม่มีข้อมูลประมาณการตอนปิดรอบ');
    expect(screen.queryByRole('link', { name: /บันทึกที่ใช้สรุป/ })).toBeNull();
    expect(screen.queryByText(/rec-4/)).toBeNull(); // never a raw id fallback either
  });

  it('active cycle + record ID → never shows the link, even with a fixture that carries one', async () => {
    hasPerm = true;
    listMock.mockResolvedValue([row({
      cycleStatus: 'active', closedAt: null, finalYieldPct: null, finalEstimatedYield: null,
      finalInspectionRecordId: 'rec-5',
    })]);
    renderReport();

    await screen.findByText(/ยังไม่ปิดรอบ/);
    expect(screen.queryByRole('link', { name: /บันทึกที่ใช้สรุป/ })).toBeNull();
  });

  it('finalInspectionRecordId null → no link', async () => {
    hasPerm = true;
    listMock.mockResolvedValue([row({ finalInspectionRecordId: null })]);
    renderReport();

    await screen.findByText('ผลผลิตประมาณการสุดท้าย');
    expect(screen.queryByRole('link', { name: /บันทึกที่ใช้สรุป/ })).toBeNull();
  });
});

describe('CycleYieldReport — actual harvest (round 8-7C.1)', () => {
  it('shows estimate AND actual harvest together, clearly separate', async () => {
    listMock.mockResolvedValue([row({
      harvestYield: 1250, finalYieldAfterClean: 1180, finalYieldUnit: 'kg',
      harvestDate: '2026-07-28', finalNote: 'ผลผลิตหลังคัดแยก',
    })]);
    renderReport();

    // Estimate line untouched.
    expect(await screen.findByText('ผลผลิตประมาณการสุดท้าย')).toBeTruthy();
    expect(screen.getByText('800 kg (80%)')).toBeTruthy();
    // Actual harvest lines, separate.
    expect(screen.getByText(/ผลผลิตตอนเก็บเกี่ยว/)).toBeTruthy();
    expect(screen.getByText('1,250 kg')).toBeTruthy();
    expect(screen.getByText(/ผลผลิตจริงหลังทำความสะอาด/)).toBeTruthy();
    expect(screen.getByText('1,180 kg')).toBeTruthy();
    expect(screen.getByText(/วันที่เก็บเกี่ยว/)).toBeTruthy();
    expect(screen.getByText('2026-07-28')).toBeTruthy();
    expect(screen.getByText(/หมายเหตุ: ผลผลิตหลังคัดแยก/)).toBeTruthy();
  });

  it('estimate uses expectedYieldUnit; actual uses finalYieldUnit (never mixed)', async () => {
    listMock.mockResolvedValue([row({
      expectedYieldUnit: 'kg', finalYieldUnit: 'ตัน',
      harvestYield: 2, finalYieldAfterClean: 1.8,
    })]);
    renderReport();

    await screen.findByText('ผลผลิตประมาณการสุดท้าย');
    expect(screen.getByText('800 kg (80%)')).toBeTruthy(); // estimate still kg
    expect(screen.getByText('2 ตัน')).toBeTruthy();        // actual in its own unit
    expect(screen.getByText('1.8 ตัน')).toBeTruthy();
  });

  it('actual-only: shows actual harvest even when the estimate is null', async () => {
    listMock.mockResolvedValue([row({
      finalYieldPct: null, finalEstimatedYield: null,
      harvestYield: 1250, finalYieldAfterClean: 1180, finalYieldUnit: 'kg',
    })]);
    renderReport();

    expect(await screen.findByText('ไม่มีข้อมูลประมาณการตอนปิดรอบ')).toBeTruthy();
    expect(screen.getByText('1,250 kg')).toBeTruthy();
    expect(screen.getByText('1,180 kg')).toBeTruthy();
  });

  it('estimate-only: still displays exactly as before when actual fields are null', async () => {
    listMock.mockResolvedValue([row()]); // default fixture: actual fields all null
    renderReport();

    expect(await screen.findByText('800 kg (80%)')).toBeTruthy();
    expect(screen.queryByText(/ผลผลิตตอนเก็บเกี่ยว/)).toBeNull();
  });

  it('a legacy closed cycle with every field null does not crash and shows no fabricated actual value', async () => {
    listMock.mockResolvedValue([row({
      finalYieldPct: null, finalEstimatedYield: null, finalInspectionRecordId: null,
    })]);
    renderReport();

    expect(await screen.findByText('ไม่มีข้อมูลประมาณการตอนปิดรอบ')).toBeTruthy();
    expect(screen.queryByText(/ผลผลิตตอนเก็บเกี่ยว/)).toBeNull();
    expect(screen.queryByText(/ผลผลิตจริงหลังทำความสะอาด/)).toBeNull();
  });

  it('an active cycle never shows actual-harvest figures even if fields somehow carry values', async () => {
    listMock.mockResolvedValue([row({
      cycleStatus: 'active', closedAt: null, finalYieldPct: null, finalEstimatedYield: null,
      finalInspectionRecordId: null,
      harvestYield: 1250, finalYieldAfterClean: 1180, finalYieldUnit: 'kg',
    })]);
    renderReport();

    await screen.findByText(/ยังไม่ปิดรอบ/);
    expect(screen.queryByText(/ผลผลิตตอนเก็บเกี่ยว/)).toBeNull();
    expect(screen.queryByText('1,250 kg')).toBeNull();
  });
});

describe('CycleYieldReport — filters / export / states', () => {
  it('default status filter is "รอบที่ปิดแล้ว" (closed)', async () => {
    listMock.mockResolvedValue([]);
    renderReport();
    await waitFor(() => expect(listMock).toHaveBeenCalled());
    expect(listMock.mock.calls[0][0]).toMatchObject({ status: 'closed' });
  });

  it('changing the status filter re-queries with the new value', async () => {
    listMock.mockResolvedValue([]);
    renderReport();
    await waitFor(() => expect(listMock).toHaveBeenCalled());

    fireEvent.change(screen.getByLabelText('สถานะรอบ'), { target: { value: 'cancelled' } });
    await waitFor(() =>
      expect(listMock.mock.calls.some((c) => c[0]?.status === 'cancelled')).toBe(true),
    );
  });

  it('export button downloads with the same filters', async () => {
    listMock.mockResolvedValue([row()]);
    downloadMock.mockResolvedValue(new Blob(['x']));
    renderReport();
    await screen.findByText('ผลผลิตประมาณการสุดท้าย');

    fireEvent.click(screen.getByRole('button', { name: /ดาวน์โหลด Excel/ }));
    await waitFor(() => expect(downloadMock).toHaveBeenCalled());
    expect(downloadMock.mock.calls[0][0]).toMatchObject({ status: 'closed' });
  });

  it('shows the empty state when no cycles match', async () => {
    listMock.mockResolvedValue([]);
    renderReport();
    expect(await screen.findByText('ไม่พบรอบปลูกตามเงื่อนไข')).toBeTruthy();
  });

  it('shows an error state when the query fails', async () => {
    listMock.mockRejectedValue(new Error('boom'));
    renderReport();
    expect(await screen.findByText(/โหลดข้อมูลไม่สำเร็จ/)).toBeTruthy();
  });
});

describe('CycleYieldReport — Yield >150% display (round 8-8C)', () => {
  it('shows a real finalYieldPct over 150% verbatim (never clamped, never an error)', async () => {
    listMock.mockResolvedValue([row({ finalYieldPct: '510.0', finalEstimatedYield: '5100.00' })]);
    renderReport();

    // describeFinalEstimate's frozen-value text: "<qty> (<pct>%)" — read
    // verbatim off the row, this report never recomputes it (round 8-2.8B).
    expect(await screen.findByText('5,100 kg (510%)')).toBeTruthy();
  });

  it('actual harvest fields alongside a >150% estimate remain unaffected (round 8-7C.1 regression)', async () => {
    listMock.mockResolvedValue([row({
      finalYieldPct: '510.0', finalEstimatedYield: '5100.00',
      harvestYield: '5200.00', finalYieldAfterClean: '5000.00', finalYieldUnit: 'kg',
    })]);
    renderReport();

    expect(await screen.findByText('5,100 kg (510%)')).toBeTruthy();
    expect(screen.getByText('5,200 kg')).toBeTruthy();
    expect(screen.getByText('5,000 kg')).toBeTruthy();
  });
});
