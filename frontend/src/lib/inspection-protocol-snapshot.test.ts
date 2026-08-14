import { describe, it, expect } from 'vitest';
import {
  getScoreDisplayItems,
  resolveScoreLabels,
  FALLBACK_SCORE_LABELS,
  SNAPSHOT_KEY,
} from './inspection-protocol-snapshot';

function snapshotRecord(criteria: unknown, scores: Record<string, number | null> = {}) {
  return {
    customFields: { [SNAPSHOT_KEY]: { version: 1, growthStage: 'X', criteria } },
    ...scores,
  };
}

describe('getScoreDisplayItems — valid snapshot', () => {
  it('uses the snapshot labels and scores for every slot', () => {
    const record = snapshotRecord([
      { slot: 'fieldPrepScore', label: 'สภาพอากาศ', score: 8 },
      { slot: 'weatherScore', label: 'การดูแลรักษา', score: 7 },
      { slot: 'careScore', label: 'ความเสี่ยง', score: 9 },
      { slot: 'varietyResistanceScore', label: 'สภาพแปลง', score: 6 },
    ]);
    expect(getScoreDisplayItems(record)).toEqual([
      { slot: 'fieldPrepScore', label: 'สภาพอากาศ', score: 8 },
      { slot: 'weatherScore', label: 'การดูแลรักษา', score: 7 },
      { slot: 'careScore', label: 'ความเสี่ยง', score: 9 },
      { slot: 'varietyResistanceScore', label: 'สภาพแปลง', score: 6 },
    ]);
  });

  it('maps by slot regardless of criteria order', () => {
    const record = snapshotRecord([
      { slot: 'varietyResistanceScore', label: 'ความเสี่ยงโรคและแมลง', score: 6 },
      { slot: 'fieldPrepScore', label: 'การติดผล', score: 8 },
      { slot: 'careScore', label: 'การดูแลรักษา', score: 9 },
      { slot: 'weatherScore', label: 'ความสมบูรณ์ของผล', score: 7 },
    ]);
    const items = getScoreDisplayItems(record);
    // Output is always in canonical slot order …
    expect(items.map((i) => i.slot)).toEqual([
      'fieldPrepScore', 'weatherScore', 'careScore', 'varietyResistanceScore',
    ]);
    // … with each label matched to its own slot, not the array position.
    expect(items.map((i) => i.label)).toEqual([
      'การติดผล', 'ความสมบูรณ์ของผล', 'การดูแลรักษา', 'ความเสี่ยงโรคและแมลง',
    ]);
  });

  it('falls back to the record score column when the snapshot omits a score', () => {
    const record = snapshotRecord(
      [{ slot: 'fieldPrepScore', label: 'การเตรียมแปลง' }], // no score key
      { fieldPrepScore: 4 },
    );
    expect(getScoreDisplayItems(record)[0]).toEqual({
      slot: 'fieldPrepScore', label: 'การเตรียมแปลง', score: 4,
    });
  });
});

describe('getScoreDisplayItems — no snapshot (old records)', () => {
  it('uses the fallback labels and the record score columns', () => {
    const record = {
      customFields: {},
      fieldPrepScore: 8, weatherScore: 7, careScore: 6, varietyResistanceScore: 5,
    };
    expect(getScoreDisplayItems(record)).toEqual([
      { slot: 'fieldPrepScore', label: 'การเตรียมแปลง', score: 8 },
      { slot: 'weatherScore', label: 'สภาพอากาศ', score: 7 },
      { slot: 'careScore', label: 'การดูแลรักษา', score: 6 },
      { slot: 'varietyResistanceScore', label: 'ความต้านทานของสายพันธุ์', score: 5 },
    ]);
  });

  it('handles a missing customFields entirely, scores default to null', () => {
    const items = getScoreDisplayItems({});
    expect(items.map((i) => i.label)).toEqual(Object.values(FALLBACK_SCORE_LABELS));
    expect(items.every((i) => i.score === null)).toBe(true);
  });
});

describe('getScoreDisplayItems — malformed snapshot degrades safely', () => {
  it('criteria not an array → fallback, no throw', () => {
    const record = { customFields: { [SNAPSHOT_KEY]: { criteria: 'nope' } }, fieldPrepScore: 3 };
    const items = getScoreDisplayItems(record);
    expect(items[0]).toEqual({ slot: 'fieldPrepScore', label: 'การเตรียมแปลง', score: 3 });
  });

  it('snapshot not an object → fallback', () => {
    const record = { customFields: { [SNAPSHOT_KEY]: 42 }, weatherScore: 5 };
    expect(getScoreDisplayItems(record)[1]).toEqual({
      slot: 'weatherScore', label: 'สภาพอากาศ', score: 5,
    });
  });

  it('individual bad criteria are ignored while good ones still apply', () => {
    const record = snapshotRecord([
      { slot: 'unknownSlot', label: 'ไม่ควรใช้', score: 1 }, // slot not one of the 4
      { slot: 'weatherScore', label: 123, score: 2 },          // label not a string
      { slot: 'careScore', label: '   ', score: 3 },           // blank label
      { slot: 'fieldPrepScore', label: 'ความสมบูรณ์ของดอก', score: 9 }, // valid
    ], { weatherScore: 7, careScore: 6 });
    const items = getScoreDisplayItems(record);
    // valid one takes the snapshot label + score
    expect(items[0]).toEqual({ slot: 'fieldPrepScore', label: 'ความสมบูรณ์ของดอก', score: 9 });
    // bad label/blank fall back to fallback label, score still from snapshot number
    expect(items[1]).toEqual({ slot: 'weatherScore', label: 'สภาพอากาศ', score: 2 });
    expect(items[2]).toEqual({ slot: 'careScore', label: 'การดูแลรักษา', score: 3 });
  });
});

describe('resolveScoreLabels', () => {
  it('returns fallback labels for an empty record', () => {
    expect(resolveScoreLabels({})).toEqual(FALLBACK_SCORE_LABELS);
  });

  it('overlays only the valid snapshot labels', () => {
    const labels = resolveScoreLabels(snapshotRecord([
      { slot: 'fieldPrepScore', label: 'ความพร้อมเก็บเกี่ยว' },
    ]));
    expect(labels.fieldPrepScore).toBe('ความพร้อมเก็บเกี่ยว');
    expect(labels.weatherScore).toBe('สภาพอากาศ'); // untouched → fallback
  });
});
