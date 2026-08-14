/**
 * inspection-photo-compression — round 8-14B. jsdom has no real canvas/
 * image-decoder backend, so every test here drives the module through hand-
 * rolled stand-ins for the three browser surfaces it touches
 * (createImageBitmap, HTMLCanvasElement#getContext/#toBlob, and the <img>
 * fallback) — never the real decode/encode pipeline. That's deliberate: this
 * suite proves the module's OWN search/validation/cleanup logic; whether a
 * real browser's WebP encoder produces bytes that look a certain way is out
 * of scope for jsdom and is instead covered by manual browser QA (see round
 * 8-14B's Final Report).
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import {
  prepareInspectionPhoto,
  InspectionPhotoPreparationError,
  MAX_SOURCE_PHOTO_BYTES,
  CLIENT_TARGET_PHOTO_BYTES,
  CLIENT_MAX_PHOTO_BYTES,
  MAX_PHOTO_EDGE,
  MIN_PHOTO_EDGE,
  MIN_WEBP_QUALITY,
  WEBP_QUALITY_STEPS,
  UNSUPPORTED_TYPE_MESSAGE,
  TOO_LARGE_MESSAGE,
  DECODE_ERROR_MESSAGE,
  ENCODER_UNSUPPORTED_WARNING,
} from './inspection-photo-compression';

// --- test doubles ------------------------------------------------------------

function fileOfSize(bytes: number, type = 'image/jpeg', name = 'a.jpg'): File {
  // Content doesn't matter — decode is entirely mocked — only .size/.type do.
  return new File([new Uint8Array(Math.max(bytes, 0))], name, { type });
}

interface FakeBitmap {
  width: number;
  height: number;
  close: ReturnType<typeof vi.fn>;
}

function fakeBitmap(width: number, height: number): FakeBitmap {
  return { width, height, close: vi.fn() };
}

/** Records every draw (by canvas size) and every encode attempt (by quality
 * + the size it was drawn at), and lets a test script exactly what
 * `toBlob` returns per call via `nextBlob`/`blobScript`. */
function installCanvasMocks() {
  const draws: { width: number; height: number }[] = [];
  const encodeCalls: { width: number; height: number; quality: number }[] = [];
  const fillRect = vi.fn();
  const drawImage = vi.fn();
  let fillStyleSeen: string | null = null;

  let blobScript: (call: { width: number; height: number; quality: number }) => Blob | null = () => null;

  const getContext = vi.fn(function (this: HTMLCanvasElement) {
    draws.push({ width: this.width, height: this.height });
    return {
      set fillStyle(v: string) { fillStyleSeen = v; },
      get fillStyle() { return fillStyleSeen ?? ''; },
      fillRect,
      drawImage,
    } as unknown as CanvasRenderingContext2D;
  });

  const toBlob = vi.fn(function (
    this: HTMLCanvasElement,
    cb: BlobCallback,
    _type?: string,
    quality?: number,
  ) {
    const call = { width: this.width, height: this.height, quality: quality ?? 1 };
    encodeCalls.push(call);
    const blob = blobScript(call);
    // Real canvas.toBlob is asynchronous — queue a microtask so callers that
    // `await` the promise wrapper genuinely exercise the async path.
    Promise.resolve().then(() => cb(blob));
  });

  HTMLCanvasElement.prototype.getContext = getContext as unknown as typeof HTMLCanvasElement.prototype.getContext;
  HTMLCanvasElement.prototype.toBlob = toBlob as unknown as typeof HTMLCanvasElement.prototype.toBlob;

  return {
    draws,
    encodeCalls,
    fillRect,
    drawImage,
    fillStyleSeenRef: () => fillStyleSeen,
    setBlobScript(fn: typeof blobScript) { blobScript = fn; },
  };
}

function webpBlob(size: number): Blob {
  return { size, type: 'image/webp' } as Blob;
}

let originalGetContext: typeof HTMLCanvasElement.prototype.getContext;
let originalToBlob: typeof HTMLCanvasElement.prototype.toBlob;
let originalCreateImageBitmap: typeof globalThis.createImageBitmap | undefined;
let originalImage: typeof Image;

