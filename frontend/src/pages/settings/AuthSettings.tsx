/**
 * AuthSettings — admin toggle UI for provider/signup settings.
 *
 * Sections:
 *   1. Sign-in methods  — auth.local.enabled / auth.sso.enabled
 *   2. Self-signup      — auth.local.signup_enabled (only if local enabled)
 *   3. Default role     — auth.signup_default_role (only if signup enabled)
 *
 * AUTH_SCOPE ceiling: a project compiled with `internal_only` cannot
 * re-enable local at runtime; `external_only` cannot enable SSO. The
 * compile-time scope ships in VITE_AUTH_SCOPE (frontend .env) so we can
 * disable the toggle BEFORE the user tries — the backend also rejects.
 *
 * Save flow:
 *   - For each changed key, PUT /admin/settings/{key}.
 *   - On success, invalidate ['admin-settings'] AND ['public-auth-settings']
 *     so the Login page reflects the new state on next visit.
 */
import { useEffect, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useTranslation } from 'react-i18next';
import { Loader2, Lock } from 'lucide-react';
import { listAllSettings, updateSetting } from '../../api/adminSettings';
import { listRoles } from '../../api/roles';
import type { AppSettingValue, RoleSummary } from '../../types/auth';

const KEYS = {
  localEnabled: 'auth.local.enabled',
  ssoEnabled: 'auth.sso.enabled',
  signupEnabled: 'auth.local.signup_enabled',
  signupRole: 'auth.signup_default_role',
  autoApprove: 'auth.auto_approve_new_users',
  notificationsEnabled: 'notifications.email.enabled',
  adminRecipients: 'notifications.email.admin_recipients',
} as const;

type AuthScope = 'both' | 'internal_only' | 'external_only';
const SCOPE: AuthScope = ((import.meta.env.VITE_AUTH_SCOPE as AuthScope | undefined) ?? 'both');

interface FormState {
  localEnabled: boolean;
  ssoEnabled: boolean;
  signupEnabled: boolean;
  signupRole: string;
  autoApprove: boolean;
  notificationsEnabled: boolean;
  // Editable as comma-separated text; we split + dedupe on save.
  adminRecipientsRaw: string;
}

function asBool(v: unknown): boolean {
  if (typeof v === 'boolean') return v;
  if (typeof v === 'string') return v === 'true';
  return Boolean(v);
}
function asStr(v: unknown): string {
  return typeof v === 'string' ? v : v == null ? '' : String(v);
}

