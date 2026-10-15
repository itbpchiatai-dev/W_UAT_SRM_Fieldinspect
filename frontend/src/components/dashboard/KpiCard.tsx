/**
 * KpiCard / SkeletonCard — the stat tile both dashboards are built from.
 *
 * Round 43 lifted these out of pages/Dashboard.tsx unchanged, so the
 * executive overview and the scoped dashboard stay visually identical. The
 * alternative was a second copy, and two copies of a card drift: one gets a
 * hover state or a tighter line-height and the two pages stop looking like
 * the same product.
 */
import { Link } from 'react-router-dom';
import type { LucideIcon } from 'lucide-react';

export type StatTone = 'primary' | 'green' | 'red' | 'orange' | 'blue' | 'purple';

export const TONE: Record<StatTone, string> = {
  primary: 'bg-primary/10 text-primary',
  green:   'bg-green-50 text-green-700',
  red:     'bg-red-50 text-red-700',
  orange:  'bg-orange-50 text-orange-700',
  blue:    'bg-blue-50 text-blue-700',
  purple:  'bg-purple-50 text-purple-700',
};

export interface KpiCardProps {
  label: string;
  value: string | number;
  hint: string;
  Icon: LucideIcon;
  tone: StatTone;
  to?: string;
}

export function KpiCard({ label, value, hint, Icon, tone, to }: KpiCardProps) {
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

export function SkeletonCard() {
  return (
    <div className="rounded-lg border bg-card p-4 shadow-sm animate-pulse">
      <div className="h-4 w-1/2 rounded bg-muted" />
      <div className="mt-3 h-7 w-1/3 rounded bg-muted" />
      <div className="mt-2 h-3 w-2/3 rounded bg-muted" />
    </div>
  );
}
