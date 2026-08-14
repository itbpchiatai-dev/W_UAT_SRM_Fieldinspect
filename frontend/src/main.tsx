import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import { BrowserRouter } from 'react-router-dom';
import { QueryClientProvider } from '@tanstack/react-query';
import App from './App';
import { queryClient } from './lib/queryClient';
import './i18n';
// Self-hosted brand fonts (bundled by Vite — no runtime CDN, works offline,
// satisfies the nginx CSP font-src 'self'). Prompt = display/headings (CT
// brand); IBM Plex Sans Thai = body/UI. Each weight CSS carries the Thai +
// Latin subsets via unicode-range, so Thai renders identically on every OS.
import '@fontsource/prompt/500.css';
import '@fontsource/prompt/600.css';
import '@fontsource/prompt/700.css';
import '@fontsource/ibm-plex-sans-thai/400.css';
import '@fontsource/ibm-plex-sans-thai/500.css';
import '@fontsource/ibm-plex-sans-thai/600.css';
import '@fontsource/ibm-plex-sans-thai/700.css';
import './index.css';
import { retireServiceWorker } from './lib/serviceWorkerRetirement';

// Round 8-4H.1 — round 8-4H's offline app-shell Service Worker is retired
// (vite.config.ts no longer builds one at all). A device that already
// installed the old one keeps running it until explicitly unregistered, so
// this best-effort cleanup runs on every load. Never awaited — a failure
// here (or the Service Worker API being entirely absent) must never delay
// or block app startup. See lib/serviceWorkerRetirement.ts for the full
// safety contract (origin-scoped unregister, evidence-matched cache
// cleanup only).
void retireServiceWorker();

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <BrowserRouter><App /></BrowserRouter>
    </QueryClientProvider>
  </StrictMode>,
);
