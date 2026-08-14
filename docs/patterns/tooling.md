# docs/patterns/tooling.md

> Tooling Foundation — make AGENTS.md §B Enforcement Matrix "ทำได้จริง"
>
> Phase 1.5 deliverables. Reference implementations + integration points

---

## Overview

หลักการ v3.0: **ย้าย enforcement จาก docs → tooling**

AI ไม่ต้องจำกฎทุกข้อ — tooling จับเองอัตโนมัติ ที่ commit / lint / type / runtime

| Layer | What it enforces |
|---|---|
| **pre-commit hooks** | secrets, real values in examples, large files, formatting |
| **Custom checks** | no direct AI SDK calls, no real values in `*.example` |
| **Ruff lint** | code style + import hygiene |
| **Decorators** | `@audited` — mutation must log activity |
| **Wrappers** | `StructuredLogger` — PII auto-mask |
| **Type system** | `CamelBaseModel` parent → camelCase JSON automatic |
| **Templates** | `Dockerfile` non-root user, `SecurityHeadersMiddleware`, `decode_jwt()` |
| **CI gates** | Dependabot, pip-audit, npm audit, Trivy |

---

## 1. Pre-commit Hooks

### 1.1 Setup

```bash
# Install pre-commit
pip install pre-commit

# Install hooks for this repo
pre-commit install

# Run manually against all files (first time)
pre-commit run --all-files
```

### 1.2 Config

ดู [`.pre-commit-config.yaml`](../../.pre-commit-config.yaml) ที่ repo root

Includes:
- **gitleaks** — secret detection in commits
- **detect-secrets** — baseline tracking
- **pre-commit-hooks** — yaml/json/toml validation, EOF, trailing whitespace
- **ruff** — Python lint + format
- **frontend-typecheck** — TypeScript check (changed files only)
- **Custom CT checks** (see §2)

### 1.3 Secret Detection — `.secrets.baseline`

First-time setup (if migrating existing project):

```bash
# Generate baseline (allow existing low-risk strings)
pip install detect-secrets
detect-secrets scan > .secrets.baseline

# Review baseline manually — audit each finding
detect-secrets audit .secrets.baseline
```

Commit `.secrets.baseline` — future commits compare against it

---

## 2. Custom CT Checks

### 2.1 No Real Secrets in `*.example`

**File:** [`scripts/checks/no_real_secrets_in_examples.py`](../../scripts/checks/no_real_secrets_in_examples.py)

**Enforces:** AGENTS.md §3 rule 6

ตรวจ `.env.example` / `project.config.example` ห้ามมี real values — ใช้ placeholder เท่านั้น:

```bash
# ✅ Allowed
API_KEY=
API_KEY=<your-api-key-here>
AZURE_AD_CLIENT_ID=00000000-0000-0000-0000-000000000000
JWT_SECRET=changeme

# ❌ Blocked
API_KEY=sk-ant-api03-abc123...
JWT_SECRET=eyJhbGc...
GITHUB_TOKEN=ghp_xxxxxxxxxxxxxxxxxxxx
```

Run manually:
```bash
python scripts/checks/no_real_secrets_in_examples.py backend/.env.example project.config.example
```

### 2.2 No Direct AI SDK Calls

**File:** [`scripts/checks/no_direct_ai_sdk.py`](../../scripts/checks/no_direct_ai_sdk.py)

**Enforces:** AGENTS.md §16 Hard Rule + Enforcement Matrix

ตรวจว่า import ของ `anthropic` / `openai` SDK ต้องอยู่ใน `backend/app/integrations/` เท่านั้น:

```python
# ❌ Blocked in app/services/ or app/api/
from anthropic import AsyncAnthropic
import openai

# ✅ Allowed in app/integrations/
# backend/app/integrations/anthropic_provider.py
from anthropic import AsyncAnthropic  # OK here
```

ที่อื่นต้องผ่าน `app/integrations/ai/` (ดู [`ai.md`](ai.md) §1.3 factory)

### 2.3 CamelBaseModel Audit

**File:** [`scripts/checks/camel_base_model_audit.py`](../../scripts/checks/camel_base_model_audit.py)

