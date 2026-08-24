import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import {
  PhotoSlotPicker,
  PHOTO_SLOT_LABELS,
  MAX_PHOTO_COUNT,
  ALLOWED_PHOTO_MIME_TYPES,
  isAllowedPhotoFile,
  countSelectedPhotos,
  emptyPhotoSlots,
} from './PhotoSlotPicker';
import {
  InspectionPhotoPreparationError,
  UNSUPPORTED_TYPE_MESSAGE,
  ENCODER_UNSUPPORTED_WARNING,
  type PreparedInspectionPhoto,
} from '../../lib/inspection-photo-compression';

const prepareMock = vi.fn();

vi.mock('../../lib/inspection-photo-compression', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../lib/inspection-photo-compression')>();
  return { ...actual, prepareInspectionPhoto: (...args: unknown[]) => prepareMock(...args) };
});

function jpegFile(name = 'a.jpg'): File {
  return new File(['x'], name, { type: 'image/jpeg' });
}
function pdfFile(name = 'a.pdf'): File {
  return new File(['x'], name, { type: 'application/pdf' });
}

function webpResult(name: string, overrides: Partial<PreparedInspectionPhoto> = {}): PreparedInspectionPhoto {
  return {
    file: new File(['compressed'], name, { type: 'image/webp' }),
    compressed: true,
    originalBytes: 2000,
    outputBytes: 500,
    fallbackUsed: false,
    warning: null,
    ...overrides,
  };
}

/** A promise the test controls the settlement of, so it can assert on the
 * "processing" state that exists strictly between pick and settle. */
function deferred<T>() {
  let resolve!: (v: T) => void;
  let reject!: (e: unknown) => void;
  const promise = new Promise<T>((res, rej) => { resolve = res; reject = rej; });
  return { promise, resolve, reject };
}

beforeEach(() => {
  prepareMock.mockReset();
});

describe('isAllowedPhotoFile', () => {
  it('accepts jpg/png/webp', () => {
    expect(isAllowedPhotoFile(new File([''], 'a', { type: 'image/jpeg' }))).toBe(true);
    expect(isAllowedPhotoFile(new File([''], 'a', { type: 'image/png' }))).toBe(true);
    expect(isAllowedPhotoFile(new File([''], 'a', { type: 'image/webp' }))).toBe(true);
  });

  it('rejects other types', () => {
    expect(isAllowedPhotoFile(pdfFile())).toBe(false);
    expect(isAllowedPhotoFile(new File([''], 'a', { type: 'image/gif' }))).toBe(false);
  });
});

describe('countSelectedPhotos / slot layout', () => {
  it('counts only non-null slots', () => {
    expect(countSelectedPhotos(emptyPhotoSlots())).toBe(0);
    expect(countSelectedPhotos([jpegFile(), null, jpegFile(), null, null])).toBe(2);
  });

  it('has 5 slots, the last being ปัญหาอื่นๆ (photos are optional — no count validation)', () => {
    expect(MAX_PHOTO_COUNT).toBe(5);
    expect(PHOTO_SLOT_LABELS).toHaveLength(5);
    expect(PHOTO_SLOT_LABELS[4]).toBe('ปัญหาอื่นๆ');
    expect(emptyPhotoSlots()).toHaveLength(5);
  });
});

