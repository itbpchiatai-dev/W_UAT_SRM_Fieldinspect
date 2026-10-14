/**
 * publicMasterdata — read-only dropdown options for the unauthenticated
 * /public/inspect flow (round 19.1). Hits GET /api/v1/public/masterdata,
 * NOT the authenticated /api/v1/masterdata api/masterdata.ts calls — that
 * one requires records.read and would 401 here. Reuses publicApiClient
 * from api/publicInspection.ts (same no-auth axios instance every other
 * unauthenticated endpoint uses).
 *
 * `type` is intentionally narrowed to the backend's allowlist
 * (PublicMasterDataType in app/api/v1/public_masterdata.py) — anything
 * else 422s server-side, so there's no point letting callers pass an
 * arbitrary string here.
 */
import { publicApiClient } from './publicInspection';

// Round 37 removed 'variety'; round 39 removes 'crop'. The backend Literal no
// longer accepts either (api/v1/public_masterdata.py), so asking would 422.
// The public form only ever requests growth_stage and weather.
export type PublicMasterDataType = 'growth_stage' | 'weather';

export interface PublicMasterDataItem {
  value: string;
  parent: string | null;
}

export interface PublicMasterDataListParams {
  type: PublicMasterDataType;
  parent?: string;
}

export const listPublicMasterData = (params: PublicMasterDataListParams) =>
  publicApiClient
    .get<PublicMasterDataItem[]>('/api/v1/public/masterdata', { params })
    .then((r) => r.data);
