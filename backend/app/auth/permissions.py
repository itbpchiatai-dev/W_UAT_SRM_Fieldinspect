"""PermissionKey constants — single source of truth for router @require_permission.

Keep in sync with the seed catalog in app/seed.py. Adding a permission
here without seeding the row makes `require_permission(...)` always
deny (no row to match against in the catalog).
"""
from __future__ import annotations


class PermissionKey:
    # Read perms — each one ALSO gates the corresponding sidebar menu item.
    # (v3.0.3 merged menu visibility into read perms; see seed DEFAULT_MENUS.)
    USERS_READ = "users.read"
    ROLES_READ = "roles.read"
    MENUS_READ = "menus.read"
    SYSTEM_LOGS_READ = "system_logs.read"
    ACTIVITY_LOGS_READ = "activity_logs.read"
    ADMIN_SETTINGS_READ = "admin_settings.read"

    # Users
    USERS_CREATE = "users.create"
    USERS_UPDATE = "users.update"
    USERS_DELETE = "users.delete"
    USERS_DEACTIVATE = "users.deactivate"
    # Approval gate — split from USERS_UPDATE so admins can edit profile
    # fields without being able to silently flip is_approved=true and
    # bypass the approval flow (Deep-Audit HIGH-2).
    USERS_APPROVE = "users.approve"

    # Roles
    ROLES_CREATE = "roles.create"
    ROLES_UPDATE = "roles.update"
    ROLES_DELETE = "roles.delete"
    ROLES_ASSIGN = "roles.assign"

    # Per-user overrides
    PERMISSIONS_GRANT_OVERRIDE = "permissions.grant_override"
    PERMISSIONS_REVOKE_OVERRIDE = "permissions.revoke_override"

    # Menus
    MENUS_CREATE = "menus.create"
    MENUS_UPDATE = "menus.update"
    MENUS_DELETE = "menus.delete"
    MENUS_REORDER = "menus.reorder"

    # App settings
    ADMIN_SETTINGS_UPDATE = "admin_settings.update"

    # FarmLog — Suppliers
    SUPPLIERS_READ   = "suppliers.read"
    SUPPLIERS_CREATE = "suppliers.create"
    SUPPLIERS_UPDATE = "suppliers.update"
    SUPPLIERS_DELETE = "suppliers.delete"

    # FarmLog — Plots
    PLOTS_READ   = "plots.read"
    PLOTS_CREATE = "plots.create"
    PLOTS_UPDATE = "plots.update"
    PLOTS_DELETE = "plots.delete"
    PLOTS_ASSIGN = "plots.assign"

    # FarmLog — Records
    RECORDS_READ   = "records.read"
    RECORDS_CREATE = "records.create"
    RECORDS_UPDATE = "records.update"
    RECORDS_DELETE = "records.delete"

    # FarmLog — Field Definitions (Field Master / manageFields — super_admin)
    FIELDDEFS_READ   = "fielddefs.read"
    FIELDDEFS_CREATE = "fielddefs.create"
    FIELDDEFS_UPDATE = "fielddefs.update"
    FIELDDEFS_DELETE = "fielddefs.delete"

    # FarmLog — Master Data (dropdown options — manageMaster, super_admin/supervisor)
    MASTERDATA_READ   = "masterdata.read"
    MASTERDATA_CREATE = "masterdata.create"
    MASTERDATA_UPDATE = "masterdata.update"
    MASTERDATA_DELETE = "masterdata.delete"

    # Database Connections module (opt-in — FEATURE_DB_CONNECTIONS). Inert
    # string constants when the feature is off; the perms are only seeded +
    # the router only mounted when the flag is set. super_admin only — these
    # keys are in users.py _PRIVILEGE_MANAGEMENT_KEYS (no per-user override).
    DB_CONNECTIONS_READ = "db_connections.read"
    DB_CONNECTIONS_MANAGE = "db_connections.manage"
    DB_CONNECTIONS_QUERY = "db_connections.query"
