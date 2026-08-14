# docs/logging.md

> Comprehensive Logging — implementation reference
>
> Policy + maturity gates อยู่ใน [`AGENTS.md`](../AGENTS.md) §14
> This doc covers data models, services, partitioning, retention, PII masking

---

## Overview

**v3.0 simplification:** 3 tables (เดิม 4 — merged `audit_logs` + `user_activity_logs` → `activity_logs`)

| Table | Default Retention | Sensitive Data | Partition Key | Required From |
|---|---|---|---|---|
| `activity_logs` | 60 days | masked PII | `created_at` | L1+ |
| `system_logs` | 60 days | none | `created_at` | L1+ |
| `ai_call_logs` | 60 days | full content + tokens | `created_at` | L1+ |

ทั้ง 3 share pattern เดียวกัน: parent table + monthly partitions + retention job

### DB vs API JSON convention

- **DB columns:** `snake_case` (PostgreSQL convention)
- **API JSON keys:** `camelCase` (via `CamelBaseModel` with `alias_generator=to_camel`)

---

## 1. Activity Logs (Merged: audit + user_activity)

### 1.1 Concept

`activity_logs` รวม **3 ประเภทเหตุการณ์** ในตารางเดียว — แยกด้วย flags:

| Flag | คือ | ตัวอย่าง |
|---|---|---|
| `is_mutation` | user สร้าง/แก้/ลบ data | create product, update profile, delete file |
| `is_sensitive_read` | user ดู/export ข้อมูล sensitive | export CSV, view national ID, view PII |
| `is_security_event` | auth/security action | login success, login failed, permission denied, role change |

หนึ่ง row อาจมีหลาย flag true พร้อมกันได้ (เช่น admin update permission = `is_mutation` + `is_security_event`)

### 1.2 Data Model

```python
# app/db/models/activity_log.py
from datetime import datetime
from typing import Any, Literal
from uuid import UUID, uuid4

from sqlalchemy import Boolean, DateTime, Index, Integer, String, func
from sqlalchemy.dialects.postgresql import JSONB, UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class ActivityLog(Base):
    __tablename__ = "activity_logs"

    id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), default=uuid4)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __mapper_args__ = {"primary_key": [id, created_at]}

    # Actor
    user_id: Mapped[UUID | None] = mapped_column(PgUUID(as_uuid=True))
    user_email_masked: Mapped[str | None] = mapped_column(String(255))
    # e.g. "jo***@example.com"

    # Action
    action_type: Mapped[str] = mapped_column(String(30), nullable=False)
    # "create" | "update" | "delete" | "read_sensitive" | "export" |
    # "login" | "login_failed" | "logout" | "permission_denied" | "role_change"

    action: Mapped[str] = mapped_column(String(100), nullable=False)
    # e.g. "product.created", "user.viewed_pii", "report.exported_csv"

    # Target
    resource_type: Mapped[str | None] = mapped_column(String(50))
    resource_id: Mapped[str | None] = mapped_column(String(100))

    # Classification flags (DB snake_case → API camelCase via CamelBaseModel)
    is_mutation: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    is_sensitive_read: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    is_security_event: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    risk_level: Mapped[str] = mapped_column(String(10), default="low", nullable=False)
    # "low" | "medium" | "high" — admin filter

    # Request context
    ip_address: Mapped[str | None] = mapped_column(String(45))
    user_agent: Mapped[str | None] = mapped_column(String(500))
    request_id: Mapped[str | None] = mapped_column(String(64))
    endpoint: Mapped[str | None] = mapped_column(String(200))
    http_method: Mapped[str | None] = mapped_column(String(10))
    http_status: Mapped[int | None]

    # Diff / details (PII-masked)
    metadata: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)

    __table_args__ = (
        Index("ix_activity_user_created", "user_id", "created_at"),
        Index("ix_activity_action_type", "action_type", "created_at"),
        Index("ix_activity_resource", "resource_type", "resource_id"),
        Index("ix_activity_sensitive_created", "is_sensitive_read", "created_at"),
        Index("ix_activity_security_created", "is_security_event", "created_at"),
        Index("ix_activity_risk_created", "risk_level", "created_at"),
        {"postgresql_partition_by": "RANGE (created_at)"},
    )
```

