import { apiClient } from './client';

export interface CropTypeStat {
  cropType: string | null;
  count: number;
}

export interface DashboardSummary {
  totalRecords: number;
  recordsThisMonth: number;
  avgConditionScore: number | null;
  lowScoreCount: number;
  totalPlots: number;
  totalSuppliers: number | null;
  byCropType: CropTypeStat[];
}

export async function getDashboardSummary(): Promise<DashboardSummary> {
  const { data } = await apiClient.get<DashboardSummary>('/api/v1/dashboard/summary');
  return data;
}

/**
 * Round 43 — the executive overview's payload. Mirrors
 * backend/app/schemas/dashboard.py::ExecutiveSummary field for field.
 *
 * The kg totals arrive as STRINGS: they are NUMERIC on the server and
 * FastAPI serialises Decimal as a string rather than risk a float rounding
 * a tonne figure. Parse at the point of display, never store them as
 * numbers here.
 */
export interface ExecutiveSummary {
  plotsTotal: number;
  plotsInspected: number;
  plotsNeverInspected: number;
  plotsOverdue: number;
  /** The overdue window itself, so a card's label can never claim a
   *  different number of days than the query used. */
  overdueDays: number;
  recordsTotal: number;
  recordsLast7Days: number;
  recordsLast30Days: number;
  avgConditionScore: number | null;
  scoredRecordCount: number;
  watchPlotCount: number;
  targetKgTotal: string | null;
  harvestKgTotal: string | null;
  achievementPct: number | null;
  cleanedKgTotal: string | null;
  cleanedRecordCount: number;
  cleanedMissingCount: number;
  /** cleaned / harvest OF THE SAME RECORDS — not of the whole window.
   *  See the backend's retention_pct for why that distinction matters. */
  cleanedRetentionPct: number | null;
  supplierTotal: number;
  /** Echo of the window these range-scoped fields were computed over. */
  range: string;
  /** DISTINCT plots needing attention. The three reasons OVERLAP and do not
   *  sum to this — show the total and list the reasons, never add them. */
  actionablePlots: number;
  actionableOverdue: number;
  actionableLowScore: number;
  actionableLowYield: number;
}

export async function getExecutiveSummary(f: ExecutiveFilters): Promise<ExecutiveSummary> {
  const { data } = await apiClient.get<ExecutiveSummary>(
    '/api/v1/dashboard/executive', { params: executiveParams(f) },
  );
  return data;
}

/** Round 44/45 — the three blocks the overview is assembled from.
 *  Kept as separate calls because the range tabs refetch the trend and the
 *  comparison table while the follow-up list never changes with the range. */
export type ExecutiveRangeKey = '30d' | '3m' | 'ytd' | 'custom';
export type BreakdownGroup = 'supplier' | 'crop' | 'variety' | 'p_code';

/** Round 47 — what the page is filtered to. One object because every
 *  executive call takes the same set, and a filter applied to three blocks
 *  out of four is worse than none: the page looks like it worked. */
export interface ExecutiveFilters {
  range: ExecutiveRangeKey;
  /** ISO dates. Sent only for a custom window; they override `range`
   *  server-side, which is why 'custom' itself is not a valid range key
   *  there — it falls back to the default if the dates are missing. */
  dateFrom: string | null;
  dateTo: string | null;
  supplierId: string | null;
  /** Round 49 — P.Code is what production strains are analysed by here, so
   *  it narrows the page the same way a supplier does. It lives on the
   *  planting cycle, not the plot, which is why the server reads it from
   *  the plot's ACTIVE cycle and from each record's OWN cycle. */
  pCode: string | null;
}

export const DEFAULT_EXECUTIVE_FILTERS: ExecutiveFilters = {
  range: '3m', dateFrom: null, dateTo: null, supplierId: null, pCode: null,
};

/** camelCase -> the snake_case the API declares. Omit (not null) for an
 *  unset value so the endpoint keeps its own default — same contract as
 *  api/suppliers.ts's listSuppliers, and the same class of bug if skipped. */
export function executiveParams(f: ExecutiveFilters): Record<string, unknown> {
  return {
    range: f.range,
    date_from: f.dateFrom || undefined,
    date_to: f.dateTo || undefined,
    supplier_id: f.supplierId || undefined,
    p_code: f.pCode || undefined,
  };
}

export interface TrendPoint {
  /** First day of the bucket, ISO date. */
  period: string;
  inspections: number;
  avgScore: number | null;
}

export interface ExecutiveTrend {
  /** 'week' | 'month' — the axis labels depend on it, so it travels with
   *  the points rather than being guessed from their count. */
  granularity: string;
  range: string;
  points: TrendPoint[];
}

export interface BreakdownRow {
  key: string;
  label: string;
  plots: number;
  plotsInspected: number;
  plotsOverdue: number;
  inspections: number;
  avgScore: number | null;
  targetKg: string | null;
  harvestKg: string | null;
  achievementPct: number | null;
}

export interface ExecutiveBreakdown {
  groupBy: string;
  range: string;
  rows: BreakdownRow[];
}

export type FollowUpReason = 'low_score' | 'overdue' | 'low_yield';

export interface FollowUpRow {
  plotId: string;
  plotCode: string;
  plotName: string;
  supplierCode: string | null;
  reason: FollowUpReason;
  /** Days overdue, lowest score, or achievement % — read it with `reason`. */
  value: number;
  lastInspected: string | null;
}

export interface ExecutiveFollowUp {
  /** Distinct plots flagged BEFORE the limit, so the page can say
   *  "ดูทั้งหมด N" without a second call. */
  total: number;
  rows: FollowUpRow[];
}

export async function getExecutiveTrend(f: ExecutiveFilters): Promise<ExecutiveTrend> {
  const { data } = await apiClient.get<ExecutiveTrend>(
    '/api/v1/dashboard/executive/trend', { params: executiveParams(f) },
  );
  return data;
}

export async function getExecutiveBreakdown(
  f: ExecutiveFilters, by: BreakdownGroup,
): Promise<ExecutiveBreakdown> {
  const { data } = await apiClient.get<ExecutiveBreakdown>(
    '/api/v1/dashboard/executive/breakdown', { params: { ...executiveParams(f), by } },
  );
  return data;
}

/** The same rows the table shows, as .xlsx. Takes the identical filters so
 *  the download can never disagree with what was on screen. */
export async function downloadExecutiveBreakdown(
  f: ExecutiveFilters, by: BreakdownGroup,
): Promise<Blob> {
  const res = await apiClient.get<Blob>(
    '/api/v1/dashboard/executive/breakdown/export',
    { params: { ...executiveParams(f), by }, responseType: 'blob' },
  );
  return res.data;
}

/** Not period-filtered — "who should I visit" is a question about today —
 *  but it IS supplier-filtered, like everything else on the page. */
export async function getExecutiveFollowUp(
  f: ExecutiveFilters, limit = 10,
): Promise<ExecutiveFollowUp> {
  // Takes the whole filter object but sends only the non-period parts: the
  // list is about today, and a window would hide the plot nobody has
  // visited in four months.
  const { range: _r, date_from: _f, date_to: _t, ...scope } = executiveParams(f);
  const { data } = await apiClient.get<ExecutiveFollowUp>(
    '/api/v1/dashboard/executive/follow-up', { params: { ...scope, limit } },
  );
  return data;
}
