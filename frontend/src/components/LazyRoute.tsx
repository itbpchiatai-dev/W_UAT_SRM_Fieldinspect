/**
 * LazyRoute — the Suspense boundary for route-level code splitting
 * (round 8-17A).
 *
 * Deliberately per-ROUTE-ELEMENT rather than one boundary wrapped around
 * <Routes>. A single outer boundary would unmount the whole shell —
 * AppLayout's top bar and sidebar included — every time a lazy page
 * resolved, so navigating between two loaded pages would flash the entire
 * frame. Keeping the boundary inside the element means only the content
 * region swaps to the fallback while the shell stays mounted.
 *
 * It also keeps `MODULE_ROUTES` self-contained: routes.test.tsx renders
 * those <Route> elements directly into a bare <Routes>, with no App shell
 * around them, and a lazy element with no enclosing boundary would throw.
 *
 * Nest INSIDE RequirePermission, not outside it: the guard renders its
 * children only when the permission check passes, so a user who lacks the
 * permission never triggers the dynamic import at all — the 403 panel
 * renders without fetching the chunk.
 */
import { Suspense, type ReactNode } from 'react';

/**
 * Matches the in-page loading idiom already used across the app (see
 * RecordList/PlotDetail) so a route-level wait looks like every other wait
 * instead of introducing a second visual language.
 *
 * role="status" + aria-live="polite" so screen readers announce the wait;
 * min-height reserves vertical space so the swap to real content doesn't
 * jump the layout.
 */
export function RouteFallback() {
  return (
    <div
      role="status"
      aria-live="polite"
      className="flex min-h-[50vh] items-center justify-center px-4 py-16 text-gray-400"
    >
      กำลังโหลด...
    </div>
  );
}

export function LazyRoute({ children }: { children: ReactNode }) {
  return <Suspense fallback={<RouteFallback />}>{children}</Suspense>;
}
