# Registry Integration Standard (Ops)

> มาตรฐานการเชื่อมต่อ CT App เข้ากับ **CT App Registry**
> Version 1.0 — 2026-05-22
> Moved to `docs/ops/` ใน v3.0 — operational governance (ไม่ใช่ technical web app concern)

**Required at L3** (external/regulated apps). L0-L2 = opt-in. ดู [`../../AGENTS.md`](../../AGENTS.md) §17

ทุก CT app ที่ต้องการให้ถูก **track** (อยู่ใน catalog ส่วนกลาง) และ **monitor**
(health, token cost, การใช้งาน) ต้องเชื่อมต่อกับ CT App Registry ตามมาตรฐานนี้

เอกสารนี้เขียนสำหรับ developer / AI agent ที่กำลังสร้างหรือดูแล CT app — *ไม่ใช่*
สำหรับคนที่ดูแลตัว registry เอง

---

## 1. ภาพรวม — integration มี 2 เฟส

| เฟส | ทำเมื่อไหร่ | ใครทำ |
|---|---|---|
| **1. Onboard** | ครั้งเดียว ตอนตั้ง app | dev/setup — register แล้วเก็บ API key |
| **2. Runtime** | ตลอดเวลา app ทำงาน | app ทำเอง — expose `/health` + push telemetry รายวัน |

**ทำไมแยก 2 เฟส:** registration คืน API key **ครั้งเดียว** และติด *approval gate*
(admin ต้องอนุมัติก่อน app ถึงใช้งานได้) — จึงไม่เหมาะให้ app auto-register ทุกครั้ง
ที่ start ตัวเอง

ทิศทางข้อมูล:

```
  Onboard  :  app  ──POST /register──▶  registry        (ครั้งเดียว, manual)
  Telemetry:  app  ──POST /telemetry─▶  registry        (push, รายวัน)
  Health   :  app  ◀──GET /health────  registry         (pull, ทุก ~5 นาที)
```

---

## 2. ค่าที่ทุก app ต้องมีใน config (env)

| env var | ค่า | ได้มาจาก |
|---|---|---|
| `REGISTRY_URL` | base URL ของ registry เช่น `https://registry.chiataigroup.internal` | IT (ตอน registry ถูก deploy) |
| `REGISTRY_API_KEY` | `ctreg_...` — secret | เฟส 1 (register response) |
| `PROJECT_SLUG` | slug ของ app ตัวเอง | เฟส 1 (ตั้งตอน register) |

`REGISTRY_API_KEY` เป็น **secret** — เก็บใน `.env` (gitignored) เท่านั้น ห้าม hardcode

---

## 3. เฟส 1 — Onboarding (ทำครั้งเดียว ตอนตั้ง app)

### 3.1 เรียก register

```
POST {REGISTRY_URL}/api/v1/projects/register
Content-Type: application/json
```

**เปิด — ไม่ต้องใส่ auth header** · body เป็น JSON **camelCase**

| field | จำเป็น | ชนิด / กติกา |
|---|---|---|
| `slug` | ✅ | 3-100 ตัว, pattern `^[a-z0-9][a-z0-9-]*[a-z0-9]$` — เป็น **ID ถาวร** ของ app เปลี่ยนไม่ได้ |
| `displayName` | ✅ | 1-200 ตัว |
| `ownerEmail` | ✅ | อีเมลที่ถูกต้อง — เจ้าของ/ผู้ดูแล app |
| `businessUnit` | ✅ | 1-50 ตัว |
| `backendUrl` | ⚠️ แนะนำ | URL — **ต้องใส่ถ้าต้องการให้ health monitor ทำงาน** (ดู §4.1) |
| `frontendUrl` | – | URL |
| `repoUrl` | – | URL |
| `version` | – | ≤50 ตัว |
| `authScope` | – | `both` / `internal_only` / `external_only` |
| `defaultLanguage` | – | ≤10 ตัว เช่น `th` |
| `techStack` | – | object อิสระ เช่น `{"backend":"FastAPI","db":"PostgreSQL"}` |

