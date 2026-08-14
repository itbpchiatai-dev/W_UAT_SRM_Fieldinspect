/**
 * ReportsPage (round 8-2.8B) — segmented tabs between Report #1 (สถานะแปลง,
 * default) and Report #2 (ผลผลิตตามรอบปลูก). The default tab must preserve the
 * existing Plot Status behavior; switching reveals the Cycle Yield report.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { ReportsPage } from './ReportsPage';

const plotStatusMock = vi.fn();
const cycleYieldMock = vi.fn();

vi.mock('../../../api/reports', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../../api/reports')>();
  return {
    ...actual,
    listPlotStatus: (...a: unknown[]) => plotStatusMock(...a),
    downloadPlotStatusReport: vi.fn(),
    listCycleYieldReport: (...a: unknown[]) => cycleYieldMock(...a),
    downloadCycleYieldReport: vi.fn(),
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
vi.mock('../../../hooks/useHasPermission', () => ({ useHasPermission: () => true }));

function renderPage() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter><ReportsPage /></MemoryRouter>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  plotStatusMock.mockReset().mockResolvedValue([]);
  cycleYieldMock.mockReset().mockResolvedValue([]);
});

describe('ReportsPage — tabs', () => {
  it('defaults to the Plot Status tab (existing behavior preserved)', async () => {
    renderPage();
    // Report #1's heading is present by default.
    expect(await screen.findByRole('heading', { name: 'รายงานสถานะแปลง' })).toBeTruthy();
    // Report #2's heading is not shown until its tab is selected.
    expect(screen.queryByRole('heading', { name: 'รายงานผลผลิตตามรอบปลูก' })).toBeNull();
  });

  it('switching to "ผลผลิตตามรอบปลูก" shows the Cycle Yield report', async () => {
    renderPage();
    await screen.findByRole('heading', { name: 'รายงานสถานะแปลง' });

    fireEvent.click(screen.getByRole('tab', { name: 'ผลผลิตตามรอบปลูก' }));

    expect(await screen.findByRole('heading', { name: 'รายงานผลผลิตตามรอบปลูก' })).toBeTruthy();
    expect(screen.queryByRole('heading', { name: 'รายงานสถานะแปลง' })).toBeNull();
  });

  it('both tabs are rendered as tablist buttons', () => {
    renderPage();
    expect(screen.getByRole('tab', { name: 'สถานะแปลงปัจจุบัน' })).toBeTruthy();
    expect(screen.getByRole('tab', { name: 'ผลผลิตตามรอบปลูก' })).toBeTruthy();
  });
});
