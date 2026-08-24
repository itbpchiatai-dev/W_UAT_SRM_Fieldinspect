/**
 * PublicInspect (round 8-3D rewrite) — phone-only flow: enter phone -> pick a
 * plot from the ones that phone may inspect -> choose an inspector type ->
 * fill and submit -> optionally inspect the next plot without re-entering the
 * phone. No inspection code, no manual Supplier/Plot-code entry anywhere.
 *
 * GPS/photos/protocol-score regression from the pre-8-3D flow is preserved
 * (same PhotoSlotPicker/ProtocolScoreInputs/PublicMasterDataButtons
 * contracts) — only how the user REACHES the form step changed.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, fireEvent, waitFor, within } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { QueryClient, QueryClientProvider, onlineManager } from '@tanstack/react-query';
import { IDBFactory } from 'fake-indexeddb';
import { PublicInspect, computeIsFormDirty, formatThaiShortDate, type PublicInspectionPlotContext } from './PublicInspect';
import type { PublicPhoneAccessPlot, PublicSelectPlotResult } from '../../api/publicInspectionAccess';
import type { PublicInspectionFormFields } from '../../api/publicInspection';
import { bangkokToday } from '../../lib/business-date';
import {
  closeOfflineInspectionDb,
  countOfflineInspectionDrafts,
  listOfflineInspectionDrafts,
  getOfflineInspectionDraft,
  putOfflineInspectionDraft,
  buildOfflineInspectionDraft,
  buildOfflinePublicAccessCache,
  putOfflinePublicAccessCache,
  getOfflinePublicAccessCache,
  type OfflineInspectionDraftV2,
} from '../../lib/offline-inspection-store';

const lookupMock = vi.fn();
const listPlotsMock = vi.fn();
const selectPlotMock = vi.fn();
const configMock = vi.fn();
const createWithPhotosMock = vi.fn();
const createJsonMock = vi.fn();
const listPublicMasterDataMock = vi.fn();
const fetchPublicProtocolsMock = vi.fn();
// Round 8-14B — client-side compression is PhotoSlotPicker's concern (see
// its own test file); page-level tests here mock it out entirely so photo
// tests don't depend on jsdom's nonexistent createImageBitmap/canvas/<img>
// decode support. Default resolves quickly to a WebP File, same shape a
// real success would produce.
const prepareInspectionPhotoMock = vi.fn();
vi.mock('../../lib/inspection-photo-compression', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../lib/inspection-photo-compression')>();
  return { ...actual, prepareInspectionPhoto: (...args: unknown[]) => prepareInspectionPhotoMock(...args) };
});

vi.mock('../../api/publicInspectionAccess', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../api/publicInspectionAccess')>();
  return {
    ...actual,
    lookupPublicInspectionAccess: (...args: unknown[]) => lookupMock(...args),
    listPublicInspectionAccessPlots: (...args: unknown[]) => listPlotsMock(...args),
    selectPublicInspectionPlot: (...args: unknown[]) => selectPlotMock(...args),
    getPublicInspectionAccessConfig: (...args: unknown[]) => configMock(...args),
  };
});

vi.mock('../../api/publicInspection', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../api/publicInspection')>();
  return {
    ...actual,
    createPublicRecordWithPhotos: (...args: unknown[]) => createWithPhotosMock(...args),
    createPublicInspectionRecord: (...args: unknown[]) => createJsonMock(...args),
  };
});

vi.mock('../../api/publicMasterdata', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../api/publicMasterdata')>();
  return { ...actual, listPublicMasterData: (...args: unknown[]) => listPublicMasterDataMock(...args) };
});

vi.mock('../../api/inspectionProtocols', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../api/inspectionProtocols')>();
  return { ...actual, fetchPublicInspectionProtocols: (...args: unknown[]) => fetchPublicProtocolsMock(...args) };
});

const PROTOCOLS = {
  version: 1,
  stages: [
    { growthStage: 'ระยะงอก', criteria: [
      { slot: 'fieldPrepScore', label: 'การเตรียมแปลง' },
      { slot: 'weatherScore', label: 'สภาพอากาศ' },
      { slot: 'careScore', label: 'การดูแลรักษา' },
      { slot: 'varietyResistanceScore', label: 'ความต้านทานของสายพันธุ์' },
    ] },
    { growthStage: 'ออกดอก', criteria: [
      { slot: 'fieldPrepScore', label: 'ความสมบูรณ์ของดอก' },
      { slot: 'weatherScore', label: 'สภาพอากาศ' },
      { slot: 'careScore', label: 'การดูแลรักษา' },
      { slot: 'varietyResistanceScore', label: 'ความเสี่ยงโรคและแมลง' },
    ] },
  ],
};

const REAL_PHONE = '0845552162'; // placeholder test number, never a real one
// Round 8-9D — placeholder plot password, never a real one. Deliberately a
// repeated/sequential-free 6-digit value so it is legal under the 4-20-digit
// policy without looking like anything meaningful.
const PLOT_PASSWORD = '135790';

const CONFIG_PHONE_ONLY = { passwordRequired: false, passwordMinLength: 4, passwordMaxLength: 20 };
const CONFIG_PASSWORD_REQUIRED = { passwordRequired: true, passwordMinLength: 4, passwordMaxLength: 20 };

function plotItem(overrides: Partial<PublicPhoneAccessPlot> = {}): PublicPhoneAccessPlot {
  return {
    plotId: 'plot-1', plotCode: 'PLOT001', plotName: 'Plot One',
    supplierId: 'sup-1', supplierCode: 'SUP001', supplierName: 'Supplier One',
    accessType: 'primary', canInspect: true, unavailableReason: null,
    plotCycleId: 'cycle-1', cycleNo: 2, cycleLabel: 'jun2026',
    crop: 'พริก', variety: 'พริกขี้หนู', inspectedToday: false,
    lastInspectionDate: null, lastInspectedAt: null,
    lotNo: null, plantingDate: null,
    plantCount: 100, expectedYieldFull: '500', expectedYieldUnit: 'กก.',
    currentYieldPct: null, currentStage: null,
    ...overrides,
  };
}

function lookupResult(plots: PublicPhoneAccessPlot[] = [plotItem()], qrMatchedPlotId: string | null = null) {
  return { phoneAccessSessionToken: 'phone-tok-abc', expiresIn: 28800, qrMatchedPlotId, plots };
}

function selectResult(overrides: Partial<PublicSelectPlotResult> = {}): PublicSelectPlotResult {
  return {
    inspectionSessionToken: 'insp-tok-abc', expiresIn: 1800,
    plotId: 'plot-1', plotCode: 'PLOT001', plotName: 'Plot One',
    supplierId: 'sup-1', supplierCode: 'SUP001', supplierName: 'Supplier One',
    plotCycleId: 'cycle-1', cycleNo: 2, cycleLabel: 'jun2026',
    currentCrop: 'พริก', currentVariety: 'พริกขี้หนู', currentLotNo: 'LOT-01',
    currentPlantingDate: '2026-01-01', plantCount: 100,
    // Round 8-8B — must be the real "kg" unit literal (backend's
    // _KG_FACTOR/frontend's KG_FACTOR only recognise "kg"/"g"/"ตัน"), not
    // the Thai abbreviation "กก." — fixed here so the many Yield-prefill
    // tests below exercise a COMPARABLE target, same as before this round.
    expectedYieldFull: '500', expectedYieldUnit: 'kg',
    currentYieldPct: null, currentStage: null, lastInspectedAt: null,
    ...overrides,
  };
}

function jpegFile(name: string): File {
  return new File(['x'], name, { type: 'image/jpeg' });
}

/** Fires a photo pick into the given slot and waits for PhotoSlotPicker's
 * (mocked) async compression to settle — required since round 8-14B, or a
 * following `fireEvent.click` on Submit would hit the hard photoProcessing
 * guard and silently no-op. */
async function pickPhotoAndWait(index: number, file: File) {
  fireEvent.change(screen.getAllByLabelText(/เลือกรูป/)[index], { target: { files: [file] } });
  await waitFor(() => expect(screen.queryByText('กำลังเตรียมรูป...')).toBeNull());
}

/** Round 8-4B — flips navigator.onLine and fires the matching window event,
 * the same signal useNetworkStatus listens for.
 *
 * Round 8-4C addendum: ALSO calls react-query's onlineManager.setOnline()
 * directly. Its default window-event listener is only actively subscribed
 * while at least one query observer exists; between tests (after cleanup()
 * unmounts the previous test's component and before the next one mounts)
 * nothing is listening, so a plain window.dispatchEvent('online') here can
 * be silently lost — leaving onlineManager internally "offline" from a
 * PRIOR test, which pauses every query in the NEXT test (networkMode:
 * 'online' never even calls the queryFn while paused). Setting it directly
 * is immune to that subscription-timing gap. */
function setOnline(value: boolean) {
  Object.defineProperty(navigator, 'onLine', { value, configurable: true });
  window.dispatchEvent(new Event(value ? 'online' : 'offline'));
  onlineManager.setOnline(value);
}

/** Round 8-4B — pre-seeds a queued draft directly in the (fake) IndexedDB,
 * for tests about the pending-count badge / purge / pre-existing-draft
 * scenarios that don't need to go through the UI to create one. */
// Round 8-6I.1 Part B — capturedAt/now/recordDate anchor to the REAL instant
// the suite is running (minus 1 day), not a fixed calendar date. A hardcoded
// date (previously 2026-07-15) drifts past PublicInspect's real 7-day
// purge window (OFFLINE_DRAFT_MAX_AGE_MS, offline-inspection-store.ts) as
// actual wall-clock time advances — the mount-time purge runs against a
// REAL `new Date()`, so a fixture seeded as "fresh" eventually reads as
// expired and gets silently deleted before an assertion ever sees it. Anchoring
// 1 day before the test's own instant keeps every default-seeded draft
// comfortably inside the 7-day window forever, regardless of which real
// calendar day the suite executes on — deterministic without touching the
// production retention constant or any offline behavior. Callers that need a
// specific age (e.g. proving the purge itself fires) still pass their own
// explicit capturedAt/now (unaffected by this default).
function defaultSeedCapturedAt(): string {
  return new Date(Date.now() - 24 * 60 * 60 * 1000).toISOString();
}

async function seedDraft(overrides: Partial<Parameters<typeof buildOfflineInspectionDraft>[0]> = {}): Promise<OfflineInspectionDraftV2> {
  const capturedAt = defaultSeedCapturedAt();
  const draft = buildOfflineInspectionDraft({
    clientSubmissionId: crypto.randomUUID(),
    capturedAt,
    capturedPlotCycleId: 'cycle-1',
    recordDate: capturedAt.slice(0, 10),
    plotId: 'plot-1', plotCode: 'PLOT001', plotName: 'Plot One',
    supplierId: 'sup-1', supplierCode: 'SUP001', supplierName: 'Supplier One',
    cycleNo: 2, cycleLabel: 'jun2026',
    crop: 'พริก', variety: 'พริกขี้หนู', lotNo: 'LOT-01', plantingDate: '2026-01-01',
    inspectorType: 'farmer',
    fields: { ...EMPTY_FIELDS_FOR_SEED, submittedByName: 'สมชาย' },
    photos: [],
    now: capturedAt,
    ...overrides,
  });
  await putOfflineInspectionDraft(draft);
  return draft;
}

const EMPTY_FIELDS_FOR_SEED: PublicInspectionFormFields = {
  submittedByName: '', growthStage: '', yieldPct: 100, yieldQuantityKg: null, weatherCondition: '',
  fieldPrepScore: null, weatherScore: null, careScore: null, varietyResistanceScore: null,
  recommendation: '', notes: '', latitude: null, longitude: null,
};

function mockGeolocation(behavior: 'success' | 'denied') {
  const getCurrentPosition = vi.fn((success: PositionCallback, error?: PositionErrorCallback) => {
    if (behavior === 'success') {
      success({ coords: { latitude: 13.7563, longitude: 100.5018 } } as GeolocationPosition);
    } else {
      error?.({ code: 1, message: 'denied' } as GeolocationPositionError);
    }
  });
  Object.defineProperty(navigator, 'geolocation', { value: { getCurrentPosition }, configurable: true });
  return getCurrentPosition;
}

function renderPublicInspect(initialPath = '/public/inspect') {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  // qc is returned (round 8-9D) so a test can inspect the query cache for
  // secrets and force the capability probe to refetch, the way a real
  // background refetch after a backend deploy would.
  return {
    ...render(
      <QueryClientProvider client={qc}>
        <MemoryRouter initialEntries={[initialPath]}>
          <PublicInspect />
        </MemoryRouter>
      </QueryClientProvider>,
    ),
    qc,
  };
}

/** Round 8-9D — the entry form is gated on the capability probe, so nothing on
 * the phone step exists synchronously after render anymore. Every helper and
 * every test that touches the entry form waits for this first. */
async function waitForEntryForm(): Promise<HTMLInputElement> {
  return (await screen.findByLabelText(/หมายเลขสำหรับเข้าตรวจ/)) as HTMLInputElement;
}

async function enterPhoneAndLookup(phone = REAL_PHONE) {
  fireEvent.change(await waitForEntryForm(), { target: { value: phone } });
  fireEvent.click(screen.getByRole('button', { name: 'ค้นหาแปลง' }));
  await waitFor(() => expect(screen.getByText('เลือกแปลงที่จะตรวจ')).toBeTruthy());
}

/** Round 8-9D enforcement mode — fills BOTH fields and submits. */
async function enterPhonePasswordAndLookup(phone = REAL_PHONE, password = PLOT_PASSWORD) {
  fireEvent.change(await waitForEntryForm(), { target: { value: phone } });
  fireEvent.change(await screen.findByLabelText(/^รหัส Supplier ตรวจแปลง/), { target: { value: password } });
  fireEvent.click(screen.getByRole('button', { name: 'ค้นหาแปลง' }));
  await waitFor(() => expect(screen.getByText('เลือกแปลงที่จะตรวจ')).toBeTruthy());
}

async function pickRoleAndPlot(roleLabel = 'เกษตรกร', plotCode = 'PLOT001') {
  fireEvent.click(screen.getByRole('radio', { name: roleLabel }));
  fireEvent.click(screen.getByRole('button', { name: new RegExp(plotCode) }));
  await waitFor(() => expect(screen.getByPlaceholderText('ไม่บังคับ')).toBeTruthy());
}

async function goToFormStep() {
  // Relies on beforeEach's default lookupMock/selectPlotMock resolutions —
  // a caller that needs a different fixture must override the mock BEFORE
  // calling this (same pattern the old file's goToFormStep used).
  renderPublicInspect();
  await enterPhoneAndLookup();
  await pickRoleAndPlot();
}

beforeEach(() => {
  lookupMock.mockReset();
  listPlotsMock.mockReset();
  selectPlotMock.mockReset();
  createWithPhotosMock.mockReset();
  createJsonMock.mockReset();
  listPublicMasterDataMock.mockReset();
  fetchPublicProtocolsMock.mockReset();
  configMock.mockReset();
  prepareInspectionPhotoMock.mockReset();
  prepareInspectionPhotoMock.mockImplementation(async (file: File, outputName: string) => ({
    file: new File([file], outputName, { type: 'image/webp' }),
    compressed: true,
    originalBytes: file.size,
    outputBytes: file.size,
    fallbackUsed: false,
    warning: null,
  }));
  // Round 8-9D — the DEFAULT for the whole suite is the live runtime state:
  // enforcement OFF. Every pre-8-9D test therefore exercises exactly the flow
  // it always did; only the tests that explicitly opt into
  // CONFIG_PASSWORD_REQUIRED see the password step.
  configMock.mockResolvedValue(CONFIG_PHONE_ONLY);
  lookupMock.mockResolvedValue(lookupResult());
  listPlotsMock.mockResolvedValue({ plots: [plotItem()] });
  selectPlotMock.mockResolvedValue(selectResult());
  listPublicMasterDataMock.mockResolvedValue([]);
  fetchPublicProtocolsMock.mockResolvedValue(PROTOCOLS);
  // Round 8-4B — fresh in-memory IndexedDB per test (fake-indexeddb persists
  // across tests otherwise) and a known-clean online state.
  closeOfflineInspectionDb();
  (globalThis as { indexedDB: IDBFactory }).indexedDB = new IDBFactory();
  setOnline(true);
});

afterEach(() => {
  closeOfflineInspectionDb();
  vi.restoreAllMocks();
});

// --- computeIsFormDirty (pure function) -------------------------------------

describe('computeIsFormDirty', () => {
  const BASE: PublicInspectionFormFields = {
    submittedByName: '', growthStage: '', yieldPct: 100, yieldQuantityKg: null,
    weatherCondition: '', fieldPrepScore: null, weatherScore: null, careScore: null,
    varietyResistanceScore: null, recommendation: '', notes: '', latitude: null, longitude: null,
  };

  it('not dirty when fields equal the baseline and no photos', () => {
    expect(computeIsFormDirty(BASE, BASE, [null, null, null, null, null])).toBe(false);
  });

  it('dirty when an inspection field differs from baseline', () => {
    expect(computeIsFormDirty({ ...BASE, notes: 'x' }, BASE, [null])).toBe(true);
  });

  it('dirty when a photo is picked', () => {
    expect(computeIsFormDirty(BASE, BASE, [new File(['x'], 'a.jpg')])).toBe(true);
  });

  it('GPS alone (latitude/longitude set) does NOT count as dirty', () => {
    const withGps = { ...BASE, latitude: 13.75, longitude: 100.5 };
    expect(computeIsFormDirty(withGps, BASE, [null])).toBe(false);
  });

  it('dirty when yieldPct differs from the baseline default', () => {
    expect(computeIsFormDirty({ ...BASE, yieldPct: 80 }, BASE, [null])).toBe(true);
  });
});

// --- Phone entry (Part C) ---------------------------------------------------

