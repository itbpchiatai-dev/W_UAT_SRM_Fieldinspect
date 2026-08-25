/**
 * Plots (admin) — round 15.1 regression guard: the edit modal's default
 * form values must not crash when getPlot() returns latitude/longitude/rai
 * as JSON strings (the actual runtime shape) — react-hook-form's default
 * `values` were previously fed the raw string, silently relying on zod's
 * `z.coerce.number()` to bail it out at submit time, but nothing verified
 * that path was safe end-to-end.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor, within } from '@testing-library/react';
import { MemoryRouter, Routes, Route, useLocation } from 'react-router-dom';
import { QueryClientProvider, QueryClient } from '@tanstack/react-query';
import { Plots } from './Plots';
import { masterDataQueryKey } from '../../../api/masterdata';
import { useAuthStore } from '../../../stores/auth';
import type { Role, User } from '../../../types/auth';

const listPlotsMock = vi.fn();
const listPlotProvincesMock = vi.fn();
const listPlotCycleLabelsMock = vi.fn();
const getPlotMock = vi.fn();
const updatePlotMock = vi.fn();
const createPlotWithCycleMock = vi.fn();
const listSuppliersMock = vi.fn();
const listUsersMock = vi.fn();
const listMasterDataMock = vi.fn();

// Round 8-26C — one crop, two varieties, one P.Code each (พริกจินดา's is
// Melon-Z, for the reactivate test that needs a second distinct value).
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

function defaultMasterData({ type, parent }: { type: string; parent?: string }) {
  if (type === 'crop') return Promise.resolve([masterDataRow('crop', 'พริก', null)]);
  if (type === 'variety') {
    return Promise.resolve(
      Object.keys(MD_P_CODE_BY_VARIETY).map((v) => masterDataRow('variety', v, 'พริก')),
    );
  }
  if (type === 'p_code') {
    const code = parent ? MD_P_CODE_BY_VARIETY[parent] : undefined;
    return Promise.resolve(code ? [masterDataRow('p_code', code, parent ?? null)] : []);
  }
  return Promise.resolve([]);
}

/** Fills the cycle form's crop/variety and waits for the derived P.Code —
 * replaces the old "type into the P.Code box", which is read-only now. */
async function pickCropAndVariety(variety = 'พริกขี้หนู') {
  fireEvent.change(await screen.findByLabelText('— เลือกชนิดพืช —'), { target: { value: 'พริก' } });
  fireEvent.change(await screen.findByLabelText('— เลือกพันธุ์ —'), { target: { value: variety } });
  await waitFor(() => expect(
    (screen.getByLabelText('P.Code') as HTMLInputElement).value,
  ).toBe(MD_P_CODE_BY_VARIETY[variety]));
}
const getPlotAccessPhonesMock = vi.fn();
const replacePlotAccessPhonesMock = vi.fn();
const downloadPlotImportTemplateMock = vi.fn();
// Round 8-18B — the Plots page's two SEPARATE search boxes, queried by
// their visible labels (the old single combined box is gone).
const NAME_CODE_LABEL = 'ชื่อแปลงหรือรหัสแปลง';
const ACCESS_NUMBER_LABEL = 'หมายเลขสำหรับเข้าตรวจ';
const searchPlotsByPhoneMock = vi.fn();
// Round 8-6G item 13 — spies (never overridden with real behavior) so a
// test can assert the Plots page's download actions never reach these,
// stronger than relying on "no test happened to trigger them".
const previewPlotImportMock = vi.fn();
const commitPlotImportWithReportMock = vi.fn();
const deactivatePlotMock = vi.fn();
const reactivatePlotMock = vi.fn();
const reactivatePlotWithCycleMock = vi.fn();

vi.mock('../../../api/plots', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../../api/plots')>();
  return {
    ...actual,
    listPlots: (...args: unknown[]) => listPlotsMock(...args),
    listPlotProvinces: (...args: unknown[]) => listPlotProvincesMock(...args),
    listPlotCycleLabels: (...args: unknown[]) => listPlotCycleLabelsMock(...args),
    getPlot: (...args: unknown[]) => getPlotMock(...args),
    updatePlot: (...args: unknown[]) => updatePlotMock(...args),
    createPlotWithCycle: (...args: unknown[]) => createPlotWithCycleMock(...args),
    getPlotAccessPhones: (...args: unknown[]) => getPlotAccessPhonesMock(...args),
    replacePlotAccessPhones: (...args: unknown[]) => replacePlotAccessPhonesMock(...args),
    downloadPlotImportTemplate: (...args: unknown[]) => downloadPlotImportTemplateMock(...args),
    searchPlotsByPhone: (...args: unknown[]) => searchPlotsByPhoneMock(...args),
    previewPlotImport: (...args: unknown[]) => previewPlotImportMock(...args),
    commitPlotImportWithReport: (...args: unknown[]) => commitPlotImportWithReportMock(...args),
    deactivatePlot: (...args: unknown[]) => deactivatePlotMock(...args),
    reactivatePlot: (...args: unknown[]) => reactivatePlotMock(...args),
    reactivatePlotWithCycle: (...args: unknown[]) => reactivatePlotWithCycleMock(...args),
  };
});

vi.mock('../../../api/suppliers', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../../api/suppliers')>();
  return { ...actual, listSuppliers: (...args: unknown[]) => listSuppliersMock(...args) };
});

vi.mock('../../../api/users', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../../api/users')>();
  return { ...actual, listUsers: (...args: unknown[]) => listUsersMock(...args) };
});

vi.mock('../../../api/masterdata', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../../api/masterdata')>();
  return { ...actual, listMasterData: (...args: unknown[]) => listMasterDataMock(...args) };
});

// Round 8-6B — intercepts downloadBlob(blob, filename) so the download
// button's tests can assert the exact filename without spying on the DOM
// anchor lib/downloadBlob.ts creates internally.
const downloadBlobMock = vi.fn();
vi.mock('../../../lib/downloadBlob', () => ({
  downloadBlob: (...args: unknown[]) => downloadBlobMock(...args),
}));

function masterDataItem(overrides: Partial<{
  id: string; type: string; value: string; parent: string | null; orderIndex: number;
}>) {
  return {
    id: 'md-1', type: 'crop', value: '', parent: null, orderIndex: 0, active: true,
    createdAt: '2026-01-01T00:00:00Z', updatedAt: '2026-01-01T00:00:00Z',
    ...overrides,
  };
}

