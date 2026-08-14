# สรุปมาตรการความปลอดภัย (Security Overview)

> CT Web App Standard — สรุปว่า standard นี้ "มีมาตรการอะไรบ้าง / ป้องกันอะไร /
> ข้อดี-จุดที่ต้องระวัง / อันไหน Auto อันไหน Manual / ใช้คำสั่งอะไร"
>
> เอกสารนี้เป็น **สรุปเชิงภาพรวม** — รายละเอียด implementation อยู่ใน
> `AGENTS.md §3, §4, §B` และ `docs/security.md`

---

## 1. ปรัชญา: ป้องกันที่ Layer ที่ WAF มองไม่เห็น

WAF ป้องกันได้ดีที่ **network / input layer** (SQLi pattern, XSS signature, DDoS,
path traversal) แต่ "มองไม่เห็น" ช่องโหว่ที่อยู่ใน **โค้ดและ logic** — IDOR,
broken access control, hardcoded secret, SSRF, insecure deserialization,
business-logic abuse

Standard นี้จึงป้องที่ **3 ชั้น** ที่ WAF เข้าไม่ถึง:

| ชั้น | ป้องกันด้วย | ตัวอย่างช่องโหว่ที่ปิด |
|---|---|---|
| **เขียนโค้ด** (AI/dev) | §3 Security Non-Negotiables ฝังใน prompt | hardcode secret, SQL concat, eval, SSRF |
| **ก่อน commit / ใน CI** | pre-commit hooks + GitHub Actions | secret leak, dependency CVE, SAST |
| **ก่อน deploy / runtime** | template + middleware + human gate | CORS, security headers, HTTPS, access control |

**หลักการสำคัญ:** ทุกกฎ "อยู่ที่เดียว" — `AGENTS.md` เป็น source of truth และ
authority order กำหนดว่า **§3 Security ชนะแม้ user สั่ง** (ถ้า user ขอให้ hardcode
secret → AI ต้องปฏิเสธ + เสนอทางอื่น)

---

## 2. มาตรการทั้งหมด — ป้องกันอะไร / Auto หรือ Manual

ตารางหลัก: 🤖 = tooling บังคับอัตโนมัติ · 👤 = ต้องคนทำ/ยืนยันเอง · 🤖+👤 = template ให้มา แต่ต้องตั้งค่า/ตรวจเอง

| มาตรการ | ป้องกัน | บังคับโดย | Auto / Manual |
|---|---|---|---|
| No hardcoded secrets | ขโมย credential จาก repo | `gitleaks` + `detect-secrets` (pre-commit + CI) | 🤖 |
| No real values in `*.example` | secret หลุดผ่าน example file | `scripts/checks/no_real_secrets_in_examples.py` | 🤖 |
| No `.env` committed | secret หลุดเข้า git | `.gitignore` + pre-commit | 🤖 |
| SQL parameterized only | SQL Injection | AI awareness + Bandit SAST | 🤖+👤 |
| No `eval`/`exec`/`pickle` | RCE / insecure deserialization | AI awareness + Bandit | 🤖+👤 |
| Pydantic validation ทุก endpoint | injection, type confusion | `scripts/checks/no_dict_in_endpoint.py` | 🤖 |
| **SSRF guard** (`safe_url.py`) | ยิง URL ไป internal/metadata (169.254.169.254) | `scripts/checks/no_unguarded_url_fetch.py` + helper | 🤖+👤 |
| No direct AI SDK call | log/cost/budget หลุด control | `scripts/checks/no_direct_ai_sdk.py` | 🤖 |
| Mutation มี activity log | ไม่มี audit trail | `@audited` decorator | 🤖 |
| PII masked in logs | ข้อมูลส่วนตัวรั่วใน log | `StructuredLogger` (mask default) | 🤖 |
| CORS ไม่ใช่ `["*"]` | cross-origin credential theft | Pydantic `Settings` validator | 🤖+👤 |
| Security headers | clickjacking, MIME sniff, XSS | `SecurityHeadersMiddleware` | 🤖 |
| JWT alg ไม่ใช่ `none` | algorithm-confusion bypass | `decode_jwt()` helper | 🤖 |
| Bcrypt passwords | plaintext/MD5 password leak | `passlib` helper | 🤖 |
| Rate limiting (login/SSO) | brute force, credential stuffing | `@limiter.limit` + fail-fast gate | 🤖+👤 |
| Broken Access Control / IDOR | เข้าถึงข้อมูลคนอื่น | `require_role()` + ownership check | 👤 |
| Container non-root | privilege escalation ใน container | Dockerfile template | 🤖+👤 |
| HTTPS in prod | MITM, sniffing | reverse proxy + deploy script | 👤 |
| Dependency CVE | supply chain / vulnerable lib | Dependabot + pip-audit + npm audit + Trivy | 🤖 |
| Business logic flaws | discount abuse, race condition | §4 confirm + human review | 👤 |
| High-risk ops | กระทบ shared system โดยไม่ตั้งใจ | §4 confirm protocol | 👤 |

