/**
 * Reports API — read-only aggregate views for the FarmLog "รายงาน" menu.
 *
 * Report #1 "สถานะแปลง" (Plot Status): every active plot with its latest
 * inspection-derived status + yield, plus an Excel export of the same
 * filtered rows. Backed by GET /api/v1/reports/plot-status[/export].
 */
import { apiClient } from './client';

// Decimal-backed fields come back as JSON strings in practice (same as
// plots.ts) — typed as the honest union; route every read through
// lib/numeric.ts rather than assuming a shape.
export interface PlotStatusRow {
  plotId: string;
  supplierCode: string;
  supplierName: string;
  plotCode: string;
  plotName: string;
  province: string | null;
  // Active planting cycle (round 7.4) — activeCycleId null ⇒ no active cycle
  // (show "รอเริ่มรอบปลูก", suppress the yield plan). currentCrop/Variety and
  // the expected-yield fields below are sourced from this cycle, null between
  // cycles.
  activeCycleId: string | null;
  activeCycleNo: number | null;
  activeCycleStatus: 'active' | null;
  currentCrop: string | null;
  currentVariety: string | null;
  currentStage: string | null;
  currentYieldPct: string | number | null;
  expectedYieldFull: string | number | null;
  expectedYieldUnit: string | null;
  plantCount: number | null;
  currentFieldPrepScore: number | null;
  currentWeatherScore: number | null;
  currentCareScore: number | null;
  currentVarietyResistanceScore: number | null;
  lastInspectedAt: string | null;
  lastInspectedByCode: string | null;
  isInspected: boolean;
}

export interface PlotStatusParams {
  supplierId?: string;
  province?: string;
  crop?: string;
  /** '' | 'inspected' | 'not_inspected' — omit/'' means all plots. */
  inspected?: string;
  /** ISO date YYYY-MM-DD — filters on last_inspected_at. */
  dateFrom?: string;
  dateTo?: string;
}

/** camelCase → snake_case for FastAPI query params (see activityLogs.ts). */
function toSnake(params: PlotStatusParams): Record<string, unknown> {
  return {
    supplier_id: params.supplierId || undefined,
    province: params.province || undefined,
    crop: params.crop || undefined,
    inspected: params.inspected || undefined,
    date_from: params.dateFrom || undefined,
    date_to: params.dateTo || undefined,
  };
}

export async function listPlotStatus(params: PlotStatusParams = {}): Promise<PlotStatusRow[]> {
  const res = await apiClient.get<PlotStatusRow[]>('/api/v1/reports/plot-status', {
    params: toSnake(params),
  });
  return res.data;
}

export async function downloadPlotStatusReport(params: PlotStatusParams = {}): Promise<Blob> {
  const res = await apiClient.get<Blob>('/api/v1/reports/plot-status/export', {
    params: toSnake(params),
    responseType: 'blob',
  });
  return res.data;
}

// --- Report #2 "ผลผลิตตามรอบปลูก" (Cycle Yield, round 8-2.8B) ----------------
// One row per PlotCycle with its frozen final ESTIMATED-yield snapshot (round
// 8-2.8A) — read verbatim from the backend, NEVER recomputed on the client.
// finalEstimatedYield is an ESTIMATE, not actual harvested yield.

export interface CycleYieldRow {
  supplierId: string;
  supplierCode: string;
  supplierName: string;
  plotId: string;
  plotCode: string;
  plotName: string;
  province: string | null;
  plotIsActive: boolean;
  cycleId: string;
  cycleNo: number;
  cycleLabel: string | null;
  cycleStatus: 'active' | 'harvested' | 'cancelled';
  crop: string | null;
  variety: string | null;
  // PO / P.Code + lot source (round 8-5B) — this row's OWN cycle values.
  poNumber?: string | null;
  pCode?: string | null;
  lotNo: string | null;
  lotNoSource?: 'auto' | 'manual' | 'legacy' | null;
  // Round 8-12C — the SUPPLIER's own lot identifier for this cycle (round
  // 8-12A). Independent of lotNo — never merged, never the Auto Lot fallback.
  supplierLotNo?: string | null;
  plantingDate: string | null;
  plantCount: number | null;
  // Decimal-backed — may come back as JSON strings (route through lib/numeric).
  expectedYieldFull: string | number | null;
  expectedYieldUnit: string | null;
  startedAt: string;
  closedAt: string | null;
  closeReason: string | null;
  finalYieldPct: string | number | null;
  finalEstimatedYield: string | number | null;
  finalInspectionRecordId: string | null;
  // Round 8-7C.1 — ACTUAL harvested yield (round 8-7A's final_plot Excel
  // action), read verbatim from the backend. Distinct from finalEstimatedYield
  // above (a frozen ESTIMATE from the last inspection at close time): these
  // are the REAL measured figures. All null for an active cycle, a cycle
  // closed by any path OTHER than final_plot, or a legacy cycle closed
  // before this field existed. Never recomputed on the client.
  harvestYield: string | number | null;
  finalYieldAfterClean: string | number | null;
  finalYieldUnit: string | null;
  harvestDate: string | null;
  finalNote: string | null;
}

export interface CycleYieldParams {
  supplierId?: string;
  crop?: string;
  /** 'closed' (default) | 'harvested' | 'cancelled' | 'active' | 'all'. */
  status?: string;
  /** ISO date YYYY-MM-DD — filters on closedAt. */
  dateFrom?: string;
  dateTo?: string;
}

function cycleYieldToSnake(params: CycleYieldParams): Record<string, unknown> {
  return {
    supplier_id: params.supplierId || undefined,
    crop: params.crop || undefined,
    status: params.status || undefined,
    date_from: params.dateFrom || undefined,
    date_to: params.dateTo || undefined,
  };
}

export async function listCycleYieldReport(params: CycleYieldParams = {}): Promise<CycleYieldRow[]> {
  const res = await apiClient.get<CycleYieldRow[]>('/api/v1/reports/cycle-yield', {
    params: cycleYieldToSnake(params),
  });
  return res.data;
}

export async function downloadCycleYieldReport(params: CycleYieldParams = {}): Promise<Blob> {
  const res = await apiClient.get<Blob>('/api/v1/reports/cycle-yield/export', {
    params: cycleYieldToSnake(params),
    responseType: 'blob',
  });
  return res.data;
}
