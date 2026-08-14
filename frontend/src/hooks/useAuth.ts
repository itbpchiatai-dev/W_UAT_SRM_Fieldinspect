/**
 * useAuth — primary auth hook for UI components.
 *
 * Returns a stable slice of the Zustand store. Re-renders only when
 * one of the selected fields actually changes (Zustand's default
 * shallow comparison handles primitives + the Set reference).
 */
import { useAuthStore } from '../stores/auth';

export function useAuth() {
  const user = useAuthStore((s) => s.user);
  const isAuthenticated = useAuthStore((s) => s.isAuthenticated);
  const isLoading = useAuthStore((s) => s.isLoading);
  const login = useAuthStore((s) => s.login);
  const logout = useAuthStore((s) => s.logout);
  const hydrate = useAuthStore((s) => s.hydrateFromServer);

  return { user, isAuthenticated, isLoading, login, logout, refresh: hydrate };
}
