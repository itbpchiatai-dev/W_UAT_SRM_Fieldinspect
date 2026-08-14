/**
 * RecordPreview — One Page Preview (print-friendly) for a single record.
 * Route: /farmlog/records/:id/preview  (Step 12.5: yield/list-driven)
 */
import { useParams, Link } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { ArrowLeft, Printer, Navigation, Camera } from 'lucide-react';
import { getRecord, type RecordDetail } from '../../api/records';
import { AuthenticatedPhoto } from '../../components/farmlog/AuthenticatedPhoto';
import {
  formattedPhoneSnapshot, hasPhoneAttribution, inspectorTypeLabel, phoneTypeLabel, submittedByLine,
} from '../../lib/inspection-attribution';
import { getScoreDisplayItems } from '../../lib/inspection-protocol-snapshot';
import { describeCycleStatus, recordCycleDisplayName } from '../../lib/plot-cycle';
import { formatYieldQuantity, YIELD_WARNING_PCT } from '../../lib/yield-planning';
import { toNumberOrNull } from '../../lib/numeric';

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

/** A condition score (1-10) — flagged orange when low (<= 3). */
function ScoreValue({ value }: { value: number | null }) {
  if (value == null) return null;
  const tone = value <= 3 ? 'bg-orange-50 text-orange-700' : 'bg-green-50 text-green-700';
  return (
    <span className={`inline-flex items-center rounded-full px-2.5 py-1 text-xs font-medium ${tone}`}>
      {value} / 10
    </span>
  );
}

/** The bar's WIDTH is capped at the container (100%) purely as a visual
 * device — round 8-8C confirms the displayed NUMBER is never capped/clamped,
 * only the bar's fill width, e.g. a real 510% still renders the text "510%"
 * with a full-width bar. Past YIELD_WARNING_PCT (150, non-blocking — see
 * yield-planning.ts) shows an informational amber note, never red/error. */
function YieldBar({ pct }: { pct: number }) {
  const width = Math.min(100, (pct / 150) * 100);
  const tone = pct >= 100 ? 'bg-green-500' : pct >= 70 ? 'bg-yellow-500' : 'bg-red-500';
  return (
    <div>
      <div className="flex items-center gap-3">
        <div className="h-2.5 w-48 max-w-full overflow-hidden rounded-full bg-gray-200">
          <div data-testid="yield-bar-fill" className={`h-full ${tone}`} style={{ width: `${width}%` }} />
        </div>
        <span className="text-base font-bold text-gray-900">{pct}%</span>
      </div>
      {pct > YIELD_WARNING_PCT && (
        <p className="mt-1 text-xs text-amber-700">ผลผลิตสูงกว่า 150% ของเป้าหมาย — ข้อมูลจริง ไม่ใช่ข้อผิดพลาด</p>
      )}
    </div>
  );
}

/** Yield section (round 8-8C) — a kg-first record (yieldQuantityKg present)
 * shows quantity + the frozen target snapshot + percent; a legacy record
 * (yieldPct only, no kg) falls back to the original percent-only display.
 * Quantity present but no comparable target (round 8-8A's derive_yield can
 * return this — e.g. the cycle's unit wasn't a weight unit) shows the
 * quantity plus an explicit "no target" note rather than a silent gap.
 * Renders nothing when there's no yield data at all (both null). */
function YieldSection({ r }: { r: RecordDetail }) {
  const yieldPct = r.yieldPct != null ? parseFloat(r.yieldPct) : null;
  const quantityKg = toNumberOrNull(r.yieldQuantityKg);
  const targetKg = toNumberOrNull(r.yieldTargetKgSnapshot);

  if (quantityKg == null && yieldPct == null) return null;

  return (
    <Section title="ผลผลิต (Yield)">
      <dl className="divide-y divide-gray-100">
        {quantityKg != null && (
          <>
            <Field label="ปริมาณผลผลิตที่ประเมินได้" value={formatYieldQuantity(quantityKg, 'kg')} />
            {targetKg != null ? (
              <Field label="เป้าหมายที่ใช้คำนวณ" value={formatYieldQuantity(targetKg, 'kg')} />
            ) : (
              <Field
                label="เป้าหมายที่ใช้คำนวณ"
                value={<span className="text-xs italic text-gray-400">ไม่มีเป้าหมายสำหรับคำนวณเปอร์เซ็นต์</span>}
              />
            )}
          </>
        )}
        {yieldPct != null && (
          <Field
            label={quantityKg != null ? 'เปอร์เซ็นต์เทียบเป้าหมาย' : '% คาดว่าจะได้ผลผลิต'}
            value={<YieldBar pct={yieldPct} />}
          />
        )}
      </dl>
    </Section>
  );
}

/** Planting-cycle block (round 7.4) — the record's OWN cycle (bound at
 * create time via record.plot_cycle), NOT the plot's current active cycle: a
 * record made in an earlier/closed cycle keeps showing that cycle here even
 * after the plot moves on to a newer one. Falls back gracefully when the
 * record carries only a cycle number, or no cycle at all. */
