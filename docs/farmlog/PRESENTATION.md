# SRM_FieldInspect (FarmLog) — Presentation Content Pack

> เอกสารข้อมูลสำหรับ Gen รายงาน/สไลด์นำเสนอโปรเจค — ผู้ฟังกลุ่มผสม (ผู้บริหาร + ทีมเทคนิค)
> ข้อเท็จจริง/ตัวเลขทั้งหมดอ้างจากโค้ดจริง ณ วันที่จัดทำ (2026-07-03)
> โครงสร้าง: **ส่วน A = ข้อมูลดิบ** (ป้อนเครื่องมือ Gen ได้เลย) / **ส่วน B = ลำดับสไลด์นำเสนอ**

---

# ส่วน A — ข้อมูลโปรเจค (Content Pack)

## A1. ข้อมูลโปรเจคโดยสรุป

| หัวข้อ | รายละเอียด |
|---|---|
| ชื่อระบบ | **SRM_FieldInspect** — โมดูลหลักชื่อ **FarmLog** |
| ประเภท | Web Application (รองรับมือถือ — ฟอร์มภาคสนามออกแบบเป็น mobile-first) |
| สร้างบน | CT Web App Standard v3.0 (scaffold มาตรฐานภายใน: auth, RBAC, menus, logging) |
| ภาษา UI | ไทย |

**ปัญหาที่ระบบแก้:**
องค์กรรับซื้อผลผลิตจาก supplier จำนวนมาก แต่ละ supplier มีแปลงเพาะปลูกหลายแปลง การตรวจสภาพแปลง (พืชที่ปลูก ระยะการเจริญเติบโต สภาพแปลง คาดการณ์ผลผลิต) เดิมกระจัดกระจาย ไม่เป็นระบบ ไม่มีข้อมูลกลางให้ผู้บริหารติดตาม — FarmLog ทำให้การตรวจแปลงภาคสนาม **บันทึกเป็นดิจิทัล มีรูปถ่าย+GPS ยืนยัน สถานะแปลงอัปเดตอัตโนมัติ และดูรายงานรวมได้ทันที**

**คุณค่าหลัก (Value Proposition):**
1. เจ้าหน้าที่ภาคสนามบันทึกการตรวจจากมือถือได้ในไม่กี่นาที — สแกน QR หน้าแปลง ฟอร์มแบบ tap-first (ปุ่มกด/slider แทนการพิมพ์)
2. คนนอกที่ไม่มี account (ผู้ช่วยเกษตรกร) ก็ส่งบันทึกได้อย่างปลอดภัยผ่าน QR + รหัสเข้าตรวจ
3. แต่ละแปลงมี "สถานะล่าสุด" เสมอ — พืชที่ปลูก, % ผลผลิตคาดการณ์เทียบแผน, คะแนนสภาพแปลง, ตรวจล่าสุดเมื่อไหร่/โดยใคร
4. ผู้บริหารเห็น Dashboard + รายงานสถานะแปลงทุก supplier พร้อม export Excel
5. ความปลอดภัยระดับฐานข้อมูล (Postgres Row-Level Security) — supplier เห็นเฉพาะข้อมูลของตัวเอง

## A2. กลุ่มผู้ใช้ 4 กลุ่ม

| กลุ่ม | ใครบ้าง | ทำอะไรในระบบ |
|---|---|---|
| **ผู้ดูแล/หัวหน้างาน** | Admin, Supervisor | ตั้งต้นข้อมูล supplier/แปลง, พิมพ์ป้าย QR, จัดการ master data, มอบหมายแปลง, ดูรายงาน |
| **เจ้าหน้าที่ภาคสนาม** | Field Officer (login) | สแกน QR/เลือกแปลง → บันทึกการตรวจ (คะแนน, yield, GPS, รูป 4 มุม) |
| **Supplier** | เจ้าของสวน/พนักงาน | ดูแปลงและผลตรวจของตัวเอง; **เจ้าของ (supplier:owner) สร้าง/แก้ไขแปลงของตัวเองได้** (self-service) |
| **ผู้ช่วยภาคสนามภายนอก** | ไม่มี account | สแกน QR ป้ายหน้าแปลง + กรอกรหัสเข้าตรวจ → ส่งบันทึกการตรวจได้ 1 รายการต่อ session (30 นาที) โดยไม่ต้อง login |

