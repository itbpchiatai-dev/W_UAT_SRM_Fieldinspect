# AGENTS.md

> **Source of truth** สำหรับ AI agents และ developers ใน CT Web App
> Version: **3.0** | Type: AI-First Technical Spec | Target audience: AI coding agents (Claude, ChatGPT, Gemini) + human developers

---

## §0 How to Read This Document

**AGENTS.md เป็น routing document** — สั้นโดยตั้งใจ ให้ AI โหลดเฉพาะที่จำเป็นต่อ task

### 0.1 อ่านอะไรตาม task tier

| Task tier | ตัวอย่าง | อ่าน |
|---|---|---|
| **Tiny** | typo, CSS, copy, single-file unit test | **Tier 1 เท่านั้น** (§1-§5, ~70 บรรทัด) |
| **Normal** | feature, API endpoint, component, schema | Tier 1 + Tier 2 + layer ของ task (`docs/<layer>.md`) |
| **Risky** | auth, migration, deploy, security, secret, CORS | Tier 1 + Tier 2 + `docs/security.md` + **confirm กับ user ก่อนทำ** |

### 0.2 หลักการ AI Context Budget

- ใช้ `rg` / `grep` ก่อนเสมอ — อ่านไฟล์เต็มเฉพาะเมื่อ task ต้องการ whole-file review (doc refactor, migration planning, code review)
- ห้ามโหลด `docs/human/*` โดยอัตโนมัติ — โหลดเฉพาะเมื่อ user ขอ
- กฎ 1 ข้อ อยู่ 1 ที่ — ถ้าเจอกฎเดียวกันหลายไฟล์ที่ wording ต่าง → **AGENTS.md เป็น authority**

### 0.3 Authority Order (เมื่อ standards ขัดกัน)

ลำดับความสำคัญจากสูง → ต่ำ:

1. **User explicit request** ใน conversation ปัจจุบัน
2. **§3 Security non-negotiables + §4 High-risk operations**
3. **Existing codebase pattern** (§1)
4. **AGENTS.md** (ไฟล์นี้)
5. **`docs/<layer>.md`** reference docs
6. **AI's own preference / training default**

> **ข้อยกเว้น:** User request ชนะทุกอย่าง **ยกเว้น §3 Security** — ถ้า user ขอให้ทำสิ่งที่ violate security non-negotiables (เช่น hardcode secret, disable HTTPS) → ปฏิเสธ + อธิบายเหตุผล + เสนอ alternative

### 0.4 Status v3.0

Phase 1.5 + Phase 2 + Phase 3 ของ v3.0 รวมเสร็จแล้ว (tooling + logging + enforcement) — สถานะ rollout (Registry deploy, pgvector install, project migration) ติดตามใน `PROGRESS.md`

---

# TIER 1 — CORE RULES

> อ่านทุก task (รวมถึง Tiny) — ~70 บรรทัด

## §1 Prefer Existing Codebase Pattern over Standard

**กฎข้อแรกและสำคัญที่สุด:**

> ถ้า codebase มี pattern อยู่แล้ว → ทำตาม pattern นั้น แม้ AGENTS.md จะแนะอย่างอื่น
> **ยกเว้น** pattern เดิมขัด §3 Security non-negotiables → ต้อง refactor และแจ้ง user

วิธีปฏิบัติ:
1. ก่อนเขียน code ใหม่ ใช้ `rg` หาตัวอย่างของ pattern คล้ายๆ ใน repo ก่อน
2. ถ้ามี → ทำตาม style เดิม (naming, structure, error handling)
3. ถ้าไม่มี → ใช้ default จาก AGENTS.md
4. ห้าม impose standard มาทับ code เก่าโดยไม่จำเป็น

**เหตุผล:** consistency ใน codebase เดียวกัน > consistency ข้าม project

### Extension Path — เมื่อ standard "ไม่มีของ" ที่งานต้องใช้

ถ้า token / pattern / component ที่ standard ให้มา**ไม่พอ** (เช่น ต้องการสีใหม่,
spacing ใหม่, pattern ที่ไม่มีใน docs):

