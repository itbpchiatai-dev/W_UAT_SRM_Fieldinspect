import axios from 'axios';

/**
 * photo-upload-budget — round 35. How long a multipart photo upload is allowed
 * to take, and when a field user deserves a warning before starting one.
 *
 * WHY THIS EXISTS
 * ---------------
 * Both axios instances — api/client.ts's apiClient and api/publicInspection.ts's
 * publicApiClient — are created with `timeout: 15_000`. That is a sensible
 * figure for a JSON request and a hopeless one for a multipart upload of up to
 * five photos, and nothing overrode it at the two upload call sites. A record
 * submitted from a plot was being aborted by the CLIENT at 15 seconds and then
 * reported as "เชื่อมต่อเครือข่ายไม่สำเร็จ", because an axios timeout produces an
 * error with no `response` — indistinguishable, to the handler, from a genuine
 * network failure. The user sees full signal, is told the network failed,
 * retries, and fails at exactly the same point every time.
 *
 * The budget has to cover three things, not one:
 *   1. pushing the bytes up a mobile uplink,
 *   2. the backend re-decoding and re-encoding EVERY photo to WebP
 *      (services/inspection_photos.py, MAX_CONCURRENT_IMAGE_JOBS at a time),
 *   3. the database write and the response.
 *
 * And the worst case is not hypothetical. When the browser cannot encode WebP
 * — Safari and the WKWebView-based in-app browsers, see
 * ENCODER_UNSUPPORTED_WARNING in lib/inspection-photo-compression.ts — the
 * client deliberately falls back to sending the ORIGINAL file, up to
 * MAX_SOURCE_PHOTO_BYTES each, five of them.
 *
 * This module holds numbers, pure functions and one error predicate. It
 * constructs no axios INSTANCE and imports no module with side effects (the
 * bare `axios` import below only brings in `isAxiosError`). That matters
 * because the public (unauthenticated) flow imports it, and
 * publicInspection.ts goes out of its way NOT to pull in client.ts —
 * importing that module would construct the authenticated client and register
 * its auth/refresh interceptors inside the public bundle.
 */

/**
 * Ceiling for one photo upload request.
 *
 * Two sanity checks rather than a round number picked by feel. Both work out
 * at ~126 s, so the budget is set above that and not at it:
 *   - a realistic bad case, 15 MiB over a 1 Mbps uplink: 125.8 s;
 *   - the absolute worst case, 5 x 15 MiB = 75 MiB over a decent 5 Mbps
 *     uplink: 125.8 s.
 *
 * (A first pass at this said "~120 s" for both, having quietly done the
 * arithmetic in decimal MB while the limits it was checking against are in
 * MiB. 120_000 would have aborted exactly the worst case it was chosen to
 * cover. The tests assert the MiB reading, which is the stricter one.)
 *
 * Deliberately NOT unlimited. A connection that has genuinely died still has
 * to fail and hand the user back their form — with the same idempotency key,
 * so their own retry replays rather than duplicating (see PublicInspect's
 * submit handler). Two and a half minutes is about the longest a field user
 * should ever be asked to stare at a screen before being told something is
 * wrong, and that is only tolerable because the upload now reports progress
 * while it waits.
 */
export const PHOTO_UPLOAD_TIMEOUT_MS = 150_000;

/**
 * The uplink this module assumes when it turns bytes into a human estimate.
 * 5 Mbps is a fair-but-not-flattering mobile upload speed in the field: a plot
 * is rarely next to a tower, and the "5G" indicator describes the downlink.
 * Only ever used for COPY — never to decide whether to send.
 */
export const ASSUMED_UPLINK_BYTES_PER_SEC = 5_000_000 / 8;

/**
 * Total payload above which the user is warned before submitting.
 *
 * Set from the estimate, not from a tidy number: 8 MiB is ~13 s of upload at
 * the assumed uplink, which is about where a submit stops feeling instant and
 * starts feeling broken. Below this, a warning would fire on almost every
 * submit and be trained away within a day.
 */
export const LARGE_UPLOAD_WARNING_BYTES = 8 * 1024 * 1024;

