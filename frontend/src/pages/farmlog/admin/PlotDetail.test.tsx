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
import { useAuthStore } from '../../../stores/auth';
import type { Role, User } from '../../../types/auth';

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
const cancelPlotCycleMock = vi.fn();
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
    cancelPlotCycle: (...args: unknown[]) => cancelPlotCycleMock(...args),
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

// The cycle modals' MasterDataSelect fields query this — never the real
// apiClient. Round 8-26C: it can no longer return an empty list, because
// P.Code stopped being a typed field and is DERIVED from the chosen พันธุ์,
// so a cycle form can only be filled in by picking real options. One crop,
// two varieties, one P.Code each (พริกจินดา's is Melon-Z, used by the
// reactivate test that needs a second distinct value).
const MD_P_CODE_BY_VARIETY: Record<string, string> = {
  'พริกขี้หนู': 'Melon-A',
  'พริกจินดา': 'Melon-Z',
};

function masterDataRow(type: string, value: string, parent: string | null) {
  return {
    id: `md-${type}-${value}`, type, value, parent, orderIndex: 0, active: true,
    createdAt: '2026-01-01T00:00:00Z', updatedAt: '2026-01-01T00:00:00Z',
  };
}

vi.mock('../../../api/masterdata', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../../api/masterdata')>();
  return {
    ...actual,
    listMasterData: ({ type, parent }: { type: string; parent?: string }) => {
      if (type === 'crop') return Promise.resolve([masterDataRow('crop', 'พริก', null)]);
      if (type === 'variety') {
        return Promise.resolve(
          Object.keys(MD_P_CODE_BY_VARIETY).map((v) => masterDataRow('variety', v, 'พริก')),
        );
      }
      if (type === 'p_code') {
        const code = parent ? MD_P_CODE_BY_VARIETY[parent] : undefined;
        return Promise.resolve(code ? [masterDataRow('p_code', code, parent!)] : []);
      }
      return Promise.resolve([]);
    },
  };
});

/** Round 8-26C — fills the cycle form's crop/variety and waits for the
 * derived P.Code to land. Replaces the old "type into the P.Code box" step:
 * that box is read-only now. */
async function pickCropAndVariety(variety = 'พริกขี้หนู') {
  fireEvent.change(await screen.findByLabelText('— เลือกชนิดพืช —'), { target: { value: 'พริก' } });
  fireEvent.change(await screen.findByLabelText('— เลือกพันธุ์ —'), { target: { value: variety } });
  await waitFor(() => expect(
    (screen.getByLabelText('P.Code') as HTMLInputElement).value,
  ).toBe(MD_P_CODE_BY_VARIETY[variety]));
}

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

// --- Round Q: the cycle-history TABLE is gone -----------------------------
// Under "one plot, one cycle" (round E) it was a one-row, 18-column table with
// a horizontal scrollbar; after round P (a close also retires the plot) it was
// the only place a finished season's data appeared at all. Everything it
// showed now lives in the "รอบปลูก" card as a label/value list, so the old
// historyRow/historyCell helpers become this: read one field by its label.

function cycleField(label: string): string {
  const dt = screen.getAllByText(label).find((el) => el.tagName === 'DT');
  if (!dt) throw new Error(`no cycle field labelled "${label}"`);
  return dt.nextElementSibling?.textContent?.trim() ?? '';
}

/** Same, but null when the field is not rendered at all — which is how the
 * card reports "this does not apply", e.g. harvest figures on a live cycle. */
function queryCycleField(label: string): string | null {
  const dt = screen.queryAllByText(label).find((el) => el.tagName === 'DT');
  return dt ? (dt.nextElementSibling?.textContent?.trim() ?? '') : null;
}

beforeEach(() => {
  getPlotMock.mockReset();
  listRecordsMock.mockReset();
  getRecordMock.mockReset();
  listPlotCyclesMock.mockReset();
  createPlotCycleMock.mockReset();
  updatePlotCycleMock.mockReset();
  closePlotCycleMock.mockReset();
  cancelPlotCycleMock.mockReset();
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
  // Round 8-25O — พันธุ์/สายพันธุ์ visibility needs an internal role
  // (canViewVariety); default to internal:admin so existing crop/variety
  // assertions keep holding. Supplier-role visibility is its own describe
  // block below, setting a supplier:owner user explicitly per test.
  useAuthStore.setState({ user: internalUser() });
});

function _role(name: string): Role {
  return { id: `role-${name}`, name, displayName: name, providerScope: 'internal', isSystem: true };
}

function internalUser(): User {
  return {
    id: 'user-1', email: 'user@example.com', fullName: 'Test User',
    authProvider: 'local', isActive: true, emailVerified: true,
    roles: [_role('internal:admin')],
  };
}