1. **ห้าม**แก้เฉพาะหน้าในโปรเจกต์ตัวเอง (hardcode, override token, local CSS hack)
2. บันทึกความต้องการ + เหตุผลใน `docs/retro.md` ของโปรเจกต์
3. เสนอเพิ่มเข้า **standard repo** (web-app-standard) → ปล่อยเป็นเวอร์ชันใหม่
   → ทุกโปรเจกต์ได้ของชิ้นเดียวกันพร้อมกัน
4. ระหว่างรอ: ถ้า block งานจริง ใช้ `brand-allow` comment ได้**เฉพาะจุด**
   พร้อมอ้าง retro entry — ห้ามใช้เป็นทางลัดถาวร

**เหตุผล:** standard ที่ถูก fork ทีละนิดในแต่ละโปรเจกต์ = ไม่มี standard.
ช่องทางขยายที่ถูกคือขยายที่ต้นน้ำที่เดียว.

---

## §2 Stack Defaults (Version Ranges)

ใช้ default ตามนี้ ห้ามเปลี่ยนโดยไม่มีเหตุผล (`docs/human/STACK_DEVIATIONS.md`):

| Layer | Default | Version Range |
|---|---|---|
| Backend | FastAPI + Python | Python 3.12+, FastAPI 0.110+ |
| Frontend | React + TypeScript + Vite | React 18 หรือ 19, TS 5+, Vite 5+/6+ |
| Database | PostgreSQL | 16+ |
| ORM | SQLAlchemy async + Alembic | SQLAlchemy 2.0+ |
| Auth | Azure AD SSO (internal) + Local (external) | MSAL latest, bcrypt |
| AI | Anthropic Claude (default provider) | latest SDK; เปิด provider abstraction (§16) |
| Containerization | Docker multi-stage | latest |
| Validation | Pydantic v2 | 2.6+ |

**Postgres extensions:**
- Default: `uuid-ossp`, `pgcrypto`
- Conditional: `pg_trgm` (เมื่อมี text search), `pgvector` (เมื่อมี embedding/RAG)

Version lock ตัวจริงอยู่ใน `pyproject.toml` + `package-lock.json` — **ห้ามใช้คำว่า "latest stable"** ใน spec

Stack อื่น (Next.js, HTMX, Streamlit) → ดู §A Approved Alternatives

---

## §3 Security Non-Negotiables (AI-controllable)

6 ข้อนี้ AI พิมพ์เองได้ → AI ต้องระวัง:

1. **ห้าม hardcode secrets** ใน source — secrets อยู่ใน env vars / secret store เท่านั้น (`.env` / Vault); `project.config` เก็บเฉพาะ **non-secret metadata** (project name, IDs, URLs)
2. **ห้าม log PII/secrets** — password, token, full national ID, full credit card
3. **ห้าม `eval()` / `exec()` / `pickle`** กับ user input
4. **ห้าม string concat / f-string ใน SQL** — ใช้ parameterized query หรือ ORM
5. **ห้าม return stack trace ใน production error response** — log ภายใน, response แค่ `requestId`
6. **ห้ามใส่ real values ใน `.env.example` / `project.config.example`** — ใช้ placeholder (`AZURE_AD_CLIENT_ID=00000000-0000-0000-0000-000000000000` หรือ `<azure-client-id>`) เท่านั้น
7. **Authentication is not authorization** — sensitive read/mutation ต้องมี explicit permission หรือ ownership check
8. **Validate external input at API boundary** — ห้าม rely เฉพาะ frontend validation
9. **ห้าม fetch user-provided URLs server-side** เว้นแต่ allowlisted/reviewed แล้ว (SSRF risk)
10. **ถ้ามี file upload** ต้อง enforce size limit, type allowlist, storage isolation, และ no direct execution

อีก ~7 ข้อ (CORS allowlist, HTTPS, JWT alg, container non-root, security headers, secret rotation, bcrypt) → tooling/template บังคับ (ดู §B Enforcement Matrix). AI อ่าน `docs/security.md` เมื่อ task เป็น tier **Risky**

---

## §4 High-Risk Operations (Confirm Required)

**หลัก:** การกระทำที่ "ย้อนยาก หรือกระทบ shared system" ต้อง confirm กับ user ก่อนทำเสมอ