export function AuthSettings() {
  const { t } = useTranslation();
  const qc = useQueryClient();

  const { data: settings = [], isLoading } = useQuery<AppSettingValue[]>({
    queryKey: ['admin-settings'],
    queryFn: listAllSettings,
  });
  const { data: roles = [] } = useQuery({ queryKey: ['roles'], queryFn: listRoles });

  const byKey = new Map(settings.map((s) => [s.key, s]));
  const recipientsRaw = (() => {
    const v = byKey.get(KEYS.adminRecipients)?.value;
    if (Array.isArray(v)) return v.join(', ');
    return asStr(v ?? '');
  })();
  const initial: FormState = {
    localEnabled: asBool(byKey.get(KEYS.localEnabled)?.value ?? true),
    ssoEnabled: asBool(byKey.get(KEYS.ssoEnabled)?.value ?? true),
    signupEnabled: asBool(byKey.get(KEYS.signupEnabled)?.value ?? false),
    signupRole: asStr(byKey.get(KEYS.signupRole)?.value ?? 'external:user'),
    autoApprove: asBool(byKey.get(KEYS.autoApprove)?.value ?? false),
    notificationsEnabled: asBool(byKey.get(KEYS.notificationsEnabled)?.value ?? false),
    adminRecipientsRaw: recipientsRaw,
  };

  const [form, setForm] = useState<FormState>(initial);
  const [saveError, setSaveError] = useState<string | null>(null);

  // Refresh form when settings reload (e.g. invalidation after save).
  useEffect(() => {
    setForm(initial);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [settings]);

  // Compile-time AUTH_SCOPE ceiling (per docs/auth.md §12):
  //   internal_only = Azure AD only -> local auth is locked OFF.
  //   external_only = local only    -> SSO is locked OFF.
  // The backend (admin_settings.py _SCOPE_LOCKED_KEYS) re-enforces this on PUT;
  // disabling the toggle here is the matching UX so the user can't try.
  const localLockedOff = SCOPE === 'internal_only';
  const ssoLockedOff = SCOPE === 'external_only';

  const effectiveLocal = localLockedOff ? false : form.localEnabled;
  const effectiveSso = ssoLockedOff ? false : form.ssoEnabled;

  const saveM = useMutation({
    mutationFn: async (next: FormState) => {
      const ops: Promise<unknown>[] = [];
      if (next.localEnabled !== initial.localEnabled && !localLockedOff) {
        ops.push(updateSetting(KEYS.localEnabled, next.localEnabled));
      }
      if (next.ssoEnabled !== initial.ssoEnabled && !ssoLockedOff) {
        ops.push(updateSetting(KEYS.ssoEnabled, next.ssoEnabled));
      }
      if (next.signupEnabled !== initial.signupEnabled) {
        ops.push(updateSetting(KEYS.signupEnabled, next.signupEnabled));
      }
      if (next.signupRole !== initial.signupRole) {
        ops.push(updateSetting(KEYS.signupRole, next.signupRole));
      }
      if (next.autoApprove !== initial.autoApprove) {
        ops.push(updateSetting(KEYS.autoApprove, next.autoApprove));
      }
      if (next.notificationsEnabled !== initial.notificationsEnabled) {
        ops.push(updateSetting(KEYS.notificationsEnabled, next.notificationsEnabled));
      }
      if (next.adminRecipientsRaw !== initial.adminRecipientsRaw) {
        const parsed = Array.from(new Set(
          next.adminRecipientsRaw
            .split(/[,\n]/)
            .map((s) => s.trim())
            .filter((s) => s.length > 0 && s.includes('@'))
        ));
        ops.push(updateSetting(KEYS.adminRecipients, parsed));
      }
      await Promise.all(ops);
    },
    onSuccess: () => {
      setSaveError(null);
      qc.invalidateQueries({ queryKey: ['admin-settings'] });
      qc.invalidateQueries({ queryKey: ['public-auth-settings'] });
    },
    onError: (e: unknown) => {
      setSaveError(e instanceof Error ? e.message : 'unknown');
    },
  });

  if (isLoading) {
    return (
      <div className="container mx-auto flex min-h-[40vh] items-center justify-center px-4">
        <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
      </div>
    );
  }

  const externalRoles = roles.filter((r: RoleSummary) => r.name.startsWith('external:'));

  return (
    <div className="container mx-auto max-w-3xl px-4 py-8 sm:px-6 lg:px-8">
      <header>
        <h1 className="text-xl font-bold">{t('settings.auth.title')}</h1>
        <p className="mt-1 text-sm text-muted-foreground">{t('settings.auth.description')}</p>
      </header>

      <section className="mt-6 flex flex-col gap-4 rounded-lg border border-border bg-card p-6 shadow-sm">
        <h2 className="text-base font-semibold">{t('settings.auth.signIn.title')}</h2>
        <ToggleRow
          label={t('settings.auth.signIn.local')}
          checked={effectiveLocal}
          disabled={localLockedOff}
          lockHint={localLockedOff ? t('settings.auth.lockedByScope') : null}
          onChange={(v) => setForm({ ...form, localEnabled: v })}
        />
        <ToggleRow
          label={t('settings.auth.signIn.sso')}
          checked={effectiveSso}
          disabled={ssoLockedOff}
          lockHint={ssoLockedOff ? t('settings.auth.lockedByScope') : null}
          onChange={(v) => setForm({ ...form, ssoEnabled: v })}
        />
      </section>

      <section className="mt-4 flex flex-col gap-4 rounded-lg border border-border bg-card p-6 shadow-sm">
        <h2 className="text-base font-semibold">{t('settings.auth.signup.title')}</h2>
        <ToggleRow
          label={t('settings.auth.signup.enabled')}
          checked={form.signupEnabled}
          disabled={!effectiveLocal}
          lockHint={!effectiveLocal ? t('settings.auth.signup.requiresLocal') : null}
          onChange={(v) => setForm({ ...form, signupEnabled: v })}
        />

        {form.signupEnabled && effectiveLocal ? (
          <label className="flex flex-col gap-1 text-sm">
            <span className="font-medium">{t('settings.auth.signup.defaultRole')}</span>
            <select
              value={form.signupRole}
              onChange={(e) => setForm({ ...form, signupRole: e.target.value })}
              className="max-w-sm rounded-md border border-input bg-background px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-ring"
            >
              {externalRoles.length === 0 ? (
                <option value="external:user">external:user</option>
              ) : (
                externalRoles.map((r: RoleSummary) => (
                  <option key={r.id} value={r.name}>{r.displayName} ({r.name})</option>
                ))
              )}
            </select>
          </label>
        ) : null}
      </section>

      <section className="mt-4 flex flex-col gap-3 rounded-lg border border-border bg-card p-6 shadow-sm">
        <h2 className="text-base font-semibold">{t('settings.auth.approval.title')}</h2>
        <ToggleRow
          label={t('settings.auth.approval.autoApprove')}
          checked={form.autoApprove}
          onChange={(v) => setForm({ ...form, autoApprove: v })}
        />
        <p className="text-xs text-muted-foreground">{t('settings.auth.approval.help')}</p>
      </section>

      <section className="mt-4 flex flex-col gap-3 rounded-lg border border-border bg-card p-6 shadow-sm">
        <h2 className="text-base font-semibold">{t('settings.notifications.title')}</h2>
        <ToggleRow
          label={t('settings.notifications.emailEnabled')}
          checked={form.notificationsEnabled}
          onChange={(v) => setForm({ ...form, notificationsEnabled: v })}
        />
        <p className="text-xs text-muted-foreground">{t('settings.notifications.help')}</p>
        {form.notificationsEnabled ? (
          <label className="flex flex-col gap-1 text-sm">
            <span className="font-medium">{t('settings.notifications.adminRecipients')}</span>
            <textarea
              value={form.adminRecipientsRaw}
              onChange={(e) => setForm({ ...form, adminRecipientsRaw: e.target.value })}
              rows={3}
              placeholder="admin1@chiataigroup.com, admin2@chiataigroup.com"
              className="rounded-md border border-input bg-background px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-ring"
            />
            <span className="text-xs text-muted-foreground">{t('settings.notifications.recipientsHelp')}</span>
          </label>
        ) : null}
      </section>

      {saveError ? (
        <p className="mt-4 rounded-md border border-destructive/40 bg-destructive/10 px-3 py-2 text-sm text-destructive">
          {saveError}
        </p>
      ) : null}

      <div className="mt-6 flex justify-end">
        <button
          type="button"
          onClick={() => saveM.mutate(form)}
          disabled={saveM.isPending}
          className="inline-flex items-center gap-2 rounded-md bg-primary px-4 py-2 text-sm font-medium text-primary-foreground shadow-sm transition-colors hover:bg-primary/90 disabled:opacity-60"
        >
          {saveM.isPending ? <Loader2 className="h-4 w-4 animate-spin" /> : null}
          {t('common.save')}
        </button>
      </div>
    </div>
  );
}

interface ToggleRowProps {
  label: string;
  checked: boolean;
  disabled?: boolean;
  lockHint?: string | null;
  onChange: (v: boolean) => void;
}

function ToggleRow({ label, checked, disabled, lockHint, onChange }: ToggleRowProps) {
  return (
    <div className="flex items-center justify-between gap-3 text-sm">
      <span className="flex items-center gap-2">
        <span className="font-medium">{label}</span>
        {disabled && lockHint ? (
          <span title={lockHint} className="flex items-center gap-1 text-xs text-muted-foreground">
            <Lock className="h-3 w-3" />
            {lockHint}
          </span>
        ) : null}
      </span>
      <label className="relative inline-flex cursor-pointer items-center">
        <input
          type="checkbox"
          checked={checked}
          disabled={disabled}
          onChange={(e) => onChange(e.target.checked)}
          className="peer sr-only"
        />
        <span className="h-6 w-11 rounded-full border border-border bg-input transition-colors peer-checked:border-primary peer-checked:bg-primary peer-disabled:opacity-50" />
        <span className="absolute left-1 top-1 h-4 w-4 rounded-full bg-white shadow ring-1 ring-black/5 transition-transform peer-checked:translate-x-5" />
      </label>
    </div>
  );
}

export default AuthSettings;
