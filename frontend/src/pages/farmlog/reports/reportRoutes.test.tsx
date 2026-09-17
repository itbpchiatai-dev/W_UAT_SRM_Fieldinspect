/**
 * Round 29 — four reports, four routes, four permissions.
 *
 * Every report endpoint used to be gated by `plots.read`, and the two reports
 * shared one page with tabs. A Supplier holding plots.read therefore ran the
 * internal reports, and there was no way to grant one report without the
 * other. Each report is now its own route with its own key, and the Supplier
 * has its own pair.
 */
import { describe, expect, it } from 'vitest';

import { MODULE_ROUTES } from '../../../routes';

type Routeish = { props?: { path?: string; element?: unknown } };

function routeFor(path: string) {
  const found = (MODULE_ROUTES as unknown as Routeish[]).find((r) => r.props?.path === path);
  if (!found) throw new Error(`no route for ${path}`);
  return found;
}

/** The `perm` a route's <RequirePermission> wrapper carries. */
function permOf(path: string): string | undefined {
  const el = routeFor(path).props?.element as
    | { props?: { perm?: string } }
    | undefined;
  return el?.props?.perm;
}

describe('report routes (round 29)', () => {
  it.each([
    ['/farmlog/reports/plot-status', 'reports.plot_status'],
    ['/farmlog/reports/cycle-yield', 'reports.cycle_yield'],
    ['/farmlog/reports/supplier/plot-status', 'reports.plot_status_supplier'],
    ['/farmlog/reports/supplier/cycle-yield', 'reports.cycle_yield_supplier'],
  ])('%s is gated by %s', (path, perm) => {
    expect(permOf(path)).toBe(perm);
  });

  it('no report route is gated by plots.read any more', () => {
    for (const path of [
      '/farmlog/reports/plot-status',
      '/farmlog/reports/cycle-yield',
      '/farmlog/reports/supplier/plot-status',
      '/farmlog/reports/supplier/cycle-yield',
    ]) {
      expect(permOf(path)).not.toBe('plots.read');
    }
  });

  it('every report has a route of its own — typing a URL is not a way in', () => {
    const paths = (MODULE_ROUTES as unknown as Routeish[])
      .map((r) => r.props?.path)
      .filter((p): p is string => !!p && p.startsWith('/farmlog/reports'));
    expect(new Set(paths).size).toBe(4);
  });
});
