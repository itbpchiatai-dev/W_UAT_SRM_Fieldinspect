/**
 * ExecutiveDashboard (rounds 50-53).
 *
 * This is a page a manager acts on, so the tests are about the readings
 * being honest. The rules, each one a failure that looks fine on screen:
 *
 *   - an estimate and a result are never averaged into one number
 *   - a dash, never 0%, when there is nothing to compare against
 *   - the cleaned figure always says how much is unfilled
 *   - every number says which clock it is on
 *   - the actionable reasons overlap, so they are listed, never summed
 *   - a plot nobody ever visited outranks every other kind of problem
 *   - one failing block does not blank the page
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor, within } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import {
  ExecutiveDashboard, Pager, periodLabel, formatKg, achievementTone, reasonValue, concernText,
} from './ExecutiveDashboard';
import type {
  ConcernRow, ExecutiveBreakdown, ExecutiveConcerns, ExecutiveFollowUp,
  ExecutiveSummary, ExecutiveTrend, ProductionStatus,
} from '../../api/dashboard';

vi.mock('../../api/suppliers', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../api/suppliers')>();
  return {
    ...actual,
    listSuppliers: () => Promise.resolve([
      { id: 'sup-5', code: 'SUP005', name: 'Supplier Five', isActive: true,
        contactName: null, contactEmail: null },
    ]),
  };
});

vi.mock('../../api/masterdata', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../api/masterdata')>();
  return {
    ...actual,
    listMasterData: () => Promise.resolve([
      { id: 'm1', type: 'p_code', value: 'WM-141', parent: 'แตงโมพันธุ์ก',
        orderIndex: 1, active: true, createdAt: '', updatedAt: '' },
    ]),
  };
});

const summaryMock = vi.fn();
const productionMock = vi.fn();
const concernsMock = vi.fn();
const trendMock = vi.fn();
const breakdownMock = vi.fn();
const followUpMock = vi.fn();
const downloadMock = vi.fn();
const downloadConcernsMock = vi.fn();
const downloadFollowUpMock = vi.fn();

vi.mock('../../api/dashboard', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../api/dashboard')>();
  return {
    ...actual,
    getExecutiveSummary: (...a: unknown[]) => summaryMock(...a),
    getProductionStatus: (...a: unknown[]) => productionMock(...a),
    getConcerns: (...a: unknown[]) => concernsMock(...a),
    getExecutiveTrend: (...a: unknown[]) => trendMock(...a),
    getExecutiveBreakdown: (...a: unknown[]) => breakdownMock(...a),
    getExecutiveFollowUp: (...a: unknown[]) => followUpMock(...a),
    downloadExecutiveBreakdown: (...a: unknown[]) => downloadMock(...a),
    downloadExecutiveConcerns: (...a: unknown[]) => downloadConcernsMock(...a),
    downloadExecutiveFollowUp: (...a: unknown[]) => downloadFollowUpMock(...a),
  };
});

function summary(o: Partial<ExecutiveSummary> = {}): ExecutiveSummary {
  return {
    plotsTotal: 763, plotsInspected: 122, plotsNeverInspected: 641,
    plotsOverdue: 60, overdueDays: 30,
    recordsTotal: 191, recordsLast7Days: 10, recordsLast30Days: 44,
    avgConditionScore: 6.7, scoredRecordCount: 159, watchPlotCount: 43,
    targetKgTotal: '162425.00', harvestKgTotal: '150584.00', achievementPct: 92.7,
    cleanedKgTotal: '28672.00', cleanedRecordCount: 11, cleanedMissingCount: 91,
    cleanedRetentionPct: 87.6, supplierTotal: 20, range: '3m',
    actionablePlots: 103, actionableOverdue: 60, actionableLowScore: 43,
    actionableLowYield: 7,
    ...o,
  };
}

function production(o: Partial<ProductionStatus> = {}): ProductionStatus {
  return {
    inProduction: {
      plots: 91, targetKg: '130500.00', valueKg: '117844.00', pct: 90.3,
    },
    finished: {
      plots: 11, targetKg: '31925.00', valueKg: '28672.00', pct: 89.8,
    },
    finishedHarvestKg: '32740.00',
    finishedRetentionPct: 87.6,
    byStage: [
      { stage: 'ระยะงอก', plots: 11 },
      { stage: 'ติดผล', plots: 25 },
      { stage: 'เก็บเกี่ยว', plots: 25 },
    ],
    range: '3m',
    ...o,
  };
}

function concerns(rows: ConcernRow[] = [
  { severity: 'high', kind: 'in_production_low', key: 'WM-141', plots: 4, value: 59.3 },
  { severity: 'high', kind: 'finished_low', key: 'WM-141', plots: 1, value: 49.2 },
  { severity: 'medium', kind: 'missing_cleaned', key: '(ไม่ระบุ)', plots: 3, value: null },
]): ExecutiveConcerns {
  return { thresholdPct: 70, total: rows.length, rows };
}

function trend(o: Partial<ExecutiveTrend> = {}): ExecutiveTrend {
  return {
    granularity: 'month', range: '3m',
    points: [
      { period: '2026-08-01', inspections: 55, avgScore: 7.7 },
      { period: '2026-09-01', inspections: 47, avgScore: 6.7 },
      { period: '2026-10-01', inspections: 21, avgScore: 7.0 },
    ],
    ...o,
  };
}

function breakdown(o: Partial<ExecutiveBreakdown> = {}): ExecutiveBreakdown {
  return {
    groupBy: 'p_code', range: '3m',
    rows: [
      { key: 'WM-141', label: 'WM-141', plots: 6, plotsInspected: 6, plotsOverdue: 2,
        inspections: 20, avgScore: 5.2,
        estimatePlots: 5, estimateTargetKg: '1000.00', estimateKg: '604.00',
        estimatePct: 60.4,
        actualPlots: 1, actualTargetKg: '500.00', actualKg: '284.00',
        actualPct: 56.9 },
      { key: 'QA-Product-G', label: 'QA-Product-G', plots: 1, plotsInspected: 1,
        plotsOverdue: 0, inspections: 3, avgScore: 7.9,
        estimatePlots: 1, estimateTargetKg: '500.00', estimateKg: '472.00',
        estimatePct: 94.4,
        actualPlots: 0, actualTargetKg: null, actualKg: null, actualPct: null },
    ],
    ...o,
  };
}

function followUp(o: Partial<ExecutiveFollowUp> = {}): ExecutiveFollowUp {
  return {
    total: 65,
    rows: [
      { plotId: 'p0', plotCode: 'SUP001-P002', plotName: 'แปลงศูนย์',
        supplierCode: 'SUP001', reason: 'never_inspected', value: 105,
        lastInspected: null },
      { plotId: 'p1', plotCode: 'SUP005-P012', plotName: 'แปลงหนึ่ง',
        supplierCode: 'SUP005', reason: 'low_score', value: 1,
        lastInspected: '2026-10-02' },
      { plotId: 'p2', plotCode: 'SUP002-P003', plotName: 'แปลงสอง',
        supplierCode: 'SUP002', reason: 'overdue', value: 98,
        lastInspected: '2026-07-09' },
    ],
    ...o,
  };
}

function renderPage() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter><ExecutiveDashboard /></MemoryRouter>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  for (const m of [summaryMock, productionMock, concernsMock, trendMock,
                   breakdownMock, followUpMock, downloadMock,
                   downloadConcernsMock, downloadFollowUpMock]) m.mockReset();
  summaryMock.mockResolvedValue(summary());
  productionMock.mockResolvedValue(production());
  concernsMock.mockResolvedValue(concerns());
  trendMock.mockResolvedValue(trend());
  breakdownMock.mockResolvedValue(breakdown());
  followUpMock.mockResolvedValue(followUp());
  downloadMock.mockResolvedValue(new Blob(['x']));
  downloadConcernsMock.mockResolvedValue(new Blob(['x']));
  downloadFollowUpMock.mockResolvedValue(new Blob(['x']));
});

// --- pure helpers --------------------------------------------------------

describe('helpers', () => {
  it('labels a month bucket with the month and a week bucket with the day', () => {
    expect(periodLabel('2026-10-01', 'month')).toBe('ต.ค.');
    expect(periodLabel('2026-10-06', 'week')).toBe('6 ต.ค.');
  });

  it('falls back to the raw value rather than printing Invalid Date', () => {
    expect(periodLabel('not-a-date', 'month')).toBe('not-a-date');
  });

  it('rounds and groups kg, and returns null when absent', () => {
    expect(formatKg('162425.00')).toBe('162,425 kg');
    expect(formatKg(null)).toBeNull();
  });

  it('gives an absent achievement a neutral tone, not a bad one', () => {
    expect(achievementTone(null)).toBe('blue');
    expect(achievementTone(96)).toBe('green');
    expect(achievementTone(80)).toBe('orange');
    expect(achievementTone(52)).toBe('red');
  });

  it('formats a follow-up value according to its reason', () => {
    const base = { plotId: 'p', plotCode: 'c', plotName: 'n', supplierCode: 's',
                   lastInspected: null } as const;
    expect(reasonValue({ ...base, reason: 'never_inspected', value: 105 })).toBe('105 วันแล้ว');
    expect(reasonValue({ ...base, reason: 'overdue', value: 98 })).toBe('98 วัน');
    expect(reasonValue({ ...base, reason: 'low_score', value: 2 })).toBe('2 / 10');
    expect(reasonValue({ ...base, reason: 'low_yield', value: 41 })).toBe('41%');
  });

  it('writes each concern kind as its own sentence', () => {
    const row = (kind: ConcernRow['kind'], value: number | null): ConcernRow =>
      ({ severity: 'high', kind, key: 'WM-141', plots: 4, value });
    expect(concernText(row('in_production_low', 59.3), 70)).toContain('กำลังผลิต 4 แปลงต่ำกว่า 70%');
    expect(concernText(row('finished_low', 49.2), 70)).toContain('ผลิตจบแล้ว 4 แปลงได้ไม่ถึง 70%');
    expect(concernText(row('missing_cleaned', null), 70)).toContain('ยังไม่กรอกผลผลิตหลังทำความสะอาด');
    expect(concernText(row('low_retention', 71), 70)).toContain('อัตราคงเหลือหลังคัดเพียง 71%');
  });

  it('builds the concern caption from the SERVER threshold', () => {
    // A caption hardcoding 70 would lie the moment the backend constant moves.
    const row: ConcernRow = {
      severity: 'high', kind: 'in_production_low', key: 'X', plots: 1, value: 50,
    };
    expect(concernText(row, 65)).toContain('65%');
  });
});

// --- row 1 ---------------------------------------------------------------

describe('summary row', () => {
  it('leads with how big the thing being looked at is', async () => {
    renderPage();
    expect(await screen.findByText('20 ราย')).toBeTruthy();
    expect(screen.getByText(/763 แปลง/)).toBeTruthy();
  });

  it('shows the two lenses as SEPARATE cards, never averaged', async () => {
    // The whole point of rounds 50-53: an estimate and a result are
    // different kinds of number. The same two percentages appear again in
    // the production block below — tile for the glance, block for the
    // detail — so this is scoped to the summary.
    renderPage();
    const row = await screen.findByRole('region', { name: 'ตัวเลขสรุป' });
    // The region renders before its data arrives, so wait on the content.
    expect(await within(row).findByText('90.3%')).toBeTruthy();
    expect(within(row).getByText('89.8%')).toBeTruthy();
    expect(within(row).getByText('กำลังผลิต (คาดการณ์)')).toBeTruthy();
    expect(within(row).getByText('ผลิตจบแล้ว (จริง)')).toBeTruthy();
  });

  it('says which clock each number is on', async () => {
    renderPage();
    await screen.findByText('20 ราย');
    expect(screen.getAllByText(/ณ ตอนนี้/).length).toBeGreaterThan(1);
    expect(screen.getAllByText(/ช่วงที่เลือก/).length).toBeGreaterThan(1);
  });

  it('lists the three actionable reasons instead of summing them', async () => {
    // 60 + 43 + 7 = 110, but the distinct total is 103.
    renderPage();
    expect(await screen.findByText('103')).toBeTruthy();
    expect(screen.getByText(/ค้าง 60 · คะแนนต่ำ 43 · ผลผลิตต่ำ 7/)).toBeTruthy();
    expect(screen.queryByText('110')).toBeNull();
  });

  it('shows a dash for a lens with nothing to compare', async () => {
    productionMock.mockResolvedValue(production({
      finished: { plots: 0, targetKg: null, valueKg: null, pct: null },
    }));
    renderPage();
    const row = await screen.findByRole('region', { name: 'ตัวเลขสรุป' });
    await waitFor(() => expect(within(row).getByText('90.3%')).toBeTruthy());
    // A finished lens with no plan is a dash, never a nought — "0% of
    // plan" reads as a failed season.
    expect(within(row).queryByText('0.0%')).toBeNull();
  });
});

// --- the production block ------------------------------------------------

describe('production block', () => {
  it('states what each lens is measured from', async () => {
    renderPage();
    expect(await screen.findByText(/วัดจาก Yield ของการตรวจครั้งล่าสุด/)).toBeTruthy();
    expect(screen.getByText(/วัดจากผลผลิตหลังทำความสะอาดของบันทึกสุดท้าย/)).toBeTruthy();
  });

  it('shows the plot count behind each lens', async () => {
    renderPage();
    const block = await screen.findByRole('region', { name: 'สถานะการผลิต' });
    expect(await within(block).findByText('91 แปลง')).toBeTruthy();
    expect(within(block).getByText('11 แปลง')).toBeTruthy();
  });

  it('reports retention against the finished plots own harvest', async () => {
    renderPage();
    expect(await screen.findByText('87.6%')).toBeTruthy();
    expect(screen.getByText(/หลังคัด ÷ เก็บได้ 32,740 kg ของแปลงชุดเดียวกัน/)).toBeTruthy();
  });

  it('says nobody has filled it in rather than showing a retention of zero', async () => {
    productionMock.mockResolvedValue(production({
      finished: { plots: 0, targetKg: null, valueKg: null, pct: null },
      finishedHarvestKg: null, finishedRetentionPct: null,
    }));
    renderPage();
    expect(await screen.findByText(/ยังไม่มีแปลงที่กรอกผลผลิตหลังทำความสะอาด/)).toBeTruthy();
  });

  it('draws the season so the estimate can be read in context', async () => {
    // 90% with most plots at ระยะงอก is a guess; at เก็บเกี่ยว it is
    // nearly a result.
    renderPage();
    expect(await screen.findByText(/ระยะงอก 11/)).toBeTruthy();
    expect(screen.getByText(/เก็บเกี่ยว 25/)).toBeTruthy();
  });
});

// --- concerns ------------------------------------------------------------

describe('concern strip', () => {
  it('writes each row as something to act on', async () => {
    renderPage();
    expect(await screen.findByText(/WM-141 — กำลังผลิต 4 แปลงต่ำกว่า 70%/)).toBeTruthy();
    expect(screen.getByText(/WM-141 — ผลิตจบแล้ว 1 แปลงได้ไม่ถึง 70%/)).toBeTruthy();
  });

  it('marks severity for a reader who cannot see the colour', async () => {
    renderPage();
    await screen.findByText(/กำลังผลิต 4 แปลง/);
    expect(screen.getAllByLabelText('รุนแรง').length).toBe(2);
    expect(screen.getAllByLabelText('เฝ้าระวัง').length).toBe(1);
  });

  it('says so plainly when there is nothing to worry about', async () => {
    concernsMock.mockResolvedValue(concerns([]));
    renderPage();
    expect(await screen.findByText(/ไม่พบสัญญาณที่ต้องกังวล/)).toBeTruthy();
  });

  it('starts on the threshold the server defaults to', async () => {
    renderPage();
    const box = await screen.findByLabelText('เกณฑ์แจ้งเตือน (% ของเป้า)');
    expect((box as HTMLInputElement).value).toBe('70');
    await waitFor(() => expect(concernsMock).toHaveBeenCalledWith(
      expect.anything(), 70, 10, 0,
    ));
  });

  it('re-asks with a threshold the reader types', async () => {
    renderPage();
    const box = await screen.findByLabelText('เกณฑ์แจ้งเตือน (% ของเป้า)');
    fireEvent.change(box, { target: { value: '85' } });
    await waitFor(() => expect(concernsMock).toHaveBeenCalledWith(
      expect.anything(), 85, 10, 0,
    ), { timeout: 2000 });
  });

  it('waits for typing to settle instead of firing per keystroke', async () => {
    renderPage();
    const box = await screen.findByLabelText('เกณฑ์แจ้งเตือน (% ของเป้า)');
    const before = concernsMock.mock.calls.length;
    fireEvent.change(box, { target: { value: '8' } });
    fireEvent.change(box, { target: { value: '85' } });
    // "8" on the way to "85" must never reach the server — it would flag
    // almost nothing and the list would flicker empty.
    await waitFor(() => expect(concernsMock).toHaveBeenCalledWith(
      expect.anything(), 85, 10, 0,
    ), { timeout: 2000 });
    const asked = concernsMock.mock.calls.slice(before).map((c) => c[1]);
    expect(asked).not.toContain(8);
  });

  it('treats an emptied box as the default, never as zero', async () => {
    // A threshold of 0 would report that nothing is wrong anywhere.
    renderPage();
    const box = await screen.findByLabelText('เกณฑ์แจ้งเตือน (% ของเป้า)');
    fireEvent.change(box, { target: { value: '' } });
    await waitFor(() => {
      const asked = concernsMock.mock.calls.map((c) => c[1]);
      expect(asked).not.toContain(0);
      expect(asked).toContain(70);
    }, { timeout: 2000 });
  });

  it('captions the rows with the threshold the SERVER used', async () => {
    // The server clamps; the caption must follow its answer and not the
    // number still sitting in the box.
    concernsMock.mockResolvedValue({
      thresholdPct: 200,
      total: 1,
      rows: [{ severity: 'high', kind: 'in_production_low', key: 'WM-141',
               plots: 4, value: 59.3 }],
    });
    renderPage();
    expect(await screen.findByText(/ต่ำกว่า 200%/)).toBeTruthy();
  });

  it('does not refetch the other blocks when only the threshold moves', async () => {
    renderPage();
    const box = await screen.findByLabelText('เกณฑ์แจ้งเตือน (% ของเป้า)');
    await waitFor(() => expect(summaryMock).toHaveBeenCalled());
    const before = summaryMock.mock.calls.length;
    fireEvent.change(box, { target: { value: '90' } });
    await waitFor(() => expect(concernsMock).toHaveBeenCalledWith(
      expect.anything(), 90, 10, 0,
    ), { timeout: 2000 });
    expect(summaryMock.mock.calls.length).toBe(before);
  });
});

// --- comparison: chart and table together --------------------------------

describe('comparison block', () => {
  it('offers P.Code first, because that is what the page is read for', async () => {
    renderPage();
    const group = await screen.findByRole('group', { name: 'เทียบตาม' });
    const labels = Array.from(group.querySelectorAll('button')).map((b) => b.textContent);
    expect(labels).toEqual(['P.Code', 'Supplier', 'พืช']);
  });

  it('no longer offers พันธุ์ — P.Code already answers it', async () => {
    // On UAT all 494 active P.Codes map 1:1 to a variety and all 763 plots
    // carry both, so the two groupings produce identical rows.
    renderPage();
    const group = await screen.findByRole('group', { name: 'เทียบตาม' });
    expect(within(group).queryByRole('button', { name: 'พันธุ์' })).toBeNull();
  });

  it('still names พันธุ์ in Thai if the server answers with it', async () => {
    // An old bookmark or a future caller can still ask for it; the header
    // must not fall back to the raw key.
    breakdownMock.mockResolvedValue(breakdown({ groupBy: 'variety' }));
    renderPage();
    const table = await screen.findByRole('table', { name: 'เทียบผลงาน' });
    expect(within(table).getByRole('columnheader', { name: 'พันธุ์' })).toBeTruthy();
  });

  it('starts grouped by P.Code', async () => {
    renderPage();
    await waitFor(() => expect(breakdownMock).toHaveBeenCalledWith(
      expect.anything(), 'p_code',
    ));
  });

  it('draws the chart and the table from the same rows', async () => {
    renderPage();
    const table = await screen.findByRole('table', { name: 'เทียบผลงาน' });
    expect(within(table).getByText('WM-141')).toBeTruthy();
    // the chart repeats the label, so there is more than one on the page
    expect(screen.getAllByText('WM-141').length).toBeGreaterThan(1);
  });

  it('gives each lens its own column plus the gap between them', async () => {
    renderPage();
    const table = await screen.findByRole('table', { name: 'เทียบผลงาน' });
    for (const h of ['กำลังผลิต', 'ผลิตจบ', 'ส่วนต่าง']) {
      expect(within(table).getByRole('columnheader', { name: h })).toBeTruthy();
    }
    expect(within(table).getByText('60.4%')).toBeTruthy();
    expect(within(table).getByText('56.9%')).toBeTruthy();
    expect(within(table).getByText('▼3.5')).toBeTruthy();
  });

  it('says "ยังไม่จบ" rather than inventing a gap', async () => {
    renderPage();
    const table = await screen.findByRole('table', { name: 'เทียบผลงาน' });
    expect(within(table).getByText('ยังไม่จบ')).toBeTruthy();
  });

  it('labels the header from the SERVER answer, not from what was asked', async () => {
    breakdownMock.mockResolvedValue(breakdown({ groupBy: 'supplier' }));
    renderPage();
    const table = await screen.findByRole('table', { name: 'เทียบผลงาน' });
    expect(within(table).getByRole('columnheader', { name: 'Supplier' })).toBeTruthy();
    expect(await screen.findByText(/ยังไม่รองรับการเทียบตาม/)).toBeTruthy();
  });

  it('stays quiet when the server did honour the grouping', async () => {
    renderPage();
    await screen.findByRole('table', { name: 'เทียบผลงาน' });
    expect(screen.queryByText(/ยังไม่รองรับการเทียบตาม/)).toBeNull();
  });
});

// --- the worklist --------------------------------------------------------

describe('follow-up list', () => {
  it('puts a plot nobody has ever visited at the top', async () => {
    renderPage();
    const table = await screen.findByRole('table', { name: 'แปลงที่ต้องตามงาน' });
    const firstRow = within(table).getAllByRole('row')[1];
    expect(within(firstRow).getByText('ไม่เคยตรวจ')).toBeTruthy();
    expect(within(firstRow).getByText('105 วันแล้ว')).toBeTruthy();
  });

  it('shows no visit date for a plot that has none', async () => {
    renderPage();
    const table = await screen.findByRole('table', { name: 'แปลงที่ต้องตามงาน' });
    expect(within(table).getByText('ยังไม่เคยตรวจ')).toBeTruthy();
  });

  it('says how many more there are beyond the ten shown', async () => {
    renderPage();
    expect(await screen.findByText(/แสดง 1–3 จาก 65 แปลง/)).toBeTruthy();
  });

  it('names the two things it still cannot show', async () => {
    renderPage();
    expect(await screen.findByText(/หมายเหตุและผู้รับผิดชอบ/)).toBeTruthy();
  });
});

// --- filters -------------------------------------------------------------

describe('filters', () => {
  it('starts on the window the backend defaults to', async () => {
    renderPage();
    await waitFor(() => expect(summaryMock).toHaveBeenCalledWith(
      expect.objectContaining({ range: '3m', supplierId: null, pCode: null, cycleStatus: null }),
    ));
  });

  it('folds the three narrowing filters behind one button', async () => {
    renderPage();
    await screen.findByText('20 ราย');
    expect(screen.getByRole('button', { name: 'ตัวกรอง' })).toBeTruthy();
    // and they are not loose in the header
    expect(screen.queryByRole('button', { name: /กรอง Supplier/ })).toBeNull();
  });

  it('narrows EVERY block when a cycle status is picked', async () => {
    // A page that filters five blocks out of six is worse than one that
    // ignores the filter: it looks like it worked.
    renderPage();
    await screen.findByText('20 ราย');
    fireEvent.click(screen.getByRole('button', { name: 'ตัวกรอง' }));
    fireEvent.click(screen.getByRole('button', { name: /กรองสถานะรอบปลูก/ }));
    fireEvent.click(await screen.findByRole('option', { name: 'เก็บเกี่ยวแล้ว' }));
    await waitFor(() => {
      const f = expect.objectContaining({ cycleStatus: 'harvested' });
      expect(summaryMock).toHaveBeenCalledWith(f);
      expect(productionMock).toHaveBeenCalledWith(f);
      // getConcerns also carries its own threshold, which the scope
      // filters must not disturb.
      expect(concernsMock).toHaveBeenCalledWith(f, 70, 10, 0);
      expect(trendMock).toHaveBeenCalledWith(f);
      expect(breakdownMock).toHaveBeenCalledWith(f, 'p_code');
      expect(followUpMock).toHaveBeenCalledWith(f, 10, 0);
    });
  });

  it('offers 6 เดือน alongside the other presets', async () => {
    renderPage();
    await screen.findByText('20 ราย');
    fireEvent.click(screen.getByRole('button', { name: '6 เดือน' }));
    await waitFor(() => expect(summaryMock).toHaveBeenCalledWith(
      expect.objectContaining({ range: '6m' }),
    ));
  });

  it('refetches the period-scoped blocks when a preset is picked', async () => {
    renderPage();
    await screen.findByText('20 ราย');
    fireEvent.click(screen.getByRole('button', { name: 'ปีนี้' }));
    await waitFor(() => {
      const f = expect.objectContaining({ range: 'ytd' });
      expect(summaryMock).toHaveBeenCalledWith(f);
      expect(productionMock).toHaveBeenCalledWith(f);
    });
  });

  it('never refetches the worklist for a period change', async () => {
    renderPage();
    await screen.findByText('20 ราย');
    const before = followUpMock.mock.calls.length;
    fireEvent.click(screen.getByRole('button', { name: '30 วัน' }));
    await waitFor(() => expect(summaryMock).toHaveBeenCalledWith(
      expect.objectContaining({ range: '30d' }),
    ));
    expect(followUpMock.mock.calls.length).toBe(before);
  });
});

// --- export --------------------------------------------------------------

describe('Excel export', () => {
  it('downloads with the same filters and grouping the table is showing', async () => {
    renderPage();
    await screen.findByRole('table', { name: 'เทียบผลงาน' });
    fireEvent.click(screen.getByRole('button', { name: 'พืช' }));
    await waitFor(() => expect(breakdownMock).toHaveBeenCalledWith(
      expect.anything(), 'crop',
    ));
    fireEvent.click(screen.getByRole('button', { name: 'Export Excel เทียบผลงาน' }));
    await waitFor(() => expect(downloadMock).toHaveBeenCalledWith(
      expect.anything(), 'crop',
    ));
  });

  it('says so when the download fails instead of failing silently', async () => {
    downloadMock.mockRejectedValue(new Error('boom'));
    renderPage();
    await screen.findByRole('table', { name: 'เทียบผลงาน' });
    fireEvent.click(screen.getByRole('button', { name: 'Export Excel เทียบผลงาน' }));
    expect(await screen.findByText(/ดาวน์โหลดไม่สำเร็จ/)).toBeTruthy();
  });
});

// --- failure isolation ---------------------------------------------------

describe('one block failing does not blank the page', () => {
  it('keeps the rest when the production call fails', async () => {
    productionMock.mockRejectedValue(new Error('boom'));
    renderPage();
    expect(await screen.findByText(/ไม่สามารถโหลดสถานะการผลิต/)).toBeTruthy();
    expect(screen.getByText('20 ราย')).toBeTruthy();
    expect(screen.getByRole('table', { name: 'เทียบผลงาน' })).toBeTruthy();
  });

  it('keeps the rest when the concerns call fails', async () => {
    concernsMock.mockRejectedValue(new Error('boom'));
    renderPage();
    expect(await screen.findByText(/ไม่สามารถโหลดสัญญาณเตือน/)).toBeTruthy();
    expect(screen.getByText('20 ราย')).toBeTruthy();
  });

  it('keeps the rest when the summary fails', async () => {
    summaryMock.mockRejectedValue(new Error('boom'));
    renderPage();
    expect(await screen.findByText(/ไม่สามารถโหลดตัวเลขสรุป/)).toBeTruthy();
    expect(screen.getByRole('table', { name: 'แปลงที่ต้องตามงาน' })).toBeTruthy();
  });

  it('no longer renders the map — it was cut from this page', async () => {
    renderPage();
    await screen.findByText('20 ราย');
    expect(screen.queryByText(/แผนที่/)).toBeNull();
  });
});

// --- round 56: paging ----------------------------------------------------

async function nav(block: string) {
  return screen.findByRole('navigation', { name: `เปลี่ยนหน้า ${block}` });
}

describe('Pager', () => {
  function renderPager(page: number, total: number) {
    const onChange = vi.fn();
    render(<Pager page={page} total={total} label="x" onChange={onChange} />);
    return onChange;
  }

  it('draws nothing when everything fits on one page', () => {
    renderPager(0, 10);
    expect(screen.queryByRole('navigation')).toBeNull();
  });

  it('appears at eleven rows', () => {
    renderPager(0, 11);
    expect(screen.getByText('หน้า 1 / 2')).toBeTruthy();
  });

  it('cannot go back from the first page', () => {
    renderPager(0, 65);
    expect((screen.getByRole('button', { name: '← ก่อนหน้า' }) as HTMLButtonElement).disabled).toBe(true);
    expect((screen.getByRole('button', { name: 'ถัดไป →' }) as HTMLButtonElement).disabled).toBe(false);
  });

  it('cannot go forward from the last page', () => {
    renderPager(6, 65);
    expect(screen.getByText('หน้า 7 / 7')).toBeTruthy();
    expect((screen.getByRole('button', { name: 'ถัดไป →' }) as HTMLButtonElement).disabled).toBe(true);
  });

  it('reports the page it moves to', () => {
    const onChange = renderPager(2, 65);
    fireEvent.click(screen.getByRole('button', { name: 'ถัดไป →' }));
    fireEvent.click(screen.getByRole('button', { name: '← ก่อนหน้า' }));
    expect(onChange.mock.calls).toEqual([[3], [1]]);
  });
});

describe('paging the two lists (round 56)', () => {
  it('asks for the first ten of each list', async () => {
    renderPage();
    await waitFor(() => {
      expect(concernsMock).toHaveBeenCalledWith(expect.anything(), 70, 10, 0);
      expect(followUpMock).toHaveBeenCalledWith(expect.anything(), 10, 0);
    });
  });

  it('pages the worklist when it is longer than ten', async () => {
    renderPage();
    expect(within(await nav('แปลงที่ต้องตามงาน')).getByText('หน้า 1 / 7')).toBeTruthy();
  });

  it('shows no pager when the concerns fit on one page', async () => {
    renderPage();
    await screen.findByText(/กำลังผลิต 4 แปลง/);
    expect(screen.queryByRole('navigation', { name: 'เปลี่ยนหน้า สิ่งที่ต้องกังวล' })).toBeNull();
  });

  it('asks the server for the next ten on ถัดไป', async () => {
    renderPage();
    fireEvent.click(within(await nav('แปลงที่ต้องตามงาน')).getByRole('button', { name: 'ถัดไป →' }));
    await waitFor(() => expect(followUpMock).toHaveBeenCalledWith(expect.anything(), 10, 10));
    expect(await screen.findByText('หน้า 2 / 7')).toBeTruthy();
  });

  it('counts the footer from where the page starts', async () => {
    renderPage();
    fireEvent.click(within(await nav('แปลงที่ต้องตามงาน')).getByRole('button', { name: 'ถัดไป →' }));
    expect(await screen.findByText(/แสดง 11–13 จาก 65 แปลง/)).toBeTruthy();
  });

  it('pages the concerns too', async () => {
    concernsMock.mockResolvedValue({ ...concerns(), total: 23 });
    renderPage();
    const pager = await nav('สิ่งที่ต้องกังวล');
    expect(within(pager).getByText('หน้า 1 / 3')).toBeTruthy();
    fireEvent.click(within(pager).getByRole('button', { name: 'ถัดไป →' }));
    await waitFor(() => expect(concernsMock).toHaveBeenCalledWith(expect.anything(), 70, 10, 10));
  });

  it('goes back to page one when a filter changes what is listed', async () => {
    // Page 4 of the OLD list is meaningless for the new one — and may not
    // exist at all.
    renderPage();
    fireEvent.click(within(await nav('แปลงที่ต้องตามงาน')).getByRole('button', { name: 'ถัดไป →' }));
    await waitFor(() => expect(followUpMock).toHaveBeenCalledWith(expect.anything(), 10, 10));
    fireEvent.click(screen.getByRole('button', { name: 'ตัวกรอง' }));
    fireEvent.click(screen.getByRole('button', { name: /กรองสถานะรอบปลูก/ }));
    fireEvent.click(await screen.findByRole('option', { name: 'เก็บเกี่ยวแล้ว' }));
    await waitFor(() => expect(followUpMock).toHaveBeenLastCalledWith(
      expect.objectContaining({ cycleStatus: 'harvested' }), 10, 0,
    ));
  });

  it('never asks the new filter for the old page first', async () => {
    // The page is derived during render; resetting it in an effect would
    // fire one request for page 2 of the new filter before correcting.
    renderPage();
    fireEvent.click(within(await nav('แปลงที่ต้องตามงาน')).getByRole('button', { name: 'ถัดไป →' }));
    await waitFor(() => expect(followUpMock).toHaveBeenCalledWith(expect.anything(), 10, 10));
    fireEvent.click(screen.getByRole('button', { name: 'ตัวกรอง' }));
    fireEvent.click(screen.getByRole('button', { name: /กรองสถานะรอบปลูก/ }));
    fireEvent.click(await screen.findByRole('option', { name: 'เก็บเกี่ยวแล้ว' }));
    await waitFor(() => expect(followUpMock).toHaveBeenLastCalledWith(
      expect.objectContaining({ cycleStatus: 'harvested' }), 10, 0,
    ));
    const harvested = followUpMock.mock.calls.filter(
      (c) => (c[0] as { cycleStatus: string | null }).cycleStatus === 'harvested');
    expect(harvested.map((c) => c[2])).toEqual([0]);
  });

  it('goes back to page one of the concerns when the threshold changes', async () => {
    concernsMock.mockResolvedValue({ ...concerns(), total: 23 });
    renderPage();
    fireEvent.click(within(await nav('สิ่งที่ต้องกังวล')).getByRole('button', { name: 'ถัดไป →' }));
    await waitFor(() => expect(concernsMock).toHaveBeenCalledWith(expect.anything(), 70, 10, 10));
    fireEvent.change(screen.getByLabelText('เกณฑ์แจ้งเตือน (% ของเป้า)'), { target: { value: '85' } });
    await waitFor(() => expect(concernsMock).toHaveBeenLastCalledWith(
      expect.anything(), 85, 10, 0,
    ), { timeout: 2000 });
  });

  it('keeps the worklist page when only the period changes', async () => {
    // The worklist is about today; the period tabs do not touch it.
    renderPage();
    fireEvent.click(within(await nav('แปลงที่ต้องตามงาน')).getByRole('button', { name: 'ถัดไป →' }));
    expect(await screen.findByText('หน้า 2 / 7')).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: '30 วัน' }));
    await waitFor(() => expect(summaryMock).toHaveBeenCalledWith(
      expect.objectContaining({ range: '30d' }),
    ));
    expect(screen.getByText('หน้า 2 / 7')).toBeTruthy();
  });

  it('does not refetch the other blocks when a list changes page', async () => {
    renderPage();
    const pager = await nav('แปลงที่ต้องตามงาน');
    await waitFor(() => expect(summaryMock).toHaveBeenCalled());
    const before = [summaryMock, productionMock, concernsMock, trendMock, breakdownMock]
      .map((m) => m.mock.calls.length);
    fireEvent.click(within(pager).getByRole('button', { name: 'ถัดไป →' }));
    await waitFor(() => expect(followUpMock).toHaveBeenCalledWith(expect.anything(), 10, 10));
    expect([summaryMock, productionMock, concernsMock, trendMock, breakdownMock]
      .map((m) => m.mock.calls.length)).toEqual(before);
  });

  it('says a page came back empty instead of claiming nothing is wrong', async () => {
    // Rows can vanish between two clicks; "ไม่พบสัญญาณ" on page 3 of 23
    // would be a false all-clear.
    concernsMock.mockResolvedValue({ thresholdPct: 70, total: 23, rows: [] });
    renderPage();
    expect(await screen.findByText('ไม่มีรายการในหน้านี้')).toBeTruthy();
    expect(screen.queryByText(/ไม่พบสัญญาณที่ต้องกังวล/)).toBeNull();
    expect(await nav('สิ่งที่ต้องกังวล')).toBeTruthy();
  });
});

// --- round 56: the two list exports --------------------------------------

describe('list exports (round 56)', () => {
  it('gives each block its own, distinguishable button', async () => {
    renderPage();
    await screen.findByRole('table', { name: 'แปลงที่ต้องตามงาน' });
    for (const block of ['สิ่งที่ต้องกังวล', 'เทียบผลงาน', 'แปลงที่ต้องตามงาน']) {
      expect(screen.getByRole('button', { name: `Export Excel ${block}` })).toBeTruthy();
    }
  });

  it('downloads the concerns with the filters and the typed threshold', async () => {
    renderPage();
    await screen.findByText(/กำลังผลิต 4 แปลง/);
    fireEvent.change(screen.getByLabelText('เกณฑ์แจ้งเตือน (% ของเป้า)'), { target: { value: '85' } });
    await waitFor(() => expect(concernsMock).toHaveBeenCalledWith(
      expect.anything(), 85, 10, 0,
    ), { timeout: 2000 });
    fireEvent.click(screen.getByRole('button', { name: 'Export Excel สิ่งที่ต้องกังวล' }));
    await waitFor(() => expect(downloadConcernsMock).toHaveBeenCalledWith(
      expect.objectContaining({ range: '3m' }), 85,
    ));
  });

  it('downloads the whole worklist, not the page on screen', async () => {
    renderPage();
    fireEvent.click(within(await nav('แปลงที่ต้องตามงาน')).getByRole('button', { name: 'ถัดไป →' }));
    await waitFor(() => expect(followUpMock).toHaveBeenCalledWith(expect.anything(), 10, 10));
    fireEvent.click(screen.getByRole('button', { name: 'Export Excel แปลงที่ต้องตามงาน' }));
    await waitFor(() => expect(downloadFollowUpMock).toHaveBeenCalledTimes(1));
    // filters only — no limit, no offset
    expect(downloadFollowUpMock.mock.calls[0]).toHaveLength(1);
  });

  it('cannot export an empty list', async () => {
    followUpMock.mockResolvedValue(followUp({ total: 0, rows: [] }));
    concernsMock.mockResolvedValue(concerns([]));
    renderPage();
    await screen.findByText(/ไม่มีแปลงที่ต้องตามงาน/);
    for (const block of ['สิ่งที่ต้องกังวล', 'แปลงที่ต้องตามงาน']) {
      const btn = screen.getByRole('button', { name: `Export Excel ${block}` }) as HTMLButtonElement;
      expect(btn.disabled).toBe(true);
    }
  });

  it('reports a failed download inside the block it came from', async () => {
    downloadFollowUpMock.mockRejectedValue(new Error('boom'));
    renderPage();
    const region = await screen.findByRole('region', { name: 'แปลงที่ต้องตามงาน' });
    await within(region).findByRole('table');
    fireEvent.click(within(region).getByRole('button', { name: 'Export Excel แปลงที่ต้องตามงาน' }));
    expect(await within(region).findByText(/ดาวน์โหลดไม่สำเร็จ/)).toBeTruthy();
    const concernsRegion = screen.getByRole('region', { name: 'สิ่งที่ต้องกังวล' });
    expect(within(concernsRegion).queryByText(/ดาวน์โหลดไม่สำเร็จ/)).toBeNull();
  });
});
