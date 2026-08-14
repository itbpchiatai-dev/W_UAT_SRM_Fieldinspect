/**
 * publicInspectionAccess — phone-only public inspection flow (round 8-3D),
 * consuming the round 8-3B backend contract: enter a phone -> list the plots
 * it may inspect -> pick one + an inspector type -> exchange for an
 * inspectionSessionToken (consumed by the existing
 * createPublicInspectionRecord/createPublicRecordWithPhotos in
 * api/publicInspection.ts — record creation itself is unchanged).
 *
 * Reuses publicApiClient from api/publicInspection.ts (never the logged-in
 * apiClient) — same reasoning as that file's docstring: no login token, and a
 * 401 here means an expired/invalid PHONE or INSPECTION session, not an
 * expired login that apiClient's silent-refresh would try to handle.
 *
 * PII: the phone number is NEVER part of any response type here (mirroring
 * the backend's PublicPhoneAccessPlotItem/LookupResponse/SelectPlotResponse,
 * which deliberately carry no phone/phoneLast4/accessPhoneId field), and this
 * module contains no logging of any kind.
 */
import { publicApiClient } from './publicInspection';

/** Canonical API values (round 8-11A) — mirrors the backend's InspectorType
 * Literal. 'extension' was renamed to 'chiatai' (migration 0047); a token or
 * request carrying the old value is rejected, which is intentional on dev.
 * Structurally identical to lib/inspection-attribution's InspectorType — the
 * public flow SENDS these, the authenticated views DISPLAY them. */
export type PublicInspectorType = 'farmer' | 'supplier' | 'chiatai';

/** Round 8-9D — the runtime capability contract for /public/inspect. The page
 * asks the BACKEND whether a plot password is required rather than reading a
 * build-time env var: the bundle and the API are deployed independently, so a
 * stale bundle guessing "no password" against a backend that now enforces one
 * would lock every field user out. Carries no secret and nothing plot-specific. */
export interface PublicInspectionAccessConfig {
  passwordRequired: boolean;
  passwordMinLength: number;
  passwordMaxLength: number;
}

/** GET /api/v1/public/inspection-access/config — no body, no auth, no DB on the
 * server side. A failure here must NOT be treated as "no password required";
 * the caller has to fail closed (see PublicInspect.tsx). */
export async function getPublicInspectionAccessConfig(): Promise<PublicInspectionAccessConfig> {
  const res = await publicApiClient.get<PublicInspectionAccessConfig>(
    '/api/v1/public/inspection-access/config',
  );
  return res.data;
}

/** One plot a phone may inspect — no phone, no qrKey, no GPS/address, no
 * yield-plan data (mirrors the backend's PublicPhoneAccessPlotItem exactly). */
export interface PublicPhoneAccessPlot {
  plotId: string;
  plotCode: string;
  plotName: string;
  supplierId: string;
  supplierCode: string;
  supplierName: string;
  accessType: 'primary' | 'additional';
  canInspect: boolean;
  unavailableReason: 'no_active_cycle' | null;
  plotCycleId: string | null;
  cycleNo: number | null;
  cycleLabel: string | null;
  crop: string | null;
  variety: string | null;
  /** Round 8-19 — plot+ACTIVE-CYCLE level, not per access phone: true when
   * the active cycle has an active record dated today, whichever authorized
   * number made it. Every number on a plot therefore sees the same status. */
  inspectedToday: boolean;
  /** Round 8-19 — "YYYY-MM-DD" of the latest active record WITHIN the active
   * cycle, or null when there is no active cycle or that cycle has no record
   * yet. Date only, never a time. A previous cycle's inspection never
   * appears here — prefer this over lastInspectedAt for anything cycle-scoped. */
  lastInspectionDate: string | null;
  /** Pre-8-19: the Plot's denormalized last-inspection TIMESTAMP, which is
   * not cycle-scoped. Kept for existing consumers. */
  lastInspectedAt: string | null;
  /** Round 8-3K — sourced from the SAME active cycle as crop/variety above
   * (never the plot's current_* mirror); null with no active cycle. */
  lotNo: string | null;
  plantingDate: string | null;
  /** Round 8-4C Part B — enough to open a full offline inspection form from
   * this cached list item without another round-trip. plantCount/
   * expectedYield* are active-cycle plan data (null with no active cycle,
   * same rule as lotNo/plantingDate above); currentYieldPct/currentStage are
   * the Plot's inspection-derived snapshot (same source as lastInspectedAt
   * above, unconditional on active-cycle presence). */
  plantCount: number | null;
  expectedYieldFull: string | number | null;
  expectedYieldUnit: string | null;
  currentYieldPct: string | number | null;
  currentStage: string | null;
}

