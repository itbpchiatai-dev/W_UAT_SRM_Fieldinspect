/**
 * PublicMasterDataButtons — like MasterDataButtons, but for the
 * unauthenticated /public/inspect flow (round 19.1). Uses the public,
 * login-free master-data endpoint (GET /api/v1/public/masterdata) instead
 * of the authenticated one api/masterdata.ts calls — same reasoning
 * api/publicInspection.ts documents for using its own axios instance
 * rather than apiClient.
 *
 * A failed load never blocks the rest of the form: crop/variety/
 * growthStage/weatherCondition are all optional fields in the public
 * payload, so this shows an inline Thai error and lets the field worker
 * continue without that one value rather than taking down the page.
 *
 * Round 8-4H — `offlineItems`: an optional caller-supplied list (sourced from
 * the persisted offline public-access cache, see lib/offline-inspection-store
 * OfflinePublicAccessCacheV1) that, when provided, is used INSTEAD of the
 * live query — the query itself is skipped entirely (`enabled: false`) so it
 * never attempts a doomed network call while genuinely offline. Omitted
 * (undefined) by every existing caller, so the online-only behavior above is
 * completely unchanged unless a caller opts in.
 */
import { useQuery } from '@tanstack/react-query';
import { listPublicMasterData, type PublicMasterDataType, type PublicMasterDataItem } from '../../api/publicMasterdata';
import { OptionButtons } from './OptionButtons';

interface Props {
  type: PublicMasterDataType;
  value: string | null;
  onChange: (value: string | null) => void;
  parent?: string | null;
  disabled?: boolean;
  /** Multi-select mode — see OptionButtons: `value` is a ", "-joined list. */
  multiple?: boolean;
  /** Round 8-4H — offline-cache override, see module docstring. */
  offlineItems?: PublicMasterDataItem[];
}

export function PublicMasterDataButtons({ type, value, onChange, parent, disabled, multiple, offlineItems }: Props) {
  const usingOffline = offlineItems !== undefined;
  const { data: liveItems = [], isLoading, isError } = useQuery({
    queryKey: ['public-masterdata', type, parent ?? null],
    queryFn: () => listPublicMasterData({ type, parent: parent ?? undefined }),
    enabled: !usingOffline,
  });
  const items = usingOffline ? offlineItems : liveItems;

  if (!usingOffline && isError) {
    return <p className="text-xs text-red-600">โหลดตัวเลือกไม่สำเร็จ — ลองใหม่ภายหลัง (ข้ามช่องนี้ไปก่อนได้)</p>;
  }

  return (
    <OptionButtons
      options={items.map((i) => i.value)}
      value={value}
      onChange={onChange}
      disabled={disabled}
      loading={!usingOffline && isLoading}
      multiple={multiple}
    />
  );
}
