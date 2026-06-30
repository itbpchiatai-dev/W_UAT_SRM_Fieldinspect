"""Routers mounted at app boot.

Auth + RBAC + user management routers are wired by default (Sprint 3).
Add project-specific routers by appending to ROUTERS below.
"""
from __future__ import annotations

from fastapi import APIRouter

from app.api.v1.db_connections import router as db_connections_router
from app.core.config import get_settings

from app.api.v1.activity_logs import router as activity_logs_router
from app.api.v1.admin_settings import router as admin_settings_router
from app.api.v1.auth import router as auth_router
from app.api.v1.me import router as me_router
from app.api.v1.menus import router as menus_router
from app.api.v1.permissions import router as permissions_router
from app.api.v1.roles import router as roles_router
from app.api.v1.system_logs import router as system_logs_router
from app.api.v1.user_approval import router as user_approval_router
from app.api.v1.users import router as users_router
from app.api.v1.dashboard import router as dashboard_router
from app.api.v1.suppliers import router as suppliers_router
from app.api.v1.plots import router as plots_router
from app.api.v1.records import router as records_router
from app.api.v1.fielddefs import router as fielddefs_router
from app.api.v1.masterdata import router as masterdata_router

ROUTERS: list[tuple[APIRouter, str]] = [
    (auth_router, "/api/v1/auth"),
    (me_router, "/api/v1/me"),
    # user_approval — PUBLIC token-gated endpoints; must be mounted before
    # any catch-all to keep /approval/{token} reachable without a session.
    (user_approval_router, "/api/v1/users/approval"),
    (users_router, "/api/v1/users"),
    (roles_router, "/api/v1/roles"),
    (permissions_router, "/api/v1/permissions"),
    (menus_router, "/api/v1/menus"),
    (admin_settings_router, "/api/v1/admin/settings"),
    (system_logs_router, "/api/v1/admin/system-logs"),
    (activity_logs_router, "/api/v1/admin/activity-logs"),
    # FarmLog modules
    (dashboard_router, "/api/v1/dashboard"),
    (suppliers_router, "/api/v1/suppliers"),
    (plots_router, "/api/v1/plots"),
    (records_router, "/api/v1/records"),
    (fielddefs_router, "/api/v1/fielddefs"),
    (masterdata_router, "/api/v1/masterdata"),
]

# Database Connections module — mounted only when the runtime flag is on.
if get_settings().FEATURE_DB_CONNECTIONS:
    ROUTERS.append((db_connections_router, "/api/v1/db-connections"))
