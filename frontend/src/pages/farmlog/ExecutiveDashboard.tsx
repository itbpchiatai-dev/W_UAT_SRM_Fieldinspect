/**
 * ExecutiveDashboard — company-wide overview (rebuilt, round 46).
 *
 * The first version of this page was eight stat tiles and a map, and it
 * read like a report: nothing moved, nothing ranked, nothing said where to
 * go next. This one answers the three questions a manager actually opens it
 * with, in the order they ask them:
 *
 *   1. how big is this and is it healthy      -> the summary row
 *   2. getting better or worse                -> the trend chart
 *   3. did we hit plan, and who do I chase    -> the funnel, the table,
 *                                                the follow-up list
 *
 * Four independent queries rather than one payload, deliberately: a failing
 * block renders its own message while the rest of the page keeps working,
 * which is the rule the v1 design wrote down and never implemented. The
 * range tabs refetch three of them; the follow-up list is not range-scoped
 * (a window would hide the plot nobody has visited in four months, which is
 * the one most worth finding) so it is never refetched by a tab click.
 */
import { useState } from 'react';
import { Link } from 'react-router-dom';
import { keepPreviousData, useMutation, useQuery } from '@tanstack/react-query';
import {
  AlertTriangle, Building2, CalendarClock, ClipboardCheck, Download, Gauge, Target,
} from 'lucide-react';
import {
  downloadExecutiveBreakdown, getExecutiveBreakdown, getExecutiveFollowUp,
  getExecutiveSummary, getExecutiveTrend, DEFAULT_EXECUTIVE_FILTERS,
  type BreakdownGroup, type BreakdownRow, type ExecutiveFilters,
  type ExecutiveSummary, type FollowUpRow,
} from '../../api/dashboard';
import { listSuppliers } from '../../api/suppliers';
import { listMasterData, masterDataQueryKey } from '../../api/masterdata';
import { SearchableSelect } from '../../components/farmlog/SearchableSelect';
import { KpiCard, SkeletonCard } from '../../components/dashboard/KpiCard';
import { PeriodFilter } from '../../components/dashboard/PeriodFilter';
import { BarLineChart, type BarLinePoint } from '../../components/charts/BarLineChart';
import { ProgressRow } from '../../components/charts/ProgressRow';

const DASH = '—';
const FOLLOW_UP_ANCHOR = 'exec-follow-up';

/** Labels that say WHICH clock a number is on. A dashboard that mixes
 *  "right now" with "inside the selected window" and labels neither starts
 *  lying the moment somebody touches the range tabs. */
const NOW = 'ณ ตอนนี้';
const IN_RANGE = 'ช่วงที่เลือก';

const THAI_MONTHS = [
  'ม.ค.', 'ก.พ.', 'มี.ค.', 'เม.ย.', 'พ.ค.', 'มิ.ย.',
  'ก.ค.', 'ส.ค.', 'ก.ย.', 'ต.ค.', 'พ.ย.', 'ธ.ค.',
];

/** ISO bucket start -> axis label. Month buckets show the month; week
 *  buckets need the day too or four of them read as the same label. */
export function periodLabel(iso: string, granularity: string): string {
  const d = new Date(`${iso}T00:00:00`);
  if (Number.isNaN(d.getTime())) return iso;
  const month = THAI_MONTHS[d.getMonth()] ?? '';
  return granularity === 'month' ? month : `${d.getDate()} ${month}`;
}

/** Decimal-as-string from the API -> a kg figure a person reads.
 *  null (not 0) when absent, so callers can show a dash. */
export function formatKg(value: string | null): string | null {
  if (value === null || value === '') return null;
  const n = Number(value);
  if (!Number.isFinite(n)) return null;
  return `${Math.round(n).toLocaleString()} kg`;
}

export function percentOf(part: number, whole: number): number | null {
  return whole <= 0 ? null : Math.round((part / whole) * 100);
}

/** Green at or above plan, orange when slipping, red when far off. A dash
 *  has no tone of its own — blue, so "no plan recorded" never reads as a
 *  bad result. */
