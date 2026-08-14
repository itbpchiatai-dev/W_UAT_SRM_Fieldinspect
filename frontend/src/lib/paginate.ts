/**
 * paginate — generic offset/limit page-fetch loop.
 * Keeps calling `fetchPage(offset, pageSize)` and accumulating results until
 * a page comes back shorter than `pageSize` (no more data) or `maxPages` is
 * hit (safety cap against an unexpected infinite loop).
 */
export async function fetchAllPages<T>(
  fetchPage: (offset: number, pageSize: number) => Promise<T[]>,
  pageSize: number,
  maxPages = 50,
): Promise<T[]> {
  const all: T[] = [];
  let offset = 0;
  for (let i = 0; i < maxPages; i++) {
    const page = await fetchPage(offset, pageSize);
    all.push(...page);
    if (page.length < pageSize) break;
    offset += pageSize;
  }
  return all;
}
