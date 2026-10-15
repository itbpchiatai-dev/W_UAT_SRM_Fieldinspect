/**
 * ExecutiveDashboard (rebuilt, round 46).
 *
 * This is a page a manager acts on, so the tests are about the readings
 * being honest rather than about the markup. The rules it must never break:
 *
 *   - a dash, never 0%, when there is nothing to compare against
 *   - the cleaned-yield figure always says how much is unfilled
 *   - every number says which clock it is on (ณ ตอนนี้ / ช่วงที่เลือก)
 *   - the three "ต้องจัดการ" reasons are listed, never summed — they overlap
 *   - one failing block does not blank the page
 *
 * All five are failures that look perfectly fine on screen.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor, within } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { ExecutiveDashboard, periodLabel, formatKg, achievementTone, reasonValue } from './ExecutiveDashboard';
import type {
  ExecutiveSummary, ExecutiveTrend, ExecutiveBreakdown, ExecutiveFollowUp,
} from '../../api/dashboard';

const summaryMock = vi.fn();
const trendMock = vi.fn();
const breakdownMock = vi.fn();
const followUpMock = vi.fn();

vi.mock('../../api/suppliers', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../api/suppliers')>();
  return {
    ...actual,
    listSuppliers: () => Promise.resolve([
      { id: 'sup-5', code: 'SUP005', name: 'Supplier Five', isActive: true,
        contactName: null, contactEmail: null },
      { id: 'sup-10', code: 'SUP010', name: 'Supplier Ten', isActive: true,
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
      { id: 'm2', type: 'p_code', value: 'CTT-507', parent: null,
        orderIndex: 2, active: true, createdAt: '', updatedAt: '' },
    ]),
  };
});

const downloadMock = vi.fn();

vi.mock('../../api/dashboard', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../api/dashboard')>();
  return {
    ...actual,
    getExecutiveSummary: (...a: unknown[]) => summaryMock(...a),
    getExecutiveTrend: (...a: unknown[]) => trendMock(...a),
    getExecutiveBreakdown: (...a: unknown[]) => breakdownMock(...a),
    getExecutiveFollowUp: (...a: unknown[]) => followUpMock(...a),
    downloadExecutiveBreakdown: (...a: unknown[]) => downloadMock(...a),
  };
});

function summary(o: Partial<ExecutiveSummary> = {}): ExecutiveSummary {
  return {
    plotsTotal: 763, plotsInspected: 122, plotsNeverInspected: 641,
    plotsOverdue: 60, overdueDays: 30,
    recordsTotal: 191, recordsLast7Days: 10, recordsLast30Days: 44,
    avgConditionScore: 6.7, scoredRecordCount: 159, watchPlotCount: 43,
    targetKgTotal: '411525.00', harvestKgTotal: '318737.34', achievementPct: 77.5,
    cleanedKgTotal: '28712.22', cleanedRecordCount: 12, cleanedMissingCount: 293,
    cleanedRetentionPct: 87.5, supplierTotal: 20, range: '3m',
    actionablePlots: 103, actionableOverdue: 60, actionableLowScore: 43,
    actionableLowYield: 7,
    ...o,
  };
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
    groupBy: 'supplier', range: '3m',
    rows: [
      { key: 'SUP005', label: 'Supplier Five', plots: 80, plotsInspected: 12,
        plotsOverdue: 41, inspections: 30, avgScore: 5.2,
        targetKg: '1000.00', harvestKg: '520.00', achievementPct: 52.0 },
      { key: 'SUP010', label: 'Supplier Ten', plots: 48, plotsInspected: 44,
        plotsOverdue: 2, inspections: 90, avgScore: 8.1,
        targetKg: '1000.00', harvestKg: '1250.00', achievementPct: 125.0 },
    ],
    ...o,
  };
}

function followUp(o: Partial<ExecutiveFollowUp> = {}): ExecutiveFollowUp {
  return {
    total: 65,
    rows: [
      { plotId: 'p1', plotCode: 'SUP005-P012', plotName: 'แปลงหนึ่ง',
        supplierCode: 'SUP005', reason: 'low_score', value: 1, lastInspected: '2026-10-02' },
      { plotId: 'p2', plotCode: 'SUP002-P003', plotName: 'แปลงสอง',
        supplierCode: 'SUP002', reason: 'overdue', value: 98, lastInspected: '2026-07-09' },
      { plotId: 'p3', plotCode: 'SUP009-P044', plotName: 'แปลงสาม',
        supplierCode: 'SUP009', reason: 'low_yield', value: 41, lastInspected: '2026-09-28' },
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
  for (const m of [summaryMock, trendMock, breakdownMock, followUpMock, downloadMock]) {
    m.mockReset();
  }
  downloadMock.mockResolvedValue(new Blob(['x']));
  summaryMock.mockResolvedValue(summary());
  trendMock.mockResolvedValue(trend());
  breakdownMock.mockResolvedValue(breakdown());
  followUpMock.mockResolvedValue(followUp());
});

// --- pure helpers --------------------------------------------------------

describe('helpers', () => {
  it('labels a month bucket with the month alone', () => {
    expect(periodLabel('2026-10-01', 'month')).toBe('ต.ค.');
  });

  it('labels a week bucket with the day too', () => {
    // Four weeks of October all read "ต.ค." without the day.
    expect(periodLabel('2026-10-06', 'week')).toBe('6 ต.ค.');
  });

  it('falls back to the raw value rather than printing Invalid Date', () => {
    expect(periodLabel('not-a-date', 'month')).toBe('not-a-date');
  });

  it('rounds kg and groups the digits', () => {
    expect(formatKg('411525.00')).toBe('411,525 kg');
  });

  it('returns null for an absent kg figure so the caller can show a dash', () => {
    expect(formatKg(null)).toBeNull();
    expect(formatKg('')).toBeNull();
  });

  it('gives an absent achievement a neutral tone, not a bad one', () => {
    expect(achievementTone(null)).toBe('blue');
    expect(achievementTone(96)).toBe('green');
    expect(achievementTone(80)).toBe('orange');
    expect(achievementTone(52)).toBe('red');
  });

  it('formats the follow-up value according to its reason', () => {
    const base = { plotId: 'p', plotCode: 'c', plotName: 'n', supplierCode: 's',
                   lastInspected: null } as const;
    expect(reasonValue({ ...base, reason: 'overdue', value: 98 })).toBe('98 วัน');
    expect(reasonValue({ ...base, reason: 'low_score', value: 2 })).toBe('2 / 10');
    expect(reasonValue({ ...base, reason: 'low_yield', value: 41 })).toBe('41%');
  });
});

// --- row 1 ---------------------------------------------------------------

describe('summary row', () => {
  it('leads with how big the thing being looked at is', async () => {
    // v1 left this out entirely; a manager needs the scale before the rates.
    renderPage();
    expect(await screen.findByText('20 ราย')).toBeTruthy();
    expect(screen.getByText(/763 แปลง/)).toBeTruthy();
  });

  it('says which clock each number is on', async () => {
    renderPage();
    await screen.findByText('20 ราย');
    expect(screen.getAllByText(/ณ ตอนนี้/).length).toBeGreaterThan(1);
    expect(screen.getAllByText(/ช่วงที่เลือก/).length).toBeGreaterThan(1);
  });

  it('lists the three actionable reasons instead of summing them', async () => {
    // 60 + 43 + 7 = 110, but the distinct total is 103: one plot can be
    // overdue AND low-scoring. Printing a sum would be wrong.
    renderPage();
    expect(await screen.findByText('103')).toBeTruthy();
    expect(screen.getByText(/ค้าง 60 · คะแนนต่ำ 43 · ผลผลิตต่ำ 7/)).toBeTruthy();
    expect(screen.queryByText('110')).toBeNull();
  });

  it('shows a dash, not 0%, when no plan was recorded', async () => {
    summaryMock.mockResolvedValue(summary({ achievementPct: null, targetKgTotal: null }));
    renderPage();
    expect(await screen.findByText('ยังไม่มีเป้าผลิตบันทึกไว้')).toBeTruthy();
    expect(screen.queryByText('0.0%')).toBeNull();
  });
});

// --- row 2 ---------------------------------------------------------------

describe('trend chart', () => {
  it('draws a bar per bucket with Thai month labels', async () => {
    renderPage();
    expect(await screen.findByText('ส.ค.')).toBeTruthy();
    expect(screen.getByText('ก.ย.')).toBeTruthy();
    expect(screen.getByText('ต.ค.')).toBeTruthy();
  });

  it('explains itself instead of drawing one bar when there is too little data', async () => {
    // What UAT will show today: 27 days of records is not a trend.
    trendMock.mockResolvedValue(trend({
      points: [{ period: '2026-10-01', inspections: 21, avgScore: 7.0 }],
    }));
    renderPage();
    expect(await screen.findByText(/ข้อมูลยังไม่พอวาดแนวโน้ม/)).toBeTruthy();
  });
});

// --- row 3 ---------------------------------------------------------------

describe('yield funnel', () => {
  it('shows the three stages with their own bases', async () => {
    renderPage();
    expect(await screen.findByText('411,525 kg')).toBeTruthy();
    expect(screen.getByText('318,737 kg')).toBeTruthy();
    expect(screen.getByText('28,712 kg')).toBeTruthy();
    expect(screen.getByText('77.5% ของเป้า')).toBeTruthy();
    // The one v1 dropped — a DIFFERENT base from the line above it.
    expect(screen.getByText('87.5% ของที่เก็บได้')).toBeTruthy();
  });

  it('always says how many records still have no cleaned weight', async () => {
    renderPage();
    expect(await screen.findByText(/ยังไม่กรอกอีก 293/)).toBeTruthy();
  });

  it('says nobody filled it in when the cleaned figure is entirely empty', async () => {
    summaryMock.mockResolvedValue(summary({
      cleanedKgTotal: null, cleanedRecordCount: 0, cleanedRetentionPct: null,
      cleanedMissingCount: 191,
    }));
    renderPage();
    expect(await screen.findByText(/ยังไม่มีใครกรอก/)).toBeTruthy();
  });
});

// --- row 4 ---------------------------------------------------------------

describe('comparison table', () => {
  it('lists each group with its counts and achievement', async () => {
    renderPage();
    expect(await screen.findByText('Supplier Five')).toBeTruthy();
    expect(screen.getByText('Supplier Ten')).toBeTruthy();
    expect(screen.getByText('52.0%')).toBeTruthy();
    expect(screen.getByText('125.0%')).toBeTruthy();
  });

  it('refetches with the new grouping when the switch is used', async () => {
    renderPage();
    await screen.findByText('Supplier Five');
    fireEvent.click(screen.getByRole('button', { name: 'พันธุ์' }));
    await waitFor(() => {
      expect(breakdownMock).toHaveBeenCalledWith(
        expect.objectContaining({ range: '3m' }), 'variety',
      );
    });
  });

  it('changes the first column header with the grouping', async () => {
    breakdownMock.mockResolvedValue(breakdown({ groupBy: 'crop' }));
    renderPage();
    await screen.findByText('Supplier Five');
    fireEvent.click(screen.getByRole('button', { name: 'พืช' }));
    const table = await screen.findByRole('table', { name: 'เทียบผลงาน' });
    expect(within(table).getByRole('columnheader', { name: 'พืช' })).toBeTruthy();
  });

  it('labels the header from the SERVER answer, not from what was asked', async () => {
    // A backend too old to know a grouping answers with a different one
    // (normalize_group_by falls back to supplier). Reading the header off
    // local state is what made that silent: the tab said P.Code and the
    // rows were suppliers, with nothing to explain it.
    breakdownMock.mockResolvedValue(breakdown({ groupBy: 'supplier' }));
    renderPage();
    await screen.findByText('Supplier Five');
    fireEvent.click(screen.getByRole('button', { name: 'P.Code' }));
    // Scoped to the comparison table: the follow-up list below also has a
    // Supplier column, which is why both tables carry a name.
    const table = await screen.findByRole('table', { name: 'เทียบผลงาน' });
    expect(within(table).getByRole('columnheader', { name: 'Supplier' })).toBeTruthy();
  });

  it('says so when the server could not honour the grouping', async () => {
    breakdownMock.mockResolvedValue(breakdown({ groupBy: 'supplier' }));
    renderPage();
    await screen.findByText('Supplier Five');
    fireEvent.click(screen.getByRole('button', { name: 'P.Code' }));
    expect(await screen.findByText(/ยังไม่รองรับการเทียบตาม/)).toBeTruthy();
  });

  it('stays quiet when the server did honour it', async () => {
    breakdownMock.mockResolvedValue(breakdown({ groupBy: 'variety' }));
    renderPage();
    await screen.findByText('Supplier Five');
    fireEvent.click(screen.getByRole('button', { name: 'พันธุ์' }));
    await waitFor(() => expect(breakdownMock).toHaveBeenCalledWith(
      expect.anything(), 'variety',
    ));
    expect(screen.queryByText(/ยังไม่รองรับการเทียบตาม/)).toBeNull();
  });
});

// --- row 5 ---------------------------------------------------------------

describe('follow-up list', () => {
  it('shows the reason and a value formatted for that reason', async () => {
    renderPage();
    expect(await screen.findByText('SUP005-P012')).toBeTruthy();
    expect(screen.getByText('คะแนนต่ำ')).toBeTruthy();
    expect(screen.getByText('1 / 10')).toBeTruthy();
    expect(screen.getByText('98 วัน')).toBeTruthy();
    expect(screen.getByText('41%')).toBeTruthy();
  });

  it('says how many more there are beyond the ten shown', async () => {
    renderPage();
    expect(await screen.findByText(/แสดง 3 จาก 65 แปลง/)).toBeTruthy();
  });

  it('names the two things it still cannot show, rather than hiding them', async () => {
    renderPage();
    expect(await screen.findByText(/หมายเหตุและผู้รับผิดชอบ/)).toBeTruthy();
  });

  it('congratulates nobody when the list is empty — it states the fact', async () => {
    followUpMock.mockResolvedValue(followUp({ total: 0, rows: [] }));
    renderPage();
    expect(await screen.findByText(/ไม่มีแปลงที่ต้องตามงาน/)).toBeTruthy();
  });
});

// --- period, supplier, export ------------------------------------------

const FILTERS_3M = {
  range: '3m', dateFrom: null, dateTo: null, supplierId: null, pCode: null,
};

describe('period filter', () => {
  it('starts on the window the backend defaults to', async () => {
    renderPage();
    await waitFor(() => expect(summaryMock).toHaveBeenCalledWith(FILTERS_3M));
  });

  it('no longer offers the unbounded range', async () => {
    // Round 47 removed it on both sides, or on neither.
    renderPage();
    await screen.findByText('20 ราย');
    expect(screen.queryByRole('button', { name: 'ทั้งหมด' })).toBeNull();
  });

  it('refetches the period-scoped blocks when a preset is picked', async () => {
    renderPage();
    await screen.findByText('20 ราย');
    fireEvent.click(screen.getByRole('button', { name: 'ปีนี้' }));
    await waitFor(() => {
      const f = {
        range: 'ytd', dateFrom: null, dateTo: null, supplierId: null, pCode: null,
      };
      expect(summaryMock).toHaveBeenCalledWith(f);
      expect(trendMock).toHaveBeenCalledWith(f);
      expect(breakdownMock).toHaveBeenCalledWith(f, 'supplier');
    });
  });

  it('queries the typed window when a custom date is entered', async () => {
    renderPage();
    await screen.findByText('20 ราย');
    fireEvent.click(screen.getByRole('button', { name: 'กำหนดเอง' }));
    fireEvent.change(screen.getByLabelText('ตั้งแต่วันที่'), { target: { value: '2026-06-01' } });
    await waitFor(() => {
      expect(summaryMock).toHaveBeenCalledWith(
        expect.objectContaining({ range: 'custom', dateFrom: '2026-06-01' }),
      );
    });
  });

  it('never refetches the follow-up list for a period change', async () => {
    // A window would hide the plot nobody has visited in four months.
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

/** The pickers are SearchableSelect comboboxes: click the trigger, then
 *  click an option. A plain <select> could not be typed into, which is
 *  unusable against the ~500 P.Codes UAT carries. */