### 1.3 Service

```python
# app/services/loggers/activity_logger.py
from typing import Any
from uuid import uuid4

from fastapi import Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.pii import mask_email
from app.db.models.activity_log import ActivityLog
from app.db.models.user import User


MUTATION_ACTIONS = {"create", "update", "delete"}
SENSITIVE_ACTIONS = {"read_sensitive", "export"}
SECURITY_ACTIONS = {"login", "login_failed", "logout", "permission_denied", "role_change"}


class ActivityLogger:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def log(
        self,
        *,
        user: User | None,
        action_type: str,
        action: str,
        resource_type: str | None = None,
        resource_id: str | None = None,
        risk_level: str = "low",
        metadata: dict[str, Any] | None = None,
        request: Request | None = None,
        http_status: int | None = None,
    ) -> None:
        entry = ActivityLog(
            id=uuid4(),
            user_id=user.id if user else None,
            user_email_masked=mask_email(user.email) if user else None,
            action_type=action_type,
            action=action,
            resource_type=resource_type,
            resource_id=resource_id,
            is_mutation=action_type in MUTATION_ACTIONS,
            is_sensitive_read=action_type in SENSITIVE_ACTIONS,
            is_security_event=action_type in SECURITY_ACTIONS,
            risk_level=risk_level,
            ip_address=request.client.host if request and request.client else None,
            user_agent=(request.headers.get("user-agent", "")[:500] if request else None),
            request_id=request.headers.get("x-request-id") if request else None,
            endpoint=str(request.url.path) if request else None,
            http_method=request.method if request else None,
            http_status=http_status,
            metadata=_scrub_pii(metadata or {}),
        )
        self.db.add(entry)
        # Caller commits

    async def log_login(
        self, *, user: User, success: bool, request: Request, failure_reason: str | None = None
    ) -> None:
        await self.log(
            user=user,
            action_type="login" if success else "login_failed",
            action=f"auth.login.{user.auth_provider}",
            risk_level="medium" if not success else "low",
            metadata={"reason": failure_reason} if failure_reason else {},
            request=request,
        )

    async def log_export(
        self, *, user: User, resource_type: str, format: str,
        count: int, filters: dict | None, request: Request,
    ) -> None:
        await self.log(
            user=user,
            action_type="export",
            action=f"{resource_type}.exported_{format}",
            resource_type=resource_type,
            risk_level="medium",
            metadata={"format": format, "count": count, "filters": filters or {}},
            request=request,
        )

    async def log_sensitive_read(
        self, *, user: User, resource_type: str, resource_id: str,
        fields_viewed: list[str], request: Request,
    ) -> None:
        await self.log(
            user=user,
            action_type="read_sensitive",
            action=f"{resource_type}.viewed_pii",
            resource_type=resource_type,
            resource_id=resource_id,
            risk_level="medium",
            metadata={"fields": fields_viewed},
            request=request,
        )

    async def log_permission_change(
        self, *, actor: User, target_user_id: str, change: str,
        details: dict, request: Request,
    ) -> None:
        """High-risk: role/permission grant/revoke"""
        await self.log(
            user=actor,
            action_type="role_change",
            action=f"permission.{change}",
            resource_type="user",
            resource_id=target_user_id,
            risk_level="high",
            metadata=details,
            request=request,
        )


def _scrub_pii(metadata: dict) -> dict:
    """Remove PII from metadata before storage."""
    sensitive_keys = {
        "password", "password_hash", "token", "access_token", "refresh_token",
        "credit_card", "national_id", "passport", "api_key", "secret",
    }
    scrubbed = {}
    for key, value in metadata.items():
        if key.lower() in sensitive_keys:
            scrubbed[key] = "***"
        elif isinstance(value, dict):
            scrubbed[key] = _scrub_pii(value)
        else:
            scrubbed[key] = value
    return scrubbed
```

