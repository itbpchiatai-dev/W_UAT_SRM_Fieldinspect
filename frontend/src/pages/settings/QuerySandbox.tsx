/**
 * QuerySandbox — run ad-hoc SQL against a registered connection
 * (super_admin). Read-only by default; the write toggle is only honoured
 * when the selected connection has allow_write enabled (the server
 * re-enforces this). Results are capped server-side (db_sandbox.max_rows).
 */
import { useRef, useState } from 'react';
import { useMutation, useQuery } from '@tanstack/react-query';
import { useTranslation } from 'react-i18next';
import { AlertTriangle, Eye, Loader2, Play, Table2, Terminal } from 'lucide-react';
import {
  type DbConnection,
  type DbTable,
  type QueryResult,
  listConnections,
  listTables,
  runQuery,
} from '../../api/dbConnections';

export function QuerySandbox() {
  const { t } = useTranslation();
  const [connId, setConnId] = useState('');
  const [sql, setSql] = useState('');
  const [readOnly, setReadOnly] = useState(true);
  const [limit, setLimit] = useState(100);
  const sqlRef = useRef<HTMLTextAreaElement>(null);

  const { data: connections = [] } = useQuery({
    queryKey: ['db-connections'],
    queryFn: listConnections,
  });

  const active = connections.filter((c) => c.isActive);
  const selected: DbConnection | undefined = connections.find((c) => c.id === connId);

  const { data: tables = [], isLoading: tablesLoading } = useQuery({
    queryKey: ['db-tables', connId],
    queryFn: () => listTables(connId),
    enabled: !!connId,
  });

  const runM = useMutation({
    mutationFn: () => runQuery(connId, { sql, readOnly, limit }),
  });

  // Insert a table's identifier into the SQL editor: prefill a SELECT when
  // empty, otherwise splice it in at the cursor.
  const insertTable = (tbl: DbTable) => {
    const ident = tbl.schemaName === 'public' ? tbl.name : `${tbl.schemaName}.${tbl.name}`;
    setSql((prev) => {
      if (!prev.trim()) return `SELECT * FROM ${ident} LIMIT ${limit};`;
      const el = sqlRef.current;
      if (!el) return `${prev} ${ident}`;
      const start = el.selectionStart;
      const end = el.selectionEnd;
      return prev.slice(0, start) + ident + prev.slice(end);
    });
    sqlRef.current?.focus();
  };

  const canRunWrite = selected?.allowWrite ?? false;
  const inputCls = 'rounded-md border border-input bg-background px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-ring';

  const result: QueryResult | undefined = runM.data;

  return (
    <div className="container mx-auto max-w-6xl px-4 py-8 sm:px-6 lg:px-8">
      <header>
        <h1 className="flex items-center gap-2 text-xl font-bold">
          <Terminal className="h-5 w-5" /> {t('settings.querySandbox.title')}
        </h1>
        <p className="mt-1 text-sm text-muted-foreground">{t('settings.querySandbox.description')}</p>
      </header>

      <section className="mt-6 flex flex-col gap-4 rounded-lg border border-border bg-card p-6 shadow-sm">
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-3">
          <label className="flex flex-col gap-1 text-sm">
            <span className="font-medium">{t('settings.querySandbox.connection')}</span>
            <select className={inputCls} value={connId} onChange={(e) => { setConnId(e.target.value); setReadOnly(true); }}>
              <option value="">{t('settings.querySandbox.selectConnection')}</option>
              {active.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}
            </select>
          </label>
          <label className="flex flex-col gap-1 text-sm">
            <span className="font-medium">{t('settings.querySandbox.limit')}</span>
            <input type="number" min={1} max={10000} className={inputCls} value={limit} onChange={(e) => setLimit(Number(e.target.value))} />
          </label>
          <label className="flex items-end gap-2 text-sm">
            <input
              type="checkbox"
              disabled={!canRunWrite}
              checked={!readOnly && canRunWrite}
              onChange={(e) => setReadOnly(!e.target.checked)}
              className="mb-2.5"
            />
            <span className="mb-2">
              {t('settings.querySandbox.allowWrite')}
              {!canRunWrite && <span className="block text-xs text-muted-foreground">{t('settings.querySandbox.writeLocked')}</span>}
            </span>
          </label>
        </div>

        {!readOnly && canRunWrite && (
          <p className="flex items-center gap-2 rounded-md border border-destructive/40 bg-destructive/10 px-3 py-2 text-xs text-destructive">
            <AlertTriangle className="h-4 w-4 shrink-0" /> {t('settings.querySandbox.writeWarning')}
          </p>
        )}

        <div className="flex flex-col gap-4 lg:flex-row">
          <label className="flex flex-1 flex-col gap-1 text-sm">
            <span className="font-medium">SQL</span>
            <textarea
              ref={sqlRef}
              value={sql}
              onChange={(e) => setSql(e.target.value)}
              rows={8}
              spellCheck={false}
              placeholder="SELECT * FROM ..."
              className={`${inputCls} font-mono`}
            />
          </label>
          <TablesPanel
            hasConnection={!!connId}
            loading={tablesLoading}
            tables={tables}
            onPick={insertTable}
          />
        </div>

        <div className="flex justify-end">
          <button
            type="button"
            disabled={runM.isPending || !connId || !sql.trim()}
            onClick={() => runM.mutate()}
            className="inline-flex items-center gap-2 rounded-md bg-primary px-4 py-2 text-sm font-medium text-primary-foreground shadow-sm hover:bg-primary/90 disabled:opacity-60"
          >
            {runM.isPending ? <Loader2 className="h-4 w-4 animate-spin" /> : <Play className="h-4 w-4" />}
            {t('settings.querySandbox.run')}
          </button>
        </div>
      </section>

      {runM.isError && (
        <p className="mt-4 rounded-md border border-destructive/40 bg-destructive/10 px-3 py-2 text-sm text-destructive whitespace-pre-wrap">
          {extractError(runM.error)}
        </p>
      )}

      {result && <ResultPanel result={result} />}
    </div>
  );
}

