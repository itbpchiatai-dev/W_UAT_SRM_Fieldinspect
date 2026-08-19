"""Auth bootstrap seed — `python -m app.seed`.

Idempotent: every insert upserts by unique key. Run after
`alembic upgrade head`. Reads three env vars (set by the wizard):

- AUTH_BOOTSTRAP_SUPER_ADMIN_EMAIL  — first super admin's email
- AUTH_BOOTSTRAP_SUPER_ADMIN_AUTH_TYPE — "sso" | "local"
- AUTH_BOOTSTRAP_INITIAL_PASSWORD   — transient, used once when type=local

The default `auth.local.enabled` / `auth.sso.enabled` values respect
`AUTH_SCOPE` env (compile-time ceiling).

See docs/auth.md §11 + docs/admin-config.md.
"""
from __future__ import annotations

import asyncio
import os

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.auth.password import PasswordPolicyError, hash_password
from app.core.config import get_settings
from app.db.models.app_setting import AppSetting
from app.db.models.menu_item import MenuItem
from app.db.models.permission import Permission
from app.db.models.role import Role
from app.db.models.user import User
from app.db.session import close_db, get_db_session, init_db
from app.services.external_submission import EXTERNAL_FIELD_HELPER_EMAIL


# (key, display_name, category, is_menu)
#
# Permission model (v3.0.3):
# We dropped the separate `menu.*.view` perms — every menu now hangs off
# the same `*.read` permission that gates the underlying list endpoint.
# Granting `users.read` therefore shows the menu AND lets the page load,
# eliminating the old footgun where a role had read but no menu-view.
DEFAULT_PERMISSIONS: list[tuple[str, str, str, bool]] = [
    # Read (also gates menu visibility)
    ("users.read",                  "ดูรายการผู้ใช้",           "users", True),
    ("roles.read",                  "ดู Roles + Permissions",  "roles", True),
    ("menus.read",                  "ดูเมนู (admin)",          "menus", True),
    ("system_logs.read",            "ดู System Logs",          "logs", True),
    ("activity_logs.read",          "ดู Activity Logs",        "logs", True),
    ("admin_settings.read",         "อ่าน App Settings",       "admin", True),
    # Action perms (write/delete)
    ("users.create",                "สร้างผู้ใช้",              "users", False),
    ("users.update",                "แก้ไขผู้ใช้",              "users", False),
    ("users.delete",                "ลบผู้ใช้",                 "users", False),
    ("users.deactivate",            "ปิดใช้งานผู้ใช้",          "users", False),
    # users.approve — gate is_approved transitions separately from
    # users.update so admin's profile-edit power can't silently approve
    # accounts. Closes Deep-Audit HIGH-2.
    ("users.approve",               "อนุมัติผู้ใช้",             "users", False),
    # users.reset_password (round 8-23A) — admin sets a new password on a
    # LOCAL account. Deliberately NOT listed in any DEFAULT_ROLES entry:
    # internal:super_admin is the `keys=None` role and picks up the whole
    # catalog, so this key lands there and NOWHERE else. Setting someone's
    # password is account takeover — users.update must never imply it.
    ("users.reset_password",        "ตั้งรหัสผ่านใหม่ให้ผู้ใช้",   "users", False),
    ("roles.create",                "สร้าง Role",              "roles", False),
    ("roles.update",                "แก้ไข Role",              "roles", False),
    ("roles.delete",                "ลบ Role",                 "roles", False),
    ("roles.assign",                "Assign Role ให้ผู้ใช้",   "roles", False),
    ("permissions.grant_override",  "ให้สิทธิ์เพิ่มเติม",       "permissions", False),
    ("permissions.revoke_override", "เพิกถอนสิทธิ์",            "permissions", False),
    ("menus.create",                "สร้างเมนู",                "menus", False),
    ("menus.update",                "แก้ไขเมนู",                "menus", False),
    ("menus.delete",                "ลบเมนู",                   "menus", False),
    ("menus.reorder",               "จัดลำดับเมนู",             "menus", False),
    ("admin_settings.update",       "แก้ App Settings",        "admin", False),
    # FarmLog — Suppliers
    ("suppliers.read",   "ดูรายการ Supplier",   "farmlog", True),
    ("suppliers.create", "สร้าง Supplier",       "farmlog", False),
    ("suppliers.update", "แก้ไข Supplier",       "farmlog", False),
    ("suppliers.delete", "ปิด/ลบ Supplier",      "farmlog", False),
    # FarmLog — Plots
    ("plots.read",   "ดูรายการแปลง",   "farmlog", True),
    ("plots.create", "สร้างแปลง",       "farmlog", False),
    ("plots.update", "แก้ไขแปลง",       "farmlog", False),
    ("plots.delete", "ปิด/ลบแปลง",      "farmlog", False),
    ("plots.assign", "มอบหมาย user ให้แปลง", "farmlog", False),
    # FarmLog — Records
    ("records.read",   "ดูบันทึกการตรวจแปลง", "farmlog", True),
    ("records.create", "สร้างบันทึก",           "farmlog", False),
    ("records.update", "แก้ไขบันทึก",           "farmlog", False),
    ("records.delete", "ลบ/ปิดบันทึก",          "farmlog", False),
    # FarmLog — Field Master (manageFields — super_admin only via all-perms bind)
    ("fielddefs.read",   "จัดการฟิลด์ฟอร์ม (Field Master)", "farmlog", True),
    ("fielddefs.create", "เพิ่ม custom field",  "farmlog", False),
    ("fielddefs.update", "แก้ไข field",          "farmlog", False),
    ("fielddefs.delete", "ลบ custom field",      "farmlog", False),
    # FarmLog — Master Data (dropdown options — manageMaster)
    ("masterdata.read",   "จัดการ Master Data (dropdown)", "farmlog", True),
    ("masterdata.create", "เพิ่มตัวเลือก",        "farmlog", False),
    ("masterdata.update", "แก้ไขตัวเลือก",        "farmlog", False),
    ("masterdata.delete", "ลบตัวเลือก",           "farmlog", False),
]


