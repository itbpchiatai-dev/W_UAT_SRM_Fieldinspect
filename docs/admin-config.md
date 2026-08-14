# docs/admin-config.md

> Admin-Configurable Patterns — implementation reference
>
> Policy + maturity gates อยู่ใน [`AGENTS.md`](../AGENTS.md) §15
> This doc covers data models, services, and implementation patterns

---

## Overview

**v3.0 maturity-tiered:** ไม่ใช่ทุก project ต้องมีครบ 3 patterns

| Pattern | Required At | Use Case |
|---|---|---|
| **C: App Settings** | **L1+** | AI prompt, email template, threshold, dropdown options, MODEL_PRICING |
| **A: Feature Flags** | **L2+** | เปิด/ปิด feature โดยไม่ deploy |
| **B: UI Permissions** | **L1+ (baseline)** | menu/action/widget visibility per role + per-user override |

L0 prototypes ใช้ env var / code constants ได้ — refactor เมื่อขึ้น L1+

> **Update 2026-05-28 (auth absorption sprint):** Pattern B ถูกย้ายลงมาเป็น L1 baseline พร้อมกับ auth scaffold เพราะ standard ต้องการ login + RBAC + per-user override ครบในทุก project ตั้งแต่ L1
> - Tables `roles`, `permissions`, `role_permissions`, `user_permission_overrides` ถูก seed อัตโนมัติโดย `backend/app/seed.py`
> - Override UI อยู่ใน `frontend/src/pages/settings/Users.tsx` (tab "สิทธิ์เพิ่มเติม")
> - Resolution logic ใน `backend/app/auth/dependencies.py::_compute_effective_permissions()` — iterate `permission_overrides` หลังจากรวม role permissions; `granted=true` = extra grant, `granted=false` = revoke

---

## Decision Framework (apply ก่อนเขียน config ใดๆ)

```
┌─────────────────────────────────────────────────────────┐
│  Q1: Business user หรือ admin จะอยากเปลี่ยนค่านี้ไหม      │
│      (ตอนนี้ หรือใน 6 เดือน-1 ปี)                       │
└────────────┬────────────────────────────────────────────┘
             │
       ┌─────┴─────┐
      Yes         No
       │           │
       ▼           ▼
┌──────────────┐  ┌──────────────────────────────────────┐
│ DB-driven    │  │ Q2: เป็น infrastructure / secret /    │
│ + Admin UI   │  │     deploy-time config ไหม?           │
│              │  └────────────┬─────────────────────────┘
│ (Pattern C/A/B│              │
│  ตาม maturity)│         ┌─────┴─────┐
└──────────────┘        Yes         No
                         │           │
                         ▼           ▼
                   ┌──────────┐  ┌──────────────┐
                   │ env var  │  │ code constant│
                   │ /vault   │  │ (rare)       │
                   └──────────┘  └──────────────┘
```

### Examples

| Type | Should be | Reason |
|---|---|---|
| Dashboard layout / widget order | DB + Admin UI (Pattern C/A) | Admin จะอยากเรียงเอง |
| Feature on/off | Pattern A (L2+) | Admin เปิด/ปิดให้ user เฉพาะกลุ่มได้ |
| User → menu/feature permission | Pattern B (L1+) | สิทธิ์เปลี่ยนแปลงตามองค์กร |
| Rate limit per user/role | Pattern A + C | Admin อยากปรับให้ VIP user ได้ |
| AI prompt template | Pattern C | Business อยาก tune โดยไม่ผ่าน dev |
| MODEL_PRICING | Pattern C (super-admin) | Pricing เปลี่ยนเป็นระยะๆ |
| Email template content | Pattern C | Marketing/HR แก้เองได้ |
| Threshold (alert, approval, quota) | Pattern C | Business rule เปลี่ยนตามนโยบาย |
| Dropdown options (status, category) | Pattern C | Business taxonomy โตขึ้นเรื่อยๆ |
| Default rate limit (system-wide) | env var | Operational tuning, dev เป็นคนตั้ง |
| DB connection string | env var / vault | Infrastructure |
| API keys, secrets | vault / `.env` | Security |
| Default JWT expiry | env var | Security tuning |
| List of supported file types | code constant | Tied to code logic |
| HTTP status codes mapping | code constant | Protocol-level |