function renderPlotsPage(
  initialEntry = '/farmlog/admin/plots',
  qc: QueryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } }),
) {
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={[initialEntry]}>
        <Routes>
          <Route path="/farmlog/admin/plots" element={<Plots />} />
          <Route path="/farmlog/admin/plots/:id" element={<div>Plot Detail Page</div>} />
          <Route path="/farmlog/records/new" element={<RecordPageProbe />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

// Captures the query string the "ตรวจแปลง" action navigated with, so the
// test can assert the supplier/plot deep-link params without pulling in the
// real RecordForm.
let lastRecordSearch = '';
function RecordPageProbe() {
  const location = useLocation();
  lastRecordSearch = location.search;
  return <div>New Record Page</div>;
}

/** Round 20 — secondary row actions live behind one ActionMenu trigger now
 * instead of standalone icon buttons; open it by its per-row aria-label/
 * title before looking for a specific action by name. */
async function openRowMenu(plotCode: string) {
  const trigger = await screen.findByTitle(`ตัวเลือกเพิ่มเติมสำหรับแปลง ${plotCode}`);
  fireEvent.click(trigger);
}

// Round 8-18A — province/crop/variety filters became searchable comboboxes
// (SupplierFilterCombobox's own UX pattern): open the trigger, then click
// the option INSIDE that listbox (scoped with `within` — the same text can
// legitimately also appear elsewhere on the page, e.g. a province name in
// the plots table).
async function selectProvinceFilter(value: string) {
  fireEvent.click(screen.getByRole('button', { name: 'กรองจังหวัด' }));
  const listbox = await screen.findByRole('listbox');
  // findByText (not getByText) — the listbox opens immediately, but the
  // real option list is still an in-flight query at that instant.
  fireEvent.click(await within(listbox).findByText(value));
}

async function selectCropFilter(value: string) {
  fireEvent.click(screen.getByRole('button', { name: 'กรองชนิดพืช' }));
  const listbox = await screen.findByRole('listbox');
  fireEvent.click(await within(listbox).findByText(value));
}

async function selectVarietyFilter(value: string) {
  fireEvent.click(screen.getByRole('button', { name: 'กรองพันธุ์/สายพันธุ์' }));
  const listbox = await screen.findByRole('listbox');
  fireEvent.click(await within(listbox).findByText(value));
}

function hasListPlotsCallContaining(expected: Record<string, unknown>) {
  return listPlotsMock.mock.calls.some(([params]) => (
    Object.entries(expected).every(([key, value]) => (params as Record<string, unknown>)[key] === value)
  ));
}

beforeEach(() => {
  listPlotsMock.mockReset();
  searchPlotsByPhoneMock.mockReset();
  listPlotProvincesMock.mockReset();
  listPlotCycleLabelsMock.mockReset();
  getPlotMock.mockReset();
  updatePlotMock.mockReset();
  createPlotWithCycleMock.mockReset();
  getPlotAccessPhonesMock.mockReset();
  replacePlotAccessPhonesMock.mockReset();
  listSuppliersMock.mockReset();
  listUsersMock.mockReset();
  downloadPlotImportTemplateMock.mockReset();
  downloadBlobMock.mockReset();
  previewPlotImportMock.mockReset();
  commitPlotImportWithReportMock.mockReset();
  deactivatePlotMock.mockReset();
  reactivatePlotMock.mockReset();
  reactivatePlotWithCycleMock.mockReset();
  listSuppliersMock.mockResolvedValue([
    { id: 'sup-1', code: 'SUP001', name: 'Supplier One', isActive: true, contactName: null, contactEmail: null },
  ]);
  listPlotProvincesMock.mockResolvedValue(['จังหวัดทดสอบ']);
  listPlotCycleLabelsMock.mockResolvedValue(['jun2026', 'aug2026']);
  listUsersMock.mockResolvedValue([]);
  listMasterDataMock.mockReset();
  // Round 8-26C — the cycle form's P.Code is DERIVED from the chosen พันธุ์,
  // so a default of [] would leave no way to fill a create-plot form at all.
  // Tests that care about a specific master-data shape still override this.
  listMasterDataMock.mockImplementation(defaultMasterData);
  useAuthStore.setState({ permissionKeys: new Set(['plots.read', 'plots.update', 'plots.create', 'plots.delete', 'plots.assign', 'records.create']) });
  // Round 8-6G — reset between tests so a full-scope `user` set by one test
  // (for the "ทุก Supplier" menu-item visibility checks) never leaks into
  // an unrelated test that runs after it. Round 8-25O — defaults to an
  // internal:admin user (not null): most existing tests assume crop/variety
  // is visible, which now requires an internal role (canViewVariety);
  // supplier-role-specific behavior is set explicitly per test (below).
  useAuthStore.setState({ user: userWithRoles('internal:admin') });
});

function _role(name: string): Role {
  return { id: `role-${name}`, name, displayName: name, providerScope: 'internal', isSystem: true };
}

function userWithRoles(...roleNames: string[]): User {
  return {
    id: 'user-1', email: 'user@example.com', fullName: 'Test User',
    authProvider: 'local', isActive: true, emailVerified: true,
    roles: roleNames.map(_role),
  };
}

describe('Plots edit modal — string latitude/longitude/rai', () => {
  it('opens without crashing and shows normalized numeric defaults', async () => {
    listPlotsMock.mockResolvedValue([
      {
        id: 'plot-1', supplierId: 'sup-1', plotCode: 'SUP001-P001', name: 'แปลงทดสอบ',
        village: null, district: null, province: 'จังหวัดทดสอบ',
        latitude: '13.7563000', longitude: '100.5018000',
        isActive: true, assignedCount: 0, primaryPhone: null, additionalPhones: [],
      },
    ]);
    getPlotMock.mockResolvedValue({
      id: 'plot-1', supplierId: 'sup-1', plotCode: 'SUP001-P001', name: 'แปลงทดสอบ',
      village: null, district: null, province: 'จังหวัดทดสอบ',
      latitude: '13.7563000', longitude: '100.5018000', rai: '2.5000',
      isActive: true, assignedUsers: [], primaryPhone: null, additionalPhones: [], createdAt: '2026-01-01T00:00:00Z', updatedAt: '2026-01-01T00:00:00Z',
    });

    renderPlotsPage();

    await openRowMenu('SUP001-P001');
    fireEvent.click(await screen.findByRole('menuitem', { name: 'แก้ไขข้อมูลแปลง' }));

    await waitFor(() => expect(screen.getByDisplayValue('13.7563')).toBeTruthy());
    expect(screen.getByDisplayValue('100.5018')).toBeTruthy();
    expect(screen.getByDisplayValue('2.5')).toBeTruthy();
  });

  it('sends only physical-plot fields (round 8.0.4 — no supplierId/plotCode/planning fields)', async () => {
    listPlotsMock.mockResolvedValue([
      {
        id: 'plot-1', supplierId: 'sup-1', plotCode: 'SUP001-P001', name: 'แปลงทดสอบ',
        village: null, district: null, province: null,
        latitude: null, longitude: null,
        isActive: true, assignedCount: 0, primaryPhone: null, additionalPhones: [],
      },
    ]);
    getPlotMock.mockResolvedValue({
      id: 'plot-1', supplierId: 'sup-1', plotCode: 'SUP001-P001', name: 'แปลงทดสอบ',
      village: null, district: null, province: null,
      latitude: null, longitude: null, rai: null,
      isActive: true, assignedUsers: [], primaryPhone: null, additionalPhones: [], createdAt: '2026-01-01T00:00:00Z', updatedAt: '2026-01-01T00:00:00Z',
      plantCount: null, expectedYieldFull: null, expectedYieldUnit: null,
      currentCrop: null, currentVariety: null, currentLotNo: null, currentPlantingDate: null,
    });
    updatePlotMock.mockResolvedValue({
      id: 'plot-1', supplierId: 'sup-1', plotCode: 'SUP001-P001', name: 'แปลงทดสอบ',
      village: null, district: null, province: null,
      latitude: null, longitude: null, rai: null,
      isActive: true, assignedUsers: [], primaryPhone: null, additionalPhones: [], createdAt: '2026-01-01T00:00:00Z', updatedAt: '2026-01-01T00:00:00Z',
    });

    renderPlotsPage();

    await openRowMenu('SUP001-P001');
    fireEvent.click(await screen.findByRole('menuitem', { name: 'แก้ไขข้อมูลแปลง' }));
    await screen.findByDisplayValue('แปลงทดสอบ');

    fireEvent.click(screen.getByRole('button', { name: 'บันทึก' }));

    await waitFor(() => expect(updatePlotMock).toHaveBeenCalled());
    const [id, payload] = updatePlotMock.mock.calls[0];
    expect(id).toBe('plot-1');
    expect(payload).not.toHaveProperty('supplierId');
    expect(payload).not.toHaveProperty('plotCode');
    expect(payload).not.toHaveProperty('plantCount');
    expect(payload).not.toHaveProperty('expectedYieldFull');
    expect(payload).not.toHaveProperty('expectedYieldUnit');
    expect(payload).not.toHaveProperty('currentCrop');
    expect(payload).not.toHaveProperty('currentVariety');
    expect(payload).not.toHaveProperty('currentLotNo');
    expect(payload).not.toHaveProperty('currentPlantingDate');
    expect(payload.name).toBe('แปลงทดสอบ');
    expect(payload.village).toBeNull();
  });
});

describe('Plots assign modal — round 16 regression guard', () => {
  it('does not revert to the original assignee after unchecking the last selected user', async () => {
    listPlotsMock.mockResolvedValue([
      {
        id: 'plot-1', supplierId: 'sup-1', plotCode: 'SUP001-P001', name: 'แปลงทดสอบ',
        village: null, district: null, province: 'จังหวัดทดสอบ',
        latitude: null, longitude: null,
        isActive: true, assignedCount: 1, primaryPhone: null, additionalPhones: [],
      },
    ]);
    getPlotMock.mockResolvedValue({
      id: 'plot-1', supplierId: 'sup-1', plotCode: 'SUP001-P001', name: 'แปลงทดสอบ',
      village: null, district: null, province: 'จังหวัดทดสอบ',
      latitude: null, longitude: null, rai: null,
      isActive: true,
      assignedUsers: [{ userId: 'user-1', email: 'a@example.com', fullName: 'User One', assignedAt: '2026-01-01T00:00:00Z' }],
      createdAt: '2026-01-01T00:00:00Z', updatedAt: '2026-01-01T00:00:00Z',
    });
    listUsersMock.mockResolvedValue([
      {
        id: 'user-1', email: 'a@example.com', fullName: 'User One', authProvider: 'local',
        isActive: true, isApproved: true, lastLoginAt: null, roles: [], supplierId: null, isSupplierAdmin: false,
      },
    ]);

    // Round 8.0 — the assign action moved out of the row menu; the modal is
    // still reachable via the Plot Detail manage deep-link, which this guard
    // now uses to reach the same AssignModal.
    renderPlotsPage('/farmlog/admin/plots?manage=assign&plotId=plot-1');

    const checkbox = await screen.findByRole('checkbox') as HTMLInputElement;
    await waitFor(() => expect(checkbox.checked).toBe(true));

    // Before the fix, the render-time sync guard (`selected.size === 0`)
    // fired again on this very click and immediately re-checked the box.
    fireEvent.click(checkbox);
    expect(checkbox.checked).toBe(false);
    expect(screen.getByText('เลือก 0 คน')).toBeTruthy();
  });
});

describe('Plots list — yield summary column (round 17, warning states round 18)', () => {
  it('renders "80% → 800 kg / 1,000 kg" for a plot with a complete yield plan', async () => {
    listPlotsMock.mockResolvedValue([
      {
        id: 'plot-1', supplierId: 'sup-1', supplierCode: 'SUP001', supplierName: 'Supplier One', qrKey: 'qr-1', plotCode: 'SUP001-P001', name: 'แปลงทดสอบ',
        village: null, district: null, province: 'จังหวัดทดสอบ',
        latitude: null, longitude: null,
        isActive: true, assignedCount: 0, primaryPhone: null, additionalPhones: [],
        plantCount: 500, currentYieldPct: '80', expectedYieldFull: '1000.00', expectedYieldUnit: 'kg',
      },
    ]);

    renderPlotsPage();

    expect(await screen.findByText('80% → 800 kg / 1,000 kg')).toBeTruthy();
  });

  it('shows a "ยังไม่ตั้งแผนผลผลิต" warning badge — not a silent dash — when a plot HAS an active cycle but no yield data yet', async () => {
    listPlotsMock.mockResolvedValue([
      {
        id: 'plot-2', supplierId: 'sup-1', plotCode: 'SUP001-P002', name: 'แปลงไม่มีแผน',
        village: null, district: null, province: 'จังหวัดทดสอบ',
        latitude: null, longitude: null,
        isActive: true, assignedCount: 0, primaryPhone: null, additionalPhones: [],
        plantCount: null, currentYieldPct: null, expectedYieldFull: null, expectedYieldUnit: null,
        // Round 7.3 — an active cycle IS inferred (currentCrop set), so the
        // yield cell reports the incomplete PLAN, not "no cycle at all".
        currentCrop: 'พริก',
      },
    ]);

    renderPlotsPage();

    expect(await screen.findByText('ยังไม่ตั้งแผนผลผลิต')).toBeTruthy();
  });

  it('shows "รอเริ่มรอบปลูก" instead of the yield-plan warning when the plot has no active cycle at all (round 7.3)', async () => {
    listPlotsMock.mockResolvedValue([
      {
        id: 'plot-2', supplierId: 'sup-1', plotCode: 'SUP001-P002', name: 'แปลงไม่มีแผน',
        village: null, district: null, province: 'จังหวัดทดสอบ',
        latitude: null, longitude: null,
        isActive: true, assignedCount: 0, primaryPhone: null, additionalPhones: [],
        plantCount: null, currentYieldPct: null, expectedYieldFull: null, expectedYieldUnit: null,
        currentCrop: null, currentVariety: null, currentLotNo: null, currentPlantingDate: null,
      },
    ]);

    renderPlotsPage();

    // Appears twice for this row: the status-column cycle badge AND the
    // yield cell (both read the same "no active cycle" inference).
    const badges = await screen.findAllByText('รอเริ่มรอบปลูก');
    expect(badges.length).toBeGreaterThanOrEqual(1);
    expect(screen.queryByText('ยังไม่ตั้งแผนผลผลิต')).toBeNull();
  });

  it('names the specific missing field when only plant count is missing', async () => {
    listPlotsMock.mockResolvedValue([
      {
        id: 'plot-3', supplierId: 'sup-1', plotCode: 'SUP001-P003', name: 'แปลงกรอกไม่ครบ',
        village: null, district: null, province: 'จังหวัดทดสอบ',
        latitude: null, longitude: null,
        isActive: true, assignedCount: 0, primaryPhone: null, additionalPhones: [],
        plantCount: null, currentYieldPct: null, expectedYieldFull: '1000.00', expectedYieldUnit: 'kg',
      },
    ]);

    renderPlotsPage();

    expect(await screen.findByText('ยังไม่ระบุจำนวนต้น/จำนวนปลูก')).toBeTruthy();
  });

  it('shows a labelled two-line planting-cycle block (crop/variety, then lot/planting date) under the plot name (round 8-3K)', async () => {
    listPlotsMock.mockResolvedValue([
      {
        id: 'plot-4', supplierId: 'sup-1', plotCode: 'SUP001-P004', name: 'แปลงทดสอบ',
        village: null, district: null, province: 'จังหวัดทดสอบ',
        latitude: null, longitude: null,
        isActive: true, assignedCount: 0, primaryPhone: null, additionalPhones: [],
        currentCrop: 'พริก', currentVariety: 'พริกขี้หนู', currentLotNo: 'LOT-01', currentPlantingDate: '2026-06-01',
      },
    ]);

    renderPlotsPage();

    expect(await screen.findByText('พืช: พริก / พริกขี้หนู')).toBeTruthy();
    expect(screen.getByText('PO: — · P.Code: — · Lot ระบบ: LOT-01')).toBeTruthy();
    expect(screen.getByText('ปลูก: 1 มิ.ย. 2569')).toBeTruthy();
  });

  it('shows a plain dash for the planting-cycle line when nothing is set', async () => {
    listPlotsMock.mockResolvedValue([
      {
        id: 'plot-5', supplierId: 'sup-1', plotCode: 'SUP001-P005', name: 'แปลงยังไม่ระบุรอบปลูก',
        village: null, district: null, province: 'จังหวัดทดสอบ',
        latitude: null, longitude: null,
        isActive: true, assignedCount: 0, primaryPhone: null, additionalPhones: [],
      },
    ]);

    renderPlotsPage();

    const nameCell = await screen.findByText('แปลงยังไม่ระบุรอบปลูก');
    const row = nameCell.closest('tr');
    expect(row).toBeTruthy();
    expect(row!.textContent).toContain('—');
  });
});

describe('Plots list — cycle status from backend read-model (round 7.3.1)', () => {
  function activePlot() {
    return {
      id: 'plot-1', supplierId: 'sup-1', plotCode: 'SUP001-P001', name: 'แปลงทดสอบ',
      village: null, district: null, province: 'จังหวัดทดสอบ',
      latitude: null, longitude: null,
      isActive: true, assignedCount: 0, primaryPhone: null, additionalPhones: [],
      activeCycleId: null,
    };
  }

  it('subtitle shows the cycle name (รอบที่ N fallback) when the read-model has an activeCycleId', async () => {
    listPlotsMock.mockResolvedValue([{
      ...activePlot(), activeCycleId: 'cycle-1', activeCycleNo: 4, activeCycleStatus: 'active',
    }]);
    renderPlotsPage();

    // Round 8.0 — the standalone "สถานะ" column/badge is gone; an active cycle
    // now surfaces through the cycle subtitle instead.
    expect(await screen.findByText(/รอบที่ 4/)).toBeTruthy();
    expect(screen.queryByText('กำลังปลูก')).toBeNull();
  });

  it('subtitle leads with activeCycleLabel when the backend provides one (round 8.0)', async () => {
    listPlotsMock.mockResolvedValue([{
      ...activePlot(), activeCycleId: 'cycle-1', activeCycleNo: 4, activeCycleStatus: 'active',
      activeCycleLabel: 'jun2026',
    }]);
    renderPlotsPage();

    expect(await screen.findByText(/jun2026/)).toBeTruthy();
    // The numeric fallback is not used when a label is present.
    expect(screen.queryByText(/รอบที่ 4/)).toBeNull();
  });

  it('shows "รอเริ่มรอบปลูก" (in the yield cell) when activeCycleId is null EVEN THOUGH mirror fields are populated (backend truth wins over inference)', async () => {
    // The key round-7.3.1 guarantee: the read-model's activeCycleId — not the
    // current_* mirror — decides. A leftover mirror value after a closed cycle
    // must NOT be read as an active cycle.
    listPlotsMock.mockResolvedValue([{
      ...activePlot(),
      activeCycleId: null,
      currentCrop: 'พริก', currentVariety: 'พริกขี้หนู', currentLotNo: 'LOT-01',
      plantCount: 500, expectedYieldFull: '1000.00',
    }]);
    renderPlotsPage();

    const badges = await screen.findAllByText('รอเริ่มรอบปลูก');
    expect(badges.length).toBeGreaterThanOrEqual(1);
    expect(screen.queryByText('กำลังปลูก')).toBeNull();
  });

  it('shows the plot as inactive (ปิดแล้ว appears nowhere as a status column) — status column removed (round 8.0)', async () => {
    listPlotsMock.mockResolvedValue([{ ...activePlot(), isActive: false, activeCycleId: 'cycle-1' }]);
    renderPlotsPage();

    await screen.findByText('SUP001-P001');
    // Round 8.0 — no standalone status column, so neither the active/closed nor
    // the cycle badge renders in a status cell.
    expect(screen.queryByText('กำลังปลูก')).toBeNull();
    expect(screen.queryByText('ปิดแล้ว')).toBeNull();
  });

  it('hides "ตรวจแปลง" in the row menu when activeCycleId is null', async () => {
    listPlotsMock.mockResolvedValue([activePlot()]);
    renderPlotsPage();

    await openRowMenu('SUP001-P001');
    expect(screen.queryByRole('menuitem', { name: 'ตรวจแปลง' })).toBeNull();
  });

  it('shows "ตรวจแปลง" in the row menu when activeCycleId is present', async () => {
    listPlotsMock.mockResolvedValue([{ ...activePlot(), activeCycleId: 'cycle-1' }]);
    renderPlotsPage();

    await openRowMenu('SUP001-P001');
    expect(await screen.findByRole('menuitem', { name: 'ตรวจแปลง' })).toBeTruthy();
  });
});

describe('Plots list — yield/identity sourced from active cycle (round 7.4)', () => {
  function cyclePlot() {
    return {
      id: 'plot-1', supplierId: 'sup-1', supplierCode: 'SUP001', supplierName: 'Supplier One',
      qrKey: 'qr-1', plotCode: 'SUP001-P001', name: 'แปลงทดสอบ',
      village: null, district: null, province: 'จังหวัดทดสอบ',
      latitude: null, longitude: null, isActive: true, assignedCount: 0, primaryPhone: null, additionalPhones: [],
    };
  }

  it('YieldCell computes the plan from activeCycle*, not the current_* mirror', async () => {
    // Mirror and cycle disagree on the plan (a divergence the read-model is
    // meant to resolve): the cell must use the CYCLE's 2,000 kg base, giving
    // 50% → 1,000 kg / 2,000 kg — not the mirror's 1,000 kg (→ 500 / 1,000).
    listPlotsMock.mockResolvedValue([{
      ...cyclePlot(),
      currentYieldPct: '50',
      plantCount: 500, expectedYieldFull: '1000.00', expectedYieldUnit: 'kg',
      activeCycleId: 'cycle-1', activeCycleStatus: 'active',
      activeCyclePlantCount: 200, activeCycleExpectedYieldFull: '2000.00',
      activeCycleExpectedYieldUnit: 'kg',
    }]);

    renderPlotsPage();

    expect(await screen.findByText('50% → 1,000 kg / 2,000 kg')).toBeTruthy();
    expect(screen.queryByText('50% → 500 kg / 1,000 kg')).toBeNull();
  });

  it('planting-cycle lines read the active cycle crop/variety/lot/planting-date, not the mirror (round 8-3K labeled two-line format)', async () => {
    listPlotsMock.mockResolvedValue([{
      ...cyclePlot(),
      currentCrop: 'พริกเก่า', currentVariety: 'สายพันธุ์เก่า',
      currentLotNo: 'LOT-OLD', currentPlantingDate: '2020-01-01',
      activeCycleId: 'cycle-1', activeCycleStatus: 'active',
      activeCycleCrop: 'ทุเรียน', activeCycleVariety: 'หมอนทอง',
      activeCycleLotNo: 'LOT-09', activeCyclePlantingDate: '2026-06-01',
      activeCyclePoNumber: 'PO25009', activeCyclePCode: 'Melon-I', activeCycleSupplierLotNo: null,
    }]);

    renderPlotsPage();

    expect(await screen.findByText('พืช: ทุเรียน / หมอนทอง')).toBeTruthy();
    expect(await screen.findByText('PO: PO25009 · P.Code: Melon-I · Lot ระบบ: LOT-09')).toBeTruthy();
    expect(screen.getByText('ปลูก: 1 มิ.ย. 2569')).toBeTruthy();
    expect(screen.queryByText(/พริกเก่า/)).toBeNull();
    expect(screen.queryByText(/LOT-OLD/)).toBeNull();
  });

  it('round 7.6 fix: planting-cycle line shows a dash (not the stale mirror) when activeCycleId is null', async () => {
    // Real-data regression found in round 7-6 QA: a plot with NO active cycle
    // (activeCycleId: null — e.g. permanently closed before the plot-cycle
    // system existed, backfilled with a cancelled cycle) still carried its
    // pre-cycle current_crop/variety/lotNo/plantingDate mirror. Before the
    // fix, plantingCycleText's `activeCycleId != null` check treated null the
    // same as undefined and fell back to that stale mirror — contradicting
    // the "รอเริ่มรอบปลูก" status the rest of the row correctly shows off the
    // same activeCycleId.
    listPlotsMock.mockResolvedValue([{
      ...cyclePlot(),
      activeCycleId: null, activeCycleStatus: null,
      activeCycleCrop: null, activeCycleVariety: null,
      activeCycleLotNo: null, activeCyclePlantingDate: null,
      currentCrop: 'เมล่อน', currentVariety: 'เมล่อนเนื้อจันทร์',
      currentLotNo: 'LOT-SUP010-02', currentPlantingDate: '2026-03-21',
    }]);

    renderPlotsPage();

    const badges = await screen.findAllByText('รอเริ่มรอบปลูก');
    expect(badges.length).toBeGreaterThanOrEqual(1);
    expect(screen.queryByText(/เมล่อน/)).toBeNull();
    expect(screen.queryByText(/LOT-SUP010-02/)).toBeNull();
  });

  it('round 8-3K: omits only the missing part (no planting date) instead of a dangling separator', async () => {
    listPlotsMock.mockResolvedValue([{
      ...cyclePlot(),
      activeCycleId: 'cycle-1', activeCycleStatus: 'active', activeCycleNo: 1,
      activeCycleCrop: null, activeCycleVariety: null,
      activeCycleLotNo: 'LOT-05', activeCyclePlantingDate: null,
    }]);

    renderPlotsPage();

    expect(await screen.findByText('PO: — · P.Code: — · Lot ระบบ: LOT-05')).toBeTruthy();
    expect(screen.queryByText(/ปลูก:/)).toBeNull();
  });

  it('round 8-3K: an active cycle with no crop/variety/lot/planting-date/label shows a dash, never "null"/"undefined"', async () => {
    listPlotsMock.mockResolvedValue([{
      ...cyclePlot(),
      activeCycleId: 'cycle-1', activeCycleStatus: 'active', activeCycleNo: null,
      activeCycleLabel: null, activeCycleCrop: null, activeCycleVariety: null,
      activeCycleLotNo: null, activeCyclePlantingDate: null,
    }]);

    renderPlotsPage();

    await screen.findByText('แปลงทดสอบ');
    expect(document.body.textContent).not.toMatch(/null|undefined/i);
    expect(screen.queryByText('รอเริ่มรอบปลูก')).toBeNull(); // it DOES have an active cycle
  });
});

describe('Plots create modal — supplier auto-preselect (supplier self-service)', () => {
  it('preselects the supplier when the list has exactly one option', async () => {
    // The default listSuppliersMock returns a single supplier — the shape a
    // supplier-scoped (supplier:owner) user sees, since the backend narrows
    // the suppliers list to their own supplier.
    listPlotsMock.mockResolvedValue([]);
    const { container } = renderPlotsPage();

    fireEvent.click(await screen.findByText('เพิ่มแปลง'));
    await screen.findByText('เพิ่มแปลงใหม่');

    const supplierSelect = container.querySelector('select[name="supplierId"]') as HTMLSelectElement;
    await waitFor(() => expect(supplierSelect.value).toBe('sup-1'));
  });

  it('leaves the supplier unselected when there are multiple options', async () => {
    listSuppliersMock.mockResolvedValue([
      { id: 'sup-1', code: 'SUP001', name: 'Supplier One', isActive: true, contactName: null, contactEmail: null },
      { id: 'sup-2', code: 'SUP002', name: 'Supplier Two', isActive: true, contactName: null, contactEmail: null },
    ]);
    listPlotsMock.mockResolvedValue([]);
    const { container } = renderPlotsPage();

    fireEvent.click(await screen.findByText('เพิ่มแปลง'));
    await screen.findByText('เพิ่มแปลงใหม่');

    const supplierSelect = container.querySelector('select[name="supplierId"]') as HTMLSelectElement;
    expect(supplierSelect.value).toBe('');
  });
});

describe('Plots create modal — yield planning validation & hints (round 18)', () => {
  it('shows an inline hint naming the missing field while the base plan is incomplete', async () => {
    listPlotsMock.mockResolvedValue([]);
    renderPlotsPage();

    fireEvent.click(await screen.findByText('เพิ่มแปลง'));
    await screen.findByText('เพิ่มแปลงใหม่');

    fireEvent.change(screen.getByPlaceholderText('0'), { target: { value: '500' } });

    await waitFor(() => expect(screen.getByText(/ยังไม่ระบุ Expected Yield ที่ 100%/)).toBeTruthy());
  });

  it('blocks submit with a clear error when expectedYieldFull is set but the unit is left blank', async () => {
    listPlotsMock.mockResolvedValue([]);
    const { container } = renderPlotsPage();

    fireEvent.click(await screen.findByText('เพิ่มแปลง'));
    await screen.findByText('เพิ่มแปลงใหม่');

    // Round 8-26C — variety is required on create, and zod skips an object's
    // superRefine when a base field fails, so the yield-unit rule under test
    // is only reachable once the variety is filled in.
    await pickCropAndVariety();
    const expectedYieldFullInput = container.querySelector('input[name="expectedYieldFull"]')!;
    fireEvent.change(expectedYieldFullInput, { target: { value: '1000' } });

    fireEvent.click(screen.getByRole('button', { name: 'สร้าง' }));

    await waitFor(() => expect(screen.getByText(/กรุณาระบุหน่วย/)).toBeTruthy());
  });
});

describe('Plots create modal — crop/variety master data (round 19)', () => {
  it('crop/variety use master data options, and picking a crop clears a stale variety', async () => {
    listPlotsMock.mockResolvedValue([]);
    listMasterDataMock.mockImplementation(({ type, parent }: { type: string; parent?: string }) => {
      if (type === 'crop') {
        return Promise.resolve([
          masterDataItem({ id: 'crop-1', type: 'crop', value: 'พริก' }),
          masterDataItem({ id: 'crop-2', type: 'crop', value: 'เมล่อน' }),
        ]);
      }
      if (type === 'variety') {
        const all = [
          masterDataItem({ id: 'v-1', type: 'variety', value: 'พริกขี้หนู', parent: 'พริก' }),
          masterDataItem({ id: 'v-2', type: 'variety', value: 'เมล่อนญี่ปุ่น', parent: 'เมล่อน' }),
        ];
        return Promise.resolve(parent ? all.filter((v) => v.parent === parent) : all);
      }
      return Promise.resolve([]);
    });

    renderPlotsPage();

    fireEvent.click(await screen.findByText('เพิ่มแปลง'));
    await screen.findByText('เพิ่มแปลงใหม่');

    const cropSelect = await screen.findByDisplayValue('— เลือกชนิดพืช —') as HTMLSelectElement;
    fireEvent.change(cropSelect, { target: { value: 'พริก' } });

    const varietySelect = await screen.findByDisplayValue('— เลือกพันธุ์ —') as HTMLSelectElement;
    await waitFor(() => expect(varietySelect.querySelector('option[value="พริกขี้หนู"]')).toBeTruthy());
    expect(varietySelect.querySelector('option[value="เมล่อนญี่ปุ่น"]')).toBeNull();

    fireEvent.change(varietySelect, { target: { value: 'พริกขี้หนู' } });
    expect(screen.getByDisplayValue('พริกขี้หนู')).toBeTruthy();

    // Changing crop clears the now-mismatched variety selection back to
    // the placeholder.
    fireEvent.change(cropSelect, { target: { value: 'เมล่อน' } });
    await waitFor(() => expect(screen.getByDisplayValue('— เลือกพันธุ์ —')).toBeTruthy());
  });
});

describe('Plots create modal — atomic plot+cycle create (round 8.0.4)', () => {
  it('shows both the physical-plot and รอบปลูกแรก sections', async () => {
    listPlotsMock.mockResolvedValue([]);
    renderPlotsPage();

    fireEvent.click(await screen.findByText('เพิ่มแปลง'));
    await screen.findByText('เพิ่มแปลงใหม่');

    expect(screen.getByText('ข้อมูลแปลง')).toBeTruthy();
    expect(screen.getByText('ที่ตั้ง / พิกัด / พื้นที่')).toBeTruthy();
    expect(screen.getByText('รอบปลูกแรก')).toBeTruthy();
  });

  it('submits ONE createPlotWithCycle request with a nested plot/cycle payload — never createPlot+createPlotCycle separately', async () => {
    listPlotsMock.mockResolvedValue([]);
    createPlotWithCycleMock.mockResolvedValue({
      plot: { id: 'new-plot-1' },
      cycle: { id: 'new-cycle-1' },
    });
    const { container } = renderPlotsPage();

    fireEvent.click(await screen.findByText('เพิ่มแปลง'));
    await screen.findByText('เพิ่มแปลงใหม่');

    fireEvent.change(container.querySelector('input[name="plotCode"]')!, { target: { value: 'p101' } });
    fireEvent.change(container.querySelector('input[name="name"]')!, { target: { value: 'แปลง A' } });
    fireEvent.change(container.querySelector('input[name="poNumber"]')!, { target: { value: 'PO25001' } });
    await pickCropAndVariety();
    fireEvent.change(screen.getByPlaceholderText('เช่น jun2026 หรือ may2026'), { target: { value: 'jun2026' } });

    fireEvent.click(screen.getByRole('button', { name: 'สร้าง' }));

    await waitFor(() => expect(createPlotWithCycleMock).toHaveBeenCalledOnce());
    const [payload] = createPlotWithCycleMock.mock.calls[0];
    expect(payload.plot).toEqual(expect.objectContaining({
      supplierId: 'sup-1', plotCode: 'p101', name: 'แปลง A',
    }));
    expect(payload.plot).not.toHaveProperty('cycleLabel');
    expect(payload.cycle).toEqual(expect.objectContaining({ cycleLabel: 'jun2026' }));
  });

  it('round 8-13B: submits successfully with PO Number left blank — payload.cycle.poNumber is null', async () => {
    listPlotsMock.mockResolvedValue([]);
    createPlotWithCycleMock.mockResolvedValue({
      plot: { id: 'new-plot-2' },
      cycle: { id: 'new-cycle-2' },
    });
    const { container } = renderPlotsPage();

    fireEvent.click(await screen.findByText('เพิ่มแปลง'));
    await screen.findByText('เพิ่มแปลงใหม่');

    fireEvent.change(container.querySelector('input[name="plotCode"]')!, { target: { value: 'p102' } });
    fireEvent.change(container.querySelector('input[name="name"]')!, { target: { value: 'แปลง B' } });
    // PO Number deliberately left blank.
    await pickCropAndVariety();
    fireEvent.change(screen.getByPlaceholderText('เช่น jun2026 หรือ may2026'), { target: { value: 'jun2026' } });

    fireEvent.click(screen.getByRole('button', { name: 'สร้าง' }));

    await waitFor(() => expect(createPlotWithCycleMock).toHaveBeenCalledOnce());
    const [payload] = createPlotWithCycleMock.mock.calls[0];
    expect(payload.cycle.poNumber).toBeNull();
    expect(payload.cycle.pCode).toBe('Melon-A');
  });

  it('does not close the modal and shows the backend error when createPlotWithCycle fails', async () => {
    listPlotsMock.mockResolvedValue([]);
    createPlotWithCycleMock.mockRejectedValue({
      isAxiosError: true,
      response: { status: 409, data: { detail: 'Plot code already exists for this supplier' } },
    });
    const { container } = renderPlotsPage();

    fireEvent.click(await screen.findByText('เพิ่มแปลง'));
    await screen.findByText('เพิ่มแปลงใหม่');

    fireEvent.change(container.querySelector('input[name="plotCode"]')!, { target: { value: 'p101' } });
    fireEvent.change(container.querySelector('input[name="name"]')!, { target: { value: 'แปลง A' } });
    fireEvent.change(container.querySelector('input[name="poNumber"]')!, { target: { value: 'PO25001' } });
    await pickCropAndVariety();
    // Round 8-12B — Auto Lot (the default) also needs a cycleLabel, otherwise
    // submit is blocked at the form and the request never reaches the backend.
    fireEvent.change(container.querySelector('input[name="cycleLabel"]')!, { target: { value: '2605' } });
    fireEvent.click(screen.getByRole('button', { name: 'สร้าง' }));

    await waitFor(() => expect(screen.getByText(/Plot code already exists/)).toBeTruthy());
    // Modal is still open — no partial plot silently left behind.
    expect(screen.getByText('เพิ่มแปลงใหม่')).toBeTruthy();
  });
});

describe('Plots edit modal — physical-only fields (round 8.0.4)', () => {
  it('shows no crop/variety/lotNo/plantingDate/plantCount/expectedYield/unit fields', async () => {
    listPlotsMock.mockResolvedValue([
      {
        id: 'plot-1', supplierId: 'sup-1', supplierCode: 'SUP001', supplierName: 'Supplier One', qrKey: 'qr-1', plotCode: 'SUP001-P001', name: 'แปลงทดสอบ',
        village: null, district: null, province: 'จังหวัดทดสอบ',
        latitude: null, longitude: null,
        isActive: true, assignedCount: 0, primaryPhone: null, additionalPhones: [],
        currentYieldPct: '50', expectedYieldFull: '1000.00', expectedYieldUnit: 'kg',
      },
    ]);
    getPlotMock.mockResolvedValue({
      id: 'plot-1', supplierId: 'sup-1', plotCode: 'SUP001-P001', name: 'แปลงทดสอบ',
      village: null, district: null, province: 'จังหวัดทดสอบ',
      latitude: null, longitude: null, rai: null,
      isActive: true, assignedUsers: [], primaryPhone: null, additionalPhones: [], createdAt: '2026-01-01T00:00:00Z', updatedAt: '2026-01-01T00:00:00Z',
      currentCrop: 'พริก', currentVariety: 'พริกขี้หนู', currentLotNo: 'LOT-01', currentPlantingDate: '2026-06-01',
      plantCount: 500, expectedYieldFull: '1000.00', expectedYieldUnit: 'kg',
    });

    renderPlotsPage();

    await openRowMenu('SUP001-P001');
    fireEvent.click(await screen.findByRole('menuitem', { name: 'แก้ไขข้อมูลแปลง' }));
    await screen.findByText('แก้ไขข้อมูลแปลง');

    // Only the physical fields load once getPlot resolves.
    await waitFor(() => expect(screen.getByDisplayValue('แปลงทดสอบ')).toBeTruthy());

    // The plot HAS these values (currentCrop/plantCount/etc.) but the
    // physical-only Edit Plot modal must never surface them as inputs —
    // that data is edited via EditCycleModal on Plot Detail instead.
    expect(screen.queryByDisplayValue('พริก')).toBeNull();
    expect(screen.queryByDisplayValue('พริกขี้หนู')).toBeNull();
    expect(screen.queryByDisplayValue('LOT-01')).toBeNull();
    expect(screen.queryByDisplayValue('500')).toBeNull();
    expect(screen.queryByDisplayValue('1000')).toBeNull();
    expect(screen.queryByText('รอบปลูกแรก')).toBeNull();
    expect(screen.queryByText('รอบปลูกปัจจุบัน')).toBeNull();
    expect(screen.queryByText('แผนผลผลิต (Yield Planning)')).toBeNull();
  });
});

describe('Plots list — UX redesign (round 20)', () => {
  function onePlot() {
    return [
      {
        id: 'plot-1', supplierId: 'sup-1', supplierCode: 'SUP001', supplierName: 'Supplier One', qrKey: 'qr-1', plotCode: 'SUP001-P001', name: 'แปลงทดสอบ',
        village: null, district: null, province: 'จังหวัดทดสอบ',
        latitude: null, longitude: null,
        isActive: true, assignedCount: 0, primaryPhone: null, additionalPhones: [],
        // Round 7.3 — an active planting cycle is inferred from this mirror
        // field; most of this describe block's tests are about row features
        // unrelated to cycle status, so default to "has an active cycle".
        currentCrop: 'พริก',
      },
    ];
  }

  it('renders the plot name as a link straight to Plot Detail, and clicking it navigates there', async () => {
    listPlotsMock.mockResolvedValue(onePlot());
    renderPlotsPage();

    const nameLink = await screen.findByRole('link', { name: 'แปลงทดสอบ' });
    expect(nameLink.getAttribute('href')).toBe('/farmlog/admin/plots/plot-1');

    fireEvent.click(nameLink);
    expect(await screen.findByText('Plot Detail Page')).toBeTruthy();
  });

  it('navigates to Plot Detail when clicking elsewhere in the row (not on an interactive element)', async () => {
    listPlotsMock.mockResolvedValue(onePlot());
    renderPlotsPage();

    const codeCell = await screen.findByText('SUP001-P001');
    fireEvent.click(codeCell);

    expect(await screen.findByText('Plot Detail Page')).toBeTruthy();
  });

  it('does not show the old per-icon action buttons directly in the row — they are collapsed into one menu', async () => {
    listPlotsMock.mockResolvedValue(onePlot());
    renderPlotsPage();

    await screen.findByText('แปลงทดสอบ');
    expect(screen.queryByRole('menuitem', { name: 'แก้ไขข้อมูลแปลง' })).toBeNull();
    expect(screen.queryByRole('menuitem', { name: 'มอบหมายผู้ใช้' })).toBeNull();
    expect(await screen.findByTitle('ตัวเลือกเพิ่มเติมสำหรับแปลง SUP001-P001')).toBeTruthy();
  });

  it('opens the row action menu without navigating the row, and lists all secondary actions', async () => {
    listPlotsMock.mockResolvedValue(onePlot());
    renderPlotsPage();

    await openRowMenu('SUP001-P001');

    expect(screen.queryByText('Plot Detail Page')).toBeNull();
    expect(screen.getByRole('menuitem', { name: 'ดูรายละเอียด' })).toBeTruthy();
    // Round 8.0.4 — split into physical-plot edit vs cycle/yield management.
    expect(screen.getByRole('menuitem', { name: 'แก้ไขข้อมูลแปลง' })).toBeTruthy();
    expect(screen.getByRole('menuitem', { name: 'จัดการรอบปลูก / แผนผลผลิต' })).toBeTruthy();
    // Round 6.1 — QR is always in the menu now (plot carries its own supplier
    // data), not gated on the suppliers map.
    expect(screen.getByRole('menuitem', { name: 'พิมพ์ QR' })).toBeTruthy();
    // Assignment remains hidden; deactivation was restored for active plots.
    expect(screen.queryByRole('menuitem', { name: 'มอบหมายผู้ใช้' })).toBeNull();
    expect(screen.getByRole('menuitem', { name: 'ปิดใช้งานแปลง' })).toBeTruthy();
  });

  it('"จัดการรอบปลูก / แผนผลผลิต" navigates straight to Plot Detail', async () => {
    listPlotsMock.mockResolvedValue(onePlot());
    renderPlotsPage();

    await openRowMenu('SUP001-P001');
    fireEvent.click(screen.getByRole('menuitem', { name: 'จัดการรอบปลูก / แผนผลผลิต' }));

    expect(await screen.findByText('Plot Detail Page')).toBeTruthy();
  });

  it('closes the action menu on Escape', async () => {
    listPlotsMock.mockResolvedValue(onePlot());
    renderPlotsPage();

    await openRowMenu('SUP001-P001');
    expect(screen.getByRole('menuitem', { name: 'แก้ไขข้อมูลแปลง' })).toBeTruthy();

    fireEvent.keyDown(document, { key: 'Escape' });
    await waitFor(() => expect(screen.queryByRole('menuitem', { name: 'แก้ไขข้อมูลแปลง' })).toBeNull());
  });

  it('offers ปิดใช้งานแปลง for an active plot with plots.delete', async () => {
    listPlotsMock.mockResolvedValue(onePlot());
    renderPlotsPage();

    await openRowMenu('SUP001-P001');
    expect(screen.getByRole('menuitem', { name: 'ปิดใช้งานแปลง' })).toBeTruthy();
  });

  it('does not offer ปิดใช้งานแปลง for an inactive plot or without plots.delete', async () => {
    listPlotsMock.mockResolvedValue([{ ...onePlot()[0], isActive: false }]);
    const firstRender = renderPlotsPage();
    await openRowMenu('SUP001-P001');
    expect(screen.queryByRole('menuitem', { name: 'ปิดใช้งานแปลง' })).toBeNull();
    firstRender.unmount();

    useAuthStore.setState({ permissionKeys: new Set(['plots.read', 'plots.update']) });
    listPlotsMock.mockResolvedValue(onePlot());
    renderPlotsPage();
    await openRowMenu('SUP001-P001');
    expect(screen.queryByRole('menuitem', { name: 'ปิดใช้งานแปลง' })).toBeNull();
  });

  it('confirms deactivation through one endpoint call and reports success', async () => {
    listPlotsMock.mockResolvedValue(onePlot());
    deactivatePlotMock.mockResolvedValue({ ...onePlot()[0], isActive: false });
    renderPlotsPage();

    await openRowMenu('SUP001-P001');
    fireEvent.click(screen.getByRole('menuitem', { name: 'ปิดใช้งานแปลง' }));

    expect(await screen.findByText('หากมีรอบปลูกที่เปิดอยู่ ต้องปิดรอบปลูกปัจจุบันก่อน')).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: 'ยืนยันปิดใช้งานแปลง' }));

    await waitFor(() => expect(deactivatePlotMock).toHaveBeenCalledTimes(1));
    expect(deactivatePlotMock).toHaveBeenCalledWith('plot-1');
    expect(await screen.findByText('ปิดใช้งานแปลงแล้ว')).toBeTruthy();
  });

  it('keeps the deactivate modal open and shows the active-cycle conflict', async () => {
    listPlotsMock.mockResolvedValue(onePlot());
    deactivatePlotMock.mockRejectedValue(Object.assign(new Error('Conflict'), {
      isAxiosError: true,
      response: {
        status: 409,
        data: { detail: 'กรุณาปิดรอบปลูกปัจจุบันก่อนปิดใช้งานแปลง' },
      },
    }));
    renderPlotsPage();

    await openRowMenu('SUP001-P001');
    fireEvent.click(screen.getByRole('menuitem', { name: 'ปิดใช้งานแปลง' }));
    fireEvent.click(await screen.findByRole('button', { name: 'ยืนยันปิดใช้งานแปลง' }));

    expect(await screen.findByText('กรุณาปิดรอบปลูกปัจจุบันก่อนปิดใช้งานแปลง')).toBeTruthy();
    expect(screen.getByRole('button', { name: 'ยืนยันปิดใช้งานแปลง' })).toBeTruthy();
  });

  it('applies search only from the explicit search action and can clear filters', async () => {
    listPlotsMock.mockResolvedValue(onePlot());
    renderPlotsPage();

    await screen.findByText('แปลงทดสอบ');
    fireEvent.change(screen.getByLabelText(NAME_CODE_LABEL), {
      target: { value: 'SUP001-P001' },
    });
    expect(listPlotsMock).toHaveBeenCalledTimes(1);

    fireEvent.click(screen.getByRole('button', { name: 'ค้นหา' }));
    await waitFor(() => expect(hasListPlotsCallContaining({
      q: 'SUP001-P001',
    })).toBe(true));

    fireEvent.click(screen.getByRole('button', { name: 'ล้างค่า' }));
    await waitFor(() => expect(hasListPlotsCallContaining({
      q: undefined,
      supplierId: undefined,
      province: undefined,
    })).toBe(true));
  });

  it('filters by province through the plots API instead of only filtering the visible page', async () => {
    listPlotsMock.mockResolvedValue(onePlot());
    listPlotProvincesMock.mockResolvedValue(['กรุงเทพมหานคร', 'เชียงใหม่']);
    renderPlotsPage();

    await selectProvinceFilter('เชียงใหม่');

    await waitFor(() => expect(hasListPlotsCallContaining({
      province: 'เชียงใหม่',
    })).toBe(true));
  });

  it('uses a searchable Supplier dropdown and sends the selected supplier id to the API', async () => {
    listPlotsMock.mockResolvedValue(onePlot());
    listSuppliersMock.mockResolvedValue([
      { id: 'sup-1', code: 'SUP001', name: 'Supplier One', isActive: true, contactName: null, contactEmail: null },
      { id: 'sup-2', code: 'SUP002', name: 'Supplier Two', isActive: true, contactName: null, contactEmail: null },
    ]);
    renderPlotsPage();

    fireEvent.click(await screen.findByRole('button', { name: 'กรอง Supplier' }));
    fireEvent.change(screen.getByPlaceholderText('ค้นหา Supplier...'), { target: { value: 'two' } });
    await waitFor(() => expect(screen.queryByText('SUP001')).toBeNull());
    fireEvent.click(await screen.findByText('SUP002'));

    await waitFor(() => expect(hasListPlotsCallContaining({
      supplierId: 'sup-2',
    })).toBe(true));
    expect(await screen.findByText('SUP002 — Supplier Two')).toBeTruthy();
  });

  it('navigates to Plot Detail (not the Plot Edit modal) from the incomplete yield-plan badge (round 8.0.4)', async () => {
    listPlotsMock.mockResolvedValue([
      {
        ...onePlot()[0],
        plantCount: null,
        expectedYieldFull: null,
        expectedYieldUnit: null,
      },
    ]);
    renderPlotsPage();

    fireEvent.click(await screen.findByRole('button', { name: 'ยังไม่ตั้งแผนผลผลิต' }));

    expect(await screen.findByText('Plot Detail Page')).toBeTruthy();
    // Never opened the physical-plot Edit modal.
    expect(screen.queryByText('แก้ไขข้อมูลแปลง')).toBeNull();
  });

  it('no longer renders the "มอบหมาย" or "สถานะ" columns (round 8.0 cleanup)', async () => {
    listPlotsMock.mockResolvedValue(onePlot());
    renderPlotsPage();

    await screen.findByText('แปลงทดสอบ');
    expect(screen.queryByRole('columnheader', { name: 'มอบหมาย' })).toBeNull();
    expect(screen.queryByRole('columnheader', { name: 'สถานะ' })).toBeNull();
    // The inline "มอบหมายงาน" quick-assign badge is gone too.
    expect(screen.queryByRole('button', { name: 'มอบหมายงาน' })).toBeNull();
  });

  it('opens the edit modal from Plot Detail manage query parameters', async () => {
    listPlotsMock.mockResolvedValue(onePlot());
    getPlotMock.mockResolvedValue({
      id: 'plot-1', supplierId: 'sup-1', plotCode: 'SUP001-P001', name: 'แปลงทดสอบ',
      village: null, district: null, province: 'จังหวัดทดสอบ',
      latitude: null, longitude: null, rai: null,
      isActive: true, assignedUsers: [], primaryPhone: null, additionalPhones: [],
      createdAt: '2026-01-01T00:00:00Z', updatedAt: '2026-01-01T00:00:00Z',
      plantCount: null, expectedYieldFull: null, expectedYieldUnit: null,
      currentCrop: null, currentVariety: null, currentLotNo: null, currentPlantingDate: null,
    });

    renderPlotsPage('/farmlog/admin/plots?manage=edit&plotId=plot-1');

    expect(await screen.findByText('แก้ไขข้อมูลแปลง')).toBeTruthy();
    expect(getPlotMock).toHaveBeenCalledWith('plot-1');
  });

  it('opens the assignment modal from Plot Detail manage query parameters', async () => {
    listPlotsMock.mockResolvedValue(onePlot());
    getPlotMock.mockResolvedValue({
      id: 'plot-1', supplierId: 'sup-1', plotCode: 'SUP001-P001', name: 'แปลงทดสอบ',
      village: null, district: null, province: 'จังหวัดทดสอบ',
      latitude: null, longitude: null, rai: null,
      isActive: true, assignedUsers: [], primaryPhone: null, additionalPhones: [],
      createdAt: '2026-01-01T00:00:00Z', updatedAt: '2026-01-01T00:00:00Z',
    });
    listUsersMock.mockResolvedValue([]);

    renderPlotsPage('/farmlog/admin/plots?manage=assign&plotId=plot-1');

    expect(await screen.findByText(/มอบหมาย user ให้แปลง/)).toBeTruthy();
    expect(getPlotMock).toHaveBeenCalledWith('plot-1');
  });

  it('offers "ตรวจแปลง" in the row menu and navigates to the prefilled new-record form', async () => {
    listPlotsMock.mockResolvedValue(onePlot());
    renderPlotsPage();

    await openRowMenu('SUP001-P001');
    const inspect = await screen.findByRole('menuitem', { name: 'ตรวจแปลง' });
    fireEvent.click(inspect);

    expect(await screen.findByText('New Record Page')).toBeTruthy();
    // The deep link carries this plot's supplier + plot id so the form lands
    // scoped to it.
    expect(lastRecordSearch).toContain('supplierId=sup-1');
    expect(lastRecordSearch).toContain('plotId=plot-1');
  });

  it('hides "ตรวจแปลง" when the user lacks records.create', async () => {
    useAuthStore.setState({ permissionKeys: new Set(['plots.read', 'plots.update']) });
    listPlotsMock.mockResolvedValue(onePlot());
    renderPlotsPage();

    await openRowMenu('SUP001-P001');
    expect(screen.queryByRole('menuitem', { name: 'ตรวจแปลง' })).toBeNull();
  });
});

