# docs/patterns/db-connections.md

> Opt-in module — **Database Connections + Query Sandbox**
>
> super_admin ลงทะเบียน external PostgreSQL ผ่าน UI แล้วรัน ad-hoc SQL
> (read-only by default, audited) — ไม่ต้องแก้โค้ดหรือ redeploy

---

## Overview

โมดูลนี้ให้ super_admin:
- ลงทะเบียน/แก้ไข external PostgreSQL targets ผ่าน `/settings/db-connections`
  (CRUD + ทดสอบการเชื่อมต่อ) — รหัสผ่านเก็บแบบ **Fernet-encrypted at rest**
- รัน ad-hoc SQL ใน Query Sandbox (`/settings/query-sandbox`) — **read-only by
  default**; เขียนได้เฉพาะเมื่อ connection เปิด `allow_write` + ผู้ใช้ opt-out read-only
- มี table-browser panel (อ่าน `information_schema` แบบ read-only)

**ปิดโดย default.** Flag: `FEATURE_DB_CONNECTIONS` (bool). เมื่อปิด: router ไม่ mount,
seed ข้าม permissions/menus/settings → feature หายทั้ง stack (frontend เป็น
permission/seed-driven ไม่ต้องแก้ conditional)

---

## เปิดใช้งาน

**โปรเจกต์ใหม่:** ตอบ "y" ที่คำถาม *"Enable Database Connections module?"* ใน
`setup.py` / `init_project.py` → scaffold จะ emit module + ตั้ง flag

**โปรเจกต์เดิม:** รัน patch จาก root ของโปรเจกต์
```bash
python patches/v3_1_0_db_connections_patch.py
cd backend && alembic upgrade head
python -m app.seed
```

**ทั้งสองกรณีต้องตั้ง encryption key** ใน `backend/.env` (มิฉะนั้น feature
fail-loud — ดู §1):
```bash
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
# ใส่ค่าใน backend/.env:
DB_CONNECTIONS_ENCRYPTION_KEY=<generated key>
FEATURE_DB_CONNECTIONS=true
```

> **สถาปัตยกรรม flag 2 ชั้น:** scaffold-time (`feature_db_connections` ใน
> project.config) ตัดสินว่า **emit** โมดูลหรือไม่; runtime (`FEATURE_DB_CONNECTIONS`
> ใน `.env`) ตัดสินว่า **mount router + seed** หรือไม่ — โปรเจกต์ที่ scaffold โมดูล
> มาแล้วยัง toggle ปิด/เปิดได้ทาง env โดยไม่ต้อง re-scaffold

---

## Security contract (ต้องคง — review ก่อนแก้)

1. **`DB_CONNECTIONS_ENCRYPTION_KEY` (Fernet) จำเป็นเมื่อเปิด — fail loud.**
   `app/core/crypto.py` raise `SecretEncryptionError` ชัดเจนเมื่อ key ว่าง/ผิด
   แทนที่จะเก็บ secret แบบกู้คืนได้เงียบๆ. key อยู่ใน `.env` เท่านั้น (ไม่ใช่ source /
   project.config / DB). หมุน key = invalidate ciphertext เดิม → ต้องกรอกรหัสใหม่
2. **super_admin only.** 3 permission keys (`db_connections.read/manage/query`)
   อยู่ใน `users.py` `_PRIVILEGE_MANAGEMENT_KEYS` → **per-user override ให้ไม่ได้**
   (เฉพาะ super_admin ผ่าน all-perms binding ใน `DEFAULT_ROLES`)
3. **Read-only by default.** read-only requests รันใน `SET TRANSACTION READ ONLY`
   (server เป็นคนปฏิเสธ write — defence เกินกว่า app-side); write ต้องการทั้ง
   `read_only=false` **และ** connection `allow_write=true`. `statement_timeout` +
   row cap อ่านจาก `app_settings` (`db_sandbox.*` — admin ปรับได้ ไม่ hardcode)
4. **ทุก query audit-logged.** activity_logs risk `high` — เก็บ **command tag +
   ความยาว SQL เท่านั้น ไม่เก็บ raw SQL** (อาจมี literal PII). password ไม่เคยอยู่ใน
   response schema (write-only field)
5. **L3 TODO — host allowlist.** admin ชี้ target ไป arbitrary `host:port` ได้ =
   SSRF surface. ดู [`../security.md`](../security.md) §3.6.1 — ต้องเพิ่ม host
   allowlist ก่อนเปิดบน `MATURITY_LEVEL=L3`

---

## ไฟล์ในโมดูล

| Layer | ไฟล์ |
|---|---|
| Model | `backend/app/db/models/db_connection.py` |
| Schemas | `backend/app/schemas/db_connection.py` |
| Service | `backend/app/services/db_connection_service.py` (engine cache + sandbox) |
| Crypto | `backend/app/core/crypto.py` (Fernet) |
| API | `backend/app/api/v1/db_connections.py` (CRUD + test + tables + query) |
| Migration | `backend/alembic/versions/…0011_db_connections.py` |
| Frontend | `frontend/src/api/dbConnections.ts`, `pages/settings/{DatabaseConnections,QuerySandbox}.tsx` |
| Test | `backend/tests/security/test_db_connection_secrets.py` |

Touchpoints (เปิดเมื่อ flag on): `config.py`, `seed.py`, `installed_routers.py`,
`auth/permissions.py`, `api/v1/users.py` (deny-list), `App.tsx`, `SettingsIndex.tsx`,
i18n `{th,en}.json`
