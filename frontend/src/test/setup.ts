import { afterEach } from 'vitest';
import { cleanup } from '@testing-library/react';
// jsdom does not implement IndexedDB — round 8-4B's offline draft queue
// (lib/offline-inspection-store.ts) needs a real (if in-memory) IndexedDB to
// test against; this polyfills the global indexedDB/IDBKeyRange/etc. for
// every test file. Dev-only dependency, never shipped to the browser build.
import 'fake-indexeddb/auto';

if (typeof window !== 'undefined' && !window.matchMedia) {
  window.matchMedia = ((query: string) => ({
    matches: false,
    media: query,
    onchange: null,
    addEventListener: () => {},
    removeEventListener: () => {},
    addListener: () => {},
    removeListener: () => {},
    dispatchEvent: () => false,
  })) as unknown as typeof window.matchMedia;
}

// jsdom doesn't implement blob object URLs — round 14's PhotoSlotPicker
// uses these for local photo previews.
if (typeof URL !== 'undefined' && !URL.createObjectURL) {
  URL.createObjectURL = (() => 'blob:mock-url') as typeof URL.createObjectURL;
  URL.revokeObjectURL = (() => {}) as typeof URL.revokeObjectURL;
}

afterEach(() => cleanup());
