/**
 * SmartPlotPicker — modal plot selector with text search and GPS distance sort.
 * Replaces the plain <select> for plot_id in RecordForm.
 */
import { useState, useEffect, useRef } from 'react';
import { Search, MapPin, ChevronDown, X, Loader2, Navigation } from 'lucide-react';
import { useQuery } from '@tanstack/react-query';
import { listPlots, type PlotSummary } from '../../api/plots';

interface Props {
  supplierId?: string;
  value: string;
  onChange: (plotId: string, plot: PlotSummary | null) => void;
  disabled?: boolean;
  error?: string;
}

function haversineKm(lat1: number, lon1: number, lat2: number, lon2: number): number {
  const R = 6371;
  const dLat = ((lat2 - lat1) * Math.PI) / 180;
  const dLon = ((lon2 - lon1) * Math.PI) / 180;
  const a =
    Math.sin(dLat / 2) ** 2 +
    Math.cos((lat1 * Math.PI) / 180) *
      Math.cos((lat2 * Math.PI) / 180) *
      Math.sin(dLon / 2) ** 2;
  return R * 2 * Math.atan2(Math.sqrt(a), Math.sqrt(1 - a));
}

export function SmartPlotPicker({ supplierId, value, onChange, disabled, error }: Props) {
  const [open, setOpen] = useState(false);
  const [search, setSearch] = useState('');
  const [userPos, setUserPos] = useState<{ lat: number; lng: number } | null>(null);
  const [gpsLoading, setGpsLoading] = useState(false);
  const [gpsError, setGpsError] = useState('');
  const inputRef = useRef<HTMLInputElement>(null);

  const { data: allPlots = [], isLoading } = useQuery({
    queryKey: ['plots', supplierId ?? 'all'],
    queryFn: () => listPlots({ supplierId: supplierId || undefined, activeOnly: true, limit: 500 }),
    staleTime: 60_000,
  });

  const selected = allPlots.find(p => p.id === value) ?? null;

  useEffect(() => {
    if (open) setTimeout(() => inputRef.current?.focus(), 50);
  }, [open]);

  function requestGps() {
    if (!navigator.geolocation) {
      setGpsError('เบราว์เซอร์ไม่รองรับ GPS');
      return;
    }
    setGpsLoading(true);
    setGpsError('');
    navigator.geolocation.getCurrentPosition(
      pos => {
        setUserPos({ lat: pos.coords.latitude, lng: pos.coords.longitude });
        setGpsLoading(false);
      },
      () => {
        setGpsError('ไม่สามารถรับ GPS ได้ กรุณาอนุญาต Location');
        setGpsLoading(false);
      },
      { timeout: 8000 },
    );
  }

  const filtered = allPlots
    .filter(p => {
      if (!search) return true;
      const q = search.toLowerCase();
      return p.plotCode.toLowerCase().includes(q) || p.name.toLowerCase().includes(q);
    })
    .sort((a, b) => {
      if (!userPos) return 0;
      const aHasGps = a.latitude != null && a.longitude != null;
      const bHasGps = b.latitude != null && b.longitude != null;
      if (!aHasGps && !bHasGps) return 0;
      if (!aHasGps) return 1;
      if (!bHasGps) return -1;
      const da = haversineKm(userPos.lat, userPos.lng, a.latitude!, a.longitude!);
      const db = haversineKm(userPos.lat, userPos.lng, b.latitude!, b.longitude!);
      return da - db;
    });

  function select(plot: PlotSummary) {
    onChange(plot.id, plot);
    setOpen(false);
    setSearch('');
  }

  function clear(e: React.MouseEvent) {
    e.stopPropagation();
    onChange('', null);
  }

  return (
    <div className="relative">
      {/* Trigger button */}
      <button
        type="button"
        onClick={() => !disabled && setOpen(true)}
        disabled={disabled}
        className={`flex w-full items-center justify-between rounded-md border px-3 py-2 text-sm shadow-sm ${
          error ? 'border-red-400' : 'border-gray-300'
        } ${disabled ? 'cursor-not-allowed bg-gray-50 text-gray-400' : 'bg-white hover:border-green-500 focus:outline-none focus:ring-1 focus:ring-green-500'}`}
      >
        <span className={selected ? 'text-gray-900' : 'text-gray-400'}>
          {selected ? (
            <span>
              <span className="font-medium">{selected.plotCode}</span>
              <span className="ml-1.5 text-gray-500">{selected.name}</span>
            </span>
          ) : (
            '— เลือกแปลง —'
          )}
        </span>
        <span className="flex items-center gap-1">
          {selected && !disabled && (
            <X className="h-3.5 w-3.5 text-gray-400 hover:text-gray-600" onClick={clear} />
          )}
          <ChevronDown className="h-4 w-4 text-gray-400" />
        </span>
      </button>

      {/* Modal */}
      {open && (
        <div className="fixed inset-0 z-50 flex items-end justify-center sm:items-center" onClick={() => setOpen(false)}>
          <div className="absolute inset-0 bg-black/30" />
          <div
            className="relative z-10 w-full max-w-md rounded-t-2xl bg-white shadow-xl sm:rounded-xl"
            onClick={e => e.stopPropagation()}
          >
            {/* Header */}
            <div className="flex items-center justify-between border-b px-4 py-3">
              <span className="text-sm font-semibold text-gray-900">เลือกแปลง</span>
              <button onClick={() => setOpen(false)} className="rounded p-1 text-gray-400 hover:bg-gray-100">
                <X className="h-4 w-4" />
              </button>
            </div>

            {/* Search + GPS row */}
            <div className="flex gap-2 px-4 pt-3 pb-2">
              <div className="relative flex-1">
                <Search className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-gray-400" />
                <input
                  ref={inputRef}
                  type="text"
                  value={search}
                  onChange={e => setSearch(e.target.value)}
                  placeholder="ค้นหารหัส / ชื่อแปลง"
                  className="w-full rounded-md border border-gray-300 py-2 pl-9 pr-3 text-sm focus:border-green-500 focus:outline-none focus:ring-1 focus:ring-green-500"
                />
              </div>
              <button
                type="button"
                onClick={requestGps}
                disabled={gpsLoading}
                title="เรียงตามระยะใกล้ฉัน"
                className={`flex items-center gap-1 rounded-md border px-3 py-2 text-xs font-medium shadow-sm ${
                  userPos
                    ? 'border-green-500 bg-green-50 text-green-700'
                    : 'border-gray-300 text-gray-600 hover:border-green-400 hover:text-green-600'
                }`}
              >
                {gpsLoading ? (
                  <Loader2 className="h-4 w-4 animate-spin" />
                ) : (
                  <Navigation className="h-4 w-4" />
                )}
                ใกล้ฉัน
              </button>
            </div>
            {gpsError && <p className="px-4 pb-1 text-xs text-red-600">{gpsError}</p>}
            {userPos && (
              <p className="px-4 pb-1 text-xs text-green-600">
                เรียงตามระยะจากคุณ ({userPos.lat.toFixed(4)}, {userPos.lng.toFixed(4)})
              </p>
            )}

            {/* Plot list */}
            <ul className="max-h-72 overflow-y-auto divide-y divide-gray-100 pb-2">
              {isLoading && (
                <li className="flex justify-center py-8 text-gray-400">
                  <Loader2 className="h-5 w-5 animate-spin" />
                </li>
              )}
              {!isLoading && filtered.length === 0 && (
                <li className="py-8 text-center text-sm text-gray-400">ไม่พบแปลง</li>
              )}
              {filtered.map(plot => {
                const dist =
                  userPos && plot.latitude != null && plot.longitude != null
                    ? haversineKm(userPos.lat, userPos.lng, plot.latitude, plot.longitude)
                    : null;
                return (
                  <li key={plot.id}>
                    <button
                      type="button"
                      onClick={() => select(plot)}
                      className={`flex w-full items-center justify-between px-4 py-3 text-left hover:bg-green-50 ${
                        plot.id === value ? 'bg-green-50' : ''
                      }`}
                    >
                      <span>
                        <span className="block text-sm font-medium text-gray-900">{plot.plotCode}</span>
                        <span className="block text-xs text-gray-500">{plot.name}</span>
                        {plot.province && (
                          <span className="block text-xs text-gray-400">{plot.province}</span>
                        )}
                      </span>
                      {dist != null && (
                        <span className="flex shrink-0 items-center gap-0.5 text-xs text-gray-400">
                          <MapPin className="h-3 w-3" />
                          {dist < 1 ? `${(dist * 1000).toFixed(0)} ม.` : `${dist.toFixed(1)} กม.`}
                        </span>
                      )}
                    </button>
                  </li>
                );
              })}
            </ul>
          </div>
        </div>
      )}
    </div>
  );
}
