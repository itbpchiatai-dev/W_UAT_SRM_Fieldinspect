/**
 * PlotDetail — round 15 behavioral guards:
 * - Current Status renders verbatim from plot.current_* (no frontend
 *   re-derivation from records).
 * - "ยังไม่มีการตรวจ" shown instead of a pile of empty fields when
 *   lastInspectionRecordId is null.
 * - History renders in whatever order the API returned (backend already
 *   orders most-recent-first — this just proves the page doesn't re-sort).
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import { MemoryRouter, Routes, Route } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { PlotDetail } from './PlotDetail';
import type { PlotCycle, PlotDetail as PlotDetailType } from '../../../api/plots';
import type { RecordSummary } from '../../../api/records';

const getPlotMock = vi.fn();
const listRecordsMock = vi.fn();
const getRecordMock = vi.fn();
// Round 8-14C — AuthenticatedPhoto is exercised for real by the new
// lightbox-integration tests below; every OTHER existing test in this file
// keeps using photoUrls: [] (AuthenticatedPhoto never renders), so this mock
// only matters for those new tests.
const getRecordPhotoBlobMock = vi.fn();
const listPlotCyclesMock = vi.fn();
const createPlotCycleMock = vi.fn();
const updatePlotCycleMock = vi.fn();
const closePlotCycleMock = vi.fn();
const rolloverPlotCycleMock = vi.fn();
const getPlotAccessPhonesMock = vi.fn();
const replacePlotAccessPhonesMock = vi.fn();
const reactivatePlotMock = vi.fn();
const reactivatePlotWithCycleMock = vi.fn();
const getPlotInspectionCredentialMock = vi.fn();
const setPlotInspectionCredentialMock = vi.fn();

vi.mock('../../../api/plots', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../../api/plots')>();
  return {
    ...actual,
    getPlot: (...args: unknown[]) => getPlotMock(...args),
    listPlotCycles: (...args: unknown[]) => listPlotCyclesMock(...args),
    createPlotCycle: (...args: unknown[]) => createPlotCycleMock(...args),
    updatePlotCycle: (...args: unknown[]) => updatePlotCycleMock(...args),
    closePlotCycle: (...args: unknown[]) => closePlotCycleMock(...args),
    rolloverPlotCycle: (...args: unknown[]) => rolloverPlotCycleMock(...args),
    getPlotAccessPhones: (...args: unknown[]) => getPlotAccessPhonesMock(...args),
    replacePlotAccessPhones: (...args: unknown[]) => replacePlotAccessPhonesMock(...args),
    reactivatePlot: (...args: unknown[]) => reactivatePlotMock(...args),
    reactivatePlotWithCycle: (...args: unknown[]) => reactivatePlotWithCycleMock(...args),
    getPlotInspectionAccessCredential: (...args: unknown[]) => getPlotInspectionCredentialMock(...args),
    setPlotInspectionAccessCredential: (...args: unknown[]) => setPlotInspectionCredentialMock(...args),
  };
});

vi.mock('../../../api/records', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../../api/records')>();
  return {
    ...actual,
    listRecords: (...args: unknown[]) => listRecordsMock(...args),
    getRecord: (...args: unknown[]) => getRecordMock(...args),
    getRecordPhotoBlob: (...args: unknown[]) => getRecordPhotoBlobMock(...args),
  };
});

// The cycle modals' MasterDataSelect fields query this — stub empty so it
// never hits the real apiClient (these tests never need real options).
vi.mock('../../../api/masterdata', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../../api/masterdata')>();
  return { ...actual, listMasterData: () => Promise.resolve([]) };
});

// null = every permission allowed (the default the existing tests rely on);
// a Set restricts to exactly those keys (for the gating tests below).
let allowedPerms: Set<string> | null = null;
vi.mock('../../../hooks/useHasPermission', () => ({
  useHasPermission: (key: string) => (allowedPerms === null ? true : allowedPerms.has(key)),
}));

function basePlot(overrides: Partial<PlotDetailType> = {}): PlotDetailType {
  return {
    id: 'plot-1',
    supplierId: 'sup-1',
    supplierCode: 'SUP001',
    supplierName: 'Supplier One',
    plotCode: 'SUP001-P001',
    name: 'แปลงทดสอบ',
    village: 'บ้านทดสอบ',
    district: 'อำเภอทดสอบ',
    province: 'จังหวัดทดสอบ',
    latitude: null,
    longitude: null,
    rai: null,
    isActive: true,
    assignedUsers: [],
    createdAt: '2026-01-01T00:00:00Z',
    updatedAt: '2026-01-01T00:00:00Z',
    qrKey: null,
    currentCrop: null,
    currentVariety: null,
    currentLotNo: null,
    currentPlantingDate: null,
    currentStage: null,
    currentYieldPct: null,
    currentFieldPrepScore: null,
    currentWeatherScore: null,
    currentCareScore: null,
    currentVarietyResistanceScore: null,
    currentGpsLat: null,
    currentGpsLng: null,
    lastInspectedAt: null,
    lastInspectedByCode: null,
    lastInspectionRecordId: null,
    plantCount: null,
    expectedYieldFull: null,
    expectedYieldUnit: null,
    // Round 7.3.1 active-cycle read-model. PlotDetail derives its active
    // cycle from the listPlotCycles query (for the history list), not these,
    // so they default null; overrides can set them when a test asserts on them.
    activeCycleId: null,
    activeCycleNo: null,
    activeCycleStatus: null,
    activeCycleCrop: null,
    activeCycleVariety: null,
    activeCycleLabel: null,
    activeCycleLotNo: null,
    activeCyclePoNumber: null,
    activeCyclePCode: null, activeCycleSupplierLotNo: null,
    activeCyclePlantingDate: null,
    activeCyclePlantCount: null,
    activeCycleExpectedYieldFull: null,
    activeCycleExpectedYieldUnit: null,
    // Access phones (round 8-3C) — default to "not set up"; overrides supply
    // real values for the AccessPhoneSection-specific tests below.
    primaryPhone: null,
    additionalPhones: [],
    ...overrides,
  };
}

function oneCycle(overrides: Partial<PlotCycle> = {}): PlotCycle {
  return {
    id: 'cycle-1',
    plotId: 'plot-1',
    cycleNo: 1,
    status: 'active',
    crop: 'พริก',
    variety: 'พริกขี้หนู',
    cycleLabel: null,
    lotNo: 'LOT-01',
    poNumber: null,
    pCode: null,
    lotNoSource: null,
    lotRunningNo: null,
    supplierLotNo: null,
    oracleSupplierCode: null,
    oracleInvoice: null,
    refAccount: null,
    plantingDate: '2026-06-01',
    plantCount: 500,
    expectedYieldFull: '1000.00',
    expectedYieldUnit: 'kg',
    startedAt: '2026-06-01T00:00:00Z',
    closedAt: null,
    closedById: null,
    closeReason: null,
    finalYieldPct: null,
    finalEstimatedYield: null,
    finalInspectionRecordId: null,
    harvestYield: null,
    finalYieldAfterClean: null,
    finalYieldUnit: null,
    harvestDate: null,
    finalNote: null,
    createdAt: '2026-06-01T00:00:00Z',
    updatedAt: '2026-06-01T00:00:00Z',
    ...overrides,
  };
}

function baseRecordSummary(overrides: Partial<RecordSummary> = {}): RecordSummary {
  return {
    id: 'rec-1',
    plotId: 'plot-1',
    plotCycleId: 'cycle-1',
    cycleNo: 1,
    cycleLabel: null,
    supplierId: 'sup-1',
    recordedById: 'user-1',
    submittedByCode: 'FIELD01',
    submittedByName: null,
    recordDate: '2026-07-01',
    crop: 'พริก',
    variety: null,
    growthStage: 'ออกดอก',
    yieldPct: '95.5',
    yieldQuantityKg: null,
    yieldTargetKgSnapshot: null,
    fieldPrepScore: 8,
    weatherScore: 7,
    careScore: 6,
    varietyResistanceScore: 5,
    isActive: true,
    createdAt: '2026-07-01T10:00:00Z',
    plotCode: 'SUP001-P001',
    plotName: 'แปลงทดสอบ',
    supplierName: 'Supplier One',
    ...overrides,
  };
}

function renderPage(qc: QueryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })) {
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={['/farmlog/admin/plots/plot-1']}>
        <Routes>
          <Route path="/farmlog/admin/plots/:plotId" element={<PlotDetail />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

// --- Round 8-10A: the cycle-history TABLE --------------------------------
// Looked up by header text rather than by fixed index, so reordering a column
// changes one place instead of breaking every assertion below.

function historyTable(): HTMLTableElement {
  const header = screen.getByRole('columnheader', { name: 'รอบปลูก' });
  const table = header.closest('table');
  if (!table) throw new Error('cycle history table not found');
  return table as HTMLTableElement;
}

function historyColumnIndex(label: string): number {
  const headers = Array.from(historyTable().querySelectorAll('thead th'));
  const index = headers.findIndex((th) => th.textContent?.trim() === label);
  if (index < 0) throw new Error(`no history column named "${label}"`);
  return index;
}

function historyRows(): HTMLTableRowElement[] {
  return Array.from(historyTable().querySelectorAll('tbody tr'));
}

/** Rows are keyed by the "รอบปลูก" cell (cycleLabel, else "รอบที่ N"). */
function historyRow(cycleName: string): HTMLTableRowElement {
  const row = historyRows().find((r) => r.cells[0]?.textContent?.trim() === cycleName);
  if (!row) throw new Error(`no history row for "${cycleName}"`);
  return row;
}

function historyCellText(cycleName: string, columnIndex: number): string {
  return historyRow(cycleName).cells[columnIndex]?.textContent ?? '';
}

function historyCell(cycleName: string, columnLabel: string): string {
  return historyCellText(cycleName, historyColumnIndex(columnLabel));
}

beforeEach(() => {
  getPlotMock.mockReset();
  listRecordsMock.mockReset();
  getRecordMock.mockReset();
  listPlotCyclesMock.mockReset();
  createPlotCycleMock.mockReset();
  updatePlotCycleMock.mockReset();
  closePlotCycleMock.mockReset();
  rolloverPlotCycleMock.mockReset();
  getPlotAccessPhonesMock.mockReset();
  replacePlotAccessPhonesMock.mockReset();
  reactivatePlotMock.mockReset();
  reactivatePlotWithCycleMock.mockReset();
  getPlotInspectionCredentialMock.mockReset();
  setPlotInspectionCredentialMock.mockReset();
  getRecordPhotoBlobMock.mockReset();
  listRecordsMock.mockResolvedValue([]);
  // Default: the plot has no inspection password yet (round 8-9B). Tests
  // about the configured/loading/error states override this per-test.
  getPlotInspectionCredentialMock.mockResolvedValue({
    configured: false, credentialVersion: null, updatedAt: null,
  });
  // Default: an active cycle exists — matches the existing tests' assumption
  // that ตรวจแปลง is available whenever the plot itself is active. Tests
  // about the no-active-cycle state override this per-test.
  listPlotCyclesMock.mockResolvedValue([oneCycle()]);
  allowedPerms = null;
});

describe('PlotDetail — current status', () => {
  it('shows a link to แก้ไขแปลง that hands off to the Plots list edit modal', async () => {
    getPlotMock.mockResolvedValue(basePlot());

    renderPage();

    const editLink = await screen.findByRole('link', { name: 'แก้ไขแปลง' });
    expect(editLink.getAttribute('href')).toBe('/farmlog/admin/plots?manage=edit&plotId=plot-1');
    // Round 8.0: มอบหมาย button removed from PlotDetail UI.
    expect(screen.queryByRole('link', { name: 'มอบหมาย' })).toBeNull();
  });

  it('shows a "ตรวจแปลง" button linking to the new-record form prefilled with this plot', async () => {
    getPlotMock.mockResolvedValue(basePlot());

    renderPage();

    const inspectLink = await screen.findByRole('link', { name: 'ตรวจแปลง' });
    expect(inspectLink.getAttribute('href')).toBe(
      '/farmlog/records/new?supplierId=sup-1&plotId=plot-1',
    );
  });

  it('hides "ตรวจแปลง" for an inactive plot', async () => {
    getPlotMock.mockResolvedValue(basePlot({ isActive: false }));

    renderPage();

    await screen.findByRole('link', { name: 'แก้ไขแปลง' });
    expect(screen.queryByRole('link', { name: 'ตรวจแปลง' })).toBeNull();
  });

  it('renders current_* fields verbatim, without recomputing from records', async () => {
    // No active cycle — isolates CurrentStatusSection's own rendering from
    // CurrentCycleSection (round 7.3), which would otherwise independently
    // render its own crop/variety text from the active cycle too.
    listPlotCyclesMock.mockResolvedValue([]);
    getPlotMock.mockResolvedValue(basePlot({
      currentCrop: 'พริก',
      currentVariety: 'พริกขี้หนู',
      currentStage: 'ออกดอก',
      currentYieldPct: '95.5',
      currentGpsLat: '13.7563000',
      currentGpsLng: '100.5018000',
      lastInspectedByCode: 'FIELD01',
      lastInspectionRecordId: 'rec-1',
    }));
    getRecordMock.mockResolvedValue({
      id: 'rec-1', photoUrls: [],
    });

    renderPage();

    expect(await screen.findByText('พริก')).toBeTruthy();
    expect(await screen.findByText('พริกขี้หนู')).toBeTruthy();
    // "95.5%" now legitimately renders twice (round 18): once in current
    // status "Yield ล่าสุด" and again inside the Yield Planning card's
    // "Current Yield %" field — both read the same plot.currentYieldPct.
    expect((await screen.findAllByText('95.5%')).length).toBeGreaterThanOrEqual(1);
    expect(await screen.findByText(/13\.756300, 100\.501800/)).toBeTruthy();
    expect(await screen.findByText('FIELD01')).toBeTruthy();

    // getRecord (full record fetch) is only used for the latest photos —
    // no per-record aggregation logic runs client-side for the numbers
    // above; they come straight from the getPlot() response.
    expect(getPlotMock).toHaveBeenCalledWith('plot-1');
  });

  it('shows "ยังไม่มีการตรวจ" instead of empty fields when never inspected', async () => {
    getPlotMock.mockResolvedValue(basePlot({ lastInspectionRecordId: null }));

    renderPage();

    expect(await screen.findByText('ยังไม่มีการตรวจแปลงนี้')).toBeTruthy();
    expect(getRecordMock).not.toHaveBeenCalled();
  });
});

describe('PlotDetail — yield planning (round 17, hero layout round 18; cycle-sourced round 8.0.4)', () => {
  it('shows a "ต้องตั้งค่าแผนผลผลิต" warning when the ACTIVE CYCLE has neither plantCount nor expectedYieldFull set', async () => {
    listPlotCyclesMock.mockResolvedValue([oneCycle({
      plantCount: null, expectedYieldFull: null, expectedYieldUnit: null,
    })]);
    getPlotMock.mockResolvedValue(basePlot());

    renderPage();

    expect(await screen.findByText('ต้องตั้งค่าแผนผลผลิต')).toBeTruthy();
    expect(screen.getByText(/ยังไม่ตั้งแผนผลผลิต/)).toBeTruthy();
  });

  it('shows a waiting-for-inspection message when the active cycle has a plan but there is no inspection yet', async () => {
    // Default active cycle from beforeEach (oneCycle()) already carries a
    // complete plan (plantCount=500, expectedYieldFull=1000.00 kg).
    getPlotMock.mockResolvedValue(basePlot({
      currentYieldPct: null, lastInspectionRecordId: null,
    }));

    renderPage();

    expect(await screen.findByText('รอข้อมูลจากการตรวจแปลงครั้งแรก')).toBeTruthy();
    expect(screen.queryByText('ต้องตั้งค่าแผนผลผลิต')).toBeNull();
  });

  it('computes base 1 kg + current 50% = 0.5 kg from the ACTIVE CYCLE plan, never the plot mirror (round 8.0.4)', async () => {
    // The plot mirror deliberately disagrees (999/999.00/ตัน) — the active
    // cycle must win; the mirror value must never render anywhere.
    listPlotCyclesMock.mockResolvedValue([oneCycle({
      plantCount: 100, expectedYieldFull: '1.00', expectedYieldUnit: 'kg',
    })]);
    getPlotMock.mockResolvedValue(basePlot({
      plantCount: 999, expectedYieldFull: '999.00', expectedYieldUnit: 'ตัน',
      currentYieldPct: '50', lastInspectionRecordId: 'rec-1',
    }));
    getRecordMock.mockResolvedValue({ id: 'rec-1', photoUrls: [] });

    renderPage();

    expect((await screen.findAllByText('0.5 kg')).length).toBeGreaterThan(0);
    expect(screen.getAllByText('50% ของ 1 kg = 0.5 kg').length).toBeGreaterThan(0);
    expect(screen.queryByText(/999/)).toBeNull();
    expect(screen.queryByText('ตัน')).toBeNull();
  });

  it('computes base 1000 kg + current 80% = 800 kg with a plain-language formula (both the hero card and the current-cycle card show it)', async () => {
    listPlotCyclesMock.mockResolvedValue([oneCycle({
      plantCount: 5000, expectedYieldFull: '1000.00', expectedYieldUnit: 'kg',
    })]);
    getPlotMock.mockResolvedValue(basePlot({
      currentYieldPct: '80', lastInspectionRecordId: 'rec-1',
    }));
    getRecordMock.mockResolvedValue({ id: 'rec-1', photoUrls: [] });

    renderPage();

    expect((await screen.findAllByText('800 kg')).length).toBeGreaterThan(0);
    expect(screen.getAllByText('80% ของ 1,000 kg = 800 kg').length).toBeGreaterThan(0);
    expect(screen.getAllByText('5,000').length).toBeGreaterThan(0);
    expect(screen.getAllByText('1,000 kg').length).toBeGreaterThan(0);
  });
});

