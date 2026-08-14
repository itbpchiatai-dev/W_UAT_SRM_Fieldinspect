/**
 * DatabaseConnections — Setup page to register/edit external PostgreSQL
 * targets (super_admin). Supports many connections; the Query Sandbox
 * runs against one at a time.
 *
 * The password field is write-only: blank on edit means "keep existing".
 * Saving never echoes the stored password back.
 */
import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useTranslation } from 'react-i18next';
import { CheckCircle2, Database, Loader2, Pencil, Plug, Plus, Trash2, XCircle } from 'lucide-react';
import {
  type DbConnection,
  type DbConnectionCreate,
  type SslMode,
  createConnection,
  deleteConnection,
  listConnections,
  testConnection,
  updateConnection,
} from '../../api/dbConnections';

const SSL_MODES: SslMode[] = ['disable', 'prefer', 'require', 'verify-ca', 'verify-full'];

type FormState = DbConnectionCreate;

const EMPTY_FORM: FormState = {
  name: '', description: '', host: '', port: 5432, database: '', username: '',
  password: '', sslMode: 'prefer', isActive: true, allowWrite: false,
};

function toForm(c: DbConnection): FormState {
  return {
    name: c.name, description: c.description ?? '', host: c.host, port: c.port,
    database: c.database, username: c.username, password: '', sslMode: c.sslMode,
    isActive: c.isActive, allowWrite: c.allowWrite,
  };
}