---

## Pattern C: App Settings (Required L1+)

Key-value config table — ROI สูงสุดของ 3 patterns

### C.1 Data Model

```python
# app/db/models/app_setting.py
from typing import Any
from uuid import UUID

from sqlalchemy import String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDMixin


class AppSetting(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "app_settings"

    key: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    # e.g. "ai.prompt.product_description", "email.invite.subject_th",
    #      "ai.pricing.claude-sonnet-4-6", "logging.activity.retention_days"

    value: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    value_type: Mapped[str] = mapped_column(String(20), nullable=False)
    # "string" | "number" | "boolean" | "json"

    category: Mapped[str] = mapped_column(String(50), nullable=False)
    # "ai" | "email" | "rate_limit" | "ui" | "logging" | "pricing"

    description: Mapped[str | None] = mapped_column(Text)
    requires_role: Mapped[str | None] = mapped_column(String(50))
    # e.g. "super_admin" for pricing, "admin" for prompt templates
    # None = any internal:admin can edit

    updated_by: Mapped[UUID | None]
```

Values wrapped ใน JSONB so type can vary:
- string: `{"value": "Hello"}`
- number: `{"value": 60}`
- json: `{"options": ["draft", "active"]}`

### C.2 Service

```python
# app/services/app_setting_service.py
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.app_setting import AppSetting
from app.db.models.user import User


class AppSettingService:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def get(self, key: str, default: Any = None) -> Any:
        result = await self.db.execute(
            select(AppSetting).where(AppSetting.key == key)
        )
        setting = result.scalar_one_or_none()
        if setting is None:
            return default
        # If wrapped with {"value": x}, unwrap
        if isinstance(setting.value, dict) and "value" in setting.value and len(setting.value) == 1:
            return setting.value["value"]
        return setting.value

    async def set(
        self, *, key: str, value: Any, value_type: str, category: str,
        description: str | None, requires_role: str | None,
        updated_by: User,
    ) -> AppSetting:
        result = await self.db.execute(
            select(AppSetting).where(AppSetting.key == key)
        )
        setting = result.scalar_one_or_none()
        if setting is None:
            setting = AppSetting(key=key, category=category)
            self.db.add(setting)
        wrapped = value if isinstance(value, dict) else {"value": value}
        setting.value = wrapped
        setting.value_type = value_type
        setting.description = description
        setting.requires_role = requires_role
        setting.updated_by = updated_by.id
        await self.db.flush()
        return setting
```

### C.3 Caching (in-memory + TTL)

Settings ที่อ่านบ่อย (เช่น AI prompt, pricing) → cache in-memory พร้อม TTL + invalidation

```python
from time import time
from typing import Any


_cache: dict[str, tuple[float, Any]] = {}
_TTL = 60  # seconds


async def get_setting_cached(service: AppSettingService, key: str, default: Any = None) -> Any:
    if key in _cache:
        ts, val = _cache[key]
        if time() - ts < _TTL:
            return val
    val = await service.get(key, default)
    _cache[key] = (time(), val)
    return val


def invalidate_cache(key: str | None = None) -> None:
    if key is None:
        _cache.clear()
    else:
        _cache.pop(key, None)
```

Admin UI ที่แก้ setting ต้องเรียก `invalidate_cache(key)` หลัง `db.commit()`

### C.4 API Endpoints