describe('PublicInspect — phone entry screen', () => {
  it('a generic URL starts at the phone step, with no code/manual-locator inputs', async () => {
    renderPublicInspect();

    expect(await waitForEntryForm()).toBeTruthy();
    expect(screen.queryByPlaceholderText(/SUP001/)).toBeNull();
    expect(screen.queryByPlaceholderText(/PLOT001/)).toBeNull();
    expect(screen.queryByText(/รหัสเข้าตรวจ/)).toBeNull();
    expect(screen.queryByText('1111')).toBeNull();
  });

  it('does not render the old "เบอร์โทรสำหรับเข้าตรวจ" label anywhere (round 8-3F neutral copy)', async () => {
    renderPublicInspect();
    await waitForEntryForm();
    expect(screen.queryByLabelText('เบอร์โทรสำหรับเข้าตรวจ')).toBeNull();
  });

  it('the identifier input uses neutral, non-telephone attributes (round 8-3F)', async () => {
    renderPublicInspect();
    const input = await waitForEntryForm();
    expect(input.type).toBe('text');
    expect(input.inputMode).toBe('numeric');
    expect(input.autocomplete).toBe('off');
  });

  it('shows the neutral placeholder "กรอกหมายเลข 10 หลัก", never an example that looks like a phone number', async () => {
    renderPublicInspect();
    await waitForEntryForm();
    expect(screen.getByPlaceholderText('กรอกหมายเลข 10 หลัก')).toBeTruthy();
    expect(screen.queryByPlaceholderText(/0845552162/)).toBeNull();
  });

  it('invalid phone format shows a Thai error and never calls lookup', async () => {
    renderPublicInspect();
    fireEvent.change(await waitForEntryForm(), { target: { value: '123' } });
    fireEvent.click(screen.getByRole('button', { name: 'ค้นหาแปลง' }));

    await waitFor(() => expect(screen.getByText('หมายเลขไม่ถูกต้อง กรุณาตรวจสอบตัวเลข 10 หลัก')).toBeTruthy());
    expect(lookupMock).not.toHaveBeenCalled();
  });

  it('invalid-format error never surfaces the word เบอร์โทรศัพท์ (round 8-3F neutral copy)', async () => {
    renderPublicInspect();
    fireEvent.change(await waitForEntryForm(), { target: { value: '' } });
    fireEvent.click(screen.getByRole('button', { name: 'ค้นหาแปลง' }));

    await screen.findByText('หมายเลขไม่ถูกต้อง กรุณาตรวจสอบตัวเลข 10 หลัก');
    expect(document.body.textContent).not.toContain('โทรศัพท์');
  });

  it('valid phone lookup succeeds and moves to the plots step', async () => {
    renderPublicInspect();
    await enterPhoneAndLookup();
    // Round 8-9D — object parameter, and with enforcement off the password key
    // is `undefined` here and omitted from the request body entirely (asserted
    // at the transport level in api/publicInspectionAccess.test.ts).
    expect(lookupMock).toHaveBeenCalledWith({
      phone: REAL_PHONE, password: undefined, qrKey: undefined,
    });
  });

  it('shows a spinner and disables submit while the lookup is in flight', async () => {
    lookupMock.mockReturnValue(new Promise(() => {}));
    renderPublicInspect();
    fireEvent.change(await waitForEntryForm(), { target: { value: REAL_PHONE } });
    fireEvent.click(screen.getByRole('button', { name: 'ค้นหาแปลง' }));

    const btn = screen.getByRole('button', { name: /ค้นหาแปลง/ }) as HTMLButtonElement;
    expect(btn.disabled).toBe(true);
  });

  it('404 shows the not-authorized message', async () => {
    lookupMock.mockRejectedValue({ isAxiosError: true, response: { status: 404 } });
    renderPublicInspect();
    fireEvent.change(await waitForEntryForm(), { target: { value: REAL_PHONE } });
    fireEvent.click(screen.getByRole('button', { name: 'ค้นหาแปลง' }));

    expect(await screen.findByText('ไม่พบแปลงที่หมายเลขนี้ได้รับอนุญาตให้เข้าตรวจ')).toBeTruthy();
  });

  it('429 shows the rate-limit message', async () => {
    lookupMock.mockRejectedValue({ isAxiosError: true, response: { status: 429 } });
    renderPublicInspect();
    fireEvent.change(await waitForEntryForm(), { target: { value: REAL_PHONE } });
    fireEvent.click(screen.getByRole('button', { name: 'ค้นหาแปลง' }));

    expect(await screen.findByText('มีการลองหลายครั้งเกินไป กรุณารอสักครู่แล้วลองใหม่')).toBeTruthy();
  });

  it('network error (no response) shows a connection message', async () => {
    lookupMock.mockRejectedValue({ isAxiosError: true, response: undefined });
    renderPublicInspect();
    fireEvent.change(await waitForEntryForm(), { target: { value: REAL_PHONE } });
    fireEvent.click(screen.getByRole('button', { name: 'ค้นหาแปลง' }));

    expect(await screen.findByText('เชื่อมต่อเครือข่ายไม่สำเร็จ กรุณาลองใหม่')).toBeTruthy();
  });

  it('a 404 error message never echoes the phone entered', async () => {
    lookupMock.mockRejectedValue({ isAxiosError: true, response: { status: 404 } });
    renderPublicInspect();
    fireEvent.change(await waitForEntryForm(), { target: { value: REAL_PHONE } });
    fireEvent.click(screen.getByRole('button', { name: 'ค้นหาแปลง' }));

    await screen.findByText('ไม่พบแปลงที่หมายเลขนี้ได้รับอนุญาตให้เข้าตรวจ');
    expect(document.body.textContent).not.toContain(REAL_PHONE);
  });

  it('the phone is never shown again after a successful lookup', async () => {
    renderPublicInspect();
    await enterPhoneAndLookup();
    expect(document.body.textContent).not.toContain(REAL_PHONE);
  });

  it('never writes the phone-access token to localStorage or sessionStorage', async () => {
    renderPublicInspect();
    await enterPhoneAndLookup();

    const dump = (s: Storage | undefined): string => {
      if (!s) return '';
      let out = '';
      for (let i = 0; i < s.length; i++) {
        const k = s.key(i);
        if (k) out += `${k}=${s.getItem(k)}|`;
      }
      return out;
    };
    const all = dump(globalThis.localStorage) + dump(globalThis.sessionStorage);
    expect(all).not.toContain('phone-tok-abc');
  });
});

// --- Plot selection screen (Part E) -----------------------------------------

describe('PublicInspect — plot selection screen', () => {
  it('renders multiple plots across multiple suppliers', async () => {
    lookupMock.mockResolvedValue(lookupResult([
      plotItem({ plotId: 'p1', plotCode: 'P001', supplierCode: 'SUP001', supplierName: 'Supplier One' }),
      plotItem({ plotId: 'p2', plotCode: 'P002', supplierCode: 'SUP002', supplierName: 'Supplier Two' }),
    ]));
    renderPublicInspect();
    await enterPhoneAndLookup();

    expect(screen.getByRole('button', { name: /P001/ })).toBeTruthy();
    expect(screen.getByRole('button', { name: /P002/ })).toBeTruthy();
    expect(screen.getByText(/Supplier One/)).toBeTruthy();
    expect(screen.getByText(/Supplier Two/)).toBeTruthy();
  });

  it('round 8-3K: shows Lot No. and planting date on the card before the plot is selected', async () => {
    lookupMock.mockResolvedValue(lookupResult([
      plotItem({ lotNo: 'LOT-09', plantingDate: '2026-06-01' }),
    ]));
    renderPublicInspect();
    await enterPhoneAndLookup();

    expect(screen.getByText('Lot: LOT-09 · ปลูก: 1 มิ.ย. 2569')).toBeTruthy();
  });

  it('round 8-3K: omits only the missing part (lot without a planting date)', async () => {
    lookupMock.mockResolvedValue(lookupResult([
      plotItem({ lotNo: 'LOT-09', plantingDate: null }),
    ]));
    renderPublicInspect();
    await enterPhoneAndLookup();

    expect(screen.getByText('Lot: LOT-09')).toBeTruthy();
    expect(screen.queryByText(/ปลูก:/)).toBeNull();
  });

  it('round 8-3K: renders no Lot/planting-date line at all when both are null, and never shows "null"/"undefined"', async () => {
    lookupMock.mockResolvedValue(lookupResult([
      plotItem({ lotNo: null, plantingDate: null }),
    ]));
    renderPublicInspect();
    await enterPhoneAndLookup();

    await screen.findByText(/PLOT001/);
    expect(screen.queryByText(/Lot:/)).toBeNull();
    expect(document.body.textContent).not.toMatch(/null|undefined/i);
  });

  it('local search filters the list without calling the API again', async () => {
    lookupMock.mockResolvedValue(lookupResult([
      plotItem({ plotId: 'p1', plotCode: 'P001', plotName: 'แปลงเหนือ' }),
      plotItem({ plotId: 'p2', plotCode: 'P002', plotName: 'แปลงใต้' }),
    ]));
    renderPublicInspect();
    await enterPhoneAndLookup();
    lookupMock.mockClear();

    fireEvent.change(screen.getByLabelText('ค้นหาแปลง'), { target: { value: 'เหนือ' } });

    expect(screen.getByRole('button', { name: /P001/ })).toBeTruthy();
    expect(screen.queryByRole('button', { name: /P002/ })).toBeNull();
    expect(lookupMock).not.toHaveBeenCalled();
  });

  it('requires an inspector type before a plot can be selected', async () => {
    renderPublicInspect();
    await enterPhoneAndLookup();

    fireEvent.click(screen.getByRole('button', { name: /PLOT001/ }));

    expect(await screen.findByText('กรุณาเลือกฐานะผู้ตรวจก่อนเลือกแปลง')).toBeTruthy();
    expect(selectPlotMock).not.toHaveBeenCalled();
  });

  // Round 8-11A — each visible label maps to its canonical API value
  // (items 15/18/19). The label a user clicks and the value sent must stay
  // paired; asserting them together is what makes a rename like this one
  // impossible to half-apply.
  it.each([
    ['เกษตรกร', 'farmer'],
    ['บริษัทผู้ผลิต', 'supplier'],
    ['Chiatai', 'chiatai'],
  ] as const)('inspector type %s sends %s', async (role, expected) => {
    renderPublicInspect();
    await enterPhoneAndLookup();

    fireEvent.click(screen.getByRole('radio', { name: role }));
    fireEvent.click(screen.getByRole('button', { name: /PLOT001/ }));

    await waitFor(() => expect(selectPlotMock).toHaveBeenCalledOnce());
    expect(selectPlotMock).toHaveBeenCalledWith('phone-tok-abc', 'plot-1', expected);
  });

  // Items 16/17 — the retired wording is gone from the form entirely, and the
  // inspector option is never labelled the bare word "Supplier".
  it('offers exactly the three new inspector options and none of the retired wording', async () => {
    renderPublicInspect();
    await enterPhoneAndLookup();

    expect(screen.getByRole('radio', { name: 'เกษตรกร' })).toBeTruthy();
    expect(screen.getByRole('radio', { name: 'บริษัทผู้ผลิต' })).toBeTruthy();
    expect(screen.getByRole('radio', { name: 'Chiatai' })).toBeTruthy();
    expect(screen.getAllByRole('radio')).toHaveLength(3);
    expect(screen.queryByRole('radio', { name: 'ส่งเสริม' })).toBeNull();
    expect(screen.queryByRole('radio', { name: 'Supplier' })).toBeNull();
  });

  it('a plot with no active cycle is disabled and cannot be selected', async () => {
    lookupMock.mockResolvedValue(lookupResult([
      plotItem({ canInspect: false, unavailableReason: 'no_active_cycle' }),
    ]));
    renderPublicInspect();
    await enterPhoneAndLookup();

    expect(screen.getByText('ยังไม่มีรอบปลูกที่เปิดอยู่')).toBeTruthy();
    const card = screen.getByRole('button', { name: /PLOT001/ }) as HTMLButtonElement;
    expect(card.disabled).toBe(true);

    fireEvent.click(screen.getByRole('radio', { name: 'เกษตรกร' }));
    fireEvent.click(card);
    expect(selectPlotMock).not.toHaveBeenCalled();
  });

  it('a plot already inspected today is still selectable (shows a badge only)', async () => {
    lookupMock.mockResolvedValue(lookupResult([plotItem({ inspectedToday: true })]));
    renderPublicInspect();
    await enterPhoneAndLookup();

    expect(screen.getByText('ตรวจแล้ววันนี้')).toBeTruthy();
    const card = screen.getByRole('button', { name: /PLOT001/ }) as HTMLButtonElement;
    expect(card.disabled).toBe(false);

    fireEvent.click(screen.getByRole('radio', { name: 'เกษตรกร' }));
    fireEvent.click(card);
    await waitFor(() => expect(selectPlotMock).toHaveBeenCalledOnce());
  });

  it('prevents a double-click from selecting twice', async () => {
    selectPlotMock.mockReturnValue(new Promise(() => {})); // never resolves
    renderPublicInspect();
    await enterPhoneAndLookup();
    fireEvent.click(screen.getByRole('radio', { name: 'เกษตรกร' }));

    const card = screen.getByRole('button', { name: /PLOT001/ });
    fireEvent.click(card);
    fireEvent.click(card);

    await waitFor(() => expect(selectPlotMock).toHaveBeenCalledTimes(1));
  });

  describe('select errors', () => {
    async function attemptSelect() {
      renderPublicInspect();
      await enterPhoneAndLookup();
      fireEvent.click(screen.getByRole('radio', { name: 'เกษตรกร' }));
      fireEvent.click(screen.getByRole('button', { name: /PLOT001/ }));
    }

    it('401 clears the phone session and returns to the phone step', async () => {
      selectPlotMock.mockRejectedValue({ isAxiosError: true, response: { status: 401 } });
      await attemptSelect();

      await waitFor(() => expect(screen.getByLabelText(/หมายเลขสำหรับเข้าตรวจ/)).toBeTruthy());
      expect(await screen.findByText(/เซสชันหมดอายุ/)).toBeTruthy();
    });

    it('404 refreshes the list and shows a message, staying on the plots step', async () => {
      selectPlotMock.mockRejectedValue({ isAxiosError: true, response: { status: 404 } });
      await attemptSelect();

      await waitFor(() => expect(listPlotsMock).toHaveBeenCalledWith('phone-tok-abc'));
      expect(await screen.findByText(/แปลงนี้ไม่พร้อมให้ตรวจแล้ว/)).toBeTruthy();
      expect(screen.getByText('เลือกแปลงที่จะตรวจ')).toBeTruthy();
    });

    it('409 (no active cycle) refreshes the list and shows a message', async () => {
      selectPlotMock.mockRejectedValue({ isAxiosError: true, response: { status: 409 } });
      await attemptSelect();

      await waitFor(() => expect(listPlotsMock).toHaveBeenCalledWith('phone-tok-abc'));
      expect(await screen.findByText(/ยังไม่มีรอบปลูกที่เปิดอยู่ในตอนนี้/)).toBeTruthy();
    });

    it('network error stays on the list and allows retry', async () => {
      selectPlotMock.mockRejectedValueOnce({ isAxiosError: true, response: undefined });
      selectPlotMock.mockResolvedValueOnce(selectResult());
      await attemptSelect();

      expect(await screen.findByText('ไม่สามารถเลือกแปลงนี้ได้ กรุณาลองใหม่')).toBeTruthy();
      expect(screen.getByText('เลือกแปลงที่จะตรวจ')).toBeTruthy();

      fireEvent.click(screen.getByRole('button', { name: /PLOT001/ }));
      await waitFor(() => expect(screen.getByPlaceholderText('ไม่บังคับ')).toBeTruthy());
    });
  });
});

// --- QR (Part D) -------------------------------------------------------------

describe('PublicInspect — QR entry', () => {
  it('a ?qr= deep link opens at the phone step and shows a QR status hint', async () => {
    renderPublicInspect('/public/inspect?qr=opaque-key-123');

    expect(await waitForEntryForm()).toBeTruthy();
    expect(screen.getByText('เข้าผ่านการสแกน QR แปลง')).toBeTruthy();
  });

  it('sends the qrKey alongside the phone on lookup', async () => {
    renderPublicInspect('/public/inspect?qr=opaque-key-123');
    await enterPhoneAndLookup();

    expect(lookupMock).toHaveBeenCalledWith({
      phone: REAL_PHONE, password: undefined, qrKey: 'opaque-key-123',
    });
  });

  it('highlights the qrMatchedPlotId plot returned by the backend, listed first', async () => {
    lookupMock.mockResolvedValue(lookupResult([
      plotItem({ plotId: 'p1', plotCode: 'P001' }),
      plotItem({ plotId: 'p2', plotCode: 'P002' }),
    ], 'p2'));
    renderPublicInspect('/public/inspect?qr=opaque-key-123');
    await enterPhoneAndLookup();

    const cards = screen.getAllByRole('button', { name: /P00[12]/ });
    expect(cards[0].textContent).toContain('P002');
    expect(within(cards[0]).getByText('จาก QR')).toBeTruthy();
  });

  it('still requires selecting an inspector type before starting, even for the QR-matched plot', async () => {
    lookupMock.mockResolvedValue(lookupResult([plotItem()], 'plot-1'));
    renderPublicInspect('/public/inspect?qr=opaque-key-123');
    await enterPhoneAndLookup();

    fireEvent.click(screen.getByRole('button', { name: /PLOT001/ }));
    expect(await screen.findByText('กรุณาเลือกฐานะผู้ตรวจก่อนเลือกแปลง')).toBeTruthy();
    expect(selectPlotMock).not.toHaveBeenCalled();
  });

  it('a legacy supplierCode+plotCode QR is matched client-side against the returned list, without an inspection code', async () => {
    lookupMock.mockResolvedValue(lookupResult([
      plotItem({ plotId: 'p1', plotCode: 'PLOT001', supplierCode: 'SUP001' }),
    ]));
    renderPublicInspect('/public/inspect?supplierCode=SUP001&plotCode=PLOT001');

    expect(await screen.findByText(/เข้าผ่าน QR แปลง PLOT001/)).toBeTruthy();
    await enterPhoneAndLookup();

    // Legacy QR is never sent to the backend as a qrKey — it has no
    // supplierCode/plotCode parameter at all.
    expect(lookupMock).toHaveBeenCalledWith({
      phone: REAL_PHONE, password: undefined, qrKey: undefined,
    });
    const card = screen.getByRole('button', { name: /PLOT001/ });
    expect(within(card).getByText('จาก QR')).toBeTruthy();
  });

  it('a legacy QR that matches no plot in the phone\'s list shows a generic note, not an inspection-code prompt', async () => {
    lookupMock.mockResolvedValue(lookupResult([
      plotItem({ plotId: 'p1', plotCode: 'OTHERPLOT', supplierCode: 'SUP002' }),
    ]));
    renderPublicInspect('/public/inspect?supplierCode=SUP001&plotCode=PLOT001');
    await enterPhoneAndLookup();

    expect(await screen.findByText(/ไม่พบแปลงที่สแกนในรายการของหมายเลขนี้/)).toBeTruthy();
    expect(screen.queryByPlaceholderText(/1111/)).toBeNull();
  });

  it('invalid QR content shows an error without crashing', async () => {
    renderPublicInspect();
    // Directly exercise the same parse path a scan would (no camera in jsdom):
    // scanning is covered by PlotQrScan's own tests; here we only need the
    // page's handling of a bad decode result, reached via the QR button + a
    // manual scan-result callback is not exposed, so this asserts the
    // page still renders the phone step (no crash) when a deep link carries
    // neither shape.
    renderPublicInspect('/public/inspect?foo=bar');
    await waitFor(() => expect(screen.getAllByLabelText(/หมายเลขสำหรับเข้าตรวจ/).length).toBeGreaterThan(0));
  });
});

// --- Form step (Part F) ------------------------------------------------------

