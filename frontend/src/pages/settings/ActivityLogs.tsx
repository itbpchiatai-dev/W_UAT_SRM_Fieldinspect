/**
 * ActivityLogs — admin audit-trail view.
 *
 * Three named views (tabs) instead of a hidden checkbox so the mental
 * models stay distinct:
 *   - Login    — login / login_failed / logout (default; most common
 *                reason an admin opens this page)
 *   - Security — login + permission_denied + role_change + anything
 *                marked is_security_event=true
 *   - All      — full audit stream (CRUD, sensitive reads, etc.)
 *
 * Columns are universal across views — pills/IP already tell the story.
 */
import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { useTranslation } from 'react-i18next';
import { Download, Loader2, Search } from 'lucide-react';
import { downloadActivityLogsCsv, listActivityLogs } from '../../api/activityLogs';
import type { ActivityLog } from '../../types/auth';

const PAGE_SIZE = 25;

const VIEWS = ['login', 'security', 'all'] as const;
type View = (typeof VIEWS)[number];

function actionTypeClass(actionType: string): string {
  switch (actionType) {
    case 'login':
      return 'bg-success/15 text-success-readable';
    case 'login_failed':
    case 'permission_denied':
      return 'bg-destructive/15 text-destructive';
    case 'logout':
      return 'bg-info/15 text-chart-blue-deep';
    case 'role_change':
      return 'bg-warning/15 text-warning-readable';
    default:
      return 'bg-muted text-muted-foreground';
  }
}

function riskClass(risk: string): string {
  switch (risk) {
    case 'high':
      return 'bg-destructive/15 text-destructive';
    case 'medium':
      return 'bg-warning/15 text-warning-readable';
    default:
      return 'bg-muted text-muted-foreground';
  }
}