```python
# app/api/v1/admin/app_settings.py
from fastapi import APIRouter, Depends, HTTPException, Request, status

from app.api.deps import CurrentUser, DbDep, require_role
from app.services.app_setting_service import AppSettingService
from app.services.loggers.activity_logger import ActivityLogger
from app.services.cache import invalidate_cache


router = APIRouter(
    prefix="/admin/settings",
    tags=["admin", "settings"],
    dependencies=[Depends(require_role("internal:admin"))],
)


@router.get("", response_model=list[AppSettingRead])
async def list_settings(
    db: DbDep, user: CurrentUser, category: str | None = None
) -> list[AppSettingRead]:
    # implement: filter by category, filter out requires_role > user.roles
    ...


@router.put("/{key}", response_model=AppSettingRead)
async def update_setting(
    key: str,
    payload: AppSettingUpdate,
    db: DbDep,
    user: CurrentUser,
    request: Request,
) -> AppSettingRead:
    service = AppSettingService(db)

    # Check requires_role
    existing = await db.execute(select(AppSetting).where(AppSetting.key == key))
    existing_setting = existing.scalar_one_or_none()
    if existing_setting and existing_setting.requires_role:
        if existing_setting.requires_role not in user.roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Requires role: {existing_setting.requires_role}",
            )

    setting = await service.set(
        key=key,
        value=payload.value,
        value_type=payload.value_type,
        category=payload.category,
        description=payload.description,
        requires_role=payload.requires_role,
        updated_by=user,
    )

    # Audit log
    activity = ActivityLogger(db)
    await activity.log(
        user=user,
        action_type="update",
        action="app_setting.updated",
        resource_type="app_setting",
        resource_id=key,
        risk_level="high" if existing_setting and existing_setting.requires_role else "medium",
        metadata={"new_value": payload.value, "category": payload.category},
        request=request,
    )

    await db.commit()
    invalidate_cache(key)  # MUST after commit
    return AppSettingRead.model_validate(setting)
```

### C.5 Usage Examples

**AI prompt:**
```python
prompt_template = await app_settings.get(
    "ai.prompt.product_description",
    default="Describe this product: {product_name}"
)
final_prompt = prompt_template.format(product_name=product.name)
```

**MODEL_PRICING (super-admin only):**
```python
pricing = await app_settings.get(f"ai.pricing.{model}")
# e.g. {"input": 3.0, "output": 15.0, "cache_read": 0.30, "cache_write": 3.75}
```

**Email template:**
```python
subject = await app_settings.get("email.invite.subject_th", default="...")
body = await app_settings.get("email.invite.body_th", default="...")
```

**Threshold:**
```python
max_uploads = await app_settings.get("upload.max_per_hour", default=10)
```

**Dropdown options:**
```python
statuses = await app_settings.get(
    "product.status.options",
    default=["draft", "active", "discontinued"]
)
```

### C.6 Seed Initial Settings

Migration หรือ init script ที่ insert default settings:

```python
async def seed_app_settings(db: AsyncSession) -> None:
    defaults = [
        # AI pricing (super-admin only)
        ("ai.pricing.claude-sonnet-4-6",
         {"input": 3.0, "output": 15.0, "cache_read": 0.30, "cache_write": 3.75},
         "json", "pricing", "Claude Sonnet 4.6 pricing per 1M tokens", "super_admin"),
        ("ai.pricing.claude-haiku-4-5-20251001",
         {"input": 1.0, "output": 5.0, "cache_read": 0.10, "cache_write": 1.25},
         "json", "pricing", "Claude Haiku 4.5 pricing per 1M tokens", "super_admin"),

        # Logging retention
        ("logging.activity.retention_days", {"value": 60}, "number", "logging",
         "Activity log retention in days", None),
        ("logging.system.retention_days", {"value": 60}, "number", "logging",
         "System log retention in days", None),
        ("logging.ai.retention_days", {"value": 60}, "number", "logging",
         "AI call log retention in days", None),

        # AI cost budget
        ("ai.budget.default_daily_usd", {"value": 1.0}, "number", "ai",
         "Default per-user daily AI cost budget (USD)", None),
    ]
    for key, value, value_type, category, description, requires_role in defaults:
        existing = await db.execute(select(AppSetting).where(AppSetting.key == key))
        if existing.scalar_one_or_none() is None:
            db.add(AppSetting(
                key=key, value=value, value_type=value_type, category=category,
                description=description, requires_role=requires_role,
            ))
    await db.commit()
```

### C.7 Auth Provider Toggles (L1 baseline)

ตั้งแต่ auth absorption sprint — standard ติด 4 setting keys สำหรับ runtime toggle ของ auth providers
(เกี่ยวข้องโดยตรงกับ `frontend/src/pages/Login.tsx` + `backend/app/api/v1/admin_settings.py`)