beforeEach(() => {
  originalGetContext = HTMLCanvasElement.prototype.getContext;
  originalToBlob = HTMLCanvasElement.prototype.toBlob;
  originalCreateImageBitmap = globalThis.createImageBitmap;
  originalImage = globalThis.Image;
});

afterEach(() => {
  HTMLCanvasElement.prototype.getContext = originalGetContext;
  HTMLCanvasElement.prototype.toBlob = originalToBlob;
  if (originalCreateImageBitmap) {
    globalThis.createImageBitmap = originalCreateImageBitmap;
  } else {
    // @ts-expect-error -- jsdom doesn't define this by default; restore that.
    delete globalThis.createImageBitmap;
  }
  globalThis.Image = originalImage;
  vi.restoreAllMocks();
});

/** Installs a working createImageBitmap stub reporting the given decoded
 * dimensions — the path most tests use (no <img> fallback involved). */
function useImageBitmapDecode(width: number, height: number): FakeBitmap {
  const bitmap = fakeBitmap(width, height);
  globalThis.createImageBitmap = vi.fn(async () => bitmap as unknown as ImageBitmap);
  return bitmap;
}

// --- 1-3: validation before decode ------------------------------------------

describe('prepareInspectionPhoto — validation before decode', () => {
  it('1. rejects an unsupported MIME type with the curated Thai message', async () => {
    const file = fileOfSize(1000, 'application/pdf', 'a.pdf');
    await expect(prepareInspectionPhoto(file, 'out.webp')).rejects.toMatchObject({
      message: UNSUPPORTED_TYPE_MESSAGE,
    });
    await expect(prepareInspectionPhoto(file, 'out.webp')).rejects.toBeInstanceOf(InspectionPhotoPreparationError);
  });

  it('2. rejects an empty file', async () => {
    const file = fileOfSize(0, 'image/jpeg');
    await expect(prepareInspectionPhoto(file, 'out.webp')).rejects.toMatchObject({
      message: DECODE_ERROR_MESSAGE,
    });
  });

  it('3. rejects a source over 15 MiB, never attempting to decode it', async () => {
    const decodeSpy = vi.fn();
    globalThis.createImageBitmap = decodeSpy as unknown as typeof createImageBitmap;
    const file = fileOfSize(MAX_SOURCE_PHOTO_BYTES + 1, 'image/jpeg');

    await expect(prepareInspectionPhoto(file, 'out.webp')).rejects.toMatchObject({
      message: TOO_LARGE_MESSAGE,
    });
    expect(decodeSpy).not.toHaveBeenCalled();
  });
});

// --- 4-8: format conversion + output contract -------------------------------

describe('prepareInspectionPhoto — output contract', () => {
  it('4. a JPEG source produces a WebP File named exactly outputName', async () => {
    useImageBitmapDecode(800, 600);
    const mocks = installCanvasMocks();
    mocks.setBlobScript(() => webpBlob(500));

    const result = await prepareInspectionPhoto(fileOfSize(2000, 'image/jpeg'), 'inspection-photo-1.webp');

    expect(result.compressed).toBe(true);
    expect(result.fallbackUsed).toBe(false);
    expect(result.file.type).toBe('image/webp');
    expect(result.file.name).toBe('inspection-photo-1.webp');
    expect(result.outputBytes).toBe(500);
  });

  it('5. a PNG source produces a WebP File', async () => {
    useImageBitmapDecode(800, 600);
    const mocks = installCanvasMocks();
    mocks.setBlobScript(() => webpBlob(500));

    const result = await prepareInspectionPhoto(fileOfSize(2000, 'image/png', 'a.png'), 'inspection-photo-2.webp');

    expect(result.file.type).toBe('image/webp');
    expect(result.file.name).toBe('inspection-photo-2.webp');
  });

  it('6. a WebP source is genuinely re-encoded, not passed through untouched', async () => {
    useImageBitmapDecode(800, 600);
    const mocks = installCanvasMocks();
    mocks.setBlobScript(() => webpBlob(500));

    const source = fileOfSize(2000, 'image/webp', 'a.webp');
    const result = await prepareInspectionPhoto(source, 'inspection-photo-3.webp');

    expect(mocks.encodeCalls.length).toBeGreaterThan(0); // the canvas encoder really ran
    expect(result.file).not.toBe(source); // not the same File object handed back
    expect(result.outputBytes).toBe(500);
  });

  it('8. the output filename is the generic name passed in, never the source filename', async () => {
    useImageBitmapDecode(800, 600);
    const mocks = installCanvasMocks();
    mocks.setBlobScript(() => webpBlob(500));

    const result = await prepareInspectionPhoto(
      fileOfSize(2000, 'image/jpeg', 'IMG_20260101_super_private_name.jpg'),
      'inspection-photo-1.webp',
    );

    expect(result.file.name).toBe('inspection-photo-1.webp');
    expect(result.file.name).not.toContain('IMG_20260101');
  });
});