### 1.4 Usage in Endpoint

```python
@router.post("/products", response_model=ProductRead, status_code=201)
async def create_product(
    payload: ProductCreate,
    db: DbDep,
    user: CurrentUser,
    request: Request,
) -> ProductRead:
    service = ProductService(db)
    product = await service.create_product(payload, user=user)

    activity_logger = ActivityLogger(db)
    await activity_logger.log(
        user=user,
        action_type="create",
        action="product.created",
        resource_type="product",
        resource_id=str(product.id),
        metadata={"sku": product.sku, "business_unit_id": str(product.business_unit_id)},
        request=request,
        http_status=201,
    )
    await db.commit()
    return ProductRead.model_validate(product)
```

**Note:** Phase 1.5 จะมี `@audited` decorator ที่ wire ActivityLogger อัตโนมัติ — ลด boilerplate

### 1.5 Sensitive Read Decorator

```python
# app/api/decorators.py
from collections.abc import Callable
from typing import Any

from fastapi import Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.user import User
from app.services.loggers.activity_logger import ActivityLogger


def log_sensitive_read(resource_type: str, fields: list[str]) -> Callable:
    """Auto-log sensitive reads. Decorated function must accept request, user, db kwargs."""
    def decorator(func: Callable) -> Callable:
        async def wrapper(*args: Any, **kwargs: Any) -> Any:
            request: Request | None = kwargs.get("request")
            user: User | None = kwargs.get("user")
            db: AsyncSession | None = kwargs.get("db")

            result = await func(*args, **kwargs)

            if user and db and request:
                resource_id = getattr(result, "id", None)
                logger = ActivityLogger(db)
                await logger.log_sensitive_read(
                    user=user,
                    resource_type=resource_type,
                    resource_id=str(resource_id) if resource_id else "unknown",
                    fields_viewed=fields,
                    request=request,
                )
                await db.commit()

            return result
        return wrapper
    return decorator


# Usage
@router.get("/users/{user_id}/personal-info")
@log_sensitive_read("user", fields=["national_id", "phone", "address"])
async def get_user_personal_info(
    user_id: str, db: DbDep, user: CurrentUser, request: Request
) -> UserPersonalInfoRead:
    # implement: fetch and return personal info for user_id
    ...
```

---

## 2. System Logs

### 2.1 Data Model

```python
# app/db/models/system_log.py
from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import DateTime, Index, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class SystemLog(Base):
    __tablename__ = "system_logs"

    id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), default=uuid4)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    __mapper_args__ = {"primary_key": [id, created_at]}

    category: Mapped[str] = mapped_column(String(30), nullable=False)
    # "job" | "integration" | "scheduler" | "system" | "migration"
    event: Mapped[str] = mapped_column(String(100), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    # "started" | "success" | "failure" | "warning" | "info"

    metadata: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)

    duration_ms: Mapped[int | None]
    error_message: Mapped[str | None] = mapped_column(Text)
    error_type: Mapped[str | None] = mapped_column(String(100))
    correlation_id: Mapped[str | None] = mapped_column(String(64))

    __table_args__ = (
        Index("ix_system_logs_created", "created_at"),
        Index("ix_system_logs_category_event", "category", "event"),
        Index("ix_system_logs_status_created", "status", "created_at"),
        {"postgresql_partition_by": "RANGE (created_at)"},
    )
```

### 2.2 Service

