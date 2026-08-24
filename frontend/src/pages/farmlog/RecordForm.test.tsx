/**
 * RecordForm — round 15.1 regression guard: selecting a plot whose
 * latitude/longitude come back as JSON strings (the actual runtime shape
 * from the backend's Decimal serialization) must not crash. Before this
 * round, the "ข้อมูลแปลง" GPS line called `selectedPlot.latitude.toFixed(6)`
 * directly, which threw a real TypeError for exactly this data shape.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor, within } from '@testing-library/react';
import { MemoryRouter, Routes, Route } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { RecordForm } from './RecordForm';
import { useAuthStore } from '../../stores/auth';
import { bangkokToday } from '../../lib/business-date';

const listSuppliersMock = vi.fn();
const listPlotsMock = vi.fn();
const listFieldDefinitionsMock = vi.fn();
const listMasterDataMock = vi.fn();
const lookupPlotByQrKeyMock = vi.fn();
const lookupPlotByQrMock = vi.fn();
const createRecordMock = vi.fn();
const createRecordWithPhotosMock = vi.fn();

vi.mock('../../api/records', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../api/records')>();
  return {
    ...actual,
    createRecord: (...args: unknown[]) => createRecordMock(...args),
    createRecordWithPhotos: (...args: unknown[]) => createRecordWithPhotosMock(...args),
  };
});

// Round 8-14B — client-side compression is PhotoSlotPicker's own concern
// (see its test file); mocked here so photo tests don't depend on jsdom's
// nonexistent createImageBitmap/canvas/<img> decode support.
const prepareInspectionPhotoMock = vi.fn();
vi.mock('../../lib/inspection-photo-compression', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../lib/inspection-photo-compression')>();
  return { ...actual, prepareInspectionPhoto: (...args: unknown[]) => prepareInspectionPhotoMock(...args) };
});

const fetchProtocolsMock = vi.fn();
// Only the network fetch is mocked; findProtocolForStage/missingProtocolScores
// keep their real implementations so the form's protocol logic is exercised.
vi.mock('../../api/inspectionProtocols', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../api/inspectionProtocols')>();
  return { ...actual, fetchInspectionProtocols: (...args: unknown[]) => fetchProtocolsMock(...args) };
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
    { growthStage: 'เจริญเติบโต', criteria: [
      { slot: 'fieldPrepScore', label: 'สภาพอากาศ' },
      { slot: 'weatherScore', label: 'การดูแลรักษา' },
      { slot: 'careScore', label: 'ความเสี่ยง' },
      { slot: 'varietyResistanceScore', label: 'สภาพแปลง' },
    ] },
  ],
};

vi.mock('../../api/suppliers', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../api/suppliers')>();
  return { ...actual, listSuppliers: (...args: unknown[]) => listSuppliersMock(...args) };
});

vi.mock('../../api/plots', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../api/plots')>();
  return {
    ...actual,
    listPlots: (...args: unknown[]) => listPlotsMock(...args),
    lookupPlotByQrKey: (...args: unknown[]) => lookupPlotByQrKeyMock(...args),
    lookupPlotByQr: (...args: unknown[]) => lookupPlotByQrMock(...args),
  };
});

// Round 20 — QR scanning drives through a real camera UI (html5-qrcode)
// that JSDOM can't render; stub it with a button that immediately reports
// a fixed scanned string, matching the "PlotQrScan never needs the real
// camera library mocked" approach used elsewhere by just replacing the
// whole component for these specific tests.
let scannedText = '';
vi.mock('../../components/farmlog/PlotQrScan', () => ({
  PlotQrScan: ({ onResult }: { onResult: (code: string) => void }) => (
    <button type="button" onClick={() => onResult(scannedText)}>__mock_scan__</button>
  ),
}));

vi.mock('../../api/fielddefs', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../api/fielddefs')>();
  return { ...actual, listFieldDefinitions: (...args: unknown[]) => listFieldDefinitionsMock(...args) };
});

vi.mock('../../api/masterdata', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../api/masterdata')>();
  return { ...actual, listMasterData: (...args: unknown[]) => listMasterDataMock(...args) };
});

function mockGeolocationDenied() {
  Object.defineProperty(navigator, 'geolocation', {
    value: {
      getCurrentPosition: vi.fn((_success, error) => error?.({ code: 1, message: 'denied' })),
    },
    configurable: true,
  });
}

function renderNewRecordForm() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={['/farmlog/records/new']}>
        <Routes>
          <Route path="/farmlog/records/:id" element={<RecordForm />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

/** Renders the new-record form deep-linked from a "ตรวจแปลง" entry point,
 * i.e. with ?supplierId=&plotId= already in the URL. */
