/**
 * masterDataQueryKey (round 8-22A) — the shared factory every masterdata
 * consumer must build its queryKey through, so an all-status query (Admin
 * Master Data) and an active-only query (MasterDataSelect/MasterDataButtons/
 * Plots' crop-variety filters) for the same type+parent can never collide
 * under the same cache key. See the factory's own docstring in
 * api/masterdata.ts for the bug this fixes.
 */
import { describe, it, expect } from 'vitest';
import { QueryClient } from '@tanstack/react-query';
import { masterDataQueryKey } from './masterdata';

describe('masterDataQueryKey', () => {
  it('all-status and active-only keys differ for the same type/parent', () => {
    const allStatus = masterDataQueryKey('crop', null, false);
    const activeOnly = masterDataQueryKey('crop', null, true);
    expect(allStatus).not.toEqual(activeOnly);
  });

  it('defaults activeOnly to false when omitted (matches the Admin page\'s all-status query)', () => {
    expect(masterDataQueryKey('crop')).toEqual(['masterdata', 'crop', null, false]);
  });

  it('normalizes an omitted parent and a null parent to the same key', () => {
    expect(masterDataQueryKey('variety', undefined, true)).toEqual(masterDataQueryKey('variety', null, true));
  });

  it('keys differ by parent (e.g. variety filtered by different crops)', () => {
    const forCropA = masterDataQueryKey('variety', 'พริก', true);
    const forCropB = masterDataQueryKey('variety', 'มะม่วง', true);
    expect(forCropA).not.toEqual(forCropB);
  });
});

describe('masterdata query-key invalidation contract', () => {
  it('invalidateQueries(["masterdata"]) still matches every activeOnly/type/parent variant', () => {
    const qc = new QueryClient();
    const keys = [
      masterDataQueryKey('crop', null, false), // Admin page (all statuses)
      masterDataQueryKey('crop', null, true), // MasterDataSelect/Plots filter (active only)
      masterDataQueryKey('variety', 'พริก', true),
      masterDataQueryKey('province'),
    ];
    for (const key of keys) qc.setQueryData(key, []);

    qc.invalidateQueries({ queryKey: ['masterdata'] });

    for (const key of keys) {
      expect(qc.getQueryState(key)?.isInvalidated).toBe(true);
    }
  });

  it('invalidation is scoped to masterdata — an unrelated key is left alone', () => {
    const qc = new QueryClient();
    qc.setQueryData(['plots', 'all'], []);
    qc.setQueryData(masterDataQueryKey('crop', null, true), []);

    qc.invalidateQueries({ queryKey: ['masterdata'] });

    expect(qc.getQueryState(['plots', 'all'])?.isInvalidated).toBeFalsy();
  });
});
