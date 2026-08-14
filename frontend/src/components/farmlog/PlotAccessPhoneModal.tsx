/**
 * PlotAccessPhoneModal — full-replacement editor for a plot's access phones
 * (round 8-3C). Always fetches GET /plots/{plotId}/access-phones itself on
 * open (never trusts a stale primaryPhone/additionalPhones already sitting in
 * the Plots list/Plot Detail cache), lets the admin edit via the shared
 * PlotAccessPhoneFields, then PUTs the full config atomically on Save.
 *
 * Gated by plots.update — a caller without it should never render this modal
 * in the first place (see Plots.tsx/PlotDetail.tsx's action gating), but the
 * component defends itself too (same belt-and-suspenders style as the rest of
 * this app) by falling back to a read-only view if it's ever mounted without
 * the permission.
 */
import { useEffect, useRef, useState } from 'react';
import axios from 'axios';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Loader2, RotateCw } from 'lucide-react';
import {
  getPlotAccessPhones,
  replacePlotAccessPhones,
  type PlotAccessPhoneConfig,
} from '../../api/plots';
import { useHasPermission } from '../../hooks/useHasPermission';
import {
  PlotAccessPhoneFields,
  PlotAccessPhoneHeading,
  accessPhoneConfigToFieldsValue,
  buildPlotAccessPhoneConfig,
  emptyPlotAccessPhoneFieldsValue,
  type PlotAccessPhoneFieldsValue,
} from './PlotAccessPhoneFields';

function valuesEqual(a: PlotAccessPhoneFieldsValue, b: PlotAccessPhoneFieldsValue): boolean {
  return a.primaryPhone === b.primaryPhone
    && a.additionalPhones.length === b.additionalPhones.length
    && a.additionalPhones.every((v, i) => v === b.additionalPhones[i]);
}

/** Maps a replace-phones failure to the exact Thai message the round 8-3C
 * brief specifies per status code — never a raw stack trace. */
function describeAccessPhoneError(error: unknown): string {
  if (axios.isAxiosError(error)) {
    const status = error.response?.status;
    if (status === 404) return 'ไม่พบแปลงนี้ หรือคุณไม่มีสิทธิ์เข้าถึง';
    if (status === 409) return 'ข้อมูลมีการเปลี่ยนแปลงจากที่อื่น กรุณาโหลดใหม่แล้วลองอีกครั้ง';
    if (status === 422) {
      const data = error.response?.data as { detail?: unknown } | undefined;
      const detail = data?.detail;
      if (typeof detail === 'string') return detail;
      if (Array.isArray(detail)) {
        const msgs = detail.map((item) => (
          item && typeof item === 'object' && 'msg' in item ? String((item as { msg: unknown }).msg) : String(item)
        ));
        if (msgs.length > 0) return msgs.join(', ');
      }
      return 'ข้อมูลเบอร์โทรไม่ถูกต้อง กรุณาตรวจสอบอีกครั้ง';
    }
    if (!error.response) return 'เชื่อมต่อเครือข่ายไม่สำเร็จ กรุณาลองใหม่อีกครั้ง';
    return `บันทึกไม่สำเร็จ (HTTP ${status})`;
  }
  return 'เกิดข้อผิดพลาดที่ไม่คาดคิด กรุณาลองใหม่อีกครั้ง';
}

