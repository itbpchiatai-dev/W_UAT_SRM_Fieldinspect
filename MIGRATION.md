# MIGRATION.md — v2.x → v3.0

> Migration guide สำหรับ project ที่ใช้ CT Web App Standard v2.x อยู่แล้ว
> Version: 3.0 | Released: 2026-05-25

---

## Scope

ไฟล์นี้ใช้กับ **project ที่ scaffold จาก template เดิม (v2.x)** เท่านั้น
Project ใหม่ที่ scaffold จาก v3.0 → ใช้ default ทันที ไม่ต้อง migrate

---

## Breaking Changes Summary

| ของเดิม v2.x | เปลี่ยนเป็น v3.0 | Impact |
|---|---|---|
| 4 log tables (audit_logs + user_activity_logs + system_logs + ai_call_logs) | 3 tables (`activity_logs` รวม audit+user_activity) | DB schema change |
| Mandatory admin-config ทุก project | Maturity-tiered (L0-L3) | Soft change — เพิ่ม `MATURITY_LEVEL` |
| MODEL_PRICING hardcoded | Seeded ใน `app_settings` | App seed + super-admin role |
| `docs/registry-integration.md` (mandatory baseline) | `docs/ops/registry.md` (L3 only) | File move + reference update |
| AGENTS.md 833 lines | AGENTS.md ~530 lines | AI context budget improvement |
| §11/§12/§13 mandatory ทุก project | Tier 4 maturity-gated | L0 prototypes ไม่ต้องทำ |
| AI proactively updates `docs/human/*` | AI proposes diff, human approves | Workflow change |

---

## Pre-Migration Checklist

1. **Backup database** — schema changes ใน activity_logs (P0)
2. **Note current maturity** — ดูที่ §1 เพื่อตัดสิน L0/L1/L2/L3
3. **List custom code** ที่ depend on `audit_logs` หรือ `user_activity_logs` tables
4. **Check `AGENTS.md` ปัจจุบัน** — ถ้าเปลี่ยน wording → save copy ก่อน overwrite

---

## 1. Determine Maturity Level

ก่อนเริ่ม migrate ต้อง classify project ปัจจุบัน:

| Level | Criteria | Required Patterns |
|---|---|---|
| **L0 Prototype** | < 3 เดือน OR < 10 users OR MVP/POC | Core Rules only |
| **L1 Internal Tool** | Stable, single BU, < 50 users | + activity_logs + App Settings |
| **L2 Business-Critical** | Multi-BU, mission-critical | + Feature Flags + AI cost budget |
| **L3 External/Regulated** | Vendor/customer-facing, PDPA scope | + Permissions + audit retention + Registry |

ตั้งใน `project.config`:
```bash
MATURITY_LEVEL=L1   # หรือ L0/L2/L3
```

---

## 2. Migrate Log Tables (Required L1+)

### 2.1 Add new `activity_logs` table

Migration file: `alembic/versions/XXXX_activity_logs.py`

```python
"""create activity_logs table (v3.0 merged audit + user_activity)"""
from alembic import op
import sqlalchemy as sa


revision = "XXXX_activity_logs"
down_revision = "<previous>"


def upgrade() -> None:
    op.execute("""
        CREATE TABLE activity_logs (
            id UUID NOT NULL DEFAULT uuid_generate_v4(),
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            user_id UUID,
            user_email_masked VARCHAR(255),
            action_type VARCHAR(30) NOT NULL,
            action VARCHAR(100) NOT NULL,
            resource_type VARCHAR(50),
            resource_id VARCHAR(100),
            is_mutation BOOLEAN NOT NULL DEFAULT FALSE,
            is_sensitive_read BOOLEAN NOT NULL DEFAULT FALSE,
            is_security_event BOOLEAN NOT NULL DEFAULT FALSE,
            risk_level VARCHAR(10) NOT NULL DEFAULT 'low',
            ip_address VARCHAR(45),
            user_agent VARCHAR(500),
            request_id VARCHAR(64),
            endpoint VARCHAR(200),
            http_method VARCHAR(10),
            http_status INTEGER,
            metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
            PRIMARY KEY (id, created_at)
        ) PARTITION BY RANGE (created_at);

        CREATE INDEX ix_activity_user_created ON activity_logs (user_id, created_at);
        CREATE INDEX ix_activity_action_type ON activity_logs (action_type, created_at);
        CREATE INDEX ix_activity_resource ON activity_logs (resource_type, resource_id);
        CREATE INDEX ix_activity_sensitive_created ON activity_logs (is_sensitive_read, created_at);
        CREATE INDEX ix_activity_security_created ON activity_logs (is_security_event, created_at);
        CREATE INDEX ix_activity_risk_created ON activity_logs (risk_level, created_at);
    """)

    # Create initial partition (current month)
    op.execute("""
        DO $$
        DECLARE
            start_date date := date_trunc('month', CURRENT_DATE);
            end_date date := start_date + interval '1 month';
            partition_name text := 'activity_logs_' || to_char(start_date, 'YYYY_MM');
        BEGIN
            EXECUTE format(
                'CREATE TABLE IF NOT EXISTS %I PARTITION OF activity_logs '
                'FOR VALUES FROM (%L) TO (%L)',
                partition_name, start_date, end_date
            );
        END $$;
    """)
```