describe('PhotoSlotPicker — static rendering', () => {
  it('renders one input per slot with the allowlisted accept attribute', () => {
    render(<PhotoSlotPicker slots={emptyPhotoSlots()} onChange={() => {}} />);
    expect(screen.getAllByLabelText(/เลือกรูป/)).toHaveLength(MAX_PHOTO_COUNT);
    for (const label of PHOTO_SLOT_LABELS) {
      const input = screen.getByLabelText(`เลือกรูป ${label}`) as HTMLInputElement;
      expect(input.accept).toBe(ALLOWED_PHOTO_MIME_TYPES.join(','));
    }
  });

  // Round 8-25L — capture="environment" used to force mobile browsers
  // straight into the camera app, with no way to attach an existing photo.
  it('never sets the capture attribute — mobile must offer camera AND gallery/files, not camera-only', () => {
    render(<PhotoSlotPicker slots={emptyPhotoSlots()} onChange={() => {}} />);
    for (const label of PHOTO_SLOT_LABELS) {
      const input = screen.getByLabelText(`เลือกรูป ${label}`) as HTMLInputElement;
      expect(input.hasAttribute('capture')).toBe(false);
    }
  });

  it('labels the photos as optional (ไม่บังคับ)', () => {
    render(<PhotoSlotPicker slots={emptyPhotoSlots()} onChange={() => {}} />);
    expect(screen.getByText('รูปถ่ายแปลง (ไม่บังคับ)')).toBeTruthy();
  });

  it('shows 0/5 initially and updates the count as slots fill', () => {
    const { rerender } = render(<PhotoSlotPicker slots={emptyPhotoSlots()} onChange={() => {}} />);
    expect(screen.getByText('0/5')).toBeTruthy();

    rerender(<PhotoSlotPicker slots={[jpegFile(), null, null, null, null]} onChange={() => {}} />);
    expect(screen.getByText('1/5')).toBeTruthy();
  });

  it('removing a filled slot calls onChange with that slot cleared', () => {
    const onChange = vi.fn();
    const slots = [jpegFile(), null, null, null, null];
    render(<PhotoSlotPicker slots={slots} onChange={onChange} />);

    const removeBtn = screen.getByLabelText(`ลบรูป ${PHOTO_SLOT_LABELS[0]}`);
    fireEvent.click(removeBtn);

    expect(onChange).toHaveBeenCalledWith([null, null, null, null, null]);
  });

  it('revokes preview object URLs when a slot is cleared or the component unmounts', () => {
    const revokeSpy = vi.spyOn(URL, 'revokeObjectURL');
    const file = jpegFile();
    const { rerender, unmount } = render(<PhotoSlotPicker slots={[file, null, null, null, null]} onChange={() => {}} />);

    rerender(<PhotoSlotPicker slots={emptyPhotoSlots()} onChange={() => {}} />);
    expect(revokeSpy).toHaveBeenCalled();

    revokeSpy.mockClear();
    unmount();
    expect(() => revokeSpy).not.toThrow();
  });
});

describe('PhotoSlotPicker — 24: shows a processing state while preparing', () => {
  it('shows the Loader2 + "กำลังเตรียมรูป..." message while the async prepare is in flight', async () => {
    const d = deferred<PreparedInspectionPhoto>();
    prepareMock.mockReturnValue(d.promise);
    render(<PhotoSlotPicker slots={emptyPhotoSlots()} onChange={() => {}} />);

    const input = screen.getByLabelText(`เลือกรูป ${PHOTO_SLOT_LABELS[0]}`) as HTMLInputElement;
    fireEvent.change(input, { target: { files: [jpegFile()] } });

    expect(await screen.findByText('กำลังเตรียมรูป...')).toBeTruthy();

    d.resolve(webpResult('inspection-photo-1.webp'));
    await waitFor(() => expect(screen.queryByText('กำลังเตรียมรูป...')).toBeNull());
  });
});

describe('PhotoSlotPicker — 25: onProcessingChange transitions', () => {
  it('calls onProcessingChange(true) then onProcessingChange(false) around one prepare', async () => {
    const d = deferred<PreparedInspectionPhoto>();
    prepareMock.mockReturnValue(d.promise);
    const onProcessingChange = vi.fn();
    render(<PhotoSlotPicker slots={emptyPhotoSlots()} onChange={() => {}} onProcessingChange={onProcessingChange} />);

    // Mount itself reports false once.
    await waitFor(() => expect(onProcessingChange).toHaveBeenCalledWith(false));
    onProcessingChange.mockClear();

    fireEvent.change(screen.getByLabelText(`เลือกรูป ${PHOTO_SLOT_LABELS[0]}`), { target: { files: [jpegFile()] } });
    await waitFor(() => expect(onProcessingChange).toHaveBeenCalledWith(true));

    d.resolve(webpResult('inspection-photo-1.webp'));
    await waitFor(() => expect(onProcessingChange).toHaveBeenLastCalledWith(false));
  });
});

