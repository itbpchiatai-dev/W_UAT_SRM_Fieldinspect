/**
 * PhotoSlotPicker — 5 fixed, optional photo slots for an inspection record
 * (round 14 introduced 4 required slots; later ปัญหาอื่นๆ was added as a
 * 5th slot and the requirement was dropped — photos are now optional, any
 * subset of slots may be filled). Shared by the public field-assistant flow
 * (PublicInspect) and the logged-in create flow (RecordForm) so both submit
 * the exact same shape of `(File | null)[]` to the round-13 with-photos
 * endpoints; a zero-photo submit goes to the plain JSON create endpoints
 * instead (the multipart path requires at least one file part).
 *
 * Round 8-14B — this is now also the ONE integration point for client-side
 * pre-compression (lib/inspection-photo-compression.ts): every picked file
 * is run through `prepareInspectionPhoto` before `onChange` ever sees it, so
 * RecordForm and PublicInspect automatically share identical compression
 * behavior without either page touching the library directly. The backend
 * (app/services/inspection_photos.py) re-validates and re-normalizes
 * everything regardless — this component's validation is UX only.
 *
 * Because preparation is asynchronous (decode + canvas + encode can take a
 * real amount of time on a phone), each slot tracks its own `processing`
 * flag and a per-slot "generation" counter that invalidates any in-flight
 * result superseded by a newer pick or a removal on the same slot — so a
 * slow first pick can never clobber a faster second one, and an unmounted
 * component never calls setState. Preparation across slots is serialized
 * (one `prepareInspectionPhoto` call in flight at a time, process-wide) to
 * keep CPU/RAM bounded on a mobile device even if a user fills all 5 slots
 * in a burst. Stale queued work is skipped BEFORE the expensive decode
 * step, not just after.
 *
 * Round 8-14B.1 — every commit (a successful prepare OR a removal) goes
 * through `commitSlots`, which updates `slotsRef.current` synchronously
 * before calling `onChange`. Merging onto the `slots` PROP (or waiting for
 * the props-sync effect to catch up) is not safe here: two slots picked
 * back-to-back can both settle before React ever re-renders/flushes
 * effects between them, which would silently drop whichever one committed
 * first.
 *
 * Previews are local `URL.createObjectURL` object URLs, revoked whenever
 * the underlying file changes and on unmount, so a long inspection session
 * doesn't leak blob URLs.
 */
import { useEffect, useRef, useState } from 'react';
import { Camera, ImagePlus, Loader2, X } from 'lucide-react';

// Round 8-17B Part C — lib/inspection-photo-compression (canvas decode/
// encode work) is dynamic-imported instead of a static top-level import, so
// it never sits in PublicInspect's or RecordForm's initial chunk (both
// embed PhotoSlotPicker) — it only loads the first time a user actually
// picks a photo file. import() is cached by the module system after the
// first call, so every subsequent pick (any slot, either page) reuses the
// same already-resolved module with no repeat network fetch.
let compressionModulePromise: Promise<typeof import('../../lib/inspection-photo-compression')> | null = null;
function loadCompressionModule() {
  compressionModulePromise ??= import('../../lib/inspection-photo-compression');
  return compressionModulePromise;
}
// Duplicated from the lib's own DECODE_ERROR_MESSAGE — used ONLY when the
// dynamic import() itself fails (e.g. offline on first pick), so the
// module that normally carries this string never loaded in the first
// place; the fallback keeps the same wording the user would otherwise see
// for any other "couldn't prepare this photo" failure.
const MODULE_LOAD_FALLBACK_MESSAGE = 'ไม่สามารถเตรียมรูปภาพนี้ได้ กรุณาเลือกรูปอื่น';

export const PHOTO_SLOT_LABELS = ['ภาพรวมแปลง', 'ใบ/ลำต้น', 'ดอก/ผล', 'ปัญหา/ความเสียหาย', 'ปัญหาอื่นๆ'] as const;
export const MAX_PHOTO_COUNT = PHOTO_SLOT_LABELS.length;
export const ALLOWED_PHOTO_MIME_TYPES = ['image/jpeg', 'image/png', 'image/webp'];