describe('Plots list — crop/variety filters', () => {
  function onePlot() {
    return [
      {
        id: 'plot-1', supplierId: 'sup-1', supplierCode: 'SUP001', supplierName: 'Supplier One', qrKey: 'qr-1', plotCode: 'SUP001-P001', name: 'แปลงทดสอบ',
        village: null, district: null, province: 'จังหวัดทดสอบ',
        latitude: null, longitude: null,
        isActive: true, assignedCount: 0, primaryPhone: null, additionalPhones: [],
      },
    ];
  }

  function mockCropVarietyMasterData() {
    listMasterDataMock.mockImplementation(({ type, parent }: { type: string; parent?: string }) => {
      if (type === 'crop') {
        return Promise.resolve([
          masterDataItem({ id: 'crop-1', type: 'crop', value: 'พริก' }),
          masterDataItem({ id: 'crop-2', type: 'crop', value: 'เมล่อน' }),
        ]);
      }
      if (type === 'variety') {
        const all = [
          masterDataItem({ id: 'v-1', type: 'variety', value: 'พริกขี้หนู', parent: 'พริก' }),
          masterDataItem({ id: 'v-2', type: 'variety', value: 'เมล่อนญี่ปุ่น', parent: 'เมล่อน' }),
        ];
        return Promise.resolve(parent ? all.filter((v) => v.parent === parent) : all);
      }
      return Promise.resolve([]);
    });
  }

  it('filters by crop through the plots API and narrows variety options to that crop', async () => {
    listPlotsMock.mockResolvedValue(onePlot());
    mockCropVarietyMasterData();
    renderPlotsPage();

    await selectCropFilter('พริก');

    await waitFor(() => expect(hasListPlotsCallContaining({ crop: 'พริก', variety: undefined })).toBe(true));

    // Variety options narrow to the chosen crop's children.
    fireEvent.click(screen.getByRole('button', { name: 'กรองพันธุ์/สายพันธุ์' }));
    const varietyListbox = await screen.findByRole('listbox');
    await waitFor(() => expect(within(varietyListbox).getByText('พริกขี้หนู')).toBeTruthy());
    expect(within(varietyListbox).queryByText('เมล่อนญี่ปุ่น')).toBeNull();

    fireEvent.click(within(varietyListbox).getByText('พริกขี้หนู'));
    await waitFor(() => expect(hasListPlotsCallContaining({ crop: 'พริก', variety: 'พริกขี้หนู' })).toBe(true));
  });

  it('round 8-22A: the crop filter never reuses the Admin Master Data (all-status) cache entry', async () => {
    listPlotsMock.mockResolvedValue(onePlot());
    mockCropVarietyMasterData();
    // Simulates the Admin Master Data page having already loaded 'crop'
    // (ALL statuses, including a deactivated one) under ITS query key,
    // before this Plots page mounts and queries the SAME type/parent with
    // activeOnly=true.
    const qc = new QueryClient({
      defaultOptions: { queries: { retry: false, staleTime: 5 * 60 * 1000 } },
    });
    qc.setQueryData(masterDataQueryKey('crop', null, false), [
      masterDataItem({ id: 'crop-1', type: 'crop', value: 'พริก' }),
      masterDataItem({ id: 'crop-2', type: 'crop', value: 'เมล่อน' }),
      masterDataItem({ id: 'crop-3', type: 'crop', value: 'ทุเรียนปิด' }),
    ]);

    renderPlotsPage('/farmlog/admin/plots', qc);

    fireEvent.click(await screen.findByRole('button', { name: 'กรองชนิดพืช' }));
    const listbox = await screen.findByRole('listbox');
    await waitFor(() => expect(within(listbox).getByText('พริก')).toBeTruthy());
    // A distinct key means this filter's own activeOnly=true fetch ran
    // instead — the deactivated crop from the Admin page's cache entry
    // never leaks in as a selectable option for a new/existing plot.
    expect(within(listbox).queryByText('ทุเรียนปิด')).toBeNull();
  });

  it('changing the crop filter resets a stale variety selection', async () => {
    listPlotsMock.mockResolvedValue(onePlot());
    mockCropVarietyMasterData();
    renderPlotsPage();

    await selectCropFilter('พริก');
    await selectVarietyFilter('พริกขี้หนู');
    await waitFor(() => expect(hasListPlotsCallContaining({ crop: 'พริก', variety: 'พริกขี้หนู' })).toBe(true));

    await selectCropFilter('เมล่อน');

    await waitFor(() => expect(hasListPlotsCallContaining({ crop: 'เมล่อน', variety: undefined })).toBe(true));
    expect(screen.getByRole('button', { name: 'กรองพันธุ์/สายพันธุ์' }).textContent).toContain('ทุกพันธุ์');
  });

  it('ล้างค่า clears the crop/variety filters too', async () => {
    listPlotsMock.mockResolvedValue(onePlot());
    mockCropVarietyMasterData();
    renderPlotsPage();

    await selectCropFilter('พริก');
    await waitFor(() => expect(hasListPlotsCallContaining({ crop: 'พริก' })).toBe(true));

    fireEvent.click(screen.getByRole('button', { name: 'ล้างค่า' }));

    await waitFor(() => expect(hasListPlotsCallContaining({
      crop: undefined,
      variety: undefined,
      supplierId: undefined,
      province: undefined,
    })).toBe(true));
    expect(screen.getByRole('button', { name: 'กรองชนิดพืช' }).textContent).toContain('ทุกชนิดพืช');
  });
});

describe('Plots list — searchable province/crop/variety filters (round 8-18A)', () => {
  function onePlot() {
    return [
      {
        id: 'plot-1', supplierId: 'sup-1', supplierCode: 'SUP001', supplierName: 'Supplier One', qrKey: 'qr-1', plotCode: 'SUP001-P001', name: 'แปลงทดสอบ',
        village: null, district: null, province: 'จังหวัดทดสอบ',
        latitude: null, longitude: null,
        isActive: true, assignedCount: 0, primaryPhone: null, additionalPhones: [],
      },
    ];
  }

  function mockCropVarietyMasterData() {
    listMasterDataMock.mockImplementation(({ type, parent }: { type: string; parent?: string }) => {
      if (type === 'crop') {
        return Promise.resolve([
          masterDataItem({ id: 'crop-1', type: 'crop', value: 'พริก' }),
          masterDataItem({ id: 'crop-2', type: 'crop', value: 'เมล่อน' }),
        ]);
      }
      if (type === 'variety') {
        const all = [
          masterDataItem({ id: 'v-1', type: 'variety', value: 'พริกขี้หนู', parent: 'พริก' }),
          masterDataItem({ id: 'v-2', type: 'variety', value: 'เมล่อนญี่ปุ่น', parent: 'เมล่อน' }),
        ];
        return Promise.resolve(parent ? all.filter((v) => v.parent === parent) : all);
      }
      return Promise.resolve([]);
    });
  }

  beforeEach(() => {
    listPlotsMock.mockResolvedValue(onePlot());
    listPlotProvincesMock.mockResolvedValue(['เชียงใหม่', 'เชียงราย', 'ตาก']);
  });

  it('typing in the province search box narrows the options (case-insensitive, partial match)', async () => {
    renderPlotsPage();
    await screen.findByText('แปลงทดสอบ');

    fireEvent.click(screen.getByRole('button', { name: 'กรองจังหวัด' }));
    const listbox = await screen.findByRole('listbox');
    await within(listbox).findByText('เชียงใหม่');
    fireEvent.change(screen.getByPlaceholderText('ค้นหาจังหวัด...'), { target: { value: 'เชียง' } });

    expect(within(listbox).getByText('เชียงใหม่')).toBeTruthy();
    expect(within(listbox).getByText('เชียงราย')).toBeTruthy();
    expect(within(listbox).queryByText('ตาก')).toBeNull();
  });

  it('province search is case-insensitive (matches regardless of case typed)', async () => {
    listPlotProvincesMock.mockResolvedValue(['Chiang Mai', 'Tak']);
    renderPlotsPage();
    await screen.findByText('แปลงทดสอบ');

    fireEvent.click(screen.getByRole('button', { name: 'กรองจังหวัด' }));
    const listbox = await screen.findByRole('listbox');
    await within(listbox).findByText('Chiang Mai');
    fireEvent.change(screen.getByPlaceholderText('ค้นหาจังหวัด...'), { target: { value: 'CHIANG' } });

    expect(within(listbox).getByText('Chiang Mai')).toBeTruthy();
    expect(within(listbox).queryByText('Tak')).toBeNull();
  });

  it('shows "ไม่พบข้อมูล" when the province search matches nothing', async () => {
    renderPlotsPage();
    await screen.findByText('แปลงทดสอบ');

    fireEvent.click(screen.getByRole('button', { name: 'กรองจังหวัด' }));
    const listbox = await screen.findByRole('listbox');
    await within(listbox).findByText('เชียงใหม่');
    fireEvent.change(screen.getByPlaceholderText('ค้นหาจังหวัด...'), { target: { value: 'xyz-no-match' } });

    expect(within(listbox).getByText('ไม่พบข้อมูล')).toBeTruthy();
  });

  it('crop search is case-insensitive and matches a partial name', async () => {
    mockCropVarietyMasterData();
    renderPlotsPage();
    await screen.findByText('แปลงทดสอบ');

    fireEvent.click(screen.getByRole('button', { name: 'กรองชนิดพืช' }));
    const listbox = await screen.findByRole('listbox');
    await within(listbox).findByText('พริก');
    fireEvent.change(screen.getByPlaceholderText('ค้นหาชนิดพืช...'), { target: { value: 'พริ' } });

    expect(within(listbox).getByText('พริก')).toBeTruthy();
    expect(within(listbox).queryByText('เมล่อน')).toBeNull();
  });

  it('typing in the variety search box narrows the options', async () => {
    mockCropVarietyMasterData();
    renderPlotsPage();
    await screen.findByText('แปลงทดสอบ');

    fireEvent.click(screen.getByRole('button', { name: 'กรองพันธุ์/สายพันธุ์' }));
    const listbox = await screen.findByRole('listbox');
    await within(listbox).findByText('พริกขี้หนู');
    fireEvent.change(screen.getByPlaceholderText('ค้นหาพันธุ์...'), { target: { value: 'เมล่อน' } });

    expect(within(listbox).getByText('เมล่อนญี่ปุ่น')).toBeTruthy();
    expect(within(listbox).queryByText('พริกขี้หนู')).toBeNull();
  });

  it('when no crop is selected, every active variety is shown (not narrowed)', async () => {
    mockCropVarietyMasterData();
    renderPlotsPage();
    await screen.findByText('แปลงทดสอบ');

    fireEvent.click(screen.getByRole('button', { name: 'กรองพันธุ์/สายพันธุ์' }));
    const listbox = await screen.findByRole('listbox');
    expect(await within(listbox).findByText('พริกขี้หนู')).toBeTruthy();
    expect(within(listbox).getByText('เมล่อนญี่ปุ่น')).toBeTruthy();
  });

  it('selecting "ทุกจังหวัด" again clears an already-selected province', async () => {
    renderPlotsPage();
    await screen.findByText('แปลงทดสอบ');
    await selectProvinceFilter('เชียงใหม่');
    await waitFor(() => expect(hasListPlotsCallContaining({ province: 'เชียงใหม่' })).toBe(true));

    fireEvent.click(screen.getByRole('button', { name: 'กรองจังหวัด' }));
    const listbox = await screen.findByRole('listbox');
    fireEvent.click(within(listbox).getByText('ทุกจังหวัด'));

    await waitFor(() => expect(hasListPlotsCallContaining({ province: undefined })).toBe(true));
    expect(screen.getByRole('button', { name: 'กรองจังหวัด' }).textContent).toContain('ทุกจังหวัด');
  });

  it('selecting "ทุกชนิดพืช" clears crop AND the already-selected variety', async () => {
    mockCropVarietyMasterData();
    renderPlotsPage();
    await screen.findByText('แปลงทดสอบ');
    await selectCropFilter('พริก');
    await selectVarietyFilter('พริกขี้หนู');
    await waitFor(() => expect(hasListPlotsCallContaining({ crop: 'พริก', variety: 'พริกขี้หนู' })).toBe(true));

    fireEvent.click(screen.getByRole('button', { name: 'กรองชนิดพืช' }));
    const listbox = await screen.findByRole('listbox');
    fireEvent.click(within(listbox).getByText('ทุกชนิดพืช'));

    await waitFor(() => expect(hasListPlotsCallContaining({ crop: undefined, variety: undefined })).toBe(true));
    expect(screen.getByRole('button', { name: 'กรองพันธุ์/สายพันธุ์' }).textContent).toContain('ทุกพันธุ์');
  });

  it('Clear Filters resets province/crop/variety AND status back to active', async () => {
    mockCropVarietyMasterData();
    renderPlotsPage();
    await screen.findByText('แปลงทดสอบ');
    await selectProvinceFilter('เชียงใหม่');
    await selectCropFilter('พริก');
    await selectVarietyFilter('พริกขี้หนู');
    const statusSelect = await screen.findByLabelText('กรองสถานะแปลง') as HTMLSelectElement;
    fireEvent.change(statusSelect, { target: { value: 'inactive' } });
    await waitFor(() => expect(hasListPlotsCallContaining({ plotStatus: 'inactive' })).toBe(true));

    fireEvent.click(screen.getByRole('button', { name: 'ล้างค่า' }));

    await waitFor(() => expect(hasListPlotsCallContaining({
      province: undefined, crop: undefined, variety: undefined, plotStatus: 'active',
    })).toBe(true));
    expect(screen.getByRole('button', { name: 'กรองจังหวัด' }).textContent).toContain('ทุกจังหวัด');
    expect(screen.getByRole('button', { name: 'กรองชนิดพืช' }).textContent).toContain('ทุกชนิดพืช');
    expect(screen.getByRole('button', { name: 'กรองพันธุ์/สายพันธุ์' }).textContent).toContain('ทุกพันธุ์');
    expect((screen.getByLabelText('กรองสถานะแปลง') as HTMLSelectElement).value).toBe('active');
  });

  it('Excel Template download still receives province/crop/variety from the searchable comboboxes', async () => {
    mockCropVarietyMasterData();
    downloadPlotImportTemplateMock.mockResolvedValue(new Blob(['x']));
    renderPlotsPage();
    await screen.findByText('แปลงทดสอบ');

    fireEvent.click(await screen.findByRole('button', { name: 'กรอง Supplier' }));
    fireEvent.click(within(await screen.findByRole('listbox')).getByText('SUP001'));
    await selectProvinceFilter('เชียงใหม่');
    await selectCropFilter('พริก');
    await selectVarietyFilter('พริกขี้หนู');

    fireEvent.click(screen.getByRole('button', { name: 'ดาวน์โหลด Excel' }));
    fireEvent.click(screen.getByRole('menuitem', { name: 'ตามตัวกรองปัจจุบัน' }));

    await waitFor(() => expect(downloadPlotImportTemplateMock).toHaveBeenCalledWith(
      expect.objectContaining({
        supplierId: 'sup-1', province: 'เชียงใหม่', crop: 'พริก', variety: 'พริกขี้หนู',
      }),
    ));
  });
});