export function achievementTone(pct: number | null) {
  if (pct === null) return 'blue' as const;
  if (pct >= 95) return 'green' as const;
  if (pct >= 75) return 'orange' as const;
  return 'red' as const;
}

const REASON_TEXT: Record<FollowUpRow['reason'], string> = {
  low_score: 'คะแนนต่ำ',
  overdue: 'ค้างตรวจ',
  low_yield: 'ผลผลิตต่ำกว่าเป้า',
};

const REASON_CLASS: Record<FollowUpRow['reason'], string> = {
  low_score: 'bg-red-50 text-red-700',
  overdue: 'bg-red-50 text-red-700',
  low_yield: 'bg-orange-50 text-orange-700',
};

/** The value column means a different thing per reason, so it is formatted
 *  with the reason rather than printed bare. */
export function reasonValue(row: FollowUpRow): string {
  if (row.reason === 'overdue') return `${Math.round(row.value)} วัน`;
  if (row.reason === 'low_score') return `${row.value} / 10`;
  return `${row.value}%`;
}

function BlockError({ what }: { what: string }) {
  return (
    <div className="rounded-lg border border-destructive/30 bg-destructive/10 px-4 py-6 text-center text-sm text-destructive">
      ไม่สามารถโหลด{what} — โปรดลองอีกครั้ง
    </div>
  );
}

function BlockEmpty({ text }: { text: string }) {
  return (
    <div className="rounded-lg border border-dashed bg-card px-4 py-10 text-center text-sm text-muted-foreground">
      {text}
    </div>
  );
}

// --- row 1 ---------------------------------------------------------------

function SummaryRow({ data }: { data: ExecutiveSummary }) {
  const coverage = percentOf(data.plotsInspected, data.plotsTotal);
  return (
    <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-5">
      <KpiCard
        label="ขอบเขต"
        value={`${data.supplierTotal.toLocaleString()} ราย`}
        hint={`${data.plotsTotal.toLocaleString()} แปลง · ${NOW}`}
        Icon={Building2}
        tone="blue"
        to="/farmlog/admin/suppliers"
      />
      <KpiCard
        label="แปลงที่เคยถูกตรวจ"
        value={coverage === null ? DASH : `${coverage}%`}
        hint={
          coverage === null
            ? 'ยังไม่มีแปลงในระบบ'
            : `${data.plotsInspected.toLocaleString()}/${data.plotsTotal.toLocaleString()} · ยังไม่เคยตรวจ ${data.plotsNeverInspected.toLocaleString()} · ${NOW}`
        }
        Icon={ClipboardCheck}
        tone="primary"
        to="/farmlog/admin/plots"
      />
      <KpiCard
        label="อัตราบรรลุเป้า"
        value={data.achievementPct === null ? DASH : `${data.achievementPct.toFixed(1)}%`}
        hint={
          data.achievementPct === null
            ? 'ยังไม่มีเป้าผลิตบันทึกไว้'
            : `ผลผลิตที่เก็บได้ ÷ เป้าผลิต · ${IN_RANGE}`
        }
        Icon={Target}
        tone={achievementTone(data.achievementPct)}
      />
      <KpiCard
        label="คะแนนตรวจเฉลี่ย"
        value={data.avgConditionScore === null ? DASH : `${data.avgConditionScore.toFixed(1)} / 10`}
        hint={
          data.avgConditionScore === null
            ? 'ยังไม่มีบันทึกที่ให้คะแนน'
            : `จาก ${data.scoredRecordCount.toLocaleString()} บันทึก · ${IN_RANGE}`
        }
        Icon={Gauge}
        tone="blue"
      />
      {/* The three reasons OVERLAP — one plot can be overdue and low-scoring
          at once — so they are listed, never summed. */}
      <a href={`#${FOLLOW_UP_ANCHOR}`} className="block">
        <KpiCard
          label="แปลงที่ต้องจัดการ"
          value={data.actionablePlots.toLocaleString()}
          hint={`ค้าง ${data.actionableOverdue} · คะแนนต่ำ ${data.actionableLowScore} · ผลผลิตต่ำ ${data.actionableLowYield} · ${NOW}`}
          Icon={data.actionablePlots > 0 ? AlertTriangle : CalendarClock}
          tone={data.actionablePlots > 0 ? 'orange' : 'green'}
        />
      </a>
    </div>
  );
}