describe('PhotoSlotPicker — 26: onChange only fires after prepare succeeds', () => {
  it('does not call onChange while the promise is still pending', async () => {
    const d = deferred<PreparedInspectionPhoto>();
    prepareMock.mockReturnValue(d.promise);
    const onChange = vi.fn();
    render(<PhotoSlotPicker slots={emptyPhotoSlots()} onChange={onChange} />);

    fireEvent.change(screen.getByLabelText(`เลือกรูป ${PHOTO_SLOT_LABELS[0]}`), { target: { files: [jpegFile()] } });
    await screen.findByText('กำลังเตรียมรูป...');
    expect(onChange).not.toHaveBeenCalled();

    d.resolve(webpResult('inspection-photo-1.webp'));
    await waitFor(() => expect(onChange).toHaveBeenCalledOnce());
  });

  it('rejects a non-image file with the curated Thai error and never calls onChange', async () => {
    prepareMock.mockRejectedValue(new InspectionPhotoPreparationError(UNSUPPORTED_TYPE_MESSAGE));
    const onChange = vi.fn();
    render(<PhotoSlotPicker slots={emptyPhotoSlots()} onChange={onChange} />);

    fireEvent.change(screen.getByLabelText(`เลือกรูป ${PHOTO_SLOT_LABELS[0]}`), { target: { files: [pdfFile()] } });

    expect(await screen.findByText(UNSUPPORTED_TYPE_MESSAGE)).toBeTruthy();
    expect(onChange).not.toHaveBeenCalled();
  });
});

describe('PhotoSlotPicker — 27: a failed prepare never overwrites an existing photo in that slot', () => {
  it('keeps showing the old photo and the old preview after a replacement pick fails', async () => {
    const oldFile = jpegFile('old.jpg');
    prepareMock.mockRejectedValue(new InspectionPhotoPreparationError('เตรียมรูปไม่สำเร็จ'));
    const onChange = vi.fn();
    render(<PhotoSlotPicker slots={[oldFile, null, null, null, null]} onChange={onChange} />);

    fireEvent.change(screen.getByLabelText(`เลือกรูป ${PHOTO_SLOT_LABELS[0]}`), { target: { files: [jpegFile('bad.jpg')] } });

    await screen.findByText('เตรียมรูปไม่สำเร็จ');
    expect(onChange).not.toHaveBeenCalled();
    // The old photo's remove button is still there — the slot was never cleared.
    expect(screen.getByLabelText(`ลบรูป ${PHOTO_SLOT_LABELS[0]}`)).toBeTruthy();
  });
});

describe('PhotoSlotPicker — 28: a faster second pick beats a slower first pick (stale result discarded)', () => {
  it('a pick already IN FLIGHT (past the early stale-check) is still discarded once superseded before it resolves', async () => {
    const first = deferred<PreparedInspectionPhoto>();
    const second = deferred<PreparedInspectionPhoto>();
    prepareMock.mockReturnValueOnce(first.promise).mockReturnValueOnce(second.promise);
    const onChange = vi.fn();
    render(<PhotoSlotPicker slots={emptyPhotoSlots()} onChange={onChange} />);

    const input = screen.getByLabelText(`เลือกรูป ${PHOTO_SLOT_LABELS[0]}`);
    fireEvent.change(input, { target: { files: [jpegFile('A.jpg')] } });
    // Let A's queued task actually reach (and start awaiting)
    // prepareInspectionPhoto — round 8-14B.1's early stale-check runs
    // BEFORE this call, so this proves the LATE discard path (after
    // await), distinct from the early-skip covered by round 8-14B.1's
    // own "removed/unmounted before its task starts" tests below.
    await waitFor(() => expect(prepareMock).toHaveBeenCalledTimes(1));

    fireEvent.change(input, { target: { files: [jpegFile('B.jpg')] } });

    // A resolves while genuinely in flight — its result must still be
    // discarded (stale generation), even though it settles first.
    first.resolve(webpResult('inspection-photo-1.webp', { file: new File(['A'], 'A.webp', { type: 'image/webp' }) }));
    await waitFor(() => expect(prepareMock).toHaveBeenCalledTimes(2)); // B's queued task now starts
    second.resolve(webpResult('inspection-photo-1.webp', { file: new File(['B'], 'B.webp', { type: 'image/webp' }) }));

    await waitFor(() => expect(onChange).toHaveBeenCalledOnce());
    expect(onChange.mock.calls[0][0][0].name).toBe('B.webp');
  });
});

