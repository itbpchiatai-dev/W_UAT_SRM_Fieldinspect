/**
 * Plots API — list / get / create / update / deactivate / assign users
 */
import axios from 'axios';
import { apiClient } from './client';
import { fetchAllPages } from '../lib/paginate';
import type { PlotQrLabelData } from '../components/farmlog/PlotQrPrintSheet';

/**
 * A QR print label built straight from a plot list/detail row — round 6.1's
 * denormalised supplierCode/supplierName mean this needs no separate
 * suppliers lookup, so it works even when a supplier is over the frontend's
 * active-suppliers cap or is inactive. Accepts either PlotSummary or
 * PlotDetail (both carry these fields).
 */
export function plotToQrLabel(plot: {
  id: string;
  plotCode: string;
  name: string;
  supplierCode: string;
  supplierName: string;
  qrKey: string | null;
}): PlotQrLabelData {
  return {
    plotId: plot.id,
    plotCode: plot.plotCode,
    plotName: plot.name,
    supplierCode: plot.supplierCode,
    supplierName: plot.supplierName,
    qrKey: plot.qrKey,
  };
}

export interface AssignedUser {
  userId: string;
  email: string;
  fullName: string;
  assignedAt: string;
}

// Decimal-backed fields (latitude/longitude/rai) — FastAPI/Pydantic
// serializes these as JSON strings in practice (verified against the real
// response; see round 15.1's report), but typing them as a plain `number`
// has been wrong before and caused a runtime `.toFixed()` crash
// (RecordForm.tsx, round 15). Typed as the honest union here — every call
// site must go through `lib/numeric.ts`'s `toNumberOrNull`/`formatFixed`
// rather than assuming either shape.
export interface PlotSummary {
  id: string;
  supplierId: string;
  // Denormalised supplier display (round 6.1) — comes straight from the
  // backend (Plot.supplier), so the list shows supplier code+name and prints
  // QR without joining the separate (capped, active-only) suppliers fetch.
  // "" for a plot whose supplier somehow didn't load.
  supplierCode: string;
  supplierName: string;
  plotCode: string;
  name: string;
  village: string | null;
  district: string | null;
  province: string | null;
  latitude: string | number | null;
  longitude: string | number | null;
  isActive: boolean;
  assignedCount: number;

  // Opaque QR locator (round 20 QR hardening) — what the printed QR deep
  // link now encodes instead of supplierCode/plotCode; see
  // lib/plot-qr.ts's buildPlotQrDeepLink. Never client-writable. null only
  // for a plot that somehow predates the round-20 backfill.
  qrKey: string | null;

  // Yield summary for the Plots list (round 17/18) — enough to render
  // "80% → 800 kg / 1,000 kg" per row without an extra per-plot fetch; see
  // lib/yield-planning.ts's computeCurrentExpectedYield/isYieldPlanComplete.
  currentYieldPct: string | null;
  expectedYieldFull: string | number | null;
  expectedYieldUnit: string | null;
  plantCount: number | null;

  // Compact planting-cycle identity (round 18) — plot MASTER data, same
  // as PlotDetail's currentCrop/currentVariety/currentLotNo/
  // currentPlantingDate, never inspection-synced.
  currentCrop: string | null;
  currentVariety: string | null;
  currentLotNo: string | null;
  currentPlantingDate: string | null;

  // Latest-inspection context alongside currentYieldPct above —
  // inspection-synced, read-only; lets the new-record form default its
  // Yield % to the plot's last known value and label the source
  // ("ตรวจล่าสุด <date> · ระยะ <stage>") without a per-plot fetch.
  currentStage: string | null;
  lastInspectedAt: string | null;

  // Active planting cycle read-model (round 7.3.1) — the authoritative
  // "does this plot have an active รอบปลูก" signal from the backend
  // (activeCycleId != null), replacing the old inferHasActiveCycle() guess
  // from the current_* mirror. All null when the plot has no active cycle.
  activeCycleId: string | null;
  activeCycleNo: number | null;
  activeCycleStatus: 'active' | null;
  activeCycleCrop: string | null;
  activeCycleVariety: string | null;
  // User-facing season name of the active cycle (round 8.0), e.g. "jun2026".
  activeCycleLabel: string | null;
  activeCycleLotNo: string | null;
  // Active cycle PO / P.Code (round 8-5B) — denormalized read mirror for
  // the Plots list/detail; null when no active cycle.
  activeCyclePoNumber: string | null;
  activeCyclePCode: string | null;
  // Round 8-12A — the active cycle's SUPPLIER lot number, mirrored alongside
  // po/pCode so the list/detail can show it without a second fetch. null when
  // there is no active cycle, or the cycle has none. Never a fallback to an
  // older cycle's value.
  activeCycleSupplierLotNo: string | null;
  activeCyclePlantingDate: string | null;
  activeCyclePlantCount: number | null;
  activeCycleExpectedYieldFull: string | number | null;
  activeCycleExpectedYieldUnit: string | null;

  // Access phones (round 8-3A/8-3C) — the plot's ACTIVE primary/additional
  // phone config, read-only here (managed via getPlotAccessPhones/
  // replacePlotAccessPhones, a sub-resource — never through PlotCreate/
  // PlotUpdate). Canonical digit strings (e.g. "0845552162"); format for
  // display with lib/phone.ts's formatThaiMobile.
  primaryPhone: string | null;
  additionalPhones: string[];
}

export interface PlotDetail {
  id: string;
  supplierId: string;
  // Denormalised supplier display (round 6.1) — see PlotSummary.
  supplierCode: string;
  supplierName: string;
  plotCode: string;
  name: string;
  village: string | null;
  district: string | null;
  province: string | null;
  latitude: string | number | null;
  longitude: string | number | null;
  rai: string | number | null;
  isActive: boolean;
  assignedUsers: AssignedUser[];
  createdAt: string;
  updatedAt: string;

  // Opaque QR locator (round 20 QR hardening) — see PlotSummary.qrKey.
  qrKey: string | null;

  // currentCrop/currentVariety/currentLotNo/currentPlantingDate are plot
  // MASTER / planting-cycle data (round 17) — set via Plot Create/Edit,
  // never overwritten by inspection-record sync (locked down round 17.1;
  // see backend plot_repository.sync_current_status_from_record). Despite
  // the "current" prefix they behave nothing like the fields below.
  currentCrop: string | null;
  currentVariety: string | null;
  currentLotNo: string | null;
  currentPlantingDate: string | null;

  // True inspection-derived current-status snapshot (round 11/12) —
  // read-only, kept in sync by the backend from the latest inspection
  // record on every create. Decimal fields come back as JSON strings in
  // practice — use lib/numeric.ts's toNumberOrNull/formatFixed rather
  // than calling .toFixed() directly.
  currentStage: string | null;
  currentYieldPct: string | null;
  currentFieldPrepScore: number | null;
  currentWeatherScore: number | null;
  currentCareScore: number | null;
  currentVarietyResistanceScore: number | null;
  currentGpsLat: string | null;
  currentGpsLng: string | null;
  lastInspectedAt: string | null;
  lastInspectedByCode: string | null;
  lastInspectionRecordId: string | null;

  // Yield-planning base/master data (round 17) — set directly on Plot
  // Create/Edit, never overwritten by inspection-record sync (unlike
  // currentYieldPct above). "Current expected yield" is deliberately not a
  // field here — compute it via lib/yield-planning.ts's
  // computeCurrentExpectedYield(expectedYieldFull, currentYieldPct).
  plantCount: number | null;
  expectedYieldFull: string | number | null;
  expectedYieldUnit: string | null;