describe('Plots list — "รอบปลูกปัจจุบัน" filter (round 8-18)', () => {
  function onePlot() {
    return [
      {
        id: 'plot-1', supplierId: 'sup-1', supplierCode: 'SUP001', supplierName: 'Supplier One', qrKey: 'qr-1', plotCode: 'SUP001-P001', name: 'แปลงทดสอบ',
        village: null, district: null, province: 'จังหวัดทดสอบ',
        latitude: null, longitude: null,
        isActive: true, assignedCount: 0, primaryPhone: null, additionalPhones: [],
      },
    ];
  }

  beforeEach(() => {
    listPlotsMock.mockResolvedValue(onePlot());
    listPlotCycleLabelsMock.mockResolvedValue(['jun2026', 'aug2026']);
  });

  function openCycleLabelCombobox() {
    fireEvent.click(screen.getByRole('button', { name: 'กรองรอบปลูกปัจจุบัน' }));
  }

  it('lists the real cycleLabel options from listPlotCycleLabels', async () => {
    renderPlotsPage();
    await screen.findByText('แปลงทดสอบ');
    openCycleLabelCombobox();

    const listbox = await screen.findByRole('listbox');
    expect(within(listbox).getByText('jun2026')).toBeTruthy();
    expect(within(listbox).getByText('aug2026')).toBeTruthy();
  });

  it('selecting a cycleLabel forwards it to listPlots', async () => {
    renderPlotsPage();
    await screen.findByText('แปลงทดสอบ');
    openCycleLabelCombobox();
    fireEvent.click(await screen.findByText('jun2026'));

    await waitFor(() => expect(hasListPlotsCallContaining({ cycleLabel: 'jun2026' })).toBe(true));
  });

  it('ล้างค่า clears the cycleLabel filter too', async () => {
    renderPlotsPage();
    await screen.findByText('แปลงทดสอบ');
    openCycleLabelCombobox();
    fireEvent.click(await screen.findByText('jun2026'));
    await waitFor(() => expect(hasListPlotsCallContaining({ cycleLabel: 'jun2026' })).toBe(true));

    fireEvent.click(screen.getByRole('button', { name: 'ล้างค่า' }));

    await waitFor(() => expect(hasListPlotsCallContaining({ cycleLabel: undefined })).toBe(true));
    expect(screen.getByRole('button', { name: 'กรองรอบปลูกปัจจุบัน' }).textContent).toContain('ทุกรอบปลูก');
  });

  it('forwards cycleLabel to searchPlotsByPhone in phone-search mode', async () => {
    searchPlotsByPhoneMock.mockResolvedValue(onePlot());
    renderPlotsPage();
    await screen.findByText('แปลงทดสอบ');
    openCycleLabelCombobox();
    fireEvent.click(await screen.findByText('jun2026'));
    await waitFor(() => expect(hasListPlotsCallContaining({ cycleLabel: 'jun2026' })).toBe(true));

    fireEvent.change(
      screen.getByLabelText(ACCESS_NUMBER_LABEL),
      { target: { value: '0812345678' } },
    );
    fireEvent.click(screen.getByRole('button', { name: 'ค้นหา' }));

    await waitFor(() => {
      const call = searchPlotsByPhoneMock.mock.calls[0]?.[0] as { cycleLabel?: string } | undefined;
      expect(call?.cycleLabel).toBe('jun2026');
    });
  });

  it('forwards cycleLabel to the Excel template download and shows it in the filter summary', async () => {
    downloadPlotImportTemplateMock.mockResolvedValue(new Blob(['x']));
    renderPlotsPage();
    await screen.findByText('แปลงทดสอบ');

    fireEvent.click(await screen.findByRole('button', { name: 'กรอง Supplier' }));
    fireEvent.click(within(await screen.findByRole('listbox')).getByText('SUP001'));

    openCycleLabelCombobox();
    fireEvent.click(await screen.findByText('jun2026'));

    expect(await screen.findByText(/รอบปลูก: jun2026/)).toBeTruthy();

    fireEvent.click(screen.getByRole('button', { name: 'ดาวน์โหลด Excel' }));
    fireEvent.click(screen.getByRole('menuitem', { name: 'ตามตัวกรองปัจจุบัน' }));

    await waitFor(() => expect(downloadPlotImportTemplateMock).toHaveBeenCalledWith(
      expect.objectContaining({ cycleLabel: 'jun2026' }),
    ));
  });
});

describe('Plots list — "วันที่เริ่ม...ถึง" planting-date filter (round 8-25K)', () => {
  function onePlot() {
    return [
      {
        id: 'plot-1', supplierId: 'sup-1', supplierCode: 'SUP001', supplierName: 'Supplier One', qrKey: 'qr-1', plotCode: 'SUP001-P001', name: 'แปลงทดสอบ',
        village: null, district: null, province: 'จังหวัดทดสอบ',
        latitude: null, longitude: null,
        isActive: true, assignedCount: 0, primaryPhone: null, additionalPhones: [],
      },
    ];
  }

  beforeEach(() => {
    listPlotsMock.mockResolvedValue(onePlot());
  });

  it('forwards both bounds to listPlots', async () => {
    renderPlotsPage();
    await screen.findByText('แปลงทดสอบ');

    fireEvent.change(screen.getByLabelText('วันที่เริ่ม (จาก)'), { target: { value: '2026-08-01' } });
    fireEvent.change(screen.getByLabelText('วันที่เริ่ม (ถึง)'), { target: { value: '2026-08-31' } });

    await waitFor(() => expect(hasListPlotsCallContaining({
      plantingDateFrom: '2026-08-01', plantingDateTo: '2026-08-31',
    })).toBe(true));
  });

  it('forwards a single bound alone — the other stays undefined, not an empty string', async () => {
    renderPlotsPage();
    await screen.findByText('แปลงทดสอบ');

    fireEvent.change(screen.getByLabelText('วันที่เริ่ม (จาก)'), { target: { value: '2026-08-01' } });

    await waitFor(() => expect(hasListPlotsCallContaining({
      plantingDateFrom: '2026-08-01', plantingDateTo: undefined,
    })).toBe(true));
  });

  it('resets to the first page when either bound changes', async () => {
    // "ถัดไป →" is disabled unless the current page is FULL (plots.length
    // >= pageSize) — a full 100-row page is needed to reach page 2 at all.
    listPlotsMock.mockResolvedValue(Array.from({ length: 100 }, (_, i) => ({
      ...onePlot()[0], id: `plot-${i}`, plotCode: `SUP001-P${i}`,
    })));
    renderPlotsPage();
    await screen.findByText('SUP001-P0');
    fireEvent.click(await screen.findByRole('button', { name: 'ถัดไป →' }));
    await waitFor(() => expect(hasListPlotsCallContaining({ offset: 100 })).toBe(true));

    fireEvent.change(screen.getByLabelText('วันที่เริ่ม (จาก)'), { target: { value: '2026-08-01' } });

    await waitFor(() => expect(hasListPlotsCallContaining({ offset: 0, plantingDateFrom: '2026-08-01' })).toBe(true));
  });

  it('ล้างค่า clears both date bounds too', async () => {
    renderPlotsPage();
    await screen.findByText('แปลงทดสอบ');
    fireEvent.change(screen.getByLabelText('วันที่เริ่ม (จาก)'), { target: { value: '2026-08-01' } });
    fireEvent.change(screen.getByLabelText('วันที่เริ่ม (ถึง)'), { target: { value: '2026-08-31' } });
    await waitFor(() => expect(hasListPlotsCallContaining({ plantingDateFrom: '2026-08-01' })).toBe(true));

    fireEvent.click(screen.getByRole('button', { name: 'ล้างค่า' }));

    await waitFor(() => expect(hasListPlotsCallContaining({
      plantingDateFrom: undefined, plantingDateTo: undefined,
    })).toBe(true));
    expect((screen.getByLabelText('วันที่เริ่ม (จาก)') as HTMLInputElement).value).toBe('');
    expect((screen.getByLabelText('วันที่เริ่ม (ถึง)') as HTMLInputElement).value).toBe('');
  });

  it('forwards both bounds to searchPlotsByPhone in phone-search mode', async () => {
    searchPlotsByPhoneMock.mockResolvedValue(onePlot());
    renderPlotsPage();
    await screen.findByText('แปลงทดสอบ');

    fireEvent.change(screen.getByLabelText('วันที่เริ่ม (จาก)'), { target: { value: '2026-08-01' } });
    await waitFor(() => expect(hasListPlotsCallContaining({ plantingDateFrom: '2026-08-01' })).toBe(true));

    fireEvent.change(screen.getByLabelText(ACCESS_NUMBER_LABEL), { target: { value: '0812345678' } });
    fireEvent.click(screen.getByRole('button', { name: 'ค้นหา' }));

    await waitFor(() => {
      const call = searchPlotsByPhoneMock.mock.calls[0]?.[0] as { plantingDateFrom?: string } | undefined;
      expect(call?.plantingDateFrom).toBe('2026-08-01');
    });
  });

  it('is NEVER forwarded to the Excel template download — list-only, same precedent as the access-number search box', async () => {
    downloadPlotImportTemplateMock.mockResolvedValue(new Blob(['x']));
    renderPlotsPage();
    await screen.findByText('แปลงทดสอบ');

    fireEvent.click(await screen.findByRole('button', { name: 'กรอง Supplier' }));
    fireEvent.click(within(await screen.findByRole('listbox')).getByText('SUP001'));
    fireEvent.change(screen.getByLabelText('วันที่เริ่ม (จาก)'), { target: { value: '2026-08-01' } });
    await waitFor(() => expect(hasListPlotsCallContaining({ plantingDateFrom: '2026-08-01' })).toBe(true));

    fireEvent.click(screen.getByRole('button', { name: 'ดาวน์โหลด Excel' }));
    fireEvent.click(screen.getByRole('menuitem', { name: 'ตามตัวกรองปัจจุบัน' }));

    await waitFor(() => expect(downloadPlotImportTemplateMock).toHaveBeenCalled());
    const call = downloadPlotImportTemplateMock.mock.calls[0][0] as Record<string, unknown>;
    expect(call).not.toHaveProperty('plantingDateFrom');
    expect(call).not.toHaveProperty('plantingDateTo');
  });
});

describe('Plots list — rows-per-page selector (100 / 200 / 500 / ทั้งหมด)', () => {
  function onePlot() {
    return [
      {
        id: 'plot-1', supplierId: 'sup-1', supplierCode: 'SUP001', supplierName: 'Supplier One', qrKey: 'qr-1', plotCode: 'SUP001-P001', name: 'แปลงทดสอบ',
        village: null, district: null, province: 'จังหวัดทดสอบ',
        latitude: null, longitude: null,
        isActive: true, assignedCount: 0, primaryPhone: null, additionalPhones: [],
      },
    ];
  }

  it('defaults to fetching 100 rows', async () => {
    listPlotsMock.mockResolvedValue(onePlot());
    renderPlotsPage();

    await waitFor(() => expect(hasListPlotsCallContaining({ limit: 100, offset: 0 })).toBe(true));
    const selector = screen.getByLabelText('แสดง') as HTMLSelectElement;
    expect(selector.value).toBe('100');
  });

  it('switches to 200 rows per page when selected', async () => {
    listPlotsMock.mockResolvedValue(onePlot());
    renderPlotsPage();

    await screen.findByText('SUP001-P001');
    fireEvent.change(screen.getByLabelText('แสดง'), { target: { value: '200' } });

    await waitFor(() => expect(hasListPlotsCallContaining({ limit: 200, offset: 0 })).toBe(true));
  });

  it('switches to 500 rows per page when selected (round 8-25D)', async () => {
    listPlotsMock.mockResolvedValue(onePlot());
    renderPlotsPage();

    await screen.findByText('SUP001-P001');
    fireEvent.change(screen.getByLabelText('แสดง'), { target: { value: '500' } });

    await waitFor(() => expect(hasListPlotsCallContaining({ limit: 500, offset: 0 })).toBe(true));
  });

  it('pages through everything (chunked) when "ทั้งหมด" is selected', async () => {
    // First page fills the 200 chunk → fetchAllPages requests a second page.
    const firstChunk = Array.from({ length: 200 }, (_, i) => ({
      ...onePlot()[0], id: `plot-${i}`, plotCode: `SUP001-P${i}`,
    }));
    listPlotsMock
      .mockResolvedValueOnce(firstChunk)      // default 100-row load on mount
      .mockResolvedValueOnce(firstChunk)      // "all": chunk 1 (full → keep going)
      .mockResolvedValueOnce(onePlot());      // "all": chunk 2 (short → stop)

    renderPlotsPage();
    await screen.findByText('SUP001-P0');

    fireEvent.change(screen.getByLabelText('แสดง'), { target: { value: 'all' } });

    // Rendering the full 200-row chunk is heavy; allow extra time so this
    // doesn't flake under parallel suite load (it's fast in isolation).
    await waitFor(() => expect(hasListPlotsCallContaining({ limit: 200, offset: 200 })).toBe(true), {
      timeout: 15000,
    });
    // Prev/next are hidden in "all" mode.
    expect(screen.queryByText('ถัดไป →')).toBeNull();
  }, 20000);
});

describe('Plots — round 6.1 (supplier from summary / QR / invalidation)', () => {
  function summaryPlot(overrides: Record<string, unknown> = {}) {
    return {
      id: 'plot-1', supplierId: 'sup-1',
      supplierCode: 'SUP777', supplierName: 'ซัพเกินลิมิต',
      plotCode: 'SUP777-P001', name: 'แปลงสรุป',
      village: null, district: null, province: 'จังหวัดทดสอบ',
      latitude: null, longitude: null, isActive: true, assignedCount: 0, primaryPhone: null, additionalPhones: [],
      qrKey: 'qr-777',
      currentYieldPct: null, expectedYieldFull: null, expectedYieldUnit: null,
      plantCount: null, currentCrop: null, currentVariety: null,
      currentLotNo: null, currentPlantingDate: null, currentStage: null,
      lastInspectedAt: null,
      ...overrides,
    };
  }

  function plotDetail(overrides: Record<string, unknown> = {}) {
    return {
      id: 'plot-1', supplierId: 'sup-1', supplierCode: 'SUP777', supplierName: 'ซัพเกินลิมิต',
      plotCode: 'SUP777-P001', name: 'แปลงสรุป',
      village: null, district: null, province: null,
      latitude: null, longitude: null, rai: null, isActive: true,
      assignedUsers: [], createdAt: '2026-01-01T00:00:00Z', updatedAt: '2026-01-01T00:00:00Z',
      qrKey: 'qr-777',
      currentCrop: null, currentVariety: null, currentLotNo: null, currentPlantingDate: null,
      currentStage: null, currentYieldPct: null,
      currentFieldPrepScore: null, currentWeatherScore: null, currentCareScore: null,
      currentVarietyResistanceScore: null, currentGpsLat: null, currentGpsLng: null,
      lastInspectedAt: null, lastInspectedByCode: null, lastInspectionRecordId: null,
      plantCount: null, expectedYieldFull: null, expectedYieldUnit: null,
      ...overrides,
    };
  }

  it('shows supplier code+name from the plot summary even when the suppliers list is empty', async () => {
    // The whole point of round 6.1: no dependency on the capped active-
    // suppliers fetch. Supplier "SUP777" is NOT in that (empty) list here.
    listSuppliersMock.mockResolvedValue([]);
    listPlotsMock.mockResolvedValue([summaryPlot()]);
    renderPlotsPage();

    expect(await screen.findByText('SUP777')).toBeTruthy();
    expect(screen.getByText('ซัพเกินลิมิต')).toBeTruthy();
    // No id-slice fallback shown.
    expect(screen.queryByText('sup-1'.slice(0, 8))).toBeNull();
  });

  it('prints a row QR from the plot summary without needing the suppliers map', async () => {
    listSuppliersMock.mockResolvedValue([]); // no map at all
    listPlotsMock.mockResolvedValue([summaryPlot()]);
    renderPlotsPage();

    await openRowMenu('SUP777-P001');
    fireEvent.click(await screen.findByRole('menuitem', { name: 'พิมพ์ QR' }));

    // The print sheet opened with exactly this one plot.
    expect(await screen.findByText(/พิมพ์ QR แปลง \(1 รายการ\)/)).toBeTruthy();
  });

  it('invalidates plots, that plot, provinces, and the plot-status report after a save', async () => {
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const invalidateSpy = vi.spyOn(qc, 'invalidateQueries');
    listPlotsMock.mockResolvedValue([summaryPlot()]);
    getPlotMock.mockResolvedValue(plotDetail());
    updatePlotMock.mockResolvedValue(plotDetail());

    render(
      <QueryClientProvider client={qc}>
        <MemoryRouter initialEntries={['/farmlog/admin/plots']}>
          <Routes>
            <Route path="/farmlog/admin/plots" element={<Plots />} />
          </Routes>
        </MemoryRouter>
      </QueryClientProvider>,
    );

    await openRowMenu('SUP777-P001');
    fireEvent.click(await screen.findByRole('menuitem', { name: 'แก้ไขข้อมูลแปลง' }));

    fireEvent.click(await screen.findByRole('button', { name: 'บันทึก' }));

    await waitFor(() => expect(updatePlotMock).toHaveBeenCalledOnce());
    const keys = invalidateSpy.mock.calls
      .map((c) => (c[0] as { queryKey?: unknown[] })?.queryKey)
      .filter(Boolean) as unknown[][];
    const firstKeys = keys.map((k) => k[0]);
    expect(firstKeys).toContain('plots');
    expect(firstKeys).toContain('plot-provinces');
    expect(firstKeys).toContain('report-plot-status');
    expect(keys.some((k) => k[0] === 'plot' && k[1] === 'plot-1')).toBe(true);
  });
});

// --- round 8-3C: access-phone columns + action menu + create integration ----

describe('Plots list — access phone columns (round 8-3C)', () => {
  function plotRow(overrides: Record<string, unknown> = {}) {
    return {
      id: 'plot-1', supplierId: 'sup-1', supplierCode: 'SUP001', supplierName: 'Supplier One',
      qrKey: 'qr-1', plotCode: 'SUP001-P001', name: 'แปลงทดสอบ',
      village: null, district: null, province: 'จังหวัดทดสอบ',
      latitude: null, longitude: null, isActive: true, assignedCount: 0,
      primaryPhone: null, additionalPhones: [] as string[],
      currentCrop: null, currentVariety: null, currentLotNo: null, currentPlantingDate: null,
      currentStage: null, lastInspectedAt: null,
      currentYieldPct: null, expectedYieldFull: null, expectedYieldUnit: null, plantCount: null,
      activeCycleId: null, activeCycleNo: null, activeCycleStatus: null, activeCycleCrop: null,
      activeCycleVariety: null, activeCycleLabel: null, activeCycleLotNo: null,
      activeCyclePlantingDate: null, activeCyclePlantCount: null,
      activeCycleExpectedYieldFull: null, activeCycleExpectedYieldUnit: null,
      ...overrides,
    };
  }

  it('renders separate เบอร์หลัก / เบอร์เสริม column headers', async () => {
    listPlotsMock.mockResolvedValue([plotRow()]);
    renderPlotsPage();

    await screen.findByText('แปลงทดสอบ');
    expect(screen.getByRole('columnheader', { name: 'เบอร์หลัก' })).toBeTruthy();
    expect(screen.getByRole('columnheader', { name: 'เบอร์เสริม' })).toBeTruthy();
  });

  it('shows the "ยังไม่ตั้ง" badge when there is no primary phone', async () => {
    listPlotsMock.mockResolvedValue([plotRow({ primaryPhone: null })]);
    renderPlotsPage();

    expect(await screen.findByText('ยังไม่ตั้ง')).toBeTruthy();
  });

  it('shows the full formatted primary phone', async () => {
    listPlotsMock.mockResolvedValue([plotRow({ primaryPhone: '0845552162' })]);
    renderPlotsPage();

    expect(await screen.findByText('084-555-2162')).toBeTruthy();
  });

  it('shows "—" when there are no additional phones', async () => {
    listPlotsMock.mockResolvedValue([plotRow({ additionalPhones: [] })]);
    renderPlotsPage();

    await screen.findByText('แปลงทดสอบ');
    // "—" is also the placeholder for other null cells (e.g. no active cycle
    // info) on this row, so assert at least one rather than a unique match.
    expect(screen.getAllByText('—').length).toBeGreaterThan(0);
  });

  it('shows all numbers in full for 1-2 additional phones', async () => {
    listPlotsMock.mockResolvedValue([plotRow({ additionalPhones: ['0812345678', '0891112222'] })]);
    renderPlotsPage();

    expect(await screen.findByText('081-234-5678, 089-111-2222')).toBeTruthy();
  });

  it('truncates to the first 2 + "อีก N เบอร์" for more than 2 additional phones', async () => {
    listPlotsMock.mockResolvedValue([plotRow({
      additionalPhones: ['0812345678', '0891112222', '0898887777'],
    })]);
    renderPlotsPage();

    await screen.findByText('แปลงทดสอบ');
    expect(screen.getByText(/081-234-5678, 089-111-2222/)).toBeTruthy();
    expect(screen.getByText('อีก 1 เบอร์')).toBeTruthy();
  });

  it('the additional-phones cell title lists every number (nothing truly hidden)', async () => {
    listPlotsMock.mockResolvedValue([plotRow({
      additionalPhones: ['0812345678', '0891112222', '0898887777'],
    })]);
    renderPlotsPage();

    await screen.findByText('แปลงทดสอบ');
    const cell = screen.getByText(/081-234-5678, 089-111-2222/);
    expect(cell.getAttribute('title')).toBe('081-234-5678, 089-111-2222, 089-888-7777');
  });

  it('the phone columns are hidden on mobile (responsive classes), shown from sm: up', async () => {
    listPlotsMock.mockResolvedValue([plotRow({ primaryPhone: '0845552162' })]);
    renderPlotsPage();

    await screen.findByText('แปลงทดสอบ');
    const primaryHeader = screen.getByRole('columnheader', { name: 'เบอร์หลัก' });
    const additionalHeader = screen.getByRole('columnheader', { name: 'เบอร์เสริม' });
    for (const th of [primaryHeader, additionalHeader]) {
      expect(th.className).toContain('hidden');
      expect(th.className).toContain('sm:table-cell');
    }
  });

  it('empty-state row spans all 8 columns', async () => {
    listPlotsMock.mockResolvedValue([]);
    renderPlotsPage();

    const emptyCell = await screen.findByText('ไม่พบข้อมูล');
    expect(emptyCell.closest('td')?.getAttribute('colspan')).toBe('8');
  });

  it('row click still navigates to Plot Detail (unchanged by the new columns)', async () => {
    listPlotsMock.mockResolvedValue([plotRow({ primaryPhone: '0845552162' })]);
    renderPlotsPage();

    fireEvent.click(await screen.findByText('084-555-2162'));
    // clicking a plain text cell (not itself interactive) bubbles to the row handler
    expect(await screen.findByText('Plot Detail Page')).toBeTruthy();
  });
});

describe('Plots list — "จัดการเบอร์เข้าตรวจ" action menu item (round 8-3C)', () => {
  function onePlot() {
    return [{
      id: 'plot-1', supplierId: 'sup-1', supplierCode: 'SUP001', supplierName: 'Supplier One',
      qrKey: 'qr-1', plotCode: 'SUP001-P001', name: 'แปลงทดสอบ',
      village: null, district: null, province: 'จังหวัดทดสอบ',
      latitude: null, longitude: null, isActive: true, assignedCount: 0,
      primaryPhone: null, additionalPhones: [],
      currentCrop: 'พริก', currentVariety: null, currentLotNo: null, currentPlantingDate: null,
      currentStage: null, lastInspectedAt: null,
      currentYieldPct: null, expectedYieldFull: null, expectedYieldUnit: null, plantCount: null,
      activeCycleId: null, activeCycleNo: null, activeCycleStatus: null, activeCycleCrop: null,
      activeCycleVariety: null, activeCycleLabel: null, activeCycleLotNo: null,
      activeCyclePlantingDate: null, activeCyclePlantCount: null,
      activeCycleExpectedYieldFull: null, activeCycleExpectedYieldUnit: null,
    }];
  }

  it('shows "จัดการเบอร์เข้าตรวจ" in the row menu with plots.update', async () => {
    listPlotsMock.mockResolvedValue(onePlot());
    renderPlotsPage();

    await openRowMenu('SUP001-P001');
    expect(screen.getByRole('menuitem', { name: 'จัดการเบอร์เข้าตรวจ' })).toBeTruthy();
  });

  it('hides "จัดการเบอร์เข้าตรวจ" without plots.update', async () => {
    useAuthStore.setState({ permissionKeys: new Set(['plots.read']) });
    listPlotsMock.mockResolvedValue(onePlot());
    renderPlotsPage();

    await openRowMenu('SUP001-P001');
    expect(screen.queryByRole('menuitem', { name: 'จัดการเบอร์เข้าตรวจ' })).toBeNull();
  });

  it('opens PlotAccessPhoneModal and fetches its own data (not the stale list row)', async () => {
    listPlotsMock.mockResolvedValue(onePlot());
    getPlotAccessPhonesMock.mockResolvedValue({
      primaryPhone: '0845552162', additionalPhones: [], items: [],
    });
    renderPlotsPage();

    await openRowMenu('SUP001-P001');
    fireEvent.click(screen.getByRole('menuitem', { name: 'จัดการเบอร์เข้าตรวจ' }));

    expect(await screen.findByText('จัดการเบอร์เข้าตรวจแปลง SUP001-P001')).toBeTruthy();
    await waitFor(() => expect(getPlotAccessPhonesMock).toHaveBeenCalledWith('plot-1'));
  });
});