Triggers:
- เปลี่ยน auth/authorization logic
- **Production** DB migration / bulk delete-update prod data / data backfill
- เปลี่ยน secret management / rotate keys
- เปลี่ยน CORS / CSP / rate limit / security headers
- เพิ่ม external integration ใหม่
- ติดตั้ง **new** auth/crypto/network/parser dependency (patch/minor bump ไม่ต้อง)

If not listed in High-Risk Operations, AI may proceed without confirmation within task scope.

Confirm protocol:
1. อธิบายว่าจะทำอะไร + impact
2. ระบุ rollback plan
3. ระบุ test plan ก่อน apply
4. รอ user ตอบ "ok" หรือเทียบเท่า

If AI discovers a committed secret or real credential:
- Do not quote the secret value
- Report path/key name only
- Ask user to rotate/revoke
- Do not rewrite git history unless explicitly approved

---

## §5 project.config (Single Source of Truth)

Project metadata ทั้งหมดอยู่ใน `project.config` ที่ root — gitignored

- ห้าม hardcode project name/IDs/URLs ใน source — อ่านจาก env vars เสมอ
- ห้าม commit `project.config` ที่มี real values
- **`project.config` ≠ secret store** — ใส่ non-secret metadata เท่านั้น (secrets อยู่ใน `.env`)

Full contract อยู่ใน §10 (Tier 2)

---

# TIER 2 — WORKING PROTOCOL

> อ่านเมื่อ task เป็น Normal หรือ Risky — ~130 บรรทัด

## §6 Task Tiers — รายละเอียด

### Tiny — ไม่กระทบ pattern หรือ contract

ตัวอย่าง:
- typo, CSS small fix, copy update, comment fix
- single-file unit test
- dependency **patch** bump (e.g. `1.2.3` → `1.2.4`)

Reading: §1-§5 เท่านั้น
Testing: lint + relevant test เฉพาะไฟล์ที่แก้
DoD: §8 checklist สั้น (skip checks ที่ไม่เกี่ยว)

### Normal — เพิ่ม/แก้ feature ภายใน layer ที่มี pattern

ตัวอย่าง:
- เพิ่ม API endpoint, สร้าง component ใหม่, เขียน service method
- **local/dev** schema change (เพิ่ม column, index) — ยังไม่ deploy prod
- dependency **minor** bump (ไม่ใช่ auth/crypto/network/parser)
- เพิ่ม dependency ใหม่ที่ไม่ใช่ security-sensitive

Reading: §1-§10 + `docs/<layer>.md` ของ layer ที่ touch
Testing: lint + typecheck + relevant unit/integration test
DoD: §8 checklist เต็ม + ตรวจ existing pattern (§1) แล้ว

### Risky — กระทบ security / data / infra

ตัวอย่าง:
- auth flow, secret, CORS, security header
- **production** migration / data backfill / breaking schema change
- เพิ่ม new auth/crypto/network/parser dependency
- deploy config, container, CI/CD pipeline
- external integration ใหม่

Reading: §1-§10 + relevant layer + `docs/security.md` + maturity-relevant Tier 4 sections
Testing: full lint + typecheck + integration test + manual verify
DoD: §8 + **explicit confirm กับ user ก่อนเริ่ม** (§4)

---

## §7 AI Context Budget

กฎเหล็กสำหรับ AI agent:

1. **ใช้ `rg` / `grep` / `glob` ก่อนเสมอ** — หา section ที่เกี่ยวข้องก่อนอ่านไฟล์
2. **โหลด section ดีกว่าโหลดทั้งไฟล์** — ใช้ `read_file(path, offset, limit)` สำหรับ targeted read
3. **อ่านไฟล์เต็ม > 300 บรรทัด เฉพาะเมื่อ task ต้องการ whole-file review** (doc refactor, migration planning, full code review) — งาน lookup ทั่วไปไม่ต้อง
4. **ห้ามโหลด `docs/human/*` อัตโนมัติ** — มี audience คนละกลุ่ม (developer/ops/tech-lead)
5. **ห้าม pre-load Tier 4 ใน Tiny/Normal task** — โหลดเฉพาะ section ที่ task touch
6. **ถ้า task บอกชัดว่า scope เล็ก** → trust user, อย่าขยาย scope เอง (เช่น "แก้ typo บรรทัดนี้" = ไม่ refactor file)