function CycleSection({ r }: { r: RecordDetail }) {
  const hasCycle = r.plotCycleId != null || r.cycleNo != null;
  if (!hasCycle) {
    return (
      <Section title="รอบปลูก">
        <p className="text-sm italic text-gray-400">ไม่พบข้อมูลรอบปลูก</p>
      </Section>
    );
  }
  const hasDetail = !!(
    r.cycleCrop || r.cycleVariety || r.cycleLotNo || r.cyclePlantingDate ||
    r.cyclePlantCount != null || toNumberOrNull(r.cycleExpectedYieldFull) != null
  );
  return (
    <Section title="รอบปลูก">
      <div className="mb-2 flex items-center gap-2">
        <span className="text-sm font-semibold text-gray-800">
          {recordCycleDisplayName(r)}
        </span>
        {r.cycleStatus && (
          <span className="rounded-full bg-gray-100 px-2 py-0.5 text-xs font-medium text-gray-500">
            {describeCycleStatus(r.cycleStatus)}
          </span>
        )}
      </div>
      {hasDetail ? (
        <dl className="divide-y divide-gray-100">
          <Field label="ชนิดพืช (รอบปลูก)" value={r.cycleCrop} />
          <Field label="พันธุ์/สายพันธุ์ (รอบปลูก)" value={r.cycleVariety} />
          <Field label="เลขล็อต (Lot No.)" value={r.cycleLotNo} />
          <Field label="วันที่ปลูก (รอบปลูก)" value={r.cyclePlantingDate} />
          <Field
            label="จำนวนต้น/จำนวนปลูก"
            value={r.cyclePlantCount != null ? r.cyclePlantCount.toLocaleString('th-TH') : null}
          />
          <Field
            label="Expected Yield ที่ 100%"
            value={formatYieldQuantity(r.cycleExpectedYieldFull, r.cycleExpectedYieldUnit)}
          />
        </dl>
      ) : (
        <p className="text-sm italic text-gray-400">มีเฉพาะหมายเลขรอบปลูก</p>
      )}
    </Section>
  );
}

function PreviewContent({ r }: { r: RecordDetail }) {
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
          {submittedByLine(r) && (
            <p className="mt-0.5 text-xs text-gray-400">
              ผู้กรอกข้อมูลหน้างาน {submittedByLine(r)}
            </p>
          )}
          {r.submittedIp && (
            <p className="mt-0.5 text-xs text-gray-400">IP ผู้บันทึก: <span className="font-mono">{r.submittedIp}</span></p>
          )}
          <p className="mt-0.5 text-xs text-gray-400">
            สถานะ: {r.isActive ? <span className="text-green-600">ใช้งาน</span> : <span className="text-gray-400">ปิด</span>}
          </p>
        </div>
      </div>

      <CycleSection r={r} />

      <Section title="ข้อมูลการเข้าตรวจ">
        {hasPhoneAttribution(r) ? (
          <dl className="divide-y divide-gray-100">
            <Field
              label="เบอร์ที่ใช้เข้าตรวจ"
              value={
                r.submittedPhoneSnapshot ? (
                  <span>
                    {formattedPhoneSnapshot(r.submittedPhoneSnapshot)}
                    {phoneTypeLabel(r.submittedPhoneType) && (
                      <span className="ml-1.5 text-xs text-gray-400">
                        ({phoneTypeLabel(r.submittedPhoneType)})
                      </span>
                    )}
                  </span>
                ) : null
              }
            />
            <Field label="เข้าตรวจในฐานะ" value={inspectorTypeLabel(r.inspectorType)} />
          </dl>
        ) : (
          <p className="text-sm italic text-gray-400">ผู้ใช้ในระบบ / ข้อมูลเดิม</p>
        )}
      </Section>

      <YieldSection r={r} />

      <Section title="ข้อมูลพื้นฐาน">
        <dl className="divide-y divide-gray-100">
          <Field label="ชนิดพืช" value={r.crop} />
          <Field label="พันธุ์/สายพันธุ์" value={r.variety} />
          <Field label="ระยะการเจริญเติบโต" value={r.growthStage} />
          <Field label="วันที่ปลูก" value={r.plantingDate} />
          <Field label="สภาพอากาศ" value={r.weatherCondition} />
        </dl>
      </Section>

      <Section title="การประเมินสภาพแปลง (คะแนน 1–10)">
        <dl className="divide-y divide-gray-100">
          {/* Labels come from the record's frozen protocol snapshot (round
              5.3) so a historical record shows the criteria it was scored
              under; records with no snapshot fall back to the original set. */}
          {getScoreDisplayItems(r).map((item) => (
            <Field key={item.slot} label={item.label} value={<ScoreValue value={item.score} />} />
          ))}
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
                  <AuthenticatedPhoto key={i} recordId={r.id} photoUrl={url} alt={`ภาพ ${i + 1}`} className="h-24 w-24 rounded-md object-cover shadow-sm" />
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
          <Link to="/farmlog/records" className="inline-flex items-center gap-1.5 text-sm text-gray-500 hover:text-gray-700">
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