## A3. Scope ระบบ (โมดูลที่มีจริงวันนี้)

**FarmLog (โดเมนหลัก):**
- **Suppliers** — ทะเบียน supplier + รหัสเข้าตรวจระดับ supplier
- **Plots (แปลง)** — ทะเบียนแปลงต่อ supplier: ที่ตั้ง (หมู่บ้าน/อำเภอ/จังหวัด/พิกัด/ไร่), **ข้อมูลรอบปลูก** (ชนิดพืช พันธุ์ Lot No. วันที่ปลูก), **แผนผลผลิต** (จำนวนต้น, เป้า yield 100%, หน่วย), มอบหมายผู้รับผิดชอบ, **ป้าย QR ต่อแปลง** (พิมพ์รายแปลง/ทั้ง supplier), Excel import template, หน้า Plot Detail แสดงสถานะล่าสุด + ประวัติการตรวจทุกรอบ
- **Records (บันทึกการตรวจ)** — วันที่ตรวจ, ผู้กรอก, ระยะการเจริญเติบโต, สภาพอากาศ, **Yield % (0–150)**, **คะแนนสภาพแปลง 4 ด้าน (1–10)**: การเตรียมแปลง/สภาพอากาศ/การดูแล/ความต้านทานพันธุ์, พิกัด GPS ขณะตรวจ (บังคับ), **รูปถ่าย 4 มุมบังคับ**, คำแนะนำ/หมายเหตุ, custom fields; ทุกบันทึกใหม่ sync "สถานะล่าสุด" ขึ้นตัวแปลงอัตโนมัติ
- **Public QR Inspect** (`/public/inspect`) — flow คนนอกไม่ต้อง login: สแกน QR → กรอกรหัสเข้าตรวจ → เห็นข้อมูลแปลงแบบอ่านอย่างเดียว → กรอกเฉพาะข้อมูลการตรวจจริง → แนบรูป 4 รูป → ส่ง
- **Master Data** — จัดการตัวเลือก dropdown (ชนิดพืช/พันธุ์แบบผูกลำดับชั้น, ระยะการเจริญเติบโต, สภาพอากาศ, จังหวัด 77 จังหวัด)
- **Dynamic Fields** — ผู้ดูแลเพิ่มฟิลด์ใหม่ให้ฟอร์มตรวจได้เองโดยไม่ต้องแก้โค้ด (schema-driven form)
- **Dashboard** — KPI: จำนวนบันทึก/เดือนนี้, คะแนนเฉลี่ย, แปลงคะแนนต่ำ (≤3) ที่ต้องดูแล, จำนวนแปลง/supplier, Top พืช
- **รายงาน** — "สถานะแปลง": ทุกแปลง + สถานะการตรวจล่าสุด กรองตาม supplier/จังหวัด/พืช/สถานะการตรวจ/ช่วงวันที่ + **export Excel (.xlsx หัวตารางไทย)**

**ระบบพื้นฐาน (จาก Standard v3.0):**
- Users / Roles / Permissions / Menus (จัดการเมนู+สิทธิ์แบบ dynamic), การอนุมัติผู้ใช้ใหม่, per-user permission override
- Login 2 ทาง: รหัสผ่าน local + **Azure AD SSO** (เปิด/ปิดได้จากหน้า settings)
- System Logs / Activity Logs (audit) พร้อม retention อัตโนมัติ
- (Optional, ปิดอยู่โดย flag) Database Connections + Query Sandbox

