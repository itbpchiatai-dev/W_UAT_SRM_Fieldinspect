/**
 * Records API — FarmLog field inspection records (Step 12.5: yield/list-driven)
 */
import { apiClient } from './client';

export interface RecordSummary {
  id: string;
  plotId: string;
  // Planting cycle this record belongs to (round 7.1/7.4) — bound at create
  // time. cycleNo/cycleLabel drive the history badge ("cycleLabel, else รอบที่
  // N, else รอบปลูก" — round 8.0.5) without a per-row fetch. cycleLabel is
  // the record's OWN cycle's label, never the plot's current active cycle.
  plotCycleId: string | null;
  cycleNo: number | null;
  cycleLabel: string | null;
  supplierId: string;
  recordedById: string;
  // Retired (round 8-3G) — nullable. Historical records keep their code;
  // new records are always null (no create flow collects it anymore).
  submittedByCode: string | null;
  submittedByName: string | null;
  recordDate: string;
  crop: string | null;
  variety: string | null;
  growthStage: string | null;
  yieldPct: string | null;
  // Round 8-8C — same read-only, server-derived fields as RecordDetail (see
  // its own comment below); both null for a legacy record / legacy client.
  yieldQuantityKg: string | number | null;
  yieldTargetKgSnapshot: string | number | null;
  fieldPrepScore: number | null;
  weatherScore: number | null;
  careScore: number | null;
  varietyResistanceScore: number | null;
  isActive: boolean;
  createdAt: string;
  plotCode: string;
  plotName: string;
  supplierName: string;
}

export interface RecordDetail {
  id: string;
  plotId: string;
  // Planting cycle this record belongs to (round 7.1/7.4) — the cycle* fields
  // are the record's OWN cycle (bound at create time), sourced from
  // record.plot_cycle, never the plot's current mirror: a record from a
  // closed/older cycle keeps showing THAT cycle's crop/variety/plan even after
  // the plot starts a newer active cycle. All null if the record has no cycle.
  plotCycleId: string | null;
  cycleNo: number | null;
  cycleStatus: 'active' | 'harvested' | 'cancelled' | null;
  // User-facing season name of the record's OWN cycle (round 8.0.5), e.g.
  // "jun2026" — same display precedence as PlotCycle.cycleLabel: shown
  // before "รอบที่ N" when set. null for cycles that predate the field.
  cycleLabel: string | null;
  cycleCrop: string | null;
  cycleVariety: string | null;
  cycleLotNo: string | null;
  cyclePlantingDate: string | null;
  cyclePlantCount: number | null;
  cycleExpectedYieldFull: string | number | null;
  cycleExpectedYieldUnit: string | null;
  supplierId: string;
  recordedById: string;
  recordedByEmail: string;
  recordedByName: string;
  // Retired (round 8-3G) — nullable, same as RecordSummary.
  submittedByCode: string | null;
  submittedByName: string | null;
  // Client IP captured server-side at creation — audit/read-only, never
  // part of any create/update payload. null for records made before the
  // feature existed.
  submittedIp: string | null;
  plotCode: string;
  plotName: string;
  supplierName: string;
  recordDate: string;
  crop: string | null;
  variety: string | null;
  growthStage: string | null;
  plantingDate: string | null;
  yieldPct: string | null;
  // Round 8-8B — kg-first Yield input + the server-derived comparison
  // target (backend round 8-8A/8-8A.1), both read-only/server-derived.
  // yieldTargetKgSnapshot is NEVER accepted on RecordCreatePayload below —
  // a client can't forge it, same as backend's RecordCreate schema.
  yieldQuantityKg: string | number | null;
  yieldTargetKgSnapshot: string | number | null;
  weatherCondition: string | null;
  fieldPrepScore: number | null;
  weatherScore: number | null;
  careScore: number | null;
  varietyResistanceScore: number | null;
  recommendation: string | null;
  notes: string | null;
  latitude: string | null;
  longitude: string | null;
  photoUrls: string[];
  customFields: Record<string, unknown>;
  isActive: boolean;
  createdAt: string;
  updatedAt: string;

  // Phone-access attribution (round 8-3A/8-3B, surfaced round 8-3E) —
  // read-only, server-derived from the phone-bound public inspection
  // session at create time. All null for a logged-in-flow record or one
  // made before this feature existed; never sent by any create/update
  // payload (the backend rejects an unrecognized field on those, and these
  // are deliberately absent from RecordCreatePayload below).
  plotAccessPhoneId: string | null;
  submittedPhoneSnapshot: string | null;
  submittedPhoneType: 'primary' | 'additional' | null;
  // Round 8-11A — canonical values; 'extension' was renamed to 'chiatai'
  // (migration 0047 rewrote the existing dev rows), so no record can still
  // read back the old value. Render via lib/inspection-attribution's
  // inspectorTypeLabel, never raw.
  inspectorType: 'farmer' | 'supplier' | 'chiatai' | null;
}

export interface RecordListParams {
  plotId?: string;
  supplierId?: string;
  dateFrom?: string;
  dateTo?: string;
  activeOnly?: boolean;
  limit?: number;
  offset?: number;
}