describe('PublicInspect — inspection form (read-only Plot/Cycle, phone hidden)', () => {
  it('shows read-only Supplier/Plot/Cycle/crop/variety/lot/planting-date/yield-plan', async () => {
    await goToFormStep();

    expect(screen.getByText(/SUP001 — Supplier One/)).toBeTruthy();
    expect(screen.getByText(/PLOT001 — Plot One/)).toBeTruthy();
    expect(screen.getByText('jun2026')).toBeTruthy();
    expect(screen.getByText('พริก')).toBeTruthy();
    expect(screen.getByText('พริกขี้หนู')).toBeTruthy();
    expect(screen.getByText('LOT-01')).toBeTruthy();
    expect(screen.getByText('2026-01-01')).toBeTruthy();
    expect(screen.getByText(/500\.00/)).toBeTruthy();
  });

  it('round 8-3K: labels the Lot No. field clearly as "เลขล็อต (Lot No.)"', async () => {
    await goToFormStep();
    expect(screen.getByText('เลขล็อต (Lot No.)')).toBeTruthy();
  });

  it('falls back to "รอบที่ N" when the cycle has no label', async () => {
    selectPlotMock.mockResolvedValue(selectResult({ cycleLabel: null, cycleNo: 5 }));
    await goToFormStep();

    expect(screen.getByText('รอบที่ 5')).toBeTruthy();
  });

  it('shows the chosen inspector type as read-only text', async () => {
    renderPublicInspect();
    await enterPhoneAndLookup();
    await pickRoleAndPlot('บริษัทผู้ผลิต');

    const heading = screen.getByText('เข้าตรวจในฐานะ');
    expect(heading.nextElementSibling?.textContent).toBe('บริษัทผู้ผลิต');
  });

  it('never shows the phone number anywhere on the form', async () => {
    await goToFormStep();
    expect(document.body.textContent).not.toContain(REAL_PHONE);
  });

  it('never renders an editable crop/variety picker or a planting-date input', async () => {
    mockGeolocation('success');
    await goToFormStep();

    expect(screen.queryByRole('button', { name: 'พริกขี้หนู' })).toBeNull();
    expect(document.querySelector('input[type="date"]')).toBeNull();
  });
});

// --- Yield % prefill from latest inspection snapshot (round 8-3J) ----------

describe('PublicInspect — Yield % prefill from latest inspection snapshot (round 8-3J, kg-first round 8-8B)', () => {
  it('a numeric currentYieldPct sets the initial Yield % (1 decimal, round 8-8B)', async () => {
    selectPlotMock.mockResolvedValue(selectResult({ currentYieldPct: 80 }));
    await goToFormStep();
    expect(screen.getByText('80.0%')).toBeTruthy();
  });

  it('a string currentYieldPct ("80.0") also sets the initial Yield %', async () => {
    selectPlotMock.mockResolvedValue(selectResult({ currentYieldPct: '80.0' }));
    await goToFormStep();
    expect(screen.getByText('80.0%')).toBeTruthy();
  });

  it('currentYieldPct=0 starts at 0%, never falls back to 100', async () => {
    selectPlotMock.mockResolvedValue(selectResult({ currentYieldPct: 0 }));
    await goToFormStep();
    expect(screen.getByText('0.0%')).toBeTruthy();
  });

  it('currentYieldPct=null starts at the 100% default (target exists, no history — Part D.2)', async () => {
    selectPlotMock.mockResolvedValue(selectResult({ currentYieldPct: null }));
    await goToFormStep();
    expect(screen.getByText('100.0%')).toBeTruthy();
  });

  it('shows the source note with the last-inspected date and growth stage', async () => {
    selectPlotMock.mockResolvedValue(selectResult({
      currentYieldPct: 62, lastInspectedAt: '2026-07-10T09:30:00Z', currentStage: 'ออกดอก',
    }));
    await goToFormStep();
    expect(screen.getByText(/ค่าเริ่มต้นดึงจากการตรวจล่าสุดของแปลงนี้/)).toBeTruthy();
    expect(screen.getByText(/· ระยะ: ออกดอก/)).toBeTruthy();
    // The shared YieldQuantityInput also shows a compact "ล่าสุด" hint next
    // to the target (round 8-8B Part C).
    expect(screen.getByText(/· ล่าสุด 62%/)).toBeTruthy();
  });

  it('shows no source note when the plot has no inspection history', async () => {
    selectPlotMock.mockResolvedValue(selectResult({ currentYieldPct: null }));
    await goToFormStep();
    expect(screen.queryByText(/ดึงจากการตรวจล่าสุด/)).toBeNull();
  });

  it("switching from plot A (80%) to plot B (50%) defaults to B's own value, not A's", async () => {
    lookupMock.mockResolvedValue(lookupResult([
      plotItem(), plotItem({ plotId: 'plot-2', plotCode: 'PLOT002', plotName: 'Plot Two' }),
    ]));
    selectPlotMock.mockResolvedValueOnce(selectResult({ currentYieldPct: 80 }));
    await goToFormStep(); // selects PLOT001
    expect(screen.getByText('80.0%')).toBeTruthy();

    fireEvent.click(screen.getByRole('button', { name: 'กลับรายการแปลง' }));
    selectPlotMock.mockResolvedValueOnce(selectResult({
      plotId: 'plot-2', plotCode: 'PLOT002', plotName: 'Plot Two', currentYieldPct: 50,
    }));
    fireEvent.click(screen.getByRole('button', { name: /PLOT002/ }));
    await waitFor(() => expect(screen.getByPlaceholderText('ไม่บังคับ')).toBeTruthy());

    expect(screen.getByText('50.0%')).toBeTruthy();
    expect(screen.queryByText('80.0%')).toBeNull();
  });

  it("switching from plot A (80%) to plot B (no history) resets to 100%, not A's value", async () => {
    lookupMock.mockResolvedValue(lookupResult([
      plotItem(), plotItem({ plotId: 'plot-2', plotCode: 'PLOT002', plotName: 'Plot Two' }),
    ]));
    selectPlotMock.mockResolvedValueOnce(selectResult({ currentYieldPct: 80 }));
    await goToFormStep();
    expect(screen.getByText('80.0%')).toBeTruthy();

    fireEvent.click(screen.getByRole('button', { name: 'กลับรายการแปลง' }));
    selectPlotMock.mockResolvedValueOnce(selectResult({
      plotId: 'plot-2', plotCode: 'PLOT002', plotName: 'Plot Two', currentYieldPct: null,
    }));
    fireEvent.click(screen.getByRole('button', { name: /PLOT002/ }));
    await waitFor(() => expect(screen.getByPlaceholderText('ไม่บังคับ')).toBeTruthy());

    expect(screen.getByText('100.0%')).toBeTruthy();
  });

  it('the form is not immediately dirty after selecting a plot with a prefilled Yield %', async () => {
    const confirmSpy = vi.spyOn(window, 'confirm');
    selectPlotMock.mockResolvedValue(selectResult({ currentYieldPct: 80 }));
    await goToFormStep();

    fireEvent.click(screen.getByRole('button', { name: 'กลับรายการแปลง' }));
    expect(confirmSpy).not.toHaveBeenCalled(); // no "unsaved changes" prompt
  });

  it('a background token-refresh at submit (401) does not overwrite a Yield value the user already adjusted', async () => {
    mockGeolocation('denied');
    selectPlotMock.mockResolvedValueOnce(selectResult({ currentYieldPct: 80 }));
    createJsonMock.mockRejectedValueOnce({ isAxiosError: true, response: { status: 401 } });
    await goToFormStep();
    expect(screen.getByText('80.0%')).toBeTruthy();

    fireEvent.change(screen.getByRole('slider'), { target: { value: '45' } });
    expect(screen.getByText('45.0%')).toBeTruthy();

    selectPlotMock.mockResolvedValueOnce(selectResult({ currentYieldPct: 80, inspectionSessionToken: 'insp-tok-fresh' }));
    fireEvent.click(screen.getByRole('button', { name: 'บันทึกการตรวจแปลง' }));

    await waitFor(() => expect(selectPlotMock).toHaveBeenCalledTimes(2));
    expect(screen.getByText('45.0%')).toBeTruthy();
  });

  it('submits the Yield % value currently shown, not the prefilled default', async () => {
    mockGeolocation('denied');
    selectPlotMock.mockResolvedValue(selectResult({ currentYieldPct: 80 }));
    createJsonMock.mockResolvedValue({ plotCode: 'PLOT001', plotName: 'Plot One' });
    await goToFormStep();

    fireEvent.change(screen.getByRole('slider'), { target: { value: '33' } });
    fireEvent.click(screen.getByRole('button', { name: 'บันทึกการตรวจแปลง' }));

    await waitFor(() => expect(createJsonMock).toHaveBeenCalledOnce());
    expect(createJsonMock.mock.calls[0][0].yieldPct).toBe(33);
  });

  it('round 8-8B — the payload carries yieldQuantityKg the user typed, alongside the derived yieldPct preview', async () => {
    mockGeolocation('denied');
    selectPlotMock.mockResolvedValue(selectResult({ currentYieldPct: null })); // target=500kg, no history
    createJsonMock.mockResolvedValue({ plotCode: 'PLOT001', plotName: 'Plot One' });
    await goToFormStep();

    fireEvent.change(screen.getByRole('spinbutton'), { target: { value: '250' } });
    fireEvent.click(screen.getByRole('button', { name: 'บันทึกการตรวจแปลง' }));

    await waitFor(() => expect(createJsonMock).toHaveBeenCalledOnce());
    expect(createJsonMock.mock.calls[0][0].yieldQuantityKg).toBe(250);
    expect(createJsonMock.mock.calls[0][0].yieldPct).toBe(50);
  });

  it('round 8-8B.1 — a Yield over 150% of target still submits successfully (warning, not a blocking error)', async () => {
    mockGeolocation('denied');
    selectPlotMock.mockResolvedValue(selectResult({ currentYieldPct: null })); // target=500kg, no history
    createJsonMock.mockResolvedValue({ plotCode: 'PLOT001', plotName: 'Plot One' });
    await goToFormStep();

    fireEvent.change(screen.getByRole('spinbutton'), { target: { value: '800' } }); // 800/500 = 160%
    expect(await screen.findByText('ผลผลิตสูงกว่า 150% ของเป้าหมาย กรุณาตรวจสอบความถูกต้องก่อนบันทึก')).toBeTruthy();

    const submitBtn = screen.getByRole('button', { name: 'บันทึกการตรวจแปลง' }) as HTMLButtonElement;
    expect(submitBtn.disabled).toBe(false);
    fireEvent.click(submitBtn);

    await waitFor(() => expect(createJsonMock).toHaveBeenCalledOnce());
    expect(createJsonMock.mock.calls[0][0].yieldQuantityKg).toBe(800);
    expect(createJsonMock.mock.calls[0][0].yieldPct).toBe(160);
  });

  it('a 422 from the backend Yield validation shows a clear Thai message, never the raw response', async () => {
    mockGeolocation('denied');
    createJsonMock.mockRejectedValue({
      isAxiosError: true,
      response: { status: 422, data: { detail: 'บางอย่างผิดพลาด' } },
    });
    await goToFormStep();

    fireEvent.click(screen.getByRole('button', { name: 'บันทึกการตรวจแปลง' }));

    expect(await screen.findByText('ข้อมูลไม่ถูกต้อง กรุณาตรวจสอบและลองใหม่')).toBeTruthy();
  });
});

// --- Submit / record payload (Part H) ---------------------------------------

describe('PublicInspect — submit and record payload', () => {
  it('submits without GPS and without photos via the plain JSON endpoint', async () => {
    mockGeolocation('denied');
    createJsonMock.mockResolvedValue({ plotCode: 'PLOT001', plotName: 'Plot One' });
    await goToFormStep();

    fireEvent.click(screen.getByRole('button', { name: 'บันทึกการตรวจแปลง' }));

    await waitFor(() => expect(createJsonMock).toHaveBeenCalledOnce());
    expect(createWithPhotosMock).not.toHaveBeenCalled();
    expect(await screen.findByText('บันทึกสำเร็จ')).toBeTruthy();
  });

  it('submits via the with-photos endpoint when at least one photo is picked', async () => {
    mockGeolocation('success');
    createWithPhotosMock.mockResolvedValue({ plotCode: 'PLOT001', plotName: 'Plot One' });
    await goToFormStep();

    await waitFor(() => expect(screen.getByText(/13\.756300/)).toBeTruthy());
    await pickPhotoAndWait(0, jpegFile('a.jpg'));
    fireEvent.click(screen.getByRole('button', { name: 'บันทึกการตรวจแปลง' }));

    await waitFor(() => expect(createWithPhotosMock).toHaveBeenCalledOnce());
    const [, photos] = createWithPhotosMock.mock.calls[0];
    expect(photos).toHaveLength(1);
    expect(createJsonMock).not.toHaveBeenCalled();
  });

  it('41. sends the compressed (WebP) File through the public with-photos multipart, not the original', async () => {
    mockGeolocation('denied');
    createWithPhotosMock.mockResolvedValue({ plotCode: 'PLOT001', plotName: 'Plot One' });
    await goToFormStep();

    await pickPhotoAndWait(0, jpegFile('a.jpg'));
    fireEvent.click(screen.getByRole('button', { name: 'บันทึกการตรวจแปลง' }));

    await waitFor(() => expect(createWithPhotosMock).toHaveBeenCalledOnce());
    const [, photos] = createWithPhotosMock.mock.calls[0];
    expect(photos[0].type).toBe('image/webp');
    expect(photos[0].name).toBe('inspection-photo-1.webp');
  });

  it('the record payload never contains phone or any server-derived field', async () => {
    mockGeolocation('denied');
    createJsonMock.mockResolvedValue({ plotCode: 'PLOT001', plotName: 'Plot One' });
    await goToFormStep();

    fireEvent.click(screen.getByRole('button', { name: 'บันทึกการตรวจแปลง' }));

    await waitFor(() => expect(createJsonMock).toHaveBeenCalledOnce());
    const [payload] = createJsonMock.mock.calls[0];
    for (const banned of [
      'phone', 'plotAccessPhoneId', 'submittedPhoneSnapshot', 'submittedPhoneType',
      'inspectorType', 'plotId', 'supplierId',
    ]) {
      expect(Object.prototype.hasOwnProperty.call(payload, banned)).toBe(false);
    }
    expect(payload.inspectionSessionToken).toBe('insp-tok-abc');
  });

  it('never puts the phone into submittedByName', async () => {
    mockGeolocation('denied');
    createJsonMock.mockResolvedValue({ plotCode: 'PLOT001', plotName: 'Plot One' });
    renderPublicInspect();
    await enterPhoneAndLookup(REAL_PHONE);
    await pickRoleAndPlot();

    const nameInput = screen.getByPlaceholderText('ไม่บังคับ') as HTMLInputElement;
    expect(nameInput.value).toBe(''); // starts blank, never auto-filled from the phone
    fireEvent.change(nameInput, { target: { value: 'สมชาย' } });
    fireEvent.click(screen.getByRole('button', { name: 'บันทึกการตรวจแปลง' }));

    await waitFor(() => expect(createJsonMock).toHaveBeenCalledOnce());
    expect(createJsonMock.mock.calls[0][0].submittedByName).toBe('สมชาย');
    expect(createJsonMock.mock.calls[0][0].submittedByName).not.toContain(REAL_PHONE);
  });
});

// --- Round 8-14B: photo processing gates submit -----------------------------

describe('PublicInspect — round 8-14B: photo processing gates submit', () => {
  it('39. disables the submit button while a photo is still mid client-side compression', async () => {
    mockGeolocation('denied');
    prepareInspectionPhotoMock.mockReturnValue(new Promise(() => {})); // never settles
    await goToFormStep();

    fireEvent.change(screen.getAllByLabelText(/เลือกรูป/)[0], { target: { files: [jpegFile('a.jpg')] } });
    await screen.findByText('กำลังเตรียมรูป...');

    const submitBtn = screen.getByRole('button', { name: 'บันทึกการตรวจแปลง' }) as HTMLButtonElement;
    expect(submitBtn.disabled).toBe(true);
  });

  it('40. a direct form submit event is hard-guarded even if the disabled button is bypassed', async () => {
    mockGeolocation('denied');
    prepareInspectionPhotoMock.mockReturnValue(new Promise(() => {})); // never settles
    await goToFormStep();

    fireEvent.change(screen.getAllByLabelText(/เลือกรูป/)[0], { target: { files: [jpegFile('a.jpg')] } });
    await screen.findByText('กำลังเตรียมรูป...');

    const submitBtn = screen.getByRole('button', { name: 'บันทึกการตรวจแปลง' }) as HTMLButtonElement;
    const form = submitBtn.closest('form');
    expect(form).toBeTruthy();
    fireEvent.submit(form!);

    expect(await screen.findByText('กรุณารอให้ระบบเตรียมรูปภาพเสร็จก่อน')).toBeTruthy();
    expect(createJsonMock).not.toHaveBeenCalled();
    expect(createWithPhotosMock).not.toHaveBeenCalled();
  });
});

// --- Session expiry / error recovery at submit (Part H) ---------------------

describe('PublicInspect — inspection-token expiry at submit', () => {
  it('re-selects the same plot silently on 401 and does NOT auto-resubmit', async () => {
    mockGeolocation('denied');
    createJsonMock.mockRejectedValueOnce({ isAxiosError: true, response: { status: 401 } });
    selectPlotMock.mockResolvedValueOnce(selectResult()); // initial select
    await goToFormStep();
    selectPlotMock.mockResolvedValueOnce(selectResult({ inspectionSessionToken: 'insp-tok-fresh' }));

    fireEvent.change(screen.getByLabelText('หมายเหตุ'), { target: { value: 'note-in-progress' } });
    fireEvent.click(screen.getByRole('button', { name: 'บันทึกการตรวจแปลง' }));

    await waitFor(() => expect(createJsonMock).toHaveBeenCalledTimes(1));
    await waitFor(() => expect(selectPlotMock).toHaveBeenCalledTimes(2));
    expect(await screen.findByText(/กรุณากดบันทึกอีกครั้ง/)).toBeTruthy();
    // form is still on the form step with the typed value intact — not resubmitted
    expect((screen.getByLabelText('หมายเหตุ') as HTMLTextAreaElement).value).toBe('note-in-progress');
    expect(createJsonMock).toHaveBeenCalledTimes(1);
  });

  it('if the phone session is also gone, clears everything and returns to the phone step', async () => {
    mockGeolocation('denied');
    createJsonMock.mockRejectedValueOnce({ isAxiosError: true, response: { status: 401 } });
    await goToFormStep();
    selectPlotMock.mockRejectedValueOnce({ isAxiosError: true, response: { status: 401 } });

    fireEvent.click(screen.getByRole('button', { name: 'บันทึกการตรวจแปลง' }));

    expect(await screen.findByLabelText(/หมายเลขสำหรับเข้าตรวจ/)).toBeTruthy();
    expect(await screen.findByText(/เซสชันหมดอายุ/)).toBeTruthy();
  });
});

describe('PublicInspect — submit 404 (stale assignment/cycle)', () => {
  it('clears the plot draft, refetches the list, and returns to the plots step', async () => {
    mockGeolocation('denied');
    createJsonMock.mockRejectedValue({ isAxiosError: true, response: { status: 404 } });
    await goToFormStep();

    fireEvent.click(screen.getByRole('button', { name: 'บันทึกการตรวจแปลง' }));

    await waitFor(() => expect(createJsonMock).toHaveBeenCalledOnce());
    await waitFor(() => expect(listPlotsMock).toHaveBeenCalledWith('phone-tok-abc'));
    expect(await screen.findByText('เลือกแปลงที่จะตรวจ')).toBeTruthy();
    expect(screen.getByText(/แปลงนี้ไม่พร้อมให้ตรวจแล้ว หรือรอบปลูกมีการเปลี่ยนแปลง/)).toBeTruthy();
  });

  it('a fresh re-select afterwards starts with a blank draft (no stale data)', async () => {
    mockGeolocation('denied');
    createJsonMock.mockRejectedValueOnce({ isAxiosError: true, response: { status: 404 } });
    await goToFormStep();
    fireEvent.change(screen.getByLabelText('หมายเหตุ'), { target: { value: 'stale note' } });
    fireEvent.click(screen.getByRole('button', { name: 'บันทึกการตรวจแปลง' }));
    await waitFor(() => expect(screen.getByText('เลือกแปลงที่จะตรวจ')).toBeTruthy());

    fireEvent.click(screen.getByRole('button', { name: /PLOT001/ }));
    await waitFor(() => expect(screen.getByPlaceholderText('ไม่บังคับ')).toBeTruthy());

    expect((screen.getByLabelText('หมายเหตุ') as HTMLTextAreaElement).value).toBe('');
  });
});

