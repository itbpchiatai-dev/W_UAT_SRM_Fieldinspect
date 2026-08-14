/**
 * UI preferences store — sidebar collapsed state, color theme, locale.
 *
 * Persisted to localStorage so the user's last choice survives reloads.
 * Initial read is synchronous (top-of-module) so the first paint already
 * has the right theme/locale — avoids the dark-mode flash on refresh.
 */
import { create } from 'zustand';
import i18n from '../i18n';

const STORAGE_KEY = 'ui.prefs.v1';

export type Theme = 'light' | 'dark';
export type Locale = 'th' | 'en';

interface UIState {
  sidebarCollapsed: boolean;
  /** Off-canvas nav drawer on small screens. Not persisted — always
   *  starts closed on load so a reload never traps the user behind it. */
  mobileNavOpen: boolean;
  theme: Theme;
  locale: Locale;
  toggleSidebar: () => void;
  openMobileNav: () => void;
  closeMobileNav: () => void;
  toggleMobileNav: () => void;
  setTheme: (t: Theme) => void;
  toggleTheme: () => void;
  setLocale: (l: Locale) => void;
}

interface PersistedPrefs {
  sidebarCollapsed?: boolean;
  theme?: Theme;
  locale?: Locale;
}

function readInitial(): PersistedPrefs {
  if (typeof window === 'undefined') return {};
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY);
    return raw ? (JSON.parse(raw) as PersistedPrefs) : {};
  } catch {
    return {};
  }
}

function persist(state: Pick<UIState, 'sidebarCollapsed' | 'theme' | 'locale'>) {
  try {
    window.localStorage.setItem(
      STORAGE_KEY,
      JSON.stringify({
        sidebarCollapsed: state.sidebarCollapsed,
        theme: state.theme,
        locale: state.locale,
      }),
    );
  } catch {
    /* localStorage may be unavailable (private mode) — drop silently */
  }
}

function applyTheme(theme: Theme) {
  if (typeof document === 'undefined') return;
  const root = document.documentElement;
  if (theme === 'dark') root.classList.add('dark');
  else root.classList.remove('dark');
}

function applyLocale(locale: Locale) {
  if (i18n.language !== locale) {
    void i18n.changeLanguage(locale);
  }
}

const initial = readInitial();
const initialTheme: Theme = initial.theme ?? 'light';
const initialLocale: Locale = initial.locale ?? 'th';

// Apply at module load so the FIRST paint is right (no flash).
applyTheme(initialTheme);
applyLocale(initialLocale);

export const useUI = create<UIState>((set, get) => ({
  sidebarCollapsed: initial.sidebarCollapsed ?? false,
  mobileNavOpen: false,
  theme: initialTheme,
  locale: initialLocale,

  toggleSidebar: () => {
    set({ sidebarCollapsed: !get().sidebarCollapsed });
    persist(get());
  },

  // Drawer state is ephemeral (not persisted) — see UIState comment.
  openMobileNav: () => set({ mobileNavOpen: true }),
  closeMobileNav: () => set({ mobileNavOpen: false }),
  toggleMobileNav: () => set({ mobileNavOpen: !get().mobileNavOpen }),

  setTheme: (t) => {
    set({ theme: t });
    applyTheme(t);
    persist(get());
  },

  toggleTheme: () => {
    const next: Theme = get().theme === 'dark' ? 'light' : 'dark';
    set({ theme: next });
    applyTheme(next);
    persist(get());
  },

  setLocale: (l) => {
    set({ locale: l });
    applyLocale(l);
    persist(get());
  },
}));