# (name, display_name, provider_scope, [permission_key...])  None = all (*).
#
# Role design (v3.0.3): roles only carry action perms — menu visibility
# rides on the same `*.read` perm as the list endpoint. The Dashboard +
# Settings index pages are reachable to any authenticated user; they
# render an empty grid if the user has no perms.
DEFAULT_ROLES: list[tuple[str, str, str, list[str] | None]] = [
    ("internal:super_admin", "Super Admin",  "internal", None),
    ("internal:admin",       "Admin",        "internal", [
        "users.read", "users.create", "users.update", "users.deactivate",
        # Approval gate is a separate perm so a future "user-editor" role
        # can edit profile fields without being able to approve accounts.
        # Admin gets it by default — admin IS the approver in v3.0.
        "users.approve",
        # Admin can assign roles to users (gated downstream — hierarchy
        # guard in _require_role_assign blocks granting super_admin
        # unless the caller IS one).
        "roles.read", "roles.assign",
        # Per-user overrides (Pattern B): admin can grant/revoke HOST-APP
        # feature keys per person. System keys (permissions.*,
        # admin_settings.*, users.approve/create/delete/deactivate,
        # roles.*, menus.delete) stay super_admin-only — enforced by the
        # deny-list in users.py add_override, NOT by this perm.
        "permissions.grant_override", "permissions.revoke_override",
        # FarmLog — admin manages supplier + plot master data + records
        "suppliers.read", "suppliers.create", "suppliers.update", "suppliers.delete",
        "plots.read", "plots.create", "plots.update", "plots.delete", "plots.assign",
        "records.read", "records.create", "records.update", "records.delete",
    ]),
    ("internal:super_user",  "Super User",   "internal", [
        "menus.read", "menus.update", "menus.reorder",
    ]),
    ("internal:user",        "User",         "internal", []),
    # FarmLog roles (Step 4 will wire supplier_id; permissions filled at Step 7)
    ("farmlog:supervisor",   "Supervisor",          "internal", [
        "suppliers.read", "plots.read", "plots.assign",
        "records.read", "records.create", "records.update",
        # manageMaster — supervisor curates dropdown options (Spec §10.4)
        "masterdata.read", "masterdata.create", "masterdata.update", "masterdata.delete",
    ]),
    ("supplier:owner",       "Supplier Owner",      "external", [
        "suppliers.read", "plots.read",
        # Self-service plots: an owner creates/edits plots for their OWN
        # supplier only — RLS scope 'supplier' hides everyone else's rows,
        # and create_plot (app/api/v1/plots.py) additionally rejects a
        # payload naming another supplier with a clean 403.
        "plots.create", "plots.update",
        # Round 8-4F: an owner records inspections for their OWN supplier's
        # plots. records.create only unlocks the ACTION — the data boundary
        # stays RLS scope 'supplier' (app/api/deps/scope.py: owner + supplier_id
        # → scope 'supplier'; no supplier_id → 'none'/fail-closed). The create
        # endpoint (app/api/v1/records.py) additionally resolves the plot under
        # that scope (a foreign plot returns a generic 404, never leaking that
        # it exists) and derives supplier_id from the plot, never the client
        # body. Authentication is not authorization: this key is the action,
        # RLS + the endpoint's plot resolution are the limits. Deliberately NOT
        # records.update/delete — owners append inspections, never edit/remove
        # history.
        "records.read", "records.create",
    ]),
    ("supplier:staff",       "Supplier Staff",      "external", [
        # Read-only: staff view their supplier's records but cannot create
        # (records.create stays owner-only in round 8-4F).
        "records.read",
    ]),
    ("farmlog:field_officer", "Field Officer",      "internal", [
        "suppliers.read", "plots.read",
        "records.read", "records.create", "records.update",
    ]),
    ("external:admin",       "Admin (ภายนอก)", "external", [
        "users.read", "users.create", "users.update", "users.approve",
    ]),
    ("external:user",        "User (ภายนอก)",  "external", []),
]