```python
# app/services/loggers/system_logger.py
import time
from typing import Any
from uuid import uuid4

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.system_log import SystemLog


class SystemLogger:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def log_job(
        self, *, job_name: str, status: str,
        duration_ms: int | None = None, error: Exception | None = None,
        metadata: dict[str, Any] | None = None, correlation_id: str | None = None,
    ) -> None:
        await self._log(
            category="job", event=f"job.{status}", status=status,
            duration_ms=duration_ms, error=error,
            metadata={"job_name": job_name, **(metadata or {})},
            correlation_id=correlation_id,
        )

    async def log_integration(
        self, *, provider: str, operation: str, status: str,
        duration_ms: int | None = None, error: Exception | None = None,
        metadata: dict[str, Any] | None = None, correlation_id: str | None = None,
    ) -> None:
        await self._log(
            category="integration", event=f"integration.{provider}.{operation}", status=status,
            duration_ms=duration_ms, error=error,
            metadata={"provider": provider, "operation": operation, **(metadata or {})},
            correlation_id=correlation_id,
        )

    async def log_system_event(
        self, *, event: str, status: str = "info",
        metadata: dict[str, Any] | None = None,
    ) -> None:
        await self._log(category="system", event=event, status=status, metadata=metadata or {})

    async def _log(
        self, *, category: str, event: str, status: str, metadata: dict[str, Any],
        duration_ms: int | None = None, error: Exception | None = None,
        correlation_id: str | None = None,
    ) -> None:
        entry = SystemLog(
            id=uuid4(),
            category=category, event=event, status=status,
            metadata=metadata, duration_ms=duration_ms,
            error_message=str(error)[:2000] if error else None,
            error_type=type(error).__name__ if error else None,
            correlation_id=correlation_id,
        )
        self.db.add(entry)
        # Caller commits
```

### 2.3 Usage in Background Job

```python
async def cleanup_expired_sessions(db: AsyncSession) -> None:
    logger = SystemLogger(db)
    started_at = time.monotonic()
    correlation_id = str(uuid4())

    await logger.log_job(job_name="cleanup_expired_sessions", status="started",
                        correlation_id=correlation_id)
    await db.commit()

    try:
        from sqlalchemy import text
        result = await db.execute(text("DELETE FROM sessions WHERE expires_at < NOW()"))
        await logger.log_job(
            job_name="cleanup_expired_sessions", status="success",
            duration_ms=int((time.monotonic() - started_at) * 1000),
            metadata={"deleted_count": result.rowcount},
            correlation_id=correlation_id,
        )
        await db.commit()
    except Exception as exc:
        await logger.log_job(
            job_name="cleanup_expired_sessions", status="failure",
            duration_ms=int((time.monotonic() - started_at) * 1000),
            error=exc, correlation_id=correlation_id,
        )
        await db.commit()
        raise
```

---

## 3. AI Call Logs

### 3.1 Data Model

```python
# app/db/models/ai_call_log.py
from datetime import datetime
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import DateTime, Index, Integer, Numeric, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class AiCallLog(Base):
    __tablename__ = "ai_call_logs"

    id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), default=uuid4)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    __mapper_args__ = {"primary_key": [id, created_at]}

    user_id: Mapped[UUID | None] = mapped_column(PgUUID(as_uuid=True))
    endpoint: Mapped[str | None] = mapped_column(String(200))
    request_id: Mapped[str | None] = mapped_column(String(64))

    provider: Mapped[str] = mapped_column(String(20), nullable=False, default="anthropic")
    model: Mapped[str] = mapped_column(String(50), nullable=False)
    operation: Mapped[str] = mapped_column(String(30), nullable=False)
    # "messages" | "embeddings" | "completions"

    # Content (PII-masked)
    prompt: Mapped[str] = mapped_column(Text, nullable=False)
    system_prompt: Mapped[str | None] = mapped_column(Text)
    response: Mapped[str | None] = mapped_column(Text)

    # Metrics
    input_tokens: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    cache_read_tokens: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    cache_write_tokens: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    cost_usd: Mapped[Decimal | None] = mapped_column(Numeric(10, 6))
    duration_ms: Mapped[int | None]

    # Status
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    # "success" | "error" | "timeout" | "rate_limited" | "budget_exceeded"
    error_type: Mapped[str | None] = mapped_column(String(100))
    error_message: Mapped[str | None] = mapped_column(Text)

    # Use case label, cache hit, prompt template version, etc.
    metadata: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)

    __table_args__ = (
        Index("ix_ai_logs_created", "created_at"),
        Index("ix_ai_logs_user_created", "user_id", "created_at"),
        Index("ix_ai_logs_model_created", "model", "created_at"),
        Index("ix_ai_logs_status_created", "status", "created_at"),
        {"postgresql_partition_by": "RANGE (created_at)"},
    )
```

