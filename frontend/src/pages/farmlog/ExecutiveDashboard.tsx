/**
 * ExecutiveDashboard — company-wide overview (rounds 50-53).
 *
 * Reads top to bottom as the questions a manager opens it with:
 *
 *   how big is this, and is it healthy   -> summary row
 *   better or worse than before          -> trend
 *   where does production actually stand -> the two lenses
 *   what do I act on                     -> concerns, comparison, worklist
 *
 * The idea the page is built around: production is TWO things, not an
 * average of them. A plot still growing is judged on its yield ESTIMATE; a
 * plot whose last record carries ผลผลิตหลังทำความสะอาด is judged on that
 * weight, which is what was actually produced. Averaging the two gave a
 * single number describing neither — and summing every record on top of
 * that counted each plot's target once per visit, inflating the kg totals
 * 2.5x and moving the percentage by 16 points.
 *
 * Cycle status is deliberately a FILTER and never the lens test: on this
 * repo's own data the two definitions of "finished" overlap in zero plots,
 * because closing a cycle is a staff action that may never happen.
 */
import { useEffect, useState } from 'react';
import type { ReactNode } from 'react';
import { Link } from 'react-router-dom';
import { keepPreviousData, useMutation, useQuery } from '@tanstack/react-query';
import {
  AlertTriangle, Building2, CalendarClock, ClipboardCheck, Download,
  Package, Sprout,
} from 'lucide-react';
import {
  downloadExecutiveBreakdown, getConcerns, getExecutiveBreakdown,
  getExecutiveFollowUp, getExecutiveSummary, getExecutiveTrend,
  getProductionStatus, DEFAULT_EXECUTIVE_FILTERS, DEFAULT_CONCERN_PCT,
  type BreakdownGroup, type BreakdownRow, type ConcernRow, type ExecutiveFilters,
  type ExecutiveSummary, type FollowUpRow, type LensFigures, type ProductionStatus,
} from '../../api/dashboard';
import { listSuppliers } from '../../api/suppliers';
import { listMasterData, masterDataQueryKey } from '../../api/masterdata';
import { KpiCard, SkeletonCard } from '../../components/dashboard/KpiCard';
import { PeriodFilter } from '../../components/dashboard/PeriodFilter';
import { FilterMenu } from '../../components/dashboard/FilterMenu';
import { BarLineChart, type BarLinePoint } from '../../components/charts/BarLineChart';
import { DumbbellChart, gapPoints, type DumbbellRow } from '../../components/charts/DumbbellChart';
import { ProgressRow } from '../../components/charts/ProgressRow';
import { StageBar } from '../../components/charts/StageBar';

const DASH = '—';
const CONCERNS_ANCHOR = 'exec-concerns';
const FOLLOW_UP_ANCHOR = 'exec-follow-up';

/** Labels that say WHICH clock a number is on. A page that mixes "right
 *  now" with "inside the selected window" and labels neither starts lying
 *  the moment somebody touches the period tabs. */
const NOW = 'ณ ตอนนี้';
const IN_RANGE = 'ช่วงที่เลือก';

const THAI_MONTHS = [
  'ม.ค.', 'ก.พ.', 'มี.ค.', 'เม.ย.', 'พ.ค.', 'มิ.ย.',
  'ก.ค.', 'ส.ค.', 'ก.ย.', 'ต.ค.', 'พ.ย.', 'ธ.ค.',
];

export function periodLabel(iso: string, granularity: string): string {
  const d = new Date(`${iso}T00:00:00`);
  if (Number.isNaN(d.getTime())) return iso;
  const month = THAI_MONTHS[d.getMonth()] ?? '';
  return granularity === 'month' ? month : `${d.getDate()} ${month}`;
}

/** Decimal-as-string from the API -> a kg figure a person reads. null (not
 *  0) when absent, so callers show a dash instead of a measurement. */
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
  never_inspected: 'ไม่เคยตรวจ',
  low_score: 'คะแนนต่ำ',
  overdue: 'ค้างตรวจ',
  low_yield: 'ผลผลิตต่ำกว่าเป้า',
};

