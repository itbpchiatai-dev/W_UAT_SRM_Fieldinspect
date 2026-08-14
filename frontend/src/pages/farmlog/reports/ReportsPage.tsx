/**
 * ReportsPage — the FarmLog "รายงาน" landing, with segmented tabs between the
 * two reports (round 8-2.8B). Reuses the existing /farmlog/reports/plot-status
 * route + menu (no new menu migration); the tab is local UI state.
 *
 *   สถานะแปลงปัจจุบัน — Report #1 (default; unchanged behavior)
 *   ผลผลิตตามรอบปลูก — Report #2 (new): frozen final ESTIMATED yield per cycle.
 *
 * The two answer different questions and must not be conflated: tab #1 is the
 * LIVE current yield while a cycle is growing; tab #2 is the FROZEN estimate at
 * the moment each cycle closed (never actual harvested yield).
 */
import { useState } from 'react';
import { Sprout } from 'lucide-react';
import { PlotStatusReport } from './PlotStatusReport';
import { CycleYieldReport } from './CycleYieldReport';

type ReportTab = 'plot-status' | 'cycle-yield';

export function ReportsPage() {
  const [tab, setTab] = useState<ReportTab>('plot-status');

  function tabClass(active: boolean): string {
    return `rounded-md px-4 py-2 text-sm font-medium transition-colors ${
      active ? 'bg-white text-green-700 shadow-sm' : 'text-gray-500 hover:text-gray-700'
    }`;
  }

  return (
    <div>
      <div className="container mx-auto px-4 pt-6 sm:px-6 lg:px-8">
        <div role="tablist" aria-label="รายงาน" className="inline-flex rounded-lg border border-gray-200 bg-gray-50 p-1">
          <button
            type="button"
            role="tab"
            aria-selected={tab === 'plot-status'}
            onClick={() => setTab('plot-status')}
            className={tabClass(tab === 'plot-status')}
          >
            สถานะแปลงปัจจุบัน
          </button>
          <button
            type="button"
            role="tab"
            aria-selected={tab === 'cycle-yield'}
            onClick={() => setTab('cycle-yield')}
            className={tabClass(tab === 'cycle-yield')}
          >
            ผลผลิตตามรอบปลูก
          </button>
        </div>
      </div>

      {tab === 'plot-status' ? (
        <PlotStatusReport />
      ) : (
        <div className="container mx-auto px-4 py-8 sm:px-6 lg:px-8">
          <div className="mb-6 flex items-center gap-2">
            <Sprout className="h-6 w-6 text-green-600" />
            <h1 className="text-2xl font-bold text-gray-900">รายงานผลผลิตตามรอบปลูก</h1>
          </div>
          <CycleYieldReport />
        </div>
      )}
    </div>
  );
}
