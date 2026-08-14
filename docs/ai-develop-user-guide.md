# คู่มืออบรม: ใช้ AI พัฒนา Web App ตาม CT Standard

> สำหรับผู้ใช้ที่ไม่ใช่โปรแกรมเมอร์ แต่ต้องการใช้ AI ช่วยสร้าง แก้ และดูแล web application อย่างปลอดภัยตามมาตรฐาน Chia Tai

---

## 1. เอกสารนี้ใช้ทำอะไร

เอกสารนี้สอนวิธีเริ่มใช้งาน `web-app-standard` ร่วมกับ AI coding assistant เช่น Claude, ChatGPT หรือ Gemini เพื่อให้ผู้ใช้สามารถสั่งงาน AI ได้ถูกทาง ตรวจงานได้เป็นขั้นตอน และไม่เผลอสร้างความเสี่ยงด้าน security หรือ governance

หลังอบรม ผู้ใช้ควรทำได้ 7 อย่าง:

1. เข้าใจว่า template นี้มีอะไรเตรียมไว้ให้
2. เริ่ม project ใหม่จาก template ได้
3. เข้าใจโครงสร้าง frontend/backend/database ที่ scaffold ออกมา
4. เขียนคำสั่งให้ AI ทำงานได้ชัดเจน
5. รู้ว่าเรื่องใด AI ทำต่อได้เอง และเรื่องใดต้องขออนุมัติก่อน
6. ใช้ git commit + push + PR workflow ได้
7. รู้ขั้นตอนพา project ขึ้น production

---

## 2. ภาพรวมแบบไม่ใช้ศัพท์โปรแกรมเมอร์

`web-app-standard` คือชุดตั้งต้นสำหรับสร้าง web app ภายในบริษัท โดยเตรียมของจำเป็นไว้แล้ว เช่น:

- โครงสร้าง backend, frontend และ database
- หน้าตาเริ่มต้นตาม CT brand
- ระบบ log สำหรับติดตามการใช้งาน
- เครื่องมือตรวจ secret และข้อผิดพลาดด้านความปลอดภัย
- มาตรฐานให้ AI อ่านก่อนเขียน code
- วิธีแบ่งความเสี่ยงของงาน เช่น งานเล็ก งานปกติ งานเสี่ยงสูง

คิดง่ายๆ คือ template นี้เป็น "รางรถไฟ" ให้ AI วิ่งได้เร็ว แต่ไม่หลุดออกนอกทางที่ปลอดภัย

---

## 3. ไฟล์สำคัญที่ควรรู้

| ไฟล์ | ใช้ทำอะไร | ใครควรอ่าน |
|---|---|---|
| `README.md` | วิธีเริ่ม project และติดตั้งเครื่องมือ | ทุกคน |
| `AGENTS.md` | กติกาหลักที่ AI ต้องทำตาม | AI, power user, reviewer |
| `project.config.example` | ตัวอย่างข้อมูล project ที่ต้องกรอก | project owner |
| `docs/ai-develop-user-guide.md` | คู่มืออบรมฉบับนี้ | ผู้ใช้ AI develop |
| `PROGRESS.md` | สถานะมาตรฐานและสิ่งที่ยัง pending | owner/reviewer |
| `docs/security.md` | รายละเอียด security เพิ่มเติม | ใช้เมื่อเป็นงานเสี่ยง |

ผู้ใช้ทั่วไปไม่จำเป็นต้องอ่านทุกไฟล์ ให้เริ่มจากเอกสารนี้กับ `README.md` ก่อน

---

## 4. สิ่งที่ต้องติดตั้งก่อนเริ่ม

ให้ติดตั้ง 3 อย่างนี้ก่อน:

| เครื่องมือ | ใช้ทำอะไร | ดาวน์โหลด |
|---|---|---|
| Python 3.12+ | รัน setup และ backend | https://www.python.org/downloads/ |
| Docker Desktop | รัน database และตรวจระบบแบบใกล้ production | https://www.docker.com/products/docker-desktop/ |
| Node.js 20+ LTS | รัน frontend | https://nodejs.org/ |
| VS Code | Editor หลักสำหรับคุยกับ AI | https://code.visualstudio.com/ |
| Git | จัดการ source code | https://git-scm.com/ |

สำหรับ Windows ตอนติดตั้ง Python ต้องเลือก **Add Python to PATH**

ตรวจว่าติดตั้งครบ — เปิด PowerShell (Windows) หรือ Terminal (Mac) แล้วพิมพ์:

```text
python --version
docker --version
node --version
git --version
```

ถ้าได้ version number ทุกตัว → พร้อมไปต่อ

---

## 5. สถาปัตยกรรมและโครงสร้าง Project

ก่อนเริ่มสั่ง AI ทำงาน ผู้ใช้ควรรู้ว่า project แบ่งเป็นกี่ชั้น แต่ละชั้นทำหน้าที่อะไร และอยู่ folder ไหน เพื่อสั่งงาน AI ได้ตรงจุด

### 5.1 ภาพรวม 3 ชั้น (3-Tier Architecture)

```
┌─────────────────┐      HTTP/JSON      ┌─────────────────┐      SQL      ┌─────────────────┐
│   Frontend      │ ───────────────────→ │    Backend      │ ────────────→ │   Database      │
│   (Browser)     │                      │   (API server)  │               │   (PostgreSQL)  │
│                 │ ←─────────────────── │                 │ ←──────────── │                 │
│   React + TS    │                      │   FastAPI       │               │   pgvector/pg16 │
│   port 5173     │                      │   port 8000     │               │   port 5432     │
└─────────────────┘                      └─────────────────┘               └─────────────────┘
       ↑                                          ↑                                ↑
   ผู้ใช้เปิด                                  รับ request                       เก็บข้อมูล
   เว็บไซต์                                    ตรวจสิทธิ์                         จริงทั้งหมด
                                              เรียก service
                                              ตอบกลับ JSON
```

### 5.2 หน้าที่ของแต่ละชั้น

| ชั้น | ใช้ทำอะไร | Stack |
|---|---|---|
| **Frontend** | แสดง UI, รับ input จากผู้ใช้, ส่ง request ไป backend | React 19 + TypeScript + Vite + Tailwind CSS |
| **Backend** | รับ HTTP request, ตรวจ auth, validate input, จัดการ business logic, คุย DB | FastAPI + Python 3.12 + SQLAlchemy async + Alembic |
| **Database** | เก็บข้อมูลทั้งหมด (users, business data, logs) | PostgreSQL 16 + pgvector extension |

### 5.3 ตำแหน่ง code ของแต่ละชั้นใน folder

