/**
 * Roles — list / create / edit / delete; permission assignment via a
 * multi-section checkbox group (grouped by permission.category).
 *
 * Name prefix discipline:
 *   provider_scope = internal → name MUST start with "internal:"
 *   provider_scope = external → name MUST start with "external:"
 *   provider_scope = any      → name MUST NOT start with either prefix
 * The form's zod schema mirrors the backend's RoleCreate validation so a
 * round-trip 422 is rare; the backend is still the source of truth.
 */
import { useMemo, useState } from 'react';
import { useForm } from 'react-hook-form';
import { z } from 'zod';
import { zodResolver } from '@hookform/resolvers/zod';
import { useTranslation } from 'react-i18next';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Loader2, Pencil, Plus, Trash2 } from 'lucide-react';
import {
  createRole,
  deleteRole,
  getRole,
  listRoles,
  updateRole,
  type RoleCreatePayload,
} from '../../api/roles';
import { groupByCategory, listPermissions } from '../../api/permissions';
import type { Permission, RoleDetail, RoleSummary } from '../../types/auth';

const roleSchema = z
  .object({
    name: z.string().min(1, 'common.required').max(100),
    displayName: z.string().min(1, 'common.required').max(150),
    providerScope: z.enum(['internal', 'external', 'any']),
    description: z.string().max(500).optional().default(''),
    permissionKeys: z.array(z.string()).default([]),
  })
  .refine(
    (v) =>
      (v.providerScope === 'internal' && v.name.startsWith('internal:')) ||
      (v.providerScope === 'external' && v.name.startsWith('external:')) ||
      (v.providerScope === 'any' && !v.name.startsWith('internal:') && !v.name.startsWith('external:')),
    { message: 'settings.roles.namePrefixMismatch', path: ['name'] },
  );

type RoleFormValues = z.infer<typeof roleSchema>;

export function Roles() {
  const { t } = useTranslation();
  const qc = useQueryClient();
  const [editingId, setEditingId] = useState<string | null>(null);
  const [creating, setCreating] = useState(false);

  const { data: roles = [], isLoading } = useQuery({
    queryKey: ['roles'],
    queryFn: listRoles,
  });
  const { data: perms = [] } = useQuery({
    queryKey: ['permissions'],
    queryFn: listPermissions,
    staleTime: 5 * 60 * 1000,
  });

  const deleteM = useMutation({
    mutationFn: (id: string) => deleteRole(id),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['roles'] }),
  });

  return (
    <div className="container mx-auto px-4 py-8 sm:px-6 lg:px-8">
      <header className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
        <div>
          <h1 className="text-xl font-bold">{t('settings.roles.title')}</h1>
          <p className="mt-1 text-sm text-muted-foreground">{t('settings.roles.description')}</p>
        </div>
        <button
          type="button"
          onClick={() => setCreating(true)}
          className="inline-flex items-center justify-center gap-2 rounded-md bg-primary px-4 py-2 text-sm font-medium text-primary-foreground shadow-sm transition-colors hover:bg-primary/90"
        >
          <Plus className="h-4 w-4" />
          {t('settings.roles.new')}
        </button>
      </header>

      <section className="mt-6 overflow-x-auto rounded-lg border border-border bg-card shadow-sm">
        {isLoading ? (
          <div className="flex items-center justify-center py-16">
            <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
          </div>
        ) : (
          <table className="w-full text-sm">
            <thead className="bg-secondary/50 text-left text-sm font-semibold text-muted-foreground">
              <tr>
                <th className="px-4 py-2">{t('settings.roles.fields.name')}</th>
                <th className="px-4 py-2">{t('settings.roles.fields.displayName')}</th>
                <th className="px-4 py-2">{t('settings.roles.fields.providerScope')}</th>
                <th className="px-4 py-2">{t('settings.roles.fields.system')}</th>
                <th className="px-4 py-2 text-right">{t('common.actions')}</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-border">
              {roles.map((r: RoleSummary) => (
                <tr key={r.id} className="hover:bg-secondary/30">
                  <td className="px-4 py-2 font-mono text-sm">{r.name}</td>
                  <td className="px-4 py-2">{r.displayName}</td>
                  <td className="px-4 py-2">
                    <span className="rounded-full bg-muted px-2 py-0.5 text-xs">{r.providerScope}</span>
                  </td>
                  <td className="px-4 py-2">
                    {r.isSystem ? (
                      <span className="rounded-full bg-warning/15 px-2 py-0.5 text-xs text-warning-readable">{t('common.yes')}</span>
                    ) : null}
                  </td>
                  <td className="px-4 py-2 text-right">
                    <div className="flex justify-end gap-1">
                      <button
                        type="button"
                        onClick={() => setEditingId(r.id)}
                        className="rounded-md p-1.5 text-foreground hover:bg-secondary"
                        aria-label={t('common.edit')}
                      >
                        <Pencil className="h-4 w-4" />
                      </button>
                      <button
                        type="button"
                        onClick={() => {
                          if (r.isSystem || (r.usersCount ?? 0) > 0) return;
                          if (confirm(t('settings.roles.confirmDelete', { name: r.name }))) {
                            deleteM.mutate(r.id);
                          }
                        }}
                        disabled={r.isSystem || (r.usersCount ?? 0) > 0}
                        className="rounded-md p-1.5 text-destructive hover:bg-destructive/10 disabled:cursor-not-allowed disabled:opacity-40"
                        aria-label={t('common.delete')}
                      >
                        <Trash2 className="h-4 w-4" />
                      </button>
                    </div>
                  </td>
                </tr>
              ))}
              {roles.length === 0 ? (
                <tr>
                  <td colSpan={5} className="px-4 py-6 text-center text-sm text-muted-foreground">
                    {t('common.noResults')}
                  </td>
                </tr>
              ) : null}
            </tbody>
          </table>
        )}
      </section>

      {creating ? (
        <RoleEditor
          mode="create"
          allPerms={perms}
          onClose={() => {
            setCreating(false);
            qc.invalidateQueries({ queryKey: ['roles'] });
          }}
        />
      ) : null}

      {editingId ? (
        <RoleEditor
          mode="edit"
          roleId={editingId}
          allPerms={perms}
          onClose={() => {
            setEditingId(null);
            qc.invalidateQueries({ queryKey: ['roles'] });
          }}
        />
      ) : null}
    </div>
  );
}