describe('PlotDetail — history', () => {
  it('renders history rows in the order the API returned them (no client re-sort)', async () => {
    getPlotMock.mockResolvedValue(basePlot());
    listRecordsMock.mockResolvedValue([
      baseRecordSummary({ id: 'rec-newest', recordDate: '2026-07-05' }),
      baseRecordSummary({ id: 'rec-older', recordDate: '2026-07-01' }),
    ]);

    renderPage();

    const dates = await screen.findAllByText(/2026-07-0[15]/);
    expect(dates[0].textContent).toBe('2026-07-05');
    expect(dates[1].textContent).toBe('2026-07-01');
  });

  it('shows an empty-history message when the plot has no records yet', async () => {
    getPlotMock.mockResolvedValue(basePlot());
    listRecordsMock.mockResolvedValue([]);

    renderPage();

    expect(await screen.findByText('ยังไม่มีประวัติการตรวจ')).toBeTruthy();
  });

  it('tags each history row with its "รอบที่ N" cycle badge (round 7.4)', async () => {
    getPlotMock.mockResolvedValue(basePlot());
    listPlotCyclesMock.mockResolvedValue([]);
    listRecordsMock.mockResolvedValue([
      baseRecordSummary({ id: 'rec-c2', recordDate: '2026-07-05', cycleNo: 2 }),
      baseRecordSummary({ id: 'rec-c1', recordDate: '2026-07-01', cycleNo: 1 }),
    ]);

    renderPage();

    // Distinct badges per record → a multi-cycle history no longer reads as if
    // every record belongs to the current cycle.
    expect(await screen.findByText('รอบที่ 2')).toBeTruthy();
    expect(screen.getByText('รอบที่ 1')).toBeTruthy();
  });

  it('history rows show the record\'s OWN cycleLabel instead of "รอบที่ N" when set (round 8.0.5)', async () => {
    getPlotMock.mockResolvedValue(basePlot());
    listPlotCyclesMock.mockResolvedValue([]);
    listRecordsMock.mockResolvedValue([
      baseRecordSummary({ id: 'rec-c2', recordDate: '2026-07-05', cycleNo: 2, cycleLabel: 'jul2026' }),
      baseRecordSummary({ id: 'rec-c1', recordDate: '2026-07-01', cycleNo: 1, cycleLabel: null }),
    ]);

    renderPage();

    // rec-c2 leads with its own label; rec-c1 (no label) falls back to รอบที่ N.
    expect(await screen.findByText('jul2026')).toBeTruthy();
    expect(screen.getByText('รอบที่ 1')).toBeTruthy();
    expect(screen.queryByText('รอบที่ 2')).toBeNull();
  });
});

describe('PlotDetail — history row submittedByCode retirement (round 8-3G)', () => {
  it('shows the historical code + name for an old record that still has one', async () => {
    getPlotMock.mockResolvedValue(basePlot());
    listRecordsMock.mockResolvedValue([
      baseRecordSummary({ submittedByCode: 'FIELD01', submittedByName: 'สมชาย' }),
    ]);

    renderPage();

    expect(await screen.findByText(/FIELD01 — สมชาย/)).toBeTruthy();
  });

  it('shows just the name (no code) when submittedByCode is null but a name was given', async () => {
    getPlotMock.mockResolvedValue(basePlot());
    listRecordsMock.mockResolvedValue([
      baseRecordSummary({ submittedByCode: null, submittedByName: 'สมหญิง' }),
    ]);

    renderPage();

    expect(await screen.findByText(/ผู้กรอก:/)).toBeTruthy();
    expect(screen.getByText(/สมหญิง/)).toBeTruthy();
  });

  it('omits the ผู้กรอก line entirely when both code and name are null (new record)', async () => {
    getPlotMock.mockResolvedValue(basePlot());
    listRecordsMock.mockResolvedValue([
      baseRecordSummary({ submittedByCode: null, submittedByName: null }),
    ]);

    renderPage();

    await screen.findByText('2026-07-01');
    expect(screen.queryByText(/ผู้กรอก:/)).toBeNull();
  });

  it('never renders the literal strings "null" or "undefined" in the collapsed row', async () => {
    getPlotMock.mockResolvedValue(basePlot());
    listRecordsMock.mockResolvedValue([
      baseRecordSummary({ submittedByCode: null, submittedByName: null }),
    ]);

    renderPage();

    await screen.findByText('2026-07-01');
    expect(document.body.textContent).not.toContain('null');
    expect(document.body.textContent).not.toContain('undefined');
  });
});

describe('PlotDetail — protocol snapshot labels (round 5.3)', () => {
  const growthSnapshot = {
    inspectionProtocolSnapshot: {
      version: 1, growthStage: 'เจริญเติบโต',
      criteria: [
        { slot: 'fieldPrepScore', label: 'สภาพอากาศ', score: 8 },
        { slot: 'weatherScore', label: 'การดูแลรักษา', score: 7 },
        { slot: 'careScore', label: 'ความเสี่ยง', score: 9 },
        { slot: 'varietyResistanceScore', label: 'สภาพแปลง', score: 6 },
      ],
    },
  };

  it('expanded history row renders score labels from the record snapshot', async () => {
    // basePlot has lastInspectionRecordId=null, so CurrentStatusSection does
    // not fetch — getRecord is exercised only by the history-row expand.
    getPlotMock.mockResolvedValue(basePlot());
    listRecordsMock.mockResolvedValue([
      baseRecordSummary({ id: 'rec-1', growthStage: 'เจริญเติบโต' }),
    ]);
    getRecordMock.mockResolvedValue({
      id: 'rec-1', photoUrls: [], recommendation: null, notes: null,
      fieldPrepScore: 8, weatherScore: 7, careScore: 9, varietyResistanceScore: 6,
      customFields: growthSnapshot,
    });

    renderPage();

    // Collapsed glance shows bare numbers only — no criterion label there.
    await screen.findByText('คะแนน 4 ด้าน:');
    expect(screen.queryByText('ความเสี่ยง')).toBeNull();

    fireEvent.click(screen.getByText('2026-07-01'));

    // After expand + detail fetch, the snapshot labels appear.
    expect(await screen.findByText('ความเสี่ยง: 9/10')).toBeTruthy();
    expect(screen.getByText('สภาพแปลง: 6/10')).toBeTruthy();
  });

  it('current status score labels come from the latest record snapshot, values from plot.current_*', async () => {
    getPlotMock.mockResolvedValue(basePlot({
      lastInspectionRecordId: 'rec-latest',
      currentStage: 'เจริญเติบโต',
      currentFieldPrepScore: 8,
      currentWeatherScore: 7,
      currentCareScore: 9,
      currentVarietyResistanceScore: 6,
    }));
    getRecordMock.mockResolvedValue({
      id: 'rec-latest', photoUrls: [], customFields: growthSnapshot,
    });

    renderPage();

    // careScore is labelled "ความเสี่ยง" by this stage's snapshot; its value
    // is the plot.current_* score (9), read verbatim — not the snapshot's.
    expect(await screen.findByText('ความเสี่ยง: 9/10')).toBeTruthy();
    expect(screen.getByText('สภาพแปลง: 6/10')).toBeTruthy();
    // The default germination label must not leak in for this stage.
    expect(screen.queryByText(/^การเตรียมแปลง:/)).toBeNull();
  });

  it('current status falls back to default labels for an old record with no snapshot', async () => {
    getPlotMock.mockResolvedValue(basePlot({
      lastInspectionRecordId: 'rec-old',
      currentFieldPrepScore: 5,
    }));
    getRecordMock.mockResolvedValue({ id: 'rec-old', photoUrls: [], customFields: {} });

    renderPage();

    expect(await screen.findByText('การเตรียมแปลง: 5/10')).toBeTruthy();
  });

  it('expanded history row shows the phone-access attribution for a public-flow record (round 8-3E)', async () => {
    getPlotMock.mockResolvedValue(basePlot());
    listRecordsMock.mockResolvedValue([
      baseRecordSummary({ id: 'rec-1', growthStage: 'เจริญเติบโต' }),
    ]);
    getRecordMock.mockResolvedValue({
      id: 'rec-1', photoUrls: [], recommendation: null, notes: null,
      fieldPrepScore: null, weatherScore: null, careScore: null, varietyResistanceScore: null,
      customFields: {},
      submittedPhoneSnapshot: '0845552162', submittedPhoneType: 'primary', inspectorType: 'farmer',
    });

    renderPage();
    await screen.findByText('คะแนน 4 ด้าน:');
    fireEvent.click(screen.getByText('2026-07-01'));

    expect(await screen.findByText(/084-555-2162/)).toBeTruthy();
    expect(screen.getByText(/เข้าตรวจในฐานะ เกษตรกร/)).toBeTruthy();
  });

  // Item 21 — a migrated DEV record ('extension' → 'chiatai') renders with the
  // new shared label in the inspection history, never the retired wording and
  // never the raw enum.
  it('expanded history row shows the Chiatai inspector label (round 8-11A)', async () => {
    getPlotMock.mockResolvedValue(basePlot());
    listRecordsMock.mockResolvedValue([
      baseRecordSummary({ id: 'rec-3', growthStage: 'เจริญเติบโต' }),
    ]);
    getRecordMock.mockResolvedValue({
      id: 'rec-3', photoUrls: [], recommendation: null, notes: null,
      fieldPrepScore: null, weatherScore: null, careScore: null, varietyResistanceScore: null,
      customFields: {},
      submittedPhoneSnapshot: '0866661234', submittedPhoneType: 'primary', inspectorType: 'chiatai',
    });

    renderPage();
    await screen.findByText('คะแนน 4 ด้าน:');
    fireEvent.click(screen.getByText('2026-07-01'));

    expect(await screen.findByText(/เข้าตรวจในฐานะ Chiatai/)).toBeTruthy();
    expect(screen.queryByText(/ส่งเสริม/)).toBeNull();
  });

  it('expanded history row shows a generic fallback for a logged-in-flow record with no phone binding', async () => {
    getPlotMock.mockResolvedValue(basePlot());
    listRecordsMock.mockResolvedValue([
      baseRecordSummary({ id: 'rec-2', growthStage: 'เจริญเติบโต' }),
    ]);
    getRecordMock.mockResolvedValue({
      id: 'rec-2', photoUrls: [], recommendation: null, notes: null,
      fieldPrepScore: null, weatherScore: null, careScore: null, varietyResistanceScore: null,
      customFields: {},
    });

    renderPage();
    await screen.findByText('คะแนน 4 ด้าน:');
    fireEvent.click(screen.getByText('2026-07-01'));

    expect(await screen.findByText('ผู้ใช้ในระบบ / ข้อมูลเดิม')).toBeTruthy();
  });
});

describe('PlotDetail — operational actions (round 6.1)', () => {
  it('renders ตรวจแปลง / แก้ไขแปลง / พิมพ์ QR when permitted and qrKey is present (round 8.0: มอบหมาย removed)', async () => {
    getPlotMock.mockResolvedValue(basePlot({ qrKey: 'qr-1' }));
    renderPage();

    expect(await screen.findByRole('link', { name: 'ตรวจแปลง' })).toBeTruthy();
    expect(screen.getByRole('link', { name: 'แก้ไขแปลง' })).toBeTruthy();
    expect(screen.getByRole('button', { name: 'พิมพ์ QR' })).toBeTruthy();
    // มอบหมาย is no longer surfaced in PlotDetail (round 8.0)
    expect(screen.queryByRole('link', { name: 'มอบหมาย' })).toBeNull();
  });

  it('hides permission-gated actions but still shows พิมพ์ QR with only plots.read', async () => {
    allowedPerms = new Set(['plots.read']); // no create/update/assign
    getPlotMock.mockResolvedValue(basePlot({ qrKey: 'qr-1' }));
    renderPage();

    expect(await screen.findByRole('button', { name: 'พิมพ์ QR' })).toBeTruthy();
    expect(screen.queryByRole('link', { name: 'ตรวจแปลง' })).toBeNull();
    expect(screen.queryByRole('link', { name: 'แก้ไขแปลง' })).toBeNull();
    // มอบหมาย was already removed from PlotDetail (round 8.0)
    expect(screen.queryByRole('link', { name: 'มอบหมาย' })).toBeNull();
  });

  it('does not show พิมพ์ QR when the plot has no qrKey', async () => {
    getPlotMock.mockResolvedValue(basePlot({ qrKey: null }));
    renderPage();

    await screen.findByRole('link', { name: 'แก้ไขแปลง' });
    expect(screen.queryByRole('button', { name: 'พิมพ์ QR' })).toBeNull();
  });

  it('opens the QR print sheet from the พิมพ์ QR button', async () => {
    getPlotMock.mockResolvedValue(basePlot({ qrKey: 'qr-1' }));
    renderPage();

    fireEvent.click(await screen.findByRole('button', { name: 'พิมพ์ QR' }));
    expect(await screen.findByText(/พิมพ์ QR แปลง \(1 รายการ\)/)).toBeTruthy();
  });

  it('offers a "แก้รอบปลูก" action (opens EditCycleModal, not the Plot Edit modal) when the active cycle\'s yield plan is incomplete (round 8.0.4)', async () => {
    listPlotCyclesMock.mockResolvedValue([oneCycle({
      plantCount: null, expectedYieldFull: null, expectedYieldUnit: null,
    })]);
    getPlotMock.mockResolvedValue(basePlot());
    renderPage();

    const buttons = await screen.findAllByRole('button', { name: 'แก้รอบปลูก' });
    expect(buttons.length).toBeGreaterThan(0);
    fireEvent.click(buttons[0]);

    // Opens the EditCycleModal (cycle plan fields), not a navigation.
    expect(await screen.findByText(/แก้รอบปลูก — รอบที่ 1/)).toBeTruthy();
  });
});

