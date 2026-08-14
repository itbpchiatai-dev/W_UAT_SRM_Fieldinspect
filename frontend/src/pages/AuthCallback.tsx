/**
 * AuthCallback — receives the OAuth response and finishes sign-in.
 *
 * Mounted at /auth/callback (matches VITE_AZURE_AD_REDIRECT_URI). Azure
 * redirects here with ?code=...&state=...; we POST both to the backend
 * /sso/callback which validates the state against an httponly cookie
 * set during /sso/redirect (CSRF guard).
 */
import { useEffect, useState } from 'react';
import { useNavigate, useSearchParams } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { Loader2 } from 'lucide-react';
import { ssoCallback } from '../api/auth';
import { useAuth } from '../hooks/useAuth';
import { setAccessToken } from '../lib/auth-token';

export function AuthCallback() {
  const { t } = useTranslation();
  const [params] = useSearchParams();
  const navigate = useNavigate();
  const { refresh } = useAuth();
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const code = params.get('code');
    const state = params.get('state');
    const oauthError = params.get('error');

    // Azure returned an OAuth error (user cancelled, app config bad, …).
    if (oauthError) {
      setError(oauthError);
      return;
    }
    if (!code || !state) {
      setError('sso_missing_params');
      return;
    }
    let cancelled = false;
    (async () => {
      try {
        const tokens = await ssoCallback(code, state);
        if (cancelled) return;
        // Mirror the password-login finisher in stores/auth.ts: stash
        // the access token then re-hydrate the user via /me.
        setAccessToken(tokens.accessToken);
        await refresh();
        if (!cancelled) navigate('/', { replace: true });
      } catch {
        if (!cancelled) setError('sso_failed');
      }
    })();
    return () => { cancelled = true; };
    // params is stable per mount; we intentionally only run once.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  if (error) {
    return (
      <main className="flex min-h-screen items-center justify-center px-4">
        <div className="w-full max-w-md rounded-lg border border-border bg-card p-6 shadow-sm text-center">
          <h1 className="text-xl font-semibold text-destructive">
            {t('auth.login.invalidCredentials')}
          </h1>
          <p className="mt-3 text-xs text-muted-foreground">{error}</p>
          <button
            type="button"
            onClick={() => navigate('/login', { replace: true })}
            className="mt-4 inline-flex items-center rounded-md bg-primary px-4 py-2 text-sm font-medium text-primary-foreground hover:bg-primary/90"
          >
            {t('auth.login.title')}
          </button>
        </div>
      </main>
    );
  }

  return (
    <main className="flex min-h-screen items-center justify-center">
      <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
    </main>
  );
}

export default AuthCallback;
