/**
 * inspection-photo-compression — round 8-14B client-side pre-compression for
 * inspection photos, shared by the logged-in RecordForm and the public
 * PublicInspect flow via PhotoSlotPicker (the one integration point for
 * both). Browser-native only (createImageBitmap / Canvas / canvas.toBlob /
 * HTMLImageElement fallback) — no new npm dependency.
 *
 * This is a UX/bandwidth optimization ONLY. The backend
 * (app/services/inspection_photos.py, round 8-14A/8-14A.1) re-decodes,
 * strips metadata, and re-encodes every uploaded photo to WebP from
 * scratch regardless of what the client sends — it is the sole security/
 * correctness authority for stored photo content. A browser that can't
 * compress (or a client that skips this module entirely) is expected to
 * keep working: the original file is a fully valid upload on its own,
 * just larger over the wire.
 *
 * Size/quality contract mirrors the backend's own (smaller numbers, since
 * this is a *pre*-compression pass and the backend still re-budgets to its
 * own 1.2/1.5 MiB WebP contract afterwards):
 *   - target  1.0 MiB   (backend target  1.2 MiB)
 *   - soft max 1.2 MiB  (backend hard max 1.5 MiB)
 *   - same max/min edge (2560 / 1280), same quality ladder shape (85→75)
 *
 * Never stores any binary/base64 anywhere persistent (localStorage,
 * sessionStorage, IndexedDB, React Query cache, URL/query string) — the
 * only output is an in-memory `File` handed back to the caller for an
 * immediate multipart upload.
 */

// --- Size / quality / dimension contract ------------------------------------

export const MAX_SOURCE_PHOTO_BYTES = 15 * 1024 * 1024;
export const CLIENT_TARGET_PHOTO_BYTES = 1 * 1024 * 1024;
export const CLIENT_MAX_PHOTO_BYTES = Math.floor(1.2 * 1024 * 1024);
export const MAX_PHOTO_EDGE = 2560;
export const MIN_PHOTO_EDGE = 1280;
export const INITIAL_WEBP_QUALITY = 0.85;
export const MIN_WEBP_QUALITY = 0.75;
export const WEBP_QUALITY_STEPS: readonly number[] = [0.85, 0.82, 0.8, 0.78, 0.76, 0.75];
export const DOWNSCALE_STEP = 0.85;

// Safety backstop only — with a start at MAX_PHOTO_EDGE and the floor
// clamped at MIN_PHOTO_EDGE (round 8-14B.1 — the floor itself is now always
// tried, see nextDownscaledDimensions), ×0.85 reaches the floor in 6 size
// steps (2560→2176→1850→1573→1337→1280), so 6 sizes × 6 quality steps (36)
// covers the entire real search space; 40 gives a little headroom while
// staying bounded, matching the backend's own _MAX_ENCODE_ATTEMPTS intent —
// a pathological image can never loop unbounded on a phone's CPU.
const MAX_ENCODE_ATTEMPTS = 40;

export const ALLOWED_SOURCE_MIME_TYPES: readonly string[] = ['image/jpeg', 'image/png', 'image/webp'];

// --- User-facing copy (Thai, curated — never a raw browser/decoder error) --

export const UNSUPPORTED_TYPE_MESSAGE = 'ไฟล์ต้องเป็นรูปภาพ JPG, PNG หรือ WebP เท่านั้น';
export const TOO_LARGE_MESSAGE = 'รูปภาพต้องมีขนาดไม่เกิน 15 MB';
export const DECODE_ERROR_MESSAGE = 'ไม่สามารถเตรียมรูปภาพนี้ได้ กรุณาเลือกรูปอื่น';
export const ENCODER_UNSUPPORTED_WARNING = 'เบราว์เซอร์ไม่สามารถลดขนาดรูปนี้ได้ ระบบจะตรวจสอบรูปอีกครั้งขณะอัปโหลด';

/** Curated failure — `.message` is always one of the Thai constants above,
 * safe to render directly. Never wraps/exposes a raw browser decoder
 * exception (its message could mention the original filename or bytes). */
export class InspectionPhotoPreparationError extends Error {
  constructor(message: string) {
    super(message);
    this.name = 'InspectionPhotoPreparationError';
  }
}

export interface PreparedInspectionPhoto {
  /** The file to upload — either a freshly compressed WebP, or (fallback)
   * the original source file, byte-for-byte untouched. */
  file: File;
  /** true only when `file` is a genuine re-encoded WebP produced here. */
  compressed: boolean;
  originalBytes: number;
  /** Byte size of `file` (== originalBytes when fallbackUsed is true). */
  outputBytes: number;
  /** true when `file` is the original source, unmodified (encoder
   * unsupported, or the compressed result was not actually smaller). */
  fallbackUsed: boolean;
  /** Non-blocking Thai copy to surface to the user, or null. Currently only
   * set when the browser could not encode WebP at all — the caller should
   * still accept the returned `file` (upload proceeds; the backend is
   * authoritative regardless). */
  warning: string | null;
}