export function ActivityLogs() {
  const { t } = useTranslation();
  const [page, setPage] = useState(0);
  const [view, setView] = useState<View>('login');
  const [riskLevel, setRiskLevel] = useState<string>('');
  const [q, setQ] = useState<string>('');
  const [dateFrom, setDateFrom] = useState<string>('');
  const [dateTo, setDateTo] = useState<string>('');
  const [exporting, setExporting] = useState(false);

  const filterParams = {
    loginOnly: view === 'login' || undefined,
    securityOnly: view === 'security' || undefined,
    riskLevel: riskLevel || undefined,
    q: q || undefined,
    dateFrom: dateFrom || undefined,
    dateTo: dateTo || undefined,
  };

  const { data: logs = [], isLoading, isFetching } = useQuery({
    queryKey: ['activity-logs', page, view, riskLevel, q, dateFrom, dateTo],
    queryFn: () =>
      listActivityLogs({
        limit: PAGE_SIZE,
        offset: page * PAGE_SIZE,
        ...filterParams,
      }),
  });

  async function handleExport() {
    setExporting(true);
    try {
      await downloadActivityLogsCsv(filterParams);
    } finally {
      setExporting(false);
    }
  }

  return (
    <div className="container mx-auto px-4 py-8 sm:px-6 lg:px-8">
      <header>
        <h1 className="text-xl font-bold">{t('settings.activityLogs.title')}</h1>
        <p className="mt-1 text-sm text-muted-foreground">{t('settings.activityLogs.description')}</p>
      </header>

      {/* Tabs replace the old "loginOnly" checkbox — each named view sets
          a different server-side filter via the query params above. */}
      <div className="mt-6 flex gap-1 border-b border-border" role="tablist">
        {VIEWS.map((v) => (
          <button
            key={v}
            type="button"
            role="tab"
            aria-selected={view === v}
            onClick={() => {
              setPage(0);
              setView(v);
            }}
            className={`-mb-px border-b-2 px-4 py-2 text-sm font-medium transition-colors ${
              view === v
                ? 'border-primary text-primary'
                : 'border-transparent text-muted-foreground hover:text-foreground'
            }`}
          >
            {t(`settings.activityLogs.views.${v}`)}
          </button>
        ))}
      </div>

      <div className="mt-4 flex flex-wrap items-center gap-3">
        <label className="relative flex flex-1 items-center sm:max-w-xs">
          <Search className="absolute left-2 h-4 w-4 text-muted-foreground" />
          <input
            type="search"
            value={q}
            onChange={(e) => {
              setPage(0);
              setQ(e.target.value);
            }}
            placeholder={t('settings.activityLogs.searchPlaceholder')}
            className="w-full rounded-md border border-input bg-background py-1.5 pl-8 pr-3 text-sm focus:outline-none focus:ring-2 focus:ring-ring"
          />
        </label>
        <label className="flex items-center gap-2 text-sm">
          <span className="text-muted-foreground">{t('settings.activityLogs.fields.riskLevel')}</span>
          <select
            value={riskLevel}
            onChange={(e) => {
              setPage(0);
              setRiskLevel(e.target.value);
            }}
            className="rounded-md border border-input bg-background px-3 py-1.5 text-sm focus:outline-none focus:ring-2 focus:ring-ring"
          >
            <option value="">{t('settings.activityLogs.allRiskLevels')}</option>
            <option value="low">low</option>
            <option value="medium">medium</option>
            <option value="high">high</option>
          </select>
        </label>
        <label className="flex items-center gap-2 text-sm">
          <span className="text-muted-foreground">{t('common.dateFrom')}</span>
          <input
            type="date"
            value={dateFrom}
            onChange={(e) => { setPage(0); setDateFrom(e.target.value); }}
            className="rounded-md border border-input bg-background px-3 py-1.5 text-sm focus:outline-none focus:ring-2 focus:ring-ring"
          />
        </label>
        <label className="flex items-center gap-2 text-sm">
          <span className="text-muted-foreground">{t('common.dateTo')}</span>
          <input
            type="date"
            value={dateTo}
            onChange={(e) => { setPage(0); setDateTo(e.target.value); }}
            className="rounded-md border border-input bg-background px-3 py-1.5 text-sm focus:outline-none focus:ring-2 focus:ring-ring"
          />
        </label>
        <button
          type="button"
          onClick={handleExport}
          disabled={exporting}
          className="inline-flex items-center gap-2 rounded-md border border-border bg-card px-3 py-1.5 text-sm font-medium hover:bg-secondary disabled:opacity-60"
          title={t('common.exportCsv')}
        >
          {exporting ? <Loader2 className="h-4 w-4 animate-spin" /> : <Download className="h-4 w-4" />}
          {t('common.exportCsv')}
        </button>
        {isFetching && !isLoading ? (
          <Loader2 className="h-4 w-4 animate-spin text-muted-foreground" />
        ) : null}
      </div>

      <section className="mt-4 overflow-x-auto rounded-lg border border-border bg-card shadow-sm">
        {isLoading ? (
          <div className="flex items-center justify-center py-16">
            <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
          </div>
        ) : (
          <table className="w-full text-sm">
            <thead className="bg-secondary/50 text-left text-sm font-semibold text-muted-foreground">
              <tr>
                <th className="whitespace-nowrap px-4 py-2">{t('settings.activityLogs.fields.createdAt')}</th>
                <th className="px-4 py-2">{t('settings.activityLogs.fields.user')}</th>
                <th className="px-4 py-2">{t('settings.activityLogs.fields.action')}</th>
                <th className="px-4 py-2">{t('settings.activityLogs.fields.actionType')}</th>
                <th className="px-4 py-2">{t('settings.activityLogs.fields.riskLevel')}</th>
                <th className="whitespace-nowrap px-4 py-2">{t('settings.activityLogs.fields.ipAddress')}</th>
                <th className="whitespace-nowrap px-4 py-2 text-right">{t('settings.activityLogs.fields.httpStatus')}</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-border">
              {logs.map((row: ActivityLog) => (
                <tr key={row.id} className="hover:bg-secondary/30">
                  <td className="whitespace-nowrap px-4 py-2 font-mono text-sm text-muted-foreground">
                    {new Date(row.createdAt).toLocaleString()}
                  </td>
                  <td className="px-4 py-2 text-sm">
                    {row.userEmailMasked ?? <span className="text-muted-foreground">—</span>}
                  </td>
                  <td className="px-4 py-2 font-mono text-sm">{row.action}</td>
                  <td className="px-4 py-2">
                    <span className={`rounded-full px-2 py-0.5 text-xs ${actionTypeClass(row.actionType)}`}>
                      {row.actionType}
                    </span>
                  </td>
                  <td className="px-4 py-2">
                    <span className={`rounded-full px-2 py-0.5 text-xs ${riskClass(row.riskLevel)}`}>
                      {row.riskLevel}
                    </span>
                  </td>
                  <td className="whitespace-nowrap px-4 py-2 font-mono text-sm text-muted-foreground">
                    {row.ipAddress ?? '—'}
                  </td>
                  <td className="whitespace-nowrap px-4 py-2 text-right font-mono text-sm">
                    {row.httpStatus ?? '—'}
                  </td>
                </tr>
              ))}
              {logs.length === 0 ? (
                <tr>
                  <td colSpan={7} className="px-4 py-6 text-center text-sm text-muted-foreground">
                    {t('common.noResults')}
                  </td>
                </tr>
              ) : null}
            </tbody>
          </table>
        )}
      </section>

      <nav className="mt-3 flex justify-end gap-2 text-sm">
        <button
          type="button"
          onClick={() => setPage((p) => Math.max(0, p - 1))}
          disabled={page === 0}
          className="rounded-md border border-border bg-card px-3 py-1.5 hover:bg-secondary disabled:opacity-40"
        >
          {t('common.previous')}
        </button>
        <span className="self-center text-muted-foreground">{page + 1}</span>
        <button
          type="button"
          onClick={() => setPage((p) => p + 1)}
          disabled={logs.length < PAGE_SIZE}
          className="rounded-md border border-border bg-card px-3 py-1.5 hover:bg-secondary disabled:opacity-40"
        >
          {t('common.next')}
        </button>
      </nav>
    </div>
  );
}

export default ActivityLogs;
