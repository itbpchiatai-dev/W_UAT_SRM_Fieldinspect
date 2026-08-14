/**
 * useHasPermission — boolean check against the user's effective perms.
 *
 * Effective = (role-granted perms) ∪ (per-user overrides). Backend
 * resolves this on every /me/permissions call; we just consult the Set
 * cached in the Zustand store.
 *
 * Wildcard "*" short-circuits every check. NOTE: the current backend
 * never emits "*" — super_admin is seeded with every catalog key
 * explicitly (see app/seed.py DEFAULT_ROLES, keys=None → all perms).
 * The check is kept as forward-compat for host apps that add a
 * wildcard later; do not rely on it being present.
 */
import { useAuthStore } from '../stores/auth';

export function useHasPermission(key: string): boolean {
  const keys = useAuthStore((s) => s.permissionKeys);
  if (keys.has('*')) return true;
  return keys.has(key);
}