```
my-app/
│
├── frontend/                       ← Frontend code ทั้งหมด
│   ├── src/
│   │   ├── App.tsx                 ← route table (public /login + protected tree)
│   │   ├── main.tsx                ← entry point ของ Vite
│   │   ├── index.css               ← CT brand theme (สีเขียว-ทอง)
│   │   ├── components/             ← UI components ที่ใช้ซ้ำได้
│   │   │   ├── AupModal.tsx        ← CT acceptable use policy popup
│   │   │   ├── AuthBootstrap.tsx   ← silent refresh ตอน mount
│   │   │   ├── RequireAuth.tsx     ← guard route ที่ต้อง login
│   │   │   ├── RequirePermission.tsx ← guard route ที่ต้องมีสิทธิ์
│   │   │   └── Layout/             ← AppLayout + TopBar + Sidebar + UserMenu
│   │   ├── pages/                  ← หน้าใน app
│   │   │   ├── Login.tsx           ← /login (form + SSO button)
│   │   │   ├── Dashboard.tsx       ← / (หลัง login)
│   │   │   └── settings/           ← /settings/* — admin pages
│   │   │       ├── Users.tsx       ← จัดการ user + per-user permissions
│   │   │       ├── Roles.tsx       ← จัดการ role (system + custom)
│   │   │       ├── Permissions.tsx ← catalog (read-only)
│   │   │       ├── Menus.tsx       ← จัดการ menu tree
│   │   │       └── AuthSettings.tsx← toggle SSO/local provider
│   │   ├── stores/auth.ts          ← Zustand: user + permissions + menus
│   │   ├── hooks/                  ← useAuth, useHasPermission
│   │   ├── api/                    ← API clients (auth, me, users, ...)
│   │   ├── types/auth.ts           ← TypeScript types
│   │   └── lib/auth-token.ts       ← access token store (sessionStorage)
│   ├── package.json                ← Node dependencies
│   ├── vite.config.ts              ← build config
│   ├── tailwind.config.ts          ← theme tokens
│   └── Dockerfile                  ← เอาไปสร้าง container ตอน deploy
│
├── backend/                        ← Backend code ทั้งหมด
│   ├── app/
│   │   ├── main.py                 ← FastAPI entry point
│   │   ├── api/                    ← REST endpoints
│   │   │   ├── v1/                 ← API version 1
│   │   │   │   ├── auth.py         ← /login, /logout, /refresh, /sso/*
│   │   │   │   ├── me.py           ← /me, /me/permissions, /me/menus
│   │   │   │   ├── users.py        ← user CRUD + per-user overrides
│   │   │   │   ├── roles.py        ← role CRUD
│   │   │   │   ├── permissions.py  ← catalog (read)
│   │   │   │   ├── menus.py        ← menu tree CRUD
│   │   │   │   ├── admin_settings.py ← Pattern C — provider toggles
│   │   │   │   └── installed_routers.py ← mount table
│   │   │   └── decorators.py       ← @audited สำหรับ log
│   │   ├── auth/                   ← JWT + password + Azure AD + dependencies
│   │   │   ├── jwt_service.py      ← encode/decode access + refresh
│   │   │   ├── password.py         ← bcrypt + policy
│   │   │   ├── azure_ad.py         ← MSAL + JWKS verify
│   │   │   ├── dependencies.py     ← get_current_user, require_permission
│   │   │   └── permissions.py      ← PermissionKey constants
│   │   ├── services/               ← business logic + DB operations
│   │   │   └── loggers/            ← activity / system / ai_call loggers
│   │   ├── db/
│   │   │   ├── session.py          ← DB connection
│   │   │   ├── base.py             ← SQLAlchemy base
│   │   │   ├── models/             ← table schemas
│   │   │   │   ├── user.py role.py permission.py
│   │   │   │   ├── role_permission.py user_role.py
│   │   │   │   ├── user_permission_override.py
│   │   │   │   ├── menu_item.py app_setting.py
│   │   │   │   └── activity_log.py system_log.py ai_call_log.py
│   │   │   └── seed.py             ← (legacy stub — ดู app/seed.py)
│   │   ├── seed.py                 ← bootstrap super admin + default roles/menus
│   │   ├── schemas/                ← Pydantic schemas (API JSON shape)
│   │   │   ├── base.py             ← CamelBaseModel
│   │   │   └── auth.py             ← Login/Token/User/Role/... schemas
│   │   ├── core/
│   │   │   ├── config.py           ← อ่าน env vars
│   │   │   ├── logging.py          ← StructuredLogger + PII mask
│   │   │   ├── pii.py              ← mask functions
│   │   │   └── scheduler.py        ← background jobs
│   │   ├── integrations/
│   │   │   ├── claude_ai.py        ← Anthropic wrapper
│   │   │   └── registry.py         ← CT App Registry client
│   │   └── middleware/             ← security headers, CORS, request_id
│   ├── alembic/
│   │   └── versions/               ← DB migrations (6 ตัว — 3 log + 3 auth)
│   ├── pyproject.toml              ← Python dependencies
│   ├── .env                        ← secrets (gitignored)
│   ├── .env.example                ← template (commit ได้, ค่าเป็น placeholder)
│   └── Dockerfile                  ← เอาไปสร้าง container ตอน deploy
│
├── docker/                         ← Docker config สำหรับ local DB
│   ├── docker-compose.yml          ← spin up PostgreSQL container
│   └── init-db.sql                 ← สร้าง DB + extension ตอน boot
│
├── docker-compose.yml              ← Production stack (root)
├── docker-compose.smoke.yml        ← Smoke test (prod-like full stack)
│
├── scripts/                        ← Setup + utility scripts
│   ├── setup.py                    ← guided setup wizard
│   ├── scaffold.py                 ← template code (อย่าแก้)
│   ├── init_project.py             ← (เหมือน setup.py แต่ headless)
│   ├── smoke-prod.bat / .sh        ← test prod stack ก่อน push
│   ├── build-user-guide-pdf.py     ← regenerate คู่มือนี้เป็น PDF
│   └── checks/                     ← custom pre-commit checks
│
├── docs/                           ← เอกสารทั้งหมด
│   ├── ai-develop-user-guide.md    ← คู่มือนี้
│   ├── backend.md / frontend.md / database.md / auth.md
│   ├── security.md / deployment.md / cicd.md / testing.md
│   ├── observability.md / logging.md / admin-config.md
│   ├── patterns/                   ← AI patterns + tooling
│   ├── ops/                        ← registry, runbook
│   └── human/                      ← onboarding, runbook, architecture
│
├── tests/                          ← meta-tests ของ template (ทดสอบ scaffold เอง)
│   ├── test_scaffold_smoke.py      ← scaffold emit OK + parse OK
│   ├── test_setup_guards.py        ← setup.py guard (ห้าม scaffold ลง template)
│   └── test_checks_units.py        ← 4 custom pre-commit checks unit tests
├── .github/workflows/              ← CI pipeline (ci.yml + security.yml)
├── .pre-commit-config.yaml         ← Hooks ที่รันก่อน commit
├── .gitignore
├── AGENTS.md                       ← Spec ของ AI (ห้ามแก้เอง)
├── CLAUDE.md                       ← Entry point ให้ AI
├── README.md                       ← Quick start
├── PROGRESS.md                     ← Status tracking
├── MIGRATION.md                    ← v2.x → v3.0 guide
├── cleanup-after-setup.bat         ← (Windows) ลบ scaffold tooling หลัง setup
└── project.config                  ← Project metadata (gitignored)
```

> 💡 `tests/` ใน root = test ของ **template เอง** (scaffold + setup + checks) — ไม่ใช่ test ของ business logic ของ project user
> User เขียน test ของ project ตัวเองที่:
> - **Backend tests:** `backend/tests/` (สร้างเอง)
> - **Frontend tests:** `frontend/src/__tests__/` หรือ `*.test.tsx` ข้างไฟล์ (Vitest co-located)
>
> หลัง setup เสร็จ + login ใช้งานได้ — รัน `python scripts/cleanup-after-setup.py` ลบ `tests/` + scaffold tooling อื่นออกได้ (ดู §7.5)

### 5.4 Ports ที่ใช้

| Service | Local Port | URL ใน browser |
|---|---|---|
| Frontend dev (Vite) | 5173 | http://localhost:5173 |
| Backend API | 8000 | http://localhost:8000 (API docs: /docs) |
| PostgreSQL | 5432 | (เปิดจาก backend ผ่าน DB_HOST=localhost) |
| Backend (prod smoke) | 8000 | http://localhost:8000/health |
| Frontend (prod smoke) | 8080 | http://localhost:8080 |

ถ้า port ติด — เช็คว่ามี service อื่นใช้อยู่หรือไม่ (เช่น PostgreSQL อื่นที่ port 5432)

### 5.5 ตัวอย่าง Data Flow: ผู้ใช้บันทึกสินค้าใหม่

```
1. ผู้ใช้เปิด http://localhost:5173/products → frontend render หน้าฟอร์ม
2. ผู้ใช้กรอก name, sku, price → กดปุ่ม "Save"
3. Frontend ส่ง:
       POST http://localhost:8000/api/v1/products
       { "name": "ปุ๋ย A", "sku": "F-001", "price": "250.00" }
4. Backend รับ → middleware check auth → endpoint validate ด้วย Pydantic schema
5. Backend เรียก service → service เรียก SQLAlchemy → SQL INSERT ไปที่ PostgreSQL
6. PostgreSQL บันทึก row → return id ที่สร้าง
7. Backend log activity ผ่าน @audited decorator → ส่ง JSON กลับ:
       { "id": 42, "name": "ปุ๋ย A", "sku": "F-001", "price": "250.00", "createdAt": "..." }
8. Frontend รับ response → แสดง toast "บันทึกสำเร็จ" + refresh table
```

ทุก step ที่เกี่ยวกับข้อมูลผู้ใช้/ธุรกิจจะถูก log เข้า `activity_logs` table โดยอัตโนมัติผ่าน `@audited` — ไม่ต้องเขียนเอง

---

## 6. วิธีเริ่ม project ใหม่ (รายละเอียดเต็ม)

### Step 0: เลือก Maturity Level ก่อน (อ่านก่อนรัน setup)

ตอน setup จะถามว่า project นี้เป็น level ไหน (L0/L1/L2/L3) — ค่านี้บอก AI ว่าควร scaffold feature governance / audit / security เข้มแค่ไหน

**Maturity Level มาจากนิยามใน [`AGENTS.md`](../AGENTS.md) §13:**

