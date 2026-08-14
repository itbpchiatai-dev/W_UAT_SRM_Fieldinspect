/**
 * publicInspection — API calls for the unauthenticated field-assistant flow
 * (enter phone -> pick a plot -> submit one record; round 8-3G retired the
 * legacy scan-QR -> verify-inspection-code path this file used to also
 * cover — see api/publicInspectionAccess.ts for the live phone-access flow).
 *
 * Uses a separate axios instance from api/client.ts's apiClient
 * deliberately: these endpoints never require a login token, and reusing
 * apiClient would (a) attach a stray Authorization header if the same
 * browser also happens to have a logged-in session elsewhere, and
 * (b) trigger apiClient's silent-refresh retry on the 401s these
 * endpoints legitimately return for an invalid/expired inspection
 * session token — that 401 has nothing to do with login, so retrying via
 * /api/v1/auth/refresh would just waste a round trip against a cookie
 * this flow never has.
 */
import axios from 'axios';

// Exported (round 19.1) so api/publicMasterdata.ts can reuse the exact same
// no-auth instance instead of creating a near-duplicate one — same
// reasoning as above, just shared across every unauthenticated endpoint
// now instead of just this file's.
export const publicApiClient = axios.create({
  baseURL: import.meta.env.VITE_API_BASE_URL || '',
  timeout: 15_000,
});

export interface PublicRecordCreatePayload {
  inspectionSessionToken: string;
  recordDate: string;
  submittedByName: string | null;
  // crop/variety/plantingDate are deliberately NOT here (round 20.2) —
  // plot MASTER data, set only via Plot Create/Edit by an admin; the
  // backend snapshots them from the verified plot itself and would 422 a
  // client that tried to send them (PublicRecordCreate has no such
  // fields — extra="forbid"). See api/publicInspectionAccess.ts's
  // PublicSelectPlotResult currentCrop/currentVariety/currentPlantingDate
  // for the read-only values to display instead.
  growthStage: string | null;
  yieldPct: number | null;
  // Round 8-8B — kg-first Yield input (optional; a legacy caller sending
  // only yieldPct is unaffected). yieldTargetKgSnapshot is deliberately NOT
  // a field here — server-derived only (backend round 8-8A), never
  // client-writable; PublicRecordCreate would 422 (extra="forbid") a client
  // that tried to send it.
  yieldQuantityKg?: number | null;
  weatherCondition: string | null;
  fieldPrepScore: number | null;
  weatherScore: number | null;
  careScore: number | null;
  varietyResistanceScore: number | null;
  recommendation: string | null;
  notes: string | null;
  latitude: number | null;
  longitude: number | null;
  // Offline submission identity (round 8-4A/8-4B) — optional so an ONLINE-only
  // caller (there are none left in this file after round 8-4B, but the type
  // itself must stay backward-compatible with anything constructing this
  // shape by hand, e.g. an existing test fixture) still compiles. When
  // present, all three travel together — see buildOfflinePublicRecordPayload.
  // Never renamed from the backend's own camelCase field names.
  clientSubmissionId?: string;
  capturedAt?: string;
  capturedPlotCycleId?: string;
}

export interface PublicRecordCreateResult {
  id: string;
  plotId: string;
  plotCode: string;
  plotName: string;
  supplierId: string;
  supplierCode: string;
  supplierName: string;
  recordDate: string;
  submittedByName: string | null;
  createdAt: string;
  // Offline receipt (round 8-4A/8-4B) — echoes the identity the client sent,
  // so the caller can match this response to the draft/request it came from.
  // Both null for a submission that never carried the offline triple.
  clientSubmissionId: string | null;
  capturedAt: string | null;
}

export async function createPublicInspectionRecord(
  payload: PublicRecordCreatePayload,
): Promise<PublicRecordCreateResult> {
  const res = await publicApiClient.post<PublicRecordCreateResult>(
    '/api/v1/public/records',
    payload,
  );
  return res.data;
}

/**
 * Builds the multipart body for POST /api/v1/public/records/with-photos
 * (round 13/14) — `payload` travels as a single JSON-encoded Form field
 * (the backend can't mix a Pydantic JSON body with File parts in one
 * multipart request; see backend/app/api/v1/public_records.py) plus one
 * `photos` file part per photo. Pulled out as its own function so the
 * exact field names/shape are testable without mocking axios.
 */
