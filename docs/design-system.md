# UX/UI Design System — CT Web App Standard

> **Source of truth = `frontend/src/index.css`** (CSS variables) + `tailwind.config.ts`.
> เอกสารนี้อธิบาย *เหตุผล + กฎการใช้* — ตัวเลขจริงอยู่ในโค้ด. **รีแบรนด์: แก้
> `index.css` ที่เดียว** (ห้าม hardcode hex ใน component หรือ tailwind.config).
> ทุก token เก็บเป็น HSL triplet เพื่อให้ opacity modifier ใช้ได้ (เช่น `bg-primary/50`).
> Dark mode = class strategy (`<html class="dark">`).

---

## 1. Brand Palette

ค่าจาก executive brand guide (v3.0.4) — สี + token ที่ map ใน Tailwind:

| Token | Tailwind | Light (hex) | บทบาท |
|---|---|---|---|
| `--primary` | `bg-primary` `text-primary` | **#114B33** (CT green) | สีหลักแบรนด์ — CTA หลัก, logo, active state |
| `--accent` | `accent` | **#B29530** (CT gold) | accent แบรนด์ — outline CTA รอง, active bar, ปุ่มเด่น |
| `--accent-readable` | `text-accent-readable` | dark gold | ตัวอักษรสีทองที่อ่านผ่าน contrast |
| `--accent-warm` | `accent-warm` | #f9e6bc (ครีม) | พื้น callout / highlight คู่กับทอง |
| `--background` | `bg-background` | #f0f4f2 (เขียว-ครีมจาง) | พื้นหน้า |
| `--foreground` | `text-foreground` | #11151f (slate-900) | ตัวอักษรหลัก |
| `--card` | `bg-card` | #ffffff | พื้นการ์ด/พื้นผิวยก |
| `--secondary` | `secondary` | #e2e5e5 | แถวสลับ, fill รอง |
| `--muted-foreground` | `text-muted-foreground` | ~#575f6b | ตัวอักษรรอง/คำอธิบายที่ยังอ่านชัด |
| `--border` | `border-border` | #cfd1d6 | เส้นขอบ/เส้นคั่น |
| `--destructive` | `destructive` | red-600 | error/ลบ |
| `--success` | `success` | #439f46 | สำเร็จ |
| `--warning` | `warning` | #e28f38 | เตือน |
| `--info` | `info` | #88bfe8 | info |
| `--success-readable` / `--warning-readable` | `text-*-readable` | darker semantic colors | ข้อความ/ไอคอนสถานะบนพื้นอ่อน |
| `--chart-blue` / `-deep` | `chart-blue` | #88bfe8 / #003878 | กราฟ 2 โทน (trend คู่) |
| `--chart-1` … `--chart-8` | `chart-1`…`chart-8` | green/blue/amber/rose/purple/teal/magenta/slate | กราฟ categorical 3+ series |

**กฎ chart palette:** series ที่ 3 ขึ้นไปใช้ `--chart-1..8` **เรียงลำดับเสมอ ห้ามข้าม**
(เรียง warm/cool สลับกันช่วยผู้ใช้ตาบอดสี; ทุกค่าผ่าน WCAG 1.4.11 ≥3:1 บนพื้น
ทั้ง 2 โหมดแล้ว). กราฟที่สื่อสถานะ (ดี/แย่/เตือน) ใช้ `success`/`warning`/`destructive`
ไม่ใช่ chart palette. ทุก series ต้องมี label/legend — ห้ามสื่อความหมายด้วยสีอย่างเดียว
(WCAG 1.4.1). ห้าม hardcode hex ในกราฟ — recharts/chart lib รับ
`hsl(var(--chart-1))` ได้ตรงๆ.

**ข้อความบน solid fill:** ปุ่ม/ป้ายพื้นทึบสี gold, success, warning ใช้**ตัวอักษรเข้ม**
(token `*-foreground` จัดให้แล้ว — ขาวบนสีเหล่านี้ contrast 2.2–3.3:1 ไม่ผ่าน AA).
มีแค่ `bg-primary` (เขียวเข้ม) กับ `bg-destructive` ใน light mode ที่ใช้ตัวอักษรขาว.
ห้าม override `text-white` ทับ token เหล่านี้.

