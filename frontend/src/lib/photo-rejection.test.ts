import { describe, expect, it } from 'vitest';
import { describePhotoRejection } from './photo-rejection';

function file(name: string): File {
  return new File(['x'], name, { type: 'image/jpeg' });
}

function rejected(status: number, detail: unknown) {
  return { isAxiosError: true, response: { status, data: { detail } } };
}

// Slot 2 is "ใบ/ลำต้น" (PHOTO_SLOT_LABELS[1]).
const SLOTS = [file('a.jpg'), file('IMG_1234.jpeg'), null, null, null];

describe('describePhotoRejection', () => {
  it('names the slot of a photo the server could not process', () => {
    const err = rejected(422, "Photo 'IMG_1234.jpeg' could not be processed: animated images are not supported");
    expect(describePhotoRejection(err, SLOTS)).toBe(
      'ระบบใช้รูป "ใบ/ลำต้น" ไม่ได้ กรุณาเปลี่ยนรูป หรือถ่ายใหม่ แล้วลองส่งอีกครั้ง',
    );
  });

  it('covers every per-photo refusal the backend sends, whatever its status', () => {
    expect(describePhotoRejection(rejected(422, "Photo 'a.jpg' is empty"), SLOTS)).toBe(
      'อ่านรูป "ภาพรวมแปลง" ไม่ได้ กรุณาเลือกรูปนี้ใหม่ แล้วลองส่งอีกครั้ง',
    );
    expect(
      describePhotoRejection(
        rejected(400, "Photo 'a.jpg' is not a supported image type (jpg/png/webp only)"),
        SLOTS,
      ),
    ).toBe('รูป "ภาพรวมแปลง" ต้องเป็นไฟล์ JPG, PNG หรือ WebP กรุณาเปลี่ยนรูป');
    expect(describePhotoRejection(rejected(413, "Photo 'a.jpg' exceeds the 15MB upload limit"), SLOTS)).toBe(
      'รูป "ภาพรวมแปลง" มีขนาดเกิน 15 MB กรุณาเปลี่ยนรูป หรือถ่ายใหม่',
    );
  });

  it('falls back to a slot-less sentence when the filename matches no slot, or two', () => {
    const unknown = rejected(422, "Photo 'other.jpg' could not be processed: malformed image");
    expect(describePhotoRejection(unknown, SLOTS)).toBe(
      'ระบบใช้รูปที่แนบมาบางรูปไม่ได้ กรุณาเปลี่ยนรูป หรือถ่ายใหม่ แล้วลองส่งอีกครั้ง',
    );

    const twins = [file('image.jpg'), file('image.jpg'), null, null, null];
    const ambiguous = rejected(422, "Photo 'image.jpg' could not be processed: malformed image");
    expect(describePhotoRejection(ambiguous, twins)).toContain('รูปที่แนบมาบางรูป');
  });

  it('copes with a filename that itself contains a quote', () => {
    const slots = [file("farmer's plot.jpg"), null, null, null, null];
    const err = rejected(422, "Photo 'farmer's plot.jpg' could not be processed: malformed image");
    expect(describePhotoRejection(err, slots)).toContain('รูป "ภาพรวมแปลง"');
  });

  it('returns null for everything that is not a photo refusal, so the caller keeps its handling', () => {
    expect(describePhotoRejection(new Error('boom'), SLOTS)).toBeNull();
    expect(describePhotoRejection(rejected(422, 'บางอย่างผิดพลาด'), SLOTS)).toBeNull();
    expect(describePhotoRejection(rejected(422, []), SLOTS)).toBeNull();
    expect(describePhotoRejection(rejected(422, { code: 'offline_draft_expired' }), SLOTS)).toBeNull();
    expect(describePhotoRejection(rejected(500, "Photo 'a.jpg' is empty"), SLOTS)).toBeNull();
    expect(describePhotoRejection({ isAxiosError: true }, SLOTS)).toBeNull();
  });
});