**นอก Scope ปัจจุบัน / แผนต่อ (ROADMAP Phase F):**
- Offline sync (บันทึกโดยไม่มีสัญญาณ), แผนที่/PostGIS, การแจ้งเตือน (notifications), API key/webhook สำหรับระบบภายนอก, ระบบอนุมัติบันทึก (approval workflow), การปรับ performance ระดับ scale ใหญ่

## A4. บทบาทและสิทธิ์ (RBAC)

ระบบสิทธิ์: **43 permissions** (+3 optional), **10 roles** ที่ seed มาตรฐาน — เมนูซ่อน/แสดงตามสิทธิ์อัตโนมัติ

| Role | ขอบเขตข้อมูล (RLS scope) | สิทธิ์หลัก |
|---|---|---|
| internal:super_admin | ทุกอย่าง | ทุก permission |
| internal:admin | ทั้งหมด | จัดการ users/roles, suppliers/plots/records ครบ CRUD |
| farmlog:supervisor | ทั้งหมด | ดูทุกแปลง, มอบหมายแปลง, บันทึก/แก้การตรวจ, จัดการ master data |
| farmlog:field_officer | เฉพาะแปลงที่ถูกมอบหมาย | ดูแปลง+บันทึกการตรวจของแปลงตน |
| **supplier:owner** | เฉพาะ supplier ตัวเอง | ดูข้อมูลตัวเอง + **สร้าง/แก้ไขแปลงของตัวเอง** (self-service — เพิ่มล่าสุด) |
| supplier:staff | เฉพาะแปลงที่ถูกมอบหมาย | ดูบันทึกการตรวจ |
| internal:super_user / user, external:admin / user | — | บทบาทระบบพื้นฐาน (จัดการเมนู / ผู้ใช้ภายนอก) |

## A5. การทำงานของระบบ — 4 Workflows หลัก

### Flow 1: ตั้งต้นข้อมูล (Admin/Supervisor)
1. สร้าง Supplier → กำหนดรหัสเข้าตรวจ (inspection code) ระดับ supplier
2. สร้างแปลง (ทีละแปลง หรือ import จาก Excel template) → ระบุพืชที่ปลูก พันธุ์ Lot วันที่ปลูก + แผนผลผลิต (จำนวนต้น/เป้า yield)
3. มอบหมายเจ้าหน้าที่ประจำแปลง
4. **พิมพ์ป้าย QR** (รายแปลง หรือทั้ง supplier ในคลิกเดียว) → นำไปติดหน้าแปลงจริง — QR เข้ารหัสเป็น key ลับเดารหัสแปลงไม่ได้

### Flow 2: เจ้าหน้าที่บันทึกการตรวจ (login)
1. เปิด "บันทึกการตรวจแปลงใหม่" จากมือถือ → **สแกน QR หน้าแปลง** (auto เลือก supplier+แปลงให้) หรือเลือกจาก Smart Plot Picker
2. ระบบแสดงข้อมูลแปลงอ่านอย่างเดียว (พืช/พันธุ์/Lot/วันที่ปลูก — แก้ไม่ได้ กันข้อมูล master เพี้ยน)
3. กรอกเฉพาะข้อมูลการตรวจ: ระยะการเจริญเติบโต, สภาพอากาศ (ปุ่มกด), Yield % (slider), คะแนน 4 ด้าน (slider), GPS จับอัตโนมัติ, **ถ่ายรูป 4 มุมบังคับ**
4. บันทึก → สถานะล่าสุดของแปลงอัปเดตทันที (yield, คะแนน, ตรวจล่าสุดเมื่อ/โดยใคร)