  // Active planting cycle read-model (round 7.3.1) — see PlotSummary. The
  // Plot Detail page loads the full cycle list separately (for history), but
  // this gives the same authoritative active-cycle snapshot without it.
  activeCycleId: string | null;
  activeCycleNo: number | null;
  activeCycleStatus: 'active' | null;
  activeCycleCrop: string | null;
  activeCycleVariety: string | null;
  // User-facing season name of the active cycle (round 8.0), e.g. "jun2026".
  activeCycleLabel: string | null;
  activeCycleLotNo: string | null;
  // Active cycle PO / P.Code (round 8-5B) — denormalized read mirror for
  // the Plots list/detail; null when no active cycle.
  activeCyclePoNumber: string | null;
  activeCyclePCode: string | null;
  // Round 8-12A — the active cycle's SUPPLIER lot number, mirrored alongside
  // po/pCode so the list/detail can show it without a second fetch. null when
  // there is no active cycle, or the cycle has none. Never a fallback to an
  // older cycle's value.
  activeCycleSupplierLotNo: string | null;
  activeCyclePlantingDate: string | null;
  activeCyclePlantCount: number | null;
  activeCycleExpectedYieldFull: string | number | null;
  activeCycleExpectedYieldUnit: string | null;

  // Access phones (round 8-3A/8-3C) — see PlotSummary's comment above; same
  // read-only sub-resource, full canonical digits (Plot Detail may show the
  // complete number where the list truncates additionalPhones for space).
  primaryPhone: string | null;
  additionalPhones: string[];
}

/** Round 8-6I Part B — additive alongside activeOnly (kept for backward
 * compatibility). 'all' (default) preserves the pre-8-6I list behavior
 * (every plot regardless of status); 'active'/'inactive' filter to exactly
 * one. Sending activeOnly=true together with plotStatus='inactive' is a
 * contradiction the backend rejects with a 422 — never combine those two. */
export type PlotStatusFilter = 'all' | 'active' | 'inactive';

export interface PlotListParams {
  supplierId?: string;
  province?: string;
  /** Filter by planting-cycle master data (plots.current_crop /
   * current_variety) — exact master-data values, same source the create/
   * edit form's dropdowns use. */
  crop?: string;
  variety?: string;
  limit?: number;
  offset?: number;
  /** Free-text "ชื่อแปลงหรือรหัสแปลง" search. Round 8-18B — matches
   * plotCode/plotName ONLY; province was dropped from this filter (the page
   * has a dedicated province filter, and folding it in here made the two
   * disagree). Never carries an access phone — see searchPlotsByPhone. */
  q?: string;
  activeOnly?: boolean;
  plotStatus?: PlotStatusFilter;
  /** "รอบปลูกปัจจุบัน" filter — matches ONLY the plot's ACTIVE PlotCycle.
   * cycleLabel (never a closed/cancelled historical cycle), exact match
   * after trim. Sourced from listPlotCycleLabels' real values, not free
   * text. */
  cycleLabel?: string;
}

export interface PlotProvinceListParams {
  supplierId?: string;
  activeOnly?: boolean;
  plotStatus?: PlotStatusFilter;
}

export interface PlotCycleLabelListParams {
  supplierId?: string;
  plotStatus?: PlotStatusFilter;
}

/**
 * Physical-plot fields ONLY (round 8.0.4 ownership lock) — planting-cycle
 * identity (crop/variety/lot/plantingDate) and yield-planning base data
 * (plantCount/expectedYieldFull/expectedYieldUnit) moved to PlotCycle;
 * see backend PlotCreate schema docstring. Sending a planning field here
 * now gets a 422 (extra="forbid"), not a silent ignore. Use
 * createPlotWithCycle() to create a plot together with its first active
 * cycle in one request.
 */
export interface PlotCreatePayload {
  supplierId: string;
  plotCode: string;
  name: string;
  village?: string | null;
  district?: string | null;
  province?: string | null;
  latitude?: number | null;
  longitude?: number | null;
  rai?: number | null;
}

/** Physical-plot fields ONLY — see PlotCreatePayload. Planting-cycle/yield-
 * plan data is edited via updatePlotCycle instead. */
export interface PlotUpdatePayload {
  name?: string;
  village?: string | null;
  district?: string | null;
  province?: string | null;
  latitude?: number | null;
  longitude?: number | null;
  rai?: number | null;
  isActive?: boolean;
}

export async function listPlots(params: PlotListParams = {}): Promise<PlotSummary[]> {
  // camelCase → snake_case: FastAPI's list_plots takes snake_case params
  // (supplier_id, active_only, plot_status); camelCase keys are silently
  // ignored, which returns every plot in scope instead of the requested
  // filter. Omit undefined so unset filters fall back to the endpoint's
  // defaults.
  const res = await apiClient.get<PlotSummary[]>('/api/v1/plots', {
    params: {
      supplier_id: params.supplierId,
      province: params.province,
      crop: params.crop,
      variety: params.variety,
      limit: params.limit,
      offset: params.offset,
      q: params.q,
      active_only: params.activeOnly,
      plot_status: params.plotStatus,
      cycle_label: params.cycleLabel,
    },
  });
  return res.data;
}

export interface PlotPhoneSearchParams {
  /** MUST already be normalized (lib/phone.ts's normalizeThaiMobile) —
   * this function does not validate/normalize it. */
  phone: string;
  supplierId?: string;
  province?: string;
  crop?: string;
  variety?: string;
  plotStatus?: PlotStatusFilter;
  limit?: number;
  offset?: number;
  cycleLabel?: string;
  /** Round 8-18B — "ชื่อแปลงหรือรหัสแปลง" free text, combined with `phone` as
   * an INTERSECTION by the backend (plot must match both). Travels in the
   * POST body alongside the phone, so using both search boxes together never
   * forces a fallback to GET /plots?q= (which would put the phone in a URL).
   * Matches plotCode/plotName only — never province. */
  q?: string;
}

/**
 * Secure phone search (round 8-17A.2) — POST body, never a GET query
 * string, so the phone never lands in the URL, browser history, or the
 * backend's access log (which records the full request line, query string
 * included, but never the body). Matches a plot's active primary OR
 * additional access phone (plot_access_phones), exact match on the
 * canonical normalized form. Response is the same PlotSummary shape as
 * listPlots — one number can authorize many plots, so this can return more
 * than one row; a plot with several matching phones still appears once
 * (backend uses an EXISTS subquery, never a JOIN, specifically to avoid
 * that duplication).
 */
export async function searchPlotsByPhone(params: PlotPhoneSearchParams): Promise<PlotSummary[]> {
  const res = await apiClient.post<PlotSummary[]>('/api/v1/plots/search-by-phone', {
    phone: params.phone,
    supplierId: params.supplierId,
    province: params.province,
    crop: params.crop,
    variety: params.variety,
    plotStatus: params.plotStatus,
    limit: params.limit,
    offset: params.offset,
    cycleLabel: params.cycleLabel,
    q: params.q,
  });
  return res.data;
}

export async function listPlotProvinces(params: PlotProvinceListParams = {}): Promise<string[]> {
  const res = await apiClient.get<string[]>('/api/v1/plots/provinces', {
    params: {
      supplier_id: params.supplierId,
      active_only: params.activeOnly,
      plot_status: params.plotStatus,
    },
  });
  return res.data;
}

/**
 * Distinct "รอบปลูกปัจจุบัน" values within the caller's scope — the
 * SearchableFilterCombobox's real option list, sourced from ACTIVE
 * PlotCycle.cycleLabel rows only (never a closed/cancelled historical
 * label). Same shape/precedent as listPlotProvinces.
 */
export async function listPlotCycleLabels(
  params: PlotCycleLabelListParams = {},
): Promise<string[]> {
  const res = await apiClient.get<string[]>('/api/v1/plots/cycle-labels', {
    params: {
      supplier_id: params.supplierId,
      plot_status: params.plotStatus,
    },
  });
  return res.data;
}

/**
 * Fetches every active plot for a supplier, paging past whatever `limit`
 * the backend applies per call — for the "print QR by supplier" report,
 * which must not be capped to a single page like the on-screen table is.
 */
export async function fetchAllPlotsForSupplier(
  supplierId: string,
  pageSize = 200,
): Promise<PlotSummary[]> {
  return fetchAllPages(
    (offset, limit) => listPlots({ supplierId, activeOnly: true, limit, offset }),
    pageSize,
  );
}

