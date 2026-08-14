/**
 * Approval page — public token-gated approve/reject UI.
 *
 * Flow:
 *   loading → ready → submitting → done   (success path)
 *           → gone                        (expired or another admin acted)
 *
 * Mounted at /approve/:token; reached from the admin notification email.
 * No login required — the token IS the capability. One click on the
 * Approve / Reject button submits the decision with the default email
 * template (defined in app/services/notifications/templates.py); we do
 * NOT collect a per-instance custom message here.
 *
 * The page can be pre-armed via ?action=approve / ?action=reject in the
 * URL (the email buttons set this). When pre-armed we still show the
 * confirmation card with both buttons so admin can re-pick if they
 * clicked the wrong one in the email.
 */
import { useEffect, useState } from 'react';
import { Link, useParams, useSearchParams } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { CheckCircle2, Languages, Loader2, XCircle } from 'lucide-react';
import {
  resolveToken, approveViaToken, rejectViaToken,
  type PendingUserPublic,
} from '../api/approval';
import { useUI } from '../stores/ui';

type Phase = 'loading' | 'ready' | 'submitting' | 'done' | 'gone';

export function Approval() {
  const { t } = useTranslation();
  const { locale, setLocale } = useUI();
  const { token = '' } = useParams<{ token: string }>();
  const [params] = useSearchParams();
  const initialAction = (params.get('action') ?? '') as 'approve' | 'reject' | '';

  const [phase, setPhase] = useState<Phase>('loading');
  const [pending, setPending] = useState<PendingUserPublic | null>(null);
  const [goneReason, setGoneReason] = useState<string>('');
  const [error, setError] = useState<string | null>(null);
  const [finalState, setFinalState] = useState<'approved' | 'rejected' | null>(null);
  // Tracks which button is currently submitting so we can show a spinner
  // on just that one (and disable both during submission).
  const [busyAction, setBusyAction] = useState<'approve' | 'reject' | null>(null);

  // Initial fetch — resolve token → user info or gone state.
  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const r = await resolveToken(token);
        if (cancelled) return;
        if (r.status === 'valid' && r.pending) {
          setPending(r.pending);
          setPhase('ready');
        } else {
          setGoneReason(r.status);
          setPhase('gone');
        }
      } catch {
        if (!cancelled) {
          setGoneReason('error');
          setPhase('gone');
        }
      }
    })();
    return () => { cancelled = true; };
  }, [token]);

  // Auto-fire when the email button pre-armed an action — saves a click.
  // Only runs once per token resolve; after that, admin uses the in-page
  // buttons. Gate on phase === 'ready' so we don\'t race the initial fetch.
  useEffect(() => {
    if (phase !== 'ready') return;
    if (initialAction === 'approve') void submit('approve');
    if (initialAction === 'reject') void submit('reject');
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [phase]);

  function flipLocale() {
    setLocale(locale === 'th' ? 'en' : 'th');
  }

  async function submit(action: 'approve' | 'reject') {
    setError(null);
    setBusyAction(action);
    setPhase('submitting');
    try {
      // Empty strings → backend falls back to the default template body.
      const r = action === 'approve'
        ? await approveViaToken(token, '')
        : await rejectViaToken(token, '', '');
      if (r.status === 'approved') {
        setFinalState('approved');
        setPhase('done');
      } else if (r.status === 'rejected') {
        setFinalState('rejected');
        setPhase('done');
      } else {
        setGoneReason(r.status);
        setPhase('gone');
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : 'error');
      setPhase('ready');
      setBusyAction(null);
    }
  }

  if (phase === 'loading') {
    return (
      <Shell onLocale={flipLocale} locale={locale}>
        <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
      </Shell>
    );
  }

  if (phase === 'gone') {
    const copy = goneReason === 'expired'
      ? t('auth.approval.linkExpired')
      : t('auth.approval.linkConsumed');
    return (
      <Shell onLocale={flipLocale} locale={locale}>
        <Card>
          <h1 className="text-xl font-semibold text-foreground">{t('auth.approval.linkUnavailable')}</h1>
          <p className="mt-3 text-sm text-muted-foreground">{copy}</p>
        </Card>
      </Shell>
    );
  }

  if (phase === 'done' && finalState) {
    return (
      <Shell onLocale={flipLocale} locale={locale}>
        <DoneCard kind={finalState} pending={pending} />
      </Shell>
    );
  }

  // phase === 'ready' | 'submitting'
  const submitting = phase === 'submitting';
  const dateLocale = locale === 'th' ? 'th-TH' : 'en-US';
  return (
    <Shell onLocale={flipLocale} locale={locale}>
      <Card>
        <h1 className="text-2xl font-bold text-foreground">{t('auth.approval.title')}</h1>
        {pending ? (
          <div className="mt-4 rounded-md bg-secondary p-4">
            <p className="text-sm">
              <span className="font-semibold">{pending.fullName || `(${t('auth.approval.anonymousName')})`}</span>
              <span className="ml-1 text-muted-foreground">({pending.email})</span>
            </p>
            <p className="mt-1 text-xs text-muted-foreground">
              {t('auth.approval.requestedAt')} {new Date(pending.requestedAt).toLocaleString(dateLocale)}
              {' · '}{t('auth.approval.expiresAt')} {new Date(pending.expiresAt).toLocaleDateString(dateLocale)}
            </p>
          </div>
        ) : null}

        <div className="mt-6 flex gap-3">
          <button
            type="button"
            onClick={() => submit('approve')}
            disabled={submitting}
            className="flex-1 inline-flex items-center justify-center gap-2 rounded-md bg-primary px-4 py-3 text-sm font-semibold text-primary-foreground shadow-sm hover:bg-primary/90 disabled:opacity-60"
          >
            {busyAction === 'approve' ? <Loader2 className="h-4 w-4 animate-spin" /> : <CheckCircle2 className="h-4 w-4" />}
            {t('auth.approval.approve')}
          </button>
          <button
            type="button"
            onClick={() => submit('reject')}
            disabled={submitting}
            className="flex-1 inline-flex items-center justify-center gap-2 rounded-md bg-destructive px-4 py-3 text-sm font-semibold text-destructive-foreground shadow-sm hover:bg-destructive/90 disabled:opacity-60"
          >
            {busyAction === 'reject' ? <Loader2 className="h-4 w-4 animate-spin" /> : <XCircle className="h-4 w-4" />}
            {t('auth.approval.reject')}
          </button>
        </div>

        {error ? (
          <p className="mt-4 rounded-md border border-destructive/40 bg-destructive/10 px-3 py-2 text-xs text-destructive">
            {t('auth.approval.submitError')} ({error})
          </p>
        ) : null}
      </Card>
      <p className="mt-3 text-center text-xs text-muted-foreground">
        {t('auth.approval.linkSingleUse')}
      </p>
    </Shell>
  );
}