ตัวอย่าง request:

```json
{
  "slug": "ct-procurement-ai",
  "displayName": "CT Procurement AI",
  "ownerEmail": "owner@chiataigroup.com",
  "businessUnit": "procurement",
  "backendUrl": "https://procurement-ai.chiataigroup.internal",
  "authScope": "internal_only",
  "techStack": {"backend": "FastAPI", "ai": "Claude"}
}
```

### 3.2 เก็บ `apiKey` ทันที — แสดงครั้งเดียว

response `201 Created`:

```json
{
  "project": { "slug": "ct-procurement-ai", "status": "pending", "...": "..." },
  "apiKey": "ctreg_xxxxxxxxxxxxxxxxxxxxxxxxxxxx"
}
```

`apiKey` **แสดงครั้งเดียวเท่านั้น** — เอาไปใส่ `REGISTRY_API_KEY` ใน `.env` ของ app
ทันที ถ้าทำหาย ต้องให้ registry admin ออกใหม่ผ่าน key rotation (§7)

### 3.3 รอ admin อนุมัติ

app ลงทะเบียนแล้วจะอยู่สถานะ **`pending`** — API key **ยังใช้ไม่ได้** จนกว่า registry
admin (ป่าน/IT) จะกด approve การ push telemetry ก่อน approve จะได้ `403`

> ติดต่อ registry admin เพื่อขอ approve หลัง register

---

## 4. เฟส 2 — Runtime (app ทำอัตโนมัติ)

### 4.1 expose `GET /health`

Registry จะ **ping `{backendUrl}/health` เป็นระยะ** (default ทุก 5 นาที — admin
ปรับได้) เพื่อเช็คว่า app ยังมีชีวิตอยู่ app ต้อง:

- ตอบ HTTP **2xx** เมื่อทำงานปกติ (เนื้อหา body เป็นอะไรก็ได้)
- **ไม่มี auth** — เป็น public liveness probe
- **เบา** — ห้าม query หนักหรือเรียก external service
- ถ้า ping ล้มเหลว **2 ครั้งติด** (default — admin ปรับได้) registry จะเปิด
  incident และนับว่า app "down"

> ⚠️ ถ้าตอน register ไม่ใส่ `backendUrl` → registry ping ไม่ได้ → ไม่มีข้อมูล
> up/down และ uptime % ของ app นั้น

### 4.2 push telemetry รายวัน

```
POST {REGISTRY_URL}/api/v1/projects/{PROJECT_SLUG}/telemetry
X-API-Key: {REGISTRY_API_KEY}
Content-Type: application/json
```

body เป็น JSON **camelCase**:

| field | ชนิด / กติกา | ความหมาย |
|---|---|---|
| `date` | ISO date `YYYY-MM-DD` ✅ | วันของข้อมูล |
| `aiCalls` | int ≥0 | จำนวนครั้งเรียก AI |
| `aiInputTokens` | int ≥0 | input token รวมของวันนั้น |
| `aiOutputTokens` | int ≥0 | output token รวมของวันนั้น |
| `aiCostUsd` | string ทศนิยม ≤4 ตำแหน่ง ≥0 เช่น `"1.2345"` | ค่าใช้จ่าย AI (USD) |
| `activeUsers` | int ≥0 | ผู้ใช้ที่ active |
| `totalLogins` | int ≥0 | login สำเร็จ |
| `failedLogins` | int ≥0 | login ล้มเหลว |
| `errorCount` | int ≥0 | error ของวันนั้น |
| `p95LatencyMs` | int ≥0 หรือ `null` | latency p95 (ms) |
| `extraMetrics` | object หรือ `null` | เมตริกเพิ่มเติมเฉพาะ app |

ทุก field ยกเว้น `date` มี default = 0 / null — ส่งเฉพาะที่มี

ตัวอย่าง request:

