/// <reference types="vite/client" />

interface ImportMetaEnv {
  readonly VITE_API_BASE_URL?: string;
  // Base URL for QR deep links (round 17.0) — falls back to
  // window.location.origin when unset; see lib/plot-qr.ts's
  // getPublicAppBaseUrl(). Never a hardcoded production domain.
  readonly VITE_PUBLIC_APP_URL?: string;
  readonly VITE_DEFAULT_LANGUAGE?: string;
  readonly VITE_AUTH_SCOPE?: 'both' | 'internal_only' | 'external_only';
  readonly VITE_APP_NAME?: string;
  readonly VITE_AZURE_AD_TENANT_ID?: string;
  readonly VITE_AZURE_AD_CLIENT_ID?: string;
  readonly VITE_AZURE_AD_REDIRECT_URI?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