// --- Validation (before any decode) -----------------------------------------

function validateSource(source: File): void {
  if (!ALLOWED_SOURCE_MIME_TYPES.includes(source.type)) {
    throw new InspectionPhotoPreparationError(UNSUPPORTED_TYPE_MESSAGE);
  }
  if (source.size > MAX_SOURCE_PHOTO_BYTES) {
    throw new InspectionPhotoPreparationError(TOO_LARGE_MESSAGE);
  }
  if (source.size === 0) {
    throw new InspectionPhotoPreparationError(DECODE_ERROR_MESSAGE);
  }
}

// --- Decode ------------------------------------------------------------------

interface DecodedImage {
  /** Anything `CanvasRenderingContext2D.drawImage` accepts. */
  source: CanvasImageSource;
  width: number;
  height: number;
  /** Releases the decoded resource — closes the ImageBitmap, or revokes the
   * fallback <img>'s object URL. Must be called exactly once, after every
   * canvas draw that needs `source` is finished. */
  close: () => void;
}

/** HTMLImageElement fallback — used when createImageBitmap is unavailable or
 * itself fails to decode. The object URL is revoked by the returned
 * `close()`, not on load, so `source` stays valid for `drawImage` calls made
 * after this promise resolves. */
function decodeViaImageElement(file: File): Promise<DecodedImage> {
  return new Promise((resolve, reject) => {
    const url = URL.createObjectURL(file);
    const img = new Image();
    img.onload = () => {
      resolve({
        source: img,
        width: img.naturalWidth,
        height: img.naturalHeight,
        close: () => URL.revokeObjectURL(url),
      });
    };
    img.onerror = () => {
      URL.revokeObjectURL(url);
      reject(new Error('image element decode failed'));
    };
    img.src = url;
  });
}

async function decodeSource(file: File): Promise<DecodedImage> {
  if (typeof createImageBitmap === 'function') {
    try {
      let bitmap: ImageBitmap;
      try {
        // Preferred: bakes EXIF rotation into the decoded pixels so the
        // canvas draw below never needs to reason about orientation itself.
        bitmap = await createImageBitmap(file, { imageOrientation: 'from-image' });
      } catch {
        // A handful of older engines throw on the options object itself
        // (not on the image) — retry with no options before giving up on
        // createImageBitmap entirely.
        bitmap = await createImageBitmap(file);
      }
      return {
        source: bitmap,
        width: bitmap.width,
        height: bitmap.height,
        close: () => bitmap.close(),
      };
    } catch {
      // Fall through to the <img> fallback below.
    }
  }
  return decodeViaImageElement(file);
}

// --- Dimensions ----------------------------------------------------------

/** Caps the longest edge at MAX_PHOTO_EDGE, preserving aspect ratio. Never
 * upscales — an image already within bounds is returned unchanged. */
function computeInitialDimensions(width: number, height: number): { width: number; height: number } {
  const longest = Math.max(width, height);
  if (longest <= MAX_PHOTO_EDGE) {
    return { width, height };
  }
  const scale = MAX_PHOTO_EDGE / longest;
  return {
    width: Math.max(1, Math.round(width * scale)),
    height: Math.max(1, Math.round(height * scale)),
  };
}

// --- Canvas draw + encode ----------------------------------------------------

/** Fills white first, then draws — transparent PNG/WebP sources get a
 * consistent white background, matching the backend's own compositing so a
 * photo doesn't visibly change appearance between what the client
 * previewed and what the backend ultimately stores. */
function drawToCanvas(source: CanvasImageSource, width: number, height: number): HTMLCanvasElement {
  const canvas = document.createElement('canvas');
  canvas.width = width;
  canvas.height = height;
  const ctx = canvas.getContext('2d');
  if (!ctx) throw new Error('canvas 2d context unavailable');
  ctx.fillStyle = '#ffffff';
  ctx.fillRect(0, 0, width, height);
  ctx.drawImage(source, 0, 0, width, height);
  return canvas;
}

function encodeCanvasToWebp(canvas: HTMLCanvasElement, quality: number): Promise<Blob | null> {
  return new Promise((resolve) => {
    canvas.toBlob((blob) => resolve(blob), 'image/webp', quality);
  });
}

type CompressResult = { blob: Blob; width: number; height: number };

/** Round 8-14B.1 — one downscale step, measured against the LONGEST edge
 * (not each axis independently, which would silently distort aspect ratio
 * whenever width and height aren't equal) and clamped so the longest edge
 * never drops below MIN_PHOTO_EDGE. The last candidate this can ever
 * produce has its longest edge exactly MIN_PHOTO_EDGE — so the safety
 * floor itself is always tried before compression gives up, never skipped.
 * Returns null once a size AT the floor has already been tried (there is
 * nothing smaller left to attempt). */