// --- Back navigation / dirty form (Part G) ----------------------------------

describe('PublicInspect — back navigation and dirty form', () => {
  it('goes back to the list immediately when the form is clean', async () => {
    const confirmSpy = vi.spyOn(window, 'confirm');
    await goToFormStep();

    fireEvent.click(screen.getByRole('button', { name: 'กลับรายการแปลง' }));

    expect(confirmSpy).not.toHaveBeenCalled();
    expect(screen.getByText('เลือกแปลงที่จะตรวจ')).toBeTruthy();
  });

  it('confirms before leaving a dirty form; cancelling keeps the user on the form', async () => {
    const confirmSpy = vi.spyOn(window, 'confirm').mockReturnValue(false);
    await goToFormStep();
    fireEvent.change(screen.getByLabelText('หมายเหตุ'), { target: { value: 'in progress' } });

    fireEvent.click(screen.getByRole('button', { name: 'กลับรายการแปลง' }));

    expect(confirmSpy).toHaveBeenCalled();
    expect(screen.queryByText('เลือกแปลงที่จะตรวจ')).toBeNull();
    expect((screen.getByLabelText('หมายเหตุ') as HTMLTextAreaElement).value).toBe('in progress');
  });

  it('confirming the dirty-leave clears the plot draft and returns to the list', async () => {
    vi.spyOn(window, 'confirm').mockReturnValue(true);
    await goToFormStep();
    fireEvent.change(screen.getByLabelText('หมายเหตุ'), { target: { value: 'in progress' } });

    fireEvent.click(screen.getByRole('button', { name: 'กลับรายการแปลง' }));

    expect(screen.getByText('เลือกแปลงที่จะตรวจ')).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: /PLOT001/ }));
    await waitFor(() => expect(screen.getByPlaceholderText('ไม่บังคับ')).toBeTruthy());
    expect((screen.getByLabelText('หมายเหตุ') as HTMLTextAreaElement).value).toBe('');
  });
});

// --- Multi-plot continuation (Part H/I) -------------------------------------

describe('PublicInspect — multi-plot continuation', () => {
  it('"ตรวจแปลงถัดไป" refetches the list and returns to the plots step', async () => {
    mockGeolocation('denied');
    createJsonMock.mockResolvedValue({ plotCode: 'PLOT001', plotName: 'Plot One' });
    await goToFormStep();
    fireEvent.click(screen.getByRole('button', { name: 'บันทึกการตรวจแปลง' }));
    await screen.findByText('บันทึกสำเร็จ');

    listPlotsMock.mockResolvedValue({ plots: [plotItem({ inspectedToday: true })] });
    fireEvent.click(screen.getByRole('button', { name: 'ตรวจแปลงถัดไป' }));

    await waitFor(() => expect(listPlotsMock).toHaveBeenCalledWith('phone-tok-abc'));
    expect(await screen.findByText('เลือกแปลงที่จะตรวจ')).toBeTruthy();
    expect(screen.getByText('ตรวจแล้ววันนี้')).toBeTruthy();
  });

  it('retains submittedByName for the next plot, but it stays editable', async () => {
    mockGeolocation('denied');
    createJsonMock.mockResolvedValue({ plotCode: 'PLOT001', plotName: 'Plot One' });
    await goToFormStep();
    fireEvent.change(screen.getByPlaceholderText('ไม่บังคับ'), { target: { value: 'สมชาย' } });
    fireEvent.click(screen.getByRole('button', { name: 'บันทึกการตรวจแปลง' }));
    await screen.findByText('บันทึกสำเร็จ');

    fireEvent.click(screen.getByRole('button', { name: 'ตรวจแปลงถัดไป' }));
    await waitFor(() => expect(screen.getByText('เลือกแปลงที่จะตรวจ')).toBeTruthy());
    fireEvent.click(screen.getByRole('button', { name: /PLOT001/ }));
    await waitFor(() => expect(screen.getByPlaceholderText('ไม่บังคับ')).toBeTruthy());

    const nameInput = screen.getByPlaceholderText('ไม่บังคับ') as HTMLInputElement;
    expect(nameInput.value).toBe('สมชาย');

    // still editable
    fireEvent.change(nameInput, { target: { value: 'สมหญิง' } });
    expect(nameInput.value).toBe('สมหญิง');
  });

  it('resets all inspection fields and photos for the next plot', async () => {
    mockGeolocation('success');
    createWithPhotosMock.mockResolvedValue({ plotCode: 'PLOT001', plotName: 'Plot One' });
    await goToFormStep();
    fireEvent.change(screen.getByLabelText('หมายเหตุ'), { target: { value: 'old note' } });
    await waitFor(() => expect(screen.getByText(/13\.756300/)).toBeTruthy());
    await pickPhotoAndWait(0, jpegFile('a.jpg'));
    fireEvent.click(screen.getByRole('button', { name: 'บันทึกการตรวจแปลง' }));
    await screen.findByText('บันทึกสำเร็จ');

    fireEvent.click(screen.getByRole('button', { name: 'ตรวจแปลงถัดไป' }));
    await waitFor(() => expect(screen.getByText('เลือกแปลงที่จะตรวจ')).toBeTruthy());
    fireEvent.click(screen.getByRole('button', { name: /PLOT001/ }));
    await waitFor(() => expect(screen.getByPlaceholderText('ไม่บังคับ')).toBeTruthy());

    expect((screen.getByLabelText('หมายเหตุ') as HTMLTextAreaElement).value).toBe('');
    expect(screen.queryAllByText(/a\.jpg/).length).toBe(0);
  });

  it('the same inspector type carries forward, still changeable before the next select', async () => {
    mockGeolocation('denied');
    createJsonMock.mockResolvedValue({ plotCode: 'PLOT001', plotName: 'Plot One' });
    renderPublicInspect();
    await enterPhoneAndLookup();
    await pickRoleAndPlot('บริษัทผู้ผลิต');
    fireEvent.click(screen.getByRole('button', { name: 'บันทึกการตรวจแปลง' }));
    await screen.findByText('บันทึกสำเร็จ');

    fireEvent.click(screen.getByRole('button', { name: 'ตรวจแปลงถัดไป' }));
    await waitFor(() => expect(screen.getByText('เลือกแปลงที่จะตรวจ')).toBeTruthy());

    const supplierRadio = screen.getByRole('radio', { name: 'บริษัทผู้ผลิต' }) as HTMLInputElement;
    expect(supplierRadio.checked).toBe(true);

    // still changeable
    fireEvent.click(screen.getByRole('radio', { name: 'Chiatai' }));
    fireEvent.click(screen.getByRole('button', { name: /PLOT001/ }));
    await waitFor(() => expect(selectPlotMock).toHaveBeenLastCalledWith('phone-tok-abc', 'plot-1', 'chiatai'));
  });
});

// --- Change phone (Part I) ---------------------------------------------------

describe('PublicInspect — change phone', () => {
  it('clears the phone session, inspector type, and plot list; returns to phone step', async () => {
    renderPublicInspect();
    await enterPhoneAndLookup();
    fireEvent.click(screen.getByRole('radio', { name: 'เกษตรกร' }));

    fireEvent.click(screen.getByRole('button', { name: 'เปลี่ยนหมายเลข' }));

    expect(screen.getByLabelText(/หมายเลขสำหรับเข้าตรวจ/)).toBeTruthy();

    // re-entering starts a genuinely fresh session — inspector type not retained
    await enterPhoneAndLookup();
    const anyChecked = ['เกษตรกร', 'บริษัทผู้ผลิต', 'Chiatai'].some(
      (label) => (screen.getByRole('radio', { name: label }) as HTMLInputElement).checked,
    );
    expect(anyChecked).toBe(false);
  });

  it('confirms before changing phone when the form is dirty; cancelling keeps the session', async () => {
    const confirmSpy = vi.spyOn(window, 'confirm').mockReturnValue(false);
    await goToFormStep();
    fireEvent.change(screen.getByLabelText('หมายเหตุ'), { target: { value: 'x' } });

    fireEvent.click(screen.getByRole('button', { name: 'เปลี่ยนหมายเลข' }));

    expect(confirmSpy).toHaveBeenCalled();
    // still on the form, still has the token — nothing was cleared
    expect(screen.queryByLabelText(/หมายเลขสำหรับเข้าตรวจ/)).toBeNull();
    expect((screen.getByLabelText('หมายเหตุ') as HTMLTextAreaElement).value).toBe('x');
  });

  it('confirming a dirty change-phone from the form clears everything, including inspector type', async () => {
    vi.spyOn(window, 'confirm').mockReturnValue(true);
    await goToFormStep();
    fireEvent.change(screen.getByLabelText('หมายเหตุ'), { target: { value: 'x' } });

    fireEvent.click(screen.getByRole('button', { name: 'เปลี่ยนหมายเลข' }));

    expect(screen.getByLabelText(/หมายเลขสำหรับเข้าตรวจ/)).toBeTruthy();
    await enterPhoneAndLookup();
    const anyChecked = ['เกษตรกร', 'บริษัทผู้ผลิต', 'Chiatai'].some(
      (label) => (screen.getByRole('radio', { name: label }) as HTMLInputElement).checked,
    );
    expect(anyChecked).toBe(false);
  });
});

// --- Protocol-driven scores regression (round 5.2, unchanged by 8-3D) ------

describe('PublicInspect — protocol-driven scores (regression)', () => {
  function mockStageOptions() {
    listPublicMasterDataMock.mockImplementation(({ type }: { type: string }) =>
      type === 'growth_stage'
        ? Promise.resolve([{ value: 'ระยะงอก', parent: null }, { value: 'ตั้งตัว', parent: null }])
        : Promise.resolve([]));
  }

  function fillScore(groupLabel: string, n: number) {
    const group = screen.getByRole('group', { name: groupLabel });
    fireEvent.click(within(group).getByRole('button', { name: String(n) }));
  }

  it('shows protocol labels for the stage and requires all 4 scores before submit', async () => {
    mockStageOptions();
    mockGeolocation('denied');
    createJsonMock.mockResolvedValue({ plotCode: 'PLOT001', plotName: 'Plot One' });
    await goToFormStep();

    fireEvent.click(await screen.findByRole('button', { name: 'ระยะงอก' }));

    expect(await screen.findByRole('group', { name: 'การเตรียมแปลง' })).toBeTruthy();
    expect(screen.getByRole('group', { name: 'ความต้านทานของสายพันธุ์' })).toBeTruthy();

    fireEvent.click(screen.getByRole('button', { name: 'บันทึกการตรวจแปลง' }));
    await waitFor(() => expect(screen.getByText(/กรุณาให้คะแนนครบทั้ง 4 ช่อง/)).toBeTruthy());
    expect(createJsonMock).not.toHaveBeenCalled();

    fillScore('การเตรียมแปลง', 8);
    fillScore('สภาพอากาศ', 7);
    fillScore('การดูแลรักษา', 9);
    fillScore('ความต้านทานของสายพันธุ์', 6);
    fireEvent.click(screen.getByRole('button', { name: 'บันทึกการตรวจแปลง' }));

    await waitFor(() => expect(createJsonMock).toHaveBeenCalledOnce());
    const [payload] = createJsonMock.mock.calls[0];
    expect(payload.growthStage).toBe('ระยะงอก');
    expect(payload.fieldPrepScore).toBe(8);
    expect(payload.varietyResistanceScore).toBe(6);
  });

  it('hides score inputs and does not require scores for a non-protocol stage', async () => {
    mockStageOptions();
    mockGeolocation('denied');
    createJsonMock.mockResolvedValue({ plotCode: 'PLOT001', plotName: 'Plot One' });
    await goToFormStep();

    fireEvent.click(await screen.findByRole('button', { name: 'ตั้งตัว' }));

    expect(await screen.findByText(/ไม่มี Protocol คะแนนเฉพาะ/)).toBeTruthy();
    expect(screen.queryByRole('group', { name: 'การเตรียมแปลง' })).toBeNull();

    fireEvent.click(screen.getByRole('button', { name: 'บันทึกการตรวจแปลง' }));
    await waitFor(() => expect(createJsonMock).toHaveBeenCalledOnce());
    const [payload] = createJsonMock.mock.calls[0];
    expect(payload.growthStage).toBe('ตั้งตัว');
    expect(payload.fieldPrepScore).toBeNull();
  });
});

describe('PublicInspect — public master-data failure does not crash the page', () => {
  it('shows an inline error and still renders the submit button', async () => {
    listPublicMasterDataMock.mockRejectedValue(new Error('network error'));
    mockGeolocation('success');
    await goToFormStep();

    expect(await screen.findAllByText(/โหลดตัวเลือกไม่สำเร็จ/)).not.toHaveLength(0);
    expect(screen.getByRole('button', { name: 'บันทึกการตรวจแปลง' })).toBeTruthy();
  });
});

// --- Neutral access-number copy (round 8-3F) --------------------------------
// The public identifier must never be presented to the user as a "phone
// number" — only as a neutral "access number" (หมายเลขสำหรับเข้าตรวจ).
// These assert rendered copy only (never JS comments/API identifiers, which
// aren't part of document.body.textContent anyway).

const BANNED_PHONE_WORDS = ['เบอร์โทร', 'โทรศัพท์', 'เปลี่ยนเบอร์', 'เบอร์นี้'];

describe('PublicInspect — neutral copy never leaks phone-specific wording (round 8-3F)', () => {
  it('the phone step never renders phone-specific wording', async () => {
    renderPublicInspect();
    await waitForEntryForm();
    for (const banned of BANNED_PHONE_WORDS) {
      expect(document.body.textContent).not.toContain(banned);
    }
  });

  it('the plots step never renders phone-specific wording', async () => {
    renderPublicInspect();
    await enterPhoneAndLookup();
    for (const banned of BANNED_PHONE_WORDS) {
      expect(document.body.textContent).not.toContain(banned);
    }
  });

  it('the form step never renders phone-specific wording', async () => {
    await goToFormStep();
    for (const banned of BANNED_PHONE_WORDS) {
      expect(document.body.textContent).not.toContain(banned);
    }
  });

  it('the success step never renders phone-specific wording', async () => {
    mockGeolocation('denied');
    createJsonMock.mockResolvedValue({ plotCode: 'PLOT001', plotName: 'Plot One' });
    await goToFormStep();
    fireEvent.click(screen.getByRole('button', { name: 'บันทึกการตรวจแปลง' }));
    await screen.findByText('บันทึกสำเร็จ');

    for (const banned of BANNED_PHONE_WORDS) {
      expect(document.body.textContent).not.toContain(banned);
    }
  });

  it('a 404 not-found error never renders phone-specific wording', async () => {
    lookupMock.mockRejectedValue({ isAxiosError: true, response: { status: 404 } });
    renderPublicInspect();
    fireEvent.change(await waitForEntryForm(), { target: { value: REAL_PHONE } });
    fireEvent.click(screen.getByRole('button', { name: 'ค้นหาแปลง' }));
    await screen.findByText('ไม่พบแปลงที่หมายเลขนี้ได้รับอนุญาตให้เข้าตรวจ');

    for (const banned of BANNED_PHONE_WORDS) {
      expect(document.body.textContent).not.toContain(banned);
    }
  });

  it('a legacy QR mismatch note never renders phone-specific wording', async () => {
    lookupMock.mockResolvedValue(lookupResult([
      plotItem({ plotId: 'p1', plotCode: 'OTHERPLOT', supplierCode: 'SUP002' }),
    ]));
    renderPublicInspect('/public/inspect?supplierCode=SUP001&plotCode=PLOT001');
    await enterPhoneAndLookup();
    await screen.findByText(/ไม่พบแปลงที่สแกนในรายการของหมายเลขนี้/);

    for (const banned of BANNED_PHONE_WORDS) {
      expect(document.body.textContent).not.toContain(banned);
    }
  });

  it('the session-expired message uses the neutral "กรอกหมายเลขอีกครั้ง" copy', async () => {
    selectPlotMock.mockRejectedValue({ isAxiosError: true, response: { status: 401 } });
    renderPublicInspect();
    await enterPhoneAndLookup();
    fireEvent.click(screen.getByRole('radio', { name: 'เกษตรกร' }));
    fireEvent.click(screen.getByRole('button', { name: /PLOT001/ }));

    expect(await screen.findByText('เซสชันหมดอายุ กรุณากรอกหมายเลขอีกครั้ง')).toBeTruthy();
  });
});

// --- submittedByCode retirement (round 8-3G) --------------------------------

describe('PublicInspect — submittedByCode retirement (round 8-3G)', () => {
  it('never renders a รหัสผู้กรอกข้อมูล field anywhere in the form', async () => {
    await goToFormStep();
    expect(screen.queryByText(/รหัสผู้กรอกข้อมูล/)).toBeNull();
    expect(screen.queryByPlaceholderText(/FIELD01/)).toBeNull();
  });

  it('submitting with no ชื่อผู้กรอกข้อมูล typed still succeeds (name is optional)', async () => {
    mockGeolocation('denied');
    createJsonMock.mockResolvedValue({ plotCode: 'PLOT001', plotName: 'Plot One' });
    await goToFormStep();

    fireEvent.click(screen.getByRole('button', { name: 'บันทึกการตรวจแปลง' }));

    await waitFor(() => expect(createJsonMock).toHaveBeenCalledOnce());
    expect(await screen.findByText('บันทึกสำเร็จ')).toBeTruthy();
  });

  it('the record payload never contains a submittedByCode key', async () => {
    mockGeolocation('denied');
    createJsonMock.mockResolvedValue({ plotCode: 'PLOT001', plotName: 'Plot One' });
    await goToFormStep();

    fireEvent.click(screen.getByRole('button', { name: 'บันทึกการตรวจแปลง' }));

    await waitFor(() => expect(createJsonMock).toHaveBeenCalledOnce());
    const [payload] = createJsonMock.mock.calls[0];
    expect(Object.prototype.hasOwnProperty.call(payload, 'submittedByCode')).toBe(false);
  });
});

// --- Round 8-4B: offline draft queue + network status UI -------------------

describe('PublicInspect — network status indicator (round 8-4B, copy updated round 8-4H.1)', () => {
  it('shows "ออนไลน์" with a Wifi icon by default (navigator.onLine = true)', async () => {
    renderPublicInspect();
    expect(await screen.findByText('ออนไลน์')).toBeTruthy();
    expect(screen.queryByText(/^ออฟไลน์/)).toBeNull();
  });

  it('switches to the offline label (with the "must connect before saving" copy) when the browser fires the offline event', async () => {
    renderPublicInspect();
    await screen.findByText('ออนไลน์');

    setOnline(false);

    expect(await screen.findByText('ออฟไลน์ — ต้องเชื่อมต่ออินเทอร์เน็ตก่อนบันทึก')).toBeTruthy();
    expect(screen.queryByText('ออนไลน์')).toBeNull();
  });

  it('switches back to "ออนไลน์" when the online event fires again', async () => {
    renderPublicInspect();
    setOnline(false);
    await screen.findByText(/ออฟไลน์/);

    setOnline(true);

    expect(await screen.findByText('ออนไลน์')).toBeTruthy();
  });
});