// --- row 3 ---------------------------------------------------------------

function YieldFunnel({ data }: { data: ExecutiveSummary }) {
  const target = Number(data.targetKgTotal ?? 0);
  const harvest = Number(data.harvestKgTotal ?? 0);
  const cleaned = Number(data.cleanedKgTotal ?? 0);
  const scale = (v: number) => (target > 0 ? (v / target) * 100 : null);

  const targetText = formatKg(data.targetKgTotal);
  const harvestText = formatKg(data.harvestKgTotal);
  const cleanedText = formatKg(data.cleanedKgTotal);

  if (targetText === null && harvestText === null) {
    return <BlockEmpty text="ยังไม่มีข้อมูลผลผลิตในช่วงที่เลือก" />;
  }
  return (
    <div className="rounded-lg border bg-card p-4 shadow-sm">
      {/* All three bars share ONE scale (against the target) while each
          reports a percentage of its own base — see ProgressRow for why
          those are separate props. */}
      <ProgressRow
        label="เป้าผลิต" value={targetText ?? DASH}
        barPct={target > 0 ? 100 : null} tone="blue"
      />
      <ProgressRow
        label="ผลผลิตที่เก็บได้" value={harvestText ?? DASH}
        barPct={scale(harvest)}
        note={data.achievementPct === null ? undefined : `${data.achievementPct.toFixed(1)}% ของเป้า`}
        tone={achievementTone(data.achievementPct)}
      />
      <ProgressRow
        label="ผลผลิตหลังทำความสะอาด" value={cleanedText ?? DASH}
        barPct={scale(cleaned)}
        note={
          data.cleanedRetentionPct === null
            ? undefined
            : `${data.cleanedRetentionPct.toFixed(1)}% ของที่เก็บได้`
        }
        hint={
          data.cleanedRecordCount === 0
            ? `ยังไม่มีใครกรอก (${data.cleanedMissingCount.toLocaleString()} บันทึก)`
            : `จาก ${data.cleanedRecordCount.toLocaleString()} บันทึก · ยังไม่กรอกอีก ${data.cleanedMissingCount.toLocaleString()}`
        }
        tone="purple"
      />
    </div>
  );
}

// --- row 4 ---------------------------------------------------------------

const GROUP_TABS: { value: BreakdownGroup; label: string }[] = [
  { value: 'supplier', label: 'Supplier' },
  { value: 'crop', label: 'พืช' },
  { value: 'variety', label: 'พันธุ์' },
  { value: 'p_code', label: 'P.Code' },
];

