/**
 * PlotQrScan — camera QR scanner for plot lookup (Step 12.6).
 * Decodes a QR code (expected to encode the plot_code) and reports the raw
 * text back to the caller; matching against the plot list is the caller's job.
 */
import { useEffect, useRef, useState } from 'react';
import { Html5Qrcode } from 'html5-qrcode';
import { X, Loader2 } from 'lucide-react';

interface Props {
  onResult: (code: string) => void;
  onClose: () => void;
}

const ELEMENT_ID = 'plot-qr-scan-region';

export function PlotQrScan({ onResult, onClose }: Props) {
  const [error, setError] = useState('');
  const [starting, setStarting] = useState(true);
  const onResultRef = useRef(onResult);
  onResultRef.current = onResult;

  useEffect(() => {
    const scanner = new Html5Qrcode(ELEMENT_ID);
    let stopped = false;

    scanner
      .start(
        { facingMode: 'environment' },
        { fps: 10, qrbox: 220 },
        (decodedText) => {
          if (stopped) return;
          stopped = true;
          scanner.stop().catch(() => {});
          onResultRef.current(decodedText);
        },
        () => {
          // per-frame decode miss — expected while aiming the camera, ignore
        },
      )
      .then(() => setStarting(false))
      .catch(() => {
        setError('ไม่สามารถเปิดกล้องได้ กรุณาอนุญาตการใช้กล้อง');
        setStarting(false);
      });

    return () => {
      stopped = true;
      scanner.stop().catch(() => {});
    };
  }, []);

  return (
    <div className="fixed inset-0 z-[60] flex items-center justify-center bg-black/70" onClick={onClose}>
      <div
        className="relative w-full max-w-sm rounded-xl bg-white p-4 shadow-xl"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="mb-3 flex items-center justify-between">
          <span className="text-sm font-semibold text-gray-900">สแกน QR รหัสแปลง</span>
          <button onClick={onClose} className="rounded p-1 text-gray-400 hover:bg-gray-100">
            <X className="h-4 w-4" />
          </button>
        </div>
        {starting && (
          <div className="flex items-center justify-center gap-2 py-6 text-sm text-gray-500">
            <Loader2 className="h-4 w-4 animate-spin" /> กำลังเปิดกล้อง...
          </div>
        )}
        {error && <p className="py-4 text-center text-sm text-red-600">{error}</p>}
        <div id={ELEMENT_ID} className="overflow-hidden rounded-lg" />
      </div>
    </div>
  );
}
