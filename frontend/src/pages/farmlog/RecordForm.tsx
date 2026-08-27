/**
 * RecordForm — create a NEW field inspection record (Step 12.6; round 8.0.5
 * append-only lock made this create-only — see api/records.ts's docstring).
 * Route: /farmlog/records/new only. An existing record is never editable
 * here or anywhere else — /farmlog/records/:id redirects straight to the
 * read-only One Page Preview (routes.tsx).
 *
 * Condition assessment is 4 score sliders (1–10): field_prep / weather / care /
 * variety_resistance — replaces the Step 12.5 list/status fields. Plot identity
 * info (village/district/province/GPS) is read-only, auto-filled from the
 * selected Plot. Crop/variety/lot/planting date are plot master data and are
 * shown read-only here, not re-selected per inspection. Record date and
 * "filled by" are auto-system (not editable). Photos = 4 fixed labeled slots.
 * Only คำแนะนำ / หมายเหตุ are free text.
 */
import { useEffect, useState } from 'react';
import axios from 'axios';
import { useNavigate, useSearchParams } from 'react-router-dom';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { ArrowLeft, Save, Navigation, X, Loader2, MapPin, User as UserIcon, QrCode } from 'lucide-react';
import {
  createRecord,
  createRecordWithPhotos,
  type RecordCreatePayload,
} from '../../api/records';
import { listSuppliers } from '../../api/suppliers';
import { listPlots, lookupPlotByQr, lookupPlotByQrKey, type PlotSummary } from '../../api/plots';
import { SmartPlotPicker } from '../../components/farmlog/SmartPlotPicker';
import { LazyPlotQrScan } from '../../components/farmlog/LazyPlotQrScan';
import { parsePlotQr } from '../../lib/plot-qr';
import { MasterDataButtons } from '../../components/farmlog/MasterDataButtons';
import { listFieldDefinitions } from '../../api/fielddefs';
import {
  DynamicFieldRenderer,
  validateField,
  type FieldValue,
} from '../../components/farmlog/fieldRegistry';
import {
  PhotoSlotPicker,
  emptyPhotoSlots,
} from '../../components/farmlog/PhotoSlotPicker';
import { ProtocolScoreInputs } from '../../components/farmlog/ProtocolScoreInputs';
import {
  fetchInspectionProtocols,
  findProtocolForStage,
} from '../../api/inspectionProtocols';
import { useAuth } from '../../hooks/useAuth';
import { formatFixed, toNumberOrNull } from '../../lib/numeric';
import { bangkokToday } from '../../lib/business-date';
import { YieldQuantityInput } from '../../components/farmlog/YieldQuantityInput';
import { computeInitialYieldValue, targetToKg, validateYieldQuantityKg } from '../../lib/yield-planning';
import { canViewVariety } from '../../lib/variety-visibility';

interface FormState {
  supplierId: string;
  plotId: string;
  submittedByName: string;
  growthStage: string | null;
  weatherCondition: string | null;
  // Round 8-8B — kg is the primary input; yieldPct is kept in sync (two-way,
  // via YieldQuantityInput) purely for the preview slider and as the legacy
  // preview field the payload still sends. Both null until a plot with a
  // comparable kg target is selected (contract #12: never a faked 100%).
  yieldQuantityKg: number | null;
  yieldPct: number | null;
  fieldPrepScore: number | null;
  weatherScore: number | null;
  careScore: number | null;
  varietyResistanceScore: number | null;
  recommendation: string;
  notes: string;
  latitude: number | null;
  longitude: number | null;
}

const EMPTY: FormState = {
  supplierId: '', plotId: '', submittedByName: '',
  growthStage: null, weatherCondition: null,
  yieldQuantityKg: null, yieldPct: null,
  fieldPrepScore: null, weatherScore: null, careScore: null,
  varietyResistanceScore: null, recommendation: '', notes: '',
  latitude: null, longitude: null,
};

