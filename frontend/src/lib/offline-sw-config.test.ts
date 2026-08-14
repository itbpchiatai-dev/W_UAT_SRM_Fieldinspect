/**
 * offline-sw-config.test.ts — round 8-4H.1: source-level assertions that the
 * Service Worker / PWA app-shell added in round 8-4H is fully RETIRED from
 * the build configuration.
 *
 * Mirrors this repo's established backend pattern (e.g.
 * backend/tests/security/test_record_create_supplier_scope_wiring.py) of
 * inspecting SOURCE TEXT rather than runtime-introspecting a built artifact.
 * Loaded via Vite's native `?raw` import suffix (no Node `fs`/`@types/node`
 * needed).
 *
 * Belt-and-suspenders alongside a REAL production build already manually
 * verified this round (see the Final Report's "Build Artifact Verification"
 * section): `npm run build`'s `dist/` output was inspected directly and
 * confirmed to contain NO `sw.js`, NO `workbox-*.js`, and NO
 * `manifest.webmanifest` — only `assets/` and `index.html`, same as any
 * plain (non-PWA) Vite build. This test file guards the SOURCE config that
 * produces that build, so a future edit can't silently re-introduce the
 * Service Worker without a deliberate, visible change here.
 */
import { describe, it, expect } from 'vitest';
import viteConfigSource from '../../vite.config.ts?raw';
import mainSource from '../main.tsx?raw';
import packageJson from '../../package.json';

describe('vite.config.ts — VitePWA is fully removed (round 8-4H.1)', () => {
  it('no VitePWA import', () => {
    expect(viteConfigSource).not.toContain('vite-plugin-pwa');
    expect(viteConfigSource).not.toContain('VitePWA');
  });

  it('no lingering PWA/Workbox config keys anywhere in the file', () => {
    const lower = viteConfigSource.toLowerCase();
    for (const forbidden of ['workbox', 'generatesw', 'injectmanifest', 'runtimecaching', 'navigatefallback', 'precache']) {
      expect(lower).not.toContain(forbidden);
    }
  });

  it('the plugins array is back to just react() — no unrelated plugin left behind', () => {
    expect(viteConfigSource).toMatch(/plugins:\s*\[react\(\)\]/);
  });

  it('server config (port/strictPort/proxy) is unchanged from before 8-4H', () => {
    expect(viteConfigSource).toContain('port: 5173');
    expect(viteConfigSource).toContain('strictPort: true');
    expect(viteConfigSource).toContain("proxy: { '/api':");
  });
});

describe('main.tsx — service worker registration replaced by retirement (round 8-4H.1)', () => {
  it('no virtual:pwa-register import or registerSW call', () => {
    expect(mainSource).not.toContain('virtual:pwa-register');
    expect(mainSource).not.toContain('registerSW(');
  });

  it('calls the retirement helper instead, without awaiting it (never blocks app startup)', () => {
    expect(mainSource).toContain("from './lib/serviceWorkerRetirement'");
    expect(mainSource).toMatch(/void\s+retireServiceWorker\(\)/);
  });
});

describe('package.json — vite-plugin-pwa dependency removed (round 8-4H.1)', () => {
  it('is not listed in devDependencies or dependencies', () => {
    const deps = { ...(packageJson.dependencies ?? {}), ...(packageJson.devDependencies ?? {}) };
    expect(Object.keys(deps)).not.toContain('vite-plugin-pwa');
  });
});
