/**
 * PublicInspect — unauthenticated field-assistant flow (round 8-3D rewrite):
 * enter a phone -> pick from every plot that phone may inspect -> choose an
 * inspector type -> fill and submit one record -> optionally inspect the
 * next plot WITHOUT re-entering the phone. No inspection code and no manual
 * Supplier/Plot-code entry anywhere in this UI anymore (see docs/human or the
 * round 8-3D report for the retired inspection-code flow this replaces).
 *
 * phoneAccessSessionToken and inspectionSessionToken live in React state
 * ONLY — never localStorage/sessionStorage/cookies, never put in the URL,
 * never decoded client-side (both are opaque strings as far as this file is
 * concerned; the backend derives everything from them). The phone NUMBER
 * itself is cleared from state the moment a lookup succeeds and is never
 * part of any later screen, the record payload, or a log statement.
 *
 * Round 8-9D adds an optional "รหัสยืนยันแปลง" (plot password) step, driven
 * entirely by GET /public/inspection-access/config — the backend's runtime
 * answer to "is a password required right now". The page never guesses that
 * from a build-time env var (the bundle and the API ship separately), never
 * renders an entry form before the answer arrives, and never falls back to
 * phone-only when the answer fails to arrive. The plaintext password lives in
 * entry-step React state only, is cleared the moment a lookup succeeds, and is
 * subject to every storage rule the tokens above already follow.
 *
 * QR (round 20 hardening) still works: scanning (or a `?qr=`/legacy
 * `?supplierCode&plotCode=` deep link) never skips the phone step — it just
 * carries a locator into the phone lookup (qrKey for the modern opaque
 * format) or, for a legacy sign, is matched against the phone's OWN plot list
 * client-side after lookup (the backend has no supplierCode/plotCode
 * parameter on this endpoint) — assignment governs access either way, never
 * the QR alone.
 */
