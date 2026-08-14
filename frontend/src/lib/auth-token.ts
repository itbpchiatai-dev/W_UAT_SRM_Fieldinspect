/**
 * Canonical access-token store for the host SPA.
 *
 * Single source of truth — the scaffold-emitted axios client
 * (api/client.ts) and any auth-aware pages import from here.
 *
 * Storage strategy:
 *   - Module-scoped variable: hot-path read for every request interceptor.
 *   - sessionStorage mirror: survives tab reload (cleared when tab closes).
 *     We deliberately avoid localStorage — a long-lived bearer token in
 *     localStorage is the classic XSS exfiltration target.
 *
 * The refresh token lives in an httpOnly cookie owned by the backend —
 * never touched from JS. See api/client.ts for the silent-refresh flow.
 */
const STORAGE_KEY = 'auth.accessToken';

let _accessToken: string | null = null;

function _readFromSession(): string | null {
  try {
    return typeof sessionStorage !== 'undefined'
      ? sessionStorage.getItem(STORAGE_KEY)
      : null;
  } catch {
    return null;
  }
}

function _writeToSession(token: string | null): void {
  try {
    if (typeof sessionStorage === 'undefined') return;
    if (token === null) {
      sessionStorage.removeItem(STORAGE_KEY);
    } else {
      sessionStorage.setItem(STORAGE_KEY, token);
    }
  } catch {
    /* swallow — quota / privacy mode / SSR */
  }
}

// Hydrate from sessionStorage on first load (post tab-reload).
_accessToken = _readFromSession();

export function getAccessToken(): string | null {
  if (_accessToken !== null) return _accessToken;
  // Defensive re-read — in case the in-memory copy was cleared in another
  // context (e.g. test reset) but sessionStorage still has it.
  _accessToken = _readFromSession();
  return _accessToken;
}

export function setAccessToken(token: string | null): void {
  _accessToken = token;
  _writeToSession(token);
}

export function clearAccessToken(): void {
  setAccessToken(null);
}

export function authHeaders(): { Authorization: string } | Record<string, never> {
  const t = getAccessToken();
  return t ? { Authorization: `Bearer ${t}` } : {};
}