async function pick(trigger: string, option: RegExp | string) {
  // "กรอง Supplier", not "Supplier": the เทียบตาม tabs carry the bare names,
  // and two buttons with one accessible name is a real screen-reader bug
  // before it is a test problem.
  fireEvent.click(await screen.findByRole('button', { name: new RegExp(trigger) }));
  fireEvent.click(await screen.findByRole('option', { name: option }));
}

describe('supplier filter', () => {
  it('can be searched by typing, not just scrolled', async () => {
    renderPage();
    await screen.findByText('20 ราย');
    fireEvent.click(await screen.findByRole('button', { name: /กรอง Supplier/ }));
    const box = await screen.findByRole('textbox');
    fireEvent.change(box, { target: { value: 'SUP010' } });
    expect(await screen.findByRole('option', { name: /SUP010/ })).toBeTruthy();
    expect(screen.queryByRole('option', { name: /SUP005/ })).toBeNull();
  });

  it('narrows EVERY block, including the follow-up list', async () => {
    // A page that filters three blocks out of four is worse than one that
    // ignores the filter: it looks like it worked.
    renderPage();
    await screen.findByText('20 ราย');
    await pick('กรอง Supplier', /SUP005/);
    await waitFor(() => {
      const f = expect.objectContaining({ supplierId: 'sup-5' });
      expect(summaryMock).toHaveBeenCalledWith(f);
      expect(trendMock).toHaveBeenCalledWith(f);
      expect(breakdownMock).toHaveBeenCalledWith(f, 'supplier');
      expect(followUpMock).toHaveBeenCalledWith(f, 10);
    });
  });

  it('goes back to every supplier when the clear option is chosen', async () => {
    renderPage();
    await screen.findByText('20 ราย');
    await pick('กรอง Supplier', /SUP005/);
    await waitFor(() => expect(summaryMock).toHaveBeenCalledWith(
      expect.objectContaining({ supplierId: 'sup-5' }),
    ));
    await pick('กรอง Supplier', /ทุก Supplier/);
    await waitFor(() => expect(summaryMock).toHaveBeenCalledWith(
      expect.objectContaining({ supplierId: null }),
    ));
  });
});