export function PlotAccessPhoneModal({
  plotId,
  plotLabel,
  onClose,
  onSaved,
}: {
  plotId: string;
  /** Optional display label for the modal title, e.g. the plot code. */
  plotLabel?: string;
  onClose: () => void;
  /** Called after a successful save + cache invalidation — the parent's job
   * here is just to close the modal (this component owns invalidation). */
  onSaved: () => void;
}) {
  const qc = useQueryClient();
  const canUpdate = useHasPermission('plots.update');

  const {
    data, isLoading, isError, refetch, isRefetching,
  } = useQuery({
    queryKey: ['plot-access-phones', plotId],
    queryFn: () => getPlotAccessPhones(plotId),
  });

  const [value, setValue] = useState<PlotAccessPhoneFieldsValue>(emptyPlotAccessPhoneFieldsValue());
  const originalRef = useRef<PlotAccessPhoneFieldsValue>(emptyPlotAccessPhoneFieldsValue());
  const syncedRef = useRef(false);

  // Populate the editable form ONCE from the freshly-fetched config — never
  // from whatever primaryPhone/additionalPhones the Plots list/Plot Detail
  // page already had cached, and never re-synced on a background refetch
  // (which would clobber in-progress edits).
  useEffect(() => {
    if (data && !syncedRef.current) {
      const initial = accessPhoneConfigToFieldsValue({
        primaryPhone: data.primaryPhone, additionalPhones: data.additionalPhones,
      });
      setValue(initial);
      originalRef.current = initial;
      syncedRef.current = true;
    }
  }, [data]);

  const isDirty = !valuesEqual(value, originalRef.current);
  const { config, hasErrors } = buildPlotAccessPhoneConfig(value);

  const saveM = useMutation({
    mutationFn: (payload: PlotAccessPhoneConfig) => replacePlotAccessPhones(plotId, payload),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['plot', plotId] });
      qc.invalidateQueries({ queryKey: ['plots'] });
      qc.invalidateQueries({ queryKey: ['plot-access-phones', plotId] });
      onSaved();
    },
  });

  function handleDismiss() {
    if (isDirty && !confirm('คุณมีการเปลี่ยนแปลงที่ยังไม่บันทึก ต้องการปิดหน้าต่างนี้หรือไม่?')) return;
    onClose();
  }

  function handleSave() {
    if (!config || hasErrors) return;
    saveM.mutate(config);
  }

  const readOnly = !canUpdate;
  const title = plotLabel ? `จัดการเบอร์เข้าตรวจแปลง ${plotLabel}` : 'จัดการเบอร์เข้าตรวจแปลง';

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4">
      <div className="flex max-h-[90vh] w-full max-w-lg flex-col rounded-xl border border-border bg-card shadow-xl">
        <div className="flex items-center justify-between border-b border-border px-6 py-4">
          <PlotAccessPhoneHeading>{title}</PlotAccessPhoneHeading>
          <button type="button" onClick={handleDismiss} className="text-muted-foreground hover:text-foreground">✕</button>
        </div>

        <div className="space-y-4 overflow-y-auto px-6 py-5">
          {isLoading ? (
            <div className="flex items-center justify-center py-10">
              <Loader2 className="h-5 w-5 animate-spin text-muted-foreground" />
            </div>
          ) : isError ? (
            <div className="flex flex-col items-center gap-3 py-10 text-center">
              <p className="text-sm text-destructive">โหลดข้อมูลเบอร์โทรไม่สำเร็จ</p>
              <button
                type="button"
                onClick={() => refetch()}
                disabled={isRefetching}
                className="inline-flex items-center gap-2 rounded-md border border-border bg-background px-3 py-1.5 text-xs font-medium shadow-sm hover:bg-secondary disabled:opacity-60"
              >
                {isRefetching ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <RotateCw className="h-3.5 w-3.5" />}
                ลองใหม่
              </button>
            </div>
          ) : (
            <>
              {readOnly && (
                <p className="rounded-md bg-secondary px-3 py-2 text-xs text-muted-foreground">
                  คุณไม่มีสิทธิ์แก้ไขเบอร์เข้าตรวจแปลงนี้ (แสดงผลอย่างเดียว)
                </p>
              )}
              <PlotAccessPhoneFields
                value={value}
                onChange={setValue}
                disabled={readOnly || saveM.isPending}
                autoFocusPrimary
              />
            </>
          )}
        </div>

        {/* Sticky footer — same pattern as CreatePlotModal/EditPlotModal. */}
        <div className="sticky bottom-0 -mx-0 mt-2 border-t border-border bg-card px-6 py-4">
          {saveM.isError && (
            <p className="mb-3 text-sm text-destructive">{describeAccessPhoneError(saveM.error)}</p>
          )}
          <div className="flex justify-end gap-2">
            <button type="button" onClick={handleDismiss} className="rounded-md border border-border px-4 py-2 text-sm hover:bg-secondary">
              ยกเลิก
            </button>
            {!readOnly && !isLoading && !isError && (
              <button
                type="button"
                onClick={handleSave}
                disabled={saveM.isPending || hasErrors}
                className="inline-flex items-center gap-2 rounded-md bg-primary px-4 py-2 text-sm font-medium text-primary-foreground disabled:opacity-60"
              >
                {saveM.isPending && <Loader2 className="h-4 w-4 animate-spin" />}
                บันทึก
              </button>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
