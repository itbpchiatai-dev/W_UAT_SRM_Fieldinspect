/**
 * RecordForm — create / edit a field inspection record (Step 12.5).
 *
 * List-driven: most fields are <select> sourced from editable Master Data, for
 * fast on-site capture. Yield is a 0–150% slider (default 100). Only คำแนะนำ /
 * หมายเหตุ are free text. Keeps Step-11 Smart Plot Picker, GPS, photo preview
 * and the Step-12 dynamic custom-fields section.
 */
import { useEffect, useRef, useState } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { ArrowLeft, Save, Navigation, Camera, X, Loader2 } from 'lucide-react';
import {
  getRecord,
  createRecord,
  updateRecord,
  type RecordCreatePayload,
} from '../../api/records';
import { listSuppliers } from '../../api/suppliers';
import { SmartPlotPicker } from '../../components/farmlog/SmartPlotPicker';
import { MasterDataButtons } from '../../components/farmlog/MasterDataButtons';
import { listFieldDefinitions } from '../../api/fielddefs';
import {
  DynamicFieldRenderer,
  validateField,
  type FieldValue,
} from '../../components/farmlog/fieldRegistry';
import { useHasPermission } from '../../hooks/useHasPermission';

interface FormState {
  supplierId: string;
  plotId: string;
  recordDate: string;
  plantingDate: string;
  crop: string | null;
  variety: string | null;
  growthStage: string | null;
  weatherCondition: string | null;
  yieldPct: number;
  fieldPrepLevel: string | null;
  careLevel: string | null;
  pestStatus: string | null;
  diseaseStatus: string | null;
  weedStatus: string | null;
  irrigationMethod: string | null;
  fertilizer: string | null;
  recommendation: string;
  notes: string;
  latitude: number | null;
  longitude: number | null;
}

const EMPTY: FormState = {
  supplierId: '', plotId: '', recordDate: new Date().toISOString().slice(0, 10),
  plantingDate: '', crop: null, variety: null, growthStage: null, weatherCondition: null,
  yieldPct: 100, fieldPrepLevel: null, careLevel: null, pestStatus: null, diseaseStatus: null,
  weedStatus: null, irrigationMethod: null, fertilizer: null, recommendation: '', notes: '',
  latitude: null, longitude: null,
};

const inputCls = 'w-full rounded-md border border-gray-300 px-3 py-2 text-sm shadow-sm focus:border-green-500 focus:outline-none focus:ring-1 focus:ring-green-500 disabled:bg-gray-50 disabled:text-gray-500';

function Field({ label, children, required }: { label: string; children: React.ReactNode; required?: boolean }) {
  return (
    <div>
      <label className="mb-1 block text-sm font-medium text-gray-700">
        {label}{required && <span className="ml-1 text-red-500">*</span>}
      </label>
      {children}
    </div>
  );
}