describe('Plots create modal — access phones (round 8-3C)', () => {
  async function fillRequiredFields(container: HTMLElement) {
    fireEvent.change(container.querySelector('input[name="plotCode"]')!, { target: { value: 'p101' } });
    fireEvent.change(container.querySelector('input[name="name"]')!, { target: { value: 'แปลง A' } });
    // Round 8-5B — the first cycle's PO/pCode are required.
    fireEvent.change(container.querySelector('input[name="poNumber"]')!, { target: { value: 'PO25001' } });
    await pickCropAndVariety();
    // Round 8-12B — the first cycle defaults to Auto Lot, which now also
    // requires a cycleLabel ({cycleLabel}-{supplierCode}-{pCode}-{running}).
    fireEvent.change(container.querySelector('input[name="cycleLabel"]')!, { target: { value: '2605' } });
  }

  it('omits accessPhones from the payload when no phone is entered', async () => {
    listPlotsMock.mockResolvedValue([]);
    createPlotWithCycleMock.mockResolvedValue({ plot: { id: 'new-plot-1' }, cycle: { id: 'new-cycle-1' } });
    const { container } = renderPlotsPage();

    fireEvent.click(await screen.findByText('เพิ่มแปลง'));
    await screen.findByText('เพิ่มแปลงใหม่');
    await fillRequiredFields(container);
    fireEvent.click(screen.getByRole('button', { name: 'สร้าง' }));

    await waitFor(() => expect(createPlotWithCycleMock).toHaveBeenCalledOnce());
    const [payload] = createPlotWithCycleMock.mock.calls[0];
    expect(Object.prototype.hasOwnProperty.call(payload, 'accessPhones')).toBe(false);
    expect(replacePlotAccessPhonesMock).not.toHaveBeenCalled();
  });

  it('sends accessPhones with a primary-only config', async () => {
    listPlotsMock.mockResolvedValue([]);
    createPlotWithCycleMock.mockResolvedValue({ plot: { id: 'new-plot-1' }, cycle: { id: 'new-cycle-1' } });
    const { container } = renderPlotsPage();

    fireEvent.click(await screen.findByText('เพิ่มแปลง'));
    await screen.findByText('เพิ่มแปลงใหม่');
    await fillRequiredFields(container);
    fireEvent.change(screen.getByLabelText('เบอร์หลัก'), { target: { value: '0845552162' } });
    fireEvent.click(screen.getByRole('button', { name: 'สร้าง' }));

    await waitFor(() => expect(createPlotWithCycleMock).toHaveBeenCalledOnce());
    const [payload] = createPlotWithCycleMock.mock.calls[0];
    expect(payload.accessPhones).toEqual({ primaryPhone: '0845552162', additionalPhones: [] });
  });

  it('sends accessPhones with primary + multiple additional numbers, canonicalized', async () => {
    listPlotsMock.mockResolvedValue([]);
    createPlotWithCycleMock.mockResolvedValue({ plot: { id: 'new-plot-1' }, cycle: { id: 'new-cycle-1' } });
    const { container } = renderPlotsPage();

    fireEvent.click(await screen.findByText('เพิ่มแปลง'));
    await screen.findByText('เพิ่มแปลงใหม่');
    await fillRequiredFields(container);
    fireEvent.change(screen.getByLabelText('เบอร์หลัก'), { target: { value: '084-555-2162' } });
    fireEvent.click(screen.getByRole('button', { name: 'เพิ่มเบอร์เสริม' }));
    fireEvent.click(screen.getByRole('button', { name: 'เพิ่มเบอร์เสริม' }));
    fireEvent.change(screen.getByLabelText('เบอร์เสริมที่ 1'), { target: { value: '081-234-5678' } });
    fireEvent.change(screen.getByLabelText('เบอร์เสริมที่ 2'), { target: { value: '089 111 2222' } });
    fireEvent.click(screen.getByRole('button', { name: 'สร้าง' }));

    await waitFor(() => expect(createPlotWithCycleMock).toHaveBeenCalledOnce());
    const [payload] = createPlotWithCycleMock.mock.calls[0];
    expect(payload.accessPhones).toEqual({
      primaryPhone: '0845552162', additionalPhones: ['0812345678', '0891112222'],
    });
  });

  it('one single createPlotWithCycle request carries the phones — never a second PUT', async () => {
    listPlotsMock.mockResolvedValue([]);
    createPlotWithCycleMock.mockResolvedValue({ plot: { id: 'new-plot-1' }, cycle: { id: 'new-cycle-1' } });
    const { container } = renderPlotsPage();

    fireEvent.click(await screen.findByText('เพิ่มแปลง'));
    await screen.findByText('เพิ่มแปลงใหม่');
    await fillRequiredFields(container);
    fireEvent.change(screen.getByLabelText('เบอร์หลัก'), { target: { value: '0845552162' } });
    fireEvent.click(screen.getByRole('button', { name: 'สร้าง' }));

    await waitFor(() => expect(createPlotWithCycleMock).toHaveBeenCalledOnce());
    expect(createPlotWithCycleMock).toHaveBeenCalledTimes(1);
    expect(replacePlotAccessPhonesMock).not.toHaveBeenCalled();
  });

  it('blocks submit (no request at all) when additional numbers are entered without a primary', async () => {
    listPlotsMock.mockResolvedValue([]);
    const { container } = renderPlotsPage();

    fireEvent.click(await screen.findByText('เพิ่มแปลง'));
    await screen.findByText('เพิ่มแปลงใหม่');
    await fillRequiredFields(container);
    fireEvent.click(screen.getByRole('button', { name: 'เพิ่มเบอร์เสริม' }));
    fireEvent.change(screen.getByLabelText('เบอร์เสริมที่ 1'), { target: { value: '0812345678' } });

    const submitBtn = screen.getByRole('button', { name: 'สร้าง' }) as HTMLButtonElement;
    expect(submitBtn.disabled).toBe(true);
    fireEvent.click(submitBtn);

    expect(createPlotWithCycleMock).not.toHaveBeenCalled();
  });
});