export function buildPublicRecordWithPhotosFormData(
  payload: PublicRecordCreatePayload,
  photos: File[],
): FormData {
  const formData = new FormData();
  formData.append('payload', JSON.stringify(payload));
  for (const photo of photos) formData.append('photos', photo);
  return formData;
}

export async function createPublicRecordWithPhotos(
  payload: PublicRecordCreatePayload,
  photos: File[],
): Promise<PublicRecordCreateResult> {
  const formData = buildPublicRecordWithPhotosFormData(payload, photos);
  const res = await publicApiClient.post<PublicRecordCreateResult>(
    '/api/v1/public/records/with-photos',
    formData,
  );
  return res.data;
}

/** Editable form fields for the public flow — everything except the
 * token/date, which the caller supplies separately (date is auto/today,
 * not user-editable, matching the logged-in RecordForm's convention). */
export interface PublicInspectionFormFields {
  submittedByName: string;
  // crop/variety/plantingDate deliberately not here (round 20.2) — see
  // PublicRecordCreatePayload above.
  growthStage: string;
  yieldPct: number | null;
  // Round 8-8B — kg is the primary input; null until a plot with a
  // comparable kg target is selected (contract #12: never a faked 100%).
  yieldQuantityKg: number | null;
  weatherCondition: string;
  fieldPrepScore: number | null;
  weatherScore: number | null;
  careScore: number | null;
  varietyResistanceScore: number | null;
  recommendation: string;
  notes: string;
  latitude: number | null;
  longitude: number | null;
}

/**
 * Shapes raw form state into the API payload: trims strings and converts
 * blanks to null. Pulled out of the page component so it's testable
 * without mounting anything — and so there's one obvious place that
 * proves the payload never carries plotId/supplierId/recordedById (those
 * aren't PublicInspectionFormFields fields at all).
 */
export function buildPublicRecordPayload(
  sessionToken: string,
  recordDate: string,
  fields: PublicInspectionFormFields,
): PublicRecordCreatePayload {
  return {
    inspectionSessionToken: sessionToken,
    recordDate,
    submittedByName: fields.submittedByName.trim() || null,
    growthStage: fields.growthStage.trim() || null,
    yieldPct: fields.yieldPct,
    yieldQuantityKg: fields.yieldQuantityKg,
    weatherCondition: fields.weatherCondition.trim() || null,
    fieldPrepScore: fields.fieldPrepScore,
    weatherScore: fields.weatherScore,
    careScore: fields.careScore,
    varietyResistanceScore: fields.varietyResistanceScore,
    recommendation: fields.recommendation.trim() || null,
    notes: fields.notes.trim() || null,
    latitude: fields.latitude,
    longitude: fields.longitude,
  };
}

/** The idempotency identity carried by every offline-capable submission
 * (round 8-4B) — one clientSubmissionId stands for one draft/attempt across
 * its whole life, whether it's ultimately sent online on the first try or
 * queued and retried later. All three are required together; the backend
 * (round 8-4A/8-4A.1) rejects a partial set with 422. */
export interface OfflineSubmissionIdentity {
  clientSubmissionId: string;
  capturedAt: string;
  capturedPlotCycleId: string;
}

/**
 * Round 8-4B — same shape as buildPublicRecordPayload, plus the offline
 * identity triple. Used for EVERY public submission now (online or offline):
 * sending the triple even on a normal online submit means a request that the
 * server actually committed, but whose response never reached the client
 * (a dropped connection, a killed tab), can be safely retried with the same
 * key later without creating a duplicate record.
 */
export function buildOfflinePublicRecordPayload(
  sessionToken: string,
  recordDate: string,
  fields: PublicInspectionFormFields,
  identity: OfflineSubmissionIdentity,
): PublicRecordCreatePayload {
  return {
    ...buildPublicRecordPayload(sessionToken, recordDate, fields),
    clientSubmissionId: identity.clientSubmissionId,
    capturedAt: identity.capturedAt,
    capturedPlotCycleId: identity.capturedPlotCycleId,
  };
}