describe('PlotDetail — identity labels (round 8-2.6)', () => {
  it('shows separate labelled Supplier/รหัสแปลง/ชื่อแปลง fields instead of the old combined "code — name" text', async () => {
    getPlotMock.mockResolvedValue(basePlot({
      supplierName: 'ซัพพลายเออร์ลุงสิบ', supplierCode: 'SUP010',
      plotCode: 'SUP010-P001', name: 'แปลงลุงสิบ',
    }));

    renderPage();

    expect(await screen.findByText('ชื่อ Supplier')).toBeTruthy();
    expect(screen.getByText('ซัพพลายเออร์ลุงสิบ (SUP010)')).toBeTruthy();

    expect(screen.getByText('รหัสแปลง')).toBeTruthy();
    expect(screen.getByText('SUP010-P001')).toBeTruthy();

    expect(screen.getByText('ชื่อแปลง')).toBeTruthy();
    // Appears twice: the H1 and the labelled "ชื่อแปลง" field.
    expect(screen.getAllByText('แปลงลุงสิบ').length).toBeGreaterThanOrEqual(2);

    // The old ambiguous combined string must no longer render anywhere.
    expect(screen.queryByText('SUP010-P001 — แปลงลุงสิบ')).toBeNull();
    expect(screen.queryByText(/SUP010-P001\s*—/)).toBeNull();
  });

  it('H1 still shows the plot name for at-a-glance identification', async () => {
    getPlotMock.mockResolvedValue(basePlot({ name: 'แปลงลุงสิบ' }));

    renderPage();

    const h1 = await screen.findByRole('heading', { level: 1 });
    expect(h1.textContent).toBe('แปลงลุงสิบ');
  });

  it('falls back to supplierCode when supplierName is empty', async () => {
    getPlotMock.mockResolvedValue(basePlot({ supplierName: '', supplierCode: 'SUP010' }));

    renderPage();

    await screen.findByText('ชื่อ Supplier');
    expect(screen.getByText('SUP010')).toBeTruthy();
  });

  it('shows — when both supplierName and supplierCode are empty', async () => {
    getPlotMock.mockResolvedValue(basePlot({ supplierName: '', supplierCode: '' }));

    renderPage();

    const dt = await screen.findByText('ชื่อ Supplier');
    const dd = dt.nextElementSibling;
    expect(dd?.textContent).toBe('—');
  });

  it('ที่ตั้ง keeps its own label, separate from the identity fields', async () => {
    getPlotMock.mockResolvedValue(basePlot({
      village: 'บ้านสิบโป่ง', district: 'แม่ริม', province: 'เชียงใหม่',
    }));

    renderPage();

    expect(await screen.findByText('ที่ตั้ง')).toBeTruthy();
    expect(screen.getByText('บ้านสิบโป่ง, แม่ริม, เชียงใหม่')).toBeTruthy();
  });
});

describe('PlotDetail — not found', () => {
  it('shows a not-found message instead of crashing when getPlot fails', async () => {
    getPlotMock.mockRejectedValue(new Error('404'));

    renderPage();

    await waitFor(() => expect(screen.getByText(/ไม่พบข้อมูลแปลงนี้/)).toBeTruthy());
  });
});

describe('PlotDetail — plot cycle lifecycle (round 7.3)', () => {
  it('renders the current cycle section (status/crop/variety/lot/plant count/yield plan) when an active cycle exists', async () => {
    getPlotMock.mockResolvedValue(basePlot());
    listPlotCyclesMock.mockResolvedValue([oneCycle()]);

    renderPage();

    // Both "กำลังปลูก" and the cycle title appear twice: the current-cycle
    // section AND the (single-entry) cycle history below it render the same
    // active cycle.
    await waitFor(() => expect(screen.getAllByText('กำลังปลูก').length).toBeGreaterThanOrEqual(1));
    expect(screen.getAllByText('รอบที่ 1 · พริก · LOT-01').length).toBeGreaterThanOrEqual(1);
    // 500/"1,000 kg" render in both the current-cycle section AND the
    // yield-planning hero card (round 8.0.4 — both are cycle-sourced now).
    expect(screen.getAllByText('500').length).toBeGreaterThanOrEqual(1);
    expect(screen.getAllByText('1,000 kg').length).toBeGreaterThanOrEqual(1);
  });

  it('displays the cycleLabel (jun2026) instead of "รอบที่ N" in the current cycle + history when set (round 8.0)', async () => {
    getPlotMock.mockResolvedValue(basePlot());
    listPlotCyclesMock.mockResolvedValue([oneCycle({ cycleLabel: 'jun2026' })]);

    renderPage();

    // formatCycleTitle leads with the label; appears in both the current-cycle
    // section and the single-entry history.
    await waitFor(() => expect(screen.getAllByText('jun2026 · พริก · LOT-01').length).toBeGreaterThanOrEqual(1));
    expect(screen.queryByText('รอบที่ 1 · พริก · LOT-01')).toBeNull();
  });

  it('falls back to "รอบที่ N" when cycleLabel is null (round 8.0)', async () => {
    getPlotMock.mockResolvedValue(basePlot());
    listPlotCyclesMock.mockResolvedValue([oneCycle({ cycleLabel: null, cycleNo: 2 })]);

    renderPage();

    expect((await screen.findAllByText('รอบที่ 2 · พริก · LOT-01')).length).toBeGreaterThanOrEqual(1);
  });

  it('shows "รอเริ่มรอบปลูก" with a "เริ่มรอบปลูกใหม่" button when there is no active cycle', async () => {
    getPlotMock.mockResolvedValue(basePlot());
    listPlotCyclesMock.mockResolvedValue([]);

    renderPage();

    // Round 8.0.4 — both the current-cycle section AND the yield-planning
    // hero card independently show the no-active-cycle state.
    expect((await screen.findAllByText('รอเริ่มรอบปลูก')).length).toBeGreaterThanOrEqual(1);
    expect(screen.getByText('แปลงนี้ยังใช้งานอยู่ แต่ยังไม่มีรอบปลูกที่เปิดอยู่')).toBeTruthy();
    expect(screen.getAllByRole('button', { name: 'เริ่มรอบปลูกใหม่' }).length).toBeGreaterThanOrEqual(1);
  });

  it('hides "เริ่มรอบปลูกใหม่" without plots.update', async () => {
    allowedPerms = new Set(['plots.read', 'records.create']);
    getPlotMock.mockResolvedValue(basePlot());
    listPlotCyclesMock.mockResolvedValue([]);

    renderPage();

    await screen.findAllByText('รอเริ่มรอบปลูก');
    expect(screen.queryByRole('button', { name: 'เริ่มรอบปลูกใหม่' })).toBeNull();
  });

  it('disables ตรวจแปลง with an explanatory tooltip when the plot has no active cycle', async () => {
    getPlotMock.mockResolvedValue(basePlot());
    listPlotCyclesMock.mockResolvedValue([]);

    renderPage();

    const btn = await screen.findByRole('button', { name: 'ตรวจแปลง' }) as HTMLButtonElement;
    expect(btn.disabled).toBe(true);
    expect(btn.getAttribute('title')).toContain('ต้องเริ่มรอบปลูกก่อน');
    expect(screen.queryByRole('link', { name: 'ตรวจแปลง' })).toBeNull();
  });

  it('starting a new cycle calls createPlotCycle and refreshes the plot/cycle data', async () => {
    getPlotMock.mockResolvedValue(basePlot());
    listPlotCyclesMock.mockResolvedValueOnce([]).mockResolvedValue([oneCycle()]);
    createPlotCycleMock.mockResolvedValue(oneCycle());

    renderPage();

    // Round 8.0.4 — both the current-cycle section AND the yield-planning
    // hero card show a "เริ่มรอบปลูกใหม่" button when there's no active
    // cycle; either one opens the same StartCycleModal.
    fireEvent.click((await screen.findAllByRole('button', { name: 'เริ่มรอบปลูกใหม่' }))[0]);
    fireEvent.change(screen.getByPlaceholderText('เช่น PO25001'), { target: { value: 'PO25001' } });
    fireEvent.change(screen.getByPlaceholderText('เช่น Melon-A'), { target: { value: 'Melon-A' } });
    // Round 8-12B — Auto Lot needs a cycleLabel as well as a P.Code.
    fireEvent.change(screen.getByPlaceholderText('เช่น jun2026 หรือ may2026'), { target: { value: '2605' } });
    fireEvent.click(await screen.findByRole('button', { name: 'เริ่มรอบปลูก' }));

    await waitFor(() => expect(createPlotCycleMock).toHaveBeenCalledWith('plot-1', expect.any(Object)));
    // Modal closed and the section now reflects the (refetched) active cycle
    // — "กำลังปลูก" renders twice (current-cycle section + its history row).
    await waitFor(() => expect(screen.queryByRole('button', { name: 'เริ่มรอบปลูกใหม่' })).toBeNull());
    await waitFor(() => expect(screen.getAllByText('กำลังปลูก').length).toBeGreaterThanOrEqual(1));
  });

  it('start-cycle modal has a ชื่อรอบปลูก input and sends its value to createPlotCycle (round 8.0)', async () => {
    getPlotMock.mockResolvedValue(basePlot());
    listPlotCyclesMock.mockResolvedValueOnce([]).mockResolvedValue([oneCycle({ cycleLabel: 'jul2026' })]);
    createPlotCycleMock.mockResolvedValue(oneCycle({ cycleLabel: 'jul2026' }));

    renderPage();

    fireEvent.click((await screen.findAllByRole('button', { name: 'เริ่มรอบปลูกใหม่' }))[0]);
    const labelInput = await screen.findByPlaceholderText('เช่น jun2026 หรือ may2026');
    fireEvent.change(labelInput, { target: { value: 'jul2026' } });
    fireEvent.change(screen.getByPlaceholderText('เช่น PO25001'), { target: { value: 'PO25001' } });
    fireEvent.change(screen.getByPlaceholderText('เช่น Melon-A'), { target: { value: 'Melon-A' } });
    // (cycleLabel was already set above — Auto Lot needs it, and this test is
    // specifically about that field reaching the payload.)
    fireEvent.click(await screen.findByRole('button', { name: 'เริ่มรอบปลูก' }));

    await waitFor(() => expect(createPlotCycleMock).toHaveBeenCalledWith(
      'plot-1', expect.objectContaining({ cycleLabel: 'jul2026' }),
    ));
  });

  it('editing the active cycle calls updatePlotCycle with the cycle id and refreshes data', async () => {
    getPlotMock.mockResolvedValue(basePlot());
    listPlotCyclesMock.mockResolvedValue([oneCycle()]);
    updatePlotCycleMock.mockResolvedValue(oneCycle({ crop: 'ทุเรียน' }));

    renderPage();

    fireEvent.click(await screen.findByRole('button', { name: 'แก้รอบปลูก' }));
    // Round 8-17A.1 — oneCycle()'s default cycleLabel is null (a legacy
    // cycle), and cycleLabel is now required on every edit submit — the
    // user must fill it in before saving, even when editing an unrelated
    // field. Without this the modal blocks submit and updatePlotCycle is
    // never called.
    fireEvent.change(
      await screen.findByPlaceholderText('เช่น jun2026 หรือ may2026'), { target: { value: 'jun2026' } },
    );
    fireEvent.click(await screen.findByRole('button', { name: 'บันทึก' }));

    await waitFor(() => expect(updatePlotCycleMock).toHaveBeenCalledWith(
      'plot-1', 'cycle-1', expect.any(Object),
    ));
  });

  it('closing the active cycle calls closePlotCycle and afterward the page shows the no-active-cycle state', async () => {
    getPlotMock.mockResolvedValue(basePlot());
    listPlotCyclesMock.mockResolvedValueOnce([oneCycle()]).mockResolvedValue([
      oneCycle({ status: 'harvested', closedAt: '2026-07-01T00:00:00Z', closeReason: 'เก็บเกี่ยวแล้ว' }),
    ]);
    closePlotCycleMock.mockResolvedValue(oneCycle({ status: 'harvested' }));

    renderPage();

    fireEvent.click(await screen.findByRole('button', { name: 'ปิดรอบปลูก' }));
    fireEvent.click(await screen.findByRole('button', { name: 'ยืนยันปิดรอบปลูก' }));

    await waitFor(() => expect(closePlotCycleMock).toHaveBeenCalledWith(
      'plot-1', 'cycle-1', expect.objectContaining({ status: 'harvested' }),
    ));
    // ตรวจแปลง disappears (no active cycle) once the refetched state lands.
    await waitFor(() => expect(screen.queryByRole('link', { name: 'ตรวจแปลง' })).toBeNull());
    expect((await screen.findAllByText('รอเริ่มรอบปลูก')).length).toBeGreaterThanOrEqual(1);
  });

  it('cycle history renders harvested/cancelled badges alongside the active one', async () => {
    getPlotMock.mockResolvedValue(basePlot());
    listPlotCyclesMock.mockResolvedValue([
      oneCycle({ id: 'cycle-2', cycleNo: 2, status: 'active', crop: 'ทุเรียน', lotNo: 'LOT-02' }),
      oneCycle({ id: 'cycle-1', cycleNo: 1, status: 'harvested', closedAt: '2026-05-01T00:00:00Z', closeReason: 'เก็บเกี่ยวรอบแรก' }),
    ]);

    renderPage();

    await screen.findByText('ประวัติรอบปลูก');
    // Round 8-10A — history is a table now: the "รอบปลูก" cell is the cycle's
    // display NAME (label, else "รอบที่ N"), and crop/lot moved to their own
    // columns. The current-cycle section above still uses the compact title.
    expect(screen.getAllByText('รอบที่ 2').length).toBeGreaterThanOrEqual(1);
    expect(screen.getByText('รอบที่ 1')).toBeTruthy();
    expect(screen.getAllByText('กำลังปลูก').length).toBeGreaterThanOrEqual(1);
    expect(screen.getByText('เก็บเกี่ยวแล้ว')).toBeTruthy();
    expect(screen.getByText('เหตุผล: เก็บเกี่ยวรอบแรก')).toBeTruthy();
  });

  it('round 8-3K: labels the current cycle\'s Lot No. clearly as "เลขล็อต (Lot No.)"', async () => {
    getPlotMock.mockResolvedValue(basePlot());
    // The single active cycle also appears in the history table below (an
    // active cycle is always its own most-recent history row too) — so the
    // label renders as a <dt> in the current-cycle section AND as a <th> in
    // the table; both must lead to the same value.
    listPlotCyclesMock.mockResolvedValue([oneCycle({ lotNo: 'LOT-09' })]);

    renderPage();

    const labels = await screen.findAllByText('Lot No ระบบ');
    expect(labels.length).toBeGreaterThanOrEqual(1);
    const currentCycleLabel = labels.find((el) => el.tagName === 'DT');
    // Round 8-5B — the value carries a source badge, so match the substring.
    expect(currentCycleLabel?.nextElementSibling?.textContent).toContain('LOT-09');
    // ...and the table shows the same lot in its own column.
    expect(screen.getAllByText('LOT-09').length).toBeGreaterThanOrEqual(2);
    // A lot with no source tag (legacy) shows the "ข้อมูลเดิม" badge.
    expect(screen.getAllByText('ข้อมูลเดิม').length).toBeGreaterThanOrEqual(1);
  });

  it('round 8-5B: shows the current cycle PO / P.Code and an "อัตโนมัติ" lot-source badge', async () => {
    getPlotMock.mockResolvedValue(basePlot());
    listPlotCyclesMock.mockResolvedValue([oneCycle({
      poNumber: 'PO25001', pCode: 'Melon-A', lotNo: 'PO25001-P001-01', lotNoSource: 'auto', lotRunningNo: 1, supplierLotNo: null,
    })]);

    renderPage();

    expect((await screen.findAllByText('PO Number')).length).toBeGreaterThanOrEqual(1);
    expect(screen.getAllByText('PO25001').length).toBeGreaterThanOrEqual(1);
    expect(screen.getAllByText('Melon-A').length).toBeGreaterThanOrEqual(1);
    // Auto lot → the "อัตโนมัติ" badge (never "กรอกเอง"/"ข้อมูลเดิม" here).
    expect(screen.getAllByText('อัตโนมัติ').length).toBeGreaterThanOrEqual(1);
  });

  it('round 8-13B: the current cycle shows an em dash when PO Number is null (blank-PO cycle), never a crash', async () => {
    getPlotMock.mockResolvedValue(basePlot());
    listPlotCyclesMock.mockResolvedValue([oneCycle({
      poNumber: null, pCode: 'WM-141', lotNo: '2605-SUP010-WM-141-001', lotNoSource: 'auto', lotRunningNo: 1,
    })]);

    renderPage();

    const labels = await screen.findAllByText('PO Number');
    const currentCycleLabel = labels.find((el) => el.tagName === 'DT');
    // the current-cycle field renders the shared "—" fallback (Field's
    // `value ?? <span>—</span>`), the same generic pattern that already
    // handles a legacy no-PO cycle — this is just proving it also covers a
    // brand-new cycle created with PO deliberately left blank (round 8-13A/B).
    expect(currentCycleLabel?.nextElementSibling?.textContent).toBe('—');
    expect(screen.getAllByText('WM-141').length).toBeGreaterThanOrEqual(1); // P.Code still shows fine
    expect(screen.queryByText('กรอกเอง')).toBeNull();
  });

  it('round 8-3K/8-10A: every history row carries its OWN Lot No. in the Lot column', async () => {
    getPlotMock.mockResolvedValue(basePlot());
    listPlotCyclesMock.mockResolvedValue([
      oneCycle({ id: 'cycle-2', cycleNo: 2, status: 'active', crop: 'ทุเรียน', lotNo: 'LOT-02' }),
      oneCycle({ id: 'cycle-1', cycleNo: 1, status: 'harvested', closedAt: '2026-05-01T00:00:00Z', lotNo: 'LOT-01' }),
    ]);

    renderPage();

    await screen.findByText('ประวัติรอบปลูก');
    const lotColumnIndex = historyColumnIndex('Lot No ระบบ');
    // Each row's lot comes from THAT row's cycle — never inherited from the
    // active one.
    expect(historyCellText('รอบที่ 2', lotColumnIndex)).toContain('LOT-02');
    expect(historyCellText('รอบที่ 1', lotColumnIndex)).toContain('LOT-01');
    // cycle-2 is the active cycle too, so LOT-02 also appears above the table.
    expect(screen.getAllByText('LOT-02').length).toBeGreaterThanOrEqual(2);
  });

  it('shows an empty-history message when the plot has no cycles at all', async () => {
    getPlotMock.mockResolvedValue(basePlot());
    listPlotCyclesMock.mockResolvedValue([]);

    renderPage();

    expect(await screen.findByText('ยังไม่มีรอบปลูก')).toBeTruthy();
  });

  it('invalidates plot, plot-cycles, plots, and the plot-status report after starting a cycle', async () => {
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const invalidateSpy = vi.spyOn(qc, 'invalidateQueries');
    getPlotMock.mockResolvedValue(basePlot());
    listPlotCyclesMock.mockResolvedValueOnce([]).mockResolvedValue([oneCycle()]);
    createPlotCycleMock.mockResolvedValue(oneCycle());

    renderPage(qc);

    fireEvent.click((await screen.findAllByRole('button', { name: 'เริ่มรอบปลูกใหม่' }))[0]);
    fireEvent.change(screen.getByPlaceholderText('เช่น PO25001'), { target: { value: 'PO25001' } });
    fireEvent.change(screen.getByPlaceholderText('เช่น Melon-A'), { target: { value: 'Melon-A' } });
    // Round 8-12B — Auto Lot needs a cycleLabel as well as a P.Code.
    fireEvent.change(screen.getByPlaceholderText('เช่น jun2026 หรือ may2026'), { target: { value: '2605' } });
    fireEvent.click(await screen.findByRole('button', { name: 'เริ่มรอบปลูก' }));

    await waitFor(() => expect(createPlotCycleMock).toHaveBeenCalledOnce());
    const keys = invalidateSpy.mock.calls
      .map((c) => (c[0] as { queryKey?: unknown[] })?.queryKey)
      .filter(Boolean) as unknown[][];
    const firstKeys = keys.map((k) => k[0]);
    expect(firstKeys).toContain('plots');
    expect(firstKeys).toContain('report-plot-status');
    // round 8-2.8B — a cycle lifecycle change can freeze a final estimate,
    // so the Cycle Yield report is refreshed too.
    expect(firstKeys).toContain('report-cycle-yield');
    expect(keys.some((k) => k[0] === 'plot' && k[1] === 'plot-1')).toBe(true);
    expect(keys.some((k) => k[0] === 'plot-cycles' && k[1] === 'plot-1')).toBe(true);
  });
});