| Level | นิยาม (ใช้เกณฑ์ใดเกณฑ์หนึ่งก็พอ) | Feature ที่ scaffold ใส่ให้ | ตัวอย่าง project |
|---|---|---|---|
| **L0 Prototype** | ทดลอง < 3 เดือน · หรือ users < 10 คน · หรือ label ว่า MVP/POC | Tier 1 + project.config เท่านั้น (basic structure, ไม่มี audit) | demo ภายใน, ทดสอบไอเดีย, hackathon, สาธิตให้ผู้บริหารดู |
| **L1 Internal Tool** *(default)* | Stable · single BU · users < 50 คน | + activity_logs + App Settings (Pattern C) + UI Permissions (Pattern B — RBAC พื้นฐาน + per-user override) | form กรอกข้อมูลในแผนก, dashboard ภายใน BU, internal CRUD ทั่วไป |
| **L2 Business-Critical** | Multi-BU · mission-critical · ถ้าระบบล่มกระทบ business จริง | + Feature Flags (Pattern A) + AI cost budget | ระบบรับ order, สต็อกสินค้า, ระบบบัญชี, ระบบขนส่ง |
| **L3 External / Regulated** | Vendor/customer-facing · เข้าข่าย PDPA · มี audit requirement | + full audit retention + CT App Registry integration | app สำหรับ vendor, customer portal, ระบบที่มีข้อมูลส่วนบุคคล, สิ่งที่ต้องผ่าน security audit |

**ผลของระดับที่เลือก:**

- **คำถาม setup ต่างกัน:** L2/L3 จะ require governance owners (Tech Owner, Security Approver) — L0/L1 เป็น optional · L3 require Data Owner + Registry URL
- **AI ทำตามกฎเข้มขึ้น:** ระดับสูง → AI ต้อง enforce pattern เพิ่ม (เช่น L3 ต้อง register กับ CT App Registry, ต้อง audit retention เต็ม)
- **เวลา scaffold ต่างกัน:** L0 = เร็วสุด · L3 = scaffold มากกว่าเพราะมี audit + permissions + registry

**ถ้าไม่แน่ใจ:**
- เลือก **L1** (default) — เหมาะกับ internal tool ทั่วไป
- เปลี่ยนเป็น level สูงขึ้นได้ภายหลังโดยแก้ `MATURITY_LEVEL=L2` ใน `project.config` + ขอ AI เพิ่ม pattern ที่ required (migration ทำแบบ phased ได้)

**กฎทอง:** เลือกตามความเป็นจริง — ระดับสูงเกินจริง = ทำช้าตอนเริ่ม · ระดับต่ำเกินจริง = scale ขึ้น production แล้วต้อง refactor ภายหลัง

### Step 1: เอา template ไปยัง folder ใหม่

ใช้วิธีใดวิธีหนึ่ง:

**A. Clone จาก GitHub (recommended)**
```text
git clone https://github.com/CT-IT-Center/WEB-APP-STANDARD.git my-new-app
cd my-new-app
rm -rf .git
```

**B. Copy folder จากเพื่อนร่วมทีม** (ดู `README.md` สำหรับ robocopy command — ผู้ส่งต้อง clean junk ก่อนส่ง)

### Step 2: รัน setup

- **Windows:** double-click `setup.bat`
- **Mac/Linux:** `python scripts/setup.py`

### Step 3: ตอบคำถามทีละข้อ

Setup wizard จะถามตามตารางนี้ — กรอกตามจริง ค่าใดไม่แน่ใจให้ใช้ default

| # | คำถาม | ความหมาย | ตัวอย่าง / Default |
|---|---|---|---|
| 1 | `Project slug` | ชื่อย่อภาษาอังกฤษ (ใช้เป็น db name, docker image, folder name) | `inventory-tracker` |
| 2 | `Project display name` | ชื่อแสดงผลใน UI/README | `Inventory Tracker` |
| 3 | `Project description` | คำอธิบายสั้นๆ | `ระบบติดตามสต็อกสินค้า BU ปุ๋ย` |
| 4 | `Auth scope` | ใครใช้ระบบนี้? | `internal_only` (พนักงาน CT เท่านั้น) / `external_only` / `both` |
| 4a | `Bootstrap super admin email` | email ของ admin คนแรกของระบบ (role `internal:super_admin`) | `you@chiataigroup.com` |
| 4b | `Bootstrap auth type` | ใช้ SSO หรือ local password สำหรับ admin คนแรก | `sso` (Azure AD, default) / `local` |
| 4c | `Initial password` | password ครั้งแรก — ใช้ครั้งเดียวตอน seed (ปรากฏเฉพาะเมื่อ auth_type=local) | ≥ 12 ตัว + ผสม ≥ 2/4 กลุ่มอักขระ, บล็อก common/sequence/ตัวอักษรล้วน-เลขล้วน, masked input |
| 5 | `Maturity level` | ระดับความ critical ของ project — **ดูนิยามใน Step 0 ข้างบน** | `L1` (default) / L0 / L2 / L3 |
| 6 | `Stack variant` | รูปแบบ stack | `default` (FastAPI + React) — ไม่ต้องเปลี่ยน |
| 7 | `Azure AD Tenant ID` | สำหรับ SSO พนักงาน CT | UUID จาก IT (ปล่อยว่างถ้า external_only) |
| 8 | `Azure AD Client ID` | สำหรับ SSO พนักงาน CT | UUID จาก IT |
| 9 | `Production URL` | URL จริงตอน deploy | `https://inventory.chiataigroup.com` (ปล่อยว่างได้) |
| 10 | `Staging URL` | URL ทดสอบ | `https://staging.inventory.chiataigroup.com` (ปล่อยว่างได้) |
| 11 | `Production DB hostname` | ชื่อ DB server จริง | `db.internal.chiataigroup.com` (ปล่อยว่างถ้ายังไม่ deploy) |
| 12 | `Registry URL` | URL ของ CT App Registry | required ถ้า L3, optional ถ้าต่ำกว่า |
| 13 | `Owner` (Registry) | ผู้ดูแลใน Registry | `you@chiataigroup.com` |
| 14 | `Business Unit` | BU ที่ใช้ระบบ | `fertilizer` / `crop_protection` / `seed` / `other` |
| 15 | `App owner` (governance) | ผู้รับผิดชอบฝั่ง business | `you@chiataigroup.com` |
| 16 | `Tech owner` (governance) | ผู้รับผิดชอบ code + deploy | `team-name@chiataigroup.com` |
| 17 | `Security approver` | คนอนุมัติงาน Risky (default = tech owner ถ้าว่าง) | (ปล่อยว่างได้) |
| 18 | `Data owner` | required ถ้า project handle PII/regulated data | (ปล่อยว่างได้) |
| 19 | `Default UI language` | ภาษา default ของ UI | `th` (Thai) / `en` |
| 20 | `CI/CD platform` | ระบบ CI ที่ใช้ | `github_actions` (default) |
| 21 | `Env var prefix` | prefix สำหรับ shared infra | (ปล่อยว่าง — ใช้ default) |

💡 **เคล็ดลับ:** กด Enter ใช้ default ได้ส่วนใหญ่ ยกเว้น `Project slug`, `Project display name` ที่ต้องกรอก

### Step 4: รอ scaffold ทำงาน

Setup จะ:

```text
[1/6] Validating environment...               ✓
[2/6] Writing project.config + .env files...  ✓
[3/6] Scaffolding backend (FastAPI)...        ✓  (~30s)
[4/6] Scaffolding frontend (React + Vite)...  ✓  (~1-2 min, npm install)
[5/6] Scaffolding docker + tooling...         ✓
[6/6] Starting DB + running migrations...     ✓
```

ใช้เวลารวม **3-5 นาที** ขึ้นกับ internet (download npm packages + docker images)

### Step 5: หลัง setup เสร็จได้อะไรบ้าง

#### ไฟล์ใหม่ที่ถูกสร้าง

