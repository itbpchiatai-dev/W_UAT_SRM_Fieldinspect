import axios, { type AxiosError, type AxiosRequestConfig } from 'axios';
import { clearAccessToken, getAccessToken, setAccessToken } from '../lib/auth-token';

export const apiClient = axios.create({
  baseURL: import.meta.env.VITE_API_BASE_URL || '',
  timeout: 15_000,
  // Send the refresh-token cookie on cross-origin calls in dev.
  withCredentials: true,
});

apiClient.interceptors.request.use((config) => {
  const token = getAccessToken();
  if (token) config.headers.set('Authorization', `Bearer ${token}`);
  return config;
});

// TEMPORARY diagnostic instrumentation (round "Debug Plots Load 1") — logs
// method/url/status/duration for every apiClient call to the console so a
// slow/failing request can be pinpointed from a live repro without needing
// browser devtools access. Dev-only; safe to delete once the plots-load
// investigation is closed out.
if (import.meta.env.DEV) {
  type TimedConfig = AxiosRequestConfig & { __requestStart?: number };
  apiClient.interceptors.request.use((config) => {
    (config as TimedConfig).__requestStart = performance.now();
    return config;
  });
  apiClient.interceptors.response.use(
    (response) => {
      const start = (response.config as TimedConfig).__requestStart;
      const ms = start ? Math.round(performance.now() - start) : '?';
      console.debug(`[api-timing] ${response.config.method?.toUpperCase()} ${response.config.url} -> ${response.status} (${ms}ms)`);
      return response;
    },
    (error: AxiosError) => {
      const cfg = error.config as TimedConfig | undefined;
      const start = cfg?.__requestStart;
      const ms = start ? Math.round(performance.now() - start) : '?';
      console.debug(
        `[api-timing] ${cfg?.method?.toUpperCase()} ${cfg?.url} -> ${error.response?.status ?? 'NO RESPONSE'} (${ms}ms)`,
      );
      return Promise.reject(error);
    },
  );
}

// Silent-refresh response interceptor.
// On a single 401, POST /api/v1/auth/refresh (httpOnly refresh-cookie
// auth) and replay the original request once. The `_retry` flag on the
// original config prevents an infinite refresh→401→refresh loop.
interface RetryableConfig extends AxiosRequestConfig {
  _retry?: boolean;
}

const REFRESH_URL = '/api/v1/auth/refresh';

apiClient.interceptors.response.use(
  (response) => response,
  async (error: AxiosError) => {
    const original = error.config as RetryableConfig | undefined;
    const status = error.response?.status;
    // Only handle 401 once, and don't recurse if the failing request WAS
    // the refresh call itself.
    if (
      status !== 401 ||
      !original ||
      original._retry ||
      original.url?.includes(REFRESH_URL)
    ) {
      return Promise.reject(error);
    }
    original._retry = true;

    // TEMPORARY diagnostic timing (round "Debug Plots Load 1") — the refresh
    // POST below bypasses apiClient (bare `axios` instance), so the
    // interceptor-based instrumentation above never sees it; time it
    // explicitly here instead.
    const refreshT0 = import.meta.env.DEV ? performance.now() : 0;
    try {
      const refreshRes = await axios.post(
        REFRESH_URL,
        {},
        {
          baseURL: apiClient.defaults.baseURL,
          withCredentials: true,
          timeout: 10_000,
        },
      );
      if (import.meta.env.DEV) {
        console.debug(`[api-timing] POST ${REFRESH_URL} (retry-on-401) -> ${refreshRes.status} (${Math.round(performance.now() - refreshT0)}ms)`);
      }
      const newToken: string | undefined =
        refreshRes.data?.accessToken ?? refreshRes.data?.access_token;
      if (newToken) {
        setAccessToken(newToken);
        // Replay with fresh Authorization header.
        return apiClient.request(original);
      }
      // Refresh succeeded HTTP-wise but no token came back — bail.
      clearAccessToken();
      return Promise.reject(error);
    } catch (refreshErr) {
      if (import.meta.env.DEV) {
        const status = axios.isAxiosError(refreshErr) ? refreshErr.response?.status : undefined;
        console.debug(`[api-timing] POST ${REFRESH_URL} (retry-on-401) -> FAILED status=${status ?? 'NO RESPONSE'} (${Math.round(performance.now() - refreshT0)}ms)`);
      }
      clearAccessToken();
      return Promise.reject(error);
    }
  },
);