### 3.2 Service

```python
# app/services/loggers/ai_call_logger.py
from decimal import Decimal
from typing import Any
from uuid import uuid4

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.pii import mask_pii_in_text
from app.db.models.ai_call_log import AiCallLog
from app.db.models.user import User
from app.services.app_setting_service import AppSettingService


class AiCallLogger:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def log(
        self, *,
        user: User | None,
        model: str,
        operation: str,
        prompt: str,
        system_prompt: str | None = None,
        response: str | None = None,
        input_tokens: int = 0,
        output_tokens: int = 0,
        cache_read_tokens: int = 0,
        cache_write_tokens: int = 0,
        duration_ms: int | None = None,
        status: str = "success",
        error: Exception | None = None,
        endpoint: str | None = None,
        request_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        cost = await self._estimate_cost(
            model, input_tokens, output_tokens, cache_read_tokens, cache_write_tokens
        )
        entry = AiCallLog(
            id=uuid4(),
            user_id=user.id if user else None,
            endpoint=endpoint,
            request_id=request_id,
            provider="anthropic",
            model=model,
            operation=operation,
            prompt=mask_pii_in_text(prompt),
            system_prompt=mask_pii_in_text(system_prompt) if system_prompt else None,
            response=mask_pii_in_text(response) if response else None,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cache_read_tokens=cache_read_tokens,
            cache_write_tokens=cache_write_tokens,
            cost_usd=cost,
            duration_ms=duration_ms,
            status=status,
            error_type=type(error).__name__ if error else None,
            error_message=str(error)[:2000] if error else None,
            metadata=metadata or {},
        )
        self.db.add(entry)
        # Caller commits

    async def _estimate_cost(
        self, model: str,
        input_tokens: int, output_tokens: int,
        cache_read_tokens: int, cache_write_tokens: int,
    ) -> Decimal | None:
        """Read pricing from app_settings (seeded, super-admin only edit).

        Setting key: ai.pricing.<model>
        Value: {"input": 3.0, "output": 15.0, "cache_read": 0.30, "cache_write": 3.75}
        Units: USD per 1M tokens
        """
        settings = AppSettingService(self.db)
        pricing = await settings.get(f"ai.pricing.{model}")
        if not pricing:
            return None
        cost = (
            Decimal(str(pricing.get("input", 0))) * Decimal(input_tokens)
            + Decimal(str(pricing.get("output", 0))) * Decimal(output_tokens)
            + Decimal(str(pricing.get("cache_read", 0))) * Decimal(cache_read_tokens)
            + Decimal(str(pricing.get("cache_write", 0))) * Decimal(cache_write_tokens)
        ) / Decimal(1_000_000)
        return cost.quantize(Decimal("0.000001"))
```

> **Pricing source:** seeded ใน `app_settings` (Pattern C) — admin restricted to super-admin role. ห้าม hardcode pricing dict ใน code (ดู AGENTS.md §16 + [`docs/patterns/ai.md`](patterns/ai.md))

### 3.3 Usage — Wrap Every Claude Call