describe('PlotDetail — cycle final estimate snapshot (round 8-2.8B)', () => {
  it('harvested cycle shows "ผลผลิตประมาณการสุดท้าย" + the stored value verbatim', async () => {
    getPlotMock.mockResolvedValue(basePlot());
    listPlotCyclesMock.mockResolvedValue([
      oneCycle({
        id: 'cycle-1', cycleNo: 1, status: 'harvested',
        closedAt: '2026-09-01T00:00:00Z', expectedYieldUnit: 'kg',
        finalYieldPct: '80.0', finalEstimatedYield: '999.00', finalInspectionRecordId: 'rec-9',
      }),
    ]);

    renderPage();

    await screen.findByText('ประวัติรอบปลูก');
    // Round 8-10A — the label is now the column header; the value is the cell.
    expect(screen.getByRole('columnheader', { name: 'ประมาณการสุดท้าย' })).toBeTruthy();
    // verbatim stored value (999), never recomputed to expected×pct
    expect(historyCell('รอบที่ 1', 'ประมาณการสุดท้าย')).toBe('999 kg (80%)');
  });

  it('cancelled cycle uses "ประมาณการล่าสุดก่อนยกเลิก"', async () => {
    getPlotMock.mockResolvedValue(basePlot());
    listPlotCyclesMock.mockResolvedValue([
      oneCycle({
        id: 'cycle-1', cycleNo: 1, status: 'cancelled', closedAt: '2026-09-01T00:00:00Z',
        expectedYieldUnit: 'kg', finalYieldPct: '45.0', finalEstimatedYield: '405.00',
      }),
    ]);

    renderPage();

    await screen.findByText('ประวัติรอบปลูก');
    // A cancelled cycle's estimate is still shown verbatim in the same column;
    // the "ก่อนยกเลิก" wording lives in the shared describeFinalEstimate helper
    // and is covered by its own unit tests.
    expect(historyCell('รอบที่ 1', 'ประมาณการสุดท้าย')).toBe('405 kg (45%)');
    expect(historyCell('รอบที่ 1', 'สถานะ')).toContain('ยกเลิก');
  });

  it('closed cycle with a NULL snapshot shows "ไม่มีข้อมูลประมาณการตอนปิดรอบ"', async () => {
    getPlotMock.mockResolvedValue(basePlot());
    listPlotCyclesMock.mockResolvedValue([
      oneCycle({
        id: 'cycle-1', cycleNo: 1, status: 'harvested', closedAt: '2026-09-01T00:00:00Z',
        finalYieldPct: null, finalEstimatedYield: null, finalInspectionRecordId: null,
      }),
    ]);

    renderPage();

    await screen.findByText('ประวัติรอบปลูก');
    expect(screen.getByText(/ไม่มีข้อมูลประมาณการตอนปิดรอบ/)).toBeTruthy();
  });

  it('active cycle in history shows NO final snapshot line', async () => {
    getPlotMock.mockResolvedValue(basePlot());
    listPlotCyclesMock.mockResolvedValue([
      oneCycle({ id: 'cycle-1', cycleNo: 1, status: 'active' }),
    ]);

    renderPage();

    await screen.findByText('ประวัติรอบปลูก');
    // An active cycle has no final snapshot at all — the cell is a plain dash,
    // never a fabricated number and never the "no data" message meant for a
    // CLOSED cycle.
    expect(historyCell('รอบที่ 1', 'ประมาณการสุดท้าย')).toBe('—');
    expect(screen.queryByText(/ไม่มีข้อมูลประมาณการตอนปิดรอบ/)).toBeNull();
  });
});

describe('PlotDetail — actual harvest (round 8-7A/8-7B)', () => {
  it('shows harvest yield, after-clean yield, and harvest date, clearly separate from the estimate', async () => {
    getPlotMock.mockResolvedValue(basePlot());
    listPlotCyclesMock.mockResolvedValue([
      oneCycle({
        id: 'cycle-1', cycleNo: 1, status: 'harvested', closedAt: '2026-09-01T00:00:00Z',
        expectedYieldUnit: 'kg', finalYieldPct: '80.0', finalEstimatedYield: '800.00',
        finalInspectionRecordId: 'rec-9',
        harvestYield: 1250, finalYieldAfterClean: 1180, finalYieldUnit: 'kg',
        harvestDate: '2026-08-30', finalNote: 'ผลผลิตหลังคัดแยกและทำความสะอาด',
      }),
    ]);

    renderPage();

    await screen.findByText('ประวัติรอบปลูก');
    // Round 8-10A — estimate and actuals are now three SEPARATE columns, so a
    // reader can never mistake one for the other.
    expect(historyCell('รอบที่ 1', 'ประมาณการสุดท้าย')).toBe('800 kg (80%)');
    expect(historyCell('รอบที่ 1', 'ผลผลิตตอนเก็บเกี่ยว')).toBe('1,250 kg');
    expect(historyCell('รอบที่ 1', 'ผลผลิตจริงหลังทำความสะอาด')).toBe('1,180 kg');
    // Date-only string, verbatim (never through Date()).
    expect(historyCell('รอบที่ 1', 'วันที่เก็บเกี่ยว')).toBe('2026-08-30');
    expect(historyCell('รอบที่ 1', 'อ้างอิง')).toContain('หมายเหตุ: ผลผลิตหลังคัดแยกและทำความสะอาด');
  });

  it('an old/legacy cycle with every actual-harvest field null shows nothing extra and does not crash', async () => {
    getPlotMock.mockResolvedValue(basePlot());
    listPlotCyclesMock.mockResolvedValue([
      oneCycle({
        id: 'cycle-1', cycleNo: 1, status: 'harvested', closedAt: '2026-09-01T00:00:00Z',
        harvestYield: null, finalYieldAfterClean: null, finalYieldUnit: null,
        harvestDate: null, finalNote: null,
      }),
    ]);

    renderPage();

    await screen.findByText('ประวัติรอบปลูก');
    // Every actual-harvest cell is a dash — never a fabricated figure.
    expect(historyCell('รอบที่ 1', 'ผลผลิตตอนเก็บเกี่ยว')).toBe('—');
    expect(historyCell('รอบที่ 1', 'ผลผลิตจริงหลังทำความสะอาด')).toBe('—');
    expect(historyCell('รอบที่ 1', 'วันที่เก็บเกี่ยว')).toBe('—');
  });

  it('an active cycle never shows actual-harvest figures, even if fields somehow carry values', async () => {
    getPlotMock.mockResolvedValue(basePlot());
    listPlotCyclesMock.mockResolvedValue([
      oneCycle({
        id: 'cycle-1', cycleNo: 1, status: 'active',
        harvestYield: 1250, finalYieldAfterClean: 1180, finalYieldUnit: 'kg',
        harvestDate: '2026-08-30', finalNote: 'ไม่ควรแสดง',
      }),
    ]);

    renderPage();

    await screen.findByText('ประวัติรอบปลูก');
    // Actual harvest belongs to a CLOSED cycle only.
    expect(historyCell('รอบที่ 1', 'ผลผลิตตอนเก็บเกี่ยว')).toBe('—');
    expect(historyCell('รอบที่ 1', 'ผลผลิตจริงหลังทำความสะอาด')).toBe('—');
    expect(historyCell('รอบที่ 1', 'วันที่เก็บเกี่ยว')).toBe('—');
    expect(screen.queryByText(/ไม่ควรแสดง/)).toBeNull();
  });

  it('shows a clickable link to the record that summarized this cycle when the user has records.read', async () => {
    getPlotMock.mockResolvedValue(basePlot());
    listPlotCyclesMock.mockResolvedValue([
      oneCycle({
        id: 'cycle-1', cycleNo: 1, status: 'harvested', closedAt: '2026-09-01T00:00:00Z',
        finalInspectionRecordId: 'rec-42',
        harvestYield: 1250, finalYieldAfterClean: 1180, finalYieldUnit: 'kg',
        harvestDate: '2026-08-30',
      }),
    ]);

    renderPage();

    await screen.findByText('ประวัติรอบปลูก');
    const link = screen.getByRole('link', { name: /บันทึกที่ใช้สรุป/ });
    expect(link.getAttribute('href')).toBe('/farmlog/records/rec-42/preview');
  });

  it('never exposes the record id at all when the user lacks records.read', async () => {
    allowedPerms = new Set(['plots.read']); // no records.read
    getPlotMock.mockResolvedValue(basePlot());
    listPlotCyclesMock.mockResolvedValue([
      oneCycle({
        id: 'cycle-1', cycleNo: 1, status: 'harvested', closedAt: '2026-09-01T00:00:00Z',
        finalInspectionRecordId: 'rec-42',
        harvestYield: 1250, finalYieldAfterClean: 1180, finalYieldUnit: 'kg',
        harvestDate: '2026-08-30',
      }),
    ]);

    renderPage();

    await screen.findByText('ประวัติรอบปลูก');
    expect(screen.queryByRole('link', { name: /บันทึกที่ใช้สรุป/ })).toBeNull();
    // Round 8-10A tightened this: previously a truncated id was rendered. An
    // id is still a pointer at a record this caller may not read, so the cell
    // now only states that one exists.
    expect(screen.queryByText(/rec-42/)).toBeNull();
    expect(historyCell('รอบที่ 1', 'อ้างอิง')).toContain('บันทึกที่ใช้สรุป: มี');
  });
});

