/**
 * PCodeSelect (round Y) — pick a planting cycle's P.Code within a crop.
 *
 * Master Data nests crop → variety → p_code, so the P.Codes of a crop are one
 * level further down than a single `parent` filter can reach. Rather than add
 * a grandparent filter to the API for one screen, this asks the two list
 * endpoints that already exist — the crop's varieties, and the active
 * P.Codes — and joins them here; both lists are small master data the app
 * already caches under the same query keys elsewhere.
 *
 * It reports the variety alongside the P.Code because the caller shows it
 * read-only: the user picks WM + CTT-507 and sees which plant that is. The
 * SERVER derives the variety it stores independently
 * (services/master_data_validation.variety_for_p_code) — what happens here is
 * display, never the source of truth.
 */
import { useQuery } from '@tanstack/react-query';

import { listMasterData, masterDataQueryKey } from '../../api/masterdata';
import { SearchableSelect } from './SearchableSelect';

interface Props {
  /** The chosen ชนิดพืช. Null = nothing to offer yet. */
  crop: string | null;
  value: string | null;
  /** (pCode, variety) — both null when the box is cleared. */
  onChange: (pCode: string | null, variety: string | null) => void;
  disabled?: boolean;
}

export function PCodeSelect({ crop, value, onChange, disabled }: Props) {
  const varietiesQuery = useQuery({
    queryKey: masterDataQueryKey('variety', crop, true),
    queryFn: () => listMasterData({ type: 'variety', parent: crop ?? undefined, activeOnly: true }),
    enabled: !!crop,
  });
  const pCodesQuery = useQuery({
    queryKey: masterDataQueryKey('p_code', null, true),
    queryFn: () => listMasterData({ type: 'p_code', activeOnly: true }),
    enabled: !!crop,
  });

  const varietyOf = new Set((varietiesQuery.data ?? []).map((v) => v.value));
  const matching = (pCodesQuery.data ?? []).filter((p) => p.parent && varietyOf.has(p.parent));
  const loading = varietiesQuery.isLoading || pCodesQuery.isLoading;
  const empty = !!crop && !loading && matching.length === 0;

  return (
    <>
      <SearchableSelect
        label="เลือก P.Code"
        placeholder={crop ? '— เลือก P.Code —' : '— เลือกชนิดพืชก่อน —'}
        // Round Z — the label carries the variety, which is both what makes
        // the choice readable and what makes it searchable: typing part of a
        // variety name finds its P.Code.
        options={matching.map((o) => ({ value: o.value, label: `${o.value} — ${o.parent}` }))}
        value={value}
        onChange={(picked) => {
          const match = matching.find((o) => o.value === picked);
          onChange(picked, match?.parent ?? null);
        }}
        disabled={disabled || !crop || loading}
        // A P.Code deactivated since the cycle was created stays visible and
        // marked, never silently blank.
        staleLabel={value ? `${value} (ปิดใช้งาน/ค่าเดิม)` : undefined}
      />
      {empty && (
        <p className="text-xs text-destructive">
          ชนิดพืชนี้ยังไม่มี P.Code — กรุณาเพิ่มที่เมนู Master Data ก่อน
        </p>
      )}
    </>
  );
}
