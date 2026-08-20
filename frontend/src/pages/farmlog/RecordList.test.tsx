/**
 * RecordList — round 5.4 guard: the score column is a single neutral
 * "คะแนนตรวจ" showing all 4 scores as bare numbers, never fixed per-slot
 * labels (which would mislead across a mixed growth-stage list, since a
 * record's protocol remaps what each slot means).
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { RecordList } from './RecordList';
import type { RecordSummary } from '../../api/records';

const listRecordsMock = vi.fn();
const listSuppliersMock = vi.fn();
const listPlotsMock = vi.fn();

vi.mock('../../api/records', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../api/records')>();
  return { ...actual, listRecords: (...a: unknown[]) => listRecordsMock(...a) };
});
vi.mock('../../api/suppliers', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../api/suppliers')>();
  return { ...actual, listSuppliers: (...a: unknown[]) => listSuppliersMock(...a) };
});
vi.mock('../../api/plots', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../api/plots')>();
  return { ...actual, listPlots: (...a: unknown[]) => listPlotsMock(...a) };
});
vi.mock('../../hooks/useHasPermission', () => ({ useHasPermission: () => true }));

function record(overrides: Partial<RecordSummary> = {}): RecordSummary {
  return {
    id: 'rec-1', plotId: 'plot-1', plotCycleId: 'cycle-1', cycleNo: 1, cycleLabel: null,
    supplierId: 'sup-1', recordedById: 'u-1',
    submittedByCode: 'FIELD01', submittedByName: null, recordDate: '2026-07-01',
    crop: 'พริก', variety: null, growthStage: 'เจริญเติบโต', yieldPct: '95.5',
    yieldQuantityKg: null, yieldTargetKgSnapshot: null,
    fieldPrepScore: 8, weatherScore: 7, careScore: 9, varietyResistanceScore: 6,
    isActive: true, createdAt: '2026-07-01T10:00:00Z',
    plotCode: 'SUP001-P001', plotName: 'แปลงทดสอบ', supplierName: 'Supplier One',
    ...overrides,
  };
}

function renderList() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter><RecordList /></MemoryRouter>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  listRecordsMock.mockReset();
  listSuppliersMock.mockReset();
  listPlotsMock.mockReset();
  listSuppliersMock.mockResolvedValue([]);
  listPlotsMock.mockResolvedValue([]);
});

describe('RecordList — neutral score column (round 5.4)', () => {
  it('uses a single neutral "คะแนนตรวจ" header, not fixed per-slot labels', async () => {
    listRecordsMock.mockResolvedValue([record()]);
    renderList();

    expect(await screen.findByText('คะแนนตรวจ (4 หัวข้อ)')).toBeTruthy();
    // The old, potentially-mislabelled column headers must be gone.
    expect(screen.queryByRole('columnheader', { name: 'เตรียมแปลง' })).toBeNull();
    expect(screen.queryByRole('columnheader', { name: 'ดูแลรักษา' })).toBeNull();
  });

  it('renders all 4 scores as bare numbers (no criterion labels)', async () => {
    listRecordsMock.mockResolvedValue([record({ fieldPrepScore: 8, weatherScore: 7, careScore: 9, varietyResistanceScore: 6 })]);
    renderList();

    const group = await screen.findByLabelText('คะแนนการตรวจ 4 หัวข้อ');
    for (const n of ['8', '7', '9', '6']) {
      expect(group.textContent).toContain(n);
    }
  });
});

describe('RecordList — cycle badge (round 8.0.5)', () => {
  it('shows the cycleLabel instead of "รอบที่ N" when set', async () => {
    listRecordsMock.mockResolvedValue([record({ cycleNo: 3, cycleLabel: 'jun2026' })]);
    renderList();

    expect(await screen.findByText('jun2026')).toBeTruthy();
    expect(screen.queryByText('รอบที่ 3')).toBeNull();
  });

  it('falls back to "รอบที่ N" when cycleLabel is null', async () => {
    listRecordsMock.mockResolvedValue([record({ cycleNo: 3, cycleLabel: null })]);
    renderList();

    expect(await screen.findByText('รอบที่ 3')).toBeTruthy();
  });

  it('shows no badge (and does not crash) for an old record with neither cycleLabel nor cycleNo', async () => {
    listRecordsMock.mockResolvedValue([record({ plotCycleId: null, cycleNo: null, cycleLabel: null })]);
    renderList();

    expect(await screen.findByText('SUP001-P001')).toBeTruthy();
    expect(screen.queryByText(/รอบที่/)).toBeNull();
  });
});

// Round 8-25D — this page used to be fixed at 30 rows/page with no way to
// see more. Same [100, 200, 500, 'ทั้งหมด'] contract as the Plots admin page.
describe('RecordList — rows-per-page selector (100 / 200 / 500 / ทั้งหมด)', () => {
  function hasListRecordsCallContaining(expected: Record<string, unknown>) {
    return listRecordsMock.mock.calls.some(([params]) => (
      Object.entries(expected).every(([key, value]) => (params as Record<string, unknown>)[key] === value)
    ));
  }

  it('defaults to fetching 100 rows', async () => {
    listRecordsMock.mockResolvedValue([record()]);
    renderList();

    await waitFor(() => expect(hasListRecordsCallContaining({ limit: 100, offset: 0 })).toBe(true));
    const selector = screen.getByLabelText('แสดง') as HTMLSelectElement;
    expect(selector.value).toBe('100');
  });

  it('switches to 500 rows per page when selected', async () => {
    listRecordsMock.mockResolvedValue([record()]);
    renderList();

    await screen.findByText('SUP001-P001');
    fireEvent.change(screen.getByLabelText('แสดง'), { target: { value: '500' } });

    await waitFor(() => expect(hasListRecordsCallContaining({ limit: 500, offset: 0 })).toBe(true));
  });

  it('pages through everything (chunked) when "ทั้งหมด" is selected', async () => {
    const firstChunk = Array.from({ length: 200 }, (_, i) => record({ id: `rec-${i}` }));
    listRecordsMock
      .mockResolvedValueOnce([record()])  // default 100-row load on mount
      .mockResolvedValueOnce(firstChunk)  // "all": chunk 1 (full → keep going)
      .mockResolvedValueOnce([record()]); // "all": chunk 2 (short → stop)

    renderList();
    await screen.findByText('SUP001-P001');

    fireEvent.change(screen.getByLabelText('แสดง'), { target: { value: 'all' } });

    await waitFor(() => expect(hasListRecordsCallContaining({ limit: 200, offset: 200 })).toBe(true), {
      timeout: 15000,
    });
    expect(screen.queryByText('ถัดไป →')).toBeNull();
  }, 20000);
});
