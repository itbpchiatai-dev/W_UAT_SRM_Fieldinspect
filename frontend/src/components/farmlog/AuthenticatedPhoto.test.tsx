import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import { AuthenticatedPhoto } from './AuthenticatedPhoto';

const getRecordPhotoBlobMock = vi.fn();

vi.mock('../../api/records', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../api/records')>();
  return {
    ...actual,
    getRecordPhotoBlob: (...args: unknown[]) => getRecordPhotoBlobMock(...args),
  };
});

function deferred<T>() {
  let resolve!: (v: T) => void;
  let reject!: (e: unknown) => void;
  const promise = new Promise<T>((res, rej) => { resolve = res; reject = rej; });
  return { promise, resolve, reject };
}

let createObjectURLCounter = 0;
function stubCreateObjectURL() {
  return vi.spyOn(URL, 'createObjectURL').mockImplementation(() => `blob:fake-url-${++createObjectURLCounter}`);
}

const PHOTO_URL_A = `/media/inspection-photos/${'a'.repeat(32)}.jpg`;
const PHOTO_URL_B = `/media/inspection-photos/${'b'.repeat(32)}.png`;

beforeEach(() => {
  getRecordPhotoBlobMock.mockReset();
  createObjectURLCounter = 0;
  vi.restoreAllMocks();
  document.body.style.overflow = '';
});

describe('AuthenticatedPhoto — 1: fetch via the scoped endpoint (unchanged)', () => {
  it('fetches the blob via the scoped endpoint and renders it as an object URL', async () => {
    const fakeBlob = new Blob(['x'], { type: 'image/jpeg' });
    getRecordPhotoBlobMock.mockResolvedValue(fakeBlob);
    const createSpy = stubCreateObjectURL();

    render(<AuthenticatedPhoto recordId="rec-1" photoUrl={PHOTO_URL_A} alt="test photo" />);

    const img = await screen.findByAltText('test photo');
    expect(img).toHaveProperty('src', expect.stringContaining('blob:fake-url'));
    expect(getRecordPhotoBlobMock).toHaveBeenCalledWith('rec-1', `${'a'.repeat(32)}.jpg`);
    expect(createSpy).toHaveBeenCalledWith(fakeBlob);
  });
});

describe('AuthenticatedPhoto — 2-5: only a loaded thumbnail is clickable', () => {
  it('2. a successfully loaded thumbnail renders as a clickable button', async () => {
    getRecordPhotoBlobMock.mockResolvedValue(new Blob(['x'], { type: 'image/jpeg' }));
    stubCreateObjectURL();
    render(<AuthenticatedPhoto recordId="rec-1" photoUrl={PHOTO_URL_A} alt="p" />);

    const button = await screen.findByRole('button', { name: 'เปิดดูภาพถ่ายแปลงขนาดใหญ่' });
    expect(button).toBeTruthy();
    expect(button.querySelector('img')).toBeTruthy();
  });

  it('3. a loading thumbnail cannot open the modal (no button rendered)', async () => {
    const d = deferred<Blob>();
    getRecordPhotoBlobMock.mockReturnValue(d.promise);
    render(<AuthenticatedPhoto recordId="rec-1" photoUrl={PHOTO_URL_A} alt="p" />);

    expect(screen.queryByRole('button')).toBeNull();
    expect(screen.queryByRole('dialog')).toBeNull();
    d.resolve(new Blob(['x'], { type: 'image/jpeg' })); // let it settle so the test doesn't leak a pending promise
  });

  it('4. a failed thumbnail cannot open the modal (no button rendered)', async () => {
    getRecordPhotoBlobMock.mockRejectedValue(new Error('403'));
    render(<AuthenticatedPhoto recordId="rec-1" photoUrl={PHOTO_URL_A} alt="p" />);

    await waitFor(() => expect(screen.queryByRole('img')).toBeNull());
    expect(screen.queryByRole('button')).toBeNull();
    expect(screen.queryByRole('dialog')).toBeNull();
  });

  it('5. an invalid photoUrl never calls the API and cannot open the modal', async () => {
    render(<AuthenticatedPhoto recordId="rec-1" photoUrl="/media/inspection-photos/../../../etc/passwd" />);

    await waitFor(() => expect(getRecordPhotoBlobMock).not.toHaveBeenCalled());
    expect(screen.queryByRole('img')).toBeNull();
    expect(screen.queryByRole('button')).toBeNull();
    expect(screen.queryByRole('dialog')).toBeNull();
  });
});