**Enforces:** AGENTS.md §12 (camelCase JSON / snake_case DB) + §B Enforcement Matrix

ตรวจว่าทุก class ใน `backend/app/schemas/` ต้อง inherit `CamelBaseModel` — ห้าม inherit raw `BaseModel` ตรงๆ ยกเว้น `app/schemas/base.py` (file ที่นิยาม CamelBaseModel เอง):

```python
# ❌ Blocked — bypasses camelCase aliasing
from pydantic import BaseModel
class ProductRead(BaseModel): ...

# ✅ Allowed — auto camelCase JSON via alias_generator
from app.schemas.base import CamelBaseModel
class ProductRead(CamelBaseModel): ...
```

CamelBaseModel นิยาม `alias_generator=to_camel` + `populate_by_name=True` + `from_attributes=True` ที่จุดเดียว — ทุก schema ที่ inherit จะได้ความสามารถนี้ครบ

### 2.4 No `dict` Param in Endpoint

**File:** [`scripts/checks/no_dict_in_endpoint.py`](../../scripts/checks/no_dict_in_endpoint.py)

**Enforces:** AGENTS.md §B Enforcement Matrix (`No dict param in endpoint`)

ตรวจว่า function ใน `backend/app/api/` ที่ decorated ด้วย `@router.<method>` / `@app.<method>` ห้ามมี parameter annotated เป็น `dict` หรือ `dict[...]` — ต้องใช้ Pydantic model (CamelBaseModel subclass) เสมอ:

```python
# ❌ Blocked — no validation, no OpenAPI schema
@router.post("/products")
async def create(payload: dict) -> dict: ...

@router.put("/products/{pid}")
async def replace(pid: int, payload: dict[str, Any]) -> dict: ...

# ✅ Allowed
@router.post("/products", response_model=ProductRead)
async def create(payload: ProductCreate) -> ProductRead: ...
```

หมายเหตุ: ตรวจเฉพาะ parameter — ไม่ตรวจ return-type annotation (return อาจเป็น dict ในเส้นทาง response อื่นๆ ที่ไม่ใช่ Pydantic schema)

---

## 3. `@audited` Decorator

### 3.1 Purpose

แทนที่จะให้ AI จำว่า "ทุก mutation ต้อง wire ActivityLogger" — ใช้ decorator ที่ทำให้:
- Compile-time check: endpoint ต้องตกแต่งด้วย `@audited(...)` ถึง pass code review
- Runtime: auto-wire `ActivityLogger.log()` หลัง endpoint success

### 3.2 Implementation

```python
# backend/app/api/decorators.py
from collections.abc import Callable
from functools import wraps
from typing import Any

from fastapi import Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.user import User
from app.services.loggers.activity_logger import ActivityLogger


def audited(
    *,
    action: str,
    action_type: str = "update",
    resource_type: str | None = None,
    risk_level: str = "low",
    extract_resource_id: Callable[[Any], str | None] | None = None,
):
    """Auto-log activity after successful endpoint execution.

    Decorated endpoint must accept `request: Request`, `user: CurrentUser`, `db: DbDep`
    as keyword arguments (FastAPI dependency injection).

    Args:
        action: Activity action name (e.g. "product.created")
        action_type: "create" | "update" | "delete" | "read_sensitive" | "export" |
                     "login" | "login_failed" | "logout" | "permission_denied" | "role_change"
        resource_type: e.g. "product", "user", "permission"
        risk_level: "low" | "medium" | "high"
        extract_resource_id: callable that takes result and returns resource_id;
                            default = result.id if present, else None

    Example:
        @router.post("/products", status_code=201)
        @audited(action="product.created", action_type="create", resource_type="product")
        async def create_product(payload, db, user, request):
            ...
    """

    def decorator(func: Callable) -> Callable:
        @wraps(func)
        async def wrapper(*args: Any, **kwargs: Any) -> Any:
            request: Request | None = kwargs.get("request")
            user: User | None = kwargs.get("user")
            db: AsyncSession | None = kwargs.get("db")

            result = await func(*args, **kwargs)

            # Wire ActivityLogger if dependencies present
            if user is not None and db is not None:
                # Extract resource_id
                if extract_resource_id is not None:
                    resource_id = extract_resource_id(result)
                else:
                    rid = getattr(result, "id", None)
                    resource_id = str(rid) if rid is not None else None

                logger = ActivityLogger(db)
                await logger.log(
                    user=user,
                    action_type=action_type,
                    action=action,
                    resource_type=resource_type,
                    resource_id=resource_id,
                    risk_level=risk_level,
                    request=request,
                )
                await db.commit()

            return result

        return wrapper

    return decorator
```

