#!/usr/bin/env python3
"""
Interactive project initialization script.

วิธีใช้:
    python scripts/init_project.py

Script นี้:
1. ถาม project metadata จาก user
2. Validate input
3. เขียน project.config (single source of truth)
4. Update ไฟล์ที่ต้องมี project name hard-coded:
   - backend/pyproject.toml
   - frontend/package.json
   - docker/docker-compose.yml
   - README.md
5. Generate backend/.env และ frontend/.env จาก template
6. Print next steps
"""
from __future__ import annotations

import json
import re
import secrets
import sys
from dataclasses import dataclass, field
from pathlib import Path

# scaffold functions ใช้ร่วมกับ setup.py — ดู scripts/scaffold.py
sys.path.insert(0, str(Path(__file__).resolve().parent))
from scaffold import (
    _branded_app_name,
    _scaffold_backend,
    _scaffold_docker,
    _scaffold_frontend,
    _scaffold_global_standards,
    _scaffold_tooling,
)
# Shared utf-8 stdio guard + masked password input — see scripts/_stdio.py.
from _stdio import ensure_utf8_stdio, getpass_masked  # noqa: E402
# Shared password-strength policy — same bar as scaffolded runtime (scaffold.py).
from password_policy import password_strength_error  # noqa: E402


# ───────────────────────────────────────────────────────────────
# Configuration data class
# ───────────────────────────────────────────────────────────────


@dataclass
class ProjectConfig:
    project_slug: str = ""
    project_display_name: str = ""
    project_description: str = ""
    auth_scope: str = "both"  # internal_only | external_only | both
    maturity_level: str = "L1"  # L0 | L1 | L2 | L3 (see AGENTS.md §13)
    stack_variant: str = "default"  # default | next-js | htmx | streamlit | timescale
    # Opt-in Database Connections + Query Sandbox module (docs/patterns/
    # db-connections.md). When True the scaffold emits the module + sets the
    # FEATURE_DB_CONNECTIONS flag; the operator must also generate a Fernet key.
    feature_db_connections: bool = False
    database_mode: str = "new"  # new | existing; advanced flow uses setup.py
    azure_ad_tenant_id: str = ""
    azure_ad_client_id: str = ""
    production_url: str = ""
    staging_url: str = ""
    default_language: str = "th"
    cicd_platform: str = "github_actions"
    env_var_prefix: str = ""
    prod_db_host: str = ""  # centralized DB hostname for staging/production
    registry_url: str = ""  # empty = skip registration (opt-in; required at L3)
    # Governance (AGENTS.md §10) — recommended L0/L1, required L2+
    app_owner: str = ""
    tech_owner: str = ""
    security_approver: str = ""
    data_owner: str = ""
    # CT extensions (not in AGENTS.md §10 — used by Registry for ownership tracking)
    owner: str = ""
    bu: str = ""

    # Derived values (generated, not asked)
    jwt_secret_key: str = field(default="")
    # AES-256 key for Auth module MFA TOTP secrets at rest (32-byte hex).
    # Always generated — see setup.py ProjectConfig for the rationale.
    mfa_encryption_key: str = field(default="")
    # Random dev DB password — eliminates the old hardcoded 'devpassword123'
    # default so two scaffolded projects never share credentials (audit #1).
    dev_db_password: str = field(default="")

    # Bootstrap super-admin — see setup.py ProjectConfig.
    bootstrap_admin_email: str = ""
    bootstrap_admin_auth_type: str = "sso"
    bootstrap_initial_password: str = ""

    def __post_init__(self) -> None:
        if not self.jwt_secret_key:
            self.jwt_secret_key = secrets.token_hex(32)
        if not self.mfa_encryption_key:
            self.mfa_encryption_key = secrets.token_hex(32)
        if not self.dev_db_password:
            self.dev_db_password = secrets.token_urlsafe(16)

    def to_config_file(self) -> str:
        """Render as project.config format. See AGENTS.md §10 for full contract."""
        return f"""# project.config — auto-generated, single source of truth
# DO NOT commit this file. Add to .gitignore.

PROJECT_SLUG={self.project_slug}
PROJECT_DISPLAY_NAME={self.project_display_name}
PROJECT_DESCRIPTION={self.project_description}

# Maturity (v3.0 — gates Tier 4 patterns; AGENTS.md §13)
MATURITY_LEVEL={self.maturity_level}

# Stack variant (v3.0 — set at init only; AGENTS.md §A)
STACK_VARIANT={self.stack_variant}

# Database ownership mode: new | existing
DATABASE_MODE={self.database_mode}

AUTH_SCOPE={self.auth_scope}

# Feature Modules (opt-in — default off; docs/patterns/db-connections.md)
FEATURE_DB_CONNECTIONS={str(self.feature_db_connections).lower()}

AZURE_AD_TENANT_ID={self.azure_ad_tenant_id}
AZURE_AD_CLIENT_ID={self.azure_ad_client_id}

PRODUCTION_URL={self.production_url}
STAGING_URL={self.staging_url}

# Governance (AGENTS.md §10) — recommended L0/L1, required L2+
APP_OWNER={self.app_owner}
TECH_OWNER={self.tech_owner}
SECURITY_APPROVER={self.security_approver}
DATA_OWNER={self.data_owner}

# Centralized DB (staging/production) — ตั้งใน backend/.env ตอน deploy
# Local dev ใช้ DB_HOST=localhost (DB ใน Docker) — ไม่ต้องแก้
PROD_DB_HOST={self.prod_db_host}

DEFAULT_LANGUAGE={self.default_language}
CICD_PLATFORM={self.cicd_platform}
ENV_VAR_PREFIX={self.env_var_prefix}

# CT extensions (used by Registry for ownership tracking — not in AGENTS.md §10)
OWNER={self.owner}
BU={self.bu}
"""