```
my-app/
├── project.config             ← ค่าที่คุณกรอก (gitignored)
├── backend/                   ← FastAPI app พร้อมรัน
│   ├── app/ ... (ตามโครงสร้างข้อ 5.3 — รวม auth/ + seed.py)
│   ├── alembic/versions/      ← 6 migrations เริ่มต้น
│   │   ├── 0001_activity_logs.py
│   │   ├── 0002_system_logs.py
│   │   ├── 0003_ai_call_logs.py
│   │   ├── 0004_auth_core.py            ← users + roles + user_roles
│   │   ├── 0005_menus_permissions.py    ← permissions + menus + overrides
│   │   └── 0006_app_settings.py         ← Pattern C (runtime toggles)
│   └── .env                   ← JWT + DB + AUTH_BOOTSTRAP_* + MFA key
├── frontend/                  ← React app พร้อมรัน
│   └── src/
│       ├── App.tsx            ← /login + protected tree (RequireAuth/Permission)
│       ├── pages/Login.tsx    ← login form + SSO button
│       ├── pages/settings/    ← Users / Roles / Permissions / Menus / Auth
│       ├── components/Layout/ ← TopBar + Sidebar + UserMenu
│       └── components/AupModal.tsx
└── docker/docker-compose.yml  ← PostgreSQL container กำลังรัน
```

#### สิ่งที่พร้อมใช้ทันที (out-of-the-box)

| Feature | Status |
|---|---|
| **Backend API** ที่ port 8000 | พร้อมรัน — `GET /health` คืน `{"status":"ok"}` + 29 endpoints (auth + RBAC + admin) |
| **API documentation** | http://localhost:8000/docs (Swagger UI auto-generated) |
| **Frontend** ที่ port 5173 | Login page → Dashboard + left sidebar (เมนูตาม role) |
| **Auth ครบชุด** | Login (local + SSO) + 8 default roles + per-user permissions + JIT user create on SSO |
| **Admin UI** | `/settings` → จัดการ Users / Roles / Permissions / Menus / Auth toggles |
| **PostgreSQL** ที่ port 5432 | กำลังรันใน Docker container — มี 12 ตาราง (3 log + 8 auth + 1 app_settings) |
| **Logging** | `activity_logs`, `system_logs`, `ai_call_logs` (partitioned by month) |
| **AUP modal** | popup แสดงครั้งแรกที่เปิด app — ผู้ใช้ต้อง accept ก่อนใช้ |
| **Pre-commit hooks** | gitleaks + detect-secrets + ruff + 4 custom CT checks |
| **CI workflow** | `.github/workflows/ci.yml` พร้อม — เริ่มทำงานทันทีที่ push |

#### สิ่งที่ยังไม่มี (ต้องเพิ่มเอง / ใช้ AI ทำต่อ)

- Business feature ของ project (ฟอร์ม, page, business logic) — ใช้ AI สร้าง
- `CLAUDE_API_KEY` ใน `backend/.env` (ถ้าจะใช้ Claude API)
- `AZURE_AD_CLIENT_SECRET` ใน `backend/.env` (ถ้าใช้ SSO)
- Real production URLs / DNS / TLS certificate (ฝั่ง infra)
- MFA enrollment UI (column `totp_secret` + `pyotp` พร้อมแล้ว — ยังไม่มี UI)
- Password reset / email invite flow (endpoints stub 501)

---

## 7. เริ่มทำงาน Daily

### 7.1 เปิด Service ครั้งแรก

> **SRM FieldInspect ใช้ runtime แบบรวม (Docker project เดียว `srm_fieldinspect`):**
> เปิดทุกอย่างด้วย `start-service.bat` · สถานะ `status-service.bat` · ปิด `stop-service.bat`
> — backend + DB อยู่ใน Docker, frontend รันบน Windows host. ดู
> [`docs/deployment.md`](deployment.md) §4 เป็น source of truth. ขั้นตอน manual
> ด้านล่างเป็นภาพรวมของ default scaffold เท่านั้น (SRM ไม่ต้องรัน backend บนเครื่องแล้ว)

ต้องเปิด terminal **2 หน้าต่าง** พร้อมกัน:

**Terminal 1 — Backend:**
```text
cd backend
set DB_HOST=localhost            # Windows (Mac: export DB_HOST=localhost)
python -m app.seed               # ครั้งแรกเท่านั้น — สร้าง super admin + roles + menus
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```
รอจน log บอก `Application startup complete` → backend พร้อม

> `python -m app.seed` รันครั้งเดียวพอ — รันซ้ำได้ (idempotent) ไม่เกิด duplicate data

**Terminal 2 — Frontend:**
```text
cd frontend
npm run dev
```
รอจน log บอก `Local: http://localhost:5173/` → frontend พร้อม

### 7.2 เปิด Browser

ไปที่ **http://localhost:5173** — ควรเห็น:

1. AUP modal popup ครั้งแรก → กด "ยอมรับ"
2. ระบบ redirect ไปหน้า `/login` อัตโนมัติ
3. Login ด้วย bootstrap admin (email/password ที่กรอกไว้ตอน wizard)
   - **Local auth:** กรอก email + password ในฟอร์ม
   - **SSO:** กดปุ่ม "เข้าสู่ระบบด้วย Azure AD" (ต้องตั้ง `AZURE_AD_CLIENT_SECRET` ก่อน)
4. หลัง login เห็น Dashboard + **left sidebar** (สีเขียว-ทอง CT brand):
   - 🟦 Dashboard (stat cards demo)
   - ⚙️ การตั้งค่า → ผู้ใช้งาน / Roles / เมนู / การ Login
5. มี TopBar แสดง project name + avatar (กดเพื่อ "ออกจากระบบ")

ถ้าเห็นแล้ว → setup สำเร็จ 🎉

> ⚠️ **หลัง login ใช้งานได้แล้ว:** ลบบรรทัด `AUTH_BOOTSTRAP_INITIAL_PASSWORD=...` ใน `backend/.env` ทันที (ใช้ครั้งเดียว ไม่ควร commit / เก็บไว้นาน)

### 7.3 เริ่มสั่งงาน AI

เปิด VS Code (หรือ Cursor / Claude Code CLI) ใน folder `my-app/` แล้วใช้ AI ตามวิธีในข้อ 10

### 7.4 หยุดทำงาน

- **SRM FieldInspect:** `stop-service.bat` (ปิด frontend + backend + DB) · ดูสถานะ `status-service.bat`
- Frontend: http://localhost:5173 · Backend: http://localhost:8000
- ⚠️ **ห้าม** `docker compose down -v` และห้ามลบ volume `srm-fieldinspect-db-data` (ข้อมูลจะหาย)

### 7.5 (Optional) ลบ template tooling หลัง setup สำเร็จ

เมื่อ:
1. รัน `python -m app.seed` ครั้งแรกผ่าน
2. Login ด้วย bootstrap admin เข้าใช้งานได้
3. ลบบรรทัด `AUTH_BOOTSTRAP_INITIAL_PASSWORD` ใน `backend/.env` แล้ว

→ scaffold tooling (setup wizard, scaffold templates, meta-tests) **ไม่ต้องใช้อีก** ลบทิ้งได้

```text
python scripts/cleanup-after-setup.py            # interactive — preview + confirm
python scripts/cleanup-after-setup.py --yes      # skip confirmation
python scripts/cleanup-after-setup.py --dry-run  # preview เท่านั้น

# Windows: double-click ก็ได้
cleanup-after-setup.bat
```

**สิ่งที่ลบ (~9,000 บรรทัด · ~1 MB):**
- `setup.bat` + `scripts/{setup,init_project,scaffold,check_python,_stdio,build-user-guide-pdf}.py`
- `tests/test_{scaffold,setup,checks}*.py` + `tests/` (ลบ folder เปล่าด้วย)
- `MIGRATION.md` (เกี่ยวกับ migrate template v2→v3 ไม่ใช่ project user)
- ตัวมันเอง (`cleanup-after-setup.bat` + `scripts/cleanup-after-setup.py`)

**สิ่งที่เก็บไว้ (ใช้ตอน runtime):**
- `backend/`, `frontend/`, `docker/`, `docs/`, `.github/`
- `scripts/checks/*.py` ← pre-commit hooks เรียกใช้
- `scripts/smoke-prod.{bat,sh}` ← ทดสอบ prod stack ก่อน push
- `.pre-commit-config.yaml`, `project.config`, ทุก runtime config

**Safety:** script จะ refuse ถ้าตรวจพบว่ารันใน upstream template repo (ไม่มี `project.config`) — กันลบเผลอ

### 7.6 หน้า Admin ที่ใช้ดูแลระบบ (มีให้ทุก project)

หลัง login (`/login`) ด้วย super-admin → sidebar ซ้ายมี 7 menu:

