/**
 * RequireAuth — protects the App's authenticated route tree.
 *
 * States:
 *   loading        → spinner (AuthBootstrap is doing /refresh + /me).
 *   not authed     → <Navigate to="/login?return=<path>" />.
 *   authed         → render children.
 *
 * The `return` query string preserves the deep-link the user wanted
 * before being bounced to /login; <Login/> reads it post-success.
 *
 * Round-4 HIGH-3: location.pathname comes from the router so under
 * normal flow it is always internal — but a future regression that
 * accepts an external pathname (or a malformed redirect) must not
 * propagate through this guard. We feed it through safeReturn before
 * round-tripping it into the URL.
 */
import type { ReactNode } from 'react';
import { Navigate, useLocation } from 'react-router-dom';
import { Loader2 } from 'lucide-react';
import { useAuth } from '../hooks/useAuth';
import { safeReturn } from '../lib/safe-redirect';

export interface RequireAuthProps {
  children: ReactNode;
}

export function RequireAuth({ children }: RequireAuthProps) {
  const { isAuthenticated, isLoading } = useAuth();
  const location = useLocation();

  if (isLoading) {
    return (
      <div className="flex min-h-screen items-center justify-center">
        <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
      </div>
    );
  }

  if (!isAuthenticated) {
    // Sanitize before encoding — pathname+search SHOULD be internal but
    // we never want this guard to be the surface that emits an
    // attacker-shaped ?return=.
    const target = safeReturn(location.pathname + location.search, '/');
    const ret = encodeURIComponent(target);
    return <Navigate to={`/login?return=${ret}`} replace />;
  }

  return <>{children}</>;
}

export default RequireAuth;
