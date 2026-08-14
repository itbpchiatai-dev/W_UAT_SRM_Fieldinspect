/**
 * CompactScores — the neutral, label-less rendering of a record's 4
 * inspection scores (round 5.4). Used wherever many records/plots are shown
 * at once (RecordList, PlotStatusReport, PlotDetail history glance): those
 * lists carry no per-record protocol snapshot, and each record's growth
 * stage can remap what a slot means, so attaching fixed criterion labels
 * there would be misleading. The bare numbers (orange-toned when low ≤ 3)
 * still answer "any weak scores?" at a glance; the labelled breakdown lives
 * in the record's preview/detail, which reads the snapshot.
 *
 * `scores` is the 4 values in the fixed slot order
 * [fieldPrep, weather, care, varietyResistance]; null renders as "—".
 */
export function CompactScores({ scores }: { scores: (number | null)[] }) {
  return (
    <span className="inline-flex flex-wrap items-center gap-1" aria-label="คะแนนการตรวจ 4 หัวข้อ">
      {scores.map((value, i) => {
        if (value == null) {
          return <span key={i} className="text-xs text-gray-300">—</span>;
        }
        const tone = value <= 3 ? 'bg-orange-50 text-orange-700' : 'bg-green-50 text-green-700';
        return (
          <span
            key={i}
            className={`inline-flex min-w-8 justify-center rounded-full px-2 py-0.5 text-xs font-medium ${tone}`}
          >
            {value}
          </span>
        );
      })}
    </span>
  );
}