export async function getPlot(id: string): Promise<PlotDetail> {
  const res = await apiClient.get<PlotDetail>(`/api/v1/plots/${id}`);
  return res.data;
}

export async function createPlot(payload: PlotCreatePayload): Promise<PlotDetail> {
  const res = await apiClient.post<PlotDetail>('/api/v1/plots', payload);
  return res.data;
}

export async function updatePlot(id: string, payload: PlotUpdatePayload): Promise<PlotDetail> {
  const res = await apiClient.patch<PlotDetail>(`/api/v1/plots/${id}`, payload);
  return res.data;
}

// --- Plot access phones (round 8-3A/8-3C) -----------------------------------
// A plot's access phones are a SUB-RESOURCE, managed via their own GET/PUT —
// never through PlotCreatePayload/PlotUpdatePayload/PlotWithCycleCreatePayload
// as top-level plot fields. The backend is the authority on normalization
// (canonical Thai-mobile digits) and validation (dup/primary-in-additional/
// additional-without-primary → 422); the frontend's lib/phone.ts helper is a
// UX convenience only, never a substitute for the backend's own check.

/** PUT /plots/{plotId}/access-phones request body, and the optional
 * accessPhones on POST /plots/with-cycle. primaryPhone null + additionalPhones
 * [] means "no access phones configured" (a plot may be entirely empty, but
 * per round 8-3C business rule may NOT have additional phones with no
 * primary — the backend rejects that shape with 422). */
export interface PlotAccessPhoneConfig {
  primaryPhone: string | null;
  additionalPhones: string[];
}

/** One row from the full per-phone detail list (GET/PUT response `items`). */
export interface PlotAccessPhoneItem {
  id: string;
  phone: string;
  accessType: 'primary' | 'additional';
  isActive: boolean;
  createdAt: string;
  updatedAt: string;
}

/** GET/PUT /plots/{plotId}/access-phones response — primaryPhone/
 * additionalPhones are the flat convenience view (same shape as the request);
 * `items` carries the full per-row detail (id/accessType/timestamps),
 * primary-first then deterministic. */
export interface PlotAccessPhoneConfigResponse {
  primaryPhone: string | null;
  additionalPhones: string[];
  items: PlotAccessPhoneItem[];
}

export async function getPlotAccessPhones(plotId: string): Promise<PlotAccessPhoneConfigResponse> {
  const res = await apiClient.get<PlotAccessPhoneConfigResponse>(
    `/api/v1/plots/${plotId}/access-phones`,
  );
  return res.data;
}

/** Full replacement (round 8-3A) — the config sent becomes the plot's
 * COMPLETE active phone set; a number omitted from `config` is deactivated
 * (not deleted) on the backend. Atomic — one request, one transaction. */
export async function replacePlotAccessPhones(
  plotId: string,
  config: PlotAccessPhoneConfig,
): Promise<PlotAccessPhoneConfigResponse> {
  const res = await apiClient.put<PlotAccessPhoneConfigResponse>(
    `/api/v1/plots/${plotId}/access-phones`,
    config,
  );
  return res.data;
}

// --- round 8-9A/8-9B: plot inspection password ("รหัสยืนยันแปลง") -----------
//
// A per-plot shared credential: whoever holds the plot's inspection phone
// number PLUS this password will (from round 8-9C) be able to find it in the
// public flow. It is NOT a person's identity and NOT a supplier-level code.
//
// The backend never returns the password in any form — there is deliberately
// no endpoint that reveals an existing one, and no passwordLastDigits/hash/
// digest anywhere in this contract. A forgotten password is REPLACED, never
// read back. Nothing here may log, cache, or persist the plaintext.

/** GET/PUT /plots/{plotId}/inspection-access-credential response — STATUS
 * only. `configured` is false (and the other two null) until the plot has an
 * active credential. credentialVersion is bookkeeping, not something to lead
 * the UI with. */
export interface PlotInspectionCredentialStatus {
  configured: boolean;
  credentialVersion: number | null;
  updatedAt: string | null;
}

/** Requires plots.read. Out-of-scope/unknown plot → 404 (generic). */
export async function getPlotInspectionAccessCredential(
  plotId: string,
): Promise<PlotInspectionCredentialStatus> {
  const res = await apiClient.get<PlotInspectionCredentialStatus>(
    `/api/v1/plots/${plotId}/inspection-access-credential`,
  );
  return res.data;
}

/** Set or replace the plot's inspection password. Requires plots.update.
 *
 * The plaintext travels ONLY in the PUT body — never a URL, never a query
 * string, never a log line, and never a react-query key (the caller's query
 * key is keyed by plotId alone). The backend is the source of truth for the
 * code policy (4-20 ASCII digits): 422 = malformed, 503 = server pepper not
 * deployed, 409 = concurrent change, 404 = out of scope. */
export async function setPlotInspectionAccessCredential(
  plotId: string,
  password: string,
): Promise<PlotInspectionCredentialStatus> {
  const res = await apiClient.put<PlotInspectionCredentialStatus>(
    `/api/v1/plots/${plotId}/inspection-access-credential`,
    { password },
  );
  return res.data;
}

/**
 * Atomic plot + first-cycle create (round 8.0.4) — the Create Plot form's
 * only write path: ONE request creates the physical plot AND its first
 * active planting cycle in the backend's single transaction, so a plot
 * never sits in an unusable "รอเริ่มรอบปลูก" state right after creation.
 * Callers must NEVER call createPlot() then createPlotCycle() as two
 * separate requests — same reasoning as rolloverPlotCycle below (a cycle
 * failure after a successful plot create would strand an uninspectable
 * plot; the backend's transaction is what prevents that).
 */
export interface PlotWithCycleCreatePayload {
  plot: PlotCreatePayload;
  cycle: PlotCycleCreatePayload;
  // Optional access phones (round 8-3A/8-3C) — when provided, created in the
  // SAME transaction as the plot + first cycle (one request, one endpoint;
  // never a separate PUT afterward). Omit entirely (or send null) for a plot
  // with no access phones yet — behaves exactly as if this field didn't exist.
  accessPhones?: PlotAccessPhoneConfig | null;
}

export interface PlotWithCycleCreateResult {
  plot: PlotDetail;
  cycle: PlotCycle;
}

export async function createPlotWithCycle(
  payload: PlotWithCycleCreatePayload,
): Promise<PlotWithCycleCreateResult> {
  const res = await apiClient.post<PlotWithCycleCreateResult>('/api/v1/plots/with-cycle', payload);
  return res.data;
}

export async function deactivatePlot(id: string): Promise<PlotDetail> {
  const res = await apiClient.post<PlotDetail>(`/api/v1/plots/${id}/deactivate`, {});
  return res.data;
}

/**
 * Reopen a permanently-deactivated plot (round 8-6H backend / 8-6I frontend)
 * WITHOUT starting a new planting cycle — the plot becomes active again but
 * still has no active cycle, so it can't be inspected until a separate
 * start-cycle call (or use reactivatePlotWithCycle below to do both
 * atomically). Same permission as deactivate (plots.delete) on the backend.
 */
export async function reactivatePlot(id: string): Promise<PlotDetail> {
  const res = await apiClient.post<PlotDetail>(`/api/v1/plots/${id}/reactivate`, {});
  return res.data;
}

/**
 * Atomically reopen a permanently-deactivated plot AND start its first new
 * planting cycle in ONE request (round 8-6H backend / 8-6I frontend) — never
 * call reactivatePlot() then createPlotCycle() as two separate requests (a
 * cycle-create failure after a successful reactivate would strand the plot
 * active with no cycle; the backend's single transaction is what prevents
 * that). Reuses the exact same PlotCycleCreatePayload contract as
 * createPlotCycle/rolloverPlotCycle — no duplicate cycle-payload shape.
 * Requires BOTH plots.delete and plots.update on the backend.
 */