# ───────────────────────────────────────────────────────────────
# Prompt helpers
# ───────────────────────────────────────────────────────────────


def prompt(question: str, default: str = "", required: bool = True,
           validator=None) -> str:
    """Ask user with optional default and validation."""
    while True:
        prefix = f"  [{default}]" if default else ""
        answer = input(f"{question}{prefix}: ").strip()
        if not answer and default:
            answer = default
        if not answer and required:
            print("    ⚠️  Required. Please provide a value.")
            continue
        if validator:
            error = validator(answer)
            if error:
                print(f"    ⚠️  {error}")
                continue
        return answer


def prompt_choice(question: str, choices: list[tuple[str, str]],
                  default_index: int = 0) -> str:
    """Ask user to pick from choices. Returns the value (not the label)."""
    print(f"{question}")
    for i, (_, label) in enumerate(choices, 1):
        marker = " (default)" if i - 1 == default_index else ""
        print(f"  {i}. {label}{marker}")
    while True:
        answer = input(f"  Choose [1-{len(choices)}]: ").strip()
        if not answer:
            return choices[default_index][0]
        try:
            idx = int(answer) - 1
            if 0 <= idx < len(choices):
                return choices[idx][0]
        except ValueError:
            pass
        print("    ⚠️  Invalid choice.")


def prompt_yes_no(question: str, default_yes: bool = True) -> bool:
    default = "Y/n" if default_yes else "y/N"
    answer = input(f"{question} [{default}]: ").strip().lower()
    if not answer:
        return default_yes
    return answer in ("y", "yes")


# ───────────────────────────────────────────────────────────────
# Validators
# ───────────────────────────────────────────────────────────────


def validate_slug(value: str) -> str | None:
    if not re.match(r"^[a-z][a-z0-9-]{1,49}$", value):
        return ("Slug must be lowercase, start with letter, "
                "contain only [a-z0-9-], 2-50 chars")
    return None


def validate_uuid(value: str) -> str | None:
    if not value:
        return None  # optional, will check elsewhere
    if not re.match(
        r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
        value.lower(),
    ):
        return "Must be a valid UUID (e.g. 00000000-0000-0000-0000-000000000000)"
    return None


def validate_url(value: str) -> str | None:
    if not value:
        return None  # optional at init time
    if not re.match(r"^https?://[^\s]+$", value):
        return "Must be a valid URL starting with http:// or https://"
    return None


def validate_prefix(value: str) -> str | None:
    if not value:
        return None
    if not re.match(r"^[A-Z][A-Z0-9_]{1,15}$", value):
        return ("Prefix must be UPPERCASE letters/digits/underscores, "
                "start with letter, 2-16 chars")
    return None


_EMAIL_RE = re.compile(r"^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$")


