/**
 * RecordPreview — One Page Preview (print-friendly) for a single record.
 * Route: /farmlog/records/:id/preview  (Step 12.5: yield/list-driven)
 */
import { useParams, Link } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { ArrowLeft, Printer, Navigation, Camera } from 'lucide-react';
import { getRecord, type RecordDetail } from '../../api/records';

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="mb-6">
      <h2 className="mb-3 border-b pb-1 text-sm font-semibold uppercase tracking-wider text-gray-500">
        {title}
      </h2>
      {children}
    </section>
  );
}

function Field({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div className="flex flex-col gap-0.5 py-1.5 sm:flex-row sm:gap-4">
      <dt className="w-44 shrink-0 text-xs font-medium text-gray-500">{label}</dt>
      <dd className="text-sm text-gray-900">{value ?? <span className="text-gray-400 italic">—</span>}</dd>
    </div>
  );
}

/** "ไม่พบ"/empty render muted; any real severity gets an alert chip. */
function StatusValue({ value }: { value: string | null }) {
  if (!value) return null;
  if (value === 'ไม่พบ') return <span className="text-green-700">ไม่พบ</span>;
  return (
    <span className="inline-flex items-center rounded-full bg-orange-50 px-2.5 py-1 text-xs font-medium text-orange-700">
      {value}
    </span>
  );
}

function YieldBar({ pct }: { pct: number }) {
  const width = Math.min(100, (pct / 150) * 100);
  const tone = pct >= 100 ? 'bg-green-500' : pct >= 70 ? 'bg-yellow-500' : 'bg-red-500';
  return (
    <div className="flex items-center gap-3">
      <div className="h-2.5 w-48 overflow-hidden rounded-full bg-gray-200">
        <div className={`h-full ${tone}`} style={{ width: `${width}%` }} />
      </div>
      <span className="text-base font-bold text-gray-900">{pct}%</span>
    </div>
  );
}

