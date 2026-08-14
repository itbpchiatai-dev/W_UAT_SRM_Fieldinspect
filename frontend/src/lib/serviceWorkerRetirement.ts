/**
 * serviceWorkerRetirement — round 8-4H.1. Round 8-4H registered a Service
 * Worker (via vite-plugin-pwa) for an offline app-shell; that offline
 * feature is now disabled ("เข้าสู่ระบบแล้ว แต่เตรียมข้อมูลออฟไลน์ไม่สำเร็จ"
 * instability), and vite.config.ts no longer builds a Service Worker at all.
 * A device that already installed the OLD one, though, keeps running it
 * until something explicitly unregisters it — this module is that
 * best-effort cleanup, called once from main.tsx on every app load.
 *
 * Deliberately kept as its own module (not deleted once retirement "is
 * done") — round 8-4H.1 requirement: a browser may not visit the app again
 * for a while after this ships, so the unregister call needs to keep running
 * on every load for the foreseeable future, not just once. Safe to delete in
 * a LATER round once there's reasonable confidence no relevant browser still
 * has the old worker installed.
 *
 * Round 8-4H.2 hardening — the Service Worker API only ever exposes
 * same-origin registrations, so the round 8-4H.1 origin-only check could
 * unregister a Service Worker belonging to a DIFFERENT app hosted on the
 * same origin under a different path (e.g. `/other-app/`). Tightened to
 * match this app's EXACT scope instead:
 *   - appScope = new URL(import.meta.env.BASE_URL, location.origin).href
 *     (round 8-4H's VitePWA config never set a custom `scope`/`base`, so the
 *     Service Worker it registered used Vite's own BASE_URL as its scope —
 *     the same value this module now reads to compute the exact match.)
 *   - a registration is only ever unregistered when its OWN normalized
 *     scope === appScope exactly. Same-origin-but-different-path and
 *     foreign-origin registrations are both left untouched.
 *   - the retired precache cache name is likewise matched by the FULL key
 *     (`workbox-precache-v2-${appScope}`), never by prefix alone — the
 *     prefix `workbox-precache-v2-` was confirmed against this project's own
 *     round 8-4H build output (dist/workbox-*.js, cacheNameDetails); the
 *     `-${appScope}` suffix follows Workbox's own documented default cache
 *     naming (workbox-core's cacheNames: `[prefix, name, suffix].join('-')`,
 *     where `suffix` defaults to `self.registration.scope` — verified
 *     directly against workbox-core's source on 2026-07-20, since
 *     vite-plugin-pwa/workbox were already fully removed from this repo in
 *     round 8-4H.1 and cannot be reinstalled this round to regenerate a
 *     fresh build artifact). If some historical build ever used a
 *     non-default cache name, this exact match simply won't fire for it —
 *     failing to clean up a leftover cache is an acceptable, safe outcome;
 *     deleting the WRONG cache is not.
 *
 * Safety rules (non-negotiable):
 *   - Only ever unregisters a registration whose scope, normalized, equals
 *     this app's exact scope — never "same origin", never a prefix/substring
 *     match on scope.
 *   - Only ever deletes a Cache Storage entry whose name exactly equals the
 *     evidence-backed retired cache key for this app's exact scope — never a
 *     prefix/substring match, never a blanket
 *     `caches.keys().forEach(caches.delete)`.
 *   - Never inspects, logs, or reads the CONTENTS of any registration or
 *     cache entry — only names/scopes, for matching purposes.
 *   - Every step is wrapped so a failure (missing API, a rejected promise,
 *     a malformed scope, anything) can never throw out of
 *     retireServiceWorker() and never blocks app startup — main.tsx calls
 *     this without awaiting it.
 */

const WORKBOX_PRECACHE_PREFIX = 'workbox-precache-v2-';

/** Pure — no globals touched. Mirrors exactly how this app's Service Worker
 * scope was determined at registration time (round 8-4H's VitePWA config
 * used Vite's own BASE_URL, unmodified). Exported so tests can exercise
 * root/subpath BASE_URL values directly, without mutating
 * `import.meta.env`. */
export function buildAppScope(baseUrl: string, origin: string): string {
  return new URL(baseUrl, origin).href;
}

/** Pure — the exact (never prefix-matched) retired precache cache name for
 * a given app scope. See the module docstring for the evidence behind this
 * formula. */
export function buildRetiredWorkboxCacheKey(appScope: string): string {
  return `${WORKBOX_PRECACHE_PREFIX}${appScope}`;
}

function currentAppScope(): string {
  const baseUrl = import.meta.env.BASE_URL;
  const origin = typeof location !== 'undefined' ? location.origin : '';
  return buildAppScope(baseUrl, origin);
}

async function unregisterAppServiceWorkers(appScope: string): Promise<void> {
  if (typeof navigator === 'undefined' || !navigator.serviceWorker) return;
  let registrations: readonly ServiceWorkerRegistration[];
  try {
    registrations = await navigator.serviceWorker.getRegistrations();
  } catch {
    return;
  }
  for (const registration of registrations) {
    try {
      const normalizedScope = new URL(registration.scope).href;
      // Exact scope match only — same origin alone is NEVER sufficient (the
      // Service Worker API already restricts getRegistrations() to same-
      // origin entries, so an origin-only check would unregister every app
      // on this origin, not just this one).
      if (normalizedScope !== appScope) continue;
      await registration.unregister();
    } catch {
      // best-effort — one registration failing to unregister (or a
      // malformed scope that fails to parse) must never stop the loop or
      // bubble up.
    }
  }
}

async function cleanupRetiredWorkboxPrecache(appScope: string): Promise<void> {
  if (typeof caches === 'undefined') return;
  let keys: string[];
  try {
    keys = await caches.keys();
  } catch {
    return;
  }
  const retiredKey = buildRetiredWorkboxCacheKey(appScope);
  for (const key of keys) {
    if (key !== retiredKey) continue; // exact key match only — never a prefix/substring sweep
    try {
      await caches.delete(key);
    } catch {
      // best-effort — one failed delete must never stop the loop.
    }
  }
}

/** Best-effort retirement of round 8-4H's Service Worker + its precache,
 * scoped exactly to this app. Never throws, never awaited by main.tsx,
 * never blocks app startup. */
export async function retireServiceWorker(): Promise<void> {
  const appScope = currentAppScope();
  await unregisterAppServiceWorkers(appScope).catch(() => {});
  await cleanupRetiredWorkboxPrecache(appScope).catch(() => {});
}