---

## §8 Definition of Done (Checklist สำหรับ AI)

ก่อน mark task เสร็จ ต้องตอบ "ใช่" ทุกข้อที่ **relevant** กับ task:

- [ ] ทำตาม existing pattern ใน repo (§1) — ไม่ใช่บังคับ standard มาทับ
- [ ] รัน check ที่ **relevant กับไฟล์ที่ touch** — ถ้า skip ระบุใน response (เช่น แก้ markdown → ไม่รัน `mypy`/`tsc`)
- [ ] Relevant test ผ่าน (เฉพาะที่เกี่ยวข้อง — ไม่ใช่ทั้ง repo)
- [ ] ไม่มี hardcoded secret / ไม่ commit `.env` / ไม่ใส่ real value ใน `*.example`
- [ ] ไม่ refactor ไฟล์อื่นที่ไม่เกี่ยวกับ task (no unrelated changes)
- [ ] **ถ้า touch UI:** ตรวจ responsive (mobile/tablet/desktop) ด้วย browser tool ถ้ามี — ถ้าไม่มี → รัน build + ระบุ limitation ใน response
- [ ] Response สุดท้าย list ไฟล์ที่เปลี่ยน + คำสั่ง test ที่ใช้
- [ ] ถ้า touch handover trigger (§9) → list proposed diff ใน final response

---

## §9 Handover Docs Behavior

ไฟล์ใน `docs/human/`:

| ไฟล์ | Update เมื่อ |
|---|---|
| `docs/human/onboarding.md` | เปลี่ยน dependency / env vars / setup steps |
| `docs/human/runbook.md` | เปลี่ยน deployment / monitoring / incident pattern |
| `docs/human/architecture.md` | เพิ่ม module / external integration / data flow |
| `docs/human/STACK_DEVIATIONS.md` | เปลี่ยน stack จาก default **ภายหลัง init** |

**AI Behavior (ไม่ขัด §0.2):**
- AI **ไม่แก้ `docs/human/*` ระหว่างทำ task หลัก** — ทำงานหลักให้เสร็จก่อน
- ถ้า task touch trigger → **list proposed diff ใน final response** (ไม่ต้องหยุดกลางงาน)
- รอ user approve ก่อนแก้ `docs/human/*`
- AI ไม่โหลดไฟล์ใน `docs/human/` ระหว่าง task ปกติ — โหลดเฉพาะตอนจะ propose diff
- **ถ้ามีโฟลเดอร์ `docs/dev-journal/`** (auto audit trail; opt-in ต่อเครื่อง): ตอนจะ propose
  diff handover ให้อ่าน `docs/dev-journal/<วันที่ของ session>.md` ก่อน ใช้เป็นวัตถุดิบ
  (ไฟล์ที่ touch + คำสั่ง test ที่รัน) — ไม่ต้องพึ่งความจำ. ถ้าไม่มีโฟลเดอร์นี้ → ข้ามไป

---

## §10 project.config Full Contract

```bash
# Identity
PROJECT_SLUG=             # lowercase-kebab, ใช้เป็น docker image, db name
PROJECT_DISPLAY_NAME=     # Title Case, UI titles
PROJECT_DESCRIPTION=      # one-line

# Maturity (v3.0 — gates Tier 4)
MATURITY_LEVEL=L1         # L0 | L1 | L2 | L3 (ดู §13)

# Stack variant (v3.0 — set ตอน init เท่านั้น; เปลี่ยนภายหลัง → log STACK_DEVIATIONS)
STACK_VARIANT=default     # default | next-js | htmx | streamlit | timescale

# Database ownership
DATABASE_MODE=new         # new | existing (existing = no automatic schema changes)

# Auth scope
AUTH_SCOPE=both           # internal_only | external_only | both

# Azure AD (สำหรับ internal users)
AZURE_AD_TENANT_ID=
AZURE_AD_CLIENT_ID=
# AZURE_AD_CLIENT_SECRET → .env เท่านั้น (gitignored)

# URLs
PRODUCTION_URL=
STAGING_URL=

# Governance
APP_OWNER=
TECH_OWNER=
SECURITY_APPROVER=
DATA_OWNER=

# Optional
DEFAULT_LANGUAGE=th       # th | en
CICD_PLATFORM=github_actions
ENV_VAR_PREFIX=           # e.g. "CTV" → CTV_DB_HOST
```

