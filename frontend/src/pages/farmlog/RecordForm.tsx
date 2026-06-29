/**
 * RecordForm — create / edit a field inspection record.
 * Route: /farmlog/records/new  (create)
 *        /farmlog/records/:id  (view + edit)
 *
 * Step 11 additions:
 *  - SmartPlotPicker replaces the plain plot <select>
 *  - GPS capture button fills lat/lng
 *  - Photo capture with local preview (upload deferred to Step 15)
 */
import { useEffect, useRef, useState } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import { useForm, Controller } from 'react-hook-form';
import { z } from 'zod';
import { zodResolver } from '@hookform/resolvers/zod';
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
import { useHasPermission } from '../../hooks/useHasPermission';
import { CROP_TYPES, WEATHER } from './RecordList';

const recordSchema = z.object({
  supplierId: z.string().uuid('กรุณาเลือก Supplier'),
  plotId: z.string().uuid('กรุณาเลือกแปลง'),
  recordDate: z.string().min(1, 'กรุณาระบุวันที่'),
  cropType: z.string().max(100).optional().nullable(),
  growthStage: z.string().max(100).optional().nullable(),
  areaRai: z.number().min(0).optional().nullable(),
  plantHeightCm: z.number().min(0).optional().nullable(),
  pestFound: z.boolean().default(false),
  pestDetail: z.string().optional().nullable(),
  pestSeverity: z.number().min(1).max(5).optional().nullable(),
  diseaseFound: z.boolean().default(false),
  diseaseDetail: z.string().optional().nullable(),
  diseaseSeverity: z.number().min(1).max(5).optional().nullable(),
  weedSeverity: z.number().min(0).max(5).optional().nullable(),
  fertilizerUsed: z.string().max(255).optional().nullable(),
  fertilizerAmountKg: z.number().min(0).optional().nullable(),
  irrigationMethod: z.string().max(100).optional().nullable(),
  weatherCondition: z.string().max(100).optional().nullable(),
  recommendation: z.string().optional().nullable(),
  notes: z.string().optional().nullable(),
  latitude: z.number().min(-90).max(90).optional().nullable(),
  longitude: z.number().min(-180).max(180).optional().nullable(),
});

type FormValues = z.infer<typeof recordSchema>;

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

const inputCls = 'w-full rounded-md border border-gray-300 px-3 py-2 text-sm shadow-sm focus:border-green-500 focus:outline-none focus:ring-1 focus:ring-green-500';
const severityOpts = [1, 2, 3, 4, 5];