### 3.3 Usage

```python
from app.api.decorators import audited


@router.post("/products", response_model=ProductRead, status_code=201)
@audited(
    action="product.created",
    action_type="create",
    resource_type="product",
)
async def create_product(
    payload: ProductCreate,
    db: DbDep,
    user: CurrentUser,
    request: Request,
) -> ProductRead:
    service = ProductService(db)
    product = await service.create_product(payload, user=user)
    return ProductRead.model_validate(product)


@router.delete("/products/{product_id}", status_code=204)
@audited(
    action="product.deleted",
    action_type="delete",
    resource_type="product",
    risk_level="medium",
    extract_resource_id=lambda _: product_id,  # captured from path param
)
async def delete_product(
    product_id: str,
    db: DbDep,
    user: CurrentUser,
    request: Request,
) -> None:
    ...


@router.post("/admin/permissions/{user_id}/grant")
@audited(
    action="permission.granted",
    action_type="role_change",
    resource_type="user",
    risk_level="high",  # security-sensitive
)
async def grant_permission(
    user_id: str,
    payload: PermissionGrantRequest,
    db: DbDep,
    user: CurrentUser,
    request: Request,
) -> dict:
    ...
```

### 3.4 Code Review Rule

PR ที่มี mutation endpoint (POST/PUT/PATCH/DELETE) **ไม่มี `@audited`** → reject

Phase 2 (future): custom ruff plugin auto-detect missing `@audited` — for now manual review

---

## 4. `StructuredLogger` PII Auto-Mask

### 4.1 Purpose

`structlog.get_logger().info("user_login", email=user.email)` ตอนนี้ AI ต้อง remember mask email
→ ห่อด้วย wrapper ที่ auto-mask sensitive keys

### 4.2 Implementation

```python
# backend/app/core/logging.py (เพิ่มจาก v2.x setup_logging)
import logging
import sys
from typing import Any

import structlog

from app.core.pii import mask_email, mask_phone, mask_pii_in_text


SENSITIVE_KEYS = {
    "password", "password_hash", "token", "access_token", "refresh_token",
    "api_key", "secret", "client_secret", "credit_card", "national_id",
    "passport", "jwt", "auth_header",
}

EMAIL_KEYS = {"email", "user_email", "from_email", "to_email"}
PHONE_KEYS = {"phone", "phone_number", "mobile", "tel"}

# Threshold above which to scan free-form text for PII
TEXT_SCAN_MIN_LENGTH = 50


class StructuredLogger:
    """Wrapper around structlog that auto-masks PII / secrets in kwargs.

    Use via `from app.core.logging import get_logger` — NOT `structlog.get_logger()` directly
    """

    def __init__(self, logger: Any) -> None:
        self._logger = logger

    def _mask_kwargs(self, kwargs: dict[str, Any]) -> dict[str, Any]:
        masked = {}
        for key, value in kwargs.items():
            key_lower = key.lower()
            if key_lower in SENSITIVE_KEYS:
                masked[key] = "***"
            elif key_lower in EMAIL_KEYS and isinstance(value, str):
                masked[key] = mask_email(value)
            elif key_lower in PHONE_KEYS and isinstance(value, str):
                masked[key] = mask_phone(value)
            elif isinstance(value, str) and len(value) >= TEXT_SCAN_MIN_LENGTH:
                # Free-form text — scan + mask any embedded PII
                masked[key] = mask_pii_in_text(value)
            elif isinstance(value, dict):
                masked[key] = self._mask_kwargs(value)
            else:
                masked[key] = value
        return masked

    def info(self, event: str, **kwargs: Any) -> None:
        self._logger.info(event, **self._mask_kwargs(kwargs))

    def warning(self, event: str, **kwargs: Any) -> None:
        self._logger.warning(event, **self._mask_kwargs(kwargs))

    def error(self, event: str, **kwargs: Any) -> None:
        self._logger.error(event, **self._mask_kwargs(kwargs))

    def debug(self, event: str, **kwargs: Any) -> None:
        self._logger.debug(event, **self._mask_kwargs(kwargs))

    def exception(self, event: str, **kwargs: Any) -> None:
        self._logger.exception(event, **self._mask_kwargs(kwargs))

    def bind(self, **kwargs: Any) -> "StructuredLogger":
        """Bind context (mask first)"""
        return StructuredLogger(self._logger.bind(**self._mask_kwargs(kwargs)))


def setup_logging(level: str = "INFO") -> None:
    """Configure structlog. Call once at app startup."""
    timestamper = structlog.processors.TimeStamper(fmt="iso", utc=True)
    shared_processors = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        timestamper,
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
    ]
    structlog.configure(
        processors=shared_processors + [structlog.processors.JSONRenderer()],
        wrapper_class=structlog.make_filtering_bound_logger(
            getattr(logging, level.upper())
        ),
        context_class=dict,
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )


def get_logger(name: str | None = None) -> StructuredLogger:
    """Get a PII-masking logger. Use this instead of structlog.get_logger() directly."""
    return StructuredLogger(structlog.get_logger(name))
```