**Dark mode** มี token ชุดเดียวกันแต่ใช้ pattern แบบ Material 3 dark theme:
**fill สว่างขึ้น + ตัวอักษรเข้มบน fill** (primary/destructive/success). เหตุผล: token
สีเดียวถูกใช้ทั้งเป็นพื้นปุ่มและเป็นสีตัวอักษรบนพื้นมืด (`text-primary`) — โทนกลาง
ผ่าน AA ได้แค่บทบาทเดียว. background #0D1117, foreground #E6EDF3.
ดู block `.dark {}` ใน `index.css`.

---

## 2. Typography

Self-hosted ผ่าน `@fontsource` (bundle โดย Vite — ไม่พึ่ง CDN, offline ได้, ผ่าน
nginx CSP `font-src 'self'`). แต่ละ weight มี subset **ไทย + Latin** → ไทย render
เท่ากันทุก OS.

| บทบาท | Tailwind | Font | Weights |
|---|---|---|---|
| Headings (h1–h6) | `font-display` | **Prompt** | 500 / 600 / 700 |
| Body / UI / ตาราง / ฟอร์ม | `font-sans` (default) | **IBM Plex Sans Thai** | 400 / 500 / 600 / 700 |

- Base CSS: `body → font-sans`, `h1..h6 → font-display` (อัตโนมัติ ไม่ต้องใส่ class รายตัว;
  override ด้วย `font-sans` บน heading ได้ถ้าจำเป็น)
- import weights ใน `main.tsx`; ถ้าต้องใช้ weight ใหม่ → เพิ่ม import ที่นั่น
- **ทำไม pairing:** Prompt = เสียงแบรนด์ที่ distinctive สำหรับหัวข้อ; Plex Sans Thai =
  คมชัดตัวเล็กสำหรับ dashboard/ตาราง. (impeccable: `single-font`/`overused-font` →
  ห้ามใช้ system default ตัวเดียว)

**กฎ type (impeccable quality rules):** body/form control = 16px, `text-sm` = 15px สำหรับ UI/button/table,
`text-xs` = 13px และใช้เฉพาะ metadata/helper text, หัวข้อหน้า ≥ 21px (`text-xl` ขึ้นไป —
scaffold ใช้ `text-xl`–`text-3xl` ตามลำดับชั้นของหน้า), และ line-height 1.6–1.65 เพื่อให้ภาษาไทยอ่านสบาย;
**weight สูงสุด = 700 (`font-bold`)** — Prompt/Plex Sans Thai โหลดถึง 700 เท่านั้น; `font-extrabold`
จะได้ faux-bold ที่ตัวไทยเพี้ยน;
ข้อความรองต้องใช้ `text-muted-foreground` ที่ผ่าน contrast บนพื้นผิวปกติ; อย่าข้าม
heading level (h1→h3), อย่าใช้ letter-spacing > 0.05em บน body, อย่า all-caps ข้อความยาว.

---

## 3. Spacing, Radius, Breakpoints

- **Radius:** `--radius: 0.5rem` → `rounded-lg` (การ์ด/modal), `rounded-md` (ปุ่ม/input),
  `rounded-sm`. logo tile = `rounded-xl`.
- **Spacing:** Tailwind scale มาตรฐาน. การ์ด `p-6`–`p-8`; ฟอร์ม gap `gap-4`; ปุ่ม `px-4 py-2`.
  impeccable: หลีกเลี่ยง spacing ค่าเดียวซ้ำทั้งหน้า — จัดกลุ่มชิด, คั่นกลุ่มห่าง.
- **Breakpoints (mobile-first):** sm 640 / md 768 / lg 1024 / xl 1280 / 2xl 1536.
- **Motion:** transition 150–250ms `ease` เฉพาะ hover / focus / expand-collapse
  (`transition-colors`, `duration-200`). **ห้าม** animation ตกแต่ง (parallax,
  entrance ทีละ element, infinite loop) — ยกเว้น `animate-spin` ของ loading.