export function RecordForm() {
  const { id } = useParams<{ id: string }>();
  const isNew = !id || id === 'new';
  const navigate = useNavigate();
  const qc = useQueryClient();
  const canUpdate = useHasPermission('records.update');

  // Local photo previews (not uploaded — Step 15 handles upload)
  const [photoPreviews, setPhotoPreviews] = useState<string[]>([]);
  const fileInputRef = useRef<HTMLInputElement>(null);

  // GPS capture state
  const [gpsLoading, setGpsLoading] = useState(false);
  const [gpsError, setGpsError] = useState('');

  const { data: existing, isLoading: loadingRecord } = useQuery({
    queryKey: ['record', id],
    queryFn: () => getRecord(id!),
    enabled: !isNew,
  });

  const { data: suppliers = [] } = useQuery({
    queryKey: ['suppliers', 'all'],
    queryFn: () => listSuppliers({ activeOnly: true, limit: 200 }),
  });

  const {
    register,
    handleSubmit,
    watch,
    reset,
    setValue,
    control,
    formState: { errors, isSubmitting },
  } = useForm<FormValues>({
    resolver: zodResolver(recordSchema),
    defaultValues: {
      pestFound: false,
      diseaseFound: false,
      recordDate: new Date().toISOString().slice(0, 10),
    },
  });

  const selectedSupplier = watch('supplierId');
  const pestFound = watch('pestFound');
  const diseaseFound = watch('diseaseFound');
  const latValue = watch('latitude');
  const lngValue = watch('longitude');

  useEffect(() => {
    if (existing) {
      reset({
        supplierId: existing.supplierId,
        plotId: existing.plotId,
        recordDate: existing.recordDate,
        cropType: existing.cropType,
        growthStage: existing.growthStage,
        areaRai: existing.areaRai ? parseFloat(existing.areaRai) : null,
        plantHeightCm: existing.plantHeightCm ? parseFloat(existing.plantHeightCm) : null,
        pestFound: existing.pestFound,
        pestDetail: existing.pestDetail,
        pestSeverity: existing.pestSeverity,
        diseaseFound: existing.diseaseFound,
        diseaseDetail: existing.diseaseDetail,
        diseaseSeverity: existing.diseaseSeverity,
        weedSeverity: existing.weedSeverity,
        fertilizerUsed: existing.fertilizerUsed,
        fertilizerAmountKg: existing.fertilizerAmountKg ? parseFloat(existing.fertilizerAmountKg) : null,
        irrigationMethod: existing.irrigationMethod,
        weatherCondition: existing.weatherCondition,
        recommendation: existing.recommendation,
        notes: existing.notes,
        latitude: existing.latitude ? parseFloat(existing.latitude) : null,
        longitude: existing.longitude ? parseFloat(existing.longitude) : null,
      });
      if (existing.photoUrls?.length) {
        setPhotoPreviews(existing.photoUrls);
      }
    }
  }, [existing, reset]);

  function captureGps() {
    if (!navigator.geolocation) {
      setGpsError('เบราว์เซอร์ไม่รองรับ GPS');
      return;
    }
    setGpsLoading(true);
    setGpsError('');
    navigator.geolocation.getCurrentPosition(
      pos => {
        setValue('latitude', parseFloat(pos.coords.latitude.toFixed(7)));
        setValue('longitude', parseFloat(pos.coords.longitude.toFixed(7)));
        setGpsLoading(false);
      },
      () => {
        setGpsError('ไม่สามารถรับ GPS ได้ กรุณาอนุญาต Location');
        setGpsLoading(false);
      },
      { timeout: 8000 },
    );
  }

  function handlePhotoChange(e: React.ChangeEvent<HTMLInputElement>) {
    const files = Array.from(e.target.files ?? []);
    files.forEach(file => {
      const reader = new FileReader();
      reader.onload = ev => {
        if (ev.target?.result) {
          setPhotoPreviews(prev => [...prev, ev.target!.result as string]);
        }
      };
      reader.readAsDataURL(file);
    });
    e.target.value = '';
  }

  function removePhoto(idx: number) {
    setPhotoPreviews(prev => prev.filter((_, i) => i !== idx));
  }

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

  const onSubmit = async (values: FormValues) => {
    const payload: RecordCreatePayload = {
      supplierId: values.supplierId,
      plotId: values.plotId,
      recordDate: values.recordDate,
      cropType: values.cropType || null,
      growthStage: values.growthStage || null,
      areaRai: values.areaRai ?? null,
      plantHeightCm: values.plantHeightCm ?? null,
      pestFound: values.pestFound,
      pestDetail: values.pestFound ? (values.pestDetail || null) : null,
      pestSeverity: values.pestFound ? (values.pestSeverity ?? null) : null,
      diseaseFound: values.diseaseFound,
      diseaseDetail: values.diseaseFound ? (values.diseaseDetail || null) : null,
      diseaseSeverity: values.diseaseFound ? (values.diseaseSeverity ?? null) : null,
      weedSeverity: values.weedSeverity ?? null,
      fertilizerUsed: values.fertilizerUsed || null,
      fertilizerAmountKg: values.fertilizerAmountKg ?? null,
      irrigationMethod: values.irrigationMethod || null,
      weatherCondition: values.weatherCondition || null,
      recommendation: values.recommendation || null,
      notes: values.notes || null,
      latitude: values.latitude ?? null,
      longitude: values.longitude ?? null,
      // photo_urls upload deferred to Step 15
    };
    if (isNew) {
      await createM.mutateAsync(payload);
    } else {
      await updateM.mutateAsync(payload);
    }
  };

  const isReadOnly = !isNew && !canUpdate;

  if (!isNew && loadingRecord) {
    return <div className="flex justify-center py-20 text-gray-400">กำลังโหลด...</div>;
  }

  return (
    <div className="container mx-auto px-4 py-8 sm:px-6 lg:px-8 max-w-3xl">
      <div className="mb-6 flex items-center gap-3">
        <button
          onClick={() => navigate('/farmlog/records')}
          className="rounded-md p-1.5 text-gray-400 hover:bg-gray-100 hover:text-gray-600"
        >
          <ArrowLeft className="h-5 w-5" />
        </button>
        <h1 className="text-2xl font-bold text-gray-900">
          {isNew ? 'บันทึกการตรวจแปลงใหม่' : `บันทึก ${existing?.recordDate ?? ''}`}
        </h1>
      </div>

      <form onSubmit={handleSubmit(onSubmit)} className="space-y-6">
        {/* Section: ข้อมูลพื้นฐาน */}
        <section className="rounded-lg border border-gray-200 bg-white p-5 shadow-sm">
          <h2 className="mb-4 text-base font-semibold text-gray-800">ข้อมูลพื้นฐาน</h2>
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
            <Field label="Supplier" required>
              <select {...register('supplierId')} disabled={!isNew || isReadOnly} className={inputCls}>
                <option value="">— เลือก Supplier —</option>
                {suppliers.map(s => <option key={s.id} value={s.id}>{s.name}</option>)}
              </select>
              {errors.supplierId && <p className="mt-1 text-xs text-red-600">{errors.supplierId.message}</p>}
            </Field>

            {/* Smart Plot Picker */}
            <Field label="แปลง" required>
              <Controller
                name="plotId"
                control={control}
                render={({ field }) => (
                  <SmartPlotPicker
                    supplierId={selectedSupplier || undefined}
                    value={field.value ?? ''}
                    onChange={(plotId) => field.onChange(plotId)}
                    disabled={!isNew || isReadOnly || !selectedSupplier}
                    error={errors.plotId?.message}
                  />
                )}
              />
              {errors.plotId && <p className="mt-1 text-xs text-red-600">{errors.plotId.message}</p>}
            </Field>

            <Field label="วันที่ตรวจ" required>
              <input type="date" {...register('recordDate')} disabled={isReadOnly} className={inputCls} />
              {errors.recordDate && <p className="mt-1 text-xs text-red-600">{errors.recordDate.message}</p>}
            </Field>

            <Field label="ประเภทพืช">
              <select {...register('cropType')} disabled={isReadOnly} className={inputCls}>
                <option value="">— เลือก —</option>
                {CROP_TYPES.map(c => <option key={c} value={c}>{c}</option>)}
              </select>
            </Field>

            <Field label="ระยะการเจริญเติบโต">
              <input type="text" {...register('growthStage')} disabled={isReadOnly}
                placeholder="เช่น ระยะงอก, ระยะออกดอก" className={inputCls} />
            </Field>

            <Field label="พื้นที่ตรวจ (ไร่)">
              <input type="number" step="0.01" {...register('areaRai', { valueAsNumber: true })}
                disabled={isReadOnly} className={inputCls} />
            </Field>

            <Field label="ความสูงต้น (ซม.)">
              <input type="number" step="0.1" {...register('plantHeightCm', { valueAsNumber: true })}
                disabled={isReadOnly} className={inputCls} />
            </Field>

            <Field label="สภาพอากาศ">
              <select {...register('weatherCondition')} disabled={isReadOnly} className={inputCls}>
                <option value="">— เลือก —</option>
                {WEATHER.map(w => <option key={w} value={w}>{w}</option>)}
              </select>
            </Field>
          </div>
        </section>

        {/* Section: แมลงศัตรูพืช */}
        <section className="rounded-lg border border-gray-200 bg-white p-5 shadow-sm">
          <h2 className="mb-4 text-base font-semibold text-gray-800">แมลงศัตรูพืช</h2>
          <div className="space-y-3">
            <label className="flex items-center gap-2 cursor-pointer">
              <input type="checkbox" {...register('pestFound')} disabled={isReadOnly}
                className="h-4 w-4 rounded border-gray-300 text-green-600 focus:ring-green-500" />
              <span className="text-sm text-gray-700">พบแมลงศัตรูพืช</span>
            </label>
            {pestFound && (
              <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 pl-6">
                <Field label="รายละเอียด">
                  <input type="text" {...register('pestDetail')} disabled={isReadOnly}
                    placeholder="ระบุชนิดแมลง" className={inputCls} />
                </Field>
                <Field label="ระดับความรุนแรง (1-5)">
                  <select {...register('pestSeverity', { valueAsNumber: true })} disabled={isReadOnly} className={inputCls}>
                    <option value="">— เลือก —</option>
                    {severityOpts.map(v => <option key={v} value={v}>{v}</option>)}
                  </select>
                </Field>
              </div>
            )}
          </div>
        </section>

        {/* Section: โรคพืช */}
        <section className="rounded-lg border border-gray-200 bg-white p-5 shadow-sm">
          <h2 className="mb-4 text-base font-semibold text-gray-800">โรคพืช</h2>
          <div className="space-y-3">
            <label className="flex items-center gap-2 cursor-pointer">
              <input type="checkbox" {...register('diseaseFound')} disabled={isReadOnly}
                className="h-4 w-4 rounded border-gray-300 text-green-600 focus:ring-green-500" />
              <span className="text-sm text-gray-700">พบโรคพืช</span>
            </label>
            {diseaseFound && (
              <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 pl-6">
                <Field label="รายละเอียด">
                  <input type="text" {...register('diseaseDetail')} disabled={isReadOnly}
                    placeholder="ระบุชนิดโรค" className={inputCls} />
                </Field>
                <Field label="ระดับความรุนแรง (1-5)">
                  <select {...register('diseaseSeverity', { valueAsNumber: true })} disabled={isReadOnly} className={inputCls}>
                    <option value="">— เลือก —</option>
                    {severityOpts.map(v => <option key={v} value={v}>{v}</option>)}
                  </select>
                </Field>
              </div>
            )}
          </div>
        </section>

        {/* Section: การดูแลรักษา */}
        <section className="rounded-lg border border-gray-200 bg-white p-5 shadow-sm">
          <h2 className="mb-4 text-base font-semibold text-gray-800">การดูแลรักษา</h2>
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
            <Field label="วัชพืช (0=ไม่มี, 5=รุนแรงมาก)">
              <select {...register('weedSeverity', { valueAsNumber: true })} disabled={isReadOnly} className={inputCls}>
                <option value="">— เลือก —</option>
                {[0, 1, 2, 3, 4, 5].map(v => <option key={v} value={v}>{v}</option>)}
              </select>
            </Field>

            <Field label="วิธีการให้น้ำ">
              <input type="text" {...register('irrigationMethod')} disabled={isReadOnly}
                placeholder="เช่น ระบบน้ำหยด, ฝนเทียม" className={inputCls} />
            </Field>

            <Field label="ปุ๋ยที่ใช้">
              <input type="text" {...register('fertilizerUsed')} disabled={isReadOnly}
                placeholder="ชื่อสูตรปุ๋ย" className={inputCls} />
            </Field>

            <Field label="ปริมาณปุ๋ย (กก./ไร่)">
              <input type="number" step="0.01" {...register('fertilizerAmountKg', { valueAsNumber: true })}
                disabled={isReadOnly} className={inputCls} />
            </Field>
          </div>
        </section>

        {/* Section: GPS */}
        <section className="rounded-lg border border-gray-200 bg-white p-5 shadow-sm">
          <h2 className="mb-4 text-base font-semibold text-gray-800">พิกัด GPS</h2>
          <div className="space-y-3">
            {!isReadOnly && (
              <button
                type="button"
                onClick={captureGps}
                disabled={gpsLoading}
                className="inline-flex items-center gap-2 rounded-md border border-gray-300 bg-white px-4 py-2 text-sm font-medium text-gray-700 shadow-sm hover:bg-gray-50 disabled:opacity-50"
              >
                {gpsLoading ? (
                  <Loader2 className="h-4 w-4 animate-spin" />
                ) : (
                  <Navigation className="h-4 w-4 text-green-600" />
                )}
                บันทึก GPS ปัจจุบัน
              </button>
            )}
            {gpsError && <p className="text-xs text-red-600">{gpsError}</p>}
            {(latValue != null || lngValue != null) && (
              <div className="flex items-center gap-3 rounded-md bg-green-50 px-3 py-2 text-sm">
                <Navigation className="h-4 w-4 text-green-600 shrink-0" />
                <span className="text-green-800 font-mono text-xs">
                  {latValue?.toFixed(6)}, {lngValue?.toFixed(6)}
                </span>
                {!isReadOnly && (
                  <button
                    type="button"
                    onClick={() => { setValue('latitude', null); setValue('longitude', null); }}
                    className="ml-auto text-green-500 hover:text-green-700"
                  >
                    <X className="h-3.5 w-3.5" />
                  </button>
                )}
              </div>
            )}
            {/* Hidden inputs for form registration */}
            <input type="hidden" {...register('latitude', { valueAsNumber: true })} />
            <input type="hidden" {...register('longitude', { valueAsNumber: true })} />
          </div>
        </section>

        {/* Section: ภาพถ่าย (local preview — upload in Step 15) */}
        {!isReadOnly && (
          <section className="rounded-lg border border-gray-200 bg-white p-5 shadow-sm">
            <h2 className="mb-4 text-base font-semibold text-gray-800">ภาพถ่าย</h2>
            <div className="space-y-3">
              <button
                type="button"
                onClick={() => fileInputRef.current?.click()}
                className="inline-flex items-center gap-2 rounded-md border border-dashed border-gray-300 px-4 py-2 text-sm text-gray-600 hover:border-green-400 hover:text-green-600"
              >
                <Camera className="h-4 w-4" />
                ถ่ายรูป / เลือกภาพ
              </button>
              <input
                ref={fileInputRef}
                type="file"
                accept="image/*"
                capture="environment"
                multiple
                className="hidden"
                onChange={handlePhotoChange}
              />
              {photoPreviews.length > 0 && (
                <div className="flex flex-wrap gap-2">
                  {photoPreviews.map((src, i) => (
                    <div key={i} className="relative">
                      <img src={src} alt={`ภาพ ${i + 1}`} className="h-20 w-20 rounded-md object-cover shadow-sm" />
                      <button
                        type="button"
                        onClick={() => removePhoto(i)}
                        className="absolute -right-1.5 -top-1.5 rounded-full bg-red-500 p-0.5 text-white shadow-sm hover:bg-red-600"
                      >
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

        {/* Section: สรุป */}
        <section className="rounded-lg border border-gray-200 bg-white p-5 shadow-sm">
          <h2 className="mb-4 text-base font-semibold text-gray-800">สรุปและคำแนะนำ</h2>
          <div className="space-y-4">
            <Field label="คำแนะนำ">
              <textarea {...register('recommendation')} disabled={isReadOnly} rows={3}
                placeholder="คำแนะนำสำหรับเกษตรกร" className={inputCls} />
            </Field>
            <Field label="หมายเหตุ">
              <textarea {...register('notes')} disabled={isReadOnly} rows={2}
                placeholder="หมายเหตุเพิ่มเติม" className={inputCls} />
            </Field>
          </div>
        </section>

        {!isReadOnly && (
          <div className="flex justify-end gap-3">
            <button
              type="button"
              onClick={() => navigate('/farmlog/records')}
              className="rounded-md border border-gray-300 px-4 py-2 text-sm font-medium text-gray-700 hover:bg-gray-50"
            >
              ยกเลิก
            </button>
            <button
              type="submit"
              disabled={isSubmitting}
              className="inline-flex items-center gap-1.5 rounded-md bg-green-600 px-4 py-2 text-sm font-semibold text-white hover:bg-green-700 disabled:opacity-50"
            >
              <Save className="h-4 w-4" />
              {isNew ? 'บันทึก' : 'บันทึกการแก้ไข'}
            </button>
          </div>
        )}
      </form>
    </div>
  );
}
