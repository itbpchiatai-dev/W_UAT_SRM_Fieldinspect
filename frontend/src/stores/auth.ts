/**
 * Zustand auth store — user profile + permission keys + menu tree.
 *
 * Storage policy:
 *   - Access token: lib/auth-token.ts (sessionStorage mirror).
 *   - user/permissions/menus: in-memory ONLY (no localStorage). Privacy
 *     hygiene — a closed tab forgets who you were, refresh re-hydrates
 *     via the httpOnly refresh cookie.
 *
 * Actions:
 *   hydrateFromServer() — fetch /me + /me/permissions + /me/menus
 *                          (assumes accessToken is already set).
 *   login(email,pw)     — POST /auth/login, store token, hydrate.
 *   logout()            — POST /auth/logout, clear token + state.
 *   setLoading(b)       — used by <AuthBootstrap/> to gate the initial
 *                          refresh round-trip so <RequireAuth/> doesn't
 *                          flash a redirect.
 */
import { create } from 'zustand';
import { setAccessToken, clearAccessToken } from '../lib/auth-token';
import { queryClient } from '../lib/queryClient';
import * as authApi from '../api/auth';
import * as meApi from '../api/me';
import type { MenuItem, User } from '../types/auth';

export interface AuthState {
  user: User | null;
  permissionKeys: Set<string>;
  menus: MenuItem[];
  isLoading: boolean;
  isAuthenticated: boolean;

  setLoading: (v: boolean) => void;
  hydrateFromServer: () => Promise<void>;
  refreshPermissions: () => Promise<void>;
  login: (email: string, password: string) => Promise<void>;
  logout: () => Promise<void>;
  clear: () => void;
}

export const useAuthStore = create<AuthState>((set) => ({
  user: null,
  permissionKeys: new Set<string>(),
  menus: [],
  isLoading: true,        // true until AuthBootstrap finishes its first round
  isAuthenticated: false,

  setLoading: (v) => set({ isLoading: v }),

  hydrateFromServer: async () => {
    // Pull profile, permissions, and menu tree in parallel — they are
    // independent reads. If ANY fail, treat the session as anonymous so
    // we never render a broken half-state.
    try {
      const [user, permissionKeys, menus] = await Promise.all([
        meApi.getMe(),
        meApi.getMyPermissions(),
        meApi.getMyMenus(),
      ]);
      set({
        user,
        permissionKeys: new Set(permissionKeys),
        menus,
        isAuthenticated: true,
      });
    } catch {
      clearAccessToken();
      set({
        user: null,
        permissionKeys: new Set<string>(),
        menus: [],
        isAuthenticated: false,
      });
    }
  },

  refreshPermissions: async () => {
    // Background re-sync of perms + menus (e.g. on window focus) so a
    // per-user override granted while this session is open shows up
    // without re-login. Unlike hydrateFromServer, a failure here keeps
    // the current session — a transient network blip on tab-switch must
    // not log the user out.
    if (!useAuthStore.getState().isAuthenticated) return;
    try {
      const [permissionKeys, menus] = await Promise.all([
        meApi.getMyPermissions(),
        meApi.getMyMenus(),
      ]);
      set({ permissionKeys: new Set(permissionKeys), menus });
    } catch {
      // Keep last-known state; the next focus/refresh will retry.
    }
  },

  login: async (email, password) => {
    const res = await authApi.login(email, password);
    setAccessToken(res.accessToken);
    // New session: drop any React Query cache left by a previous user so
    // the sidebar menus / list pages can't surface the prior user's data
    // (this path also covers re-login after a silent session expiry).
    queryClient.clear();
    // Re-use the same hydration path so login + bootstrap share one
    // code path — easier to reason about race conditions.
    await useAuthStore.getState().hydrateFromServer();
  },

  logout: async () => {
    try {
      await authApi.logout();
    } catch {
      // Even if the server call fails, locally tear down the session —
      // the user clicked "ออกจากระบบ", they expect to be logged out.
    }
    clearAccessToken();
    set({
      user: null,
      permissionKeys: new Set<string>(),
      menus: [],
      isAuthenticated: false,
    });
    queryClient.clear();
  },

  clear: () => {
    clearAccessToken();
    set({
      user: null,
      permissionKeys: new Set<string>(),
      menus: [],
      isAuthenticated: false,
    });
    queryClient.clear();
  },
}));