# (key, label_th, label_en, icon, path, parent_key, order, perm_key)
#
# Menu perm rules (v3.0.3):
# - perm_key = ""  → no permission needed; visible to all authenticated.
#                   Parents with empty perm also auto-hide if all their
#                   children get filtered out (see app/services/menus.py).
# - perm_key = "<action>.read" → ties menu to the same read perm that
#                                gates the underlying list endpoint.
DEFAULT_MENUS: list[tuple[str, str, str, str | None, str, str | None, int, str]] = [
    ("dashboard",       "Dashboard",   "Dashboard", "LayoutDashboard",  "/",                None,        10, ""),
    ("settings",        "การตั้งค่า",  "Settings",  "Settings",         "/settings",        None,        90, ""),
    ("settings.users",  "ผู้ใช้งาน",   "Users",     "Users",            "/settings/users",  "settings",  10, "users.read"),
    ("settings.roles",  "Roles",       "Roles",     "Shield",           "/settings/roles",  "settings",  20, "roles.read"),
    ("settings.menus",  "เมนู",        "Menus",     "List",             "/settings/menus",  "settings",  30, "menus.read"),
    ("settings.auth",   "การ Login",   "Login",     "Key",              "/settings/auth",   "settings",  40, "admin_settings.read"),
    ("settings.system_logs", "System Logs", "System Logs", "Activity",   "/settings/system-logs", "settings", 50, "system_logs.read"),
    ("settings.activity_logs", "ประวัติการใช้งาน", "Activity Logs", "History", "/settings/activity-logs", "settings", 55, "activity_logs.read"),
    # FarmLog section
    ("farmlog",                "FarmLog",       "FarmLog",       "Leaf",         "/farmlog",                     None,       20, ""),
    ("farmlog.admin",          "จัดการข้อมูล", "Admin",          "Database",     "/farmlog/admin",               "farmlog",  10, "suppliers.read"),
    ("farmlog.admin.suppliers","Suppliers",     "Suppliers",     "Building2",    "/farmlog/admin/suppliers",     "farmlog.admin", 10, "suppliers.read"),
    ("farmlog.admin.plots",    "แปลง",          "Plots",         "MapPin",       "/farmlog/admin/plots",         "farmlog.admin", 20, "plots.read"),
    ("farmlog.admin.fields",   "ฟิลด์ฟอร์ม",    "Field Master",  "SlidersHorizontal", "/farmlog/admin/fields",   "farmlog.admin", 30, "fielddefs.read"),
    ("farmlog.admin.masterdata","Master Data",   "Master Data",   "ListChecks",   "/farmlog/admin/masterdata",    "farmlog.admin", 40, "masterdata.read"),
    # FarmLog — Records (visible to all roles with records.read)
    ("farmlog.records",        "บันทึกการตรวจ", "Records",       "ClipboardList", "/farmlog/records",             "farmlog",       30, "records.read"),
    # FarmLog — Reports (gated by plots.read; the reports read from the
    # plots table's denormalized status, so no separate report permission).
    ("farmlog.reports",            "รายงาน",     "Reports",     "BarChart3", "/farmlog/reports",             "farmlog",         40, "plots.read"),
    ("farmlog.reports.plotstatus", "สถานะแปลง",  "Plot Status", "Table2",    "/farmlog/reports/plot-status", "farmlog.reports", 10, "plots.read"),
]


