/**
 * AuthenticatedPhoto — renders one inspection-record photo (round 15;
 * click-to-view lightbox round 8-14C).
 *
 * records.photoUrls can never be used as a plain <img src> (no static
 * route serves that prefix — see api/records.ts's module docs), so this
 * fetches the photo as a Blob through the scoped download endpoint and
 * renders it via a local object URL, revoking that URL whenever the photo
 * changes or the component unmounts.
 *
 * A failed fetch (network error, 403/404, or a photoUrl that doesn't match
 * the expected filename shape) renders an inline fallback box instead of
 * throwing — one bad photo must never take down the whole page.
 *
 * Round 8-14C — once the Blob has loaded, the thumbnail becomes a clickable
 * button that opens a full-size lightbox reusing the SAME object URL: no
 * second fetch, no new object URL, no change to the scoped-download
 * security boundary. This is the one shared integration point for every
 * page that renders an inspection photo (RecordPreview, PlotDetail's
 * latest-photo and history sections, and anywhere future) — fixing/
 * extending it here applies everywhere automatically.
 *
 * Round 8-17B Part D — the Blob fetch itself is deferred until the
 * placeholder box is near the viewport (IntersectionObserver, 200px
 * rootMargin so it starts a little before the user actually scrolls it
 * into view). A page with many photos (e.g. PlotDetail's history section)
 * no longer fires every photo's authenticated download on mount. Browsers
 * without IntersectionObserver fetch immediately (`inView` starts `true`)
 * — never silently show nothing.
 */
import { useEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { ImageOff, Loader2, X, ZoomIn } from 'lucide-react';
import { extractPhotoFilename, getRecordPhotoBlob } from '../../api/records';

interface Props {
  recordId: string;
  photoUrl: string;
  alt?: string;
  className?: string;
}

const DEFAULT_ALT = 'ภาพถ่ายแปลง';
const DEFAULT_BOX_CLS = 'h-24 w-24 rounded-md object-cover shadow-sm';

export function AuthenticatedPhoto({ recordId, photoUrl, alt, className }: Props) {
  const [blobUrl, setBlobUrl] = useState<string | null>(null);
  const [failed, setFailed] = useState(false);
  const [isOpen, setIsOpen] = useState(false);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const placeholderRef = useRef<HTMLDivElement>(null);

  // Round 8-17B Part D — starts true (fetch immediately) when the browser
  // has no IntersectionObserver at all; otherwise starts false and flips
  // once the placeholder box below is observed near the viewport.
  const [inView, setInView] = useState(() => typeof IntersectionObserver === 'undefined');

  useEffect(() => {
    if (inView) return;
    const node = placeholderRef.current;
    if (!node) return;
    const observer = new IntersectionObserver(
      (entries) => {
        if (entries.some((entry) => entry.isIntersecting)) setInView(true);
      },
      { rootMargin: '200px' },
    );
    observer.observe(node);
    return () => observer.disconnect();
  }, [inView]);

  useEffect(() => {
    setBlobUrl(null);
    setFailed(false);
    if (!inView) return;

    const filename = extractPhotoFilename(photoUrl);
    if (!filename) {
      setFailed(true);
      return;
    }

    let cancelled = false;
    let objectUrl: string | null = null;

    getRecordPhotoBlob(recordId, filename)
      .then((blob) => {
        if (cancelled) return;
        objectUrl = URL.createObjectURL(blob);
        setBlobUrl(objectUrl);
      })
      .catch(() => {
        if (!cancelled) setFailed(true);
      });

    return () => {
      cancelled = true;
      // Round 8-14C — close BEFORE revoking, so the lightbox (if open) never
      // has a chance to render a URL that's about to be torn down; also
      // prevents a photo/record change from silently "carrying over" an
      // open lightbox onto the NEW photo once its own fetch resolves.
      setIsOpen(false);
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [recordId, photoUrl, inView]);

  const boxCls = className ?? DEFAULT_BOX_CLS;
  const altText = alt ?? DEFAULT_ALT;

  if (failed) {
    return (
      <div className={`flex items-center justify-center bg-gray-100 text-gray-400 ${boxCls}`}>
        <ImageOff className="h-5 w-5" />
      </div>
    );
  }

  if (!blobUrl) {
    return (
      <div ref={placeholderRef} className={`flex items-center justify-center bg-gray-50 text-gray-300 ${boxCls}`}>
        <Loader2 className="h-4 w-4 animate-spin" />
      </div>
    );
  }

  return (
    <>
      <button
        type="button"
        ref={triggerRef}
        onClick={() => setIsOpen(true)}
        aria-label="เปิดดูภาพถ่ายแปลงขนาดใหญ่"
        title="ดูภาพขนาดใหญ่"
        className={`group relative block overflow-hidden focus:outline-none focus:ring-2 focus:ring-green-500 focus:ring-offset-1 ${boxCls}`}
      >
        <img src={blobUrl} alt={altText} className="h-full w-full object-cover" />
        <span
          aria-hidden="true"
          className="pointer-events-none absolute inset-0 flex items-center justify-center bg-black/0 opacity-0 transition-opacity group-hover:bg-black/30 group-hover:opacity-100 group-focus-visible:bg-black/30 group-focus-visible:opacity-100"
        >
          <ZoomIn className="h-1/3 w-1/3 text-white drop-shadow" />
        </span>
      </button>

      {isOpen && blobUrl && (
        <PhotoLightbox blobUrl={blobUrl} alt={altText} onClose={() => setIsOpen(false)} triggerRef={triggerRef} />
      )}
    </>
  );
}

interface PhotoLightboxProps {
  blobUrl: string;
  alt: string;
  onClose: () => void;
  triggerRef: React.RefObject<HTMLButtonElement>;
}

/** Full-size photo popup — always reuses the caller's already-loaded Blob
 * URL; never fetches, never creates a second object URL. Rendered via a
 * portal onto document.body so it always sits above headers/sidebars/other
 * modals regardless of where AuthenticatedPhoto itself is nested. */
function PhotoLightbox({ blobUrl, alt, onClose, triggerRef }: PhotoLightboxProps) {
  const closeButtonRef = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    closeButtonRef.current?.focus();

    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
    // Captured now (not read from the ref inside the cleanup) — the node
    // that was current AT OPEN TIME is what focus should return to, and
    // this also satisfies react-hooks/exhaustive-deps' warning about a
    // ref's `.current` possibly having changed by the time cleanup runs.
    const triggerNode = triggerRef.current;

    function handleKeyDown(e: KeyboardEvent) {
      if (e.key === 'Escape') {
        onClose();
        return;
      }
      // Only one interactive control exists in this dialog (the close
      // button) — Tab/Shift+Tab must never move focus out of it.
      if (e.key === 'Tab') {
        e.preventDefault();
        closeButtonRef.current?.focus();
      }
    }
    document.addEventListener('keydown', handleKeyDown);

    return () => {
      document.removeEventListener('keydown', handleKeyDown);
      document.body.style.overflow = previousOverflow;
      // Guarded — the thumbnail that opened this may have unmounted (e.g.
      // the surrounding list re-rendered) while the lightbox was open.
      triggerNode?.focus?.();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  function handleBackdropClick(e: React.MouseEvent<HTMLDivElement>) {
    // Only a click on the backdrop ITSELF closes it — a click that bubbled
    // up from the image or the close button must never close as a
    // side-effect.
    if (e.target === e.currentTarget) onClose();
  }

  return createPortal(
    <div
      className="fixed inset-0 z-[100] flex items-center justify-center bg-black/80 p-4 print:hidden"
      onClick={handleBackdropClick}
    >
      <div role="dialog" aria-modal="true" aria-label={alt} className="relative">
        <img src={blobUrl} alt={alt} className="max-h-[90vh] max-w-[95vw] object-contain" />
        <button
          ref={closeButtonRef}
          type="button"
          onClick={onClose}
          aria-label="ปิดภาพขนาดใหญ่"
          title="ปิด"
          className="absolute -right-3 -top-3 flex h-10 w-10 items-center justify-center rounded-full bg-white text-gray-700 shadow-lg hover:bg-gray-100 focus:outline-none focus:ring-2 focus:ring-green-500"
        >
          <X className="h-5 w-5" />
        </button>
      </div>
    </div>,
    document.body,
  );
}
