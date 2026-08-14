/**
 * Users — paginated list + create/edit/deactivate + per-user
 * permission override management (Pattern B).
 *
 * The detail panel has two tabs:
 *   "Profile"  — basic fields + role multi-select.
 *   "Overrides" — full permission list with grant/revoke toggles per row.
 *
 * Server-side enforcement remains the source of truth — this page is
 * purely a UX shell over /api/v1/users + /api/v1/users/{id}/overrides.
 */
import { useMemo, useState } from 'react';
import { useForm } from 'react-hook-form';
import { z } from 'zod';
import { zodResolver } from '@hookform/resolvers/zod';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useTranslation } from 'react-i18next';
import { Loader2, Pencil, Plus, Search, UserCheck, UserX } from 'lucide-react';
import {
  bulkApproveUsers,
  createUser,
  deactivateUser,
  getUser,
  listUsers,
  setUserOverride,
  updateUser,
  type UserCreatePayload,
} from '../../api/users';
import { listSuppliers, type SupplierSummary } from '../../api/suppliers';
import { listRoles } from '../../api/roles';
import { useAuth } from '../../hooks/useAuth';
import { useHasPermission } from '../../hooks/useHasPermission';
import { groupByCategory, listPermissions } from '../../api/permissions';
import type { Permission, RoleSummary, UserDetail, UserSummary } from '../../types/auth';

const PAGE_SIZE = 20;

const SUPPLIER_ROLE_PREFIXES = ['supplier:', 'farmlog:field_officer'];

const userSchema = z.object({
  email: z.string().email('auth.login.emailInvalid'),
  fullName: z.string().min(1, 'common.required'),
  authProvider: z.enum(['local', 'azure_ad']),
  password: z.string().optional().default(''),
  roleNames: z.array(z.string()).default([]),
  supplierId: z.string().nullable().optional(),
  isSupplierAdmin: z.boolean().default(false),
});
type UserFormValues = z.infer<typeof userSchema>;