ค่าใน `project.config` inject เข้า `.env`, `pyproject.toml`, `package.json`, `docker-compose.yml` ผ่าน `scripts/init_project.py` / `scripts/setup.py`

**ข้อจำกัด:** `project.config` ห้ามมี secret/password/token — ใส่ `.env` (gitignored) เท่านั้น

---

# TIER 3 — LAYER STANDARDS

> อ่าน reference doc ของ layer เมื่อ task touch — ~60 บรรทัด

## §11 Layer References

| Layer | Reference | อ่านเมื่อ |
|---|---|---|
| Backend (FastAPI) | [`docs/backend.md`](docs/backend.md) | เพิ่ม endpoint / service / repository |
| Frontend (React+TS) | [`docs/frontend.md`](docs/frontend.md) | สร้าง component / page / hook **(responsive check ใน §8 DoD)** |
| UX/UI Design System | [`docs/design-system.md`](docs/design-system.md) | styling, สี/ฟอนต์/spacing, brand usage, component visual spec |
| Database (PostgreSQL) | [`docs/database.md`](docs/database.md) | migration / model / index |
| Auth | [`docs/auth.md`](docs/auth.md) | login/logout flow, role, permission |
| Testing | [`docs/testing.md`](docs/testing.md) | เขียน test ใหม่ / เปลี่ยน test infra |
| Deployment | [`docs/deployment.md`](docs/deployment.md) | Dockerfile / compose / CI/CD |
| Security (cross-cutting) | [`docs/security.md`](docs/security.md) | task Risky (§6) |
| Observability | [`docs/observability.md`](docs/observability.md) | logging infra, metrics, alerts |

---

## §12 API Conventions (Inline — Core)

### URL
- Plural nouns: `/users`, `/products`, `/business-units`
- kebab-case multi-word: `/business-units`
- Version at path: `/api/v1/...`
- Nested max 2 levels

### HTTP Verbs
| Verb | ใช้เมื่อ |
|---|---|
| `GET` | Read |
| `POST` | Create |
| `PUT` | Replace entire resource |
| `PATCH` | Partial update |
| `DELETE` | Remove |

### Status Codes (เลือกที่ใช้บ่อย)
`200` OK · `201` Created · `204` No Content · `400` Bad Request · `401` Unauthorized · `403` Forbidden · `404` Not Found · `409` Conflict · `422` Validation Error · `429` Rate Limit · `500` Internal Error · `503` Service Unavailable

### Error Format
```json
{
  "error": {
    "code": "VALIDATION_ERROR",
    "message": "Email is invalid",
    "details": [{"field": "email", "code": "invalid_format", "message": "..."}],
    "requestId": "abc123"
  }
}
```

### Conventions
- **DB columns:** `snake_case` (e.g. `user_id`, `created_at`, `is_active`)
- **API JSON keys:** `camelCase` (Pydantic ใช้ `alias_generator=to_camel` ผ่าน `CamelBaseModel`)
- Timestamps: ISO 8601 with timezone
- Money: string decimal — `"price": "1250.50"`
- Pagination: cursor default (`?cursor=...&limit=20`), offset เมื่อต้อง "page X of Y"; hard limit `limit ≤ 100`
- Filter/Sort: `?status=active&sort=-createdAt,name`

---

# TIER 4 — ENTERPRISE PATTERNS (Maturity-Gated)

> โหลดเฉพาะ section ที่ relevant กับ `MATURITY_LEVEL` ของ project — ~130 บรรทัด

## §13 Maturity Levels

ตั้งใน `project.config`: `MATURITY_LEVEL=L0|L1|L2|L3` (default `L1`)

