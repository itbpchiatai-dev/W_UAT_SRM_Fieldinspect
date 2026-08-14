/**
 * SettingsIndex — landing page for /settings.
 *
 * Phase C: each card is a real <Link> to its admin page; the cards are
 * gated by useHasPermission so the user only sees what they're allowed
 * to navigate to. Cards are still rendered (not hidden) for the routes
 * the user can reach — the underlying RequirePermission inside each
 * route is the actual enforcement boundary; this is purely UX hygiene.
 */
import { Activity, Database, KeyRound, ListChecks, Menu as MenuIcon, ScrollText, ShieldCheck, Terminal, Users } from 'lucide-react';
import type { LucideIcon } from 'lucide-react';
import { Link } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { useHasPermission } from '../hooks/useHasPermission';

interface SettingsCard {
  to: string;
  perm: string;
  titleKey: string;
  descriptionKey: string;
  Icon: LucideIcon;
}

const CARDS: SettingsCard[] = [
  { to: '/settings/users', perm: 'users.read', titleKey: 'settings.users.title', descriptionKey: 'settings.users.description', Icon: Users },
  { to: '/settings/roles', perm: 'roles.read', titleKey: 'settings.roles.title', descriptionKey: 'settings.roles.description', Icon: ShieldCheck },
  { to: '/settings/permissions', perm: 'roles.read', titleKey: 'settings.permissions.title', descriptionKey: 'settings.permissions.description', Icon: ListChecks },
  { to: '/settings/menus', perm: 'menus.read', titleKey: 'settings.menus.title', descriptionKey: 'settings.menus.description', Icon: MenuIcon },
  { to: '/settings/auth', perm: 'admin_settings.read', titleKey: 'settings.auth.title', descriptionKey: 'settings.auth.description', Icon: KeyRound },
  { to: '/settings/system-logs', perm: 'system_logs.read', titleKey: 'settings.systemLogs.title', descriptionKey: 'settings.systemLogs.description', Icon: ScrollText },
  { to: '/settings/activity-logs', perm: 'activity_logs.read', titleKey: 'settings.activityLogs.title', descriptionKey: 'settings.activityLogs.description', Icon: Activity },
  { to: '/settings/db-connections', perm: 'db_connections.read', titleKey: 'settings.dbConnections.title', descriptionKey: 'settings.dbConnections.description', Icon: Database },
  { to: '/settings/query-sandbox', perm: 'db_connections.query', titleKey: 'settings.querySandbox.title', descriptionKey: 'settings.querySandbox.description', Icon: Terminal },
];

function SettingsCardLink({ card }: { card: SettingsCard }) {
  const { t } = useTranslation();
  const allowed = useHasPermission(card.perm);
  if (!allowed) return null;
  const { to, titleKey, descriptionKey, Icon } = card;
  return (
    <Link
      to={to}
      className="block rounded-lg border border-border bg-card p-4 text-card-foreground shadow-sm transition-colors hover:border-primary hover:bg-secondary"
    >
      <div className="flex items-start gap-3">
        <span className="flex h-10 w-10 items-center justify-center rounded-md bg-primary/10 text-primary">
          <Icon className="h-5 w-5" />
        </span>
        <div className="flex-1">
          <h2 className="text-base font-semibold">{t(titleKey)}</h2>
          <p className="mt-1 text-sm text-muted-foreground">{t(descriptionKey)}</p>
        </div>
      </div>
    </Link>
  );
}

export function SettingsIndex() {
  const { t } = useTranslation();
  return (
    <div className="container mx-auto px-4 py-8 sm:px-6 lg:px-8">
      <header>
        <h1 className="text-xl font-bold">{t('settings.title')}</h1>
      </header>

      <section className="mt-8 grid grid-cols-1 gap-4 sm:grid-cols-2">
        {CARDS.map((card) => (
          <SettingsCardLink key={card.to} card={card} />
        ))}
      </section>
    </div>
  );
}

export default SettingsIndex;