---

## 4. Component Patterns + States

ดึงจาก component จริงใน scaffold — ใช้เป็น reference ตอนสร้างหน้าใหม่:

| Component | คลาสหลัก | หมายเหตุ |
|---|---|---|
| **Button — primary** | `bg-primary text-primary-foreground rounded-md px-4 py-2.5 font-semibold hover:bg-primary/90` | CTA หลัก (เขียวทึบ) |
| **Button — secondary/outline** | `border-2 border-accent text-accent-readable bg-transparent hover:bg-accent-warm` | CTA รอง (ขอบทอง) — ใช้สีทองเข้มสำหรับข้อความ |
| **Button — icon/ghost** | `bg-card/80 text-foreground hover:bg-secondary rounded-md` | toggle ภาษา/ธีม |
| **Card** | `rounded-lg border border-border bg-card p-6 shadow-sm` | ❌ ห้าม `border-t-4`/`border-l-4` ทองบนการ์ด (ดู §5) |
| **Input** | `rounded-md border border-input bg-background px-3 py-2 focus:ring-2 focus:ring-ring` | focus ring = เขียว |
| **Modal** | `max-h-[90vh] overflow-y-auto rounded-lg border border-border bg-card p-6 shadow-lg` | ต้องมี `max-h` + `overflow-y-auto` (กันปุ่ม Save หลุดจอ — Bug 10) |
| **Alert/error** | `rounded-md border border-destructive/40 bg-destructive/10 text-destructive` | สี tinted (ไม่ใช่ gray-on-color) |
| **Sidebar item — active** | `border-l-4 border-accent bg-primary/10 text-primary` | ✅ active-bar ทอง = affordance ปกติ |
| **Page brand bar** | `h-1 bg-primary` | แถบแบรนด์บนหน้า public (ทึบ ไม่ใช่ gradient) |
| **Logo mark** | `rounded-xl bg-primary text-primary-foreground font-bold` "CT" | icon tile เดียวที่อนุญาต = brand mark (700 = weight สูงสุดที่โหลด) |

**States:** hover (`hover:bg-*/90` หรือ `bg-secondary`), focus (`focus:ring-2 focus:ring-ring`),
disabled (`disabled:opacity-60`), loading (`<Loader2 className="animate-spin" />`).

---

## 5. Brand Usage — Do / Don't  (governance)

กฎนี้คือสิ่งที่ทำให้ output ไม่ดูเป็น "AI slop" — มาจากการ audit ด้วย **impeccable**
(pbakaus/impeccable). หลัก: **impeccable เป็นที่ปรึกษา; เมื่อขัดกับ CT brand → brand ชนะ.**

**สีทอง (accent) — DO:**
- logo mark, ปุ่ม CTA รอง (ขอบทอง), active sidebar bar, ปุ่มเด่นเฉพาะจุด

**สีทอง — DON'T:**
- ❌ เส้นทองหนาบน/ข้างการ์ด (`border-t-4`/`border-l-4 border-accent` บน card) =
  impeccable `side-tab` / `border-accent-on-rounded` — "AI-tell อันดับ 1"

**ทั่วไป — DON'T (impeccable anti-patterns ที่เรายึด):**
- ❌ gradient text / decorative gradient (ใช้แถบสีทึบแทน)
- ❌ nested cards (การ์ดซ้อนการ์ด) — ใช้ spacing/typography/divider แทน
- ❌ icon-tile-stack ซ้ำๆ (กล่องไอคอนเหนือหัวข้อ) — ยกเว้น CT logo mark
- ❌ dark-mode glow (เงาสีเรือง), system-default fonts, ฟอนต์ overused
- ❌ gray-on-color, contrast ต่ำกว่า WCAG AA

**วิธีใช้ impeccable (authoring-time เท่านั้น):** ลงเป็น personal skill ของ Claude Code
แล้วรัน `/impeccable audit` / `/impeccable critique` กับ diff → รับเฉพาะที่ไม่ขัด brand →
ผล bake ลง `scaffold.py`. **คนรับ standard ไม่ต้องลง impeccable.**

---