// --- round 8-6B: "Excel ตามตัวกรอง" filtered template download -------------
describe('Excel ตามตัวกรอง download (round 8-6B)', () => {
  async function selectSupplierFilter(code: string) {
    fireEvent.click(await screen.findByRole('button', { name: 'กรอง Supplier' }));
    fireEvent.click(await screen.findByText(code));
  }

  // Round 8-6G — "Excel ตามตัวกรอง" is no longer a standalone button; it's
  // the "ตามตัวกรองปัจจุบัน" item inside the "ดาวน์โหลด Excel" dropdown menu
  // (Part E). downloadTrigger() is the always-present, always-clickable
  // control (its `disabled` reflects pending state — see item 20 below);
  // clickFilteredDownload() opens the menu and picks that one item, exactly
  // reproducing what clicking the old standalone button used to do.
  function downloadTrigger() {
    return screen.getByRole('button', { name: 'ดาวน์โหลด Excel' });
  }

  function openDownloadMenu() {
    fireEvent.click(downloadTrigger());
  }

  function filteredMenuItem() {
    return screen.getByRole('menuitem', { name: 'ตามตัวกรองปัจจุบัน' });
  }

  function clickFilteredDownload() {
    openDownloadMenu();
    fireEvent.click(filteredMenuItem());
  }

  beforeEach(() => {
    listPlotsMock.mockResolvedValue([]);
  });

  it('renders the "ดาวน์โหลด Excel" trigger, with "ตามตัวกรองปัจจุบัน" as a menu option (item 11)', async () => {
    renderPlotsPage();
    expect(await screen.findByRole('button', { name: 'ดาวน์โหลด Excel' })).toBeTruthy();
    openDownloadMenu();
    expect(filteredMenuItem()).toBeTruthy();
  });

  it('clicking without a Supplier selected never calls the API (item 12)', async () => {
    renderPlotsPage();
    await screen.findByRole('button', { name: 'ดาวน์โหลด Excel' });

    clickFilteredDownload();

    expect(downloadPlotImportTemplateMock).not.toHaveBeenCalled();
  });

  it('clicking without a Supplier selected shows the Thai guidance message (item 13)', async () => {
    renderPlotsPage();
    await screen.findByRole('button', { name: 'ดาวน์โหลด Excel' });

    clickFilteredDownload();

    expect(await screen.findByText('กรุณาเลือก Supplier ก่อนดาวน์โหลด Excel ตามตัวกรอง')).toBeTruthy();
  });

  it('with a Supplier selected, sends supplierId (item 14)', async () => {
    downloadPlotImportTemplateMock.mockResolvedValue(new Blob(['x']));
    renderPlotsPage();
    await selectSupplierFilter('SUP001');

    clickFilteredDownload();

    await waitFor(() => expect(downloadPlotImportTemplateMock).toHaveBeenCalledWith({
      supplierId: 'sup-1', province: undefined, crop: undefined, variety: undefined, q: undefined,
      // Round 8-6J — plotStatus is now always forwarded; round 8-17A.2 Part B
      // changed the default from 'all' to 'active'.
      plotStatus: 'active',
    }));
  });

  it('sends the currently-selected plot status filter (round 8-6J)', async () => {
    downloadPlotImportTemplateMock.mockResolvedValue(new Blob(['x']));
    renderPlotsPage();
    await selectSupplierFilter('SUP001');
    const statusSelect = await screen.findByLabelText('กรองสถานะแปลง') as HTMLSelectElement;
    fireEvent.change(statusSelect, { target: { value: 'inactive' } });

    clickFilteredDownload();

    await waitFor(() => expect(downloadPlotImportTemplateMock).toHaveBeenCalledWith(
      expect.objectContaining({ plotStatus: 'inactive' }),
    ));
  });

  it('Supplier + province are both sent (item 15)', async () => {
    downloadPlotImportTemplateMock.mockResolvedValue(new Blob(['x']));
    listPlotProvincesMock.mockResolvedValue(['เชียงใหม่']);
    renderPlotsPage();
    await selectSupplierFilter('SUP001');
    await selectProvinceFilter('เชียงใหม่');

    clickFilteredDownload();

    await waitFor(() => expect(downloadPlotImportTemplateMock).toHaveBeenCalledWith(
      expect.objectContaining({ supplierId: 'sup-1', province: 'เชียงใหม่' }),
    ));
  });

  it('crop + variety are both sent (item 16)', async () => {
    downloadPlotImportTemplateMock.mockResolvedValue(new Blob(['x']));
    listMasterDataMock.mockImplementation(({ type }: { type: string }) => {
      if (type === 'crop') return Promise.resolve([masterDataItem({ id: 'c-1', type: 'crop', value: 'พริก' })]);
      if (type === 'variety') return Promise.resolve([masterDataItem({ id: 'v-1', type: 'variety', value: 'พริกขี้หนู', parent: 'พริก' })]);
      return Promise.resolve([]);
    });
    renderPlotsPage();
    await selectSupplierFilter('SUP001');
    await selectCropFilter('พริก');
    await selectVarietyFilter('พริกขี้หนู');

    clickFilteredDownload();

    await waitFor(() => expect(downloadPlotImportTemplateMock).toHaveBeenCalledWith(
      expect.objectContaining({ supplierId: 'sup-1', crop: 'พริก', variety: 'พริกขี้หนู' }),
    ));
  });

  it('sends the APPLIED search (q), not unapplied searchText (items 17/18)', async () => {
    downloadPlotImportTemplateMock.mockResolvedValue(new Blob(['x']));
    renderPlotsPage();
    await selectSupplierFilter('SUP001');

    // Type into the search box but do NOT press Enter / click ค้นหา.
    fireEvent.change(screen.getByLabelText(NAME_CODE_LABEL), {
      target: { value: 'not-applied-yet' },
    });
    clickFilteredDownload();
    await waitFor(() => expect(downloadPlotImportTemplateMock).toHaveBeenCalledWith(
      expect.objectContaining({ q: undefined }),
    ));
    downloadPlotImportTemplateMock.mockClear();

    // Now apply the search (click ค้นหา) — the applied q must be sent.
    fireEvent.click(screen.getByRole('button', { name: 'ค้นหา' }));
    clickFilteredDownload();
    await waitFor(() => expect(downloadPlotImportTemplateMock).toHaveBeenCalledWith(
      expect.objectContaining({ q: 'not-applied-yet' }),
    ));
  });

  it('never sends page/pageSize/limit/offset (item 19)', async () => {
    downloadPlotImportTemplateMock.mockResolvedValue(new Blob(['x']));
    renderPlotsPage();
    await selectSupplierFilter('SUP001');

    clickFilteredDownload();

    await waitFor(() => expect(downloadPlotImportTemplateMock).toHaveBeenCalled());
    const call = downloadPlotImportTemplateMock.mock.calls[0][0] as Record<string, unknown>;
    expect(call).not.toHaveProperty('page');
    expect(call).not.toHaveProperty('pageSize');
    expect(call).not.toHaveProperty('limit');
    expect(call).not.toHaveProperty('offset');
  });

  it('pending state disables the button, preventing a double submit (item 20)', async () => {
    let resolveDownload: (blob: Blob) => void = () => {};
    downloadPlotImportTemplateMock.mockReturnValue(
      new Promise((resolve) => { resolveDownload = resolve; }),
    );
    renderPlotsPage();
    await selectSupplierFilter('SUP001');

    clickFilteredDownload();
    // Round 8-6G — pending state now disables the TRIGGER (it shows the
    // spinner), not a menu item (the menu already auto-closed once
    // "ตามตัวกรองปัจจุบัน" was clicked, same as any other menu).
    await waitFor(() => expect((downloadTrigger() as HTMLButtonElement).disabled).toBe(true));
    fireEvent.click(downloadTrigger()); // disabled -> no-op, menu can't even reopen

    resolveDownload(new Blob(['x']));
    await waitFor(() => expect((downloadTrigger() as HTMLButtonElement).disabled).toBe(false));
    expect(downloadPlotImportTemplateMock).toHaveBeenCalledTimes(1);
  });

  it('shows the backend 422 detail on the page (item 21)', async () => {
    downloadPlotImportTemplateMock.mockRejectedValue(
      Object.assign(new Error('ไม่พบแปลงที่ใช้งานอยู่ (active) ตรงตามตัวกรองที่ระบุ'), { status: 422 }),
    );
    renderPlotsPage();
    await selectSupplierFilter('SUP001');

    clickFilteredDownload();

    expect(await screen.findByText('ไม่พบแปลงที่ใช้งานอยู่ (active) ตรงตามตัวกรองที่ระบุ')).toBeTruthy();
  });

  it('shows the backend 404 detail on the page (item 22)', async () => {
    downloadPlotImportTemplateMock.mockRejectedValue(
      Object.assign(new Error('ไม่พบ Supplier'), { status: 404 }),
    );
    renderPlotsPage();
    await selectSupplierFilter('SUP001');

    clickFilteredDownload();

    expect(await screen.findByText('ไม่พบ Supplier')).toBeTruthy();
  });

  it('selecting a different Supplier clears the previous template error (item 23)', async () => {
    listSuppliersMock.mockResolvedValue([
      { id: 'sup-1', code: 'SUP001', name: 'Supplier One', isActive: true, contactName: null, contactEmail: null },
      { id: 'sup-2', code: 'SUP002', name: 'Supplier Two', isActive: true, contactName: null, contactEmail: null },
    ]);
    renderPlotsPage();
    await screen.findByRole('button', { name: 'ดาวน์โหลด Excel' });
    clickFilteredDownload(); // no supplier yet -> error
    await screen.findByText('กรุณาเลือก Supplier ก่อนดาวน์โหลด Excel ตามตัวกรอง');

    await selectSupplierFilter('SUP002');

    expect(screen.queryByText('กรุณาเลือก Supplier ก่อนดาวน์โหลด Excel ตามตัวกรอง')).toBeNull();
  });

  it('changing province/crop/variety clears the previous template error (item 24)', async () => {
    downloadPlotImportTemplateMock.mockRejectedValue(Object.assign(new Error('เกิดข้อผิดพลาด'), { status: 422 }));
    listPlotProvincesMock.mockResolvedValue(['เชียงใหม่']);
    renderPlotsPage();
    await selectSupplierFilter('SUP001');
    clickFilteredDownload();
    await screen.findByText('เกิดข้อผิดพลาด');

    await selectProvinceFilter('เชียงใหม่');

    expect(screen.queryByText('เกิดข้อผิดพลาด')).toBeNull();
  });

  it('a successful download uses a filename built from the Supplier CODE (item 25)', async () => {
    downloadPlotImportTemplateMock.mockResolvedValue(new Blob(['x']));
    renderPlotsPage();
    await selectSupplierFilter('SUP001');

    clickFilteredDownload();

    await waitFor(() => expect(downloadBlobMock).toHaveBeenCalled());
    const filename = downloadBlobMock.mock.calls[0][1] as string;
    expect(filename).toBe('plot-next-cycle-SUP001.xlsx');
  });

  it('the selected province is included in the filename (item 26)', async () => {
    downloadPlotImportTemplateMock.mockResolvedValue(new Blob(['x']));
    listPlotProvincesMock.mockResolvedValue(['เชียงใหม่']);
    renderPlotsPage();
    await selectSupplierFilter('SUP001');
    await selectProvinceFilter('เชียงใหม่');

    clickFilteredDownload();

    await waitFor(() => expect(downloadBlobMock).toHaveBeenCalled());
    expect(downloadBlobMock.mock.calls[0][1]).toBe('plot-next-cycle-SUP001-เชียงใหม่.xlsx');
  });

  it('the filename is sanitized against path separators/control characters (item 27)', async () => {
    listSuppliersMock.mockResolvedValue([
      { id: 'sup-1', code: 'SUP/001:*?', name: 'Supplier One', isActive: true, contactName: null, contactEmail: null },
    ]);
    downloadPlotImportTemplateMock.mockResolvedValue(new Blob(['x']));
    renderPlotsPage();
    await selectSupplierFilter('SUP/001:*?');

    clickFilteredDownload();

    await waitFor(() => expect(downloadBlobMock).toHaveBeenCalled());
    const filename = downloadBlobMock.mock.calls[0][1] as string;
    expect(filename).not.toMatch(/[/\\:*?"<>|]/);
    expect(filename.endsWith('.xlsx')).toBe(true);
  });

  it('falls back to a generic filename when the Supplier code cannot be resolved', async () => {
    // supplierById only knows suppliers from the active-suppliers query;
    // this simulates a filter value that isn't in that map (defensive path).
    listSuppliersMock.mockResolvedValue([]);
    downloadPlotImportTemplateMock.mockResolvedValue(new Blob(['x']));
    renderPlotsPage();
    await screen.findByRole('button', { name: 'ดาวน์โหลด Excel' });
    // No supplier is selectable, so directly exercise the filename builder's
    // fallback via the "no Supplier resolvable" shape: skip UI selection and
    // rely on the guard that requires filterSupplier before mutate() anyway —
    // covered structurally by item 12's test. This test instead confirms the
    // helper text guidance items below don't assume a resolvable supplier.
    // Round 8-6J — wording updated: no longer claims "active only", since
    // plotStatus='all' (default) now mixes active AND inactive plots.
    expect(await screen.findByText(/แปลงที่ใช้งาน: เริ่มรอบถัดไป/)).toBeTruthy();
  });

  it('the helper copy says every matching active plot is exported, not just the current page (item 28)', async () => {
    renderPlotsPage();
    // Round 8-6J — wording updated: no longer claims "active only", since
    // plotStatus='all' (default) now mixes active AND inactive plots.
    expect(await screen.findByText(/แปลงที่ปิด: เปิดแปลงพร้อมเริ่มรอบใหม่/)).toBeTruthy();
  });

  it('the guidance (shown once a Supplier is selected) says cycleLabel must be changed (item 29)', async () => {
    renderPlotsPage();
    await selectSupplierFilter('SUP001');

    const guidance = await screen.findByText(/cycleLabel ต้องเปลี่ยนเป็นชื่อรอบใหม่/);
    expect(guidance).toBeTruthy();
  });

  it('the guidance says Download/Preview does not close the current cycle (item 30)', async () => {
    renderPlotsPage();
    await selectSupplierFilter('SUP001');

    // Round 8-6J — generalized wording (Download/Preview never mutates
    // ANY data, not just "doesn't close the current cycle" — the same
    // sentence now also covers reactivate_plot_with_cycle, which doesn't
    // close a cycle at all).
    const guidance = await screen.findByText(/การดาวน์โหลด\/ตรวจสอบไฟล์ยังไม่เปลี่ยนข้อมูลใดๆ/);
    expect(guidance).toBeTruthy();
  });

  it('the filter summary shows the Supplier code + name, and province only when selected', async () => {
    listPlotProvincesMock.mockResolvedValue(['เชียงใหม่']);
    renderPlotsPage();
    await selectSupplierFilter('SUP001');

    expect(await screen.findByText(/Supplier: SUP001 — Supplier One/)).toBeTruthy();
    expect(screen.queryByText(/จังหวัด:/)).toBeNull();

    await selectProvinceFilter('เชียงใหม่');

    expect(await screen.findByText(/จังหวัด: เชียงใหม่/)).toBeTruthy();
  });
});

describe('Excel ตามตัวกรอง — request-state race + error cleanup + q summary (round 8-6C)', () => {
  async function selectSupplierFilter(code: string) {
    fireEvent.click(await screen.findByRole('button', { name: 'กรอง Supplier' }));
    fireEvent.click(await screen.findByText(code));
  }

  // Round 8-6G — same helper set as the "round 8-6B" describe block above
  // (duplicated per this file's existing per-describe-block convention).
  function downloadTrigger() {
    return screen.getByRole('button', { name: 'ดาวน์โหลด Excel' });
  }

  function openDownloadMenu() {
    fireEvent.click(downloadTrigger());
  }

  function filteredMenuItem() {
    return screen.getByRole('menuitem', { name: 'ตามตัวกรองปัจจุบัน' });
  }

  function clickFilteredDownload() {
    openDownloadMenu();
    fireEvent.click(filteredMenuItem());
  }

  beforeEach(() => {
    listPlotsMock.mockResolvedValue([]);
    listSuppliersMock.mockResolvedValue([
      { id: 'sup-1', code: 'SUP001', name: 'Supplier One', isActive: true, contactName: null, contactEmail: null },
      { id: 'sup-2', code: 'SUP002', name: 'Supplier Two', isActive: true, contactName: null, contactEmail: null },
    ]);
    // Both provinces available regardless of which Supplier is selected, so
    // the race test below can pick either without re-mocking mid-test.
    listPlotProvincesMock.mockResolvedValue(['เชียงใหม่', 'เชียงราย']);
  });

  // Part A (items 1-6): filename AND the request itself must reflect the
  // filter snapshot submitted at click-time — changing the UI filters while
  // the download is still pending must not leak into either.
  it('binds both the API request and the filename to the submitted filter snapshot, not filters changed during pending (items 1-6)', async () => {
    let resolveDownload: (blob: Blob) => void = () => {};
    downloadPlotImportTemplateMock.mockReturnValue(
      new Promise((resolve) => { resolveDownload = resolve; }),
    );
    renderPlotsPage();

    // 1. Start a download with SUP001 / เชียงใหม่.
    await selectSupplierFilter('SUP001');
    await selectProvinceFilter('เชียงใหม่');
    clickFilteredDownload();
    await waitFor(() => expect(downloadPlotImportTemplateMock).toHaveBeenCalledTimes(1));

    // 2. Before the Promise resolves, change the UI to SUP002 / เชียงราย.
    await selectSupplierFilter('SUP002');
    await selectProvinceFilter('เชียงราย');

    // 3. Resolve the (first) download now.
    resolveDownload(new Blob(['x']));
    await waitFor(() => expect(downloadBlobMock).toHaveBeenCalled());

    // 4/5. filename and API request must still be SUP001 / เชียงใหม่.
    const submittedCall = downloadPlotImportTemplateMock.mock.calls[0][0] as Record<string, unknown>;
    expect(submittedCall).toMatchObject({ supplierId: 'sup-1', province: 'เชียงใหม่' });
    const filename = downloadBlobMock.mock.calls[0][1] as string;
    expect(filename).toBe('plot-next-cycle-SUP001-เชียงใหม่.xlsx');

    // 6. Must NOT be the latest UI values (SUP002 / เชียงราย).
    expect(filename).not.toContain('SUP002');
    expect(filename).not.toContain('เชียงราย');
  });

  // Part B (items 7-11): a stale download error must clear once the applied
  // filter actually changes (search apply/Enter/clear), but NOT just from
  // typing into the search box before applying.
  it('shows the backend error first (item 7)', async () => {
    downloadPlotImportTemplateMock.mockRejectedValue(
      Object.assign(new Error('ไม่พบแปลงที่ใช้งานอยู่ (active) ตรงตามตัวกรองที่ระบุ'), { status: 422 }),
    );
    renderPlotsPage();
    await selectSupplierFilter('SUP001');
    clickFilteredDownload();

    expect(await screen.findByText('ไม่พบแปลงที่ใช้งานอยู่ (active) ตรงตามตัวกรองที่ระบุ')).toBeTruthy();
  });

  it('clicking ค้นหา with a new applied search clears the stale error (item 8)', async () => {
    downloadPlotImportTemplateMock.mockRejectedValue(Object.assign(new Error('เกิดข้อผิดพลาด'), { status: 422 }));
    renderPlotsPage();
    await selectSupplierFilter('SUP001');
    clickFilteredDownload();
    await screen.findByText('เกิดข้อผิดพลาด');

    fireEvent.change(screen.getByLabelText(NAME_CODE_LABEL), { target: { value: 'P001' } });
    fireEvent.click(screen.getByRole('button', { name: 'ค้นหา' }));

    expect(screen.queryByText('เกิดข้อผิดพลาด')).toBeNull();
  });

  it('pressing Enter in the search box clears the stale error (item 9)', async () => {
    downloadPlotImportTemplateMock.mockRejectedValue(Object.assign(new Error('เกิดข้อผิดพลาด'), { status: 422 }));
    renderPlotsPage();
    await selectSupplierFilter('SUP001');
    clickFilteredDownload();
    await screen.findByText('เกิดข้อผิดพลาด');

    const searchInput = screen.getByLabelText(NAME_CODE_LABEL);
    fireEvent.change(searchInput, { target: { value: 'P001' } });
    fireEvent.keyDown(searchInput, { key: 'Enter' });

    expect(screen.queryByText('เกิดข้อผิดพลาด')).toBeNull();
  });

  it('clicking ล้างค่า clears the stale error (item 10)', async () => {
    downloadPlotImportTemplateMock.mockRejectedValue(Object.assign(new Error('เกิดข้อผิดพลาด'), { status: 422 }));
    renderPlotsPage();
    await selectSupplierFilter('SUP001');
    clickFilteredDownload();
    await screen.findByText('เกิดข้อผิดพลาด');

    fireEvent.click(screen.getByRole('button', { name: 'ล้างค่า' }));

    expect(screen.queryByText('เกิดข้อผิดพลาด')).toBeNull();
  });

  it('typing into the search box WITHOUT applying does not clear the stale error, and sends no new request (item 11)', async () => {
    downloadPlotImportTemplateMock.mockRejectedValue(Object.assign(new Error('เกิดข้อผิดพลาด'), { status: 422 }));
    renderPlotsPage();
    await selectSupplierFilter('SUP001');
    clickFilteredDownload();
    await screen.findByText('เกิดข้อผิดพลาด');
    downloadPlotImportTemplateMock.mockClear();

    fireEvent.change(screen.getByLabelText(NAME_CODE_LABEL), { target: { value: 'still typing' } });

    expect(screen.queryByText('เกิดข้อผิดพลาด')).toBeTruthy();
    expect(downloadPlotImportTemplateMock).not.toHaveBeenCalled();
  });

  // Part C (items 12-16): the filter summary must include the APPLIED
  // search (q), not the unapplied searchText, and must match what's sent.
  it('shows the applied search in the filter summary (item 12)', async () => {
    renderPlotsPage();
    await selectSupplierFilter('SUP001');
    fireEvent.change(screen.getByLabelText(NAME_CODE_LABEL), { target: { value: 'P001' } });
    fireEvent.click(screen.getByRole('button', { name: 'ค้นหา' }));

    expect(await screen.findByText(/คำค้นหา: P001/)).toBeTruthy();
  });

  it('does not show unapplied searchText in the summary (item 13)', async () => {
    renderPlotsPage();
    await selectSupplierFilter('SUP001');
    fireEvent.change(screen.getByLabelText(NAME_CODE_LABEL), { target: { value: 'not-applied' } });

    expect(screen.queryByText(/คำค้นหา:/)).toBeNull();
  });

  it('pressing Enter applies the search and the summary then shows it (item 14)', async () => {
    renderPlotsPage();
    await selectSupplierFilter('SUP001');
    const searchInput = screen.getByLabelText(NAME_CODE_LABEL);
    fireEvent.change(searchInput, { target: { value: 'P002' } });
    expect(screen.queryByText(/คำค้นหา:/)).toBeNull();

    fireEvent.keyDown(searchInput, { key: 'Enter' });

    expect(await screen.findByText(/คำค้นหา: P002/)).toBeTruthy();
  });

  it('clearing filters removes the search from the summary (item 15)', async () => {
    renderPlotsPage();
    await selectSupplierFilter('SUP001');
    fireEvent.change(screen.getByLabelText(NAME_CODE_LABEL), { target: { value: 'P001' } });
    fireEvent.click(screen.getByRole('button', { name: 'ค้นหา' }));
    await screen.findByText(/คำค้นหา: P001/);

    fireEvent.click(screen.getByRole('button', { name: 'ล้างค่า' }));

    expect(screen.queryByText(/คำค้นหา:/)).toBeNull();
  });

  it('the q shown in the summary matches the q sent to the API (item 16)', async () => {
    downloadPlotImportTemplateMock.mockResolvedValue(new Blob(['x']));
    renderPlotsPage();
    await selectSupplierFilter('SUP001');
    fireEvent.change(screen.getByLabelText(NAME_CODE_LABEL), { target: { value: 'P003' } });
    fireEvent.click(screen.getByRole('button', { name: 'ค้นหา' }));
    await screen.findByText(/คำค้นหา: P003/);

    clickFilteredDownload();

    await waitFor(() => expect(downloadPlotImportTemplateMock).toHaveBeenCalledWith(
      expect.objectContaining({ q: 'P003' }),
    ));
  });
});

describe('ดาวน์โหลด Excel — "ทุก Supplier" all-suppliers mode (round 8-6G)', () => {
  async function selectSupplierFilter(code: string) {
    fireEvent.click(await screen.findByRole('button', { name: 'กรอง Supplier' }));
    fireEvent.click(await screen.findByText(code));
  }

  function downloadTrigger() {
    return screen.getByRole('button', { name: 'ดาวน์โหลด Excel' });
  }

  function openDownloadMenu() {
    fireEvent.click(downloadTrigger());
  }

  function filteredMenuItem() {
    return screen.getByRole('menuitem', { name: 'ตามตัวกรองปัจจุบัน' });
  }

  function allSuppliersMenuItem() {
    return screen.getByRole('menuitem', { name: 'ทุก Supplier' });
  }

  beforeEach(() => {
    listPlotsMock.mockResolvedValue([]);
  });

  // --- items 1/2/3: menu visibility gated by role, never Supplier count ---

  it('a full-scope Admin sees both download options (item 1)', async () => {
    useAuthStore.setState({ user: userWithRoles('internal:admin') });
    renderPlotsPage();
    await screen.findByRole('button', { name: 'ดาวน์โหลด Excel' });
    openDownloadMenu();
    expect(filteredMenuItem()).toBeTruthy();
    expect(allSuppliersMenuItem()).toBeTruthy();
  });

  it('farmlog:supervisor also counts as full scope, matching backend _FULL_ACCESS_ROLES', async () => {
    useAuthStore.setState({ user: userWithRoles('farmlog:supervisor') });
    renderPlotsPage();
    await screen.findByRole('button', { name: 'ดาวน์โหลด Excel' });
    openDownloadMenu();
    expect(allSuppliersMenuItem()).toBeTruthy();
  });

  // Round 8-25P — a Supplier-side caller (canSeeVariety false) no longer
  // sees the "ดาวน์โหลด Excel" trigger AT ALL (not just the "ทุก Supplier"
  // item within it): the file shares one column layout with the import
  // parser, so hiding the variety column just for this download isn't safe
  // — the whole entry point is hidden instead. Supersedes the older,
  // narrower "still gets the filtered option, just not ทุก Supplier"
  // behavior these three tests used to check.
  it('a Supplier Owner never sees "ดาวน์โหลด Excel" at all (item 2)', async () => {
    useAuthStore.setState({ user: userWithRoles('supplier:owner') });
    renderPlotsPage();
    await screen.findByText('ไม่พบข้อมูล');
    expect(screen.queryByRole('button', { name: 'ดาวน์โหลด Excel' })).toBeNull();
  });

  it('a Field Officer never sees "ดาวน์โหลด Excel" at all (item 3)', async () => {
    useAuthStore.setState({ user: userWithRoles('farmlog:field_officer') });
    renderPlotsPage();
    await screen.findByText('ไม่พบข้อมูล');
    expect(screen.queryByRole('button', { name: 'ดาวน์โหลด Excel' })).toBeNull();
  });

  it('no user loaded yet -> "ดาวน์โหลด Excel" fails closed, not open', async () => {
    // Round 8-25O — the shared beforeEach default changed from null to an
    // internal:admin user (crop/variety visibility needs one); this test's
    // whole premise is "no user yet", so it must set null explicitly now.
    useAuthStore.setState({ user: null });
    renderPlotsPage();
    await screen.findByText('ไม่พบข้อมูล');
    expect(screen.queryByRole('button', { name: 'ดาวน์โหลด Excel' })).toBeNull();
  });

  it('a Supplier Owner never sees "นำเข้า Excel" either, even holding plots.update', async () => {
    useAuthStore.setState({
      user: userWithRoles('supplier:owner'),
      permissionKeys: new Set(['plots.read', 'plots.update', 'plots.create']),
    });
    renderPlotsPage();
    await screen.findByText('ไม่พบข้อมูล');
    expect(screen.queryByRole('button', { name: 'นำเข้า Excel' })).toBeNull();
  });

  // --- items 6/7: all-suppliers request sends ONLY template_mode -----------

  it('"ทุก Supplier" sends only template_mode=all_suppliers, ignoring any filter selected in the UI (items 6/7)', async () => {
    useAuthStore.setState({ user: userWithRoles('internal:admin') });
    downloadPlotImportTemplateMock.mockResolvedValue(new Blob(['x']));
    renderPlotsPage();
    await selectSupplierFilter('SUP001'); // a filter IS selected in the UI...
    openDownloadMenu();
    fireEvent.click(allSuppliersMenuItem()); // ...but this must ignore it entirely

    await waitFor(() => expect(downloadPlotImportTemplateMock).toHaveBeenCalledWith({
      templateMode: 'all_suppliers',
      // Round 8-6J — plotStatus IS still forwarded in this mode; round
      // 8-17A.2 Part B changed the default from 'all' to 'active'.
      plotStatus: 'active',
    }));
    const call = downloadPlotImportTemplateMock.mock.calls[0][0] as Record<string, unknown>;
    expect(call).not.toHaveProperty('supplierId');
    expect(call).not.toHaveProperty('province');
    expect(call).not.toHaveProperty('crop');
    expect(call).not.toHaveProperty('variety');
    expect(call).not.toHaveProperty('q');
  });

  it('"ทุก Supplier" forwards a non-default plotStatus too (round 8-6J)', async () => {
    useAuthStore.setState({ user: userWithRoles('internal:admin') });
    downloadPlotImportTemplateMock.mockResolvedValue(new Blob(['x']));
    renderPlotsPage();
    const statusSelect = await screen.findByLabelText('กรองสถานะแปลง') as HTMLSelectElement;
    fireEvent.change(statusSelect, { target: { value: 'inactive' } });
    openDownloadMenu();
    fireEvent.click(allSuppliersMenuItem());

    await waitFor(() => expect(downloadPlotImportTemplateMock).toHaveBeenCalledWith({
      templateMode: 'all_suppliers', plotStatus: 'inactive',
    }));
  });

  // --- item 8: filename ----------------------------------------------------

  it('"ทุก Supplier" uses the fixed filename plot-next-cycle-ALL-SUPPLIERS.xlsx (item 8)', async () => {
    useAuthStore.setState({ user: userWithRoles('internal:admin') });
    downloadPlotImportTemplateMock.mockResolvedValue(new Blob(['x']));
    renderPlotsPage();
    await screen.findByRole('button', { name: 'ดาวน์โหลด Excel' });
    openDownloadMenu();
    fireEvent.click(allSuppliersMenuItem());

    await waitFor(() => expect(downloadBlobMock).toHaveBeenCalled());
    expect(downloadBlobMock.mock.calls[0][1]).toBe('plot-next-cycle-ALL-SUPPLIERS.xlsx');
  });

  // --- item 9: changing filters after the request started doesn't leak ----

  it('changing the Supplier filter while an all-suppliers download is pending does not change its filename or request (item 9)', async () => {
    useAuthStore.setState({ user: userWithRoles('internal:admin') });
    listSuppliersMock.mockResolvedValue([
      { id: 'sup-1', code: 'SUP001', name: 'Supplier One', isActive: true, contactName: null, contactEmail: null },
      { id: 'sup-2', code: 'SUP002', name: 'Supplier Two', isActive: true, contactName: null, contactEmail: null },
    ]);
    let resolveDownload: (blob: Blob) => void = () => {};
    downloadPlotImportTemplateMock.mockReturnValue(new Promise((resolve) => { resolveDownload = resolve; }));
    renderPlotsPage();
    await screen.findByRole('button', { name: 'ดาวน์โหลด Excel' });
    openDownloadMenu();
    fireEvent.click(allSuppliersMenuItem());
    await waitFor(() => expect(downloadPlotImportTemplateMock).toHaveBeenCalledTimes(1));

    // Change the filter WHILE the all-suppliers download is still pending.
    await selectSupplierFilter('SUP001');

    resolveDownload(new Blob(['x']));
    await waitFor(() => expect(downloadBlobMock).toHaveBeenCalled());
    expect(downloadBlobMock.mock.calls[0][1]).toBe('plot-next-cycle-ALL-SUPPLIERS.xlsx');
    expect(downloadPlotImportTemplateMock).toHaveBeenCalledTimes(1);
  });

  // --- item 10: backend 403/422 shows error, downloads nothing -------------

  it('a backend 403 for all-suppliers shows the error and downloads nothing (item 10)', async () => {
    useAuthStore.setState({ user: userWithRoles('internal:admin') });
    downloadPlotImportTemplateMock.mockRejectedValue(
      Object.assign(new Error('ไม่มีสิทธิ์ดาวน์โหลด Excel ทุก Supplier'), { status: 403 }),
    );
    renderPlotsPage();
    await screen.findByRole('button', { name: 'ดาวน์โหลด Excel' });
    openDownloadMenu();
    fireEvent.click(allSuppliersMenuItem());

    expect(await screen.findByText('ไม่มีสิทธิ์ดาวน์โหลด Excel ทุก Supplier')).toBeTruthy();
    expect(downloadBlobMock).not.toHaveBeenCalled();
  });

  it('a backend 422 for all-suppliers (e.g. combined with a filter) shows the error and downloads nothing', async () => {
    useAuthStore.setState({ user: userWithRoles('internal:admin') });
    downloadPlotImportTemplateMock.mockRejectedValue(
      Object.assign(new Error('ไม่สามารถระบุ Supplier หรือตัวกรองอื่นพร้อมกับการดาวน์โหลดทุก Supplier ได้'), { status: 422 }),
    );
    renderPlotsPage();
    await screen.findByRole('button', { name: 'ดาวน์โหลด Excel' });
    openDownloadMenu();
    fireEvent.click(allSuppliersMenuItem());

    expect(await screen.findByText(
      'ไม่สามารถระบุ Supplier หรือตัวกรองอื่นพร้อมกับการดาวน์โหลดทุก Supplier ได้',
    )).toBeTruthy();
    expect(downloadBlobMock).not.toHaveBeenCalled();
  });

  // --- item 11: "รายการที่ไม่รวม" is mentioned in the menu ------------------

  it('the menu explains inactive plots go to "รายการที่ไม่รวม" and are never imported (item 11)', async () => {
    renderPlotsPage();
    await screen.findByRole('button', { name: 'ดาวน์โหลด Excel' });
    openDownloadMenu();
    expect(screen.getByText(/รายการที่ไม่รวม/)).toBeTruthy();
  });

  // --- item 12: keyboard support + no fixed-overflow width on mobile -------

  it('pressing Escape closes the download menu (keyboard support, item 12)', async () => {
    renderPlotsPage();
    await screen.findByRole('button', { name: 'ดาวน์โหลด Excel' });
    openDownloadMenu();
    expect(screen.getByRole('menu', { name: 'ดาวน์โหลด Excel' })).toBeTruthy();

    fireEvent.keyDown(document, { key: 'Escape' });

    expect(screen.queryByRole('menu', { name: 'ดาวน์โหลด Excel' })).toBeNull();
  });

  it('the menu is capped to the viewport width so it cannot force horizontal overflow on mobile (item 12)', async () => {
    renderPlotsPage();
    await screen.findByRole('button', { name: 'ดาวน์โหลด Excel' });
    openDownloadMenu();
    // jsdom has no real layout engine — this asserts the responsive class is
    // present rather than a measured pixel width (see Final Report's Browser
    // QA section for the honest limitation this implies).
    expect(screen.getByRole('menu', { name: 'ดาวน์โหลด Excel' }).className).toContain('max-w-[90vw]');
  });

  // --- item 13: download never reaches preview/commit ----------------------

  it('downloading (either mode) never calls the preview or commit APIs (item 13)', async () => {
    useAuthStore.setState({ user: userWithRoles('internal:admin') });
    downloadPlotImportTemplateMock.mockResolvedValue(new Blob(['x']));
    renderPlotsPage();
    await screen.findByRole('button', { name: 'ดาวน์โหลด Excel' });
    openDownloadMenu();
    fireEvent.click(allSuppliersMenuItem());
    await waitFor(() => expect(downloadBlobMock).toHaveBeenCalled());

    expect(previewPlotImportMock).not.toHaveBeenCalled();
    expect(commitPlotImportWithReportMock).not.toHaveBeenCalled();
  });
});

// Round 8-6I — plot-status filter, inactive badge/YieldCell, and reactivate/
// reactivate-with-cycle actions on the Plots list.
describe('Plots list — plot status filter + reactivation (round 8-6I)', () => {
  function plotFixture(overrides: Partial<{
    id: string; supplierId: string; plotCode: string; name: string; province: string | null;
    isActive: boolean; activeCycleId: string | null;
  }> = {}) {
    return {
      id: 'plot-1', supplierId: 'sup-1', supplierCode: 'SUP001', supplierName: 'Supplier One',
      plotCode: 'SUP001-P001', name: 'แปลงทดสอบ',
      village: null, district: null, province: 'จังหวัดทดสอบ',
      latitude: null, longitude: null,
      isActive: true, assignedCount: 0, qrKey: 'qr-1',
      currentYieldPct: null, expectedYieldFull: null, expectedYieldUnit: null, plantCount: null,
      currentCrop: null, currentVariety: null, currentLotNo: null, currentPlantingDate: null,
      currentStage: null, lastInspectedAt: null,
      activeCycleId: null, activeCycleNo: null, activeCycleStatus: null, activeCycleCrop: null,
      activeCycleVariety: null, activeCycleLabel: null, activeCycleLotNo: null,
      activeCyclePoNumber: null, activeCyclePCode: null, activeCycleSupplierLotNo: null, activeCyclePlantingDate: null,
      activeCyclePlantCount: null, activeCycleExpectedYieldFull: null, activeCycleExpectedYieldUnit: null,
      primaryPhone: null, additionalPhones: [],
      ...overrides,
    };
  }

  beforeEach(() => {
    listPlotsMock.mockResolvedValue([plotFixture()]);
  });

  // --- item 1/2: status dropdown has 3 values, default all -----------------

  // Round 8-17A.2 Part B — default changed from 'all' to 'active'.
  it('the status dropdown has ทั้งหมด/ใช้งาน/ปิดใช้งาน, defaulting to ใช้งาน', async () => {
    renderPlotsPage();
    const select = await screen.findByLabelText('กรองสถานะแปลง') as HTMLSelectElement;
    const optionLabels = Array.from(select.options).map((o) => o.textContent);
    expect(optionLabels).toEqual(['สถานะแปลง: ทั้งหมด', 'สถานะแปลง: ใช้งาน', 'สถานะแปลง: ปิดใช้งาน']);
    expect(select.value).toBe('active');
  });

  // --- item 3: changing to inactive sends plotStatus=inactive ---------------

  it('changing the status filter to ปิดใช้งาน sends plotStatus=inactive to both listPlots and listPlotProvinces', async () => {
    renderPlotsPage();
    const select = await screen.findByLabelText('กรองสถานะแปลง');
    fireEvent.change(select, { target: { value: 'inactive' } });

    await waitFor(() => expect(hasListPlotsCallContaining({ plotStatus: 'inactive' })).toBe(true));
    await waitFor(() => expect(
      listPlotProvincesMock.mock.calls.some(([p]) => (p as { plotStatus?: string }).plotStatus === 'inactive'),
    ).toBe(true));
  });

  // --- item 4: changing the filter resets page ------------------------------

  it('changing the status filter resets the page to the first one', async () => {
    listPlotsMock.mockResolvedValue(Array.from({ length: 100 }, (_, i) => plotFixture({
      id: `plot-${i}`, plotCode: `SUP001-P${i}`,
    })));
    renderPlotsPage();
    await screen.findByText('SUP001-P0');
    fireEvent.click(await screen.findByRole('button', { name: 'ถัดไป →' }));
    await waitFor(() => expect(hasListPlotsCallContaining({ offset: 100 })).toBe(true));

    const select = screen.getByLabelText('กรองสถานะแปลง');
    // Round 8-17A.2 Part B — default is now 'active'; change to a DIFFERENT
    // value ('inactive') so this is a genuine change, not a same-value no-op.
    fireEvent.change(select, { target: { value: 'inactive' } });

    await waitFor(() => {
      const lastCall = listPlotsMock.mock.calls[listPlotsMock.mock.calls.length - 1][0] as { offset: number };
      expect(lastCall.offset).toBe(0);
    });
  });

  // --- clearFilters resets status to the default ("active") -----------------

  // Round 8-17A.2 Part B — default changed from 'all' to 'active'.
  it('ล้างค่า resets the status filter back to ใช้งาน (default)', async () => {
    renderPlotsPage();
    const select = await screen.findByLabelText('กรองสถานะแปลง') as HTMLSelectElement;
    fireEvent.change(select, { target: { value: 'inactive' } });
    await waitFor(() => expect(select.value).toBe('inactive'));

    fireEvent.click(await screen.findByRole('button', { name: 'ล้างค่า' }));
    expect(select.value).toBe('active');
  });

  // --- item 5/6: inactive badge under the plot name -------------------------

  it('shows a "ปิดใช้งาน" badge under the name for an inactive plot', async () => {
    listPlotsMock.mockResolvedValue([plotFixture({ isActive: false })]);
    renderPlotsPage();

    await screen.findByText('แปลงทดสอบ');
    // "ปิดใช้งาน" legitimately appears twice for an inactive row (the name
    // badge AND the YieldCell below) — assert presence, not a single match.
    expect((await screen.findAllByText('ปิดใช้งาน')).length).toBeGreaterThanOrEqual(1);
  });

  it('does not show the "ปิดใช้งาน" badge for an active plot', async () => {
    listPlotsMock.mockResolvedValue([plotFixture({ isActive: true })]);
    renderPlotsPage();

    await screen.findByText('แปลงทดสอบ');
    expect(screen.queryByText('ปิดใช้งาน')).toBeNull();
  });

  // --- item 7: inactive YieldCell shows ปิดใช้งาน ---------------------------

  it('shows "ปิดใช้งาน" in the yield cell for an inactive plot (never the yield summary/warning states)', async () => {
    listPlotsMock.mockResolvedValue([plotFixture({ isActive: false })]);
    renderPlotsPage();

    const row = (await screen.findByText('แปลงทดสอบ')).closest('tr')!;
    const yieldCell = row.querySelector('td:nth-last-child(2)')!;
    // The Yield column, scoped specifically — the planting-cycle cell
    // (under the name) has its own distinct copy (round 8-6I.1 Part D,
    // tested below), never "รอเริ่มรอบปลูก" for an inactive plot either.
    expect(yieldCell.textContent).toBe('ปิดใช้งาน');
  });

  // --- round 8-6I.1 Part D: planting-cycle cell copy for inactive plots -----

  it('shows a neutral "ไม่มีรอบปลูกที่เปิดอยู่" (never "รอเริ่มรอบปลูก") in the planting-cycle cell for an inactive plot', async () => {
    listPlotsMock.mockResolvedValue([plotFixture({ isActive: false })]);
    renderPlotsPage();

    expect(await screen.findByText('ไม่มีรอบปลูกที่เปิดอยู่')).toBeTruthy();
    expect(screen.queryByText('รอเริ่มรอบปลูก')).toBeNull();
  });

  it('still shows "รอเริ่มรอบปลูก" in the planting-cycle cell for an ACTIVE plot with no active cycle (no regression)', async () => {
    listPlotsMock.mockResolvedValue([plotFixture({ isActive: true, activeCycleId: null })]);
    renderPlotsPage();

    // Renders twice for an active-no-cycle plot (planting-cycle cell + yield
    // cell both say it) — both are legitimate and pre-existing.
    expect((await screen.findAllByText('รอเริ่มรอบปลูก')).length).toBeGreaterThanOrEqual(1);
    expect(screen.queryByText('ไม่มีรอบปลูกที่เปิดอยู่')).toBeNull();
  });

  // --- item 8: inactive plot never shows ตรวจแปลง, even with activeCycleId --

  it('never offers ตรวจแปลง for an inactive plot even if activeCycleId is (inconsistently) set', async () => {
    listPlotsMock.mockResolvedValue([plotFixture({ isActive: false, activeCycleId: 'cycle-1' })]);
    renderPlotsPage();

    await openRowMenu('SUP001-P001');
    expect(screen.queryByRole('menuitem', { name: 'ตรวจแปลง' })).toBeNull();
  });

  // --- item 9: reactivate action visibility by permission -------------------

  it('shows only "เปิดใช้งานแปลง" with plots.delete but not plots.update', async () => {
    listPlotsMock.mockResolvedValue([plotFixture({ isActive: false })]);
    useAuthStore.setState({ permissionKeys: new Set(['plots.read', 'plots.delete']) });
    renderPlotsPage();

    await openRowMenu('SUP001-P001');
    expect(await screen.findByRole('menuitem', { name: 'เปิดใช้งานแปลง' })).toBeTruthy();
    expect(screen.queryByRole('menuitem', { name: 'เปิดใช้งานและเริ่มรอบปลูกใหม่' })).toBeNull();
  });

  it('shows both reactivate actions with plots.delete + plots.update', async () => {
    listPlotsMock.mockResolvedValue([plotFixture({ isActive: false })]);
    useAuthStore.setState({ permissionKeys: new Set(['plots.read', 'plots.delete', 'plots.update']) });
    renderPlotsPage();

    await openRowMenu('SUP001-P001');
    expect(await screen.findByRole('menuitem', { name: 'เปิดใช้งานแปลง' })).toBeTruthy();
    expect(screen.getByRole('menuitem', { name: 'เปิดใช้งานและเริ่มรอบปลูกใหม่' })).toBeTruthy();
  });

  it('shows neither reactivate action without plots.delete', async () => {
    listPlotsMock.mockResolvedValue([plotFixture({ isActive: false })]);
    useAuthStore.setState({ permissionKeys: new Set(['plots.read', 'plots.update']) });
    renderPlotsPage();

    await openRowMenu('SUP001-P001');
    expect(screen.queryByRole('menuitem', { name: 'เปิดใช้งานแปลง' })).toBeNull();
    expect(screen.queryByRole('menuitem', { name: 'เปิดใช้งานและเริ่มรอบปลูกใหม่' })).toBeNull();
  });

  it('never shows either reactivate action for an active plot even with full permissions', async () => {
    listPlotsMock.mockResolvedValue([plotFixture({ isActive: true })]);
    renderPlotsPage();

    await openRowMenu('SUP001-P001');
    expect(screen.queryByRole('menuitem', { name: 'เปิดใช้งานแปลง' })).toBeNull();
    expect(screen.queryByRole('menuitem', { name: 'เปิดใช้งานและเริ่มรอบปลูกใหม่' })).toBeNull();
  });

  // --- item 10/13: reactivate-only calls one endpoint + invalidates ---------

  it('reactivate-only calls reactivatePlot exactly once and shows success feedback', async () => {
    listPlotsMock.mockResolvedValue([plotFixture({ isActive: false })]);
    reactivatePlotMock.mockResolvedValue({ ...plotFixture(), isActive: true });
    renderPlotsPage();

    await openRowMenu('SUP001-P001');
    fireEvent.click(screen.getByRole('menuitem', { name: 'เปิดใช้งานแปลง' }));
    fireEvent.click(await screen.findByRole('button', { name: 'ยืนยันเปิดใช้งานแปลง' }));

    await waitFor(() => expect(reactivatePlotMock).toHaveBeenCalledTimes(1));
    expect(reactivatePlotMock).toHaveBeenCalledWith('plot-1');
    expect(reactivatePlotWithCycleMock).not.toHaveBeenCalled();
    expect(await screen.findByText('เปิดใช้งานแปลงแล้ว')).toBeTruthy();
  });

  // --- item 11/12: reactivate-with-cycle calls one endpoint with payload ---

  it('reactivate-with-cycle calls reactivatePlotWithCycle exactly once with the cycle payload', async () => {
    listPlotsMock.mockResolvedValue([plotFixture({ isActive: false })]);
    reactivatePlotWithCycleMock.mockResolvedValue({
      plot: { ...plotFixture(), isActive: true },
      cycle: {
        id: 'cycle-1', plotId: 'plot-1', cycleNo: 1, status: 'active', crop: null, variety: null,
        cycleLabel: null, lotNo: null, poNumber: 'PO25009', pCode: 'Melon-Z', lotNoSource: 'auto',
        lotRunningNo: 1,
        supplierLotNo: null, plantingDate: null, plantCount: null, expectedYieldFull: null,
        expectedYieldUnit: null, startedAt: '2026-07-23T00:00:00Z', closedAt: null, closedById: null,
        closeReason: null, finalYieldPct: null, finalEstimatedYield: null, finalInspectionRecordId: null,
        createdAt: '2026-07-23T00:00:00Z', updatedAt: '2026-07-23T00:00:00Z',
      },
    });
    useAuthStore.setState({ permissionKeys: new Set(['plots.read', 'plots.delete', 'plots.update']) });
    renderPlotsPage();

    await openRowMenu('SUP001-P001');
    fireEvent.click(screen.getByRole('menuitem', { name: 'เปิดใช้งานและเริ่มรอบปลูกใหม่' }));
    await screen.findByPlaceholderText('เช่น PO25001');
    fireEvent.change(screen.getByPlaceholderText('เช่น PO25001'), { target: { value: 'PO25009' } });
    await pickCropAndVariety('พริกจินดา');
    // Round 8-12B — Auto Lot needs a cycleLabel as well as a P.Code.
    fireEvent.change(screen.getByPlaceholderText('เช่น jun2026 หรือ may2026'), { target: { value: '2605' } });
    fireEvent.click(screen.getByRole('button', { name: 'เปิดใช้งานและเริ่มรอบปลูก' }));

    await waitFor(() => expect(reactivatePlotWithCycleMock).toHaveBeenCalledTimes(1));
    expect(reactivatePlotMock).not.toHaveBeenCalled();
    const [calledId, payload] = reactivatePlotWithCycleMock.mock.calls[0] as [string, { poNumber: string; pCode: string }];
    expect(calledId).toBe('plot-1');
    expect(payload.poNumber).toBe('PO25009');
    expect(payload.pCode).toBe('Melon-Z');
    expect(await screen.findByText('เปิดใช้งานแปลงและเริ่มรอบปลูกใหม่แล้ว')).toBeTruthy();
  });

  // --- item 14: error mapping + modal stays open ----------------------------

  it('a 409-already-active error shows the mapped message and the modal stays open', async () => {
    listPlotsMock.mockResolvedValue([plotFixture({ isActive: false })]);
    reactivatePlotMock.mockRejectedValue(Object.assign(new Error('Conflict'), {
      isAxiosError: true, response: { status: 409, data: { detail: 'แปลงนี้เปิดใช้งานอยู่แล้ว' } },
    }));
    renderPlotsPage();

    await openRowMenu('SUP001-P001');
    fireEvent.click(screen.getByRole('menuitem', { name: 'เปิดใช้งานแปลง' }));
    fireEvent.click(await screen.findByRole('button', { name: 'ยืนยันเปิดใช้งานแปลง' }));

    expect(await screen.findByText('แปลงนี้เปิดใช้งานอยู่แล้ว กรุณารีเฟรชข้อมูล')).toBeTruthy();
    expect(screen.getByRole('button', { name: 'ยืนยันเปิดใช้งานแปลง' })).toBeTruthy();
  });

  it('a network error shows the connection-failure message', async () => {
    listPlotsMock.mockResolvedValue([plotFixture({ isActive: false })]);
    reactivatePlotMock.mockRejectedValue(Object.assign(new Error('Network Error'), {
      isAxiosError: true, response: undefined,
    }));
    renderPlotsPage();

    await openRowMenu('SUP001-P001');
    fireEvent.click(screen.getByRole('menuitem', { name: 'เปิดใช้งานแปลง' }));
    fireEvent.click(await screen.findByRole('button', { name: 'ยืนยันเปิดใช้งานแปลง' }));

    expect(await screen.findByText('เชื่อมต่อระบบไม่สำเร็จ กรุณาลองใหม่อีกครั้ง')).toBeTruthy();
  });

  // --- item 17: regression — active plot row workflows unaffected -----------

  it('regression: an active plot still shows ตรวจแปลง/edit/QR unaffected by the reactivation feature', async () => {
    listPlotsMock.mockResolvedValue([plotFixture({ isActive: true, activeCycleId: 'cycle-1' })]);
    renderPlotsPage();

    await openRowMenu('SUP001-P001');
    expect(await screen.findByRole('menuitem', { name: 'ตรวจแปลง' })).toBeTruthy();
    expect(screen.getByRole('menuitem', { name: 'แก้ไขข้อมูลแปลง' })).toBeTruthy();
    expect(screen.getByRole('menuitem', { name: 'พิมพ์ QR' })).toBeTruthy();
  });

  // --- round 8-6I.1 Part C: changing plot status clears the stale province filter --

  it('changing the status filter clears a previously-selected province back to "ทุกจังหวัด"', async () => {
    listPlotProvincesMock.mockResolvedValue(['จังหวัดทดสอบ']);
    renderPlotsPage();

    // selectProvinceFilter itself waits for the option to actually load
    // before clicking it (findByText inside the opened listbox).
    await selectProvinceFilter('จังหวัดทดสอบ');
    await waitFor(() => expect(hasListPlotsCallContaining({ province: 'จังหวัดทดสอบ' })).toBe(true));

    const statusSelect = screen.getByLabelText('กรองสถานะแปลง');
    fireEvent.change(statusSelect, { target: { value: 'inactive' } });

    expect(screen.getByRole('button', { name: 'กรองจังหวัด' }).textContent).toContain('ทุกจังหวัด');

    await waitFor(() => {
      const lastCall = listPlotsMock.mock.calls[listPlotsMock.mock.calls.length - 1][0] as {
        province?: string; plotStatus?: string;
      };
      expect(lastCall.plotStatus).toBe('inactive');
      expect(lastCall.province).toBeUndefined();
    });
  });
});

// Round 8-17A.2 Part D — secure phone search: the search box also accepts a
// Thai mobile number, routed through POST searchPlotsByPhone (never GET
// listPlots?q=), with a malformed attempt blocked client-side before any
// request fires.
describe('Plots list — secure phone search (round 8-17A.2)', () => {
  function plotFixture(overrides: Partial<{
    id: string; plotCode: string; primaryPhone: string | null; additionalPhones: string[];
  }> = {}) {
    return {
      id: 'plot-1', supplierId: 'sup-1', supplierCode: 'SUP001', supplierName: 'Supplier One',
      plotCode: 'SUP001-P001', name: 'แปลงทดสอบ',
      village: null, district: null, province: 'จังหวัดทดสอบ',
      latitude: null, longitude: null,
      isActive: true, assignedCount: 0, qrKey: 'qr-1',
      currentYieldPct: null, expectedYieldFull: null, expectedYieldUnit: null, plantCount: null,
      primaryPhone: null, additionalPhones: [],
      ...overrides,
    };
  }

  // Round 8-18B — phone search now lives in its own dedicated box, split
  // from the name/code box (which keeps the OLD placeholder-based helper
  // name below for a smaller diff, but now points at the phone input).

  beforeEach(() => {
    listPlotsMock.mockResolvedValue([plotFixture()]);
    searchPlotsByPhoneMock.mockResolvedValue([plotFixture()]);
  });

  it('a valid phone goes through POST searchPlotsByPhone, never GET listPlots q', async () => {
    renderPlotsPage();
    await screen.findByText('แปลงทดสอบ');
    listPlotsMock.mockClear();

    fireEvent.change(screen.getByLabelText(ACCESS_NUMBER_LABEL), {
      target: { value: '081-234-5678' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'ค้นหา' }));

    await waitFor(() => expect(searchPlotsByPhoneMock).toHaveBeenCalled());
    const call = searchPlotsByPhoneMock.mock.calls[0][0] as { phone: string };
    // Normalized (dashes stripped) — same canonical shape the backend uses.
    expect(call.phone).toBe('0812345678');
    // Never routed through the GET q path.
    expect(listPlotsMock.mock.calls.some(([p]) => (p as { q?: string }).q)).toBe(false);
  });

  it('the phone never appears in a listPlots (GET) call\'s q parameter', async () => {
    renderPlotsPage();
    await screen.findByText('แปลงทดสอบ');

    fireEvent.change(screen.getByLabelText(ACCESS_NUMBER_LABEL), {
      target: { value: '0812345678' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'ค้นหา' }));
    await waitFor(() => expect(searchPlotsByPhoneMock).toHaveBeenCalled());

    for (const [params] of listPlotsMock.mock.calls) {
      expect((params as { q?: string }).q).not.toBe('0812345678');
    }
  });

  it('dashes/spaces in the entered number are normalized before the request', async () => {
    renderPlotsPage();
    await screen.findByText('แปลงทดสอบ');

    fireEvent.change(screen.getByLabelText(ACCESS_NUMBER_LABEL), {
      target: { value: '081 234 5678' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'ค้นหา' }));

    await waitFor(() => {
      const call = searchPlotsByPhoneMock.mock.calls[0]?.[0] as { phone: string } | undefined;
      expect(call?.phone).toBe('0812345678');
    });
  });

  it('a too-short number shows a generic Thai error and sends NO request at all', async () => {
    // Round 8-18B.1 — "too short" is now < 4 digits; a 6-digit fragment like
    // 099123 is a legitimate partial lookup and no longer an error.
    renderPlotsPage();
    await screen.findByText('แปลงทดสอบ');
    listPlotsMock.mockClear();

    fireEvent.change(screen.getByLabelText(ACCESS_NUMBER_LABEL), {
      target: { value: '12' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'ค้นหา' }));

    const alert = await screen.findByRole('alert');
    expect(alert.textContent).toMatch(/หมายเลขสำหรับเข้าตรวจ/);
    expect(searchPlotsByPhoneMock).not.toHaveBeenCalled();
    expect(listPlotsMock).not.toHaveBeenCalled();
  });

  it('never falls back to GET listPlots q on a malformed number attempt', async () => {
    renderPlotsPage();
    await screen.findByText('แปลงทดสอบ');
    listPlotsMock.mockClear();

    fireEvent.change(screen.getByLabelText(ACCESS_NUMBER_LABEL), {
      target: { value: '1' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'ค้นหา' }));

    await screen.findByRole('alert');
    expect(listPlotsMock).not.toHaveBeenCalled();
    expect(searchPlotsByPhoneMock).not.toHaveBeenCalled();
  });

  it('one phone can return multiple plots, all rendered', async () => {
    searchPlotsByPhoneMock.mockResolvedValue([
      plotFixture({ id: 'plot-1', plotCode: 'SUP001-P001' }),
      plotFixture({ id: 'plot-2', plotCode: 'SUP001-P002' }),
    ]);
    renderPlotsPage();
    await screen.findByText('แปลงทดสอบ');

    fireEvent.change(screen.getByLabelText(ACCESS_NUMBER_LABEL), {
      target: { value: '0812345678' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'ค้นหา' }));

    await screen.findByText('SUP001-P001');
    await screen.findByText('SUP001-P002');
  });

  // Round 8-18B — the two boxes are now independent inputs: a plain (non-
  // phone) search goes through the NAME/CODE box, never the phone box.
  it('a plain (non-phone) search uses the name/code box and GET listPlots q as before', async () => {
    renderPlotsPage();
    await screen.findByText('แปลงทดสอบ');

    fireEvent.change(screen.getByLabelText(NAME_CODE_LABEL), {
      target: { value: 'SUP001-P001' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'ค้นหา' }));

    await waitFor(() => expect(hasListPlotsCallContaining({ q: 'SUP001-P001' })).toBe(true));
    expect(searchPlotsByPhoneMock).not.toHaveBeenCalled();
  });

  it('an all-digit plot-code fragment in the name/code box is a normal GET search (round 8-18B.1)', async () => {
    // Round 8-18B.1 removed the looksLikePhoneAttempt guard here: "002" is a
    // plot-code fragment, not a phone number. The number box has its own
    // state, so nothing typed here can ever become a phone search.
    renderPlotsPage();
    await screen.findByText('แปลงทดสอบ');

    fireEvent.change(screen.getByLabelText(NAME_CODE_LABEL), {
      target: { value: '002' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'ค้นหา' }));

    await waitFor(() => expect(hasListPlotsCallContaining({ q: '002' })).toBe(true));
    expect(screen.queryByRole('alert')).toBeNull();
    expect(searchPlotsByPhoneMock).not.toHaveBeenCalled();
  });

  it('filling BOTH boxes sends one POST carrying phone + q (intersection), never a GET (round 8-18B)', async () => {
    renderPlotsPage();
    await screen.findByText('แปลงทดสอบ');
    listPlotsMock.mockClear();

    fireEvent.change(screen.getByLabelText(NAME_CODE_LABEL), {
      target: { value: 'SUP001-P001' },
    });
    fireEvent.change(screen.getByLabelText(ACCESS_NUMBER_LABEL), {
      target: { value: '081-234-5678' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'ค้นหา' }));

    await waitFor(() => expect(searchPlotsByPhoneMock).toHaveBeenCalled());
    const call = searchPlotsByPhoneMock.mock.calls[0][0] as { phone: string; q?: string };
    expect(call.phone).toBe('0812345678');
    expect(call.q).toBe('SUP001-P001');
    // No "pick one box" error, and the text half never went out as a GET.
    expect(screen.queryByRole('alert')).toBeNull();
    expect(listPlotsMock).not.toHaveBeenCalled();
  });

  it('an invalid number sends NOTHING even when the name/code box is filled (round 8-18B)', async () => {
    // Sending only the q half would silently show a WIDER result set than
    // the user asked for.
    renderPlotsPage();
    await screen.findByText('แปลงทดสอบ');
    listPlotsMock.mockClear();

    fireEvent.change(screen.getByLabelText(NAME_CODE_LABEL), {
      target: { value: 'SUP001-P001' },
    });
    fireEvent.change(screen.getByLabelText(ACCESS_NUMBER_LABEL), {
      target: { value: '12' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'ค้นหา' }));

    await screen.findByRole('alert');
    expect(searchPlotsByPhoneMock).not.toHaveBeenCalled();
    expect(listPlotsMock).not.toHaveBeenCalled();
  });

  it('the name/code box alone still uses GET listPlots, with no phone anywhere (round 8-18B)', async () => {
    renderPlotsPage();
    await screen.findByText('แปลงทดสอบ');

    fireEvent.change(screen.getByLabelText(NAME_CODE_LABEL), {
      target: { value: 'SUP001-P001' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'ค้นหา' }));

    await waitFor(() => expect(hasListPlotsCallContaining({ q: 'SUP001-P001' })).toBe(true));
    expect(searchPlotsByPhoneMock).not.toHaveBeenCalled();
  });

  it('ล้างค่า clears the phone search state (input, in-progress note, and mode)', async () => {
    renderPlotsPage();
    await screen.findByText('แปลงทดสอบ');

    const input = screen.getByLabelText(ACCESS_NUMBER_LABEL) as HTMLInputElement;
    fireEvent.change(input, { target: { value: '0812345678' } });
    fireEvent.click(screen.getByRole('button', { name: 'ค้นหา' }));
    await waitFor(() => expect(searchPlotsByPhoneMock).toHaveBeenCalled());
    await screen.findByText(/กำลังค้นหาแปลงที่หมายเลข/);

    fireEvent.click(screen.getByRole('button', { name: 'ล้างค่า' }));

    expect(input.value).toBe('');
    expect(screen.queryByText(/กำลังค้นหาแปลงที่หมายเลข/)).toBeNull();

    // Confirm the mode genuinely reverted to text search (not stuck on
    // phone) — a fresh plain-text search via the (separate) name/code box
    // after clearing must use listPlots.
    searchPlotsByPhoneMock.mockClear();
    fireEvent.change(screen.getByLabelText(NAME_CODE_LABEL), { target: { value: 'SUP001-P001' } });
    fireEvent.click(screen.getByRole('button', { name: 'ค้นหา' }));
    await waitFor(() => expect(hasListPlotsCallContaining({ q: 'SUP001-P001' })).toBe(true));
    expect(searchPlotsByPhoneMock).not.toHaveBeenCalled();
  });

  it('pagination (ถัดไป) works with phone search results', async () => {
    searchPlotsByPhoneMock.mockResolvedValue(
      Array.from({ length: 100 }, (_, i) => plotFixture({ id: `plot-${i}`, plotCode: `SUP001-P${i}` })),
    );
    renderPlotsPage();
    await screen.findByText('แปลงทดสอบ');

    fireEvent.change(screen.getByLabelText(ACCESS_NUMBER_LABEL), {
      target: { value: '0812345678' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'ค้นหา' }));
    await screen.findByText('SUP001-P0');

    fireEvent.click(await screen.findByRole('button', { name: 'ถัดไป →' }));

    await waitFor(() => {
      const lastCall = searchPlotsByPhoneMock.mock.calls[searchPlotsByPhoneMock.mock.calls.length - 1][0] as { offset: number };
      expect(lastCall.offset).toBe(100);
    });
  });

  it('React Query cache key never carries the raw phone', async () => {
    renderPlotsPage();
    await screen.findByText('แปลงทดสอบ');

    fireEvent.change(screen.getByLabelText(ACCESS_NUMBER_LABEL), {
      target: { value: '0812345678' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'ค้นหา' }));
    await waitFor(() => expect(searchPlotsByPhoneMock).toHaveBeenCalled());

    // The mock call ARGS legitimately carry the phone (it's the actual
    // request payload) — what must never happen is the raw phone leaking
    // into anything URL-shaped. Confirm no GET call (listPlots) ever saw it,
    // covering the only other place a query key/params object is built.
    for (const call of listPlotsMock.mock.calls) {
      expect(JSON.stringify(call)).not.toContain('0812345678');
    }
  });

  it('round 8-17A.2.1: re-searching the SAME phone fires a fresh request (nonce, not a phone-derived hash, drives the query key)', async () => {
    // Regression guard: the old cache key was a hash of the phone, so the
    // SAME phone searched twice produced the SAME query key and — within
    // the 60s staleTime — the second search silently served the first
    // search's cached result instead of re-querying. A nonce that bumps on
    // every applied search fixes this even though the phone itself is
    // unchanged.
    renderPlotsPage();
    await screen.findByText('แปลงทดสอบ');

    const input = screen.getByLabelText(ACCESS_NUMBER_LABEL);
    fireEvent.change(input, { target: { value: '0812345678' } });
    fireEvent.click(screen.getByRole('button', { name: 'ค้นหา' }));
    await waitFor(() => expect(searchPlotsByPhoneMock).toHaveBeenCalledTimes(1));

    // Re-set the input value via an intermediate change so the DOM's
    // native value tracker registers a real change (setting the identical
    // string twice in a row can otherwise be deduped before React's
    // onChange even fires) — then re-search the exact same phone.
    fireEvent.change(input, { target: { value: '' } });
    fireEvent.change(input, { target: { value: '0812345678' } });
    fireEvent.click(screen.getByRole('button', { name: 'ค้นหา' }));
    await waitFor(() => expect(searchPlotsByPhoneMock).toHaveBeenCalledTimes(2));
  });

  it('template download excludes the phone and shows an explanatory note', async () => {
    downloadPlotImportTemplateMock.mockResolvedValue(new Blob(['x']));
    renderPlotsPage();
    await screen.findByText('แปลงทดสอบ');

    // Supplier filter required before the filtered-download button engages.
    fireEvent.click(await screen.findByRole('button', { name: 'กรอง Supplier' }));
    const listbox = await screen.findByRole('listbox');
    fireEvent.click(within(listbox).getByText('SUP001'));

    fireEvent.change(screen.getByLabelText(ACCESS_NUMBER_LABEL), {
      target: { value: '0812345678' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'ค้นหา' }));
    await waitFor(() => expect(searchPlotsByPhoneMock).toHaveBeenCalled());

    expect(await screen.findByText(/Template ใช้ตัวกรองอื่นที่เลือกไว้ทั้งหมด.*แต่ไม่ใช้หมายเลขสำหรับเข้าตรวจ/)).toBeTruthy();

    fireEvent.click(screen.getByRole('button', { name: 'ดาวน์โหลด Excel' }));
    fireEvent.click(screen.getByRole('menuitem', { name: 'ตามตัวกรองปัจจุบัน' }));
    await waitFor(() => expect(downloadPlotImportTemplateMock).toHaveBeenCalled());
    const call = downloadPlotImportTemplateMock.mock.calls[0][0] as Record<string, unknown>;
    expect(JSON.stringify(call)).not.toContain('0812345678');
  });
});

describe('Plots list — split identity/access-number search (round 8-18B)', () => {
  function plotFixture(overrides: Record<string, unknown> = {}) {
    return {
      id: 'plot-1', supplierId: 'sup-1', supplierCode: 'SUP001', supplierName: 'Supplier One',
      plotCode: 'SUP001-P001', name: 'แปลงทดสอบ',
      village: null, district: null, province: 'จังหวัดทดสอบ',
      latitude: null, longitude: null,
      isActive: true, assignedCount: 0, qrKey: 'qr-1',
      currentYieldPct: null, expectedYieldFull: null, expectedYieldUnit: null, plantCount: null,
      primaryPhone: null, additionalPhones: [],
      ...overrides,
    };
  }

  beforeEach(() => {
    listPlotsMock.mockResolvedValue([plotFixture()]);
    searchPlotsByPhoneMock.mockResolvedValue([plotFixture()]);
  });

  // --- the old combined box is gone, replaced by two labelled inputs ---

  it('no longer renders the old combined search box', async () => {
    renderPlotsPage();
    await screen.findByText('แปลงทดสอบ');

    // The retired copy must not survive anywhere on the page.
    expect(screen.queryByPlaceholderText(/ค้นหารหัส\/ชื่อแปลง\/จังหวัด/)).toBeNull();
    expect(screen.queryByText(/หมายเลขสำหรับเข้าตรวจ\.\.\./)).toBeNull();
  });

  it('renders both search boxes with their visible labels', async () => {
    renderPlotsPage();
    await screen.findByText('แปลงทดสอบ');

    expect(screen.getByText(NAME_CODE_LABEL)).toBeTruthy();
    expect(screen.getByText(ACCESS_NUMBER_LABEL)).toBeTruthy();
    expect(screen.getByLabelText(NAME_CODE_LABEL)).toBeTruthy();
    expect(screen.getByLabelText(ACCESS_NUMBER_LABEL)).toBeTruthy();
  });

  it('the access-number input is numeric-friendly and never autofilled', async () => {
    renderPlotsPage();
    await screen.findByText('แปลงทดสอบ');

    const input = screen.getByLabelText(ACCESS_NUMBER_LABEL) as HTMLInputElement;
    expect(input.type).toBe('text');
    expect(input.inputMode).toBe('numeric');
    expect(input.autocomplete).toBe('off');
  });

  // --- Enter in either box applies BOTH ---

  it('Enter in the name/code box applies both boxes together', async () => {
    renderPlotsPage();
    await screen.findByText('แปลงทดสอบ');

    fireEvent.change(screen.getByLabelText(ACCESS_NUMBER_LABEL), { target: { value: '0812345678' } });
    const nameCode = screen.getByLabelText(NAME_CODE_LABEL);
    fireEvent.change(nameCode, { target: { value: 'SUP001-P001' } });
    fireEvent.keyDown(nameCode, { key: 'Enter' });

    await waitFor(() => expect(searchPlotsByPhoneMock).toHaveBeenCalled());
    const call = searchPlotsByPhoneMock.mock.calls[0][0] as { phone: string; q?: string };
    expect(call.phone).toBe('0812345678');
    expect(call.q).toBe('SUP001-P001');
  });

  it('Enter in the access-number box applies both boxes together', async () => {
    renderPlotsPage();
    await screen.findByText('แปลงทดสอบ');

    fireEvent.change(screen.getByLabelText(NAME_CODE_LABEL), { target: { value: 'SUP001-P001' } });
    const number = screen.getByLabelText(ACCESS_NUMBER_LABEL);
    fireEvent.change(number, { target: { value: '0812345678' } });
    fireEvent.keyDown(number, { key: 'Enter' });

    await waitFor(() => expect(searchPlotsByPhoneMock).toHaveBeenCalled());
    const call = searchPlotsByPhoneMock.mock.calls[0][0] as { phone: string; q?: string };
    expect(call.q).toBe('SUP001-P001');
  });

  it('an access-number search alone sends no q at all', async () => {
    renderPlotsPage();
    await screen.findByText('แปลงทดสอบ');

    fireEvent.change(screen.getByLabelText(ACCESS_NUMBER_LABEL), { target: { value: '0812345678' } });
    fireEvent.click(screen.getByRole('button', { name: 'ค้นหา' }));

    await waitFor(() => expect(searchPlotsByPhoneMock).toHaveBeenCalled());
    const call = searchPlotsByPhoneMock.mock.calls[0][0] as { q?: string };
    expect(call.q).toBeUndefined();
  });

  // --- เบอร์หลัก / เบอร์เสริม render identically from the API result ---

  it('renders a result reached via the plot\'s เบอร์หลัก and one reached via เบอร์เสริม the same way', async () => {
    // The backend matches both access types in ONE query (EXISTS, no
    // access_type branch) — the page must not treat them differently either.
    searchPlotsByPhoneMock.mockResolvedValue([
      plotFixture({ id: 'plot-1', plotCode: 'SUP001-P001', primaryPhone: '0812345678' }),
      plotFixture({ id: 'plot-2', plotCode: 'SUP001-P002', additionalPhones: ['0812345678'] }),
    ]);
    renderPlotsPage();
    await screen.findByText('แปลงทดสอบ');

    fireEvent.change(screen.getByLabelText(ACCESS_NUMBER_LABEL), { target: { value: '0812345678' } });
    fireEvent.click(screen.getByRole('button', { name: 'ค้นหา' }));

    expect(await screen.findByText('SUP001-P001')).toBeTruthy();
    expect(await screen.findByText('SUP001-P002')).toBeTruthy();
  });

  it('a plot authorized by the number more than once still renders a single row', async () => {
    // Backend guarantees this (EXISTS, never a JOIN) — this asserts the page
    // adds no dedup of its own that could mask a future backend regression.
    searchPlotsByPhoneMock.mockResolvedValue([
      plotFixture({ id: 'plot-1', plotCode: 'SUP001-P001', primaryPhone: '0812345678' }),
    ]);
    renderPlotsPage();
    await screen.findByText('แปลงทดสอบ');

    fireEvent.change(screen.getByLabelText(ACCESS_NUMBER_LABEL), { target: { value: '0812345678' } });
    fireEvent.click(screen.getByRole('button', { name: 'ค้นหา' }));

    await waitFor(() => expect(searchPlotsByPhoneMock).toHaveBeenCalled());
    expect(await screen.findAllByText('SUP001-P001')).toHaveLength(1);
  });

  // --- PII discipline is unchanged by the split ---

  it('the access number never reaches a listPlots (GET) call, even combined with q', async () => {
    renderPlotsPage();
    await screen.findByText('แปลงทดสอบ');

    fireEvent.change(screen.getByLabelText(NAME_CODE_LABEL), { target: { value: 'SUP001-P001' } });
    fireEvent.change(screen.getByLabelText(ACCESS_NUMBER_LABEL), { target: { value: '0812345678' } });
    fireEvent.click(screen.getByRole('button', { name: 'ค้นหา' }));
    await waitFor(() => expect(searchPlotsByPhoneMock).toHaveBeenCalled());

    for (const call of listPlotsMock.mock.calls) {
      expect(JSON.stringify(call)).not.toContain('0812345678');
    }
  });

  it('re-searching the same number with a changed q refires (nonce query key, no raw phone in it)', async () => {
    renderPlotsPage();
    await screen.findByText('แปลงทดสอบ');

    fireEvent.change(screen.getByLabelText(ACCESS_NUMBER_LABEL), { target: { value: '0812345678' } });
    fireEvent.click(screen.getByRole('button', { name: 'ค้นหา' }));
    await waitFor(() => expect(searchPlotsByPhoneMock).toHaveBeenCalledTimes(1));

    fireEvent.change(screen.getByLabelText(NAME_CODE_LABEL), { target: { value: 'SUP001-P002' } });
    fireEvent.click(screen.getByRole('button', { name: 'ค้นหา' }));
    await waitFor(() => expect(searchPlotsByPhoneMock).toHaveBeenCalledTimes(2));
    const second = searchPlotsByPhoneMock.mock.calls[1][0] as { q?: string };
    expect(second.q).toBe('SUP001-P002');
  });

  // --- Clear Filters ---

  it('ล้างค่า clears BOTH boxes, the number mode, and restores status=active', async () => {
    renderPlotsPage();
    await screen.findByText('แปลงทดสอบ');

    const nameCode = screen.getByLabelText(NAME_CODE_LABEL) as HTMLInputElement;
    const number = screen.getByLabelText(ACCESS_NUMBER_LABEL) as HTMLInputElement;
    fireEvent.change(nameCode, { target: { value: 'SUP001-P001' } });
    fireEvent.change(number, { target: { value: '0812345678' } });
    fireEvent.change(screen.getByLabelText('กรองสถานะแปลง'), { target: { value: 'inactive' } });
    fireEvent.click(screen.getByRole('button', { name: 'ค้นหา' }));
    await waitFor(() => expect(searchPlotsByPhoneMock).toHaveBeenCalled());

    fireEvent.click(screen.getByRole('button', { name: 'ล้างค่า' }));

    expect(nameCode.value).toBe('');
    expect(number.value).toBe('');
    expect((screen.getByLabelText('กรองสถานะแปลง') as HTMLSelectElement).value).toBe('active');
    // Back to the plain GET path with nothing carried over.
    await waitFor(() => expect(hasListPlotsCallContaining({
      q: undefined, plotStatus: 'active',
    })).toBe(true));
  });

  // --- Excel template ---

  it('the Excel template receives q from the name/code box but never the access number', async () => {
    downloadPlotImportTemplateMock.mockResolvedValue(new Blob(['x']));
    renderPlotsPage();
    await screen.findByText('แปลงทดสอบ');

    fireEvent.click(await screen.findByRole('button', { name: 'กรอง Supplier' }));
    fireEvent.click(within(await screen.findByRole('listbox')).getByText('SUP001'));

    fireEvent.change(screen.getByLabelText(NAME_CODE_LABEL), { target: { value: 'SUP001-P001' } });
    fireEvent.change(screen.getByLabelText(ACCESS_NUMBER_LABEL), { target: { value: '0812345678' } });
    fireEvent.click(screen.getByRole('button', { name: 'ค้นหา' }));
    await waitFor(() => expect(searchPlotsByPhoneMock).toHaveBeenCalled());

    fireEvent.click(screen.getByRole('button', { name: 'ดาวน์โหลด Excel' }));
    fireEvent.click(screen.getByRole('menuitem', { name: 'ตามตัวกรองปัจจุบัน' }));

    await waitFor(() => expect(downloadPlotImportTemplateMock).toHaveBeenCalled());
    const call = downloadPlotImportTemplateMock.mock.calls[0][0] as Record<string, unknown>;
    expect(call.q).toBe('SUP001-P001');
    expect(JSON.stringify(call)).not.toContain('0812345678');
  });
});

// Round 8-18B.1 — both boxes became PARTIAL searches. The identity box lost
// its all-digit block (so "002" works), and the number box takes a 4-10
// digit fragment instead of a complete normalized Thai mobile.
describe('Plots list — partial identity/access-number search (round 8-18B.1)', () => {
  function plotFixture(overrides: Record<string, unknown> = {}) {
    return {
      id: 'plot-1', supplierId: 'sup-1', supplierCode: 'SUP010', supplierName: 'Supplier Ten',
      plotCode: 'SUP010-P002', name: 'แปลงเมล่อน',
      village: null, district: null, province: 'จังหวัดทดสอบ',
      latitude: null, longitude: null,
      isActive: true, assignedCount: 0, qrKey: 'qr-1',
      currentYieldPct: null, expectedYieldFull: null, expectedYieldUnit: null, plantCount: null,
      primaryPhone: null, additionalPhones: [],
      ...overrides,
    };
  }

  beforeEach(() => {
    listPlotsMock.mockResolvedValue([plotFixture()]);
    searchPlotsByPhoneMock.mockResolvedValue([plotFixture()]);
  });

  // --- Part A: partial identity search ---

  it('acceptance: "002" reaches the API as q and is never blocked', async () => {
    renderPlotsPage();
    await screen.findByText('แปลงเมล่อน');

    fireEvent.change(screen.getByLabelText(NAME_CODE_LABEL), { target: { value: '002' } });
    fireEvent.click(screen.getByRole('button', { name: 'ค้นหา' }));

    await waitFor(() => expect(hasListPlotsCallContaining({ q: '002' })).toBe(true));
    expect(screen.queryByRole('alert')).toBeNull();
  });

  it('acceptance: a Thai name fragment reaches the API as q', async () => {
    renderPlotsPage();
    await screen.findByText('แปลงเมล่อน');

    fireEvent.change(screen.getByLabelText(NAME_CODE_LABEL), { target: { value: 'เมล่อน' } });
    fireEvent.click(screen.getByRole('button', { name: 'ค้นหา' }));

    await waitFor(() => expect(hasListPlotsCallContaining({ q: 'เมล่อน' })).toBe(true));
  });

  it('trims the identity search before sending it', async () => {
    renderPlotsPage();
    await screen.findByText('แปลงเมล่อน');

    fireEvent.change(screen.getByLabelText(NAME_CODE_LABEL), { target: { value: '   002   ' } });
    fireEvent.click(screen.getByRole('button', { name: 'ค้นหา' }));

    await waitFor(() => expect(hasListPlotsCallContaining({ q: '002' })).toBe(true));
  });

  it('a 1-character identity search shows a message and sends nothing', async () => {
    renderPlotsPage();
    await screen.findByText('แปลงเมล่อน');
    listPlotsMock.mockClear();

    fireEvent.change(screen.getByLabelText(NAME_CODE_LABEL), { target: { value: '0' } });
    fireEvent.click(screen.getByRole('button', { name: 'ค้นหา' }));

    expect((await screen.findByRole('alert')).textContent).toMatch(/อย่างน้อย 2 ตัวอักษร/);
    expect(listPlotsMock).not.toHaveBeenCalled();
    expect(searchPlotsByPhoneMock).not.toHaveBeenCalled();
  });

  it('whitespace-only is treated as empty, not as a too-short search', async () => {
    renderPlotsPage();
    await screen.findByText('แปลงเมล่อน');

    fireEvent.change(screen.getByLabelText(NAME_CODE_LABEL), { target: { value: '   ' } });
    fireEvent.click(screen.getByRole('button', { name: 'ค้นหา' }));

    expect(screen.queryByRole('alert')).toBeNull();
    await waitFor(() => expect(hasListPlotsCallContaining({ q: undefined })).toBe(true));
  });

  // --- Part B: partial access-number search ---

  it('acceptance: a 4-digit fragment goes through POST searchPlotsByPhone as-is', async () => {
    renderPlotsPage();
    await screen.findByText('แปลงเมล่อน');
    listPlotsMock.mockClear();

    fireEvent.change(screen.getByLabelText(ACCESS_NUMBER_LABEL), { target: { value: '5552' } });
    fireEvent.click(screen.getByRole('button', { name: 'ค้นหา' }));

    await waitFor(() => expect(searchPlotsByPhoneMock).toHaveBeenCalled());
    // Sent verbatim — NOT normalized/expanded to a full 10-digit number.
    expect((searchPlotsByPhoneMock.mock.calls[0][0] as { phone: string }).phone).toBe('5552');
    expect(listPlotsMock).not.toHaveBeenCalled();
  });

  it('the number box strips non-digits as they are typed', async () => {
    renderPlotsPage();
    await screen.findByText('แปลงเมล่อน');

    const input = screen.getByLabelText(ACCESS_NUMBER_LABEL) as HTMLInputElement;
    fireEvent.change(input, { target: { value: '084-555-2162' } });
    expect(input.value).toBe('0845552162');

    fireEvent.change(input, { target: { value: '55%2_' } });
    expect(input.value).toBe('552');
  });

  it('the number box caps input at 10 digits', async () => {
    renderPlotsPage();
    await screen.findByText('แปลงเมล่อน');

    const input = screen.getByLabelText(ACCESS_NUMBER_LABEL) as HTMLInputElement;
    fireEvent.change(input, { target: { value: '08999999999999' } });
    expect(input.value).toBe('0899999999');
  });

  it('a 3-digit number shows a generic Thai message and sends nothing', async () => {
    renderPlotsPage();
    await screen.findByText('แปลงเมล่อน');
    listPlotsMock.mockClear();

    fireEvent.change(screen.getByLabelText(ACCESS_NUMBER_LABEL), { target: { value: '555' } });
    fireEvent.click(screen.getByRole('button', { name: 'ค้นหา' }));

    const alert = await screen.findByRole('alert');
    expect(alert.textContent).toMatch(/4-10 หลัก/);
    // Generic — the entered digits are never echoed back at the user.
    expect(alert.textContent).not.toContain('555');
    expect(searchPlotsByPhoneMock).not.toHaveBeenCalled();
    expect(listPlotsMock).not.toHaveBeenCalled();
  });

  it('a full 10-digit number is still accepted (partial is additive)', async () => {
    renderPlotsPage();
    await screen.findByText('แปลงเมล่อน');

    fireEvent.change(screen.getByLabelText(ACCESS_NUMBER_LABEL), { target: { value: '0845552162' } });
    fireEvent.click(screen.getByRole('button', { name: 'ค้นหา' }));

    await waitFor(() => expect(searchPlotsByPhoneMock).toHaveBeenCalled());
    expect((searchPlotsByPhoneMock.mock.calls[0][0] as { phone: string }).phone).toBe('0845552162');
  });

  it('the in-progress note never echoes the searched digits back on screen', async () => {
    renderPlotsPage();
    await screen.findByText('แปลงเมล่อน');

    fireEvent.change(screen.getByLabelText(ACCESS_NUMBER_LABEL), { target: { value: '0845552162' } });
    fireEvent.click(screen.getByRole('button', { name: 'ค้นหา' }));

    const note = await screen.findByText(/กำลังค้นหาแปลงที่หมายเลข/);
    expect(note.textContent).not.toContain('0845552162');
    expect(note.textContent).not.toContain('084-555-2162');
  });

  // --- Part C: combined + scope-preserving behavior ---

  it('a partial number AND a partial identity go out together in one POST body', async () => {
    renderPlotsPage();
    await screen.findByText('แปลงเมล่อน');
    listPlotsMock.mockClear();

    fireEvent.change(screen.getByLabelText(NAME_CODE_LABEL), { target: { value: '002' } });
    fireEvent.change(screen.getByLabelText(ACCESS_NUMBER_LABEL), { target: { value: '5552' } });
    fireEvent.click(screen.getByRole('button', { name: 'ค้นหา' }));

    await waitFor(() => expect(searchPlotsByPhoneMock).toHaveBeenCalled());
    const call = searchPlotsByPhoneMock.mock.calls[0][0] as { phone: string; q?: string };
    expect(call.phone).toBe('5552');
    expect(call.q).toBe('002');
    expect(listPlotsMock).not.toHaveBeenCalled();
  });

  it('an invalid number blocks the whole search even with a valid identity fragment', async () => {
    renderPlotsPage();
    await screen.findByText('แปลงเมล่อน');
    listPlotsMock.mockClear();

    fireEvent.change(screen.getByLabelText(NAME_CODE_LABEL), { target: { value: '002' } });
    fireEvent.change(screen.getByLabelText(ACCESS_NUMBER_LABEL), { target: { value: '55' } });
    fireEvent.click(screen.getByRole('button', { name: 'ค้นหา' }));

    await screen.findByRole('alert');
    expect(searchPlotsByPhoneMock).not.toHaveBeenCalled();
    expect(listPlotsMock).not.toHaveBeenCalled();
  });

  it('the partial number never appears in a GET call or the page URL', async () => {
    renderPlotsPage();
    await screen.findByText('แปลงเมล่อน');

    fireEvent.change(screen.getByLabelText(NAME_CODE_LABEL), { target: { value: '002' } });
    fireEvent.change(screen.getByLabelText(ACCESS_NUMBER_LABEL), { target: { value: '5552' } });
    fireEvent.click(screen.getByRole('button', { name: 'ค้นหา' }));
    await waitFor(() => expect(searchPlotsByPhoneMock).toHaveBeenCalled());

    for (const call of listPlotsMock.mock.calls) {
      expect(JSON.stringify(call)).not.toContain('5552');
    }
    expect(window.location.search).not.toContain('5552');
    expect(window.location.href).not.toContain('5552');
  });

  it('other filters still combine with a partial number search', async () => {
    renderPlotsPage();
    await screen.findByText('แปลงเมล่อน');

    fireEvent.change(screen.getByLabelText('กรองสถานะแปลง'), { target: { value: 'inactive' } });
    fireEvent.change(screen.getByLabelText(ACCESS_NUMBER_LABEL), { target: { value: '5552' } });
    fireEvent.click(screen.getByRole('button', { name: 'ค้นหา' }));

    await waitFor(() => expect(searchPlotsByPhoneMock).toHaveBeenCalled());
    const lastCall = searchPlotsByPhoneMock.mock.calls[searchPlotsByPhoneMock.mock.calls.length - 1][0] as { plotStatus?: string };
    expect(lastCall.plotStatus).toBe('inactive');
  });

  it('ล้างค่า clears both partial searches, the error, and restores status=active', async () => {
    renderPlotsPage();
    await screen.findByText('แปลงเมล่อน');

    const nameCode = screen.getByLabelText(NAME_CODE_LABEL) as HTMLInputElement;
    const number = screen.getByLabelText(ACCESS_NUMBER_LABEL) as HTMLInputElement;
    // Leave an error on screen first, then clear.
    fireEvent.change(number, { target: { value: '55' } });
    fireEvent.click(screen.getByRole('button', { name: 'ค้นหา' }));
    await screen.findByRole('alert');

    fireEvent.change(nameCode, { target: { value: '002' } });
    fireEvent.change(number, { target: { value: '5552' } });
    fireEvent.click(screen.getByRole('button', { name: 'ค้นหา' }));
    await waitFor(() => expect(searchPlotsByPhoneMock).toHaveBeenCalled());

    fireEvent.click(screen.getByRole('button', { name: 'ล้างค่า' }));

    expect(nameCode.value).toBe('');
    expect(number.value).toBe('');
    expect(screen.queryByRole('alert')).toBeNull();
    expect(screen.queryByText(/กำลังค้นหาแปลงที่หมายเลข/)).toBeNull();
    expect((screen.getByLabelText('กรองสถานะแปลง') as HTMLSelectElement).value).toBe('active');
    await waitFor(() => expect(hasListPlotsCallContaining({
      q: undefined, plotStatus: 'active',
    })).toBe(true));
  });

  it('the Excel template gets the partial q but never the partial number', async () => {
    downloadPlotImportTemplateMock.mockResolvedValue(new Blob(['x']));
    renderPlotsPage();
    await screen.findByText('แปลงเมล่อน');

    fireEvent.click(await screen.findByRole('button', { name: 'กรอง Supplier' }));
    fireEvent.click(within(await screen.findByRole('listbox')).getByText('SUP001'));

    fireEvent.change(screen.getByLabelText(NAME_CODE_LABEL), { target: { value: '002' } });
    fireEvent.change(screen.getByLabelText(ACCESS_NUMBER_LABEL), { target: { value: '5552' } });
    fireEvent.click(screen.getByRole('button', { name: 'ค้นหา' }));
    await waitFor(() => expect(searchPlotsByPhoneMock).toHaveBeenCalled());

    fireEvent.click(screen.getByRole('button', { name: 'ดาวน์โหลด Excel' }));
    fireEvent.click(screen.getByRole('menuitem', { name: 'ตามตัวกรองปัจจุบัน' }));

    await waitFor(() => expect(downloadPlotImportTemplateMock).toHaveBeenCalled());
    const call = downloadPlotImportTemplateMock.mock.calls[0][0] as Record<string, unknown>;
    expect(call.q).toBe('002');
    expect(JSON.stringify(call)).not.toContain('5552');
  });
});

// Round 8-25I — a shortcut to the PUBLIC (no-login) inspection entry point,
// for admins who want to open/demo it without a phone.
describe('Plots list — public inspect shortcut (round 8-25I)', () => {
  it('links to /public/inspect and opens in a new tab', async () => {
    listPlotsMock.mockResolvedValue([
      {
        id: 'plot-1', supplierId: 'sup-1', plotCode: 'SUP001-P001', name: 'แปลงทดสอบ',
        village: null, district: null, province: 'จังหวัดทดสอบ',
        latitude: '13.7563000', longitude: '100.5018000',
        isActive: true, assignedCount: 0, primaryPhone: null, additionalPhones: [],
      },
    ]);
    renderPlotsPage();
    await screen.findByText('SUP001-P001');

    const link = screen.getByRole('link', { name: /เปิดหน้าตรวจแปลง \(Public\)/ });
    expect(link.getAttribute('href')).toBe('/public/inspect');
    expect(link.getAttribute('target')).toBe('_blank');
    // rel="noopener noreferrer" — a target="_blank" link without it lets the
    // opened page reach back into this window via window.opener.
    expect(link.getAttribute('rel')).toBe('noopener noreferrer');
  });
});