describe('P.Code filter', () => {
  it('offers the active P.Codes with their variety', async () => {
    renderPage();
    await screen.findByText('20 ราย');
    fireEvent.click(await screen.findByRole('button', { name: /กรอง P\.Code/ }));
    expect(await screen.findByRole('option', { name: /WM-141 · แตงโมพันธุ์ก/ })).toBeTruthy();
    // A P.Code with no variety recorded still has to be pickable.
    expect(await screen.findByRole('option', { name: /CTT-507/ })).toBeTruthy();
  });

  it('narrows every block the same way the supplier does', async () => {
    renderPage();
    await screen.findByText('20 ราย');
    await pick('กรอง P\\.Code', /WM-141/);
    await waitFor(() => {
      const f = expect.objectContaining({ pCode: 'WM-141' });
      expect(summaryMock).toHaveBeenCalledWith(f);
      expect(trendMock).toHaveBeenCalledWith(f);
      expect(breakdownMock).toHaveBeenCalledWith(f, 'supplier');
      expect(followUpMock).toHaveBeenCalledWith(f, 10);
    });
  });

  it('can also be the thing the table is grouped by', async () => {
    // "คนดูจะดู P.Code เป็นหลักในการวิเคราะห์สายพันธุ์การผลิต"
    renderPage();
    await screen.findByText('Supplier Five');
    fireEvent.click(screen.getByRole('button', { name: 'P.Code' }));
    await waitFor(() => expect(breakdownMock).toHaveBeenCalledWith(
      expect.anything(), 'p_code',
    ));
  });
});