| Level | Criteria | Required Patterns |
|---|---|---|
| **L0 Prototype** | < 3 เดือน OR < 10 users OR labelled "MVP/POC" | Tier 1 + project.config เท่านั้น |
| **L1 Internal Tool** | Stable, single BU, < 50 users | + §14 activity_logs + §15 Pattern C (App Settings) + §15 Pattern B (UI Permissions — baseline RBAC + per-user override) |
| **L2 Business-Critical** | Multi-BU, mission-critical, business-affecting failure | + §15 Pattern A (Feature Flags) + §16 AI cost budget |
| **L3 External / Regulated** | Vendor/customer-facing, PDPA scope, audit requirement | + full audit retention + §17 Registry |

**Upgrade rule:** ถ้า project เปลี่ยน level → update `MATURITY_LEVEL` + เพิ่ม pattern ที่ required ของ level ใหม่ (migration ทำแบบ phased)

---

## §14 Activity Logging (L1+)

**v3.0 simplification:** เหลือ **3 tables** (เดิม 4 — รวม `audit_logs` + `user_activity_logs` แล้ว)

| Table | Purpose | Required From |
|---|---|---|
| `activity_logs` | mutations + sensitive reads + security events (merged) | L1 |
| `system_logs` | jobs, integrations, system events | L1 |
| `ai_call_logs` | ทุก call ไป Claude API | L1 |

### DB schema vs API JSON convention

`activity_logs` มี flags — ใช้ snake_case ใน DB และ camelCase ใน API JSON:

| Concept | DB column (snake_case) | API JSON (camelCase) |
|---|---|---|
| Mutation flag | `is_mutation` | `isMutation` |
| Sensitive read flag | `is_sensitive_read` | `isSensitiveRead` |
| Security event flag | `is_security_event` | `isSecurityEvent` |
| Risk level | `risk_level` | `riskLevel` |

Risk level values: `low` | `medium` | `high` — สำหรับ admin filter

**Tooling enforcement (Phase 1.5):**
- `@audited` decorator บังคับให้ทุก mutation endpoint มี activity log entry — error ถ้าหาย
- `LoggerWrapper` mask PII auto-default — AI ไม่ต้อง remember
- Claude SDK direct call → blocked by lint rule (ต้องผ่าน `app/integrations/claude_ai.py`)

Full implementation → [`docs/logging.md`](docs/logging.md)

Default retention: 60 วัน — admin override ผ่าน `app_settings`

---

## §15 Admin-Configurable (Maturity-Tiered)

3 patterns — required ต่างกันตาม level:

| Pattern | Use | Required At |
|---|---|---|
| **C: App Settings** | AI prompt, email template, threshold, dropdown options | **L1+** |
| **A: Feature Flags** | เปิด/ปิด feature โดยไม่ deploy | **L2+** |
| **B: UI Permissions** | menu/action/widget visibility per user/role + per-user override | **L1+** |

### Decision Framework

ก่อนเขียน config / constant ใดๆ:
1. Business/admin อาจอยากเปลี่ยนค่านี้ในอนาคต? **Yes** → DB-driven (Pattern C/A/B ตาม level)
2. Infrastructure / secret / deploy-time? → env var / vault
3. Tied to code logic (HTTP code, file types)? → code constant

### Frontend Permission Keys — Hybrid (v3.0)

- **Backend:** seed permission catalog ใน DB
- **Frontend:** typed constants (generated จาก backend) — type safety + no typo
- **Visibility:** dynamic จาก `/api/v1/me/permissions` — admin จัด permission ได้
- **Route/component mapping:** code constants — ไม่ dynamic

Full implementation → [`docs/admin-config.md`](docs/admin-config.md)

---

## §16 AI Patterns (NEW v3.0)

Required at L2+ (recommended L1+ ถ้า AI feature เป็น core)

### Required patterns
1. **Provider abstraction** — interface `AIProvider`, default `AnthropicProvider`; เปิดให้ swap (OpenAI/Azure OpenAI/Gemini)
2. **Prompt caching** — system prompts **ยาว** (> 1024 tokens สำหรับ Sonnet, > 2048 สำหรับ Haiku) **หรือใช้ซ้ำกัน** ควรใช้ `cache_control` blocks (ลด cost 50-90%); short/dynamic prompts ไม่ต้อง
3. **Streaming response** — **interactive** endpoints (chat, real-time UI) ใช้ `StreamingResponse`; batch/background AI jobs (summarize, export) ใช้ normal response ได้
4. **Cost budget** — per-user quota รายวัน + circuit breaker; budget อ่านจาก `app_settings`
5. **MODEL_PRICING** — seeded ใน `app_settings` (super-admin only edit); ห้าม hardcode ใน code

