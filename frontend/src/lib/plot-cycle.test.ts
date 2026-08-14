import { describe, it, expect } from 'vitest';
import {
  getActiveCycle,
  getLatestCycle,
  describeCycleStatus,
  formatCycleTitle,
  plotHasActiveCycle,
  inferHasActiveCycle,
} from './plot-cycle';
import type { PlotCycle } from '../api/plots';

function cycle(overrides: Partial<PlotCycle> = {}): PlotCycle {
  return {
    id: 'c1', plotId: 'p1', cycleNo: 1, status: 'active',
    crop: 'พริก', variety: 'พริกขี้หนู', cycleLabel: null, lotNo: 'LOT-01',
    poNumber: null, pCode: null, lotNoSource: null, lotRunningNo: null, supplierLotNo: null,
    oracleSupplierCode: null, oracleInvoice: null, refAccount: null,
    plantingDate: '2026-06-01', plantCount: 100,
    expectedYieldFull: '1000.00', expectedYieldUnit: 'kg',
    startedAt: '2026-06-01T00:00:00Z', closedAt: null, closedById: null,
    closeReason: null,
    finalYieldPct: null, finalEstimatedYield: null, finalInspectionRecordId: null,
    harvestYield: null, finalYieldAfterClean: null, finalYieldUnit: null,
    harvestDate: null, finalNote: null,
    createdAt: '2026-06-01T00:00:00Z', updatedAt: '2026-06-01T00:00:00Z',
    ...overrides,
  };
}

describe('getActiveCycle / getLatestCycle', () => {
  it('returns the single active cycle', () => {
    const cs = [cycle({ id: 'c1', cycleNo: 1, status: 'harvested' }), cycle({ id: 'c2', cycleNo: 2, status: 'active' })];
    expect(getActiveCycle(cs)?.id).toBe('c2');
  });
  it('returns null when no cycle is active', () => {
    expect(getActiveCycle([cycle({ status: 'cancelled' })])).toBeNull();
    expect(getActiveCycle([])).toBeNull();
  });
  it('getLatestCycle picks the highest cycleNo regardless of status', () => {
    const cs = [cycle({ id: 'c1', cycleNo: 1 }), cycle({ id: 'c3', cycleNo: 3, status: 'cancelled' })];
    expect(getLatestCycle(cs)?.id).toBe('c3');
  });
});

describe('describeCycleStatus / formatCycleTitle', () => {
  it('maps each status to Thai, and null → รอเริ่มรอบปลูก', () => {
    expect(describeCycleStatus('active')).toBe('กำลังปลูก');
    expect(describeCycleStatus('harvested')).toBe('เก็บเกี่ยวแล้ว');
    expect(describeCycleStatus('cancelled')).toBe('ยกเลิก');
    expect(describeCycleStatus(null)).toBe('รอเริ่มรอบปลูก');
  });
  it('formatCycleTitle omits unset parts', () => {
    expect(formatCycleTitle(cycle({ cycleNo: 2, crop: 'พริก', lotNo: 'LOT-01' }))).toBe('รอบที่ 2 · พริก · LOT-01');
    expect(formatCycleTitle(cycle({ cycleNo: 3, crop: null, lotNo: null }))).toBe('รอบที่ 3');
  });
  it('formatCycleTitle leads with cycleLabel when set (round 8.0), else รอบที่ N', () => {
    expect(formatCycleTitle(cycle({ cycleLabel: 'jun2026', cycleNo: 2, crop: 'พริก', lotNo: 'LOT-01' })))
      .toBe('jun2026 · พริก · LOT-01');
    // blank/whitespace label falls back to รอบที่ N
    expect(formatCycleTitle(cycle({ cycleLabel: '   ', cycleNo: 5, crop: null, lotNo: null }))).toBe('รอบที่ 5');
  });
});

describe('plotHasActiveCycle (round 7.3.1 — backend truth primary)', () => {
  it('uses activeCycleId as the authoritative signal', () => {
    expect(plotHasActiveCycle({ activeCycleId: 'cycle-1' })).toBe(true);
    expect(plotHasActiveCycle({ activeCycleId: null })).toBe(false);
  });

  it('activeCycleId=null WINS over populated mirror fields (no false "active" from stale mirror)', () => {
    expect(plotHasActiveCycle({
      activeCycleId: null,
      currentCrop: 'พริก', currentVariety: 'พริกขี้หนู', plantCount: 500, expectedYieldFull: '1000',
    })).toBe(false);
  });

  it('falls back to mirror inference only when activeCycleId is absent (pre-read-model shape)', () => {
    expect(plotHasActiveCycle({ currentCrop: 'พริก' })).toBe(true);
    expect(plotHasActiveCycle({})).toBe(false);
  });
});

describe('inferHasActiveCycle (fallback, unchanged behavior)', () => {
  it('is true when any mirror field is set', () => {
    expect(inferHasActiveCycle({ currentLotNo: 'LOT-1' })).toBe(true);
    expect(inferHasActiveCycle({ plantCount: 10 })).toBe(true);
    expect(inferHasActiveCycle({ expectedYieldFull: '5' })).toBe(true);
  });
  it('is false when all mirror fields are empty/zero', () => {
    expect(inferHasActiveCycle({ currentCrop: null, plantCount: 0, expectedYieldFull: null })).toBe(false);
    expect(inferHasActiveCycle({})).toBe(false);
  });
});
