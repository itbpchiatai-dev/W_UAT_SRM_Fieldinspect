/**
 * useNetworkStatus — the browser's own online/offline signal (round 8-4B),
 * for the "ออนไลน์/ออฟไลน์" indicator and the offline-submit branch in
 * PublicInspect.
 *
 * This is navigator.onLine plus the window online/offline events — a
 * best-effort, browser-reported signal, NOT proof the API is actually
 * reachable (a captive portal or a flaky connection can report "online"
 * while every request still fails). PublicInspect treats a network error
 * with no HTTP response as an offline-like failure regardless of what this
 * hook currently reports — see its submit handler.
 */
import { useEffect, useState } from 'react';

function readNavigatorOnline(): boolean {
  return typeof navigator === 'undefined' ? true : navigator.onLine;
}

export function useNetworkStatus(): boolean {
  const [online, setOnline] = useState(readNavigatorOnline);

  useEffect(() => {
    function handleOnline() { setOnline(true); }
    function handleOffline() { setOnline(false); }
    window.addEventListener('online', handleOnline);
    window.addEventListener('offline', handleOffline);
    return () => {
      window.removeEventListener('online', handleOnline);
      window.removeEventListener('offline', handleOffline);
    };
  }, []);

  return online;
}