# --- Optional module: Database Connections + Query Sandbox ---------------
# Gated by FEATURE_DB_CONNECTIONS (config.py). When the flag is off these are
# never seeded, so the sidebar menu, settings cards, and routes all stay
# hidden — the app is permission-driven, so no frontend change is needed.
# super_admin still receives these via the keys=None all-perms binding in
# DEFAULT_ROLES; they're NOT listed in any other role and are in users.py
# _PRIVILEGE_MANAGEMENT_KEYS so a per-user override can't grant them.
_DB_CONNECTIONS_PERMISSIONS: list[tuple[str, str, str, bool]] = [
    ("db_connections.read",         "ดู Database Connections", "database", True),
    ("db_connections.manage",       "จัดการ DB Connections",   "database", False),
    ("db_connections.query",        "รัน Query Sandbox",       "database", False),
]
_DB_CONNECTIONS_MENUS: list[tuple[str, str, str, str | None, str, str | None, int, str]] = [
    ("settings.db_connections", "ฐานข้อมูล", "Database Connections", "Database", "/settings/db-connections", "settings", 60, "db_connections.read"),
    ("settings.query_sandbox", "Query Sandbox", "Query Sandbox", "Terminal", "/settings/query-sandbox", "settings", 65, "db_connections.query"),
]
# Query Sandbox safety limits — admin-tunable via /settings/admin (no redeploy).
# Read by db_connection_service.run_query. Conservative defaults.
_DB_CONNECTIONS_SETTINGS: list[tuple[str, object, str, str]] = [
    ("db_sandbox.statement_timeout_ms", 30000, "integer", "database"),
    ("db_sandbox.max_rows",             1000,  "integer", "database"),
]


def _scope() -> str:
    return (os.getenv("AUTH_SCOPE") or "both").lower()


async def _seed_permissions(db: AsyncSession) -> dict[str, Permission]:
    out: dict[str, Permission] = {}
    permissions = list(DEFAULT_PERMISSIONS)
    if get_settings().FEATURE_DB_CONNECTIONS:
        permissions += _DB_CONNECTIONS_PERMISSIONS
    for key, display_name, category, is_menu in permissions:
        existing = (await db.execute(select(Permission).where(Permission.key == key))).scalar_one_or_none()
        if existing is None:
            existing = Permission(key=key, display_name=display_name, category=category, is_menu=is_menu)
            db.add(existing)
            await db.flush()
        out[key] = existing
    return out