### Optional patterns
- **MCP server** — เมื่อ app เปิดให้ AI client เรียก
- **Vector RAG** — เมื่อใช้ semantic search (ต้อง enable pgvector ใน §2)

### Hard rule
ห้ามเรียก `AsyncAnthropic` / `OpenAI` SDK ตรงๆ จาก service หรือ endpoint — ต้องผ่าน `app/integrations/<provider>.py` ที่ log + cache + budget-check อัตโนมัติ

Full implementation → [`docs/patterns/ai.md`](docs/patterns/ai.md)

---

## §17 Registry Integration

> Required at **L3** only — operational governance ไม่ใช่ technical concern

`L3 project ต้อง onboard กับ CT App Registry` (catalog + monitor + cost tracking ส่วนกลาง)
`L0-L2 opt-in` — ถ้าจะ track ใน catalog ส่วนกลางก็ได้

Implementation → [`docs/ops/registry.md`](docs/ops/registry.md)

---

## §18 CT Global Standards

มาตรฐาน CT-wide ที่ทุก app (ทุก level) ต้องตาม:

| Standard | Reference | Auto-delivered |
|---|---|---|
| AUP Modal (Acceptable Use Policy popup) | [`docs/aup-modal-standard.md`](docs/aup-modal-standard.md) | ✅ scaffold copy `docs/AupModal.tsx` → `frontend/src/components/AupModal.tsx` |

---

# APPENDICES

## §A Approved Stack Alternatives

Default stack (§2) ใช้กับ most cases — alternatives ตาม scenario:

| Scenario | ใช้ทำอะไร (ตัวอย่าง) | Approved Stack | `STACK_VARIANT` value |
|---|---|---|---|
| Internal CRUD / standard web app | dashboard, admin tool, line-of-business app | **Default (FastAPI + React/Vite)** | `default` |
| Public site ต้อง SEO / SSR | vendor portal, customer landing, marketing site | Next.js 15+ + FastAPI BFF | `next-js` |
| Tiny internal tool (< 5 pages) | form tool, simple report viewer, small admin panel | FastAPI + HTMX + Jinja | `htmx` |
| AI prototype (< 1 เดือน) | demo LLM chat, model eval UI, quick data viz | Streamlit / Gradio | `streamlit` (L0 only — refactor ถ้าขึ้น L1) |
| Production AI chat app | customer-facing chatbot, internal AI assistant | Default + streaming | `default` (ใช้ §16 AI Patterns) |
| Heavy time-series | sensor data, IoT telemetry, monitoring metrics | Default + TimescaleDB | `timescale` |

### กฎการเลือก variant

1. **ตอน init** เลือก variant ตาม scenario → บันทึกใน `project.config` → `STACK_VARIANT=<value>` — **ไม่ต้องบันทึก `STACK_DEVIATIONS.md`**
2. **เปลี่ยน variant ภายหลัง init** → log ใน `docs/human/STACK_DEVIATIONS.md` พร้อม rationale ตาม template (§C)
3. ใช้ tech ที่**ไม่อยู่ในรายการ approved** → ต้องคุยกับ user + log ใน `STACK_DEVIATIONS.md`

---

## §B Enforcement Matrix

Rule แต่ละข้อ ใคร enforce:

| Rule | Enforcer | AI Concern? |
|---|---|---|
| No hardcoded secrets | pre-commit (`gitleaks` + `detect-secrets`) ✅ | ✅ AI พิมพ์เอง |
| No real values in `*.example` | pre-commit (`scripts/checks/no_real_secrets_in_examples.py`) ✅ | ✅ AI พิมพ์เอง |
| No `.env` committed | `.gitignore` + pre-commit | — template |
| No direct AI SDK call outside `integrations/` | pre-commit (`scripts/checks/no_direct_ai_sdk.py`) ✅ | ✅ AI พิมพ์เอง |
| No `dict` param in endpoint | pre-commit (`scripts/checks/no_dict_in_endpoint.py`) ✅ | ✅ AI พิมพ์เอง |
| No unguarded server-side fetch of user-controlled URL (SSRF) | pre-commit (`scripts/checks/no_unguarded_url_fetch.py`) + `app/core/safe_url.py` helper ✅ | ✅ AI พิมพ์เอง |
| Mutation has activity log | `@audited` decorator in `app/api/decorators.py` ✅ | tooling raise |
| PII masked in logs | `StructuredLogger` (`app/core/logging.py` — `get_logger()`) ✅ | tooling default |
| camelCase JSON / snake_case DB | `CamelBaseModel` parent + `scripts/checks/camel_base_model_audit.py` ✅ | template default |
| CORS not `["*"]` | Pydantic `Settings` field validator | template |
| Container non-root | Dockerfile template | template |
| HTTPS in prod | reverse proxy + deploy script | ops |
| Security headers | `SecurityHeadersMiddleware` (มีอยู่แล้ว) | template |
| JWT alg not none | `decode_jwt()` helper enforces algo | template |
| Bcrypt for passwords | `passlib` helper | template |
| Migration on prod | human approval gate (§4) | ✅ AI ต้อง confirm |
| Security review | code-reviewer agent + human PR review | ✅ AI ต้อง flag |
| Dependency vulnerabilities | Dependabot + pip-audit + npm audit (CI) | ops |

**Legend:**
- ✅ = implemented in v3.0 (delivered by scaffold or already in template)
- ดู [`docs/patterns/tooling.md`](docs/patterns/tooling.md) สำหรับ reference implementations

**AI Reading:** AI ดูตารางนี้ก่อนเขียน → รู้ว่าอันไหน "tooling จับให้" (relax memory) อันไหน "ต้องระวังเอง"

---

## §C STACK_DEVIATIONS Entry Template

```markdown
## YYYY-MM-DD — <Deviation Name>

**Default:** <what AGENTS.md §2 says>
**Chose instead:** <what you used>
**Why default insufficient:** <reason>
**Risk:** <what could go wrong>
**Rollback plan:** <how to revert if needed>
**Approver:** <user who approved>
```

บันทึกใน `docs/human/STACK_DEVIATIONS.md` — เฉพาะเมื่อเปลี่ยน stack **ภายหลัง init** (init variant บันทึกใน `project.config` แทน)

---

## §D References

### AI Core (load per Tier 3 routing)
- [`docs/backend.md`](docs/backend.md) · [`docs/frontend.md`](docs/frontend.md) · [`docs/design-system.md`](docs/design-system.md) · [`docs/database.md`](docs/database.md)
- [`docs/auth.md`](docs/auth.md) · [`docs/security.md`](docs/security.md) · [`docs/deployment.md`](docs/deployment.md)
- [`docs/testing.md`](docs/testing.md) · [`docs/cicd.md`](docs/cicd.md) · [`docs/observability.md`](docs/observability.md)

### Tier 4 (maturity-gated)
- [`docs/logging.md`](docs/logging.md) — 3 tables (activity / system / ai_call), partitioned, retention
- [`docs/admin-config.md`](docs/admin-config.md) — Pattern C (L1+), B (L1+ baseline RBAC), A (L2+)
- [`docs/patterns/ai.md`](docs/patterns/ai.md) — provider abstraction, caching, streaming, cost budget, MCP, vector RAG
- [`docs/patterns/tooling.md`](docs/patterns/tooling.md) — pre-commit, custom checks, `@audited`, `StructuredLogger`
- [`docs/ops/registry.md`](docs/ops/registry.md) — L3 mandatory, L0-L2 opt-in

### CT Global
- [`docs/aup-modal-standard.md`](docs/aup-modal-standard.md)

### Human-only (don't auto-load)
- `docs/human/onboarding.md` · `docs/human/runbook.md` · `docs/human/architecture.md` · `docs/human/STACK_DEVIATIONS.md`

### Project
- [`project.config`](project.config) (gitignored) · [`project.config.example`](project.config.example)
- [`PROGRESS.md`](PROGRESS.md) — v3.0 tracking
- [`MIGRATION.md`](MIGRATION.md) — v2.x → v3.0 transition guide

---

**End of AGENTS.md v3.0** — ~530 lines · routing-doc model · maturity-tiered · tooling-enforced