### Flow 3: ผู้ช่วยภายนอกส่งบันทึกผ่าน QR (ไม่ต้อง login)
1. สแกน QR ป้ายหน้าแปลงด้วยมือถือ → เปิดหน้า `/public/inspect` อัตโนมัติ
2. กรอก **รหัสเข้าตรวจ** ที่ได้รับจาก supplier (rate-limit 10 ครั้ง/นาที กันเดารหัส) → ได้ session 30 นาทีเฉพาะแปลงนั้น
3. เห็นข้อมูลแปลงอ่านอย่างเดียว → กรอกข้อมูลการตรวจ + รูป 4 รูป → ส่ง
4. ระบบบันทึกในนามผู้ช่วยภายนอก (ระบุรหัส/ชื่อผู้กรอก) — เลือกแปลง/supplier เองไม่ได้ ทุกอย่าง server กำหนดจาก session

### Flow 4: ผู้บริหาร/หัวหน้าติดตามผล
1. Dashboard: KPI รวม + แปลงคะแนนต่ำที่ต้องดูแล + Top พืช
2. รายงาน "สถานะแปลง": ทุกแปลง กรอง supplier/จังหวัด/พืช/ยังไม่ถูกตรวจ/ช่วงวันที่
3. คลิกเข้าแปลง → Plot Detail: สถานะล่าสุด + แผนผลผลิต + ประวัติการตรวจทุกรอบพร้อมรูป
4. Export Excel ส่งต่อ/วิเคราะห์ภายนอก

## A6. จุดเด่นด้านเทคนิค/ความปลอดภัย (สำหรับสายเทคนิค)

1. **Postgres Row-Level Security (RLS)** — การแบ่งเขตข้อมูล (supplier เห็นแค่ของตัวเอง, field officer เห็นแค่แปลงที่มอบหมาย) บังคับที่ระดับฐานข้อมูล ไม่ใช่แค่ระดับแอป — ต่อให้โค้ดชั้นบนพลาด ข้อมูลก็ไม่รั่วข้าม supplier
2. **RBAC 43 permissions** + per-user override + เมนู dynamic ตามสิทธิ์ — เพิ่ม role ใหม่ไม่ต้องแก้โค้ด
3. **Public QR flow แบบ 2 ชั้น** — QR พิมพ์เป็น opaque key (สุ่ม ~192 bits เดา/enumerate ไม่ได้ ไม่เผยรหัส supplier/แปลง) + รหัสเข้าตรวจเป็น bcrypt hash; ตอบ 404 แบบ generic กันการไล่สแกนหาแปลง; session token อายุ 30 นาที ผูกกับแปลงเดียว
4. **Payload hardening** — client ฝั่ง public ส่ง plot_id/supplier_id/ชนิดพืช/พันธุ์เองไม่ได้ (server กำหนดจากข้อมูลที่ verify แล้วเท่านั้น ส่งมา = 422)
5. **Photo upload validation** — จำกัด 4 รูป × 5MB, ตรวจ magic bytes จริง (ไม่เชื่อ Content-Type/นามสกุลไฟล์), ชื่อไฟล์ถูก generate ใหม่, ดาวน์โหลดรูปผ่าน endpoint ที่เช็ค RLS ทุกครั้ง (ไม่มี public static)
6. **Rate limiting** (slowapi) บน login/SSO/public endpoints พร้อมการอ่าน X-Forwarded-For อย่างปลอดภัย (trusted proxy CIDR)
7. **Audit ครบวงจร** — activity logs (ทุก mutation), system logs, AI call logs + retention scheduler + PII masking
8. **Auth 2 ทาง** — JWT (local password + TOTP-ready) และ Azure AD SSO เปิด/ปิดจากหน้า settings; token revocation
9. **Supply-chain security ใน CI** — gitleaks/detect-secrets (secret scan), pip-audit/npm audit, Bandit, Trivy; dependency pins เหนือ CVE ที่รู้จัก

## A7. Tech Stack + ตัวเลขโครงการ

