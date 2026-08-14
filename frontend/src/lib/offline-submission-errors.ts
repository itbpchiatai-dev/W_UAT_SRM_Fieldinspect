/**
 * offline-submission-errors — structured error codes the backend's public
 * offline-submission contract returns (round 8-4A/8-4A.1
 * app/api/v1/public_records.py), as `{"detail": {"code": "..."}}`. Shared
 * lib (not local to PublicInspect) since round 8-4C's sync flow will need
 * the same codes/messages.
 *
 * Round 8-4B does not build a conflict-RESOLUTION workflow — a matched code
 * just gets a clear Thai message instead of the generic "บันทึกไม่สำเร็จ".
 */
import axios from 'axios';

export const OFFLINE_ERROR_CODES = [
  'planting_cycle_changed',
  'offline_draft_expired',
  'idempotency_conflict',
  'offline_captured_at_invalid',
] as const;

export type OfflineErrorCode = (typeof OFFLINE_ERROR_CODES)[number];

const OFFLINE_ERROR_MESSAGES: Record<OfflineErrorCode, string> = {
  planting_cycle_changed: 'รอบปลูกของแปลงนี้เปลี่ยนแล้ว รายการนี้ไม่สามารถส่งเข้ารอบใหม่อัตโนมัติได้',
  offline_draft_expired: 'รายการนี้เกิน 7 วันแล้วและไม่สามารถส่งได้',
  idempotency_conflict: 'รหัสรายการนี้ถูกใช้กับข้อมูลอื่นแล้ว กรุณาเก็บรายการใหม่',
  offline_captured_at_invalid: 'เวลาที่บันทึกรายการไม่ถูกต้อง กรุณาตรวจสอบวันที่และเวลาของอุปกรณ์',
};

function isOfflineErrorCode(value: unknown): value is OfflineErrorCode {
  return typeof value === 'string' && (OFFLINE_ERROR_CODES as readonly string[]).includes(value);
}

/** Reads `err.response.data.detail.code` off an axios error and returns it
 * ONLY if it's one of the known offline error codes — null for every other
 * shape (a plain string detail, a validation-error array, no response,
 * etc.), so a caller can safely fall back to a generic message. */
export function extractOfflineErrorCode(err: unknown): OfflineErrorCode | null {
  if (!axios.isAxiosError(err)) return null;
  const detail = (err.response?.data as { detail?: unknown } | undefined)?.detail;
  const code = detail && typeof detail === 'object' && 'code' in detail
    ? (detail as { code?: unknown }).code
    : undefined;
  return isOfflineErrorCode(code) ? code : null;
}

export function describeOfflineErrorCode(code: OfflineErrorCode): string {
  return OFFLINE_ERROR_MESSAGES[code];
}

/** Round 8-4C — client-only diagnostic codes the sequential sync engine
 * (lib/offline-inspection-sync.ts) stamps as a draft's lastErrorCode when the
 * failure didn't come with one of the 4 backend structured codes above (a
 * plain 404, an unrecognized 4xx, a rate limit, or a server error hit during
 * sync). Kept separate from OfflineErrorCode/OFFLINE_ERROR_CODES since these
 * are never something the CREATE endpoint itself returns as {detail.code}. */
const DRAFT_CLIENT_ERROR_MESSAGES: Record<string, string> = {
  not_found: 'ไม่พบแปลงนี้ในสิทธิ์ปัจจุบัน หรือแปลง/รอบปลูกถูกปิดใช้งานไปแล้ว',
  no_active_cycle: 'แปลงนี้ยังไม่มีรอบปลูกที่เปิดอยู่ในขณะนี้',
  rate_limited: 'มีการส่งหลายครั้งเกินไป กรุณารอสักครู่แล้วลองส่งใหม่',
  server_error: 'เกิดข้อผิดพลาดที่เซิร์ฟเวอร์ระหว่างส่ง กรุณาลองใหม่ภายหลัง',
  unknown_error: 'ไม่สามารถส่งรายการนี้ได้ กรุณาลองใหม่ภายหลัง',
};

/** Safe description for ANY draft.lastErrorCode value — the 4 backend codes
 * (via describeOfflineErrorCode), the 5 client-only sync codes above, or null
 * (no failed attempt yet). Never falls back to a raw error message/stack —
 * an unrecognized code still gets a generic, safe sentence. */
export function describeDraftErrorCode(code: string | null): string | null {
  if (code === null) return null;
  if (isOfflineErrorCode(code)) return describeOfflineErrorCode(code);
  return DRAFT_CLIENT_ERROR_MESSAGES[code] ?? 'ไม่สามารถส่งรายการนี้ได้ กรุณาลองใหม่ภายหลัง';
}
