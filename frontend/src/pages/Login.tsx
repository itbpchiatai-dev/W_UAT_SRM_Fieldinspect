/**
 * Login page — local form + SSO button, gated by app settings.
 *
 * Decision matrix (after /admin/settings/public resolves):
 *   sso=on,  local=on  → both shown (form + SSO button on the side).
 *   sso=on,  local=off → SSO button only.
 *   sso=off, local=on  → form only.
 *   sso=off, local=off → "Login is disabled" message.
 *
 * `?return=<path>` query param: if present, navigate there post-login.
 */
import { useEffect, useState } from 'react';
import { useNavigate, useSearchParams } from 'react-router-dom';
import { useForm } from 'react-hook-form';
import { z } from 'zod';
import { zodResolver } from '@hookform/resolvers/zod';
import { useTranslation } from 'react-i18next';
import { Eye, EyeOff, Languages, Loader2, Moon, Sun } from 'lucide-react';
import { useAuth } from '../hooks/useAuth';
import { ssoRedirect } from '../api/auth';
import { getPublicAuthSettings } from '../api/adminSettings';
import { safeReturn } from '../lib/safe-redirect';
import { useUI } from '../stores/ui';
import type { PublicAuthSettings } from '../types/auth';

const loginSchema = z.object({
  email: z.string().min(1, 'auth.login.emailRequired').email('auth.login.emailInvalid'),
  password: z.string().min(1, 'auth.login.passwordRequired'),
});
type LoginValues = z.infer<typeof loginSchema>;