/** Sum of the chosen photos. Nulls (empty slots) are skipped. */
export function totalUploadBytes(photos: readonly (File | null)[]): number {
  return photos.reduce<number>((sum, photo) => sum + (photo ? photo.size : 0), 0);
}

/** Seconds the upload is expected to take, rounded up, floored at 1. */
export function estimateUploadSeconds(bytes: number): number {
  if (bytes <= 0) return 0;
  return Math.max(1, Math.ceil(bytes / ASSUMED_UPLINK_BYTES_PER_SEC));
}

/** "45 วินาที" / "ประมาณ 2 นาที" — a duration a person can act on. Minutes are
 * used from 90 s up, because "90 วินาที" reads as precision this estimate does
 * not have. */
export function describeUploadDuration(seconds: number): string {
  if (seconds < 90) return `ประมาณ ${seconds} วินาที`;
  return `ประมาณ ${Math.ceil(seconds / 60)} นาที`;
}

/** Megabytes with one decimal, for copy only. */
export function describeUploadSize(bytes: number): string {
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

/**
 * The Thai warning to show beside the submit button, or null when the payload
 * is small enough that saying anything would be noise.
 *
 * Keyed on total SIZE, not on whether compression fell back. Size is what
 * actually decides whether the request finishes: five photos that compressed
 * perfectly can still add up, and one that did not may be small enough not to
 * matter. It also needs no new data from PhotoSlotPicker, whose onChange hands
 * the page `(File | null)[]` and nothing else.
 *
 * The copy leads with the time rather than the megabytes, because the time is
 * the part a user can act on.
 */
export function largeUploadWarning(photos: readonly (File | null)[]): string | null {
  const bytes = totalUploadBytes(photos);
  if (bytes < LARGE_UPLOAD_WARNING_BYTES) return null;
  const count = photos.filter((photo) => photo !== null).length;
  const duration = describeUploadDuration(estimateUploadSeconds(bytes));
  return (
    `รูป ${count} ใบ รวม ${describeUploadSize(bytes)} — การส่งอาจใช้เวลา${duration} ` +
    `กรุณาเปิดหน้านี้ค้างไว้จนกว่าจะขึ้นว่าบันทึกสำเร็จ`
  );
}

/**
 * Turn axios's `loaded` / `total` into a whole percentage for the UI.
 *
 * `total` is absent whenever the request body's length cannot be measured, so
 * a caller must cope with a progress bar that never appears; -1 says "unknown"
 * rather than inventing a number that would stall at a lie. The value is
 * clamped to 0..100 because `loaded` can briefly exceed `total` once transport
 * overhead is counted.
 */
export function uploadPercent(loaded: number, total: number | undefined): number {
  if (!total || total <= 0) return -1;
  return Math.max(0, Math.min(100, Math.round((loaded / total) * 100)));
}

/**
 * True when an error is the CLIENT giving up on its own clock, rather than the
 * network failing.
 *
 * Both look identical to a naive handler — axios rejects with no `response`
 * either way — and that is exactly how a 15 s abort came to be reported as
 * "เชื่อมต่อเครือข่ายไม่สำเร็จ" to a user holding a phone with full signal.
 * axios marks a timeout with `ECONNABORTED`; some adapters and some versions
 * use `ETIMEDOUT` instead, so both are accepted.
 */
export function isUploadTimeout(err: unknown): boolean {
  if (!axios.isAxiosError(err)) return false;
  if (err.response) return false;
  return err.code === 'ECONNABORTED' || err.code === 'ETIMEDOUT';
}

/**
 * What to say when the upload ran out of time.
 *
 * Three jobs, in order: name the real cause (not "the network"), promise the
 * work is not lost, and give one action. The promise is safe to make — the
 * submit handlers keep the form, the chosen photos and the SAME idempotency
 * identity, so pressing save again replays rather than duplicating.
 */
export const UPLOAD_TIMEOUT_MESSAGE =
  'ส่งข้อมูลไม่ทันเวลา — รูปอาจมีขนาดใหญ่หรือสัญญาณอัปโหลดช้า ' +
  'ข้อมูลและรูปในหน้านี้ยังอยู่ครบ กดบันทึกอีกครั้งได้เลย';