```python
# app/integrations/claude_ai.py
import time

from anthropic import AsyncAnthropic
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.services.loggers.ai_call_logger import AiCallLogger


client = AsyncAnthropic(api_key=get_settings().CLAUDE_API_KEY)


async def call_claude_messages(
    *,
    prompt: str,
    system_prompt: str | None = None,
    user: User | None,
    db: AsyncSession,
    endpoint: str | None = None,
    request_id: str | None = None,
    use_case: str | None = None,
) -> str:
    settings = get_settings()
    model = settings.CLAUDE_MODEL
    started_at = time.monotonic()
    logger = AiCallLogger(db)

    try:
        response = await client.messages.create(
            model=model,
            max_tokens=4096,
            system=system_prompt or "",
            messages=[{"role": "user", "content": prompt}],
        )
        duration_ms = int((time.monotonic() - started_at) * 1000)
        response_text = response.content[0].text if response.content else ""

        await logger.log(
            user=user, model=model, operation="messages",
            prompt=prompt, system_prompt=system_prompt, response=response_text,
            input_tokens=response.usage.input_tokens,
            output_tokens=response.usage.output_tokens,
            cache_read_tokens=getattr(response.usage, "cache_read_input_tokens", 0) or 0,
            cache_write_tokens=getattr(response.usage, "cache_creation_input_tokens", 0) or 0,
            duration_ms=duration_ms, status="success",
            endpoint=endpoint, request_id=request_id,
            metadata={"use_case": use_case} if use_case else {},
        )
        await db.commit()
        return response_text

    except Exception as exc:
        duration_ms = int((time.monotonic() - started_at) * 1000)
        status = "error"
        msg = str(exc).lower()
        if "rate" in msg:
            status = "rate_limited"
        elif "timeout" in msg:
            status = "timeout"

        await logger.log(
            user=user, model=model, operation="messages",
            prompt=prompt, system_prompt=system_prompt,
            duration_ms=duration_ms, status=status, error=exc,
            endpoint=endpoint, request_id=request_id,
            metadata={"use_case": use_case} if use_case else {},
        )
        await db.commit()
        raise
```

### 3.4 Hard Rule: No Direct SDK Calls

ห้ามเรียก `AsyncAnthropic` / `OpenAI` / provider SDK ตรงๆ จาก service หรือ endpoint — ต้องผ่าน `app/integrations/<provider>.py` ที่ log + cache + budget-check อัตโนมัติ

Phase 1.5 lint rule: grep `AsyncAnthropic\(` หรือ `client.messages.create` outside `app/integrations/` → fail CI

---

## 4. PII Masking

```python
# app/core/pii.py
import re

EMAIL_PATTERN = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b")
PHONE_PATTERN = re.compile(r"\b0[0-9]{8,9}\b|\b\+?66[0-9]{8,9}\b")
THAI_ID_PATTERN = re.compile(r"\b[0-9]{13}\b")


def mask_email(email: str) -> str:
    if "@" not in email:
        return "***"
    local, domain = email.split("@", 1)
    if len(local) <= 2:
        return f"**@{domain}"
    return f"{local[:2]}***@{domain}"


def mask_phone(phone: str) -> str:
    digits = "".join(c for c in phone if c.isdigit())
    if len(digits) < 4:
        return "***"
    return f"***{digits[-4:]}"


def mask_pii_in_text(text: str) -> str:
    """Mask PII patterns in free-form text before logging.

    Used for AI prompts/responses that may contain PII unintentionally.
    """
    text = EMAIL_PATTERN.sub(lambda m: mask_email(m.group(0)), text)
    text = PHONE_PATTERN.sub(lambda m: mask_phone(m.group(0)), text)
    text = THAI_ID_PATTERN.sub("***-****-****-**", text)
    return text
```

Phase 1.5: `LoggerWrapper` ใช้ `mask_pii_in_text()` auto-default — AI ไม่ต้อง remember

---

## 5. Partition Management

Partitions ต้องสร้างล่วงหน้า (รายเดือน) — ถ้าไม่มี partition รองรับช่วงเวลานั้น INSERT จะ fail

