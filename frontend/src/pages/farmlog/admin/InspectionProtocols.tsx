/**
 * Inspection Protocol admin (round 5.5) — view/edit the 4 criteria labels of
 * each growth stage. Grouped by Growth Stage (not a flat table): each stage
 * card shows its 4 fixed score slots and their editable labels. Labels only —
 * slots and the 4-per-stage shape are fixed this round.
 *
 * The record forms read this same config; editing a label here changes what
 * new records are scored/snapshotted under, while old records keep their
 * frozen snapshot labels.
 */
import { useEffect, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Loader2, Pencil } from 'lucide-react';
import {
  fetchAdminInspectionProtocols,
  bulkUpdateInspectionProtocolCriteria,
  type InspectionProtocolAdminStage,
} from '../../../api/inspectionProtocols';
import { useHasPermission } from '../../../hooks/useHasPermission';

// Editing labels here changes what NEW records are scored/snapshotted under,
// so refresh every query that reads the protocol — the admin editor, the
// logged-in RecordForm, and the public /public/inspect flow.
const PROTOCOL_QUERY_KEYS = [
  ['admin-inspection-protocols'],
  ['inspection-protocols'],
  ['public-inspection-protocols'],
] as const;

export function InspectionProtocols() {
  const canUpdate = useHasPermission('masterdata.update');
  const { data, isLoading, isError } = useQuery({
    queryKey: ['admin-inspection-protocols'],
    queryFn: fetchAdminInspectionProtocols,
  });

  return (
    <div className="container mx-auto px-4 py-8 sm:px-6 lg:px-8">
      <header>
        <h1 className="text-xl font-bold">เกณฑ์การตรวจแปลง (Inspection Protocol)</h1>
        <p className="mt-1 text-sm text-muted-foreground">
          กำหนดชื่อเกณฑ์ให้คะแนน 4 ข้อของแต่ละระยะการเจริญเติบโต — บันทึกใหม่จะใช้ชื่อล่าสุด
          ส่วนบันทึกเดิมยังคงชื่อเดิมตามที่บันทึกไว้
        </p>
      </header>

      {isLoading ? (
        <div className="flex items-center justify-center py-16">
          <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
        </div>
      ) : isError || !data ? (
        <p className="mt-6 rounded-md border border-destructive/30 bg-destructive/10 p-4 text-sm text-destructive">
          โหลดเกณฑ์การตรวจไม่สำเร็จ
        </p>
      ) : data.stages.length === 0 ? (
        <p className="mt-6 rounded-md border border-border bg-card p-4 text-sm text-muted-foreground">
          ยังไม่มีเกณฑ์การตรวจในระบบ
        </p>
      ) : (
        <div className="mt-6 grid grid-cols-1 gap-4 lg:grid-cols-2">
          {data.stages.map((stage) => (
            <StageCard key={stage.growthStage} stage={stage} canUpdate={canUpdate} />
          ))}
        </div>
      )}
    </div>
  );
}

function StageCard({ stage, canUpdate }: { stage: InspectionProtocolAdminStage; canUpdate: boolean }) {
  const qc = useQueryClient();
  const [editing, setEditing] = useState(false);
  const [labels, setLabels] = useState<Record<string, string>>(() =>
    Object.fromEntries(stage.criteria.map((c) => [c.id, c.label])),
  );
  const [error, setError] = useState('');

  // Re-sync from server after a save/refetch (stage identity changes).
  useEffect(() => {
    setLabels(Object.fromEntries(stage.criteria.map((c) => [c.id, c.label])));
    setEditing(false);
    setError('');
  }, [stage]);

  const saveM = useMutation({
    // One atomic bulk PATCH (round 5.6) instead of per-label Promise.all — no
    // partial save possible.
    mutationFn: async () => {
      const changed = stage.criteria
        .filter((c) => labels[c.id].trim() !== c.label)
        .map((c) => ({ id: c.id, label: labels[c.id].trim() }));
      if (changed.length > 0) await bulkUpdateInspectionProtocolCriteria(changed);
    },
    onSuccess: () => {
      for (const queryKey of PROTOCOL_QUERY_KEYS) qc.invalidateQueries({ queryKey });
    },
    onError: () => {
      // Re-sync from server so the UI never shows a half-applied state, and
      // surface a reliable error.
      qc.invalidateQueries({ queryKey: ['admin-inspection-protocols'] });
      setError('บันทึกไม่สำเร็จ กรุณาตรวจสอบและลองใหม่อีกครั้ง');
    },
  });

  function onSave() {
    setError('');
    if (stage.criteria.some((c) => !labels[c.id]?.trim())) {
      setError('ชื่อเกณฑ์ต้องไม่ว่าง');
      return;
    }
    saveM.mutate();
  }

  function onCancel() {
    setLabels(Object.fromEntries(stage.criteria.map((c) => [c.id, c.label])));
    setEditing(false);
    setError('');
  }

  const inputCls =
    'w-full rounded-md border border-input bg-background px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-ring';

  return (
    <section className="rounded-lg border border-border bg-card p-5 shadow-sm">
      <div className="mb-3 flex items-center justify-between">
        <h2 className="text-base font-semibold">{stage.growthStage}</h2>
        {canUpdate && !editing && (
          <button
            type="button"
            onClick={() => setEditing(true)}
            className="inline-flex items-center gap-1.5 rounded-md border border-border px-3 py-1.5 text-xs font-medium hover:bg-secondary"
          >
            <Pencil className="h-3.5 w-3.5" /> แก้ไข
          </button>
        )}
      </div>

      <ol className="space-y-2">
        {stage.criteria.map((c, i) => (
          <li key={c.id} className="flex items-center gap-3">
            <span className="w-14 shrink-0 rounded-full bg-secondary px-2 py-0.5 text-center text-xs text-secondary-foreground">
              ช่อง {i + 1}
            </span>
            {editing ? (
              <input
                aria-label={`ชื่อเกณฑ์ ช่อง ${i + 1}`}
                value={labels[c.id] ?? ''}
                onChange={(e) => setLabels((prev) => ({ ...prev, [c.id]: e.target.value }))}
                maxLength={255}
                className={inputCls}
              />
            ) : (
              <span className="text-sm font-medium text-foreground">{c.label}</span>
            )}
          </li>
        ))}
      </ol>

      {error && <p className="mt-2 text-sm text-destructive">{error}</p>}

      {editing && (
        <div className="mt-4 flex justify-end gap-2">
          <button
            type="button"
            onClick={onCancel}
            className="rounded-md border border-border px-4 py-2 text-sm hover:bg-secondary"
          >
            ยกเลิก
          </button>
          <button
            type="button"
            onClick={onSave}
            disabled={saveM.isPending}
            className="inline-flex items-center gap-2 rounded-md bg-primary px-4 py-2 text-sm font-medium text-primary-foreground disabled:opacity-60"
          >
            {saveM.isPending && <Loader2 className="h-4 w-4 animate-spin" />}
            บันทึก
          </button>
        </div>
      )}
    </section>
  );
}

export default InspectionProtocols;