### 4.3 Usage

```python
from app.core.logging import get_logger

logger = get_logger(__name__)

# These auto-mask:
logger.info("user_login", email=user.email, phone=user.phone)
# Output: {"event": "user_login", "email": "jo***@example.com", "phone": "***1234"}

logger.info("api_call", api_key=settings.CLAUDE_API_KEY)
# Output: {"event": "api_call", "api_key": "***"}

logger.error("invalid_input", input_text=very_long_user_input)
# Output: input_text has PII patterns masked (emails, phones, Thai national IDs)
```

### 4.4 Migration from v2.x

```python
# Find/replace
- import structlog
- logger = structlog.get_logger()

+ from app.core.logging import get_logger
+ logger = get_logger(__name__)
```

---

## 5. CamelBaseModel — Schema Default

ทุก Pydantic schema ที่ใช้กับ API ต้อง inherit `CamelBaseModel` → camelCase JSON อัตโนมัติ

```python
# backend/app/schemas/common.py (มีอยู่แล้วใน v2.x)
from pydantic import BaseModel, ConfigDict
from pydantic.alias_generators import to_camel


class CamelBaseModel(BaseModel):
    """Base for API schemas: camelCase JSON, snake_case Python."""

    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
        from_attributes=True,
    )
```

**Phase 1.5 audit:** ตรวจว่า schemas ทั้งหมดใน `app/schemas/` inherit `CamelBaseModel` — ถ้าใช้ `BaseModel` ตรงๆ → flag ใน code review

Phase 2 (future): ruff plugin `B-001 schemas must inherit CamelBaseModel`

---

## 6. Template-Enforced Rules (มีอยู่แล้ว ใน scaffold)

### 6.1 Dockerfile

Scaffold generates Dockerfile ที่ใช้ non-root user:

```dockerfile
# Final stage
FROM python:3.12-slim
RUN useradd -m -u 1000 appuser
USER appuser
# ...
```

### 6.2 SecurityHeadersMiddleware

`app/main.py` ใช้ `SecurityHeadersMiddleware` — เพิ่ม headers ทุก response (HSTS, CSP, X-Frame-Options ฯลฯ)

### 6.3 Settings validator

`app/core/config.py` มี `field_validator` ที่ block `allow_origins=["*"]` ใน production:

```python
@field_validator("API_CORS_ORIGINS")
@classmethod
def validate_cors(cls, v: list[str], info: ValidationInfo) -> list[str]:
    env = info.data.get("APP_ENV")
    if env == "production" and "*" in v:
        raise ValueError("CORS allow_origins cannot be ['*'] in production")
    return v
```