export function RecordForm() {
  const { id } = useParams<{ id: string }>();
  const isNew = !id || id === 'new';
  const navigate = useNavigate();
  const qc = useQueryClient();
  const canUpdate = useHasPermission('records.update');

  const [form, setForm] = useState<FormState>(EMPTY);
  const set = <K extends keyof FormState>(key: K, value: FormState[K]) =>
    setForm(prev => ({ ...prev, [key]: value }));
  const [formError, setFormError] = useState('');

  // Local photo previews (upload deferred to Step 15)
  const [photoPreviews, setPhotoPreviews] = useState<string[]>([]);
  const fileInputRef = useRef<HTMLInputElement>(null);

  const [gpsLoading, setGpsLoading] = useState(false);
  const [gpsError, setGpsError] = useState('');

  // Dynamic custom fields (Step 12)
  const [customValues, setCustomValues] = useState<Record<string, FieldValue>>({});
  const [customErrors, setCustomErrors] = useState<Record<string, string>>({});
  const { data: fieldDefs = [] } = useQuery({
    queryKey: ['fielddefs', 'active'],
    queryFn: () => listFieldDefinitions(true),
  });
  const customFields = fieldDefs.filter(f => !f.isCore && f.active);

  const { data: existing, isLoading: loadingRecord } = useQuery({
    queryKey: ['record', id],
    queryFn: () => getRecord(id!),
    enabled: !isNew,
  });

  const { data: suppliers = [] } = useQuery({
    queryKey: ['suppliers', 'all'],
    queryFn: () => listSuppliers({ activeOnly: true, limit: 200 }),
  });

  useEffect(() => {
    if (!existing) return;
    setForm({
      supplierId: existing.supplierId,
      plotId: existing.plotId,
      recordDate: existing.recordDate,
      plantingDate: existing.plantingDate ?? '',
      crop: existing.crop,
      variety: existing.variety,
      growthStage: existing.growthStage,
      weatherCondition: existing.weatherCondition,
      yieldPct: existing.yieldPct != null ? parseFloat(existing.yieldPct) : 100,
      fieldPrepLevel: existing.fieldPrepLevel,
      careLevel: existing.careLevel,
      pestStatus: existing.pestStatus,
      diseaseStatus: existing.diseaseStatus,
      weedStatus: existing.weedStatus,
      irrigationMethod: existing.irrigationMethod,
      fertilizer: existing.fertilizer,
      recommendation: existing.recommendation ?? '',
      notes: existing.notes ?? '',
      latitude: existing.latitude != null ? parseFloat(existing.latitude) : null,
      longitude: existing.longitude != null ? parseFloat(existing.longitude) : null,
    });
    if (existing.photoUrls?.length) setPhotoPreviews(existing.photoUrls);
    if (existing.customFields) setCustomValues(existing.customFields as Record<string, FieldValue>);
  }, [existing]);

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

  function handlePhotoChange(e: React.ChangeEvent<HTMLInputElement>) {
    const files = Array.from(e.target.files ?? []);
    files.forEach(file => {
      const reader = new FileReader();
      reader.onload = ev => { if (ev.target?.result) setPhotoPreviews(prev => [...prev, ev.target!.result as string]); };
      reader.readAsDataURL(file);
    });
    e.target.value = '';
  }
  const removePhoto = (idx: number) => setPhotoPreviews(prev => prev.filter((_, i) => i !== idx));

  const createM = useMutation({
    mutationFn: (data: RecordCreatePayload) => createRecord(data),
    onSuccess: (record) => {
      qc.invalidateQueries({ queryKey: ['records'] });
      navigate(`/farmlog/records/${record.id}`);
    },
  });
  const updateM = useMutation({
    mutationFn: (data: Partial<RecordCreatePayload>) => updateRecord(id!, data),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['record', id] });
      qc.invalidateQueries({ queryKey: ['records'] });
    },
  });

  async function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    setFormError('');
    if (!form.supplierId) { setFormError('กรุณาเลือก Supplier'); return; }
    if (!form.plotId) { setFormError('กรุณาเลือกแปลง'); return; }
    if (!form.recordDate) { setFormError('กรุณาระบุวันที่ตรวจ'); return; }

    // Validate dynamic custom fields
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
      recordDate: form.recordDate,
      plantingDate: form.plantingDate || null,
      crop: form.crop,
      variety: form.variety,
      growthStage: form.growthStage,
      weatherCondition: form.weatherCondition,
      yieldPct: form.yieldPct,
      fieldPrepLevel: form.fieldPrepLevel,
      careLevel: form.careLevel,
      pestStatus: form.pestStatus,
      diseaseStatus: form.diseaseStatus,
      weedStatus: form.weedStatus,
      irrigationMethod: form.irrigationMethod,
      fertilizer: form.fertilizer,
      recommendation: form.recommendation || null,
      notes: form.notes || null,
      latitude: form.latitude,
      longitude: form.longitude,
      customFields: customPayload,
    };
    if (isNew) await createM.mutateAsync(payload);
    else await updateM.mutateAsync(payload);
  }

  const isReadOnly = !isNew && !canUpdate;
  const submitting = createM.isPending || updateM.isPending;

  if (!isNew && loadingRecord) {
    return <div className="flex justify-center py-20 text-gray-400">กำลังโหลด...</div>;
  }

  return (
    <div className="container mx-auto px-4 py-8 sm:px-6 lg:px-8 max-w-3xl">
      <div className="mb-6 flex items-center gap-3">
        <button onClick={() => navigate('/farmlog/records')}
          className="rounded-md p-1.5 text-gray-400 hover:bg-gray-100 hover:text-gray-600">
          <ArrowLeft className="h-5 w-5" />
        </button>
        <h1 className="text-2xl font-bold text-gray-900">
          {isNew ? 'บันทึกการตรวจแปลงใหม่' : `บันทึก ${existing?.recordDate ?? ''}`}
        </h1>
      </div>

      <form onSubmit={onSubmit} className="space-y-6">
        {/* ข้อมูลพื้นฐาน */}
        <section className="rounded-lg border border-gray-200 bg-white p-5 shadow-sm">
          <h2 className="mb-4 text-base font-semibold text-gray-800">ข้อมูลพื้นฐาน</h2>
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
            <Field label="Supplier" required>
              <select value={form.supplierId} onChange={e => { set('supplierId', e.target.value); set('plotId', ''); }}
                disabled={!isNew || isReadOnly} className={inputCls}>
                <option value="">— เลือก Supplier —</option>
                {suppliers.map(s => <option key={s.id} value={s.id}>{s.name}</option>)}
              </select>
            </Field>

            <Field label="แปลง" required>
              <SmartPlotPicker
                supplierId={form.supplierId || undefined}
                value={form.plotId}
                onChange={(plotId) => set('plotId', plotId)}
                disabled={!isNew || isReadOnly || !form.supplierId}
              />
            </Field>

            <Field label="วันที่ตรวจ" required>
              <input type="date" value={form.recordDate} onChange={e => set('recordDate', e.target.value)}
                disabled={isReadOnly} className={inputCls} />
            </Field>

            <Field label="วันที่ปลูก">
              <input type="date" value={form.plantingDate} onChange={e => set('plantingDate', e.target.value)}
                disabled={isReadOnly} className={inputCls} />
            </Field>

            <div className="sm:col-span-2">
              <Field label="ชนิดพืช">
                <MasterDataButtons type="crop" value={form.crop} disabled={isReadOnly}
                  onChange={v => { set('crop', v); set('variety', null); }} />
              </Field>
            </div>

            <div className="sm:col-span-2">
              <Field label="พันธุ์/สายพันธุ์">
                {form.crop ? (
                  <MasterDataButtons type="variety" parent={form.crop} value={form.variety}
                    disabled={isReadOnly} onChange={v => set('variety', v)} />
                ) : (
                  <p className="text-xs text-gray-400 italic">— เลือกชนิดพืชก่อน —</p>
                )}
              </Field>
            </div>

            <div className="sm:col-span-2">
              <Field label="ระยะการเจริญเติบโต">
                <MasterDataButtons type="growth_stage" value={form.growthStage} disabled={isReadOnly}
                  onChange={v => set('growthStage', v)} />
              </Field>
            </div>

            <div className="sm:col-span-2">
              <Field label="สภาพอากาศ">
                <MasterDataButtons type="weather" value={form.weatherCondition} disabled={isReadOnly}
                  onChange={v => set('weatherCondition', v)} />
              </Field>
            </div>
          </div>
        </section>

        {/* Yield */}
        <section className="rounded-lg border border-green-200 bg-green-50/50 p-5 shadow-sm">
          <h2 className="mb-1 text-base font-semibold text-gray-800">ผลผลิต (Yield)</h2>
          <p className="mb-4 text-xs text-gray-500">% คาดว่าจะได้ผลผลิตเทียบเป้า (0–150%)</p>
          <div className="flex items-center gap-4">
            <input type="range" min={0} max={150} step={1} value={form.yieldPct}
              disabled={isReadOnly} onChange={e => set('yieldPct', Number(e.target.value))}
              className="h-2 flex-1 cursor-pointer accent-green-600 disabled:cursor-not-allowed" />
            <span className="w-20 shrink-0 text-right text-2xl font-bold text-green-700">{form.yieldPct}%</span>
          </div>
        </section>

        {/* การประเมิน (list-driven) */}
        <section className="rounded-lg border border-gray-200 bg-white p-5 shadow-sm">
          <h2 className="mb-4 text-base font-semibold text-gray-800">การประเมินสภาพแปลง</h2>
          <div className="space-y-4">
            <Field label="การเตรียมแปลง">
              <MasterDataButtons type="level" value={form.fieldPrepLevel} disabled={isReadOnly}
                onChange={v => set('fieldPrepLevel', v)} />
            </Field>
            <Field label="การดูแลรักษา">
              <MasterDataButtons type="level" value={form.careLevel} disabled={isReadOnly}
                onChange={v => set('careLevel', v)} />
            </Field>
            <Field label="แมลงศัตรูพืช">
              <MasterDataButtons type="severity" value={form.pestStatus} disabled={isReadOnly}
                onChange={v => set('pestStatus', v)} />
            </Field>
            <Field label="โรคพืช">
              <MasterDataButtons type="severity" value={form.diseaseStatus} disabled={isReadOnly}
                onChange={v => set('diseaseStatus', v)} />
            </Field>
            <Field label="วัชพืช">
              <MasterDataButtons type="severity" value={form.weedStatus} disabled={isReadOnly}
                onChange={v => set('weedStatus', v)} />
            </Field>
            <Field label="วิธีการให้น้ำ">
              <MasterDataButtons type="irrigation" value={form.irrigationMethod} disabled={isReadOnly}
                onChange={v => set('irrigationMethod', v)} />
            </Field>
            <Field label="ปุ๋ยที่ใช้">
              <MasterDataButtons type="fertilizer" value={form.fertilizer} disabled={isReadOnly}
                onChange={v => set('fertilizer', v)} />
            </Field>
          </div>
        </section>

        {/* GPS */}
        <section className="rounded-lg border border-gray-200 bg-white p-5 shadow-sm">
          <h2 className="mb-4 text-base font-semibold text-gray-800">พิกัด GPS</h2>
          <div className="space-y-3">
            {!isReadOnly && (
              <button type="button" onClick={captureGps} disabled={gpsLoading}
                className="inline-flex items-center gap-2 rounded-md border border-gray-300 bg-white px-4 py-2 text-sm font-medium text-gray-700 shadow-sm hover:bg-gray-50 disabled:opacity-50">
                {gpsLoading ? <Loader2 className="h-4 w-4 animate-spin" /> : <Navigation className="h-4 w-4 text-green-600" />}
                บันทึก GPS ปัจจุบัน
              </button>
            )}
            {gpsError && <p className="text-xs text-red-600">{gpsError}</p>}
            {(form.latitude != null || form.longitude != null) && (
              <div className="flex items-center gap-3 rounded-md bg-green-50 px-3 py-2 text-sm">
                <Navigation className="h-4 w-4 text-green-600 shrink-0" />
                <span className="text-green-800 font-mono text-xs">{form.latitude?.toFixed(6)}, {form.longitude?.toFixed(6)}</span>
                {!isReadOnly && (
                  <button type="button" onClick={() => { set('latitude', null); set('longitude', null); }}
                    className="ml-auto text-green-500 hover:text-green-700"><X className="h-3.5 w-3.5" /></button>
                )}
              </div>
            )}
          </div>
        </section>

        {/* ภาพถ่าย */}
        {!isReadOnly && (
          <section className="rounded-lg border border-gray-200 bg-white p-5 shadow-sm">
            <h2 className="mb-4 text-base font-semibold text-gray-800">ภาพถ่าย</h2>
            <div className="space-y-3">
              <button type="button" onClick={() => fileInputRef.current?.click()}
                className="inline-flex items-center gap-2 rounded-md border border-dashed border-gray-300 px-4 py-2 text-sm text-gray-600 hover:border-green-400 hover:text-green-600">
                <Camera className="h-4 w-4" /> ถ่ายรูป / เลือกภาพ
              </button>
              <input ref={fileInputRef} type="file" accept="image/*" capture="environment" multiple
                className="hidden" onChange={handlePhotoChange} />
              {photoPreviews.length > 0 && (
                <div className="flex flex-wrap gap-2">
                  {photoPreviews.map((src, i) => (
                    <div key={i} className="relative">
                      <img src={src} alt={`ภาพ ${i + 1}`} className="h-20 w-20 rounded-md object-cover shadow-sm" />
                      <button type="button" onClick={() => removePhoto(i)}
                        className="absolute -right-1.5 -top-1.5 rounded-full bg-red-500 p-0.5 text-white shadow-sm hover:bg-red-600">
                        <X className="h-3 w-3" />
                      </button>
                    </div>
                  ))}
                </div>
              )}
              <p className="text-xs text-gray-400">การอัปโหลดภาพจะพร้อมใช้ใน Phase F (Step 15)</p>
            </div>
          </section>
        )}

        {/* ฟิลด์เพิ่มเติม (custom — Step 12) */}
        {customFields.length > 0 && (
          <section className="rounded-lg border border-gray-200 bg-white p-5 shadow-sm">
            <h2 className="mb-4 text-base font-semibold text-gray-800">ฟิลด์เพิ่มเติม</h2>
            <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
              {customFields.map(f => (
                <DynamicFieldRenderer key={f.key} field={f} value={customValues[f.key] ?? null}
                  disabled={isReadOnly} error={customErrors[f.key]}
                  onChange={v => setCustomValues(prev => ({ ...prev, [f.key]: v }))} />
              ))}
            </div>
          </section>
        )}

        {/* สรุป (text) */}
        <section className="rounded-lg border border-gray-200 bg-white p-5 shadow-sm">
          <h2 className="mb-4 text-base font-semibold text-gray-800">สรุปและคำแนะนำ</h2>
          <div className="space-y-4">
            <Field label="คำแนะนำ">
              <textarea value={form.recommendation} onChange={e => set('recommendation', e.target.value)}
                disabled={isReadOnly} rows={3} placeholder="คำแนะนำสำหรับเกษตรกร" className={inputCls} />
            </Field>
            <Field label="หมายเหตุ">
              <textarea value={form.notes} onChange={e => set('notes', e.target.value)}
                disabled={isReadOnly} rows={2} placeholder="หมายเหตุเพิ่มเติม" className={inputCls} />
            </Field>
          </div>
        </section>

        {formError && <p className="text-sm text-red-600">{formError}</p>}

        {!isReadOnly && (
          <div className="flex justify-end gap-3">
            <button type="button" onClick={() => navigate('/farmlog/records')}
              className="rounded-md border border-gray-300 px-4 py-2 text-sm font-medium text-gray-700 hover:bg-gray-50">
              ยกเลิก
            </button>
            <button type="submit" disabled={submitting}
              className="inline-flex items-center gap-1.5 rounded-md bg-green-600 px-4 py-2 text-sm font-semibold text-white hover:bg-green-700 disabled:opacity-50">
              <Save className="h-4 w-4" />
              {isNew ? 'บันทึก' : 'บันทึกการแก้ไข'}
            </button>
          </div>
        )}
      </form>
    </div>
  );
}