```json
{
  "date": "2026-05-22",
  "aiCalls": 142,
  "aiInputTokens": 89000,
  "aiOutputTokens": 31000,
  "aiCostUsd": "2.4710",
  "activeUsers": 18,
  "totalLogins": 25,
  "failedLogins": 1,
  "errorCount": 3,
  "p95LatencyMs": 240
}
```

**Idempotent ตาม `date`** — ส่งซ้ำวันเดิม = แทนที่ของเดิม (ไม่เกิดแถวซ้ำ) → retry
ได้ปลอดภัย และ re-push เพื่อแก้ตัวเลขย้อนหลังได้

การ push จะอัปเดต `lastSeenAt` ของ app ใน registry ให้โดยอัตโนมัติด้วย

### 4.3 monitor ว่า "agent ทำงานปกติไหม"

ใช้ 2 สัญญาณรวมกัน:

| คำถาม | สัญญาณ | กลไก |
|---|---|---|
| server ยังขึ้นอยู่ไหม (real-time) | `/health` ตอบ 2xx | registry **pull** |
| agent ทำงานจริง / error เยอะไหม (รายวัน) | `aiCalls`, `errorCount` ใน telemetry | app **push** |

app ที่เป็น AI agent **ควรส่ง `aiCalls` เสมอ** — ถ้า `aiCalls = 0` ติดต่อกันหลายวัน
ทั้งที่ `/health` เขียว = สัญญาณว่า agent อาจไม่ถูกเรียกใช้

---

## 5. การทำให้อัตโนมัติ (standard implementation)

เป้าหมาย: dev ไม่ต้องเขียน integration เองทุก app — มีของกลางให้ drop-in

### 5.1 โครงสร้างที่แนะนำ

1. **โมดูล client** `app/integrations/registry.py` — drop-in เข้าทุก CT app
2. **APScheduler job** — รัน telemetry push วันละครั้ง (CT app ใช้ APScheduler
   เป็น default อยู่แล้ว)
3. **`/health` route** — FastAPI app ส่วนใหญ่มีอยู่แล้ว ระบุให้ชัดว่า**ห้ามมี auth**
4. **section บังคับใน `AGENTS.md`** — ทำให้ registry integration เป็น mandatory
   baseline เหมือน §12 (logging)

### 5.2 โครงโมดูล client (ตัวอย่าง)

```python
# app/integrations/registry.py
import httpx
from app.core.config import get_settings


async def push_daily_telemetry(metrics: dict) -> None:
    """ส่ง telemetry ของเมื่อวานเข้า CT App Registry. เรียกจาก scheduler job."""
    settings = get_settings()
    if not settings.REGISTRY_URL or not settings.REGISTRY_API_KEY:
        return  # ยังไม่ onboard — ข้าม

    url = f"{settings.REGISTRY_URL}/api/v1/projects/{settings.PROJECT_SLUG}/telemetry"
    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.post(
            url, json=metrics, headers={"X-API-Key": settings.REGISTRY_API_KEY}
        )
    resp.raise_for_status()  # 403 = ยังไม่ approve / key ผิด — ให้ retry วันถัดไป
```

scheduler job (รันรายวัน):

```python
# app/core/scheduler.py — เพิ่ม job
scheduler.add_job(
    _push_yesterday_telemetry,        # รวมเมตริก -> เรียก push_daily_telemetry()
    CronTrigger(hour=1, minute=0),    # ตี 1 ของทุกวัน
    id="registry_telemetry",
)
```

### 5.3 ทำเป็น mandatory

เพิ่ม section ใน `AGENTS.md` ของ template ระบุว่า: *ทุก CT web app ต้อง onboard กับ
CT App Registry* — พร้อม onboarding checklist (§8) และ env vars (§2) เป็น baseline
เหมือน §12 logging

---

## 6. Error handling — status code ที่ app ต้องรับมือ

ทุก error response มีรูปแบบ:

```json
{ "error": { "code": "...", "message": "...", "requestId": "...", "details": [...] } }
```

