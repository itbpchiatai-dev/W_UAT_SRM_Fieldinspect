/**
 * Thai business date (round 8-19.1).
 *
 * The app's calendar day is Thailand's, not the device's and not UTC's.
 * Thailand is UTC+7 year-round (no DST), so `new Date().toISOString()
 * .slice(0, 10)` — the pattern this replaces — is wrong for the first seven
 * hours of every Thai day: at 01:00 ICT it still reports YESTERDAY's date.
 * That made an inspection recorded early in the morning carry the previous
 * day's recordDate, and left its plot card showing "พร้อมตรวจ" instead of
 * "ตรวจแล้ววันนี้" (the backend's own _today() moved to Asia/Bangkok in the
 * same round, so the two now agree by construction).
 *
 * Uses Intl with an explicit timeZone — no dependency, and independent of
 * whatever timezone the phone/browser is actually set to, which for a public
 * page is not something we control.
 */

const BANGKOK_TIME_ZONE = 'Asia/Bangkok';

const bangkokParts = new Intl.DateTimeFormat('en-US', {
  timeZone: BANGKOK_TIME_ZONE,
  year: 'numeric',
  month: '2-digit',
  day: '2-digit',
});

/**
 * Today's date in Asia/Bangkok as "YYYY-MM-DD" — the shape the API expects
 * for recordDate.
 *
 * Deliberately a FUNCTION, not a module-level constant: the value it returns
 * has to be read at submit time. The constants it replaced were evaluated
 * once at import, so a page left open across midnight kept submitting the
 * previous day's date.
 *
 * Built from formatToParts rather than a locale whose format happens to look
 * ISO-like, so the output can never depend on locale-formatting details.
 */
export function bangkokToday(now: Date = new Date()): string {
  const parts = bangkokParts.formatToParts(now);
  const get = (type: Intl.DateTimeFormatPartTypes) =>
    parts.find((p) => p.type === type)?.value ?? '';
  return `${get('year')}-${get('month')}-${get('day')}`;
}