describe('PhotoSlotPicker — 29: removing during processing prevents the stale result from reappearing', () => {
  it('clears the slot immediately on remove, and the late-resolving prepare never repopulates it — no manual rerender needed', async () => {
    const oldFile = jpegFile('old.jpg');
    const d = deferred<PreparedInspectionPhoto>();
    prepareMock.mockReturnValue(d.promise);
    const onChange = vi.fn();
    render(<PhotoSlotPicker slots={[oldFile, null, null, null, null]} onChange={onChange} />);

    fireEvent.change(screen.getByLabelText(`เลือกรูป ${PHOTO_SLOT_LABELS[0]}`), { target: { files: [jpegFile('replacement.jpg')] } });
    await screen.findByText('กำลังเตรียมรูป...');

    fireEvent.click(screen.getByLabelText(`ลบรูป ${PHOTO_SLOT_LABELS[0]}`));
    expect(onChange).toHaveBeenCalledWith([null, null, null, null, null]);

    // Round 8-14B.1 — commitSlots updates slotsRef.current synchronously
    // inside remove() itself, so no rerender from the parent is needed for
    // the picker to know the slot is now empty when the stale prepare
    // eventually settles.
    onChange.mockClear();
    d.resolve(webpResult('inspection-photo-1.webp'));
    await new Promise((r) => setTimeout(r, 0));

    expect(onChange).not.toHaveBeenCalled();
  });
});

describe('PhotoSlotPicker — 30: no setState after unmount', () => {
  it('does not throw or warn when the prepare promise resolves after unmount', async () => {
    const d = deferred<PreparedInspectionPhoto>();
    prepareMock.mockReturnValue(d.promise);
    const errorSpy = vi.spyOn(console, 'error').mockImplementation(() => {});
    const { unmount } = render(<PhotoSlotPicker slots={emptyPhotoSlots()} onChange={() => {}} />);

    fireEvent.change(screen.getByLabelText(`เลือกรูป ${PHOTO_SLOT_LABELS[0]}`), { target: { files: [jpegFile()] } });
    await screen.findByText('กำลังเตรียมรูป...');
    unmount();

    d.resolve(webpResult('inspection-photo-1.webp'));
    await new Promise((r) => setTimeout(r, 0));

    const unmountedWarning = errorSpy.mock.calls.some((args) =>
      String(args[0]).includes('unmounted'));
    expect(unmountedWarning).toBe(false);
    errorSpy.mockRestore();
  });
});

describe('PhotoSlotPicker — 31: preview URL revoke (async path)', () => {
  it('revokes the old preview URL once the parent applies a successfully-prepared replacement', async () => {
    const revokeSpy = vi.spyOn(URL, 'revokeObjectURL');
    const oldFile = jpegFile('old.jpg');
    prepareMock.mockResolvedValue(webpResult('inspection-photo-1.webp'));
    const onChange = vi.fn();
    const { rerender } = render(<PhotoSlotPicker slots={[oldFile, null, null, null, null]} onChange={onChange} />);
    revokeSpy.mockClear();

    fireEvent.change(screen.getByLabelText(`เลือกรูป ${PHOTO_SLOT_LABELS[0]}`), { target: { files: [jpegFile('new.jpg')] } });
    await waitFor(() => expect(onChange).toHaveBeenCalledOnce());
    const [nextSlots] = onChange.mock.calls[0];
    rerender(<PhotoSlotPicker slots={nextSlots} onChange={onChange} />);

    expect(revokeSpy).toHaveBeenCalled();
  });
});

describe('PhotoSlotPicker — 32: slot order is preserved under concurrent picks (round 8-14B.1: no manual rerender)', () => {
  it('assigns each result to its own slot regardless of processing order — slot 0 result survives slot 2 finishing, with NO rerender from the test in between', async () => {
    const slot0 = deferred<PreparedInspectionPhoto>();
    const slot2 = deferred<PreparedInspectionPhoto>();
    prepareMock.mockImplementation((_file: File, outputName: string) => (
      outputName === 'inspection-photo-1.webp' ? slot0.promise : slot2.promise
    ));
    let currentSlots = emptyPhotoSlots();
    const onChange = vi.fn((next: (File | null)[]) => { currentSlots = next; });
    // Deliberately NOT using the returned `rerender` — this render() call is
    // the only one for the whole test. Before round 8-14B.1, slot 2's merge
    // relied on `slotsRef.current` having been refreshed by the props-sync
    // effect, which never fires without a rerender — so slot 0's committed
    // photo would have been silently dropped from slot 2's onChange call.
    render(<PhotoSlotPicker slots={currentSlots} onChange={onChange} />);

    // 1. Pick slot 0 and slot 2 back-to-back.
    fireEvent.change(screen.getByLabelText(`เลือกรูป ${PHOTO_SLOT_LABELS[0]}`), { target: { files: [jpegFile('a.jpg')] } });
    fireEvent.change(screen.getByLabelText(`เลือกรูป ${PHOTO_SLOT_LABELS[2]}`), { target: { files: [jpegFile('c.jpg')] } });

    // 2. Slot 0 succeeds (processing is serialized, so slot 2's own prepare
    // call can't even start until slot 0's queue slot clears).
    slot0.resolve(webpResult('inspection-photo-1.webp', { file: new File(['a'], 'a.webp', { type: 'image/webp' }) }));
    await waitFor(() => expect(onChange).toHaveBeenCalledOnce());

    // 3. (no rerender here, by design)

    // 4. Slot 2 succeeds.
    slot2.resolve(webpResult('inspection-photo-3.webp', { file: new File(['c'], 'c.webp', { type: 'image/webp' }) }));
    await waitFor(() => expect(onChange).toHaveBeenCalledTimes(2));

    // 5. The LAST onChange call must carry both slot 0 and slot 2.
    const [finalSlots] = onChange.mock.calls[onChange.mock.calls.length - 1];
    expect(finalSlots[0]?.name).toBe('a.webp');
    expect(finalSlots[1]).toBeNull();
    expect(finalSlots[2]?.name).toBe('c.webp');
    expect(currentSlots[0]?.name).toBe('a.webp');
    expect(currentSlots[2]?.name).toBe('c.webp');
  });
});