// Round 8-25M — dark-mode readability fix. This file used hardcoded light
// Tailwind grays (bg-white/text-gray-800/border-gray-300 etc.) with no
// `dark:` variants at all — unlike Plots/Suppliers/Users/Roles, which
// already use the theme-aware semantic tokens defined in index.css
// (bg-card/text-foreground/border-input/bg-muted, flipped by the `.dark`
// class on <html>). AppLayout's own bg-background+text-foreground meant any
// text here that didn't sit inside an explicit bg-white card — the page
// title, the back button — rendered near-black text directly on the page's
// now-near-black background in dark mode: unreadable, not just mismatched.
const inputCls = 'w-full rounded-md border border-input bg-background px-3 py-2 text-sm shadow-sm focus:border-green-500 focus:outline-none focus:ring-1 focus:ring-green-500 disabled:bg-muted disabled:text-muted-foreground';
const emptyText = '—';

/** Readable reason for a failed save — HTTP status + the backend's detail
 * when present (mirrors Plots.tsx's describeQueryError). Special-cases the
 * one 409 create can return (round 7.1's "no active planting cycle" guard,
 * round 7.10 finding): SmartPlotPicker doesn't filter out no-active-cycle
 * plots, so a user can fill the entire form — scores, GPS, up to 5 photos —
 * before this rejects at submit; showing the raw English backend detail at
 * that point ("No active planting cycle for this plot") would be both
 * unhelpful and jarring in an otherwise all-Thai form. Every other status
 * keeps the generic HTTP+detail format unchanged. */
function describeSubmitError(error: unknown): string {
  if (axios.isAxiosError(error)) {
    const status = error.response?.status;
    if (status === 409) {
      return 'ไม่สามารถบันทึกได้ — แปลงนี้ยังไม่มีรอบปลูกที่เปิดอยู่ กรุณาเลือกแปลงอื่น หรือให้ผู้ดูแลระบบเริ่มรอบปลูกใหม่ก่อน';
    }
    const data = error.response?.data as { detail?: unknown } | undefined;
    let detail = '';
    if (data?.detail) {
      detail = Array.isArray(data.detail)
        ? data.detail.map((item) =>
            item && typeof item === 'object' && 'msg' in item ? String((item as { msg: unknown }).msg) : String(item),
          ).join(', ')
        : String(data.detail);
    }
    return `บันทึกไม่สำเร็จ: ${status ? `HTTP ${status}` : 'เชื่อมต่อไม่ได้'}${detail ? ` — ${detail}` : ''}`;
  }
  return `บันทึกไม่สำเร็จ: ${error instanceof Error ? error.message : 'ข้อผิดพลาดไม่ทราบสาเหตุ'}`;
}

function Field({ label, children, required }: { label: string; children: React.ReactNode; required?: boolean }) {
  return (
    <div>
      <label className="mb-1 block text-sm font-medium text-foreground">
        {label}{required && <span className="ml-1 text-red-500">*</span>}
      </label>
      {children}
    </div>
  );
}