interface RoleEditorProps {
  mode: 'create' | 'edit';
  roleId?: string;
  allPerms: Permission[];
  onClose: () => void;
}

function RoleEditor({ mode, roleId, allPerms, onClose }: RoleEditorProps) {
  const { t } = useTranslation();
  const qc = useQueryClient();
  const { data: existing } = useQuery<RoleDetail>({
    queryKey: ['role', roleId],
    queryFn: () => getRole(roleId!),
    enabled: mode === 'edit' && !!roleId,
  });

  const defaults: RoleFormValues = useMemo(
    () => ({
      name: existing?.name ?? '',
      displayName: existing?.displayName ?? '',
      providerScope: (existing?.providerScope ?? 'any') as RoleFormValues['providerScope'],
      description: existing?.description ?? '',
      permissionKeys: existing?.permissions?.map((p) => p.key) ?? [],
    }),
    [existing],
  );

  const {
    register,
    handleSubmit,
    watch,
    setValue,
    reset,
    formState: { errors, isSubmitting },
  } = useForm<RoleFormValues>({
    resolver: zodResolver(roleSchema),
    values: defaults,
  });

  const selected = new Set(watch('permissionKeys'));
  const grouped = useMemo(() => groupByCategory(allPerms), [allPerms]);

  const togglePerm = (key: string) => {
    const next = new Set(selected);
    if (next.has(key)) next.delete(key);
    else next.add(key);
    setValue('permissionKeys', Array.from(next), { shouldDirty: true });
  };

  const onSubmit = async (values: RoleFormValues) => {
    const payload: RoleCreatePayload = {
      name: values.name,
      displayName: values.displayName,
      providerScope: values.providerScope,
      description: values.description,
      permissionKeys: values.permissionKeys,
    };
    if (mode === 'create') {
      await createRole(payload);
    } else if (roleId) {
      await updateRole(roleId, {
        displayName: values.displayName,
        description: values.description,
        permissionKeys: values.permissionKeys,
      });
    }
    reset();
    qc.invalidateQueries({ queryKey: ['roles'] });
    onClose();
  };

  return (
    <ModalShell onClose={onClose} title={t(mode === 'create' ? 'settings.roles.new' : 'settings.roles.edit')}>
      <form onSubmit={handleSubmit(onSubmit)} className="flex flex-col gap-4">
        <label className="flex flex-col gap-1 text-sm">
          <span className="font-medium">{t('settings.roles.fields.name')}</span>
          <input
            type="text"
            readOnly={mode === 'edit'}
            {...register('name')}
            className="rounded-md border border-input bg-background px-3 py-2 text-sm font-mono focus:outline-none focus:ring-2 focus:ring-ring disabled:opacity-70 read-only:opacity-70"
          />
          {errors.name ? (
            <span className="text-xs text-destructive">{t(errors.name.message ?? '')}</span>
          ) : null}
        </label>

        <label className="flex flex-col gap-1 text-sm">
          <span className="font-medium">{t('settings.roles.fields.displayName')}</span>
          <input
            type="text"
            {...register('displayName')}
            className="rounded-md border border-input bg-background px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-ring"
          />
          {errors.displayName ? (
            <span className="text-xs text-destructive">{t(errors.displayName.message ?? '')}</span>
          ) : null}
        </label>

        <label className="flex flex-col gap-1 text-sm">
          <span className="font-medium">{t('settings.roles.fields.providerScope')}</span>
          <select
            {...register('providerScope')}
            disabled={mode === 'edit'}
            className="rounded-md border border-input bg-background px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-ring disabled:opacity-70"
          >
            <option value="any">any</option>
            <option value="internal">internal</option>
            <option value="external">external</option>
          </select>
        </label>

        <label className="flex flex-col gap-1 text-sm">
          <span className="font-medium">{t('settings.roles.fields.description')}</span>
          <textarea
            rows={2}
            {...register('description')}
            className="rounded-md border border-input bg-background px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-ring"
          />
        </label>

        <div>
          <span className="text-sm font-medium">{t('settings.roles.fields.permissions')}</span>
          <div className="mt-2 max-h-72 overflow-y-auto rounded-md border border-border bg-background p-2">
            {Object.keys(grouped).sort().map((cat) => (
              <fieldset key={cat} className="mb-3 last:mb-0">
                <legend className="text-xs font-semibold uppercase text-muted-foreground">{cat}</legend>
                <div className="mt-1 grid grid-cols-1 gap-1 sm:grid-cols-2">
                  {grouped[cat]!.map((p) => (
                    <label key={p.id} className="flex items-start gap-2 text-xs">
                      <input
                        type="checkbox"
                        checked={selected.has(p.key)}
                        onChange={() => togglePerm(p.key)}
                        className="mt-0.5"
                      />
                      <span>
                        <code className="rounded bg-muted px-1 py-0.5 text-xs">{p.key}</code>
                        <span className="ml-1 text-foreground">{p.displayName}</span>
                      </span>
                    </label>
                  ))}
                </div>
              </fieldset>
            ))}
          </div>
        </div>

        <ModalActions onCancel={onClose} isSubmitting={isSubmitting} />
      </form>
    </ModalShell>
  );
}