// --- 9-11: dimensions --------------------------------------------------------

describe('prepareInspectionPhoto — dimensions', () => {
  it('9. preserves aspect ratio when downscaling', async () => {
    useImageBitmapDecode(5120, 2560); // 2:1
    const mocks = installCanvasMocks();
    mocks.setBlobScript(() => webpBlob(500));

    await prepareInspectionPhoto(fileOfSize(2000), 'out.webp');

    const [{ width, height }] = mocks.draws;
    expect(width / height).toBeCloseTo(2, 5);
  });

  it('10. caps the longest edge at MAX_PHOTO_EDGE (2560)', async () => {
    useImageBitmapDecode(5120, 2560);
    const mocks = installCanvasMocks();
    mocks.setBlobScript(() => webpBlob(500));

    await prepareInspectionPhoto(fileOfSize(2000), 'out.webp');

    const [{ width, height }] = mocks.draws;
    expect(Math.max(width, height)).toBe(MAX_PHOTO_EDGE);
    expect(width).toBe(2560);
    expect(height).toBe(1280);
  });

  it('11. never upscales an image already smaller than MAX_PHOTO_EDGE', async () => {
    useImageBitmapDecode(800, 600);
    const mocks = installCanvasMocks();
    mocks.setBlobScript(() => webpBlob(500));

    await prepareInspectionPhoto(fileOfSize(2000), 'out.webp');

    const [{ width, height }] = mocks.draws;
    expect(width).toBe(800);
    expect(height).toBe(600);
  });
});

// --- 12-16: quality ladder / target / soft-max / downscale / attempt guard --

describe('prepareInspectionPhoto — quality ladder and size search', () => {
  it('12. quality is never dropped below MIN_WEBP_QUALITY (0.75)', async () => {
    useImageBitmapDecode(2560, 2560); // square — the downscale chain lands exactly on the floor at the 6th size level
    const mocks = installCanvasMocks();
    // Always oversized — forces the full ladder, every size step, every time.
    mocks.setBlobScript(() => webpBlob(CLIENT_MAX_PHOTO_BYTES * 10));

    await prepareInspectionPhoto(fileOfSize(1), 'out.webp');

    const qualities = mocks.encodeCalls.map((c) => c.quality);
    expect(Math.min(...qualities)).toBe(MIN_WEBP_QUALITY);
    expect(qualities.every((q) => q >= MIN_WEBP_QUALITY)).toBe(true);
  });

  it('13. the first result at/under the target (1.0 MiB) is used immediately — no further attempts', async () => {
    useImageBitmapDecode(800, 600);
    const mocks = installCanvasMocks();
    mocks.setBlobScript(() => webpBlob(CLIENT_TARGET_PHOTO_BYTES - 1000));

    const result = await prepareInspectionPhoto(fileOfSize(2_000_000), 'out.webp');

    expect(mocks.encodeCalls).toHaveLength(1);
    expect(mocks.encodeCalls[0].quality).toBe(WEBP_QUALITY_STEPS[0]);
    expect(result.outputBytes).toBe(CLIENT_TARGET_PHOTO_BYTES - 1000);
  });

  it('14. once the quality ladder is exhausted, a result within the soft max (1.2 MiB) is accepted without downscaling', async () => {
    useImageBitmapDecode(800, 600);
    const mocks = installCanvasMocks();
    // Every quality step lands strictly between the target and the soft max.
    mocks.setBlobScript(() => webpBlob(CLIENT_TARGET_PHOTO_BYTES + 1000));

    const result = await prepareInspectionPhoto(fileOfSize(2_000_000), 'out.webp');

    expect(mocks.encodeCalls).toHaveLength(WEBP_QUALITY_STEPS.length); // full ladder tried, once
    expect(mocks.draws).toHaveLength(1); // never downscaled
    expect(result.outputBytes).toBe(CLIENT_TARGET_PHOTO_BYTES + 1000);
  });

  it('15. downscales one step at a time (never below MIN_PHOTO_EDGE) when even the soft max cannot be met at the current size', async () => {
    useImageBitmapDecode(2560, 2560);
    const mocks = installCanvasMocks();
    // Oversized at the first size, small enough to hit the target the moment
    // a downscale happens (any width < 2560 crosses the threshold here).
    mocks.setBlobScript((call) => (
      call.width < 2560 ? webpBlob(500) : webpBlob(CLIENT_MAX_PHOTO_BYTES * 5)
    ));

    await prepareInspectionPhoto(fileOfSize(2_000_000), 'out.webp');

    expect(mocks.draws.length).toBeGreaterThan(1);
    const widths = mocks.draws.map((d) => d.width);
    expect(widths[1]).toBe(Math.round(2560 * 0.85));
    expect(widths.every((w) => w >= MIN_PHOTO_EDGE)).toBe(true);
  });

  it('16. has a bounded maximum-attempt guard (40) — never loops forever on a pathological image', async () => {
    useImageBitmapDecode(2560, 2560);
    const mocks = installCanvasMocks();
    mocks.setBlobScript(() => webpBlob(CLIENT_MAX_PHOTO_BYTES * 10)); // never good enough at any size/quality

    await prepareInspectionPhoto(fileOfSize(1), 'out.webp');

    expect(mocks.encodeCalls.length).toBeLessThanOrEqual(40);
    expect(mocks.encodeCalls.length).toBeGreaterThan(0);
  });
});