| Key | Type | Default (seed) | Description |
|---|---|---|---|
| `auth.local.enabled` | `boolean` | `true` ถ้า `AUTH_SCOPE != internal_only` | แสดง email+password form บนหน้า `/login` |
| `auth.sso.enabled` | `boolean` | `true` ถ้า `AUTH_SCOPE != external_only` | แสดงปุ่ม "Sign in with Microsoft" |
| `auth.local.signup_enabled` | `boolean` | `false` | เปิด public signup endpoint (default = admin-invite only) |
| `auth.signup_default_role` | `string` | `"external:user"` | Role assigned ให้ user signup เอง |

**Who can change** — endpoint `PUT /api/v1/admin/settings/{key}` require permission `admin_settings.update`
ตามค่า seed default permission นี้อยู่ใน role `internal:super_admin` เท่านั้น
(super admin delegate ให้ role อื่นได้ผ่าน `/settings/roles`)

**AUTH_SCOPE compile-time ceiling** — `AUTH_SCOPE` env var เป็นเพดาน, runtime toggle เกินไม่ได้:

```python
# backend/app/api/v1/admin_settings.py
_SCOPE_LOCKED_KEYS = {
    # internal_only = Azure AD only -> local auth locked OFF
    # external_only = local only    -> SSO locked OFF
    "internal_only": {"auth.local.enabled": False},
    "external_only": {"auth.sso.enabled": False},
}
```

ถ้า PUT มาขัด ceiling — backend reject `400 BAD_REQUEST` พร้อมข้อความ
`"Setting ... is locked by AUTH_SCOPE env (compile-time ceiling)"`

**Public read endpoint** — `GET /api/v1/admin/settings/public` คืน camelCase subset
(`authLocalEnabled`, `authSsoEnabled`) — auth-free เพราะหน้า Login ต้องอ่านก่อน user known

**Frontend mirror** — `frontend/src/pages/settings/AuthSettings.tsx` อ่าน `import.meta.env.VITE_AUTH_SCOPE`
แล้ว disable toggle ที่ ceiling ปิดไว้ (UX ดีกว่าให้กดแล้วเด้ง 400)

Cross-reference: ดู `docs/auth.md` §12 สำหรับ provider toggle semantics + login decision matrix

---

## Pattern A: Feature Flags (Required L2+)

### A.1 Data Model

```python
# app/db/models/feature_flag.py
from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy import Boolean, String, Text
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDMixin


class FeatureFlag(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "feature_flags"

    key: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    # e.g. "ai_chat_v2", "new_dashboard", "experimental_export"

    enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    description: Mapped[str | None] = mapped_column(Text)

    # Targeting: empty list = global flag.enabled applies
    # Non-empty: flag enabled ONLY for matching roles/users
    enabled_for_roles: Mapped[list[str]] = mapped_column(
        ARRAY(String(50)), default=list, nullable=False
    )
    enabled_for_user_ids: Mapped[list[str]] = mapped_column(
        ARRAY(String(50)), default=list, nullable=False
    )

    updated_by: Mapped[UUID | None]
```

### A.2 Service

```python
# app/services/feature_flag_service.py
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.feature_flag import FeatureFlag
from app.db.models.user import User


class FeatureFlagService:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def is_enabled(self, key: str, user: User | None = None) -> bool:
        result = await self.db.execute(
            select(FeatureFlag).where(FeatureFlag.key == key)
        )
        flag = result.scalar_one_or_none()
        if flag is None:
            return False  # unknown flag = off (fail-safe)

        # If targeting is set, flag is only on for matching users
        if flag.enabled_for_user_ids or flag.enabled_for_roles:
            if user is None:
                return False
            if str(user.id) in flag.enabled_for_user_ids:
                return True
            if any(r in flag.enabled_for_roles for r in user.roles):
                return True
            return False

        # Otherwise: global on/off
        return flag.enabled
```

### A.3 API Endpoints