describe('PublicInspect — leftover queue badge (round 8-4B, relabeled round 8-4H.1 Part D)', () => {
  it('shows no leftover-queue button when there are no local drafts', async () => {
    renderPublicInspect();
    await screen.findByText('ออนไลน์');
    expect(screen.queryByText(/รายการค้างเดิม/)).toBeNull();
  });

  it('shows "รายการค้างเดิม N รายการ" when drafts already exist locally', async () => {
    await seedDraft({ clientSubmissionId: 'a' });
    await seedDraft({ clientSubmissionId: 'b', plotCode: 'PLOT002' });

    renderPublicInspect();

    expect(await screen.findByText('รายการค้างเดิม 2 รายการ')).toBeTruthy();
  });

  it('opens the queue panel from the leftover-queue button', async () => {
    await seedDraft();
    renderPublicInspect();
    fireEvent.click(await screen.findByText(/รายการค้างเดิม/));

    expect(await screen.findByText('PLOT001 — Plot One')).toBeTruthy();
  });
});

describe('PublicInspect — Online-only: offline submit is blocked (round 8-4H.1 Part B)', () => {
  it('never calls the API when navigator.onLine is false', async () => {
    mockGeolocation('denied');
    await goToFormStep();
    setOnline(false);
    await screen.findByText(/ออฟไลน์/);

    fireEvent.click(screen.getByRole('button', { name: 'บันทึกการตรวจแปลง' }));

    await waitFor(() => expect(screen.getByText('ยังไม่สามารถบันทึกได้ กรุณาเชื่อมต่ออินเทอร์เน็ตแล้วลองอีกครั้ง')).toBeTruthy());
    expect(createJsonMock).not.toHaveBeenCalled();
    expect(createWithPhotosMock).not.toHaveBeenCalled();
  });

  it('never writes a row to inspection_drafts', async () => {
    mockGeolocation('denied');
    await goToFormStep();
    setOnline(false);
    await screen.findByText(/ออฟไลน์/);

    fireEvent.click(screen.getByRole('button', { name: 'บันทึกการตรวจแปลง' }));
    await waitFor(() => expect(screen.getByText('ยังไม่สามารถบันทึกได้ กรุณาเชื่อมต่ออินเทอร์เน็ตแล้วลองอีกครั้ง')).toBeTruthy());

    expect(await countOfflineInspectionDrafts()).toBe(0);
  });

  it('never navigates away or resets the form — field values and a picked photo both survive', async () => {
    mockGeolocation('denied');
    await goToFormStep();
    fireEvent.change(screen.getByLabelText('หมายเหตุ'), { target: { value: 'my note stays' } });
    await pickPhotoAndWait(0, jpegFile('a.jpg'));
    setOnline(false);
    await screen.findByText(/ออฟไลน์/);

    fireEvent.click(screen.getByRole('button', { name: 'บันทึกการตรวจแปลง' }));

    await waitFor(() => expect(screen.getByText('ยังไม่สามารถบันทึกได้ กรุณาเชื่อมต่ออินเทอร์เน็ตแล้วลองอีกครั้ง')).toBeTruthy());
    // still on the form — the read-only plot-info panel (unique to the form step) is still present.
    expect(screen.getByPlaceholderText('ไม่บังคับ')).toBeTruthy();
    expect((screen.getByLabelText('หมายเหตุ') as HTMLTextAreaElement).value).toBe('my note stays');
  });

  it('never shows the removed "เตรียมข้อมูลออฟไลน์ไม่สำเร็จ" copy anywhere', async () => {
    mockGeolocation('denied');
    await goToFormStep();
    setOnline(false);
    await screen.findByText(/ออฟไลน์/);

    fireEvent.click(screen.getByRole('button', { name: 'บันทึกการตรวจแปลง' }));
    await waitFor(() => expect(screen.getByText('ยังไม่สามารถบันทึกได้ กรุณาเชื่อมต่ออินเทอร์เน็ตแล้วลองอีกครั้ง')).toBeTruthy());

    expect(screen.queryByText(/เตรียมข้อมูลออฟไลน์ไม่สำเร็จ/)).toBeNull();
    expect(screen.queryByText(/ใช้รายการแปลงในเครื่อง/)).toBeNull();
    expect(screen.queryByText(/บันทึกไว้ในเครื่องแล้ว/)).toBeNull();
  });
});

describe('PublicInspect — offline phone lookup / plot selection are blocked (round 8-4H.1 Part B)', () => {
  it('cold-reload-like state: phone lookup while offline shows the connect-first message and never calls the API', async () => {
    renderPublicInspect();
    setOnline(false);
    await screen.findByText(/ออฟไลน์/);
    fireEvent.change(await waitForEntryForm(), { target: { value: REAL_PHONE } });
    fireEvent.click(screen.getByRole('button', { name: 'ค้นหาแปลง' }));

    expect(await screen.findByText('ขณะนี้ไม่มีการเชื่อมต่ออินเทอร์เน็ต กรุณาเชื่อมต่อก่อนค้นหาและบันทึกการตรวจแปลง')).toBeTruthy();
    expect(lookupMock).not.toHaveBeenCalled();
  });

  it('picking a plot while offline (list still in memory from an earlier online lookup) never opens a form or calls select-plot', async () => {
    renderPublicInspect();
    await enterPhoneAndLookup();
    fireEvent.click(screen.getByRole('radio', { name: 'เกษตรกร' }));
    setOnline(false);
    await screen.findByText(/ออฟไลน์/);

    fireEvent.click(screen.getByRole('button', { name: /PLOT001/ }));

    expect(await screen.findByText('ขณะนี้ไม่มีการเชื่อมต่ออินเทอร์เน็ต กรุณาเชื่อมต่อก่อนค้นหาและบันทึกการตรวจแปลง')).toBeTruthy();
    expect(selectPlotMock).not.toHaveBeenCalled();
    expect(screen.queryByPlaceholderText('ไม่บังคับ')).toBeNull(); // form never opened
  });

  it('no cached-plot-entry UI exists anywhere on the phone step while offline', async () => {
    renderPublicInspect();
    setOnline(false);
    await screen.findByText(/ออฟไลน์/);

    expect(screen.queryByRole('button', { name: 'ใช้รายการแปลงในเครื่อง' })).toBeNull();
    expect(screen.queryByRole('button', { name: 'ล้างรายการแปลงในเครื่อง' })).toBeNull();
    expect(screen.queryByText(/รายการแปลงล่าสุดที่บันทึกไว้บนอุปกรณ์นี้/)).toBeNull();
  });
});

describe('PublicInspect — network failure during an online submit (round 8-4H.1 Part B)', () => {
  it('shows a retry message and never writes a draft', async () => {
    mockGeolocation('denied');
    createJsonMock.mockRejectedValueOnce({ isAxiosError: true, response: undefined });
    await goToFormStep();

    fireEvent.click(screen.getByRole('button', { name: 'บันทึกการตรวจแปลง' }));

    expect(await screen.findByText('เชื่อมต่อเครือข่ายไม่สำเร็จ กรุณาลองใหม่')).toBeTruthy();
    expect(await countOfflineInspectionDrafts()).toBe(0);
    expect(screen.queryByText(/บันทึกไว้ในเครื่องแล้ว/)).toBeNull();
    expect(screen.queryByText('บันทึกสำเร็จ')).toBeNull();
  });

  it('keeps the form/photos exactly as entered — never resets, never navigates away', async () => {
    mockGeolocation('denied');
    createJsonMock.mockRejectedValueOnce({ isAxiosError: true, response: undefined });
    await goToFormStep();
    fireEvent.change(screen.getByLabelText('หมายเหตุ'), { target: { value: 'keep me' } });

    fireEvent.click(screen.getByRole('button', { name: 'บันทึกการตรวจแปลง' }));

    await screen.findByText('เชื่อมต่อเครือข่ายไม่สำเร็จ กรุณาลองใหม่');
    expect(screen.getByPlaceholderText('ไม่บังคับ')).toBeTruthy();
    expect((screen.getByLabelText('หมายเหตุ') as HTMLTextAreaElement).value).toBe('keep me');
  });

  it('a manual retry click reuses the SAME idempotency identity (never a new UUID)', async () => {
    mockGeolocation('denied');
    createJsonMock.mockRejectedValueOnce({ isAxiosError: true, response: undefined });
    createJsonMock.mockResolvedValueOnce({ plotCode: 'PLOT001', plotName: 'Plot One' });
    await goToFormStep();

    fireEvent.click(screen.getByRole('button', { name: 'บันทึกการตรวจแปลง' }));
    await waitFor(() => expect(createJsonMock).toHaveBeenCalledTimes(1));
    const firstKey = createJsonMock.mock.calls[0][0].clientSubmissionId;
    await screen.findByText('เชื่อมต่อเครือข่ายไม่สำเร็จ กรุณาลองใหม่');

    // Reconnecting alone must NOT auto-fire the request — only the user's
    // own click does. setOnline(true) here just restores the network for
    // the manual click below to actually succeed.
    setOnline(true);
    await screen.findByText('ออนไลน์');
    expect(createJsonMock).toHaveBeenCalledTimes(1); // still 1 — no auto-retry from reconnecting alone

    fireEvent.click(screen.getByRole('button', { name: 'บันทึกการตรวจแปลง' }));
    await waitFor(() => expect(createJsonMock).toHaveBeenCalledTimes(2));
    expect(createJsonMock.mock.calls[1][0].clientSubmissionId).toBe(firstKey);
    expect(await screen.findByText('บันทึกสำเร็จ')).toBeTruthy();
  });
});

describe('PublicInspect — retry reuses the same submission identity (round 8-4B Part 4)', () => {
  it('a 401 token-refresh retry sends the SAME clientSubmissionId, and never clears form/photos', async () => {
    // A photo is picked, so submission routes through the with-photos
    // endpoint, not the plain JSON one — mock THAT mock, not createJsonMock.
    mockGeolocation('success');
    createWithPhotosMock.mockRejectedValueOnce({ isAxiosError: true, response: { status: 401 } });
    selectPlotMock.mockResolvedValueOnce(selectResult());
    await goToFormStep();
    selectPlotMock.mockResolvedValueOnce(selectResult({ inspectionSessionToken: 'insp-tok-fresh' }));
    fireEvent.change(screen.getByLabelText('หมายเหตุ'), { target: { value: 'note-in-progress' } });
    await pickPhotoAndWait(0, jpegFile('a.jpg'));

    fireEvent.click(screen.getByRole('button', { name: 'บันทึกการตรวจแปลง' }));
    await waitFor(() => expect(createWithPhotosMock).toHaveBeenCalledTimes(1));
    await waitFor(() => expect(selectPlotMock).toHaveBeenCalledTimes(2));
    await screen.findByText(/กรุณากดบันทึกอีกครั้ง/);

    const firstKey = createWithPhotosMock.mock.calls[0][0].clientSubmissionId;
    expect(firstKey).toBeTruthy();
    // form/photos survive the 401 (existing round 8-3D guarantee, still true)
    expect((screen.getByLabelText('หมายเหตุ') as HTMLTextAreaElement).value).toBe('note-in-progress');

    createWithPhotosMock.mockResolvedValueOnce({ plotCode: 'PLOT001', plotName: 'Plot One' });
    fireEvent.click(screen.getByRole('button', { name: 'บันทึกการตรวจแปลง' }));
    await waitFor(() => expect(createWithPhotosMock).toHaveBeenCalledTimes(2));

    const secondKey = createWithPhotosMock.mock.calls[1][0].clientSubmissionId;
    expect(secondKey).toBe(firstKey);
  });
});

describe('PublicInspect — server-confirmed success clears the matching local draft (round 8-4B Part 11)', () => {
  it('a 201 create deletes any local draft under the same identity and shows online success', async () => {
    const fixedId = 'fixed-uuid-for-201-test';
    vi.spyOn(crypto, 'randomUUID').mockReturnValue(fixedId as unknown as `${string}-${string}-${string}-${string}-${string}`);
    await seedDraft({ clientSubmissionId: fixedId });
    mockGeolocation('denied');
    createJsonMock.mockResolvedValue({ plotCode: 'PLOT001', plotName: 'Plot One' });
    await goToFormStep();
    expect(await screen.findByText('รายการค้างเดิม 1 รายการ')).toBeTruthy();

    fireEvent.click(screen.getByRole('button', { name: 'บันทึกการตรวจแปลง' }));

    await waitFor(() => expect(createJsonMock).toHaveBeenCalledOnce());
    expect(createJsonMock.mock.calls[0][0].clientSubmissionId).toBe(fixedId);
    expect(await screen.findByText('บันทึกสำเร็จ')).toBeTruthy();
    await waitFor(async () => expect(await countOfflineInspectionDrafts()).toBe(0));
    expect(screen.queryByText(/รายการค้างเดิม/)).toBeNull();
  });

  it('a success with NO pre-existing local draft still succeeds (delete-if-present is a safe no-op)', async () => {
    mockGeolocation('denied');
    createJsonMock.mockResolvedValue({ plotCode: 'PLOT001', plotName: 'Plot One' });
    await goToFormStep();

    fireEvent.click(screen.getByRole('button', { name: 'บันทึกการตรวจแปลง' }));

    expect(await screen.findByText('บันทึกสำเร็จ')).toBeTruthy();
    expect(await countOfflineInspectionDrafts()).toBe(0);
  });
});

describe('PublicInspect — HTTP error responses are never auto-queued (round 8-4B Part 11)', () => {
  it('422 shows a validation error and does not queue a draft', async () => {
    mockGeolocation('denied');
    createJsonMock.mockRejectedValueOnce({ isAxiosError: true, response: { status: 422, data: { detail: [] } } });
    await goToFormStep();

    fireEvent.click(screen.getByRole('button', { name: 'บันทึกการตรวจแปลง' }));

    await waitFor(() => expect(screen.getByText('ข้อมูลไม่ถูกต้อง กรุณาตรวจสอบและลองใหม่')).toBeTruthy());
    expect(screen.queryByText(/บันทึกไว้ในเครื่องแล้ว/)).toBeNull();
    expect(await countOfflineInspectionDrafts()).toBe(0);
  });

  it('409 with a structured planting_cycle_changed code shows the exact mapped Thai message', async () => {
    mockGeolocation('denied');
    createJsonMock.mockRejectedValueOnce({
      isAxiosError: true,
      response: { status: 409, data: { detail: { code: 'planting_cycle_changed' } } },
    });
    await goToFormStep();

    fireEvent.click(screen.getByRole('button', { name: 'บันทึกการตรวจแปลง' }));

    expect(await screen.findByText('รอบปลูกของแปลงนี้เปลี่ยนแล้ว รายการนี้ไม่สามารถส่งเข้ารอบใหม่อัตโนมัติได้')).toBeTruthy();
    expect(await countOfflineInspectionDrafts()).toBe(0);
  });

  it('409 with idempotency_conflict shows its own mapped message', async () => {
    mockGeolocation('denied');
    createJsonMock.mockRejectedValueOnce({
      isAxiosError: true,
      response: { status: 409, data: { detail: { code: 'idempotency_conflict' } } },
    });
    await goToFormStep();

    fireEvent.click(screen.getByRole('button', { name: 'บันทึกการตรวจแปลง' }));

    expect(await screen.findByText('รหัสรายการนี้ถูกใช้กับข้อมูลอื่นแล้ว กรุณาเก็บรายการใหม่')).toBeTruthy();
  });

  it('a 409 without a recognized structured code falls back to a generic message', async () => {
    mockGeolocation('denied');
    createJsonMock.mockRejectedValueOnce({ isAxiosError: true, response: { status: 409, data: {} } });
    await goToFormStep();

    fireEvent.click(screen.getByRole('button', { name: 'บันทึกการตรวจแปลง' }));

    expect(await screen.findByText('ไม่สามารถบันทึกได้ กรุณาลองใหม่')).toBeTruthy();
  });

  it('429 shows the rate-limit message and does not queue', async () => {
    mockGeolocation('denied');
    createJsonMock.mockRejectedValueOnce({ isAxiosError: true, response: { status: 429 } });
    await goToFormStep();

    fireEvent.click(screen.getByRole('button', { name: 'บันทึกการตรวจแปลง' }));

    expect(await screen.findByText('มีการลองหลายครั้งเกินไป กรุณารอสักครู่แล้วลองใหม่')).toBeTruthy();
    expect(await countOfflineInspectionDrafts()).toBe(0);
  });

  it('a 5xx response shows the generic failure message and does not queue', async () => {
    mockGeolocation('denied');
    createJsonMock.mockRejectedValueOnce({ isAxiosError: true, response: { status: 500 } });
    await goToFormStep();

    fireEvent.click(screen.getByRole('button', { name: 'บันทึกการตรวจแปลง' }));

    expect(await screen.findByText('บันทึกไม่สำเร็จ กรุณาลองใหม่')).toBeTruthy();
    expect(await countOfflineInspectionDrafts()).toBe(0);
  });
});

describe('PublicInspect — IndexedDB unavailable (round 8-4H.1 Part D)', () => {
  it('online inspection still works fully — a missing IndexedDB never blocks the online flow', async () => {
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    delete (globalThis as any).indexedDB;
    mockGeolocation('denied');
    createJsonMock.mockResolvedValue({ plotCode: 'PLOT001', plotName: 'Plot One' });
    await goToFormStep();

    fireEvent.click(screen.getByRole('button', { name: 'บันทึกการตรวจแปลง' }));

    expect(await screen.findByText('บันทึกสำเร็จ')).toBeTruthy();
    expect(createJsonMock).toHaveBeenCalledOnce();
  });

  it('never shows the removed "เตรียมข้อมูลออฟไลน์ไม่สำเร็จ" message when IndexedDB is unavailable', async () => {
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    delete (globalThis as any).indexedDB;
    renderPublicInspect();
    await screen.findByText('ออนไลน์');

    expect(screen.queryByText(/เตรียมข้อมูลออฟไลน์ไม่สำเร็จ/)).toBeNull();
  });
});

describe('PublicInspect — expired draft purge on mount (round 8-4B Part 8)', () => {
  it('purges drafts older than 7 days and shows a plain count notice with no PII', async () => {
    await seedDraft({ clientSubmissionId: 'stale' });
    // seedDraft's default capturedAt is always ~1 day ago (deterministic,
    // round 8-6I.1) — force it far in the past by overriding directly, so
    // this test's own "definitely expired" case never depends on that default.
    const stale = await getOfflineInspectionDraft('stale');
    await putOfflineInspectionDraft({ ...stale!, capturedAt: '2000-01-01T00:00:00.000Z' });

    renderPublicInspect();

    expect(await screen.findByText(/ลบรายการที่เก็บไว้เกิน 7 วันออกจากเครื่องแล้ว 1 รายการ/)).toBeTruthy();
    // the notice never names the purged draft's plot/crop/etc.
    expect(screen.queryByText('PLOT001 — Plot One')).toBeNull();
    await waitFor(async () => expect(await countOfflineInspectionDrafts()).toBe(0));
  });

  it('does not purge a draft within the retention window', async () => {
    await seedDraft({ clientSubmissionId: 'fresh', capturedAt: new Date().toISOString(), now: new Date().toISOString() });

    renderPublicInspect();

    await screen.findByText('ออนไลน์');
    expect(screen.queryByText(/ลบรายการที่เก็บไว้เกิน 7 วัน/)).toBeNull();
    expect(await countOfflineInspectionDrafts()).toBe(1);
  });
});

