/**
 * Permissions — read-only catalog browser.
 *
 * The permission catalog is seeded server-side (see backend seed.py); the
 * admin UI never creates or deletes permissions, only roles consume them.
 * This page is mainly a discovery surface for admins building roles.
 */
import { useMemo, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { useTranslation } from 'react-i18next';
import { Loader2, Search } from 'lucide-react';
import { groupByCategory, listPermissions } from '../../api/permissions';

export function Permissions() {
  const { t } = useTranslation();
  const [q, setQ] = useState('');

  const { data: perms = [], isLoading } = useQuery({
    queryKey: ['permissions'],
    queryFn: listPermissions,
    staleTime: 5 * 60 * 1000,
  });

  const filtered = useMemo(() => {
    const needle = q.trim().toLowerCase();
    if (!needle) return perms;
    return perms.filter(
      (p) =>
        p.key.toLowerCase().includes(needle) ||
        p.displayName.toLowerCase().includes(needle) ||
        p.category.toLowerCase().includes(needle),
    );
  }, [perms, q]);

  const grouped = useMemo(() => groupByCategory(filtered), [filtered]);

  return (
    <div className="container mx-auto px-4 py-8 sm:px-6 lg:px-8">
      <header className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
        <div>
          <h1 className="text-xl font-bold">{t('settings.permissions.title')}</h1>
          <p className="mt-1 text-sm text-muted-foreground">{t('settings.permissions.description')}</p>
        </div>
        <label className="relative flex items-center">
          <Search className="absolute left-2 h-4 w-4 text-muted-foreground" />
          <input
            type="search"
            value={q}
            onChange={(e) => setQ(e.target.value)}
            placeholder={t('common.search')}
            className="w-full rounded-md border border-input bg-background py-2 pl-8 pr-3 text-sm focus:outline-none focus:ring-2 focus:ring-ring sm:w-64"
          />
        </label>
      </header>

      {isLoading ? (
        <div className="mt-12 flex items-center justify-center">
          <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
        </div>
      ) : (
        <section className="mt-6 flex flex-col gap-6">
          {Object.keys(grouped).sort().map((cat) => (
            <div key={cat} className="rounded-lg border border-border bg-card p-4 shadow-sm">
              <h2 className="text-base font-semibold capitalize text-foreground">{cat}</h2>
              <ul className="mt-3 flex flex-col divide-y divide-border">
                {grouped[cat]!.map((p) => (
                  <li key={p.id} className="flex items-center justify-between gap-3 py-2 text-sm">
                    <div>
                      <code className="rounded bg-muted px-1.5 py-0.5 text-xs">{p.key}</code>
                      <span className="ml-2 text-foreground">{p.displayName}</span>
                    </div>
                    {p.isMenu ? (
                      <span className="rounded-full bg-primary/10 px-2 py-0.5 text-xs font-medium text-primary">
                        {t('settings.permissions.menu')}
                      </span>
                    ) : null}
                  </li>
                ))}
              </ul>
            </div>
          ))}
          {Object.keys(grouped).length === 0 ? (
            <p className="text-sm text-muted-foreground">{t('common.noResults')}</p>
          ) : null}
        </section>
      )}
    </div>
  );
}

export default Permissions;