function renderPrefilledRecordForm(supplierId: string, plotId: string) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={[`/farmlog/records/new?supplierId=${supplierId}&plotId=${plotId}`]}>
        <Routes>
          <Route path="/farmlog/records/:id" element={<RecordForm />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

function masterDataItem(overrides: Partial<{
  id: string; type: string; value: string; parent: string | null; orderIndex: number;
}>) {
  return {
    id: 'md-1', type: 'crop', value: '', parent: null, orderIndex: 0, active: true,
    createdAt: '2026-01-01T00:00:00Z', updatedAt: '2026-01-01T00:00:00Z',
    ...overrides,
  };
}

beforeEach(() => {
  listSuppliersMock.mockReset();
  listPlotsMock.mockReset();
  listFieldDefinitionsMock.mockReset();
  listMasterDataMock.mockReset();
  lookupPlotByQrKeyMock.mockReset();
  lookupPlotByQrMock.mockReset();
  createRecordMock.mockReset();
  createRecordWithPhotosMock.mockReset();
  prepareInspectionPhotoMock.mockReset();
  prepareInspectionPhotoMock.mockImplementation(async (file: File, outputName: string) => ({
    file: new File([file], outputName, { type: 'image/webp' }),
    compressed: true,
    originalBytes: file.size,
    outputBytes: file.size,
    fallbackUsed: false,
    warning: null,
  }));
  fetchProtocolsMock.mockReset();
  fetchProtocolsMock.mockResolvedValue(PROTOCOLS);
  useAuthStore.setState({ permissionKeys: new Set<string>() });
  listFieldDefinitionsMock.mockResolvedValue([]);
  listSuppliersMock.mockResolvedValue([
    { id: 'sup-1', code: 'SUP001', name: 'Supplier One', isActive: true, contactName: null, contactEmail: null },
  ]);
  listMasterDataMock.mockResolvedValue([]);
  listPlotsMock.mockResolvedValue([]);
  mockGeolocationDenied();
  scannedText = '';
});

describe('RecordForm — plot GPS with string coordinates', () => {
  it('does not crash and renders the formatted GPS line when the selected plot has string lat/lng', async () => {
    listPlotsMock.mockResolvedValue([
      {
        id: 'plot-1', supplierId: 'sup-1', plotCode: 'SUP001-P001', name: 'แปลงทดสอบ',
        village: null, district: null, province: 'จังหวัดทดสอบ',
        latitude: '13.7563000', longitude: '100.5018000',
        isActive: true, assignedCount: 0,
        activeCycleId: 'cycle-1',
      },
    ]);

    renderNewRecordForm();

    // Wait for the supplier option to actually be in the DOM before
    // selecting it — the select shows the placeholder from first render,
    // so waiting on that alone races the async suppliers query.
    await screen.findByText('Supplier One');
    const supplierSelect = screen.getByDisplayValue('— เลือก Supplier —');
    fireEvent.change(supplierSelect, { target: { value: 'sup-1' } });

    const plotPickerTrigger = await screen.findByText('— เลือกแปลง —');
    fireEvent.click(plotPickerTrigger);

    const plotOption = await screen.findByText('SUP001-P001');
    fireEvent.click(plotOption);

    // The crash (round 15) was a thrown TypeError from .toFixed() on a
    // string — if that regressed, this render would throw before ever
    // reaching this assertion.
    expect(await screen.findByText('13.756300, 100.501800')).toBeTruthy();
  });

  it('does not crash and shows no GPS line when latitude/longitude are null', async () => {
    listPlotsMock.mockResolvedValue([
      {
        id: 'plot-2', supplierId: 'sup-1', plotCode: 'SUP001-P002', name: 'แปลงไม่มี GPS',
        village: null, district: null, province: null,
        latitude: null, longitude: null,
        isActive: true, assignedCount: 0,
        activeCycleId: 'cycle-2',
      },
    ]);

    renderNewRecordForm();

    // Wait for the supplier option to actually be in the DOM before
    // selecting it — the select shows the placeholder from first render,
    // so waiting on that alone races the async suppliers query.
    await screen.findByText('Supplier One');
    const supplierSelect = screen.getByDisplayValue('— เลือก Supplier —');
    fireEvent.change(supplierSelect, { target: { value: 'sup-1' } });

    const plotPickerTrigger = await screen.findByText('— เลือกแปลง —');
    fireEvent.click(plotPickerTrigger);

    const plotOption = await screen.findByText('SUP001-P002');
    fireEvent.click(plotOption);

    await waitFor(() => expect(screen.getByText('แปลงไม่มี GPS')).toBeTruthy());
    expect(screen.queryByText(/GPS แปลง/)).toBeNull();
  });
});

describe('RecordForm — plot master crop/variety are read-only in inspection flow (codex 1)', () => {
  it('shows crop/variety/lot/planting date from the selected plot and does not render crop/variety pickers', async () => {
    listMasterDataMock.mockImplementation(({ type }: { type: string }) => {
      if (type === 'crop') {
        return Promise.resolve([
          masterDataItem({ id: 'crop-1', type: 'crop', value: 'พริก' }),
          masterDataItem({ id: 'crop-2', type: 'crop', value: 'เมล่อน' }),
        ]);
      }
      if (type === 'variety') {
        return Promise.resolve([
          masterDataItem({ id: 'v-1', type: 'variety', value: 'พริกขี้หนู', parent: 'พริก' }),
        ]);
      }
      return Promise.resolve([]);
    });
    listPlotsMock.mockResolvedValue([
      {
        id: 'plot-1', supplierId: 'sup-1', plotCode: 'SUP001-P001', name: 'แปลงพริก',
        village: null, district: null, province: null,
        latitude: null, longitude: null,
        isActive: true, assignedCount: 0,
        currentCrop: 'พริก',
        currentVariety: 'พริกขี้หนู',
        currentLotNo: 'LOT-01',
        currentPlantingDate: '2026-07-01',
      },
    ]);

    renderNewRecordForm();

    await screen.findByText('Supplier One');
    fireEvent.change(screen.getByDisplayValue('— เลือก Supplier —'), { target: { value: 'sup-1' } });

    const plotPickerTrigger = await screen.findByText('— เลือกแปลง —');
    fireEvent.click(plotPickerTrigger);
    fireEvent.click(await screen.findByText('SUP001-P001'));

    expect(await screen.findByText('แปลงพริก')).toBeTruthy();
    expect(screen.getByText('พริก')).toBeTruthy();
    expect(screen.getByText('พริกขี้หนู')).toBeTruthy();
    expect(screen.getByText('LOT-01')).toBeTruthy();
    expect(screen.getByText('2026-07-01')).toBeTruthy();

    // Crop/variety are now plot master data. The inspection form should not
    // let the field worker choose a different crop/variety for this record.
    expect(screen.queryByRole('button', { name: 'เมล่อน' })).toBeNull();
    expect(screen.queryByRole('button', { name: 'พริกขี้หนู', pressed: true })).toBeNull();
  });
});

describe('RecordForm — "ตรวจแปลง" deep-link prefill (supplier + plot from query params)', () => {
  it('preselects the supplier and plot from ?supplierId=&plotId= and shows the read-only plot panel', async () => {
    listPlotsMock.mockResolvedValue([
      {
        id: 'plot-7', supplierId: 'sup-1', plotCode: 'SUP001-P007', name: 'แปลงตรวจ',
        village: null, district: null, province: 'จังหวัดทดสอบ',
        latitude: null, longitude: null,
        isActive: true, assignedCount: 0,
        currentCrop: 'พริก', currentVariety: 'พริกขี้หนู',
        currentLotNo: 'LOT-07', currentPlantingDate: '2026-07-01',
      },
    ]);

    renderPrefilledRecordForm('sup-1', 'plot-7');

    // Supplier is preselected (locked select shows the chosen supplier's name).
    await waitFor(() => expect(screen.getByDisplayValue('Supplier One')).toBeTruthy());
    // Plot is preselected — the plot code shows in both the picker trigger and
    // the read-only "ข้อมูลแปลง" panel, which auto-fills from it.
    expect((await screen.findAllByText('SUP001-P007')).length).toBeGreaterThanOrEqual(1);
    expect(await screen.findByText('แปลงตรวจ')).toBeTruthy();
  });

  it('leaves the "ชื่อผู้กรอกข้อมูล" field blank on a prefilled inspection', async () => {
    listPlotsMock.mockResolvedValue([
      {
        id: 'plot-7', supplierId: 'sup-1', plotCode: 'SUP001-P007', name: 'แปลงตรวจ',
        village: null, district: null, province: null,
        latitude: null, longitude: null,
        isActive: true, assignedCount: 0,
      },
    ]);

    renderPrefilledRecordForm('sup-1', 'plot-7');

    await waitFor(() => expect(screen.getByDisplayValue('Supplier One')).toBeTruthy());
    const nameInput = screen.getByPlaceholderText('ไม่บังคับ') as HTMLInputElement;
    expect(nameInput.value).toBe('');
  });
});

describe('RecordForm — QR scan resolves the round-20 opaque qrKey format', () => {
  it('scanning a new-format QR deep link calls lookupPlotByQrKey (not the legacy lookup) and autofills supplier', async () => {
    lookupPlotByQrKeyMock.mockResolvedValue({
      plotId: 'plot-9', plotCode: 'SUP001-P009', plotName: 'แปลงเก้า',
      supplierId: 'sup-1', supplierCode: 'SUP001', supplierName: 'Supplier One',
    });
    scannedText = 'https://app.example.com/public/inspect?qr=opaque-key-xyz';

    renderNewRecordForm();

    fireEvent.click(await screen.findByTitle('สแกน QR ป้ายหน้าแปลง เพื่อเลือก Supplier และแปลงอัตโนมัติ'));
    fireEvent.click(await screen.findByText('__mock_scan__'));

    await waitFor(() => expect(lookupPlotByQrKeyMock).toHaveBeenCalledWith('opaque-key-xyz'));
    expect(lookupPlotByQrMock).not.toHaveBeenCalled();
    await waitFor(() => expect(screen.getByDisplayValue('Supplier One')).toBeTruthy());
  });

  it('shows a QR-specific not-found message (no plot/supplier code echoed) when the qrKey does not resolve', async () => {
    lookupPlotByQrKeyMock.mockRejectedValue({ isAxiosError: true, response: { status: 404 } });
    scannedText = 'https://app.example.com/public/inspect?qr=unknown-key';

    renderNewRecordForm();

    fireEvent.click(await screen.findByTitle('สแกน QR ป้ายหน้าแปลง เพื่อเลือก Supplier และแปลงอัตโนมัติ'));
    fireEvent.click(await screen.findByText('__mock_scan__'));

    expect(await screen.findByText('ไม่พบแปลงจาก QR นี้ในระบบ หรือคุณไม่มีสิทธิ์เข้าถึง — กรุณาเลือกด้วยตนเอง')).toBeTruthy();
  });

  it('still resolves a legacy supplierCode/plotCode QR via the original lookup endpoint', async () => {
    lookupPlotByQrMock.mockResolvedValue({
      plotId: 'plot-1', plotCode: 'SUP001-P001', plotName: 'แปลงเดิม',
      supplierId: 'sup-1', supplierCode: 'SUP001', supplierName: 'Supplier One',
    });
    scannedText = 'https://app.example.com/public/inspect?supplierCode=SUP001&plotCode=SUP001-P001';

    renderNewRecordForm();

    fireEvent.click(await screen.findByTitle('สแกน QR ป้ายหน้าแปลง เพื่อเลือก Supplier และแปลงอัตโนมัติ'));
    fireEvent.click(await screen.findByText('__mock_scan__'));

    await waitFor(() => expect(lookupPlotByQrMock).toHaveBeenCalledWith({ supplierCode: 'SUP001', plotCode: 'SUP001-P001' }));
    expect(lookupPlotByQrKeyMock).not.toHaveBeenCalled();
  });
});

describe('RecordForm — Yield % defaults from the plot latest inspection (round 8-8B kg-first)', () => {
  it('prefills the yield slider from currentYieldPct and labels the source with date + stage', async () => {
    listPlotsMock.mockResolvedValue([
      {
        id: 'plot-1', supplierId: 'sup-1', plotCode: 'SUP001-P001', name: 'แปลงพริก',
        village: null, district: null, province: null,
        latitude: null, longitude: null,
        isActive: true, assignedCount: 0,
        activeCycleId: 'cycle-1',
        // Round 8-8B — a comparable kg target (500 kg) so the initial-value
        // logic (lib/yield-planning.ts's computeInitialYieldValue) derives a
        // real quantity/pct pair instead of falling into the no-target branch.
        activeCycleExpectedYieldFull: '500', activeCycleExpectedYieldUnit: 'kg',
        currentYieldPct: '80.0', currentStage: 'ออกดอก',
        lastInspectedAt: '2026-07-01T08:30:00Z',
      },
    ]);

    renderNewRecordForm();

    await screen.findByText('Supplier One');
    fireEvent.change(screen.getByDisplayValue('— เลือก Supplier —'), { target: { value: 'sup-1' } });
    fireEvent.click(await screen.findByText('— เลือกแปลง —'));
    fireEvent.click(await screen.findByText('SUP001-P001'));

    // 1 decimal place display (round 8-8B contract #2/#3).
    await waitFor(() => expect(screen.getByText('80.0%')).toBeTruthy());
    expect(screen.getByText(/ค่าเริ่มต้นดึงจากการตรวจล่าสุดของแปลงนี้/)).toBeTruthy();
    expect(screen.getByText(/ระยะ: ออกดอก/)).toBeTruthy();
    // The shared YieldQuantityInput's own compact hint next to the target.
    expect(screen.getByText(/· ล่าสุด 80%/)).toBeTruthy();
  });

  it('keeps the flat 100 default and shows no source label for a never-inspected plot (target exists, no history — Part D.2)', async () => {
    listPlotsMock.mockResolvedValue([
      {
        id: 'plot-2', supplierId: 'sup-1', plotCode: 'SUP001-P002', name: 'แปลงใหม่',
        village: null, district: null, province: null,
        latitude: null, longitude: null,
        isActive: true, assignedCount: 0,
        activeCycleId: 'cycle-2',
        activeCycleExpectedYieldFull: '500', activeCycleExpectedYieldUnit: 'kg',
        currentYieldPct: null, currentStage: null, lastInspectedAt: null,
      },
    ]);

    renderNewRecordForm();

    await screen.findByText('Supplier One');
    fireEvent.change(screen.getByDisplayValue('— เลือก Supplier —'), { target: { value: 'sup-1' } });
    fireEvent.click(await screen.findByText('— เลือกแปลง —'));
    fireEvent.click(await screen.findByText('SUP001-P002'));

    await waitFor(() => expect(screen.getByText('100.0%')).toBeTruthy());
    expect(screen.queryByText(/ดึงจากการตรวจล่าสุด/)).toBeNull();
  });

  it('no comparable kg target: slider disabled, Yield % stays null — never a faked 100% (contract #12)', async () => {
    listPlotsMock.mockResolvedValue([
      {
        id: 'plot-3', supplierId: 'sup-1', plotCode: 'SUP001-P003', name: 'แปลงไม่มีแผน',
        village: null, district: null, province: null,
        latitude: null, longitude: null,
        isActive: true, assignedCount: 0,
        activeCycleId: 'cycle-3',
        // No activeCycleExpectedYieldFull/Unit at all — no plan set.
        currentYieldPct: null, currentStage: null, lastInspectedAt: null,
      },
    ]);

    renderNewRecordForm();

    await screen.findByText('Supplier One');
    fireEvent.change(screen.getByDisplayValue('— เลือก Supplier —'), { target: { value: 'sup-1' } });
    fireEvent.click(await screen.findByText('— เลือกแปลง —'));
    fireEvent.click(await screen.findByText('SUP001-P003'));

    expect(await screen.findByText('รอบปลูกนี้ไม่มีเป้าหมายหน่วย kg สำหรับคำนวณเปอร์เซ็นต์')).toBeTruthy();
    expect((screen.getByRole('slider') as HTMLInputElement).disabled).toBe(true);
  });
});

describe('RecordForm — submit (บันทึก)', () => {
  function plotForSubmit() {
    return [
      {
        id: 'plot-1', supplierId: 'sup-1', plotCode: 'SUP001-P001', name: 'แปลงพริก',
        village: null, district: null, province: null,
        latitude: null, longitude: null,
        isActive: true, assignedCount: 0,
        currentCrop: 'พริก', currentVariety: null, currentLotNo: null,
        currentPlantingDate: null, currentYieldPct: null, currentStage: null,
        lastInspectedAt: null,
      },
    ];
  }

  async function fillAndSubmit() {
    renderNewRecordForm();
    await screen.findByText('Supplier One');
    fireEvent.change(screen.getByDisplayValue('— เลือก Supplier —'), { target: { value: 'sup-1' } });
    fireEvent.click(await screen.findByText('— เลือกแปลง —'));
    fireEvent.click(await screen.findByText('SUP001-P001'));
    fireEvent.click(screen.getByRole('button', { name: 'บันทึก' }));
  }

  it('never renders a รหัสผู้กรอกข้อมูล field (retired round 8-3G)', async () => {
    listPlotsMock.mockResolvedValue(plotForSubmit());
    renderNewRecordForm();
    await screen.findByText('Supplier One');

    expect(screen.queryByText(/รหัสผู้กรอกข้อมูล/)).toBeNull();
    expect(screen.queryByPlaceholderText(/FIELD01/)).toBeNull();
  });

  it('submits successfully with no ชื่อผู้กรอกข้อมูล typed (name is optional)', async () => {
    listPlotsMock.mockResolvedValue(plotForSubmit());
    createRecordMock.mockResolvedValue({ id: 'rec-9' });

    await fillAndSubmit();

    await waitFor(() => expect(createRecordMock).toHaveBeenCalledOnce());
  });

  it('saves without GPS and without photos via the plain JSON create', async () => {
    listPlotsMock.mockResolvedValue(plotForSubmit());
    createRecordMock.mockResolvedValue({ id: 'rec-9' });

    await fillAndSubmit();

    await waitFor(() => expect(createRecordMock).toHaveBeenCalledOnce());
    const [payload] = createRecordMock.mock.calls[0];
    expect(payload.plotId).toBe('plot-1');
    expect(payload).not.toHaveProperty('submittedByCode');
    expect(payload.latitude).toBeNull();
    expect(createRecordWithPhotosMock).not.toHaveBeenCalled();
  });

  it('round 8-8B — the payload carries yieldQuantityKg the user typed, alongside the derived yieldPct preview', async () => {
    listPlotsMock.mockResolvedValue([
      {
        id: 'plot-1', supplierId: 'sup-1', plotCode: 'SUP001-P001', name: 'แปลงพริก',
        village: null, district: null, province: null,
        latitude: null, longitude: null,
        isActive: true, assignedCount: 0,
        currentCrop: 'พริก', currentVariety: null, currentLotNo: null,
        currentPlantingDate: null, currentYieldPct: null, currentStage: null,
        lastInspectedAt: null,
        activeCycleExpectedYieldFull: '1000', activeCycleExpectedYieldUnit: 'kg',
      },
    ]);
    createRecordMock.mockResolvedValue({ id: 'rec-9' });

    renderNewRecordForm();
    await screen.findByText('Supplier One');
    fireEvent.change(screen.getByDisplayValue('— เลือก Supplier —'), { target: { value: 'sup-1' } });
    fireEvent.click(await screen.findByText('— เลือกแปลง —'));
    fireEvent.click(await screen.findByText('SUP001-P001'));
    await screen.findByText('100.0%'); // default prefill (no history, target=1000kg)

    fireEvent.change(screen.getByRole('spinbutton'), { target: { value: '750' } });
    fireEvent.click(screen.getByRole('button', { name: 'บันทึก' }));

    await waitFor(() => expect(createRecordMock).toHaveBeenCalledOnce());
    const [payload] = createRecordMock.mock.calls[0];
    expect(payload.yieldQuantityKg).toBe(750);
    expect(payload.yieldPct).toBe(75);
  });

  it('round 8-8B.1 — a Yield over 150% of target still submits successfully (warning, not a blocking error)', async () => {
    listPlotsMock.mockResolvedValue([
      {
        id: 'plot-1', supplierId: 'sup-1', plotCode: 'SUP001-P001', name: 'แปลงพริก',
        village: null, district: null, province: null,
        latitude: null, longitude: null,
        isActive: true, assignedCount: 0,
        currentCrop: 'พริก', currentVariety: null, currentLotNo: null,
        currentPlantingDate: null, currentYieldPct: null, currentStage: null,
        lastInspectedAt: null,
        activeCycleExpectedYieldFull: '1000', activeCycleExpectedYieldUnit: 'kg',
      },
    ]);
    createRecordMock.mockResolvedValue({ id: 'rec-9' });

    renderNewRecordForm();
    await screen.findByText('Supplier One');
    fireEvent.change(screen.getByDisplayValue('— เลือก Supplier —'), { target: { value: 'sup-1' } });
    fireEvent.click(await screen.findByText('— เลือกแปลง —'));
    fireEvent.click(await screen.findByText('SUP001-P001'));
    await screen.findByText('100.0%');

    fireEvent.change(screen.getByRole('spinbutton'), { target: { value: '1600' } });
    expect(await screen.findByText('ผลผลิตสูงกว่า 150% ของเป้าหมาย กรุณาตรวจสอบความถูกต้องก่อนบันทึก')).toBeTruthy();

    const submitBtn = screen.getByRole('button', { name: 'บันทึก' }) as HTMLButtonElement;
    expect(submitBtn.disabled).toBe(false);
    fireEvent.click(submitBtn);

    await waitFor(() => expect(createRecordMock).toHaveBeenCalledOnce());
    const [payload] = createRecordMock.mock.calls[0];
    expect(payload.yieldQuantityKg).toBe(1600);
    expect(payload.yieldPct).toBe(160);
    // The warning text must never leak into the request payload.
    expect(JSON.stringify(payload)).not.toContain('กรุณาตรวจสอบ');
  });

  it('shows a visible error (not silence) when the server rejects the save', async () => {
    listPlotsMock.mockResolvedValue(plotForSubmit());
    createRecordMock.mockRejectedValue({
      isAxiosError: true,
      response: { status: 422, data: { detail: 'something invalid' } },
    });

    await fillAndSubmit();

    await waitFor(() => expect(createRecordMock).toHaveBeenCalledOnce());
    expect(await screen.findByText(/บันทึกไม่สำเร็จ: HTTP 422 — something invalid/)).toBeTruthy();
  });

  it('shows a friendly Thai message (not the raw backend detail) on a 409 no-active-cycle rejection (round 7.10)', async () => {
    // SmartPlotPicker doesn't filter out plots with no active planting cycle
    // (round 7.10 finding, deferred) — a user can fill the whole form before
    // hitting this 409. The raw detail is English ("No active planting cycle
    // for this plot"); it must never reach the screen verbatim.
    listPlotsMock.mockResolvedValue(plotForSubmit());
    createRecordMock.mockRejectedValue({
      isAxiosError: true,
      response: { status: 409, data: { detail: 'No active planting cycle for this plot' } },
    });

    await fillAndSubmit();

    await waitFor(() => expect(createRecordMock).toHaveBeenCalledOnce());
    expect(await screen.findByText('ไม่สามารถบันทึกได้ — แปลงนี้ยังไม่มีรอบปลูกที่เปิดอยู่ กรุณาเลือกแปลงอื่น หรือให้ผู้ดูแลระบบเริ่มรอบปลูกใหม่ก่อน')).toBeTruthy();
    expect(screen.queryByText(/No active planting cycle/)).toBeNull();
  });
});

describe('RecordForm — round 8-14B: photo processing gates submit', () => {
  function plotForSubmit() {
    return [
      {
        id: 'plot-1', supplierId: 'sup-1', plotCode: 'SUP001-P001', name: 'แปลงพริก',
        village: null, district: null, province: null,
        latitude: null, longitude: null,
        isActive: true, assignedCount: 0,
        currentCrop: 'พริก', currentVariety: null, currentLotNo: null,
        currentPlantingDate: null, currentYieldPct: null, currentStage: null,
        lastInspectedAt: null,
      },
    ];
  }

  async function goToNewRecordForm() {
    listPlotsMock.mockResolvedValue(plotForSubmit());
    renderNewRecordForm();
    await screen.findByText('Supplier One');
    fireEvent.change(screen.getByDisplayValue('— เลือก Supplier —'), { target: { value: 'sup-1' } });
    fireEvent.click(await screen.findByText('— เลือกแปลง —'));
    fireEvent.click(await screen.findByText('SUP001-P001'));
  }

  it('35. disables the บันทึก button while a photo is still mid client-side compression', async () => {
    prepareInspectionPhotoMock.mockReturnValue(new Promise(() => {})); // never settles
    await goToNewRecordForm();

    fireEvent.change(screen.getAllByLabelText(/เลือกไฟล์/)[0], { target: { files: [new File(['x'], 'a.jpg', { type: 'image/jpeg' })] } });
    await screen.findByText('กำลังเตรียมรูป...');

    const submitBtn = screen.getByRole('button', { name: 'บันทึก' }) as HTMLButtonElement;
    expect(submitBtn.disabled).toBe(true);
  });

  it('36. a direct form submit event is hard-guarded even if the disabled button is bypassed', async () => {
    prepareInspectionPhotoMock.mockReturnValue(new Promise(() => {})); // never settles
    await goToNewRecordForm();

    fireEvent.change(screen.getAllByLabelText(/เลือกไฟล์/)[0], { target: { files: [new File(['x'], 'a.jpg', { type: 'image/jpeg' })] } });
    await screen.findByText('กำลังเตรียมรูป...');

    const submitBtn = screen.getByRole('button', { name: 'บันทึก' }) as HTMLButtonElement;
    const form = submitBtn.closest('form');
    expect(form).toBeTruthy();
    fireEvent.submit(form!);

    expect(await screen.findByText('กรุณารอให้ระบบเตรียมรูปภาพเสร็จก่อน')).toBeTruthy();
    expect(createRecordMock).not.toHaveBeenCalled();
    expect(createRecordWithPhotosMock).not.toHaveBeenCalled();
  });

  it('37. compressed (WebP) files are sent through the multipart with-photos endpoint', async () => {
    createRecordWithPhotosMock.mockResolvedValue({ id: 'rec-1' });
    await goToNewRecordForm();

    fireEvent.change(screen.getAllByLabelText(/เลือกไฟล์/)[0], { target: { files: [new File(['x'], 'IMG_private.jpg', { type: 'image/jpeg' })] } });
    await waitFor(() => expect(screen.queryByText('กำลังเตรียมรูป...')).toBeNull());
    fireEvent.click(screen.getByRole('button', { name: 'บันทึก' }));

    await waitFor(() => expect(createRecordWithPhotosMock).toHaveBeenCalledOnce());
    const [, photos] = createRecordWithPhotosMock.mock.calls[0];
    expect(photos[0].type).toBe('image/webp');
    expect(photos[0].name).toBe('inspection-photo-1.webp');
    expect(createRecordMock).not.toHaveBeenCalled();
  });

  it('38. a zero-photo submit still uses the plain JSON create endpoint, unaffected by the compression pipeline', async () => {
    createRecordMock.mockResolvedValue({ id: 'rec-9' });
    await goToNewRecordForm();

    fireEvent.click(screen.getByRole('button', { name: 'บันทึก' }));

    await waitFor(() => expect(createRecordMock).toHaveBeenCalledOnce());
    expect(createRecordWithPhotosMock).not.toHaveBeenCalled();
    expect(prepareInspectionPhotoMock).not.toHaveBeenCalled();
  });
});

describe('RecordForm — append-only (round 8.0.5): create-only, no edit mode', () => {
  it('never renders "บันทึกการแก้ไข" or any editable-existing-record affordance', async () => {
    listPlotsMock.mockResolvedValue(plotForSubmit());
    renderNewRecordForm();

    await screen.findByText('บันทึกการตรวจแปลงใหม่');
    expect(screen.queryByText('บันทึกการแก้ไข')).toBeNull();
    expect(screen.getByRole('button', { name: 'บันทึก' })).toBeTruthy();
  });

  it('the create submit button always says "บันทึก" regardless of records.update permission', async () => {
    useAuthStore.setState({ permissionKeys: new Set(['records.update', 'records.create']) });
    listPlotsMock.mockResolvedValue(plotForSubmit());
    renderNewRecordForm();

    expect(await screen.findByRole('button', { name: 'บันทึก' })).toBeTruthy();
    expect(screen.queryByRole('button', { name: 'บันทึกการแก้ไข' })).toBeNull();
  });

  function plotForSubmit() {
    return [
      {
        id: 'plot-1', supplierId: 'sup-1', plotCode: 'SUP001-P001', name: 'แปลงพริก',
        village: null, district: null, province: null,
        latitude: null, longitude: null,
        isActive: true, assignedCount: 0,
        currentCrop: 'พริก', currentVariety: null, currentLotNo: null,
        currentPlantingDate: null, currentYieldPct: null, currentStage: null,
        lastInspectedAt: null,
      },
    ];
  }
});

describe('RecordForm — protocol-driven scores', () => {
  function plotForStage() {
    return [{
      id: 'plot-1', supplierId: 'sup-1', plotCode: 'SUP001-P001', name: 'แปลงพริก',
      village: null, district: null, province: null, latitude: null, longitude: null,
      isActive: true, assignedCount: 0,
      currentCrop: 'พริก', currentVariety: null, currentLotNo: null,
      currentPlantingDate: null, currentYieldPct: null, currentStage: null, lastInspectedAt: null,
    }];
  }

  function fillScore(groupLabel: string, n: number) {
    const group = screen.getByRole('group', { name: groupLabel });
    fireEvent.click(within(group).getByRole('button', { name: String(n) }));
  }

  async function renderPickPlotAndStage(stage?: string) {
    renderNewRecordForm();
    await screen.findByText('Supplier One');
    fireEvent.change(screen.getByDisplayValue('— เลือก Supplier —'), { target: { value: 'sup-1' } });
    fireEvent.click(await screen.findByText('— เลือกแปลง —'));
    fireEvent.click(await screen.findByText('SUP001-P001'));
    if (stage) fireEvent.click(await screen.findByRole('button', { name: stage }));
  }

  beforeEach(() => {
    listPlotsMock.mockResolvedValue(plotForStage());
    // growth_stage dropdown offers a protocol stage AND a supplement stage.
    listMasterDataMock.mockImplementation(({ type }: { type: string }) =>
      type === 'growth_stage'
        ? Promise.resolve([
            masterDataItem({ type: 'growth_stage', value: 'ระยะงอก' }),
            masterDataItem({ type: 'growth_stage', value: 'เจริญเติบโต' }),
            masterDataItem({ type: 'growth_stage', value: 'ตั้งตัว' }),
          ])
        : Promise.resolve([]));
  });

  it('renders protocol labels for the stage and requires all 4 scores before submit', async () => {
    createRecordMock.mockResolvedValue({ id: 'rec-p1' });
    await renderPickPlotAndStage('ระยะงอก');

    // Labels come from the protocol response, not a hardcoded set.
    expect(await screen.findByRole('group', { name: 'การเตรียมแปลง' })).toBeTruthy();
    expect(screen.getByRole('group', { name: 'การดูแลรักษา' })).toBeTruthy();
    expect(screen.getByRole('group', { name: 'ความต้านทานของสายพันธุ์' })).toBeTruthy();

    // Missing scores → blocked client-side (no 422 round trip).
    fireEvent.click(screen.getByRole('button', { name: 'บันทึก' }));
    await waitFor(() => expect(screen.getByText(/กรุณาให้คะแนนครบทั้ง 4 ช่อง/)).toBeTruthy());
    expect(createRecordMock).not.toHaveBeenCalled();

    fillScore('การเตรียมแปลง', 8);
    fillScore('สภาพอากาศ', 7);
    fillScore('การดูแลรักษา', 9);
    fillScore('ความต้านทานของสายพันธุ์', 6);
    fireEvent.click(screen.getByRole('button', { name: 'บันทึก' }));

    await waitFor(() => expect(createRecordMock).toHaveBeenCalledOnce());
    const [payload] = createRecordMock.mock.calls[0];
    expect(payload.growthStage).toBe('ระยะงอก');
    expect(payload.fieldPrepScore).toBe(8);
    expect(payload.weatherScore).toBe(7);
    expect(payload.careScore).toBe(9);
    expect(payload.varietyResistanceScore).toBe(6);
  });

  it('relabels the score inputs when a different protocol stage is picked', async () => {
    await renderPickPlotAndStage('เจริญเติบโต');
    // เจริญเติบโต maps different labels onto the same 4 slots.
    expect(await screen.findByRole('group', { name: 'ความเสี่ยง' })).toBeTruthy();
    expect(screen.getByRole('group', { name: 'สภาพแปลง' })).toBeTruthy();
    // The germination-only label must NOT be present for this stage.
    expect(screen.queryByRole('group', { name: 'การเตรียมแปลง' })).toBeNull();
  });

  it('hides score inputs and does not require scores for a non-protocol (supplement) stage', async () => {
    createRecordMock.mockResolvedValue({ id: 'rec-p2' });
    await renderPickPlotAndStage('ตั้งตัว');

    expect(await screen.findByText(/ไม่มี Protocol คะแนนเฉพาะ/)).toBeTruthy();
    expect(screen.queryByRole('group', { name: 'การเตรียมแปลง' })).toBeNull();

    fireEvent.click(screen.getByRole('button', { name: 'บันทึก' }));
    await waitFor(() => expect(createRecordMock).toHaveBeenCalledOnce());
    const [payload] = createRecordMock.mock.calls[0];
    expect(payload.growthStage).toBe('ตั้งตัว');
    expect(payload.fieldPrepScore).toBeNull();
  });

  it('never renders an editable crop/variety picker or a planting-date input', async () => {
    listMasterDataMock.mockImplementation(({ type }: { type: string }) => {
      if (type === 'growth_stage') return Promise.resolve([masterDataItem({ type: 'growth_stage', value: 'ระยะงอก' })]);
      if (type === 'crop') return Promise.resolve([masterDataItem({ type: 'crop', value: 'เมล่อน' })]);
      if (type === 'variety') return Promise.resolve([masterDataItem({ type: 'variety', value: 'เมล่อนญี่ปุ่น', parent: 'เมล่อน' })]);
      return Promise.resolve([]);
    });
    await renderPickPlotAndStage();

    // Crop/variety are read-only plot master data (text), never pickers here.
    expect(screen.queryByRole('button', { name: 'เมล่อน' })).toBeNull();
    expect(screen.queryByRole('button', { name: 'เมล่อนญี่ปุ่น' })).toBeNull();
    // No planting-date (or any date) input in the inspection form.
    expect(document.querySelector('input[type="date"]')).toBeNull();
  });
});

// --- Round 8-19.1: Thai business date -------------------------------------

describe('RecordForm — recordDate is the Thai business date (round 8-19.1)', () => {
  function plotForSubmit() {
    return [
      {
        id: 'plot-1', supplierId: 'sup-1', plotCode: 'SUP001-P001', name: 'แปลงพริก',
        village: null, district: null, province: null,
        latitude: null, longitude: null,
        isActive: true, assignedCount: 0,
        currentCrop: 'พริก', currentVariety: null, currentLotNo: null,
        currentPlantingDate: null, currentYieldPct: null, currentStage: null,
        lastInspectedAt: null,
      },
    ];
  }

  async function fillAndSubmit() {
    renderNewRecordForm();
    await screen.findByText('Supplier One');
    fireEvent.change(screen.getByDisplayValue('— เลือก Supplier —'), { target: { value: 'sup-1' } });
    fireEvent.click(await screen.findByText('— เลือกแปลง —'));
    fireEvent.click(await screen.findByText('SUP001-P001'));
    fireEvent.click(screen.getByRole('button', { name: 'บันทึก' }));
  }

  it('submits the Asia/Bangkok date, not the UTC one', async () => {
    listPlotsMock.mockResolvedValue(plotForSubmit());
    createRecordMock.mockResolvedValue({ id: 'rec-9' });

    await fillAndSubmit();

    await waitFor(() => expect(createRecordMock).toHaveBeenCalled());
    expect(createRecordMock.mock.calls[0][0].recordDate).toBe(bangkokToday());
  });

  it('displays the same Thai business date it submits', async () => {
    listPlotsMock.mockResolvedValue(plotForSubmit());
    createRecordMock.mockResolvedValue({ id: 'rec-9' });

    await fillAndSubmit();

    await waitFor(() => expect(createRecordMock).toHaveBeenCalled());
    const submitted = createRecordMock.mock.calls[0][0].recordDate as string;
    // The read-only "วันที่บันทึก" field shows exactly what was sent.
    expect(screen.getAllByText(new RegExp(submitted)).length).toBeGreaterThan(0);
  });

  it('never sends the raw UTC date during the ICT early-morning window', async () => {
    // 23:30 UTC = 06:30 ICT the NEXT day — the window the replaced
    // `new Date().toISOString().slice(0, 10)` got wrong.
    const moment = new Date('2026-08-13T23:30:00Z');
    expect(moment.toISOString().slice(0, 10)).toBe('2026-08-13');
    expect(bangkokToday(moment)).toBe('2026-08-14');
  });
});

describe('RecordForm — Oracle reference fields never appear (round 8-21B)', () => {
  it('renders no Oracle Supplier Code / Oracle Invoice / Ref Account input or label', async () => {
    listPlotsMock.mockResolvedValue([
      {
        id: 'plot-1', supplierId: 'sup-1', plotCode: 'SUP001-P001', name: 'แปลงพริก',
        village: null, district: null, province: null,
        latitude: null, longitude: null,
        isActive: true, assignedCount: 0,
        currentCrop: 'พริก', currentVariety: null, currentLotNo: null,
        currentPlantingDate: null, currentYieldPct: null, currentStage: null,
        lastInspectedAt: null,
      },
    ]);
    renderNewRecordForm();

    await screen.findByText('บันทึกการตรวจแปลงใหม่');
    // Cycle-level admin data — never on the field inspection form.
    expect(screen.queryByText('Oracle Supplier Code')).toBeNull();
    expect(screen.queryByText('Oracle Invoice')).toBeNull();
    expect(screen.queryByText('Ref Account')).toBeNull();
    expect(screen.queryByText('ข้อมูลอ้างอิง Oracle')).toBeNull();
    expect(screen.queryByPlaceholderText('เช่น ORC-SUP-001')).toBeNull();
    expect(screen.queryByPlaceholderText('เช่น INV-2026-0001')).toBeNull();
    expect(screen.queryByPlaceholderText('เช่น ACC-0001')).toBeNull();
  });
});