// --- round 8-14B.1: the 1280px safety floor is genuinely tried -------------

describe('prepareInspectionPhoto — round 8-14B.1: the safety floor is genuinely attempted', () => {
  it('1. a 2560×2560 source whose every candidate exceeds the soft max is eventually drawn at exactly 1280×1280', async () => {
    useImageBitmapDecode(2560, 2560);
    const mocks = installCanvasMocks();
    mocks.setBlobScript(() => webpBlob(CLIENT_MAX_PHOTO_BYTES * 10)); // never good enough anywhere

    await prepareInspectionPhoto(fileOfSize(2_000_000), 'out.webp');

    const floorDraw = mocks.draws.find((d) => d.width === MIN_PHOTO_EDGE);
    expect(floorDraw).toEqual({ width: MIN_PHOTO_EDGE, height: MIN_PHOTO_EDGE });
    // The full downscale chain: 2560 → 2176 → 1850 → 1573 → 1337 → 1280 —
    // six distinct size levels, the floor being the last, never skipped.
    const widths = mocks.draws.map((d) => d.width);
    expect(widths).toEqual([2560, 2176, 1850, 1573, 1337, 1280]);
  });

  it('2. the full quality ladder (all 6 steps) is tried at the floor, not just at larger sizes', async () => {
    useImageBitmapDecode(2560, 2560);
    const mocks = installCanvasMocks();
    mocks.setBlobScript(() => webpBlob(CLIENT_MAX_PHOTO_BYTES * 10));

    await prepareInspectionPhoto(fileOfSize(2_000_000), 'out.webp');

    const floorQualities = mocks.encodeCalls.filter((c) => c.width === MIN_PHOTO_EDGE).map((c) => c.quality);
    expect(floorQualities).toEqual(WEBP_QUALITY_STEPS);
  });

  it('3. total attempts across all 6 size levels never exceed 40 (6 sizes × 6 qualities = 36 in the real worst case)', async () => {
    useImageBitmapDecode(2560, 2560);
    const mocks = installCanvasMocks();
    mocks.setBlobScript(() => webpBlob(CLIENT_MAX_PHOTO_BYTES * 10));

    await prepareInspectionPhoto(fileOfSize(2_000_000), 'out.webp');

    expect(mocks.encodeCalls.length).toBe(36);
    expect(mocks.encodeCalls.length).toBeLessThanOrEqual(40);
  });

  it('5. never upscales a source already below the floor toward it', async () => {
    useImageBitmapDecode(1000, 800); // longest edge 1000 < MIN_PHOTO_EDGE (1280)
    const mocks = installCanvasMocks();
    mocks.setBlobScript(() => webpBlob(CLIENT_MAX_PHOTO_BYTES * 10)); // never good enough

    await prepareInspectionPhoto(fileOfSize(2_000_000), 'out.webp');

    // Only the original size is ever drawn — no downscale attempt exists to
    // upscale FROM, since nextDownscaledDimensions bails out immediately.
    expect(mocks.draws).toEqual([{ width: 1000, height: 800 }]);
  });

  it('7. aspect ratio is preserved exactly when clamped down to the floor (non-square source)', async () => {
    useImageBitmapDecode(3840, 1920); // 2:1
    const mocks = installCanvasMocks();
    mocks.setBlobScript(() => webpBlob(CLIENT_MAX_PHOTO_BYTES * 10));

    await prepareInspectionPhoto(fileOfSize(2_000_000), 'out.webp');

    const floorDraw = mocks.draws[mocks.draws.length - 1];
    expect(Math.max(floorDraw.width, floorDraw.height)).toBe(MIN_PHOTO_EDGE);
    expect(floorDraw.width / floorDraw.height).toBeCloseTo(2, 2);
  });

  it('8. the accepted result genuinely comes from the size level that produced it, not a mismatched leftover', async () => {
    useImageBitmapDecode(2560, 2560);
    const mocks = installCanvasMocks();
    // Only ONE specific (size, quality) combination — the very last quality
    // step at the floor — ever produces an acceptable (between target and
    // soft max) Blob; every other attempt at every other size is oversized.
    // Before round 8-14B.1, `best` tracked only a bare Blob alongside
    // separately-scoped width/height variables — this proves the returned
    // byte size can only have come from that one exact size level.
    const ACCEPTED_BYTES = CLIENT_TARGET_PHOTO_BYTES + 50_000;
    mocks.setBlobScript((call) => (
      call.width === MIN_PHOTO_EDGE && call.quality === MIN_WEBP_QUALITY
        ? webpBlob(ACCEPTED_BYTES)
        : webpBlob(CLIENT_MAX_PHOTO_BYTES * 5)
    ));

    const result = await prepareInspectionPhoto(fileOfSize(2_000_000), 'out.webp');

    expect(result.outputBytes).toBe(ACCEPTED_BYTES);
    const widths = mocks.draws.map((d) => d.width);
    expect(widths[widths.length - 1]).toBe(MIN_PHOTO_EDGE); // search stopped at the floor, as expected
    expect(widths.every((w) => w >= MIN_PHOTO_EDGE)).toBe(true); // never went past it
  });
});

