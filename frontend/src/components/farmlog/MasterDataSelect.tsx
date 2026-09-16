/**
 * MasterDataSelect (Step 12.5) — a searchable chooser whose options come from
 * the editable master_data table, filtered by `type` (and optional `parent`,
 * e.g. variety filtered by the chosen crop).
 *
 * Round Z — was a plain <select>. With 77 provinces in the list that meant
 * scrolling to find one, so it now renders SearchableSelect, the same control
 * the filter bar uses. The props are unchanged: callers still pass
 * type/parent/value/onChange and get a value or null back.
 */
import { useQuery } from '@tanstack/react-query';

import { listMasterData, masterDataQueryKey } from '../../api/masterdata';
import { SearchableSelect } from './SearchableSelect';

interface Props {
  type: string;
  value: string | null;
  onChange: (value: string | null) => void;
  parent?: string | null;
  disabled?: boolean;
  placeholder?: string;
}

export function MasterDataSelect({ type, value, onChange, parent, disabled, placeholder }: Props) {
  const { data: items = [], isLoading } = useQuery({
    queryKey: masterDataQueryKey(type, parent, true),
    queryFn: () => listMasterData({ type, parent: parent ?? undefined, activeOnly: true }),
  });

  return (
    <SearchableSelect
      // The wrapping <label> carries no htmlFor, so this is the field's only
      // accessible name — and the field's own wording ("— เลือกพันธุ์ —") is
      // what a screen reader should announce; `type` is the last resort.
      label={placeholder ?? `เลือก${type}`}
      placeholder={placeholder ?? '— เลือก —'}
      options={items.map((i) => ({ value: i.value, label: i.value }))}
      value={value}
      onChange={onChange}
      disabled={disabled || isLoading}
      // A value that is no longer in the active list (deactivated in Master
      // Data after this cycle was created) stays visible and marked, so the
      // field never goes blank and looks like data loss.
      staleLabel={value ? `${value} (ปิดใช้งาน/ค่าเดิม)` : undefined}
      clearLabel={placeholder ?? '— เลือก —'}
    />
  );
}