function PreviewContent({ r }: { r: RecordDetail }) {
  const yieldPct = r.yieldPct != null ? parseFloat(r.yieldPct) : null;
  return (
    <div className="space-y-0">
      <div className="mb-6 flex items-start justify-between gap-4 border-b pb-4">
        <div>
          <h1 className="text-xl font-bold text-gray-900">บันทึกการตรวจแปลง</h1>
          <p className="mt-1 text-sm text-gray-500">
            {r.plotCode && <span className="font-medium text-gray-700">{r.plotCode}</span>}
            {r.plotName && r.plotName !== r.plotCode && ` — ${r.plotName}`}
            {r.supplierName && <span className="ml-2 text-gray-400">({r.supplierName})</span>}
          </p>
        </div>
        <div className="text-right">
          <p className="text-lg font-semibold">{r.recordDate}</p>
          <p className="mt-0.5 text-xs text-gray-400">บันทึกโดย {r.recordedByName || r.recordedByEmail || '—'}</p>
          <p className="mt-0.5 text-xs text-gray-400">
            สถานะ: {r.isActive ? <span className="text-green-600">ใช้งาน</span> : <span className="text-gray-400">ปิด</span>}
          </p>
        </div>
      </div>

      {yieldPct != null && (
        <Section title="ผลผลิต (Yield)">
          <Field label="% คาดว่าจะได้ผลผลิต" value={<YieldBar pct={yieldPct} />} />
        </Section>
      )}

      <Section title="ข้อมูลพื้นฐาน">
        <dl className="divide-y divide-gray-100">
          <Field label="ชนิดพืช" value={r.crop} />
          <Field label="พันธุ์/สายพันธุ์" value={r.variety} />
          <Field label="ระยะการเจริญเติบโต" value={r.growthStage} />
          <Field label="วันที่ปลูก" value={r.plantingDate} />
          <Field label="สภาพอากาศ" value={r.weatherCondition} />
        </dl>
      </Section>

      <Section title="การประเมินสภาพแปลง">
        <dl className="divide-y divide-gray-100">
          <Field label="การเตรียมแปลง" value={r.fieldPrepLevel} />
          <Field label="การดูแลรักษา" value={r.careLevel} />
          <Field label="แมลงศัตรูพืช" value={<StatusValue value={r.pestStatus} />} />
          <Field label="โรคพืช" value={<StatusValue value={r.diseaseStatus} />} />
          <Field label="วัชพืช" value={<StatusValue value={r.weedStatus} />} />
          <Field label="วิธีการให้น้ำ" value={r.irrigationMethod} />
          <Field label="ปุ๋ยที่ใช้" value={r.fertilizer} />
        </dl>
      </Section>

      {(r.recommendation || r.notes) && (
        <Section title="คำแนะนำและหมายเหตุ">
          <dl className="divide-y divide-gray-100">
            {r.recommendation && <Field label="คำแนะนำ" value={r.recommendation} />}
            {r.notes && <Field label="หมายเหตุ" value={r.notes} />}
          </dl>
        </Section>
      )}

      {(r.latitude != null || (r.photoUrls && r.photoUrls.length > 0)) && (
        <Section title="พิกัดและภาพถ่าย">
          <dl className="divide-y divide-gray-100">
            {r.latitude != null && r.longitude != null && (
              <Field label="พิกัด GPS" value={
                <span className="inline-flex items-center gap-1.5 font-mono text-xs">
                  <Navigation className="h-3 w-3 text-green-600" />
                  {parseFloat(r.latitude).toFixed(6)}, {parseFloat(r.longitude).toFixed(6)}
                </span>
              } />
            )}
          </dl>
          {r.photoUrls && r.photoUrls.length > 0 && (
            <div className="mt-2">
              <div className="flex items-center gap-1 mb-2 text-xs font-medium text-gray-500">
                <Camera className="h-3.5 w-3.5" /> ภาพถ่าย ({r.photoUrls.length})
              </div>
              <div className="flex flex-wrap gap-2">
                {r.photoUrls.map((url, i) => (
                  <img key={i} src={url} alt={`ภาพ ${i + 1}`} className="h-24 w-24 rounded-md object-cover shadow-sm" />
                ))}
              </div>
            </div>
          )}
        </Section>
      )}

      <p className="mt-4 text-right text-xs text-gray-300 print:block hidden">
        พิมพ์เมื่อ {new Date().toLocaleString('th-TH')}
      </p>
    </div>
  );
}

export function RecordPreview() {
  const { id } = useParams<{ id: string }>();

  const { data, isLoading, isError } = useQuery({
    queryKey: ['record', id],
    queryFn: () => getRecord(id!),
    enabled: !!id,
  });

  return (
    <div className="min-h-screen bg-gray-50 print:bg-white">
      <div className="print:hidden sticky top-0 z-10 border-b bg-white px-4 py-2 shadow-sm">
        <div className="container mx-auto flex items-center justify-between gap-4">
          <Link to={`/farmlog/records/${id}`} className="inline-flex items-center gap-1.5 text-sm text-gray-500 hover:text-gray-700">
            <ArrowLeft className="h-4 w-4" /> กลับ
          </Link>
          <button onClick={() => window.print()}
            className="inline-flex items-center gap-1.5 rounded-md bg-gray-800 px-4 py-1.5 text-sm font-medium text-white hover:bg-gray-700">
            <Printer className="h-4 w-4" /> พิมพ์
          </button>
        </div>
      </div>

      <div className="container mx-auto max-w-2xl px-4 py-8 print:max-w-none print:px-8 print:py-4">
        {isLoading && (
          <div className="space-y-4 animate-pulse">
            {Array.from({ length: 6 }).map((_, i) => <div key={i} className="h-4 rounded bg-gray-200" />)}
          </div>
        )}
        {isError && (
          <div className="rounded-md border border-red-200 bg-red-50 p-4 text-sm text-red-700">ไม่พบข้อมูลบันทึกนี้</div>
        )}
        {data && <PreviewContent r={data} />}
      </div>
    </div>
  );
}

export default RecordPreview;