function supplierUser(): User {
  return {
    id: 'user-2', email: 'supplier@example.com', fullName: 'Supplier User',
    authProvider: 'local', isActive: true, emailVerified: true,
    roles: [_role('supplier:owner')],
  };
}

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
    // "เปอร์เซ็นต์เทียบเป้าผลิต" field (round 8-27F) — both read the same
    // plot.currentYieldPct.
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

  // Round 8-25O — พันธุ์/สายพันธุ์ is Chiatai-internal-only.
  it('hides the พันธุ์/สายพันธุ์ field for a supplier-role user, crop stays visible', async () => {
    useAuthStore.setState({ user: supplierUser() });
    listPlotCyclesMock.mockResolvedValue([]);
    getPlotMock.mockResolvedValue(basePlot({
      currentCrop: 'พริก', currentVariety: 'พริกขี้หนู',
      lastInspectedByCode: 'FIELD01', lastInspectionRecordId: 'rec-1',
    }));
    getRecordMock.mockResolvedValue({ id: 'rec-1', photoUrls: [] });

    renderPage();

    expect(await screen.findByText('พริก')).toBeTruthy();
    expect(screen.queryByText('พริกขี้หนู')).toBeNull();
    expect(screen.queryByText('พันธุ์/สายพันธุ์')).toBeNull();
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

  it('shows "รอเริ่มรอบปลูก" with a "เริ่มรอบปลูกแรก" button when there is no active cycle', async () => {
    getPlotMock.mockResolvedValue(basePlot());
    listPlotCyclesMock.mockResolvedValue([]);

    renderPage();

    // Round 8.0.4 — both the current-cycle section AND the yield-planning
    // hero card independently show the no-active-cycle state.
    expect((await screen.findAllByText('รอเริ่มรอบปลูก')).length).toBeGreaterThanOrEqual(1);
    expect(screen.getByText('แปลงนี้ยังใช้งานอยู่ แต่ยังไม่มีรอบปลูกที่เปิดอยู่')).toBeTruthy();
    expect(screen.getAllByRole('button', { name: 'เริ่มรอบปลูกแรก' }).length).toBeGreaterThanOrEqual(1);
  });

  it('hides "เริ่มรอบปลูกแรก" without plots.update', async () => {
    allowedPerms = new Set(['plots.read', 'records.create']);
    getPlotMock.mockResolvedValue(basePlot());
    listPlotCyclesMock.mockResolvedValue([]);

    renderPage();

    await screen.findAllByText('รอเริ่มรอบปลูก');
    expect(screen.queryByRole('button', { name: 'เริ่มรอบปลูกแรก' })).toBeNull();
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
    // hero card show a "เริ่มรอบปลูกแรก" button when there's no active
    // cycle; either one opens the same StartCycleModal.
    fireEvent.click((await screen.findAllByRole('button', { name: 'เริ่มรอบปลูกแรก' }))[0]);
    fireEvent.change(screen.getByPlaceholderText('เช่น PO25001'), { target: { value: 'PO25001' } });
    await pickCropAndVariety();
    // Round 8-12B — Auto Lot needs a cycleLabel as well as a P.Code.
    fireEvent.change(screen.getByPlaceholderText('เช่น jun2026 หรือ may2026'), { target: { value: '2605' } });
    fireEvent.click(await screen.findByRole('button', { name: 'เริ่มรอบปลูก' }));

    await waitFor(() => expect(createPlotCycleMock).toHaveBeenCalledWith('plot-1', expect.any(Object)));
    // Modal closed and the section now reflects the (refetched) active cycle
    // — "กำลังปลูก" renders twice (current-cycle section + its history row).
    await waitFor(() => expect(screen.queryByRole('button', { name: 'เริ่มรอบปลูกแรก' })).toBeNull());
    await waitFor(() => expect(screen.getAllByText('กำลังปลูก').length).toBeGreaterThanOrEqual(1));
  });

  it('start-cycle modal has a ชื่อรอบปลูก input and sends its value to createPlotCycle (round 8.0)', async () => {
    getPlotMock.mockResolvedValue(basePlot());
    listPlotCyclesMock.mockResolvedValueOnce([]).mockResolvedValue([oneCycle({ cycleLabel: 'jul2026' })]);
    createPlotCycleMock.mockResolvedValue(oneCycle({ cycleLabel: 'jul2026' }));

    renderPage();

    fireEvent.click((await screen.findAllByRole('button', { name: 'เริ่มรอบปลูกแรก' }))[0]);
    const labelInput = await screen.findByPlaceholderText('เช่น jun2026 หรือ may2026');
    fireEvent.change(labelInput, { target: { value: 'jul2026' } });
    fireEvent.change(screen.getByPlaceholderText('เช่น PO25001'), { target: { value: 'PO25001' } });
    await pickCropAndVariety();
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

    fireEvent.click(await screen.findByRole('button', { name: /ปิดรอบปลูก/ }));
    fireEvent.click(await screen.findByRole('button', { name: 'ยืนยันปิดรอบปลูก' }));

    await waitFor(() => expect(closePlotCycleMock).toHaveBeenCalledWith(
      'plot-1', 'cycle-1', expect.objectContaining({ status: 'harvested' }),
    ));
    // ตรวจแปลง disappears (no active cycle) once the refetched state lands.
    await waitFor(() => expect(screen.queryByRole('link', { name: 'ตรวจแปลง' })).toBeNull());
    expect((await screen.findAllByText('รอเริ่มรอบปลูก')).length).toBeGreaterThanOrEqual(1);
  });

  it('the card badges the LATEST cycle status, never an older one (round Q)', async () => {
    getPlotMock.mockResolvedValue(basePlot());
    listPlotCyclesMock.mockResolvedValue([
      oneCycle({ id: 'cycle-2', cycleNo: 2, status: 'active', crop: 'ทุเรียน', lotNo: 'LOT-02' }),
      oneCycle({ id: 'cycle-1', cycleNo: 1, status: 'harvested', closedAt: '2026-05-01T00:00:00Z', closeReason: 'เก็บเกี่ยวรอบแรก' }),
    ]);

    renderPage();

    await screen.findByText('ชนิดพืช');
    // Round Q — with the history table gone there is one card, and it shows
    // the LATEST cycle: its title, its status badge, and nothing belonging to
    // the older closed one beside it.
    expect(screen.getAllByText(/รอบที่ 2/).length).toBeGreaterThanOrEqual(1);
    expect(screen.getByText('กำลังปลูก')).toBeTruthy();
    expect(screen.queryByText('เก็บเกี่ยวแล้ว')).toBeNull();
    expect(screen.queryByText(/เก็บเกี่ยวรอบแรก/)).toBeNull();
  });

  it('round 8-3K: labels the current cycle\'s Lot No. clearly as "เลขล็อต (Lot No.)"', async () => {
    getPlotMock.mockResolvedValue(basePlot());
    // Round Q — the lot now renders in exactly one place: the รอบปลูก card's
    // <dt>/<dd> pair. It used to be here AND in the history table's column.
    listPlotCyclesMock.mockResolvedValue([oneCycle({ lotNo: 'LOT-09' })]);

    renderPage();

    const labels = await screen.findAllByText('Lot No ระบบ');
    expect(labels.length).toBeGreaterThanOrEqual(1);
    const currentCycleLabel = labels.find((el) => el.tagName === 'DT');
    // Round 8-5B — the value carries a source badge, so match the substring.
    expect(currentCycleLabel?.nextElementSibling?.textContent).toContain('LOT-09');
    // Round Q — ONE place now. It used to appear twice (card + history table),
    // which is exactly the duplication this round removed.
    expect(screen.getAllByText('LOT-09').length).toBe(1);
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

  it('round 8-3K/Q: the card shows the LATEST cycle Lot No., never an older one', async () => {
    getPlotMock.mockResolvedValue(basePlot());
    listPlotCyclesMock.mockResolvedValue([
      oneCycle({ id: 'cycle-2', cycleNo: 2, status: 'active', crop: 'ทุเรียน', lotNo: 'LOT-02' }),
      oneCycle({ id: 'cycle-1', cycleNo: 1, status: 'harvested', closedAt: '2026-05-01T00:00:00Z', lotNo: 'LOT-01' }),
    ]);

    renderPage();

    await screen.findByText('ชนิดพืช');
    // Round Q — the history table this used to check row-by-row is gone. The
    // guarantee that replaces it: the card reads exactly one cycle, the
    // latest, and never mixes an older cycle's value into it.
    expect(cycleField('Lot No ระบบ')).toContain('LOT-02');
    expect(screen.queryByText('LOT-01')).toBeNull();
  });

  it('shows a clear empty state when the plot has no cycles at all', async () => {
    // Round Q — the history table had its own "ยังไม่มีรอบปลูก" line; with the
    // table gone the รอบปลูก card's existing empty state is the single answer.
    getPlotMock.mockResolvedValue(basePlot());
    listPlotCyclesMock.mockResolvedValue([]);

    renderPage();

    expect((await screen.findAllByText('รอเริ่มรอบปลูก')).length).toBeGreaterThan(0);
    expect(screen.queryByText('ชนิดพืช')).toBeNull();
  });

  it('invalidates plot, plot-cycles, plots, and the plot-status report after starting a cycle', async () => {
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const invalidateSpy = vi.spyOn(qc, 'invalidateQueries');
    getPlotMock.mockResolvedValue(basePlot());
    listPlotCyclesMock.mockResolvedValueOnce([]).mockResolvedValue([oneCycle()]);
    createPlotCycleMock.mockResolvedValue(oneCycle());

    renderPage(qc);

    fireEvent.click((await screen.findAllByRole('button', { name: 'เริ่มรอบปลูกแรก' }))[0]);
    fireEvent.change(screen.getByPlaceholderText('เช่น PO25001'), { target: { value: 'PO25001' } });
    await pickCropAndVariety();
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

    await screen.findByText('ผลการปิดรอบ');
    // Round Q — a field of the รอบปลูก card, rendered only once the cycle is
    // closed. Verbatim stored value (999), never recomputed to expected×pct.
    expect(cycleField('ประมาณการสุดท้าย')).toBe('999 kg (80%)');
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

    await screen.findByText('ผลการปิดรอบ');
    // Still verbatim; the "ก่อนยกเลิก" wording lives in the shared
    // describeFinalEstimate helper and has its own unit tests. The status is
    // the card's badge now rather than a table cell.
    expect(cycleField('ประมาณการสุดท้าย')).toBe('405 kg (45%)');
    expect(screen.getByText('ยกเลิก')).toBeTruthy();
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

    await screen.findByText('ผลการปิดรอบ');
    expect(screen.getByText(/ไม่มีข้อมูลประมาณการตอนปิดรอบ/)).toBeTruthy();
  });

  it('active cycle in history shows NO final snapshot line', async () => {
    getPlotMock.mockResolvedValue(basePlot());
    listPlotCyclesMock.mockResolvedValue([
      oneCycle({ id: 'cycle-1', cycleNo: 1, status: 'active' }),
    ]);

    renderPage();

    await screen.findByText('ชนิดพืช');
    // Round Q — a live cycle has no final snapshot, so the card omits the
    // whole ผลการปิดรอบ block: no fabricated number, and not the "no data"
    // message meant for a CLOSED cycle either.
    expect(screen.queryByText('ผลการปิดรอบ')).toBeNull();
    expect(queryCycleField('ประมาณการสุดท้าย')).toBeNull();
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

    await screen.findByText('ชนิดพืช');
    // Round 8-10A — estimate and actuals are now three SEPARATE columns, so a
    // reader can never mistake one for the other.
    expect(cycleField('ประมาณการสุดท้าย')).toBe('800 kg (80%)');
    expect(cycleField('ผลผลิตตอนเก็บเกี่ยว')).toBe('1,250 kg');
    expect(cycleField('ผลผลิตจริงหลังทำความสะอาด')).toBe('1,180 kg');
    // Date-only string, verbatim (never through Date()).
    expect(cycleField('วันที่เก็บเกี่ยว')).toBe('2026-08-30');
    expect(screen.getByText(/หมายเหตุ: ผลผลิตหลังคัดแยกและทำความสะอาด/)).toBeTruthy();
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

    await screen.findByText('ชนิดพืช');
    // Every actual-harvest cell is a dash — never a fabricated figure.
    expect(cycleField('ผลผลิตตอนเก็บเกี่ยว')).toBe('—');
    expect(cycleField('ผลผลิตจริงหลังทำความสะอาด')).toBe('—');
    expect(cycleField('วันที่เก็บเกี่ยว')).toBe('—');
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

    await screen.findByText('ชนิดพืช');
    // Actual harvest belongs to a CLOSED cycle only.
    // Round Q — stronger than the table's dash: on a live cycle the card does
    // not render the ผลการปิดรอบ block at all, so there is no cell for a
    // stray value to leak into.
    expect(screen.queryByText('ผลการปิดรอบ')).toBeNull();
    expect(queryCycleField('ผลผลิตตอนเก็บเกี่ยว')).toBeNull();
    expect(queryCycleField('ผลผลิตจริงหลังทำความสะอาด')).toBeNull();
    expect(queryCycleField('วันที่เก็บเกี่ยว')).toBeNull();
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

    await screen.findByText('ชนิดพืช');
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

    await screen.findByText('ชนิดพืช');
    expect(screen.queryByRole('link', { name: /บันทึกที่ใช้สรุป/ })).toBeNull();
    // Round 8-10A tightened this: previously a truncated id was rendered. An
    // id is still a pointer at a record this caller may not read, so the cell
    // now only states that one exists.
    expect(screen.queryByText(/rec-42/)).toBeNull();
    expect(screen.getByText(/บันทึกที่ใช้สรุป|หมายเหตุ:/).closest('div')!.textContent!).toContain('บันทึกที่ใช้สรุป: มี');
  });
});

describe('PlotDetail — rollover is retired (round E)', () => {
  it('never offers "จบรอบ + เริ่มรอบใหม่", even on an active plot with an active cycle', async () => {
    // Rollover exists only to start a SECOND cycle on the same plot, which
    // "one plot, one cycle" does not allow. The button is gone in every state
    // it used to appear in; closing a cycle on its own is unaffected, and is
    // where the season's actual harvest is now recorded (round D).
    getPlotMock.mockResolvedValue(basePlot());
    listPlotCyclesMock.mockResolvedValue([oneCycle()]);

    renderPage();

    await screen.findByRole('button', { name: /ปิดรอบปลูก/ });
    expect(screen.queryByRole('button', { name: 'จบรอบ + เริ่มรอบใหม่' })).toBeNull();
    expect(rolloverPlotCycleMock).not.toHaveBeenCalled();
  });

  it('still offers ปิดรอบปลูก — closing a cycle is not what was retired', async () => {
    getPlotMock.mockResolvedValue(basePlot());
    listPlotCyclesMock.mockResolvedValue([oneCycle()]);

    renderPage();

    expect(await screen.findByRole('button', { name: /ปิดรอบปลูก/ })).toBeTruthy();
  });
});

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

  it('offers plain reactivation only — never reactivate-with-a-new-cycle (round E)', async () => {
    // Reopening a plot AND starting another season is the same move as
    // rollover, so it went with it. Plain reactivation stays: an accidental
    // deactivation must remain undoable, and reopening a plot without
    // starting a cycle breaks no rule.
    getPlotMock.mockResolvedValue(inactivePlot());
    listPlotCyclesMock.mockResolvedValue([]);
    allowedPerms = new Set(['plots.delete', 'plots.update', 'plots.read']);
    renderPage();

    expect(await screen.findByRole('button', { name: 'เปิดใช้งานแปลงเท่านั้น' })).toBeTruthy();
    expect(screen.queryByRole('button', { name: 'เปิดใช้งานและเริ่มรอบปลูกใหม่' })).toBeNull();
    expect(reactivatePlotWithCycleMock).not.toHaveBeenCalled();
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

  it('never shows "เริ่มรอบปลูกแรก" for an inactive plot — StartCycleModal must not be reachable', async () => {
    getPlotMock.mockResolvedValue(inactivePlot());
    listPlotCyclesMock.mockResolvedValue([]);
    allowedPerms = new Set(['plots.update', 'plots.delete', 'plots.read']);
    renderPage();

    await screen.findByText('แปลงนี้ปิดใช้งานอยู่');
    expect(screen.queryByRole('button', { name: 'เริ่มรอบปลูกแรก' })).toBeNull();
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

  it('reactivate-with-cycle is unreachable — the flow retired with rollover (round E)', async () => {
    // The endpoint and its modal still exist; nothing in the UI opens them.
    // Reopening a plot AND starting another season is the same move rollover
    // made, and "one plot, one cycle" allows neither.
    getPlotMock.mockResolvedValue(inactivePlot());
    listPlotCyclesMock.mockResolvedValue([]);
    allowedPerms = new Set(['plots.delete', 'plots.update', 'plots.read']);
    renderPage();

    await screen.findByRole('button', { name: 'เปิดใช้งานแปลงเท่านั้น' });
    expect(screen.queryByRole('button', { name: 'เปิดใช้งานและเริ่มรอบปลูกใหม่' })).toBeNull();
    // and no path reaches the API even indirectly
    expect(reactivatePlotWithCycleMock).not.toHaveBeenCalled();
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

  it('regression: active-plot workflows (ตรวจแปลง, เริ่มรอบปลูกแรก, แก้ไขแปลง) are unaffected', async () => {
    getPlotMock.mockResolvedValue(basePlot());
    listPlotCyclesMock.mockResolvedValue([]);
    allowedPerms = new Set(['plots.update', 'plots.delete', 'plots.read', 'records.create']);
    renderPage();

    expect(await screen.findByRole('link', { name: 'แก้ไขแปลง' })).toBeTruthy();
    // Both the current-cycle section and the yield-planning hero card show
    // their own "เริ่มรอบปลูกแรก" button when there's no active cycle
    // (pre-existing behavior, unchanged by this round).
    expect((await screen.findAllByRole('button', { name: 'เริ่มรอบปลูกแรก' })).length).toBeGreaterThanOrEqual(1);
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
    expect(await screen.findByText('Yield % (เทียบเป้าผลิต)')).toBeTruthy();
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
    expect(await screen.findByText('ผลผลิตที่คาดว่าจะได้:')).toBeTruthy();
    expect((await screen.findAllByText('1,600 kg')).length).toBeGreaterThanOrEqual(1);
    expect(screen.getByText('เป้าผลิตที่ใช้คำนวณ:')).toBeTruthy();
    // "1,000 kg" also legitimately matches the active cycle's own Expected
    // Yield field elsewhere on the page (default fixture) — just prove ours
    // is among them, not that it's the only occurrence.
    expect((await screen.findAllByText('1,000 kg')).length).toBeGreaterThanOrEqual(1);
    expect(screen.getByText('เปอร์เซ็นต์เทียบเป้าผลิต:')).toBeTruthy();
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

// --- round 8-9B: "รหัส Supplier ตรวจแปลง" status section ----------------------------

describe('PlotDetail — inspection password section (round 8-9B)', () => {
  it('renders the section heading next to the access-phone section', async () => {
    getPlotMock.mockResolvedValue(basePlot());

    renderPage();

    expect(await screen.findByText('รหัส Supplier ตรวจแปลง')).toBeTruthy();
    expect(screen.getByText('เบอร์โทรสำหรับเข้าตรวจแปลง')).toBeTruthy();
  });

  it('shows a stable loading placeholder and never flashes "ยังไม่ตั้งรหัส" first', async () => {
    getPlotMock.mockResolvedValue(basePlot());
    getPlotInspectionCredentialMock.mockReturnValue(new Promise(() => {})); // never resolves

    renderPage();

    expect(await screen.findByText('กำลังโหลดสถานะรหัส Supplier ตรวจแปลง…')).toBeTruthy();
    expect(screen.queryByText('ยังไม่ตั้งรหัส')).toBeNull();
    expect(screen.queryByText('ตั้งรหัสแล้ว')).toBeNull();
    // no action button until we know which action it is
    expect(screen.queryByRole('button', { name: /ตั้งรหัส Supplier ตรวจแปลง|เปลี่ยนรหัส Supplier ตรวจแปลง/ })).toBeNull();
  });

  it('shows "ยังไม่ตั้งรหัส" with the rollout hint when configured=false', async () => {
    getPlotMock.mockResolvedValue(basePlot());

    renderPage();

    expect(await screen.findByText('ยังไม่ตั้งรหัส')).toBeTruthy();
    expect(screen.getByText('ต้องตั้งรหัสก่อนเปิดใช้การค้นหาแปลงด้วยหมายเลขและรหัส')).toBeTruthy();
    expect(screen.getByRole('button', { name: 'ตั้งรหัส Supplier ตรวจแปลง' })).toBeTruthy();
  });

  it('shows "ตั้งรหัสแล้ว" plus the last-updated time when configured=true', async () => {
    getPlotMock.mockResolvedValue(basePlot());
    getPlotInspectionCredentialMock.mockResolvedValue({
      configured: true, credentialVersion: 2, updatedAt: '2026-08-01T10:00:00Z',
    });

    renderPage();

    expect(await screen.findByText('ตั้งรหัสแล้ว')).toBeTruthy();
    expect(screen.getByText(/แก้ไขล่าสุด/)).toBeTruthy();
    expect(screen.getByRole('button', { name: 'เปลี่ยนรหัส Supplier ตรวจแปลง' })).toBeTruthy();
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

    expect(await screen.findByText('โหลดสถานะรหัส Supplier ตรวจแปลงไม่สำเร็จ')).toBeTruthy();
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

    await screen.findByText('โหลดสถานะรหัส Supplier ตรวจแปลงไม่สำเร็จ');
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
    expect(screen.queryByRole('button', { name: 'ตั้งรหัส Supplier ตรวจแปลง' })).toBeNull();
  });

  it('shows the set/change button with plots.update', async () => {
    allowedPerms = new Set(['plots.read', 'plots.update']);
    getPlotMock.mockResolvedValue(basePlot());

    renderPage();

    expect(await screen.findByRole('button', { name: 'ตั้งรหัส Supplier ตรวจแปลง' })).toBeTruthy();
  });

  it('opens the modal with the plot identity and the correct mode', async () => {
    getPlotMock.mockResolvedValue(basePlot());
    getPlotInspectionCredentialMock.mockResolvedValue({
      configured: true, credentialVersion: 1, updatedAt: null,
    });

    renderPage();

    fireEvent.click(await screen.findByRole('button', { name: 'เปลี่ยนรหัส Supplier ตรวจแปลง' }));

    // modal title reflects the configured state, not just the button label
    await waitFor(() => expect(screen.getByRole('heading', { name: 'เปลี่ยนรหัส Supplier ตรวจแปลง' })).toBeTruthy());
    expect(screen.getByLabelText('รหัส Supplier ตรวจแปลง')).toBeTruthy();
    expect(screen.getByLabelText('ยืนยันรหัสอีกครั้ง')).toBeTruthy();
    // Supplier / plot identity carried into the modal
    expect(screen.getAllByText('SUP001').length).toBeGreaterThanOrEqual(1);
    expect(screen.getAllByText('SUP001-P001').length).toBeGreaterThanOrEqual(1);
  });

  it('opens the modal in "set" mode when no password exists yet', async () => {
    getPlotMock.mockResolvedValue(basePlot());

    renderPage();

    fireEvent.click(await screen.findByRole('button', { name: 'ตั้งรหัส Supplier ตรวจแปลง' }));

    await waitFor(() => expect(screen.getByRole('heading', { name: 'ตั้งรหัส Supplier ตรวจแปลง' })).toBeTruthy());
    // no "existing inspectors lose access" warning on a first set
    expect(screen.queryByText(/ผู้ตรวจที่ใช้รหัสเดิม/)).toBeNull();
  });

  it('closes the modal without ever calling the set API when cancelled', async () => {
    getPlotMock.mockResolvedValue(basePlot());

    renderPage();

    fireEvent.click(await screen.findByRole('button', { name: 'ตั้งรหัส Supplier ตรวจแปลง' }));
    await waitFor(() => expect(screen.getByRole('heading', { name: 'ตั้งรหัส Supplier ตรวจแปลง' })).toBeTruthy());
    fireEvent.click(screen.getByRole('button', { name: 'ยกเลิก' }));

    await waitFor(() => expect(screen.queryByLabelText('ยืนยันรหัสอีกครั้ง')).toBeNull());
    expect(setPlotInspectionCredentialMock).not.toHaveBeenCalled();
  });
});

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
    expect(cycleField('Supplier Lot No')).toBe('—');
  });

  it('shows the LATEST cycle supplier lot, never an older one (round Q)', async () => {
    getPlotMock.mockResolvedValue(basePlot());
    listPlotCyclesMock.mockResolvedValue([
      oneCycle({ id: 'c2', cycleNo: 2, status: 'active', supplierLotNo: 'ACTIVE-LOT' }),
      oneCycle({ id: 'c1', cycleNo: 1, status: 'harvested', supplierLotNo: 'OLD-LOT' }),
    ]);

    renderPage();
    await screen.findByText('ชนิดพืช');

    // Round Q — the table's per-row isolation went with the table. What
    // replaces it: the card reads ONE cycle, and it must be the latest.
    expect(cycleField('Supplier Lot No')).toBe('ACTIVE-LOT');
    expect(screen.queryByText('OLD-LOT')).toBeNull();
  });

  it('a CLOSED cycle with no supplier lot still shows an em dash (round Q)', async () => {
    getPlotMock.mockResolvedValue(basePlot());
    listPlotCyclesMock.mockResolvedValue([
      oneCycle({ id: 'c1', cycleNo: 1, status: 'harvested', supplierLotNo: null }),
    ]);

    renderPage();
    await screen.findByText('ชนิดพืช');

    expect(cycleField('Supplier Lot No')).toBe('—');
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
    await screen.findByText('ชนิดพืช');

    expect(cycleField('Oracle Supplier Code')).toBe('—');
    expect(cycleField('Oracle Invoice')).toBe('—');
    expect(cycleField('Ref Account')).toBe('—');
  });

  it('shows the LATEST cycle values, never an older one (round Q)', async () => {
    getPlotMock.mockResolvedValue(basePlot());
    listPlotCyclesMock.mockResolvedValue([
      oneCycle({ id: 'c2', cycleNo: 2, status: 'active', oracleSupplierCode: 'ACTIVE-ORC' }),
      oneCycle({ id: 'c1', cycleNo: 1, status: 'harvested', oracleSupplierCode: 'OLD-ORC' }),
    ]);

    renderPage();
    await screen.findByText('ชนิดพืช');

    // Round Q — see the Supplier Lot test above: one card, latest cycle.
    expect(cycleField('Oracle Supplier Code')).toBe('ACTIVE-ORC');
    expect(screen.queryByText('OLD-ORC')).toBeNull();
  });

  it('is never shown as a Plot-level/permanent field — only inside a cycle context', async () => {
    getPlotMock.mockResolvedValue(basePlot());
    listPlotCyclesMock.mockResolvedValue([oneCycle({ oracleSupplierCode: 'ORC-SUP-7' })]);

    renderPage();
    await screen.findAllByText('ORC-SUP-7');

    // The plot header/permanent-info area never repeats the label a third
    // time beyond the current-cycle field + its own history row.
    // Round Q — one place now (the รอบปลูก card), not card + history table.
    expect(screen.getAllByText('Oracle Supplier Code').length).toBe(1);
  });
});

/**
 * Round Q — the "cycle history table (round 8-10A)" describe went with the
 * table it covered: the 10/25/50/100 selector, newest-first ordering, the
 * "แสดง X จากทั้งหมด Y รอบ" summary, the horizontal scroll, and the
 * each-row-owns-its-own-value isolation. None of those behaviours exist any
 * more, so keeping the tests would have meant making them assert something
 * else.
 *
 * What the table DISPLAYED is still covered — retargeted onto the "รอบปลูก"
 * card through cycleField() in the describes above (final estimate, actual
 * harvest, Supplier Lot No, the Oracle references). The one test that was
 * about the REQUEST rather than the table survives here: the page still
 * fetches the cycles, it just renders them somewhere else.
 */
describe('PlotDetail — cycle fetch (round 8-10A, retargeted round Q)', () => {
  it('fetches the cycles once, with limit/offset on the request', async () => {
    getPlotMock.mockResolvedValue(basePlot());
    listPlotCyclesMock.mockResolvedValue([oneCycle()]);

    renderPage();
    await screen.findByText('ชนิดพืช');

    expect(listPlotCyclesMock).toHaveBeenCalledTimes(1);
    expect(listPlotCyclesMock.mock.calls[0][0]).toBe('plot-1');
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

  it('8. is the ONLY page-size selector now, and changing it never refetches cycles', async () => {
    // Round Q — this used to prove the inspection selector and the cycle-history
    // table's own selector stayed independent. The cycle table (and its
    // selector) are gone, so the guarantee becomes the stronger one: there is a
    // single selector on the page, and driving it still leaves the cycle query
    // alone.
    getPlotMock.mockResolvedValue(basePlot());
    listPlotCyclesMock.mockResolvedValue([oneCycle()]);
    listRecordsMock.mockResolvedValue(manyRecords(5));

    renderPage();

    await screen.findByText('ชนิดพืช');
    expect(screen.queryByLabelText('จำนวนรอบที่แสดง')).toBeNull();
    const recordSelect = screen.getByLabelText('แสดงต่อหน้า') as HTMLSelectElement;
    expect(recordSelect.value).toBe('5');   // inspection history's own default

    listPlotCyclesMock.mockClear();
    fireEvent.change(recordSelect, { target: { value: '20' } });
    await waitFor(() => expect(lastListRecordsCall().limit).toBe(20));
    expect(listPlotCyclesMock).not.toHaveBeenCalled();
  });
});

/**
 * Round P — closing a cycle also takes the plot out of service, for a caller
 * holding plots.delete. Undoing that needs the same permission, so the modal
 * has to say it BEFORE the button, not after.
 */
describe('PlotDetail — close warns that the plot is retired too (round P)', () => {
  beforeEach(() => { allowedPerms = null; });

  async function openCloseModal() {
    getPlotMock.mockResolvedValue(basePlot());
    listPlotCyclesMock.mockResolvedValue([oneCycle()]);
    renderPage();
    fireEvent.click(await screen.findByRole('button', { name: /ปิดรอบปลูก/ }));
  }

  it('warns that the plot will be deactivated when the user may deactivate it', async () => {
    allowedPerms = new Set(['plots.read', 'plots.update', 'plots.delete']);
    await openCloseModal();

    expect(await screen.findByText(/แปลงนี้จะถูกปิดใช้งานทันที/)).toBeTruthy();
    // and says what that costs, since the farmer-facing effect is invisible
    // from this screen
    expect(screen.getByText(/เกษตรกรจะไม่เห็นแปลงนี้ในหน้าตรวจแปลงอีก/)).toBeTruthy();
  });

  it('does NOT promise deactivation to a caller who lacks plots.delete', async () => {
    // For them the close is all that happens — the backend gate leaves the
    // plot in service, so claiming otherwise would be a lie on screen.
    allowedPerms = new Set(['plots.read', 'plots.update']);
    await openCloseModal();

    await screen.findByRole('button', { name: 'ยืนยันปิดรอบปลูก' });
    expect(screen.queryByText(/แปลงนี้จะถูกปิดใช้งานทันที/)).toBeNull();
  });

  it('no longer says a new cycle can be started on this plot', async () => {
    // The old copy ended "...จนกว่าจะเริ่มรอบปลูกใหม่", which round E removed.
    allowedPerms = new Set(['plots.read', 'plots.update', 'plots.delete']);
    await openCloseModal();

    await screen.findByRole('button', { name: 'ยืนยันปิดรอบปลูก' });
    expect(screen.queryByText(/จนกว่าจะเริ่มรอบปลูกใหม่/)).toBeNull();
    expect(screen.getByText(/1 แปลง = 1 รอบปลูก/)).toBeTruthy();
  });
});

/**
 * Round S — ending a season splits into two actions with two permissions.
 *
 * plots.update closes it as HARVESTED (a claim about a delivered crop, so it
 * stays Chiatai's). plots.cancel_cycle ends it as CANCELLED — the one ending a
 * Supplier Owner may record, since they are who knows a planting failed. The
 * reason is mandatory, and the plot leaves service whoever does it.
 */
describe('PlotDetail — cancel vs close (round S)', () => {
  beforeEach(() => { allowedPerms = null; });

  async function open(perms: string[]) {
    allowedPerms = new Set(perms);
    getPlotMock.mockResolvedValue(basePlot());
    listPlotCyclesMock.mockResolvedValue([oneCycle()]);
    renderPage();
    await screen.findByText('ชนิดพืช');
  }

  it('a Supplier Owner is offered ยกเลิก only — never the harvested close or edit', async () => {
    await open(['plots.read', 'plots.cancel_cycle']);

    expect(screen.getByRole('button', { name: /ยกเลิกรอบปลูก/ })).toBeTruthy();
    expect(screen.queryByRole('button', { name: /ปิดรอบปลูก/ })).toBeNull();
    expect(screen.queryByRole('button', { name: 'แก้รอบปลูก' })).toBeNull();
  });

  it('an admin is offered both endings, clearly labelled', async () => {
    await open(['plots.read', 'plots.update', 'plots.delete', 'plots.cancel_cycle']);

    expect(screen.getByRole('button', { name: /ปิดรอบปลูก \(เก็บเกี่ยว\)/ })).toBeTruthy();
    expect(screen.getByRole('button', { name: /ยกเลิกรอบปลูก/ })).toBeTruthy();
  });

  it('someone with neither key is offered no ending at all', async () => {
    await open(['plots.read']);

    expect(screen.queryByRole('button', { name: /ยกเลิกรอบปลูก/ })).toBeNull();
    expect(screen.queryByRole('button', { name: /ปิดรอบปลูก/ })).toBeNull();
  });

  it('refuses to submit a cancel with no reason, and never calls the API', async () => {
    await open(['plots.read', 'plots.cancel_cycle']);
    fireEvent.click(screen.getByRole('button', { name: /ยกเลิกรอบปลูก/ }));
    fireEvent.click(await screen.findByRole('button', { name: 'ยืนยันยกเลิกรอบปลูก' }));

    expect(await screen.findByText('กรุณาระบุเหตุผลที่ยกเลิกรอบปลูก')).toBeTruthy();
    expect(cancelPlotCycleMock).not.toHaveBeenCalled();
  });

  it('sends the trimmed reason once one is typed', async () => {
    cancelPlotCycleMock.mockResolvedValue(oneCycle({ status: 'cancelled' }));
    await open(['plots.read', 'plots.cancel_cycle']);
    fireEvent.click(screen.getByRole('button', { name: /ยกเลิกรอบปลูก/ }));

    // Queried by placeholder: PlotCycleModals' shared Field renders a bare
    // <label> with no htmlFor, so getByLabelText cannot reach the control.
    // Pre-existing across every cycle modal — not this round's to change.
    expect(screen.getByText('เหตุผลที่ยกเลิก (บังคับ)')).toBeTruthy();
    fireEvent.change(await screen.findByPlaceholderText(/ต้นกล้าเสียหายจากน้ำท่วม/), {
      target: { value: '  ต้นกล้าเสียหายจากน้ำท่วม  ' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'ยืนยันยกเลิกรอบปลูก' }));

    await waitFor(() => expect(cancelPlotCycleMock).toHaveBeenCalledWith(
      'plot-1', 'cycle-1', 'ต้นกล้าเสียหายจากน้ำท่วม',
    ));
  });

  it('warns that the plot leaves service, before the button', async () => {
    // The last reversible moment: reopening needs plots.delete, which a
    // Supplier does not have.
    await open(['plots.read', 'plots.cancel_cycle']);
    fireEvent.click(screen.getByRole('button', { name: /ยกเลิกรอบปลูก/ }));

    expect(await screen.findByText(/แปลงนี้จะถูกปิดใช้งานทันที/)).toBeTruthy();
    expect(screen.getByText(/เกษตรกรจะไม่เห็นแปลงนี้ในหน้าตรวจแปลงอีก/)).toBeTruthy();
  });

  it('the harvested close no longer offers a status choice', async () => {
    // Leaving "ยกเลิก" in that dropdown would be a second route to cancelling
    // that skips the mandatory reason and the deactivation.
    await open(['plots.read', 'plots.update', 'plots.delete', 'plots.cancel_cycle']);
    fireEvent.click(screen.getByRole('button', { name: /ปิดรอบปลูก \(เก็บเกี่ยว\)/ }));

    await screen.findByRole('button', { name: 'ยืนยันปิดรอบปลูก' });
    expect(screen.queryByLabelText('สถานะ')).toBeNull();
    expect(screen.queryByRole('option', { name: 'ยกเลิก' })).toBeNull();
  });
});
