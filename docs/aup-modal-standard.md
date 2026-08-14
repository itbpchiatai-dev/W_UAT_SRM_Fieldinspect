# AUP Modal — มาตรฐานนโยบายการใช้งาน AI

> **ประเภท:** CT Global Standard (ใช้ร่วมกันทุก internal AI app)
> **สถานะ:** บังคับใช้
> **AUP เวอร์ชันปัจจุบัน:** v1
> **อัปเดตล่าสุด:** 2026-05-22

---

## 1. ภาพรวม

ทุก internal AI app ของ Chia Tai ต้องแสดง popup AUP (Acceptable Use Policy)
ให้ผู้ใช้อ่านและกดยอมรับก่อนเข้าใช้งานระบบ — เป็นข้อกำหนด CT compliance
ที่กำหนดให้ user รับรู้และยอมรับนโยบายก่อนเข้าถึง AI system

---

## 2. ไฟล์เดียวที่ต้องมี

`frontend/src/components/AupModal.tsx`

- Reference implementation: [`docs/AupModal.tsx`](AupModal.tsx) — scaffold copy อัตโนมัติตอน setup
- `export default AupModal` — `<AupModal onAccepted={callback} />`
- `export hasAcceptedAup(): boolean` — อ่าน flag จาก localStorage
- บันทึก flag ที่ key `ct_aup_accepted_{AUP_VERSION}` (เช่น `ct_aup_accepted_v1`)

---

## 3. Integration Pattern (App.tsx)

```tsx
import { useState } from 'react'
import AupModal, { hasAcceptedAup } from './components/AupModal'

const [aupAccepted, setAupAccepted] = useState<boolean>(hasAcceptedAup)

// แสดงเฉพาะเมื่อ login แล้ว + ยังไม่ยอมรับ
{user && !aupAccepted && <AupModal onAccepted={() => setAupAccepted(true)} />}
```

---

## 4. กฎ Versioning (สำคัญที่สุด)

เมื่อแก้เนื้อหานโยบาย → **ต้องเปลี่ยน `AUP_VERSION`** (`v1` → `v2` → ...)

เพราะ localStorage key ผูกกับเวอร์ชัน ถ้าไม่เปลี่ยน user เก่าจะไม่เห็น modal ใหม่เลย
ทั้งที่นโยบายเปลี่ยนไปแล้ว

---

## 5. กฎสี — ใช้ CSS variable เท่านั้น (ห้าม hardcode)

| ส่วน | ต้องใช้ |
|------|---------|
| ปุ่ม "ยืนยัน" (bg) | `bg-primary hover:bg-primary/90 text-primary-foreground` |
| Checkbox accent | `accent-primary` |
| Icon container (header) | `bg-info/15` + icon `text-chart-blue-deep` |
| Icon "ทำได้" / success | `text-success-readable` |
| Icon "ห้ามทำ" | `text-destructive` |
| Icon "แนวปฏิบัติ" | `text-warning-readable` |
| พื้น modal | `bg-background` |
| เส้นขอบ modal | `border-border` |
| เส้นคั่นภายใน | `border-border` |
| ข้อความหลัก | `text-foreground` |
| ข้อความรอง (checkbox) | `text-foreground` |
| คำบรรยาย / label | `text-muted-foreground` |

> **ผลพลอยได้:** เมื่อใช้ CSS variable แล้ว dark mode ทำงานเองทันที
> เพราะ `.dark` ใน `index.css` override ตัวแปรเหล่านี้อยู่แล้ว

---

## 6. เนื้อหานโยบายมาตรฐาน (CT AUP v1)

### Header
- **หัวข้อ:** `นโยบายการใช้งานระบบ AI`
- **คำบรรยาย:** `Acceptable Use Policy (AUP) – บริษัท เจียไต๋ จำกัด`