function TablesPanel({ hasConnection, loading, tables, onPick }: {
  hasConnection: boolean;
  loading: boolean;
  tables: DbTable[];
  onPick: (tbl: DbTable) => void;
}) {
  const { t } = useTranslation();
  return (
    <aside className="flex w-full flex-col rounded-md border border-border bg-background lg:w-64 lg:shrink-0">
      <div className="border-b border-border px-3 py-2 text-sm font-medium">
        {t('settings.querySandbox.tables')}
        {hasConnection && tables.length > 0 && (
          <span className="ml-1 text-xs text-muted-foreground">({tables.length})</span>
        )}
      </div>
      <div className="max-h-64 overflow-auto p-1 lg:max-h-[14.5rem]">
        {!hasConnection ? (
          <p className="px-2 py-3 text-xs text-muted-foreground">{t('settings.querySandbox.tablesSelectFirst')}</p>
        ) : loading ? (
          <div className="flex justify-center py-4"><Loader2 className="h-4 w-4 animate-spin text-muted-foreground" /></div>
        ) : tables.length === 0 ? (
          <p className="px-2 py-3 text-xs text-muted-foreground">{t('settings.querySandbox.tablesEmpty')}</p>
        ) : (
          <ul>
            {tables.map((tbl) => (
              <li key={`${tbl.schemaName}.${tbl.name}`}>
                <button
                  type="button"
                  onClick={() => onPick(tbl)}
                  title={`${tbl.schemaName}.${tbl.name}`}
                  className="flex w-full items-center gap-2 rounded px-2 py-1.5 text-left text-xs hover:bg-secondary"
                >
                  {tbl.type === 'view'
                    ? <Eye className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />
                    : <Table2 className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />}
                  <span className="truncate font-mono">{tbl.name}</span>
                  {tbl.schemaName !== 'public' && (
                    <span className="ml-auto shrink-0 text-[10px] text-muted-foreground">{tbl.schemaName}</span>
                  )}
                </button>
              </li>
            ))}
          </ul>
        )}
      </div>
      {hasConnection && tables.length > 0 && (
        <p className="border-t border-border px-3 py-1.5 text-[10px] text-muted-foreground">
          {t('settings.querySandbox.tablesHint')}
        </p>
      )}
    </aside>
  );
}

function ResultPanel({ result }: { result: QueryResult }) {
  const { t } = useTranslation();
  return (
    <section className="mt-4 rounded-lg border border-border bg-card shadow-sm">
      <div className="flex flex-wrap items-center gap-3 border-b border-border px-4 py-3 text-xs text-muted-foreground">
        <span>{t('settings.querySandbox.rowsReturned', { count: result.rowCount })}</span>
        {result.command && <span className="font-mono">{result.command}</span>}
        <span>{result.durationMs} ms</span>
        {result.readOnly && <span className="rounded bg-secondary px-2 py-0.5">{t('settings.dbConnections.readOnly')}</span>}
        {result.truncated && (
          <span className="rounded bg-amber-500/10 px-2 py-0.5 text-amber-600">{t('settings.querySandbox.truncated')}</span>
        )}
      </div>
      {result.columns.length === 0 ? (
        <p className="px-4 py-6 text-sm text-muted-foreground">{t('settings.querySandbox.noRows')}</p>
      ) : (
        <div className="max-h-[55vh] overflow-auto">
          <table className="w-full text-left text-xs">
            <thead className="sticky top-0 border-b border-border bg-card text-muted-foreground">
              <tr>{result.columns.map((col) => <th key={col} className="px-3 py-2 font-medium">{col}</th>)}</tr>
            </thead>
            <tbody className="font-mono">
              {result.rows.map((row, i) => (
                <tr key={i} className="border-b border-border last:border-0">
                  {row.map((cell, j) => (
                    <td key={j} className="max-w-xs truncate px-3 py-1.5" title={fmt(cell)}>{fmt(cell)}</td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}

function fmt(v: unknown): string {
  if (v === null || v === undefined) return 'NULL';
  if (typeof v === 'object') return JSON.stringify(v);
  return String(v);
}

function extractError(err: unknown): string {
  if (err && typeof err === 'object' && 'response' in err) {
    const detail = (err as { response?: { data?: { detail?: string } } }).response?.data?.detail;
    if (detail) return detail;
  }
  return err instanceof Error ? err.message : 'Error';
}

export default QuerySandbox;
