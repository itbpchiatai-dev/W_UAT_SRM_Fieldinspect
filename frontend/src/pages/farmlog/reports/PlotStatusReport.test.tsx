/**
 * PlotStatusReport — round 7.4: identity + yield come from the plot's ACTIVE
 * cycle. A plot with no active cycle shows "รอเริ่มรอบปลูก" and no yield plan
 * (never a stale crop/yield from a closed cycle); a plot with an active cycle
 * shows its crop and the computed current yield.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen } from '@testing-library/react';
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