### สิ่งที่ทำได้ (DO_LIST)
1. ใช้เครื่องมือ AI ที่บริษัทจัดหาให้เพื่อประโยชน์ในการทำงาน
2. ใช้ AI เพื่อสนับสนุนและเพิ่มประสิทธิภาพในการทำงาน เช่น ร่างเอกสาร วิเคราะห์ข้อมูล สรุปรายงาน

### สิ่งที่ห้ามทำ (DONT_LIST)
1. ห้ามนำข้อมูลระดับ Secret เข้าสู่ระบบ AI ภายนอกโดยไม่ได้รับอนุมัติ
2. ห้ามใช้ AI ส่วนตัวกับข้อมูลระดับ Confidential โดยไม่ผ่าน AIBP ก่อน
3. ห้ามส่งข้อมูลส่วนบุคคลของลูกค้าหรือพนักงานให้ AI โดยไม่ได้รับอนุมัติ
4. ห้ามใช้ AI ตัดสินใจที่มีผลกระทบสูงโดยไม่มีมนุษย์ตรวจสอบ
5. ห้ามนำผลลัพธ์จาก AI สู่ภายนอกบริษัทโดยไม่ผ่านการตรวจสอบ

### แนวปฏิบัติที่ดี (TIPS)
1. ตรวจสอบผลลัพธ์จาก AI ก่อนนำไปใช้งานจริงทุกครั้ง
2. รายงานพฤติกรรมผิดปกติของระบบ AI ต่อทีม IT ทันที

### ส่วนยอมรับ (Footer)
- **Checkbox:** `ข้าพเจ้าได้อ่านและยอมรับนโยบายการใช้งาน AI`
- **ปุ่ม:** `ยืนยัน` — `disabled` จนกว่าจะติ๊ก checkbox
- **ข้อความสำเร็จ:** `บันทึกแล้ว – ขอให้ใช้งาน AI อย่างรับผิดชอบ` (แสดง 1.5 วิ แล้วปิด)

---

## 7. Visual Spec

- Overlay: `rgba(0,0,0,0.45)`, `z-index 9999`
- Modal: `bg-[var(--background)]`, `rounded-xl`, `max-width 480px`, `max-height 90vh` + scroll
- 3 ส่วน: ทำได้ (icon `--success`) / ห้ามทำ (icon `--danger`) / แนวปฏิบัติ (icon `--warning`)
- Icon: Lucide React (`Bot`, `CheckCircle2`, `XCircle`, `Lightbulb`) — ห้าม emoji
- มี `role="dialog"` + `aria-modal` + `aria-labelledby` (a11y)

---

## 8. โฟลว์การทำงาน

1. user login → เช็ค `hasAcceptedAup()`
2. ยังไม่ยอมรับ → แสดง modal บังคับ (ไม่มีปุ่มปิด / ไม่มี backdrop dismiss)
3. ติ๊ก checkbox → ปุ่ม "ยืนยัน" เปิดใช้งาน
4. กดยืนยัน → บันทึก timestamp ลง localStorage → แสดงข้อความสำเร็จ → callback `onAccepted`

---

## 9. ตารางแก้สี (เดิม → ใหม่)

ใช้อ้างอิงเมื่อ migrate component เดิมที่ยัง hardcode สีอยู่

| เดิม (hardcode) | ใหม่ (semantic token) |
|---|---|
| `style={{ background: '#114B33' }}` | `bg-primary hover:bg-primary/90` |
| `accent-[#114B33]` | `accent-primary` |
| `text-blue-500` / `bg-blue-50` | `text-chart-blue-deep` / `bg-info/15` |
| `text-green-500` | `text-success-readable` |
| `text-red-500` | `text-destructive` |
| `text-yellow-500` | `text-warning-readable` |
| `text-gray-400` / `text-gray-600` | `text-muted-foreground` |
| `text-gray-700` | `text-foreground` |
| `border-gray-100` / `border-gray-200` | `border-border` |
| `bg-white` | `bg-background` |

---

*CT Dev Standard · Chia Tai Co., Ltd*
