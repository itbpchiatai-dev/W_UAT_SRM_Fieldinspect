/**
 * SystemLogs — admin read-only view of recent system events.
 *
 * Columns: time / category / event / status / duration / error message.
 * Status pill colors map the backend `status` values to semantic tokens
 * (success / failure / warning / info / started). Pagination is server-
 * side via limit+offset; we keep PAGE_SIZE small so the table fits
 * without horizontal scrolling on the typical admin viewport.
 */
import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { useTranslation } from 'react-i18next';
import { Download, Loader2, Search } from 'lucide-react';
import { downloadSystemLogsCsv, listSystemLogs } from '../../api/systemLogs';
import type { SystemLog } from '../../types/auth';

const PAGE_SIZE = 25;

const STATUS_OPTIONS = ['', 'success', 'failure', 'warning', 'info', 'started'] as const;

function statusClass(status: string): string {
  switch (status) {
    case 'success':
      return 'bg-success/15 text-success-readable';
    case 'failure':
      return 'bg-destructive/15 text-destructive';
    case 'warning':
      return 'bg-warning/15 text-warning-readable';
    case 'info':
    case 'started':
      return 'bg-info/15 text-chart-blue-deep';
    default:
      return 'bg-muted text-muted-foreground';
  }
}

export function SystemLogs() {
  const { t } = useTranslation();
  const [page, setPage] = useState(0);
  const [status, setStatus] = useState<string>('');
  const [category, setCategory] = useState<string>('');
  const [q, setQ] = useState<string>('');
  const [dateFrom, setDateFrom] = useState<string>('');
  const [dateTo, setDateTo] = useState<string>('');
  const [exporting, setExporting] = useState(false);

  const filterParams = {
    status: status || undefined,
    category: category || undefined,
    q: q || undefined,
    dateFrom: dateFrom || undefined,
    dateTo: dateTo || undefined,
  };

  const { data: logs = [], isLoading, isFetching } = useQuery({
    queryKey: ['system-logs', page, status, category, q, dateFrom, dateTo],
    queryFn: () =>
      listSystemLogs({
        limit: PAGE_SIZE,
        offset: page * PAGE_SIZE,
        ...filterParams,
      }),
  });

  async function handleExport() {
    setExporting(true);
    try {
      await downloadSystemLogsCsv(filterParams);
    } finally {
      setExporting(false);
    }
  }

  return (
    <div className="container mx-auto px-4 py-8 sm:px-6 lg:px-8">
      <header>
        <h1 className="text-xl font-bold">{t('settings.systemLogs.title')}</h1>
        <p className="mt-1 text-sm text-muted-foreground">{t('settings.systemLogs.description')}</p>
      </header>

      <div className="mt-4 flex flex-wrap items-center gap-2">
        <label className="relative flex flex-1 items-center sm:max-w-xs">
          <Search className="absolute left-2 h-4 w-4 text-muted-foreground" />
          <input
            type="search"
            value={q}
            onChange={(e) => {
              setPage(0);
              setQ(e.target.value);
            }}
            placeholder={t('settings.systemLogs.searchPlaceholder')}
            className="w-full rounded-md border border-input bg-background py-1.5 pl-8 pr-3 text-sm focus:outline-none focus:ring-2 focus:ring-ring"
          />
        </label>
        <label className="flex items-center gap-2 text-sm">
          <span className="text-muted-foreground">{t('settings.systemLogs.fields.status')}</span>
          <select
            value={status}
            onChange={(e) => {
              setPage(0);
              setStatus(e.target.value);
            }}
            className="rounded-md border border-input bg-background px-3 py-1.5 text-sm focus:outline-none focus:ring-2 focus:ring-ring"
          >
            {STATUS_OPTIONS.map((s) => (
              <option key={s} value={s}>
                {s === '' ? t('settings.systemLogs.allStatuses') : s}
              </option>
            ))}
          </select>
        </label>
        <label className="flex items-center gap-2 text-sm">
          <span className="text-muted-foreground">{t('settings.systemLogs.fields.category')}</span>
          <input
            type="text"
            value={category}
            onChange={(e) => {
              setPage(0);
              setCategory(e.target.value);
            }}
            placeholder={t('settings.systemLogs.categoryPlaceholder')}
            className="rounded-md border border-input bg-background px-3 py-1.5 text-sm focus:outline-none focus:ring-2 focus:ring-ring"
          />
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
                <th className="whitespace-nowrap px-4 py-2">{t('settings.systemLogs.fields.createdAt')}</th>
                <th className="px-4 py-2">{t('settings.systemLogs.fields.category')}</th>
                <th className="px-4 py-2">{t('settings.systemLogs.fields.event')}</th>
                <th className="px-4 py-2">{t('settings.systemLogs.fields.status')}</th>
                <th className="whitespace-nowrap px-4 py-2 text-right">{t('settings.systemLogs.fields.durationMs')}</th>
                <th className="px-4 py-2">{t('settings.systemLogs.fields.error')}</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-border">
              {logs.map((row: SystemLog) => (
                <tr key={row.id} className="hover:bg-secondary/30">
                  <td className="whitespace-nowrap px-4 py-2 font-mono text-sm text-muted-foreground">
                    {new Date(row.createdAt).toLocaleString()}
                  </td>
                  <td className="px-4 py-2">
                    <span className="rounded-full bg-muted px-2 py-0.5 text-xs">{row.category}</span>
                  </td>
                  <td className="px-4 py-2">{row.event}</td>
                  <td className="px-4 py-2">
                    <span className={`rounded-full px-2 py-0.5 text-xs ${statusClass(row.status)}`}>
                      {row.status}
                    </span>
                  </td>
                  <td className="whitespace-nowrap px-4 py-2 text-right font-mono text-sm">
                    {row.durationMs == null ? '—' : row.durationMs.toLocaleString()}
                  </td>
                  <td className="px-4 py-2 text-sm text-destructive">
                    {row.errorType ? (
                      <span title={row.errorMessage ?? undefined}>
                        <span className="font-mono">{row.errorType}</span>
                        {row.errorMessage ? `: ${row.errorMessage.slice(0, 80)}${row.errorMessage.length > 80 ? '…' : ''}` : ''}
                      </span>
                    ) : (
                      <span className="text-muted-foreground">—</span>
                    )}
                  </td>
                </tr>
              ))}
              {logs.length === 0 ? (
                <tr>
                  <td colSpan={6} className="px-4 py-6 text-center text-sm text-muted-foreground">
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

export default SystemLogs;