function BreakdownTable(
  { rows, groupBy }: { rows: BreakdownRow[]; groupBy: string },
) {
  if (rows.length === 0) return <BlockEmpty text="ไม่มีข้อมูลสำหรับการเทียบนี้" />;
  // `groupBy` here is the server's answer, never the request. If the two
  // ever disagree the header tells the truth about what these rows are.
  const head = GROUP_TABS.find((g) => g.value === groupBy)?.label ?? groupBy;
  return (
    <div className="overflow-x-auto rounded-lg border bg-card shadow-sm">
      <table aria-label="เทียบผลงาน" className="min-w-full divide-y divide-border">
        <thead className="bg-muted/40">
          <tr>
            {[head, 'แปลง', 'ตรวจแล้ว', 'ค้าง', 'คะแนน', 'บรรลุเป้า'].map((h) => (
              <th key={h} className="px-4 py-2.5 text-left text-xs font-medium uppercase tracking-wider text-muted-foreground">
                {h}
              </th>
            ))}
          </tr>
        </thead>
        <tbody className="divide-y divide-border/60">
          {rows.map((r) => (
            <tr key={r.key} className="hover:bg-muted/30">
              <td className="whitespace-nowrap px-4 py-2.5 text-sm font-medium">{r.label}</td>
              <td className="px-4 py-2.5 text-sm tabular-nums">{r.plots.toLocaleString()}</td>
              <td className="px-4 py-2.5 text-sm tabular-nums">{r.plotsInspected.toLocaleString()}</td>
              <td className="px-4 py-2.5 text-sm tabular-nums">{r.plotsOverdue.toLocaleString()}</td>
              <td className="px-4 py-2.5 text-sm tabular-nums">
                {r.avgScore === null ? DASH : r.avgScore.toFixed(1)}
              </td>
              <td className="min-w-[200px] px-4 py-2.5">
                <ProgressRow
                  label={`บรรลุเป้า ${r.label}`}
                  value={r.achievementPct === null ? DASH : `${r.achievementPct.toFixed(1)}%`}
                  barPct={r.achievementPct}
                  tone={achievementTone(r.achievementPct)}
                />
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      <p className="border-t px-4 py-2 text-xs text-muted-foreground">
        เรียงจากบรรลุเป้าน้อยไปมาก — หน้านี้มีไว้หาจุดที่ต้องเข้าไปดู
      </p>
    </div>
  );
}

// --- row 5 ---------------------------------------------------------------

function FollowUpTable({ rows, total }: { rows: FollowUpRow[]; total: number }) {
  if (rows.length === 0) {
    return <BlockEmpty text="ไม่มีแปลงที่ต้องตามงาน — ทุกแปลงได้รับการตรวจตามเกณฑ์" />;
  }
  return (
    <div className="overflow-x-auto rounded-lg border bg-card shadow-sm">
      <table aria-label="แปลงที่ต้องตามงาน" className="min-w-full divide-y divide-border">
        <thead className="bg-muted/40">
          <tr>
            {['แปลง', 'Supplier', 'เหตุผล', 'ค่า', 'ตรวจล่าสุด'].map((h) => (
              <th key={h} className="px-4 py-2.5 text-left text-xs font-medium uppercase tracking-wider text-muted-foreground">
                {h}
              </th>
            ))}
          </tr>
        </thead>
        <tbody className="divide-y divide-border/60">
          {rows.map((r) => (
            <tr key={r.plotId} className="hover:bg-muted/30">
              <td className="whitespace-nowrap px-4 py-2.5 text-sm font-medium">
                <Link to={`/farmlog/admin/plots/${r.plotId}`} className="hover:underline">
                  {r.plotCode}
                </Link>
                <span className="ml-2 text-xs text-muted-foreground">{r.plotName}</span>
              </td>
              <td className="px-4 py-2.5 text-sm">{r.supplierCode ?? DASH}</td>
              <td className="px-4 py-2.5 text-sm">
                <span className={`rounded px-2 py-0.5 text-xs font-medium ${REASON_CLASS[r.reason]}`}>
                  {REASON_TEXT[r.reason]}
                </span>
              </td>
              <td className="px-4 py-2.5 text-sm tabular-nums">{reasonValue(r)}</td>
              <td className="px-4 py-2.5 text-sm text-muted-foreground">
                {r.lastInspected ?? 'ยังไม่เคยตรวจ'}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      <p className="border-t px-4 py-2 text-xs text-muted-foreground">
        แสดง {rows.length} จาก {total.toLocaleString()} แปลง ·
        {' '}แปลงที่มีหมายเหตุและผู้รับผิดชอบจะเพิ่มเข้ามาได้เมื่อเริ่มมีการกรอก
      </p>
    </div>
  );
}

// --- the page ------------------------------------------------------------

export function ExecutiveDashboard() {
  const [filters, setFilters] = useState<ExecutiveFilters>(DEFAULT_EXECUTIVE_FILTERS);
  const [groupBy, setGroupBy] = useState<BreakdownGroup>('supplier');
  const { range, dateFrom, dateTo, supplierId, pCode } = filters;

  const suppliers = useQuery({
    queryKey: ['suppliers', 'active-for-executive'],
    queryFn: () => listSuppliers({ activeOnly: true, limit: 500 }),
    staleTime: 300_000,
  });
  const pCodes = useQuery({
    queryKey: masterDataQueryKey('p_code', null, true),
    queryFn: () => listMasterData({ type: 'p_code', activeOnly: true }),
    staleTime: 300_000,
  });

  const summary = useQuery({
    queryKey: ['executive-summary', range, dateFrom, dateTo, supplierId, pCode],
    queryFn: () => getExecutiveSummary(filters),
    staleTime: 60_000,
    placeholderData: keepPreviousData,
  });
  const trend = useQuery({
    queryKey: ['executive-trend', range, dateFrom, dateTo, supplierId, pCode],
    queryFn: () => getExecutiveTrend(filters),
    staleTime: 60_000,
    placeholderData: keepPreviousData,
  });
  const breakdown = useQuery({
    queryKey: ['executive-breakdown', range, dateFrom, dateTo, supplierId, pCode, groupBy],
    queryFn: () => getExecutiveBreakdown(filters, groupBy),
    staleTime: 60_000,
    placeholderData: keepPreviousData,
  });
  // No period in the key: this list is about today, not about the window.
  // The supplier IS in it — picking one changes what "this" is.
  const followUp = useQuery({
    queryKey: ['executive-follow-up', supplierId, pCode],
    queryFn: () => getExecutiveFollowUp(filters, 10),
    staleTime: 60_000,
    placeholderData: keepPreviousData,
  });

  const exportM = useMutation({
    mutationFn: () => downloadExecutiveBreakdown(filters, groupBy),
    onSuccess: (blob) => {
      const url = URL.createObjectURL(blob);
      const link = document.createElement('a');
      link.href = url;
      link.download = `executive-breakdown-${groupBy}.xlsx`;
      document.body.appendChild(link);
      link.click();
      link.remove();
      window.setTimeout(() => URL.revokeObjectURL(url), 1000);
    },
  });

  const trendPoints: BarLinePoint[] = (trend.data?.points ?? []).map((p) => ({
    label: periodLabel(p.period, trend.data!.granularity),
    bar: p.inspections,
    line: p.avgScore,
  }));

  return (
    <div className="container mx-auto px-4 py-8 sm:px-6 lg:px-8">
      <header className="mb-6 flex flex-wrap items-start justify-between gap-3">
        <div>
          <h1 className="text-xl font-bold">ภาพรวมผู้บริหาร</h1>
          <p className="mt-1 text-sm text-muted-foreground">
            ภาพรวมทุก Supplier — การใช้งานระบบ ผลผลิตเทียบเป้า และแปลงที่ต้องตามงาน
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          {/* SearchableSelect, not a <select>: UAT carries ~500 P.Codes and
              a plain dropdown cannot be typed into. Same control the Plots
              filter bar already uses. */}
          <SearchableSelect
            label="กรอง Supplier"
            placeholder="ทุก Supplier"
            clearLabel="— ทุก Supplier —"
            options={(suppliers.data ?? []).map((s) => ({
              value: s.id, label: `${s.code} — ${s.name}`,
            }))}
            value={supplierId}
            onChange={(v) => setFilters((f) => ({ ...f, supplierId: v }))}
          />
          <SearchableSelect
            label="กรอง P.Code"
            placeholder="ทุก P.Code"
            clearLabel="— ทุก P.Code —"
            options={(pCodes.data ?? []).map((m) => ({
              value: m.value,
              label: m.parent ? `${m.value} · ${m.parent}` : m.value,
            }))}
            value={pCode}
            onChange={(v) => setFilters((f) => ({ ...f, pCode: v }))}
          />
          <PeriodFilter
            value={{ range, dateFrom, dateTo }}
            onChange={(p) => setFilters((f) => ({ ...f, ...p }))}
          />
        </div>
      </header>

      <section>
        {summary.isLoading && (
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-5">
            {Array.from({ length: 5 }).map((_, i) => <SkeletonCard key={i} />)}
          </div>
        )}
        {summary.isError && <BlockError what="ตัวเลขสรุป" />}
        {summary.data && <SummaryRow data={summary.data} />}
      </section>

      <section className="mt-6">
        <h2 className="mb-3 text-sm font-semibold text-muted-foreground">
          แนวโน้มการตรวจและคะแนน <span className="font-normal">({IN_RANGE})</span>
        </h2>
        {trend.isError ? (
          <BlockError what="กราฟแนวโน้ม" />
        ) : trend.isLoading ? (
          <SkeletonCard />
        ) : (
          <BarLineChart
            points={trendPoints}
            barLabel="จำนวนการตรวจ"
            lineLabel="คะแนนเฉลี่ย"
            emptyMessage="ข้อมูลยังไม่พอวาดแนวโน้ม — กราฟจะขึ้นเองเมื่อมีข้อมูลหลายช่วงเวลา"
          />
        )}
      </section>

      <section className="mt-6">
        <h2 className="mb-3 text-sm font-semibold text-muted-foreground">
          ผลผลิตเทียบเป้า <span className="font-normal">({IN_RANGE})</span>
        </h2>
        {summary.isError ? (
          <BlockError what="ผลผลิต" />
        ) : summary.isLoading ? (
          <SkeletonCard />
        ) : summary.data ? (
          <YieldFunnel data={summary.data} />
        ) : null}
      </section>

      <section className="mt-6">
        <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
          <h2 className="text-sm font-semibold text-muted-foreground">
            เทียบผลงาน <span className="font-normal">({IN_RANGE})</span>
          </h2>
          <div className="flex flex-wrap items-center gap-2">
            <div role="group" aria-label="เทียบตาม" className="inline-flex rounded-md border bg-card p-0.5">
            {GROUP_TABS.map((g) => (
              <button
                key={g.value}
                type="button"
                aria-pressed={g.value === groupBy}
                onClick={() => setGroupBy(g.value)}
                className={
                  'rounded px-3 py-1.5 text-sm transition-colors ' +
                  (g.value === groupBy
                    ? 'bg-primary font-semibold text-primary-foreground'
                    : 'text-muted-foreground hover:bg-muted')
                }
              >
                {g.label}
              </button>
            ))}
            </div>
            <button
              type="button"
              onClick={() => exportM.mutate()}
              disabled={exportM.isPending || !breakdown.data?.rows.length}
              className="inline-flex items-center gap-1.5 rounded-md border bg-card px-3 py-1.5 text-sm font-medium hover:bg-muted disabled:cursor-not-allowed disabled:opacity-50"
            >
              <Download className="h-4 w-4" />
              {exportM.isPending ? 'กำลังสร้างไฟล์…' : 'Export Excel'}
            </button>
          </div>
        </div>
        {exportM.isError && (
          <p className="mb-2 text-sm text-destructive">ดาวน์โหลดไม่สำเร็จ — โปรดลองอีกครั้ง</p>
        )}
        {breakdown.isError ? (
          <BlockError what="ตารางเทียบผลงาน" />
        ) : breakdown.isLoading ? (
          <SkeletonCard />
        ) : breakdown.data ? (
          <>
            {breakdown.data.groupBy !== groupBy && (
              <p className="mb-2 rounded-md border border-orange-300 bg-orange-50 px-3 py-2 text-sm text-orange-800">
                เซิร์ฟเวอร์ยังไม่รองรับการเทียบตาม
                {' '}{GROUP_TABS.find((g) => g.value === groupBy)?.label ?? groupBy}
                {' '}— ตารางด้านล่างเทียบตาม
                {' '}{GROUP_TABS.find((g) => g.value === breakdown.data!.groupBy)?.label
                      ?? breakdown.data!.groupBy}
                {' '}แทน
              </p>
            )}
            <BreakdownTable rows={breakdown.data.rows} groupBy={breakdown.data.groupBy} />
          </>
        ) : null}
      </section>

      <section className="mt-6" id={FOLLOW_UP_ANCHOR}>
        <h2 className="mb-3 text-sm font-semibold text-muted-foreground">
          แปลงที่ต้องตามงาน <span className="font-normal">({NOW})</span>
        </h2>
        {followUp.isError ? (
          <BlockError what="รายการแปลงที่ต้องตามงาน" />
        ) : followUp.isLoading ? (
          <SkeletonCard />
        ) : followUp.data ? (
          <FollowUpTable rows={followUp.data.rows} total={followUp.data.total} />
        ) : null}
      </section>
    </div>
  );
}

export default ExecutiveDashboard;
