/**
 * UserMenu — avatar + dropdown (profile placeholder, logout).
 *
 * Logout: calls store.logout() (which POSTs /auth/logout + clears the
 * client-side state) then navigates to /login. The server is expected
 * to clear the httpOnly refresh cookie as part of the logout response.
 */
import { useEffect, useRef, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { ChevronDown, LogOut, UserCircle } from 'lucide-react';
import { useAuth } from '../../hooks/useAuth';

function initials(name: string | undefined | null): string {
  if (!name) return '?';
  return name
    .split(/\s+/)
    .map((p) => p[0])
    .filter(Boolean)
    .slice(0, 2)
    .join('')
    .toUpperCase();
}

export function UserMenu() {
  const { user, logout } = useAuth();
  const { t } = useTranslation();
  const navigate = useNavigate();
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement | null>(null);

  // Close on outside click — small UX nicety; avoids needing a portal/modal.
  useEffect(() => {
    function onClick(e: MouseEvent) {
      if (!ref.current?.contains(e.target as Node)) setOpen(false);
    }
    document.addEventListener('mousedown', onClick);
    return () => document.removeEventListener('mousedown', onClick);
  }, []);

  async function handleLogout() {
    await logout();
    navigate('/login', { replace: true });
  }

  return (
    <div ref={ref} className="relative">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="flex items-center gap-2 rounded-md px-2 py-1.5 text-sm hover:bg-secondary"
      >
        <span className="flex h-8 w-8 items-center justify-center rounded-full bg-primary text-primary-foreground">
          <span className="text-xs font-semibold">{initials(user?.fullName)}</span>
        </span>
        <span className="hidden text-sm font-medium sm:inline">
          {user?.fullName ?? user?.email ?? ''}
        </span>
        <ChevronDown className="h-4 w-4 text-muted-foreground" />
      </button>

      {open ? (
        <div className="absolute right-0 mt-2 w-48 rounded-md border border-border bg-popover text-popover-foreground shadow-lg">
          <button
            type="button"
            disabled
            className="flex w-full items-center gap-2 px-3 py-2 text-left text-sm text-muted-foreground"
          >
            <UserCircle className="h-4 w-4" />
            {t('layout.profile')}
          </button>
          <button
            type="button"
            onClick={handleLogout}
            className="flex w-full items-center gap-2 border-t border-border px-3 py-2 text-left text-sm text-foreground hover:bg-secondary"
          >
            <LogOut className="h-4 w-4" />
            {t('auth.logout')}
          </button>
        </div>
      ) : null}
    </div>
  );
}

export default UserMenu;
