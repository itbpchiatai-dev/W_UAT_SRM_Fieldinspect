/**
 * serviceWorkerRetirement.test.ts — round 8-4H.1, hardened round 8-4H.2.
 * Verifies the best-effort retirement helper unregisters/cleans up ONLY
 * this app's EXACT scope (never "same origin", never a cache-name prefix
 * sweep), and never throws even when the Service Worker / Cache Storage
 * APIs are entirely absent or a registration's scope is malformed.
 */
import { afterEach, describe, expect, it, vi } from 'vitest';
import { retireServiceWorker, buildAppScope, buildRetiredWorkboxCacheKey } from './serviceWorkerRetirement';

// This project's vite.config.ts sets no `base`, so BASE_URL is the Vite
// default '/' — appScope is therefore this test run's own origin + '/'.
// Reading it from window.location.origin (rather than hardcoding a host)
// keeps these tests correct under whatever jsdom test URL vitest uses.
const APP_SCOPE = buildAppScope('/', window.location.origin);
const OTHER_APP_SCOPE = buildAppScope('/other-app/', window.location.origin);
const CHILD_SCOPE = buildAppScope('/fieldinspect-child/', window.location.origin);
const FOREIGN_ORIGIN_SCOPE = 'https://not-this-app.example/';

function stubServiceWorker(registrations: { scope: string; unregister: () => Promise<boolean> }[]) {
  Object.defineProperty(navigator, 'serviceWorker', {
    value: { getRegistrations: vi.fn().mockResolvedValue(registrations) },
    configurable: true,
  });
}

function stubCaches(keys: string[], deleteImpl?: (key: string) => Promise<boolean>) {
  (globalThis as { caches?: unknown }).caches = {
    keys: vi.fn().mockResolvedValue(keys),
    delete: vi.fn(deleteImpl ?? (() => Promise.resolve(true))),
  };
}

afterEach(() => {
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  delete (navigator as any).serviceWorker;
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  delete (globalThis as any).caches;
  vi.restoreAllMocks();
});

describe('buildAppScope — pure BASE_URL -> scope computation (round 8-4H.2)', () => {
  it('a root BASE_URL normalizes to origin + "/"', () => {
    expect(buildAppScope('/', 'https://example.com')).toBe('https://example.com/');
  });

  it('a subpath BASE_URL normalizes to origin + the subpath', () => {
    expect(buildAppScope('/fieldinspect/', 'https://example.com')).toBe('https://example.com/fieldinspect/');
  });
});

describe('buildRetiredWorkboxCacheKey — pure exact cache key computation (round 8-4H.2)', () => {
  it('joins the evidence-backed prefix with the exact app scope', () => {
    expect(buildRetiredWorkboxCacheKey('https://example.com/')).toBe('workbox-precache-v2-https://example.com/');
  });

  it('a subpath scope produces a key distinct from the root scope\'s key', () => {
    const root = buildRetiredWorkboxCacheKey('https://example.com/');
    const sub = buildRetiredWorkboxCacheKey('https://example.com/fieldinspect/');
    expect(root).not.toBe(sub);
  });
});

describe('retireServiceWorker — unregistration matches this app\'s EXACT scope (round 8-4H.2)', () => {
  it('unregisters a registration whose scope exactly equals this app\'s scope', async () => {
    const unregister = vi.fn().mockResolvedValue(true);
    stubServiceWorker([{ scope: APP_SCOPE, unregister }]);
    stubCaches([]);

    await retireServiceWorker();

    expect(unregister).toHaveBeenCalledOnce();
  });

  it('never unregisters a same-origin registration belonging to a DIFFERENT app (/other-app/)', async () => {
    const unregister = vi.fn().mockResolvedValue(true);
    stubServiceWorker([{ scope: OTHER_APP_SCOPE, unregister }]);
    stubCaches([]);

    await retireServiceWorker();

    expect(unregister).not.toHaveBeenCalled();
  });

  it('never unregisters a same-origin registration scoped to a child path (/fieldinspect-child/)', async () => {
    const unregister = vi.fn().mockResolvedValue(true);
    stubServiceWorker([{ scope: CHILD_SCOPE, unregister }]);
    stubCaches([]);

    await retireServiceWorker();

    expect(unregister).not.toHaveBeenCalled();
  });

  it('never unregisters a registration scoped to a DIFFERENT origin', async () => {
    const unregister = vi.fn().mockResolvedValue(true);
    stubServiceWorker([{ scope: FOREIGN_ORIGIN_SCOPE, unregister }]);
    stubCaches([]);

    await retireServiceWorker();

    expect(unregister).not.toHaveBeenCalled();
  });

  it('never throws and never unregisters when a registration has a malformed scope', async () => {
    const unregister = vi.fn().mockResolvedValue(true);
    stubServiceWorker([{ scope: 'not a valid url::', unregister }]);
    stubCaches([]);

    await expect(retireServiceWorker()).resolves.toBeUndefined();
    expect(unregister).not.toHaveBeenCalled();
  });

  it('with multiple registrations, unregisters ONLY the exact-app-scope one', async () => {
    const exact = vi.fn().mockResolvedValue(true);
    const otherApp = vi.fn().mockResolvedValue(true);
    const child = vi.fn().mockResolvedValue(true);
    const foreign = vi.fn().mockResolvedValue(true);
    stubServiceWorker([
      { scope: OTHER_APP_SCOPE, unregister: otherApp },
      { scope: APP_SCOPE, unregister: exact },
      { scope: CHILD_SCOPE, unregister: child },
      { scope: FOREIGN_ORIGIN_SCOPE, unregister: foreign },
    ]);
    stubCaches([]);

    await retireServiceWorker();

    expect(exact).toHaveBeenCalledOnce();
    expect(otherApp).not.toHaveBeenCalled();
    expect(child).not.toHaveBeenCalled();
    expect(foreign).not.toHaveBeenCalled();
  });

  it('never throws when the exact-scope registration\'s unregister() itself rejects', async () => {
    const failing = vi.fn().mockRejectedValue(new Error('boom'));
    stubServiceWorker([{ scope: APP_SCOPE, unregister: failing }]);
    stubCaches([]);

    await expect(retireServiceWorker()).resolves.toBeUndefined();
  });

  it('never throws when navigator.serviceWorker is entirely absent', async () => {
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    delete (navigator as any).serviceWorker;
    stubCaches([]);

    await expect(retireServiceWorker()).resolves.toBeUndefined();
  });

  it('never throws when getRegistrations() itself rejects', async () => {
    Object.defineProperty(navigator, 'serviceWorker', {
      value: { getRegistrations: vi.fn().mockRejectedValue(new Error('boom')) },
      configurable: true,
    });
    stubCaches([]);

    await expect(retireServiceWorker()).resolves.toBeUndefined();
  });
});

