/**
 * TopBar — sidebar-toggle (left) + app name + language/theme controls + UserMenu (right).
 *
 * App name source of truth (in order):
 *   1. import.meta.env.VITE_APP_NAME (project-specific override)
 *   2. fallback string "Chia Tai"
 * Avoid hardcoding the literal anywhere else in the app.
 */
import { useTranslation } from 'react-i18next';
import { Moon, Sun, Languages, Menu, PanelLeftClose, PanelLeftOpen, RefreshCw } from 'lucide-react';
import { useUI } from '../../stores/ui';
import { UserMenu } from './UserMenu';

const APP_NAME = import.meta.env.VITE_APP_NAME ?? 'Chia Tai';

export function TopBar() {
  const { t } = useTranslation();
  const { theme, locale, sidebarCollapsed, toggleSidebar, toggleMobileNav, toggleTheme, setLocale } = useUI();
  const nextLocale = locale === 'th' ? 'en' : 'th';
  const isDark = theme === 'dark';
  const sidebarLabel = sidebarCollapsed
    ? t('common.expandSidebar')
    : t('common.collapseSidebar');

  return (
    <header className="sticky top-0 z-30 flex h-14 items-center justify-between border-b-2 border-accent bg-card px-4 shadow-sm sm:px-6">
      <div className="flex items-center gap-2">
        <button
          type="button"
          onClick={toggleMobileNav}
          className="inline-flex rounded-md p-2 text-foreground hover:bg-secondary sm:hidden"
          aria-label={t('common.openMenu')}
          title={t('common.openMenu')}
        >
          <Menu className="h-5 w-5" />
        </button>
        <button
          type="button"
          onClick={toggleSidebar}
          className="hidden rounded-md p-2 text-foreground hover:bg-secondary sm:inline-flex"
          aria-label={sidebarLabel}
          title={sidebarLabel}
        >
          {sidebarCollapsed
            ? <PanelLeftOpen className="h-5 w-5" />
            : <PanelLeftClose className="h-5 w-5" />}
        </button>
        <span className="text-lg font-semibold text-primary">{APP_NAME}</span>
      </div>
      <div className="flex items-center gap-1">
        <button
          type="button"
          onClick={() => window.location.reload()}
          className="inline-flex items-center rounded-md p-2 text-foreground hover:bg-secondary"
          aria-label={t('common.refresh')}
          title={t('common.refresh')}
        >
          <RefreshCw className="h-4 w-4" />
        </button>
        <button
          type="button"
          onClick={() => setLocale(nextLocale)}
          className="inline-flex items-center gap-1 rounded-md px-2 py-1.5 text-xs font-medium text-foreground hover:bg-secondary"
          aria-label={t('common.language')}
          title={t('common.language')}
        >
          <Languages className="h-4 w-4" />
          <span className="uppercase">{locale}</span>
        </button>
        <button
          type="button"
          onClick={toggleTheme}
          className="inline-flex items-center rounded-md p-2 text-foreground hover:bg-secondary"
          aria-label={isDark ? t('common.lightMode') : t('common.darkMode')}
          title={isDark ? t('common.lightMode') : t('common.darkMode')}
        >
          {isDark ? <Sun className="h-4 w-4" /> : <Moon className="h-4 w-4" />}
        </button>
        <UserMenu />
      </div>
    </header>
  );
}

export default TopBar;