async def _seed_roles(db: AsyncSession, perms: dict[str, Permission]) -> dict[str, Role]:
    out: dict[str, Role] = {}
    all_perms = list(perms.values())
    for name, display_name, provider_scope, keys in DEFAULT_ROLES:
        existing = (await db.execute(
            select(Role).where(Role.name == name)
            .options(selectinload(Role.permissions))
        )).scalar_one_or_none()
        if existing is None:
            existing = Role(
                name=name, display_name=display_name, provider_scope=provider_scope,
                is_system=True,
            )
            db.add(existing)
            await db.flush()
            # Freshly-flushed row: relationship not loaded yet. Assigning to
            # it would trigger an async lazy-load (MissingGreenlet).
            await db.refresh(existing, attribute_names=["permissions"])
        # Reset perm bindings — idempotent re-run keeps the canonical mapping.
        if keys is None:
            existing.permissions = all_perms
        else:
            existing.permissions = [perms[k] for k in keys if k in perms]
        out[name] = existing
    return out


async def _seed_menus(db: AsyncSession) -> None:
    by_key: dict[str, MenuItem] = {}
    menus = list(DEFAULT_MENUS)
    if get_settings().FEATURE_DB_CONNECTIONS:
        menus += _DB_CONNECTIONS_MENUS
    # First pass — items without parent.
    for key, label_th, label_en, icon, path, parent_key, order, perm_key in menus:
        existing = (await db.execute(select(MenuItem).where(MenuItem.key == key))).scalar_one_or_none()
        if existing is None:
            existing = MenuItem(
                key=key, label_th=label_th, label_en=label_en, icon=icon,
                path=path, order_index=order, required_permission_key=perm_key,
                is_system=True,
            )
            db.add(existing)
            await db.flush()
        by_key[key] = existing
    # Second pass — set parent_id now that keys are loaded.
    for key, *_rest, parent_key, _order, _perm_key in menus:
        if parent_key:
            by_key[key].parent_id = by_key[parent_key].id


async def _seed_app_settings(db: AsyncSession) -> None:
    scope = _scope()
    # Pricing seeds are starter values per the public Claude pricing page —
    # admin must verify against current rates and adjust via /settings/admin
    # (super-admin only) before relying on cost reports.
    sonnet_pricing = {"input": 3.0, "output": 15.0,
                      "cache_read": 0.30, "cache_write": 3.75}
    haiku_pricing = {"input": 1.0, "output": 5.0,
                     "cache_read": 0.10, "cache_write": 1.25}
    defaults: list[tuple[str, object, str, str]] = [
        ("auth.local.enabled",        scope != "internal_only", "boolean", "auth"),
        ("auth.sso.enabled",          scope != "external_only", "boolean", "auth"),
        ("auth.local.signup_enabled", False,                    "boolean", "auth"),
        ("auth.signup_default_role",  "external:user",         "string",  "auth"),
        # AI pricing — used by AiCallLogger._estimate_cost (docs/logging.md §3.2)
        ("ai.pricing.claude-sonnet-4-6",         sonnet_pricing, "json", "pricing"),
        ("ai.pricing.claude-haiku-4-5-20251001", haiku_pricing,  "json", "pricing"),
        # Log retention overrides — read by retention.drop_old_partitions
        ("logging.activity.retention_days", 90,  "integer", "logging"),
        ("logging.system.retention_days",   60,  "integer", "logging"),
        ("logging.ai.retention_days",       180, "integer", "logging"),
        # Email notifications (M365 Graph) — disabled by default so the
        # scaffold doesn\'t spam noreply mail from a blank-config install.
        # Admin enables via /settings/notifications after configuring
        # M365_* env vars + supplying admin_recipients.
        ("notifications.email.enabled",          False, "boolean", "notifications"),
        ("notifications.email.admin_recipients", [],    "json",    "notifications"),
    ]
    if get_settings().FEATURE_DB_CONNECTIONS:
        defaults += _DB_CONNECTIONS_SETTINGS
    for key, value, value_type, category in defaults:
        existing = (await db.execute(select(AppSetting).where(AppSetting.key == key))).scalar_one_or_none()
        if existing is None:
            db.add(AppSetting(key=key, value=value, value_type=value_type, category=category))