export function Login() {
  const { t } = useTranslation();
  const { login, isAuthenticated } = useAuth();
  const navigate = useNavigate();
  const [params] = useSearchParams();
  // Round-4 HIGH-3: validate the untrusted ?return= before feeding it
  // to navigate(). safeReturn() rejects protocol-relative URLs, external
  // origins, javascript:/data:, backslash confusion, etc. — defaults to '/'.
  const returnTo = safeReturn(params.get('return'), '/');
  // Same UI store the TopBar uses — so a choice made on Login carries
  // straight into the app shell after sign-in (and vice-versa).
  const { theme, locale, toggleTheme, setLocale } = useUI();
  const isDark = theme === 'dark';

  const [settings, setSettings] = useState<PublicAuthSettings | null>(null);
  const [settingsError, setSettingsError] = useState(false);
  const [submitError, setSubmitError] = useState<string | null>(null);
  const [ssoLoading, setSsoLoading] = useState(false);
  const [showPassword, setShowPassword] = useState(false);

  const {
    register,
    handleSubmit,
    formState: { errors, isSubmitting },
  } = useForm<LoginValues>({ resolver: zodResolver(loginSchema) });

  // If the user is already logged in (e.g. navigated back here), bounce
  // them to the intended return path immediately.
  useEffect(() => {
    if (isAuthenticated) {
      navigate(returnTo, { replace: true });
    }
  }, [isAuthenticated, navigate, returnTo]);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const s = await getPublicAuthSettings();
        if (!cancelled) setSettings(s);
      } catch {
        if (!cancelled) setSettingsError(true);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  async function onSubmit(values: LoginValues) {
    setSubmitError(null);
    try {
      await login(values.email.trim().toLowerCase(), values.password);
      navigate(returnTo, { replace: true });
    } catch (err) {
      // Distinguish "not approved" (403 + detail token) from bad creds —
      // approval is a friendly state, not a security event the user should
      // feel they fumbled.
      const detail = (err as { response?: { data?: { detail?: string }; status?: number } })
        ?.response;
      if (detail?.status === 403 && detail?.data?.detail === 'account_not_approved') {
        setSubmitError(t('auth.login.notApproved'));
      } else {
        setSubmitError(t('auth.login.invalidCredentials'));
      }
    }
  }

  async function onSsoClick() {
    setSsoLoading(true);
    try {
      const { url } = await ssoRedirect();
      window.location.href = url;
    } catch {
      setSsoLoading(false);
      setSubmitError(t('auth.login.invalidCredentials'));
    }
  }

  // Settings haven't loaded yet — show a spinner; we don't know which
  // form to render.
  if (settings === null && !settingsError) {
    return (
      <main className="flex min-h-screen items-center justify-center">
        <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
      </main>
    );
  }

  // Settings endpoint failed — assume both providers are available so the
  // user has SOME way to sign in. The backend will reject correctly if
  // they're actually disabled.
  const localEnabled = settingsError ? true : settings!.authLocalEnabled;
  const ssoEnabled = settingsError ? true : settings!.authSsoEnabled;
  const bothDisabled = !localEnabled && !ssoEnabled;

  return (
    <main className="relative flex min-h-screen items-center justify-center bg-background px-4">
      {/* Lang + theme controls — pinned outside the card so they're available
          BEFORE login (the TopBar with the same controls only shows after).
          Uses the same UI store so the choice carries into the app shell. */}
      <div className="absolute right-4 top-4 flex items-center gap-1">
        <button
          type="button"
          onClick={() => setLocale(locale === 'th' ? 'en' : 'th')}
          className="inline-flex items-center gap-1 rounded-md bg-card/80 px-2 py-1.5 text-xs font-medium text-foreground shadow-sm hover:bg-secondary"
          aria-label={t('common.language')}
          title={t('common.language')}
        >
          <Languages className="h-4 w-4" />
          <span className="uppercase">{locale}</span>
        </button>
        <button
          type="button"
          onClick={toggleTheme}
          className="inline-flex items-center rounded-md bg-card/80 p-2 text-foreground shadow-sm hover:bg-secondary"
          aria-label={isDark ? t('common.lightMode') : t('common.darkMode')}
          title={isDark ? t('common.lightMode') : t('common.darkMode')}
        >
          {isDark ? <Sun className="h-4 w-4" /> : <Moon className="h-4 w-4" />}
        </button>
      </div>

      <div className="w-full max-w-md rounded-lg border border-border bg-card p-8 shadow-sm">
        {/* Brand header — CT mark in primary green + centred title, so the
            login page reads as Chia Tai-branded from the first glance. */}
        <div className="mb-7 flex flex-col items-center text-center">
          <div className="mb-3 flex h-14 w-14 items-center justify-center rounded-xl bg-primary text-lg font-bold tracking-wide text-primary-foreground shadow-md">
            CT
          </div>
          <h1 className="text-xl font-bold text-foreground">{t('auth.login.title')}</h1>
        </div>

        {bothDisabled ? (
          <p className="mt-6 rounded-md border border-warning/40 bg-warning/10 p-3 text-sm text-foreground">
            {t('auth.login.bothDisabled')}
          </p>
        ) : null}

        {/* SSO is the primary path for internal users → solid CT green
            (--primary), shown FIRST with the Microsoft mark. */}
        {ssoEnabled ? (
          <button
            type="button"
            onClick={onSsoClick}
            disabled={ssoLoading}
            className="inline-flex w-full items-center justify-center gap-2 rounded-md bg-primary px-4 py-2.5 text-sm font-semibold text-primary-foreground shadow-sm transition-colors hover:bg-primary/90 disabled:opacity-60"
          >
            {ssoLoading ? (
              <Loader2 className="h-4 w-4 animate-spin" />
            ) : (
              <svg className="h-4 w-4" viewBox="0 0 23 23" aria-hidden="true">
                <rect x="1" y="1" width="10" height="10" fill="#f25022" />
                <rect x="12" y="1" width="10" height="10" fill="#7fba00" />
                <rect x="1" y="12" width="10" height="10" fill="#00a4ef" />
                <rect x="12" y="12" width="10" height="10" fill="#ffb900" />
              </svg>
            )}
            {t('auth.login.sso')}
          </button>
        ) : null}

        {localEnabled && ssoEnabled ? (
          <div className="my-5 flex items-center gap-2 text-xs text-muted-foreground">
            <span className="h-px flex-1 bg-border" />
            <span>{t('auth.login.or')}</span>
            <span className="h-px flex-1 bg-border" />
          </div>
        ) : null}

        {localEnabled ? (
          <form onSubmit={handleSubmit(onSubmit)} className={ssoEnabled ? 'flex flex-col gap-4' : 'mt-6 flex flex-col gap-4'}>
            <label className="flex flex-col gap-1 text-sm">
              <span className="font-medium text-foreground">{t('auth.login.email')}</span>
              <input
                type="email"
                autoComplete="username"
                {...register('email')}
                className="rounded-md border border-input bg-background px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-ring"
              />
              {errors.email ? (
                <span className="text-xs text-destructive">{t(errors.email.message ?? '')}</span>
              ) : null}
            </label>

            <label className="flex flex-col gap-1 text-sm">
              <span className="font-medium text-foreground">{t('auth.login.password')}</span>
              <div className="relative">
                <input
                  type={showPassword ? 'text' : 'password'}
                  autoComplete="current-password"
                  {...register('password')}
                  className="w-full rounded-md border border-input bg-background px-3 py-2 pr-10 text-sm focus:outline-none focus:ring-2 focus:ring-ring"
                />
                <button
                  type="button"
                  onClick={() => setShowPassword((value) => !value)}
                  className="absolute inset-y-0 right-0 inline-flex w-10 items-center justify-center rounded-r-md text-muted-foreground hover:text-foreground focus:outline-none focus:ring-2 focus:ring-ring"
                  aria-label={showPassword ? t('auth.login.hidePassword') : t('auth.login.showPassword')}
                  title={showPassword ? t('auth.login.hidePassword') : t('auth.login.showPassword')}
                >
                  {showPassword ? <EyeOff className="h-4 w-4" /> : <Eye className="h-4 w-4" />}
                </button>
              </div>
              {errors.password ? (
                <span className="text-xs text-destructive">{t(errors.password.message ?? '')}</span>
              ) : null}
            </label>

            {submitError ? (
              <p className="rounded-md border border-destructive/40 bg-destructive/10 px-3 py-2 text-sm text-destructive">
                {submitError}
              </p>
            ) : null}

            {/* When SSO is also shown, local sign-in is the secondary action:
                a gold (--accent) outline. When local is the ONLY method it
                becomes the solid-green primary CTA. */}
            <button
              type="submit"
              disabled={isSubmitting}
              className={ssoEnabled
                ? "inline-flex items-center justify-center gap-2 rounded-md border-2 border-accent bg-transparent px-4 py-2 text-sm font-semibold text-accent-readable transition-colors hover:bg-accent-warm hover:text-accent-warm-foreground disabled:opacity-60"
                : "inline-flex items-center justify-center gap-2 rounded-md bg-primary px-4 py-2.5 text-sm font-semibold text-primary-foreground shadow-sm transition-colors hover:bg-primary/90 disabled:opacity-60"}
            >
              {isSubmitting ? <Loader2 className="h-4 w-4 animate-spin" /> : null}
              {t('auth.login.submit')}
            </button>
          </form>
        ) : null}
      </div>
    </main>
  );
}

export default Login;
