/**
 * PlotStatusReport — round 7.4: identity + yield come from the plot's ACTIVE
 * cycle. A plot with no active cycle shows "รอเริ่มรอบปลูก" and no yield plan
 * (never a stale crop/yield from a closed cycle); a plot with an active cycle
 * shows its crop and the computed current yield.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { PlotStatusReport } from './PlotStatusReport';
import type { PlotStatusRow } from '../../../api/reports';

const listPlotStatusMock = vi.fn();

vi.mock('../../../api/reports', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../../api/reports')>();
  return {
    ...actual,
    listPlotStatus: (...a: unknown[]) => listPlotStatusMock(...a),
    downloadPlotStatusReport: vi.fn(),
  };
});

vi.mock('../../../api/suppliers', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../../api/suppliers')>();
  return { ...actual, listSuppliers: () => Promise.resolve([]) };
});

vi.mock('../../../api/plots', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../../api/plots')>();
  return { ...actual, listPlotProvinces: () => Promise.resolve([]) };
});

vi.mock('../../../api/masterdata', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../../api/masterdata')>();
  return { ...actual, listMasterData: () => Promise.resolve([]) };
});

function row(overrides: Partial<PlotStatusRow> = {}): PlotStatusRow {
  return {
    plotId: 'plot-1', supplierCode: 'SUP001', supplierName: 'Supplier One',
    plotCode: 'SUP001-P001', plotName: 'แปลงทดสอบ', province: 'เชียงใหม่',
    activeCycleId: 'cycle-1', activeCycleNo: 1, activeCycleStatus: 'active',
    currentCrop: 'พริก', currentVariety: 'พริกขี้หนู', currentStage: 'ออกดอก',
    currentYieldPct: '80', expectedYieldFull: '1000.00', expectedYieldUnit: 'kg',
    plantCount: 500,
    currentFieldPrepScore: 8, currentWeatherScore: 7, currentCareScore: 6,
    currentVarietyResistanceScore: 5,
    lastInspectedAt: '2026-06-15T09:30:00Z', lastInspectedByCode: 'W01',
    isInspected: true,
    ...overrides,
  };
}

function renderReport() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter><PlotStatusReport /></MemoryRouter>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  listPlotStatusMock.mockReset();
});

describe('PlotStatusReport — active-cycle alignment (round 7.4)', () => {
  it('shows the active-cycle crop and the computed current yield', async () => {
    listPlotStatusMock.mockResolvedValue([row()]);
    renderReport();

    expect(await screen.findByText('พริก')).toBeTruthy();
    // 80% of 1,000 kg = 800 kg
    expect(screen.getByText('800 kg')).toBeTruthy();
  });

  it('shows "รอเริ่มรอบปลูก" and suppresses the yield plan when there is no active cycle', async () => {
    listPlotStatusMock.mockResolvedValue([row({
      activeCycleId: null, activeCycleNo: null, activeCycleStatus: null,
      currentCrop: null, currentVariety: null, currentStage: null,
      currentYieldPct: null, expectedYieldFull: null, expectedYieldUnit: null,
      plantCount: null, isInspected: false,
      lastInspectedAt: null, lastInspectedByCode: null,
    })]);
    renderReport();

    expect(await screen.findByText('รอเริ่มรอบปลูก')).toBeTruthy();
    expect(screen.queryByText('800 kg')).toBeNull();
  });
});

// Round 8-25D — this report had NO ceiling at all before (every matching
// plot came back in one response). Same [100, 200, 500, 'ทั้งหมด'] contract
// as the Plots admin page test suite above.
function hasListPlotStatusCallContaining(expected: Record<string, unknown>) {
  return listPlotStatusMock.mock.calls.some(([params]) => (
    Object.entries(expected).every(([key, value]) => (params as Record<string, unknown>)[key] === value)
  ));
}

describe('PlotStatusReport — rows-per-page selector (100 / 200 / 500 / ทั้งหมด)', () => {
  it('defaults to fetching 100 rows', async () => {
    listPlotStatusMock.mockResolvedValue([row()]);
    renderReport();

    await waitFor(() => expect(hasListPlotStatusCallContaining({ limit: 100, offset: 0 })).toBe(true));
    const selector = screen.getByLabelText('แสดง') as HTMLSelectElement;
    expect(selector.value).toBe('100');
  });

  it('switches to 500 rows per page when selected', async () => {
    listPlotStatusMock.mockResolvedValue([row()]);
    renderReport();

    await screen.findByText('SUP001-P001');
    fireEvent.change(screen.getByLabelText('แสดง'), { target: { value: '500' } });

    await waitFor(() => expect(hasListPlotStatusCallContaining({ limit: 500, offset: 0 })).toBe(true));
  });

  it('pages through everything (chunked) when "ทั้งหมด" is selected', async () => {
    const firstChunk = Array.from({ length: 200 }, (_, i) => row({
      plotId: `plot-${i}`, plotCode: `SUP001-P${i}`,
    }));
    listPlotStatusMock
      .mockResolvedValueOnce([row()])   // default 100-row load on mount
      .mockResolvedValueOnce(firstChunk) // "all": chunk 1 (full → keep going)
      .mockResolvedValueOnce([row()]);   // "all": chunk 2 (short → stop)

    renderReport();
    await screen.findByText('SUP001-P001');

    fireEvent.change(screen.getByLabelText('แสดง'), { target: { value: 'all' } });

    await waitFor(() => expect(hasListPlotStatusCallContaining({ limit: 200, offset: 200 })).toBe(true), {
      timeout: 15000,
    });
    expect(screen.queryByText('ถัดไป →')).toBeNull();
  }, 20000);

  it('never sends a limit/offset to the export download', async () => {
    listPlotStatusMock.mockResolvedValue([row()]);
    const downloadMock = (await import('../../../api/reports')).downloadPlotStatusReport as unknown as ReturnType<typeof vi.fn>;
    downloadMock.mockResolvedValue(new Blob(['xlsx']));
    renderReport();
    await screen.findByText('SUP001-P001');
    fireEvent.change(screen.getByLabelText('แสดง'), { target: { value: '500' } });
    await waitFor(() => expect(hasListPlotStatusCallContaining({ limit: 500 })).toBe(true));
    await screen.findByText('SUP001-P001');

    fireEvent.click(screen.getByText('ดาวน์โหลด Excel'));

    await waitFor(() => expect(downloadMock).toHaveBeenCalled());
    const exportParams = downloadMock.mock.calls[0][0] as Record<string, unknown>;
    expect(exportParams.limit).toBeUndefined();
    expect(exportParams.offset).toBeUndefined();
  });
});

// Round X — one "เลขที่ Invoice" box, shared by the table and the export.
describe('PlotStatusReport — invoice search (round X)', () => {
  it('sends the typed invoice to the table query, back on page 1', async () => {
    // A full page, so there IS a page 2 to be on when the search changes.
    listPlotStatusMock.mockResolvedValue(Array.from({ length: 100 }, (_, i) => row({
      plotId: `plot-${i}`, plotCode: `SUP001-P${i}`,
    })));
    renderReport();
    await screen.findByText('SUP001-P0');
    fireEvent.click(screen.getByText('ถัดไป →'));
    await waitFor(() => expect(hasListPlotStatusCallContaining({ offset: 100 })).toBe(true));

    fireEvent.change(screen.getByLabelText('เลขที่ Invoice'), { target: { value: 'INV-26' } });

    await waitFor(() => expect(hasListPlotStatusCallContaining({ invoice: 'INV-26', offset: 0 })).toBe(true));
  });

  it('exports exactly what the invoice search shows', async () => {
    listPlotStatusMock.mockResolvedValue([row()]);
    const downloadMock = (await import('../../../api/reports')).downloadPlotStatusReport as unknown as ReturnType<typeof vi.fn>;
    downloadMock.mockResolvedValue(new Blob(['xlsx']));
    renderReport();
    await screen.findByText('SUP001-P001');
    fireEvent.change(screen.getByLabelText('เลขที่ Invoice'), { target: { value: 'INV-26' } });
    await waitFor(() => expect(hasListPlotStatusCallContaining({ invoice: 'INV-26' })).toBe(true));
    await screen.findByText('SUP001-P001');

    fireEvent.click(screen.getByText('ดาวน์โหลด Excel'));

    await waitFor(() => expect(downloadMock).toHaveBeenCalled());
    expect((downloadMock.mock.calls.at(-1)![0] as Record<string, unknown>).invoice).toBe('INV-26');
  });

  it("shows the open cycle's invoice in its own column", async () => {
    listPlotStatusMock.mockResolvedValue([row({ oracleInvoice: 'INV-2026-0001' })]);
    renderReport();
    expect(await screen.findByText('INV-2026-0001')).toBeTruthy();
    expect(screen.getByRole('columnheader', { name: 'Invoice' })).toBeTruthy();
  });
});