```python
# app/api/v1/admin/feature_flags.py
router = APIRouter(
    prefix="/admin/feature-flags",
    tags=["admin", "feature-flags"],
    dependencies=[Depends(require_role("internal:admin"))],
)


@router.get("", response_model=list[FeatureFlagRead])
async def list_flags(db: DbDep) -> list[FeatureFlagRead]:
    # implement: list all flags
    ...


@router.put("/{key}", response_model=FeatureFlagRead)
async def update_flag(
    key: str, payload: FeatureFlagUpdate, db: DbDep,
    user: CurrentUser, request: Request,
) -> FeatureFlagRead:
    # implement: upsert flag + audit log via ActivityLogger.log(risk_level="high")
    ...


@router.get("/me", response_model=dict[str, bool])
async def my_flags(db: DbDep, user: CurrentUser) -> dict[str, bool]:
    """Return all flags resolved per current user — frontend consumes this"""
    service = FeatureFlagService(db)
    result = await db.execute(select(FeatureFlag))
    flags = list(result.scalars().all())
    return {f.key: await service.is_enabled(f.key, user=user) for f in flags}
```

### A.4 Usage in Code

```python
flag_service = FeatureFlagService(db)
if await flag_service.is_enabled("ai_chat_v2", user=current_user):
    return await new_ai_handler(payload, db=db)
else:
    return await legacy_ai_handler(payload, db=db)
```

### A.5 Frontend

```typescript
// src/hooks/useFeatureFlag.ts
import { useQuery } from '@tanstack/react-query';
import { apiClient } from '@/api/client';

export function useFeatureFlags() {
  return useQuery({
    queryKey: ['me', 'feature-flags'],
    queryFn: async () => {
      const { data } = await apiClient.get<Record<string, boolean>>(
        '/api/v1/admin/feature-flags/me'
      );
      return data;
    },
    staleTime: 5 * 60 * 1000,
  });
}

export function useFeatureFlag(key: string): boolean {
  const { data } = useFeatureFlags();
  return data?.[key] ?? false;
}
```

```tsx
function NewFeature() {
  const enabled = useFeatureFlag('ai_chat_v2');
  if (!enabled) return null;
  return <AiChatV2 />;
}
```

---

## Pattern B: UI Permissions (Required L1+ — baseline RBAC)

### B.1 Concept

Permission keys ใช้ dot notation:

```
menu.reports.view
menu.products.view
products.create
products.delete
products.export
dashboard.widget.revenue
```

- **Role** → base permission set
- **User override** → grant หรือ revoke เฉพาะ user คนนั้น

Effective = (role permissions) + (granted overrides) − (revoked overrides)

### B.1.1 ใครให้สิทธิ์รายบุคคลได้ (นโยบาย v3.0.29)

| ผู้ให้ | ให้ได้ | ให้ไม่ได้ |
|---|---|---|
| **admin** (มี `permissions.grant_override` ใน seed แล้ว) | feature keys ของแอป + `*.read` ทั่วไป | คีย์ระดับระบบ → 403 |
| **super_admin** | ทุกคีย์ | grant ให้ตัวเอง (self-elevation guard) |

**คีย์ระดับระบบ (super_admin เท่านั้น)** — enforce ด้วย deny-list ใน
`api/v1/users.py: add_override` (ไม่ใช่ที่ตัว permission):
`permissions.*`, `admin_settings.*`, `users.approve/create/delete/deactivate`,
`roles.*`, `menus.delete`

**ผลมีเมื่อไหร่:** backend คำนวณ effective permissions ใหม่**ทุก request**
→ API มีผลทันทีที่กด grant/revoke. ฝั่ง UI ของผู้ถูกแก้สิทธิ์ re-sync
permissions + เมนูอัตโนมัติเมื่อ window กลับมา focus (throttle 30 วิ —
ดู `AuthBootstrap.tsx`) หรือเมื่อรีเฟรชหน้า — **ไม่ต้อง login ใหม่**.

### B.2 Data Model

