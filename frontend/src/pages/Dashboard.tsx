/**
 * Dashboard — FarmLog KPI summary, scope-filtered by the current user's role.
 */
import { Link } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import {
  AlertTriangle,
  Building2,
  ClipboardList,
  Gauge,
  Leaf,
  MapPin,
  TrendingUp,
} from 'lucide-react';
import type { LucideIcon } from 'lucide-react';
import { getDashboardSummary, type CropTypeStat } from '../api/dashboard';
import { useHasPermission } from '../hooks/useHasPermission';
import { PlotMapCard } from '../components/farmlog/PlotMapCard';

type StatTone = 'primary' | 'green' | 'red' | 'orange' | 'blue' | 'purple';

const TONE: Record<StatTone, string> = {
  primary: 'bg-primary/10 text-primary',
  green:   'bg-green-50 text-green-700',
  red:     'bg-red-50 text-red-700',
  orange:  'bg-orange-50 text-orange-700',
  blue:    'bg-blue-50 text-blue-700',
  purple:  'bg-purple-50 text-purple-700',
};

interface KpiCardProps {
  label: string;
  value: string | number;
  hint: string;
  Icon: LucideIcon;
  tone: StatTone;
  to?: string;
}

function KpiCard({ label, value, hint, Icon, tone, to }: KpiCardProps) {
  const inner = (
    <div className="rounded-lg border bg-card p-4 text-card-foreground shadow-sm hover:shadow-md transition-shadow">
      <div className="flex items-start justify-between gap-2">
        <p className="text-sm font-medium text-muted-foreground">{label}</p>
        <span className={`flex h-8 w-8 shrink-0 items-center justify-center rounded-md ${TONE[tone]}`}>
          <Icon className="h-4 w-4" />
        </span>
      </div>
      <p className="mt-2 text-2xl font-semibold">{value}</p>
      <p className="mt-1 text-xs text-muted-foreground">{hint}</p>
    </div>
  );
  return to ? <Link to={to}>{inner}</Link> : inner;
}

function SkeletonCard() {
  return (
    <div className="rounded-lg border bg-card p-4 shadow-sm animate-pulse">
      <div className="h-4 w-1/2 rounded bg-muted" />
      <div className="mt-3 h-7 w-1/3 rounded bg-muted" />
      <div className="mt-2 h-3 w-2/3 rounded bg-muted" />
    </div>
  );
}