function DoneCard({
  kind, pending,
}: { kind: 'approved' | 'rejected'; pending: PendingUserPublic | null }) {
  const { t } = useTranslation();
  const approved = kind === 'approved';
  const titleKey = approved ? 'auth.approval.doneApproved' : 'auth.approval.doneRejected';
  const hintKey = approved ? 'auth.approval.doneApprovedHint' : 'auth.approval.doneRejectedHint';
  const Icon = approved ? CheckCircle2 : XCircle;
  // Brand green for approve, destructive red for reject. Both screens
  // share the same layout: centered icon + heading + identity line + hint.
  const accent = approved ? '#114B33' : '#dc2626';
  return (
    <Card>
      <div className="flex flex-col items-center text-center py-2">
        <h1 className="flex items-center gap-2 text-xl font-bold" style={{color: accent}}>
          {t(titleKey)}
          <Icon className="h-6 w-6" />
        </h1>
        {pending ? (
          <p className="mt-4 text-sm font-semibold">
            {pending.fullName || ''}
            <span className="ml-1 font-normal text-muted-foreground">({pending.email})</span>
          </p>
        ) : null}
        <p className="mt-1 text-sm text-muted-foreground">{t(hintKey)}</p>
        <Link
          to="/"
          className="mt-5 text-sm text-muted-foreground underline-offset-4 hover:text-foreground hover:underline"
        >
          {t('auth.approval.backHome')} →
        </Link>
      </div>
    </Card>
  );
}

function Shell({
  children, onLocale, locale,
}: { children: React.ReactNode; onLocale: () => void; locale: string }) {
  return (
    <main className="relative flex min-h-screen items-center justify-center bg-background px-4 py-12">
      <div className="absolute left-0 right-0 top-0 h-1 bg-primary" />
      <div className="absolute right-4 top-4">
        <button
          type="button"
          onClick={onLocale}
          className="inline-flex items-center gap-1 rounded-md bg-card/80 px-2 py-1.5 text-xs font-medium text-foreground shadow-sm hover:bg-secondary"
        >
          <Languages className="h-4 w-4" />
          <span className="uppercase">{locale}</span>
        </button>
      </div>
      <div className="w-full max-w-xl">
        {children}
      </div>
    </main>
  );
}

function Card({ children }: { children: React.ReactNode }) {
  return (
    <div className="rounded-lg border border-border bg-card p-6 shadow-sm">
      {children}
    </div>
  );
}

export default Approval;