```python
# app/db/models/permission.py
from uuid import UUID

from sqlalchemy import Boolean, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDMixin


class Permission(UUIDMixin, TimestampMixin, Base):
    """Catalog of all permission keys in the system."""
    __tablename__ = "permissions"

    key: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    category: Mapped[str] = mapped_column(String(20), nullable=False)
    # "menu" | "action" | "widget" | "feature"
    description: Mapped[str | None] = mapped_column(Text)


class RolePermission(Base):
    """M2M: which permissions does each role have."""
    __tablename__ = "role_permissions"

    role: Mapped[str] = mapped_column(String(50), primary_key=True)
    permission_key: Mapped[str] = mapped_column(
        String(100), ForeignKey("permissions.key", ondelete="CASCADE"),
        primary_key=True,
    )


class UserPermissionOverride(TimestampMixin, Base):
    """Per-user grant or revoke vs role default."""
    __tablename__ = "user_permission_overrides"

    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    permission_key: Mapped[str] = mapped_column(
        String(100), ForeignKey("permissions.key", ondelete="CASCADE"),
        primary_key=True,
    )
    granted: Mapped[bool] = mapped_column(Boolean, nullable=False)
    # True = extra grant beyond role; False = revoke from role
    reason: Mapped[str | None] = mapped_column(Text)
```

### B.3 Service

```python
# app/services/permission_service.py
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.permission import Permission, RolePermission, UserPermissionOverride
from app.db.models.user import User


class PermissionService:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def get_effective_permissions(self, user: User) -> set[str]:
        # Role permissions
        result = await self.db.execute(
            select(RolePermission.permission_key).where(
                RolePermission.role.in_(user.roles)
            )
        )
        permissions = {row[0] for row in result.all()}

        # User overrides
        result = await self.db.execute(
            select(UserPermissionOverride).where(
                UserPermissionOverride.user_id == user.id
            )
        )
        for override in result.scalars().all():
            if override.granted:
                permissions.add(override.permission_key)
            else:
                permissions.discard(override.permission_key)

        return permissions

    async def has_permission(self, user: User, permission_key: str) -> bool:
        effective = await self.get_effective_permissions(user)
        return permission_key in effective
```

### B.4 Authorization Dependency

```python
# app/api/deps.py
def require_permission(permission_key: str):
    """Authorization by permission key (not role)."""
    async def checker(user: CurrentUser, db: DbDep) -> User:
        service = PermissionService(db)
        if not await service.has_permission(user, permission_key):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Missing permission: {permission_key}",
            )
        return user
    return checker


# Usage
@router.delete(
    "/products/{id}",
    dependencies=[Depends(require_permission("products.delete"))],
)
async def delete_product(product_id: str, db: DbDep, user: CurrentUser) -> None:
    ...
```

### B.5 Frontend — Hybrid (typed const + dynamic visibility)

**Backend seed:** insert permission catalog (B.6 below)

**Frontend:** typed constants + dynamic visibility

```typescript
// src/permissions/keys.ts — generated/synced from backend
export const PermissionKeys = {
  MENU_PRODUCTS_VIEW: 'menu.products.view',
  MENU_REPORTS_VIEW: 'menu.reports.view',
  PRODUCTS_CREATE: 'products.create',
  PRODUCTS_DELETE: 'products.delete',
  PRODUCTS_EXPORT: 'products.export',
  DASHBOARD_WIDGET_REVENUE: 'dashboard.widget.revenue',
} as const;

export type PermissionKey = (typeof PermissionKeys)[keyof typeof PermissionKeys];
```

```typescript
// src/hooks/usePermissions.ts
import { useQuery } from '@tanstack/react-query';
import { apiClient } from '@/api/client';
import type { PermissionKey } from '@/permissions/keys';

export function usePermissions() {
  return useQuery({
    queryKey: ['me', 'permissions'],
    queryFn: async () => {
      const { data } = await apiClient.get<{ permissions: PermissionKey[] }>(
        '/api/v1/me/permissions'
      );
      return new Set(data.permissions);
    },
    staleTime: 5 * 60 * 1000,
  });
}

export function useHasPermission(key: PermissionKey): boolean {
  const { data } = usePermissions();
  return data?.has(key) ?? false;
}
```

