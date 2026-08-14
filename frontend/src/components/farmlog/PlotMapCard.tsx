/**
 * PlotMapCard — Dashboard card: Thailand map of plot locations with client-side
 * filters (crop / supplier / province). Fetches every plot in the user's scope
 * once (RLS-filtered server-side) and filters in memory, so switching filters
 * is instant and needs no extra requests. Rendered only for users with
 * plots.read (the Dashboard gates it).
 */
import { useMemo, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { MapPin } from 'lucide-react';
import { listPlots, type PlotSummary } from '../../api/plots';
import { fetchAllPages } from '../../lib/paginate';
import { ThailandPlotMap } from './ThailandPlotMap';
import { cropColor } from './cropColor';

const PAGE_SIZE = 200;

function uniqueSorted(values: (string | null)[]): string[] {
  return Array.from(new Set(values.filter((v): v is string => !!v && v.trim() !== ''))).sort(
    (a, b) => a.localeCompare(b, 'th'),
  );
}

export function PlotMapCard() {
  const [crop, setCrop] = useState('');
  const [supplierId, setSupplierId] = useState('');
  const [province, setProvince] = useState('');

  const { data: plots = [], isLoading, isError } = useQuery({
    queryKey: ['dashboard-map-plots'],
    queryFn: () =>
      fetchAllPages(
        (offset, limit) => listPlots({ activeOnly: true, limit, offset }),
        PAGE_SIZE,
      ),
    staleTime: 5 * 60 * 1000,
  });

  // Filter option lists derived from the full dataset (not the filtered
  // view), so choosing one filter never empties the others' menus.
  const cropOptions = useMemo(() => uniqueSorted(plots.map((p) => p.currentCrop)), [plots]);
  const provinceOptions = useMemo(() => uniqueSorted(plots.map((p) => p.province)), [plots]);
  const suppliers = useMemo(() => {
    const map = new Map<string, string>();
    for (const p of plots) {
      if (!map.has(p.supplierId)) {
        // Plot code is prefixed with the supplier code (e.g. SUP010-P002);
        // derive a readable supplier label without an extra suppliers fetch.
        map.set(p.supplierId, p.plotCode.split('-')[0] || p.supplierId);
      }
    }
    return Array.from(map, ([id, label]) => ({ id, label })).sort((a, b) =>
      a.label.localeCompare(b.label),
    );
  }, [plots]);

  const filtered = useMemo(
    () =>
      plots.filter(
        (p: PlotSummary) =>
          (crop === '' || (p.currentCrop ?? '') === crop) &&
          (supplierId === '' || p.supplierId === supplierId) &&
          (province === '' || (p.province ?? '') === province),
      ),
    [plots, crop, supplierId, province],
  );

  // Legend entries for the crops actually visible after filtering.
  const legend = useMemo(() => {
    const crops = uniqueSorted(filtered.map((p) => p.currentCrop));
    const hasUnspecified = filtered.some((p) => !p.currentCrop || p.currentCrop.trim() === '');
    const entries = crops.map((c) => ({ label: c, color: cropColor(c) }));
    if (hasUnspecified) entries.push({ label: 'ไม่ระบุพืช', color: cropColor(null) });
    return entries;
  }, [filtered]);

  const selectClass =
    'rounded-md border border-input bg-background px-3 py-2 text-sm shadow-sm focus:outline-none focus:ring-2 focus:ring-ring';

  return (
    <div className="overflow-hidden rounded-lg border bg-card shadow-sm">
      <div className="flex items-center gap-2 border-b px-4 py-3">
        <MapPin className="h-4 w-4 text-primary" />
        <h2 className="text-sm font-semibold">แผนที่ตำแหน่งแปลง</h2>
      </div>

      <div className="flex flex-col gap-3 border-b px-4 py-3 sm:flex-row sm:flex-wrap">
        <select value={crop} onChange={(e) => setCrop(e.target.value)} className={selectClass}>
          <option value="">ทุกชนิดพืช</option>
          {cropOptions.map((c) => (
            <option key={c} value={c}>{c}</option>
          ))}
        </select>
        <select value={supplierId} onChange={(e) => setSupplierId(e.target.value)} className={selectClass}>
          <option value="">ทุก Supplier</option>
          {suppliers.map((s) => (
            <option key={s.id} value={s.id}>{s.label}</option>
          ))}
        </select>
        <select value={province} onChange={(e) => setProvince(e.target.value)} className={selectClass}>
          <option value="">ทุกจังหวัด</option>
          {provinceOptions.map((p) => (
            <option key={p} value={p}>{p}</option>
          ))}
        </select>
      </div>

      <div className="p-4">
        {isError ? (
          <p className="py-16 text-center text-sm text-destructive">โหลดข้อมูลแปลงไม่สำเร็จ</p>
        ) : isLoading ? (
          <div className="py-16 text-center text-sm text-muted-foreground">กำลังโหลดแผนที่...</div>
        ) : (
          <>
            <ThailandPlotMap plots={filtered} totalCount={plots.length} />
            {legend.length > 0 && (
              <div className="mt-3 flex flex-wrap justify-center gap-x-4 gap-y-1.5">
                {legend.map((e) => (
                  <span key={e.label} className="inline-flex items-center gap-1.5 text-xs text-foreground">
                    <span
                      className="inline-block h-2.5 w-2.5 rounded-full"
                      style={{ backgroundColor: e.color }}
                    />
                    {e.label}
                  </span>
                ))}
              </div>
            )}
          </>
        )}
      </div>
    </div>
  );
}

export default PlotMapCard;
