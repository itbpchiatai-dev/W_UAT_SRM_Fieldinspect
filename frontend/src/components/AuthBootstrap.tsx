/**
 * AuthBootstrap — silent-refresh + session hydration on app mount.
 *
 * Always runs once on mount (Phase B onwards). The `enabled` prop is
 * retained for tests + the rare case where you need to disable the
 * bootstrap in a story/preview environment. Default: `true`.
 *
 * Behavior:
 *   refresh OK → setAccessToken + hydrate user/permissions/menus.
 *   refresh 401 → leave the Zustand store empty; <RequireAuth/> will
 *                 redirect to /login when a protected route mounts.
 */
import { useEffect } from 'react';
import type { ReactNode } from 'react';
import { setAccessToken, clearAccessToken } from '../lib/auth-token';
import { useAuthStore } from '../stores/auth';

export interface AuthBootstrapProps {
  /** Default true. Set false in tests/stories to skip the round-trip. */
  enabled?: boolean;
  refreshUrl?: string;
  children?: ReactNode;
}

export function AuthBootstrap({
  enabled = true,
  refreshUrl = '/api/v1/auth/refresh',
  children,
}: AuthBootstrapProps) {
  const setLoading = useAuthStore((s) => s.setLoading);
  const hydrate = useAuthStore((s) => s.hydrateFromServer);

  useEffect(() => {
    if (!enabled) {
      setLoading(false);
      return;
    }
    let cancelled = false;
    setLoading(true);
    (async () => {
      // TEMPORARY diagnostic timing (round "Debug Plots Load 1") — this
      // bootstrap refresh uses a bare `fetch()`, not apiClient, so it's
      // outside apiClient's interceptor-based instrumentation. It's also
      // the one refresh call with NO client-side timeout at all — if it's
      // the source of a multi-second stall, this is where it'd show up.
      const bootstrapT0 = import.meta.env.DEV ? performance.now() : 0;
      try {
        const res = await fetch(refreshUrl, {
          method: 'POST',
          credentials: 'include',
          headers: { 'Content-Type': 'application/json' },
        });
        if (import.meta.env.DEV) {
          console.debug(`[api-timing] POST ${refreshUrl} (bootstrap) -> ${res.status} (${Math.round(performance.now() - bootstrapT0)}ms)`);
        }
        if (cancelled) return;
        if (!res.ok) {
          // 401/403 — anonymous. Clear any stale token and bail.
          clearAccessToken();
          return;
        }
        const data = await res.json().catch(() => ({}));
        const token: string | undefined = data?.accessToken ?? data?.access_token;
        if (token && !cancelled) {
          setAccessToken(token);
          await hydrate();
        } else {
          clearAccessToken();
        }
      } catch (err) {
        if (import.meta.env.DEV) {
          console.debug(`[api-timing] POST ${refreshUrl} (bootstrap) -> FAILED ${String(err)} (${Math.round(performance.now() - bootstrapT0)}ms)`);
        }
        // Network or backend-down — render the app anonymous.
        clearAccessToken();
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [enabled, refreshUrl, hydrate, setLoading]);

  // Re-sync permissions + menus when the window regains focus, so a
  // per-user override granted by an admin shows up in this session
  // without re-login. Throttled to once per 30s; silent on failure
  // (see refreshPermissions in stores/auth.ts).
  useEffect(() => {
    if (!enabled) return;
    let lastRun = 0;
    const onFocus = () => {
      const now = Date.now();
      if (now - lastRun < 30_000) return;
      lastRun = now;
      void useAuthStore.getState().refreshPermissions();
    };
    window.addEventListener('focus', onFocus);
    return () => window.removeEventListener('focus', onFocus);
  }, [enabled]);

  return <>{children}</>;
}

export default AuthBootstrap;