export async function reactivatePlotWithCycle(
  id: string,
  payload: PlotCycleCreatePayload,
): Promise<PlotWithCycleCreateResult> {
  const res = await apiClient.post<PlotWithCycleCreateResult>(
    `/api/v1/plots/${id}/reactivate-with-cycle`,
    payload,
  );
  return res.data;
}

export async function assignPlotUsers(id: string, userIds: string[]): Promise<PlotDetail> {
  const res = await apiClient.put<PlotDetail>(`/api/v1/plots/${id}/assignments`, { userIds });
  return res.data;
}

/** Optional filter matching the Plots page (round 8-6A backend / 8-6B
 * frontend wiring). Omitting every field (or the whole argument) keeps the
 * pre-8-6A generic single-sheet template behavior — the backend requires
 * supplierId whenever ANY other filter is set (a 422 otherwise).
 *
 * `templateMode: 'all_suppliers'` (round 8-6G) is an explicit, separate mode:
 * every active plot of every active Supplier the caller's scope covers, in
 * one file. Mutually exclusive with supplierId/province/crop/variety/
 * cycleLabel/q — never inferred from omitting supplierId (see
 * downloadPlotImportTemplate's own build-query step, which drops every
 * other field once this is set, even if a caller mistakenly passed both).
 * Only a full ("all") scope caller may actually use it — the backend 403s
 * anyone else; the frontend must never be the only gate (Plots.tsx only
 * shows this option for a full-scope user, but that's a UX nicety, not the
 * real boundary). */
export interface PlotImportTemplateParams {
  supplierId?: string;
  province?: string;
  crop?: string;
  variety?: string;
  /** "รอบปลูกปัจจุบัน" filter — same active-cycle-only match as listPlots. */
  cycleLabel?: string;
  /** Round 8-18B — same semantics as listPlots' q: plotCode/plotName only,
   * never province. The access phone is NEVER sent here (it would land in
   * the download URL) — see Plots.tsx's explanatory note. */
  q?: string;
  templateMode?: 'all_suppliers';
  /** Round 8-6J — which plot statuses Sheet 1 seeds rows from: 'all'
   * (default if omitted) mixes start_next_cycle rows (active plots) and
   * reactivate_plot_with_cycle rows (inactive plots) in the SAME sheet;
   * 'active'/'inactive' narrow to exactly one. Sent alongside BOTH the
   * filtered (supplierId) and templateMode:'all_suppliers' requests — never
   * combined with the pre-8-6A no-filter generic template. */
  plotStatus?: PlotStatusFilter;
}

/**
 * GET /plots/import-template. No params (or none set) → the generic
 * single-sheet template, byte-for-byte the pre-8-6A request (no `params` key
 * at all). Any filter set → the round 8-6A contextual "next cycle" workbook
 * scoped to that Supplier, snake_case on the wire (supplier_id, not
 * supplierId) — same convention as listPlots; FastAPI silently ignores an
 * unrecognized camelCase query key rather than erroring, so this is a
 * correctness requirement, not a style choice. `templateMode: 'all_suppliers'`
 * (round 8-6G) sends ONLY `template_mode=all_suppliers`, dropping every other
 * field. Any error response (422 no-Supplier / no-active-plot / over-5000 /
 * bad-template_mode / all_suppliers-combined-with-a-filter, or 403
 * out-of-scope all_suppliers, or 404 out-of-scope Supplier, or a network
 * failure) is normalized into a thrown PlotImportReportError — reuses the
 * exact same blobToText/extractErrorDetailFromBlob helpers postForXlsxOrThrow
 * uses below, rather than a second parser, since this is also a
 * blob-response GET that can come back as a JSON error body.
 */
export async function downloadPlotImportTemplate(
  params?: PlotImportTemplateParams,
): Promise<Blob> {
  // Build with keys OMITTED (not just undefined-valued) for anything unset —
  // stricter than relying on axios's own undefined-drops-from-querystring
  // behavior, and keeps `params` entirely absent from the request config when
  // nothing was filtered on (the exact pre-8-6A shape).
  const query: Record<string, string> = {};
  if (params?.templateMode === 'all_suppliers') {
    // Round 8-6G — explicit all-suppliers mode. Deliberately ignores every
    // other field below even if a caller passed both by mistake: the
    // backend rejects the combination (422) anyway, but there's no reason
    // to ever send a filter alongside this mode. Round 8-6J — plotStatus IS
    // still sent in this mode (it's not one of the "other filters" the
    // backend rejects alongside template_mode).
    query.template_mode = params.templateMode;
    if (params?.plotStatus) query.plot_status = params.plotStatus;
  } else {
    if (params?.supplierId) query.supplier_id = params.supplierId;
    if (params?.province) query.province = params.province;
    if (params?.crop) query.crop = params.crop;
    if (params?.variety) query.variety = params.variety;
    if (params?.cycleLabel) query.cycle_label = params.cycleLabel;
    if (params?.q) query.q = params.q;
    if (params?.plotStatus) query.plot_status = params.plotStatus;
  }
  const hasQuery = Object.keys(query).length > 0;
  try {
    const res = await apiClient.get<Blob>('/api/v1/plots/import-template', {
      responseType: 'blob',
      ...(hasQuery ? { params: query } : {}),
    });
    return res.data;
  } catch (error) {
    if (axios.isAxiosError(error) && error.response) {
      const blob = error.response.data as Blob;
      const { message, code } = await extractErrorDetailFromBlob(blob);
      throw new PlotImportReportError(message, error.response.status, code);
    }
    // No response received at all — network error/timeout.
    throw new PlotImportReportError(
      error instanceof Error ? error.message : 'ดาวน์โหลด Excel ไม่สำเร็จ',
      null,
    );
  }
}

// --- Plot + cycle Excel import (round 7.5) — preview (read-only) then
// commit (all-or-nothing). Both take a single .xlsx file uploaded as
// multipart/form-data field "file"; the backend re-validates on commit and
// never trusts a client-sent preview.
export type PlotImportAction =
  | 'create_plot_with_cycle'
  | 'start_new_cycle'
  | 'update_current_cycle'
  | 'close_and_start_new_cycle'
  | 'start_next_cycle'
  // Round 8-7A/8-7B — closes the plot's ACTIVE cycle as harvested and stamps
  // the REAL harvested-yield figures; Plot.isActive stays true. Preview-bound
  // (round 8-7A.1) exactly like start_next_cycle — see PlotImportFinalPlotPreviewStateRow.
  | 'final_plot';

export interface PlotImportRowPayload {
  action: string | null;
  supplierCode: string | null;
  plotCode: string | null;
  plotName: string | null;
  // Access-phone columns (round 8-3E) — canonical digits, echoed back for the
  // preview table. Both null/[] means the row's phone columns were left
  // blank (preserve for an existing plot, none for a new one).
  primaryPhone: string | null;
  additionalPhones: string[];
  village: string | null;
  district: string | null;
  province: string | null;
  latitude: string | number | null;
  longitude: string | number | null;
  rai: string | number | null;
  crop: string | null;
  variety: string | null;
  cycleLabel: string | null;
  // PO / P.Code (round 8-5B) — normalized echo of the row's input.
  poNumber: string | null;
  pCode: string | null;
  lotNo: string | null;
  // Round 8-12A — the SUPPLIER's own lot number for the row, echoed back
  // trimmed (blank → null). Shown beside, never merged into, the system lot:
  // it takes no part in the Auto Lot formula or the Manual/Auto decision.
  supplierLotNo: string | null;
  // Round 8-21A/8-21B — three independent, OPTIONAL back-office reference
  // fields, echoed back trimmed (blank → null). For update_current_cycle
  // ONLY, this differs from poNumber/pCode/supplierLotNo above: a blank
  // cell CLEARS the stored value when the column exists in the workbook at
  // all (never "preserve on blank") — see PlotImportModal's blank-clears
  // warning, shown only when the file has an update_current_cycle row.
  oracleSupplierCode: string | null;
  oracleInvoice: string | null;
  refAccount: string | null;
  plantingDate: string | null;
  plantCount: number | null;
  expectedYieldFull: string | number | null;
  expectedYieldUnit: string | null;
  // final_plot only (round 8-7A/8-7B) — actual harvest figures echoed back
  // for the preview table. harvestYield/finalYieldAfterClean are Decimal-
  // backed → may come back as a JSON string, always in kilograms (round
  // 8-10B — the unit is fixed server-side and is no longer part of this
  // payload; display it with a fixed "kg" constant, never a field read from
  // here). All null for every other action.
  harvestYield: string | number | null;
  finalYieldAfterClean: string | number | null;
  harvestDate: string | null;
  // Round 8-10B — finalInspectionRecordId is GONE from this payload: it used
  // to be the row's own unvalidated Excel input, and the Excel contract no
  // longer accepts one at all (the server always resolves the cycle's latest
  // active record itself). The record that will actually be snapshotted
  // lives only in PlotImportFinalPlotPreviewStateRow.
  // resolvedFinalInspectionRecordId — internal Preview→Commit binding, never
  // rendered in the UI (round 8-10C — see PlotImportRowResult.finalRecordNote
  // for what the user is told instead).
  finalNote: string | null;
}

