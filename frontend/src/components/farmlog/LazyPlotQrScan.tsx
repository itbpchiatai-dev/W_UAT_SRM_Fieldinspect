/**
 * LazyPlotQrScan — shared lazy-loading wrapper around PlotQrScan (round
 * 8-17B Part B). html5-qrcode is a heavy dependency that PublicInspect,
 * RecordForm, and SmartPlotPicker only ever need once the user actually
 * taps "สแกน QR" — a static import of PlotQrScan (and therefore
 * html5-qrcode) would ship it in all three initial bundles regardless of
 * whether the user ever scans. `React.lazy` defers the import() until this
 * component first renders (i.e. until the caller's qrOpen/scanOpen state
 * flips to true), and Suspense shows a loading state while the chunk
 * downloads.
 *
 * Every caller renders this exactly like the old `<PlotQrScan ... />` —
 * same two props, same conditional-render-on-open pattern.
 */
import { lazy, Suspense } from 'react';
import { Loader2 } from 'lucide-react';

const PlotQrScanLazy = lazy(() =>
  import('./PlotQrScan').then((m) => ({ default: m.PlotQrScan })),
);

interface Props {
  onResult: (code: string) => void;
  onClose: () => void;
}

export function LazyPlotQrScan({ onResult, onClose }: Props) {
  return (
    <Suspense
      fallback={
        <div className="fixed inset-0 z-[60] flex items-center justify-center bg-black/70">
          <div className="flex items-center gap-2 rounded-xl bg-white px-4 py-3 text-sm text-gray-500 shadow-xl">
            <Loader2 className="h-4 w-4 animate-spin" /> กำลังโหลดตัวสแกน QR...
          </div>
        </div>
      }
    >
      <PlotQrScanLazy onResult={onResult} onClose={onClose} />
    </Suspense>
  );
}