```python
# app/services/loggers/partition_manager.py
from datetime import date

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


PARTITIONED_TABLES = ["activity_logs", "system_logs", "ai_call_logs"]


async def ensure_partitions_exist(db: AsyncSession, months_ahead: int = 2) -> None:
    """Create partitions for current month + N months ahead. Idempotent."""
    today = date.today()
    start_of_current = today.replace(day=1)

    for offset in range(months_ahead + 1):
        year = start_of_current.year + (start_of_current.month - 1 + offset) // 12
        month = (start_of_current.month - 1 + offset) % 12 + 1
        partition_start = date(year, month, 1)

        next_year = year + (month // 12)
        next_month = month % 12 + 1
        partition_end = date(next_year, next_month, 1)

        for table in PARTITIONED_TABLES:
            partition_name = f"{table}_{partition_start.strftime('%Y_%m')}"
            await db.execute(text(f"""
                CREATE TABLE IF NOT EXISTS {partition_name}
                PARTITION OF {table}
                FOR VALUES FROM ('{partition_start}') TO ('{partition_end}')
            """))

    await db.commit()
```

### Schedule

```python
scheduler.add_job(
    _ensure_partitions,
    trigger=CronTrigger(day=25, hour=2, minute=0),  # 25th of each month
    id="ensure_log_partitions",
    replace_existing=True,
)
```

⚠️ **Initial setup:** รัน `ensure_partitions_exist()` ตอน first deploy (ไม่ใช่รอ scheduler) — เพิ่มใน `init_db()` หรือ migration

---

## 6. Retention Job

```python
# app/services/loggers/retention.py
from datetime import date, timedelta

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.app_setting_service import AppSettingService


DEFAULT_RETENTION_DAYS = 60

RETENTION_KEYS = {
    "activity_logs": "logging.activity.retention_days",
    "system_logs": "logging.system.retention_days",
    "ai_call_logs": "logging.ai.retention_days",
}


async def drop_old_partitions(db: AsyncSession) -> dict[str, list[str]]:
    """Drop partitions older than retention period. Returns dropped partition names."""
    settings_service = AppSettingService(db)
    today = date.today()
    dropped: dict[str, list[str]] = {}

    for table, setting_key in RETENTION_KEYS.items():
        retention_days = await settings_service.get(setting_key, DEFAULT_RETENTION_DAYS)
        cutoff = today - timedelta(days=retention_days)

        result = await db.execute(text("""
            SELECT inhrelid::regclass::text AS partition_name
            FROM pg_inherits
            WHERE inhparent = :parent::regclass
        """), {"parent": table})

        dropped[table] = []
        for row in result.all():
            partition_name = row[0]
            try:
                suffix = partition_name.rsplit("_", 2)
                year = int(suffix[-2])
                month = int(suffix[-1])
                partition_end = date(year + (month // 12), month % 12 + 1, 1)
                if partition_end <= cutoff:
                    await db.execute(text(f"DROP TABLE IF EXISTS {partition_name}"))
                    dropped[table].append(partition_name)
            except (ValueError, IndexError):
                continue

    await db.commit()
    return dropped
```

### Schedule

```python
scheduler.add_job(
    _run_retention,
    trigger=CronTrigger(hour=3, minute=0),  # daily at 3 AM
    id="log_retention",
    replace_existing=True,
)
```

**Alert:** ถ้า `log_retention` fail 3 วันติด → P2 alert (configurable via Prometheus/Sentry rules)

---

## 7. Admin Query Patterns