---

## 3. กลุ่ม Auto (🤖) — tooling จับให้ ไม่ต้องจำเอง

ของพวกนี้ "พลาดไม่ได้" เพราะมีเครื่องจักรดักไว้ — AI หรือ dev ลืมก็ยังโดน block

### 3.1 Pre-commit hooks (รันตอน `git commit`)

| Hook | จับอะไร |
|---|---|
| `gitleaks` / `detect-secrets` | secret ที่กำลังจะ commit |
| `no-real-secrets-in-examples` | real value ใน `*.example` |
| `no-direct-ai-sdk` | เรียก Anthropic/OpenAI SDK ตรงๆ นอก `integrations/` |
| `camel-base-model-audit` | schema ไม่ inherit `CamelBaseModel` |
| `no-dict-in-endpoint` | endpoint รับ raw `dict` แทน Pydantic |
| `no-raw-colors` | สีนอก brand token (frontend) |
| `no-unguarded-url-fetch` | **fetch URL จาก user input โดยไม่ผ่าน SSRF guard** |

**ข้อดี:** จับตั้งแต่ก่อนเข้า git — ไม่ต้องพึ่งความจำ AI
**จุดที่ต้องระวัง:** hook ทำงานเฉพาะเครื่องที่ `pre-commit install` แล้ว — เครื่องใหม่
ต้องติดตั้งก่อน (CI เป็น safety net ชั้นสอง)

### 3.2 CI (GitHub Actions — รันทุก push / PR)

`.github/workflows/security.yml` รัน: `gitleaks`, `pip-audit`, `npm audit`,
`Bandit` (Python SAST), `Trivy` (container scan)

**ข้อดี:** บังคับทุกคนเท่ากัน แม้ skip pre-commit ในเครื่อง — ตรงกับแนวทาง
"Scan ทุก PR อัตโนมัติ"
**จุดที่ต้องระวัง:** `pip-audit` บน `pyproject.toml` ใช้ floor (`>=`) — ต้อง pin
`requirements.lock.txt` ก่อน go-live แล้ว audit ของจริงที่จะ deploy ด้วย

---

## 4. กลุ่ม Manual (👤) — tooling จับไม่ได้ ต้องคนตัดสิน

ของพวกนี้เป็น **logic / context** ที่เครื่องไม่รู้ "เจตนา" — เหมือน WAF ที่มองไม่เห็น

| มาตรการ | ทำไมต้อง manual | ใครรับผิดชอบ |
|---|---|---|
| Broken Access Control / IDOR | เครื่องไม่รู้ว่า resource นี้ "เป็นของใคร" | dev เขียน ownership check + reviewer ตรวจ |
| Business logic flaws | discount/race ดู request ปกติทุกอย่าง | human review + activity_logs |
| High-risk ops (auth/migration/CORS) | กระทบ shared system — ต้องมี rollback plan | §4 confirm กับ user ก่อนทำ |
| Sensitive logic (payment/data access) | "อย่าปล่อยให้ AI generate โดยไม่มี dev เข้าใจ security ตรวจ" | human PR review |

**§4 Confirm protocol** (ก่อนทำงาน Risky):
1. อธิบายว่าจะทำอะไร + impact
2. ระบุ rollback plan
3. ระบุ test plan ก่อน apply
4. รอ user ตอบ "ok"

---

## 5. SSRF — มาตรการล่าสุดที่เพิ่ง "ขยับจากกฎ → tooling"

SSRF เป็นช่องที่สถิติชี้ว่า AI สร้างบ่อยสุด (100% เมื่อ build URL-fetching feature)
เดิม standard มีแค่ "กฎ" (§3 ข้อ 9) — ตอนนี้บังคับด้วย tooling แล้ว

```python
# ❌ SSRF — attacker ชี้ url ไป http://169.254.169.254/ (cloud metadata)
async with httpx.AsyncClient() as client:
    resp = await client.get(payload.image_url)

# ✅ ผ่าน guard ก่อน fetch
from app.core.safe_url import assert_safe_url
safe = assert_safe_url(payload.image_url)   # block private/loopback/metadata IP
async with httpx.AsyncClient() as client:
    resp = await client.get(safe)
```

