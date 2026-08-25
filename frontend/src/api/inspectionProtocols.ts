/**
 * Inspection Protocol API — the growth-stage → 4-criteria-label contract
 * (round 5.1 backend). The backend is the source of truth: which label each
 * of the 4 fixed score slots carries depends on the selected growth stage,
 * and the record forms render those labels (never a hardcoded set) from this
 * response. See backend/app/services/inspection_protocols.py.
 *
 * Two endpoints, same shape: the logged-in RecordForm reads the
 * records.read-gated one; the public /public/inspect flow reads the
 * unauthenticated sibling via publicApiClient (no login token attached).
 */
import { apiClient } from './client';
import { publicApiClient } from './publicInspection';

/** One of the 4 fixed score columns. `slot` matches the RecordCreate score
 * field names exactly (fieldPrepScore / weatherScore / careScore /
 * varietyResistanceScore); `label` is the stage-specific criterion text. */
export interface InspectionProtocolCriterion {
  slot: string;
  label: string;
}

export interface InspectionProtocolStage {
  growthStage: string;
  criteria: InspectionProtocolCriterion[];
}

export interface InspectionProtocolResponse {
  version: number;
  stages: InspectionProtocolStage[];
}

export const fetchInspectionProtocols = () =>
  apiClient
    .get<InspectionProtocolResponse>('/api/v1/inspection-protocols')
    .then((r) => r.data);

export const fetchPublicInspectionProtocols = () =>
  publicApiClient
    .get<InspectionProtocolResponse>('/api/v1/public/inspection-protocols')
    .then((r) => r.data);

/**
 * The protocol for a growth stage, or null when there's no stage or the
 * stage has no protocol (e.g. the supplement master-data stages) — the same
 * gated contract the backend enforces: a stage without a protocol imposes no
 * score requirement.
 */
export function findProtocolForStage(
  protocols: InspectionProtocolResponse | undefined,
  growthStage: string | null | undefined,
): InspectionProtocolStage | null {
  if (!protocols || !growthStage) return null;
  return protocols.stages.find((s) => s.growthStage === growthStage) ?? null;
}

// --- Admin editor (round 5.5) ------------------------------------------------
// The admin config carries the row id (+ order/active) so a label can be
// PATCHed. Gated by master-data permissions on the backend
// (/api/v1/admin/inspection-protocols).

export interface InspectionProtocolAdminCriterion {
  id: string;
  growthStage: string;
  slot: string;
  label: string;
  orderIndex: number;
  active: boolean;
}

export interface InspectionProtocolAdminStage {
  growthStage: string;
  criteria: InspectionProtocolAdminCriterion[];
}

export interface InspectionProtocolAdminResponse {
  version: number;
  stages: InspectionProtocolAdminStage[];
}

export const fetchAdminInspectionProtocols = () =>
  apiClient
    .get<InspectionProtocolAdminResponse>('/api/v1/admin/inspection-protocols')
    .then((r) => r.data);

export const updateInspectionProtocolCriterion = (id: string, label: string) =>
  apiClient
    .patch<InspectionProtocolAdminCriterion>(
      `/api/v1/admin/inspection-protocols/criteria/${id}`,
      { label },
    )
    .then((r) => r.data);

/** Atomic multi-label edit (round 5.6) — one transaction on the backend, so
 * a stage's labels never partially save. */
export const bulkUpdateInspectionProtocolCriteria = (
  items: { id: string; label: string }[],
) =>
  apiClient
    .patch<InspectionProtocolAdminCriterion[]>(
      '/api/v1/admin/inspection-protocols/criteria',
      { items },
    )
    .then((r) => r.data);