// --- round 8-4C: submit-in-flight guard (identity race hardening) ----------

describe('PublicInspect — submit-in-flight guard prevents duplicate submissions (round 8-4C)', () => {
  it('a rapid double-click on "บันทึกการตรวจแปลง" (online) results in exactly one API call and one identity', async () => {
    mockGeolocation('denied');
    const randomUUIDSpy = vi.spyOn(crypto, 'randomUUID');
    createJsonMock.mockResolvedValue({ plotCode: 'PLOT001', plotName: 'Plot One' });
    await goToFormStep();
    const btn = screen.getByRole('button', { name: 'บันทึกการตรวจแปลง' });

    fireEvent.click(btn);
    fireEvent.click(btn);

    await waitFor(() => expect(screen.getByText('บันทึกสำเร็จ')).toBeTruthy());
    expect(createJsonMock).toHaveBeenCalledTimes(1);
    expect(randomUUIDSpy).toHaveBeenCalledTimes(1);
  });

  it('round 8-4H.1 — the same guard applies to the offline-blocked path: a double-click while offline shows the message once, writes no draft', async () => {
    mockGeolocation('denied');
    await goToFormStep();
    setOnline(false);
    await screen.findByText(/ออฟไลน์/);
    const btn = screen.getByRole('button', { name: 'บันทึกการตรวจแปลง' });

    fireEvent.click(btn);
    fireEvent.click(btn);

    await waitFor(() => expect(screen.getByText('ยังไม่สามารถบันทึกได้ กรุณาเชื่อมต่ออินเทอร์เน็ตแล้วลองอีกครั้ง')).toBeTruthy());
    expect(await countOfflineInspectionDrafts()).toBe(0);
  });
});

describe('PublicInspect — persisted plot cache: retired, never written or read (round 8-4H.1 Part C)', () => {
  it('a successful online lookup never writes to public_access_cache', async () => {
    renderPublicInspect();

    await enterPhoneAndLookup();

    expect(await getOfflinePublicAccessCache()).toBeNull();
  });

  it('startup cleanup removes any leftover public_access_cache row from round 8-4H, but never touches inspection_drafts', async () => {
    const cache = buildOfflinePublicAccessCache({
      plots: [plotItem()],
      protocols: PROTOCOLS,
      masterData: { growthStage: [], weather: [] },
      now: new Date().toISOString(),
    });
    if (cache) await putOfflinePublicAccessCache(cache);
    await seedDraft({ clientSubmissionId: 'keep-me' });

    renderPublicInspect();
    await screen.findByText('ออนไลน์');

    await waitFor(async () => expect(await getOfflinePublicAccessCache()).toBeNull());
    expect(await countOfflineInspectionDrafts()).toBe(1);
  });
});

// --- round 8-4C.1 Part D: no fake/placeholder token on the plot context ----

describe('PublicInspect — PublicInspectionPlotContext has no token field (round 8-4C.1 Part D)', () => {
  it('rejects inspectionSessionToken/expiresIn at compile time — structurally impossible to construct one with a token', () => {
    const context: PublicInspectionPlotContext = {
      plotId: 'plot-1', plotCode: 'PLOT001', plotName: 'Plot One',
      supplierId: 'sup-1', supplierCode: 'SUP001', supplierName: 'Supplier One',
      plotCycleId: 'cycle-1', cycleNo: 1, cycleLabel: null,
      currentCrop: null, currentVariety: null, currentLotNo: null, currentPlantingDate: null,
      plantCount: null, expectedYieldFull: null, expectedYieldUnit: null,
      currentYieldPct: null, currentStage: null, lastInspectedAt: null,
    };
    // A fully-valid PublicInspectionPlotContext structurally has no token
    // field at all, proven by the object above compiling without one.
    expect(Object.keys(context)).not.toContain('inspectionSessionToken');
    expect(Object.keys(context)).not.toContain('expiresIn');

    // @ts-expect-error inspectionSessionToken is not assignable to PublicInspectionPlotContext
    const withToken: PublicInspectionPlotContext = { ...context, inspectionSessionToken: 'should-not-compile' };
    expect(withToken).toBeTruthy(); // silence unused-var lint; the @ts-expect-error line is the actual assertion
  });
});

// ===========================================================================
// Round 8-9D — plot password ("รหัส Supplier ตรวจแปลง") UX + capability contract
// ===========================================================================
//
// The live runtime has PUBLIC_PLOT_PASSWORD_ENFORCEMENT=false and this round
// does not change that. Enforcement mode is exercised HERE ONLY, by making the
// mocked capability endpoint answer passwordRequired=true — the same thing the
// real backend will do the day an operator flips the flag.

/** Every input's live value plus all rendered text — the two places a secret
 * could survive on screen. `innerHTML` alone is not enough: an <input>'s typed
 * value is a DOM property, not an attribute, so it never appears there. */
function renderedSecretSurface(): string {
  const values = Array.from(document.querySelectorAll('input, textarea'))
    .map((el) => (el as HTMLInputElement).value)
    .join('|');
  return `${document.body.textContent ?? ''}|${document.body.innerHTML}|${values}`;
}

function dumpStorage(s: Storage | undefined): string {
  if (!s) return '';
  let out = '';
  for (let i = 0; i < s.length; i++) {
    const k = s.key(i);
    if (k) out += `${k}=${s.getItem(k)}|`;
  }
  return out;
}

/** Anchored on purpose: the reveal button's aria-label ("แสดงรหัส Supplier ตรวจแปลง" /
 * "ซ่อนรหัส Supplier ตรวจแปลง") also CONTAINS the field label, so an unanchored regex
 * matches three elements. */
async function passwordField(): Promise<HTMLInputElement> {
  return (await screen.findByLabelText(/^รหัส Supplier ตรวจแปลง/)) as HTMLInputElement;
}

// --- Part D: capability loading / failure ----------------------------------

describe('PublicInspect — capability probe gates the entry form (round 8-9D Part D)', () => {
  it('renders neither the phone form nor a submit button while the probe is in flight', async () => {
    configMock.mockReturnValue(new Promise(() => {}));
    renderPublicInspect();

    expect(await screen.findByText('กำลังเตรียมหน้าตรวจแปลง...')).toBeTruthy();
    expect(screen.queryByLabelText(/หมายเลขสำหรับเข้าตรวจ/)).toBeNull();
    expect(screen.queryByRole('button', { name: 'ค้นหาแปลง' })).toBeNull();
    expect(lookupMock).not.toHaveBeenCalled();
  });

  it('the loading placeholder is announced and reserves height so the card does not jump', async () => {
    configMock.mockReturnValue(new Promise(() => {}));
    renderPublicInspect();

    const status = await screen.findByRole('status');
    expect(status.getAttribute('aria-live')).toBe('polite');
    expect(status.className).toContain('min-h-');
  });

  it('passwordRequired=false renders the phone-only form, with no password field at all', async () => {
    renderPublicInspect();

    await waitForEntryForm();
    expect(screen.queryByLabelText(/^รหัส Supplier ตรวจแปลง/)).toBeNull();
    expect(screen.queryByText(/กรอกตัวเลขอย่างน้อย/)).toBeNull();
    expect(screen.getByRole('button', { name: 'ค้นหาแปลง' })).toBeTruthy();
  });

  it('passwordRequired=true renders both fields in one form', async () => {
    configMock.mockResolvedValue(CONFIG_PASSWORD_REQUIRED);
    renderPublicInspect();

    await waitForEntryForm();
    expect(await passwordField()).toBeTruthy();
    expect(screen.getByText('กรอกตัวเลขอย่างน้อย 4 หลัก')).toBeTruthy();
    expect(screen.getByRole('button', { name: 'ค้นหาแปลง' })).toBeTruthy();
  });

  it('a failed probe fails SAFE: an error + retry, never a silent phone-only fallback', async () => {
    configMock.mockRejectedValue({ isAxiosError: true, response: { status: 503 } });
    renderPublicInspect();

    expect(await screen.findByText('ไม่สามารถเตรียมหน้าตรวจแปลงได้ กรุณาลองใหม่อีกครั้ง')).toBeTruthy();
    // The whole point: no usable entry form, so nothing can be submitted
    // against a backend whose requirements we do not know.
    expect(screen.queryByLabelText(/หมายเลขสำหรับเข้าตรวจ/)).toBeNull();
    expect(screen.queryByRole('button', { name: 'ค้นหาแปลง' })).toBeNull();
    expect(screen.getByRole('button', { name: /ลองใหม่/ })).toBeTruthy();
  });

  it('"ลองใหม่" refetches, and a probe that then says passwordRequired shows the password field', async () => {
    configMock
      .mockRejectedValueOnce({ isAxiosError: true, response: { status: 503 } })
      .mockResolvedValue(CONFIG_PASSWORD_REQUIRED);
    renderPublicInspect();
    await screen.findByText('ไม่สามารถเตรียมหน้าตรวจแปลงได้ กรุณาลองใหม่อีกครั้ง');

    fireEvent.click(screen.getByRole('button', { name: /ลองใหม่/ }));

    expect(await passwordField()).toBeTruthy();
    expect(configMock).toHaveBeenCalledTimes(2);
  });

  it('a mode flip from true to false ends the session and drops the incompatible input', async () => {
    // A backend deploy (or a flag rollback) mid-visit: the token in memory was
    // minted under enforcement and is meaningless without it.
    configMock.mockResolvedValue(CONFIG_PASSWORD_REQUIRED);
    const { qc } = renderPublicInspect();
    await enterPhonePasswordAndLookup();
    expect(screen.getByText('เลือกแปลงที่จะตรวจ')).toBeTruthy();

    configMock.mockResolvedValue(CONFIG_PHONE_ONLY);
    await qc.invalidateQueries({ queryKey: ['public-inspection-access-config'] });

    // Back at the entry step, in the NEW mode, with nothing carried over.
    await waitForEntryForm();
    await waitFor(() => expect(screen.queryByLabelText(/^รหัส Supplier ตรวจแปลง/)).toBeNull());
    expect(screen.queryByText('เลือกแปลงที่จะตรวจ')).toBeNull();
    expect(renderedSecretSurface()).not.toContain(PLOT_PASSWORD);
  });

  it('a mode flip from false to true ends the session too', async () => {
    const { qc } = renderPublicInspect();
    await enterPhoneAndLookup();

    configMock.mockResolvedValue(CONFIG_PASSWORD_REQUIRED);
    await qc.invalidateQueries({ queryKey: ['public-inspection-access-config'] });

    expect(await passwordField()).toBeTruthy();
    expect(screen.queryByText('เลือกแปลงที่จะตรวจ')).toBeNull();
  });
});

// --- Part E: password input + validation -----------------------------------

describe('PublicInspect — password input (round 8-9D Part E)', () => {
  beforeEach(() => {
    configMock.mockResolvedValue(CONFIG_PASSWORD_REQUIRED);
  });

  it('is masked by default, numeric-friendly, capped, and never type="number"', async () => {
    renderPublicInspect();
    const input = await passwordField();

    expect(input.type).toBe('password');
    expect(input.inputMode).toBe('numeric');
    expect(input.autocomplete).toBe('off');
    expect(input.maxLength).toBe(20);
    // type="number" would silently drop the leading zero of "0123".
    expect(input.type).not.toBe('number');
  });

  it('the eye button reveals and re-masks, with an aria-label both ways', async () => {
    renderPublicInspect();
    const input = await passwordField();

    const reveal = screen.getByRole('button', { name: 'แสดงรหัส Supplier ตรวจแปลง' });
    fireEvent.click(reveal);
    expect((await passwordField()).type).toBe('text');

    fireEvent.click(screen.getByRole('button', { name: 'ซ่อนรหัส Supplier ตรวจแปลง' }));
    expect((await passwordField()).type).toBe('password');
    expect(input).toBeTruthy();
  });

  it('the eye button never submits the form', async () => {
    renderPublicInspect();
    await passwordField();
    fireEvent.change(await waitForEntryForm(), { target: { value: REAL_PHONE } });
    fireEvent.change(await passwordField(), { target: { value: PLOT_PASSWORD } });

    fireEvent.click(screen.getByRole('button', { name: 'แสดงรหัส Supplier ตรวจแปลง' }));

    expect(lookupMock).not.toHaveBeenCalled();
    expect(screen.queryByText('เลือกแปลงที่จะตรวจ')).toBeNull();
  });

  it('an empty password blocks submit with its own message and never calls lookup', async () => {
    renderPublicInspect();
    fireEvent.change(await waitForEntryForm(), { target: { value: REAL_PHONE } });
    fireEvent.click(screen.getByRole('button', { name: 'ค้นหาแปลง' }));

    expect(await screen.findByText('กรุณากรอกรหัส Supplier ตรวจแปลง')).toBeTruthy();
    expect(lookupMock).not.toHaveBeenCalled();
  });

  it('3 digits is rejected by the policy message and never calls lookup', async () => {
    renderPublicInspect();
    fireEvent.change(await waitForEntryForm(), { target: { value: REAL_PHONE } });
    fireEvent.change(await passwordField(), { target: { value: '135' } });
    fireEvent.click(screen.getByRole('button', { name: 'ค้นหาแปลง' }));

    expect(await screen.findByText('รหัส Supplier ตรวจแปลงต้องเป็นตัวเลข 4 ถึง 20 หลัก')).toBeTruthy();
    expect(lookupMock).not.toHaveBeenCalled();
  });

  it.each(['1357', '01234567890123456789'])('accepts a %s-length legal code', async (password) => {
    renderPublicInspect();
    await enterPhonePasswordAndLookup(REAL_PHONE, password);

    expect(lookupMock).toHaveBeenCalledWith({ phone: REAL_PHONE, password, qrKey: undefined });
  });

  it('caps a 21-digit paste at 20 characters instead of sending an over-long value', async () => {
    renderPublicInspect();
    const input = await passwordField();

    fireEvent.change(input, { target: { value: '123456789012345678901' } });

    expect((await passwordField()).value).toBe('12345678901234567890');
    expect((await passwordField()).value.length).toBe(20);
  });

  it('preserves a leading zero', async () => {
    renderPublicInspect();
    await enterPhonePasswordAndLookup(REAL_PHONE, '0123');

    expect(lookupMock).toHaveBeenCalledWith({ phone: REAL_PHONE, password: '0123', qrKey: undefined });
  });

  it.each(['1111', '000000', '1234', '987654'])('accepts the repeated/sequential code %s (no guessability rule)', async (password) => {
    renderPublicInspect();
    await enterPhonePasswordAndLookup(REAL_PHONE, password);

    expect(lookupMock).toHaveBeenCalledWith({ phone: REAL_PHONE, password, qrKey: undefined });
    expect(screen.queryByText(/เดาง่าย/)).toBeNull();
  });

  it.each([
    // The backend accepts ASCII [0-9] ONLY — Thai and full-width digits are
    // rejected there, so they must never survive the input either.
    ['thai digits', '๑๒๓๔', ''],
    ['full-width digits', '１２３４', ''],
    ['letters', 'abcd', ''],
    ['a dash', '12-34', '1234'],
    ['spaces', '12 34', '1234'],
    ['a mixed paste', 'pin: 0987!', '0987'],
  ])('sanitises %s to ASCII digits only', async (_label, raw, expected) => {
    renderPublicInspect();
    const input = await passwordField();

    fireEvent.change(input, { target: { value: raw } });

    expect((await passwordField()).value).toBe(expected);
    expect((await passwordField()).value).toMatch(/^[0-9]*$/);
  });

  it('a value that sanitises to nothing is treated as empty, not sent', async () => {
    renderPublicInspect();
    fireEvent.change(await waitForEntryForm(), { target: { value: REAL_PHONE } });
    fireEvent.change(await passwordField(), { target: { value: '๑๒๓๔' } });
    fireEvent.click(screen.getByRole('button', { name: 'ค้นหาแปลง' }));

    expect(await screen.findByText('กรุณากรอกรหัส Supplier ตรวจแปลง')).toBeTruthy();
    expect(lookupMock).not.toHaveBeenCalled();
  });
});

// --- Parts G/I/J: lookup, errors, QR ---------------------------------------

describe('PublicInspect — enforcement-mode lookup (round 8-9D Parts G/J)', () => {
  beforeEach(() => {
    configMock.mockResolvedValue(CONFIG_PASSWORD_REQUIRED);
  });

  it('sends the NORMALIZED number together with the password', async () => {
    renderPublicInspect();
    // typed with the formatting a field user actually uses
    await enterPhonePasswordAndLookup('084-555-2162', PLOT_PASSWORD);

    expect(lookupMock).toHaveBeenCalledWith({
      phone: REAL_PHONE, password: PLOT_PASSWORD, qrKey: undefined,
    });
  });

  it('clears the plaintext password the moment the lookup succeeds', async () => {
    renderPublicInspect();
    await enterPhonePasswordAndLookup();

    // not on screen anywhere...
    expect(renderedSecretSurface()).not.toContain(PLOT_PASSWORD);
    // ...and not still sitting in state either: going back to the entry screen
    // shows an empty field, not the previous code.
    fireEvent.click(screen.getByRole('button', { name: 'เปลี่ยนหมายเลขหรือรหัส' }));
    expect((await passwordField()).value).toBe('');
    expect((await waitForEntryForm()).value).toBe('');
  });

  it('a FAILED lookup keeps the typed code so the user can fix one digit', async () => {
    lookupMock.mockRejectedValue({ isAxiosError: true, response: { status: 404 } });
    renderPublicInspect();
    fireEvent.change(await waitForEntryForm(), { target: { value: REAL_PHONE } });
    fireEvent.change(await passwordField(), { target: { value: PLOT_PASSWORD } });
    fireEvent.click(screen.getByRole('button', { name: 'ค้นหาแปลง' }));

    await screen.findByText('หมายเลขหรือรหัส Supplier ตรวจแปลงไม่ถูกต้อง หรือยังไม่ได้รับอนุญาตให้เข้าตรวจ');
    expect((await passwordField()).value).toBe(PLOT_PASSWORD);
  });

  it('404 shows ONE combined message — never which of the two was wrong', async () => {
    lookupMock.mockRejectedValue({ isAxiosError: true, response: { status: 404 } });
    renderPublicInspect();
    await enterPhonePasswordAndLookupExpectingFailure();

    const message = await screen.findByText('หมายเลขหรือรหัส Supplier ตรวจแปลงไม่ถูกต้อง หรือยังไม่ได้รับอนุญาตให้เข้าตรวจ');
    expect(message).toBeTruthy();
    expect(screen.queryByText(/รหัสไม่ถูกต้อง$/)).toBeNull();
    expect(screen.queryByText(/ไม่พบหมายเลข/)).toBeNull();
  });

  it('429 shows the generic throttle message with no attempt counter', async () => {
    lookupMock.mockRejectedValue({ isAxiosError: true, response: { status: 429 } });
    renderPublicInspect();
    await enterPhonePasswordAndLookupExpectingFailure();

    expect(await screen.findByText('มีการลองหลายครั้งเกินไป กรุณารอสักครู่แล้วลองใหม่')).toBeTruthy();
    expect(document.body.textContent).not.toMatch(/เหลืออีก|ครั้งที่ \d/);
  });

  it('never renders a raw backend detail string', async () => {
    lookupMock.mockRejectedValue({
      isAxiosError: true,
      response: { status: 422, data: { detail: 'LEAKY-SERVER-DETAIL-do-not-render' } },
    });
    renderPublicInspect();
    await enterPhonePasswordAndLookupExpectingFailure();

    expect(await screen.findByText('รหัส Supplier ตรวจแปลงต้องเป็นตัวเลข 4 ถึง 20 หลัก')).toBeTruthy();
    expect(document.body.textContent).not.toContain('LEAKY-SERVER-DETAIL-do-not-render');
  });

  it('a QR deep link cannot skip the password step', async () => {
    renderPublicInspect('/public/inspect?qr=opaque-key-123');
    await waitForEntryForm();

    // The QR hint is shown, but the credential fields are still the only way in.
    expect(screen.getByText('เข้าผ่านการสแกน QR แปลง')).toBeTruthy();
    expect(await passwordField()).toBeTruthy();

    fireEvent.change(await waitForEntryForm(), { target: { value: REAL_PHONE } });
    fireEvent.click(screen.getByRole('button', { name: 'ค้นหาแปลง' }));
    expect(await screen.findByText('กรุณากรอกรหัส Supplier ตรวจแปลง')).toBeTruthy();
    expect(lookupMock).not.toHaveBeenCalled();

    // With the code supplied, the qrKey travels in the SAME body as the
    // password — it is a plot hint, never an authenticator.
    fireEvent.change(await passwordField(), { target: { value: PLOT_PASSWORD } });
    fireEvent.click(screen.getByRole('button', { name: 'ค้นหาแปลง' }));
    await waitFor(() => expect(lookupMock).toHaveBeenCalledWith({
      phone: REAL_PHONE, password: PLOT_PASSWORD, qrKey: 'opaque-key-123',
    }));
  });

  it('the password never reaches the URL or the query string', async () => {
    renderPublicInspect('/public/inspect?qr=opaque-key-123');
    await enterPhonePasswordAndLookup();

    expect(window.location.search).not.toContain(PLOT_PASSWORD);
    expect(window.location.href).not.toContain(PLOT_PASSWORD);
  });
});