| Page | Path | สำหรับ | Permission key |
|---|---|---|---|
| Dashboard | `/` | หน้าแรกหลัง login | `menu.dashboard.view` |
| ผู้ใช้งาน | `/settings/users` | จัดการ user account + อนุมัติ + assign role | `menu.settings.users.view` |
| Roles | `/settings/roles` | สร้าง/แก้ role + กำหนด permission (super-admin only) | `menu.settings.roles.view` |
| เมนู | `/settings/menus` | จัดลำดับเมนูใน sidebar + visibility | `menu.settings.menus.view` |
| การ Login | `/settings/auth` | toggle local/SSO/signup/auto-approve | `menu.settings.auth.view` |
| **System Logs** | `/settings/system-logs` | ดู background job / scheduler / integration events | `menu.settings.system_logs.view` |
| **Activity Logs** | `/settings/activity-logs` | audit trail: login/logout/CRUD/role change/permission denied/CSV export | `menu.settings.activity_logs.view` |

**ใน Users page** — admin features ที่มีให้แล้ว:
- per-row toggle อนุมัติ user (`is_approved`) — ปุ่ม switch
- bulk "อนุมัติทั้งหมดในหน้านี้" (เห็นเฉพาะตอนมี user รออนุมัติ)
- edit user → assign role (super-admin role checkbox จะซ่อนถ้า caller ไม่ใช่ super-admin → ป้องกัน privilege escalation ที่ backend + UI 2 ชั้น)

**ใน Activity Logs / System Logs** — features:
- **Tabs** (Activity Logs only): Login / Security / All — switch มุมมองโดยไม่ต้องเปลี่ยน URL
- **Search box** (top-left) — ilike ทั่วฟิลด์ admin มักค้น (action / email / IP / resource / event / error)
- **Risk / Status / Category dropdown** — filter เพิ่มเติม
- **Date range** — `ตั้งแต่ / ถึง` — ครอบทั้งวัน (inclusive end-of-day, UTC)
- **Export CSV** — ดาวน์โหลด max 10,000 rows ตาม filter ปัจจุบัน (ตัวการ export เองก็ถูก audit เก็บเข้า activity_logs ด้วย)

**TopBar (มุมขวาบน):**
- 🌐 toggle ภาษา TH ↔ EN
- 🌙/☀️ toggle dark/light mode
- avatar → logout
- (มุมซ่อนซ้ายบน) ◧/◨ ย่อ/ขยาย sidebar (เป็น rail icon-only 56px)

**Login page (`/login`):**
- ปุ่ม Azure AD บน (primary) — ปุ่ม email/password ล่าง (secondary)
- มี toggle ภาษา + theme มุมขวาบนของหน้า (ใช้ store เดียวกับ TopBar → จำสถานะข้าม login)
- ถ้าบัญชียัง `is_approved=false` → ขึ้น "บัญชีของคุณรอการอนุมัติจากผู้ดูแลระบบ" (ไม่ใช่ "wrong password")

> 💡 **Permission gate ทำงาน 2 ระดับ:**
> 1. Frontend `<RequirePermission>` รอบ route — ถ้าไม่มีสิทธิ์ → แสดง `403 missing permission` page ทันที (ไม่ต้องรอ API)
> 2. Backend `require_permission()` รอบ endpoint — log permission_denied + 403
>
> ลบหรือเปลี่ยน frontend gate ไม่กระทบ backend gate — defense in depth

---

## 8. Git Workflow

หลัง setup เสร็จ project ยังไม่อยู่ใน git — ต้อง init เอง

### 8.1 ครั้งแรก: Init repo + First commit

```text
cd my-app
git init
git branch -m main
git add .
git commit -m "Initial commit from web-app-standard"   # version: see PROGRESS.md
```

⚠️ `git add .` จะ add ทุกไฟล์ยกเว้นที่อยู่ใน `.gitignore` (ซึ่ง exclude `project.config`, `.env`, junk folders อยู่แล้ว) — ปลอดภัย

### 8.2 ตั้ง Remote (เชื่อม GitHub)

ถ้ามี repo ใน GitHub (ขอจาก IT) แล้ว:

```text
git remote add origin https://github.com/CT-IT-Center/my-app.git
git push -u origin main
```

### 8.3 Daily: ทำ Feature → Commit → Push

แนะนำใช้ **feature branch** ไม่ commit ลง `main` ตรงๆ:

```text
# 1. สร้าง branch จาก main
git checkout -b feature/add-product-form

# 2. สั่ง AI ทำ feature → AI แก้ไฟล์ให้

# 3. ดูว่ามีอะไรเปลี่ยน
git status
git diff

# 4. commit
git add .
git commit -m "feat: add product form with sku validation"

# 5. push branch
git push -u origin feature/add-product-form

# 6. ไปที่ GitHub → กด "Create Pull Request"
```

### 8.4 Pre-commit Hooks (ก่อน commit จะรันอัตโนมัติ)

หลัง init แล้วต้อง install hooks ครั้งเดียว:

```text
pip install pre-commit
pre-commit install
```

หลังจากนั้นทุกครั้งที่ `git commit` hooks จะรัน — ถ้าเจอปัญหาจะ **block commit**:

| Hook | Block ถ้า | วิธีแก้ |
|---|---|---|
| `gitleaks` | มี secret (sk-, ghp_, eyJ...) ในไฟล์ | ลบ secret ออก, ใส่ใน `.env` แทน |
| `detect-secrets` | คล้ายข้างบน + รูปแบบอื่น | เหมือนกัน |
| `no-real-secrets-in-examples` | ใส่ค่าจริงใน `*.example` | ใช้ placeholder (`<your-key>`) |
| `no-direct-ai-sdk` | `from anthropic import` นอก `app/integrations/` | ใช้ `claude_ai.py` wrapper แทน |
| `camel-base-model-audit` | Pydantic schema ไม่ inherit `CamelBaseModel` | เปลี่ยน base class |
| `no-dict-in-endpoint` | endpoint ใช้ `dict` แทน schema | สร้าง schema |
| `ruff` (lint) | code มี style issue | ส่วนใหญ่ auto-fix ให้ |
| `check-yaml` / `check-json` | YAML/JSON มี syntax error | แก้ syntax |

> 💡 **AI จะรู้เอง:** ถ้า AI พิมพ์ secret/dict/SDK ผิด hooks จะ block ตอน commit → AI จะแก้ให้ คุณไม่ต้องจำ

### 8.5 Commit Message Convention

ใช้รูปแบบ `<type>: <description>`:

| Type | ใช้เมื่อ | ตัวอย่าง |
|---|---|---|
| `feat:` | เพิ่ม feature ใหม่ | `feat: add product CRUD endpoints` |
| `fix:` | แก้ bug | `fix: handle null sku in product create` |
| `docs:` | แก้เอกสาร | `docs: update onboarding for new env vars` |
| `refactor:` | refactor code (ไม่เปลี่ยน behavior) | `refactor: extract product service` |
| `test:` | เพิ่ม/แก้ test | `test: add cases for sku validation` |
| `chore:` | งานเบื้องหลัง (dependency, config) | `chore: bump fastapi to 0.115` |

### 8.6 CI Workflow (รันอัตโนมัติเมื่อ push หรือ open PR)

ทุก push จะ trigger `.github/workflows/ci.yml` ซึ่งมี 5 jobs:

```
┌──────────────────────────────────────────────────────────────┐
│  CI Pipeline (~5-8 นาที)                                      │
├──────────────────────────────────────────────────────────────┤
│  1. pre-commit       → รัน hooks ทั้งหมด                       │
│  2. backend-lint     → ruff check + ruff format + mypy        │
│  3. backend-test     → pytest (เชื่อม PostgreSQL test DB)      │
│  4. frontend-lint    → ESLint + tsc --noEmit + vite build     │
│  5. docker-smoke     → build prod Dockerfiles + ping /health  │
└──────────────────────────────────────────────────────────────┘
```

ถ้า job ไหน fail → PR จะ "Checks failed" → ห้าม merge จนกว่าจะแก้

### 8.7 ก่อน push ใหญ่ — ทดสอบ smoke

```text
# Windows
scripts\smoke-prod.bat

# Mac/Linux
bash scripts/smoke-prod.sh
```

จะ build Dockerfiles เหมือน prod + รันทั้ง stack local + ping `/health` → ถ้าผ่าน = CI ก็จะผ่าน (จับ Dockerfile rot ก่อนเสียเวลา)

---

## 9. ขึ้น Production

> ⚠️ **Risky operation** — ต้อง confirm กับ tech owner ก่อนทุกครั้ง

### 9.1 Server Prerequisites