**Backend:** Python 3.12+ / FastAPI / SQLAlchemy 2.0 (async) / PostgreSQL / Alembic / Pydantic v2 / slowapi / structlog / APScheduler / MSAL (Azure AD)
**Frontend:** React 18 / Vite 5 / TypeScript 5 / TailwindCSS / TanStack React Query / react-hook-form + zod / zustand / html5-qrcode + qrcode.react / i18n ไทย
**Deployment:** Docker (backend/frontend Dockerfile + docker-compose), GitHub Actions CI (test + security scanning)

| ตัวเลข ณ 2026-07-03 | ค่า |
|---|---|
| Database migrations | 29 versions |
| Backend tests | 326 ผ่านทั้งหมด (รวม security wiring tests) |
| Frontend tests | 161 ผ่านทั้งหมด (16 ไฟล์ component tests) |
| Permissions / Roles | 43 (+3 optional) / 10 roles |
| API routers | Auth, Users, Roles, Permissions, Menus, Settings, Logs, Dashboard, Suppliers, Plots, Records, FieldDefs, MasterData, Reports, Public (3 ตัว) |

## A8. สถานะโครงการ (Phase A–F)

| Phase | เนื้อหา | สถานะ |
|---|---|---|
| A — Foundation | Scaffold, DB baseline, Auth JWT | ✅ เสร็จ |
| B — Fixed CRUD | Suppliers, Users↔Supplier, Plots, Records | ✅ เสร็จ |
| C — Security | RBAC เต็มรูปแบบ, Postgres RLS | ✅ เสร็จ |
| D — UX | Dashboard, Record list + One-Page Preview, ฟอร์มมือถือ + Smart Plot Picker + GPS + รูป | ✅ เสร็จ |
| E — Dynamic | Schema-driven form (custom fields), Master Data, Yield/tap-UI | ✅ เสร็จ (Step 13 column profiles ยังไม่ทำ) |
| F — Extras | รูปถ่ายจริง ✅, Excel import/export ✅, QR + Public Inspect ✅ (พร้อม security hardening), รายงาน+Excel ✅, **Supplier self-service plots ✅ (ล่าสุด)** — ที่เหลือ: approval workflow, offline sync, แผนที่, notifications, API key/webhook | 🔶 บางส่วน |

> หมายเหตุ: ตัว ROADMAP tracker เขียนถึง Step 12.5 แต่โค้ดจริงไปไกลกว่า (migrations ถึง 0029) — ยึดตารางนี้เป็นสถานะจริง

---

# ส่วน B — ลำดับการนำเสนอ (Slide Outline ~14 สไลด์)

> เรียงจาก business value → demo → technical → roadmap เหมาะกับผู้ฟังผสม
> 💡 = แนะนำภาพหน้าจอประกอบ

**สไลด์ 1 — Title**
- SRM_FieldInspect (FarmLog) — ระบบบันทึกการตรวจแปลงเกษตรภาคสนาม
- ชื่อผู้นำเสนอ / วันที่
- *Speaker note: ประโยคเดียว — "ระบบที่ทำให้การตรวจแปลงของทุก supplier เป็นดิจิทัล มีหลักฐาน และเห็นสถานะรวมได้ทันที"*

**สไลด์ 2 — ปัญหา/ที่มา**
- การตรวจแปลงเดิม: กระดาษ/LINE/Excel กระจัดกระจาย, ไม่มีหลักฐานรูป+พิกัด, ผู้บริหารไม่เห็นภาพรวม, คนนอกช่วยตรวจไม่ได้เพราะไม่มี account
- *Speaker note: เล่า pain 3 ข้อ แล้วปิดว่า "ทุกข้อถูกออกแบบแก้ในระบบนี้"*

**สไลด์ 3 — ภาพรวมระบบ (1 ภาพ)**
- แผนภาพ: Supplier → แปลง (QR) → บันทึกการตรวจ (จนท./คนนอก) → สถานะแปลง → Dashboard/รายงาน
- *ใช้เนื้อหา A1 Value Proposition 5 ข้อเป็น bullet ข้างภาพ*

