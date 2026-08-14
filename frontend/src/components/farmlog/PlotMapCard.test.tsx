import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { QueryClientProvider, QueryClient } from '@tanstack/react-query';
import { PlotMapCard } from './PlotMapCard';

const listPlotsMock = vi.fn();

vi.mock('../../api/plots', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../api/plots')>();
  return { ...actual, listPlots: (...args: unknown[]) => listPlotsMock(...args) };
});

function plot(overrides: Partial<import('../../api/plots').PlotSummary>) {
  return {
    id: 'p1', supplierId: 'sup-1', plotCode: 'SUP001-P001', name: 'แปลง',
    village: null, district: null, province: 'เชียงใหม่',
    latitude: '18.79', longitude: '98.98',
    isActive: true, assignedCount: 0, qrKey: null,
    currentYieldPct: null, expectedYieldFull: null, expectedYieldUnit: null, plantCount: null,
    currentCrop: 'พริก', currentVariety: null, currentLotNo: null, currentPlantingDate: null,
    ...overrides,
  };
}

function renderCard() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <PlotMapCard />
    </QueryClientProvider>,
  );
}

/** Marker circles live inside the map svg only — scope to it so the header's
 * MapPin lucide icon (which also renders a <circle>) isn't miscounted. */
function markerCount(container: HTMLElement): number {
  return container.querySelectorAll('svg[aria-label] [data-marker]').length;
}

beforeEach(() => {
  listPlotsMock.mockReset();
});

describe('PlotMapCard', () => {
  it('renders a marker per plot with a valid coordinate', async () => {
    // Single page shorter than PAGE_SIZE → fetchAllPages stops after one call.
    listPlotsMock.mockResolvedValue([
      plot({ id: 'p1', plotCode: 'SUP001-P001', currentCrop: 'พริก' }),
      plot({ id: 'p2', plotCode: 'SUP002-P001', currentCrop: 'ข้าวโพด', province: 'น่าน', latitude: '19.2', longitude: '100.7' }),
    ]);

    const { container } = renderCard();

    await waitFor(() => expect(markerCount(container)).toBe(2));
    expect(screen.getByText(/แสดง 2 แปลงบนแผนที่/)).toBeTruthy();
  });

  it('counts plots without a coordinate as skipped instead of drawing them', async () => {
    listPlotsMock.mockResolvedValue([
      plot({ id: 'p1', currentCrop: 'พริก' }),
      plot({ id: 'p2', latitude: null, longitude: null }),
    ]);

    const { container } = renderCard();

    await waitFor(() => expect(markerCount(container)).toBe(1));
    expect(screen.getByText(/1 แปลงไม่มีพิกัด/)).toBeTruthy();
  });

  it('filters markers by crop', async () => {
    listPlotsMock.mockResolvedValue([
      plot({ id: 'p1', currentCrop: 'พริก' }),
      plot({ id: 'p2', currentCrop: 'ข้าวโพด', latitude: '19.2', longitude: '100.7' }),
    ]);

    const { container } = renderCard();
    await waitFor(() => expect(markerCount(container)).toBe(2));

    // First combobox is the crop filter.
    const selects = screen.getAllByRole('combobox');
    fireEvent.change(selects[0], { target: { value: 'พริก' } });

    await waitFor(() => expect(markerCount(container)).toBe(1));
  });
});
