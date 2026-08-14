import { useState } from 'react'
import { Bot, CheckCircle2, XCircle, Lightbulb } from 'lucide-react'

const AUP_VERSION = 'v1'
const AUP_KEY = `ct_aup_accepted_${AUP_VERSION}`

const DO_LIST: readonly string[] = [
  'ใช้เครื่องมือ AI ที่บริษัทจัดหาให้เพื่อประโยชน์ในการทำงาน',
  'ใช้ AI เพื่อสนับสนุนและเพิ่มประสิทธิภาพในการทำงาน เช่น ร่างเอกสาร วิเคราะห์ข้อมูล สรุปรายงาน',
]

const DONT_LIST: readonly string[] = [
  'ห้ามนำข้อมูลระดับ Secret เข้าสู่ระบบ AI ภายนอกโดยไม่ได้รับอนุมัติ',
  'ห้ามใช้ AI ส่วนตัวกับข้อมูลระดับ Confidential โดยไม่ผ่าน AIBP ก่อน',
  'ห้ามส่งข้อมูลส่วนบุคคลของลูกค้าหรือพนักงานให้ AI โดยไม่ได้รับอนุมัติ',
  'ห้ามใช้ AI ตัดสินใจที่มีผลกระทบสูงโดยไม่มีมนุษย์ตรวจสอบ',
  'ห้ามนำผลลัพธ์จาก AI สู่ภายนอกบริษัทโดยไม่ผ่านการตรวจสอบ',
]

const TIPS: readonly string[] = [
  'ตรวจสอบผลลัพธ์จาก AI ก่อนนำไปใช้งานจริงทุกครั้ง',
  'รายงานพฤติกรรมผิดปกติของระบบ AI ต่อทีม IT ทันที',
]

export function hasAcceptedAup(): boolean {
  return !!localStorage.getItem(AUP_KEY)
}

type RuleType = 'do' | 'dont' | 'tip'

interface AupModalProps {
  onAccepted?: () => void
}

export default function AupModal({ onAccepted }: AupModalProps) {
  const [checked, setChecked] = useState(false)
  const [accepted, setAccepted] = useState(false)

  const handleConfirm = () => {
    setAccepted(true)
    localStorage.setItem(AUP_KEY, Date.now().toString())
    setTimeout(() => onAccepted?.(), 1500)
  }

  return (
    <div
      role="dialog"
      aria-labelledby="aup-title"
      aria-modal="true"
      className="fixed inset-0 flex items-center justify-center z-[9999]"
      style={{ background: 'rgba(0,0,0,0.45)' }}
    >
      <div
        className="bg-background text-foreground rounded-xl border border-border p-6 w-[90vw] max-w-[480px] max-h-[90vh] overflow-y-auto"
        style={{ boxShadow: '0 8px 32px rgba(0,0,0,0.12)' }}
      >
        {/* Header */}
        <div className="flex items-center gap-3 mb-5">
          <div className="w-9 h-9 rounded-lg bg-info/15 flex items-center justify-center shrink-0">
            <Bot className="h-5 w-5 text-chart-blue-deep" />
          </div>
          <div>
            <div id="aup-title" className="text-base font-medium leading-snug">
              นโยบายการใช้งานระบบ AI
            </div>
            <div className="text-xs text-muted-foreground">
              Acceptable Use Policy (AUP) – บริษัท เจียไต๋ จำกัด
            </div>
          </div>
        </div>

        <RuleSection label="สิ่งที่ทำได้" items={DO_LIST} type="do" />
        <RuleSection label="สิ่งที่ห้ามทำ" items={DONT_LIST} type="dont" />
        <RuleSection label="แนวปฏิบัติที่ดี" items={TIPS} type="tip" />

        {/* Footer */}
        <div className="border-t border-border pt-4 mt-5">
          {!accepted ? (
            <>
              <label className="flex items-center gap-2 cursor-pointer select-none">
                <input
                  type="checkbox"
                  checked={checked}
                  onChange={e => setChecked(e.target.checked)}
                  className="h-4 w-4 accent-primary cursor-pointer"
                />
                <span className="text-sm text-foreground">
                  ข้าพเจ้าได้อ่านและยอมรับนโยบายการใช้งาน AI
                </span>
              </label>
              <div className="mt-3 flex justify-end">
                <button
                  disabled={!checked}
                  onClick={handleConfirm}
                  aria-label="ยืนยันการยอมรับนโยบายการใช้งาน AI"
                  className="min-h-11 px-4 text-sm text-primary-foreground rounded-lg bg-primary hover:bg-primary/90 transition-opacity disabled:opacity-30"
                >
                  ยืนยัน
                </button>
              </div>
            </>
          ) : (
            <div className="text-center py-4 flex flex-col items-center gap-2">
              <CheckCircle2 className="h-7 w-7 text-success-readable" />
              <p className="text-sm text-foreground">
                บันทึกแล้ว – ขอให้ใช้งาน AI อย่างรับผิดชอบ
              </p>
            </div>
          )}
        </div>
      </div>
    </div>
  )
}

interface RuleSectionProps {
  label: string
  items: readonly string[]
  type: RuleType
}

function RuleSection({ label, items, type }: RuleSectionProps) {
  const iconMap: Record<RuleType, JSX.Element> = {
    do: <CheckCircle2 className="h-4 w-4 text-success-readable mt-0.5 shrink-0" />,
    dont: <XCircle className="h-4 w-4 text-destructive mt-0.5 shrink-0" />,
    tip: <Lightbulb className="h-4 w-4 text-warning-readable mt-0.5 shrink-0" />,
  }

  return (
    <div className="mb-4">
      <p className="text-xs uppercase tracking-wide text-muted-foreground font-semibold mb-1">
        {label}
      </p>
      {items.map((item, i) => (
        <div
          key={i}
          className={`flex items-start gap-2 text-sm py-1.5 ${
            i < items.length - 1 ? 'border-b border-border' : ''
          }`}
        >
          {iconMap[type]}
          <span>{item}</span>
        </div>
      ))}
    </div>
  )
}