function nextDownscaledDimensions(width: number, height: number): { width: number; height: number } | null {
  const currentLongest = Math.max(width, height);
  if (currentLongest <= MIN_PHOTO_EDGE) return null;
  const nextLongest = Math.max(MIN_PHOTO_EDGE, Math.round(currentLongest * DOWNSCALE_STEP));
  const scale = nextLongest / currentLongest;
  return {
    width: Math.max(1, Math.round(width * scale)),
    height: Math.max(1, Math.round(height * scale)),
  };
}

/** Same target-then-ceiling search the backend uses: try the quality ladder
 * at the current size (first result ≤ target wins); if the ladder is
 * exhausted but the smallest result seen is still ≤ the soft max, accept
 * it; otherwise downscale one step (see nextDownscaledDimensions — never
 * below MIN_PHOTO_EDGE, but the floor itself IS always tried) and retry.
 * Returns null only when the browser cannot produce a `image/webp` blob at
 * all (canvas.toBlob returned null, or returned a different type — some
 * engines silently fall back to PNG for an unsupported requested type).
 * Never drops quality below MIN_WEBP_QUALITY or the size below
 * MIN_PHOTO_EDGE just to chase the target — a bounded "best effort" is
 * returned instead. The best candidate is tracked as a full
 * `{blob, width, height}` triple (never just the Blob) so the dimensions
 * returned always describe the size level that actually produced that
 * Blob, even when a later, worse-quality attempt at a DIFFERENT size is
 * what ends up being evaluated last. */
async function compressWithinBudget(
  source: CanvasImageSource,
  initialWidth: number,
  initialHeight: number,
): Promise<CompressResult | null> {
  let width = initialWidth;
  let height = initialHeight;
  let attempts = 0;
  let best: CompressResult | null = null;

  for (;;) {
    const canvas = drawToCanvas(source, width, height);
    for (const quality of WEBP_QUALITY_STEPS) {
      attempts += 1;
      const blob = await encodeCanvasToWebp(canvas, quality);
      if (!blob || blob.type !== 'image/webp') {
        return best;
      }
      if (!best || blob.size < best.blob.size) best = { blob, width, height };
      if (blob.size <= CLIENT_TARGET_PHOTO_BYTES) {
        return { blob, width, height };
      }
      if (attempts >= MAX_ENCODE_ATTEMPTS) {
        return best;
      }
    }
    if (best && best.blob.size <= CLIENT_MAX_PHOTO_BYTES) {
      return best;
    }
    if (attempts >= MAX_ENCODE_ATTEMPTS) {
      return best;
    }
    const next = nextDownscaledDimensions(width, height);
    if (!next) {
      return best;
    }
    width = next.width;
    height = next.height;
  }
}

function buildFallback(source: File, originalBytes: number, warning: string | null): PreparedInspectionPhoto {
  return {
    file: source,
    compressed: false,
    originalBytes,
    outputBytes: originalBytes,
    fallbackUsed: true,
    warning,
  };
}

/**
 * Validates, decodes, and (best-effort) compresses one inspection photo to
 * WebP entirely in the browser. Always resolves to something uploadable —
 * either the compressed WebP or the original source file — unless the
 * source itself fails validation or cannot be decoded at all, in which case
 * it rejects with an `InspectionPhotoPreparationError` carrying a curated
 * Thai message safe to show directly.
 *
 * `outputName` names the file ONLY when a fresh compressed WebP is
 * produced — never applied to a fallback (the original `File` is returned
 * completely untouched, including its own name/type/lastModified, exactly
 * as it always was before this round).
 */
export async function prepareInspectionPhoto(source: File, outputName: string): Promise<PreparedInspectionPhoto> {
  validateSource(source);
  const originalBytes = source.size;

  let decoded: DecodedImage;
  try {
    decoded = await decodeSource(source);
  } catch {
    throw new InspectionPhotoPreparationError(DECODE_ERROR_MESSAGE);
  }

  try {
    if (!decoded.width || !decoded.height) {
      throw new InspectionPhotoPreparationError(DECODE_ERROR_MESSAGE);
    }

    const initial = computeInitialDimensions(decoded.width, decoded.height);
    const result = await compressWithinBudget(decoded.source, initial.width, initial.height);

    if (!result) {
      return buildFallback(source, originalBytes, ENCODER_UNSUPPORTED_WARNING);
    }
    if (result.blob.size >= originalBytes) {
      // The compressed WebP isn't actually smaller — use the original; the
      // backend converts to WebP on its own end regardless.
      return buildFallback(source, originalBytes, null);
    }

    return {
      file: new File([result.blob], outputName, { type: 'image/webp', lastModified: Date.now() }),
      compressed: true,
      originalBytes,
      outputBytes: result.blob.size,
      fallbackUsed: false,
      warning: null,
    };
  } finally {
    decoded.close();
  }
}
