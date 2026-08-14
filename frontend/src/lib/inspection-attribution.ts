/**
 * Record phone-access attribution display helpers (round 8-3E) — shared by
 * RecordPreview and PlotDetail's expanded history row so both authenticated
 * views ("ข้อมูลการเข้าตรวจ") describe a phone-bound inspection record
 * identically. Reads ONLY the snapshot fields already on RecordDetail
 * (server-derived at create time from the phone-access session token) —
 * never derives a phone from the plot's CURRENT access-phone config, which
 * may have changed since this record was made.
 */
import { formatThaiMobile } from './phone';

export type SubmittedPhoneType = 'primary' | 'additional';
/** Canonical API/DB values (round 8-11A) — mirrors the backend's
 * app.db.models.record.INSPECTOR_TYPES and the InspectorType Literal exactly.
 * 'extension' was renamed to 'chiatai' (migration 0047 rewrote the dev rows),
 * so there is no legacy value to accept here. Note this is the INSPECTOR's
 * role, unrelated to the Supplier ENTITY shown on the Admin/Plots screens. */
export type InspectorType = 'farmer' | 'supplier' | 'chiatai';

export interface RecordAttributionFields {
  submittedPhoneSnapshot: string | null;
  submittedPhoneType: SubmittedPhoneType | null;
  inspectorType: InspectorType | null;
}

const PHONE_TYPE_LABEL: Record<SubmittedPhoneType, string> = {
  primary: 'เบอร์หลัก',
  additional: 'เบอร์เสริม',
};

/** Round 8-11A — the ONE visible-label mapping for inspector type, shared by
 * the public inspection form (PublicInspect), RecordPreview and PlotDetail's
 * inspection history, so a user never sees two different words for the same
 * stored value. Never persisted: the DB only ever holds the canonical key. */
const INSPECTOR_TYPE_LABEL: Record<InspectorType, string> = {
  farmer: 'เกษตรกร',
  supplier: 'บริษัทผู้ผลิต',
  chiatai: 'Chiatai',
};

/** The order the public form renders these in. Kept next to the label map so a
 * new inspector type cannot be added to one without the other. */
export const INSPECTOR_TYPE_ORDER: readonly InspectorType[] = ['farmer', 'supplier', 'chiatai'];

/** Every inspector type paired with its visible label, in form order — the
 * single source PublicInspect's radio group renders from. */
export const INSPECTOR_TYPE_OPTIONS: readonly { value: InspectorType; label: string }[] =
  INSPECTOR_TYPE_ORDER.map((value) => ({ value, label: INSPECTOR_TYPE_LABEL[value] }));

/** True when this record was made through the phone-bound public inspection
 * flow — false for a logged-in-flow record or one made before the feature
 * existed (both fields null). */
export function hasPhoneAttribution(r: RecordAttributionFields): boolean {
  return r.submittedPhoneSnapshot != null || r.inspectorType != null;
}

export function phoneTypeLabel(type: SubmittedPhoneType | null): string | null {
  return type ? PHONE_TYPE_LABEL[type] : null;
}

export function inspectorTypeLabel(type: InspectorType | null): string | null {
  return type ? INSPECTOR_TYPE_LABEL[type] : null;
}

/** Full formatted number — this module is only ever used behind records.read
 * (RecordPreview/PlotDetail both require it to load a record at all), so no
 * masking here; the public flow's own response types never carry a phone. */
export function formattedPhoneSnapshot(snapshot: string | null): string | null {
  return snapshot ? formatThaiMobile(snapshot) : null;
}

export interface SubmittedByFields {
  submittedByCode: string | null;
  submittedByName: string | null;
}

/**
 * Legacy field-attribution display line (round 8-3G retired
 * submittedByCode for new records — nullable now). A historical record
 * that still has a code shows "code — name" (or bare code with no name);
 * a record with only a name (submittedByCode never collected, or already
 * null) shows just the name; a record with neither returns null so the
 * caller can omit the line entirely rather than render nothing/undefined —
 * that record's identity lives in recordedBy (logged-in flow) or the
 * phone-attribution section (public flow) instead.
 */
export function submittedByLine(r: SubmittedByFields): string | null {
  if (r.submittedByCode) {
    return r.submittedByName ? `${r.submittedByCode} — ${r.submittedByName}` : r.submittedByCode;
  }
  return r.submittedByName || null;
}
