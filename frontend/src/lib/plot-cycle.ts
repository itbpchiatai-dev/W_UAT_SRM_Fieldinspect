/**
 * Plot cycle (รอบปลูก) display helpers — round 7.3.
 *
 * "Current expected yield" is NOT computed here — that's
 * computeCurrentExpectedYield in lib/yield-planning.ts (reused, not
 * duplicated): a cycle carries the yield PLAN (expectedYieldFull), while
 * currentYieldPct is inspection-derived and lives on the Plot, not the cycle.
 */
import type { PlotCycle, PlotCycleStatus } from '../api/plots';
import { toNumberOrNull } from './numeric';

/** The plot's single active cycle, or null. Mirrors the backend invariant
 * (partial unique index: at most one active cycle per plot) — this never
 * has to pick among several. */
export function getActiveCycle(cycles: PlotCycle[]): PlotCycle | null {
  return cycles.find((c) => c.status === 'active') ?? null;
}

/** The most recent cycle by cycleNo, regardless of status — for a plot with
 * no active cycle, this is what to show as "last known" context. */
export function getLatestCycle(cycles: PlotCycle[]): PlotCycle | null {
  if (cycles.length === 0) return null;
  return cycles.reduce((latest, c) => (c.cycleNo > latest.cycleNo ? c : latest));
}

const STATUS_LABELS: Record<PlotCycleStatus, string> = {
  active: 'กำลังปลูก',
  harvested: 'เก็บเกี่ยวแล้ว',
  cancelled: 'ยกเลิก',
};

/** Thai label for a cycle's status; status=null/undefined means "no active
 * cycle at all" (a plot with zero or only-closed cycles), distinct from any
 * real cycle status. */
export function describeCycleStatus(status: PlotCycleStatus | null | undefined): string {
  if (!status) return 'รอเริ่มรอบปลูก';
  return STATUS_LABELS[status];
}

/** The cycle's display title (round 8.0) — the admin-chosen season name
 * (cycleLabel, e.g. "jun2026") when set, else the system fallback
 * "รอบที่ <cycleNo>". */
export function cycleDisplayName(cycle: Pick<PlotCycle, 'cycleLabel' | 'cycleNo'>): string {
  return cycle.cycleLabel?.trim() || `รอบที่ ${cycle.cycleNo}`;
}

/** e.g. "jun2026 · พริก · LOT-01" (or "รอบที่ 2 · …" when there's no label) —
 * omits any part that's unset rather than showing a dangling " · ". */
export function formatCycleTitle(cycle: PlotCycle): string {
  const parts = [cycleDisplayName(cycle), cycle.crop, cycle.lotNo].filter(
    (p): p is string => !!p,
  );
  return parts.join(' · ');
}

/** Record-history cycle badge text (round 8.0.5) — the record's OWN cycle
 * (record.cycleLabel/cycleNo, denormalised from Record.plot_cycle at read
 * time), never the plot's current active cycle. Unlike cycleDisplayName
 * (PlotCycle-shaped, cycleNo always present), a Record's cycle fields are
 * both nullable: cycleLabel when set, else "รอบที่ N" when there's at least
 * a cycle number, else the generic "รอบปลูก" for older/broken data with no
 * cycle info at all. */
export function recordCycleDisplayName(record: {
  cycleLabel?: string | null;
  cycleNo?: number | null;
}): string {
  const label = record.cycleLabel?.trim();
  if (label) return label;
  if (record.cycleNo != null) return `รอบที่ ${record.cycleNo}`;
  return 'รอบปลูก';
}

/**
 * Round 7.3.1 — the AUTHORITATIVE "does this plot have an active รอบปลูก"
 * check for the Plots list. Prefers the backend read-model's activeCycleId
 * (added round 7.3.1: null ⇒ no active cycle, a value ⇒ active), and only
 * falls back to the mirror-field inference below for a shape/cache that
 * predates the read-model (activeCycleId undefined). This replaces
 * inferHasActiveCycle as the PRIMARY signal.
 */
export function plotHasActiveCycle(plot: {
  activeCycleId?: string | null;
  currentCrop?: string | null;
  currentVariety?: string | null;
  currentLotNo?: string | null;
  currentPlantingDate?: string | null;
  plantCount?: number | null;
  expectedYieldFull?: string | number | null;
}): boolean {
  if (plot.activeCycleId !== undefined) return plot.activeCycleId != null;
  return inferHasActiveCycle(plot);
}

/**
 * Round 7.3 — FALLBACK ONLY (superseded as the primary check by
 * plotHasActiveCycle above, round 7.3.1). Infers "has an active planting
 * cycle" from the plot's MASTER/planting mirror fields, which the active
 * cycle keeps in sync (plot_cycle_repository.sync_plot_mirror_from_cycle) and
 * closing a cycle clears ALL of
 * (clear_plot_cycle_mirror_and_inspection_snapshot) — so "every one of these
 * is unset" reliably means "no active cycle". Kept for shapes that don't
 * carry the read-model's activeCycleId yet.
 */
export function inferHasActiveCycle(plot: {
  currentCrop?: string | null;
  currentVariety?: string | null;
  currentLotNo?: string | null;
  currentPlantingDate?: string | null;
  plantCount?: number | null;
  expectedYieldFull?: string | number | null;
}): boolean {
  return !!(
    plot.currentCrop
    || plot.currentVariety
    || plot.currentLotNo
    || plot.currentPlantingDate
    || (plot.plantCount != null && plot.plantCount > 0)
    || toNumberOrNull(plot.expectedYieldFull) != null
  );
}
