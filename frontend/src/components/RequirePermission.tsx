/**
 * RequirePermission — permission-gate a route subtree.
 *
 * Renders `children` if the current user has the requested perm key (or
 * the wildcard `*`). Otherwise shows an inline 403 panel so the user
 * understands why they landed here. Does NOT redirect — that would mask
 * the fact that an admin tried to deep-link to something they can't see.
 *
 * Always nest INSIDE <RequireAuth/>. Anonymous users should never reach
 * a RequirePermission boundary; the auth guard bounces them first.
 */
import type { ReactNode } from 'react';
import { useTranslation } from 'react-i18next';
import { ShieldAlert } from 'lucide-react';
import { useHasPermission } from '../hooks/useHasPermission';

export interface RequirePermissionProps {
  perm: string;
  children: ReactNode;
}

export function RequirePermission({ perm, children }: RequirePermissionProps) {
  const { t } = useTranslation();
  const allowed = useHasPermission(perm);
  if (allowed) return <>{children}</>;
  return (
    <main className="container mx-auto flex min-h-[40vh] items-center justify-center px-4 py-8">
      <div className="flex max-w-md flex-col items-center gap-3 rounded-lg border border-border bg-card p-6 text-center shadow-sm">
        <ShieldAlert className="h-8 w-8 text-destructive" />
        <h2 className="text-lg font-semibold text-foreground">{t('common.forbidden')}</h2>
        <p className="text-sm text-muted-foreground">{t('common.missingPermission', { perm })}</p>
      </div>
    </main>
  );
}

export default RequirePermission;