| สิ่งที่ต้องมี | ใครจัดให้ |
|---|---|
| Linux server (VM / bare-metal) | IT Infrastructure |
| Docker + Docker Compose | IT |
| PostgreSQL server (centralized) — มี extensions `uuid-ossp`, `pgcrypto`, `pgvector` (ถ้าใช้ RAG) | DBA |
| Reverse proxy (Nginx/Caddy) สำหรับ HTTPS + routing | IT |
| `proxy-net` Docker network (template ใช้ที่ root `docker-compose.yml`) | IT (ครั้งเดียว) |
| Domain + TLS certificate | IT |
| GitHub access สำหรับ pull code | Tech owner |

### 9.2 ก่อน deploy: รัน Security Scan (บังคับ)

> 🔒 **Risky operation gate** — ทุก deploy ขึ้น staging/prod ต้องผ่าน security scan ก่อนเสมอ ถ้ามี finding ระดับ 🔴 CRITICAL หรือ 🟠 HIGH **ห้าม deploy** จนกว่าจะแก้

template ติด Claude Code skill ชื่อ `code-security-audit` มาให้พร้อม (`.claude/skills/code-security-audit/`) ครอบคลุม OWASP Top 10, CWE/CVE, ISO 27001, NIST — ใช้ได้กับทุกภาษา/framework

#### วิธี 1 — เรียก skill ผ่าน AI (แนะนำ)

ใน Claude Code chat พิมพ์ข้อความใดข้อความหนึ่ง — ระบบจะ trigger skill ให้อัตโนมัติ:

```text
ตรวจ security ของ project นี้ก่อน deploy ขึ้น prod
```

หรือเรียกตรงด้วย slash command:

```text
/code-security-audit
```