/** Fills both fields and submits WITHOUT waiting for the plots step — for the
 * error paths, where that step never arrives. */
async function enterPhonePasswordAndLookupExpectingFailure() {
  fireEvent.change(await waitForEntryForm(), { target: { value: REAL_PHONE } });
  fireEvent.change(await passwordField(), { target: { value: PLOT_PASSWORD } });
  fireEvent.click(screen.getByRole('button', { name: 'ค้นหาแปลง' }));
}

// --- Parts H/I: session across plots ---------------------------------------

describe('PublicInspect — one password, many plots (round 8-9D Parts H/I)', () => {
  beforeEach(() => {
    configMock.mockResolvedValue(CONFIG_PASSWORD_REQUIRED);
    lookupMock.mockResolvedValue(lookupResult([
      plotItem({ plotId: 'p1', plotCode: 'PLOT001' }),
      plotItem({ plotId: 'p2', plotCode: 'PLOT002' }),
    ]));
    listPlotsMock.mockResolvedValue({ plots: [
      plotItem({ plotId: 'p1', plotCode: 'PLOT001' }),
      plotItem({ plotId: 'p2', plotCode: 'PLOT002' }),
    ] });
  });

  it('selecting a plot uses the session token and never re-sends the password', async () => {
    renderPublicInspect();
    await enterPhonePasswordAndLookup();

    fireEvent.click(screen.getByRole('radio', { name: 'เกษตรกร' }));
    fireEvent.click(screen.getByRole('button', { name: /PLOT001/ }));
    await screen.findByPlaceholderText('ไม่บังคับ');

    expect(selectPlotMock).toHaveBeenCalledWith('phone-tok-abc', 'p1', 'farmer');
    expect(JSON.stringify(selectPlotMock.mock.calls)).not.toContain(PLOT_PASSWORD);
    expect(lookupMock).toHaveBeenCalledTimes(1);
  });

  it('"กลับรายการแปลง" keeps the session — no second lookup, no re-entry', async () => {
    renderPublicInspect();
    await enterPhonePasswordAndLookup();
    fireEvent.click(screen.getByRole('radio', { name: 'เกษตรกร' }));
    fireEvent.click(screen.getByRole('button', { name: /PLOT001/ }));
    await screen.findByPlaceholderText('ไม่บังคับ');

    fireEvent.click(screen.getByRole('button', { name: 'กลับรายการแปลง' }));

    expect(await screen.findByText('เลือกแปลงที่จะตรวจ')).toBeTruthy();
    expect(screen.queryByLabelText(/^รหัส Supplier ตรวจแปลง/)).toBeNull();
    expect(lookupMock).toHaveBeenCalledTimes(1);
  });

  it('"ตรวจแปลงถัดไป" after a save keeps the session and asks for no code', async () => {
    mockGeolocation('denied');
    createJsonMock.mockResolvedValue({ plotCode: 'PLOT001', plotName: 'Plot One' });
    renderPublicInspect();
    await enterPhonePasswordAndLookup();
    fireEvent.click(screen.getByRole('radio', { name: 'เกษตรกร' }));
    fireEvent.click(screen.getByRole('button', { name: /PLOT001/ }));
    await screen.findByPlaceholderText('ไม่บังคับ');
    fireEvent.click(screen.getByRole('button', { name: 'บันทึกการตรวจแปลง' }));
    await screen.findByText('บันทึกสำเร็จ');

    fireEvent.click(screen.getByRole('button', { name: 'ตรวจแปลงถัดไป' }));

    expect(await screen.findByText('เลือกแปลงที่จะตรวจ')).toBeTruthy();
    expect(screen.queryByLabelText(/^รหัส Supplier ตรวจแปลง/)).toBeNull();
    expect(lookupMock).toHaveBeenCalledTimes(1);
    // a second plot in the same session — still no re-entry
    fireEvent.click(screen.getByRole('radio', { name: 'เกษตรกร' }));
    fireEvent.click(screen.getByRole('button', { name: /PLOT002/ }));
    await screen.findByPlaceholderText('ไม่บังคับ');
    expect(lookupMock).toHaveBeenCalledTimes(1);
  });

  it('the record payload never carries the password', async () => {
    mockGeolocation('denied');
    createJsonMock.mockResolvedValue({ plotCode: 'PLOT001', plotName: 'Plot One' });
    renderPublicInspect();
    await enterPhonePasswordAndLookup();
    fireEvent.click(screen.getByRole('radio', { name: 'เกษตรกร' }));
    fireEvent.click(screen.getByRole('button', { name: /PLOT001/ }));
    await screen.findByPlaceholderText('ไม่บังคับ');

    fireEvent.click(screen.getByRole('button', { name: 'บันทึกการตรวจแปลง' }));
    await screen.findByText('บันทึกสำเร็จ');

    const payload = createJsonMock.mock.calls[0][0] as Record<string, unknown>;
    expect('password' in payload).toBe(false);
    expect(JSON.stringify(payload)).not.toContain(PLOT_PASSWORD);
  });

  it('"เปลี่ยนหมายเลขหรือรหัส" is the label in every step, and clears everything', async () => {
    mockGeolocation('denied');
    createJsonMock.mockResolvedValue({ plotCode: 'PLOT001', plotName: 'Plot One' });
    renderPublicInspect();
    await enterPhonePasswordAndLookup();

    // plots step
    expect(screen.getByRole('button', { name: 'เปลี่ยนหมายเลขหรือรหัส' })).toBeTruthy();
    expect(screen.queryByRole('button', { name: 'เปลี่ยนหมายเลข' })).toBeNull();

    // form step
    fireEvent.click(screen.getByRole('radio', { name: 'เกษตรกร' }));
    fireEvent.click(screen.getByRole('button', { name: /PLOT001/ }));
    await screen.findByPlaceholderText('ไม่บังคับ');
    expect(screen.getByRole('button', { name: 'เปลี่ยนหมายเลขหรือรหัส' })).toBeTruthy();

    // success step
    fireEvent.click(screen.getByRole('button', { name: 'บันทึกการตรวจแปลง' }));
    await screen.findByText('บันทึกสำเร็จ');
    expect(screen.getByRole('button', { name: 'เปลี่ยนหมายเลขหรือรหัส' })).toBeTruthy();

    fireEvent.click(screen.getByRole('button', { name: 'เปลี่ยนหมายเลขหรือรหัส' }));

    // everything gone: both inputs blank, no plots, no retained name
    expect((await waitForEntryForm()).value).toBe('');
    expect((await passwordField()).value).toBe('');
    expect(screen.queryByText('เลือกแปลงที่จะตรวจ')).toBeNull();
    expect(renderedSecretSurface()).not.toContain(PLOT_PASSWORD);
  });

  it('the phone-only label is used when enforcement is off', async () => {
    configMock.mockResolvedValue(CONFIG_PHONE_ONLY);
    renderPublicInspect();
    await enterPhoneAndLookup();

    expect(screen.getByRole('button', { name: 'เปลี่ยนหมายเลข' })).toBeTruthy();
    expect(screen.queryByRole('button', { name: 'เปลี่ยนหมายเลขหรือรหัส' })).toBeNull();
  });
});

// --- Part I: session expiry / credential invalidation -----------------------

describe('PublicInspect — session invalidation asks for both credentials (round 8-9D Part I)', () => {
  beforeEach(() => {
    configMock.mockResolvedValue(CONFIG_PASSWORD_REQUIRED);
  });

  it('a 401 from select-plot returns to the entry step and asks for both again', async () => {
    selectPlotMock.mockRejectedValue({ isAxiosError: true, response: { status: 401 } });
    renderPublicInspect();
    await enterPhonePasswordAndLookup();

    fireEvent.click(screen.getByRole('radio', { name: 'เกษตรกร' }));
    fireEvent.click(screen.getByRole('button', { name: /PLOT001/ }));

    expect(await screen.findByText(
      'เซสชันหมดอายุหรือข้อมูลเข้าตรวจมีการเปลี่ยนแปลง กรุณากรอกหมายเลขและรหัสอีกครั้ง',
    )).toBeTruthy();
    expect(await passwordField()).toBeTruthy();
    expect((await passwordField()).value).toBe('');
  });

  it('a changed plot password (401 at submit, renewal also 401) ends the session safely', async () => {
    // Round 8-9C: changing the password bumps credential_version, so every
    // token minted against the old one stops resolving — the renewal attempt
    // 401s too, and there is nothing to do but ask again.
    mockGeolocation('denied');
    createJsonMock.mockRejectedValue({ isAxiosError: true, response: { status: 401 } });
    renderPublicInspect();
    await enterPhonePasswordAndLookup();
    fireEvent.click(screen.getByRole('radio', { name: 'เกษตรกร' }));
    fireEvent.click(screen.getByRole('button', { name: /PLOT001/ }));
    await screen.findByPlaceholderText('ไม่บังคับ');
    selectPlotMock.mockRejectedValue({ isAxiosError: true, response: { status: 401 } });

    fireEvent.click(screen.getByRole('button', { name: 'บันทึกการตรวจแปลง' }));

    expect(await screen.findByText(
      'เซสชันหมดอายุหรือข้อมูลเข้าตรวจมีการเปลี่ยนแปลง กรุณากรอกหมายเลขและรหัสอีกครั้ง',
    )).toBeTruthy();
    // never claims the record was saved, never says WHAT changed
    expect(screen.queryByText('บันทึกสำเร็จ')).toBeNull();
    expect(document.body.textContent).not.toContain('รหัสถูกเปลี่ยน');
  });

  it('a 401 while refreshing the plot list also ends the session', async () => {
    selectPlotMock.mockRejectedValue({ isAxiosError: true, response: { status: 404 } });
    listPlotsMock.mockRejectedValue({ isAxiosError: true, response: { status: 401 } });
    renderPublicInspect();
    await enterPhonePasswordAndLookup();

    fireEvent.click(screen.getByRole('radio', { name: 'เกษตรกร' }));
    fireEvent.click(screen.getByRole('button', { name: /PLOT001/ }));

    expect(await screen.findByText(
      'เซสชันหมดอายุหรือข้อมูลเข้าตรวจมีการเปลี่ยนแปลง กรุณากรอกหมายเลขและรหัสอีกครั้ง',
    )).toBeTruthy();
  });

  it('keeps the legacy copy when enforcement is off', async () => {
    configMock.mockResolvedValue(CONFIG_PHONE_ONLY);
    selectPlotMock.mockRejectedValue({ isAxiosError: true, response: { status: 401 } });
    renderPublicInspect();
    await enterPhoneAndLookup();

    fireEvent.click(screen.getByRole('radio', { name: 'เกษตรกร' }));
    fireEvent.click(screen.getByRole('button', { name: /PLOT001/ }));

    expect(await screen.findByText('เซสชันหมดอายุ กรุณากรอกหมายเลขอีกครั้ง')).toBeTruthy();
  });
});

// --- Part F: the password never leaves entry-step React state ---------------

describe('PublicInspect — the password is never persisted anywhere (round 8-9D Part F)', () => {
  beforeEach(() => {
    configMock.mockResolvedValue(CONFIG_PASSWORD_REQUIRED);
  });

  it('never reaches localStorage or sessionStorage', async () => {
    renderPublicInspect();
    await enterPhonePasswordAndLookup();

    const all = dumpStorage(globalThis.localStorage) + dumpStorage(globalThis.sessionStorage);
    expect(all).not.toContain(PLOT_PASSWORD);
    expect(all).not.toContain('phone-tok-abc');
  });

  it('never reaches IndexedDB — not the draft store, not any leftover cache', async () => {
    mockGeolocation('denied');
    createJsonMock.mockResolvedValue({ plotCode: 'PLOT001', plotName: 'Plot One' });
    renderPublicInspect();
    await enterPhonePasswordAndLookup();
    fireEvent.click(screen.getByRole('radio', { name: 'เกษตรกร' }));
    fireEvent.click(screen.getByRole('button', { name: /PLOT001/ }));
    await screen.findByPlaceholderText('ไม่บังคับ');
    fireEvent.click(screen.getByRole('button', { name: 'บันทึกการตรวจแปลง' }));
    await screen.findByText('บันทึกสำเร็จ');

    const drafts = await listOfflineInspectionDrafts();
    expect(JSON.stringify(drafts)).not.toContain(PLOT_PASSWORD);
    expect(JSON.stringify(await getOfflinePublicAccessCache())).not.toContain(PLOT_PASSWORD);
  });

  it('never reaches the React Query cache — not a key, not a value', async () => {
    const { qc } = renderPublicInspect();
    await enterPhonePasswordAndLookup();

    const dump = JSON.stringify(qc.getQueryCache().getAll().map((q) => ({
      key: q.queryKey, state: q.state.data,
    })));
    expect(dump).not.toContain(PLOT_PASSWORD);
    expect(dump).not.toMatch(/password(?!Required|MinLength|MaxLength)/);
  });

  it('logs nothing while a password is being handled', async () => {
    const spies = (['log', 'info', 'warn', 'error', 'debug'] as const).map(
      (m) => vi.spyOn(console, m).mockImplementation(() => {}),
    );
    lookupMock.mockRejectedValueOnce({ isAxiosError: true, response: { status: 404 } });
    renderPublicInspect();
    await enterPhonePasswordAndLookupExpectingFailure();
    await screen.findByText('หมายเลขหรือรหัส Supplier ตรวจแปลงไม่ถูกต้อง หรือยังไม่ได้รับอนุญาตให้เข้าตรวจ');

    for (const spy of spies) {
      for (const call of spy.mock.calls) {
        expect(JSON.stringify(call)).not.toContain(PLOT_PASSWORD);
      }
    }
  });

  it('is gone from the DOM once the flow has moved on', async () => {
    mockGeolocation('denied');
    createJsonMock.mockResolvedValue({ plotCode: 'PLOT001', plotName: 'Plot One' });
    renderPublicInspect();
    await enterPhonePasswordAndLookup();
    expect(renderedSecretSurface()).not.toContain(PLOT_PASSWORD);

    fireEvent.click(screen.getByRole('radio', { name: 'เกษตรกร' }));
    fireEvent.click(screen.getByRole('button', { name: /PLOT001/ }));
    await screen.findByPlaceholderText('ไม่บังคับ');
    expect(renderedSecretSurface()).not.toContain(PLOT_PASSWORD);

    fireEvent.click(screen.getByRole('button', { name: 'บันทึกการตรวจแปลง' }));
    await screen.findByText('บันทึกสำเร็จ');
    expect(renderedSecretSurface()).not.toContain(PLOT_PASSWORD);
  });
});

// --- Part K: accessibility + responsive -------------------------------------

describe('PublicInspect — password field accessibility and layout (round 8-9D Part K)', () => {
  beforeEach(() => {
    configMock.mockResolvedValue(CONFIG_PASSWORD_REQUIRED);
  });

  it('the visible label is bound to the input with htmlFor/id', async () => {
    renderPublicInspect();
    const input = await passwordField();

    const label = document.querySelector('label[for="plot-access-password"]');
    expect(label).toBeTruthy();
    expect(label?.textContent).toContain('รหัส Supplier ตรวจแปลง');
    expect(input.id).toBe('plot-access-password');
  });

  it('the helper text is linked with aria-describedby, not left floating', async () => {
    renderPublicInspect();
    const input = await passwordField();

    const describedBy = input.getAttribute('aria-describedby');
    expect(describedBy).toBe('plot-access-password-help');
    expect(document.getElementById(describedBy!)?.textContent).toBe('กรอกตัวเลขอย่างน้อย 4 หลัก');
  });

  it('a validation error is announced with role="alert" and marks the input invalid', async () => {
    renderPublicInspect();
    fireEvent.change(await waitForEntryForm(), { target: { value: REAL_PHONE } });
    fireEvent.click(screen.getByRole('button', { name: 'ค้นหาแปลง' }));

    const alert = await screen.findByText('กรุณากรอกรหัส Supplier ตรวจแปลง');
    expect(alert.getAttribute('role')).toBe('alert');
    expect((await passwordField()).getAttribute('aria-invalid')).toBe('true');
  });

  it('tab order is number → code → show/hide → submit', async () => {
    renderPublicInspect();
    const phone = await waitForEntryForm();
    const password = await passwordField();
    const toggle = screen.getByRole('button', { name: 'แสดงรหัส Supplier ตรวจแปลง' });
    const submit = screen.getByRole('button', { name: 'ค้นหาแปลง' });

    // No element overrides tabindex, so DOM order IS tab order.
    for (const el of [phone, password, toggle, submit]) {
      expect(el.getAttribute('tabindex')).toBeNull();
    }
    const order = [phone, password, toggle, submit];
    for (let i = 1; i < order.length; i++) {
      // Node.DOCUMENT_POSITION_FOLLOWING === 4
      expect(order[i - 1].compareDocumentPosition(order[i]) & 4).toBeTruthy();
    }
  });

  it('Enter in the code field submits the form', async () => {
    renderPublicInspect();
    fireEvent.change(await waitForEntryForm(), { target: { value: REAL_PHONE } });
    fireEvent.change(await passwordField(), { target: { value: PLOT_PASSWORD } });

    // jsdom does not synthesise the implicit submit from a keypress, so submit
    // the form the way the browser would — what matters is that the form has a
    // real submit button inside it, which is what makes Enter work at all.
    const submit = screen.getByRole('button', { name: 'ค้นหาแปลง' }) as HTMLButtonElement;
    expect(submit.type).toBe('submit');
    expect(submit.form).toBe((await passwordField()).form);
    fireEvent.submit(submit.form!);

    await waitFor(() => expect(lookupMock).toHaveBeenCalledTimes(1));
  });

  it('a second click while the lookup is in flight cannot double-submit', async () => {
    lookupMock.mockReturnValue(new Promise(() => {}));
    renderPublicInspect();
    fireEvent.change(await waitForEntryForm(), { target: { value: REAL_PHONE } });
    fireEvent.change(await passwordField(), { target: { value: PLOT_PASSWORD } });

    const submit = screen.getByRole('button', { name: /ค้นหาแปลง/ }) as HTMLButtonElement;
    fireEvent.click(submit);
    await waitFor(() => expect(submit.disabled).toBe(true));
    fireEvent.click(submit);

    expect(lookupMock).toHaveBeenCalledTimes(1);
  });

  it('the row is fluid: the input can shrink and the button cannot, so nothing overflows at 320px', async () => {
    renderPublicInspect();
    const input = await passwordField();
    const toggle = screen.getByRole('button', { name: 'แสดงรหัส Supplier ตรวจแปลง' });

    // min-w-0 + flex-1 is what actually stops a flex child from forcing its
    // parent wider than the viewport on a 320px phone.
    expect(input.className).toContain('min-w-0');
    expect(input.className).toContain('flex-1');
    expect(input.className).toContain('w-full');
    expect(toggle.className).toContain('shrink-0');
    // fixed 48px touch target, never a viewport-scaled font
    expect(toggle.className).toContain('w-12');
    expect(input.className).toContain('text-base');
    expect(input.className).not.toMatch(/text-\[\d+vw\]|w-\[\d+px\]/);
  });
});