export function DatabaseConnections() {
  const { t } = useTranslation();
  const qc = useQueryClient();
  const [editing, setEditing] = useState<DbConnection | null>(null);
  const [creating, setCreating] = useState(false);
  const [testResult, setTestResult] = useState<Record<string, string>>({});

  const { data: connections = [], isLoading } = useQuery({
    queryKey: ['db-connections'],
    queryFn: listConnections,
  });

  const invalidate = () => qc.invalidateQueries({ queryKey: ['db-connections'] });

  const saveM = useMutation({
    mutationFn: async (form: FormState) => {
      if (editing) {
        const payload = { ...form };
        if (!payload.password) delete (payload as Partial<FormState>).password;
        return updateConnection(editing.id, payload);
      }
      return createConnection(form);
    },
    onSuccess: () => { invalidate(); setEditing(null); setCreating(false); },
  });

  const deleteM = useMutation({
    mutationFn: (id: string) => deleteConnection(id),
    onSuccess: invalidate,
  });

  const testM = useMutation({
    mutationFn: (id: string) => testConnection(id),
    onSuccess: (res, id) => {
      setTestResult((prev) => ({
        ...prev,
        [id]: res.success
          ? `✓ ${res.message}${res.latencyMs != null ? ` (${res.latencyMs}ms)` : ''}`
          : `✗ ${res.message}`,
      }));
      invalidate();
    },
  });

  if (isLoading) {
    return (
      <div className="container mx-auto flex min-h-[40vh] items-center justify-center px-4">
        <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
      </div>
    );
  }

  const showForm = creating || editing !== null;

  return (
    <div className="container mx-auto max-w-5xl px-4 py-8 sm:px-6 lg:px-8">
      <header className="flex items-start justify-between gap-4">
        <div>
          <h1 className="flex items-center gap-2 text-xl font-bold">
            <Database className="h-5 w-5" /> {t('settings.dbConnections.title')}
          </h1>
          <p className="mt-1 text-sm text-muted-foreground">{t('settings.dbConnections.description')}</p>
        </div>
        {!showForm && (
          <button
            type="button"
            onClick={() => { setCreating(true); setEditing(null); }}
            className="inline-flex shrink-0 items-center gap-2 rounded-md bg-primary px-4 py-2 text-sm font-medium text-primary-foreground shadow-sm hover:bg-primary/90"
          >
            <Plus className="h-4 w-4" /> {t('settings.dbConnections.new')}
          </button>
        )}
      </header>

      {showForm && (
        <ConnectionForm
          initial={editing ? toForm(editing) : EMPTY_FORM}
          isEdit={editing !== null}
          isPending={saveM.isPending}
          error={saveM.isError ? extractError(saveM.error) : null}
          onCancel={() => { setCreating(false); setEditing(null); saveM.reset(); }}
          onSubmit={(form) => saveM.mutate(form)}
        />
      )}

      <section className="mt-6 overflow-x-auto rounded-lg border border-border bg-card shadow-sm">
        <table className="w-full text-sm">
          <thead className="border-b border-border text-left text-muted-foreground">
            <tr>
              <th className="px-4 py-3 font-medium">{t('settings.dbConnections.fields.name')}</th>
              <th className="px-4 py-3 font-medium">{t('settings.dbConnections.fields.target')}</th>
              <th className="px-4 py-3 font-medium">{t('settings.dbConnections.fields.mode')}</th>
              <th className="px-4 py-3 font-medium">{t('settings.dbConnections.fields.lastTest')}</th>
              <th className="px-4 py-3 text-right font-medium">{t('common.actions')}</th>
            </tr>
          </thead>
          <tbody>
            {connections.length === 0 ? (
              <tr><td colSpan={5} className="px-4 py-8 text-center text-muted-foreground">{t('common.noResults')}</td></tr>
            ) : connections.map((c) => (
              <tr key={c.id} className="border-b border-border last:border-0">
                <td className="px-4 py-3">
                  <div className="font-medium">{c.name}</div>
                  {c.description && <div className="text-xs text-muted-foreground">{c.description}</div>}
                  {!c.isActive && <span className="text-xs text-muted-foreground">({t('settings.dbConnections.disabled')})</span>}
                </td>
                <td className="px-4 py-3 font-mono text-xs">{c.username}@{c.host}:{c.port}/{c.database}</td>
                <td className="px-4 py-3">
                  {c.allowWrite
                    ? <span className="rounded bg-destructive/10 px-2 py-0.5 text-xs text-destructive">{t('settings.dbConnections.readWrite')}</span>
                    : <span className="rounded bg-secondary px-2 py-0.5 text-xs text-muted-foreground">{t('settings.dbConnections.readOnly')}</span>}
                </td>
                <td className="px-4 py-3 text-xs">
                  {testResult[c.id]
                    ? <span className={testResult[c.id].startsWith('✓') ? 'text-green-600' : 'text-destructive'}>{testResult[c.id]}</span>
                    : c.lastTestStatus === 'success'
                      ? <span className="inline-flex items-center gap-1 text-green-600"><CheckCircle2 className="h-3 w-3" /> {t('settings.dbConnections.ok')}</span>
                      : c.lastTestStatus === 'failed'
                        ? <span className="inline-flex items-center gap-1 text-destructive"><XCircle className="h-3 w-3" /> {t('settings.dbConnections.failed')}</span>
                        : <span className="text-muted-foreground">—</span>}
                </td>
                <td className="px-4 py-3">
                  <div className="flex justify-end gap-1">
                    <IconButton title={t('settings.dbConnections.test')} onClick={() => testM.mutate(c.id)} busy={testM.isPending && testM.variables === c.id}>
                      <Plug className="h-4 w-4" />
                    </IconButton>
                    <IconButton title={t('common.edit')} onClick={() => { setEditing(c); setCreating(false); }}>
                      <Pencil className="h-4 w-4" />
                    </IconButton>
                    <IconButton title={t('common.delete')} danger
                      onClick={() => { if (window.confirm(t('settings.dbConnections.confirmDelete', { name: c.name }))) deleteM.mutate(c.id); }}>
                      <Trash2 className="h-4 w-4" />
                    </IconButton>
                  </div>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>
    </div>
  );
}

function IconButton({ children, title, onClick, danger, busy }: {
  children: React.ReactNode; title: string; onClick: () => void; danger?: boolean; busy?: boolean;
}) {
  return (
    <button
      type="button" title={title} onClick={onClick} disabled={busy}
      className={`rounded-md p-2 transition-colors hover:bg-secondary disabled:opacity-50 ${danger ? 'text-destructive' : 'text-muted-foreground'}`}
    >
      {busy ? <Loader2 className="h-4 w-4 animate-spin" /> : children}
    </button>
  );
}

function ConnectionForm({ initial, isEdit, isPending, error, onCancel, onSubmit }: {
  initial: FormState; isEdit: boolean; isPending: boolean; error: string | null;
  onCancel: () => void; onSubmit: (form: FormState) => void;
}) {
  const { t } = useTranslation();
  const [form, setForm] = useState<FormState>(initial);
  const set = <K extends keyof FormState>(k: K, v: FormState[K]) => setForm((f) => ({ ...f, [k]: v }));
  const inputCls = 'rounded-md border border-input bg-background px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-ring';

  return (
    <section className="mt-6 rounded-lg border border-border bg-card p-6 shadow-sm">
      <h2 className="text-base font-semibold">
        {isEdit ? t('settings.dbConnections.edit') : t('settings.dbConnections.new')}
      </h2>
      <div className="mt-4 grid grid-cols-1 gap-4 sm:grid-cols-2">
        <Field label={t('settings.dbConnections.fields.name')}>
          <input className={inputCls} value={form.name} onChange={(e) => set('name', e.target.value)} />
        </Field>
        <Field label={t('settings.dbConnections.fields.description')}>
          <input className={inputCls} value={form.description ?? ''} onChange={(e) => set('description', e.target.value)} />
        </Field>
        <Field label={t('settings.dbConnections.fields.host')}>
          <input className={inputCls} value={form.host} onChange={(e) => set('host', e.target.value)} />
        </Field>
        <Field label={t('settings.dbConnections.fields.port')}>
          <input type="number" className={inputCls} value={form.port} onChange={(e) => set('port', Number(e.target.value))} />
        </Field>
        <Field label={t('settings.dbConnections.fields.database')}>
          <input className={inputCls} value={form.database} onChange={(e) => set('database', e.target.value)} />
        </Field>
        <Field label={t('settings.dbConnections.fields.username')}>
          <input className={inputCls} autoComplete="off" value={form.username} onChange={(e) => set('username', e.target.value)} />
        </Field>
        <Field label={t('settings.dbConnections.fields.password')} hint={isEdit ? t('settings.dbConnections.passwordEditHint') : undefined}>
          <input type="password" className={inputCls} autoComplete="new-password"
            placeholder={isEdit ? '••••••••' : ''} value={form.password} onChange={(e) => set('password', e.target.value)} />
        </Field>
        <Field label={t('settings.dbConnections.fields.sslMode')}>
          <select className={inputCls} value={form.sslMode} onChange={(e) => set('sslMode', e.target.value as SslMode)}>
            {SSL_MODES.map((m) => <option key={m} value={m}>{m}</option>)}
          </select>
        </Field>
      </div>

      <div className="mt-4 flex flex-col gap-3">
        <label className="flex items-center gap-2 text-sm">
          <input type="checkbox" checked={form.isActive} onChange={(e) => set('isActive', e.target.checked)} />
          {t('settings.dbConnections.fields.active')}
        </label>
        <label className="flex items-center gap-2 text-sm">
          <input type="checkbox" checked={form.allowWrite} onChange={(e) => set('allowWrite', e.target.checked)} />
          <span>{t('settings.dbConnections.fields.allowWrite')}</span>
        </label>
        {form.allowWrite && (
          <p className="rounded-md border border-destructive/40 bg-destructive/10 px-3 py-2 text-xs text-destructive">
            {t('settings.dbConnections.allowWriteWarning')}
          </p>
        )}
      </div>

      {error && <p className="mt-4 rounded-md border border-destructive/40 bg-destructive/10 px-3 py-2 text-sm text-destructive">{error}</p>}

      <div className="mt-6 flex justify-end gap-2">
        <button type="button" onClick={onCancel} className="rounded-md border border-border px-4 py-2 text-sm hover:bg-secondary">
          {t('common.cancel')}
        </button>
        <button
          type="button"
          disabled={isPending || !form.name || !form.host || !form.database || !form.username || (!isEdit && !form.password)}
          onClick={() => onSubmit(form)}
          className="inline-flex items-center gap-2 rounded-md bg-primary px-4 py-2 text-sm font-medium text-primary-foreground shadow-sm hover:bg-primary/90 disabled:opacity-60"
        >
          {isPending && <Loader2 className="h-4 w-4 animate-spin" />}
          {t('common.save')}
        </button>
      </div>
    </section>
  );
}

function Field({ label, hint, children }: { label: string; hint?: string; children: React.ReactNode }) {
  return (
    <label className="flex flex-col gap-1 text-sm">
      <span className="font-medium">{label}</span>
      {children}
      {hint && <span className="text-xs text-muted-foreground">{hint}</span>}
    </label>
  );
}

function extractError(err: unknown): string {
  if (err && typeof err === 'object' && 'response' in err) {
    const detail = (err as { response?: { data?: { detail?: string } } }).response?.data?.detail;
    if (detail) return detail;
  }
  return err instanceof Error ? err.message : 'Error';
}

export default DatabaseConnections;