skill จะ:
1. **Auto-detect** ภาษา + framework ของ project จากไฟล์ใน repo
2. **สแกน source code** ตาม checklist ของแต่ละ stack (Python/TS/Go/Java/PHP/Ruby/C#/Rust/Swift/SQL/Docker/Terraform)
3. **รัน dependency audit อัตโนมัติ** — `npm audit` / `pip-audit` / `govulncheck` / `composer audit` / `cargo audit` / `dotnet list package --vulnerable` ตามที่เจอ
4. **Web search CVE ล่าสุด** ของ library หลัก (framework, ORM, auth) ในช่วง 6 เดือนหลัง
5. **ออกรายงาน** Excel 5 sheets + PDF 5 ส่วน — Dashboard / Findings / By File / Remediation Plan / References

#### วิธี 2 — รัน dependency audit ตรงๆ (ใช้เป็น manual fallback)

ถ้าอยากเช็คเฉพาะ dependency โดยไม่ผ่าน skill (เร็วกว่า, แต่ไม่ครอบคลุม code-level):

```bash
# Backend (Python)
cd backend
pip install pip-audit                                # ครั้งแรก
pip-audit                                            # human-readable
pip-audit --format=json --output=pip-audit.json      # ไว้แนบ PR

# Frontend (npm)
cd frontend
npm audit                                            # human-readable
npm audit --json > npm-audit.json                    # JSON for tooling
npm audit fix                                        # แก้อัตโนมัติ (เฉพาะที่ปลอดภัย)
```

#### วิธี 3 — รัน pre-commit hooks ทั้งชุดบน working tree

จะได้ทั้ง gitleaks (secret scan) + detect-secrets + ruff + CT custom checks 4 ตัว:

```bash
pre-commit install                                   # ครั้งแรก
pre-commit run --all-files                           # สแกนทั้ง repo
```

#### CI ทำอะไรอยู่แล้ว (ไม่ต้องรันมือ)

`.github/workflows/security.yml` รัน weekly:
- `pip-audit` + `npm audit` + `gitleaks --redact --no-git`

ผลขึ้นที่ tab **Actions** บน GitHub — ตรวจก่อน deploy ทุกครั้ง

#### ก่อน deploy ต้องผ่านเกณฑ์นี้

| ระดับ | เกณฑ์ | ทำอย่างไร |
|---|---|---|
| 🔴 CRITICAL | 0 finding | **บล็อก deploy** — แก้ก่อนเสมอ |
| 🟠 HIGH | 0 finding (หรือมี waiver ลายลักษณ์อักษรจาก tech owner) | แก้ใน sprint นี้ |
| 🟡 MEDIUM | ≤ 5 finding | ใส่ backlog ภายใน 2 sprints |
| 🔵 LOW / ⚪ INFO | ไม่บล็อก | บันทึกใน `docs/retro.md` ถ้าจะข้าม |

แนบไฟล์ report (`.xlsx` + `.pdf`) เข้า PR หรือ deploy ticket ทุกครั้ง

### 9.3 First-Time Deploy

ขั้นตอนทั่วไป (รายละเอียดเต็มใน [`docs/deployment.md`](deployment.md)):

```text
# 1. SSH เข้า server
ssh deploy@app.chiataigroup.com

# 2. Clone repo
git clone https://github.com/CT-IT-Center/my-app.git /opt/my-app
cd /opt/my-app

# 3. สร้าง backend/.env จาก template (กรอก secrets จริง)
cp backend/.env.example backend/.env
nano backend/.env
# ใส่:
#   DB_HOST=db.internal.chiataigroup.com
#   DB_PASSWORD=<from password manager>
#   JWT_SECRET_KEY=<random 64-char hex>
#   CLAUDE_API_KEY=<from anthropic console>
#   AZURE_AD_CLIENT_SECRET=<from Azure portal>

# 4. Build + start stack
docker compose up -d --build

# 5. รัน migration ครั้งแรก
docker compose exec -w /app backend alembic upgrade head

# 6. ทดสอบ
curl https://my-app.chiataigroup.com/health
```

### 9.4 Update Deploy (ครั้งต่อๆ ไป)

```text
cd /opt/my-app
git pull origin main
docker compose up -d --build
docker compose exec -w /app backend alembic upgrade head
```

หรือถ้าตั้ง CI/CD ไว้ — push ขึ้น main → GitHub Action deploy ให้อัตโนมัติ (ดู `.github/workflows/`)

### 9.5 Health Check + Smoke Test ก่อน Mark Done

หลัง deploy ตรวจอย่างน้อย:

```text
# Backend health
curl -fsS https://my-app.chiataigroup.com/health
# → {"status":"ok"}

# Frontend
curl -fsS https://my-app.chiataigroup.com/
# → HTML response

# Check log มี error ไหม
docker compose logs backend --tail 100
```

### 9.6 Rollback ถ้าพัง

```text
cd /opt/my-app
git log --oneline -5                          # หา commit ก่อนพัง
git checkout <good-commit-hash>
docker compose up -d --build
```

ถ้า migration พัง:

```text
docker compose exec -w /app backend alembic downgrade -1
```

### 9.7 Monitoring + Log

ใช้ **admin UI** ที่ scaffold มาให้ (ดู §7.6 รายละเอียดเต็ม) — ไม่ต้องเข้า DB เอง:

| สิ่งที่ต้องดู | UI page | ทำได้ |
|---|---|---|
| Login attempts / audit trail | `/settings/activity-logs` | tab Login/Security/All + filter risk + date range + search + Export CSV |
| Background jobs / scheduler / integration errors | `/settings/system-logs` | filter status/category + date range + search + Export CSV |
| Container status (infra-level) | `docker compose ps` (terminal) | — |
| Backend stdout / vite log (live) | `docker compose logs -f backend` | — |

ถ้า project เป็น **L3** จะมี Registry telemetry push อัตโนมัติ — ดูใน CT App Registry dashboard

### 9.8 ข้อห้ามตอน Deploy

- ❌ ห้าม push secret/`.env` ขึ้น git
- ❌ ห้าม run migration บน prod โดยไม่ backup DB ก่อน
- ❌ ห้าม `docker compose down -v` บน prod (จะลบ volume → ข้อมูลหาย)
- ❌ ห้าม deploy คนเดียวโดยไม่มี code review
- ❌ ห้าม disable HTTPS / pre-commit hooks เพื่อ deploy เร็ว

---

## 10. วิธีสั่งงาน AI ให้ได้ผลดี

คำสั่งที่ดีควรมี 4 ส่วน:

1. ต้องการทำอะไร
2. อยู่หน้าไหนหรือไฟล์ไหน
3. ข้อจำกัดที่ต้องรักษา
4. ให้ AI ตรวจอะไรหลังทำเสร็จ

ตัวอย่าง:

```text
ช่วยเพิ่มหน้า Dashboard สำหรับทีมจัดซื้อ
ใช้ pattern เดิมของ frontend ใน repo
ห้ามเพิ่ม dependency ใหม่ถ้าไม่จำเป็น
หลังทำเสร็จให้รัน check ที่เกี่ยวข้องและบอกไฟล์ที่แก้
```

ตัวอย่างสำหรับแก้ข้อความ:

```text
แก้ข้อความในหน้า login ให้เป็นภาษาไทยที่สุภาพขึ้น
แก้เฉพาะ copy ไม่ต้อง refactor code
หลังแก้ให้บอกตำแหน่งไฟล์ที่เปลี่ยน
```

ตัวอย่างสำหรับงาน backend:

```text
เพิ่ม API สำหรับดูรายการสินค้าแบบ read-only
ทำตาม pattern endpoint เดิม
ต้อง validate input และห้ามใช้ SQL string concat
เพิ่ม test เฉพาะส่วนที่เกี่ยวข้อง
```

---

## 11. เรื่องที่ AI ทำต่อได้เอง

ถ้าไม่ใช่งานเสี่ยงสูง AI สามารถทำต่อได้ใน scope ที่สั่ง เช่น:

- แก้ข้อความ
- ปรับ UI เล็กน้อย
- เพิ่ม component หรือ page ตาม pattern เดิม
- เพิ่ม test
- รัน lint/test/build ที่เกี่ยวข้อง
- แก้ bug ภายใน local/dev
- เสนอ diff ของเอกสาร handover โดยยังไม่แก้ไฟล์ human doc

หลักคือ ผู้ใช้ควรบอก scope ให้ชัด และให้ AI รายงานไฟล์ที่แก้กับคำสั่งที่ใช้ตรวจ

---

## 12. เรื่องที่ต้องให้คนอนุมัติก่อน

ถ้าเกี่ยวกับหัวข้อต่อไปนี้ ให้ AI หยุดและขออนุมัติก่อน:

- เปลี่ยน login, role, permission หรือ authorization
- migration หรือแก้ข้อมูลบน production
- เปลี่ยน secret management หรือ rotate key
- เปลี่ยน CORS, CSP, rate limit หรือ security headers
- เพิ่ม external integration ใหม่
- เพิ่ม dependency ใหม่กลุ่ม auth, crypto, network หรือ parser

ก่อนอนุมัติ ให้ขอให้ AI ตอบ 3 อย่าง:

1. จะทำอะไร และ impact คืออะไร
2. rollback plan คืออะไร
3. test plan คืออะไร

---

## 13. กฎ Security ที่ผู้ใช้ต้องจำ

ห้ามสั่ง AI ทำสิ่งเหล่านี้:

- ใส่ password, token, API key หรือ secret ลงใน code
- ใส่ค่าจริงใน `.env.example` หรือ `project.config.example`
- log ข้อมูลส่วนบุคคลหรือ secret
- ปิด security check เพื่อให้ผ่านง่ายๆ
- ใช้ SQL แบบต่อ string เอง
- ให้ระบบดึง URL จาก user โดยไม่ review
- upload file โดยไม่มี limit และ allowlist

ถ้า AI เจอ secret ที่เคยถูก commit แล้ว:

- ห้ามให้ AI quote ค่า secret กลับมา
- ให้รายงานเฉพาะ path/key name
- ให้แจ้ง owner เพื่อ rotate/revoke
- ห้ามให้ AI rewrite git history เองถ้ายังไม่ได้อนุมัติ

---

## 14. Governance ที่ต้องกรอกให้ชัด

ใน project จริงควรมี owner อย่างน้อย:

| Field | ความหมาย |
|---|---|
| `APP_OWNER` | เจ้าของระบบฝั่ง business |
| `TECH_OWNER` | เจ้าของด้านเทคนิค |
| `SECURITY_APPROVER` | ผู้อนุมัติเรื่อง security |
| `DATA_OWNER` | เจ้าของข้อมูล |

ข้อมูลเหล่านี้ไม่ใช่ secret แต่ช่วยให้รู้ว่าใครต้องตัดสินใจเมื่อมีงานเสี่ยง

---

## 15. Checklist ก่อนบอกว่างานเสร็จ

ให้ผู้ใช้ถาม AI ทุกครั้งก่อนจบงาน:

```text
ช่วยสรุป Definition of Done:
1. แก้ไฟล์อะไรบ้าง
2. รัน test/check อะไรบ้าง
3. มี security concern ไหม
4. มีสิ่งที่ต้องให้ owner อนุมัติไหม
5. มีอะไรที่ยังไม่ได้ตรวจหรือมี limitation ไหม
```

คำตอบที่ดีควรมี:

- รายชื่อไฟล์ที่เปลี่ยน
- คำสั่งที่รัน เช่น test, lint, build
- ผลลัพธ์ pass/fail
- สิ่งที่ไม่ได้รัน พร้อมเหตุผล
- note ด้าน security ถ้ามี

**สำหรับงานที่จะ deploy ขึ้น prod** — เพิ่ม gate นี้ก่อน mark done:

- รัน `/code-security-audit` (ดู §9.2)
- แนบไฟล์ report Excel + PDF เข้า PR/deploy ticket
- ยืนยันว่า finding ระดับ 🔴 CRITICAL / 🟠 HIGH = 0 (หรือมี waiver จาก tech owner)

---

## 16. ตัวอย่าง prompt สำหรับใช้งานจริง

### เริ่มต้น project

```text
ฉันเป็นผู้ใช้ที่ไม่ใช่ programmer
ช่วยพาฉันเริ่ม project นี้จาก README.md ทีละขั้น
ถ้าต้องรัน command ให้บอกว่ารันที่ไหน และ command ใช้ทำอะไร
```

### ให้ AI อ่านมาตรฐานก่อนทำงาน

```text
ก่อนแก้ code ให้อ่าน AGENTS.md เฉพาะ section ที่เกี่ยวข้อง
ทำตาม existing pattern ใน repo
ถ้าเจองานที่เข้าข่าย High-Risk Operations ให้หยุดถามก่อน
```

### เพิ่ม feature

```text
ช่วยเพิ่ม feature <ชื่อ feature>
ผู้ใช้คือ <กลุ่มผู้ใช้>
เป้าหมายคือ <ผลลัพธ์ที่อยากได้>
ข้อมูลที่ต้องแสดงคือ <รายการข้อมูล>
ทำตาม pattern เดิม และอย่าเพิ่ม dependency ใหม่ถ้าไม่จำเป็น
หลังทำเสร็จให้รัน check ที่เกี่ยวข้อง
```

### ตรวจ security

**ก่อน deploy / release ใหญ่ — ใช้ skill `code-security-audit`** (ดู §9.2):

```text
ตรวจ security ของ project นี้ก่อน deploy ขึ้น prod
```

หรือ slash command:

```text
/code-security-audit
```

skill จะออก Excel + PDF report — ใช้แนบ PR/deploy ticket

**สำหรับ review feature ย่อยระหว่างเขียน** (ไม่ต้องออก report):

```text
ช่วย review งานนี้แบบเน้น security
ดูเรื่อง secret, permission, input validation, SQL, logging PII, file upload และ SSRF
ตอบเป็นรายการ risk พร้อมไฟล์/บรรทัดที่เกี่ยวข้อง
```

### ขอคำอธิบายหลัง AI แก้เสร็จ

```text
อธิบายสิ่งที่แก้แบบให้ non-programmer เข้าใจ
แยกเป็น: เปลี่ยนอะไร, ทำไมต้องเปลี่ยน, ตรวจแล้วอย่างไร, ยังมีข้อควรระวังอะไร
```

### Git workflow

```text
ช่วยตรวจว่าตอนนี้ branch อะไร มีอะไรเปลี่ยนบ้าง
แล้วแนะนำ commit message ที่เหมาะกับ change นี้
ใช้ convention <type>: <description>
```

### Deploy

```text
ฉันจะ deploy ขึ้น staging
ช่วยเช็คก่อน: smoke test ผ่านมั้ย, CI ผ่านครบมั้ย, migration ใหม่มี downgrade plan มั้ย
และอธิบายว่า deploy command แต่ละบรรทัดทำอะไรก่อนรัน
```

---

## 17. แผนอบรม 2 ชั่วโมง (120 นาที)

| เวลา | หัวข้อ | ผลลัพธ์ |
|---|---|---|
| 0-10 นาที | ภาพรวม template และบทบาทของ AI | รู้ว่า template ช่วยอะไร |
| 10-25 นาที | สถาปัตยกรรม + folder structure | รู้ว่า frontend/backend/db อยู่ไหน |
| 25-50 นาที | อ่าน README + รัน setup + ตอบคำถาม | scaffold project ใหม่ได้ |
| 50-65 นาที | เปิด backend + frontend + ดู Dashboard | ระบบรันได้จริง |
| 65-80 นาที | รู้จัก AGENTS.md + ฝึก prompt | สั่งงาน AI ได้ชัด |
| 80-95 นาที | Workshop: ให้ AI แก้/เพิ่ม feature เล็ก | เห็น workflow จริง |
| 95-105 นาที | Git: branch + commit + push + PR | ใช้ git ได้ |
| 105-115 นาที | Security scan (§20) + pre-commit checks (§21) + High-Risk Ops | รัน `/code-security-audit` เป็น + รู้ว่าอะไรต้องอนุมัติ |
| 115-120 นาที | Checklist DoD (§21.3) + Deploy overview | ตรวจงานได้เอง + รู้เส้นทางขึ้น prod |

> หัวข้อเสริม (ถ้ามีเวลา/รุ่นที่ทำ UI เยอะ): **§22 impeccable** — ปรับดีไซน์หน้าใหม่

---

## 18. Workshop สำหรับผู้เข้าอบรม

ให้ผู้เข้าอบรมลองสั่ง AI ทำงานเล็ก 1 งาน:

```text
ช่วยแก้ข้อความหน้า Dashboard ให้เหมาะกับทีม <ชื่อทีม>
แก้เฉพาะข้อความ ไม่ต้องเปลี่ยน logic
หลังแก้ให้บอกไฟล์ที่เปลี่ยนและไม่ต้องรัน full test ถ้าไม่จำเป็น
```

จากนั้นให้ถาม AI:

```text
สรุปสิ่งที่แก้ตาม Definition of Done และบอกว่ามี security concern หรือไม่
```

เป้าหมายของ workshop ไม่ใช่ให้เขียน code เอง แต่ให้รู้วิธีสั่ง ตรวจ และหยุด AI เมื่อเข้าเขตเสี่ยง

### Workshop ขั้นต่อ (optional)

```text
ช่วยสร้าง branch ใหม่ชื่อ feature/copy-update
commit งานที่แก้
push ขึ้น remote
แล้วแนะนำว่าจะเปิด PR ยังไง
```

---

## 19. หลักคิดสำคัญ

- ให้ AI ทำงานเร็วได้ แต่ต้องอยู่ใน scope ที่ชัด
- Security rule สำคัญกว่าความสะดวก
- ถ้าเป็นงานเสี่ยง ให้หยุดถาม owner ก่อน
- อย่าส่ง secret ให้ AI และอย่าให้ AI เขียน secret ลง code
- ตรวจผลลัพธ์ด้วย checklist ทุกครั้ง
- เอกสารเยอะไม่ใช่เป้าหมาย เป้าหมายคือทำงานจริงได้ ปลอดภัย และตรวจสอบย้อนหลังได้
- ก่อน push ใหญ่ — รัน smoke test
- ก่อน deploy production — code review + CI ผ่าน + tech owner approve

---

## 20. ตรวจความปลอดภัยด้วย Code Security Audit

template ติดตั้ง Claude Code skill ชื่อ **`code-security-audit`** มาให้พร้อม
(`.claude/skills/code-security-audit/`) — ครอบคลุม OWASP Top 10, CWE/CVE,
ISO 27001, NIST, hardcoded secrets · ใช้ได้ทุกภาษา/framework

**วิธีเรียก** (พิมพ์ใน Claude Code):
```
/code-security-audit
```
หรือสั่งเป็นภาษาไทยก็ได้: *"ตรวจ security ของ project นี้ก่อน deploy"* /
*"หาช่องโหว่"* / *"audit code"*

**เมื่อไหร่ต้องรัน:**
- 🔴 **บังคับก่อน deploy ขึ้น staging/prod ทุกครั้ง**
- เมื่อแก้ของเสี่ยง: auth, login, การจัดการ secret, CORS, integration ภายนอก
- เป็นระยะ (เช่น ก่อน release ใหญ่)

**ผลลัพธ์:** รายงาน 5 ระดับ — 🔴 Critical / 🟠 High / 🟡 Medium / 🔵 Low / ⚪ Info
ออกเป็นไฟล์ **Excel + PDF**

**Gate (กฎเหล็ก):** ถ้ามี finding ระดับ **Critical หรือ High → ห้าม deploy**
จนกว่าจะแก้เสร็จ

> Security scan นี้ทำงานคู่กับ pre-commit (gitleaks + detect-secrets) ที่ตรวจ
> secret ให้ทุก commit อยู่แล้ว — §21 ด้านล่าง

---

## 21. เครื่องมือตรวจคุณภาพอัตโนมัติ + จุดที่ต้องเน้น

### 21.1 Pre-commit checks (ทำงานเองทุก commit)
รัน `pre-commit install` ครั้งเดียวหลัง setup แล้วทุก commit จะถูกตรวจ:

| Check | จับอะไร |
|---|---|
| gitleaks + detect-secrets | secret หลุดเข้า git |
| ruff (+format) | คุณภาพ/สไตล์ Python |
| no-real-secrets-in-examples | ค่าจริงใน `.example` |
| no-direct-ai-sdk | เรียก Claude/OpenAI SDK ตรงๆ (ต้องผ่าน `app/integrations/`) |
| camel-base-model-audit | schema ไม่ inherit `CamelBaseModel` |
| no-dict-in-endpoint | endpoint รับ `dict` ดิบ |
| **no-raw-colors** | สีนอก brand (`bg-blue-500`/hex) — เติม `brand-allow` ถ้าตั้งใจ |

ถ้า commit ถูกบล็อก = check เจอปัญหา → แก้ตามข้อความ แล้ว commit ใหม่

### 21.2 CI (อัตโนมัติทุก PR)
`ci.yml` (lint + test + build + docker-smoke) · `security.yml` (pip-audit + npm audit รายสัปดาห์)

### 21.3 จุดที่ต้อง "เน้น/ตรวจ" เสมอก่อนบอกว่าเสร็จ
บทเรียนจาก dogfood จริง — **compile/build ผ่าน ≠ ใช้งานได้**:
- ✅ **login เข้าได้จริง end-to-end** — generate → wizard → seed → login บนเว็บจริง ไม่ใช่แค่รันผ่าน
- ✅ ตอน wizard: auth type เลือก **local** ถ้าจะ login email/password; **email ตรงกัน** (.env / seed / หน้า login)
- ✅ migration ครบทุกคอลัมน์ (`alembic upgrade head` + `python -m app.seed` ไม่ crash)
- ✅ responsive 375px · focus ring · ทุกข้อความผ่าน i18n (th/en) · list มี empty state
- ✅ สีใช้ token (no-raw-colors เตือนให้) · ฟอนต์ Prompt/Plex อัตโนมัติ
- ✅ Risky op (auth/migration/deploy/secret/CORS) → **หยุดถาม owner ก่อน**

---

## 22. ปรับดีไซน์ให้สวยขึ้นด้วย impeccable (optional)

**`pbakaus/impeccable`** = design language (Claude Code skill/plugin) ที่ช่วยชี้ +
แก้ "anti-pattern" ของดีไซน์ (เช่น เส้นทองข้างการ์ด, gradient เลอะ, contrast ต่ำ)

**ติดตั้ง** (ในเครื่องคุณเอง — ไม่ได้มากับ project):
```
/plugin marketplace add pbakaus/impeccable
```

**ใช้งาน:**
```
/impeccable audit      ← ชี้ปัญหาดีไซน์หน้าที่เปิดอยู่ (แนะนำเริ่มจากอันนี้)
/impeccable critique   ← วิจารณ์เชิงลึก
/impeccable polish     ← แก้ให้เลย
```

**เมื่อไหร่:** ตอนทำหน้าใหม่หรือปรับ UI แล้วอยากได้ความเห็นด้านดีไซน์

**กฎสำคัญ — CT brand ชนะเสมอ:** ถ้า impeccable เสนอเปลี่ยน **สี/ฟอนต์** ที่ขัด CT
brand → **ไม่รับ** (standard คุมสี/ฟอนต์ไว้แล้วด้วย token + check `no-raw-colors`).
รับเฉพาะข้อเสนอเรื่อง spacing / hierarchy / contrast ที่ไม่ขัด brand

> **ไม่จำเป็นต้องติดตั้ง** — หน้าที่ scaffold มาผ่าน impeccable แล้ว (ไม่มี AI-tell).
> impeccable มีไว้ช่วยตอนคุณ *ออกแบบหน้าใหม่เอง* เท่านั้น · รายละเอียด design system:
> [`design-system.md`](design-system.md)

---

**End of คู่มืออบรม** — v3.0.14 · Chia Tai Web App Standard
