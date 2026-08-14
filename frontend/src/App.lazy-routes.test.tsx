/**
 * Route-level code splitting (round 8-17A).
 *
 * The goal is that an unauthenticated /public/inspect visitor — a field
 * user on mobile data — does not download the Settings, FarmLog admin and
 * Reports screens. These tests pin the two halves of that:
 *
 *   1. STRUCTURE — App.tsx / routes.tsx must reach pages through dynamic
 *      `import()`, not top-level imports. Source-level assertions, because
 *      jsdom cannot observe real network chunk loading (see the note on
 *      Browser QA in the round report).
 *   2. BEHAVIOUR — lazy routes still render, still show a fallback while
 *      resolving, and still sit behind the same auth/permission guards.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { Suspense, lazy } from 'react';
import { LazyRoute, RouteFallback } from './components/LazyRoute';
import { useAuthStore } from './stores/auth';

vi.mock('./pages/farmlog/admin/Plots', () => ({
  // This suite exercises the guard + Suspense wiring, not the Plots page.
  // Rendering the real one would require the whole QueryClientProvider tree
  // and leak an unhandled error out of the test file.
  Plots: () => <div>__plots_page__</div>,
}));

// Vite's `?raw` suffix inlines the file as a string at transform time.
// Used instead of node:fs so this suite typechecks under the app's own
// browser tsconfig (no @types/node, and adding it would be a new dependency).
import APP_SRC from './App.tsx?raw';
import ROUTES_SRC from './routes.tsx?raw';
import VITE_CONFIG_SRC from '../vite.config.ts?raw';

// Pages that must NOT be reachable from the initial bundle. If any of these
// regains a static import, a /public/inspect visitor pays for it.
const MUST_BE_LAZY: Array<[string, string]> = [
  ['PublicInspect', './pages/farmlog/PublicInspect'],
  ['Dashboard', './pages/Dashboard'],
  ['SettingsIndex', './pages/SettingsIndex'],
  ['Users', './pages/settings/Users'],
  ['Roles', './pages/settings/Roles'],
  ['Permissions', './pages/settings/Permissions'],
  ['Menus', './pages/settings/Menus'],
  ['AuthSettings', './pages/settings/AuthSettings'],
  ['SystemLogs', './pages/settings/SystemLogs'],
  ['ActivityLogs', './pages/settings/ActivityLogs'],
  ['DatabaseConnections', './pages/settings/DatabaseConnections'],
  ['QuerySandbox', './pages/settings/QuerySandbox'],
];

const MODULE_ROUTE_PAGES: Array<[string, string]> = [
  ['Suppliers', './pages/farmlog/admin/Suppliers'],
  ['Plots', './pages/farmlog/admin/Plots'],
  ['PlotDetail', './pages/farmlog/admin/PlotDetail'],
  ['Fields', './pages/farmlog/admin/Fields'],
  ['MasterData', './pages/farmlog/admin/MasterData'],
  ['InspectionProtocols', './pages/farmlog/admin/InspectionProtocols'],
  ['RecordList', './pages/farmlog/RecordList'],
  ['RecordForm', './pages/farmlog/RecordForm'],
  ['RecordPreview', './pages/farmlog/RecordPreview'],
  ['ReportsPage', './pages/farmlog/reports/ReportsPage'],
];

/** A static `import ... from '<path>'` at the top of the file. */
function hasStaticImport(source: string, path: string): boolean {
  return new RegExp(`^import\\s[^;]*from\\s+['"]${path}['"]`, 'm').test(source);
}

/** A dynamic `import('<path>')` inside a lazy() loader. */
function hasDynamicImport(source: string, path: string): boolean {
  return source.includes(`import('${path}')`);
}

describe('structure — pages are reached by dynamic import, not static', () => {
  it.each(MUST_BE_LAZY)('App.tsx loads %s lazily', (_name, path) => {
    expect(hasStaticImport(APP_SRC, path)).toBe(false);
    expect(hasDynamicImport(APP_SRC, path)).toBe(true);
  });

  it.each(MODULE_ROUTE_PAGES)('routes.tsx loads %s lazily', (_name, path) => {
    expect(hasStaticImport(ROUTES_SRC, path)).toBe(false);
    expect(hasDynamicImport(ROUTES_SRC, path)).toBe(true);
  });

  it('keeps the routing shell eager — it decides what to render at all', () => {
    // Deferring these would only add a waterfall before first paint, and
    // RequireAuth/RequirePermission must be synchronously available so an
    // unauthorised deep link is bounced without first fetching a chunk.
    for (const path of [
      './components/AuthBootstrap',
      './components/RequireAuth',
      './components/RequirePermission',
      './components/Layout/AppLayout',
      './components/LazyRoute',
    ]) {
      expect(hasStaticImport(APP_SRC, path)).toBe(true);
    }
  });

  it('does not introduce manualChunks — Vite automatic splitting is enough', () => {
    // Round 8-17A brief: no manual chunking before seeing a real build.
    expect(VITE_CONFIG_SRC).not.toContain('manualChunks');
  });
});

