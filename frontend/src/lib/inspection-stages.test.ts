/**
 * Round C — the two growth stages that change what the yield section means.
 *
 * These are matched by exact name against admin-editable Master Data, so the
 * tests double as the record of WHICH names carry that meaning: if an admin
 * renames a stage, this file is where the mismatch has to be resolved.
 */
import { describe, it, expect } from 'vitest';
import {
  FINAL_YIELD_STAGE,
  HARVEST_STAGE,
  isActualYieldStage,
  showsFinalYieldAfterClean,
  yieldQuantityLabel,
} from './inspection-stages';

describe('isActualYieldStage', () => {
  it('is true for the two stages where the kg is measured, not forecast', () => {
    expect(isActualYieldStage(HARVEST_STAGE)).toBe(true);
    expect(isActualYieldStage(FINAL_YIELD_STAGE)).toBe(true);
  });

  it('is false for every earlier stage, where the number really is an estimate', () => {
    for (const stage of ['ระยะงอก', 'เจริญเติบโต', 'ออกดอก', 'ติดผล']) {
      expect(isActualYieldStage(stage)).toBe(false);
    }
  });

  it('is false when no stage is chosen yet', () => {
    expect(isActualYieldStage(null)).toBe(false);
    expect(isActualYieldStage(undefined)).toBe(false);
    expect(isActualYieldStage('')).toBe(false);
  });

  it('tolerates stray whitespace around a stored value', () => {
    expect(isActualYieldStage(`  ${HARVEST_STAGE}  `)).toBe(true);
  });
});

describe('showsFinalYieldAfterClean', () => {
  it('is true ONLY on ผลผลิตสุดท้าย', () => {
    expect(showsFinalYieldAfterClean(FINAL_YIELD_STAGE)).toBe(true);
    // เก็บเกี่ยว records the harvested figure, but not the after-cleaning one:
    // the crop has not been cleaned yet at that point.
    expect(showsFinalYieldAfterClean(HARVEST_STAGE)).toBe(false);
    expect(showsFinalYieldAfterClean('ติดผล')).toBe(false);
    expect(showsFinalYieldAfterClean(null)).toBe(false);
  });
});

describe('yieldQuantityLabel', () => {
  it('names the same field differently depending on what it means', () => {
    // One field, one column, two names — never two kg boxes that mean almost
    // the same thing (the round-C design decision the user confirmed).
    expect(yieldQuantityLabel(HARVEST_STAGE)).toBe('ผลผลิตที่เก็บได้');
    expect(yieldQuantityLabel(FINAL_YIELD_STAGE)).toBe('ผลผลิตที่เก็บได้');
    expect(yieldQuantityLabel('ระยะงอก')).toBe('ผลผลิตที่คาดว่าจะได้');
    expect(yieldQuantityLabel(null)).toBe('ผลผลิตที่คาดว่าจะได้');
  });

  it('uses ONE name for the harvested figure across both stages', () => {
    expect(yieldQuantityLabel(HARVEST_STAGE)).toBe(yieldQuantityLabel(FINAL_YIELD_STAGE));
  });
});