export interface PlotImportRowResult {
  rowNumber: number;
  action: string | null;
  supplierCode: string | null;
  plotCode: string | null;
  status: 'valid' | 'error';
  message: string;
  payload: PlotImportRowPayload | null;
  existingPlotId: string | null;
  activeCycleId: string | null;
  // Additive (round 8-2.4/8-2.5) — optional so this stays backward-compatible
  // with any cached/older response shape.
  //   errorCode       — structured reason for an error row (e.g.
  //                     "duplicate_rollover"); the UI must derive the
  //                     DUPLICATE badge from this, never by parsing `message`.
  //   resultCycleNo   — cycle_no created/edited on a successful commit; null
  //                     for preview / errored rows.
  errorCode?: string | null;
  resultCycleNo?: number | null;
  // Additive (round 8-2.7.1) — start_next_cycle only. Read-only server
  // result: this UI must never send it back to force a resolution, and must
  // never derive/guess it client-side (the backend recomputes it fresh under
  // the plot lock at commit time; a preview value is only ever an estimate).
  //   resolvedAction      — "start_new_cycle" or "close_and_start_new_cycle",
  //                         whichever the plot's current state resolves this
  //                         row to. null for every other action, and for a
  //                         start_next_cycle row that errors before resolving.
  //   currentCycleNo/currentCycleLabel — identify the ACTIVE cycle a resolved
  //                         rollover would close.
  resolvedAction?: string | null;
  currentCycleNo?: number | null;
  currentCycleLabel?: string | null;
  // Round 8-5B lot fields — optional/additive. On PREVIEW: lotMode ('auto' |
  // 'manual' | 'preserve') + proposedLotNo. Round 8-12A — an Auto preview
  // reads "{cycleLabel}-{supplierCode}-{pCode}-###", where ### stands for the
  // running number: it is allocated ONLY at commit, server-side, so the
  // client must render proposedLotNo verbatim and never compute a number.
  // On COMMIT: the real committed values resultLotNo/resultLotNoSource/
  // resultLotRunningNo.
  lotMode?: string | null;
  proposedLotNo?: string | null;
  resultLotNo?: string | null;
  resultLotNoSource?: string | null;
  resultLotRunningNo?: number | null;
  // final_plot only (round 8-7A/8-7B) — non-blocking: finalYieldAfterClean >
  // harvestYield. Never sets status to 'error'; the row stays 'valid'/READY
  // and Commit is never blocked by it.
  warning?: string | null;
  // Round 8-10B — final_plot only, purely INFORMATIONAL: whether the server
  // found an inspection record to snapshot when this cycle closes —
  // "พบบันทึกการตรวจที่ใช้สรุป" / "ไม่มีบันทึกการตรวจที่ใช้สรุป". Never the
  // record's id (see PlotImportFinalPlotPreviewStateRow.
  // resolvedFinalInspectionRecordId for the internal binding that carries
  // it). null/undefined for every other action, and for an errored final_plot
  // row that never reached resolution (no active cycle / cycle-label
  // mismatch) — an older response shape that predates this field is also
  // null/undefined here, never a reason to guess.
  finalRecordNote?: string | null;
  // Round 8-9B.1 — plot inspection password, SAFE metadata only. There is
  // deliberately no password/hash/digest/version field anywhere in this type:
  //   inspectionPasswordConfigured — does the plot have one right now?
  //     null = the server didn't look it up (this file touches no passwords).
  //   inspectionPasswordChange — 'set' | 'replace' | null(keep existing).
  // Neither reveals the password, nor even its length.
  inspectionPasswordConfigured?: boolean | null;
  inspectionPasswordChange?: string | null;
}

// Round 8-2.7.2: the read-only optimistic-concurrency expectation the preview
// endpoints return and the client echoes back on commit. NOT authority — the
// backend recomputes the real state under lock and only uses this to detect
// divergence. Stored in component memory only (never localStorage), never
// shown in the UI, and activeCycleId is never surfaced to the user.
export interface PlotImportPreviewStateRow {
  rowNumber: number;
  supplierCode: string;
  plotCode: string;
  resolvedAction: string;
  activeCycleId: string | null;
}

// Round 8-7A.1: final_plot's own preview-state binding — wider than
// PlotImportPreviewStateRow above since final_plot closes a SPECIFIC cycle
// and (optionally) snapshots a SPECIFIC inspection record. NOT authority —
// the backend re-verifies every one of these fields under lock at commit
// time and rejects the commit if any diverged. EVERY field here, including
// resolvedFinalInspectionRecordId, is internal bookkeeping the UI never
// displays (round 8-10C — the id is never shown, not even truncated; the
// Preview table tells the user found/not-found via PlotImportRowResult.
// finalRecordNote instead). This object exists purely so Commit can echo the
// exact state Preview approved back to the backend for re-verification.
export interface PlotImportFinalPlotPreviewStateRow {
  rowNumber: number;
  supplierCode: string;
  plotCode: string;
  plotUpdatedAt: string;
  activeCycleId: string;
  activeCycleNo: number;
  activeCycleUpdatedAt: string;
  cycleLabel: string | null;
  resolvedFinalInspectionRecordId: string | null;
}

// Round 8-9B.1: one entry per row that will set/replace a plot inspection
// password, binding it to the credential state the user was shown. Carries NO
// password, hash or digest — only the version number, which is bookkeeping.
// Never displayed; echoed back verbatim on commit so the backend can reject
// the whole file if someone else changed a password in between.
export interface PlotImportCredentialPreviewStateRow {
  rowNumber: number;
  supplierCode: string;
  plotCode: string;
  plotId: string | null;
  expectedConfigured: boolean;
  expectedCredentialVersion: number | null;
  intendedChange: string;
}

export interface PlotImportPreviewState {
  fileSha256: string;
  startNextRows: PlotImportPreviewStateRow[];
  // Optional/defaulted (round 8-7A.1) — mirrors the backend's own
  // Field(default_factory=list): a pre-8-7A response/test fixture that only
  // sets startNextRows is still a valid PlotImportPreviewState. Treat as []
  // when absent, never as "missing state".
  finalPlotRows?: PlotImportFinalPlotPreviewStateRow[];
  // Optional/defaulted the same way (round 8-9B.1).
  credentialRows?: PlotImportCredentialPreviewStateRow[];
}

export interface PlotImportPreview {
  totalRows: number;
  validRows: number;
  errorRows: number;
  rows: PlotImportRowResult[];
  // Present on the read-only preview endpoints (round 8-2.7.2); absent on the
  // error-preview embedded in a commit's 422 detail.
  previewState?: PlotImportPreviewState | null;
}