describe('public inspect isolation', () => {
  it('reaches PublicInspect without statically importing any Settings or admin page', () => {
    // The whole point of the round: nothing on the /public/inspect path may
    // pull Settings/Admin/Reports into the same initial dependency graph.
    const forbidden = [
      './pages/settings/Users',
      './pages/settings/Roles',
      './pages/settings/AuthSettings',
      './pages/settings/DatabaseConnections',
      './pages/farmlog/admin/Plots',
      './pages/farmlog/reports/ReportsPage',
    ];
    for (const path of forbidden) {
      expect(hasStaticImport(APP_SRC, path)).toBe(false);
      expect(hasStaticImport(ROUTES_SRC, path)).toBe(false);
    }
  });

  it('PublicInspect is not imported by routes.tsx at all (module-route file is auth-gated)', () => {
    expect(ROUTES_SRC).not.toContain('PublicInspect');
  });
});

describe('behaviour — LazyRoute boundary', () => {
  it('renders an accessible fallback while a lazy module resolves', async () => {
    let resolveModule: (v: { default: () => JSX.Element }) => void = () => {};
    const Pending = lazy(
      () => new Promise<{ default: () => JSX.Element }>((r) => { resolveModule = r; }),
    );

    render(
      <LazyRoute>
        <Pending />
      </LazyRoute>,
    );

    // Fallback is announced to assistive tech, not just visually present.
    const status = screen.getByRole('status');
    expect(status).toBeTruthy();
    expect(status.getAttribute('aria-live')).toBe('polite');
    expect(status.textContent).toContain('กำลังโหลด');

    resolveModule({ default: () => <div>__resolved_page__</div> });
    expect(await screen.findByText('__resolved_page__')).toBeTruthy();
    // Fallback is gone once the real page mounts.
    expect(screen.queryByRole('status')).toBeNull();
  });

  it('RouteFallback reserves height so the swap to content does not jump the layout', () => {
    render(<RouteFallback />);
    expect(screen.getByRole('status').className).toContain('min-h-');
  });

  it('a rejected dynamic import surfaces as a caught error, not an unhandled rejection', async () => {
    // Guards the test suite itself: without an error boundary a rejected
    // lazy() import escapes as an unhandled rejection and can fail an
    // unrelated test file. Also mirrors the real-world case of a stale
    // chunk 404ing after a redeploy.
    const onError = vi.fn();
    class Boundary extends (await import('react')).Component<
      { children: React.ReactNode },
      { failed: boolean }
    > {
      state = { failed: false };
      static getDerivedStateFromError() { return { failed: true }; }
      componentDidCatch(error: Error) { onError(error); }
      render() { return this.state.failed ? <div>__chunk_load_failed__</div> : this.props.children; }
    }

    const Broken = lazy(() => Promise.reject(new Error('simulated chunk load failure')));

    render(
      <Boundary>
        <Suspense fallback={<div>__loading__</div>}>
          <Broken />
        </Suspense>
      </Boundary>,
    );

    expect(await screen.findByText('__chunk_load_failed__')).toBeTruthy();
    await waitFor(() => expect(onError).toHaveBeenCalled());
  });
});

describe('behaviour — guards still wrap lazy pages', () => {
  beforeEach(() => {
    useAuthStore.setState({ permissionKeys: new Set(['plots.read']) });
  });
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it('a permitted lazy module route renders after resolving', async () => {
    const { MODULE_ROUTES } = await import('./routes');
    render(
      <MemoryRouter initialEntries={['/farmlog/admin/plots']}>
        <Routes>{MODULE_ROUTES}</Routes>
      </MemoryRouter>,
    );
    // Resolves past the Suspense fallback to the page itself.
    expect(await screen.findByText('__plots_page__')).toBeTruthy();
    expect(screen.queryByRole('status')).toBeNull();
  });

  it('denies a lazy route when the permission is missing — 403 panel, page never mounts', async () => {
    useAuthStore.setState({ permissionKeys: new Set([]) });
    const { MODULE_ROUTES } = await import('./routes');
    render(
      <MemoryRouter initialEntries={['/farmlog/admin/plots']}>
        <Routes>{MODULE_ROUTES}</Routes>
      </MemoryRouter>,
    );
    // RequirePermission sits OUTSIDE LazyRoute, so the guard answers first
    // and the chunk is never requested.
    expect(await screen.findByRole('heading')).toBeTruthy();
    expect(screen.queryByText('__plots_page__')).toBeNull();
    expect(screen.queryByRole('status')).toBeNull();
  });

  it('permission gate wraps the Suspense boundary, not the other way round', () => {
    // Ordering is load-bearing: RequirePermission renders children only when
    // allowed, so nesting LazyRoute inside it means an unauthorised user
    // never triggers the dynamic import.
    expect(ROUTES_SRC).toContain('><LazyRoute>');
    expect(ROUTES_SRC).not.toContain('<LazyRoute><RequirePermission');
  });
});

describe('behaviour — public and protected routes still resolve', () => {
  it('renders a lazy public route with no auth involved', async () => {
    const Public = lazy(() =>
      Promise.resolve({ default: () => <div>__public_page__</div> }),
    );
    render(
      <MemoryRouter initialEntries={['/public/inspect']}>
        <Routes>
          <Route path="/public/inspect" element={<LazyRoute><Public /></LazyRoute>} />
        </Routes>
      </MemoryRouter>,
    );
    expect(await screen.findByText('__public_page__')).toBeTruthy();
  });

  it('NotFound stays eager so an unknown path never waits on a chunk', () => {
    expect(APP_SRC).toContain('function NotFound()');
    expect(APP_SRC).not.toContain("lazy(() => import('./pages/NotFound')");
  });
});
