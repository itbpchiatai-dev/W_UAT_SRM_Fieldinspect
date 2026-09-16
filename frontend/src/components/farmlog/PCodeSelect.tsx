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

const inputCls =
  'w-full rounded-md border border-gray-300 px-3 py-2 text-sm shadow-sm focus:border-green-500 focus:outline-none focus:ring-1 focus:ring-green-500 disabled:bg-gray-50 disabled:text-gray-500';

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
  const options = (pCodesQuery.data ?? []).filter((p) => p.parent && varietyOf.has(p.parent));
  const loading = varietiesQuery.isLoading || pCodesQuery.isLoading;
  const empty = !!crop && !loading && options.length === 0;

  return (
    <>
      <select
        className={inputCls}
        // The wrapping <label> carries no htmlFor, so this is the field's only
        // accessible name.
        aria-label="เลือก P.Code"
        value={value ?? ''}
        disabled={disabled || !crop || loading}
        onChange={(e) => {
          const picked = e.target.value || null;
          const match = options.find((o) => o.value === picked);
          onChange(picked, match?.parent ?? null);
        }}
      >
        <option value="">{crop ? '— เลือก P.Code —' : '— เลือกชนิดพืชก่อน —'}</option>
        {/* A stored value that is no longer on offer (deactivated since the
            cycle was created) stays visible and marked, never silently
            blank — the same rule MasterDataSelect follows. */}
        {value && !options.some((o) => o.value === value) && (
          <option value={value} disabled>{value} (ปิดใช้งาน/ค่าเดิม)</option>
        )}
        {options.map((o) => (
          <option key={o.id} value={o.value}>{`${o.value} — ${o.parent}`}</option>
        ))}
      </select>
      {empty && (
        <p className="text-xs text-destructive">
          ชนิดพืชนี้ยังไม่มี P.Code — กรุณาเพิ่มที่เมนู Master Data ก่อน
        </p>
      )}
    </>
  );
}