async def _seed_bootstrap_user(db: AsyncSession, roles: dict[str, Role]) -> None:
    email = os.getenv("AUTH_BOOTSTRAP_SUPER_ADMIN_EMAIL", "").strip().lower()
    if not email:
        return
    existing = (await db.execute(select(User).where(func.lower(User.email) == email))).scalar_one_or_none()
    if existing is not None:
        return
    auth_type = (os.getenv("AUTH_BOOTSTRAP_SUPER_ADMIN_AUTH_TYPE") or "sso").lower()
    user = User(
        email=email,
        full_name=email.split("@", 1)[0],
        auth_provider="local" if auth_type == "local" else "azure_ad",
        is_active=True,
        is_approved=True,  # bootstrap super-admin is self-approved by definition
        email_verified=True,
    )
    if auth_type == "local":
        password = os.getenv("AUTH_BOOTSTRAP_INITIAL_PASSWORD", "")
        if not password:
            raise RuntimeError(
                "AUTH_BOOTSTRAP_INITIAL_PASSWORD env required when "
                "AUTH_BOOTSTRAP_SUPER_ADMIN_AUTH_TYPE=local"
            )
        # Defense-in-depth: the wizard already enforces the strength policy,
        # but re-check here so a manually-set weak env value fails with a
        # clear message instead of an opaque traceback mid-seed.
        try:
            user.password_hash = hash_password(
                password, context_terms=[email.split("@", 1)[0]]
            )
        except PasswordPolicyError as exc:
            raise RuntimeError(
                f"AUTH_BOOTSTRAP_INITIAL_PASSWORD is too weak: {exc}"
            ) from exc
    db.add(user)
    await db.flush()
    super_admin_role = roles.get("internal:super_admin")
    if super_admin_role is not None:
        await db.refresh(user, attribute_names=["roles"])
        user.roles = [super_admin_role]


async def _seed_external_field_helper_user(db: AsyncSession) -> None:
    """System user that recorded_by_id points to for records created via
    the public (unauthenticated) inspection-code flow (round 8) —
    recorded_by_id is NOT NULL and there's no real logged-in user in that
    flow. No role, no password: never authenticated as, only ever
    referenced as a foreign key."""
    existing = (await db.execute(
        select(User).where(func.lower(User.email) == EXTERNAL_FIELD_HELPER_EMAIL)
    )).scalar_one_or_none()
    if existing is not None:
        return
    db.add(User(
        email=EXTERNAL_FIELD_HELPER_EMAIL,
        full_name="External Field Helper (system)",
        auth_provider="system",
        is_active=True,
        is_approved=True,
        email_verified=True,
    ))


async def seed() -> None:
    async with get_db_session() as db:
        perms = await _seed_permissions(db)
        roles = await _seed_roles(db, perms)
        await _seed_menus(db)
        await _seed_app_settings(db)
        await _seed_bootstrap_user(db, roles)
        await _seed_external_field_helper_user(db)
        await db.commit()


async def main() -> None:
    # Load .env into os.environ so the AUTH_BOOTSTRAP_* vars below are
    # visible to os.getenv. Settings loads .env separately, but only into
    # the Settings object — not the process env.
    from dotenv import load_dotenv
    load_dotenv()
    await init_db()
    try:
        await seed()
    finally:
        await close_db()


if __name__ == "__main__":
    asyncio.run(main())