### 6.4 JWT helper

`app/core/security.py` `decode_jwt()` enforce algorithm:

```python
def decode_jwt(token: str, secret: str, algorithm: str = "HS256") -> dict:
    if algorithm.lower() in ("none", ""):
        raise ValueError("JWT algorithm 'none' is forbidden")
    return jwt.decode(token, secret, algorithms=[algorithm])
```

---

## 7. CI Gates

CI workflows (GitHub Actions / GitLab CI / Azure DevOps) — ดู [`../cicd.md`](../cicd.md)

| Gate | Tool | Blocks |
|---|---|---|
| Secrets scan | gitleaks | Real secrets in commits |
| Python deps | pip-audit + Dependabot | Critical CVEs |
| JS deps | npm audit + Dependabot | Critical CVEs |
| Container scan | Trivy | Critical CVEs in image |
| SAST | Bandit (Python) | Common Python security antipatterns |
| Lint | ruff + eslint | Style + import hygiene |
| Type check | mypy + tsc | Type errors |
| Tests | pytest + vitest | Failed tests |

---

## 8. Integration into `scripts/scaffold.py`

**Status:** ✅ Implemented in v3.0 (Phase 2)

scaffold เขียนไฟล์ต่อไปนี้ให้ทุก project ใหม่:

| File | Source |
|---|---|
| `.pre-commit-config.yaml` | `_scaffold_tooling()` copy จาก standard root |
| `.secrets.baseline` | `_scaffold_tooling()` ใส่ minimal empty baseline |
| `scripts/checks/no_real_secrets_in_examples.py` | `_scaffold_tooling()` |
| `scripts/checks/no_direct_ai_sdk.py` | `_scaffold_tooling()` |
| `backend/app/core/pii.py` | `_scaffold_backend()` heredoc |
| `backend/app/core/logging.py` (StructuredLogger + `get_logger()`) | `_scaffold_backend()` heredoc |
| `backend/app/db/models/activity_log.py` | `_scaffold_backend()` heredoc |
| `backend/app/services/loggers/activity_logger.py` | `_scaffold_backend()` heredoc |
| `backend/app/api/decorators.py` (`@audited`) | `_scaffold_backend()` heredoc |
| `backend/alembic/versions/0001_activity_logs.py` (partitioned table + initial partition) | `_scaffold_backend()` heredoc |

After scaffolding, dev runs:
```bash
pip install pre-commit detect-secrets
pre-commit install
detect-secrets scan > .secrets.baseline   # optional — overwrites the minimal placeholder
cd backend && alembic upgrade head        # creates activity_logs + initial partition
```

---

## 9. Verification Checklist

ใน new project หลัง scaffold + setup:

- [ ] `.pre-commit-config.yaml` มีและ active
- [ ] `pre-commit run --all-files` ผ่าน
- [ ] `.secrets.baseline` มี (ถึง empty)
- [ ] `python scripts/checks/no_real_secrets_in_examples.py *.example backend/.env.example` ผ่าน
- [ ] `python scripts/checks/no_direct_ai_sdk.py backend/app/**/*.py` ผ่าน
- [ ] `backend/app/api/decorators.py` มี `@audited`
- [ ] `backend/app/core/logging.py` มี `StructuredLogger` + `get_logger()`
- [ ] AI endpoint ใหม่ใช้ `@audited` decorator
- [ ] Direct `structlog.get_logger()` ทดแทนด้วย `get_logger()` ทั่ว codebase

---

## 10. References

- [`../../AGENTS.md`](../../AGENTS.md) §B — Enforcement Matrix
- [`../logging.md`](../logging.md) §1 — `ActivityLogger` ที่ `@audited` wraps
- [`../security.md`](../security.md) — security non-negotiables
- [`ai.md`](ai.md) §1 — AIProvider abstraction ที่ no-direct-sdk check enforces
- gitleaks: https://github.com/gitleaks/gitleaks
- detect-secrets: https://github.com/Yelp/detect-secrets
- pre-commit: https://pre-commit.com/