export interface PlotImportCommitResult {
  createdPlots: number;
  startedCycles: number;
  updatedCycles: number;
  rolledOverCycles: number;
  // Round 8-7A — count of final_plot rows executed (cycle closed + actual
  // harvest recorded). Additive/required since the backend always returns it
  // (default 0), matching rolledOverCycles' existing style.
  finalizedPlots: number;
  skippedRows: number;
  rowResults: PlotImportRowResult[];
}

export async function previewPlotImport(file: File): Promise<PlotImportPreview> {
  const form = new FormData();
  form.append('file', file);
  // Let the browser set the multipart boundary — never set Content-Type here.
  const res = await apiClient.post<PlotImportPreview>('/api/v1/plots/import/preview', form);
  return res.data;
}

/**
 * The legacy JSON commit endpoint — kept for backward compatibility only;
 * the modal commits through commitPlotImportWithReport (below), never this.
 *
 * `previewState` (round 8-7A.1/8-7B): same contract as
 * commitPlotImportWithReport's — the read-only expectation from the preview
 * response, echoed back verbatim so the backend can reject a start_next_cycle
 * or final_plot commit whose file/plot state diverged from what the user
 * approved. Sent only on commit; omit for files with neither action (legacy
 * compatible). Never construct or mutate this client-side.
 */
export async function commitPlotImport(
  file: File,
  previewState?: PlotImportPreviewState | null,
): Promise<PlotImportCommitResult> {
  const form = new FormData();
  form.append('file', file);
  if (previewState) form.append('previewState', JSON.stringify(previewState));
  const res = await apiClient.post<PlotImportCommitResult>('/api/v1/plots/import/commit', form);
  return res.data;
}

// --- Excel result-workbook variants (round 8-2.4/8-2.5) --------------------
// preview-report / commit-report reuse the SAME validate/commit services as
// the JSON endpoints above and return an .xlsx result file instead of JSON.
// commit-report is the ONE mutation the modal calls to commit — the JSON
// commitPlotImport() above is kept only for backward compatibility and must
// never be called alongside commit-report for the same file/click.

export interface PlotImportReportFile {
  blob: Blob;
  filename: string;
  kind: 'validation' | 'completed';
  httpStatus: number;
}

export type PlotImportCommitReportResponse =
  | { outcome: 'completed'; report: PlotImportReportFile }
  | { outcome: 'blocked'; report: PlotImportReportFile };

/**
 * Thrown by downloadPlotImportPreviewReport/commitPlotImportWithReport (and,
 * round 8-6B, downloadPlotImportTemplate) when the response is NOT a
 * workbook — a file-level error (bad file/columns), a write conflict (409),
 * an unexpected server error, or no response at all.
 *
 * `status`: the HTTP status if a response was received (the backend's
 * transaction has therefore already rolled back on error — see
 * plots.py's commit_plot_import_report, every error path raises BEFORE any
 * commit). `status === null` means NO response arrived (network error/
 * timeout) — in that case the commit's outcome is NOT confirmed; the caller
 * must not claim nothing was imported.
 */
export class PlotImportReportError extends Error {
  status: number | null;
  // Machine-readable error code from the backend's structured `detail.code`
  // (round 8-2.7.2) — currently only 'preview_state_conflict'. Lets the UI
  // branch on the error TYPE without parsing the Thai message. null when the
  // response carried no structured code.
  code: string | null;
  // Round 8-6E — the backend's structured `detail.reason` sub-code for a
  // preview-state conflict (e.g. 'missing_preview_state',
  // 'file_digest_mismatch', 'row_set_mismatch', 'resolution_changed') and
  // `detail.changedRows` (the Excel row numbers whose resolution changed —
  // advisory only, never a cycle id/hash). Both null/empty when the
  // response carried no structured detail beyond message/code.
  reason: string | null;
  changedRows: number[];
  constructor(
    message: string,
    status: number | null,
    code: string | null = null,
    reason: string | null = null,
    changedRows: number[] = [],
  ) {
    super(message);
    this.name = 'PlotImportReportError';
    this.status = status;
    this.code = code;
    this.reason = reason;
    this.changedRows = changedRows;
  }
}

export const PREVIEW_STATE_CONFLICT_CODE = 'preview_state_conflict';

function isXlsxContentType(contentType: unknown): boolean {
  return typeof contentType === 'string' && contentType.includes('spreadsheetml');
}

/** Blob.text() is standard in every real browser; jsdom's Blob (used by this
 * project's vitest test environment) doesn't implement it, so fall back to
 * FileReader (which jsdom does support) rather than relying on a test-only
 * polyfill for production code. */
async function blobToText(blob: Blob): Promise<string> {
  if (typeof blob.text === 'function') return blob.text();
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result ?? ''));
    reader.onerror = () => reject(reader.error);
    reader.readAsText(blob);
  });
}

async function extractErrorDetailFromBlob(blob: Blob): Promise<{
  message: string; code: string | null; reason: string | null; changedRows: number[];
}> {
  try {
    const text = await blobToText(blob);
    const data: unknown = JSON.parse(text);
    if (data && typeof data === 'object' && 'detail' in data) {
      const detail = (data as { detail: unknown }).detail;
      if (typeof detail === 'string') return { message: detail, code: null, reason: null, changedRows: [] };
      if (detail && typeof detail === 'object') {
        const obj = detail as { message?: unknown; code?: unknown; reason?: unknown; changedRows?: unknown };
        const message = typeof obj.message === 'string' ? obj.message : text;
        const code = typeof obj.code === 'string' ? obj.code : null;
        const reason = typeof obj.reason === 'string' ? obj.reason : null;
        const changedRows = Array.isArray(obj.changedRows)
          ? obj.changedRows.filter((n): n is number => typeof n === 'number' && Number.isFinite(n))
          : [];
        return { message, code, reason, changedRows };
      }
    }
    return { message: text || 'เกิดข้อผิดพลาด', code: null, reason: null, changedRows: [] };
  } catch {
    return { message: 'เกิดข้อผิดพลาด', code: null, reason: null, changedRows: [] };
  }
}

/** Strip path separators/control chars and require a plausible .xlsx name —
 * defense-in-depth even though the backend always generates a safe,
 * server-side filename (never derived from the client's upload name). */
function sanitizeDownloadFilename(name: string): string | null {
  const cleaned = name.replace(/[/\\:*?"<>|]/g, '').trim();
  if (!cleaned || !cleaned.toLowerCase().endsWith('.xlsx')) return null;
  return cleaned.slice(0, 200);
}

function filenameFromContentDisposition(headerValue: unknown, fallback: string): string {
  if (typeof headerValue === 'string') {
    const match = /filename="?([^";]+)"?/i.exec(headerValue);
    const safe = match?.[1] ? sanitizeDownloadFilename(match[1]) : null;
    if (safe) return safe;
  }
  return fallback;
}

/** Shared low-level POST: sends the file as multipart, expects an .xlsx blob
 * back for any status in `validStatuses`. Any other outcome — a JSON error
 * body (any status), or no response at all (network/timeout) — is normalized
 * into a thrown PlotImportReportError. Never lets a JSON error blob be
 * mistaken for a workbook. */
async function postForXlsxOrThrow(
  url: string,
  file: File,
  validStatuses: number[],
  extraFields?: Record<string, string>,
): Promise<{ blob: Blob; httpStatus: number; headers: Record<string, unknown> }> {
  const form = new FormData();
  form.append('file', file);
  // Optional extra multipart fields (round 8-2.7.2: previewState on commit).
  // Appended as plain string parts — the browser still sets the boundary.
  if (extraFields) {
    for (const [key, value] of Object.entries(extraFields)) form.append(key, value);
  }
  // Let the browser set the multipart boundary — never set Content-Type here.
  let res;
  try {
    res = await apiClient.post<Blob>(url, form, {
      responseType: 'blob',
      // Accept the "blocked" status too (422 commit-report) as a normal
      // response so we can inspect ITS content-type ourselves, rather than
      // letting axios throw and losing the workbook body.
      validateStatus: (status) => validStatuses.includes(status),
    });
  } catch (error) {
    if (axios.isAxiosError(error) && error.response) {
      const blob = error.response.data as Blob;
      const { message, code, reason, changedRows } = await extractErrorDetailFromBlob(blob);
      throw new PlotImportReportError(message, error.response.status, code, reason, changedRows);
    }
    // No response received at all — network error/timeout. Outcome unknown.
    throw new PlotImportReportError(
      error instanceof Error ? error.message : 'Network error',
      null,
    );
  }
  const headers = res.headers as Record<string, unknown>;
  if (!isXlsxContentType(headers['content-type'])) {
    const { message, code, reason, changedRows } = await extractErrorDetailFromBlob(res.data);
    throw new PlotImportReportError(message, res.status, code, reason, changedRows);
  }
  return { blob: res.data, httpStatus: res.status, headers };
}