// --- round 8-14B.1: multi-slot merge race + stale-queue-skip hotfix --------

describe('PhotoSlotPicker — round 8-14B.1: multi-slot race hardening', () => {
  it('6. removing a queued slot before its own prepare starts skips prepareInspectionPhoto for it entirely', async () => {
    const slot0 = deferred<PreparedInspectionPhoto>();
    prepareMock.mockReturnValueOnce(slot0.promise);
    const onChange = vi.fn();
    // Slot 2 already has a photo so its remove (X) button exists while it's
    // shown as processing (mid-replacement) — see PhotoSlotPicker's own
    // "remove stays available during a replacement" design.
    render(<PhotoSlotPicker slots={[null, null, jpegFile('existing-c.jpg'), null, null]} onChange={onChange} />);

    fireEvent.change(screen.getByLabelText(`เลือกรูป ${PHOTO_SLOT_LABELS[0]}`), { target: { files: [jpegFile('a.jpg')] } });
    fireEvent.change(screen.getByLabelText(`เลือกรูป ${PHOTO_SLOT_LABELS[2]}`), { target: { files: [jpegFile('replacement-c.jpg')] } });

    // Slot 2's queued task is still waiting behind slot 0 — cancel it now.
    fireEvent.click(screen.getByLabelText(`ลบรูป ${PHOTO_SLOT_LABELS[2]}`));

    slot0.resolve(webpResult('inspection-photo-1.webp', { file: new File(['a'], 'a.webp', { type: 'image/webp' }) }));
    await waitFor(() => expect(onChange).toHaveBeenCalled());
    await new Promise((r) => setTimeout(r, 0));

    // prepareInspectionPhoto was invoked exactly once — for slot 0. Slot 2's
    // stale queued task returned before ever calling it (no wasted decode/
    // canvas/encode work on a result already known to be discarded).
    expect(prepareMock).toHaveBeenCalledTimes(1);
    expect(prepareMock).toHaveBeenCalledWith(expect.any(File), 'inspection-photo-1.webp');
  });

  it('7. unmounting before a queued slot\'s prepare starts skips prepareInspectionPhoto for it entirely', async () => {
    const slot0 = deferred<PreparedInspectionPhoto>();
    prepareMock.mockReturnValueOnce(slot0.promise);
    const { unmount } = render(<PhotoSlotPicker slots={emptyPhotoSlots()} onChange={() => {}} />);

    fireEvent.change(screen.getByLabelText(`เลือกรูป ${PHOTO_SLOT_LABELS[0]}`), { target: { files: [jpegFile('a.jpg')] } });
    fireEvent.change(screen.getByLabelText(`เลือกรูป ${PHOTO_SLOT_LABELS[2]}`), { target: { files: [jpegFile('c.jpg')] } });

    unmount();
    slot0.resolve(webpResult('inspection-photo-1.webp'));
    await new Promise((r) => setTimeout(r, 0));

    // Unmount happened synchronously, before EITHER queued task's microtask
    // had a chance to run — the early stale-check (mountedRef false) skips
    // both, so prepareInspectionPhoto is never called at all.
    expect(prepareMock).toHaveBeenCalledTimes(0);
  });

  it('8. remove() acts on the latest committed state — removing one slot never drops another slot\'s just-finished photo', async () => {
    const slot0 = deferred<PreparedInspectionPhoto>();
    prepareMock.mockReturnValueOnce(slot0.promise);
    const onChange = vi.fn();
    render(<PhotoSlotPicker slots={[null, null, jpegFile('existing-c.jpg'), null, null]} onChange={onChange} />);

    fireEvent.change(screen.getByLabelText(`เลือกรูป ${PHOTO_SLOT_LABELS[0]}`), { target: { files: [jpegFile('a.jpg')] } });
    slot0.resolve(webpResult('inspection-photo-1.webp', { file: new File(['a'], 'a.webp', { type: 'image/webp' }) }));
    await waitFor(() => expect(onChange).toHaveBeenCalledOnce());

    // No manual rerender — remove slot 2 now. If remove() read the original
    // `slots` PROP instead of slotsRef.current, this would silently wipe
    // out slot 0's photo that was only ever committed via commitSlots.
    fireEvent.click(screen.getByLabelText(`ลบรูป ${PHOTO_SLOT_LABELS[2]}`));

    const [finalSlots] = onChange.mock.calls[onChange.mock.calls.length - 1];
    expect(finalSlots[0]?.name).toBe('a.webp');
    expect(finalSlots[2]).toBeNull();
  });

  it('10. onProcessingChange returns to false once a cancelled (stale-skipped) queued task is the last thing pending', async () => {
    const slot0 = deferred<PreparedInspectionPhoto>();
    prepareMock.mockReturnValueOnce(slot0.promise);
    const onProcessingChange = vi.fn();
    render(<PhotoSlotPicker slots={[null, null, jpegFile('existing-c.jpg'), null, null]}
      onChange={() => {}} onProcessingChange={onProcessingChange} />);
    await waitFor(() => expect(onProcessingChange).toHaveBeenCalledWith(false));

    fireEvent.change(screen.getByLabelText(`เลือกรูป ${PHOTO_SLOT_LABELS[0]}`), { target: { files: [jpegFile('a.jpg')] } });
    fireEvent.change(screen.getByLabelText(`เลือกรูป ${PHOTO_SLOT_LABELS[2]}`), { target: { files: [jpegFile('replacement-c.jpg')] } });
    await waitFor(() => expect(onProcessingChange).toHaveBeenLastCalledWith(true));

    // Cancel slot 2 (its queued task hasn't started) before slot 0 resolves.
    fireEvent.click(screen.getByLabelText(`ลบรูป ${PHOTO_SLOT_LABELS[2]}`));
    slot0.resolve(webpResult('inspection-photo-1.webp'));

    await waitFor(() => expect(onProcessingChange).toHaveBeenLastCalledWith(false));
  });
});

