/**
 * SmartPlotPicker — round 15.1 regression guard: plot.latitude/longitude
 * can be JSON strings at runtime (Decimal serialization) — GPS-distance
 * sorting/display must normalize them before doing math/formatting rather
 * than relying on implicit JS string-to-number coercion in `-`/`*`.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { SmartPlotPicker } from './SmartPlotPicker';
import type { PlotSummary } from '../../api/plots';

const listPlotsMock = vi.fn();

vi.mock('../../api/plots', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../api/plots')>();
  return { ...actual, listPlots: (...args: unknown[]) => listPlotsMock(...args) };
});

// Round 20 — same reasoning as RecordForm.test.tsx: stub the camera UI
// with a button that reports a fixed scanned string.
let scannedText = '';
vi.mock('./PlotQrScan', () => ({
  PlotQrScan: ({ onResult }: { onResult: (code: string) => void }) => (
    <button type="button" onClick={() => onResult(scannedText)}>__mock_scan__</button>
  ),
}));

function mockGeolocationSuccess(lat: number, lng: number) {
  Object.defineProperty(navigator, 'geolocation', {
    value: {
      getCurrentPosition: vi.fn((success) => success({ coords: { latitude: lat, longitude: lng } })),
    },
    configurable: true,
  });
}

function renderPicker(onChange: (plotId: string, plot: PlotSummary | null) => void = () => {}) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <SmartPlotPicker value="" onChange={onChange} />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  listPlotsMock.mockReset();
  scannedText = '';
});

describe('SmartPlotPicker — string GPS coordinates', () => {
  it('sorts and displays distance without crashing when plot lat/lng are strings', async () => {
    listPlotsMock.mockResolvedValue([
      {
        id: 'plot-near', supplierId: 'sup-1', plotCode: 'NEAR', name: 'แปลงใกล้',
        village: null, district: null, province: null,
        latitude: '13.7570000', longitude: '100.5020000',
        isActive: true, assignedCount: 0,
      },
      {
        id: 'plot-far', supplierId: 'sup-1', plotCode: 'FAR', name: 'แปลงไกล',
        village: null, district: null, province: null,
        latitude: '14.5000000', longitude: '101.5000000',
        isActive: true, assignedCount: 0,
      },
    ]);
    mockGeolocationSuccess(13.7563, 100.5018);

    renderPicker();
    fireEvent.click(screen.getByText('— เลือกแปลง —'));
    fireEvent.click(await screen.findByTitle('เรียงตามระยะใกล้ฉัน'));

    await waitFor(() => expect(screen.getByText(/เรียงตามระยะจากคุณ/)).toBeTruthy());

    // Neither distance renders as "NaN" — both plots' string coordinates
    // were normalized before the haversine calculation.
    expect(screen.queryByText(/NaN/)).toBeNull();
    const near = await screen.findByText('แปลงใกล้');
    const far = await screen.findByText('แปลงไกล');
    expect(near).toBeTruthy();
    expect(far).toBeTruthy();
  });

  it('treats a plot with no GPS as unsortable-by-distance without crashing', async () => {
    listPlotsMock.mockResolvedValue([
      {
        id: 'plot-no-gps', supplierId: 'sup-1', plotCode: 'NOGPS', name: 'ไม่มี GPS',
        village: null, district: null, province: null,
        latitude: null, longitude: null,
        isActive: true, assignedCount: 0,
      },
    ]);
    mockGeolocationSuccess(13.7563, 100.5018);

    renderPicker();
    fireEvent.click(screen.getByText('— เลือกแปลง —'));
    fireEvent.click(await screen.findByTitle('เรียงตามระยะใกล้ฉัน'));

    expect(await screen.findByText('ไม่มี GPS')).toBeTruthy();
    expect(screen.queryByText(/กม\.|ม\./)).toBeNull();
  });
});

describe('SmartPlotPicker — QR scan resolves the round-20 opaque qrKey format', () => {
  it('matches a plot by qrKey when the scanned text is a new-format deep link', async () => {
    listPlotsMock.mockResolvedValue([
      {
        id: 'plot-qr', supplierId: 'sup-1', plotCode: 'SUP001-P009', name: 'แปลงเก้า',
        village: null, district: null, province: null,
        latitude: null, longitude: null,
        isActive: true, assignedCount: 0, qrKey: 'opaque-key-xyz',
        activeCycleId: 'cycle-qr',
      },
    ]);
    scannedText = 'https://app.example.com/public/inspect?qr=opaque-key-xyz';
    let selectedId = '';
    renderPicker((id) => { selectedId = id; });

    fireEvent.click(screen.getByText('— เลือกแปลง —'));
    fireEvent.click(await screen.findByTitle('สแกน QR รหัสแปลง'));
    fireEvent.click(await screen.findByText('__mock_scan__'));

    await waitFor(() => expect(selectedId).toBe('plot-qr'));
  });

  it('still matches a plot by bare plotCode for older signs that only encode the plain code', async () => {
    listPlotsMock.mockResolvedValue([
      {
        id: 'plot-old', supplierId: 'sup-1', plotCode: 'OLDSIGN', name: 'ป้ายเก่า',
        village: null, district: null, province: null,
        latitude: null, longitude: null,
        isActive: true, assignedCount: 0, qrKey: 'some-other-key',
        activeCycleId: 'cycle-old',
      },
    ]);
    scannedText = 'OLDSIGN';
    let selectedId = '';
    renderPicker((id) => { selectedId = id; });

    fireEvent.click(screen.getByText('— เลือกแปลง —'));
    fireEvent.click(await screen.findByTitle('สแกน QR รหัสแปลง'));
    fireEvent.click(await screen.findByText('__mock_scan__'));

    await waitFor(() => expect(selectedId).toBe('plot-old'));
  });
});

describe('SmartPlotPicker — active-cycle guard (round 7.11)', () => {
  function activePlot(overrides: Partial<PlotSummary> = {}): PlotSummary {
    return {
      id: 'plot-active', supplierId: 'sup-1', plotCode: 'ACTIVE-1', name: 'แปลงกำลังปลูก',
      village: null, district: null, province: null,
      latitude: null, longitude: null,
      isActive: true, assignedCount: 0,
      activeCycleId: 'cycle-1',
      ...overrides,
    } as PlotSummary;
  }

  function noCyclePlot(overrides: Partial<PlotSummary> = {}): PlotSummary {
    return {
      id: 'plot-no-cycle', supplierId: 'sup-1', plotCode: 'NOCYCLE-1', name: 'แปลงรอเริ่มรอบ',
      village: null, district: null, province: null,
      latitude: null, longitude: null,
      isActive: true, assignedCount: 0,
      activeCycleId: null,
      ...overrides,
    } as PlotSummary;
  }

  it('shows an active plot normally and selects it on click', async () => {
    listPlotsMock.mockResolvedValue([activePlot()]);
    let selectedId = '';
    renderPicker((id) => { selectedId = id; });

    fireEvent.click(screen.getByText('— เลือกแปลง —'));
    fireEvent.click(await screen.findByText('แปลงกำลังปลูก'));

    expect(selectedId).toBe('plot-active');
  });

  it('shows a no-active-cycle plot in the list with a "รอเริ่มรอบปลูก" badge', async () => {
    listPlotsMock.mockResolvedValue([noCyclePlot()]);
    renderPicker();

    fireEvent.click(screen.getByText('— เลือกแปลง —'));

    expect(await screen.findByText('แปลงรอเริ่มรอบ')).toBeTruthy();
    expect(screen.getByText('รอเริ่มรอบปลูก')).toBeTruthy();
  });

  it('does not call onChange when clicking a no-active-cycle plot row', async () => {
    listPlotsMock.mockResolvedValue([noCyclePlot()]);
    const onChange = vi.fn();
    renderPicker(onChange);

    fireEvent.click(screen.getByText('— เลือกแปลง —'));
    const row = await screen.findByText('แปลงรอเริ่มรอบ');
    fireEvent.click(row.closest('button')!);

    expect(onChange).not.toHaveBeenCalled();
    const button = row.closest('button') as HTMLButtonElement;
    expect(button.disabled).toBe(true);
    expect(button.title).toBe('ต้องเริ่มรอบปลูกก่อนจึงจะบันทึกการตรวจแปลงได้');
  });

  it('still calls onChange for an active plot listed alongside a no-active-cycle one', async () => {
    listPlotsMock.mockResolvedValue([noCyclePlot(), activePlot()]);
    let selectedId = '';
    renderPicker((id) => { selectedId = id; });

    fireEvent.click(screen.getByText('— เลือกแปลง —'));
    fireEvent.click(await screen.findByText('แปลงกำลังปลูก'));

    expect(selectedId).toBe('plot-active');
  });

  it('QR scan matching a no-active-cycle plot does not select it and shows a Thai error, modal stays open', async () => {
    listPlotsMock.mockResolvedValue([noCyclePlot({ qrKey: 'qr-no-cycle' })]);
    scannedText = 'https://app.example.com/public/inspect?qr=qr-no-cycle';
    let selectedId = '';
    renderPicker((id) => { selectedId = id; });

    fireEvent.click(screen.getByText('— เลือกแปลง —'));
    fireEvent.click(await screen.findByTitle('สแกน QR รหัสแปลง'));
    fireEvent.click(await screen.findByText('__mock_scan__'));

    expect(await screen.findByText('แปลงนี้ยังไม่มีรอบปลูกที่เปิดอยู่ กรุณาให้ผู้ดูแลเริ่มรอบปลูกก่อน')).toBeTruthy();
    expect(selectedId).toBe('');
    // The plot-list modal (not just the QR sub-modal) is still open — the
    // search input is still visible, proving the user wasn't bounced out.
    expect(screen.getByPlaceholderText('ค้นหารหัส / ชื่อแปลง')).toBeTruthy();
  });

  it('warns under the trigger, without auto-clearing, when the already-selected plot has no active cycle', async () => {
    listPlotsMock.mockResolvedValue([noCyclePlot()]);
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(
      <QueryClientProvider client={qc}>
        <SmartPlotPicker value="plot-no-cycle" onChange={() => {}} />
      </QueryClientProvider>,
    );

    expect(await screen.findByText('แปลงนี้ยังไม่มีรอบปลูกที่เปิดอยู่ กรุณาเลือกแปลงอื่น')).toBeTruthy();
    // Selection itself is untouched — the plot code/name still show on the trigger.
    expect(screen.getByText('NOCYCLE-1')).toBeTruthy();
  });
});