## 6. Accessibility (เป้า WCAG 2.1 AA)

- Contrast: body ≥ 4.5:1, large text ≥ 3:1 (token ออกแบบให้ผ่านแล้ว — ตรวจซ้ำเมื่อเพิ่มสี)
- Focus visible: ทุก interactive element มี `focus:ring-2 focus:ring-ring`
- Heading hierarchy ไม่ข้ามระดับ (screen reader ใช้ navigate)
- ปุ่ม/ไอคอนล้วนมี `aria-label` + `title`
- i18n: ทุกข้อความผ่าน `useTranslation` (th/en) — ไม่ hardcode (ดู frontend.md §5)

---

## 7. ไฟล์ที่เกี่ยวข้อง

| สิ่งที่ต้องแก้ | ไฟล์ |
|---|---|
| สี / token / dark mode | `frontend/src/index.css` (**ที่เดียว**) |
| font family mapping | `frontend/tailwind.config.ts` (`fontFamily`) |
| โหลด font weight | `frontend/src/main.tsx` (`@fontsource/*` imports) |
| Frontend architecture | [`frontend.md`](frontend.md) |
| ประวัติการเปลี่ยนดีไซน์ | [`handover-2026-06-01.md`](handover-2026-06-01.md), `PROGRESS.md` |

---

## 8. หน้าใหม่ — เราคุม "สี/ฟอนต์/กรอบ" ไม่คุม "การออกแบบ"

**เราไม่ lock ว่าหน้าใหม่ต้องเป็น layout อะไร** — form, chart, wizard, detail view,
kanban อะไรก็ได้ตามที่งานต้องการ. สิ่งที่ standard **คุม** คือ *visual contract*
เท่านั้น เพื่อให้ทุกหน้าดู "เป็น CT ชุดเดียวกัน" ไม่ว่า layout จะเป็นแบบไหน:

| คุม (fixed — ห้ามออกนอกกรอบ) | อิสระ (ของผู้สร้างหน้า) |
|---|---|
| **สี** = semantic token เท่านั้น (`bg-primary`, `text-foreground`, `bg-accent`…) — ห้าม hex ดิบ / ห้ามสี Tailwind ดิบ (`bg-blue-500`) | layout, จำนวน section, component ที่ใช้ |
| **ฟอนต์** = `font-display`/`font-sans` (มาจาก base CSS อัตโนมัติ) | เนื้อหา, ลำดับการเล่าเรื่อง, interaction |
| **radius/shadow/spacing scale** = `rounded-lg`/`shadow-sm`/Tailwind scale | จะมีตาราง/กราฟ/การ์ด/ฟอร์ม กี่อันก็ได้ |
| **กฎ do/don't** (§5) — ห้าม AI-tell (side-tab, gradient, gray-on-color…) | สไตล์การจัดวางภายในกรอบนั้น |

**ทำไมไม่ต้อง "ติดตั้ง" อะไรเพิ่ม:** สี/ฟอนต์ถูกบังคับที่ **foundation** อยู่แล้ว —
`index.css` (tokens) + `tailwind.config.ts` (font/สี map) + base CSS (h1–h6 → font-display).
ตราบใดที่หน้าใหม่ใช้ **semantic class** หน้าจะ on-brand เองทันที **ไม่ว่าจะออกแบบ layout
แบบไหน**. เราไม่ได้ออกแบบหน้าให้เขา — เราแค่การันตีว่า "เขียว/ทอง/ฟอนต์/contrast" ตรง brand.

**บังคับจริง (ไม่ใช่แค่กฎ):** pre-commit hook `no-raw-colors`
(`scripts/checks/no_raw_colors.py`) สแกน `frontend/src/**/*.tsx` — ถ้าเจอสี Tailwind
ดิบ (`bg-blue-500`) หรือ hex (`text-[#ff0000]`) จะ **เตือน + บล็อก commit**. ถ้าตั้งใจ
ใช้สีนั้นจริง (เช่น โลโก้ vendor) → เติมคอมเมนต์ `brand-allow` บนบรรทัดนั้น = ยืนยัน แล้ว
commit ผ่าน. (token เช่น `bg-primary`/`bg-accent` ไม่โดน — มันคือสี brand อยู่แล้ว)

