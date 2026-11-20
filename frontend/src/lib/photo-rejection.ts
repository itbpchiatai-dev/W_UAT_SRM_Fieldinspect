import axios from 'axios';
import { PHOTO_SLOT_LABELS } from '../components/farmlog/PhotoSlotPicker';

/**
 * photo-rejection — round 59. Says WHICH photo the server refused, in Thai,
 * instead of the generic "ข้อมูลไม่ถูกต้อง กรุณาตรวจสอบและลองใหม่".
 *
 * On 2026-11-20 UAT refused one iPhone's photos 38 times in a morning. Every
 * refusal reached the field user as that generic sentence, which reads as
 * "something you typed is wrong", so they kept re-sending the same photos.
 *
 * The backend (services/inspection_photos.py, validate_and_save_photos) answers
 * every per-photo refusal with a plain string detail that starts with
 * "Photo '<filename>' ", pinned by a backend test. That string is shared with
 * the logged-in RecordForm, which shows it verbatim, so it is read here rather
 * than changed into a structured code. The filename is matched back to the
 * slot it was picked into, which is the only name the user knows it by.
 *
 * Returns null for anything else, so the caller's existing handling stands.
 */

type RejectionKind = 'empty' | 'unsupported' | 'too-large' | 'unusable';

const REJECTION_PATTERN =
  /^Photo '([\s\S]*)' (is empty|is not a supported image type|exceeds the |could not be processed: )/;

function kindOf(phrase: string): RejectionKind {
  if (phrase === 'is empty') return 'empty';
  if (phrase === 'is not a supported image type') return 'unsupported';
  if (phrase === 'exceeds the ') return 'too-large';
  return 'unusable';
}

/** The slot label for `filename`, or null when it matches no slot or more
 * than one (two photos picked from the same source can share a name). */
function slotLabelFor(filename: string, slots: readonly (File | null)[]): string | null {
  const matches = slots.flatMap((file, idx) => (file && file.name === filename ? [idx] : []));
  if (matches.length !== 1) return null;
  return PHOTO_SLOT_LABELS[matches[0]] ?? null;
}

export function describePhotoRejection(err: unknown, slots: readonly (File | null)[]): string | null {
  if (!axios.isAxiosError(err)) return null;
  const status = err.response?.status;
  if (status !== 400 && status !== 413 && status !== 422) return null;
  const detail = (err.response?.data as { detail?: unknown } | undefined)?.detail;
  if (typeof detail !== 'string') return null;
  const match = REJECTION_PATTERN.exec(detail);
  if (!match) return null;

  const label = slotLabelFor(match[1], slots);
  // The quoted label needs a space before the next word; the plain Thai
  // phrase runs straight on, as Thai does.
  const subject = label ? `รูป "${label}" ` : 'รูปที่แนบมาบางรูป';
  switch (kindOf(match[2])) {
    case 'empty':
      return `อ่าน${subject}ไม่ได้ กรุณาเลือกรูปนี้ใหม่ แล้วลองส่งอีกครั้ง`;
    case 'unsupported':
      return `${subject}ต้องเป็นไฟล์ JPG, PNG หรือ WebP กรุณาเปลี่ยนรูป`;
    case 'too-large':
      return `${subject}มีขนาดเกิน 15 MB กรุณาเปลี่ยนรูป หรือถ่ายใหม่`;
    case 'unusable':
      return `ระบบใช้${subject}ไม่ได้ กรุณาเปลี่ยนรูป หรือถ่ายใหม่ แล้วลองส่งอีกครั้ง`;
  }
}