describe('AuthenticatedPhoto — 6-11: opening the lightbox reuses the same Blob, all three formats', () => {
  async function renderLoaded(type: string, photoUrl = PHOTO_URL_A) {
    getRecordPhotoBlobMock.mockResolvedValue(new Blob(['x'], { type }));
    stubCreateObjectURL();
    render(<AuthenticatedPhoto recordId="rec-1" photoUrl={photoUrl} alt="p" />);
    const button = await screen.findByRole('button', { name: 'เปิดดูภาพถ่ายแปลงขนาดใหญ่' });
    return button;
  }

  it('6. clicking the thumbnail opens a dialog', async () => {
    const button = await renderLoaded('image/webp');
    fireEvent.click(button);
    expect(await screen.findByRole('dialog')).toBeTruthy();
  });

  it('7. the dialog image uses the exact same Blob URL as the thumbnail', async () => {
    const button = await renderLoaded('image/webp');
    const thumbSrc = button.querySelector('img')!.getAttribute('src');
    fireEvent.click(button);

    const dialog = await screen.findByRole('dialog');
    const dialogImg = dialog.querySelector('img')!;
    expect(dialogImg.getAttribute('src')).toBe(thumbSrc);
  });

  it('8. opening the dialog never calls getRecordPhotoBlob again', async () => {
    const button = await renderLoaded('image/webp');
    expect(getRecordPhotoBlobMock).toHaveBeenCalledTimes(1);
    fireEvent.click(button);
    await screen.findByRole('dialog');
    expect(getRecordPhotoBlobMock).toHaveBeenCalledTimes(1);
  });

  it('9. a WebP Blob opens fine', async () => {
    const button = await renderLoaded('image/webp');
    fireEvent.click(button);
    expect(await screen.findByRole('dialog')).toBeTruthy();
  });

  it('10. a JPG Blob opens fine', async () => {
    const button = await renderLoaded('image/jpeg');
    fireEvent.click(button);
    expect(await screen.findByRole('dialog')).toBeTruthy();
  });

  it('11. a PNG Blob opens fine', async () => {
    const button = await renderLoaded('image/png');
    fireEvent.click(button);
    expect(await screen.findByRole('dialog')).toBeTruthy();
  });
});

describe('AuthenticatedPhoto — 12-15: close behavior', () => {
  async function openLightbox() {
    getRecordPhotoBlobMock.mockResolvedValue(new Blob(['x'], { type: 'image/jpeg' }));
    stubCreateObjectURL();
    render(<AuthenticatedPhoto recordId="rec-1" photoUrl={PHOTO_URL_A} alt="p" />);
    const button = await screen.findByRole('button', { name: 'เปิดดูภาพถ่ายแปลงขนาดใหญ่' });
    fireEvent.click(button);
    const dialog = await screen.findByRole('dialog');
    return { button, dialog };
  }

  it('12. clicking the X button closes the dialog', async () => {
    await openLightbox();
    fireEvent.click(screen.getByRole('button', { name: 'ปิดภาพขนาดใหญ่' }));
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
  });

  it('13. pressing Escape closes the dialog', async () => {
    await openLightbox();
    fireEvent.keyDown(document, { key: 'Escape' });
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
  });

  it('14. clicking the backdrop (outside the image) closes the dialog', async () => {
    const { dialog } = await openLightbox();
    const backdrop = dialog.parentElement!;
    fireEvent.click(backdrop);
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
  });

  it('15. clicking the image itself does NOT close the dialog', async () => {
    const { dialog } = await openLightbox();
    const dialogImg = dialog.querySelector('img')!;
    fireEvent.click(dialogImg);
    expect(screen.queryByRole('dialog')).toBeTruthy();
  });
});

