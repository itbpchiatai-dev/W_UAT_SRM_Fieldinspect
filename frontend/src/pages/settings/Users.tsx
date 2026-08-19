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
import axios from 'axios';
import { Eye, EyeOff, KeyRound, Loader2, Pencil, Plus, Search, UserCheck, UserX } from 'lucide-react';
import {
  bulkApproveUsers,
  createUser,
  deactivateUser,
  getUser,
  listUsers,
  resetUserPassword,
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

// Mirrors of the backend password policy (app/auth/password.py). The
// backend remains the source of truth and re-checks every rule — these
// exist purely so the common mistakes are caught before a round-trip.
const PASSWORD_MIN_LENGTH = 12;
// bcrypt's hard limit is 72 BYTES of UTF-8, not 72 characters (round
// 8-23A.1). Thai is 3 bytes/char, so ~25 Thai characters already exceed
// it while looking short — which is exactly why this must be measured in
// bytes with TextEncoder rather than via String.length.
const PASSWORD_MAX_BYTES = 72;

function passwordByteLength(value: string): number {
  return new TextEncoder().encode(value).length;
}

/**
 * Pull a safe, human-readable message out of a failed mutation.
 *
 * The backend's 4xx `detail` for these endpoints is already fixed, safe
 * Thai text that never echoes the submitted password (rounds 8-23A /
 * 8-23A.1), so surfacing it verbatim is both useful and safe. Anything
 * else (network failure, unexpected shape) collapses to the caller's
 * fallback rather than leaking a raw axios/stack string.
 *
 * Local copy on purpose — same convention as Plots.tsx's
 * plotMutationErrorMessage and MasterData.tsx's
 * masterDataMutationErrorMessage.
 */
function userMutationErrorMessage(error: unknown, fallback: string): string {
  if (axios.isAxiosError(error)) {
    const data = error.response?.data as { detail?: unknown } | string | undefined;
    if (typeof data === 'string') return data.trim() ? data : fallback;
    const detail = data?.detail;
    // A plain-string detail is the backend's own fixed, safe Thai message.
    // Anything else — notably pydantic's 422 array of per-field objects,
    // which can restate submitted input — collapses to the fallback.
    if (typeof detail === 'string' && detail.trim()) return detail;
    return fallback;
  }
  return fallback;
}

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
  // Submit-level API error for the profile form. Before round 8-23B this
  // did not exist: onSubmit awaited createUser with no try/catch and the
  // form rendered no error surface at all, so a rejected password left the
  // modal open with zero feedback (react-hook-form swallows the throw into
  // an unhandled rejection after clearing isSubmitting) — which read as
  // "the Add User button is stuck".
  const [submitError, setSubmitError] = useState('');
  const [resetOpen, setResetOpen] = useState(false);
  const [resetSuccess, setResetSuccess] = useState('');

  // Caller must already hold super_admin to assign it (backend mirrors
  // this in _require_role_assign — the UI just hides the checkbox so the
  // user doesn't tick it, hit Save, and bounce off a 403).
  const { user: currentUser } = useAuth();
  const isSuperAdmin = !!currentUser?.roles?.some((r) => r.name === 'internal:super_admin');
  const canResetPassword = useHasPermission('users.reset_password');

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
    setSubmitError('');
    // Round 8-23B — every failure path is handled HERE. Letting the
    // rejection escape means react-hook-form re-throws it (v7 does
    // `catch(e){s=e}` … `if(s) throw s`), producing an unhandled promise
    // rejection and, far worse, no visible feedback at all: the modal just
    // sits there. 400 (weak/oversized password), 409 (duplicate email),
    // 422, 500 and outright network failures all land in the same place.
    try {
      if (mode === 'create') {
        const payload: UserCreatePayload = {
          email: values.email,
          fullName: values.fullName,
          authProvider: values.authProvider,
          // Azure AD accounts never carry a password — the backend rejects
          // one, and sending it would put a secret on the wire for nothing.
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
    } catch (err) {
      // Keep the modal open with every non-password field intact so the
      // admin can fix the one thing that was wrong and retry.
      setSubmitError(userMutationErrorMessage(err, t('settings.users.saveError')));
      return;
    }
    // reset() also clears the password field — the secret never outlives
    // a successful create.
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

            {/* Round 8-23B — admin password reset entry point. Three gates,
                all mirrored server-side: the caller holds
                users.reset_password, the target is a LOCAL account, and it
                is not the caller's own account (self-reset must go through
                the self-service flow; the backend answers 403). */}
            {mode === 'edit' && existing ? (
              existing.authProvider === 'local' ? (
                canResetPassword && existing.id !== currentUser?.id ? (
                  <div className="mt-1 rounded-md border border-border/60 bg-secondary/20 p-3">
                    <button
                      type="button"
                      onClick={() => {
                        setResetSuccess('');
                        setResetOpen(true);
                      }}
                      className="inline-flex items-center gap-2 rounded-md border border-border bg-background px-3 py-2 text-sm font-medium shadow-sm hover:bg-secondary"
                    >
                      <KeyRound className="h-4 w-4" />
                      {t('settings.users.resetPassword.action')}
                    </button>
                    {resetSuccess ? (
                      <p role="status" className="mt-2 rounded-md border border-success/40 bg-success/10 px-3 py-2 text-xs text-success-readable">
                        {resetSuccess}
                      </p>
                    ) : null}
                  </div>
                ) : null
              ) : (
                <p className="mt-1 rounded-md border border-border/60 bg-secondary/20 px-3 py-2 text-xs text-muted-foreground">
                  {t('settings.users.resetPassword.azureNotice')}
                </p>
              )
            ) : null}

            {submitError ? (
              <p role="alert" className="rounded-md border border-destructive/40 bg-destructive/10 px-3 py-2 text-xs text-destructive">
                {submitError}
              </p>
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

      {resetOpen && userId && existing ? (
        <ResetPasswordModal
          userId={userId}
          email={existing.email}
          onClose={() => setResetOpen(false)}
          onSuccess={() => {
            setResetOpen(false);
            setResetSuccess(t('settings.users.resetPassword.success'));
          }}
        />
      ) : null}
    </div>
  );
}

/**
 * Admin "set new password" modal — deliberately SEPARATE from the profile
 * form so the password never enters UserUpdatePayload (the PATCH endpoint
 * has no password field, by design: setting a password is account
 * takeover and gets its own endpoint + its own permission).
 *
 * Secret handling: both values live only in this component's local state
 * and are wiped the moment the request succeeds. They are never put in a
 * React Query key, a URL, localStorage/sessionStorage, or a log line.
 */
function ResetPasswordModal({
  userId,
  email,
  onClose,
  onSuccess,
}: {
  userId: string;
  email: string;
  onClose: () => void;
  onSuccess: () => void;
}) {
  const { t } = useTranslation();
  const [newPassword, setNewPassword] = useState('');
  const [confirmPassword, setConfirmPassword] = useState('');
  const [reveal, setReveal] = useState(false);
  const [error, setError] = useState('');

  const resetM = useMutation({
    mutationFn: () => resetUserPassword(userId, newPassword),
    onSuccess: () => {
      // Wipe both secrets from state BEFORE anything else runs.
      setNewPassword('');
      setConfirmPassword('');
      setError('');
      onSuccess();
    },
    onError: (err: unknown) => {
      // Modal stays open, spinner stops (isPending flips back on its own),
      // and the field values are preserved so the admin can correct them.
      setError(userMutationErrorMessage(err, t('settings.users.resetPassword.errors.failed')));
    },
  });

  const busy = resetM.isPending;

  // Never allow a dismiss while the request is in flight — closing
  // mid-request would strand a mutation whose result nobody reports.
  //
  // stopPropagation matters: this modal renders INSIDE the UserEditor
  // dialog, whose own root has onClick={onClose}. Without it, a backdrop
  // click here would bubble up and tear down the editor behind it too.
  const requestClose = (e?: React.MouseEvent) => {
    e?.stopPropagation();
    if (busy) return;
    onClose();
  };

  const onSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    if (busy) return; // double-submit guard
    setError('');

    if (!newPassword) {
      setError(t('settings.users.resetPassword.errors.required'));
      return;
    }
    if (newPassword !== confirmPassword) {
      setError(t('settings.users.resetPassword.errors.mismatch'));
      return;
    }
    if (newPassword.length < PASSWORD_MIN_LENGTH) {
      setError(t('settings.users.resetPassword.errors.tooShort'));
      return;
    }
    // Byte-length gate — never truncates, only refuses.
    if (passwordByteLength(newPassword) > PASSWORD_MAX_BYTES) {
      setError(t('settings.users.resetPassword.errors.tooLong'));
      return;
    }
    resetM.mutate();
  };

  const inputCls =
    'w-full rounded-md border border-input bg-background px-3 py-2 pr-10 text-sm focus:outline-none focus:ring-2 focus:ring-ring';

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-label={t('settings.users.resetPassword.title')}
      className="fixed inset-0 z-50 flex items-center justify-center bg-background/80 p-4 backdrop-blur-sm"
      onClick={requestClose}
    >
      <div
        className="w-full max-w-md rounded-lg border border-border bg-card p-6 shadow-lg"
        onClick={(e) => e.stopPropagation()}
      >
        <h2 className="text-base font-semibold">{t('settings.users.resetPassword.title')}</h2>
        <p className="mt-1 text-xs text-muted-foreground">
          {t('settings.users.resetPassword.forUser', { email })}
        </p>

        <form onSubmit={onSubmit} className="mt-4 flex flex-col gap-3">
          <label className="flex flex-col gap-1 text-sm">
            <span className="font-medium">{t('settings.users.resetPassword.newPassword')}</span>
            <span className="relative flex items-center">
              <input
                type={reveal ? 'text' : 'password'}
                autoComplete="new-password"
                value={newPassword}
                disabled={busy}
                onChange={(e) => setNewPassword(e.target.value)}
                className={inputCls}
              />
              <button
                type="button"
                onClick={() => setReveal((v) => !v)}
                title={t(reveal ? 'settings.users.resetPassword.hide' : 'settings.users.resetPassword.show')}
                aria-label={t(reveal ? 'settings.users.resetPassword.hide' : 'settings.users.resetPassword.show')}
                className="absolute right-2 text-muted-foreground hover:text-foreground"
              >
                {reveal ? <EyeOff className="h-4 w-4" /> : <Eye className="h-4 w-4" />}
              </button>
            </span>
          </label>

          <label className="flex flex-col gap-1 text-sm">
            <span className="font-medium">{t('settings.users.resetPassword.confirmPassword')}</span>
            <input
              type={reveal ? 'text' : 'password'}
              autoComplete="new-password"
              value={confirmPassword}
              disabled={busy}
              onChange={(e) => setConfirmPassword(e.target.value)}
              className="w-full rounded-md border border-input bg-background px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-ring"
            />
          </label>

          <p className="text-xs text-muted-foreground">{t('settings.users.resetPassword.hint')}</p>

          {error ? (
            <p role="alert" className="rounded-md border border-destructive/40 bg-destructive/10 px-3 py-2 text-xs text-destructive">
              {error}
            </p>
          ) : null}

          <div className="mt-2 flex justify-end gap-2">
            <button
              type="button"
              onClick={(e) => requestClose(e)}
              disabled={busy}
              className="rounded-md border border-border bg-card px-4 py-2 text-sm text-foreground hover:bg-secondary disabled:opacity-60"
            >
              {t('common.cancel')}
            </button>
            <button
              type="submit"
              disabled={busy}
              className="inline-flex items-center gap-2 rounded-md bg-primary px-4 py-2 text-sm font-medium text-primary-foreground hover:bg-primary/90 disabled:opacity-60"
            >
              {busy ? <Loader2 className="h-4 w-4 animate-spin" /> : null}
              {t('settings.users.resetPassword.submit')}
            </button>
          </div>
        </form>
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