export interface PublicPhoneAccessLookupResult {
  phoneAccessSessionToken: string;
  expiresIn: number;
  qrMatchedPlotId: string | null;
  plots: PublicPhoneAccessPlot[];
}

/** Round 8-9D — a single object instead of positional arguments. With three
 * optional-ish values (phone, password, qrKey) all typed `string`, a positional
 * signature makes it possible to slip a password into the qrKey slot with no
 * type error at all — and qrKey is echoed into a plot-matching decision. Named
 * keys make that class of mistake impossible. */
export interface PublicInspectionAccessLookupInput {
  phone: string;
  /** Only supplied when the backend's config says passwordRequired. Omitted
   * from the request body entirely otherwise — never sent as an empty string. */
  password?: string | null;
  qrKey?: string | null;
}

/**
 * POST /api/v1/public/inspection-access/lookup — phone and password in the POST
 * BODY only, never a query string, never a header, never the URL. qrKey is
 * optional (round 8-3D QR entry — the modern opaque-key locator only; the legacy
 * supplierCode+plotCode QR shape has no server-side match here and is resolved
 * client-side against the returned `plots` list instead, see PublicInspect.tsx).
 *
 * The password key is OMITTED (not null, not '') whenever the caller has none,
 * so a phone-only deployment sends the byte-identical body it sent before round
 * 8-9D. Nothing here logs, and the returned object never carries either secret.
 */
export async function lookupPublicInspectionAccess(
  input: PublicInspectionAccessLookupInput,
): Promise<PublicPhoneAccessLookupResult> {
  const { phone, password, qrKey } = input;
  const body: Record<string, unknown> = { phone, qrKey: qrKey ?? null };
  if (password) body.password = password;
  const res = await publicApiClient.post<PublicPhoneAccessLookupResult>(
    '/api/v1/public/inspection-access/lookup',
    body,
  );
  return res.data;
}

export interface PublicPhoneAccessListResult {
  plots: PublicPhoneAccessPlot[];
}

/**
 * POST /api/v1/public/inspection-access/plots — re-lists the SAME phone
 * session's plots from the token (no new token minted). Used to refresh
 * inspectedToday/canInspect after a select-time 404/409, and after a
 * successful submit ("ตรวจแปลงถัดไป").
 */
export async function listPublicInspectionAccessPlots(
  phoneAccessSessionToken: string,
): Promise<PublicPhoneAccessListResult> {
  const res = await publicApiClient.post<PublicPhoneAccessListResult>(
    '/api/v1/public/inspection-access/plots',
    { phoneAccessSessionToken },
  );
  return res.data;
}

/** Exchange result — same shape the verify-code flow returns (read-only Plot/
 * Supplier/PlotCycle identity + planting/yield-plan data), minus anything
 * phone-related. inspectionSessionToken is what actually gates record
 * creation; the phone binding lives INSIDE that token, server-side only. */
export interface PublicSelectPlotResult {
  inspectionSessionToken: string;
  expiresIn: number;
  plotId: string;
  plotCode: string;
  plotName: string;
  supplierId: string;
  supplierCode: string;
  supplierName: string;
  plotCycleId: string;
  cycleNo: number;
  cycleLabel: string | null;
  currentCrop: string | null;
  currentVariety: string | null;
  currentLotNo: string | null;
  currentPlantingDate: string | null;
  plantCount: number | null;
  expectedYieldFull: string | number | null;
  expectedYieldUnit: string | null;
  /** Inspection-derived current-status snapshot (round 8-3J) — mirrors
   * plot.current_yield_pct/current_stage/last_inspected_at, the same
   * columns the logged-in RecordForm reads. expectedYieldFull above stays
   * the 100%-target from PlotCycle; this is the latest inspection's actual
   * percentage of that target. */
  currentYieldPct: string | number | null;
  currentStage: string | null;
  lastInspectedAt: string | null;
}

/**
 * POST /api/v1/public/inspection-access/select-plot — picks one plot + an
 * inspector type, minting the inspectionSessionToken the record-create
 * endpoints consume. Errors this endpoint returns (mapped by the caller):
 *   401 — phone token expired/invalid
 *   404 — assignment revoked / plot no longer in this phone's list
 *   409 — plot has no active planting cycle right now
 */
export async function selectPublicInspectionPlot(
  phoneAccessSessionToken: string,
  plotId: string,
  inspectorType: PublicInspectorType,
): Promise<PublicSelectPlotResult> {
  const res = await publicApiClient.post<PublicSelectPlotResult>(
    '/api/v1/public/inspection-access/select-plot',
    { phoneAccessSessionToken, plotId, inspectorType },
  );
  return res.data;
}