/** Read-only: validate the file (same core as previewPlotImport) and return
 * an .xlsx validation report — never writes. */
export async function downloadPlotImportPreviewReport(file: File): Promise<PlotImportReportFile> {
  const { blob, httpStatus, headers } = await postForXlsxOrThrow(
    '/api/v1/plots/import/preview-report', file, [200],
  );
  return {
    blob,
    filename: filenameFromContentDisposition(headers['content-disposition'], 'plot-import-validation.xlsx'),
    kind: 'validation',
    httpStatus,
  };
}

/**
 * THE commit mutation: re-validates server-side and executes every row in
 * ONE transaction (same core as commitPlotImport), then returns an .xlsx
 * result workbook instead of JSON. Call this EXACTLY once per commit click —
 * never alongside commitPlotImport() for the same file.
 *
 *   outcome: 'completed' — HTTP 200, every row committed; report.kind is
 *            'completed'.
 *   outcome: 'blocked'   — HTTP 422, validation failed BEFORE any mutation
 *            (nothing written); report.kind is 'validation'.
 *
 * Any other outcome (409 conflict, JSON 422 file-level error, 500, or no
 * response at all) throws PlotImportReportError — see its docstring for how
 * to read `status` (null means the commit's outcome is NOT confirmed). A
 * preview-state conflict (round 8-2.7.2) surfaces as a thrown
 * PlotImportReportError with `code === PREVIEW_STATE_CONFLICT_CODE`.
 *
 * `previewState` (round 8-2.7.2; extended round 8-7A.1 to also cover
 * final_plot): the read-only expectation from the preview response, echoed
 * back verbatim so the backend can reject a start_next_cycle or final_plot
 * commit whose file or plot state diverged from what the user approved. Sent
 * only on commit; omit for files with neither action (legacy compatible).
 */
export async function commitPlotImportWithReport(
  file: File,
  previewState?: PlotImportPreviewState | null,
): Promise<PlotImportCommitReportResponse> {
  const extraFields = previewState
    ? { previewState: JSON.stringify(previewState) }
    : undefined;
  const { blob, httpStatus, headers } = await postForXlsxOrThrow(
    '/api/v1/plots/import/commit-report', file, [200, 422], extraFields,
  );
  const outcome = httpStatus === 200 ? 'completed' : 'blocked';
  const fallback = httpStatus === 200 ? 'plot-import-result.xlsx' : 'plot-import-validation.xlsx';
  return {
    outcome,
    report: {
      blob,
      filename: filenameFromContentDisposition(headers['content-disposition'], fallback),
      kind: outcome === 'completed' ? 'completed' : 'validation',
      httpStatus,
    },
  };
}

// --- Plot cycles (round 7.1–7.2B) — Supplier → Plot → PlotCycle →
// InspectionRecord. A Plot is the permanent physical field; a PlotCycle is
// one รอบปลูก on it (crop/variety/lot/planting date/plant count/expected
// yield). A plot has AT MOST ONE active cycle at a time; an inspection
// record binds to whichever cycle is active when it's created. Closing a
// cycle (harvested/cancelled) keeps its history and lets a fresh cycle start
// on the same plot — the QR stays on the Plot, never reprinted per cycle.
export type PlotCycleStatus = 'active' | 'harvested' | 'cancelled';

export interface PlotCycle {
  id: string;
  plotId: string;
  cycleNo: number;
  status: PlotCycleStatus;
  crop: string | null;
  variety: string | null;
  // User-facing season name (round 8.0), e.g. "jun2026" — display title for
  // the cycle, distinct from lotNo. null for cycles created before the field.
  cycleLabel: string | null;
  lotNo: string | null;
  // PO / P.Code + Auto Lot metadata (round 8-5A/8-5B; formula V2 round 8-12A).
  // lotNoSource is how lotNo was derived — 'auto' (server-generated
  // {cycleLabel}-{supplierCode}-{pCode}-{running}, e.g.
  // "2605-SUP010-WM-141-003"), 'manual' (user-entered), 'legacy' (pre-8-5A
  // data), or null (no lot / older cycle). lotRunningNo is the Auto Lot's
  // running number (null for manual/legacy). All read-only — the client never
  // sends lotNoSource/lotRunningNo.
  //
  // poNumber is still cycle data but is NOT part of the Auto Lot formula any
  // more (it led V1's). The server's internal series bookkeeping
  // (auto_lot_series_key) is deliberately absent from this contract.
  poNumber: string | null;
  pCode: string | null;
  lotNoSource: 'auto' | 'manual' | 'legacy' | null;
  lotRunningNo: number | null;
  // Round 8-12A — the SUPPLIER's own lot identifier for this cycle, stored
  // beside (never mixed into) lotNo above. Free-form and optional; it takes no
  // part in the Auto Lot formula, the running number, or the Manual/Auto
  // decision. null for every cycle created before the field existed.
  supplierLotNo: string | null;
  // Round 8-21A/8-21B — three independent, OPTIONAL back-office reference
  // fields, stored per-cycle (may legitimately change cycle to cycle).
  // Free-text, trimmed server-side, max 255 chars. No business logic of
  // their own — never part of the Auto Lot formula, never required. null
  // for every cycle created before the field existed (no backfill).
  oracleSupplierCode: string | null;
  oracleInvoice: string | null;
  refAccount: string | null;
  plantingDate: string | null;
  plantCount: number | null;
  // Decimal-backed — see PlotSummary's comment; may come back as a JSON string.
  expectedYieldFull: string | number | null;
  expectedYieldUnit: string | null;
  startedAt: string;
  closedAt: string | null;
  closedById: string | null;
  closeReason: string | null;
  // Frozen final ESTIMATED-yield snapshot taken when the cycle closed (round
  // 8-2.8A). NOT actual harvested yield. All null for cycles closed before the
  // snapshot existed, a cycle closed with no inspection, or any active cycle.
  // Read verbatim — never recompute on the client. Decimal-backed → JSON string.
  finalYieldPct: string | number | null;
  finalEstimatedYield: string | number | null;
  finalInspectionRecordId: string | null;
  // Actual harvested yield (round 8-7A) — the REAL figures recorded when the
  // cycle is finalized via Excel action final_plot, distinct from the
  // ESTIMATE fields above. All null for an active cycle, a cycle closed by
  // any OTHER path (rollover/manual close), or a legacy cycle closed before
  // this field existed. Read verbatim — never recompute on the client.
  // harvestYield/finalYieldAfterClean are Decimal-backed → may be a JSON string.
  harvestYield: string | number | null;
  finalYieldAfterClean: string | number | null;
  finalYieldUnit: string | null;
  harvestDate: string | null;
  finalNote: string | null;
  createdAt: string;
  updatedAt: string;
}

