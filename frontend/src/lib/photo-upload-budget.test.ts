import { describe, it, expect } from 'vitest';
import { AxiosError } from 'axios';
import {
  ASSUMED_UPLINK_BYTES_PER_SEC,
  LARGE_UPLOAD_WARNING_BYTES,
  PHOTO_UPLOAD_TIMEOUT_MS,
  UPLOAD_TIMEOUT_MESSAGE,
  describeUploadDuration,
  describeUploadSize,
  estimateUploadSeconds,
  isUploadTimeout,
  largeUploadWarning,
  totalUploadBytes,
  uploadPercent,
} from './photo-upload-budget';

/** A File whose `size` is whatever the test needs — the real constructor
 * derives size from the contents, and allocating megabytes per case would be
 * pure waste when only `.size` is ever read. */
function fileOf(bytes: number): File {
  const file = new File([''], 'p.webp', { type: 'image/webp' });
  Object.defineProperty(file, 'size', { value: bytes });
  return file;
}

const MB = 1024 * 1024;

describe('PHOTO_UPLOAD_TIMEOUT_MS', () => {
  // The regression this round exists for: both axios instances default to
  // 15_000, which aborted a multipart photo upload mid-flight and reported it
  // as a network failure.
  it('is far longer than the 15s default both clients carry', () => {
    expect(PHOTO_UPLOAD_TIMEOUT_MS).toBeGreaterThan(15_000);
  });

  it('covers the realistic bad case — 15MB at 1 Mbps', () => {
    const secondsAt1Mbps = (15 * MB) / (1_000_000 / 8);
    expect(PHOTO_UPLOAD_TIMEOUT_MS / 1000).toBeGreaterThanOrEqual(secondsAt1Mbps);
  });

  it('stays finite, so a dead connection still hands the form back', () => {
    expect(Number.isFinite(PHOTO_UPLOAD_TIMEOUT_MS)).toBe(true);
    expect(PHOTO_UPLOAD_TIMEOUT_MS).toBeLessThanOrEqual(300_000);
  });
});

describe('totalUploadBytes', () => {
  it('sums the chosen files and skips empty slots', () => {
    expect(totalUploadBytes([fileOf(3 * MB), null, fileOf(2 * MB), null, null])).toBe(5 * MB);
  });

  it('is 0 when nothing is chosen', () => {
    expect(totalUploadBytes([null, null])).toBe(0);
  });
});

describe('estimateUploadSeconds', () => {
  it('is 0 for an empty payload', () => {
    expect(estimateUploadSeconds(0)).toBe(0);
  });

  it('never rounds a real payload down to 0 seconds', () => {
    expect(estimateUploadSeconds(1)).toBe(1);
  });

  it('scales with the assumed uplink', () => {
    expect(estimateUploadSeconds(ASSUMED_UPLINK_BYTES_PER_SEC * 10)).toBe(10);
  });
});

describe('describeUploadDuration', () => {
  it('speaks seconds below 90', () => {
    expect(describeUploadDuration(45)).toBe('ประมาณ 45 วินาที');
  });

  it('switches to minutes at 90, where seconds stop sounding like an estimate', () => {
    expect(describeUploadDuration(90)).toBe('ประมาณ 2 นาที');
  });
});

describe('describeUploadSize', () => {
  it('reports megabytes to one decimal', () => {
    expect(describeUploadSize(Math.round(2.5 * MB))).toBe('2.5 MB');
  });
});

describe('largeUploadWarning', () => {
  it('says nothing about an ordinary submit', () => {
    expect(largeUploadWarning([fileOf(1 * MB), fileOf(1 * MB), null])).toBeNull();
  });

  it('stays silent right below the threshold', () => {
    expect(largeUploadWarning([fileOf(LARGE_UPLOAD_WARNING_BYTES - 1)])).toBeNull();
  });

  it('warns at the threshold, naming the count, the size and the time', () => {
    const warning = largeUploadWarning([fileOf(5 * MB), fileOf(5 * MB), null]);
    expect(warning).toContain('รูป 2 ใบ');
    expect(warning).toContain('10.0 MB');
    expect(warning).toContain('ประมาณ');
    // The actionable half: the page must stay open.
    expect(warning).toContain('บันทึกสำเร็จ');
  });

  it('counts only the slots that hold a file', () => {
    const warning = largeUploadWarning([fileOf(9 * MB), null, null, null, null]);
    expect(warning).toContain('รูป 1 ใบ');
  });
});

describe('uploadPercent', () => {
  it('reports whole percentages', () => {
    expect(uploadPercent(50, 200)).toBe(25);
  });

  it('returns -1 when the body length is unmeasurable, rather than inventing one', () => {
    expect(uploadPercent(10, undefined)).toBe(-1);
    expect(uploadPercent(10, 0)).toBe(-1);
  });

  it('clamps overshoot from transport overhead', () => {
    expect(uploadPercent(220, 200)).toBe(100);
  });
});

describe('isUploadTimeout', () => {
  // The whole point: these two are indistinguishable without the code, and
  // conflating them told a user on full signal that their network had failed.
  it('is true for an axios timeout', () => {
    const err = new AxiosError('timeout of 120000ms exceeded', 'ECONNABORTED');
    expect(isUploadTimeout(err)).toBe(true);
  });

  it('also accepts ETIMEDOUT, which some adapters use instead', () => {
    expect(isUploadTimeout(new AxiosError('timeout', 'ETIMEDOUT'))).toBe(true);
  });

  it('is false for a genuine network error with no code', () => {
    expect(isUploadTimeout(new AxiosError('Network Error', 'ERR_NETWORK'))).toBe(false);
  });

  it('is false once the server actually answered', () => {
    const err = new AxiosError('boom', 'ECONNABORTED');
    err.response = { status: 500, data: {}, statusText: '', headers: {}, config: {} as never };
    expect(isUploadTimeout(err)).toBe(false);
  });

  it('is false for anything that is not an axios error', () => {
    expect(isUploadTimeout(new Error('nope'))).toBe(false);
    expect(isUploadTimeout(null)).toBe(false);
  });
});

describe('UPLOAD_TIMEOUT_MESSAGE', () => {
  it('blames the size or the uplink, never "the network"', () => {
    expect(UPLOAD_TIMEOUT_MESSAGE).not.toContain('เชื่อมต่อเครือข่ายไม่สำเร็จ');
  });

  it('promises the work is kept and gives one action', () => {
    expect(UPLOAD_TIMEOUT_MESSAGE).toContain('ยังอยู่ครบ');
    expect(UPLOAD_TIMEOUT_MESSAGE).toContain('อีกครั้ง');
  });
});