export interface RecordCreatePayload {
  plotId: string;
  supplierId: string;
  recordDate: string;
  // submittedByCode is retired (round 8-3G) — never a create-payload field.
  submittedByName?: string | null;
  crop?: string | null;
  variety?: string | null;
  growthStage?: string | null;
  plantingDate?: string | null;
  yieldPct?: number | null;
  // Round 8-8B — kg-first Yield input (optional; a legacy caller sending
  // only yieldPct is unaffected). yieldTargetKgSnapshot is deliberately NOT
  // a field here — server-derived only, never client-writable.
  yieldQuantityKg?: number | null;
  weatherCondition?: string | null;
  fieldPrepScore?: number | null;
  weatherScore?: number | null;
  careScore?: number | null;
  varietyResistanceScore?: number | null;
  recommendation?: string | null;
  notes?: string | null;
  latitude?: number | null;
  longitude?: number | null;
  photoUrls?: string[];
  customFields?: Record<string, unknown>;
}

/**
 * camelCase → snake_case for the GET /records query params. FastAPI's
 * list_records declares snake_case params (plot_id, supplier_id, date_from,
 * date_to, active_only); sending camelCase keys makes FastAPI silently
 * ignore the filters and return every record in scope — which is what made
 * a plot's "ประวัติการตรวจ" show records from other plots. Omit undefined
 * so an unset filter doesn't override the endpoint's own defaults.
 */
function toSnakeListParams(params: RecordListParams): Record<string, unknown> {
  return {
    plot_id: params.plotId,
    supplier_id: params.supplierId,
    date_from: params.dateFrom,
    date_to: params.dateTo,
    active_only: params.activeOnly,
    limit: params.limit,
    offset: params.offset,
  };
}

export const listRecords = (params: RecordListParams = {}) =>
  apiClient
    .get<RecordSummary[]>('/api/v1/records', { params: toSnakeListParams(params) })
    .then(r => r.data);

export const getRecord = (id: string) =>
  apiClient.get<RecordDetail>(`/api/v1/records/${id}`).then(r => r.data);

export const createRecord = (payload: RecordCreatePayload) =>
  apiClient.post<RecordDetail>('/api/v1/records', payload).then(r => r.data);

/**
 * Builds the multipart body for POST /api/v1/records/with-photos
 * (round 13/14) — `payload` travels as a single JSON-encoded Form field
 * (the backend can't mix a Pydantic JSON body with File parts in one
 * multipart request; see backend/app/api/v1/records.py) plus one `photos`
 * file part per photo. Caller is expected to omit `photoUrls` from
 * `payload` — the backend always overwrites it from the uploaded files.
 */
export function buildRecordWithPhotosFormData(payload: RecordCreatePayload, photos: File[]): FormData {
  const formData = new FormData();
  formData.append('payload', JSON.stringify(payload));
  for (const photo of photos) formData.append('photos', photo);
  return formData;
}

export const createRecordWithPhotos = (payload: RecordCreatePayload, photos: File[]) => {
  const formData = buildRecordWithPhotosFormData(payload, photos);
  return apiClient.post<RecordDetail>('/api/v1/records/with-photos', formData).then(r => r.data);
};

// Round 8.0.5 append-only lock — there is deliberately no updateRecord/PATCH
// client here. An existing record's inspection fields (crop/variety/stage/
// yield/scores/GPS/photos/notes/customFields) can never change once
// created; a new inspection always means a new Record. deactivateRecord
// below is the only mutation an existing record can undergo.
export const deactivateRecord = (id: string) =>
  apiClient.post<RecordDetail>(`/api/v1/records/${id}/deactivate`).then(r => r.data);

/**
 * round 15 — scoped photo download. records.photoUrls stores raw storage
 * paths like `/media/inspection-photos/<uuid>.<ext>` (see
 * backend/app/services/inspection_photos.py) — there is no static route
 * serving that prefix (deliberately: it would bypass RLS/permission scope),
 * so a photo must always be fetched through
 * GET /api/v1/records/{recordId}/photos/{photoId} instead, which re-checks
 * the same scope as reading the record itself.
 *
 * Matches backend's PHOTO_FILENAME_PATTERN exactly (uuid4().hex + one of
 * the allowlisted extensions) — anything else is rejected client-side
 * before ever making a request.
 */
const PHOTO_FILENAME_PATTERN = /^[0-9a-f]{32}\.(?:jpg|png|webp)$/;

/** Pulls the filename out of a stored photoUrl and validates its shape.
 * Returns null (not a thrown error) for anything that doesn't match, so
 * callers can render a fallback instead of crashing on unexpected/legacy
 * data. */
export function extractPhotoFilename(photoUrl: string): string | null {
  const filename = photoUrl.split('/').filter(Boolean).pop() ?? '';
  return PHOTO_FILENAME_PATTERN.test(filename) ? filename : null;
}

/** Fetches one photo as a Blob via the authenticated, scope-checked
 * download endpoint. Throws if `photoFilename` doesn't match the expected
 * shape — callers should validate with `extractPhotoFilename` first, but
 * this re-checks regardless so the function is safe to call directly. */
export async function getRecordPhotoBlob(recordId: string, photoFilename: string): Promise<Blob> {
  if (!PHOTO_FILENAME_PATTERN.test(photoFilename)) {
    throw new Error(`Invalid photo filename: ${photoFilename}`);
  }
  const res = await apiClient.get<Blob>(
    `/api/v1/records/${recordId}/photos/${photoFilename}`,
    { responseType: 'blob' },
  );
  return res.data;
}
