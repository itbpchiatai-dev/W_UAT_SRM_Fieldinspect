import { describe, it, expect, vi } from 'vitest';
import { fetchAllPages } from './paginate';

function pagedSource<T>(items: T[]) {
  return async (offset: number, pageSize: number) => items.slice(offset, offset + pageSize);
}

describe('fetchAllPages', () => {
  it('returns everything when it all fits in one page', async () => {
    const result = await fetchAllPages(pagedSource([1, 2, 3]), 10);
    expect(result).toEqual([1, 2, 3]);
  });

  it('loops across multiple pages until a short page is returned', async () => {
    const items = Array.from({ length: 5 }, (_, i) => i); // [0..4]
    const fetchPage = vi.fn(pagedSource(items));
    const result = await fetchAllPages(fetchPage, 2);
    expect(result).toEqual(items);
    // pages of 2,2,1 -> 3 calls
    expect(fetchPage).toHaveBeenCalledTimes(3);
    expect(fetchPage).toHaveBeenNthCalledWith(1, 0, 2);
    expect(fetchPage).toHaveBeenNthCalledWith(2, 2, 2);
    expect(fetchPage).toHaveBeenNthCalledWith(3, 4, 2);
  });

  it('stops after an exact-multiple final page returns empty next call', async () => {
    const items = [1, 2, 3, 4]; // exactly 2 pages of size 2
    const fetchPage = vi.fn(pagedSource(items));
    const result = await fetchAllPages(fetchPage, 2);
    expect(result).toEqual(items);
    // page of 2, page of 2 (full, must probe again), page of 0 (stop) -> 3 calls
    expect(fetchPage).toHaveBeenCalledTimes(3);
  });

  it('returns an empty array when the source is empty', async () => {
    const result = await fetchAllPages(pagedSource<number>([]), 50);
    expect(result).toEqual([]);
  });

  it('respects the maxPages safety cap', async () => {
    // A page fetcher that always returns a full page (misbehaving/infinite backend).
    const fetchPage = vi.fn(async (_offset: number, pageSize: number) =>
      Array.from({ length: pageSize }, () => 'x'),
    );
    const result = await fetchAllPages(fetchPage, 10, 3);
    expect(fetchPage).toHaveBeenCalledTimes(3);
    expect(result).toHaveLength(30);
  });
});
