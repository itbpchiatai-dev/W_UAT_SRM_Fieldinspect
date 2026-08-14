/**
 * Thai mobile number helper — UX-layer normalization/formatting only (round
 * 8-3C). The BACKEND (app/core/phone.py::normalize_thai_mobile) is the
 * authority on validation; this exists purely so the Plot access-phone forms
 * can show an immediate Thai error message and a readable formatted display
 * before a request ever reaches the server. Never silently truncates or
 * deduplicates — a caller (the shared PlotAccessPhoneFields component) is
 * responsible for surfacing duplicate-number errors to the user.
 */

/** Canonical Thai mobile: leading 0, then 6/8/9, then 8 more digits — same
 * shape the backend's CHECK constraint and normalizer enforce. */
export const THAI_MOBILE_RE = /^0[689][0-9]{8}$/;

const FORMATTING_CHARS_RE = /[\s()\-.]/g;

/**
 * Canonicalize a Thai mobile number to ^0[689][0-9]{8}$, mirroring the
 * backend's app.core.phone.normalize_thai_mobile: trims, strips spaces/
 * dashes/parens/dots, folds a +66/66 country code to a leading 0. Throws
 * Error (Thai message) on anything that doesn't resolve to the canonical
 * shape — never truncates or "fixes" a malformed number.
 */
export function normalizeThaiMobile(raw: string): string {
  const stripped = raw.trim().replace(FORMATTING_CHARS_RE, '');
  if (!stripped) {
    throw new Error('กรุณากรอกเบอร์โทรศัพท์');
  }

  let candidate = stripped;
  if (candidate.startsWith('+66')) {
    candidate = '0' + candidate.slice(3);
  } else if (candidate.startsWith('66')) {
    candidate = '0' + candidate.slice(2);
  }

  if (!THAI_MOBILE_RE.test(candidate)) {
    throw new Error('รูปแบบเบอร์โทรศัพท์ไม่ถูกต้อง (ต้องเป็นเบอร์มือถือไทย 10 หลัก)');
  }
  return candidate;
}

/** True/false wrapper around normalizeThaiMobile for callers that just need a
 * boolean check without handling the thrown error. */
export function isValidThaiMobile(raw: string): boolean {
  try {
    normalizeThaiMobile(raw);
    return true;
  } catch {
    return false;
  }
}

/** Every character is a digit or a common phone-formatting character
 * (space/dash/parens/dot/leading +) — matches ONLY things a user could be
 * typing as a phone number, never a plot code/name/province (which always
 * contain at least one letter or Thai character in practice). Round
 * 8-17A.2's Plots search box uses this to decide the secure POST
 * search-by-phone path vs the plain GET q path — deliberately loose (also
 * matches a too-short/too-long typo) so a malformed phone attempt is
 * reported as a phone error, never silently sent as GET q (which would leak
 * the partial digits into the URL/access log). */
const PHONE_LIKE_RE = /^[+0-9\s()\-.]+$/;

export function looksLikePhoneAttempt(raw: string): boolean {
  const trimmed = raw.trim();
  return trimmed.length > 0 && PHONE_LIKE_RE.test(trimmed);
}

/**
 * Display formatter: canonical digits → "084-555-2162" (3-3-4 grouping).
 * Returns the input unchanged if it isn't exactly 10 canonical digits (e.g.
 * already-formatted or unexpected input) — never throws, this is display-only.
 */
export function formatThaiMobile(canonical: string): string {
  if (!THAI_MOBILE_RE.test(canonical)) return canonical;
  return `${canonical.slice(0, 3)}-${canonical.slice(3, 6)}-${canonical.slice(6)}`;
}