describe('Excel export', () => {
  it('downloads with the SAME filters the table is showing', async () => {
    // The file must never disagree with what was on screen when it was
    // clicked.
    renderPage();
    await screen.findByText('Supplier Five');
    fireEvent.click(screen.getByRole('button', { name: 'ปีนี้' }));
    await waitFor(() => expect(breakdownMock).toHaveBeenCalledWith(
      expect.objectContaining({ range: 'ytd' }), 'supplier',
    ));
    fireEvent.click(screen.getByRole('button', { name: /Export Excel/ }));
    await waitFor(() => expect(downloadMock).toHaveBeenCalledWith(
      expect.objectContaining({ range: 'ytd' }), 'supplier',
    ));
  });

  it('exports the grouping currently selected', async () => {
    renderPage();
    await screen.findByText('Supplier Five');
    fireEvent.click(screen.getByRole('button', { name: 'พันธุ์' }));
    await waitFor(() => expect(breakdownMock).toHaveBeenCalledWith(
      expect.anything(), 'variety',
    ));
    fireEvent.click(screen.getByRole('button', { name: /Export Excel/ }));
    await waitFor(() => expect(downloadMock).toHaveBeenCalledWith(
      expect.anything(), 'variety',
    ));
  });

  it('is disabled when the table has no rows to export', async () => {
    breakdownMock.mockResolvedValue(breakdown({ rows: [] }));
    renderPage();
    await screen.findByText(/ไม่มีข้อมูลสำหรับการเทียบนี้/);
    expect((screen.getByRole('button', { name: /Export Excel/ }) as HTMLButtonElement).disabled)
      .toBe(true);
  });

  it('says so when the download fails instead of failing silently', async () => {
    downloadMock.mockRejectedValue(new Error('boom'));
    renderPage();
    await screen.findByText('Supplier Five');
    fireEvent.click(screen.getByRole('button', { name: /Export Excel/ }));
    expect(await screen.findByText(/ดาวน์โหลดไม่สำเร็จ/)).toBeTruthy();
  });
});