```tsx
// Menu — visibility dynamic via API
import { PermissionKeys } from '@/permissions/keys';

function NavMenu() {
  const canSeeReports = useHasPermission(PermissionKeys.MENU_REPORTS_VIEW);
  const canSeeProducts = useHasPermission(PermissionKeys.MENU_PRODUCTS_VIEW);

  return (
    <nav>
      {canSeeProducts && <Link to="/products">{t('menu.products')}</Link>}
      {canSeeReports && <Link to="/reports">{t('menu.reports')}</Link>}
    </nav>
  );
}

// Action — type-safe key, dynamic visibility
function ProductRow({ product }) {
  const canDelete = useHasPermission(PermissionKeys.PRODUCTS_DELETE);
  return (
    <tr>
      <td>{product.name}</td>
      <td>{canDelete && <DeleteButton id={product.id} />}</td>
    </tr>
  );
}
```

**Route/component mapping = code constants:**
```typescript
// src/routes/index.ts — NOT dynamic from API
export const routes = [
  { path: '/products', component: ProductsPage, requires: PermissionKeys.MENU_PRODUCTS_VIEW },
  { path: '/reports', component: ReportsPage, requires: PermissionKeys.MENU_REPORTS_VIEW },
];
```

### B.6 Seed Permission Catalog

```python
async def seed_permissions(db: AsyncSession) -> None:
    catalog = [
        ("menu.products.view", "menu", "View Products menu"),
        ("menu.reports.view", "menu", "View Reports menu"),
        ("menu.admin.view", "menu", "View Admin section"),
        ("products.create", "action", "Create products"),
        ("products.update", "action", "Update products"),
        ("products.delete", "action", "Delete products"),
        ("products.export", "action", "Export products"),
        ("dashboard.widget.revenue", "widget", "Revenue widget"),
        ("dashboard.widget.user_growth", "widget", "User growth widget"),
    ]
    for key, category, description in catalog:
        existing = await db.execute(select(Permission).where(Permission.key == key))
        if existing.scalar_one_or_none() is None:
            db.add(Permission(key=key, category=category, description=description))
    await db.commit()
```

**Sync to frontend:** run after release ที่เพิ่ม permission ใหม่ — script generate `src/permissions/keys.ts` จาก DB

---

## Hard Rules (v3.0)

1. ทุก mutation ของ App Setting / FeatureFlag / Permission / RolePermission / UserPermissionOverride → audit ผ่าน `ActivityLogger.log(risk_level="high")`
2. ทุก admin endpoint → require `internal:admin` role อย่างน้อย
3. App Setting ที่เป็น sensitive (pricing, security config) → set `requires_role="super_admin"`
4. Frontend ห้าม trust permission state — ตรวจซ้ำที่ backend เสมอ
5. Permission key เป็น **กลาง** — เริ่มต้นด้วย category (menu/action/widget/feature) แล้วลงรายละเอียด
6. **ห้ามใช้ `app_settings` เก็บ secret** — secret อยู่ใน vault / `.env` เท่านั้น
7. Cache invalidation — admin UI ที่แก้ setting ต้อง invalidate cache ของ key นั้นๆ
8. Frontend permission keys ใช้ **typed constants** — ห้ามใช้ raw string ทั่ว codebase

---

## Migration Strategy (Adding to Existing Project)

ถ้า project มีอยู่แล้วแต่ยังไม่ใช้ patterns เหล่านี้:

1. **Phase 1: Add Pattern C** (L1 requirement) — migration `app_settings` table + service
2. **Phase 2: Seed defaults** — pricing, retention, AI prompts ฯลฯ
3. **Phase 3: Refactor hardcoded → DB** — แทนที่ code constant ทีละจุดด้วย `app_settings.get(...)`
4. **Phase 4: Pattern B (L1 baseline)** — RBAC + per-user override; มากับ auth scaffold ตั้งแต่ L1. project เก่าที่ยังไม่มี ให้เพิ่ม table `roles`/`permissions`/`role_permissions`/`user_permission_overrides` + seed
5. **Phase 5: Pattern A (L2)** — เมื่อ project ขึ้น L2 หรือเริ่มมี feature toggle จริง

ไม่ต้องทำทุก phase ใน sprint เดียว — เริ่ม Pattern C + B (baseline) ก่อน, A รอ project ขึ้น L2