def _prompt_bootstrap_super_admin(cfg: "ProjectConfig") -> None:
    """Mirror of setup.py._prompt_bootstrap_super_admin — see there for rationale."""
    print("── Bootstrap Super Admin ─────────────────────────────────")
    print("  First user (role internal:super_admin) — used for initial login.")

    while True:
        email = input("  Super admin email: ").strip()
        if not email:
            print("    ⚠️  Required.")
            continue
        if not _EMAIL_RE.match(email):
            print("    ⚠️  Invalid email format.")
            continue
        cfg.bootstrap_admin_email = email.lower()
        break

    # Default to the auth type that GUARANTEES login for the chosen scope so a
    # user accepting defaults never locks themselves out (mirrors setup.py):
    #   external_only → local; internal_only → SSO (seed disables local);
    #   both → local (works without Azure config).
    if cfg.auth_scope == "external_only":
        cfg.bootstrap_admin_auth_type = "local"
        print("  Auth type:  local (external_only scope)")
    elif cfg.auth_scope == "internal_only":
        cfg.bootstrap_admin_auth_type = "sso"
        print("  Auth type:  SSO / Azure AD (internal_only scope)")
        print("  ⚠️  Configure Azure AD in backend/.env first — "
              "internal_only disables email/password login.")
    else:
        cfg.bootstrap_admin_auth_type = prompt_choice(
            "Auth type for super admin?",
            [("local", "Local — email + password (works immediately, no Azure AD)"),
             ("sso", "SSO — Azure AD (production with Azure AD configured)")],
            default_index=0,
        )

    if cfg.bootstrap_admin_auth_type == "local":
        print("  Initial password (≥ 12 chars, shows '*' per keystroke)")
        print("  • Mix ≥ 2 of 4: upper / lower / digit / symbol — no all-letter")
        print("    or all-number, sequences (123456789012), or common passwords.")
        print("  • One-shot for seed; not committed; change on first login.")
        context_terms = [cfg.bootstrap_admin_email.split("@", 1)[0], cfg.project_slug]
        while True:
            pw = getpass_masked("  Password: ")
            err = password_strength_error(pw, context_terms=context_terms)
            if err:
                print(f"    ⚠️  {err}")
                continue
            pw2 = getpass_masked("  Confirm: ")
            if pw != pw2:
                print("    ⚠️  Passwords don't match — try again.")
                continue
            cfg.bootstrap_initial_password = pw
            break
    print()


# ───────────────────────────────────────────────────────────────
# Interactive flow
# ───────────────────────────────────────────────────────────────