interface ModalShellProps {
  onClose: () => void;
  title: string;
  children: React.ReactNode;
}

function ModalShell({ onClose, title, children }: ModalShellProps) {
  return (
    <div
      role="dialog"
      aria-modal="true"
      className="fixed inset-0 z-40 flex items-center justify-center bg-background/80 p-4 backdrop-blur-sm"
      onClick={onClose}
    >
      <div
        className="flex max-h-[90vh] w-full max-w-xl flex-col overflow-y-auto rounded-lg border border-border bg-card p-6 shadow-lg"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="mb-4 flex items-center justify-between">
          <h2 className="text-lg font-semibold">{title}</h2>
        </div>
        {children}
      </div>
    </div>
  );
}

function ModalActions({ onCancel, isSubmitting }: { onCancel: () => void; isSubmitting: boolean }) {
  const { t } = useTranslation();
  return (
    <div className="mt-2 flex justify-end gap-2">
      <button
        type="button"
        onClick={onCancel}
        className="rounded-md border border-border bg-card px-4 py-2 text-sm text-foreground hover:bg-secondary"
      >
        {t('common.cancel')}
      </button>
      <button
        type="submit"
        disabled={isSubmitting}
        className="inline-flex items-center gap-2 rounded-md bg-primary px-4 py-2 text-sm font-medium text-primary-foreground hover:bg-primary/90 disabled:opacity-60"
      >
        {isSubmitting ? <Loader2 className="h-4 w-4 animate-spin" /> : null}
        {t('common.save')}
      </button>
    </div>
  );
}

export default Roles;
