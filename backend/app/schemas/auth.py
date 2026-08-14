"""Auth + RBAC schemas (CamelBaseModel — camelCase JSON / snake_case Python)."""
from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from app.schemas.base import CamelBaseModel


class LoginRequest(CamelBaseModel):
    email: str
    password: str


class TokenResponse(CamelBaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int


class RefreshRequest(CamelBaseModel):
    refresh_token: str | None = None


class PermissionRead(CamelBaseModel):
    id: UUID
    key: str
    display_name: str
    category: str
    is_menu: bool


class RoleSummary(CamelBaseModel):
    id: UUID
    name: str
    display_name: str
    provider_scope: str
    is_system: bool


class RoleRead(RoleSummary):
    description: str | None = None
    permissions: list[PermissionRead] = []


class RoleCreate(CamelBaseModel):
    name: str
    display_name: str
    provider_scope: str = "any"
    description: str | None = None
    permission_keys: list[str] = []


class RoleUpdate(CamelBaseModel):
    display_name: str | None = None
    description: str | None = None
    permission_keys: list[str] | None = None


class UserSummary(CamelBaseModel):
    id: UUID
    email: str
    full_name: str
    auth_provider: str
    is_active: bool
    is_approved: bool = False
    last_login_at: datetime | None = None
    roles: list[RoleSummary] = []
    supplier_id: UUID | None = None
    is_supplier_admin: bool = False


class UserOverrideRead(CamelBaseModel):
    permission_key: str
    granted: bool


class UserRead(UserSummary):
    email_verified: bool
    business_unit_ids: list[str] = []
    created_at: datetime
    updated_at: datetime
    overrides: list[UserOverrideRead] = []


class UserCreate(CamelBaseModel):
    email: str
    full_name: str
    auth_provider: str
    password: str | None = None
    role_names: list[str] = []
    business_unit_ids: list[str] = []
    supplier_id: UUID | None = None
    is_supplier_admin: bool = False
    # When True the user is created as is_approved=False and admins get
    # an email notification. Useful for bulk imports that need per-row
    # review. Default False — usual admin-create flow stays "approved
    # on create" so a new hire can sign in immediately.
    require_approval: bool = False


class UserUpdate(CamelBaseModel):
    full_name: str | None = None
    is_active: bool | None = None
    is_approved: bool | None = None
    role_names: list[str] | None = None
    business_unit_ids: list[str] | None = None
    supplier_id: UUID | None = None
    is_supplier_admin: bool | None = None


class BulkApproveRequest(CamelBaseModel):
    user_ids: list[UUID]


class OverrideRequest(CamelBaseModel):
    permission_key: str
    granted: bool
    reason: str | None = None


class MenuRead(CamelBaseModel):
    id: UUID
    key: str
    label_th: str
    label_en: str
    icon: str | None = None
    path: str
    parent_id: UUID | None = None
    order_index: int
    required_permission_key: str
    is_system: bool
    children: list["MenuRead"] = []


MenuRead.model_rebuild()


class MenuCreate(CamelBaseModel):
    key: str
    label_th: str
    label_en: str
    icon: str | None = None
    path: str
    parent_id: UUID | None = None
    order_index: int = 0
    required_permission_key: str


class MenuUpdate(CamelBaseModel):
    label_th: str | None = None
    label_en: str | None = None
    icon: str | None = None
    path: str | None = None
    parent_id: UUID | None = None
    order_index: int | None = None
    required_permission_key: str | None = None


class AppSettingRead(CamelBaseModel):
    id: UUID
    key: str
    value: Any
    value_type: str
    category: str
    description: str | None = None
    requires_role: str
    updated_at: datetime


class AppSettingUpdate(CamelBaseModel):
    value: Any


class MePermissionsResponse(CamelBaseModel):
    permission_keys: list[str]


class SystemLogRead(CamelBaseModel):
    id: UUID
    created_at: datetime
    category: str
    event: str
    status: str
    duration_ms: int | None = None
    error_type: str | None = None
    error_message: str | None = None
    correlation_id: str | None = None
    extra_metadata: dict[str, Any] = {}


class ActivityLogRead(CamelBaseModel):
    id: UUID
    created_at: datetime
    user_id: UUID | None = None
    user_email_masked: str | None = None
    action: str
    action_type: str
    resource_type: str | None = None
    resource_id: str | None = None
    risk_level: str
    is_security_event: bool
    ip_address: str | None = None
    http_status: int | None = None
    extra_metadata: dict[str, Any] = {}