export function isAllowedPhotoFile(file: File): boolean {
  return (ALLOWED_PHOTO_MIME_TYPES as readonly string[]).includes(file.type);
}

export function countSelectedPhotos(slots: readonly (File | null)[]): number {
  return slots.filter((f): f is File => f !== null).length;
}

export function emptyPhotoSlots(): (File | null)[] {
  return Array(MAX_PHOTO_COUNT).fill(null);
}

interface Props {
  slots: (File | null)[];
  onChange: (slots: (File | null)[]) => void;
  disabled?: boolean;
  /** Round 8-14B — fires whenever the aggregate "is any slot mid-prepare"
   * state changes, so a page can disable its own Submit button without
   * re-deriving per-slot state itself. */
  onProcessingChange?: (processing: boolean) => void;
}

export function PhotoSlotPicker({ slots, onChange, disabled, onProcessingChange }: Props) {
  // Round 8-25L.1 — two separate inputs per slot, not one. Dropping
  // `capture="environment"` (round 8-25L) fixed gallery/file selection but
  // then LOST the camera option outright on some mobile browsers: without
  // `capture`, whether the OS chooser sheet even offers "Camera" alongside
  // "Files/Photos" is inconsistent across browsers/devices — there is no
  // single <input> incantation that reliably guarantees BOTH everywhere.
  // Two explicit buttons, each wired to its own hidden input (one plain, one
  // capture="environment"), guarantees both choices unconditionally instead
  // of gambling on one OS sheet's contents.
  const cameraInputRefs = [
    useRef<HTMLInputElement>(null),
    useRef<HTMLInputElement>(null),
    useRef<HTMLInputElement>(null),
    useRef<HTMLInputElement>(null),
    useRef<HTMLInputElement>(null),
  ];
  const fileInputRefs = [
    useRef<HTMLInputElement>(null),
    useRef<HTMLInputElement>(null),
    useRef<HTMLInputElement>(null),
    useRef<HTMLInputElement>(null),
    useRef<HTMLInputElement>(null),
  ];
  const [previews, setPreviews] = useState<(string | null)[]>(() => Array(MAX_PHOTO_COUNT).fill(null));
  const [slotErrors, setSlotErrors] = useState<(string | null)[]>(() => Array(MAX_PHOTO_COUNT).fill(null));
  const [slotWarnings, setSlotWarnings] = useState<(string | null)[]>(() => Array(MAX_PHOTO_COUNT).fill(null));
  const [processing, setProcessing] = useState<boolean[]>(() => Array(MAX_PHOTO_COUNT).fill(false));

  // Always mirrors the latest `slots` prop — the async prepare handler below
  // must merge its result onto the CURRENT slots array, never the one
  // closed-over at the moment the file was picked (another slot may have
  // finished and called onChange in between).
  const slotsRef = useRef(slots);
  useEffect(() => { slotsRef.current = slots; }, [slots]);

  // Bumped every time a slot is (re)picked or removed — a resolved/rejected
  // prepareInspectionPhoto call whose generation no longer matches the
  // slot's current generation is stale and its result is discarded.
  const generationRef = useRef<number[]>(Array(MAX_PHOTO_COUNT).fill(0));

  const mountedRef = useRef(true);
  useEffect(() => {
    mountedRef.current = true;
    return () => { mountedRef.current = false; };
  }, []);

  // Serializes every prepareInspectionPhoto call across all 5 slots — never
  // more than one decode/canvas/encode running at once, to bound CPU/RAM on
  // a phone even when several slots are picked in a burst.
  const queueRef = useRef<Promise<void>>(Promise.resolve());

  useEffect(() => {
    onProcessingChange?.(processing.some(Boolean));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [processing]);

  useEffect(() => {
    const urls = slots.map((f) => (f ? URL.createObjectURL(f) : null));
    setPreviews(urls);
    return () => {
      urls.forEach((u) => u && URL.revokeObjectURL(u));
    };
  }, [slots]);

  function setSlot<T>(setter: React.Dispatch<React.SetStateAction<T[]>>, idx: number, value: T) {
    setter((prev) => prev.map((v, i) => (i === idx ? value : v)));
  }

  /** Round 8-14B.1 — the ONE place that both updates `slotsRef.current` and
   * notifies the parent, and it does so in that exact order, synchronously.
   * `slotsRef.current = slots` (the props-sync effect above) only runs on
   * the NEXT render's passive-effect pass — which can lag behind several
   * back-to-back queued prepares finishing microtasks apart from each other
   * (e.g. two slots picked together, both resolving before React ever gets
   * a chance to re-render/flush effects). Without this, a second slot's
   * merge could read a stale `slotsRef.current` that doesn't yet contain
   * the first slot's just-committed result, silently dropping it. Every
   * commit — from a successful prepare OR a removal — goes through this. */
  function commitSlots(nextSlots: (File | null)[]) {
    slotsRef.current = nextSlots;
    onChange(nextSlots);
  }

  function pick(idx: number, e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0];
    e.target.value = '';
    if (!file) return;

    const myGeneration = ++generationRef.current[idx];
    setSlot<string | null>(setSlotErrors, idx, null);
    setSlot<string | null>(setSlotWarnings, idx, null);
    setSlot<boolean>(setProcessing, idx, true);

    const outputName = `inspection-photo-${idx + 1}.webp`;
    queueRef.current = queueRef.current.then(async () => {
      const stale = () => !mountedRef.current || generationRef.current[idx] !== myGeneration;
      // Round 8-14B.1 — checked BEFORE the expensive decode/canvas/encode
      // work, not just after: if this slot was removed or re-picked while
      // queued behind an earlier slot's prepare, there is no point burning
      // CPU on a result that's already known to be discarded.
      if (stale()) return;

      let mod;
      try {
        mod = await loadCompressionModule();
      } catch {
        if (stale()) return;
        setSlot<boolean>(setProcessing, idx, false);
        setSlot<string | null>(setSlotErrors, idx, MODULE_LOAD_FALLBACK_MESSAGE);
        return;
      }
      if (stale()) return;

      let result;
      try {
        result = await mod.prepareInspectionPhoto(file, outputName);
      } catch (err) {
        if (stale()) return;
        setSlot<boolean>(setProcessing, idx, false);
        const message = err instanceof mod.InspectionPhotoPreparationError ? err.message : mod.DECODE_ERROR_MESSAGE;
        setSlot<string | null>(setSlotErrors, idx, message);
        return;
      }

      if (stale()) return;
      setSlot<boolean>(setProcessing, idx, false);
      if (result.warning) setSlot<string | null>(setSlotWarnings, idx, result.warning);
      commitSlots(slotsRef.current.map((v, i) => (i === idx ? result.file : v)));
    });
  }

  function remove(idx: number) {
    generationRef.current[idx] += 1;
    setSlot<boolean>(setProcessing, idx, false);
    setSlot<string | null>(setSlotErrors, idx, null);
    setSlot<string | null>(setSlotWarnings, idx, null);
    // Round 8-14B.1 — reads slotsRef.current (always current), not the
    // `slots` prop closed over at render time, which carries the same
    // stale-read risk the async merge above had.
    commitSlots(slotsRef.current.map((v, i) => (i === idx ? null : v)));
  }

  const count = countSelectedPhotos(slots);

  return (
    <div className="space-y-3">
      <div className="flex items-center justify-between">
        <span className="text-sm font-medium text-gray-700">รูปถ่ายแปลง (ไม่บังคับ)</span>
        <span className={`text-sm font-semibold ${count > 0 ? 'text-green-700' : 'text-gray-400'}`}>
          {count}/{MAX_PHOTO_COUNT}
        </span>
      </div>
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-5">
        {PHOTO_SLOT_LABELS.map((label, idx) => (
          <div key={label} className="space-y-1.5">
            <p className="text-xs font-medium text-gray-600">{label}</p>
            {previews[idx] ? (
              <div className="relative">
                <img
                  src={previews[idx]!}
                  alt={label}
                  className={`h-24 w-full rounded-md object-cover shadow-sm ${processing[idx] ? 'opacity-50' : ''}`}
                />
                {processing[idx] && (
                  <div className="absolute inset-0 flex flex-col items-center justify-center gap-1 rounded-md bg-white/60">
                    <Loader2 className="h-4 w-4 animate-spin text-green-600" />
                    <span className="text-[10px] font-medium text-gray-700">กำลังเตรียมรูป...</span>
                  </div>
                )}
                {!disabled && (
                  <button type="button" onClick={() => remove(idx)}
                    aria-label={`ลบรูป ${label}`}
                    className="absolute -right-1.5 -top-1.5 rounded-full bg-red-500 p-0.5 text-white shadow-sm hover:bg-red-600">
                    <X className="h-3 w-3" />
                  </button>
                )}
              </div>
            ) : processing[idx] ? (
              <div className="flex h-24 w-full flex-col items-center justify-center gap-1 rounded-md border border-dashed border-gray-300 text-gray-400">
                <Loader2 className="h-5 w-5 animate-spin text-green-600" />
                <span className="text-[11px]">กำลังเตรียมรูป...</span>
              </div>
            ) : (
              // Round 8-25L.1 — two explicit choices, not one combined
              // button: on mobile, "ถ่ายรูป" opens the camera directly
              // (capture="environment" on its own hidden input) while
              // "เลือกไฟล์" opens the OS's normal file/gallery picker (no
              // capture on ITS hidden input) — see the refs' comment above
              // for why this replaced the single-button, single-input,
              // OS-chooser-dependent version.
              <div className="flex h-24 w-full gap-1">
                <button type="button" disabled={disabled} onClick={() => cameraInputRefs[idx].current?.click()}
                  className="flex flex-1 flex-col items-center justify-center gap-1 rounded-md border border-dashed border-gray-300 text-gray-400 hover:border-green-400 hover:text-green-600 disabled:opacity-50">
                  <Camera className="h-5 w-5" />
                  <span className="text-[10px]">ถ่ายรูป</span>
                </button>
                <button type="button" disabled={disabled} onClick={() => fileInputRefs[idx].current?.click()}
                  className="flex flex-1 flex-col items-center justify-center gap-1 rounded-md border border-dashed border-gray-300 text-gray-400 hover:border-green-400 hover:text-green-600 disabled:opacity-50">
                  <ImagePlus className="h-5 w-5" />
                  <span className="text-[10px]">เลือกไฟล์</span>
                </button>
              </div>
            )}
            <input
              ref={cameraInputRefs[idx]}
              type="file"
              aria-label={`ถ่ายรูป ${label}`}
              accept={ALLOWED_PHOTO_MIME_TYPES.join(',')}
              capture="environment"
              className="hidden"
              onChange={(e) => pick(idx, e)}
            />
            <input
              ref={fileInputRefs[idx]}
              type="file"
              aria-label={`เลือกไฟล์ ${label}`}
              accept={ALLOWED_PHOTO_MIME_TYPES.join(',')}
              className="hidden"
              onChange={(e) => pick(idx, e)}
            />
            {slotErrors[idx] && <p className="text-[11px] text-red-600">{slotErrors[idx]}</p>}
            {!slotErrors[idx] && slotWarnings[idx] && <p className="text-[11px] text-amber-600">{slotWarnings[idx]}</p>}
          </div>
        ))}
      </div>
    </div>
  );
}