describe('retireServiceWorker — Cache Storage cleanup matches this app\'s EXACT retired cache key (round 8-4H.2)', () => {
  it('deletes only the cache key that exactly matches this app\'s retired precache name', async () => {
    stubServiceWorker([]);
    const retiredKey = buildRetiredWorkboxCacheKey(APP_SCOPE);
    const deleted: string[] = [];
    stubCaches(
      [retiredKey, 'some-other-app-cache', 'another-unrelated-key'],
      async (key) => { deleted.push(key); return true; },
    );

    await retireServiceWorker();

    expect(deleted).toEqual([retiredKey]);
  });

  it('never deletes a cache key with the same Workbox prefix but a DIFFERENT scope', async () => {
    stubServiceWorker([]);
    const differentScopeKey = buildRetiredWorkboxCacheKey(buildAppScope('/some/other/path/', window.location.origin));
    const deleteSpy = vi.fn().mockResolvedValue(true);
    stubCaches([differentScopeKey], deleteSpy);

    await retireServiceWorker();

    expect(deleteSpy).not.toHaveBeenCalled();
  });

  it('never deletes a same-origin app\'s OWN exact retired cache key (/other-app/)', async () => {
    stubServiceWorker([]);
    const otherAppKey = buildRetiredWorkboxCacheKey(OTHER_APP_SCOPE);
    const deleteSpy = vi.fn().mockResolvedValue(true);
    stubCaches([otherAppKey], deleteSpy);

    await retireServiceWorker();

    expect(deleteSpy).not.toHaveBeenCalled();
  });

  it('never deletes an unrelated, non-Workbox cache key', async () => {
    stubServiceWorker([]);
    const deleteSpy = vi.fn().mockResolvedValue(true);
    stubCaches(['completely-unrelated-cache-1', 'completely-unrelated-cache-2'], deleteSpy);

    await retireServiceWorker();

    expect(deleteSpy).not.toHaveBeenCalled();
  });

  it('caches.delete is never called at all when no key is an exact match — no prefix-based sweep', async () => {
    stubServiceWorker([]);
    const deleteSpy = vi.fn().mockResolvedValue(true);
    stubCaches(
      [buildRetiredWorkboxCacheKey(OTHER_APP_SCOPE), buildRetiredWorkboxCacheKey(FOREIGN_ORIGIN_SCOPE)],
      deleteSpy,
    );

    await retireServiceWorker();

    expect(deleteSpy).not.toHaveBeenCalled();
  });

  it('never throws when the Cache Storage API is entirely absent', async () => {
    stubServiceWorker([]);
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    delete (globalThis as any).caches;

    await expect(retireServiceWorker()).resolves.toBeUndefined();
  });

  it('never throws when caches.keys() itself rejects', async () => {
    stubServiceWorker([]);
    (globalThis as { caches?: unknown }).caches = {
      keys: vi.fn().mockRejectedValue(new Error('boom')),
      delete: vi.fn(),
    };

    await expect(retireServiceWorker()).resolves.toBeUndefined();
  });

  it('never throws when caches.delete() itself rejects for the exact-match key', async () => {
    stubServiceWorker([]);
    const retiredKey = buildRetiredWorkboxCacheKey(APP_SCOPE);
    stubCaches([retiredKey], async () => { throw new Error('boom'); });

    await expect(retireServiceWorker()).resolves.toBeUndefined();
  });
});
