/**
 * Growth-stage-dependent form behaviour (round C).
 *
 * Growth stages are admin-editable Master Data, not a hard-coded enum — the
 * form renders whatever buttons the backend serves. Two of them nonetheless
 * change what the yield section MEANS, and this module is the one place that
 * knows which:
 *
 *   เก็บเกี่ยว        — the kg entered is what was actually harvested, not a
 *                      forecast. Same field, same column; only the label
 *                      changes, because at ระยะงอก it genuinely IS an estimate.
 *   ผลผลิตสุดท้าย     — as above, PLUS ผลผลิตหลังทำความสะอาด
 *                      (records.final_yield_after_clean, migration 0054).
 *
 * Matching by exact stage name is deliberate. The alternative — a flag on the
 * Master Data row — would be a schema change to express something that has
 * exactly two known values, and an admin renaming a stage should visibly break
 * this behaviour rather than silently change what a number means. If a rename
 * ever happens, these constants are the single place to update.
 */

/** The harvest stage: kg entered here is the real harvested figure. */
export const HARVEST_STAGE = 'เก็บเกี่ยว';

/** Round C's new stage: the same harvested figure PLUS the after-cleaning one.
 * Deliberately NOT an inspection-protocol stage — it carries no 4-score
 * assessment, and the backend's gated pass-through already supports a stage
 * with no protocol (services/inspection_protocols.py). */
export const FINAL_YIELD_STAGE = 'ผลผลิตสุดท้าย';

/** True when the kg the form collects is an ACTUAL measured figure rather than
 * a forecast — which is the only thing the label depends on. */
export function isActualYieldStage(stage: string | null | undefined): boolean {
  const s = (stage ?? '').trim();
  return s === HARVEST_STAGE || s === FINAL_YIELD_STAGE;
}

/** True only on the stage that records ผลผลิตหลังทำความสะอาด. Every other
 * stage leaves records.final_yield_after_clean null. */
export function showsFinalYieldAfterClean(stage: string | null | undefined): boolean {
  return (stage ?? '').trim() === FINAL_YIELD_STAGE;
}

/** The kg input's label for a stage. One name for one number: the user
 * confirmed "ผลผลิตที่เก็บได้" and "ผลผลิตตอนเก็บเกี่ยว" are the same figure,
 * so the form must never show both. */
export function yieldQuantityLabel(stage: string | null | undefined): string {
  return isActualYieldStage(stage) ? 'ผลผลิตที่เก็บได้' : 'ผลผลิตที่คาดว่าจะได้';
}