describe('PhotoSlotPicker — 33: generic per-slot filename passed to the compressor', () => {
  it('calls prepareInspectionPhoto with inspection-photo-<slot>.webp', async () => {
    prepareMock.mockResolvedValue(webpResult('inspection-photo-5.webp'));
    render(<PhotoSlotPicker slots={emptyPhotoSlots()} onChange={() => {}} />);

    fireEvent.change(screen.getByLabelText('เลือกรูป ปัญหาอื่นๆ'), { target: { files: [jpegFile('IMG_private.jpg')] } });

    await waitFor(() => expect(prepareMock).toHaveBeenCalledWith(expect.any(File), 'inspection-photo-5.webp'));
  });
});

describe('PhotoSlotPicker — 34: non-blocking fallback warning', () => {
  it('shows the browser-cannot-compress warning but still accepts the (fallback) file via onChange', async () => {
    prepareMock.mockResolvedValue(webpResult('inspection-photo-1.webp', {
      fallbackUsed: true, compressed: false, warning: ENCODER_UNSUPPORTED_WARNING,
    }));
    const onChange = vi.fn();
    render(<PhotoSlotPicker slots={emptyPhotoSlots()} onChange={onChange} />);

    fireEvent.change(screen.getByLabelText(`เลือกรูป ${PHOTO_SLOT_LABELS[0]}`), { target: { files: [jpegFile()] } });

    expect(await screen.findByText(ENCODER_UNSUPPORTED_WARNING)).toBeTruthy();
    await waitFor(() => expect(onChange).toHaveBeenCalledOnce());
  });
});