**สไลด์ 4 — ผู้ใช้ 4 กลุ่ม**
- ตาราง A2 ย่อ — เน้นว่าครอบคลุมตั้งแต่ผู้บริหารถึงคนนอกที่ไม่มี account
- *Speaker note: จุดขายคือ "คนนอกส่งข้อมูลได้อย่างปลอดภัย" ซึ่งระบบทั่วไปไม่มี*

**สไลด์ 5 — Demo Flow 1: ตั้งต้นข้อมูล + ป้าย QR** 💡 หน้า Plots + ป้าย QR ที่พิมพ์
- ตาม A5 Flow 1 — ไฮไลต์: Excel import, พิมพ์ QR ทั้ง supplier คลิกเดียว, แผนผลผลิตต่อแปลง

**สไลด์ 6 — Demo Flow 2: เจ้าหน้าที่ตรวจแปลง** 💡 หน้า RecordForm บนมือถือ
- ตาม A5 Flow 2 — ไฮไลต์: สแกน QR auto-fill, ฟอร์ม tap-first, GPS+รูป 4 มุมบังคับ, สถานะแปลงอัปเดตอัตโนมัติ

**สไลด์ 7 — Demo Flow 3: คนนอกส่งบันทึกผ่าน QR** 💡 หน้า /public/inspect
- ตาม A5 Flow 3 — ไฮไลต์: ไม่ต้อง login แต่ปลอดภัย 2 ชั้น (QR key ลับ + รหัสเข้าตรวจ), session 30 นาที
- *Speaker note: สไลด์นี้คือฟีเจอร์เด่นสุด — อธิบายว่าปลอดภัยเพราะอะไรแบบง่ายๆ*

**สไลด์ 8 — Demo Flow 4: Supplier self-service**
- Supplier เจ้าของสวน login เอง → เห็นเฉพาะของตัวเอง → สร้าง/แก้แปลงตัวเองได้
- *Speaker note: ลดภาระแอดมิน — supplier ดูแลทะเบียนแปลงตัวเอง ระบบกันข้ามเขตให้อัตโนมัติ*

**สไลด์ 9 — Dashboard + รายงาน** 💡 Dashboard + รายงานสถานะแปลง + ไฟล์ Excel
- KPI, แปลงคะแนนต่ำที่ต้องดูแล, รายงานกรองหลายมิติ, export Excel หัวตารางไทย

**สไลด์ 10 — สถาปัตยกรรม (1 สไลด์)** — สำหรับสายเทคนิค
- Diagram: React SPA ↔ FastAPI ↔ PostgreSQL (RLS) + Azure AD + Docker/CI
- Stack ตาม A7 บรรทัดเดียวต่อชั้น

**สไลด์ 11 — Security Highlights** — สำหรับสายเทคนิค
- เลือก 5 ข้อเด่นจาก A6: RLS ระดับฐานข้อมูล, RBAC 43 permissions, QR 2 ชั้น, photo validation, audit logs
- *Speaker note: สรุปให้ผู้บริหารฟังได้ว่า "ข้อมูลแต่ละ supplier แยกขาดกันที่ระดับฐานข้อมูล"*

**สไลด์ 12 — สถานะโครงการ + ตัวเลข**
- ตาราง Phase A–F จาก A8 + ตัวเลขจาก A7 (29 migrations, 326+161 tests)
- *Speaker note: เน้น "ทุกฟีเจอร์มี automated test คุ้มครอง"*

**สไลด์ 13 — Roadmap ต่อ**
- จาก A3 นอก scope: approval workflow, offline sync, แผนที่, notifications, API/webhook
- *Speaker note: เรียงตาม impact — แนะนำ approval workflow กับ notifications ก่อน*

**สไลด์ 14 — สรุป + Q&A**
- ย้ำ 3 ข้อ: ตรวจแปลงเป็นดิจิทัลมีหลักฐาน / คนนอกร่วมส่งข้อมูลได้ปลอดภัย / ผู้บริหารเห็นภาพรวมทันที