describe('PlotDetail — cycle rollover (round 7.9C)', () => {
  it('shows "จบรอบ + เริ่มรอบใหม่" when the plot is active, has plots.update, and has an active cycle', async () => {
    getPlotMock.mockResolvedValue(basePlot());
    listPlotCyclesMock.mockResolvedValue([oneCycle()]);

    renderPage();

    expect(await screen.findByRole('button', { name: 'จบรอบ + เริ่มรอบใหม่' })).toBeTruthy();
  });

  it('hides the rollover button when there is no active cycle', async () => {
    getPlotMock.mockResolvedValue(basePlot());
    listPlotCyclesMock.mockResolvedValue([]);

    renderPage();

    await screen.findAllByText('รอเริ่มรอบปลูก');
    expect(screen.queryByRole('button', { name: 'จบรอบ + เริ่มรอบใหม่' })).toBeNull();
  });

  it('hides the rollover button on a permanently-closed (inactive) plot even if an active cycle somehow remains', async () => {
    getPlotMock.mockResolvedValue(basePlot({ isActive: false }));
    listPlotCyclesMock.mockResolvedValue([oneCycle()]);

    renderPage();

    await waitFor(() => expect(screen.getAllByText('กำลังปลูก').length).toBeGreaterThanOrEqual(1));
    expect(screen.queryByRole('button', { name: 'จบรอบ + เริ่มรอบใหม่' })).toBeNull();
  });

  it('hides the rollover button without plots.update', async () => {
    allowedPerms = new Set(['plots.read', 'records.create']);
    getPlotMock.mockResolvedValue(basePlot());
    listPlotCyclesMock.mockResolvedValue([oneCycle()]);

    renderPage();

    await waitFor(() => expect(screen.getAllByText('กำลังปลูก').length).toBeGreaterThanOrEqual(1));
    expect(screen.queryByRole('button', { name: 'จบรอบ + เริ่มรอบใหม่' })).toBeNull();
  });

  it('clicking the button opens the modal showing the current cycle read-only', async () => {
    getPlotMock.mockResolvedValue(basePlot());
    listPlotCyclesMock.mockResolvedValue([oneCycle()]);

    renderPage();
    fireEvent.click(await screen.findByRole('button', { name: 'จบรอบ + เริ่มรอบใหม่' }));

    expect(await screen.findByText('จบรอบเดิม + เริ่มรอบใหม่ — รอบที่ 1')).toBeTruthy();
    // readonly current-cycle fields from oneCycle() — the underlying page
    // (CurrentCycleSection) still renders behind the modal overlay, so these
    // also appear there; assert at least one match rather than exactly one.
    expect(screen.getAllByText('LOT-01').length).toBeGreaterThanOrEqual(1);
    expect(screen.getAllByText('2026-06-01').length).toBeGreaterThanOrEqual(1);
    expect(screen.getAllByText('1,000 kg').length).toBeGreaterThanOrEqual(1);
  });

  it('shows copy confirming the QR key stays valid and history/records are preserved', async () => {
    getPlotMock.mockResolvedValue(basePlot());
    listPlotCyclesMock.mockResolvedValue([oneCycle()]);

    renderPage();
    fireEvent.click(await screen.findByRole('button', { name: 'จบรอบ + เริ่มรอบใหม่' }));

    expect(await screen.findByText('QR เดิมของแปลงยังใช้ต่อได้')).toBeTruthy();
    expect(screen.getByText('ระบบจะปิดรอบเดิมและเปิดรอบใหม่ในครั้งเดียว')).toBeTruthy();
    expect(screen.getByText('ประวัติรอบเดิมและบันทึกการตรวจเดิมจะไม่หาย')).toBeTruthy();
  });

  it('submitting calls ONLY rolloverPlotCycle — never closePlotCycle/createPlotCycle separately — with no record/photo/QR fields in the payload', async () => {
    getPlotMock.mockResolvedValue(basePlot());
    listPlotCyclesMock.mockResolvedValueOnce([oneCycle()]).mockResolvedValue([
      oneCycle({ id: 'cycle-2', cycleNo: 2 }),
    ]);
    rolloverPlotCycleMock.mockResolvedValue({
      plotId: 'plot-1', activeCycleId: 'cycle-2', activeCycleNo: 2,
      closedCycle: oneCycle({ status: 'harvested' }),
      newCycle: oneCycle({ id: 'cycle-2', cycleNo: 2 }),
    });

    renderPage();
    fireEvent.click(await screen.findByRole('button', { name: 'จบรอบ + เริ่มรอบใหม่' }));
    fireEvent.change(screen.getByPlaceholderText('เช่น PO25001'), { target: { value: 'PO25001' } });
    fireEvent.change(screen.getByPlaceholderText('เช่น Melon-A'), { target: { value: 'Melon-A' } });
    // Round 8-12B — Auto Lot needs a cycleLabel as well as a P.Code.
    fireEvent.change(screen.getByPlaceholderText('เช่น jun2026 หรือ may2026'), { target: { value: '2605' } });
    fireEvent.click(await screen.findByRole('button', { name: 'ยืนยันจบรอบ + เริ่มรอบใหม่' }));

    await waitFor(() => expect(rolloverPlotCycleMock).toHaveBeenCalledOnce());
    expect(rolloverPlotCycleMock).toHaveBeenCalledWith('plot-1', 'cycle-1', expect.any(Object));
    expect(closePlotCycleMock).not.toHaveBeenCalled();
    expect(createPlotCycleMock).not.toHaveBeenCalled();

    // the payload sent is exactly { closeStatus, closeReason, newCycle: {...} }
    // — no record/photo/qrKey field of any kind.
    const payload = rolloverPlotCycleMock.mock.calls[0][2] as Record<string, unknown>;
    expect(Object.keys(payload).sort()).toEqual(['closeReason', 'closeStatus', 'newCycle']);
    const newCycle = payload.newCycle as Record<string, unknown>;
    for (const forbidden of ['recordId', 'photoUrls', 'qrKey', 'inspectionSessionToken']) {
      expect(Object.prototype.hasOwnProperty.call(payload, forbidden)).toBe(false);
      expect(Object.prototype.hasOwnProperty.call(newCycle, forbidden)).toBe(false);
    }
  });

  it('invalidates plot, plot-cycles, plots, and the plot-status report after a successful rollover', async () => {
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const invalidateSpy = vi.spyOn(qc, 'invalidateQueries');
    getPlotMock.mockResolvedValue(basePlot());
    listPlotCyclesMock.mockResolvedValueOnce([oneCycle()]).mockResolvedValue([
      oneCycle({ id: 'cycle-2', cycleNo: 2 }),
    ]);
    rolloverPlotCycleMock.mockResolvedValue({
      plotId: 'plot-1', activeCycleId: 'cycle-2', activeCycleNo: 2,
      closedCycle: oneCycle({ status: 'harvested' }),
      newCycle: oneCycle({ id: 'cycle-2', cycleNo: 2 }),
    });

    renderPage(qc);
    fireEvent.click(await screen.findByRole('button', { name: 'จบรอบ + เริ่มรอบใหม่' }));
    fireEvent.change(screen.getByPlaceholderText('เช่น PO25001'), { target: { value: 'PO25001' } });
    fireEvent.change(screen.getByPlaceholderText('เช่น Melon-A'), { target: { value: 'Melon-A' } });
    // Round 8-12B — Auto Lot needs a cycleLabel as well as a P.Code.
    fireEvent.change(screen.getByPlaceholderText('เช่น jun2026 หรือ may2026'), { target: { value: '2605' } });
    fireEvent.click(await screen.findByRole('button', { name: 'ยืนยันจบรอบ + เริ่มรอบใหม่' }));

    await waitFor(() => expect(rolloverPlotCycleMock).toHaveBeenCalledOnce());
    const keys = invalidateSpy.mock.calls
      .map((c) => (c[0] as { queryKey?: unknown[] })?.queryKey)
      .filter(Boolean) as unknown[][];
    const firstKeys = keys.map((k) => k[0]);
    expect(firstKeys).toContain('plots');
    expect(firstKeys).toContain('report-plot-status');
    expect(keys.some((k) => k[0] === 'plot' && k[1] === 'plot-1')).toBe(true);
    expect(keys.some((k) => k[0] === 'plot-cycles' && k[1] === 'plot-1')).toBe(true);
  });

  it('shows an understandable message on a 409 (someone else changed the cycle) without crashing', async () => {
    getPlotMock.mockResolvedValue(basePlot());
    listPlotCyclesMock.mockResolvedValue([oneCycle()]);
    rolloverPlotCycleMock.mockRejectedValue({ isAxiosError: true, response: { status: 409 } });

    renderPage();
    fireEvent.click(await screen.findByRole('button', { name: 'จบรอบ + เริ่มรอบใหม่' }));
    fireEvent.change(screen.getByPlaceholderText('เช่น PO25001'), { target: { value: 'PO25001' } });
    fireEvent.change(screen.getByPlaceholderText('เช่น Melon-A'), { target: { value: 'Melon-A' } });
    // Round 8-12B — Auto Lot needs a cycleLabel as well as a P.Code.
    fireEvent.change(screen.getByPlaceholderText('เช่น jun2026 หรือ may2026'), { target: { value: '2605' } });
    fireEvent.click(await screen.findByRole('button', { name: 'ยืนยันจบรอบ + เริ่มรอบใหม่' }));

    expect(await screen.findByText(
      'ไม่สามารถจบรอบได้ อาจมีการเปลี่ยนแปลงรอบปลูกจากผู้ใช้อื่น กรุณารีเฟรชแล้วลองใหม่',
    )).toBeTruthy();
    // modal stays open on error (not silently dismissed)
    expect(screen.getByText('จบรอบเดิม + เริ่มรอบใหม่ — รอบที่ 1')).toBeTruthy();
  });
});

// --- round 8-3C: access-phone section + management modal --------------------

describe('PlotDetail — access phones (round 8-3C)', () => {
  it('shows the full formatted primary and additional numbers', async () => {
    getPlotMock.mockResolvedValue(basePlot({
      primaryPhone: '0845552162', additionalPhones: ['0812345678', '0891112222'],
    }));
    renderPage();

    await screen.findByText('เบอร์โทรสำหรับเข้าตรวจแปลง');
    expect(screen.getByText('084-555-2162')).toBeTruthy();
    expect(screen.getByText('081-234-5678')).toBeTruthy();
    expect(screen.getByText('089-111-2222')).toBeTruthy();
    expect(screen.getByText('เบอร์หลัก')).toBeTruthy();
    expect(screen.getAllByText('เบอร์เสริม').length).toBe(2);
  });

  it('shows the empty state when no phones are set', async () => {
    getPlotMock.mockResolvedValue(basePlot({ primaryPhone: null, additionalPhones: [] }));
    renderPage();

    expect(await screen.findByText('ยังไม่ได้ตั้งเบอร์สำหรับเข้าตรวจ')).toBeTruthy();
  });

  it('shows "จัดการเบอร์เข้าตรวจ" only with plots.update', async () => {
    allowedPerms = new Set(['plots.read']); // no plots.update
    getPlotMock.mockResolvedValue(basePlot({ primaryPhone: '0845552162', additionalPhones: [] }));
    renderPage();

    await screen.findByText('เบอร์โทรสำหรับเข้าตรวจแปลง');
    expect(screen.queryByRole('button', { name: 'จัดการเบอร์เข้าตรวจ' })).toBeNull();
    // still readable — the full number is shown even without update rights
    expect(screen.getByText('084-555-2162')).toBeTruthy();
  });

  it('opens PlotAccessPhoneModal and fetches its own data on click', async () => {
    getPlotMock.mockResolvedValue(basePlot({ primaryPhone: '0845552162', additionalPhones: [] }));
    getPlotAccessPhonesMock.mockResolvedValue({
      primaryPhone: '0845552162', additionalPhones: [], items: [],
    });
    renderPage();

    fireEvent.click(await screen.findByRole('button', { name: 'จัดการเบอร์เข้าตรวจ' }));
    expect(await screen.findByText('จัดการเบอร์เข้าตรวจแปลง SUP001-P001')).toBeTruthy();
    await waitFor(() => expect(getPlotAccessPhonesMock).toHaveBeenCalledWith('plot-1'));
  });

  it('never reintroduces the removed assign-users/deactivate row actions', async () => {
    getPlotMock.mockResolvedValue(basePlot({ primaryPhone: '0845552162', additionalPhones: [] }));
    renderPage();

    await screen.findByText('เบอร์โทรสำหรับเข้าตรวจแปลง');
    expect(screen.queryByText('มอบหมายผู้ใช้')).toBeNull();
    expect(screen.queryByRole('button', { name: /ปิดใช้งาน/ })).toBeNull();
  });
});

// Round 8-6I Part F — inactive-plot warning band + reactivate/reactivate-
// with-cycle actions on Plot Detail.
describe('PlotDetail — reactivation (round 8-6I)', () => {
  function inactivePlot(overrides: Partial<PlotDetailType> = {}) {
    return basePlot({ isActive: false, ...overrides });
  }

  function axiosError(status: number | undefined, data?: unknown) {
    return Object.assign(new Error('Request failed'), {
      isAxiosError: true,
      response: status === undefined ? undefined : { status, data },
    });
  }

  it('shows the inactive warning band and its explanation', async () => {
    getPlotMock.mockResolvedValue(inactivePlot());
    listPlotCyclesMock.mockResolvedValue([]);
    renderPage();

    expect(await screen.findByText('แปลงนี้ปิดใช้งานอยู่')).toBeTruthy();
    expect(screen.getByText(/QR และหมายเลขเข้าตรวจเดิมจะกลับมาใช้ได้/)).toBeTruthy();
  });

  it('does not show the warning band for an active plot', async () => {
    getPlotMock.mockResolvedValue(basePlot());
    renderPage();

    await screen.findByText('เบอร์โทรสำหรับเข้าตรวจแปลง');
    expect(screen.queryByText('แปลงนี้ปิดใช้งานอยู่')).toBeNull();
  });

  it('shows both reactivate buttons with plots.delete + plots.update', async () => {
    getPlotMock.mockResolvedValue(inactivePlot());
    listPlotCyclesMock.mockResolvedValue([]);
    allowedPerms = new Set(['plots.delete', 'plots.update', 'plots.read']);
    renderPage();

    expect(await screen.findByRole('button', { name: 'เปิดใช้งานและเริ่มรอบปลูกใหม่' })).toBeTruthy();
    expect(screen.getByRole('button', { name: 'เปิดใช้งานแปลงเท่านั้น' })).toBeTruthy();
  });

  it('shows only the secondary button with plots.delete but not plots.update', async () => {
    getPlotMock.mockResolvedValue(inactivePlot());
    listPlotCyclesMock.mockResolvedValue([]);
    allowedPerms = new Set(['plots.delete', 'plots.read']);
    renderPage();

    expect(await screen.findByRole('button', { name: 'เปิดใช้งานแปลงเท่านั้น' })).toBeTruthy();
    expect(screen.queryByRole('button', { name: 'เปิดใช้งานและเริ่มรอบปลูกใหม่' })).toBeNull();
  });

  it('shows neither reactivate button without plots.delete', async () => {
    getPlotMock.mockResolvedValue(inactivePlot());
    listPlotCyclesMock.mockResolvedValue([]);
    allowedPerms = new Set(['plots.update', 'plots.read']);
    renderPage();

    await screen.findByText('แปลงนี้ปิดใช้งานอยู่');
    expect(screen.queryByRole('button', { name: 'เปิดใช้งานแปลงเท่านั้น' })).toBeNull();
    expect(screen.queryByRole('button', { name: 'เปิดใช้งานและเริ่มรอบปลูกใหม่' })).toBeNull();
  });

  it('never shows either reactivate button for an active plot even with full permissions', async () => {
    getPlotMock.mockResolvedValue(basePlot());
    allowedPerms = new Set(['plots.delete', 'plots.update', 'plots.read', 'records.create']);
    renderPage();

    await screen.findByText('เบอร์โทรสำหรับเข้าตรวจแปลง');
    expect(screen.queryByRole('button', { name: 'เปิดใช้งานแปลงเท่านั้น' })).toBeNull();
    expect(screen.queryByRole('button', { name: 'เปิดใช้งานและเริ่มรอบปลูกใหม่' })).toBeNull();
  });

  it('hides ตรวจแปลง for an inactive plot even with records.create', async () => {
    getPlotMock.mockResolvedValue(inactivePlot());
    listPlotCyclesMock.mockResolvedValue([]);
    allowedPerms = new Set(['records.create', 'plots.read']);
    renderPage();

    await screen.findByText('แปลงนี้ปิดใช้งานอยู่');
    expect(screen.queryByRole('link', { name: /ตรวจแปลง/ })).toBeNull();
    expect(screen.queryByRole('button', { name: /ตรวจแปลง/ })).toBeNull();
  });

  it('never shows "เริ่มรอบปลูกใหม่" for an inactive plot — StartCycleModal must not be reachable', async () => {
    getPlotMock.mockResolvedValue(inactivePlot());
    listPlotCyclesMock.mockResolvedValue([]);
    allowedPerms = new Set(['plots.update', 'plots.delete', 'plots.read']);
    renderPage();

    await screen.findByText('แปลงนี้ปิดใช้งานอยู่');
    expect(screen.queryByRole('button', { name: 'เริ่มรอบปลูกใหม่' })).toBeNull();
  });

  it('reactivate-only calls reactivatePlot exactly once, shows success, and invalidates every required key', async () => {
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const invalidateSpy = vi.spyOn(qc, 'invalidateQueries');
    getPlotMock.mockResolvedValue(inactivePlot());
    listPlotCyclesMock.mockResolvedValue([]);
    reactivatePlotMock.mockResolvedValue({ ...inactivePlot(), isActive: true });
    allowedPerms = new Set(['plots.delete', 'plots.read']);
    renderPage(qc);

    fireEvent.click(await screen.findByRole('button', { name: 'เปิดใช้งานแปลงเท่านั้น' }));
    fireEvent.click(await screen.findByRole('button', { name: 'ยืนยันเปิดใช้งานแปลง' }));

    await waitFor(() => expect(reactivatePlotMock).toHaveBeenCalledTimes(1));
    expect(reactivatePlotWithCycleMock).not.toHaveBeenCalled();
    expect(await screen.findByText('เปิดใช้งานแปลงแล้ว')).toBeTruthy();

    const invalidatedKeys = invalidateSpy.mock.calls.map(
      (c) => (c[0] as { queryKey: unknown[] }).queryKey[0],
    );
    for (const key of ['plot', 'plot-cycles', 'plots', 'report-plot-status', 'report-cycle-yield', 'plot-provinces']) {
      expect(invalidatedKeys).toContain(key);
    }
  });

  it('reactivate-with-cycle calls reactivatePlotWithCycle exactly once with the cycle payload, and shows success', async () => {
    getPlotMock.mockResolvedValue(inactivePlot());
    listPlotCyclesMock.mockResolvedValue([]);
    reactivatePlotWithCycleMock.mockResolvedValue({
      plot: { ...inactivePlot(), isActive: true },
      cycle: oneCycle(),
    });
    allowedPerms = new Set(['plots.delete', 'plots.update', 'plots.read']);
    renderPage();

    fireEvent.click(await screen.findByRole('button', { name: 'เปิดใช้งานและเริ่มรอบปลูกใหม่' }));
    await screen.findByPlaceholderText('เช่น PO25001');

    fireEvent.change(screen.getByPlaceholderText('เช่น PO25001'), { target: { value: 'PO25009' } });
    fireEvent.change(screen.getByPlaceholderText('เช่น Melon-A'), { target: { value: 'Melon-Z' } });
    // Round 8-12B — Auto Lot needs a cycleLabel as well as a P.Code.
    fireEvent.change(screen.getByPlaceholderText('เช่น jun2026 หรือ may2026'), { target: { value: '2605' } });
    fireEvent.click(screen.getByRole('button', { name: 'เปิดใช้งานและเริ่มรอบปลูก' }));

    await waitFor(() => expect(reactivatePlotWithCycleMock).toHaveBeenCalledTimes(1));
    expect(reactivatePlotMock).not.toHaveBeenCalled();
    const [calledPlotId, payload] = reactivatePlotWithCycleMock.mock.calls[0] as [string, { poNumber: string; pCode: string }];
    expect(calledPlotId).toBe('plot-1');
    expect(payload.poNumber).toBe('PO25009');
    expect(payload.pCode).toBe('Melon-Z');
    expect(await screen.findByText('เปิดใช้งานแปลงและเริ่มรอบปลูกใหม่แล้ว')).toBeTruthy();
  });

  it('404 shows the mapped Thai message and the modal stays open', async () => {
    getPlotMock.mockResolvedValue(inactivePlot());
    listPlotCyclesMock.mockResolvedValue([]);
    reactivatePlotMock.mockRejectedValue(axiosError(404, {}));
    allowedPerms = new Set(['plots.delete', 'plots.read']);
    renderPage();

    fireEvent.click(await screen.findByRole('button', { name: 'เปิดใช้งานแปลงเท่านั้น' }));
    fireEvent.click(await screen.findByRole('button', { name: 'ยืนยันเปิดใช้งานแปลง' }));

    expect(await screen.findByText('ไม่พบแปลง หรือคุณไม่มีสิทธิ์เข้าถึง')).toBeTruthy();
    expect(screen.getByRole('button', { name: 'ยืนยันเปิดใช้งานแปลง' })).toBeTruthy();
  });

  it('409 already-active shows the specific refresh message', async () => {
    getPlotMock.mockResolvedValue(inactivePlot());
    listPlotCyclesMock.mockResolvedValue([]);
    reactivatePlotMock.mockRejectedValue(axiosError(409, { detail: 'แปลงนี้เปิดใช้งานอยู่แล้ว' }));
    allowedPerms = new Set(['plots.delete', 'plots.read']);
    renderPage();

    fireEvent.click(await screen.findByRole('button', { name: 'เปิดใช้งานแปลงเท่านั้น' }));
    fireEvent.click(await screen.findByRole('button', { name: 'ยืนยันเปิดใช้งานแปลง' }));

    expect(await screen.findByText('แปลงนี้เปิดใช้งานอยู่แล้ว กรุณารีเฟรชข้อมูล')).toBeTruthy();
  });

  it('409 inconsistent-state shows the backend message verbatim', async () => {
    getPlotMock.mockResolvedValue(inactivePlot());
    listPlotCyclesMock.mockResolvedValue([]);
    const backendDetail = 'พบข้อมูลไม่สอดคล้องกัน (แปลงปิดใช้งานแต่มีรอบปลูกที่เปิดอยู่) กรุณาติดต่อผู้ดูแลระบบ';
    reactivatePlotMock.mockRejectedValue(axiosError(409, { detail: backendDetail }));
    allowedPerms = new Set(['plots.delete', 'plots.read']);
    renderPage();

    fireEvent.click(await screen.findByRole('button', { name: 'เปิดใช้งานแปลงเท่านั้น' }));
    fireEvent.click(await screen.findByRole('button', { name: 'ยืนยันเปิดใช้งานแปลง' }));

    expect(await screen.findByText(backendDetail)).toBeTruthy();
  });

  it('a 422 shows the backend validation detail', async () => {
    getPlotMock.mockResolvedValue(inactivePlot());
    listPlotCyclesMock.mockResolvedValue([]);
    reactivatePlotMock.mockRejectedValue(axiosError(422, { detail: 'ข้อมูลไม่ถูกต้อง' }));
    allowedPerms = new Set(['plots.delete', 'plots.read']);
    renderPage();

    fireEvent.click(await screen.findByRole('button', { name: 'เปิดใช้งานแปลงเท่านั้น' }));
    fireEvent.click(await screen.findByRole('button', { name: 'ยืนยันเปิดใช้งานแปลง' }));

    expect(await screen.findByText(/ข้อมูลไม่ถูกต้อง/)).toBeTruthy();
  });

  it('a network error (no response) shows the connection-failure message', async () => {
    getPlotMock.mockResolvedValue(inactivePlot());
    listPlotCyclesMock.mockResolvedValue([]);
    reactivatePlotMock.mockRejectedValue(axiosError(undefined));
    allowedPerms = new Set(['plots.delete', 'plots.read']);
    renderPage();

    fireEvent.click(await screen.findByRole('button', { name: 'เปิดใช้งานแปลงเท่านั้น' }));
    fireEvent.click(await screen.findByRole('button', { name: 'ยืนยันเปิดใช้งานแปลง' }));

    expect(await screen.findByText('เชื่อมต่อระบบไม่สำเร็จ กรุณาลองใหม่อีกครั้ง')).toBeTruthy();
  });

  it('closing the reactivate modal never calls either endpoint', async () => {
    getPlotMock.mockResolvedValue(inactivePlot());
    listPlotCyclesMock.mockResolvedValue([]);
    allowedPerms = new Set(['plots.delete', 'plots.update', 'plots.read']);
    renderPage();

    fireEvent.click(await screen.findByRole('button', { name: 'เปิดใช้งานแปลงเท่านั้น' }));
    fireEvent.click(screen.getByRole('button', { name: 'ยกเลิก' }));
    await waitFor(() => expect(screen.queryByRole('button', { name: 'ยืนยันเปิดใช้งานแปลง' })).toBeNull());

    expect(reactivatePlotMock).not.toHaveBeenCalled();
    expect(reactivatePlotWithCycleMock).not.toHaveBeenCalled();
  });

  it('regression: active-plot workflows (ตรวจแปลง, เริ่มรอบปลูกใหม่, แก้ไขแปลง) are unaffected', async () => {
    getPlotMock.mockResolvedValue(basePlot());
    listPlotCyclesMock.mockResolvedValue([]);
    allowedPerms = new Set(['plots.update', 'plots.delete', 'plots.read', 'records.create']);
    renderPage();

    expect(await screen.findByRole('link', { name: 'แก้ไขแปลง' })).toBeTruthy();
    // Both the current-cycle section and the yield-planning hero card show
    // their own "เริ่มรอบปลูกใหม่" button when there's no active cycle
    // (pre-existing behavior, unchanged by this round).
    expect((await screen.findAllByRole('button', { name: 'เริ่มรอบปลูกใหม่' })).length).toBeGreaterThanOrEqual(1);
    expect(screen.queryByRole('button', { name: 'เปิดใช้งานแปลงเท่านั้น' })).toBeNull();
    expect(screen.queryByRole('button', { name: 'เปิดใช้งานและเริ่มรอบปลูกใหม่' })).toBeNull();
  });
});