def collect_config() -> ProjectConfig:
    print()
    print("=" * 60)
    print("  Web App Standard — Project Initialization")
    print("=" * 60)
    print()
    print("  Script นี้จะถาม 3 กลุ่ม แล้ว generate ไฟล์ทั้งหมดให้")
    print()
    print("  ┌─ ต้องรู้ตอนนี้ ──────────────────────────────────────┐")
    print("  │  • ชื่อ project (slug) เช่น fertilizer-executive-portal│")
    print("  │  • ประเภท user: internal / external / ทั้งคู่          │")
    print("  └──────────────────────────────────────────────────────┘")
    print()
    print("  ┌─ มีก็ดี ข้ามได้ถ้าไม่มี ────────────────────────────┐")
    print("  │  • Production URL / Staging URL                       │")
    print("  │  • Production DB hostname (ถ้ายังไม่มี ข้ามได้)      │")
    print("  └──────────────────────────────────────────────────────┘")
    print()
    print("  ┌─ แนะนำข้ามก่อน เพิ่มทีหลังได้ ─────────────────────┐")
    print("  │  • Azure AD Tenant/Client ID                          │")
    print("  │    → ข้ามโดยเลือก 'External only' แล้วเปลี่ยน        │")
    print("  │      scope ทีหลังใน project.config                    │")
    print("  │  • Env var prefix (ใช้เฉพาะ shared infra)            │")
    print("  └──────────────────────────────────────────────────────┘")
    print()
    input("  กด Enter เพื่อเริ่ม...")
    print()

    cfg = ProjectConfig()

    # ── Identity
    print("── Project Identity ─────────────────────────────────────")
    cfg.project_slug = prompt(
        "Project slug (lowercase, kebab-case, e.g. chiatai-vendor-portal)",
        validator=validate_slug,
    )
    cfg.project_display_name = prompt(
        "Display name (Title Case, e.g. Chia Tai Vendor Portal)",
    )
    cfg.project_description = prompt(
        "One-line description",
    )
    print()

    # ── Auth scope
    print("── Authentication Scope ─────────────────────────────────")
    cfg.auth_scope = prompt_choice(
        "Which user types does this project support?",
        [
            ("both", "Both — internal (Azure AD) + external (local auth)"),
            ("internal_only", "Internal only — employees via Azure AD"),
            ("external_only", "External only — vendors/customers via local auth"),
        ],
        default_index=0,
    )
    print()

    # ── Bootstrap super admin
    _prompt_bootstrap_super_admin(cfg)

    # ── Azure AD (if applicable)
    if cfg.auth_scope in ("both", "internal_only"):
        print("── Azure AD Configuration ───────────────────────────────")
        print("  (Get these from your Azure AD app registration)")
        cfg.azure_ad_tenant_id = prompt(
            "Azure AD Tenant ID (UUID)",
            validator=validate_uuid,
        )
        cfg.azure_ad_client_id = prompt(
            "Azure AD Client ID (UUID)",
            validator=validate_uuid,
        )
        print("  Note: AZURE_AD_CLIENT_SECRET will be set in .env after init")
        print()

    # ── URLs
    print("── Deployment URLs (optional at init time) ──────────────")
    cfg.production_url = prompt(
        "Production URL (e.g. https://app.example.com)",
        required=False,
        validator=validate_url,
    )
    cfg.staging_url = prompt(
        "Staging URL (e.g. https://staging.app.example.com)",
        required=False,
        validator=validate_url,
    )
    print()

    # ── Database
    print("── Production Database ───────────────────────────────────")
    print("  Local dev ใช้ DB ใน Docker อัตโนมัติ")
    print("  ถ้า staging/production ใช้ centralized DB — ใส่ hostname ที่นี่")
    cfg.prod_db_host = prompt(
        "Production/Staging DB hostname or IP (leave blank if using Docker DB)",
        required=False,
    )
    print()

    # ── Maturity level + stack variant (v3.0)
    print("── Maturity Level ───────────────────────────────────────")
    print("  L0 Prototype       : < 3 months OR < 10 users OR MVP/POC")
    print("  L1 Internal Tool   : Stable, single BU, < 50 users (DEFAULT)")
    print("  L2 Business-Critical: Multi-BU, mission-critical")
    print("  L3 External/Regulated: Vendor/customer-facing, PDPA scope")
    cfg.maturity_level = prompt_choice(
        "Maturity level (AGENTS.md §13)",
        [("L1", "L1 Internal Tool"),
         ("L0", "L0 Prototype"),
         ("L2", "L2 Business-Critical"),
         ("L3", "L3 External/Regulated")],
        default_index=0,
    )
    print()

    print("── Stack Variant ────────────────────────────────────────")
    print("  default   : Internal CRUD, dashboard, standard web app")
    print("  next-js   : Public site that needs SEO/SSR (vendor/customer-facing)")
    print("  htmx      : Tiny internal tool < 5 pages, no SPA needed")
    print("  streamlit : AI prototype < 1 month (L0 only — refactor before L1)")
    print("  timescale : Heavy time-series (sensor, IoT, telemetry, metrics)")
    cfg.stack_variant = prompt_choice(
        "Stack variant (AGENTS.md §A)",
        [("default", "default — FastAPI + React/Vite (standard web app)"),
         ("next-js", "next-js — Next.js + FastAPI BFF (SEO/SSR)"),
         ("htmx", "htmx — FastAPI + HTMX + Jinja (tiny internal tool)"),
         ("streamlit", "streamlit — Streamlit/Gradio (AI prototype, L0 only)"),
         ("timescale", "timescale — Default + TimescaleDB (time-series)")],
        default_index=0,
    )
    print()

    # ── Feature Modules — opt-in (default off) ───────────────────────
    print("── Feature Modules ──────────────────────────────────────")
    print("  Database Connections + Query Sandbox: super_admin registers")
    print("  external PostgreSQL via the UI and runs audited, read-only-by-")
    print("  default SQL. Enable only when the project connects to external DBs.")
    cfg.feature_db_connections = prompt_yes_no(
        "Enable Database Connections module?", default_yes=False
    )
    if cfg.feature_db_connections:
        print("  ⚠️  Generate a Fernet key and set DB_CONNECTIONS_ENCRYPTION_KEY in")
        print("      backend/.env before use (see docs/patterns/db-connections.md):")
        print("      python -c \"from cryptography.fernet import Fernet; "
              "print(Fernet.generate_key().decode())\"")
    print()

    # ── CT App Registry — opt-in (required at L3, optional elsewhere)
    print("── CT App Registry ──────────────────────────────────────")
    if cfg.maturity_level == "L3":
        print("  L3 project — Registry registration is required.")
        cfg.registry_url = prompt(
            "Registry URL",
            default="https://ctappregistry.chiataigroup.com",
            required=True,
            validator=validate_url,
        )
    else:
        print(f"  {cfg.maturity_level} project — Registry registration is optional.")
        print("  (Track in central catalog + daily telemetry push)")
        if prompt_yes_no("  Register with CT App Registry?", default_yes=False):
            cfg.registry_url = prompt(
                "Registry URL",
                default="https://ctappregistry.chiataigroup.com",
                required=True,
                validator=validate_url,
            )
        else:
            cfg.registry_url = ""
    print()

    # ── CT extension: Owner + Business Unit (Registry tracking)
    print("── Project Owner & Business Unit (CT Registry) ──────────")
    cfg.owner = prompt(
        "Owner (name or email)",
        required=False,
    )
    cfg.bu = prompt_choice(
        "Business Unit",
        [("fertilizer", "Fertilizer"),
         ("crop_protection", "Crop Protection"),
         ("seed", "Seed"),
         ("other", "Other / Multi-BU")],
        default_index=3,
    )
    print()

    # ── Governance (AGENTS.md §10) — required at L2+, recommended otherwise
    print("── Governance Owners (AGENTS.md §10) ────────────────────")
    governance_required = cfg.maturity_level in ("L2", "L3")
    if governance_required:
        print(f"  {cfg.maturity_level} project — governance owners are required.")
    else:
        print(f"  {cfg.maturity_level} project — governance owners optional (leave blank to fill later).")
    cfg.app_owner = prompt(
        "App owner (business accountable; name or email)",
        default=cfg.owner,
        required=governance_required,
    )
    cfg.tech_owner = prompt(
        "Tech owner (code + deploy accountable; name or email)",
        required=governance_required,
    )
    cfg.security_approver = prompt(
        "Security approver for Risky ops (§4; blank → defaults to tech owner)",
        required=False,
    )
    cfg.data_owner = prompt(
        "Data owner (required if handling PII/regulated data)",
        required=cfg.maturity_level == "L3",
    )
    print()

    # ── Options
    print("── Options ──────────────────────────────────────────────")
    cfg.default_language = prompt_choice(
        "Default UI language",
        [("th", "Thai"), ("en", "English")],
        default_index=0,
    )
    cfg.cicd_platform = prompt_choice(
        "CI/CD platform",
        [
            ("github_actions", "GitHub Actions"),
            ("gitlab_ci", "GitLab CI"),
            ("azure_devops", "Azure DevOps"),
        ],
        default_index=0,
    )
    cfg.env_var_prefix = prompt(
        "Env var prefix (optional; recorded for shared-infra docs, NOT applied to .env)",
        required=False,
        validator=validate_prefix,
    )
    print()

    return cfg


