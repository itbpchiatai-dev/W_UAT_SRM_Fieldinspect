/**
 * ProtocolScoreInputs — renders the 4 inspection score inputs driven by the
 * growth-stage protocol (round 5.2), shared by RecordForm and PublicInspect.
 *
 * The labels come entirely from the protocol response — never hardcoded — so
 * "ระยะงอก" shows การเตรียมแปลง/สภาพอากาศ/การดูแลรักษา/ความต้านทานของสายพันธุ์
 * while "เจริญเติบโต" shows สภาพอากาศ/การดูแลรักษา/ความเสี่ยง/สภาพแปลง, each
 * bound (by slot, not position) to its fixed score column. A stage with no
 * protocol (or no stage yet) shows a short note instead of misleading old
 * labels — matching the backend's gated contract where those stages impose
 * no score requirement.
 */
import { ScoreButtons } from './ScoreButtons';
import type { InspectionProtocolStage } from '../../api/inspectionProtocols';

/** The 4 fixed score slots ↔ their form-state values. */
export type ScoreSlot =
  | 'fieldPrepScore'
  | 'weatherScore'
  | 'careScore'
  | 'varietyResistanceScore';

export type ProtocolScores = Record<ScoreSlot, number | null>;

const noteCls = 'rounded-md bg-gray-50 px-3 py-2 text-sm text-gray-500';

export function ProtocolScoreInputs({
  protocol,
  stageSelected,
  loading,
  loadError,
  scores,
  onChange,
  disabled,
}: {
  /** Result of findProtocolForStage — null when the selected stage has no
   * protocol (or no stage is selected; see stageSelected). */
  protocol: InspectionProtocolStage | null;
  stageSelected: boolean;
  loading?: boolean;
  loadError?: boolean;
  scores: ProtocolScores;
  onChange: (slot: ScoreSlot, value: number | null) => void;
  disabled?: boolean;
}) {
  if (loadError) {
    return <p className={`${noteCls} text-red-600`}>โหลด Protocol การตรวจไม่สำเร็จ — กรุณารีเฟรชหน้านี้</p>;
  }
  if (!stageSelected) {
    return <p className={noteCls}>เลือกระยะการเจริญเติบโตก่อน เพื่อประเมินคะแนนตามระยะ</p>;
  }
  if (loading) {
    return <p className={noteCls}>กำลังโหลด Protocol การตรวจ...</p>;
  }
  if (!protocol) {
    return <p className={noteCls}>ระยะนี้ไม่มี Protocol คะแนนเฉพาะ — ไม่ต้องให้คะแนน</p>;
  }
  return (
    <div className="space-y-4">
      {protocol.criteria.map((c) => (
        <ScoreButtons
          key={c.slot}
          label={c.label}
          value={scores[c.slot as ScoreSlot] ?? null}
          disabled={disabled}
          onChange={(v) => onChange(c.slot as ScoreSlot, v)}
        />
      ))}
    </div>
  );
}