**ฉลาดตรงไหน:** check จับเฉพาะ URL ที่มาจาก user input — outbound call ที่ใช้
`settings.*` (Azure SSO, JWKS, MS Graph, Registry) **ไม่โดน flag** จึงไม่กระทบ
โค้ดเดิมที่ทำงานอยู่
**จุดที่ต้องระวัง:** guard validate ตอนเรียก — DNS rebinding (TOCTOU) ยังเป็นไปได้
เชิงทฤษฎี สำหรับ path assurance สูงให้ใช้ `allowed_hosts` allowlist

---

## 6. ข้อดีโดยรวม / จุดที่ต้องระวังโดยรวม

### ข้อดี

- **Defense-in-depth จริง** — กฎ (prompt) + pre-commit + CI + template + human gate ซ้อนกัน 4 ชั้น
- **ลดภาระความจำ** — สิ่งที่ tooling จับ (🤖) AI/dev ไม่ต้อง remember
- **Source of truth เดียว** — กฎไม่ขัดกันข้ามไฟล์ (`AGENTS.md` authority)
- **มีหลักฐานว่าใช้จริง** — `docs/security.md §15` log ทุก finding ที่แก้ + guard test กัน regress

### จุดที่ต้องระวัง

- **Manual ยังเป็น Manual** — IDOR / business logic / sensitive review ต้องมีคนเข้าใจ security ตรวจจริง เครื่องช่วยไม่ได้
- **pre-commit = opt-in ต่อเครื่อง** — ต้อง `pre-commit install` ทุกเครื่อง (CI เป็น net ชั้นสอง)
- **Dependency floor** — ต้อง pin lock file ก่อน prod ไม่งั้นอาจดึง version ที่ยังไม่เทสต์
- **Rate limit ต้องตั้ง backend จริง** — prod ต้องใช้ Redis (`RATE_LIMIT_STORAGE_URI`) ไม่งั้น app ไม่ boot (by design)
- **SSRF guard มี TOCTOU window** — ใช้ allowlist สำหรับงาน assurance สูง

---

## 7. คำสั่งที่ใช้ (Cheat Sheet)

### ติดตั้ง / เปิดใช้ guard ทั้งหมด

```bash
# ติดตั้ง pre-commit hooks (ทำครั้งเดียวต่อเครื่อง — สำคัญมาก)
pip install pre-commit
pre-commit install

# สร้าง secret baseline สำหรับ detect-secrets (ครั้งแรก)
detect-secrets scan > .secrets.baseline
```

### รัน scan เอง (ก่อน push / ตรวจทั้ง repo)

```bash
# รันทุก pre-commit hook กับทุกไฟล์
pre-commit run --all-files

# secret scan
gitleaks detect --source . --verbose

# Python SAST + dependency audit
bandit -r backend/app -ll
pip-audit -r backend/requirements.lock.txt --strict

# Frontend dependency audit
cd frontend && npm audit --audit-level=high
```

### รัน custom check ทีละตัว

```bash
# SSRF guard check (ไฟล์ backend)
python scripts/checks/no_unguarded_url_fetch.py backend/app/**/*.py

# secret ใน example file
python scripts/checks/no_real_secrets_in_examples.py .env.example

# AI SDK ตรงๆ
python scripts/checks/no_direct_ai_sdk.py backend/app/**/*.py
```

### Pin dependency ก่อน go-live (mandatory)

```bash
cd backend
pip install -e .[dev]
pip freeze > requirements.lock.txt        # commit ไฟล์นี้
pip-audit -r requirements.lock.txt --strict
```

### Deep security audit (ออกรายงาน Excel + PDF)

```bash
# ใช้ skill code-security-audit — ครอบคลุม OWASP Top 10, CWE/CVE, ISO 27001
/code-security-audit
```

### Incident: rotate secret ทันที

```bash
# JWT secret (revoke ทุก session)
openssl rand -hex 32        # ใส่ใน JWT_SECRET_KEY แล้ว restart

# DB credential / API key — rotate ที่ secret store แล้ว redeploy
```

---

## 8. สรุปหนึ่งบรรทัด

> ช่องโหว่ที่ **เครื่องจับได้** (secrets, SQLi, XSS, dependency, SSRF) — standard นี้
> **บังคับด้วย tooling อัตโนมัติแล้ว** · ช่องโหว่ที่ **ต้องใช้วิจารณญาณ** (IDOR,
> business logic, sensitive review) — บังคับด้วย **human review + §4 confirm gate**
> ทั้งสองอย่างซ้อนกันเป็น defense-in-depth ที่ครอบเกินกว่าที่ WAF ทำได้
