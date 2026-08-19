/**
 * resetUserPassword — API client contract (round 8-23B).
 *
 * Locks in the shape agreed with the backend (rounds 8-23A / 8-23A.1):
 *   POST /api/v1/users/{userId}/reset-password   body: { newPassword }
 * and the security property that matters most here — the password travels
 * in the BODY only, never in the URL (where it would land in access logs,
 * browser history, and Referer headers).
 *
 * The password constant below is an obviously-fake local test value; every
 * assertion about it is a NOT-in check proving it did not leak.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { apiClient } from './client';
import { resetUserPassword } from './users';

const USER_ID = '11111111-2222-3333-4444-555555555555';
const SECRET = 'Correct-Horse-Battery-42';

// apiClient.post is an overloaded axios method, so vi.spyOn's inferred
// signature does not fit the generic MockInstance shape — capture the
// calls through a plain typed view instead of fighting the overloads.
let postSpy: {
  mock: { calls: unknown[][] };
  mockRejectedValueOnce: (v: unknown) => void;
};

beforeEach(() => {
  postSpy = vi.spyOn(apiClient, 'post').mockResolvedValue({
    data: { status: 'ok', userId: USER_ID, authVersion: 1, sessionsInvalidated: true },
  } as never) as unknown as typeof postSpy;
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe('resetUserPassword', () => {
  it('POSTs to the exact backend path', async () => {
    await resetUserPassword(USER_ID, SECRET);
    expect(postSpy).toHaveBeenCalledTimes(1);
    expect(postSpy.mock.calls[0][0]).toBe(`/api/v1/users/${USER_ID}/reset-password`);
  });

  it('sends the password in the request BODY as newPassword', async () => {
    await resetUserPassword(USER_ID, SECRET);
    expect(postSpy.mock.calls[0][1]).toEqual({ newPassword: SECRET });
  });

  it('never puts the password in the URL', async () => {
    await resetUserPassword(USER_ID, SECRET);
    const url = String(postSpy.mock.calls[0][0]);
    expect(url).not.toContain(SECRET);
    expect(url).not.toContain('newPassword');
    expect(url).not.toContain('?');
  });

  it('sends no extra fields alongside the password', async () => {
    await resetUserPassword(USER_ID, SECRET);
    expect(Object.keys(postSpy.mock.calls[0][1] as object)).toEqual(['newPassword']);
  });

  it('returns the status-only result payload', async () => {
    const result = await resetUserPassword(USER_ID, SECRET);
    expect(result).toEqual({
      status: 'ok',
      userId: USER_ID,
      authVersion: 1,
      sessionsInvalidated: true,
    });
    // Defence in depth: nothing password-shaped comes back from the API.
    expect(JSON.stringify(result)).not.toContain(SECRET);
    expect(JSON.stringify(result)).not.toContain('$2b$');
  });

  it('never writes the password to localStorage or sessionStorage', async () => {
    const localSpy = vi.spyOn(Storage.prototype, 'setItem');
    await resetUserPassword(USER_ID, SECRET);
    const persisted = localSpy.mock.calls.map((c) => String(c[1])).join('|');
    expect(persisted).not.toContain(SECRET);
  });

  it('propagates a rejection to the caller instead of swallowing it', async () => {
    postSpy.mockRejectedValueOnce(new Error('boom'));
    await expect(resetUserPassword(USER_ID, SECRET)).rejects.toThrow();
  });
});