// --- round 8-8C: kg-first Yield display in Current Status + history --------

describe('PlotDetail — Current Status Yield kg (round 8-8C)', () => {
  it('shows the latest record\'s own kg quantity + percent, not a recompute from the active cycle', async () => {
    listPlotCyclesMock.mockResolvedValue([]);
    getPlotMock.mockResolvedValue(basePlot({
      currentYieldPct: '80.0', lastInspectionRecordId: 'rec-1',
    }));
    getRecordMock.mockResolvedValue({
      id: 'rec-1', photoUrls: [],
      yieldPct: '80.0', yieldQuantityKg: '800.00', yieldTargetKgSnapshot: '1000.00',
    });

    renderPage();

    expect(await screen.findByText('ผลผลิตประเมินล่าสุด')).toBeTruthy();
    expect(screen.getByText('800 kg')).toBeTruthy();
    expect(await screen.findByText('Yield % (เทียบเป้าหมาย)')).toBeTruthy();
    expect(screen.getByText('80%')).toBeTruthy();
    expect(screen.queryByText('Yield ล่าสุด')).toBeNull();
  });

  it('falls back to the plot\'s synced currentYieldPct when the latest record has not loaded (no permission / still fetching)', async () => {
    listPlotCyclesMock.mockResolvedValue([]);
    getPlotMock.mockResolvedValue(basePlot({
      currentYieldPct: '95.5', lastInspectionRecordId: 'rec-1',
    }));
    // getRecord never resolves (simulates records.read denied / pending) —
    // the plot-level snapshot must still render.
    getRecordMock.mockReturnValue(new Promise(() => {}));

    renderPage();

    expect(await screen.findByText('Yield ล่าสุด')).toBeTruthy();
    expect(screen.getByText('95.5%')).toBeTruthy();
  });

  it('a legacy latest record (percent only, no kg) keeps the original "Yield ล่าสุด" label', async () => {
    listPlotCyclesMock.mockResolvedValue([]);
    getPlotMock.mockResolvedValue(basePlot({
      currentYieldPct: '80.0', lastInspectionRecordId: 'rec-1',
    }));
    getRecordMock.mockResolvedValue({ id: 'rec-1', photoUrls: [] });

    renderPage();

    expect(await screen.findByText('Yield ล่าสุด')).toBeTruthy();
    expect(screen.queryByText('ผลผลิตประเมินล่าสุด')).toBeNull();
  });
});

describe('PlotDetail — history Yield kg (round 8-8C)', () => {
  it('collapsed history row shows kg as primary with percent in parentheses', async () => {
    getPlotMock.mockResolvedValue(basePlot());
    listRecordsMock.mockResolvedValue([
      baseRecordSummary({
        id: 'rec-1', yieldPct: '160.0', yieldQuantityKg: '1600.00', yieldTargetKgSnapshot: '1000.00',
      }),
    ]);

    renderPage();

    expect(await screen.findByText('1,600 kg')).toBeTruthy();
    expect(screen.getByText('(160%)')).toBeTruthy();
  });

  it('collapsed history row falls back to percent-only for a legacy record (no kg)', async () => {
    getPlotMock.mockResolvedValue(basePlot());
    listRecordsMock.mockResolvedValue([
      baseRecordSummary({ id: 'rec-1', yieldPct: '95.5', yieldQuantityKg: null, yieldTargetKgSnapshot: null }),
    ]);

    renderPage();

    expect(await screen.findByText('95.5%')).toBeTruthy();
    expect(screen.queryByText(/^\(.*%\)$/)).toBeNull();
  });

  it('expanded history row shows this record\'s OWN frozen quantity/target/percent, never the active cycle\'s', async () => {
    getPlotMock.mockResolvedValue(basePlot());
    listRecordsMock.mockResolvedValue([
      baseRecordSummary({ id: 'rec-1', yieldPct: '160.0', yieldQuantityKg: '1600.00', yieldTargetKgSnapshot: '1000.00' }),
    ]);
    getRecordMock.mockResolvedValue({
      id: 'rec-1', photoUrls: [], recommendation: null, notes: null,
      fieldPrepScore: null, weatherScore: null, careScore: null, varietyResistanceScore: null,
      customFields: {},
      yieldPct: '160.0', yieldQuantityKg: '1600.00', yieldTargetKgSnapshot: '1000.00',
    });

    renderPage();
    await screen.findByText('คะแนน 4 ด้าน:');
    fireEvent.click(screen.getByText('2026-07-01'));

    // "1,600 kg" / "160%" legitimately appear twice once expanded — once in
    // the collapsed glance (still visible) and once in the expanded detail
    // block — same convention as this file's other "renders twice" assertions.
    expect(await screen.findByText('ผลผลิตที่ประเมินได้:')).toBeTruthy();
    expect((await screen.findAllByText('1,600 kg')).length).toBeGreaterThanOrEqual(1);
    expect(screen.getByText('เป้าหมายที่ใช้คำนวณ:')).toBeTruthy();
    // "1,000 kg" also legitimately matches the active cycle's own Expected
    // Yield field elsewhere on the page (default fixture) — just prove ours
    // is among them, not that it's the only occurrence.
    expect((await screen.findAllByText('1,000 kg')).length).toBeGreaterThanOrEqual(1);
    expect(screen.getByText('เปอร์เซ็นต์เทียบเป้าหมาย:')).toBeTruthy();
    expect((await screen.findAllByText('160%')).length).toBeGreaterThanOrEqual(1);
  });

  it('a real >150% history value is never clamped anywhere in collapsed or expanded view', async () => {
    getPlotMock.mockResolvedValue(basePlot());
    listRecordsMock.mockResolvedValue([
      baseRecordSummary({ id: 'rec-1', yieldPct: '510.0', yieldQuantityKg: '5100.00', yieldTargetKgSnapshot: '1000.00' }),
    ]);
    getRecordMock.mockResolvedValue({
      id: 'rec-1', photoUrls: [], recommendation: null, notes: null,
      fieldPrepScore: null, weatherScore: null, careScore: null, varietyResistanceScore: null,
      customFields: {},
      yieldPct: '510.0', yieldQuantityKg: '5100.00', yieldTargetKgSnapshot: '1000.00',
    });

    renderPage();
    expect(await screen.findByText('(510%)')).toBeTruthy();

    fireEvent.click(screen.getByText('2026-07-01'));
    expect(await screen.findByText('510%')).toBeTruthy();
  });
});

// --- round 8-9B: "รหัสยืนยันแปลง" status section ----------------------------

describe('PlotDetail — inspection password section (round 8-9B)', () => {
  it('renders the section heading next to the access-phone section', async () => {
    getPlotMock.mockResolvedValue(basePlot());

    renderPage();

    expect(await screen.findByText('รหัสยืนยันแปลง')).toBeTruthy();
    expect(screen.getByText('เบอร์โทรสำหรับเข้าตรวจแปลง')).toBeTruthy();
  });

  it('shows a stable loading placeholder and never flashes "ยังไม่ตั้งรหัส" first', async () => {
    getPlotMock.mockResolvedValue(basePlot());
    getPlotInspectionCredentialMock.mockReturnValue(new Promise(() => {})); // never resolves

    renderPage();

    expect(await screen.findByText('กำลังโหลดสถานะรหัสยืนยันแปลง…')).toBeTruthy();
    expect(screen.queryByText('ยังไม่ตั้งรหัส')).toBeNull();
    expect(screen.queryByText('ตั้งรหัสแล้ว')).toBeNull();
    // no action button until we know which action it is
    expect(screen.queryByRole('button', { name: /ตั้งรหัสยืนยันแปลง|เปลี่ยนรหัสยืนยันแปลง/ })).toBeNull();
  });

  it('shows "ยังไม่ตั้งรหัส" with the rollout hint when configured=false', async () => {
    getPlotMock.mockResolvedValue(basePlot());

    renderPage();

    expect(await screen.findByText('ยังไม่ตั้งรหัส')).toBeTruthy();
    expect(screen.getByText('ต้องตั้งรหัสก่อนเปิดใช้การค้นหาแปลงด้วยหมายเลขและรหัส')).toBeTruthy();
    expect(screen.getByRole('button', { name: 'ตั้งรหัสยืนยันแปลง' })).toBeTruthy();
  });

  it('shows "ตั้งรหัสแล้ว" plus the last-updated time when configured=true', async () => {
    getPlotMock.mockResolvedValue(basePlot());
    getPlotInspectionCredentialMock.mockResolvedValue({
      configured: true, credentialVersion: 2, updatedAt: '2026-08-01T10:00:00Z',
    });

    renderPage();

    expect(await screen.findByText('ตั้งรหัสแล้ว')).toBeTruthy();
    expect(screen.getByText(/แก้ไขล่าสุด/)).toBeTruthy();
    expect(screen.getByRole('button', { name: 'เปลี่ยนรหัสยืนยันแปลง' })).toBeTruthy();
    expect(screen.queryByText('ยังไม่ตั้งรหัส')).toBeNull();
  });

  it('omits the last-updated line when updatedAt is null', async () => {
    getPlotMock.mockResolvedValue(basePlot());
    getPlotInspectionCredentialMock.mockResolvedValue({
      configured: true, credentialVersion: 1, updatedAt: null,
    });

    renderPage();

    expect(await screen.findByText('ตั้งรหัสแล้ว')).toBeTruthy();
    expect(screen.queryByText(/แก้ไขล่าสุด/)).toBeNull();
  });

  it('never renders the credential version as page text', async () => {
    getPlotMock.mockResolvedValue(basePlot());
    getPlotInspectionCredentialMock.mockResolvedValue({
      configured: true, credentialVersion: 7, updatedAt: '2026-08-01T10:00:00Z',
    });

    renderPage();

    await screen.findByText('ตั้งรหัสแล้ว');
    const text = document.body.textContent ?? '';
    expect(text).not.toContain('เวอร์ชัน');
    expect(text.toLowerCase()).not.toContain('credentialversion');
  });

  it('degrades to an inline error + retry without breaking the rest of the page', async () => {
    getPlotMock.mockResolvedValue(basePlot());
    getPlotInspectionCredentialMock.mockRejectedValue(new Error('boom'));

    renderPage();

    expect(await screen.findByText('โหลดสถานะรหัสยืนยันแปลงไม่สำเร็จ')).toBeTruthy();
    // the rest of Plot Detail still rendered
    expect(screen.getByRole('link', { name: 'แก้ไขแปลง' })).toBeTruthy();
    expect(screen.getByText('เบอร์โทรสำหรับเข้าตรวจแปลง')).toBeTruthy();
    // and no misleading status is shown
    expect(screen.queryByText('ยังไม่ตั้งรหัส')).toBeNull();
  });

  it('refetches when the retry button is pressed', async () => {
    getPlotMock.mockResolvedValue(basePlot());
    getPlotInspectionCredentialMock.mockRejectedValueOnce(new Error('boom'));

    renderPage();

    await screen.findByText('โหลดสถานะรหัสยืนยันแปลงไม่สำเร็จ');
    getPlotInspectionCredentialMock.mockResolvedValue({
      configured: true, credentialVersion: 1, updatedAt: null,
    });
    fireEvent.click(screen.getByRole('button', { name: /ลองใหม่/ }));

    expect(await screen.findByText('ตั้งรหัสแล้ว')).toBeTruthy();
  });

  it('hides the set/change button without plots.update but still shows the status', async () => {
    allowedPerms = new Set(['plots.read', 'records.read']);
    getPlotMock.mockResolvedValue(basePlot());

    renderPage();

    expect(await screen.findByText('ยังไม่ตั้งรหัส')).toBeTruthy();
    expect(screen.queryByRole('button', { name: 'ตั้งรหัสยืนยันแปลง' })).toBeNull();
  });

  it('shows the set/change button with plots.update', async () => {
    allowedPerms = new Set(['plots.read', 'plots.update']);
    getPlotMock.mockResolvedValue(basePlot());

    renderPage();

    expect(await screen.findByRole('button', { name: 'ตั้งรหัสยืนยันแปลง' })).toBeTruthy();
  });

  it('opens the modal with the plot identity and the correct mode', async () => {
    getPlotMock.mockResolvedValue(basePlot());
    getPlotInspectionCredentialMock.mockResolvedValue({
      configured: true, credentialVersion: 1, updatedAt: null,
    });

    renderPage();

    fireEvent.click(await screen.findByRole('button', { name: 'เปลี่ยนรหัสยืนยันแปลง' }));

    // modal title reflects the configured state, not just the button label
    await waitFor(() => expect(screen.getByRole('heading', { name: 'เปลี่ยนรหัสยืนยันแปลง' })).toBeTruthy());
    expect(screen.getByLabelText('รหัสยืนยันแปลง')).toBeTruthy();
    expect(screen.getByLabelText('ยืนยันรหัสอีกครั้ง')).toBeTruthy();
    // Supplier / plot identity carried into the modal
    expect(screen.getAllByText('SUP001').length).toBeGreaterThanOrEqual(1);
    expect(screen.getAllByText('SUP001-P001').length).toBeGreaterThanOrEqual(1);
  });

  it('opens the modal in "set" mode when no password exists yet', async () => {
    getPlotMock.mockResolvedValue(basePlot());

    renderPage();

    fireEvent.click(await screen.findByRole('button', { name: 'ตั้งรหัสยืนยันแปลง' }));

    await waitFor(() => expect(screen.getByRole('heading', { name: 'ตั้งรหัสยืนยันแปลง' })).toBeTruthy());
    // no "existing inspectors lose access" warning on a first set
    expect(screen.queryByText(/ผู้ตรวจที่ใช้รหัสเดิม/)).toBeNull();
  });

  it('closes the modal without ever calling the set API when cancelled', async () => {
    getPlotMock.mockResolvedValue(basePlot());

    renderPage();

    fireEvent.click(await screen.findByRole('button', { name: 'ตั้งรหัสยืนยันแปลง' }));
    await waitFor(() => expect(screen.getByRole('heading', { name: 'ตั้งรหัสยืนยันแปลง' })).toBeTruthy());
    fireEvent.click(screen.getByRole('button', { name: 'ยกเลิก' }));

    await waitFor(() => expect(screen.queryByLabelText('ยืนยันรหัสอีกครั้ง')).toBeNull());
    expect(setPlotInspectionCredentialMock).not.toHaveBeenCalled();
  });
});