export function Users() {
  const { t } = useTranslation();
  const qc = useQueryClient();
  const [page, setPage] = useState(0);
  const [q, setQ] = useState('');
  const [editingId, setEditingId] = useState<string | null>(null);
  const [creating, setCreating] = useState(false);

  const { data: users = [], isLoading } = useQuery({
    queryKey: ['users', page, q],
    queryFn: () => listUsers({ limit: PAGE_SIZE, offset: page * PAGE_SIZE, q: q || undefined }),
  });

  const deactivateM = useMutation({
    mutationFn: (id: string) => deactivateUser(id),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['users'] }),
  });

  const toggleApproveM = useMutation({
    mutationFn: ({ id, isApproved }: { id: string; isApproved: boolean }) =>
      updateUser(id, { isApproved }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['users'] }),
  });

  const bulkApproveM = useMutation({
    mutationFn: (ids: string[]) => bulkApproveUsers(ids),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['users'] }),
  });

  const pendingVisible = users.filter((u: UserSummary) => !u.isApproved);

  return (
    <div className="container mx-auto px-4 py-8 sm:px-6 lg:px-8">
      <header className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
        <div>
          <h1 className="text-xl font-bold">{t('settings.users.title')}</h1>
          <p className="mt-1 text-sm text-muted-foreground">{t('settings.users.description')}</p>
        </div>
        <button
          type="button"
          onClick={() => setCreating(true)}
          className="inline-flex items-center justify-center gap-2 rounded-md bg-primary px-4 py-2 text-sm font-medium text-primary-foreground shadow-sm transition-colors hover:bg-primary/90"
        >
          <Plus className="h-4 w-4" />
          {t('settings.users.new')}
        </button>
      </header>

      <div className="mt-4 flex flex-wrap items-center gap-2">
        <label className="relative flex flex-1 items-center sm:max-w-sm">
          <Search className="absolute left-2 h-4 w-4 text-muted-foreground" />
          <input
            type="search"
            value={q}
            onChange={(e) => {
              setPage(0);
              setQ(e.target.value);
            }}
            placeholder={t('settings.users.searchPlaceholder')}
            className="w-full rounded-md border border-input bg-background py-2 pl-8 pr-3 text-sm focus:outline-none focus:ring-2 focus:ring-ring"
          />
        </label>
        {pendingVisible.length > 0 ? (
          <button
            type="button"
            onClick={() => {
              if (confirm(t('settings.users.confirmApproveAll', { count: pendingVisible.length }))) {
                bulkApproveM.mutate(pendingVisible.map((u) => u.id));
              }
            }}
            disabled={bulkApproveM.isPending}
            className="inline-flex items-center gap-2 rounded-md bg-success px-3 py-2 text-sm font-medium text-success-foreground shadow-sm hover:bg-success/90 disabled:opacity-60"
          >
            {bulkApproveM.isPending ? <Loader2 className="h-4 w-4 animate-spin" /> : <UserCheck className="h-4 w-4" />}
            {t('settings.users.approveAllVisible')} ({pendingVisible.length})
          </button>
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
                <th className="px-4 py-2">{t('settings.users.fields.email')}</th>
                <th className="px-4 py-2">{t('settings.users.fields.fullName')}</th>
                <th className="px-4 py-2">{t('settings.users.fields.authProvider')}</th>
                <th className="px-4 py-2">{t('settings.users.fields.roles')}</th>
                <th className="px-4 py-2">{t('settings.users.fields.approved')}</th>
                <th className="px-4 py-2">{t('settings.users.fields.active')}</th>
                <th className="px-4 py-2">{t('settings.users.fields.lastLogin')}</th>
                <th className="px-4 py-2 text-right">{t('common.actions')}</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-border">
              {users.map((u: UserSummary) => (
                <tr key={u.id} className="hover:bg-secondary/30">
                  <td className="px-4 py-2 font-mono text-sm">{u.email}</td>
                  <td className="px-4 py-2">{u.fullName}</td>
                  <td className="px-4 py-2">
                    <span className="rounded-full bg-muted px-2 py-0.5 text-xs">{u.authProvider}</span>
                  </td>
                  <td className="px-4 py-2">
                    {u.roles && u.roles.length > 0 ? (
                      <div className="flex flex-wrap gap-1">
                        {u.roles.map((r) => (
                          <span key={r.id} className="rounded-full bg-primary/10 px-2 py-0.5 text-xs text-primary">
                            {r.displayName}
                          </span>
                        ))}
                      </div>
                    ) : (
                      <span className="text-xs text-muted-foreground">—</span>
                    )}
                  </td>
                  <td className="px-4 py-2">
                    <button
                      type="button"
                      onClick={() => toggleApproveM.mutate({ id: u.id, isApproved: !u.isApproved })}
                      disabled={toggleApproveM.isPending}
                      className={`inline-flex h-5 w-9 items-center rounded-full border transition-colors disabled:opacity-60 ${
                        u.isApproved ? 'border-success bg-success' : 'border-border bg-input'
                      }`}
                      aria-label={u.isApproved ? t('settings.users.unapprove') : t('settings.users.approve')}
                      title={u.isApproved ? t('settings.users.unapprove') : t('settings.users.approve')}
                    >
                      <span
                        className={`inline-block h-4 w-4 transform rounded-full bg-white shadow ring-1 ring-black/5 transition-transform ${
                          u.isApproved ? 'translate-x-4' : 'translate-x-0.5'
                        }`}
                      />
                    </button>
                  </td>
                  <td className="px-4 py-2">
                    {u.isActive ? (
                      <span className="rounded-full bg-success/15 px-2 py-0.5 text-xs text-success-readable">
                        {t('common.yes')}
                      </span>
                    ) : (
                      <span className="rounded-full bg-destructive/15 px-2 py-0.5 text-xs text-destructive">
                        {t('common.no')}
                      </span>
                    )}
                  </td>
                  <td className="px-4 py-2 text-sm text-muted-foreground">
                    {u.lastLoginAt ? new Date(u.lastLoginAt).toLocaleString() : '—'}
                  </td>
                  <td className="px-4 py-2 text-right">
                    <div className="flex justify-end gap-1">
                      <button
                        type="button"
                        onClick={() => setEditingId(u.id)}
                        className="rounded-md p-1.5 text-foreground hover:bg-secondary"
                        aria-label={t('common.edit')}
                      >
                        <Pencil className="h-4 w-4" />
                      </button>
                      <button
                        type="button"
                        disabled={!u.isActive}
                        onClick={() => {
                          if (confirm(t('settings.users.confirmDeactivate', { email: u.email }))) {
                            deactivateM.mutate(u.id);
                          }
                        }}
                        className="rounded-md p-1.5 text-destructive hover:bg-destructive/10 disabled:cursor-not-allowed disabled:opacity-40"
                        aria-label={t('settings.users.deactivate')}
                      >
                        <UserX className="h-4 w-4" />
                      </button>
                    </div>
                  </td>
                </tr>
              ))}
              {users.length === 0 ? (
                <tr>
                  <td colSpan={8} className="px-4 py-6 text-center text-sm text-muted-foreground">
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
        <span className="px-2 py-1.5 text-muted-foreground">{page + 1}</span>
        <button
          type="button"
          onClick={() => setPage((p) => p + 1)}
          disabled={users.length < PAGE_SIZE}
          className="rounded-md border border-border bg-card px-3 py-1.5 hover:bg-secondary disabled:opacity-40"
        >
          {t('common.next')}
        </button>
      </nav>

      {creating ? (
        <UserEditor
          mode="create"
          onClose={() => {
            setCreating(false);
            qc.invalidateQueries({ queryKey: ['users'] });
          }}
        />
      ) : null}

      {editingId ? (
        <UserEditor
          mode="edit"
          userId={editingId}
          onClose={() => {
            setEditingId(null);
            qc.invalidateQueries({ queryKey: ['users'] });
          }}
        />
      ) : null}
    </div>
  );
}

interface UserEditorProps {
  mode: 'create' | 'edit';
  userId?: string;
  onClose: () => void;
}

function UserEditor({ mode, userId, onClose }: UserEditorProps) {
  const { t } = useTranslation();
  const qc = useQueryClient();
  const [tab, setTab] = useState<'profile' | 'overrides'>('profile');

  // Caller must already hold super_admin to assign it (backend mirrors
  // this in _require_role_assign — the UI just hides the checkbox so the
  // user doesn't tick it, hit Save, and bounce off a 403).
  const { user: currentUser } = useAuth();
  const isSuperAdmin = !!currentUser?.roles?.some((r) => r.name === 'internal:super_admin');

  const { data: allRoles = [] } = useQuery({ queryKey: ['roles'], queryFn: listRoles });
  const roles = isSuperAdmin
    ? allRoles
    : allRoles.filter((r: RoleSummary) => r.name !== 'internal:super_admin');
  const { data: suppliers = [] } = useQuery<SupplierSummary[]>({
    queryKey: ['suppliers-active'],
    queryFn: () => listSuppliers({ activeOnly: true, limit: 200 }),
  });
  const { data: perms = [] } = useQuery({
    queryKey: ['permissions'],
    queryFn: listPermissions,
    staleTime: 5 * 60 * 1000,
  });
  const { data: existing } = useQuery<UserDetail>({
    queryKey: ['user', userId],
    queryFn: () => getUser(userId!),
    enabled: mode === 'edit' && !!userId,
  });

  const defaults: UserFormValues = useMemo(
    () => ({
      email: existing?.email ?? '',
      fullName: existing?.fullName ?? '',
      authProvider: (existing?.authProvider ?? 'local') as UserFormValues['authProvider'],
      password: '',
      roleNames: existing?.roles?.map((r) => r.name) ?? [],
      supplierId: existing?.supplierId ?? null,
      isSupplierAdmin: existing?.isSupplierAdmin ?? false,
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
  } = useForm<UserFormValues>({
    resolver: zodResolver(userSchema),
    values: defaults,
  });

  const selectedRoles = new Set(watch('roleNames'));
  const authProvider = watch('authProvider');
  const needsSupplier = Array.from(selectedRoles).some((r) =>
    SUPPLIER_ROLE_PREFIXES.some((prefix) => r.startsWith(prefix))
  );

  const toggleRole = (name: string) => {
    const next = new Set(selectedRoles);
    if (next.has(name)) next.delete(name);
    else next.add(name);
    setValue('roleNames', Array.from(next), { shouldDirty: true });
  };

  const onSubmit = async (values: UserFormValues) => {
    const supplierPayload = {
      supplierId: values.supplierId || null,
      isSupplierAdmin: values.isSupplierAdmin,
    };
    if (mode === 'create') {
      const payload: UserCreatePayload = {
        email: values.email,
        fullName: values.fullName,
        authProvider: values.authProvider,
        password: values.authProvider === 'local' ? values.password : undefined,
        roleNames: values.roleNames,
        ...supplierPayload,
      };
      await createUser(payload);
    } else if (userId) {
      await updateUser(userId, {
        fullName: values.fullName,
        roleNames: values.roleNames,
        ...supplierPayload,
      });
    }
    reset();
    qc.invalidateQueries({ queryKey: ['users'] });
    onClose();
  };

  return (
    <div
      role="dialog"
      aria-modal="true"
      className="fixed inset-0 z-40 flex items-center justify-center bg-background/80 p-4 backdrop-blur-sm"
      onClick={onClose}
    >
      <div
        className="flex max-h-[90vh] w-full max-w-2xl flex-col overflow-y-auto rounded-lg border border-border bg-card p-6 shadow-lg"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="mb-4 flex items-center justify-between">
          <h2 className="text-lg font-semibold">
            {t(mode === 'create' ? 'settings.users.new' : 'settings.users.edit')}
          </h2>
          {mode === 'edit' ? (
            <div className="flex gap-1 rounded-md border border-border p-1 text-xs">
              <button
                type="button"
                onClick={() => setTab('profile')}
                className={`rounded px-2 py-1 ${tab === 'profile' ? 'bg-secondary' : ''}`}
              >
                {t('settings.users.tabs.profile')}
              </button>
              <button
                type="button"
                onClick={() => setTab('overrides')}
                className={`rounded px-2 py-1 ${tab === 'overrides' ? 'bg-secondary' : ''}`}
              >
                {t('settings.users.tabs.overrides')}
              </button>
            </div>
          ) : null}
        </div>

        {tab === 'profile' || mode === 'create' ? (
          <form onSubmit={handleSubmit(onSubmit)} className="flex flex-col gap-3">
            <label className="flex flex-col gap-1 text-sm">
              <span className="font-medium">{t('settings.users.fields.email')}</span>
              <input
                type="email"
                readOnly={mode === 'edit'}
                {...register('email')}
                className="rounded-md border border-input bg-background px-3 py-2 text-sm read-only:opacity-70 focus:outline-none focus:ring-2 focus:ring-ring"
              />
              {errors.email ? (
                <span className="text-xs text-destructive">{t(errors.email.message ?? '')}</span>
              ) : null}
            </label>

            <label className="flex flex-col gap-1 text-sm">
              <span className="font-medium">{t('settings.users.fields.fullName')}</span>
              <input
                type="text"
                {...register('fullName')}
                className="rounded-md border border-input bg-background px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-ring"
              />
              {errors.fullName ? (
                <span className="text-xs text-destructive">{t(errors.fullName.message ?? '')}</span>
              ) : null}
            </label>

            <label className="flex flex-col gap-1 text-sm">
              <span className="font-medium">{t('settings.users.fields.authProvider')}</span>
              <select
                {...register('authProvider')}
                disabled={mode === 'edit'}
                className="rounded-md border border-input bg-background px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-ring disabled:opacity-70"
              >
                <option value="local">local</option>
                <option value="azure_ad">azure_ad</option>
              </select>
            </label>

            {mode === 'create' && authProvider === 'local' ? (
              <label className="flex flex-col gap-1 text-sm">
                <span className="font-medium">{t('settings.users.fields.password')}</span>
                <input
                  type="password"
                  autoComplete="new-password"
                  {...register('password')}
                  className="rounded-md border border-input bg-background px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-ring"
                />
              </label>
            ) : null}

            <div>
              <span className="text-sm font-medium">{t('settings.users.fields.roles')}</span>
              <div className="mt-2 grid grid-cols-1 gap-1 sm:grid-cols-2">
                {roles.map((r: RoleSummary) => (
                  <label key={r.id} className="flex items-center gap-2 text-xs">
                    <input
                      type="checkbox"
                      checked={selectedRoles.has(r.name)}
                      onChange={() => toggleRole(r.name)}
                    />
                    <span>{r.displayName} <code className="ml-1 rounded bg-muted px-1 text-xs">{r.name}</code></span>
                  </label>
                ))}
              </div>
            </div>

            {needsSupplier || watch('supplierId') ? (
              <div className="flex flex-col gap-2 rounded-md border border-border/60 bg-secondary/20 p-3">
                <span className="text-xs font-semibold text-muted-foreground uppercase">ผูก Supplier</span>
                <label className="flex flex-col gap-1 text-sm">
                  <span className="font-medium">Supplier</span>
                  <select
                    {...register('supplierId')}
                    className="rounded-md border border-input bg-background px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-ring"
                  >
                    <option value="">— ไม่ผูก Supplier —</option>
                    {suppliers.map((s: SupplierSummary) => (
                      <option key={s.id} value={s.id}>{s.code} — {s.name}</option>
                    ))}
                  </select>
                </label>
                <label className="flex items-center gap-2 text-sm">
                  <input type="checkbox" {...register('isSupplierAdmin')} />
                  <span>เป็น Supplier Admin (supplier:owner)</span>
                </label>
              </div>
            ) : null}

            <div className="mt-2 flex justify-end gap-2">
              <button
                type="button"
                onClick={onClose}
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
          </form>
        ) : (
          <OverridePanel userId={userId!} allPerms={perms} />
        )}
      </div>
    </div>
  );
}

function OverridePanel({ userId, allPerms }: { userId: string; allPerms: Permission[] }) {
  const { t } = useTranslation();
  const qc = useQueryClient();
  // Server enforces this too (POST /overrides -> 403). Mirror it client-side
  // so a user without the grant perm sees disabled buttons + a reason,
  // instead of clicking into a silent 403.
  const canEdit = useHasPermission('permissions.grant_override');
  const grouped = useMemo(() => groupByCategory(allPerms), [allPerms]);

  // Overrides come embedded on the UserDetail payload — re-read on mount.
  const { data: detail } = useQuery<UserDetail>({
    queryKey: ['user', userId],
    queryFn: () => getUser(userId),
  });
  const overrides = ((detail as unknown as { overrides?: { permissionKey: string; granted: boolean }[] })?.overrides) ?? [];
  const overrideMap = new Map(overrides.map((o) => [o.permissionKey, o.granted]));

  const mutate = useMutation({
    mutationFn: ({ key, granted }: { key: string; granted: boolean }) =>
      setUserOverride(userId, key, granted),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['user', userId] }),
  });

  return (
    <div className="max-h-[60vh] overflow-y-auto pr-2">
      <p className="mb-3 text-xs text-muted-foreground">{t('settings.users.overrides.help')}</p>
      {!canEdit ? (
        <p className="mb-3 rounded-md border border-warning/40 bg-warning/10 px-3 py-2 text-xs text-foreground">
          {t('settings.users.overrides.noPermission')}
        </p>
      ) : null}
      {mutate.isError ? (
        <p className="mb-3 rounded-md border border-destructive/40 bg-destructive/10 px-3 py-2 text-xs text-destructive">
          {t('settings.users.overrides.error')}
          {(() => {
            // Surface the backend reason (e.g. "Only super_admin can grant
            // permission overrides for privilege-management keys") so a
            // denied grant doesn't look like a random failure.
            const detail = (mutate.error as { response?: { data?: { detail?: string } } } | null)
              ?.response?.data?.detail;
            return detail ? ` — ${detail}` : null;
          })()}
        </p>
      ) : null}
      {Object.keys(grouped).sort().map((cat) => (
        <fieldset key={cat} className="mb-4">
          <legend className="text-xs font-semibold uppercase text-muted-foreground">{cat}</legend>
          <ul className="mt-1 divide-y divide-border">
            {grouped[cat]!.map((p) => {
              const current = overrideMap.get(p.key);
              return (
                <li key={p.id} className="flex items-center justify-between gap-2 py-2 text-xs">
                  <div>
                    <code className="rounded bg-muted px-1 py-0.5 text-xs">{p.key}</code>
                    <span className="ml-1">{p.displayName}</span>
                  </div>
                  <div className="flex gap-1">
                    <button
                      type="button"
                      disabled={!canEdit || mutate.isPending}
                      onClick={() => mutate.mutate({ key: p.key, granted: true })}
                      className={`rounded px-2 py-0.5 text-xs font-medium disabled:cursor-not-allowed disabled:opacity-50 ${current === true ? 'bg-success/20 text-success-readable' : 'bg-muted text-muted-foreground hover:bg-secondary'}`}
                    >
                      {t('settings.users.overrides.grant')}
                    </button>
                    <button
                      type="button"
                      disabled={!canEdit || mutate.isPending}
                      onClick={() => mutate.mutate({ key: p.key, granted: false })}
                      className={`rounded px-2 py-0.5 text-xs font-medium disabled:cursor-not-allowed disabled:opacity-50 ${current === false ? 'bg-destructive/20 text-destructive' : 'bg-muted text-muted-foreground hover:bg-secondary'}`}
                    >
                      {t('settings.users.overrides.revoke')}
                    </button>
                  </div>
                </li>
              );
            })}
          </ul>
        </fieldset>
      ))}
    </div>
  );
}

export default Users;
