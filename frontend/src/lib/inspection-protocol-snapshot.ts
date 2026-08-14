/**
 * Read-side helper for the growth-stage inspection protocol snapshot
 * (round 5.3). The backend freezes each inspection's 4 criteria labels
 * (and scores) into records.customFields.inspectionProtocolSnapshot at
 * create time (round 5.1); the display pages read the labels from there so
 * a historical record always shows the criteria it was scored under — even
 * if the live protocol is later edited — instead of a hardcoded label set.
 *
 * Everything here is defensive: a record with no snapshot (created before
 * this feature, or on a stage with no protocol), or a snapshot that's
 * malformed/partial, always degrades to the fixed fallback labels below
 * rather than throwing. The one place snapshot shape is trusted is inside
 * these validators.
 */

/** The 4 fixed score columns, in canonical display order. */
export const SCORE_SLOTS = [
  'fieldPrepScore',
  'weatherScore',
  'careScore',
  'varietyResistanceScore',
] as const;

export type ScoreSlot = (typeof SCORE_SLOTS)[number];

/** Labels for records with no (or unusable) snapshot — the original
 * pre-protocol wording, kept so old records read exactly as before. */
export const FALLBACK_SCORE_LABELS: Record<ScoreSlot, string> = {
  fieldPrepScore: 'การเตรียมแปลง',
  weatherScore: 'สภาพอากาศ',
  careScore: 'การดูแลรักษา',
  varietyResistanceScore: 'ความต้านทานของสายพันธุ์',
};

export const SNAPSHOT_KEY = 'inspectionProtocolSnapshot';

export interface InspectionProtocolSnapshotCriterion {
  slot: string;
  label: string;
  score?: number | null;
}

export interface InspectionProtocolSnapshot {
  version?: number;
  growthStage?: string;
  criteria: InspectionProtocolSnapshotCriterion[];
}

/** Any record-ish object carrying customFields and/or the 4 score columns —
 * RecordDetail, RecordSummary, or a synthetic { scores } object all fit. */
export interface ScoreRecordLike {
  customFields?: Record<string, unknown> | null;
  fieldPrepScore?: number | null;
  weatherScore?: number | null;
  careScore?: number | null;
  varietyResistanceScore?: number | null;
}

const SCORE_SLOT_SET = new Set<string>(SCORE_SLOTS);

/** Pull a structurally-valid snapshot out of a record, or null. Only checks
 * the outer shape (object + criteria array); each criterion is validated
 * individually where it's consumed, so one bad entry can't poison the rest. */
function readSnapshot(record: ScoreRecordLike): InspectionProtocolSnapshot | null {
  const raw = record.customFields?.[SNAPSHOT_KEY];
  if (!raw || typeof raw !== 'object') return null;
  const criteria = (raw as { criteria?: unknown }).criteria;
  if (!Array.isArray(criteria)) return null;
  return raw as InspectionProtocolSnapshot;
}

/** slot → label, from the snapshot where each criterion is valid (slot is
 * one of the 4, label is a non-empty string), else the fallback for that
 * slot. Order-independent: criteria are matched by slot, not position. */
export function resolveScoreLabels(record: ScoreRecordLike): Record<ScoreSlot, string> {
  const labels: Record<ScoreSlot, string> = { ...FALLBACK_SCORE_LABELS };
  const snap = readSnapshot(record);
  if (!snap) return labels;
  for (const c of snap.criteria) {
    if (
      c && typeof c === 'object' &&
      typeof c.slot === 'string' && SCORE_SLOT_SET.has(c.slot) &&
      typeof c.label === 'string' && c.label.trim() !== ''
    ) {
      labels[c.slot as ScoreSlot] = c.label;
    }
  }
  return labels;
}

export interface ScoreDisplayItem {
  slot: ScoreSlot;
  label: string;
  score: number | null;
}

/**
 * The 4 score rows to render for a record, in canonical slot order: label
 * from the snapshot (or fallback), score from the snapshot's own value when
 * present, else the record's score column, else null. Never throws.
 */
export function getScoreDisplayItems(record: ScoreRecordLike): ScoreDisplayItem[] {
  const labels = resolveScoreLabels(record);
  const snap = readSnapshot(record);

  const snapScore: Partial<Record<ScoreSlot, number>> = {};
  if (snap) {
    for (const c of snap.criteria) {
      if (
        c && typeof c === 'object' &&
        typeof c.slot === 'string' && SCORE_SLOT_SET.has(c.slot) &&
        typeof c.score === 'number'
      ) {
        snapScore[c.slot as ScoreSlot] = c.score;
      }
    }
  }

  return SCORE_SLOTS.map((slot) => ({
    slot,
    label: labels[slot],
    score: snapScore[slot] ?? record[slot] ?? null,
  }));
}