| Status | เมื่อไหร่ | app ควรทำอะไร |
|---|---|---|
| `201` | register / telemetry สำเร็จ | – |
| `401` | ไม่มี `X-API-Key` | ตรวจว่าตั้ง env ครบ |
| `403` | key ผิด **หรือ** ยังไม่ approve | ขอ admin approve / ตรวจ key — retry วันถัดไป |
| `404` | slug ไม่มีใน registry | ยังไม่ register — ทำเฟส 1 |
| `409` | register ซ้ำ slug เดิม | app เคย register แล้ว — ใช้ key เดิม |
| `422` | payload ผิด format | แก้ payload ตามตาราง §4.2 |

telemetry push เป็นงาน background — **ห้ามให้ push ที่ fail ทำ app หลักล่ม** log
ไว้แล้ว retry รอบถัดไป (idempotent อยู่แล้ว)

---

## 7. Key rotation (เมื่อ key หาย/รั่ว)

```
POST {REGISTRY_URL}/api/v1/projects/{slug}/rotate-key
X-API-Key: {REGISTRY_API_KEY}      ← key ปัจจุบัน (self-service)
```

คืน key ใหม่ (แสดงครั้งเดียว) — key เก่าตายทันที ถ้า key หายจน rotate เองไม่ได้
ติดต่อ registry admin ให้ rotate ให้ (admin ใช้สิทธิ์ของตัวเอง)

---

## 8. Onboarding Checklist

- [ ] เลือก `slug` (ตัวพิมพ์เล็ก/เลข/ขีด) — ถาวร เปลี่ยนไม่ได้
- [ ] `POST /api/v1/projects/register` พร้อม metadata + `backendUrl`
- [ ] เก็บ `apiKey` ใส่ `REGISTRY_API_KEY` ใน `.env` (gitignored)
- [ ] ตั้ง `REGISTRY_URL` + `PROJECT_SLUG` ใน config
- [ ] ติดต่อ registry admin ขอ approve
- [ ] ยืนยันว่ามี `GET /health` ตอบ 2xx โดยไม่ต้อง auth
- [ ] เพิ่ม scheduler job push telemetry รายวัน
- [ ] ทดสอบ: push telemetry 1 ครั้ง ได้ `201`

---

## 9. Endpoint Reference (ฝั่ง app ใช้)

| ใช้เมื่อ | Method + Path | Auth |
|---|---|---|
| Onboard | `POST /api/v1/projects/register` | ไม่มี |
| Push telemetry รายวัน | `POST /api/v1/projects/{slug}/telemetry` | `X-API-Key` |
| แก้ metadata ตัวเอง | `PATCH /api/v1/projects/{slug}` | `X-API-Key` |
| ขอ key ใหม่ | `POST /api/v1/projects/{slug}/rotate-key` | `X-API-Key` |
| (registry เรียก app) | `GET {backendUrl}/health` | ไม่มี — app เป็นคน expose |

---

## 10. Notes

- `slug` ตั้งครั้งเดียว เปลี่ยนไม่ได้ — ถ้าตั้งผิดต้อง register ใหม่ (ให้ admin
  reject ตัวเก่าเพื่อปล่อย slug)
- โค้ด integration เก่าใน `scripts/setup.py` ของ template **ใช้ไม่ได้** — path ผิด
  (`/api/v1/register` ที่ถูกคือ `/api/v1/projects/register`), แนบ `X-API-Key` ตอน
  register โดยไม่จำเป็น, และใช้ชื่อ env `CENTRAL_SERVER_URL` (มาตรฐานใหม่คือ
  `REGISTRY_URL`) — ต้องแก้/แทนที่ตามเอกสารนี้
- การ register **ไม่ควรทำอัตโนมัติตอน app start** — key คืนครั้งเดียว + ติด
  approval gate ทำเป็น setup step ครั้งเดียวเท่านั้น ส่วน runtime (health +
  telemetry) อัตโนมัติเต็มที่