// --- 17-19: white background / cleanup --------------------------------------

describe('prepareInspectionPhoto — compositing and resource cleanup', () => {
  it('17. fills white before drawing, so a transparent source gets a white background', async () => {
    useImageBitmapDecode(800, 600);
    const mocks = installCanvasMocks();
    mocks.setBlobScript(() => webpBlob(500));

    await prepareInspectionPhoto(fileOfSize(2000, 'image/png'), 'out.webp');

    expect(mocks.fillStyleSeenRef()).toBe('#ffffff');
    expect(mocks.fillRect).toHaveBeenCalled();
    expect(mocks.drawImage).toHaveBeenCalled();
    // fillRect must run before drawImage for the white base to actually show
    // through any transparent pixels.
    const fillOrder = mocks.fillRect.mock.invocationCallOrder[0];
    const drawOrder = mocks.drawImage.mock.invocationCallOrder[0];
    expect(fillOrder).toBeLessThan(drawOrder);
  });

  it('18. closes the ImageBitmap exactly once after finishing', async () => {
    const bitmap = useImageBitmapDecode(800, 600);
    const mocks = installCanvasMocks();
    mocks.setBlobScript(() => webpBlob(500));

    await prepareInspectionPhoto(fileOfSize(2000), 'out.webp');

    expect(bitmap.close).toHaveBeenCalledTimes(1);
  });

  it('19. revokes the fallback <img> object URL after use', async () => {
    // @ts-expect-error -- simulate a browser with no createImageBitmap at all.
    delete globalThis.createImageBitmap;
    const revokeSpy = vi.spyOn(URL, 'revokeObjectURL');
    const createSpy = vi.spyOn(URL, 'createObjectURL').mockReturnValue('blob:fake-url');

    class FakeImage {
      onload: (() => void) | null = null;
      onerror: (() => void) | null = null;
      naturalWidth = 800;
      naturalHeight = 600;
      set src(_v: string) {
        Promise.resolve().then(() => this.onload?.());
      }
    }
    globalThis.Image = FakeImage as unknown as typeof Image;

    const mocks = installCanvasMocks();
    mocks.setBlobScript(() => webpBlob(500));

    await prepareInspectionPhoto(fileOfSize(2000), 'out.webp');

    expect(createSpy).toHaveBeenCalledWith(expect.any(File));
    expect(revokeSpy).toHaveBeenCalledWith('blob:fake-url');
  });
});