// --- Round 8-10A: cycle history table (latest 10, up to 100) ---------------

/** N cycles, newest cycleNo first — the order the backend already returns. */
function manyCycles(count: number) {
  return Array.from({ length: count }, (_, i) => {
    const cycleNo = count - i;
    return oneCycle({
      id: `cycle-${cycleNo}`,
      cycleNo,
      cycleLabel: null,
      status: cycleNo === count ? 'active' : 'harvested',
      closedAt: cycleNo === count ? null : '2026-05-01T00:00:00Z',
    });
  });
}

describe('PlotDetail — Supplier Lot No (round 8-12B)', () => {
  it('shows the current cycle System Lot and Supplier Lot as separate fields', async () => {
    getPlotMock.mockResolvedValue(basePlot());
    listPlotCyclesMock.mockResolvedValue([
      oneCycle({ lotNo: '2605-SUP010-WM-141-001', lotNoSource: 'auto', supplierLotNo: 'SUP-OWN-7' }),
    ]);

    renderPage();

    expect((await screen.findAllByText('Lot No ระบบ')).length).toBeGreaterThan(0);
    expect(screen.getAllByText('Supplier Lot No').length).toBeGreaterThan(0);
    expect(screen.getAllByText('2605-SUP010-WM-141-001').length).toBeGreaterThan(0);
    expect(screen.getAllByText('SUP-OWN-7').length).toBeGreaterThan(0);
  });

  it('shows an em dash when the current cycle has no supplier lot', async () => {
    getPlotMock.mockResolvedValue(basePlot());
    listPlotCyclesMock.mockResolvedValue([
      oneCycle({ lotNo: '2605-SUP010-WM-141-001', lotNoSource: 'auto', supplierLotNo: null }),
    ]);

    renderPage();

    // the system lot renders (current cycle + its history row), and the
    // supplier lot field is present but empty
    expect((await screen.findAllByText('2605-SUP010-WM-141-001')).length).toBeGreaterThan(0);
    expect(screen.getAllByText('Supplier Lot No').length).toBeGreaterThan(0);
    expect(historyCell('รอบที่ 1', 'Supplier Lot No')).toBe('—');
  });

  it('every history row shows its OWN supplier lot, never the active cycle value', async () => {
    getPlotMock.mockResolvedValue(basePlot());
    listPlotCyclesMock.mockResolvedValue([
      oneCycle({ id: 'c2', cycleNo: 2, status: 'active', supplierLotNo: 'ACTIVE-LOT' }),
      oneCycle({ id: 'c1', cycleNo: 1, status: 'harvested', supplierLotNo: 'OLD-LOT' }),
    ]);

    renderPage();
    await screen.findByText('ประวัติรอบปลูก');

    expect(historyCell('รอบที่ 2', 'Supplier Lot No')).toBe('ACTIVE-LOT');
    expect(historyCell('รอบที่ 1', 'Supplier Lot No')).toBe('OLD-LOT');
  });

  it('a history row with no supplier lot shows an em dash', async () => {
    getPlotMock.mockResolvedValue(basePlot());
    listPlotCyclesMock.mockResolvedValue([
      oneCycle({ id: 'c1', cycleNo: 1, status: 'harvested', supplierLotNo: null }),
    ]);

    renderPage();
    await screen.findByText('ประวัติรอบปลูก');

    expect(historyCell('รอบที่ 1', 'Supplier Lot No')).toBe('—');
  });
});

describe('PlotDetail — Oracle reference fields (round 8-21B)', () => {
  it('shows all three current-cycle values as separate fields', async () => {
    getPlotMock.mockResolvedValue(basePlot());
    listPlotCyclesMock.mockResolvedValue([
      oneCycle({ oracleSupplierCode: 'ORC-SUP-7', oracleInvoice: 'INV-7', refAccount: 'ACC-7' }),
    ]);

    renderPage();

    expect((await screen.findAllByText('Oracle Supplier Code')).length).toBeGreaterThan(0);
    expect(screen.getAllByText('Oracle Invoice').length).toBeGreaterThan(0);
    expect(screen.getAllByText('Ref Account').length).toBeGreaterThan(0);
    expect(screen.getAllByText('ORC-SUP-7').length).toBeGreaterThan(0);
    expect(screen.getAllByText('INV-7').length).toBeGreaterThan(0);
    expect(screen.getAllByText('ACC-7').length).toBeGreaterThan(0);
  });

  it('shows an em dash when the current cycle has none of the three', async () => {
    getPlotMock.mockResolvedValue(basePlot());
    listPlotCyclesMock.mockResolvedValue([
      oneCycle({ oracleSupplierCode: null, oracleInvoice: null, refAccount: null }),
    ]);

    renderPage();
    await screen.findByText('ประวัติรอบปลูก');

    expect(historyCell('รอบที่ 1', 'Oracle Supplier Code')).toBe('—');
    expect(historyCell('รอบที่ 1', 'Oracle Invoice')).toBe('—');
    expect(historyCell('รอบที่ 1', 'Ref Account')).toBe('—');
  });

  it('every history row shows its OWN values, never the active cycle\'s', async () => {
    getPlotMock.mockResolvedValue(basePlot());
    listPlotCyclesMock.mockResolvedValue([
      oneCycle({ id: 'c2', cycleNo: 2, status: 'active', oracleSupplierCode: 'ACTIVE-ORC' }),
      oneCycle({ id: 'c1', cycleNo: 1, status: 'harvested', oracleSupplierCode: 'OLD-ORC' }),
    ]);

    renderPage();
    await screen.findByText('ประวัติรอบปลูก');

    expect(historyCell('รอบที่ 2', 'Oracle Supplier Code')).toBe('ACTIVE-ORC');
    expect(historyCell('รอบที่ 1', 'Oracle Supplier Code')).toBe('OLD-ORC');
  });

  it('is never shown as a Plot-level/permanent field — only inside a cycle context', async () => {
    getPlotMock.mockResolvedValue(basePlot());
    listPlotCyclesMock.mockResolvedValue([oneCycle({ oracleSupplierCode: 'ORC-SUP-7' })]);

    renderPage();
    await screen.findAllByText('ORC-SUP-7');

    // The plot header/permanent-info area never repeats the label a third
    // time beyond the current-cycle field + its own history row.
    expect(screen.getAllByText('Oracle Supplier Code').length).toBe(2);
  });
});

describe('PlotDetail — cycle history table (round 8-10A)', () => {
  it('fetches the newest 100 cycles once, with limit/offset on the request', async () => {
    getPlotMock.mockResolvedValue(basePlot());
    listPlotCyclesMock.mockResolvedValue(manyCycles(3));

    renderPage();

    await screen.findByText('ประวัติรอบปลูก');
    expect(listPlotCyclesMock).toHaveBeenCalledWith('plot-1', { limit: 100, offset: 0 });
  });

  it('renders a semantic table, not divs pretending to be one', async () => {
    getPlotMock.mockResolvedValue(basePlot());
    listPlotCyclesMock.mockResolvedValue(manyCycles(3));

    renderPage();

    await screen.findByText('ประวัติรอบปลูก');
    const table = historyTable();
    expect(table.tagName).toBe('TABLE');
    expect(table.querySelector('thead')).toBeTruthy();
    expect(table.querySelector('tbody')).toBeTruthy();
    // Every header is a <th scope="col"> — what a screen reader needs to
    // announce which column a cell belongs to.
    const headers = Array.from(table.querySelectorAll('thead th'));
    expect(headers.length).toBeGreaterThanOrEqual(14);
    for (const th of headers) expect(th.getAttribute('scope')).toBe('col');
  });

  it('shows the 10 newest cycles by default and hides the rest', async () => {
    getPlotMock.mockResolvedValue(basePlot());
    listPlotCyclesMock.mockResolvedValue(manyCycles(15));

    renderPage();

    await screen.findByText('ประวัติรอบปลูก');
    expect(historyRows()).toHaveLength(10);
    // newest kept...
    expect(historyRow('รอบที่ 15')).toBeTruthy();
    expect(historyRow('รอบที่ 6')).toBeTruthy();
    // ...oldest five dropped
    for (const n of [5, 4, 3, 2, 1]) {
      expect(historyRows().some((r) => r.cells[0].textContent?.trim() === `รอบที่ ${n}`)).toBe(false);
    }
  });

  it('orders rows newest cycle first', async () => {
    getPlotMock.mockResolvedValue(basePlot());
    listPlotCyclesMock.mockResolvedValue(manyCycles(4));

    renderPage();

    await screen.findByText('ประวัติรอบปลูก');
    expect(historyRows().map((r) => r.cells[0].textContent?.trim())).toEqual([
      'รอบที่ 4', 'รอบที่ 3', 'รอบที่ 2', 'รอบที่ 1',
    ]);
  });

  it('offers 10 / 25 / 50 / 100 and widens the table when one is picked', async () => {
    getPlotMock.mockResolvedValue(basePlot());
    listPlotCyclesMock.mockResolvedValue(manyCycles(15));

    renderPage();

    await screen.findByText('ประวัติรอบปลูก');
    const select = screen.getByLabelText('จำนวนรอบที่แสดง') as HTMLSelectElement;
    expect(Array.from(select.options).map((o) => o.value)).toEqual(['10', '25', '50', '100']);

    fireEvent.change(select, { target: { value: '25' } });

    await waitFor(() => expect(historyRows()).toHaveLength(15));
  });

  it('can show all 100 fetched cycles', async () => {
    getPlotMock.mockResolvedValue(basePlot());
    listPlotCyclesMock.mockResolvedValue(manyCycles(100));

    renderPage();

    await screen.findByText('ประวัติรอบปลูก');
    fireEvent.change(screen.getByLabelText('จำนวนรอบที่แสดง'), { target: { value: '100' } });

    await waitFor(() => expect(historyRows()).toHaveLength(100));
  });

  it('changing the selector never refetches', async () => {
    getPlotMock.mockResolvedValue(basePlot());
    listPlotCyclesMock.mockResolvedValue(manyCycles(30));

    renderPage();

    await screen.findByText('ประวัติรอบปลูก');
    expect(listPlotCyclesMock).toHaveBeenCalledTimes(1);

    const select = screen.getByLabelText('จำนวนรอบที่แสดง');
    fireEvent.change(select, { target: { value: '25' } });
    await waitFor(() => expect(historyRows()).toHaveLength(25));
    fireEvent.change(select, { target: { value: '50' } });
    await waitFor(() => expect(historyRows()).toHaveLength(30));

    // The whole point of fetching 100 up front: slicing is client-side.
    expect(listPlotCyclesMock).toHaveBeenCalledTimes(1);
  });

  it('changing the selector does not disturb the current-cycle section', async () => {
    getPlotMock.mockResolvedValue(basePlot());
    listPlotCyclesMock.mockResolvedValue(manyCycles(15));

    renderPage();

    await screen.findByText('ประวัติรอบปลูก');
    const before = screen.getByText('รอบปลูกปัจจุบัน').closest('section')?.textContent;

    fireEvent.change(screen.getByLabelText('จำนวนรอบที่แสดง'), { target: { value: '50' } });
    await waitFor(() => expect(historyRows()).toHaveLength(15));

    expect(screen.getByText('รอบปลูกปัจจุบัน').closest('section')?.textContent).toBe(before);
  });

  it('summarises "แสดง X จากทั้งหมด Y รอบ" below 100', async () => {
    getPlotMock.mockResolvedValue(basePlot());
    listPlotCyclesMock.mockResolvedValue(manyCycles(15));

    renderPage();

    expect(await screen.findByText('แสดง 10 จากทั้งหมด 15 รอบ')).toBeTruthy();
  });

  it('never claims a total it cannot know when the fetch came back full', async () => {
    getPlotMock.mockResolvedValue(basePlot());
    listPlotCyclesMock.mockResolvedValue(manyCycles(100));

    renderPage();

    // 100 rows back means "at least 100" — the plot may well have more.
    expect(await screen.findByText('แสดง 10 รอบล่าสุด (สูงสุด 100 รอบ)')).toBeTruthy();
    expect(screen.queryByText(/จากทั้งหมด 100 รอบ/)).toBeNull();
  });

  it('uses cycleLabel as the row name and falls back to รอบที่ N', async () => {
    getPlotMock.mockResolvedValue(basePlot());
    listPlotCyclesMock.mockResolvedValue([
      oneCycle({ id: 'c2', cycleNo: 2, status: 'active', cycleLabel: 'jun2026' }),
      oneCycle({ id: 'c1', cycleNo: 1, status: 'harvested', cycleLabel: null, closedAt: '2026-05-01T00:00:00Z' }),
    ]);

    renderPage();

    await screen.findByText('ประวัติรอบปลูก');
    expect(historyRows().map((r) => r.cells[0].textContent?.trim())).toEqual(['jun2026', 'รอบที่ 1']);
  });

  it('renders the right badge for each of the three statuses', async () => {
    getPlotMock.mockResolvedValue(basePlot());
    listPlotCyclesMock.mockResolvedValue([
      oneCycle({ id: 'c3', cycleNo: 3, status: 'active', cycleLabel: null }),
      oneCycle({ id: 'c2', cycleNo: 2, status: 'harvested', cycleLabel: null, closedAt: '2026-05-01T00:00:00Z' }),
      oneCycle({ id: 'c1', cycleNo: 1, status: 'cancelled', cycleLabel: null, closedAt: '2026-04-01T00:00:00Z' }),
    ]);

    renderPage();

    await screen.findByText('ประวัติรอบปลูก');
    expect(historyCell('รอบที่ 3', 'สถานะ')).toContain('กำลังปลูก');
    expect(historyCell('รอบที่ 2', 'สถานะ')).toContain('เก็บเกี่ยวแล้ว');
    expect(historyCell('รอบที่ 1', 'สถานะ')).toContain('ยกเลิก');
  });

  it('reads PO / P.Code / plan from the row own cycle, never the active one', async () => {
    getPlotMock.mockResolvedValue(basePlot());
    listPlotCyclesMock.mockResolvedValue([
      oneCycle({
        id: 'c2', cycleNo: 2, status: 'active', cycleLabel: null,
        poNumber: 'PO-NEW', pCode: 'PC-NEW', expectedYieldFull: '2000', expectedYieldUnit: 'kg',
      }),
      oneCycle({
        id: 'c1', cycleNo: 1, status: 'harvested', cycleLabel: null, closedAt: '2026-05-01T00:00:00Z',
        poNumber: 'PO-OLD', pCode: 'PC-OLD', expectedYieldFull: '900', expectedYieldUnit: 'kg',
      }),
    ]);

    renderPage();

    await screen.findByText('ประวัติรอบปลูก');
    expect(historyCell('รอบที่ 2', 'PO Number')).toBe('PO-NEW');
    expect(historyCell('รอบที่ 1', 'PO Number')).toBe('PO-OLD');
    expect(historyCell('รอบที่ 2', 'P.Code')).toBe('PC-NEW');
    expect(historyCell('รอบที่ 1', 'P.Code')).toBe('PC-OLD');
    expect(historyCell('รอบที่ 2', 'แผนผลผลิต')).toBe('2,000 kg');
    expect(historyCell('รอบที่ 1', 'แผนผลผลิต')).toBe('900 kg');
  });

  it('shows an em dash for every missing value', async () => {
    getPlotMock.mockResolvedValue(basePlot());
    listPlotCyclesMock.mockResolvedValue([
      oneCycle({
        id: 'c1', cycleNo: 1, status: 'harvested', cycleLabel: null,
        closedAt: '2026-05-01T00:00:00Z',
        crop: null, variety: null, poNumber: null, pCode: null, lotNo: null,
        lotNoSource: null, plantingDate: null, expectedYieldFull: null,
        expectedYieldUnit: null, harvestYield: null, finalYieldAfterClean: null,
        harvestDate: null, finalNote: null, closeReason: null,
        finalInspectionRecordId: null,
      }),
    ]);

    renderPage();

    await screen.findByText('ประวัติรอบปลูก');
    for (const column of [
      'พืช / พันธุ์', 'PO Number', 'P.Code', 'Lot No ระบบ', 'Supplier Lot No',
      'Oracle Supplier Code', 'Oracle Invoice', 'Ref Account', 'วันที่ปลูก',
      'แผนผลผลิต', 'ผลผลิตตอนเก็บเกี่ยว', 'ผลผลิตจริงหลังทำความสะอาด',
      'วันที่เก็บเกี่ยว', 'อ้างอิง',
    ]) {
      expect(historyCell('รอบที่ 1', column)).toBe('—');
    }
  });

  it('keeps the empty state when the plot has no cycles', async () => {
    getPlotMock.mockResolvedValue(basePlot());
    listPlotCyclesMock.mockResolvedValue([]);

    renderPage();

    expect(await screen.findByText('ยังไม่มีรอบปลูก')).toBeTruthy();
    // no table and no selector to operate on nothing
    expect(screen.queryByLabelText('จำนวนรอบที่แสดง')).toBeNull();
  });

  it('scrolls horizontally instead of crushing columns on a narrow screen', async () => {
    getPlotMock.mockResolvedValue(basePlot());
    listPlotCyclesMock.mockResolvedValue(manyCycles(3));

    renderPage();

    await screen.findByText('ประวัติรอบปลูก');
    const table = historyTable();
    expect(table.parentElement?.className).toContain('overflow-x-auto');
    expect(table.className).toMatch(/min-w-\[\d+px\]/);
    // fixed type scale — never sized off the viewport
    expect(table.className).not.toMatch(/text-\[\d+vw\]/);
  });
});