describe('AuthenticatedPhoto — 16-18: focus management', () => {
  async function openLightbox() {
    getRecordPhotoBlobMock.mockResolvedValue(new Blob(['x'], { type: 'image/jpeg' }));
    stubCreateObjectURL();
    render(<AuthenticatedPhoto recordId="rec-1" photoUrl={PHOTO_URL_A} alt="p" />);
    const button = await screen.findByRole('button', { name: 'เปิดดูภาพถ่ายแปลงขนาดใหญ่' });
    fireEvent.click(button);
    await screen.findByRole('dialog');
    return button;
  }

  it('16. focus moves to the close button when the dialog opens', async () => {
    await openLightbox();
    const closeBtn = screen.getByRole('button', { name: 'ปิดภาพขนาดใหญ่' });
    expect(document.activeElement).toBe(closeBtn);
  });

  it('17. focus returns to the thumbnail once the dialog closes', async () => {
    const button = await openLightbox();
    fireEvent.click(screen.getByRole('button', { name: 'ปิดภาพขนาดใหญ่' }));
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
    expect(document.activeElement).toBe(button);
  });

  it('18. Tab does not move focus outside the dialog (only one control exists)', async () => {
    await openLightbox();
    const closeBtn = screen.getByRole('button', { name: 'ปิดภาพขนาดใหญ่' });
    fireEvent.keyDown(document, { key: 'Tab' });
    expect(document.activeElement).toBe(closeBtn);
    fireEvent.keyDown(document, { key: 'Tab', shiftKey: true });
    expect(document.activeElement).toBe(closeBtn);
  });
});

describe('AuthenticatedPhoto — 19-21: body scroll lock', () => {
  it('19. body overflow is set to hidden while the dialog is open', async () => {
    getRecordPhotoBlobMock.mockResolvedValue(new Blob(['x'], { type: 'image/jpeg' }));
    stubCreateObjectURL();
    render(<AuthenticatedPhoto recordId="rec-1" photoUrl={PHOTO_URL_A} alt="p" />);
    fireEvent.click(await screen.findByRole('button', { name: 'เปิดดูภาพถ่ายแปลงขนาดใหญ่' }));
    await screen.findByRole('dialog');

    expect(document.body.style.overflow).toBe('hidden');
  });

  it('20. body overflow is restored once the dialog closes', async () => {
    getRecordPhotoBlobMock.mockResolvedValue(new Blob(['x'], { type: 'image/jpeg' }));
    stubCreateObjectURL();
    render(<AuthenticatedPhoto recordId="rec-1" photoUrl={PHOTO_URL_A} alt="p" />);
    fireEvent.click(await screen.findByRole('button', { name: 'เปิดดูภาพถ่ายแปลงขนาดใหญ่' }));
    await screen.findByRole('dialog');

    fireEvent.keyDown(document, { key: 'Escape' });
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
    expect(document.body.style.overflow).toBe('');
  });

  it('21. body overflow is restored on unmount while the dialog is still open', async () => {
    getRecordPhotoBlobMock.mockResolvedValue(new Blob(['x'], { type: 'image/jpeg' }));
    stubCreateObjectURL();
    const { unmount } = render(<AuthenticatedPhoto recordId="rec-1" photoUrl={PHOTO_URL_A} alt="p" />);
    fireEvent.click(await screen.findByRole('button', { name: 'เปิดดูภาพถ่ายแปลงขนาดใหญ่' }));
    await screen.findByRole('dialog');
    expect(document.body.style.overflow).toBe('hidden');

    expect(() => unmount()).not.toThrow();
    expect(document.body.style.overflow).toBe('');
  });
});

