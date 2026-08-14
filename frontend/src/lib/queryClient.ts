/**
 * Single shared QueryClient instance.
 *
 * Lives in its own module (not inline in main.tsx) so non-React code —
 * notably the auth store's login/logout actions — can import it and clear
 * the cache on a session change. Without that, React Query would hand the
 * next user the previous user's cached menus / list data until a hard
 * refresh.
 */
import { QueryClient } from '@tanstack/react-query';

export const queryClient = new QueryClient({
  defaultOptions: { queries: { staleTime: 60_000, retry: 1 } },
});