```sql
-- Most active users (last 30 days)
SELECT user_id, user_email_masked, COUNT(*) AS event_count
FROM activity_logs
WHERE created_at > NOW() - INTERVAL '30 days'
GROUP BY user_id, user_email_masked
ORDER BY event_count DESC
LIMIT 50;

-- AI cost per user (current month)
SELECT user_id, SUM(cost_usd) AS total_cost, SUM(input_tokens + output_tokens) AS total_tokens
FROM ai_call_logs
WHERE created_at >= date_trunc('month', CURRENT_DATE)
GROUP BY user_id
ORDER BY total_cost DESC;

-- Failed integration calls (last 24h)
SELECT event, COUNT(*) AS failures, MAX(created_at) AS last_failure
FROM system_logs
WHERE category = 'integration' AND status = 'failure'
  AND created_at > NOW() - INTERVAL '24 hours'
GROUP BY event ORDER BY failures DESC;

-- Sensitive data access (compliance review — PDPA)
SELECT user_id, user_email_masked, action, resource_type, resource_id, created_at
FROM activity_logs
WHERE is_sensitive_read = TRUE
  AND created_at > NOW() - INTERVAL '7 days'
ORDER BY created_at DESC;

-- High-risk security events (last 24h)
SELECT user_id, action, resource_id, risk_level, ip_address, created_at
FROM activity_logs
WHERE is_security_event = TRUE AND risk_level = 'high'
  AND created_at > NOW() - INTERVAL '24 hours'
ORDER BY created_at DESC;

-- AI cache hit rate (current month)
SELECT
  COUNT(*) AS total_calls,
  SUM(cache_read_tokens) AS cache_read,
  SUM(cache_write_tokens) AS cache_write,
  SUM(input_tokens) AS input_total,
  ROUND(100.0 * SUM(cache_read_tokens) / NULLIF(SUM(input_tokens), 0), 2) AS cache_hit_pct
FROM ai_call_logs
WHERE created_at >= date_trunc('month', CURRENT_DATE);
```

---

## 8. Hard Rules

1. ห้ามเรียก Claude SDK ตรงๆ — ต้องผ่าน `app/integrations/claude_ai.py` ที่ log อัตโนมัติ
2. ทุก mutation endpoint ต้อง wire `ActivityLogger.log()` (Phase 1.5: `@audited` decorator บังคับ)
3. ทุก export/sensitive read ต้อง wire `log_export()` หรือ `log_sensitive_read()` (หรือ `@log_sensitive_read` decorator)
4. ทุก background job ต้อง wire `SystemLogger.log_job()` start + end
5. ทุก integration call ต้อง wire `SystemLogger.log_integration()`
6. PII ต้อง mask ก่อน insert — ใช้ `mask_pii_in_text()` หรือ `_scrub_pii()` เสมอ
7. Partition ต้องสร้าง 2 เดือนล่วงหน้า — ห้ามให้ขาด
8. Retention job ต้องรันทุกวัน — fail 3 วันติด = alert
9. ห้าม store secret ใน `ai_call_logs.prompt` — ตรวจ prompt template ก่อน production
10. ห้าม hardcode `MODEL_PRICING` — อ่านจาก `app_settings` key `ai.pricing.<model>`

---

## 9. Quick Reference

| Task | What to wire |
|---|---|
| New mutation endpoint | `ActivityLogger.log(action_type="create/update/delete", ...)` |
| New export endpoint | `ActivityLogger.log_export(...)` |
| Endpoint viewing PII | `ActivityLogger.log_sensitive_read(...)` หรือ `@log_sensitive_read` decorator |
| Auth event (login/logout) | `ActivityLogger.log_login(...)` |
| Permission/role change | `ActivityLogger.log_permission_change(...)` (risk_level=high) |
| New background job | `SystemLogger.log_job()` start + end + error |
| New integration call | `SystemLogger.log_integration()` success + error |
| AI call | Use `call_claude_messages()` wrapper — auto-logs |
| Query logs | Use admin endpoints in `/api/v1/admin/logs/*` |

---

## Migration from v2.x (4 tables → 3 tables)

ถ้า project ใช้ v2.x อยู่แล้ว ดู [`MIGRATION.md`](../MIGRATION.md) — ไม่ apply กับ project ใหม่ที่ start จาก v3.0