function CropTable({ rows }: { rows: CropTypeStat[] }) {
  if (rows.length === 0) return null;
  const total = rows.reduce((s, r) => s + r.count, 0);
  return (
    <div className="overflow-hidden rounded-lg border bg-card shadow-sm">
      <div className="flex items-center gap-2 border-b px-4 py-3">
        <Leaf className="h-4 w-4 text-green-600" />
        <h2 className="text-sm font-semibold">บันทึกตามชนิดพืช</h2>
      </div>
      <table className="min-w-full divide-y divide-border">
        <thead className="bg-muted/40">
          <tr>
            {['พืช', 'จำนวนบันทึก', '%'].map(h => (
              <th key={h} className="px-4 py-2 text-left text-xs font-medium text-muted-foreground uppercase tracking-wider">
                {h}
              </th>
            ))}
          </tr>
        </thead>
        <tbody className="divide-y divide-border">
          {rows.map(r => (
            <tr key={r.cropType ?? 'none'} className="hover:bg-muted/20">
              <td className="px-4 py-2 text-sm">{r.cropType ?? <span className="text-muted-foreground italic">ไม่ระบุ</span>}</td>
              <td className="px-4 py-2 text-sm font-medium">{r.count}</td>
              <td className="px-4 py-2 text-sm text-muted-foreground">
                {total > 0 ? `${Math.round((r.count / total) * 100)}%` : '—'}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export function Dashboard() {
  const canSeeRecords   = useHasPermission('records.read');
  const canSeePlots     = useHasPermission('plots.read');
  const canSeeSuppliers = useHasPermission('suppliers.read');

  const { data, isLoading, isError } = useQuery({
    queryKey: ['dashboard-summary'],
    queryFn: getDashboardSummary,
    enabled: canSeeRecords,
    staleTime: 60_000,
  });

  return (
    <div className="container mx-auto px-4 py-8 sm:px-6 lg:px-8">
      <header className="mb-6">
        <h1 className="text-xl font-bold">Dashboard</h1>
        <p className="mt-1 text-sm text-muted-foreground">ภาพรวมการตรวจแปลงในขอบเขตของคุณ</p>
      </header>

      {isError && (
        <div className="mb-4 rounded-md border border-destructive/30 bg-destructive/10 px-4 py-3 text-sm text-destructive">
          ไม่สามารถโหลดข้อมูล — โปรดลองอีกครั้ง
        </div>
      )}

      {/* KPI grid */}
      <section className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-4">
        {isLoading ? (
          Array.from({ length: 4 }).map((_, i) => <SkeletonCard key={i} />)
        ) : data ? (
          <>
            <KpiCard
              label="บันทึกทั้งหมด"
              value={data.totalRecords.toLocaleString()}
              hint="ในขอบเขตของคุณ"
              Icon={ClipboardList}
              tone="green"
              to="/farmlog/records"
            />
            <KpiCard
              label="บันทึกเดือนนี้"
              value={data.recordsThisMonth.toLocaleString()}
              hint="นับจากต้นเดือน"
              Icon={TrendingUp}
              tone="blue"
              to="/farmlog/records"
            />
            {canSeePlots && (
              <KpiCard
                label="แปลงทั้งหมด"
                value={data.totalPlots.toLocaleString()}
                hint="แปลงที่ใช้งานอยู่"
                Icon={MapPin}
                tone="purple"
                to="/farmlog/admin/plots"
              />
            )}
            {canSeeSuppliers && data.totalSuppliers !== null && (
              <KpiCard
                label="Suppliers"
                value={data.totalSuppliers.toLocaleString()}
                hint="ที่ใช้งานอยู่"
                Icon={Building2}
                tone="primary"
                to="/farmlog/admin/suppliers"
              />
            )}
          </>
        ) : null}
      </section>

      {/* Condition score KPIs */}
      {data && (data.avgConditionScore !== null || data.lowScoreCount > 0) && (
        <section className="mt-6 grid grid-cols-1 gap-4 sm:grid-cols-2">
          {data.avgConditionScore !== null && (
            <KpiCard
              label="คะแนนสภาพแปลงเฉลี่ย"
              value={`${data.avgConditionScore.toFixed(1)} / 10`}
              hint="เฉลี่ยจากคะแนนการตรวจ 4 หัวข้อ"
              Icon={Gauge}
              tone="blue"
              to="/farmlog/records"
            />
          )}
          {data.lowScoreCount > 0 && (
            <KpiCard
              label="แปลงที่ควรเฝ้าระวัง"
              value={data.lowScoreCount.toLocaleString()}
              hint="บันทึกที่มีคะแนนข้อใดข้อหนึ่ง ≤ 3"
              Icon={AlertTriangle}
              tone="orange"
              to="/farmlog/records"
            />
          )}
        </section>
      )}

      {/* Plot location map (offline SVG, filter by crop/supplier/province) */}
      {canSeePlots && (
        <section className="mt-6">
          <PlotMapCard />
        </section>
      )}

      {/* Crop type breakdown */}
      {data && data.byCropType.length > 0 && (
        <section className="mt-6">
          <CropTable rows={data.byCropType} />
        </section>
      )}

      {/* Empty state */}
      {!isLoading && !isError && data && data.totalRecords === 0 && (
        <section className="mt-8 flex flex-col items-center justify-center gap-3 rounded-lg border border-dashed bg-card py-16 text-center">
          <ClipboardList className="h-10 w-10 text-muted-foreground" />
          <p className="text-sm font-medium text-muted-foreground">ยังไม่มีบันทึกการตรวจ</p>
          {canSeeRecords && (
            <Link
              to="/farmlog/records/new"
              className="inline-flex items-center gap-1.5 rounded-md bg-green-600 px-4 py-2 text-sm font-semibold text-white hover:bg-green-700"
            >
              สร้างบันทึกแรก
            </Link>
          )}
        </section>
      )}
    </div>
  );
}

export default Dashboard;