**Token ไม่พอ?** ห้ามแก้เฉพาะหน้าในโปรเจกต์ — ใช้ **Extension Path** (AGENTS.md §1):
บันทึก retro → เสนอเพิ่มที่ standard repo → ปล่อยเวอร์ชันใหม่ให้ทุกโปรเจกต์พร้อมกัน.

> **Optional — ตัวอย่างโครง (ไม่ใช่ข้อบังคับ):** ถ้าอยากได้จุดเริ่ม ก๊อปอันนี้แล้ว
> ดัดแปลงได้เต็มที่ — มันแค่โชว์ว่า "ใช้ token ยังไงให้อยู่ในกรอบ" ไม่ใช่ layout ที่ต้องตาม:

```tsx
import { useTranslation } from 'react-i18next';
import { Plus } from 'lucide-react';

export function MyPage() {
  const { t } = useTranslation();
  const rows: MyRow[] = useMyData();           // React Query (ดู frontend.md §7)

  return (
    <div className="container mx-auto px-4 py-8 sm:px-6 lg:px-8">
      {/* PageHeader — h1 (font-display อัตโนมัติ) + ปุ่ม action */}
      <header className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
        <div>
          <h1 className="text-2xl font-bold sm:text-3xl">{t('myPage.title')}</h1>
          <p className="mt-1 text-sm text-muted-foreground">{t('myPage.subtitle')}</p>
        </div>
        <button type="button"
          className="inline-flex items-center gap-2 rounded-md bg-primary px-4 py-2 text-sm font-semibold text-primary-foreground shadow-sm hover:bg-primary/90 focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2">
          <Plus className="h-4 w-4" /> {t('myPage.add')}
        </button>
      </header>

      {/* Card + Table — px-4 py-2 ต่อ cell (≥8px), header bg-secondary */}
      <section className="mt-8 rounded-lg border border-border bg-card text-card-foreground shadow-sm">
        {rows.length === 0 ? (
          <p className="p-8 text-center text-sm text-muted-foreground">{t('myPage.empty')}</p>
        ) : (
          <table className="w-full text-sm">
            <thead className="border-b border-border bg-secondary/50 text-left">
              <tr><th className="px-4 py-2 font-medium">{t('myPage.col.name')}</th></tr>
            </thead>
            <tbody>
              {rows.map((r) => (
                <tr key={r.id} className="border-b border-border last:border-0 hover:bg-secondary/40">
                  <td className="px-4 py-2">{r.name}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </section>
    </div>
  );
}
```

### Checklist ก่อน merge หน้าใหม่ (DoD ด้านดีไซน์)
- [ ] ใช้ **token เท่านั้น** (`bg-card`/`text-foreground`/`border-border`…) — ห้าม hex ดิบ
- [ ] การ์ด = `rounded-lg border border-border bg-card shadow-sm` — **ห้าม** `border-t-4 border-accent`
- [ ] heading = `<h1>/<h2>` (ได้ font-display เอง) ไม่ข้ามระดับ; body/form = 16px, UI/table ≥ 15px, metadata ≥ 13px
- [ ] interactive ทุกตัวมี `focus-visible:ring-2 focus-visible:ring-ring`
- [ ] list มี **empty state** (`rows.length === 0`)
- [ ] table cell `px-4 py-2`; ไม่มี gray-on-color; ไม่มี nested card
- [ ] ทุกข้อความผ่าน `t()` (i18n th/en) — ไม่ hardcode
- [ ] กราฟ: 3+ series ใช้ `chart-1..8` เรียงลำดับ + มี legend/label (ไม่สื่อด้วยสีอย่างเดียว)
- [ ] responsive: เช็คที่ 375px (mobile-first; ดู AGENTS.md §8 DoD)

> อยากตรวจให้ลึกกว่านี้ → ลง `pbakaus/impeccable` เป็น personal skill แล้วรัน
> `/impeccable audit` กับหน้านั้น (optional — recipe นี้ครอบ rule หลักไว้แล้ว)