export function RecordForm() {
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();
  const qc = useQueryClient();
  const { user } = useAuth();

  // "ตรวจแปลง" entry points (Plots list action / Plot Detail button) deep-link
  // here with ?supplierId=&plotId= so the inspector lands on a form already
  // scoped to that plot. The read-only Plot info panel + GPS auto-capture
  // then behave exactly as a manual pick would.
  const prefillSupplierId = searchParams.get('supplierId') ?? '';
  const prefillPlotId = searchParams.get('plotId') ?? '';

  const [form, setForm] = useState<FormState>(() => ({
    ...EMPTY,
    supplierId: prefillSupplierId,
    plotId: prefillPlotId,
  }));
  const set = <K extends keyof FormState>(key: K, value: FormState[K]) =>
    setForm(prev => ({ ...prev, [key]: value }));
  const [formError, setFormError] = useState('');
  const [selectedPlot, setSelectedPlot] = useState<PlotSummary | null>(null);

  // 4 fixed photo slots, required for a brand-new record (round 14) — real
  // upload via POST /api/v1/records/with-photos (round 13).
  const [photoFiles, setPhotoFiles] = useState<(File | null)[]>(emptyPhotoSlots());
  // Round 8-14B — true while PhotoSlotPicker has at least one slot mid
  // client-side compression. Submit must not fire while a slot is still
  // preparing (its File isn't in photoFiles yet), so this both disables the
  // button AND hard-gates onSubmit itself (never rely on disabled alone —
  // see onSubmit).
  const [photoProcessing, setPhotoProcessing] = useState(false);

  const [gpsLoading, setGpsLoading] = useState(false);
  const [gpsError, setGpsError] = useState('');

  // QR scan (supplier + plot) — Step: QR auto-fill
  const [qrOpen, setQrOpen] = useState(false);
  const [qrError, setQrError] = useState('');
  const [qrLookupLoading, setQrLookupLoading] = useState(false);

  // Dynamic custom fields (Step 12)
  const [customValues, setCustomValues] = useState<Record<string, FieldValue>>({});
  const [customErrors, setCustomErrors] = useState<Record<string, string>>({});
  const { data: fieldDefs = [] } = useQuery({
    queryKey: ['fielddefs', 'active'],
    queryFn: () => listFieldDefinitions(true),
  });
  const customFields = fieldDefs.filter(f => !f.isCore && f.active);

  // Growth-stage inspection protocol (round 5.2) — decides which 4 criteria
  // labels the score inputs show and, for a protocol stage, that all 4 are
  // required. Backend is the source of truth (records.read-gated endpoint).
  const {
    data: protocols,
    isLoading: protocolsLoading,
    isError: protocolsError,
  } = useQuery({
    queryKey: ['inspection-protocols'],
    queryFn: fetchInspectionProtocols,
    staleTime: 5 * 60_000,
  });
  const stageProtocol = findProtocolForStage(protocols, form.growthStage);

  const { data: suppliers = [] } = useQuery({
    queryKey: ['suppliers', 'all'],
    queryFn: () => listSuppliers({ activeOnly: true, limit: 200 }),
  });

  // Plot info panel (read-only, auto-filled from the selected Plot).
  const { data: plotsForSupplier = [] } = useQuery({
    queryKey: ['plots', form.supplierId || 'all'],
    queryFn: () => listPlots({ supplierId: form.supplierId || undefined, activeOnly: true, limit: 500 }),
    enabled: !!form.plotId,
    staleTime: 60_000,
  });
  useEffect(() => {
    const p = plotsForSupplier.find(p => p.id === form.plotId) ?? null;
    if (p) setSelectedPlot(p);
  }, [plotsForSupplier, form.plotId]);

  // Default the kg/Yield% pair from the active cycle's target + the plot's
  // latest inspection-derived value (round 8-8B Part D) so a field officer
  // starts from the last known state instead of a flat 100 — never a faked
  // 100% when there's no comparable kg target at all (contract #12). Keyed
  // on the plot id (not the object) so a background refetch of the same
  // plot doesn't clobber a value the user already adjusted; switching plots
  // re-defaults using the NEW plot's own target/history, never carrying the
  // previous plot's kg over (contract Part D.4).
  const latestYieldPct = toNumberOrNull(selectedPlot?.currentYieldPct);
  const targetKg = targetToKg(selectedPlot?.activeCycleExpectedYieldFull, selectedPlot?.activeCycleExpectedYieldUnit);
  // Live inline validation (contract #8) — recomputed on every kg keystroke,
  // not just at submit; onSubmit re-checks the same thing as a hard gate.
  const yieldFormError = validateYieldQuantityKg(form.yieldQuantityKg, targetKg);
  useEffect(() => {
    if (!selectedPlot) return;
    const initial = computeInitialYieldValue(
      selectedPlot.activeCycleExpectedYieldFull, selectedPlot.activeCycleExpectedYieldUnit,
      selectedPlot.currentYieldPct,
    );
    setForm(prev => ({ ...prev, yieldQuantityKg: initial.quantityKg, yieldPct: initial.yieldPct }));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedPlot?.id]);

  function captureGps() {
    if (!navigator.geolocation) { setGpsError('เบราว์เซอร์ไม่รองรับ GPS'); return; }
    setGpsLoading(true); setGpsError('');
    navigator.geolocation.getCurrentPosition(
      pos => {
        set('latitude', parseFloat(pos.coords.latitude.toFixed(7)));
        set('longitude', parseFloat(pos.coords.longitude.toFixed(7)));
        setGpsLoading(false);
      },
      () => { setGpsError('ไม่สามารถรับ GPS ได้ กรุณาอนุญาต Location'); setGpsLoading(false); },
      { timeout: 8000 },
    );
  }

  // Request GPS once as soon as the form mounts, reuse the value thereafter
  // (never re-ask just to "refresh" it).
  useEffect(() => {
    if (form.latitude == null) {
      captureGps();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  async function handleQrScan(raw: string) {
    setQrOpen(false);
    const parsed = parsePlotQr(raw);
    if (!parsed) {
      setQrError('QR ไม่ถูกต้อง ไม่รองรับรูปแบบนี้ — กรุณาสแกนใหม่หรือกรอกข้อมูลด้วยตนเอง');
      return;
    }
    setQrError('');
    setQrLookupLoading(true);
    try {
      // Round 20 — new signs print an opaque qrKey; legacy signs still
      // scan to supplierCode+plotCode. Both resolve via a sibling lookup
      // endpoint with identical generic-404/RLS-scoped behavior.
      const result = parsed.mode === 'qr'
        ? await lookupPlotByQrKey(parsed.qrKey)
        : await lookupPlotByQr({ supplierCode: parsed.supplierCode, plotCode: parsed.plotCode });
      set('supplierId', result.supplierId);
      set('plotId', result.plotId);
    } catch (err) {
      const notFound = axios.isAxiosError(err) && err.response?.status === 404;
      setQrError(
        notFound
          ? (parsed.mode === 'qr'
              ? 'ไม่พบแปลงจาก QR นี้ในระบบ หรือคุณไม่มีสิทธิ์เข้าถึง — กรุณาเลือกด้วยตนเอง'
              : `ไม่พบแปลงรหัส "${parsed.plotCode}" ของ Supplier "${parsed.supplierCode}" ในระบบ หรือคุณไม่มีสิทธิ์เข้าถึง — กรุณาเลือกด้วยตนเอง`)
          : 'ค้นหาข้อมูลจาก QR ไม่สำเร็จ กรุณาลองใหม่หรือกรอกข้อมูลด้วยตนเอง',
      );
    } finally {
      setQrLookupLoading(false);
    }
  }

  const createM = useMutation({
    // Photos are optional — the multipart endpoint requires at least one
    // file part, so a zero-photo submit uses the plain JSON create instead.
    mutationFn: ({ payload, photos }: { payload: RecordCreatePayload; photos: File[] }) =>
      photos.length > 0 ? createRecordWithPhotos(payload, photos) : createRecord(payload),
    onSuccess: (record) => {
      qc.invalidateQueries({ queryKey: ['records'] });
      navigate(`/farmlog/records/${record.id}`);
    },
  });

  async function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    setFormError('');
    // Round 8-14B hard guard — never rely on the submit button's `disabled`
    // attribute alone (a queued/already-in-flight native form submit, or a
    // test dispatching submit directly, would bypass it).
    if (photoProcessing) { setFormError('กรุณารอให้ระบบเตรียมรูปภาพเสร็จก่อน'); return; }
    if (!form.supplierId) { setFormError('กรุณาเลือก Supplier'); return; }
    if (!form.plotId) { setFormError('กรุณาเลือกแปลง'); return; }

    // Round 8-27B — the protocol scores are OPTIONAL, so there is no submit
    // gate for them any more (the backend dropped the matching 422). An
    // inspector who can only judge some of the criteria submits what they
    // saw; the unscored slots are stored as nulls.

    // Round 8-8B, threshold relaxed round 8-8B.1 — block the same invalid-kg
    // cases the backend would still 422 on (negative, >2 decimals, numeric
    // overflow, or a derived % over the 9999.9 storage ceiling) before ever
    // making the request. A result over 150% is NOT one of these anymore —
    // it's a genuine, storable value; YieldQuantityInput shows a
    // non-blocking amber notice for it, but Submit stays enabled.
    const yieldError = validateYieldQuantityKg(form.yieldQuantityKg, targetKg);
    if (yieldError) { setFormError(yieldError); return; }

    // GPS and photos are both optional now (per the field-UX change that
    // also added the 5th ปัญหาอื่นๆ photo slot) — no submit gate for either.

    const errs: Record<string, string> = {};
    const customPayload: Record<string, unknown> = {};
    for (const f of customFields) {
      const v = customValues[f.key] ?? null;
      const err = validateField(f, v);
      if (err) errs[f.key] = err;
      if (v !== null && v !== '') customPayload[f.key] = v;
    }
    setCustomErrors(errs);
    if (Object.keys(errs).length > 0) return;

    const payload: RecordCreatePayload = {
      supplierId: form.supplierId,
      plotId: form.plotId,
      submittedByName: form.submittedByName.trim() || null,
      // Round 8-19.1 — Thai business date, resolved at submit time.
      recordDate: bangkokToday(),
      plantingDate: selectedPlot?.currentPlantingDate ?? null,
      crop: selectedPlot?.currentCrop ?? null,
      variety: selectedPlot?.currentVariety ?? null,
      growthStage: form.growthStage,
      weatherCondition: form.weatherCondition,
      // Round 8-8B — kg is the primary input; yieldPct still travels as a
      // preview (contract #10), but the Backend always overwrites it with
      // its own server-derived value whenever yieldQuantityKg is present
      // (round 8-8A). A null kg means null pct too — never a stray 100.
      yieldQuantityKg: form.yieldQuantityKg,
      yieldPct: form.yieldPct,
      fieldPrepScore: form.fieldPrepScore,
      weatherScore: form.weatherScore,
      careScore: form.careScore,
      varietyResistanceScore: form.varietyResistanceScore,
      recommendation: form.recommendation || null,
      notes: form.notes || null,
      latitude: form.latitude,
      longitude: form.longitude,
      // photoUrls deliberately omitted — the uploaded files (below) are the
      // only source of truth; the backend overwrites whatever's here.
      customFields: customPayload,
    };
    try {
      const photos = photoFiles.filter((p): p is File => p !== null);
      await createM.mutateAsync({ payload, photos });
    } catch (err) {
      // Without this, a rejected save is an unhandled promise rejection —
      // the button un-spins and NOTHING appears on screen, which reads as
      // "กด save แล้วไม่บันทึก". Surface the failure where formError
      // already renders, with enough detail (status + backend detail) to
      // act on.
      setFormError(describeSubmitError(err));
    }
  }

  const submitting = createM.isPending || photoProcessing;
  // Round 8-25O — พันธุ์/สายพันธุ์ is Chiatai-internal-only; a Supplier-side
  // logged-in user (supplier:*/farmlog:field_officer) never sees it here.
  const canSeeVariety = canViewVariety(user?.roles);
  const filledByName = user?.fullName || user?.email || '';

  return (
    <div className="container mx-auto px-4 py-8 sm:px-6 lg:px-8 max-w-3xl">
      <div className="mb-6 flex items-center gap-3">
        <button onClick={() => navigate('/farmlog/records')}
          className="rounded-md p-1.5 text-muted-foreground hover:bg-secondary hover:text-foreground">
          <ArrowLeft className="h-5 w-5" />
        </button>
        <h1 className="text-2xl font-bold text-foreground">บันทึกการตรวจแปลงใหม่</h1>
      </div>

      <form onSubmit={onSubmit} className="space-y-6">
        {/* ข้อมูลพื้นฐาน */}
        <section className="rounded-lg border border-border bg-card p-5 shadow-sm">
          <h2 className="mb-4 text-base font-semibold text-foreground">ข้อมูลพื้นฐาน</h2>
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
            <Field label="Supplier" required>
              <select value={form.supplierId} onChange={e => { set('supplierId', e.target.value); set('plotId', ''); setSelectedPlot(null); }}
                className={inputCls}>
                <option value="">— เลือก Supplier —</option>
                {suppliers.map(s => <option key={s.id} value={s.id}>{s.name}</option>)}
              </select>
            </Field>

            <Field label="แปลง" required>
              <div className="flex items-center gap-2">
                <div className="min-w-0 flex-1">
                  <SmartPlotPicker
                    supplierId={form.supplierId || undefined}
                    value={form.plotId}
                    onChange={(plotId, plot) => {
                      set('plotId', plotId);
                      setSelectedPlot(plot);
                      // A plot belongs to exactly one supplier — keep
                      // supplierId locked to the chosen plot's owner so the
                      // pair can never be submitted mismatched (the backend
                      // also derives it from the plot, but this keeps the
                      // visible Supplier in sync too).
                      if (plot) set('supplierId', plot.supplierId);
                    }}
                    disabled={!form.supplierId}
                  />
                </div>
                <button
                  type="button"
                  onClick={() => { setQrError(''); setQrOpen(true); }}
                  disabled={qrLookupLoading}
                  title="สแกน QR ป้ายหน้าแปลง เพื่อเลือก Supplier และแปลงอัตโนมัติ"
                  className="flex shrink-0 items-center gap-1 rounded-md border border-input px-3 py-2 text-xs font-medium text-muted-foreground shadow-sm hover:border-green-400 hover:text-green-600 disabled:opacity-50"
                >
                  {qrLookupLoading ? <Loader2 className="h-4 w-4 animate-spin" /> : <QrCode className="h-4 w-4" />}
                  Scan QR
                </button>
              </div>
              {qrError && <p className="mt-1 text-xs text-red-600">{qrError}</p>}
            </Field>

            <Field label="ชื่อผู้กรอกข้อมูล">
              <input type="text" value={form.submittedByName}
                onChange={e => set('submittedByName', e.target.value)}
                maxLength={255} placeholder="ไม่บังคับ" className={inputCls} />
            </Field>

            <Field label="วันที่บันทึก">
              <div className={`${inputCls} flex items-center bg-muted text-muted-foreground`}>
                {bangkokToday()} <span className="ml-2 text-xs text-muted-foreground">(อัตโนมัติ)</span>
              </div>
            </Field>

            <Field label="บันทึกโดย (ผู้ใช้งานระบบ)">
              <div className={`${inputCls} flex items-center gap-1.5 bg-muted text-muted-foreground`}>
                <UserIcon className="h-3.5 w-3.5 shrink-0" /> {filledByName || '—'}
              </div>
            </Field>

            <div className="sm:col-span-2">
              <Field label="ระยะการเจริญเติบโต">
                <MasterDataButtons type="growth_stage" value={form.growthStage}
                  onChange={v => set('growthStage', v)} />
              </Field>
            </div>

            <div className="sm:col-span-2">
              <Field label="สภาพอากาศ (เลือกได้หลายตัวเลือก)">
                <MasterDataButtons type="weather" value={form.weatherCondition}
                  multiple onChange={v => set('weatherCondition', v)} />
              </Field>
            </div>
          </div>
        </section>

        {/* ข้อมูลแปลง (read-only, auto จาก Plot ที่เลือก) */}
        {selectedPlot && (
          <section className="rounded-lg border border-border bg-muted p-5 shadow-sm">
            <h2 className="mb-3 text-base font-semibold text-foreground">ข้อมูลแปลง</h2>
            <dl className="grid grid-cols-2 gap-x-4 gap-y-2 text-sm sm:grid-cols-3">
              <div>
                <dt className="text-xs text-muted-foreground">เลขที่แปลง</dt>
                <dd className="font-medium text-foreground">{selectedPlot.plotCode}</dd>
              </div>
              <div>
                <dt className="text-xs text-muted-foreground">ชนิดพืช</dt>
                <dd className="text-foreground">{selectedPlot.currentCrop || emptyText}</dd>
              </div>
              {canSeeVariety && (
                <div>
                  <dt className="text-xs text-muted-foreground">พันธุ์/สายพันธุ์</dt>
                  <dd className="text-foreground">{selectedPlot.currentVariety || emptyText}</dd>
                </div>
              )}
              <div>
                <dt className="text-xs text-muted-foreground">Lot No.</dt>
                <dd className="text-foreground">{selectedPlot.currentLotNo || emptyText}</dd>
              </div>
              <div>
                <dt className="text-xs text-muted-foreground">วันที่ปลูก</dt>
                <dd className="text-foreground">{selectedPlot.currentPlantingDate || emptyText}</dd>
              </div>
              <div>
                <dt className="text-xs text-muted-foreground">หมู่บ้าน</dt>
                <dd className="text-foreground">{selectedPlot.village || emptyText}</dd>
              </div>
              <div>
                <dt className="text-xs text-muted-foreground">อำเภอ</dt>
                <dd className="text-foreground">{selectedPlot.district || emptyText}</dd>
              </div>
              <div>
                <dt className="text-xs text-muted-foreground">จังหวัด</dt>
                <dd className="text-foreground">{selectedPlot.province || emptyText}</dd>
              </div>
              {(() => {
                const lat = formatFixed(selectedPlot.latitude, 6);
                const lng = formatFixed(selectedPlot.longitude, 6);
                return lat != null && lng != null && (
                  <div className="col-span-2 sm:col-span-2">
                    <dt className="text-xs text-muted-foreground">GPS แปลง</dt>
                    <dd className="flex items-center gap-1 font-mono text-xs text-foreground">
                      <MapPin className="h-3 w-3 text-muted-foreground" />
                      {lat}, {lng}
                    </dd>
                  </div>
                );
              })()}
            </dl>
          </section>
        )}

        {/* Yield */}
        <section className="rounded-lg border border-green-200 bg-green-50/50 p-5 shadow-sm">
          <h2 className="mb-1 text-base font-semibold text-foreground">ผลผลิต (Yield)</h2>
          <p className="mb-4 text-xs text-muted-foreground">กรอกปริมาณที่คาดว่าจะได้เป็น kg — ระบบคำนวณเปอร์เซ็นต์เทียบเป้าผลิตให้อัตโนมัติ</p>
          <YieldQuantityInput
            quantityKg={form.yieldQuantityKg}
            yieldPct={form.yieldPct}
            expectedYieldFull={selectedPlot?.activeCycleExpectedYieldFull}
            expectedYieldUnit={selectedPlot?.activeCycleExpectedYieldUnit}
            latestYieldPct={selectedPlot?.currentYieldPct}
            onChange={({ quantityKg, yieldPct }) => setForm(prev => ({ ...prev, yieldQuantityKg: quantityKg, yieldPct }))}
            error={yieldFormError}
          />
          {selectedPlot && latestYieldPct != null && (
            <p className="mt-2 text-xs text-muted-foreground">
              ค่าเริ่มต้นดึงจากการตรวจล่าสุดของแปลงนี้
              {selectedPlot.lastInspectedAt
                ? ` เมื่อ ${new Date(selectedPlot.lastInspectedAt).toLocaleDateString('th-TH', { day: 'numeric', month: 'short', year: 'numeric' })}`
                : ''}
              {selectedPlot.currentStage ? ` · ระยะ: ${selectedPlot.currentStage}` : ''}
            </p>
          )}
        </section>

        {/* การประเมินสภาพแปลง (คะแนน 1-10) — เกณฑ์ 4 ข้อตาม Protocol ของระยะ */}
        <section className="rounded-lg border border-border bg-card p-5 shadow-sm">
          <h2 className="mb-1 text-base font-semibold text-foreground">การประเมินสภาพแปลง (คะแนน 1–10)</h2>
          <p className="mb-4 text-xs text-muted-foreground">
            {stageProtocol
              ? `เกณฑ์ตามระยะ "${stageProtocol.growthStage}" — ให้คะแนนเท่าที่ประเมินได้ ไม่บังคับครบทุกข้อ`
              : 'เกณฑ์การให้คะแนนขึ้นกับระยะการเจริญเติบโตที่เลือก'}
          </p>
          <ProtocolScoreInputs
            protocol={stageProtocol}
            stageSelected={!!form.growthStage}
            loading={protocolsLoading}
            loadError={protocolsError}
            disabled={false}
            scores={{
              fieldPrepScore: form.fieldPrepScore,
              weatherScore: form.weatherScore,
              careScore: form.careScore,
              varietyResistanceScore: form.varietyResistanceScore,
            }}
            onChange={(slot, v) => set(slot, v)}
          />
        </section>

        {/* GPS */}
        <section className="rounded-lg border border-border bg-card p-5 shadow-sm">
          <h2 className="mb-4 text-base font-semibold text-foreground">
            พิกัด GPS ขณะตรวจ <span className="text-xs font-normal text-muted-foreground">(ไม่บังคับ)</span>
          </h2>
          <div className="space-y-3">
            <button type="button" onClick={captureGps} disabled={gpsLoading}
              className="inline-flex items-center gap-2 rounded-md border border-input bg-card px-4 py-2 text-sm font-medium text-foreground shadow-sm hover:bg-secondary disabled:opacity-50">
              {gpsLoading ? <Loader2 className="h-4 w-4 animate-spin" /> : <Navigation className="h-4 w-4 text-green-600" />}
              {gpsLoading ? 'กำลังรับตำแหน่ง GPS...' : 'บันทึก GPS ปัจจุบัน'}
            </button>
            {gpsError && <p className="text-xs text-red-600">{gpsError}</p>}
            {(form.latitude != null || form.longitude != null) && (
              <div className="flex items-center gap-3 rounded-md bg-green-50 px-3 py-2 text-sm">
                <Navigation className="h-4 w-4 text-green-600 shrink-0" />
                <span className="text-green-800 font-mono text-xs">{form.latitude?.toFixed(6)}, {form.longitude?.toFixed(6)}</span>
                <button type="button" onClick={() => { set('latitude', null); set('longitude', null); }}
                  className="ml-auto text-green-500 hover:text-green-700"><X className="h-3.5 w-3.5" /></button>
              </div>
            )}
          </div>
        </section>

        {/* ภาพถ่าย — 5 ช่องตายตัว ไม่บังคับ */}
        <section className="rounded-lg border border-border bg-card p-5 shadow-sm">
          <PhotoSlotPicker slots={photoFiles} onChange={setPhotoFiles} disabled={false}
            onProcessingChange={setPhotoProcessing} />
        </section>

        {/* ฟิลด์เพิ่มเติม (custom — Step 12) */}
        {customFields.length > 0 && (
          <section className="rounded-lg border border-border bg-card p-5 shadow-sm">
            <h2 className="mb-4 text-base font-semibold text-foreground">ฟิลด์เพิ่มเติม</h2>
            <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
              {customFields.map(f => (
                <DynamicFieldRenderer key={f.key} field={f} value={customValues[f.key] ?? null}
                  disabled={false} error={customErrors[f.key]}
                  onChange={v => setCustomValues(prev => ({ ...prev, [f.key]: v }))} />
              ))}
            </div>
          </section>
        )}

        {/* สรุป (text) */}
        <section className="rounded-lg border border-border bg-card p-5 shadow-sm">
          <h2 className="mb-4 text-base font-semibold text-foreground">สรุปและคำแนะนำ</h2>
          <div className="space-y-4">
            <Field label="คำแนะนำ">
              <textarea value={form.recommendation} onChange={e => set('recommendation', e.target.value)}
                rows={3} placeholder="คำแนะนำสำหรับเกษตรกร" className={inputCls} />
            </Field>
            <Field label="หมายเหตุ">
              <textarea value={form.notes} onChange={e => set('notes', e.target.value)}
                rows={2} placeholder="หมายเหตุเพิ่มเติม" className={inputCls} />
            </Field>
          </div>
        </section>

        {formError && <p className="text-sm text-red-600">{formError}</p>}

        <div className="flex justify-end gap-3">
          <button type="button" onClick={() => navigate('/farmlog/records')}
            className="rounded-md border border-input px-4 py-2 text-sm font-medium text-foreground hover:bg-secondary">
            ยกเลิก
          </button>
          <button type="submit" disabled={submitting}
            className="inline-flex items-center gap-1.5 rounded-md bg-green-600 px-4 py-2 text-sm font-semibold text-white hover:bg-green-700 disabled:opacity-50">
            <Save className="h-4 w-4" />
            บันทึก
          </button>
        </div>
      </form>

      {qrOpen && <LazyPlotQrScan onResult={handleQrScan} onClose={() => setQrOpen(false)} />}
    </div>
  );
}