// --- round 8-14C: click-to-view photo lightbox integration ------------------

describe('PlotDetail — round 8-14C: photo lightbox integration', () => {
  const PHOTO_FILENAME = `${'e'.repeat(32)}.webp`;
  const PHOTO_URL = `/media/inspection-photos/${PHOTO_FILENAME}`;

  beforeEach(() => {
    getRecordPhotoBlobMock.mockResolvedValue(new Blob(['x'], { type: 'image/webp' }));
    vi.spyOn(URL, 'createObjectURL').mockReturnValue('blob:fake-url');
  });

  it('31. the latest-photo (ภาพถ่ายล่าสุด) thumbnail opens the lightbox on click', async () => {
    getPlotMock.mockResolvedValue(basePlot({ lastInspectionRecordId: 'rec-1' }));
    getRecordMock.mockResolvedValue({ id: 'rec-1', photoUrls: [PHOTO_URL] });

    renderPage();

    await screen.findByText('ภาพถ่ายล่าสุด (1)');
    const thumbnail = await screen.findByRole('button', { name: 'เปิดดูภาพถ่ายแปลงขนาดใหญ่' });
    fireEvent.click(thumbnail);

    expect(await screen.findByRole('dialog')).toBeTruthy();
    expect(getRecordPhotoBlobMock).toHaveBeenCalledWith('rec-1', PHOTO_FILENAME);
  });

  it('32. a photo inside an expanded ประวัติการตรวจ row opens the lightbox on click', async () => {
    getPlotMock.mockResolvedValue(basePlot());
    listRecordsMock.mockResolvedValue([baseRecordSummary({ id: 'rec-h1' })]);
    getRecordMock.mockResolvedValue({
      id: 'rec-h1', photoUrls: [PHOTO_URL], recommendation: null, notes: null,
      fieldPrepScore: null, weatherScore: null, careScore: null, varietyResistanceScore: null,
      customFields: {},
    });

    renderPage();
    await screen.findByText('คะแนน 4 ด้าน:');
    fireEvent.click(screen.getByText('2026-07-01')); // expand the row

    const thumbnail = await screen.findByRole('button', { name: 'เปิดดูภาพถ่ายแปลงขนาดใหญ่' });
    fireEvent.click(thumbnail);

    expect(await screen.findByRole('dialog')).toBeTruthy();
    expect(getRecordPhotoBlobMock).toHaveBeenCalledWith('rec-h1', PHOTO_FILENAME);
  });

  it('33. no photos on the latest record means no lightbox trigger exists', async () => {
    getPlotMock.mockResolvedValue(basePlot({ lastInspectionRecordId: 'rec-1' }));
    getRecordMock.mockResolvedValue({ id: 'rec-1', photoUrls: [] });

    renderPage();

    await screen.findByText('ดูบันทึกการตรวจล่าสุดแบบเต็ม →');
    expect(screen.queryByRole('button', { name: 'เปิดดูภาพถ่ายแปลงขนาดใหญ่' })).toBeNull();
  });

  it('34. a photo fetch failure degrades gracefully — no crash, rest of the current-status section still renders (no regression)', async () => {
    getRecordPhotoBlobMock.mockRejectedValue(new Error('403'));
    getPlotMock.mockResolvedValue(basePlot({ lastInspectionRecordId: 'rec-1' }));
    getRecordMock.mockResolvedValue({ id: 'rec-1', photoUrls: [PHOTO_URL] });

    renderPage();

    // One bad photo must never take the rest of the page down with it.
    await screen.findByText('ดูบันทึกการตรวจล่าสุดแบบเต็ม →');
    expect(screen.queryByRole('button', { name: 'เปิดดูภาพถ่ายแปลงขนาดใหญ่' })).toBeNull();
    expect(screen.queryByRole('dialog')).toBeNull();
  });
});

// --- round 8-14D: ประวัติการตรวจ page-size selector -------------------------
// NOTE: this is the INSPECTION-RECORD history (backend-paginated via
// listRecords' limit/offset), NOT the cycle-history table above — that one
// has its own separate "จำนวนรอบที่แสดง" selector which slices an
// already-fetched array client-side and is deliberately untouched here.

describe('PlotDetail — round 8-14D: inspection history page-size selector', () => {
  /** N distinct record summaries — enough rows for the ถัดไป button to
   * enable (it needs history.length >= the current page size). */
  function manyRecords(n: number): RecordSummary[] {
    return Array.from({ length: n }, (_, i) =>
      baseRecordSummary({ id: `rec-${i + 1}`, recordDate: `2026-07-${String(i + 1).padStart(2, '0')}` }));
  }

  function pageSizeSelect(): HTMLSelectElement {
    return screen.getByLabelText('แสดงต่อหน้า') as HTMLSelectElement;
  }

  /** Waits for the history SECTION itself, not merely for listRecords to
   * have fired — the whole page renders a single "กำลังโหลด..." spinner
   * until the plot query resolves, so the selector doesn't exist yet even
   * though the records request is already in flight. */
  async function waitForHistorySection(): Promise<HTMLSelectElement> {
    return (await screen.findByLabelText('แสดงต่อหน้า')) as HTMLSelectElement;
  }

  function lastListRecordsCall() {
    return listRecordsMock.mock.calls[listRecordsMock.mock.calls.length - 1][0];
  }

  it('1. defaults to 5 per page — the first request uses limit: 5, offset: 0', async () => {
    getPlotMock.mockResolvedValue(basePlot());
    listRecordsMock.mockResolvedValue(manyRecords(5));

    renderPage();

    const select = await waitForHistorySection();
    expect(lastListRecordsCall()).toEqual({ plotId: 'plot-1', limit: 5, offset: 0 });
    expect(select.value).toBe('5');
  });

  it('2. the selector offers exactly 5 / 10 / 20 / 50 / 100', async () => {
    getPlotMock.mockResolvedValue(basePlot());
    listRecordsMock.mockResolvedValue(manyRecords(5));

    renderPage();

    const options = Array.from((await screen.findByLabelText('แสดงต่อหน้า') as HTMLSelectElement).options);
    expect(options.map((o) => o.value)).toEqual(['5', '10', '20', '50', '100']);
    expect(options.map((o) => o.textContent)).toEqual([
      '5 รายการ', '10 รายการ', '20 รายการ', '50 รายการ', '100 รายการ',
    ]);
  });

  it('3. selecting 10 refetches with limit: 10', async () => {
    getPlotMock.mockResolvedValue(basePlot());
    listRecordsMock.mockResolvedValue(manyRecords(10));

    renderPage();
    const select = await waitForHistorySection();

    fireEvent.change(select, { target: { value: '10' } });

    await waitFor(() => expect(lastListRecordsCall()).toEqual({ plotId: 'plot-1', limit: 10, offset: 0 }));
  });

  it('4. paging forward at size 10 uses offset: 10', async () => {
    getPlotMock.mockResolvedValue(basePlot());
    listRecordsMock.mockResolvedValue(manyRecords(10));

    renderPage();
    const select = await waitForHistorySection();
    fireEvent.change(select, { target: { value: '10' } });
    // The key change puts the query back into its loading state, so the
    // pagination controls unmount until the new page lands — wait for the
    // button itself, not merely for the request to have fired.
    await waitFor(() => expect(lastListRecordsCall().limit).toBe(10));
    fireEvent.click(await screen.findByRole('button', { name: 'ถัดไป →' }));

    await waitFor(() => expect(lastListRecordsCall()).toEqual({ plotId: 'plot-1', limit: 10, offset: 10 }));
    expect(await screen.findByText('หน้า 2')).toBeTruthy();
  });

  it('5. changing the page size while on a later page resets to page 1 / offset 0', async () => {
    getPlotMock.mockResolvedValue(basePlot());
    listRecordsMock.mockResolvedValue(manyRecords(10));

    renderPage();
    const select = await waitForHistorySection();
    fireEvent.change(select, { target: { value: '10' } });
    await waitFor(() => expect(lastListRecordsCall().limit).toBe(10));
    fireEvent.click(await screen.findByRole('button', { name: 'ถัดไป →' }));
    await waitFor(() => expect(lastListRecordsCall().offset).toBe(10));
    expect(await screen.findByText('หน้า 2')).toBeTruthy();

    // Page 2 of 10-row pages is not page 2 of 20-row pages — go back to the
    // first page rather than leaving offset pointing into nowhere.
    fireEvent.change(pageSizeSelect(), { target: { value: '20' } });

    await waitFor(() => expect(lastListRecordsCall()).toEqual({ plotId: 'plot-1', limit: 20, offset: 0 }));
    expect(await screen.findByText('หน้า 1')).toBeTruthy();
  });

  it('6. ถัดไป is disabled when the page returned fewer rows than the page size', async () => {
    getPlotMock.mockResolvedValue(basePlot());
    listRecordsMock.mockResolvedValue(manyRecords(3)); // 3 < default 5

    renderPage();

    await screen.findByText('ประวัติการตรวจ');
    const next = await screen.findByRole('button', { name: 'ถัดไป →' }) as HTMLButtonElement;
    expect(next.disabled).toBe(true);
    // ...and ก่อนหน้า is disabled on the first page, as before.
    expect((screen.getByRole('button', { name: '← ก่อนหน้า' }) as HTMLButtonElement).disabled).toBe(true);
  });

  it('7a. no records.read permission → no history section and no selector at all', async () => {
    allowedPerms = new Set(['plots.read']);
    getPlotMock.mockResolvedValue(basePlot());

    renderPage();

    await screen.findByRole('heading', { level: 1 });
    expect(screen.queryByText('ประวัติการตรวจ')).toBeNull();
    expect(screen.queryByLabelText('แสดงต่อหน้า')).toBeNull();
    expect(listRecordsMock).not.toHaveBeenCalled();
  });

  it('7b. the empty state still renders (with the selector present) and hides pagination', async () => {
    getPlotMock.mockResolvedValue(basePlot());
    listRecordsMock.mockResolvedValue([]);

    renderPage();

    expect(await screen.findByText('ยังไม่มีประวัติการตรวจ')).toBeTruthy();
    expect(screen.getByLabelText('แสดงต่อหน้า')).toBeTruthy();
    expect(screen.queryByRole('button', { name: 'ถัดไป →' })).toBeNull();
  });

  it('7c. the error state still renders without regression', async () => {
    getPlotMock.mockResolvedValue(basePlot());
    listRecordsMock.mockRejectedValue(new Error('500'));

    renderPage();

    expect(await screen.findByText('โหลดประวัติการตรวจไม่สำเร็จ')).toBeTruthy();
  });

  it('8. the cycle-history table keeps its OWN separate selector, untouched by this one', async () => {
    getPlotMock.mockResolvedValue(basePlot());
    listPlotCyclesMock.mockResolvedValue([oneCycle()]);
    listRecordsMock.mockResolvedValue(manyRecords(5));

    renderPage();

    await screen.findByText('ประวัติรอบปลูก');
    // Two independent selectors, different labels, different option text.
    const cycleSelect = screen.getByLabelText('จำนวนรอบที่แสดง') as HTMLSelectElement;
    const recordSelect = screen.getByLabelText('แสดงต่อหน้า') as HTMLSelectElement;
    expect(cycleSelect).not.toBe(recordSelect);
    expect(cycleSelect.value).toBe('10');   // cycle table's own default, unchanged
    expect(recordSelect.value).toBe('5');   // inspection history's new default

    // Changing the inspection-history size must not refetch or resize cycles.
    listPlotCyclesMock.mockClear();
    fireEvent.change(recordSelect, { target: { value: '20' } });
    await waitFor(() => expect(lastListRecordsCall().limit).toBe(20));
    expect(listPlotCyclesMock).not.toHaveBeenCalled();
    expect(cycleSelect.value).toBe('10');
  });
});