import { useEffect, useMemo, useRef, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { useSearchParams } from 'react-router-dom';
import axios from 'axios';
import {
  QrCode, Loader2, CheckCircle2, Navigation, Search, Circle, Ban,
  Wifi, WifiOff, CloudOff, Eye, EyeOff,
} from 'lucide-react';
import { LazyPlotQrScan } from '../../components/farmlog/LazyPlotQrScan';
import { PublicMasterDataButtons } from '../../components/farmlog/PublicMasterDataButtons';
import { OfflineInspectionQueuePanel } from '../../components/farmlog/OfflineInspectionQueuePanel';
import { parsePlotQr, parseDeepLinkParams, type PlotQrLocator } from '../../lib/plot-qr';
import { normalizeThaiMobile } from '../../lib/phone';
import { bangkokToday } from '../../lib/business-date';
import {
  createPublicInspectionRecord,
  createPublicRecordWithPhotos,
  buildOfflinePublicRecordPayload,
  type PublicRecordCreateResult,
  type PublicInspectionFormFields,
  type OfflineSubmissionIdentity,
} from '../../api/publicInspection';
import {
  lookupPublicInspectionAccess,
  listPublicInspectionAccessPlots,
  selectPublicInspectionPlot,
  getPublicInspectionAccessConfig,
  type PublicInspectorType,
  type PublicPhoneAccessPlot,
  type PublicSelectPlotResult,
} from '../../api/publicInspectionAccess';
import {
  PhotoSlotPicker,
  emptyPhotoSlots,
} from '../../components/farmlog/PhotoSlotPicker';
import { ProtocolScoreInputs } from '../../components/farmlog/ProtocolScoreInputs';
import {
  fetchPublicInspectionProtocols,
  findProtocolForStage,
  missingProtocolScores,
} from '../../api/inspectionProtocols';
import {
  INSPECTOR_TYPE_OPTIONS,
  inspectorTypeLabel as sharedInspectorTypeLabel,
} from '../../lib/inspection-attribution';
import { formatFixed, toNumberOrNull } from '../../lib/numeric';
import { computeInitialYieldValue, targetToKg, validateYieldQuantityKg } from '../../lib/yield-planning';
import { YieldQuantityInput } from '../../components/farmlog/YieldQuantityInput';
import { useNetworkStatus } from '../../hooks/useNetworkStatus';
import {
  deleteOfflineInspectionDraft,
  countOfflineInspectionDrafts,
  purgeExpiredOfflineInspectionDrafts,
  clearOfflinePublicAccessCache,
} from '../../lib/offline-inspection-store';
import { extractOfflineErrorCode, describeOfflineErrorCode } from '../../lib/offline-submission-errors';

// Round 8-4H.1 — Offline inspection creation is temporarily disabled;
// /public/inspect is Online-only until offline is redeveloped. See module
// docstring additions below and the round's Final Report for the full
// rationale ("เข้าสู่ระบบแล้ว แต่เตรียมข้อมูลออฟไลน์ไม่สำเร็จ" instability).
type Step = 'phone' | 'plots' | 'form' | 'success';

/** Round 8-4C.1 Part D — the read-only plot/supplier/cycle/yield-plan data a
 * form needs to render, deliberately WITHOUT inspectionSessionToken/
 * expiresIn. Those two live ONLY in their own standalone `inspectionSessionToken`
 * state, so there is no field on `plotInfo` a caller could ever mistakenly
 * read a token from — and building an offline-cached plot's context can
 * never need a placeholder/empty-string token to satisfy the type (round
 * 8-4C's buildOfflinePlotInfo used to set inspectionSessionToken: '' just to
 * satisfy PublicSelectPlotResult; that's now structurally impossible). */
export type PublicInspectionPlotContext = Omit<PublicSelectPlotResult, 'inspectionSessionToken' | 'expiresIn'>;

/** Strips the token/expiresIn off a fresh select-plot result — the ONLY
 * place the online path constructs a PublicInspectionPlotContext, so the two
 * fields this type deliberately excludes are dropped in exactly one spot. */
function toPlotContext(result: PublicSelectPlotResult): PublicInspectionPlotContext {
  const { inspectionSessionToken: _token, expiresIn: _expiresIn, ...context } = result;
  return context;
}

const emptyText = '—';

// Round 8-4H.1 — the ONE offline-blocked message for every entry point that
// requires a live network call (phone lookup, plot selection): never a
// cache-derived fallback anymore. Kept as a single constant so every block
// site shows byte-identical copy.
const OFFLINE_BLOCKED_MESSAGE = 'ขณะนี้ไม่มีการเชื่อมต่ออินเทอร์เน็ต กรุณาเชื่อมต่อก่อนค้นหาและบันทึกการตรวจแปลง';
// Distinct message specifically for the submit button — the user has
// already filled a form; this must say "not yet saved", not "can't search".
const OFFLINE_SUBMIT_BLOCKED_MESSAGE = 'ยังไม่สามารถบันทึกได้ กรุณาเชื่อมต่ออินเทอร์เน็ตแล้วลองอีกครั้ง';

// --- Round 8-9D: plot password ("รหัสยืนยันแปลง") ---------------------------
//
// Whether this is required at all is decided by the BACKEND at runtime
// (GET /public/inspection-access/config), never by a build-time env var — see
// the config query below for why. Everything in this block is dormant while
// passwordRequired is false.

/** Fail-safe copy for a capability probe that didn't answer. Deliberately does
 * NOT offer to continue without a password: the backend may already be
 * enforcing one, and a phone-only lookup would then fail with a confusing
 * "not authorized" instead of an honest "we couldn't set this page up". */
const CONFIG_ERROR_MESSAGE = 'ไม่สามารถเตรียมหน้าตรวจแปลงได้ กรุณาลองใหม่อีกครั้ง';

/** ONE combined message for the enforcement-mode 404 — the backend already
 * folds "unknown number", "wrong password", "no credential" and "not
 * authorized" into a single generic response (round 8-9C); saying which one it
 * was here would rebuild client-side the enumeration oracle the backend
 * deliberately avoids. */
const AUTH_FAILED_MESSAGE = 'หมายเลขหรือรหัสยืนยันแปลงไม่ถูกต้อง หรือยังไม่ได้รับอนุญาตให้เข้าตรวจ';

const PASSWORD_REQUIRED_MESSAGE = 'กรุณากรอกรหัสยืนยันแปลง';

/** Strips everything that isn't an ASCII digit and caps the length, so the
 * value in state can never be something the backend policy rejects for a
 * reason the user can't see. `[0-9]` not `\d` — the backend rejects Thai
 * digits ๐-๙ and full-width forms too (app/auth/plot_access_password.py). */
export function sanitizePlotPassword(raw: string, maxLength: number): string {
  return raw.replace(/[^0-9]/g, '').slice(0, Math.max(0, maxLength));
}

/** UX-layer only — the backend stays the authority (it re-validates with the
 * shared policy and folds a failure into the same generic error). Returns ''
 * when the value is acceptable. */
export function validatePlotPassword(
  value: string, minLength: number, maxLength: number,
): string {
  if (!value) return PASSWORD_REQUIRED_MESSAGE;
  if (!new RegExp(`^[0-9]{${minLength},${maxLength}}$`).test(value)) {
    return `รหัสยืนยันแปลงต้องเป็นตัวเลข ${minLength} ถึง ${maxLength} หลัก`;
  }
  return '';
}

const EMPTY_FIELDS: PublicInspectionFormFields = {
  submittedByName: '',
  growthStage: '',
  // Round 8-8B — null until a plot with a comparable kg target is selected
  // (fieldsForSelectedPlot below); never a faked 100% (contract #12).
  yieldQuantityKg: null, yieldPct: null, weatherCondition: '',
  fieldPrepScore: null, weatherScore: null, careScore: null, varietyResistanceScore: null,
  recommendation: '', notes: '',
  latitude: null, longitude: null,
};

/** Round 8-11A — options and labels now come from the SHARED mapping in
 * lib/inspection-attribution.ts, the same one RecordPreview and PlotDetail's
 * inspection history read, so the choice a user makes here and the label they
 * see on the saved record can never drift apart. This page previously kept its
 * own second copy of the list. */
function inspectorTypeLabel(v: PublicInspectorType | null): string {
  return sharedInspectorTypeLabel(v) ?? emptyText;
}

/** Display name of a selected cycle — the admin-chosen season label leads,
 * falling back to "รอบที่ N" for cycles with no label (round 8-0.6 rule,
 * unchanged by this round). */
function cycleDisplayName(info: { cycleLabel: string | null; cycleNo: number | null } | null): string {
  const label = info?.cycleLabel?.trim();
  if (label) return label;
  if (info?.cycleNo != null) return `รอบที่ ${info.cycleNo}`;
  return emptyText;
}

/** Thai short date, same options already used inline for plantingDate /
 * lastInspectedAt in this file. Round 8-19: a DATE-ONLY string ("YYYY-MM-DD")
 * is split and rebuilt as a LOCAL date on purpose — `new Date('2026-08-13')`
 * parses as UTC midnight, which renders as the previous day for any viewer
 * behind UTC. Never shows a time. */
export function formatThaiShortDate(value: string): string {
  const dateOnly = /^(\d{4})-(\d{2})-(\d{2})$/.exec(value);
  const d = dateOnly
    ? new Date(Number(dateOnly[1]), Number(dateOnly[2]) - 1, Number(dateOnly[3]))
    : new Date(value);
  if (Number.isNaN(d.getTime())) return emptyText;
  return d.toLocaleDateString('th-TH', { day: 'numeric', month: 'short', year: 'numeric' });
}

/**
 * Pure dirty-check (round 8-3D Part G) — exported for direct unit testing.
 * A photo picked, OR any inspection field differing from the baseline taken
 * when the form was entered, counts as dirty. GPS (latitude/longitude) is
 * DELIBERATELY excluded — it's auto-requested the moment the form opens, so
 * its mere presence must never by itself trigger the "unsaved changes"
 * confirm (matching the pre-8-3D UX, which never gated on GPS either).
 */
export function computeIsFormDirty(
  fields: PublicInspectionFormFields,
  baseline: PublicInspectionFormFields,
  photos: (File | null)[],
): boolean {
  if (photos.some((p) => p !== null)) return true;
  const keys: (keyof PublicInspectionFormFields)[] = [
    'submittedByName', 'growthStage', 'yieldPct', 'yieldQuantityKg',
    'weatherCondition', 'fieldPrepScore', 'weatherScore', 'careScore',
    'varietyResistanceScore', 'recommendation', 'notes',
  ];
  return keys.some((k) => fields[k] !== baseline[k]);
}

const inputCls = 'w-full rounded-md border border-gray-300 px-3 py-3 text-base shadow-sm focus:border-green-500 focus:outline-none focus:ring-1 focus:ring-green-500';
const primaryBtnCls = 'flex w-full items-center justify-center gap-2 rounded-md bg-green-600 px-4 py-4 text-base font-semibold text-white shadow-sm hover:bg-green-700 disabled:opacity-50';
const secondaryBtnCls = 'w-full rounded-md border border-gray-300 px-4 py-3 text-sm font-medium text-gray-700 hover:bg-gray-50';

/** Wraps `children` INSIDE the `<label>` (the native "wrapping label"
 * pattern) so every field is properly labelled for assistive tech without
 * needing separate htmlFor/id bookkeeping per input. */
function Field({ label, required, children }: { label: string; required?: boolean; children: React.ReactNode }) {
  return (
    <label className="block">
      <span className="mb-1 block text-sm font-medium text-gray-700">
        {label}{required && <span className="ml-1 text-red-500">*</span>}
      </span>
      {children}
    </label>
  );
}

/** Like Field, but for a group of several controls (e.g. option-button chips)
 * rather than a single input — a <label> may only wrap ONE labelable control,
 * so wrapping a button group in <label> corrupts every button's computed
 * accessible name. Uses role="group" + aria-label instead. */
function GroupField({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div>
      <span className="mb-1 block text-sm font-medium text-gray-700">{label}</span>
      <div role="group" aria-label={label}>
        {children}
      </div>
    </div>
  );
}

function PlotStatusBadge({ plot }: { plot: PublicPhoneAccessPlot }) {
  if (!plot.canInspect) {
    return (
      <span className="inline-flex items-center gap-1 rounded-full bg-gray-100 px-2.5 py-1 text-xs font-medium text-gray-500">
        <Ban className="h-3.5 w-3.5" /> ยังไม่มีรอบปลูกที่เปิดอยู่
      </span>
    );
  }
  if (plot.inspectedToday) {
    return (
      <span className="inline-flex items-center gap-1 rounded-full bg-blue-50 px-2.5 py-1 text-xs font-medium text-blue-700">
        <CheckCircle2 className="h-3.5 w-3.5" /> ตรวจแล้ววันนี้
      </span>
    );
  }
  return (
    <span className="inline-flex items-center gap-1 rounded-full bg-green-50 px-2.5 py-1 text-xs font-medium text-green-700">
      <Circle className="h-3.5 w-3.5" /> พร้อมตรวจ
    </span>
  );
}

function PlotCard({
  plot, isQrMatch, selecting, blockedByOtherSelect, onSelect,
}: {
  plot: PublicPhoneAccessPlot;
  isQrMatch: boolean;
  selecting: boolean;
  blockedByOtherSelect: boolean;
  onSelect: () => void;
}) {
  const disabled = !plot.canInspect || blockedByOtherSelect;
  return (
    <button
      type="button"
      disabled={disabled}
      onClick={onSelect}
      className={`w-full rounded-lg border p-4 text-left shadow-sm transition-colors ${
        isQrMatch ? 'border-green-500 ring-2 ring-green-200' : 'border-gray-200'
      } ${disabled ? 'cursor-not-allowed bg-gray-50 opacity-60' : 'bg-white hover:border-green-400'}`}
    >
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0">
          <p className="truncate font-semibold text-gray-900">{plot.plotCode} — {plot.plotName}</p>
          <p className="truncate text-xs text-gray-500">{plot.supplierCode} — {plot.supplierName}</p>
        </div>
        {selecting && <Loader2 className="h-4 w-4 shrink-0 animate-spin text-green-600" />}
      </div>
      <p className="mt-1.5 text-xs text-gray-500">
        {plot.cycleLabel || (plot.cycleNo != null ? `รอบที่ ${plot.cycleNo}` : 'ยังไม่มีรอบปลูก')}
        {plot.crop ? ` · ${plot.crop}` : ''}{plot.variety ? ` (${plot.variety})` : ''}
      </p>
      {/* Round 8-3K — Lot/planting-date, compact "Lot:" label (see docs/human
          or the round report for the full-page "เลขล็อต (Lot No.)" label used
          elsewhere). Sourced from the active cycle only (never re-derived);
          the whole line is omitted (not a dangling "—") when the plot has no
          active cycle at all, since PlotStatusBadge below already says so. */}
      {(plot.lotNo || plot.plantingDate) && (
        <p className="mt-0.5 text-xs text-gray-500">
          {[
            plot.lotNo ? `Lot: ${plot.lotNo}` : null,
            plot.plantingDate
              ? `ปลูก: ${new Date(plot.plantingDate).toLocaleDateString('th-TH', { day: 'numeric', month: 'short', year: 'numeric' })}`
              : null,
          ].filter(Boolean).join(' · ')}
        </p>
      )}
      <div className="mt-2 flex flex-wrap items-center gap-1.5">
        <PlotStatusBadge plot={plot} />
        {isQrMatch && (
          <span className="inline-flex items-center rounded-full bg-green-100 px-2 py-0.5 text-xs font-medium text-green-800">
            จาก QR
          </span>
        )}
      </div>
      {/* Round 8-19 — latest inspection WITHIN the active cycle, under the
          badge. Only rendered when the plot HAS an active cycle: with none,
          the badge already says "ยังไม่มีรอบปลูกที่เปิดอยู่" and showing a date
          there could only be a closed cycle's (backend sends null anyway).
          break-words so a long cycle label wraps instead of overflowing on a
          narrow phone. */}
      {plot.canInspect && (
        <p className="mt-1.5 break-words text-xs text-gray-600">
          {plot.lastInspectionDate
            ? `ตรวจล่าสุดในรอบ ${cycleDisplayName(plot)}: ${formatThaiShortDate(plot.lastInspectionDate)}`
            : 'ยังไม่มีการตรวจในรอบนี้'}
        </p>
      )}
    </button>
  );
}

function InspectorTypeControl({
  value, onChange, error, groupRef,
}: {
  value: PublicInspectorType | null;
  onChange: (v: PublicInspectorType) => void;
  error?: string;
  groupRef?: React.Ref<HTMLDivElement>;
}) {
  return (
    <div>
      <p id="inspector-type-label" className="mb-2 text-sm font-medium text-gray-700">เข้าตรวจในฐานะ</p>
      <div
        ref={groupRef}
        role="radiogroup"
        aria-labelledby="inspector-type-label"
        tabIndex={-1}
        className="grid grid-cols-3 gap-2"
      >
        {INSPECTOR_TYPE_OPTIONS.map((opt) => (
          <label
            key={opt.value}
            className={`flex cursor-pointer items-center justify-center rounded-md border px-3 py-2.5 text-sm font-medium transition-colors ${
              value === opt.value ? 'border-green-600 bg-green-50 text-green-700' : 'border-gray-300 text-gray-600 hover:bg-gray-50'
            }`}
          >
            <input
              type="radio"
              name="inspectorType"
              value={opt.value}
              checked={value === opt.value}
              onChange={() => onChange(opt.value)}
              className="sr-only"
            />
            {opt.label}
          </label>
        ))}
      </div>
      {error && <p role="alert" className="mt-1 text-xs text-red-600">{error}</p>}
    </div>
  );
}

export function PublicInspect() {
  const [searchParams] = useSearchParams();
  // Read once at mount — never re-derived from a later searchParams change.
  const initialDeepLinkRef = useRef<PlotQrLocator | null>(parseDeepLinkParams(searchParams));

  const [step, setStep] = useState<Step>('phone');

  // --- Offline queue (round 8-4B) -------------------------------------------
  // navigator.onLine + window online/offline events — a best-effort signal,
  // NOT proof the API is reachable (see useNetworkStatus's own docstring).
  const isOnline = useNetworkStatus();
  const [pendingCount, setPendingCount] = useState(0);
  const [indexedDbAvailable, setIndexedDbAvailable] = useState(true);
  const [queuePanelOpen, setQueuePanelOpen] = useState(false);
  const [purgedNotice, setPurgedNotice] = useState('');
  // The idempotency identity for the CURRENTLY OPEN form — created once, on
  // the first submit attempt, and reused for every retry of that same form
  // (round 8-4B Part 4). A REF, not state (round 8-4C): ensureSubmissionIdentity
  // can be called twice in the same synchronous tick (e.g. a double-click
  // before React commits the disabled-button re-render) — a ref is updated
  // synchronously, so the SECOND call sees the identity the first call just
  // wrote, guaranteeing one UUID per form no matter how it's invoked. Never
  // rendered directly, so no useState is needed for it at all. Reset only
  // when the backend confirms success, the user starts another plot's
  // inspection, or confirms abandoning the draft — never merely because a
  // token expired.
  const submissionIdentityRef = useRef<OfflineSubmissionIdentity | null>(null);
  // Round 8-4C — blocks a second handleSubmit invocation from starting before
  // React has committed the `disabled={submitting}` re-render (the same
  // double-click race as above, but for the whole submit handler rather than
  // just identity creation).
  const submitInFlightRef = useRef(false);

  // --- QR (round 8-3D Part D) --------------------------------------------
  const [qrOpen, setQrOpen] = useState(false);
  const [qrTarget, setQrTarget] = useState<PlotQrLocator | null>(initialDeepLinkRef.current);
  const [qrMatchNote, setQrMatchNote] = useState('');

  // --- Phone entry ---------------------------------------------------------
  const [phoneInput, setPhoneInput] = useState('');
  const [phoneLookupLoading, setPhoneLookupLoading] = useState(false);
  const [phoneLookupError, setPhoneLookupError] = useState('');

  // --- Round 8-9D: plot password ------------------------------------------
  // Plaintext lives HERE and nowhere else: React state on the entry step,
  // cleared the instant a lookup succeeds. Never localStorage/sessionStorage/
  // IndexedDB, never an offline draft, never a React Query key or cached
  // request object, never the URL, never a log. It is passed straight into the
  // one POST body that consumes it and then dropped.
  const [passwordInput, setPasswordInput] = useState('');
  const [showPassword, setShowPassword] = useState(false);
  const [passwordError, setPasswordError] = useState('');

  // --- Phone session (persists across every plot picked this visit) -------
  const [phoneAccessSessionToken, setPhoneAccessSessionToken] = useState<string | null>(null);
  const [accessiblePlots, setAccessiblePlots] = useState<PublicPhoneAccessPlot[]>([]);
  const [qrMatchedPlotId, setQrMatchedPlotId] = useState<string | null>(null);
  const [selectedInspectorType, setSelectedInspectorType] = useState<PublicInspectorType | null>(null);
  const [inspectorTypeError, setInspectorTypeError] = useState('');
  const [plotSearch, setPlotSearch] = useState('');
  const roleGroupRef = useRef<HTMLDivElement>(null);

  // --- Plot selection --------------------------------------------------------
  const [selectingPlotId, setSelectingPlotId] = useState<string | null>(null);
  const [selectError, setSelectError] = useState('');

  // --- Selected plot / inspection session ----------------------------------
  const [selectedPlotId, setSelectedPlotId] = useState<string | null>(null);
  const [inspectionSessionToken, setInspectionSessionToken] = useState<string | null>(null);
  const [plotInfo, setPlotInfo] = useState<PublicInspectionPlotContext | null>(null);

  // --- Record form -----------------------------------------------------------
  // submittedByName is RETAINED across plots within a phone session (round
  // 8-3D Part F/H) to speed up multi-plot inspection — but must start blank
  // for the very first plot, and stay fully editable every time.
  // submittedByCode is retired (round 8-3G) — the name is now the only
  // (optional) field-attribution input.
  const [retainedSubmittedByName, setRetainedSubmittedByName] = useState('');
  const [fields, setFields] = useState<PublicInspectionFormFields>(EMPTY_FIELDS);
  const [formBaseline, setFormBaseline] = useState<PublicInspectionFormFields>(EMPTY_FIELDS);
  const set = <K extends keyof PublicInspectionFormFields>(key: K, value: PublicInspectionFormFields[K]) =>
    setFields((prev) => ({ ...prev, [key]: value }));
  const [gpsLoading, setGpsLoading] = useState(false);
  const [gpsError, setGpsError] = useState('');
  const [photos, setPhotos] = useState<(File | null)[]>(emptyPhotoSlots());
  // Round 8-14B — true while PhotoSlotPicker has at least one slot mid
  // client-side compression; see RecordForm's identical field for the same
  // "never rely on disabled alone" rationale.
  const [photoProcessing, setPhotoProcessing] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [submitError, setSubmitError] = useState('');
  const [submitNotice, setSubmitNotice] = useState('');

  // --- Success ---------------------------------------------------------------
  const [submittedRecord, setSubmittedRecord] = useState<PublicRecordCreateResult | null>(null);

  // Round 8-8B — the active cycle's kg target (same shared helper RecordForm
  // uses, contract #11) + live inline validation for the kg input, recomputed
  // on every keystroke; handleSubmit re-checks the same thing as a hard gate.
  const yieldTargetKg = targetToKg(plotInfo?.expectedYieldFull, plotInfo?.expectedYieldUnit);
  const yieldFieldError = validateYieldQuantityKg(fields.yieldQuantityKg, yieldTargetKg);

  /** Round 8-9D — the runtime capability probe. The page may not render an
   * entry form until this answers, because the ONE thing it decides (is a plot
   * password required?) changes what the form must collect.
   *
   * Never inferred from import.meta.env: the bundle and the API are deployed
   * separately, so a cached bundle could easily be older than the flag flip.
   *
   * networkMode 'always' on purpose — the default pauses a query whenever
   * navigator.onLine reads false, which would leave this page stuck on the
   * loading state forever behind a captive portal or a browser that reports
   * onLine wrongly. Better to attempt it, fail, and show the retry screen.
   *
   * The response holds no secret, so having it in the React Query cache is
   * safe — the PASSWORD never goes near this or any other query.
   *
   * Round 8-9E — the caching here is deliberately as weak as it can be without
   * polling. Round 8-9D used a 5-minute staleTime, which meant a tab left open
   * across a flag flip could keep showing the phone-only form (and keep sending
   * password-less lookups that now 404) for up to five minutes. So:
   *   staleTime 0            — the answer is never considered current
   *   gcTime 0               — nothing survives an unmount, so a remount always
   *                            starts from the loading gate, never from a
   *                            cached posture
   *   refetchOnMount 'always'/refetchOnWindowFocus 'always' — the two moments a
   *                            field user actually comes back to this page
   * and NO refetchInterval: polling a public endpoint from every open phone
   * costs the backend far more than it buys, and the mode-flip effect below
   * already recovers correctly whenever an answer does arrive. */
  const configQuery = useQuery({
    queryKey: ['public-inspection-access-config'],
    queryFn: getPublicInspectionAccessConfig,
    staleTime: 0,
    gcTime: 0,
    refetchOnMount: 'always',
    refetchOnWindowFocus: 'always',
    networkMode: 'always',
  });
  const accessConfig = configQuery.data ?? null;
  // A background refetch that fails must NOT throw the user out of a session
  // they already have — the error screen is only for "we never got an answer".
  const configFailed = accessConfig === null && configQuery.isError;
  const configLoading = accessConfig === null && !configQuery.isError;
  const passwordRequired = accessConfig?.passwordRequired === true;
  const passwordMinLength = accessConfig?.passwordMinLength ?? 4;
  const passwordMaxLength = accessConfig?.passwordMaxLength ?? 20;

  /** "เปลี่ยนหมายเลข" is a lie once a password is also required — the button
   * clears BOTH. One derived label so every step says the same thing. */
  const changeIdentityLabel = passwordRequired ? 'เปลี่ยนหมายเลขหรือรหัส' : 'เปลี่ยนหมายเลข';
  const sessionExpiredMessage = passwordRequired
    ? 'เซสชันหมดอายุหรือข้อมูลเข้าตรวจมีการเปลี่ยนแปลง กรุณากรอกหมายเลขและรหัสอีกครั้ง'
    : 'เซสชันหมดอายุ กรุณากรอกหมายเลขอีกครั้ง';

  const {
    data: protocols,
    isLoading: protocolsLoading,
    isError: protocolsError,
  } = useQuery({
    queryKey: ['public-inspection-protocols'],
    queryFn: fetchPublicInspectionProtocols,
    staleTime: 5 * 60_000,
  });
  const stageProtocol = findProtocolForStage(protocols, fields.growthStage || null);

  const visiblePlots = useMemo(() => {
    const q = plotSearch.trim().toLowerCase();
    let list = q
      ? accessiblePlots.filter((p) => (
        p.plotCode.toLowerCase().includes(q)
        || p.plotName.toLowerCase().includes(q)
        || p.supplierCode.toLowerCase().includes(q)
        || p.supplierName.toLowerCase().includes(q)
        || (p.cycleLabel ?? '').toLowerCase().includes(q)
        || (p.crop ?? '').toLowerCase().includes(q)
        || (p.variety ?? '').toLowerCase().includes(q)
        || (p.lotNo ?? '').toLowerCase().includes(q)
      ))
      : [...accessiblePlots];
    if (qrMatchedPlotId) {
      const idx = list.findIndex((p) => p.plotId === qrMatchedPlotId);
      if (idx > 0) {
        const [matched] = list.splice(idx, 1);
        list = [matched, ...list];
      }
    }
    return list;
  }, [accessiblePlots, plotSearch, qrMatchedPlotId]);

  function freshFieldsFor(submittedByName: string): PublicInspectionFormFields {
    return { ...EMPTY_FIELDS, submittedByName };
  }

  /** Round 8-3J, kg-first round 8-8B: default the kg/Yield% pair from the
   * active cycle's target (result.expectedYieldFull/Unit) + the plot's
   * latest inspection snapshot (result.currentYieldPct — same source as the
   * logged-in RecordForm's plots.current_yield_pct), via the SAME shared
   * lib/yield-planning.ts helper RecordForm uses (contract #11) — never a
   * faked 100% when there's no comparable kg target (contract #12). Called
   * once, imperatively, right where the select-plot result arrives
   * (handlePlotClick) — never via a background-refetching effect that could
   * clobber a value the user already adjusted. */
  function fieldsForSelectedPlot(
    submittedByName: string,
    result: PublicInspectionPlotContext,
  ): PublicInspectionFormFields {
    const initial = computeInitialYieldValue(
      result.expectedYieldFull, result.expectedYieldUnit, result.currentYieldPct,
    );
    return {
      ...EMPTY_FIELDS,
      submittedByName,
      yieldQuantityKg: initial.quantityKg,
      yieldPct: initial.yieldPct,
    };
  }

  function resetPlotDraft(submittedByName: string) {
    setSelectedPlotId(null);
    setInspectionSessionToken(null);
    setPlotInfo(null);
    const fresh = freshFieldsFor(submittedByName);
    setFields(fresh);
    setFormBaseline(fresh);
    setPhotos(emptyPhotoSlots());
    // Defensive — PhotoSlotPicker unmounts with the 'form' step and remounts
    // fresh for the next plot (its own mount effect reports false again
    // regardless), but reset explicitly too so there's never a window where
    // a stale true could gate the NEXT plot's submit button.
    setPhotoProcessing(false);
    setGpsError('');
    setSubmitError('');
    setSubmitNotice('');
    setSelectError('');
    // Round 8-4B Part 4 rule 8: the submission identity belongs to ONE plot's
    // draft — cleared whenever that draft is abandoned/superseded (starting
    // another plot, confirming a dirty-leave, or after a confirmed success).
    // NEVER cleared merely because a token expired (that path never calls
    // this function — see handleInspectionTokenExpiredAtSubmit).
    submissionIdentityRef.current = null;
  }

  /** Round 8-4B Part 4 (hardened round 8-4C) — returns the identity for the
   * currently-open form, creating it once on the FIRST call (before the
   * first API attempt) and returning the SAME identity on every later call
   * for this same form (a retry, a network-failure queue, a double-click
   * before re-render, etc.) — never a new UUID. Backed by a ref (not state)
   * specifically so two synchronous calls in the same tick can never race
   * into two different identities — see submissionIdentityRef's own comment. */
  function ensureSubmissionIdentity(): OfflineSubmissionIdentity {
    if (submissionIdentityRef.current) return submissionIdentityRef.current;
    const identity: OfflineSubmissionIdentity = {
      clientSubmissionId: crypto.randomUUID(),
      capturedAt: new Date().toISOString(),
      capturedPlotCycleId: plotInfo?.plotCycleId ?? '',
    };
    submissionIdentityRef.current = identity;
    return identity;
  }

  async function refreshPendingCount() {
    try {
      setPendingCount(await countOfflineInspectionDrafts());
      setIndexedDbAvailable(true);
    } catch {
      // IndexedDB unavailable in this browsing context (old browser, blocked
      // private mode, etc.) — surface the warning banner, never crash.
      setIndexedDbAvailable(false);
    }
  }

  // On mount: purge drafts older than the backend's 7-day window (round
  // 8-4A), then load the pending count. A purge failure or an unavailable
  // IndexedDB never blocks the rest of the page — the online flow works
  // exactly as before regardless.
  useEffect(() => {
    (async () => {
      try {
        const purged = await purgeExpiredOfflineInspectionDrafts(new Date());
        if (purged > 0) {
          setPurgedNotice(`ลบรายการที่เก็บไว้เกิน 7 วันออกจากเครื่องแล้ว ${purged} รายการ`);
        }
      } catch {
        // handled generically by refreshPendingCount's own try/catch below
      } finally {
        await refreshPendingCount();
      }
    })();
    // Round 8-4H.1 Part C — best-effort cleanup of the NOW-RETIRED public
    // access plot cache (round 8-4H). Fire-and-forget, never awaited by
    // anything else on this page: a failure here must never block or delay
    // the rest of startup. Deliberately does NOT touch inspection_drafts
    // (a completely separate IndexedDB object store) and does NOT lower
    // OFFLINE_DB_VERSION — the store itself, and the v1→v2→v3 migration
    // logic, stay in lib/offline-inspection-store.ts so a browser that
    // already opened the v3 database can still open it (see that module's
    // docstring for the full "why keep the store" rationale).
    clearOfflinePublicAccessCache().catch(() => {});
  }, []);

  /** The ONE function that ends an access session — "เปลี่ยนหมายเลข(หรือรหัส)",
   * a 401 from any endpoint, and a capability-mode flip all go through it, so
   * there is exactly one place that has to remember every piece of secret and
   * session state. Deliberately does NOT touch pending offline drafts (round
   * 8-4B): those belong to the device, not to this session — and the password
   * was never in one to begin with. */
  function clearPhoneSession() {
    setPhoneAccessSessionToken(null);
    setAccessiblePlots([]);
    setQrMatchedPlotId(null);
    setQrTarget(null);
    setQrMatchNote('');
    setSelectedInspectorType(null);
    setInspectorTypeError('');
    setPlotSearch('');
    setRetainedSubmittedByName('');
    resetPlotDraft('');
    setSubmittedRecord(null);
    setPhoneInput('');
    setPhoneLookupError('');
    // Round 8-9D — the plaintext password and its reveal state die with the
    // session too; a fresh entry screen must never show the previous one.
    setPasswordInput('');
    setShowPassword(false);
    setPasswordError('');
  }

  /** Round 8-9D — the backend flipped enforcement while this tab was open (a
   * retry after a config error, or a background refetch after a deploy). A
   * session minted in the other mode can't be honoured in this one: a
   * phone-only token is rejected once enforcement is on, and a password
   * session is meaningless once it is off. Start over rather than let the user
   * hit a confusing 401 three screens later. */
  const previousPasswordRequiredRef = useRef<boolean | null>(null);
  useEffect(() => {
    if (accessConfig === null) return;
    const previous = previousPasswordRequiredRef.current;
    previousPasswordRequiredRef.current = accessConfig.passwordRequired;
    if (previous === null || previous === accessConfig.passwordRequired) return;
    clearPhoneSession();
    setStep('phone');
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [accessConfig?.passwordRequired]);

  async function refreshPlotsList(token = phoneAccessSessionToken) {
    if (!token) return;
    try {
      const res = await listPublicInspectionAccessPlots(token);
      setAccessiblePlots(res.plots);
    } catch (err) {
      // Round 8-9D — a 401 here is not a transient glitch: the session is
      // gone (expired, revoked, or — under enforcement — the plot password was
      // changed, which bumps the credential version and invalidates every
      // token minted against the old one). Showing a stale plot list after
      // that would just fail again at the next click, so end the session
      // honestly and ask for the credentials once.
      if (axios.isAxiosError(err) && err.response?.status === 401) {
        clearPhoneSession();
        setStep('phone');
        setPhoneLookupError(sessionExpiredMessage);
        return;
      }
      // Anything else: keep the stale list rather than crash the page — the
      // user can still retry the action that triggered this refresh.
    }
  }

  function handleQrScan(raw: string) {
    setQrOpen(false);
    const parsed = parsePlotQr(raw);
    if (!parsed) {
      setPhoneLookupError('QR ไม่ถูกต้อง กรุณาสแกนใหม่');
      return;
    }
    setPhoneLookupError('');
    setQrTarget(parsed);
  }

  async function handlePhoneLookup(e: React.FormEvent) {
    e.preventDefault();
    // Round 8-9D — never submit before the capability probe has answered; the
    // form isn't even rendered in that state, this is the belt-and-braces path
    // (an Enter key racing a refetch).
    if (accessConfig === null) return;
    // Round 8-4H.1 — /public/inspect is Online-only: offline lookup is
    // always blocked with the standard "connect first" message, never
    // silently attempted against a cache (the persistent offline cache
    // feature is disabled, see module docstring).
    if (!isOnline) {
      setPhoneLookupError(OFFLINE_BLOCKED_MESSAGE);
      return;
    }
    let normalized: string;
    try {
      normalized = normalizeThaiMobile(phoneInput);
    } catch {
      // Never surface normalizeThaiMobile's own message here — it may
      // contain the word "เบอร์โทรศัพท์", which this round's neutral
      // "หมายเลขสำหรับเข้าตรวจ" copy must not leak.
      setPhoneLookupError('หมายเลขไม่ถูกต้อง กรุณาตรวจสอบตัวเลข 10 หลัก');
      return;
    }
    // Round 8-9D — client-side policy check is a courtesy so the user isn't
    // spending one of their five attempts a minute on a typo. The backend
    // re-validates and folds any failure into the same generic error.
    if (passwordRequired) {
      const policyError = validatePlotPassword(
        passwordInput, passwordMinLength, passwordMaxLength,
      );
      if (policyError) {
        setPasswordError(policyError);
        return;
      }
    }
    setPhoneLookupLoading(true);
    setPhoneLookupError('');
    setPasswordError('');
    setQrMatchNote('');
    try {
      const qrKeyToSend = qrTarget?.mode === 'qr' ? qrTarget.qrKey : undefined;
      const result = await lookupPublicInspectionAccess({
        phone: normalized,
        // Omitted entirely when enforcement is off — the request body is then
        // byte-identical to the pre-8-9D one.
        password: passwordRequired ? passwordInput : undefined,
        qrKey: qrKeyToSend,
      });
      setPhoneAccessSessionToken(result.phoneAccessSessionToken);
      setAccessiblePlots(result.plots);

      let matchedId = result.qrMatchedPlotId;
      if (!matchedId && qrTarget?.mode === 'legacy') {
        const found = result.plots.find(
          (p) => p.supplierCode === qrTarget.supplierCode && p.plotCode === qrTarget.plotCode,
        );
        if (found) matchedId = found.plotId;
        else setQrMatchNote('ไม่พบแปลงที่สแกนในรายการของหมายเลขนี้');
      }
      setQrMatchedPlotId(matchedId ?? null);
      // Never keep the entered phone once the lookup has succeeded — and, from
      // round 8-9D, never keep the password either. Everything the rest of the
      // visit needs now lives in the opaque session token; the plaintext has no
      // second use, so holding it would be pure risk. A FAILED lookup
      // deliberately keeps it so the user can fix one digit instead of
      // retyping both fields.
      setPhoneInput('');
      setPasswordInput('');
      setShowPassword(false);
      setStep('plots');
    } catch (err) {
      const status = axios.isAxiosError(err) ? err.response?.status : undefined;
      if (status === 404) {
        setPhoneLookupError(
          passwordRequired ? AUTH_FAILED_MESSAGE : 'ไม่พบแปลงที่หมายเลขนี้ได้รับอนุญาตให้เข้าตรวจ',
        );
      } else if (status === 429) {
        setPhoneLookupError('มีการลองหลายครั้งเกินไป กรุณารอสักครู่แล้วลองใหม่');
      } else if (status === 422 && passwordRequired) {
        // A 422 that got past the client check is still a policy problem —
        // show OUR policy copy. The backend's `detail` is never rendered: it
        // can be a nested Pydantic error object, and echoing server text into
        // the page is how a validation error turns into an information leak.
        setPasswordError(
          `รหัสยืนยันแปลงต้องเป็นตัวเลข ${passwordMinLength} ถึง ${passwordMaxLength} หลัก`,
        );
      } else if (passwordRequired) {
        // One message for a network failure and for anything else unexpected
        // (round 8-9D Part G copy).
        setPhoneLookupError('ไม่สามารถค้นหาแปลงได้ กรุณาลองใหม่');
      } else if (axios.isAxiosError(err) && !err.response) {
        setPhoneLookupError('เชื่อมต่อเครือข่ายไม่สำเร็จ กรุณาลองใหม่');
      } else {
        setPhoneLookupError('ไม่สามารถค้นหาแปลงได้ กรุณาลองใหม่');
      }
    } finally {
      setPhoneLookupLoading(false);
    }
  }

  function handleChangePhone() {
    if (computeIsFormDirty(fields, formBaseline, photos)) {
      if (!confirm('ข้อมูลที่กรอกในแปลงนี้ยังไม่ได้บันทึก ต้องการออกหรือไม่')) return;
    }
    clearPhoneSession();
    setStep('phone');
  }

  async function handlePlotClick(plot: PublicPhoneAccessPlot) {
    if (!plot.canInspect || selectingPlotId) return;
    if (!selectedInspectorType) {
      setInspectorTypeError('กรุณาเลือกฐานะผู้ตรวจก่อนเลือกแปลง');
      roleGroupRef.current?.focus();
      return;
    }

    // Round 8-4H.1 — offline plot selection is disabled entirely: no cache
    // to fall back to anymore. accessiblePlots may still hold a list from an
    // earlier online lookup this session (in-memory only), but picking one
    // while offline must never open a form — select-plot is never callable
    // without connectivity, and there is no offline path left to substitute.
    if (!isOnline) {
      setSelectError(OFFLINE_BLOCKED_MESSAGE);
      return;
    }

    if (!phoneAccessSessionToken) return;
    setSelectingPlotId(plot.plotId);
    setSelectError('');
    try {
      const result = await selectPublicInspectionPlot(phoneAccessSessionToken, plot.plotId, selectedInspectorType);
      setSelectedPlotId(plot.plotId);
      setInspectionSessionToken(result.inspectionSessionToken);
      const context = toPlotContext(result);
      setPlotInfo(context);
      const fresh = fieldsForSelectedPlot(retainedSubmittedByName, context);
      setFields(fresh);
      setFormBaseline(fresh);
      setPhotos(emptyPhotoSlots());
      setGpsError('');
      setSubmitError('');
      setSubmitNotice('');
      setStep('form');
    } catch (err) {
      const status = axios.isAxiosError(err) ? err.response?.status : undefined;
      if (status === 401) {
        clearPhoneSession();
        setStep('phone');
        setPhoneLookupError(sessionExpiredMessage);
      } else if (status === 404) {
        setSelectError('แปลงนี้ไม่พร้อมให้ตรวจแล้ว กรุณาเลือกแปลงอื่น');
        await refreshPlotsList();
      } else if (status === 409) {
        setSelectError('แปลงนี้ยังไม่มีรอบปลูกที่เปิดอยู่ในตอนนี้');
        await refreshPlotsList();
      } else {
        setSelectError('ไม่สามารถเลือกแปลงนี้ได้ กรุณาลองใหม่');
      }
    } finally {
      setSelectingPlotId(null);
    }
  }

  function captureGps() {
    if (!navigator.geolocation) { setGpsError('เบราว์เซอร์นี้ไม่รองรับ GPS'); return; }
    setGpsLoading(true);
    setGpsError('');
    navigator.geolocation.getCurrentPosition(
      (pos) => {
        set('latitude', parseFloat(pos.coords.latitude.toFixed(7)));
        set('longitude', parseFloat(pos.coords.longitude.toFixed(7)));
        setGpsLoading(false);
      },
      () => {
        setGpsError('ไม่สามารถรับตำแหน่ง GPS ได้ — กรุณาอนุญาตการเข้าถึงตำแหน่งที่ตั้งในเบราว์เซอร์แล้วลองใหม่');
        setGpsLoading(false);
      },
      { timeout: 8000 },
    );
  }

  // Request GPS once on entering the form step — reuse the value if we
  // already have one (e.g. a silent inspection-token re-select left it
  // untouched), never ask again just to "refresh" it.
  useEffect(() => {
    if (step === 'form' && fields.latitude == null && !gpsLoading) {
      captureGps();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [step]);

  function handleBackToList() {
    if (computeIsFormDirty(fields, formBaseline, photos)) {
      if (!confirm('ข้อมูลที่กรอกในแปลงนี้ยังไม่ได้บันทึก ต้องการออกหรือไม่')) return;
    }
    resetPlotDraft(retainedSubmittedByName);
    setStep('plots');
  }

  async function handleInspectionTokenExpiredAtSubmit() {
    if (!phoneAccessSessionToken || !selectedPlotId || !selectedInspectorType) {
      clearPhoneSession();
      setStep('phone');
      setPhoneLookupError(sessionExpiredMessage);
      return;
    }
    try {
      const result = await selectPublicInspectionPlot(phoneAccessSessionToken, selectedPlotId, selectedInspectorType);
      setInspectionSessionToken(result.inspectionSessionToken);
      setPlotInfo(toPlotContext(result));
      // Form fields/photos are deliberately untouched — never auto-resubmit.
      setSubmitNotice('เซสชันหมดอายุ ระบบต่ออายุให้แล้ว กรุณากดบันทึกอีกครั้ง');
    } catch {
      // Round 8-9D — under enforcement this is also the path a CHANGED plot
      // password lands on: select-plot re-checks the credential version and
      // 401s, so the renewal fails and the whole session ends here. The user
      // is asked for both credentials again; the form they had open is gone,
      // which is the honest outcome — the record could not have been saved
      // against a credential that no longer exists.
      clearPhoneSession();
      setStep('phone');
      setPhoneLookupError(sessionExpiredMessage);
    }
  }

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    // Round 8-14B hard guard — never rely on the submit button's `disabled`
    // attribute alone; a slot still mid client-side compression means its
    // File isn't in `photos` yet.
    if (photoProcessing) { setSubmitError('กรุณารอให้ระบบเตรียมรูปภาพเสร็จก่อน'); return; }
    // Round 8-4C — blocks a second overlapping invocation (double-click
    // before the disabled={submitting} re-render commits).
    if (submitInFlightRef.current) return;
    if (!plotInfo?.plotCycleId) return;
    if (missingProtocolScores(stageProtocol, {
      fieldPrepScore: fields.fieldPrepScore,
      weatherScore: fields.weatherScore,
      careScore: fields.careScore,
      varietyResistanceScore: fields.varietyResistanceScore,
    }).length > 0) {
      setSubmitError('กรุณาให้คะแนนครบทั้ง 4 ช่องตามระยะการเจริญเติบโต');
      return;
    }
    // Round 8-8B, threshold relaxed round 8-8B.1 — block the same invalid-kg
    // cases the backend would still 422 on (negative, >2 decimals, numeric
    // overflow, or a derived % over the 9999.9 storage ceiling) before ever
    // making the request. A result over 150% is NOT one of these anymore —
    // it's a genuine, storable value; YieldQuantityInput shows a
    // non-blocking amber notice for it, but Submit stays enabled.
    if (yieldFieldError) {
      setSubmitError(yieldFieldError);
      return;
    }

    submitInFlightRef.current = true;
    try {
      // Round 8-4B Part 4 (kept in 8-4H.1): every submission carries an
      // idempotency identity, ensured/reused before the first API attempt —
      // still needed so a network-failure retry (below) replays safely
      // instead of risking a duplicate record, even though drafts are no
      // longer queued to IndexedDB.
      const identity = ensureSubmissionIdentity();
      const selectedPhotos = photos.filter((p): p is File => p !== null);

      // Round 8-4H.1 — /public/inspect is Online-only: offline never queues
      // a draft anymore. The form, photos, and identity are left completely
      // untouched so the user can press "บันทึกการตรวจแปลง" again the moment
      // connectivity returns — never auto-submitted for them (Part B).
      if (!isOnline || !inspectionSessionToken) {
        setSubmitError(OFFLINE_SUBMIT_BLOCKED_MESSAGE);
        return;
      }

      setSubmitting(true);
      setSubmitError('');
      setSubmitNotice('');
      try {
        // Round 8-19.1 — resolved at SUBMIT time, in Asia/Bangkok (was a
        // module-level UTC constant, wrong 00:00-06:59 ICT and stale for a
        // page left open across midnight).
        const payload = buildOfflinePublicRecordPayload(inspectionSessionToken, bangkokToday(), fields, identity);
        const result = selectedPhotos.length > 0
          ? await createPublicRecordWithPhotos(payload, selectedPhotos)
          : await createPublicInspectionRecord(payload);
        // 201 (server created it) or 200 (idempotent replay of an earlier
        // attempt) both land here — axios only throws for a non-2xx status.
        // Either way the server has now confirmed this identity, so any
        // pre-existing draft under the same key (from before this round, see
        // OfflineInspectionQueuePanel) is redundant — clear it.
        await deleteOfflineInspectionDraft(identity.clientSubmissionId).catch(() => {});
        await refreshPendingCount();
        setRetainedSubmittedByName(fields.submittedByName.trim());
        setSubmittedRecord(result);
        setStep('success');
      } catch (err) {
        const isAxios = axios.isAxiosError(err);
        const status = isAxios ? err.response?.status : undefined;
        const hasResponse = isAxios ? !!err.response : false;

        if (isAxios && !hasResponse) {
          // Round 8-4H.1 — a network error with NO response mid-request: the
          // server's outcome is unknown, but this no longer queues a draft
          // (Part B: "ห้าม fallback ไป Offline Queue"). The form/photos stay
          // exactly as they are and the SAME idempotency identity is kept
          // (ensureSubmissionIdentity above never re-generates it) so the
          // user's own retry click safely replays instead of risking a
          // duplicate record — never claim success, never auto-retry.
          setSubmitError('เชื่อมต่อเครือข่ายไม่สำเร็จ กรุณาลองใหม่');
          return;
        }

        if (status === 401) {
          await handleInspectionTokenExpiredAtSubmit();
        } else if (status === 404) {
          resetPlotDraft(retainedSubmittedByName);
          setSelectError('แปลงนี้ไม่พร้อมให้ตรวจแล้ว หรือรอบปลูกมีการเปลี่ยนแปลง กรุณาเลือกแปลงอีกครั้ง');
          await refreshPlotsList();
          setStep('plots');
        } else if (status === 409 || status === 422) {
          // A real HTTP response was received — never auto-queue these (Part
          // 11). A structured code gets its own clear message; anything else
          // falls back to a generic one for that status.
          const code = extractOfflineErrorCode(err);
          if (code) {
            setSubmitError(describeOfflineErrorCode(code));
          } else if (status === 422) {
            setSubmitError('ข้อมูลไม่ถูกต้อง กรุณาตรวจสอบและลองใหม่');
          } else {
            setSubmitError('ไม่สามารถบันทึกได้ กรุณาลองใหม่');
          }
        } else if (status === 429) {
          setSubmitError('มีการลองหลายครั้งเกินไป กรุณารอสักครู่แล้วลองใหม่');
        } else {
          // 5xx or anything else with a real response — a response was
          // received, so this is never auto-queued.
          setSubmitError('บันทึกไม่สำเร็จ กรุณาลองใหม่');
        }
      } finally {
        setSubmitting(false);
      }
    } finally {
      submitInFlightRef.current = false;
    }
  }

  async function inspectNextPlot() {
    resetPlotDraft(retainedSubmittedByName);
    setStep('plots');
    await refreshPlotsList();
  }

  /** Round 8-19 — the success screen's "กลับรายการแปลง". Identical to
   * inspectNextPlot: both return to the list after a save, so both must
   * re-fetch it, otherwise the plot just inspected still shows its
   * pre-submit status. */
  async function backToPlotsAfterSubmit() {
    resetPlotDraft(retainedSubmittedByName);
    setStep('plots');
    await refreshPlotsList();
  }

  return (
    <div className="min-h-screen bg-gray-50 px-4 py-6">
      <div className="mx-auto max-w-md">
        {/* Round 8-4B — network status + pending-queue indicator. Fixed
            h-8 on both pills so the row's own height never jumps when the
            queue button appears/disappears as pendingCount changes. */}
        <div className="mb-3 flex items-center justify-between gap-2">
          <span
            title={isOnline ? 'อุปกรณ์นี้เชื่อมต่ออินเทอร์เน็ตอยู่' : 'ต้องเชื่อมต่ออินเทอร์เน็ตก่อนค้นหาและบันทึกการตรวจแปลง'}
            className={`inline-flex h-8 items-center gap-1.5 rounded-full px-3 text-xs font-medium ${
              isOnline ? 'bg-green-50 text-green-700' : 'bg-red-50 text-red-700'
            }`}
          >
            {isOnline ? <Wifi className="h-3.5 w-3.5" /> : <WifiOff className="h-3.5 w-3.5" />}
            {isOnline ? 'ออนไลน์' : 'ออฟไลน์ — ต้องเชื่อมต่ออินเทอร์เน็ตก่อนบันทึก'}
          </span>
          {/* Round 8-4H.1 — this badge/panel now only ever surfaces LEFTOVER
              drafts saved before offline was disabled (Part D); no new draft
              can ever be added to this count anymore. */}
          {pendingCount > 0 && (
            <button
              type="button"
              onClick={() => setQueuePanelOpen(true)}
              title="รายการนี้ถูกบันทึกไว้ก่อนปิดการใช้งาน Offline กรุณาเชื่อมต่ออินเทอร์เน็ตเพื่อส่งข้อมูล"
              className="inline-flex h-8 items-center gap-1.5 rounded-full bg-amber-50 px-3 text-xs font-medium text-amber-700 hover:bg-amber-100"
            >
              <CloudOff className="h-3.5 w-3.5" />
              รายการค้างเดิม {pendingCount} รายการ
            </button>
          )}
        </div>

        {!indexedDbAvailable && (
          <p className="mb-3 rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-700">
            อุปกรณ์นี้ไม่สามารถโหลดรายการค้างเดิมได้ในขณะนี้ — การตรวจแปลงแบบออนไลน์ยังใช้งานได้ตามปกติ
          </p>
        )}

        {purgedNotice && (
          <p role="status" className="mb-3 flex items-start justify-between gap-2 rounded-md border border-gray-200 bg-gray-50 px-3 py-2 text-xs text-gray-500">
            <span>{purgedNotice}</span>
            <button type="button" onClick={() => setPurgedNotice('')} aria-label="ปิดข้อความ" className="shrink-0 text-gray-400 hover:text-gray-600">✕</button>
          </p>
        )}

        <h1 className="mb-4 text-center text-lg font-bold text-gray-900">บันทึกการตรวจแปลง</h1>

        {step === 'phone' && (
          <section className="space-y-4 rounded-lg border border-gray-200 bg-white p-5 shadow-sm">
            {qrTarget && (
              <div className="rounded-md bg-green-50 px-3 py-2 text-center text-sm text-green-700">
                {qrTarget.mode === 'qr' ? 'เข้าผ่านการสแกน QR แปลง' : `เข้าผ่าน QR แปลง ${qrTarget.plotCode}`}
              </div>
            )}

            {/* Round 8-9D — the capability gate. Until the backend has said
                whether a plot password is required, there is no correct form to
                render, so we render NEITHER: showing the phone-only form early
                would let a user submit a request that is missing a field the
                server now demands. Both placeholder states reserve the same
                minimum height as the form itself so the card doesn't jump. */}
            {configLoading && (
              <div
                role="status"
                aria-live="polite"
                className="flex min-h-[11rem] flex-col items-center justify-center gap-2 text-sm text-gray-500"
              >
                <Loader2 className="h-6 w-6 animate-spin text-green-600" />
                กำลังเตรียมหน้าตรวจแปลง...
              </div>
            )}

            {configFailed && (
              <div className="flex min-h-[11rem] flex-col items-center justify-center gap-3">
                <p role="alert" className="text-center text-sm text-red-600">{CONFIG_ERROR_MESSAGE}</p>
                <button
                  type="button"
                  onClick={() => { void configQuery.refetch(); }}
                  disabled={configQuery.isFetching}
                  className={primaryBtnCls}
                >
                  {configQuery.isFetching && <Loader2 className="h-5 w-5 animate-spin" />} ลองใหม่
                </button>
              </div>
            )}

            {accessConfig !== null && (
              <>
                <form onSubmit={handlePhoneLookup} className="space-y-3">
                  <Field label="หมายเลขสำหรับเข้าตรวจ" required>
                    <input
                      type="text" inputMode="numeric" autoComplete="off" autoFocus
                      value={phoneInput}
                      onChange={(e) => { setPhoneInput(e.target.value); setPhoneLookupError(''); }}
                      className={inputCls}
                      placeholder="กรอกหมายเลข 10 หลัก"
                    />
                  </Field>

                  {/* Rendered ONLY when the backend says so. htmlFor/id rather
                      than the wrapping-<label> Field helper used above: a
                      <label> may wrap just one labelable control, and this
                      field needs the reveal button beside the input. */}
                  {passwordRequired && (
                    <div>
                      <label htmlFor="plot-access-password" className="mb-1 block text-sm font-medium text-gray-700">
                        รหัสยืนยันแปลง<span className="ml-1 text-red-500">*</span>
                      </label>
                      <div className="flex items-stretch gap-2">
                        <input
                          id="plot-access-password"
                          // Masked by default; type="text" only while the user
                          // is holding it revealed. NEVER type="number" — that
                          // silently drops a leading zero, and "0123" is a
                          // perfectly legal code.
                          type={showPassword ? 'text' : 'password'}
                          inputMode="numeric"
                          autoComplete="off"
                          maxLength={passwordMaxLength}
                          value={passwordInput}
                          onChange={(e) => {
                            setPasswordInput(sanitizePlotPassword(e.target.value, passwordMaxLength));
                            setPasswordError('');
                          }}
                          aria-describedby="plot-access-password-help"
                          aria-invalid={passwordError ? true : undefined}
                          className={`${inputCls} min-w-0 flex-1`}
                        />
                        <button
                          // type="button" — inside a <form>, the default is
                          // "submit", so revealing the code would fire a lookup.
                          type="button"
                          onClick={() => setShowPassword((v) => !v)}
                          aria-label={showPassword ? 'ซ่อนรหัสยืนยันแปลง' : 'แสดงรหัสยืนยันแปลง'}
                          title={showPassword ? 'ซ่อนรหัสยืนยันแปลง' : 'แสดงรหัสยืนยันแปลง'}
                          className="flex w-12 shrink-0 items-center justify-center rounded-md border border-gray-300 text-gray-500 hover:bg-gray-50"
                        >
                          {showPassword ? <EyeOff className="h-5 w-5" /> : <Eye className="h-5 w-5" />}
                        </button>
                      </div>
                      <p id="plot-access-password-help" className="mt-1 text-xs text-gray-500">
                        กรอกตัวเลขอย่างน้อย {passwordMinLength} หลัก
                      </p>
                      {passwordError && <p role="alert" className="mt-1 text-sm text-red-600">{passwordError}</p>}
                    </div>
                  )}

                  {phoneLookupError && <p role="alert" className="text-sm text-red-600">{phoneLookupError}</p>}
                  <button type="submit" disabled={phoneLookupLoading} className={primaryBtnCls}>
                    {phoneLookupLoading && <Loader2 className="h-5 w-5 animate-spin" />} ค้นหาแปลง
                  </button>
                </form>
                <p className="text-center text-xs text-gray-400">หรือ</p>
                <button type="button" onClick={() => setQrOpen(true)} className={secondaryBtnCls}>
                  <QrCode className="mr-2 inline h-4 w-4" /> สแกน QR แปลง
                </button>
              </>
            )}
          </section>
        )}

        {step === 'plots' && (
          <section className="space-y-4">
            <div className="flex items-center justify-between rounded-lg border border-gray-200 bg-white p-4 shadow-sm">
              <h2 className="text-base font-semibold text-gray-900">เลือกแปลงที่จะตรวจ</h2>
              <button type="button" onClick={handleChangePhone} className="text-sm font-medium text-green-700 hover:underline">
                {changeIdentityLabel}
              </button>
            </div>

            <div className="rounded-lg border border-gray-200 bg-white p-4 shadow-sm">
              <InspectorTypeControl
                value={selectedInspectorType}
                onChange={(v) => { setSelectedInspectorType(v); setInspectorTypeError(''); }}
                error={inspectorTypeError}
                groupRef={roleGroupRef}
              />
            </div>

            {qrMatchNote && (
              <p role="alert" className="rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-700">
                {qrMatchNote}
              </p>
            )}
            {selectError && (
              <p role="alert" className="rounded-md border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-700">
                {selectError}
              </p>
            )}

            <label className="relative block">
              <Search className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-gray-400" />
              <input
                type="search"
                value={plotSearch}
                onChange={(e) => setPlotSearch(e.target.value)}
                aria-label="ค้นหาแปลง"
                placeholder="ค้นหารหัส/ชื่อแปลง, Supplier, พืช..."
                className={`${inputCls} pl-9`}
              />
            </label>

            <div className="space-y-3" aria-live="polite">
              {visiblePlots.length === 0 ? (
                <p className="py-8 text-center text-sm text-gray-400">ไม่พบแปลง</p>
              ) : (
                visiblePlots.map((p) => (
                  <PlotCard
                    key={p.plotId}
                    plot={p}
                    isQrMatch={p.plotId === qrMatchedPlotId}
                    selecting={selectingPlotId === p.plotId}
                    blockedByOtherSelect={selectingPlotId != null && selectingPlotId !== p.plotId}
                    onSelect={() => handlePlotClick(p)}
                  />
                ))
              )}
            </div>
          </section>
        )}

        {step === 'form' && (
          <form onSubmit={handleSubmit} className="space-y-4">
            {/* ข้อมูลแปลง — read-only, plot MASTER data from the selected
                plot/cycle (unchanged read-only contract from round 20.2). */}
            <section className="rounded-lg border border-gray-200 bg-gray-50 p-4 text-sm shadow-sm">
              <div className="text-xs text-gray-400">Supplier</div>
              <div className="font-semibold text-gray-800">{plotInfo?.supplierCode} — {plotInfo?.supplierName}</div>
              <div className="mt-2 text-xs text-gray-400">แปลง</div>
              <div className="font-semibold text-gray-800">{plotInfo?.plotCode} — {plotInfo?.plotName}</div>
              <div className="mt-2 text-xs text-gray-400">รอบปลูก</div>
              <div className="font-semibold text-gray-800">{cycleDisplayName(plotInfo)}</div>
              <div className="mt-2 text-xs text-gray-400">เข้าตรวจในฐานะ</div>
              <div className="font-semibold text-gray-800">{inspectorTypeLabel(selectedInspectorType)}</div>
              <dl className="mt-3 grid grid-cols-2 gap-x-4 gap-y-2 border-t border-gray-200 pt-3">
                <div>
                  <dt className="text-xs text-gray-400">ชนิดพืช</dt>
                  <dd className="text-gray-700">{plotInfo?.currentCrop || emptyText}</dd>
                </div>
                <div>
                  <dt className="text-xs text-gray-400">พันธุ์/สายพันธุ์</dt>
                  <dd className="text-gray-700">{plotInfo?.currentVariety || emptyText}</dd>
                </div>
                <div>
                  <dt className="text-xs text-gray-400">เลขล็อต (Lot No.)</dt>
                  <dd className="text-gray-700">{plotInfo?.currentLotNo || emptyText}</dd>
                </div>
                <div>
                  <dt className="text-xs text-gray-400">วันที่ปลูก</dt>
                  <dd className="text-gray-700">{plotInfo?.currentPlantingDate || emptyText}</dd>
                </div>
                <div className="col-span-2">
                  <dt className="text-xs text-gray-400">แผนผลผลิต</dt>
                  <dd className="text-gray-700">
                    {plotInfo?.plantCount ?? emptyText} ต้น / {formatFixed(plotInfo?.expectedYieldFull ?? null, 2) ?? emptyText} {plotInfo?.expectedYieldUnit || ''}
                  </dd>
                </div>
              </dl>
            </section>

            <section className="space-y-3 rounded-lg border border-gray-200 bg-white p-4 shadow-sm">
              <Field label="ชื่อผู้กรอกข้อมูล">
                <input value={fields.submittedByName} onChange={(e) => set('submittedByName', e.target.value)}
                  className={inputCls} placeholder="ไม่บังคับ" />
              </Field>
            </section>

            <section className="space-y-3 rounded-lg border border-gray-200 bg-white p-4 shadow-sm">
              <GroupField label="ระยะการเจริญเติบโต">
                <PublicMasterDataButtons type="growth_stage" value={fields.growthStage || null}
                  onChange={(v) => set('growthStage', v ?? '')} />
              </GroupField>
              <GroupField label="สภาพอากาศ (เลือกได้หลายตัวเลือก)">
                <PublicMasterDataButtons type="weather" value={fields.weatherCondition || null}
                  multiple onChange={(v) => set('weatherCondition', v ?? '')} />
              </GroupField>
            </section>

            <section className="rounded-lg border border-green-200 bg-green-50/50 p-4 shadow-sm">
              <h2 className="mb-1 text-sm font-medium text-gray-700">ผลผลิต (Yield)</h2>
              <YieldQuantityInput
                quantityKg={fields.yieldQuantityKg}
                yieldPct={fields.yieldPct}
                expectedYieldFull={plotInfo?.expectedYieldFull}
                expectedYieldUnit={plotInfo?.expectedYieldUnit}
                latestYieldPct={plotInfo?.currentYieldPct}
                onChange={({ quantityKg, yieldPct }) => {
                  set('yieldQuantityKg', quantityKg);
                  set('yieldPct', yieldPct);
                }}
                error={yieldFieldError}
              />
              {(() => {
                const latestYieldPct = toNumberOrNull(plotInfo?.currentYieldPct ?? null);
                if (latestYieldPct == null) return null;
                return (
                  <p className="mt-2 text-xs text-gray-600">
                    ค่าเริ่มต้นดึงจากการตรวจล่าสุดของแปลงนี้
                    {plotInfo?.lastInspectedAt
                      ? ` เมื่อ ${new Date(plotInfo.lastInspectedAt).toLocaleDateString('th-TH', { day: 'numeric', month: 'short', year: 'numeric' })}`
                      : ''}
                    {plotInfo?.currentStage ? ` · ระยะ: ${plotInfo.currentStage}` : ''}
                  </p>
                );
              })()}
            </section>

            <section className="space-y-4 rounded-lg border border-gray-200 bg-white p-4 shadow-sm">
              <p className="text-sm font-medium text-gray-700">
                {stageProtocol
                  ? `การประเมินตามระยะ "${stageProtocol.growthStage}" (ต้องครบ 4 ข้อ)`
                  : 'การประเมินสภาพแปลง (คะแนน 1–10)'}
              </p>
              <ProtocolScoreInputs
                protocol={stageProtocol}
                stageSelected={!!fields.growthStage}
                loading={protocolsLoading}
                loadError={protocolsError}
                scores={{
                  fieldPrepScore: fields.fieldPrepScore,
                  weatherScore: fields.weatherScore,
                  careScore: fields.careScore,
                  varietyResistanceScore: fields.varietyResistanceScore,
                }}
                onChange={(slot, v) => set(slot, v)}
              />
            </section>

            <section className="space-y-3 rounded-lg border border-gray-200 bg-white p-4 shadow-sm">
              <p className="text-sm font-medium text-gray-700">พิกัด GPS <span className="text-xs font-normal text-gray-400">(ไม่บังคับ)</span></p>
              <button type="button" onClick={captureGps} disabled={gpsLoading}
                className="flex w-full items-center justify-center gap-2 rounded-md border border-gray-300 px-4 py-3 text-sm font-medium text-gray-700 hover:bg-gray-50 disabled:opacity-50">
                {gpsLoading ? <Loader2 className="h-4 w-4 animate-spin" /> : <Navigation className="h-4 w-4 text-green-600" />}
                {gpsLoading ? 'กำลังรับตำแหน่ง GPS...' : 'บันทึก GPS ปัจจุบัน'}
              </button>
              {gpsError && <p className="text-center text-xs text-red-600">{gpsError}</p>}
              {fields.latitude != null && (
                <p className="text-center font-mono text-xs text-green-700">
                  {fields.latitude.toFixed(6)}, {fields.longitude?.toFixed(6)}
                </p>
              )}
            </section>

            <section className="rounded-lg border border-gray-200 bg-white p-4 shadow-sm">
              <PhotoSlotPicker slots={photos} onChange={setPhotos} onProcessingChange={setPhotoProcessing} />
            </section>

            <section className="space-y-3 rounded-lg border border-gray-200 bg-white p-4 shadow-sm">
              <Field label="คำแนะนำ">
                <textarea value={fields.recommendation} onChange={(e) => set('recommendation', e.target.value)}
                  rows={2} className={inputCls} />
              </Field>
              <Field label="หมายเหตุ">
                <textarea value={fields.notes} onChange={(e) => set('notes', e.target.value)}
                  rows={2} className={inputCls} />
              </Field>
            </section>

            {submitNotice && <p role="alert" className="text-sm text-amber-600">{submitNotice}</p>}
            {submitError && <p role="alert" className="text-sm text-red-600">{submitError}</p>}

            <button type="submit" disabled={submitting || photoProcessing} className={primaryBtnCls}>
              {(submitting || photoProcessing) && <Loader2 className="h-5 w-5 animate-spin" />} บันทึกการตรวจแปลง
            </button>
            <button type="button" onClick={handleBackToList} className={secondaryBtnCls}>กลับรายการแปลง</button>
            <button type="button" onClick={handleChangePhone} className="w-full text-center text-sm font-medium text-green-700 hover:underline">
              {changeIdentityLabel}
            </button>
          </form>
        )}

        {step === 'success' && (
          <section className="space-y-4 rounded-lg border border-green-200 bg-white p-6 text-center shadow-sm">
            <CheckCircle2 className="mx-auto h-12 w-12 text-green-600" />
            <p className="text-base font-semibold text-gray-900">บันทึกสำเร็จ</p>
            <p className="text-sm text-gray-500">{submittedRecord?.plotCode} — {submittedRecord?.plotName}</p>
            <div className="space-y-2 pt-2">
              {/* Round 8-4B Part 14 — selecting another plot needs the
                  select-plot API, which offline can't do yet (that's 8-4C);
                  never offer a button implying it works while offline. */}
              {isOnline && (
                <button type="button" onClick={inspectNextPlot} className="w-full rounded-md bg-green-600 px-4 py-3 text-sm font-semibold text-white hover:bg-green-700">
                  ตรวจแปลงถัดไป
                </button>
              )}
              {/* Round 8-19 — same refresh as "ตรวจแปลงถัดไป": the record just
                  saved must flip this plot's card to "ตรวจแล้ววันนี้", and the
                  list is only re-fetched, never a full page reload. */}
              <button type="button" onClick={() => { void backToPlotsAfterSubmit(); }} className={secondaryBtnCls}>
                กลับรายการแปลง
              </button>
              <button type="button" onClick={handleChangePhone} className="w-full text-sm font-medium text-green-700 hover:underline">
                {changeIdentityLabel}
              </button>
            </div>
          </section>
        )}

      </div>

      {qrOpen && <LazyPlotQrScan onResult={handleQrScan} onClose={() => setQrOpen(false)} />}
      {queuePanelOpen && (
        <OfflineInspectionQueuePanel
          isOnline={isOnline}
          onClose={() => setQueuePanelOpen(false)}
          onQueueChanged={refreshPendingCount}
        />
      )}
    </div>
  );
}

export default PublicInspect;
