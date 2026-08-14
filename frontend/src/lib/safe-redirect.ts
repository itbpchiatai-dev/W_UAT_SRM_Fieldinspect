/**
 * safeReturn — validate untrusted `?return=<path>` query values before
 * feeding them into react-router's navigate().
 *
 * Closes Round-4 HIGH-3. The login page and the RequireAuth guard both
 * accept a return path from the URL; without this gate, a phishing
 * link like `/login?return=//evil.example/login` would bounce the
 * signed-in user to an attacker-controlled origin.
 *
 * Accepted: a single internal path starting with exactly one '/' and
 * containing only path/query characters (e.g. /settings/users?tab=pending).
 *
 * Rejected (falls back to `defaultPath`):
 *   - empty / null / longer than MAX_LEN
 *   - protocol-relative (//evil.example)
 *   - absolute URL (https://..., http://..., ftp://...)
 *   - javascript:, data:, vbscript:
 *   - backslash confusion (/\evil — IE/Edge legacy parse it as protocol)
 *   - any non-printable / control character
 *   - whitespace anywhere (browsers strip but proxies don't always)
 *
 * Callers should use the returned string verbatim — no further escape.
 */
export const SAFE_RETURN_MAX_LEN = 1024;
export const SAFE_RETURN_DEFAULT = '/';

export function safeReturn(raw: unknown, defaultPath: string = SAFE_RETURN_DEFAULT): string {
  if (typeof raw !== 'string') return defaultPath;
  const v = raw;
  if (v.length === 0 || v.length > SAFE_RETURN_MAX_LEN) return defaultPath;

  // Reject any whitespace or control char — browsers tolerate them
  // inconsistently and the difference becomes a bypass surface.
  // eslint-disable-next-line no-control-regex
  if (/[\s\x00-\x1f\x7f]/.test(v)) return defaultPath;

  // Reject backslash anywhere — legacy parsers (and some proxies) treat
  // /\foo as a protocol-relative reference.
  if (v.includes('\\')) return defaultPath;

  // Must start with EXACTLY one '/'. Protocol-relative '//host' is the
  // classic open-redirect bypass and is rejected here.
  if (!v.startsWith('/')) return defaultPath;
  if (v.startsWith('//')) return defaultPath;

  // Reject the dangerous URL schemes even if encoded into a relative path.
  // (We've already required the value to start with '/', so a literal
  // 'javascript:' prefix can't reach this point — but defence in depth:
  // re-check the whole string after lowercasing in case future relaxations
  // open a window.)
  const lower = v.toLowerCase();
  if (
    lower.includes('javascript:') ||
    lower.includes('data:') ||
    lower.includes('vbscript:')
  ) {
    return defaultPath;
  }
  return v;
}

export default safeReturn;