const REASON_CLASS: Record<FollowUpRow['reason'], string> = {
  never_inspected: 'bg-red-100 text-red-800',
  low_score: 'bg-red-50 text-red-700',
  overdue: 'bg-red-50 text-red-700',
  low_yield: 'bg-orange-50 text-orange-700',
};

/** The value column means a different thing per reason, so it is formatted
 *  with the reason rather than printed bare. */
export function reasonValue(row: FollowUpRow): string {
  if (row.reason === 'never_inspected') return `${Math.round(row.value)} วันแล้ว`;
  if (row.reason === 'overdue') return `${Math.round(row.value)} วัน`;
  if (row.reason === 'low_score') return `${row.value} / 10`;
  return `${row.value}%`;
}

/** Concern rows carry data, not prose — the wording lives here so the list
 *  can be re-sorted or re-grouped without rewriting its sentences. */
export function concernText(row: ConcernRow, thresholdPct: number): string {
  const where = row.key;
  switch (row.kind) {
    case 'in_production_low':
      return `${where} — กำลังผลิต ${row.plots} แปลงต่ำกว่า ${thresholdPct}% ของเป้า (แย่สุด ${row.value}%)`;
    case 'finished_low':
      return `${where} — ผลิตจบแล้ว ${row.plots} แปลงได้ไม่ถึง ${thresholdPct}% ของเป้า (แย่สุด ${row.value}%)`;
    case 'missing_cleaned':
      return `${where} — ปิดรอบแล้ว ${row.plots} แปลง แต่ยังไม่กรอกผลผลิตหลังทำความสะอาด`;
    case 'low_retention':
      return `${where} — อัตราคงเหลือหลังคัดเพียง ${row.value}% ต่ำกว่าค่าเฉลี่ยของหน้านี้มาก`;
    default:
      return where;
  }
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

function SectionTitle({ children, clock }: { children: ReactNode; clock: string }) {
  return (
    <h2 className="mb-3 text-sm font-semibold text-muted-foreground">
      {children} <span className="font-normal">({clock})</span>
    </h2>
  );
}

// --- row 1 ---------------------------------------------------------------

function SummaryRow(
  { data, production }: { data: ExecutiveSummary; production?: ProductionStatus },
) {
  const coverage = percentOf(data.plotsInspected, data.plotsTotal);
  const growing = production?.inProduction.pct ?? null;
  const done = production?.finished.pct ?? null;
  return (
    <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-5">
      <KpiCard
        label="ขอบเขต"
        value={`${data.supplierTotal.toLocaleString()} ราย`}
        hint={`${data.plotsTotal.toLocaleString()} แปลง · ${NOW}`}
        Icon={Building2} tone="blue" to="/farmlog/admin/suppliers"
      />
      <KpiCard
        label="แปลงที่เคยถูกตรวจ"
        value={coverage === null ? DASH : `${coverage}%`}
        hint={
          coverage === null
            ? 'ยังไม่มีแปลงในระบบ'
            : `${data.plotsInspected.toLocaleString()}/${data.plotsTotal.toLocaleString()} · ยังไม่เคยตรวจ ${data.plotsNeverInspected.toLocaleString()} · ${NOW}`
        }
        Icon={ClipboardCheck} tone="primary" to="/farmlog/admin/plots"
      />
      {/* Two cards, not one blended figure — an estimate and a result are
          different kinds of number and must not be averaged together. */}
      <KpiCard
        label="กำลังผลิต (คาดการณ์)"
        value={growing === null ? DASH : `${growing.toFixed(1)}%`}
        hint={
          production
            ? `${production.inProduction.plots} แปลงยังปลูกอยู่ · ${IN_RANGE}`
            : 'กำลังโหลด'
        }
        Icon={Sprout} tone={achievementTone(growing)}
      />
      <KpiCard
        label="ผลิตจบแล้ว (จริง)"
        value={done === null ? DASH : `${done.toFixed(1)}%`}
        hint={
          production
            ? `${production.finished.plots} แปลงวัดผลผลิตหลังคัดแล้ว · ${IN_RANGE}`
            : 'กำลังโหลด'
        }
        Icon={Package} tone={achievementTone(done)}
      />
      {/* The three reasons OVERLAP — one plot can be overdue and
          low-scoring at once — so they are listed, never summed. */}
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

// --- row 3: the two lenses ----------------------------------------------

function LensCard(
  { title, subtitle, figures, tone, children }: {
    title: string; subtitle: string; figures: LensFigures;
    tone: 'blue' | 'green' | 'orange' | 'red'; children?: ReactNode;
  },
) {
  const target = formatKg(figures.targetKg);
  const value = formatKg(figures.valueKg);
  return (
    <div className="rounded-lg border bg-card p-4 shadow-sm">
      <div className="flex items-baseline justify-between gap-2">
        <h3 className="text-sm font-semibold">{title}</h3>
        <span className="text-sm text-muted-foreground">
          {figures.plots.toLocaleString()} แปลง
        </span>
      </div>
      <p className="mt-0.5 text-xs text-muted-foreground">{subtitle}</p>
      <p className="mt-3 text-3xl font-semibold">
        {figures.pct === null ? DASH : `${figures.pct.toFixed(1)}%`}
      </p>
      <p className="text-xs text-muted-foreground">
        {figures.pct === null ? 'ยังไม่มีเป้าผลิตให้เทียบ' : 'ของเป้าผลิต'}
      </p>
      <div className="mt-3">
        <ProgressRow
          label={`${title} เทียบเป้า`}
          value={value ?? DASH}
          barPct={figures.pct}
          note={target ? `เป้า ${target}` : undefined}
          tone={tone}
        />
      </div>
      {children}
    </div>
  );
}

function ProductionBlock({ data }: { data: ProductionStatus }) {
  const retention = data.finishedRetentionPct;
  const doneTone = achievementTone(data.finished.pct);
  return (
    <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
      <LensCard
        title="กำลังผลิต"
        subtitle="วัดจาก Yield ของการตรวจครั้งล่าสุด — ยังเป็นประมาณการ"
        figures={data.inProduction}
        tone="blue"
      >
        <div className="mt-4 border-t pt-3">
          <p className="mb-2 text-xs font-medium text-muted-foreground">
            ระยะของรอบที่กำลังผลิต
          </p>
          {/* Read together with the percentage above: 90% with most plots
              at ระยะงอก is a guess; 90% at เก็บเกี่ยว is nearly a result. */}
          <StageBar slices={data.byStage} />
        </div>
      </LensCard>

      <LensCard
        title="ผลิตจบแล้ว"
        subtitle="วัดจากผลผลิตหลังทำความสะอาดของบันทึกสุดท้าย — ของจริง"
        figures={data.finished}
        tone={doneTone === 'blue' ? 'green' : doneTone}
      >
        <div className="mt-4 border-t pt-3 text-sm">
          <div className="flex items-baseline justify-between">
            <span className="text-muted-foreground">อัตราคงเหลือหลังคัด</span>
            <span className="font-semibold">
              {retention === null ? DASH : `${retention.toFixed(1)}%`}
            </span>
          </div>
          <p className="mt-1 text-xs text-muted-foreground">
            {retention === null
              ? 'ยังไม่มีแปลงที่กรอกผลผลิตหลังทำความสะอาด'
              : `หลังคัด ÷ เก็บได้ ${formatKg(data.finishedHarvestKg) ?? DASH} ของแปลงชุดเดียวกัน`}
          </p>
        </div>
      </LensCard>
    </div>
  );
}

// --- row 4: concerns -----------------------------------------------------

function ConcernStrip(
  { rows, thresholdPct }: { rows: ConcernRow[]; thresholdPct: number },
) {
  if (rows.length === 0) {
    return <BlockEmpty text="ไม่พบสัญญาณที่ต้องกังวลในตัวกรองนี้" />;
  }
  return (
    <ul className="divide-y rounded-lg border bg-card shadow-sm">
      {rows.map((r, i) => (
        <li key={`${r.kind}-${r.key}-${i}`} className="flex items-start gap-3 px-4 py-2.5">
          <span
            className={
              'mt-1 inline-block h-2.5 w-2.5 shrink-0 rounded-full ' +
              (r.severity === 'high' ? 'bg-red-600' : 'bg-orange-500')
            }
            aria-label={r.severity === 'high' ? 'รุนแรง' : 'เฝ้าระวัง'}
          />
          <span className="text-sm">{concernText(r, thresholdPct)}</span>
        </li>
      ))}
    </ul>
  );
}

// --- row 5: comparison, chart and table together -------------------------

/** Thai names for every grouping the SERVER can answer with, including the
 *  one the tabs no longer offer — the table header reads the server's
 *  answer, so an old bookmark must still render "พันธุ์" and not the raw
 *  key. */
const GROUP_LABELS: Record<string, string> = {
  p_code: 'P.Code',
  supplier: 'Supplier',
  crop: 'พืช',
  variety: 'พันธุ์',
};

/** What the tabs offer. พันธุ์ is deliberately absent: on UAT every one of
 *  the 494 active P.Codes maps to exactly one variety, and all 763 plots
 *  carry both — so the two groupings produce the same 20 rows, and P.Code
 *  is the one production strains are actually analysed by. Two tabs for one
 *  answer is just a choice nobody can make correctly. */
const GROUP_TABS: { value: BreakdownGroup; label: string }[] = [
  { value: 'p_code', label: GROUP_LABELS.p_code },
  { value: 'supplier', label: GROUP_LABELS.supplier },
  { value: 'crop', label: GROUP_LABELS.crop },
];

function BreakdownTable({ rows, groupBy }: { rows: BreakdownRow[]; groupBy: string }) {
  if (rows.length === 0) return <BlockEmpty text="ไม่มีข้อมูลสำหรับการเทียบนี้" />;
  // The SERVER's answer, never the request: if the two disagree the header
  // tells the truth about what these rows are.
  const head = GROUP_LABELS[groupBy] ?? groupBy;
  return (
    <div className="overflow-x-auto rounded-lg border bg-card shadow-sm">
      <table aria-label="เทียบผลงาน" className="min-w-full divide-y divide-border">
        <thead className="bg-muted/40">
          <tr>
            {[head, 'แปลง', 'ตรวจแล้ว', 'ค้าง', 'คะแนน',
              'กำลังผลิต', 'ผลิตจบ', 'ส่วนต่าง'].map((h) => (
              <th key={h} className="px-3 py-2.5 text-left text-xs font-medium uppercase tracking-wider text-muted-foreground">
                {h}
              </th>
            ))}
          </tr>
        </thead>
        <tbody className="divide-y divide-border/60">
          {rows.map((r) => {
            const gap = gapPoints({
              key: r.key, label: r.label,
              estimatePct: r.estimatePct, actualPct: r.actualPct,
            });
            return (
              <tr key={r.key} className="hover:bg-muted/30">
                <td className="whitespace-nowrap px-3 py-2.5 text-sm font-medium">{r.label}</td>
                <td className="px-3 py-2.5 text-sm tabular-nums">{r.plots.toLocaleString()}</td>
                <td className="px-3 py-2.5 text-sm tabular-nums">{r.plotsInspected.toLocaleString()}</td>
                <td className="px-3 py-2.5 text-sm tabular-nums">{r.plotsOverdue.toLocaleString()}</td>
                <td className="px-3 py-2.5 text-sm tabular-nums">
                  {r.avgScore === null ? DASH : r.avgScore.toFixed(1)}
                </td>
                <td className="px-3 py-2.5 text-sm tabular-nums">
                  {r.estimatePct === null ? DASH : `${r.estimatePct.toFixed(1)}%`}
                  <span className="ml-1 text-xs text-muted-foreground">({r.estimatePlots})</span>
                </td>
                <td className="px-3 py-2.5 text-sm tabular-nums">
                  {r.actualPct === null ? DASH : `${r.actualPct.toFixed(1)}%`}
                  <span className="ml-1 text-xs text-muted-foreground">({r.actualPlots})</span>
                </td>
                <td className="px-3 py-2.5 text-sm tabular-nums">
                  {gap === null ? (
                    <span className="text-xs text-muted-foreground">ยังไม่จบ</span>
                  ) : (
                    <span className={gap > 0 ? 'text-red-700' : 'text-green-700'}>
                      {gap > 0 ? `▼${gap}` : `▲${Math.abs(gap)}`}
                    </span>
                  )}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
      <p className="border-t px-4 py-2 text-xs text-muted-foreground">
        เรียงจากผลงานแย่ไปดี · ตัวเลขในวงเล็บคือจำนวนแปลงของแต่ละมุม
      </p>
    </div>
  );
}

// --- row 6: the worklist -------------------------------------------------

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
  const [groupBy, setGroupBy] = useState<BreakdownGroup>('p_code');
  // Two pieces of state for one number: what is in the box, and what has
  // been asked of the server. Without the pause every keystroke would be a
  // request, and "7" on the way to "75" would briefly flag almost nothing.
  const [thresholdText, setThresholdText] = useState(String(DEFAULT_CONCERN_PCT));
  const [thresholdPct, setThresholdPct] = useState<number>(DEFAULT_CONCERN_PCT);
  useEffect(() => {
    const id = window.setTimeout(() => {
      const n = Number(thresholdText);
      // An empty or unreadable box means "the default", never 0 — which
      // would quietly report that nothing is wrong anywhere.
      setThresholdPct(thresholdText.trim() !== '' && Number.isFinite(n)
        ? n : DEFAULT_CONCERN_PCT);
    }, 400);
    return () => window.clearTimeout(id);
  }, [thresholdText]);
  const { range, dateFrom, dateTo, supplierId, pCode, cycleStatus } = filters;
  const scope = [range, dateFrom, dateTo, supplierId, pCode, cycleStatus];

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

  // Independent queries on purpose: a failing block renders its own message
  // while the rest of the page keeps working. keepPreviousData so changing
  // a filter does not empty the page — and does not disable the Export
  // button mid-click, which it used to.
  const common = { staleTime: 60_000, placeholderData: keepPreviousData };
  const summary = useQuery({
    queryKey: ['executive-summary', ...scope],
    queryFn: () => getExecutiveSummary(filters), ...common,
  });
  const production = useQuery({
    queryKey: ['executive-production', ...scope],
    queryFn: () => getProductionStatus(filters), ...common,
  });
  const concerns = useQuery({
    queryKey: ['executive-concerns', ...scope, thresholdPct],
    queryFn: () => getConcerns(filters, thresholdPct), ...common,
  });
  const trend = useQuery({
    queryKey: ['executive-trend', ...scope],
    queryFn: () => getExecutiveTrend(filters), ...common,
  });
  const breakdown = useQuery({
    queryKey: ['executive-breakdown', ...scope, groupBy],
    queryFn: () => getExecutiveBreakdown(filters, groupBy), ...common,
  });
  // No period in the key: this list is about today. The scope filters ARE
  // in it — picking a supplier changes what "this" is.
  const followUp = useQuery({
    queryKey: ['executive-follow-up', supplierId, pCode, cycleStatus],
    queryFn: () => getExecutiveFollowUp(filters, 10), ...common,
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

  const dumbbellRows: DumbbellRow[] = (breakdown.data?.rows ?? [])
    .filter((r) => r.estimatePct !== null || r.actualPct !== null)
    .map((r) => ({
      key: r.key, label: r.label,
      estimatePct: r.estimatePct, actualPct: r.actualPct,
    }));

  return (
    <div className="container mx-auto px-4 py-8 sm:px-6 lg:px-8">
      <header className="mb-6 flex flex-wrap items-start justify-between gap-3">
        <div>
          <h1 className="text-xl font-bold">ภาพรวมผู้บริหาร</h1>
          <p className="mt-1 text-sm text-muted-foreground">
            ภาพรวมทุก Supplier — สถานะการผลิต ผลผลิตเทียบเป้า และแปลงที่ต้องตามงาน
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <PeriodFilter
            value={{ range, dateFrom, dateTo }}
            onChange={(p) => setFilters((f) => ({ ...f, ...p }))}
          />
          <FilterMenu
            value={{ supplierId, pCode, cycleStatus }}
            onChange={(v) => setFilters((f) => ({ ...f, ...v }))}
            supplierOptions={(suppliers.data ?? []).map((s) => ({
              value: s.id, label: `${s.code} — ${s.name}`,
            }))}
            pCodeOptions={(pCodes.data ?? []).map((m) => ({
              value: m.value,
              label: m.parent ? `${m.value} · ${m.parent}` : m.value,
            }))}
          />
        </div>
      </header>

      <section aria-label="ตัวเลขสรุป">
        {summary.isLoading && (
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-5">
            {Array.from({ length: 5 }).map((_, i) => <SkeletonCard key={i} />)}
          </div>
        )}
        {summary.isError && <BlockError what="ตัวเลขสรุป" />}
        {summary.data && (
          <SummaryRow data={summary.data} production={production.data} />
        )}
      </section>

      <section className="mt-6" aria-label="แนวโน้มการตรวจและคะแนน">
        <SectionTitle clock={IN_RANGE}>แนวโน้มการตรวจและคะแนน</SectionTitle>
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

      <section className="mt-6" aria-label="สถานะการผลิต">
        <SectionTitle clock={IN_RANGE}>สถานะการผลิต</SectionTitle>
        {production.isError ? (
          <BlockError what="สถานะการผลิต" />
        ) : production.isLoading ? (
          <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
            <SkeletonCard /><SkeletonCard />
          </div>
        ) : production.data ? (
          <ProductionBlock data={production.data} />
        ) : null}
      </section>

      <section className="mt-6" id={CONCERNS_ANCHOR} aria-label="สิ่งที่ต้องกังวล">
        <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
          <h2 className="text-sm font-semibold text-muted-foreground">
            สิ่งที่ต้องกังวล <span className="font-normal">({IN_RANGE})</span>
          </h2>
          <label className="flex items-center gap-2 text-sm">
            <span className="text-muted-foreground">แจ้งเตือนเมื่อต่ำกว่า</span>
            <input
              type="number"
              aria-label="เกณฑ์แจ้งเตือน (% ของเป้า)"
              min={1}
              max={200}
              value={thresholdText}
              onChange={(e) => setThresholdText(e.target.value)}
              className="w-20 rounded-md border px-2 py-1 text-sm tabular-nums"
            />
            <span className="text-muted-foreground">% ของเป้า</span>
          </label>
        </div>
        {concerns.isError ? (
          <BlockError what="สัญญาณเตือน" />
        ) : concerns.isLoading ? (
          <SkeletonCard />
        ) : concerns.data ? (
          <ConcernStrip rows={concerns.data.rows} thresholdPct={concerns.data.thresholdPct} />
        ) : null}
      </section>

      <section className="mt-6" aria-label="เทียบผลงาน">
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
                {' '}{GROUP_LABELS[groupBy] ?? groupBy}
                {' '}— ตารางด้านล่างเทียบตาม
                {' '}{GROUP_LABELS[breakdown.data!.groupBy] ?? breakdown.data!.groupBy}
                {' '}แทน
              </p>
            )}
            {/* Chart and table are the same rows in the same order: the
                chart shows the shape, the table the detail. */}
            <div className="mb-3">
              <DumbbellChart
                rows={dumbbellRows}
                emptyMessage="ยังไม่มีกลุ่มที่มีตัวเลขให้เทียบในตัวกรองนี้"
              />
            </div>
            <BreakdownTable rows={breakdown.data.rows} groupBy={breakdown.data.groupBy} />
          </>
        ) : null}
      </section>

      <section className="mt-6" id={FOLLOW_UP_ANCHOR} aria-label="แปลงที่ต้องตามงาน">
        <SectionTitle clock={NOW}>แปลงที่ต้องตามงาน</SectionTitle>
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