describe('AuthenticatedPhoto — 22-26: object URL lifecycle', () => {
  it('22. opening and closing the dialog never revokes the object URL', async () => {
    getRecordPhotoBlobMock.mockResolvedValue(new Blob(['x'], { type: 'image/jpeg' }));
    stubCreateObjectURL();
    const revokeSpy = vi.spyOn(URL, 'revokeObjectURL');
    render(<AuthenticatedPhoto recordId="rec-1" photoUrl={PHOTO_URL_A} alt="p" />);

    const button = await screen.findByRole('button', { name: 'เปิดดูภาพถ่ายแปลงขนาดใหญ่' });
    fireEvent.click(button);
    await screen.findByRole('dialog');
    fireEvent.keyDown(document, { key: 'Escape' });
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());

    expect(revokeSpy).not.toHaveBeenCalled();
  });

  it('23. changing photoUrl/recordId revokes the previous object URL', async () => {
    getRecordPhotoBlobMock.mockResolvedValue(new Blob(['x'], { type: 'image/jpeg' }));
    stubCreateObjectURL();
    const revokeSpy = vi.spyOn(URL, 'revokeObjectURL');

    const { rerender } = render(
      <AuthenticatedPhoto recordId="rec-1" photoUrl={PHOTO_URL_A} alt="p1" />,
    );
    await screen.findByAltText('p1');

    rerender(<AuthenticatedPhoto recordId="rec-1" photoUrl={PHOTO_URL_B} alt="p2" />);
    await waitFor(() => expect(revokeSpy).toHaveBeenCalledWith('blob:fake-url-1'));
    await screen.findByAltText('p2');
  });

  it('24. unmounting revokes the object URL', async () => {
    getRecordPhotoBlobMock.mockResolvedValue(new Blob(['x'], { type: 'image/jpeg' }));
    stubCreateObjectURL();
    const revokeSpy = vi.spyOn(URL, 'revokeObjectURL');

    const { unmount } = render(<AuthenticatedPhoto recordId="rec-1" photoUrl={PHOTO_URL_A} alt="p" />);
    await screen.findByAltText('p');
    unmount();
    expect(revokeSpy).toHaveBeenCalledWith('blob:fake-url-1');
  });

  it('25. changing props while the dialog is open closes it immediately', async () => {
    getRecordPhotoBlobMock.mockResolvedValue(new Blob(['x'], { type: 'image/jpeg' }));
    stubCreateObjectURL();
    const { rerender } = render(<AuthenticatedPhoto recordId="rec-1" photoUrl={PHOTO_URL_A} alt="p1" />);
    fireEvent.click(await screen.findByRole('button', { name: 'เปิดดูภาพถ่ายแปลงขนาดใหญ่' }));
    await screen.findByRole('dialog');

    rerender(<AuthenticatedPhoto recordId="rec-1" photoUrl={PHOTO_URL_B} alt="p2" />);
    expect(screen.queryByRole('dialog')).toBeNull();
    // body scroll must not stay locked behind the now-closed dialog either.
    expect(document.body.style.overflow).toBe('');
  });

  it('26. a slow-resolving stale request never overwrites the newer photo', async () => {
    const first = deferred<Blob>();
    const second = deferred<Blob>();
    getRecordPhotoBlobMock.mockReturnValueOnce(first.promise).mockReturnValueOnce(second.promise);
    stubCreateObjectURL();

    const { rerender } = render(<AuthenticatedPhoto recordId="rec-1" photoUrl={PHOTO_URL_A} alt="p1" />);
    rerender(<AuthenticatedPhoto recordId="rec-1" photoUrl={PHOTO_URL_B} alt="p2" />);

    // The SECOND (current) request resolves first; the stale FIRST request
    // resolves late — its result must never appear.
    second.resolve(new Blob(['y'], { type: 'image/png' }));
    await screen.findByAltText('p2');
    first.resolve(new Blob(['x'], { type: 'image/jpeg' }));

    await new Promise((r) => setTimeout(r, 0));
    expect(screen.queryByAltText('p1')).toBeNull();
    expect(screen.getByAltText('p2')).toBeTruthy();
  });
});

describe('AuthenticatedPhoto — 27: existing thumbnail sizing is preserved', () => {
  it('applies the passed className to the thumbnail button (e.g. PlotDetail history 16×16)', async () => {
    getRecordPhotoBlobMock.mockResolvedValue(new Blob(['x'], { type: 'image/jpeg' }));
    stubCreateObjectURL();
    render(
      <AuthenticatedPhoto recordId="rec-1" photoUrl={PHOTO_URL_A} alt="p"
        className="h-16 w-16 rounded-md object-cover shadow-sm" />,
    );

    const button = await screen.findByRole('button', { name: 'เปิดดูภาพถ่ายแปลงขนาดใหญ่' });
    expect(button.className).toContain('h-16');
    expect(button.className).toContain('w-16');
  });

  it('falls back to the default 24×24 sizing when no className is passed', async () => {
    getRecordPhotoBlobMock.mockResolvedValue(new Blob(['x'], { type: 'image/jpeg' }));
    stubCreateObjectURL();
    render(<AuthenticatedPhoto recordId="rec-1" photoUrl={PHOTO_URL_A} alt="p" />);

    const button = await screen.findByRole('button', { name: 'เปิดดูภาพถ่ายแปลงขนาดใหญ่' });
    expect(button.className).toContain('h-24');
    expect(button.className).toContain('w-24');
  });
});

