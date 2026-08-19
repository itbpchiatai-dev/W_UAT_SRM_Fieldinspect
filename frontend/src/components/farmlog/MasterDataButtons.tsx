/**
 * MasterDataButtons (Step 12.5 tap-UI) — OptionButtons backed by master_data.
 * Loads options for a `type` (and optional `parent`, e.g. variety by crop) and
 * renders them as tap chips. Drop-in replacement for MasterDataSelect in the form.
 */
import { useQuery } from '@tanstack/react-query';
import { listMasterData, masterDataQueryKey } from '../../api/masterdata';
import { OptionButtons } from './OptionButtons';

interface Props {
  type: string;
  value: string | null;
  onChange: (value: string | null) => void;
  parent?: string | null;
  disabled?: boolean;
  /** Multi-select mode — see OptionButtons: `value` is a ", "-joined list. */
  multiple?: boolean;
}

export function MasterDataButtons({ type, value, onChange, parent, disabled, multiple }: Props) {
  const { data: items = [], isLoading } = useQuery({
    queryKey: masterDataQueryKey(type, parent, true),
    queryFn: () => listMasterData({ type, parent: parent ?? undefined, activeOnly: true }),
  });

  return (
    <OptionButtons
      options={items.map(i => i.value)}
      value={value}
      onChange={onChange}
      disabled={disabled}
      loading={isLoading}
      multiple={multiple}
    />
  );
}