// --- failure isolation ---------------------------------------------------

describe('one block failing does not blank the page', () => {
  it('keeps the other blocks when the trend call fails', async () => {
    trendMock.mockRejectedValue(new Error('boom'));
    renderPage();
    expect(await screen.findByText(/ไม่สามารถโหลดกราฟแนวโน้ม/)).toBeTruthy();
    expect(screen.getByText('20 ราย')).toBeTruthy();
    expect(screen.getByText('Supplier Five')).toBeTruthy();
  });

  it('keeps the other blocks when the follow-up call fails', async () => {
    followUpMock.mockRejectedValue(new Error('boom'));
    renderPage();
    expect(await screen.findByText(/ไม่สามารถโหลดรายการแปลงที่ต้องตามงาน/)).toBeTruthy();
    expect(screen.getByText('20 ราย')).toBeTruthy();
  });

  it('keeps the chart and the list when the summary fails', async () => {
    summaryMock.mockRejectedValue(new Error('boom'));
    renderPage();
    expect(await screen.findByText(/ไม่สามารถโหลดตัวเลขสรุป/)).toBeTruthy();
    expect(screen.getByText('SUP005-P012')).toBeTruthy();
  });

  it('no longer renders the map — it was cut from this page', async () => {
    renderPage();
    await screen.findByText('20 ราย');
    expect(screen.queryByText(/แผนที่/)).toBeNull();
  });
});