### 2.2 Backfill from `audit_logs` + `user_activity_logs`

```python
"""backfill activity_logs from legacy tables"""

def upgrade() -> None:
    # From user_activity_logs
    op.execute("""
        INSERT INTO activity_logs (
            id, created_at, user_id, user_email_masked,
            action_type, action, resource_type, resource_id,
            is_mutation, is_sensitive_read, is_security_event, risk_level,
            ip_address, user_agent, request_id, endpoint, http_method, http_status,
            metadata
        )
        SELECT
            id, created_at, user_id, user_email_masked,
            action_type, action, resource_type, resource_id,
            is_mutation, is_sensitive_read,
            FALSE AS is_security_event,  -- not tracked in old schema
            'low' AS risk_level,
            ip_address, user_agent, request_id, endpoint, http_method, http_status,
            metadata
        FROM user_activity_logs
        WHERE created_at >= NOW() - INTERVAL '60 days';  -- retention window
    """)

    # From audit_logs — security events
    op.execute("""
        INSERT INTO activity_logs (
            id, created_at, user_id,
            action_type, action, resource_type, resource_id,
            is_security_event, risk_level,
            ip_address, user_agent, metadata
        )
        SELECT
            id, created_at, actor_id,
            CASE
                WHEN action LIKE 'auth.login%' THEN 'login'
                WHEN action LIKE '%permission%' THEN 'role_change'
                ELSE 'update'
            END,
            action, resource_type, resource_id,
            TRUE,
            'medium',
            ip_address, user_agent, metadata
        FROM audit_logs
        WHERE created_at >= NOW() - INTERVAL '90 days';  -- audit retention longer
    """)
```

### 2.3 Drop legacy tables (after verification)

⚠️ **Wait 7-14 days** after backfill to verify data integrity ก่อน drop

```python
"""drop legacy audit_logs and user_activity_logs"""

def upgrade() -> None:
    op.execute("DROP TABLE IF EXISTS audit_logs CASCADE;")
    op.execute("DROP TABLE IF EXISTS user_activity_logs CASCADE;")
```

### 2.4 Update Application Code

**Find/replace:**
- `AuditLogger` → `ActivityLogger`
- `UserActivityLogger` → `ActivityLogger`
- `audit_service.log(...)` → `activity_logger.log(action_type=..., risk_level=...)`
- Wire `risk_level="high"` สำหรับ security events (permission changes, role updates)

ดู [`docs/logging.md`](docs/logging.md) §1.3 สำหรับ service signature ใหม่

---

## 3. Migrate `MODEL_PRICING` to `app_settings`

### 3.1 Seed pricing (super-admin only)

```python
# scripts/seed_ai_pricing.py
import asyncio

from app.db.session import get_db_session
from app.services.app_setting_service import AppSettingService


PRICING_SEED = {
    "claude-opus-4-7": {"input": 15.0, "output": 75.0, "cache_read": 1.5, "cache_write": 18.75},
    "claude-sonnet-4-6": {"input": 3.0, "output": 15.0, "cache_read": 0.30, "cache_write": 3.75},
    "claude-haiku-4-5-20251001": {"input": 1.0, "output": 5.0, "cache_read": 0.10, "cache_write": 1.25},
}


async def main():
    async with get_db_session() as db:
        service = AppSettingService(db)
        for model, pricing in PRICING_SEED.items():
            # implement: upsert with requires_role="super_admin"
            ...
        await db.commit()


if __name__ == "__main__":
    asyncio.run(main())
```

### 3.2 Update `AiCallLogger`

Remove `MODEL_PRICING` dict from code → use `_estimate_cost()` ที่อ่านจาก `app_settings`

ดู [`docs/logging.md`](docs/logging.md) §3.2

### 3.3 Add `requires_role` column to `app_settings`