describe('AuthenticatedPhoto — round 8-17B Part D: viewport-gated fetch', () => {
  class FakeIntersectionObserver {
    static instances: FakeIntersectionObserver[] = [];
    callback: IntersectionObserverCallback;
    constructor(callback: IntersectionObserverCallback) {
      this.callback = callback;
      FakeIntersectionObserver.instances.push(this);
    }
    observe() {}
    unobserve() {}
    disconnect() {}
  }

  let originalIO: typeof IntersectionObserver | undefined;

  beforeEach(() => {
    originalIO = globalThis.IntersectionObserver;
    FakeIntersectionObserver.instances = [];
    globalThis.IntersectionObserver = FakeIntersectionObserver as unknown as typeof IntersectionObserver;
  });

  afterEach(() => {
    globalThis.IntersectionObserver = originalIO as typeof IntersectionObserver;
  });

  it('does not fetch the blob until the placeholder intersects the viewport', async () => {
    getRecordPhotoBlobMock.mockResolvedValue(new Blob(['x'], { type: 'image/jpeg' }));
    stubCreateObjectURL();
    render(<AuthenticatedPhoto recordId="rec-1" photoUrl={PHOTO_URL_A} alt="p" />);

    await new Promise((r) => setTimeout(r, 0));
    expect(getRecordPhotoBlobMock).not.toHaveBeenCalled();
  });

  it('fetches the blob once the placeholder is reported as intersecting', async () => {
    getRecordPhotoBlobMock.mockResolvedValue(new Blob(['x'], { type: 'image/jpeg' }));
    stubCreateObjectURL();
    render(<AuthenticatedPhoto recordId="rec-1" photoUrl={PHOTO_URL_A} alt="p" />);

    const observer = FakeIntersectionObserver.instances[0];
    observer.callback(
      [{ isIntersecting: true } as IntersectionObserverEntry],
      observer as unknown as IntersectionObserver,
    );

    await waitFor(() =>
      expect(getRecordPhotoBlobMock).toHaveBeenCalledWith('rec-1', `${'a'.repeat(32)}.jpg`),
    );
    await screen.findByAltText('p');
  });

  it('falls back to fetching immediately when the browser has no IntersectionObserver', async () => {
    globalThis.IntersectionObserver = undefined as unknown as typeof IntersectionObserver;
    getRecordPhotoBlobMock.mockResolvedValue(new Blob(['x'], { type: 'image/jpeg' }));
    stubCreateObjectURL();
    render(<AuthenticatedPhoto recordId="rec-1" photoUrl={PHOTO_URL_A} alt="p" />);

    await waitFor(() => expect(getRecordPhotoBlobMock).toHaveBeenCalled());
  });
});

describe('AuthenticatedPhoto — 28: no raw identifiers ever reach the dialog', () => {
  it('the dialog never renders the raw recordId, photoUrl, or filename', async () => {
    const recordId = 'super-secret-record-id-999';
    const filename = `${'c'.repeat(32)}.webp`;
    getRecordPhotoBlobMock.mockResolvedValue(new Blob(['x'], { type: 'image/webp' }));
    stubCreateObjectURL();
    render(<AuthenticatedPhoto recordId={recordId} photoUrl={`/media/inspection-photos/${filename}`} alt="p" />);

    fireEvent.click(await screen.findByRole('button', { name: 'เปิดดูภาพถ่ายแปลงขนาดใหญ่' }));
    const dialog = await screen.findByRole('dialog');

    expect(dialog.outerHTML).not.toContain(recordId);
    expect(dialog.outerHTML).not.toContain(filename);
    expect(dialog.outerHTML).not.toContain('/media/inspection-photos/');
  });
});