export interface PlotCycleCreatePayload {
  // PO (round 8-13A — OPTIONAL; was required through round 8-12C) / P.Code
  // (round 8-5B — still REQUIRED nonblank) on every "create a new cycle" flow
  // (Start / Create-plot-with-cycle / Rollover.newCycle). poNumber omitted,
  // null, or blank/whitespace all mean "no PO" — the backend stores null and
  // normalize_po_number never raises on it; a nonblank value is upper-cased
  // server-side. The backend still 422s a blank/omitted P.Code. lotNo
  // controls the Auto/Manual lot:
  //   - lotNo omitted/null  → Auto: the backend generates
  //     {cycleLabel}-{supplierCode}-{pCode}-{running}. Round 8-12A.1 — it
  //     REQUIRES a nonblank cycleLabel and pCode (and resolves the supplier
  //     code itself, server-side); a missing one is a 422, never a cycle with
  //     no lot. The PO is NOT part of the formula and was never required by
  //     it (round 8-12A already dropped PO from the formula; round 8-13A
  //     just stopped requiring PO on the request itself).
  //   - lotNo nonblank      → Manual: stored verbatim (Manual wins over Auto),
  //     and needs none of the Auto components (including PO).
  // NEVER send lotNoSource/lotRunningNo/autoLotSeriesKey — those are
  // server-derived and autoLotSeriesKey is never even exposed by the API.
  poNumber?: string | null;
  pCode: string;
  crop?: string | null;
  variety?: string | null;
  // Round 8-17A.1 — REQUIRED (nonblank after trim) on every "create a new
  // cycle" flow this payload backs (Start / Create-plot-with-cycle /
  // Rollover.newCycle / Reactivate-with-cycle), independent of Auto vs
  // Manual lot. The backend's PlotCycleCreate schema enforces the same rule
  // server-side (source of truth); this type just stops the client from
  // constructing a request that can't pass. PlotCycle.cycleLabel (the READ
  // model above) stays `string | null` — historical cycles predating this
  // requirement still report null and must keep rendering.
  cycleLabel: string;
  lotNo?: string | null;
  // Round 8-12A — the Supplier's OWN lot number. Optional and independent:
  // omitting it never affects the system lot. Trimmed server-side; send null
  // (or omit) for "none". Max 100 characters.
  supplierLotNo?: string | null;
  // Round 8-21A/8-21B — three independent, OPTIONAL back-office reference
  // fields. Free-text, max 255 chars, trimmed server-side; send null (or
  // omit) for "none". Never feeds the Auto Lot formula. On EDIT this client
  // always SENDS the field (never omits it, same convention as
  // supplierLotNo) — see PlotCycleModals.toEditPayload: blank clears the
  // stored value, an unchanged value round-trips back verbatim.
  oracleSupplierCode?: string | null;
  oracleInvoice?: string | null;
  refAccount?: string | null;
  plantingDate?: string | null;
  plantCount?: number | null;
  expectedYieldFull?: number | null;
  expectedYieldUnit?: string | null;
}

/** PATCH the active cycle — EVERY field optional (exclude_unset preserve
 * semantics): omit a field to keep its current value. pCode is optional here
 * even though it's required on create (round 8-5B) — omit to preserve, send
 * a value to change. poNumber is optional on BOTH create and edit (round
 * 8-13A); here specifically: omit to preserve, explicit null/blank to clear,
 * a value to change.
 *
 * Lot semantics (round 8-5A; formula V2 round 8-12A/8-12A.1):
 *   - lotNo OMITTED  → the lot is left exactly as it is, even when cycleLabel
 *     or pCode change. Renaming a cycle never silently renumbers its lot.
 *   - lotNo null     → regenerate an Auto Lot from the EFFECTIVE cycleLabel +
 *     pCode (this request's values, else the cycle's own) plus the plot's
 *     supplier. A missing component is a 422 and the existing lot is kept —
 *     the PO is NOT consulted, so a cycle with no PO can still regenerate.
 *   - lotNo nonblank → switch to Manual, stored verbatim.
 *
 * supplierLotNo follows the same exclude_unset rule and NEVER regenerates the
 * system lot: omit to keep, send null/'' to clear, send a value to replace.
 *
 * status, cycleNo, the closed-fields, lotNoSource and lotRunningNo are
 * deliberately absent, as is the server's internal auto-lot series key. */
export type PlotCycleUpdatePayload = Partial<PlotCycleCreatePayload>;

export interface PlotCycleClosePayload {
  status: 'harvested' | 'cancelled';
  closeReason?: string | null;
}

/** Round 8-10A — optional paging for GET /plots/{plotId}/cycles. The backend
 * has always accepted limit/offset (default limit 50, ordered cycle_no DESC);
 * this client simply stopped ignoring them.
 *
 * Both keys are omitted from the request when not supplied, so an existing
 * caller sends the byte-identical URL it sent before and keeps the backend's
 * own defaults. */
export async function listPlotCycles(
  plotId: string,
  options?: { limit?: number; offset?: number },
): Promise<PlotCycle[]> {
  const params: Record<string, number> = {};
  if (options?.limit != null) params.limit = options.limit;
  if (options?.offset != null) params.offset = options.offset;
  const res = await apiClient.get<PlotCycle[]>(
    `/api/v1/plots/${plotId}/cycles`,
    Object.keys(params).length > 0 ? { params } : undefined,
  );
  return res.data;
}

export async function createPlotCycle(
  plotId: string,
  payload: PlotCycleCreatePayload,
): Promise<PlotCycle> {
  const res = await apiClient.post<PlotCycle>(`/api/v1/plots/${plotId}/cycles`, payload);
  return res.data;
}

export async function updatePlotCycle(
  plotId: string,
  cycleId: string,
  payload: PlotCycleUpdatePayload,
): Promise<PlotCycle> {
  const res = await apiClient.patch<PlotCycle>(
    `/api/v1/plots/${plotId}/cycles/${cycleId}`,
    payload,
  );
  return res.data;
}

export async function closePlotCycle(
  plotId: string,
  cycleId: string,
  payload: PlotCycleClosePayload,
): Promise<PlotCycle> {
  const res = await apiClient.post<PlotCycle>(
    `/api/v1/plots/${plotId}/cycles/${cycleId}/close`,
    payload,
  );
  return res.data;
}

/**
 * Rollover (round 7.9B/7.9C) — atomically close the active cycle and open a
 * fresh one, in ONE request. The single-plot equivalent of the Excel
 * close_and_start_new_cycle import action (round 7.8). Callers must NEVER
 * call closePlotCycle then createPlotCycle as two separate requests: a start
 * failure after a successful close would strand the plot with no active
 * cycle — the backend's single transaction is what prevents that, and this
 * is the only client entry point into it.
 */
export interface PlotCycleRolloverPayload {
  closeStatus: 'harvested' | 'cancelled';
  closeReason?: string | null;
  newCycle: PlotCycleCreatePayload;
}

export interface PlotCycleRolloverResult {
  plotId: string;
  activeCycleId: string;
  activeCycleNo: number;
  closedCycle: PlotCycle;
  newCycle: PlotCycle;
}

export async function rolloverPlotCycle(
  plotId: string,
  cycleId: string,
  payload: PlotCycleRolloverPayload,
): Promise<PlotCycleRolloverResult> {
  const res = await apiClient.post<PlotCycleRolloverResult>(
    `/api/v1/plots/${plotId}/cycles/${cycleId}/rollover`,
    payload,
  );
  return res.data;
}

export interface PlotLookupResult {
  plotId: string;
  plotCode: string;
  plotName: string;
  supplierId: string;
  supplierCode: string;
  supplierName: string;
}

export interface PlotLookupParams {
  supplierCode: string;
  plotCode: string;
}

export async function lookupPlotByQr(params: PlotLookupParams): Promise<PlotLookupResult> {
  const res = await apiClient.get<PlotLookupResult>('/api/v1/plots/lookup', { params });
  return res.data;
}

/** Sibling of lookupPlotByQr for the round-20 opaque-qr-key deep link
 * format — same PlotLookupResult shape, keyed by qrKey instead. */
export async function lookupPlotByQrKey(qrKey: string): Promise<PlotLookupResult> {
  const res = await apiClient.get<PlotLookupResult>('/api/v1/plots/lookup-by-qr', {
    params: { qrKey },
  });
  return res.data;
}