```python
"""add requires_role to app_settings"""

def upgrade() -> None:
    op.add_column(
        "app_settings",
        sa.Column("requires_role", sa.String(50), nullable=True),
    )
    # Mark pricing settings as super-admin only
    op.execute("""
        UPDATE app_settings
        SET requires_role = 'super_admin'
        WHERE key LIKE 'ai.pricing.%';
    """)
```

---

## 4. Move Registry References

### 4.1 Move file

```bash
mkdir -p docs/ops
git mv docs/registry-integration.md docs/ops/registry.md
```

### 4.2 Update references

Find/replace ใน:
- `AGENTS.md` (1 ที่)
- `scripts/init_project.py`
- `scripts/setup.py`
- `scripts/scaffold.py`
- `PROGRESS.md`

```
docs/registry-integration.md → docs/ops/registry.md
```

### 4.3 If project is < L3

Registry ไม่ใช่ mandatory baseline อีกต่อไป — ตัดสินใจ:
- **Keep:** ถ้าอยากให้ app ถูก track ใน CT App Registry → ทำต่อ
- **Remove:** ถ้าไม่ต้องการ → unset `REGISTRY_URL` ใน `.env` + ลบ telemetry job

---

## 5. Update `project.config`

เพิ่ม 2 fields ใหม่:

```bash
# v3.0 additions
MATURITY_LEVEL=L1         # L0 | L1 | L2 | L3
STACK_VARIANT=default     # default | next-js | htmx | streamlit | timescale
```

ดู [`project.config.example`](project.config.example)

---

## 6. Update `AGENTS.md` Reading Behavior

AI agents that have been pre-loaded กับ v2.x AGENTS.md จะอ่าน v3.0 อัตโนมัติเมื่อเริ่ม session ใหม่

แต่ behavior changes:
- AI **เสนอ diff** สำหรับ `docs/human/*` แทน silent update
- AI อ่าน **Tier 1 (~60 lines) เท่านั้น** สำหรับ Tiny task
- AI ใช้ `rg` ก่อน เปิดไฟล์เต็มเฉพาะเมื่อจำเป็น

ถ้าทีมมี internal AI tooling ที่ pre-cache AGENTS.md — invalidate cache

---

## 7. Phase 1.5 — Tooling (Recommended)

หลัง migrate schema แล้ว ติดตั้ง tooling ที่บังคับ rule แทน AI memory:

| Tool | Install | Enforces |
|---|---|---|
| `pre-commit` + `gitleaks` | `pre-commit install` | No secrets in commits |
| `detect-secrets` baseline | `detect-secrets scan > .secrets.baseline` | No new secrets |
| `@audited` decorator | Implement ตาม `docs/logging.md` §1.4 note | Mutation has activity log |
| Custom ruff rule | Add to `pyproject.toml` | No direct Claude SDK calls |

Phase 1.5 spec อยู่ใน `PROGRESS.md`

---

## 8. Verification Checklist

หลัง migrate เสร็จ:

- [ ] `MATURITY_LEVEL` set ใน `project.config`
- [ ] `STACK_VARIANT` set (default ถ้าใช้ default stack)
- [ ] `activity_logs` table มี data backfilled
- [ ] Application ใช้ `ActivityLogger` (ไม่มี `AuditLogger`/`UserActivityLogger`)
- [ ] `app_settings` มี pricing seeded สำหรับทุก model ที่ใช้
- [ ] `MODEL_PRICING` dict ลบจาก code แล้ว
- [ ] `docs/ops/registry.md` exists; old path references updated
- [ ] AGENTS.md เป็น v3.0 (อ้าง §1-§18, ไม่ใช่ §1-§22 ของ v2.x)
- [ ] CI passes — lint, typecheck, tests
- [ ] (Optional, after 7-14 days) drop `audit_logs` + `user_activity_logs`

---

## 9. Rollback Plan

ถ้าเจอปัญหาหลัก migration:

1. **Keep old tables** — อย่า drop `audit_logs`/`user_activity_logs` จนแน่ใจ
2. **Revert AGENTS.md** จาก git history (commit ก่อน v3.0)
3. **Revert `project.config`** — remove `MATURITY_LEVEL`, `STACK_VARIANT`
4. **Code rollback** — ถ้า logger refactor มีปัญหา revert commits + redeploy

---

## 10. Support

- ปัญหา migration → ติดต่อทีม IT Center
- v3.0 spec questions → ดู [`AGENTS.md`](AGENTS.md) §0 reading protocol
- Updates หลัง v3.0 → ดู [`PROGRESS.md`](PROGRESS.md)