// --- 20-22: fallback behavior -------------------------------------------------

describe('prepareInspectionPhoto — fallback to the original source', () => {
  it('20. canvas.toBlob resolving null falls back to the source safely, with a non-blocking warning', async () => {
    useImageBitmapDecode(800, 600);
    const mocks = installCanvasMocks();
    mocks.setBlobScript(() => null);

    const source = fileOfSize(2000, 'image/jpeg');
    const result = await prepareInspectionPhoto(source, 'out.webp');

    expect(result.fallbackUsed).toBe(true);
    expect(result.compressed).toBe(false);
    expect(result.file).toBe(source);
    expect(result.warning).toBe(ENCODER_UNSUPPORTED_WARNING);
  });

  it('21. an encoder that cannot actually produce image/webp falls back to the source', async () => {
    useImageBitmapDecode(800, 600);
    const mocks = installCanvasMocks();
    // Some engines silently substitute PNG for an unsupported requested type
    // instead of returning null — must be treated the same as "unsupported".
    mocks.setBlobScript(() => ({ size: 400, type: 'image/png' }) as Blob);

    const source = fileOfSize(2000, 'image/jpeg');
    const result = await prepareInspectionPhoto(source, 'out.webp');

    expect(result.fallbackUsed).toBe(true);
    expect(result.file).toBe(source);
    expect(result.warning).toBe(ENCODER_UNSUPPORTED_WARNING);
  });

  it('22. a compressed result that ended up bigger than the source falls back to the (smaller) source, no warning', async () => {
    useImageBitmapDecode(800, 600);
    const mocks = installCanvasMocks();
    const source = fileOfSize(1000, 'image/jpeg'); // tiny original
    mocks.setBlobScript(() => webpBlob(5000)); // "compressed" result is bigger

    const result = await prepareInspectionPhoto(source, 'out.webp');

    expect(result.fallbackUsed).toBe(true);
    expect(result.file).toBe(source);
    expect(result.outputBytes).toBe(1000);
    expect(result.warning).toBeNull();
  });
});

// --- 23: no raw browser error ever reaches the caller -----------------------

describe('prepareInspectionPhoto — never leaks a raw decoder error', () => {
  it('23. a raw exception from decode is replaced with the curated Thai message', async () => {
    globalThis.createImageBitmap = vi.fn(async () => {
      throw new Error('SecurityError: tainted canvas at /Users/real/private/path/photo.jpg');
    });
    class ThrowingImage {
      set src(_v: string) {
        throw new Error('some other raw engine-internal decoder error');
      }
    }
    globalThis.Image = ThrowingImage as unknown as typeof Image;

    const file = fileOfSize(2000, 'image/jpeg');
    await expect(prepareInspectionPhoto(file, 'out.webp')).rejects.toMatchObject({
      message: DECODE_ERROR_MESSAGE,
    });
    // Explicitly prove the raw message text never appears anywhere in what
    // the caller would see.
    try {
      await prepareInspectionPhoto(file, 'out.webp');
    } catch (err) {
      expect(String((err as Error).message)).not.toContain('tainted canvas');
      expect(String((err as Error).message)).not.toContain('/Users/real/private/path');
    }
  });

  it('rejects with the curated message when the decoded image reports zero dimensions', async () => {
    useImageBitmapDecode(0, 0);
    await expect(prepareInspectionPhoto(fileOfSize(2000), 'out.webp')).rejects.toMatchObject({
      message: DECODE_ERROR_MESSAGE,
    });
  });
});
