/**
 * MasterDataSelect (Step 12.5) — a <select> whose options come from the
 * editable master_data table, filtered by `type` (and optional `parent`,
 * e.g. variety filtered by the chosen crop).
 */
import { useQuery } from '@tanstack/react-query';
import { listMasterData } from '../../api/masterdata';

const inputCls =
  'w-full rounded-md border border-gray-300 px-3 py-2 text-sm shadow-sm focus:border-green-500 focus:outline-none focus:ring-1 focus:ring-green-500 disabled:bg-gray-50 disabled:text-gray-500';

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
    queryKey: ['masterdata', type, parent ?? null],
    queryFn: () => listMasterData({ type, parent: parent ?? undefined, activeOnly: true }),
  });

  return (
    <select
      className={inputCls}
      value={value ?? ''}
      disabled={disabled || isLoading}
      onChange={(e) => onChange(e.target.value || null)}
    >
      <option value="">{placeholder ?? '— เลือก —'}</option>
      {/* Keep a value that's no longer in the list visible (e.g. after edit) */}
      {value && !items.some((i) => i.value === value) && <option value={value}>{value}</option>}
      {items.map((i) => (
        <option key={i.id} value={i.value}>{i.value}</option>
      ))}
    </select>
  );
}