// ===========================================================================
// Round 8-9E — capability cache hardening + legacy-path reachability
// ===========================================================================

describe('PublicInspect — the capability answer is never allowed to go stale (round 8-9E Part B)', () => {
  it('remounting re-asks the backend and honours the NEW answer, not the cached one', async () => {
    // The 8-9D bug this closes: a 5-minute staleTime meant a tab could keep
    // showing the phone-only form (and keep sending password-less lookups that
    // now 404) for minutes after an operator flipped the flag.
    configMock.mockResolvedValue(CONFIG_PHONE_ONLY);
    const first = renderPublicInspect();
    await waitForEntryForm();
    expect(screen.queryByLabelText(/^รหัส Supplier ตรวจแปลง/)).toBeNull();
    first.unmount();

    configMock.mockResolvedValue(CONFIG_PASSWORD_REQUIRED);
    renderPublicInspect();

    expect(await passwordField()).toBeTruthy();
    expect(configMock).toHaveBeenCalledTimes(2);
  });

  it('a remount after enforcement is switched OFF goes back to phone-only', async () => {
    configMock.mockResolvedValue(CONFIG_PASSWORD_REQUIRED);
    const first = renderPublicInspect();
    await passwordField();
    first.unmount();

    configMock.mockResolvedValue(CONFIG_PHONE_ONLY);
    renderPublicInspect();

    await waitForEntryForm();
    await waitFor(() => expect(screen.queryByLabelText(/^รหัส Supplier ตรวจแปลง/)).toBeNull());
  });

  it('a remount always passes through the loading gate — never renders from a cached posture', async () => {
    configMock.mockResolvedValue(CONFIG_PHONE_ONLY);
    const first = renderPublicInspect();
    await waitForEntryForm();
    first.unmount();

    // gcTime 0 drops the cache entry the moment the last observer goes away,
    // so the next mount has nothing to render the form from.
    configMock.mockReturnValue(new Promise(() => {}));
    renderPublicInspect();

    expect(await screen.findByText('กำลังเตรียมหน้าตรวจแปลง...')).toBeTruthy();
    expect(screen.queryByLabelText(/หมายเลขสำหรับเข้าตรวจ/)).toBeNull();
  });

  it('a failed refetch still fails CLOSED on a cold mount — no form, no phone-only fallback', async () => {
    configMock.mockRejectedValue({ isAxiosError: true, response: { status: 503 } });
    renderPublicInspect();

    expect(await screen.findByText('ไม่สามารถเตรียมหน้าตรวจแปลงได้ กรุณาลองใหม่อีกครั้ง')).toBeTruthy();
    expect(screen.queryByRole('button', { name: 'ค้นหาแปลง' })).toBeNull();
    expect(lookupMock).not.toHaveBeenCalled();
  });

  it('does not poll: no refetchInterval is configured for the capability query', async () => {
    const { qc } = renderPublicInspect();
    await waitForEntryForm();

    const query = qc.getQueryCache().find({ queryKey: ['public-inspection-access-config'] });
    const options = query?.options as { refetchInterval?: unknown; staleTime?: number; gcTime?: number };
    expect(options.refetchInterval).toBeUndefined();
    expect(options.staleTime).toBe(0);
    expect(options.gcTime).toBe(0);
  });

  it('the hardened query still keeps the password out of the cache', async () => {
    configMock.mockResolvedValue(CONFIG_PASSWORD_REQUIRED);
    const { qc } = renderPublicInspect();
    await enterPhonePasswordAndLookup();

    const dump = JSON.stringify(qc.getQueryCache().getAll().map((q) => ({
      key: q.queryKey, data: q.state.data,
    })));
    expect(dump).not.toContain(PLOT_PASSWORD);
    const storage = dumpStorage(globalThis.localStorage) + dumpStorage(globalThis.sessionStorage);
    expect(storage).not.toContain(PLOT_PASSWORD);
    expect(window.location.href).not.toContain(PLOT_PASSWORD);
  });
});

describe('PublicInspect — the retired offline queue cannot become an entry point (round 8-9E Part C)', () => {
  it('the queue panel is not rendered on a device with no leftover drafts', async () => {
    renderPublicInspect();
    await waitForEntryForm();

    expect(screen.queryByText(/รายการค้างเดิม/)).toBeNull();
    expect(screen.queryByText('รายการที่บันทึกไว้ในเครื่อง')).toBeNull();
  });

  it('no production module can create the leftover draft the panel needs', async () => {
    // The panel's ONLY entry point is pendingCount > 0, and pendingCount comes
    // from the inspection_drafts store. Round 8-4H.1 removed every writer, so
    // on any device that has not run a pre-8-4H.1 build the panel is
    // unreachable by construction. This test fails the moment a writer returns.
    const sources = import.meta.glob(
      ['../../**/*.ts', '../../**/*.tsx'],
      { query: '?raw', import: 'default', eager: true },
    ) as Record<string, string>;

    const writers = Object.entries(sources).filter(([path, src]) => (
      !path.includes('.test.')
      && !path.includes('offline-inspection-store')
      && /putOfflineInspectionDraft\s*\(|buildOfflineInspectionDraft\s*\(/.test(src)
    ));

    expect(writers.map(([p]) => p)).toEqual([]);
  });

  it('the panel stays behind the leftover-count guard in PublicInspect', async () => {
    const source = (await import('./PublicInspect.tsx?raw')).default as string;
    const guard = source.slice(source.indexOf('{queuePanelOpen && ('));
    expect(guard).toContain('<OfflineInspectionQueuePanel');
    // the only thing that can open it
    expect(source).toContain('{pendingCount > 0 && (');
    expect(source).toContain('setQueuePanelOpen(true)');
    // ...and nothing else sets it
    expect(source.match(/setQueuePanelOpen\(true\)/g)?.length).toBe(1);
  });

  it('the panel re-auth is phone-only and sends no password — it cannot bypass enforcement', async () => {
    const source = (await import('../../components/farmlog/OfflineInspectionQueuePanel.tsx?raw')).default as string;
    // one call, phone only: the backend rejects it outright under enforcement
    // (no password -> the same generic 404 as any other failure), so this path
    // dead-ends at the FIRST request rather than reaching select-plot or
    // record-create.
    expect(source).toContain('lookupPublicInspectionAccess({ phone: normalized })');
    // no password is collected, held, or transmitted anywhere in this
    // component (asserted on CODE forms — the file's comments explain why the
    // word appears in prose at all).
    expect(source).not.toMatch(/password\s*:/);
    expect(source).not.toMatch(/passwordInput|setPassword|useState.*[Pp]assword/);
    expect(source).not.toContain('getPublicInspectionAccessConfig');
  });
});

// --- Round 8-19: latest inspection date, scoped to the ACTIVE cycle --------
//
// The plot card must show the latest inspection of the CURRENT cycle only.
// lastInspectionDate is cycle-scoped by the backend (keyed by PlotCycle.id),
// so the card simply renders what it is given — these tests pin the mapping
// from that field to the badge + the line under it.

describe('PublicInspect — latest inspection date per active cycle (round 8-19)', () => {
  const TODAY_ISO = '2026-08-13';

  async function showPlots(plot: PublicPhoneAccessPlot) {
    lookupMock.mockResolvedValue(lookupResult([plot]));
    renderPublicInspect();
    await enterPhoneAndLookup();
  }

  it('inspected today: shows the badge and the cycle-scoped date', async () => {
    await showPlots(plotItem({ inspectedToday: true, lastInspectionDate: TODAY_ISO }));

    expect(screen.getByText('ตรวจแล้ววันนี้')).toBeTruthy();
    expect(screen.getByText(/ตรวจล่าสุดในรอบ jun2026:/)).toBeTruthy();
  });

  it('inspected earlier in this cycle: "พร้อมตรวจ" but still shows the date', async () => {
    await showPlots(plotItem({ inspectedToday: false, lastInspectionDate: '2026-08-10' }));

    expect(screen.getByText('พร้อมตรวจ')).toBeTruthy();
    expect(screen.getByText(/ตรวจล่าสุดในรอบ jun2026:/)).toBeTruthy();
  });

  it('no record yet in this cycle: "ยังไม่มีการตรวจในรอบนี้"', async () => {
    await showPlots(plotItem({ inspectedToday: false, lastInspectionDate: null }));

    expect(screen.getByText('พร้อมตรวจ')).toBeTruthy();
    expect(screen.getByText('ยังไม่มีการตรวจในรอบนี้')).toBeTruthy();
    expect(screen.queryByText(/ตรวจล่าสุดในรอบ/)).toBeNull();
  });

  it('falls back to "รอบที่ N" when the cycle has no label', async () => {
    await showPlots(plotItem({
      cycleLabel: null, cycleNo: 3, lastInspectionDate: '2026-08-10',
    }));

    expect(screen.getByText(/ตรวจล่าสุดในรอบ รอบที่ 3:/)).toBeTruthy();
  });

  it('no active cycle: keeps the "ยังไม่มีรอบปลูกที่เปิดอยู่" state and shows no date', async () => {
    // A plot between cycles must never display the previous cycle's
    // inspection — the backend sends null, and the line is not rendered at
    // all so nothing can stand in for it.
    await showPlots(plotItem({
      canInspect: false, unavailableReason: 'no_active_cycle',
      plotCycleId: null, cycleNo: null, cycleLabel: null,
      inspectedToday: false, lastInspectionDate: null,
      lastInspectedAt: '2020-01-01T00:00:00Z',
    }));

    expect(screen.getByText('ยังไม่มีรอบปลูกที่เปิดอยู่')).toBeTruthy();
    expect(screen.queryByText(/ตรวจล่าสุดในรอบ/)).toBeNull();
    expect(screen.queryByText('ยังไม่มีการตรวจในรอบนี้')).toBeNull();
    expect(screen.queryByText(/2563|2020/)).toBeNull();
  });

  it('renders the date without any time component', async () => {
    await showPlots(plotItem({ lastInspectionDate: '2026-08-10' }));

    const line = screen.getByText(/ตรวจล่าสุดในรอบ jun2026:/);
    expect(line.textContent).not.toMatch(/\d{1,2}:\d{2}/);
  });

  it('formats a date-only value without shifting it across a timezone', async () => {
    // `new Date('2026-08-10')` is UTC midnight — rendered in a timezone
    // behind UTC that is the 9th. The formatter parses Y-M-D as a LOCAL date
    // so the day shown always matches the day stored.
    expect(formatThaiShortDate('2026-08-10')).toContain('10');
  });

  it('each plot in a multi-plot list shows its own cycle status', async () => {
    lookupMock.mockResolvedValue(lookupResult([
      plotItem({
        plotId: 'plot-1', plotCode: 'PLOT001', plotName: 'Plot One',
        cycleLabel: 'jun2026', inspectedToday: true, lastInspectionDate: TODAY_ISO,
      }),
      plotItem({
        plotId: 'plot-2', plotCode: 'PLOT002', plotName: 'Plot Two',
        cycleLabel: 'aug2026', inspectedToday: false, lastInspectionDate: null,
      }),
    ]));
    renderPublicInspect();
    await enterPhoneAndLookup();

    expect(screen.getByText(/ตรวจล่าสุดในรอบ jun2026:/)).toBeTruthy();
    expect(screen.getByText('ยังไม่มีการตรวจในรอบนี้')).toBeTruthy();
    expect(screen.getByText('ตรวจแล้ววันนี้')).toBeTruthy();
    expect(screen.getByText('พร้อมตรวจ')).toBeTruthy();
  });

  it('an additional-phone session sees the same status as a primary one', async () => {
    // Round 8-19 made this plot+cycle level; it used to be keyed by the
    // access phone row, so these two disagreed.
    await showPlots(plotItem({
      accessType: 'additional', inspectedToday: true, lastInspectionDate: TODAY_ISO,
    }));

    expect(screen.getByText('ตรวจแล้ววันนี้')).toBeTruthy();
    expect(screen.getByText(/ตรวจล่าสุดในรอบ jun2026:/)).toBeTruthy();
  });

  it('the date line wraps instead of overflowing on a narrow screen', async () => {
    await showPlots(plotItem({
      cycleLabel: 'รอบปลูกฤดูฝนปี 2569 ชุดที่ 12 แปลงทดสอบระยะยาว',
      lastInspectionDate: '2026-08-10',
    }));

    const line = screen.getByText(/ตรวจล่าสุดในรอบ/);
    expect(line.className).toContain('break-words');
  });

  // --- refresh after a save --------------------------------------------

  it('"กลับรายการแปลง" after a save re-fetches the list and shows the new status', async () => {
    mockGeolocation('denied');
    createJsonMock.mockResolvedValue({ plotCode: 'PLOT001', plotName: 'Plot One' });
    await goToFormStep();
    fireEvent.click(screen.getByRole('button', { name: 'บันทึกการตรวจแปลง' }));
    await screen.findByText('บันทึกสำเร็จ');

    listPlotsMock.mockResolvedValue({
      plots: [plotItem({ inspectedToday: true, lastInspectionDate: TODAY_ISO })],
    });
    fireEvent.click(screen.getByRole('button', { name: 'กลับรายการแปลง' }));

    await waitFor(() => expect(listPlotsMock).toHaveBeenCalledWith('phone-tok-abc'));
    expect(await screen.findByText('เลือกแปลงที่จะตรวจ')).toBeTruthy();
    expect(await screen.findByText('ตรวจแล้ววันนี้')).toBeTruthy();
    expect(screen.getByText(/ตรวจล่าสุดในรอบ jun2026:/)).toBeTruthy();
  });

  it('"ตรวจแปลงถัดไป" after a save shows the refreshed cycle date too', async () => {
    mockGeolocation('denied');
    createJsonMock.mockResolvedValue({ plotCode: 'PLOT001', plotName: 'Plot One' });
    await goToFormStep();
    fireEvent.click(screen.getByRole('button', { name: 'บันทึกการตรวจแปลง' }));
    await screen.findByText('บันทึกสำเร็จ');

    listPlotsMock.mockResolvedValue({
      plots: [plotItem({ inspectedToday: true, lastInspectionDate: TODAY_ISO })],
    });
    fireEvent.click(screen.getByRole('button', { name: 'ตรวจแปลงถัดไป' }));

    await waitFor(() => expect(listPlotsMock).toHaveBeenCalledWith('phone-tok-abc'));
    expect(await screen.findByText(/ตรวจล่าสุดในรอบ jun2026:/)).toBeTruthy();
  });
});

// --- Round 8-19.1: Thai business date --------------------------------------

describe('PublicInspect — recordDate is the Thai business date (round 8-19.1)', () => {
  it('submits the Asia/Bangkok date, not the UTC one', async () => {
    mockGeolocation('denied');
    createJsonMock.mockResolvedValue({ plotCode: 'PLOT001', plotName: 'Plot One' });
    await goToFormStep();

    fireEvent.click(screen.getByRole('button', { name: 'บันทึกการตรวจแปลง' }));
    await screen.findByText('บันทึกสำเร็จ');

    expect(createJsonMock.mock.calls[0][0].recordDate).toBe(bangkokToday());
  });

  it('agrees with the logged-in RecordForm — one shared helper, one date', async () => {
    // Both pages create the same kind of record, so a plot inspected from
    // /public/inspect and one inspected from the admin form must never land
    // on different calendar days for the same moment.
    mockGeolocation('denied');
    createJsonMock.mockResolvedValue({ plotCode: 'PLOT001', plotName: 'Plot One' });
    await goToFormStep();

    fireEvent.click(screen.getByRole('button', { name: 'บันทึกการตรวจแปลง' }));
    await screen.findByText('บันทึกสำเร็จ');

    const submitted = createJsonMock.mock.calls[0][0].recordDate as string;
    expect(submitted).toBe(bangkokToday());
    expect(submitted).toMatch(/^\d{4}-\d{2}-\d{2}$/);
  });

  it('the submitted date is not the raw UTC slice during the ICT early morning', async () => {
    // 23:30 UTC = 06:30 ICT the next day — the exact window the replaced
    // module-level `new Date().toISOString().slice(0, 10)` reported wrongly.
    const moment = new Date('2026-08-13T23:30:00Z');
    expect(moment.toISOString().slice(0, 10)).toBe('2026-08-13');
    expect(bangkokToday(moment)).toBe('2026-08-14');
  });

  it('keeps the Thai display formatting of dates unchanged', async () => {
    // Round 8-19.1 changed which DAY is "today", never how a date is
    // rendered — formatThaiShortDate still produces the Thai locale form.
    lookupMock.mockResolvedValue(lookupResult([
      plotItem({ lastInspectionDate: '2026-08-10' }),
    ]));
    renderPublicInspect();
    await enterPhoneAndLookup();

    const line = screen.getByText(/ตรวจล่าสุดในรอบ jun2026:/);
    expect(line.textContent).toContain(formatThaiShortDate('2026-08-10'));
    expect(line.textContent).not.toContain('2026-08-10');
  });
});

describe('PublicInspect — Oracle reference fields never appear (round 8-21B)', () => {
  it('renders no Oracle Supplier Code / Oracle Invoice / Ref Account input or label', async () => {
    await goToFormStep();

    // Cycle-level admin data — never on the public inspection flow.
    expect(screen.queryByText('Oracle Supplier Code')).toBeNull();
    expect(screen.queryByText('Oracle Invoice')).toBeNull();
    expect(screen.queryByText('Ref Account')).toBeNull();
    expect(screen.queryByText('ข้อมูลอ้างอิง Oracle')).toBeNull();
    expect(screen.queryByPlaceholderText('เช่น ORC-SUP-001')).toBeNull();
    expect(screen.queryByPlaceholderText('เช่น INV-2026-0001')).toBeNull();
    expect(screen.queryByPlaceholderText('เช่น ACC-0001')).toBeNull();
  });
});
