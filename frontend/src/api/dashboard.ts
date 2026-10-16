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
export type ExecutiveRangeKey = '30d' | '3m' | '6m' | 'ytd' | 'custom';
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
  /** Round 50 — a VIEW over the plot's LATEST cycle, never the thing that
   *  decides which production lens a plot falls in. No default: closing a
   *  cycle is a staff action that may never happen. */
  cycleStatus: string | null;
}

export const DEFAULT_EXECUTIVE_FILTERS: ExecutiveFilters = {
  range: '3m', dateFrom: null, dateTo: null,
  supplierId: null, pCode: null, cycleStatus: null,
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
    cycle_status: f.cycleStatus || undefined,
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
  /** Round 53 — one figure per lens, not one blended one. Both divide by
   *  the same target, so the pair is comparable and the gap between them
   *  is the thing worth reading. */
  estimatePlots: number;
  estimateTargetKg: string | null;
  estimateKg: string | null;
  estimatePct: number | null;
  actualPlots: number;
  actualTargetKg: string | null;
  actualKg: string | null;
  actualPct: number | null;
}

export interface ExecutiveBreakdown {
  groupBy: string;
  range: string;
  rows: BreakdownRow[];
}

/** Round 50 added never_inspected and made it the worst reason: an
 *  overdue plot still has data to look at, one nobody has ever visited has
 *  none. Its value is days since the plot was registered. */
export type FollowUpReason =
  | 'never_inspected' | 'low_score' | 'overdue' | 'low_yield';

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
  /** Distinct plots flagged across EVERY page — what the pager counts to. */
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
  f: ExecutiveFilters, limit = 10, offset = 0,
): Promise<ExecutiveFollowUp> {
  // Takes the whole filter object but sends only the non-period parts: the
  // list is about today, and a window would hide the plot nobody has
  // visited in four months.
  const { range: _r, date_from: _f, date_to: _t, ...scope } = executiveParams(f);
  const { data } = await apiClient.get<ExecutiveFollowUp>(
    '/api/v1/dashboard/executive/follow-up', { params: { ...scope, limit, offset } },
  );
  return data;
}

/** Every plot on the worklist, not the page on screen. Same non-period
 *  filters as the list, for the same reason. */
export async function downloadExecutiveFollowUp(f: ExecutiveFilters): Promise<Blob> {
  const { range: _r, date_from: _f, date_to: _t, ...scope } = executiveParams(f);
  const res = await apiClient.get<Blob>(
    '/api/v1/dashboard/executive/follow-up/export',
    { params: scope, responseType: 'blob' },
  );
  return res.data;
}

/** Round 50 — production seen through two lenses, split by each plot's
 *  LAST record: still growing (judged on the yield estimate) or finished
 *  (judged on the cleaned weight, which is what was actually produced). */
export interface LensFigures {
  plots: number;
  targetKg: string | null;
  /** The yield estimate for the growing lens, the cleaned weight for the
   *  finished one — read it together with which lens it came from. */
  valueKg: string | null;
  pct: number | null;
}

export interface StageCount {
  stage: string;
  plots: number;
}

export interface ProductionStatus {
  inProduction: LensFigures;
  finished: LensFigures;
  /** Harvest weight of the finished plots only — retention's matching
   *  base, never the whole window's harvest. */
  finishedHarvestKg: string | null;
  finishedRetentionPct: number | null;
  byStage: StageCount[];
  range: string;
}

export type ConcernKind =
  | 'in_production_low' | 'finished_low' | 'missing_cleaned' | 'low_retention';

export interface ConcernRow {
  severity: 'high' | 'medium';
  kind: ConcernKind;
  /** The P.Code, or the unspecified label. */
  key: string;
  plots: number;
  /** Reading depends on `kind`; unset for missing_cleaned, where the count
   *  is the whole message. */
  value: number | null;
}

export interface ExecutiveConcerns {
  /** Echoed so a caption can never claim a threshold the server did not use. */
  thresholdPct: number;
  /** Every concern found, across all pages. */
  total: number;
  rows: ConcernRow[];
}

export async function getProductionStatus(f: ExecutiveFilters): Promise<ProductionStatus> {
  const { data } = await apiClient.get<ProductionStatus>(
    '/api/v1/dashboard/executive/production', { params: executiveParams(f) },
  );
  return data;
}

/** The default the server falls back to when no threshold is sent. Kept
 *  here so the input can start on the same number the caption would show. */
export const DEFAULT_CONCERN_PCT = 70;

/** `thresholdPct` belongs to this block alone, not to ExecutiveFilters —
 *  putting it there would invalidate the other five queries every time
 *  somebody nudged the number. The server clamps it and echoes back what
 *  it used, which is what the caption reads. */
export async function getConcerns(
  f: ExecutiveFilters, thresholdPct?: number | null, limit?: number, offset?: number,
): Promise<ExecutiveConcerns> {
  const { data } = await apiClient.get<ExecutiveConcerns>(
    '/api/v1/dashboard/executive/concerns',
    { params: {
      ...executiveParams(f), threshold_pct: thresholdPct ?? undefined, limit, offset,
    } },
  );
  return data;
}

/** Every concern for these filters AND this threshold — without the
 *  threshold the file would list different P.Codes from the screen. */
export async function downloadExecutiveConcerns(
  f: ExecutiveFilters, thresholdPct?: number | null,
): Promise<Blob> {
  const res = await apiClient.get<Blob>(
    '/api/v1/dashboard/executive/concerns/export',
    {
      params: { ...executiveParams(f), threshold_pct: thresholdPct ?? undefined },
      responseType: 'blob',
    },
  );
  return res.data;
}