# ───────────────────────────────────────────────────────────────
# File operations
# ───────────────────────────────────────────────────────────────


def write_config_file(cfg: ProjectConfig, root: Path) -> None:
    (root / "project.config").write_text(cfg.to_config_file(), encoding="utf-8")
    print("✅ Wrote project.config")


def update_backend_pyproject(cfg: ProjectConfig, root: Path) -> None:
    """Update name + description in pyproject.toml."""
    path = root / "backend" / "pyproject.toml"
    if not path.exists():
        print(f"⚠️  Skipped {path} (not found)")
        return

    content = path.read_text(encoding="utf-8")
    content = re.sub(
        r'^name\s*=\s*"[^"]*"',
        f'name = "{cfg.project_slug}-backend"',
        content,
        count=1,
        flags=re.MULTILINE,
    )
    content = re.sub(
        r'^description\s*=\s*"[^"]*"',
        f'description = "{cfg.project_description}"',
        content,
        count=1,
        flags=re.MULTILINE,
    )
    path.write_text(content, encoding="utf-8")
    print(f"✅ Updated {path.name}")


def update_frontend_package(cfg: ProjectConfig, root: Path) -> None:
    """Update name + description in package.json."""
    path = root / "frontend" / "package.json"
    if not path.exists():
        print(f"⚠️  Skipped {path} (not found)")
        return

    data = json.loads(path.read_text(encoding="utf-8"))
    data["name"] = f"{cfg.project_slug}-frontend"
    data["description"] = cfg.project_description
    path.write_text(
        json.dumps(data, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    print(f"✅ Updated {path.name}")


def write_readme(cfg: ProjectConfig, root: Path) -> None:
    """Generate root README.md from template."""
    auth_description = {
        "both": "Azure AD SSO (internal) + Local auth (external)",
        "internal_only": "Azure AD SSO",
        "external_only": "Local auth (email + password)",
    }[cfg.auth_scope]

    readme = f"""# {cfg.project_display_name}

{cfg.project_description}

## Quick Start (Local Dev)

```bash
# 1. Start local DB only (docker/docker-compose.yml = DB-only)
cd docker && docker compose up -d && cd ..

# 2. Backend (local Python venv — recommended for dev hot-reload)
cd backend
# Windows: set DB_HOST=localhost
# macOS/Linux: export DB_HOST=localhost
alembic upgrade head
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000

# 3. Frontend (new terminal)
cd frontend && npm install && npm run dev
```

- Frontend: http://localhost:5173
- Backend API: http://localhost:8000
- API docs: http://localhost:8000/docs

For production deploy (full Docker stack): `./deploy.sh` (uses root `docker-compose.yml`)

## Documentation

- [`AGENTS.md`](AGENTS.md) — technical standard and architecture
- [`CLAUDE.md`](CLAUDE.md) — entry point for AI agents
- [`docs/human/onboarding.md`](docs/human/onboarding.md) — detailed developer setup
- [`docs/human/runbook.md`](docs/human/runbook.md) — operations and incident response
- [`docs/human/architecture.md`](docs/human/architecture.md) — system architecture

## Tech Stack

- **Backend:** FastAPI + Python 3.12+ + PostgreSQL 16+ + SQLAlchemy 2.0
- **Frontend:** React 18+ + TypeScript 5+ + Vite
- **Auth:** {auth_description}
- **AI:** Anthropic Claude API
- **Deployment:** Docker (multi-stage)

See [`AGENTS.md`](AGENTS.md) for full stack details.
"""
    (root / "README.md").write_text(readme, encoding="utf-8")
    print("✅ Wrote README.md")


def update_docker_compose(cfg: ProjectConfig, root: Path) -> None:
    """Ensure docker-compose uses ${PROJECT_SLUG} interpolation.

    Template should already use ${PROJECT_SLUG}; we just make sure
    .env is in place for it.
    """
    print("ℹ️  docker-compose uses ${PROJECT_SLUG} from .env (no changes needed)")


def generate_env_files(cfg: ProjectConfig, root: Path) -> None:
    """Generate backend/.env and frontend/.env with project values."""
    # ENV_VAR_PREFIX is recorded in project.config for shared-infra
    # documentation only — it is intentionally NOT applied to the generated
    # .env keys. The backend reads canonical names both through pydantic
    # Settings (which has no env_prefix) AND through raw os.getenv
    # (AUTH_SCOPE, AUTH_BOOTSTRAP_*), so prefixing the keys would make a
    # freshly-generated project fail to boot. setup.py never prefixes either;
    # keeping the two entry points identical is the whole point. See
    # docs/backend.md "ENV_VAR_PREFIX".
    prefix = ""

    # Backend .env
    backend_env = f"""# backend/.env — generated by init_project.py
# DO NOT commit this file.

# App
{prefix}APP_NAME={cfg.project_slug}
{prefix}APP_ENV=dev
{prefix}APP_DEBUG=false
{prefix}APP_LOG_LEVEL=INFO

# Auth scope — compile-time ceiling (both | internal_only | external_only).
# Read via raw os.getenv by admin_settings.py to lock provider toggles, and
# mirrored to the SPA through frontend VITE_AUTH_SCOPE. Must match setup.py.
AUTH_SCOPE={cfg.auth_scope}

# Frontend reference (used for CORS, OAuth callback)
PROJECT_SLUG={cfg.project_slug}

# API
{prefix}API_CORS_ORIGINS=http://localhost:5173

# Database
# LOCAL DEV: backend runs locally (Quick Start) and connects to Docker DB
# on the host network → DB_HOST=localhost.
# PRODUCTION (Docker compose with backend service): change to DB_HOST=db
# or centralized hostname — see backend/.env.prod.example + docs/deployment.md §6
{prefix}DB_HOST=localhost
{prefix}DB_PORT=5432
{prefix}DB_NAME={cfg.project_slug.replace('-', '_')}
{prefix}DB_USER={cfg.project_slug.replace('-', '_')}
{prefix}DB_PASSWORD={cfg.dev_db_password}
{prefix}DB_POOL_SIZE=10
{prefix}DB_MAX_OVERFLOW=20
"""

    if cfg.auth_scope in ("both", "internal_only"):
        backend_env += f"""
# Auth — Azure AD (Internal users)
{prefix}AZURE_AD_TENANT_ID={cfg.azure_ad_tenant_id}
{prefix}AZURE_AD_CLIENT_ID={cfg.azure_ad_client_id}
{prefix}AZURE_AD_CLIENT_SECRET=PASTE_FROM_AZURE_PORTAL
{prefix}AZURE_AD_REDIRECT_URI=
"""

    # JWT is emitted for EVERY scope — Settings.JWT_SECRET_KEY is required
    # (no default), and the app signs its own session token even for SSO
    # logins. Gating this behind external_only made internal_only projects
    # crash on boot (ValidationError: JWT_SECRET_KEY). Matches scaffold.py.
    backend_env += f"""
# Auth — Local JWT (also signs SSO session tokens)
{prefix}JWT_SECRET_KEY={cfg.jwt_secret_key}
{prefix}JWT_ALGORITHM=HS256
{prefix}JWT_ACCESS_TOKEN_EXPIRE_MINUTES=60
{prefix}JWT_REFRESH_TOKEN_EXPIRE_DAYS=7
"""

    # Auth module hooks — emitted regardless of auth_scope so the key stays
    # stable across the project lifetime even if MFA / SMTP get wired later.
    # AUTH_MFA_ENCRYPTION_KEY: AES-256 key for TOTP secrets at rest.
    #   ⚠️  Rotating it invalidates every enrolled user's MFA — back it up.
    # AUTH_FRONTEND_BASE_URL: public SPA origin for password-reset / invite
    #   email links. Emitted now; consumed once auth content is absorbed
    #   into the scaffold (planned).
    bootstrap_pw_line = (
        f"{prefix}AUTH_BOOTSTRAP_INITIAL_PASSWORD={cfg.bootstrap_initial_password}"
        if cfg.bootstrap_initial_password
        else f"# {prefix}AUTH_BOOTSTRAP_INITIAL_PASSWORD=<set-before-seed-if-local-auth>"
    )
    backend_env += f"""
# Auth Settings
{prefix}AUTH_MFA_ENCRYPTION_KEY={cfg.mfa_encryption_key}
{prefix}AUTH_BOOTSTRAP_SUPER_ADMIN_EMAIL={cfg.bootstrap_admin_email}
{prefix}AUTH_BOOTSTRAP_SUPER_ADMIN_AUTH_TYPE={cfg.bootstrap_admin_auth_type}
# Transient — used once by seed.py; delete after seed succeeds; never commit
{bootstrap_pw_line}
{prefix}AUTH_FRONTEND_BASE_URL=http://localhost:5173
{prefix}SMTP_HOST=
{prefix}SMTP_PORT=587
{prefix}SMTP_USERNAME=
{prefix}SMTP_PASSWORD=
{prefix}SMTP_FROM_EMAIL=
{prefix}SMTP_USE_TLS=true
"""

    backend_env += f"""
# AI
{prefix}CLAUDE_API_KEY=PASTE_YOUR_CLAUDE_KEY
{prefix}CLAUDE_MODEL=claude-sonnet-4-20250514

# Rate limiting
{prefix}RATE_LIMIT_PER_MINUTE=60

# CT App Registry — ดู docs/ops/registry.md
# Required at L3; opt-in for L0/L1/L2.
# REGISTRY_URL ว่าง = setup ข้าม registration ทั้งหมด
# REGISTRY_API_KEY ถูกเขียนหลัง register สำเร็จ — ห้าม commit
REGISTRY_URL={cfg.registry_url}
REGISTRY_API_KEY=
"""

    backend_dir = root / "backend"
    if backend_dir.exists():
        (backend_dir / ".env").write_text(backend_env, encoding="utf-8")
        print("✅ Generated backend/.env")
    else:
        (root / "backend.env.generated").write_text(backend_env, encoding="utf-8")
        print("ℹ️  backend/ not found — saved as backend.env.generated at root")
        print("   Move to backend/.env once you've scaffolded the backend folder")

    # Frontend .env
    frontend_env = f"""# frontend/.env — generated by init_project.py
# DO NOT commit this file.

VITE_APP_NAME={_branded_app_name(cfg.project_display_name)}
VITE_API_BASE_URL=http://localhost:8000
VITE_DEFAULT_LANGUAGE={cfg.default_language}
# AUTH_SCOPE mirror — compile-time ceiling for /settings/auth toggles.
# Values: both | internal_only | external_only (must match backend AUTH_SCOPE).
# Omitting this makes AuthSettings.tsx fall back to 'both' and silently
# stop enforcing the ceiling in the UI — keep it in sync with setup.py.
VITE_AUTH_SCOPE={cfg.auth_scope}
"""

    if cfg.auth_scope in ("both", "internal_only"):
        frontend_env += f"""
VITE_AZURE_AD_TENANT_ID={cfg.azure_ad_tenant_id}
VITE_AZURE_AD_CLIENT_ID={cfg.azure_ad_client_id}
VITE_AZURE_AD_REDIRECT_URI=http://localhost:5173/auth/callback
"""

    frontend_dir = root / "frontend"
    if frontend_dir.exists():
        (frontend_dir / ".env").write_text(frontend_env, encoding="utf-8")
        print("✅ Generated frontend/.env")
    else:
        (root / "frontend.env.generated").write_text(frontend_env, encoding="utf-8")
        print("ℹ️  frontend/ not found — saved as frontend.env.generated at root")
        print("   Move to frontend/.env once you've scaffolded the frontend folder")


def ensure_gitignore(root: Path) -> None:
    """Make sure project.config and .env files are gitignored."""
    gitignore = root / ".gitignore"
    entries = [
        "# Generated by init_project.py — do not commit",
        "project.config",
        "backend/.env",
        "backend/.env.*",
        "!backend/.env.example",
        "!backend/.env.prod.example",
        "frontend/.env",
        "frontend/.env.*",
        "!frontend/.env.example",
        "",
    ]

    existing = gitignore.read_text(encoding="utf-8") if gitignore.exists() else ""
    if "project.config" not in existing:
        gitignore.write_text(existing + "\n" + "\n".join(entries), encoding="utf-8")
        print("✅ Updated .gitignore")


# ───────────────────────────────────────────────────────────────
# Project scaffolding
# ───────────────────────────────────────────────────────────────


def scaffold_project_structure(cfg: ProjectConfig, root: Path) -> None:
    """Create backend/ and frontend/ folder structure with stub files.

    Only runs when these directories don't exist yet.
    Existing files are never overwritten.

    db_password is propagated so backend/.env and docker-compose.yml end
    up with the same random credential (audit #1/#2).
    """
    _scaffold_backend(cfg, root, db_password=cfg.dev_db_password)
    _scaffold_frontend(cfg, root)
    _scaffold_docker(cfg, root, db_password=cfg.dev_db_password)
    _scaffold_tooling(cfg, root)
    _scaffold_global_standards(cfg, root)
    # Stamp the project so migrate-security.py --check sees it as up-to-date.
    # Format mirrors setup.py._write_security_baseline.
    from datetime import date as _date
    (root / ".security-baseline").write_text(
        "# Security baseline marker — see docs/migration-security-v3.0.2.md\n"
        "SECURITY_BASELINE=v3.0.2\n"
        f"APPLIED={_date.today().isoformat()}\n"
        "SOURCE=scaffold\n",
        encoding="utf-8",
    )
    print("✅ Scaffolded project structure (.security-baseline v3.0.2)")


# ───────────────────────────────────────────────────────────────
# Main flow
# ───────────────────────────────────────────────────────────────


def print_next_steps(cfg: ProjectConfig, root: Path) -> None:
    print()
    print("=" * 60)
    print("  ✨ Project initialized!")
    print("=" * 60)
    print()
    print(f"  Project: {cfg.project_display_name} ({cfg.project_slug})")
    print(f"  Auth:    {cfg.auth_scope}")
    print()
    print("── Structure created ────────────────────────────────────")
    print("  backend/      FastAPI app (app/, alembic/, tests/)")
    print("  frontend/     React+TS app (src/)")
    print("  docker/       docker-compose.yml + init-db.sql")
    print()
    print("── Next steps ───────────────────────────────────────────")
    print()
    step = 1
    if cfg.auth_scope in ("both", "internal_only"):
        print(f"  {step}. Fill in AZURE_AD_CLIENT_SECRET in backend/.env")
        print("     (Azure Portal → App registrations → Certificates & secrets)")
        step += 1
    print(f"  {step}. Fill in CLAUDE_API_KEY in backend/.env")
    print("     (https://console.anthropic.com/)")
    step += 1
    print(f"  {step}. backend/.env มี DB_PASSWORD random ให้แล้ว (เปลี่ยนได้ถ้าต้องการ)")
    step += 1
    if cfg.prod_db_host:
        print(f"  {step}. Production DB: ตั้ง DB_HOST={cfg.prod_db_host} ใน backend/.env")
        print("     และรัน DB setup script (docs/deployment.md §6.4) ก่อน first deploy")
        step += 1
    else:
        print(f"  {step}. Production DB: แก้ DB_HOST ใน backend/.env ก่อน deploy")
        print("     (ดู docs/deployment.md §6 สำหรับ centralized DB setup)")
        step += 1
    print(f"  {step}. Install frontend deps:")
    print("       cd frontend && npm install")
    step += 1
    print(f"  {step}. Start local DB (docker compose = DB only):")
    print("       cd docker && docker compose up -d && cd ..")
    step += 1
    print(f"  {step}. Run migrations (local Python — see backend/README):")
    print("       cd backend")
    print("       # Windows:  set DB_HOST=localhost")
    print("       # Mac/Linux: export DB_HOST=localhost")
    print("       alembic upgrade head")
    step += 1
    print(f"  {step}. Open http://localhost:5173")
    print()
    print("  📖 AI agents: read CLAUDE.md → AGENTS.md §0 before starting")
    print()


def main() -> int:
    ensure_utf8_stdio()
    # Detect root (script lives in <root>/scripts/)
    script_path = Path(__file__).resolve()
    root = script_path.parent.parent

    # Sanity check
    if not (root / "AGENTS.md").exists():
        print(f"⚠️  AGENTS.md not found at {root}. "
              f"Are you running from the right directory?")
        return 1

    # Check if already initialized
    if (root / "project.config").exists():
        print(f"⚠️  project.config already exists at {root}")
        if not prompt_yes_no("Overwrite and re-initialize?", default_yes=False):
            print("Aborted.")
            return 0

    try:
        cfg = collect_config()
    except (KeyboardInterrupt, EOFError):
        print("\n\nAborted by user.")
        return 130

    # Show summary
    print("── Summary ──────────────────────────────────────────────")
    print(f"  Slug:         {cfg.project_slug}")
    print(f"  Display:      {cfg.project_display_name}")
    print(f"  Auth scope:   {cfg.auth_scope}")
    print(f"  Language:     {cfg.default_language}")
    print(f"  CI/CD:        {cfg.cicd_platform}")
    if cfg.env_var_prefix:
        print(f"  Env prefix:   {cfg.env_var_prefix}_ (recorded only — not applied to .env)")
    print()

    if not prompt_yes_no("Proceed with initialization?", default_yes=True):
        print("Aborted.")
        return 0

    print()
    print("── Writing files ────────────────────────────────────────")
    write_config_file(cfg, root)
    write_readme(cfg, root)
    ensure_gitignore(root)

    print()
    print("── Scaffolding project structure ────────────────────────")
    scaffold_project_structure(cfg, root)

    print()
    print("── Finalizing configuration ─────────────────────────────")
    update_backend_pyproject(cfg, root)
    update_frontend_package(cfg, root)
    update_docker_compose(cfg, root)
    generate_env_files(cfg, root)

    print_next_steps(cfg, root)
    return 0


if __name__ == "__main__":
    sys.exit(main())
