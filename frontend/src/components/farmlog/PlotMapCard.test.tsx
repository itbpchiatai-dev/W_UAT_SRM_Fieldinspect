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

/** Round 38 — province-level aggregates, counted separately from exact dots. */
function bubbleCount(container: HTMLElement): number {
  return container.querySelectorAll('svg[aria-label] [data-province-bubble]').length;
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

  // Round 38 — a plot with no coordinate used to be counted and dropped. On
  // real data that was the entire map: all 738 active plots on UAT have no
  // coordinate, so this view drew nothing. Such a plot still knows its
  // province, which is a coarser but true answer.
  it('round 38: a plot with no coordinate becomes a province bubble, not a dropped row', async () => {
    listPlotsMock.mockResolvedValue([
      plot({ id: 'p1', currentCrop: 'พริก' }),
      plot({ id: 'p2', latitude: null, longitude: null, province: 'เชียงใหม่' }),
    ]);

    const { container } = renderCard();

    await waitFor(() => expect(markerCount(container)).toBe(1));
    expect(bubbleCount(container)).toBe(1);
    expect(screen.getByText(/1 ตำแหน่งจริง/)).toBeTruthy();
    expect(screen.getByText(/1 ระบุได้แค่ระดับจังหวัด/)).toBeTruthy();
    expect(screen.getByText(/แสดง 2 แปลงบนแผนที่/)).toBeTruthy();
  });

  it('round 38: both spellings of one province collapse into a single bubble', async () => {
    // The pair that exists on UAT today: the Plots form writes the master_data
    // value, the Excel import let "จ." through unchecked.
    listPlotsMock.mockResolvedValue([
      plot({ id: 'p1', latitude: null, longitude: null, province: 'จ.ขอนแก่น' }),
      plot({ id: 'p2', latitude: null, longitude: null, province: 'ขอนแก่น' }),
    ]);

    const { container } = renderCard();

    await waitFor(() => expect(bubbleCount(container)).toBe(1));
    expect(container.querySelector('[data-province-bubble]')?.getAttribute('data-province-count')).toBe('2');
    expect(screen.getByText(/2 ระบุได้แค่ระดับจังหวัด/)).toBeTruthy();
  });

  it('round 38: a plot with neither a coordinate nor a usable province is reported, never hidden', async () => {
    listPlotsMock.mockResolvedValue([
      plot({ id: 'p1', latitude: null, longitude: null, province: null }),
      plot({ id: 'p2', latitude: null, longitude: null, province: 'ไม่มีจังหวัดนี้' }),
    ]);

    const { container } = renderCard();

    await waitFor(() => expect(screen.getByText(/2 ไม่มีทั้งพิกัดและจังหวัด/)).toBeTruthy());
    expect(bubbleCount(container)).toBe(0);
    expect(markerCount(container)).toBe(0);
  });

  it('filters markers by crop', async () => {
    listPlotsMock.mockResolvedValue([
      plot({ id: 'p1', currentCrop: 'พริก' }),
      plot({ id: 'p2', currentCrop: 'ข้าวโพด', latitude: '19.2', longitude: '100.7' }),
    ]);

    const { container } = renderCard();
    await waitFor(() => expect(markerCount(container)).toBe(2));

    // Round Z — the filters are searchable listboxes: open and click.
    fireEvent.click(screen.getByRole('button', { name: 'กรองชนิดพืช' }));
    fireEvent.click(await screen.findByRole('option', { name: 'พริก' }));

    await waitFor(() => expect(markerCount(container)).toBe(1));
  });
});
