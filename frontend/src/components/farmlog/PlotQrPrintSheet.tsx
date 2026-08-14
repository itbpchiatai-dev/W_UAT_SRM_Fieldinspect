/**
 * PlotQrPrintSheet — A4 print sheet, 6 QR labels/page, for plot field signs.
 * Scanning a label's QR with the "บันทึกการตรวจแปลงใหม่" scanner auto-fills
 * supplier + plot (see lib/plot-qr.ts).
 *
 * Portals to document.body so print output can be isolated from the app
 * shell (Sidebar/TopBar) via the `body.qr-print-active` rule in index.css —
 * otherwise the browser would print the whole page, shell included.
 */
import { useEffect } from 'react';
import { createPortal } from 'react-dom';
import { Printer, X } from 'lucide-react';
import { QRCodeSVG } from 'qrcode.react';
import { buildPlotQrDeepLink, getPublicAppBaseUrl } from '../../lib/plot-qr';

export interface PlotQrLabelData {
  plotId: string;
  plotCode: string;
  plotName: string;
  supplierCode: string;
  supplierName: string;
  // Opaque QR locator (round 20 QR hardening) — null only for a plot that
  // somehow predates the backfill; buildPlotQrDeepLink falls back to the
  // legacy supplierCode/plotCode shape in that case rather than printing
  // a broken link.
  qrKey: string | null;
}

interface Props {
  items: PlotQrLabelData[];
  onClose: () => void;
}

const LABELS_PER_PAGE = 6;

function chunk<T>(items: T[], size: number): T[][] {
  const pages: T[][] = [];
  for (let i = 0; i < items.length; i += size) pages.push(items.slice(i, i + size));
  return pages;
}

function QrLabel({ item, baseUrl }: { item: PlotQrLabelData; baseUrl: string }) {
  // URL deep link — scanning with a phone's camera app opens /public/inspect
  // directly, no in-app scanner needed first. Round 20 QR hardening: carries
  // the plot's opaque qrKey rather than the guessable supplierCode/plotCode
  // pair, and never an inspection code/token/secret either way.
  const deepLink = buildPlotQrDeepLink(item, baseUrl);
  return (
    <div className="flex flex-col items-center justify-center gap-1 rounded-md border border-dashed border-gray-300 p-3 text-center print:break-inside-avoid print:border-gray-400">
      <QRCodeSVG value={deepLink} size={130} level="M" />
      <p className="mt-1 text-sm font-bold text-gray-900">{item.supplierCode}</p>
      <p className="text-xs text-gray-600">{item.supplierName}</p>
      <p className="text-sm font-bold text-gray-900">{item.plotCode}</p>
      <p className="text-xs text-gray-600">{item.plotName}</p>
      <p className="break-all font-mono text-[9px] text-gray-400">{deepLink}</p>
      <p className="text-[10px] text-gray-500">Scan เพื่อบันทึกการตรวจแปลง</p>
    </div>
  );
}

export function PlotQrPrintSheet({ items, onClose }: Props) {
  useEffect(() => {
    document.body.classList.add('qr-print-active');
    return () => document.body.classList.remove('qr-print-active');
  }, []);

  const pages = chunk(items, LABELS_PER_PAGE);
  const baseUrl = getPublicAppBaseUrl();

  return createPortal(
    <div className="fixed inset-0 z-[100] overflow-y-auto bg-black/50 print:static print:inset-auto print:z-auto print:overflow-visible print:bg-white">
      <div className="sticky top-0 z-10 flex items-center justify-between border-b bg-white px-4 py-3 shadow-sm print:hidden">
        <span className="text-sm font-semibold text-gray-900">พิมพ์ QR แปลง ({items.length} รายการ)</span>
        <div className="flex items-center gap-2">
          <button
            type="button"
            onClick={() => window.print()}
            disabled={items.length === 0}
            className="inline-flex items-center gap-1.5 rounded-md bg-green-600 px-4 py-2 text-sm font-semibold text-white hover:bg-green-700 disabled:opacity-50"
          >
            <Printer className="h-4 w-4" /> พิมพ์
          </button>
          <button
            type="button"
            onClick={onClose}
            className="inline-flex items-center gap-1.5 rounded-md border border-gray-300 px-4 py-2 text-sm font-medium text-gray-700 hover:bg-gray-50"
          >
            <X className="h-4 w-4" /> ปิด
          </button>
        </div>
      </div>

      <div className="mx-auto max-w-3xl px-4 py-6 print:max-w-none print:px-0 print:py-0">
        {items.length === 0 ? (
          <p className="py-16 text-center text-sm text-gray-400">ไม่มีแปลงให้พิมพ์ QR</p>
        ) : (
          pages.map((page, pageIdx) => (
            <div
              key={pageIdx}
              className="mb-6 grid grid-cols-2 grid-rows-3 gap-4 rounded-lg border border-gray-200 bg-white p-4 shadow-sm print:mb-0 print:break-after-page print:rounded-none print:border-0 print:p-0 print:shadow-none"
            >
              {page.map(item => <QrLabel key={item.plotId} item={item} baseUrl={baseUrl} />)}
            </div>
          ))
        )}
      </div>
    </div>,
    document.body,
  );
}
